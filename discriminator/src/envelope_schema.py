"""Single model-output schema and vocabulary for review-envelope/2.

Packet construction, CLI transports and canonical parsing use this definition.
Tool-authored status, provenance, assessments and verdict floor belong to the
separate delivery envelope and must not enter the model's writable namespace.

Strict output requires closed key sets and every property to be required. File,
line, quote and contract-echo fields are therefore required but nullable. Non-null
anchors have their own floors and coupled branches; envelope parsing owns checks
such as reversed line ranges that this schema cannot express.
"""

# The literal `schema_version` value. A record written under this id and read by a `/1`
# reader fails loudly on this field, which is what the field is for (design §7.2a-6).
SCHEMA_ID = "review-envelope/2"

# --- the four closed vocabularies, declared once and referenced once each ---------------

# The canonical verdict ordering is applied by envelope's monotone floor.
VERDICT_VALUES = ("approve", "needs-attention", "fail")

# I4: `low` has left. A finding that would have been `low` is not emitted at all.
SEVERITY_VALUES = ("critical", "high", "medium")

# Ordering is the rubric's category tie-break and must remain synchronized with
# the locked rubric. Runtime asset loading cross-checks the vocabulary.
CATEGORY_VALUES = ("truthfulness", "stability", "usability", "flexibility")

# The rubric's two confidence bands, not a numeric confidence estimate.
BASIS_VALUES = ("mechanical", "judgment")

# --- the schema -------------------------------------------------------------------------

ENVELOPE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "discriminator review envelope (review-envelope/2, layer 1)",
    "description": (
        "The document the reviewing model produces under constrained decoding. Key sets "
        "and closed vocabularies follow the canonical rubric and preambles. Every object "
        "level that declares properties is strict-mode "
        "conformant (additionalProperties:false AND every property listed in required) "
        "because a non-strict schema is rejected upstream with invalid_request_body — which "
        "is why provenance is nullable rather than optional. The finding object's `anyOf` "
        "branches are not such a level: they restate the types of three already-declared and "
        "already-required keys to couple their nullability, and add no key of their own. "
        "SCOPE: this constrains the MODEL's "
        "output only. It does NOT describe the tool's delivery envelope, which carries "
        "status, provenance and the per-finding blocking boolean in a key space the model "
        "never writes; do not validate that against this file."
    ),
    "type": "object",
    "additionalProperties": False,
    # I3 pins this order and I5 edit 1 repeats it. It is the contract, not a preference.
    "required": [
        "schema_version",
        "verdict",
        "summary",
        "findings",
        "next_steps",
        "artifact_class_echo",
    ],
    "properties": {
        # READER: `envelope`'s layer-2 shape check, which refuses an id it does not know.
        # A one-value `enum` and not `const` — `enum` is the keyword this schema already
        # relies on under strict mode (I5 edit 2).
        "schema_version": {
            "type": "string",
            "enum": [SCHEMA_ID],
        },
        # READER: `envelope` layer 2 — the single site that derives the returned verdict
        # (design §7.4). Read from this key; NEVER recomputed from the finding counts.
        "verdict": {
            "type": "string",
            "enum": list(VERDICT_VALUES),
        },
        # READER: `result`, which publishes it to the calling process. The tool names the
        # review state in its OWN layer-2 summary; it does not edit this string.
        "summary": {
            "type": "string",
            "minLength": 1,
        },
        # READER: `envelope` layer 2 — the severity gate (§7.4) and the `blocking`
        # injection (I10.3) both walk this array.
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                # I3's per-finding block pins this order; I5 edit 4 repeats it.
                "required": [
                    "severity",
                    "category",
                    "title",
                    "body",
                    "file",
                    "line_start",
                    "line_end",
                    "basis",
                    "evidence_quote",
                    "recommendation",
                ],
                "properties": {
                    # READER: `envelope` layer 2's verdict gate and its deny-by-default
                    # blocking test (register R13).
                    "severity": {
                        "type": "string",
                        "enum": list(SEVERITY_VALUES),
                    },
                    # The canonical must-fix test uses high severity and the
                    # artifact class's primary category in delivery assessments.
                    "category": {
                        "type": "string",
                        "enum": list(CATEGORY_VALUES),
                    },
                    # READER: `result`, which publishes the finding to the calling process.
                    "title": {
                        "type": "string",
                        "minLength": 1,
                    },
                    # READER: `result`. This is the full explanation, and the founding rule
                    # is that no path returns less of it than the model produced.
                    "body": {
                        "type": "string",
                        "minLength": 1,
                    },
                    # READER: `result` — the anchor the caller resolves to locate the
                    # defect. Nullable so an unanchored finding is honest rather than
                    # fabricated; `minLength` because the empty string is NEITHER — it is
                    # not an honest null and it resolves to nothing.
                    "file": {
                        "type": ["string", "null"],
                        "minLength": 1,
                        "description": (
                            "Source path this finding is anchored to, or null when the "
                            "finding is not file-localized. NEVER invent a path to satisfy "
                            "the field, and NEVER answer with an empty string — null is how "
                            "an unanchored finding is stated."
                        ),
                    },
                    # READER: `result`, with `file`. `minimum` because source lines are
                    # 1-based, so a non-positive line resolves to nothing (same reasoning as
                    # `file`'s `minLength`: honest null, or a usable anchor, and nothing
                    # between).
                    "line_start": {
                        "type": ["integer", "null"],
                        "minimum": 1,
                        "description": (
                            "First line of the anchored range, or null when the finding is "
                            "not line-localized. Lines are 1-based. NEVER invent a line "
                            "number to satisfy the field."
                        ),
                    },
                    # READER: `result`, with `file`.
                    "line_end": {
                        "type": ["integer", "null"],
                        "minimum": 1,
                        "description": (
                            "Last line of the anchored range, or null when the finding is "
                            "not line-localized. Lines are 1-based, and this line is not "
                            "before `line_start`. NEVER invent a line number to satisfy the "
                            "field."
                        ),
                    },
                    # Retained evidence basis; no transport branches on this field.
                    "basis": {
                        "type": "string",
                        "enum": list(BASIS_VALUES),
                    },
                    # READER: `telemetry`, as the run record's evidence_quote_null_count
                    # (I10). That count is the only thing that can see a reviewer nulling
                    # every quote; it is invisible in every other field. `minLength` for the
                    # same reason as `file`'s, plus one this key alone has: the empty string
                    # is neither an honest null nor a span that makes anything checkable, AND
                    # it is not what that null count counts — so an empty quote is the one
                    # form of a missing quote nothing downstream can see.
                    "evidence_quote": {
                        "type": ["string", "null"],
                        "minLength": 1,
                        "description": (
                            "Shortest verbatim span of the reviewed artifact that makes this "
                            "finding checkable, or null when no single span carries the "
                            "defect (an omission, or a defect spread across the artifact). "
                            "NEVER paraphrase or invent a quote to fill this field, and "
                            "NEVER answer with an empty string — null is how a finding with "
                            "no single quotable span is stated."
                        ),
                    },
                    # READER: `result` — what the calling process or fixer acts on.
                    "recommendation": {
                        "type": "string",
                    },
                },
                # THE ANCHOR IS ONE VALUE SPREAD OVER THREE KEYS, so its nullability is
                # coupled rather than per-key. Without this branch set the model can emit a
                # line number with no file — provenance that is neither the honest null the
                # `NULLABILITY IS THE HONESTY VALVE` block describes nor an anchor the caller
                # can resolve. Strict mode forbids optional keys, so the coupling cannot be
                # spelled with `dependentRequired`: every key is always PRESENT and it is the
                # VALUES that must agree. These states, and no other:
                #   no anchor at all / a whole-file anchor / a line range inside a named file.
                # The branches are mutually exclusive by construction — `file`'s type separates
                # the unanchored state from the anchored ones, and `line_start`'s type
                # separates the whole-file anchor from the line range — so a half-stated
                # anchor matches no branch and is refused. They constrain NULLABILITY ONLY;
                # the value constraints stay on the properties above, where a reader looks.
                #
                # `anyOf` AND NOT `oneOf`, AND THE REASON IS MEASURED RATHER THAN STYLISTIC.
                # The server refuses `oneOf` here outright — verbatim, at `turn/start`:
                #   Invalid schema for response_format 'codex_output_schema':
                #   In context=('properties', 'findings', 'items'), 'oneOf' is not permitted.
                # `allOf` is refused the same way; `anyOf` is accepted. Because the branches
                # are mutually exclusive by construction, `anyOf` and `oneOf` admit exactly
                # the same documents, so this costs nothing. A local JSON-Schema validator
                # calls every one of the three well-formed, which is why this was caught by
                # driving a real turn and could not have been caught any other way.
                #
                # AND IT IS ENFORCED, NOT MERELY ACCEPTED. Asked point-blank for a line number
                # with no file: without these branches the model emitted one; with them, under
                # the same prompt, it could not. Acceptance of a schema is a receipt for what
                # was recorded, never proof that anything constrains the decoder.
                #
                # NOT HERE: `line_end` not before `line_start`. That is a comparison between
                # two values, which this vocabulary cannot express, and this module is a
                # definition with no validation in it. `envelope`'s parser owns that check.
                # THE BRANCHES ARE GENERATED, BELOW THIS LITERAL — see `_anchor_branch`.
                # They cannot be written out here: each branch must itself be a fully closed
                # object, and a branch naming only the anchor's three keys makes the other
                # seven "additional" and is refused. Generating them from this very object
                # also means a key added to a finding cannot be forgotten in three copies.
            },
        },
        # READER: `result`, which publishes them to the calling process. Inherited from
        # `review-envelope/1` unchanged; no component of this tool branches on it.
        "next_steps": {
            "type": "array",
            "items": {
                "type": "string",
                "minLength": 1,
            },
        },
        # READER: `envelope` layer 2's contract-echo check. The tool knows which class it
        # appended; on a null or unequal echo it authors a finding of its own. That is the
        # only thing that can see an absent contract, which is otherwise byte-identical to
        # an applied one. The finding is TOOL-authored — layer 2 — never a model finding.
        "artifact_class_echo": {
            "type": ["string", "null"],
            "description": (
                "The artifact class named in the contract appended to this prompt, echoed "
                "verbatim; null when the prompt named none. NEVER guess a class — null is "
                "the correct answer for an uncontracted review."
            ),
        },
    },
}


# --- the anchor coupling, generated rather than written out ------------------------------
#
# WHY THIS IS CODE AND NOT THREE DICT LITERALS, measured against the real server rather than
# reasoned about. The server applies the strict-mode rule to EVERY object level that declares
# properties, branches included: each one needs `additionalProperties: false` and every
# declared property in `required`. A branch naming only the anchor's three keys therefore
# turns the finding's other seven keys into additional properties and the whole schema is
# refused before the model is ever asked anything. Building each branch from the finding
# object itself makes that impossible to get wrong, and makes a key added to a finding
# arrive in all three branches without anyone remembering to copy it.
_FINDING_SCHEMA = ENVELOPE_SCHEMA["properties"]["findings"]["items"]

#: The permitted anchor states as (file type, line type), in the order a reader meets them
#: in the comment above: no anchor at all; a whole-file anchor; a line range inside a file.
#: Any other combination — most importantly a line number with no file — matches no branch.
_ANCHOR_STATES = (("null", "null"), ("string", "null"), ("string", "integer"))


def _anchor_branch(file_type: str, line_type: str) -> dict:
    """One fully closed copy of the finding object, with the anchor's three types narrowed.

    Value floors stay on non-null types. On null-only properties they are inert under
    JSON Schema but rejected by Claude's strictTypes compiler; omit only those
    inapplicable keywords without changing the accepted instances.
    """
    narrowed = dict(_FINDING_SCHEMA["properties"])
    narrowed["file"] = {**narrowed["file"], "type": file_type}
    narrowed["line_start"] = {**narrowed["line_start"], "type": line_type}
    narrowed["line_end"] = {**narrowed["line_end"], "type": line_type}
    if file_type == "null":
        narrowed["file"].pop("minLength", None)
    if line_type == "null":
        narrowed["line_start"].pop("minimum", None)
        narrowed["line_end"].pop("minimum", None)
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(_FINDING_SCHEMA["required"]),
        "properties": narrowed,
    }


_FINDING_SCHEMA["anyOf"] = [_anchor_branch(f, ln) for f, ln in _ANCHOR_STATES]
