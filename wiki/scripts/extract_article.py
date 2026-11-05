#!/usr/bin/env python
"""Extract article/web page content, clean it for wiki consumption.

Usage:
    python extract_article.py <url> [--include-links]

Outputs structured JSON to stdout.
"""

import argparse
import io
import json
import re
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

# Force UTF-8 stdout on Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


USER_AGENT = "wiki/1.0 (knowledge-compiler)"
URL_REGEX = re.compile(r"https?://[^\s\)\]>\"'`]+")

# Tags to strip entirely (including content)
STRIP_TAGS = {"script", "style", "nav", "header", "footer", "aside", "noscript",
              "iframe", "svg", "form", "button", "input", "select", "textarea"}

# Tags that act as block-level separators
BLOCK_TAGS = {"p", "div", "section", "article", "main", "h1", "h2", "h3", "h4", "h5", "h6",
              "blockquote", "pre", "ul", "ol", "li", "table", "tr", "td", "th", "figure",
              "figcaption", "details", "summary", "hr", "br", "dt", "dd"}

# Content container candidates (priority order)
CONTENT_SELECTORS = ["article", "main", "[role=main]"]


class MetadataExtractor(HTMLParser):
    """Extract metadata from HTML head."""

    def __init__(self):
        super().__init__()
        self.title = ""
        self.og_title = ""
        self.author = ""
        self.date = ""
        self.description = ""
        self.og_description = ""
        self.site_name = ""
        self._in_title = False
        self._title_parts = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "title":
            self._in_title = True
            self._title_parts = []
        elif tag == "meta":
            name = attrs_dict.get("name", "").lower()
            prop = attrs_dict.get("property", "").lower()
            content = attrs_dict.get("content", "")
            if name == "author" or prop == "article:author":
                self.author = content
            elif name == "description":
                self.description = content
            elif prop == "og:description":
                self.og_description = content
            elif prop == "og:title":
                self.og_title = content
            elif prop == "og:site_name":
                self.site_name = content
            elif prop == "article:published_time" or name == "date":
                self.date = content
        elif tag == "time":
            dt = attrs_dict.get("datetime", "")
            if dt and not self.date:
                self.date = dt

    def handle_data(self, data):
        if self._in_title:
            self._title_parts.append(data)

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
            self.title = "".join(self._title_parts).strip()


class ContentExtractor(HTMLParser):
    """Extract and convert HTML content to markdown."""

    def __init__(self, base_url: str = ""):
        super().__init__()
        self.base_url = base_url
        self._output = []
        self._tag_stack = []
        self._skip_depth = 0
        self._in_pre = False
        self._in_code = False
        self._code_lang = ""
        self._link_href = ""
        self._link_text_parts = []
        self._in_link = False
        self._list_depth = 0
        self._ordered_list = False
        self._list_counter = 0
        self.images = []
        self.links = []
        # Table tracking
        self._in_table = False
        self._table_rows = []
        self._current_row = []
        self._current_cell_parts = []
        self._in_cell = False
        self._is_header_row = False

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        tag = tag.lower()

        # Skip stripped tags
        if tag in STRIP_TAGS:
            self._skip_depth += 1
            self._tag_stack.append(tag)
            return

        if self._skip_depth > 0:
            self._tag_stack.append(tag)
            return

        # Also skip elements with common non-content classes/roles
        cls = attrs_dict.get("class", "").lower()
        role = attrs_dict.get("role", "").lower()
        skip_classes = ("sidebar", "nav", "menu", "ad", "advertisement", "social",
                        "share", "related", "comment", "footer", "header")
        if any(sc in cls for sc in skip_classes) or role in ("navigation", "banner", "complementary"):
            self._skip_depth += 1
            self._tag_stack.append(tag)
            return

        self._tag_stack.append(tag)

        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(tag[1])
            self._output.append("\n\n" + "#" * level + " ")
        elif tag == "p":
            self._output.append("\n\n")
        elif tag == "br":
            self._output.append("\n")
        elif tag == "hr":
            self._output.append("\n\n---\n\n")
        elif tag == "blockquote":
            self._output.append("\n\n> ")
        elif tag == "pre":
            self._in_pre = True
            self._output.append("\n\n```")
        elif tag == "code":
            if self._in_pre:
                # Extract language hint from class attribute
                cls = attrs_dict.get("class", "")
                lang_match = re.search(r'language-(\w+)', cls) or re.search(r'lang-(\w+)', cls)
                if lang_match:
                    self._code_lang = lang_match.group(1)
                    self._output.append(self._code_lang)
                self._output.append("\n")
            else:
                self._in_code = True
                self._output.append("`")
        elif tag == "strong" or tag == "b":
            self._output.append("**")
        elif tag == "em" or tag == "i":
            self._output.append("*")
        elif tag == "a":
            href = attrs_dict.get("href", "")
            if href and not href.startswith("#") and not href.startswith("javascript:"):
                if not href.startswith(("http://", "https://")):
                    href = urljoin(self.base_url, href)
                self._link_href = href
                self._link_text_parts = []
                self._in_link = True
            else:
                self._link_href = ""
        elif tag == "img":
            src = attrs_dict.get("src", "")
            alt = attrs_dict.get("alt", "")
            if src:
                if not src.startswith(("http://", "https://")):
                    src = urljoin(self.base_url, src)
                self.images.append({"src": src, "alt": alt})
                if alt:
                    self._output.append(f"![{alt}]({src})")
        elif tag == "ul":
            self._list_depth += 1
            self._ordered_list = False
            self._output.append("\n")
        elif tag == "ol":
            self._list_depth += 1
            self._ordered_list = True
            self._list_counter = 0
            self._output.append("\n")
        elif tag == "li":
            indent = "  " * max(0, self._list_depth - 1)
            if self._ordered_list:
                self._list_counter += 1
                self._output.append(f"\n{indent}{self._list_counter}. ")
            else:
                self._output.append(f"\n{indent}- ")
        elif tag in ("div", "section", "article", "main"):
            self._output.append("\n")
        elif tag == "table":
            self._in_table = True
            self._table_rows = []
        elif tag == "tr":
            self._current_row = []
            self._is_header_row = False
        elif tag in ("td", "th"):
            self._in_cell = True
            self._current_cell_parts = []
            if tag == "th":
                self._is_header_row = True

    def handle_endtag(self, tag):
        tag = tag.lower()

        if self._tag_stack and self._tag_stack[-1] == tag:
            self._tag_stack.pop()

        if self._skip_depth > 0:
            if tag in STRIP_TAGS or self._skip_depth > 0:
                self._skip_depth = max(0, self._skip_depth - 1)
            return

        if tag == "pre":
            self._in_pre = False
            self._code_lang = ""
            self._output.append("\n```\n")
        elif tag == "code":
            if not self._in_pre:
                self._in_code = False
                self._output.append("`")
        elif tag == "strong" or tag == "b":
            self._output.append("**")
        elif tag == "em" or tag == "i":
            self._output.append("*")
        elif tag == "a":
            if self._in_link and self._link_href:
                text = "".join(self._link_text_parts).strip()
                if text:
                    self._output.append(f"[{text}]({self._link_href})")
                    self.links.append({"url": self._link_href, "anchor_text": text})
                else:
                    self._output.append(self._link_href)
                    self.links.append({"url": self._link_href, "anchor_text": ""})
            self._in_link = False
            self._link_href = ""
        elif tag in ("ul", "ol"):
            self._list_depth = max(0, self._list_depth - 1)
            self._output.append("\n")
        elif tag in ("td", "th"):
            self._in_cell = False
            cell_text = "".join(self._current_cell_parts).strip()
            cell_text = re.sub(r"\s+", " ", cell_text)
            self._current_row.append(cell_text)
        elif tag == "tr":
            if self._current_row:
                self._table_rows.append((self._current_row, self._is_header_row))
        elif tag == "table":
            self._in_table = False
            if self._table_rows:
                # Build markdown table
                all_rows = [row for row, _ in self._table_rows]
                if all_rows:
                    max_cols = max(len(r) for r in all_rows)
                    # Pad rows to same width
                    for r in all_rows:
                        while len(r) < max_cols:
                            r.append("")
                    first_is_header = self._table_rows[0][1] if self._table_rows else False
                    header_row = all_rows[0]
                    md = "\n\n| " + " | ".join(header_row) + " |\n"
                    md += "| " + " | ".join(["---"] * max_cols) + " |\n"
                    for row in all_rows[1:]:
                        md += "| " + " | ".join(row) + " |\n"
                    self._output.append(md)
            self._table_rows = []
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6", "p"):
            self._output.append("\n")

    def handle_data(self, data):
        if self._skip_depth > 0:
            return

        if self._in_link:
            self._link_text_parts.append(data)
            return

        if self._in_cell:
            self._current_cell_parts.append(data)
            return

        if self._in_pre:
            self._output.append(data)
        else:
            # Collapse whitespace
            text = re.sub(r"\s+", " ", data)
            self._output.append(text)

    def handle_entityref(self, name):
        char_map = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'",
                     "nbsp": " ", "mdash": "—", "ndash": "–"}
        ch = char_map.get(name, f"&{name};")
        if self._skip_depth == 0:
            if self._in_link:
                self._link_text_parts.append(ch)
            else:
                self._output.append(ch)

    def handle_charref(self, name):
        try:
            if name.startswith("x"):
                ch = chr(int(name[1:], 16))
            else:
                ch = chr(int(name))
        except (ValueError, OverflowError):
            ch = f"&#{name};"
        if self._skip_depth == 0:
            if self._in_link:
                self._link_text_parts.append(ch)
            else:
                self._output.append(ch)

    def get_markdown(self) -> str:
        text = "".join(self._output)
        # Clean up excessive newlines
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def try_beautifulsoup(html: str, base_url: str) -> tuple[str, list, list] | None:
    """Try to use BeautifulSoup for better content extraction."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return None

    soup = BeautifulSoup(html, "html.parser")

    # Remove unwanted elements
    for tag in soup.find_all(STRIP_TAGS):
        tag.decompose()

    # Remove elements with non-content classes
    for tag in soup.find_all(True, class_=re.compile(
            r"(sidebar|nav|menu|ad|advertisement|social|share|related|comment|footer|header)", re.I)):
        tag.decompose()

    # Find main content container
    content = None
    for selector in ["article", "main", "[role='main']"]:
        content = soup.find(selector.replace("[role='main']", ""))
        if selector == "[role='main']":
            content = soup.find(attrs={"role": "main"})
        if content:
            break

    if not content:
        # Use body or largest div
        content = soup.find("body") or soup

    # Extract using ContentExtractor on the found content
    extractor = ContentExtractor(base_url)
    extractor.feed(str(content))
    return extractor.get_markdown(), extractor.links, extractor.images


def is_word_html(content: str) -> bool:
    """Detect if HTML content was generated by Microsoft Word."""
    return '<meta name=ProgId content=Word.Document>' in content or \
           '<meta name=Generator content="Microsoft Word' in content or \
           'xmlns:w="urn:schemas-microsoft-com:office:word"' in content or \
           'xmlns:o="urn:schemas-microsoft-com:office:office"' in content


def clean_word_html(content: str) -> tuple[str, dict]:
    """Clean Word-generated HTML and extract metadata."""
    metadata = {}
    props_match = re.search(
        r'<o:DocumentProperties>(.*?)</o:DocumentProperties>',
        content, re.DOTALL | re.IGNORECASE
    )
    if props_match:
        props_xml = props_match.group(1)
        for tag in ("Title", "Author", "Created", "LastSaved", "Company"):
            m = re.search(rf'<o:{tag}>(.*?)</o:{tag}>', props_xml, re.DOTALL | re.IGNORECASE)
            if m:
                metadata[tag.lower()] = m.group(1).strip()

    content = re.sub(r'<!--\[if.*?\]>.*?<!\[endif\]-->', '', content, flags=re.DOTALL)
    content = re.sub(r'<v:[^>]*>.*?</v:[^>]*>', '', content, flags=re.DOTALL)
    content = re.sub(r'<v:[^>]*/>', '', content)
    content = re.sub(r'mso-[\w-]+:[^;]+;?', '', content)
    content = re.sub(r'</?o:[^>]*>', '', content)
    content = re.sub(r'</?w:[^>]*>', '', content)
    return content, metadata


def detect_paywall(html: str, extracted_text: str) -> list[str]:
    """Detect paywall indicators in HTML content."""
    indicators = []
    if len(extracted_text) < 500 and len(html) > 5000:
        indicators.append("suspiciously_short_content")
    paywall_patterns = [
        'piano.io', 'tinypass', 'leaky-paywall', 'paywall',
        'subscribe-wall', 'premium-content', 'regwall', 'meter-count',
    ]
    html_lower = html.lower()
    for p in paywall_patterns:
        if p in html_lower:
            indicators.append(f"paywall_pattern:{p}")
    return indicators


def extract_article(url: str, include_links: bool = False) -> dict:
    """Fetch and extract article content."""
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            content_type = resp.headers.get("Content-Type", "")
            # Detect encoding
            encoding = "utf-8"
            if "charset=" in content_type:
                encoding = content_type.split("charset=")[-1].split(";")[0].strip()
            raw = resp.read()
            html = raw.decode(encoding, errors="replace")
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.reason}"}
    except urllib.error.URLError as e:
        return {"error": f"URL error: {e.reason}"}
    except Exception as e:
        return {"error": f"Fetch error: {str(e)}"}

    parsed_url = urlparse(url)
    base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
    site_name = parsed_url.netloc.replace("www.", "")

    # Extract metadata
    meta_parser = MetadataExtractor()
    try:
        meta_parser.feed(html)
    except Exception:
        pass

    # Extract content — try BeautifulSoup first, fall back to stdlib
    extraction_method = "html_parser"
    bs_result = try_beautifulsoup(html, base_url)
    if bs_result:
        content_md, links, images = bs_result
        extraction_method = "beautifulsoup"
    else:
        extractor = ContentExtractor(base_url)
        try:
            extractor.feed(html)
        except Exception:
            pass
        content_md = extractor.get_markdown()
        links = extractor.links
        images = extractor.images

    # Plain text version
    content_text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", content_md)  # strip md links
    content_text = re.sub(r"[#*`>]", "", content_text)  # strip md formatting
    content_text = re.sub(r"\n{2,}", "\n\n", content_text).strip()

    # Word count
    word_count = len(content_text.split())

    # Title
    title = meta_parser.og_title or meta_parser.title or ""
    # Clean title — remove site name suffix if present
    if " | " in title:
        title = title.split(" | ")[0].strip()
    elif " - " in title and site_name.lower() in title.lower():
        title = title.rsplit(" - ", 1)[0].strip()

    # Date — try to parse and normalize
    date = meta_parser.date
    if date:
        # Try to extract just the date portion
        m = re.match(r"(\d{4}[-/]\d{1,2}[-/]\d{1,2})", date)
        if m:
            date = m.group(1).replace("/", "-")

    # Description
    description = meta_parser.description or meta_parser.og_description or ""

    # Site name
    if meta_parser.site_name:
        site_name = meta_parser.site_name

    # Build extracted_urls
    extracted_urls = []
    if include_links:
        for link in links:
            extracted_urls.append({
                "url": link["url"],
                "context": "linked in article body",
                "anchor_text": link.get("anchor_text", ""),
            })

    # Paywall detection
    paywall_indicators = detect_paywall(html, content_text)

    # JS-rendered content detection
    js_rendered_warning = ""
    if len(content_text) < 200 and len(html) > 2048:
        js_rendered_warning = "Content may require JavaScript rendering. Consider using playwright."

    result = {
        "source_type": "article",
        "url": url,
        "title": title,
        "author": meta_parser.author,
        "date": date,
        "site_name": site_name,
        "description": description,
        "content_markdown": content_md,
        "content_text": content_text,
        "word_count": word_count,
        "extracted_urls": extracted_urls,
        "images": images,
        "paywall_indicators": paywall_indicators,
        "metadata": {
            "fetch_date": datetime.now(tz=timezone.utc).strftime("%Y-%m-%d"),
            "content_length": len(content_md),
            "extraction_method": extraction_method,
        },
    }

    if js_rendered_warning:
        result["js_rendered_warning"] = js_rendered_warning

    return result


def extract_local_html(filepath_str: str, include_links: bool = False, *, base_url: str = "") -> dict:
    """Extract content from a local HTML file, with Word HTML detection."""
    filepath = Path(filepath_str).resolve()
    if not filepath.exists():
        return {"error": f"File not found: {filepath}"}

    try:
        html = filepath.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return {"error": f"Read error: {str(e)}"}

    extraction_method = "local_html"
    extracted_images = []
    word_metadata = {}

    # Check for Word HTML
    if is_word_html(html):
        if 'charset=windows-1252' in html.lower():
            try:
                html = filepath.read_bytes().decode("windows-1252", errors="replace")
            except Exception:
                pass
        html, word_metadata = clean_word_html(html)
        extraction_method = "local_word_html"

        # Check companion _files/ directory
        files_dir = filepath.parent / (filepath.stem + "_files")
        if files_dir.is_dir():
            try:
                for img_file in files_dir.iterdir():
                    if img_file.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".emf", ".wmf"):
                        extracted_images.append({
                            "source": str(img_file),
                            "format": img_file.suffix[1:].lower(),
                            "size_bytes": img_file.stat().st_size,
                        })
            except Exception:
                pass

    # Extract metadata
    meta_parser = MetadataExtractor()
    try:
        meta_parser.feed(html)
    except Exception:
        pass

    # Extract content
    extractor = ContentExtractor(base_url)
    try:
        extractor.feed(html)
    except Exception:
        pass
    content_md = extractor.get_markdown()

    # Plain text
    content_text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", content_md)
    content_text = re.sub(r"[#*`>]", "", content_text)
    content_text = re.sub(r"\n{2,}", "\n\n", content_text).strip()

    word_count = len(content_text.split())
    title = word_metadata.get("title", "") or meta_parser.og_title or meta_parser.title or filepath.stem

    extracted_urls = []
    if include_links:
        for link in extractor.links:
            extracted_urls.append({
                "url": link["url"],
                "context": "linked in document body",
                "anchor_text": link.get("anchor_text", ""),
            })

    return {
        "source_type": "local_html",
        "filepath": str(filepath),
        "title": title,
        "author": word_metadata.get("author", "") or meta_parser.author,
        "date": word_metadata.get("created", "") or meta_parser.date,
        "description": meta_parser.description or meta_parser.og_description or "",
        "content_markdown": content_md,
        "content_text": content_text,
        "word_count": word_count,
        "extracted_urls": extracted_urls,
        "images": extractor.images,
        "extracted_images": extracted_images,
        "paywall_indicators": [],
        "metadata": {
            "fetch_date": datetime.now(tz=timezone.utc).strftime("%Y-%m-%d"),
            "content_length": len(content_md),
            "extraction_method": extraction_method,
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Extract article/web page content")
    parser.add_argument("url", help="Article URL or local HTML file path")
    parser.add_argument("--include-links", action="store_true",
                        help="Include extracted links in output")
    parser.add_argument("--base-url", default="", help="Original response URL for captured local HTML links/assets")
    args = parser.parse_args()

    # Detect local file vs URL
    input_path = Path(args.url)
    if input_path.exists() and input_path.suffix.lower() in (".html", ".htm"):
        result = extract_local_html(args.url, args.include_links, base_url=args.base_url)
    else:
        result = extract_article(args.url, args.include_links)

    if "error" in result:
        json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
        sys.exit(1)

    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
