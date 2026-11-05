"""Finite controls CLI regressions on invented, isolated vaults only."""
import base64
from contextlib import closing, redirect_stdout
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if "_workflow" not in sys.modules:
    package = types.ModuleType("_workflow")
    package.__path__ = [str(ROOT / "_shared/workflow/src")]
    sys.modules["_workflow"] = package

from _workflow.publication import PublicationManager
from _workflow.store import Store
from wiki.runtime import vault_controls as controls
from wiki.runtime.schema import SchemaError, resolve_schema


class DurableControlTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.vault = self.home / "vault"
        self.vault.mkdir()
        self.project = self.home / "private-project"
        self.schema_bytes = (b'---\r\nwiki_name: SyntheticLib\r\nversion: "1.3"\r\n'
                             b'extends: canonical-schema.md\r\nextends_version: "3.0"\r\n'
                             b'hub_file: _hub.md\r\nindex_file: index.md\r\n'
                             b'domains: [synthetic]\r\ncanonical_files:\r\n  glossary: Glossary.md\r\n'
                             b'---\r\nHuman schema prose must survive.\r\n')
        (self.vault / ".wiki-schema.md").write_bytes(self.schema_bytes)
        (self.vault / "Glossary.md").write_bytes(b"Custom reference body\n")
        (self.vault / "_hub.md").write_bytes(b"---\nhuman_edited: true\n---\nCurated hub\n")
        (self.vault / "index.md").write_bytes(b"Manual catalog\r\n")
        (self.vault / "raw/documents").mkdir(parents=True)
        (self.vault / "raw/documents/source.md").write_bytes(
            b"---\ntype: source\nsource_sha256: authority-value\n---\nAlpha requires condition Q and costs 20.\n")
        (self.vault / "raw/documents/image.bin").write_bytes(b"invented-binary")
        (self.vault / "raw/_manifest.json").write_text(json.dumps({"last_verified_at": "2001-02-03", "opaque": [1, 2]}), encoding="utf-8")
        self.request = {"schema_version": 1, "selection_key": "SyntheticSelection", "vault_root": str(self.vault),
                        "project_root": str(self.project), "overrides": {}}
        self.request_path = self.home / "request.json"
        self.request_path.write_text(json.dumps(self.request), encoding="utf-8")
        self.plan_path = self.home / "plan.json"

    def invoke(self, *arguments):
        output = io.StringIO()
        with redirect_stdout(output):
            code = controls.main(list(arguments))
        return code, json.loads(output.getvalue())

    def plan(self):
        code, _ = self.invoke("plan", "--request", str(self.request_path), "--out", str(self.plan_path))
        self.assertEqual(0, code)
        return controls.load_control_plan(self.plan_path)

    def apply(self):
        return self.invoke("apply", "--plan", str(self.plan_path), "--receipt", str(self.home / "apply.json"))

    def assert_refuses(self, envelope, code=None):
        with self.assertRaises(SchemaError) as caught:
            controls.hydrate_control_plan(envelope)
        if code is not None:
            self.assertEqual(code, caught.exception.code)

    def rewrite_candidate(self, envelope, role, transform):
        change = next(row for row in envelope["plan"]["changes"] if row["role"] == role)
        data = transform(base64.b64decode(change["content_base64"]))
        change["content_base64"] = base64.b64encode(data).decode("ascii")
        change["desired_sha256"] = sha256(data).hexdigest()

    def test_plan_collection_allocates_nothing_and_conflicting_request_refuses(self):
        self.plan()
        before = self.plan_path.read_bytes()
        self.assertFalse((self.project / "state.db").exists())
        with patch.object(controls.uuid, "uuid4", side_effect=AssertionError("must collect before identity allocation")):
            self.assertEqual(0, self.invoke("plan", "--request", str(self.request_path), "--out", str(self.plan_path))[0])
        self.assertEqual(before, self.plan_path.read_bytes())
        self.request["overrides"] = {"outbound_archiving": False}
        self.request_path.write_text(json.dumps(self.request), encoding="utf-8")
        code, result = self.invoke("plan", "--request", str(self.request_path), "--out", str(self.plan_path))
        self.assertEqual(2, code)
        self.assertEqual("CONTROL_PLAN_CONFLICT", result["error"]["code"])
        self.assertEqual(before, self.plan_path.read_bytes())
        self.assertFalse((self.project / "state.db").exists())

    def test_desired_hash_cannot_authorize_outside_bytes_or_false_verification(self):
        envelope, _ = self.plan()
        self.rewrite_candidate(envelope, "index", lambda value: b"Changed human body\n" + value)
        self.assert_refuses(envelope, "CONTROL_DELTA_UNAUTHORIZED")
        envelope, _ = controls.load_control_plan(self.plan_path)
        self.rewrite_candidate(envelope, "manifest", lambda value: value.replace(b"2001-02-03", b"2099-12-31"))
        self.assert_refuses(envelope, "CONTROL_DELTA_UNAUTHORIZED")
        envelope, _ = controls.load_control_plan(self.plan_path)
        self.rewrite_candidate(envelope, "schema", lambda value: value.replace(b"outbound_archiving: true", b"outbound_archiving: false"))
        self.assert_refuses(envelope, "CONTROL_DELTA_UNAUTHORIZED")

    def test_runtime_root_evidence_and_role_tampering_refuse(self):
        for mutation in (lambda value: value["runtime_refs"][0].update(sha256="a" * 64),
                         lambda value: value["root_identity"].update(inode=value["root_identity"]["inode"] + 1),
                         lambda value: value["plan"]["changes"][0].update(path="raw/documents/source.md"),
                         lambda value: value["plan"]["guards"].pop(),
                         lambda value: value.update(operation_id="controls:another-run:adopt")):
            envelope, _ = self.plan() if not self.plan_path.exists() else controls.load_control_plan(self.plan_path)
            mutation(envelope)
            self.assert_refuses(envelope)
        envelope, _ = controls.load_control_plan(self.plan_path)
        ref = next(row["ref"] for row in envelope["preimage_refs"] if row["ref"] is not None)
        Path(ref["path"]).write_bytes(b"corrupted backup")
        self.assert_refuses(envelope, "CONTROL_PREIMAGE_CONFLICT")

    def test_partial_schema_publication_resumes_same_effect_ids(self):
        envelope, plan = self.plan()
        store = Store(self.project)
        store.initialize()
        try:
            store.create_run(envelope["run_id"], "wiki-vault-controls", sha256(controls._json_bytes(envelope)).hexdigest(),
                             envelope["request"], envelope, run_kind="inline")
            first = plan.changes[0]
            key = sha256(first.path.relative_to(self.vault).as_posix().encode()).hexdigest()
            candidate = self.project / "docs/tmp/pipeline-runs" / envelope["run_id"] / "control-candidates" / (key + ".candidate")
            candidate.parent.mkdir(parents=True)
            candidate.write_bytes(first.content)
            with store.acquire_owner(envelope["run_id"]):
                manager = PublicationManager(store)
                manager._intent(envelope["run_id"], None, "replace", candidate, first.path,
                                first.expected_sha256, sha256(first.content).hexdigest(),
                                envelope["operation_id"] + ":" + key, self.vault)
                # Simulate process loss after replacement but before effect ACK.
                first.path.write_bytes(first.content)
        finally:
            store.close()
        self.assertEqual(0, self.apply()[0])
        with closing(sqlite3.connect(self.project / "state.db")) as connection:
            ids = connection.execute("SELECT operation_id FROM wf_publications ORDER BY operation_id").fetchall()
            self.assertEqual(len(plan.changes), len(ids))
            self.assertEqual("done", connection.execute("SELECT status FROM wf_runs").fetchone()[0])
        self.assertEqual(0, self.invoke("apply", "--plan", str(self.plan_path), "--receipt", str(self.home / "collected.json"))[0])
        with closing(sqlite3.connect(self.project / "state.db")) as connection:
            self.assertEqual(ids, connection.execute("SELECT operation_id FROM wf_publications ORDER BY operation_id").fetchall())
        self.assertTrue((self.vault / ".wiki-schema.md").read_bytes().startswith(self.schema_bytes))
        self.assertEqual(0, self.invoke("verify", "--plan", str(self.plan_path), "--receipt", str(self.home / "verify.json"))[0])
        second = self.home / "second-plan.json"
        self.assertEqual(0, self.invoke("plan", "--request", str(self.request_path), "--out", str(second))[0])
        self.assertEqual([], json.loads(second.read_bytes())["plan"]["changes"])

    def test_matching_desired_hash_with_wrong_journal_candidate_refuses(self):
        envelope, plan = self.plan()
        store = Store(self.project)
        store.initialize()
        try:
            store.create_run(envelope["run_id"], "wiki-vault-controls", sha256(controls._json_bytes(envelope)).hexdigest(),
                             envelope["request"], envelope, run_kind="inline")
            first = plan.changes[0]
            candidate = self.project / "not-the-bound-candidate.bin"
            candidate.write_bytes(first.content)
            key = sha256(first.path.relative_to(self.vault).as_posix().encode()).hexdigest()
            with store.acquire_owner(envelope["run_id"]):
                PublicationManager(store).publish_file(envelope["run_id"], None, candidate, first.path,
                                                       first.expected_sha256, envelope["operation_id"] + ":" + key, root=self.vault)
        finally:
            store.close()
        code, result = self.apply()
        self.assertEqual(2, code)
        self.assertEqual("CONTROL_CANDIDATE_CONFLICT", result["error"]["code"])
        self.assertEqual(b"Manual catalog\r\n", (self.vault / "index.md").read_bytes())

    def test_desired_bytes_without_original_journal_are_not_recovery_authority(self):
        _, plan = self.plan()
        first = plan.changes[0]
        first.path.write_bytes(first.content)
        code, result = self.apply()
        self.assertEqual(2, code)
        self.assertEqual("CONTROL_PREIMAGE_CONFLICT", result["error"]["code"])
        with closing(sqlite3.connect(self.project / "state.db")) as connection:
            self.assertEqual("blocked", connection.execute("SELECT status FROM wf_runs").fetchone()[0])
            self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM wf_publications").fetchone()[0])

    def test_reference_and_custom_metadata_changes_block_without_first_effect(self):
        self.plan()
        (self.vault / "Glossary.md").write_bytes(b"New human glossary\n")
        code, result = self.apply()
        self.assertEqual(2, code)
        self.assertEqual("CONTROL_PREIMAGE_CONFLICT", result["error"]["code"])
        self.assertEqual(self.schema_bytes, (self.vault / ".wiki-schema.md").read_bytes())

    def test_verify_failure_is_read_only_and_does_not_create_database(self):
        self.plan()
        before = {path: path.read_bytes() for path in self.vault.rglob("*") if path.is_file()}
        code, result = self.invoke("verify", "--plan", str(self.plan_path), "--receipt", str(self.home / "negative.verify.json"))
        self.assertEqual(2, code)
        self.assertEqual("CONTROL_VERIFICATION_FAILED", result["error"]["code"])
        self.assertFalse((self.project / "state.db").exists())
        self.assertEqual(before, {path: path.read_bytes() for path in self.vault.rglob("*") if path.is_file()})

    def test_immutable_failure_receipt_collects_only_and_new_receipt_can_resume(self):
        self.plan()
        original = (self.vault / "index.md").read_bytes()
        (self.vault / "index.md").write_bytes(b"Concurrent edit\n")
        code, _ = self.apply()
        self.assertEqual(2, code)
        receipt = (self.home / "apply.json").read_bytes()
        (self.vault / "index.md").write_bytes(original)
        self.assertEqual(2, self.apply()[0])
        self.assertEqual(receipt, (self.home / "apply.json").read_bytes())
        self.assertEqual(0, self.invoke("apply", "--plan", str(self.plan_path), "--receipt", str(self.home / "resumed.apply.json"))[0])
        self.assertEqual(receipt, (self.home / "apply.json").read_bytes())
        with closing(sqlite3.connect(self.project / "state.db")) as connection:
            self.assertEqual(1, connection.execute("SELECT COUNT(*) FROM wf_runs").fetchone()[0])

    def test_custom_file_metadata_and_forged_protected_skip_refuse(self):
        custom = self.vault / "Human-Custom.md"
        custom.write_bytes(b"Private custom body\n")
        envelope, _ = self.plan()
        envelope["plan"]["receipts"].append({"path": "index.md", "role": "index", "status": "skipped-protected"})
        self.assert_refuses(envelope)
        custom.write_bytes(b"Changed private body\n")
        code, result = self.apply()
        self.assertEqual(2, code)
        self.assertEqual("CONTROL_PRESERVATION_CONFLICT", result["error"]["code"])
        self.assertEqual(self.schema_bytes, (self.vault / ".wiki-schema.md").read_bytes())

    def test_successor_retains_predecessor_and_only_remaining_effects(self):
        self.plan()
        self.assertEqual(0, self.apply()[0])
        original_plan = self.plan_path.read_bytes()
        successor = self.home / "successor.json"
        self.assertEqual(0, self.invoke("plan", "--request", str(self.request_path), "--out", str(successor),
                                       "--predecessor-plan", str(self.plan_path))[0])
        value = json.loads(successor.read_bytes())
        self.assertEqual([], value["plan"]["changes"])
        self.assertEqual({"path": str(self.plan_path), "sha256": sha256(original_plan).hexdigest()}, value["predecessor_plan_ref"])
        self.assertEqual(original_plan, self.plan_path.read_bytes())

    def test_inline_initialization_hydrates_original_request_without_a_nested_run(self):
        root = self.home / "empty-vault"
        root.mkdir()
        plan = controls.prepare_initialization(root, "NewSynthetic", ("synthetic",))
        request = {"schema_version": 1, "workflow": "wiki-ingest", "project_root": str(self.project),
                   "arguments": ["--init", "--vault", str(root), "--wiki-name", "NewSynthetic", "--domain", "synthetic"],
                   "caller_host": "omp", "caller_family": "gpt", "backend": "auto", "profile": None}
        envelope = controls.serialize_control_plan(plan, request, controls.control_runtime_refs(), "inline-initialization")
        envelope["operation_id"] = "controls:inline-initialization:L0.initialize"
        self.assertEqual(plan, controls.hydrate_control_plan(envelope))
        self.assertFalse((self.project / "state.db").exists())
        self.rewrite_candidate(envelope, "schema", lambda value: value + b"Invented body outside controls.\n")
        self.assert_refuses(envelope, "CONTROL_DELTA_UNAUTHORIZED")

    def test_flattened_three_process_cli_protects_custom_and_raw_bytes(self):
        execution = self.home / "installed-skills"
        members = {"wiki/__init__.py": "wiki/__init__.py",
                   "wiki/runtime/__init__.py": "wiki/runtime/__init__.py",
                   "wiki/runtime/schema.py": "wiki/runtime/schema.py",
                   "wiki/runtime/vault_controls.py": "wiki/runtime/vault_controls.py",
                   "wiki/canonical-schema.md": "wiki/canonical-schema.md",
                   "wiki/_archive/canonical-schema-v3.0.md": "wiki/_archive/canonical-schema-v3.0.md"}
        for source, target in members.items():
            destination = execution / target
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / source, destination)
        core = Path(sys.modules[Store.__module__].__file__).parent
        for name in ("__init__.py", "contracts.py", "store.py", "publication.py"):
            destination = execution / "_workflow" / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(core / name, destination)
        environment = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR") if key in os.environ}
        for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "TMPDIR"):
            value = self.home / ("synthetic-" + key.lower())
            value.mkdir()
            environment[key] = str(value)
        cli = execution / "wiki/runtime/vault_controls.py"
        original = {relative: (self.vault / relative).read_bytes() for relative in
                    ("Glossary.md", "_hub.md", "raw/documents/source.md", "raw/documents/image.bin")}
        def command(*arguments):
            completed = subprocess.run([sys.executable, "-I", "-B", str(cli), *arguments], cwd=self.home,
                                       env=environment, capture_output=True, text=True, timeout=30)
            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
            return json.loads(completed.stdout)
        command("plan", "--request", str(self.request_path), "--out", str(self.plan_path))
        self.assertFalse((self.project / "state.db").exists())
        applied = command("apply", "--plan", str(self.plan_path), "--receipt", str(self.home / "external.apply.json"))
        self.assertEqual("verified_with_protected_skips", applied["status"])
        db_before = (self.project / "state.db").read_bytes()
        verified = command("verify", "--plan", str(self.plan_path), "--receipt", str(self.home / "external.verify.json"))
        self.assertEqual("verified_with_protected_skips", verified["status"])
        self.assertEqual(db_before, (self.project / "state.db").read_bytes())
        for relative, data in original.items():
            self.assertEqual(data, (self.vault / relative).read_bytes())
        manifest = json.loads((self.vault / "raw/_manifest.json").read_bytes())
        self.assertEqual("2001-02-03", manifest["last_verified_at"])
        self.assertEqual(2, len(manifest["wiki_workflow"]["inventory"]))
        second = self.home / "external.second.json"
        command("plan", "--request", str(self.request_path), "--out", str(second))
        self.assertEqual([], json.loads(second.read_bytes())["plan"]["changes"])


if __name__ == "__main__":
    unittest.main()
