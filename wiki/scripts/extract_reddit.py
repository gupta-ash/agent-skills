#!/usr/bin/env python
"""Extract Reddit thread content, comments, and linked URLs.

Usage:
    python extract_reddit.py <reddit-url> [--limit N]

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

# Force UTF-8 stdout on Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


USER_AGENT = "wiki/1.0 (knowledge-compiler)"

# URLs to skip during extraction (non-content links)
SKIP_URL_PATTERNS = [
    re.compile(r"https?://(www\.)?reddit\.com/user/"),
    re.compile(r"https?://(www\.)?reddit\.com/r/\w+/?$"),
    re.compile(r"https?://v\.redd\.it/"),
    re.compile(r"https?://(www\.)?google\.com/.*[?&](q|url|redirect)="),
]

URL_REGEX = re.compile(r"https?://[^\s\)\]>\"'`]+")

# Patterns that identify image URLs
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
IMAGE_DOMAINS = {"i.redd.it", "i.imgur.com"}

def should_skip_url(url: str) -> bool:
    """Check if a URL should be filtered out."""
    for pattern in SKIP_URL_PATTERNS:
        if pattern.search(url):
            return True
    return False


def is_image_url(url: str) -> bool:
    """Check if a URL points to an image (by extension or known image domain)."""
    # Strip query params for extension check
    path = url.split("?")[0].split("#")[0].lower()
    if any(path.endswith(ext) for ext in IMAGE_EXTENSIONS):
        return True
    # Check known image hosting domains
    try:
        from urllib.parse import urlparse
        host = urlparse(url).hostname or ""
        if host in IMAGE_DOMAINS:
            return True
    except Exception:
        pass
    return False


def collect_image_urls_from_text(text: str) -> list[str]:
    """Extract image URLs from text, deduplicated."""
    if not text:
        return []
    urls = URL_REGEX.findall(text)
    images = []
    for url in urls:
        url = url.rstrip(".,;:!?)")
        if is_image_url(url):
            images.append(url)
    return list(dict.fromkeys(images))


def extract_urls(text: str) -> list[str]:
    """Extract all URLs from text, deduplicated."""
    if not text:
        return []
    urls = URL_REGEX.findall(text)
    # Clean trailing punctuation
    cleaned = []
    for url in urls:
        url = url.rstrip(".,;:!?)")
        if not should_skip_url(url):
            cleaned.append(url)
    return list(dict.fromkeys(cleaned))  # dedupe preserving order


def utc_iso(ts: float) -> str:
    """Convert Unix timestamp to ISO 8601 string."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_comment(comment_data: dict, depth: int = 0) -> dict | None:
    """Parse a single comment from Reddit's JSON structure."""
    kind = comment_data.get("kind")
    if kind == "more":
        # "More comments" stub — record count but don't fetch
        data = comment_data.get("data", {})
        count = data.get("count", 0)
        return {
            "author": "[more]",
            "body": f"[{count} more comments not loaded]",
            "score": 0,
            "depth": depth,
            "replies": [],
        }

    if kind != "t1":
        return None

    data = comment_data.get("data", {})
    author = data.get("author", "[deleted]")
    body = data.get("body", "[removed]")
    score = data.get("score", 0)

    # Parse replies recursively
    replies = []
    replies_data = data.get("replies")
    if isinstance(replies_data, dict):
        children = replies_data.get("data", {}).get("children", [])
        for child in children:
            parsed = parse_comment(child, depth + 1)
            if parsed:
                replies.append(parsed)

    return {
        "author": author,
        "body": body,
        "score": score,
        "depth": depth,
        "replies": replies,
    }


def collect_urls_from_comments(comments: list, extracted: list):
    """Recursively collect URLs from comment tree."""
    for comment in comments:
        if comment["author"] == "[more]":
            continue
        urls = extract_urls(comment.get("body", ""))
        for url in urls:
            extracted.append({
                "url": url,
                "context": f"mentioned in comment by {comment['author']}",
                "source": "comment",
            })
        if comment.get("replies"):
            collect_urls_from_comments(comment["replies"], extracted)


def count_comments(comments: list) -> int:
    """Count total comments in tree."""
    total = 0
    for c in comments:
        if c["author"] != "[more]":
            total += 1
        total += count_comments(c.get("replies", []))
    return total


def normalize_reddit_url(url: str) -> str:
    """Normalize a Reddit URL for the JSON API."""
    # Remove query parameters
    url = url.split("?")[0]
    # Strip trailing slash
    url = url.rstrip("/")
    # Append .json directly (no trailing slash before it)
    if not url.endswith(".json"):
        url += ".json"
    # Ensure https
    if url.startswith("http://"):
        url = "https://" + url[7:]
    # Use www.reddit.com
    url = url.replace("://reddit.com", "://www.reddit.com")
    # Use old.reddit.com to avoid redirect issues
    url = url.replace("://www.reddit.com", "://old.reddit.com")
    return url


def fetch_reddit(url: str, limit: int | None = None) -> dict:
    """Fetch and parse a Reddit thread."""
    json_url = normalize_reddit_url(url)
    # raw_json=1 gives unescaped unicode in JSON responses
    separator = "&" if "?" in json_url else "?"
    json_url += f"{separator}raw_json=1"
    if limit:
        json_url += f"&limit={limit}"

    req = urllib.request.Request(json_url, headers={"User-Agent": USER_AGENT})

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.reason} — URL: {json_url}"}
    except urllib.error.URLError as e:
        return {"error": f"URL error: {e.reason}"}
    except Exception as e:
        return {"error": f"Fetch error: {str(e)}"}

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return {"error": f"JSON parse error: {str(e)}"}

    # Reddit returns a list: [post_listing, comments_listing]
    if not isinstance(data, list) or len(data) < 2:
        return {"error": "Unexpected Reddit JSON structure — expected list of 2 listings"}

    # Parse post
    post_listing = data[0]
    post_data = post_listing.get("data", {}).get("children", [{}])[0].get("data", {})

    subreddit = post_data.get("subreddit", "")
    title = post_data.get("title", "")
    author = post_data.get("author", "[deleted]")
    score = post_data.get("score", 0)
    upvote_ratio = post_data.get("upvote_ratio", 0)
    num_comments = post_data.get("num_comments", 0)
    created_utc = post_data.get("created_utc", 0)
    selftext = post_data.get("selftext", "")
    post_url = post_data.get("url", url)

    # Parse comments
    comment_listing = data[1]
    comment_children = comment_listing.get("data", {}).get("children", [])
    comments = []
    for child in comment_children:
        parsed = parse_comment(child, depth=0)
        if parsed:
            comments.append(parsed)

    # Extract URLs
    extracted_urls = []

    # URLs from selftext
    selftext_urls = extract_urls(selftext)
    for u in selftext_urls:
        extracted_urls.append({
            "url": u,
            "context": f"mentioned in post by {author}",
            "source": "selftext",
        })

    # URLs from post URL (if it's a link post)
    if post_url and post_url != url and not should_skip_url(post_url):
        extracted_urls.append({
            "url": post_url,
            "context": "thread link target",
            "source": "post_url",
        })

    # URLs from comments
    collect_urls_from_comments(comments, extracted_urls)

    # Deduplicate extracted URLs by url
    seen_urls = set()
    deduped_urls = []
    for entry in extracted_urls:
        if entry["url"] not in seen_urls:
            seen_urls.add(entry["url"])
            deduped_urls.append(entry)

    total_parsed = count_comments(comments)

    # Collect image URLs from selftext, post URL, and comments
    image_urls = []
    # From selftext
    image_urls.extend(collect_image_urls_from_text(selftext))
    # From post URL (if it's an image post)
    if post_url and is_image_url(post_url):
        if post_url not in image_urls:
            image_urls.insert(0, post_url)
    # From preview URL (Reddit image posts often have preview)
    preview = post_data.get("preview", {})
    if preview:
        preview_images = preview.get("images", [])
        for pi in preview_images:
            source_url = pi.get("source", {}).get("url", "")
            if source_url and source_url not in image_urls:
                image_urls.append(source_url)
    # Deduplicate preserving order
    image_urls = list(dict.fromkeys(image_urls))

    # Clean the original URL (remove .json if we added it)
    clean_url = url.rstrip("/").split("?")[0]

    return {
        "source_type": "reddit",
        "url": clean_url,
        "subreddit": subreddit,
        "title": title,
        "author": author,
        "score": score,
        "upvote_ratio": upvote_ratio,
        "num_comments": num_comments,
        "created_utc": utc_iso(created_utc) if created_utc else None,
        "selftext": selftext,
        "comments": comments,
        "extracted_urls": deduped_urls,
        "image_urls": image_urls,
        "metadata": {
            "fetch_date": datetime.now(tz=timezone.utc).strftime("%Y-%m-%d"),
            "total_comments_parsed": total_parsed,
            "total_urls_extracted": len(deduped_urls),
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Extract Reddit thread content and comments")
    parser.add_argument("url", help="Reddit thread URL")
    parser.add_argument("--limit", type=int, default=None, help="Max comments to fetch")
    args = parser.parse_args()

    result = fetch_reddit(args.url, args.limit)
    if "error" in result:
        json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
        sys.exit(1)

    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
