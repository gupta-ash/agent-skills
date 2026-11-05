#!/usr/bin/env python
"""
vault_search.py — Enhanced Obsidian vault search CLI.
Full-text ranked search, tag search, link graph queries, orphan detection.
"""

import argparse
import json
import os
import re
import sys
from collections import defaultdict
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


def _ensure_utf8_stdout():
    """Force stdout to UTF-8 on Windows to avoid charmap errors."""
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")


def out(data):
    _ensure_utf8_stdout()
    print(json.dumps(data, ensure_ascii=False, default=str))


def err(msg):
    _ensure_utf8_stdout()
    print(json.dumps({"error": str(msg)}, ensure_ascii=False))
    sys.exit(1)


def parse_frontmatter(text: str):
    """Parse YAML frontmatter. Returns (dict, content)."""
    if not text.startswith("---"):
        return {}, text

    end_match = re.search(r'\n---\s*\n', text[3:])
    if not end_match:
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
            fm = {}
    else:
        fm = {}

    return fm, content


def find_inline_tags(text: str) -> list:
    """Find inline #tags in content (outside code blocks)."""
    _, content = parse_frontmatter(text)
    content = re.sub(r'```[\s\S]*?```', '', content)
    content = re.sub(r'`[^`]*`', '', content)
    return list(set(re.findall(r'(?<!\w)#([\w][\w/-]*)', content)))


def get_all_tags(text: str) -> set:
    """Get all tags from a note (frontmatter + inline). Defensively drops
    non-string entries so malformed frontmatter (template placeholders
    parsed as dicts, nested structures, etc.) doesn't crash the search."""
    fm, _ = parse_frontmatter(text)
    fm_tags = fm.get("tags") or []
    if isinstance(fm_tags, str):
        fm_tags = [fm_tags]
    elif not isinstance(fm_tags, list):
        fm_tags = []
    fm_tags = [t for t in fm_tags if isinstance(t, str)]
    inline = find_inline_tags(text)
    return set(fm_tags + inline)


def extract_wikilinks(text: str) -> list:
    """Extract all wikilink targets from text. Returns list of note names."""
    # Match [[target]] and [[target|alias]]
    matches = re.findall(r'\[\[([^\]|]+)(?:\|[^\]]*)?\]\]', text)
    return list(set(matches))


def iter_notes(vault: Path):
    """Iterate over all .md files in vault, yielding (relative_path, full_path)."""
    for md_file in vault.rglob("*.md"):
        if any(p.startswith(".") for p in md_file.relative_to(vault).parts):
            continue
        rel = str(md_file.relative_to(vault)).replace("\\", "/")
        yield rel, md_file


# ========== COMMANDS ==========

def cmd_text(args, vault):
    """Full-text search ranked by match count."""
    query = args.query.lower()
    limit = args.limit or 10
    results = []

    for rel, md_file in iter_notes(vault):
        try:
            text = md_file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError):
            continue

        # Count occurrences (case-insensitive)
        count = text.lower().count(query)
        if count == 0:
            continue

        # Gather matching lines with context
        lines = text.split("\n")
        matching_lines = []
        for i, line in enumerate(lines):
            if query in line.lower():
                matching_lines.append({
                    "line": i + 1,
                    "text": line.strip()[:200]
                })
                if len(matching_lines) >= 5:  # Cap context lines per file
                    break

        results.append({
            "path": rel,
            "score": count,
            "matches": matching_lines
        })

    # Sort by score descending
    results.sort(key=lambda x: x["score"], reverse=True)
    results = results[:limit]

    out(results)


def cmd_tag(args, vault):
    """Find notes by tags."""
    search_tags = set(t.lstrip("#") for t in args.tags)
    mode = args.mode or "any"
    results = []

    for rel, md_file in iter_notes(vault):
        try:
            text = md_file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError):
            continue

        note_tags = get_all_tags(text)

        if mode == "any":
            matched = search_tags & note_tags
            if matched:
                results.append({"path": rel, "matched_tags": sorted(matched), "all_tags": sorted(note_tags)})
        elif mode == "all":
            if search_tags.issubset(note_tags):
                results.append({"path": rel, "matched_tags": sorted(search_tags), "all_tags": sorted(note_tags)})

    results.sort(key=lambda x: x["path"])
    out(results)


def cmd_links_to(args, vault):
    """Find all notes that link TO the given note (backlinks)."""
    target = args.note_name
    # Also try without extension
    target_stem = Path(target).stem if "." in target else target

    # Pattern: [[target]] or [[target|alias]] — match by stem name
    pattern = re.compile(
        r'\[\[(?:[^|\]]*/)?' + re.escape(target_stem) + r'(?:\|[^\]]*)?\]\]',
        re.IGNORECASE
    )

    results = []
    for rel, md_file in iter_notes(vault):
        try:
            text = md_file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError):
            continue

        matches = pattern.findall(text)
        if matches:
            results.append({
                "path": rel,
                "link_count": len(matches),
                "links": matches[:10]
            })

    results.sort(key=lambda x: x["link_count"], reverse=True)
    out(results)


def cmd_links_from(args, vault):
    """Find all outbound wikilinks FROM a note."""
    # Find the note - try exact path first, then search by name
    note_name = args.note_name
    target_path = None

    # Try as a direct path
    candidate = vault / note_name
    if candidate.exists():
        target_path = candidate
    else:
        # Try with .md extension
        candidate = vault / (note_name + ".md")
        if candidate.exists():
            target_path = candidate
        else:
            # Search for it
            for rel, md_file in iter_notes(vault):
                if md_file.stem == note_name or rel == note_name:
                    target_path = md_file
                    break

    if not target_path or not target_path.exists():
        err(f"Note not found: {note_name}")

    target_path = target_path.resolve()
    if not target_path.is_relative_to(vault.resolve()):
        err(f"Path escapes vault: {note_name}")

    text = target_path.read_text(encoding="utf-8")
    links = extract_wikilinks(text)

    out({
        "path": str(target_path.relative_to(vault)).replace("\\", "/"),
        "outbound_links": sorted(links),
        "count": len(links)
    })


def cmd_orphans(args, vault):
    """Find notes with zero inbound wikilinks."""
    # Build a map of all note stems
    all_notes = {}  # stem -> relative path
    for rel, md_file in iter_notes(vault):
        all_notes[md_file.stem] = rel

    # Count inbound links for each note
    inbound_counts = defaultdict(int)

    for rel, md_file in iter_notes(vault):
        try:
            text = md_file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError):
            continue

        links = extract_wikilinks(text)
        for link in links:
            # Links can be paths like "folder/note" or just "note"
            link_stem = Path(link).stem if "/" in link else link
            # Normalize: strip .md if present
            if link_stem.endswith(".md"):
                link_stem = link_stem[:-3]
            inbound_counts[link_stem] += 1

    # Find orphans
    orphans = []
    for stem, rel in all_notes.items():
        if inbound_counts.get(stem, 0) == 0:
            orphans.append({"path": rel, "name": stem})

    orphans.sort(key=lambda x: x["path"])
    out({"orphans": orphans, "count": len(orphans)})


# ========== CLI ==========

def main():
    vault_parent = argparse.ArgumentParser(add_help=False)
    vault_parent.add_argument("--vault", type=str, default=None,
                              help="Vault root path (default: auto-detect)")

    parser = argparse.ArgumentParser(
        description="Enhanced Obsidian vault search",
        prog="vault_search"
    )

    sub = parser.add_subparsers(dest="command", help="Command to run")

    # text
    p_text = sub.add_parser("text", help="Full-text search ranked by relevance", parents=[vault_parent])
    p_text.add_argument("query", help="Search query")
    p_text.add_argument("--limit", type=int, default=10)

    # tag
    p_tag = sub.add_parser("tag", help="Find notes by tags", parents=[vault_parent])
    p_tag.add_argument("tags", nargs="+", help="Tags to search for")
    p_tag.add_argument("--mode", choices=["any", "all"], default="any",
                       help="any=match any tag, all=match all tags")

    # links-to
    p_lt = sub.add_parser("links-to", help="Find notes linking TO this note", parents=[vault_parent])
    p_lt.add_argument("note_name", help="Note name or path")

    # backlinks (alias for links-to)
    p_bl = sub.add_parser("backlinks", help="Find backlinks (alias for links-to)", parents=[vault_parent])
    p_bl.add_argument("note_name", help="Note name or path")

    # links-from
    p_lf = sub.add_parser("links-from", help="Find outbound links FROM a note", parents=[vault_parent])
    p_lf.add_argument("note_name", help="Note name or path")

    # orphans
    sub.add_parser("orphans", help="Find notes with zero inbound links", parents=[vault_parent])

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    try:
        vault = resolve_vault_arg(args.vault)
    except SystemExit as exc:
        err(exc.code)
    vault = vault.resolve()
    if not vault.exists():
        err(f"Vault not found: {vault}")
    if not vault.is_dir():
        err(f"Vault is not a directory: {vault}")

    dispatch = {
        "text": cmd_text,
        "tag": cmd_tag,
        "links-to": cmd_links_to,
        "backlinks": cmd_links_to,  # alias
        "links-from": cmd_links_from,
        "orphans": cmd_orphans,
    }

    try:
        dispatch[args.command](args, vault)
    except Exception as e:
        err(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
