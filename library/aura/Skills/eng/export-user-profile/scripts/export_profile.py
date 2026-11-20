#!/usr/bin/env python3
"""Export the profile Aurie keeps about one user, plus their account details, to a readable PDF or Markdown.

The last part of a data-request export, in the same layout as the journal, conversations, insights,
affirmations and memory exports. Two sources:
- the `profile` file eng:query-user-export-data wrote (read-only SQL): the ingested profile document,
  the compiled insights document the coach prompts read, the Profile-tab cards (only those with
  `display` on, as the app shows them) and the onboarding conversation;
- the account page: UserService `GET /users/:userId` and PaymentService `POST /subscriptions/list/:userId`,
  each read through a field allow-list. Never the whole response: no credentials, ip, staff notes,
  revenue, receipts, transaction ids or cancel feedback.

Verbatim: no LLM reads the content. Everything about the profile is labelled "Written by Aurie"; the
user's own onboarding answers are "Written by you". Files are 0600; a folder the script creates is 0700.
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
FILE_VERSION = 1

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
]

# The account fields a person can read about themselves, in display order. Anything else on the user
# row (credentials, device tokens, ip, staff notes, flags) is never read into the document.
ACCOUNT_FIELDS = [
    ("email", "Email"), ("givenName", "First name"), ("familyName", "Last name"),
    ("createdAt", "Member since"), ("locale", "Language"), ("timezone", "Timezone"),
    ("countryCode", "Country"), ("gender", "Gender"),
]  # ageGroup is left out: a stored code with no readable meaning, and only Aura users have it
# The subscription fields shown (plan and dates only), per PaymentService's owner. Never revenue,
# transaction or receipt ids, codes, notes or cancel feedback.
PLAN_FIELDS = ("type", "period", "duration", "trial", "lifetime", "familyPlan", "free", "autoRenew",
               "expirationAt", "purchaseAt", "createdAt", "provider", "deletedAt")
CARD_TITLES = {
    "at_your_core": "At your core", "quote_card": "Your quote", "inner_tensions": "Inner tensions",
    "what_you_are_tending": "What you are tending", "you_at_your_best": "You at your best",
}


# ------------------------------------------------------------------------ input


def load_profile(path, uid):
    """The one profile row from eng:query-user-export-data's file. Refuses a file of another
    version, section or user."""
    try:
        data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise SystemExit(f"Could not read the export file ({type(error).__name__}).") from error
    if not isinstance(data, dict) or data.get("version") != FILE_VERSION:
        raise SystemExit(f"The export file is not version {FILE_VERSION}; refusing to guess its shape.")
    if data.get("entity") != "profile":
        raise SystemExit(f"The export file holds {data.get('entity')!r}, not 'profile'.")
    if data.get("userIdPrefix") != uid_prefix(uid):
        raise SystemExit("The export file is for another user (its uid prefix does not match --uid).")
    rows = data.get("rows")
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise SystemExit("The profile file must hold exactly one row.")
    return rows[0]


def load_ratings(path, uid):
    """The user's Profile-tab and card ratings (`profileRatings` file), or [] without one. Refuses a file
    of another version, section or user."""
    if not path:
        return []
    try:
        data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise SystemExit(f"Could not read the ratings file ({type(error).__name__}).") from error
    if not isinstance(data, dict) or data.get("version") != FILE_VERSION or data.get("entity") != "profileRatings":
        raise SystemExit("The ratings file is not a version-1 profileRatings file.")
    if data.get("userIdPrefix") != uid_prefix(uid):
        raise SystemExit("The ratings file is for another user (its uid prefix does not match --uid).")
    if not isinstance(data.get("rows"), list):
        raise SystemExit("The ratings file has no rows list.")
    return [r for r in data["rows"] if isinstance(r, dict)]


def _get(url, token, method="GET", body=None):
    request = urllib.request.Request(
        url, method=method, data=None if body is None else json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "User-Agent": user_agent(), "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8") or "null")


def fetch_account(host, token, uid):
    """(account fields, plan fields or None). Each response is cut to its allow-list at once."""
    quoted = urllib.parse.quote(uid, safe="")
    try:
        user = _get(f"{host.rstrip('/')}/users/{quoted}", token) or {}
        subs = _get(f"{host.rstrip('/')}/subscriptions/list/{quoted}", token, "POST", {}) or []
    except urllib.error.HTTPError as error:
        raise SystemExit(f"The account read failed (HTTP {error.code}); the account page needs it. "
                         "Check AURA_API_TOKEN (agent role).") from error
    except (urllib.error.URLError, ValueError, OSError) as error:
        raise SystemExit(f"The account read failed ({type(error).__name__}).") from error
    account = {k: user.get(k) for k, _ in ACCOUNT_FIELDS}
    plans = [{k: s.get(k) for k in PLAN_FIELDS} for s in subs if isinstance(s, dict)]
    return account, current_plans(plans)


def current_plans(plans, now=None):
    """Every subscription in force, one per product type: not deleted, and lifetime or an expiry still to
    come. A cancelled or lapsed row (rows are not deleted when they end) is never shown as current."""
    now = now or datetime.datetime.now(datetime.timezone.utc)

    def in_force(p):
        if p.get("deletedAt"):
            return False
        if p.get("lifetime") is True:
            return True
        try:
            return datetime.datetime.fromisoformat(str(p.get("expirationAt")).replace("Z", "+00:00")) > now
        except (TypeError, ValueError):
            return False
    best = {}
    for p in filter(in_force, plans):
        key = p.get("type") or ""
        if key not in best or (bool(p.get("lifetime")), str(p.get("expirationAt") or "")) > \
                (bool(best[key].get("lifetime")), str(best[key].get("expirationAt") or "")):
            best[key] = p
    return [best[k] for k in sorted(best)]



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


def _when(value, tz):
    """An ISO timestamp as a readable date in the user's timezone, or "" when absent."""
    if not value:
        return ""
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(tz).strftime("%B %-d, %Y")
    except ValueError:
        return str(value)


def _yes(value):
    return "yes" if value is True else "no" if value is False else ""


# Readable names for the stored codes. A code not listed here is shown as stored, never guessed.
LANGUAGES = {"en": "English", "es": "Spanish", "fr": "French", "de": "German", "pt": "Portuguese", "it": "Italian",
             "nl": "Dutch", "sv": "Swedish", "da": "Danish", "no": "Norwegian", "nb": "Norwegian", "fi": "Finnish",
             "pl": "Polish", "ru": "Russian", "uk": "Ukrainian", "tr": "Turkish", "ar": "Arabic", "he": "Hebrew",
             "hi": "Hindi", "ja": "Japanese", "ko": "Korean", "zh": "Chinese", "id": "Indonesian", "th": "Thai",
             "vi": "Vietnamese", "el": "Greek", "cs": "Czech", "ro": "Romanian", "hu": "Hungarian"}
COUNTRIES = {
    "US": "United States", "CA": "Canada", "GB": "United Kingdom", "IE": "Ireland", "AU": "Australia",
    "NZ": "New Zealand", "FR": "France", "DE": "Germany", "ES": "Spain", "PT": "Portugal", "IT": "Italy",
    "NL": "Netherlands", "BE": "Belgium", "CH": "Switzerland", "AT": "Austria", "SE": "Sweden", "NO": "Norway",
    "DK": "Denmark", "FI": "Finland", "IS": "Iceland", "PL": "Poland", "CZ": "Czechia", "SK": "Slovakia",
    "HU": "Hungary", "RO": "Romania", "BG": "Bulgaria", "GR": "Greece", "TR": "Turkey", "UA": "Ukraine",
    "RU": "Russia", "IL": "Israel", "AE": "United Arab Emirates", "SA": "Saudi Arabia", "EG": "Egypt",
    "ZA": "South Africa", "NG": "Nigeria", "KE": "Kenya", "IN": "India", "PK": "Pakistan", "BD": "Bangladesh",
    "SG": "Singapore", "MY": "Malaysia", "ID": "Indonesia", "PH": "Philippines", "TH": "Thailand",
    "VN": "Vietnam", "CN": "China", "HK": "Hong Kong", "TW": "Taiwan", "JP": "Japan", "KR": "South Korea",
    "MX": "Mexico", "BR": "Brazil", "AR": "Argentina", "CL": "Chile", "CO": "Colombia", "PE": "Peru",
}
# Where a plan was bought, in the words the user knows. Any other value is left off the page.
PROVIDERS = {"apple": "App Store", "google": "Google Play", "stripe": "Web", "paypal": "Web", "braintree": "Web"}


def _language(code):
    code = (code or "").strip()
    return LANGUAGES.get(code.split("-")[0].split("_")[0].lower(), code)


def account_rows(account, plan, tz):
    """[(group, [(label, value)])]. A row whose stored value is missing or empty is not shown at all."""
    readable = {"createdAt": _when(account.get("createdAt"), tz), "locale": _language(account.get("locale")),
                "countryCode": COUNTRIES.get((account.get("countryCode") or "").upper(), account.get("countryCode"))}
    identity = []
    for key, label in ACCOUNT_FIELDS:
        value = readable.get(key, account.get(key))
        if value not in (None, "") and str(value).strip():
            identity.append((label, str(value).strip()))
    groups = [("Identity", identity)] if identity else []
    for plan in plan or []:
        rows = _plan_rows(plan, tz)
        if rows:
            groups.append(("Plan", rows))
    return groups


def _plan_rows(plan, tz):
    words = [str(w) for w in (plan.get("type"), plan.get("period"), plan.get("duration")) if w]
    flags = [n for n, k in (("lifetime", "lifetime"), ("family plan", "familyPlan"), ("trial", "trial"),
                            ("free", "free")) if plan.get(k) is True]
    rows = []
    if words or flags:
        name = ", ".join(words).replace("-", " ")
        rows.append(("Plan", (name[:1].upper() + name[1:] if name else "Plan") + (f" ({', '.join(flags)})" if flags else "")))
    for label, value in (("Started", _when(plan.get("createdAt"), tz)),
                         ("Last purchase or renewal", _when(plan.get("purchaseAt"), tz)),
                         ("Renews on" if plan.get("autoRenew") else "Ends on", _when(plan.get("expirationAt"), tz)),
                         ("Renews automatically", _yes(plan.get("autoRenew"))),
                         ("Purchased through", PROVIDERS.get(str(plan.get("provider") or "").lower(), ""))):
        if value:
            rows.append((label, value))
    return rows


def _readable(value):
    """The text of a card item: strings as they are, objects by their readable fields."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return " — ".join(value[k].strip() for k in ("title", "label", "phrase", "sentence", "text")
                          if isinstance(value.get(k), str) and value[k].strip())
    return ""


def cards(renderers):
    """[(title, [(kind, subheading, items)])] for every card with `display` on, in a stable order. `kind`
    is "text" (paragraphs), "list" (items) or "tensions" (pairs of poles)."""
    out = []
    for prompter in sorted((renderers or {}).keys()):
        for key, card in (renderers[prompter] or {}).items():
            if not isinstance(card, dict) or card.get("display") is not True:
                continue
            parts = []
            for field, value in card.items():
                if field == "display":
                    continue
                if field == "tensions" and isinstance(value, list):
                    pairs = [(_readable(v.get("left_pole")), _readable(v.get("right_pole")))
                             for v in value if isinstance(v, dict)]
                    pairs = [pq for pq in pairs if pq[0] or pq[1]]
                    if pairs:
                        parts.append(("tensions", "", pairs))
                elif isinstance(value, list):
                    items = [s for s in (_readable(v) for v in value) if s]
                    if items:
                        parts.append(("list", field.replace("_", " ").capitalize(), items))
                elif _readable(value):
                    parts.append(("text", "", [_readable(value)]))
            if parts:
                out.append((CARD_TITLES.get(key, key.replace("_", " ").capitalize()), parts))
    return out


def doc_blocks(text, drop_title=None):
    """A stored Markdown document as blocks: ("label", s) for a heading, ("pair", insight, evidence)
    for an "Insight: / Evidence:" item, ("bullet", s) and ("para", s). The text itself is unchanged."""
    blocks = []
    for raw in (text or "").split("\n"):
        line = raw.strip()
        if not line:
            continue
        heading = re.match(r"^#{1,6}\s+(.*)$", line)
        if heading:
            title = heading.group(1).strip()
            if not (drop_title and not blocks and title.lower() == drop_title.lower()):
                blocks.append(("label", title))
            continue
        insight = re.match(r"^[-*]\s*Insight:\s*(.*)$", line, re.I)
        evidence = re.match(r"^Evidence:\s*(.*)$", line, re.I)
        if insight:
            blocks.append(("pair", insight.group(1).strip(), ""))
        elif evidence and blocks and blocks[-1][0] == "pair" and not blocks[-1][2]:
            blocks[-1] = ("pair", blocks[-1][1], evidence.group(1).strip())
        elif re.match(r"^[-*]\s+", line):
            blocks.append(("bullet", re.sub(r"^[-*]\s+", "", line)))
        else:
            blocks.append(("para", line))
    return blocks


def onboarding_pairs(conversation):
    """[(question, [answers])], plus Aurie's messages that ask nothing (the welcome and the close).
    Each answer is the user's choice that followed the question; the app shows these as a quiz."""
    out = []
    for m in conversation:
        if m["role"] == "assistant":
            out.append([m["message"].strip(), []])
        elif out:
            out[-1][1].append(m["message"].strip())
    return [(q, a) for q, a in out]


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


CHIPS = {"aurie": "Written by Aurie", "you": "Written by you", "account": "Your account"}
LEGEND = [("account", "Your account", "the details your account holds, and your plan."),
          ("aurie", "Written by Aurie", "the profile Aurie built about you from your conversations: its "
                                        "profile document, what it has learned about you, and your profile cards."),
          ("you", "Written by you", "your answers to the onboarding quiz you took when you signed up.")]
NOTE = ("This document shows your account details and the profile Aurie builds about you, exactly as "
        "they are stored. The app shows only the profile cards; this document is the only copy of the rest "
        "that you can read. Dates are in your local timezone ({tz}).")
EXTRA_CSS = """
.author.account { background:#e8f0ea; color:#3d5a45; border:1px solid #c7d8cb; }
.cover-contents { margin-top:18px; font-size:9pt; color:#3b3746; }
.cover-contents div { padding:2px 0; }
.sechead { display:flex; align-items:center; gap:8px; border-bottom:2px solid #1f1d26; padding-bottom:4px;
  margin:0 0 12px; break-after:avoid; }
.sechead h3 { font-size:14pt; font-weight:600; color:#1f1d26; margin:0; }
.sechead .meta { font-size:8pt; color:#5f5a6b; margin-left:auto; }
.group { font-size:7.5pt; font-weight:600; color:#5f5a6b; letter-spacing:.6px; text-transform:uppercase;
  margin:14px 0 4px; }
table.acct { border-collapse:collapse; width:100%; max-width:150mm; font-size:10pt; }
table.acct td { padding:5px 0; border-bottom:1px solid #e3dfeb; }
table.acct td:first-child { color:#5f5a6b; width:42%; }
.doc { max-width:160mm; }
.doc .label { font-size:8pt; font-weight:600; color:#5f5a6b; letter-spacing:.6px; text-transform:uppercase;
  margin:16px 0 6px; break-after:avoid; }
.doc .pair { padding:6px 0; border-bottom:1px solid #ebe8f0; break-inside:avoid; }
.doc .pair b { font-weight:600; color:#24212c; }
.doc .evidence { margin:2px 0 0 14px; font-size:9pt; color:#5f5a6b; }
.doc ul { margin:4px 0 8px; padding-left:18px; }
.doc li { margin:2px 0; }
.doc p { margin:0 0 8px; }
.card { border:1px solid #d9d6df; border-radius:10px; padding:10px 14px; margin-bottom:12px; break-inside:avoid;
  max-width:160mm; }
.card .etitle { margin-top:0; }
.card p { margin:0 0 6px; }
table.tensions { border-collapse:collapse; width:100%; font-size:9.5pt; }
table.tensions td { padding:5px 4px; border-bottom:1px solid #ebe8f0; vertical-align:top; width:46%; }
table.tensions td.arrow { width:8%; text-align:center; color:#5f5a6b; }
ol.quiz { padding-left:20px; max-width:160mm; }
ol.quiz li { margin-bottom:12px; break-inside:avoid; }
.q { color:#24212c; }
.ans { margin-top:4px; padding:5px 10px; background:#ebe6f7; border:1px solid #c9bfe6; border-radius:8px;
  display:inline-block; }
.aside { font-size:9pt; color:#5f5a6b; margin:0 0 10px; max-width:160mm; }
.aside p, .q p { margin:0 0 4px; }
ol.quiz li.between { list-style:none; }
.aside b { font-weight:600; display:block; margin-bottom:2px; }
.fb { font-size:9pt; color:#24212c; margin:2px 0 6px; }
.fb .fbl { font-size:7.5pt; font-weight:600; color:#5f5a6b; letter-spacing:.4px; text-transform:uppercase; margin-right:6px; }
"""


def _chip(kind):
    return f'<span class="author {kind}">{CHIPS[kind]}</span>'


PREVIOUS_NOTE = "These ratings were given on earlier versions of your profile, which has been updated since."
# A card rating names the card by its prompter key; the Profile-tab rating has no key.
RATED_CARDS = {"core-quote-renderer": "Your quote and At your core", "inner-tensions-renderer": "Inner tensions",
               "tending-renderer": "What you are tending", "you-best-renderer": "You at your best"}


def sections(profile, account, plan, tz, ratings=None):
    """The document as data, shared by the HTML and Markdown builders. A part with nothing stored is left out."""
    out = []
    groups = account_rows(account, plan, tz)
    if groups:
        out.append({"title": "Your account", "chip": "account", "meta": "", "groups": groups})
    if (profile.get("profile") or "").strip():
        out.append({"title": "Your profile, as Aurie wrote it", "chip": "aurie",
                    "meta": f"Last updated {_when(profile.get('profileIngestedAt'), tz)}".strip(),
                    "blocks": doc_blocks(profile["profile"], drop_title="User Profile")})
    if (profile.get("insights") or "").strip():
        out.append({"title": "What Aurie has learned about you", "chip": "aurie",
                    "meta": f"Last compiled {_when(profile.get('insightsCompiledAt'), tz)}".strip(),
                    "blocks": doc_blocks(profile["insights"])})
    found = cards(profile.get("insightsRenderers"))
    if found:
        out.append({"title": "Your profile cards", "chip": "aurie",
                    "meta": f"As shown in the app · {_when(profile.get('insightsRenderersCompiledAt'), tz)}".strip(" ·"),
                    "cards": found})
    convo = [m for m in (profile.get("onboardingConversation") or [])
             if isinstance(m, dict) and m.get("role") in ("user", "assistant") and (m.get("message") or "").strip()]
    if convo:
        out.append({"title": "Your onboarding answers", "chip": "you", "meta": "", "quiz": onboarding_pairs(convo)})
    rated = [{"when": _when(r.get("createdAt"), tz), "card": RATED_CARDS.get(r.get("rendererKey"), "Your profile")
              if r.get("rendererKey") else "Your profile as a whole", "rating": r.get("rating"), "feedback": r.get("feedback")}
             for r in (ratings or []) if feedback_parts(r)]
    if rated:
        out.append({"title": "Previous profile feedback", "chip": "you", "meta": "", "ratings": rated,
                    "note": PREVIOUS_NOTE})
    return out


def _blocks_html(blocks):
    p, bullets = [], []

    def flush():
        if bullets:
            p.append("<ul>" + "".join(f"<li>{fmt_md(b)[3:-4]}</li>" for b in bullets) + "</ul>")
            bullets.clear()
    for kind, *rest in blocks:
        if kind != "bullet":
            flush()
        if kind == "label":
            p.append(f'<div class="label">{html.escape(rest[0])}</div>')
        elif kind == "pair":
            ev = f'<div class="evidence">Evidence: {fmt_md(rest[1])[3:-4]}</div>' if rest[1] else ""
            p.append(f'<div class="pair"><b>{fmt_md(rest[0])[3:-4]}</b>{ev}</div>')
        elif kind == "bullet":
            bullets.append(rest[0])
        else:
            p.append(fmt_md(rest[0]))
    flush()
    return '<div class="doc">' + "".join(p) + "</div>"


def build_html(profile, account, plan, tz, ratings=None):
    secs = sections(profile, account, plan, tz, ratings)
    title = "Your profile with Aurie"
    n_cards = sum(len(s.get("cards", [])) for s in secs)
    n_quiz = sum(sum(1 for q, a in s.get("quiz", []) if a) for s in secs)
    facts = [(a, b) for a, b in (("Profile cards", n_cards), ("Onboarding questions answered", n_quiz)) if b]
    p = [f'<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>'
         f'<style>{CSS}{EXTRA_CSS}{header_rule(title, prepared_line())}</style></head><body>']
    p.append(f'<div class="cover"><div class="mark"></div><h1>{title}</h1>'
             f'<div class="prepared">{prepared_line()}</div>'
             + ('<div class="facts">' + "".join(f'<div class="fact"><span>{a}</span><span>{num(b)}</span></div>'
                                               for a, b in facts) + "</div>" if facts else "")
             + '<div class="legend">' + "".join(f'<div>{_chip(k)}{html.escape(b)}</div>' for k, _, b in LEGEND)
             + '</div><div class="cover-contents">'
             + "".join(f'<div>{i}. {html.escape(s["title"])}</div>' for i, s in enumerate(secs, 1))
             + f'</div><div class="note">{html.escape(NOTE.format(tz=tz))}</div></div>')
    for i, s in enumerate(secs):
        p.append(f'<div class="day{" first" if i == 0 else ""}"><div class="sechead">{_chip(s["chip"])}'
                 f'<h3>{html.escape(s["title"])}</h3><span class="meta">{html.escape(s["meta"])}</span></div>')
        if s.get("note"):
            p.append(f'<div class="aside">{html.escape(s["note"])}</div>')
        for group, rows in s.get("groups", []):
            p.append(f'<div class="group">{html.escape(group)}</div><table class="acct">'
                     + "".join(f"<tr><td>{html.escape(a)}</td><td>{html.escape(b)}</td></tr>" for a, b in rows)
                     + "</table>")
        if "blocks" in s:
            p.append(_blocks_html(s["blocks"]))
        for ctitle, parts in s.get("cards", []):
            p.append(f'<div class="card"><div class="etitle">{html.escape(ctitle)}</div>')
            for kind, sub, items in parts:
                if kind == "tensions":
                    p.append('<table class="tensions">' + "".join(
                        f'<tr><td>{html.escape(l)}</td><td class="arrow">↔</td><td>{html.escape(r)}</td></tr>'
                        for l, r in items) + "</table>")
                elif kind == "list":
                    p.append(f'<div class="sec">{html.escape(sub)}</div><div class="doc"><ul>'
                             + "".join(f"<li>{html.escape(i)}</li>" for i in items) + "</ul></div>")
                else:
                    p.append("".join(fmt_md(i) for i in items))
            p.append("</div>")
        if "quiz" in s:
            quiz = s["quiz"]
            answered = [i for i, (q, a) in enumerate(quiz) if a]
            first, last = (answered[0], answered[-1]) if answered else (len(quiz), len(quiz))
            for q, _ in quiz[:first]:
                p.append(f'<div class="aside"><b>Aurie introduced the quiz with:</b>{fmt_md(q)}</div>')
            items, n = [], 0
            for q, a in quiz[first:last + 1]:
                if a:
                    n += 1
                    items.append(f'<li value="{n}"><div class="q">{fmt_md(q)}</div>'
                                 + "".join(f'<div class="ans">{html.escape(x)}</div>' for x in a) + "</li>")
                else:  # an Aurie message between questions, with no answer: kept in place, unnumbered
                    items.append(f'<li class="between"><div class="aside">{fmt_md(q)}</div></li>')
            p.append('<ol class="quiz">' + "".join(items) + "</ol>")
            for n, (q, _) in enumerate(quiz[last + 1:] if answered else []):
                p.append(f'<div class="aside">{"<b>Aurie closed with:</b>" if n == 0 else ""}{fmt_md(q)}</div>')
        for r in s.get("ratings", []):
            p.append(f'<div class="entry"><div class="who">{html.escape(r["when"])} &middot; {html.escape(r["card"])}</div>'
                     f'{feedback_html(r)}</div>')
        p.append("</div>")
    p.append("</body></html>")
    return "".join(p)


def build_markdown(profile, account, plan, tz, ratings=None):
    secs = sections(profile, account, plan, tz, ratings)
    out = ["# Your profile with Aurie", "", prepared_line(), "", *[f"- **{a}**: {b}" for _, a, b in LEGEND], "",
           "> " + NOTE.format(tz=tz), ""]
    for s in secs:
        out += ["---", "", f"## {s['title']}", "", f"**{CHIPS[s['chip']]}**" + (f" · *{s['meta']}*" if s["meta"] else ""), ""]
        for group, rows in s.get("groups", []):
            out += [f"*{group}*", "", "| | |", "| --- | --- |", *[f"| {a} | {md_safe(b)} |" for a, b in rows], ""]
        for kind, *rest in s.get("blocks", []):
            if kind == "label":
                out += [f"**{md_safe(rest[0])}**", ""]
            elif kind == "pair":
                out += [f"- **{md_safe(rest[0])}**" + (f"  \n  Evidence: {md_safe(rest[1])}" if rest[1] else "")]
            elif kind == "bullet":
                out += [f"- {md_safe(rest[0])}"]
            else:
                out += ["", md_safe(rest[0]), ""]
        if s.get("blocks"):
            out.append("")
        for ctitle, parts in s.get("cards", []):
            out += ["", f"### {md_safe(ctitle)}", ""]
            for kind, sub, items in parts:
                if kind == "tensions":
                    out += [f"- {md_safe(l)} ↔ {md_safe(r)}" for l, r in items] + [""]
                elif kind == "list":
                    out += [f"*{sub}*", ""] + [f"- {md_safe(i)}" for i in items] + [""]
                else:
                    out += [md_safe(i) for i in items] + [""]
        if s.get("note"):
            out += [f"*{s['note']}*", ""]
        for r in s.get("ratings", []):
            out += [f"- {r['when']} · {r['card']} · " + feedback_md(r).replace("*Your feedback:* ", ""), ""]
        n = 0
        for q, a in s.get("quiz", []):
            if a:
                n += 1
                # Continuation lines are indented, so a question of several paragraphs stays one list item.
                question = md_safe(q.strip()).replace("\n", "\n   ")
                out += [f"{n}. {question}", "", *[f"   **{CHIPS['you']}**: {md_safe(x)}" for x in a], ""]
            else:
                out += [md_safe(q.strip()), ""]
    return "\n".join(out) + "\n"


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


def main(argv=None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--uid", required=True, help="The user's Firebase UID, from a trusted source")
    ap.add_argument("--from-file", required=True, help="The profile file from eng:query-user-export-data")
    ap.add_argument("--ratings-file", help="The profileRatings file from eng:query-user-export-data (optional)")
    ap.add_argument("--host", default=os.environ.get("AURA_API_HOST", DEFAULT_HOST),
                    help=f"API host for the account page (default {DEFAULT_HOST}, or $AURA_API_HOST)")
    ap.add_argument("--format", choices=("pdf", "md", "both"), default="pdf")
    ap.add_argument("--out", help="Output path (default ./aurie-profile-<uid prefix>.pdf); the extension is swapped per format")
    ap.add_argument("--timezone", help="Override the user's timezone (IANA name)")
    ap.add_argument("--html-only", action="store_true", help="Write HTML and skip PDF rendering")
    ap.add_argument("--keep-intermediates", action="store_true", help="Keep the HTML (it holds the profile)")
    args = ap.parse_args(argv)

    uid = args.uid
    if not uid.strip() or len(uid) > 256 or re.search(r"\s", uid):
        raise SystemExit("Refusing to run: that is not a usable user id.")
    token = os.environ.get("AURA_API_TOKEN")
    if not token:
        raise SystemExit("AURA_API_TOKEN is not set: the account page reads UserService and PaymentService.")
    profile = load_profile(args.from_file, uid)
    ratings = load_ratings(args.ratings_file, uid)
    account, plan = fetch_account(args.host, token, uid)
    tz_name = args.timezone or account.get("timezone") or DEFAULT_TZ
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        raise SystemExit(f"Unknown timezone {tz_name!r}; pass --timezone with an IANA name.")

    safe = re.sub(r"[^A-Za-z0-9_-]", "-", uid_prefix(uid))
    base = (Path(args.out) if args.out else Path(f"aurie-profile-{safe}.pdf")).expanduser().resolve()
    if not base.parent.exists():
        base.parent.mkdir(parents=True, mode=0o700)
    if args.format in ("md", "both"):
        md_path = base if base.suffix.lower() == ".md" else base.with_suffix(".md")
        _private_write(md_path, build_markdown(profile, account, plan, tz, ratings))
        print(f"Markdown: {md_path}")
    if args.format in ("pdf", "both") or args.html_only:
        pdf_path = base if base.suffix.lower() == ".pdf" else base.with_suffix(".pdf")
        keep = args.keep_intermediates or args.html_only
        html_path = pdf_path.with_suffix(".html") if keep else Path(tempfile.mkdtemp(prefix="aurie-profile-")) / "export.html"
        _private_write(html_path, build_html(profile, account, plan, tz, ratings))
        if args.html_only:
            print(f"HTML: {html_path}")
        else:
            try:
                render_pdf(html_path, pdf_path)
            finally:  # the HTML holds the profile: never left behind, even when rendering fails
                if not keep:
                    shutil.rmtree(html_path.parent, ignore_errors=True)
            os.chmod(pdf_path, 0o600)
            if not keep:
                print("  intermediate HTML deleted")
            print(f"PDF: {pdf_path}")
    secs = sections(profile, account, plan, tz, ratings)
    print(f"  profile | uid {uid_prefix(uid)}… | sections: {', '.join(s['title'] for s in secs)} | "
          f"plans in force: {len(plan or [])}")


if __name__ == "__main__":
    main()
