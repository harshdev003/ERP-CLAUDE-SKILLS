#!/usr/bin/env python3
"""Fetch Reddit posts and comments that may mention Aura or Aurie.

Canonical skill: marketing:harvest-social-mentions

Usage:
    python3 fetch_mentions.py [--since 30d] [--queries Q1,Q2] [--archive PATH | --no-archive]
                              [--delay 5] [--out PATH]

Reddit is read through PullPush (api.pullpush.io), a public Reddit archive that needs
no credentials. It is unofficial, rate-limits hard, and runs a few days behind
Reddit, so every query reports its status and newest date.

"Aura" is a common word: the Aura photo frame, Oura ring, Asus Aura, identity-theft
Aura and spiritual "aura" all match. The script tags each hit with `prefilter`
(a cheap regex for meditation/sleep/our domain context) instead of dropping
anything; relevance is decided by the skill on read, with counts reported.

Every hit is appended to a local JSONL archive (dedup on source + id). Author names
are never stored or emitted.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PULLPUSH = "https://api.pullpush.io/reddit/search"
DEFAULT_QUERIES = [
    '"aura app"',
    '"aura meditation"',
    '"aura health"',
    "aurahealth",
    "aurie",
]
PAGE_SIZE = 100
MAX_PAGES = 5  # per query x kind; 500 hits is far above a month's volume
RETRIES = 3
DEFAULT_ARCHIVE = Path(
    os.environ.get("SOCIAL_MENTIONS_DIR", Path.home() / ".cache" / "social-mentions")
) / "mentions.jsonl"

# Context that makes an "aura"/"aurie" hit plausibly ours. Tag only — never a drop.
PREFILTER = re.compile(
    r"aurahealth|aura\s*health|\baura app\b"
    r"|\baurie\b.{0,60}\b(app|ai|therap\w*|chat\w*|voice|coach\w*|emotion\w*)"
    r"|\b(app|ai|therap\w*|chat\w*|voice|coach\w*|emotion\w*)\b.{0,60}\baurie\b"
    r"|\baura\b.{0,80}\b(meditat\w*|sleep|hypno\w*|anxiety|stress|hrv|relax\w*|mindful\w*|headspace|calm app|insight timer|subscription|coach\w*|track)"
    r"|\b(meditat\w*|sleep|hypno\w*|anxiety|stress|relax\w*|mindful\w*|headspace|insight timer)\b.{0,80}\baura\b",
    re.I | re.S,
)


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "aura-harvest-social-mentions/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def fetch_pullpush(kind: str, query: str, after: int, delay: float) -> tuple[list[dict], str | None]:
    """Page backwards from now to `after`. Returns (rows, error); rows survive an error."""
    rows: list[dict] = []
    before = None
    for _ in range(MAX_PAGES):
        params = {"q": query, "size": PAGE_SIZE, "after": after,
                  "sort": "desc", "sort_type": "created_utc"}
        if before:
            params["before"] = before
        url = f"{PULLPUSH}/{kind}/?" + urllib.parse.urlencode(params)
        data, err = None, None
        for attempt in range(RETRIES):
            try:
                data = _get_json(url).get("data") or []
                break
            except urllib.error.HTTPError as exc:
                err = f"HTTP {exc.code}"
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                wait = float(retry_after) if retry_after and retry_after.isdigit() else 20 * 2 ** attempt
            except Exception as exc:  # noqa: BLE001
                err = f"{type(exc).__name__}: {exc}"
                wait = 20 * 2 ** attempt
            if attempt < RETRIES - 1:
                _sleep(wait)
        if data is None:
            return rows, err
        rows.extend(data)
        if len(data) < PAGE_SIZE:
            break
        before = int(min(x["created_utc"] for x in data))
        _sleep(delay)
    return rows, None


def normalize(kind: str, query: str, x: dict) -> dict:
    title = x.get("title") or ""
    text = x.get("selftext") or x.get("body") or ""
    permalink = x.get("permalink") or ""
    # A link submission's target lives in `url`, not the body; an aurahealth.io
    # track share often has no text at all.
    link = (x.get("url") or "") if kind == "submission" else ""
    if "reddit.com" in link or link.startswith("/"):
        link = ""
    return {
        "source": "reddit",
        "kind": kind,
        "id": f"{'t3' if kind == 'submission' else 't1'}_{x['id']}",
        "date": datetime.fromtimestamp(float(x["created_utc"]), tz=timezone.utc).date().isoformat(),
        "community": x.get("subreddit") or "",
        "url": f"https://www.reddit.com{permalink}" if permalink.startswith("/") else permalink,
        "title": title,
        "text": text,
        "link": link,
        "score": int(x.get("score") or 0),
        "queries": [query],
        "prefilter": bool(PREFILTER.search(f"{title}\n{text}\n{link}")),
    }


def parse_since(value: str, today: date) -> date:
    m = re.fullmatch(r"(\d+)d", value)
    if m:
        return today - timedelta(days=int(m.group(1)))
    return date.fromisoformat(value)


def load_archive(path: Path) -> dict[tuple[str, str], dict]:
    rows: dict[tuple[str, str], dict] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                rows[(r["source"], r["id"])] = r
    return rows


def append_archive(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def run(args: argparse.Namespace, today: date) -> dict:
    since = parse_since(args.since, today)
    after = int(datetime(since.year, since.month, since.day, tzinfo=timezone.utc).timestamp())
    queries = [q.strip() for q in args.queries.split(",") if q.strip()]

    sources: list[dict] = []
    fetched: dict[tuple[str, str], dict] = {}
    first = True
    for query in queries:
        for kind in ("submission", "comment"):
            if not first:
                _sleep(args.delay)
            first = False
            raw, err = fetch_pullpush(kind, query, after, args.delay)
            src = {"source": "reddit", "kind": kind, "query": query, "fetched": len(raw)}
            if raw:
                src["newest"] = max(normalize(kind, query, x)["date"] for x in raw)
            src["status"] = ("partial" if raw else "error") if err else "ok"
            if err:
                src["error"] = err
            sources.append(src)
            for x in raw:
                r = normalize(kind, query, x)
                key = (r["source"], r["id"])
                if key in fetched:
                    if query not in fetched[key]["queries"]:
                        fetched[key]["queries"].append(query)
                else:
                    fetched[key] = r

    archive_path = None if args.no_archive else Path(args.archive)
    archived = load_archive(archive_path) if archive_path else {}
    new = [r for k, r in fetched.items() if k not in archived]
    if archive_path and new:
        append_archive(archive_path, new)
    merged = {**archived, **fetched}

    in_window = sorted(
        (r for r in merged.values() if r["date"] >= since.isoformat()),
        key=lambda r: (r["date"], r["id"]),
        reverse=True,
    )
    newest = max((s["newest"] for s in sources if s.get("newest")), default=None)
    return {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "since": since.isoformat(),
            "queries": queries,
            "archive": str(archive_path) if archive_path else None,
            # PullPush lags Reddit; the digest names how far behind the data stops.
            "newest_seen": newest,
            "counts": {
                "fetched_unique": len(fetched),
                "new_to_archive": len(new) if archive_path else None,
                "in_window": len(in_window),
                "prefilter_pass": sum(1 for r in in_window if r["prefilter"]),
                "prefilter_fail": sum(1 for r in in_window if not r["prefilter"]),
                "sources_error": sum(1 for s in sources if s["status"] in ("error", "partial")),
            },
            "by_community": dict(Counter(r["community"] for r in in_window if r["prefilter"]).most_common(15)),
            "sources": sources,
        },
        "mentions": in_window,
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Fetch Reddit mentions of Aura / Aurie via PullPush.")
    ap.add_argument("--since", default="30d", help="Nd or YYYY-MM-DD (default 30d)")
    ap.add_argument("--queries", default=",".join(DEFAULT_QUERIES))
    ap.add_argument("--archive", default=str(DEFAULT_ARCHIVE))
    ap.add_argument("--no-archive", action="store_true", help="Don't read or write the archive")
    ap.add_argument("--delay", type=float, default=5.0, help="Seconds between requests (default 5)")
    ap.add_argument("--out", help="Write JSON here instead of stdout")
    args = ap.parse_args()

    result = run(args, datetime.now(timezone.utc).date())
    text = json.dumps(result, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        c = result["meta"]["counts"]
        archived = ("archive off" if c["new_to_archive"] is None
                    else f"{c['new_to_archive']} new to archive")
        print(f"wrote {args.out}: {c['in_window']} in window "
              f"({c['prefilter_pass']} pass prefilter), {archived}, "
              f"{c['sources_error']} source errors, newest {result['meta']['newest_seen']}")
    else:
        print(text)


if __name__ == "__main__":
    main()
