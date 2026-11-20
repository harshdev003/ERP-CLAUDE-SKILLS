#!/usr/bin/env python3
"""
fetch_transcript.py — Fetches a YouTube video's transcript and metadata,
prints raw JSON to stdout.

Used by the summarize-youtube-to-notion skill. Claude does all composition
and Notion page creation.

Usage:
    python3 fetch_transcript.py <youtube_url>

Dependencies:
    uv pip install --python ~/.claude/python-env/bin/python youtube-transcript-api requests

No API keys required. Uses:
    - YouTube's public oEmbed endpoint for title/author/thumbnail
    - youtube-transcript-api for captions (auto-generated or creator-uploaded)

Output JSON shape:
    {
      "url": "https://www.youtube.com/watch?v=...",
      "video_id": "...",
      "title": "...",
      "author": "...",
      "thumbnail": "...",
      "duration_seconds": null,
      "transcript": [{"timestamp": "00:01:23", "seconds": 83.0, "text": "..."}],
      "transcript_language": "en",
      "transcript_is_auto_generated": true,
      "errors": []
    }

Exit codes:
    0 — script ran (may still have soft errors in `errors`)
    1 — hard failure (bad URL, missing deps)
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any
from urllib.parse import parse_qs, urlparse


def die(errors: list[str]) -> int:
    print(json.dumps({"errors": errors}))
    return 1


try:
    import requests
except ImportError:
    sys.exit(die(["Python 'requests' is not installed. Run: uv pip install --python ~/.claude/python-env/bin/python requests"]))

try:
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api._errors import (
        TranscriptsDisabled,
        NoTranscriptFound,
        VideoUnavailable,
    )
except ImportError:
    sys.exit(die([
        "Python 'youtube-transcript-api' is not installed. "
        "Run: uv pip install --python ~/.claude/python-env/bin/python youtube-transcript-api"
    ]))


YOUTUBE_OEMBED = "https://www.youtube.com/oembed"


def extract_video_id(url: str) -> str | None:
    """Pull the 11-character video id out of any YouTube URL shape."""
    u = urlparse(url)
    host = (u.hostname or "").lower()
    if host.endswith("youtu.be"):
        vid = u.path.lstrip("/").split("/")[0]
        return vid if re.fullmatch(r"[A-Za-z0-9_-]{11}", vid) else None
    if host.endswith("youtube.com"):
        if u.path == "/watch":
            vid = parse_qs(u.query).get("v", [None])[0]
            if vid and re.fullmatch(r"[A-Za-z0-9_-]{11}", vid):
                return vid
        m = re.match(r"^/(shorts|embed|v)/([A-Za-z0-9_-]{11})", u.path)
        if m:
            return m.group(2)
    return None


def canonical_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def fetch_oembed(url: str) -> dict[str, Any]:
    """Fetch title/author/thumbnail from YouTube's public oEmbed endpoint."""
    try:
        r = requests.get(YOUTUBE_OEMBED, params={"url": url, "format": "json"}, timeout=15)
        if r.status_code == 401:
            return {"_error": "private_or_restricted"}
        if r.status_code == 404:
            return {"_error": "not_found"}
        r.raise_for_status()
        data = r.json()
        return {
            "title": data.get("title"),
            "author": data.get("author_name"),
            "thumbnail": data.get("thumbnail_url"),
        }
    except requests.RequestException as e:
        return {"_error": f"oembed_request_failed: {e}"}


def seconds_to_timestamp(seconds: float) -> str:
    """Convert seconds → HH:MM:SS or MM:SS."""
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def fetch_transcript(video_id: str) -> dict[str, Any]:
    """
    Try to fetch the best available transcript using youtube-transcript-api v1.x.

    Preference order:
      1. Manually uploaded English transcript
      2. Any manually uploaded transcript, auto-translated to English
      3. Auto-generated English transcript
      4. Auto-generated in any language, translated to English if possible
    """
    yt = YouTubeTranscriptApi()

    try:
        transcript_list = yt.list(video_id)
    except TranscriptsDisabled:
        return {"_error": "transcripts_disabled_on_video"}
    except VideoUnavailable:
        return {"_error": "video_unavailable"}
    except Exception as e:
        return {"_error": f"transcript_list_failed: {type(e).__name__}: {e}"}

    picked = None
    is_auto = False
    was_translated = False

    # 1. Manual English
    try:
        picked = transcript_list.find_manually_created_transcript(["en", "en-US", "en-GB"])
    except Exception:
        pass

    # 2. Any manual transcript, translate to English if possible
    if picked is None:
        for t in transcript_list:
            if not t.is_generated:
                picked = t
                if t.language_code.lower() != "en" and t.is_translatable:
                    try:
                        picked = t.translate("en")
                        was_translated = True
                    except Exception:
                        pass
                break

    # 3. Auto-generated English
    if picked is None:
        try:
            picked = transcript_list.find_generated_transcript(["en", "en-US", "en-GB"])
            is_auto = True
        except Exception:
            pass

    # 4. Any auto-generated, translate if possible
    if picked is None:
        for t in transcript_list:
            if t.is_generated:
                picked = t
                is_auto = True
                if t.language_code.lower() != "en" and t.is_translatable:
                    try:
                        picked = t.translate("en")
                        was_translated = True
                    except Exception:
                        pass
                break

    if picked is None:
        return {"_error": "no_transcript_available"}

    try:
        fetched = picked.fetch()
    except Exception as e:
        return {"_error": f"transcript_fetch_failed: {type(e).__name__}: {e}"}

    # v1.x: fetched is a FetchedTranscript with .snippets, each snippet has .text, .start, .duration
    segments: list[dict[str, Any]] = []
    for snip in fetched:
        text = getattr(snip, "text", "") or ""
        start = getattr(snip, "start", 0.0) or 0.0
        text = text.replace("\n", " ").strip()
        if text:
            segments.append({
                "timestamp": seconds_to_timestamp(float(start)),
                "seconds": float(start),
                "text": text,
            })

    language = getattr(fetched, "language_code", None) or getattr(picked, "language_code", None)

    return {
        "segments": segments,
        "language": language,
        "is_auto_generated": is_auto,
        "was_translated": was_translated,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) != 2:
        return die(["usage: fetch_transcript.py <youtube_url>"])

    raw_url = sys.argv[1].strip()
    video_id = extract_video_id(raw_url)
    if not video_id:
        return die([f"could_not_extract_video_id: {raw_url}"])

    url = canonical_url(video_id)
    errors: list[str] = []

    oembed = fetch_oembed(url)
    if "_error" in oembed:
        errors.append(f"oembed:{oembed['_error']}")
        oembed = {"title": None, "author": None, "thumbnail": None}

    transcript = fetch_transcript(video_id)
    if "_error" in transcript:
        errors.append(f"transcript:{transcript['_error']}")
        segments: list[dict[str, Any]] = []
        language = None
        is_auto = None
    else:
        segments = transcript["segments"]
        language = transcript["language"]
        is_auto = transcript["is_auto_generated"]
        if transcript.get("was_translated"):
            errors.append("transcript:auto_translated_to_english")

    output = {
        "url": url,
        "video_id": video_id,
        "title": oembed.get("title"),
        "author": oembed.get("author"),
        "thumbnail": oembed.get("thumbnail") or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        "duration_seconds": None,  # not provided by oEmbed; leave null
        "transcript": segments,
        "transcript_language": language,
        "transcript_is_auto_generated": is_auto,
        "errors": errors,
    }

    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
