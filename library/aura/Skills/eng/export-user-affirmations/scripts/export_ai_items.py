#!/usr/bin/env python3
"""Export one user's Aurie moods, insights or affirmations to a readable PDF or Markdown file.

One engine for the data-request exports of stored Aurie items (eng:export-user-insights,
eng:export-user-affirmations, eng:export-user-memory); each skill ships this same file and passes `--entity`. The
`moods` entity is kept for completeness: moods are a retired feature with no export skill. It reads
the JSON file eng:query-user-export-data wrote for one section (`{version, entity, generatedAt,
userIdPrefix, rows}`; there is deliberately no API route for another user's rows), keeps the
fields a person can read about themselves, groups them by the user's LOCAL day, and prints
them to PDF with headless Chrome, in the eng:export-user-journal layout.

Read-only: the only call it makes is UserService `GET /users/:userId`, for the timezone.
Verbatim: no LLM reads the content; the user's own feedback (rating and text) shows inside its item;
ids, session ids and delivery flags are
never rendered. Removed rows are not returned by the API. Output files are written 0600, and a
folder the script creates is 0700.
"""

from __future__ import annotations

import argparse
import datetime
import html
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
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

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
]


# ------------------------------------------------------------------------ input

FILE_VERSION = 1


def load_rows(path, entity, uid):
    """The rows of one section from eng:query-user-export-data's file. Refuses a file of another
    version, another section or another user, so a mixed-up file is never rendered."""
    try:
        data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise SystemExit(f"Could not read the export file ({type(error).__name__}).") from error
    if not isinstance(data, dict) or data.get("version") != FILE_VERSION:
        raise SystemExit(f"The export file is not version {FILE_VERSION}; refusing to guess its shape.")
    if data.get("entity") != entity:
        raise SystemExit(f"The export file holds {data.get('entity')!r}, not {entity!r}.")
    if data.get("userIdPrefix") != uid_prefix(uid):
        raise SystemExit("The export file is for another user (its uid prefix does not match --uid).")
    rows = data.get("rows")
    if not isinstance(rows, list):
        raise SystemExit("The export file has no rows list.")
    return rows


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


def _text(value):
    return str(value).strip() if isinstance(value, (str, int, float)) and str(value).strip() else ""


def _label(kind, fallback):
    """`session-insight` -> `Session insight`; the kind as words, nothing invented."""
    words = _text(kind).replace("-", " ").replace("_", " ").strip()
    return words[:1].upper() + words[1:] if words else fallback


def mood_entry(row):
    score = _text(row.get("score"))
    if not score:
        return None
    low, high = _text(row.get("min")), _text(row.get("max"))
    body = f"Mood score: {score}"
    if low and high and (low, high) != (score, score):
        body += f" · lowest {low}, highest {high}"
    return {"label": _label(row.get("kind"), "Mood"), "title": "", "body": body, "lists": []}


def insight_entry(row):
    title, message = _text(row.get("title")), _text(row.get("message"))
    lists = [(name, _items(row.get(key))) for key, name in
             (("highlights", "Highlights"), ("recommendations", "Recommendations"),
              ("chatStarters", "Conversation starters"))]
    # `timeline` is the retired emotion-detection series: deliberately not rendered.
    lists = [(n, v) for n, v in lists if v]
    feel = [f"{n}: {v}" for n, v in (("Emotion", _text(row.get("emotion"))), ("Mood", _text(row.get("mood")))) if v]
    if not (title or message or lists):
        return None
    body = message + ("\n\n" + " · ".join(feel) if feel else "")
    return {"label": _label(row.get("kind"), "Insight"), "title": title, "body": body.strip(), "lists": lists}


def memory_entry(row):
    text = _text(row.get("memory"))
    return {"label": "Memory", "title": "", "body": text, "lists": []} if text else None


def affirmation_entry(row):
    text = _text(row.get("text"))
    return {"label": "Affirmation", "title": "", "body": text, "lists": []} if text else None


ENTITIES = {
    "moods": {
        "entry": mood_entry, "noun": ("mood", "moods"),
        "title": "Your moods with Aurie", "author": ("aurie", "Written by Aurie"),
        "legend": "moods Aurie's systems computed for you. You never entered them, and the app does not show them, so this document is the only copy you can read.",
        "note": "This document lists the moods recorded for you in the Aurie app, in the order they were "
                "recorded, grouped by day with times in your local timezone ({tz}). Moods you removed in the "
                "app are not included.",
    },
    "insights": {
        "entry": insight_entry, "noun": ("insight", "insights"),
        "title": "Your insights from Aurie", "author": ("aurie", "Written by Aurie"),
        "legend": "insights Aurie generated for you. The app shows an insight only briefly, at the end of a conversation or with a journal entry, so this document is the only lasting copy.",
        "note": "This document lists the insights Aurie wrote for you, with their highlights and "
                "recommendations, in the order they were written, grouped by day with times in your local "
                "timezone ({tz}). Insights you removed in the app are not included.",
    },
    "affirmations": {
        "entry": affirmation_entry, "noun": ("affirmation", "affirmations"),
        "title": "Your affirmations from Aurie", "author": ("aurie", "Written by Aurie"),
        "legend": "affirmations Aurie generated for you.",
        "note": "This document lists your affirmations in the Aurie app, in the order they were created, "
                "grouped by day with times in your local timezone ({tz}). Affirmations you removed in the app "
                "are not included.",
    },
    "memory": {
        "entry": memory_entry, "noun": ("memory", "memories"),
        "title": "What Aurie remembers about you", "author": ("aurie", "Written by Aurie"),
        "legend": "memories Aurie wrote down from your conversations to remember about you. The app does not "
                  "show them, so this document is the only copy you can read.",
        "note": "This document lists what Aurie currently remembers about you, in the order it was noted, "
                "grouped by day with times in your local timezone ({tz}). Memories that were deleted or "
                "have expired are not included.",
    },
}


def prepare(rows, entity, tz):
    """(days, stats): shown entries by local day, oldest first, plus counts. A row with nothing a
    person can read is counted, never silently dropped."""
    shown, empty = [], 0
    for row in rows:
        e = ENTITIES[entity]["entry"](row)
        if e is not None:
            e["rating"], e["feedback"] = row.get("rating"), row.get("feedback")
        if e is None or not row.get("createdAt"):
            empty += 1
            continue
        e["dt"] = datetime.datetime.fromisoformat(str(row["createdAt"]).replace("Z", "+00:00")).astimezone(tz)
        shown.append(e)
    shown.sort(key=lambda e: e["dt"])
    days = OrderedDict()
    for e in shown:
        days.setdefault(e["dt"].date(), []).append(e)
    stats = {"rows": len(rows), "entries": len(shown), "days": len(days), "empty": empty,
             "by_label": dict(Counter(e["label"] for e in shown)),
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
.ins p { margin:0; }
.fb { font-size:8.5pt; color:#3b3746; margin:3px 0 6px; }
.fb .fbl { font-size:7.5pt; font-weight:600; color:#5f5a6b; letter-spacing:.4px; text-transform:uppercase; margin-right:6px; }
/* Long item lists may break across pages; a single item, a chip line or a title never does. */
.flow .entry { page-break-inside:auto; }
.flow .ins, .flow .who, .flow .etitle { break-inside:avoid; }
.flow .who, .flow .etitle, .flow .sec { break-after:avoid; }
.aff { margin:0 0 3px; padding-left:12px; }
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



def date_range(first, last, dash="–"):
    """The cover's span. The first date keeps its year when the span crosses a year."""
    start = first.strftime("%B %-d" if first.year == last.year else "%B %-d, %Y")
    return f"{start} {dash} {last.strftime('%B %-d, %Y')}"


# The readable parts of a list item, in reading order. Everything else on a stored item (ids, timestamps,
# urls, nested emotion data) is internal and never rendered.
READABLE = ("title", "message", "text", "quote", "context", "label", "sentence")


def _item_text(i):
    if isinstance(i, str):
        return i
    return " — ".join(i[k] for k in READABLE if isinstance(i.get(k), str) and i[k].strip())


def _count(n, noun):
    return f"{n:,} {noun[0] if n == 1 else noun[1]}"


def _facts(entity, stats):
    noun = ENTITIES[entity]["noun"]
    by_label = sorted(stats["by_label"].items())
    return [("Days", stats["days"]), (noun[1].capitalize(), stats["entries"])] + [
        (_kind_label(label, noun), n) for label, n in by_label if len(by_label) > 1]


def _kind_label(label, noun):
    """A per-kind cover line, plural, never repeating the total's own label."""
    plural_label = label if label.endswith("s") else label + "s"
    return f"Other {noun[1]}" if plural_label.lower() == noun[1].lower() else plural_label


def _chip(entity):
    cls, label = ENTITIES[entity]["author"]
    return f'<span class="author {cls}">{label}</span>'


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


def _entry_html(entity, e, chip=True):
    """One item. Every item in these documents has the same author, so the chip shows once a day."""
    p = [f'<div class="entry"><div class="who">{_chip(entity) if chip else ""}{html.escape(e["label"])} &middot; '
         f'{e["dt"].strftime("%-I:%M %p")}</div>']
    if e["title"]:
        p.append(f'<div class="etitle">{html.escape(e["title"])}</div>')
    if e["body"]:
        p.append(f'<div class="ebody">{fmt_md(e["body"])}</div>')
    for name, values in e["lists"]:
        p.append(f'<div class="sec">{html.escape(name)}</div>'
                 + "".join(f'<div class="ins">{fmt_md(_item_text(i))}</div>' for i in values if _item_text(i)))
    p.append(feedback_html(e))
    p.append("</div>")
    return "".join(p)


def build_html(rows, entity, tz):
    days, stats = prepare(rows, entity, tz)
    cfg, first, last = ENTITIES[entity], stats["first"], stats["last"]
    months = []
    for d in days:
        if months and (months[-1][0].year, months[-1][0].month) == (d.year, d.month):
            months[-1][1] += 1
        else:
            months.append([d.replace(day=1), 1])
    p = [f'<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(cfg["title"])}</title>'
         f'<style>{CSS}{header_rule(cfg["title"], date_range(first, last))}</style></head><body>']
    p.append(f'<div class="cover"><div class="mark"></div><h1>{html.escape(cfg["title"])}</h1>'
             f'<div class="sub">{date_range(first, last, "&ndash;")}</div>'
             f'<div class="prepared">{prepared_line()}</div>'
             '<div class="facts">' + "".join(f'<div class="fact"><span>{html.escape(str(a))}</span><span>{num(b)}</span></div>'
                                              for a, b in _facts(entity, stats))
             + f'</div><div class="legend"><div>{_chip(entity)}{html.escape(cfg["legend"])}</div>'
             + f'</div><div class="note">{html.escape(cfg["note"].format(tz=tz))}</div></div>')
    if entity == "insights" and len(months) > 1:
        p.append(toc_html(months))
    # Days flow on: no page per day. A day's heading stays with its first entry.
    p.append('<div class="flow">')
    seen_month = None
    for d, entries in days.items():
        month = ""
        if (d.year, d.month) != seen_month:
            seen_month = (d.year, d.month)
            month = f'<h2 class="month" id="{month_id(d)}">{d.strftime("%B %Y")}</h2>'
        p.append(f'<div class="day"><div class="keep">{month}<h3 class="dayhead">{d.strftime("%A, %B %-d, %Y")}</h3>'
                 f'<div class="daymeta">{_count(len(entries), cfg["noun"])}</div>{_entry_html(entity, entries[0])}</div>')
        p.extend(_entry_html(entity, e, chip=False) for e in entries[1:])
        p.append("</div>")
    p.append("</div></body></html>")
    return "".join(p), stats


def build_markdown(rows, entity, tz):
    days, stats = prepare(rows, entity, tz)
    cfg, first, last = ENTITIES[entity], stats["first"], stats["last"]
    out = [f"# {cfg['title']}", "", f'{date_range(first, last)}', "",
           "| | |\n| --- | --- |\n" + "\n".join(f"| {a} | {b} |" for a, b in _facts(entity, stats)), "",
           f"- **{cfg['author'][1]}**: {cfg['legend']}", "", "> " + cfg["note"].format(tz=tz), ""]
    for d, entries in days.items():
        out += ["---", "", f'## {d.strftime("%A, %B %-d, %Y")}', "", f'*{_count(len(entries), cfg["noun"])}*', ""]
        for e in entries:
            out += [f'**{cfg["author"][1]}** · {md_safe(e["label"])} · {e["dt"].strftime("%-I:%M %p")}', ""]
            if e["title"]:
                out += [f'### {md_safe(e["title"])}', ""]
            if e["body"]:
                out += [md_safe(e["body"]), ""]
            for name, values in e["lists"]:
                out += [f"*{name}*", ""] + [f"- {md_safe(_item_text(i))}" for i in values if _item_text(i)] + [""]
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


def _private_write(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(path, 0o600)


def summary_line(entity, uid, stats):
    return (f"  {entity} | uid {uid_prefix(uid)}… | {stats['first']} → {stats['last']} | {stats['days']} days | "
            f"{stats['entries']} shown of {stats['rows']} rows"
            + (f" | {stats['empty']} with nothing to show" if stats["empty"] else ""))


def main(argv=None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--entity", required=True, choices=sorted(ENTITIES))
    ap.add_argument("--uid", required=True, help="Firebase UID to export: one you trust for this person, never an email lookup")
    ap.add_argument("--from-file", required=True, help="The section's JSON file from eng:query-user-export-data")
    ap.add_argument("--host", default=os.environ.get("AURA_API_HOST", DEFAULT_HOST),
                    help=f"API host (default {DEFAULT_HOST}, or $AURA_API_HOST)")
    ap.add_argument("--format", choices=("pdf", "md", "both"), default="pdf")
    ap.add_argument("--out", help="Output path (default ./aurie-<entity>-<uid prefix>.pdf); the extension is swapped per format")
    ap.add_argument("--timezone", help="Override the user's timezone (IANA name)")
    ap.add_argument("--token-env", default="AURA_API_TOKEN", help="Env var holding the bearer token")
    ap.add_argument("--html-only", action="store_true", help="Write HTML and skip PDF rendering")
    ap.add_argument("--keep-intermediates", action="store_true", help="Keep the HTML (it holds the export)")
    args = ap.parse_args(argv)

    token = os.environ.get(args.token_env)
    if not token and not args.timezone:  # the token is only for reading the user's timezone
        raise SystemExit(f"{args.token_env} is not set: it reads the user's timezone. Or pass --timezone.")
    # A typo guard only: Aura user ids come in more shapes than any allowlist survives.
    if not args.uid.strip() or len(args.uid) > 256 or re.search(r"\s", args.uid):
        raise SystemExit("Refusing to run: that is not a usable user id.")
    entity, uid = args.entity, args.uid
    rows = load_rows(args.from_file, entity, uid)
    tz, how = resolve_timezone(args.host, token, uid, args.timezone)
    print(f"  timezone {tz} ({how})")
    _, stats = prepare(rows, entity, tz)
    if not stats["entries"]:
        raise SystemExit(f"No {entity} to export for {uid_prefix(uid)}… ({stats['rows']} rows, "
                         f"{stats['empty']} with nothing to show).")

    safe = re.sub(r"[^A-Za-z0-9_-]", "-", uid_prefix(uid))
    base = (Path(args.out) if args.out else Path(f"aurie-{entity}-{safe}.pdf")).expanduser().resolve()
    if not base.parent.exists():
        base.parent.mkdir(parents=True, mode=0o700)
    if args.format in ("md", "both"):
        md_doc, _ = build_markdown(rows, entity, tz)
        md_path = base if base.suffix.lower() == ".md" else base.with_suffix(".md")
        _private_write(md_path, md_doc)
        print(f"Markdown: {md_path}")
    if args.format in ("pdf", "both") or args.html_only:
        doc, _ = build_html(rows, entity, tz)
        pdf_path = base if base.suffix.lower() == ".pdf" else base.with_suffix(".pdf")
        keep = args.keep_intermediates or args.html_only
        html_path = pdf_path.with_suffix(".html") if keep else Path(tempfile.mkdtemp(prefix=f"aurie-{entity}-")) / "export.html"
        _private_write(html_path, doc)
        if args.html_only:
            print(f"HTML: {html_path}")
        else:
            try:
                render_pdf(html_path, pdf_path)
            finally:  # the HTML holds the export: never left behind, even when rendering fails
                if not keep:
                    shutil.rmtree(html_path.parent, ignore_errors=True)
            os.chmod(pdf_path, 0o600)
            if not keep:
                print("  intermediate HTML deleted")
            print(f"PDF: {pdf_path}")
    print(summary_line(entity, uid, stats))


if __name__ == "__main__":
    main()
