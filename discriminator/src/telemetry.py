"""Read-only views of a supervisor run record and its measurement horizon.

Supervisor owns the single run record and every write. This module reads one
explicit record/path, never creates a telemetry store or a second terminal update,
and reports the population covered by a quoted figure at read time.

Terminal recovery_state values are outcomes. Nonterminal state means only that
no outcome is recorded; it proves neither process death nor a stall. Unknown
states raise rather than being guessed. No clock or timeout is owned here.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from envelope_schema import SCHEMA_ID

__all__ = [
    "count_null_evidence_quotes",
    "read_record",
    "read_outcome",
    "horizon_statement",
]


# §5.1's `recovery_state` vocabulary, and which of its values is an OUTCOME.
#
# The classification, not the vocabulary, is this table's job — `supervisor.RecoveryState`
# owns the vocabulary and is where a value is DECIDED. This module is a leaf and imports no
# sibling but `envelope_schema`, so the terminal split is stated here rather than reached for
# across a module boundary.
#
# THE MECHANISM THAT KEEPS THIS COPY HONEST IS THE MISS, NOT THE MATCH. A `recovery_state`
# this build cannot classify raises. It is NOT read as non-terminal: doing so would report
# "no outcome" for a run that had one, silently, which is §7.9 row 3 inverted — and a second
# copy of a vocabulary with no mechanism keeping it true is exactly how that lands.
#
# `interrupted` is FALSE here and that is deliberate, not an oversight. §5.6 permits a review
# to span more than one attempt, so an attempt that stopped without completing and without
# the supervisor giving up has no outcome yet — the review is still open.
_RECOVERY_STATE_IS_TERMINAL = {
    "running": False,
    "resumed": False,
    "interrupted": False,
    "abandoned": True,
    "complete": True,
}


def count_null_evidence_quotes(findings: Sequence[Mapping[str, Any]]) -> int:
    """I10's `evidence_quote_null_count`, over layer-1 findings already parsed.

    A finding with no `evidence_quote` KEY raises rather than counting as a non-null. The
    key is required by `review-envelope/2`, so its absence means the document did not come
    through the schema; treating that as "not null" would hide the breakage in a count
    whose whole job is to be the only thing that can see a reviewer nulling every quote.

    This computes the value. It does not store it — the field belongs to the record
    `supervisor` writes at its single exit (§7.9 row 2).
    """
    total = 0
    for index, finding in enumerate(findings):
        if not isinstance(finding, Mapping) or "evidence_quote" not in finding:
            raise ValueError(
                f"telemetry: finding at index {index} has no 'evidence_quote' key; "
                "`review-envelope/2` requires it. Refusing to count it as non-null."
            )
        if finding["evidence_quote"] is None:
            total += 1
    return total


# -- read-only access, and it stays read-only (§7.9 row 4) -------------------------------


def read_record(path: str | os.PathLike[str]) -> dict:
    """Load a run record, refusing one written under a different envelope schema.

    §5.1's version guard: a record written under one schema and read by a later version
    must fail LOUDLY rather than be misparsed. A later run may READ a prior record — that
    is §5's recovery question — and may never rewrite it.

    `envelope_schema` owns `SCHEMA_ID`; this module imports it rather than restating it,
    because a second copy of a version constant is how a guard silently stops guarding.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"telemetry: {os.fspath(path)!r} is not a run record object.")
    found = data.get("schema_version")
    if found != SCHEMA_ID:
        raise ValueError(
            f"telemetry: run record at {os.fspath(path)!r} declares schema_version "
            f"{found!r}; this build reads {SCHEMA_ID!r}. Refusing to interpret it."
        )
    return data


def read_outcome(record: Mapping[str, Any]) -> str | None:
    """The outcome the run itself wrote, or None.

    Reads `recovery_state`, which IS the outcome — §7.9 requires the record to carry "what
    the outcome was", and §5.1's exhaustive table answers that with this field and no other.
    There is no `outcome` key to read and none is added.

    A TERMINAL value (`complete`, `abandoned`) is returned verbatim. A NON-TERMINAL value
    (`running`, `resumed`, `interrupted`) returns None, meaning EXACTLY that this record
    records no outcome. It is not death, not a stall, not a timeout, and it must never be
    substituted with one — §7.9 row 3: an outcome is written only by the run that had it,
    and row 4: v1 says nothing further about a record that has none.

    Two things RAISE rather than answering None, because None here is a claim about the run
    and must only be made about a record this build can actually read:
      - the key ABSENT — the document was not written by `supervisor.RunRecordStore`, and
        answering "no outcome" about it would be inventing a fact about a run whose record
        this is not;
      - a value this build does not recognise — see `_RECOVERY_STATE_IS_TERMINAL`.
    """
    if "recovery_state" not in record:
        raise ValueError(
            "telemetry: mapping has no 'recovery_state' key — it is not a run record "
            "written by `supervisor`. §5.1 makes that field the outcome, so a document "
            "without it has no outcome to report and none may be inferred for it."
        )
    state = record["recovery_state"]
    if state not in _RECOVERY_STATE_IS_TERMINAL:
        raise ValueError(
            f"telemetry: 'recovery_state' is {state!r}, which this build cannot classify as "
            f"terminal or non-terminal; §5.1's vocabulary is "
            f"{tuple(_RECOVERY_STATE_IS_TERMINAL)}. Refusing to read it as 'no outcome' — "
            "that would silently report a run that HAD an outcome as having none."
        )
    return state if _RECOVERY_STATE_IS_TERMINAL[state] else None


def horizon_statement(record: Mapping[str, Any]) -> str:
    """The sentence that must travel with any figure quoted from this record (C-17, §7.9).

    Aggregating across records is building a corpus, and the corpus is v2 (C-44). This
    speaks for one record because one record is what v1 has.

    The no-outcome branch does NOT say "no terminal update was written". That would be false
    for `interrupted`, which `supervisor` writes AT its single terminal write site — the
    update happened; the review is simply still open. It names the stored `recovery_state`
    verbatim (§5.1: §7.9 "REPORTS it and never rewrites it") and refuses the inference.
    """
    attempt = record.get("attempt")
    outcome = read_outcome(record)
    if outcome is not None:
        tail = (
            f"This record carries an outcome written by the run itself: {outcome!r}."
        )
    else:
        tail = (
            f"This record records NO outcome; its `recovery_state` is "
            f"{record['recovery_state']!r}. That means only that no outcome was written — "
            "it is not evidence of death, a stall, or any outcome, and no later run may "
            "label it as one."
        )
    return (
        f"Covers one discriminator run (attempt {attempt}) on the machine that wrote "
        "this record. It is one attempt, not one review and not one caller work unit; "
        "v1 keeps no corpus, so it is not a sample of anything. Records of runs that died "
        f"before their single exit have no outcome and are absent from any figure "
        f"taken over completed ones. {tail}"
    )
