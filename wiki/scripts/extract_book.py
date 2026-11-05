#!/usr/bin/env python
"""Extract chapters from books (EPUB, MOBI, AZW3, image-only PDF).

Usage:
    python extract_book.py <filepath>
    python extract_book.py --extract-images <filepath>
    python extract_book.py --list-chapters <filepath>
    python extract_book.py --list-library <calibre-root>
    python extract_book.py --calibre-dir <book-directory>

Outputs structured JSON to stdout.
"""

import argparse
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

# Force UTF-8 stdout on Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


# Try to import SimpleHTMLToText from extract_document.py
_SimpleHTMLToText = None
try:
    sys.path.insert(0, os.path.dirname(__file__))
    from extract_document import SimpleHTMLToText as _SimpleHTMLToText
except ImportError:
    pass

# Try PyMuPDF for image-only PDF support
try:
    import fitz as _fitz
except ImportError:
    _fitz = None


URL_REGEX = re.compile(r"https?://[^\s\)\]>\"'`]+")

# Image extraction thresholds
MIN_IMAGE_BYTES = 5000
MIN_IMAGE_DIMENSION = 50
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".tif", ".webp", ".svg"}

# Book file extensions
BOOK_EXTENSIONS = {".epub", ".mobi", ".azw3", ".azw", ".pdf"}

# OPF / Dublin Core namespaces
NS_DC = "http://purl.org/dc/elements/1.1/"
NS_OPF = "http://www.idpf.org/2007/opf"
NS_CONTAINER = "urn:oasis:names:tc:opendocument:xmlns:container"
NS_NCX = "http://www.daisy.org/z3986/2005/ncx/"
NS_XHTML = "http://www.w3.org/1999/xhtml"
NS_EPUB = "http://www.idpf.org/2007/ops"


def extract_urls(text: str) -> list[str]:
    """Extract all URLs from text, deduplicated."""
    if not text:
        return []
    urls = URL_REGEX.findall(text)
    cleaned = [u.rstrip(".,;:!?)") for u in urls]
    return list(dict.fromkeys(cleaned))


def slugify(text: str) -> str:
    """Convert title to kebab-case slug (lowercase, hyphens, max 60 chars)."""
    if not text:
        return "untitled"
    slug = text.lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:60] if slug else "untitled"


def _clean_word_count(content_md: str, title: str = "", author: str = "") -> int:
    """Compute word count with publisher-running-header bleed mitigated.

    Heuristic: drop short all-caps lines containing the book title (>=4 chars
    after normalization) or any author surname (>=4 chars) matched at WORD
    BOUNDARIES. These are the dominant form of running headers Calibre's
    EPUB conversion preserves from print source. Applied only to short
    sections (<=500 words pre-strip) where header bleed is a significant
    fraction; substantive prose chapters return unchanged.

    Conservative by design:
    - Only ALL-CAPS lines (typical Calibre print-to-EPUB header form).
    - Title substring match requires title length >= 4 (skips ambiguous short
      titles like "It" matching "ITALIC").
    - Author tokens matched at WORD BOUNDARIES via regex (e.g., "KING"
      matches "KING JR." but NOT "KINGDOM" or "VIKING").
    - Stopword set covers common short words to reduce false-positive header
      matches from generic vocabulary.

    Does NOT detect cross-chapter line repetition (the robust fix); that
    requires a second pass after all chapters extracted. Logged as a known
    limitation; revisit if downstream ingestion surfaces other header forms
    (e.g., mixed-case running heads, headers without title/author tokens).
    """
    if not content_md:
        return 0

    raw_word_count = len(content_md.split())
    if raw_word_count > 500:
        return raw_word_count

    title_upper = (title or "").upper().strip()
    # Skip ambiguously-short titles to avoid false positives (e.g., "It" in "ITALIC")
    if len(title_upper) < 4:
        title_upper = ""

    author_tokens: set[str] = set()
    if author:
        for raw in author.upper().replace(",", " ").replace("&", " ").split():
            if len(raw) >= 4 and raw not in {"AND", "THE", "FROM", "WITH", "BY", "OF", "FOR", "AN"}:
                author_tokens.add(raw)
    # Word-boundary regex per token: prevents KING matching KINGDOM/VIKING.
    author_patterns = [re.compile(r"\b" + re.escape(tok) + r"\b") for tok in author_tokens]

    def is_header_line(line: str) -> bool:
        stripped = line.strip()
        if not stripped or stripped != stripped.upper():
            return False
        if title_upper and title_upper in stripped:
            return True
        return any(pat.search(stripped) for pat in author_patterns)

    cleaned = [line for line in content_md.split("\n") if not is_header_line(line)]
    return len(" ".join(cleaned).split())


# ---------------------------------------------------------------------------
# Inline fallback HTML-to-text converter (used if extract_document import fails)
# ---------------------------------------------------------------------------

class _FallbackHTMLToText:
    """Minimal HTML to markdown converter using stdlib html.parser."""

    STRIP_TAGS = {"script", "style", "nav", "header", "footer", "aside", "noscript", "iframe", "svg"}

    def __init__(self, extract_images: bool = False):
        from html.parser import HTMLParser

        self._extract_images = extract_images
        self.image_refs = []

        outer = self  # closure ref

        class _Parser(HTMLParser):
            def __init__(self):
                super().__init__()
                self._output = []
                self._skip_depth = 0
                self._in_pre = False

            def handle_starttag(self, tag, attrs):
                tag = tag.lower()
                if tag in outer.STRIP_TAGS:
                    self._skip_depth += 1
                    return
                if self._skip_depth > 0:
                    return
                if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
                    level = int(tag[1])
                    self._output.append("\n\n" + "#" * level + " ")
                elif tag == "p":
                    self._output.append("\n\n")
                elif tag == "br":
                    self._output.append("\n")
                elif tag == "hr":
                    self._output.append("\n\n---\n\n")
                elif tag == "pre":
                    self._in_pre = True
                    self._output.append("\n\n```\n")
                elif tag == "code" and not self._in_pre:
                    self._output.append("`")
                elif tag in ("strong", "b"):
                    self._output.append("**")
                elif tag in ("em", "i"):
                    self._output.append("*")
                elif tag == "li":
                    self._output.append("\n- ")
                elif tag in ("ul", "ol"):
                    self._output.append("\n")
                elif tag == "img" and outer._extract_images:
                    attrs_dict = dict(attrs)
                    src = attrs_dict.get("src", "")
                    alt = attrs_dict.get("alt", "")
                    if src:
                        outer.image_refs.append({"src": src, "alt": alt})
                        self._output.append(f"\n[IMAGE: {src} | {alt}]\n")

            def handle_endtag(self, tag):
                tag = tag.lower()
                if tag in outer.STRIP_TAGS:
                    self._skip_depth = max(0, self._skip_depth - 1)
                    return
                if self._skip_depth > 0:
                    return
                if tag == "pre":
                    self._in_pre = False
                    self._output.append("\n```\n")
                elif tag == "code" and not self._in_pre:
                    self._output.append("`")
                elif tag in ("strong", "b"):
                    self._output.append("**")
                elif tag in ("em", "i"):
                    self._output.append("*")

            def handle_data(self, data):
                if self._skip_depth > 0:
                    return
                if self._in_pre:
                    self._output.append(data)
                else:
                    self._output.append(re.sub(r"\s+", " ", data))

            def handle_entityref(self, name):
                char_map = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "nbsp": " "}
                if self._skip_depth == 0:
                    self._output.append(char_map.get(name, f"&{name};"))

            def handle_charref(self, name):
                try:
                    ch = chr(int(name[1:], 16)) if name.startswith("x") else chr(int(name))
                except (ValueError, OverflowError):
                    ch = f"&#{name};"
                if self._skip_depth == 0:
                    self._output.append(ch)

            def get_text(self) -> str:
                text = "".join(self._output)
                text = re.sub(r"\n{3,}", "\n\n", text)
                return text.strip()

        self._parser = _Parser()

    def feed(self, data: str):
        self._parser.feed(data)

    def get_text(self) -> str:
        return self._parser.get_text()


def _get_html_converter(extract_images: bool = False):
    """Return an HTML-to-text converter instance."""
    if _SimpleHTMLToText is not None:
        return _SimpleHTMLToText(extract_images=extract_images)
    return _FallbackHTMLToText(extract_images=extract_images)


# ---------------------------------------------------------------------------
# XHTML → Markdown conversion
# ---------------------------------------------------------------------------

def xhtml_to_markdown(xhtml_bytes: bytes, extract_images: bool = False) -> tuple[str, list]:
    """Convert XHTML bytes to markdown text.

    Returns (markdown_text, image_refs).
    """
    # Decode XHTML — try UTF-8, then latin-1
    try:
        text = xhtml_bytes.decode("utf-8", errors="replace")
    except Exception:
        text = xhtml_bytes.decode("latin-1", errors="replace")

    converter = _get_html_converter(extract_images=extract_images)
    converter.feed(text)
    md = converter.get_text()
    image_refs = getattr(converter, "image_refs", [])
    return md, image_refs


# ---------------------------------------------------------------------------
# EPUB TOC Parsers
# ---------------------------------------------------------------------------

def _resolve_href(href: str, base_dir: str) -> str:
    """Resolve a relative href against a base directory within the ZIP."""
    if not href:
        return ""
    # Strip fragment
    path = href.split("#")[0]
    if not path:
        return ""
    # Normalize path relative to base_dir
    combined = os.path.normpath(os.path.join(base_dir, path)).replace("\\", "/")
    return combined


def _strip_fragment(href: str) -> str:
    """Return href without the #fragment."""
    return href.split("#")[0] if href else ""


def parse_toc_ncx(z: zipfile.ZipFile, ncx_path: str, opf_dir: str) -> list[dict]:
    """Parse EPUB 2 TOC from toc.ncx.

    Returns list of {title, href, level, play_order}.
    """
    try:
        ncx_data = z.read(ncx_path)
    except KeyError:
        return []

    try:
        root = ET.fromstring(ncx_data)
    except ET.ParseError:
        return []

    entries = []

    def _parse_navpoints(parent, level=1):
        for np in parent:
            tag = np.tag
            # Handle namespace
            if "}" in tag:
                tag = tag.split("}", 1)[1]
            if tag != "navPoint":
                continue

            title = ""
            href = ""
            play_order = 0

            try:
                play_order = int(np.get("playOrder", "0"))
            except (ValueError, TypeError):
                pass

            # Find navLabel/text
            for child in np:
                ctag = child.tag.split("}", 1)[-1] if "}" in child.tag else child.tag
                if ctag == "navLabel":
                    for t in child:
                        ttag = t.tag.split("}", 1)[-1] if "}" in t.tag else t.tag
                        if ttag == "text" and t.text:
                            title = t.text.strip()
                elif ctag == "content":
                    href = child.get("src", "")

            if title and href:
                resolved = _resolve_href(href, opf_dir)
                entries.append({
                    "title": title,
                    "href": resolved,
                    "href_raw": href,
                    "level": level,
                    "play_order": play_order,
                })

            # Recurse into nested navPoints
            _parse_navpoints(np, level + 1)

    # Find navMap
    nav_map = None
    for child in root:
        ctag = child.tag.split("}", 1)[-1] if "}" in child.tag else child.tag
        if ctag == "navMap":
            nav_map = child
            break

    if nav_map is not None:
        _parse_navpoints(nav_map, level=1)

    return entries


def parse_toc_nav(z: zipfile.ZipFile, nav_path: str, opf_dir: str) -> list[dict]:
    """Parse EPUB 3 TOC from nav.xhtml.

    Returns list of {title, href, level}.
    """
    try:
        nav_data = z.read(nav_path)
    except KeyError:
        return []

    # Parse as HTML since nav.xhtml may have namespaces that trip up ET
    try:
        text = nav_data.decode("utf-8", errors="replace")
    except Exception:
        text = nav_data.decode("latin-1", errors="replace")

    entries = []

    # Use regex-based extraction for robustness with XHTML namespaces
    # Find the toc nav element
    # Look for <nav epub:type="toc"> or <nav id="toc"> patterns
    toc_match = re.search(
        r'<nav[^>]*(?:epub:type\s*=\s*"toc"|id\s*=\s*"toc")[^>]*>(.*?)</nav>',
        text, re.DOTALL | re.IGNORECASE
    )
    if not toc_match:
        # Try without namespace prefix — some EPUB3 files omit it
        toc_match = re.search(
            r'<nav[^>]*type\s*=\s*"toc"[^>]*>(.*?)</nav>',
            text, re.DOTALL | re.IGNORECASE
        )

    if not toc_match:
        return entries

    toc_html = toc_match.group(1)

    # Compute nav file's directory for href resolution
    nav_dir = os.path.dirname(nav_path).replace("\\", "/")

    def _parse_list(html: str, level: int = 1):
        """Parse <ol>/<li>/<a> structure recursively."""
        # Find all <li> blocks
        li_pattern = re.compile(r'<li[^>]*>(.*?)</li>', re.DOTALL | re.IGNORECASE)
        for li_match in li_pattern.finditer(html):
            li_content = li_match.group(1)

            # Extract first <a> tag
            a_match = re.search(r'<a[^>]*href\s*=\s*"([^"]*)"[^>]*>(.*?)</a>', li_content, re.DOTALL | re.IGNORECASE)
            if a_match:
                href_raw = a_match.group(1)
                title = re.sub(r'<[^>]+>', '', a_match.group(2)).strip()

                if title and href_raw:
                    resolved = _resolve_href(href_raw, nav_dir)
                    entries.append({
                        "title": title,
                        "href": resolved,
                        "href_raw": href_raw,
                        "level": level,
                    })

            # Check for nested <ol>
            nested_ol = re.search(r'<ol[^>]*>(.*?)</ol>', li_content, re.DOTALL | re.IGNORECASE)
            if nested_ol:
                _parse_list(nested_ol.group(1), level + 1)

    # Find top-level <ol>
    ol_match = re.search(r'<ol[^>]*>(.*?)</ol>', toc_html, re.DOTALL | re.IGNORECASE)
    if ol_match:
        _parse_list(ol_match.group(1), level=1)

    return entries


# ---------------------------------------------------------------------------
# Spine ↔ Chapter mapping
# ---------------------------------------------------------------------------

def map_spine_to_chapters(
    spine_hrefs: list[str],
    toc_entries: list[dict],
    z: zipfile.ZipFile,
    opf_dir: str,
    extract_images: bool = False,
    image_dir: str | None = None,
) -> tuple[list[dict], dict, dict]:
    """Map spine items to TOC chapters.

    Returns (chapters, front_matter, back_matter).
    """
    if not spine_hrefs:
        return [], {"has_content": False, "sections": [], "content_markdown": ""}, \
               {"has_content": False, "sections": [], "content_markdown": ""}

    # Build a map from spine file (without fragment) to TOC entries that start in it
    toc_by_file = {}
    for entry in toc_entries:
        file_key = _strip_fragment(entry["href"])
        if file_key not in toc_by_file:
            toc_by_file[file_key] = []
        toc_by_file[file_key].append(entry)

    # Build ordered list of TOC start points
    # Each has (spine_index, toc_entry)
    toc_starts = []
    for i, spine_href in enumerate(spine_hrefs):
        if spine_href in toc_by_file:
            for entry in toc_by_file[spine_href]:
                toc_starts.append((i, entry))

    # If no TOC entries map to spine, treat entire book as one chapter
    if not toc_starts:
        all_md = []
        all_images = []
        for spine_href in spine_hrefs:
            try:
                data = z.read(spine_href)
                md, imgs = xhtml_to_markdown(data, extract_images=extract_images)
                all_md.append(md)
                all_images.extend(imgs)
            except KeyError:
                continue

        combined = "\n\n".join(all_md)
        word_count = len(combined.split())
        chapters = [{
            "number": 1,
            "title": "Full Text",
            "slug": "full-text",
            "content_markdown": combined,
            "word_count": word_count,
            "has_inline_images": bool(all_images),
            "extracted_images": _save_chapter_images(all_images, z, opf_dir, image_dir, 1) if extract_images else [],
            "spine_index": 0,
        }]
        return chapters, {"has_content": False, "sections": [], "content_markdown": ""}, \
               {"has_content": False, "sections": [], "content_markdown": ""}

    # Determine front matter = spine items before first TOC entry
    first_toc_spine_idx = toc_starts[0][0]
    front_spine = spine_hrefs[:first_toc_spine_idx]

    # Determine back matter = spine items after last TOC entry's range
    last_toc_spine_idx = toc_starts[-1][0]
    # Find the end of the last chapter
    last_chapter_end = last_toc_spine_idx + 1
    if len(toc_starts) > 1:
        # The last chapter extends to the end of spine
        last_chapter_end = len(spine_hrefs)
    back_spine = spine_hrefs[last_chapter_end:] if last_chapter_end < len(spine_hrefs) else []

    # Build chapters
    chapters = []
    for ch_idx, (spine_idx, toc_entry) in enumerate(toc_starts):
        # Chapter spans from this spine_idx to the next TOC start's spine_idx
        if ch_idx + 1 < len(toc_starts):
            next_spine_idx = toc_starts[ch_idx + 1][0]
        else:
            next_spine_idx = len(spine_hrefs)

        chapter_spines = spine_hrefs[spine_idx:next_spine_idx]

        ch_parts = []
        ch_images = []
        for sp_href in chapter_spines:
            try:
                data = z.read(sp_href)
                md, imgs = xhtml_to_markdown(data, extract_images=extract_images)
                ch_parts.append(md)
                ch_images.extend(imgs)
            except KeyError:
                continue

        content_md = "\n\n".join(ch_parts)
        word_count = len(content_md.split())
        ch_num = ch_idx + 1

        saved_images = []
        if extract_images and ch_images and image_dir:
            saved_images = _save_chapter_images(ch_images, z, opf_dir, image_dir, ch_num)

        chapters.append({
            "number": ch_num,
            "title": toc_entry["title"],
            "slug": slugify(toc_entry["title"]),
            "content_markdown": content_md,
            "word_count": word_count,
            "has_inline_images": bool(ch_images),
            "extracted_images": saved_images,
            "spine_index": spine_idx,
        })

    # Build front matter
    front_md = ""
    front_sections = []
    if front_spine:
        fm_parts = []
        for sp_href in front_spine:
            try:
                data = z.read(sp_href)
                md, _ = xhtml_to_markdown(data)
                if md.strip():
                    fm_parts.append(md)
                    front_sections.append(sp_href.split("/")[-1])
            except KeyError:
                continue
        front_md = "\n\n".join(fm_parts)

    front_matter = {
        "has_content": bool(front_md.strip()),
        "sections": front_sections,
        "content_markdown": front_md,
    }

    # Build back matter
    back_md = ""
    back_sections = []
    if back_spine:
        bm_parts = []
        for sp_href in back_spine:
            try:
                data = z.read(sp_href)
                md, _ = xhtml_to_markdown(data)
                if md.strip():
                    bm_parts.append(md)
                    back_sections.append(sp_href.split("/")[-1])
            except KeyError:
                continue
        back_md = "\n\n".join(bm_parts)

    back_matter = {
        "has_content": bool(back_md.strip()),
        "sections": back_sections,
        "content_markdown": back_md,
    }

    return chapters, front_matter, back_matter


def _save_chapter_images(
    image_refs: list[dict],
    z: zipfile.ZipFile,
    opf_dir: str,
    image_dir: str | None,
    chapter_num: int,
) -> list[dict]:
    """Save referenced images from ZIP to image_dir."""
    if not image_dir or not image_refs:
        return []

    saved = []
    for i, ref in enumerate(image_refs):
        src = ref.get("src", "")
        if not src:
            continue

        # Resolve src relative to EPUB content
        zip_path = _resolve_href(src, opf_dir)
        try:
            img_data = z.read(zip_path)
        except KeyError:
            # Try alternate paths
            try:
                img_data = z.read(src)
            except KeyError:
                continue

        if len(img_data) < MIN_IMAGE_BYTES:
            continue

        ext = os.path.splitext(src)[1] or ".png"
        filename = f"ch{chapter_num}_img{i + 1}{ext}"
        save_path = os.path.join(image_dir, filename)
        with open(save_path, "wb") as f:
            f.write(img_data)

        saved.append({
            "filename": filename,
            "saved_path": save_path,
            "source": src,
            "size_bytes": len(img_data),
            "chapter": chapter_num,
            "alt": ref.get("alt", ""),
        })

    return saved


# ---------------------------------------------------------------------------
# Core EPUB extraction
# ---------------------------------------------------------------------------

def extract_epub(filepath: str, extract_images: bool = False, image_dir: str | None = None) -> dict:
    """Extract chapters and metadata from an EPUB file using stdlib only.

    Args:
        filepath: Path to the .epub file.
        extract_images: Whether to extract inline images.
        image_dir: Directory to save extracted images.

    Returns:
        Dict with chapters, metadata, TOC, front/back matter.
    """
    filepath = str(filepath)

    try:
        z = zipfile.ZipFile(filepath, "r")
    except (zipfile.BadZipFile, FileNotFoundError, OSError) as e:
        return {"error": f"Cannot open EPUB: {e}"}

    # --- Step 1: Find content.opf via container.xml ---
    try:
        container_data = z.read("META-INF/container.xml")
    except KeyError:
        z.close()
        return {"error": "Not a valid EPUB: missing META-INF/container.xml"}

    try:
        container_root = ET.fromstring(container_data)
    except ET.ParseError as e:
        z.close()
        return {"error": f"Cannot parse container.xml: {e}"}

    # Find rootfile — handle namespace variations
    opf_path = None
    for elem in container_root.iter():
        tag = elem.tag.split("}", 1)[-1] if "}" in elem.tag else elem.tag
        if tag == "rootfile":
            opf_path = elem.get("full-path")
            if opf_path:
                break

    if not opf_path:
        z.close()
        return {"error": "Cannot find content.opf path in container.xml"}

    opf_dir = os.path.dirname(opf_path).replace("\\", "/")

    # --- Step 2: Parse content.opf ---
    try:
        opf_data = z.read(opf_path)
    except KeyError:
        z.close()
        return {"error": f"content.opf not found in ZIP: {opf_path}"}

    try:
        opf_root = ET.fromstring(opf_data)
    except ET.ParseError as e:
        z.close()
        return {"error": f"Cannot parse content.opf: {e}"}

    # --- Step 3: Extract metadata from OPF ---
    metadata = _parse_opf_metadata(opf_root)

    # --- Step 4: Build manifest (id → href) ---
    manifest = {}
    manifest_by_href = {}
    ncx_path = None
    nav_path = None

    for elem in opf_root.iter():
        tag = elem.tag.split("}", 1)[-1] if "}" in elem.tag else elem.tag
        if tag == "item":
            item_id = elem.get("id", "")
            item_href = elem.get("href", "")
            item_media = elem.get("media-type", "")
            item_props = elem.get("properties", "")

            if item_id and item_href:
                resolved = _resolve_href(item_href, opf_dir)
                manifest[item_id] = {
                    "href": resolved,
                    "href_raw": item_href,
                    "media_type": item_media,
                    "properties": item_props,
                }
                manifest_by_href[resolved] = item_id

            # Detect NCX (EPUB 2 TOC)
            if item_media == "application/x-dtbncx+xml":
                ncx_path = _resolve_href(item_href, opf_dir)

            # Detect nav document (EPUB 3 TOC)
            if "nav" in item_props:
                nav_path = _resolve_href(item_href, opf_dir)

    # --- Step 5: Build spine (reading order) ---
    spine_hrefs = []
    spine_toc_id = None
    for elem in opf_root.iter():
        tag = elem.tag.split("}", 1)[-1] if "}" in elem.tag else elem.tag
        if tag == "spine":
            spine_toc_id = elem.get("toc")
        if tag == "itemref":
            idref = elem.get("idref", "")
            if idref in manifest:
                spine_hrefs.append(manifest[idref]["href"])

    # If spine toc attribute points to NCX and we haven't found it yet
    if not ncx_path and spine_toc_id and spine_toc_id in manifest:
        ncx_path = manifest[spine_toc_id]["href"]

    # --- Step 6: Parse TOC ---
    toc_entries = []
    if nav_path:
        toc_entries = parse_toc_nav(z, nav_path, opf_dir)
    if not toc_entries and ncx_path:
        toc_entries = parse_toc_ncx(z, ncx_path, opf_dir)

    # --- Step 7: Map spine to chapters ---
    chapters, front_matter, back_matter = map_spine_to_chapters(
        spine_hrefs, toc_entries, z, opf_dir,
        extract_images=extract_images, image_dir=image_dir,
    )

    # --- Step 7.5: T7 — refine word_counts to drop publisher header bleed ---
    # Chapter dicts mutated in-place (chapter["word_count"] is part of the existing
    # schema). Front/back cleaned counts kept as locals — adding new keys to those
    # dicts would leak underscore-prefixed internals into the stdout JSON output.
    book_title = metadata.get("title", "")
    book_author = metadata.get("author", "")
    for ch in chapters:
        ch["word_count"] = _clean_word_count(
            ch["content_markdown"], title=book_title, author=book_author,
        )
    front_clean = _clean_word_count(
        front_matter["content_markdown"], title=book_title, author=book_author,
    ) if front_matter["has_content"] else 0
    back_clean = _clean_word_count(
        back_matter["content_markdown"], title=book_title, author=book_author,
    ) if back_matter["has_content"] else 0

    # --- Step 8: Extract cover image ---
    cover_image_path = None
    if extract_images and image_dir:
        cover_image_path = _extract_cover_image(z, opf_root, manifest, opf_dir, image_dir)

    # --- Step 9: Compute totals (using cleaned per-section counts from Step 7.5) ---
    total_word_count = sum(ch["word_count"] for ch in chapters) + front_clean + back_clean

    # Collect all URLs from chapter content
    all_text = "\n".join(ch["content_markdown"] for ch in chapters)
    extracted_url_list = extract_urls(all_text)
    extracted_urls = [{"url": u, "context": "found in book text", "source": "content"} for u in extracted_url_list]

    # Build TOC output
    toc_output = [{"level": e.get("level", 1), "title": e["title"], "href": e.get("href_raw", "")} for e in toc_entries]

    file_size = os.path.getsize(filepath)

    z.close()

    return {
        "source_type": "book",
        "title": metadata.get("title", "") or Path(filepath).stem,
        "author": metadata.get("author", ""),
        "isbn": metadata.get("isbn", ""),
        "asin": metadata.get("asin", ""),
        "publisher": metadata.get("publisher", ""),
        "publication_date": metadata.get("date", ""),
        "amazon_url": f"https://amazon.com/dp/{metadata['asin']}" if metadata.get("asin") else "",
        "calibre_path": "",
        "calibre_id": metadata.get("calibre_id", ""),
        "description": metadata.get("description", ""),
        "subjects": metadata.get("subjects", []),
        "language": metadata.get("language", ""),
        "format": "epub",
        "original_format": None,
        "chapter_count": len(chapters),
        "total_word_count": total_word_count,
        "chapters": chapters,
        "front_matter": front_matter,
        "back_matter": back_matter,
        "toc": toc_output,
        "cover_image_path": cover_image_path,
        "extracted_urls": extracted_urls,
        "metadata": {
            "fetch_date": datetime.now(tz=timezone.utc).strftime("%Y-%m-%d"),
            "extraction_method": "epub_zip",
            "conversion_from": None,
            "file_size_bytes": file_size,
        },
    }


def _parse_opf_metadata(opf_root) -> dict:
    """Extract metadata from OPF root element."""
    meta = {
        "title": "",
        "author": "",
        "publisher": "",
        "date": "",
        "description": "",
        "language": "",
        "isbn": "",
        "asin": "",
        "calibre_id": "",
        "subjects": [],
    }

    # T4 (iter-2): collect creators into an ordered list; dedup by full
    # normalized string. Joined into meta["author"] AFTER the loop so
    # "Last, First" form names (which contain a comma) are not re-split.
    # Example: ["Smith, John", "Doe, Jane"] -> "Smith, John, Doe, Jane".
    _creators: list[str] = []

    for elem in opf_root.iter():
        tag = elem.tag.split("}", 1)[-1] if "}" in elem.tag else elem.tag
        text = (elem.text or "").strip()

        if tag == "title" and text and not meta["title"]:
            meta["title"] = text
        elif tag == "creator" and text:
            # T4: collect into _creators; dedup by full normalized string.
            # Backward compat: meta["author"] (set after the loop) remains a
            # single comma-joined string for downstream consumers, now reflecting
            # all creators (e.g., "Author One, Author Two") instead of dropping
            # the 2nd+ creator like the original `not meta["author"]` guard did.
            normalized = text.strip()
            if normalized and normalized not in _creators:
                _creators.append(normalized)
        elif tag == "publisher" and text:
            meta["publisher"] = text
        elif tag == "date" and text and not meta["date"]:
            meta["date"] = text
        elif tag == "description" and text:
            # Strip HTML tags from description
            meta["description"] = re.sub(r"<[^>]+>", "", text).strip()
        elif tag == "language" and text:
            meta["language"] = text
        elif tag == "subject" and text:
            meta["subjects"].append(text)
        elif tag == "identifier" and text:
            # Check scheme attribute or opf:scheme
            scheme = (elem.get("scheme") or elem.get(f"{{{NS_OPF}}}scheme") or "").lower()
            id_attr = (elem.get("id") or "").lower()

            text_lower = text.lower()
            if scheme == "isbn" or "isbn" in id_attr:
                # Extract digits only for ISBN
                isbn_digits = re.sub(r"[^\dXx]", "", text)
                if len(isbn_digits) in (10, 13):
                    meta["isbn"] = isbn_digits
            elif scheme == "mobi-asin" or scheme == "amazon" or "asin" in id_attr:
                meta["asin"] = text
            elif scheme == "calibre" or "calibre" in id_attr:
                meta["calibre_id"] = text
            elif text_lower.startswith("urn:isbn:"):
                isbn_digits = re.sub(r"[^\dXx]", "", text[9:])
                if len(isbn_digits) in (10, 13):
                    meta["isbn"] = isbn_digits
        elif tag == "meta":
            name = elem.get("name", "")
            content = elem.get("content", "")
            if name == "calibre:title_sort" and not meta["title"]:
                meta["title"] = content
            elif name == "calibre:author_link_map" and not _creators and not meta["author"]:
                # Fallback when no <dc:creator> tags present AND no creators
                # collected: parse calibre:author_link_map for the first author.
                # T4 (iter-2): also gated on `not _creators` so creators-driven
                # path always wins when both are present.
                try:
                    author_map = json.loads(content)
                    if author_map:
                        meta["author"] = list(author_map.keys())[0]
                except (json.JSONDecodeError, IndexError):
                    pass

    # T4 (iter-2): join collected creators into meta["author"] after the loop.
    # Overrides any meta-tag fallback set above (creators-driven path wins).
    if _creators:
        meta["author"] = ", ".join(_creators)

    return meta


def _extract_cover_image(
    z: zipfile.ZipFile,
    opf_root,
    manifest: dict,
    opf_dir: str,
    image_dir: str,
) -> str | None:
    """Try to extract cover image from EPUB."""
    cover_id = None

    # Method 1: <meta name="cover" content="cover-image-id">
    for elem in opf_root.iter():
        tag = elem.tag.split("}", 1)[-1] if "}" in elem.tag else elem.tag
        if tag == "meta" and elem.get("name") == "cover":
            cover_id = elem.get("content", "")
            break

    # Method 2: manifest item with properties="cover-image"
    if not cover_id:
        for item_id, item_info in manifest.items():
            if "cover-image" in item_info.get("properties", ""):
                cover_id = item_id
                break

    # Method 3: look for item with id containing "cover"
    if not cover_id:
        for item_id, item_info in manifest.items():
            if "cover" in item_id.lower() and item_info["media_type"].startswith("image/"):
                cover_id = item_id
                break

    if cover_id and cover_id in manifest:
        cover_href = manifest[cover_id]["href"]
        try:
            cover_data = z.read(cover_href)
            ext = os.path.splitext(cover_href)[1] or ".jpg"
            cover_path = os.path.join(image_dir, f"cover{ext}")
            with open(cover_path, "wb") as f:
                f.write(cover_data)
            return cover_path
        except (KeyError, OSError):
            pass

    return None


# ---------------------------------------------------------------------------
# Calibre ebook-convert (MOBI/AZW3 → EPUB)
# ---------------------------------------------------------------------------

def find_calibre_convert() -> str | None:
    """Find ebook-convert executable. Returns path or None."""
    import shutil

    # Check PATH first
    found = shutil.which("ebook-convert")
    if found:
        return found

    # Check common locations
    candidates = [
        r"C:\Program Files\Calibre2\ebook-convert.exe",
        r"C:\Program Files (x86)\Calibre2\ebook-convert.exe",
        "/usr/bin/ebook-convert",
        "/usr/local/bin/ebook-convert",
        "/opt/calibre/ebook-convert",
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path

    return None


def _calibre_convert_timeout() -> int:
    """Resolve Calibre conversion timeout in seconds.

    Default 120s; override via CALIBRE_CONVERT_TIMEOUT env var.
    Large books (1000+ pages) may need 300-600s. Returns 120 on parse failure.
    """
    raw = os.environ.get("CALIBRE_CONVERT_TIMEOUT", "").strip()
    if raw:
        try:
            val = int(raw)
            if val > 0:
                return val
        except ValueError:
            pass
    return 120


def convert_to_epub(filepath: str, temp_dir: str) -> str | None:
    """Convert MOBI/AZW3 to EPUB using Calibre's ebook-convert.

    Returns path to converted .epub, or None on failure.

    Disable Calibre's synthetic default cover, not an existing source cover.
    Conversion must not invent a JPEG/titlepage and expose it as source evidence.

    Timeout is 120s default; override via CALIBRE_CONVERT_TIMEOUT env var.
    On non-zero exit, timeout, or OSError: stderr is surfaced to sys.stderr
    (truncated to 500 chars) so callers can diagnose conversion failures
    instead of silently receiving None.
    """
    converter = find_calibre_convert()
    if not converter:
        return None

    output_path = os.path.join(temp_dir, "converted.epub")
    timeout = _calibre_convert_timeout()

    try:
        result = subprocess.run(
            [converter, str(filepath), output_path, "--no-default-epub-cover"],
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode == 0 and os.path.isfile(output_path):
            return output_path
        # T6 (iter-2): exit 0 but output file missing is a distinct failure mode
        # — Calibre wrote somewhere unexpected or failed silently. Don't conflate
        # with the non-zero-exit message.
        if result.returncode == 0:
            print(f"[extract_book] Calibre convert returned 0 but output file missing "
                  f"at {output_path}", file=sys.stderr)
            return None
        # T6: surface stderr on non-zero exit instead of silent None
        err_msg = (result.stderr or "").strip()
        if err_msg:
            print(f"[extract_book] Calibre convert returned {result.returncode}; "
                  f"stderr: {err_msg[:500]}", file=sys.stderr)
        else:
            print(f"[extract_book] Calibre convert returned {result.returncode}; "
                  f"no stderr output", file=sys.stderr)
    except subprocess.TimeoutExpired:
        print(f"[extract_book] Calibre convert timed out after {timeout}s. "
              f"Override via CALIBRE_CONVERT_TIMEOUT env var.", file=sys.stderr)
    except (FileNotFoundError, OSError) as e:
        print(f"[extract_book] Calibre convert failed: {e}", file=sys.stderr)

    return None


# ---------------------------------------------------------------------------
# Calibre metadata.opf parsing
# ---------------------------------------------------------------------------

def parse_calibre_metadata(book_dir_or_filepath: str) -> dict:
    """Parse Calibre's metadata.opf file for a book.

    Looks for metadata.opf in the same directory as the book file,
    or in the directory itself if a directory is passed.
    """
    path = Path(book_dir_or_filepath)
    if path.is_file():
        book_dir = path.parent
    else:
        book_dir = path

    metadata_opf = book_dir / "metadata.opf"
    if not metadata_opf.exists():
        return {}

    try:
        tree = ET.parse(str(metadata_opf))
        root = tree.getroot()
    except (ET.ParseError, OSError):
        return {}

    meta = {
        "title": "",
        "author": "",
        "publisher": "",
        "date": "",
        "description": "",
        "subjects": [],
        "isbn": "",
        "asin": "",
        "calibre_id": "",
        "google_id": "",
        "amazon_url": "",
        "calibre_path": str(book_dir),
    }

    # T4 (iter-2): collect creators into an ordered list; dedup by full
    # normalized string. Joined into meta["author"] AFTER the loop so
    # "Last, First" form names are not re-split. parse_calibre_metadata
    # has no meta-tag fallback so the join is unconditional.
    _creators: list[str] = []

    for elem in root.iter():
        tag = elem.tag.split("}", 1)[-1] if "}" in elem.tag else elem.tag
        text = (elem.text or "").strip()

        if tag == "title" and text and not meta["title"]:
            meta["title"] = text
        elif tag == "creator" and text:
            # T4: collect into _creators; dedup by full normalized string.
            # Backward compat: meta["author"] (set after the loop) remains a
            # single comma-joined string for downstream consumers, now reflecting
            # all creators (e.g., "Author One, Author Two") instead of dropping
            # the 2nd+ creator like the original `not meta["author"]` guard did.
            normalized = text.strip()
            if normalized and normalized not in _creators:
                _creators.append(normalized)
        elif tag == "publisher" and text:
            meta["publisher"] = text
        elif tag == "date" and text and not meta["date"]:
            meta["date"] = text
        elif tag == "description" and text:
            meta["description"] = re.sub(r"<[^>]+>", "", text).strip()
        elif tag == "subject" and text:
            meta["subjects"].append(text)
        elif tag == "identifier" and text:
            scheme = (elem.get("scheme") or elem.get(f"{{{NS_OPF}}}scheme") or "").lower()
            id_attr = (elem.get("id") or "").lower()

            if scheme == "isbn" or "isbn" in id_attr:
                isbn_digits = re.sub(r"[^\dXx]", "", text)
                if len(isbn_digits) in (10, 13):
                    meta["isbn"] = isbn_digits
            elif scheme == "mobi-asin" or scheme == "amazon" or "asin" in id_attr:
                meta["asin"] = text
            elif scheme == "calibre" or "calibre" in id_attr:
                meta["calibre_id"] = text
            elif scheme == "google" or "google" in id_attr:
                meta["google_id"] = text
            elif text.startswith("urn:isbn:"):
                isbn_digits = re.sub(r"[^\dXx]", "", text[9:])
                if len(isbn_digits) in (10, 13):
                    meta["isbn"] = isbn_digits

    # T4 (iter-2): join collected creators into meta["author"] after the loop.
    if _creators:
        meta["author"] = ", ".join(_creators)

    if meta["asin"]:
        meta["amazon_url"] = f"https://amazon.com/dp/{meta['asin']}"

    return meta


# ---------------------------------------------------------------------------
# Image-only PDF support
# ---------------------------------------------------------------------------

def detect_image_only_pdf(filepath: str) -> bool:
    """Detect if a PDF is image-only (scanned/no extractable text).

    Uses PyMuPDF to check first 10 pages for extractable text.
    Returns True if total text < 100 chars.
    """
    if _fitz is None:
        return False

    try:
        doc = _fitz.open(str(filepath))
        total_text = 0
        check_pages = min(len(doc), 10)

        for i in range(check_pages):
            page = doc[i]
            text = page.get_text("text") or ""
            total_text += len(text.strip())
            if total_text >= 100:
                doc.close()
                return False

        doc.close()
        return total_text < 100
    except Exception:
        return False


def extract_image_only_pdf(filepath: str, calibre_dir: str | None = None) -> dict:
    """Extract pages from an image-only PDF as images.

    Renders each page at 150 DPI and saves as PNG.
    """
    if _fitz is None:
        return {"error": "PyMuPDF (fitz) not available. Install: pip install pymupdf"}

    filepath = str(filepath)
    file_size = os.path.getsize(filepath)

    # Get metadata from Calibre if available
    cal_meta = {}
    if calibre_dir:
        cal_meta = parse_calibre_metadata(calibre_dir)
    if not cal_meta:
        cal_meta = parse_calibre_metadata(filepath)

    try:
        doc = _fitz.open(filepath)
    except Exception as e:
        return {"error": f"Cannot open PDF: {e}"}

    total_pages = len(doc)

    # Get PDF metadata
    pdf_meta = doc.metadata or {}
    title = cal_meta.get("title") or pdf_meta.get("title", "") or Path(filepath).stem
    author = cal_meta.get("author") or pdf_meta.get("author", "") or ""

    # Render pages to images
    img_dir = tempfile.mkdtemp(prefix="ash_wiki_book_pages_")
    page_images = []

    for i in range(total_pages):
        page = doc[i]
        pix = page.get_pixmap(dpi=150)
        img_path = os.path.join(img_dir, f"page_{i + 1:04d}.png")
        pix.save(img_path)
        page_images.append({
            "page": i + 1,
            "path": img_path,
            "size_bytes": os.path.getsize(img_path),
        })

    doc.close()

    return {
        "source_type": "image-only-book",
        "title": title,
        "author": author,
        "format": "pdf",
        "is_image_only": True,
        "total_pages": total_pages,
        "image_dir": img_dir,
        "page_images": page_images,
        "isbn": cal_meta.get("isbn", ""),
        "publisher": cal_meta.get("publisher", ""),
        "calibre_path": cal_meta.get("calibre_path", ""),
        "calibre_id": cal_meta.get("calibre_id", ""),
        "amazon_url": cal_meta.get("amazon_url", ""),
        "metadata": {
            "fetch_date": datetime.now(tz=timezone.utc).strftime("%Y-%m-%d"),
            "extraction_method": "pymupdf_page_render",
            "note": "Image-only PDF. Agent must use Read tool on each page_image for OCR.",
            "file_size_bytes": file_size,
        },
    }


# ---------------------------------------------------------------------------
# Library / Calibre modes
# ---------------------------------------------------------------------------

def list_library(calibre_root: str) -> list[dict]:
    """Walk a Calibre library directory and list all books with metadata.

    Looks for directories containing metadata.opf files.
    """
    root = Path(calibre_root)
    if not root.is_dir():
        return []

    books = []

    for dirpath, dirnames, filenames in os.walk(str(root)):
        if "metadata.opf" in filenames:
            meta = parse_calibre_metadata(dirpath)
            if not meta:
                continue

            # Find the book file
            book_file = None
            book_format = None
            for ext in (".epub", ".mobi", ".azw3", ".azw", ".pdf"):
                for fname in filenames:
                    if fname.lower().endswith(ext):
                        book_file = os.path.join(dirpath, fname)
                        book_format = ext[1:]  # strip dot
                        break
                if book_file:
                    break

            books.append({
                "title": meta.get("title", "Unknown"),
                "author": meta.get("author", "Unknown"),
                "format": book_format or "unknown",
                "isbn": meta.get("isbn", ""),
                "asin": meta.get("asin", ""),
                "path": book_file or dirpath,
                "calibre_id": meta.get("calibre_id", ""),
                "subjects": meta.get("subjects", []),
            })

    return books


def extract_from_calibre_dir(dir_path: str) -> dict:
    """Extract a book from a Calibre book directory.

    Finds metadata.opf and the book file, then extracts.
    """
    dirpath = Path(dir_path)
    if not dirpath.is_dir():
        return {"error": f"Not a directory: {dir_path}"}

    cal_meta = parse_calibre_metadata(dir_path)

    # Find book file
    book_file = None
    for ext in (".epub", ".mobi", ".azw3", ".azw", ".pdf"):
        for f in dirpath.iterdir():
            if f.suffix.lower() == ext:
                book_file = f
                break
        if book_file:
            break

    if not book_file:
        return {"error": f"No book file found in {dir_path}"}

    return extract_book(str(book_file), calibre_dir=dir_path)


def list_chapters_only(filepath: str) -> dict:
    """Extract TOC from a book without full chapter text.

    Returns just the chapter listing.
    """
    filepath = str(filepath)
    ext = Path(filepath).suffix.lower()

    if ext != ".epub":
        # For non-EPUB, try to convert first
        if ext in (".mobi", ".azw3", ".azw"):
            temp_dir = tempfile.mkdtemp(prefix="ash_wiki_convert_")
            epub_path = convert_to_epub(filepath, temp_dir)
            if not epub_path:
                return {"error": f"Cannot convert {ext} to EPUB. Install Calibre (ebook-convert)."}
            filepath = epub_path
        else:
            return {"error": f"Chapter listing not supported for {ext}"}

    try:
        z = zipfile.ZipFile(filepath, "r")
    except (zipfile.BadZipFile, FileNotFoundError, OSError) as e:
        return {"error": f"Cannot open EPUB: {e}"}

    # Find content.opf
    try:
        container_data = z.read("META-INF/container.xml")
        container_root = ET.fromstring(container_data)
    except (KeyError, ET.ParseError) as e:
        z.close()
        return {"error": f"Invalid EPUB: {e}"}

    opf_path = None
    for elem in container_root.iter():
        tag = elem.tag.split("}", 1)[-1] if "}" in elem.tag else elem.tag
        if tag == "rootfile":
            opf_path = elem.get("full-path")
            if opf_path:
                break

    if not opf_path:
        z.close()
        return {"error": "Cannot find content.opf"}

    opf_dir = os.path.dirname(opf_path).replace("\\", "/")

    try:
        opf_data = z.read(opf_path)
        opf_root = ET.fromstring(opf_data)
    except (KeyError, ET.ParseError) as e:
        z.close()
        return {"error": f"Cannot parse content.opf: {e}"}

    # Get metadata
    metadata = _parse_opf_metadata(opf_root)

    # Find TOC
    ncx_path = None
    nav_path = None
    for elem in opf_root.iter():
        tag = elem.tag.split("}", 1)[-1] if "}" in elem.tag else elem.tag
        if tag == "item":
            media_type = elem.get("media-type", "")
            props = elem.get("properties", "")
            href = elem.get("href", "")
            if media_type == "application/x-dtbncx+xml":
                ncx_path = _resolve_href(href, opf_dir)
            if "nav" in props:
                nav_path = _resolve_href(href, opf_dir)

    toc_entries = []
    if nav_path:
        toc_entries = parse_toc_nav(z, nav_path, opf_dir)
    if not toc_entries and ncx_path:
        toc_entries = parse_toc_ncx(z, ncx_path, opf_dir)

    z.close()

    return {
        "title": metadata.get("title", "") or Path(filepath).stem,
        "author": metadata.get("author", ""),
        "chapter_count": len(toc_entries),
        "chapters": [
            {"number": i + 1, "level": e.get("level", 1), "title": e["title"]}
            for i, e in enumerate(toc_entries)
        ],
    }


# ---------------------------------------------------------------------------
# Top-level extraction dispatcher
# ---------------------------------------------------------------------------

def extract_book(
    filepath: str,
    extract_images: bool = False,
    force_format: str | None = None,
    calibre_dir: str | None = None,
) -> dict:
    """Extract a book file (EPUB, MOBI, AZW3, PDF).

    Dispatches to format-specific extractors.
    """
    filepath = str(Path(filepath).resolve())

    if not os.path.isfile(filepath):
        return {"error": f"File not found: {filepath}"}

    ext = (force_format or Path(filepath).suffix.lower().lstrip(".")).lower()
    if not ext.startswith("."):
        ext = "." + ext

    original_format = ext.lstrip(".")

    # Get Calibre metadata if available
    cal_meta = {}
    if calibre_dir:
        cal_meta = parse_calibre_metadata(calibre_dir)
    if not cal_meta:
        cal_meta = parse_calibre_metadata(filepath)

    # Image extraction directory
    image_dir = None
    if extract_images:
        image_dir = tempfile.mkdtemp(prefix="ash_wiki_book_images_")

    if ext == ".epub":
        result = extract_epub(filepath, extract_images=extract_images, image_dir=image_dir)
        if "error" in result:
            return result
        # Merge Calibre metadata
        _merge_calibre_meta(result, cal_meta)
        return result

    elif ext in (".mobi", ".azw3", ".azw"):
        # Convert to EPUB first
        temp_dir = tempfile.mkdtemp(prefix="ash_wiki_convert_")
        epub_path = convert_to_epub(filepath, temp_dir)
        if not epub_path:
            return {"error": f"Cannot convert {ext} to EPUB. Install Calibre (ebook-convert)."}

        result = extract_epub(epub_path, extract_images=extract_images, image_dir=image_dir)
        if "error" in result:
            return result

        # Update format info
        result["original_format"] = original_format
        result["format"] = "epub"
        result["metadata"]["conversion_from"] = original_format
        result["metadata"]["file_size_bytes"] = os.path.getsize(filepath)

        # Merge Calibre metadata
        _merge_calibre_meta(result, cal_meta)
        return result

    elif ext == ".pdf":
        # Check if it's an image-only PDF
        if detect_image_only_pdf(filepath):
            return extract_image_only_pdf(filepath, calibre_dir=calibre_dir)
        else:
            return {"error": "PDF has extractable text. Use extract_document.py instead. "
                    "Only image-only PDFs (scanned books) are handled here."}

    else:
        return {"error": f"Unsupported format: {ext}. Supported: epub, mobi, azw3, pdf (image-only)"}


def _merge_calibre_meta(result: dict, cal_meta: dict):
    """Merge Calibre metadata into extraction result."""
    if not cal_meta:
        return

    if not result.get("isbn") and cal_meta.get("isbn"):
        result["isbn"] = cal_meta["isbn"]
    if not result.get("asin") and cal_meta.get("asin"):
        result["asin"] = cal_meta["asin"]
    if not result.get("amazon_url") and cal_meta.get("amazon_url"):
        result["amazon_url"] = cal_meta["amazon_url"]
    if not result.get("calibre_id") and cal_meta.get("calibre_id"):
        result["calibre_id"] = cal_meta["calibre_id"]
    if cal_meta.get("calibre_path"):
        result["calibre_path"] = cal_meta["calibre_path"]
    if not result.get("publisher") and cal_meta.get("publisher"):
        result["publisher"] = cal_meta["publisher"]
    if not result.get("description") and cal_meta.get("description"):
        result["description"] = cal_meta["description"]
    if not result.get("subjects") and cal_meta.get("subjects"):
        result["subjects"] = cal_meta["subjects"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Extract chapters from books (EPUB, MOBI, AZW3, PDF)"
    )
    parser.add_argument("filepath", nargs="?", help="Path to book file")
    parser.add_argument("--extract-images", action="store_true",
                        help="Extract images inline")
    parser.add_argument("--format", choices=["epub", "mobi", "azw3", "pdf"],
                        help="Force format detection")
    parser.add_argument("--calibre-dir",
                        help="Calibre book directory (with metadata.opf)")
    parser.add_argument("--list-chapters",
                        help="List chapters without full extraction")
    parser.add_argument("--list-library",
                        help="List all books in a Calibre library root")

    args = parser.parse_args()

    # Mode: list library
    if args.list_library:
        books = list_library(args.list_library)
        json.dump(books, sys.stdout, indent=2, ensure_ascii=False)
        return

    # Mode: list chapters
    if args.list_chapters:
        result = list_chapters_only(args.list_chapters)
        if "error" in result:
            json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
            sys.exit(1)
        json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
        return

    # Mode: extract from Calibre directory
    if args.calibre_dir and not args.filepath:
        result = extract_from_calibre_dir(args.calibre_dir)
        if "error" in result:
            json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
            sys.exit(1)
        json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
        return

    # Mode: extract book file
    if not args.filepath:
        parser.error("filepath is required (unless --list-library or --calibre-dir is used)")

    result = extract_book(
        args.filepath,
        extract_images=args.extract_images,
        force_format=args.format,
        calibre_dir=args.calibre_dir,
    )

    if "error" in result:
        json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
        sys.exit(1)

    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
