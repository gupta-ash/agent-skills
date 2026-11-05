# wiki-reverify

One maintenance front selects source drift (`wiki-reverify`, the default) or claim reassessment (`wiki-content-check`). On OMP invoke `/skill:wiki-reverify [--mode drift_check|content_check] --vault NAME-or-path`. The [front](SKILL.md) consumes only `--mode`; all other original argument values go unchanged to the shared runtime. See the [maintenance manual](../wiki/docs/user-manual.md#maintenance).

## Effects and reporting

Drift compares compatible acquired-source hash domains; body, byte-transport and manifest-authoritative hashes are not interchangeable. Books are skipped, transcript delta must be strictly below 0.05, and missing baselines are disclosed. Explicit snapshots create new unreviewed versions without overwriting old evidence. Backfill is report-only and does not invent/write a baseline.

Content checking unions/deduplicates explicit slugs, slug-file, wildcard and all-page selectors. It reads actual cited evidence and uses typed claim evaluations plus native review gates. Zero claims, rejected review, incomplete fan-in and tooling failures cannot become clean verification. Findings retain null scores and unreviewed subjects when applicable. `--apply` is not supported.

Both modes preserve existing note bodies/frontmatter. Enrolled, unprotected audit controls may be updated explicitly; only complete qualifying content fan-in can refresh semantic `last_verified_at`. Unenrolled read-only reporting discloses skipped audit rather than silently enrolling controls. Dry-run does not mutate state, vault, audit or network.

The canonical domain registry is `wiki/runtime/workflows.py`; shared `_workflow` owns native admission, state, cancellation, resume and publication. There is no scheduler registration, ClickUp routing, standalone verifier or automatic replay of uncertain work.

## Checks and limitations

Ordinary consumers in `wiki/tests/` cover reports, hash domains, read-only notes, audit fan-in, rejected reviews and retained charges using temporary fixtures and finite non-native peers. See the [family README](../wiki/README.md#consumer-checks) for parent commands. They were not executed during this edit and do not establish semantic truth, native qualification or installed acceptance. Private engineering histories and recovery APIs are excluded.
