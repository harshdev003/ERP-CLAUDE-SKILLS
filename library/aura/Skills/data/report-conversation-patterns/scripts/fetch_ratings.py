#!/usr/bin/env python3
"""Fetch + parse + filter `*[TYPE] RATED*` k8bot messages from a Slack channel.

Deterministic core of the `data:investigate-user-rating` skill: pulls the
channel history over a window, parses all six RATED shapes, applies polarity +
shape filters, drops only allowlisted internal accounts, and emits structured JSON the
SKILL.md drives the one-at-a-time sub-skill loop from.

Auth: SLACK_BOT_TOKEN (env, or --token). The bot must be a member of the channel.

Slack reads go through the shared `slack_read` helper (copied into this skill's
`scripts/` dir — the canonical `shared:read-slack` library; see AINative `CLAUDE.md`
→ "Canonical source for `slack_read.py`"), which owns transport (requests, curl
fallback; never urllib), cursor pagination, and the 429-retry.

Forces UTF-8 stdout (Windows cp1252 would choke on the → / — this script emits in
error strings).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import pathlib
from collections import Counter
from datetime import datetime, timezone

try:  # optional — only the Aura-API internal join needs it
    import requests
except ImportError:  # pragma: no cover - degraded mode
    requests = None

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


DEFAULT_CHANNEL = "C07MB0TMF5J"  # #ai-conversation-ratings
WORKSPACE = "auramobile"

# The footer's `user:` field, captured by its DELIMITER rather than by an
# enumerated character class. Aura ids come in at least four shapes — 28-char
# Firebase, UUID, Firebase push key (20 chars, leading `-`, embedded `_`), and
# provider-prefixed (`google:112…`) — and a class that misses one truncates it
# silently: the fragment 404s, and the empty profile reads as "no history"
# rather than "wrong key". A class narrow enough to have caused this once is
# narrow enough to cause it again on the next id scheme, so match to the comma
# instead. Checked against all 7,573,740 Aura ids: none contains `,` or `]`,
# and this truncates zero of them (`[A-Za-z0-9]+` truncates 256,754). ENG-1059.
UID_RE = r"user:\s*([^,\]]+)"

# Warn-only shape check. The parse above cannot truncate, so this exists purely
# to surface an id family we have not seen before — it never drops or rewrites
# an entry, and a malformed footer must not abort an unattended cadence sweep.
# Calibrated on the same corpus: 28 false warnings (0.0004%, odd-length and junk
# rows), and it flags 100% of the artifacts the old parse produced.
UID_SHAPE_RE = re.compile(
    r"[A-Za-z0-9]{20,32}"                                       # Firebase 28 + bare alnum
    r"|[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}"    # UUID
    r"|-[A-Za-z0-9_-]{19}"                                      # Firebase push key
    r"|[a-z]+:[A-Za-z0-9._\-]+"                                 # provider-prefixed
)
# Rating volume is not a test signal — it tracks engagement, not QA, so the most
# prolific raters in any window are long-tenured paying accounts (ENG-1048).
# Prolific UIDs are annotated and reported so a genuine QA loop stays visible;
# the annotation never drops an entry.
HIGH_VOLUME_UID_THRESHOLD = 20  # report-only

# Generation counter for the internal/test filter. Consumers with a persisted
# watermark (the two rating cadences) compare this against the generation recorded
# in their state file: a higher value here means the filter admits entries their
# watermark never had the chance to see, so they seed rather than act on the
# resulting backlog. Bump it in any change that makes the filter MORE INCLUSIVE.
FILTER_GENERATION = 2

# Internal / non-customer signals read from the Aura user record, for accounts the
# `Team/Internal Accounts` allowlist doesn't name. Same ladder as
# `data:investigate-pmf-survey`. `isCoach` is deliberately absent — a coach can be
# a genuine Aurie user whose rating is worth reading.
INTERNAL_ROLES = frozenset(
    {"admin", "agent", "service", "system", "creator", "audio-engineer"}
)
INTERNAL_EMAIL_DOMAINS = frozenset({"aurahealth.io"})
DEFAULT_AURA_BASE_URL = "https://services.aurahealth.io"

# Infrastructure/voice feedback patterns to skip by default.
# These are k8bot feedback tags for ElevenLabs TTS or connectivity issues — not
# content-quality signals. Checked as case-insensitive substrings of the feedback
# field (not exact match) so "Voice issue | couldn't connect" is also caught.
# Entries tagged is_infra=True are moved to infra_entries in the output and excluded
# from `entries` by default. Pass --include-infra to keep them.
INFRA_SUBSTRINGS = (
    "voice issue",
    "voice issues",
    "audio issue",
    "audio issues",
    "invalid intent",
    "does not reply",
    "won't allow ai to speak",
    "will not allow ai to speak",
    "not connecting",
    "it's not connecting",
    "cannot use it",
    "can't use it",
)

# Headers for the six RATED shapes
SHAPE_HEADERS = {
    "qp": "*QUICK PROCESSING RATED*",
    "coaching": "*COACHING RATED*",
    "conversation": "*CONVERSATION RATED*",
    "chat": "*CHAT MESSAGE RATED*",
    "journal": "*JOURNAL RATED*",
    "profile": "*PROFILE RATED*",
}

VALID_SHAPES = set(SHAPE_HEADERS.keys())
VALID_POLARITIES = {"negative", "negative_neutral", "all"}


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


def _grp(text: str, pattern: str, flags: int = 0) -> str:
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else ""


def _detect_polarity(text: str) -> str:
    """Map a rating message's emoji to positive / neutral / negative.

    Shared by every shape. The three-value scale is the service's own — the
    Intelligence Service validates a rating as 1 / 0 / -1 and maps 0 to
    `:neutral_face:` — so EVERY shape can carry a neutral, profile included.
    Observed encodings: ":+1: Positive" / ":-1: Negative" (older text form),
    ":thumbsup:" / ":thumbsdown:" (emoji-code form), ":neutral_face: Neutral",
    and the raw emoji characters.

    Parsing a neutral on every shape is right; EXPECTING one is not. The
    three-value scale is the service's, and the WIDGET is narrower on two
    shapes — chat and journal ship 👍/👎 only, so a neutral cannot reach this
    function from them at all (every one observed came from a test account
    calling the endpoint directly). That makes `--polarity negative_neutral`
    equivalent to `negative` on those two, and it makes any polarity rate
    pooling them with a three-option shape an artifact of the widget mix.
    Canonical per-surface option-set table: the `Signals/Conversation Patterns`
    schema in `shared:record-knowledge` (ENG-1118).
    """
    if ":thumbsup:" in text or ":+1:" in text or "\U0001f44d" in text:
        return "positive"
    if ":thumbsdown:" in text or ":-1:" in text or "\U0001f44e" in text:
        return "negative"
    if ":neutral_face:" in text:
        return "neutral"
    return "unknown"


def _normalise_feedback(raw: str) -> str:
    """Return empty string for placeholder 'No feedback' or whitespace-only text."""
    stripped = raw.strip()
    if not stripped or stripped.lower() in {"no feedback", "no feedback."}:
        return ""
    return stripped


def detect_shape(text: str) -> str | None:
    """Return the shape key if the message matches a known RATED header, else None."""
    for shape, header in SHAPE_HEADERS.items():
        if text.startswith(header):
            return shape
    return None


def parse_conversation_entry(msg: dict, channel: str, shape: str) -> dict:
    """Parse a conversation-pivot RATED message (qp / coaching / conversation / chat)."""
    text = (msg.get("text") or "").strip()
    ts = msg.get("ts", "")

    # Footer format: `[dialogue: <uuid>, conversation: <uuid>, user: <uid>, prompt:<name>:<version>]`
    # The footer is wrapped in single backticks — strip them before extracting fields.
    footer = _grp(text, r"`\[([^\]]+)\]`")
    if not footer:
        # Fallback: match [ ... ] anywhere near the end (handles minor formatting variants)
        footer = _grp(text, r"\[([^\]]+)\](?:`\s*)?$", re.DOTALL)

    dialogue_id = _grp(footer, r"dialogue:\s*([0-9a-fA-F\-]+)")
    conversation_id = _grp(footer, r"conversation:\s*([0-9a-fA-F\-]+)")
    uid = _grp(footer, UID_RE)

    # prompt:<name>:<version>
    prompt_raw = _grp(footer, r"prompt:(.+?)(?:\]|$)")
    if ":" in prompt_raw:
        parts = prompt_raw.rsplit(":", 1)
        prompter_name = parts[0].strip()
        prompter_version = parts[1].strip()
    else:
        prompter_name = prompt_raw.strip()
        prompter_version = ""

    # Polarity — three observed formats in the wild:
    #   ":+1: Positive" / ":-1: Negative"          (older text form)
    #   ":thumbsup:" / ":thumbsdown:"               (emoji-code form, no text suffix)
    #   ":neutral_face: Neutral"                    (neutral, text form)
    #   👍 / 👎                                     (raw emoji characters)
    polarity = _detect_polarity(text)

    # Feedback: fenced block (may be absent or placeholder)
    feedback_raw = _grp(text, r"```(.*?)```", re.DOTALL)
    feedback = _normalise_feedback(feedback_raw)

    when = (
        datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        if ts else ""
    )
    return {
        "ts": ts,
        "when": when,
        "shape": shape,
        "uid": uid,
        "polarity": polarity,
        "conversation_id": conversation_id,
        "dialogue_id": dialogue_id,
        "prompter_name": prompter_name,
        "prompter_version": prompter_version,
        "feedback": feedback,
        "journal_id": None,
        "journal_text": None,
        "rating_id": None,
        "renderer_key": None,
        "permalink": ts_to_permalink(channel, ts) if ts else "",
        "thread_ts": ts,
        "is_test": False,
        "test_reason": "",
    }


def parse_journal_entry(msg: dict, channel: str) -> dict:
    """Parse a *JOURNAL RATED* message.

    Shape:
        *JOURNAL RATED*
        👎   (or 👍)

        `<journal entry text>`

        [id: <journal-entry-uuid>, user: <Firebase UID>]
    """
    text = (msg.get("text") or "").strip()
    ts = msg.get("ts", "")

    # Footer: `[id: <uuid>, user: <uid>]`  (backtick-wrapped, same as conversation shapes)
    footer = _grp(text, r"`\[([^\]]+)\]`")
    if not footer:
        footer = _grp(text, r"\[([^\]]+)\](?:`\s*)?$", re.DOTALL)
    journal_id = _grp(footer, r"id:\s*([0-9a-fA-F\-]+)")
    uid = _grp(footer, UID_RE)

    # Polarity — same multi-format handling as conversation shapes
    polarity = _detect_polarity(text)

    # Journal text / feedback: triple-backtick block is the content fenced block.
    # (Single-backtick is reserved for the footer `[...]`; don't confuse them.)
    journal_text = _grp(text, r"```(.*?)```", re.DOTALL).strip()

    when = (
        datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        if ts else ""
    )
    return {
        "ts": ts,
        "when": when,
        "shape": "journal",
        "uid": uid,
        "polarity": polarity,
        "conversation_id": None,
        "dialogue_id": None,
        "prompter_name": None,
        "prompter_version": None,
        "feedback": "",
        "journal_id": journal_id,
        "journal_text": journal_text,
        "rating_id": None,
        "renderer_key": None,
        "permalink": ts_to_permalink(channel, ts) if ts else "",
        "thread_ts": ts,
        "is_test": False,
        "test_reason": "",
    }


def parse_profile_entry(msg: dict, channel: str) -> dict:
    """Parse a *PROFILE RATED* message.

    Shape:
        *PROFILE RATED*
        :thumbsup:   (or :thumbsdown:)

        ```<feedback, or the literal "No feedback">```

        `[id: <rendererRating-row-uuid>, user: <Firebase UID>]`

    The footer gains `renderer: <prompter-name-key>` when a single card was rated
    rather than the whole profile.
    """
    text = (msg.get("text") or "").strip()
    ts = msg.get("ts", "")

    # Footer: `[id: <uuid>, user: <uid>]` (+ optional `renderer: <key>`), same
    # backtick-wrapped shape as the journal footer.
    footer = _grp(text, r"`\[([^\]]+)\]`")
    if not footer:
        footer = _grp(text, r"\[([^\]]+)\](?:`\s*)?$", re.DOTALL)
    rating_id = _grp(footer, r"id:\s*([0-9a-fA-F\-]+)")
    uid = _grp(footer, UID_RE)
    renderer_key = _grp(footer, r"renderer:\s*([a-z0-9\-]+)") or None

    polarity = _detect_polarity(text)

    feedback = _normalise_feedback(_grp(text, r"```(.*?)```", re.DOTALL))

    when = (
        datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        if ts else ""
    )
    return {
        "ts": ts,
        "when": when,
        "shape": "profile",
        "uid": uid,
        "polarity": polarity,
        "conversation_id": None,
        "dialogue_id": None,
        "prompter_name": None,
        "prompter_version": None,
        "feedback": feedback,
        "journal_id": None,
        "journal_text": None,
        "rating_id": rating_id,
        "renderer_key": renderer_key,
        "permalink": ts_to_permalink(channel, ts) if ts else "",
        "thread_ts": ts,
        "is_test": False,
        "test_reason": "",
    }


def parse_entry(msg: dict, channel: str) -> dict | None:
    """Dispatch to the right parser based on the message header, or return None."""
    text = (msg.get("text") or "").strip()
    shape = detect_shape(text)
    if shape is None:
        return None
    if shape == "journal":
        return parse_journal_entry(msg, channel)
    if shape == "profile":
        return parse_profile_entry(msg, channel)
    return parse_conversation_entry(msg, channel, shape)


def check_uid_shapes(entries: list[dict]) -> dict[str, int]:
    """Warn on uids matching no known id family. Returns {masked_shape: count}.

    Purely observational — entries are never dropped or rewritten. The mask
    collapses alphanumeric runs to `X` so the warning names the *shape*
    (`X:X`, `X-X-X-X-X`) without printing a real id.
    """
    unrecognised: Counter[str] = Counter()
    for e in entries:
        uid = e.get("uid") or ""
        if uid and not UID_SHAPE_RE.fullmatch(uid):
            unrecognised[re.sub(r"[A-Za-z0-9]+", "X", uid) + f"|len{len(uid)}"] += 1
    for shape, count in unrecognised.most_common():
        print(
            f"warning: {count} rating(s) carry a user id of unrecognised shape {shape} — "
            "parsed as-is; add the family to UID_SHAPE_RE if it is legitimate",
            file=sys.stderr,
        )
    return dict(unrecognised)


def apply_polarity_filter(entries: list[dict], polarity_filter: str) -> list[dict]:
    """Return entries matching the polarity filter."""
    if polarity_filter == "all":
        return entries
    if polarity_filter == "negative":
        return [e for e in entries if e["polarity"] == "negative"]
    if polarity_filter == "negative_neutral":
        return [e for e in entries if e["polarity"] in ("negative", "neutral")]
    return entries


def apply_shape_filter(entries: list[dict], shape_filter: str) -> list[dict]:
    """Return entries matching the shape filter."""
    if shape_filter == "all":
        return entries
    return [e for e in entries if e["shape"] == shape_filter]


def aura_request(
    session: "requests.Session", method: str, url: str, json_body: dict | None = None
) -> tuple[int | None, object]:
    """One Aura API call with retry on 429/5xx. Returns (status_code, parsed_json).

    Never raises — on a transport error or exhausted retries returns (None, None) so
    the caller degrades per-UID instead of aborting the sweep.
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


def classify_internal(user: dict, has_creator_sub: bool) -> tuple[bool, str]:
    """Return (is_internal, reason) for an Aura user record. First signal wins."""
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


def build_internal_index(
    uids: list[str], token: str, base_url: str
) -> tuple[dict[str, str], dict]:
    """Join distinct UIDs against the Aura API. Returns ({uid: reason}, meta).

    One `GET /users/:uid` + one `POST /subscriptions/list/:uid` per DISTINCT uid —
    a window repeats uids heavily, so the join is per-user, not per-rating.

    A UID that can't be resolved is counted in `meta["unresolved"]` and treated as
    a real customer. That direction is deliberate: analysing one extra rating costs
    a sweep step, while dropping one loses a paying customer's feedback outright.
    """
    reasons: dict[str, str] = {}
    meta = {"checked": 0, "internal": 0, "unresolved": 0}
    session = requests.Session()
    session.headers.update(
        {"Authorization": f"Bearer {token}", "User-Agent": user_agent(), "Content-Type": "application/json"}
    )
    for uid in uids:
        # An empty id builds `/users/` and `/subscriptions/list/`, which resolve to
        # the self-serve routes and answer for the token owner — an admin account,
        # so every entry sharing the blank id would be classified internal and
        # dropped from the sweep. Callers already filter falsy uids; this keeps the
        # invariant with the request that depends on it (PROD-6300).
        if not (uid or "").strip():
            continue
        meta["checked"] += 1
        status, data = aura_request(session, "GET", f"{base_url}/users/{uid}")
        if status != 200 or not isinstance(data, dict):
            meta["unresolved"] += 1
            time.sleep(0.1)
            continue
        sub_status, sub_data = aura_request(
            session, "POST", f"{base_url}/subscriptions/list/{uid}", {"includeDeleted": True}
        )
        rows = parse_subscription_rows(sub_data) if sub_status == 200 else None
        has_creator_sub = any(
            isinstance(row, dict)
            and (row.get("provider") or "").lower() == "aura"
            and (row.get("lifetime") or row.get("free"))
            for row in (rows or [])
        )
        is_internal, reason = classify_internal(data, has_creator_sub)
        if is_internal:
            reasons[uid] = reason
            meta["internal"] += 1
        time.sleep(0.1)  # gentle pacing on the Aura API
    return reasons, meta


def uid_prefix(uid: str) -> str:
    """Short, non-identifying prefix of `uid` that still distinguishes users.

    A bare `uid[:8]` turns every `google:112…` into `"google:1"`, so two employees
    on personal Google logins collapse onto one `test_reasons` key. The 8 characters
    are taken after the provider instead. Same derivation as
    `report-conversation-patterns/scripts/sample.py` (canonical); held in step by
    `tests/test_uid_prefix_rule.py`.
    """
    uid = uid or ""
    if ":" in uid:
        provider, _, rest = uid.partition(":")
        return f"{provider}:{rest[:8]}"
    return uid[:8]


def check_allowlist(allowlist: dict[str, set[str]]) -> dict:
    """Report allowlist rows that can never match a rating footer. Warn-only.

    The footer's `user:` field carries the account's id — a Firebase UID, or the
    provider id itself (`google:…`) for accounts created on one, which is how the
    founders' personal logins appear. It never carries an email, so email rows are
    inert here, and an id in no family `UID_SHAPE_RE` knows is a typo or a new
    scheme. Either row silently excludes nothing, so both are counted in the JSON
    (cron swallows stderr); bad-shape ids and caller-passed emails also warn.
    """
    bad_shape = sorted(i for i in allowlist["ids"] if not UID_SHAPE_RE.fullmatch(i))
    n_email = len(allowlist["emails"]) + len(allowlist["email_patterns"])
    for i in bad_shape:
        print(
            f"warning: allowlist id {uid_prefix(i)}… matches no rating-footer id shape — "
            "it can never exclude an entry; check the Team/Internal Accounts row",
            file=sys.stderr,
        )
    # Only addresses the caller passed in --internal-ids: the page's own email rows
    # are expected to be inert here, and a warning on every tick gets ignored.
    if allowlist["explicit_emails"]:
        print(
            f"warning: {allowlist['explicit_emails']} --internal-ids email(s) can never match here — rating "
            "footers carry only `user:<id>`; those accounts need an id row or the Aura "
            "API ladder",
            file=sys.stderr,
        )
    return {
        "ids": len(allowlist["ids"]),
        "provider_ids": len(allowlist["provider_ids"]),
        "unmatchable_ids": [uid_prefix(i) for i in bad_shape],
        "unmatchable_email_rows": n_email,
    }


def apply_test_filter(
    entries: list[dict],
    internal_ids: set[str] | None = None,
    api_reasons: dict[str, str] | None = None,
) -> None:
    """Tag each entry in place with is_test + test_reason + high_volume.

    A test criterion has to be a signal that says the account itself is internal.
    Two supply one: the `Team/Internal Accounts` allowlist passed via
    --internal-ids, and the Aura user record read by `build_internal_index`
    (`api_reasons`, keyed by UID). Nothing is dropped on inferred grounds.

    Volume is deliberately not a test criterion — it tracks engagement, so a rate
    threshold drops real accounts in proportion to how much they use the product
    (ENG-1048). Prolific UIDs get `high_volume=True` and are reported, not dropped.

    No feedback-text test filter either — conversation ratings text is optional and
    usually substantive when present; journal_text is a real user entry.
    """
    internal_ids = internal_ids or set()
    api_reasons = api_reasons or {}
    uid_counts = Counter(e["uid"] for e in entries if e["uid"])
    for e in entries:
        reasons = []
        if e["uid"] and e["uid"] in internal_ids:
            reasons.append(f"allowlist:{uid_prefix(e['uid'])}")
        if e["uid"] and e["uid"] in api_reasons:
            reasons.append(api_reasons[e["uid"]])
        e["is_test"] = bool(reasons)
        e["test_reason"] = ",".join(reasons)
        e["high_volume"] = bool(
            e["uid"] and uid_counts[e["uid"]] >= HIGH_VOLUME_UID_THRESHOLD
        )


def apply_api_internal(entries: list[dict], api_reasons: dict[str, str]) -> None:
    """Fold Aura-API internal verdicts into entries already tagged by the allowlist.

    Deliberately additive and separate from `apply_test_filter`: that function
    derives `high_volume` from the entry list it is handed, so re-running it on a
    post-filter slice would recompute the window count over the wrong denominator
    and under-report prolific UIDs. This only ORs reasons into `test_reason`.
    """
    for e in entries:
        reason = api_reasons.get(e["uid"] or "")
        if not reason:
            continue
        e["is_test"] = True
        e["test_reason"] = f"{e['test_reason']},{reason}" if e["test_reason"] else reason


def apply_infra_filter(entries: list[dict]) -> None:
    """Tag each entry in place with is_infra=True when feedback signals a voice/
    connectivity infrastructure issue rather than a content-quality problem.

    Detection: feedback field contains any INFRA_SUBSTRINGS (case-insensitive
    substring match, not exact). Multi-tag k8bot feedback like
    "Voice issue | I cannot use it" is caught by the substring check.

    Tagged entries are excluded from `entries` in the main output and collected
    separately in `infra_entries`. Pass --include-infra to the script to keep
    them in `entries` (useful for ElevenLabs / voice-quality investigations).
    """
    for e in entries:
        feedback_lower = (e.get("feedback") or "").lower()
        e["is_infra"] = any(sub in feedback_lower for sub in INFRA_SUBSTRINGS)
        e["infra_reason"] = next(
            (sub for sub in INFRA_SUBSTRINGS if sub in feedback_lower), ""
        )


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(description="Fetch + parse + filter conversation ratings.")
    ap.add_argument("--channel", default=DEFAULT_CHANNEL)
    ap.add_argument("--window", default="7d", help="Nd or YYYY-MM-DD..YYYY-MM-DD")
    ap.add_argument("--single-ts", default=None,
                    help="Scope to one message by its ts (e.g. 1780279622.389939).")
    ap.add_argument("--token", default=os.environ.get("SLACK_BOT_TOKEN"))
    ap.add_argument(
        "--polarity",
        default="negative",
        choices=["negative", "negative_neutral", "all"],
        help="Polarity filter applied after parsing.",
    )
    ap.add_argument(
        "--shape",
        default="all",
        choices=["all", "qp", "coaching", "conversation", "chat", "journal", "profile"],
        help="Shape filter applied after parsing.",
    )
    ap.add_argument(
        "--internal-ids",
        default="",
        help=(
            "Comma-separated Firebase UIDs / provider auth ids from the "
            "`Team/Internal Accounts` allowlist. A hard internal flag that needs "
            "no Aura token — the path that catches employees on personal accounts. "
            "Prefer --internal-accounts-file, which reads every id off the page."
        ),
    )
    ap.add_argument(
        "--internal-accounts-file",
        default=None,
        metavar="PAGE_MD",
        help=(
            "The `Team/Internal Accounts` page as markdown (the retrieve-knowledge "
            "cache file or a saved notion-fetch). Every Firebase UID and provider id "
            "on it joins the allowlist, merged with --internal-ids."
        ),
    )
    ap.add_argument("--aura-token", default=os.environ.get("AURA_API_TOKEN"))
    ap.add_argument("--aura-base-url", default=DEFAULT_AURA_BASE_URL)
    ap.add_argument(
        "--no-enrich",
        action="store_true",
        default=False,
        help=(
            "Skip the Aura API internal-account join (one GET /users/:uid + one "
            "POST /subscriptions/list/:uid per distinct surviving UID). Only the "
            "--internal-ids allowlist then applies, so fewer entries are dropped."
        ),
    )
    ap.add_argument(
        "--include-infra",
        action="store_true",
        default=False,
        help=(
            "Include infrastructure/voice entries (tagged is_infra=True) in the "
            "main entries list instead of routing them to infra_entries. Useful "
            "for ElevenLabs / voice-quality investigations."
        ),
    )
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
    all_entries = [e for m in raw for e in [parse_entry(m, args.channel)] if e]
    unrecognised_uid_shapes = check_uid_shapes(all_entries)

    # Apply test + infra filters on all parsed entries (before polarity/shape filters)
    try:
        allowlist = internal_accounts.resolve(args.internal_ids, args.internal_accounts_file)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"--internal-accounts-file: {exc}")
    allowlist_report = check_allowlist(allowlist)
    internal_ids = allowlist["ids"]
    apply_test_filter(all_entries, internal_ids)
    apply_infra_filter(all_entries)

    # Newest first for a natural review order.
    all_entries.sort(key=lambda e: float(e["ts"] or 0), reverse=True)

    parsed_count = len(all_entries)

    # Apply polarity + shape filters (on all entries, test + real)
    after_polarity = apply_polarity_filter(all_entries, args.polarity)
    after_shape = apply_shape_filter(after_polarity, args.shape)

    # Aura-API internal join, over the UIDs that survived the polarity + shape
    # filters only — is_test decides whether a candidate reaches the roster, so a
    # UID outside that slice never needs the lookup. On --polarity negative that is
    # a fraction of the window's UIDs, which keeps a 30-minute cron cheap.
    distinct_uids = sorted({e["uid"] for e in after_shape if e["uid"]} - internal_ids)
    enrichment = {
        "ran": False,
        "reason": "",
        **{k: 0 for k in ("checked", "internal", "unresolved")},
    }
    if args.no_enrich:
        enrichment["reason"] = "disabled (--no-enrich)"
    elif requests is None:
        enrichment["reason"] = "requests not installed"
    elif not args.aura_token:
        enrichment["reason"] = "AURA_API_TOKEN not set"
    elif not distinct_uids:
        enrichment["reason"] = "no UIDs to check"
    else:
        api_reasons, meta = build_internal_index(
            distinct_uids, args.aura_token, args.aura_base_url.rstrip("/")
        )
        enrichment.update({"ran": True, "reason": "", **meta})
        apply_api_internal(after_shape, api_reasons)

    real = [e for e in after_shape if not e["is_test"] and (args.include_infra or not e.get("is_infra"))]
    tests = [e for e in after_shape if e["is_test"]]
    infra = [e for e in after_shape if not e["is_test"] and e.get("is_infra") and not args.include_infra]

    # Shape distribution for real entries
    shape_counts = Counter(e["shape"] for e in real)
    by_shape = {s: shape_counts.get(s, 0) for s in ["qp", "coaching", "conversation", "chat", "journal", "profile"]}
    # Only include shapes that have at least one entry
    by_shape = {k: v for k, v in by_shape.items() if v > 0}

    # Prompter mix (conversation-pivot shapes only; journal has no prompter)
    prompter_mix: dict[str, int] = {}
    for e in real:
        if e["prompter_name"]:
            key = f"{e['prompter_name']}:{e['prompter_version']}" if e["prompter_version"] else e["prompter_name"]
            prompter_mix[key] = prompter_mix.get(key, 0) + 1
    prompter_mix = dict(sorted(prompter_mix.items(), key=lambda x: -x[1]))

    out = {
        "channel": args.channel,
        "window": label,
        "polarity_filter": args.polarity,
        "shape_filter": args.shape,
        "counts": {
            "parsed": parsed_count,
            "after_polarity_filter": len(after_polarity),
            "real": len(real),
            "test_dropped": len(tests),
            "infra_skipped": len(infra),
            "by_shape": by_shape,
        },
        "filter_generation": FILTER_GENERATION,
        # Masked id shapes UID_SHAPE_RE didn't recognise. Empty is the normal
        # state; a non-empty map means a new id family reached the parser and
        # the enrichment ladder may be keying on something it can't resolve.
        # Reported here as well as on stderr because cron swallows stderr.
        "unrecognised_uid_shapes": unrecognised_uid_shapes,
        "enrichment": enrichment,
        # Counts only. `provider_ids: 0` on a page that lists some is the
        # 2026-09-28 failure (a hand-assembled --internal-ids that kept only UIDs).
        "allowlist": allowlist_report,
        "prompter_mix": prompter_mix,
        "test_reasons": dict(Counter(e["test_reason"] for e in tests if e["test_reason"]).most_common()),
        # Report-only: prolific UIDs are surfaced, never dropped (ENG-1048).
        "high_volume_uids": dict(
            Counter(e["uid"] for e in real if e.get("high_volume") and e["uid"]).most_common()
        ),
        "infra_reasons": dict(Counter(e["infra_reason"] for e in infra if e.get("infra_reason")).most_common()),
        "entries": real,
        "test_entries": [
            {
                "ts": e["ts"],
                "shape": e["shape"],
                "uid": e["uid"],
                "polarity": e["polarity"],
                "test_reason": e["test_reason"],
                "permalink": e["permalink"],
            }
            for e in tests
        ],
        "infra_entries": [
            {
                "ts": e["ts"],
                "shape": e["shape"],
                "uid": e["uid"],
                "polarity": e["polarity"],
                "feedback": e["feedback"],
                "infra_reason": e.get("infra_reason", ""),
                "conversation_id": e.get("conversation_id"),
                "prompter_name": e.get("prompter_name"),
                "prompter_version": e.get("prompter_version"),
                "permalink": e["permalink"],
            }
            for e in infra
        ],
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
