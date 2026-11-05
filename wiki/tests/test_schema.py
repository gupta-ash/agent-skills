"""Synthetic structural schema cases. No live vault bindings or native execution."""
from hashlib import sha256
from pathlib import Path
import sys
import tempfile
import types
import unittest

# Bind source contracts externally, exactly as an installed _workflow package.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if "_workflow" not in sys.modules:
    package = types.ModuleType("_workflow")
    package.__path__ = [str(ROOT / "_shared/workflow/src")]
    sys.modules["_workflow"] = package
from wiki.runtime.schema import SchemaError, resolve_output_path, resolve_schema
from wiki.scripts.tag_ontology import parse_schema, normalize_tags


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.vault = Path(self.temporary.name)

    def schema(self, text):
        (self.vault / ".wiki-schema.md").write_bytes(text.encode("utf-8"))
        return resolve_schema(self.vault, "PhysicalObs")

    def refuse(self, code, text):
        with self.assertRaises(SchemaError) as caught:
            self.schema(text)
        self.assertEqual(code, caught.exception.code)

    def test_selection_identity_revision_and_real_pinned_snapshot(self):
        resolved = self.schema('''---
vault_name: TradingLib
version: "1.3"
extends: ~/.agents/skills/wiki/canonical-schema.md
extends_version: "3.0"
domains: [market]
book_paths:
  raw_layer: raw/books/
  index_file: _index.md
index_file: catalog.md
---
''')
        self.assertEqual("PhysicalObs", resolved.selection_key)
        self.assertEqual("TradingLib", resolved.wiki_name)
        self.assertEqual(("version", "1.3", "3.0"), (resolved.local_revision_field, resolved.local_revision, resolved.canonical_version))
        archive = ROOT / "wiki/_archive/canonical-schema-v3.0.md"
        self.assertEqual(sha256(archive.read_bytes()).hexdigest(), resolved.canonical_sha256)
        self.assertEqual("catalog.md", resolved.canonical_files["index"])
        self.assertEqual("_hub.md", resolved.canonical_files["hub"])
        self.assertEqual(self.vault / "raw/books/example/_index.md", resolve_output_path(resolved, "book_index", {"book-slug": "example"}))

    def test_declared_catalog_is_not_an_implicit_legacy_hub(self):
        (self.vault / "index.md").write_text("Hand-curated catalog.\n", encoding="utf-8")
        resolved = self.schema("---\nvault_name: Example\nindex_file: index.md\n---\n")
        self.assertEqual("_hub.md", resolved.canonical_files["hub"])
        self.assertEqual("index.md", resolved.canonical_files["index"])
        self.refuse("SCHEMA_DECLARATION_CONFLICT", "---\nvault_name: Example\nhub_file: index.md\nindex_file: index.md\n---\n")

    def test_undeclared_legacy_index_retains_hub_compatibility(self):
        (self.vault / "index.md").write_text("Legacy hub.\n", encoding="utf-8")
        resolved = self.schema("---\nvault_name: Example\n---\n")
        self.assertEqual("index.md", resolved.canonical_files["hub"])
        self.assertIsNone(resolved.canonical_files["index"])

    def test_body_only_named_blocks_preserve_unknown_constraints(self):
        text = '''# Synthetic evidence vault
## Declarations
```yaml declarations
vault_name: SyntheticCase
schema_version: 7
extends: canonical-schema.md
extends_version: "3.0"
allowed_tiers: [A, B]
raw_modalities: [documents, assets]
allowed_provenance_classes: [pdf, document, image]
source_hash_authority: corpus:MANIFEST.md
custom_custody:
  dates: '[unknown]'
  allegations: distinguish-status
```
Human prose stays exactly here.
## Domains
```yaml
[legal]
```
## Topics
```yaml
legal: [dates, witnesses]
```
## Tag aliases
```yaml
123: legal/dates
obsolete: null
```
'''
        resolved = self.schema(text)
        self.assertEqual(text, resolved.source_text)
        self.assertEqual("[unknown]", resolved.raw_declarations["custom_custody"]["dates"])
        self.assertEqual("legal/dates", resolved.aliases["123"])
        self.assertIsNone(resolved.aliases["obsolete"])
        self.assertEqual(("A", "B"), resolved.allowed_tiers)
        self.assertEqual("corpus:MANIFEST.md", resolved.source_hash_authority)
        with self.assertRaises(SchemaError) as caught:
            resolve_output_path(resolved, "strategy_path_template", {"domain": "legal", "goal-slug": "review"})
        self.assertEqual("SCHEMA_TIER_PROHIBITED", caught.exception.code)
        with self.assertRaises(SchemaError) as caught:
            resolve_output_path(resolved, "raw_source", {"kind": "pdf", "slug": "evidence"})
        self.assertEqual("SCHEMA_RAW_MODALITY_PROHIBITED", caught.exception.code)

    def test_h3_block_lists_and_ontology_consumer_shape(self):
        self.schema('''---
vault_name: Example
---
### Domains (every note gets exactly one)
- tech
### Topics (nested under domains)
```yaml
tech:
  - ai
  - ai/agents
```
### Content Descriptors
```yaml
- benchmark
```
### Content Types
- source
- compilation
### Status Tags
- stale
### Tag Aliases
```yaml
agents: ai/agents
123: ai
obsolete: null
```
''')
        ontology = parse_schema(self.vault)
        self.assertEqual(["ai", "tech/ai", "ai/agents"], ontology["topics"]["tech"])
        self.assertEqual(["benchmark"], ontology["descriptors"])
        self.assertEqual(["source", "compilation"], ontology["content_types"])
        self.assertEqual(["stale"], ontology["status_tags"])
        self.assertEqual(["ai", "ai/agents"], normalize_tags(["123", "agents", "obsolete"], ontology))

    def test_markdown_taxonomy_does_not_invent_descriptor_domains(self):
        resolved = self.schema('''---
vault_name: Example
---
## Domains
- `market` — market structure
- `risk` — risk controls
## Topics
- **market**: `trend`, `market/structure`
- `risk`: `sizing`, `exposure`
## Content Descriptors
- `analysis`
''')
        self.assertEqual(("market", "risk"), resolved.domains)
        self.assertEqual(("trend", "market/structure"), resolved.topics["market"])
        self.assertNotIn("analysis", resolved.domains)

    def test_equivalent_duplicates_merge_but_policy_identity_path_conflicts_refuse(self):
        resolved = self.schema('''---
vault_name: Example
domains: [tech]
book_paths: {index_file: _index.md}
---
```yaml declarations
vault_name: Example
book_paths: {index_file: _index.md, slug_by: author}
```
## Domains
- tech
- health
''')
        self.assertEqual(("tech", "health"), resolved.domains)
        self.assertEqual("author", resolved.raw_declarations["book_paths"]["slug_by"])
        for extra in ("vault_name: Different", "book_paths: {index_file: other.md}", "allowed_tiers: [A, B]"):
            with self.subTest(extra=extra):
                self.refuse("SCHEMA_DECLARATION_CONFLICT", "---\nvault_name: Example\nbook_paths: {index_file: _index.md}\nallowed_tiers: [A, B, C]\n---\n```yaml declarations\n" + extra + "\n```\n")
        self.refuse("SCHEMA_DECLARATION_CONFLICT", "---\nvault_name: A\nvault_name: B\n---\n")

    def test_kids_nested_paths_and_empty_section(self):
        resolved = self.schema('''---
vault_name: Example
domains: [college]
compilation_path_template: wiki/{level}/{section}/{topic-leaf-slug}-compilation.md
distillation_path_template: wiki/{level}/{section}/{topic-leaf-slug}/sources/{source-id}-{unit-id}.md
strategy_path_template: education/{domain}/strategy/{goal-slug}.md
book_paths: {raw_layer: raw/books/, slug_by: author, chapter_pattern: 'chNN-{slug}.md'}
content_roots: [raw/, wiki/, education/]
---
''')
        values = {"level": "college", "section": "", "topic-leaf-slug": "admissions", "source-id": "a" * 64, "unit-id": "u000001"}
        self.assertEqual(self.vault / "wiki/college/admissions-compilation.md", resolve_output_path(resolved, "compilation_path_template", values))
        self.assertEqual(self.vault / ("wiki/college/admissions/sources/" + "a" * 64 + "-u000001.md"), resolve_output_path(resolved, "distillation_path_template", values))
        self.assertEqual(self.vault / "raw/books/writer/ch02-introduction.md", resolve_output_path(resolved, "book_chapter", {"author-slug": "writer", "NN": "02", "chapter-slug": "introduction"}))
        self.assertEqual((self.vault / "raw", self.vault / "wiki", self.vault / "education"), resolved.read_roots)

    def test_containment_placeholders_and_protected_reference_collisions(self):
        for path in ("../other.md", "C:/other.md", "/other.md", "raw/../other.md", "raw\\other.md"):
            with self.subTest(path=path):
                self.refuse("SCHEMA_PATH_OUTSIDE_VAULT" if "\\" not in path else "SCHEMA_INVALID_PATH", f"---\nvault_name: Example\nhub_file: '{path}'\n---\n")
        resolved = self.schema('''---
vault_name: Example
domains: [tech]
canonical_files: {glossary: wiki/tech/glossary-compilation.md}
---
''')
        for values, code in (({"domain": "tech"}, "SCHEMA_UNRESOLVED_PLACEHOLDER"), ({"domain": "tech", "topic-slug": "../escape"}, "SCHEMA_INVALID_PATH"), ({"domain": "tech", "topic-slug": "glossary"}, "SCHEMA_PROTECTED_REFERENCE")):
            with self.assertRaises(SchemaError) as caught:
                resolve_output_path(resolved, "compilation_path_template", values)
            self.assertEqual(code, caught.exception.code)

    def test_canonical_version_selection(self):
        resolved = self.schema("---\nvault_name: Example\nextends: canonical-schema.md\n---\n")
        self.assertEqual("3.1", resolved.canonical_version)
        self.assertEqual("SCHEMA_VERSION_UNPINNED", resolved.diagnostics[0]["code"])
        for version in ("2.9", "3.2"):
            self.refuse("SCHEMA_VERSION_UNAVAILABLE", f'---\nvault_name: Example\nextends: canonical-schema.md\nextends_version: "{version}"\n---\n')
        legacy = self.schema("---\nvault_name: Example\nversion: 3\n---\n")
        self.assertIsNone(legacy.canonical_version)
        self.assertEqual(3, legacy.local_revision)

    def test_managed_markers_are_not_ownership_and_unbalanced_markers_refuse(self):
        resolved = self.schema("---\nvault_name: Example\nworkflow_controls_version: 1\n---\n")
        self.assertFalse(resolved.controls_enrolled)
        self.refuse("SCHEMA_INVALID_DECLARATION", "---\nvault_name: Example\n---\n<!-- ash_wiki:begin controls -->\n")

    def test_symlink_output_component_refuses(self):
        resolved = self.schema("---\nvault_name: Example\ndomains: [tech]\n---\n")
        outside = self.vault / "outside"
        outside.mkdir()
        try:
            (self.vault / "wiki").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Host does not permit unprivileged symlink fixture creation")
        with self.assertRaises(SchemaError) as caught:
            resolve_output_path(resolved, "compilation_path_template", {"domain": "tech", "topic-slug": "agents"})
        self.assertEqual("SCHEMA_PATH_OUTSIDE_VAULT", caught.exception.code)

    def test_nested_markdown_topics_flat_books_and_distinct_glossaries(self):
        (self.vault / ".glossary.md").write_text("machine glossary", encoding="utf-8")
        (self.vault / "glossary.md").write_text("human glossary", encoding="utf-8")
        resolved = self.schema('''---
vault_name: Example
domains: [market, risk]
book_paths: {raw_layer: raw/books/, slug_by: null, chapter_pattern: '{book-slug}.md'}
---
## Topics
### market
- `topic/structure`
### risk
- `topic/exposure`
## Content Descriptors
- `analysis`
''')
        self.assertEqual(("structure",), resolved.topics["market"])
        self.assertEqual(("exposure",), resolved.topics["risk"])
        self.assertEqual(".glossary.md", resolved.canonical_files["glossary"])
        self.assertEqual(self.vault / "raw/books/example.md", resolve_output_path(resolved, "book_chapter", {"book-slug": "example"}))
        self.assertEqual("human glossary", (self.vault / "glossary.md").read_text(encoding="utf-8"))

    def test_controls_require_owned_declaration_and_policy_sets_are_equivalent(self):
        resolved = self.schema('''---
vault_name: Example
allowed_tiers: [A, B]
---
<!-- ash_wiki:begin controls -->
```yaml controls
workflow_controls_version: 1
allowed_tiers: [B, A]
managed_navigation: {log.md: operations}
```
<!-- ash_wiki:end controls -->
''')
        self.assertTrue(resolved.controls_enrolled)
        self.assertEqual(("A", "B"), resolved.allowed_tiers)
        self.assertEqual("operations", resolved.raw_declarations["managed_navigation"]["log.md"])

    def test_invalid_policy_and_derived_raw_paths_refuse(self):
        for declaration in ("book_paths: {slug_by: {bad: value}}", "outbound_archiving: 1", "allowed_tiers: [D]", "managed_navigation: {log.md: {bad: value}}"):
            with self.subTest(declaration=declaration):
                self.refuse("SCHEMA_INVALID_DECLARATION", "---\nvault_name: Example\n" + declaration + "\n---\n")
        resolved = self.schema("---\nvault_name: Example\ndomains: [tech]\ncompilation_path_template: raw/articles/{topic-slug}.md\n---\n")
        with self.assertRaises(SchemaError) as caught:
            resolve_output_path(resolved, "compilation_path_template", {"topic-slug": "example"})
        self.assertEqual("SCHEMA_IMMUTABLE_RAW", caught.exception.code)


if __name__ == "__main__":
    unittest.main()
