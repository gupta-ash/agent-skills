"""Expected-preimage file effects with durable intent and crash reconciliation.

This is not filesystem CAS or a global multi-file transaction. An uncooperative
editor can race the final check; observed conflicts stop, never roll back. Rename
uses an exclusive hard-link then unlink, so a destination is never overwritten,
including on POSIX. A crash between those two effects is explicitly reconciled.

Store ownership checks refuse private engineering ledgers before publication or
reconciliation. Only ordinary settled producers and registered local outputs can
authorize effects; historical source-recovery events never grant authority.
"""
from __future__ import annotations

from hashlib import sha256
import errno
import json
import os
from pathlib import Path
import re
import stat
from typing import Callable

from .store import Store, StoreError, _hash, _json, _text


class PublicationError(StoreError):
    pass


def _path(path: Path, root: Path | None = None, *, regular=False) -> Path:
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise PublicationError("UNSAFE_PATH", "Publication paths must be absolute without traversal")
    if root is not None:
        root = _path(root)
        if not root.is_dir():
            raise PublicationError("UNSAFE_ROOT", "Publication root must be an existing directory")
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise PublicationError("OUTSIDE_ROOT", "Publication target escapes its declared root") from exc
        if path == root:
            raise PublicationError("UNSAFE_PATH", "A root directory is not an artifact")
    for parent in (*reversed(path.parents), path):
        if parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction()):
            raise PublicationError("UNSAFE_PATH", "Symlinks/junctions are not publication paths")
    if path.exists():
        mode = path.stat().st_mode
        if regular and not stat.S_ISREG(mode):
            raise PublicationError("NOT_REGULAR", "Publication artifacts must be regular files")
    elif regular:
        raise PublicationError("MISSING_CANDIDATE", "Publication candidate/source is unavailable")
    return path


def _read(path: Path) -> bytes:
    _path(path, regular=True)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise PublicationError("NOT_REGULAR", "Publication input must be regular")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            return stream.read()
    finally:
        os.close(descriptor)


def _digest(path: Path) -> str | None:
    _path(path)
    if not path.exists():
        return None
    return sha256(_read(path)).hexdigest()


def _human_protected(path: Path) -> bool:
    if not path.exists():
        return False
    # Protection is frontmatter, not arbitrary body prose. Do not add a YAML
    # dependency to the reusable core. Recognize quoted/unquoted scalar flags.
    text = _read(path).decode("utf-8-sig", errors="replace").replace("\r\n", "\n")
    if not text.startswith("---\n"):
        return False
    lines = text.split("\n")
    end = next((i for i, line in enumerate(lines[1:], 1) if line.strip() in ("---", "...")), None)
    if end is None:
        raise PublicationError("MALFORMED_FRONTMATTER", "Unterminated frontmatter cannot be safely published over")
    for line in lines[1:end]:
        flag = re.search(r'''(?:^|[,{]\s*)["']?human_edited["']?\s*:\s*([^,}]*)''', line.strip(), re.I)
        if flag is not None:
            value = flag[1].split("#", 1)[0].strip().lower()
            # Ambiguous/anchored/multiline protection values fail closed; the
            # domain can additionally supply its full schema-aware checker.
            if value not in ("false", "no", "off", "null", "~", "'false'", '"false"'):
                return True
        if re.search(r'''(?:^|[,{]\s*)["']?pipeline_id["']?\s*:\s*(?:hand-curated|"hand-curated"|'hand-curated')(?:\s*(?:#.*)?$|\s*[,}])''', line.strip(), re.I):
            return True
    return False


_PROJECTION_FIELDS = frozenset({"schema_version", "projection_id", "producer_attempt_id", "source", "candidate", "edits", "proof_ref"})


def validate_byte_projection(source: bytes, candidate: bytes, edits) -> None:
    """Replay at most two declared scalar-byte edits, never touching the body."""
    opening = re.match(rb"(?:\xef\xbb\xbf)?---[ \t]*\r?\n", source)
    closing = re.compile(rb"^---[ \t]*(?:\r?\n|$)", re.MULTILINE).search(source, opening.end()) if opening else None
    if closing is None or not isinstance(edits, (list, tuple)) or not 1 <= len(edits) <= 2:
        raise PublicationError("INVALID_PROJECTION", "A projection requires bounded edits inside leading frontmatter")
    previous, output = 0, 0
    original_view, candidate_view = memoryview(source), memoryview(candidate)
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) != {"start", "end", "before_hex", "after_hex"}:
            raise PublicationError("INVALID_PROJECTION", "Projection edit fields must be exact")
        start, end = edit["start"], edit["end"]
        if (type(start) is not int or type(end) is not int or not opening.end() <= start <= end <= closing.start()
                or start < previous):
            raise PublicationError("INVALID_PROJECTION", "Projection edits overlap or escape the frontmatter values")
        if any(type(edit[key]) is not str or re.fullmatch(r"(?:[0-9a-f]{2})*", edit[key]) is None
               for key in ("before_hex", "after_hex")):
            raise PublicationError("INVALID_PROJECTION", "Projection byte edits require canonical hexadecimal")
        before, after = bytes.fromhex(edit["before_hex"]), bytes.fromhex(edit["after_hex"])
        if original_view[start:end] != before:
            raise PublicationError("INVALID_PROJECTION", "Projection preimage differs from the actual native bytes")
        length = start - previous
        if candidate_view[output:output + length] != original_view[previous:start]:
            raise PublicationError("INVALID_PROJECTION", "Projection changed undeclared bytes")
        output += length
        if candidate_view[output:output + len(after)] != after:
            raise PublicationError("INVALID_PROJECTION", "Projection replacement bytes differ")
        output += len(after)
        previous = end
    if candidate_view[output:] != original_view[previous:]:
        raise PublicationError("INVALID_PROJECTION", "Projection changed undeclared frontmatter/body bytes")


class PublicationManager:
    def __init__(self, store: Store):
        self.store = store

    def _protected(self, path: Path, check: Callable | None) -> None:
        if _human_protected(path) or (path.exists() and check is not None and check(path)):
            raise PublicationError("PROTECTED_TARGET", "Human-protected content cannot be overwritten or moved", {"path": str(path)})

    @staticmethod
    def _effect(kind, candidate, target, expected, operation_id, root):
        return {"kind": kind, "source" if kind == "rename" else "candidate": str(candidate),
                "target": str(target), "expected_sha256": expected,
                "operation_id": operation_id, "root": str(root)}

    def _authorize_local(self, run, effect, *, new_effect):
        """Only a registered local handler's exact durable intent grants authority."""
        if effect is None:
            raise PublicationError("LOCAL_EFFECT_REQUIRED", "Pipeline effects require their validated local intent")
        seen = set()
        events = self.store.connection.execute(
            "SELECT payload_json FROM wf_events WHERE run_id=? AND attempt_id IS NULL "
            "AND event_type='local_output_validated' ORDER BY event_id DESC", (run["run_id"],))
        for event in events:
            payload = json.loads(event[0])
            identity = (payload.get("step_id"), payload.get("item_id"))
            if identity in seen:
                continue
            seen.add(identity)
            if (payload.get("schema_version") != 1 or payload.get("run_id") != run["run_id"]
                    or payload.get("definition_sha256") != run["definition_sha256"]
                    or payload.get("validation") != {"valid": True, "source": "registered_local_handler"}
                    or effect not in payload.get("publications", [])):
                continue
            step = self.store.connection.execute(
                "SELECT ordinal,current_attempt_id FROM wf_steps WHERE run_id=? AND step_id=? AND item_id=?",
                (run["run_id"], *identity)).fetchone()
            if (step is None or type(payload.get("ordinal")) is not int
                    or step["ordinal"] != payload["ordinal"] or step["current_attempt_id"] is not None):
                continue
            run_root = self.store.project_root / "docs" / "tmp" / "pipeline-runs" / run["run_id"]
            candidate_dir = _path(Path(payload["candidate_dir"]), run_root)
            if effect["kind"] == "replace":
                candidate = _path(Path(effect["candidate"]), candidate_dir)
                matches = [artifact for artifact in payload.get("artifacts", [])
                           if artifact.get("path") == str(candidate)]
                if len(matches) != 1:
                    continue
                _hash(matches[0]["sha256"])
                if new_effect and _digest(candidate) != matches[0]["sha256"]:
                    raise PublicationError("ARTIFACT_CONFLICT", "Local candidate differs from its validated bytes")
            return
        raise PublicationError("LOCAL_EFFECT_REQUIRED", "No current validated local output authorizes this exact effect")

    def _authorize_effect(self, row, root, *, new_effect=True):
        effect = self._effect(row["kind"], row["candidate_path"], row["target"],
                              row["expected_sha256"], row["operation_id"], root)
        return self._authorize(row["run_id"], row["attempt_id"], new_effect=new_effect, effect=effect)

    def _authorize(self, run_id, attempt_id, *, new_effect=True, effect=None):
        self.store.assert_owner(run_id)
        run = self.store.read_run(run_id)
        if new_effect and (run["cancel_requested"] or run["status"] in ("done", "failed", "cancelled", "preview")):
            raise PublicationError("PUBLICATION_NOT_AUTHORIZED", "Cancelled/terminal runs cannot start publication effects")
        if attempt_id is not None:
            row = self.store._attempt(self.store.connection, run_id, attempt_id)
            if (new_effect and row["current_attempt_id"] != attempt_id) or row["status"] != "completed" or row["result_json"] is None:
                raise PublicationError("UNACCEPTABLE_ATTEMPT", "Publication requires the current completed attempt")
            if json.loads(row["result_json"])["verification"] == "FAIL":
                raise PublicationError("UNACCEPTABLE_ATTEMPT", "A completed FAIL is not publication acceptance")
            return row
        if run["run_kind"] != "inline":
            self._authorize_local(run, effect, new_effect=new_effect)
        return None

    def _native_candidate(self, candidate: Path, attempt) -> Path:
        allowed = Path(json.loads(attempt["task_json"])["candidate_dir"])
        candidate = _path(candidate, allowed, regular=True)
        relative = candidate.relative_to(allowed).as_posix()
        artifacts = json.loads(attempt["result_json"])["artifacts"]
        matches = [artifact for artifact in artifacts if artifact["relative_path"] == relative]
        if len(matches) != 1 or _digest(candidate) != matches[0]["sha256"]:
            raise PublicationError("ARTIFACT_CONFLICT", "Candidate is not an exact hash-bound settled output")
        settled = self.store.connection.execute(
            "SELECT event_id FROM wf_events WHERE run_id=? AND attempt_id=? AND event_type='attempt_settled'",
            (attempt["run_id"], attempt["attempt_id"])).fetchone()
        if settled is None:
            raise PublicationError("UNACCEPTABLE_ATTEMPT", "Native producer has no durable settlement identity")
        artifact_id = json.loads(attempt["task_json"])["artifact_id"]
        if artifact_id is None:
            return candidate
        newer = self.store.connection.execute(
            "SELECT a.task_json,a.result_json FROM wf_events e JOIN wf_attempts a ON a.attempt_id=e.attempt_id "
            "JOIN wf_steps s ON s.run_id=a.run_id AND s.step_id=a.step_id AND s.item_id=a.item_id "
            "WHERE e.run_id=? AND e.event_id>? AND e.event_type='attempt_settled' "
            "AND a.attempt_id=s.current_attempt_id AND a.status='completed' AND a.result_json IS NOT NULL",
            (attempt["run_id"], settled["event_id"]))
        for row in newer:
            task = json.loads(row["task_json"])
            if task["artifact_id"] != artifact_id or task["duty"] in ("verify", "verify_capture", "verify_coverage"):
                continue
            result = json.loads(row["result_json"])
            if result["verification"] != "FAIL" and any(value["name"] == matches[0]["name"] and value["media_type"] == matches[0]["media_type"]
                                                      for value in result["artifacts"]):
                raise PublicationError("UNACCEPTABLE_ATTEMPT", "A newer native producer superseded this logical artifact member")
        return candidate

    def validate_projection(self, run_id, projection, *, candidate_dir):
        """Validate native producer lineage for a controller-recorded projection."""
        if (not isinstance(projection, dict) or set(projection) != _PROJECTION_FIELDS
                or type(projection["schema_version"]) is not int or projection["schema_version"] != 1):
            raise PublicationError("INVALID_PROJECTION", "Projection schema/fields must be exact")
        _text(projection["projection_id"], "projection_id")
        _text(projection["producer_attempt_id"], "producer_attempt_id")
        attempt = self._authorize(run_id, projection["producer_attempt_id"])
        run_root = self.store.project_root / "docs" / "tmp" / "pipeline-runs" / run_id
        candidate_dir = _path(Path(candidate_dir), run_root)
        refs = {}
        for name in ("source", "candidate", "proof_ref"):
            ref = projection[name]
            fields = {"path", "sha256", "media_type"} | ({"name"} if name != "proof_ref" else set())
            if not isinstance(ref, dict) or set(ref) != fields:
                raise PublicationError("INVALID_PROJECTION", "Projection references must be complete and closed")
            _hash(ref["sha256"])
            _text(ref["media_type"], "media_type")
            if name != "proof_ref":
                _text(ref["name"], "name")
            path = self._native_candidate(Path(ref["path"]), attempt) if name == "source" else _path(Path(ref["path"]), candidate_dir, regular=True)
            content = _read(path)
            if sha256(content).hexdigest() != ref["sha256"]:
                raise PublicationError("ARTIFACT_CONFLICT", "Projection source/candidate/proof bytes changed")
            refs[name] = (path, content)
        if refs["source"][0] == refs["candidate"][0] or projection["source"]["media_type"] != "text/markdown" or projection["candidate"]["media_type"] != "text/markdown":
            raise PublicationError("INVALID_PROJECTION", "A lifecycle projection requires a distinct Markdown candidate")
        artifact = next(value for value in json.loads(attempt["result_json"])["artifacts"]
                        if value["relative_path"] == refs["source"][0].relative_to(Path(json.loads(attempt["task_json"])["candidate_dir"])).as_posix())
        if artifact["name"] != projection["source"]["name"] or artifact["media_type"] != projection["source"]["media_type"]:
            raise PublicationError("INVALID_PROJECTION", "Projection source differs from the native output identity")
        proof = json.loads(refs["proof_ref"][1].decode("utf-8"))
        if not isinstance(proof, dict) or any(proof.get(key) != projection[key] for key in _PROJECTION_FIELDS - {"proof_ref"}):
            raise PublicationError("INVALID_PROJECTION", "Projection proof does not bind the exact declared lineage")
        gate_refs = proof.get("gate_refs")
        if not isinstance(gate_refs, list) or not gate_refs:
            raise PublicationError("INVALID_PROJECTION", "Projection requires actual retained gate evidence")
        for ref in gate_refs:
            if not isinstance(ref, dict) or not {"path", "sha256", "media_type"} <= set(ref):
                raise PublicationError("INVALID_PROJECTION", "Projection gates require full hash-bound evidence refs")
            _hash(ref["sha256"])
            gate = _path(Path(ref["path"]), run_root, regular=True)
            if _digest(gate) != ref["sha256"]:
                raise PublicationError("ARTIFACT_CONFLICT", "Projection gate evidence changed")
        validate_byte_projection(refs["source"][1], refs["candidate"][1], projection["edits"])
        return projection

    def _current_projection(self, payload):
        """Old registrations are evidence, never current canonical authority."""
        run_id = payload["run_id"]
        key = (payload.get("step_id"), payload.get("item_id"))
        projection = payload["projection"]
        events = self.store.connection.execute(
            "SELECT event_type,payload_json FROM wf_events WHERE run_id=? "
            "AND event_type IN ('local_output_validated','item_completed') ORDER BY event_id", (run_id,))
        validation, completions = None, []
        for event in events:
            value = json.loads(event["payload_json"])
            if (value.get("step_id"), value.get("item_id")) == key:
                if event["event_type"] == "local_output_validated":
                    validation = value
                else:
                    completions.append(value)
        if validation is None:
            return False
        if (validation.get("candidate_dir") != payload.get("candidate_dir")
                or validation.get("schema_version") != 1 or validation.get("run_id") != run_id
                or validation.get("definition_sha256") != payload.get("definition_sha256")
                or validation.get("validation") != {"valid": True, "source": "registered_local_handler"}
                or projection not in validation.get("data", {}).get("candidate_projections", ())
                or not all(any(all(artifact.get(key) == value for key, value in reference.items())
                    for artifact in validation.get("artifacts", ())) for reference in (projection["candidate"], projection["proof_ref"]))):
            return False
        if completions:
            return len(completions) == 1 and projection in completions[0].get("data", {}).get("candidate_projections", ())
        # A current LOCAL handler may publish its exact freshly validated output
        # before complete_step.
        step = self.store.connection.execute("SELECT status FROM wf_steps WHERE run_id=? AND step_id=? AND item_id=?", (run_id, *key)).fetchone()
        return step is not None and step["status"] not in ("done", "failed", "cancelled", "preview")

    def _candidate(self, candidate: Path, attempt) -> Path:
        if attempt is None:
            return _path(candidate, self.store.project_root, regular=True)
        candidate = _path(candidate, regular=True)
        allowed = Path(json.loads(attempt["task_json"])["candidate_dir"])
        if candidate.is_relative_to(allowed):
            return self._native_candidate(candidate, attempt)
        run = self.store.read_run(attempt["run_id"])
        events = self.store.connection.execute(
            "SELECT payload_json FROM wf_events WHERE run_id=? AND attempt_id IS NULL "
            "AND event_type='candidate_projection_validated' ORDER BY event_id DESC", (attempt["run_id"],))
        seen = set()
        for event in events:
            payload = json.loads(event[0])
            projection = payload.get("projection", {})
            identity = projection.get("projection_id")
            if identity in seen:
                continue
            seen.add(identity)
            if (projection.get("producer_attempt_id") != attempt["attempt_id"]
                    or projection.get("candidate", {}).get("path") != str(candidate)
                    or payload.get("schema_version") != 1 or payload.get("run_id") != attempt["run_id"]
                    or payload.get("definition_sha256") != run["definition_sha256"]
                    or payload.get("validation") != {"valid": True, "source": "registered_local_handler"}):
                continue
            step = self.store.connection.execute(
                "SELECT ordinal,current_attempt_id FROM wf_steps WHERE run_id=? AND step_id=? AND item_id=?",
                (attempt["run_id"], payload.get("step_id"), payload.get("item_id"))).fetchone()
            if step is None or type(payload.get("ordinal")) is not int or step["ordinal"] != payload["ordinal"] or step["current_attempt_id"] is not None:
                continue
            if not self._current_projection(payload):
                continue
            self.validate_projection(attempt["run_id"], projection, candidate_dir=payload["candidate_dir"])
            return candidate
        raise PublicationError("UNSAFE_PATH", "Candidate is neither a native output nor a current validated projection")

    def _row(self, operation_id):
        row = self.store.connection.execute("SELECT * FROM wf_publications WHERE operation_id=?", (operation_id,)).fetchone()
        return dict(row) if row is not None else None

    def _intent(self, run_id, attempt_id, kind, candidate, target, expected, desired, operation_id, root):
        _text(operation_id, "operation_id")
        values = {"run_id": run_id, "attempt_id": attempt_id, "kind": kind,
                  "target": str(target), "expected_sha256": expected,
                  "desired_sha256": desired, "candidate_path": str(candidate)}
        with self.store.transaction() as conn:
            self.store.assert_owner(run_id)
            previous = self._row(operation_id)
            if previous is not None:
                if any(previous[key] != value for key, value in values.items()):
                    raise PublicationError("OPERATION_CONFLICT", "Operation ID already binds a different immutable intent")
                self._root(operation_id, root)
                return previous
            effect = self._effect(kind, candidate, target, expected, operation_id, root)
            self._authorize(run_id, attempt_id, effect=effect)
            conn.execute("INSERT INTO wf_publications VALUES(?,?,?,?,?,?,?,?, 'intent',NULL)",
                         (operation_id, run_id, attempt_id, kind, str(target), expected, desired, str(candidate)))
            self.store._event(conn, run_id, attempt_id, "publication_intent", {"operation_id": operation_id, "root": str(root), **values})
        return self._row(operation_id)

    def _root(self, operation_id, root):
        row = self._row(operation_id)
        if row is None:
            raise PublicationError("UNKNOWN_OPERATION", "No durable publication intent")
        for event in self.store.connection.execute("SELECT payload_json FROM wf_events WHERE run_id=? AND event_type='publication_intent'", (row["run_id"],)):
            payload = json.loads(event[0])
            if payload["operation_id"] == operation_id:
                if Path(payload["root"]) != Path(root):
                    raise PublicationError("ROOT_CONFLICT", "Reconciliation cannot change publication authority")
                return
        raise PublicationError("MISSING_INTENT", "Publication row lacks its immutable intent event")

    def _receipt(self, row, status, classification, *, error=None):
        receipt = {"operation_id": row["operation_id"], "run_id": row["run_id"],
                   "attempt_id": row["attempt_id"], "kind": row["kind"],
                   "target": row["target"], "expected_sha256": row["expected_sha256"],
                   "desired_sha256": row["desired_sha256"], "status": status,
                   "classification": classification, "error": error}
        with self.store.transaction() as conn:
            self.store.assert_owner(row["run_id"])
            conn.execute("UPDATE wf_publications SET status=?,receipt_json=? WHERE operation_id=?", (status, _json(receipt), row["operation_id"]))
            self.store._event(conn, row["run_id"], row["attempt_id"], "publication_observed", receipt)
        return receipt

    def publish_file(self, run_id, attempt_id, candidate: Path, target: Path,
                     expected_sha256: str | None, operation_id: str, *, root: Path,
                     kind="replace", protected_check: Callable | None = None):
        if kind != "replace":
            raise PublicationError("INVALID_KIND", "Use rename_file for a rename effect")
        if expected_sha256 is not None:
            _hash(expected_sha256)
        root, target = _path(root), _path(target, root)
        previous = self._row(operation_id)
        if previous is not None:
            # Retry can reconcile a vanished candidate after a completed effect.
            if (previous["run_id"], previous["attempt_id"], previous["kind"], previous["candidate_path"], previous["target"], previous["expected_sha256"]) != (run_id, attempt_id, kind, str(candidate), str(target), expected_sha256):
                raise PublicationError("OPERATION_CONFLICT", "Operation ID was reused")
            return self.reconcile(operation_id, root=root, protected_check=protected_check)
        effect = self._effect(kind, candidate, target, expected_sha256, operation_id, root)
        attempt = self._authorize(run_id, attempt_id, effect=effect)
        candidate = self._candidate(candidate, attempt)
        if candidate == target:
            raise PublicationError("INVALID_CANDIDATE", "Candidate and target must be distinct paths")
        desired = sha256(_read(candidate)).hexdigest()
        row = self._intent(run_id, attempt_id, kind, candidate, target, expected_sha256, desired, operation_id, root)
        return self._perform(row, root, protected_check, recovering=False)

    def rename_file(self, run_id, attempt_id, source: Path, target: Path,
                    expected_sha256: str, operation_id: str, *, root: Path,
                    protected_check: Callable | None = None):
        _hash(expected_sha256)
        root, source, target = _path(root), _path(source, root), _path(target, root)
        if source == target:
            raise PublicationError("INVALID_RENAME", "Rename source and destination must differ")
        previous = self._row(operation_id)
        if previous is not None:
            if (previous["run_id"], previous["attempt_id"], previous["kind"], previous["candidate_path"], previous["target"], previous["expected_sha256"]) != (run_id, attempt_id, "rename", str(source), str(target), expected_sha256):
                raise PublicationError("OPERATION_CONFLICT", "Operation ID was reused")
            return self.reconcile(operation_id, root=root, protected_check=protected_check)
        effect = self._effect("rename", source, target, expected_sha256, operation_id, root)
        self._authorize(run_id, attempt_id, effect=effect)
        _path(source, root, regular=True)
        if _digest(source) != expected_sha256:
            raise PublicationError("PREIMAGE_CONFLICT", "Rename source differs from expected bytes")
        row = self._intent(run_id, attempt_id, "rename", source, target, expected_sha256, expected_sha256, operation_id, root)
        return self._perform(row, root, protected_check, recovering=False)

    def reconcile(self, operation_id: str, *, root: Path, protected_check: Callable | None = None):
        root = _path(root)
        self._root(operation_id, root)
        row = self._row(operation_id)
        self.store.assert_owner(row["run_id"])
        return self._perform(row, root, protected_check)

    def _perform(self, row, root, check, *, recovering=True):
        run_id, attempt_id = row["run_id"], row["attempt_id"]
        target, source = Path(row["target"]), Path(row["candidate_path"])
        try:
            self._authorize_effect(row, root, new_effect=False)
            _path(target, root)
            actual = _digest(target)
            if row["kind"] == "replace":
                if not recovering and actual != row["expected_sha256"]:
                    raise PublicationError("PREIMAGE_CONFLICT", "Initial publication requires its exact preimage, including absence")
                if not recovering:
                    self._protected(target, check)
                if actual == row["desired_sha256"]:
                    if row["status"] == "applied" and row["receipt_json"]:
                        return json.loads(row["receipt_json"])
                    return self._receipt(row, "applied", "desired")
                if row["status"] == "applied" or actual != row["expected_sha256"]:
                    raise PublicationError("PREIMAGE_CONFLICT", "Target is neither the authorized prior nor desired bytes")
                self._authorize_effect(row, root)
                if recovering:
                    self._protected(target, check)
                attempt = self.store._attempt(self.store.connection, run_id, attempt_id) if attempt_id is not None else None
                self._candidate(source, attempt)
                data = _read(source)
                if sha256(data).hexdigest() != row["desired_sha256"]:
                    raise PublicationError("CANDIDATE_CONFLICT", "Candidate changed after durable intent")
                self._replace(row, target, data, root, check)
            else:
                _path(source, root)
                prior = _digest(source)
                if not recovering and actual is not None:
                    raise PublicationError("RENAME_COLLISION", "Initial rename destination must be absent")
                if actual == row["desired_sha256"] and prior is None:
                    if row["status"] == "applied" and row["receipt_json"]:
                        return json.loads(row["receipt_json"])
                    return self._receipt(row, "applied", "desired")
                if row["status"] == "applied":
                    raise PublicationError("PREIMAGE_CONFLICT", "A completed rename was subsequently changed")
                self._authorize_effect(row, root)
                self._protected(source, check)
                self._protected(target, check)
                if prior != row["expected_sha256"]:
                    raise PublicationError("PREIMAGE_CONFLICT", "Rename source conflicts with intent")
                if actual is not None:
                    if actual != row["desired_sha256"] or not os.path.samefile(source, target):
                        raise PublicationError("RENAME_COLLISION", "Destination is not this operation's linked source")
                    reserved = any(json.loads(event[0])["operation_id"] == row["operation_id"] for event in self.store.connection.execute("SELECT payload_json FROM wf_events WHERE run_id=? AND event_type='rename_link_intent'", (run_id,)))
                    if not reserved:
                        raise PublicationError("RENAME_COLLISION", "No durable link reservation authorizes source removal")
                self._rename(row, source, target, root, check, linked=actual is not None)
            return self._receipt(row, "applied", "desired")
        except StoreError as exc:
            self._receipt(row, "blocked", "conflicting" if exc.code in ("PREIMAGE_CONFLICT", "CANDIDATE_CONFLICT", "RENAME_COLLISION") else "prior_or_unknown", error={"code": exc.code, "message": str(exc), "details": exc.details})
            raise
        except OSError as exc:
            details = {"errno": exc.errno, "winerror": getattr(exc, "winerror", None)}
            if row["kind"] == "rename":
                if isinstance(exc, FileExistsError):
                    self._receipt(row, "blocked", "conflicting", error={"code": "RENAME_COLLISION", "message": str(exc), "details": details})
                    raise PublicationError("RENAME_COLLISION", "Rename destination was concurrently occupied", details) from exc
                if exc.errno in (errno.EACCES, errno.EPERM, errno.EXDEV, errno.ENOSYS, errno.EOPNOTSUPP, errno.EROFS):
                    try:
                        prior_only = _digest(source) == row["expected_sha256"] and _digest(target) is None
                    except OSError:
                        prior_only = False
                    if prior_only:
                        self._receipt(row, "blocked", "prior", error={"code": "RENAME_REFUSED", "message": str(exc), "details": details})
                        raise PublicationError("RENAME_REFUSED", "Filesystem refused rename; original source remains at its observed preimage", details) from exc
            # An unproved syscall outcome remains unknown, not retry permission.
            self._receipt(row, "blocked", "unknown", error={"code": "EFFECT_UNCERTAIN", "message": str(exc), "details": details})
            raise PublicationError("EFFECT_UNCERTAIN", "Reconcile the retained operation before any continuation", details) from exc

    def _replace(self, row, target, data, root, check):
        self._authorize_effect(row, root)
        _path(target, root)
        # Directory creation is an authorized additive output effect, never a
        # reason to relocate existing content. Check every ancestor first.
        target.parent.mkdir(parents=True, exist_ok=True)
        suffix = sha256(row["operation_id"].encode("utf-8")).hexdigest()
        temporary = target.with_name("." + target.name + ".wf-" + suffix + ".tmp")
        _path(temporary, root)
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        except FileExistsError:
            if _digest(temporary) != row["desired_sha256"]:
                raise PublicationError("TEMPORARY_CONFLICT", "Interrupted temporary bytes conflict; preserve for inspection")
        else:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        self._authorize_effect(row, root)
        _path(target, root)
        self._protected(target, check)
        if _digest(target) != row["expected_sha256"] or _digest(temporary) != row["desired_sha256"]:
            raise PublicationError("PREIMAGE_CONFLICT", "Target/temporary changed immediately before replacement")
        os.replace(temporary, target)
        self._sync_directory(target.parent)

    def _rename(self, row, source, target, root, check, *, linked):
        if not linked:
            self.store.append_event(row["run_id"], row["attempt_id"], "rename_link_intent", {"operation_id": row["operation_id"]})
        self._authorize_effect(row, root)
        target.parent.mkdir(parents=True, exist_ok=True)
        _path(source, root, regular=True)
        _path(target, root)
        self._protected(source, check)
        self._protected(target, check)
        if _digest(source) != row["expected_sha256"]:
            raise PublicationError("PREIMAGE_CONFLICT", "Rename source changed immediately before effect")
        if not linked:
            if target.exists():
                raise PublicationError("RENAME_COLLISION", "Rename never overwrites a destination")
            os.link(source, target, follow_symlinks=False)
            self._sync_directory(target.parent)
        # A crash after link is reconciled only if both paths still identify this
        # same inode and exact desired bytes. No blind rename replay.
        self.store.append_event(row["run_id"], row["attempt_id"], "rename_unlink_intent", {"operation_id": row["operation_id"]})
        self._authorize_effect(row, root)
        _path(source, root, regular=True)
        _path(target, root, regular=True)
        self._protected(source, check)
        self._protected(target, check)
        if not os.path.samefile(source, target) or _digest(source) != row["desired_sha256"] or _digest(target) != row["desired_sha256"]:
            raise PublicationError("PREIMAGE_CONFLICT", "Linked rename changed before source removal")
        source.unlink()
        self._sync_directory(source.parent)

    @staticmethod
    def _sync_directory(path):
        # Windows does not expose a directory fsync through the stdlib; file
        # contents are flushed, while SQLite intent remains the recovery truth.
        if os.name != "nt":
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
