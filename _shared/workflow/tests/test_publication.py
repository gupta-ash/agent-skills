"""Filesystem/SQLite crash-boundary tests; all inputs are invented temporary files."""
from hashlib import sha256
import errno
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from _shared.workflow.src import contracts as c
from _shared.workflow.src.publication import PublicationManager, PublicationError
from _shared.workflow.src.store import Store, StoreError


def digest(data):
    return sha256(data).hexdigest()


class Crash(BaseException):
    """A process death: deliberately bypass ordinary exception settlement."""


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name) / "project"
        self.root = Path(self.temporary.name) / "vault"
        self.root.mkdir()
        self.store = Store(self.project)
        self.store.initialize()
        self.store.create_run("run", "synthetic-control", "a" * 64, {}, [], run_kind="inline")
        self.owner = self.store.acquire_owner("run")
        self.owner.__enter__()
        self.manager = PublicationManager(self.store)
        self.addCleanup(self.close)
        self.candidate = self.project / "candidate.md"
        self.candidate.write_bytes(b"accepted synthetic bytes\n")
        self.target = self.root / "wiki" / "topic.md"

    def close(self):
        if self.owner is not None:
            self.owner.__exit__(None, None, None)
            self.owner = None
        self.store.close()

    def restart(self):
        self.close()
        self.store = Store(self.project)
        self.store.initialize()
        self.owner = self.store.acquire_owner("run")
        self.owner.__enter__()
        self.manager = PublicationManager(self.store)

    def publish(self, *, expected=None, operation="op", target=None, candidate=None, check=None):
        return self.manager.publish_file("run", None, candidate or self.candidate,
                                         target or self.target, expected, operation,
                                         root=self.root, protected_check=check)

    def row(self, operation="op"):
        return dict(self.store.connection.execute("SELECT * FROM wf_publications WHERE operation_id=?", (operation,)).fetchone())

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(StoreError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def test_expected_absence_and_duplicate_receipts_are_real_no_overwrite(self):
        receipt = self.publish()
        self.assertEqual(self.target.read_bytes(), self.candidate.read_bytes())
        self.assertEqual(receipt["status"], "applied")
        before = self.target.stat().st_mtime_ns
        self.candidate.unlink()
        self.assertEqual(self.publish(), receipt)
        self.assertEqual(self.target.stat().st_mtime_ns, before)
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM wf_publications").fetchone()[0], 1)
        self.assert_code("OPERATION_CONFLICT", self.publish, expected="b" * 64)
        self.target.write_bytes(b"new human body")
        self.assert_code("PREIMAGE_CONFLICT", self.manager.reconcile, "op", root=self.root)
        self.assertEqual(self.target.read_bytes(), b"new human body")

    def test_existing_target_requires_exact_preimage_and_conflict_never_rolls_back(self):
        self.target.parent.mkdir()
        self.target.write_bytes(b"prior")
        self.assert_code("PREIMAGE_CONFLICT", self.publish)
        self.assertEqual(self.target.read_bytes(), b"prior")
        self.assertEqual(self.row()["status"], "blocked")
        self.publish(expected=digest(b"prior"), operation="correct-preimage")
        self.assertEqual(self.target.read_bytes(), self.candidate.read_bytes())
        self.assertEqual(self.manager.reconcile("op", root=self.root)["classification"], "desired")
        self.assertEqual(self.target.read_bytes(), self.candidate.read_bytes())

    def test_fresh_expected_absence_refuses_even_identical_existing_bytes(self):
        self.target.parent.mkdir()
        self.target.write_bytes(self.candidate.read_bytes())
        self.assert_code("PREIMAGE_CONFLICT", self.publish)
        self.assertEqual(self.row()["status"], "blocked")

    def test_preexisting_hardlink_is_not_this_rename_operations_authority(self):
        source, target = self.root / "old.md", self.root / "archive.md"
        source.write_bytes(b"original")
        os.link(source, target)
        self.assert_code("RENAME_COLLISION", self.manager.rename_file, "run", None, source, target, digest(b"original"), "preexisting-link", root=self.root)
        self.assert_code("RENAME_COLLISION", self.manager.reconcile, "preexisting-link", root=self.root)
        self.assertTrue(source.exists())
        self.assertTrue(target.exists())

    def test_durable_intent_commits_before_replace_and_post_replace_crash_reconciles(self):
        real_replace = os.replace
        def replace_then_crash(source, target):
            self.assertFalse(self.store.connection.in_transaction)
            row = self.row()
            self.assertEqual(row["status"], "intent")
            self.assertIsNone(row["receipt_json"])
            self.assertEqual(row["expected_sha256"], None)
            real_replace(source, target)
            raise Crash()
        with patch("_shared.workflow.src.publication.os.replace", replace_then_crash):
            with self.assertRaises(Crash):
                self.publish()
        self.assertEqual(self.target.read_bytes(), self.candidate.read_bytes())
        self.assertEqual(self.row()["status"], "intent")
        self.candidate.unlink()
        self.restart()
        with patch("_shared.workflow.src.publication.os.replace", side_effect=AssertionError("desired effect must not replay")):
            receipt = self.manager.reconcile("op", root=self.root)
        self.assertEqual(receipt["classification"], "desired")
        self.assertEqual(self.row()["status"], "applied")

    def test_pre_replace_crash_recovers_prior_and_reuses_hash_bound_temporary(self):
        with patch("_shared.workflow.src.publication.os.replace", side_effect=Crash()):
            with self.assertRaises(Crash):
                self.publish()
        self.assertFalse(self.target.exists())
        self.assertEqual(len(list(self.target.parent.glob("*.tmp"))), 1)
        self.restart()
        receipt = self.manager.reconcile("op", root=self.root)
        self.assertEqual(receipt["status"], "applied")
        self.assertEqual(self.target.read_bytes(), b"accepted synthetic bytes\n")
        self.assertEqual(list(self.target.parent.glob("*.tmp")), [])

    def test_candidate_change_after_intent_blocks_recovery_without_touching_prior(self):
        with patch("_shared.workflow.src.publication.os.replace", side_effect=Crash()):
            with self.assertRaises(Crash):
                self.publish()
        self.candidate.write_bytes(b"unreviewed revision")
        self.restart()
        self.assert_code("CANDIDATE_CONFLICT", self.manager.reconcile, "op", root=self.root)
        self.assertFalse(self.target.exists())
        self.assertEqual(self.row()["status"], "blocked")

    def test_changed_human_target_after_intent_is_preserved_on_recovery(self):
        with patch("_shared.workflow.src.publication.os.replace", side_effect=Crash()):
            with self.assertRaises(Crash):
                self.publish()
        self.target.write_bytes(b"human race")
        self.restart()
        self.assert_code("PREIMAGE_CONFLICT", self.manager.reconcile, "op", root=self.root)
        self.assertEqual(self.target.read_bytes(), b"human race")

    def test_protection_is_reread_immediately_before_replacement(self):
        self.target.parent.mkdir()
        self.target.write_bytes(b"prior")
        calls = []
        def edit_during_checks(path):
            calls.append(path)
            if len(calls) == 2:
                path.write_bytes(b"---\nhuman_edited: true\n---\nHuman work\n")
            return False
        self.assert_code("PREIMAGE_CONFLICT", self.publish, expected=digest(b"prior"), check=edit_during_checks)
        self.assertIn(b"Human work", self.target.read_bytes())
        self.assertEqual(self.row()["status"], "blocked")

    def test_frontmatter_protection_including_bom_quotes_and_flow_mapping(self):
        self.target.parent.mkdir()
        for number, prior in enumerate((b"---\nhuman_edited: true\n---\nhuman", b"---\npipeline_id: hand-curated\n---\nhuman", b'\xef\xbb\xbf---\n"human_edited": true\n---\nhuman', b'---\n{human_edited: true, title: Human}\n---\nhuman')):
            with self.subTest(number=number):
                self.target.write_bytes(prior)
                self.assert_code("PROTECTED_TARGET", self.publish, expected=digest(prior), operation=f"protected-{number}")
                self.assertEqual(self.target.read_bytes(), prior)

    def test_cancellation_blocks_prior_effect_but_can_record_already_desired(self):
        with patch("_shared.workflow.src.publication.os.replace", side_effect=Crash()):
            with self.assertRaises(Crash):
                self.publish()
        self.store.request_cancel("run")
        self.assert_code("PUBLICATION_NOT_AUTHORIZED", self.manager.reconcile, "op", root=self.root)
        self.assertFalse(self.target.exists())
        # Observe an already-completed effect without replay, including after cancel.
        self.target.write_bytes(self.candidate.read_bytes())
        receipt = self.manager.reconcile("op", root=self.root)
        self.assertEqual(receipt["status"], "applied")
        self.assertTrue(self.store.is_cancelled("run"))

    def test_root_traversal_candidate_containment_and_special_files_refuse(self):
        self.assert_code("OUTSIDE_ROOT", self.publish, target=self.project / "outside.md")
        self.assert_code("UNSAFE_PATH", self.publish, target=self.root / "wiki" / ".." / "escape.md")
        external = Path(self.temporary.name) / "external.md"
        external.write_bytes(b"outside candidate authority")
        self.assert_code("OUTSIDE_ROOT", self.publish, candidate=external)
        directory = self.project / "not-a-file"
        directory.mkdir()
        self.assert_code("NOT_REGULAR", self.publish, candidate=directory)
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM wf_publications").fetchone()[0], 0)

    def test_linked_candidates_and_target_ancestors_are_rejected(self):
        def junction(path):
            subprocess.run([os.environ["COMSPEC"], "/d", "/c", "mklink", "/J", str(path), str(self.project)],
                           check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.addCleanup(path.rmdir)
            self.assertTrue(path.is_junction())
        if os.name == "nt":
            # Exercise native links without Developer Mode or symlink privileges.
            link = self.project / "linked-candidate"
            junction(link)
            candidate = link / self.candidate.name
        else:
            candidate = self.project / "link.md"
            candidate.symlink_to(self.candidate)
        self.assert_code("UNSAFE_PATH", self.publish, candidate=candidate)
        if os.name == "nt":
            junction(self.root / "wiki")
        else:
            (self.root / "wiki").symlink_to(self.project, target_is_directory=True)
        self.assert_code("UNSAFE_PATH", self.publish)
        self.assertFalse((self.project / "topic.md").exists())
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM wf_publications").fetchone()[0], 0)

    def test_wrong_reconciliation_root_and_missing_owner_refuse(self):
        self.publish()
        self.assert_code("ROOT_CONFLICT", self.manager.reconcile, "op", root=self.project)
        self.owner.__exit__(None, None, None)
        self.owner = None
        self.assert_code("OWNER_REQUIRED", self.manager.reconcile, "op", root=self.root)

    def test_rename_never_overwrites_even_equal_independent_destination(self):
        source = self.root / "old.md"
        target = self.root / "archive.md"
        source.write_bytes(b"old canonical")
        target.write_bytes(b"old canonical")
        self.assert_code("RENAME_COLLISION", self.manager.rename_file, "run", None, source, target, digest(b"old canonical"), "rename", root=self.root)
        self.assertEqual(source.read_bytes(), b"old canonical")
        self.assertEqual(target.read_bytes(), b"old canonical")
        self.assertEqual(self.row("rename")["status"], "blocked")

    def test_rename_crash_after_link_resumes_unlink_without_replaying_link(self):
        source, target = self.root / "old.md", self.root / "archive" / "old.md"
        source.write_bytes(b"old canonical")
        real_unlink = Path.unlink
        def crash_unlink(path, *args, **kwargs):
            if path == source:
                raise Crash()
            return real_unlink(path, *args, **kwargs)
        with patch.object(Path, "unlink", crash_unlink):
            with self.assertRaises(Crash):
                self.manager.rename_file("run", None, source, target, digest(b"old canonical"), "rename", root=self.root)
        self.assertTrue(os.path.samefile(source, target))
        self.restart()
        with patch("_shared.workflow.src.publication.os.link", side_effect=AssertionError("must not relink after crash")):
            receipt = self.manager.reconcile("rename", root=self.root)
        self.assertFalse(source.exists())
        self.assertEqual(target.read_bytes(), b"old canonical")
        self.assertEqual(receipt["status"], "applied")
        self.assertEqual(self.manager.rename_file("run", None, source, target, digest(b"old canonical"), "rename", root=self.root), receipt)

    def test_rename_crash_after_unlink_records_desired_without_second_mutation(self):
        source, target = self.root / "old.md", self.root / "archive.md"
        source.write_bytes(b"old canonical")
        with patch.object(self.manager, "_receipt", side_effect=Crash()):
            with self.assertRaises(Crash):
                self.manager.rename_file("run", None, source, target, digest(b"old canonical"), "rename", root=self.root)
        self.assertFalse(source.exists())
        self.restart()
        with patch("_shared.workflow.src.publication.os.link", side_effect=AssertionError("must not replay rename")):
            receipt = self.manager.reconcile("rename", root=self.root)
        self.assertEqual(receipt["classification"], "desired")
        self.assertEqual(target.read_bytes(), b"old canonical")

    def test_proved_filesystem_refusal_retains_source_and_reports_separate_prior_receipt(self):
        source, target = self.root / "old.md", self.root / "archive.md"
        source.write_bytes(b"old canonical")
        with patch("_shared.workflow.src.publication.os.link", side_effect=PermissionError(errno.EACCES, "synthetic denied rename")):
            self.assert_code("RENAME_REFUSED", self.manager.rename_file, "run", None, source, target, digest(b"old canonical"), "rename", root=self.root)
        receipt = json.loads(self.row("rename")["receipt_json"])
        self.assertEqual(receipt["classification"], "prior")
        self.assertEqual(receipt["error"]["code"], "RENAME_REFUSED")
        self.assertEqual(source.read_bytes(), b"old canonical")
        self.assertFalse(target.exists())

    def test_rename_uncertain_syscall_is_reconciled_not_classified_as_no_effect(self):
        source, target = self.root / "old.md", self.root / "archive.md"
        source.write_bytes(b"old canonical")
        real_link = os.link
        def link_then_error(*args, **kwargs):
            real_link(*args, **kwargs)
            raise OSError("synthetic lost acknowledgement")
        with patch("_shared.workflow.src.publication.os.link", link_then_error):
            self.assert_code("EFFECT_UNCERTAIN", self.manager.rename_file, "run", None, source, target, digest(b"old canonical"), "rename", root=self.root)
        self.assertEqual(json.loads(self.row("rename")["receipt_json"])["classification"], "unknown")
        self.manager.reconcile("rename", root=self.root)
        self.assertFalse(source.exists())
        self.assertEqual(target.read_bytes(), b"old canonical")

    def test_rename_conflicting_source_after_partial_link_preserves_both_paths(self):
        source, target = self.root / "old.md", self.root / "archive.md"
        source.write_bytes(b"old canonical")
        with patch.object(Path, "unlink", side_effect=Crash()):
            with self.assertRaises(Crash):
                self.manager.rename_file("run", None, source, target, digest(b"old canonical"), "rename", root=self.root)
        # Replace source inode, as a human editor commonly does.
        edited = self.root / "edit.tmp"
        edited.write_bytes(b"human update")
        os.replace(edited, source)
        self.assert_code("PREIMAGE_CONFLICT", self.manager.reconcile, "rename", root=self.root)
        self.assertEqual(source.read_bytes(), b"human update")
        self.assertEqual(target.read_bytes(), b"old canonical")

    def test_pipeline_requires_settled_hash_bound_attempt_not_taskless_authority(self):
        self.owner.__exit__(None, None, None)
        self.store.create_run("task-run", "synthetic", "b" * 64, {}, [])
        self.owner = self.store.acquire_owner("task-run")
        self.owner.__enter__()
        with self.assertRaises(PublicationError):
            self.manager.publish_file("task-run", None, self.candidate, self.target, None, "taskless", root=self.root)
        self.assertFalse(self.target.exists())
        directory = self.project / "docs" / "tmp" / "pipeline-runs" / "task-run" / "attempts" / "attempt"
        directory.mkdir(parents=True)
        candidate = directory / "note.md"
        candidate.write_bytes(b"hash bound output")
        task = {"schema_version": 1, "run_id": "task-run", "step_id": "produce", "item_id": "one", "attempt_id": "attempt", "duty": "produce", "instructions": "Use only synthetic evidence.", "inputs": [], "resources": [], "candidate_dir": str(directory), "outputs": [{"name": "note", "relative_path": "note.md", "media_type": "text/markdown", "required": True}], "allowed_actions": ["write_candidate"], "read_roots": [str(self.project)], "write_roots": [str(directory)], "result_schema": c.task_result_schema(), "artifact_id": "note", "review_contract": "vault-wiki", "artifact_tier": "B", "semantic_sha256": ""}
        task["semantic_sha256"] = c.semantic_hash(task)
        self.store.create_step("task-run", "produce", "one", 0)
        self.store.create_attempt(task)
        self.store.bind_attempt("task-run", "attempt", {"id": "synthetic-child"})
        self.assert_code("UNACCEPTABLE_ATTEMPT", self.manager.publish_file, "task-run", "attempt", candidate, self.target, None, "unsettled", root=self.root)
        result = {"schema_version": 1, "run_id": "task-run", "step_id": "produce", "item_id": "one", "attempt_id": "attempt", "status": "completed", "verification": "PASS", "artifacts": [{"name": "note", "relative_path": "note.md", "sha256": digest(candidate.read_bytes()), "media_type": "text/markdown"}], "task_sha256": task["semantic_sha256"], "evidence": [], "error": None, "decision": None}
        self.store.record_result("task-run", "attempt", result)
        candidate.write_bytes(b"unreviewed alteration")
        self.assert_code("ARTIFACT_CONFLICT", self.manager.publish_file, "task-run", "attempt", candidate, self.target, None, "altered", root=self.root)
        candidate.write_bytes(b"hash bound output")
        self.manager.publish_file("task-run", "attempt", candidate, self.target, None, "accepted", root=self.root)
        self.assertEqual(self.target.read_bytes(), b"hash bound output")

    def settled_projection_source(self, step_id="produce", duty="produce"):
        """Synthetic Store observations exercise admission, not native qualification."""
        directory = self.project / "docs/tmp/pipeline-runs/local-run/attempts" / step_id
        directory.mkdir(parents=True)
        candidate = directory / "note.md"
        candidate.write_bytes("\ufeff---\r\ntitle: café\r\nstatus: draft\r\nlast_verified: null\r\n---\r\nOriginal body.\r\n".encode())
        task = {"schema_version": 1, "run_id": "local-run", "step_id": step_id, "item_id": "one", "attempt_id": step_id,
                "duty": duty, "instructions": "Use synthetic evidence only.", "inputs": [], "resources": [],
                "candidate_dir": str(directory), "outputs": [{"name": "note", "relative_path": "note.md", "media_type": "text/markdown", "required": True}],
                "allowed_actions": ["write_candidate"], "read_roots": [str(self.project)], "write_roots": [str(directory)],
                "result_schema": c.task_result_schema(), "artifact_id": "logical-note", "review_contract": "vault-wiki", "artifact_tier": "B", "semantic_sha256": ""}
        task["semantic_sha256"] = c.semantic_hash(task)
        ordinal = self.store.connection.execute("SELECT MAX(ordinal)+1 FROM wf_steps WHERE run_id='local-run'").fetchone()[0]
        self.store.create_step("local-run", step_id, "one", ordinal)
        self.store.create_attempt(task)
        self.store.bind_attempt("local-run", step_id, {"id": "synthetic-" + step_id})
        self.store.record_result("local-run", step_id, {
            "schema_version": 1, "run_id": "local-run", "step_id": step_id, "item_id": "one", "attempt_id": step_id,
            "status": "completed", "verification": "PASS", "artifacts": [{"name": "note", "relative_path": "note.md", "sha256": digest(candidate.read_bytes()), "media_type": "text/markdown"}],
            "task_sha256": task["semantic_sha256"], "evidence": [], "error": None, "decision": None})
        return candidate

    def projection_pipeline(self):
        candidate, local = self.local_pipeline()
        source = self.settled_projection_source()
        original = source.read_bytes()
        edits = []
        for before, after in ((b"draft", b"published"), (b"null", b"2026-10-17T03:00:00Z")):
            start = original.index(before)
            edits.append({"start": start, "end": start + len(before), "before_hex": before.hex(), "after_hex": after.hex()})
        payload, previous = b"", 0
        for edit in edits:
            payload += original[previous:edit["start"]] + bytes.fromhex(edit["after_hex"])
            previous = edit["end"]
        candidate.write_bytes(payload + original[previous:])
        def ref(path, name=None):
            value = {"path": str(path), "sha256": digest(path.read_bytes()), "media_type": "text/markdown" if path.suffix == ".md" else "application/json"}
            return dict(value, name=name) if name else value
        gate = candidate.parent / "gate.json"
        gate.write_bytes(b'{"synthetic":true,"native_authenticity":false}\n')
        projection = {"schema_version": 1, "projection_id": "projection", "producer_attempt_id": "produce",
                      "source": ref(source, "note"), "candidate": ref(candidate, "accepted-note"), "edits": edits}
        proof = candidate.parent / "projection.json"
        proof.write_bytes(json.dumps(dict(projection, kind="wiki-lifecycle", gate_refs=[ref(gate)])).encode())
        projection["proof_ref"] = ref(proof)
        event = {key: local[key] for key in ("schema_version", "run_id", "step_id", "item_id", "ordinal", "definition_sha256", "candidate_dir", "validation")}
        event["projection"] = projection
        local["artifacts"] = [projection["candidate"], {"name": "projection-proof", **projection["proof_ref"]}]
        local["data"] = {"candidate_projections": [projection]}
        self.store.append_event("local-run", None, "local_output_validated", local)
        return source, candidate, projection, event

    def publish_projection(self, candidate, operation="projection-effect"):
        return self.manager.publish_file("local-run", "produce", candidate, self.target, None, operation, root=self.root)

    def test_projected_publication_requires_registered_admission_and_preserves_native_source(self):
        source, candidate, projection, event = self.projection_pipeline()
        original = source.read_bytes()
        self.manager.validate_projection("local-run", projection, candidate_dir=candidate.parent)
        self.assert_code("UNSAFE_PATH", self.publish_projection, candidate)
        self.store.append_event("local-run", None, "candidate_projection_validated", dict(event, definition_sha256="c" * 64))
        self.assert_code("UNSAFE_PATH", self.publish_projection, candidate)
        self.store.append_event("local-run", None, "candidate_projection_validated", event)
        self.assertEqual(self.publish_projection(candidate)["status"], "applied")
        self.assertEqual(self.target.read_bytes(), candidate.read_bytes())
        self.assertEqual(source.read_bytes(), original)
        candidate.unlink()
        source.unlink()
        Path(projection["proof_ref"]["path"]).unlink()
        with patch("_shared.workflow.src.publication.os.replace", side_effect=AssertionError("desired publication cannot replay")):
            self.assertEqual(self.publish_projection(candidate)["classification"], "desired")

    def test_projection_rechecks_source_candidate_proof_and_declared_body_boundary(self):
        source, candidate, projection, event = self.projection_pipeline()
        for path in (source, candidate, Path(projection["proof_ref"]["path"])):
            original = path.read_bytes()
            path.write_bytes(original + b"tamper")
            self.assert_code("ARTIFACT_CONFLICT", self.manager.validate_projection, "local-run", projection, candidate_dir=candidate.parent)
            path.write_bytes(original)
        candidate.write_bytes(candidate.read_bytes() + b"undeclared body change")
        projection["candidate"]["sha256"] = digest(candidate.read_bytes())
        proof = Path(projection["proof_ref"]["path"])
        data = json.loads(proof.read_bytes())
        data["candidate"] = projection["candidate"]
        proof.write_bytes(json.dumps(data).encode())
        projection["proof_ref"]["sha256"] = digest(proof.read_bytes())
        self.assert_code("INVALID_PROJECTION", self.manager.validate_projection, "local-run", projection, candidate_dir=candidate.parent)
        self.assertFalse(self.target.exists())

    def test_newer_repair_supersedes_original_member_but_verifier_does_not(self):
        source, candidate, projection, event = self.projection_pipeline()
        self.settled_projection_source("verify", "verify")
        self.manager.validate_projection("local-run", projection, candidate_dir=candidate.parent)
        self.store.append_event("local-run", None, "candidate_projection_validated", event)
        self.settled_projection_source("repair", "repair")
        self.assert_code("UNACCEPTABLE_ATTEMPT", self.manager.validate_projection, "local-run", projection, candidate_dir=candidate.parent)
        self.assert_code("UNACCEPTABLE_ATTEMPT", self.publish_projection, candidate)
        self.assertFalse(self.target.exists())

    def local_pipeline(self, *, kind="replace"):
        self.owner.__exit__(None, None, None)
        self.owner = None
        self.store.create_run("local-run", "synthetic-control", "b" * 64, {}, [])
        self.owner = self.store.acquire_owner("local-run")
        self.owner.__enter__()
        self.store.create_step("local-run", "controls", "vault", 0)
        directory = self.project / "docs" / "tmp" / "pipeline-runs" / "local-run" / "local" / "controls"
        directory.mkdir(parents=True)
        candidate = directory / "control.md"
        candidate.write_bytes(b"validated local control\n")
        intent = {"kind": kind, "candidate": str(candidate), "target": str(self.target),
                  "expected_sha256": None, "operation_id": "local-effect", "root": str(self.root)}
        event = {"schema_version": 1, "run_id": "local-run", "step_id": "controls", "item_id": "vault",
                 "ordinal": 0, "definition_sha256": "b" * 64, "candidate_dir": str(directory),
                 "artifacts": [{"name": "control", "path": str(candidate),
                                "sha256": digest(candidate.read_bytes()), "media_type": "text/markdown"}],
                 "data": {}, "validation": {"valid": True, "source": "registered_local_handler"},
                 "publications": [intent]}
        return candidate, event

    def test_pipeline_local_effect_requires_exact_durable_authority_and_bytes(self):
        candidate, event = self.local_pipeline()
        publish = lambda target=self.target, operation="local-effect": self.manager.publish_file(
            "local-run", None, candidate, target, None, operation, root=self.root)
        self.assert_code("LOCAL_EFFECT_REQUIRED", publish)
        self.store.append_event("local-run", None, "local_output_validated", event)
        self.assert_code("LOCAL_EFFECT_REQUIRED", publish, target=self.root / "elsewhere.md")
        self.assert_code("LOCAL_EFFECT_REQUIRED", publish, operation="new-authority")
        candidate.write_bytes(b"unvalidated control")
        self.assert_code("ARTIFACT_CONFLICT", publish)
        self.assertFalse(self.target.exists())
        candidate.write_bytes(b"validated local control\n")
        publish()
        self.assertEqual(self.target.read_bytes(), b"validated local control\n")
        self.assertEqual(self.store.connection.execute(
            "SELECT COUNT(*) FROM wf_attempts WHERE run_id='local-run'").fetchone()[0], 0)
        candidate.unlink()
        self.assertEqual(publish()["classification"], "desired")

    def test_local_event_cannot_authorize_changed_definition_or_superseded_output(self):
        candidate, event = self.local_pipeline()
        event["definition_sha256"] = "c" * 64
        self.store.append_event("local-run", None, "local_output_validated", event)
        self.assert_code("LOCAL_EFFECT_REQUIRED", self.manager.publish_file,
                         "local-run", None, candidate, self.target, None, "local-effect", root=self.root)
        event["definition_sha256"] = "b" * 64
        self.store.append_event("local-run", None, "local_output_validated", event)
        superseding = dict(event, publications=[])
        self.store.append_event("local-run", None, "local_output_validated", superseding)
        self.assert_code("LOCAL_EFFECT_REQUIRED", self.manager.publish_file,
                         "local-run", None, candidate, self.target, None, "local-effect", root=self.root)
        self.assertFalse(self.target.exists())

    def test_local_effect_crash_reconciles_without_candidate_or_replay(self):
        candidate, event = self.local_pipeline()
        self.store.append_event("local-run", None, "local_output_validated", event)
        real_replace = os.replace
        def replace_then_crash(source, target):
            real_replace(source, target)
            raise Crash()
        with patch("_shared.workflow.src.publication.os.replace", replace_then_crash):
            with self.assertRaises(Crash):
                self.manager.publish_file("local-run", None, candidate, self.target, None,
                                          "local-effect", root=self.root)
        candidate.unlink()
        self.close()
        self.store = Store(self.project)
        self.store.initialize()
        self.owner = self.store.acquire_owner("local-run")
        self.owner.__enter__()
        self.manager = PublicationManager(self.store)
        with patch("_shared.workflow.src.publication.os.replace", side_effect=AssertionError("must not replay")):
            receipt = self.manager.reconcile("local-effect", root=self.root)
        self.assertEqual(receipt["classification"], "desired")
        self.assertEqual(self.target.read_bytes(), b"validated local control\n")

    def test_direct_publication_and_rename_refuse_engineering_ledgers(self):
        forbidden = (
            "source_recovery_admitted", "negative_recovery_admitted", "owned_input_evolution_admitted",
            "owned_input_source_revised", "settled_owned_input_evolution_admitted", "local_runtime_revision_admitted",
            "unprepared_local_source_revision_admitted", "operation_quarantined", "semantic_verifier_continuation_reserved",
            "completed_scope_confirmation_source_admitted", "completed_scope_preservation_confirmed",
            "pre_native_snapshots_recovered", "source_attempt_superseded", "source_completion_invalidated",
            "native_admission_succeeded", "quarantine_successor_admitted", "staged_continuation_replaced",
        )
        self.owner.__exit__(None, None, None)
        self.owner = None
        source = self.root / "old.md"
        source.write_bytes(b"ordinary rename preimage")
        for index, event_type in enumerate(forbidden):
            with self.subTest(event_type=event_type):
                run_id = f"engineering-{index}"
                self.store.create_run(run_id, "synthetic-control", "a" * 64, {}, [], run_kind="inline")
                with self.store.acquire_owner(run_id):
                    self.store.append_event(run_id, None, event_type, {})
                    before = self.store.connection.execute("SELECT COUNT(*) FROM wf_events").fetchone()[0]
                    self.assert_code("SOURCE_RECOVERY_CONFLICT", self.manager.publish_file,
                                     run_id, None, self.candidate, self.target, None, f"replace-{index}", root=self.root)
                    self.assert_code("SOURCE_RECOVERY_CONFLICT", self.manager.rename_file,
                                     run_id, None, source, self.target, digest(source.read_bytes()), f"rename-{index}", root=self.root)
                    self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM wf_events").fetchone()[0], before)
                self.assertFalse(self.target.exists())
                self.assertEqual(source.read_bytes(), b"ordinary rename preimage")
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM wf_publications").fetchone()[0], 0)

    def test_source_invalidation_cannot_admit_a_superseded_native_projection(self):
        source, candidate, projection, event = self.projection_pipeline()
        self.store.append_event("local-run", None, "candidate_projection_validated", event)
        self.settled_projection_source("repair", "repair")
        completion_id = self.store.append_event("local-run", None, "item_completed", {
            "step_id": "controls", "item_id": "vault", "data": {"candidate_projections": [projection]}})
        self.store.append_event("local-run", None, "source_completion_invalidated", {
            "step_id": "controls", "item_id": "vault", "completion_event_id": completion_id})
        self.assert_code("SOURCE_RECOVERY_CONFLICT", self.manager.validate_projection,
                         "local-run", projection, candidate_dir=candidate.parent)
        self.assert_code("SOURCE_RECOVERY_CONFLICT", self.publish_projection, candidate)
        self.assertFalse(self.target.exists())
        self.assertTrue(source.exists())
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM wf_publications").fetchone()[0], 0)

    def test_engineering_authority_cannot_reconcile_prior_or_already_desired_effects(self):
        receipt = self.publish()
        pending_target = self.root / "pending.md"
        with patch("_shared.workflow.src.publication.os.replace", side_effect=Crash()):
            with self.assertRaises(Crash):
                self.publish(operation="pending", target=pending_target)
        pending = self.row("pending")
        self.store.append_event("run", None, "completed_scope_preservation_confirmed", {})
        before = self.store.connection.execute("SELECT COUNT(*) FROM wf_events").fetchone()[0]
        with patch("_shared.workflow.src.publication.os.replace", side_effect=AssertionError("engineering authority cannot replay")):
            self.assert_code("SOURCE_RECOVERY_CONFLICT", self.publish)
            self.assert_code("SOURCE_RECOVERY_CONFLICT", self.manager.reconcile, "op", root=self.root)
            self.assert_code("SOURCE_RECOVERY_CONFLICT", self.manager.reconcile, "pending", root=self.root)
        self.assertEqual(self.target.read_bytes(), self.candidate.read_bytes())
        self.assertFalse(pending_target.exists())
        self.assertEqual(json.loads(self.row()["receipt_json"]), receipt)
        self.assertEqual(self.row("pending"), pending)
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM wf_events").fetchone()[0], before)


if __name__ == "__main__":
    unittest.main()
