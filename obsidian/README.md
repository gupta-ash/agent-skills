# Obsidian

A local filesystem CLI for vault notes, not an Obsidian plugin or MCP server. [SKILL.md](SKILL.md) documents note operations, JSON output and failures. Both CLIs require sibling `_vault_resolve.py` and Python with PyYAML.

Set `VAULT_<NAME>_LOCATION` in the invoking process or pass `--vault <Name-or-path>` after the subcommand. Exactly one configured vault is the default. Zero/multiple configured vaults require explicit selection; there is no preferred personal vault, drive or Dropbox fallback. Vault names never resolve through cwd.

The existing path-containment, link-boundary, mutation-confirmation, frontmatter and wikilink contracts remain enforced. Explicit `--overwrite` controls moves; preserve unrelated vault files. Source examples and tests use synthetic vaults only; this repository contains no vault data.

Install through root `install.ps1`; no source checkout, staging repository, fleet metadata, Obsidian application or service is required at runtime. Run `python -B obsidian/tests/test_vault_resolver.py`, `test_synthetic_vault.py`, and `test_vault_boundaries.py` from source for isolated behavior checks. Actual user vault operations require separate explicit authorization.
