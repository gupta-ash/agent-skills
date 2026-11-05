"""Read-only control planning and journalled, expected-preimage application.

Templates are declarations, not directory-creation requests. Callers own the
inline maintenance run and Store lock; only PublicationManager touches the vault.
"""
from __future__ import annotations

import argparse
import base64
import binascii
from dataclasses import dataclass, replace
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys
import uuid
from collections.abc import Mapping

# Direct execution resolves only the installed skill's file-relative siblings.
if __package__ in (None, ""):
    _skills = Path(__file__).resolve().parents[2]
    if not (_skills / "_workflow" / "store.py").is_file():
        raise SystemExit("Installed sibling _workflow package is unavailable")
    sys.path.insert(0, str(_skills))
    __package__ = "wiki.runtime"

import yaml

from . import schema
from .schema import ResolvedVault, SchemaError


@dataclass(frozen=True)
class ControlChange:
    path: Path
    expected_sha256: str | None
    content: bytes
    role: str
    region: str | None


@dataclass(frozen=True)
class ControlGuard:
    path: Path
    sha256: str | None


@dataclass(frozen=True)
class ControlPlan:
    root: Path
    changes: tuple[ControlChange, ...]
    receipts: tuple[dict, ...]
    schema_sha256: str | None
    guards: tuple[ControlGuard, ...] = ()
    selection_key: str | None = None
    reference_paths: tuple[Path, ...] = ()
    raw_metadata: tuple[tuple[str, str, int, int], ...] = ()
    preservation_metadata: tuple[tuple[str, str, int, int], ...] | None = None


_ROLES = {"hub": "navigation", "index": "catalog", "log": "operations", "verify_log": "verification"}
_CONTROL_KEYS = {"workflow_controls_version", "distillation_path_template", "content_roots", "index_file",
                 "canonical_files", "allowed_tiers", "raw_modalities", "allowed_provenance_classes",
                 "outbound_archiving", "managed_navigation", "hub_file", "strategy_path_template",
                 "compilation_path_template", "book_paths"}


def _hash(data):
    return sha256(data).hexdigest()


def _read(path):
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise SchemaError("SCHEMA_PATH_OUTSIDE_VAULT", "Control paths cannot be links", {"path": str(path)})
    if path.exists() and not path.is_file():
        raise SchemaError("CONTROL_NOT_REGULAR", "Control target is not a regular file", {"path": str(path)})
    return path.read_bytes() if path.exists() else None


def _plain(value):
    if isinstance(value, Mapping):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _frontmatter(data):
    text = data.decode("utf-8-sig")
    if not re.match(r"\A---\r?\n", text):
        return {}
    match = re.match(r"\A---\r?\n(.*?)\r?\n(?:---|\.\.\.)(?:\r?\n|$)", text, re.S)
    if match is None:
        raise SchemaError("SCHEMA_INVALID_YAML", "Unclosed control frontmatter")
    result = schema._load(match[1], "control frontmatter") or {}
    if not isinstance(result, dict):
        raise SchemaError("SCHEMA_INVALID_DECLARATION", "Control frontmatter must be a mapping")
    return result


def is_protected(path: Path, resolved: ResolvedVault | None = None) -> bool:
    path = Path(path).absolute()
    if resolved is not None:
        for role, relative in resolved.canonical_files.items():
            if role not in {"schema", "manifest", *_ROLES} and relative is not None and path == resolved.root / relative:
                return True
    data = _read(path)
    if data is None:
        return False
    if path.suffix.lower() == ".json":
        try:
            metadata = json.loads(data)
        except (ValueError, UnicodeError) as exc:
            raise SchemaError("CONTROL_MANIFEST_INVALID", "Cannot inspect invalid JSON protection metadata") from exc
        if not isinstance(metadata, dict):
            raise SchemaError("CONTROL_MANIFEST_INVALID", "JSON control must be an object")
    elif path.suffix.lower() == ".md":
        metadata = _frontmatter(data)
    else:
        return False
    flag = metadata.get("human_edited", False)
    # Ambiguous human flags fail closed, including quoted true/anchored values.
    return (flag is not False and flag is not None) or metadata.get("pipeline_id") == "hand-curated"


def _region(data, name, body, *, append):
    begin = f"<!-- ash_wiki:begin {name} -->".encode()
    end = f"<!-- ash_wiki:end {name} -->".encode()
    if data.count(begin) != data.count(end) or data.count(begin) > 1 or (begin in data and data.index(begin) > data.index(end)):
        raise SchemaError("CONTROL_MARKER_CONFLICT", "Unbalanced or duplicate managed markers", {"region": name})
    newline = b"\r\n" if b"\r\n" in data else b"\n"
    payload = begin + newline + body.replace(b"\r\n", b"\n").replace(b"\n", newline).rstrip(b"\r\n") + newline + end
    if begin in data:
        start, stop = data.index(begin), data.index(end) + len(end)
        return data[:start] + payload + data[stop:]
    if not append:
        raise SchemaError("VAULT_CONTROLS_NOT_ENROLLED", "Markerless control requires explicit adoption", {"region": name})
    return data + (b"" if not data else (newline if data.endswith(b"\n") else newline * 2)) + payload + newline


def _owned(data, name):
    begin, end = f"<!-- ash_wiki:begin {name} -->".encode(), f"<!-- ash_wiki:end {name} -->".encode()
    # Validate even when preserving the existing managed body.
    _region(data, name, b"", append=True)
    return data.split(begin, 1)[1].split(end, 1)[0].strip(b"\r\n") if begin in data else None


def _validate_controls(root, values):
    unknown = set(values) - _CONTROL_KEYS
    if unknown:
        raise SchemaError("SCHEMA_INVALID_DECLARATION", "Unsupported control overrides", {"fields": sorted(unknown)})
    if type(values.get("workflow_controls_version", 1)) is not int or values.get("workflow_controls_version", 1) != 1:
        raise SchemaError("SCHEMA_INVALID_DECLARATION", "Unknown controls version")
    for key in ("distillation_path_template", "compilation_path_template", "strategy_path_template"):
        if key in values:
            schema._relative(root, values[key], key, template=True)
            schema._fields(values[key])
    for key, allowed in (("content_roots", None), ("allowed_tiers", "ABC"), ("raw_modalities", None), ("allowed_provenance_classes", None)):
        if key in values:
            items = schema._strings(values[key], key, allowed)
            if key == "content_roots":
                for item in items:
                    schema._relative(root, item, key)
            if key == "raw_modalities" and any(not re.fullmatch(r"[a-z][a-z0-9-]*", item) for item in items):
                raise SchemaError("SCHEMA_INVALID_DECLARATION", "Invalid raw modality")
    if "outbound_archiving" in values and type(values["outbound_archiving"]) is not bool:
        raise SchemaError("SCHEMA_INVALID_DECLARATION", "outbound_archiving must be boolean")
    for key in ("canonical_files", "managed_navigation"):
        if key in values:
            if not isinstance(values[key], Mapping):
                raise SchemaError("SCHEMA_INVALID_DECLARATION", f"{key} must be a mapping")
            for role, value in values[key].items():
                if not isinstance(role, str) or not isinstance(value, str):
                    raise SchemaError("SCHEMA_INVALID_DECLARATION", f"Invalid {key}")
                schema._relative(root, role if key == "managed_navigation" else value, key)
                if key == "managed_navigation" and value not in _ROLES.values():
                    raise SchemaError("SCHEMA_INVALID_DECLARATION", "Invalid navigation region")
                if key == "canonical_files" and role in {"schema", "manifest"} and value != {"schema": ".wiki-schema.md", "manifest": "raw/_manifest.json"}[role]:
                    raise SchemaError("CONTROL_PATH_CONFLICT", "Fixed schema/manifest roles cannot be redirected")
    for key in ("hub_file", "index_file"):
        if key in values and not (key == "index_file" and values[key] is None):
            schema._relative(root, values[key], key)
    if "book_paths" in values:
        book = values["book_paths"]
        if not isinstance(book, Mapping):
            raise SchemaError("SCHEMA_INVALID_DECLARATION", "book_paths must be a mapping")
        if book.get("slug_by", "title") not in ("title", "author", None):
            raise SchemaError("SCHEMA_INVALID_DECLARATION", "Invalid book slug policy")
        for key, value in book.items():
            if key != "slug_by" and value is not None:
                schema._relative(root, value, "book_paths." + key, template=True)


def _raw_frontmatter(path):
    # Inspect the metadata prefix only, never the source body or binary assets.
    with path.open("rb") as stream:
        first = stream.readline()
        if first.lstrip(b"\xef\xbb\xbf").strip() != b"---":
            return {}
        lines = [first]
        for line in stream:
            lines.append(line)
            if line.strip() in (b"---", b"..."):
                return _frontmatter(b"".join(lines))
    raise SchemaError("SCHEMA_INVALID_YAML", "Raw metadata frontmatter is unclosed", {"path": str(path)})


def _raw_inventory(root):
    records, metadata = [], []
    raw = root / "raw"
    if raw.is_symlink() or (hasattr(raw, "is_junction") and raw.is_junction()):
        raise SchemaError("SCHEMA_PATH_OUTSIDE_VAULT", "Raw root cannot be a link")
    if not raw.exists():
        return records, tuple(metadata)
    for path in sorted(_walk_preserved(raw)):
        if path == raw / "_manifest.json":
            continue
        relative = path.relative_to(root).as_posix()
        schema._relative(root, relative, "raw inventory")
        stat = path.stat()
        metadata.append((relative, "directory" if path.is_dir() else "file", stat.st_size, stat.st_mtime_ns))
        if not path.is_file():
            continue
        record = {"path": relative, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
                  "classification": "asset", "acceptance": "unreviewed"}
        if path.suffix.lower() == ".md":
            front = _raw_frontmatter(path)
            if front.get("type") == "source":
                record["classification"] = "source_note"
            elif front.get("type") in ("moc", "index") or path.name in ("_index.md", "index.md"):
                record["classification"] = "navigation"
            else:
                record["classification"] = "unknown_note"
            observed = {key: front[key] for key in ("source_id", "source_class", "provenance_class", "content_hash", "source_sha256", "source_hash", "source_hash_kind", "source_hash_algorithm", "source_hash_authority") if key in front}
            if observed:
                record["observed_provenance"] = observed
        records.append(record)
    return records, tuple(metadata)


def _merge_missing(target, supplied, prefix=""):
    for key, value in supplied.items():
        name = prefix + str(key)
        if key not in target:
            target[key] = _plain(value)
        elif isinstance(value, Mapping) and isinstance(target[key], dict):
            _merge_missing(target[key], value, name + ".")
        elif not schema._equivalent(target[key], _plain(value)):
            raise SchemaError("SCHEMA_DECLARATION_CONFLICT", f"Conflicting explicit control: {name}", {"field": name})


def prepare_controls(resolved: ResolvedVault, overrides: dict, *, adopt_existing: bool = False) -> ControlPlan:
    if not isinstance(overrides, dict):
        raise SchemaError("SCHEMA_INVALID_DECLARATION", "Overrides must be a mapping")
    root = resolved.root
    schema_path = root / ".wiki-schema.md"
    original = _read(schema_path)
    if original is None or _hash(original) != resolved.constraints_ref.sha256:
        raise SchemaError("CONTROL_PREIMAGE_CONFLICT", "Schema changed since resolution")
    if not resolved.controls_enrolled and not adopt_existing:
        raise SchemaError("VAULT_CONTROLS_NOT_ENROLLED", "Explicit read-only adoption preparation required", {"root": str(root)})
    _validate_controls(root, overrides)
    declarations = _plain(resolved.raw_declarations)
    _merge_missing(declarations, overrides)
    defaults = {"workflow_controls_version": 1,
                "distillation_path_template": resolved.output_templates["distillation_path_template"],
                "content_roots": [p.relative_to(root).as_posix() + "/" for p in resolved.read_roots],
                "index_file": resolved.canonical_files["index"],
                "canonical_files": {}, "allowed_tiers": list(resolved.allowed_tiers),
                "raw_modalities": list(resolved.raw_modalities),
                "allowed_provenance_classes": list(resolved.allowed_provenance_classes),
                "outbound_archiving": True, "managed_navigation": {}}
    for key, value in defaults.items():
        declarations.setdefault(key, value)
    _validate_controls(root, {key: value for key, value in declarations.items() if key in _CONTROL_KEYS})
    references = tuple(root / relative for role, relative in declarations.get("canonical_files", {}).items()
                       if role not in {"schema", "manifest", *_ROLES} and relative is not None)
    if any(path in {root / ".wiki-schema.md", root / "raw/_manifest.json"} for path in references):
        raise SchemaError("CONTROL_PATH_CONFLICT", "Reference roles cannot overlap schema or raw manifest")
    for role, relative in declarations.get("canonical_files", {}).items():
        if role in {"hub", "index", "log", "verify_log"}:
            expected = {"hub": declarations.get("hub_file", resolved.canonical_files["hub"]),
                        "index": declarations.get("index_file"), "log": "log.md", "verify_log": "verify-log.md"}[role]
            if relative != expected:
                raise SchemaError("SCHEMA_DECLARATION_CONFLICT", "Conflicting canonical control role", {"role": role})
    receipts, changes, guards = [], [], []
    navigation = dict(declarations.get("managed_navigation", {}))
    paths = {"hub": declarations.get("hub_file", resolved.canonical_files["hub"]),
             "index": declarations.get("index_file"), "log": "log.md", "verify_log": "verify-log.md"}
    paths.update({role: value for role, value in declarations.get("canonical_files", {}).items() if role in _ROLES})
    allowed = {relative: _ROLES[role] for role, relative in paths.items() if relative is not None}
    if len(allowed) != len([p for p in paths.values() if p is not None]):
        raise SchemaError("CONTROL_PATH_CONFLICT", "Canonical control roles must have distinct paths")
    for relative, region in navigation.items():
        if allowed.get(relative) != region:
            raise SchemaError("CONTROL_PATH_CONFLICT", "Navigation enrollment is not an allowed control role", {"path": relative})
    for role, relative in paths.items():
        if relative is None:
            continue
        path = schema._relative(root, relative, role)
        if path in references or relative.startswith("raw/") or path == schema_path:
            raise SchemaError("CONTROL_PATH_CONFLICT", "Control overlaps a protected reference or evidence", {"path": relative})
        data = _read(path)
        guards.append(ControlGuard(path, _hash(data) if data is not None else None))
        if is_protected(path, resolved) or path in references:
            receipts.append({"path": relative, "role": role, "status": "skipped-protected", "proposal": {"region": _ROLES[role]}})
            # Existing enrollment does not override protection; retain its declaration.
            continue
        if not path.parent.is_dir():
            raise SchemaError("CONTROL_DIRECTORY_UNAVAILABLE", "Control adoption does not scaffold corpus directories", {"path": relative})
        body = _owned(data or b"", _ROLES[role])
        if data is not None and body is None and not adopt_existing:
            raise SchemaError("VAULT_CONTROLS_NOT_ENROLLED", "Existing control has no enrolled region", {"path": relative})
        if data is not None and body is not None and navigation.get(relative) != _ROLES[role] and not adopt_existing:
            raise SchemaError("VAULT_CONTROLS_NOT_ENROLLED", "Control region is not enrolled", {"path": relative})
        navigation[relative] = _ROLES[role]
        if body is None:
            body = {"hub": b"## Accepted knowledge\n\nNo accepted artifacts have been registered by this control adoption.",
                    "index": b"## Workflow catalog\n\nNo accepted artifacts have been registered by this control adoption.",
                    "log": b"## Workflow operations\n\nNo workflow operations recorded in this region.",
                    "verify_log": b"## Workflow verification\n\nNo verification has been performed by this control adoption."}[role]
        content = _region(data or b"", _ROLES[role], body, append=adopt_existing or data is None)
        if content != data:
            changes.append(ControlChange(path, _hash(data) if data is not None else None, content, role, _ROLES[role]))
    declarations["managed_navigation"] = navigation
    # Retain unknown keys already inside the owned region. Never duplicate an
    # equivalent declaration outside it; YAML merging detects genuine conflicts.
    existing_body = _owned(original, "controls")
    owned = {}
    if existing_body is not None:
        match = re.fullmatch(rb"```(?:yaml|yml)(?: controls)?\s*\r?\n(.*?)\r?\n```", existing_body, re.S)
        if match is None:
            raise SchemaError("CONTROL_MARKER_CONFLICT", "Controls region requires one YAML block")
        owned = schema._load(match[1].decode("utf-8"), "owned controls") or {}
    outside = original
    if existing_body is not None:
        begin, end = b"<!-- ash_wiki:begin controls -->", b"<!-- ash_wiki:end controls -->"
        outside = original[:original.index(begin)] + original[original.index(end) + len(end):]
    outside_declarations = schema._parse(outside.decode("utf-8-sig"))
    for key in sorted(_CONTROL_KEYS):
        if key not in declarations:
            continue
        value = declarations[key]
        if key in outside_declarations:
            if key == "managed_navigation":
                additions = {k: v for k, v in value.items() if k not in outside_declarations[key]}
                if additions:
                    owned[key] = additions
            elif isinstance(value, dict) and isinstance(outside_declarations[key], dict):
                additions = {k: v for k, v in value.items() if k not in outside_declarations[key]}
                if additions:
                    owned[key] = additions
        else:
            owned[key] = value
    # The enrollment version must live in the owned block even if equivalently
    # declared elsewhere: it is the canonical parser's explicit ownership proof.
    owned["workflow_controls_version"] = 1
    content = _region(original, "controls", ("```yaml controls\n" + yaml.safe_dump(owned, sort_keys=False, allow_unicode=True) + "```").encode("utf-8"), append=adopt_existing)
    schema._parse(content.decode("utf-8-sig"))
    if content != original:
        if is_protected(schema_path, resolved):
            raise SchemaError("VAULT_CONTROLS_NOT_ENROLLED", "Protected schema cannot acquire or change controls", {"status": "skipped-protected"})
        changes.insert(0, ControlChange(schema_path, _hash(original), content, "schema", "controls"))
    records, metadata = _raw_inventory(root)
    manifest_path = root / "raw/_manifest.json"
    manifest_data = _read(manifest_path)
    try:
        manifest = json.loads(manifest_data) if manifest_data is not None else {}
    except (ValueError, UnicodeError) as exc:
        raise SchemaError("CONTROL_MANIFEST_INVALID", "Raw manifest is not valid JSON") from exc
    if not isinstance(manifest, dict):
        raise SchemaError("CONTROL_MANIFEST_INVALID", "Raw manifest must be an object")
    namespace = manifest.get("wiki_workflow", {})
    if not isinstance(namespace, dict):
        raise SchemaError("CONTROL_MANIFEST_CONFLICT", "wiki_workflow namespace must be an object")
    namespace = dict(namespace)
    if "version" in namespace and (type(namespace["version"]) is not int or namespace["version"] != 1):
        raise SchemaError("CONTROL_MANIFEST_CONFLICT", "Unsupported workflow manifest version")
    namespace.setdefault("version", 1)
    namespace.setdefault("artifacts", [])
    namespace.setdefault("inventory", records)
    namespace.setdefault("inventory_diagnostics", {"verification": "not_performed", "preservation": "metadata-only", "source_hashes": "observed-provenance-only"})
    manifest["wiki_workflow"] = namespace
    if is_protected(manifest_path, resolved):
        receipts.append({"path": "raw/_manifest.json", "role": "manifest", "status": "skipped-protected", "proposal": {"namespace": "wiki_workflow"}})
    elif manifest_data is None or json.loads(manifest_data) != manifest:
        changes.append(ControlChange(manifest_path, _hash(manifest_data) if manifest_data is not None else None,
                                     (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode(), "manifest", None))
    guards.append(ControlGuard(schema_path, _hash(original)))
    guards.append(ControlGuard(manifest_path, _hash(manifest_data) if manifest_data is not None else None))
    for path in references:
        data = _read(path)
        guards.append(ControlGuard(path, _hash(data) if data is not None else None))
        receipts.append({"path": path.relative_to(root).as_posix(), "status": "skipped-protected", "role": "reference"})
    return _bind_preservation(ControlPlan(root, tuple(changes), tuple(receipts), _hash(original), tuple(guards), resolved.selection_key, references, metadata))


def prepare_initialization(vault_root: Path, wiki_name: str, domains: tuple[str, ...], *, profile: dict | None = None) -> ControlPlan:
    return _initialization(vault_root, wiki_name, domains, profile=profile, require_empty=True)


def _initialization(vault_root, wiki_name, domains, *, profile=None, require_empty):
    root = Path(vault_root).absolute()
    if root.is_symlink() or not root.is_dir() or (require_empty and any(root.iterdir())):
        raise SchemaError("VAULT_NOT_EMPTY", "Initialization requires an existing genuinely empty directory")
    schema._relative(root, ".wiki-schema.md", "schema")
    root = root.resolve()
    current = Path(schema.__file__).resolve().parents[1] / "canonical-schema.md"
    if schema._resource_version(current.read_bytes()) != "3.1":
        raise SchemaError("SCHEMA_VERSION_UNAVAILABLE", "Initialization requires the shipped canonical3.1 resource")
    if not isinstance(wiki_name, str) or not wiki_name.strip():
        raise SchemaError("SCHEMA_INVALID_DECLARATION", "wiki_name must be nonempty")
    schema._strings(domains, "domains")
    supplied = {} if profile is None else _plain(profile)
    if not isinstance(supplied, dict):
        raise SchemaError("SCHEMA_INVALID_DECLARATION", "Profile must be a mapping")
    _validate_controls(root, supplied)
    values = {"workflow_controls_version": 1, "distillation_path_template": schema._DEFAULT_TEMPLATES["distillation_path_template"],
              "content_roots": ["raw/", "wiki/"], "index_file": None, "canonical_files": {},
              "allowed_tiers": ["A", "B", "C"], "raw_modalities": list(schema._MODALITIES),
              "allowed_provenance_classes": list(schema._PROVENANCE), "outbound_archiving": True}
    values.update(supplied)
    paths = {"hub": values.get("hub_file", "_hub.md"), "index": values["index_file"], "log": "log.md", "verify_log": "verify-log.md"}
    paths.update({key: value for key, value in values["canonical_files"].items() if key in _ROLES})
    if len(set(p for p in paths.values() if p is not None)) != len([p for p in paths.values() if p is not None]):
        raise SchemaError("CONTROL_PATH_CONFLICT", "Control paths must be distinct")
    references = tuple(root / value for key, value in values["canonical_files"].items() if key not in {"schema", "manifest", *_ROLES})
    if any(path in {root / ".wiki-schema.md", root / "raw/_manifest.json"} for path in references):
        raise SchemaError("CONTROL_PATH_CONFLICT", "Reference roles cannot overlap schema or manifest")
    for role, relative in values["canonical_files"].items():
        if role in _ROLES:
            expected = {"hub": values.get("hub_file", "_hub.md"), "index": values["index_file"], "log": "log.md", "verify_log": "verify-log.md"}[role]
            if relative != expected:
                raise SchemaError("SCHEMA_DECLARATION_CONFLICT", "Conflicting canonical control role")
    navigation = {value: _ROLES[key] for key, value in paths.items() if value is not None}
    if "managed_navigation" in supplied and supplied["managed_navigation"] != navigation:
        raise SchemaError("CONTROL_PATH_CONFLICT", "Initial navigation must match control roles")
    values["managed_navigation"] = navigation
    metadata = {"wiki_name": wiki_name, "extends": "canonical-schema.md", "extends_version": "3.1", "domains": list(domains)}
    original = ("---\n" + yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True) + "---\n").encode()
    content = _region(original, "controls", ("```yaml controls\n" + yaml.safe_dump(values, sort_keys=False, allow_unicode=True) + "```").encode(), append=True)
    schema._parse(content.decode())
    changes = [ControlChange(root / ".wiki-schema.md", None, content, "schema", "controls")]
    for role, relative in paths.items():
        if relative is None:
            continue
        path = schema._relative(root, relative, role)
        if path.parent != root:
            raise SchemaError("CONTROL_DIRECTORY_UNAVAILABLE", "Initialization does not scaffold corpus directories")
        if relative.startswith("raw/") or root / relative in references or relative == ".wiki-schema.md":
            raise SchemaError("CONTROL_PATH_CONFLICT", "Control path overlaps evidence or reference")
        body = b"No verification has been performed." if role == "verify_log" else b"No accepted artifacts or workflow operations recorded."
        changes.append(ControlChange(root / relative, None, _region(b"", _ROLES[role], body, append=True), role, _ROLES[role]))
    manifest = {"wiki_workflow": {"version": 1, "artifacts": [], "inventory": [], "inventory_diagnostics": {"verification": "not_performed", "preservation": "metadata-only", "source_hashes": "observed-provenance-only"}}}
    changes.append(ControlChange(root / "raw/_manifest.json", None, (json.dumps(manifest, indent=2) + "\n").encode(), "manifest", None))
    return _bind_preservation(ControlPlan(root, tuple(changes), (), None, tuple(ControlGuard(c.path, None) for c in changes), reference_paths=references))


def validate_control_guards(plan, publication=None, run_id=None, operation_id=None) -> None:
    """Check pending preimages and exact, previously journalled desired effects."""
    changes = {change.path: change for change in plan.changes}
    if len(changes) != len(plan.changes):
        raise SchemaError("CONTROL_PLAN_INVALID", "Duplicate control targets")
    if plan.schema_sha256 is None:
        for path in _walk_preserved(plan.root):
            if path.is_file() and path not in changes:
                raise SchemaError("VAULT_NOT_EMPTY", "Content appeared after initialization preparation")
    for guard in plan.guards:
        data = _read(guard.path)
        digest = _hash(data) if data is not None else None
        if digest == guard.sha256:
            continue
        change = changes.get(guard.path)
        if change is None or publication is None or digest != _hash(change.content):
            raise SchemaError("CONTROL_PREIMAGE_CONFLICT", "Planned control/reference changed", {"path": str(guard.path)})
        effect = operation_id + ":" + _hash(guard.path.relative_to(plan.root).as_posix().encode())
        row = publication.store.connection.execute("SELECT * FROM wf_publications WHERE operation_id=?", (effect,)).fetchone()
        if (row is None or row["run_id"] != run_id or row["kind"] != "replace" or
                row["target"] != str(guard.path) or row["expected_sha256"] != change.expected_sha256 or
                row["desired_sha256"] != digest):
            raise SchemaError("CONTROL_PREIMAGE_CONFLICT", "Desired bytes lack this plan's exact intent", {"path": str(guard.path)})
        publication._root(effect, plan.root)
        candidate = Path(row["candidate_path"])
        _private_path(candidate, plan.root)
        run = publication.store.read_run(run_id)
        if run["run_kind"] == "pipeline":
            publication._authorize_effect(row, plan.root, new_effect=False)
        elif run["workflow"] == "wiki-vault-controls":
            key = _hash(guard.path.relative_to(plan.root).as_posix().encode())
            expected_candidate = publication.store.project_root / "docs/tmp/pipeline-runs" / run_id / "control-candidates" / (key + ".candidate")
            if candidate != expected_candidate:
                raise SchemaError("CONTROL_CANDIDATE_CONFLICT", "Candidate is not the original inline plan candidate")
        if _read(candidate) != change.content:
            raise SchemaError("CONTROL_CANDIDATE_CONFLICT", "Journalled candidate binding changed")
        bound = False
        for event in publication.store.connection.execute(
                "SELECT payload_json FROM wf_events WHERE run_id=? AND event_type='publication_intent'", (run_id,)):
            intent = json.loads(event[0])
            if intent.get("operation_id") == effect:
                bound = (Path(intent.get("root", "")) == plan.root and intent.get("candidate_path") == str(candidate)
                         and intent.get("target") == str(guard.path) and intent.get("expected_sha256") == change.expected_sha256
                         and intent.get("desired_sha256") == digest and intent.get("run_id") == run_id)
                break
        if not bound:
            raise SchemaError("CONTROL_PREIMAGE_CONFLICT", "Publication intent does not bind this plan")
    if plan.schema_sha256 is not None:
        schema.resolve_schema(plan.root, plan.selection_key)
    _, metadata = _raw_inventory(plan.root)
    if tuple(row for row in plan.raw_metadata if row[1] == "file") != tuple(row for row in metadata if row[1] == "file"):
        raise SchemaError("CONTROL_RAW_METADATA_CONFLICT", "Raw file inventory changed after preparation")
    if plan.preservation_metadata is not None and plan.preservation_metadata != _metadata_rows(_preservation(plan)):
        raise SchemaError("CONTROL_PRESERVATION_CONFLICT", "Outside-allowlist metadata changed after preparation", {"preservation": "metadata-only"})


def apply_controls(plan, publication, run_id, attempt_id, operation_id, candidate_dir: Path) -> tuple[dict, ...]:
    """Apply/reconcile in deterministic order; retain partial effects on failure.

    SchemaError.details['receipts'] records effects completed before any refusal.
    The same operation ID and private candidate directory must be used on resume.
    """
    candidate_dir = Path(candidate_dir).absolute()
    try:
        candidate_dir.relative_to(plan.root)
    except ValueError:
        pass
    else:
        raise SchemaError("CONTROL_CANDIDATE_UNSAFE", "Candidates must be outside the canonical vault")
    changes = {change.path: change for change in plan.changes}
    receipts = list(plan.receipts)
    try:
        for ancestor in (*reversed(candidate_dir.parents), candidate_dir):
            if ancestor.is_symlink() or (hasattr(ancestor, "is_junction") and ancestor.is_junction()):
                raise SchemaError("CONTROL_CANDIDATE_UNSAFE", "Candidate ancestors must not be links")
        validate_control_guards(plan, publication, run_id, operation_id)
        candidate_dir.mkdir(parents=True, exist_ok=True)
        for change in plan.changes:
            schema._relative(plan.root, change.path.relative_to(plan.root).as_posix(), "control effect")
            key = _hash(change.path.relative_to(plan.root).as_posix().encode())
            candidate = candidate_dir / (key + ".candidate")
            if candidate.exists():
                if _read(candidate) != change.content:
                    raise SchemaError("CONTROL_CANDIDATE_CONFLICT", "Private control candidate changed")
            else:
                _write_absent(candidate, change.content)
            validate_control_guards(plan, publication, run_id, operation_id)
            def protected(path):
                return path in plan.reference_paths or is_protected(path)
            receipt = publication.publish_file(run_id, attempt_id, candidate, change.path, change.expected_sha256,
                                               operation_id + ":" + key, root=plan.root, protected_check=protected)
            receipts.append(receipt)
            if change.role == "schema":
                # Validate actual published declarations before any dependent effect;
                # the already prepared target membership remains frozen.
                schema.resolve_schema(plan.root, plan.selection_key)
        return tuple(receipts)
    except Exception as exc:
        details = dict(getattr(exc, "details", {}))
        details["receipts"] = tuple(receipts)
        raise SchemaError(getattr(exc, "code", "CONTROL_APPLICATION_FAILED"), str(exc), details) from exc


_ENVELOPE_FIELDS = {"schema_version", "request", "run_id", "operation_id", "runtime_refs", "root_identity",
                    "schema_identity", "preimage_refs", "preservation_metadata", "predecessor_plan_ref", "plan"}
_PLAN_FIELDS = {"root", "selection_key", "schema_sha256", "changes", "receipts", "guards", "reference_paths", "raw_metadata"}
_EXCLUDED = {".obsidian", ".git", ".trash", "trash", "backup", "backups", "delete", "plugins", "auth", ".auth", "credentials"}


def _invalid(message, code="CONTROL_PLAN_INVALID", **details):
    raise SchemaError(code, message, details)


def _closed(value, fields, name):
    if not isinstance(value, Mapping) or set(value) != set(fields):
        _invalid("Invalid closed " + name)


def _digest_value(value, *, nullable=False):
    if nullable and value is None:
        return
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        _invalid("Invalid SHA256")


def _text_value(value):
    if not isinstance(value, str) or not value or "\0" in value:
        _invalid("Expected nonempty NUL-free text")


def _absolute(value):
    _text_value(value)
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        _invalid("Expected canonical absolute path")
    for part in (*path.parents, path):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            _invalid("Path ancestors cannot be links")
    if path != path.resolve():
        _invalid("Path is not canonical")
    return path.resolve()


def _private_path(path, root):
    path = _absolute(str(path))
    if path == root or root in path.parents or path in root.parents:
        _invalid("Private evidence must be outside the vault", "CONTROL_PRIVATE_PATH_UNSAFE")
    return path


def _request(value):
    _closed(value, {"schema_version", "selection_key", "vault_root", "project_root", "overrides"}, "control request")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        _invalid("Unsupported request version")
    if value["selection_key"] is not None:
        _text_value(value["selection_key"])
    root = _absolute(value["vault_root"])
    if not root.is_dir():
        _invalid("Vault root unavailable")
    project = _private_path(value["project_root"], root)
    if not isinstance(value["overrides"], Mapping):
        _invalid("Overrides must be a mapping")
    _validate_controls(root, value["overrides"])
    result = _plain(value)
    result.update(vault_root=str(root), project_root=str(project))
    return result


def _authorization(value, root=None, selection_key=None):
    if isinstance(value, Mapping) and "workflow" in value:
        from _workflow.controller import RunRequest
        from .workflows import parse_arguments
        request = RunRequest.from_dict(_plain(value))
        options = parse_arguments(request)
        if request.workflow != "wiki-ingest" or not options.get("init"):
            _invalid("Inline controls require the original explicit wiki-ingest --init request")
        if root is None:
            _invalid("Inline controls require the selected plan root")
        _private_path(str(request.project_root), root)
        selected = options.get("vault")
        if selected is not None:
            supplied = Path(selected)
            if supplied.is_absolute():
                if supplied.resolve() != root:
                    _invalid("Inline request selects another vault")
            elif selected != selection_key:
                _invalid("Inline selection identity differs")
        return {"schema_version": 1, "vault_root": str(root), "selection_key": selection_key,
                "project_root": str(request.project_root), "overrides": {}}, options
    return _request(value), None


def _walk_preserved(root):
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(name for name in dirs if name.casefold() not in _EXCLUDED)
        for name in (*dirs, *sorted(files)):
            if name.casefold() in _EXCLUDED:
                continue
            path = Path(directory) / name
            schema._relative(root, path.relative_to(root).as_posix(), "preservation inventory")
            if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
                _invalid("Preserved paths cannot be links")
            yield path


def _preservation(plan):
    changed = {change.path for change in plan.changes}
    # Only parent directories of allowed writes may gain children/mtime changes.
    parents = {parent for path in changed for parent in path.parents if parent != plan.root and plan.root in parent.parents}
    result = []
    for path in _walk_preserved(plan.root):
        if path in changed or path in parents:
            continue
        observed = path.stat()
        result.append({"path": path.relative_to(plan.root).as_posix(), "kind": "directory" if path.is_dir() else "file",
                       "size": observed.st_size, "mtime_ns": observed.st_mtime_ns})
    return sorted(result, key=lambda row: row["path"])


def _metadata_rows(rows):
    return tuple((row["path"], row["kind"], row["size"], row["mtime_ns"]) for row in rows)




def _bind_preservation(plan):
    return replace(plan, preservation_metadata=_metadata_rows(_preservation(plan)))


def control_runtime_refs():
    from _workflow import contracts, publication, store
    paths = {Path(__file__).resolve(), Path(schema.__file__).resolve(), Path(contracts.__file__).resolve(),
             Path(store.__file__).resolve(), Path(publication.__file__).resolve()}
    resources = Path(schema.__file__).resolve().parents[1]
    paths.update((resources / "canonical-schema.md", resources / "_archive/canonical-schema-v3.0.md"))
    return [{"path": str(path), "sha256": _hash(_read(path)),
             "media_type": "text/markdown" if path.suffix == ".md" else "text/x-python"} for path in sorted(paths)]


def _check_runtime(refs):
    if not isinstance(refs, (list, tuple)):
        _invalid("Runtime refs must be an array")
    actual = control_runtime_refs()
    for ref in refs:
        _closed(ref, {"path", "sha256", "media_type"}, "runtime ref")
        _digest_value(ref["sha256"])
    if sorted((_plain(ref) for ref in refs), key=lambda row: row["path"]) != actual:
        _invalid("Bound runtime changed or closure is incomplete", "CONTROL_RUNTIME_CONFLICT")


def _identity(root):
    root = _absolute(str(root))
    observed = root.stat()
    return {"path": str(root), "device": observed.st_dev, "inode": observed.st_ino}


def _schema_identity(data):
    declarations = schema._parse(data.decode("utf-8-sig"))
    field = next((key for key in ("version", "schema_version", "revision") if key in declarations), None)
    return {"wiki_name": declarations.get("wiki_name", declarations.get("vault_name")),
            "local_revision_field": field, "local_revision": declarations.get(field),
            "canonical_version": declarations.get("extends_version")}


def _write_absent(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _json_bytes(value):
    return (json.dumps(_plain(value), ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def serialize_control_plan(plan: ControlPlan, request: dict, runtime_refs: list[dict], run_id: str, *, predecessor_plan_ref=None) -> dict:
    """Freeze one plan and actual preimages privately, without opening state.db."""
    bound_request = _plain(request)
    request, _ = _authorization(bound_request, plan.root, plan.selection_key)
    _text_value(run_id)
    if re.fullmatch(r"[A-Za-z0-9_-]+", run_id) is None:
        _invalid("Invalid run identity")
    if plan.root != Path(request["vault_root"]) or plan.selection_key not in (None, request["selection_key"]):
        _invalid("Plan/request root or selection mismatch")
    _check_runtime(runtime_refs)
    if plan.preservation_metadata is not None and plan.preservation_metadata != _metadata_rows(_preservation(plan)):
        _invalid("Outside-allowlist metadata changed during preparation", "CONTROL_PRESERVATION_CONFLICT")
    directory = Path(request["project_root"]) / "docs/tmp/pipeline-runs" / run_id / "control-preimages"
    _private_path(directory, plan.root)
    preimages = []
    for guard in plan.guards:
        relative = guard.path.relative_to(plan.root).as_posix()
        data = _read(guard.path)
        if (None if data is None else _hash(data)) != guard.sha256:
            _invalid("Preimage changed during planning", "CONTROL_PREIMAGE_CONFLICT", path=relative)
        ref = None
        if data is not None:
            path = directory / (_hash(relative.encode()) + ".preimage")
            if path.exists():
                if _read(path) != data:
                    _invalid("Private preimage conflict", "CONTROL_PLAN_CONFLICT")
            else:
                _write_absent(path, data)
            ref = {"path": str(path), "sha256": guard.sha256, "media_type": "application/octet-stream"}
        preimages.append({"path": relative, "sha256": guard.sha256, "ref": ref})
    schema_change = next((change for change in plan.changes if change.role == "schema"), None)
    schema_data = _read(plan.root / ".wiki-schema.md")
    identity = _schema_identity(schema_data if schema_data is not None else schema_change.content)
    payload = {"root": str(plan.root), "selection_key": plan.selection_key, "schema_sha256": plan.schema_sha256,
               "changes": [{"path": change.path.relative_to(plan.root).as_posix(), "expected_sha256": change.expected_sha256,
                            "desired_sha256": _hash(change.content), "role": change.role, "region": change.region,
                            "content_base64": base64.b64encode(change.content).decode("ascii")} for change in plan.changes],
               "receipts": _plain(plan.receipts),
               "guards": [{"path": guard.path.relative_to(plan.root).as_posix(), "sha256": guard.sha256} for guard in plan.guards],
               "reference_paths": [path.relative_to(plan.root).as_posix() for path in plan.reference_paths],
               "raw_metadata": _plain(plan.raw_metadata)}
    envelope = {"schema_version": 1, "request": bound_request, "run_id": run_id, "operation_id": "controls:" + run_id + ":adopt",
                "runtime_refs": _plain(runtime_refs), "root_identity": _identity(plan.root), "schema_identity": identity,
                "preimage_refs": preimages, "preservation_metadata": _preservation(plan),
                "predecessor_plan_ref": predecessor_plan_ref, "plan": payload}
    hydrate_control_plan(envelope)
    return envelope


def _load_preimages(envelope, plan):
    rows = envelope["preimage_refs"]
    if not isinstance(rows, (list, tuple)) or len(rows) != len(plan.guards):
        _invalid("Every guard requires exactly one preimage")
    result = {}
    private = Path(envelope["request"]["project_root"]) / "docs/tmp/pipeline-runs" / envelope["run_id"] / "control-preimages"
    for row in rows:
        _closed(row, {"path", "sha256", "ref"}, "preimage")
        path = schema._relative(plan.root, row["path"], "preimage target")
        if path in result:
            _invalid("Duplicate preimage")
        _digest_value(row["sha256"], nullable=True)
        ref = row["ref"]
        if row["sha256"] is None:
            if ref is not None:
                _invalid("Absent preimage cannot bind bytes")
            result[path] = None
        else:
            _closed(ref, {"path", "sha256", "media_type"}, "private preimage ref")
            expected = private / (_hash(row["path"].encode()) + ".preimage")
            if ref["path"] != str(expected) or ref["sha256"] != row["sha256"] or ref["media_type"] != "application/octet-stream":
                _invalid("Private preimage ref does not match its target")
            _private_path(expected, plan.root)
            data = _read(expected)
            if data is None or _hash(data) != row["sha256"]:
                _invalid("Private preimage missing or changed", "CONTROL_PREIMAGE_CONFLICT")
            result[path] = data
    if {path: None if data is None else _hash(data) for path, data in result.items()} != {g.path: g.sha256 for g in plan.guards}:
        _invalid("Preimage and guard membership differ")
    return result


def _preserved_region(original, desired, name):
    body = _owned(desired, name)
    if body is None:
        _invalid("Candidate has no authorized managed region")
    if _region(original, name, body, append=True) != desired:
        _invalid("Candidate changes bytes outside the authorized region", "CONTROL_DELTA_UNAUTHORIZED")
    return body


def _validate_deltas(plan, envelope, preimages):
    """Validate authority independently of attacker-recomputed candidate hashes."""
    schema_path = plan.root / ".wiki-schema.md"
    original = preimages.get(schema_path)
    change = next((c for c in plan.changes if c.role == "schema"), None)
    desired_schema = change.content if change else original
    if desired_schema is None or _schema_identity(desired_schema) != envelope["schema_identity"]:
        _invalid("Schema identity/pin/revision changed", "CONTROL_SCHEMA_CONFLICT")
    owned_schema = _owned(desired_schema, "controls")
    if change is not None and (owned_schema is None or re.fullmatch(rb"```(?:yaml|yml)(?: controls)?\s*\r?\n(.*?)\r?\n```", owned_schema, re.S) is None):
        _invalid("Schema control region requires exactly one YAML block", "CONTROL_DELTA_UNAUTHORIZED")
    before = schema._parse(original.decode("utf-8-sig")) if original is not None else {}
    after = schema._parse(desired_schema.decode("utf-8-sig"))
    _validate_controls(plan.root, {key: value for key, value in after.items() if key in _CONTROL_KEYS})
    authorization, inline_options = _authorization(envelope["request"], plan.root, plan.selection_key)
    supplied = authorization["overrides"]
    if original is not None:
        if _schema_identity(original) != envelope["schema_identity"]:
            _invalid("Original schema identity differs")
        expected = _plain(before)
        _merge_missing(expected, supplied)
        defaults = {"workflow_controls_version": 1,
                    "distillation_path_template": schema._DEFAULT_TEMPLATES["distillation_path_template"],
                    "content_roots": ["raw/", "wiki/"], "index_file": None, "canonical_files": {},
                    "allowed_tiers": ["A", "B", "C"], "raw_modalities": list(schema._MODALITIES) + list(before.get("additional_raw_kinds", [])),
                    "allowed_provenance_classes": list(schema._PROVENANCE), "outbound_archiving": True, "managed_navigation": {}}
        for key, value in defaults.items():
            expected.setdefault(key, value)
        for key in (set(before) | set(after)) - {"managed_navigation"}:
            if after.get(key) != expected.get(key) or (key not in expected and key in after):
                _invalid("Schema candidate changes an unauthorized declaration", "CONTROL_DELTA_UNAUTHORIZED", field=key)
        if change:
            _preserved_region(original, desired_schema, "controls")
    else:
        if inline_options is None or not inline_options.get("wiki_name") or not inline_options.get("domain"):
            _invalid("Only explicit inline --init can authorize a new schema")
        expected_initial = _initialization(plan.root, inline_options["wiki_name"], (inline_options["domain"],), require_empty=False)
        if plan.changes != expected_initial.changes:
            _invalid("Initial candidates differ from the original --init request", "CONTROL_DELTA_UNAUTHORIZED")
    paths = {"schema": ".wiki-schema.md", "manifest": "raw/_manifest.json", "hub": after.get("hub_file", "_hub.md"),
             "index": after.get("index_file"), "log": "log.md", "verify_log": "verify-log.md"}
    paths.update({key: value for key, value in after.get("canonical_files", {}).items() if key in _ROLES})
    expected_refs = {plan.root / value for key, value in after.get("canonical_files", {}).items()
                     if key not in {"schema", "manifest", *_ROLES} and value is not None}
    expected_skips = {path.relative_to(plan.root).as_posix(): "reference" for path in expected_refs}
    for role, relative in paths.items():
        if role != "schema" and relative is not None:
            data = preimages.get(plan.root / relative)
            if data is not None and _protected_bytes(data, (plan.root / relative).suffix):
                expected_skips[relative] = role
    actual_skips = {row["path"]: row["role"] for row in plan.receipts}
    if len(actual_skips) != len(plan.receipts) or actual_skips != expected_skips:
        _invalid("Protected-skip receipts do not match actual protection")
    if set(plan.reference_paths) != expected_refs:
        _invalid("Reference membership changed")
    role_paths = [value for value in paths.values() if value is not None]
    if len(set(role_paths)) != len(role_paths):
        _invalid("Control roles overlap")
    navigation = dict(before.get("managed_navigation", {}))
    skipped = {row["path"] for row in plan.receipts if row.get("status") == "skipped-protected"}
    for role, relative in paths.items():
        if role in _ROLES and relative is not None and relative not in skipped:
            navigation[relative] = _ROLES[role]
    if after.get("managed_navigation", {}) != navigation:
        _invalid("Navigation enrollment differs from exact control roles")
    expected_guards = {plan.root / value for value in role_paths} | expected_refs
    if {guard.path for guard in plan.guards} != expected_guards:
        _invalid("Missing or extra whole-plan guard")
    for item in plan.changes:
        relative = item.path.relative_to(plan.root).as_posix()
        if paths.get(item.role) != relative or item.region != ({"schema": "controls", **_ROLES}.get(item.role)):
            _invalid("Change is not an authorized role/region", "CONTROL_DELTA_UNAUTHORIZED")
        old = preimages[item.path]
        if item.path in plan.reference_paths or (old is not None and _protected_bytes(old, item.path.suffix)):
            _invalid("Protected target cannot be changed", "CONTROL_DELTA_UNAUTHORIZED")
        if item.role == "schema":
            continue
        if item.role == "manifest":
            prior = json.loads(old) if old is not None else {}
            value = json.loads(item.content)
            if not isinstance(prior, dict) or not isinstance(value, dict):
                _invalid("Manifest must be an object")
            preserved = {key: value.get(key) for key in prior if key != "wiki_workflow"}
            if _json_bytes(preserved) != _json_bytes({key: val for key, val in prior.items() if key != "wiki_workflow"}) or set(value) != set(prior) | {"wiki_workflow"}:
                _invalid("Manifest changed prior fields/timestamps", "CONTROL_DELTA_UNAUTHORIZED")
            namespace = value.get("wiki_workflow")
            prior_namespace = prior.get("wiki_workflow", {})
            if not isinstance(namespace, dict) or not isinstance(prior_namespace, dict) or any(_json_bytes(namespace.get(key)) != _json_bytes(val) for key, val in prior_namespace.items()):
                _invalid("Manifest changed prior workflow values")
            if set(namespace) != set(prior_namespace) | {"version", "artifacts", "inventory", "inventory_diagnostics"} or type(namespace.get("version")) is not int or namespace["version"] != 1:
                _invalid("Unauthorized manifest namespace")
            if "artifacts" not in prior_namespace and namespace["artifacts"] != []:
                _invalid("Adoption cannot fabricate accepted artifacts")
            if "inventory_diagnostics" not in prior_namespace and namespace["inventory_diagnostics"] != {"verification": "not_performed", "preservation": "metadata-only", "source_hashes": "observed-provenance-only"}:
                _invalid("Adoption cannot assert verification")
            if "inventory" not in prior_namespace:
                records, _ = _raw_inventory(plan.root)
                if namespace["inventory"] != records:
                    _invalid("Manifest inventory does not match actual raw metadata")
        else:
            body = _preserved_region(old or b"", item.content, item.region)
            previous_body = _owned(old or b"", item.region)
            if previous_body is not None:
                if body != previous_body:
                    _invalid("Adoption cannot rewrite existing managed navigation/operations")
            else:
                allowed = {"hub": b"## Accepted knowledge\n\nNo accepted artifacts have been registered by this control adoption.",
                           "index": b"## Workflow catalog\n\nNo accepted artifacts have been registered by this control adoption.",
                           "log": b"## Workflow operations\n\nNo workflow operations recorded in this region.",
                           "verify_log": b"## Workflow verification\n\nNo verification has been performed by this control adoption."}
                initial = b"No verification has been performed." if item.role == "verify_log" else b"No accepted artifacts or workflow operations recorded."
                if body.replace(b"\r\n", b"\n") != (initial if original is None else allowed[item.role]):
                    _invalid("Adoption candidate contains unauthorized narrative")


def _protected_bytes(data, suffix):
    metadata = json.loads(data) if suffix == ".json" else _frontmatter(data)
    flag = metadata.get("human_edited")
    return (flag is not None and flag is not False) or metadata.get("pipeline_id") == "hand-curated"


def hydrate_control_plan(envelope: Mapping) -> ControlPlan:
    """Validate the identical durable payload in memory for pipeline --init."""
    _closed(envelope, _ENVELOPE_FIELDS, "control envelope")
    if type(envelope["schema_version"]) is not int or envelope["schema_version"] != 1:
        _invalid("Unsupported envelope version")
    wire = envelope["plan"]
    _closed(wire, _PLAN_FIELDS, "ControlPlan")
    root = _absolute(wire["root"])
    request, _ = _authorization(envelope["request"], root, wire["selection_key"])
    run_id = envelope["run_id"]
    _text_value(run_id)
    if re.fullmatch(r"[A-Za-z0-9_-]+", run_id) is None:
        _invalid("Invalid run identity")
    prefix = "controls:" + run_id + ":"
    if not isinstance(envelope["operation_id"], str) or not envelope["operation_id"].startswith(prefix) or re.fullmatch(r"[A-Za-z0-9_.-]+", envelope["operation_id"][len(prefix):]) is None:
        _invalid("Control operation must bind the exact run and one step identity")
    _check_runtime(envelope["runtime_refs"])
    if root != Path(request["vault_root"]) or wire["selection_key"] not in (None, request["selection_key"]):
        _invalid("Request/plan identities differ")
    _closed(envelope["root_identity"], {"path", "device", "inode"}, "root identity")
    if any(type(envelope["root_identity"][key]) is not int for key in ("device", "inode")) or envelope["root_identity"] != _identity(root):
        _invalid("Vault root identity changed", "CONTROL_ROOT_CONFLICT")
    _closed(envelope["schema_identity"], {"wiki_name", "local_revision_field", "local_revision", "canonical_version"}, "schema identity")
    _digest_value(wire["schema_sha256"], nullable=True)
    for key in ("changes", "receipts", "guards", "reference_paths", "raw_metadata"):
        if not isinstance(wire[key], (list, tuple)):
            _invalid("Plan collections must be arrays")
    changes = []
    for item in wire["changes"]:
        _closed(item, {"path", "expected_sha256", "desired_sha256", "role", "region", "content_base64"}, "change")
        if not isinstance(item["role"], str) or item["role"] not in {"schema", "manifest", *_ROLES}:
            _invalid("Unknown control role")
        if item["region"] is not None and not isinstance(item["region"], str):
            _invalid("Invalid managed region type")
        _digest_value(item["expected_sha256"], nullable=True)
        _digest_value(item["desired_sha256"])
        if not isinstance(item["content_base64"], str):
            _invalid("Candidate encoding must be base64 text")
        try:
            content = base64.b64decode(item["content_base64"], validate=True)
        except (ValueError, binascii.Error) as exc:
            raise SchemaError("CONTROL_PLAN_INVALID", "Invalid base64 candidate") from exc
        if _hash(content) != item["desired_sha256"]:
            _invalid("Candidate hash mismatch")
        changes.append(ControlChange(schema._relative(root, item["path"], "change"), item["expected_sha256"], content, item["role"], item["region"]))
    guards = []
    for item in wire["guards"]:
        _closed(item, {"path", "sha256"}, "guard")
        _digest_value(item["sha256"], nullable=True)
        guards.append(ControlGuard(schema._relative(root, item["path"], "guard"), item["sha256"]))
    if len({c.path for c in changes}) != len(changes) or len({g.path for g in guards}) != len(guards):
        _invalid("Duplicate plan targets or guards")
    for item in wire["raw_metadata"]:
        if (not isinstance(item, (list, tuple)) or len(item) != 4 or item[1] not in ("file", "directory")
                or type(item[2]) is not int or type(item[3]) is not int):
            _invalid("Invalid raw metadata")
        schema._relative(root, item[0], "raw metadata")
    for receipt in wire["receipts"]:
        if not isinstance(receipt, Mapping) or receipt.get("status") != "skipped-protected" or receipt.get("role") not in {"reference", "schema", "manifest", *_ROLES}:
            _invalid("Invalid protected-skip receipt")
        if set(receipt) not in ({"path", "role", "status"}, {"path", "role", "status", "proposal"}):
            _invalid("Invalid closed protected-skip receipt")
        role = receipt["role"]
        expected_proposal = {"namespace": "wiki_workflow"} if role == "manifest" else {"region": _ROLES.get(role, "controls")}
        if (role == "reference" and "proposal" in receipt) or (role != "reference" and receipt.get("proposal") != expected_proposal):
            _invalid("Invalid protected-skip proposal")
        schema._relative(root, receipt["path"], "protected skip")
    references = tuple(schema._relative(root, value, "reference") for value in wire["reference_paths"])
    if len(set(references)) != len(references):
        _invalid("Duplicate references")
    plan = ControlPlan(root, tuple(changes), tuple(_plain(wire["receipts"])), wire["schema_sha256"], tuple(guards),
                       wire["selection_key"], references, tuple(tuple(item) for item in wire["raw_metadata"]))
    preimages = _load_preimages(envelope, plan)
    for change in changes:
        if change.path not in preimages or (None if preimages[change.path] is None else _hash(preimages[change.path])) != change.expected_sha256:
            _invalid("Change lacks its exact original preimage")
    if (None if preimages.get(root / ".wiki-schema.md") is None else _hash(preimages[root / ".wiki-schema.md"])) != plan.schema_sha256:
        _invalid("Schema preimage identity differs")
    predecessor = envelope["predecessor_plan_ref"]
    if predecessor is not None:
        _closed(predecessor, {"path", "sha256"}, "predecessor plan ref")
        _digest_value(predecessor["sha256"])
        path = _private_path(predecessor["path"], root)
        if _read(path) is None or _hash(_read(path)) != predecessor["sha256"]:
            _invalid("Predecessor history is unavailable")
    if not isinstance(envelope["preservation_metadata"], (list, tuple)):
        _invalid("Preservation metadata must be an array")
    for item in envelope["preservation_metadata"]:
        _closed(item, {"path", "kind", "size", "mtime_ns"}, "preservation metadata")
        schema._relative(root, item["path"], "preserved path")
        if item["kind"] not in ("file", "directory") or type(item["size"]) is not int or type(item["mtime_ns"]) is not int:
            _invalid("Invalid preservation metadata")
    if len({row["path"] for row in envelope["preservation_metadata"]}) != len(envelope["preservation_metadata"]):
        _invalid("Duplicate preservation metadata paths")
    plan = replace(plan, preservation_metadata=_metadata_rows(envelope["preservation_metadata"]))
    _validate_deltas(plan, envelope, preimages)
    return plan


def load_control_plan(path: Path) -> tuple[dict, ControlPlan]:
    path = _absolute(str(path))
    envelope = json.loads(_read(path))
    plan = hydrate_control_plan(envelope)
    _private_path(path, plan.root)
    return envelope, plan


def verify_controls(plan: ControlPlan, *, envelope: Mapping) -> tuple[dict, ...]:
    """Read-only verification: no Store, publication, network, or native work."""
    validated = hydrate_control_plan(envelope)
    if validated != plan:
        _invalid("Plan differs from its bound envelope")
    preimages = _load_preimages(envelope, plan)
    changes = {change.path: change for change in plan.changes}
    results = list(plan.receipts)
    conflicts = []
    for guard in plan.guards:
        actual = _read(guard.path)
        expected = _hash(changes[guard.path].content) if guard.path in changes else guard.sha256
        status = "verified" if actual is not None and _hash(actual) == expected else "conflict"
        row = {"path": guard.path.relative_to(plan.root).as_posix(), "status": status, "sha256": None if actual is None else _hash(actual)}
        results.append(row)
        if status == "conflict":
            conflicts.append(row)
    expected_metadata = _metadata_rows(envelope["preservation_metadata"])
    actual_metadata = _metadata_rows(_preservation(plan))
    if expected_metadata != actual_metadata:
        conflicts.append({"status": "conflict", "code": "CONTROL_PRESERVATION_CONFLICT", "preservation": "metadata-only"})
    if conflicts:
        raise SchemaError("CONTROL_VERIFICATION_FAILED", "Control plan did not independently verify", {"results": results, "conflicts": conflicts})
    resolved = schema.resolve_schema(plan.root, plan.selection_key)
    if not resolved.controls_enrolled:
        _invalid("Schema did not enroll controls", "VAULT_CONTROLS_NOT_ENROLLED")
    if _schema_identity(_read(plan.root / ".wiki-schema.md")) != envelope["schema_identity"]:
        _invalid("Schema pin/revision/identity changed", "CONTROL_SCHEMA_CONFLICT")
    results.append({"status": "verified", "preservation": "metadata-only", "write_allowlist": [c.path.relative_to(plan.root).as_posix() for c in plan.changes]})
    return tuple(results)


def _check_retained_state(envelope, plan):
    """Inspect an existing journal without bootstrapping any database."""
    from _workflow.store import Store
    from _workflow.publication import PublicationManager
    project = Path(envelope["request"]["project_root"])
    if not (project / "state.db").exists():
        validate_control_guards(plan)
        return
    store = Store(project, read_only=True)
    try:
        run = store.read_run(envelope["run_id"])
        if run is None:
            validate_control_guards(plan)
        else:
            if json.loads(run["input_manifest_json"]) != _plain(envelope):
                _invalid("Retained run binds a different immutable plan", "CONTROL_PLAN_CONFLICT")
            validate_control_guards(plan, PublicationManager(store), envelope["run_id"], envelope["operation_id"])
    finally:
        store.close()


def _predecessor(path, root):
    """Read-only effect classification; predecessor history never grants execution."""
    from _workflow.store import Store
    path = _private_path(path, root)
    data = _read(path)
    envelope = json.loads(data)
    _closed(envelope, _ENVELOPE_FIELDS, "predecessor envelope")
    wire = envelope["plan"]
    _closed(wire, _PLAN_FIELDS, "predecessor plan")
    request, _ = _authorization(envelope["request"], root, wire["selection_key"])
    if request["vault_root"] != str(root) or wire["root"] != str(root) or envelope["root_identity"] != _identity(root):
        _invalid("Predecessor belongs to a different vault")
    database = Path(request["project_root"]) / "state.db"
    store = Store(database.parent, read_only=True) if database.exists() else None
    try:
        run = store.read_run(envelope["run_id"]) if store is not None else None
        if run is not None:
            recorded = json.loads(run["input_manifest_json"])
            if run["run_kind"] == "pipeline":
                recorded = None
                events = store.connection.execute(
                    "SELECT payload_json FROM wf_events WHERE run_id=? AND attempt_id IS NULL "
                    "AND event_type='local_output_validated' ORDER BY event_id DESC", (run["run_id"],))
                for event in events:
                    payload = json.loads(event[0])
                    if (payload.get("schema_version") == 1 and payload.get("run_id") == run["run_id"]
                            and payload.get("definition_sha256") == run["definition_sha256"]
                            and payload.get("validation") == {"valid": True, "source": "registered_local_handler"}
                            and envelope["operation_id"] == "controls:" + run["run_id"] + ":" + payload.get("step_id", "")
                            and any(ref.get("name") == "control-plan.json" and Path(ref["path"]) == path
                                    and ref.get("sha256") == _hash(data) for ref in payload.get("artifacts", ()))):
                        recorded = payload.get("data", {}).get("control_plan")
                        break
            if recorded != envelope:
                _invalid("Predecessor differs from its immutable journal")
        for item in wire["changes"]:
            _closed(item, {"path", "expected_sha256", "desired_sha256", "role", "region", "content_base64"}, "predecessor change")
            target = schema._relative(root, item["path"], "predecessor target")
            _digest_value(item["expected_sha256"], nullable=True)
            _digest_value(item["desired_sha256"])
            content = base64.b64decode(item["content_base64"], validate=True)
            if _hash(content) != item["desired_sha256"]:
                _invalid("Predecessor candidate hash differs")
            actual = _read(target)
            digest = None if actual is None else _hash(actual)
            operation = envelope["operation_id"] + ":" + _hash(item["path"].encode())
            row = store.connection.execute("SELECT * FROM wf_publications WHERE operation_id=?", (operation,)).fetchone() if store is not None else None
            if row is not None:
                if (row["run_id"] != envelope["run_id"] or row["target"] != str(target) or row["kind"] != "replace"
                        or row["expected_sha256"] != item["expected_sha256"] or row["desired_sha256"] != item["desired_sha256"]):
                    _invalid("Predecessor effect binding differs")
                from _workflow.publication import PublicationManager
                PublicationManager(store)._root(operation, root)
            if digest == item["expected_sha256"]:
                continue
            if row is None:
                _invalid("Unjournalled predecessor effect cannot be reconciled", "CONTROL_PREIMAGE_CONFLICT")
            if digest != item["desired_sha256"] and row["status"] not in {"applied", "reconciled"}:
                _invalid("Predecessor has an unexplained conflicting effect", "CONTROL_PREIMAGE_CONFLICT")
    finally:
        if store is not None:
            store.close()
    return {"path": str(path), "sha256": _hash(data)}


def _receipt_binding(plan_path, envelope):
    return {"path": str(plan_path), "sha256": _hash(_read(plan_path)), "run_id": envelope["run_id"], "operation_id": envelope["operation_id"]}


def _collect_receipt(path, binding, command):
    if not path.exists():
        return None
    value = json.loads(_read(path))
    if not isinstance(value, dict) or value.get("plan_ref") != binding or value.get("command") != command:
        _invalid("Receipt destination already binds different content", "CONTROL_RECEIPT_CONFLICT")
    return value


def _apply_plan(path, envelope, plan):
    from _workflow.store import Store
    from _workflow.publication import PublicationManager
    store = Store(Path(envelope["request"]["project_root"]))
    store.initialize()
    receipts = []
    try:
        store.create_run(envelope["run_id"], "wiki-vault-controls", _hash(_json_bytes(envelope)), envelope["request"], envelope, run_kind="inline")
        with store.acquire_owner(envelope["run_id"]):
            run = store.read_run(envelope["run_id"])
            if run["status"] == "done":
                return verify_controls(plan, envelope=envelope)
            if run["status"] not in {"waiting", "blocked", "executing"}:
                _invalid("Control run is not resumable")
            store.create_step(envelope["run_id"], "adopt", "controls", 0)
            store.update_run(envelope["run_id"], "executing")
            store.update_step(envelope["run_id"], "adopt", "controls", "executing")
            try:
                receipts = apply_controls(plan, PublicationManager(store), envelope["run_id"], None, envelope["operation_id"],
                                          Path(envelope["request"]["project_root"]) / "docs/tmp/pipeline-runs" / envelope["run_id"] / "control-candidates")
                verification = verify_controls(plan, envelope=envelope)
                store.append_event(envelope["run_id"], None, "controls_verified", {"results": verification})
                store.update_step(envelope["run_id"], "adopt", "controls", "done")
                store.update_run(envelope["run_id"], "done")
                return (*receipts, *verification)
            except Exception as exc:
                store.update_step(envelope["run_id"], "adopt", "controls", "blocked", error_summary=str(exc))
                store.update_run(envelope["run_id"], "blocked")
                details = dict(getattr(exc, "details", {}))
                details.setdefault("receipts", receipts)
                raise SchemaError(getattr(exc, "code", "CONTROL_APPLICATION_FAILED"), str(exc), details) from exc
    finally:
        store.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Finite wiki control plan/apply/verify")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("plan")
    prepare.add_argument("--request", required=True)
    prepare.add_argument("--out", required=True)
    prepare.add_argument("--predecessor-plan")
    for command in ("apply", "verify"):
        child = commands.add_parser(command)
        child.add_argument("--plan", required=True)
        child.add_argument("--receipt", required=True)
    args = parser.parse_args(argv)
    receipt_path = binding = None
    try:
        if args.command == "plan":
            request = _request(json.loads(_read(_absolute(args.request))))
            root = Path(request["vault_root"])
            out = _private_path(args.out, root)
            predecessor = None
            if args.predecessor_plan:
                predecessor = _predecessor(args.predecessor_plan, root)
            # Do not allocate an identity, backup, or database before collection.
            if out.exists():
                try:
                    envelope, plan = load_control_plan(out)
                    if envelope["request"] != request or envelope["predecessor_plan_ref"] != predecessor:
                        _invalid("Existing output binds another control request")
                    _check_retained_state(envelope, plan)
                except Exception as exc:
                    raise SchemaError("CONTROL_PLAN_CONFLICT", "Existing immutable plan cannot be reused", {"cause": getattr(exc, "code", type(exc).__name__)}) from exc
                print(_json_bytes({"schema_version": 1, "status": "planned", "plan": str(out), "run_id": envelope["run_id"], "changes": len(plan.changes)}).decode(), end="")
                return 0
            resolved = schema.resolve_schema(root, request["selection_key"])
            plan = prepare_controls(resolved, request["overrides"], adopt_existing=True)
            run_id = "controls-" + str(uuid.uuid4())
            envelope = serialize_control_plan(plan, request, control_runtime_refs(), run_id, predecessor_plan_ref=predecessor)
            _write_absent(out, _json_bytes(envelope))
            print(_json_bytes({"schema_version": 1, "status": "planned", "plan": str(out), "run_id": run_id, "changes": len(plan.changes)}).decode(), end="")
            return 0
        path = _absolute(args.plan)
        envelope = json.loads(_read(path))
        _closed(envelope, _ENVELOPE_FIELDS, "control envelope")
        _closed(envelope["plan"], _PLAN_FIELDS, "ControlPlan")
        root = _absolute(envelope["plan"]["root"])
        _private_path(path, root)
        receipt_path = _private_path(args.receipt, root)
        if receipt_path == path:
            _invalid("Receipt cannot replace its plan")
        binding = _receipt_binding(path, envelope)
        old = _collect_receipt(receipt_path, binding, args.command)
        if old is not None:
            print(_json_bytes(old).decode(), end="")
            return 0 if old.get("status") in {"adopted", "verified_with_protected_skips"} else 2
        plan = hydrate_control_plan(envelope)
        if args.command == "apply" and "workflow" in envelope["request"]:
            _invalid("Pipeline control plans must resume their original controller; CLI cannot create a nested inline run")
        results = _apply_plan(path, envelope, plan) if args.command == "apply" else verify_controls(plan, envelope=envelope)
        status = "verified_with_protected_skips" if plan.receipts else "adopted"
        result = {"schema_version": 1, "command": args.command, "status": status, "plan_ref": binding, "results": results, "error": None}
        _write_absent(receipt_path, _json_bytes(result))
        print(_json_bytes(result).decode(), end="")
        return 0
    except Exception as exc:
        result = {"schema_version": 1, "command": args.command, "status": "blocked", "plan_ref": binding,
                  "results": _plain(getattr(exc, "details", {}).get("receipts", ())),
                  "error": {"code": getattr(exc, "code", "CONTROL_COMMAND_FAILED"), "message": str(exc), "details": _plain(getattr(exc, "details", {}))}}
        if receipt_path is not None and binding is not None and not receipt_path.exists():
            _write_absent(receipt_path, _json_bytes(result))
        print(_json_bytes(result).decode(), end="")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
