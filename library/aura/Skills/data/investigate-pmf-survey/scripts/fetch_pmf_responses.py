#!/usr/bin/env python3
"""Fetch + parse + filter `New PMF Survey Response` k8bot messages from a Slack channel.

Deterministic core of the `data:investigate-pmf-survey` skill: pulls the channel history
over a window, parses the PMF shape, drops test entries, and emits structured JSON the
SKILL.md drives the one-at-a-time `profile-user` loop from.

Auth: SLACK_BOT_TOKEN (env, or --token). The bot must be a member of the channel.

Slack reads go through the shared `slack_read` helper (copied into this skill's
`scripts/` dir — the canonical `shared:read-slack` library; see AINative `CLAUDE.md`
→ "Canonical source for `slack_read.py`"); it owns Slack transport + 429-retry. The
Aura API enrichment still uses `requests` directly (bundles certifi — never urllib).

Forces UTF-8 stdout (Windows cp1252 would choke on → / — etc.).
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
import pathlib
from collections import Counter
from datetime import datetime, timezone

try:
    import requests
except ImportError:  # pragma: no cover
    raise SystemExit("fetch_pmf_responses.py requires 'requests' (pip install requests).")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from slack_read import SlackReadError, history, message_by_ts  # noqa: E402
import internal_accounts  # noqa: E402


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


DEFAULT_CHANNEL = "C0AENS8D48N"  # #ai-feedback
WORKSPACE = "auramobile"
HEADER = "New PMF Survey Response"

# Feedback text (lowercased, stripped) that marks a test entry.
TEST_EXACT = {"test", "testing", "test dev"}

# Answers that are not free text. Not test entries either — a real respondent who
# picked a canned option or left the field blank — so they are counted and treated
# as empty steering rather than dropped.
#
# The five follow-up options offered on the "Not disappointed" path, as the machine
# values the app posts. Source of truth: AuraConsumerApp
# `Services/PMFSurvey/Models/PMFNotDisappointedOption.swift`. Keep this in sync with
# that enum — NOT with whatever happens to appear in the channel, which can only
# show the options someone has picked so far.
SURVEY_CHOICE_VALUES = frozenset({
    "did_not_help",
    "did_not_need",
    "did_not_understand",
    "only_tried_briefly",
    "other",
})

# Not a choice value: IntelligenceService `src/models/survey.js` renders
# `${result.feedback || 'No Feedback'}` when the survey's optional `feedback` is
# null, so this is the blank-field placeholder and appears on all three sentiment
# paths — unlike the five above, which are "Not disappointed" only.
BLANK_FEEDBACK_PLACEHOLDER = frozenset({"no feedback"})

# Neither a choice value nor the placeholder: typed text that answers the question
# without saying anything actionable. Deliberately counted as non-free-text — it
# carries no signal to quote, steer a profile with, or put in a digest snippet.
# Reviewed and kept; not a backlog item.
LOW_SIGNAL_TYPED_ANSWERS = frozenset({"not sure yet"})

NON_FREE_TEXT_ANSWERS = (
    SURVEY_CHOICE_VALUES | BLANK_FEEDBACK_PLACEHOLDER | LOW_SIGNAL_TYPED_ANSWERS
)

# Slack renders the k8bot PMF body as a blockquote (since 2026-09-15), so
# `conversations.history` delivers each quoted line HTML-escaped and marker-prefixed:
# `&gt; *Very disappointed…*` / `&gt; No Feedback`. One leading marker, escaped or literal.
BLOCKQUOTE_MARKER_RE = re.compile(r"^\s*(?:&gt;|>)\s?")


def normalize_quoted_line(line: str, quoted: bool) -> str:
    """Undo Slack's rendering of one k8bot body line: blockquote marker, then entities.

    `quoted` gates the marker strip on positive evidence that the MESSAGE is
    blockquote-rendered (the sentiment line carries the marker too — true in 9/9
    observed cases, and never one line alone). Stripping unconditionally would eat a
    respondent's own leading `>`, which is content, not markup.

    Strip before unescape, never after: unescaping first lets a literal `&amp;gt;`
    someone typed become a marker that the strip then eats.
    """
    if quoted:
        line = BLOCKQUOTE_MARKER_RE.sub("", line, count=1)
    # Slack escapes `&`, `<` and `>` across the whole line, so an ampersand or angle
    # bracket in genuine free text arrives as an entity. Exactly one pass.
    return html.unescape(line)


# The Aura user id space as it actually reaches this footer, measured over 857
# messages: a 28-char Firebase uid (853), a provider-prefixed auth id (3), and a
# legacy UUID from an account that predates Firebase (1 — a 2017 signup who joined
# Aurie in 2026). Kept as explicit alternatives rather than a permissive `\S+`:
# the id is the last field on the line, so anything loose here turns a malformed
# footer into a fabricated user id.
AURA_USER_ID_RE = (
    r"[A-Za-z0-9]{20,30}"
    r"|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"|(?:google|apple|facebook|twitter|microsoft):\S+"
)

SENTIMENT_MAP = {
    "Very": "Very disappointed",
    "Somewhat": "Somewhat disappointed",
    "Not": "Not disappointed",
}
SENTIMENT_KEYS = ("Very disappointed", "Somewhat disappointed", "Not disappointed")

# --- Segmentation (PROD-2665) ------------------------------------------------
# A respondent is "content-anchored" (vs "Aurie-anchored") when they have an
# established Aura *content* listening habit — a fallback that depresses the
# Aurie-specific PMF answer independently of how well Aurie fits them. Signals
# come from the Aura API user record (GET /users/<id>) + subscriptions list.
CONTENT_LISTEN_THRESHOLD = 25  # listenedCount (lifetime listens) >= this → content-anchored
CONTENT_STREAK_THRESHOLD = 7   # streakMax (max listen streak)    >= this → content-anchored

# Tenure buckets, in days since aiCreatedAt (Aurie join), falling back to
# createdAt (account signup). The PMF question is Aurie-specific, so the
# habit-formation clock starts at the Aurie join — NOT at account age.
TENURE_ORDER = ("0-7", "8-29", "30+", "unknown")

# Internal / non-customer accounts excluded from PMF aggregates (the broadened
# AURIE-CREATOR rule). Roles mirror UserService config `userRolesList` plus the
# privileged/internal roles the comment block there keeps DB-only.
INTERNAL_ROLES = frozenset(
    {"admin", "agent", "service", "system", "creator", "audio-engineer"}
)
INTERNAL_EMAIL_DOMAINS = frozenset({"aurahealth.io"})

# A local-part token that marks a disposable test alias, on ANY domain. The
# `Team/Internal Accounts` page records the precedent: Yawar's QA accounts run on
# `<redacted-email>` and the page's instruction is to "match the email
# pattern, not just the listed UIDs". Matched per token (local part split on
# `. + _ -`), never as a substring — `contest@…` must not match.
TEST_LOCAL_TOKEN_RE = re.compile(r"^test(ing)?\d*$")

# Slack renders a bare address in message text as `<mailto:a@b|a@b>`, and a URL as
# `<https://x|label>`. The PMF footer's name slot is free text, so whatever the
# survey captured lands there verbatim — including an address.
SLACK_ENTITY_RE = re.compile(r"<(mailto:|https?://)?([^|>]*)(?:\|([^>]*))?>")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")

DEFAULT_AURA_BASE_URL = "https://services.aurahealth.io"


def resolve_window(window: str) -> tuple[float, float, str]:
    """Return (oldest_epoch, latest_epoch, human_label) for a window spec.

    Accepts `7d` / `14d` / `30d` / `Nd` or an inclusive `YYYY-MM-DD..YYYY-MM-DD` range.
    """
    now = time.time()
    if ".." in window:
        start_s, end_s = window.split("..", 1)
        start = datetime.strptime(start_s.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end = datetime.strptime(end_s.strip(), "%Y-%m-%d").replace(
            hour=23, minute=59, second=59, tzinfo=timezone.utc
        )
        return start.timestamp(), end.timestamp(), f"{start_s.strip()}..{end_s.strip()}"
    m = re.fullmatch(r"(\d+)d", window.strip())
    if not m:
        raise SystemExit(f"Unrecognised --window '{window}'. Use Nd or YYYY-MM-DD..YYYY-MM-DD.")
    days = int(m.group(1))
    return now - days * 86400, now, f"last {days}d"


def fetch_messages(
    token: str, channel: str, oldest: float, latest: float, single_ts: str | None
) -> list[dict]:
    """Return raw messages in [oldest, latest], or just the single message at single_ts.

    Thin wrapper over the shared `slack_read` helper. Raises `SlackReadError` on a
    Slack error — caught in main() and surfaced as a clean exit.
    """
    if single_ts:
        return message_by_ts(channel, single_ts, token=token)
    return history(channel, token=token, oldest=oldest, latest=latest)


def ts_to_permalink(channel: str, ts: str) -> str:
    return f"https://{WORKSPACE}.slack.com/archives/{channel}/p{ts.replace('.', '')}"


def uid_prefix(uid: str) -> str:
    """Return a short, non-identifying prefix of `uid` that still distinguishes users.

    A bare `uid[:8]` assumes the first 8 characters are distinguishing. For
    provider-prefixed ids they are not: every `google:112...` yields `"google:1"`,
    so distinct users collapse onto one value.

    So the 8 characters are taken from the part *after* the provider, and the
    provider is kept as a readable label:

        google:112233445566778899  ->  google:11223344
        FhE3nMkP8yQ2wLxV0tRb9dJc   ->  FhE3nMkP        (unchanged)

    Firebase-28 ids (no colon) are untouched, which is 99.8% of the corpus — the
    fix costs no churn on the common case.

    Firebase **push keys** (20 chars, leading `-`) are deliberately left on the
    plain-truncation path. Their first 8 characters are the timestamp component,
    so ids minted close together share a prefix — weaker than intended, but a
    different problem with a different remedy (it needs the random suffix, not the
    provider split), and re-deriving them would churn ~47k ids for no gain here.

    PII: the return value is a prefix, never a full uid. The provider label is not
    personal data.

    Here it labels the rows of `internal_excluded`, where provider-prefixed ids are
    the accounts the block exists to surface — the `Team/Internal Accounts`
    allowlist entries the `@aurahealth.io` / role rules cannot catch are personal
    Google logins, so the collapse makes two excluded employees indistinguishable.

    Duplicated in `report-conversation-patterns/scripts/sample.py` (canonical) and
    `harvest-conversation-patterns/scripts/fetch_sweep_analyses.py` — skills are
    self-contained, so the copies must stay in step. `tests/test_uid_prefix_rule.py`
    holds them to it, and fails on a fourth implementation under any filename.
    """
    uid = uid or ""
    if ":" in uid:
        provider, _, rest = uid.partition(":")
        return f"{provider}:{rest[:8]}"
    return uid[:8]


def parse_footer_name(slot: str) -> tuple[str, str]:
    """Split the PMF footer's name slot into (display_name, email).

    The footer is `<First Name> - <UID>`, but the name slot is whatever the survey
    captured — for QA accounts that is an address, which Slack wraps as
    `<mailto:a@b|a@b>`. Left unhandled it flows into `first_name` as raw link
    markup and from there onto a card, which both looks wrong and puts a full
    address on a Slack surface the PII rule forbids it on.

    Returns the email so the caller can classify on it (see `classify_footer_email`);
    the entry keeps only its **domain**, never the address.
    """
    email = ""

    def unwrap(m: re.Match) -> str:
        scheme, target, label = m.group(1) or "", (m.group(2) or "").strip(), m.group(3)
        nonlocal email
        if not email and scheme.startswith("mailto") and EMAIL_RE.match(target):
            email = target.lower()
        return label if label is not None else target

    display = SLACK_ENTITY_RE.sub(unwrap, slot).strip()
    if not email and EMAIL_RE.match(display):
        email = display.lower()
    if EMAIL_RE.match(display):
        display = ""  # the slot is an address, not a name — never carry it forward
    return display, email


def classify_footer_email(email: str) -> tuple[bool, str]:
    """Return (is_internal, reason) from the footer address alone — no Aura API.

    Same two email signals `classify_internal` applies to the Aura user record,
    but derived from the message the parser already has. That independence is the
    point: `GET /users/<id>` 404s on deleted and QA accounts — the very accounts
    the rule exists to catch — so a check that reads only the user record cannot
    fire on them, even with the address sitting in the footer.
    """
    if not email or "@" not in email:
        return False, ""
    local, _, domain = email.partition("@")
    if domain in INTERNAL_EMAIL_DOMAINS:
        return True, f"email_domain={domain}"
    if any(TEST_LOCAL_TOKEN_RE.match(t) for t in re.split(r"[.+_-]", local) if t):
        return True, "email_test_alias"
    return False, ""


def apply_footer_identity(
    entries: list[dict],
    allow_emails: set[str] | frozenset = frozenset(),
    allow_patterns: set[str] | frozenset = frozenset(),
) -> None:
    """Tag each entry in place from its own footer: email domain + internal verdict.

    Runs before the Aura API join so the verdict survives an unresolvable UID.
    `enrich_entries` and `apply_allowlist` then fold their signals in additively —
    neither may clear a flag this set.

    The allowlist's email rows (`allow_emails`, and wildcard `allow_patterns` such as
    a test-account alias family) are matched here because this is the only place
    the full address exists — the entry keeps just the domain.
    """
    for e in entries:
        display, email = parse_footer_name(e.get("first_name", ""))
        e["first_name"] = display
        e["email_domain"] = email.rsplit("@", 1)[-1] if "@" in email else ""
        if internal_accounts.email_matches(email, set(allow_emails), set(allow_patterns)):
            e["is_internal"] = True
            e["internal_reason"] = f"allowlist:*@{e['email_domain']}"
            continue
        is_internal, reason = classify_footer_email(email)
        e["is_internal"] = is_internal
        e["internal_reason"] = reason


def parse_entry(msg: dict, channel: str) -> dict | None:
    """Parse one `New PMF Survey Response` message into a structured entry, or None."""
    text = (msg.get("text") or "").strip()
    if not text.startswith(HEADER):
        return None
    ts = msg.get("ts", "")

    lines = [l.strip() for l in text.split("\n") if l.strip()]
    # Expected shape (all 4 elements required):
    #   lines[0]    = "New PMF Survey Response"
    #   lines[1]    = "*<Sentiment> if could no longer use Aurie*"
    #   lines[2..-2]= feedback (always a single line in practice; join if multi)
    #   lines[-1]   = "<First Name> - <UID>"
    if len(lines) < 4:
        return None

    # Sentiment
    s_m = re.search(r"\*(Very|Somewhat|Not) disappointed", lines[1])
    sentiment = SENTIMENT_MAP.get(s_m.group(1), "") if s_m else ""
    if not sentiment:
        return None  # not a standard PMF response

    # UID + first_name from last line. The id is either a 20-30 char Firebase
    # UID or a provider-prefixed auth id (e.g. `google:1115…`, `apple:…`) — the
    # latter is how employees signed in with a personal SSO account show up, and
    # the exact case that motivated the allowlist (PROD-3225). Accept both so
    # those entries aren't silently dropped before the internal-account overlay.
    # The same 2026-09-15 template change that introduced the blockquote also added
    # a `- Day <N> -` tenure segment to the footer. It is OPTIONAL here because both
    # shapes are live in any window longer than a day. Non-greedy on the name so the
    # segment is captured rather than swallowed into `first_name` — a greedy `.+`
    # yields a display name of "Renee - Day 4", which reaches the Slack card.
    # The producer joins `[givenName, day, id].filter(Boolean)` with " - ", so BOTH
    # leading parts are independently optional: a user with no display name yields
    # `Day 5 - <uid>` or a bare `<uid>`. The lookahead stops the name group eating a
    # `Day N` that is really the day segment (which reads as a person named "Day 5"),
    # and a bare id no longer drops the respondent entirely.
    uid_m = re.match(
        r"^(?:(?!Day \d+ - )(.+?) - )?(?: ?Day (\d+) - )?"
        rf"({AURA_USER_ID_RE})$",
        lines[-1],
    )
    if not uid_m:
        return None
    first_name = (uid_m.group(1) or "").strip()
    # k8bot's own day-since-join count. Captured, not consumed: `tenure_bucket` stays
    # derived from the Aura API's `aiCreatedAt` (see `enrich_entries`), so this is an
    # independent signal and a free cross-check, not a silent second source of truth.
    day_n = int(uid_m.group(2)) if uid_m.group(2) else None
    uid = uid_m.group(3)

    # Feedback: everything between the sentiment line and the UID line.
    #
    # Normalised HERE rather than at render time, so every consumer — the default
    # detector below, `apply_test_filter`, the cadence card's quoted line, the
    # `steering` / `context.stated` strings handed to `data:profile-user`, and the
    # digest's per-sentiment snippets — sees the same clean text. Leaving the raw
    # form on the entry would make each of them responsible for remembering, and
    # the raw line stays recoverable from the entry's own `permalink`.
    quoted = bool(BLOCKQUOTE_MARKER_RE.match(lines[1]))
    feedback = "\n".join(normalize_quoted_line(l, quoted) for l in lines[2:-1])
    fb_lower = feedback.strip().lower()
    feedback_is_default = fb_lower in NON_FREE_TEXT_ANSWERS or not fb_lower

    when = (
        datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        if ts else ""
    )
    return {
        "ts": ts,
        "when": when,
        "uid": uid,
        "first_name": first_name,
        "day_n": day_n,
        "sentiment": sentiment,
        "feedback": feedback,
        "feedback_is_default": feedback_is_default,
        "permalink": ts_to_permalink(channel, ts) if ts else "",
        "thread_ts": ts,
    }


def partition_messages(raw: list[dict], channel: str) -> tuple[list[dict], list[dict]]:
    """Split fetched messages into parsed entries and header-bearing parse failures.

    A message carrying the header that does not parse is REPORTED, never merely
    skipped. The silent skip is why a legacy-UUID respondent went unparsed for
    months and why the 2026-09-15 template change produced no signal at all — a
    dropped message leaves nothing behind to notice, so the run looks clean and
    simply has fewer rows. `unparsed` is the tripwire for the next producer change.

    Messages without the header are not failures: the channel carries other traffic.
    """
    entries: list[dict] = []
    unparsed: list[dict] = []
    for msg in raw:
        entry = parse_entry(msg, channel)
        if entry:
            entries.append(entry)
            continue
        text = (msg.get("text") or "").strip()
        if not text.startswith(HEADER):
            continue
        ts = msg.get("ts", "")
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        unparsed.append({
            "ts": ts,
            "permalink": ts_to_permalink(channel, ts) if ts else "",
            "footer": lines[-1] if lines else "",
        })
    return entries, unparsed


def apply_test_filter(entries: list[dict]) -> None:
    """Tag each entry in place with is_test + test_reason.

    PMF does not apply a UID-blast filter (unlike cancellations) because the PMF survey
    fires at multiple lifecycle points per user — genuine re-submissions are expected.
    Only feedback-text-based test detection applies.
    """
    for e in entries:
        fb = e["feedback"].strip().lower()
        reasons = []
        if fb in TEST_EXACT or (fb.startswith("test") and fb not in NON_FREE_TEXT_ANSWERS):
            reasons.append("feedback-text-test")
        e["is_test"] = bool(reasons)
        e["test_reason"] = ",".join(reasons)


# --- Segmentation enrichment (PROD-2665) ------------------------------------
#
# For every real respondent we join the Slack PMF message against the Aura API
# user record so the aggregate digest can split scores by tenure + content-habit
# and exclude internal accounts. This is deterministic and runs over the FULL
# respondent population (the Sean-Ellis denominator) — independent of whichever
# subset the orchestrator later deep-profiles via `profile-user`. It is
# best-effort: a missing user record or a failed call never drops the entry, it
# just leaves it `tenure_bucket="unknown"` / `content_anchored=None`.


def aura_request(
    session: requests.Session, method: str, url: str, json_body: dict | None = None
) -> tuple[int | None, object]:
    """One Aura API call with retry on 429/5xx. Returns (status_code, parsed_json).

    Never raises — on a transport error or exhausted retries returns (None, None)
    so the caller can degrade per-entry instead of aborting the whole sweep.
    """
    for attempt in range(4):
        try:
            if method == "GET":
                r = session.get(url, timeout=30)
            else:
                r = session.post(url, json=json_body or {}, timeout=30)
        except requests.RequestException:
            if attempt == 3:
                return None, None
            time.sleep(1.0 + attempt)
            continue
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(min(float(r.headers.get("Retry-After", 1.0 + attempt)), 5.0))
            continue
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, None
    return None, None


def parse_ts(value: object) -> datetime | None:
    """Parse an ISO-8601 timestamp string into an aware UTC datetime, or None."""
    if not value:
        return None
    s = str(value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        m = re.match(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})", s)
        if m:
            try:
                dt = datetime.strptime(
                    f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M:%S"
                ).replace(tzinfo=timezone.utc)
            except ValueError:
                return None
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def days_between(start_value: object, as_of_epoch: float) -> int | None:
    """Whole days from an ISO-8601 timestamp until `as_of_epoch` (UTC seconds), or None.

    Tenure is measured **as of when the user answered the PMF survey** (the entry's
    Slack ts), not as of now — the confound is how mature the user was at survey
    time, so re-running the same window later must not age respondents forward into
    a higher tenure bucket.
    """
    dt = parse_ts(start_value)
    if dt is None:
        return None
    return max(0, int((as_of_epoch - dt.timestamp()) // 86400))


def hours_between(start_value: object, as_of_epoch: float) -> float | None:
    """Fractional hours from an ISO-8601 timestamp until `as_of_epoch`, or None.

    Companion to `days_between`, and the finer resolution is the whole point:
    `tenure_days` floors, so a survey answered 24 minutes after signup and one
    answered 20 hours after both land on `0` and become indistinguishable. That
    collapse is what let a 24-minute Sean-Ellis answer sit inside the Day 0-7
    bucket looking like ordinary tenure bias (PROD-4170).

    Computed here rather than left to the consumer: the entry carries
    `tenure_anchor` as a *field name* (`"aiCreatedAt"` / `"createdAt"`), never a
    timestamp, so a downstream reader has nothing to subtract from.
    """
    dt = parse_ts(start_value)
    if dt is None:
        return None
    return max(0.0, (as_of_epoch - dt.timestamp()) / 3600.0)


def tenure_bucket(days: int | None) -> str:
    """Map days-since-Aurie-join to a bucket label."""
    if days is None:
        return "unknown"
    if days <= 7:
        return "0-7"
    if days <= 29:
        return "8-29"
    return "30+"


def classify_content_habit(user: dict, has_content_sub: bool) -> tuple[bool, str]:
    """Return (content_anchored, signal). First matching signal wins.

    `listenedCount` is the lifetime *count* of content listens (the ticket's
    "lifetime listens"); `listenedTotal` is cumulative listen minutes and is NOT
    used here.
    """
    if has_content_sub:
        return True, "content_sub"
    listened = user.get("listenedCount") or 0
    if isinstance(listened, (int, float)) and listened >= CONTENT_LISTEN_THRESHOLD:
        return True, f"listens={int(listened)}"
    streak_max = user.get("streakMax") or 0
    if isinstance(streak_max, (int, float)) and streak_max >= CONTENT_STREAK_THRESHOLD:
        return True, f"streakMax={int(streak_max)}"
    return False, "none"


def apply_allowlist(entries: list[dict], internal_ids: set[str], internal_names: set[str]) -> None:
    """Overlay the `Team/Internal Accounts` allowlist onto entries, in place.

    The orchestrator pre-loads that AI Knowledge page (it can't be read from this
    script) and passes the identifiers via --internal-ids / --internal-names. This
    runs independent of the Aura API join, so it catches employees on personal
    accounts (e.g. a founder's `google:…` login) that carry none of the
    role / isAIReviewer / @aurahealth.io signals — and it works even when
    enrichment didn't run (no token). A confirmed id match is a hard internal
    flag; a name-only match is recorded as *possible* (never auto-excluded, since
    real users share first names). Origin: PROD-3225.
    """
    ids = {i.strip() for i in internal_ids if i.strip()}
    names = {n.strip().lower() for n in internal_names if n.strip()}
    for e in entries:
        uid = e.get("uid", "")
        if uid and uid in ids:
            e["is_internal"] = True
            # Keep any existing Aura-API reason but lead with the allowlist hit.
            prior = e.get("internal_reason") or ""
            # Prefixed, not the raw id: the digest prints this reason, and the PII
            # rule for every Slack surface here is UID-prefix only. The rule that
            # fired is what a reader needs, and the prefix still identifies which
            # allowlist entry matched.
            e["internal_reason"] = f"allowlist:{uid_prefix(uid)}" + (f"; {prior}" if prior else "")
            continue
        if names and (e.get("first_name") or "").strip().lower() in names:
            # Soft signal only — flag for the digest, do NOT set is_internal.
            e["allowlist_name_match"] = e.get("first_name", "")


def classify_internal(user: dict, has_creator_sub: bool) -> tuple[bool, str]:
    """Return (is_internal, reason). First matching signal wins. isCoach is
    intentionally NOT an exclusion signal here (a coach can be a genuine Aurie
    user) — it is recorded separately as a flag for the digest to weigh.

    The `Team/Internal Accounts` allowlist is applied separately (see
    apply_allowlist) so it works even when this Aura-API join didn't run."""
    role = (user.get("role") or "").strip().lower()
    if role in INTERNAL_ROLES:
        return True, f"role={role}"
    if user.get("isAIReviewer"):
        return True, "isAIReviewer"
    email = (user.get("email") or "").strip().lower()
    domain = email.rsplit("@", 1)[-1] if "@" in email else ""
    if domain in INTERNAL_EMAIL_DOMAINS:
        return True, f"email_domain={domain}"
    if user.get("test"):
        return True, "test"
    if has_creator_sub:
        return True, "ai_lifetime_free"
    return False, ""


def parse_subscription_rows(data: object) -> list[dict] | None:
    """Normalise the /subscriptions/list response into a list of rows, or None."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("result", "results", "subscriptions"):
            rows = data.get(key)
            if isinstance(rows, list):
                return rows
    return None


def enrich_entries(entries: list[dict], token: str, base_url: str) -> None:
    """Join each entry against the Aura API and attach segmentation fields in place."""
    session = requests.Session()
    session.headers.update(
        {"Authorization": f"Bearer {token}", "User-Agent": user_agent(), "Content-Type": "application/json"}
    )
    for e in entries:
        uid = e["uid"]
        status, data = aura_request(session, "GET", f"{base_url}/users/{uid}")
        user = data if status == 200 and isinstance(data, dict) else None
        if not user:
            # Deliberately does NOT touch is_internal / internal_reason: the
            # footer-email verdict is the only one an unresolvable UID has, so
            # resetting it here would admit every QA account whose UID 404s.
            e.update(
                {
                    "aura_found": False,
                    "tenure_days": None,
                    "tenure_hours": None,
                    "tenure_anchor": None,
                    "tenure_bucket": "unknown",
                    "content_anchored": None,
                    "content_signal": "unknown",
                    "is_coach": None,
                    "enrichment_error": f"users_endpoint_status={status}",
                }
            )
            time.sleep(0.1)
            continue

        # Tenure as of survey time (the entry's Slack ts) — Aurie join first,
        # fall back to account signup. Falls back to "now" only if ts is missing.
        as_of = float(e["ts"]) if e.get("ts") else time.time()
        if user.get("aiCreatedAt"):
            anchor_value, tenure_anchor = user.get("aiCreatedAt"), "aiCreatedAt"
        else:
            anchor_value, tenure_anchor = user.get("createdAt"), "createdAt"
        tenure_days = days_between(anchor_value, as_of)
        tenure_hours = hours_between(anchor_value, as_of)

        # Subscriptions (best-effort) — canonical content-sub + creator signals.
        sub_status, sub_data = aura_request(
            session, "POST", f"{base_url}/subscriptions/list/{uid}", {"includeDeleted": True}
        )
        rows = parse_subscription_rows(sub_data) if sub_status == 200 else None
        has_content_sub = False
        has_creator_sub = False
        if rows is None:
            # Fall back to the user-row premium fields (they mirror the type:content sub).
            has_content_sub = bool(user.get("premium")) or bool(user.get("lifetime"))
            e["subs_error"] = True
        else:
            for row in rows:
                if not isinstance(row, dict):
                    continue
                rtype = (row.get("type") or "").lower()
                provider = (row.get("provider") or "").lower()
                if rtype == "content":
                    has_content_sub = True
                if provider == "aura" and (row.get("lifetime") or row.get("free")):
                    has_creator_sub = True

        content_anchored, content_signal = classify_content_habit(user, has_content_sub)
        api_internal, api_reason = classify_internal(user, has_creator_sub)

        # Additive, never clobbering — the footer-email verdict already ran.
        prior_reason = e.get("internal_reason") or ""
        reasons = [r for r in (prior_reason, api_reason) if r]
        e.update(
            {
                "aura_found": True,
                "tenure_days": tenure_days,
                "tenure_hours": tenure_hours,
                "tenure_anchor": tenure_anchor,
                "tenure_bucket": tenure_bucket(tenure_days),
                "content_anchored": content_anchored,
                "content_signal": content_signal,
                "is_internal": bool(e.get("is_internal")) or api_internal,
                "internal_reason": "; ".join(dict.fromkeys(reasons)),
                "is_coach": bool(user.get("isCoach")),
            }
        )
        # The user record's email can confirm a domain the footer never carried.
        if not e.get("email_domain"):
            api_email = (user.get("email") or "").strip().lower()
            if "@" in api_email:
                e["email_domain"] = api_email.rsplit("@", 1)[-1]
        time.sleep(0.1)  # gentle pacing on the Aura API


def _new_counter() -> dict:
    return {**{k: 0 for k in SENTIMENT_KEYS}, "total": 0}


def _tally(counter: dict, sentiment: str) -> None:
    if sentiment in counter:
        counter[sentiment] += 1
    counter["total"] += 1


def pct_very(counter: dict) -> float | None:
    """'Very disappointed' share of a cohort, rounded to 1 dp, or None if empty."""
    if not counter["total"]:
        return None
    return round(100.0 * counter["Very disappointed"] / counter["total"], 1)


def build_segments(real_non_internal: list[dict]) -> dict:
    """Roll the internal-excluded respondents up into tenure / content / cross-tab cells."""
    by_tenure = {b: _new_counter() for b in TENURE_ORDER}
    by_content = {k: _new_counter() for k in ("content_anchored", "aurie_anchored", "unknown")}
    cross: dict[str, dict[str, dict]] = {}
    for e in real_non_internal:
        sentiment = e["sentiment"]
        bucket = e.get("tenure_bucket", "unknown")
        _tally(by_tenure.get(bucket, by_tenure["unknown"]), sentiment)

        anchored = e.get("content_anchored")
        ckey = "unknown" if anchored is None else ("content_anchored" if anchored else "aurie_anchored")
        _tally(by_content[ckey], sentiment)

        cross.setdefault(bucket, {}).setdefault(ckey, _new_counter())
        _tally(cross[bucket][ckey], sentiment)
    return {"by_tenure": by_tenure, "by_content_habit": by_content, "by_tenure_x_content": cross}


def build_benchmark(real_non_internal: list[dict]) -> dict:
    """Blended vs Aurie-anchored-tenure-mature 'Very disappointed' %, both vs 40%.

    An `aura_found=false` respondent stays in the blended denominator. Dropping
    them would bias the score: a 404 on `GET /users/<id>` is most often a deleted
    account, i.e. a churned real user, and those skew "Not disappointed" — so
    excluding them lifts the number rather than cleaning it. They are instead
    reported as their own split (`blended_resolved_very_pct` over the resolved
    subset, plus `n_blended_unresolved`) so a residue large enough to move the
    headline is visible rather than absorbed. Test/internal accounts among them
    are handled earlier, by the footer-email rule, which needs no API call.
    """
    blended = _new_counter()
    blended_resolved = _new_counter()
    aurie_mature = _new_counter()
    for e in real_non_internal:
        _tally(blended, e["sentiment"])
        if e.get("aura_found"):
            _tally(blended_resolved, e["sentiment"])
        if e.get("content_anchored") is False and e.get("tenure_bucket") == "30+":
            _tally(aurie_mature, e["sentiment"])
    cohort_pct = pct_very(aurie_mature)
    return {
        "cohort": "Aurie-anchored (content_anchored=false) AND tenure 30+, internal excluded",
        "blended_very_pct": pct_very(blended),
        "blended_n": blended["total"],
        "blended_resolved_very_pct": pct_very(blended_resolved),
        "n_blended_resolved": blended_resolved["total"],
        "n_blended_unresolved": blended["total"] - blended_resolved["total"],
        "blended_note": (
            "unresolved (aura_found=false) respondents are KEPT in blended_n — a 404 is "
            "usually a deleted account, so dropping them biases the score upward; compare "
            "blended_very_pct against blended_resolved_very_pct to see if the residue moves it"
        ),
        "aurie_mature_very_pct": cohort_pct,
        "n_aurie_mature": aurie_mature["total"],
        "meets_40": bool(cohort_pct is not None and cohort_pct >= 40.0),
        "small_n": aurie_mature["total"] < 10,
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(description="Fetch + parse + filter PMF survey responses.")
    ap.add_argument("--channel", default=DEFAULT_CHANNEL)
    ap.add_argument("--window", default="30d", help="Nd or YYYY-MM-DD..YYYY-MM-DD")
    ap.add_argument("--single-ts", default=None,
                    help="Scope to one message by its ts (e.g. 1780465597.965429).")
    ap.add_argument("--token", default=os.environ.get("SLACK_BOT_TOKEN"))
    ap.add_argument("--json", default=None, metavar="PATH",
                    help="Write JSON output to PATH instead of (or in addition to) stdout. "
                         "Always use this flag — never pipe stdout through inline Python "
                         "transformations, which can mangle UIDs and other field values.")
    ap.add_argument("--aura-token", default=os.environ.get("AURA_API_TOKEN"),
                    help="Aura API bearer token for the segmentation join "
                         "(GET /users/<id> + POST /subscriptions/list/<id>). "
                         "Defaults to $AURA_API_TOKEN.")
    ap.add_argument("--aura-base-url", default=DEFAULT_AURA_BASE_URL,
                    help=f"Aura API base URL for enrichment (default {DEFAULT_AURA_BASE_URL}).")
    ap.add_argument("--internal-ids", default="",
                    help="Comma-separated employee/internal identifiers (Firebase UID or "
                         "provider auth id like google:123). The orchestrator fills this from "
                         "the Team/Internal Accounts AI Knowledge page. Matches are flagged "
                         "is_internal (reason allowlist:<uid-prefix>) regardless of the Aura API join. "
                         "Prefer --internal-accounts-file.")
    ap.add_argument("--internal-accounts-file", default=None, metavar="PAGE_MD",
                    help="The Team/Internal Accounts page as markdown (the retrieve-knowledge "
                         "cache file or a saved notion-fetch). Every Firebase UID, provider id "
                         "and email row on it joins the allowlist, merged with --internal-ids.")
    ap.add_argument("--internal-names", default="",
                    help="Comma-separated employee first names for a SOFT (possible) match on "
                         "the PMF display name. Recorded as allowlist_name_match only — never "
                         "auto-excluded, since real users share first names.")
    ap.add_argument("--no-enrich", action="store_true",
                    help="Skip the Aura API enrichment join entirely (Slack-only / offline). "
                         "Output then omits the segments / internal_excluded / benchmark blocks.")
    args = ap.parse_args()

    if not args.token:
        raise SystemExit("SLACK_BOT_TOKEN not set (pass --token or export it).")

    if args.single_ts:
        oldest = latest = float(args.single_ts)
        label = f"single message {args.single_ts}"
    else:
        oldest, latest, label = resolve_window(args.window)

    try:
        raw = fetch_messages(args.token, args.channel, oldest, latest, args.single_ts)
    except SlackReadError as exc:
        raise SystemExit(str(exc))
    entries, unparsed = partition_messages(raw, args.channel)
    if unparsed:
        print(
            f"WARNING: {len(unparsed)} message(s) matched the PMF header but did not "
            f"parse — the sender's format may have changed. See counts.unparsed / "
            f"unparsed_messages.",
            file=sys.stderr,
        )
    try:
        allowlist = internal_accounts.resolve(args.internal_ids, args.internal_accounts_file)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"--internal-accounts-file: {exc}")
    apply_test_filter(entries)
    # Footer identity (email domain + internal verdict) BEFORE any API call, so the
    # verdict holds for entries whose UID never resolves.
    apply_footer_identity(entries, allowlist["emails"], allowlist["email_patterns"])
    # Newest first for a natural review order.
    entries.sort(key=lambda e: float(e["ts"] or 0), reverse=True)

    real = [e for e in entries if not e["is_test"]]
    tests = [e for e in entries if e["is_test"]]
    with_text = [e for e in real if not e["feedback_is_default"]]

    # Flag UIDs with more than one submission in the window (not filtered, just reported).
    uid_counts = Counter(e["uid"] for e in real)
    duplicate_uids = {uid: count for uid, count in uid_counts.items() if count > 1}

    # Segmentation enrichment (PROD-2665) — deterministic Aura API join over the
    # full real-respondent population. Best-effort: degrades gracefully when the
    # token is absent or --no-enrich is set.
    if args.no_enrich:
        enrichment_ran, enrichment_note = False, "--no-enrich set; segmentation skipped"
    elif not args.aura_token:
        enrichment_ran, enrichment_note = (
            False,
            "AURA_API_TOKEN not set; segmentation skipped (pass --aura-token, export it, "
            "or use --no-enrich)",
        )
    else:
        enrich_entries(real, args.aura_token, args.aura_base_url)
        enrichment_ran, enrichment_note = True, None

    # Overlay the Team/Internal Accounts allowlist AFTER any enrichment so it can
    # flag employees on personal accounts the Aura-API rules miss — and so it
    # works even when enrichment was skipped (no token / --no-enrich).
    internal_ids = allowlist["ids"]
    internal_names = set(args.internal_names.split(",")) if args.internal_names else set()
    if internal_ids or internal_names:
        apply_allowlist(real, internal_ids, internal_names)

    # Computed AFTER every internal signal has landed (footer email, Aura API,
    # allowlist). Both distributions are reported: the raw one keeps the full
    # observed record, the non-internal one is what the Sean-Ellis headline reads.
    non_internal = [e for e in real if not e.get("is_internal")]
    sentiment_dist = dict(Counter(e["sentiment"] for e in real).most_common())
    sentiment_dist_non_internal = dict(
        Counter(e["sentiment"] for e in non_internal).most_common()
    )

    out = {
        "channel": args.channel,
        "window": label,
        "counts": {
            "parsed": len(entries),
            "unparsed": len(unparsed),
            "real": len(real),
            "test_dropped": len(tests),
            "real_with_feedback_text": len(with_text),
            "real_without_feedback_text": len(real) - len(with_text),
            "internal_excluded": len(real) - len(non_internal),
            "real_non_internal": len(non_internal),
        },
        "sentiment_distribution": sentiment_dist,
        "sentiment_distribution_non_internal": sentiment_dist_non_internal,
        "duplicate_uids": duplicate_uids,
        "unparsed_messages": unparsed,
        "test_reasons": dict(Counter(e["test_reason"] for e in tests).most_common()),
        "entries": real,
        "test_entries": [
            {"uid": e["uid"], "first_name": e["first_name"], "sentiment": e["sentiment"],
             "feedback": e["feedback"], "test_reason": e["test_reason"],
             "permalink": e["permalink"]}
            for e in tests
        ],
    }

    out["enrichment"] = {
        "ran": enrichment_ran,
        "note": enrichment_note,
        "tenure_hours": "fractional hours since the anchor, as of survey time — sub-24h banner input; tenure_days floors and cannot resolve it",
        "tenure_anchor": "aiCreatedAt (fallback createdAt)",
        "tenure_buckets": "0-7 / 8-29 / 30+ (days)",
        "content_thresholds": {
            "listenedCount_gte": CONTENT_LISTEN_THRESHOLD,
            "streakMax_gte": CONTENT_STREAK_THRESHOLD,
            "or_content_subscription": True,
        },
        "internal_roles": sorted(INTERNAL_ROLES),
        "internal_email_domains": sorted(INTERNAL_EMAIL_DOMAINS),
        "footer_email_rule": (
            "the PMF footer's name slot sometimes holds an address; its domain + test-alias "
            "local part are classified at parse time (reasons email_domain=<d> / "
            "email_test_alias) so the rule fires even when GET /users/<id> 404s. Only the "
            "domain is retained — never the address (PII)."
        ),
    }
    # internal_excluded surfaces whenever anything matched — the allowlist overlay
    # runs without enrichment, so this block appears even when the Aura API join
    # was skipped. segments / benchmark still require enrichment (tenure/content).
    internal = [e for e in real if e.get("is_internal")]
    name_matches = [e for e in real if e.get("allowlist_name_match") and not e.get("is_internal")]
    if internal or name_matches:
        out["internal_excluded"] = {
            "count": len(internal),
            "entries": [
                {"uid_prefix": uid_prefix(e["uid"]), "sentiment": e["sentiment"],
                 "email_domain": e.get("email_domain", ""),
                 "internal_reason": e.get("internal_reason", "")}
                for e in internal
            ],
            "possible_by_name": [
                {"uid_prefix": uid_prefix(e["uid"]), "sentiment": e["sentiment"],
                 "first_name": e["allowlist_name_match"]}
                for e in name_matches
            ],
        }
    if enrichment_ran:
        out["segments"] = {
            "denominator": "real respondents, internal-excluded",
            "n_real_non_internal": len(non_internal),
            "n_enrichment_unresolved": sum(
                1 for e in non_internal if not e.get("aura_found")
            ),
            **build_segments(non_internal),
        }
        out["benchmark"] = build_benchmark(non_internal)

    payload = json.dumps(out, indent=2, ensure_ascii=False)
    print(payload)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            fh.write(payload)
        print(f"  wrote {args.json}", file=sys.stderr)


if __name__ == "__main__":
    main()
