"""Deterministic, project-local orchestration over finite native JSONL adapters.

Only this thread owns SQLite. Adapter reader threads collect bytes; a binding ACK
is sent here only after Store.bind_attempt has committed. Domain rules live in
registered, source-bound handlers, never in native replies or this module.
"""


from __future__ import annotations




from collections.abc import Mapping


from dataclasses import dataclass, field, fields, is_dataclass, replace


from hashlib import sha256


import inspect


import json


import os


from pathlib import Path


import queue


import platform


import re


import sys


import stat


import subprocess


import threading


import time


import tempfile


from types import MappingProxyType


from typing import Any, Callable


import uuid


from .contracts import (InputRef, TaskSpec, result_from_dict, result_to_dict,
                        semantic_hash, task_from_dict,
                        task_result_schema, task_to_dict, validate_result)


from .store import Store, StoreError


from .publication import PublicationManager, _PROJECTION_FIELDS, validate_byte_projection


class ControllerError(RuntimeError):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code, self.details = code, details or {}


class NativeLaunchUnavailable(ControllerError):
    """Only an observed Popen failure before an adapter process exists."""
    def __init__(self, message):
        super().__init__("NATIVE_PROCESS_UNAVAILABLE", message, {"pre_execution": True})


def json_value(value):
    """Portable JSON projection; no repr/stringification of unknown objects."""
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return {f.name: json_value(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_value(v) for v in value]
    if value is None or type(value) in (str, int, float, bool):
        return value
    raise ControllerError("NON_JSON_VALUE", "Runtime metadata must be JSON-safe", {"type": type(value).__name__})


def _json(value):
    return json.dumps(json_value(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(v) for v in value)
    return value


def _error(exc):
    return {"code": getattr(exc, "code", type(exc).__name__), "message": str(exc),
            "details": json_value(getattr(exc, "details", {}))}


def _path(path, *, root=None, regular=False):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise ControllerError("UNSAFE_PATH", "Absolute non-traversing paths required")
    for part in (*path.parents, path):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise ControllerError("UNSAFE_PATH", "Symlink/junction paths are not allowed", {"path": str(part)})
    if root is not None and not path.is_relative_to(Path(root)):
        raise ControllerError("PATH_ESCAPE", "Artifact escapes its owned candidate directory")
    if regular and (not path.exists() or not stat.S_ISREG(path.stat().st_mode)):
        raise ControllerError("INPUT_UNAVAILABLE", "Expected an actual regular file", {"path": str(path)})
    return path


def _digest(path):
    with _path(path, regular=True).open("rb") as handle:
        return _stream_digest(handle)


def _stream_digest(handle):
    digest = sha256()
    while chunk := handle.read(1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


def _snapshot_partial_io_path(path):
    """Render private IO only; never change a retained path or target identity."""
    path = Path(path)
    match = re.fullmatch(r"([0-9a-f]{64})\.partial-[0-9a-f]{32}", path.name)
    if match is None or path.parent.name not in ("snapshots", "native-snapshots"):
        raise ControllerError("UNSAFE_PATH", "Exact content-addressed snapshot partial required", {"path": str(path)})
    _path(path.parent)
    _path(path.with_name(match.group(1)))
    spelling = str(path)
    if os.name == "nt" and not spelling.startswith(("\\\\?\\", "\\\\.\\")):
        return Path("\\\\?\\UNC\\" + spelling[2:] if spelling.startswith("\\\\") else "\\\\?\\" + spelling)
    return path


def _snapshot_io_error(error, path, io_path):
    for name in ("filename", "filename2"):
        if getattr(error, name, None) == str(io_path):
            setattr(error, name, str(path))


def _publish_snapshot(source, target, expected):
    """Flush a private sibling before exclusive publication; retain failed bytes."""
    source, target = _path(source, regular=True), _path(target)
    if target.exists():
        if _digest(source) != expected:
            raise ControllerError("INPUT_CHANGED", "Snapshot source changed")
        if _digest(target) != expected:
            raise ControllerError("SNAPSHOT_CHANGED", "Content-addressed snapshot was changed")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".partial-" + uuid.uuid4().hex)
    partial_io = _snapshot_partial_io_path(partial)
    digest = sha256()
    try:
        with source.open("rb") as incoming, partial_io.open("xb") as outgoing:
            while chunk := incoming.read(1024 * 1024):
                if outgoing.write(chunk) != len(chunk):
                    raise OSError("Short snapshot write")
                digest.update(chunk)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        if digest.hexdigest() != expected:
            raise ControllerError("INPUT_CHANGED", "Input changed while snapshots were retained")
        try:
            os.link(partial_io, target, follow_symlinks=False)
        except FileExistsError:
            if _digest(target) != expected:
                raise ControllerError("SNAPSHOT_CHANGED", "Concurrent snapshot publication differs")
        PublicationManager._sync_directory(target.parent)
        partial_io.unlink()
        PublicationManager._sync_directory(target.parent)
    except OSError as exc:
        _snapshot_io_error(exc, partial, partial_io)
        raise


def _durable(path, value):
    path = _path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (_json(value) + "\n").encode("utf-8")
    if path.exists():
        if path.read_bytes() != content:
            raise ControllerError("SNAPSHOT_CONFLICT", "Immutable snapshot differs", {"path": str(path)})
        return
    partial = path.with_name(path.name + ".partial-" + uuid.uuid4().hex)
    with partial.open("xb") as handle:
        if handle.write(content) != len(content):
            raise OSError("Short immutable record write")
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.link(partial, path, follow_symlinks=False)
    except FileExistsError:
        if _path(path, regular=True).read_bytes() != content:
            raise ControllerError("SNAPSHOT_CONFLICT", "Concurrent immutable snapshot differs", {"path": str(path)})
    PublicationManager._sync_directory(path.parent)
    partial.unlink()
    PublicationManager._sync_directory(path.parent)


@dataclass(frozen=True)
class RunRequest:
    schema_version: int
    workflow: str
    project_root: Path
    arguments: tuple[str, ...]
    caller_host: str
    caller_family: str
    backend: str
    profile: str | None

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ControllerError("INVALID_REQUEST", "schema_version must be integer 1")
        if not isinstance(self.workflow, str) or not self.workflow or "\0" in self.workflow:
            raise ControllerError("INVALID_REQUEST", "A workflow identity is required")
        object.__setattr__(self, "project_root", _path(self.project_root))
        if not isinstance(self.arguments, (tuple, list)) or any(type(a) is not str or "\0" in a for a in self.arguments):
            raise ControllerError("INVALID_REQUEST", "arguments must be argv strings")
        object.__setattr__(self, "arguments", tuple(self.arguments))
        if self.caller_host not in ("omp", "codex", "claude") or self.caller_family not in ("gpt", "claude"):
            raise ControllerError("INVALID_REQUEST", "Explicit native caller identity required")
        if self.backend not in ("auto", "omp", "codex", "claude"):
            raise ControllerError("INVALID_REQUEST", "Unknown execution backend")
        if self.profile is not None:
            raise ControllerError("UNKNOWN_PROFILE", "No execution profiles are installed; semantic overrides are forbidden")

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) != {f.name for f in fields(cls)}:
            raise ControllerError("INVALID_REQUEST", "Closed run-request object required")
        return cls(**value)


@dataclass(frozen=True)
class StepSpec:
    step_id: str
    dependencies: tuple[str, ...]
    kind: str
    prepare_handler: Callable
    fanout_source: str | None = None
    concurrency: int = 1
    policy: Mapping = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.step_id, str) or not self.step_id or "\0" in self.step_id:
            raise ControllerError("INVALID_GRAPH", "Step identity is required")
        if self.kind not in ("local", "task", "review", "decision", "publish") or not callable(self.prepare_handler):
            raise ControllerError("INVALID_GRAPH", "A source-bound step handler and known kind are required")
        if type(self.concurrency) is not int or self.concurrency < 1:
            raise ControllerError("INVALID_GRAPH", "Positive step concurrency required")
        if len(set(self.dependencies)) != len(self.dependencies):
            raise ControllerError("INVALID_GRAPH", "Duplicate dependencies")
        if any(type(value) is not str or not value or "\0" in value for value in self.dependencies):
            raise ControllerError("INVALID_GRAPH", "Dependency identities must be nonempty strings")
        if self.fanout_source is not None and (type(self.fanout_source) is not str or "." not in self.fanout_source):
            raise ControllerError("INVALID_GRAPH", "Fanout source must name a predecessor data key")
        object.__setattr__(self, "dependencies", tuple(self.dependencies))
        object.__setattr__(self, "policy", _freeze(self.policy))
        _json(self.policy)


@dataclass(frozen=True)
class WorkflowSpec:
    workflow_id: str
    steps: tuple[StepSpec, ...]
    source_paths: tuple[Path, ...] = ()
    metadata: Mapping = field(default_factory=dict)
    effect_handler: Callable | None = None
    publication_guard: Callable | None = None

    def __post_init__(self):
        seen = set()
        for step in self.steps:
            if step.step_id in seen or not set(step.dependencies) <= seen:
                raise ControllerError("INVALID_GRAPH", "Steps must be unique and dependency-ordered")
            seen.add(step.step_id)
        if not self.steps:
            raise ControllerError("INVALID_GRAPH", "A workflow must declare actual work")
        object.__setattr__(self, "steps", tuple(self.steps))
        object.__setattr__(self, "source_paths", tuple(Path(p) for p in self.source_paths))
        if type(self.metadata.get("max_concurrency", 4)) is not int or self.metadata.get("max_concurrency", 4) < 1:
            raise ControllerError("INVALID_GRAPH", "Positive workflow concurrency required")
        if self.effect_handler is not None and not callable(self.effect_handler):
            raise ControllerError("INVALID_GRAPH", "External effects require a source-bound callable")
        if self.publication_guard is not None and not callable(self.publication_guard):
            raise ControllerError("INVALID_GRAPH", "Publication guard requires a source-bound callable")
        object.__setattr__(self, "metadata", _freeze(self.metadata))
        _json(self.metadata)


@dataclass(frozen=True)
class StepContext:
    request: RunRequest
    run_id: str
    step: StepSpec
    item_id: str
    ordinal: int
    counters: Mapping
    predecessors: tuple[Mapping, ...]
    paths: Mapping
    data: Mapping = field(default_factory=dict)

    def __post_init__(self):
        for name in ("counters", "paths", "data"):
            object.__setattr__(self, name, _freeze(getattr(self, name)))
        object.__setattr__(self, "predecessors", tuple(_freeze(v) for v in self.predecessors))

    @property
    def step_id(self):
        return self.step.step_id

    @property
    def attempt_id(self):
        return self.paths["attempt_id"]

    @property
    def candidate_dir(self):
        return Path(self.paths["candidate_dir"])

    @property
    def skills_root(self):
        return Path(self.paths["skills_root"])


@dataclass(frozen=True)
class PreparedStep:
    status: str
    tasks: tuple[TaskSpec, ...] = ()
    skip_reason: str | None = None
    decision: Mapping | None = None
    local_result: Mapping | None = None

    def __post_init__(self):
        if self.status not in ("ready", "skip", "blocked", "local_result"):
            raise ControllerError("INVALID_PREPARATION", "Unknown prepared step tag")
        if self.status != "ready" and self.tasks:
            raise ControllerError("INVALID_PREPARATION", "Only ready preparation may contain tasks")
        if self.status == "skip" and not self.skip_reason:
            raise ControllerError("INVALID_PREPARATION", "Skipped work requires a reason")
        if self.status == "local_result" and self.local_result is None:
            raise ControllerError("INVALID_PREPARATION", "Missing local result")
        object.__setattr__(self, "tasks", tuple(self.tasks))
        object.__setattr__(self, "decision", _freeze(self.decision))
        object.__setattr__(self, "local_result", _freeze(self.local_result))


@dataclass(frozen=True)
class RunOutcome:
    schema_version: int = 1
    run_id: str = ""
    status: str = "failed"
    outputs: tuple[Mapping, ...] = ()
    decision: Mapping | None = None
    error: Mapping | None = None
    def __post_init__(self):
        object.__setattr__(self, "outputs", tuple(_freeze(v) for v in self.outputs))
        object.__setattr__(self, "decision", _freeze(self.decision))
        object.__setattr__(self, "error", _freeze(self.error))


    def to_dict(self):
        return json_value(self)


class NativeIPC:
    """One finite adapter, byte-only reader threads, strict correlated framing."""
    def __init__(self, command, context_path, evidence_root, run_id, attempt_id, owner_epoch):
        self.run_id, self.attempt_id, self.owner_epoch = run_id, attempt_id, owner_epoch
        self.evidence_root = Path(evidence_root)
        self.frames, self.stderr = queue.Queue(), []
        self.requests, self.sequence = {}, 0
        try:
            self.process = subprocess.Popen([*map(str, command), "--context", str(context_path)],
                                            cwd=str(self.evidence_root), stdin=subprocess.PIPE,
                                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                            text=True, encoding="utf-8", errors="strict", bufsize=1)
        except FileNotFoundError as exc:
            raise NativeLaunchUnavailable(str(exc)) from exc
        def collect(stream, errors=False):
            try:
                for line in stream:
                    if errors:
                        self.stderr.append(line)
                    else:
                        self.frames.put(line)
            except Exception as exc:
                self.frames.put(exc)
            finally:
                if not errors:
                    self.frames.put(EOFError("Adapter stdout closed"))
        self.reader = threading.Thread(target=collect, args=(self.process.stdout,), daemon=True)
        self.errors = threading.Thread(target=collect, args=(self.process.stderr, True), daemon=True)
        self.reader.start()
        self.errors.start()

    def _retain(self, direction, frame):
        self.sequence += 1
        _durable(self.evidence_root / f"frame-{self.sequence:06d}-{direction}.json", frame)

    def send(self, operation, payload, *, request_id=None):
        request_id = request_id or str(uuid.uuid4())
        frame = {"request_id": request_id, "run_id": self.run_id, "attempt_id": self.attempt_id,
                 "owner_epoch": self.owner_epoch, "operation": operation, "payload": json_value(payload)}
        if operation != "binding_ack":
            self.requests[request_id] = frame
        elif request_id not in self.requests:
            raise ControllerError("IPC_CORRELATION", "Binding ACK has no originating request")
        self._retain("request", frame)
        try:
            self.process.stdin.write(_json(frame) + "\n")
            self.process.stdin.flush()
        except (OSError, ValueError) as exc:
            raise ControllerError("NATIVE_CHANNEL_LOST", "Submission outcome requires reconciliation") from exc
        return request_id

    def poll(self, timeout=0):
        try:
            line = self.frames.get(timeout=timeout)
        except queue.Empty:
            return None
        if isinstance(line, BaseException):
            raise ControllerError("NATIVE_CHANNEL_LOST", str(line))
        self._retain("received", {"line": line})
        try:
            frame = json.loads(line)
        except (ValueError, TypeError) as exc:
            raise ControllerError("IPC_MALFORMED", "Adapter stdout is not a JSON frame") from exc
        names = {"request_id", "run_id", "attempt_id", "owner_epoch", "status", "payload", "error"}
        if not isinstance(frame, dict) or set(frame) != names or frame["request_id"] not in self.requests:
            raise ControllerError("IPC_CORRELATION", "Unknown or malformed adapter reply")
        for key in ("run_id", "attempt_id", "owner_epoch"):
            expected = getattr(self, key)
            if type(frame[key]) is not type(expected) or frame[key] != expected:
                raise ControllerError("IPC_CORRELATION", "Stale or mismatched native identity", {"field": key})
        if not isinstance(frame["payload"], dict) or not isinstance(frame["status"], str):
            raise ControllerError("IPC_MALFORMED", "Invalid native reply payload/status")
        error = frame["error"]
        if error is not None and (not isinstance(error, dict) or set(error) != {"code", "message", "details"}
                                  or not isinstance(error["code"], str) or not isinstance(error["message"], str)
                                  or not isinstance(error["details"], dict)):
            raise ControllerError("IPC_MALFORMED", "Closed structured native error required")
        if frame["status"] in ("binding_required", "binding_updated") and (error is not None or set(frame["payload"]) != {"binding"}):
            raise ControllerError("IPC_MALFORMED", "Errored/malformed reservation cannot receive a binding ACK")
        self._retain("reply", frame)
        body = frame["payload"]
        if body.get("locally_settled") is True or body.get("settled") is True:
            result = body.get("native_result", body.get("result"))
            prior = getattr(self, "_terminal_result", None)
            if result is not None and prior is not None and result != prior:
                raise ControllerError("DIFFERING_NATIVE_DUPLICATE", "Differing duplicate native completion retained without advancement")
            if result is not None:
                self._terminal_result = result
        return frame

    def close(self, on_wait=None):
        actions = []
        try:
            self.process.stdin.close()
            actions.append("stdin_eof_sent")
        except (OSError, ValueError):
            actions.append("stdin_already_closed")
        def await_exit(seconds):
            deadline = time.monotonic() + seconds
            while self.process.poll() is None and time.monotonic() < deadline:
                if on_wait is not None:
                    on_wait()
                try:
                    self.process.wait(timeout=0.1)
                except subprocess.TimeoutExpired:
                    pass
            return self.process.poll() is not None
        if not await_exit(15):
            actions.append("terminate_requested")
            self.process.terminate()
            if not await_exit(5):
                actions.append("kill_requested")
                self.process.kill()
                await_exit(5)
        for thread in (self.reader, self.errors):
            deadline = time.monotonic() + 2
            while thread.is_alive() and time.monotonic() < deadline:
                if on_wait is not None:
                    on_wait()
                thread.join(timeout=0.1)
        protocol_errors = []
        while True:
            try:
                pending = self.frames.get_nowait()
            except queue.Empty:
                break
            if isinstance(pending, EOFError):
                continue
            try:
                if isinstance(pending, BaseException):
                    raise pending
                frame = json.loads(pending)
                self._retain("unconsumed_reply", frame)
                if (not isinstance(frame, dict) or frame.get("request_id") not in self.requests
                        or frame.get("run_id") != self.run_id or frame.get("attempt_id") != self.attempt_id
                        or type(frame.get("owner_epoch")) is not int or frame["owner_epoch"] != self.owner_epoch):
                    raise ControllerError("IPC_CORRELATION", "Unconsumed native frame has wrong identity")
                body = frame.get("payload", {})
                result = body.get("native_result", body.get("result"))
                if result is not None and getattr(self, "_terminal_result", result) != result:
                    raise ControllerError("DIFFERING_NATIVE_DUPLICATE", "Late differing native result retained")
            except Exception as exc:
                protocol_errors.append(_error(exc))
                self._retain("unconsumed_error", {"error": _error(exc), "frame": str(pending)})
        receipt = {"pid": self.process.pid, "returncode": self.process.returncode, "shutdown_actions": actions,
                   "process_settled": self.process.poll() is not None,
                   "graceful_eof_exit": actions == ["stdin_eof_sent"] and self.process.returncode == 0 and not protocol_errors and not self.reader.is_alive() and not self.errors.is_alive(),
                   "stderr": "".join(self.stderr), "protocol_errors": protocol_errors}
        _durable(self.evidence_root / "adapter-process.json", receipt)
        return receipt


def review_gate(reply, *, native_evidence_aliases=None):
    """Consume canonical fields; optional aliases must come from the trusted native ledger."""
    body = reply.get("payload", {})
    compact = "native_result" in body
    result = body.get("native_result") if compact else body.get("result")
    settled = body.get("locally_settled") is True or body.get("settled") is True
    if not isinstance(result, dict):
        return {"accepted": False, "tooling_failure": True, "reason": "NO_REVIEW"}
    code = result.get("exitCode") if compact else body.get("native", {}).get("process_exit_code")
    retained = bool(result.get("result_path") and result.get("receipt_path")) if compact else result.get("review_conforming") is True
    # An unreadable receipt cannot satisfy a publication gate.
    readable = True
    for key in ("result_path", "receipt_path", "findings_path"):
        if result.get(key):
            try:
                alias = (native_evidence_aliases or {}).get(result[key])
                content = _path(alias["path"] if alias is not None else result[key], regular=True).read_bytes()
                if alias is not None and sha256(content).hexdigest() != alias["sha256"]:
                    readable = False
            except (OSError, ControllerError):
                readable = False
    accepted = (reply.get("status") == "completed" and settled and result.get("status") == "reviewed"
                and result.get("verdict") in ("approve", "needs-attention") and type(code) is int
                and code in (0, 2) and result.get("loop_should_continue") is False and retained and readable)
    tooling = not settled or result.get("status") != "reviewed" or type(code) is not int or code not in (0, 2, 4) or not retained or not readable
    return {"accepted": accepted, "tooling_failure": tooling,
            "reason": None if accepted else "needs_orchestrator_review" if result.get("verdict") == "fail" else "NO_REVIEW" if tooling else "REVIEW_BLOCKED"}


@dataclass
class _Active:
    context: StepContext
    task: TaskSpec
    transport: Any
    review: dict | None
    backend: str
    started: float
    request_id: str | None = None
    phase: str = "prepare"
    next_poll: float = 0
    cancelled: bool = False
    settled: bool = False
    pending_frame: dict | None = None
    deferred_error: Exception | None = None


class Controller:

    def __init__(self, workflow_factory, *, artifact_validator=None, adapter_factory=None, skills_root=None, workflow_restorer=None, native_configuration=None):
        self.workflow_factory = workflow_factory
        self.artifact_validator = artifact_validator
        self.adapter_factory = adapter_factory
        self.skills_root = Path(skills_root).absolute() if skills_root is not None else Path(__file__).resolve().parent.parent
        self.workflow_restorer = workflow_restorer
        self.native_configuration = json_value(native_configuration) if native_configuration is not None else None
        self._store = None
        self._active = {}
        self._stopped_results = []

    def _assert_public_ledger(self, store, run_id):
        """Private engineering records never grant public execution or terminal success."""
        forbidden = {
            "source_recovery_admitted", "negative_recovery_admitted", "owned_input_evolution_admitted",
            "owned_input_source_revised", "settled_owned_input_evolution_admitted", "local_runtime_revision_admitted",
            "unprepared_local_source_revision_admitted", "operation_quarantined", "semantic_verifier_continuation_reserved",
            "completed_scope_confirmation_source_admitted", "completed_scope_preservation_confirmed",
            "pre_native_snapshots_recovered", "source_attempt_superseded", "source_completion_invalidated",
            "native_admission_succeeded", "quarantine_successor_admitted", "staged_continuation_replaced",
            "bounded_repair_reserved", "bounded_candidate_repair_reserved",
        }
        events = store.connection.execute("SELECT event_type FROM wf_events WHERE run_id=?", (run_id,))
        if any(row[0] in forbidden for row in events):
            raise ControllerError("SOURCE_RECOVERY_CONFLICT", "Private engineering ledger is not an ordinary public run")

    def _definition(self, spec):
        source_paths = set(spec.source_paths)
        handlers = (self.workflow_factory, *(s.prepare_handler for s in spec.steps), spec.effect_handler, spec.publication_guard, self.workflow_restorer)
        for fn in handlers:
            if fn is None:
                continue
            source = inspect.getsourcefile(fn)
            if source:
                source_paths.add(Path(source).absolute())
        source_paths.update((Path(__file__).absolute(), Path(inspect.getfile(Store)).absolute(),
                             Path(inspect.getfile(TaskSpec)).absolute(), Path(inspect.getfile(PublicationManager)).absolute(),
                             Path(__file__).with_name("workflow.py").absolute()))
        registry = self.skills_root / "_workflow" / "registry.json"
        if registry.exists():
            source_paths.add(registry)
        media = {".py": "text/x-python", ".ts": "text/typescript", ".json": "application/json", ".md": "text/markdown"}
        sources = [{"path": str(p), "sha256": _digest(p), "media_type": media.get(p.suffix, "application/octet-stream")} for p in sorted(source_paths, key=str)]
        definition = {"workflow_id": spec.workflow_id,
                      "steps": [{"step_id": s.step_id, "dependencies": s.dependencies, "kind": s.kind,
                                 "handler": s.prepare_handler.__module__ + ":" + s.prepare_handler.__qualname__,
                                 "fanout_source": s.fanout_source, "concurrency": s.concurrency, "policy": s.policy} for s in spec.steps],
                      "sources": sources, "metadata": spec.metadata,
                      "effect_handler": None if spec.effect_handler is None else spec.effect_handler.__module__ + ":" + spec.effect_handler.__qualname__,
                      "publication_guard": None if spec.publication_guard is None else spec.publication_guard.__module__ + ":" + spec.publication_guard.__qualname__,
                      "restore_handler": None if self.workflow_restorer is None else self.workflow_restorer.__module__ + ":" + self.workflow_restorer.__qualname__}
        return json_value(definition), sha256(_json(definition).encode()).hexdigest()

    def _inputs(self, spec):
        refs = json_value(spec.metadata.get("inputs", ()))
        seen = set()
        for ref in refs:
            if not isinstance(ref, dict) or not {"path", "sha256", "media_type"} <= set(ref) or ref["path"] in seen:
                raise ControllerError("INVALID_INPUT_MANIFEST", "Distinct complete input references required")
            seen.add(ref["path"])
            if _digest(ref["path"]) != ref["sha256"]:
                raise ControllerError("INPUT_CHANGED", "Input differs from declared snapshot", ref)
        return refs

    def _snapshot_files(self, root, references):
        directory = _path(root / "snapshots")
        directory.mkdir(parents=True, exist_ok=True)
        for ref in references:
            InputRef(**{key: ref[key] for key in ("path", "sha256", "media_type")})
            _publish_snapshot(ref["path"], directory / ref["sha256"], ref["sha256"])

    def _snapshot_native_files(self, request, run_id, root, references):
        self._snapshot_files(root, references)

    def run(self, request):
        run_id = str(uuid.uuid4())
        try:
            if not isinstance(request, RunRequest):
                request = RunRequest.from_dict(request)
            spec = self.workflow_factory(request)
            if spec.workflow_id != request.workflow:
                raise ControllerError("WORKFLOW_MISMATCH", "Registered factory returned another workflow")
            definition, definition_hash = self._definition(spec)
            inputs = self._inputs(spec)
            if "--dry-run" in request.arguments:
                if spec.metadata.get("preview_error"):
                    return RunOutcome(run_id=run_id, status="blocked", error=json_value(spec.metadata["preview_error"]))
                preview = spec.metadata.get("preview")
                if preview is None:
                    module = inspect.getmodule(self.workflow_factory)
                    preview = module.preview(request) if module and callable(getattr(module, "preview", None)) else {"workflow": request.workflow, "steps": definition["steps"], "inputs": inputs}
                return RunOutcome(run_id=run_id, status="preview", outputs=({"preview": json_value(preview)},))
            store = Store(request.project_root)
            store.initialize()
            self._store = store
            try:
                store.create_run(run_id, request.workflow, definition_hash, json_value(request), inputs)
                with store.acquire_owner(run_id):
                    root = self._root(request, run_id)
                    _durable(root / "request.json", request)
                    _durable(root / "definition.json", definition)
                    _durable(root / "input-manifest.json", inputs)
                    self._snapshot_files(root, [*definition["sources"], *inputs])
                    store.append_event(run_id, None, "snapshots_committed", {"definition_sha256": definition_hash, "run_root": str(root)})
                    return self._execute(request, run_id, spec)
            finally:
                store.close()
                self._store = None
        except Exception as exc:
            return RunOutcome(run_id=run_id, status="blocked" if getattr(exc, "code", "") not in ("INVALID_REQUEST", "INVALID_ARGUMENTS", "INVALID_GRAPH", "UNKNOWN_PROFILE") else "failed", error=_error(exc))

    def status(self, project, run_id):
        store = Store(Path(project), read_only=True)
        try:
            row = store.read_run(run_id)
            if row is None:
                raise ControllerError("UNKNOWN_RUN", "No such run")
            self._assert_public_ledger(store, run_id)
            return self._outcome(store, run_id, row["status"])
        except Exception as exc:
            return RunOutcome(run_id=run_id, status="blocked", error=_error(exc))
        finally:
            store.close()

    def cancel(self, project, run_id):
        if not (Path(project) / "state.db").is_file():
            raise StoreError("DATABASE_UNAVAILABLE", "Cancellation requires an existing project database")
        store = Store(Path(project))
        store.initialize()
        try:
            self._assert_public_ledger(store, run_id)
            row = store.request_cancel(run_id)
            return self._outcome(store, run_id, row["status"], error={"code": "CANCELLATION_REQUESTED", "message": "Cancellation latched; native settlement is separate", "details": {}})
        finally:
            store.close()

    def _resume_inputs(self, run_id, root, inputs):
        self._assert_public_ledger(self._store, run_id)
        return self._resume_input_refs(run_id, root, inputs)

    def _resume_input_refs(self, run_id, root, inputs):
        observed = []
        operations = [dict(row) for row in self._store.connection.execute("SELECT * FROM wf_publications WHERE run_id=?", (run_id,))]
        for ref in inputs:
            if _digest(root / "snapshots" / ref["sha256"]) != ref["sha256"]:
                raise ControllerError("SNAPSHOT_CHANGED", "Original evidence snapshot changed")
            path = _path(ref["path"])
            actual = _digest(path) if path.exists() else None
            if actual == ref["sha256"]:
                continue
            matching = [op for op in operations if op["target"] == str(path) and actual is not None and op["desired_sha256"] == actual]
            if actual is None:
                matching = []
                for operation in operations:
                    if operation["kind"] != "rename" or operation["candidate_path"] != str(path) or operation["expected_sha256"] != ref["sha256"] or not Path(operation["target"]).exists():
                        continue
                    known = {operation["desired_sha256"]}
                    while True:
                        expanded = known | {later["desired_sha256"] for later in operations if later["target"] == operation["target"] and later["expected_sha256"] in known}
                        if expanded == known:
                            break
                        known = expanded
                    if _digest(operation["target"]) in known:
                        matching.append(operation)
            if not matching:
                raise ControllerError("SNAPSHOT_CHANGED", "Input changed without this run's exact journalled desired effect", {"path": str(path)})
            observed.append({"path": str(path), "original_sha256": ref["sha256"], "actual_sha256": actual,
                             "operation_id": matching[-1]["operation_id"], "classification": "desired"})
        return observed

    def _check_frozen_request(self, request, row, root):
        inputs = json.loads(row["input_manifest_json"])
        if (_path(root / "request.json", regular=True).read_bytes() != (_json(request) + "\n").encode("utf-8")
                or _path(root / "input-manifest.json", regular=True).read_bytes() != (_json(inputs) + "\n").encode("utf-8")):
            raise ControllerError("SNAPSHOT_CHANGED", "Run request/input snapshot bytes changed")
        definition_path = _path(root / "definition.json", regular=True)
        definition = json.loads(definition_path.read_text(encoding="utf-8"))
        if definition_path.read_bytes() != (_json(definition) + "\n").encode("utf-8"):
            raise ControllerError("SNAPSHOT_CHANGED", "Original definition snapshot bytes changed")

    def _resume_definition(self, request, run_id, row, root, original):
        self._assert_public_ledger(self._store, run_id)
        for source in original["sources"]:
            if _digest(root / "snapshots" / source["sha256"]) != source["sha256"]:
                raise ControllerError("SNAPSHOT_CHANGED", "Immutable runtime source snapshot changed")
            if _digest(source["path"]) != source["sha256"]:
                raise ControllerError("SNAPSHOT_CHANGED", "Registered runtime/domain source changed before resume", {"path": source["path"]})
        spec = self.workflow_restorer(request, snapshot=original) if self.workflow_restorer is not None else self.workflow_factory(request)
        definition, digest = self._definition(spec)
        if digest != row["definition_sha256"] or json_value(spec.metadata.get("inputs", ())) != json.loads(row["input_manifest_json"]):
            raise ControllerError("SNAPSHOT_CHANGED", "Registered source or frozen graph/data changed; refusing replay")
        self._check_frozen_request(request, row, root)
        return spec

    def resume(self, project, run_id, decision=None):
        if not (Path(project) / "state.db").is_file():
            return RunOutcome(run_id=run_id, status="blocked", error={"code": "DATABASE_UNAVAILABLE", "message": "Resume requires an existing project database", "details": {}})
        store = Store(Path(project))
        store.initialize()
        self._store = store
        try:
            row = store.read_run(run_id)
            if row is None:
                raise ControllerError("UNKNOWN_RUN", "No such run")
            self._assert_public_ledger(store, run_id)
            if row["status"] in ("done", "failed", "cancelled", "preview"):
                if decision is not None:
                    raise ControllerError("TERMINAL_RUN", "A terminal run cannot accept decisions")
                return self._outcome(store, run_id, row["status"])
            request = RunRequest.from_dict(json.loads(row["request_json"]))
            if request.project_root != Path(project).absolute():
                raise ControllerError("ROOT_CONFLICT", "Resume cannot relocate a project")
            root = self._root(request, run_id)
            snapshot = json.loads(_path(root / "definition.json", regular=True).read_text(encoding="utf-8"))
            if sha256(_json(snapshot).encode()).hexdigest() != row["definition_sha256"]:
                raise ControllerError("SNAPSHOT_CHANGED", "Immutable workflow definition snapshot changed")
            spec = self._resume_definition(request, run_id, row, root, snapshot)
            inputs = json.loads(row["input_manifest_json"])
            self._resume_inputs(run_id, root, inputs)
            with store.acquire_owner(run_id):
                if decision is not None:
                    self._check_resume_native_admission(request, run_id)
                owned_changes = self._resume_inputs(run_id, root, inputs)
                if owned_changes:
                    store.append_event(run_id, None, "owned_input_change_observed", {"inputs": owned_changes})
                if decision is not None:
                    if not isinstance(decision, dict) or set(decision) != {"key", "answer"}:
                        raise ControllerError("INVALID_DECISION", "Decision must select an exact persisted option")
                    saved = store.connection.execute("SELECT * FROM wf_decisions WHERE run_id=? AND decision_key=?", (run_id, decision["key"])).fetchone()
                    if saved is None:
                        raise ControllerError("INVALID_DECISION", "Unknown decision key")
                    store.record_decision(run_id, decision["key"], json.loads(saved["question_json"]), decision)
                return self._execute(request, run_id, spec, recovering=True)
        except Exception as exc:
            return RunOutcome(run_id=run_id, status="blocked", error=_error(exc))
        finally:
            store.close()
            self._store = None

    def _root(self, request, run_id):
        if re.fullmatch(r"[A-Za-z0-9_-]+", run_id) is None:
            raise ControllerError("INVALID_IDENTITY", "Unsafe run identity")
        return _path(request.project_root / "docs" / "tmp" / "pipeline-runs" / run_id)

    def _events(self, run_id, event_type=None, *, cutoff=None):
        query = "SELECT * FROM wf_events WHERE run_id=?"
        args = [run_id]
        if event_type:
            query += " AND event_type=?"
            args.append(event_type)
        if cutoff is not None:
            query += " AND event_id<=?"
            args.append(cutoff)
        return [{**dict(row), "payload": json.loads(row["payload_json"])} for row in self._store.connection.execute(query + " ORDER BY event_id", args)]

    def _records(self, run_id, *, cutoff=None):
        self._assert_public_ledger(self._store, run_id)
        records = []
        for event in self._events(run_id, "item_completed", cutoff=cutoff):
            record = event["payload"]
            data = dict(record.get("data", {}))
            data["completed_at"] = event["created_at"]
            attempt_id = data.get("attempt_id")
            native = self._store.connection.execute("SELECT settled_at FROM wf_attempts WHERE run_id=? AND step_id=? AND item_id=? AND attempt_id=?", (run_id, record["step_id"], record["item_id"], attempt_id)).fetchone() if attempt_id else None
            data["settled_at"] = native[0] if native is not None else None
            records.append({**record, "data": data})
        return records

    def _dependency_records(self, run_id, *, include_scheduling_tombstones=True):
        return self._records(run_id)

    def _outcome(self, store, run_id, status, *, error=None):
        self._assert_public_ledger(store, run_id)
        records = [json.loads(row[0]) for row in store.connection.execute(
            "SELECT payload_json FROM wf_events WHERE run_id=? AND event_type='item_completed' ORDER BY event_id", (run_id,))]
        outputs = tuple(a for record in records for a in record.get("artifacts", ()))
        pending = store.connection.execute("SELECT question_json FROM wf_decisions WHERE run_id=? AND status='waiting' ORDER BY decision_key", (run_id,)).fetchone()
        if error is None and status == "failed":
            failed = next((record["data"] for record in records if record.get("data", {}).get("run_status") == "failed"), None)
            if failed is not None:
                error = failed.get("error") or {"code": "REPORT_TOOLING_FAILURE", "message": "Report/audit retained; complete successful checking was not established", "details": {}}
        if error is None:
            last = store.connection.execute("SELECT payload_json FROM wf_events WHERE run_id=? AND event_type='controller_blocked' ORDER BY event_id DESC LIMIT 1", (run_id,)).fetchone()
            if last is not None and status in ("blocked", "failed"):
                error = json.loads(last[0])["error"]
        return RunOutcome(run_id=run_id, status=status, outputs=outputs,
                          decision=json.loads(pending[0]) if pending else None, error=error)

    def _native_aliases(self, run_id, *, cutoff=None):
        aliases = {}
        root = self._store.project_root / "docs" / "tmp" / "pipeline-runs"
        observations = self._store.connection.execute("""SELECT e.payload_json FROM wf_events e
            JOIN wf_attempts a ON a.run_id=e.run_id AND a.attempt_id=e.attempt_id
            WHERE e.event_type='native_review_evidence_retained' AND a.binding_json IS NOT NULL
            AND (? IS NULL OR e.event_id<=?)
            AND a.settled_at IS NOT NULL AND EXISTS (SELECT 1 FROM wf_events r WHERE r.run_id=e.run_id
            AND r.attempt_id=e.attempt_id AND r.event_type='native_review_result'
            AND (? IS NULL OR r.event_id<=?)) ORDER BY e.event_id""", (cutoff, cutoff, cutoff, cutoff))
        for event in observations:
            for binding in json.loads(event[0])["bindings"]:
                ref = {key: binding[key] for key in ("path", "sha256", "media_type")}
                if _digest(_path(ref["path"], root=root, regular=True)) != ref["sha256"]:
                    raise ControllerError("NATIVE_EVIDENCE_CHANGED", "Trusted retained native evidence changed")
                previous = aliases.get(binding["original_path"])
                if previous is not None and (previous["sha256"], previous["media_type"]) != (ref["sha256"], ref["media_type"]):
                    raise ControllerError("NATIVE_EVIDENCE_ALIAS_CONFLICT", "One original native evidence path cannot alias different bytes/media")
                if previous is None:
                    aliases[binding["original_path"]] = ref
        return aliases

    def _counter_groups(self, run_id, *, cutoff=None):
        policies = {(row["step_id"], row["item_id"]): json.loads(row["policy_json"]) for row in self._store.connection.execute("SELECT step_id,item_id,policy_json FROM wf_steps WHERE run_id=?", (run_id,))}
        groups = {}
        for event in self._events(run_id, "step_reserved", cutoff=cutoff):
            operation = event["payload"]
            policy = policies[(operation["step_id"], operation["item_id"])]
            for group in policy.get("counter_groups", ()):
                if group["operation_kind"] != operation["operation_kind"]:
                    continue
                key = group["key"].replace("{run_id}", run_id).replace("{item_id}", operation["item_id"])
                compound = (key, group["operation_kind"])
                previous = groups.setdefault(compound, {"operation_kind": group["operation_kind"], "maximum": group["maximum"], "operations": set()})
                if previous["maximum"] != group["maximum"]:
                    raise ControllerError("COUNTER_POLICY_CHANGED", "Logical counter group policy changed")
                previous["operations"].add(operation["operation_id"])
        result = {}
        for (key, kind), value in groups.items():
            result.setdefault(key, {})[kind] = {"maximum": value["maximum"], "count": len(value["operations"]), "operation_ids": sorted(value["operations"])}
        return result

    def _ancestor_ids(self, step, spec):
        ancestors = set(step.dependencies)
        for predecessor in reversed(spec.steps):
            if predecessor.step_id in ancestors:
                ancestors.update(predecessor.dependencies)
        return ancestors

    def _context(self, request, run_id, step, row, item, spec, *, attempt_id=None, data=None, invalidated=(), native_alias_cutoff=None, records_cutoff=None):
        excluded = {(value["step_id"], value["item_id"], value["attempt_id"]) for value in invalidated}
        records = [record for record in (self._dependency_records(run_id, include_scheduling_tombstones=False) if records_cutoff is None else self._records(run_id, cutoff=records_cutoff))
                   if (record["step_id"], record["item_id"], record["data"].get("attempt_id")) not in excluded]
        step_data = {}
        for record in records:
            step_data.setdefault(record["step_id"], []).append(record)
        decisions = {r["decision_key"]: json.loads(r["answer_json"])["answer"] for r in self._store.connection.execute("SELECT * FROM wf_decisions WHERE run_id=? AND status='answered'", (run_id,))}
        steps = {r["step_id"] + ":" + r["item_id"]: dict(r) for r in self._store.connection.execute("SELECT * FROM wf_steps WHERE run_id=?", (run_id,))}
        for value in invalidated:
            key = value["step_id"] + ":" + value["item_id"]
            steps[key] = {**steps[key], "status": "blocked"}
        reviews = {r["artifact_id"]: dict(r) for r in self._store.connection.execute("SELECT * FROM wf_reviews WHERE run_id=?", (run_id,))}
        attempt_id = attempt_id or str(uuid.uuid4())
        candidate = self._root(request, run_id) / "attempts" / attempt_id / "candidate"
        merged = {**json_value(spec.metadata.get("data", {})), "step_data": step_data, "decisions": decisions, "item": item}
        ancestors = self._ancestor_ids(step, spec)
        for predecessor in spec.steps:
            if predecessor.step_id not in ancestors:
                continue
            for record in step_data.get(predecessor.step_id, ()):
                if predecessor.fanout_source is None or (step.fanout_source is not None and record["item_id"] == row["item_id"]):
                    merged.update(record.get("data", {}))
        if data:
            merged.update(data)
        merged.pop("review_subject_successions", None)
        merged["step_data"], merged["item"], merged["decisions"] = step_data, item, decisions
        merged["native_evidence_aliases"] = self._native_aliases(run_id, cutoff=native_alias_cutoff)
        observed = {}
        for record in records:
            if record.get("status") == "negative_only":
                continue
            observed.setdefault(record["step_id"], {})[record["item_id"]] = {key: record["data"][key] for key in ("completed_at", "settled_at")}
        merged["gate_observed_at"] = observed
        return StepContext(request, run_id, step, row["item_id"], row["ordinal"], {"steps": steps, "reviews": reviews, "groups": self._counter_groups(run_id)},
                           tuple(a for r in records if r["step_id"] in step.dependencies for a in r.get("artifacts", ())),
                           {"project_root": str(request.project_root), "run_root": str(self._root(request, run_id)),
                            "attempt_id": attempt_id, "candidate_dir": str(candidate), "skills_root": str(self.skills_root)}, merged)

    def _members(self, step, records, spec):
        if step.fanout_source is None:
            return [{"item_id": "main"}]
        source = max((predecessor for predecessor in self._ancestor_ids(step, spec) if step.fanout_source.startswith(predecessor + ".")), key=len, default=None)
        if source is None:
            raise ControllerError("INVALID_FANOUT", "Fanout must reference a declared predecessor data key")
        key = step.fanout_source[len(source) + 1:]
        values = []
        for record in records:
            if record["step_id"] == source:
                current = record.get("data", {})
                for part in key.split("."):
                    if not isinstance(current, dict) or part not in current:
                        raise ControllerError("INVALID_FANOUT", "Missing explicit fanout source", {"source": step.fanout_source})
                    current = current[part]
                if not isinstance(current, list):
                    raise ControllerError("INVALID_FANOUT", "Fanout source must be a list")
                values.extend(current)
        identities = []
        for value in values:
            if not isinstance(value, dict) or type(value.get("item_id")) is not str or not value["item_id"]:
                raise ControllerError("INVALID_FANOUT", "Every subject needs a stable explicit item_id")
            identities.append(value["item_id"])
        if len(identities) != len(set(identities)):
            raise ControllerError("INVALID_FANOUT", "Duplicate fanout subjects")
        return values

    def _finish(self, context, artifacts=(), data=None, *, skipped=False):
        store = self._store
        if store.is_cancelled(context.run_id):
            raise ControllerError("CANCELLED", "Cancellation precedes acceptance")
        record = {"step_id": context.step_id, "item_id": context.item_id, "ordinal": context.ordinal,
                  "status": "skipped" if skipped else "done", "artifacts": json_value(artifacts), "data": json_value(data or {})}
        store.complete_step(context.run_id, context.step_id, context.item_id, record)

    def _register_subjects(self, context, data):
        subjects = data.get("review_subjects", ())
        if not subjects:
            return
        values = []
        for subject in subjects:
            value = json_value(subject)
            value["artifact_id"] = value["artifact_id"].replace("{run_id}", context.run_id).replace("{item_id}", context.item_id)
            values.append(value)
        self._store.register_review_subjects(context.run_id, values)

    def _validate_projections(self, context, data, artifacts):
        projections = json_value(data.get("candidate_projections", ()))
        if not isinstance(projections, list):
            raise ControllerError("INVALID_PROJECTION", "Candidate projections must be an explicit list")
        expected = {"schema_version", "projection_id", "producer_attempt_id", "source", "candidate", "edits", "proof_ref"}
        refs = {"name", "path", "sha256", "media_type"}
        lineage = {"producer_attempt_id", "projection_id", "projection_ref", "native_producer_ref"}
        subjects, payloads = {}, []
        manager = PublicationManager(self._store)
        for projection in projections:
            if (not isinstance(projection, dict) or set(projection) != expected
                    or type(projection["schema_version"]) is not int or projection["schema_version"] != 1
                    or type(projection["projection_id"]) is not str or not projection["projection_id"]
                    or type(projection["producer_attempt_id"]) is not str or not projection["producer_attempt_id"]
                    or projection["projection_id"] in subjects):
                raise ControllerError("INVALID_PROJECTION", "Distinct closed version-one projection identities required")
            for key in ("source", "candidate"):
                ref = projection[key]
                if not isinstance(ref, dict) or set(ref) != refs or type(ref["name"]) is not str or not ref["name"]:
                    raise ControllerError("INVALID_PROJECTION", "Closed named projection references required")
                InputRef(**{name: ref[name] for name in ("path", "sha256", "media_type")})
            if not isinstance(projection["proof_ref"], dict) or set(projection["proof_ref"]) != {"path", "sha256", "media_type"}:
                raise ControllerError("INVALID_PROJECTION", "Closed projection proof reference required")
            InputRef(**projection["proof_ref"])
            edits = projection["edits"]
            if not isinstance(edits, list) or any(not isinstance(edit, dict) or set(edit) != {"start", "end", "before_hex", "after_hex"} for edit in edits):
                raise ControllerError("INVALID_PROJECTION", "Closed ordered byte edits required")
            candidate = projection["candidate"]
            matching = [artifact for artifact in artifacts if all(artifact.get(key) == candidate[key] for key in refs)]
            proof = projection["proof_ref"]
            if len(matching) != 1 or not any(all(artifact.get(key) == proof[key] for key in proof) for artifact in artifacts):
                raise ControllerError("INVALID_PROJECTION", "H1 and proof must be actual declared current LOCAL artifacts")
            artifact = matching[0]
            native_ref = {key: projection["source"][key] for key in ("path", "sha256", "media_type")}
            if (not lineage <= set(artifact) or artifact["producer_attempt_id"] != projection["producer_attempt_id"]
                    or artifact["projection_id"] != projection["projection_id"] or artifact["projection_ref"] != proof
                    or artifact["native_producer_ref"] != native_ref):
                raise ControllerError("PROJECTION_LINEAGE_MISMATCH", "LOCAL H1 must retain its exact native H0 and projection lineage")
            manager.validate_projection(context.run_id, projection, candidate_dir=context.candidate_dir)
            subjects[projection["projection_id"]] = projection
            payloads.append({"schema_version": 1, "run_id": context.run_id, "step_id": context.step_id,
                             "item_id": context.item_id, "ordinal": context.ordinal,
                             "definition_sha256": self._store.read_run(context.run_id)["definition_sha256"],
                             "candidate_dir": str(context.candidate_dir), "projection": projection,
                             "validation": {"valid": True, "source": "registered_local_handler"}})
        for artifact in artifacts:
            if lineage & set(artifact) and (not lineage <= set(artifact) or artifact["projection_id"] not in subjects):
                raise ControllerError("PROJECTION_LINEAGE_MISMATCH", "A LOCAL artifact cannot invent native projection lineage")
        # Validate the whole declared set before registering any member.
        for payload in payloads:
            previous = [event["payload"] for event in self._events(context.run_id, "candidate_projection_validated")
                        if event["payload"]["projection"]["projection_id"] == payload["projection"]["projection_id"]]
            if previous and previous != [payload]:
                raise ControllerError("PROJECTION_CONFLICT", "Projection identity already binds different immutable bytes/context")
            if not previous:
                self._store.append_event(context.run_id, None, "candidate_projection_validated", payload)

    def _validate_local(self, context, local):
        if not isinstance(local, Mapping) or set(local) - {"artifacts", "data"}:
            raise ControllerError("INVALID_LOCAL_RESULT", "Local result must contain artifacts and data only")
        artifacts, names = [], set()
        base_fields = {"name", "path", "sha256", "media_type"}
        lineage_fields = {"producer_attempt_id", "projection_id", "projection_ref", "native_producer_ref"}
        for value in local.get("artifacts", ()):
            value = json_value(value)
            if not isinstance(value, dict) or not base_fields <= set(value) or set(value) - base_fields - lineage_fields or value["name"] in names:
                raise ControllerError("INVALID_LOCAL_RESULT", "Distinct closed actual local artifact references required")
            names.add(value["name"])
            path = _path(value["path"], root=context.candidate_dir, regular=True)
            if _digest(path) != value["sha256"]:
                raise ControllerError("ARTIFACT_CHANGED", "Local candidate hash mismatch")
            artifacts.append({**value, "step_id": context.step_id, "item_id": context.item_id, "attempt_id": None})
        self._snapshot_files(context.candidate_dir.parent, artifacts)
        prepared_path = context.candidate_dir.parent / "local-result.json"
        _durable(prepared_path, local)
        prepared = {"schema_version": 1, "run_id": context.run_id, "step_id": context.step_id,
                    "item_id": context.item_id, "ordinal": context.ordinal,
                    "definition_sha256": self._store.read_run(context.run_id)["definition_sha256"],
                    "candidate_dir": str(context.candidate_dir), "local_result_path": str(prepared_path),
                    "local_result_sha256": _digest(prepared_path)}
        old = [event["payload"] for event in self._events(context.run_id, "local_result_prepared")
               if event["payload"]["candidate_dir"] == str(context.candidate_dir)]
        if old and old != [prepared]:
            raise ControllerError("LOCAL_SNAPSHOT_CONFLICT", "Prepared LOCAL output snapshot changed")
        if not old:
            self._store.append_event(context.run_id, None, "local_result_prepared", prepared)
        self._validate_projections(context, local.get("data", {}), artifacts)
        payload = {"schema_version": 1, "run_id": context.run_id, "step_id": context.step_id,
                   "item_id": context.item_id, "ordinal": context.ordinal,
                   "definition_sha256": self._store.read_run(context.run_id)["definition_sha256"],
                   "candidate_dir": str(context.candidate_dir), "artifacts": [{key: a[key] for key in base_fields | lineage_fields if key in a} for a in artifacts],
                   "data": json_value(local.get("data", {})),
                   "publications": self._publication_intents(context, local.get("data", {}), include_fallbacks=True),
                   "validation": {"valid": True, "source": "registered_local_handler"}}
        _json(payload)
        self._store.append_event(context.run_id, None, "local_output_validated", payload)
        self._register_subjects(context, payload["data"])
        return artifacts, payload["data"]

    def _publication_intents(self, context, data, *, include_fallbacks=False, require_files=True):
        originals = list(data.get("publications", ()))
        if include_fallbacks:
            for policy in data.get("publication_refusal", {}).values():
                if not isinstance(policy, Mapping) or set(policy) != {"fallback_publications"}:
                    raise ControllerError("INVALID_PUBLICATION", "Closed source-defined refusal branch required")
                originals.extend(policy["fallback_publications"])
        intents = []
        for index, original in enumerate(originals):
            effect = json_value(original)
            kind = effect.get("kind")
            source_key = "source" if kind == "rename" else "candidate"
            candidate = effect.get(source_key, effect.get("candidate_path"))
            required = {"target", "expected_sha256", "root"}
            allowed = required | {"kind", "candidate_path", "candidate", "source", "desired_sha256", "acceptance", "operation_id", "attempt_id"}
            if kind not in ("replace", "rename") or not required <= set(effect) or set(effect) - allowed or candidate is None:
                raise ControllerError("INVALID_PUBLICATION", "Explicit bounded publication intent required")
            candidate = _path(candidate, regular=require_files)
            if require_files and effect.get("desired_sha256") is not None and _digest(candidate) != effect["desired_sha256"]:
                raise ControllerError("ARTIFACT_CHANGED", "Publication candidate changed")
            intent = {"kind": kind, source_key: str(candidate), "target": str(_path(effect["target"])),
                      "expected_sha256": effect["expected_sha256"], "root": str(_path(effect["root"])),
                      "operation_id": effect.get("operation_id", f"{context.run_id}:{context.step_id}:{context.item_id}:{index}")}
            if effect.get("attempt_id") is not None:
                intent["attempt_id"] = effect["attempt_id"]
            intents.append(intent)
        if len({effect["operation_id"] for effect in intents}) != len(intents):
            raise ControllerError("INVALID_PUBLICATION", "Publication operation identities must be distinct")
        return intents

    def _publish(self, context, data, spec, *, intents=None):
        receipts = []
        guard_context = replace(context, data={**context.data, **data})
        manager = PublicationManager(self._store)
        original = self._publication_intents(context, data, require_files=intents is None)
        authorized = {value["operation_id"]: value for value in intents or self._publication_intents(context, data, include_fallbacks=True)}
        pending = [authorized[value["operation_id"]] for value in original]
        while pending:
            effect = pending.pop(0)
            if self._store.is_cancelled(context.run_id):
                raise ControllerError("CANCELLED", "Cancellation blocks publication")
            attempt, operation = effect.get("attempt_id"), effect["operation_id"]
            previous = [event["payload"] for event in self._events(context.run_id, "publication_branch_selected") if event["payload"]["operation_id"] == operation]
            if previous:
                pending = [authorized[value["operation_id"]] for value in previous[-1]["fallback_publications"]]
                continue
            if spec.publication_guard is not None:
                spec.publication_guard(guard_context, self._store, _freeze(effect))
            try:
                if effect["kind"] == "rename":
                    receipt = manager.rename_file(context.run_id, attempt, Path(effect["source"]), Path(effect["target"]), effect["expected_sha256"], operation, root=Path(effect["root"]))
                else:
                    receipt = manager.publish_file(context.run_id, attempt, Path(effect["candidate"]), Path(effect["target"]), effect["expected_sha256"], operation, root=Path(effect["root"]))
            except StoreError as exc:
                policy = data.get("publication_refusal", {}).get(operation)
                if effect["kind"] != "rename" or exc.code not in ("RENAME_REFUSED", "RENAME_COLLISION") or not policy:
                    raise
                source = _path(effect["source"], regular=True)
                destination = _path(effect["target"])
                actual_source = _digest(source)
                actual_destination = _digest(destination) if destination.exists() else None
                if actual_source != effect["expected_sha256"] or (destination.exists() and os.path.samefile(source, destination)) or (exc.code == "RENAME_REFUSED" and actual_destination is not None):
                    raise ControllerError("EFFECT_UNCERTAIN", "Rename refusal cannot prove an unchanged prior state") from exc
                fallback = self._publication_intents(context, {"publications": policy["fallback_publications"]})
                if not fallback or any(value["operation_id"] not in authorized or value != authorized[value["operation_id"]] for value in fallback):
                    raise ControllerError("PUBLICATION_NOT_AUTHORIZED", "Fallback effect lacks prior immutable authorization")
                self._store.append_event(context.run_id, attempt, "publication_branch_selected", {"operation_id": operation,
                                         "refusal": _error(exc), "source_sha256": actual_source, "destination_sha256": actual_destination,
                                         "fallback_publications": fallback})
                pending = fallback
                continue
            receipts.append(receipt)
            if receipt.get("status") != "applied":
                raise ControllerError("PUBLICATION_BLOCKED", "Publication effect did not complete", receipt)
        return receipts

    def _external_effects(self, context, data, spec):
        operations = json_value(data.get("external_actions", ()))
        if not operations:
            return []
        self._assert_public_ledger(self._store, context.run_id)
        if context.step.kind != "publish" or spec.effect_handler is None:
            raise ControllerError("EXTERNAL_EFFECT_NOT_AUTHORIZED", "External effects require an explicit source-bound publication callback")
        local = [event["payload"] for event in self._events(context.run_id, "local_output_validated")
                 if (event["payload"]["step_id"], event["payload"]["item_id"]) == (context.step_id, context.item_id)]
        if not local or local[-1]["data"].get("external_actions", []) != operations:
            raise ControllerError("EXTERNAL_EFFECT_NOT_AUTHORIZED", "External operation differs from its immutable LOCAL authorization")
        results, identities = [], set()
        for operation in operations:
            if not isinstance(operation, dict) or type(operation.get("operation_id")) is not str or not operation["operation_id"] or operation["operation_id"] in identities:
                raise ControllerError("INVALID_EXTERNAL_EFFECT", "Distinct stable source-declared operation identities required")
            operation_id = operation["operation_id"]
            identities.add(operation_id)
            if self._store.is_cancelled(context.run_id):
                raise ControllerError("CANCELLED", "Cancellation precedes the external effect")
            prior = [event["payload"] for event in self._events(context.run_id, "external_effect_observed") if event["payload"]["operation_id"] == operation_id]
            authorizations = [event["payload"] for event in self._events(context.run_id, "external_effect_authorized") if event["payload"]["operation_id"] == operation_id]
            if any(value["operation"] != operation for value in (*prior, *authorizations)):
                raise ControllerError("EXTERNAL_EFFECT_CONFLICT", "Operation identity already binds different immutable arguments")
            previous = prior[-1]["result"] if prior else None
            if previous is not None and previous.get("status") not in ("blocked", "unknown", "uncertain"):
                results.append(previous)
                continue
            mode = "reconcile" if authorizations else "execute"
            if not authorizations:
                self._store.append_event(context.run_id, None, "external_effect_authorized", {"step_id": context.step_id,
                                         "item_id": context.item_id, "ordinal": context.ordinal, "operation_id": operation_id,
                                         "definition_sha256": self._store.read_run(context.run_id)["definition_sha256"], "operation": operation})
            else:
                self._store.append_event(context.run_id, None, "external_effect_reconciliation_requested", {"operation_id": operation_id})
            effect_context = replace(context, data={**context.data, **data, "effect_mode": mode,
                                     "native_evidence_aliases": self._native_aliases(context.run_id)})
            try:
                result = json_value(spec.effect_handler(effect_context, self._store, operation))
                if not isinstance(result, dict) or type(result.get("status")) is not str:
                    raise ControllerError("INVALID_EXTERNAL_RESULT", "Source-bound effect must return its actual structured outcome")
            except Exception as exc:
                result = {"status": "unknown", "error": _error(exc)}
            self._store.append_event(context.run_id, None, "external_effect_observed", {"operation_id": operation_id, "operation": operation,
                                     "mode": mode, "result": result})
            results.append(result)
            if result["status"] in ("blocked", "unknown", "uncertain"):
                raise ControllerError("EXTERNAL_EFFECT_UNRESOLVED", "Retained external outcome needs read-only reconciliation, never another submission", {"operation_id": operation_id, "result": result})
        return results

    def _reserve(self, context, task, data, *, review=False):
        self._assert_public_ledger(self._store, context.run_id)
        policy = context.step.policy
        reservation_identity = step_reservation_identity = task.attempt_id
        self._register_subjects(context, data)
        charges = [*policy.get("charges", ()), *data.get("charges", ())]
        if review:
            info = data["review"]
            charges.append({"artifact_id": info["artifact_id"], "contract": info["contract"], "artifact_tier": info["artifact_tier"],
                            "operation_kind": "full_review", "max_full_reviews": policy.get("max_full_reviews", 4),
                            "max_repairs": policy.get("max_repair_rounds", 3)})
        def expand(value):
            return value.replace("{run_id}", context.run_id).replace("{item_id}", context.item_id) if isinstance(value, str) else value
        reviews, steps = [], []
        for charge in charges:
            charge = {k: expand(v) for k, v in charge.items()}
            reviews.append({"artifact_id": charge.get("artifact_id", task.artifact_id),
                            "contract": charge.get("contract", task.review_contract or "none"),
                            "artifact_tier": charge.get("artifact_tier", task.artifact_tier),
                            "operation_kind": charge["operation_kind"],
                            "operation_id": charge.get("operation_id", reservation_identity),
                            "max_full_reviews": charge.get("max_full_reviews", 4),
                            "max_repairs": charge.get("max_repairs", 3)})
        for charge in (*policy.get("step_charges", ()), *data.get("step_charges", ())):
            charge = {k: expand(v) for k, v in charge.items()}
            steps.append({"step_id": charge.get("step_id", context.step_id), "item_id": charge.get("item_id", context.item_id),
                          "operation_kind": charge["operation_kind"], "operation_id": charge.get("operation_id", step_reservation_identity),
                          "maximum": charge["maximum"]})
        groups = self._counter_groups(context.run_id)
        for group in policy.get("counter_groups", ()):
            if not {"key", "operation_kind", "maximum"} <= set(group) or set(group) - {"key", "operation_kind", "maximum", "operation_id"} or type(group["maximum"]) is not int or group["maximum"] < 1:
                raise ControllerError("INVALID_RESERVATION", "Closed logical counter group required")
            key, kind = expand(group["key"]), group["operation_kind"]
            matching = [charge for charge in steps if charge["operation_kind"] == kind]
            if not matching:
                matching = [{"step_id": context.step_id, "item_id": context.item_id, "operation_kind": kind, "operation_id": step_reservation_identity, "maximum": 1}]
                steps.extend(matching)
            if "operation_id" in group:
                for charge in matching:
                    charge["operation_id"] = expand(group["operation_id"])
            existing = groups.get(key, {}).get(kind, {"maximum": group["maximum"], "operation_ids": []})
            if existing["maximum"] != group["maximum"]:
                raise ControllerError("COUNTER_POLICY_CHANGED", "Logical counter group policy changed")
            if len(set(existing["operation_ids"]) | {charge["operation_id"] for charge in matching}) > group["maximum"]:
                raise ControllerError("STEP_EXHAUSTED", "Logical sequence counter allowance exhausted", {"group": key})
        self._store.reserve_operations(context.run_id, reviews, steps)

    def _review_task(self, context, data):
        review = data.get("review")
        required = {"artifact_id", "artifact_tier", "contract", "source", "focus", "input_refs"}
        if not isinstance(review, Mapping) or set(review) != required:
            raise ControllerError("INVALID_REVIEW", "A complete explicit native review request is required")
        refs = tuple(InputRef(**json_value(v)) for v in review["input_refs"])
        if not any(ref.path == review["source"] for ref in refs):
            raise ControllerError("INVALID_REVIEW", "Review source must be hash-bound")
        for ref in refs:
            if _digest(ref.path) != ref.sha256:
                raise ControllerError("INPUT_CHANGED", "Review inputs changed")
        _path(review["focus"], regular=True)
        wire = {"schema_version": 1, "run_id": context.run_id, "step_id": context.step_id, "item_id": context.item_id,
                "attempt_id": context.attempt_id, "duty": "review", "instructions": "Invoke the native discriminator on the immutable source and complete focus, preserving its envelope unchanged.",
                "inputs": json_value(refs), "resources": [{"path": review["focus"], "sha256": _digest(review["focus"]), "media_type": "text/markdown"}],
                "candidate_dir": str(context.candidate_dir), "outputs": [], "allowed_actions": ["read"],
                "read_roots": sorted({str(Path(r.path).parent) for r in refs} | {str(Path(review["focus"]).parent)}), "write_roots": [],
                "result_schema": task_result_schema(), "artifact_id": review["artifact_id"], "review_contract": review["contract"],
                "artifact_tier": review["artifact_tier"]}
        wire["semantic_sha256"] = semantic_hash(wire)
        return task_from_dict(wire)

    def _backend(self, request, review=False):
        return request.caller_host if review else "omp" if request.backend == "auto" else request.backend

    def _native_admission_for(self, request, run_id):
        from .workflow import _checked_refs
        self._assert_public_ledger(self._store, run_id)
        root = self._root(request, run_id)
        events = self._events(run_id, "native_admission_committed")
        if len(events) != 1:
            raise ControllerError("NATIVE_CAPABILITY_UNAVAILABLE", "Exactly one original native admission is required")
        ref = events[0]["payload"]
        if not isinstance(ref, dict) or set(ref) != {"path", "sha256", "media_type"}:
            raise ControllerError("NATIVE_CAPABILITY_UNAVAILABLE", "Closed native admission InputRef required")
        path = _path(ref["path"], root=root, regular=True)
        if path != root / "native-admission.json" or ref["media_type"] != "application/json" or _digest(path) != ref["sha256"]:
            raise ControllerError("NATIVE_CAPABILITY_UNAVAILABLE", "Immutable native admission changed")
        value = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(value, dict) or set(value) != {"schema_version", "run_id", "request_sha256", "admission"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1 or value["run_id"] != run_id
                or value["request_sha256"] != sha256(_json(request).encode()).hexdigest()):
            raise ControllerError("NATIVE_CAPABILITY_UNAVAILABLE", "Admission does not bind this immutable request")
        admission = value["admission"]
        if (not isinstance(admission, dict) or set(admission) != {"schema_version", "machine", "caller_host", "caller_family", "backend", "configuration", "task_backends", "input_refs", "unavailable"}
                or type(admission["schema_version"]) is not int or admission["schema_version"] != 1
                or admission["caller_host"] != request.caller_host or admission["caller_family"] != request.caller_family
                or admission["backend"] != request.backend or type(admission["machine"]) is not str
                or admission["machine"].casefold() != platform.node().casefold()):
            raise ControllerError("NATIVE_CAPABILITY_UNAVAILABLE", "Native admission machine/caller/backend or closed schema changed")
        sources = admission["input_refs"]
        if not isinstance(sources, list) or not sources:
            raise ControllerError("NATIVE_CAPABILITY_UNAVAILABLE", "Nonempty native admission evidence required")
        _checked_refs(sources)
        for source in sources:
            if _digest(root / "snapshots" / source["sha256"]) != source["sha256"]:
                raise ControllerError("NATIVE_CAPABILITY_UNAVAILABLE", "Retained native admission snapshot changed")
            self._heartbeat()
            if self._store.is_cancelled(run_id):
                raise ControllerError("CANCELLED", "Cancellation blocks native admission/dispatch")
        return admission

    def _check_native_contexts(self, run_id, root):
        for event in self._events(run_id, "native_context_committed"):
            native = event["payload"]
            path = _path(native["path"], root=root / "attempts", regular=True)
            if _digest(path) != native["sha256"]:
                raise ControllerError("NATIVE_CONTEXT_CHANGED", "Original native context changed")
            for ref in native.get("sources", ()):
                snapshot = path.parent / "snapshots" / ref["sha256"]
                if (_digest(snapshot) != ref["sha256"]
                        or _digest(ref["path"]) != ref["sha256"]):
                    raise ControllerError("NATIVE_CONTEXT_CHANGED", "Native context source evidence changed")

    def _check_resume_native_admission(self, request, run_id):
        self._assert_public_ledger(self._store, run_id)
        if self._events(run_id, "native_admission_committed"):
            self._native_admission_for(request, run_id)

    def _native_admission(self, context):
        return self._native_admission_for(context.request, context.run_id)

    def _ensure_native_admission(self, context, backend, *, review=False):
        # Source adapter factories are isolated deterministic transports, not
        # installed routes or declarations of native readiness.
        if not review and backend == "codex":
            raise ControllerError("NATIVE_BACKEND_UNAVAILABLE", "Codex tasks are unavailable by approved scope; caller reviews remain supported")
        if self.adapter_factory is not None and self.native_configuration is None:
            return
        from .workflow import preflight_native
        if self._events(context.run_id, "native_admission_committed"):
            admission = self._native_admission(context)
        else:
            admission = preflight_native(context.request, self.skills_root, native_configuration=self.native_configuration)
            value = {"schema_version": 1, "run_id": context.run_id,
                     "request_sha256": sha256(_json(context.request).encode()).hexdigest(), "admission": admission}
            path = self._root(context.request, context.run_id) / "native-admission.json"
            _durable(path, value)
            self._snapshot_native_files(context.request, context.run_id, path.parent, admission["input_refs"])
            self._store.append_event(context.run_id, None, "native_admission_committed",
                                     {"path": str(path), "sha256": _digest(path), "media_type": "application/json"})
            admission = self._native_admission(context)
        if backend not in admission["configuration"]:
            raise ControllerError("NATIVE_CAPABILITY_UNAVAILABLE", "Selected native route has no frozen static admission", {"backend": backend})
        if not review and backend not in admission.get("task_backends", ()):
            raise ControllerError("NATIVE_CAPABILITY_UNAVAILABLE", "Caller review readiness cannot authorize a task fallback", {"backend": backend})

    def _adapter(self, context, task, backend, spec, *, recovery=False):
        if self.adapter_factory is not None:
            return self.adapter_factory(context=context, task=task, backend=backend,
                                        owner_epoch=self._store.assert_owner(context.run_id), recovery=recovery)
        admission = self._native_admission(context)
        declaration = admission["configuration"]
        config = declaration.get(backend)
        if not isinstance(config, dict):
            raise ControllerError("NATIVE_DECLARATION_UNAVAILABLE", "Selected backend has no accepted native context", {"backend": backend})
        from .workflow import _checked_refs
        sources = _checked_refs(config.get("source_refs"))
        directory = Path(task.candidate_dir).parent
        evidence = directory / ("ipc-" + str(self._store.assert_owner(context.run_id)))
        evidence.mkdir(exist_ok=True)
        native_evidence = directory / "native"
        native_evidence.mkdir(exist_ok=True)
        review = task.duty == "review"
        cwd = str(Path(task.candidate_dir))
        if backend == "codex" and not review:
            if config.get("context", {}).get("task_enabled", False) is not True:
                raise ControllerError("NATIVE_BACKEND_UNAVAILABLE", "Codex task execution is unavailable by approved scope")
            scratch = directory / "codex-scratch.json"
            if scratch.exists():
                cwd = json.loads(scratch.read_text(encoding="utf-8"))["cwd"]
            else:
                cwd = tempfile.mkdtemp(prefix="wiki-codex-task-")
                _durable(scratch, {"cwd": cwd})
            root = _path(cwd)
            if (not root.is_dir() or any(root.iterdir()) or root.is_relative_to(context.request.project_root)
                    or context.request.project_root.is_relative_to(root)
                    or any((parent / marker).exists() for parent in (root, *root.parents) for marker in (".codex", ".agents", ".git", "AGENTS.md"))
                    or any(root.is_relative_to(Path(p)) or Path(p).is_relative_to(root) for p in config["context"]["private_state_roots"])):
                raise ControllerError("UNSAFE_NATIVE_WORKSPACE", "Codex task scratch must be empty outside project/private/discovery ancestry")
        native = {**config.get("context", {}), "schema_version": 1, "cwd": cwd, "evidence_dir": str(native_evidence), "source_refs": sources}
        if backend == "omp":
            native["session_dir"] = str(directory / "sessions")
            command = [_path(config["bun"], regular=True), _path(self.skills_root / "_workflow" / "harness" / "omp" / "adapter.ts", regular=True)]
        else:
            native.update(backend=backend, caller_host=context.request.caller_host, caller_family=context.request.caller_family)
            command = [_path(config["python"], regular=True), "-I", "-B", _path(self.skills_root / "_workflow" / "harness" / backend / "adapter.py", regular=True)]
        context_path = directory / "execution-context.json"
        _durable(context_path, native)
        self._snapshot_files(directory, sources)
        _durable(directory / "native-command.json", {"command": command, "backend": backend, "context_sha256": _digest(context_path), "sources": sources})
        self._store.append_event(context.run_id, task.attempt_id, "native_context_committed", {"path": str(context_path), "sha256": _digest(context_path), "sources": sources})
        transport = NativeIPC(command, context_path, evidence, context.run_id, task.attempt_id, self._store.assert_owner(context.run_id))
        try:
            self._store.append_event(context.run_id, task.attempt_id, "adapter_process_started", {"pid": transport.process.pid, "backend": backend, "context_path": str(context_path), "owner_epoch": self._store.assert_owner(context.run_id)})
        except Exception:
            # No native input has been sent. Close this owned finite host even
            # when the database cannot record its post-Popen identity.
            transport.close()
            raise
        return transport

    def _alternate_attempt(self, context, task):
        attempt_id = str(uuid.uuid4())
        candidate = self._root(context.request, context.run_id) / "attempts" / attempt_id / "candidate"
        old_root = Path(task.candidate_dir)
        wire = task_to_dict(task)
        wire.update(attempt_id=attempt_id, candidate_dir=str(candidate),
                    write_roots=[str(candidate / Path(root).relative_to(old_root)) for root in task.write_roots])
        wire["semantic_sha256"] = semantic_hash(wire)
        context = replace(context, paths={**context.paths, "attempt_id": attempt_id, "candidate_dir": str(candidate)})
        return context, task_from_dict(wire)

    def _attempt_backend(self, context, task, review):
        events = [event["payload"] for event in self._events(context.run_id, "native_dispatch_intent") if event["attempt_id"] == task.attempt_id]
        return events[-1]["backend"] if events else self._backend(context.request, review)

    def _fallback_backend(self, context, backend, *, review=False):
        if review or context.request.backend != "auto" or self._store.is_cancelled(context.run_id):
            return None
        # Codex tasks are deliberately unavailable; no attempt is allocated for
        # an omitted backend, and explicit selections never fall through.
        return "claude" if backend == "omp" else None

    def _start(self, context, task, data, spec, *, review=False, recovery=False, backend_override=None, charges_reserved=False):
        self._assert_public_ledger(self._store, context.run_id)
        store = self._store
        if store.is_cancelled(context.run_id):
            raise ControllerError("CANCELLED", "Cancellation blocks dispatch")
        if (task.run_id, task.step_id, task.item_id) != (context.run_id, context.step_id, context.item_id):
            raise ControllerError("TASK_IDENTITY_MISMATCH", "Prepared task has wrong run/step/item")
        if Path(task.candidate_dir) != context.candidate_dir or task.attempt_id != context.attempt_id:
            raise ControllerError("TASK_IDENTITY_MISMATCH", "Prepared task must use the current preallocated attempt/candidate")
        backend = backend_override or (self._attempt_backend(context, task, review) if recovery else self._backend(context.request, review))
        self._ensure_native_admission(context, backend, review=review)
        context.candidate_dir.mkdir(parents=True, exist_ok=True)
        for ref in task.inputs + task.resources:
            if _digest(ref.path) != ref.sha256:
                raise ControllerError("INPUT_CHANGED", "Task snapshot input changed")
        store.create_attempt(task)
        _durable(context.candidate_dir.parent / "task.json", task_to_dict(task))
        # Admission precedes create_attempt and every review/repair reservation.
        if not recovery and not charges_reserved:
            self._reserve(context, task, data, review=review)
        self._snapshot_files(context.candidate_dir.parent, json_value(task.inputs + task.resources))
        while True:
            store.append_event(context.run_id, task.attempt_id, "native_dispatch_intent", {"backend": backend, "review": review, "recovery": recovery})
            try:
                transport = self._adapter(context, task, backend, spec, recovery=recovery)
                break
            except NativeLaunchUnavailable as exc:
                # Popen failed before creating an adapter process. No native
                # input/submission exists; this alone permits operational fallback.
                next_backend = None if recovery else self._fallback_backend(context, backend, review=review)
                store.record_attempt_error(context.run_id, task.attempt_id, _error(exc), settled=True)
                store.append_event(context.run_id, task.attempt_id, "native_operational_failure", {"pre_execution": True, "backend": backend, "error": _error(exc), "next_backend": next_backend})
                if next_backend is None:
                    raise
                try:
                    self._ensure_native_admission(context, next_backend, review=False)
                except ControllerError as unavailable:
                    store.append_event(context.run_id, task.attempt_id, "native_fallback_unavailable", {"backend": next_backend, "error": _error(unavailable)})
                    raise
                store.append_event(context.run_id, task.attempt_id, "native_backend_unavailable", {"backend": "codex", "status": "unavailable_by_approved_scope", "task_enabled": False})
                context, task = self._alternate_attempt(context, task)
                context.candidate_dir.mkdir(parents=True, exist_ok=True)
                store.create_attempt(task)
                _durable(context.candidate_dir.parent / "task.json", task_to_dict(task))
                store.append_event(context.run_id, None, "item_prepared", {"step_id": context.step_id, "item_id": context.item_id, "task": task_to_dict(task), "data": json_value(data), "paths": json_value(context.paths)})
                backend = next_backend
        # The child independently rechecks this same route closure before code
        # loading and submission; also close the controller's post-Popen gap.
        if self.adapter_factory is None:
            try:
                self._native_admission(context)
            except BaseException:
                transport.close()
                raise
        active = _Active(context, task, transport, json_value(data["review"]) if review else None, backend, time.monotonic())
        self._active[task.attempt_id] = active
        if recovery:
            row = store._attempt(store.connection, context.run_id, task.attempt_id)
            binding = self._current_binding(context.run_id, task.attempt_id)
            active.phase = "reconcile"
            active.request_id = transport.send("reconcile", {} if backend == "omp" else {"binding": binding})
        else:
            payload = {"caller_family": context.request.caller_family, **{k: data["review"][k] for k in ("contract", "source", "focus")}} if review else None
            if review and backend == "omp":
                images = [json_value(ref) for ref in task.inputs if ref.media_type.startswith("image/")]
                if images:
                    payload["images"] = images
            active.request_id = transport.send("prepare", {"review": payload} if review else {"task": task_to_dict(task)})
        store.update_step(context.run_id, context.step_id, context.item_id, "executing", current_task=task.duty)
        return active

    def _current_binding(self, run_id, attempt_id):
        row = self._store._attempt(self._store.connection, run_id, attempt_id)
        binding = json.loads(row["binding_json"]) if row["binding_json"] else None
        for event in self._events(run_id, "native_binding_updated"):
            if event["attempt_id"] == attempt_id:
                binding = event["payload"]["binding"]
        return binding

    def _heartbeat(self, excluded=(), *, launch=False):
        for attempt_id, peer in list(self._active.items()):
            if attempt_id in excluded or peer.pending_frame is not None or peer.deferred_error is not None:
                continue
            # Source admission and teardown may service an existing reservation,
            # but must not launch a prepared peer during cohort preparation.
            if peer.phase == "prepare" and not launch:
                continue
            try:
                frame = self._drive(peer)
                if frame is not None:
                    peer.pending_frame = frame
            except Exception as exc:
                peer.deferred_error = exc

    def _close_transport(self, active):
        if isinstance(active.transport, NativeIPC):
            return active.transport.close(on_wait=lambda: self._heartbeat((active.task.attempt_id,)))
        return active.transport.close()

    def _drive(self, active):
        context, transport = active.context, active.transport
        store = self._store
        if active.deferred_error is not None:
            error, active.deferred_error = active.deferred_error, None
            raise error
        if store.is_cancelled(context.run_id) and not active.cancelled:
            active.cancelled = True
            transport.send("cancel", {})
            store.append_event(context.run_id, active.task.attempt_id, "native_cancellation_sent", {})
        if active.pending_frame is not None:
            frame, active.pending_frame = active.pending_frame, None
            return frame
        frame = transport.poll(0)
        if frame is None:
            timeout = context.step.policy.get("timeout_seconds", 1800)
            if active.review is None and time.monotonic() - active.started > timeout:
                raise ControllerError("NATIVE_TIMEOUT", "Native effects require reconciliation, not another attempt")
            if active.phase == "observe" and time.monotonic() >= active.next_poll:
                active.phase = "observing"
                active.request_id = transport.send("observe", {})
            return None
        status, body = frame["status"], frame["payload"]
        if status == "binding_required":
            binding = body.get("binding")
            expected_backend = "omp" if active.backend == "omp" else "discriminator" if active.review is not None else active.backend
            if not isinstance(binding, dict) or binding.get("backend") != expected_backend:
                raise ControllerError("NATIVE_BINDING_CONFLICT", "Reservation backend differs from this immutable execution attempt")
            store.bind_attempt(context.run_id, active.task.attempt_id, binding)
            # Database commit has completed on this owning thread before the ACK.
            transport.send("binding_ack", {"committed": True, "binding": binding}, request_id=frame["request_id"])
            return None
        if status == "binding_updated":
            previous = self._current_binding(context.run_id, active.task.attempt_id)
            updated = body.get("binding")
            if previous is None:
                raise ControllerError("UNBOUND_NATIVE_UPDATE", "Native identity update preceded durable binding")
            if not isinstance(updated, dict) or any(updated.get(key) != value for key, value in previous.items()):
                raise ControllerError("NATIVE_BINDING_CONFLICT", "Native update changed its immutable reservation identity")
            extra = set(updated) - set(previous)
            if extra and (active.backend != "codex" or extra != {"turn_id"} or type(updated["turn_id"]) is not str or not updated["turn_id"]):
                raise ControllerError("NATIVE_BINDING_CONFLICT", "Unexpected native identity fields")
            store.append_event(context.run_id, active.task.attempt_id, "native_binding_updated", {"binding": updated})
            return None
        store.append_event(context.run_id, active.task.attempt_id, "native_observation", frame)
        if body.get("locally_settled") is True or body.get("settled") is True:
            active.settled = True
            return frame
        if active.phase == "prepare" and status in ("prepared", "ready") and frame["error"] is None:
            if store.is_cancelled(context.run_id):
                active.cancelled = True
                transport.send("cancel", {})
            else:
                store.append_event(context.run_id, active.task.attempt_id, "native_launch_requested", {})
                active.request_id = transport.send("launch_with_binding_barrier", {})
            active.phase = "launch"
            return None
        if status in ("blocked", "failed", "cancelled", "unknown") or frame["error"] is not None:
            raise ControllerError("NATIVE_UNSETTLED", "Native result lacks explicit settlement; preserve and reconcile", {"reply": frame})
        if status == "completed":
            raise ControllerError("NATIVE_UNSETTLED", "A completed frame without observed settlement cannot be accepted")
        active.phase, active.next_poll = "observe", time.monotonic() + 0.1
        return None

    def _review_evidence(self, active, frame):
        context, task = active.context, active.task
        reply_path = context.candidate_dir.parent / "native-review.json"
        _durable(reply_path, frame)
        reply_ref = {"path": str(reply_path), "sha256": _digest(reply_path), "media_type": "application/json"}
        refs = []
        body = frame["payload"]
        producer = body.get("native_result") if active.backend == "omp" else body.get("result")
        if not isinstance(producer, dict):
            return reply_ref, refs
        for key in ("result_path", "receipt_path", "findings_path"):
            if not producer.get(key):
                continue
            source = _path(producer[key], regular=True)
            content = source.read_bytes()
            directory = context.candidate_dir.parent / "review-evidence"
            directory.mkdir(exist_ok=True)
            target = directory / (key + "-" + sha256(content).hexdigest())
            if target.exists():
                if target.read_bytes() != content:
                    raise ControllerError("REVIEW_EVIDENCE_CHANGED", "Retained native evidence changed")
            else:
                with target.open("xb") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
            refs.append({"name": key, "path": str(target), "sha256": sha256(content).hexdigest(), "media_type": "application/json" if key != "findings_path" else "text/plain"})
        bindings = [{"original_path": producer[ref["name"]], **{key: ref[key] for key in ("path", "sha256", "media_type")}} for ref in refs]
        aliases = self._native_aliases(context.run_id)
        if any(value["original_path"] in aliases and (aliases[value["original_path"]]["sha256"], aliases[value["original_path"]]["media_type"]) != (value["sha256"], value["media_type"]) for value in bindings):
            raise ControllerError("NATIVE_EVIDENCE_ALIAS_CONFLICT", "Original evidence path already binds other bytes")
        genuine = any(event["attempt_id"] == task.attempt_id for event in self._events(context.run_id, "native_review_result"))
        if bindings and genuine:
            self._store.append_event(context.run_id, task.attempt_id, "native_review_evidence_retained", {"attempt_id": task.attempt_id, "bindings": bindings})
        for ref in task.inputs + task.resources:
            if _digest(ref.path) != ref.sha256:
                raise ControllerError("REVIEW_EVIDENCE_CHANGED", "Review source/focus changed during invocation")
        if active.backend == "omp" and producer.get("status") == "reviewed":
            receipt = json.loads(_path(producer["receipt_path"], regular=True).read_text(encoding="utf-8"))
            binding = receipt.get("binding", {})
            original_binding = json.loads(self._store._attempt(self._store.connection, context.run_id, task.attempt_id)["binding_json"])
            source_ref = next(ref for ref in task.inputs if ref.path == active.review["source"])
            if (receipt.get("local_settled") is not True or receipt.get("publication_allowed") is not True
                    or binding.get("caller_family") != context.request.caller_family
                    or binding.get("artifact_class") != active.review["contract"]
                    or binding.get("source_sha256") != sha256(Path(source_ref.path).read_text(encoding="utf-8").encode("utf-8")).hexdigest()
                    or (original_binding.get("tool_call_id") is not None and binding.get("tool_call_id") != original_binding["tool_call_id"])
                    or receipt.get("result", {}).get("path") != producer["result_path"]
                    or receipt.get("result", {}).get("sha256") != _digest(producer["result_path"])
                    or any(receipt.get("compact", {}).get(key) != producer.get(key) for key in ("status", "verdict", "exitCode", "loop_should_continue", "caller_family"))):
                raise ControllerError("REVIEW_BINDING_MISMATCH", "Native receipt does not qualify this exact source/contract/caller")
        return reply_ref, refs

    def _accept(self, active, frame):
        context, task, store = active.context, active.task, self._store
        body = frame["payload"]
        if active.review is not None:
            # Pre-execution NO_REVIEW can be a report's tooling outcome, but it
            # cannot be forged into a bound review or a worker TaskResult.
            bound = store._attempt(store.connection, context.run_id, task.attempt_id)["binding_json"]
            if bound is None:
                failure = frame.get("error") or {"code": "NO_REVIEW", "message": "Native review unavailable before binding", "details": {}}
                store.record_attempt_error(context.run_id, task.attempt_id, failure, settled=True)
                store.append_event(context.run_id, task.attempt_id, "native_review_unavailable", {"reply": frame, "locally_settled": True})
            else:
                store.record_native_review(context.run_id, task.attempt_id, frame)
            gate = review_gate(frame)
            if bound is None:
                gate = {"accepted": False, "tooling_failure": True, "reason": "NO_REVIEW"}
            try:
                reply_ref, evidence = self._review_evidence(active, frame)
            except Exception as exc:
                store.append_event(context.run_id, task.attempt_id, "review_evidence_rejected", {"error": _error(exc)})
                gate = {"accepted": False, "tooling_failure": True,
                        "reason": "needs_orchestrator_review" if gate["reason"] == "needs_orchestrator_review" else "REVIEW_EVIDENCE_INVALID"}
                reply_path = context.candidate_dir.parent / "native-review.json"
                _durable(reply_path, frame)
                reply_ref, evidence = {"path": str(reply_path), "sha256": _digest(reply_path), "media_type": "application/json"}, []
            result_data = {"native_review": frame, "native_review_ref": reply_ref,
                           "producer_evidence": [{key: ref[key] for key in ("path", "sha256", "media_type")} for ref in evidence],
                           "review_gate": gate, "artifact_id": task.artifact_id}
            result_data["attempt_id"] = task.attempt_id
            if gate["tooling_failure"]:
                result_data.update(tooling_failure=True, run_status="failed")
            if not gate["accepted"] and not context.step.policy.get("allow_review_fail", False):
                raise ControllerError(gate["reason"], "Native publication review blocked immediately", {"review": frame})
            artifacts = [{"name": "native-review", **reply_ref}, *evidence]
            return [{**ref, "step_id": context.step_id, "item_id": context.item_id, "attempt_id": task.attempt_id} for ref in artifacts], result_data
        if body.get("binding") != self._current_binding(context.run_id, task.attempt_id):
            raise ControllerError("NATIVE_BINDING_CONFLICT", "Completed worker is not the durably reserved native identity")
        carrier = body.get("native_result") if active.backend == "omp" else body.get("result")
        if active.backend == "omp":
            if not isinstance(carrier, dict) or any(carrier.get(k) != v for k, v in (("source", "caller"), ("mode", "strict"), ("status", "valid"))):
                raise ControllerError("INVALID_NATIVE_CARRIER", "Required caller/strict/valid result is absent")
            settlement = body.get("native_settlement", {})
            if settlement.get("status") != "completed" or settlement.get("end_time") is None:
                raise ControllerError("NATIVE_JOB_FAILED", "Actual native job did not settle successfully")
            carrier = carrier.get("data")
        if frame["status"] != "completed" or frame["error"] is not None:
            raise ControllerError("NATIVE_EXECUTION_FAILED", "Native transport did not complete", {"reply": frame})
        result = result_from_dict(carrier)
        validation = validate_result(result, task)
        if not validation.valid:
            raise ControllerError("RESULT_REJECTED", "Native result violates task identity/schema", {"errors": validation.errors})
        store.record_result(context.run_id, task.attempt_id, result)
        if store.is_cancelled(context.run_id):
            raise ControllerError("CANCELLED", "Late result retained but cannot advance")
        if result.status != "completed":
            if result.decision:
                store.record_decision(context.run_id, result.decision["key"], json_value(result.decision))
            raise ControllerError("TASK_" + result.status.upper(), "Worker did not complete", {"result": carrier})
        for ref in task.inputs + task.resources:
            if _digest(ref.path) != ref.sha256:
                raise ControllerError("INPUT_CHANGED", "Supporting evidence changed during execution")
        artifacts, outputs = [], {}
        validation_context = replace(context, data={**context.data, "task": task, "task_result": result})
        for ref in result.artifacts:
            spec = next(s for s in task.outputs if s.name == ref.name)
            path = _path(Path(task.candidate_dir) / ref.relative_path, root=Path(task.candidate_dir), regular=True)
            if _digest(path) != ref.sha256:
                raise ControllerError("ARTIFACT_CHANGED", "Actual candidate bytes differ from result")
            if self.artifact_validator is not None:
                valid = self.artifact_validator(spec, path, validation_context)
                if not valid.valid:
                    raise ControllerError("ARTIFACT_REJECTED", "Registered artifact validator refused output", {"errors": valid.errors})
            if ref.media_type == "application/json":
                outputs[ref.name] = json.loads(path.read_text(encoding="utf-8"))
            artifacts.append({"name": ref.name, "path": str(path), "sha256": ref.sha256, "media_type": ref.media_type,
                              "step_id": context.step_id, "item_id": context.item_id, "attempt_id": task.attempt_id})
        if result.verification == "FAIL" and not context.step.policy.get("allow_verification_fail", False):
            raise ControllerError("VERIFICATION_FAIL", "Semantic FAIL is completed execution, not acceptance")
        if store.is_cancelled(context.run_id):
            raise ControllerError("CANCELLED", "Cancellation during validation blocks acceptance")
        store.append_event(context.run_id, task.attempt_id, "artifacts_validated", {"artifacts": artifacts, "verification": result.verification})
        return artifacts, {"result": result_to_dict(result), "verification": result.verification, "outputs": outputs,
                           "attempt_id": task.attempt_id, "artifact_id": task.artifact_id}

    def _retain_stopped_result(self, active, frame):
        run_id, attempt_id = active.context.run_id, active.task.attempt_id
        self._store.append_event(run_id, attempt_id, "native_terminal_during_settlement", frame)
        self._stopped_results.append((active, frame))

    def _drain_stopped_results(self):
        # Settlement must keep servicing peers too. Retain observations now,
        # but never enter an unbounded domain validator beside live native work.
        if any(not peer.settled for peer in self._active.values()):
            return
        pending, self._stopped_results = self._stopped_results, []
        for active, frame in pending:
            run_id, attempt_id = active.context.run_id, active.task.attempt_id
            try:
                self._accept(active, frame)
            except Exception as exc:
                self._store.append_event(run_id, attempt_id, "stopped_result_not_accepted", {"error": _error(exc)})
                row = self._store._attempt(self._store.connection, run_id, attempt_id)
                if row["settled_at"] is None:
                    self._store.record_attempt_error(run_id, attempt_id, json.loads(row["error_json"]) if row["error_json"] else _error(exc), settled=active.settled)

    def _settle_all(self, run_id, attempt_ids=None):
        unresolved, failures = [], []
        owned = {key: value for key, value in self._active.items() if attempt_ids is None or key in attempt_ids}
        deadline = time.monotonic() + 15
        for active in owned.values():
            if not active.settled:
                try:
                    active.transport.send("cancel", {})
                    active.cancelled = True
                except Exception as exc:
                    failures.append(_error(exc))
        while any(not a.settled for a in owned.values()) and time.monotonic() < deadline:
            for active in owned.values():
                if active.settled:
                    continue
                try:
                    frame = self._drive(active)
                    if frame is not None:
                        self._retain_stopped_result(active, frame)
                except Exception as exc:
                    failures.append(_error(exc))
                    # Do not interpret a transport failure as observed worker settlement.
                    active.next_poll = deadline
            self._heartbeat(tuple(owned))
            time.sleep(0.05)
        for active in owned.values():
            if active.pending_frame is not None:
                frame, active.pending_frame = active.pending_frame, None
                self._retain_stopped_result(active, frame)
            if not active.settled:
                unresolved.append(active.task.attempt_id)
            try:
                receipt = self._close_transport(active)
                self._store.append_event(run_id, active.task.attempt_id, "adapter_process_settled", receipt)
                if not receipt.get("process_settled", False) and active.task.attempt_id not in unresolved:
                    unresolved.append(active.task.attempt_id)
                if not receipt.get("graceful_eof_exit", False):
                    failures.append({"code": "ADAPTER_TEARDOWN_FAILED", "message": "Adapter did not naturally exit cleanly", "details": receipt})
            except Exception as exc:
                failures.append(_error(exc))
        for attempt_id in owned:
            self._active.pop(attempt_id, None)
        self._store.append_event(run_id, None, "owned_work_settlement", {"unresolved_attempts": unresolved, "failures": failures})
        self._drain_stopped_results()
        return unresolved, failures

    def _execute(self, request, run_id, spec, recovering=False):
        store = self._store
        self._assert_public_ledger(store, run_id)
        self._active = {}
        self._stopped_results = []
        prepared, blocked, final_error = {}, {}, None
        try:
            if recovering:
                self._check_resume_native_admission(request, run_id)
            store.update_run(run_id, "executing")
            if recovering:
                blocked.update(self._recover(request, run_id, spec))
                saved = {e["payload"]["task"]["attempt_id"]: e["payload"] for e in self._events(run_id, "item_prepared")}
                for active in self._active.values():
                    key = (active.context.step_id, active.context.item_id)
                    prepared[key] = (active.context, active.task, saved[active.task.attempt_id]["data"])
            while True:
                if store.is_cancelled(run_id):
                    raise ControllerError("CANCELLED", "Cancellation requested")
                if self._active:
                    # Drain a finite native cohort before arbitrary source,
                    # artifact or domain work. A pulse between validators cannot
                    # service a reservation arriving inside a slow validator.
                    self._heartbeat(launch=True)
                    if any(not peer.settled and peer.deferred_error is None for peer in self._active.values()):
                        time.sleep(0.05)
                        continue
                records = self._dependency_records(run_id)
                completed = {(r["step_id"], r["item_id"]) for r in records}
                done_steps = set()
                memberships = {e["payload"]["step_id"]: e["payload"]["items"] for e in self._events(run_id, "step_membership")}
                for step in spec.steps:
                    if step.step_id in memberships and all((step.step_id, item["item_id"]) in completed for item in memberships[step.step_id]):
                        done_steps.add(step.step_id)
                maximum = spec.metadata.get("max_concurrency", 4)
                if type(maximum) is not int or maximum < 1:
                    raise ControllerError("INVALID_GRAPH", "Positive workflow concurrency required")
                changed = False
                scheduling = not self._active
                for step in (spec.steps if scheduling else ()):
                    if step.step_id in done_steps or not set(step.dependencies) <= done_steps:
                        continue
                    if step.step_id not in memberships:
                        items = self._members(step, records, spec)
                        store.append_event(run_id, None, "step_membership", {"step_id": step.step_id, "items": items})
                        memberships[step.step_id] = items
                        changed = True
                    # Membership is immutable; a crash while materializing rows must
                    # fill only missing rows, never recalculate/reuse prior ordinals.
                    for item in memberships[step.step_id]:
                        existing = store.connection.execute("SELECT 1 FROM wf_steps WHERE run_id=? AND step_id=? AND item_id=?", (run_id, step.step_id, item["item_id"])).fetchone()
                        if existing is None:
                            ordinal = store.connection.execute("SELECT COALESCE(MAX(ordinal),-1)+1 FROM wf_steps WHERE run_id=?", (run_id,)).fetchone()[0]
                            store.create_step(run_id, step.step_id, item["item_id"], ordinal, dependencies=step.dependencies, policy=json_value(step.policy))
                    step_active = sum(a.context.step_id == step.step_id for a in self._active.values())
                    for item in memberships[step.step_id]:
                        key = (step.step_id, item["item_id"])
                        if key in completed or key in blocked or any((a.context.step_id, a.context.item_id) == key for a in self._active.values()):
                            continue
                        if step_active >= step.concurrency or len(self._active) >= maximum:
                            break
                        row = dict(store.connection.execute("SELECT * FROM wf_steps WHERE run_id=? AND step_id=? AND item_id=?", (run_id, *key)).fetchone())
                        try:
                            if key not in prepared:
                                context = self._context(request, run_id, step, row, item, spec)
                                context.candidate_dir.mkdir(parents=True, exist_ok=True)
                                result = step.prepare_handler(context)
                                if not isinstance(result, PreparedStep):
                                    raise ControllerError("INVALID_PREPARATION", "Registered handler must return PreparedStep")
                                local = result.local_result or {"artifacts": [], "data": {}}
                                data = json_value(local.get("data", {}))
                                if result.status == "skip":
                                    self._finish(context, data={"skip_reason": result.skip_reason}, skipped=True)
                                    changed = True
                                    continue
                                if result.decision:
                                    saved = store.connection.execute("SELECT answer_json FROM wf_decisions WHERE run_id=? AND decision_key=?", (run_id, result.decision["key"])).fetchone()
                                    if saved is None or saved[0] is None:
                                        store.record_decision(run_id, result.decision["key"], json_value(result.decision))
                                        blocked[key] = {"code": "DECISION_REQUIRED", "message": result.decision["question"], "details": {}}
                                        store.update_step(run_id, *key, "blocked")
                                        continue
                                if result.status == "blocked":
                                    raise ControllerError(data.get("error", {}).get("code", "PREPARATION_BLOCKED"), result.skip_reason or data.get("error", {}).get("message", "Registered handler blocked"), data.get("error", {}).get("details", {}))
                                if result.status == "local_result":
                                    artifacts, data = self._validate_local(context, local)
                                    receipts = self._publish(context, data, spec)
                                    if receipts:
                                        data["publication_receipts"] = receipts
                                    actions = self._external_effects(context, data, spec)
                                    if actions:
                                        data["external_action_results"] = actions
                                    self._finish(context, artifacts, data)
                                    changed = True
                                    continue
                                context = replace(context, data={**context.data, **data, "native_evidence_aliases": self._native_aliases(run_id)})
                                tasks = result.tasks
                                if step.kind == "review":
                                    if tasks:
                                        raise ControllerError("INVALID_REVIEW", "Native review cannot be wrapped in a generic worker")
                                    tasks = (self._review_task(context, data),)
                                if not tasks:
                                    raise ControllerError("EMPTY_TASK_SET", "Ready native step has no explicit task")
                                if len(tasks) != 1:
                                    raise ControllerError("INVALID_TASK_SET", "One native attempt per declared item; unroll sequential operations in the source graph")
                                prepared[key] = (context, tasks[0], data)
                                store.append_event(run_id, None, "item_prepared", {"step_id": step.step_id, "item_id": item["item_id"], "task": task_to_dict(tasks[0]), "data": json_value(data), "paths": json_value(context.paths)})
                            context, task, data = prepared[key]
                            self._start(context, task, data, spec, review=step.kind == "review")
                            step_active += 1
                            changed = True
                        except Exception as exc:
                            blocked[key] = _error(exc)
                            store.update_step(run_id, *key, "blocked", error_summary=str(exc))
                self._heartbeat(launch=True)
                accepting = all(peer.settled for peer in self._active.values())
                self._drain_stopped_results()
                for attempt_id, active in list(self._active.items()):
                    key = (active.context.step_id, active.context.item_id)
                    try:
                        if not accepting and active.deferred_error is None:
                            continue
                        frame = self._drive(active)
                        if frame is None:
                            continue
                        artifacts, result_data = self._accept(active, frame)
                        receipt = self._close_transport(active)
                        store.append_event(run_id, attempt_id, "adapter_process_settled", receipt)
                        if not receipt.get("graceful_eof_exit", False):
                            raise ControllerError("ADAPTER_TEARDOWN_FAILED", "Native result settled but finite host failed teardown", receipt)
                        del self._active[attempt_id]
                        data = {**prepared[key][2], **result_data}
                        self._finish(active.context, artifacts, data)
                        changed = True
                    except Exception as exc:
                        blocked[key] = _error(exc)
                        row = store._attempt(store.connection, run_id, attempt_id)
                        if row["settled_at"] is None:
                            store.record_attempt_error(run_id, attempt_id, _error(exc), settled=active.settled)
                        if active.settled and active.context.step.policy.get("allow_task_failure", False) and active.review is None and not store.is_cancelled(run_id):
                            receipt = self._close_transport(active)
                            store.append_event(run_id, attempt_id, "adapter_process_settled", receipt)
                            if receipt.get("graceful_eof_exit", False):
                                self._active.pop(attempt_id, None)
                                failed_data = {**prepared[key][2], "tooling_failure": True, "run_status": "failed", "error": _error(exc), "attempt_id": attempt_id}
                                store.append_event(run_id, attempt_id, "report_task_failure", {"error": _error(exc)})
                                self._finish(active.context, data=failed_data)
                                blocked.pop(key, None)
                                changed = True
                                continue
                        store.update_step(run_id, *key, "blocked", error_summary=str(exc))
                        # Settle/cancel this owned transport before any blocked return.
                        unresolved, failures = self._settle_all(run_id, [attempt_id])
                        if unresolved or failures:
                            blocked[key]["details"]["settlement"] = {"unresolved": unresolved, "failures": failures}
                        changed = True
                        break
                if not self._active and all(step.step_id in done_steps for step in spec.steps):
                    records = self._records(run_id)
                    final_status = "failed" if any(r.get("data", {}).get("run_status") == "failed" for r in records) else "done"
                    store.update_run(run_id, final_status)
                    return self._outcome(store, run_id, final_status)
                if not self._active and not changed:
                    if blocked:
                        final_error = next(iter(blocked.values()))
                        break
                    raise ControllerError("GRAPH_STALLED", "No declared node can progress")
                time.sleep(0.05 if self._active else 0)
        except Exception as exc:
            final_error = _error(exc)
        unresolved, failures = self._settle_all(run_id)
        if unresolved or failures:
            final_error = final_error or {"code": "NATIVE_UNSETTLED", "message": "Owned native work needs reconciliation", "details": {}}
            final_error["details"]["settlement"] = {"unresolved": unresolved, "failures": failures}
        status = "cancelled" if store.is_cancelled(run_id) and not unresolved else "blocked"
        store.append_event(run_id, None, "controller_blocked", {"error": final_error})
        store.update_run(run_id, status)
        return self._outcome(store, run_id, status, error=final_error)

    def _restore_local_files(self, context, local):
        missing = []
        for ref in local["artifacts"]:
            path = _path(ref["path"], root=context.candidate_dir)
            if not path.exists():
                missing.append(ref)
            elif _digest(path) != ref["sha256"]:
                raise ControllerError("ARTIFACT_CHANGED", "Modified LOCAL evidence is never restored over")
        if not missing:
            return
        operations = {effect["operation_id"] for effect in local.get("publications", ())}
        desired = []
        for row in self._store.connection.execute("SELECT * FROM wf_publications WHERE run_id=?", (context.run_id,)):
            if row["operation_id"] in operations and Path(row["target"]).exists() and _digest(row["target"]) == row["desired_sha256"]:
                desired.append(row["operation_id"])
        if not desired:
            raise ControllerError("INPUT_UNAVAILABLE", "Missing LOCAL candidates require an already-desired journalled effect")
        for ref in missing:
            snapshot = context.candidate_dir.parent / "snapshots" / ref["sha256"]
            if _digest(snapshot) != ref["sha256"]:
                raise ControllerError("SNAPSHOT_CHANGED", "Retained LOCAL evidence snapshot changed")
            target = _path(ref["path"], root=context.candidate_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as handle:
                handle.write(snapshot.read_bytes())
                handle.flush()
                os.fsync(handle.fileno())
            self._store.append_event(context.run_id, None, "local_artifact_restored", {"step_id": context.step_id,
                                     "item_id": context.item_id, "artifact": ref, "desired_operations": desired,
                                     "basis": "Exact retained LOCAL bytes; canonical desired effect is not replayed"})

    def _recover(self, request, run_id, spec):
        """Accept only recorded settlements; never resubmit unknown native work."""
        completed = {(r["step_id"], r["item_id"]) for r in self._dependency_records(run_id)}
        root = self._root(request, run_id)
        original = json.loads((root / "definition.json").read_text(encoding="utf-8"))
        self._assert_public_ledger(self._store, run_id)
        self._check_resume_native_admission(request, run_id)
        blocked = {}
        self._check_native_contexts(run_id, root)
        validated = {event["payload"]["candidate_dir"] for event in self._events(run_id, "local_output_validated")}
        for event in self._events(run_id, "local_result_prepared"):
            prepared = event["payload"]
            key = (prepared["step_id"], prepared["item_id"])
            if key in completed or prepared["candidate_dir"] in validated:
                continue
            step = next(step for step in spec.steps if step.step_id == key[0])
            row = self._store.connection.execute("SELECT * FROM wf_steps WHERE run_id=? AND step_id=? AND item_id=?", (run_id, *key)).fetchone()
            if row is None or row["ordinal"] != prepared["ordinal"] or row["current_attempt_id"] is not None or prepared["definition_sha256"] != self._store.read_run(run_id)["definition_sha256"]:
                raise ControllerError("LOCAL_RECOVERY_CONFLICT", "Prepared LOCAL identity no longer matches its source-bound step")
            candidate = _path(prepared["candidate_dir"], root=self._root(request, run_id))
            path = _path(prepared["local_result_path"], root=candidate.parent, regular=True)
            if _digest(path) != prepared["local_result_sha256"]:
                raise ControllerError("LOCAL_SNAPSHOT_CONFLICT", "Immutable prepared LOCAL result changed")
            local_result = json.loads(path.read_text(encoding="utf-8"))
            context = self._context(request, run_id, step, dict(row), {"item_id": key[1]}, spec,
                                    attempt_id=candidate.parent.name, data=local_result.get("data", {}))
            if context.candidate_dir != candidate:
                raise ControllerError("LOCAL_RECOVERY_CONFLICT", "Prepared LOCAL candidate identity changed")
            self._validate_local(context, local_result)
            validated.add(str(candidate))
        for event in self._events(run_id, "local_output_validated"):
            local = event["payload"]
            key = (local["step_id"], local["item_id"])
            if key in completed:
                continue
            step = next(s for s in spec.steps if s.step_id == key[0])
            row = self._store.connection.execute("SELECT * FROM wf_steps WHERE run_id=? AND step_id=? AND item_id=?", (run_id, *key)).fetchone()
            if row is None or row["ordinal"] != local["ordinal"] or row["current_attempt_id"] is not None:
                raise ControllerError("LOCAL_RECOVERY_CONFLICT", "Local output no longer matches its durable subject")
            candidate = _path(local["candidate_dir"], root=self._root(request, run_id))
            context = self._context(request, run_id, step, dict(row), {"item_id": key[1]}, spec,
                                    attempt_id=candidate.parent.name, data=local["data"])
            if context.candidate_dir != candidate:
                raise ControllerError("LOCAL_RECOVERY_CONFLICT", "Local candidate identity changed")
            self._restore_local_files(context, local)
            artifacts = []
            for ref in local["artifacts"]:
                if _digest(_path(ref["path"], root=candidate, regular=True)) != ref["sha256"]:
                    raise ControllerError("ARTIFACT_CHANGED", "Local recovery candidate changed")
                artifacts.append({**ref, "step_id": key[0], "item_id": key[1], "attempt_id": None})
            self._validate_projections(context, local["data"], artifacts)
            self._register_subjects(context, local["data"])
            receipts = self._publish(context, local["data"], spec, intents=local.get("publications", []))
            data = {**local["data"], **({"publication_receipts": receipts} if receipts else {})}
            actions = self._external_effects(context, data, spec)
            if actions:
                data["external_action_results"] = actions
            self._finish(context, artifacts, data)
            completed.add(key)
        prepared = {(e["payload"]["step_id"], e["payload"]["item_id"]): e["payload"] for e in self._events(run_id, "item_prepared")}
        for row in self._store.connection.execute("SELECT * FROM wf_attempts WHERE run_id=? ORDER BY created_at,attempt_id", (run_id,)).fetchall():
            key = (row["step_id"], row["item_id"])
            if key in completed:
                continue
            operational = [event["payload"] for event in self._events(run_id, "native_operational_failure") if event["attempt_id"] == row["attempt_id"]]
            if operational and operational[-1].get("pre_execution") is True and row["settled_at"] is not None:
                continue
            step = next(s for s in spec.steps if s.step_id == row["step_id"])
            task = task_from_dict(json.loads(row["task_json"]))
            saved = prepared.get(key)
            if saved is None:
                raise ControllerError("RECOVERY_EVIDENCE_MISSING", "Attempt lacks immutable preparation evidence")
            membership = next(e["payload"]["items"] for e in self._events(run_id, "step_membership") if e["payload"]["step_id"] == step.step_id)
            item = next(i for i in membership if i["item_id"] == row["item_id"])
            context = self._context(request, run_id, step, {"item_id": row["item_id"], "ordinal": self._store.connection.execute("SELECT ordinal FROM wf_steps WHERE run_id=? AND step_id=? AND item_id=?", (run_id, *key)).fetchone()[0]}, item, spec, attempt_id=task.attempt_id, data=saved["data"])
            native_reviews = [e["payload"]["reply"] for e in self._events(run_id, "native_review_result") if e["attempt_id"] == task.attempt_id]
            observations = [e["payload"] for e in self._events(run_id, "native_observation") if e["attempt_id"] == task.attempt_id and (e["payload"]["payload"].get("settled") is True or e["payload"]["payload"].get("locally_settled") is True)]
            if row["result_json"] is not None or native_reviews:
                if not observations and not native_reviews:
                    raise ControllerError("RECOVERY_EVIDENCE_MISSING", "Result lacks actual native settlement")
                frame = native_reviews[-1] if native_reviews else observations[-1]
                active = _Active(context, task, None, saved["data"].get("review") if step.kind == "review" else None, self._attempt_backend(context, task, step.kind == "review"), time.monotonic(), settled=True)
                artifacts, data = self._accept(active, frame)
                receipts = [e["payload"] for e in self._events(run_id, "adapter_process_settled") if e["attempt_id"] == task.attempt_id]
                if not receipts or not receipts[-1].get("graceful_eof_exit"):
                    raise ControllerError("RECOVERY_TEARDOWN_UNCONFIRMED", "A settled result does not prove adapter process teardown")
                self._finish(context, artifacts, {**saved["data"], **data})
            else:
                # Reconciliation may inspect known thread/session receipts, never start a turn.
                active = self._start(context, task, saved["data"], spec, review=step.kind == "review", recovery=True)
                deadline = time.monotonic() + 15
                frame = None
                while frame is None and time.monotonic() < deadline:
                    frame = self._drive(active)
                    if frame is None:
                        time.sleep(0.05)
                if frame is None:
                    raise ControllerError("RECOVERY_UNKNOWN", "Native submission remains unknown; no retry authorized")
                artifacts, data = self._accept(active, frame)
                receipt = self._close_transport(active)
                self._store.append_event(run_id, task.attempt_id, "adapter_process_settled", receipt)
                del self._active[task.attempt_id]
                if not receipt.get("graceful_eof_exit"):
                    raise ControllerError("RECOVERY_TEARDOWN_UNCONFIRMED", "Recovery adapter did not exit cleanly")
                self._finish(context, artifacts, {**saved["data"], **data})
        return blocked

