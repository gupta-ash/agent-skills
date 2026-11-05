#!/usr/bin/env python
"""Tag ontology management — single source of truth from .wiki-schema.md.

Parses the wiki schema to extract tag aliases, domains, topics, descriptors,
and provides normalization + validation functions.

Platform & Vault agnostic: works on Windows/Linux against any vault path passed to --vault.

Usage:
    python tag_ontology.py --vault "/path/to/vault" --dry-run        # Preview tag normalization
    python tag_ontology.py --vault "/path/to/vault"                   # Apply normalization
    python tag_ontology.py --vault "/path/to/vault" --file wiki/tech/ai/page.md  # Single file
    python tag_ontology.py --vault "/path/to/vault" --validate       # Validate only, don't fix
"""

import argparse
import io
import json
import os
import re
import sys
from pathlib import Path

# Force UTF-8 stdout on Windows
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    elif sys.stdout.encoding != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Schema Parsing
# ---------------------------------------------------------------------------

def parse_schema(vault_path):
    """Return the legacy ontology dictionary from canonical schema resolution.

    This adapter keeps normalization consumers' mutable list/dict shape, including
    the retained bare-topic plus domain-qualified single-segment topic entries.
    Schema conflicts propagate rather than silently yielding an empty ontology.
    """
    # Direct CLI execution has scripts/ on sys.path, not its sibling skills root.
    skills_root = str(Path(__file__).resolve().parents[2])
    if skills_root not in sys.path:
        sys.path.insert(0, skills_root)
    from wiki.runtime.schema import resolve_schema

    resolved = resolve_schema(Path(vault_path))
    topics = {}
    for domain, items in resolved.topics.items():
        expanded = []
        for item in items:
            expanded.append(item)
            if "/" not in item:
                expanded.append(f"{domain}/{item}")
        topics[domain] = list(dict.fromkeys(expanded))
    return {
        "aliases": dict(resolved.aliases),
        "domains": list(resolved.domains),
        "topics": topics,
        "descriptors": list(resolved.raw_declarations.get("descriptors", ())),
        "content_types": list(resolved.raw_declarations.get("content_types", ())),
        "status_tags": list(resolved.raw_declarations.get("status_tags", ())),
        "vault_name": resolved.wiki_name,
    }


# ---------------------------------------------------------------------------
# Tag Normalization
# ---------------------------------------------------------------------------

def normalize_tag(tag, schema):
    """Normalize a single tag using the schema.

    Returns:
        str — canonical form of the tag
        None — tag should be removed
    """
    tag = tag.strip().lower()

    # Check aliases first
    if tag in schema["aliases"]:
        return schema["aliases"][tag]  # Could be None (= remove)

    # Check if it's a valid domain, topic, descriptor, content type, or status
    all_valid = set()
    all_valid.update(schema["domains"])
    for topic_list in schema["topics"].values():
        all_valid.update(topic_list)
    all_valid.update(schema["descriptors"])
    all_valid.update(schema["content_types"])
    all_valid.update(schema["status_tags"])

    if tag in all_valid:
        return tag

    # Unknown tag — return as-is (validation will flag it)
    return tag


def normalize_tags(tags, schema):
    """Normalize a list of tags. Deduplicate, sort, apply aliases.

    Args:
        tags: list of tag strings
        schema: parsed schema dict from parse_schema()

    Returns:
        list of normalized, deduplicated, sorted tags
    """
    if not tags:
        return []

    normalized = set()
    for tag in tags:
        result = normalize_tag(tag, schema)
        if result is not None:
            normalized.add(result)

    return sorted(normalized)


def validate_tags(tags, schema):
    """Validate tags against the schema.

    Returns:
        list of violation dicts: [{tag, issue, suggestion}]
    """
    violations = []

    if not tags:
        violations.append({
            "tag": None,
            "issue": "no_tags",
            "suggestion": "Add at least one topic tag",
        })
        return violations

    all_valid = set()
    all_valid.update(schema["domains"])
    for topic_list in schema["topics"].values():
        all_valid.update(topic_list)
    all_valid.update(schema["descriptors"])
    all_valid.update(schema["content_types"])
    all_valid.update(schema["status_tags"])

    for tag in tags:
        tag_lower = tag.strip().lower()

        # Check if it's an alias that should have been normalized
        if tag_lower in schema["aliases"]:
            canonical = schema["aliases"][tag_lower]
            if canonical is None:
                violations.append({
                    "tag": tag,
                    "issue": "should_remove",
                    "suggestion": f"Remove tag '{tag}' (meta tag)",
                })
            else:
                violations.append({
                    "tag": tag,
                    "issue": "unnormalized_alias",
                    "suggestion": f"'{tag}' → '{canonical}'",
                })
        elif tag_lower not in all_valid:
            violations.append({
                "tag": tag,
                "issue": "unknown_tag",
                "suggestion": f"Tag '{tag}' not in schema. Add to .wiki-schema.md or normalize.",
            })

    return violations


# ---------------------------------------------------------------------------
# Frontmatter Parsing (minimal — just enough for tag operations)
# ---------------------------------------------------------------------------

def _parse_frontmatter(content):
    """Parse YAML frontmatter from markdown content.

    Returns: (frontmatter_dict, body_str, raw_frontmatter_str)
    """
    if not content.startswith("---"):
        return {}, content, ""

    end = content.find("\n---", 3)
    if end == -1:
        return {}, content, ""

    raw_fm = content[3:end].strip()
    body = content[end + 4:]

    fm = {}
    current_key = None
    list_values = []

    for line in raw_fm.split("\n"):
        if line.strip().startswith("#"):
            continue

        # Key: value
        m = re.match(r"^(\w[\w-]*)\s*:\s*(.*)", line)
        if m:
            if current_key and list_values:
                fm[current_key] = list_values
                list_values = []

            key = m.group(1)
            val = m.group(2).strip()

            if val == "" or val == "null":
                current_key = key
                fm[key] = None
                continue

            if val.startswith("[") and val.endswith("]"):
                items = [x.strip().strip('"').strip("'") for x in val[1:-1].split(",") if x.strip()]
                fm[key] = items
                current_key = key
                continue

            if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                val = val[1:-1]

            if val.lower() in ("true", "false"):
                fm[key] = val.lower() == "true"
                current_key = key
                continue

            fm[key] = val
            current_key = key
            continue

        # List item
        m = re.match(r"^\s+-\s+(.*)", line)
        if m and current_key:
            val = m.group(1).strip().strip('"').strip("'")
            list_values.append(val)

    if current_key and list_values:
        fm[current_key] = list_values

    return fm, body, raw_fm


def _write_frontmatter(fm, body):
    """Serialize frontmatter dict + body back to markdown."""
    lines = ["---"]

    key_order = [
        "type", "title", "url", "date", "source_type", "source",
        "domain", "topic", "tags",
        "depth", "parent_source", "raw_archive",
        "compiled_into", "related", "unfetched_links",
        "freshness", "last_verified", "human_edited",
        "concept", "sources", "last_compiled",
        "contradictions", "open_questions",
        "entity_type", "mentioned_in",
        "area", "notes", "compilations",
    ]

    written = set()
    for key in key_order:
        if key in fm:
            _write_key(lines, key, fm[key])
            written.add(key)

    for key in sorted(fm.keys()):
        if key not in written:
            _write_key(lines, key, fm[key])

    lines.append("---")
    return "\n".join(lines) + body


def _write_key(lines, key, val):
    """Write a single frontmatter key-value pair."""
    if val is None:
        lines.append(f"{key}:")
    elif isinstance(val, bool):
        lines.append(f"{key}: {'true' if val else 'false'}")
    elif isinstance(val, (int, float)):
        lines.append(f"{key}: {val}")
    elif isinstance(val, list):
        if not val:
            lines.append(f"{key}: []")
        elif len(val) <= 3 and all(isinstance(v, str) and len(v) < 30 for v in val):
            lines.append(f"{key}: [{', '.join(val)}]")
        else:
            lines.append(f"{key}:")
            for item in val:
                lines.append(f"  - {item}")
    elif isinstance(val, str):
        if any(c in val for c in ":#[]{}|>*&!%@`"):
            lines.append(f'{key}: "{val}"')
        else:
            lines.append(f"{key}: {val}")
    else:
        lines.append(f"{key}: {val}")


# ---------------------------------------------------------------------------
# File & Vault Operations
# ---------------------------------------------------------------------------

def normalize_file(filepath, schema, dry_run=False):
    """Normalize tags in a single file's frontmatter.

    Returns:
        dict with: file, original_tags, normalized_tags, changes, violations
    """
    filepath = Path(filepath)
    result = {
        "file": str(filepath),
        "original_tags": [],
        "normalized_tags": [],
        "changes": [],
        "violations": [],
    }

    try:
        with filepath.open("r", encoding="utf-8", newline="") as _fh:
            _raw = _fh.read()
        _crlf = _raw.count("\r\n")
        _lf = _raw.count("\n") - _crlf
        _cr = _raw.count("\r") - _crlf
        _styles = sum(1 for _c in (_crlf, _lf, _cr) if _c)
        if _styles > 1:
            result["error"] = (
                f"SKIP {filepath}: mixed line endings "
                f"(CRLF={_crlf} LF={_lf} CR={_cr}); refusing to rewrite (file NOT modified)"
            )
            return result
        _nl = "\r\n" if _crlf > _lf and _crlf >= _cr else ("\r" if _cr > _lf else "\n")
        content = _raw.replace("\r\n", "\n").replace("\r", "\n")
    except (OSError, UnicodeDecodeError) as e:
        result["error"] = str(e)
        return result

    fm, body, raw_fm = _parse_frontmatter(content)

    tags = fm.get("tags", [])
    if tags is None:
        tags = []
    if isinstance(tags, str):
        tags = [tags]

    result["original_tags"] = list(tags)

    # Normalize
    normalized = normalize_tags(tags, schema)
    result["normalized_tags"] = normalized

    # Track changes
    if set(tags) != set(normalized) or sorted(tags) != normalized:
        for old_tag in tags:
            old_lower = old_tag.strip().lower()
            if old_lower in schema["aliases"]:
                canonical = schema["aliases"][old_lower]
                if canonical is None:
                    result["changes"].append(f"removed: {old_tag}")
                elif old_lower != canonical:
                    result["changes"].append(f"alias: {old_tag} → {canonical}")

        added = set(normalized) - set(t.strip().lower() for t in tags)
        for tag in added:
            result["changes"].append(f"kept/added: {tag}")

    # Validate
    result["violations"] = validate_tags(normalized, schema)

    # Apply if not dry run
    if not dry_run and result["changes"]:
        fm["tags"] = normalized
        new_content = _write_frontmatter(fm, body)
        _out = new_content.replace("\r\n", "\n").replace("\r", "\n")
        with filepath.open("w", encoding="utf-8", newline="") as _fh:
            _fh.write(_out if _nl == "\n" else _out.replace("\n", _nl))

    return result


def normalize_vault(vault_path, dry_run=False, file_filter=None):
    """Normalize all tags across a vault.

    Args:
        vault_path: Path to vault root
        dry_run: If True, don't write changes
        file_filter: Optional relative path to process only one file

    Returns:
        list of result dicts from normalize_file()
    """
    vault = Path(vault_path).resolve()
    schema = parse_schema(vault)

    results = []
    changed = 0
    total = 0
    violations_count = 0

    if file_filter:
        target = vault / file_filter
        if target.exists():
            result = normalize_file(target, schema, dry_run)
            results.append(result)
            if result["changes"]:
                changed += 1
            total += 1
            violations_count += len(result["violations"])
        else:
            results.append({"file": str(target), "error": "File not found"})
    else:
        for search_dir in ["wiki", "mocs"]:
            base = vault / search_dir
            if not base.is_dir():
                continue
            for root, dirs, files in os.walk(base):
                for fn in files:
                    if not fn.endswith(".md"):
                        continue
                    filepath = Path(root) / fn
                    result = normalize_file(filepath, schema, dry_run)
                    results.append(result)
                    if result.get("changes"):
                        changed += 1
                    total += 1
                    violations_count += len(result.get("violations", []))

    return {
        "vault": str(vault),
        "vault_name": schema.get("vault_name", ""),
        "total_files": total,
        "files_changed": changed,
        "total_violations": violations_count,
        "dry_run": dry_run,
        "results": results,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Tag ontology management — normalize and validate wiki tags from .wiki-schema.md"
    )
    parser.add_argument("--vault", required=True, help="Path to vault root")
    parser.add_argument("--file", help="Process only this file (relative to vault)")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing")
    parser.add_argument("--validate", action="store_true", help="Validate only — report violations, don't fix")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--schema-only", action="store_true", help="Just parse and dump the schema")
    args = parser.parse_args()

    vault = Path(args.vault).resolve()

    try:
        schema = parse_schema(vault)
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    # Schema dump mode
    if args.schema_only:
        if args.json:
            print(json.dumps(schema, indent=2))
        else:
            print(f"Vault: {schema['vault_name']}")
            print(f"Domains: {', '.join(schema['domains'])}")
            print(f"Topics: {sum(len(v) for v in schema['topics'].values())} total")
            print(f"Aliases: {len(schema['aliases'])} mappings")
            print(f"Descriptors: {len(schema['descriptors'])}")
            print(f"Content types: {', '.join(schema['content_types'])}")
            print(f"Status tags: {', '.join(schema['status_tags'])}")
        return

    # Validate-only mode
    if args.validate:
        output = normalize_vault(vault, dry_run=True, file_filter=args.file)
        violations = []
        for r in output["results"]:
            for v in r.get("violations", []):
                violations.append({"file": r["file"], **v})

        if args.json:
            print(json.dumps({"vault": str(vault), "violations": violations}, indent=2))
        else:
            if violations:
                print(f"=== Tag Validation Report ===")
                print(f"Vault: {vault}")
                print(f"Files scanned: {output['total_files']}")
                print(f"Violations found: {len(violations)}")
                print()
                for v in violations:
                    print(f"  {v['file']}")
                    print(f"    [{v['issue']}] {v.get('suggestion', '')}")
            else:
                print(f"✓ All tags valid in {vault} ({output['total_files']} files scanned)")
        return

    # Normalize mode
    output = normalize_vault(vault, dry_run=args.dry_run, file_filter=args.file)

    if args.json:
        # Slim down results for JSON output
        slim_results = []
        for r in output["results"]:
            if r.get("changes") or r.get("violations") or r.get("error"):
                slim_results.append(r)
        output["results"] = slim_results
        print(json.dumps(output, indent=2))
    else:
        mode = "DRY RUN" if args.dry_run else "APPLIED"
        print(f"=== Tag Normalization [{mode}] ===")
        print(f"Vault: {vault} ({output.get('vault_name', '')})")
        print(f"Files scanned: {output['total_files']}")
        print(f"Files {'would change' if args.dry_run else 'changed'}: {output['files_changed']}")
        print(f"Violations remaining: {output['total_violations']}")
        print()

        for r in output["results"]:
            if r.get("error"):
                print(f"  ERROR: {r['file']} — {r['error']}")
            elif r.get("changes"):
                print(f"  {r['file']}:")
                for change in r["changes"]:
                    print(f"    {change}")
            elif r.get("violations"):
                print(f"  {r['file']}:")
                for v in r["violations"]:
                    print(f"    ⚠ [{v['issue']}] {v.get('suggestion', '')}")


if __name__ == "__main__":
    main()
