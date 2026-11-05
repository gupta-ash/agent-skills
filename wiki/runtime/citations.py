"""Citation syntax and exact raw-span binding, not claim truth verification.

Only ^[[[slug]]] and retained ^[[slug]] are evidence markers. Ordinary links
are navigation. Callers supply the capture-bound raw InputRefs explicitly; a
recursive search or a filename match alone never supplies evidence authority.
Span offsets are Unicode code-point offsets in the *original UTF-8 document*,
not byte offsets or offsets in a normalized body. No normalization hides changes.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re
from typing import Mapping, Sequence
import yaml

from _workflow.contracts import InputRef, ValidationResult
from .capture import contained_file, file_bytes, issue


class CitationError(ValueError):
    pass


@dataclass(frozen=True)
class Citation:
    slug: str
    start: int
    end: int
    legacy: bool


class _EvidenceLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        self.flatten_mapping(node)
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, (str, int, float, bool, type(None))) or key in result:
                raise CitationError("Conflicting/duplicate raw frontmatter keys")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


_NAVIGATION = re.compile(r"(?<![\^\[])\[\[(?!\[)[^\]\n]+\]\](?!\])|(?<!!)\[[^\]\n]+\]\([^\)\n]+\)")


def _blank(text: str) -> str:
    return "".join("\n" if char == "\n" else " " for char in text)


def evidence_text(text: str) -> str:
    """Mask frontmatter, comments, fenced/indented/inline code; retain offsets."""
    if not isinstance(text, str):
        raise CitationError("Evidence text must be a string")
    masked = list(text)
    def hide(start, end):
        masked[start:end] = _blank(text[start:end])
    lines = text.splitlines(keepends=True)
    offset, fence, frontmatter = 0, None, bool(lines and lines[0].lstrip("\ufeff").rstrip("\r\n") == "---")
    for index, line in enumerate(lines):
        if frontmatter:
            hide(offset, offset + len(line))
            if index and line.rstrip("\r\n") in {"---", "..."}:
                frontmatter = False
        elif fence:
            hide(offset, offset + len(line))
            closing = re.fullmatch(r" {0,3}" + re.escape(fence[0]) + "{" + str(fence[1]) + r",}[ \t]*(?:\r?\n)?", line)
            if closing:
                fence = None
        else:
            opening = re.match(r" {0,3}(`{3,}|~{3,})(.*)", line)
            if opening and not (opening[1][0] == "`" and "`" in opening[2]):
                fence = (opening[1][0], len(opening[1]))
                hide(offset, offset + len(line))
            elif line.startswith(("    ", "\t")):
                hide(offset, offset + len(line))
        offset += len(line)
    current = "".join(masked)
    for match in re.finditer(r"<!--(?:[\s\S]*?-->|[\s\S]*$)", current):
        hide(match.start(), match.end())
    current = "".join(masked)
    for match in re.finditer(r"(?<!`)(`+)(?!`)([\s\S]*?)(?<!`)\1(?!`)", current):
        hide(match.start(), match.end())
    current = "".join(masked)
    for match in re.finditer(r"<(pre|code)\b[^>]*>(?:[\s\S]*?</\1\s*>|[\s\S]*$)", current, re.IGNORECASE):
        hide(match.start(), match.end())
    current = "".join(masked)
    # Navigation links in generated notes are not evidence markers.
    for match in _NAVIGATION.finditer(current):
        hide(match.start(), match.end())
    return "".join(masked)


def extract_citations(text: str) -> tuple[Citation, ...]:
    """Extract evidence markers outside code/comments, with original offsets."""
    visible = evidence_text(text)
    pattern = r"(?<![\\\[])\^\[\[\[([^\[\]\n]+)\]\]\](?!\])|(?<![\\\[])\^\[\[([^\[\]\n]+)\]\](?!\])"
    result = []
    for match in re.finditer(pattern, visible):
        slug = match[1] if match[1] is not None else match[2]
        # Aliases, headings, block refs and path tricks are not retained raw slugs.
        if slug.strip() == slug and re.fullmatch(r"[\w-]+(?:/[\w-]+)*", slug, re.UNICODE):
            result.append(Citation(slug, match.start(), match.end(), match[1] is None))
    return tuple(result)


def _ref(ref: InputRef | Mapping) -> dict:
    if isinstance(ref, InputRef):
        return {"path": ref.path, "sha256": ref.sha256}
    if isinstance(ref, Mapping):
        return {"path": ref.get("path"), "sha256": ref.get("sha256")}
    raise CitationError("Raw reference must be an InputRef or bound mapping")


def _raw_text(ref: InputRef | Mapping, root: Path) -> tuple[Path, str]:
    ref = _ref(ref)
    path = contained_file(root, ref["path"])
    data = file_bytes(root, ref)
    try:
        text = data.decode("utf-8")
    except UnicodeError as exc:
        raise CitationError("Raw span requires a UTF-8 transcript, not interpretation of a binary") from exc
    if text.lstrip("\ufeff").startswith(("---\n", "---\r\n")):
        lines = text.splitlines()
        closing = next((i for i in range(1, len(lines)) if lines[i] in {"---", "..."}), None)
        if closing is None:
            raise CitationError("Unclosed raw frontmatter")
        try:
            metadata = yaml.load("\n".join(lines[1:closing]), Loader=_EvidenceLoader)
        except yaml.YAMLError as exc:
            raise CitationError("Invalid raw frontmatter") from exc
        if metadata is not None and not isinstance(metadata, dict):
            raise CitationError("Raw frontmatter must be a mapping")
        metadata = metadata or {}
        if metadata.get("type") not in (None, "source") or metadata.get("artifact_role") not in (None, "capture"):
            raise CitationError("Generated notes, entities and raw MOCs are not source evidence")
        if metadata.get("type") is None and any(key in metadata for key in ("compiled_from", "contributing_sources", "contributing_books")):
            raise CitationError("Derived content cannot masquerade as raw evidence")
    return path, text


def bind_citation_targets(citation_targets, raw_inputs) -> dict[str, InputRef]:
    """Bind source-declared canonical slugs; aliases never grant file authority."""
    if citation_targets is None:
        return {}
    if not isinstance(citation_targets, Mapping):
        raise CitationError("Citation targets must be an explicit mapping")
    def typed(value):
        if isinstance(value, InputRef):
            return value
        if not isinstance(value, Mapping) or set(value) != {"path", "sha256", "media_type"}:
            raise CitationError("Citation targets require complete InputRefs")
        try:
            return InputRef(value["path"], value["sha256"], value["media_type"])
        except (ValueError, TypeError) as exc:
            raise CitationError("Invalid citation target InputRef") from exc
    supplied = {typed(ref) for ref in raw_inputs}
    result = {}
    for slug, value in citation_targets.items():
        if not isinstance(slug, str) or not slug.startswith("raw/") or not re.fullmatch(r"[\w-]+(?:/[\w-]+)*", slug):
            raise CitationError("Citation targets require safe canonical raw-relative slugs")
        ref = typed(value)
        if ref not in supplied:
            raise CitationError("Citation target is not an exact supplied raw InputRef")
        result[slug] = ref
    return result


def resolve_raw_reference(slug: str, raw_inputs: Sequence[InputRef | Mapping], root: Path, *, citation_targets: Mapping[str, InputRef | Mapping] | None = None) -> InputRef:
    """Resolve one marker against explicit raw inputs, rejecting ambiguity/staleness.

    Bare slugs match stems; qualified slugs match root-relative paths without the
    extension. A second actual raw with the same stem is ambiguous even if it has
    identical content. Duplicate declarations of one physical file do not add an
    origin, but conflicting hashes are refused. Explicit canonical targets may
    resolve to current private raw members without granting another read root.
    """
    if not isinstance(slug, str) or not re.fullmatch(r"[\w-]+(?:/[\w-]+)*", slug):
        raise CitationError("Invalid raw citation slug")
    root = Path(root).absolute()
    matches = {}
    for raw in raw_inputs:
        ref = _ref(raw)
        path = contained_file(root, ref["path"])
        target = path.relative_to(root).with_suffix("").as_posix() if "/" in slug else path.stem
        if target != slug:
            continue
        _raw_text(raw, root)
        if path in matches and matches[path]["sha256"] != ref["sha256"]:
            raise CitationError("Conflicting hashes for one raw input")
        matches[path] = ref
    for target, raw in bind_citation_targets(citation_targets, raw_inputs).items():
        if (target if "/" in slug else target.rsplit("/", 1)[-1]) != slug:
            continue
        path, _text = _raw_text(raw, root)
        ref = _ref(raw)
        if path in matches and matches[path]["sha256"] != ref["sha256"]:
            raise CitationError("Conflicting direct and aliased raw identities")
        matches[path] = ref
    if len(matches) != 1:
        raise CitationError("Citation does not resolve to exactly one current raw input")
    path, ref = next(iter(matches.items()))
    return InputRef(str(path), ref["sha256"], "text/markdown" if path.suffix.lower() == ".md" else "text/plain")


def validate_raw_span(raw_ref: InputRef | Mapping, root: Path, span: str, start: int, end: int) -> ValidationResult:
    """Bind original source text, including metadata/examples; not entailment."""
    try:
        _, text = _raw_text(raw_ref, root)
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text):
            raise CitationError("Span offsets are invalid")
        if not isinstance(span, str) or not span.strip() or text[start:end] != span:
            raise CitationError("Supplied span is not the exact bound raw text")
        # Source examples/metadata are evidence, not generated-note claims.
        # Only the latter use evidence_text's code/frontmatter masking.
        if not _NAVIGATION.sub("", span).strip():
            raise CitationError("Navigation links alone cannot support a claim")
        return ValidationResult(True, diagnostics=(issue("EXACT_SPAN_ONLY", "Text existence is not semantic support or claim verification"),))
    except (ValueError, OSError, TypeError) as exc:
        return ValidationResult(False, (issue("RAW_SPAN_INVALID", str(exc)),))


def validate_citations(text: str, raw_inputs: Sequence[InputRef | Mapping], root: Path, supports: list[dict], *, citation_targets: Mapping[str, InputRef | Mapping] | None = None) -> ValidationResult:
    """Bind every visible marker to one exact raw span and its claim text hash.

    Each support has citation_start, raw_path, raw_sha256, start, end, span,
    claim_start, claim_end, claim_sha256. Claim offsets refer to text and contain
    the marker. This binds the evaluated claim; it does NOT judge its meaning.
    No missing/duplicate/extra support defaults to verified.
    """
    errors, indexed = [], {}
    citations = extract_citations(text)
    if not isinstance(supports, list):
        return ValidationResult(False, (issue("CITATION_SUPPORT_INVALID", "supports must be an array"),))
    required = {"citation_start", "raw_path", "raw_sha256", "start", "end", "span", "claim_start", "claim_end", "claim_sha256"}
    for support in supports:
        if not isinstance(support, dict) or set(support) != required or type(support["citation_start"]) is not int:
            errors.append(issue("CITATION_SUPPORT_INVALID", "Support requires exact claim/raw identities and spans"))
            continue
        offset = support["citation_start"]
        if offset in indexed:
            errors.append(issue("CITATION_DUPLICATE_SUPPORT", "A marker has multiple supplied bindings", citation_start=offset))
        indexed[offset] = support
    for citation in citations:
        binding = indexed.pop(citation.start, None)
        if binding is None:
            errors.append(issue("CITATION_SUPPORT_MISSING", "No bound raw span for marker", citation_start=citation.start))
            continue
        try:
            raw = resolve_raw_reference(citation.slug, raw_inputs, root, citation_targets=citation_targets)
            if contained_file(root, binding["raw_path"]) != Path(raw.path) or binding["raw_sha256"] != raw.sha256:
                raise CitationError("Support binds a different raw identity/hash")
            start, end = binding["claim_start"], binding["claim_end"]
            if type(start) is not int or type(end) is not int or not 0 <= start <= citation.start < citation.end <= end <= len(text):
                raise CitationError("Claim span does not contain its citation")
            if sha256(text[start:end].encode("utf-8")).hexdigest() != binding["claim_sha256"]:
                raise CitationError("Claim bytes changed since evaluation")
            errors.extend(validate_raw_span(raw, root, binding["span"], binding["start"], binding["end"]).errors)
        except (ValueError, OSError, TypeError) as exc:
            errors.append(issue("CITATION_BINDING_INVALID", str(exc), citation_start=citation.start))
    for offset in indexed:
        errors.append(issue("CITATION_PHANTOM_SUPPORT", "Binding has no visible citation", citation_start=offset))
    return ValidationResult(not errors, tuple(errors), (issue("CITATION_BINDING_ONLY", "Syntax, unique raw identity and exact spans do not prove semantic entailment"),))
