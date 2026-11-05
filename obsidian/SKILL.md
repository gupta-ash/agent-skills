---
name: obsidian
description: Obsidian vault operations via Python — replaces MCP plugin. Read, write, search, move, tag, frontmatter, stats.
---

# obsidian — Obsidian Vault Operations

In OMP, Claude Code and Codex, run vault operations through the shared Python CLI. This replaces ALL `mcp__obsidian__*` tool calls; native file/search tools do not replace vault selection, JSON results, mutation safeguards or wikilink updates.

## Usage

```bash
python "$HOME/.agents/skills/obsidian/scripts/vault_ops.py" <command> [args]
python "$HOME/.agents/skills/obsidian/scripts/vault_search.py" <command> [args]
```

**IMPORTANT:** Always use `python`, NEVER `python3`.

Use the host's native command tool (OMP `bash` or `eval` subprocess, Claude Code Bash/PowerShell, Codex command execution). The quoted `$HOME` paths above work in Bash and PowerShell; use the active shell's stdin/quoting syntax, not a Bash heredoc in PowerShell. Keep `--vault <Name-or-path>` after the subcommand. No Obsidian application, MCP server or host-specific launcher is required.

## Default Vault

Vault names resolve from `VAULT_<NAME>_LOCATION` in the active process environment (for example `Notes` → `VAULT_NOTES_LOCATION`). Pass `--vault <Name>` or a literal path containing a path separator or colon. Without `--vault`, exactly one configured vault is the default; zero or multiple configured vaults refuse and require an explicit selection. An unset name fails with JSON on stdout; no alternate vault or filesystem fallback is guessed.

## vault_ops.py — Core Commands (15)

Successful commands output JSON to stdout and exit 0. Vault/operation failures output `{"error": "message"}` to stdout and exit 1. Argument syntax errors (for example, missing `--content`) use argparse's stderr diagnostic and exit 2, not the JSON error contract. Do not treat empty search results as a missing-vault error or a failed command as success.

### read — Read a note

```bash
python vault_ops.py read <path>
# Returns: {"path": "...", "content": "...", "frontmatter": {...}}
```

### write — Write a note

```bash
python vault_ops.py write <path> --content "text" [--frontmatter '{"key":"val"}'] [--mode overwrite|append|prepend]
# --mode defaults to overwrite
# --content can be "-" to read from stdin
```

### patch — String replace in a note

```bash
python vault_ops.py patch <path> --old "find this" --new "replace with"  [--replace-all]
```

### search — Search notes

```bash
python vault_ops.py search "query" [--content] [--frontmatter] [--limit 5] [--case-sensitive]
# --content and --frontmatter default to true and false respectively
# Returns: [{"path": "...", "matches": [{"line": N, "text": "..."}]}]
```

### list — List files and directories

```bash
python vault_ops.py list [path]
# path defaults to "/" (vault root)
# Returns: {"files": [...], "directories": [...]}
```

### tags — Manage tags

```bash
python vault_ops.py tags <path> list
python vault_ops.py tags <path> add --tags tag1,tag2
python vault_ops.py tags <path> remove --tags tag1,tag2
# list returns both frontmatter tags and inline #tags
```

### frontmatter — Get frontmatter

```bash
python vault_ops.py frontmatter <path>
# Returns: {"path": "...", "frontmatter": {...}}
```

### update-frontmatter — Update frontmatter

```bash
python vault_ops.py update-frontmatter <path> --data '{"key":"val"}' [--merge]
# --merge defaults to true (merge with existing). Use --no-merge to replace entirely.
```

### stats — Vault statistics

```bash
python vault_ops.py stats [--recent-count 5]
# Returns: {"total_notes": N, "total_folders": N, "total_size_bytes": N, "recent": [...]}
```

### all-tags — All tags with counts

```bash
python vault_ops.py all-tags
# Returns: [{"tag": "...", "count": N}, ...]  sorted by count desc
```

### move — Move/rename a note (updates wikilinks)

```bash
python vault_ops.py move <old-path> <new-path> [--overwrite] [--no-wikilink-update]
# Scans ALL .md files in vault and updates [[old-name]] → [[new-name]] wikilinks
# --no-wikilink-update: SKIP the inbound-link rewrite. Use for D44 rename-then-write
#   supersession (rename old canonical → archive; write new canonical to old slug):
#   inbound [[old_slug]] links must keep pointing at old_slug so they resolve to the
#   NEW canonical, not the renamed archive.
```

### delete — Delete a note

```bash
python vault_ops.py delete <path> --confirm
# --confirm flag is required (safety)
```

### read-multiple — Batch read (max 10)

```bash
python vault_ops.py read-multiple <path1> <path2> ...
# Returns: [{"path": "...", "content": "...", "frontmatter": {...}}, ...]
```

### notes-info — Batch metadata (no content)

```bash
python vault_ops.py notes-info <path1> <path2> ...
# Returns: [{"path": "...", "size": N, "modified": "...", "frontmatter": {...}}, ...]
```

### move-file — Move any file (updates wikilinks)

```bash
python vault_ops.py move-file <old-path> <new-path> [--overwrite] [--no-wikilink-update]
# Like move but works on any file type, not just .md
# --no-wikilink-update: same supersession-safe semantics as `move` (skip inbound link rewrite).
```

## vault_search.py — Enhanced Search

### text — Full-text search ranked by match count

```bash
python vault_search.py text "query" [--limit 10]
```

### tag — Find notes by tags

```bash
python vault_search.py tag tag1 [tag2 ...] [--mode any|all]
# --mode any (default): notes with ANY of the tags
# --mode all: notes with ALL of the tags
```

### links-to / backlinks — Inbound wikilinks

```bash
python vault_search.py links-to <note-name>
python vault_search.py backlinks <note-name>
# Finds all notes containing [[note-name]] or [[note-name|alias]]
```

### links-from — Outbound wikilinks

```bash
python vault_search.py links-from <note-name>
# Extracts all [[target]] links from the note
```

### orphans — Notes with zero inbound links

```bash
python vault_search.py orphans
# Returns notes that no other note links to
```

## Routing Rules

For OMP, Claude Code and Codex, use these commands for the corresponding vault operation:

| MCP Tool | Replacement Command |
|---|---|
| `mcp__obsidian__read_note` | `vault_ops.py read` |
| `mcp__obsidian__write_note` | `vault_ops.py write` |
| `mcp__obsidian__patch_note` | `vault_ops.py patch` |
| `mcp__obsidian__search_notes` | `vault_ops.py search` |
| `mcp__obsidian__list_directory` | `vault_ops.py list` |
| `mcp__obsidian__manage_tags` | `vault_ops.py tags` |
| `mcp__obsidian__get_frontmatter` | `vault_ops.py frontmatter` |
| `mcp__obsidian__update_frontmatter` | `vault_ops.py update-frontmatter` |
| `mcp__obsidian__get_vault_stats` | `vault_ops.py stats` |
| `mcp__obsidian__list_all_tags` | `vault_ops.py all-tags` |
| `mcp__obsidian__move_note` | `vault_ops.py move` |
| `mcp__obsidian__delete_note` | `vault_ops.py delete` |
| `mcp__obsidian__read_multiple_notes` | `vault_ops.py read-multiple` |
| `mcp__obsidian__get_notes_info` | `vault_ops.py notes-info` |
| `mcp__obsidian__move_file` | `vault_ops.py move-file` |

## Tips

- All note paths are **relative to vault root** (for example `projects/example.md`).
- Content with special characters: use stdin mode (`--content -`) and pipe content
- For multi-line content in write/patch: use heredocs or temp files
- JSON output can be piped to `python -m json.tool` for pretty-printing
