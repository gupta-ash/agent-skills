"""Source-only deterministic export/admission tests; never native qualification."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from _shared.workflow.src import workflow
from _shared.workflow.src.controller import ControllerError


class ReviewExtensionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        entries = json.loads((ROOT / "deploy/exports.json").read_text(encoding="utf-8"))["entries"]
        selected = [entry for entry in entries if entry["source"].startswith("discriminator/")]
        self.assertEqual(len(selected), 29)
        for entry in selected:
            destination = self.root / Path(entry["destination"]).relative_to("harness")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / entry["source"], destination)
        self.extension = self.root / ".omp/shared/extensions/discriminator/index.ts"
        self.skills = self.root / ".agents/skills"
        self.owned_members = {str(self.root / Path(entry["destination"]).relative_to("harness"))
                              for entry in selected if Path(entry["destination"]).is_relative_to(
                                  Path("harness/.agents/skills/discriminator"))}
        self.ref = workflow._input_ref(self.extension)

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")

    def json(self, path, value):
        self.write(path, json.dumps(value))

    def test_exact_generated_population_and_optional_declaration(self):
        sources = workflow._omp_review_extension(self.ref)
        self.assertEqual(len(sources), 28)
        self.assertEqual({source["path"] for source in sources}, {str(self.extension), *self.owned_members})
        self.assertEqual(sources[0], {key: self.ref[key] for key in ("path", "sha256")})
        self.assertEqual(sources[1:], sorted(sources[1:], key=lambda item: item["path"]))
        self.assertEqual(workflow._omp_settings({}), {})
        self.assertEqual(workflow._omp_settings({"review_extension": self.ref}), {"review_extension": self.ref})
        self.assertIn(str(self.skills / "discriminator/native_review.py"), {item["path"] for item in sources})

    def test_hash_media_closed_relative_and_foreign_inputs_refuse(self):
        foreign = self.root / "foreign/index.ts"
        self.write(foreign, self.extension.read_text(encoding="utf-8"))
        for ref in (None, {}, {**self.ref, "extra": True}, {**self.ref, "sha256": "0" * 64},
                    {**self.ref, "media_type": "application/javascript"}, {**self.ref, "path": "index.ts"},
                    workflow._input_ref(foreign)):
            with self.subTest(ref=ref), self.assertRaises(ControllerError):
                workflow._omp_review_extension(ref)

    def test_missing_owned_helper_rubric_resource_and_nonregular_extension_refuse(self):
        self.write(self.skills / "discriminator/retained-user-notes.txt", "not an owned resource substitute")
        for member in ("native_review.py", "assets/rubric.lock.json", "contracts/code-service.md"):
            path = self.skills / "discriminator" / member
            data = path.read_bytes()
            path.unlink()
            with self.subTest(member=member), self.assertRaises(ControllerError):
                workflow._omp_review_extension(self.ref)
            path.write_bytes(data)
        self.extension.unlink()
        self.extension.mkdir()
        with self.assertRaises(ControllerError):
            workflow._omp_review_extension(self.ref)

    def test_unmanaged_neighbors_do_not_change_review_sources_or_selection(self):
        sources = workflow._omp_review_extension(self.ref)
        config, adapter = self.configuration(selected=False)
        selected_refs, fingerprint = workflow._omp_selection(config, adapter)
        neighbors = {self.skills / "discriminator" / name: b"retained unmanaged bytes: " + name.encode()
                     for name in ("retained-user-notes.txt", "__pycache__/user-cache.pyc", "tests/user-fixture.py")}
        for path, content in neighbors.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        self.assertEqual(workflow._omp_review_extension(self.ref), sources)
        self.assertEqual(workflow._omp_selection(config, adapter), (selected_refs, fingerprint))
        self.assertTrue(set(map(str, neighbors)).isdisjoint(ref["path"] for ref in selected_refs))
        self.assertEqual({path: path.read_bytes() for path in neighbors}, neighbors)

    def test_symlink_at_owned_resource_refuses_when_supported(self):
        link = self.skills / "discriminator/native_review.py"
        link.unlink()
        try:
            link.symlink_to(self.extension)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Symlink creation unavailable: {exc}")
        with self.assertRaises(ControllerError):
            workflow._omp_review_extension(self.ref)

    def test_link_at_owned_resource_parent_refuses_when_supported(self):
        assets = self.skills / "discriminator/assets"
        retained = self.root / "retained-assets"
        assets.rename(retained)
        try:
            assets.symlink_to(retained, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Directory symlink creation unavailable: {exc}")
        with self.assertRaises(ControllerError):
            workflow._omp_review_extension(self.ref)

    def configuration(self, selected=True, *, caller_host="omp"):
        native = self.root / "native"
        tree = native / "package"
        for name in ("bun.exe", "omp.exe", "payload.js"):
            self.write(native / name, "source-only inert native placeholder")
        tree.mkdir(parents=True)
        self.json(tree / "package.json", {"name": "@oh-my-pi/pi-coding-agent"})
        self.runtime_root = self.root / ".agents/agent-skills"
        self.declaration = self.runtime_root / "runtime.json"
        self.manifest = self.runtime_root / "omp-install.json"
        self.machine = {"machine": "SourceOnly", "tokens": {"__USERPROFILE__": str(self.root), "__OMP_PYTHON__": sys.executable},
                        "omp_managed": {"enabled": True, "install_root": str(native)}, "workflow_native": {"omp": {}}}
        if selected:
            self.machine["workflow_native"]["omp"]["review_extension"] = self.ref
        self.json(self.declaration, self.machine)
        self.json(self.manifest, {"schema": 1, "package": "@oh-my-pi/pi-coding-agent", "installer": {"runtime_relative": "bun.exe"},
                                  "executable_relative": "omp.exe", "payload_relative": "payload.js", "package_tree_relative": "package"})
        for name in ("controller.py", "workflow.py", "contracts.py", "store.py", "publication.py", "harness/omp/adapter.ts"):
            origin = ROOT / "_shared/workflow" / (name if name.startswith("harness/") else "src/" + name)
            destination = self.skills / "_workflow" / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(origin, destination)
        self.request = None if caller_host is None else workflow.RunRequest(
            1, "synthetic", self.root / "project", (), caller_host, "gpt" if caller_host != "claude" else "claude", "omp", None)
        with patch.object(workflow, "_user_profile", return_value=self.root):
            config = workflow.resolve_native_configuration(self.request, self.skills, hostname="SourceOnly")["omp"]
        adapter = ROOT / "_shared/workflow/harness/omp/adapter.ts"
        return config, adapter

    def test_resolver_and_selection_bind_export_and_entire_closure_without_receipts(self):
        config, adapter = self.configuration()
        expected = {item["path"] for item in workflow._omp_review_extension(self.ref)}
        native = Path(config["bun"]).parent
        expected.update(str(native / name) for name in ("bun.exe", "omp.exe", "payload.js", "package/package.json"))
        self.assertTrue(expected <= {item["path"] for item in config["source_refs"]})
        self.assertEqual(config["context"]["declaration_path"], str(self.declaration))
        self.assertEqual(config["context"]["install_manifest_path"], str(self.manifest))
        refs, fingerprint = workflow._omp_selection(config, adapter)
        self.assertTrue(expected <= {item["path"] for item in refs})
        self.assertEqual(fingerprint["review_extension"], self.ref)
        self.assertEqual(fingerprint["review_extension_sources"], workflow._omp_review_extension(self.ref))
        self.assertIsNone(fingerprint["model_hint"])
        self.assertEqual(workflow._checked_refs(config["source_refs"]), config["source_refs"])
        helper = self.skills / "discriminator/native_review.py"
        self.write(helper, helper.read_text(encoding="utf-8") + "\n# source-only stale closure\n")
        with self.assertRaises(ControllerError):
            workflow._checked_refs(config["source_refs"])

    def test_fresh_declaration_hash_does_not_hide_explicit_selection_mismatch(self):
        config, adapter = self.configuration()
        self.machine["workflow_native"]["omp"]["review_extension"] = {**self.ref, "sha256": "0" * 64}
        self.json(self.declaration, self.machine)
        config["source_refs"] = [workflow._input_ref(item["path"]) for item in config["source_refs"]]
        with self.assertRaises(ControllerError):
            workflow._omp_selection(config, adapter)
        with patch.object(workflow, "_user_profile", return_value=self.root), self.assertRaises(ControllerError):
            workflow.resolve_native_configuration(self.request, self.skills, hostname="SourceOnly")

    def test_explicit_extension_bytes_never_refresh_a_stale_declared_ref(self):
        config, adapter = self.configuration()
        declaration = self.declaration.read_bytes()
        self.extension.write_bytes(self.extension.read_bytes() + b"\nchanged explicitly declared extension\n")
        with patch.object(workflow, "_user_profile", return_value=self.root), self.assertRaises(ControllerError):
            workflow.resolve_native_configuration(self.request, self.skills, hostname="SourceOnly")
        with self.assertRaises(ControllerError):
            workflow._omp_selection(config, adapter)
        self.assertEqual(self.declaration.read_bytes(), declaration)
        self.assertEqual(config["context"]["review_extension"], self.ref)

    def test_certification_fields_are_rejected_not_loaded(self):
        config, adapter = self.configuration()
        for field in ("capability_receipt_path", "qualification_receipt_path"):
            machine = deepcopy(self.machine)
            machine["workflow_native"]["omp"][field] = str(self.root / "absent-certification.json")
            self.json(self.declaration, machine)
            with self.subTest(field=field), patch.object(workflow, "_user_profile", return_value=self.root):
                with self.assertRaises(ControllerError):
                    workflow.resolve_native_configuration(None, self.skills, hostname="SourceOnly")
                with self.assertRaises(ControllerError):
                    workflow._omp_selection(config, adapter)

    def test_default_caller_binds_canonical_closure_without_mutating_declaration(self):
        config, adapter = self.configuration(selected=False)
        declaration = self.declaration.read_bytes()
        self.assertEqual(self.machine["workflow_native"]["omp"], {})
        self.assertEqual(config["context"]["review_extension"], self.ref)
        expected = {str(self.extension), *self.owned_members, str(Path(sys.executable).resolve())}
        self.assertTrue(expected <= {item["path"] for item in config["source_refs"]})
        refs, fingerprint = workflow._omp_selection(config, adapter)
        self.assertTrue(expected <= {item["path"] for item in refs})
        self.assertEqual(fingerprint["review_extension"], self.ref)
        self.assertEqual(fingerprint["review_extension_sources"], workflow._omp_review_extension(self.ref))
        self.assertNotIn("model", config["context"])
        self.assertIsNone(fingerprint["model_hint"])
        self.assertEqual(self.declaration.read_bytes(), declaration)

    def test_task_only_configuration_can_omit_review_until_actual_omp_preflight(self):
        config, adapter = self.configuration(selected=False, caller_host=None)
        self.assertNotIn("review_extension", config["context"])
        self.assertNotIn(str(self.extension), {item["path"] for item in config["source_refs"]})
        refs, fingerprint = workflow._omp_selection(config, adapter)
        native = Path(config["bun"]).parent
        self.assertEqual(refs, [workflow._input_ref(native / name)
                                for name in ("bun.exe", "omp.exe", "payload.js", "package/package.json")])
        self.assertIsNone(fingerprint["review_extension"])
        request = workflow.RunRequest(1, "synthetic", self.root / "project", (), "omp", "gpt", "omp", None)
        declaration = self.declaration.read_bytes()
        with patch.object(workflow, "_user_profile", return_value=self.root), patch.object(workflow.platform, "node", return_value="SourceOnly"):
            admitted = workflow.preflight_native(request, self.skills, native_configuration={"omp": config})
        route = admitted["configuration"]["omp"]
        self.assertEqual(route["context"]["review_extension"], self.ref)
        self.assertNotIn("review_extension", config["context"])
        self.assertTrue({str(self.extension), *self.owned_members, str(Path(sys.executable).resolve())}
                        <= {item["path"] for item in route["source_refs"]})
        self.assertEqual(self.declaration.read_bytes(), declaration)

    def test_retained_default_extension_and_helper_tamper_cannot_refresh(self):
        config, adapter = self.configuration(selected=False)
        retained = deepcopy(config)
        for path in (self.extension, self.skills / "discriminator/native_review.py"):
            content = path.read_bytes()
            path.write_bytes(content + b"\nchanged after default caller admission\n")
            with self.subTest(path=path), self.assertRaises(ControllerError):
                workflow._checked_refs(config["source_refs"])
            with self.subTest(path=path), patch.object(workflow, "_user_profile", return_value=self.root), patch.object(workflow.platform, "node", return_value="SourceOnly"), self.assertRaises(ControllerError):
                workflow.preflight_native(self.request, self.skills, native_configuration={"omp": config})
            self.assertEqual(config, retained)
            path.write_bytes(content)

    def test_derived_context_cannot_select_another_profiles_extension(self):
        config, adapter = self.configuration(selected=False)
        foreign = self.root / "foreign-profile/.omp/shared/extensions/discriminator/index.ts"
        self.write(foreign, self.extension.read_text(encoding="utf-8"))
        shutil.copytree(self.skills / "discriminator", self.root / "foreign-profile/.agents/skills/discriminator")
        config["context"]["review_extension"] = workflow._input_ref(foreign)
        with self.assertRaises(ControllerError):
            workflow._omp_selection(config, adapter)


if __name__ == "__main__":
    unittest.main()
