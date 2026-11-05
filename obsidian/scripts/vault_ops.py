#!/usr/bin/env python
"""
vault_ops.py — Obsidian vault operations CLI.
Replaces the Obsidian MCP plugin with direct filesystem operations.
15 commands: read, write, patch, search, list, tags, frontmatter,
update-frontmatter, stats, all-tags, move, delete, read-multiple,
notes-info, move-file.
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# --- YAML handling with fallback ---
try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

# Shared vault resolution: VAULT_<NAME>_LOCATION, fail-loud (per-vault fleet env).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _vault_resolve import resolve_vault_arg  # noqa: E402


def resolve_path(vault: Path, rel_path: str) -> Path:
    """Resolve a relative path within the vault, preventing traversal."""
    full = (vault / rel_path).resolve()
    vault_resolved = vault.resolve()
    if not full.is_relative_to(vault_resolved):
        raise ValueError(f"Path escapes vault: {rel_path}")
    return full


def _ensure_utf8_stdout():
    """Force stdout to UTF-8 on Windows to avoid charmap errors."""
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")


def out(data):
    """Print JSON to stdout."""
    _ensure_utf8_stdout()
    print(json.dumps(data, ensure_ascii=False, default=str))


def err(msg):
    """Print error JSON and exit."""
    _ensure_utf8_stdout()
    print(json.dumps({"error": str(msg)}, ensure_ascii=False))
    sys.exit(1)


# --- Frontmatter parsing ---

def parse_frontmatter(text: str):
    """Parse YAML frontmatter from markdown text. Returns (frontmatter_dict, content_without_frontmatter)."""
    if not text.startswith("---"):
        return {}, text

    # Find closing ---
    end_match = re.search(r'\n---\s*\n', text[3:])
    if not end_match:
        # Try end of file
        end_match = re.search(r'\n---\s*$', text[3:])
        if not end_match:
            return {}, text

    fm_text = text[4:3 + end_match.start()]
    content = text[3 + end_match.end():]

    if HAS_YAML:
        try:
            fm = yaml.safe_load(fm_text)
            if fm is None:
                fm = {}
            if not isinstance(fm, dict):
                fm = {"value": fm}
        except yaml.YAMLError:
            fm = _regex_parse_frontmatter(fm_text)
    else:
        fm = _regex_parse_frontmatter(fm_text)

    return fm, content


def _regex_parse_frontmatter(fm_text: str) -> dict:
    """Fallback regex parser for frontmatter when PyYAML unavailable."""
    result = {}
    for line in fm_text.strip().split("\n"):
        match = re.match(r'^(\w[\w\s-]*?):\s*(.*)$', line)
        if match:
            key = match.group(1).strip()
            val = match.group(2).strip()
            # Try to parse lists: [a, b, c]
            if val.startswith("[") and val.endswith("]"):
                items = [x.strip().strip('"').strip("'") for x in val[1:-1].split(",")]
                result[key] = [x for x in items if x]
            elif val.lower() in ("true", "false"):
                result[key] = val.lower() == "true"
            elif val.isdigit():
                result[key] = int(val)
            else:
                result[key] = val.strip('"').strip("'")
    return result


def serialize_frontmatter(fm: dict) -> str:
    """Serialize dict to YAML frontmatter string."""
    if HAS_YAML:
        return yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False).rstrip()
    else:
        lines = []
        for k, v in fm.items():
            if isinstance(v, list):
                lines.append(f"{k}: [{', '.join(str(x) for x in v)}]")
            elif isinstance(v, bool):
                lines.append(f"{k}: {'true' if v else 'false'}")
            else:
                lines.append(f"{k}: {v}")
        return "\n".join(lines)


def rebuild_note(fm: dict, content: str) -> str:
    """Rebuild a note with frontmatter + content."""
    if fm:
        return f"---\n{serialize_frontmatter(fm)}\n---\n{content}"
    else:
        return content


def read_note(path: Path):
    """Read a note and return parsed frontmatter + content."""
    text = path.read_text(encoding="utf-8")
    fm, content = parse_frontmatter(text)
    return fm, content


# --- Wikilink update ---

def update_wikilinks(vault: Path, old_name: str, new_name: str) -> int:
    """Scan all .md files and replace [[old_name]] with [[new_name]]. Returns count of files updated."""
    # Match [[old_name]] and [[old_name|alias]]
    pattern = re.compile(
        r'\[\[' + re.escape(old_name) + r'(\|[^\]]*?)?\]\]'
    )
    count = 0
    for md_file in vault.rglob("*.md"):
        try:
            text = md_file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError):
            continue
        new_text = pattern.sub(lambda m: f"[[{new_name}{m.group(1) or ''}]]", text)
        if new_text != text:
            md_file.write_text(new_text, encoding="utf-8")
            count += 1
    return count


# --- Inline tag extraction ---

def find_inline_tags(text: str) -> list:
    """Find inline #tags in text (not in code blocks or frontmatter)."""
    # Remove frontmatter
    _, content = parse_frontmatter(text)
    # Remove code blocks
    content = re.sub(r'```[\s\S]*?```', '', content)
    content = re.sub(r'`[^`]*`', '', content)
    # Find #tags (not preceded by & for HTML entities, not in URLs)
    tags = re.findall(r'(?<!\w)#([\w][\w/-]*)', content)
    return list(set(tags))


# ========== COMMANDS ==========

def cmd_read(args, vault):
    p = resolve_path(vault, args.path)
    if not p.exists():
        err(f"Note not found: {args.path}")
    if not p.is_file():
        err(f"Not a file: {args.path}")
    fm, content = read_note(p)
    out({"path": args.path, "frontmatter": fm, "content": content})


def cmd_write(args, vault):
    p = resolve_path(vault, args.path)
    p.parent.mkdir(parents=True, exist_ok=True)

    if args.content == "-":
        content = sys.stdin.read()
    else:
        content = args.content

    fm = {}
    if args.frontmatter:
        try:
            fm = json.loads(args.frontmatter)
        except json.JSONDecodeError as e:
            err(f"Invalid frontmatter JSON: {e}")

    mode = args.mode or "overwrite"

    if mode == "overwrite":
        text = rebuild_note(fm, content)
        p.write_text(text, encoding="utf-8")
    elif mode == "append":
        existing = p.read_text(encoding="utf-8") if p.exists() else ""
        p.write_text(existing + content, encoding="utf-8")
    elif mode == "prepend":
        existing = p.read_text(encoding="utf-8") if p.exists() else ""
        if existing.startswith("---"):
            # Preserve frontmatter, prepend to content
            existing_fm, existing_content = parse_frontmatter(existing)
            p.write_text(rebuild_note(existing_fm, content + existing_content), encoding="utf-8")
        else:
            p.write_text(content + existing, encoding="utf-8")
    else:
        err(f"Invalid mode: {mode}")

    out({"ok": True, "path": args.path, "mode": mode})


def cmd_patch(args, vault):
    p = resolve_path(vault, args.path)
    if not p.exists():
        err(f"Note not found: {args.path}")
    text = p.read_text(encoding="utf-8")

    count = text.count(args.old)
    if count == 0:
        err(f"String not found in {args.path}")
    if count > 1 and not args.replace_all:
        err(f"Multiple matches ({count}) found. Use --replace-all to replace all, or provide a more specific string.")

    if args.replace_all:
        new_text = text.replace(args.old, args.new)
    else:
        new_text = text.replace(args.old, args.new, 1)

    p.write_text(new_text, encoding="utf-8")
    out({"ok": True, "path": args.path, "replacements": count if args.replace_all else 1})


def cmd_search(args, vault):
    query = args.query
    limit = args.limit or 5
    case_sensitive = args.case_sensitive
    search_content = args.content
    search_fm = args.frontmatter

    if not case_sensitive:
        query_lower = query.lower()

    results = []
    for md_file in vault.rglob("*.md"):
        try:
            text = md_file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError):
            continue

        fm, content = parse_frontmatter(text)
        matches = []

        if search_content:
            for i, line in enumerate(content.split("\n"), 1):
                if case_sensitive:
                    if query in line:
                        matches.append({"line": i, "text": line.strip()})
                else:
                    if query_lower in line.lower():
                        matches.append({"line": i, "text": line.strip()})

        if search_fm and fm:
            fm_str = json.dumps(fm)
            if case_sensitive:
                if query in fm_str:
                    matches.append({"line": 0, "text": f"[frontmatter] {fm_str[:200]}"})
            else:
                if query_lower in fm_str.lower():
                    matches.append({"line": 0, "text": f"[frontmatter] {fm_str[:200]}"})

        if matches:
            rel = str(md_file.relative_to(vault)).replace("\\", "/")
            results.append({"path": rel, "matches": matches})

        if len(results) >= limit:
            break

    out(results)


def cmd_list(args, vault):
    rel = args.path or ""
    target = resolve_path(vault, rel) if rel else vault
    if not target.exists():
        err(f"Directory not found: {rel}")
    if not target.is_dir():
        err(f"Not a directory: {rel}")

    files = []
    dirs = []
    for item in sorted(target.iterdir()):
        name = item.name
        if name.startswith("."):
            continue
        rel_path = str(item.relative_to(vault)).replace("\\", "/")
        if item.is_dir():
            dirs.append(rel_path)
        else:
            files.append(rel_path)

    out({"path": rel or "/", "files": files, "directories": dirs})


def cmd_tags(args, vault):
    p = resolve_path(vault, args.path)
    if not p.exists():
        err(f"Note not found: {args.path}")

    text = p.read_text(encoding="utf-8")
    fm, content = parse_frontmatter(text)
    operation = args.operation

    if operation == "list":
        fm_tags = fm.get("tags") or []
        if isinstance(fm_tags, str):
            fm_tags = [fm_tags]
        inline_tags = find_inline_tags(text)
        all_tags = sorted(set(fm_tags + inline_tags))
        out({"path": args.path, "tags": all_tags, "frontmatter_tags": fm_tags, "inline_tags": inline_tags})

    elif operation == "add":
        if not args.tags:
            err("--tags required for add operation")
        tags_to_add = [t.strip().lstrip("#") for t in args.tags.split(",")]
        current = fm.get("tags") or []
        if isinstance(current, str):
            current = [current]
        for t in tags_to_add:
            if t not in current:
                current.append(t)
        fm["tags"] = current
        p.write_text(rebuild_note(fm, content), encoding="utf-8")
        out({"ok": True, "path": args.path, "tags": current})

    elif operation == "remove":
        if not args.tags:
            err("--tags required for remove operation")
        tags_to_rm = [t.strip().lstrip("#") for t in args.tags.split(",")]
        current = fm.get("tags") or []
        if isinstance(current, str):
            current = [current]
        current = [t for t in current if t not in tags_to_rm]
        if current:
            fm["tags"] = current
        elif "tags" in fm:
            del fm["tags"]
        p.write_text(rebuild_note(fm, content), encoding="utf-8")
        out({"ok": True, "path": args.path, "tags": current})

    else:
        err(f"Unknown tag operation: {operation}")


def cmd_frontmatter(args, vault):
    p = resolve_path(vault, args.path)
    if not p.exists():
        err(f"Note not found: {args.path}")
    fm, _ = read_note(p)
    out({"path": args.path, "frontmatter": fm})


def cmd_update_frontmatter(args, vault):
    p = resolve_path(vault, args.path)
    if not p.exists():
        err(f"Note not found: {args.path}")

    try:
        new_data = json.loads(args.data)
    except json.JSONDecodeError as e:
        err(f"Invalid JSON: {e}")

    text = p.read_text(encoding="utf-8")
    fm, content = parse_frontmatter(text)

    if args.merge:
        fm.update(new_data)
    else:
        fm = new_data

    p.write_text(rebuild_note(fm, content), encoding="utf-8")
    out({"ok": True, "path": args.path, "frontmatter": fm})


def cmd_stats(args, vault):
    recent_count = args.recent_count or 5
    total_notes = 0
    total_folders = set()
    total_size = 0
    recent = []

    for item in vault.rglob("*"):
        if item.name.startswith(".") or any(p.startswith(".") for p in item.relative_to(vault).parts):
            continue
        if item.is_dir():
            total_folders.add(str(item))
        elif item.is_file():
            if item.suffix == ".md":
                total_notes += 1
            size = item.stat().st_size
            total_size += size
            mtime = item.stat().st_mtime
            rel = str(item.relative_to(vault)).replace("\\", "/")
            recent.append({"path": rel, "modified": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(), "size": size})

    recent.sort(key=lambda x: x["modified"], reverse=True)
    recent = recent[:recent_count]

    out({
        "total_notes": total_notes,
        "total_folders": len(total_folders),
        "total_size_bytes": total_size,
        "total_size_mb": round(total_size / (1024 * 1024), 2),
        "recent": recent
    })


def cmd_all_tags(args, vault):
    tag_counts = {}

    for md_file in vault.rglob("*.md"):
        if any(p.startswith(".") for p in md_file.relative_to(vault).parts):
            continue
        try:
            text = md_file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError):
            continue

        fm, _ = parse_frontmatter(text)
        fm_tags = fm.get("tags") or []
        if isinstance(fm_tags, str):
            fm_tags = [fm_tags]

        inline_tags = find_inline_tags(text)

        for t in set(fm_tags + inline_tags):
            tag_counts[t] = tag_counts.get(t, 0) + 1

    result = [{"tag": t, "count": c} for t, c in tag_counts.items()]
    result.sort(key=lambda x: x["count"], reverse=True)
    out(result)


def cmd_move(args, vault):
    old_p = resolve_path(vault, args.old_path)
    new_p = resolve_path(vault, args.new_path)

    if not old_p.exists():
        err(f"Source not found: {args.old_path}")
    if new_p.exists() and not args.overwrite:
        err(f"Destination exists: {args.new_path}. Use --overwrite to replace.")

    new_p.parent.mkdir(parents=True, exist_ok=True)

    # Get old and new names (without extension, for wikilinks)
    old_name = old_p.stem
    new_name = new_p.stem

    # Move the file
    if args.overwrite:
        old_p.replace(new_p)
    else:
        old_p.rename(new_p)

    # Update wikilinks if name changed (unless explicitly skipped).
    # --no-wikilink-update preserves the existing [[old_name]] pointers, e.g.
    # for D44 rename-then-write supersession where a new file will land at
    # old_name immediately after this move and inbound links should resolve
    # to the new canonical, not the archive.
    links_updated = 0
    skipped = False
    if old_name != new_name:
        if args.no_wikilink_update:
            skipped = True
        else:
            links_updated = update_wikilinks(vault, old_name, new_name)

    out({
        "ok": True,
        "old_path": args.old_path,
        "new_path": args.new_path,
        "wikilinks_updated": links_updated,
        "wikilink_update_skipped": skipped
    })


def cmd_delete(args, vault):
    if not args.confirm:
        err("--confirm flag required for delete")

    p = resolve_path(vault, args.path)
    if not p.exists():
        err(f"Note not found: {args.path}")

    p.unlink()
    out({"ok": True, "deleted": args.path})


def cmd_read_multiple(args, vault):
    paths = args.paths
    if len(paths) > 10:
        err("Maximum 10 files for read-multiple")

    results = []
    for rel in paths:
        p = resolve_path(vault, rel)
        if not p.exists():
            results.append({"path": rel, "error": "Not found"})
            continue
        if not p.is_file():
            results.append({"path": rel, "error": "Not a file"})
            continue
        fm, content = read_note(p)
        results.append({"path": rel, "frontmatter": fm, "content": content})

    out(results)


def cmd_notes_info(args, vault):
    paths = args.paths
    results = []
    for rel in paths:
        p = resolve_path(vault, rel)
        if not p.exists():
            results.append({"path": rel, "error": "Not found"})
            continue
        stat = p.stat()
        fm = {}
        if p.suffix == ".md":
            try:
                fm, _ = read_note(p)
            except Exception:
                pass
        results.append({
            "path": rel,
            "size": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            "created": datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc).isoformat(),
            "frontmatter": fm
        })

    out(results)


def cmd_move_file(args, vault):
    """Move any file (not just .md) and update wikilinks."""
    old_p = resolve_path(vault, args.old_path)
    new_p = resolve_path(vault, args.new_path)

    if not old_p.exists():
        err(f"Source not found: {args.old_path}")
    if new_p.exists() and not args.overwrite:
        err(f"Destination exists: {args.new_path}. Use --overwrite to replace.")

    new_p.parent.mkdir(parents=True, exist_ok=True)

    old_name = old_p.stem
    new_name = new_p.stem

    if args.overwrite:
        old_p.replace(new_p)
    else:
        old_p.rename(new_p)

    links_updated = 0
    skipped = False
    if old_name != new_name:
        if args.no_wikilink_update:
            skipped = True
        else:
            links_updated = update_wikilinks(vault, old_name, new_name)

    out({
        "ok": True,
        "old_path": args.old_path,
        "new_path": args.new_path,
        "wikilinks_updated": links_updated,
        "wikilink_update_skipped": skipped
    })


# ========== CLI ==========

def main():
    # Parent parser with shared --vault flag
    vault_parent = argparse.ArgumentParser(add_help=False)
    vault_parent.add_argument("--vault", type=str, default=None,
                              help="Vault root path (default: auto-detect)")

    parser = argparse.ArgumentParser(
        description="Obsidian vault operations CLI",
        prog="vault_ops"
    )

    sub = parser.add_subparsers(dest="command", help="Command to run")

    # read
    p_read = sub.add_parser("read", help="Read a note", parents=[vault_parent])
    p_read.add_argument("path", help="Note path relative to vault")

    # write
    p_write = sub.add_parser("write", help="Write a note", parents=[vault_parent])
    p_write.add_argument("path", help="Note path relative to vault")
    p_write.add_argument("--content", required=True, help='Note content (use "-" for stdin)')
    p_write.add_argument("--frontmatter", default=None, help="JSON frontmatter")
    p_write.add_argument("--mode", choices=["overwrite", "append", "prepend"], default="overwrite")

    # patch
    p_patch = sub.add_parser("patch", help="String replace in a note", parents=[vault_parent])
    p_patch.add_argument("path", help="Note path relative to vault")
    p_patch.add_argument("--old", required=True, help="String to find")
    p_patch.add_argument("--new", required=True, help="Replacement string")
    p_patch.add_argument("--replace-all", action="store_true", default=False)

    # search
    p_search = sub.add_parser("search", help="Search notes", parents=[vault_parent])
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--content", action="store_true", default=True,
                          help="Search in content (default: true)")
    p_search.add_argument("--no-content", action="store_false", dest="content")
    p_search.add_argument("--frontmatter", action="store_true", default=False,
                          help="Search in frontmatter")
    p_search.add_argument("--limit", type=int, default=5)
    p_search.add_argument("--case-sensitive", action="store_true", default=False)

    # list
    p_list = sub.add_parser("list", help="List files and directories", parents=[vault_parent])
    p_list.add_argument("path", nargs="?", default="", help="Directory path (default: root)")

    # tags
    p_tags = sub.add_parser("tags", help="Manage tags", parents=[vault_parent])
    p_tags.add_argument("path", help="Note path")
    p_tags.add_argument("operation", choices=["add", "remove", "list"])
    p_tags.add_argument("--tags", default=None, help="Comma-separated tags")

    # frontmatter
    p_fm = sub.add_parser("frontmatter", help="Get frontmatter", parents=[vault_parent])
    p_fm.add_argument("path", help="Note path")

    # update-frontmatter
    p_ufm = sub.add_parser("update-frontmatter", help="Update frontmatter", parents=[vault_parent])
    p_ufm.add_argument("path", help="Note path")
    p_ufm.add_argument("--data", required=True, help="JSON data to set/merge")
    p_ufm.add_argument("--merge", action="store_true", default=True)
    p_ufm.add_argument("--no-merge", action="store_false", dest="merge")

    # stats
    p_stats = sub.add_parser("stats", help="Vault statistics", parents=[vault_parent])
    p_stats.add_argument("--recent-count", type=int, default=5)

    # all-tags
    sub.add_parser("all-tags", help="List all tags with counts", parents=[vault_parent])

    # move
    p_move = sub.add_parser("move", help="Move/rename a note (updates wikilinks)", parents=[vault_parent])
    p_move.add_argument("old_path", help="Current path")
    p_move.add_argument("new_path", help="New path")
    p_move.add_argument("--overwrite", action="store_true", default=False)
    p_move.add_argument("--no-wikilink-update", action="store_true", default=False,
                        help="Skip auto-updating inbound [[wikilinks]] from old name to new name. "
                             "Use for D44 rename-then-write supersession where inbound links "
                             "should keep resolving to the slug a NEW canonical will occupy.")

    # delete
    p_del = sub.add_parser("delete", help="Delete a note", parents=[vault_parent])
    p_del.add_argument("path", help="Note path")
    p_del.add_argument("--confirm", action="store_true", default=False, help="Required safety flag")

    # read-multiple
    p_rm = sub.add_parser("read-multiple", help="Batch read (max 10)", parents=[vault_parent])
    p_rm.add_argument("paths", nargs="+", help="Note paths")

    # notes-info
    p_ni = sub.add_parser("notes-info", help="Batch metadata", parents=[vault_parent])
    p_ni.add_argument("paths", nargs="+", help="Note paths")

    # move-file
    p_mf = sub.add_parser("move-file", help="Move any file (updates wikilinks)", parents=[vault_parent])
    p_mf.add_argument("old_path", help="Current path")
    p_mf.add_argument("new_path", help="New path")
    p_mf.add_argument("--overwrite", action="store_true", default=False)
    p_mf.add_argument("--no-wikilink-update", action="store_true", default=False,
                      help="Skip auto-updating inbound [[wikilinks]] from old name to new name. "
                           "Use for D44 rename-then-write supersession where inbound links "
                           "should keep resolving to the slug a NEW canonical will occupy.")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Resolve vault
    try:
        vault = resolve_vault_arg(args.vault)
    except SystemExit as exc:
        err(exc.code)
    vault = vault.resolve()
    if not vault.exists():
        err(f"Vault not found: {vault}")
    if not vault.is_dir():
        err(f"Vault is not a directory: {vault}")

    # Dispatch
    dispatch = {
        "read": cmd_read,
        "write": cmd_write,
        "patch": cmd_patch,
        "search": cmd_search,
        "list": cmd_list,
        "tags": cmd_tags,
        "frontmatter": cmd_frontmatter,
        "update-frontmatter": cmd_update_frontmatter,
        "stats": cmd_stats,
        "all-tags": cmd_all_tags,
        "move": cmd_move,
        "delete": cmd_delete,
        "read-multiple": cmd_read_multiple,
        "notes-info": cmd_notes_info,
        "move-file": cmd_move_file,
    }

    try:
        dispatch[args.command](args, vault)
    except Exception as e:
        err(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
