"""Strict review parsing and canonical tool-authored delivery.

The verdict path accepts complete schema-conforming JSON documents only: no
fence stripping, duplicate keys, nonfinite numbers or guessed partial answers.
Multiple documents merge monotonically, retaining findings and never reducing
the most blocking verdict. The content-forwarding failure path cannot approve.

The immutable model document and tool-authored delivery occupy separate key
spaces. Canonical assessments apply the class-primary must-fix-high rule and
floor the delivered verdict away from approval, never toward it. Consumers must
read that top-level verdict, not recount findings or use the nested model label.

This module owns parsing, grading and payload encoding, not result publication,
transport supervision, packet construction or the caller's exit-code mapping.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, fields
from typing import Any

import envelope_schema

# --- layer 2's own version ---------------------------------------------------------------

# READER: `result`, which publishes this document, and any consumer's shape check. Layer 1
# has its own version and the two are deliberately separate ids: the model's schema and the
# tool's delivery shape change for different reasons and on different authority.
DELIVERY_VERSION = "review-delivery/1"

# --- the reserved status vocabulary, which lives ONLY in layer 2 (R16, §7.8a) --------------

# §7.4 step 1 is answered by this key. `tooling-failure` and `too-large` mean NO REVIEW
# HAPPENED; they carry zero model findings, so any counts-only rule reads them as a clean
# pass. That is the silent failure the tool was built against.
STATUS_REVIEWED = "reviewed"
STATUS_TOOLING_FAILURE = "tooling-failure"
STATUS_TOO_LARGE = "too-large"

RESERVED_STATUS = (STATUS_TOOLING_FAILURE, STATUS_TOO_LARGE)

# R16 becomes a PROPERTY rather than a rule someone must remember: the reserved tokens are
# not members of the layer-1 verdict vocabulary at all, so a model cannot emit one under
# constrained decoding, and if it types one on the degraded path (R10) the conformance check
# refuses the document and the degrade path takes it. `closed-vocabulary membership is not
# authorisation` stops being enforceable-by-discipline and becomes where the bytes go.
_COLLISION = set(RESERVED_STATUS) & set(envelope_schema.VERDICT_VALUES)
if _COLLISION:
    raise ValueError(
        "layer-2 reserved status collides with the layer-1 verdict enum: "
        + repr(sorted(_COLLISION))
        + " — R16's structural guarantee is gone if the model can emit a reserved token"
    )
del _COLLISION

# --- R4: the severity SPLIT on tool-authored findings, re-derived against the new enum -----

# R4's behaviour is the SPLIT, not the pin. Both literals are re-derived against
# `envelope_schema.SEVERITY_VALUES` rather than carried across, because the row names
# find-and-replace as the exact trap.
#
# An envelope authored when the tool CANNOT KNOW what the review says takes the
# blocking-most severity in the enum.
SEVERITY_TOOL_BLIND = "critical"
# An envelope reporting a CALLER-INPUT or ENVIRONMENT fault takes the mid one. That class is
# mid-loop actionable — check the declaration, the contract file, the launch — which is what
# this severity means to a consumer. Do not lower it.
SEVERITY_TOOL_FAULT = "high"
# AND NOT THE LEAST-BLOCKING VALUE, WHICH IS THE RE-DERIVATION THAT MATTERS. The new
# contract gives the least-blocking severity the property "recorded, never blocks the work" —
# the property that made the deleted severity dangerous. Pinning a tool-authored finding
# there reproduces a measured fail-open under a new name.

for _pin in (SEVERITY_TOOL_BLIND, SEVERITY_TOOL_FAULT):
    if _pin not in envelope_schema.SEVERITY_VALUES:
        raise ValueError(
            "tool-authored severity " + repr(_pin) + " is not in the schema's severity "
            "vocabulary — re-derive the split against the enum, never find-and-replace it"
        )
del _pin

# The fault classes R4 splits on. This is a REQUIRED argument to `no_review` rather than
# a defaulted one: a default is how one of the two halves quietly becomes the other.
FAULT_UNKNOWN_REVIEW_STATE = "unknown-review-state"
FAULT_CALLER_OR_ENVIRONMENT = "caller-or-environment"
# THE THIRD VALUE, AND IT WAS PRE-REGISTERED IN `request._collect`'s OWN COMMENT BEFORE IT
# EXISTED: "nothing is wrong, it is simply not done" is neither of the two above. A run whose
# record carries no terminal update has not FAILED at anything. The tool still cannot say what
# the review will conclude — which is why this remains a no-review envelope under a reserved
# status — but it has nothing to report as broken either, and the two are different facts.
FAULT_REVIEW_UNFINISHED = "review-unfinished"

_FAULT_SEVERITY = {
    FAULT_UNKNOWN_REVIEW_STATE: SEVERITY_TOOL_BLIND,
    FAULT_CALLER_OR_ENVIRONMENT: SEVERITY_TOOL_FAULT,
    # THE MID SEVERITY, AND THE FAIL-OPEN QUESTION IS ANSWERED RATHER THAN ASSUMED. Taking this
    # state off the blocking-most severity cannot make a no-review read as a clean pass: the
    # status stays reserved, `verdict` stays None, and `loop_should_continue` is a literal True
    # below — every gate that stops an empty findings list being read as approval is untouched,
    # and a consumer that branches on the status first reaches none of this. What changes is
    # only what a consumer is told about a run that has not finished, and mid-loop actionable
    # is what that is: wait for the launch to exit, then collect once.
    #
    # NOT the least-blocking value, for the reason the split above already gives: that value's
    # property is "recorded, never blocks the work", and pinning a tool-authored finding there
    # reproduces a measured fail-open under a new name.
    FAULT_REVIEW_UNFINISHED: SEVERITY_TOOL_FAULT,
}

# Artifact-class primary categories used by canonical must-fix-high assessment.
# This mapping follows the installed contracts; request only resolves class names.
PRIMARY_CATEGORY = {
    "code-service": "stability",
    "code-fleet": "stability",
    "vault-raw": "truthfulness",
    "vault-wiki": "truthfulness",
    "research-report": "truthfulness",
    "plan-design": "truthfulness",
    "project-state": "truthfulness",
    "model-instruction": "usability",
    "pipeline-template": "stability",
    "configuration": "stability",
}

for _cls, _cat in PRIMARY_CATEGORY.items():
    if _cat not in envelope_schema.CATEGORY_VALUES:
        raise ValueError(
            "primary category " + repr(_cat) + " for artifact class " + repr(_cls)
            + " is not in the schema's category vocabulary — §1.4 and §1.6 have drifted"
        )
del _cls, _cat


def primary_category_for(artifact_class: str) -> str | None:
    """The category the must-fix-high test compares against, or None when there is none.

    None is returned for the declared no-contract opt-out and for any class this module
    does not recognise. None is NOT a licence to skip the test — `_assess` treats a `high`
    with no primary category to compare against as must-fix, which is the deny-by-default
    direction §2.3's own absent-category clause takes.
    """
    return PRIMARY_CATEGORY.get(artifact_class)


# --- the strict verdict parse (§7.3, R24, R12) --------------------------------------------


class StrictParseError(Exception):
    """The stream is not a whitespace-separated sequence of conforming-shaped JSON documents.

    Raised, never absorbed into a default. The caller's disposition is DEGRADE — keep every
    byte, deliver a non-approve verdict — never REFUSE, because refusing is where a real
    review gets discarded.
    """


_INFINITY = float("inf")


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict:
    """Object hook that RAISES on a duplicate key instead of silently keeping the last.

    R12. `json.loads`' default is last-wins, and a duplicate key survives neither a Python
    dict nor a `str` round-trip — so a model that emits `findings` twice, trivially reachable
    when it re-drafts mid-generation, gets its real findings replaced by the later empty
    list. The resulting object is FULLY schema-conforming, so `outputSchema` cannot see it
    and this hook is not retired by constrained decoding.

    SCOPED WIDER THAN THE ROW, DELIBERATELY. R12 states the rule for TOP-LEVEL keys; this
    hook fires at every object depth, so a finding whose `severity` appears twice raises too.
    The widening is safe in the only direction that matters: raising sends the text to the
    degrade path, which preserves every byte and cannot produce a pass.
    """
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise StrictParseError(
                "duplicate key " + repr(key) + " — last-wins parsing would silently drop "
                "the earlier value, so this text is not unambiguously one document"
            )
        seen[key] = value
    return seen


def _reject_constant(token: str) -> Any:
    """`NaN` / `Infinity` / `-Infinity` are not JSON and cannot round-trip. Refuse them."""
    raise StrictParseError(
        "non-JSON constant " + repr(token) + " in the stream — it is accepted by Python's "
        "parser, is not valid JSON, and cannot be re-emitted to the caller"
    )


def _reject_nonfinite(token: str) -> float:
    """A numeric token that OVERFLOWS to infinity is refused here, where it can still degrade.

    `_reject_constant` covers only the literal `NaN` / `Infinity` TOKENS. A model does not
    have to write those to produce a non-finite value: `1e9999` is a perfectly legal JSON
    number that Python's float conversion turns into `inf` WITHOUT consulting
    `parse_constant` at all.

    Left alone it reaches `encode_payload`, whose `allow_nan=False` — which is correct and
    deliberate, since a Python-only constant would produce bytes no JSON reader accepts —
    raises `ValueError`, and that is not the `UnicodeEncodeError` the write path knows how to
    survive. **A COMPLETED REVIEW THEN PRODUCES NO PAYLOAD AT ALL.** Refusing at the parse
    boundary routes it into the degrade path instead, where every byte is still preserved
    under `model_text`.

    Found by the cross-family review, driven, and PRE-EXISTING: the single-document path has
    always had it, so this is not a regression from the multi-document work — it is a defect
    that work happened to expose.
    """
    value = float(token)
    if value in (_INFINITY, -_INFINITY):
        raise StrictParseError(
            "numeric token " + repr(token) + " overflows to infinity — it is legal JSON on "
            "the way in and unserialisable on the way out, so it is refused here where the "
            "answer can still be degraded rather than destroyed"
        )
    return value


def parse_stream(stream_text: str) -> list[dict]:
    """Every complete JSON object in the stream, in order, optionally separated by whitespace.

    WHITESPACE BETWEEN DOCUMENTS IS PERMITTED, NOT REQUIRED, and that is deliberate. `}{`
    with nothing between them is two unambiguous documents to a real JSON parser, and
    refusing it would destroy a complete review over a cosmetic separator — the exact class
    of failure this function was rewritten to stop. A reviewer of this code recommended
    requiring a separator; that recommendation is declined for this reason, and the wording
    here was the thing at fault, not the behaviour.

    R24, driven against four measured false passes. Three of the four tolerances a rebuilder
    reaches for are still FORBIDDEN here, and this function has no site for any of them:
      - brace-counting desyncs on a `}` inside a JSON string and lands on approve-shaped
        prose. Not used: `raw_decode` is a real JSON parser, so a brace inside a string is
        a brace inside a string.
      - falling back to stderr promotes a cached `approve` from an EARLIER RUN over a crash
        traceback. Not possible: this function is not given stderr and has no parameter for
        it.
      - stripping fences accepts an `approve` the model merely QUOTED from its own preamble.
        Not done: a fence is not whitespace, so a fenced stream is REFUSED, exactly as before.

    THE FOURTH TOLERANCE — LAST-ONE-WINS — IS WHERE THIS CHANGED, AND THE REASON IS MEASURED.
    Refusing every stream that was not exactly one document DESTROYED complete reviews in
    production: the reviewing model narrates a step in one conforming envelope and then
    answers in a second, and both were thrown away. R24's hazard is REAL and is not conceded;
    what closes it is `merge_documents`, which can never move a verdict toward `approve` and
    can never drop a finding. Last-one-wins is still forbidden — the merge does not take the
    last, it takes the most blocking, which is why R24's own G1 fixture (a genuine
    `needs-attention` carrying a CRITICAL, followed by a stale `approve`) still fails closed.

    Trailing bytes that are not another complete document are REFUSED, which is the half of
    the old strictness that catches prose, fences and truncation.

    NO BYTES ARE TRIMMED HERE. `stream_text` reaches the decoder as received; the only
    characters stepped over are the whitespace BETWEEN documents. R14's invariant — never
    trim more than was observed, never `strip()` — has no site in this module.

    Raises StrictParseError on an empty stream, on a non-object document, and on anything
    between or after the documents that is not whitespace.
    """
    decoder = json.JSONDecoder(
        object_pairs_hook=_no_duplicate_keys,
        parse_constant=_reject_constant,
        parse_float=_reject_nonfinite,
    )
    documents: list[dict] = []
    index = 0
    length = len(stream_text)
    while True:
        while index < length and stream_text[index] in " \t\r\n":
            index += 1
        if index >= length:
            break
        try:
            document, index = decoder.raw_decode(stream_text, index)
        except StrictParseError:
            raise
        except ValueError as exc:  # JSONDecodeError is a ValueError
            raise StrictParseError(
                "not a whitespace-separated sequence of JSON documents: " + str(exc)
                + " — the text from that point on is not another complete document, so it "
                "is prose, a fence, or a truncation, and none of those establishes a verdict"
            ) from exc
        if not isinstance(document, dict):
            raise StrictParseError(
                "a document in the stream parsed to " + type(document).__name__
                + ", not an object"
            )
        documents.append(document)
    if not documents:
        raise StrictParseError("the stream holds no JSON document")
    return documents


def _rank_of(verdict: Any) -> int | None:
    """The blocking rank of a verdict, or None for anything this module does not recognise.

    TYPE-SAFE ON PURPOSE, AND THAT IS THE WHOLE REASON IT EXISTS. `verdict` is whatever the
    model put in that key, and JSON permits a list or an object there — both UNHASHABLE, so
    a bare `_VERDICT_RANK.get(verdict)` raises TypeError on them. That raise escaped
    `deliver` and produced NO ENVELOPE AT ALL, which is worse than anything else in this
    module: the answer is DESTROYED rather than degraded. Found by the cross-family review
    of this very change, driven, with a single-document control proving it was specific to
    the multi-document path.
    """
    return _VERDICT_RANK.get(verdict) if isinstance(verdict, str) else None


# How an ABSENT key renders in the agreement check below. It can never collide with a
# rendered JSON value: `json.dumps` puts quotes around every string, so no value in any
# document can produce this bare token.
_ABSENT_RENDER = "(absent)"


def merge_documents(documents: list[dict]) -> dict:
    """Fold a multi-document stream into one document, MONOTONICALLY.

    THE INVARIANT, and it is stated as what it actually is rather than as the stronger thing
    an earlier draft of this docstring claimed: **the merge can never make the delivered
    result LESS BLOCKING than any document in the stream.** That is what earns the tolerance,
    because R24's whole requirement is that the permissive path be structurally incapable of
    producing a pass.

    NOTHING IS DEDUPLICATED, AND THE ONE READABLE THING THIS FOLD DROPS IS NAMED IN RULE THREE.
    This paragraph is the THIRD correction to this sentence and every correction has had the
    same shape: the sentence claimed more than the code does. The first draft claimed a
    cardinality guarantee. The second named two reductions the implementation performs NEITHER
    of — it keeps duplicates, and it lets an unreadable `findings` value contribute nothing while
    `deliver` authors a blocking finding about it. The third said nothing readable is dropped,
    while rule three drops every non-base document's `next_steps`. What earns the tolerance is
    the invariant above and nothing else: no reduction, no cardinality promise and no
    completeness claim is doing any of that work, and a sentence that keeps trying to add one is
    the sentence to distrust.

    Three rules, one per key that can decide anything:
      - `verdict` is the MOST BLOCKING recognised verdict across the documents — never the
        last one. If ANY document carries a verdict this module does not recognise, that
        unrecognised value is what survives — deliberately, because it fails the conformance
        check downstream and the permissive path there cannot write `approve`.
      - `findings` is the ordered union of every READABLE `findings` list, duplicates and all.
        A document whose `findings` is not a list contributes nothing to that union and
        displaces nothing already in it.
      - every other key — the summary a human reads, the echo, the schema version, AND
        `next_steps` — comes from the most blocking document, ties resolved toward the LATER
        one, because a narration envelope's summary describes what the reviewer was about to do
        rather than what it found. **`next_steps` IS THE COST OF THAT RULE AND IT IS NOT
        METADATA:** it is the one key in "every other" that a caller ACTS on, so a non-base
        document's next steps are dropped here rather than unioned the way findings are. Named
        rather than fixed, because unioning it would be a second merge policy over a key no
        gate reads, and the monotone invariant above is untouched either way — a dropped next
        step cannot make the result less blocking. THAT RULE IS ALSO THE ONE PLACE THIS FOLD
        CAN HIDE SOMETHING, and it is why `deliver` runs the conformance check over EVERY
        document in the stream rather than over the merged object: a nonconforming earlier
        document is replaced here by a later conforming one, and a gate reading only the fold
        would never meet it. The invariant at the top of this docstring is upheld by the two
        halves together.

    A single-document stream is returned unchanged. That is the control arm: the common case
    must be byte-identical to what it was before this function existed.
    """
    if len(documents) == 1:
        return documents[0]

    ranks = [_rank_of(document.get("verdict")) for document in documents]

    base = documents[-1]
    best = None
    for document, rank in zip(documents, ranks):
        if rank is not None and (best is None or rank >= best):
            best, base = rank, document

    # `base` IS the most blocking document, so the verdict it already carries is the most
    # blocking verdict. Nothing reconstructs it from the rank — a reconstruction would be a
    # second place the answer is decided, and the two could drift.
    merged = dict(base)

    unrecognised = [
        document.get("verdict")
        for document, rank in zip(documents, ranks)
        if rank is None
    ]
    if unrecognised:
        merged["verdict"] = unrecognised[0]

    # THE FIELDS THAT DESCRIBE THE REVIEW ITSELF MUST AGREE, OR THERE IS NO SINGLE REVIEW TO
    # DESCRIBE. `artifact_class_echo` is the ONLY observable separating a review conducted
    # under the contract from one conducted without it; taking the base's would assert that
    # the contract reached the reviewer for findings contributed by a document that said
    # otherwise, and the delivered `contract_echo.agrees` would read true over the top of the
    # mismatch. Degrading is right here precisely because it is unresolvable: we cannot know
    # which document's claim covers which finding. Measured on the real two-envelope captures
    # before adopting this — both documents agree in both streams, so this refuses nothing
    # that happens today. Raised by the cross-family review.
    #
    # A MISSING KEY AND AN EXPLICIT `null` ARE TWO DIFFERENT CLAIMS, AND `document.get` CANNOT
    # TELL THEM APART — both answer None, so a stream in which one document OMITS
    # `artifact_class_echo` and another explicitly nulls it was accepted here as agreeing. If
    # the explicit-null document was the most blocking it became the base, and `_contract_echo`
    # — which tests the KEY, not the value, and is right to — then found the key present and
    # published `agrees: true` for a stream in which a document contributing findings had said
    # nothing about the contract at all. The conformance gate still failed closed, so the
    # VERDICT was safe; the delivered RECORD was not, and the echo is the only observable that
    # separates a contracted review from an uncontracted one. A wrong `agrees` is worse than an
    # absent one.
    #
    # COMPARING PRESENCE AS WELL AS VALUE IS WHAT LETS `_contract_echo` READ THE BASE AND STILL
    # BE READING THE WHOLE STREAM. Once this loop passes, every document holds the key in the
    # same state with the same value, so the base's echo IS the stream's echo — the agreement is
    # derived from every document, and the base is merely where it is read. The single-document
    # path already drew this distinction; this is the merge learning it.
    for key in ("artifact_class_echo", "schema_version"):
        rendered = {
            json.dumps(document[key], sort_keys=True) if key in document else _ABSENT_RENDER
            for document in documents
        }
        if len(rendered) > 1:
            raise StrictParseError(
                "the documents disagree on " + repr(key) + " (" + ", ".join(sorted(rendered))
                + ") — that field describes the review itself, so there is no single review "
                "for a merge to produce"
            )

    # EVERY READABLE FINDING SURVIVES, IN ORDER, AND NOTHING IS DEDUPLICATED.
    #
    # Two earlier drafts lost findings here and a reviewer caught both. The first replaced the
    # whole union with a document's UNREADABLE `findings` value, which deleted real findings
    # from the structured review to signal a fault — a smaller copy of the very defect this
    # rewrite exists to end. The second deduplicated, which can drop below the count a single
    # document carried.
    #
    # So: unreadable values contribute nothing and are NOT substituted, and `deliver` authors
    # a blocking finding about them, which is where a fault belongs. Duplicates are kept,
    # because a repeated finding is visible and harmless while a deleted one is neither, and
    # double-counting can only make the result more blocking.
    union: list = []
    for document in documents:
        entries = document.get("findings")
        if isinstance(entries, list):
            union.extend(entries)
    merged["findings"] = union

    return merged


# --- the conformance check, DERIVED from the schema rather than transcribed (§3.1) ---------

# THIS SECTION DELIBERATELY CONTAINS NO STATEMENT OF THIS SCHEMA'S RULES, AND THAT IS THE
# POINT. A checker written by reading `envelope_schema` and re-typing its constraints is a
# second copy of one shape, and the copy this replaced had already drifted: it enforced the
# key sets and three enums and ignored every length floor, every per-finding type, and the
# anchor coupling — so a document with an empty summary was reported CONFORMING and walked
# through the approval gate R22 exists to hold. What follows is an interpreter over the
# SCHEMA DOCUMENT, generic over JSON Schema Draft 2020-12. A constraint added to
# `envelope_schema` is enforced here from the moment it is written, by nobody.

# Keywords that assert nothing about an instance. `$defs` is here because it HOLDS
# subschemas rather than constraining anything itself; `$ref` reaches into it.
_SCHEMA_INERT_KEYWORDS = frozenset(
    {
        "$schema", "$id", "$anchor", "$comment", "$defs",
        "title", "description", "default", "examples",
        "deprecated", "readOnly", "writeOnly",
    }
)

# The assertions `_validate` actually applies. A keyword outside BOTH sets is refused at
# import — see `_audit_schema`.
_SCHEMA_ENFORCED_KEYWORDS = frozenset(
    {
        "$ref",
        "type", "enum", "const",
        "properties", "required", "additionalProperties", "dependentRequired",
        "items", "prefixItems", "minItems", "maxItems", "uniqueItems",
        "minLength", "maxLength", "pattern",
        "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
        "allOf", "anyOf", "oneOf", "not",
        "if", "then", "else",
    }
)

# Where subschemas sit, by shape: reached directly, through a name map, or through a list.
# `_audit_schema` and `_validate` descend the SAME places, so a keyword `_validate` would
# skip is a keyword `_audit_schema` refuses.
_SUBSCHEMA_DIRECT = ("items", "additionalProperties", "not", "if", "then", "else")
_SUBSCHEMA_MAPPED = ("properties", "$defs")
_SUBSCHEMA_LISTED = ("allOf", "anyOf", "oneOf", "prefixItems")


def _audit_schema(schema: Any, where: str) -> None:
    """Refuse, at import, any schema keyword this interpreter would silently ignore.

    THE FAILURE MODE THIS EXISTS AGAINST IS SILENCE. An unrecognised keyword that is merely
    skipped is an unenforced constraint no reader can see — which is exactly the defect the
    hand-written checker had, rebuilt one keyword at a time. Raising is loud, immediate, and
    names the keyword and the site, so whoever tightened the schema learns in one line that
    the validator has to grow with it.
    """
    if isinstance(schema, bool):
        return
    if not isinstance(schema, dict):
        raise TypeError("schema at " + where + " is " + type(schema).__name__ + ", not a schema")
    unknown = sorted(set(schema) - _SCHEMA_INERT_KEYWORDS - _SCHEMA_ENFORCED_KEYWORDS)
    if unknown:
        raise ValueError(
            "schema keyword(s) " + repr(unknown) + " at " + where + " are not enforced by "
            "this module's validator. An unenforced constraint is a silent hole in the "
            "conformance gate — teach `_validate` the keyword, or remove it from the schema."
        )
    for keyword in _SUBSCHEMA_DIRECT:
        if keyword in schema:
            _audit_schema(schema[keyword], where + "/" + keyword)
    for keyword in _SUBSCHEMA_MAPPED:
        for name, sub in schema.get(keyword, {}).items():
            _audit_schema(sub, where + "/" + keyword + "/" + name)
    for keyword in _SUBSCHEMA_LISTED:
        for index, sub in enumerate(schema.get(keyword, ())):
            _audit_schema(sub, where + "/" + keyword + "[" + str(index) + "]")


_audit_schema(envelope_schema.ENVELOPE_SCHEMA, "ENVELOPE_SCHEMA")


def _is_type(value: Any, name: str) -> bool:
    """JSON Schema's type test, not Python's. `True` is a boolean and never an integer."""
    if name == "null":
        return value is None
    if name == "boolean":
        return isinstance(value, bool)
    if name == "object":
        return isinstance(value, dict)
    if name == "array":
        return isinstance(value, list)
    if name == "string":
        return isinstance(value, str)
    if name == "integer":
        if isinstance(value, bool):
            return False
        if isinstance(value, int):
            return True
        return isinstance(value, float) and value.is_integer()
    if name == "number":
        return not isinstance(value, bool) and isinstance(value, (int, float))
    raise ValueError("unknown JSON Schema type name " + repr(name))


def _type_of(value: Any) -> str:
    """How a reason names what it found. Prose, because a caller reads these."""
    if value is None:
        return "null"
    for name, phrase in (
        ("boolean", "a boolean"),
        ("string", "a string"),
        ("integer", "an integer"),
        ("number", "a number"),
        ("array", "an array"),
        ("object", "an object"),
    ):
        if _is_type(value, name):
            return phrase
    # Not a JSON type at all — reachable from `Provenance`, which validates Python values.
    return "a value of Python type " + type(value).__name__


def _json_equal(left: Any, right: Any) -> bool:
    """JSON equality, which is not Python's: `True` does not equal `1` in a JSON enum."""
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left is right
    if isinstance(left, dict) and isinstance(right, dict):
        return set(left) == set(right) and all(_json_equal(left[k], right[k]) for k in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _json_equal(a, b) for a, b in zip(left, right)
        )
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    return type(left) is type(right) and left == right


def _show(value: Any) -> str:
    """A value, short enough to sit inside a finding body. Long review prose is clipped."""
    text = repr(value)
    return text if len(text) <= 72 else text[:69] + "..."


def _at(path: str) -> str:
    return path if path else "the document"


def _join(path: str, key: str) -> str:
    return key if not path else path + "." + key


def _resolve_ref(ref: str, root: dict) -> Any:
    if not ref.startswith("#"):
        raise ValueError(
            "$ref " + repr(ref) + " leaves this document; only local pointers resolve here"
        )
    node: Any = root
    for token in ref[1:].split("/"):
        if not token:
            continue
        node = node[token.replace("~1", "/").replace("~0", "~")]
    return node


def _reasons_for(value: Any, schema: Any, path: str, root: dict) -> list[str]:
    """`_validate` into a fresh list — how the combinators try a branch without committing."""
    attempt: list[str] = []
    _validate(value, schema, path, attempt, root)
    return attempt


def _validate(value: Any, schema: Any, path: str, reasons: list[str], root: dict) -> None:
    """Check `value` against `schema`, appending one human-readable reason per violation.

    Generic over the vocabulary and blind to this schema's contents. `path` names the site a
    reader has to look at; `root` is the document `$ref` resolves against.
    """
    if isinstance(schema, bool):
        if not schema:
            reasons.append(_at(path) + ": is refused outright by the schema at this position")
        return

    if "$ref" in schema:
        _validate(value, _resolve_ref(schema["$ref"], root), path, reasons, root)

    declared = schema.get("type")
    if declared is not None:
        allowed = [declared] if isinstance(declared, str) else list(declared)
        if not any(_is_type(value, name) for name in allowed):
            reasons.append(
                _at(path) + ": is " + _type_of(value) + "; the schema requires "
                + " or ".join(allowed)
            )
            # Every keyword below is a constraint ON that type. Reporting them too would bury
            # the one reason that explains all of them.
            return

    if "enum" in schema and not any(
        _json_equal(value, option) for option in schema["enum"]
    ):
        reasons.append(
            _at(path) + ": is " + _show(value) + ", which is not one of "
            + _show(schema["enum"])
        )
    if "const" in schema and not _json_equal(value, schema["const"]):
        reasons.append(
            _at(path) + ": is " + _show(value) + "; the schema requires "
            + _show(schema["const"])
        )

    if isinstance(value, str):
        floor = schema.get("minLength")
        if floor is not None and len(value) < floor:
            reasons.append(
                _at(path) + ": is " + str(len(value)) + " characters long; the schema "
                "requires at least " + str(floor)
            )
        ceiling = schema.get("maxLength")
        if ceiling is not None and len(value) > ceiling:
            reasons.append(
                _at(path) + ": is " + str(len(value)) + " characters long; the schema "
                "permits at most " + str(ceiling)
            )
        pattern = schema.get("pattern")
        if pattern is not None and re.search(pattern, value) is None:
            reasons.append(
                _at(path) + ": " + _show(value) + " does not match the required pattern "
                + _show(pattern)
            )

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        for keyword, permitted, phrase in (
            ("minimum", value >= schema.get("minimum", value), "at least"),
            ("maximum", value <= schema.get("maximum", value), "at most"),
            ("exclusiveMinimum", value > schema.get("exclusiveMinimum", value - 1),
             "greater than"),
            ("exclusiveMaximum", value < schema.get("exclusiveMaximum", value + 1),
             "less than"),
        ):
            if keyword in schema and not permitted:
                reasons.append(
                    _at(path) + ": is " + _show(value) + "; the schema requires " + phrase
                    + " " + _show(schema[keyword])
                )
        step = schema.get("multipleOf")
        if step is not None and (value % step) != 0:
            reasons.append(
                _at(path) + ": is " + _show(value) + "; the schema requires a multiple of "
                + _show(step)
            )

    if isinstance(value, list):
        prefix = schema.get("prefixItems") or ()
        for index, sub in enumerate(prefix):
            if index < len(value):
                _validate(value[index], sub, path + "[" + str(index) + "]", reasons, root)
        item_schema = schema.get("items")
        if item_schema is not None:
            for index in range(len(prefix), len(value)):
                _validate(
                    value[index], item_schema, path + "[" + str(index) + "]", reasons, root
                )
        floor = schema.get("minItems")
        if floor is not None and len(value) < floor:
            reasons.append(
                _at(path) + ": holds " + str(len(value)) + " items; the schema requires at "
                "least " + str(floor)
            )
        ceiling = schema.get("maxItems")
        if ceiling is not None and len(value) > ceiling:
            reasons.append(
                _at(path) + ": holds " + str(len(value)) + " items; the schema permits at "
                "most " + str(ceiling)
            )
        if schema.get("uniqueItems"):
            for index, item in enumerate(value):
                if any(_json_equal(item, other) for other in value[:index]):
                    reasons.append(
                        _at(path) + "[" + str(index) + "]: repeats an earlier item, and the "
                        "schema requires every item to be distinct"
                    )
                    break

    if isinstance(value, dict):
        for key in schema.get("required", ()):
            if key not in value:
                reasons.append(_at(path) + ": is missing the required key " + repr(key))
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties")
        for key, item in value.items():
            if key in properties:
                _validate(item, properties[key], _join(path, key), reasons, root)
            elif additional is False:
                reasons.append(
                    _at(path) + ": carries the key " + repr(key) + ", which the schema does "
                    "not define and does not permit"
                )
            elif isinstance(additional, (dict, bool)):
                _validate(item, additional, _join(path, key), reasons, root)
        for key, dependents in schema.get("dependentRequired", {}).items():
            if key in value:
                for other in dependents:
                    if other not in value:
                        reasons.append(
                            _at(path) + ": carries " + repr(key) + ", which the schema says "
                            "requires " + repr(other) + " alongside it"
                        )

    for sub in schema.get("allOf", ()):
        _validate(value, sub, path, reasons, root)

    branches = schema.get("anyOf")
    if branches is not None:
        attempts = [_reasons_for(value, branch, path, root) for branch in branches]
        if all(attempts):
            reasons.append(
                _at(path) + ": matches none of the shapes the schema permits ("
                + _why_no_branch_matched(attempts) + ")"
            )

    branches = schema.get("oneOf")
    if branches is not None:
        attempts = [_reasons_for(value, branch, path, root) for branch in branches]
        matched = [index for index, attempt in enumerate(attempts) if not attempt]
        if not matched:
            reasons.append(
                _at(path) + ": matches none of the shapes the schema permits ("
                + _why_no_branch_matched(attempts) + ")"
            )
        elif len(matched) > 1:
            reasons.append(
                _at(path) + ": matches more than one of the schema's alternatives, where "
                "exactly one is permitted"
            )

    if "not" in schema and not _reasons_for(value, schema["not"], path, root):
        reasons.append(_at(path) + ": matches a shape the schema forbids")

    if "if" in schema:
        taken = "then" if not _reasons_for(value, schema["if"], path, root) else "else"
        if taken in schema:
            _validate(value, schema[taken], path, reasons, root)


def _why_no_branch_matched(attempts: list[list[str]]) -> str:
    """One reason per rejected alternative — enough for a reader to see WHICH shape they
    nearly wrote, without reprinting every branch's full failure list."""
    return "; ".join(
        "alternative " + str(index + 1) + ": " + attempt[0]
        for index, attempt in enumerate(attempts)
        if attempt
    )


def _semantic_reasons(document: Any) -> list[str]:
    """The rules the schema vocabulary cannot state, checked where they CAN be — here.

    `envelope_schema` says so itself at its anchor `oneOf`: `line_end >= line_start` is a
    comparison BETWEEN TWO VALUES and JSON Schema has no keyword for one, so the parser owns
    it. A reversed range is neither of the two honest answers the nullability valve allows —
    it is not a null, and it does not resolve to anything a caller can open.
    """
    reasons: list[str] = []
    if not isinstance(document, dict):
        return reasons
    findings = document.get("findings")
    if not isinstance(findings, list):
        return reasons
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            continue
        start = finding.get("line_start")
        end = finding.get("line_end")
        if _is_type(start, "integer") and _is_type(end, "integer") and end < start:
            reasons.append(
                "findings[" + str(index) + "]: line_end is " + _show(end)
                + " and line_start is " + _show(start) + " — the range ends before it "
                "starts, so the anchor resolves to nothing"
            )
    return reasons


def conformance_reasons(document: dict) -> list[str]:
    """Why this document is not a natively-parsed CONFORMING envelope. Empty means it is.

    R22's gate, made addressable: `approve` is accepted only from a conforming document, and
    the reasons are published in layer 2 so a reader can see WHICH clause refused rather
    than inferring it.

    THE CHECK IS DERIVED, NEVER TRANSCRIBED. `envelope_schema.ENVELOPE_SCHEMA` is run through
    the interpreter above, so what is enforced is the schema AS WRITTEN — including every
    constraint added to it after this function was — and then the one rule that vocabulary
    cannot express is added. Restating the schema's rules here is what drifted before: two
    copies of one shape drift and nothing detects it (§3.1).

    A MODEL-TYPED RESERVED STATUS TOKEN LANDS HERE TOO (R16): it is not in the layer-1
    verdict enum, so the `verdict` enum assertion refuses the document as non-conforming
    rather than honouring it, and the degrade path keeps every byte.
    """
    reasons: list[str] = []
    _validate(
        document,
        envelope_schema.ENVELOPE_SCHEMA,
        "",
        reasons,
        envelope_schema.ENVELOPE_SCHEMA,
    )
    reasons.extend(_semantic_reasons(document))
    return reasons


# --- deny-by-default severity, and the two gates (§7.4, I10, I11, R13) ---------------------

_COUNT_UNRECOGNIZED = "unrecognized"


def _normalise_severity(raw: Any) -> str | None:
    """A recognised severity, or None. Case and surrounding whitespace are forgiven; nothing
    else is.

    `"Critical"` — one capital letter — is the cheapest and most likely malformed severity
    there is, and a plain membership test against lowercase constants does not catch it. It
    is folded to the real severity rather than being treated as unrecognised, because
    treating it as unrecognised would UNDER-count a critical.
    """
    if not isinstance(raw, str):
        return None
    candidate = raw.strip().lower()
    return candidate if candidate in envelope_schema.SEVERITY_VALUES else None


def _assess(findings: Any, primary_category: str | None) -> tuple[dict, list[dict], bool]:
    """Return (counts, per-finding assessments, saw_unreadable_structure).

    Deny by default: every recognized severity prevents approval, and malformed
    or unreadable finding structure cannot become a clean result. Preserve the
    content while reporting the failed assessment separately.

    THE PER-FINDING `blocking` BOOLEAN IS I10.3'S, AND IT IS A LAYER-2 FIELD. It is returned
    in a PARALLEL array keyed by index, not written into the model's finding objects: layer 1
    is immutable and forwarded byte-exact (§7.8a), so annotating it in place would undo the
    split the schema is deliberately missing this key for.

    `must_fix_high` is the FIX-LOOP gate and it is a DIFFERENT gate over a DIFFERENT set
    (I11, §7.4). A finding whose severity or category cannot be read is scored must-fix:
    an unreadable finding could be the one that matters, and the cost of the wrong guess is
    one more iteration against a cap the caller owns.
    """
    counts = {name: 0 for name in envelope_schema.SEVERITY_VALUES}
    counts[_COUNT_UNRECOGNIZED] = 0
    assessments: list[dict] = []

    if not isinstance(findings, list):
        # Handled FIRST and uniformly. A previous generation of this rule short-circuited an
        # emptiness test above the type test, and `findings: null` was read as CLEAN.
        counts[_COUNT_UNRECOGNIZED] = 1
        return counts, assessments, True

    saw_unreadable = False
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            counts[_COUNT_UNRECOGNIZED] += 1
            saw_unreadable = True
            assessments.append(
                {
                    "index": index,
                    "blocking": True,
                    "must_fix_high": True,
                    "severity_normalized": None,
                    "severity_recognized": False,
                    "category_matches_primary": False,
                    "note": "finding-is-" + type(finding).__name__ + "-not-an-object",
                }
            )
            continue

        severity = _normalise_severity(finding.get("severity"))
        recognized = severity is not None
        if recognized:
            counts[severity] += 1
        else:
            counts[_COUNT_UNRECOGNIZED] += 1
            saw_unreadable = True

        category = finding.get("category")
        category_readable = (
            isinstance(category, str)
            and category.strip() != ""
            and category in envelope_schema.CATEGORY_VALUES
        )
        matches_primary = bool(
            category_readable
            and primary_category is not None
            and category == primary_category
        )

        if severity == "high":
            # §2.3: severity high AND category equal to the class's primary category. Its
            # own stated consequence for the absent case — a high whose category is absent,
            # null or empty MUST count as must-fix — is extended here in the same direction
            # to a category outside the vocabulary and to a class with no primary category
            # to compare against. Both are the unreadable case wearing a different shape.
            must_fix = matches_primary or not category_readable or primary_category is None
        elif recognized:
            must_fix = False
        else:
            must_fix = True

        assessments.append(
            {
                "index": index,
                "blocking": True,
                "must_fix_high": must_fix,
                "severity_normalized": severity,
                "severity_recognized": recognized,
                "category_matches_primary": matches_primary,
                "note": None
                if recognized
                else "unrecognized-severity:" + repr(finding.get("severity"))[:48],
            }
        )
    return counts, assessments, saw_unreadable


# The ordering the floor moves along. approve is the only verdict that suppresses downstream
# action, so it is the least blocking; `fail` is the only one that forbids yielding at all.
_VERDICT_RANK = {"approve": 0, "needs-attention": 1, "fail": 2}


def _floor(counts: dict, must_fix_high_count: int) -> str | None:
    """The most blocking state the findings themselves justify, or None.

    §7.4's order, expressed as a floor rather than as a recount so that I10 survives it: a
    model-emitted label is never moved TOWARD approve by this function, because None is
    returned when nothing justifies a floor.

    §2.1.1 — A MUST-FIX HIGH FLOORS TO `fail`, EXACTLY AS A CRITICAL DOES. Two tables
    that were already in the spec decided this and had never been joined up: §2.2's `high`
    row blocks yielding "only if must-fix", and `fail` is the token whose own definition is
    "MUST NOT be yielded as reviewed". Those are the same predicate written twice.

    `needs-attention` is affirmatively WRONG here rather than merely weaker, which is the
    sharper half of the argument: its row compels the caller to "fix it, or proceed and
    record why", and §2.4's exit test requires count(must-fix high) == 0 — so the
    "proceed and record why" branch it offers the caller does not exist. Delivering it would
    hand the caller a discretion the exit rule forbids.

    THE COUNT IS PASSED IN AND IS NEVER RE-DERIVED HERE. Must-fix is a property of each
    finding assessed against the artifact class's PRIMARY category; `counts` carries no
    category axis at all, so a floor that tried to reconstruct it from `counts` could only
    treat every `high` as must-fix — precisely the reading §2.2 rules out.
    """
    if counts["critical"] > 0:
        return "fail"
    if must_fix_high_count > 0:
        return "fail"
    if (
        counts["high"] > 0
        or counts["medium"] > 0
        or counts[_COUNT_UNRECOGNIZED] > 0
    ):
        return "needs-attention"
    return None


def _more_blocking(left: str, right: str) -> str:
    return left if _VERDICT_RANK[left] >= _VERDICT_RANK[right] else right


# --- provenance (§7.8, §0.1) ----------------------------------------------------------------


@dataclass(frozen=True)
class Provenance:
    """Who produced this envelope, so a reader never has to infer the sender from context.

    §0.1's THREE MODEL IDENTITIES ARE THREE FIELDS AND ARE NEVER COLLAPSED. `requested_model`
    is what we asked for; `server_reported_model` is what the selected transport said answered;
    `attested_route` records the route and the exact response metadata used to verify that the
    completed answer came from the expected family. It is nullable because refusals, incomplete
    runs, and unverifiable results have no attested completed route. It is never copied from a
    declaration alone: the transport must have returned the family identity it records.
    """

    service_instance: str
    channel: str
    requested_model: str
    server_reported_model: str | None
    attested_route: str | None = None

    def __post_init__(self) -> None:
        """Refuse a malformed identity AT CONSTRUCTION, where the caller can still see why.

        Dataclass annotations do not enforce types or nonempty identity at
        runtime. Refuse here rather than publishing an unusable producer identity
        or failing later at a serialization boundary with no caller context.

        WHICH FIELDS ARE REQUIRED IS READ OFF THE ANNOTATIONS, NOT RE-TYPED HERE. A list of
        names in this method is a second copy of the field list, and it goes stale the first
        time a field is added — silently, in the permissive direction. A field whose
        annotation this cannot read as nullable is treated as REQUIRED, so the failure is a
        loud refusal rather than an unchecked identity.

        AND THE ANNOTATION ALSO DECIDES WHAT A BLANK MEANS. On a REQUIRED identity a blank
        stands in for something the caller had to supply, so it is refused — that is a real
        fault and the refusal catches it. On a NULLABLE field a blank is the NULL: it is what
        a caller's type-only filter yields when the SERVER SAID NOTHING, and saying nothing is
        exactly what the null is for. Refusing it raises out of a constructor that runs AFTER
        a review's bytes are in hand and OUTSIDE any caller's try, which destroys a completed
        review over an unreadable record — the one thing this tool must never do. `request`'s
        `_reported_model` already answers an unreadable run record with null rather than
        failing the delivery; a blank arriving through its filter is that same answer wearing
        a different shape.
        """
        for field in fields(self):
            annotation = str(field.type)
            nullable = "None" in annotation or "Optional" in annotation
            value = getattr(self, field.name)
            if nullable and value is None:
                continue
            if not isinstance(value, str):
                raise TypeError(
                    "provenance: " + field.name + " is " + _type_of(value)
                    + "; §7.8 requires a string" + (" or null" if nullable else "")
                )
            if not value.strip():
                if nullable:
                    # NORMALISED, NOT MERELY TOLERATED. `as_dict` publishes this straight into
                    # the delivered envelope, so leaving the blank in place would put an empty
                    # string where a reader looks for an identity — the fabricated-provenance
                    # shape the nullability valve exists against. One meaning, one value.
                    object.__setattr__(self, field.name, None)
                    continue
                raise ValueError(
                    "provenance: " + field.name + " is blank. An unset identity is an error, "
                    "never a guessed default — and never an empty string standing in for one."
                )

    def as_dict(self) -> dict:
        return {
            "service_instance": self.service_instance,
            "channel": self.channel,
            "requested_model": self.requested_model,
            "server_reported_model": self.server_reported_model,
            "attested_route": self.attested_route,
        }


def _require(name: str, value: Any) -> Any:
    """An unset required value is an error, never a guessed default."""
    if value is None or (isinstance(value, str) and value.strip() == ""):
        raise ValueError("envelope: " + name + " is required and was not supplied")
    return value


# --- tool-authored findings, in layer 2 (R4, R23) -------------------------------------------


def _tool_finding(severity: str, category: str, title: str, body: str, recommendation: str) -> dict:
    """A finding the TOOL wrote. It has the layer-1 finding SHAPE so a caller can render it
    beside the model's, and it lives in a layer-2 key so its authorship is a property of
    where it sits rather than a claim inside it.
    """
    if severity not in envelope_schema.SEVERITY_VALUES:
        raise ValueError("tool finding severity " + repr(severity) + " is not in the enum")
    if category not in envelope_schema.CATEGORY_VALUES:
        raise ValueError("tool finding category " + repr(category) + " is not in the enum")
    return {
        "severity": severity,
        "category": category,
        "title": title,
        "body": body,
        "file": None,
        "line_start": None,
        "line_end": None,
        "basis": "mechanical",
        "evidence_quote": None,
        "recommendation": recommendation,
    }


def _contract_echo(document: dict | None, artifact_class: str) -> tuple[dict, dict | None]:
    """Compare the class the tool appended against the class the model echoed.

    The null-or-unequal echo is the ONLY thing that can see an absent contract, which is
    otherwise byte-identical to an applied one. The DECLARED half comes from the caller's
    request — never from `packet` (§3.1).

    The finding is pinned at the caller-or-environment severity, not the blind one: the
    review happened and its text is in hand; what is in doubt is whether the contract reached
    the reviewer, and that is mid-loop actionable (check the contract file, check the packet).
    AN ECHO HAS TO EXIST BEFORE IT CAN AGREE, AND `None` IS TWO DIFFERENT FACTS HERE. It is
    the echo of an uncontracted review, and it is also what a document that does not exist
    yields. Reading them as one made a run that produced NO PARSED DOCUMENT AT ALL record
    `agrees: true` under the declared no-contract class — a successful contract echo from a
    reviewer that emitted nothing. `no_review` already records the equivalent no-document
    state as false; testing for the KEY, rather than reading its value, is what makes the two
    paths answer alike.
    """
    expected = None if artifact_class == "none" else artifact_class
    present = isinstance(document, dict) and "artifact_class_echo" in document
    echoed = document["artifact_class_echo"] if present else None
    agrees = present and echoed == expected
    record = {"declared": artifact_class, "expected": expected, "echoed": echoed, "agrees": agrees}
    if agrees:
        return record, None
    return record, _tool_finding(
        SEVERITY_TOOL_FAULT,
        "stability",
        "Artifact-class echo does not match the contract the tool appended",
        "The tool appended the contract for " + repr(expected) + " and the reviewer echoed "
        + repr(echoed) + ". A null or unequal echo is the only observable that separates a "
        "review conducted under the contract from one conducted without it; the two are "
        "otherwise byte-identical. This finding is TOOL-authored — the reviewing model did "
        "not write it and cannot write into the key space it sits in.",
        "Confirm the contract file resolved and reached the packet before treating this "
        "review as contracted.",
    )


# --- the two constructors -------------------------------------------------------------------


def deliver(
    *,
    model_text: str,
    artifact_class: str,
    provenance: Provenance,
    error_condition: str | None = None,
    caller_notes: Sequence[str] | None = None,
) -> dict:
    """Build the layer-2 delivery envelope for a run that produced model bytes.

    R3 — THE ORDER IS THE DESIGN. The model's bytes are captured into layer 2 FIRST, before
    any parse and before any error classification decides what to emit. An answer recovered
    under an error condition DOWNGRADES; it never becomes a free pass, and it is never
    replaced by the error. R17 is the same rule from the environment's side: an environment
    signal is a heuristic about the run, while the model's output is evidence, and evidence
    wins.

    THE CALLER HAS TWO CHANNELS INTO THIS ENVELOPE AND THEY ARE NOT INTERCHANGEABLE.

    `error_condition` is a description of something the SUPERVISOR or TRANSPORT observed —
    a non-zero exit, an interrupt, a truncated stream. Passing one can only make the
    delivered verdict more blocking. It can never make it less, and it never deletes a byte.
    IT IS BLOCKING BY CONSTRUCTION, AND THE TWO HALVES ARE BLOCKED BY DIFFERENT MEANS. The
    VERDICT is floored by the `stability` finding it authors, which is enough because every
    severity in the enum keeps the verdict off `approve` whatever its category. The LOOP GATE
    is held open by a test on `error_condition` itself, and it has to be: the must-fix-high
    test (§2.3) asks whether a high sits in the ARTIFACT CLASS's primary category, so routing
    the loop gate through it made the blocking channel block for some declared classes and
    not others. There is no way to pass an error condition quietly.

    `caller_notes` is the other half, and it exists because that was the ONLY channel. A
    caller with something to record about a run that is otherwise FINE — which prompt file
    was sent, that a retry succeeded, which contract path resolved — had to either say it
    through `error_condition`, which fails the review to say it, or not say it. These notes
    reach the reader and author NOTHING: no finding, no count, no floor. Every value the
    verdict and the loop gate are derived from is computed without reading this list, which
    is why a note cannot move either one.

    They are LABELLED on the way in. `tool_notes` is one list holding two authorships now,
    and where a note sits is no longer enough to say who wrote it — the prefix is, and it is
    the same reason the tool's findings live in a layer-2 key rather than claiming their
    authorship in prose (R23).

    THIS IS THE ONLY CONSTRUCTOR THAT ACCEPTS MODEL BYTES, and that is R17's structural half:
    `no_review` has no parameter for them, so a `too-large` or `tooling-failure` cannot be
    authored over an answer that was already received.
    """
    if not isinstance(model_text, str):
        raise TypeError("model_text must be str; layer 1 is delivered as the model wrote it")
    _require("artifact_class", artifact_class)
    if not isinstance(provenance, Provenance):
        raise TypeError("provenance must be a Provenance — §7.8 requires all three identities")
    # REFUSED HERE, BESIDE THE OTHER ARGUMENT CHECKS, AND FOR THE SAME REASON: a value that
    # reaches `tool_notes` malformed is raised out of `json.dumps` much later, at a site with
    # no idea which caller supplied it. A bare `str` is refused explicitly because it IS a
    # sequence of str — of one-character ones — so the permissive read of this parameter
    # delivers a sentence one letter per note and nothing complains.
    if caller_notes is not None:
        if isinstance(caller_notes, (str, bytes)) or not isinstance(caller_notes, Sequence):
            raise TypeError(
                "caller_notes must be a sequence of str (a bare str is not one for this "
                "purpose — it would be delivered one character per note); got "
                + _type_of(caller_notes)
            )
        for position, note in enumerate(caller_notes):
            if not isinstance(note, str):
                raise TypeError(
                    "caller_notes[" + str(position) + "] is " + _type_of(note)
                    + "; every note must be a str"
                )

    # 1. THE ANSWER IS KEPT FIRST. Nothing below can remove it from the document.
    delivery: dict[str, Any] = {
        "delivery_version": DELIVERY_VERSION,
        "model_text": model_text,
    }
    notes: list[str] = []
    tool_findings: list[dict] = []
    findings_unreadable = False

    # 2. Only now: parse, and classify.
    document: dict | None
    stream: list[dict] = []
    try:
        stream = parse_stream(model_text)
        document = merge_documents(stream)
    except StrictParseError as exc:
        document = None
        notes.append("strict verdict parse refused the stream: " + str(exc))
    except Exception as exc:
        # DEGRADE, NEVER DESTROY — and this arm is the reason `_rank_of` exists. An
        # unhashable verdict made `merge_documents` raise TypeError, and the raise escaped
        # this function entirely: no envelope, no `model_text`, nothing. That is worse than
        # every failure this module is built to survive.
        #
        # A BROAD `except` IS THE RIGHT SHAPE **HERE** AND THE WRONG SHAPE ONE LEVEL UP, and
        # the difference is where it routes. The measured false-clean pathway in this project
        # was a broad `except` that routed into the NO-REVIEW envelope, which carries zero
        # findings and therefore reads as a clean pass to any counts-only caller. This one
        # routes into the DEGRADE path below, where `document` is None: non-conforming, a
        # blocking tool finding authored, verdict floored away from `approve`, and every byte
        # already preserved above. It cannot produce a pass.
        document = None
        notes.append(
            "the multi-document merge failed unexpectedly and the stream was degraded "
            "rather than delivered: " + type(exc).__name__ + ": " + str(exc)
        )
    else:
        if len(stream) > 1:
            # Report multiple complete documents rather than silently selecting
            # one. Canonical merge keeps their findings and strongest verdict.
            notes.append(
                "the reviewer emitted more than one complete JSON document ("
                + str(len(stream)) + "); the most blocking verdict was delivered and every "
                "byte of every document is preserved above under `model_text`"
            )
            if any(not isinstance(d.get("findings"), list) for d in stream):
                notes.append(
                    "at least one of those documents carried a `findings` value that is not "
                    "a list; its entries could not be read and are absent from the union, "
                    "and a blocking finding below says so. The readable findings were kept."
                )
                findings_unreadable = True
            if len(stream) > 1:
                notes.append(
                    "the summary, contract echo and schema version come from the most "
                    "blocking document; the findings are the union across all of them, so "
                    "the summary may not describe every finding listed"
                )

    if document is None:
        reasons = ["no JSON document could be recovered from the reviewer's output"]
        conforming = False
    else:
        # THE GATE READS EVERY DOCUMENT, NOT THE FOLD OF THEM, AND THAT IS WHAT MAKES THE
        # MERGE'S MONOTONICITY TRUE RATHER THAN CLAIMED. `merge_documents` takes every key
        # but `findings` from the most blocking document, so a nonconforming EARLIER
        # document is replaced by a later conforming one and this check never meets it.
        # Measured on this code: a stream whose FIRST document alone was refused was
        # delivered as `approve`, zero findings, fix loop closed. Every conformance
        # dimension is reachable that way; a missing `summary` was merely the cheapest.
        #
        # THE STREAM IS STILL MERGED AND STILL PRESERVED — refusing multi-document streams
        # is the defect this build exists to end. What a nonconforming document costs is
        # the APPROVAL, not the review: the reasons name which document and which clause,
        # the blocking finding below is authored from them, and every byte is already under
        # `model_text`.
        reasons = conformance_reasons(document)
        for position, parsed in enumerate(stream):
            # The merge returns a single-document stream unchanged, so identity — not a
            # length test — is what says this document has already been checked.
            if parsed is document:
                continue
            reasons.extend(
                "document " + str(position + 1) + " of " + str(len(stream)) + ": " + reason
                for reason in conformance_reasons(parsed)
            )
        conforming = not reasons

    # 3. EVERY TOOL FINDING IS AUTHORED BEFORE ANY GATE READS ONE. The order used to be the
    # other way round for the echo finding, and the consequence was measured: a conforming
    # `approve` whose artifact-class echo was missing came back as `approve` sitting beside a
    # tool finding that said the contract may never have reached the reviewer, with the fix
    # loop stopped. A finding that cannot move the verdict is decoration, and a CRITICAL one
    # that cannot is the false-clean this tool exists to prevent. Nothing between here and
    # the gate may author a finding.
    if not conforming:
        tool_findings.append(
            _tool_finding(
                SEVERITY_TOOL_BLIND,
                "stability",
                "The reviewer's document did not conform, so the tool authored this verdict",
                "The complete text the reviewer produced is preserved verbatim under "
                "`model_text`, and the parsed value, where one existed, under `review`. "
                "Nothing was trimmed and nothing was discarded. The delivered verdict is the "
                "tool's because the reviewer's own label could not be established from a "
                "conforming document, and a label that cannot be established is never read "
                "as a pass. Refusals: " + "; ".join(reasons),
                "Read `model_text` — the review may be complete and merely malformed.",
            )
        )

    if error_condition is not None:
        notes.append("error condition observed by the caller: " + error_condition)
        tool_findings.append(
            _tool_finding(
                SEVERITY_TOOL_FAULT,
                "stability",
                "An error condition was observed around a review that produced output",
                "The reviewer's output was captured before this condition was classified, "
                "and it is delivered in full. The condition floors the verdict away from "
                "`approve`; it does not replace the answer. Observed: " + error_condition,
                "Treat the findings as real and the run as unreliable, in that order.",
            )
        )

    if findings_unreadable:
        # THE FAULT IS A FINDING, NOT A DELETION. An earlier draft signalled this by putting
        # the unreadable value into `findings`, which removed real findings from the
        # structured review to report a problem — the same shape as the defect this whole
        # change exists to end. The readable findings are kept; this says what was lost.
        tool_findings.append(
            _tool_finding(
                SEVERITY_TOOL_BLIND,
                "stability",
                "One of the reviewer's documents carried an unreadable findings value",
                "The reviewer emitted more than one JSON document and at least one of them "
                "gave `findings` a value that is not a list. Its entries, if it had any, "
                "could not be read and are NOT in the delivered union; the findings from "
                "every readable document ARE. The complete text is preserved verbatim under "
                "`model_text`. This finding is TOOL-authored.",
                "Read `model_text` — a finding the tool could not parse may still be there.",
            )
        )

    echo_record, echo_finding = _contract_echo(document, artifact_class)
    if echo_finding is not None:
        tool_findings.append(echo_finding)

    # 4. ONE ASSESSMENT OVER BOTH AUTHORSHIPS. The same deny-by-default rule and the same
    # severity vocabulary are applied to the tool's findings as to the model's — a severity
    # means the same thing whoever wrote it, and a gate that reads only one of the two sets
    # can be walked past by writing nothing.
    #
    # THE TOOL'S FINDINGS ARE ASSESSED WITH NO PRIMARY CATEGORY, AND THAT IS THE ONE
    # DIFFERENCE. §2.3's must-fix test asks whether a `high` sits in the artifact class's
    # PRIMARY category — a question about the REVIEWER's findings, graded against what this
    # contract says it cares about. A tool finding is not a graded review finding: it says the
    # tool could not do its job, and every site that authors one names the same category,
    # which is some classes' primary and not others'. Grading them against the contract made a
    # tool fault must-fix under `--contract none` and NOT must-fix under a DECLARED contract.
    # The consequence was measured, not reasoned: a contract-echo mismatch — the ONLY
    # observable separating a review conducted under the contract from one conducted without
    # it — reached the caller with the same `must_fix_high_count` and the same
    # `loop_should_continue` as a genuinely clean review, on the two fields the caller contract
    # instructs callers to gate on. Reported by `wiki`, whose call sites all declare a
    # contract; reproduced on the deployed build and on this one, with controls firing.
    #
    # The two assessment arrays stay SEPARATE because each is keyed by index into its own
    # list: `assessments` is I10.3's parallel array over the model's `review.findings`, and
    # merging the tool's into it would break that correspondence. `counts` is the union,
    # because the union is what the verdict and the loop gate are derived from.
    primary = primary_category_for(artifact_class)
    counts, assessments, saw_unreadable = _assess(
        document.get("findings") if document is not None else None, primary
    )
    tool_counts, tool_assessments, _ = _assess(tool_findings, primary_category=None)
    for severity_name, tally in tool_counts.items():
        counts[severity_name] += tally

    # THE MUST-FIX COUNT IS TAKEN HERE, ABOVE THE VERDICT, BECAUSE §2.1.1 MAKES IT AN INPUT
    # TO THE FLOOR. It used to be summed below, after the verdict had already been written
    # — which is the whole reason the rule could not be expressed, rather than an oversight.
    # Neither assessment list is read or written between the assessment above and this line,
    # so moving the sum changes its value nowhere; only its availability moves.
    must_fix_high_count = sum(
        1 for a in assessments + tool_assessments if a["must_fix_high"]
    )
    model_verdict = document.get("verdict") if document is not None else None
    floor = _floor(counts, must_fix_high_count)

    # 5. THE VERDICT. Exactly one site in this module writes `approve`, and this is it.
    if (
        conforming
        and model_verdict == "approve"
        and floor is None
        and error_condition is None
    ):
        verdict = "approve"
    elif conforming and isinstance(model_verdict, str) and model_verdict in _VERDICT_RANK:
        # Read from the key (I10), then floored — never recomputed. A `needs-attention`
        # carrying an empty findings list comes through unchanged.
        verdict = model_verdict if floor is None else _more_blocking(model_verdict, floor)
        if error_condition is not None:
            verdict = _more_blocking(verdict, "needs-attention")
    else:
        # THE PERMISSIVE CONTENT PATH, AND IT CANNOT PRODUCE A PASS. Every byte the model
        # wrote is already in `model_text`, and the parsed value — if there was one — is
        # preserved structurally below. The verdict is the tool's, floored by what the
        # findings justify, and it is never `approve`. The finding explaining this branch was
        # authored in step 3, so the floor already carries its weight.
        verdict = _more_blocking(floor or "needs-attention", "needs-attention")

    if saw_unreadable:
        notes.append(
            "at least one finding's severity could not be positively recognised; it is "
            "counted as blocking and as must-fix"
        )

    # THE CALLER'S OWN NOTES, APPENDED LAST AND DELIBERATELY BELOW EVERY GATE. Placing them
    # here is the readable half of the guarantee the docstring makes: the findings are
    # authored above, the counts and the floor are taken above, the verdict is written above,
    # and this list is read by none of them. A reader asking "can a note change the answer?"
    # can see that everything the answer is made of has already happened.
    for note in caller_notes or ():
        notes.append("caller note: " + note)

    if error_condition is not None:
        # AN ERROR CONDITION HOLDS THE LOOP OPEN UNCONDITIONALLY, AND IT CANNOT BE ROUTED
        # THROUGH THE MUST-FIX TEST. It blocks by authoring a `stability` high, and §2.3 makes
        # a high must-fix only in the artifact class's PRIMARY category — so for every class
        # whose primary category is not `stability` the condition moved the verdict and left
        # this gate reading zero. Measured across the classes a caller can declare: the
        # channel blocked for some and not for others, which is not a property an error
        # condition is allowed to have. The docstring above promises it moves BOTH; the
        # promise is kept here, where the artifact class cannot reach it.
        loop_should_continue = True
    elif not isinstance(document.get("findings") if document else None, list):
        # The not-a-list case produces no per-finding assessments, so the loop gate would
        # read zero from an envelope whose findings are unreadable. Deny by default.
        loop_should_continue = True
    else:
        loop_should_continue = counts["critical"] > 0 or must_fix_high_count > 0

    delivery.update(
        {
            "status": STATUS_REVIEWED,
            "verdict": verdict,
            # THE UNION, model plus tool. It is what the verdict and the loop gate above were
            # derived from, so a reader can re-derive them; the two authorships stay
            # separable through `assessments` / `tool_assessments` and `review` /
            # `tool_findings`.
            "counts": counts,
            "must_fix_high_count": must_fix_high_count,
            "loop_should_continue": loop_should_continue,
            "assessments": assessments,
            "tool_assessments": tool_assessments,
            "tool_findings": tool_findings,
            "tool_notes": notes,
            "provenance": provenance.as_dict(),
            "artifact_class": artifact_class,
            "primary_category": primary,
            "contract_echo": echo_record,
            "review_conforming": conforming,
            "review_nonconformance": reasons,
            # R23 — THE ONE RESERVED SLOT, un-forgeable BY CONSTRUCTION. Layer 1 is NESTED
            # here, never merged into layer 2, so the model cannot reach a single key above.
            # The consumer is a PROGRAM: a caller that indexed a key of the model's document
            # can still reach it by path, which prose in a body string never allowed.
            "review": document,
        }
    )
    return delivery


def no_review(
    *,
    status: str,
    fault: str,
    title: str,
    detail: str,
    recommendation: str,
    artifact_class: str,
    provenance: Provenance,
    run_state: str | None,
    quiet_seconds: float | None = None,
    last_frame_at: str | None = None,
) -> dict:
    """Build the layer-2 envelope for a run in which NO REVIEW HAPPENED.

    §7.4 step 1, and it is not a formality: `tooling-failure` and `too-large` carry zero
    findings, so any counts-only rule reads them as a clean pass. `verdict` is None here
    rather than a token, so there is nothing for a counts-only reader to misread as approve.

    R4's SPLIT is a required argument. `FAULT_UNKNOWN_REVIEW_STATE` is for an envelope
    authored when the tool cannot know what the review would have said;
    `FAULT_CALLER_OR_ENVIRONMENT` is for a caller-input or environment fault, which is
    mid-loop actionable. Flattening the two is what the row exists against.

    `recommendation` IS REQUIRED AND HAS NO DEFAULT, for the reason already written beside
    `fault`: a default is how one of the two halves quietly becomes the other. This parameter
    exists because it had one. A single hardcoded string told every caller to resolve the
    condition and RE-RUN — including the caller reporting a review that is still running and
    must not be relaunched, so one document said *do not launch a second review* and
    *re-run* at once, in the field an operator acts on. What to do about a condition depends
    entirely on which condition it is, and only the caller knows that.

    WHAT THE ENVELOPE STILL OWNS is the sentence about this document's own shape, which is
    true of every no-review envelope and which no caller knows better. It is appended to
    whatever the caller passes rather than restated at each call site, so it cannot be
    suppressed from any argument: it is the sentence that stops a counts-only reader treating
    an empty findings list as a clean pass.

    R17: this function has no parameter for model bytes. If bytes exist, `deliver` is the
    only constructor that accepts them, so an environment signature cannot overwrite an
    answer that was already received.

    `run_state` IS THE FIELD A PROGRAM BRANCHES ON, AND IT IS REQUIRED FOR THE REASON `fault`
    IS. It carries `supervisor`'s own run-state constant VERBATIM — this module never spells
    one out, because a second copy of a state name is free to drift with nothing that would
    notice. `None` is a real answer and means no run state applies: every refusal decided
    before a run existed reaches this function, and inventing a state for those would be
    provenance nothing read.

    WHY IT EXISTS AT ALL. `describe_run` already distinguishes four states and the caller
    that composes this document already branches on them — and then the distinction reached
    the consumer only as four different prose titles. Four states that a human can tell apart
    by READING and a program cannot tell apart by any field is the adjacency failure this
    tool is built against, one floor up from the counts-only misreading above.

    WHAT IT DOES NOT PROMISE, and this must not be oversold on the strength of a new field.
    `in-flight` means the record carries no terminal update. It is NOT a claim that anything
    is alive — `supervisor` says so above its own constants and gives the reason — so a run
    whose process tree was killed reports `in-flight` here and always will. Measured, not
    inferred. `quiet_seconds` is carried beside it as the record's own last written reading,
    copied out rather than differenced against a clock (C-34): two collects far apart
    returning the SAME figure is CONSISTENT WITH a run that stopped being written to, and is
    not proof of one, because a live review that is genuinely quiet reads identically. It is
    a weak signal and is documented as one.

    THE DELIVERY VERSION IS NOT BUMPED FOR THIS, and that is a decision rather than an
    oversight. Both fields are additions: no key changes meaning, none is removed, and every
    gate a consumer already branches on is untouched. Bumping would signal a break to every
    consumer pinning the current id in order to describe a change that breaks none of them.
    """
    if status not in RESERVED_STATUS:
        raise ValueError(
            "no_review status must be one of " + repr(RESERVED_STATUS) + ", got " + repr(status)
        )
    if fault not in _FAULT_SEVERITY:
        raise ValueError(
            "fault must be one of " + repr(tuple(_FAULT_SEVERITY)) + ", got " + repr(fault)
        )
    _require("title", title)
    _require("detail", detail)
    _require("recommendation", recommendation)
    _require("artifact_class", artifact_class)
    # TYPE ONLY, AND THE LIMIT IS DELIBERATE RATHER THAN LAZY. Checking MEMBERSHIP would need
    # either an import of `supervisor` — coupling layer 2 to the run store it is kept clear of
    # — or a copy of that vocabulary here, which is the drift the carry-it-verbatim rule exists
    # to prevent. The call site holds the constant and is the only place a wrong value could be
    # introduced. What this module can honestly refuse is a value of the wrong SHAPE, and the
    # empty string is the one that would reach a consumer looking like a state.
    if run_state is not None and (not isinstance(run_state, str) or not run_state.strip()):
        raise ValueError(
            "run_state must be a supervisor run-state constant carried verbatim, or None "
            "where no run state applies — got " + repr(run_state)
        )
    if last_frame_at is not None and (
        not isinstance(last_frame_at, str) or not last_frame_at.strip()
    ):
        # SHAPE ONLY, and the limit is the same one `run_state` states above: this
        # module does not own the timestamp format, and inventing a parser here would
        # be a second place that format is decided. The empty string is the one value
        # that would reach a consumer looking like a timestamp it could compare.
        raise ValueError(
            "last_frame_at must be the record's own written timestamp, or None where "
            "the record holds none -- got " + repr(last_frame_at)
        )
    if quiet_seconds is not None and (
        isinstance(quiet_seconds, bool) or not isinstance(quiet_seconds, (int, float))
    ):
        # `bool` is excluded BY NAME because it IS an int here, and would land on a consumer
        # as a plausible zero-or-one-second reading rather than as the type error it is.
        raise TypeError(
            "quiet_seconds must be the record's own written reading, or None — got "
            + repr(quiet_seconds)
        )
    if not isinstance(provenance, Provenance):
        raise TypeError("provenance must be a Provenance — §7.8 requires all three identities")

    tool_findings = [
        _tool_finding(
            _FAULT_SEVERITY[fault],
            "stability",
            title,
            detail,
            # CO-AUTHORED, AND THE SPLIT IS BY OWNERSHIP. The caller's half depends entirely
            # on which condition was hit; the clause below is a statement about THIS
            # document's own shape, true of every no-review envelope, and it is appended here
            # rather than restated at every call site so that no argument can reach or
            # suppress it (C-30).
            recommendation.rstrip() + " " + (
                "This envelope reports no review, and a reader that treats an empty findings "
                "list as a clean result is reading the failure this tool was built against."
            ),
        )
    ]
    # The same union `deliver` publishes, for the same reason: the one finding this envelope
    # carries is the tool's, and an all-zero `counts` beside a CRITICAL is precisely the
    # counts-only misreading the paragraph above names.
    #
    # THE TOOL'S FINDINGS ARE ASSESSED WITH NO PRIMARY CATEGORY, exactly as in `deliver` and
    # for the reason stated there. THE TEXT HERE USED TO ARGUE THE OPPOSITE, and it is
    # replaced rather than deleted because the way it failed is the point. It held that
    # letting `must_fix_high_count` fall out of §2.3's artifact-class test was deliberate and
    # harmless BECAUSE `loop_should_continue` below is unconditional. That premise is TRUE
    # HERE — the flag below is a literal. It was then offered as licence for `deliver` doing
    # the same, and in `deliver` `loop_should_continue` is DERIVED from
    # `must_fix_high_count`. A conclusion was carried from the one constructor where its
    # premise holds to the one where it does not, and the reasoning travelled without it.
    primary = primary_category_for(artifact_class)
    counts, tool_assessments, _ = _assess(tool_findings, primary_category=None)
    return {
        "delivery_version": DELIVERY_VERSION,
        "model_text": "",
        "status": status,
        # Fault is structured independently of run_state: caller/environment
        # refusals can occur before any run record exists.
        "fault": fault,
        # BESIDE `status`, BECAUSE THIS IS THE FIELD THAT SAYS WHICH no-review IT IS. `status`
        # says a review did not happen; this says what the run's own record supports saying
        # about it, in `supervisor`'s words. Both keys are ALWAYS present on a no-review
        # envelope — a key that appears only sometimes is a second thing a consumer has to
        # branch on — and None is a real answer meaning no run state applies.
        "run_state": run_state,
        "quiet_seconds": quiet_seconds,
        # THE ABSOLUTE READING BESIDE THE RELATIVE ONE, AND IT IS THE ONE THAT CAN AGE.
        # `quiet_seconds` is frozen at the moment the record was last written, so a run
        # whose tree was killed reports the same small figure forever -- measured, 0.0
        # on a run dead for days, which is the most reassuring value available. This is
        # the record's own timestamp carried verbatim: it names no duration and decides
        # nothing (C-34), and the caller does the comparison against its own clock.
        # IT IS NOT EVIDENCE OF DEATH. It is evidence a caller can act on, where the
        # document previously carried none.
        "last_frame_at": last_frame_at,
        # No token. A verdict here would be a label over a review that did not happen.
        "verdict": None,
        "counts": counts,
        "must_fix_high_count": sum(
            1 for a in tool_assessments if a["must_fix_high"]
        ),
        # Nothing was reviewed, so nothing is settled. The loop must not exit on this.
        "loop_should_continue": True,
        "assessments": [],
        "tool_assessments": tool_assessments,
        "tool_findings": tool_findings,
        "tool_notes": ["no review happened: " + fault],
        "provenance": provenance.as_dict(),
        "artifact_class": artifact_class,
        "primary_category": primary,
        "contract_echo": {
            "declared": artifact_class,
            "expected": None if artifact_class == "none" else artifact_class,
            "echoed": None,
            "agrees": False,
        },
        "review_conforming": False,
        "review_nonconformance": ["no model document was produced"],
        "review": None,
    }


# --- the one payload write (R2, R18) ---------------------------------------------------------


def encode_payload(document: dict, encoding: str = "utf-8") -> tuple[bytes, bool]:
    """Serialize the delivery envelope to bytes. Returns (bytes, escaped).

    R2 — THE WRITE ITSELF CAN DESTROY THE REVIEW. A lone surrogate anywhere in the model's
    text makes the whole encode raise, and a text stream encodes the entire string before
    buffering any of it, so nothing reaches the caller. The guard re-emits THE SAME JSON
    VALUE with every non-ASCII character escaped: the bytes differ, the value does not.

    NEVER A LENIENT ERROR HANDLER. `replace`, `ignore` and `xmlcharrefreplace` each rewrite
    the model's text and exit 0 — a silent content edit wearing the shape of a successful
    write. `errors` is `strict` on both attempts.

    `allow_nan=False` because a Python-only constant would produce a byte stream no JSON
    reader accepts; failing loud here beats publishing something unparseable.

    R18 — NOTHING IS APPENDED. Layer 1 sits nested under one key and is serialized as parsed;
    returning MORE than the model produced is as bad as returning less.
    """
    try:
        return (
            json.dumps(document, ensure_ascii=False, allow_nan=False).encode(
                encoding, errors="strict"
            ),
            False,
        )
    except UnicodeEncodeError:
        return (
            json.dumps(document, ensure_ascii=True, allow_nan=False).encode(
                encoding, errors="strict"
            ),
            True,
        )


def bind_payload_stream(stream) -> None:
    """Bind a payload stream to utf-8 STRICT. Encoding is bound, never inherited.

    An inherited console encoding is how non-ASCII review text was destroyed on this
    platform. A payload stream is strict so that a failure to encode RAISES and the guard in
    `write_payload` can act on it, rather than being silently absorbed by the stream.
    """
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        raise TypeError("payload stream cannot be rebound to utf-8 strict")
    reconfigure(encoding="utf-8", errors="strict")


def bind_diagnostic_stream(stream) -> None:
    """Bind a DIAGNOSTIC stream leniently. This is the one stream where that is correct:
    it carries no review content, and a diagnostic that cannot be written is a diagnostic
    lost at exactly the moment it was needed.
    """
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        raise TypeError("diagnostic stream cannot be rebound")
    reconfigure(encoding="utf-8", errors="backslashreplace")


def write_payload(document: dict, stream, diagnostic_stream=None) -> bool:
    """THE ONE WRITE. Returns True when the escaping fallback was used.

    The caller binds the streams (`bind_payload_stream`) before calling. `result` owns where
    the published copy goes and the atomic rename; this function owns the single write and
    the guard on it.

    THE RETURN VALUE IS ABOUT THE PAYLOAD AND NOTHING ELSE. Once the payload is written this
    function has done its job, and the courtesy line that follows must not be able to turn that
    into a failure — a caller told the write failed over a payload already on its way to disk
    acts on a false report about durable state, which is the worst answer this tool can give.
    """
    try:
        stream.write(json.dumps(document, ensure_ascii=False, allow_nan=False))
        return False
    except UnicodeEncodeError:
        stream.write(json.dumps(document, ensure_ascii=True, allow_nan=False))
        if diagnostic_stream is not None:
            # THE PAYLOAD IS ALREADY OUT. A failure here is a failure of the CHANNEL FOR SAYING
            # SO, and there is no second channel: the only place to report that the diagnostic
            # stream is broken is the diagnostic stream. The payload stream is not an option —
            # it carries JSON and prose written into it would corrupt the answer itself. So the
            # swallow is deliberate, and it is NARROWED to what a stream that has gone away
            # actually raises: OSError for a closed pipe, ValueError for a closed file object,
            # and UnicodeEncodeError — a ValueError — for a diagnostic stream that cannot encode
            # this line either. Anything else propagates, because a TypeError here means a
            # caller passed something that is not a stream, and hiding that is how a broken
            # diagnostic path stays broken for good.
            try:
                diagnostic_stream.write(
                    "[envelope] the payload stream could not encode the document verbatim; "
                    "re-emitting it with every non-ASCII character escaped. The JSON VALUE is "
                    "unchanged.\n"
                )
            except (OSError, ValueError):
                pass
        return True
