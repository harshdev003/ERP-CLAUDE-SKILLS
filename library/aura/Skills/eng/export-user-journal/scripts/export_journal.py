#!/usr/bin/env python3
"""Export one user's Aurie journal to a readable PDF or Markdown file.

Reads the journal file eng:query-user-export-data wrote (read-only SQL; there is deliberately
no API route for another user's journal), keeps what the user saw in the app's journal, groups
it by the user's LOCAL day, and prints it to PDF with headless Chrome. Same layout as
eng:export-user-conversations, so the two read as one set.

Read-only: the only call it makes is UserService `GET /users/:userId`, for the timezone.
Verbatim: no LLM reads the content, and internal fields (rating, feedback,
notification, emotions, impacts, ids) are never rendered.
"""

from __future__ import annotations

import argparse
import datetime
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import pathlib
from collections import Counter, OrderedDict
from pathlib import Path
from zoneinfo import ZoneInfo


def _ua_who() -> str:
    """Login name of whoever runs this, for the user agent: AINATIVE_UA_WHO, else the global git email's local part, git user.name, the OS account, or "unknown"."""
    who = re.sub(r"^\d+\+", "", os.environ.get("AINATIVE_UA_WHO", "").strip().partition("@")[0])
    if not who:
        if not hasattr(_ua_who, "found"):
            import getpass, subprocess
            found = ""
            for key in ("user.email", "user.name"):
                try:
                    val = subprocess.run(["git", "config", "--global", key], capture_output=True, encoding="utf-8", errors="replace", timeout=5).stdout.strip()
                except (OSError, subprocess.SubprocessError):
                    val = ""
                found = re.sub(r"^\d+\+", "", val.partition("@")[0]) if key == "user.email" else val
                if found:
                    break
            if not found:
                try:
                    found = getpass.getuser()
                except Exception:
                    found = ""
            _ua_who.found = found
        who = _ua_who.found
    return re.sub(r"[^A-Za-z0-9._-]+", "-", who).strip("-.") or "unknown"


def user_agent() -> str:
    """`AuraClaude/<who>/<plugin>:<skill>@<version> (session <id>)` -- labels this skill's calls to RTDB, GCP and the Aura API.

    Same format as gcloud_sa.user_agent(). A label, not proof: anyone can send it.
    """
    skill = pathlib.Path(__file__).resolve().parents[1]
    root = skill.parents[1]
    try:
        plugin = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["name"]
    except (OSError, ValueError, KeyError, TypeError):
        plugin = root.name
    try:
        front = (skill / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[1]
        version = re.search(r'version:\s*"([^"]+)"', front).group(1)
    except (OSError, IndexError, AttributeError):
        version = "unknown"
    session = os.environ.get("AINATIVE_SESSION_NAME") or (os.environ.get("CLAUDE_CODE_SESSION_ID") or "")[:8]
    session = re.sub(r"[^A-Za-z0-9._-]+", "-", session).strip("-") or "none"
    return f"AuraClaude/{_ua_who()}/{plugin}:{skill.name}@{version} (session {session})"


DEFAULT_TZ = "America/New_York"  # matches the `user`.`timezone` column default
DEFAULT_HOST = "https://services.aurahealth.io"
# The journal entries the app shows, and who wrote each.
SHOWN = {"daily": "Aurie", "onboarding": "Aurie", "user": "You", "affirmation": "Aurie"}
# The label every entry carries, so the reader always knows whose words they are.
AUTHOR = {"You": "Written by you", "Aurie": "Written by Aurie"}
KIND = {"daily": "Daily journal", "onboarding": "First journal", "user": "Your entry", "affirmation": "Saved affirmation"}
# What the app shows for an entry with no title; the export adds it, nothing stores it.
UNTITLED = "Untitled Journal"

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
]


# -------------------------------------------------------------------------- api


FILE_VERSION = 1


def load_rows(path, uid):
    """The journal rows of one user from eng:query-user-export-data's file. Entries the user removed
    in the app are left out (and counted by the caller), as the app shows them. Refuses a file of
    another version, section or user."""
    try:
        data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise SystemExit(f"Could not read the export file ({type(error).__name__}).") from error
    if not isinstance(data, dict) or data.get("version") != FILE_VERSION:
        raise SystemExit(f"The export file is not version {FILE_VERSION}; refusing to guess its shape.")
    if data.get("entity") != "journal":
        raise SystemExit(f"The export file holds {data.get('entity')!r}, not 'journal'.")
    if data.get("userIdPrefix") != uid_prefix(uid):
        raise SystemExit("The export file is for another user (its uid prefix does not match --uid).")
    if not isinstance(data.get("rows"), list):
        raise SystemExit("The export file has no rows list.")
    rows = data["rows"]
    return [r for r in rows if not r.get("removed")], sum(1 for r in rows if r.get("removed"))


class TimezoneError(Exception):
    """The user's timezone could not be read: their days can't be drawn right."""


def fetch_timezone(host, token, uid):
    """The user's `timezone` from UserService `GET /users/<uid>`; only that field is kept.
    None when the profile has none (the column's default applies). Raises TimezoneError when it
    can't be read, so an outage never moves entries onto the wrong days."""
    request = urllib.request.Request(
        f"{host.rstrip('/')}/users/{urllib.parse.quote(uid, safe='')}",
        headers={"Authorization": f"Bearer {token}", "User-Agent": user_agent()})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return (json.loads(response.read().decode("utf-8")) or {}).get("timezone")
    except urllib.error.HTTPError as error:
        raise TimezoneError(f"GET /users/:userId answered {error.code}") from error
    except (urllib.error.URLError, ValueError, OSError) as error:
        raise TimezoneError(f"GET /users/:userId failed: {type(error).__name__}") from error


def resolve_timezone(host, token, uid, override):
    """(ZoneInfo, how it was chosen). Refuses rather than guessing: the days are the user's."""
    if override:
        name, how = override, "--timezone"
    elif not uid:
        raise SystemExit("Exporting the token's own user: pass --timezone (the user's IANA timezone), "
                         "since there is no profile id to read it from.")
    else:
        try:
            name = fetch_timezone(host, token, uid)
        except TimezoneError as error:
            raise SystemExit(f"Could not read the user's timezone ({error}). Retry, or pass --timezone "
                             "if you know it; the export would otherwise put entries on the wrong days.")
        how = "profile"
        if not name:
            name, how = DEFAULT_TZ, "profile has none: the column default"
    try:
        return ZoneInfo(name), how
    except Exception:
        raise SystemExit(f"Unknown timezone {name!r} (from {how}); pass --timezone with an IANA name.")


# -------------------------------------------------------------------- content


def _items(value):
    """Insights or affirmations as plain dicts or strings, the shapes the app decodes."""
    out = []
    for item in value or []:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
        elif isinstance(item, dict):
            fields = {k: str(v).strip() for k, v in item.items() if isinstance(v, (str, int, float)) and str(v).strip()}
            if fields:
                out.append(fields)
    return out


def entry(row):
    """What the app shows for one journal row, or None for a type this export leaves out."""
    kind = row.get("type")
    if kind not in SHOWN:
        return None
    if kind == "affirmation":
        text = (row.get("title") or "").strip()
        if not text:
            first = _items(row.get("affirmations"))[:1]
            text = first[0] if first and isinstance(first[0], str) else ""
        return {"kind": kind, "by": SHOWN[kind], "title": "", "body": text, "insights": [], "affirmations": [],
                "rating": row.get("rating"), "feedback": row.get("feedback")}
    body = row.get("notes") if kind == "user" else row.get("summary")
    return {"kind": kind, "by": SHOWN[kind], "rating": row.get("rating"), "feedback": row.get("feedback"),
            "title": (row.get("title") or "").strip() or UNTITLED,
            "body": (body or "").strip(), "insights": _items(row.get("insights")),
            "affirmations": [a for a in _items(row.get("affirmations")) if isinstance(a, str)]}


def prepare(rows, tz):
    """(days, stats): shown entries by local day, oldest first, plus counts. A row whose type
    the export leaves out is counted per type, never silently dropped."""
    shown, left_out = [], Counter()
    for row in rows:
        e = entry(row)
        if e is None:
            left_out[row.get("type") or "(no type)"] += 1
            continue
        e["dt"] = datetime.datetime.fromisoformat(str(row["createdAt"]).replace("Z", "+00:00")).astimezone(tz)
        shown.append(e)
    shown.sort(key=lambda e: e["dt"])
    days = OrderedDict()
    for e in shown:
        days.setdefault(e["dt"].date(), []).append(e)
    stats = {"rows": len(rows), "entries": len(shown), "days": len(days),
             "by_kind": dict(Counter(e["kind"] for e in shown)), "left_out": dict(left_out),
             "first": min(days) if days else None, "last": max(days) if days else None}
    return days, stats


# ---------------------------------------------------------------------- render


def fmt_md(text: str) -> str:
    """Escape, then apply the small subset of Markdown Aurie actually emits."""
    t = html.escape(text.strip())
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t, flags=re.S)
    t = re.sub(r"(?<![\*\w])\*(?!\s)(.+?)(?<!\s)\*(?![\*\w])", r"<em>\1</em>", t, flags=re.S)
    paras = [p.strip() for p in re.split(r"\n\s*\n", t) if p.strip()]
    return "".join("<p>%s</p>" % p.replace("\n", "<br>") for p in paras)


CSS = """
@page { size: A4; margin: 18mm 16mm 18mm;
  @top-left { font-family: -apple-system, "Segoe UI", Inter, Helvetica, Arial, sans-serif; font-size: 8pt; color: #5f5a6b; }
  @bottom-center { content: "Page " counter(page) " of " counter(pages);
    font-family: -apple-system, "Segoe UI", Inter, Helvetica, Arial, sans-serif; font-size: 8pt; color: #5f5a6b; } }
@page:first { @top-left { content: none; } @bottom-center { content: none; } }
* { box-sizing: border-box; }
body { margin:0; font-family: -apple-system, "Segoe UI", Inter, Helvetica, Arial, sans-serif;
  color:#24212c; background:#fff; font-size:10pt; line-height:1.4; }
.cover { padding-top:52mm; page-break-after:always; }
.mark { width:52px; height:52px; border-radius:16px;
  background:linear-gradient(140deg,#8b7ec8,#c9a5c9); margin-bottom:24px; }
h1 { font-size:30pt; font-weight:600; margin:0 0 8px; letter-spacing:-.6px; color:#1f1d26; line-height:1.15; }
.sub { font-size:12pt; color:#5f5a6b; margin-bottom:4px; }
.prepared { font-size:8pt; color:#5f5a6b; margin-bottom:28px; }
.facts { border-top:1px solid #d9d4e3; padding-top:14px; max-width:340px; }
.fact { display:flex; justify-content:space-between; padding:4px 0; font-size:10pt; }
.fact span:first-child { color:#5f5a6b; }
.fact span:last-child { font-variant-numeric:tabular-nums; color:#24212c; }
.legend { margin-top:16px; max-width:430px; font-size:9pt; }
.legend div { margin-bottom:6px; color:#3b3746; }
.note { margin-top:22px; font-size:8pt; color:#5f5a6b; line-height:1.55; max-width:430px; }
.toc { page-break-after:always; }
.toc h2 { font-size:14pt; margin:0 0 12px; }
.toc a { color:#24212c; text-decoration:none; }
.toc div { padding:3px 0; border-bottom:1px solid #eeebf5; display:flex; justify-content:space-between; max-width:420px; }
.toc span { color:#5f5a6b; font-variant-numeric:tabular-nums; }
h2.month { font-size:8pt; font-weight:600; color:#5f5a6b; text-transform:uppercase; letter-spacing:.6px;
  margin:0 0 6px; }
.day { page-break-before:always; }
.keep { break-inside:avoid; }
.day.first { page-break-before:avoid; }
.flow .day { page-break-before:auto; margin-top:18px; }
h3.dayhead { border-bottom:2px solid #1f1d26; padding-bottom:4px; margin:0 0 2px;
  font-size:14pt; font-weight:600; color:#1f1d26; break-after:avoid; }
.daymeta { font-size:8pt; color:#5f5a6b; margin-bottom:12px; break-after:avoid; }
.conv { width:74%; margin:0 auto 18px; }
.convhead { font-size:8pt; font-weight:600; color:#5f5a6b; margin-bottom:8px; break-after:avoid;
  padding-bottom:3px; border-bottom:1px solid #e3dfeb; letter-spacing:.2px; }
.cont { display:inline-block; background:#efecf6; color:#4f4a5c; border-radius:20px;
  padding:1px 7px; font-size:7.5pt; margin-left:6px; }
.run { margin-bottom:10px; }
.run.u { text-align:right; }
.turn { margin-bottom:4px; page-break-inside:avoid; }
.who { font-size:8pt; color:#5f5a6b; margin-bottom:2px; }
.bub { display:inline-block; max-width:88%; text-align:left; padding:6px 11px; border-radius:12px; }
.u .bub { background:#ebe6f7; color:#24212c; border:1px solid #c9bfe6; border-bottom-right-radius:4px; }
.a .bub { background:#f6f6f8; color:#24212c; border:1px solid #d9d6df; border-bottom-left-radius:4px; }
.bub p { margin:0 0 5px; } .bub p:last-child { margin-bottom:0; }
.long { font-size:9pt; color:#2f2b38; background:#faf9fc; border:1px solid #d9d6df;
  border-radius:10px; padding:12px 14px; }
.long p { margin:0 0 6px; }
.entry { margin-bottom:14px; page-break-inside:avoid; max-width:165mm; }
.etitle { font-size:11pt; font-weight:600; color:#1f1d26; margin:2px 0 4px; }
.ebody { background:#f6f6f8; border:1px solid #d9d6df; border-radius:10px; padding:8px 12px; }
.ebody p { margin:0 0 5px; } .ebody p:last-child { margin-bottom:0; }
.you .ebody { background:#ebe6f7; border-color:#c9bfe6; }
.sec { font-size:7.5pt; color:#5f5a6b; letter-spacing:.5px; text-transform:uppercase; margin:8px 0 3px; }
.ins { border-left:3px solid #b99bc4; padding:1px 0 1px 9px; margin-bottom:5px; }
.ins q { font-style:italic; }
.aff { margin:0 0 3px; padding-left:12px; }
.fb { font-size:8.5pt; color:#3b3746; margin:3px 0 6px; }
.fb .fbl { font-size:7.5pt; font-weight:600; color:#5f5a6b; letter-spacing:.4px; text-transform:uppercase; margin-right:6px; }
.author { display:inline-block; border-radius:20px; padding:1px 8px; font-size:7.5pt; font-weight:600;
  letter-spacing:.2px; vertical-align:1px; margin-right:6px; }
.author.you { background:#5b4b9e; color:#fff; }
.author.aurie { background:#efecf6; color:#4f4a5c; border:1px solid #cfc9dc; }
"""


def plural(n, one, many=None):
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


def num(v):
    return f"{v:,}" if isinstance(v, int) else str(v)


def header_rule(title, span):
    text = f"{title} · {span}".replace("\\", "\\\\").replace('"', '\\"')
    return f'@page {{ @top-left {{ content: "{text}"; }} }}'


def prepared_line():
    return "Prepared on " + datetime.date.today().strftime("%B %-d, %Y")


def time_span(a, b):
    """'4:07 PM – 4:14 PM', or a single time when both ends are the same minute."""
    s, e = a.strftime("%-I:%M %p"), b.strftime("%-I:%M %p")
    return s if s == e else f"{s} &ndash; {e}"


def month_id(d):
    return f"m-{d.year}-{d.month:02d}"


def toc_html(months):
    """A contents page: one clickable line per month (Chrome cannot compute page numbers)."""
    return ('<div class="toc"><h2>Contents</h2>'
            + "".join(f'<div><a href="#{month_id(m)}">{m.strftime("%B %Y")}</a><span>{plural(n, "day")}</span></div>'
                      for m, n in months)
            + "</div>")


def md_safe(text: str) -> str:
    """Neutralise block-level Markdown inside a message body.

    Users paste documents into the chat, and a pasted `# Heading` or `---` rule
    would otherwise be emitted as real document structure — hijacking the day
    hierarchy the export is built on. Escaping the leading marker keeps every
    character on the page while stopping it from acting as structure. Inline
    marks (bold, italic, links) are left alone; they cannot break the outline.
    """
    out = []
    for line in text.split("\n"):
        line = re.sub(r"^(\s*)(#{1,6})(\s|$)", r"\1\\\2\3", line)
        line = re.sub(r"^(\s*)(-{3,}|_{3,}|\*{3,}|={3,})\s*$", r"\1\\\2", line)
        out.append(line)
    return "\n".join(out)


LEGEND = [("you", "Written by you", "entries you typed in the app."),
          ("aurie", "Written by Aurie", "daily journal entries, insights and affirmations Aurie generated "
                                       "for you, including the insights on your own entries.")]

COVER_NOTE = (
    "This document contains your journal as it appears in the Aurie app: your own entries, "
    "the entries Aurie wrote for you, their insights and affirmations, and your saved "
    "affirmations, in the order they were written. Entries are grouped by day, with times in "
    "your local timezone ({tz}). Entries you removed in the app are not included."
)


def _insight_html(i):
    if isinstance(i, str):
        return f'<div class="ins">{fmt_md(i)}</div>'
    quote, context = i.get("quote"), i.get("context")
    if quote or context:
        return ('<div class="ins">' + (f'<q>{html.escape(quote)}</q>' if quote else "")
                + (fmt_md(context) if context else "") + "</div>")
    return '<div class="ins">' + "".join(fmt_md(v) for v in i.values()) + "</div>"


def _insight_md(i):
    if isinstance(i, str):
        return "> " + md_safe(i).replace("\n", "\n> ")
    quote, context = i.get("quote"), i.get("context")
    if quote or context:
        return "> " + (f"*\u201c{md_safe(quote)}\u201d* " if quote else "") + md_safe(context or "").replace("\n", "\n> ")
    return "> " + md_safe(" ".join(i.values()))


def _facts(stats):
    k = stats["by_kind"]
    return [("Days with journal entries", stats["days"]),
            ("Written by you", k.get("user", 0)),
            ("Written by Aurie (daily journals)", k.get("daily", 0) + k.get("onboarding", 0)),
            ("Saved affirmations (written by Aurie)", k.get("affirmation", 0))]


def _chip(by):
    return f'<span class="author {"you" if by == "You" else "aurie"}">{AUTHOR[by]}</span>'


def _sec(name, own_entry):
    """Insights and affirmations are always Aurie's, even on an entry the user wrote."""
    return f'<div class="sec">{name}{" &middot; written by Aurie" if own_entry else ""}</div>'


def date_range(first, last, dash="–"):
    """The cover's span. The first date keeps its year when the span crosses a year, so a history
    from 2024 never reads as starting in the last year."""
    start = first.strftime("%B %-d" if first.year == last.year else "%B %-d, %Y")
    return f"{start} {dash} {last.strftime('%B %-d, %Y')}"


FEEDBACK_CSS = """
.fb { font-size:8.5pt; color:#3b3746; margin:3px 0 6px; }
.fb .fbl { font-size:7.5pt; font-weight:600; color:#5f5a6b; letter-spacing:.4px; text-transform:uppercase; margin-right:6px; }
"""


def rating_text(rating):
    """A stored rating as the app shows it: thumbs for -1/0/1, stars for the 1–5 scale above 1."""
    if rating is None:
        return ""
    try:
        value = float(rating)
    except (TypeError, ValueError):
        return str(rating)
    if value == 1:
        return "👍"
    if value == -1:
        return "👎"
    if value == 0:
        return "😐"
    if 1 < value <= 5 and value == int(value):
        n = int(value)
        return "★" * n + "☆" * (5 - n) + f" ({n} of 5)"
    return f"{value:g}"


def feedback_parts(row):
    """(rating text, feedback text), or None when the user gave no feedback on this item."""
    rating, text = rating_text(row.get("rating")), (row.get("feedback") or "").strip()
    return (rating, text) if rating or text else None


def feedback_html(row, label="Your feedback"):
    parts = feedback_parts(row)
    if not parts:
        return ""
    rating, text = parts
    shown = " · ".join(x for x in (rating, f"“{html.escape(text)}”" if text else "") if x)
    return f'<div class="fb"><span class="fbl">{label}</span>{shown}</div>'


def feedback_md(row, label="Your feedback"):
    parts = feedback_parts(row)
    if not parts:
        return ""
    rating, text = parts
    return f"*{label}:* " + " · ".join(x for x in (rating, f"“{md_safe(text)}”" if text else "") if x)


def _entry_html(e):
    p = [f'<div class="entry{" you" if e["by"] == "You" else ""}">'
         f'<div class="who">{_chip(e["by"])}{KIND[e["kind"]]} &middot; {e["dt"].strftime("%-I:%M %p")}</div>']
    if e["title"]:
        p.append(f'<div class="etitle">{html.escape(e["title"])}</div>')
    if e["body"]:
        p.append(f'<div class="ebody">{fmt_md(e["body"])}</div>')
    if e["insights"]:
        p.append(_sec("Insights", e["by"] == "You") + "".join(_insight_html(i) for i in e["insights"]))
    if e["affirmations"]:
        p.append(_sec("Affirmations", e["by"] == "You")
                 + "".join(f'<p class="aff">{html.escape(a)}</p>' for a in e["affirmations"]))
    p.append(feedback_html(e))
    p.append("</div>")
    return "".join(p)


def build_html(rows, tz):
    days, stats = prepare(rows, tz)
    first, last = stats["first"], stats["last"]
    title = "Your journal with Aurie"
    p = [f'<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>'
         f'<style>{CSS}{header_rule(title, date_range(first, last))}</style></head><body>']
    p.append(f'<div class="cover"><div class="mark"></div><h1>{title}</h1>'
             f'<div class="sub">{date_range(first, last, "&ndash;")}</div>'
             f'<div class="prepared">{prepared_line()}</div>'
             '<div class="facts">' + "".join(f'<div class="fact"><span>{a}</span><span>{num(b)}</span></div>'
                                              for a, b in _facts(stats))
             + '</div><div class="legend">'
             + "".join(f'<div><span class="author {k}">{a}</span>{html.escape(b)}</div>' for k, a, b in LEGEND)
             + f'</div><div class="note">{html.escape(COVER_NOTE.format(tz=tz))}</div></div>')
    # Days flow on: no page per day. A day's heading stays with its first entry.
    p.append('<div class="flow">')
    seen_month = None
    for d, entries in days.items():
        p.append('<div class="day">')
        month = ""
        if (d.year, d.month) != seen_month:
            seen_month = (d.year, d.month)
            month = f'<h2 class="month" id="{month_id(d)}">{d.strftime("%B %Y")}</h2>'
        p.append(f'<div class="keep">{month}<h3 class="dayhead">{d.strftime("%A, %B %-d, %Y")}</h3>'
                 f'<div class="daymeta">{plural(len(entries), "entry", "entries")}</div>{_entry_html(entries[0])}</div>')
        p.extend(_entry_html(e) for e in entries[1:])
        p.append("</div>")
    p.append("</div></body></html>")
    return "".join(p), stats


def build_markdown(rows, tz):
    days, stats = prepare(rows, tz)
    first, last = stats["first"], stats["last"]
    out = ["# Your journal with Aurie", "", f'{date_range(first, last)}', "",
           "| | |\n| --- | --- |\n" + "\n".join(f"| {a} | {b} |" for a, b in _facts(stats)), "",
           *[f"- **{a}**: {b}" for _, a, b in LEGEND], "",
           "> " + COVER_NOTE.format(tz=tz), ""]
    for d, entries in days.items():
        out += ["---", "", f'## {d.strftime("%A, %B %-d, %Y")}', "",
                f'*{len(entries)} entr{"ies" if len(entries) != 1 else "y"}*', ""]
        for e in entries:
            out += [f'**{AUTHOR[e["by"]]}** · {KIND[e["kind"]]} · {e["dt"].strftime("%-I:%M %p")}', ""]
            if e["title"]:
                out += [f'### {md_safe(e["title"])}', ""]
            if e["body"]:
                out += [md_safe(e["body"]), ""]
            if e["insights"]:
                out += [f'*Insights{" · written by Aurie" if e["by"] == "You" else ""}*', ""] + [
                    _insight_md(i) + "\n" for i in e["insights"]]
            if e["affirmations"]:
                out += [f'*Affirmations{" · written by Aurie" if e["by"] == "You" else ""}*', ""] + [f"- {md_safe(a)}" for a in e["affirmations"]] + [""]
            if feedback_md(e):
                out += [feedback_md(e), ""]
    return "\n".join(out) + "\n", stats


def find_chrome() -> str:
    for c in CHROME_CANDIDATES:
        if Path(c).exists():
            return c
    for name in ("google-chrome", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    raise SystemExit(
        "No Chrome/Chromium found for PDF rendering. Install Google Chrome, "
        "or pass --html-only and render the HTML yourself."
    )


def render_pdf(html_path: Path, pdf_path: Path) -> None:
    # A PDF left by an earlier run must never pass for this one.
    pdf_path.unlink(missing_ok=True)
    done = subprocess.run(
        [
            find_chrome(), "--headless", "--disable-gpu", "--no-sandbox",
            "--no-pdf-header-footer", "--generate-pdf-document-outline", f"--print-to-pdf={pdf_path}", f"file://{html_path}",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",  # Chrome's own output is never what decides success: the file is
    )
    if done.returncode != 0 or not pdf_path.exists():
        pdf_path.unlink(missing_ok=True)
        raise SystemExit(f"Chrome did not produce a PDF (exit {done.returncode}).")


# ----------------------------------------------------------------------- main


def uid_prefix(uid: str) -> str:
    """A uid prefix that still tells users apart: for a provider id (`google:112...`) the 8
    characters come after the provider, which stays as a label. Canonical:
    report-conversation-patterns/scripts/sample.py (docs/canonical-sources.md)."""
    uid = uid or ""
    if ":" in uid:
        provider, _, rest = uid.partition(":")
        return f"{provider}:{rest[:8]}"
    return uid[:8]


def summary_line(uid, stats):
    left = ", ".join(f"{k} {v}" for k, v in sorted(stats["left_out"].items()))
    return (f"  uid {uid_prefix(uid)}… | {stats['first']} → {stats['last']} | {stats['days']} days | "
            f"{stats['entries']} entries of {stats['rows']} rows"
            + (f" | left out (not in this export): {left}" if left else ""))


def main(argv=None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--uid", required=True, help="The user's Firebase UID, from a trusted source; "
                                                 "the file's uid prefix must match it.")
    ap.add_argument("--from-file", required=True, help="The journal file from eng:query-user-export-data")
    ap.add_argument("--host", default=os.environ.get("AURA_API_HOST", DEFAULT_HOST),
                    help=f"API host (default {DEFAULT_HOST}, or $AURA_API_HOST)")
    ap.add_argument("--format", choices=("pdf", "md", "both"), default="pdf")
    ap.add_argument("--out", help="Output path (default ./aurie-journal-<uid8>.pdf); the extension is swapped per format")
    ap.add_argument("--timezone", help="Override the user's timezone (IANA name)")
    ap.add_argument("--token-env", default="AURA_API_TOKEN", help="Env var holding the bearer token")
    ap.add_argument("--html-only", action="store_true", help="Write HTML and skip PDF rendering")
    ap.add_argument("--keep-intermediates", action="store_true", help="Keep the HTML (it holds the journal)")
    args = ap.parse_args(argv)

    token = os.environ.get(args.token_env)
    if not token and not args.timezone:  # the token is only for reading the user's timezone
        raise SystemExit(f"{args.token_env} is not set: it reads the user's timezone. Or pass --timezone.")
    # A typo guard only: Aura user ids come in more shapes than any allowlist survives.
    if not args.uid.strip() or len(args.uid) > 256 or re.search(r"\s", args.uid):
        raise SystemExit("Refusing to run: that is not a usable user id.")

    rows, removed = load_rows(args.from_file, args.uid)
    uid = args.uid
    tz, how = resolve_timezone(args.host, token, args.uid, args.timezone)
    print(f"  timezone {tz} ({how})")
    _, stats = prepare(rows, tz)
    if removed:  # read from the file, left out as the app shows it: counted in rows and in left out
        stats["left_out"]["removed in the app"] = removed
        stats["rows"] += removed
    if not stats["entries"]:
        raise SystemExit(f"No journal entries to export for {uid_prefix(uid)}… ({stats['rows']} rows, "
                         f"left out: {stats['left_out'] or 'none'}).")

    base = (Path(args.out) if args.out else Path(f"aurie-journal-{re.sub(r'[^A-Za-z0-9_-]', '-', uid_prefix(uid))}.pdf")).expanduser().resolve()
    base.parent.mkdir(parents=True, exist_ok=True)
    if args.format in ("md", "both"):
        md_doc, _ = build_markdown(rows, tz)
        md_path = base if base.suffix.lower() == ".md" else base.with_suffix(".md")
        md_path.write_text(md_doc, encoding="utf-8")
        os.chmod(md_path, 0o600)
        print(f"Markdown: {md_path}")
    if args.format in ("pdf", "both") or args.html_only:
        doc, _ = build_html(rows, tz)
        pdf_path = base if base.suffix.lower() == ".pdf" else base.with_suffix(".pdf")
        keep = args.keep_intermediates or args.html_only
        html_path = pdf_path.with_suffix(".html") if keep else Path(tempfile.mkdtemp(prefix="aurie-journal-")) / "export.html"
        html_path.write_text(doc, encoding="utf-8")
        os.chmod(html_path, 0o600)  # the HTML holds the whole journal
        if args.html_only:
            print(f"HTML: {html_path}")
        else:
            try:
                render_pdf(html_path, pdf_path)
            finally:  # the HTML holds the whole journal: never left behind, even when rendering fails
                if not keep:
                    shutil.rmtree(html_path.parent, ignore_errors=True)
            os.chmod(pdf_path, 0o600)
            if not keep:
                print("  intermediate HTML deleted")
            print(f"PDF: {pdf_path}")
    print(summary_line(uid, stats))


if __name__ == "__main__":
    main()
