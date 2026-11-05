"""Isolated real-SQLite regressions; no native execution or live project access."""
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from _shared.workflow.src import contracts as c
from _shared.workflow.src.store import Store, StoreError


# Independent literal retained v1.5/v1.6 layouts, not generated from store DDL.
def legacy(path, *, expanded=False, status="done", wrong_fk=False):
    connection = sqlite3.connect(path)
    connection.executescript("""
        PRAGMA user_version=73;
        CREATE TABLE unrelated(id INTEGER PRIMARY KEY, payload BLOB);
        INSERT INTO unrelated VALUES(1,X'00FF');
        CREATE TABLE pipelines(id TEXT PRIMARY KEY,shape TEXT NOT NULL,
            started_at INTEGER NOT NULL,completed_at INTEGER,
            status TEXT NOT NULL CHECK(status IN
            ('open','in_progress','blocked','done','failed','cancelled')));
    """)
    foreign = "" if wrong_fk else " REFERENCES pipelines(id) ON DELETE CASCADE"
    additions = ",current_task TEXT,error_summary TEXT,session_id TEXT,channel TEXT" if expanded else ""
    connection.execute(f"""CREATE TABLE pipeline_steps(
        pipeline_id TEXT NOT NULL{foreign},step_index INTEGER NOT NULL,
        agent_type TEXT NOT NULL,status TEXT NOT NULL,output_path TEXT,
        last_task_id TEXT,inbox_path TEXT,output_artifact_paths TEXT,
        started_at INTEGER NOT NULL,completed_at INTEGER{additions},
        PRIMARY KEY(pipeline_id,step_index))""")
    connection.execute("""CREATE TABLE pipeline_dependencies(
        pipeline_id TEXT NOT NULL REFERENCES pipelines(id) ON DELETE CASCADE,
        depends_on_pipeline_id TEXT NOT NULL REFERENCES pipelines(id) ON DELETE RESTRICT,
        PRIMARY KEY(pipeline_id,depends_on_pipeline_id))""")
    connection.execute("INSERT INTO pipelines VALUES('old','retained',1,2,?)", (status,))
    connection.execute("INSERT INTO pipeline_steps(pipeline_id,step_index,agent_type,status,started_at,output_path) VALUES('old',0,'producer','done',1,'original.md')")
    connection.commit()
    connection.close()


def task_wire(project, attempt_id="attempt-1", *, run_id="run-1", item_id="item-1"):
    candidate = project / "docs" / "tmp" / "pipeline-runs" / run_id / "attempts" / attempt_id
    task = {"schema_version": 1, "run_id": run_id, "step_id": "step-1", "item_id": item_id,
            "attempt_id": attempt_id, "duty": "verify", "instructions": "Read complete synthetic evidence; return a verdict.",
            "inputs": [], "resources": [], "candidate_dir": str(candidate), "outputs": [],
            "allowed_actions": ["read"], "read_roots": [str(project)], "write_roots": [],
            "result_schema": c.task_result_schema(), "artifact_id": "artifact-1",
            "review_contract": "vault-wiki", "artifact_tier": "B", "semantic_sha256": ""}
    task["semantic_sha256"] = c.semantic_hash(task)
    return task


def result_wire(task, *, verification="PASS"):
    return {"schema_version": 1, "run_id": task["run_id"], "step_id": task["step_id"],
            "item_id": task["item_id"], "attempt_id": task["attempt_id"], "status": "completed",
            "verification": verification, "artifacts": [], "task_sha256": task["semantic_sha256"],
            "evidence": [], "error": None, "decision": None}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name) / "project"
        self.project.mkdir()
        self.stores = []
        self.addCleanup(self.close_stores)

    def close_stores(self):
        for store in reversed(self.stores):
            store.close()

    def store(self, **kwargs):
        store = Store(self.project, **kwargs)
        self.stores.append(store)
        store.initialize()
        return store

    def run_record(self, store, name="run-1", **kwargs):
        return store.create_run(name, "synthetic", "a" * 64, {"source": "invented"}, [], **kwargs)

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(StoreError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def report_failure(self, store, run_id, *, policy=None, settled=True, events=True, duty="verify"):
        attempt_id = run_id + "-attempt"
        task = task_wire(self.project, attempt_id, run_id=run_id)
        task["duty"] = duty
        task["semantic_sha256"] = c.semantic_hash(task)
        error = {"code": "RESULT_REJECTED", "message": "Native result has the wrong attempt identity",
                 "details": {"errors": ["attempt_id differs"]}}
        receipt = {"returncode": 0, "shutdown_actions": ["stdin_eof_sent"], "process_settled": True,
                   "graceful_eof_exit": True, "protocol_errors": []}
        record = {"step_id": "step-1", "item_id": "item-1", "ordinal": 0, "status": "done", "artifacts": [],
                  "data": {"attempt_id": attempt_id, "claim_id": "claim-1", "tooling_failure": True,
                           "run_status": "failed", "error": error}}
        store.create_step(run_id, "step-1", "item-1", 0,
                          policy={"allow_task_failure": True} if policy is None else policy)
        store.update_step(run_id, "step-1", "item-1", "executing", current_task=duty)
        store.create_attempt(task)
        store.bind_attempt(run_id, attempt_id, {"id": run_id + "-child"})
        store.record_attempt_error(run_id, attempt_id, error, settled=settled)
        if events:
            store.append_event(run_id, attempt_id, "adapter_process_settled", receipt)
            store.append_event(run_id, attempt_id, "report_task_failure", {"error": error})
        return record, receipt

    def test_empty_database_and_unrelated_database_have_real_constraints(self):
        store = self.store()
        self.assertEqual(store.connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(store.connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(store.connection.execute("SELECT version FROM wf_schema").fetchone()[0], 1)
        self.assertEqual(store.connection.execute("PRAGMA user_version").fetchone()[0], 0)
        self.run_record(store)
        with self.assertRaises(sqlite3.IntegrityError):
            with store.transaction() as connection:
                connection.execute("INSERT INTO wf_events(run_id,event_type,payload_json,created_at) VALUES('phantom','x','{}',1)")
        with self.assertRaises(sqlite3.IntegrityError):
            with store.transaction() as connection:
                connection.execute("INSERT INTO wf_schema VALUES(1)")
        with self.assertRaises(sqlite3.IntegrityError):
            with store.transaction() as connection:
                connection.execute("UPDATE wf_runs SET cancel_requested=3 WHERE run_id='run-1'")

    def test_legacy_ten_and_fourteen_columns_preserve_data_and_user_version(self):
        for expanded in (False, True):
            with self.subTest(expanded=expanded), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                legacy(root / "state.db", expanded=expanded)
                store = Store(root)
                try:
                    store.initialize()
                    self.assertEqual(store.connection.execute("PRAGMA user_version").fetchone()[0], 73)
                    self.assertEqual(tuple(store.connection.execute("SELECT * FROM unrelated").fetchone()), (1, b"\x00\xff"))
                    original = store.connection.execute("SELECT * FROM pipeline_steps WHERE pipeline_id='old'").fetchone()
                    self.assertEqual(len(original), 14)
                    self.assertEqual(original["agent_type"], "producer")
                    self.assertEqual(original["output_path"], "original.md")
                    self.assertEqual(tuple(original)[10:], (None, None, None, None))
                    self.run_record(store)
                    with store.acquire_owner("run-1"):
                        store.create_step("run-1", "step-1", "item-1", 7)
                        store.update_step("run-1", "step-1", "item-1", "executing")
                        projected = store.connection.execute("SELECT * FROM pipeline_steps WHERE pipeline_id='run-1'").fetchone()
                        self.assertEqual(projected["step_index"], 7)
                        self.assertEqual(projected["status"], "in_progress")
                        self.assertIsNone(projected["channel"])
                        store.update_run("run-1", "done")
                    self.assertEqual(store.connection.execute("SELECT status FROM pipelines WHERE id='old'").fetchone()[0], "done")
                finally:
                    store.close()

    def test_active_legacy_refusal_precedes_any_schema_or_wal_effect(self):
        legacy(self.project / "state.db", status="blocked")
        before = (self.project / "state.db").read_bytes()
        names = {path.name for path in self.project.iterdir()}
        store = Store(self.project)
        self.assert_code("ACTIVE_LEGACY_RUN", store.initialize)
        self.assertEqual((self.project / "state.db").read_bytes(), before)
        self.assertEqual({path.name for path in self.project.iterdir()}, names)
        with closing(sqlite3.connect(self.project / "state.db")) as connection, connection:
            self.assertEqual(len(connection.execute("PRAGMA table_info(pipeline_steps)").fetchall()), 10)

    def test_incompatible_legacy_foreign_key_and_future_namespace_refuse_unchanged(self):
        legacy(self.project / "state.db", wrong_fk=True)
        before = (self.project / "state.db").read_bytes()
        self.assert_code("INCOMPATIBLE_SCHEMA", Store(self.project).initialize)
        self.assertEqual((self.project / "state.db").read_bytes(), before)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with closing(sqlite3.connect(root / "state.db")) as connection, connection:
                connection.execute("CREATE TABLE wf_schema(version INTEGER)")
                connection.execute("INSERT INTO wf_schema VALUES(2)")
            before = (root / "state.db").read_bytes()
            self.assert_code("INCOMPATIBLE_SCHEMA", Store(root).initialize)
            self.assertEqual((root / "state.db").read_bytes(), before)

    def test_complete_future_version_refuses_without_creating_journals(self):
        store = self.store()
        self.run_record(store)
        store.close()
        with closing(sqlite3.connect(self.project / "state.db")) as connection, connection:
            trigger = connection.execute("SELECT sql FROM sqlite_master WHERE name='wf_schema_update'").fetchone()[0]
            connection.execute("DROP TRIGGER wf_schema_update")
            connection.execute("PRAGMA ignore_check_constraints=ON")
            connection.execute("UPDATE wf_schema SET version=2")
            connection.execute(trigger)
        before = {path.name: path.read_bytes() for path in self.project.iterdir()}
        self.assert_code("INCOMPATIBLE_SCHEMA", Store(self.project).initialize)
        self.assertEqual({path.name: path.read_bytes() for path in self.project.iterdir()}, before)

    def test_attempt_foreign_keys_cannot_cross_runs_or_step_subjects(self):
        store = self.store()
        self.run_record(store)
        self.run_record(store, "run-2")
        with store.acquire_owner("run-1"):
            store.create_step("run-1", "step-1", "item-1", 0)
            store.create_attempt(task_wire(self.project))
            self.assert_code("ORDINAL_CONFLICT", store.create_step, "run-1", "step-1", "different-item", 0)
            with self.assertRaises(sqlite3.IntegrityError):
                with store.transaction() as connection:
                    connection.execute("INSERT INTO wf_events(run_id,attempt_id,event_type,payload_json,created_at) VALUES('run-2','attempt-1','wrong-run','{}',1)")
        with store.acquire_owner("run-2"):
            store.create_step("run-2", "step-1", "item-1", 0)
            with self.assertRaises(sqlite3.IntegrityError):
                with store.transaction() as connection:
                    connection.execute("UPDATE wf_steps SET current_attempt_id='attempt-1' WHERE run_id='run-2'")

    def test_readonly_missing_path_does_not_bootstrap_and_legacy_status_is_empty(self):
        absent = self.project / "absent"
        self.assert_code("DATABASE_UNAVAILABLE", Store, absent, read_only=True)
        self.assertFalse(absent.exists())
        legacy(self.project / "state.db")
        before = (self.project / "state.db").read_bytes()
        names = {path.name for path in self.project.iterdir()}
        readonly = self.store(read_only=True)
        self.assertIsNone(readonly.read_run("unknown"))
        self.assert_code("READ_ONLY", readonly.request_cancel, "unknown")
        readonly.close()
        self.assertEqual({path.name for path in self.project.iterdir()}, names)
        self.assertEqual((self.project / "state.db").read_bytes(), before)

    def test_readonly_closed_wal_database_and_live_wal_status_create_no_files(self):
        writable = self.store()
        self.run_record(writable)
        names = {path.name for path in self.project.iterdir()}
        reader = self.store(read_only=True)
        self.assertEqual(reader.read_run("run-1")["status"], "waiting")
        reader.close()
        self.assertEqual({path.name for path in self.project.iterdir()}, names)
        writable.close()
        names = {path.name for path in self.project.iterdir()}
        before = (self.project / "state.db").read_bytes()
        reader = self.store(read_only=True)
        self.assertEqual(reader.read_run("run-1")["definition_sha256"], "a" * 64)
        reader.close()
        self.assertEqual({path.name for path in self.project.iterdir()}, names)
        self.assertEqual((self.project / "state.db").read_bytes(), before)

    def test_readonly_snapshot_includes_committed_wal_under_active_owner_without_holding_cancel(self):
        store = self.store()
        self.run_record(store)
        with store.acquire_owner("run-1"):
            store.update_run("run-1", "executing")
            names = {path.name for path in self.project.iterdir()}
            reader = self.store(read_only=True)
            self.assertEqual(reader.read_run("run-1")["status"], "executing")
            other = self.store()
            other.request_cancel("run-1")
            self.assertTrue(store.is_cancelled("run-1"))
            reader.close()
            self.assertEqual({path.name for path in self.project.iterdir()}, names)

    def test_readonly_reconstructs_missing_shm_without_creating_one_or_ignoring_wal(self):
        store = self.store()
        self.run_record(store)
        with store.acquire_owner("run-1"):
            store.update_run("run-1", "executing")
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                # A controlled, quiescent DB+WAL copy models a crashed process;
                # no mutation runs concurrently with this fixture copy.
                (root / "state.db").write_bytes((self.project / "state.db").read_bytes())
                (root / "state.db-wal").write_bytes((self.project / "state.db-wal").read_bytes())
                before = {path.name: path.read_bytes() for path in root.iterdir()}
                reader = Store(root, read_only=True)
                try:
                    self.assertEqual(reader.read_run("run-1")["status"], "executing")
                finally:
                    reader.close()
                self.assertEqual({path.name: path.read_bytes() for path in root.iterdir()}, before)

    @unittest.skipUnless(sys.platform == "win32", "Win32 share-deny-write snapshot guard")
    def test_cold_readonly_snapshot_refuses_competing_write_handle_without_effects(self):
        store = self.store()
        self.run_record(store)
        store.close()
        with closing(sqlite3.connect(self.project / "state.db")) as competitor:
            before = {path.name: path.read_bytes() for path in self.project.iterdir()}
            self.assert_code("READ_ONLY_BUSY", Store, self.project, read_only=True)
            self.assertEqual({path.name: path.read_bytes() for path in self.project.iterdir()}, before)
        reader = self.store(read_only=True)
        self.assertEqual("waiting", reader.read_run("run-1")["status"])

    def test_transaction_rollback_and_five_busy_attempts(self):
        store = self.store()
        self.run_record(store)
        with self.assertRaises(RuntimeError):
            with store.transaction() as connection:
                connection.execute("UPDATE wf_runs SET status='executing' WHERE run_id='run-1'")
                raise RuntimeError("synthetic failure")
        self.assertEqual(store.read_run("run-1")["status"], "waiting")
        second = self.store()
        second.connection.execute("PRAGMA busy_timeout=0")
        with store.transaction(), patch("_shared.workflow.src.store.time.sleep") as sleeping:
            self.assert_code("DATABASE_BUSY", second.request_cancel, "run-1")
            self.assertEqual([call.args[0] for call in sleeping.call_args_list], [0.05, 0.1, 0.2, 0.4])
        self.assertFalse(second.is_cancelled("run-1"))

    def test_owner_lock_epoch_and_cancel_are_independent_across_processes(self):
        store = self.store()
        self.run_record(store)
        code = """import importlib,sys,json
from pathlib import Path
sys.path.insert(0,sys.argv[1])
module=importlib.import_module(sys.argv[3]); Store,StoreError=module.Store,module.StoreError
s=Store(Path(sys.argv[2]));s.initialize()
try:
    with s.acquire_owner('run-1'):
        raise AssertionError('competing owner acquired')
except StoreError as e:
    assert e.code=='OWNER_BUSY', e.code
s.request_cancel('run-1');print(json.dumps(s.read_run('run-1')));s.close()
"""
        with store.acquire_owner("run-1") as first:
            process = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code, str(ROOT), str(self.project), Store.__module__], capture_output=True, text=True, timeout=30)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(json.loads(process.stdout)["cancel_requested"], 1)
            self.assertTrue(store.is_cancelled("run-1"))
            self.assert_code("CANCELLED", store.update_run, "run-1", "done")
        with store.acquire_owner("run-1") as second:
            self.assertGreater(second, first)
        self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM wf_events WHERE event_type='cancel_requested'").fetchone()[0], 1)
        store.request_cancel("run-1")
        self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM wf_events WHERE event_type='cancel_requested'").fetchone()[0], 1)

    def test_binding_and_result_survive_restart_and_are_immutable_idempotent(self):
        store = self.store()
        self.run_record(store)
        task = task_wire(self.project)
        result = result_wire(task, verification="FAIL")
        with store.acquire_owner("run-1"):
            store.create_step("run-1", "step-1", "item-1", 0)
            store.create_attempt(task)
            self.assert_code("UNSETTLED_ATTEMPT", store.create_attempt, task_wire(self.project, "attempt-2"))
            store.bind_attempt("run-1", "attempt-1", {"backend": "native-double", "id": "child-1"})
            store.record_result("run-1", "attempt-1", result)
            count = store.connection.execute("SELECT COUNT(*) FROM wf_events").fetchone()[0]
            store.record_result("run-1", "attempt-1", result)
            store.bind_attempt("run-1", "attempt-1", {"backend": "native-double", "id": "child-1"})
            self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM wf_events").fetchone()[0], count)
            with self.assertRaises(sqlite3.IntegrityError):
                with store.transaction() as connection:
                    connection.execute("UPDATE wf_attempts SET binding_json='{}' WHERE attempt_id='attempt-1'")
            with self.assertRaises(sqlite3.IntegrityError):
                with store.transaction() as connection:
                    connection.execute("DELETE FROM wf_events")
            self.assertEqual(store.connection.execute("SELECT status FROM wf_steps").fetchone()[0], "waiting")
        store.close()
        restarted = self.store()
        with restarted.acquire_owner("run-1"):
            before = restarted.connection.execute("SELECT task_json,binding_json,result_json FROM wf_attempts").fetchone()
            restarted.record_result("run-1", "attempt-1", result)
            self.assertEqual(tuple(restarted.connection.execute("SELECT task_json,binding_json,result_json FROM wf_attempts").fetchone()), tuple(before))
            changed = result_wire(task)
            self.assert_code("RESULT_REJECTED", restarted.record_result, "run-1", "attempt-1", changed)
            self.assertEqual(restarted.connection.execute("SELECT COUNT(*) FROM wf_events WHERE event_type='result_rejected'").fetchone()[0], 1)

    def test_wrong_attempt_hash_and_phantom_results_are_retained_not_accepted(self):
        store = self.store()
        self.run_record(store)
        task = task_wire(self.project)
        with store.acquire_owner("run-1"):
            store.create_step("run-1", "step-1", "item-1", 0)
            store.create_attempt(task)
            store.bind_attempt("run-1", "attempt-1", {"id": "child"})
            for field, value in (("attempt_id", "other"), ("task_sha256", "f" * 64), ("item_id", "phantom")):
                output = result_wire(task)
                output[field] = value
                self.assert_code("RESULT_REJECTED", store.record_result, "run-1", "attempt-1", output)
            self.assert_code("UNKNOWN_ATTEMPT", store.record_result, "run-1", "never-created", result_wire(task))
            self.assertIsNone(store.connection.execute("SELECT result_json FROM wf_attempts").fetchone()[0])
            self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM wf_events WHERE event_type='result_rejected'").fetchone()[0], 4)
            store.request_cancel("run-1")
            observed = store.record_result("run-1", "attempt-1", result_wire(task))
            self.assertEqual(observed["status"], "completed")
            self.assert_code("CANCELLED", store.update_step, "run-1", "step-1", "item-1", "done")

    def test_unknown_execution_cannot_be_restarted_until_observed_settlement(self):
        store = self.store()
        self.run_record(store)
        with store.acquire_owner("run-1"):
            store.create_step("run-1", "step-1", "item-1", 0)
            store.create_attempt(task_wire(self.project))
            error = {"code": "EOF", "message": "submission unknown", "details": {}}
            store.record_attempt_error("run-1", "attempt-1", error, settled=False)
            self.assert_code("UNSETTLED_ATTEMPT", store.create_attempt, task_wire(self.project, "attempt-2"))
            store.record_attempt_error("run-1", "attempt-1", {"code": "SETTLED", "message": "proved no execution", "details": {}}, settled=True)
            replacement = store.create_attempt(task_wire(self.project, "attempt-2"))
            self.assertEqual(replacement["ordinal"], 1)
            self.assert_code("RESULT_REJECTED", store.record_result, "run-1", "attempt-1", result_wire(task_wire(self.project)))

    def test_review_reservations_are_durable_bounded_and_policy_immutable(self):
        store = self.store()
        self.run_record(store)
        with store.acquire_owner("run-1"):
            one = store.reserve_review("run-1", "claim", "vault-wiki", "B", "full_review", "logical-1", 1, 0)
            self.assertEqual(one["full_attempts"], 1)
            self.assertEqual(store.reserve_review("run-1", "claim", "vault-wiki", "B", "full_review", "logical-1", 1, 0), one)
            self.assert_code("REVIEW_EXHAUSTED", store.reserve_review, "run-1", "claim", "vault-wiki", "B", "full_review", "logical-2", 1, 0)
            self.assert_code("REVIEW_EXHAUSTED", store.reserve_review, "run-1", "claim", "vault-wiki", "B", "repair", "repair-1", 1, 0)
            self.assert_code("REVIEW_CONFLICT", store.reserve_review, "run-1", "claim", "vault-wiki", "B", "full_review", "logical-1", 4, 3)
            self.assert_code("REVIEW_CONFLICT", store.reserve_review, "run-1", "claim", "vault-wiki", "B", "full_review", "new-policy", 4, 3)
            # Independent artifact allowances are not a run-wide pool.
            for number in range(5):
                store.reserve_review("run-1", f"note-{number}", "vault-wiki", "B", "full_review", "review")
            for number in range(4):
                store.reserve_review("run-1", "same-artifact", "vault-wiki", "B", "full_review", f"full-{number}")
            for number in range(3):
                store.reserve_review("run-1", "same-artifact", "vault-wiki", "B", "repair", f"repair-{number}")
            self.assertEqual(tuple(store.connection.execute(
                "SELECT full_attempts,repair_rounds FROM wf_reviews WHERE artifact_id='same-artifact'").fetchone()), (4, 3))
            self.assert_code("REVIEW_EXHAUSTED", store.reserve_review,
                             "run-1", "same-artifact", "vault-wiki", "B", "full_review", "fifth-full")
            self.assert_code("REVIEW_EXHAUSTED", store.reserve_review,
                             "run-1", "same-artifact", "vault-wiki", "B", "repair", "fourth-repair")
            self.assert_code("INVALID_RESERVATION", store.reserve_review,
                             "run-1", "over-cap", "vault-wiki", "B", "full_review", "extra", 5, 3)
            self.assert_code("INVALID_RESERVATION", store.reserve_review,
                             "run-1", "over-cap", "vault-wiki", "B", "repair", "extra", 4, 4)
        store.close()
        resumed = self.store()
        with resumed.acquire_owner("run-1"):
            self.assertEqual(resumed.reserve_review("run-1", "claim", "vault-wiki", "B", "full_review", "logical-1", 1, 0), one)
            self.assert_code("REVIEW_EXHAUSTED", resumed.reserve_review, "run-1", "claim", "vault-wiki", "B", "full_review", "new-after-crash", 1, 0)
            self.assert_code("REVIEW_EXHAUSTED", resumed.reserve_review,
                             "run-1", "same-artifact", "vault-wiki", "B", "full_review", "after-restart-full")
            self.assert_code("REVIEW_EXHAUSTED", resumed.reserve_review,
                             "run-1", "same-artifact", "vault-wiki", "B", "repair", "after-restart-repair")

    def test_three_verification_passes_and_two_local_repairs_are_independent(self):
        store = self.store()
        self.run_record(store)
        with store.acquire_owner("run-1"):
            store.create_step("run-1", "step-1", "item-1", 0)
            for number in range(3):
                reservation = store.reserve_step_operation("run-1", "step-1", "item-1", "verify_pass", str(number), maximum=3)
                self.assertEqual(reservation["count"], number + 1)
                store.reserve_step_operation("run-1", "step-1", "item-1", "verify_pass", str(number), maximum=3)
                if number < 2:
                    store.reserve_step_operation("run-1", "step-1", "item-1", "repair_round", str(number), maximum=2)
            self.assert_code("STEP_EXHAUSTED", store.reserve_step_operation, "run-1", "step-1", "item-1", "verify_pass", "fourth", maximum=3)
            self.assert_code("STEP_EXHAUSTED", store.reserve_step_operation, "run-1", "step-1", "item-1", "repair_round", "third", maximum=2)
            counts = store.connection.execute("SELECT verify_passes,repair_rounds FROM wf_steps").fetchone()
            self.assertEqual(tuple(counts), (3, 2))

    def test_decision_inputs_and_answer_cannot_change_or_reopen_terminal(self):
        store = self.store()
        self.run_record(store)
        with store.acquire_owner("run-1"):
            question = {"key": "scope", "question": "Choose the recorded scope", "options": ["full", "partial"]}
            store.record_decision("run-1", "scope", question)
            store.record_decision("run-1", "scope", question, {"key": "scope", "answer": "partial"})
            self.assert_code("DECISION_CONFLICT", store.record_decision, "run-1", "scope", question, {"key": "scope", "answer": "full"})
            store.update_run("run-1", "done")
            self.assert_code("TERMINAL_RUN", store.update_run, "run-1", "ready")
            self.assertEqual(store.read_run("run-1")["status"], "done")

    def test_opaque_native_review_survives_restart_without_fabricating_worker_result(self):
        store = self.store()
        self.run_record(store)
        task = task_wire(self.project)
        task["duty"] = "review"
        task["semantic_sha256"] = c.semantic_hash(task)
        binding = {"backend": "native-double", "session_id": "review-session"}
        with store.acquire_owner("run-1") as epoch:
            store.create_step("run-1", "step-1", "item-1", 0)
            store.create_attempt(task)
            reply = {"request_id": "review-request", "run_id": "run-1", "attempt_id": "attempt-1",
                     "owner_epoch": epoch, "status": "completed", "error": None,
                     "payload": {"binding": binding, "locally_settled": True,
                                 "native_result": {"verdict": "fail", "exitCode": 2, "unreadable": 3}}}
            self.assert_code("REVIEW_REJECTED", store.record_native_review, "run-1", "attempt-1", reply)
            store.bind_attempt("run-1", "attempt-1", binding)
            row = store.record_native_review("run-1", "attempt-1", reply)
            self.assertEqual(row["status"], "completed")
            self.assertIsNone(row["result_json"])
            self.assertIsNotNone(row["settled_at"])
            self.assertEqual(store.connection.execute("SELECT status FROM wf_steps").fetchone()[0], "waiting")
        store.close()
        restarted = self.store()
        with restarted.acquire_owner("run-1"):
            restarted.record_native_review("run-1", "attempt-1", reply)
            changed = json.loads(json.dumps(reply))
            changed["payload"]["native_result"]["verdict"] = "approve"
            self.assert_code("REVIEW_REJECTED", restarted.record_native_review, "run-1", "attempt-1", changed)
            retained = restarted.connection.execute(
                "SELECT payload_json FROM wf_events WHERE event_type='native_review_result'").fetchone()[0]
            self.assertEqual(json.loads(retained), {"reply": reply, "locally_settled": True})
            self.assertEqual(restarted.connection.execute(
                "SELECT COUNT(*) FROM wf_events WHERE event_type='native_review_result'").fetchone()[0], 1)
            self.assert_code("RESULT_REJECTED", restarted.record_result, "run-1", "attempt-1", result_wire(task))

    def test_native_review_rejects_wrong_epoch_binding_and_unsettled_observation(self):
        store = self.store()
        self.run_record(store)
        task = task_wire(self.project)
        task["duty"] = "review"
        task["semantic_sha256"] = c.semantic_hash(task)
        binding = {"backend": "native-double", "id": "review"}
        with store.acquire_owner("run-1") as epoch:
            store.create_step("run-1", "step-1", "item-1", 0)
            store.create_attempt(task)
            store.bind_attempt("run-1", "attempt-1", binding)
            reply = {"run_id": "run-1", "attempt_id": "attempt-1", "owner_epoch": epoch,
                     "status": "completed", "payload": {"binding": binding, "settled": True}}
            invalid = [dict(reply, owner_epoch=epoch + 1), dict(reply, attempt_id="other"),
                       dict(reply, payload={"binding": binding, "settled": False}),
                       dict(reply, payload={"binding": {"id": "foreign"}, "settled": True})]
            for observation in invalid:
                self.assert_code("REVIEW_REJECTED", store.record_native_review, "run-1", "attempt-1", observation)
            row = store.connection.execute("SELECT result_json,settled_at,status FROM wf_attempts").fetchone()
            self.assertEqual(tuple(row), (None, None, "bound"))
            self.assertEqual(store.connection.execute(
                "SELECT COUNT(*) FROM wf_events WHERE event_type='native_review_rejected'").fetchone()[0], 4)


    def test_group_reservation_rolls_back_every_counter_before_any_dispatch(self):
        store = self.store()
        self.run_record(store)
        consolidated = {"artifact_id": "consolidated", "contract": "vault-raw", "artifact_tier": "B",
                        "operation_kind": "repair", "operation_id": "iteration-1"}
        exhausted = {"artifact_id": "original-note", "contract": "vault-wiki", "artifact_tier": "B",
                     "operation_kind": "repair", "operation_id": "new-repair", "max_repairs": 1}
        local = {"step_id": "step-1", "item_id": "item-1", "operation_kind": "repair_round",
                 "operation_id": "iteration-1", "maximum": 1}
        with store.acquire_owner("run-1"):
            store.create_step("run-1", "step-1", "item-1", 0)
            store.reserve_review("run-1", "original-note", "vault-wiki", "B", "repair", "prior-repair", 4, 1)
            self.assert_code("REVIEW_EXHAUSTED", store.reserve_operations,
                             "run-1", [consolidated, exhausted], [local])
            self.assertIsNone(store.connection.execute(
                "SELECT 1 FROM wf_reviews WHERE artifact_id='consolidated'").fetchone())
            self.assertEqual(store.connection.execute("SELECT repair_rounds FROM wf_steps").fetchone()[0], 0)
            self.assertEqual(store.connection.execute(
                "SELECT COUNT(*) FROM wf_events WHERE event_type='review_reserved'").fetchone()[0], 1)
            changed = dict(exhausted, artifact_id="eligible-note")
            self.assert_code("STEP_EXHAUSTED", store.reserve_operations,
                             "run-1", [consolidated, changed], [dict(local, maximum=0)])
            self.assertIsNone(store.connection.execute(
                "SELECT 1 FROM wf_reviews WHERE artifact_id='eligible-note'").fetchone())
            receipt = store.reserve_operations("run-1", [consolidated, changed], [local])
            self.assertEqual([entry["repair_rounds"] for entry in receipt["reviews"]], [1, 1])
            self.assertEqual(receipt["steps"][0]["count"], 1)
            self.assertEqual(store.reserve_operations("run-1", [consolidated, changed], [local]), receipt)
            self.assertEqual(store.connection.execute(
                "SELECT COUNT(*) FROM wf_events WHERE event_type='review_reserved'").fetchone()[0], 3)
            self.assertEqual(store.connection.execute(
                "SELECT COUNT(*) FROM wf_events WHERE event_type='step_reserved'").fetchone()[0], 1)

    def test_unattempted_claim_subjects_remain_zero_and_cannot_broaden_policy(self):
        store = self.store()
        self.run_record(store)
        subjects = [{"artifact_id": f"claim-{number}", "contract": "vault-wiki", "artifact_tier": "B",
                     "max_full_reviews": 1, "max_repairs": 0} for number in range(1, 4)]
        with store.acquire_owner("run-1"):
            store.register_review_subjects("run-1", subjects)
            store.register_review_subjects("run-1", subjects)
            self.assert_code("REVIEW_CONFLICT", store.reserve_review,
                             "run-1", "claim-1", "vault-wiki", "B", "full_review", "unsafe-default")
            for number in (1, 2):
                store.reserve_review("run-1", f"claim-{number}", "vault-wiki", "B", "full_review", "only", 1, 0)
            counts = store.connection.execute(
                "SELECT artifact_id,full_attempts,repair_rounds FROM wf_reviews ORDER BY artifact_id").fetchall()
            self.assertEqual([tuple(row) for row in counts], [("claim-1", 1, 0), ("claim-2", 1, 0), ("claim-3", 0, 0)])
            self.assert_code("REVIEW_EXHAUSTED", store.reserve_review,
                             "run-1", "claim-2", "vault-wiki", "B", "full_review", "second", 1, 0)
            self.assert_code("REVIEW_CONFLICT", store.register_review_subjects,
                             "run-1", [dict(subjects[2], max_full_reviews=4)])
            self.assertEqual(store.connection.execute(
                "SELECT COUNT(*) FROM wf_events WHERE event_type='review_subject_registered'").fetchone()[0], 3)

    def test_item_completion_and_legacy_projection_share_one_crash_boundary(self):
        store = self.store()
        self.run_record(store)
        output = self.project / "accepted.md"
        output.write_bytes(b"synthetic output")
        record = {"step_id": "step-1", "item_id": "item-1", "ordinal": 7, "status": "done",
                  "artifacts": [{"name": "output", "path": str(output), "sha256": sha256(output.read_bytes()).hexdigest(),
                                 "media_type": "text/markdown"}], "data": {"observed": "local"}}
        with store.acquire_owner("run-1"):
            store.create_step("run-1", "step-1", "item-1", 7)
            store.update_step("run-1", "step-1", "item-1", "executing", current_task="produce", session_id="observed-native-session")
            with patch.object(store, "_update_step", side_effect=RuntimeError("crash after evidence before projection")):
                with self.assertRaises(RuntimeError):
                    store.complete_step("run-1", "step-1", "item-1", record)
            self.assertEqual(store.connection.execute("SELECT status FROM wf_steps").fetchone()[0], "executing")
            self.assertEqual(store.connection.execute(
                "SELECT COUNT(*) FROM wf_events WHERE event_type='item_completed'").fetchone()[0], 0)
            store.complete_step("run-1", "step-1", "item-1", record)
            projection = store.connection.execute(
                "SELECT status,output_path,output_artifact_paths,current_task,session_id FROM pipeline_steps").fetchone()
            self.assertEqual(tuple(projection), ("done", str(output), json.dumps([str(output)], separators=(",", ":")),
                                                 "produce", "observed-native-session"))
        store.close()
        resumed = self.store()
        with resumed.acquire_owner("run-1"):
            resumed.complete_step("run-1", "step-1", "item-1", record)
            self.assert_code("COMPLETION_REJECTED", resumed.complete_step, "run-1", "step-1", "item-1",
                             dict(record, data={"observed": "differing"}))
            self.assertEqual(resumed.connection.execute(
                "SELECT COUNT(*) FROM wf_events WHERE event_type='item_completed'").fetchone()[0], 1)

    def test_completion_requires_exact_current_attempt_and_completed_execution(self):
        store = self.store()
        self.run_record(store)
        record = {"step_id": "step-1", "item_id": "item-1", "ordinal": 0,
                  "status": "done", "artifacts": [], "data": {"attempt_id": "attempt-1"}}
        task = task_wire(self.project)
        with store.acquire_owner("run-1"):
            store.create_step("run-1", "step-1", "item-1", 0)
            store.append_event("run-1", None, "native_admission_committed", {})
            store.append_event("run-1", None, "native_admission_context", {})
            self.assert_code("COMPLETION_REJECTED", store.complete_step, "run-1", "step-1", "item-1", record)
            store.create_attempt(task)
            self.assert_code("COMPLETION_REJECTED", store.complete_step, "run-1", "step-1", "item-1", dict(record, data={}))
            self.assert_code("COMPLETION_REJECTED", store.complete_step, "run-1", "step-1", "item-1",
                             dict(record, data={"attempt_id": "other"}))
            self.assert_code("UNSETTLED_ATTEMPT", store.complete_step, "run-1", "step-1", "item-1", record)
            store.bind_attempt("run-1", "attempt-1", {"id": "first-child"})
            store.record_attempt_error("run-1", "attempt-1",
                                       {"code": "FAILED", "message": "observed exit", "details": {}}, settled=True)
            self.assert_code("COMPLETION_REJECTED", store.complete_step, "run-1", "step-1", "item-1", record)
            replacement = task_wire(self.project, "attempt-2")
            store.create_attempt(replacement)
            self.assert_code("COMPLETION_REJECTED", store.complete_step, "run-1", "step-1", "item-1", record)
            store.bind_attempt("run-1", "attempt-2", {"id": "second-child"})
            store.record_result("run-1", "attempt-2", result_wire(replacement))
            current = dict(record, data={"attempt_id": "attempt-2"})
            store.complete_step("run-1", "step-1", "item-1", current)
            store.complete_step("run-1", "step-1", "item-1", current)
            completed = store.connection.execute(
                "SELECT attempt_id,payload_json FROM wf_events WHERE event_type='item_completed'").fetchall()
            self.assertEqual([(row[0], json.loads(row[1])) for row in completed], [("attempt-2", current)])
            self.assertEqual(store.connection.execute("SELECT status FROM wf_steps").fetchone()[0], "done")

    def test_report_failure_completes_only_diagnostics_and_allows_failed_audit(self):
        store = self.store()
        self.run_record(store)
        with store.acquire_owner("run-1"):
            record, _ = self.report_failure(store, "run-1")
            attempt_id = record["data"]["attempt_id"]
            wrong = result_wire(task_wire(self.project, attempt_id))
            wrong["attempt_id"] = "foreign-attempt"
            self.assert_code("RESULT_REJECTED", store.record_result, "run-1", attempt_id, wrong)
            before = dict(store.connection.execute("SELECT * FROM wf_attempts").fetchone())
            store.complete_step("run-1", "step-1", "item-1", record)
            store.complete_step("run-1", "step-1", "item-1", record)
            self.assertEqual(dict(store.connection.execute("SELECT * FROM wf_attempts").fetchone()), before)
            self.assertEqual(before["status"], "failed")
            self.assertIsNotNone(before["settled_at"])
            self.assertIsNone(before["result_json"])
            self.assertEqual(json.loads(before["error_json"]), record["data"]["error"])
            completed = store.connection.execute(
                "SELECT attempt_id,payload_json FROM wf_events WHERE event_type='item_completed'").fetchall()
            self.assertEqual([(row[0], json.loads(row[1])) for row in completed], [(attempt_id, record)])
            rejected = store.connection.execute(
                "SELECT payload_json FROM wf_events WHERE event_type='result_rejected'").fetchone()
            self.assertEqual(json.loads(rejected[0])["result"], wrong)
            self.assertEqual(store.connection.execute(
                "SELECT COUNT(*) FROM wf_events WHERE event_type='attempt_settled'").fetchone()[0], 0)
            projection = store.connection.execute(
                "SELECT status,output_path,output_artifact_paths FROM pipeline_steps").fetchone()
            self.assertEqual(tuple(projection), ("done", None, "[]"))
            store.create_step("run-1", "audit", "item-1", 1, dependencies=("step-1",))
            audit = {"step_id": "audit", "item_id": "item-1", "ordinal": 1, "status": "done", "artifacts": [],
                     "data": {"audit_emitted": True, "run_status": "failed"}}
            store.complete_step("run-1", "audit", "item-1", audit)
            store.update_run("run-1", "failed")
            self.assertEqual(store.read_run("run-1")["status"], "failed")
            self.assertEqual(store.connection.execute("SELECT status FROM pipelines").fetchone()[0], "failed")

    def test_report_failure_requires_exact_policy_and_failure_only_record(self):
        store = self.store()
        cases = [
            ("missing-policy", {}, {}, {}), ("false-policy", {"allow_task_failure": False}, {}, {}),
            ("integer-policy", {"allow_task_failure": 1}, {}, {}),
            ("string-policy", {"allow_task_failure": "true"}, {}, {}),
            ("skipped", None, {"status": "skipped"}, {}),
            ("wrong-step", None, {"step_id": "other"}, {}),
            ("wrong-item", None, {"item_id": "other"}, {}),
            ("wrong-ordinal", None, {"ordinal": 1}, {}),
            ("artifacts", None, {"artifacts": [{"name": "invented", "path": str(self.project / "not-accepted.json"),
                                                "sha256": "a" * 64, "media_type": "application/json"}]}, {}),
            ("false-failure", None, {}, {"tooling_failure": False}),
            ("integer-failure", None, {}, {"tooling_failure": 1}),
            ("successful-run", None, {}, {"run_status": "done"}),
            ("different-error", None, {}, {"error": {"code": "OTHER", "message": "not retained", "details": {}}}),
            ("wrong-attempt", None, {}, {"attempt_id": "foreign-attempt"}),
            ("missing-attempt", None, {}, {"attempt_id": None}),
            ("accepted-result", None, {}, {"result": {"status": "completed"}}),
            ("native-carrier", None, {}, {"native_result": {"status": "valid", "data": {"status": "completed"}}}),
            ("native-verdict", None, {}, {"verification": "PASS"}),
            ("native-outputs", None, {}, {"outputs": {}}),
            ("native-review", None, {}, {"native_review": {"status": "reviewed"}}),
            ("review-gate", None, {}, {"review_gate": {"accepted": True}}),
        ]
        for name, policy, changes, data in cases:
            with self.subTest(case=name):
                self.run_record(store, name)
                with store.acquire_owner(name):
                    record, _ = self.report_failure(store, name, policy=policy)
                    invalid = {**record, **changes, "data": {**record["data"], **data}}
                    self.assert_code("COMPLETION_REJECTED", store.complete_step, name, "step-1", "item-1", invalid)
                    self.assertEqual(store.connection.execute(
                        "SELECT status FROM wf_steps WHERE run_id=?", (name,)).fetchone()[0], "executing")
                    self.assertEqual(store.connection.execute(
                        "SELECT COUNT(*) FROM wf_events WHERE run_id=? AND event_type='item_completed'", (name,)).fetchone()[0], 0)

    def test_report_failure_requires_matching_ordered_attempt_evidence_and_graceful_teardown(self):
        store = self.store()
        cases = ("missing-events", "missing-report", "missing-teardown", "different-attempt-error",
                 "unsettled-error", "different-report-error", "wrong-attempt-evidence", "reversed-order",
                 "malformed-receipt", "false-graceful", "integer-graceful", "unsettled-process",
                 "nonzero-exit", "boolean-exit", "forced-shutdown", "protocol-error", "later-bad-receipt")
        for name in cases:
            with self.subTest(case=name):
                self.run_record(store, name)
                with store.acquire_owner(name):
                    record, receipt = self.report_failure(store, name, events=False)
                    attempt_id, error = record["data"]["attempt_id"], record["data"]["error"]
                    other_error = {"code": "OTHER", "message": "not the retained error", "details": {}}
                    if name in ("different-attempt-error", "unsettled-error"):
                        store.append_event(name, attempt_id, "attempt_error",
                                           {"error": other_error if name == "different-attempt-error" else error,
                                            "settled": name != "unsettled-error"})
                    if name == "wrong-attempt-evidence":
                        store.create_step(name, "step-1", "item-2", 1)
                        other = task_wire(self.project, name + "-other", run_id=name, item_id="item-2")
                        store.create_attempt(other)
                        attempt_id = other["attempt_id"]
                    changes = {"false-graceful": {"graceful_eof_exit": False},
                               "integer-graceful": {"graceful_eof_exit": 1},
                               "unsettled-process": {"process_settled": False}, "nonzero-exit": {"returncode": 1},
                               "boolean-exit": {"returncode": False},
                               "forced-shutdown": {"shutdown_actions": ["stdin_eof_sent", "terminate_requested"]},
                               "protocol-error": {"protocol_errors": [{"code": "IPC_CORRELATION"}]}}
                    receipt = None if name == "malformed-receipt" else {**receipt, **changes.get(name, {})}
                    report = {"error": other_error if name == "different-report-error" else error}
                    if name == "reversed-order":
                        store.append_event(name, attempt_id, "report_task_failure", report)
                    if name not in ("missing-events", "missing-teardown"):
                        store.append_event(name, attempt_id, "adapter_process_settled", receipt)
                    if name not in ("missing-events", "missing-report", "reversed-order"):
                        store.append_event(name, attempt_id, "report_task_failure", report)
                    if name == "later-bad-receipt":
                        store.append_event(name, attempt_id, "adapter_process_settled", dict(receipt, graceful_eof_exit=False))
                    self.assert_code("COMPLETION_REJECTED", store.complete_step, name, "step-1", "item-1", record)
                    self.assertEqual(store.connection.execute(
                        "SELECT COUNT(*) FROM wf_events WHERE run_id=? AND event_type='item_completed'", (name,)).fetchone()[0], 0)

    def test_report_failure_cannot_bypass_unsettled_review_cancelled_terminal_or_superseded_attempt(self):
        store = self.store()
        for name in ("unsettled", "review", "cancelled", "terminal-run", "terminal-step", "superseded"):
            with self.subTest(case=name):
                self.run_record(store, name)
                with store.acquire_owner(name):
                    record, _ = self.report_failure(store, name, settled=name != "unsettled",
                                                    duty="review" if name == "review" else "verify")
                    if name == "cancelled":
                        store.request_cancel(name)
                    elif name == "terminal-run":
                        store.update_run(name, "failed")
                    elif name == "terminal-step":
                        store.update_step(name, "step-1", "item-1", "failed")
                    elif name == "superseded":
                        store.create_attempt(task_wire(self.project, name + "-replacement", run_id=name))
                    self.assert_code("UNSETTLED_ATTEMPT" if name == "unsettled" else "COMPLETION_REJECTED",
                                     store.complete_step, name, "step-1", "item-1", record)
                    if name == "superseded":
                        current = dict(record, data={**record["data"], "attempt_id": name + "-replacement"})
                        self.assert_code("UNSETTLED_ATTEMPT", store.complete_step, name, "step-1", "item-1", current)
                    self.assertEqual(store.connection.execute(
                        "SELECT COUNT(*) FROM wf_events WHERE run_id=? AND event_type='item_completed'", (name,)).fetchone()[0], 0)

    def test_report_failure_cannot_relabel_an_accepted_task_result_as_diagnostic_completion(self):
        store = self.store()
        self.run_record(store)
        task = task_wire(self.project)
        error = {"code": "TASK_FAILED", "message": "Typed worker failure", "details": {}}
        failed = result_wire(task)
        failed.update(status="failed", verification=None, error=error)
        record = {"step_id": "step-1", "item_id": "item-1", "ordinal": 0, "status": "done", "artifacts": [],
                  "data": {"attempt_id": "attempt-1", "tooling_failure": True, "run_status": "failed", "error": error}}
        with store.acquire_owner("run-1"):
            store.create_step("run-1", "step-1", "item-1", 0, policy={"allow_task_failure": True})
            store.create_attempt(task)
            store.bind_attempt("run-1", "attempt-1", {"id": "child"})
            store.record_result("run-1", "attempt-1", failed)
            store.append_event("run-1", "attempt-1", "attempt_error", {"error": error, "settled": True})
            store.append_event("run-1", "attempt-1", "adapter_process_settled",
                               {"graceful_eof_exit": True, "process_settled": True, "returncode": 0,
                                "shutdown_actions": ["stdin_eof_sent"]})
            store.append_event("run-1", "attempt-1", "report_task_failure", {"error": error})
            self.assert_code("COMPLETION_REJECTED", store.complete_step, "run-1", "step-1", "item-1", record)
            self.assertEqual(json.loads(store.connection.execute("SELECT result_json FROM wf_attempts").fetchone()[0]), failed)
            self.assertEqual(store.connection.execute(
                "SELECT COUNT(*) FROM wf_events WHERE event_type='item_completed'").fetchone()[0], 0)

    def test_report_failure_cannot_trust_failed_row_without_retained_attempt_error(self):
        store = self.store()
        self.run_record(store)
        error = {"code": "FAILED", "message": "Row alone is not retained error evidence", "details": {}}
        record = {"step_id": "step-1", "item_id": "item-1", "ordinal": 0, "status": "done", "artifacts": [],
                  "data": {"attempt_id": "attempt-1", "tooling_failure": True, "run_status": "failed", "error": error}}
        with store.acquire_owner("run-1"):
            store.create_step("run-1", "step-1", "item-1", 0, policy={"allow_task_failure": True})
            store.create_attempt(task_wire(self.project))
            store.bind_attempt("run-1", "attempt-1", {"id": "child"})
            # An incomplete ledger must not borrow the row's status as its missing observation.
            with store.transaction() as connection:
                connection.execute("UPDATE wf_attempts SET status='failed',error_json=?,settled_at=created_at WHERE attempt_id='attempt-1'",
                                   (json.dumps(error, sort_keys=True, separators=(",", ":")),))
            store.append_event("run-1", "attempt-1", "adapter_process_settled",
                               {"graceful_eof_exit": True, "process_settled": True, "returncode": 0,
                                "shutdown_actions": ["stdin_eof_sent"]})
            store.append_event("run-1", "attempt-1", "report_task_failure", {"error": error})
            self.assert_code("COMPLETION_REJECTED", store.complete_step, "run-1", "step-1", "item-1", record)
            self.assertEqual(store.connection.execute(
                "SELECT COUNT(*) FROM wf_events WHERE event_type='item_completed'").fetchone()[0], 0)

    def test_private_engineering_events_reject_mutations_and_terminal_idempotence(self):
        store = self.store()
        events = (
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
        for number, event_type in enumerate(events):
            with self.subTest(event_type=event_type):
                run_id, attempt_id = f"run-{number}", f"attempt-{number}"
                self.run_record(store, run_id)
                task = task_wire(self.project, attempt_id, run_id=run_id)
                result = result_wire(task)
                record = {"step_id": "step-1", "item_id": "item-1", "ordinal": 0,
                          "status": "done", "artifacts": [], "data": {"attempt_id": attempt_id}}
                binding = {"id": f"child-{number}"}
                with store.acquire_owner(run_id):
                    store.create_step(run_id, "step-1", "item-1", 0)
                    store.create_attempt(task)
                    store.bind_attempt(run_id, attempt_id, binding)
                    store.record_result(run_id, attempt_id, result)
                    store.reserve_review(run_id, "artifact-1", "vault-wiki", "B", "full_review", "only")
                    store.complete_step(run_id, "step-1", "item-1", record)
                    store.update_run(run_id, "done")
                    store.append_event(run_id, None, event_type, {})
                    before = store.read_run(run_id)
                    count = store.connection.execute("SELECT COUNT(*) FROM wf_events WHERE run_id=?", (run_id,)).fetchone()[0]
                    self.assert_code("SOURCE_RECOVERY_CONFLICT", store._assert_public_ledger, store.connection, run_id)
                    self.assert_code("SOURCE_RECOVERY_CONFLICT", self.run_record, store, run_id)
                    self.assert_code("SOURCE_RECOVERY_CONFLICT", store.create_attempt, task)
                    self.assert_code("SOURCE_RECOVERY_CONFLICT", store.bind_attempt, run_id, attempt_id, binding)
                    self.assert_code("SOURCE_RECOVERY_CONFLICT", store.record_result, run_id, attempt_id, result)
                    self.assert_code("SOURCE_RECOVERY_CONFLICT", store.complete_step, run_id, "step-1", "item-1", record)
                    self.assert_code("SOURCE_RECOVERY_CONFLICT", store.reserve_review,
                                     run_id, "artifact-1", "vault-wiki", "B", "full_review", "only")
                    self.assert_code("SOURCE_RECOVERY_CONFLICT", store.update_run, run_id, "done")
                    self.assert_code("SOURCE_RECOVERY_CONFLICT", store.request_cancel, run_id)
                    self.assert_code("SOURCE_RECOVERY_CONFLICT", store.append_event, run_id, None, "normal", {})
                    self.assertEqual(store.read_run(run_id), before)
                    self.assertEqual(store.connection.execute(
                        "SELECT COUNT(*) FROM wf_events WHERE run_id=?", (run_id,)).fetchone()[0], count)
                    self.assertEqual(tuple(store.connection.execute(
                        "SELECT full_attempts,repair_rounds FROM wf_reviews WHERE run_id=?", (run_id,)).fetchone()), (1, 0))
                with self.assertRaises(StoreError) as caught:
                    with store.acquire_owner(run_id):
                        self.fail("Private engineering ledgers must not acquire a new ownership epoch")
                self.assertEqual(caught.exception.code, "SOURCE_RECOVERY_CONFLICT")
                self.assertEqual(store.read_run(run_id), before)

    def test_quarantine_cannot_settle_unknown_execution_or_admit_a_successor(self):
        store = self.store()
        self.run_record(store)
        with store.acquire_owner("run-1"):
            store.create_step("run-1", "step-1", "item-1", 0)
            store.create_attempt(task_wire(self.project))
            error = {"code": "EOF", "message": "submission unknown", "details": {}}
            before = store.record_attempt_error("run-1", "attempt-1", error, settled=False)
            store.append_event("run-1", "attempt-1", "operation_quarantined", {})
            self.assert_code("SOURCE_RECOVERY_CONFLICT", store.create_attempt, task_wire(self.project, "attempt-2"))
            self.assert_code("SOURCE_RECOVERY_CONFLICT", store.record_attempt_error,
                             "run-1", "attempt-1", error, settled=True)
            observed = dict(store.connection.execute("SELECT * FROM wf_attempts").fetchone())
            self.assertEqual({key: observed[key] for key in before}, before)
            self.assertIsNone(observed["settled_at"])
            self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM wf_attempts").fetchone()[0], 1)
            self.assertEqual(store.connection.execute("SELECT current_attempt_id FROM wf_steps").fetchone()[0], "attempt-1")


if __name__ == "__main__":
    unittest.main()
