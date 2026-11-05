"""Static standalone declaration/source admission; no native process is executed."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from _shared.workflow.src.controller import ControllerError, RunRequest
from _shared.workflow.src import workflow

DISCRIMINATOR_FILES = (
    "SKILL.md", "agents/discriminator.toml", "assets/preamble_envelope.txt", "assets/preamble_finding.txt",
    "assets/rubric.lock.json", "assets/rubric.txt", "claude_reviewer.py", "contracts/code-fleet.md",
    "contracts/code-service.md", "contracts/configuration.md", "contracts/model-instruction.md",
    "contracts/pipeline-template.md", "contracts/plan-design.md", "contracts/project-state.md",
    "contracts/research-report.md", "contracts/vault-raw.md", "contracts/vault-wiki.md", "envelope.py",
    "envelope_schema.py", "family_attestation.py", "native_review.py", "packet.py", "request.py",
    "result.py", "supervisor.py", "telemetry.py", "transport.py")


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.skills = self.home / ".agents/skills"
        self.runtime = self.skills / "_workflow"
        self.config_root = self.home / ".agents/agent-skills"
        self.declaration = self.config_root / "runtime.json"
        self.manifest = self.config_root / "omp-install.json"
        self.python = self.home / "python.exe"
        self.native = self.home / "native"
        for path in (self.python, self.native / "claude.exe", self.native / "codex.exe", self.native / "bun.exe",
                     self.native / "omp.exe", self.native / "payload.js"):
            self.write(path, "synthetic executable never launched: " + path.name)
        self.json(self.native / "package/package.json", {"name": "@oh-my-pi/pi-coding-agent"})
        self.json(self.manifest, {"schema": 1, "package": "@oh-my-pi/pi-coding-agent", "package_tree_relative": "package",
            "executable_relative": "omp.exe", "payload_relative": "payload.js", "installer": {"runtime_relative": "bun.exe"}})
        for name in ("controller.py", "workflow.py", "contracts.py", "store.py", "publication.py",
                     "harness/omp/adapter.ts", "harness/claude/adapter.py", "harness/codex/adapter.py"):
            self.write(self.runtime / name, "synthetic source never loaded: " + name)
        for name in DISCRIMINATOR_FILES:
            self.write(self.skills / "discriminator" / name, "synthetic resource: " + name)
        self.extension = self.home / ".omp/shared/extensions/discriminator/index.ts"
        self.write(self.extension, "synthetic canonical extension never loaded")
        self.machine = {"machine": "FixtureHost", "tokens": {"__USERPROFILE__": str(self.home), "__OMP_PYTHON__": str(self.python)},
            "omp_managed": {"enabled": True, "install_root": str(self.native)}, "workflow_native": {
                "omp": {}, "claude": {"executable": str(self.native / "claude.exe"), "private_state_roots": [str(self.home / "private-claude")]},
                "codex": {"executable": str(self.native / "codex.exe"), "private_state_roots": [str(self.home / "private-codex")], "task_enabled": False}}}
        self.request = RunRequest(1, "synthetic", self.home / "project", (), "claude", "claude", "claude", None)
        for target, value in (("_user_profile", self.home), ("platform.node", "FixtureHost")):
            observed = patch.object(workflow, target, return_value=value) if "." not in target else patch.object(workflow.platform, "node", return_value=value)
            observed.start()
            self.addCleanup(observed.stop)
        self.resolve()

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def json(self, path, value):
        self.write(path, json.dumps(value))

    def resolve(self, request=None):
        self.json(self.declaration, self.machine)
        self.config = workflow.resolve_native_configuration(request or self.request, self.skills)
        return self.config

    def admit(self, request=None):
        with patch("subprocess.Popen", side_effect=AssertionError("Static admission must not spawn")), patch("subprocess.run", side_effect=AssertionError("Static admission must not probe")):
            return workflow.preflight_native(request or self.request, self.skills, native_configuration=self.config)

    def test_exact_caller_backend_and_complete_source_closure_without_certification(self):
        value = self.admit()
        self.assertEqual(set(value), {"schema_version", "machine", "caller_host", "caller_family", "backend", "configuration", "task_backends", "input_refs", "unavailable"})
        self.assertEqual(value["task_backends"], ["claude"])
        self.assertEqual(set(value["configuration"]), {"claude"})
        paths = {ref["path"] for ref in value["input_refs"]}
        self.assertTrue({str(self.skills / "discriminator" / name) for name in DISCRIMINATOR_FILES} <= paths)
        self.assertTrue({str(self.python), str(self.native / "claude.exe"), str(self.runtime / "harness/codex/adapter.py")} <= paths)
        self.assertFalse(any("qualification" in path or "capability" in path for path in paths))

    def test_machine_profile_and_caller_family_must_match(self):
        for field in ("machine", "profile", "family"):
            with self.subTest(field=field):
                request = self.request
                if field == "machine":
                    value = deepcopy(self.machine); value["machine"] = "ForeignHost"; self.json(self.declaration, value)
                elif field == "profile":
                    value = deepcopy(self.machine); value["tokens"]["__USERPROFILE__"] = str(self.home / "foreign"); self.json(self.declaration, value)
                else:
                    request = replace(request, caller_family="gpt")
                with self.assertRaises(ControllerError):
                    self.admit(request)
                self.resolve()

    def test_closed_refs_reject_duplicates_unknown_fields_missing_files_and_tamper(self):
        self.config = self.admit()["configuration"]
        original = deepcopy(self.config)
        for change in ("duplicate", "extra", "hash", "missing"):
            self.config = deepcopy(original)
            refs = self.config["claude"]["source_refs"]
            if change == "duplicate":
                refs.append(deepcopy(refs[0]))
            elif change == "extra":
                refs[0]["untrusted"] = True
            elif change == "hash":
                refs[0]["sha256"] = "0" * 64
            else:
                refs[0]["path"] = str(self.home / "missing.json")
            with self.subTest(change=change), self.assertRaises(ControllerError):
                self.admit()
        self.config = original
        for path in (self.runtime / "store.py", self.runtime / "harness/claude/adapter.py", self.native / "claude.exe",
                     self.python, self.skills / "discriminator/contracts/vault-wiki.md"):
            previous = path.read_bytes(); path.write_bytes(previous + b"changed")
            with self.subTest(path=path), self.assertRaises(ControllerError):
                self.admit()
            path.write_bytes(previous)

    def test_discriminator_population_is_complete_independent_of_cli_harness(self):
        member = self.skills / "discriminator/contracts/research-report.md"
        (self.skills / "discriminator/retained-user-notes.txt").write_bytes(b"not an owned substitute")
        previous = member.read_bytes(); member.unlink()
        with self.assertRaises(ControllerError):
            self.admit()
        member.write_bytes(previous)

    def test_unmanaged_notes_tests_and_caches_are_preserved_outside_both_cli_closures(self):
        requests = [replace(self.request, caller_host=host, caller_family=family)
                    for host, family in (("claude", "claude"), ("codex", "gpt"))]
        baseline = [self.admit(request)["input_refs"] for request in requests]
        neighbors = {self.skills / "discriminator" / name: b"retained unmanaged bytes: " + name.encode()
                     for name in ("retained-user-notes.txt", "__pycache__/user-cache.pyc", "tests/user-fixture.py")}
        for path, content in neighbors.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        for request, sources in zip(requests, baseline):
            with self.subTest(caller=request.caller_host):
                admitted = self.admit(request)
                self.assertEqual(admitted["input_refs"], sources)
                self.assertTrue(set(map(str, neighbors)).isdisjoint(ref["path"] for ref in admitted["input_refs"]))
        self.assertEqual({path: path.read_bytes() for path in neighbors}, neighbors)

    def test_link_at_owned_discriminator_path_refuses(self):
        owned = self.skills / "discriminator/native_review.py"
        outside = self.home / "not-owned-native-review.py"
        outside.write_bytes(owned.read_bytes())
        owned.unlink()
        try:
            owned.symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Symlink creation unavailable: {exc}")
        with self.assertRaises(ControllerError):
            self.admit()

    def test_certification_keys_are_rejected_even_when_null(self):
        for route in ("omp", "claude", "codex"):
            for field in ("capability_receipt_path", "qualification_receipt_path"):
                value = deepcopy(self.machine)
                value["workflow_native"][route][field] = None
                self.json(self.declaration, value)
                with self.subTest(route=route, field=field), self.assertRaises(ControllerError):
                    workflow.resolve_native_configuration(None, self.skills)
        self.resolve()
        self.config["claude"]["qualification_receipt_path"] = None
        with self.assertRaises(ControllerError):
            self.admit()

    def test_codex_task_unavailable_does_not_disable_caller_review_route(self):
        value = self.admit(replace(self.request, caller_host="codex", caller_family="gpt"))
        self.assertEqual(set(value["configuration"]), {"claude", "codex"})
        self.assertFalse(value["configuration"]["codex"]["context"]["task_enabled"])
        self.assertNotIn("codex", value["task_backends"])
        self.machine["workflow_native"]["codex"]["task_enabled"] = True
        self.resolve()
        enabled = self.admit(replace(self.request, caller_host="codex", caller_family="gpt"))
        self.assertTrue(enabled["configuration"]["codex"]["context"]["task_enabled"])
        self.assertNotIn("codex", enabled["task_backends"])
        with self.assertRaises(ControllerError) as caught:
            self.admit(replace(self.request, backend="codex"))
        self.assertEqual(caught.exception.code, "NATIVE_BACKEND_UNAVAILABLE")

    def test_auto_selects_omp_and_invalid_optional_claude_is_unavailable_not_fatal(self):
        request = replace(self.request, caller_host="omp", caller_family="gpt", backend="auto")
        self.machine["workflow_native"]["claude"]["executable"] = str(self.home / "missing-claude.exe")
        self.resolve(request)
        value = self.admit(request)
        self.assertEqual(value["task_backends"], ["omp"])
        self.assertEqual(set(value["configuration"]), {"omp"})
        self.assertEqual(value["unavailable"]["claude"]["status"], "unavailable")
        with self.assertRaises(ControllerError):
            self.admit(replace(request, caller_host="claude", caller_family="claude"))
        explicit = replace(request, backend="omp")
        self.resolve(explicit)
        self.assertEqual(self.admit(explicit)["task_backends"], ["omp"])

    def test_default_omp_caller_binds_canonical_review_sources_without_persisting_hash(self):
        request = replace(self.request, caller_host="omp", caller_family="gpt", backend="omp")
        self.resolve(request)
        before = self.declaration.read_bytes()
        admitted = self.admit(request)
        route = admitted["configuration"]["omp"]
        self.assertEqual(route["context"]["review_extension"], workflow._input_ref(self.extension))
        self.assertEqual(self.machine["workflow_native"]["omp"], {})
        self.assertEqual(self.declaration.read_bytes(), before)
        expected = {str(self.skills / "discriminator" / name) for name in DISCRIMINATOR_FILES}
        expected.update((str(self.extension), str(self.python)))
        self.assertTrue(expected <= {ref["path"] for ref in route["source_refs"]})
        retained = deepcopy(admitted["configuration"])
        for path in (self.extension, self.skills / "discriminator/native_review.py"):
            previous = path.read_bytes()
            path.write_bytes(previous + b"same-path default review source change")
            self.config = deepcopy(retained)
            with self.subTest(path=path), self.assertRaises(ControllerError):
                self.admit(request)
            self.resolve(request)
            fresh = self.admit(request)
            current = {ref["path"]: ref for ref in fresh["configuration"]["omp"]["source_refs"]}
            self.assertEqual(current[str(path)], workflow._input_ref(path))
            self.assertEqual(self.declaration.read_bytes(), before)
            path.write_bytes(previous)

    def test_explicit_review_pin_never_silently_refreshes_after_extension_change(self):
        request = replace(self.request, caller_host="omp", caller_family="gpt", backend="omp")
        self.machine["workflow_native"]["omp"]["review_extension"] = workflow._input_ref(self.extension)
        self.resolve(request)
        self.extension.write_bytes(self.extension.read_bytes() + b"changed explicit pin")
        with self.assertRaises(ControllerError):
            self.resolve(request)

    def test_omp_task_for_cli_caller_does_not_require_unselected_omp_review_installation(self):
        self.extension.unlink()
        request = replace(self.request, backend="omp")
        self.resolve(request)
        admitted = self.admit(request)
        self.assertNotIn("review_extension", admitted["configuration"]["omp"]["context"])
        with self.assertRaises(ControllerError):
            self.admit(replace(request, caller_host="omp", caller_family="gpt"))

    def test_model_including_absence_settings_and_bun_route_are_exact(self):
        request = replace(self.request, caller_host="omp", caller_family="gpt", backend="omp")
        agent = self.home / "agent"; agent.mkdir()
        settings = agent / "settings.yml"; settings.write_bytes(b"modelRoles: {}\n")
        self.machine["workflow_native"]["omp"] = {"model": "fixture-provider/exact-model", "agent_dir": str(agent), "config_files": [str(settings)]}
        self.resolve(request)
        self.config = self.admit(request)["configuration"]
        original = deepcopy(self.config)
        for change in ("model-missing", "model-changed", "agent", "settings", "bun"):
            self.config = deepcopy(original)
            context = self.config["omp"]["context"]
            if change == "model-missing": context.pop("model")
            elif change == "model-changed": context["model"] = "fixture-provider/another-model"
            elif change == "agent": context["agent_dir"] = str(self.home)
            elif change == "settings": context["config_files"] = []
            else: self.config["omp"]["bun"] = str(self.python)
            with self.subTest(change=change), self.assertRaises(ControllerError):
                self.admit(request)
        self.config = original
        settings.write_bytes(b"changed settings\n")
        with self.assertRaises(ControllerError): self.admit(request)

    def test_selected_current_runtime_refs_detect_same_path_tamper_and_allow_fresh_upgrade_admission(self):
        request = replace(self.request, caller_host="omp", caller_family="gpt", backend="omp")
        self.resolve(request)
        retained = self.admit(request)["configuration"]
        paths = (self.native / "bun.exe", self.native / "omp.exe", self.native / "payload.js", self.native / "package/package.json")
        bound = {ref["path"]: ref for ref in retained["omp"]["source_refs"]}
        self.assertTrue(set(map(str, paths)) <= set(bound))
        for path in paths:
            previous = path.read_bytes()
            changed = (json.dumps({"name": "@oh-my-pi/pi-coding-agent", "version": "synthetic-new-version"}).encode()
                       if path.name == "package.json" else previous + b"same-path runtime update")
            path.write_bytes(changed)
            self.config = deepcopy(retained)
            with self.subTest(path=path), self.assertRaises(ControllerError):
                self.admit(request)
            self.resolve(request)
            current = self.admit(request)
            observed = {ref["path"]: ref for ref in current["configuration"]["omp"]["source_refs"]}
            self.assertEqual(observed[str(path)], workflow._input_ref(path))
            self.assertNotEqual(observed[str(path)]["sha256"], bound[str(path)]["sha256"])
            path.write_bytes(previous)
        self.config = deepcopy(retained)

    def test_absent_model_cannot_be_added_only_to_execution_context(self):
        request = replace(self.request, caller_host="omp", caller_family="gpt", backend="omp")
        self.config["omp"]["context"]["model"] = "fixture-provider/exact-model"
        with self.assertRaises(ControllerError):
            self.admit(request)


if __name__ == "__main__":
    unittest.main()
