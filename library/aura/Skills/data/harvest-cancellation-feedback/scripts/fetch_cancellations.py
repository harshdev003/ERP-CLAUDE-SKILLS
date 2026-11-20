#!/usr/bin/env python3
r"""Fetch + parse + filter Slack feedback messages for `data:investigate-cancellation-feedback`.

Deterministic core of the orchestrator: pulls a Slack channel's history over a window,
parses one of two known feedback shapes, drops test/noise entries, and emits structured
JSON the SKILL.md drives the one-at-a-time `profile-user` loop from.

Two formats are supported, dispatched by `--format` (default `auto` derives from `--channel`):

- ``cancellation_feedback`` (channel ``C0BR3UTNL83`` = ``#ai-cancellation-feedback``, and
  ``C0AENS8D48N`` = ``#ai-feedback`` for posts predating the 2026-08 split) — k8bot
  ``*New Cancellation Feedback*`` posts with a ``Reason``, a fenced ``Feedback`` block,
  and a ``_User | Subscription | Type | Product | Purchased | Provider_`` metadata line.
  Tests are filtered on feedback text, on the same-UID-blast threshold, and on known
  DEV-environment Stripe price IDs (the cancellation webhook leaks DEV cancellations
  into the PROD channel — see AI Knowledge ``Tools/Stripe`` → "Price IDs").

- ``aurie_feedback`` (channel ``C0AEK511BT9`` = ``#aurie-feedback``) — free-text human-team
  posts where the support team pastes the verbatim user email/feedback ending with
  ``- <28-char Firebase UID>``, optionally followed by a back-tick-fenced support
  annotation (e.g. ``\`Asked to book a call.\```). Bot posts (weekly digests) and channel
  housekeeping subtypes are skipped.

Both formats land in the same JSON shape so the SKILL.md doesn't need a per-format branch;
fields that don't apply to a given format are emitted as empty strings / null.

Two sentinels sweep several channels in one call: ``--channel all`` (every known feedback
channel) and ``--channel cancellations`` (both sides of the 2026-08 cancellation split, no
``#aurie-feedback``). The output wraps each single-channel result under ``channels[]`` and
adds a top-level ``totals`` block; per-channel sub-objects are byte-for-byte identical to
the single-channel output, so orchestrator code can
``for ch in result["channels"]: walk ch["entries"]`` and reuse all the same per-format
synthesis. Single-channel invocations stay byte-for-byte unchanged — back-compat is the
critical invariant here (PROD-2524).

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
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from slack_read import SlackReadError, history, message_by_ts  # noqa: E402

# Canonical channel IDs. The defaults track AI Knowledge → Tools/Slack → Channel IDs.
CANCELLATION_FEEDBACK_CHANNEL = "C0BR3UTNL83"   # #ai-cancellation-feedback (k8bot posts)
AURIE_FEEDBACK_CHANNEL = "C0AEK511BT9"          # #aurie-feedback (free-text support posts)
# Cancellations landed in #ai-feedback until the 2026-08 split. Nothing new arrives here,
# but every pre-cutover post stays put, so a window reaching back past the split still
# has to read it.
LEGACY_CANCELLATION_FEEDBACK_CHANNEL = "C0AENS8D48N"
DEFAULT_CHANNEL = CANCELLATION_FEEDBACK_CHANNEL  # preserves existing CLI behaviour
# Order matters: emitted under `channels[]` in this order, so a consumer can rely on
# `[0]` being the k8bot channel and `[1]` being the human-team channel. The legacy
# channel is appended rather than inserted so that contract survives the split.
ALL_CHANNELS = [
    CANCELLATION_FEEDBACK_CHANNEL,
    AURIE_FEEDBACK_CHANNEL,
    LEGACY_CANCELLATION_FEEDBACK_CHANNEL,
]
# The cancellation feed alone, both sides of the split — no #aurie-feedback.
CANCELLATION_CHANNELS = [
    CANCELLATION_FEEDBACK_CHANNEL,
    LEGACY_CANCELLATION_FEEDBACK_CHANNEL,
]
ALL_SENTINEL = "all"                            # --channel all → sweep ALL_CHANNELS
CANCELLATIONS_SENTINEL = "cancellations"        # --channel cancellations → CANCELLATION_CHANNELS
# --channel <sentinel> → the channel list it sweeps, wrapped under `channels[]`.
SENTINEL_CHANNELS = {
    ALL_SENTINEL: ALL_CHANNELS,
    CANCELLATIONS_SENTINEL: CANCELLATION_CHANNELS,
}
WORKSPACE = "auramobile"
HEADER = "*New Cancellation Feedback*"
TEST_BLAST_THRESHOLD = 3  # same UID firing >= this many cancellations in-window = QA loop
# Same UID firing exactly twice within this window is the AIiOS cancel-modal double-fire
# bug: one user-initiated cancel produces two k8bot Slack messages ~4-10 min apart because
# the modal re-runs the cancel flow. Drop the later entry as `cancel-modal-double-fire` so
# the earlier one stays as the canonical cancel record. A legitimate re-cancel after a
# resubscribe takes far longer than this window (Stripe billing cycles operate on days, not
# minutes), so the window-guarded rule won't suppress real signal. When the upstream
# AIiOS bug is fixed (Linear: PROD-2554), this rule becomes redundant but stays as a
# belt-and-suspenders defence.
DOUBLE_MODAL_WINDOW_SECONDS = 600

# Feedback text (lowercased, stripped) that marks an entry as a test.
TEST_EXACT = {"test", "testing", "test dev"}

# The k8bot metadata line renders `Purchased` as a US Pacific wall clock, while the
# cancellation instant is the Slack message `ts` (a true UTC epoch) — so `Purchased` is
# localized before the two are subtracted. Skipping that adds the entire UTC offset to
# every gap, which turns a same-minute cancel into a ~7-hour one. Named as a zone, not an
# offset, because a fixed offset is wrong for four months of the year. If the backend ever
# changes its render timezone, this constant is the one edit.
PURCHASED_TZ_NAME = "America/Los_Angeles"

# Known DEV-environment Stripe price IDs. The cancellation k8bot webhook currently routes
# both DEV and PROD Stripe-side cancellations to PROD #ai-feedback (a webhook-routing bug
# worth tracking separately from any individual cancel investigation). DEV cancellations
# don't represent real-user signal — drop them as test_reason="dev-environment-stripe-price"
# so they don't contaminate the cohort. Mirror of AIWeb/src/data/pricing-dev.json
# ``stripePlan`` values; full inventory + dev↔prod mapping lives in AI Knowledge
# ``Tools/Stripe`` → "Price IDs (dev vs prod, plus the dev-leak gotcha)". Keep in sync
# when AIWeb adds a new dev variant.
DEV_STRIPE_PRICES = {
    "price_1SaaoIIS90bgNudEFtMDb6Xl",  # DEV ai_yearly_6999_xdays (all three internal annual plans share)
    "price_1TRtfBIS90bgNudEYQsIa7gU",  # DEV ai_monthly_1999
}

# Firebase UID shape used by #aurie-feedback posts: 28 alphanumeric characters.
AURIE_UID_RE = re.compile(r"\b([A-Za-z0-9]{28})\b")

# Channel ID → built-in format. Anything not in this table requires --format to be set
# explicitly (or falls back to the cancellation_feedback parser for back-compat).
CHANNEL_FORMAT_DEFAULTS = {
    CANCELLATION_FEEDBACK_CHANNEL: "cancellation_feedback",
    LEGACY_CANCELLATION_FEEDBACK_CHANNEL: "cancellation_feedback",
    AURIE_FEEDBACK_CHANNEL: "aurie_feedback",
}


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

    Thin wrapper over the shared `slack_read` helper (windowed history with cursor
    pagination + 429 retry, or single-message fetch by ts). Raises `SlackReadError`
    on a Slack error — caught in main() and surfaced as a clean exit.
    """
    if single_ts:
        return message_by_ts(channel, single_ts, token=token)
    return history(channel, token=token, oldest=oldest, latest=latest)


def ts_to_permalink(channel: str, ts: str) -> str:
    return f"https://{WORKSPACE}.slack.com/archives/{channel}/p{ts.replace('.', '')}"


def _nth_sunday(year: int, month: int, n: int) -> int:
    """Day-of-month of the nth Sunday. `weekday()` is Mon=0 … Sun=6."""
    first = datetime(year, month, 1)
    return 1 + (6 - first.weekday()) % 7 + 7 * (n - 1)


def _pacific_offset(naive: datetime) -> timedelta:
    """UTC offset for a naive US-Pacific wall clock, with the DST rule spelled out.

    Only reached when the host has no tzdata (Windows without the `tzdata`
    package), where `ZoneInfo` raises. Post-2007 US rule: DST runs from 02:00
    local on the second Sunday in March to 02:00 local on the first Sunday in
    November. The spring-forward hour (02:00-03:00 local) is not a wall clock any
    renderer can emit, so its handling here is arbitrary; the fall-back
    ambiguous hour resolves to DST, matching `ZoneInfo`'s `fold=0` default.
    """
    y = naive.year
    start = datetime(y, 3, _nth_sunday(y, 3, 2), 2, 0)
    end = datetime(y, 11, _nth_sunday(y, 11, 1), 2, 0)
    return timedelta(hours=-7 if start <= naive < end else -8)


def _as_utc(naive: datetime) -> datetime:
    """Read a naive `Purchased` wall clock as PURCHASED_TZ_NAME, return it in UTC."""
    try:
        from zoneinfo import ZoneInfo

        return naive.replace(tzinfo=ZoneInfo(PURCHASED_TZ_NAME)).astimezone(timezone.utc)
    except Exception:  # no tzdata on this host — fall back to the explicit rule
        return naive.replace(tzinfo=timezone(_pacific_offset(naive))).astimezone(timezone.utc)


def parse_purchased(raw: str) -> float | None:
    """Parse a k8bot `Purchased` string like 'Jun 4, 2026, 8:07 AM' to a UTC epoch.

    The string is a **wall clock in PURCHASED_TZ_NAME**, not UTC — see that
    constant. Localizing before converting is what keeps the result comparable to
    the Slack `ts`.
    """
    raw = raw.strip()
    for fmt in ("%b %d, %Y, %I:%M %p", "%b %d, %Y", "%B %d, %Y, %I:%M %p"):
        try:
            naive = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        return _as_utc(naive).timestamp()
    return None


def parse_entry(msg: dict, channel: str) -> dict | None:
    """Parse one `*New Cancellation Feedback*` message into a structured entry, or None."""
    text = (msg.get("text") or "").strip()
    if not text.startswith(HEADER):
        return None
    ts = msg.get("ts", "")

    def grp(pattern, flags=0):
        m = re.search(pattern, text, flags)
        return m.group(1).strip() if m else ""

    reason = grp(r"\*Reason:\*\s*(.+?)(?:\n|$)")
    feedback = grp(r"\*Feedback:\*\s*```(.*?)```", re.DOTALL)
    uid = grp(r"_User:\s*([A-Za-z0-9]+)")
    sub_type = grp(r"\|\s*Type:\s*([A-Za-z0-9_]+)")
    provider = grp(r"\|\s*Provider:\s*([A-Za-z0-9_]+)").rstrip("_")
    product = grp(r"\|\s*Product:\s*([A-Za-z0-9_]+)")
    subscription = grp(r"\bSubscription:\s*([A-Za-z0-9_\-]+)")
    purchased = grp(r"\|\s*Purchased:\s*([^|]+?)\s*\|")

    purchased_epoch = parse_purchased(purchased) if purchased else None
    days_to_cancel = None
    if purchased_epoch and ts:
        days_to_cancel = round((float(ts) - purchased_epoch) / 86400, 2)

    when = (
        datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        if ts else ""
    )
    return {
        "ts": ts,
        "when": when,
        "uid": uid,
        # Source channel, so a consumer flattening channels[] keeps each entry's
        # channel for dedup (conversations.replies) and posting (chat.postMessage).
        "channel_id": channel,
        "reason": reason,
        "feedback": feedback,
        "type": sub_type,
        "provider": provider,
        "product": product,
        "subscription": subscription,
        "purchased": purchased,
        # The same instant in UTC, so `days_to_cancel` can be re-derived against `when`
        # without knowing which clock `purchased` was rendered in.
        "purchased_utc": (
            datetime.fromtimestamp(purchased_epoch, tz=timezone.utc).strftime(
                "%Y-%m-%d %H:%M UTC"
            )
            if purchased_epoch else ""
        ),
        "days_to_cancel": days_to_cancel,
        "permalink": ts_to_permalink(channel, ts) if ts else "",
        "thread_ts": ts,  # ts (with the dot) is the thread_ts for chat.postMessage replies
    }


def apply_test_filter(entries: list[dict]) -> None:
    """Tag each entry in place with is_test + test_reason.

    Used by the ``cancellation_feedback`` format. The ``aurie_feedback`` format has no
    structured test indicator — bot posts are excluded upstream in
    ``should_skip_aurie_message`` and human-team posts are treated as real by definition.
    """
    from collections import Counter, defaultdict

    uid_counts = Counter(e["uid"] for e in entries if e["uid"])

    # Identify same-UID pairs whose two cancellations land within
    # DOUBLE_MODAL_WINDOW_SECONDS of each other — the AIiOS cancel-modal double-fire shape.
    # Only the LATER entry in the pair is flagged; the earlier one stays as the canonical
    # cancel record. Skipped for UIDs already caught by `uid-blast` (>=3 cancellations in
    # window) — those are QA loops, not modal-replay artifacts.
    by_uid: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        if e["uid"] and e["ts"]:
            by_uid[e["uid"]].append(e)
    double_fire_ts: set[str] = set()
    for uid, group in by_uid.items():
        if len(group) != 2:
            continue
        sorted_group = sorted(group, key=lambda x: float(x["ts"]))
        gap = float(sorted_group[1]["ts"]) - float(sorted_group[0]["ts"])
        if gap <= DOUBLE_MODAL_WINDOW_SECONDS:
            # Drop the later entry; keep the earlier one as canonical.
            double_fire_ts.add(sorted_group[1]["ts"])

    for e in entries:
        fb = e["feedback"].strip().lower()
        reasons = []
        if fb in TEST_EXACT or fb.startswith("test"):
            reasons.append("feedback-text-test")
        if e["uid"] and uid_counts[e["uid"]] >= TEST_BLAST_THRESHOLD:
            reasons.append(f"uid-blast-x{uid_counts[e['uid']]}")
        if e["product"] in DEV_STRIPE_PRICES:
            reasons.append("dev-environment-stripe-price")
        if e["ts"] in double_fire_ts:
            reasons.append("cancel-modal-double-fire")
        e["is_test"] = bool(reasons)
        e["test_reason"] = ",".join(reasons)


# --- aurie_feedback format ----------------------------------------------------------------
#
# Posts in #aurie-feedback are free-text quotes that a support team member pastes from an
# email reply. The shape is:
#
#     "<verbatim user quote, possibly multi-line>" - <28-char Firebase UID>
#
#     `<optional support annotation>`
#
# The leading/trailing quote characters can be ASCII (" ') or Slack's smart-quote
# autocorrects (curly “ ” ‘ ’). The trailing annotation lives on its own line, wrapped
# in single back-ticks; it's added by the support team member and must NOT bleed into
# the steering string handed to data:profile-user.

# Subtypes we always skip in aurie_feedback (housekeeping / off-format).
_AURIE_SKIP_SUBTYPES = {"channel_join", "channel_leave", "bot_message"}

# Leading quote characters we strip from the verbatim text — Slack autocorrects "..." to
# “...” on most clients, and the support team mixes both. We only strip a matching pair
# at the very start/end of the feedback so we don't damage in-body quotation marks.
_AURIE_QUOTE_PAIRS = (("“", "”"), ("\"", "\""), ("‘", "’"), ("'", "'"))

# Trailing back-tick-fenced annotation on its own line. Single back-ticks; the regex
# captures the inner text so the surrounding `s are stripped.
_AURIE_ANNOTATION_RE = re.compile(r"\n\s*`([^`\n]+)`\s*$")


def should_skip_aurie_message(msg: dict) -> str:
    """Return a non-empty skip reason for #aurie-feedback messages that aren't feedback posts.

    Returns "" when the message looks like a real free-text feedback post worth parsing.
    """
    if msg.get("bot_id"):
        return "bot-post"
    subtype = msg.get("subtype")
    if subtype in _AURIE_SKIP_SUBTYPES:
        return f"subtype-{subtype}"
    text = (msg.get("text") or "").strip()
    if not text:
        return "empty-text"
    return ""


def _strip_outer_quotes(text: str) -> str:
    """Remove a single matching pair of quote characters wrapping ``text``."""
    if len(text) < 2:
        return text
    first, last = text[0], text[-1]
    for open_q, close_q in _AURIE_QUOTE_PAIRS:
        if first == open_q and last == close_q:
            return text[1:-1].strip()
    return text


def parse_aurie_feedback_entry(msg: dict, channel: str) -> dict | None:
    """Parse one #aurie-feedback message into the shared entry shape, or None.

    Returns None when the message isn't a feedback post (use ``should_skip_aurie_message``
    upstream to capture the *reason* for telemetry; this function just declines).
    """
    if should_skip_aurie_message(msg):
        return None

    raw_text = (msg.get("text") or "").strip()
    ts = msg.get("ts", "")

    # Pull off the optional trailing `support annotation` line first so it doesn't get
    # caught up in the UID-suffix split below.
    support_annotation = ""
    annot_match = _AURIE_ANNOTATION_RE.search(raw_text)
    if annot_match:
        support_annotation = annot_match.group(1).strip()
        body = raw_text[: annot_match.start()].rstrip()
    else:
        body = raw_text

    # The UID is the 28-alphanumeric token at the very end of the message body. Anchor on
    # the LAST match in the body — UIDs that appear earlier (extremely unlikely in real
    # posts, but possible if a user quoted one) shouldn't match.
    uid_matches = list(AURIE_UID_RE.finditer(body))
    if not uid_matches:
        return None
    uid_match = uid_matches[-1]
    # Require the UID to live in the final stretch of the message (last ~80 chars) and to
    # be reachable from the end through whitespace and a `-` separator. This rejects
    # mid-body UIDs while staying tolerant of trailing whitespace / newlines.
    tail = body[uid_match.end():].strip()
    if tail:
        return None
    uid = uid_match.group(1)

    # Verbatim feedback text = everything before the UID, with the trailing ``- `` (or
    # ``— ``) separator dropped, then outer matching quotes stripped, then re-trimmed.
    pre_uid = body[: uid_match.start()].rstrip()
    pre_uid = re.sub(r"\s*[-—]\s*$", "", pre_uid).rstrip()
    feedback = _strip_outer_quotes(pre_uid).strip()
    if not feedback:
        # No verbatim text in front of the UID — almost certainly noise (someone pasted
        # just a UID into the channel). Skip rather than emit a content-less entry.
        return None

    when = (
        datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        if ts else ""
    )
    return {
        "ts": ts,
        "when": when,
        "uid": uid,
        # Source channel, so a consumer flattening channels[] keeps each entry's
        # channel for dedup (conversations.replies) and posting (chat.postMessage).
        "channel_id": channel,
        # The aurie_feedback format has no structured cancellation category. The SKILL.md
        # maps an empty reason to ``context.category = "free-text aurie-feedback channel post"``.
        "reason": "",
        "feedback": feedback,
        "support_annotation": support_annotation,
        # No k8bot metadata line — these fields stay empty for schema compatibility.
        "type": "",
        "provider": "",
        "product": "",
        "subscription": "",
        "purchased": "",
        "purchased_utc": "",
        "days_to_cancel": None,
        "permalink": ts_to_permalink(channel, ts) if ts else "",
        "thread_ts": ts,
        # Always real in this format — see should_skip_aurie_message for the upstream
        # filter. Kept for output-schema parity with the cancellation_feedback path.
        "is_test": False,
        "test_reason": "",
        "posted_by": msg.get("user", ""),
    }


def parse_entries_aurie_feedback(raw: list[dict], channel: str) -> tuple[list[dict], list[dict]]:
    """Parse a #aurie-feedback message batch into (real_entries, skipped_records).

    ``skipped_records`` is a list of ``{ts, permalink, skip_reason}`` for messages we
    intentionally dropped (bot posts, channel_join events, off-format), so the SKILL.md
    can surface counts the same way the cancellation_feedback path surfaces
    ``test_reasons``.
    """
    entries: list[dict] = []
    skipped: list[dict] = []
    for msg in raw:
        reason = should_skip_aurie_message(msg)
        if reason:
            ts = msg.get("ts", "")
            skipped.append({
                "ts": ts,
                "permalink": ts_to_permalink(channel, ts) if ts else "",
                "skip_reason": reason,
            })
            continue
        entry = parse_aurie_feedback_entry(msg, channel)
        if entry is None:
            ts = msg.get("ts", "")
            skipped.append({
                "ts": ts,
                "permalink": ts_to_permalink(channel, ts) if ts else "",
                "skip_reason": "no-uid-match",
            })
            continue
        entries.append(entry)
    return entries, skipped


def resolve_format(channel: str, requested: str) -> str:
    """Return the concrete format to use given the channel + the user's --format flag."""
    if requested != "auto":
        return requested
    fmt = CHANNEL_FORMAT_DEFAULTS.get(channel)
    if fmt:
        return fmt
    # Unknown channel + auto → preserve the historical default (cancellation_feedback)
    # rather than guessing wrong. Callers can override with an explicit --format.
    return "cancellation_feedback"


def process_channel(
    token: str,
    channel: str,
    fmt: str,
    oldest: float,
    latest: float,
    single_ts: str | None,
    label: str,
) -> dict:
    """Fetch + parse + filter one channel and return the single-channel output dict.

    The returned shape is exactly what the script has historically emitted for a
    single-channel invocation — used directly by single-channel mode, and wrapped
    into ``channels[]`` for ``--channel all``.
    """
    from collections import Counter

    raw = fetch_messages(token, channel, oldest, latest, single_ts)

    if fmt == "cancellation_feedback":
        entries = [e for m in raw for e in [parse_entry(m, channel)] if e]
        apply_test_filter(entries)
        entries.sort(key=lambda e: float(e["ts"] or 0), reverse=True)
        real = [e for e in entries if not e["is_test"]]
        tests = [e for e in entries if e["is_test"]]
        with_text = [e for e in real if e["feedback"].strip()]
        reason_mix = Counter(e["reason"] for e in real)
        provider_mix = Counter(e["provider"] for e in real)
        return {
            "channel": channel,
            "format": fmt,
            "window": label,
            "counts": {
                "parsed": len(entries),
                "real": len(real),
                "test_dropped": len(tests),
                "real_with_feedback_text": len(with_text),
            },
            "reason_mix": dict(reason_mix.most_common()),
            "provider_mix": dict(provider_mix.most_common()),
            "test_reasons": dict(Counter(e["test_reason"] for e in tests).most_common()),
            "entries": real,
            "test_entries": [
                {"uid": e["uid"], "reason": e["reason"], "feedback": e["feedback"],
                 "test_reason": e["test_reason"], "permalink": e["permalink"]}
                for e in tests
            ],
        }

    # aurie_feedback
    entries, skipped = parse_entries_aurie_feedback(raw, channel)
    entries.sort(key=lambda e: float(e["ts"] or 0), reverse=True)
    with_text = [e for e in entries if e["feedback"].strip()]
    annotated = [e for e in entries if e.get("support_annotation")]
    skip_reasons = Counter(s["skip_reason"] for s in skipped)
    return {
        "channel": channel,
        "format": fmt,
        "window": label,
        "counts": {
            # ``parsed`` is the count of feedback posts that actually parsed —
            # matching the cancellation_feedback shape. Bot/channel-housekeeping
            # noise lives in ``skipped`` instead of inflating this number.
            "parsed": len(entries),
            "real": len(entries),
            "skipped": len(skipped),
            "real_with_feedback_text": len(with_text),
            "with_support_annotation": len(annotated),
        },
        # No structured reason / provider in this format. Reported as empty dicts so
        # callers that union the two outputs see the same keys.
        "reason_mix": {},
        "provider_mix": {},
        "skip_reasons": dict(skip_reasons.most_common()),
        "entries": entries,
        "skipped_messages": skipped,
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(description="Fetch + parse + filter Slack feedback messages.")
    ap.add_argument("--channel", default=DEFAULT_CHANNEL,
                    help=(
                        f"Slack channel ID, or a sentinel that sweeps several channels in "
                        f"one call. Defaults to {DEFAULT_CHANNEL} (#ai-cancellation-feedback). "
                        f"Pass {AURIE_FEEDBACK_CHANNEL} for #aurie-feedback, "
                        f"{LEGACY_CANCELLATION_FEEDBACK_CHANNEL} for pre-split cancellations "
                        f"in #ai-feedback, '{CANCELLATIONS_SENTINEL}' for "
                        f"{' + '.join(CANCELLATION_CHANNELS)}, or '{ALL_SENTINEL}' for "
                        f"{' + '.join(ALL_CHANNELS)} — sentinels wrap their results under "
                        f"channels[]."
                    ))
    ap.add_argument("--format", default="auto",
                    choices=["auto", "cancellation_feedback", "aurie_feedback"],
                    help="Parser shape. 'auto' derives from --channel; pass an explicit "
                         "value to override for a non-default channel. Only meaningful "
                         "when --channel is a single channel ID — incompatible with a "
                         "sentinel --channel.")
    ap.add_argument("--window", default="7d", help="Nd or YYYY-MM-DD..YYYY-MM-DD")
    ap.add_argument("--single-ts", default=None,
                    help="Scope to one message by its ts (e.g. 1780465597.965429). "
                         "Single-channel only — incompatible with a sentinel --channel.")
    ap.add_argument("--token", default=os.environ.get("SLACK_BOT_TOKEN"))
    args = ap.parse_args()

    if not args.token:
        raise SystemExit("SLACK_BOT_TOKEN not set (pass --token or export it).")

    # Sentinel compatibility gates. Both errors are explicit so the orchestrator /
    # caller sees the actionable message rather than a downstream Slack 400 or a stack
    # trace from passing the sentinel to conversations.history.
    if args.channel in SENTINEL_CHANNELS:
        if args.single_ts:
            raise SystemExit(
                f"--single-ts requires a specific --channel, not '{args.channel}'"
            )
        if args.format != "auto":
            raise SystemExit("--format only applies when --channel is a single channel ID")

    if args.single_ts:
        oldest = latest = float(args.single_ts)
        label = f"single message {args.single_ts}"
    else:
        oldest, latest, label = resolve_window(args.window)

    try:
        if args.channel in SENTINEL_CHANNELS:
            # Sweep the sentinel's channels under one call. The shared window is the same
            # for all of them — surfaced once at the top level so the orchestrator's UI can
            # label the sweep without reading into per-channel sub-objects.
            per_channel = [
                process_channel(
                    args.token, ch, resolve_format(ch, "auto"), oldest, latest, None, label,
                )
                for ch in SENTINEL_CHANNELS[args.channel]
            ]
            by_channel = {sub["channel"]: len(sub["entries"]) for sub in per_channel}
            out: dict = {
                "format": args.channel,
                "window": label,
                "channels": per_channel,
                "totals": {
                    "real_entries_across_channels": sum(by_channel.values()),
                    "by_channel": by_channel,
                },
            }
        else:
            fmt = resolve_format(args.channel, args.format)
            out = process_channel(
                args.token, args.channel, fmt, oldest, latest, args.single_ts, label,
            )
    except SlackReadError as exc:
        # Preserve the historical clean exit (message, no traceback) on a Slack error.
        raise SystemExit(str(exc))

    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
