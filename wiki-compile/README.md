# wiki-compile

Build detailed cross-source topic synthesis through the stable `wiki-compile` workflow. On OMP use `/skill:wiki-compile`; the [front](SKILL.md) specifies argument forwarding and the [family manual](../wiki/docs/user-manual.md#compilation) documents the full contract.

This front owns no separate native runtime or publication path. `wiki/runtime/workflows.py` is the canonical registry; the shared `_workflow` installation owns run/status/cancel/resume, native admission, durable attempts and publication.

## Matching and evidence

`scripts/wiki_topic_match.py` provides `match_topic(...)` and an argv CLI. Matching unions M-of-N keyword overlap, literal topic-slug hits and frontmatter topic hits. Compilation frontmatter mode supports custom filenames and nested directories. Optional source-grounded glossary extras use the same filtering/deduplication and expose whether any survived. Files selected by a matcher are candidates, not accepted evidence.

The directory name contains a hyphen. Runtime callers load this script through the existing file-relative loader; do not create an invalid Python dotted import or a second underscore-named package. The canonical public path is `wiki-compile/scripts/wiki_topic_match.py`.

The workflow requires at least three distinct matching raw files, accepted distillations and complete supporting evidence. Chapters are not automatically independent origins. `--from-pipeline ID` remains first when present and binds the named same-project accepted handoff rather than substituting the global corpus. Legacy raw-only inputs must earn their missing acceptance within that run.

Required review and bounded verification/repair preserve artifact identity and cumulative limits. Detailed qualifications, contradictions, numbers and direct raw citations must survive synthesis. Protected targets, preimages, schema paths and supersession semantics still apply. Dry-run is read-only planning, not semantic acceptance.

## Checks and limitations

The family [source README](../wiki/README.md) lists ordinary synthetic consumer checks. The integration owner can run the matcher self-test with `<python> -I -B wiki-compile/scripts/wiki_topic_match.py --test`; it was not run during this edit. No native end-to-end or installed acceptance is claimed. Missing accepted evidence or native prerequisites blocks; there is no inline fallback or private engineering recovery API.
