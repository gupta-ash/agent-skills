"""Codex app-server stdio transport and the reviewer session fence.

Owns process spawning, outbound payloads, response correlation and frame
classification. Supervisor owns lifecycle, timeout and termination; envelope owns
review interpretation. Drive public methods from one thread.

The wire uses LF-delimited JSON. Initialize precedes thread operations; responses
need not contain jsonrpc. turn/completed is a status notification, not EOF, and
frames continue until the caller stops or the process dies. EOF is ServerDied,
never an inference based on a quiet period.

Every applicable outbound payload uses the same empty session directory and
pinned sandbox/approval settings; resume echoes are checked. Reviewer content is
inlined in the packet, not granted as a repository read root. Outbound bytes and
turn IDs remain inspectable so recovery cannot hide a different sent payload.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterator, Mapping, Sequence

# --- the wire vocabulary -----------------------------------------------------------------
# Method names as the server's own generated protocol schema spells them. Values only;
# meaning belongs to the sections cited above.
#
# The item- and thread-lifecycle names are PUBLISHED for consumers to filter on, and
# nothing in this file branches on them — deliberately. `supervisor` needs `item/completed`
# because §5.4a makes the last COMPLETED item the recovery boundary. Centralising the
# strings here is the point: a consumer that hardcodes them acquires a second copy of a
# vocabulary that moves on an `[experimental]` interface, and nothing would detect the drift.

M_INITIALIZE = "initialize"
M_THREAD_START = "thread/start"
M_TURN_START = "turn/start"
M_TURN_INTERRUPT = "turn/interrupt"
M_THREAD_RESUME = "thread/resume"

N_AGENT_MESSAGE_DELTA = "item/agentMessage/delta"
N_REASONING_TEXT_DELTA = "item/reasoning/textDelta"
N_REASONING_SUMMARY_TEXT_DELTA = "item/reasoning/summaryTextDelta"
N_REASONING_SUMMARY_PART_ADDED = "item/reasoning/summaryPartAdded"
N_ITEM_STARTED = "item/started"
N_ITEM_COMPLETED = "item/completed"
N_TURN_STARTED = "turn/started"
N_TURN_COMPLETED = "turn/completed"
N_THREAD_STARTED = "thread/started"
N_ERROR = "error"

# The reasoning deltas are listed because §6.1 needs them to exist — the reasoning phase is
# not structurally silent on this transport. They are NOT part of the model's answer, and
# separating them from `N_AGENT_MESSAGE_DELTA` is why that vocabulary lives here rather
# than in whichever component accumulates the answer.
REASONING_DELTA_METHODS = frozenset(
    {
        N_REASONING_TEXT_DELTA,
        N_REASONING_SUMMARY_TEXT_DELTA,
        N_REASONING_SUMMARY_PART_ADDED,
    }
)

# --- the pinned session settings (§7.5b owns the values; this module applies them) --------
# There is no parameter for any of these. §7.5b: what the reviewer session needs is
# NOTHING — the artifact arrives as packet text, so no filesystem, no network, no exec.

SANDBOX = "read-only"           # resolves server-side to readOnly with networkAccess false
APPROVAL_POLICY = "never"       # suppresses the prompt; the sandbox is what restricts
APPROVALS_REVIEWER = "user"     # NEVER auto_review: that routes approvals to a model

# THE SANDBOX ECHO IS NOT THE TOKEN THAT WAS SENT, and that is why checking it needs its own
# pinned value. §7.5b measured `read-only` coming back RESOLVED, as an object, with
# `networkAccess` closed by the same setting — so an equality against SANDBOX would raise
# FenceViolation on every real run, which is how this check ends up deleted rather than fixed.
# `_check_echo` matches these keys individually: every one of them must be present and equal,
# and a key the server adds later is not a violation of anything we pinned.
SANDBOX_RESOLVED_ECHO: dict[str, Any] = {"type": "readOnly", "networkAccess": False}

# §7.5a rule 4 — defence in depth, never the fence. This is the one closing key with both
# halves demonstrated. It is merged over any caller config, pinned key winning.
DEFENCE_IN_DEPTH_CONFIG: dict[str, Any] = {"project_doc_max_bytes": 0}

# stderr carries human-readable tracing only — nothing protocol-bearing was observed on it
# in any measured run. It is kept as a bounded tail purely so a failure can be explained.
STDERR_TAIL_LINES = 200

# How many times in a row a raw `write()` may answer "wrote nothing" before the frame is given
# up on. AN ATTEMPT COUNT, NOT A DURATION.
ZERO_PROGRESS_WRITES_ALLOWED = 16


# --- named conditions ---------------------------------------------------------------------


class TransportError(Exception):
    """Base for every failure this module raises. Never raised directly."""


class ServerSpawnError(TransportError):
    """The `codex app-server` child could not be started, or its executable is unresolvable."""


class ServerDied(TransportError):
    """The stream ended with no sentinel — §4.1a's silent EOF.

    This is a DISTINCT, NAMED condition and not a generic read error, because the
    protocol gives death no announcement at all and §6.1 forbids inferring it from
    silence. `supervisor` confirms it out of band from the process handle; `returncode`
    is attached here as a convenience, and is None when the child has not been reaped yet.
    """

    def __init__(self, message: str, returncode: int | None, stderr_tail: Sequence[str]):
        super().__init__(message)
        self.returncode = returncode
        self.stderr_tail = list(stderr_tail)


class WriteStalled(TransportError):
    """The stream stopped taking bytes; the frame is unsent or part-sent.

    A raw `write()` answering None or 0 is "wrote nothing, not an error", so it is retried —
    but retried a bounded number of times. Distinct from `ServerDied`: the stream did not end,
    it simply stopped taking bytes, and §6.4's rule is that the protocol's distinctions are
    reported rather than collapsed.
    """


class ProtocolError(TransportError):
    """The server answered a request with a JSON-RPC error object.

    Distinct from an `error` NOTIFICATION, which is a turn-scoped event carrying
    `willRetry` and is never raised — see §6.4: the classifier reports the distinction
    and never picks for the caller.
    """

    def __init__(self, method: str, code: Any, message: str):
        super().__init__(f"{method} -> [{code}] {message}")
        self.method = method
        self.code = code
        self.error_message = message


class FenceViolation(TransportError):
    """The server's echo of an applied setting did not match what was sent.

    §7.5a rule 5: the server accepting a key is worth nothing. `thread/start` and
    `thread/resume` echo `cwd`, `sandbox`, `approvalPolicy` and `approvalsReviewer`, so
    the applied fence is checkable rather than assumed, and a mismatch stops the run
    loudly instead of reviewing hostile bytes inside their own repository.
    """


# --- frames ---------------------------------------------------------------------------------


class FrameKind(Enum):
    RESPONSE = "response"
    ERROR_RESPONSE = "error_response"
    NOTIFICATION = "notification"
    # The server may ask the CLIENT something (approvals, elicitations). Nothing here
    # answers one: with the pinned settings none has been observed, and §7.5c measured
    # that a client denying every inbound server request changes nothing about what the
    # session loads — so there is no safety to buy by replying. The frame is surfaced
    # rather than swallowed, because an unanswered request the caller cannot see is a
    # hang with no explanation.
    SERVER_REQUEST = "server_request"
    UNPARSEABLE = "unparseable"


@dataclass(frozen=True)
class ServerFrame:
    """One line read off the server's stdout, classified but not interpreted.

    `raw` is kept for every frame including the unparseable ones. Nothing is dropped:
    R18's rule has a sibling on this side — nothing appended after the model's output,
    and nothing discarded before it reaches the component that owns the meaning.
    """

    raw: bytes
    message: Mapping[str, Any] | None
    kind: FrameKind
    method: str | None
    request_id: Any
    thread_id: str | None
    turn_id: str | None

    @property
    def params(self) -> Mapping[str, Any]:
        if self.message is None:
            return {}
        value = self.message.get("params")
        return value if isinstance(value, Mapping) else {}

    @property
    def agent_message_delta(self) -> str | None:
        """The model's answer text carried by this frame, or None.

        Only `item/agentMessage/delta` answers. Reasoning deltas are a different item
        stream and are NOT the answer; conflating them is why this discrimination lives
        in the module that owns the wire vocabulary.
        """
        if self.method != N_AGENT_MESSAGE_DELTA:
            return None
        value = self.params.get("delta")
        return value if isinstance(value, str) else None

    @property
    def is_reasoning_delta(self) -> bool:
        return self.method in REASONING_DELTA_METHODS

    @property
    def is_error_notification(self) -> bool:
        return self.method == N_ERROR

    @property
    def will_retry(self) -> bool | None:
        """§6.4 — reported, never collapsed.

        True: the server is retrying and the run is NOT over. False: terminal. None: this
        frame is not an `error` notification, or the field is missing. Read this field and
        not the payload around it — `codexErrorInfo` changes shape between the retrying
        and the terminal error, measured.
        """
        if not self.is_error_notification:
            return None
        value = self.params.get("willRetry")
        return value if isinstance(value, bool) else None

    @property
    def turn_status(self) -> str | None:
        """The status carried by `turn/started` / `turn/completed`.

        `turn/completed` is a STATUS and not a stream terminator (§4.1a). Reading this
        property is never permission to stop reading.
        """
        if self.method not in (N_TURN_STARTED, N_TURN_COMPLETED):
            return None
        turn = self.params.get("turn")
        if isinstance(turn, Mapping):
            status = turn.get("status")
            return status if isinstance(status, str) else None
        return None


@dataclass(frozen=True)
class OutboundRecord:
    """One line written to the server, kept as the exact bytes that went out."""

    method: str
    request_id: Any
    raw: bytes

    @property
    def params(self) -> Mapping[str, Any]:
        """Re-parsed from `raw`, so this can never disagree with what was sent."""
        return json.loads(self.raw.decode("utf-8")).get("params", {})


@dataclass
class TurnStartRecord:
    """A `turn/start` as it was actually sent, plus the turn the server opened for it.

    §7.2a-5 part 4 asserts against this and not against a builder's return value. On the
    recovery path the replacement turn appends a new record with its own `turn_id`, so a
    first-turn-only implementation is visible here rather than inferred.
    """

    outbound: OutboundRecord
    constrained: bool
    turn_id: str | None = None

    @property
    def raw(self) -> bytes:
        return self.outbound.raw

    @property
    def payload(self) -> Mapping[str, Any]:
        return self.outbound.params

    @property
    def prompt_text(self) -> str:
        """The single text input this turn carried, for positional assertions on it."""
        return self.payload["input"][0]["text"]


@dataclass(frozen=True)
class ThreadSession:
    """What `thread/start` or `thread/resume` opened, with the echo it came back with.

    `requested_model` and `reported_model` are SEPARATE fields and are never collapsed
    (§0.1). The server may answer with something other than what was asked for, and a
    single field cannot say so.

    `requested_model` is None when NO `model` key was sent — the design names no reviewer
    model, and the omission is how the reviewer CLI's own configuration selects the family.
    None here means "we asked for nothing", which is a different fact from "we asked and
    were told nothing", and the two must not be merged.

    `reported_model` IS NOT A VALIDITY CHECK, and this is measured rather than assumed.
    `thread/start` echoes back whatever `model` it was handed, valid or not: an unserveable
    id came back verbatim and the call still succeeded, failing only later at the turn with
    HTTP 404. So a populated `reported_model` proves the server RECORDED a value, never that
    a model exists to serve it. On the omission path it is the resolved configured value,
    which is the one case where it carries new information.
    """

    thread_id: str
    requested_model: str | None
    reported_model: str | None
    reported_cwd: str | None
    reported_sandbox: Any
    reported_approval_policy: Any
    reported_approvals_reviewer: Any
    instruction_sources: Any


@dataclass(frozen=True)
class OutputSchemaSupport:
    """R10's capability probe result. `supported` is TRI-STATE and None means undetermined.

    §8.2 R10: probe the capability and FAIL OPEN. Nothing in this module refuses a turn
    because of this value — `turn_start` accepts `output_schema=None` and sends an
    unconstrained turn, because failing closed is a total outage by another route.
    Deciding what to do with an undetermined answer belongs to the caller; sending the
    key anyway is harmless (unknown keys are swallowed silently, measured).
    """

    supported: bool | None
    detail: str


# --- executable resolution -------------------------------------------------------------------


def resolve_codex_executable(explicit: str | None = None) -> str:
    """Resolve the codex binary. Fails loudly; never returns a guessed path.

    Order: the explicit argument, then `$DISCRIMINATOR_CODEX`, then `codex` on PATH.

    A `.cmd` / `.js` shim is accepted but is NOT the server: the measured chain is
    `codex.cmd -> node -> codex.js -> spawn(codex.exe)`, so the child this module holds
    would be the shim and the real server a grandchild. `supervisor` owns the kill and it
    must be a PROCESS-TREE kill for that reason. Pointing this at the native executable
    removes the hop.
    """
    candidate = explicit or os.environ.get("DISCRIMINATOR_CODEX") or shutil.which("codex")
    if not candidate:
        raise ServerSpawnError(
            "codex executable unresolved: pass codex_path, set DISCRIMINATOR_CODEX, "
            "or put codex on PATH"
        )
    if not os.path.isfile(candidate):
        raise ServerSpawnError(f"codex executable does not exist: {candidate!r}")
    return os.path.abspath(candidate)


def probe_output_schema_support(codex_path: str | None = None) -> OutputSchemaSupport:
    """R10 — probe the server's own DECLARATION of `outputSchema`, and fail open.

    The original row probed an argparse declaration rather than a mention. The transplant
    is the same shape: `codex app-server generate-json-schema --out DIR` is the server
    describing its own wire, and `TurnStartParams` either declares `outputSchema` or does
    not. A mention in help text would not be evidence and is not consulted.

    This never raises and never blocks a run. It carries NO timeout of its own — C-34
    gives the one timeout to `supervisor`, and a second place a duration is decided is
    exactly what that constraint forbids.
    """
    try:
        exe = resolve_codex_executable(codex_path)
    except ServerSpawnError as exc:
        return OutputSchemaSupport(None, f"probe could not resolve codex: {exc}")

    out_dir = tempfile.mkdtemp(prefix="discriminator-schema-")
    try:
        completed = subprocess.run(
            [exe, "app-server", "generate-json-schema", "--out", out_dir],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if completed.returncode != 0:
            tail = completed.stderr.decode("utf-8", "replace").strip()[-300:]
            return OutputSchemaSupport(None, f"generate-json-schema rc={completed.returncode}: {tail}")
        params_path = os.path.join(out_dir, "v2", "TurnStartParams.json")
        if not os.path.isfile(params_path):
            return OutputSchemaSupport(None, f"declaration file absent: {params_path}")
        with open(params_path, "r", encoding="utf-8") as handle:
            declaration = json.load(handle)
        declared = "outputSchema" in declaration.get("properties", {})
        return OutputSchemaSupport(
            declared,
            "TurnStartParams declares outputSchema" if declared else
            "TurnStartParams does NOT declare outputSchema",
        )
    except Exception as exc:  # the probe is advisory; an unusable answer is not a failure
        return OutputSchemaSupport(None, f"probe raised {type(exc).__name__}: {exc}")
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


# --- the transport ------------------------------------------------------------------------------


_EOF = object()


class Transport:
    """One `codex app-server` process, and the methods we speak to it.

    Lifetime: constructed, `initialize()`d, then used. `process` is exposed because
    `supervisor` owns the lifecycle and the kill; this class never terminates the child
    and never applies a deadline to anything.
    """

    def __init__(
        self,
        session_cwd: str,
        codex_path: str | None = None,
        client_name: str = "discriminator",
        client_version: str = "1",
    ) -> None:
        """`session_cwd` is THE FENCE (§7.5a rule 1) and is set here and nowhere else.

        It must be an absolute path to an EXISTING, EMPTY directory. Empty is the
        checkable form of §4.1b's "clean directory": codex's project-scoped discovery —
        skills, hooks, project docs, repo `.codex/config.toml` and the MCP servers it can
        declare — is keyed on `cwd`, and a directory with nothing in it has nothing to
        discover. The reviewed repository is never this directory, on any call, and the
        reviewed artifact reaches the model as packet text only (§7.5a rule 3).
        """
        if not os.path.isabs(session_cwd):
            raise ServerSpawnError(f"session_cwd must be absolute: {session_cwd!r}")
        if not os.path.isdir(session_cwd):
            raise ServerSpawnError(f"session_cwd is not a directory: {session_cwd!r}")
        entries = os.listdir(session_cwd)
        if entries:
            raise ServerSpawnError(
                f"session_cwd must be empty (the fence is a clean directory): "
                f"{session_cwd!r} contains {sorted(entries)[:5]}"
            )

        self._session_cwd = os.path.abspath(session_cwd)
        self.codex_path = resolve_codex_executable(codex_path)
        self.client_name = client_name
        self.client_version = client_version

        self.outbound: list[OutboundRecord] = []
        self.turn_starts: list[TurnStartRecord] = []
        self.stderr_tail: deque[str] = deque(maxlen=STDERR_TAIL_LINES)

        self._inbox: queue.Queue[Any] = queue.Queue()
        self._backlog: deque[ServerFrame] = deque()
        self._id_lock = threading.Lock()
        self._next_id = 0
        self._eof_seen = False
        self._server_info: Mapping[str, Any] | None = None
        self._client_closed = False
        self._initialized = False

        try:
            self.process = subprocess.Popen(
                # `--disable memories` IS A REVIEW-INTEGRITY FLAG, NOT A PREFERENCE, AND IT
                # MUST NOT BE DROPPED. Without it the server prepends a `## Memory` block as
                # developer item [0] -- ahead of our own instructions -- telling the reviewer
                # to read notes distilled from PREVIOUS reviews, and it writes this review
                # back into that same store. A review of one artifact then carries
                # conclusions drawn about an unrelated one, which is precisely the
                # cross-contamination the clean session directory and the pinned session
                # settings exist to prevent.
                #
                # WHAT IS MEASURED, STATED AT ITS OWN GRADE rather than rounded up. The
                # INBOUND half is driven: with this flag a real review carries no
                # `## Memory` item and no `memories/` reference, against a rollout corpus
                # where every run without it does. The OUTBOUND half -- that the finished
                # review is no longer written back -- is CONSISTENT WITH but not proven
                # by that run: ingestion is asynchronous, and the observation window was
                # minutes. Per-thread reconfiguration was NOT probed, so treat this as a
                # process-level disable that held on the normal path, not as a proof that
                # no per-thread setting could ever override it.
                #
                # WHY THE FLAG AND NOT `-c features.memories=false`, WHICH IS EQUIVALENT ON
                # PAPER: an unknown `-c` key is accepted at exit 0, so an upstream rename of
                # the feature would reopen the channel with nothing anywhere reporting it.
                # `--disable` refuses an unknown feature name outright -- measured, the
                # server prints `Unknown feature flag` and does not start. A defence that
                # fails loudly is the only kind that survives an upstream rename.
                #
                # THIS DEFENCE HAS ALREADY DIED ONCE, AND SILENTLY. It previously lived as a
                # command-line string inside a wrapper script; when that wrapper was retired
                # the flag went with it, no test named it, and nothing failed. If you are
                # removing this argument, the burden is a measurement showing the memory item
                # is absent without it -- not a reading of the config file, which enables the
                # feature and is not ours to change.
                [self.codex_path, "app-server", "--disable", "memories"],
                cwd=self._session_cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                # THE CHILD MUST BE ITS OWN PROCESS-GROUP LEADER, AND THAT IS A CONTAINMENT
                # REQUIREMENT RATHER THAN A PREFERENCE. `supervisor.kill_process_tree` kills a
                # GROUP on POSIX. Without this flag the child inherits OUR group, so the one
                # timeout expiring would SIGKILL the supervisor, its shell and its siblings —
                # uncatchably, and with the kill reported as having succeeded. Containment is
                # established here, at the spawn, because it cannot be established at the kill.
                #
                # NO PLATFORM GUARD, and the absence is measured rather than assumed: CPython
                # 3.12.3's Windows `_execute_child` names this parameter
                # `unused_start_new_session` and accepts it (driven, rc=0), while the control
                # `preexec_fn` RAISES `ValueError` on the same box. A `sys.platform` branch here
                # would be a named thing added to the path the timeout runs on, for a tolerance
                # that was tested.
                start_new_session=True,
            )
        except OSError as exc:
            raise ServerSpawnError(f"could not spawn {self.codex_path!r}: {exc}") from exc

        threading.Thread(target=self._pump_stdout, daemon=True).start()
        threading.Thread(target=self._pump_stderr, daemon=True).start()

    # -- the fence, in one place ---------------------------------------------------------

    @property
    def session_cwd(self) -> str:
        """The one value every outbound payload's `cwd` comes from (§7.5c).

        There is no setter and no per-call `cwd` argument anywhere in this class. That
        absence IS the enforcement: `cwd` is settable on `thread/start`, on `turn/start`
        and via `skills/extraRoots/set`, which is three places one mistake reopens
        everything, and nothing on the server side prevents it.
        """
        return self._session_cwd

    def _check_echo(self, method: str, result: Mapping[str, Any]) -> None:
        """§7.5a rule 5 — the server accepting a key is worth nothing; check the echo.

        ALL pinned settings are checked, and a MISSING echo is as fatal as a wrong one:
        §7.5c measured that nothing on the server enforces the fence, so this function IS the
        fence rather than a double check on one. An absent key says the server did not record
        the restriction, which is the same fact as recording the wrong one.
        """
        echoed_cwd = result.get("cwd")
        if isinstance(echoed_cwd, str):
            wanted = os.path.normcase(os.path.abspath(self._session_cwd))
            got = os.path.normcase(os.path.abspath(echoed_cwd))
            if wanted != got:
                raise FenceViolation(f"{method}: cwd echo {echoed_cwd!r} != fence {self._session_cwd!r}")
        else:
            raise FenceViolation(f"{method}: no cwd echoed; the fence is unconfirmable")

        reviewer = result.get("approvalsReviewer")
        if reviewer != APPROVALS_REVIEWER:
            raise FenceViolation(
                f"{method}: approvalsReviewer echo {reviewer!r} != {APPROVALS_REVIEWER!r}"
            )

        # The sandbox is the setting that actually RESTRICTS (§7.5b: `approvalPolicy` only
        # suppresses the prompt), so it is checked in both spellings it can legitimately come
        # back in — the resolved object, or the sent token verbatim. Anything else, including
        # the object with `networkAccess` reopened, is a restriction we did not get.
        sandbox = result.get("sandbox")
        confirmed = sandbox == SANDBOX or (
            isinstance(sandbox, Mapping)
            and all(sandbox.get(key) == value for key, value in SANDBOX_RESOLVED_ECHO.items())
        )
        if not confirmed:
            raise FenceViolation(
                f"{method}: sandbox echo {sandbox!r} confirms neither {SANDBOX!r} nor "
                f"{SANDBOX_RESOLVED_ECHO!r}; the restriction is unconfirmable"
            )

        policy = result.get("approvalPolicy")
        if policy != APPROVAL_POLICY:
            raise FenceViolation(
                f"{method}: approvalPolicy echo {policy!r} != {APPROVAL_POLICY!r}"
            )

    # -- framing -------------------------------------------------------------------------

    def _pump_stdout(self) -> None:
        try:
            for line in self.process.stdout:  # type: ignore[union-attr]
                self._inbox.put(_classify(line))
        finally:
            self._inbox.put(_EOF)

    def _pump_stderr(self) -> None:
        try:
            for line in self.process.stderr:  # type: ignore[union-attr]
                self.stderr_tail.append(line.decode("utf-8", "replace").rstrip("\r\n"))
        except Exception:
            self.stderr_tail.append("<stderr reader ended>")

    def _died(self, context: str) -> ServerDied:
        self._eof_seen = True
        return ServerDied(
            f"{context}: stream ended with no sentinel (silent EOF)",
            self.process.poll(),
            self.stderr_tail,
        )

    def _send(self, method: str, params: Mapping[str, Any], request_id: Any) -> OutboundRecord:
        """Write one NDJSON line, LF-terminated, and record the exact bytes."""
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params}
        if request_id is not None:
            message["id"] = request_id
        raw = (json.dumps(message) + "\n").encode("utf-8")
        record = OutboundRecord(method=method, request_id=request_id, raw=raw)
        self.outbound.append(record)
        try:
            # THE PIPE IS UNBUFFERED, SO THIS IS A RAW WRITE AND THE COUNT IS THE ONLY REPORT
            # OF WHAT IT TOOK. A raw write is permitted to accept fewer bytes than it was
            # handed; one call whose return value is discarded therefore truncates the packet
            # on the wire while the OutboundRecord just appended still claims the whole request
            # went out — a review corrupted at the source with the evidence saying otherwise.
            # None IS NOT A COUNT. A raw stream answers it for "wrote nothing, not an error",
            # so it is retried and never advances the offset; folding it to an offset of 0
            # re-slices the SAME remaining bytes and hands them over again for as long as the
            # writer keeps answering None, which is an unbounded spin rather than a wait. The
            # tolerance for that is in ATTEMPTS and never in seconds — C-34 gives the one
            # timeout to `supervisor`, so a sleep or a backoff here would be a second place a
            # duration is decided. The loop holds no state between runs and ends when the bytes
            # are gone, when the pipe raises (§4.1a's silent EOF below), or when the tolerance
            # is spent and the undelivered frame is named instead of re-offered forever.
            view = memoryview(raw)
            stalled = 0
            while view:
                written = self.process.stdin.write(view)  # type: ignore[union-attr]
                if written is None or written <= 0:
                    stalled += 1
                    if stalled > ZERO_PROGRESS_WRITES_ALLOWED:
                        raise WriteStalled(
                            f"write of {method}: the stream took no bytes on {stalled} "
                            f"consecutive attempts; {len(view)} of {len(raw)} bytes never "
                            f"went out"
                        )
                    continue
                view = view[written:]
                stalled = 0
            self.process.stdin.flush()  # type: ignore[union-attr]
        except (OSError, ValueError) as exc:
            # Measured on Windows: a write to dead stdin raises OSError EINVAL, not
            # BrokenPipeError. Both mean the same thing and both are the silent EOF.
            raise self._died(f"write of {method} failed ({exc})") from exc
        return record

    def _request(self, method: str, params: Mapping[str, Any]) -> tuple[int, OutboundRecord]:
        """Send a request and return its id plus the record of the bytes that went out."""
        with self._id_lock:
            self._next_id += 1
            request_id = self._next_id
        return request_id, self._send(method, params, request_id)

    def _await(self, request_id: int, method: str) -> Mapping[str, Any]:
        """Wait for one request's answer, keeping every frame seen on the way.

        Notifications and unrelated frames that arrive while waiting are pushed to the
        backlog, which `frames()` drains first. Nothing read here is discarded — a delta
        that arrived before the response is still part of the model's answer.
        """
        while True:
            item = self._inbox.get()
            if item is _EOF:
                raise self._died(f"waiting for the {method} response")
            frame: ServerFrame = item
            if frame.request_id == request_id and frame.kind in (
                FrameKind.RESPONSE,
                FrameKind.ERROR_RESPONSE,
            ):
                if frame.kind is FrameKind.ERROR_RESPONSE:
                    error = (frame.message or {}).get("error", {})
                    raise ProtocolError(method, error.get("code"), str(error.get("message")))
                result = (frame.message or {}).get("result")
                return result if isinstance(result, Mapping) else {}
            self._backlog.append(frame)

    def _call(self, method: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        request_id, _ = self._request(method, params)
        return self._await(request_id, method)

    def frames(self) -> Iterator[ServerFrame]:
        """Yield every frame until EOF. Runs past `turn/completed` by construction.

        §4.1a's drain rule: `turn/completed` marks the STATUS, never the END OF THE
        STREAM — measured interrupt trials delivered further deltas after it with the same
        `turnId`, on some runs and not others, which is a race rather than a rule and the
        worst shape for it to have. So this generator has no terminal condition other than
        the stream ending, and the caller decides when it has enough. Attribute late frames
        by `turn_id`.

        On an EOF the client did not ask for, this raises `ServerDied`. After `close()`
        it simply ends, because that EOF is the one we requested.
        """
        while True:
            if self._backlog:
                yield self._backlog.popleft()
                continue
            item = self._inbox.get()
            if item is _EOF:
                self._eof_seen = True
                if self._client_closed:
                    return
                raise self._died("reading the frame stream")
            yield item

    @property
    def eof_seen(self) -> bool:
        return self._eof_seen

    def is_alive(self) -> bool:
        """Is the `codex app-server` child still running, right now?

        §6.1 forbids inferring death from silence, so this is the OUT-OF-BAND confirmation
        `ServerDied`'s docstring refers to. It is a property of the process this module
        spawned, which is why it lives here and not in `supervisor`: the supervisor holds no
        handle and cannot answer it without one.

        Distinct from `eof_seen`, and the two must not be substituted for one another.
        `eof_seen` is TERMINAL and about the STREAM — once true it stays true. This is
        INSTANTANEOUS and about the PROCESS. A child can be alive with EOF seen (it closed
        stdout and is still exiting) and can be dead with no EOF seen (killed between frames),
        so neither implies the other in either direction.
        """
        return self.process.poll() is None

    @property
    def pid(self) -> int:
        """The `codex app-server` child's process id.

        `supervisor` needs it for the ONE kill at the ONE timeout (C-34) —
        `kill_process_tree` takes a pid and containment is anchored at the engine, not at
        the wrapper. This module spawned the child, so it is the only component that can
        answer; exposing the id rather than the handle keeps process management here.
        """
        return self.process.pid

    @property
    def server_info(self) -> Mapping[str, Any]:
        """`initialize`'s result — `userAgent`, `codexHome`, `platformFamily`, `platformOs`.

        Recorded on the run record as the transport's REPORTED identity, which §0.1 keeps
        separate from the requested one and never collapses into it.

        Raises if `initialize` has not run. §4.1a makes the handshake mandatory before
        `thread/start`, so reading this earlier is a caller sequencing error, and returning
        an empty mapping would put a silent blank where a reported identity belongs.
        """
        if self._server_info is None:
            raise TransportError(
                "transport: server_info read before initialize(). §4.1a makes the handshake "
                "mandatory before any thread call; there is no reported identity yet."
            )
        return self._server_info

    # -- the methods ---------------------------------------------------------------------

    def initialize(self) -> Mapping[str, Any]:
        """MANDATORY before `thread/start` (§4.1a, measured both halves).

        The result carries `userAgent`, `codexHome`, `platformFamily`, `platformOs` and
        nothing versioned: there is NO capability negotiation on this interface, so
        nothing here can announce a protocol change. §4.1's small surface is the whole
        mitigation.
        """
        params = {
            "clientInfo": {
                "name": self.client_name,
                "title": self.client_name,
                "version": self.client_version,
            }
        }
        result = self._call(M_INITIALIZE, params)
        self._initialized = True
        self._server_info = result
        return result

    def thread_start(
        self,
        model: str | None = None,
        base_instructions: str | None = None,
        developer_instructions: str | None = None,
        config: Mapping[str, Any] | None = None,
    ) -> ThreadSession:
        """Open the reviewer session. `ephemeral` is never set (R21).

        `model` DEFAULTS TO None AND THE DEFAULT IS THE DESIGNED PATH, not a convenience.
        The signed design names no reviewer model; passing None omits the key entirely and
        the reviewer CLI's own configuration selects the family, which is how the
        cross-family guarantee is enforced structurally rather than by a string somebody
        has to keep true. See `_session_params` for what was measured.

        `ephemeral` is not omitted by accident and not left to a caller: it destroys the
        rollout that recovery depends on, and this method has no parameter for it. On
        `thread/resume` the field does not exist at all, so the rule is structurally
        unreachable there (§5.3).
        """
        if not self._initialized:
            raise TransportError("thread/start before initialize (§4.1a: it is mandatory)")
        params = self._session_params(model, base_instructions, developer_instructions, config)
        result = self._call(M_THREAD_START, params)
        return self._session_from(M_THREAD_START, result, model)

    def turn_start(
        self,
        thread_id: str,
        prompt_text: str,
        output_schema: Mapping[str, Any] | None,
        effort: str | None = None,
        summary: str | None = None,
    ) -> tuple[str, TurnStartRecord]:
        """Send the packet and constrain the answer. Returns the turn id and its record.

        `output_schema` is REQUIRED as an argument and may be None. None is R10's
        degraded path: the turn goes unconstrained and the review still happens. There is
        no branch here that refuses a turn for want of a schema — failing closed would be
        a total outage by another route, and relaxing the verdict parse is a different
        thing that `envelope` owns and does not do.

        The schema object is forwarded exactly as given. `envelope` owns the ONE
        definition (§3.1); this module never copies, defaults or edits it.
        """
        params: dict[str, Any] = {
            "threadId": thread_id,
            "input": [{"type": "text", "text": prompt_text}],
            "cwd": self._session_cwd,
        }
        if output_schema is not None:
            params["outputSchema"] = output_schema
        if effort is not None:
            params["effort"] = effort
        if summary is not None:
            params["summary"] = summary

        # The record is appended BEFORE the answer is awaited, and deliberately. §7.2a-5
        # part 4 inspects the payload the turn ACTUALLY SENT — and a turn that was sent
        # and then failed is precisely the case it exists to catch. A record appended only
        # on success would be missing exactly when it is wanted, leaving `turn_id` as the
        # honest None rather than the row absent.
        request_id, outbound = self._request(M_TURN_START, params)
        record = TurnStartRecord(outbound=outbound, constrained=output_schema is not None)
        self.turn_starts.append(record)

        result = self._await(request_id, M_TURN_START)
        turn = result.get("turn")
        if not isinstance(turn, Mapping) or not isinstance(turn.get("id"), str):
            raise ProtocolError(M_TURN_START, None, f"no turn.id in result: {result!r}")
        record.turn_id = turn["id"]
        return record.turn_id, record

    def turn_interrupt(self, thread_id: str, turn_id: str) -> Mapping[str, Any]:
        """The clean in-band cancel. Returns almost immediately and does NOT wait.

        Measured: it answers `{}` in ~0 s without the turn having stopped, which is the
        other half of why the drain rule exists. Keep reading after calling this.
        """
        result = self._call(M_TURN_INTERRUPT, {"threadId": thread_id, "turnId": turn_id})
        return result

    def thread_resume(
        self,
        thread_id: str,
        model: str | None = None,
        base_instructions: str | None = None,
        developer_instructions: str | None = None,
        config: Mapping[str, Any] | None = None,
    ) -> ThreadSession:
        """Reopen a thread, or rejoin an in-flight turn — the two are the same call.

        §5.4a: with the server ALIVE this rejoins the in-flight turn, same turn id,
        deltas keep flowing. Only when the server DIED is the interrupted turn terminal,
        and then a fresh `turn/start` is required and gets a new turn id. This method
        does not decide which happened; `supervisor` reads the returned thread state.

        Every setting is a named field of resume, so they are re-sent rather than
        remembered (§5.3) — an omitted setting would be a bug we wrote, not a hazard the
        protocol imposes.
        """
        if not self._initialized:
            raise TransportError("thread/resume before initialize (§4.1a: it is mandatory)")
        params = self._session_params(model, base_instructions, developer_instructions, config)
        params["threadId"] = thread_id
        result = self._call(M_THREAD_RESUME, params)
        return self._session_from(M_THREAD_RESUME, result, model)

    # -- payload construction ------------------------------------------------------------

    def _session_params(
        self,
        model: str | None,
        base_instructions: str | None,
        developer_instructions: str | None,
        config: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """The settings block `thread/start` and `thread/resume` share.

        Start and resume use the same pinned settings. A None model omits the
        key, allowing the reviewer's CLI configuration to select it; neither JSON
        null nor a guessed default is sent. Acceptance of thread/start alone is
        not family qualification: the completed route metadata is checked later.
        """
        merged = dict(config or {})
        merged.update(DEFENCE_IN_DEPTH_CONFIG)
        params: dict[str, Any] = {
            "cwd": self._session_cwd,
            "sandbox": SANDBOX,
            "approvalPolicy": APPROVAL_POLICY,
            "approvalsReviewer": APPROVALS_REVIEWER,
            "baseInstructions": base_instructions,
            "developerInstructions": developer_instructions,
            "config": merged,
        }
        if model is not None:
            params["model"] = model
        return params

    def _session_from(
        self, method: str, result: Mapping[str, Any], requested_model: str | None
    ) -> ThreadSession:
        thread = result.get("thread")
        # The id nests as `thread.id`. A check that reads a top-level `threadId` returns a
        # clean-looking negative, which is how this very fact was nearly missed (§4.1a).
        if not isinstance(thread, Mapping) or not isinstance(thread.get("id"), str):
            raise ProtocolError(method, None, f"no thread.id in result: {result!r}")
        self._check_echo(method, result)
        return ThreadSession(
            thread_id=thread["id"],
            requested_model=requested_model,
            reported_model=result.get("model"),
            reported_cwd=result.get("cwd"),
            reported_sandbox=result.get("sandbox"),
            reported_approval_policy=result.get("approvalPolicy"),
            reported_approvals_reviewer=result.get("approvalsReviewer"),
            instruction_sources=result.get("instructionSources"),
        )

    # -- shutdown ------------------------------------------------------------------------

    def close(self) -> None:
        """Close stdin. The child then exits on its own.

        This is not the kill. `supervisor` owns termination and owns the process tree —
        see `resolve_codex_executable` on why a shim makes the real server a grandchild.
        """
        self._client_closed = True
        try:
            if self.process.stdin is not None:
                self.process.stdin.close()
        except OSError:
            pass


def _classify(line: bytes) -> ServerFrame:
    """Classify one stdout line. Never validates for `jsonrpc` — responses omit it."""
    try:
        message = json.loads(line.decode("utf-8"))
    except Exception:
        return ServerFrame(line, None, FrameKind.UNPARSEABLE, None, None, None, None)
    if not isinstance(message, dict):
        return ServerFrame(line, None, FrameKind.UNPARSEABLE, None, None, None, None)

    method = message.get("method")
    has_id = "id" in message
    if has_id and method is not None:
        kind = FrameKind.SERVER_REQUEST
    elif "error" in message and has_id:
        kind = FrameKind.ERROR_RESPONSE
    elif has_id:
        kind = FrameKind.RESPONSE
    else:
        kind = FrameKind.NOTIFICATION

    params = message.get("params")
    params = params if isinstance(params, dict) else {}
    thread_id = params.get("threadId")
    turn_id = params.get("turnId")
    return ServerFrame(
        raw=line,
        message=message,
        kind=kind,
        method=method if isinstance(method, str) else None,
        request_id=message.get("id"),
        thread_id=thread_id if isinstance(thread_id, str) else None,
        turn_id=turn_id if isinstance(turn_id, str) else None,
    )
