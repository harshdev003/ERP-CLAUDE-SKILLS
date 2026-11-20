#!/usr/bin/env python3
"""Project a sim-test knowledge folder into a Notion Screens database.

The repo is the system of record. This script never writes back to it; it makes
Notion match what the repo says, and reports anything it cannot reconcile.

    sync_screens.py check  --app aurie          # read-only, exits non-zero on drift
    sync_screens.py plan   --app aurie          # what apply would do
    sync_screens.py apply  --app aurie          # make it so

Both apps are parameters, not branches: Aurie has a screenshot store, Aura does
not, and a knowledge folder with no `screenshots/` is a supported shape rather
than an error.

Needs NOTION_TOKEN (env, or `env.NOTION_TOKEN` in ~/.claude/settings.json).
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

# Screen docs carry em dashes and arrows; a cp1252 stdout would crash the run.
# At module scope so every entry point is covered, not just main().
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

# The sky variants the Aurie store keeps, in the order a reader should meet them.
# The suffix is the store's key suffix; anytime is the bare key. Aura's axis is
# locale rather than sky, so when it ports this the list changes, not the code.
VARIANTS = (("Anytime", ""), ("Light", "_light"), ("Dark", "_dark"))

# `knowledge_dir` is where the screens live; `database` may be empty until that
# app's database exists, which is the Aura case today.
APPS = {
    "aurie": {
        "repo": "~/Development/Aura/Mobile/AuraConsumerApp",
        "knowledge": "AuraConsumerAppSimTests/knowledge",
        "github": ("https://github.com/AuraHealth/AuraConsumerApp/blob/main/"
                   "AuraConsumerAppSimTests/knowledge/screens/"),
        "database": "3e375e23-af20-8133-8f52-d541815960e0",
    },
    "aura": {
        "repo": "~/Development/Aura/Mobile/Aura",
        "knowledge": "AuraSimTests/knowledge",
        "github": ("https://github.com/AuraHealth/Aura/blob/main/"
                   "AuraSimTests/knowledge/screens/"),
        "database": "3e475e23-af20-81c7-b431-c50444d9a654",
    },
}

# The twelve Domain options, shared verbatim with the Aurie Features Ideas database so
# the two are joinable. Ordered: the first match wins, so specific beats general.
AURIE_DOMAIN_RULES = [
    ("growth/referral", ("referral", "invite", "pmf", "survey", "rating", "appstorerating")),
    ("paywall", ("paywall", "pricing", "package", "subscription", "cancellation",
                 "unsubscribe", "payment", "plans", "limitreached", "usagelimit",
                 "usagewarning", "chatlimit")),
    ("home/feed", ("home", "feed", "fortoday", "checkin", "reflection", "dailypractices",
                   "usecases")),
    ("grounding", ("grounding", "meditation", "player", "prayer", "practice", "track",
                   "breath", "sleep")),
    ("affirmations", ("affirmation", "bookmark")),
    ("journal", ("journal",)),
    ("conversation", ("conversation", "chat", "livesession", "voice", "quickprocessing",
                      "crisis", "incognito", "done")),
    ("onboarding", ("onboarding", "welcome", "signup", "signin", "login", "splash",
                    "agegate", "selfcommitment", "quiz", "valueprops", "didyouknow",
                    "tutorial", "promptresults", "archetype", "supportstyle", "intro")),
    ("notifications", ("notification",)),
    ("profile", ("profile", "about", "account")),
    ("settings", ("settings", "preference", "memory", "language", "theme")),
]

# Aura's own sixteen. NOT the same product, so not the same list: Aurie has no player,
# content library, coaching, live or community surface, and Aura's conversational AI is a
# single embedded area rather than the whole app. Nine names are spelled EXACTLY as Aurie's
# so the two boards stay comparable where the surfaces genuinely correspond; seven are
# Aura's own. Derived from the 133 screen documents in AuraSimTests/knowledge/screens/.
#
# Ordered: first match wins, so specific beats general — `aurie` before `conversation`-ish
# words, `coaching` before `live`, `search` before `content/browse`.
AURA_DOMAIN_RULES = [
    ("growth/referral", ("referral", "invite", "guestpass", "ambassador", "rating",
                         "appstorerating", "share")),
    ("paywall", ("paywall", "pricing", "subscription", "familyplan", "payment", "plans",
                 "freetrial", "manageSubscription".lower(), "starttrial", "changeplan")),
    ("aurie", ("aurie", "aichat", "aiconversation")),
    ("coaching", ("coach", "booking", "session")),
    ("live", ("live", "event", "replay")),
    ("community", ("community", "post", "channel", "userprofile")),
    ("search", ("search", "filterpicker", "filter")),
    ("player", ("player", "track", "breather", "backgroundsound", "downloaded",
                "listenlater", "recent", "playedandcompleted", "saved", "playlist")),
    ("journal", ("journal", "reflection", "mood", "emotion", "weeklyreflection")),
    ("onboarding", ("onboarding", "welcome", "signup", "signin", "login", "splash",
                    "howdidyouhear", "quiz", "personalization")),
    ("notifications", ("notification", "inbox", "message", "push", "email")),
    ("home/feed", ("home", "dashboard", "feed", "today", "qod", "quote", "customize",
                   "viewall", "recommend")),
    ("content/browse", ("program", "series", "masterclass", "podcast", "challenge",
                        "topic", "mfy", "course", "celebrity", "sleep")),
    ("profile", ("profile", "account", "badge", "streak")),
    ("settings", ("settings", "preference", "language", "theme", "widget", "reminder",
                  "privacy", "faq", "moreinfo", "goals")),
    ("other", ()),
]

DOMAIN_RULES_BY_APP = {"aurie": AURIE_DOMAIN_RULES, "aura": AURA_DOMAIN_RULES}

PLACEHOLDER_IDS = {"none", "null", "n/a", "na", "-", ""}


# --------------------------------------------------------------------------- util

# What the run was doing, for the stop line.
STAGE = "start"
_ID = re.compile(r"\b[0-9a-f]{8}(?:-?[0-9a-f]{4}){3}-?[0-9a-f]{12}\b", re.I)


def die(msg: str, code: int = 2):
    sys.stderr.write(msg.rstrip() + "\n")
    # One machine-readable line per stop; an unattended run raises a follow-up for it.
    # Ids are cut to a prefix: the follow-up refuses full ids outside links.
    first = (msg.strip().splitlines() or ["no reason given"])[0]
    print(f"FOLLOWUP stop {STAGE}: {_ID.sub(lambda m: m.group(0)[:8] + '…', first)[:200]}", flush=True)
    raise SystemExit(code)


def token() -> str:
    tok = os.environ.get("NOTION_TOKEN")
    if not tok:
        p = os.path.expanduser("~/.claude/settings.json")
        if os.path.exists(p):
            try:
                tok = json.load(open(p)).get("env", {}).get("NOTION_TOKEN", "")
            except (OSError, ValueError):
                tok = ""
    if not tok:
        die("NOTION_TOKEN is not set (env or ~/.claude/settings.json).")
    return tok


def api(method: str, path: str, body=None, raw=None, content_type=None, retries: int = 4):
    """One Notion call, with backoff on 429 and 5xx."""
    url = path if path.startswith("http") else NOTION_API + path
    for attempt in range(retries):
        headers = {"Authorization": f"Bearer {token()}", "Notion-Version": NOTION_VERSION}
        if raw is None:
            data = json.dumps(body).encode() if body is not None else None
            headers["Content-Type"] = "application/json"
        else:
            data = raw
            if content_type:
                headers["Content-Type"] = content_type
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            payload = e.read().decode()
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            die(f"Notion {method} {path} -> {e.code}\n{payload[:600]}")
        except urllib.error.URLError as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            die(f"Notion {method} {path} -> {e}")
    return {}


def norm(s: str) -> str:
    """Fold a screen name the way sim_screenshots._norm does.

    Doc filenames and store keys disagree -- `welcome` vs `welcome_screen` -- so
    strip non-alphanumerics *and* a trailing `screen`. Without this, 8 of Aurie's
    69 screenshots fail to find their document.
    """
    s = re.sub(r"[^a-z0-9]", "", s.lower())
    return s[:-6] if s.endswith("screen") else s


# ------------------------------------------------------------------------ extract

def section(text: str, *titles: str) -> str:
    lines = text.splitlines()
    out = []
    for i, ln in enumerate(lines):
        low = ln.strip().lower().lstrip("#* ").rstrip("*: ")
        if not any(low.startswith(t.lower()) for t in titles):
            continue
        body = []
        for nxt in lines[i + 1:]:
            s = nxt.strip()
            marker = s.startswith("#") or (s.startswith("**") and s.endswith("**") and len(s) > 4)
            if marker and body:
                break
            body.append(nxt)
        chunk = "\n".join(body).strip()
        if chunk:
            out.append(chunk)
    return "\n\n".join(out).strip()


def clean(s: str) -> str:
    """Normalise markdown. Never truncate.

    Any cut here defeats the chunking in `rt` -- the back half of a trap, which is
    usually the useful half, would be gone before it could be split. An earlier
    9000-char "sanity bound" was live data loss: dailyPractices_forToday's traps
    section is exactly 9000 characters. `rt` enforces the only real limit, which
    is Notion's.
    """
    s = re.sub(r"`{1,3}", "", s)
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def domain_for(name: str, app: str = "aurie") -> str:
    """Which product area a screen belongs to, per the app's own taxonomy.

    The two apps do not share a domain list. Aurie has no player, content library,
    coaching, live or community surface; Aura's conversational AI is one embedded area
    rather than the whole product. Using Aurie's twelve for Aura filed most of the app
    under `other`, which makes the collision gate and the ideation priors useless — both
    group by domain.
    """
    n = re.sub(r"[^a-z0-9]", "", name.lower())
    for dom, keys in DOMAIN_RULES_BY_APP.get(app, AURIE_DOMAIN_RULES):
        if any(k in n for k in keys):
            return dom
    return "other"


def identifier_of(text: str, fallback: str) -> str:
    m = re.search(r"^\s*[-*]\s*\*\*Identifier:?\*\*\s*:?\s*`?([A-Za-z0-9_]+)`?", text, re.M)
    cand = m.group(1) if m else ""
    if cand.lower() in PLACEHOLDER_IDS:
        cand = ""
    if not cand:
        m = re.search(r"accessibilityIdentifier\(\"([A-Za-z0-9_]+)\"\)", text)
        cand = m.group(1) if m else ""
    return cand or fallback


def read_status_buckets(knowledge: str, repo: str):
    """Ask sim_screenshots.py for status. It is the authority, and inference is not.

    Deriving `stale` from the index's `issues` field is wrong: `keyboard` and
    `dimmed` are capture-quality heuristics, not staleness. Doing so once marked
    32 of Aurie's screens stale when 16 are.
    """
    import subprocess
    script = os.path.join(repo, os.path.dirname(knowledge.rstrip("/")), "Scripts",
                          "sim_screenshots.py")
    script = os.path.normpath(script)
    if not os.path.exists(script):
        return None
    try:
        out = subprocess.run(["python3", script, "check"], cwd=repo,
                             capture_output=True, text=True, timeout=300).stdout
    except Exception:
        return None
    buckets, cur = {"stale": set(), "missing": set(), "harvestable": set()}, None
    for line in out.splitlines():
        low = line.strip().lower()
        if low.startswith("stale"):
            cur = "stale"; continue
        if low.startswith("no capture anywhere"):
            cur = "missing"; continue
        if low.startswith("harvestable"):
            cur = "harvestable"; continue
        if not line.strip():
            cur = None; continue
        if cur and line.startswith("  "):
            buckets[cur].add(norm(line.split()[0]))
    return buckets


def extract(app: str, repo: str = ""):
    cfg = APPS[app]
    # `repo` overrides the configured checkout: a scheduled run harvests in its own
    # worktree and syncs from there, never from the main checkout.
    repo = os.path.abspath(os.path.expanduser(repo or cfg["repo"]))
    kdir = os.path.join(repo, cfg["knowledge"])
    sdir = os.path.join(kdir, "screens")
    shots = os.path.join(kdir, "screenshots")
    if not os.path.isdir(sdir):
        die(f"no screens directory at {sdir}")

    index = {}
    if os.path.exists(os.path.join(shots, "index.json")):
        index = json.load(open(os.path.join(shots, "index.json"))).get("screens", {})
    by_norm = {norm(k): (k, v) for k, v in index.items()}
    buckets = read_status_buckets(cfg["knowledge"], repo)

    records = []
    for fn in sorted(os.listdir(sdir)):
        if not fn.endswith(".md") or fn == "README.md":
            continue
        stem = fn[:-3]
        text = open(os.path.join(sdir, fn)).read()
        key, meta = by_norm.get(norm(stem), (None, None))
        # Three pictures per screen, one per sky. The store keys them `<key>`
        # (anytime), `<key>_light` (midday) and `<key>_dark` (night), each with
        # its own index entry and its own sha -- see VARIANTS.
        shots_for = []
        for label, suffix in VARIANTS:
            vkey = f"{key}{suffix}" if key else None
            vmeta = index.get(vkey) if vkey else None
            vpng = os.path.join(shots, f"{vkey}.png") if vkey else None
            if vpng and os.path.exists(vpng):
                shots_for.append({"label": label, "png": vpng, "key": vkey,
                                  "sha": (vmeta or {}).get("sha", ""),
                                  "captured": (vmeta or {}).get("captured", "")})
        png = shots_for[0]["png"] if shots_for else None

        if buckets is not None:
            n = norm(stem)
            status = ("blocked" if n in buckets["missing"]
                      else "stale" if n in buckets["stale"]
                      else "captured" if meta else "blocked")
        else:
            status = "captured" if meta else "blocked"

        records.append({
            "screen": stem,
            "identifier": identifier_of(text, stem),
            "domain": domain_for(stem, app),
            "status": status,
            "how_to_reach": clean(section(
                text, "Advance", "How to reach", "Entry / exit", "Entry/exit",
                "Trigger conditions", "Reaching it")),
            "experiment_gate": clean(section(
                text, "Experiment gate", "Experiment gates", "Which users see it",
                "Experiment")),
            "traps": clean(section(text, "Tips", "Traps", "Known", "Notes", "Gotchas")),
            "blocker": clean(section(text, "Blocker", "Why it is blocked")),
            "png": png,
            "shots": shots_for,
            # One sha over every variant. The anytime sha alone left a row looking
            # clean after a light- or dark-only re-capture, so two thirds of the
            # store could drift while `check` reported "in sync".
            "sha": "+".join(v["sha"] for v in shots_for),
            "captured": max((v["captured"] for v in shots_for if v["captured"]),
                            default=""),
            "issues": ",".join((meta or {}).get("issues") or []),
            "source_doc": cfg["github"] + fn,
        })
    return records


# -------------------------------------------------------------------------- notion

def rt(s: str):
    """Rich text, chunked: Notion rejects any single text node over 2000 chars."""
    s = s or ""
    if not s:
        return []
    # Notion caps a single text node at 2000 characters, and a property at 100
    # nodes. Chunk the whole string; never silently drop the tail.
    return [{"type": "text", "text": {"content": s[i:i + 1900]}}
            for i in range(0, min(len(s), 1900 * 100), 1900)]


def fetch_rows(db: str):
    rows, cursor = {}, None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        d = api("POST", f"/databases/{db}/query", body)
        for r in d.get("results", []):
            title = "".join(t["plain_text"] for t in r["properties"]["Screen"]["title"])
            rows[title] = r
        if not d.get("has_more"):
            return rows
        cursor = d["next_cursor"]


def upload_png(path: str) -> str:
    name = os.path.basename(path)
    ctype = mimetypes.guess_type(name)[0] or "image/png"
    up = api("POST", "/file_uploads", {"filename": name, "content_type": ctype})
    boundary = "----sync-screens-boundary"
    with open(path, "rb") as fh:
        blob = fh.read()
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"{name}\"\r\nContent-Type: {ctype}\r\n\r\n").encode() + blob + \
           f"\r\n--{boundary}--\r\n".encode()
    api("POST", f"/file_uploads/{up['id']}/send", raw=body,
        content_type=f"multipart/form-data; boundary={boundary}")
    return up["id"]


def props_for(rec: dict, upload_ids: list) -> dict:
    p = {
        "Screen": {"title": rt(rec["screen"])},
        "Identifier": {"rich_text": rt(rec["identifier"])},
        "Domain": {"select": {"name": rec["domain"]}},
        "Status": {"select": {"name": rec["status"]}},
        "How to reach": {"rich_text": rt(rec["how_to_reach"])},
        "Experiment gate": {"rich_text": rt(rec["experiment_gate"])},
        "Traps": {"rich_text": rt(rec["traps"])},
        "Blocker": {"rich_text": rt(rec["blocker"])},
        "Capture sha": {"rich_text": rt(rec["sha"])},
        "Capture issues": {"rich_text": rt(rec["issues"])},
        "Source doc": {"url": rec["source_doc"]},
    }
    # Write these unconditionally. Setting them only when present meant a screen
    # that lost its capture kept a stale Notion date and file, so `row_matches`
    # called it dirty on every run while `apply` never cleared them -- `check`
    # exited 1 forever.
    p["Last captured"] = ({"date": {"start": rec["captured"][:10]}} if rec["captured"]
                          else {"date": None})
    if upload_ids:
        p["Screenshot"] = {"files": [
            {"type": "file_upload", "file_upload": {"id": uid},
             "name": f"{rec['screen']}__{v['label'].lower()}.png"}
            for v, uid in zip(rec["shots"], upload_ids)]}
    elif not rec["shots"]:
        p["Screenshot"] = {"files": []}
    return p


def row_sha(row: dict) -> str:
    return "".join(t["plain_text"] for t in row["properties"]["Capture sha"]["rich_text"])


def _text(row: dict, name: str) -> str:
    p = row["properties"].get(name) or {}
    if p.get("type") == "rich_text":
        return "".join(t["plain_text"] for t in p["rich_text"])
    if p.get("type") == "title":
        return "".join(t["plain_text"] for t in p["title"])
    if p.get("type") == "select":
        return (p.get("select") or {}).get("name", "")
    if p.get("type") == "url":
        return p.get("url") or ""
    if p.get("type") == "date":
        # Notion normalises and may return a timestamp; the index stores a plain
        # date. Compare the calendar day or every captured row stays dirty.
        return ((p.get("date") or {}).get("start") or "")[:10]
    return ""


def row_matches(row: dict, rec: dict) -> bool:
    """True when every synced field already equals what the repo says.

    Comparing only `Capture sha` was wrong and hid real drift: a screen that goes
    stale, changes its gate, or gains a trap keeps the same screenshot -- and so
    the same sha -- so Notion kept the old Status and prose forever while `check`
    cheerfully reported "in sync". The sha now decides only whether the *image*
    needs re-uploading; the text decides whether the row needs writing.
    """
    if _text(row, "Identifier") != rec["identifier"]: return False
    if _text(row, "Domain") != rec["domain"]: return False
    if _text(row, "Status") != rec["status"]: return False
    if _text(row, "How to reach") != rec["how_to_reach"]: return False
    if _text(row, "Experiment gate") != rec["experiment_gate"]: return False
    if _text(row, "Traps") != rec["traps"]: return False
    if _text(row, "Blocker") != rec["blocker"]: return False
    if _text(row, "Capture sha") != rec["sha"]: return False
    if _text(row, "Capture issues") != rec["issues"]: return False
    if _text(row, "Source doc") != rec["source_doc"]: return False
    if _text(row, "Last captured") != (rec["captured"] or "")[:10]: return False
    # A screen must carry one file per variant the repo holds. Comparing only
    # "has any file" left every row that predates the light/dark pass looking
    # clean while carrying a single picture out of three.
    files = (row["properties"].get("Screenshot") or {}).get("files") or []
    if len(rec["shots"]) != len(files): return False
    return True


def has_image_block(page_id: str) -> bool:
    d = api("GET", f"/blocks/{page_id}/children?page_size=50")
    return any(b["type"] == "image" for b in d.get("results", []))


def clear_image_blocks(page_id: str):
    """Remove the body image when the repo no longer has a capture.

    Clearing the Screenshot property alone is not enough: the block is the thing a
    reader actually sees, so a screen that lost its capture would still show the
    old picture, and `row_matches` -- which only inspects properties -- would call
    the row clean and never look again.
    """
    d = api("GET", f"/blocks/{page_id}/children?page_size=50")
    for b in d.get("results", []):
        if b["type"] == "image":
            api("DELETE", f"/blocks/{b['id']}")


def set_image_blocks(page_id: str, uploads: list, captions: list):
    """A files property renders as a chip. The body images are what a reader sees.

    One block per sky, captioned, in VARIANTS order -- so the row reads as
    Anytime / Light / Dark rather than as three unlabelled pictures.
    """
    d = api("GET", f"/blocks/{page_id}/children?page_size=50")
    for b in d.get("results", []):
        if b["type"] == "image":
            api("DELETE", f"/blocks/{b['id']}")
    api("PATCH", f"/blocks/{page_id}/children", {"children": [
        {"object": "block", "type": "image",
         "image": {"type": "file_upload", "file_upload": {"id": uid},
                   "caption": rt(cap)}}
        for uid, cap in zip(uploads, captions)]})


# ---------------------------------------------------------------------------- run

def doc_path(row: dict, app: str) -> str:
    """The repo path of the doc a row was synced from, or "" when its Source doc isn't ours."""
    url, cfg = _text(row, "Source doc"), APPS[app]
    return f"{cfg['knowledge']}/screens/{url[len(cfg['github']):]}" if url.startswith(cfg["github"]) else ""


def deleted_on_main(repo: str, path: str) -> str:
    """The commit on origin/main that deleted `path`, or "" unless it is gone from main AND git shows the
    delete. A doc that was recreated, never existed, or can't be checked is never treated as deleted:
    the row then needs a person (see orphan_plan)."""
    if not path:
        return ""
    git = ["git", "-C", repo]
    try:
        if subprocess.run(git + ["cat-file", "-e", f"origin/main:{path}"], capture_output=True).returncode == 0:
            return ""
        out = subprocess.run(git + ["log", "origin/main", "--no-renames", "--diff-filter=D", "-1",
                                    "--format=%h", "--", path], capture_output=True, text=True)
    except OSError:
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def fetch_main(repo: str) -> bool:
    """Bring origin/main up to date. A stale ref that has a delete but not a later recreate would
    otherwise archive a row whose doc is back on main."""
    try:
        return subprocess.run(["git", "-C", repo, "fetch", "-q", "origin", "main"],
                              capture_output=True, timeout=120).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def orphan_plan(orphans: list, rows: dict, app: str, repo: str):
    """Split orphaned rows into (archive, ask): archive only a row whose doc was deleted on main."""
    if not fetch_main(repo):
        return [], [(n, "could not fetch origin/main to confirm the doc was deleted") for n in orphans]
    archive, ask = [], []
    for name in orphans:
        path = doc_path(rows[name], app)
        sha = deleted_on_main(repo, path)
        if sha:
            archive.append((name, sha))
        else:
            ask.append((name, "its Source doc is not this app's repo" if not path
                        else f"{path} is still on main or git shows no delete of it"))
    return archive, ask


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["check", "plan", "apply"])
    ap.add_argument("--app", default="aurie", choices=sorted(APPS))
    ap.add_argument("--database", default="", help="override the app's database id")
    ap.add_argument("--only", default="", help="sync a single screen by name")
    ap.add_argument("--confirm", action="store_true",
                    help="required by `apply`; without it apply behaves as plan")
    ap.add_argument("--repo", default="", help="read this checkout or worktree instead of "
                                              "the app's configured repo")
    ap.add_argument("--keep-orphans", action="store_true",
                    help="apply: leave orphaned rows alone, even ones whose doc was deleted on main")
    args = ap.parse_args()
    repo = os.path.abspath(os.path.expanduser(args.repo or APPS[args.app]["repo"]))

    global STAGE
    STAGE = "read-docs"
    recs = all_recs = extract(args.app, args.repo)
    if args.only:
        recs = [r for r in recs if r["screen"] == args.only]
        if not recs:
            die(f"no screen named {args.only}")

    db = args.database or APPS[args.app]["database"]
    if not db:
        die(f"no database configured for {args.app}. Build it, then set APPS['{args.app}']"
            f"['database'] or pass --database.", 2)

    STAGE = "read-notion"
    rows = fetch_rows(db)
    STAGE = "plan"
    create = [r for r in recs if r["screen"] not in rows]
    update, unchanged = [], []
    for r in recs:
        row = rows.get(r["screen"])
        if not row:
            continue
        (unchanged if row_matches(row, r) else update).append(r)
    # Orphans are computed against the FULL document set. With --only, `recs` is
    # one screen, and diffing against it would report all 75 others as orphaned.
    all_names = {r["screen"] for r in (all_recs if args.only else recs)}
    orphans = sorted(set(rows) - all_names)

    print(f"app={args.app}  docs={len(recs)}  rows={len(rows)}")
    print(f"  create   {len(create)}")
    print(f"  update   {len(update)}")
    print(f"  unchanged{len(unchanged):>3}")
    print(f"  orphaned {len(orphans)}" + (f"  {orphans[:5]}" if orphans else ""))
    # --only syncs one screen: other rows, orphans included, are not this run's to touch.
    archive, ask = orphan_plan(orphans, rows, args.app, repo) if orphans and not args.only else ([], [])
    by_status = {}
    for r in recs:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    print("  status:", by_status)

    if args.mode == "check":
        drift = len(create) + len(update) + len(orphans)
        print(("\nDRIFT: %d" % drift) if drift else "\nin sync")
        raise SystemExit(1 if drift else 0)

    # Print EVERY row the write would touch. Showing the first ten made the
    # preview disagree with the writes, which is worse than showing nothing.
    if args.mode == "plan" or (args.mode == "apply" and not args.confirm):
        for r in create:
            print(f"  + {r['screen']:<36} {r['status']:<9} {r['domain']}")
        for r in update:
            print(f"  ~ {r['screen']:<36} {r['status']:<9} {r['domain']}")
        if not args.keep_orphans:
            for name, sha in archive:
                print(f"  - {name:<36} archive: doc deleted on main in {sha}")
        for name, why in ask:
            print(f"  ? {name:<36} orphaned, needs a decision: {why}")
        if args.mode == "apply":
            print("\nNothing written. Notion writes are user-visible and not cheap to\n"
                  "undo at scale, so `apply` needs --confirm once the plan looks right.")
        raise SystemExit(0)

    STAGE = "write"
    done = 0
    for r in create + update:
        ups = [upload_png(v["png"]) for v in r["shots"]]
        body = props_for(r, ups)
        if r["screen"] in rows:
            page = rows[r["screen"]]
            api("PATCH", f"/pages/{page['id']}", {"properties": body})
            pid = page["id"]
        else:
            pid = api("POST", "/pages",
                      {"parent": {"database_id": db}, "properties": body})["id"]
        if ups:
            caps = [f"{v['label']} — {r['screen']}"
                    + (f", captured {v['captured']}" if v["captured"] else "")
                    for v in r["shots"]]
            set_image_blocks(pid, ups, caps)
        else:
            clear_image_blocks(pid)
        done += 1
        if done % 10 == 0:
            print(f"  ... {done}/{len(create) + len(update)}")
    print(f"\nwrote {done} row(s)")
    if args.keep_orphans:
        ask = [(n, "left alone (--keep-orphans)") for n, _ in archive] + ask
    else:
        STAGE = "archive"
        # Archived, not deleted: the row goes to Notion's trash and can be restored.
        for name, sha in archive:
            api("PATCH", f"/pages/{rows[name]['id']}", {"archived": True})
            print(f"archived {name} (doc deleted on main in {sha}; restorable from Notion's trash)")
    # One line per orphan a person must decide on; an unattended run raises a follow-up for each.
    for name, why in ask:
        print(f"FOLLOWUP orphan {name}: {why}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # anything die() did not catch still ends in a stop line
        die(f"{type(e).__name__}: {e}")
