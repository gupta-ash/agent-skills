"""Project-local workflow truth, fenced execution and legacy read projections.

Only initialize() bootstraps a writable store. Read-only instances never create
files or change SQLite pragmas with persistent effects. transaction() is short;
callers must never hold it while awaiting native work or touching vault files.
Private engineering ledgers remain readable evidence, never execution authority.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
from typing import Any, Mapping

from .contracts import (result_from_dict, result_to_dict, task_from_dict,
                        task_to_dict, validate_result)


class StoreError(RuntimeError):
    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code, self.details = code, details or {}


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _now() -> int:
    return time.time_ns() // 1_000_000


def _text(value: str, name: str) -> None:
    if type(value) is not str or not value or "\0" in value:
        raise StoreError("INVALID_IDENTITY", f"{name} must be a nonempty NUL-free string")


def _hash(value: str) -> None:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise StoreError("INVALID_HASH", "Expected a lowercase SHA256")


_LEGACY = {
    "pipelines": """CREATE TABLE pipelines (
        id TEXT PRIMARY KEY, shape TEXT NOT NULL, started_at INTEGER NOT NULL,
        completed_at INTEGER, status TEXT NOT NULL CHECK
        (status IN ('open','in_progress','blocked','done','failed','cancelled')))""",
    "pipeline_steps": """CREATE TABLE pipeline_steps (
        pipeline_id TEXT NOT NULL REFERENCES pipelines(id) ON DELETE CASCADE,
        step_index INTEGER NOT NULL, agent_type TEXT NOT NULL, status TEXT NOT NULL,
        output_path TEXT, last_task_id TEXT, inbox_path TEXT, output_artifact_paths TEXT,
        started_at INTEGER NOT NULL, completed_at INTEGER, current_task TEXT,
        error_summary TEXT, session_id TEXT, channel TEXT,
        PRIMARY KEY(pipeline_id,step_index))""",
    "pipeline_dependencies": """CREATE TABLE pipeline_dependencies (
        pipeline_id TEXT NOT NULL REFERENCES pipelines(id) ON DELETE CASCADE,
        depends_on_pipeline_id TEXT NOT NULL REFERENCES pipelines(id) ON DELETE RESTRICT,
        PRIMARY KEY(pipeline_id,depends_on_pipeline_id))""",
}
_DDL = {
    "wf_schema": "CREATE TABLE wf_schema (version INTEGER NOT NULL CHECK(version=1))",
    "wf_runs": """CREATE TABLE wf_runs (
        run_id TEXT PRIMARY KEY, run_kind TEXT NOT NULL CHECK(run_kind IN ('pipeline','inline')),
        workflow TEXT NOT NULL, definition_sha256 TEXT NOT NULL, request_json TEXT NOT NULL,
        input_manifest_json TEXT NOT NULL, status TEXT NOT NULL,
        owner_epoch INTEGER NOT NULL CHECK(owner_epoch>=0),
        cancel_requested INTEGER NOT NULL CHECK(cancel_requested IN (0,1)),
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)""",
    "wf_steps": """CREATE TABLE wf_steps (
        run_id TEXT NOT NULL REFERENCES wf_runs(run_id), step_id TEXT NOT NULL,
        item_id TEXT NOT NULL, ordinal INTEGER NOT NULL CHECK(ordinal>=0), status TEXT NOT NULL,
        current_attempt_id TEXT, dependency_json TEXT NOT NULL, policy_json TEXT NOT NULL,
        verify_passes INTEGER NOT NULL CHECK(verify_passes>=0),
        repair_rounds INTEGER NOT NULL CHECK(repair_rounds>=0),
        PRIMARY KEY(run_id,step_id,item_id),
        FOREIGN KEY(run_id,step_id,item_id,current_attempt_id)
        REFERENCES wf_attempts(run_id,step_id,item_id,attempt_id))""",
    "wf_attempts": """CREATE TABLE wf_attempts (
        attempt_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, step_id TEXT NOT NULL,
        item_id TEXT NOT NULL, ordinal INTEGER NOT NULL CHECK(ordinal>=0),
        task_json TEXT NOT NULL, task_sha256 TEXT NOT NULL, status TEXT NOT NULL,
        binding_json TEXT, result_json TEXT, error_json TEXT, created_at INTEGER NOT NULL,
        settled_at INTEGER,
        FOREIGN KEY(run_id,step_id,item_id) REFERENCES wf_steps(run_id,step_id,item_id))""",
    "wf_events": """CREATE TABLE wf_events (
        event_id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES wf_runs(run_id),
        attempt_id TEXT, event_type TEXT NOT NULL, payload_json TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        FOREIGN KEY(run_id,attempt_id) REFERENCES wf_attempts(run_id,attempt_id))""",
    "wf_decisions": """CREATE TABLE wf_decisions (
        run_id TEXT NOT NULL REFERENCES wf_runs(run_id), decision_key TEXT NOT NULL,
        question_json TEXT NOT NULL, answer_json TEXT, status TEXT NOT NULL,
        PRIMARY KEY(run_id,decision_key))""",
    "wf_reviews": """CREATE TABLE wf_reviews (
        run_id TEXT NOT NULL REFERENCES wf_runs(run_id), artifact_id TEXT NOT NULL,
        artifact_tier TEXT NOT NULL, contract TEXT NOT NULL,
        full_attempts INTEGER NOT NULL CHECK(full_attempts>=0),
        repair_rounds INTEGER NOT NULL CHECK(repair_rounds>=0),
        closed INTEGER NOT NULL CHECK(closed IN (0,1)), PRIMARY KEY(run_id,artifact_id))""",
    "wf_publications": """CREATE TABLE wf_publications (
        operation_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES wf_runs(run_id),
        attempt_id TEXT, kind TEXT NOT NULL CHECK(kind IN ('replace','rename')),
        target TEXT NOT NULL, expected_sha256 TEXT, desired_sha256 TEXT NOT NULL,
        candidate_path TEXT NOT NULL, status TEXT NOT NULL, receipt_json TEXT,
        FOREIGN KEY(run_id,attempt_id) REFERENCES wf_attempts(run_id,attempt_id))""",
}
_INDEXES = {
    "wf_single_version": "CREATE UNIQUE INDEX wf_single_version ON wf_schema(version)",
    "wf_steps_ordinal": "CREATE UNIQUE INDEX wf_steps_ordinal ON wf_steps(run_id,ordinal)",
    "wf_attempts_identity": "CREATE UNIQUE INDEX wf_attempts_identity ON wf_attempts(run_id,attempt_id)",
    "wf_attempts_subject": "CREATE UNIQUE INDEX wf_attempts_subject ON wf_attempts(run_id,step_id,item_id,attempt_id)",
    "wf_attempts_ordinal": "CREATE UNIQUE INDEX wf_attempts_ordinal ON wf_attempts(run_id,step_id,item_id,ordinal)",
    "wf_current_attempt": "CREATE INDEX wf_current_attempt ON wf_steps(current_attempt_id)",
    "wf_run_events": "CREATE INDEX wf_run_events ON wf_events(run_id,event_id)",
    "wf_publication_recovery": "CREATE INDEX wf_publication_recovery ON wf_publications(run_id,status)",
}
# Guard immutable observations even when an integrator uses connection directly.
_TRIGGERS = {
    "wf_events_update": "CREATE TRIGGER wf_events_update BEFORE UPDATE ON wf_events BEGIN SELECT RAISE(ABORT,'immutable event'); END",
    "wf_events_delete": "CREATE TRIGGER wf_events_delete BEFORE DELETE ON wf_events BEGIN SELECT RAISE(ABORT,'immutable event'); END",
    "wf_schema_update": "CREATE TRIGGER wf_schema_update BEFORE UPDATE ON wf_schema BEGIN SELECT RAISE(ABORT,'immutable version'); END",
    "wf_schema_delete": "CREATE TRIGGER wf_schema_delete BEFORE DELETE ON wf_schema BEGIN SELECT RAISE(ABORT,'immutable version'); END",
    "wf_attempt_snapshot": """CREATE TRIGGER wf_attempt_snapshot BEFORE UPDATE ON wf_attempts
        WHEN NEW.attempt_id IS NOT OLD.attempt_id OR NEW.run_id IS NOT OLD.run_id
        OR NEW.step_id IS NOT OLD.step_id OR NEW.item_id IS NOT OLD.item_id
        OR NEW.ordinal IS NOT OLD.ordinal OR NEW.task_json IS NOT OLD.task_json
        OR NEW.task_sha256 IS NOT OLD.task_sha256 OR NEW.created_at IS NOT OLD.created_at
        OR (OLD.binding_json IS NOT NULL AND NEW.binding_json IS NOT OLD.binding_json)
        OR (OLD.result_json IS NOT NULL AND NEW.result_json IS NOT OLD.result_json)
        OR (OLD.settled_at IS NOT NULL AND NEW.settled_at IS NOT OLD.settled_at)
        BEGIN SELECT RAISE(ABORT,'immutable attempt observation'); END""",
    "wf_attempt_delete": "CREATE TRIGGER wf_attempt_delete BEFORE DELETE ON wf_attempts BEGIN SELECT RAISE(ABORT,'immutable attempt'); END",
    "wf_run_snapshot": """CREATE TRIGGER wf_run_snapshot BEFORE UPDATE ON wf_runs
        WHEN NEW.run_id IS NOT OLD.run_id OR NEW.run_kind IS NOT OLD.run_kind
        OR NEW.workflow IS NOT OLD.workflow OR NEW.definition_sha256 IS NOT OLD.definition_sha256
        OR NEW.request_json IS NOT OLD.request_json OR NEW.input_manifest_json IS NOT OLD.input_manifest_json
        OR NEW.created_at IS NOT OLD.created_at OR NEW.owner_epoch<OLD.owner_epoch
        OR NEW.cancel_requested<OLD.cancel_requested
        BEGIN SELECT RAISE(ABORT,'immutable run snapshot'); END""",
    "wf_publication_intent": """CREATE TRIGGER wf_publication_intent BEFORE UPDATE ON wf_publications
        WHEN NEW.operation_id IS NOT OLD.operation_id OR NEW.run_id IS NOT OLD.run_id
        OR NEW.attempt_id IS NOT OLD.attempt_id OR NEW.kind IS NOT OLD.kind
        OR NEW.target IS NOT OLD.target OR NEW.expected_sha256 IS NOT OLD.expected_sha256
        OR NEW.desired_sha256 IS NOT OLD.desired_sha256 OR NEW.candidate_path IS NOT OLD.candidate_path
        BEGIN SELECT RAISE(ABORT,'immutable publication intent'); END""",
}
_STATUSES = {"waiting", "ready", "executing", "blocked", "done", "failed", "cancelled", "preview"}
_TERMINAL = {"done", "failed", "cancelled", "preview"}
_PROJECTION = {"waiting": "open", "ready": "open", "executing": "in_progress",
               "blocked": "blocked", "done": "done", "failed": "failed",
               "cancelled": "cancelled", "preview": "done"}
_ENGINEERING_EVENTS = (
    "source_recovery_admitted", "negative_recovery_admitted",
    "owned_input_evolution_admitted", "owned_input_source_revised",
    "settled_owned_input_evolution_admitted", "local_runtime_revision_admitted",
    "unprepared_local_source_revision_admitted", "operation_quarantined",
    "semantic_verifier_continuation_reserved", "completed_scope_confirmation_source_admitted",
    "completed_scope_preservation_confirmed", "pre_native_snapshots_recovered",
    "source_attempt_superseded", "source_completion_invalidated",
    "native_admission_succeeded", "quarantine_successor_admitted", "staged_continuation_replaced",
    "bounded_repair_reserved", "bounded_candidate_repair_reserved",
)


def _normalized_sql(sql: str) -> str:
    return re.sub(r"\s+", "", sql).rstrip(";").lower()


def _readonly_connection(path: Path, guards: ExitStack) -> sqlite3.Connection:
    """Read committed WAL without bootstrapping source sidecar files."""
    sidecars = [Path(str(path) + suffix) for suffix in ("-wal", "-shm")]
    shared = all(item.is_file() for item in sidecars)
    uri = path.as_uri() + "?mode=ro"
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                           wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        create.restype = wintypes.HANDLE
        close = kernel.CloseHandle
        close.argtypes, close.restype = (wintypes.HANDLE,), wintypes.BOOL
        def pin(item, share):
            if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
                raise StoreError("UNSAFE_PATH", "SQLite sidecars must not be links")
            handle = create(str(item), 0x80000000, share, None, 3, 0x80, None)
            if handle == wintypes.HANDLE(-1).value:
                raise StoreError("READ_ONLY_BUSY", "Could not pin SQLite source without mutation", {"winerror": ctypes.get_last_error()})
            guards.callback(close, handle)
        if shared:
            # Keep the existing names alive while SQLite takes its ordinary
            # WAL read lock. Readers may update existing SHM read marks.
            for item in (path, *sidecars):
                pin(item, 3)  # FILE_SHARE_READ | FILE_SHARE_WRITE, not DELETE.
        else:
            # Win32's read-only handle cannot take SQLite's EXCLUSIVE lock.
            # Native share-deny-write/delete handles instead exclude every
            # SQLite writer before using the no-lock VFS and heap WAL index.
            # A live writer makes this refuse, never return an unsafe copy.
            pin(path, 1)  # FILE_SHARE_READ only.
            if sidecars[0].exists():
                pin(sidecars[0], 1)
            uri += "&vfs=win32-none"
    connection = sqlite3.connect(uri, uri=True, isolation_level=None)
    try:
        if not shared:
            connection.execute("PRAGMA locking_mode=EXCLUSIVE")
        return connection
    except BaseException:
        connection.close()
        raise


class Store:
    def __init__(self, project_root: Path, read_only: bool = False):
        if ".." in Path(project_root).parts:
            raise StoreError("UNSAFE_PATH", "Project root must not traverse parents")
        self.project_root = Path(project_root).absolute()
        self.path = self.project_root / "state.db"
        self.read_only = read_only
        self._connection: sqlite3.Connection | None = None
        self._owner: tuple[str, int] | None = None
        self._lock_file = None
        self._initialized = False
        if read_only:
            self.initialize()

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None or not self._initialized:
            raise StoreError("NOT_INITIALIZED", "Call initialize() before accessing the writable store")
        return self._connection

    def _inspect(self, conn: sqlite3.Connection) -> None:
        objects = {row[0]: (row[1], row[2]) for row in conn.execute(
            "SELECT name,type,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")}
        unknown = {name for name in objects if name.startswith("wf_")} - set(_DDL) - set(_INDEXES) - set(_TRIGGERS)
        if unknown:
            raise StoreError("INCOMPATIBLE_SCHEMA", "Unknown workflow schema objects", {"objects": sorted(unknown)})
        present = set(_DDL) & objects.keys()
        if present and present != set(_DDL):
            raise StoreError("INCOMPATIBLE_SCHEMA", "Incomplete namespaced schema")
        reference = sqlite3.connect(":memory:")
        try:
            for sql in (*_LEGACY.values(), *_DDL.values(), *_INDEXES.values()):
                reference.execute(sql)
            for name in set(_LEGACY) | set(_DDL):
                if name not in objects:
                    continue
                if objects[name][0] != "table":
                    raise StoreError("INCOMPATIBLE_SCHEMA", f"{name} is not a table")
                actual = [tuple(row)[1:] for row in conn.execute(f'PRAGMA table_info("{name}")')]
                expected = [tuple(row)[1:] for row in reference.execute(f'PRAGMA table_info("{name}")')]
                if name == "pipeline_steps":
                    optional = {"current_task", "error_summary", "session_id", "channel"}
                    expected = [row for row in expected if row[0] not in optional or any(a[0] == row[0] for a in actual)]
                if actual != expected:
                    raise StoreError("INCOMPATIBLE_SCHEMA", f"Unexpected columns/types/keys in {name}")
                if sorted(tuple(row)[1:] for row in conn.execute(f'PRAGMA foreign_key_list("{name}")')) != sorted(
                        tuple(row)[1:] for row in reference.execute(f'PRAGMA foreign_key_list("{name}")')):
                    raise StoreError("INCOMPATIBLE_SCHEMA", f"Unexpected foreign keys in {name}")
                # Namespaced CHECK constraints and exact table semantics are versioned too.
                if name in _DDL and _normalized_sql(objects[name][1]) != _normalized_sql(_DDL[name]):
                    raise StoreError("INCOMPATIBLE_SCHEMA", f"Changed version-one definition: {name}")
                for index in conn.execute(f'PRAGMA index_list("{name}")'):
                    if index[3] == "pk":
                        continue
                    if name in _DDL and index[1] not in _INDEXES:
                        raise StoreError("INCOMPATIBLE_SCHEMA", f"Unexpected index on {name}")
            if present:
                if [tuple(row) for row in conn.execute("SELECT version FROM wf_schema")] != [(1,)]:
                    raise StoreError("INCOMPATIBLE_SCHEMA", "Unsupported workflow schema version")
                for name, sql in {**_INDEXES, **_TRIGGERS}.items():
                    if name not in objects or _normalized_sql(objects[name][1]) != _normalized_sql(sql):
                        raise StoreError("INCOMPATIBLE_SCHEMA", f"Missing/changed workflow invariant {name}")
            if "pipelines" in objects:
                query = "SELECT id FROM pipelines WHERE status NOT IN ('done','failed','cancelled')"
                if present:
                    query += " AND id NOT IN (SELECT run_id FROM wf_runs)"
                active = [row[0] for row in conn.execute(query)]
                if active:
                    raise StoreError("ACTIVE_LEGACY_RUN", "Prose-driven active runs cannot be adopted", {"run_ids": active})
            for name in set(_LEGACY) | set(_DDL):
                if name in objects and conn.execute(f'PRAGMA foreign_key_check("{name}")').fetchone() is not None:
                    raise StoreError("INCOMPATIBLE_SCHEMA", f"Broken foreign-key data in {name}")
        finally:
            reference.close()

    def initialize(self) -> None:
        if self._initialized:
            return
        for path in (*self.project_root.parents, self.project_root, self.path):
            if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
                raise StoreError("UNSAFE_PATH", "Project/database ancestors must not be symlinks or junctions")
        if self.path.exists() and not stat.S_ISREG(self.path.stat().st_mode):
            raise StoreError("UNSAFE_PATH", "Database must be a regular file")
        if self.read_only and not self.path.exists():
            raise StoreError("DATABASE_UNAVAILABLE", "No existing project state.db")
        if not self.read_only and self.path.exists():
            # Refuse an incompatible existing database through the same
            # no-bootstrap read snapshot before opening a write-capable handle.
            preflight = Store(self.project_root, read_only=True)
            preflight.close()
        if not self.read_only:
            self.project_root.mkdir(parents=True, exist_ok=True)
        mode = "ro" if self.read_only else ("rw" if self.path.exists() else "rwc")
        guards = ExitStack()
        try:
            conn = _readonly_connection(self.path, guards) if self.read_only else sqlite3.connect(self.path.as_uri() + "?mode=" + mode, uri=True, isolation_level=None)
        except sqlite3.Error as exc:
            guards.close()
            raise StoreError("DATABASE_UNAVAILABLE", str(exc)) from exc
        except BaseException:
            guards.close()
            raise
        self._connection = conn
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA foreign_keys=ON")
            if self.read_only:
                conn.execute("PRAGMA query_only=ON")
            self._inspect(conn)  # Refuse before WAL or any DDL/DML on existing files.
            if self.read_only:
                # A status command receives one coherent SQLite backup snapshot,
                # including committed WAL pages, then immediately releases its
                # source locks. Holding the returned Store cannot block cancel.
                snapshot = sqlite3.connect(":memory:", isolation_level=None)
                deadline = time.monotonic() + 5
                def progress(status, remaining, total):
                    if time.monotonic() >= deadline:
                        raise StoreError("READ_ONLY_BUSY", "Read-only snapshot exceeded five seconds")
                try:
                    conn.backup(snapshot, pages=64, progress=progress, sleep=0.05)
                    snapshot.row_factory = sqlite3.Row
                    snapshot.execute("PRAGMA foreign_keys=ON")
                    snapshot.execute("PRAGMA query_only=ON")
                    self._inspect(snapshot)
                except BaseException:
                    snapshot.close()
                    raise
                conn.close()
                self._connection = snapshot
            if not self.read_only:
                self._initialized = True
                with self.transaction():
                    self._inspect(conn)  # Fence another initializer between inspection and BEGIN.
                    objects = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
                    for name, sql in _LEGACY.items():
                        if name not in objects:
                            conn.execute(sql)
                    columns = {row[1] for row in conn.execute("PRAGMA table_info(pipeline_steps)")}
                    for name in ("current_task", "error_summary", "session_id", "channel"):
                        if name not in columns:
                            conn.execute(f"ALTER TABLE pipeline_steps ADD COLUMN {name} TEXT")
                    if "wf_schema" not in objects:
                        for sql in (*_DDL.values(), *_INDEXES.values(), *_TRIGGERS.values()):
                            conn.execute(sql)
                        conn.execute("INSERT INTO wf_schema(version) VALUES(1)")
                if conn.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() != "wal":
                    raise StoreError("WAL_UNAVAILABLE", "SQLite did not enable required WAL mode")
            self._initialized = True
        except BaseException:
            conn.close()
            self._connection = None
            self._initialized = False
            raise
        finally:
            guards.close()

    def close(self) -> None:
        if self._owner is not None:
            raise StoreError("OWNER_HELD", "Release the ownership context before closing")
        if self._connection is not None:
            self._connection.close()
        self._connection, self._initialized = None, False

    @contextmanager
    def transaction(self):
        if self.read_only:
            raise StoreError("READ_ONLY", "Read-only stores cannot start write transactions")
        conn = self.connection
        if conn.in_transaction:
            raise StoreError("NESTED_TRANSACTION", "Transactions must remain short and unnested")
        for attempt in range(5):
            try:
                conn.execute("BEGIN IMMEDIATE")
                break
            except sqlite3.OperationalError as exc:
                if getattr(exc, "sqlite_errorcode", 0) & 255 not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                    raise
                if attempt == 4:
                    raise StoreError("DATABASE_BUSY", "Five transaction attempts exhausted") from exc
                time.sleep((0.05, 0.1, 0.2, 0.4)[attempt])
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise

    def _assert_public_ledger(self, conn, run_id):
        """Refuse private engineering authority without rewriting its historical ledger."""
        event = conn.execute(
            "SELECT event_type FROM wf_events WHERE run_id=? "
            "AND event_type IN (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ORDER BY event_id LIMIT 1",
            (run_id, *_ENGINEERING_EVENTS)).fetchone()
        if event is not None:
            raise StoreError("SOURCE_RECOVERY_CONFLICT", "Private engineering ledgers cannot execute in the public runtime",
                             {"run_id": run_id, "event_type": event[0]})

    def _run(self, conn, run_id):
        row = conn.execute("SELECT * FROM wf_runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise StoreError("UNKNOWN_RUN", "Unknown workflow run", {"run_id": run_id})
        self._assert_public_ledger(conn, run_id)
        return row

    def assert_owner(self, run_id: str) -> int:
        if self._owner is None or self._owner[0] != run_id or self._run(self.connection, run_id)["owner_epoch"] != self._owner[1]:
            raise StoreError("OWNER_REQUIRED", "Execution requires the current OS lock and database epoch")
        return self._owner[1]

    @contextmanager
    def acquire_owner(self, run_id: str):
        if self.read_only or self._owner is not None:
            raise StoreError("OWNER_UNAVAILABLE", "Read-only/nested ownership is not allowed")
        self._run(self.connection, run_id)
        path = self.project_root / ".workflow.lock"
        if path.is_symlink() or (path.exists() and not stat.S_ISREG(path.stat().st_mode)):
            raise StoreError("UNSAFE_PATH", "Lock must be a regular non-symlink file")
        handle = path.open("a+b")
        locked = False
        try:
            if os.name == "nt":
                import msvcrt
                if os.fstat(handle.fileno()).st_size == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
            with self.transaction() as conn:
                self._inspect(conn)
                epoch = conn.execute("SELECT COALESCE(MAX(owner_epoch),0)+1 FROM wf_runs").fetchone()[0]
                conn.execute("UPDATE wf_runs SET owner_epoch=?,updated_at=? WHERE run_id=?", (epoch, _now(), run_id))
                self._event(conn, run_id, None, "owner_acquired", {"owner_epoch": epoch})
            self._owner, self._lock_file = (run_id, epoch), handle
            yield epoch
        except OSError as exc:
            if not locked:
                raise StoreError("OWNER_BUSY", "Another controller holds the project lock") from exc
            raise
        finally:
            self._owner, self._lock_file = None, None
            if locked:
                if os.name == "nt":
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    def _event(self, conn, run_id, attempt_id, event_type, payload):
        _text(event_type, "event_type")
        self._run(conn, run_id)
        return conn.execute("INSERT INTO wf_events(run_id,attempt_id,event_type,payload_json,created_at) VALUES(?,?,?,?,?)",
                            (run_id, attempt_id, event_type, _json(payload), _now())).lastrowid

    def append_event(self, run_id, attempt_id, event_type, payload):
        with self.transaction() as conn:
            self.assert_owner(run_id)
            return self._event(conn, run_id, attempt_id, event_type, payload)

    def read_run(self, run_id):
        if self.connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='wf_runs'").fetchone() is None:
            return None
        row = self.connection.execute("SELECT * FROM wf_runs WHERE run_id=?", (run_id,)).fetchone()
        return dict(row) if row is not None else None

    def request_cancel(self, run_id):
        # Deliberately independent of acquire_owner: only a latch and append-only event.
        with self.transaction() as conn:
            row = self._run(conn, run_id)
            if not row["cancel_requested"]:
                conn.execute("UPDATE wf_runs SET cancel_requested=1,updated_at=? WHERE run_id=?", (_now(), run_id))
                self._event(conn, run_id, None, "cancel_requested", {})
        return self.read_run(run_id)

    def is_cancelled(self, run_id):
        return bool(self._run(self.connection, run_id)["cancel_requested"])

    def create_run(self, run_id, workflow, definition_sha256, request, input_manifest, *, run_kind="pipeline", dependencies=()):
        for value, label in ((run_id, "run_id"), (workflow, "workflow")):
            _text(value, label)
        _hash(definition_sha256)
        if run_kind not in ("pipeline", "inline") or len(set(dependencies)) != len(dependencies) or run_id in dependencies:
            raise StoreError("INVALID_RUN", "Invalid run kind/dependencies")
        values = (run_kind, workflow, definition_sha256, _json(request), _json(input_manifest))
        with self.transaction() as conn:
            old = conn.execute("SELECT * FROM wf_runs WHERE run_id=?", (run_id,)).fetchone()
            if old is not None:
                self._assert_public_ledger(conn, run_id)
                if tuple(old[key] for key in ("run_kind", "workflow", "definition_sha256", "request_json", "input_manifest_json")) != values:
                    raise StoreError("SNAPSHOT_CONFLICT", "Run identity already binds different immutable inputs")
                actual = tuple(row[0] for row in conn.execute("SELECT depends_on_pipeline_id FROM pipeline_dependencies WHERE pipeline_id=? ORDER BY depends_on_pipeline_id", (run_id,)))
                if actual != tuple(sorted(dependencies)):
                    raise StoreError("SNAPSHOT_CONFLICT", "Run dependencies changed")
                return dict(old)
            self._inspect(conn)
            now = _now()
            conn.execute("INSERT INTO wf_runs VALUES(?,?,?,?,?,?,?,0,0,?,?)", (run_id, *values, "waiting", now, now))
            conn.execute("INSERT INTO pipelines(id,shape,started_at,status) VALUES(?,?,?,'open')", (run_id, workflow, now))
            for dependency in dependencies:
                if self._run(conn, dependency)["status"] != "done":
                    raise StoreError("DEPENDENCY_NOT_DONE", "Cross-run dependency must be accepted done")
                conn.execute("INSERT INTO pipeline_dependencies VALUES(?,?)", (run_id, dependency))
            self._event(conn, run_id, None, "run_created", {"definition_sha256": definition_sha256})
        return self.read_run(run_id)

    def create_step(self, run_id, step_id, item_id, ordinal, *, dependencies=(), policy=None):
        for value, label in ((step_id, "step_id"), (item_id, "item_id")):
            _text(value, label)
        if type(ordinal) is not int or ordinal < 0:
            raise StoreError("INVALID_ORDINAL", "Step ordinal must be a nonnegative integer")
        if not isinstance(dependencies, (list, tuple)) or len(set(dependencies)) != len(dependencies):
            raise StoreError("INVALID_DEPENDENCIES", "Dependencies must be distinct step identities")
        for dependency in dependencies:
            _text(dependency, "dependency")
        if not isinstance({} if policy is None else policy, dict):
            raise StoreError("INVALID_POLICY", "Step policy must be a JSON object")
        values = (ordinal, _json(list(dependencies)), _json({} if policy is None else policy))
        with self.transaction() as conn:
            self.assert_owner(run_id)
            row = conn.execute("SELECT * FROM wf_steps WHERE run_id=? AND step_id=? AND item_id=?", (run_id, step_id, item_id)).fetchone()
            if row is not None:
                if tuple(row[key] for key in ("ordinal", "dependency_json", "policy_json")) != values:
                    raise StoreError("SNAPSHOT_CONFLICT", "Step membership/order/policy changed")
                return dict(row)
            if conn.execute("SELECT 1 FROM wf_steps WHERE run_id=? AND ordinal=?", (run_id, ordinal)).fetchone() is not None:
                raise StoreError("ORDINAL_CONFLICT", "Step ordinals cannot be reused by another subject")
            conn.execute("INSERT INTO wf_steps VALUES(?,?,?,?, 'waiting',NULL,?,?,0,0)", (run_id, step_id, item_id, *values))
            conn.execute("INSERT INTO pipeline_steps(pipeline_id,step_index,agent_type,status,started_at) VALUES(?,?,?,'open',?)", (run_id, ordinal, "workflow", _now()))
        return dict(self.connection.execute("SELECT * FROM wf_steps WHERE run_id=? AND step_id=? AND item_id=?", (run_id, step_id, item_id)).fetchone())

    def create_attempt(self, task):
        wire = task_to_dict(task) if not isinstance(task, Mapping) else dict(task)
        parsed = task_from_dict(wire)
        run_id, step_id, item_id, attempt_id = parsed.run_id, parsed.step_id, parsed.item_id, parsed.attempt_id
        with self.transaction() as conn:
            self.assert_owner(run_id)
            existing = conn.execute("SELECT * FROM wf_attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
            if existing is not None:
                if existing["task_json"] != _json(wire):
                    raise StoreError("ATTEMPT_CONFLICT", "Attempt IDs cannot be reused")
                return dict(existing)
            if self.is_cancelled(run_id) or self._run(conn, run_id)["status"] in _TERMINAL:
                raise StoreError("CANCELLED_OR_TERMINAL", "Cannot dispatch in a cancelled/terminal run")
            step = conn.execute("SELECT * FROM wf_steps WHERE run_id=? AND step_id=? AND item_id=?", (run_id, step_id, item_id)).fetchone()
            if step is None or step["status"] in _TERMINAL:
                raise StoreError("INVALID_STEP", "Attempt requires a known nonterminal step")
            if step["current_attempt_id"] is not None:
                previous = conn.execute("SELECT * FROM wf_attempts WHERE attempt_id=?", (step["current_attempt_id"],)).fetchone()
                if previous["settled_at"] is None:
                    raise StoreError("UNSETTLED_ATTEMPT", "Reconcile the existing attempt before another dispatch")
            ordinal = conn.execute("SELECT COALESCE(MAX(ordinal),-1)+1 FROM wf_attempts WHERE run_id=? AND step_id=? AND item_id=?", (run_id, step_id, item_id)).fetchone()[0]
            conn.execute("INSERT INTO wf_attempts VALUES(?,?,?,?,?,?,?,'prepared',NULL,NULL,NULL,?,NULL)", (attempt_id, run_id, step_id, item_id, ordinal, _json(wire), parsed.semantic_sha256, _now()))
            conn.execute("UPDATE wf_steps SET current_attempt_id=? WHERE run_id=? AND step_id=? AND item_id=?", (attempt_id, run_id, step_id, item_id))
            conn.execute("UPDATE pipeline_steps SET last_task_id=?,current_task=? WHERE pipeline_id=? AND step_index=?", (attempt_id, parsed.duty, run_id, step["ordinal"]))
            self._event(conn, run_id, attempt_id, "attempt_prepared", {"task_sha256": parsed.semantic_sha256})
        return dict(self.connection.execute("SELECT * FROM wf_attempts WHERE attempt_id=?", (attempt_id,)).fetchone())

    def _attempt(self, conn, run_id, attempt_id):
        row = conn.execute("SELECT a.*,s.current_attempt_id FROM wf_attempts a JOIN wf_steps s USING(run_id,step_id,item_id) WHERE a.attempt_id=? AND a.run_id=?", (attempt_id, run_id)).fetchone()
        if row is None:
            raise StoreError("UNKNOWN_ATTEMPT", "Attempt does not belong to this run")
        return row

    def bind_attempt(self, run_id, attempt_id, binding):
        if not isinstance(binding, dict) or not binding:
            raise StoreError("INVALID_BINDING", "Native identity must be a nonempty JSON object")
        encoded, rejection = _json(binding), None
        with self.transaction() as conn:
            self.assert_owner(run_id)
            row = self._attempt(conn, run_id, attempt_id)
            if row["binding_json"] == encoded:
                return dict(row)
            if row["binding_json"] is not None or row["settled_at"] is not None or row["current_attempt_id"] != attempt_id or self.is_cancelled(run_id) or self._run(conn, run_id)["status"] in _TERMINAL:
                self._event(conn, run_id, attempt_id, "binding_rejected", binding)
                rejection = StoreError("BINDING_CONFLICT", "Stale/cancelled or differing native identity")
            else:
                conn.execute("UPDATE wf_attempts SET binding_json=?,status='bound' WHERE attempt_id=?", (encoded, attempt_id))
                self._event(conn, run_id, attempt_id, "attempt_bound", binding)
        if rejection:
            raise rejection
        return dict(self.connection.execute("SELECT * FROM wf_attempts WHERE attempt_id=?", (attempt_id,)).fetchone())

    def record_result(self, run_id, attempt_id, result):
        wire = result_to_dict(result) if not isinstance(result, Mapping) else dict(result)
        encoded, rejection = _json(wire), None
        with self.transaction() as conn:
            self.assert_owner(run_id)
            try:
                row = self._attempt(conn, run_id, attempt_id)
            except StoreError as exc:
                self._event(conn, run_id, None, "result_rejected", {"attempt_id": attempt_id, "result": wire, "reason": exc.code})
                rejection, row = exc, None
            if row is not None:
                if row["result_json"] == encoded:
                    return dict(row)
                try:
                    parsed = result_from_dict(wire)
                    validation = validate_result(parsed, task_from_dict(json.loads(row["task_json"])))
                    valid = validation.valid
                except (ValueError, TypeError, KeyError) as exc:
                    valid, validation = False, str(exc)
                if not valid or row["result_json"] is not None or row["settled_at"] is not None or row["current_attempt_id"] != attempt_id or row["binding_json"] is None:
                    self._event(conn, run_id, attempt_id, "result_rejected", {"result": wire, "reason": str(validation)})
                    rejection = StoreError("RESULT_REJECTED", "Malformed/stale/unbound/differing result retained without advancement")
                else:
                    # Native settlement is not business acceptance. Late successes remain observations.
                    conn.execute("UPDATE wf_attempts SET result_json=?,status=?,error_json=?,settled_at=? WHERE attempt_id=?", (encoded, parsed.status, _json(wire["error"]) if wire["error"] is not None else None, _now(), attempt_id))
                    self._event(conn, run_id, attempt_id, "attempt_settled", {"result": wire, "cancel_requested": self.is_cancelled(run_id)})
        if rejection:
            raise rejection
        return dict(self.connection.execute("SELECT * FROM wf_attempts WHERE attempt_id=?", (attempt_id,)).fetchone())

    def record_native_review(self, run_id, attempt_id, reply):
        """Retain the native review envelope; never convert it to a TaskResult."""
        if not isinstance(reply, dict):
            raise StoreError("REVIEW_REJECTED", "Native review requires its original JSON envelope")
        encoded, rejection = _json(reply), None
        with self.transaction() as conn:
            self.assert_owner(run_id)
            row = self._attempt(conn, run_id, attempt_id)
            existing = conn.execute(
                "SELECT payload_json FROM wf_events WHERE run_id=? AND attempt_id=? "
                "AND event_type='native_review_result' ORDER BY event_id",
                (run_id, attempt_id)).fetchone()
            if existing is not None and _json(json.loads(existing[0])["reply"]) == encoded:
                return dict(row)
            payload = reply.get("payload")
            valid = (reply.get("run_id") == run_id and reply.get("attempt_id") == attempt_id
                     and type(reply.get("owner_epoch")) is int
                     and reply["owner_epoch"] == self._run(conn, run_id)["owner_epoch"]
                     and reply.get("status") in {"completed", "blocked", "failed", "cancelled"}
                     and isinstance(payload, dict)
                     and (payload.get("locally_settled") is True or payload.get("settled") is True))
            if (not valid or existing is not None or row["binding_json"] is None
                    or row["result_json"] is not None or row["settled_at"] is not None
                    or row["current_attempt_id"] != attempt_id
                    or json.loads(row["task_json"])["duty"] != "review"):
                self._event(conn, run_id, attempt_id, "native_review_rejected", {"reply": reply})
                rejection = StoreError("REVIEW_REJECTED", "Unbound/stale/unsettled/differing native review retained without advancement")
            else:
                binding = payload.get("binding")
                if binding is None or binding != json.loads(row["binding_json"]):
                    self._event(conn, run_id, attempt_id, "native_review_rejected", {"reply": reply})
                    rejection = StoreError("REVIEW_REJECTED", "Native review binding differs from the committed observation")
                else:
                    conn.execute("UPDATE wf_attempts SET status=?,settled_at=? WHERE attempt_id=?",
                                 (reply["status"], _now(), attempt_id))
                    self._event(conn, run_id, attempt_id, "native_review_result",
                                {"reply": reply, "locally_settled": True})
        if rejection:
            raise rejection
        return dict(self.connection.execute("SELECT * FROM wf_attempts WHERE attempt_id=?", (attempt_id,)).fetchone())

    def record_attempt_error(self, run_id, attempt_id, error, *, settled: bool):
        """Record launch/tool faults; unknown effects remain unsettled and unretryable."""
        if not isinstance(error, dict) or set(error) != {"code", "message", "details"} or type(settled) is not bool:
            raise StoreError("INVALID_ERROR", "Expected structured error and an observed settlement flag")
        encoded = _json(error)
        with self.transaction() as conn:
            self.assert_owner(run_id)
            row = self._attempt(conn, run_id, attempt_id)
            if row["error_json"] == encoded and (row["settled_at"] is not None) == settled:
                return dict(row)
            if row["current_attempt_id"] != attempt_id or row["settled_at"] is not None:
                raise StoreError("ATTEMPT_CONFLICT", "Cannot replace an accepted settlement")
            conn.execute("UPDATE wf_attempts SET error_json=?,status=?,settled_at=? WHERE attempt_id=?", (encoded, "failed" if settled else "blocked", _now() if settled else None, attempt_id))
            self._event(conn, run_id, attempt_id, "attempt_error", {"error": error, "settled": settled})
        return dict(self.connection.execute("SELECT * FROM wf_attempts WHERE attempt_id=?", (attempt_id,)).fetchone())

    def update_run(self, run_id, status):
        if status not in _STATUSES:
            raise StoreError("INVALID_STATUS", "Unknown run status")
        with self.transaction() as conn:
            self._update_run(conn, run_id, status)
        return self.read_run(run_id)

    def _update_run(self, conn, run_id, status):
        self.assert_owner(run_id)
        row = self._run(conn, run_id)
        if row["status"] in _TERMINAL and row["status"] != status:
            raise StoreError("TERMINAL_RUN", "Terminal runs cannot be reopened")
        if row["cancel_requested"] and status in ("done", "preview"):
            raise StoreError("CANCELLED", "Late success cannot accept a cancelled run")
        now = _now()
        conn.execute("UPDATE wf_runs SET status=?,updated_at=? WHERE run_id=?", (status, now, run_id))
        conn.execute("UPDATE pipelines SET status=?,completed_at=? WHERE id=?", (_PROJECTION[status], now if status in _TERMINAL else None, run_id))
        self._event(conn, run_id, None, "run_transition", {"from": row["status"], "to": status})

    def update_step(self, run_id, step_id, item_id, status, *, current_task=None, error_summary=None, output_paths=(), session_id=None):
        with self.transaction() as conn:
            self._update_step(conn, run_id, step_id, item_id, status, current_task=current_task,
                              error_summary=error_summary, output_paths=output_paths, session_id=session_id)

    def _update_step(self, conn, run_id, step_id, item_id, status, *, current_task=None, error_summary=None, output_paths=(), session_id=None):
        if status not in _STATUSES:
            raise StoreError("INVALID_STATUS", "Unknown step status")
        self.assert_owner(run_id)
        row = conn.execute("SELECT * FROM wf_steps WHERE run_id=? AND step_id=? AND item_id=?", (run_id, step_id, item_id)).fetchone()
        if row is None or (row["status"] in _TERMINAL and row["status"] != status):
            raise StoreError("INVALID_STEP", "Unknown/terminal step")
        if self.is_cancelled(run_id) and status in ("done", "preview"):
            raise StoreError("CANCELLED", "Late success cannot accept a cancelled step")
        conn.execute("UPDATE wf_steps SET status=? WHERE run_id=? AND step_id=? AND item_id=?", (status, run_id, step_id, item_id))
        conn.execute("UPDATE pipeline_steps SET status=?,completed_at=?,current_task=?,error_summary=?,output_path=?,output_artifact_paths=?,session_id=? WHERE pipeline_id=? AND step_index=?", (_PROJECTION[status], _now() if status in _TERMINAL else None, current_task, error_summary[:2000] if error_summary else None, output_paths[0] if output_paths else None, _json(list(output_paths)), session_id, run_id, row["ordinal"]))
        self._event(conn, run_id, row["current_attempt_id"], "step_transition", {"step_id": step_id, "item_id": item_id, "from": row["status"], "to": status})

    def _report_failure_completion(self, conn, step, attempt, record):
        """Allow report progress from retained failure, never native acceptance."""
        data = record["data"]
        if (json.loads(step["policy_json"]).get("allow_task_failure") is not True
                or record["status"] != "done" or record["artifacts"]
                or attempt["status"] != "failed" or attempt["result_json"] is not None
                or attempt["settled_at"] is None or attempt["error_json"] is None
                or json.loads(attempt["task_json"])["duty"] == "review"
                or data.get("tooling_failure") is not True or data.get("run_status") != "failed"
                or _json(data.get("error")) != attempt["error_json"]
                or any(key in data for key in ("result", "verification", "outputs", "native_result", "native_review", "review_gate"))):
            return False
        evidence = {}
        for event in conn.execute(
                "SELECT event_id,event_type,payload_json FROM wf_events WHERE run_id=? AND attempt_id=? "
                "AND event_type IN ('attempt_error','adapter_process_settled','report_task_failure') ORDER BY event_id",
                (step["run_id"], attempt["attempt_id"])):
            evidence[event["event_type"]] = event
        if set(evidence) != {"attempt_error", "adapter_process_settled", "report_task_failure"}:
            return False
        failure, teardown, report = (evidence[key] for key in
                                     ("attempt_error", "adapter_process_settled", "report_task_failure"))
        receipt = json.loads(teardown["payload_json"])
        return (failure["event_id"] < teardown["event_id"] < report["event_id"]
                and failure["payload_json"] == _json({"error": data["error"], "settled": True})
                and report["payload_json"] == _json({"error": data["error"]})
                and isinstance(receipt, dict) and receipt.get("graceful_eof_exit") is True
                and receipt.get("process_settled") is True
                and type(receipt.get("returncode")) is int and receipt["returncode"] == 0
                and receipt.get("shutdown_actions") == ["stdin_eof_sent"]
                and receipt.get("protocol_errors", []) == [])

    def complete_step(self, run_id, step_id, item_id, record):
        """Commit item evidence and its legacy projection at one crash boundary."""
        fields = {"step_id", "item_id", "ordinal", "status", "artifacts", "data"}
        if (not isinstance(record, dict) or set(record) != fields
                or record["step_id"] != step_id or record["item_id"] != item_id
                or type(record["ordinal"]) is not int or record["ordinal"] < 0
                or record["status"] not in ("done", "skipped")
                or not isinstance(record["artifacts"], list) or not isinstance(record["data"], dict)):
            raise StoreError("COMPLETION_REJECTED", "Expected an exact JSON item completion record")
        paths = []
        for ref in record["artifacts"]:
            if not isinstance(ref, dict) or not {"name", "path", "sha256", "media_type"} <= set(ref):
                raise StoreError("COMPLETION_REJECTED", "Completion requires full absolute artifact references")
            _hash(ref["sha256"])
            path = ref["path"]
            if not isinstance(path, str) or not Path(path).is_absolute() or ".." in Path(path).parts or path in paths:
                raise StoreError("COMPLETION_REJECTED", "Completion paths must be distinct and absolute")
            paths.append(path)
        encoded, rejection = _json(record), None
        with self.transaction() as conn:
            self.assert_owner(run_id)
            previous = None
            for event in conn.execute("SELECT payload_json FROM wf_events WHERE run_id=? AND event_type='item_completed' ORDER BY event_id", (run_id,)):
                payload = json.loads(event[0])
                if (payload.get("step_id"), payload.get("item_id")) == (step_id, item_id):
                    previous = payload
                    break
            if previous is not None and _json(previous) == encoded:
                return record
            row = conn.execute("SELECT * FROM wf_steps WHERE run_id=? AND step_id=? AND item_id=?", (run_id, step_id, item_id)).fetchone()
            if (previous is not None or row is None or row["ordinal"] != record["ordinal"]
                    or row["status"] in _TERMINAL or self.is_cancelled(run_id)
                    or self._run(conn, run_id)["status"] in _TERMINAL):
                rejection = StoreError("COMPLETION_REJECTED", "Differing, cancelled, terminal or stale completion cannot advance")
            elif record["data"].get("attempt_id") != row["current_attempt_id"]:
                rejection = StoreError("COMPLETION_REJECTED", "Completion must identify the exact current attempt")
            elif row["current_attempt_id"] is not None:
                attempt = self._attempt(conn, run_id, row["current_attempt_id"])
                if attempt["settled_at"] is None:
                    rejection = StoreError("UNSETTLED_ATTEMPT", "Completion requires observed local native settlement")
                elif (record["status"] != "done" or attempt["status"] != "completed" or attempt["binding_json"] is None):
                    if not self._report_failure_completion(conn, row, attempt, record):
                        rejection = StoreError("COMPLETION_REJECTED", "Completion requires completed bound execution or an evidence-bound report failure")
            if rejection is not None:
                self._event(conn, run_id, None, "item_completion_rejected", {"record": record, "reason": rejection.code})
            else:
                self._event(conn, run_id, row["current_attempt_id"], "item_completed", record)
                projection = conn.execute("SELECT current_task,session_id FROM pipeline_steps WHERE pipeline_id=? AND step_index=?",
                                          (run_id, row["ordinal"])).fetchone()
                self._update_step(conn, run_id, step_id, item_id, "done", output_paths=paths,
                                  current_task=projection["current_task"], session_id=projection["session_id"])
        if rejection:
            raise rejection
        return record

    def reserve_review(self, run_id, artifact_id, contract, artifact_tier, operation_kind, operation_id, max_full_reviews=4, max_repairs=3):
        with self.transaction() as conn:
            return self._reserve_review(conn, run_id, artifact_id, contract, artifact_tier,
                                        operation_kind, operation_id, max_full_reviews, max_repairs)

    def _reserve_review(self, conn, run_id, artifact_id, contract, artifact_tier, operation_kind, operation_id, max_full_reviews=4, max_repairs=3):
        for value, label in ((artifact_id, "artifact_id"), (contract, "contract"), (artifact_tier, "artifact_tier"), (operation_id, "operation_id")):
            _text(value, label)
        if operation_kind not in ("full_review", "repair") or any(type(cap) is not int or cap < 0 for cap in (max_full_reviews, max_repairs)):
            raise StoreError("INVALID_RESERVATION", "Invalid review operation/caps")
        if max_full_reviews > 4 or max_repairs > 3:
            raise StoreError("INVALID_RESERVATION", "Review ceilings may only be narrowed from four/three")
        self.assert_owner(run_id)
        row = conn.execute("SELECT * FROM wf_reviews WHERE run_id=? AND artifact_id=?", (run_id, artifact_id)).fetchone()
        if row is not None and (row["contract"], row["artifact_tier"]) != (contract, artifact_tier):
            raise StoreError("REVIEW_CONFLICT", "Artifact review identity cannot change")
        for event in conn.execute("SELECT payload_json FROM wf_events WHERE run_id=? AND event_type IN ('review_reserved','review_subject_registered') ORDER BY event_id", (run_id,)):
            payload = json.loads(event[0])
            if payload["artifact_id"] == artifact_id:
                if (payload["max_full_reviews"], payload["max_repairs"]) != (max_full_reviews, max_repairs):
                    raise StoreError("REVIEW_CONFLICT", "Reservation policy changed")
                if (payload.get("operation_kind"), payload.get("operation_id")) == (operation_kind, operation_id):
                    return payload
        if self.is_cancelled(run_id) or self._run(conn, run_id)["status"] in _TERMINAL or (row is not None and row["closed"]):
            raise StoreError("REVIEW_CLOSED", "Cancelled/terminal/closed artifacts cannot reserve another operation")
        reviews, repairs = (row["full_attempts"], row["repair_rounds"]) if row else (0, 0)
        if (operation_kind == "full_review" and reviews >= max_full_reviews) or (operation_kind == "repair" and repairs >= max_repairs):
            raise StoreError("REVIEW_EXHAUSTED", "Artifact operation allowance exhausted")
        reviews += operation_kind == "full_review"
        repairs += operation_kind == "repair"
        conn.execute("INSERT INTO wf_reviews VALUES(?,?,?,?,?,?,0) ON CONFLICT(run_id,artifact_id) DO UPDATE SET full_attempts=excluded.full_attempts,repair_rounds=excluded.repair_rounds", (run_id, artifact_id, artifact_tier, contract, reviews, repairs))
        payload = {"artifact_id": artifact_id, "operation_kind": operation_kind, "operation_id": operation_id, "full_attempts": reviews, "repair_rounds": repairs, "max_full_reviews": max_full_reviews, "max_repairs": max_repairs}
        self._event(conn, run_id, None, "review_reserved", payload)
        return payload


    def register_review_subjects(self, run_id, subjects):
        """Freeze source-selected subjects/policies without charging an invocation."""
        fields = {"artifact_id", "contract", "artifact_tier", "max_full_reviews", "max_repairs"}
        if not isinstance(subjects, (list, tuple)):
            raise StoreError("INVALID_RESERVATION", "Review subjects must be an explicit array")
        for subject in subjects:
            if not isinstance(subject, dict) or set(subject) != fields:
                raise StoreError("INVALID_RESERVATION", "Review subject fields must be exact")
            for name in ("artifact_id", "contract", "artifact_tier"):
                _text(subject[name], name)
            for name, ceiling in (("max_full_reviews", 4), ("max_repairs", 3)):
                if type(subject[name]) is not int or not 0 <= subject[name] <= ceiling:
                    raise StoreError("INVALID_RESERVATION", "Review subject ceilings may only be narrowed")
        result = []
        with self.transaction() as conn:
            self.assert_owner(run_id)
            for subject in subjects:
                artifact = subject["artifact_id"]
                row = conn.execute("SELECT * FROM wf_reviews WHERE run_id=? AND artifact_id=?", (run_id, artifact)).fetchone()
                if row is not None and (row["contract"], row["artifact_tier"]) != (subject["contract"], subject["artifact_tier"]):
                    raise StoreError("REVIEW_CONFLICT", "Review subject identity cannot change")
                registered = False
                for event in conn.execute("SELECT event_type,payload_json FROM wf_events WHERE run_id=? AND event_type IN ('review_reserved','review_subject_registered')", (run_id,)):
                    payload = json.loads(event["payload_json"])
                    if payload["artifact_id"] == artifact:
                        if (payload["max_full_reviews"], payload["max_repairs"]) != (subject["max_full_reviews"], subject["max_repairs"]):
                            raise StoreError("REVIEW_CONFLICT", "Review subject policy cannot change")
                        registered |= event["event_type"] == "review_subject_registered"
                if not registered:
                    if self.is_cancelled(run_id) or self._run(conn, run_id)["status"] in _TERMINAL or (row is not None and row["closed"]):
                        raise StoreError("REVIEW_CLOSED", "Cancelled/terminal/closed subjects cannot be registered")
                    conn.execute("INSERT INTO wf_reviews VALUES(?,?,?,?,0,0,0) ON CONFLICT(run_id,artifact_id) DO NOTHING",
                                 (run_id, artifact, subject["artifact_tier"], subject["contract"]))
                    self._event(conn, run_id, None, "review_subject_registered", subject)
                result.append(dict(conn.execute("SELECT * FROM wf_reviews WHERE run_id=? AND artifact_id=?", (run_id, artifact)).fetchone()))
        return result

    def close_review(self, run_id, artifact_id):
        with self.transaction() as conn:
            self.assert_owner(run_id)
            if conn.execute("UPDATE wf_reviews SET closed=1 WHERE run_id=? AND artifact_id=?", (run_id, artifact_id)).rowcount != 1:
                raise StoreError("UNKNOWN_ARTIFACT", "No review record for artifact")
            self._event(conn, run_id, None, "review_closed", {"artifact_id": artifact_id})

    def reserve_step_operation(self, run_id, step_id, item_id, operation_kind, operation_id, *, maximum):
        """Persist narrow verifier/local-repair charges, independent of artifact reviews."""
        with self.transaction() as conn:
            return self._reserve_step_operation(conn, run_id, step_id, item_id, operation_kind,
                                                operation_id, maximum=maximum)

    def _reserve_step_operation(self, conn, run_id, step_id, item_id, operation_kind, operation_id, *, maximum):
        if operation_kind not in ("verify_pass", "repair_round") or type(maximum) is not int or maximum < 0:
            raise StoreError("INVALID_RESERVATION", "Invalid step charge/cap")
        _text(operation_id, "operation_id")
        field = "verify_passes" if operation_kind == "verify_pass" else "repair_rounds"
        self.assert_owner(run_id)
        for event in conn.execute("SELECT payload_json FROM wf_events WHERE run_id=? AND event_type='step_reserved'", (run_id,)):
            payload = json.loads(event[0])
            if (payload["step_id"], payload["item_id"], payload["operation_kind"]) == (step_id, item_id, operation_kind):
                if payload["maximum"] != maximum:
                    raise StoreError("SNAPSHOT_CONFLICT", "Step charge policy changed")
                if payload["operation_id"] == operation_id:
                    return payload
        row = conn.execute("SELECT * FROM wf_steps WHERE run_id=? AND step_id=? AND item_id=?", (run_id, step_id, item_id)).fetchone()
        if row is None or self.is_cancelled(run_id) or row["status"] in _TERMINAL or row[field] >= maximum:
            raise StoreError("STEP_EXHAUSTED", "Unknown/closed/cancelled step or exhausted allowance")
        conn.execute(f"UPDATE wf_steps SET {field}={field}+1 WHERE run_id=? AND step_id=? AND item_id=?", (run_id, step_id, item_id))
        payload = {"step_id": step_id, "item_id": item_id, "operation_kind": operation_kind, "operation_id": operation_id, "maximum": maximum, "count": row[field] + 1}
        self._event(conn, run_id, None, "step_reserved", payload)
        return payload

    def reserve_operations(self, run_id, reviews, steps):
        """Reserve a dispatch group's charges together, or commit none of them."""
        if not isinstance(reviews, (list, tuple)) or not isinstance(steps, (list, tuple)):
            raise StoreError("INVALID_RESERVATION", "Coupled reservations require review and step arrays")
        required_review = {"artifact_id", "contract", "artifact_tier", "operation_kind", "operation_id"}
        optional_review = {"max_full_reviews", "max_repairs"}
        required_step = {"step_id", "item_id", "operation_kind", "operation_id", "maximum"}
        for entry in reviews:
            if (not isinstance(entry, dict) or not required_review <= set(entry)
                    or set(entry) - required_review - optional_review):
                raise StoreError("INVALID_RESERVATION", "Invalid coupled review reservation fields")
        for entry in steps:
            if not isinstance(entry, dict) or set(entry) != required_step:
                raise StoreError("INVALID_RESERVATION", "Invalid coupled step reservation fields")
        with self.transaction() as conn:
            self.assert_owner(run_id)
            review_receipts = [self._reserve_review(conn, run_id, **entry) for entry in reviews]
            step_receipts = [self._reserve_step_operation(conn, run_id, **entry) for entry in steps]
            return {"reviews": review_receipts, "steps": step_receipts}

    def record_decision(self, run_id, key, question, answer=None):
        _text(key, "decision_key")
        if not isinstance(question, dict) or set(question) != {"key", "question", "options"} or question["key"] != key or not isinstance(question["options"], list) or not question["options"]:
            raise StoreError("INVALID_DECISION", "Decision requires fixed key, question and options")
        if answer is not None and (not isinstance(answer, dict) or set(answer) != {"key", "answer"} or answer["key"] != key or answer["answer"] not in question["options"]):
            raise StoreError("INVALID_DECISION", "Answer must select an exact persisted option")
        with self.transaction() as conn:
            self.assert_owner(run_id)
            row = conn.execute("SELECT * FROM wf_decisions WHERE run_id=? AND decision_key=?", (run_id, key)).fetchone()
            encoded = _json(answer) if answer is not None else None
            if row is not None:
                if row["question_json"] != _json(question) or (row["answer_json"] is not None and row["answer_json"] != encoded):
                    raise StoreError("DECISION_CONFLICT", "Decision snapshot/answer cannot change")
                if row["answer_json"] == encoded:
                    return dict(row)
                conn.execute("UPDATE wf_decisions SET answer_json=?,status='answered' WHERE run_id=? AND decision_key=?", (encoded, run_id, key))
            else:
                conn.execute("INSERT INTO wf_decisions VALUES(?,?,?,?,?)", (run_id, key, _json(question), encoded, "answered" if answer is not None else "waiting"))
            self._event(conn, run_id, None, "decision_recorded", {"question": question, "answer": answer})
        return dict(self.connection.execute("SELECT * FROM wf_decisions WHERE run_id=? AND decision_key=?", (run_id, key)).fetchone())
