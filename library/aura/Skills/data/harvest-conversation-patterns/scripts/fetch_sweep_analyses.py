#!/usr/bin/env python3
"""Fetch rated entries + bot-posted analyses from #ai-data-intelligence.

For each k8bot *[TYPE] RATED* message in the window that has a non-empty
feedback field and a conversation_id, reads the Slack thread and extracts
any reply posted by the analysis bot (U0ATMPESR37). Outputs structured JSON
for the harvest-conversation-patterns SKILL.md to cluster and filter.

One entry per rating message, keyed by `rating_ts` (the Slack ts) — not per rated
object. `source_id` repeats across entries whenever a thread was rated more than
once, and the batch reports how often (`repeated_source_ids`).

Only pure-infra entries are dropped. Rating **volume** never drops an entry — prolific
UIDs are annotated `high_volume` and reported (ENG-1048 / ENG-1132); see the threshold
block below for why, and why its span is fixed at 7 days rather than the pass's width.

Uses the shared slack_read helper (copied into this skill's scripts/ dir —
canonical at plugins/shared/skills/read-slack/scripts/slack_read.py).

Auth: SLACK_BOT_TOKEN env (or --token).

Forces UTF-8 stdout so → / — in analysis text don't raise on Windows cp1252.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from slack_read import SlackReadError, history, replies  # noqa: E402

DEFAULT_CHANNEL = "C07MB0TMF5J"  # #ai-data-intelligence
BOT_UID = "U0ATMPESR37"          # analysis bot that posts threaded reviews
WORKSPACE = "auramobile"

# Rating volume is not a test signal — it tracks engagement, so the most prolific
# raters in any window are long-tenured paying accounts rather than QA loops
# (ENG-1048). Prolific UIDs are ANNOTATED (`high_volume=True`) and reported; nothing
# is ever dropped for volume. This is the writer for `Signals/Conversation Patterns`,
# so a drop here would be unrecoverable — the table keeps no trace of a filtered row,
# and a later reader cannot tell a filtered corpus from a complete one.
#
# The bar is 20 ratings inside any ROLLING 7-day span, and the span belongs to the rule
# rather than to the caller's window. Counting over the window makes the verdict depend
# on how wide the pass is: the same UID rating at the same rate qualifies in a wide
# re-harvest and not in a weekly sweep, which is a filter whose meaning nobody can state
# without also naming the pass. On the cadence's own 7-day width the rolling span and
# the window coincide, so this is exactly the canonical rule there. ENG-1132 has the
# measured cost of getting it wrong.
HIGH_VOLUME_UID_THRESHOLD = 20  # report-only
HIGH_VOLUME_SPAN_SECONDS = 7 * 86400

# k8bot RATED shape headers → shape key (must start-of-text match)
SHAPE_HEADERS: dict[str, str] = {
    "*QUICK PROCESSING RATED*": "qp",
    "*COACHING RATED*": "coaching",
    "*CONVERSATION RATED*": "conversation",
    "*CHAT MESSAGE RATED*": "chat",
    "*JOURNAL RATED*": "journal",
    "*PROFILE RATED*": "profile",
}

# Infra substrings — voice/connectivity issues, not content quality
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

# Actual k8bot footer format:
#   `[dialogue: <uuid>, conversation: <uuid>, user: <uid>, prompt:<name>:<ver>]`
# Backtick-wrapped, or bare at end of message. Extracted groups below.
_FOOTER_RE = re.compile(r"`\[([^\]]+)\]`|(?:\[([^\]]+)\])\s*`?\s*$", re.DOTALL)

# The footer's `user:` field, captured by its DELIMITER rather than by an
# enumerated character class — Aura ids include UUIDs, Firebase push keys
# (leading `-`, embedded `_`) and provider-prefixed forms (`google:112…`), and
# a class that misses one truncates it silently. Here that also collides users:
# `google:…` would collapse to `google`, summing distinct people onto one id, which
# the volume annotation below then reads as a single prolific account. No id in the
# 7.57M-row users table contains `,` or `]`, so matching to the comma truncates
# none. ENG-1059.
UID_RE = r"user:\s*([^,\]]+)"

# Warn-only shape check — never drops or rewrites an entry. See the same block
# in `data:investigate-user-rating`'s fetch_ratings.py for the calibration.
UID_SHAPE_RE = re.compile(
    r"[A-Za-z0-9]{20,32}"                                       # Firebase 28 + bare alnum
    r"|[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}"    # UUID
    r"|-[A-Za-z0-9_-]{19}"                                      # Firebase push key
    r"|[a-z]+:[A-Za-z0-9._\-]+"                                 # provider-prefixed
)

# Polarity — three observed formats
_POLARITY_POSITIVE = (":thumbsup:", ":+1:", "\U0001f44d")
_POLARITY_NEGATIVE = (":thumbsdown:", ":-1:", "\U0001f44e")


def resolve_window(window: str) -> tuple[float, float, str]:
    """Return (oldest_epoch, latest_epoch, label) for Nd or YYYY-MM-DD..YYYY-MM-DD."""
    now = time.time()
    if ".." in window:
        start_s, end_s = window.split("..", 1)
        oldest = datetime.fromisoformat(start_s).replace(tzinfo=timezone.utc).timestamp()
        latest = datetime.fromisoformat(end_s).replace(tzinfo=timezone.utc).timestamp() + 86399
        label = window
    else:
        m = re.fullmatch(r"(\d+)d", window.strip(), re.I)
        if not m:
            raise ValueError(f"Unknown window spec: {window!r}  (use Nd or YYYY-MM-DD..YYYY-MM-DD)")
        days = int(m.group(1))
        oldest = now - days * 86400
        latest = now
        label = f"{days}d"
    return oldest, latest, label


def _grp(text: str, pattern: str, flags: int = 0) -> str:
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else ""


def _normalise_feedback(raw: str) -> str:
    stripped = raw.strip()
    if not stripped or stripped.lower() in {"no feedback", "no feedback."}:
        return ""
    return stripped


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

    Duplicated in `report-conversation-patterns/scripts/sample.py` — both write
    `uid_prefix` into the same Signals table and skills are self-contained, so the
    two copies must stay in step.
    """
    uid = uid or ""
    if ":" in uid:
        provider, _, rest = uid.partition(":")
        return f"{provider}:{rest[:8]}"
    return uid[:8]


def permalink(channel: str, ts: str) -> str:
    """Return the Slack permalink for `ts` in `channel`.

    Built here rather than left to the caller because the `p<digits>` segment is the
    `rating_ts` with the dot removed — the same transform the ENG-1115 backfill had to
    invert to recover a ts from an already-written row. Keeping both directions in one
    place is what makes `Slack URL` a reliable fallback source for `Rating ID`.
    """
    return f"https://{WORKSPACE}.slack.com/archives/{channel}/p{ts.replace('.', '')}"


def _detect_polarity(text: str) -> str:
    if any(tok in text for tok in _POLARITY_POSITIVE):
        return "positive"
    if any(tok in text for tok in _POLARITY_NEGATIVE):
        return "negative"
    if ":neutral_face:" in text:
        return "neutral"
    return "unknown"


def parse_rated_message(msg: dict) -> dict | None:
    """Parse one k8bot RATED message; return structured dict or None if not a match."""
    text = (msg.get("text") or "").strip()
    if not text:
        return None

    # Detect shape (header appears at start of message)
    shape = None
    for header, key in SHAPE_HEADERS.items():
        if text.startswith(header):
            shape = key
            break
    if shape is None:
        return None

    # Footer: `` `[key: val, ...]` `` or bare `[...]` at end
    fm = _FOOTER_RE.search(text)
    footer = (fm.group(1) or fm.group(2) or "") if fm else ""

    # UID always in footer as `user: <uid>`
    uid = _grp(footer, UID_RE)
    if not uid:
        return None
    if not UID_SHAPE_RE.fullmatch(uid):
        print(
            "warning: user id of unrecognised shape "
            f"{re.sub(r'[A-Za-z0-9]+', 'X', uid)}|len{len(uid)} — parsed as-is; "
            "add the family to UID_SHAPE_RE if it is legitimate",
            file=sys.stderr,
        )

    # Conversation ID — conversation-pivot shapes have `conversation: <uuid>` in footer
    conv_id = _grp(footer, r"conversation:\s*([0-9a-fA-F\-]+)")

    # Journal and profile shapes carry `id: <uuid>` instead — the journal entry id
    # and the rendererRating row id respectively. Same role: the stable id of the
    # thing that was rated, which is what the Signals table dedups on.
    entity_id = _grp(footer, r"\bid:\s*([0-9a-fA-F\-]+)")

    # Profile ratings name the rated card when a single renderer was rated;
    # absent means the whole profile.
    renderer_key = _grp(footer, r"renderer:\s*([a-z0-9\-]+)")

    # Prompter version — `prompt:<name>:<version>` in footer
    prompt_raw = _grp(footer, r"prompt:(.+?)(?:,\s*|\]|$)")
    if ":" in prompt_raw:
        prompter_version = prompt_raw.rsplit(":", 1)[1].strip()
    else:
        prompter_version = ""

    polarity = _detect_polarity(text)

    # Feedback in triple-backtick fenced block
    feedback = _normalise_feedback(_grp(text, r"```(.*?)```", re.DOTALL))

    # Infra check on feedback text
    fb_lower = feedback.lower()
    is_infra = any(s in fb_lower for s in INFRA_SUBSTRINGS)

    return {
        "ts": msg["ts"],
        # Dedup key for the Signals row (its `Rating ID` column): one rating event,
        # one Slack ts. The rated object's id is NOT unique per rating — a thread can be
        # rated repeatedly, and `chat` rates per message — so keying on it drops the
        # repeats (ENG-1115).
        #
        # Named `rating_ts`, not `rating_id`, on purpose: the sibling `fetch_ratings.py`
        # in this family already uses `rating_id` for the profile `rendererRating` row
        # id. Two meanings behind one field name across two frames feeding one sampler
        # is a silent-divergence trap. Do not "harmonise" this to `rating_id`.
        "rating_ts": msg["ts"],
        "uid": uid,
        "uid_prefix": uid_prefix(uid),  # PII: prefix only, never a full uid
        "conversation_id": conv_id,
        # The id of whatever was rated. Falls back to the footer `id:` so journal and
        # profile entries carry one too. Groups a thread's ratings; never a dedup key.
        "source_id": conv_id or entity_id,
        "renderer_key": renderer_key or None,
        "feedback": feedback,
        "prompter_version": prompter_version,
        "polarity": polarity,
        "shape": shape,
        "is_infra": is_infra,
    }


def peak_span_counts(parsed: list[dict]) -> dict[str, int]:
    """Return {uid: most ratings that fall inside any rolling HIGH_VOLUME_SPAN_SECONDS}.

    The peak of a rolling span, not a total over the pass, is what makes the verdict
    independent of how wide the pass is: the same UID rating at the same rate gets the
    same answer from a 7-day harvest and a 70-day re-harvest. A window-relative total
    cannot do that — widening the pass raises every count while the bar stays put, so
    the pass's width silently decides who counts as a test loop (ENG-1132).

    The peak also keeps a genuine QA burst visible, which a rate averaged over the
    whole pass would flatten away: 30 ratings in one afternoon is 30 in its peak week
    whether the pass is 7 days or 70.

    Entries with no uid are absent from the result; callers read misses as 0.
    """
    by_uid: dict[str, list[float]] = {}
    for p in parsed:
        if p.get("uid"):
            by_uid.setdefault(p["uid"], []).append(float(p["ts"]))

    peaks: dict[str, int] = {}
    for uid, stamps in by_uid.items():
        stamps.sort()
        lo = peak = 0
        for hi in range(len(stamps)):
            while stamps[hi] - stamps[lo] > HIGH_VOLUME_SPAN_SECONDS:
                lo += 1
            peak = max(peak, hi - lo + 1)
        peaks[uid] = peak
    return peaks


def fetch_bot_analysis(channel: str, thread_ts: str, tok: str) -> str:
    """Return the text of the first BOT_UID reply in a thread, or empty string."""
    try:
        msgs = replies(channel, thread_ts, token=tok)
    except SlackReadError:
        return ""
    for m in msgs[1:]:  # skip parent
        if m.get("user") == BOT_UID:
            return m.get("text", "")
    return ""


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(description="Fetch rated entries + bot analyses from Slack.")
    ap.add_argument("--window", default="7d", help="Window: Nd or YYYY-MM-DD..YYYY-MM-DD")
    ap.add_argument("--channel", default=DEFAULT_CHANNEL)
    ap.add_argument("--token", default=os.environ.get("SLACK_BOT_TOKEN", ""))
    ap.add_argument("--json", dest="json_out", default="", help="Write output to this path instead of stdout")
    ap.add_argument("--no-analysis", action="store_true", help="Skip fetching thread analyses (faster for large windows)")
    args = ap.parse_args()

    if not args.token:
        print("ERROR: SLACK_BOT_TOKEN not set", file=sys.stderr)
        sys.exit(1)

    oldest, latest, label = resolve_window(args.window)

    print(f"[fetch_sweep_analyses] window={label}  channel={args.channel}", file=sys.stderr)

    # Pull channel history
    try:
        msgs = history(args.channel, oldest=oldest, latest=latest, token=args.token)
    except SlackReadError as e:
        print(f"ERROR reading channel: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"[fetch_sweep_analyses] {len(msgs)} messages in window", file=sys.stderr)

    # Parse rated messages
    parsed: list[dict] = []
    for m in msgs:
        p = parse_rated_message(m)
        if p is None:
            continue
        parsed.append(p)

    # Annotate volume, drop only infra. Volume never drops (ENG-1048/ENG-1132) — a
    # prolific rater is the most engaged kind of real user, and this is the writer for
    # the durable table.
    peaks = peak_span_counts(parsed)
    high_volume_uids: dict[str, int] = {}
    entries: list[dict] = []
    skipped_infra = 0
    for p in parsed:
        peak = peaks.get(p["uid"], 0)
        p["high_volume"] = peak >= HIGH_VOLUME_UID_THRESHOLD
        if p["is_infra"]:
            skipped_infra += 1
            continue
        if p["high_volume"]:
            # Keyed by prefix, not uid — this dict is output, and output is prefix-only
            # (PII rule below). Two uids can share a prefix, so merge on the max.
            pfx = p["uid_prefix"]
            high_volume_uids[pfx] = max(high_volume_uids.get(pfx, 0), peak)
        entries.append(p)

    high_volume_entries = sum(1 for e in entries if e["high_volume"])

    print(
        f"[fetch_sweep_analyses] {len(parsed)} rated → {len(entries)} entries "
        f"({skipped_infra} infra skipped; 0 dropped for volume — "
        f"{high_volume_entries} entries from {len(high_volume_uids)} high-volume "
        f"UID(s) annotated, not dropped)",
        file=sys.stderr,
    )

    # Every entry's thread is read: the sweep posts its analysis as a threaded
    # reply under any rating shape, and fetch_bot_analysis keys on `ts`, so
    # there is nothing shape-specific to gate on.
    # Neutral ratings without feedback text are still signal — always check their threads.
    with_feedback = [e for e in entries if (e.get("feedback") or "").strip()]
    threadable = [e for e in entries if e.get("ts")]
    with_analysis = 0

    if not args.no_analysis:
        for i, e in enumerate(threadable, 1):
            print(
                f"[fetch_sweep_analyses] reading thread {i}/{len(threadable)}  ts={e['ts'][:10]}",
                file=sys.stderr,
            )
            analysis = fetch_bot_analysis(args.channel, e["ts"], args.token)
            e["bot_analysis"] = analysis
            if analysis:
                with_analysis += 1
    else:
        for e in threadable:
            e["bot_analysis"] = ""

    # Belt-and-braces: an entry whose thread read failed still needs the key set
    for e in entries:
        if "bot_analysis" not in e:
            e["bot_analysis"] = ""
        e["permalink"] = permalink(args.channel, e["ts"])
        # Strip uid to prefix only in output (PII rule)
        del e["uid"]
        del e["is_infra"]

    # Repeat diagnostics. Two or more ratings on one thread are normal data, and also
    # exactly what a `Conversation ID` dedup key discards — so the batch reports how
    # much that would cost rather than leaving it invisible (ENG-1115).
    src_counts: dict[str, int] = {}
    for e in entries:
        if e.get("source_id"):
            src_counts[e["source_id"]] = src_counts.get(e["source_id"], 0) + 1
    repeated = {s: c for s, c in src_counts.items() if c > 1}

    output = {
        "window": label,
        "from_ts": oldest,
        "to_ts": latest,
        "total_rated": len(parsed),
        "recorded": len(entries),
        "skipped_infra": skipped_infra,
        # Constant 0, and explicit rather than absent: a reader checking whether a
        # volume filter ran gets an answer instead of having to infer one from a
        # missing key. Volume is reported (below), never subtracted.
        "skipped_volume": 0,
        "high_volume_entries": high_volume_entries,
        "high_volume_uids": dict(
            sorted(high_volume_uids.items(), key=lambda kv: -kv[1])
        ),
        "high_volume_threshold": HIGH_VOLUME_UID_THRESHOLD,
        "high_volume_span_days": HIGH_VOLUME_SPAN_SECONDS // 86400,
        "with_feedback": len(with_feedback),
        "with_analysis": with_analysis,
        "distinct_source_ids": len(src_counts),
        "repeated_source_ids": len(repeated),
        "rows_beyond_first_per_source": sum(c - 1 for c in repeated.values()),
        "entries": entries,
    }

    out_str = json.dumps(output, ensure_ascii=False, indent=2)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            f.write(out_str)
        print(f"[fetch_sweep_analyses] wrote {args.json_out}", file=sys.stderr)
    else:
        print(out_str)


if __name__ == "__main__":
    main()
