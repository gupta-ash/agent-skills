---
type: schema
version: "3.0"
created: 2026-06-02
updated: 2026-06-02
scope: canonical
applies_to: all vaults under the wiki pipeline
---
# Canonical Wiki Schema

Single source-of-truth for the multi-vault wiki pipeline (wiki-ingest / wiki-compile / wiki-strategize / wiki-reverify / wiki-content-check). Every vault's `.wiki-schema.md` extends this document; per-vault overrides live only in the vault's own schema file.

This document is loaded by the pipeline at invocation time alongside the target vault's `.wiki-schema.md`. The pipeline merges canonical declarations + per-vault overrides; per-vault wins on collision.

## Extension Mechanism

A vault declares inheritance with two fields near the top of its `.wiki-schema.md`:

```yaml
extends: ~/.agents/skills/wiki/canonical-schema.md
extends_version: "3.0"               # which canonical version this vault's schema is written against
```

`extends_version:` is the canonical version this vault has been validated against (must match a published canonical schema version; see § Versioning).

### Legacy-mode behavior matrix

The pipeline reads BOTH fields at invocation and chooses behavior:

| Vault state | Pipeline behavior |
|---|---|
| Both `extends:` and `extends_version:` present AND version matches canonical | NORMAL — canonical declarations merged with per-vault overrides; per-vault wins on collision; all validations active |
| `extends:` present but `extends_version:` absent | WARN once per invocation ("vault schema does not pin a canonical version; treating as latest"); otherwise NORMAL |
| Both fields present AND version is OLDER than current canonical | Pipeline attempts to load the older snapshot from the canonical-schema archive at `~/.agents/skills/wiki/_archive/canonical-schema-v{X}.md` (this is the schema-archive directory; NOT any vault's `_archive/` retirement area). The `{X}` placeholder is the exact `extends_version:` string with surrounding quotes stripped (e.g., `extends_version: "3.0"` resolves to `canonical-schema-v3.0.md`). The `~` expands to the current user's home directory per OS convention (Windows: `%USERPROFILE%`; Unix: `$HOME`). If the snapshot is PRESENT: WARN once ("vault pinned to canonical v{X}; current is v{Y}; only v{X} declarations active") and load the snapshot. If the snapshot is ABSENT: BLOCK with a clear error ("vault pinned to canonical v{X}; archive snapshot not found at `~/.agents/skills/wiki/_archive/canonical-schema-v{X}.md`; either install the snapshot or bump `extends_version:` to current"). Never silently fall through to current canonical. **Release obligation:** every published canonical schema version (every time the `version:` field at the top of THIS file changes) MUST be snapshot-copied to the archive path BEFORE the version bump merges; this is a pre-merge gate, not a follow-up. |
| Both fields present AND version is NEWER than current canonical | BLOCK — vault schema requires a canonical version this pipeline doesn't ship; pipeline aborts with a clear error |
| `extends:` absent entirely | LEGACY MODE — pipeline uses only per-vault declarations; all validations downgrade to advisory (emit findings, do not block); WARN once per invocation that the vault has not opted in |

Per-validation legacy-mode behavior: in LEGACY MODE, the lint checks in § Validation become advisory — pipeline emits findings to the run log but does not block emission or fail wiki-reverify. This is the migration on-ramp for vaults that have not yet adopted canonical.

A vault MAY override any canonical declaration by re-declaring it in its own schema. Per-vault declarations take precedence on collision. A vault MUST declare:
- `vault_name:` — the canonical short name (e.g., `ResearchWiki`, `LibraryWiki`)
- `domains:` — the vault-specific list of top-level domain slugs (e.g., `[science, technology]` for ResearchWiki)

A vault MAY declare:
- `compilation_path_template:` — overrides the canonical default (see § Output Paths)
- `strategy_path_template:` — overrides the canonical default
- `aliases:` — vault-specific tag/term alias map (additive to any canonical aliases)
- `additional_raw_kinds:` — list of raw-archive modalities beyond the canonical six
- `tag_namespace_legacy:` — set to `bare` to suppress prefixed-tag emission on new content (use only for vaults with extensive legacy bare-tag history; opt-in only)

## Vault Layout (canonical)

```
{vault-root}/
├── .wiki-schema.md              ← Per-vault schema (extends this document)
├── _hub.md                      ← Vault navigation hub (canonical name across all vaults; see § Hub File Compatibility for legacy `index.md` handling)
├── log.md                       ← Operation log
├── verify-log.md                ← Re-verification audit trail
├── .glossary.md                 ← Per-vault glossary (optional)
├── raw/                         ← Immutable source archive (create only modality subdirs the vault actually uses; see § Raw Archive Modalities)
│   ├── _manifest.json           ← Per-vault raw inventory (managed by wiki-ingest)
│   ├── books/                   ← Book chapter-level source notes (one folder per book) — optional; create only if used
│   ├── articles/                ← Web articles, blog posts — optional; create only if used
│   ├── threads/                 ← Reddit / X / forum threads — optional; create only if used
│   ├── videos/                  ← Video transcripts — optional; create only if used
│   ├── documents/               ← PDFs, docs, other long-form — optional; create only if used
│   └── assets/                  ← Images, attachments referenced by other raws — optional; create only if used
├── wiki/                        ← All synthesized knowledge content
│   └── {domain}/                ← Per-domain content folder
│       ├── {slug}-compilation.md      ← Cross-source compilations
│       ├── {slug}.md                  ← Wiki notes (single-source or short syntheses)
│       └── strategy/                  ← Tier-C strategy content
│           └── {slug}.md              ← strategy / strategy-card / inference / plan
├── mocs/                        ← Map-of-Content navigation pages (optional; supplementary to _hub.md)
└── _archive/                    ← Retired material (excluded from manifest + auto-scan)
```

`_archive/` is NEVER auto-loaded by the pipeline; it is the retirement destination for superseded compilations, backup trees, and other historical material that must persist but should not pollute live scans.

## Page Types

Every page declares its type via the `type:` frontmatter field. Ten canonical types organized by contract-strictness:

**High-contract** (pipeline-emitted; full frontmatter + body-section validation):
- `compilation`, `strategy`, `strategy-card`, `inference`, `plan`, `thesis`, `source`

**Low-contract** (per-vault declared; minimal required frontmatter — `type:` + `title:` + `domain:` only; bodies are free-form):
- `entity`, `query`, `moc`

| Type | Contract | Frontmatter `type:` | Naming convention | Purpose | Lives at |
|---|---|---|---|---|---|
| Source | high | `source` | `YYYY-MM-DD-slug.md` (web) or `ch{NN}-slug.md` (book chapter) | Deep analysis of one source | `raw/{kind}/...` |
| Compilation | high | `compilation` | `{slug}-compilation.md` | Cross-source synthesis of a topic (3+ sources convergent) | `wiki/{domain}/` (or per-vault override) |
| Strategy | high | `strategy` | `{slug}.md` | Overarching prescriptive direction across multiple scenarios | `wiki/{domain}/strategy/` |
| Strategy-card | high | `strategy-card` | `{slug}.md` | Discrete per-instrument or per-scenario strategy unit | `wiki/{domain}/strategy/` |
| Inference | high | `inference` | `{slug}.md` | Probabilistic conclusion (descriptive, NOT prescriptive) | `wiki/{domain}/strategy/` |
| Plan | high | `plan` | `{slug}.md` | Operational steps / workflow / checklist | `wiki/{domain}/strategy/` |
| Thesis | high | `thesis` | `thesis-YYYY-MM-DD-{slug}.md` | Single-claim adversarial evaluation with verdict + evidence-for/against + anti-bias audit | `wiki/{domain}/` |
| Entity | low | `entity` | `{slug}.md` | School / program / tool / person / org reference page | `wiki/{domain}/` |
| Query | low | `query` | `YYYY-MM-DD-slug.md` | Saved query result | `wiki/{domain}/` |
| MOC | low | `moc` | `{area}.md` | Map of Content (navigation page) | `mocs/` or `_hub.md` |

### Thesis frontmatter + body contract (high-contract)

```yaml
---
type: thesis                         # REQUIRED
title: "..."                         # REQUIRED — the claim being evaluated, restated as title
claim: "..."                         # REQUIRED — verbatim single-sentence claim
verdict: {supported|refuted|inconclusive}   # REQUIRED — outcome of the evaluation
domain: {domain-slug}                # REQUIRED
tags:
  - topic/{slug}
  - type/thesis
  - vault/{VaultName}
  - domain/{domain-slug}
created: 'YYYY-MM-DD'
updated: 'YYYY-MM-DD'
vault: {VaultName}
author: claude
pipeline_id: {pipeline-id}
status: {draft|verified|published|superseded}
last_verified: 'YYYY-MM-DD' | null
confidence: {low|medium|high}
human_edited: {true|false}
supersedes: "" | {prior-slug}
---
```

Required body sections (verified at wiki-reverify time):
1. **Claim** (verbatim restatement of the `claim:` frontmatter field)
2. **Verdict** (verbatim restatement of `verdict:`; brief one-paragraph rationale)
3. **Evidence For** — bullets citing supporting compilations via `^[[[compilation-slug]]]`
4. **Evidence Against** — bullets citing refuting compilations
5. **Anti-bias Audit** — explicit consideration of confirmation bias, recency bias, source-quality skew, missing perspectives
6. **Limitations** — what the verdict does NOT cover; conditions under which it might flip

### Low-contract type minimal frontmatter

For `entity`, `query`, `moc`: only `type:`, `title:`, `domain:` are required. Other fields are per-vault discretion (vaults MAY declare additional required fields for these types in their `.wiki-schema.md`). No body-section enforcement.

## pipeline_id sentinels (global to all high-contract types)

The `pipeline_id:` field is REQUIRED on every high-contract page type (compilation, strategy, strategy-card, inference, plan, thesis, source). It accepts:

- `{shape-prefix-timestamp-suffix}` — an actual controller-assigned pipeline run identifier, copied from the current run rather than invented from an example; set by the pipeline at emit time.
- `legacy-pre-canonicalization` — set by the multi-vault migration scripts for content authored before canonical schema adoption.
- `hand-curated` — set when a human author creates a canonical-format page outside the pipeline (e.g., hand-writing a compilation, strategy, or thesis in the canonical shape).
- `imported-{source-name}` — set when content is imported from an external source verbatim (e.g., `imported-public-library` for an explicitly imported collection).

No empty string, no `null`. The sentinel preserves grep-ability and provenance audit. This sentinel set applies uniformly to compilation, strategy, strategy-card, inference, plan, thesis, and source frontmatter; per-type frontmatter sections below show `pipeline_id: {pipeline-id}` as a placeholder — any of the four sentinel forms above is a legal value.

The Tier-C decision rule (strategy / strategy-card / inference / plan):

- Prescriptive + discrete unit (one instrument, one scenario) → `strategy-card`
- Prescriptive + overarching (multiple instruments or scenarios) → `strategy`
- Descriptive + probabilistic (observed patterns, no recommendation) → `inference`
- Operational + sequential (steps, checklist, workflow) → `plan`

## Output Paths (canonical defaults)

- **Compilation:** `wiki/{domain}/{topic-slug}-compilation.md`
- **Strategy (any of 4 Tier-C types):** `wiki/{domain}/strategy/{goal-slug}.md`
- **Raw source note:** `raw/{kind}/{slug}.md` where `kind ∈ {books, articles, threads, videos, documents, assets}` ∪ the vault's declared `additional_raw_kinds:` list
- **Book chapter (when kind=books):** `raw/books/{book-slug}/ch{NN}-{chapter-slug}.md` + `raw/books/{book-slug}/_index.md`
- **Hub:** `{vault-root}/_hub.md`

Per-vault overrides are declared via `compilation_path_template:` and `strategy_path_template:` in the vault's `.wiki-schema.md`. A vault that needs nested subdomains (e.g., `wiki/science/methods/` instead of `wiki/science/`) declares:

```yaml
compilation_path_template: "wiki/{level}/{section}/{topic-slug}-compilation.md"
```

The pipeline interpolates the template's `{...}` placeholders from per-topic frontmatter at emit time.

### Hub File Compatibility

Canonical hub filename is `_hub.md`. To minimize migration friction for vaults still using the legacy `index.md`:

**If vault declares `hub_file: <name>` in its `.wiki-schema.md`:** the declared filename is authoritative for both read and write. Pipeline operates on the declared name in all cases; no fallback, no WARN.

**If vault does NOT declare `hub_file:`:**
- **At pipeline read time:** the pipeline looks for `{vault-root}/_hub.md` first; if absent, falls back to `{vault-root}/index.md` and proceeds with a one-line WARN ("vault uses legacy index.md; rename to _hub.md when convenient OR declare `hub_file: index.md` in .wiki-schema.md to silence this warning").
- **At pipeline write time** (when wiki-compile or wiki-strategize updates the hub to add a new compilation/strategy link): writes to whichever filename currently exists; if both exist, writes to `_hub.md` and emits a finding about the duplicate (resolve manually). If neither exists, creates `_hub.md`.

This makes the `index.md` → `_hub.md` rename optional, not blocking. Vaults that prefer to keep `index.md` (e.g., because external systems link to that filename) declare `hub_file: index.md` and the pipeline honors the pin without any fallback ambiguity.

## Compilation Frontmatter Contract

Compilations carry frontmatter in two tiers: **identity-and-provenance** (required, no defaults) and **lifecycle** (required field name, defaultable value). The pipeline populates both at L4_emit time; legacy migration may need to fill identity-and-provenance fields per migration script.

### Tier A — Identity and provenance (REQUIRED, no defaults)

```yaml
type: compilation                    # always literal "compilation"
title: "..."                         # human-readable title (often "{Concept} — Cross-Source Compilation")
concept: "..."                       # 1-2 sentence summary of what this compilation covers
domain: {domain-slug}                # top-level domain (must be in vault's domains: list)
topic: {topic-slug}                  # short identifier (often matches the filename slug)
sources: {integer}                   # count of contributing sources

# One-of: exactly one of the next two fields MUST be present (never both, never neither).
# Selection rule per § Compilation source-list contract below.
contributing_books:                  # USE when ALL contributing sources are books (source_class: books for every entry in compiled_from:)
  - "Author — Title"
# OR
contributing_sources:                # USE when ANY contributing source is non-book (mixed-class or non-book-only)
  - "Source label or URL"

compiled_from:                       # provenance list (dict-shape; one entry per source; authoritative)
  - slug: {source-slug}
    source_class: {books|articles|threads|videos|documents|assets|<vault-additional>}
    citation_count: {integer}        # count of ^[[[slug]]] references in body
vault: {VaultName}                   # matches vault's vault_name declaration
author: {claude|human|named-author}  # generator identity ("claude" for pipeline-emitted; "human" or named for hand-curated)
pipeline_id: {pipeline-id-or-sentinel}   # see § pipeline_id sentinels for legal values
created: 'YYYY-MM-DD'
tags:                                # see § Tag Namespace
  - topic/{slug}
  - type/compilation
  - vault/{VaultName}
  - domain/{domain-slug}
```

### Tier B — Lifecycle (REQUIRED field name; default values listed)

```yaml
updated: 'YYYY-MM-DD'                # default: same as created
last_compiled: 'YYYY-MM-DD'          # default: same as created
contradictions: {integer}            # default: 0 (count surfaced during synthesis)
open_questions: {integer}            # default: 0
status: {draft|verified|published|superseded}   # DEFAULT for clean pipeline emit: "published"
last_verified: 'YYYY-MM-DD' | null   # default: null (never verified)
freshness: {live|weekly|monthly|quarterly|permanent}   # default: monthly
confidence: {low|medium|high}        # default: medium
human_edited: {true|false}           # default: false (pipeline-emitted = false; flip to true on hand-edit)
supersedes: "" | {prior-slug}        # default: "" (not superseding)
related: []                          # default: [] (no related compilations)
```

### Compilation source-list contract (mutually exclusive)

The `contributing_books:` and `contributing_sources:` fields are **mutually exclusive**:

- **`contributing_books:`** — REQUIRED when ALL contributing sources are books (`source_class: books` for every entry in `compiled_from:`). One entry per book.
- **`contributing_sources:`** — REQUIRED when ANY contributing source is NOT a book (mixed-class or non-book-only). One entry per source.

Exactly one of the two MUST be present. A compilation with both fields populated is a lint violation. Empty values are not permitted; if no contributing sources exist, the compilation is not valid.

`compiled_from:` is the authoritative provenance list (dict-shape, per-source detail). `contributing_books:` / `contributing_sources:` are display-friendly labels derived from `compiled_from:` for human-readable rendering; their values MUST be consistent with `compiled_from:` (every entry in the label field corresponds to an entry in `compiled_from:`).

### Default `status` for pipeline emits

Pipeline-emitted compilations that pass L3_remediate clean (no GAN findings) receive `status: published` by default. The user MAY override per-emit via pipeline argument; status flows through the full lifecycle (draft → verified → published → superseded) per user/pipeline workflow.

### Status enum semantics
- `draft` — emitted by pipeline but not yet user-reviewed
- `verified` — passed wiki-reverify pass with no breaking findings
- `published` — surfaced in `_hub.md` and queryable as authoritative; default for pipeline-emit clean outputs
- `superseded` — replaced by a newer compilation; `supersedes:` field on the newer one points back here; this file MAY be auto-archived to `_archive/superseded-compilations/`

### Freshness tier semantics
- `live` — re-verify daily (rare; only fast-moving regulatory or market data)
- `weekly` — re-verify every 7 days
- `monthly` — re-verify every 30 days (default for most topics)
- `quarterly` — re-verify every 90 days (stable knowledge with infrequent updates)
- `permanent` — never auto-re-verify (historical or fixed reference; e.g., book canon)

## Strategy / Strategy-card / Inference / Plan Frontmatter Contract

The 4 Tier-C types share a common base; each adds type-specific fields. Common base:

```yaml
---
type: {strategy|strategy-card|inference|plan}   # REQUIRED — one of the 4
title: "..."                         # REQUIRED
goal: "..."                          # REQUIRED — verbatim Goal statement (the question or objective this answers)
domain: {domain-slug}                # REQUIRED
tags:                                # REQUIRED
  - topic/{slug}
  - type/{strategy|strategy-card|inference|plan}
  - vault/{VaultName}
  - domain/{domain-slug}
created: 'YYYY-MM-DD'
updated: 'YYYY-MM-DD'
compiled_from:                       # REQUIRED — provenance list (dict-shape; one entry per source)
  - slug: {compilation-or-raw-slug}
    source_class: {compilation|<declared raw source_class>}
    citation_count: {integer}        # count of ^[[[slug]]] references in body
vault: {VaultName}
author: claude
pipeline_id: {pipeline-id}
status: {draft|verified|published|superseded}
last_verified: 'YYYY-MM-DD' | null
confidence: {low|medium|high}
human_edited: {true|false}
supersedes: "" | {prior-slug}
---
```

Type-specific additional fields:
- **strategy-card:** `parent_bundle:` (optional), `strategy_number:` (optional), `source_section:` (optional)
- **strategy:** no additional required fields
- **inference:** `observation_window:` (optional — e.g., "2026-Q1")
- **plan:** `execution_cadence:` (optional — e.g., "daily", "weekly")

### Required body sections for Tier-C content

All 4 Tier-C types MUST include these body sections. **Enforcement is normative at pipeline emit time (L3_remediate):** the cross-family discriminator review fails any Tier-C emit missing any required section, and L4_emit is blocked until L3 reaches clean. wiki-reverify additionally re-checks these sections on every drift-check pass; missing sections surface as drift findings.

1. **Goal** (verbatim restatement of the `goal:` frontmatter field)
2. **Context** (what scenario / market regime / situation this applies to)
3. **Recommendation** (for strategy / strategy-card / plan) OR **Conclusion** (for inference) — prescriptive content marked with `[STRATEGY]` evidence markers
4. **Reasoning Chain** — 100% citation footnotes using `^[[[compilation-slug]]]` format; every prescriptive claim cites a compilation
5. **Evidence Base** — bullets summarizing the compilations consulted
6. **Counter-Considerations** — concrete failure scenarios where the strategy/inference might not hold
7. **Review Triggers** — FALSIFIABLE conditions (conditional connector + numeric comparator OR measurable noun + explicit threshold) under which this content should be re-evaluated

No Tier-C file may be written to vault without all 7 sections present. Hand-curated Tier-C content (created outside the pipeline) MUST satisfy the same body-section contract; wiki-reverify will flag any hand-curated file that drifts.

## Source Note Frontmatter Contract

```yaml
---
type: source                         # REQUIRED
source: "..."                        # REQUIRED — human-readable source identifier (e.g., "Public Research Methods Course — Chapter 3")
source_url: "..."                    # REQUIRED for web/article/thread/video; OPTIONAL for book
source_file: "..."                   # REQUIRED for document/book; path to original file
source_class: {books|articles|threads|videos|documents|assets|<vault-additional>}   # REQUIRED — one of the canonical six OR a value declared in vault.additional_raw_kinds
ingested: 'YYYY-MM-DD'               # REQUIRED
pipeline_id: {pipeline-id-or-sentinel}   # REQUIRED — wiki-ingest pipeline_id when emitted by pipeline; sentinel value per § pipeline_id sentinels for hand-curated / imported / legacy notes
content_hash: {sha256-or-null}       # REQUIRED — hash of full-text body for drift detection
freshness: {live|weekly|monthly|quarterly|permanent}   # REQUIRED
last_verified: 'YYYY-MM-DD' | null
backfill: {true|false}               # REQUIRED — true if archive-fetched from Wayback; false if direct
archive_url: "..." | null            # REQUIRED IF backfill=true
tags:
  - source/{provider-slug}
  - domain/{domain-slug}            # primary domain tag for retrieval (prefixed namespace)
  - topic/{topic-slug}              # fine-grained topic tag (prefixed namespace)
---
```

## Tag Namespace Convention

The canonical convention is **prefixed-namespace tags**:

- `domain/{slug}` — top-level domain (e.g., `domain/science`, `domain/technology`)
- `type/{slug}` — page type (e.g., `type/compilation`, `type/strategy-card`)
- `topic/{slug}` — fine-grained topic (e.g., `topic/reproducibility`)
- `vault/{slug}` — vault identity (e.g., `vault/ResearchWiki`)
- `source/{slug}` — source provider identity (e.g., `source/public-library`, `source/research-journal`)

All new content emitted by the pipeline uses prefixed tags. Vaults with extensive legacy bare-tag history (e.g., `science/experimental-design`, `methods/measurement`) MAY opt out by declaring `tag_namespace_legacy: bare` in their `.wiki-schema.md`; otherwise the pipeline emits prefixed by default.

Migration discipline: existing legacy bare tags on already-shipped content STAY IN PLACE (additive migration, not retroactive rewrite). Only newly-emitted content uses the prefixed convention.

## Raw Archive Modalities

Six canonical modalities under `raw/`:

| Modality | Contains | Typical content |
|---|---|---|
| `books/` | Long-form book chapters | One subfolder per book: `{book-slug}/_index.md` + `ch{NN}-{slug}.md` per chapter |
| `articles/` | Web articles, blog posts, news | One file per article: `YYYY-MM-DD-{slug}.md` |
| `threads/` | Forum threads, Reddit posts, X threads | One file per thread |
| `videos/` | Video transcripts (YouTube, podcasts, courses) | One file per video |
| `documents/` | PDFs, Word docs, slide decks, long-form documents | One file per document |
| `assets/` | Images, attachments referenced by other raws | Non-text assets |

A vault MAY use only a subset (e.g., a public-domain reading library may use only `books/`). A vault MAY declare additional modalities via `additional_raw_kinds:` in its schema.

## Per-Vault Alias Map

Vaults declare term/tag aliases in their `.wiki-schema.md` under `aliases:`. The pipeline consults this map at ingest time to disambiguate user-supplied terms and at compile time to normalize tag references.

**Alias-map semantics:** LHS is the input term (user-typed, source-extracted, or legacy bare-tag); RHS is the canonical resolved slug. The pipeline then emits the resolved slug under a namespace prefix per § Tag Namespace — e.g., a resolved slug `ai/agents` is emitted as the tag `topic/ai-agents` (slashes in slugs are replaced with hyphens in the namespaced tag form), or `domain/ai` depending on whether the resolved value is a topic or a domain.

A vault MAY declare both kinds of aliases:
- **Topic-resolving aliases** (LHS → slug under topic/): the resolved slug becomes a `topic/{slug}` tag.
- **Domain-resolving aliases** (LHS → slug under domain/): the resolved slug becomes a `domain/{slug}` tag.

Vault schema declares which slugs are domains (via the required `domains:` list) and which are topics (any RHS value not in `domains:` defaults to topic). Example structure:

```yaml
aliases:
  # Topic-resolving
  ai-agent: ai/agents              # → tag: topic/ai-agents
  llm: ai                          # → tag: topic/ai
  sanya: education                 # (if "education" is in domains:, → tag: domain/education; else topic/education)
  carlmont: education
```

Aliases are vault-local; canonical schema declares no global aliases. Legacy bare-tag content in a vault is left in place; the alias map governs only NEW pipeline-emitted tags.

## Validation (run at every pipeline emit + every reverify)

The pipeline validates outputs against this schema at emit time. Lint checks:

1. **Frontmatter completeness** — every required field present
2. **Output path conformance** — file lives at canonical path (or per-vault override path)
3. **Evidence marker discipline** — every prescriptive claim in a Tier-C file carries a `^[[[compilation-slug]]]` citation
4. **Tag-namespace conformance** — new content uses prefixed tags (unless vault declares `tag_namespace_legacy: bare`)
5. **Wikilink resolution** — every `[[wikilink]]` resolves to a real file in the vault
6. **Status-lifecycle integrity** — `status: superseded` requires the newer file's `supersedes:` to point back here
7. **Freshness-tier validity** — `freshness:` value is one of the 5 canonical tiers
8. **Source-class validity** — `source_class:` in raw notes is one of the canonical six (books / articles / threads / videos / documents / assets) UNION the vault's declared `additional_raw_kinds:` list
9. **Compilation source-list one-of** — exactly one of `contributing_books:` / `contributing_sources:` is present per compilation; never both, never neither; selected per § Compilation source-list contract

Violations surface as wiki-reverify findings; the pipeline does not auto-fix (preserves human-edit protection); user reviews and decides.

## Pipeline Integration

The canonical pipeline consumes this schema:

- **wiki-ingest** writes raws to `raw/{kind}/{slug}.md` per § Raw Archive Modalities; populates source-note frontmatter per § Source Note Frontmatter Contract.
- **wiki-compile** writes compilations to `wiki/{domain}/{slug}-compilation.md` (or per-vault override path) per § Compilation Frontmatter Contract.
- **wiki-strategize** writes Tier-C content to `wiki/{domain}/strategy/{slug}.md` per § Strategy Frontmatter Contract; enforces required body sections via cross-family discriminator review at L3_remediate (normative; L4 blocked until L3 clean).
- **wiki-reverify** scans all canonical-located content; emits drift report; updates `last_verified:` on clean pass.
- **wiki-content-check** verifies per-claim citations resolve against raw archive; surfaces unsupported claims.

All pipelines respect per-vault overrides; the vault's `.wiki-schema.md` is the per-invocation contract carrier.

## Anti-Patterns

Do NOT:
- Emit a compilation or strategy without `vault:`, `author:`, `pipeline_id:` — these are required for downstream re-verification and provenance audit.
- Use bare tags (`science/experimental-design`) on new content unless the vault declares `tag_namespace_legacy: bare`.
- Write content directly to vault paths without going through the pipeline — bypasses validation, evidence-marker discipline, and provenance audit.
- Write per-save change narrative into compilation bodies — the audit trail belongs in `verify-log.md` and `log.md`, not in the compilation itself.
- Hand-edit a compilation marked `human_edited: false` and leave the flag false — flip to `true` so the pipeline knows not to overwrite.
- Place a compilation outside `wiki/{domain}/` (or the vault's declared override path) — the pipeline globs by canonical path; off-path files are invisible.

## Versioning

Canonical schema version is declared in the frontmatter `version:` field of THIS file. Per-vault `.wiki-schema.md` declares its own `schema_version:` (independent; tracks vault-specific evolution). The pipeline consults BOTH at invocation.

A vault that extends `version: 3.0` of canonical inherits all declarations in this file; a vault that extends an older snapshot will see only that snapshot's declarations.

---

_Maintained by the wiki project (`wiki/`)._
