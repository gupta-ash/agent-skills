#!/usr/bin/env python
"""Extract text content from local document files (PDF, DOCX, HTML, TXT, MD).

Usage:
    python extract_document.py <filepath>
    python extract_document.py --extract-images <filepath>

When --extract-images is used, images are saved to a temp directory and inline
[IMAGE: filename] placeholders are inserted in the text at their original positions.
This preserves the contextual relationship between text and images.

Outputs structured JSON to stdout.
"""

import argparse
from contextlib import redirect_stdout
import email
import email.policy
import io
import json
import os
import re
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

# Force UTF-8 stdout on Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


URL_REGEX = re.compile(r"https?://[^\s\)\]>\"'`]+")

# Image extraction thresholds — skip decorative/tiny images
MIN_IMAGE_BYTES = 5000       # 5KB minimum
MIN_IMAGE_DIMENSION = 50     # 50px minimum width or height

# Image formats to extract
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".tif", ".webp"}

# Supported formats
SUPPORTED_FORMATS = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".doc": "doc",
    ".html": "html",
    ".htm": "html",
    ".mhtml": "mhtml",
    ".mht": "mhtml",
    ".txt": "txt",
    ".md": "markdown",
    ".markdown": "markdown",
    ".rst": "rst",
    ".rtf": "rtf",
}


def extract_urls(text: str) -> list[str]:
    """Extract all URLs from text, deduplicated."""
    if not text:
        return []
    urls = URL_REGEX.findall(text)
    cleaned = [u.rstrip(".,;:!?)") for u in urls]
    return list(dict.fromkeys(cleaned))


class SimpleHTMLToText(HTMLParser):
    """Minimal HTML to text/markdown converter for local HTML files."""

    STRIP_TAGS = {"script", "style", "nav", "header", "footer", "aside", "noscript", "iframe", "svg"}

    def __init__(self, extract_images: bool = False):
        super().__init__()
        self._output = []
        self._skip_depth = 0
        self._in_pre = False
        self._extract_images = extract_images
        self.image_refs = []  # Collected image references

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in self.STRIP_TAGS:
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
        elif tag == "img" and self._extract_images:
            attrs_dict = dict(attrs)
            src = attrs_dict.get("src", "")
            alt = attrs_dict.get("alt", "")
            if src:
                self.image_refs.append({"src": src, "alt": alt})
                self._output.append(f"\n[IMAGE: {src} | {alt}]\n")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in self.STRIP_TAGS:
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


def extract_pdf(filepath: Path, extract_images: bool = False, image_dir: str | None = None) -> dict:
    """Extract text from PDF file.

    When extract_images=True, images are saved to image_dir and [IMAGE: ...]
    placeholders are inserted inline at their original positions in the text.
    """
    method = None
    text = ""
    page_count = 0
    title = ""
    author = ""
    date = ""
    extracted_images = []

    # Try PyMuPDF (fitz) — preferred: fast, rich metadata, image & table extraction
    try:
        import fitz  # pymupdf
        doc = fitz.open(str(filepath))
        page_count = len(doc)
        pages = []
        for page_num, page in enumerate(doc):
            if extract_images and image_dir:
                # Use dict mode for positional text+image interleaving
                page_dict = page.get_text("dict")
                blocks = page_dict.get("blocks", [])
                # Sort by vertical position for reading order
                blocks.sort(key=lambda b: (b["bbox"][1], b["bbox"][0]))

                page_parts = []
                text_so_far = []  # Track text for context_before

                for block in blocks:
                    if block["type"] == 0:  # Text block
                        block_text = ""
                        for line in block.get("lines", []):
                            line_text = ""
                            for span in line.get("spans", []):
                                line_text += span.get("text", "")
                            if line_text.strip():
                                block_text += line_text + "\n"
                        if block_text.strip():
                            page_parts.append(block_text.strip())
                            text_so_far.append(block_text.strip())

                    elif block["type"] == 1:  # Image block
                        # Get the image xref from the block
                        xref = block.get("xref", 0)
                        if not xref:
                            # Fallback: try to get image from page image list
                            continue
                        try:
                            img_data = doc.extract_image(xref)
                            if not img_data or not img_data.get("image"):
                                continue

                            img_bytes = img_data["image"]
                            img_width = img_data.get("width", 0)
                            img_height = img_data.get("height", 0)
                            img_ext = img_data.get("ext", "png")

                            # Skip tiny/decorative images
                            if (len(img_bytes) < MIN_IMAGE_BYTES or
                                    img_width < MIN_IMAGE_DIMENSION or
                                    img_height < MIN_IMAGE_DIMENSION):
                                continue

                            # Save image
                            img_count = len(extracted_images) + 1
                            img_filename = f"page{page_num + 1}_img{img_count}.{img_ext}"
                            img_path = os.path.join(image_dir, img_filename)
                            with open(img_path, "wb") as f:
                                f.write(img_bytes)

                            # Get surrounding text context
                            context_before = " ".join(text_so_far)[-200:] if text_so_far else ""

                            # Insert inline placeholder
                            placeholder = f"\n[IMAGE: {img_filename} | page {page_num + 1}, {img_width}x{img_height}px]\n"
                            page_parts.append(placeholder)

                            extracted_images.append({
                                "page": page_num + 1,
                                "xref": xref,
                                "format": img_ext,
                                "size_bytes": len(img_bytes),
                                "width": img_width,
                                "height": img_height,
                                "saved_path": img_path,
                                "filename": img_filename,
                                "context_before": context_before[-100:] if context_before else "",
                                "position_y": block["bbox"][1],
                            })
                        except Exception:
                            pass

                pages.append("\n".join(page_parts))

                # Also extract images not in the dict blocks (some PDFs)
                try:
                    dict_xrefs = {img.get("xref", 0) for img in extracted_images if img.get("page") == page_num + 1}
                    for img_info in page.get_images(full=True):
                        xref = img_info[0]
                        if xref in dict_xrefs:
                            continue  # Already extracted
                        try:
                            img_data = doc.extract_image(xref)
                            if not img_data or not img_data.get("image"):
                                continue
                            img_bytes = img_data["image"]
                            img_width = img_data.get("width", 0)
                            img_height = img_data.get("height", 0)
                            img_ext = img_data.get("ext", "png")
                            if (len(img_bytes) < MIN_IMAGE_BYTES or
                                    img_width < MIN_IMAGE_DIMENSION or
                                    img_height < MIN_IMAGE_DIMENSION):
                                continue
                            img_count = len(extracted_images) + 1
                            img_filename = f"page{page_num + 1}_img{img_count}.{img_ext}"
                            img_path = os.path.join(image_dir, img_filename)
                            with open(img_path, "wb") as f:
                                f.write(img_bytes)
                            # Append at end of page since position unknown
                            pages.append(f"\n[IMAGE: {img_filename} | page {page_num + 1}, {img_width}x{img_height}px]\n")
                            extracted_images.append({
                                "page": page_num + 1,
                                "xref": xref,
                                "format": img_ext,
                                "size_bytes": len(img_bytes),
                                "width": img_width,
                                "height": img_height,
                                "saved_path": img_path,
                                "filename": img_filename,
                                "context_before": "",
                                "position_y": 9999,
                            })
                        except Exception:
                            pass
                except Exception:
                    pass
            else:
                # Original text-only extraction
                page_text = page.get_text("text") or ""
                pages.append(page_text)

                # Extract image metadata only (backward compat)
                try:
                    for img_info in page.get_images(full=True):
                        xref = img_info[0]
                        try:
                            img_data = doc.extract_image(xref)
                            if img_data:
                                extracted_images.append({
                                    "page": page_num + 1,
                                    "xref": xref,
                                    "format": img_data.get("ext", "unknown"),
                                    "size_bytes": len(img_data.get("image", b"")),
                                    "width": img_data.get("width", 0),
                                    "height": img_data.get("height", 0),
                                })
                        except Exception:
                            pass  # Skip unextractable images
                except Exception:
                    pass

            # Try table extraction (PyMuPDF 1.23+)
            try:
                if hasattr(page, "find_tables"):
                    with redirect_stdout(sys.stderr):
                        tables = page.find_tables()
                    for table in tables:
                        table_data = table.extract()
                        if table_data and len(table_data) > 0:
                            # Convert to markdown table
                            header_row = table_data[0]
                            header_cells = [str(c) if c is not None else "" for c in header_row]
                            md_header = "| " + " | ".join(header_cells) + " |"
                            md_sep = "| " + " | ".join(["---"] * len(header_cells)) + " |"
                            md_rows = []
                            for row in table_data[1:]:
                                cells = [str(c) if c is not None else "" for c in row]
                                md_rows.append("| " + " | ".join(cells) + " |")
                            md_table = f"\n{md_header}\n{md_sep}\n" + "\n".join(md_rows) + "\n"
                            pages.append(md_table)
            except Exception:
                pass

        text = "\n\n".join(pages)
        meta = doc.metadata or {}
        title = meta.get("title", "") or ""
        author = meta.get("author", "") or ""
        date = meta.get("creationDate", "") or ""
        doc.close()
        method = "pymupdf"
    except ImportError:
        pass
    except Exception as e:
        # Don't return error — fall through to next extractor
        pass

    # Try pdfplumber
    if not method:
        try:
            import pdfplumber
            with pdfplumber.open(filepath) as pdf:
                page_count = len(pdf.pages)
                pages = []
                for page in pdf.pages:
                    page_text = page.extract_text() or ""
                    pages.append(page_text)
                text = "\n\n".join(pages)
                meta = pdf.metadata or {}
                title = meta.get("Title", "") or ""
                author = meta.get("Author", "") or ""
                date = meta.get("CreationDate", "") or ""
                method = "pdfplumber"
        except ImportError:
            pass
        except Exception as e:
            return {"error": f"pdfplumber error: {str(e)}"}

    # Try pypdf
    if not method:
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(filepath))
            page_count = len(reader.pages)
            pages = []
            for page in reader.pages:
                page_text = page.extract_text() or ""
                pages.append(page_text)
            text = "\n\n".join(pages)
            meta = reader.metadata or {}
            title = (meta.get("/Title") or meta.get("Title") or "") if meta else ""
            author = (meta.get("/Author") or meta.get("Author") or "") if meta else ""
            date = (meta.get("/CreationDate") or meta.get("CreationDate") or "") if meta else ""
            method = "pypdf"
        except ImportError:
            pass
        except Exception as e:
            return {"error": f"pypdf error: {str(e)}"}

    # Try PyPDF2 (older)
    if not method:
        try:
            import PyPDF2
            reader = PyPDF2.PdfReader(str(filepath))
            page_count = len(reader.pages)
            pages = []
            for page in reader.pages:
                page_text = page.extract_text() or ""
                pages.append(page_text)
            text = "\n\n".join(pages)
            meta = reader.metadata or {}
            title = (meta.get("/Title", "") or "") if meta else ""
            author = (meta.get("/Author", "") or "") if meta else ""
            date = (meta.get("/CreationDate", "") or "") if meta else ""
            method = "PyPDF2"
        except ImportError:
            pass
        except Exception as e:
            return {"error": f"PyPDF2 error: {str(e)}"}

    if not method:
        return {"error": "No PDF library available. Install one of: pymupdf, pdfplumber, pypdf, PyPDF2"}

    return {
        "text": text,
        "page_count": page_count,
        "title": title,
        "author": author,
        "date": date,
        "method": method,
        "extracted_images": extracted_images,
    }


def extract_docx(filepath: Path, extract_images: bool = False, image_dir: str | None = None) -> dict:
    """Extract text from DOCX file.

    When extract_images=True, images are saved inline with [IMAGE: ...] placeholders.
    """
    method = None
    text = ""
    title = ""
    author = ""
    date = ""
    extracted_images = []
    image_map = {}  # rId -> saved_path for inline image resolution

    # Pre-extract images from ZIP if extracting images
    if extract_images and image_dir:
        try:
            with zipfile.ZipFile(str(filepath), "r") as z:
                # Build relationship map: rId -> media filename
                rel_map = {}
                if "word/_rels/document.xml.rels" in z.namelist():
                    with z.open("word/_rels/document.xml.rels") as f:
                        rels_tree = ET.parse(f)
                    rels_root = rels_tree.getroot()
                    for rel in rels_root:
                        rid = rel.get("Id", "")
                        target = rel.get("Target", "")
                        if target.startswith("media/"):
                            rel_map[rid] = target  # e.g., "media/image1.png"

                # Save all media images
                for name in z.namelist():
                    if name.startswith("word/media/") and any(
                        name.lower().endswith(ext) for ext in IMAGE_EXTENSIONS
                    ):
                        data = z.read(name)
                        if len(data) < MIN_IMAGE_BYTES:
                            continue
                        img_filename = Path(name).name
                        img_path = os.path.join(image_dir, img_filename)
                        with open(img_path, "wb") as f:
                            f.write(data)
                        # Map media path -> saved path
                        media_rel = name.replace("word/", "")  # "media/image1.png"
                        image_map[media_rel] = {
                            "saved_path": img_path,
                            "filename": img_filename,
                            "format": Path(name).suffix[1:].lower(),
                            "size_bytes": len(data),
                        }
                        # Also map rIds
                        for rid, target in rel_map.items():
                            if target == media_rel:
                                image_map[rid] = image_map[media_rel]
        except Exception:
            pass

    # Try python-docx
    try:
        import docx
        doc = docx.Document(str(filepath))
        paragraphs = []

        # OOXML namespaces for image detection
        nsmap = {
            "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
            "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
            "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
        }

        for para in doc.paragraphs:
            para_text = ""
            if para.style and para.style.name and para.style.name.startswith("Heading"):
                try:
                    level = int(para.style.name[-1])
                except (ValueError, IndexError):
                    level = 1
                para_text = "#" * level + " " + para.text
            else:
                para_text = para.text

            # Check for inline images in this paragraph
            if extract_images and image_dir:
                blips = para._element.findall(".//a:blip", nsmap)
                for blip in blips:
                    embed_id = blip.get(f"{{{nsmap['r']}}}embed", "")
                    if embed_id and embed_id in image_map:
                        img_info = image_map[embed_id]
                        placeholder = f"\n[IMAGE: {img_info['filename']} | DOCX inline]\n"
                        para_text += placeholder

                        if not any(e.get("filename") == img_info["filename"] for e in extracted_images):
                            extracted_images.append({
                                "source": f"word/media/{img_info['filename']}",
                                "format": img_info["format"],
                                "size_bytes": img_info["size_bytes"],
                                "saved_path": img_info["saved_path"],
                                "filename": img_info["filename"],
                                "context_before": para.text[:100] if para.text else "",
                            })

            paragraphs.append(para_text)

        # Tables
        for i, table in enumerate(doc.tables):
            rows = []
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                rows.append(cells)
            if rows:
                header = "| " + " | ".join(rows[0]) + " |"
                separator = "| " + " | ".join(["---"] * len(rows[0])) + " |"
                body = "\n".join("| " + " | ".join(row) + " |" for row in rows[1:])
                paragraphs.append(f"\n{header}\n{separator}\n{body}\n")

        text = "\n\n".join(paragraphs)

        props = doc.core_properties
        title = props.title or ""
        author = props.author or ""
        date = str(props.created) if props.created else ""
        method = "python-docx"
    except ImportError:
        pass
    except Exception as e:
        return {"error": f"python-docx error: {str(e)}"}

    # Fallback: parse DOCX as zip of XML
    if not method:
        try:
            with zipfile.ZipFile(str(filepath), "r") as z:
                # Read document.xml
                if "word/document.xml" in z.namelist():
                    with z.open("word/document.xml") as f:
                        tree = ET.parse(f)
                    root = tree.getroot()

                    # OOXML namespace
                    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
                    paragraphs = []
                    for p in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
                        texts = []
                        for t in p.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"):
                            if t.text:
                                texts.append(t.text)
                        if texts:
                            paragraphs.append("".join(texts))
                    text = "\n\n".join(paragraphs)
                    method = "zipfile-xml"

                # Try to get core properties
                if "docProps/core.xml" in z.namelist():
                    with z.open("docProps/core.xml") as f:
                        props_tree = ET.parse(f)
                    props_root = props_tree.getroot()
                    dc_ns = "http://purl.org/dc/elements/1.1/"
                    dcterms_ns = "http://purl.org/dc/terms/"
                    cp_ns = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"

                    title_el = props_root.find(f"{{{dc_ns}}}title")
                    if title_el is not None and title_el.text:
                        title = title_el.text
                    creator_el = props_root.find(f"{{{dc_ns}}}creator")
                    if creator_el is not None and creator_el.text:
                        author = creator_el.text
                    created_el = props_root.find(f"{{{dcterms_ns}}}created")
                    if created_el is not None and created_el.text:
                        date = created_el.text

        except (zipfile.BadZipFile, ET.ParseError) as e:
            return {"error": f"DOCX zip/XML parse error: {str(e)}"}
        except Exception as e:
            return {"error": f"DOCX extraction error: {str(e)}"}

    if not method:
        return {"error": "Could not extract DOCX content. Install python-docx or check file format."}

    # Extract images from DOCX ZIP (backward compat when not using --extract-images)
    if not extract_images:
        try:
            with zipfile.ZipFile(str(filepath), "r") as z:
                for name in z.namelist():
                    if name.startswith("word/media/") and any(
                        name.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".gif", ".bmp")
                    ):
                        data = z.read(name)
                        extracted_images.append({
                            "source": name,
                            "format": Path(name).suffix[1:],
                            "size_bytes": len(data),
                        })
        except Exception:
            pass  # Image extraction is best-effort

    return {
        "text": text,
        "page_count": 0,  # Not reliably available from docx
        "title": title,
        "author": author,
        "date": date,
        "method": method,
        "extracted_images": extracted_images,
    }


class _WordHTMLDetector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.word = False

    def handle_starttag(self, tag, attrs):
        if self.word:
            return
        fields = dict(attrs)
        if (fields.get("xmlns:w") == "urn:schemas-microsoft-com:office:word"
                or fields.get("xmlns:o") == "urn:schemas-microsoft-com:office:office"):
            self.word = True
        elif tag == "meta":
            name = (fields.get("name") or "").casefold()
            value = (fields.get("content") or "").casefold()
            self.word = ((name == "generator" and value.startswith("microsoft word"))
                         or (name == "progid" and value == "word.document"))


def is_word_html(content: str) -> bool:
    """Detect if HTML content was generated by Microsoft Word."""
    detector = _WordHTMLDetector()
    detector.feed(content)
    return detector.word


def clean_word_html(content: str) -> tuple[str, dict]:
    """Clean Word-generated HTML and extract metadata.

    Returns (cleaned_html, metadata_dict).
    """
    metadata = {}

    # Extract metadata from <o:DocumentProperties>
    props_match = re.search(
        r'<o:DocumentProperties>(.*?)</o:DocumentProperties>',
        content, re.DOTALL | re.IGNORECASE
    )
    if props_match:
        props_xml = props_match.group(1)
        for tag in ("Title", "Author", "Created", "LastSaved", "Company", "Version"):
            m = re.search(rf'<o:{tag}>(.*?)</o:{tag}>', props_xml, re.DOTALL | re.IGNORECASE)
            if m:
                metadata[tag.lower()] = m.group(1).strip()

    # Strip MSO conditional comments
    content = re.sub(r'<!--\[if.*?\]>.*?<!\[endif\]-->', '', content, flags=re.DOTALL)

    # Strip VML elements (<v:*> tags)
    content = re.sub(r'<v:[^>]*>.*?</v:[^>]*>', '', content, flags=re.DOTALL)
    content = re.sub(r'<v:[^>]*/>', '', content)

    # Strip MSO CSS properties
    content = re.sub(r'mso-[\w-]+:[^;]+;?', '', content)

    # Strip Office namespace XML tags
    content = re.sub(r'</?o:[^>]*>', '', content)
    content = re.sub(r'</?w:[^>]*>', '', content)

    return content, metadata


def extract_html(filepath: Path, extract_images: bool = False, image_dir: str | None = None) -> dict:
    """Extract text from HTML file."""
    # Try utf-8 first, then windows-1252 for Word HTML
    try:
        content = filepath.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return {"error": f"Read error: {str(e)}"}

    extracted_images = []
    word_metadata = {}

    # Check for Word HTML
    if is_word_html(content):
        # Try windows-1252 encoding if markers suggest it
        if 'charset=windows-1252' in content.lower() or 'charset="windows-1252"' in content.lower():
            try:
                content = filepath.read_bytes().decode("windows-1252", errors="replace")
            except Exception:
                pass

        content, word_metadata = clean_word_html(content)

        # Check for companion _files/ directory
        files_dir = filepath.parent / (filepath.stem + "_files")
        if files_dir.is_dir():
            try:
                for img_file in files_dir.iterdir():
                    if img_file.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".emf", ".wmf"):
                        img_info = {
                            "source": str(img_file),
                            "format": img_file.suffix[1:].lower(),
                            "size_bytes": img_file.stat().st_size,
                        }
                        if extract_images and img_file.stat().st_size >= MIN_IMAGE_BYTES:
                            img_info["saved_path"] = str(img_file)
                            img_info["filename"] = img_file.name
                        extracted_images.append(img_info)
            except Exception:
                pass

        method = "word_html"
    else:
        method = "html_parser"

    # Extract title
    title = word_metadata.get("title", "")
    if not title:
        m = re.search(r"<title[^>]*>(.*?)</title>", content, re.IGNORECASE | re.DOTALL)
        if m:
            title = re.sub(r"<[^>]+>", "", m.group(1)).strip()

    parser = SimpleHTMLToText(extract_images=extract_images)
    try:
        parser.feed(content)
    except Exception:
        pass

    text = parser.get_text()

    # Add image refs from parser if extracting images
    if extract_images and parser.image_refs:
        for ref in parser.image_refs:
            src = ref["src"]
            # Check if it's a local file path (companion _files/ or relative)
            src_path = filepath.parent / src
            if src_path.exists() and src_path.stat().st_size >= MIN_IMAGE_BYTES:
                extracted_images.append({
                    "source": str(src_path),
                    "format": src_path.suffix[1:].lower(),
                    "size_bytes": src_path.stat().st_size,
                    "saved_path": str(src_path),
                    "filename": src_path.name,
                    "context_before": ref.get("alt", ""),
                })
            else:
                extracted_images.append({
                    "source": src,
                    "format": Path(src).suffix[1:].lower() if "." in src else "unknown",
                    "size_bytes": 0,
                    "url": src,
                })

    return {
        "text": text,
        "page_count": 0,
        "title": title,
        "author": word_metadata.get("author", ""),
        "date": word_metadata.get("created", ""),
        "method": method,
        "extracted_images": extracted_images,
    }


def extract_mhtml(filepath: Path, extract_images: bool = False, image_dir: str | None = None) -> dict:
    """Extract text from MHTML/MHT file (MIME-encoded web archive)."""
    try:
        raw = filepath.read_bytes()
    except Exception as e:
        return {"error": f"Read error: {str(e)}"}

    extracted_images = []
    html_content = ""

    try:
        msg = email.message_from_bytes(raw, policy=email.policy.default)
    except Exception as e:
        return {"error": f"MHTML parse error: {str(e)}"}

    # Walk MIME parts to find HTML and images
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type == "text/html" and not html_content:
                charset = part.get_content_charset() or "utf-8"
                try:
                    payload = part.get_payload(decode=True)
                    if payload:
                        html_content = payload.decode(charset, errors="replace")
                except Exception:
                    try:
                        html_content = part.get_payload(decode=False) or ""
                    except Exception:
                        pass
            elif content_type.startswith("image/"):
                try:
                    payload = part.get_payload(decode=True)
                    location = part.get("Content-Location", "") or part.get_filename() or ""
                    if payload:
                        img_entry = {
                            "source": location,
                            "format": content_type.split("/")[-1],
                            "size_bytes": len(payload),
                        }
                        if extract_images and image_dir and len(payload) >= MIN_IMAGE_BYTES:
                            img_ext = content_type.split("/")[-1].replace("jpeg", "jpg")
                            img_count = len(extracted_images) + 1
                            img_filename = f"mhtml_img{img_count}.{img_ext}"
                            img_path = os.path.join(image_dir, img_filename)
                            with open(img_path, "wb") as f:
                                f.write(payload)
                            img_entry["saved_path"] = img_path
                            img_entry["filename"] = img_filename
                        extracted_images.append(img_entry)
                except Exception:
                    pass
    else:
        # Single-part — likely just HTML
        charset = msg.get_content_charset() or "utf-8"
        try:
            payload = msg.get_payload(decode=True)
            if payload:
                html_content = payload.decode(charset, errors="replace")
        except Exception:
            html_content = msg.get_payload(decode=False) or ""

    if not html_content:
        return {"error": "No HTML content found in MHTML file."}

    # Check if it's Word-generated MHTML
    word_metadata = {}
    if is_word_html(html_content):
        html_content, word_metadata = clean_word_html(html_content)
        method = "mhtml_word"
    else:
        method = "mhtml"

    # Extract title
    title = word_metadata.get("title", "")
    if not title:
        m = re.search(r"<title[^>]*>(.*?)</title>", html_content, re.IGNORECASE | re.DOTALL)
        if m:
            title = re.sub(r"<[^>]+>", "", m.group(1)).strip()

    parser = SimpleHTMLToText(extract_images=extract_images)
    try:
        parser.feed(html_content)
    except Exception:
        pass

    text = parser.get_text()

    return {
        "text": text,
        "page_count": 0,
        "title": title,
        "author": word_metadata.get("author", ""),
        "date": word_metadata.get("created", ""),
        "method": method,
        "extracted_images": extracted_images,
    }


def extract_text_file(filepath: Path) -> dict:
    """Extract text from plain text or markdown file."""
    try:
        text = filepath.read_text(encoding="utf-8", errors="replace")
    except UnicodeDecodeError:
        try:
            text = filepath.read_text(encoding="latin-1", errors="replace")
        except Exception as e:
            return {"error": f"Read error: {str(e)}"}
    except Exception as e:
        return {"error": f"Read error: {str(e)}"}

    # Try to extract title from first heading or first line
    title = ""
    for line in text.split("\n"):
        line = line.strip()
        if line:
            if line.startswith("#"):
                title = line.lstrip("#").strip()
            else:
                title = line[:100]
            break

    suffix = filepath.suffix.lower()
    method = "direct_read"
    if suffix in (".md", ".markdown"):
        method = "markdown_read"
    elif suffix == ".rst":
        method = "rst_read"

    return {
        "text": text,
        "page_count": 0,
        "title": title,
        "author": "",
        "date": "",
        "method": method,
        "extracted_images": [],
    }


def extract_document(filepath_str: str, extract_images: bool = False) -> dict:
    """Extract content from a local document file.

    When extract_images=True, images are saved to a temp directory and
    [IMAGE: filename] placeholders are inserted inline in the text.
    """
    filepath = Path(filepath_str).resolve()

    if not filepath.exists():
        return {"error": f"File not found: {filepath}"}

    if not filepath.is_file():
        return {"error": f"Not a file: {filepath}"}

    suffix = filepath.suffix.lower()
    fmt = SUPPORTED_FORMATS.get(suffix)

    if not fmt:
        return {"error": f"Unsupported format: {suffix}. Supported: {', '.join(sorted(SUPPORTED_FORMATS.keys()))}"}

    file_size = filepath.stat().st_size

    # Create temp directory for image extraction
    image_dir = None
    if extract_images:
        image_dir = tempfile.mkdtemp(prefix="wiki_images_")

    # Dispatch to format-specific extractor
    if fmt == "pdf":
        result = extract_pdf(filepath, extract_images=extract_images, image_dir=image_dir)
    elif fmt == "docx":
        result = extract_docx(filepath, extract_images=extract_images, image_dir=image_dir)
    elif fmt == "html":
        result = extract_html(filepath, extract_images=extract_images, image_dir=image_dir)
    elif fmt == "mhtml":
        result = extract_mhtml(filepath, extract_images=extract_images, image_dir=image_dir)
    elif fmt in ("txt", "markdown", "rst", "rtf"):
        result = extract_text_file(filepath)
    elif fmt == "doc":
        return {"error": "Legacy .doc format not supported. Convert to .docx first."}
    else:
        return {"error": f"No extractor for format: {fmt}"}

    if "error" in result:
        return result

    text = result["text"]

    # Generate markdown version
    # For text/markdown files, content IS markdown already
    if fmt in ("markdown",):
        content_markdown = text
    else:
        content_markdown = text  # Other formats produce pseudo-markdown

    # Plain text version
    content_text = re.sub(r"[#*`>]", "", text)
    content_text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", content_text)
    content_text = re.sub(r"\n{3,}", "\n\n", content_text).strip()

    word_count = len(content_text.split())

    # Extract URLs
    urls = extract_urls(text)
    extracted_urls = [{"url": u, "context": "found in document text", "source": "content"} for u in urls]

    # File modification date as fallback
    file_date = ""
    try:
        mtime = filepath.stat().st_mtime
        file_date = datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        pass

    doc_date = result.get("date", "") or file_date

    return {
        "source_type": "document",
        "filepath": str(filepath),
        "filename": filepath.name,
        "format": fmt,
        "title": result.get("title", "") or filepath.stem,
        "author": result.get("author", ""),
        "date": doc_date,
        "content_markdown": content_markdown,
        "content_text": content_text,
        "word_count": word_count,
        "page_count": result.get("page_count", 0),
        "extracted_urls": extracted_urls,
        "extracted_images": result.get("extracted_images", []),
        "image_dir": image_dir,
        "has_inline_images": bool(image_dir and any(
            img.get("saved_path") for img in result.get("extracted_images", [])
        )),
        "metadata": {
            "fetch_date": datetime.now(tz=timezone.utc).strftime("%Y-%m-%d"),
            "extraction_method": result.get("method", "unknown"),
            "file_size_bytes": file_size,
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Extract text content from local document files")
    parser.add_argument("filepath", help="Path to the document file")
    parser.add_argument("--extract-images", action="store_true", default=False,
                        help="Extract images to a temp directory and insert inline placeholders")
    args = parser.parse_args()

    result = extract_document(args.filepath, extract_images=args.extract_images)
    if "error" in result:
        json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
        sys.exit(1)

    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
