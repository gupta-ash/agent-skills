"""Synthetic Controls consumer regressions; no live vault/native dependencies."""
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
from wiki.runtime.schema import SchemaError, resolve_schema
from wiki.runtime.vault_controls import apply_controls, is_protected, prepare_controls, prepare_initialization


class ControlsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.vault = self.home / "vault"
        self.vault.mkdir()
        self.project = self.home / "project"
        self.store = Store(self.project)
        self.store.initialize()
        self.addCleanup(self.store.close)
        self.store.create_run("controls-run", "synthetic-controls", "a" * 64, {}, [], run_kind="inline")
        self.manager = PublicationManager(self.store)

    def write_schema(self, extra="", *, crlf=False):
        text = ('---\nwiki_name: SyntheticLib\nversion: 7\nextends: canonical-schema.md\n'
                'extends_version: "3.0"\nhub_file: _hub.md\nindex_file: index.md\n'
                'domains: [synthetic]\ncustom_policy:\n  literal: preserve-me\n' + extra + '---\nHuman schema prose.\n')
        data = text.encode().replace(b"\n", b"\r\n") if crlf else text.encode()
        (self.vault / ".wiki-schema.md").write_bytes(data)
        return resolve_schema(self.vault, "SyntheticSelection")

    def apply(self, plan, operation="controls-op"):
        with self.store.acquire_owner("controls-run"):
            return apply_controls(plan, self.manager, "controls-run", None, operation, self.project / "candidates")

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(SchemaError) as caught:
            function(*args, **kwargs)
        self.assertEqual(code, caught.exception.code)
        return caught.exception

    def test_prepare_is_read_only_preserves_pin_identity_and_mixed_newlines(self):
        resolved = self.write_schema(crlf=True)
        schema_before = (self.vault / ".wiki-schema.md").read_bytes()
        hub = b"Manual hub\r\nA mixed LF line\nFinal bytes without newline"
        (self.vault / "_hub.md").write_bytes(hub)
        before = set(self.vault.rglob("*"))
        plan = prepare_controls(resolved, {}, adopt_existing=True)
        self.assertEqual(before, set(self.vault.rglob("*")))
        self.assertEqual(schema_before, (self.vault / ".wiki-schema.md").read_bytes())
        self.apply(plan)
        self.assertTrue((self.vault / ".wiki-schema.md").read_bytes().startswith(schema_before))
        self.assertTrue((self.vault / "_hub.md").read_bytes().startswith(hub))
        actual = resolve_schema(self.vault, "SyntheticSelection")
        self.assertEqual(("SyntheticSelection", "SyntheticLib", 7, "3.0"),
                         (actual.selection_key, actual.wiki_name, actual.local_revision, actual.canonical_version))
        self.assertTrue(actual.controls_enrolled)
        self.assertEqual("preserve-me", actual.raw_declarations["custom_policy"]["literal"])
        self.assertEqual("_hub.md", actual.canonical_files["hub"])
        self.assertEqual("index.md", actual.canonical_files["index"])
        self.assertFalse((self.vault / "wiki").exists())
        self.assertFalse((self.vault / "raw/books").exists())
        self.assertEqual((), prepare_controls(actual, {}, adopt_existing=True).changes)

    def test_markerless_ordinary_use_refuses_without_any_write(self):
        resolved = self.write_schema()
        before = (self.vault / ".wiki-schema.md").read_bytes()
        self.assert_code("VAULT_CONTROLS_NOT_ENROLLED", prepare_controls, resolved, {})
        self.assertEqual(before, (self.vault / ".wiki-schema.md").read_bytes())
        self.assertFalse((self.vault / "raw").exists())

    def test_protected_hub_catalog_log_and_custom_entity_reference_are_skips(self):
        resolved = self.write_schema('canonical_files:\n  glossary: Glossary.md\n')
        protected = {"_hub.md": b"---\nhuman_edited: true\n---\nCurated hub\n",
                     "index.md": b"---\npipeline_id: hand-curated\n---\nCurated catalog\n",
                     "log.md": b"---\nhuman_edited: true\n---\nCurated operations\n",
                     "Glossary.md": b"---\ntype: entity\n---\nCustom reference\n"}
        for name, data in protected.items():
            (self.vault / name).write_bytes(data)
        plan = prepare_controls(resolved, {}, adopt_existing=True)
        self.assertTrue(is_protected(self.vault / "Glossary.md", resolved))
        self.assertEqual(set(protected), {r["path"] for r in plan.receipts if r["status"] == "skipped-protected"})
        self.apply(plan)
        for name, data in protected.items():
            self.assertEqual(data, (self.vault / name).read_bytes())
        navigation = resolve_schema(self.vault).raw_declarations["managed_navigation"]
        self.assertNotIn("_hub.md", navigation)
        self.assertNotIn("index.md", navigation)
        self.assertNotIn("Glossary.md", navigation)

    def test_duplicate_unbalanced_and_malformed_regions_refuse(self):
        for data in (b"<!-- ash_wiki:begin navigation -->\n",
                     b"<!-- ash_wiki:end navigation --><!-- ash_wiki:begin navigation -->",
                     b"<!-- ash_wiki:begin navigation --><!-- ash_wiki:end navigation -->" * 2):
            with self.subTest(data=data):
                resolved = self.write_schema()
                (self.vault / "_hub.md").write_bytes(data)
                self.assert_code("CONTROL_MARKER_CONFLICT", prepare_controls, resolved, {}, adopt_existing=True)
                self.assertEqual(data, (self.vault / "_hub.md").read_bytes())

    def test_explicit_override_conflicts_and_unsafe_paths_refuse(self):
        resolved = self.write_schema('allowed_tiers: [A, B]\n')
        self.assert_code("SCHEMA_DECLARATION_CONFLICT", prepare_controls, resolved, {"allowed_tiers": ["A", "B", "C"]}, adopt_existing=True)
        self.assert_code("SCHEMA_PATH_OUTSIDE_VAULT", prepare_controls, resolved, {"distillation_path_template": "../escape/{topic-slug}.md"}, adopt_existing=True)
        self.assert_code("SCHEMA_INVALID_DECLARATION", prepare_controls, resolved, {"outbound_archiving": "false"}, adopt_existing=True)
        self.assert_code("CONTROL_PATH_CONFLICT", prepare_controls, resolved, {"managed_navigation": {"Reference.md": "navigation"}}, adopt_existing=True)
        self.assert_code("CONTROL_PATH_CONFLICT", prepare_controls, resolved, {"canonical_files": {"manifest": "other.json"}}, adopt_existing=True)

    def populate_raw(self):
        folder = self.vault / "raw/documents"
        folder.mkdir(parents=True)
        (folder / "source.md").write_text('---\ntype: source\nsource_sha256: authority-value\nsource_hash_authority: corpus:MANIFEST.md\n---\nSynthetic original body\n', encoding="utf-8")
        (folder / "_index.md").write_text('---\ntype: moc\n---\nNavigation\n', encoding="utf-8")
        (folder / "image.bin").write_bytes(b"synthetic asset")
        return folder

    def test_missing_manifest_inventories_populated_raw_without_fake_verification(self):
        resolved = self.write_schema()
        folder = self.populate_raw()
        before = {path: path.read_bytes() for path in folder.iterdir()}
        plan = prepare_controls(resolved, {}, adopt_existing=True)
        self.assertFalse((self.vault / "raw/_manifest.json").exists())
        self.apply(plan)
        manifest = json.loads((self.vault / "raw/_manifest.json").read_bytes())
        inventory = {item["path"]: item for item in manifest["wiki_workflow"]["inventory"]}
        self.assertEqual(3, len(inventory))
        self.assertEqual("navigation", inventory["raw/documents/_index.md"]["classification"])
        source = inventory["raw/documents/source.md"]
        self.assertEqual("unreviewed", source["acceptance"])
        self.assertEqual("authority-value", source["observed_provenance"]["source_sha256"])
        self.assertNotIn("last_verified_at", manifest)
        self.assertNotIn("sha256", source)
        for path, data in before.items():
            self.assertEqual(data, path.read_bytes())
        self.assertIn(b"No verification has been performed", (self.vault / "verify-log.md").read_bytes())

    def test_declared_source_named_index_is_not_inventory_navigation(self):
        resolved = self.write_schema()
        folder = self.populate_raw()
        (folder / "source.md").rename(folder / "index.md")
        self.apply(prepare_controls(resolved, {}, adopt_existing=True))
        manifest = json.loads((self.vault / "raw/_manifest.json").read_bytes())
        inventory = {item["path"]: item for item in manifest["wiki_workflow"]["inventory"]}
        self.assertEqual("source_note", inventory["raw/documents/index.md"]["classification"])
        self.assertEqual("navigation", inventory["raw/documents/_index.md"]["classification"])

    def test_human_protected_json_manifest_is_skipped_byte_exactly(self):
        resolved = self.write_schema()
        (self.vault / "raw").mkdir()
        path = self.vault / "raw/_manifest.json"
        original = b'{ "human_edited": true, "custom": [1, 2] }\r\n'
        path.write_bytes(original)
        plan = prepare_controls(resolved, {}, adopt_existing=True)
        self.assertTrue(any(row["path"] == "raw/_manifest.json" and row["status"] == "skipped-protected" for row in plan.receipts))
        self.apply(plan)
        self.assertEqual(original, path.read_bytes())

    def test_manifest_preserves_unknown_fields_and_old_success_timestamp(self):
        resolved = self.write_schema()
        (self.vault / "raw").mkdir()
        original = {"last_verified_at": "2001-02-03", "custom": {"opaque": [1, 2]}, "sources": [{"legacy": True}]}
        (self.vault / "raw/_manifest.json").write_text(json.dumps(original), encoding="utf-8")
        self.apply(prepare_controls(resolved, {}, adopt_existing=True))
        actual = json.loads((self.vault / "raw/_manifest.json").read_bytes())
        self.assertEqual(original, {key: actual[key] for key in original})
        self.assertEqual((), prepare_controls(resolve_schema(self.vault), {}, adopt_existing=True).changes)

    def test_changed_preimage_refuses_before_canonical_effects(self):
        resolved = self.write_schema()
        (self.vault / "_hub.md").write_text("Manual content", encoding="utf-8")
        plan = prepare_controls(resolved, {}, adopt_existing=True)
        (self.vault / "_hub.md").write_text("New human edit", encoding="utf-8")
        error = self.assert_code("CONTROL_PREIMAGE_CONFLICT", self.apply, plan)
        self.assertEqual((), error.details["receipts"])
        self.assertFalse((self.vault / "raw").exists())
        self.assertEqual("New human edit", (self.vault / "_hub.md").read_text())

    def test_durable_partial_effects_reconcile_without_replacing_human_changes(self):
        resolved = self.write_schema()
        plan = prepare_controls(resolved, {}, adopt_existing=True)
        # Perform the first prepared effect with its exact operation identity,
        # modelling process loss after the schema's durable publication.
        first = plan.changes[0]
        key = sha256(first.path.relative_to(self.vault).as_posix().encode()).hexdigest()
        candidate_dir = self.project / "candidates"
        candidate_dir.mkdir()
        candidate = candidate_dir / (key + ".candidate")
        candidate.write_bytes(first.content)
        publication_root = Path(str(self.vault).swapcase()) if sys.platform == "win32" else self.vault
        self.assertEqual(self.vault.stat().st_ino, publication_root.stat().st_ino)
        with self.store.acquire_owner("controls-run"):
            self.manager.publish_file("controls-run", None, candidate, first.path, first.expected_sha256,
                                      "controls-op:" + key, root=publication_root)
        receipts = self.apply(plan)
        self.assertTrue(any(r.get("status") == "applied" for r in receipts))
        count = self.store.connection.execute("SELECT COUNT(*) FROM wf_publications").fetchone()[0]
        self.apply(plan)
        self.assertEqual(count, self.store.connection.execute("SELECT COUNT(*) FROM wf_publications").fetchone()[0])
        (self.vault / "_hub.md").write_text("Human intervened", encoding="utf-8")
        self.assert_code("CONTROL_PREIMAGE_CONFLICT", self.apply, plan)
        self.assertEqual("Human intervened", (self.vault / "_hub.md").read_text())

    def test_raw_metadata_and_reference_preimages_are_guarded(self):
        resolved = self.write_schema('canonical_files:\n  register: Register.md\n')
        reference = self.vault / "Register.md"
        reference.write_text("Original reference", encoding="utf-8")
        folder = self.populate_raw()
        plan = prepare_controls(resolved, {}, adopt_existing=True)
        reference.write_text("Changed reference", encoding="utf-8")
        self.assert_code("CONTROL_PREIMAGE_CONFLICT", self.apply, plan)
        reference.write_text("Original reference", encoding="utf-8")
        (folder / "source.md").write_text("Changed raw", encoding="utf-8")
        self.assert_code("CONTROL_RAW_METADATA_CONFLICT", self.apply, plan)

    def test_initialization_does_not_write_or_scaffold_and_refuses_nonempty_vault(self):
        plan = prepare_initialization(self.vault, "FreshSynthetic", (), profile={"allowed_tiers": ["A", "B"], "outbound_archiving": False})
        self.assertEqual([], list(self.vault.iterdir()))
        self.apply(plan)
        actual = resolve_schema(self.vault)
        self.assertEqual("3.1", actual.canonical_version)
        self.assertEqual((), actual.domains)
        self.assertEqual(("A", "B"), actual.allowed_tiers)
        self.assertFalse((self.vault / "wiki").exists())
        self.assertFalse((self.vault / "raw/documents").exists())
        self.assert_code("VAULT_NOT_EMPTY", prepare_initialization, self.vault, "Replacement", ())

    def test_template_data_is_persisted_without_creating_nested_directories(self):
        resolved = self.write_schema()
        template = "wiki/{level}/{section}/{topic-leaf-slug}/sources/{source-id}-{unit-id}.md"
        plan = prepare_controls(resolved, {"distillation_path_template": template,
                                         "strategy_path_template": "education/{domain}/strategy/{goal-slug}.md",
                                         "content_roots": ["raw/", "wiki/", "education/"]}, adopt_existing=True)
        self.apply(plan)
        actual = resolve_schema(self.vault)
        self.assertEqual(template, actual.output_templates["distillation_path_template"])
        self.assertFalse((self.vault / "wiki").exists())
        self.assertFalse((self.vault / "education").exists())


if __name__ == "__main__":
    unittest.main()
