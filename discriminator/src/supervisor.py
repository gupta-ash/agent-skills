"""Codex review process lifecycle, durable records and internal recovery.

Owns the timeout/kill, report-only liveness ledger, attempt count, run-record
ordering and disposition of every classified transport frame. Uninterpretable
frames are retained, counted and reported instead of disappearing. Transport
owns wire vocabulary and session fencing; result owns answer publication.

Each attempt has its own timed wait covering spawn, initialization, thread
open/resume, durable writes, dispatch and streaming. Teardown after that wait is
not bounded: process termination and record locking/writes can delay return past
the configured timeout. The timeout is not a wall-clock return guarantee.

Recovery cannot discriminate an active resumed turn from one needing reopening
when the transport's resume response supplies neither turn ID nor turn status.
Internal recovery is not a public cross-session resume command.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Protocol, Sequence

from envelope_schema import SCHEMA_ID
from transport import (
    FrameKind,
    N_AGENT_MESSAGE_DELTA,
    N_TURN_COMPLETED,
    ServerDied,
    ServerFrame,
    ThreadSession,
    TurnStartRecord,
)

# --------------------------------------------------------------------------------------
# THE ONE TIMEOUT.  §6.2 Tier A fixes the value; C-34 (SEVERE) fixes that there is exactly
# one of it, in one place, and that every attempt gets it independently.
#
# This maximum-runtime policy may stop live work; it is not an idle threshold.
#
# If you are about to add a second duration to this module — a poll interval, a grace period,
# a flush cadence, a join timeout, a liveness threshold — you are adding a second place a
# duration is decided, and it is forbidden.
# --------------------------------------------------------------------------------------
THE_TIMEOUT_SECONDS: float = 7200.0

# §5.6 — a resume CONSUMES an attempt, so one logical review is bounded at two independent
# runs. This is a COUNT OF RUNS, not a clock: it adds no duration decision and no dependency
# between attempts, which is the operative C-34 test. C-4 is the policy it enforces — retry
# once, then a tooling failure; never a same-family self-review.
MAX_ATTEMPTS: int = 2

# --------------------------------------------------------------------------------------
# The wire vocabulary.  IT IS NOT DEFINED HERE, and that is the repair.
#
# `transport` owns the wire (§3.1), and `transport.ServerFrame` already exposes `params`,
# `agent_message_delta`, `is_reasoning_delta`, `is_error_notification`, `will_retry` and
# `turn_status`. This module previously carried its own copies of the method names, its own
# `willRetry` field name and its own list of candidate delta keys, and read them back off a
# dict — one wire vocabulary in two places, free to drift, with nothing that would notice.
# The two method names below are IMPORTED from `transport` rather than restated, so there is
# exactly one definition of each. The decisions that read them are still this module's:
# terminal classification (§6.4) and the liveness ledger (§6.2).
# --------------------------------------------------------------------------------------

# §5.4a — the value that says a turn reached its end. Read from the frame's own `turn_status`
# accessor; the string itself is compared here because the COMPLETION DECISION is this
# module's, not the wire's.
TURN_STATUS_COMPLETED: str = "completed"


class SupervisorError(Exception):
    """Base for every loud failure this module raises."""


class ResumeRefused(SupervisorError):
    """§5 — a resume that would change the review is refused rather than performed."""


class RecordVersionMismatch(SupervisorError):
    """§5.1 — a record written under a different schema version fails loudly, not silently."""


class WireVocabularyDrift(SupervisorError):
    """A frame carried content this module could not read. Loud, because §7.3's founding rule
    is that no path returns less content than the model produced."""


class UnhandledFrameKind(WireVocabularyDrift):
    """`transport` classified a frame into a bucket this module's reader has no arm for.

    A drift in the wire vocabulary like its parent, but the one shape that must never be
    answered by doing nothing: the frame-disposition rule §3's structural requirement is that
    the arm which decides is TOTAL over the buckets, and that a bucket added later is a LOUD
    FAILURE rather than a line that falls quietly out of the bottom of the reader. A
    fall-through is what the defect this class exists to end actually WAS.
    """


# --------------------------------------------------------------------------------------
# THE FIVE BUCKETS, AND THE REFUSAL THAT KEEPS THEM FIVE (frame-disposition rule §3).
#
# `transport` owns the classification; this module owns what is DONE with each class. The
# set below is this module's claim to dispose of every one of them, and the refusal beneath
# it turns that claim into a check at IMPORT — the moment `transport` grows a sixth kind,
# which is "the point the bucket is added" rather than the much later moment such a frame
# first arrives on a stream. `_read_until_eof` carries the second half: an arm that raises
# rather than falling through, for a kind added to this set without an arm to match.
# --------------------------------------------------------------------------------------
DISPOSED_FRAME_KINDS: frozenset[FrameKind] = frozenset(
    {
        FrameKind.NOTIFICATION,
        FrameKind.SERVER_REQUEST,
        FrameKind.RESPONSE,
        FrameKind.ERROR_RESPONSE,
        FrameKind.UNPARSEABLE,
    }
)

# The tally key for an ordinary announcement that carried no method name. It is a KEY IN A
# COUNTER, and the counter's keys are method names, so the absence needs a name of its own:
# `None` is not a JSON object key and `str(None)` would sit in the same namespace as a method
# the server could legitimately call `None`. The angle brackets are what keep it out of that
# namespace — no method name on this wire contains one.
IGNORED_WITHOUT_METHOD_NAME = "<notification with no method>"


def _refuse_undisposed_kinds(kinds: Iterable[Any] = FrameKind) -> None:
    """Refuse a frame kind this module has no disposition for. Called at import.

    The argument exists so the refusal can be DRIVEN — a check that cannot be shown to fail
    on the fault it guards is not a check, and this one guards a fault that does not exist
    yet by construction. Default is the real vocabulary, so the shipping call takes none.
    """
    undisposed = [kind for kind in kinds if kind not in DISPOSED_FRAME_KINDS]
    if undisposed:
        raise UnhandledFrameKind(
            "transport classifies frames into a kind this module's reader has no arm for: "
            f"{[getattr(kind, 'name', kind) for kind in undisposed]}. Every kind must have a "
            "disposition before a stream carrying one is read; a kind with none would reach "
            "the bottom of the reader and be dropped without a count, which is the defect the "
            "frame-disposition rule exists to end."
        )


_refuse_undisposed_kinds()


class RecoveryState(str, Enum):
    """§5.1 — the record's own state. Its reader is §5's recovery path.

    §7.9 row 3: an outcome is written only by the run that had it, at that run's single exit.
    §7.9 row 4: nothing reclassifies another run's record. A record with no terminal update
    means exactly that no terminal update was written, and v1 says nothing further about it.
    """

    RUNNING = "running"
    RESUMED = "resumed"
    INTERRUPTED = "interrupted"
    ABANDONED = "abandoned"
    COMPLETE = "complete"


class StopReason(str, Enum):
    """Why an attempt stopped reading. §6.4 separates evidence of death from absence of life."""

    TURN_COMPLETED = "turn-completed"
    TIMEOUT = "timeout"
    ERROR_TERMINAL = "error-terminal"
    PROCESS_EXITED = "process-exited"
    CONNECTION_CLOSED = "connection-closed"
    READER_FAILED = "reader-failed"
    # The attempt never reached the stream: the spawn, the handshake, `thread/start`,
    # `thread/resume`, a durable write or `turn/start` raised. Kept distinct from
    # READER_FAILED because "we never opened the session" and "our reader broke" are
    # different findings and filing one under the other loses the difference.
    SETUP_FAILED = "setup-failed"
    # The attempt opened a thread, started a turn and read its stream — and then the TEARDOWN
    # after the one wait raised. Its own value for the same reason SETUP_FAILED has one: this
    # enum exists to stop one finding being filed under another, and reporting a failure that
    # happened after a thread, a turn and a record existed as "we never opened the session" is
    # exactly that loss.
    FINISH_FAILED = "finish-failed"
    # Not an attempt this invocation ran: a PRIOR attempt of the same review, read back off the
    # durable record a later process resumed from. Its own value because §5.1's record carries no
    # stop reason at all, and filing it under any of the above would report a cause this module
    # never observed.
    FROM_RECORD = "from-record"


# --------------------------------------------------------------------------------------
# What this module needs from `transport`.
#
# EVERY MEMBER BELOW IS COPIED FROM `transport.Transport`'s REAL SIGNATURE, INCLUDING THE
# DEFAULTED ARGUMENTS THIS MODULE NEVER PASSES. A Protocol is a claim about a neighbour that
# nothing at runtime will check, so the only thing that makes it true is having read the
# neighbour. `pid` and `server_info` are declared as PROPERTIES because that is what they are
# on `Transport`; `is_alive` is a plain method because that is what it is there.
#
# `reported_model` is deliberately ABSENT: it is not on the session at all, it is a field of
# the `ThreadSession` that `thread_start` and `thread_resume` RETURN.
# --------------------------------------------------------------------------------------
class TransportSession(Protocol):
    """A live `codex app-server` session, already through the mandatory `initialize`
    handshake (§4.1a — the handshake is transport's, not this module's).

    `close()` MUST be safe to call from the frame-reading thread and must be idempotent: on
    `Transport` it closes the SEND side only and marks the EOF that follows as one the client
    asked for. It is how §4.1a's drain rule is honoured — a terminal STATUS stops us SENDING,
    never stops us READING, because the stdout pump is untouched by it.
    """

    @property
    def pid(self) -> int:
        """The child's process id. Read by `kill_process_tree` at the one timeout."""

    @property
    def server_info(self) -> Mapping[str, Any]:
        """`initialize`'s result, carrying `userAgent`. RAISES if `initialize` has not run —
        this module never calls `initialize`, so it is the factory's job to have done so."""

    def thread_start(
        self,
        model: str | None = None,
        base_instructions: str | None = None,
        developer_instructions: str | None = None,
        config: Mapping[str, Any] | None = None,
    ) -> ThreadSession: ...

    def thread_resume(
        self,
        thread_id: str,
        model: str | None = None,
        base_instructions: str | None = None,
        developer_instructions: str | None = None,
        config: Mapping[str, Any] | None = None,
    ) -> ThreadSession: ...

    def turn_start(
        self,
        thread_id: str,
        prompt_text: str,
        output_schema: Mapping[str, Any] | None,
        effort: str | None = None,
        summary: str | None = None,
    ) -> tuple[str, TurnStartRecord]: ...

    def frames(self) -> Iterator[ServerFrame]:
        """Blocking. Yields classified frames until EOF. Raises `ServerDied` on an EOF the
        client did not ask for; after `close()` it simply ends."""

    def is_alive(self) -> bool:
        """Whether the child process is still running. §6.4 — process death is a silent EOF
        with no sentinel, so it must be evidenced out of band."""

    def close(self) -> None:
        """Ordinary teardown: closes the send side. Not the kill: the kill is this module's,
        and it happens in one place, on the one timeout."""


# --------------------------------------------------------------------------------------
# The liveness ledger.  §6.2 Tier B, C-42(a): IT REPORTS. IT NEVER TERMINATES.
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class LivenessSnapshot:
    """§6.2 Tier B — what the caller may read while the run is in flight."""

    frame_count: int
    last_frame_at: str | None
    longest_quiet_seconds: float
    current_quiet_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame_count": self.frame_count,
            "last_frame_at": self.last_frame_at,
            "longest_quiet_seconds": round(self.longest_quiet_seconds, 3),
            "current_quiet_seconds": round(self.current_quiet_seconds, 3),
        }


class LivenessLedger:
    """§6.1: a frame is positive evidence of life; silence is not evidence of death.

    This class reports activity and quiet intervals, but holds no threshold,
    session reference or kill capability. Its snapshot lives on this run's record;
    it does not aggregate another agent's or machine's state.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._frame_count = 0
        self._started_monotonic = time.monotonic()
        self._last_frame_monotonic: float | None = None
        self._last_frame_wall: str | None = None
        self._longest_quiet = 0.0

    def note_frame(self) -> None:
        now = time.monotonic()
        with self._lock:
            previous = self._last_frame_monotonic
            reference = self._started_monotonic if previous is None else previous
            quiet = now - reference
            if quiet > self._longest_quiet:
                self._longest_quiet = quiet
            self._frame_count += 1
            self._last_frame_monotonic = now
            self._last_frame_wall = _utc_now()

    def snapshot(self) -> LivenessSnapshot:
        now = time.monotonic()
        with self._lock:
            reference = (
                self._started_monotonic
                if self._last_frame_monotonic is None
                else self._last_frame_monotonic
            )
            current_quiet = now - reference
            longest = max(self._longest_quiet, current_quiet)
            return LivenessSnapshot(
                frame_count=self._frame_count,
                last_frame_at=self._last_frame_wall,
                longest_quiet_seconds=longest,
                current_quiet_seconds=current_quiet,
            )


# --------------------------------------------------------------------------------------
# The run plan — what this module carries but does not author.
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class RunPlan:
    """Everything one review run needs. Authored by `request` and `packet`; carried here.

    `session_settings` is the OPAQUE bundle `transport` applies to `thread/start` and, on a
    resume, to `thread/resume` — §5.3: the settings are named fields of the resume call, so a
    resume that omits them is a bug we would have to write. ITS KEYS ARE `transport`'s OWN
    PARAMETER NAMES and it is splatted into both calls, so this module never lists them: a
    list here is a second copy of `transport`'s signature, free to drift, and the drift is
    what this file was repaired for. A key `transport` does not take is a `TypeError` at the
    call rather than a setting silently dropped.
    """

    session_settings: Mapping[str, Any]
    packet_text: str
    # None is a VALUE this field must be able to carry, not a missing one — see `__post_init__`.
    output_schema: Mapping[str, Any] | None
    cwd: str
    artifact_class: str
    contract: str
    model_requested: str
    transport_requested: str
    packet_digest: str
    rubric_version: Mapping[str, str | None]

    def __post_init__(self) -> None:
        for name in (
            "packet_text",
            "cwd",
            "artifact_class",
            "contract",
            "model_requested",
            "transport_requested",
            "packet_digest",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise SupervisorError(f"RunPlan.{name} is required and must be a non-empty str")
        # THE UNCONSTRAINED TURN IS A MODE, AND None IS HOW IT IS SPELLED. `transport.turn_start`
        # takes `output_schema=None` and omits the wire key deliberately — R10's fail-open path
        # for a server without output-schema support, where the review still happens and only the
        # constraint is lost. Typing this field as a required Mapping made that documented mode
        # UNREACHABLE from the supervisor: the one component that builds the call could not
        # express the one argument the call was built to accept.
        #
        # An EMPTY mapping is still refused, and the two are not the same thing. None says "no
        # schema, on purpose"; `{}` is a schema object that constrains nothing while claiming to
        # be one, which no caller means and which `transport` would send on the wire.
        if self.output_schema is not None and (
            not isinstance(self.output_schema, Mapping) or not self.output_schema
        ):
            raise SupervisorError(
                "RunPlan.output_schema must be a non-empty mapping, or None for the explicit "
                "unconstrained turn (owner: `envelope`)"
            )
        if not isinstance(self.session_settings, Mapping):
            raise SupervisorError("RunPlan.session_settings is required (owner: `transport`)")
        # §5.1 — `rubric_version` is a TAGGED value, not a bare digest. `{"rubric": null}` is
        # producible only by the acceptance harness; the shipping tool refuses a run with no
        # asset (§7.2a-6), so this module validates the SHAPE and never invents the value.
        if (
            not isinstance(self.rubric_version, Mapping)
            or set(self.rubric_version) != {"rubric"}
            or not (
                self.rubric_version["rubric"] is None
                or isinstance(self.rubric_version["rubric"], str)
            )
        ):
            raise SupervisorError(
                "RunPlan.rubric_version must be {'rubric': <digest str>} or {'rubric': None}"
            )
        # A consistency CHECK, not a second application site: the fence directory this module
        # records must be the one `transport` was configured with (§7.5c — one enforced path).
        # `transport` takes `session_cwd` once, at construction, and has no per-call `cwd`
        # argument — so in the shipping caller this key is absent and this check is inert. It
        # stays because a key that reappeared would otherwise reach `thread_start` as a
        # splatted argument it does not accept, which is a worse error message than this one.
        declared = self.session_settings.get("cwd")
        if declared is not None and declared != self.cwd:
            raise SupervisorError(
                "RunPlan.cwd disagrees with session_settings['cwd']; the recorded directory "
                "must be the one transport applies"
            )


# --------------------------------------------------------------------------------------
# The durable run record.  §5.1 — and the ORDERING is the design, not a detail.
# --------------------------------------------------------------------------------------
@dataclass
class RunRecord:
    """§5.1's record. Every field is that table's; the table declares itself exhaustive.

    `model` and `transport` are mappings rather than scalars because §0.1 requires requested,
    server-reported and attested route to be SEPARATE and never collapsed. `attested_route`
    has no value in v1 — attestation does not exist (§0.1, §11 item 2) — and a reported value
    that the server did not give is left null rather than filled from the request, because
    filling it is the collapse §0.1 forbids.

    There is no `outcome` field and none is to be added: `recovery_state`'s terminal value IS
    the outcome, and it is what `telemetry` keys on.
    """

    thread_id: str
    turn_id: str | None
    cwd: str
    artifact_class: str
    contract: str
    model: dict[str, Any]
    transport: dict[str, Any]
    packet_digest: str
    schema_version: str
    accumulated_output: str
    recovery_state: str
    liveness_ledger: dict[str, Any]
    rubric_version: dict[str, Any]
    started_at: str
    attempt: int
    # §7.9 requires "when it ended" to be recorded. §5.1's table does not carry a row for it,
    # and that table declares itself exhaustive — the conflict is real and is reported rather
    # than resolved by dropping either requirement. §7.9's "what the outcome was" needs no
    # field: `recovery_state`'s terminal value IS the outcome.
    ended_at: str | None = None
    # §5 refuses a resume that would CHANGE the review, and the reviewer's own settings change it
    # more than anything else on this record. §5.1's table carries none of them — the same
    # conflict `ended_at` has, reported here rather than resolved by dropping the requirement.
    # A digest, for the reason `_settings_digest` gives.
    #
    # None means the record was written before this field existed. That is not a match: a resume
    # against it cannot be shown to be the same review, and `_refuse_incompatible_resume` refuses
    # it rather than reading the absence as agreement.
    session_settings_digest: str | None = None
    # §5 refuses a resume that would change the review, and THE RESPONSE CONSTRAINT CHANGES IT:
    # the same packet answered under a schema, under a different schema, and unconstrained are
    # three different reviews. `schema_version` above is not this — see `_output_schema_digest`,
    # which is also where the unconstrained mode gets a marker of its own.
    #
    # None means the record was written before this field existed, and it is refused for
    # `session_settings_digest`'s reason: an absence is not agreement.
    output_schema_digest: str | None = None
    # --- what the reader did with the frames it did not act on (frame-disposition rule §5) ---
    #
    # THE PROPERTY THESE FOUR BUY IS FORENSIC. Before them, a run that silently lost a frame and
    # a clean run were byte-identical in this file, which is how the defect went unnoticed for
    # as long as it did. They are the durable half of what `AttemptOutcome` reports; the counts
    # are the containers' own lengths and are never stored beside them, because a stored count
    # is a second representation free to disagree with the thing it counts.
    #
    # None of them is in `from_dict`'s required list, for `session_settings_digest`'s reason: a
    # record written before they existed reads them as empty rather than failing as malformed.
    # Empty is the honest reading — that run recorded no dispositions.
    unparseable_frames: list[dict[str, Any]] = field(default_factory=list)
    server_requests: list[dict[str, Any]] = field(default_factory=list)
    late_responses: list[dict[str, Any]] = field(default_factory=list)
    ignored_notification_counts: dict[str, int] = field(default_factory=dict)
    # --- what the reader could NOT attribute, and what it attributed elsewhere (§7.3) ---
    #
    # THESE TWO ARE DURABLE FOR THE REASON THE FOUR ABOVE ARE, AND THEY WERE NOT. `AttemptOutcome`
    # has reported both since the identity repair, but the record carried neither — so a review
    # that was interrupted and then resumed reported the prior attempt as having seen no foreign
    # frames and no unattributed ones, whatever it had actually seen. The evidence that answer
    # bytes were accepted with no ids to check them against, or that another turn's frames arrived
    # on this stream, stopped existing at the moment a review was retried. A resume does not
    # un-see them, and this module's rule is that if the tool knows, the caller is told.
    #
    # `None` IS "THIS RECORD DOES NOT CARRY THAT EVIDENCE", AND IT IS NOT THE SAME VALUE AS
    # EMPTY. A record written before these fields existed lacks the keys entirely; reading that
    # absence as `[]` and `0` would say "this attempt saw none", which is the substitution the
    # defect was made of. `from_dict` keeps the two apart and `_attempt_from_record` reports the
    # absence as an explicit unknown.
    #
    # NEITHER IS IN `from_dict`'s REQUIRED LIST, and the choice is REPORT-UNKNOWN rather than
    # REFUSE-THE-RESUME. The two absent digests above are refused because their absence means the
    # resume cannot be shown to be the SAME REVIEW — proceeding would run a different one. An
    # absent attribution field means something else: the resume is the same review, and it is the
    # PRIOR ATTEMPT'S REPORT that is incomplete. Refusing there would destroy the answer text and
    # the unreadable-line evidence the record does hold, over evidence that is empty on almost
    # every run — a loud total loss traded for a silent partial one. So it is reported, loudly,
    # as unknown.
    foreign_frames: list[dict[str, Any]] | None = None
    unattributed_frame_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "thread_id": self.thread_id,
            "turn_id": self.turn_id,
            "cwd": self.cwd,
            "artifact_class": self.artifact_class,
            "contract": self.contract,
            "model": dict(self.model),
            "transport": dict(self.transport),
            "packet_digest": self.packet_digest,
            "schema_version": self.schema_version,
            "accumulated_output": self.accumulated_output,
            "recovery_state": self.recovery_state,
            "liveness_ledger": dict(self.liveness_ledger),
            "rubric_version": dict(self.rubric_version),
            "started_at": self.started_at,
            "attempt": self.attempt,
            "ended_at": self.ended_at,
            "session_settings_digest": self.session_settings_digest,
            "output_schema_digest": self.output_schema_digest,
            "unparseable_frames": [dict(entry) for entry in self.unparseable_frames],
            "server_requests": [dict(entry) for entry in self.server_requests],
            "late_responses": [dict(entry) for entry in self.late_responses],
            "ignored_notification_counts": dict(self.ignored_notification_counts),
            # `None` is written through as null rather than flattened to `[]` / `0`: null is
            # "not recorded" and the empty pair is "recorded, and there were none".
            "foreign_frames": (
                None
                if self.foreign_frames is None
                else [dict(entry) for entry in self.foreign_frames]
            ),
            "unattributed_frame_count": self.unattributed_frame_count,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "RunRecord":
        version = raw.get("schema_version")
        if version != SCHEMA_ID:
            # §5.1 — a record whose `schema_version` DIFFERS from this build's fails loudly
            # rather than being misparsed. That is the whole of what this guard promises, and
            # it is narrower than the sentence it replaces: a field added to the record while
            # the marker stays put is invisible to it, and `session_settings_digest` is such a
            # field. The reader below carries that case instead — an older record simply lacks
            # the key, reads as None, and is REFUSED at the resume check. No second version
            # scheme is invented for it.
            raise RecordVersionMismatch(
                f"run record schema_version {version!r} is not this build's {SCHEMA_ID!r}"
            )
        missing = [
            name
            for name in (
                "thread_id",
                "cwd",
                "artifact_class",
                "contract",
                "packet_digest",
                "recovery_state",
                "rubric_version",
                "started_at",
                "attempt",
            )
            if name not in raw
        ]
        if missing:
            raise SupervisorError(f"run record is missing required fields: {missing}")
        return cls(
            thread_id=raw["thread_id"],
            turn_id=raw.get("turn_id"),
            cwd=raw["cwd"],
            artifact_class=raw["artifact_class"],
            contract=raw["contract"],
            model=dict(raw.get("model") or {}),
            transport=dict(raw.get("transport") or {}),
            packet_digest=raw["packet_digest"],
            schema_version=raw["schema_version"],
            accumulated_output=raw.get("accumulated_output") or "",
            recovery_state=raw["recovery_state"],
            liveness_ledger=dict(raw.get("liveness_ledger") or {}),
            rubric_version=dict(raw["rubric_version"]),
            started_at=raw["started_at"],
            attempt=int(raw["attempt"]),
            ended_at=raw.get("ended_at"),
            # NOT in the required-field list above, and deliberately: a record from before this
            # field existed must reach `_refuse_incompatible_resume` and be REFUSED there, with
            # the reason a caller can act on, rather than failing here as a malformed file.
            session_settings_digest=raw.get("session_settings_digest"),
            # Absent for the same reason and answered the same way: refused at the resume check,
            # not treated as a malformed file here.
            output_schema_digest=raw.get("output_schema_digest"),
            unparseable_frames=[dict(entry) for entry in (raw.get("unparseable_frames") or [])],
            server_requests=[dict(entry) for entry in (raw.get("server_requests") or [])],
            late_responses=[dict(entry) for entry in (raw.get("late_responses") or [])],
            ignored_notification_counts=dict(raw.get("ignored_notification_counts") or {}),
            # ABSENT AND EMPTY ARE READ DIFFERENTLY HERE, which is the whole reason these two do
            # not use the `or []` / `or 0` shape the four above use. `raw.get(...) or []` maps a
            # legacy record's MISSING key and a real run's EMPTY list onto the same value, and
            # that collapse is the defect. A missing key stays None — reported as unknown — and a
            # recorded `[]` / `0` stays the honest "there were none".
            foreign_frames=(
                None
                if raw.get("foreign_frames") is None
                else [dict(entry) for entry in raw["foreign_frames"]]
            ),
            unattributed_frame_count=(
                None
                if raw.get("unattributed_frame_count") is None
                else int(raw["unattributed_frame_count"])
            ),
        )


# §5.1 — THE RECORD'S FILENAME IS BUILT FROM AN ID THE SERVER CHOSE, AND A FILENAME IS A PATH.
# `_open_and_stream` reads the thread id off the `ThreadSession` the call RETURNED, so it is a
# value this module does not author. `..\..\OUT` resolves OUTSIDE `record_dir`, and normalization
# eats the `run-` prefix on the way, so a reader globbing `run-*.json` finds nothing and the only
# durable trace of the attempt sits somewhere nobody looks.
#
# REJECTED, NOT SANITIZED, AND THE CHOICE IS THE CONTRACT. A sanitized id no longer names the
# thread it came from, and two ids differing only where the sanitizer bites collapse onto ONE
# filename — the later run then overwrites the earlier run's record, which is the silent loss of
# the only copy this tool exists to prevent. A refusal is loud and lands where the id arrives.
_THREAD_ID_PATH_COMPONENT = re.compile(r"\A[A-Za-z0-9._-]+\Z")


def record_filename(thread_id: str, attempt: int) -> str:
    """One record per RUN (§7.9), and `(thread_id, attempt)` is the run's identity — §5.6's
    `attempt` field is what bounds a logical review, so no extra linking field is needed and
    none is added (§5.1's table is exhaustive).

    RAISES on an id that is not a single safe path component. This is the one place a
    server-chosen id becomes a path, so it is the one place the check belongs."""
    if (
        not isinstance(thread_id, str)
        or _THREAD_ID_PATH_COMPONENT.match(thread_id) is None
        or not thread_id.strip(".")
    ):
        raise SupervisorError(
            "the server named a thread that is not a usable path component, so its record "
            "would not be written where §5's recovery path looks for it: "
            f"{thread_id!r}"
        )
    return f"run-{thread_id}-attempt{attempt}.json"


def load_run_record(path: Path) -> RunRecord:
    """Read a record a prior run left behind. §5's recovery path is the reader."""
    with open(path, "r", encoding="utf-8", newline="") as handle:
        return RunRecord.from_dict(json.load(handle))


# --------------------------------------------------------------------------------------
# WHAT STATE A RUN IS IN.  The diagnosis lives here because the record does — the
# collect-state rule §4(b).
#
# Result storage can report that no answer is published, not whether the launch
# has died. Diagnose only the recorded state. Return immediately, without probing
# a potentially stale PID or interpreting quiet intervals as proof of liveness.
# --------------------------------------------------------------------------------------

# A record exists and carries no terminal update. §7.9 row 4 is the whole of what may be said
# about it: no terminal update was written. It is NOT a claim that the run is alive — nothing
# here watched a process — and the word this state may never reach for is "died".
RUN_STATE_IN_FLIGHT = "in-flight"
# The record carries its own terminal update, so the run wrote its ending. Beside `result`'s
# fact that no answer is published, this is the genuine failure, and the rule §4(c) lets it be
# said with confidence for one reason: the record says it.
RUN_STATE_ENDED_UNPUBLISHED = "ended-without-answer"
# Nothing was there to read. Never launched, launched and lost, and not our directory are
# indistinguishable from here, and picking one of them is the defect being repaired.
RUN_STATE_NO_RECORD = "no-record"
# A record is present and could not be read. THE RULE'S §4(c) TABLE HAS NO ROW FOR THIS: the
# fourth state is an amendment to it, made in the open (interface pin §3) rather than taken
# quietly. It is not `no-record` — a file is there, and saying otherwise is a false statement
# about the directory. It is not `in-flight` — that asserts liveness nothing established. It
# is not `ended-without-answer` — that asserts an ending nothing established, which is the
# entire defect being repaired. It says strictly less than the other three, which is all that
# is honest when the evidence itself could not be read.
RUN_STATE_RECORD_UNREADABLE = "record-unreadable"

# THE PATTERN THAT FINDS A RECORD IS DERIVED FROM THE FUNCTION THAT NAMES ONE, never written
# out a second time. A literal `run-*.json` here would be a second copy of the record's name,
# free to drift the moment the name changes, with nothing that would notice — the shape this
# file was repaired for once already, one floor down. The stencils are values
# `record_filename` accepts and that its own literal text cannot contain, so replacing each
# with a wildcard leaves the rest of the name exactly as that function built it.
_GLOB_THREAD_STENCIL = "THREADSTENCIL"
_GLOB_ATTEMPT_STENCIL = 987654321


def _record_glob() -> str:
    return (
        record_filename(_GLOB_THREAD_STENCIL, _GLOB_ATTEMPT_STENCIL)
        .replace(_GLOB_THREAD_STENCIL, "*")
        .replace(str(_GLOB_ATTEMPT_STENCIL), "*")
    )


@dataclass(frozen=True)
class RunState:
    """What may be said about one run, from that run's own record and from nothing else.

    EVERY VALUE EXCEPT `state` AND `detail` IS THE RECORD'S OWN, CARRIED VERBATIM. Nothing
    here is computed, nothing is differenced against a clock, and nothing is inferred from
    the filesystem. A value the record does not carry is None, which is not the same as a
    zero: `quiet_seconds=None` says the record holds no usable reading, where `0.0` would say
    a frame had just arrived.
    """

    state: str
    attempt: int | None
    started_at: str | None
    ended_at: str | None
    quiet_seconds: float | None
    record_name: str | None
    # NOT ONE OF THE PINNED SIX, AND PLACED AFTER THEM so their pinned order is untouched.
    # `state` is what a caller BRANCHES on; this is the one sentence an operator READS, and it
    # states only what the record says. It claims no cause the record does not carry.
    detail: str
    # THE ABSOLUTE READING, PLACED LAST FOR THE SAME REASON `detail` IS -- the six
    # fields above carry a pinned order and a new one does not go among them. It sits
    # apart from `quiet_seconds` in the layout and beside it in meaning: that figure
    # is RELATIVE and frozen when the record was last written, so it cannot age, and
    # this one can. It defaults because every arm that answers without a record
    # reaches this constructor, and there None is the only honest value.
    last_frame_at: str | None = None


def _last_frame_at(ledger: Mapping[str, Any]) -> str | None:
    """The run's own record of WHEN its last frame arrived, or None if it has none.

    THE SIBLING OF `_quiet_seconds`, AND THE DIFFERENCE IS THE WHOLE POINT. That one
    returns a RELATIVE duration frozen at the moment the record was last written, so a
    run that stopped being written to reports the same small number forever -- `0.0`
    for a run whose tree was killed, which is the most reassuring value available.
    This one is ABSOLUTE, so it stops being reassuring on its own the moment the
    caller reads its own clock.

    IT NAMES NO DURATION AND DECIDES NONE (C-34), exactly as its sibling does not. It
    copies a string the run itself wrote out of the record it wrote it into. The
    comparison is the caller's and is made nowhere in this engine.

    A LEDGER THAT IS ABSENT OR MALFORMED IS NOT AN ERROR, for the reason stated on
    `_quiet_seconds`: this is reporting, and nothing above it depends on the answer.
    Only a non-empty string is accepted -- an empty one would reach a consumer looking
    like a timestamp it could parse.
    """
    value = ledger.get("last_frame_at")
    if not isinstance(value, str) or not value.strip():
        return None
    # IT MUST ACTUALLY PARSE, AND THE CHECK BELONGS HERE RATHER THAN IN `envelope`.
    # A non-empty string is not a timestamp: a ledger carrying `not-a-timestamp` would
    # otherwise publish a field that LOOKS comparable and is not, which is worse than
    # publishing nothing -- the entire value of this field is that a caller can compare
    # it against its own clock. THIS MODULE IS THE RIGHT PLACE because it is the same
    # module that WRITES the value: `_utc_now` is the producer, so this is the format's
    # owner checking its own output rather than a SECOND place the format is decided.
    # Parsing in `envelope` would be that second decider, which is what its shape-only
    # comment exists to avoid.
    #
    # A MALFORMED VALUE IS None, NOT AN ERROR, matching `_quiet_seconds` above: this is
    # reporting, nothing above it depends on the answer, and a record that was edited or
    # truncated costs the caller a detail rather than an answer.
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    # Date-only and naive datetime strings can parse but carry no timezone offset.
    # Such edited/incomplete records cannot supply the promised absolute timestamp.
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        return None
    return value


def _quiet_seconds(ledger: Mapping[str, Any]) -> float | None:
    """The run's own last reading of how long it had been quiet, or None if it has none.

    A LEDGER THAT IS ABSENT OR MALFORMED IS NOT AN ERROR. This figure is reporting — §6.2
    Tier B, report-only — and no answer above it depends on it, so a missing one costs the
    caller a detail rather than an answer. `LivenessSnapshot.to_dict` writes a float into this
    slot; anything else in it came from a file that was edited or truncated, and None is the
    honest reading of that. A zero would say the opposite of what is known.

    `bool` is excluded although Python counts it an `int`: `True` in this slot is a malformed
    record, and reporting it as one second of quiet would be inventing a measurement.

    IT NAMES NO DURATION AND DECIDES NONE (C-34). It copies a number the run itself wrote out
    of the record it wrote it into. Surfacing that number is the point — the rule §5 observes
    that it is on every record and reaches nobody.
    """
    value = ledger.get("current_quiet_seconds")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def describe_run(directory: Path | str) -> RunState:
    """What state the run in `directory` is in, answered from that run's own record.

    A CALLER HANDS THE DIRECTORY AND NEVER A FILENAME. `record_filename` is this module's
    naming authority and stays it, and `_record_glob` derives the search from that one
    function — a reader does not need to know the name in order to ask about the run.

    THE HIGHEST `attempt` IS THE RUN BEING ASKED ABOUT. §5.6 bounds a review at two runs and
    each writes its own record, so a directory can hold several; the later one is what a
    caller asking "what is happening here" means. The number comes off the record's own
    `attempt` field rather than out of the filename, so nothing here parses a name that is
    built somewhere else.

    NOTHING RAISES. A missing directory, an absent record, an unreadable one and a malformed
    one are four ANSWERS rather than four exceptions. This function exists to replace a raised
    guess with a reported fact, and one that threw would hand the guess back one layer up.

    A FILE THAT COULD NOT BE READ CARRIES NO ATTEMPT NUMBER, so it cannot be shown to be the
    run being asked about, and a record that CAN be read answers in its place. That leaves the
    unreadable sibling unnamed in the answer; `result`'s own neutral directory listing is what
    shows it to an operator, and the limit is stated here rather than papered over.

    NOTHING WAITS AND NOTHING IS PROBED (rule §4(d), §4(e)). There is no pid lookup, no
    process enumeration and no stat standing in for one, and the seconds reported are the
    run's own written reading rather than a difference taken now.
    """
    directory = Path(directory)
    try:
        candidates = sorted(directory.glob(_record_glob()))
    except OSError as error:
        # A LISTING THAT FAILED IS NOT AN EMPTY DIRECTORY, and the two answers are opposites.
        # `result._other_files` states the same rule for the same reason. It is answered with
        # the state that asserts least: nothing established that a record is present, so
        # `record_name` stays None and the sentence says what actually happened. Neither the
        # rule's §4(c) table nor the pin's §3 — which is about a file that WAS found — covers
        # this case, and it is reported rather than folded into one of them quietly.
        return RunState(
            state=RUN_STATE_RECORD_UNREADABLE,
            attempt=None,
            started_at=None,
            ended_at=None,
            quiet_seconds=None,
            record_name=None,
            detail=(
                f"the run directory {directory} could not be listed, so no run record "
                f"could be read: {_describe_error(error)}"
            ),
        )

    latest: RunRecord | None = None
    latest_path: Path | None = None
    unreadable: tuple[Path, BaseException] | None = None
    for path in candidates:
        try:
            record = load_run_record(path)
        except Exception as error:  # noqa: BLE001 - unreadable is an answer here
            if unreadable is None:
                unreadable = (path, error)
            continue
        if latest is None or record.attempt > latest.attempt:
            latest = record
            latest_path = path

    if latest is None or latest_path is None:
        if unreadable is None:
            return RunState(
                state=RUN_STATE_NO_RECORD,
                attempt=None,
                started_at=None,
                ended_at=None,
                quiet_seconds=None,
                record_name=None,
                detail=f"no run record is present in {directory}",
            )
        path, error = unreadable
        return RunState(
            state=RUN_STATE_RECORD_UNREADABLE,
            attempt=None,
            started_at=None,
            ended_at=None,
            quiet_seconds=None,
            # THE FILE THE ANSWER IS ABOUT, which is what an operator needs in order to go and
            # look at it. Nothing was read OUT of it, and the sentence beside it says so.
            record_name=path.name,
            detail=(
                f"a run record is present at {path.name} and could not be read: "
                f"{_describe_error(error)}"
            ),
        )

    quiet = _quiet_seconds(latest.liveness_ledger)
    last_frame = _last_frame_at(latest.liveness_ledger)
    try:
        recovery = RecoveryState(latest.recovery_state)
    except ValueError:
        # THE ONE FIELD THAT DECIDES IS THE ONE FIELD THAT COULD NOT BE READ. The rest of the
        # record parsed, so what it carries is reported rather than discarded — it costs
        # nothing and it is what an operator has to go on — but the state may not be guessed
        # from it, and every guess available here is one of the three claims the fourth state
        # exists in order not to make.
        return RunState(
            state=RUN_STATE_RECORD_UNREADABLE,
            attempt=latest.attempt,
            started_at=latest.started_at,
            ended_at=latest.ended_at,
            quiet_seconds=quiet,
            last_frame_at=last_frame,
            record_name=latest_path.name,
            detail=(
                f"the run record {latest_path.name} records attempt {latest.attempt} in a "
                f"recovery state this build does not know: {latest.recovery_state!r}"
            ),
        )

    # WHICH OF THE FIVE MEAN THE RUN WROTE ITS ENDING, decided from what each value MEANS on a
    # record rather than from how it reads as a word. `running` and `resumed` are the two
    # values `_open_and_stream` writes as a record's INITIAL state, before `turn/start`
    # dispatches anything, and neither is ever written as a terminal update. `complete`,
    # `abandoned` and `interrupted` are the three `_finish` writes at the attempt's single
    # exit — in the one terminal write, beside `ended_at`, under the seal — so each of them is
    # this run recording that it ended. `interrupted` counts as an ending although §5's
    # recovery path will resume such a record: a resume is a LATER run with a record of its
    # own, and the run THIS record describes is over.
    #
    # §7.9 row 4 IS THE LIMIT ON THE OTHER SIDE. A record with no terminal update means
    # exactly that no terminal update was written. In-flight is therefore reported as
    # in-flight, and never as alive, because nothing here watched anything.
    ended = recovery in (
        RecoveryState.COMPLETE,
        RecoveryState.ABANDONED,
        RecoveryState.INTERRUPTED,
    )
    ending = "no end time" if latest.ended_at is None else f"an end time of {latest.ended_at}"
    liveness = (
        "no usable reading of how long it had been quiet"
        if quiet is None
        else f"{quiet} seconds since its last frame"
    )
    return RunState(
        state=RUN_STATE_ENDED_UNPUBLISHED if ended else RUN_STATE_IN_FLIGHT,
        attempt=latest.attempt,
        started_at=latest.started_at,
        ended_at=latest.ended_at,
        quiet_seconds=quiet,
        last_frame_at=last_frame,
        record_name=latest_path.name,
        # ONE SENTENCE, AND EVERY CLAUSE IN IT IS THE RECORD'S OWN WORD OR ITS OWN NUMBER. The
        # recovery state is quoted rather than translated, the end time is the value on the
        # file, and the seconds are the ledger's. It claims no cause. The two states also
        # differ in their TEXT and not only in `state`, because the clause their records differ
        # on — whether an ending was written — is in the sentence.
        detail=(
            f"the run record {latest_path.name} records attempt {latest.attempt} in "
            f"recovery state {latest.recovery_state!r}, with {ending} and {liveness}"
        ),
    )


class RunRecordStore:
    """Writes one record durably. "Durable" means it survives OUR death, not just the
    supervisor's decision to kill (§5.1) — so every write is flushed and fsynced, and every
    update replaces the file atomically so a reader never sees a half-written record.

    R20 transplants here: "could not read" must stay distinguishable from "genuinely empty",
    and the uncertain case RETAINS. This store never deletes a record.
    """

    def __init__(self, directory: Path, thread_id: str, attempt: int) -> None:
        self._path = Path(directory) / record_filename(thread_id, attempt)
        self._lock = threading.Lock()
        # THE SEAL LIVES HERE, on the store, and that placement IS the repair. It was an Event on
        # the stream state, checked by `_checkpoint` and set by the exit — a check and an act with
        # an entire file write in between.
        self._sealed = False
        # WHETHER A DURABLE RECORD EXISTS AT THIS PATH — the store's own answer, and the ONLY
        # authority any reporting site is to use for it. It goes False→True once and never back;
        # a plain bool read needs no lock for that, and taking one here would put a wait behind a
        # worker still inside a write, on the reporting path.
        self._durable = False

    @property
    def path(self) -> Path:
        return self._path

    @property
    def wrote_durably(self) -> bool:
        """True once a write has actually landed at `path`.

        `path` NAMES WHERE A RECORD WOULD GO, NOT THAT ONE IS THERE. Every write can fail — a
        full disk, a sharing violation on `os.replace`, a directory that vanished — and if the
        FIRST one did, `path` names a file nothing ever created. A caller handed that path as a
        retained artifact (R15) is being told this run produced something it did not, so every
        site that reports a record path asks this first.
        """
        return self._durable

    def write(
        self,
        record: RunRecord,
        *,
        updates: Mapping[str, Any] | None = None,
        terminal: bool = False,
        best_effort: bool = False,
    ) -> bool:
        """Write the record atomically. Returns False if the store was sealed and nothing was written.

        EVERYTHING HAPPENS UNDER THE ONE LOCK — staging `updates`, serializing, writing, replacing,
        committing and sealing. Serializing OUTSIDE it was the defect, and it was not theoretical: a
        checkpoint could freeze a payload reading `recovery_state: "running"` / `ended_at: null`,
        lose the lock race to the terminal update, and then land SECOND through `os.replace` —
        ERASING the terminal write from the durable record while the returned outcome said
        `abandoned`. Measured on this box: on-disk and returned state disagreed, and §7.9 row 4
        forbids any later reader from reclassifying the file, so the disagreement is permanent.

        `updates` ARE STAGED ONTO A COPY AND COMMITTED TO THE SHARED RECORD ONLY AFTER THE REPLACE
        SUCCEEDS, and that ordering is the second half of the same repair. They used to be applied
        to the shared record FIRST, so a write that then failed left the in-memory record ahead of
        the file: `recovery_state` read `abandoned` in the object while the file still read
        `running`, and `_drive`'s failure path — which reports "the record's own last value rather
        than a fresh classification, because the terminal update is exactly what may not have
        landed" — reported the terminal value it had just failed to persist. Staging first makes
        that comment true instead of aspirational. `dataclasses.replace` is what stages it, and it
        refuses an unknown field name where a bare `setattr` would have quietly invented one.

        `updates` also exists so the caller does not mutate the record outside the lock. Both
        writers touch `accumulated_output` and `liveness_ledger`; handing the fields in means the
        staging, the serialization, the replace and the commit are one critical section rather
        than four.

        `terminal=True` seals as part of that same section, which is what makes the terminal update
        provably last: any checkpoint after it is refused while holding the lock, and any checkpoint
        before it is simply superseded.
        `best_effort=True` takes the lock WITHOUT BLOCKING and returns False if another writer
        holds it. Checkpoints use it; the terminal update never does. A checkpoint is a periodic
        snapshot and skipping one costs nothing, but a checkpoint QUEUED behind another writer is
        a checkpoint the terminal update then has to wait for — and `_finish` runs after the only
        mechanism allowed to stop a run, so anything it waits on unboundedly turns the advertised
        bound back into a hang. Non-blocking acquisition names no duration, so it is not a second
        timer; it simply refuses to form the queue.
        """
        if not self._lock.acquire(blocking=not best_effort):
            return False
        try:
            if self._sealed:
                return False
            staged = replace(record, **dict(updates)) if updates else record
            payload = json.dumps(staged.to_dict(), indent=2, ensure_ascii=False)
            temporary = self._path.with_suffix(".json.tmp")
            with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._path)
            _fsync_directory(self._path.parent)
            # THE COMMIT, AND IT IS PAST EVERY STEP THAT CAN RAISE. Anything above this line
            # leaves the shared record exactly as the caller handed it in, so the object and the
            # file agree on failure as well as on success.
            if updates:
                for field_name, value in updates.items():
                    setattr(record, field_name, value)
            self._durable = True
            if terminal:
                self._sealed = True
            return True
        finally:
            self._lock.release()


def _fsync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        # Windows cannot open a directory for fsync. The file's own fsync above is what is
        # available there; the rename is still atomic.
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _describe_error(error: BaseException | None) -> str:
    """One rendering of a carried exception, used by every site that reports one.

    `None` is spelled out rather than rendered as an empty string: a blank where a cause
    belongs reads as "no cause was looked for" instead of "there was none".
    """
    if error is None:
        return "no exception was recorded"
    return f"{type(error).__name__}: {error}"


def _canonical(value: Any, where: str = "session_settings") -> Any:
    """The settings bundle rendered into a form whose JSON text depends on its CONTENT alone.

    Mappings become plain dicts with string keys, so `json.dumps(..., sort_keys=True)` can order
    them: two bundles that differ only in the order their keys were inserted must digest the
    same, or every resume refuses itself. Sequences keep their order, because there the order IS
    content.

    A VALUE THIS CANNOT RENDER STABLY IS REFUSED, NOT RENDERED ANYWAY. The fallback used to be
    the value's type and its repr, and such a rendering is only as stable as that repr: a set
    renders in an iteration order that is randomised per process for string members, and an
    object with no `__repr__` of its own renders its address. Driven under differing hash seeds,
    a set and a plain object each digested differently on every seed, while a plain dict digested
    identically on all of them — and this digest exists precisely to be compared ACROSS
    processes, because a resume is by definition a later one. A bundle carrying such a value
    would make every legitimate resume refuse itself, permanently, at a moment far away from the
    code that put it there.

    THE REFUSAL NAMES THE KEY AND THE TYPE AND NEVER THE VALUE. An exception is a channel OUT of
    this module — its text reaches `_SetupAbort.detail` and then `ToolingFailure.detail`, which
    is the caller's — and `_settings_digest`'s rule is that the digest travels and the settings
    do not. `where` exists so the message can be located without carrying anything the bundle
    holds.

    THE REFUSAL IS NOT A WIDENING. Nothing here learns to render more types; it declines to
    guess. It is the same rule `_refuse_incompatible_resume` states about an absent digest: a
    comparison that cannot be shown to mean anything is not quietly performed and reported as
    agreement.
    """
    if isinstance(value, Mapping):
        return {str(key): _canonical(item, f"{where}.{key}") for key, item in value.items()}
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, (list, tuple)):
        return [_canonical(item, f"{where}[{index}]") for index, item in enumerate(value)]
    raise SupervisorError(
        f"the session setting at {where} has type {type(value).__name__!r}, which has no "
        "rendering this module can show is the same in a later process, so it cannot be "
        "digested and a resume against it could never be shown to ask the same review. Its "
        "value is not reported here: the digest travels and the settings do not."
    )


def _settings_digest(settings: Mapping[str, Any]) -> str:
    """A stable digest of the settings bundle a run was opened with.

    THE DIGEST AND NOT THE SETTINGS. §7.5c gives `transport` the one enforced path and this
    module re-hands the identical object without inspecting it; storing a copy here would be a
    second version of the settings, free to drift and available to re-apply. A digest can be
    compared and cannot be applied.

    A BUNDLE THAT CANNOT BE DIGESTED STABLY IS REFUSED rather than digested anyway — see
    `_canonical` for what that covers and why. Callers can hit it: `_new_record`, where the
    record is written, and `_refuse_incompatible_resume`, which turns it into the
    `ResumeRefused` its callers are built to catch.
    """
    text = json.dumps(
        _canonical(settings), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _output_schema_digest(schema: Mapping[str, Any] | None) -> str:
    """The RESPONSE CONSTRAINT a run was opened under, in a form a later process can compare.

    A MODE MARKER AND A DIGEST IN ONE VALUE, because a resume needs both answers: was there a
    schema at all, and was it this one. `RunPlan.output_schema` carries None as a VALUE — the
    explicitly supported unconstrained turn, whose wire key `transport` omits — and a digest
    alone cannot express that, while a mode flag alone cannot tell two schemas apart. The
    marker is a word and a digest is sixty-four hex characters, so neither can be read as the
    other.

    `RunRecord.schema_version` IS NOT THIS, and mistaking it for this is the defect. That field
    is `envelope`'s SCHEMA_ID — the version of the record FORMAT — and it is written identically
    whether the turn was constrained by a schema, by a different schema, or by nothing at all,
    so a resume compared against it compares nothing about the review.
    """
    if schema is None:
        return "unconstrained"
    text = json.dumps(
        _canonical(schema, "output_schema"),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------------------
# The kill.  ONE call site: the timeout expiring.  §6.2 Tier A / C-34.
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class KillResult:
    still_alive: bool
    detail: str


def kill_process_tree(pid: int) -> KillResult:
    """Kill the process tree rooted at `pid`. Called from exactly one place — the expiry of
    THE ONE TIMEOUT. A tree kill, not a process kill: the child spawns its own children, and
    C-34's rule is that the timeout "kills the process tree when it expires".
    """
    if sys.platform == "win32":
        executable = _resolve_taskkill()
        completed = subprocess.run(
            [executable, "/T", "/F", "/PID", str(pid)],
            capture_output=True,
            text=True,
            check=False,
        )
        detail = f"taskkill rc={completed.returncode} {completed.stdout.strip()} {completed.stderr.strip()}"
    else:
        # VERIFY CONTAINMENT BEFORE KILLING A GROUP. `transport` spawns the child with
        # `start_new_session=True`, so the child leads its own group and killing that group
        # cannot reach us. This RE-READS that at the moment of the kill instead of trusting
        # it, because if it is ever untrue the failure is silent and total: `killpg` SUCCEEDS,
        # takes this process, its shell and its siblings down uncatchably, and the `except
        # OSError` below never runs — a fallback cannot rescue a call that did not fail.
        # The guard fires only when the spawn site has changed underneath this one, which is
        # the only condition under which it is needed and the reason it is checkable at all.
        try:
            group = os.getpgid(pid)
        except ProcessLookupError:
            return KillResult(still_alive=False, detail="process already gone")
        if group == os.getpgid(0):
            os.kill(pid, signal.SIGKILL)
            detail = (
                f"CONTAINMENT LOST: pgid {group} is this process's own group, so killpg would "
                "kill the supervisor and its caller; killed the child pid alone instead. The "
                "app-server was not spawned into its own session."
            )
        else:
            try:
                os.killpg(group, signal.SIGKILL)
                detail = f"killpg SIGKILL pgid={group}"
            except ProcessLookupError:
                detail = "process already gone"
            except OSError as error:
                os.kill(pid, signal.SIGKILL)
                detail = f"killpg failed ({error}); fell back to kill"
    return KillResult(still_alive=_process_alive(pid), detail=detail)


def _resolve_taskkill() -> str:
    """`CreateProcess` searches System32 first, so a bare name would be resolved there anyway
    — but this module states its interpreter paths rather than inheriting them."""
    system_root = os.environ.get("SystemRoot")
    if system_root:
        candidate = Path(system_root) / "System32" / "taskkill.exe"
        if candidate.is_file():
            return str(candidate)
    found = shutil.which("taskkill")
    if found:
        return found
    raise SupervisorError("taskkill could not be resolved; the one timeout has no way to kill")


def _process_alive(pid: int) -> bool:
    if sys.platform == "win32":
        executable = _resolve_taskkill()
        query = Path(executable).with_name("tasklist.exe")
        completed = subprocess.run(
            [str(query), "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        )
        return str(pid) in completed.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# --------------------------------------------------------------------------------------
# Outcomes.
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ToolingFailure:
    """C-4: retry once, then a STRUCTURED tooling failure. This module reports one; it does
    not author an envelope — that is `envelope`'s, and under §7.4 step 1 a tooling failure
    means no review happened and is therefore never readable as a pass."""

    reason: str
    detail: str
    attempts: int


@dataclass(frozen=True)
class AttemptOutcome:
    """One attempt's report to the caller. NOT §5.1's record — that is `RunRecord`, whose
    field table is exhaustive and closed. This struct is this module's own reporting shape."""

    attempt: int
    complete: bool
    stop_reason: StopReason
    turn_status: str | None
    thread_id: str
    turn_id: str | None
    accumulated_output: str
    liveness: LivenessSnapshot
    # WHERE THIS ATTEMPT'S DURABLE RECORD ACTUALLY IS, or None when no write ever landed.
    #
    # It used to be a plain `Path`, filled from `RunRecordStore.path` at every reporting site
    # merely because a store existed. A store exists the instant a thread id is known, and its
    # `path` is where a record WOULD go — so an attempt whose every write failed reported a path
    # naming a file nothing had created, and `RunOutcome.record_paths` then promised the caller a
    # retained artifact that was not there (R15 broken by the struct that makes the promise).
    # None says so, and `record_paths` drops it rather than naming it. The rest of the row still
    # stands: the attempt number, the thread, the turn and everything the stream saw are reported
    # exactly as they were, because losing those was the separate defect `_drive` already fixed.
    record_path: Path | None
    recovery_state: RecoveryState
    rejoined_in_flight_turn: bool
    retrying_error_count: int
    terminal_error: Mapping[str, Any] | None
    kill: KillResult | None
    reader_error: str | None
    # §6.4 keeps EVIDENCE OF DEATH separate from ABSENCE OF LIFE, and `transport` supplies
    # the evidence: `frames()` raises `ServerDied` on an EOF the client did not ask for.
    # Folding that into `reader_error` would file a dead child under "our reader broke".
    server_died: str | None = None
    # The attempt raised OUTSIDE the frame reader. Its own field for the same reason
    # `server_died` has one: a `FenceViolation` on `thread/start` is not a reader fault, and
    # the one place a reader records itself is the one place it must not appear. Two failures
    # reach it — one before the stream (the spawn, the handshake, the opening call, a durable
    # write, `turn/start`) and one after it (the teardown in `_finish` raising, which `_drive`
    # reports on the attempt it belongs to rather than as a `_SetupAbort` that would lose the
    # thread, the turn and the record path). The text says which, and `stop_reason` separates
    # them: `setup-failed` against `finish-failed`.
    setup_error: str | None = None
    # Frames that arrived on this attempt's stream naming a DIFFERENT thread or turn. They were
    # not appended to the answer and did not end the turn; they are carried here rather than
    # dropped, because a frame we declined to act on is still something the caller may need to
    # know arrived.
    #
    # `None` MEANS UNKNOWN AND THE DEFAULT IS UNKNOWN, which is the repair rather than a style
    # choice. This defaulted to `()` — "this attempt saw none" — and `_attempt_from_record` built
    # a resumed row without passing it, so a prior attempt that HAD seen foreign frames was
    # reported as having seen none the moment its review was resumed. The honest default for a
    # value nobody supplied is "not known", so any future construction site that forgets it says
    # that instead of asserting a clean run.
    foreign_frames: tuple[Mapping[str, Any], ...] | None = None
    # Answer or terminal frames acted on while carrying no ids to check. A non-zero value says
    # how much of this answer rests on frames that could not be attributed either way.
    #
    # A ZERO DOES NOT MEAN EVERY SUCH FRAME WAS CHECKED, and the sentence that claimed so was
    # driven false. This counts the frames that ARRIVED without ids; a comparison is also
    # skipped when this attempt does not know its OWN ids, and frames passed on that ground are
    # not counted here.
    #
    # `None` is UNKNOWN, for `foreign_frames`' reason and with the same history: a zero here used
    # to be the answer given for a resumed attempt whose count was never persisted, and "no answer
    # bytes were accepted unchecked" is precisely the claim that must not be invented.
    unattributed_frame_count: int | None = None
    # --- the four dispositions (frame-disposition rule §3), reported to the caller ---
    #
    # These are what the reader did with the frames it did not act on. Every one of them was
    # silently dropped before: the rule's §1 measured a reviewer that answered `fail` with a
    # critical finding delivered as `approve` with none, because the frames carrying the
    # difference reached the bottom of the reader and stopped existing.
    #
    # THE ANSWER IS NOT AFFECTED BY ANY OF THEM. §4: the reviewer's bytes are delivered whole
    # and the loss is reported BESIDE them, never by trimming, replacing or withholding them —
    # reporting a problem by deleting content is the shape of the defect this fix ends.
    #
    # Uninterpretable frames remain evidence. Delivery decides their blocking
    # consequence; this module must neither infer their meaning nor discard them.
    unparseable_frames: tuple[Mapping[str, Any], ...] = ()
    # Questions the server asked US (approvals, elicitations). Nothing answers one — that is
    # `transport.FrameKind.SERVER_REQUEST`'s own documented decision and it stands — but the
    # module's comment already promises the frame is "surfaced rather than swallowed, because
    # an unanswered request the caller cannot see is a hang with no explanation." This field is
    # what makes that promise true. Non-blocking: the worst case is a stall reported incomplete.
    server_requests: tuple[Mapping[str, Any], ...] = ()
    # Replies to requests of ours that arrived after `_await` stopped waiting for them. They
    # carry no answer text, so one cannot cost a finding — but an unmatched reply means our
    # bookkeeping and the server's diverged, which is the same silence in a quieter key.
    late_responses: tuple[Mapping[str, Any], ...] = ()
    # Ordinary announcements the reader deliberately ignores, COUNTED PER NAME AND NEVER AS ONE
    # INTEGER. A single total would distinguish nothing and that is measured rather than
    # assumed: the real server sends about two hundred ignored announcements on every healthy
    # run from a stable set of names, so a total is non-zero on healthy and broken runs alike.
    # A per-name tally is also the instrument §3a requires for the gap it declines to guard —
    # if the answer ever moves onto a new name, an unseen name appears here.
    ignored_notification_counts: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class _SetupAbort:
    """An attempt that produced no `AttemptOutcome` at all, because no thread id was ever
    returned and `AttemptOutcome.thread_id` is not nullable.

    It is a separate shape rather than a half-filled outcome: an attempt that never learned an
    identity has no attempt row to report — no thread, no turn, no record file and no stream to
    describe. `record_path` being nullable does NOT make this shape redundant; that nullability
    answers a different case, an attempt that HAD an identity and whose writes all failed, and
    demoting one of those to an abort would lose the attempt number, the thread and the turn it
    really had.
    """

    reason: StopReason
    detail: str
    # WHATEVER THE MODEL HAD ALREADY SAID. An abort is not a reason to lose the answer: a
    # filesystem error on the terminal write can land AFTER a complete response, and returning
    # nothing then would erase a review the reviewer actually produced — §7.3's founding rule
    # broken by the error path rather than by the happy one. `_assemble` retains this as a
    # partial (§5.4a: never discarded, never silently merged).
    accumulated_output: str = ""


@dataclass(frozen=True)
class RunOutcome:
    """§5.4a — partial and complete answers are NEVER silently merged.

    `final_output` is the answer from the attempt that completed, or None. `partial_outputs`
    holds every non-completing attempt's accumulated deltas, in attempt order, retained
    beside it. Combining them is the delivery layer's decision (§7.8), not this module's.
    """

    complete: bool
    final_output: str | None
    partial_outputs: tuple[str, ...]
    attempts: tuple[AttemptOutcome, ...]
    tooling_failure: ToolingFailure | None

    @property
    def record_paths(self) -> tuple[Path, ...]:
        """R15 — never delete an artifact the emitted envelope points at; name every retained
        path. These are the paths this run promised.

        AN ATTEMPT THAT NEVER LEARNED A THREAD IDENTITY HAS NO ROW HERE, because it wrote no
        record and promised no path — `_finish` gives the reason. So this is not a count of
        attempts, and reading it as one under-reports a run whose attempts aborted in setup.

        NEITHER DOES AN ATTEMPT WHOSE EVERY WRITE FAILED, and that is the same rule reaching one
        case further. Such an attempt HAS an identity and keeps its row in `attempts`; what it
        does not have is a file. Naming its would-be path here would promise an artifact nobody
        can open — which is this property asserting something it does not know, the mirror of the
        deletion R15 forbids. `AttemptOutcome.record_path` is None in exactly that case."""
        return tuple(
            outcome.record_path for outcome in self.attempts if outcome.record_path is not None
        )


def _attempt_from_record(record: RunRecord, record_path: Path) -> AttemptOutcome:
    """A PRIOR attempt of this review, reported from the durable record it left behind.

    EVERYTHING ON THAT RECORD EXCEPT THE ANSWER TEXT IS EVIDENCE ABOUT THE REVIEW, NOT ABOUT THE
    ATTEMPT. An unreadable line the first attempt kept is a line this review lost, and a second
    attempt that reads cleanly does not un-lose it. A resume used to carry the text forward and
    nothing else, so the delivery layer — which aggregates over `RunOutcome.attempts` — was handed
    a clean resume that looked like the only attempt there had ever been, and blocking evidence
    stopped existing at the moment a review was retried. That is the silence the frame-disposition
    rule exists to end, arriving one layer up.

    IT CARRIES NO TIME. An attempt number, a path and a set of kept frames are reporting. Nothing
    here is read by anything that decides a duration, and every attempt's grant remains its own
    (C-34).

    WHAT THE RECORD DOES NOT HOLD IS NOT INVENTED HERE. §5.1's table carries no stop reason, no
    turn status, no kill result, no reader fault and no retry tally, so those are reported as the
    absences they are rather than guessed from the recovery state. `complete` is False because a
    terminal record is refused before this is ever built, so no record reaching here records an
    attempt that finished.

    THE ATTRIBUTION PAIR IS DIFFERENT NOW, AND THE DIFFERENCE IS THE POINT. `foreign_frames` and
    `unattributed_frame_count` used to be in that list of absences — but they were absent because
    nothing PERSISTED them, not because they are unknowable, and the row was built without passing
    either, so `AttemptOutcome`'s old `()` / `0` defaults answered "this attempt saw none" for an
    attempt that may have seen many. `RunRecord` carries both now, so a record written by this
    build is reported exactly as the live attempt reported it.

    A RECORD FROM BEFORE THOSE FIELDS EXISTED IS REPORTED AS UNKNOWN, NOT AS EMPTY, and it does
    not refuse the resume. `RunRecord`'s own field comment argues that choice at length: an absent
    digest means the resume may be a different review and is refused; an absent attribution field
    means this attempt's REPORT is incomplete, and refusing there would throw away the answer text
    and the unreadable-line evidence the record does hold. So `None` travels through — it is
    distinguishable from zero and empty at every layer above, which is the whole of what was lost.
    """
    try:
        recovery = RecoveryState(record.recovery_state)
    except ValueError as error:
        raise SupervisorError(
            "the prior record's recovery_state is not one this build knows, so the attempt it "
            f"records cannot be reported: {record.recovery_state!r}"
        ) from error
    ledger = record.liveness_ledger
    return AttemptOutcome(
        attempt=record.attempt,
        complete=False,
        stop_reason=StopReason.FROM_RECORD,
        turn_status=None,
        thread_id=record.thread_id,
        turn_id=record.turn_id,
        accumulated_output=record.accumulated_output,
        # THE RECORD'S OWN LAST READING, NOT A FRESH ONE: this process never watched that
        # attempt's stream. A record carrying no ledger reads as an empty one, which is the
        # reading `RunRecord.from_dict` already takes for the dispositions below.
        liveness=LivenessSnapshot(
            frame_count=ledger.get("frame_count") or 0,
            last_frame_at=ledger.get("last_frame_at"),
            longest_quiet_seconds=ledger.get("longest_quiet_seconds") or 0.0,
            current_quiet_seconds=ledger.get("current_quiet_seconds") or 0.0,
        ),
        record_path=record_path,
        recovery_state=recovery,
        rejoined_in_flight_turn=False,
        retrying_error_count=0,
        terminal_error=None,
        kill=None,
        reader_error=None,
        # THE FOUR DISPOSITIONS, WHICH ARE THE REASON THIS ROW EXISTS AT ALL.
        unparseable_frames=tuple(dict(entry) for entry in record.unparseable_frames),
        server_requests=tuple(dict(entry) for entry in record.server_requests),
        late_responses=tuple(dict(entry) for entry in record.late_responses),
        ignored_notification_counts=dict(record.ignored_notification_counts),
        # THE ATTRIBUTION PAIR, CARRIED RATHER THAN DEFAULTED. `None` on the record is the
        # legacy case and stays None here — an explicit unknown, never a zero.
        foreign_frames=(
            None
            if record.foreign_frames is None
            else tuple(dict(entry) for entry in record.foreign_frames)
        ),
        unattributed_frame_count=record.unattributed_frame_count,
    )


# --------------------------------------------------------------------------------------
# The stream state a single attempt accumulates.
# --------------------------------------------------------------------------------------
class _StreamState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.ledger = LivenessLedger()
        self.chunks: list[str] = []
        self.turn_status: str | None = None
        self.terminal_error: dict[str, Any] | None = None
        self.retrying_error_count = 0
        self.reader_error: str | None = None
        self.server_died: str | None = None
        # Frames whose ids named a DIFFERENT thread or turn. Kept whole rather than counted:
        # §7.3's rule is that nothing is discarded, and a frame this attempt refused to act on
        # is still evidence about what the server was doing.
        #
        # THERE IS NO CAP ON THIS, AND THAT IS STATED RATHER THAN FIXED HERE. A stream that
        # names another turn on every frame copies that turn's content into this attempt's
        # outcome. Capping it means discarding, which is the one thing §7.3 forbids this module
        # from doing quietly, and how much of a run's own evidence is retained is the retention
        # question `result` owns (§7.7) — inventing an answer here would be a second retention
        # policy. What made this reachable in bulk was misattributing our OWN frames, and that
        # is what the identity repair in `_open_and_stream` closes.
        self.foreign_frames: list[dict[str, Any]] = []
        # Frames this attempt ACTED ON while carrying no ids to check them against.
        self.unattributed_frame_count = 0
        # The four dispositions of the frame-disposition rule §3. `AttemptOutcome`'s own fields
        # carry the reasons; these are the accumulators behind them. THE COUNTS ARE THESE
        # CONTAINERS' LENGTHS — no counter is kept beside them, because a counter and the thing
        # it counts are two representations of one fact and only one of them can be wrong.
        self.unparseable_frames: list[dict[str, Any]] = []
        self.server_requests: list[dict[str, Any]] = []
        self.late_responses: list[dict[str, Any]] = []
        self.ignored_notification_counts: dict[str, int] = {}

    def output(self) -> str:
        with self.lock:
            return "".join(self.chunks)

    def note_unparseable(self, frame: ServerFrame) -> None:
        """Keep a line whose meaning could not be established. §3: keep, count, tell — never
        abort and never ignore. We cannot read it, so we cannot know whether it carried part of
        the answer; only saying so is honest about not knowing."""
        with self.lock:
            self.unparseable_frames.append(_kept_frame(frame))

    def note_server_request(self, frame: ServerFrame) -> None:
        """Keep a question the server asked us. Not answered, not aborted on."""
        with self.lock:
            self.server_requests.append(_kept_frame(frame))

    def note_late_response(self, frame: ServerFrame) -> None:
        """Keep a reply to one of our requests that arrived after we stopped waiting.

        `RESPONSE` and `ERROR_RESPONSE` share this arm and are NOT collapsed: §6.4's rule is
        that the protocol's own distinctions are reported rather than picked between, and
        `_kept_frame` carries the kind on every entry, so the two remain separable by a reader
        without this module deciding which of them mattered.
        """
        with self.lock:
            self.late_responses.append(_kept_frame(frame))

    def note_ignored_notification(self, frame: ServerFrame) -> None:
        """Tally an ordinary announcement the reader deliberately ignores, BY NAME."""
        name = frame.method if isinstance(frame.method, str) else IGNORED_WITHOUT_METHOD_NAME
        with self.lock:
            self.ignored_notification_counts[name] = (
                self.ignored_notification_counts.get(name, 0) + 1
            )

    def dispositions(self) -> dict[str, Any]:
        """The four dispositions as the durable record and the outcome both take them.

        ONE READER FOR BOTH WRITE SITES. `_checkpoint` and `_finish` write the same four
        fields, and two independent renderings of them is how a checkpoint and a terminal
        update come to disagree about what a run saw.
        """
        with self.lock:
            return {
                "unparseable_frames": [dict(entry) for entry in self.unparseable_frames],
                "server_requests": [dict(entry) for entry in self.server_requests],
                "late_responses": [dict(entry) for entry in self.late_responses],
                "ignored_notification_counts": dict(self.ignored_notification_counts),
            }

    def attribution(self) -> dict[str, Any]:
        """What the reader could not attribute, in the shape the record and the outcome both take.

        THE SAME ONE-READER RULE `dispositions` STATES, extended to the pair that now needs it.
        Both values are durable, so a checkpoint, the terminal write and the returned outcome must
        all be the same reading; and both are appended to by the reader thread, so a report built
        from an unlocked `state.foreign_frames` was reading a list mid-append. One locked snapshot
        answers both.
        """
        with self.lock:
            return {
                "foreign_frames": [dict(entry) for entry in self.foreign_frames],
                "unattributed_frame_count": self.unattributed_frame_count,
            }


def _kept_frame(frame: ServerFrame) -> dict[str, Any]:
    """One retained frame, in the shape the record and the outcome both carry.

    `raw` IS BASE64 AND `text` IS LOSSY, AND BOTH ARE PRESENT FOR DIFFERENT REASONS. §5 asks
    for the kept raw LINES, and an unparseable line is frequently unparseable precisely because
    it is not valid UTF-8 — so `errors="replace"` cannot be the kept bytes, it destroys the
    evidence it is asked to preserve. Base64 is the bytes; it is also unreadable to a human
    doing the forensics this record exists for, which is what `text` is for. Neither substitutes
    for the other, so neither is dropped.
    """
    return {
        "kind": frame.kind.value if isinstance(frame.kind, Enum) else str(frame.kind),
        "method": frame.method,
        "request_id": frame.request_id if isinstance(frame.request_id, (str, int)) else None,
        "raw": base64.b64encode(frame.raw).decode("ascii"),
        "text": frame.raw.decode("utf-8", errors="replace"),
    }


def _disagreeing_ids(
    frame: ServerFrame, expected_thread: str | None, expected_turn: str | None
) -> str | None:
    """Which of a frame's ids name something OTHER than this attempt, or None if none do.

    ABSENT IS NOT DISAGREEING. A `None` on either side is nothing to compare, so it is not
    reported here; what to do with an unchecked frame is the caller's decision and the caller
    counts them. Keeping the two apart is the point — collapsing them would either drop the
    model's answer whenever the wire omits an id, or accept another turn's text as ours.

    `expected_turn` is None only if this attempt does not know its own turn id. There is then no
    turn to judge against, and refusing every frame would discard a whole review over a
    comparison that was never possible.
    """
    disagreements: list[str] = []
    if (
        expected_thread is not None
        and frame.thread_id is not None
        and frame.thread_id != expected_thread
    ):
        disagreements.append(f"threadId {frame.thread_id!r} is not {expected_thread!r}")
    if (
        expected_turn is not None
        and frame.turn_id is not None
        and frame.turn_id != expected_turn
    ):
        disagreements.append(f"turnId {frame.turn_id!r} is not {expected_turn!r}")
    return "; ".join(disagreements) or None


class _AttemptContext:
    """What the attempt worker publishes as it runs, so the caller's thread can act on an
    expiry that lands ANYWHERE in the attempt — including before a record or a child process
    exists.

    THE PID SLOT IS THE POINT. The worker writes the child's pid the instant a session exists
    and BEFORE any wire call is made on it. At expiry a populated slot is a tree to kill; an
    empty slot means the hang is inside session creation, which is then a NAMED OUTCOME rather
    than an orphan nobody reports. The shape this replaces — a timeout that only starts once
    the session is built — is what made every setup call unbounded.

    Nothing here decides a duration and nothing here is shared between attempts: one context
    is built per attempt and discarded with it (C-34).
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        # Set by the worker when the attempt ends, however it ends. This is the event THE ONE
        # TIMEOUT waits on.
        self.done = threading.Event()
        # Set by `_drive` the moment that wait returns, because from then on this attempt has
        # been reported to the caller. A worker still blocked inside `session_factory`,
        # `thread/start` or `thread/resume` has no way to learn that on its own, and one that
        # carried on would open a thread, write a record `_finish` never sealed and DISPATCH A
        # REVIEW behind an attempt already reported stopped — possibly beside its own
        # replacement. IT IS A FLAG, NOT A CLOCK: it decides no duration and names no interval,
        # so it is not the second timer C-34 forbids.
        self.cancelled = threading.Event()
        self.pid: int | None = None
        self.session: TransportSession | None = None
        self.store: RunRecordStore | None = None
        self.record: RunRecord | None = None
        self.state: _StreamState | None = None
        self.error: BaseException | None = None

    def publish_session(self, session: TransportSession) -> None:
        with self.lock:
            self.session = session
            self.pid = session.pid

    def stop_if_cancelled(self, session: TransportSession) -> None:
        """Abandon the attempt if `_drive` has already returned it.

        Read where a blocking call has just returned, which is the only moment a worker can
        discover it was left behind. The session is closed on the way out: `_drive`'s own
        `finally` has already run and will not run again, and `close()` shuts the send side
        only — the same teardown the reader performs — so the child sees EOF and exits rather
        than surviving as an app-server nobody owns.

        This check and the following side effect are not atomic. Cancellation in
        that window can allow one further thread open, record write or dispatch;
        a child that survives termination can outlive the returned attempt. This
        existing lifecycle limit is not a public resumption or approval guarantee.
        """
        if not self.cancelled.is_set():
            return
        try:
            session.close()
        except Exception:  # noqa: BLE001 - best effort; the refusal below is the point
            pass
        raise SupervisorError(
            "this attempt was already reported to the caller, so its worker stops here rather "
            "than opening a thread, writing a record or dispatching a turn behind it"
        )

    def publish_run(
        self, store: RunRecordStore, record: RunRecord, state: _StreamState
    ) -> None:
        with self.lock:
            self.store = store
            self.record = record
            self.state = state

    def snapshot(
        self,
    ) -> tuple[
        TransportSession | None,
        int | None,
        RunRecordStore | None,
        RunRecord | None,
        _StreamState | None,
        BaseException | None,
    ]:
        """Read every slot at once. On the expiry path the worker is still running, so a
        field-by-field read could see a session without the record that was published with
        it."""
        with self.lock:
            return (self.session, self.pid, self.store, self.record, self.state, self.error)


class Supervisor:
    """Drives one logical review: at most two attempts (§5.6), each with the one timeout.

    `record_dir` is REQUIRED and has no default. The durable, machine-wide, env-var-configured
    directory is v2 (C-44), so v1 refuses to guess a location rather than inventing half of a
    deferred design.
    """

    def __init__(self, record_dir: Path | str) -> None:
        if not record_dir:
            raise SupervisorError(
                "record_dir is required: the run record must be durable (§5.1) and v1 has no "
                "configured directory to fall back on (C-44 defers that to v2)"
            )
        self._record_dir = Path(record_dir)
        self._record_dir.mkdir(parents=True, exist_ok=True)
        self._live_state: _StreamState | None = None

    # -- the caller's in-run window on Tier B (§6.2, C-44: v1 surfaces it, v2 stores it) ----
    def liveness(self) -> LivenessSnapshot | None:
        """Read the current attempt's ledger while the run is in flight. Report-only."""
        state = self._live_state
        return None if state is None else state.ledger.snapshot()

    # -- entry points ------------------------------------------------------------------
    def run(
        self,
        plan: RunPlan,
        session_factory: Callable[[], TransportSession],
    ) -> RunOutcome:
        """Attempt 1 opens a new thread. If it does not complete, attempt 2 RESUMES it — and
        §5.6 makes that resume an attempt, so the review is bounded at two runs.

        AN ATTEMPT THAT DIES BEFORE `thread/start` RETURNS HAS NO THREAD TO RESUME, so §5.6's
        second attempt can only be another new thread. C-4's retry is spent either way; what
        changes is which door it goes through, and resuming a thread id that does not exist
        would be the worse answer. The loop is bounded by `MAX_ATTEMPTS` and by nothing else:
        it holds no clock, and carries nothing from one attempt into the next but the thread
        id there is to reopen (C-34).
        """
        outcomes: list[AttemptOutcome] = []
        aborts: list[_SetupAbort] = []
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if outcomes and outcomes[-1].complete:
                break
            if outcomes and outcomes[-1].turn_status == TURN_STATUS_COMPLETED:
                # THE TURN FINISHED; ONLY THE DRAIN WAS CUT SHORT. Retrying would pay for the
                # same review twice and come back with a DIFFERENT one, while the text already
                # in hand is retained as a partial (§5.4a, never discarded, never merged).
                # C-4's retry exists for a reviewer that did not answer, not for one that did.
                break
            result = self._drive(
                plan,
                session_factory,
                attempt=attempt,
                # None means "open a new thread". An attempt that produced an outcome left a
                # thread id behind; one that aborted during setup did not.
                thread_id=outcomes[-1].thread_id if outcomes else None,
                final=(attempt >= MAX_ATTEMPTS),
            )
            if isinstance(result, _SetupAbort):
                aborts.append(result)
            else:
                outcomes.append(result)
        return self._assemble(tuple(outcomes), aborts=tuple(aborts))

    def resume_from_record(
        self,
        record_path: Path,
        plan: RunPlan,
        session_factory: Callable[[], TransportSession],
    ) -> RunOutcome:
        """A LATER session recovering a review that died with its supervisor or its machine.

        §5.5 — never resume "the most recent session": the thread id comes from an explicit
        record handed in by the caller. This module has no code path that discovers a session.
        """
        prior = load_run_record(Path(record_path))
        # A TERMINAL RECORD IS NOT A RECOVERY CANDIDATE. §7.9 row 3 — `complete` and `abandoned`
        # are written by the run that HAD that outcome, at its single exit, and they mean the
        # review is over. Resuming one passed every compatibility check by construction (it is
        # the same review), incremented the attempt, reopened the thread and started a SECOND
        # turn, and the finished answer was then carried forward as a partial — a
        # duplicate review, paid for twice, with the first one's answer relabelled partial.
        # `running`, `resumed` and `interrupted` are the states a resume exists for and they are
        # untouched. Checked before compatibility because no plan makes a finished review
        # resumable.
        if prior.recovery_state in (RecoveryState.COMPLETE.value, RecoveryState.ABANDONED.value):
            raise ResumeRefused(
                f"refusing to resume: the prior record's recovery_state is "
                f"{prior.recovery_state!r}, which is terminal — the review it records is "
                "finished, and resuming it would run a second review and report the first "
                "one's answer as partial output"
            )
        self._refuse_incompatible_resume(prior, plan)
        # THE PRIOR ATTEMPT IS PART OF THIS REVIEW AND IS REPORTED AS ONE, at every exit below.
        # `_attempt_from_record` says what that buys and why the alternative was a silence.
        prior_attempt = _attempt_from_record(prior, Path(record_path))
        attempt = prior.attempt + 1
        if attempt > MAX_ATTEMPTS:
            return RunOutcome(
                complete=False,
                final_output=None,
                partial_outputs=(prior.accumulated_output,) if prior.accumulated_output else (),
                # THE BUDGET IS SPENT, SO THE PRIOR RECORD IS THE ONLY EVIDENCE THIS REVIEW HAS.
                # An empty tuple here dropped all of it — including the unreadable lines a caller
                # blocks on — on the one path where nothing else can ever supply them.
                attempts=(prior_attempt,),
                tooling_failure=ToolingFailure(
                    reason="attempts-exhausted",
                    detail=(
                        f"the prior record is attempt {prior.attempt}; a resume consumes an "
                        "attempt (§5.6) and the budget is spent"
                    ),
                    attempts=prior.attempt,
                ),
            )
        result = self._drive(
            plan,
            session_factory,
            attempt=attempt,
            thread_id=prior.thread_id,
            final=(attempt >= MAX_ATTEMPTS),
        )
        if isinstance(result, _SetupAbort):
            return self._assemble((), prior=prior_attempt, aborts=(result,))
        return self._assemble((result,), prior=prior_attempt)

    # -- one attempt: the worker that runs it, and the one wait that bounds it ----------
    def _open_and_stream(
        self,
        ctx: _AttemptContext,
        plan: RunPlan,
        session_factory: Callable[[], TransportSession],
        attempt: int,
        thread_id: str | None,
    ) -> None:
        """EVERYTHING THE ATTEMPT DOES, ON THE FAR SIDE OF THE ONE TIMED WAIT. Read this with
        `_drive`.

        Every call in here can block forever against a server that accepts and never answers:
        the spawn and `initialize` inside `session_factory`, `thread/start` or
        `thread/resume`, the durable writes, and `turn/start`. They used to run on the
        caller's thread AHEAD of the wait, so the wait was reached only if all of them
        returned — which is why a quiet server hung with no bound at all, and why
        `kill_process_tree` could not rescue it: its only trigger is a timeout that had never
        started.

        They run here for exactly the reason the frame reader already did. A timeout can only
        observe a blocking call from the OTHER SIDE of it, and the fix for an uncovered call
        is to move it to that side — never to add a second clock to this one.

        `thread_id is None` means open a new thread; otherwise reopen that one. The two paths
        differ only in WHICH call opens the session — everything after it, including the
        identity the attempt then runs under, is one path rather than two near-copies free to
        drift apart.

        EXCEPTIONS ARE CARRIED, NOT RAISED. `ServerSpawnError`, `FenceViolation`,
        `ProtocolError`, `ServerDied` and the record store's `OSError` used to escape `run` as
        a raw traceback, taking the retry, the structured tooling failure AND the record's
        terminal update with them — leaving a file on disk reading `running` with a null
        `ended_at` forever, which §7.9 row 3 then forbids anyone from reclassifying.
        """
        try:
            session = session_factory()
            # THE FACTORY CAN RETURN AFTER THE WAIT GAVE UP ON IT. `_finish` then saw no pid and
            # no session, `_drive` returned a setup abort, and `run` was free to begin attempt
            # two — while this worker went on to open a thread, write a record and start a REVIEW
            # behind it. Everything below this line is such a side effect, so the flag is read
            # here, at the first instant the worker is awake enough to read it.
            ctx.stop_if_cancelled(session)
            # THE PID SLOT, filled before a single wire call is made on this session. From
            # here on an expiry has a tree to kill.
            ctx.publish_session(session)

            # THE IDENTITY OF THE THREAD IS THE SERVER'S ANSWER, NEVER THE ID WE SENT. Both
            # branches read it off the `ThreadSession` the call RETURNED, and for the same
            # reason: a server is free to canonicalise, normalise or re-issue an id, and its
            # frames then carry the id IT chose. Reading our own id back on the reopen path was
            # the defect. Every frame of a correct review was judged foreign, so the
            # `turn/completed` that ends the drain never reached `session.close()`, and the
            # reader went on blocking on a stream that would not end — measured against a
            # `frames()` that blocks the way the real one does: the attempt spent its whole
            # grant, produced nothing, and the stream was ended by the waiting thread rather
            # than by the reader.
            #
            # THE RECORD IS THEREFORE BUILT AFTER THE OPENING CALL ON BOTH PATHS. §5.1 STEP 1
            # is what forces it on the start path — threadId does not exist until thread/start
            # returns — and the reopen path now honours the same rule for the same reason: the
            # id this attempt must record, name its record file with, and attribute frames
            # against is not settled until thread/resume answers. §5.1 STEP 2 is unchanged and
            # still met, because it asks for durability before work is DISPATCHED and
            # `turn/start` is the dispatch. The consequence is deliberate and now identical on
            # both paths: an attempt that dies inside the opening call leaves no record for
            # itself, because it never learned an identity to write one under — and on the
            # reopen path the record it was reopening is still on disk, untouched.
            if thread_id is None:
                opened = session.thread_start(**plan.session_settings)
                initial_state = RecoveryState.RUNNING
            else:
                opened = session.thread_resume(thread_id, **plan.session_settings)
                initial_state = RecoveryState.RESUMED
            # The same check again, for the hang that was inside the opening call rather than
            # inside the factory. Past this line come the record file, `turn/start` and the
            # stream, which is the same set of side effects behind an attempt already returned.
            ctx.stop_if_cancelled(session)
            resolved_id = opened.thread_id
            # §0.1 keeps the server-REPORTED model separate from the requested one, and the
            # `ThreadSession` just returned is the only place it can be read from.
            reported_model = opened.reported_model
            # AN ANSWER THAT NAMES NO THREAD IS REFUSED rather than answered with the id we
            # sent. Silence is not agreement — the same rule
            # `_refuse_incompatible_resume` states about an absent settings digest. Reading it
            # as "unchanged" is what would reopen the misattribution above by another route,
            # and an unnamed thread has no identity to build a record filename from either.
            # `transport` cannot produce this: `Transport._session_from` raises before it
            # returns when the result carries no `thread.id`. The guard is here for a session
            # this module is HANDED, which is the only kind it ever sees.
            if not isinstance(resolved_id, str) or not resolved_id:
                raise SupervisorError(
                    "the opened session named no thread, so this attempt has no identity to "
                    f"record under or to attribute frames against: {resolved_id!r}"
                )

            # THE SERVER'S ID NOW BECOMES A PATH. `record_filename` refuses one that is not a
            # single path component, so a value this module does not author cannot decide where
            # the record lands; the refusal arrives as a setup failure on this attempt.
            store = RunRecordStore(self._record_dir, resolved_id, attempt=attempt)
            state = _StreamState()
            record = self._new_record(
                plan,
                session,
                resolved_id,
                attempt=attempt,
                initial_state=initial_state,
                reported_model=reported_model,
            )
            ctx.publish_run(store, record, state)
            # §5.1 STEP 2 — DURABLE BEFORE ANY WORK IS DISPATCHED. If this raises, no turn has
            # started, so nothing exists that would need recovering.
            store.write(record)

            # §5.1 STEP 3. `turn_start` returns the turn id and the record of the turn as it
            # was actually sent; §7.2a-5 asserts against that record, which `transport` keeps,
            # so nothing is carried here that would become a second copy of it.
            turn_id, _turn_record = session.turn_start(
                resolved_id, plan.packet_text, plan.output_schema
            )
            record.turn_id = turn_id
            # §5.1 STEP 4 — same file, atomic update.
            store.write(record)

            self._live_state = state
            self._read_until_eof(session, state, store, record, ctx.done)
        except BaseException as exc:  # noqa: BLE001 - carried to `_finish`, never swallowed
            ctx.error = exc
        finally:
            ctx.done.set()

    def _drive(
        self,
        plan: RunPlan,
        session_factory: Callable[[], TransportSession],
        attempt: int,
        thread_id: str | None,
        final: bool,
    ) -> "AttemptOutcome | _SetupAbort":
        """§5.4a's REOPEN path is `thread_id is not None`, and ONLY that path. Read this
        before changing it.

        §5.4a names two situations a resume can land in and says they need different handling:
        with the app-server ALIVE the resume REJOINS the in-flight turn — same turn id, deltas
        keep flowing, and starting a fresh turn would duplicate or change the review. With the
        app-server DEAD the thread reopens but the interrupted turn is terminal, no method
        continues it, and a fresh `turn/start` is the only option.

        THE DISCRIMINATION §5.4a ASKS FOR IS NOT BUILDABLE ON THE BUILT INTERFACE, AND IT IS
        LEFT UNBUILT RATHER THAN FAKED. `transport.Transport.thread_resume` returns a
        `ThreadSession`, whose fields are the thread id, the requested and reported models,
        the reported cwd, sandbox, approval policy, approvals reviewer and instruction
        sources. There is no turn id on it and no turn status. Nothing else on the interface
        answers the question either: deciding it from the frame stream would mean waiting to
        see whether deltas arrive, and a wait is a second place a duration is decided, which
        C-34 (SEVERE) forbids outright.

        What the reopen path DOES is the REOPEN branch, because that is the branch the
        architecture forces rather than the branch that was easier. `session_factory` yields a
        NEWLY SPAWNED app-server process on every call — the previous attempt's session is
        closed in `_drive`'s own `finally`, and `resume_from_record` runs in a later process
        entirely. A turn in flight inside some other process is not rejoinable from this one,
        so the ALIVE branch is unreachable here and `rejoined_in_flight_turn` is reported as
        false because it is false, not because it was not checked.

        A `session_factory` that handed back a LIVE session already inside an in-flight turn
        would break that reasoning, and this module cannot detect one. That is the gap; it is
        named here rather than papered over.
        """
        ctx = _AttemptContext()
        try:
            worker = threading.Thread(
                target=self._open_and_stream,
                args=(ctx, plan, session_factory, attempt, thread_id),
                name="discriminator-attempt",
                daemon=True,
            )
            worker.start()
        except BaseException as exc:  # noqa: BLE001 - reported, never swallowed
            # `Thread.start` raises `RuntimeError` when the process cannot create another thread.
            # It is outside the worker's own boundary by construction — there is no worker yet —
            # so it needs its own, or the whole run dies with a traceback before the one timeout
            # has even been armed.
            return _SetupAbort(
                reason=StopReason.SETUP_FAILED,
                detail=(
                    f"attempt {attempt} could not start its worker thread: "
                    f"{_describe_error(exc)}"
                ),
            )

        # THE ONE TIMEOUT — the only duration this module names, read from the one place it is
        # defined, and the only thing permitted to stop a run. IT BOUNDS THIS WAIT AND NOTHING
        # AFTER IT. `_finish` below shells out to kill the tree, probes the child, takes the
        # record store's lock, serializes, fsyncs and replaces, and none of that is bounded —
        # a worker still inside a record write holds that lock and the terminal write waits on
        # it, so this call can return later than the grant. Nothing below waits with a deadline
        # and nothing is to be given one: a second wait carrying a duration would be the second
        # timer C-34 forbids, and a join with one is still one.
        finished = ctx.done.wait(THE_TIMEOUT_SECONDS)
        # THE ATTEMPT IS RETURNED FROM HERE ON, however `_finish` goes, so a worker still inside
        # a blocking call must not carry on behind it (`_open_and_stream` reads this where its
        # blocking calls return). A flag decides no duration and names no interval, so it is not
        # the second timer C-34 forbids; when the wait was satisfied by the worker rather than by
        # the clock the worker has already finished and this is a no-op.
        ctx.cancelled.set()

        try:
            return self._finish(ctx, attempt=attempt, final=final, finished=finished)
        except BaseException as exc:  # noqa: BLE001 - reported, never swallowed
            # THE WORKER'S BOUNDARY DOES NOT COVER THIS THREAD, and `_finish` does real work that
            # really raises: `_resolve_taskkill` raises when taskkill cannot be found, the terminal
            # `store.write` raises `OSError` (on Windows a sharing violation on `os.replace` is a
            # live class), and `_process_alive` shells out too. Driven: with `SystemRoot` unset the
            # `SupervisorError` from `_resolve_taskkill` escaped `run()` raw AT THE EXACT MOMENT
            # THE TIMEOUT EXPIRED, skipping the terminal update and stranding the record at
            # `running` with a null `ended_at` — the same outcome the worker's boundary was added
            # to prevent, one thread over.
            salvaged = ""
            _session, _pid, store, record, state, _error = ctx.snapshot()
            if state is not None:
                try:
                    salvaged = state.output()
                except Exception:  # noqa: BLE001 - salvage is best effort by definition
                    salvaged = ""
            if record is not None and store is not None and state is not None:
                # AN IDENTITY EXISTED, SO THIS IS NOT A SETUP ABORT. `_SetupAbort` says in its
                # own docstring that no thread id was ever returned, and `_assemble` acts on
                # that claim: an abort gets no row in `record_paths`, loses its attempt number,
                # thread and turn, and has its salvaged text placed BEFORE every outcome's
                # rather than in attempt order — so attempt two's partial preceded attempt
                # one's. A thread, a turn, a store and a record on disk all existed here and
                # only the teardown failed, so the attempt is reported as the attempt it was.
                #
                # `kill` is None because the result of the kill was a local in `_finish` and
                # went with the exception; None is "no kill result reached this report", which
                # is what happened. `recovery_state` is the record's own last value rather than
                # a fresh classification, because the terminal update is exactly what may not
                # have landed — and since `RunRecordStore.write` now commits `updates` to the
                # record only AFTER the replace succeeds, that value is the one on disk rather
                # than a terminal state nothing persisted.
                dispositions = state.dispositions()
                attribution = state.attribution()
                return AttemptOutcome(
                    attempt=record.attempt,
                    # Never `complete`: the terminal write is what failed, so nothing here can
                    # show the record and the answer agree. `turn_status` still carries the
                    # turn's own verdict, which is what stops `run` paying for a second review
                    # of a turn that finished.
                    complete=False,
                    stop_reason=(
                        StopReason.TIMEOUT if not finished else StopReason.FINISH_FAILED
                    ),
                    turn_status=state.turn_status,
                    thread_id=record.thread_id,
                    turn_id=record.turn_id,
                    accumulated_output=salvaged,
                    liveness=state.ledger.snapshot(),
                    # THE STORE'S OWN ANSWER. This is the path a failing record write reaches, so
                    # it is the path where `store.path` is most likely to name a file nothing
                    # created — the attempt keeps its row, and the row simply does not claim a
                    # durable artifact it has no evidence for.
                    record_path=store.path if store.wrote_durably else None,
                    recovery_state=RecoveryState(record.recovery_state),
                    rejoined_in_flight_turn=False,
                    retrying_error_count=state.retrying_error_count,
                    terminal_error=state.terminal_error,
                    kill=None,
                    reader_error=state.reader_error,
                    server_died=state.server_died,
                    setup_error=(
                        f"attempt {attempt} could not be finished: {_describe_error(exc)}"
                    ),
                    foreign_frames=tuple(attribution["foreign_frames"]),
                    unattributed_frame_count=attribution["unattributed_frame_count"],
                    unparseable_frames=tuple(dispositions["unparseable_frames"]),
                    server_requests=tuple(dispositions["server_requests"]),
                    late_responses=tuple(dispositions["late_responses"]),
                    ignored_notification_counts=dict(
                        dispositions["ignored_notification_counts"]
                    ),
                )
            # NO IDENTITY EXISTED, so this really is the shape `_SetupAbort` documents.
            return _SetupAbort(
                reason=StopReason.TIMEOUT if not finished else StopReason.SETUP_FAILED,
                detail=(
                    f"attempt {attempt} could not be finished: {_describe_error(exc)}"
                ),
                accumulated_output=salvaged,
            )
        finally:
            self._live_state = None
            session = ctx.session
            if session is not None:
                # Closes the send side only; this is not the kill. The worker cannot do it in
                # its own `finally`, because the outcome built above reads `is_alive()` and
                # closing first would answer that question about a session we had just shut.
                try:
                    session.close()
                except Exception:  # noqa: BLE001
                    # Best-effort cleanup, and the swallow is deliberate rather than lazy: an
                    # exception raised in a `finally` DISCARDS the return value, so letting one
                    # through here would throw away a COMPLETED review because tearing down its
                    # already-finished session went wrong. Losing the answer is the worse outcome
                    # by a wide margin, and `Transport.close` swallows `OSError` itself, so
                    # anything reaching here is already outside the contract this module holds.
                    pass

    def _finish(
        self,
        ctx: _AttemptContext,
        attempt: int,
        final: bool,
        finished: bool,
    ) -> "AttemptOutcome | _SetupAbort":
        """Everything after the one wait: the kill, the seal, the single terminal update.

        NONE OF IT IS BOUNDED, and saying so is the honest version of what the one timeout
        promises. `kill_process_tree` and `_process_alive` shell out, `is_alive` probes the
        child, and the terminal write takes `RunRecordStore._lock` — which a worker still
        inside a record write is holding. The bound is on the wait in `_drive`, not on the
        return of this function. Giving this side a bound of its own means a second wait
        carrying a duration, which C-34 (SEVERE) forbids by name.

        THE WORKER IS NEVER JOINED HERE. On the expiry path it is still blocked inside
        whatever call hung, and waiting for it would put an unbounded block immediately after
        the only mechanism allowed to stop a run.
        """
        session, pid, store, record, state, error = ctx.snapshot()

        kill: KillResult | None = None
        if not finished:
            if pid is not None:
                try:
                    kill = kill_process_tree(pid)
                except BaseException as kill_error:  # noqa: BLE001 - reported, never swallowed
                    # A KILL THAT COULD NOT BE ATTEMPTED IS A RESULT, NOT AN ABORT. `taskkill` can
                    # be unresolvable (`SystemRoot` unset and not on PATH — driven), and shelling
                    # out can fail for reasons that have nothing to do with this run. Letting that
                    # escape would skip the terminal update below and strand the record at
                    # `running` with a null `ended_at`, which is the exact failure this attempt is
                    # being torn down to avoid. `still_alive=True` because nothing killed anything.
                    kill = KillResult(
                        still_alive=True,
                        detail=(
                            f"the kill could not be attempted: {_describe_error(kill_error)}; "
                            f"the child tree at pid {pid} was not signalled"
                        ),
                    )
            else:
                # The expiry landed inside session creation — the spawn, or `initialize` — so
                # no child pid ever reached this thread and there is nothing to aim a kill at.
                # `still_alive` is True because that is the safe direction to be wrong in: a
                # child may exist, and reporting a kill that was never issued is exactly the
                # false clean this tool exists to prevent.
                kill = KillResult(
                    still_alive=True,
                    detail=(
                        "the one timeout expired while the session was still being created; "
                        "no child pid was reachable from the supervisor, so no kill was "
                        "issued and a spawned app-server may have survived"
                    ),
                )

        if record is None or store is None or state is None:
            # No thread id was ever returned, so there is nothing to seal and no
            # `AttemptOutcome` to build — its `thread_id` and `record_path` are not nullable,
            # and a record path that does not exist would break R15 in the struct that makes
            # the promise.
            #
            # NO RECORD IS WRITTEN ON THIS PATH, AND THAT IS THE DECISION RATHER THAN AN
            # OVERSIGHT. Writing one would mean naming a file after an identity the server never
            # confirmed — the id we SENT, which `_open_and_stream` states its reason for not
            # trusting — and on the reopen path that filename could land on top of the record
            # this attempt was reopening, which is the loss this module exists to prevent rather
            # than a trace of it. A hang here is exactly the case where the child may have
            # survived, so the kill above is attempted on the hang when a pid was published
            # and its result is folded into `detail` below. The trace is therefore RETURNED and not
            # durable: `_assemble` carries every abort's reason and detail into the
            # `ToolingFailure` it builds for a review that did not complete, and on the reopen
            # path the prior attempt's own record is still on disk, untouched.
            if not finished:
                reason = StopReason.TIMEOUT
                detail = f"attempt {attempt} hit the one timeout before a thread was opened"
            else:
                reason = StopReason.SETUP_FAILED
                detail = (
                    f"attempt {attempt} failed before a thread was opened: "
                    f"{_describe_error(error)}"
                )
            if kill is not None:
                detail = f"{detail}; {kill.detail}"
            return _SetupAbort(
                reason=reason,
                detail=detail,
                accumulated_output="" if state is None else state.output(),
            )

        output = state.output()
        snapshot = state.ledger.snapshot()
        dispositions = state.dispositions()
        attribution = state.attribution()

        # A TURN THAT COMPLETED IS NOT THE SAME AS A REVIEW THAT WAS FULLY DRAINED, and keeping
        # them apart IS the founding rule. §4.1a: `turn/completed` marks the STATUS, never the
        # END OF THE STREAM — interrupt trials delivered further deltas AFTER it, same turn id.
        # So when the one timeout cuts the drain short, what we hold may be SHORTER than what the
        # model produced, and calling that complete is §7.3 broken silently.
        #
        # AN EARLIER VERSION OF THIS FIX DROPPED `finished` FROM THE TEST and a cross-family
        # reviewer caught it. The intent was right — a completed review was being thrown away —
        # but it traded a FALSE NEGATIVE (a good review reported as a failure: wasteful, and safe
        # in the direction that matters) for a FALSE POSITIVE (a truncated review reported as
        # complete: silent, and unsafe). The answer is now kept and the retry is not spent, but
        # `complete` still means DRAINED.
        turn_finished = (
            error is None
            and state.reader_error is None
            and state.turn_status == TURN_STATUS_COMPLETED
            and state.terminal_error is None
        )
        complete = turn_finished and finished

        # §6.4 — EVIDENCE OF DEATH and ABSENCE OF LIFE are different findings and are kept
        # apart. `server_died` is `transport`'s own out-of-band evidence (an EOF the client
        # did not ask for); the `is_alive` probe is the second reading, for the case where the
        # stream ended cleanly and the child is nonetheless gone. Silence alone is neither.
        #
        # `complete` is tested FIRST so a turn that finished is reported as having finished even
        # when we stopped waiting for its pipe. The kill still fired in that case and that is
        # correct — we killed a child that had already answered.
        if complete:
            stop_reason = StopReason.TURN_COMPLETED
        elif not finished:
            stop_reason = StopReason.TIMEOUT
        elif error is not None:
            stop_reason = StopReason.SETUP_FAILED
        elif state.reader_error is not None:
            stop_reason = StopReason.READER_FAILED
        elif state.terminal_error is not None:
            stop_reason = StopReason.ERROR_TERMINAL
        elif state.turn_status is not None:
            stop_reason = StopReason.TURN_COMPLETED
        elif state.server_died is not None:
            stop_reason = StopReason.PROCESS_EXITED
        elif session is not None and not session.is_alive():
            stop_reason = StopReason.PROCESS_EXITED
        else:
            stop_reason = StopReason.CONNECTION_CLOSED

        if complete:
            recovery_state = RecoveryState.COMPLETE
        elif final or turn_finished:
            # §7.9 row 3 — `abandoned` is written by a supervisor that is ALIVE and has
            # stopped trying, at its own single exit. It is never synthesised afterwards for
            # a record that simply stops. A turn that FINISHED ends the review whether or not
            # this was the last budgeted attempt, because `run` does not retry a turn that
            # completed — so this supervisor has stopped trying, which is what the row asks.
            recovery_state = RecoveryState.ABANDONED
        else:
            recovery_state = RecoveryState.INTERRUPTED

        # THE ONE ATOMIC TERMINAL UPDATE (§7.9 row 2). Every branch above falls through to
        # this single write site; there is no other terminal write in this module. A setup
        # failure reaches it too, which is what stops a raised `turn/start` leaving the record
        # at `running` with a null `ended_at` forever.
        #
        # `terminal=True` seals the store inside the same lock that writes the file, which is what
        # makes this the LAST write even though the worker is never joined. The fields go in as
        # `updates` rather than being assigned here, so the mutation cannot race the reader's own
        # checkpoint — both writers now touch the record only while holding that lock.
        #
        # Neither reported identity is touched here. Both are fixed when the session is
        # opened — `model["reported"]` comes from the `ThreadSession` that `thread_start` or
        # `thread_resume` returned, `transport["reported"]` from `initialize`'s result — and
        # re-reading them at the exit is how this module came to believe the session carried
        # a `reported_model` attribute it never had.
        store.write(
            record,
            updates={
                "accumulated_output": output,
                "liveness_ledger": snapshot.to_dict(),
                "recovery_state": recovery_state.value,
                "ended_at": _utc_now(),
                **dispositions,
                **attribution,
            },
            terminal=True,
        )

        return AttemptOutcome(
            attempt=record.attempt,
            complete=complete,
            stop_reason=stop_reason,
            turn_status=state.turn_status,
            thread_id=record.thread_id,
            turn_id=record.turn_id,
            accumulated_output=output,
            liveness=snapshot,
            # THE STORE'S OWN ANSWER, NOT THE MERE EXISTENCE OF A STORE. The write above normally
            # lands and this is the path; if every write this attempt made failed, no file was
            # created and naming one would promise an artifact that is not there.
            record_path=store.path if store.wrote_durably else None,
            recovery_state=recovery_state,
            # §5.4a's ALIVE branch is unreachable on the built interface — see `_drive` for
            # why, at length. False here is a report, not a default.
            rejoined_in_flight_turn=False,
            retrying_error_count=state.retrying_error_count,
            terminal_error=state.terminal_error,
            kill=kill,
            reader_error=state.reader_error,
            server_died=state.server_died,
            setup_error=None if error is None else _describe_error(error),
            # THE SAME PAIR THE TERMINAL WRITE ABOVE PUT IN THE RECORD, from the one locked
            # snapshot — for the reason the four below give, and because reading the reader's
            # lists again here is reading them mid-append.
            foreign_frames=tuple(attribution["foreign_frames"]),
            unattributed_frame_count=attribution["unattributed_frame_count"],
            # THE SAME FOUR VALUES THE TERMINAL WRITE ABOVE PUT IN THE RECORD, from the one
            # snapshot taken above. Re-reading `state` here would let the two disagree about
            # what the run saw, and a report that disagrees with its own durable record is the
            # forensic property §5 buys, spent.
            unparseable_frames=tuple(dispositions["unparseable_frames"]),
            server_requests=tuple(dispositions["server_requests"]),
            late_responses=tuple(dispositions["late_responses"]),
            ignored_notification_counts=dict(dispositions["ignored_notification_counts"]),
        )

    def _read_until_eof(
        self,
        session: TransportSession,
        state: _StreamState,
        store: RunRecordStore,
        record: RunRecord,
        drained: threading.Event,
    ) -> None:
        """§4.1a's DRAIN RULE: `turn/completed` marks the STATUS, never the END OF THE STREAM.
        Interrupt trials delivered further deltas after it with the same turn id, so a
        supervisor that closes on the completion marker intermittently truncates the review —
        returning less than the model produced, which is the founding rule broken silently.

        A terminal status therefore stops us SENDING (`close`, which on `Transport` closes the
        send side only) and never stops us READING: the stdout pump is untouched by it, so
        late frames keep arriving and keep being appended.

        Every field read below comes off `transport.ServerFrame`'s own accessors. This loop
        used to re-derive them from `frame.get("method")` / `frame.get("params")`, against a
        producer that hands back a frozen dataclass — the mismatch that produced this repair.

        THE ARM THAT DECIDES IS THE FRAME'S KIND, AND IT IS TOTAL OVER THE FIVE OF THEM. It
        used to be the frame's METHOD, and three of the five kinds have no method to decide on:
        an unparseable frame's is None, a server request carries one this loop never matched,
        and a late response carries none. All three reached the bottom of the loop body and
        stopped existing — not retained, not counted, not reported — and the run then called
        itself complete and correct. Measured end to end: a reviewer that answered `fail` with
        one critical finding was delivered as `approve` with none. The frame-disposition rule
        §3 is the decision table this implements, and its structural requirement is the `else`
        below: a bucket with no arm is a loud failure, never a quiet fall-through.
        """
        # The ids this attempt's own frames carry. Read once, here: `turn_start` has already
        # returned, so the record names both, and re-reading a record the exit path also writes
        # would be watching a moving target for nothing.
        expected_thread = record.thread_id
        expected_turn = record.turn_id
        try:
            for frame in session.frames():
                state.ledger.note_frame()

                if frame.kind is FrameKind.NOTIFICATION:
                    self._dispose_notification(
                        frame, state, store, record, session, expected_thread, expected_turn
                    )
                elif frame.kind is FrameKind.SERVER_REQUEST:
                    # §3: keep, count, tell the caller — do not answer it, do not abort. Not
                    # answering is `transport.FrameKind.SERVER_REQUEST`'s own decision and it
                    # stands; what changes is that the frame is now surfaced rather than
                    # swallowed, which is what that decision's comment already promised.
                    state.note_server_request(frame)
                elif frame.kind in (FrameKind.RESPONSE, FrameKind.ERROR_RESPONSE):
                    # A reply to a request of ours that `_await` is no longer waiting for. It
                    # carries no answer text, so it cannot cost a finding — but an unmatched
                    # reply means our bookkeeping and the server's diverged.
                    state.note_late_response(frame)
                elif frame.kind is FrameKind.UNPARSEABLE:
                    # §3: keep the bytes, count it, tell the caller. NOT an abort — one stray
                    # banner line must not destroy a complete review, which is the fault #84
                    # was. NOT ignored — that is this defect. We cannot read it, so we cannot
                    # know whether it carried part of the answer, and only saying so is honest.
                    state.note_unparseable(frame)
                else:
                    # THE FALL-THROUGH THIS WHOLE CHANGE EXISTS TO DELETE. `transport` grew a
                    # kind with no arm here, or a kind was added to `DISPOSED_FRAME_KINDS`
                    # without one. Either way the honest answer is that this module does not
                    # know what the frame was, and the one answer that is never acceptable is
                    # to do nothing and report the run correct.
                    raise UnhandledFrameKind(
                        f"a frame arrived classified as {frame.kind!r}, which this reader has "
                        f"no disposition for; refusing to drop it silently: {frame.raw!r}"
                    )
        except ServerDied as death:
            # §6.4's evidence of death, raised by `transport` because the EOF was one the
            # client did not ask for. Recorded as death, not as a reader fault.
            state.server_died = f"{type(death).__name__}: {death}"
        except Exception as error:  # noqa: BLE001 - recorded, never swallowed
            state.reader_error = f"{type(error).__name__}: {error}"
        finally:
            drained.set()

    def _dispose_notification(
        self,
        frame: ServerFrame,
        state: _StreamState,
        store: RunRecordStore,
        record: RunRecord,
        session: TransportSession,
        expected_thread: str | None,
        expected_turn: str | None,
    ) -> None:
        """The answer channel — bucket one and bucket two of the frame-disposition rule §3.

        EVERYTHING THE ANSWER PATH DID BEFORE THIS CHANGE, IT STILL DOES, and that is the
        rule's own first row: the path that already carries the review is unchanged, including
        BOTH loud aborts. What is added is the last line — an announcement this reader
        deliberately ignores is now tallied BY NAME instead of vanishing.
        """
        # Reasoning is a different item stream and is NOT the answer. It is positive
        # evidence of life, counted by the ledger, and nothing more. It is TALLIED like
        # every other ignored announcement: §3a's instrument is the whole census of names
        # we do not act on, and a name held out of it is a name that cannot show up as new.
        if frame.is_reasoning_delta:
            state.note_ignored_notification(frame)
            return

        # ATTRIBUTION. `transport.frames()` says it in its own docstring — attribute late
        # frames by turn id — and this loop did not, so any frame reaching it was treated
        # as this turn's. It is applied to the ANSWER frames and the TERMINAL ones,
        # whose handling is a decision about THIS turn: appending text, declaring the run
        # over, and marking the turn finished. The generic `/completed` checkpoint below
        # stays unfiltered, because it only rewrites state we already hold and no other
        # turn's content can enter through it.
        #
        # MISMATCH AND ABSENT ARE DIFFERENT CASES AND ARE ANSWERED DIFFERENTLY. A frame
        # naming another thread or turn is not ours: it is not appended, it does not end
        # our turn, and it is RETAINED whole rather than dropped. A frame carrying NO ids
        # is ACCEPTED — the wire is not known to put ids on every notification, and
        # refusing them would throw away the model's answer to guard a case that cannot
        # be shown to be happening, which is §7.3's rule broken by the guard instead of
        # by the defect. Those are counted, so "accepted unchecked" can never be read
        # back as "checked and matched".
        if (
            frame.method == N_AGENT_MESSAGE_DELTA
            or frame.is_error_notification
            or frame.method == N_TURN_COMPLETED
        ):
            disagreement = _disagreeing_ids(frame, expected_thread, expected_turn)
            if disagreement is not None:
                with state.lock:
                    state.foreign_frames.append(
                        {
                            "method": frame.method,
                            "thread_id": frame.thread_id,
                            "turn_id": frame.turn_id,
                            "mismatch": disagreement,
                            "raw": frame.raw.decode("utf-8", errors="replace"),
                        }
                    )
                return
            if frame.thread_id is None or frame.turn_id is None:
                with state.lock:
                    state.unattributed_frame_count += 1

        if frame.method == N_AGENT_MESSAGE_DELTA:
            delta = frame.agent_message_delta
            if delta is None:
                # The frame said it carried answer text and the text was not readable.
                # Loud, because dropping it is §7.3's founding rule broken silently.
                raise WireVocabularyDrift(
                    "an agent-message delta carried no readable text; refusing to "
                    f"drop model output silently: {frame.raw!r}"
                )
            with state.lock:
                state.chunks.append(delta)
            return

        if frame.is_error_notification:
            # §6.4 — the protocol hands us "an error arrived" and "this run is over"
            # as separate facts. `will_retry` is the one field to read; the payload
            # around it changes shape between the retrying and the terminal error, so
            # a parser keyed on that payload breaks on one of the two.
            will_retry = frame.will_retry
            if will_retry is True:
                state.retrying_error_count += 1
                # NOT death. The server is retrying. Do not kill it, do not stop.
            elif will_retry is False:
                state.terminal_error = dict(frame.params)
                self._checkpoint(store, record, state)
                session.close()
            else:
                # An error notification with no readable `willRetry`. Guessing either
                # way is the collapse §6.4 forbids: guess retrying and a dead run
                # hangs to the timeout; guess terminal and a live one is abandoned.
                raise WireVocabularyDrift(
                    "an error notification carried no readable willRetry; §6.4 "
                    f"refuses to infer whether the run is over: {frame.raw!r}"
                )
            return

        if frame.method == N_TURN_COMPLETED:
            state.turn_status = frame.turn_status
            self._checkpoint(store, record, state)
            session.close()
            return

        if isinstance(frame.method, str) and frame.method.endswith("/completed"):
            # A structural checkpoint boundary — an item finished, so the accumulated
            # answer is stable here. The boundary is structural and never a clock: a
            # flush interval would be a second place a duration is decided (C-34).
            self._checkpoint(store, record, state)
            return

        # BUCKET TWO. Ignoring is correct — these are lifecycle, status and usage events that
        # carry no answer — but a SINGLE TOTAL would distinguish nothing, and that is measured
        # rather than assumed: the real server sends about two hundred of these on every
        # healthy run from a stable set of names. A per-name tally is the only shape in which a
        # name that has never been seen before can show up in the record, which is the
        # instrument §3a needs for the gap it deliberately declines to guard.
        state.note_ignored_notification(frame)

    def _checkpoint(self, store: RunRecordStore, record: RunRecord, state: _StreamState) -> None:
        """§5.1 step 5 — checkpoint the accumulated output as the stream proceeds. Without it
        "return only the partial output" is not an option this module has, and §5.4a makes it
        the only complete record we are entitled to rely on: the rollout persists the reasoning
        and tool items, and the protocol does not serve them back.

        The seal is NOT consulted here. It is enforced inside `store.write`, under the same lock
        that serializes and replaces the file — checking it here would be the check-then-act that
        let a stale payload erase the terminal update.

        The four dispositions ride along for the reason the accumulated output does: a run that
        dies before its terminal update still leaves a record, and a record that holds the answer
        but not what was dropped beside it is the pair §5 requires to be distinguishable, made
        indistinguishable again on the one path where it matters most.

        THE ATTRIBUTION PAIR RIDES ALONG FOR EXACTLY THAT REASON TOO. A checkpointed record that
        held the answer but not the count of frames whose bytes went into it unchecked would let a
        resume report the answer as fully attributed, which is the same silence one field over.
        """
        store.write(
            record,
            updates={
                "accumulated_output": state.output(),
                "liveness_ledger": state.ledger.snapshot().to_dict(),
                **state.dispositions(),
                **state.attribution(),
            },
            best_effort=True,
        )

    # -- helpers -----------------------------------------------------------------------
    def _new_record(
        self,
        plan: RunPlan,
        session: TransportSession,
        thread_id: str,
        attempt: int,
        initial_state: RecoveryState,
        reported_model: str | None,
    ) -> RunRecord:
        """`reported_model` is passed in rather than read off the session: it is a field of
        the `ThreadSession` the thread call RETURNED, and no session object carries it."""
        return RunRecord(
            thread_id=thread_id,
            turn_id=None,
            cwd=plan.cwd,
            artifact_class=plan.artifact_class,
            contract=plan.contract,
            model={
                "requested": plan.model_requested,
                "reported": reported_model,
                "attested_route": None,
            },
            transport={
                "requested": plan.transport_requested,
                # `initialize`'s result. `transport` raises if the handshake has not run, and
                # that is the right failure: an empty mapping here would put a silent blank
                # where §0.1 requires a reported identity.
                "reported": session.server_info.get("userAgent"),
            },
            packet_digest=plan.packet_digest,
            schema_version=SCHEMA_ID,
            accumulated_output="",
            recovery_state=initial_state.value,
            liveness_ledger=LivenessLedger().snapshot().to_dict(),
            rubric_version=dict(plan.rubric_version),
            started_at=_utc_now(),
            attempt=attempt,
            # Taken from the plan this run was actually opened with, so a later process can ask
            # whether its own plan is the same one before resuming into it.
            session_settings_digest=_settings_digest(plan.session_settings),
            # The response constraint this run was actually opened under, so a later process can
            # ask whether its own plan asks for the same one before resuming into it.
            output_schema_digest=_output_schema_digest(plan.output_schema),
        )

    def _refuse_incompatible_resume(self, prior: RunRecord, plan: RunPlan) -> None:
        """§5 — a resume that would change the review is refused.

        `rubric_version` is checked SEPARATELY from `packet_digest` and that separation is the
        point (§5.1): the digest moves for any packet change, so it can say "not the same
        review" and can never say "a different rubric". A resume across the null boundary is
        refused for the same reason.

        THE PACKET IS NOT THE WHOLE REVIEW. Everything above describes what was ASKED; the
        settings, the requested model and the requested transport describe WHO WAS ASKED and
        under what configuration, and a resume that changes those is a different review by the
        same argument. Without them a later process could resume with other instructions, other
        configuration, another model or another transport while this method asserted the review
        was unchanged.
        """
        mismatches: list[str] = []
        if dict(prior.rubric_version) != dict(plan.rubric_version):
            mismatches.append("rubric_version")
        if prior.packet_digest != plan.packet_digest:
            mismatches.append("packet_digest")
        if prior.artifact_class != plan.artifact_class:
            mismatches.append("artifact_class")
        if prior.contract != plan.contract:
            mismatches.append("contract")
        if prior.cwd != plan.cwd:
            mismatches.append("cwd")
        # §0.1 keeps requested and reported apart, and it is the REQUESTED value that belongs
        # here: it is what this run asked for and what the resume would ask for again. The
        # reported one is the server's answer and is expected to differ between processes.
        if prior.model.get("requested") != plan.model_requested:
            mismatches.append("model_requested")
        if prior.transport.get("requested") != plan.transport_requested:
            mismatches.append("transport_requested")
        if prior.session_settings_digest is not None:
            try:
                current_digest = _settings_digest(plan.session_settings)
            except SupervisorError as error:
                # A BUNDLE THIS PROCESS CANNOT DIGEST IS A REFUSAL TO RESUME, and it is raised
                # as one. `ResumeRefused` is what a caller of `resume_from_record` catches;
                # letting `_canonical`'s bare `SupervisorError` through here turned a documented
                # catchable refusal into an uncatchable one at the single site built for it.
                raise ResumeRefused(f"refusing to resume: {error}") from error
            if prior.session_settings_digest != current_digest:
                mismatches.append("session_settings")
        if prior.output_schema_digest is not None:
            try:
                current_schema = _output_schema_digest(plan.output_schema)
            except SupervisorError as error:
                # `_canonical`'s refusal, turned into the catchable one for the reason the
                # settings digest above gives.
                raise ResumeRefused(f"refusing to resume: {error}") from error
            if prior.output_schema_digest != current_schema:
                mismatches.append("output_schema")
        if mismatches:
            raise ResumeRefused(
                "refusing to resume: a resume that changes "
                + ", ".join(mismatches)
                + " is a different review"
            )
        if prior.session_settings_digest is None:
            # AN ABSENT DIGEST IS NOT A MATCH, and it is not a mismatch either — nothing was
            # compared. It gets its own sentence because "we cannot tell" is a different thing
            # to tell a caller than "these differ", and reading silence as agreement is the
            # whole defect. Reported after the list above so a record that ALSO differs in the
            # packet says so, which is the more useful finding.
            raise ResumeRefused(
                "refusing to resume: the prior record carries no digest of the session "
                "settings, so there is no way to show this resume asks the same reviewer "
                "under the same configuration. An absent digest is not a match."
            )
        if prior.output_schema_digest is None:
            # THE SAME SENTENCE ABOUT THE RESPONSE CONSTRAINT, and it needs its own because the
            # record already carries a `schema_version` that LOOKS like an answer and is not:
            # it is the record format's version, written identically whatever the turn was
            # constrained by. A resume that read it as agreement would happily answer the same
            # packet under a different constraint — or unconstrained — and call it the same
            # review.
            raise ResumeRefused(
                "refusing to resume: the prior record carries no digest of the output schema, "
                "so there is no way to show this resume asks for the same response constraint. "
                "An absent digest is not a match."
            )

    def _assemble(
        self,
        outcomes: Sequence[AttemptOutcome],
        prior: AttemptOutcome | None = None,
        aborts: Sequence[_SetupAbort] = (),
    ) -> RunOutcome:
        """`prior` is an attempt of this review that a LATER process is resuming — a row read
        back off its record rather than driven here. It is kept out of `outcomes` deliberately:
        the failure reason below must stay THIS invocation's, and a prior row standing in for it
        would report the resumed attempt's state as the reason the resume failed.
        """
        final_output: str | None = None
        partials: list[str] = []
        if prior is not None and prior.accumulated_output:
            # First, because it is first in attempt order (§5.4a: never discarded, never merged).
            partials.append(prior.accumulated_output)
        for abort in aborts:
            # An attempt that produced no outcome may still have produced TEXT. R15 and §5.4a
            # both say the same thing here: never drop an artifact the caller was promised.
            if abort.accumulated_output:
                partials.append(abort.accumulated_output)
        for outcome in outcomes:
            if outcome.complete:
                final_output = outcome.accumulated_output
            elif outcome.accumulated_output:
                # §5.4a — never discarded and never silently merged.
                partials.append(outcome.accumulated_output)
        complete = final_output is not None
        failure: ToolingFailure | None = None
        if not complete:
            # An attempt that aborted during setup produced no `AttemptOutcome`, so without
            # this it would vanish from the report entirely — including the case where EVERY
            # attempt did, which is the one a caller most needs told.
            aborted = "; ".join(f"{a.reason.value}: {a.detail}" for a in aborts)
            if outcomes:
                last = outcomes[-1]
                reason = last.stop_reason.value
                detail = (
                    f"attempt {last.attempt} stopped with turn status {last.turn_status!r}; "
                    f"reader_error={last.reader_error!r}; server_died={last.server_died!r}; "
                    f"setup_error={last.setup_error!r}"
                )
            elif aborts:
                reason = aborts[-1].reason.value
                detail = "no attempt opened a thread"
            else:
                reason = StopReason.SETUP_FAILED.value
                detail = "no attempt ran"
            if aborted:
                detail = f"{detail} | aborted attempts: {aborted}"
            failure = ToolingFailure(
                reason=reason,
                detail=detail,
                # Every attempt spent, whether or not it reached a thread — and on a resume
                # that includes the attempts spent BEFORE this process existed, which is what the
                # prior record's own attempt number counts. Counting only this invocation's
                # under-reports the budget C-4 bounds by exactly the attempts a resume follows.
                attempts=len(outcomes) + len(aborts) + (0 if prior is None else prior.attempt),
            )
        return RunOutcome(
            complete=complete,
            final_output=final_output,
            partial_outputs=tuple(partials),
            attempts=(() if prior is None else (prior,)) + tuple(outcomes),
            tooling_failure=failure,
        )
