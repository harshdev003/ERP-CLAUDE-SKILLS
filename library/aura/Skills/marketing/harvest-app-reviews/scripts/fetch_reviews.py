#!/usr/bin/env python3
"""Fetch App Store + Google Play reviews for Aura and Aurie.

Canonical skill: marketing:harvest-app-reviews

Usage:
    python3 fetch_reviews.py [--since 30d] [--apps aura,aurie] [--stores ios,play]
                             [--countries us,gb,ca,au] [--archive PATH | --no-archive]
                             [--out PATH]

No credentials. iOS reads Apple's public customer-reviews RSS feed (newest 500 per
app per country). Google Play goes through the `google-play-scraper` package, which
scrapes the public store page; when it isn't installed Play is reported as
`unavailable`, never silently skipped.

Every fetched review is appended to a local JSONL archive (dedup on store + id), so
history accumulates past the per-source 500 cap. The window is selected from the
archive merged with this run's fetch.

Reviewer names and avatars are never stored or emitted.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

APPS = {
    "aura": {"ios": 1114223104, "play": "com.aurahealth"},
    "aurie": {"ios": 6748324270, "play": None},  # Aurie is not on Google Play
}
DEFAULT_COUNTRIES = ["us", "gb", "ca", "au"]
IOS_MAX_PAGES = 10  # Apple serves 10 pages x 50 reviews, then stops
PLAY_CAP = 500
DEFAULT_ARCHIVE = Path(
    os.environ.get("APP_REVIEWS_DIR", Path.home() / ".cache" / "app-reviews")
) / "reviews.jsonl"
LOW_STAR = 2
# Support replies greet reviewers by first name ("Hi Claire, ..."): 22 of 492 Play
# replies on 2026-10-15, every one in this shape. The name is the reviewer's, so it
# is redacted like the reviewer's username.
GREETING_NAME = re.compile(
    r"\b((?i:hi|hello|hey|dear))\s+(?!(?i:there|again|all|everyone|friend)\b)"
    r"[A-Z][\w'’-]*(?:\s+[A-Z][\w'’-]*)?(?=\s*[,!.:;])"
)


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "aura-harvest-app-reviews/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _play_reviews(package: str, country: str, count: int) -> list[dict]:
    from google_play_scraper import Sort, reviews  # optional dependency

    rows, _ = reviews(package, lang="en", country=country, sort=Sort.NEWEST, count=count)
    return rows


def _ios_urls(country: str, app_id: int, page: int) -> list[str]:
    # Apple's CDN intermittently caches an empty feed for one spelling of a URL while
    # the others still serve reviews (observed 2026-10-15: 0 vs 48 for Aurie US).
    # An empty page is only believed once every spelling agrees.
    base = "https://itunes.apple.com"
    path = f"rss/customerreviews/page={page}/id={app_id}"
    return [
        f"{base}/{country}/{path}/sortby=mostrecent/json",
        f"{base}/{path}/sortby=mostrecent/json?cc={country}",
        f"{base}/{country}/{path}/sortBy=mostRecent/json",
    ]


def _ios_page(country: str, app_id: int, page: int) -> tuple[list[dict], str | None]:
    last_err = None
    for url in _ios_urls(country, app_id, page):
        try:
            feed = _get_json(url).get("feed", {})
        except Exception as exc:  # noqa: BLE001
            last_err = f"{type(exc).__name__}: {exc}"
            continue
        entries = feed.get("entry") or []
        if isinstance(entries, dict):  # a single-review page is an object, not a list
            entries = [entries]
        entries = [e for e in entries if "im:rating" in e]  # page 1 may lead with app metadata
        if entries:
            return entries, None
        last_err = None  # a clean empty answer
    return [], last_err


def fetch_ios(app: str, app_id: int, country: str) -> tuple[list[dict], str | None]:
    """Return (reviews, error). An error with rows means the fetch stopped part-way."""
    out: list[dict] = []
    for page in range(1, IOS_MAX_PAGES + 1):
        entries, err = _ios_page(country, app_id, page)
        if err:
            # Past the last review Apple answers 200 with an empty feed, so a raised
            # error on any page is a real failure. Keep what arrived, report the gap.
            return out, f"page {page}: {err}"
        if not entries:
            break
        for e in entries:
            out.append({
                "store": "ios",
                "app": app,
                "country": country,
                "id": e["id"]["label"],
                "date": e["updated"]["label"][:10],
                "rating": int(e["im:rating"]["label"]),
                "version": (e.get("im:version") or {}).get("label", ""),
                "title": (e.get("title") or {}).get("label", ""),
                "text": (e.get("content") or {}).get("label", ""),
                "helpful": int((e.get("im:voteSum") or {}).get("label") or 0),
                "reply": "",
            })
    return out, None


def redact_reply(text: str) -> str:
    return GREETING_NAME.sub(r"\1 [name]", text or "")


def fetch_play(app: str, package: str, country: str) -> tuple[list[dict], str | None]:
    try:
        rows = _play_reviews(package, country, PLAY_CAP)
    except ImportError:
        return [], "unavailable: pip install google-play-scraper"
    except Exception as exc:  # noqa: BLE001
        return [], f"{type(exc).__name__}: {exc}"
    out = []
    for r in rows:
        at = r.get("at")
        out.append({
            "store": "play",
            "app": app,
            "country": country,
            "id": r["reviewId"],
            "date": at.date().isoformat() if hasattr(at, "date") else str(at)[:10],
            "rating": int(r.get("score") or 0),
            "version": r.get("reviewCreatedVersion") or r.get("appVersion") or "",
            "title": "",
            "text": r.get("content") or "",
            "helpful": int(r.get("thumbsUpCount") or 0),
            "reply": redact_reply(r.get("replyContent")),
        })
    return out, None


def parse_since(value: str, today: date) -> date:
    m = re.fullmatch(r"(\d+)d", value)
    if m:
        return today - timedelta(days=int(m.group(1)))
    return date.fromisoformat(value)


def load_archive(path: Path) -> dict[tuple[str, str], dict]:
    rows: dict[tuple[str, str], dict] = {}
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            rows[(r["store"], r["id"])] = r
    return rows


def append_archive(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def stats(reviews: list[dict]) -> dict:
    by_app: dict[str, dict] = {}
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in reviews:
        groups[f"{r['app']}/{r['store']}"].append(r)
    for key, rs in sorted(groups.items()):
        ratings = Counter(r["rating"] for r in rs)
        versions: dict[str, dict] = defaultdict(lambda: {"n": 0, "low_star": 0})
        for r in rs:
            v = versions[r["version"] or "unknown"]
            v["n"] += 1
            v["low_star"] += r["rating"] <= LOW_STAR
        by_app[key] = {
            "n": len(rs),
            "avg_rating": round(sum(r["rating"] for r in rs) / len(rs), 2),
            "ratings": {str(k): ratings.get(k, 0) for k in range(1, 6)},
            "low_star": sum(1 for r in rs if r["rating"] <= LOW_STAR),
            "by_version": dict(sorted(versions.items(), key=lambda kv: -kv[1]["n"])),
        }
    return by_app


def run(args: argparse.Namespace, today: date) -> dict:
    since = parse_since(args.since, today)
    apps = [a.strip() for a in args.apps.split(",") if a.strip()]
    stores = [s.strip() for s in args.stores.split(",") if s.strip()]
    countries = [c.strip().lower() for c in args.countries.split(",") if c.strip()]
    unknown = [a for a in apps if a not in APPS]
    if unknown:
        raise SystemExit(f"unknown app(s): {unknown} — known: {sorted(APPS)}")

    sources: list[dict] = []
    fetched: dict[tuple[str, str], dict] = {}
    for app in apps:
        for store in stores:
            target = APPS[app].get(store)
            if not target:
                sources.append({"app": app, "store": store, "country": "*", "status": "not_listed"})
                continue
            # Play serves reviews by language, not storefront: every country returns
            # the same English set, so one fetch covers it.
            for country in (countries if store == "ios" else countries[:1]):
                if store == "ios":
                    rows, err = fetch_ios(app, target, country)
                    cap = IOS_MAX_PAGES * 50
                else:
                    rows, err = fetch_play(app, target, country)
                    cap = PLAY_CAP
                src = {"app": app, "store": store, "country": country, "fetched": len(rows)}
                if err:
                    src["status"] = "partial" if rows else "error"
                    src["error"] = err
                    if rows:
                        src["oldest"] = min(r["date"] for r in rows)
                else:
                    src["status"] = "ok"
                    if rows:
                        src["oldest"] = min(r["date"] for r in rows)
                    # Hitting the cap with nothing older than `since` means the window
                    # starts before what the store would give us: the gap is invisible
                    # unless the archive already holds it.
                    src["window_truncated"] = bool(
                        len(rows) >= cap and src.get("oldest", "") > since.isoformat()
                    )
                sources.append(src)
                for r in rows:
                    fetched.setdefault((r["store"], r["id"]), r)

    archive_path = None if args.no_archive else Path(args.archive)
    archived = load_archive(archive_path) if archive_path else {}
    new = [r for k, r in fetched.items() if k not in archived]
    if archive_path and new:
        append_archive(archive_path, new)
    merged = {**archived, **fetched}

    # A truncated source is only a gap if the archive doesn't reach back to `since`.
    for src in sources:
        if src.get("window_truncated"):
            older = [r["date"] for r in archived.values()
                     if (r["app"], r["store"], r["country"]) == (src["app"], src["store"], src["country"])]
            src["covered_by_archive"] = bool(older) and min(older) <= since.isoformat()

    # Archives written before redaction existed still hold the raw reply.
    for r in merged.values():
        r["reply"] = redact_reply(r.get("reply"))

    in_window = sorted(
        (r for r in merged.values()
         if r["app"] in apps and r["store"] in stores
         # Play is fetched once, so its rows carry the first requested country.
         and (r["store"] == "play" or r["country"] in countries)
         and r["date"] >= since.isoformat()),
        key=lambda r: (r["date"], r["id"]),
        reverse=True,
    )
    return {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "since": since.isoformat(),
            "apps": apps,
            "stores": stores,
            "countries": countries,
            "archive": str(archive_path) if archive_path else None,
            "counts": {
                "fetched_unique": len(fetched),
                "new_to_archive": len(new) if archive_path else None,
                "archive_total": len(merged) if archive_path else None,
                "in_window": len(in_window),
                "sources_error": sum(1 for s in sources if s["status"] in ("error", "partial")),
                "sources_truncated": sum(
                    1 for s in sources
                    if s.get("window_truncated") and not s.get("covered_by_archive")
                ),
            },
            "sources": sources,
        },
        "stats": stats(in_window),
        "reviews": in_window,
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Fetch App Store + Google Play reviews.")
    ap.add_argument("--since", default="30d", help="Nd or YYYY-MM-DD (default 30d)")
    ap.add_argument("--apps", default="aura,aurie")
    ap.add_argument("--stores", default="ios,play")
    ap.add_argument("--countries", default=",".join(DEFAULT_COUNTRIES))
    ap.add_argument("--archive", default=str(DEFAULT_ARCHIVE))
    ap.add_argument("--no-archive", action="store_true", help="Don't read or write the archive")
    ap.add_argument("--out", help="Write JSON here instead of stdout")
    args = ap.parse_args()

    result = run(args, datetime.now(timezone.utc).date())
    text = json.dumps(result, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        c = result["meta"]["counts"]
        archived = ("archive off" if c["new_to_archive"] is None
                    else f"{c['new_to_archive']} new to archive")
        print(f"wrote {args.out}: {c['in_window']} reviews in window, "
              f"{archived}, {c['sources_error']} source errors, "
              f"{c['sources_truncated']} truncated sources")
    else:
        print(text)


if __name__ == "__main__":
    main()
