---
schema_version: 1
vault_name: "{vault_name}"
extends: ~/.agents/skills/wiki/canonical-schema.md
extends_version: "3.1"
created: {date}
---

# Wiki Schema

## Vault Structure

```
{vault_name}/
├── .wiki-schema.md          ← This file (source of truth)
├── raw/                     ← Immutable source archive
│   ├── articles/
│   ├── threads/
│   ├── videos/
│   ├── books/
│   ├── documents/
│   └── assets/
├── wiki/                    ← ALL knowledge pages (topic hierarchy)
│   └── {area}/{topic}/      ← source, compilation, entity, query pages
├── mocs/                    ← Maps of Content (navigational hubs)
├── _hub.md                  ← Navigation hub (owned region only)
├── index.md                 ← Optional distinct catalog (explicit enrollment)
└── log.md                   ← Operation log (auto-maintained)
```

## Page Types

| Type | Frontmatter `type:` | Naming | Purpose |
|---|---|---|---|
| Source | `source`, role `capture` | `YYYY-MM-DD-slug.md` | Faithful evidence, not generated analysis |
| Compilation | `compilation`, role `topic_distill` or `synthesis` | resolved template | Detailed source-topic knowledge or cross-source synthesis |
| Entity | `entity` | `name.md` | Tool/person/org page |
| Query | `query` | `YYYY-MM-DD-slug.md` | Saved query result |
| MOC | `moc` | `area.md` | Map of Content |

## Tag Ontology

### Domains (every note gets exactly one)
- tech
- wealth
- family
- health
- home
- marketing
- social

### Topics (nested under domains)
```yaml
tech:
  - ai
  - ai/agents
  - ai/memory
  - ai/frameworks
  - ai/security
  - ai/prompting
  - ai/research
  - devtools
  - devtools/claude-code
  - devtools/openclaw
  - devtools/obsidian
  - infrastructure
  - home-automation
  - web
wealth:
  - trading
  - investing
  - real-estate
  - insurance
  - tax
family:
  - kids
  - education
  - health
health:
  - fitness
  - nutrition
  - medical
home:
  - automation
  - maintenance
marketing:
  - seo
  - content
  - social-media
social:
  - networking
  - events
```

### Content Types (every note gets exactly one)
- source
- compilation
- entity
- query
- moc
- project

### Status Tags (optional)
- actionable
- contradicted
- stale
- archived

### Tag Aliases (normalization rules)
```yaml
ai-agent: ai/agents
ai-agents: ai/agents
agents: ai/agents
autonomous-agents: ai/agents
plugin: devtools
plugins: devtools
claude: devtools/claude-code
claude-code: devtools/claude-code
obsidian: devtools/obsidian
oc: devtools/openclaw
openclaw: devtools/openclaw
llm: ai
llm-wiki: ai/knowledge-management
prompt-engineering: ai/prompting
prompt-injection: ai/security
rag: ai/memory
vector-db: ai/memory
embeddings: ai/memory
```

## Workflow Controls

<!-- ash_wiki:begin controls -->
```yaml controls
workflow_controls_version: 1
distillation_path_template: wiki/{domain}/{topic-slug}/sources/{source-id}-{unit-id}.md
content_roots: [raw/, wiki/]
hub_file: _hub.md
index_file: null
canonical_files: {}
allowed_tiers: [A, B, C]
raw_modalities: [books, articles, threads, videos, documents, assets]
allowed_provenance_classes: [web, book, course, pdf, podcast, video, image, thread, document]
outbound_archiving: true
managed_navigation: {}
```
<!-- ash_wiki:end controls -->

Templates are declarations, not permission to scaffold directories. Managed regions
must be explicitly enrolled before a writer touches them; custom/reference files
and human-edited controls remain protected. Narrow tiers/modalities to the supplied
new-vault profile; never apply these defaults over an existing vault's policy.


## Conventions

- **Depth-1 link following:** Follow direct links from source, create sub-source notes. Note depth-2 as `unfetched`.
- **Compilation threshold:** 3+ source notes on same concept → create compilation page.
- **Manual edit protection:** Pages with `human_edited: true` are never auto-modified.
- **Freshness tiers:** `live` (daily), `weekly`, `monthly` (default), `quarterly`, `permanent`.
- **Provenance:** Every claim should trace to its source via `^[[[source]]]` notation.
- **Wikilinks:** Use `[[page-name]]` for internal links. Obsidian resolves by filename.
