"""Version-one wire contracts. No native, domain, database or third-party imports."""
from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from hashlib import sha256
import json
import math
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
from types import MappingProxyType
from typing import Any, Callable, Mapping


class ContractError(ValueError):
    """A closed wire contract or its ownership/identity invariant was violated."""


def _freeze(value: Any, depth: int = 0) -> Any:
    if depth > 64:
        raise ContractError("JSON nesting exceeds 64 levels")
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise ContractError("JSON object keys must be strings")
        return MappingProxyType({key: _freeze(item, depth + 1) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item, depth + 1) for item in value)
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise ContractError("Not a finite JSON value")


def _wire(value: Any) -> Any:
    if is_dataclass(value):
        return {field.name: _wire(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {key: _wire(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_wire(item) for item in value]
    return value


def _compact(value: Any) -> bytes:
    return json.dumps(_wire(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def semantic_hash(task: TaskSpec | Mapping[str, Any]) -> str:
    data = _wire(task)
    if not isinstance(data, dict):
        raise ContractError("Task must be an object")
    data.pop("semantic_sha256", None)
    return sha256(_compact(data)).hexdigest()


def _string(value: Any, label: str) -> None:
    if type(value) is not str or not value or "\x00" in value:
        raise ContractError(f"{label}: expected nonempty NUL-free string")


def _digest(value: Any, label: str) -> None:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ContractError(f"{label}: expected lowercase SHA256")


def _relative(value: Any) -> None:
    _string(value, "relative_path")
    if "\\" in value or ":" in value or value.startswith("/") or any(part in ("", ".", "..") for part in value.split("/")):
        raise ContractError("Output path must be a normalized contained relative POSIX path")


def _absolute(value: Any) -> None:
    _string(value, "path")
    if not (PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute()):
        raise ContractError("Execution paths must be absolute")
    if ".." in value.replace("\\", "/").split("/"):
        raise ContractError("Execution paths must not traverse parents")


def _within(child: str, root: str) -> bool:
    windows = PureWindowsPath(child).is_absolute()
    cls = PureWindowsPath if windows else PurePosixPath
    try:
        cls(child).relative_to(cls(root))
        return True
    except ValueError:
        return False


def _closed(data: Any, names: set[str]) -> None:
    if not isinstance(data, Mapping) or set(data) != names:
        raise ContractError(f"Expected exactly fields {sorted(names)}")


def _version(value: Any) -> None:
    if type(value) is not int or value != 1:
        raise ContractError("schema_version must be integer 1")


@dataclass(frozen=True)
class InputRef:
    path: str
    sha256: str
    media_type: str

    def __post_init__(self):
        _absolute(self.path)
        _digest(self.sha256, "sha256")
        _string(self.media_type, "media_type")


@dataclass(frozen=True)
class OutputSpec:
    name: str
    relative_path: str
    media_type: str
    required: bool

    def __post_init__(self):
        _string(self.name, "name")
        _relative(self.relative_path)
        _string(self.media_type, "media_type")
        if type(self.required) is not bool:
            raise ContractError("required must be bool")


@dataclass(frozen=True)
class ArtifactRef:
    name: str
    relative_path: str
    sha256: str
    media_type: str

    def __post_init__(self):
        _string(self.name, "name")
        _relative(self.relative_path)
        _digest(self.sha256, "sha256")
        _string(self.media_type, "media_type")


def _refs(value: Any, cls: type) -> tuple:
    if not isinstance(value, (list, tuple)):
        raise ContractError("Expected an array")
    if not all(isinstance(item, cls) for item in value):
        raise ContractError(f"Expected {cls.__name__} array")
    return tuple(value)


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ContractError("Expected string array")
    for item in value:
        _string(item, "array item")
    if len(set(value)) != len(value):
        raise ContractError("Duplicate string array members")
    return tuple(value)


@dataclass(frozen=True)
class TaskSpec:
    schema_version: int
    run_id: str
    step_id: str
    item_id: str
    attempt_id: str
    duty: str
    instructions: str
    inputs: tuple[InputRef, ...]
    resources: tuple[InputRef, ...]
    candidate_dir: str
    outputs: tuple[OutputSpec, ...]
    allowed_actions: tuple[str, ...]
    read_roots: tuple[str, ...]
    write_roots: tuple[str, ...]
    result_schema: Mapping[str, Any]
    artifact_id: str
    review_contract: str | None
    artifact_tier: str
    semantic_sha256: str

    def __post_init__(self):
        _version(self.schema_version)
        for name in ("run_id", "step_id", "item_id", "attempt_id", "duty", "instructions", "artifact_id", "artifact_tier"):
            _string(getattr(self, name), name)
        for name, cls in (("inputs", InputRef), ("resources", InputRef), ("outputs", OutputSpec)):
            object.__setattr__(self, name, _refs(getattr(self, name), cls))
        for name in ("allowed_actions", "read_roots", "write_roots"):
            object.__setattr__(self, name, _strings(getattr(self, name)))
        _absolute(self.candidate_dir)
        for root in self.read_roots + self.write_roots:
            _absolute(root)
        if any(not _within(root, self.candidate_dir) for root in self.write_roots):
            raise ContractError("Writes must be confined to this attempt candidate_dir")
        if (self.write_roots or self.outputs) and not any(_within(self.candidate_dir, root) for root in self.write_roots):
            raise ContractError("candidate_dir needs explicit write authority")
        for ref in self.inputs + self.resources:
            if not any(_within(ref.path, root) for root in self.read_roots):
                raise ContractError("Input/resource outside declared read roots")
        if len({output.name for output in self.outputs}) != len(self.outputs) or len({output.relative_path for output in self.outputs}) != len(self.outputs):
            raise ContractError("Duplicate output name or path")
        if self.review_contract is not None:
            _string(self.review_contract, "review_contract")
        if _compact(self.result_schema) != _compact(task_result_schema()):
            raise ContractError("result_schema must be the closed TaskResult schema")
        object.__setattr__(self, "result_schema", _freeze(self.result_schema))
        _digest(self.semantic_sha256, "semantic_sha256")
        if semantic_hash(self) != self.semantic_sha256:
            raise ContractError("Task semantic hash mismatch")


@dataclass(frozen=True)
class TaskResult:
    schema_version: int
    run_id: str
    step_id: str
    item_id: str
    attempt_id: str
    status: str
    verification: str | None
    artifacts: tuple[ArtifactRef, ...]
    task_sha256: str
    evidence: tuple[InputRef, ...]
    error: Mapping[str, Any] | None
    decision: Mapping[str, Any] | None

    def __post_init__(self):
        _version(self.schema_version)
        for name in ("run_id", "step_id", "item_id", "attempt_id"):
            _string(getattr(self, name), name)
        if type(self.status) is not str or self.status not in ("completed", "blocked", "failed", "cancelled"):
            raise ContractError("Invalid result status")
        if self.verification is not None and (type(self.verification) is not str or self.verification not in ("PASS", "FAIL")):
            raise ContractError("Invalid verification")
        if self.status != "completed" and self.verification is not None:
            raise ContractError("Uncompleted task cannot assert verification")
        _digest(self.task_sha256, "task_sha256")
        object.__setattr__(self, "artifacts", _refs(self.artifacts, ArtifactRef))
        object.__setattr__(self, "evidence", _refs(self.evidence, InputRef))
        if len({a.name for a in self.artifacts}) != len(self.artifacts) or len({a.relative_path for a in self.artifacts}) != len(self.artifacts):
            raise ContractError("Duplicate artifact name or path")
        if self.error is not None:
            _closed(self.error, {"code", "message", "details"})
            _string(self.error["code"], "error.code")
            _string(self.error["message"], "error.message")
            if not isinstance(self.error["details"], Mapping):
                raise ContractError("error.details must be object")
            object.__setattr__(self, "error", _freeze(self.error))
        if self.decision is not None:
            _closed(self.decision, {"key", "question", "options"})
            _string(self.decision["key"], "decision.key")
            _string(self.decision["question"], "decision.question")
            options = _strings(self.decision["options"])
            if not options:
                raise ContractError("Decision options must not be empty")
            object.__setattr__(self, "decision", _freeze(self.decision))
        if self.status == "completed" and (self.error is not None or self.decision is not None):
            raise ContractError("Completed result cannot carry error or decision")
        if self.status == "failed" and self.error is None:
            raise ContractError("Failed result must identify its error")


def _object(properties: dict) -> dict:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def task_result_schema() -> dict:
    """Fresh, closed native structured-output schema; semantic checks remain mandatory."""
    text = {"type": "string", "minLength": 1}
    digest = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
    path = {"type": "string", "pattern": r"^[^/\\:]+(?:/[^/\\:]+)*$"}
    input_ref = _object({"path": text, "sha256": digest, "media_type": text})
    artifact = _object({"name": text, "relative_path": path, "sha256": digest, "media_type": text})
    error = _object({"code": text, "message": text, "details": _object({})})
    decision = _object({"key": text, "question": text, "options": {"type": "array", "items": text, "minItems": 1}})
    # Native strict providers require closed objects; richer diagnostics remain in adapter evidence.
    return _object({"schema_version": {"type": "integer", "const": 1}, "run_id": text, "step_id": text, "item_id": text, "attempt_id": text,
                    "status": {"type": "string", "enum": ["completed", "blocked", "failed", "cancelled"]},
                    "verification": {"type": ["string", "null"], "enum": ["PASS", "FAIL", None]},
                    "artifacts": {"type": "array", "items": artifact}, "task_sha256": digest,
                    "evidence": {"type": "array", "items": input_ref},
                    "error": {"anyOf": [error, {"type": "null"}]}, "decision": {"anyOf": [decision, {"type": "null"}]}})


def _decode(cls: type, data: Any):
    _closed(data, {field.name for field in fields(cls)})
    return cls(**data)


def task_from_dict(data: Mapping[str, Any]) -> TaskSpec:
    _closed(data, {field.name for field in fields(TaskSpec)})
    copied = dict(data)
    for name in ("allowed_actions", "read_roots", "write_roots"):
        if type(copied[name]) is not list:
            raise ContractError(f"{name}: expected JSON array")
    if not isinstance(copied["result_schema"], Mapping):
        raise ContractError("result_schema must be object")
    for name, cls in (("inputs", InputRef), ("resources", InputRef), ("outputs", OutputSpec)):
        if type(copied[name]) is not list:
            raise ContractError(f"{name}: expected JSON array")
        copied[name] = tuple(_decode(cls, item) for item in copied[name])
    return TaskSpec(**copied)


def result_from_dict(data: Mapping[str, Any]) -> TaskResult:
    _closed(data, {field.name for field in fields(TaskResult)})
    copied = dict(data)
    if copied["decision"] is not None:
        if not isinstance(copied["decision"], Mapping) or type(copied["decision"].get("options")) is not list:
            raise ContractError("decision.options must be JSON array")
    for name, cls in (("artifacts", ArtifactRef), ("evidence", InputRef)):
        if type(copied[name]) is not list:
            raise ContractError(f"{name}: expected JSON array")
        copied[name] = tuple(_decode(cls, item) for item in copied[name])
    return TaskResult(**copied)


def task_to_dict(task: TaskSpec) -> dict:
    return _wire(task)


def result_to_dict(result: TaskResult) -> dict:
    return _wire(result)


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    errors: tuple[Mapping[str, Any], ...] = ()
    diagnostics: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self):
        if type(self.valid) is not bool:
            raise ContractError("valid must be bool")
        for name in ("errors", "diagnostics"):
            items = getattr(self, name)
            for issue in items:
                _closed(issue, {"code", "message", "details"})
                _string(issue["code"], "issue.code")
                _string(issue["message"], "issue.message")
            object.__setattr__(self, name, _freeze(items))
        if self.valid and self.errors:
            raise ContractError("Valid result cannot have errors")


def validate_result(result: TaskResult, task: TaskSpec) -> ValidationResult:
    errors = []
    def issue(code, message):
        errors.append({"code": code, "message": message, "details": {}})
    for name in ("run_id", "step_id", "item_id", "attempt_id"):
        if getattr(result, name) != getattr(task, name):
            issue("RESULT_IDENTITY_MISMATCH", name)
    if result.task_sha256 != task.semantic_sha256:
        issue("TASK_HASH_MISMATCH", "Result is not bound to current task")
    expected = {item.name: item for item in task.outputs}
    actual = {item.name: item for item in result.artifacts}
    for name, artifact in actual.items():
        spec = expected.get(name)
        if spec is None or (artifact.relative_path, artifact.media_type) != (spec.relative_path, spec.media_type):
            issue("OUTPUT_MISMATCH", name)
    if result.status == "completed":
        for name, spec in expected.items():
            if spec.required and name not in actual:
                issue("MISSING_OUTPUT", name)
    allowed_evidence = {(ref.path, ref.sha256, ref.media_type) for ref in task.inputs + task.resources}
    for ref in result.evidence:
        if (ref.path, ref.sha256, ref.media_type) not in allowed_evidence:
            issue("EVIDENCE_MISMATCH", ref.path)
    return ValidationResult(not errors, tuple(errors))


def validate_execution_profile(profile: Mapping[str, Any] | None) -> ValidationResult:
    allowed = {"backend", "model", "resource_hints"}
    if profile is None:
        return ValidationResult(True)
    if not isinstance(profile, Mapping) or set(profile) - allowed:
        return ValidationResult(False, ({"code": "PROFILE_SEMANTIC_OVERRIDE", "message": "Profiles may contain execution hints only", "details": {}},))
    _freeze(profile)
    for key in ("backend", "model"):
        if key in profile:
            _string(profile[key], key)
    if "resource_hints" in profile and not isinstance(profile["resource_hints"], Mapping):
        raise ContractError("resource_hints must be an object")
    if "resource_hints" in profile:
        # Resource hints cannot smuggle task text, roots, schemas or review policy.
        allowed_resources = {"memory_mb", "cpu_count", "timeout_seconds", "context_tokens"}
        hints = profile["resource_hints"]
        if set(hints) - allowed_resources:
            return ValidationResult(False, ({"code": "PROFILE_SEMANTIC_OVERRIDE", "message": "Unknown resource hint", "details": {}},))
        if any(type(value) is not int or value < 1 for value in hints.values()):
            raise ContractError("Resource hints must be positive integers")
    return ValidationResult(True)


@dataclass(frozen=True)
class RunRequest:
    schema_version: int
    workflow: str
    project_root: str
    arguments: tuple[str, ...]
    caller_host: str
    caller_family: str
    backend: str
    profile: str | None

    def __post_init__(self):
        _version(self.schema_version)
        _string(self.workflow, "workflow")
        _absolute(self.project_root)
        if not isinstance(self.arguments, (list, tuple)) or any(type(arg) is not str or "\x00" in arg for arg in self.arguments):
            raise ContractError("arguments must be argv strings")
        object.__setattr__(self, "arguments", tuple(self.arguments))
        if self.caller_host not in ("omp", "codex", "claude") or self.caller_family not in ("gpt", "claude") or self.backend not in ("auto", "omp", "codex", "claude"):
            raise ContractError("Unknown native identity/backend")
        if self.profile is not None:
            _string(self.profile, "profile")


@dataclass(frozen=True)
class StepSpec:
    step_id: str
    dependencies: tuple[str, ...]
    kind: str
    prepare_handler: Callable
    fanout_source: str | None
    concurrency: int
    policy: Mapping[str, Any]

    def __post_init__(self):
        _string(self.step_id, "step_id")
        object.__setattr__(self, "dependencies", _strings(self.dependencies))
        if self.kind not in ("local", "task", "review", "decision", "publish") or not callable(self.prepare_handler):
            raise ContractError("Invalid source-bound step")
        if type(self.concurrency) is not int or self.concurrency < 1:
            raise ContractError("concurrency must be positive integer")
        if self.fanout_source is not None:
            _string(self.fanout_source, "fanout_source")
        object.__setattr__(self, "policy", _freeze(self.policy))


@dataclass(frozen=True)
class WorkflowSpec:
    workflow_id: str
    steps: tuple[StepSpec, ...]

    def __post_init__(self):
        _string(self.workflow_id, "workflow_id")
        object.__setattr__(self, "steps", _refs(self.steps, StepSpec))
        seen = set()
        for step in self.steps:
            if step.step_id in seen or not set(step.dependencies) <= seen:
                raise ContractError("Steps must be unique and dependency-ordered")
            seen.add(step.step_id)


@dataclass(frozen=True)
class StepContext:
    request: RunRequest
    predecessors: tuple[ArtifactRef, ...]
    step_id: str
    item_id: str
    counters: Mapping[str, Any]
    paths: Mapping[str, Any]

    def __post_init__(self):
        object.__setattr__(self, "predecessors", _refs(self.predecessors, ArtifactRef))
        object.__setattr__(self, "counters", _freeze(self.counters))
        object.__setattr__(self, "paths", _freeze(self.paths))


@dataclass(frozen=True)
class PreparedStep:
    status: str
    tasks: tuple[TaskSpec, ...] = ()
    skip_reason: str | None = None
    decision: Mapping[str, Any] | None = None
    local_result: Mapping[str, Any] | None = None

    def __post_init__(self):
        if self.status not in ("ready", "skip", "blocked", "local_result"):
            raise ContractError("Invalid prepared-step tag")
        object.__setattr__(self, "tasks", _refs(self.tasks, TaskSpec))
        object.__setattr__(self, "decision", _freeze(self.decision))
        object.__setattr__(self, "local_result", _freeze(self.local_result))
        if self.status == "skip" and not self.skip_reason:
            raise ContractError("Skipped step needs reason")
        if self.status != "ready" and self.tasks:
            raise ContractError("Only ready steps dispatch tasks")
        if self.status == "local_result" and self.local_result is None:
            raise ContractError("Local result missing")


@dataclass(frozen=True)
class RunOutcome:
    schema_version: int
    run_id: str
    status: str
    outputs: tuple[ArtifactRef, ...]
    decision: Mapping[str, Any] | None
    error: Mapping[str, Any] | None

    def __post_init__(self):
        _version(self.schema_version)
        _string(self.run_id, "run_id")
        _string(self.status, "status")
        object.__setattr__(self, "outputs", _refs(self.outputs, ArtifactRef))
        object.__setattr__(self, "decision", _freeze(self.decision))
        object.__setattr__(self, "error", _freeze(self.error))
