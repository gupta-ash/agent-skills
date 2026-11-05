#!/usr/bin/env python
"""Extract YouTube video transcript, metadata, and description links.

Usage:
    python extract_youtube.py <youtube-url>

Outputs structured JSON to stdout.
"""

import argparse
import io
import json
import re
import shutil
import subprocess
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone

# Force UTF-8 stdout on Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


USER_AGENT = "wiki/1.0 (knowledge-compiler)"
URL_REGEX = re.compile(r"https?://[^\s\)\]>\"'`]+")


def extract_video_id(url: str) -> str | None:
    """Extract YouTube video ID from various URL formats."""
    patterns = [
        r"(?:youtube\.com/watch\?.*v=|youtu\.be/|youtube\.com/embed/|youtube\.com/v/|youtube\.com/shorts/)([a-zA-Z0-9_-]{11})",
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None


def format_duration(seconds: int | float) -> str:
    """Format seconds into HH:MM:SS or MM:SS."""
    seconds = int(seconds)
    h, remainder = divmod(seconds, 3600)
    m, s = divmod(remainder, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def extract_urls(text: str) -> list[str]:
    """Extract all URLs from text, deduplicated."""
    if not text:
        return []
    urls = URL_REGEX.findall(text)
    cleaned = [u.rstrip(".,;:!?)") for u in urls]
    return list(dict.fromkeys(cleaned))


def get_metadata_ytdlp(url: str) -> dict | None:
    """Get video metadata using yt-dlp -j."""
    ytdlp = shutil.which("yt-dlp")
    if not ytdlp:
        return None

    try:
        result = subprocess.run(
            [ytdlp, "-j", "--no-warnings", url],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return None


def get_metadata_oembed(url: str) -> dict | None:
    """Get basic metadata using YouTube oEmbed API."""
    oembed_url = f"https://www.youtube.com/oembed?url={urllib.request.quote(url, safe='')}&format=json"
    req = urllib.request.Request(oembed_url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def get_transcript_ytdlp(url: str, video_id: str, meta: dict | None = None) -> tuple[str | None, list | None, str | None]:
    """Get transcript using yt-dlp metadata subtitle URLs.

    Extracts subtitle URLs from yt-dlp JSON metadata and downloads directly,
    avoiding yt-dlp's file-based subtitle download which can fail on Windows.

    Returns: (full_text, segments, method)
    """
    if meta is None:
        meta = get_metadata_ytdlp(url)
    if not meta:
        return None, None, None

    # Try manual subtitles first, then auto-generated
    for sub_key, method_label in [("subtitles", "yt-dlp (manual)"), ("automatic_captions", "yt-dlp (auto)")]:
        subs = meta.get(sub_key, {})
        if not subs:
            continue

        # Try 'en' first, then any English variant
        en_subs = subs.get("en")
        if not en_subs:
            for key in subs:
                if key.startswith("en"):
                    en_subs = subs[key]
                    break
        if not en_subs:
            continue

        # Strategy 1: Try json3/srv1 direct download (works for some videos)
        for preferred_fmt in ["json3", "srv1"]:
            for sub in en_subs:
                if sub.get("ext") == preferred_fmt and sub.get("url"):
                    try:
                        req = urllib.request.Request(sub["url"], headers={"User-Agent": "Mozilla/5.0"})
                        with urllib.request.urlopen(req, timeout=30) as resp:
                            raw = resp.read()
                            if len(raw) == 0:
                                continue
                            text = raw.decode("utf-8")
                            sub_data = json.loads(text)
                            events = sub_data.get("events", [])
                            segments = []
                            full_text_parts = []
                            for event in events:
                                segs = event.get("segs", [])
                                t = "".join(s.get("utf8", "") for s in segs).strip()
                                if not t or t == "\n":
                                    continue
                                start_ms = event.get("tStartMs", 0)
                                dur_ms = event.get("dDurationMs", 0)
                                segments.append({
                                    "start": round(start_ms / 1000.0, 1),
                                    "end": round((start_ms + dur_ms) / 1000.0, 1),
                                    "text": t,
                                })
                                full_text_parts.append(t)
                            if full_text_parts:
                                return " ".join(full_text_parts), segments, method_label
                    except Exception:
                        continue

        # Strategy 2: Try HLS VTT manifest (first VTT URL is usually M3U8)
        for sub in en_subs:
            if sub.get("ext") == "vtt" and sub.get("url"):
                try:
                    result = _download_hls_vtt(sub["url"])
                    if result:
                        return result[0], result[1], method_label
                except Exception:
                    continue

    return None, None, None


def _download_hls_vtt(manifest_url: str) -> tuple[str, list | None] | None:
    """Download VTT subtitles from an HLS M3U8 manifest."""
    try:
        req = urllib.request.Request(manifest_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            manifest = resp.read().decode("utf-8")
    except Exception:
        return None

    if "#EXTM3U" not in manifest:
        # Not a manifest — maybe direct VTT content
        if "WEBVTT" in manifest or "-->" in manifest:
            return _parse_vtt(manifest)
        return None

    # Extract segment URLs from M3U8
    segment_urls = []
    for line in manifest.split("\n"):
        line = line.strip()
        if line and not line.startswith("#"):
            segment_urls.append(line)

    if not segment_urls:
        return None

    # Download each segment and concatenate
    all_vtt = []
    for seg_url in segment_urls:
        try:
            req = urllib.request.Request(seg_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                vtt = resp.read().decode("utf-8")
                all_vtt.append(vtt)
        except Exception:
            continue

    if not all_vtt:
        return None

    combined = "\n".join(all_vtt)
    return _parse_vtt(combined)


def _parse_vtt(vtt_text: str) -> tuple[str, list] | None:
    """Parse VTT subtitle text into full text and segments."""
    segments = []
    full_text_parts = []

    lines = vtt_text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        # Look for timestamp lines: 00:00:00.000 --> 00:00:05.000
        if "-->" in line:
            parts = line.split("-->")
            start_str = parts[0].strip().split(" ")[0]  # Remove positioning
            end_str = parts[1].strip().split(" ")[0]
            start_s = _parse_vtt_time(start_str)
            end_s = _parse_vtt_time(end_str)

            # Collect text lines until empty line
            text_lines = []
            i += 1
            while i < len(lines) and lines[i].strip():
                text = lines[i].strip()
                # Remove VTT formatting tags
                text = re.sub(r"<[^>]+>", "", text)
                if text:
                    text_lines.append(text)
                i += 1

            if text_lines:
                text = " ".join(text_lines)
                # Skip duplicate lines (VTT often has overlapping segments)
                if not full_text_parts or text != full_text_parts[-1]:
                    segments.append({
                        "start": round(start_s, 1),
                        "end": round(end_s, 1),
                        "text": text,
                    })
                    full_text_parts.append(text)
        else:
            i += 1

    if not full_text_parts:
        return None

    return " ".join(full_text_parts), segments


def _parse_vtt_time(time_str: str) -> float:
    """Parse VTT timestamp (HH:MM:SS.mmm or MM:SS.mmm) to seconds."""
    parts = time_str.split(":")
    try:
        if len(parts) == 3:
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + float(s)
        elif len(parts) == 2:
            m, s = parts
            return int(m) * 60 + float(s)
    except (ValueError, IndexError):
        pass
    return 0.0


def get_transcript_api(video_id: str) -> tuple[str | None, list | None, str | None]:
    """Get transcript using youtube_transcript_api (v1.0+ .fetch() API)."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return None, None, None

    try:
        # v1.0+ replaced classmethod get_transcript() with instance .fetch();
        # .to_raw_data() returns the dict format the loop below expects.
        transcript_list = YouTubeTranscriptApi().fetch(video_id).to_raw_data()
        segments = []
        full_text_parts = []
        for entry in transcript_list:
            text = entry.get("text", "").strip()
            if not text:
                continue
            start = entry.get("start", 0)
            duration = entry.get("duration", 0)
            segments.append({
                "start": round(start, 1),
                "end": round(start + duration, 1),
                "text": text,
            })
            full_text_parts.append(text)
        return " ".join(full_text_parts), segments, "youtube-transcript-api"
    except Exception:
        return None, None, None


def fetch_youtube(url: str) -> dict:
    """Fetch and parse YouTube video data."""
    video_id = extract_video_id(url)
    if not video_id:
        return {"error": f"Could not extract video ID from URL: {url}"}

    # --- Metadata ---
    meta = get_metadata_ytdlp(url)
    oembed = None
    if not meta:
        oembed = get_metadata_oembed(url)

    if meta:
        title = meta.get("title", "")
        channel = meta.get("channel", meta.get("uploader", ""))
        upload_date_raw = meta.get("upload_date", "")
        if upload_date_raw and len(upload_date_raw) == 8:
            upload_date = f"{upload_date_raw[:4]}-{upload_date_raw[4:6]}-{upload_date_raw[6:8]}"
        else:
            upload_date = upload_date_raw
        duration_seconds = meta.get("duration", 0)
        duration_str = format_duration(duration_seconds) if duration_seconds else ""
        view_count = meta.get("view_count", 0)
        like_count = meta.get("like_count", 0)
        description = meta.get("description", "")
        tags = meta.get("tags", []) or []
        thumbnail_url = meta.get("thumbnail", "")
    elif oembed:
        title = oembed.get("title", "")
        channel = oembed.get("author_name", "")
        upload_date = ""
        duration_seconds = 0
        duration_str = ""
        view_count = 0
        like_count = 0
        description = ""
        tags = []
        thumbnail_url = oembed.get("thumbnail_url", "")
    else:
        return {"error": "Could not fetch video metadata. Ensure yt-dlp is installed."}

    # --- Transcript ---
    transcript_text, transcript_segments, transcript_method = get_transcript_ytdlp(url, video_id, meta)
    if not transcript_text:
        transcript_text, transcript_segments, transcript_method = get_transcript_api(video_id)

    transcript_available = transcript_text is not None

    # --- URLs from description ---
    extracted_urls = []
    desc_urls = extract_urls(description)
    for u in desc_urls:
        extracted_urls.append({
            "url": u,
            "context": "from video description",
            "source": "description",
        })

    return {
        "source_type": "youtube",
        "url": url,
        "video_id": video_id,
        "title": title,
        "channel": channel,
        "upload_date": upload_date,
        "duration": duration_str,
        "duration_seconds": int(duration_seconds) if duration_seconds else 0,
        "view_count": view_count,
        "like_count": like_count,
        "description": description,
        "tags": tags,
        "thumbnail_url": thumbnail_url,
        "transcript": transcript_text or "",
        "transcript_segments": transcript_segments or [],
        "extracted_urls": extracted_urls,
        "metadata": {
            "fetch_date": datetime.now(tz=timezone.utc).strftime("%Y-%m-%d"),
            "transcript_available": transcript_available,
            "transcript_method": transcript_method or "none",
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Extract YouTube video transcript and metadata")
    parser.add_argument("url", help="YouTube video URL")
    args = parser.parse_args()

    result = fetch_youtube(args.url)
    if "error" in result:
        json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
        sys.exit(1)

    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
