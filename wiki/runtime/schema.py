"""Read-only vault schema resolution; declarations are data, never write authority."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
from string import Formatter
from types import MappingProxyType
from typing import Any, Mapping

import yaml

from _workflow.contracts import InputRef


class SchemaError(ValueError):
    def __init__(self, code: str, message: str, details=None):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details or {}


@dataclass(frozen=True)
class ResolvedVault:
    selection_key: str | None
    root: Path
    wiki_name: str
    local_revision_field: str | None
    local_revision: Any
    canonical_version: str | None
    canonical_sha256: str | None
    domains: tuple[str, ...]
    topics: Mapping[str, tuple[str, ...]]
    aliases: Mapping[str, str | None]
    read_roots: tuple[Path, ...]
    output_templates: Mapping[str, str]
    canonical_files: Mapping[str, str | None]
    allowed_tiers: tuple[str, ...]
    allowed_provenance_classes: tuple[str, ...]
    raw_modalities: tuple[str, ...]
    source_hash_authority: str | None
    constraints_ref: InputRef
    raw_declarations: Mapping[str, Any]
    source_text: str
    diagnostics: tuple[Mapping[str, Any], ...]
    controls_enrolled: bool


class _Loader(yaml.SafeLoader):
    pass


def _mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if not isinstance(key, (str, int)) or isinstance(key, bool):
            raise SchemaError("SCHEMA_INVALID_DECLARATION", "Declaration keys must be strings or integers")
        key = str(key)
        value = loader.construct_object(value_node, deep=True)
        if key in result and not _equivalent(result[key], value):
            raise SchemaError("SCHEMA_DECLARATION_CONFLICT", f"Conflicting duplicate declaration: {key}")
        result[key] = value
    return result


_Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def _equivalent(a, b):
    # Do not equate YAML booleans and integers, or local numeric/string revisions.
    return type(a) is type(b) and a == b


def _load(text, label):
    try:
        return yaml.load(text, Loader=_Loader)
    except yaml.YAMLError as exc:
        raise SchemaError("SCHEMA_INVALID_YAML", f"Invalid YAML in {label}", {"error": str(exc)}) from exc


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    return value


def _merge(target, incoming, prefix=""):
    for key, value in incoming.items():
        name = prefix + key
        if key not in target:
            target[key] = value
        elif isinstance(target[key], dict) and isinstance(value, dict):
            _merge(target[key], value, name + ".")
        elif not _equivalent(target[key], value):
            # Ontology lists are additive; policy lists may only repeat the same set.
            if isinstance(target[key], list) and isinstance(value, list):
                if name in {"domains", "descriptors", "content_types", "status_tags"} or name.startswith("topics."):
                    target[key] = list(dict.fromkeys(_strings(target[key], name) + _strings(value, name)))
                    continue
                if name in {"allowed_tiers", "raw_modalities", "allowed_provenance_classes", "content_roots", "additional_raw_kinds"}:
                    if set(_strings(target[key], name)) == set(_strings(value, name)):
                        continue
            raise SchemaError("SCHEMA_DECLARATION_CONFLICT", f"Conflicting declaration: {name}", {"field": name})


def _category(heading):
    heading = re.sub(r"[`*_]", "", heading).lower().strip()
    if re.match(r"(?:tag )?aliases\b", heading):
        return "aliases"
    if re.match(r"(?:tag )?domains\b", heading):
        return "domains"
    if re.match(r"(?:tag )?topics\b", heading):
        return "topics"
    if heading.startswith("content descriptors"):
        return "descriptors"
    if heading.startswith("content types"):
        return "content_types"
    if heading.startswith("status tags"):
        return "status_tags"
    return None


_KNOWN = {"vault_name", "wiki_name", "extends", "extends_version", "schema_version", "version",
          "hub_file", "index_file", "book_paths", "canonical_files", "workflow_controls_version",
          "compilation_path_template", "strategy_path_template", "distillation_path_template",
          "content_roots", "allowed_tiers", "raw_modalities", "allowed_provenance_classes",
          "outbound_archiving", "managed_navigation", "source_hash_authority", "domains", "topics", "aliases"}


def _parse(text):
    declarations = {}
    lines = text.splitlines()
    start = 0
    if lines and lines[0] == "---":
        try:
            end = lines.index("---", 1)
        except ValueError as exc:
            raise SchemaError("SCHEMA_INVALID_YAML", "Unclosed leading frontmatter") from exc
        frontmatter = _load("\n".join(lines[1:end]), "frontmatter") or {}
        if not isinstance(frontmatter, dict):
            raise SchemaError("SCHEMA_INVALID_DECLARATION", "Frontmatter must be a mapping")
        _merge(declarations, frontmatter)
        start = end + 1
    headings = []
    i = start
    while i < len(lines):
        line = lines[i]
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            level, title = len(heading[1]), heading[2]
            while headings and headings[-1][0] >= level:
                headings.pop()
            headings.append((level, title))
            i += 1
            continue
        fence = re.match(r"^\s*(`{3,}|~{3,})(.*)$", line)
        category = next((_category(h) for _, h in reversed(headings) if _category(h)), None)
        if fence:
            marker, info = fence[1], fence[2].strip().lower()
            end = i + 1
            while end < len(lines) and not re.fullmatch(r"\s*" + re.escape(marker[0]) + "{" + str(len(marker)) + r",}\s*", lines[end]):
                end += 1
            if end == len(lines):
                raise SchemaError("SCHEMA_INVALID_DECLARATION", "Unclosed fenced block")
            if info.startswith(("yaml", "yml")):
                body = "\n".join(lines[i + 1:end])
                named = info.split(maxsplit=1)[1] if len(info.split(maxsplit=1)) == 2 else ""
                block_category = _category(named) or (category if not named else None)
                named_declaration = named in {"declarations", "controls", "schema", "constraints", "policy", "paths", "book_paths"}
                first_keys = set(re.findall(r"^([A-Za-z_][\w-]*):", body, re.MULTILINE))
                if not (block_category or named_declaration or first_keys & _KNOWN):
                    i = end + 1
                    continue
                parsed = _load(body, " / ".join(h for _, h in headings))
                if named == "book_paths" and isinstance(parsed, dict) and "book_paths" not in parsed:
                    _merge(declarations, {"book_paths": parsed})
                elif block_category and parsed is not None:
                    if isinstance(parsed, dict) and block_category in parsed:
                        _merge(declarations, parsed)
                    else:
                        _merge(declarations, {block_category: parsed})
                elif isinstance(parsed, dict) and (set(parsed) & _KNOWN or named_declaration):
                    _merge(declarations, parsed)
                elif parsed is not None:
                    raise SchemaError("SCHEMA_INVALID_DECLARATION", "Named declarations must be a mapping")
            i = end + 1
            continue
        # Trading-style taxonomy: only domain bullets in a Domains section,
        # and domain-labelled topic bullets in Topics. Other tag categories never become domains.
        if category:
            bullet = re.match(r"^\s*[-*]\s+(.+)$", line)
            if bullet:
                value = bullet[1].strip()
                if category == "topics":
                    match = re.match(r"(?:\*\*)?`?([\w-]+)`?(?:\*\*)?\s*:\s*(.+)", value)
                    if match:
                        items = re.findall(r"`([^`]+)`", match[2]) or [s.strip() for s in match[2].split(",")]
                        _merge(declarations, {"topics": {match[1]: items}})
                    else:
                        tags = [tag.removeprefix("topic/") for tag in re.findall(r"`([^`]+)`", value)]
                        if tags:
                            domains = declarations.get("domains", [])
                            domain = next((re.sub(r"[`*_]", "", heading).strip() for _, heading in reversed(headings)
                                           if re.sub(r"[`*_]", "", heading).strip() in domains), None)
                            if domain is None and len(domains) == 1:
                                domain = domains[0]
                            if domain is None:
                                raise SchemaError("SCHEMA_AMBIGUOUS_TAXONOMY", "Markdown topic bullets require an explicit domain heading or a sole declared domain")
                            _merge(declarations, {"topics": {domain: tags}})
                elif category == "aliases":
                    alias = re.fullmatch(r"`([^`]+)`\s*(?:→|->)\s*(?:`([^`]+)`|(null|remove))", value)
                    if alias:
                        _merge(declarations, {"aliases": {alias[1]: alias[2]}})
                elif category != "aliases":
                    tags = re.findall(r"`([^`]+)`", value)
                    if not tags:
                        token = re.match(r"([\w/-]+)(?:\s|$)", value)
                        tags = [token[1]] if token else []
                    if category == "domains":
                        tags = [tag.removeprefix("domain/") for tag in tags]
                    _merge(declarations, {category: tags})
        i += 1
    return declarations


def _strings(value, field, allowed=None):
    # YAML legal-section topics may be plain integers, like numeric alias keys.
    if field.startswith("topics.") and isinstance(value, (list, tuple)):
        value = [str(item) if type(item) is int else item for item in value]
    if not isinstance(value, (list, tuple)) or any(not isinstance(x, str) or not x for x in value):
        raise SchemaError("SCHEMA_INVALID_DECLARATION", f"{field} must be a list of nonempty strings")
    if allowed is not None and set(value) - set(allowed):
        raise SchemaError("SCHEMA_INVALID_DECLARATION", f"Invalid {field}")
    return tuple(dict.fromkeys(value))


def _relative(root, value, field, template=False):
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise SchemaError("SCHEMA_INVALID_PATH", f"Invalid {field}")
    if PurePosixPath(value).is_absolute() or PureWindowsPath(value).drive or any(p in {".", ".."} for p in value.split("/")) or ":" in value:
        raise SchemaError("SCHEMA_PATH_OUTSIDE_VAULT", f"{field} must be vault-relative")
    if "//" in value:
        raise SchemaError("SCHEMA_INVALID_PATH", f"Empty path component in {field}")
    if not template and ("{" in value or "}" in value):
        raise SchemaError("SCHEMA_UNRESOLVED_PLACEHOLDER", f"Unresolved placeholder in {field}")
    for component in PurePosixPath(value).parts:
        if component.endswith((".", " ")) or re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", component, re.IGNORECASE) or any(c in component for c in '<>"|?*'):
            raise SchemaError("SCHEMA_INVALID_PATH", f"Nonportable path component in {field}")
    path = root / value
    current = root
    for part in PurePosixPath(value).parts:
        current /= part
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            raise SchemaError("SCHEMA_PATH_OUTSIDE_VAULT", f"Link component in {field}")
    try:
        path.resolve().relative_to(root)
    except ValueError as exc:
        raise SchemaError("SCHEMA_PATH_OUTSIDE_VAULT", f"{field} escapes vault") from exc
    return path


_DEFAULT_TEMPLATES = {
    "compilation_path_template": "wiki/{domain}/{topic-slug}-compilation.md",
    "strategy_path_template": "wiki/{domain}/strategy/{goal-slug}.md",
    "distillation_path_template": "wiki/{domain}/{topic-slug}/sources/{source-id}-{unit-id}.md",
    "raw_source": "raw/{kind}/{slug}.md",
}
_PROVENANCE = ("web", "book", "course", "pdf", "podcast", "video", "image", "thread", "document")
_MODALITIES = ("books", "articles", "threads", "videos", "documents", "assets")


def _resource_version(data):
    text = data.decode("utf-8-sig")
    match = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)", text, re.DOTALL)
    metadata = _load(match[1], "canonical metadata") if match else {}
    version = metadata.get("version") if isinstance(metadata, dict) else None
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+", version):
        raise SchemaError("SCHEMA_VERSION_UNAVAILABLE", "Invalid canonical resource metadata")
    return version


def resolve_schema(vault_root: Path, selection_key: str | None = None) -> ResolvedVault:
    original_root = Path(vault_root).absolute()
    if original_root.is_symlink() or (hasattr(original_root, "is_junction") and original_root.is_junction()):
        raise SchemaError("SCHEMA_PATH_OUTSIDE_VAULT", "Vault root must not be a link")
    root = original_root.resolve()
    if not root.is_dir():
        raise SchemaError("VAULT_NOT_DIRECTORY", "Vault root is not a directory")
    schema_path = _relative(root, ".wiki-schema.md", "schema")
    if not schema_path.is_file():
        raise FileNotFoundError(f"No .wiki-schema.md found at {schema_path}")
    source_bytes = schema_path.read_bytes()
    text = source_bytes.decode("utf-8-sig")
    declarations = _parse(text)
    identity = declarations.get("wiki_name", declarations.get("vault_name"))
    if "wiki_name" in declarations and "vault_name" in declarations and declarations["wiki_name"] != declarations["vault_name"]:
        raise SchemaError("SCHEMA_DECLARATION_CONFLICT", "wiki_name and vault_name disagree")
    if not isinstance(identity, str) or not identity.strip():
        raise SchemaError("SCHEMA_INVALID_DECLARATION", "Schema must declare vault_name or wiki_name")
    diagnostics = []
    canonical_version = canonical_hash = None
    if "extends" in declarations:
        if not isinstance(declarations["extends"], str) or not declarations["extends"]:
            raise SchemaError("SCHEMA_INVALID_DECLARATION", "extends must be a nonempty string")
        resources = Path(__file__).resolve().parents[1]
        current = resources / "canonical-schema.md"
        current_data = current.read_bytes()
        current_version = _resource_version(current_data)
        pin = declarations.get("extends_version")
        if pin is None:
            pin = current_version
            diagnostics.append({"code": "SCHEMA_VERSION_UNPINNED", "message": "Using current canonical schema", "details": {"version": pin}})
        if not isinstance(pin, str) or not re.fullmatch(r"\d+\.\d+", pin):
            raise SchemaError("SCHEMA_INVALID_DECLARATION", "extends_version must be a quoted major.minor string")
        if tuple(map(int, pin.split("."))) > tuple(map(int, current_version.split("."))):
            raise SchemaError("SCHEMA_VERSION_UNAVAILABLE", "Vault requires a newer canonical schema", {"version": pin})
        snapshot = current if pin == current_version else resources / "_archive" / f"canonical-schema-v{pin}.md"
        if not snapshot.is_file():
            raise SchemaError("SCHEMA_VERSION_UNAVAILABLE", "Canonical snapshot is not shipped", {"version": pin})
        data = snapshot.read_bytes()
        if _resource_version(data) != pin:
            raise SchemaError("SCHEMA_VERSION_UNAVAILABLE", "Canonical snapshot identity mismatch")
        canonical_version, canonical_hash = pin, sha256(data).hexdigest()
    else:
        diagnostics.append({"code": "SCHEMA_LEGACY_ADVISORY", "message": "Vault-only schema validation is advisory; ownership and fidelity gates remain required", "details": {}})
    domains = _strings(declarations.get("domains", []), "domains")
    topics = declarations.get("topics", {})
    aliases = declarations.get("aliases", {})
    if not isinstance(topics, dict) or not isinstance(aliases, dict):
        raise SchemaError("SCHEMA_INVALID_DECLARATION", "topics and aliases must be mappings")
    topics = {domain: _strings(items, f"topics.{domain}") for domain, items in topics.items()}
    # Legacy ontology blocks also group source/vault tags; these are not topics.
    # Preserve those declarations verbatim, but never expose them as output domains.
    topics = {domain: items for domain, items in topics.items()
              if domain in domains or not items
              or not all(item.startswith(("source/", "vault/")) for item in items)}
    if set(topics) - set(domains):
        raise SchemaError("SCHEMA_INVALID_DECLARATION", "Topic domains must be declared", {"domains": sorted(set(topics) - set(domains))})
    if any(v is not None and not isinstance(v, str) for v in aliases.values()):
        raise SchemaError("SCHEMA_INVALID_DECLARATION", "Alias targets must be strings or null")
    templates = {key: declarations.get(key, default) for key, default in _DEFAULT_TEMPLATES.items()}
    book = declarations.get("book_paths", {})
    if not isinstance(book, dict):
        raise SchemaError("SCHEMA_INVALID_DECLARATION", "book_paths must be a mapping")
    slug_by = book.get("slug_by", "title")
    if (slug_by is not None and not isinstance(slug_by, str)) or slug_by not in ("title", "author", None):
        raise SchemaError("SCHEMA_INVALID_DECLARATION", "book_paths.slug_by must be title or author")
    raw_layer = book.get("raw_layer", "raw/books/")
    if not isinstance(raw_layer, str) or not raw_layer:
        raise SchemaError("SCHEMA_INVALID_DECLARATION", "book_paths.raw_layer must be a nonempty string")
    _relative(root, raw_layer, "book_paths.raw_layer", template=True)
    folder = raw_layer.rstrip("/")
    if slug_by is not None:
        folder += "/{author-slug}" if slug_by == "author" else "/{book-slug}"
    for key, default in (("index_file", "_index.md"), ("chapter_pattern", "ch{NN}-{chapter-slug}.md"), ("raw_archive", "full-text.md")):
        value = book.get(key, default)
        if value is not None:
            if not isinstance(value, str):
                raise SchemaError("SCHEMA_INVALID_DECLARATION", f"book_paths.{key} must be a string or null")
            value = value.replace("chNN", "ch{NN}").replace("{slug}", "{chapter-slug}")
            templates[{"index_file": "book_index", "chapter_pattern": "book_chapter", "raw_archive": "book_archive"}[key]] = folder + "/" + value
    if "compilation_layer" in book:
        _relative(root, book["compilation_layer"], "book_paths.compilation_layer", template=True)
    for key, template in templates.items():
        _relative(root, template, key, template=True)
        _fields(template)
    hub = declarations.get("hub_file")
    if "hub_file" in declarations and (not isinstance(hub, str) or not hub):
        raise SchemaError("SCHEMA_INVALID_DECLARATION", "hub_file must be a nonempty string")
    if hub is None:
        hub = "_hub.md"
        legacy = root / "index.md"
        catalog = declarations.get("index_file")
        if not (root / hub).exists() and legacy.exists() and (catalog is None or _relative(root, catalog, "index_file").resolve() != legacy.resolve()):
            hub = "index.md"
            diagnostics.append({"code": "SCHEMA_LEGACY_HUB", "message": "Vault uses legacy index.md; declare hub_file or adopt _hub.md", "details": {"path": "index.md"}})
    controls = {"schema": ".wiki-schema.md", "hub": hub, "index": declarations.get("index_file"), "manifest": "raw/_manifest.json", "log": "log.md", "verify_log": "verify-log.md"}
    custom = declarations.get("canonical_files", {})
    if not isinstance(custom, dict):
        raise SchemaError("SCHEMA_INVALID_DECLARATION", "canonical_files must be a mapping")
    for role, path in custom.items():
        if role in controls and controls[role] != path:
            raise SchemaError("SCHEMA_DECLARATION_CONFLICT", f"Conflicting canonical role: {role}")
        controls[role] = path
    for role, path in controls.items():
        if path is not None:
            _relative(root, path, f"canonical_files.{role}")
    if controls["index"] is not None and _relative(root, controls["index"], "index_file").resolve() == _relative(root, hub, "hub_file").resolve():
        raise SchemaError("SCHEMA_DECLARATION_CONFLICT", "Catalog and hub must have distinct paths")
    if "glossary" not in controls and (root / ".glossary.md").exists():
        _relative(root, ".glossary.md", "canonical_files.glossary")
        controls["glossary"] = ".glossary.md"
    tiers = _strings(declarations.get("allowed_tiers", ["A", "B", "C"]), "allowed_tiers", "ABC")
    provenance = _strings(declarations.get("allowed_provenance_classes", list(_PROVENANCE)), "allowed_provenance_classes")
    extras = _strings(declarations.get("additional_raw_kinds", []), "additional_raw_kinds")
    modalities = _strings(declarations.get("raw_modalities", list(_MODALITIES) + list(extras)), "raw_modalities")
    for modality in modalities:
        if not re.fullmatch(r"[a-z][a-z0-9-]*", modality):
            raise SchemaError("SCHEMA_INVALID_DECLARATION", "Invalid raw modality")
    roots = _strings(declarations.get("content_roots", ["raw/", "wiki/"]), "content_roots")
    read_roots = tuple(_relative(root, path, "content_roots") for path in roots)
    authority = declarations.get("source_hash_authority")
    if authority is not None and (not isinstance(authority, str) or not authority):
        raise SchemaError("SCHEMA_INVALID_DECLARATION", "source_hash_authority must be a nonempty string")
    if "outbound_archiving" in declarations and type(declarations["outbound_archiving"]) is not bool:
        raise SchemaError("SCHEMA_INVALID_DECLARATION", "outbound_archiving must be boolean")
    enrollment = declarations.get("workflow_controls_version")
    if enrollment is not None and (type(enrollment) is not int or enrollment != 1):
        raise SchemaError("SCHEMA_INVALID_DECLARATION", "Unknown workflow controls version")
    navigation = declarations.get("managed_navigation", {})
    if not isinstance(navigation, dict):
        raise SchemaError("SCHEMA_INVALID_DECLARATION", "managed_navigation must be a mapping")
    for path, region in navigation.items():
        _relative(root, path, "managed_navigation")
        if not isinstance(region, str) or region not in {"controls", "navigation", "catalog", "operations", "verification"}:
            raise SchemaError("SCHEMA_INVALID_DECLARATION", "Unknown navigation region")
    begin, end = "<!-- ash_wiki:begin controls -->", "<!-- ash_wiki:end controls -->"
    if text.count(begin) != text.count(end) or text.count(begin) > 1 or (begin in text and text.index(begin) > text.index(end)):
        raise SchemaError("SCHEMA_INVALID_DECLARATION", "Unbalanced or duplicate controls markers")
    enrolled = False
    if begin in text:
        owned = text.split(begin, 1)[1].split(end, 1)[0]
        block = re.fullmatch(r"\s*```(?:yaml|yml)(?: controls)?\s*\n(.*?)\n```\s*", owned, re.DOTALL)
        if not block:
            raise SchemaError("SCHEMA_INVALID_DECLARATION", "Controls region must contain one YAML declaration block")
        owned_declarations = _load(block[1], "owned controls")
        enrolled = isinstance(owned_declarations, dict) and type(owned_declarations.get("workflow_controls_version")) is int and owned_declarations["workflow_controls_version"] == 1
    for category in ("descriptors", "content_types", "status_tags"):
        if category in declarations:
            _strings(declarations[category], category)
    revision_field = next((field for field in ("version", "schema_version", "revision") if field in declarations), None)
    return ResolvedVault(selection_key, root, identity, revision_field,
                         declarations.get(revision_field), canonical_version, canonical_hash,
                         domains, _freeze(topics), _freeze(aliases), read_roots, _freeze(templates), _freeze(controls),
                         tiers, provenance, modalities, authority,
                         InputRef(str(schema_path), sha256(source_bytes).hexdigest(), "text/markdown"),
                         _freeze(declarations), text, tuple(_freeze(d) for d in diagnostics),
                         enrolled)


def _fields(template):
    try:
        fields = []
        for _, field, spec, conversion in Formatter().parse(template):
            if field is not None:
                if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", field) or conversion or spec:
                    raise ValueError("Only simple named placeholders are supported")
                fields.append(field)
        return fields
    except ValueError as exc:
        raise SchemaError("SCHEMA_INVALID_PATH", "Invalid output template", {"template": template}) from exc


def resolve_output_path(resolved: ResolvedVault, template_key: str, values: Mapping[str, str]) -> Path:
    if template_key not in resolved.output_templates:
        raise SchemaError("SCHEMA_UNKNOWN_TEMPLATE", f"Unknown output template: {template_key}")
    tier = "C" if template_key == "strategy_path_template" else ("B" if template_key in {"compilation_path_template", "distillation_path_template"} else "A")
    if tier not in resolved.allowed_tiers:
        raise SchemaError("SCHEMA_TIER_PROHIBITED", f"Vault does not permit Tier {tier}")
    if template_key.startswith("book_") and "books" not in resolved.raw_modalities:
        raise SchemaError("SCHEMA_RAW_MODALITY_PROHIBITED", "Vault does not permit book capture")
    template = resolved.output_templates[template_key]
    fields = _fields(template)
    for field in fields:
        if field not in values or not isinstance(values[field], str) or (not values[field] and field != "section"):
            raise SchemaError("SCHEMA_UNRESOLVED_PLACEHOLDER", f"Missing output value: {field}")
        value = values[field]
        if value and (not re.fullmatch(r"[\w.-]+", value, re.UNICODE) or value in {".", ".."}):
            raise SchemaError("SCHEMA_INVALID_PATH", f"Unsafe output value: {field}")
    if "domain" in fields and values["domain"] not in resolved.domains:
        raise SchemaError("SCHEMA_INVALID_DECLARATION", "Output domain is not declared")
    if "source-id" in fields and not re.fullmatch(r"[0-9a-f]{64}", values["source-id"]):
        raise SchemaError("SCHEMA_INVALID_DECLARATION", "source-id must be a full lowercase SHA256")
    if "unit-id" in fields and not re.fullmatch(r"u[0-9]{6}", values["unit-id"]):
        raise SchemaError("SCHEMA_INVALID_DECLARATION", "unit-id must be a stable inventory unit")
    if "kind" in fields and values["kind"] not in resolved.raw_modalities:
        raise SchemaError("SCHEMA_RAW_MODALITY_PROHIBITED", "Raw modality is not permitted")
    path = template.format_map(values)
    # Only an explicitly empty section component may collapse; other empty components refuse.
    if values.get("section") == "":
        path = path.replace("//", "/")
    if "//" in path or "{" in path or "}" in path:
        raise SchemaError("SCHEMA_UNRESOLVED_PLACEHOLDER", "Unresolved output path")
    result = _relative(resolved.root, path, template_key)
    relative = result.relative_to(resolved.root)
    if tier in {"B", "C"} and relative.parts[0].casefold() == "raw":
        raise SchemaError("SCHEMA_IMMUTABLE_RAW", "Derived artifacts cannot overwrite immutable raw evidence")
    protected = {p.casefold() for p in resolved.canonical_files.values() if p is not None}
    if relative.as_posix().casefold() in protected:
        raise SchemaError("SCHEMA_PROTECTED_REFERENCE", "Output collides with a canonical control/reference")
    return result
