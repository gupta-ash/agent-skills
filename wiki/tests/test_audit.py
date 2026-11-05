"""Synthetic audit consumers; real temporary SQLite and PublicationManager.

Source-only regressions, never live vaults or native reviews.
"""
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if "_workflow" not in sys.modules:
    package = types.ModuleType("_workflow")
    package.__path__ = [str(ROOT / "_shared/workflow/src")]
    sys.modules["_workflow"] = package
from _workflow.store import Store
from _workflow.publication import PublicationManager
from wiki.runtime.audit import apply_audit, prepare_audit
from wiki.runtime.schema import SchemaError, resolve_schema

NOW = "2026-10-17T04:00:00Z"
OLD = "2025-01-01T00:00:00Z"


class AuditTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()
        (self.vault / "raw").mkdir()
        (self.vault / ".wiki-schema.md").write_bytes(b'''---
vault_name: SyntheticLib
version: "1.3"
extends: canonical-schema.md
extends_version: "3.0"
domains: [tech]
---
Human schema prose stays.
<!-- ash_wiki:begin controls -->
```yaml controls
workflow_controls_version: 1
managed_navigation:
  log.md: operations
  verify-log.md: verification
```
<!-- ash_wiki:end controls -->
''')
        self.outside_before = b"---\r\nhuman_edited: false\r\n---\r\nManual header.\n"
        self.outside_after = b"\r\nManual footer with [[custom/reference]].\n"
        (self.vault / "verify-log.md").write_bytes(self.outside_before + b"<!-- ash_wiki:begin verification -->\r\nOld managed entry.\r\n<!-- ash_wiki:end verification -->" + self.outside_after)
        (self.vault / "log.md").write_bytes(b"# Operations\n<!-- ash_wiki:begin operations -->\n<!-- ash_wiki:end operations -->\nHuman tail.\n")
        self.manifest = {"items": [{"slug": "a", "source_sha256": "declared-authority", "custom": 4}],
                         "last_verified_at": OLD, "custom": {"preserved": True},
                         "wiki_workflow": {"runtime_version": 1, "custom_namespace": [1, 2]}}
        self.manifest_path = self.vault / "raw/_manifest.json"
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        self.project = self.root / "project"
        self.store = Store(self.project)
        self.store.initialize()
        self.addCleanup(self.store.close)
        self.store.create_run("audit-run", "audit-maintenance", "a" * 64, {}, {}, run_kind="inline")
        self.publication = PublicationManager(self.store)
        self.resolved = resolve_schema(self.vault, "SelectionObs")

    def report(self, **changes):
        result = {"verification_axis": "content", "pages_expected": 2, "pages_processed": 2,
                  "summary": {"verified": 1, "needs_attention": 1, "insufficient_data": 0, "tooling_failure": 0,
                              "claims_not_reviewed": 0, "claims_insufficient_data": 0},
                  "fan_in_complete": True, "scope": {"slugs": ["a", "b"]}, "tooling_failures": [],
                  "report_path": "docs/tmp/pipeline-runs/audit-run/content-check-report.json"}
        result.update(changes)
        return result

    def plan(self, report=None, operation="audit-op"):
        return prepare_audit(self.resolved, self.report() if report is None else report, "audit-run", operation, NOW)

    def apply(self, plan, publication=None):
        with self.store.acquire_owner("audit-run"):
            return apply_audit(plan, publication or self.publication, "audit-run", None,
                               plan.operation_id, self.project / "candidates")

    def read_manifest(self):
        return json.loads(self.manifest_path.read_bytes())

    def test_readonly_prepare_and_complete_content_counts_preserve_bytes(self):
        before = {str(p): p.read_bytes() for p in self.vault.rglob("*") if p.is_file()}
        plan = self.plan()
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.vault.rglob("*") if p.is_file()})
        receipts = self.apply(plan)
        self.assertEqual(["applied", "applied"], [receipt["status"] for receipt in receipts])
        data = (self.vault / "verify-log.md").read_bytes()
        self.assertTrue(data.startswith(self.outside_before))
        self.assertTrue(data.endswith(self.outside_after))
        self.assertIn(b"**Slugs expected:** 2\r\n", data)
        self.assertIn(b"**Slugs processed:** 2\r\n", data)
        self.assertIn(b"**Slugs verified:** 1\r\n", data)
        manifest = self.read_manifest()
        self.assertEqual(NOW, manifest["last_verified_at"])
        self.assertEqual(1, manifest["last_verified_slug_count"])
        self.assertEqual(self.manifest["items"], manifest["items"])
        self.assertEqual(self.manifest["custom"], manifest["custom"])
        self.assertEqual([1, 2], manifest["wiki_workflow"]["custom_namespace"])
        record, = manifest["wiki_workflow"]["audit_operations"].values()
        self.assertEqual(("content", 2, 2, 1, NOW), tuple(record[key] for key in ("verification_axis", "selected", "processed", "verified", "attempted_at")))
        resolved = resolve_schema(self.vault, "SelectionObs")
        self.assertEqual(("SyntheticLib", "1.3", "3.0"), (resolved.wiki_name, resolved.local_revision, resolved.canonical_version))

    def test_timestamp_never_refreshes_drift_zero_partial_tooling_or_ingest(self):
        cases = [
            {"verification_axis": "drift", "selected": 2, "processed": 2, "verified": 2, "fan_in_complete": True},
            {"verification_axis": "content", "selected": 0, "processed": 0, "verified": 0, "fan_in_complete": True, "claims_total": 0},
            {"verification_axis": "content", "selected": 3, "processed": 1, "verified": 1, "fan_in_complete": False},
            {"verification_axis": "content", "selected": 2, "processed": 2, "verified": 1, "fan_in_complete": True, "tooling_failures": ["b"]},
            {"verification_axis": "content", "operation": "ingest", "selected": 2, "processed": 2, "verified": 2, "fan_in_complete": True, "input": "Synthetic document", "source_type": "document", "created": ["raw/documents/a.md"]},
        ]
        for index, report in enumerate(cases):
            with self.subTest(report=report):
                self.apply(self.plan(report, f"boundary-{index}"))
                self.assertEqual(OLD, self.read_manifest()["last_verified_at"])
                self.assertEqual(NOW, self.read_manifest()["last_verify_attempt_at"])
        self.assertIn(b"wiki ingest", (self.vault / "log.md").read_bytes())

    def test_identical_operation_is_noop_and_changed_report_refuses(self):
        self.apply(self.plan())
        before = (self.manifest_path.read_bytes(), (self.vault / "verify-log.md").read_bytes())
        again = self.plan()
        self.assertEqual((), again.changes)
        self.apply(again)
        self.assertEqual(before, (self.manifest_path.read_bytes(), (self.vault / "verify-log.md").read_bytes()))
        with self.assertRaises(SchemaError) as caught:
            self.plan(self.report(scope={"slugs": ["other"]}))
        self.assertEqual("AUDIT_OPERATION_CONFLICT", caught.exception.code)

    def test_conflicting_aliases_and_false_fanin_refuse(self):
        for report in (self.report(selected=3), self.report(verified=2),
                       self.report(pages_processed=1), self.report(missing_cc2_outputs=["lost"]),
                       self.report(selectors={"slugs": ["different"]}), self.report(verification_axis="ingest")):
            with self.subTest(report=report), self.assertRaises(SchemaError):
                self.plan(report)

    def test_missing_manifest_locked_and_markerless_logs_do_not_write(self):
        log = self.vault / "verify-log.md"
        original = log.read_bytes()
        for content, code in ((b"---\nverify_log_locked: true\n---\n", "AUDIT_LOG_LOCKED"),
                              (b"Manual markerless log\n", "AUDIT_REGION_NOT_ENROLLED"),
                              (original + b"<!-- ash_wiki:begin verification -->", "AUDIT_REGION_NOT_ENROLLED")):
            log.write_bytes(content)
            before = self.manifest_path.read_bytes()
            with self.assertRaises(SchemaError) as caught:
                self.plan()
            self.assertEqual(code, caught.exception.code)
            self.assertEqual(before, self.manifest_path.read_bytes())
            self.assertEqual(content, log.read_bytes())
        log.write_bytes(original)
        self.manifest_path.unlink()
        with self.assertRaises(SchemaError) as caught:
            self.plan()
        self.assertEqual("AUDIT_MANIFEST_MISSING", caught.exception.code)

    def test_human_protection_proposal_and_late_edit_preserved(self):
        log = self.vault / "verify-log.md"
        protected = log.read_bytes().replace(b"human_edited: false", b"human_edited: true")
        log.write_bytes(protected)
        plan = self.plan()
        self.assertEqual("skipped_protected", plan.receipts[0]["status"])
        self.apply(plan)
        self.assertEqual(protected, log.read_bytes())
        log.write_bytes(protected.replace(b"human_edited: true", b"human_edited: false"))
        late_plan = self.plan(operation="late-edit")
        late = log.read_bytes() + b"New human text.\n"
        log.write_bytes(late)
        with self.assertRaises(SchemaError) as caught:
            self.apply(late_plan)
        self.assertEqual("PREIMAGE_CONFLICT", caught.exception.code)
        self.assertEqual(late, log.read_bytes())
        self.assertEqual([], caught.exception.details["receipts"])

    def test_same_plan_reconciles_partial_apply_without_duplicate_log(self):
        plan = self.plan()
        class Interrupted:
            def __init__(self, actual):
                self.actual, self.calls = actual, 0
            def publish_file(self, *args, **kwargs):
                self.calls += 1
                if self.calls == 2:
                    raise OSError("Synthetic process boundary before second effect")
                return self.actual.publish_file(*args, **kwargs)
        with self.assertRaises(SchemaError) as caught:
            self.apply(plan, Interrupted(self.publication))
        self.assertEqual(1, len(caught.exception.details["receipts"]))
        after_log = (self.vault / "verify-log.md").read_bytes()
        self.apply(plan)
        self.assertEqual(after_log, (self.vault / "verify-log.md").read_bytes())
        self.assertEqual(1, len(self.read_manifest()["wiki_workflow"]["audit_operations"]))
        self.assertEqual((), self.plan().changes)

    def test_changed_schema_and_new_protection_block_before_effect(self):
        plan = self.plan()
        schema = self.vault / ".wiki-schema.md"
        schema.write_bytes(schema.read_bytes() + b"Human schema addition.\n")
        with self.assertRaises(SchemaError) as caught:
            self.apply(plan)
        self.assertEqual("AUDIT_SCHEMA_CHANGED", caught.exception.code)
        self.assertEqual(OLD, self.read_manifest()["last_verified_at"])

    def test_json_manifest_protection_flags_are_byte_exact_skips(self):
        for index, flags in enumerate(({"human_edited": True}, {"pipeline_id": "hand-curated"})):
            with self.subTest(flags=flags):
                original = json.dumps({**self.manifest, **flags}, indent=3).encode() + b"\n"
                self.manifest_path.write_bytes(original)
                plan = self.plan(operation=f"protected-json-{index}")
                skips = [receipt for receipt in plan.receipts if receipt["role"] == "manifest"]
                self.assertEqual("skipped_protected", skips[0]["status"])
                self.assertIn("proposal", skips[0])
                self.apply(plan)
                self.assertEqual(original, self.manifest_path.read_bytes())

    def test_late_json_protection_preserves_manifest_and_partial_receipts(self):
        plan = self.plan()
        late = json.dumps({**self.manifest, "human_edited": True}).encode()
        self.manifest_path.write_bytes(late)
        with self.assertRaises(SchemaError) as caught:
            self.apply(plan)
        self.assertEqual(late, self.manifest_path.read_bytes())
        self.assertEqual(1, len(caught.exception.details["receipts"]))
        self.assertIn(caught.exception.code, ("PREIMAGE_CONFLICT", "PROTECTED_TARGET"))


if __name__ == "__main__":
    unittest.main()
