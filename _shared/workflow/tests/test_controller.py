"""Generic control/state/protocol regressions. Adapter doubles are NOT native proof."""
from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
import inspect
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from _shared.workflow.src import contracts as c
from _shared.workflow.src import workflow as native_workflow
from _shared.workflow.src.controller import (Controller, ControllerError, NativeIPC, NativeLaunchUnavailable, PreparedStep,
                                            RunRequest, StepSpec, WorkflowSpec, _Active, json_value, review_gate)
from _shared.workflow.src.publication import PublicationManager, PublicationError
from _shared.workflow.src.store import Store

REAL_PREFLIGHT = native_workflow.preflight_native

ENGINEERING_EVENTS = (
    "source_recovery_admitted", "negative_recovery_admitted", "owned_input_evolution_admitted",
    "owned_input_source_revised", "settled_owned_input_evolution_admitted", "local_runtime_revision_admitted",
    "unprepared_local_source_revision_admitted", "operation_quarantined", "semantic_verifier_continuation_reserved",
    "completed_scope_confirmation_source_admitted", "completed_scope_preservation_confirmed",
    "pre_native_snapshots_recovered", "source_attempt_superseded", "source_completion_invalidated",
    "native_admission_succeeded", "quarantine_successor_admitted", "staged_continuation_replaced",
    "bounded_repair_reserved", "bounded_candidate_repair_reserved",
)


def request(project, arguments=()):
    return RunRequest(1, "synthetic", project, arguments, "omp", "gpt", "auto", None)


def local(context, data=None, artifacts=()):
    return PreparedStep("local_result", local_result={"artifacts": list(artifacts), "data": data or {}})


def task(context, duty="produce"):
    wire = {"schema_version": 1, "run_id": context.run_id, "step_id": context.step_id,
            "item_id": context.item_id, "attempt_id": context.attempt_id, "duty": duty,
            "instructions": "Process exactly this synthetic subject and return actual hash-bound output.",
            "inputs": [], "resources": [], "candidate_dir": str(context.candidate_dir),
            "outputs": [{"name": "record", "relative_path": "record.json", "media_type": "application/json", "required": True}],
            "allowed_actions": ["read", "write"], "read_roots": [], "write_roots": [str(context.candidate_dir)],
            "result_schema": c.task_result_schema(), "artifact_id": "synthetic:" + context.item_id,
            "review_contract": "synthetic", "artifact_tier": "B"}
    wire["semantic_sha256"] = c.semantic_hash(wire)
    return c.task_from_dict(wire)


def review_export(home):
    """Materialize only the canonical extension and positive owned export members."""
    from _shared.workflow.src import workflow
    entries = json.loads((ROOT / "deploy/exports.json").read_text(encoding="utf-8"))["entries"]
    extension = Path(".omp/shared/extensions/discriminator/index.ts")
    members = {Path(".agents/skills/discriminator") / name for name in workflow._DISCRIMINATOR_RUNTIME_FILES}
    expected = {extension, *members}
    selected = {Path(entry["destination"]).relative_to("harness"): entry for entry in entries
                if Path(entry["destination"]).is_relative_to("harness")
                and Path(entry["destination"]).relative_to("harness") in expected}
    if set(selected) != expected or len(members) != 27:
        raise AssertionError("Exact positive discriminator runtime export required")
    for relative, entry in selected.items():
        destination = home / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((ROOT / entry["source"]).read_bytes())
    return home / extension


def static_native_context(test, base, req, *, config_files=(), skills_root=None):
    """Real receipt-free static admission; inert executable files are never launched."""
    from _shared.workflow.src import workflow
    skills = Path(skills_root) if skills_root is not None else base / ".agents" / "skills"
    home = skills.parent.parent
    profile = patch.object(workflow, "_user_profile", return_value=home)
    profile.start()
    test.addCleanup(profile.stop)
    review_export(home)
    config_root = base / ".agents" / "agent-skills"
    config_root.mkdir(parents=True)
    install = base / "native"
    (install / "package").mkdir(parents=True)
    for name in ("bun.exe", "omp.exe", "omp.js"):
        (install / name).write_bytes(("nonexecutable static fixture; never launched: " + name).encode())
    (install / "package" / "package.json").write_text(json.dumps({"name": "@oh-my-pi/pi-coding-agent"}), encoding="utf-8")
    declaration, manifest = config_root / "runtime.json", config_root / "omp-install.json"
    settings = {"model": "openai/synthetic"}
    if config_files:
        settings["config_files"] = [str(path) for path in config_files]
    declaration.write_text(json.dumps({"machine": workflow.platform.node(),
        "tokens": {"__USERPROFILE__": str(home), "__OMP_PYTHON__": str(Path(sys.executable).resolve())},
        "omp_managed": {"enabled": True, "install_root": str(install)}, "workflow_native": {"omp": settings}}), encoding="utf-8")
    manifest.write_text(json.dumps({"schema": 1, "package": "@oh-my-pi/pi-coding-agent", "package_tree_relative": "package",
        "executable_relative": "omp.exe", "payload_relative": "omp.js", "installer": {"runtime_relative": "bun.exe"}}), encoding="utf-8")
    for name in ("controller.py", "workflow.py", "contracts.py", "store.py", "publication.py", "harness/omp/adapter.ts"):
        origin = ROOT / "_shared" / "workflow" / (name if name.startswith("harness/") else "src/" + name)
        target = skills / "_workflow" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(origin.read_bytes())
    configuration = workflow.resolve_native_configuration(req, skills, config_root=config_root)
    admission = REAL_PREFLIGHT(req, skills, native_configuration=configuration)
    configuration = admission["configuration"]
    expected_review = {str(home / ".omp/shared/extensions/discriminator/index.ts"),
                       *(str(home / ".agents/skills/discriminator" / name)
                         for name in workflow._DISCRIMINATOR_RUNTIME_FILES), str(Path(sys.executable).resolve())}
    test.assertEqual(configuration["omp"]["context"]["review_extension"],
                     workflow._input_ref(home / ".omp/shared/extensions/discriminator/index.ts"))
    test.assertTrue(expected_review <= {item["path"] for item in configuration["omp"]["source_refs"]})
    # Source-level controller fixtures keep their authoring source-map root.
    # Redirect only layout selection, never the genuine admission validator.
    # This explicit account observer/transport double is not installed-account proof.
    routing = patch.object(workflow, "preflight_native", side_effect=lambda selected, source_root, **kwargs:
        REAL_PREFLIGHT(selected, skills if Path(source_root) == ROOT else source_root, **kwargs))
    routing.start()
    test.addCleanup(routing.stop)
    return SimpleNamespace(skills_root=skills, configuration=configuration, admission=admission, declaration=declaration)


class ProtocolDouble:
    """Deterministic IPC peer; proves controller behavior, never provider behavior."""
    def __init__(self, owner, context, task, backend, owner_epoch, recovery=False):
        self.owner, self.context, self.task, self.backend = owner, context, task, backend
        self.epoch, self.recovery = owner_epoch, recovery
        self.queue, self.requests, self.operations = [], {}, []
        self.binding = {"backend": backend, "child_id": task.attempt_id, "job_id": "job-" + task.attempt_id}
        self.cancelled, self.closed = False, False
        self.thread = threading.get_ident()
        owner.instances.append(self)

    def frame(self, request_id, status, payload, error=None):
        return {"request_id": request_id, "run_id": self.context.run_id, "attempt_id": self.task.attempt_id,
                "owner_epoch": self.epoch, "status": status, "payload": payload, "error": error}

    def send(self, operation, payload, *, request_id=None):
        self.operations.append(operation)
        request_id = request_id or str(len(self.requests))
        self.requests[request_id] = operation
        if operation == "prepare":
            self.queue.append(self.frame(request_id, "prepared", {}))
        elif operation == "launch_with_binding_barrier":
            self.queue.append(self.frame(request_id, "binding_required", {"binding": self.binding}))
        elif operation == "binding_ack":
            # ACK must observe a COMMITTED real SQLite binding on owning thread.
            self.owner.test.assertEqual(threading.get_ident(), self.thread)
            connection = sqlite3.connect(self.context.request.project_root / "state.db")
            bound, epoch = connection.execute("SELECT a.binding_json,r.owner_epoch FROM wf_attempts a JOIN wf_runs r USING(run_id) WHERE a.attempt_id=?", (self.task.attempt_id,)).fetchone()
            connection.close()
            self.owner.test.assertEqual(json.loads(bound), self.binding)
            self.owner.test.assertEqual(epoch, self.epoch)
            self.owner.test.assertEqual(payload, {"committed": True, "binding": self.binding})
            if self.owner.channel_failure and not self.recovery:
                self.queue.append(ControllerError("NATIVE_CHANNEL_LOST", "Synthetic EOF after durable binding"))
            elif self.owner.cancel_on_ack:
                cancellation = Store(self.context.request.project_root)
                cancellation.initialize()
                cancellation.request_cancel(self.context.run_id)
                cancellation.close()
                self.queue.append(self.terminal(request_id))
            else:
                self.queue.append(self.terminal(request_id))
        elif operation == "observe":
            self.queue.append(self.terminal(request_id))
        elif operation == "cancel":
            self.cancelled = True
            self.queue.append(self.frame(request_id, "cancelled", {"locally_settled": True, "binding": self.binding}))
        elif operation == "reconcile":
            self.owner.reconciliations += 1
            self.queue.append(self.frame(request_id, "unknown", {"locally_settled": False, "binding": self.binding}))
        return request_id

    def terminal(self, request_id):
        if self.context.step.kind == "review":
            path = self.context.candidate_dir / "unit-review-result.json"
            receipt = self.context.candidate_dir / "unit-review-receipt.json"
            path.write_text('{"unit_double_only":true}', encoding="utf-8")
            value = {"status": self.owner.review_status, "verdict": self.owner.review_verdict,
                     "exitCode": self.owner.review_code, "loop_should_continue": False,
                     "caller_family": self.context.request.caller_family,
                     "result_path": str(path), "receipt_path": str(receipt)}
            receipt.write_text(json.dumps({"unit_double_only": True, "local_settled": True, "publication_allowed": True,
                               "binding": {"caller_family": self.context.request.caller_family, "artifact_class": self.task.review_contract,
                                           "source_sha256": self.task.inputs[0].sha256}, "compact": value,
                               "result": {"path": str(path), "sha256": sha256(path.read_bytes()).hexdigest()}}), encoding="utf-8")
            return self.frame(request_id, "completed", {"locally_settled": True, "binding": self.binding, "native_result": value})
        output = self.task.outputs[0]
        path = self.context.candidate_dir / output.relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if output.media_type == "text/markdown":
            path.write_bytes("---\ntitle: Résumé α\nstatus: draft\nlast_verified: null\nother: keep\n---\nBody: 10 only after Q.\n".encode("utf-8"))
        else:
            path.write_text(json.dumps({"subject": self.task.item_id, "duty": self.task.duty}), encoding="utf-8")
        verification = "FAIL" if self.task.duty == "verify" and self.owner.fail_verifier else "PASS"
        value = {"schema_version": 1, "run_id": self.task.run_id, "step_id": self.task.step_id,
                 "item_id": self.task.item_id, "attempt_id": "phantom" if self.owner.wrong_identity else self.task.attempt_id,
                 "status": "completed", "verification": verification,
                 "artifacts": [{"name": output.name, "relative_path": output.relative_path, "sha256": sha256(path.read_bytes()).hexdigest(), "media_type": output.media_type}],
                 "task_sha256": self.task.semantic_sha256, "evidence": [], "error": None, "decision": None}
        payload = {"locally_settled": True, "binding": self.binding}
        if self.backend == "omp":
            payload.update(native_result={"source": "caller", "mode": "strict", "status": "valid", "data": value},
                           native_settlement={"status": "completed", "end_time": 1})
        else:
            payload["result"] = value
        return self.frame(request_id, "completed", payload)

    def poll(self, timeout=0):
        if not self.queue:
            return None
        value = self.queue.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def close(self):
        self.closed = True
        return {"returncode": 0, "shutdown_actions": ["stdin_eof_sent"], "process_settled": True,
                "graceful_eof_exit": True, "unit_double_only": True}


class Doubles:
    def __init__(self, test, **options):
        self.test, self.instances, self.reconciliations = test, [], 0
        self.fail_verifier = self.wrong_identity = self.channel_failure = self.cancel_on_ack = False
        self.review_verdict, self.review_code = "approve", 0
        self.review_status = "reviewed"
        for name, value in options.items():
            setattr(self, name, value)

    def __call__(self, **kwargs):
        return ProtocolDouble(self, **kwargs)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()

    def events(self, run_id, name):
        connection = sqlite3.connect(self.project / "state.db")
        result = [json.loads(r[0]) for r in connection.execute("SELECT payload_json FROM wf_events WHERE run_id=? AND event_type=? ORDER BY event_id", (run_id, name))]
        connection.close()
        return result

    def test_controller_exposes_only_ordinary_public_api(self):
        parameters = inspect.signature(Controller).parameters
        self.assertEqual(tuple(parameters), ("workflow_factory", "artifact_validator", "adapter_factory",
                                             "skills_root", "workflow_restorer", "native_configuration"))
        self.assertEqual(parameters["workflow_factory"].kind, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        for name in tuple(parameters)[1:]:
            self.assertEqual(parameters[name].kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertEqual({name for name, value in inspect.getmembers(Controller, inspect.isfunction)
                          if not name.startswith("_")}, {"run", "status", "cancel", "resume"})

    def test_engineering_ledgers_refuse_status_and_resume_before_terminal_return_or_progress(self):
        for event_type in ENGINEERING_EVENTS:
            for status in ("done", "failed", "cancelled", "preview", "blocked", "executing"):
                with self.subTest(event_type=event_type, status=status):
                    run_id = event_type + "-" + status
                    store = Store(self.project)
                    store.initialize()
                    try:
                        store.create_run(run_id, "synthetic", "a" * 64, json_value(request(self.project)), [])
                        with store.acquire_owner(run_id):
                            store.create_step(run_id, "pending", "main", 0)
                            store.record_decision(run_id, "continue", {"key": "continue", "question": "Continue?", "options": ["yes"]})
                            store.update_run(run_id, status)
                            store.append_event(run_id, None, event_type, {"synthetic": True})
                    finally:
                        store.close()
                    controller = Controller(lambda req: self.fail("Rejected ledgers cannot restore or prepare a workflow"),
                                            adapter_factory=lambda **kwargs: self.fail("Rejected ledgers cannot dispatch"))
                    connection = sqlite3.connect(self.project / "state.db")
                    try:
                        history = connection.execute("SELECT * FROM wf_events WHERE run_id=? ORDER BY event_id", (run_id,)).fetchall()
                        observed = controller.status(self.project, run_id)
                        self.assertEqual((observed.status, observed.error["code"]), ("blocked", "SOURCE_RECOVERY_CONFLICT"), observed.error)
                        self.assertEqual(connection.execute("SELECT * FROM wf_events WHERE run_id=? ORDER BY event_id", (run_id,)).fetchall(), history)
                    finally:
                        connection.close()
                    with patch.object(native_workflow, "preflight_native", side_effect=AssertionError("Rejected ledgers cannot admit native work")):
                        outcome = controller.resume(self.project, run_id, {"key": "continue", "answer": "yes"})
                    self.assertEqual((outcome.status, outcome.error["code"]), ("blocked", "SOURCE_RECOVERY_CONFLICT"), outcome.error)
                    connection = sqlite3.connect(self.project / "state.db")
                    try:
                        self.assertEqual(connection.execute("SELECT status,answer_json FROM wf_decisions WHERE run_id=?", (run_id,)).fetchone(),
                                         ("waiting", None))
                        self.assertEqual(connection.execute("SELECT status,current_attempt_id FROM wf_steps WHERE run_id=?", (run_id,)).fetchone(),
                                         ("waiting", None))
                        for table in ("wf_attempts", "wf_reviews", "wf_publications"):
                            self.assertEqual(connection.execute("SELECT COUNT(*) FROM " + table + " WHERE run_id=?", (run_id,)).fetchone()[0], 0)
                    finally:
                        connection.close()
                    for name in ("item_completed", "review_reserved", "step_reserved", "native_dispatch_intent", "publication_intent"):
                        self.assertEqual(self.events(run_id, name), [])

    def test_engineering_ledger_refuses_initial_execution_before_step_preparation(self):
        append = Store.append_event
        for event_type in ENGINEERING_EVENTS:
            with self.subTest(event_type=event_type):
                def inject(store, run_id, attempt_id, name, payload):
                    result = append(store, run_id, attempt_id, name, payload)
                    if name == "snapshots_committed":
                        append(store, run_id, None, event_type, {"synthetic": True})
                    return result
                def forbidden(context):
                    self.fail("Rejected ledgers cannot prepare a step")
                spec = WorkflowSpec("synthetic", (StepSpec("pending", (), "local", forbidden),))
                with patch.object(Store, "append_event", inject):
                    outcome = Controller(lambda req: spec).run(request(self.project))
                self.assertEqual((outcome.status, outcome.error["code"]), ("blocked", "SOURCE_RECOVERY_CONFLICT"), outcome.error)
                self.assertEqual(self.events(outcome.run_id, "item_completed"), [])
                self.assertEqual(self.events(outcome.run_id, "local_result_prepared"), [])

    def graph(self, *, decision=False):
        def inventory(ctx):
            return local(ctx, {"items": [{"item_id": str(n), "value": n} for n in range(3)]})
        def produce(ctx):
            return PreparedStep("ready", (task(ctx),))
        def aggregate(ctx):
            rows = ctx.data["step_data"]["produce"]
            self.assertEqual([row["item_id"] for row in sorted(rows, key=lambda r: r["ordinal"])], ["0", "1", "2"])
            self.assertEqual({a["item_id"] for a in ctx.predecessors}, {"0", "1", "2"})
            path = ctx.candidate_dir / "all.json"
            path.write_text(json.dumps(json_value([row["data"]["outputs"]["record"] for row in sorted(rows, key=lambda r: r["ordinal"])])), encoding="utf-8")
            return local(ctx, artifacts=[{"name": "all", "path": str(path), "sha256": sha256(path.read_bytes()).hexdigest(), "media_type": "application/json"}])
        def choose(ctx):
            if "continue" not in ctx.data["decisions"]:
                return PreparedStep("blocked", decision={"key": "continue", "question": "Continue synthetic aggregation?", "options": ["yes", "no"]})
            return local(ctx, {"answer": ctx.data["decisions"]["continue"]})
        steps = [StepSpec("inventory", (), "local", inventory),
                 StepSpec("produce", ("inventory",), "task", produce, "inventory.items", 3),
                 StepSpec("aggregate", ("produce",), "local", aggregate)]
        if decision:
            steps.append(StepSpec("choose", ("aggregate",), "decision", choose))
        return WorkflowSpec("synthetic", tuple(steps))

    def test_dotted_predecessor_identity_keeps_nested_fanout_key_path(self):
        def inventory(context):
            return local(context, {"payload": {"items": [{"item_id": "alpha"}, {"item_id": "beta"}]}})
        def consume(context):
            return local(context, {"observed": context.item_id})
        spec = WorkflowSpec("synthetic", (StepSpec("L1.inventory", (), "local", inventory),
            StepSpec("relay", ("L1.inventory",), "local", lambda context: local(context)),
            StepSpec("consume", ("relay",), "local", consume, "L1.inventory.payload.items", 2)))
        outcome = Controller(lambda req: spec).run(request(self.project))
        self.assertEqual(outcome.status, "done", outcome.error)
        completed = [event for event in self.events(outcome.run_id, "item_completed") if event["step_id"] == "consume"]
        self.assertEqual({event["item_id"] for event in completed}, {"alpha", "beta"})

    def test_ancestor_state_excludes_unrelated_steps_and_other_fanout_items(self):
        def inventory(context):
            return local(context, {"shared": 7, "items": [{"item_id": "alpha"}, {"item_id": "beta"}]})
        def select(context):
            self.assertEqual(context.data["shared"], 7)
            return local(context, {"selected": context.item_id})
        def relay(context):
            self.assertNotIn("selected", context.data)
            return local(context)
        def consume(context):
            self.assertEqual(context.data["shared"], 7)
            self.assertEqual(context.data["selected"], context.item_id)
            return local(context)
        spec = WorkflowSpec("synthetic", (
            StepSpec("inventory", (), "local", inventory),
            StepSpec("unrelated", (), "local", lambda context: local(context, {"shared": 99})),
            StepSpec("select", ("inventory",), "local", select, "inventory.items", 2),
            StepSpec("relay", ("select",), "local", relay),
            StepSpec("consume", ("relay",), "local", consume, "inventory.items", 2)))
        outcome = Controller(lambda req: spec).run(request(self.project))
        self.assertEqual(outcome.status, "done", outcome.error)
        self.assertEqual({event["item_id"] for event in self.events(outcome.run_id, "item_completed") if event["step_id"] == "consume"}, {"alpha", "beta"})

    def test_nonwiki_fanout_exact_fanin_binding_ack_and_stable_ordinals(self):
        doubles = Doubles(self)
        outcome = Controller(lambda req: self.graph(), adapter_factory=doubles).run(request(self.project))
        self.assertEqual(outcome.status, "done", outcome.error)
        self.assertEqual(len(doubles.instances), 3)
        self.assertTrue(all(i.closed for i in doubles.instances))
        records = self.events(outcome.run_id, "item_completed")
        self.assertEqual([r["ordinal"] for r in records], [0, 1, 2, 3, 4])
        self.assertEqual(len({a["attempt_id"] for a in outcome.outputs if a["attempt_id"]}), 3)
        snapshots = self.project / "docs" / "tmp" / "pipeline-runs" / outcome.run_id / "snapshots"
        self.assertTrue(any(snapshots.iterdir()))

    def test_decision_resume_preserves_successful_siblings_and_run_identity(self):
        doubles = Doubles(self)
        controller = Controller(lambda req: self.graph(decision=True), adapter_factory=doubles)
        first = controller.run(request(self.project))
        self.assertEqual(first.status, "blocked")
        self.assertEqual(first.decision["key"], "continue")
        invalid = controller.resume(self.project, first.run_id, {"key": "different", "answer": "yes"})
        self.assertEqual(invalid.error["code"], "INVALID_DECISION")
        final = controller.resume(self.project, first.run_id, {"key": "continue", "answer": "yes"})
        self.assertEqual(final.status, "done", final.error)
        self.assertEqual(final.run_id, first.run_id)
        self.assertEqual(len(doubles.instances), 3)

    def test_changed_immutable_input_refuses_resume_without_dispatch(self):
        source = self.root / "source.txt"
        source.write_text("first", encoding="utf-8")
        def factory(req):
            spec = self.graph(decision=True)
            return replace(spec, metadata={"inputs": [{"path": str(source), "sha256": sha256(source.read_bytes()).hexdigest(), "media_type": "text/plain"}]})
        doubles = Doubles(self)
        controller = Controller(factory, adapter_factory=doubles)
        first = controller.run(request(self.project))
        source.write_text("changed", encoding="utf-8")
        outcome = controller.resume(self.project, first.run_id)
        self.assertEqual(outcome.error["code"], "SNAPSHOT_CHANGED")
        self.assertEqual(len(doubles.instances), 3)

    def test_wrong_attempt_output_is_retained_and_never_satisfies_fanin(self):
        doubles = Doubles(self, wrong_identity=True)
        outcome = Controller(lambda req: self.graph(), adapter_factory=doubles).run(request(self.project))
        self.assertEqual(outcome.status, "blocked")
        completed = self.events(outcome.run_id, "item_completed")
        self.assertFalse(any(row["step_id"] == "aggregate" for row in completed))
        self.assertTrue(self.events(outcome.run_id, "native_observation"))
        self.assertTrue(all(i.closed for i in doubles.instances))

    def test_completed_fail_requires_explicit_verdict_branch_policy(self):
        doubles = Doubles(self, fail_verifier=True)
        def prepare(ctx):
            return PreparedStep("ready", (task(ctx, "verify"),))
        def factory(req):
            return WorkflowSpec("synthetic", (StepSpec("verify", (), "task", prepare),))
        rejected = Controller(factory, adapter_factory=doubles).run(request(self.project))
        self.assertEqual(rejected.error["code"], "VERIFICATION_FAIL")
        def allowed(req):
            return WorkflowSpec("synthetic", (StepSpec("verify", (), "task", prepare, policy={"allow_verification_fail": True}),))
        accepted_execution = Controller(allowed, adapter_factory=doubles).run(request(self.project))
        self.assertEqual(accepted_execution.status, "done")
        result = self.events(accepted_execution.run_id, "item_completed")[0]
        self.assertEqual(result["data"]["verification"], "FAIL")

    def test_cancellation_latches_before_late_success_and_settles_owned_work(self):
        doubles = Doubles(self, cancel_on_ack=True)
        outcome = Controller(lambda req: self.graph(), adapter_factory=doubles).run(request(self.project))
        self.assertEqual(outcome.status, "cancelled", outcome.error)
        self.assertFalse(any(r["step_id"] == "aggregate" for r in self.events(outcome.run_id, "item_completed")))
        self.assertTrue(all(i.closed for i in doubles.instances))
        self.assertTrue(self.events(outcome.run_id, "cancel_requested"))

    def test_unknown_submission_cannot_be_resubmitted_on_resume(self):
        doubles = Doubles(self, channel_failure=True)
        def prepare(ctx):
            return PreparedStep("ready", (task(ctx),))
        controller = Controller(lambda req: WorkflowSpec("synthetic", (StepSpec("one", (), "task", prepare),)), adapter_factory=doubles)
        outcome = controller.run(request(self.project))
        self.assertEqual(outcome.status, "blocked")
        controller.resume(self.project, outcome.run_id)
        self.assertEqual(doubles.reconciliations, 1)
        self.assertEqual(sum(i.operations.count("launch_with_binding_barrier") for i in doubles.instances), 1)

    def test_dryrun_has_no_database_run_directory_or_native_calls(self):
        doubles = Doubles(self)
        before = list(self.project.iterdir())
        outcome = Controller(lambda req: self.graph(), adapter_factory=doubles).run(request(self.project, ("--dry-run",)))
        self.assertEqual(outcome.status, "preview")
        self.assertEqual(list(self.project.iterdir()), before)
        self.assertEqual(doubles.instances, [])

    def test_duplicate_or_phantom_fanout_subjects_are_refused(self):
        def inventory(ctx):
            return local(ctx, {"items": [{"item_id": "same"}, {"item_id": "same"}]})
        def prepare(ctx):
            return PreparedStep("ready", (task(ctx),))
        spec = WorkflowSpec("synthetic", (StepSpec("inventory", (), "local", inventory), StepSpec("fan", ("inventory",), "task", prepare, "inventory.items", 3)))
        doubles = Doubles(self)
        outcome = Controller(lambda req: spec, adapter_factory=doubles).run(request(self.project))
        self.assertEqual(outcome.error["code"], "INVALID_FANOUT")
        self.assertEqual(doubles.instances, [])

    def test_local_output_outside_current_candidate_cannot_authorize_effect(self):
        outside = self.root / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        def prepare(ctx):
            return local(ctx, artifacts=[{"name": "outside", "path": str(outside), "sha256": sha256(outside.read_bytes()).hexdigest(), "media_type": "application/json"}])
        outcome = Controller(lambda req: WorkflowSpec("synthetic", (StepSpec("local", (), "local", prepare),))).run(request(self.project))
        self.assertEqual(outcome.error["code"], "PATH_ESCAPE")
        self.assertFalse(self.events(outcome.run_id, "local_output_validated"))

    def test_local_publication_crash_reconciles_without_handler_rerun(self):
        destination = self.root / "published"
        destination.mkdir()
        target = destination / "accepted.json"
        calls = []
        def prepare(ctx):
            calls.append(ctx.run_id)
            candidate = ctx.candidate_dir / "accepted.json"
            candidate.write_text('{"actual":1}', encoding="utf-8")
            return local(ctx, {"publications": [{"kind": "replace", "candidate": str(candidate), "target": str(target), "expected_sha256": None, "operation_id": ctx.run_id + ":publish", "root": str(destination)}]},
                         [{"name": "accepted", "path": str(candidate), "sha256": sha256(candidate.read_bytes()).hexdigest(), "media_type": "application/json"}])
        controller = Controller(lambda req: WorkflowSpec("synthetic", (StepSpec("publish", (), "publish", prepare),)))
        original = PublicationManager.publish_file
        def crash(manager, *args, **kwargs):
            original(manager, *args, **kwargs)
            raise ControllerError("SYNTHETIC_CRASH", "After replacement, before item completion")
        with patch.object(PublicationManager, "publish_file", crash):
            first = controller.run(request(self.project))
        self.assertEqual(first.status, "blocked")
        self.assertEqual(target.read_text(encoding="utf-8"), '{"actual":1}')
        outcome = controller.resume(self.project, first.run_id)
        self.assertEqual(outcome.status, "done", outcome.error)
        self.assertEqual(calls, [first.run_id])
        self.assertEqual(len(self.events(first.run_id, "publication_intent")), 1)

    def test_native_fail_is_immediate_even_with_zero_blocking_counts(self):
        doubles = Doubles(self, review_verdict="fail", review_code=4)
        source = self.root / "source.md"
        source.write_text("Synthetic source", encoding="utf-8")
        def review(ctx):
            focus = ctx.candidate_dir / "focus.md"
            focus.write_text("Read full synthetic source.", encoding="utf-8")
            return PreparedStep("ready", local_result={"data": {"review": {"artifact_id": "fixed-claim", "artifact_tier": "B", "contract": "vault-wiki", "source": str(source), "focus": str(focus), "input_refs": [{"path": str(source), "sha256": sha256(source.read_bytes()).hexdigest(), "media_type": "text/markdown"}]}}})
        def factory(req):
            return WorkflowSpec("synthetic", (StepSpec("review", (), "review", review, policy={"max_full_reviews": 1, "max_repair_rounds": 0}), StepSpec("never", ("review",), "local", lambda ctx: local(ctx))))
        outcome = Controller(factory, adapter_factory=doubles).run(request(self.project))
        self.assertEqual(outcome.error["code"], "needs_orchestrator_review")
        self.assertEqual(len(doubles.instances), 1)
        self.assertEqual(len(self.events(outcome.run_id, "review_reserved")), 1)
        self.assertFalse(any(r["step_id"] == "never" for r in self.events(outcome.run_id, "item_completed")))


    def test_sequence_verifier_cap_is_shared_across_source_unrolled_steps(self):
        def prepare(ctx):
            return PreparedStep("ready", (task(ctx, "verify"),))
        steps = []
        for number in range(4):
            name = "verify-" + str(number)
            policy = {"allow_verification_fail": True,
                      "counter_groups": [{"key": "sequence", "operation_kind": "verify_pass", "maximum": 3}]}
            steps.append(StepSpec(name, (steps[-1].step_id,) if steps else (), "task", prepare, policy=policy))
        doubles = Doubles(self)
        outcome = Controller(lambda req: WorkflowSpec("synthetic", tuple(steps)), adapter_factory=doubles).run(request(self.project))
        self.assertEqual(outcome.error["code"], "STEP_EXHAUSTED")
        self.assertEqual(len(doubles.instances), 3)
        self.assertEqual(len(self.events(outcome.run_id, "step_reserved")), 3)

    def test_one_parallel_remediation_iteration_charges_c_once_and_each_note_once(self):
        def inventory(ctx):
            return local(ctx, {"items": [{"item_id": value} for value in ("a", "b", "c")]})
        def repair(ctx):
            return PreparedStep("ready", (task(ctx, "repair"),), local_result={"data": {"charges": [
                {"artifact_id": "C", "contract": "synthetic", "artifact_tier": "B", "operation_kind": "repair", "operation_id": "l5-round-1"}]}})
        policy = {"charges": [{"artifact_id": "N:{item_id}", "contract": "synthetic", "artifact_tier": "B", "operation_kind": "repair"}],
                  "counter_groups": [{"key": "L4L5", "operation_kind": "repair_round", "maximum": 3, "operation_id": "l5-round-1"}]}
        spec = WorkflowSpec("synthetic", (StepSpec("inventory", (), "local", inventory),
                            StepSpec("repair", ("inventory",), "task", repair, "inventory.items", 3, policy)))
        doubles = Doubles(self)
        outcome = Controller(lambda req: spec, adapter_factory=doubles).run(request(self.project))
        self.assertEqual(outcome.status, "done", outcome.error)
        connection = sqlite3.connect(self.project / "state.db")
        rows = dict(connection.execute("SELECT artifact_id,repair_rounds FROM wf_reviews WHERE run_id=?", (outcome.run_id,)))
        connection.close()
        self.assertEqual(rows, {"C": 1, "N:a": 1, "N:b": 1, "N:c": 1})
        self.assertEqual({event["operation_id"] for event in self.events(outcome.run_id, "step_reserved")}, {"l5-round-1"})

    def test_report_task_failure_retains_evidence_then_finishes_audit_as_failed(self):
        doubles = Doubles(self, wrong_identity=True)
        def evaluate(ctx):
            return PreparedStep("ready", (task(ctx, "verify"),))
        def audit(ctx):
            row = ctx.data["step_data"]["evaluate"][0]
            self.assertTrue(row["data"]["tooling_failure"])
            return local(ctx, {"audit_emitted": True, "run_status": "failed"})
        spec = WorkflowSpec("synthetic", (StepSpec("evaluate", (), "task", evaluate, policy={"allow_task_failure": True}),
                            StepSpec("audit", ("evaluate",), "local", audit)))
        outcome = Controller(lambda req: spec, adapter_factory=doubles).run(request(self.project))
        self.assertEqual(outcome.status, "failed", outcome.error)
        completed = {row["step_id"]: row for row in self.events(outcome.run_id, "item_completed")}
        diagnostic = completed["evaluate"]
        self.assertEqual(diagnostic["artifacts"], [])
        self.assertTrue(diagnostic["data"]["tooling_failure"])
        self.assertEqual(diagnostic["data"]["run_status"], "failed")
        self.assertEqual(json_value(outcome.error), diagnostic["data"]["error"])
        self.assertNotIn("result", diagnostic["data"])
        self.assertNotIn("verification", diagnostic["data"])
        self.assertNotIn("outputs", diagnostic["data"])
        self.assertTrue(completed["audit"]["data"]["audit_emitted"])
        failure = self.events(outcome.run_id, "report_task_failure")
        self.assertEqual(failure, [{"error": diagnostic["data"]["error"]}])
        with sqlite3.connect(self.project / "state.db") as connection:
            attempt = connection.execute("SELECT attempt_id,status,settled_at,result_json FROM wf_attempts WHERE run_id=?", (outcome.run_id,)).fetchone()
        connection.close()
        self.assertEqual(attempt[0], diagnostic["data"]["attempt_id"])
        self.assertEqual(attempt[1], "failed")
        self.assertIsNotNone(attempt[2])
        self.assertIsNone(attempt[3])
        self.assertEqual(len(self.events(outcome.run_id, "native_dispatch_intent")), 1)
        self.assertEqual(len(doubles.instances), 1)

    def test_preexecution_process_absence_falls_back_without_resetting_repair_charge(self):
        doubles = Doubles(self)
        def adapter(**kwargs):
            if kwargs["backend"] == "omp":
                raise NativeLaunchUnavailable("Synthetic absent adapter executable; no process created")
            return doubles(**kwargs)
        def prepare(ctx):
            return PreparedStep("ready", (task(ctx, "repair"),))
        spec = WorkflowSpec("synthetic", (StepSpec("repair", (), "task", prepare,
                            policy={"charges": [{"operation_kind": "repair"}]}),))
        outcome = Controller(lambda req: spec, adapter_factory=adapter).run(request(self.project))
        self.assertEqual(outcome.status, "done", outcome.error)
        self.assertEqual([instance.backend for instance in doubles.instances], ["claude"])
        self.assertEqual(len(self.events(outcome.run_id, "review_reserved")), 1)
        self.assertEqual(len(self.events(outcome.run_id, "native_operational_failure")), 1)
        intents = self.events(outcome.run_id, "native_dispatch_intent")
        self.assertEqual([value["backend"] for value in intents], ["omp", "claude"])
        self.assertEqual(len(self.events(outcome.run_id, "native_backend_unavailable")), 1)

    def test_disabled_codex_refuses_before_attempt_and_counter_reservation(self):
        doubles = Doubles(self)
        def prepare(ctx):
            return PreparedStep("ready", (task(ctx, "repair"),))
        spec = WorkflowSpec("synthetic", (StepSpec("repair", (), "task", prepare,
                            policy={"charges": [{"operation_kind": "repair"}]}),))
        outcome = Controller(lambda req: spec, adapter_factory=doubles).run(replace(request(self.project), backend="codex"))
        self.assertEqual((outcome.status, outcome.error["code"]), ("blocked", "NATIVE_BACKEND_UNAVAILABLE"))
        self.assertEqual(doubles.instances, [])
        self.assertEqual(self.events(outcome.run_id, "review_reserved"), [])
        connection = sqlite3.connect(self.project / "state.db")
        self.addCleanup(connection.close)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM wf_attempts").fetchone()[0], 0)

    def test_local_only_operations_do_not_resolve_or_commit_native_admission(self):
        spec = WorkflowSpec("synthetic", (StepSpec("report", (), "local", lambda ctx: local(ctx, {"claims": []})),))
        with patch("_shared.workflow.src.workflow.preflight_native", side_effect=AssertionError("No model work")):
            outcome = Controller(lambda req: spec, native_configuration={}).run(replace(request(self.project), backend="codex"))
        self.assertEqual(outcome.status, "done", outcome.error)
        self.assertEqual(self.events(outcome.run_id, "native_admission_committed"), [])

    def test_missing_native_readiness_refuses_before_attempt_and_charge(self):
        def prepare(ctx):
            return PreparedStep("ready", (task(ctx, "repair"),))
        spec = WorkflowSpec("synthetic", (StepSpec("repair", (), "task", prepare,
                            policy={"charges": [{"operation_kind": "repair"}]}),))
        doubles = Doubles(self)
        outcome = Controller(lambda req: spec, adapter_factory=doubles, native_configuration={}).run(request(self.project))
        self.assertEqual((outcome.status, outcome.error["code"]), ("blocked", "INVALID_NATIVE_DECLARATION"))
        self.assertEqual(self.events(outcome.run_id, "review_reserved"), [])
        self.assertEqual(self.events(outcome.run_id, "native_dispatch_intent"), [])
        self.assertEqual(doubles.instances, [])
        connection = sqlite3.connect(self.project / "state.db")
        self.addCleanup(connection.close)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM wf_attempts").fetchone()[0], 0)

    def test_explicit_backend_and_permission_refusal_never_operationally_fall_through(self):
        for backend, code in (("omp", "NATIVE_PROCESS_UNAVAILABLE"), ("auto", "PERMISSION_DENIED")):
            def prepare(ctx):
                return PreparedStep("ready", (task(ctx),))
            calls = []
            def adapter(**kwargs):
                calls.append(kwargs["backend"])
                if backend == "omp":
                    raise NativeLaunchUnavailable("Observed pre-process failure")
                raise ControllerError("PERMISSION_DENIED", "Native policy cannot authorize fallback")
            spec = WorkflowSpec("synthetic", (StepSpec("produce", (), "task", prepare),))
            outcome = Controller(lambda req: spec, adapter_factory=adapter).run(replace(request(self.project), backend=backend))
            self.assertEqual(outcome.error["code"], code)
            self.assertEqual(calls, ["omp"])

    def test_review_owns_deadline_and_cancellation_precedes_cached_terminal_frame(self):
        sent = []
        transport = SimpleNamespace(poll=lambda timeout: None, send=lambda operation, payload: sent.append(operation))
        controller = Controller(lambda req: None)
        controller._store = SimpleNamespace(is_cancelled=lambda run: False, append_event=lambda *args: None)
        context = SimpleNamespace(run_id="run", step=SimpleNamespace(policy={"timeout_seconds": 1}))
        descriptor = SimpleNamespace(attempt_id="attempt")
        review = _Active(context, descriptor, transport, {}, "omp", 0, phase="observing")
        with patch("_shared.workflow.src.controller.time.monotonic", return_value=7201):
            self.assertIsNone(controller._drive(review))
            worker = replace(review, review=None)
            with self.assertRaises(ControllerError) as error:
                controller._drive(worker)
            self.assertEqual(error.exception.code, "NATIVE_TIMEOUT")
        controller._store.is_cancelled = lambda run: True
        review.pending_frame = {"status": "completed"}
        self.assertEqual(controller._drive(review), {"status": "completed"})
        self.assertEqual(sent, ["cancel"])
        transport.poll = lambda timeout: (_ for _ in ()).throw(EOFError("unknown native submission"))
        with self.assertRaises(EOFError):
            controller._drive(review)

    def test_admission_is_once_before_reservation_and_stale_evidence_blocks_resume(self):
        from _shared.workflow.src import workflow
        witness = self.root / "admission-source.json"
        witness.write_text('{"fixture_only":true}', encoding="utf-8")
        req = request(self.project)
        native = static_native_context(self, self.root / "native-admission", req, config_files=(witness,))
        doubles = Doubles(self)
        def prepare(ctx):
            return PreparedStep("ready", (task(ctx),))
        def choose(ctx):
            return PreparedStep("blocked", decision={"key": "hold", "question": "Hold?", "options": ["yes"]})
        spec = WorkflowSpec("synthetic", (StepSpec("first", (), "task", prepare), StepSpec("second", ("first",), "task", prepare),
                                        StepSpec("hold", ("second",), "decision", choose)))
        controller = Controller(lambda value: spec, adapter_factory=doubles, skills_root=native.skills_root,
                                native_configuration=native.configuration)
        with patch.object(workflow, "preflight_native", wraps=workflow.preflight_native) as preflight:
            first = controller.run(req)
            self.assertEqual(preflight.call_count, 1)
        self.assertEqual(first.status, "blocked")
        self.assertEqual(len(doubles.instances), 2)
        self.assertEqual(len(self.events(first.run_id, "native_admission_committed")), 1)
        connection = sqlite3.connect(self.project / "state.db")
        self.addCleanup(connection.close)
        native_id = connection.execute("SELECT event_id FROM wf_events WHERE event_type='native_admission_committed'").fetchone()[0]
        self.assertLess(native_id, connection.execute("SELECT MIN(event_id) FROM wf_events WHERE attempt_id IS NOT NULL").fetchone()[0])
        immutable = self.project / "docs" / "tmp" / "pipeline-runs" / first.run_id
        original_inputs = (immutable / "input-manifest.json").read_bytes()
        witness.write_text('{"changed":true}', encoding="utf-8")
        second = controller.resume(self.project, first.run_id)
        self.assertEqual((second.status, second.error["code"]), ("blocked", "NATIVE_CAPABILITY_UNAVAILABLE"))
        answered = controller.resume(self.project, first.run_id, decision={"key": "hold", "answer": "yes"})
        self.assertEqual((answered.status, answered.error["code"]), ("blocked", "NATIVE_CAPABILITY_UNAVAILABLE"))
        self.assertEqual(connection.execute("SELECT status,answer_json FROM wf_decisions WHERE run_id=? AND decision_key='hold'",
                                           (first.run_id,)).fetchone(), ("waiting", None))
        self.assertEqual(len(doubles.instances), 2)
        self.assertEqual((immutable / "input-manifest.json").read_bytes(), original_inputs)

    def test_stale_native_refs_block_local_recovery_but_status_remains_read_only(self):
        witness = self.root / "local-admission-source.json"
        original_witness = b'{"fixture_only":true}'
        witness.write_bytes(original_witness)
        req = request(self.project)
        native = static_native_context(self, self.root / "local-native-admission", req, config_files=(witness,))
        doubles, prepared = Doubles(self), []
        target = self.project / "published.json"
        def produce(ctx):
            return PreparedStep("ready", (task(ctx),))
        def publish(ctx):
            prepared.append(ctx.attempt_id)
            candidate = ctx.candidate_dir / "published.json"
            candidate.write_bytes(b'{"actual_local_result":true}')
            artifact = {"name": "published", "path": str(candidate), "sha256": sha256(candidate.read_bytes()).hexdigest(),
                        "media_type": "application/json"}
            return local(ctx, {"publications": [{"kind": "replace", "candidate": str(candidate), "target": str(target),
                "expected_sha256": None, "root": str(self.project), "operation_id": ctx.run_id + ":publish"}]}, [artifact])
        spec = WorkflowSpec("synthetic", (StepSpec("produce", (), "task", produce), StepSpec("publish", ("produce",), "publish", publish)))
        controller = Controller(lambda value: spec, adapter_factory=doubles, skills_root=native.skills_root,
                                native_configuration=native.configuration)
        with patch.object(PublicationManager, "publish_file", side_effect=ControllerError("UNIT_HOLD", "Before LOCAL publication")):
            first = controller.run(req)
        self.assertEqual((first.status, first.error["code"]), ("blocked", "UNIT_HOLD"))
        self.assertEqual(len(prepared), 1)
        retained = {name: self.events(first.run_id, name) for name in
                    ("item_completed", "local_result_prepared", "local_output_validated", "native_dispatch_intent", "step_reserved")}
        self.assertEqual(len(retained["local_output_validated"]), 1)
        witness.write_bytes(b'{"changed_current_source":true}')
        history_before = {name: (self.project / "docs/tmp/pipeline-runs" / first.run_id / name).read_bytes()
                          for name in ("request.json", "definition.json", "native-admission.json")}
        history = controller.status(self.project, first.run_id)
        self.assertEqual((history.status, history.error["code"]), ("blocked", "UNIT_HOLD"), history.error)
        self.assertEqual({name: (self.project / "docs/tmp/pipeline-runs" / first.run_id / name).read_bytes()
                          for name in history_before}, history_before)
        with patch.object(PublicationManager, "publish_file", side_effect=AssertionError("Stale current admission cannot publish")) as publication:
            refused = controller.resume(self.project, first.run_id)
        self.assertEqual((refused.status, refused.error["code"]), ("blocked", "NATIVE_CAPABILITY_UNAVAILABLE"))
        publication.assert_not_called()
        self.assertEqual({name: self.events(first.run_id, name) for name in retained}, retained)
        self.assertEqual(len(prepared), 1)
        self.assertEqual(len(doubles.instances), 1)
        self.assertFalse(target.exists())
        witness.write_bytes(original_witness)
        completed = controller.resume(self.project, first.run_id)
        self.assertEqual(completed.status, "done", completed.error)
        self.assertEqual(target.read_bytes(), b'{"actual_local_result":true}')
        self.assertEqual(len(prepared), 1)
        self.assertEqual(len(doubles.instances), 1)

    def test_current_native_reference_aliases_refuse_without_mutating_inputs(self):
        from _shared.workflow.src import workflow
        witness = self.root / "native-proof.json"
        witness.write_bytes(b'{"synthetic_only":true}')
        reference = workflow._input_ref(witness)
        values = [reference, {**reference, "path": witness.as_posix()}]
        before = json.loads(json.dumps(values))
        with self.assertRaises(ControllerError) as error:
            workflow._checked_refs(values)
        self.assertEqual(error.exception.code, "NATIVE_CAPABILITY_UNAVAILABLE")
        self.assertEqual(values, before)
        self.assertEqual(workflow._checked_refs([reference]), [reference])

    def test_current_native_source_conflicting_alias_or_stale_hash_refuses(self):
        from _shared.workflow.src import workflow
        witness = self.root / "native-proof.json"
        witness.write_bytes(b'{"synthetic_only":true}')
        reference = workflow._input_ref(witness)
        for changed in ({"sha256": "0" * 64}, {"media_type": "application/octet-stream"}):
            with self.subTest(changed=changed):
                with self.assertRaises(ControllerError) as error:
                    workflow._checked_refs([reference, {**reference, **changed}])
                self.assertEqual(error.exception.code, "NATIVE_CAPABILITY_UNAVAILABLE")
        witness.write_bytes(b'{"changed_current_source":true}')
        with self.assertRaises(ControllerError) as error:
            workflow._checked_refs([reference])
        self.assertEqual(error.exception.code, "NATIVE_CAPABILITY_UNAVAILABLE")

    def test_current_native_source_identical_bytes_in_distinct_files_remain_distinct(self):
        from _shared.workflow.src import workflow
        files = [self.root / "first.json", self.root / "second.json"]
        for path in files:
            path.write_bytes(b'{"synthetic_only":true}')
        values = [workflow._input_ref(path) for path in files]
        self.assertEqual(workflow._checked_refs(values), values)

    def test_current_native_hardlink_references_are_each_checked_without_deduplication(self):
        from _shared.workflow.src import workflow
        witness = self.root / "native-proof.json"
        witness.write_bytes(b'{"synthetic_only":true}')
        alias = self.root / "native-proof-alias.json"
        try:
            alias.hardlink_to(witness)
        except OSError as error:
            self.skipTest(str(error))
        values = [workflow._input_ref(witness), workflow._input_ref(alias)]
        self.assertEqual(workflow._checked_refs(values), values)
        alias.write_bytes(b'{"changed_through_hardlink":true}')
        with self.assertRaises(ControllerError) as error:
            workflow._checked_refs(values)
        self.assertEqual(error.exception.code, "NATIVE_CAPABILITY_UNAVAILABLE")

    def test_publication_guard_sees_complete_local_plan_before_each_effect_and_recovery(self):
        target = self.root / "published.json"
        observed = []
        def publish(ctx):
            path = ctx.candidate_dir / "controls.json"
            path.write_text('{"managed":true}', encoding="utf-8")
            ref = {"name": "controls", "path": str(path), "sha256": sha256(path.read_bytes()).hexdigest(), "media_type": "application/json"}
            return local(ctx, {"whole_plan": {"guard": "original"}, "publications": [{"kind": "replace", "candidate": str(path),
                         "target": str(target), "expected_sha256": None, "root": str(self.root), "operation_id": "controls:" + ctx.run_id + ":publish"}]}, [ref])
        def guard(ctx, store, effect):
            self.assertEqual(ctx.data["whole_plan"]["guard"], "original")
            self.assertTrue(effect["operation_id"].startswith("controls:" + ctx.run_id))
            self.assertEqual(effect["candidate"], str(ctx.candidate_dir / "controls.json"))
            observed.append(target.exists())
        spec = WorkflowSpec("synthetic", (StepSpec("publish", (), "publish", publish),), publication_guard=guard)
        controller = Controller(lambda req: spec)
        original = PublicationManager.publish_file
        def interrupt(manager, *args, **kwargs):
            original(manager, *args, **kwargs)
            raise ControllerError("SYNTHETIC_CRASH", "Effect completed before controller collection")
        with patch.object(PublicationManager, "publish_file", interrupt):
            first = controller.run(request(self.project))
        self.assertEqual(first.status, "blocked")
        self.assertEqual(observed, [False])
        actual = target.read_bytes()
        second = controller.resume(self.project, first.run_id)
        self.assertEqual(second.status, "done", second.error)
        self.assertEqual(observed, [False, True])
        self.assertEqual(target.read_bytes(), actual)

    def test_guard_refusal_prevents_canonical_write_and_is_definition_bound(self):
        target = self.root / "forbidden.json"
        def publish(ctx):
            path = ctx.candidate_dir / "record.json"
            path.write_text("{}", encoding="utf-8")
            return local(ctx, {"publications": [{"kind": "replace", "candidate": str(path), "target": str(target),
                         "expected_sha256": None, "root": str(self.root)}]},
                         [{"name": "record", "path": str(path), "sha256": sha256(path.read_bytes()).hexdigest(), "media_type": "application/json"}])
        def guard(ctx, store, effect):
            raise ControllerError("PROTECTED_GUARD", "Whole-plan reference changed")
        spec = WorkflowSpec("synthetic", (StepSpec("publish", (), "publish", publish),), publication_guard=guard)
        controller = Controller(lambda req: spec)
        self.assertNotEqual(controller._definition(spec)[1], controller._definition(replace(spec, publication_guard=None))[1])
        outcome = controller.run(request(self.project))
        self.assertEqual(outcome.error["code"], "PROTECTED_GUARD")
        self.assertFalse(target.exists())


    def test_review_receipt_copies_preserve_opaque_paths_and_supply_trusted_aliases(self):
        doubles = Doubles(self)
        source = self.root / "evidence.md"
        source.write_text("Complete synthetic evidence", encoding="utf-8")
        def review(ctx):
            focus = ctx.candidate_dir / "focus.md"
            focus.write_text("Read complete evidence, preserve numbers and conditions.", encoding="utf-8")
            return PreparedStep("ready", local_result={"data": {"review": {"artifact_id": "fixed", "artifact_tier": "B", "contract": "synthetic", "source": str(source), "focus": str(focus), "input_refs": [{"path": str(source), "sha256": sha256(source.read_bytes()).hexdigest(), "media_type": "text/markdown"}]}}})
        def consume(ctx):
            data = ctx.data["step_data"]["review"][0]["data"]
            self.assertTrue(data["review_gate"]["accepted"])
            original = data["native_review"]["payload"]["native_result"]
            self.assertNotEqual(original["result_path"], data["producer_evidence"][0]["path"])
            aliases = ctx.data["native_evidence_aliases"]
            self.assertEqual(Path(original["result_path"]).read_bytes(), Path(aliases[original["result_path"]]["path"]).read_bytes())
            self.assertTrue(Path(aliases[original["receipt_path"]]["path"]).is_relative_to(self.project))
            return local(ctx, {"consumed_actual_copied_evidence": True})
        spec = WorkflowSpec("synthetic", (StepSpec("review", (), "review", review), StepSpec("consume", ("review",), "local", consume)))
        outcome = Controller(lambda req: spec, adapter_factory=doubles).run(request(self.project))
        self.assertEqual(outcome.status, "done", outcome.error)
        self.assertEqual(len(self.events(outcome.run_id, "native_review_evidence_retained")), 1)

    def test_human_protection_rechecked_at_local_publication(self):
        vault = self.root / "vault"
        vault.mkdir()
        target = vault / "manual.md"
        prior = "---\nhuman_edited: true\n---\nDo not replace this manual text.\n"
        target.write_text(prior, encoding="utf-8")
        def prepare(ctx):
            candidate = ctx.candidate_dir / "manual.md"
            candidate.write_text("Machine replacement", encoding="utf-8")
            return local(ctx, {"publications": [{"kind": "replace", "candidate": str(candidate), "target": str(target),
                         "expected_sha256": sha256(target.read_bytes()).hexdigest(), "root": str(vault), "operation_id": ctx.run_id + ":manual"}]},
                         [{"name": "manual", "path": str(candidate), "sha256": sha256(candidate.read_bytes()).hexdigest(), "media_type": "text/markdown"}])
        outcome = Controller(lambda req: WorkflowSpec("synthetic", (StepSpec("publish", (), "publish", prepare),))).run(request(self.project))
        self.assertEqual(outcome.error["code"], "PROTECTED_TARGET")
        self.assertEqual(target.read_text(encoding="utf-8"), prior)

    def test_only_definite_rename_refusal_can_take_source_declared_version_branch(self):
        for error_code in ("RENAME_REFUSED", "EFFECT_UNCERTAIN"):
            with self.subTest(error_code=error_code):
                vault = self.root / error_code
                vault.mkdir()
                canonical, archive, alternate = (vault / name for name in ("current.json", "archived.json", "current-date.json"))
                canonical.write_text('{"prior":1}', encoding="utf-8")
                def produce(ctx):
                    return PreparedStep("ready", (task(ctx),))
                def publish(ctx):
                    ref = ctx.predecessors[0]
                    rename_id = ctx.run_id + ":rename"
                    initial = {"kind": "rename", "source": str(canonical), "target": str(archive), "expected_sha256": sha256(canonical.read_bytes()).hexdigest(), "root": str(vault), "operation_id": rename_id, "attempt_id": ref["attempt_id"]}
                    replacement = {"kind": "replace", "candidate": ref["path"], "target": str(canonical), "expected_sha256": None, "root": str(vault), "operation_id": ctx.run_id + ":canonical", "attempt_id": ref["attempt_id"]}
                    fallback = {**replacement, "target": str(alternate), "operation_id": ctx.run_id + ":fallback"}
                    return local(ctx, {"publications": [initial, replacement], "publication_refusal": {rename_id: {"fallback_publications": [fallback]}}})
                spec = WorkflowSpec("synthetic", (StepSpec("produce", (), "task", produce), StepSpec("publish", ("produce",), "publish", publish)))
                doubles = Doubles(self)
                with patch.object(PublicationManager, "rename_file", side_effect=PublicationError(error_code, "Synthetic filesystem outcome")):
                    outcome = Controller(lambda req: spec, adapter_factory=doubles).run(request(self.project))
                self.assertEqual(outcome.status, "done" if error_code == "RENAME_REFUSED" else "blocked", outcome.error)
                self.assertEqual(canonical.read_text(encoding="utf-8"), '{"prior":1}')
                self.assertEqual(alternate.exists(), error_code == "RENAME_REFUSED")
                self.assertFalse(archive.exists())


    def test_report_claim_no_review_preserves_uninvoked_zero_charge_subject(self):
        doubles = Doubles(self)
        source = self.root / "claims.md"
        source.write_text("Three fixed synthetic claims.", encoding="utf-8")
        def snapshot(ctx):
            return local(ctx, {"review_subjects": [{"artifact_id": "claim-" + str(n), "contract": "synthetic", "artifact_tier": "B", "max_full_reviews": 1, "max_repairs": 0} for n in (1, 2, 3)]})
        def review(ctx):
            number = int(ctx.step_id[-1])
            if number == 3 and ctx.data["step_data"]["claim2"][0]["data"]["review_gate"]["tooling_failure"]:
                return PreparedStep("skip", skip_reason="Page aborted; third claim remains unreviewed")
            if number == 2:
                doubles.review_code, doubles.review_verdict, doubles.review_status = 1, None, "no_review"
            focus = ctx.candidate_dir / "focus.md"
            focus.write_text("Inspect exactly claim " + str(number), encoding="utf-8")
            return PreparedStep("ready", local_result={"data": {"review": {"artifact_id": "claim-" + str(number), "artifact_tier": "B", "contract": "synthetic", "source": str(source), "focus": str(focus), "input_refs": [{"path": str(source), "sha256": sha256(source.read_bytes()).hexdigest(), "media_type": "text/markdown"}]}}})
        def audit(ctx):
            return local(ctx, {"run_status": "failed", "audit_emitted": True, "unreviewed_claims": [2, 3]})
        policy = {"max_full_reviews": 1, "max_repair_rounds": 0, "allow_review_fail": True}
        spec = WorkflowSpec("synthetic", (StepSpec("snapshot", (), "local", snapshot),
                            StepSpec("claim1", ("snapshot",), "review", review, policy=policy),
                            StepSpec("claim2", ("claim1",), "review", review, policy=policy),
                            StepSpec("claim3", ("claim2",), "review", review, policy=policy),
                            StepSpec("audit", ("claim3",), "local", audit)))
        outcome = Controller(lambda req: spec, adapter_factory=doubles).run(request(self.project))
        self.assertEqual(outcome.status, "failed", outcome.error)
        connection = sqlite3.connect(self.project / "state.db")
        rows = list(connection.execute("SELECT artifact_id,full_attempts,repair_rounds FROM wf_reviews WHERE run_id=? ORDER BY artifact_id", (outcome.run_id,)))
        connection.close()
        self.assertEqual(rows, [("claim-1", 1, 0), ("claim-2", 1, 0), ("claim-3", 0, 0)])
        self.assertEqual(len(doubles.instances), 2)
        self.assertTrue(any(row["data"].get("audit_emitted") for row in self.events(outcome.run_id, "item_completed")))


    def test_restorer_preserves_original_graph_after_owned_input_effect_but_refuses_foreign_edit(self):
        for foreign_edit in (False, True):
            with self.subTest(foreign_edit=foreign_edit):
                base = self.root / ("foreign" if foreign_edit else "owned")
                base.mkdir()
                project = base / "project"
                project.mkdir()
                control = base / "control.json"
                control.write_text('{"revision":0}', encoding="utf-8")
                def declare(metadata):
                    def update(ctx):
                        candidate = ctx.candidate_dir / "control.json"
                        candidate.write_text('{"revision":1}', encoding="utf-8")
                        return local(ctx, {"publications": [{"kind": "replace", "candidate": str(candidate), "target": str(control),
                                     "expected_sha256": ctx.data["original_sha256"], "root": str(base), "operation_id": ctx.run_id + ":control"}]},
                                     [{"name": "control", "path": str(candidate), "sha256": sha256(candidate.read_bytes()).hexdigest(), "media_type": "application/json"}])
                    def choose(ctx):
                        if "continue" not in ctx.data["decisions"]:
                            return PreparedStep("blocked", decision={"key": "continue", "question": "Continue?", "options": ["yes"]})
                        return local(ctx, {"finished": True})
                    return WorkflowSpec("synthetic", (StepSpec("update", (), "publish", update), StepSpec("choose", ("update",), "decision", choose)), metadata=metadata)
                def factory(req):
                    digest = sha256(control.read_bytes()).hexdigest()
                    return declare({"inputs": [{"path": str(control), "sha256": digest, "media_type": "application/json"}], "data": {"original_sha256": digest}})
                def restore(req, *, snapshot):
                    return declare(snapshot["metadata"])
                controller = Controller(factory, workflow_restorer=restore)
                first = controller.run(request(project))
                self.assertEqual(first.status, "blocked", first.error)
                self.assertEqual(control.read_text(encoding="utf-8"), '{"revision":1}')
                if foreign_edit:
                    control.write_text('{"revision":99}', encoding="utf-8")
                outcome = controller.resume(project, first.run_id, {"key": "continue", "answer": "yes"})
                self.assertEqual(outcome.status, "blocked" if foreign_edit else "done", outcome.error)
                if foreign_edit:
                    self.assertEqual(outcome.error["code"], "SNAPSHOT_CHANGED")
                    self.assertEqual(control.read_text(encoding="utf-8"), '{"revision":99}')

    def test_unknown_external_outcome_reconciles_same_operation_without_second_mutation(self):
        calls, mutations = [], []
        def prepare(ctx):
            return local(ctx, {"external_actions": [{"operation_id": ctx.run_id + ":external", "provider": "synthetic", "value": 1}]})
        def effect(ctx, store, operation):
            calls.append(ctx.data["effect_mode"])
            if ctx.data["effect_mode"] == "execute":
                store.append_event(ctx.run_id, None, "synthetic_effect_intent", operation)
                mutations.append(operation["operation_id"])
                return {"status": "unknown", "message": "Synthetic response lost after effect"}
            # A read-only reconciliation stand-in; no mutation on this branch.
            return {"status": "completed", "retained_id": "synthetic-external-id"}
        spec = WorkflowSpec("synthetic", (StepSpec("external", (), "publish", prepare),), effect_handler=effect)
        controller = Controller(lambda req: spec)
        first = controller.run(request(self.project))
        self.assertEqual(first.status, "blocked")
        outcome = controller.resume(self.project, first.run_id)
        self.assertEqual(outcome.status, "done", outcome.error)
        self.assertEqual(calls, ["execute", "reconcile"])
        self.assertEqual(mutations, [first.run_id + ":external"])
        self.assertEqual(len(self.events(first.run_id, "external_effect_authorized")), 1)
        self.assertEqual(len(self.events(first.run_id, "external_effect_observed")), 2)


    def projection_graph(self, variation="valid", preparations=None):
        """Mechanical synthetic projection; no claim of actual native wiki review."""
        target = self.root / ("projected-" + variation + ".md")
        def produce(ctx):
            wire = c.task_to_dict(task(ctx))
            wire["outputs"] = [{"name": "note", "relative_path": "note.md", "media_type": "text/markdown", "required": True}]
            wire["semantic_sha256"] = c.semantic_hash(wire)
            return PreparedStep("ready", (c.task_from_dict(wire),))
        def gate(ctx):
            native = next(ref for ref in ctx.predecessors if ref["name"] == "note")
            actual = Path(native["path"]).read_bytes()
            if b"10 only after Q" not in actual:
                raise ControllerError("SYNTHETIC_GATE_FAIL", "Actual fixture condition missing")
            path = ctx.candidate_dir / "synthetic-gate.json"
            path.write_text(json.dumps({"unit_double_only": True, "condition_present": True, "source_sha256": sha256(actual).hexdigest()}), encoding="utf-8")
            return local(ctx, {"verification": "PASS"}, [{"name": "gate", "path": str(path), "sha256": sha256(path.read_bytes()).hexdigest(), "media_type": "application/json"}])
        def project(ctx):
            if preparations is not None:
                preparations.append(ctx.run_id)
            native = next(ref for ref in ctx.predecessors if ref["name"] == "note")
            source = {key: native[key] for key in ("name", "path", "sha256", "media_type")}
            original = Path(source["path"]).read_bytes()
            observed = ctx.data["gate_observed_at"]
            settled = observed["produce"]["main"]["settled_at"]
            completed = observed["gate"]["main"]["completed_at"]
            self.assertIs(type(settled), int)
            self.assertIs(type(completed), int)
            accepted_at = datetime.fromtimestamp(max(settled, completed) / 1000, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            def edit(before, after):
                start = original.index(before)
                return {"start": start, "end": start + len(before), "before_hex": before.hex(), "after_hex": after.hex()}
            edits = [edit(b"draft", b"published"), edit(b"null", ('"' + accepted_at + '"').encode("utf-8"))]
            if variation == "declared-body":
                edits = [edits[0], edit(b"10 only after Q", b"99 without Q")]
            cursor, pieces = 0, []
            for value in edits:
                pieces.extend((original[cursor:value["start"]], bytes.fromhex(value["after_hex"])))
                cursor = value["end"]
            changed = b"".join((*pieces, original[cursor:]))
            if variation == "body":
                changed = changed.replace(b"Body: 10", b"Body: 99")
            candidate_path = ctx.candidate_dir / "published.md"
            candidate_path.write_bytes(changed)
            candidate = {"name": "note", "path": str(candidate_path), "sha256": sha256(changed).hexdigest(), "media_type": "text/markdown"}
            producer = "unknown-old-attempt" if variation == "wrong-attempt" else native["attempt_id"]
            projection = {"schema_version": 1, "projection_id": ctx.run_id + ":lifecycle", "producer_attempt_id": producer,
                          "source": source, "candidate": candidate, "edits": edits}
            gate_ref = next(ref for ref in ctx.predecessors if ref["name"] == "gate")
            proof = {**projection, "kind": "wiki-lifecycle", "changed_fields": ["status", "last_verified"], "accepted_at": accepted_at,
                     "gate_refs": [{**{key: gate_ref[key] for key in ("path", "sha256", "media_type")}, "gate": "synthetic-local-condition"}],
                     "native_scope": "original candidate H0; H1 has deterministic earned lifecycle metadata only"}
            proof_path = ctx.candidate_dir / "projection-proof.json"
            proof_path.write_text(json.dumps(proof, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            proof_ref = {"path": str(proof_path), "sha256": sha256(proof_path.read_bytes()).hexdigest(), "media_type": "application/json"}
            projection["proof_ref"] = proof_ref
            projected_ref = {**candidate, "producer_attempt_id": producer, "projection_id": projection["projection_id"],
                             "projection_ref": proof_ref, "native_producer_ref": {key: source[key] for key in ("path", "sha256", "media_type")}}
            if variation == "source":
                Path(source["path"]).write_bytes(original + b"Changed original source\n")
            elif variation == "proof":
                proof_path.write_text('{"tampered":true}', encoding="utf-8")
            elif variation == "gate":
                Path(gate_ref["path"]).write_text('{"condition_present":false}', encoding="utf-8")
            elif variation == "lineage":
                projected_ref["native_producer_ref"] = {**projected_ref["native_producer_ref"], "sha256": "0" * 64}
            elif variation == "extra-field":
                projection["verification"] = "PASS"
            projections = [projection, projection] if variation == "duplicate" else [projection]
            return local(ctx, {"candidate_projections": projections, "publications": [{"kind": "replace", "candidate_path": str(candidate_path),
                         "desired_sha256": candidate["sha256"], "acceptance": {"fixture_only": True}, "target": str(target), "root": str(self.root),
                         "expected_sha256": None, "operation_id": ctx.run_id + ":projected-publish", "attempt_id": producer}]},
                         [projected_ref, {"name": "projection-proof", **proof_ref}])
        return WorkflowSpec("synthetic", (StepSpec("produce", (), "task", produce), StepSpec("gate", ("produce",), "local", gate),
                            StepSpec("project", ("produce", "gate"), "publish", project)), metadata={"data": {"variation": variation}}), target

    def test_lifecycle_projection_preserves_h0_and_native_result_with_actual_gate_time(self):
        doubles = Doubles(self)
        spec, target = self.projection_graph()
        outcome = Controller(lambda req: spec, adapter_factory=doubles).run(request(self.project))
        self.assertEqual(outcome.status, "done", outcome.error)
        events = self.events(outcome.run_id, "candidate_projection_validated")
        self.assertEqual(len(events), 1)
        projection = events[0]["projection"]
        original = Path(projection["source"]["path"]).read_bytes()
        self.assertIn(b"status: draft", original)
        self.assertIn(b"last_verified: null", original)
        self.assertEqual(sha256(original).hexdigest(), projection["source"]["sha256"])
        self.assertEqual(sha256(target.read_bytes()).hexdigest(), projection["candidate"]["sha256"])
        self.assertEqual(original.split(b"---\n", 2)[2], target.read_bytes().split(b"---\n", 2)[2])
        output = next(ref for ref in outcome.outputs if ref.get("projection_id") == projection["projection_id"])
        self.assertIsNone(output["attempt_id"])
        self.assertEqual(output["producer_attempt_id"], projection["producer_attempt_id"])
        self.assertEqual(output["native_producer_ref"]["sha256"], projection["source"]["sha256"])
        connection = sqlite3.connect(self.project / "state.db")
        native_result = json.loads(connection.execute("SELECT result_json FROM wf_attempts WHERE attempt_id=?", (projection["producer_attempt_id"],)).fetchone()[0])
        gate_time = connection.execute("SELECT created_at FROM wf_events WHERE run_id=? AND event_type='item_completed' AND payload_json LIKE '%\"step_id\":\"gate\"%'", (outcome.run_id,)).fetchone()[0]
        connection.close()
        proof = json.loads(Path(projection["proof_ref"]["path"]).read_text(encoding="utf-8"))
        self.assertEqual(proof["accepted_at"], datetime.fromtimestamp(gate_time / 1000, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"))
        self.assertEqual(native_result["artifacts"][0]["sha256"], projection["source"]["sha256"])
        self.assertEqual(len(doubles.instances), 1)
        self.assertEqual(self.events(outcome.run_id, "review_reserved"), [])

    def test_projection_rejects_tamper_wrong_attempt_body_edit_and_fake_lineage(self):
        for variation in ("body", "declared-body", "source", "proof", "gate", "wrong-attempt", "lineage", "extra-field", "duplicate"):
            with self.subTest(variation=variation):
                doubles = Doubles(self)
                spec, target = self.projection_graph(variation)
                outcome = Controller(lambda req: spec, adapter_factory=doubles).run(request(self.project))
                self.assertEqual(outcome.status, "blocked")
                self.assertFalse(target.exists())
                self.assertEqual(self.events(outcome.run_id, "candidate_projection_validated"), [])
                self.assertFalse(any(ref.get("projection_id") for ref in outcome.outputs))
                self.assertEqual(len(doubles.instances), 1)
                self.assertEqual(self.events(outcome.run_id, "review_reserved"), [])

    def test_projection_registered_before_local_completion_recovers_without_preparing_again(self):
        preparations, doubles = [], Doubles(self)
        spec, target = self.projection_graph(preparations=preparations)
        controller = Controller(lambda req: spec, adapter_factory=doubles)
        original = Store.append_event
        interrupted = []
        def crash(store, run_id, attempt_id, event_type, payload):
            result = original(store, run_id, attempt_id, event_type, payload)
            if event_type == "candidate_projection_validated" and not interrupted:
                interrupted.append(True)
                raise ControllerError("SYNTHETIC_CRASH", "Projection committed before LOCAL completion")
            return result
        with patch.object(Store, "append_event", crash):
            first = controller.run(request(self.project))
        self.assertEqual(first.status, "blocked")
        self.assertFalse(target.exists())
        projection = self.events(first.run_id, "candidate_projection_validated")[0]["projection"]
        outcome = controller.resume(self.project, first.run_id)
        self.assertEqual(outcome.status, "done", outcome.error)
        self.assertEqual(preparations, [first.run_id])
        self.assertEqual(len(doubles.instances), 1)
        self.assertEqual(len(self.events(first.run_id, "candidate_projection_validated")), 1)
        self.assertEqual(sha256(target.read_bytes()).hexdigest(), projection["candidate"]["sha256"])

    def test_desired_projection_effect_restores_missing_private_candidate_without_replay(self):
        preparations, doubles = [], Doubles(self)
        spec, target = self.projection_graph(preparations=preparations)
        controller = Controller(lambda req: spec, adapter_factory=doubles)
        original = PublicationManager.publish_file
        def crash(manager, *args, **kwargs):
            original(manager, *args, **kwargs)
            raise ControllerError("SYNTHETIC_CRASH", "Desired effect before LOCAL completion")
        with patch.object(PublicationManager, "publish_file", crash):
            first = controller.run(request(self.project))
        self.assertEqual(first.status, "blocked")
        projection = self.events(first.run_id, "candidate_projection_validated")[0]["projection"]
        Path(projection["candidate"]["path"]).unlink()
        with patch.object(PublicationManager, "_replace", side_effect=AssertionError("Do not replay desired canonical effect")):
            outcome = controller.resume(self.project, first.run_id)
        self.assertEqual(outcome.status, "done", outcome.error)
        self.assertEqual(preparations, [first.run_id])
        self.assertEqual(len(doubles.instances), 1)
        self.assertEqual(len(self.events(first.run_id, "local_artifact_restored")), 1)
        self.assertEqual(sha256(target.read_bytes()).hexdigest(), projection["candidate"]["sha256"])
        self.assertEqual(self.events(first.run_id, "review_reserved"), [])


    def test_projection_admission_does_not_weaken_expected_preimage(self):
        doubles = Doubles(self)
        spec, target = self.projection_graph()
        target.write_bytes(b"Pre-existing human bytes\n")
        outcome = Controller(lambda req: spec, adapter_factory=doubles).run(request(self.project))
        self.assertEqual(outcome.status, "blocked")
        self.assertEqual(target.read_bytes(), b"Pre-existing human bytes\n")
        self.assertEqual(len(self.events(outcome.run_id, "candidate_projection_validated")), 1)
        self.assertFalse(any(ref.get("projection_id") for ref in outcome.outputs))
        self.assertEqual(len(doubles.instances), 1)

    def test_changed_projection_bytes_after_admission_still_cannot_publish(self):
        for member in ("candidate", "proof_ref"):
            with self.subTest(member=member):
                doubles = Doubles(self)
                spec, target = self.projection_graph(member)
                original = Store.append_event
                changed = []
                def tamper(store, run_id, attempt_id, event_type, payload):
                    result = original(store, run_id, attempt_id, event_type, payload)
                    if event_type == "candidate_projection_validated" and not changed:
                        changed.append(True)
                        path = Path(payload["projection"][member]["path"])
                        path.write_bytes(path.read_bytes() + b"Tampered after admission\n")
                    return result
                with patch.object(Store, "append_event", tamper):
                    outcome = Controller(lambda req: spec, adapter_factory=doubles).run(request(self.project))
                self.assertEqual(outcome.status, "blocked")
                self.assertFalse(target.exists())
                self.assertFalse(any(ref.get("projection_id") for ref in outcome.outputs))
                self.assertEqual(len(doubles.instances), 1)
                self.assertEqual(self.events(outcome.run_id, "review_reserved"), [])




class FramingTests(unittest.TestCase):
    def peer(self, frame):
        peer = object.__new__(NativeIPC)
        peer.run_id, peer.attempt_id, peer.owner_epoch = "run", "attempt", 9
        import queue
        peer.frames = queue.Queue()
        peer.frames.put(json.dumps(frame))
        peer.requests = {"known": {}}
        peer._retain = lambda direction, value: None
        return peer

    def test_stale_epoch_and_wrong_request_are_rejected_before_database_binding(self):
        base = {"request_id": "known", "run_id": "run", "attempt_id": "attempt", "owner_epoch": 9,
                "status": "binding_required", "payload": {"binding": {"child": "one"}}, "error": None}
        for changes in ({"owner_epoch": 8}, {"owner_epoch": True}, {"request_id": "foreign"}, {"attempt_id": "other"}):
            with self.subTest(changes=changes), self.assertRaises(ControllerError):
                self.peer({**base, **changes}).poll()
    def test_errored_binding_frame_is_never_acknowledgeable(self):
        frame = {"request_id": "known", "run_id": "run", "attempt_id": "attempt", "owner_epoch": 9,
                 "status": "binding_required", "payload": {"binding": {"child": "one"}},
                 "error": {"code": "REFUSED", "message": "Do not spawn", "details": {}}}
        with self.assertRaises(ControllerError):
            self.peer(frame).poll()


    def test_empty_findings_cannot_replace_current_canonical_review_qualification(self):
        reply = {"status": "completed", "payload": {"locally_settled": True, "native_result": {"status": "reviewed", "verdict": "fail", "exitCode": 4, "loop_should_continue": False, "findings": []}}}
        self.assertFalse(review_gate(reply)["accepted"])


if __name__ == "__main__":
    unittest.main()
