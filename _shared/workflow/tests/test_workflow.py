"""Closed CLI/registry/declared-layout regressions; never launch native programs."""
from contextlib import redirect_stdout
from hashlib import sha256
import io
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from _shared.workflow.src.controller import (Controller, ControllerError, PreparedStep,
                                            RunRequest, StepSpec, WorkflowSpec, json_value)
from _shared.workflow.src import workflow
from _shared.workflow.src.store import Store

REGISTRY = ROOT / "_shared" / "workflow" / "registry.json"


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        profile = patch.object(workflow, "_user_profile", return_value=self.root)
        profile.start()
        self.addCleanup(profile.stop)
        self.skills = self.root / "skills"
        self.skills.mkdir()
        self.project = self.root / "project"
        self.project.mkdir()
        self.registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
        write_json(self.skills / "_workflow" / "registry.json", self.registry)

    def invoke(self, argv):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = workflow.main(argv, skills_root=self.skills)
        return code, json.loads(stdout.getvalue())

    def run_request(self, **overrides):
        return {"schema_version": 1, "workflow": "wiki-ingest", "project_root": str(self.project),
                "arguments": [], "caller_host": "omp", "caller_family": "gpt", "backend": "auto", "profile": None, **overrides}

    def test_exact_five_registry_rejects_partial_extra_alias_and_import_override(self):
        self.assertEqual(set(workflow.load_registry(self.skills)["workflows"]), workflow.ROUTES)
        for change in ("remove", "extra", "import"):
            changed = json.loads(json.dumps(self.registry))
            if change == "remove":
                changed["workflows"].pop("wiki-content-check")
            elif change == "extra":
                changed["workflows"]["standalone"] = changed["workflows"]["wiki-ingest"]
            else:
                changed["workflows"]["wiki-ingest"]["module"] = "../../outside.py"
            write_json(self.skills / "_workflow" / "registry.json", changed)
            with self.subTest(change=change), self.assertRaises(ControllerError):
                workflow.load_registry(self.skills)

    def test_closed_request_refuses_unknown_missing_types_and_profiles_before_effects(self):
        for value in (self.run_request(unexpected=True), self.run_request(schema_version=True), self.run_request(arguments="source.md"),
                      self.run_request(profile="hidden-role"), self.run_request(profile={"instructions": "override"}),
                      self.run_request(backend="shell"), self.run_request(project_root="relative")):
            request_path = self.root / "request.json"
            write_json(request_path, value)
            code, envelope = self.invoke(["run", "--request", str(request_path)])
            self.assertEqual(code, 1)
            self.assertEqual(envelope["status"], "failed")
            self.assertFalse((self.project / "state.db").exists())

    def test_duplicate_json_key_and_cli_abbreviation_are_refused(self):
        path = self.root / "duplicate.json"
        path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
        code, envelope = self.invoke(["run", "--request", str(path)])
        self.assertEqual((code, envelope["error"]["code"]), (1, "INVALID_JSON"))
        code, envelope = self.invoke(["run", "--req", str(path)])
        self.assertEqual((code, envelope["error"]["code"]), (1, "INVALID_REQUEST"))

    def test_preview_is_real_no_effect_cli_with_structured_plan(self):
        request_path = self.root / "preview.json"
        write_json(request_path, self.run_request(arguments=["--dry-run", "--clickup"]))
        def factory(req):
            def forbidden(context):
                raise AssertionError("Preview must not prepare live work")
            return WorkflowSpec(req.workflow, (StepSpec("prepare", (), "local", forbidden),),
                                metadata={"preview": {"planned": ["raw/documents/example.md"], "clickup": "describe-only"}})
        controller = Controller(factory, adapter_factory=lambda **kwargs: self.fail("No native preview"), skills_root=self.skills)
        before = list(self.project.iterdir())
        with patch.object(workflow, "_controller", return_value=controller):
            code, envelope = self.invoke(["run", "--request", str(request_path)])
        self.assertEqual(code, 0)
        self.assertEqual(envelope["status"], "preview")
        self.assertEqual(envelope["outputs"][0]["preview"]["clickup"], "describe-only")
        self.assertEqual(list(self.project.iterdir()), before)

    def test_registered_controller_runs_and_restores_through_public_api(self):
        restored = []
        def declare():
            def choose(context):
                if "continue" not in context.data["decisions"]:
                    return PreparedStep("blocked", decision={"key": "continue", "question": "Continue synthetic run?", "options": ["yes"]})
                return PreparedStep("local_result", local_result={"artifacts": [], "data": {"answer": context.data["decisions"]["continue"]}})
            return WorkflowSpec("wiki-ingest", (StepSpec("choose", (), "decision", choose),), metadata={"synthetic": True})
        def factory(request):
            return declare()
        def restore(request, *, snapshot):
            restored.append(snapshot["metadata"])
            return declare()
        with patch.object(workflow, "registered_factory", return_value=(factory, None, restore)):
            controller = workflow._controller(self.skills, "wiki-ingest")
        first = controller.run(RunRequest.from_dict(self.run_request()))
        self.assertEqual((first.status, first.decision["key"]), ("blocked", "continue"), first.error)
        final = controller.resume(self.project, first.run_id, {"key": "continue", "answer": "yes"})
        self.assertEqual(final.status, "done", final.error)
        self.assertEqual(restored, [{"synthetic": True}])

    def test_status_without_database_is_readonly_and_does_not_bootstrap(self):
        code, envelope = self.invoke(["status", "--project", str(self.project), "--run", "absent"])
        self.assertEqual(code, 1)
        self.assertEqual(envelope["error"]["code"], "DATABASE_UNAVAILABLE")
        self.assertEqual(list(self.project.iterdir()), [])

    def test_status_does_not_import_domain_or_advance_counters(self):
        store = Store(self.project)
        store.initialize()
        store.create_run("existing", "synthetic", "a" * 64, {}, [])
        with store.acquire_owner("existing"):
            store.create_step("existing", "waiting", "one", 0)
            store.record_decision("existing", "choose", {"key": "choose", "question": "Choose", "options": ["yes", "no"]})
            store.update_run("existing", "blocked")
        store.close()
        connection = sqlite3.connect(self.project / "state.db")
        before = connection.execute("SELECT COUNT(*) FROM wf_events").fetchone()[0]
        connection.close()
        with patch.object(workflow, "registered_factory", side_effect=AssertionError("status must not load domain")):
            code, envelope = self.invoke(["status", "--project", str(self.project), "--run", "existing"])
        self.assertEqual(code, 2)
        self.assertEqual(envelope["decision"]["key"], "choose")
        connection = sqlite3.connect(self.project / "state.db")
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM wf_events").fetchone()[0], before)
        connection.close()

    def test_cancel_is_independent_of_controller_owner_and_only_latches(self):
        store = Store(self.project)
        store.initialize()
        store.create_run("existing", "synthetic", "a" * 64, {}, [])
        with store.acquire_owner("existing"):
            code, envelope = self.invoke(["cancel", "--project", str(self.project), "--run", "existing"])
            self.assertTrue(store.is_cancelled("existing"))
            self.assertEqual(store.read_run("existing")["status"], "waiting")
        store.close()
        self.assertNotEqual(code, 0)
        self.assertEqual(envelope["error"]["code"], "CANCELLATION_REQUESTED")

    def machine(self, *, native=None):
        configuration = self.root / "runtime-config"
        runtime = self.root / "managed-bun"
        for relative in ("bin/bun.exe", "bin/omp.exe", "payload.js", "install/global/node_modules/@oh-my-pi/pi-coding-agent/package.json"):
            path = runtime / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{"name":"@oh-my-pi/pi-coding-agent"}' if path.suffix == ".json" else "synthetic-executable-not-run", encoding="utf-8")
        python = self.root / "python.exe"
        python.write_text("synthetic-python-not-run", encoding="utf-8")
        declaration = {"machine": "FixtureHost", "tokens": {"__USERPROFILE__": str(self.root), "__OMP_PYTHON__": str(python)},
                       "omp_managed": {"enabled": True, "install_root": str(runtime)}}
        if native is not None:
            declaration["workflow_native"] = native
        write_json(configuration / "runtime.json", declaration)
        write_json(configuration / "omp-install.json", {"schema": 1, "package": "@oh-my-pi/pi-coding-agent",
                   "executable_relative": "bin/omp.exe", "payload_relative": "payload.js", "package_tree_relative": "install/global/node_modules/@oh-my-pi/pi-coding-agent", "installer": {"runtime_relative": "bin/bun.exe"}})
        return configuration, runtime

    def test_managed_runtime_paths_come_from_local_manifest_not_environment(self):
        configuration, runtime = self.machine()
        req = RunRequest.from_dict(self.run_request(caller_host="claude", caller_family="claude"))
        with patch.dict("os.environ", {"HOME": "wrong-home", "USERPROFILE": "wrong-profile", "PATH": "not-used"}):
            config = workflow.resolve_native_configuration(req, self.skills, config_root=configuration, hostname="FixtureHost")
        self.assertEqual(config["omp"]["bun"], str(runtime / "bin/bun.exe"))
        self.assertEqual(set(config["omp"]["context"]), {"declaration_path", "install_manifest_path"})
        self.assertNotIn("codex", config)

    def test_native_metadata_rejects_certification_semantic_overrides_and_external_proxy(self):
        for metadata in ({"omp": {"instructions": "replace duties"}}, {"omp": {"capability_receipt_path": None}},
                         {"omp": {"qualification_receipt_path": None}},
                         {"claude": {"executable": str(Path(sys.executable)), "private_state_roots": [str(self.root / "private")], "proxy_url": "https://unapproved.example"}}):
            configuration, _ = self.machine(native=metadata)
            with self.subTest(metadata=metadata), self.assertRaises(ControllerError):
                workflow.resolve_native_configuration(None, self.skills, config_root=configuration, hostname="FixtureHost")

    def test_wrong_machine_account_or_manifest_escape_refuses_before_any_executable(self):
        configuration, _ = self.machine()
        with self.assertRaises(ControllerError):
            workflow.resolve_machine(config_root=configuration, hostname="DifferentHost")
        with patch.object(workflow, "_user_profile", return_value=self.root / "another-account"), self.assertRaises(ControllerError):
            workflow.resolve_machine(config_root=configuration, hostname="FixtureHost")
        path = configuration / "omp-install.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["installer"]["runtime_relative"] = "../escape.exe"
        write_json(path, value)
        with self.assertRaises(ControllerError):
            workflow.resolve_native_configuration(RunRequest.from_dict(self.run_request()), self.skills, config_root=configuration, hostname="FixtureHost")

    def test_default_configuration_is_installed_layout_local_and_account_bound(self):
        installed = self.root / ".agents/skills"
        installed.mkdir(parents=True)
        self.assertEqual(workflow._configuration_root(installed), self.root / ".agents/agent-skills")
        with self.assertRaises(ControllerError):
            workflow._configuration_root(self.skills)
        with patch.object(workflow, "_user_profile", return_value=self.root / "foreign"), self.assertRaises(ControllerError):
            workflow._configuration_root(installed)
        with self.assertRaises(ControllerError):
            workflow.resolve_machine()

    def test_declared_agent_directory_and_configs_are_bound_without_home_fallback(self):
        agent = self.root / "managed-profile/agent"
        agent.mkdir(parents=True)
        config_file = agent / "managed.config.yml"
        config_file.write_bytes(b"modelRoles: {}\n")
        configuration, _ = self.machine(native={"omp": {"agent_dir": str(agent), "config_files": [str(config_file)]}})
        with patch.dict("os.environ", {"HOME": "wrong-home", "USERPROFILE": "wrong-home"}):
            selected = workflow.resolve_native_configuration(RunRequest.from_dict(self.run_request(caller_host="claude", caller_family="claude")), self.skills,
                                                              config_root=configuration, hostname="FixtureHost")["omp"]
        self.assertEqual(selected["context"]["agent_dir"], str(agent))
        self.assertEqual(selected["context"]["config_files"], [str(config_file)])
        self.assertIn(workflow._input_ref(config_file), selected["source_refs"])
        for invalid in ({"agent_dir": "relative"}, {"agent_dir": str(config_file)},
                        {"config_files": str(config_file)}, {"config_files": []},
                        {"config_files": [str(config_file), str(config_file)]},
                        {"config_files": [str(agent / "absent.yml")]}, {"config_files": ["relative.yml"]}):
            configuration, _ = self.machine(native={"omp": invalid})
            with self.subTest(invalid=invalid), self.assertRaises(ControllerError):
                workflow.resolve_native_configuration(RunRequest.from_dict(self.run_request(caller_host="claude", caller_family="claude")), self.skills,
                                                      config_root=configuration, hostname="FixtureHost")

    def test_codex_task_flag_is_boolean_defaults_disabled_and_keeps_review_metadata(self):
        req = RunRequest.from_dict(self.run_request(caller_host="codex"))
        for fields in ({}, {"task_enabled": False}, {"task_enabled": True}):
            native = {"codex": {"executable": sys.executable, "private_state_roots": [str(self.root / "private")],
                                "windows_sandbox": "mxc", **fields}}
            configuration, _ = self.machine(native=native)
            config = workflow.resolve_native_configuration(req, self.skills, config_root=configuration, hostname="FixtureHost")
            self.assertIs(config["codex"]["context"]["task_enabled"], fields.get("task_enabled", False))
            self.assertEqual(config["codex"]["context"]["discriminator"]["request"], str(self.skills / "discriminator/request.py"))
        for value in ("false", 0, 1, None):
            configuration, _ = self.machine(native={"codex": {"executable": sys.executable,
                                     "private_state_roots": [str(self.root / "private")], "task_enabled": value}})
            with self.subTest(value=value), self.assertRaises(ControllerError):
                workflow.resolve_native_configuration(req, self.skills, config_root=configuration, hostname="FixtureHost")

    def test_selected_current_runtime_observations_change_without_a_version_allowlist(self):
        configuration, runtime = self.machine()
        request = RunRequest.from_dict(self.run_request(caller_host="claude", caller_family="claude"))
        original = workflow.resolve_native_configuration(request, self.skills, config_root=configuration, hostname="FixtureHost")
        selected = original["omp"]
        members = (runtime / "bin/bun.exe", runtime / "bin/omp.exe", runtime / "payload.js",
                   runtime / "install/global/node_modules/@oh-my-pi/pi-coding-agent/package.json")
        self.assertEqual({ref["path"] for ref in selected["source_refs"]},
                         {selected["context"]["declaration_path"], selected["context"]["install_manifest_path"], *map(str, members)})
        for member in members:
            previous = member.read_bytes()
            if member.name == "package.json":
                write_json(member, {"name": "@oh-my-pi/pi-coding-agent", "version": "synthetic-new-version"})
            else:
                member.write_bytes(b"different selected runtime bytes")
            with self.subTest(member=member), self.assertRaises(ControllerError):
                workflow._checked_refs(selected["source_refs"])
            current = workflow.resolve_native_configuration(request, self.skills, config_root=configuration, hostname="FixtureHost")
            self.assertNotEqual(current, original)
            self.assertEqual(workflow._checked_refs(current["omp"]["source_refs"]), current["omp"]["source_refs"])
            member.write_bytes(previous)

    def test_missing_or_foreign_selected_package_identity_refuses_static_resolution(self):
        configuration, runtime = self.machine()
        package = runtime / "install/global/node_modules/@oh-my-pi/pi-coding-agent/package.json"
        write_json(package, {"name": "@example/foreign-agent"})
        with self.assertRaises(ControllerError):
            workflow.resolve_native_configuration(None, self.skills, config_root=configuration, hostname="FixtureHost")
        package.unlink()
        with self.assertRaises(ControllerError):
            workflow.resolve_native_configuration(None, self.skills, config_root=configuration, hostname="FixtureHost")

    def test_flattened_cli_status_works_without_source_or_domain_checkout(self):
        installed = self.skills / "_workflow"
        source = Path(workflow.__file__).resolve().parent
        for name in ("__init__.py", "workflow.py", "controller.py", "store.py", "publication.py", "contracts.py"):
            shutil.copyfile(source / name, installed / name)
        completed = subprocess.run([sys.executable, "-I", "-S", "-B", str(installed / "workflow.py"),
                                    "status", "--project", str(self.project), "--run", "missing"],
                                   cwd=str(self.root), capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(json.loads(completed.stdout)["error"]["code"], "DATABASE_UNAVAILABLE")
        self.assertEqual(list(self.project.iterdir()), [])

    def test_direct_source_cli_is_not_an_installed_layout_fallback(self):
        completed = subprocess.run([sys.executable, "-I", "-S", "-B", str(ROOT / "_shared" / "workflow" / "src" / "workflow.py"),
                                    "status", "--project", str(self.project), "--run", "missing"],
                                   cwd=str(self.root), capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(json.loads(completed.stdout)["error"]["code"], "INVALID_RUNTIME_LAYOUT")
        self.assertEqual(list(self.project.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
