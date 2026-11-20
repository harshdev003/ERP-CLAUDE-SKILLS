#!/usr/bin/env python3
"""Fetch a month of k8bot `*[TYPE] RATED*` ratings and aggregate them by type + polarity.

Deterministic core of the `data:chart-monthly-ratings` skill. Pulls the
#ai-data-intelligence channel history for one calendar month, parses all six
RATED shapes, drops test entries (UID blast), then aggregates real entries into
a per-shape × polarity matrix the SKILL.md renders (inline widget + matplotlib
PNGs for Slack). Profile ratings additionally break out by rated renderer.

The parsing / auth / test-filtering core is a self-contained copy of
`data:investigate-user-rating/scripts/fetch_ratings.py` (Architecture principle 1
— skills don't reach into sibling skill folders). Keep the two in sync when the
RATED message shapes change upstream.

Usage:
    # last complete month, JSON summary to stdout
    python3 chart_ratings.py
    # explicit month
    python3 chart_ratings.py --month 2026-05
    # also render PNG charts (absolute + normalized) into a dir
    python3 chart_ratings.py --month 2026-05 --png /tmp/rating_charts

Auth: SLACK_BOT_TOKEN (env, or --token). The bot must be a member of the channel.

Slack reads go through the shared `slack_read` helper (copied into this skill's
`scripts/` dir — the canonical `shared:read-slack` library; see AINative `CLAUDE.md`
→ "Canonical source for `slack_read.py`"), which owns transport (requests, curl
fallback; never urllib), cursor pagination, and the 429-retry.

PNG rendering requires matplotlib; it is imported lazily only when --png is passed,
so the JSON path has no hard dependency on it.

Stdlib only (+ matplotlib when --png). Forces UTF-8 stdout (Windows cp1252 would
choke on the → / — this script emits in error / label strings).
"""
from __future__ import annotations

import argparse
import calendar
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from slack_read import SlackReadError, history  # noqa: E402
import internal_accounts  # noqa: E402

DEFAULT_CHANNEL = "C07MB0TMF5J"  # #ai-data-intelligence
WORKSPACE = "auramobile"

# Rating volume is not a test signal — it tracks engagement, not QA, so the most
# prolific raters in any month are long-tenured paying accounts (ENG-1048). Over a
# calendar month the effect is severe: a user rating twice a week clears any small
# threshold by mid-month. Prolific UIDs are annotated and reported so a genuine QA
# loop stays visible; the annotation never drops an entry.
HIGH_VOLUME_UID_THRESHOLD = 40  # report-only; ~a month of daily rating

# The footer's `user:` field, captured by its DELIMITER rather than by an
# enumerated character class — Aura ids include UUIDs, Firebase push keys
# (leading `-`, embedded `_`) and provider-prefixed forms (`google:112…`), and
# a class that misses one truncates it silently, collapsing distinct people
# onto one key (every `google:…` becomes `google`) so uid_counts and the
# allowlist match both read wrong. No id in the 7.57M-row users table contains
# `,` or `]`, so matching to the comma truncates none. ENG-1059.
UID_RE = r"user:\s*([^,\]]+)"

# Warn-only shape check — never drops or rewrites an entry. See the same block
# in `data:investigate-user-rating`'s fetch_ratings.py for the calibration.
UID_SHAPE_RE = re.compile(
    r"[A-Za-z0-9]{20,32}"                                       # Firebase 28 + bare alnum
    r"|[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}"    # UUID
    r"|-[A-Za-z0-9_-]{19}"                                      # Firebase push key
    r"|[a-z]+:[A-Za-z0-9._\-]+"                                 # provider-prefixed
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
# Display order + friendly labels for charts
SHAPE_ORDER = ["chat", "journal", "profile", "conversation", "qp", "coaching"]
SHAPE_LABELS = {
    "chat": "chat",
    "journal": "journal",
    "profile": "profile",
    "conversation": "conversation",
    "qp": "quick-processing",
    "coaching": "coaching",
}
POLARITIES = ["positive", "neutral", "negative"]
# green / amber / red — matches the in-session widget palette
POLARITY_COLORS = {"positive": "#639922", "neutral": "#EF9F27", "negative": "#E24B4A"}


def resolve_month(month: str | None) -> tuple[float, float, str]:
    """Return (oldest_epoch, latest_epoch, 'YYYY-MM') for a month spec.

    `month` is `YYYY-MM`; when None, defaults to the last *complete* calendar
    month relative to now (running in June → May).
    """
    now = datetime.now(timezone.utc)
    if month:
        m = re.fullmatch(r"(\d{4})-(\d{2})", month.strip())
        if not m:
            raise SystemExit(f"Unrecognised --month '{month}'. Use YYYY-MM.")
        year, mon = int(m.group(1)), int(m.group(2))
        if not 1 <= mon <= 12:
            raise SystemExit(f"Month out of range in '{month}'.")
    else:
        # last complete month
        year, mon = now.year, now.month
        mon -= 1
        if mon == 0:
            mon = 12
            year -= 1

    start = datetime(year, mon, 1, tzinfo=timezone.utc)
    last_day = calendar.monthrange(year, mon)[1]
    end = datetime(year, mon, last_day, 23, 59, 59, tzinfo=timezone.utc)
    return start.timestamp(), end.timestamp(), f"{year:04d}-{mon:02d}"


def fetch_messages(token: str, channel: str, oldest: float, latest: float) -> list[dict]:
    """Return raw messages in [oldest, latest] (paginated).

    Thin wrapper over the shared `slack_read` helper. Raises `SlackReadError` on a
    Slack error — caught in main() and surfaced as a clean exit.
    """
    return history(channel, token=token, oldest=oldest, latest=latest)


def ts_to_permalink(channel: str, ts: str) -> str:
    return f"https://{WORKSPACE}.slack.com/archives/{channel}/p{ts.replace('.', '')}"


def _grp(text: str, pattern: str, flags: int = 0) -> str:
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else ""


def detect_shape(text: str) -> str | None:
    for shape, header in SHAPE_HEADERS.items():
        if text.startswith(header):
            return shape
    return None


def _polarity(text: str) -> str:
    if ":thumbsup:" in text or ":+1:" in text or "\U0001f44d" in text:
        return "positive"
    if ":thumbsdown:" in text or ":-1:" in text or "\U0001f44e" in text:
        return "negative"
    if ":neutral_face:" in text:
        return "neutral"
    return "unknown"


def parse_conversation_entry(msg: dict, channel: str, shape: str) -> dict:
    """Parse a conversation-pivot RATED message (qp / coaching / conversation / chat)."""
    text = (msg.get("text") or "").strip()
    ts = msg.get("ts", "")
    footer = _grp(text, r"`\[([^\]]+)\]`") or _grp(text, r"\[([^\]]+)\](?:`\s*)?$", re.DOTALL)
    uid = _grp(footer, UID_RE)
    prompt_raw = _grp(footer, r"prompt:(.+?)(?:\]|$)")
    if ":" in prompt_raw:
        name, version = prompt_raw.rsplit(":", 1)
        prompter_name, prompter_version = name.strip(), version.strip()
    else:
        prompter_name, prompter_version = prompt_raw.strip(), ""
    return {
        "ts": ts,
        "shape": shape,
        "uid": uid,
        "polarity": _polarity(text),
        "prompter_name": prompter_name,
        "prompter_version": prompter_version,
        "permalink": ts_to_permalink(channel, ts) if ts else "",
        "is_test": False,
        "test_reason": "",
    }


def parse_footer_entry(msg: dict, channel: str, shape: str) -> dict:
    """Parse a shape whose footer carries only `[id: …, user: …]` (journal / profile).

    Neither shape names a prompter. `*PROFILE RATED*` may add `renderer: <key>`
    when a single card was rated rather than the whole profile.
    """
    text = (msg.get("text") or "").strip()
    ts = msg.get("ts", "")
    footer = _grp(text, r"`\[([^\]]+)\]`") or _grp(text, r"\[([^\]]+)\](?:`\s*)?$", re.DOTALL)
    uid = _grp(footer, UID_RE)
    return {
        "ts": ts,
        "shape": shape,
        "uid": uid,
        "polarity": _polarity(text),
        "prompter_name": None,
        "prompter_version": None,
        "renderer_key": _grp(footer, r"renderer:\s*([a-z0-9\-]+)") or None,
        "permalink": ts_to_permalink(channel, ts) if ts else "",
        "is_test": False,
        "test_reason": "",
    }


def parse_entry(msg: dict, channel: str) -> dict | None:
    text = (msg.get("text") or "").strip()
    shape = detect_shape(text)
    if shape is None:
        return None
    if shape in ("journal", "profile"):
        return parse_footer_entry(msg, channel, shape)
    return parse_conversation_entry(msg, channel, shape)


def uid_prefix(uid: str) -> str:
    """Short, non-identifying prefix that keeps `google:` ids distinct. Same
    derivation as `report-conversation-patterns/scripts/sample.py` (canonical);
    held in step by `tests/test_uid_prefix_rule.py`."""
    uid = uid or ""
    if ":" in uid:
        provider, _, rest = uid.partition(":")
        return f"{provider}:{rest[:8]}"
    return uid[:8]


def check_allowlist(allowlist: dict[str, set[str]]) -> dict:
    """Warn on allowlist rows that can never match a rating footer's `user:` id —
    an id of no known shape, or an email (the footer carries none). Same rule as
    the `fetch_ratings.py` family."""
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
            "footers carry only `user:<id>`",
            file=sys.stderr,
        )
    return {
        "ids": len(allowlist["ids"]),
        "provider_ids": len(allowlist["provider_ids"]),
        "unmatchable_ids": [uid_prefix(i) for i in bad_shape],
        "unmatchable_email_rows": n_email,
    }


def apply_test_filter(entries: list[dict], internal_ids: set[str] | None = None) -> None:
    """Tag entries in place with is_test + test_reason + high_volume.

    The only test criterion is an explicit internal-account match: a UID present in
    the `Team/Internal Accounts` allowlist passed via --internal-ids. Nothing is
    dropped on inferred grounds, and omitting the flag drops nothing.

    Volume is deliberately not a criterion — see the module header. Prolific UIDs get
    `high_volume=True` and are reported, not dropped.

    Unlike the `fetch_ratings.py` family this carries no Aura-API internal ladder
    (`role` / `isAIReviewer` / `@aurahealth.io` / `test` / creator sub): that join is
    per-distinct-UID, and a calendar month has hundreds, which is a multi-minute walk
    for a chart that only needs aggregate counts. The consequence is real and worth
    stating in the report — an internal account the allowlist doesn't name is counted
    as a user here, where the rating sweeps would exclude it.
    """
    internal_ids = internal_ids or set()
    uid_counts = Counter(e["uid"] for e in entries if e["uid"])
    for e in entries:
        reasons = []
        if e["uid"] and e["uid"] in internal_ids:
            reasons.append(f"allowlist:{uid_prefix(e['uid'])}")
        e["is_test"] = bool(reasons)
        e["test_reason"] = ",".join(reasons)
        e["high_volume"] = bool(
            e["uid"] and uid_counts[e["uid"]] >= HIGH_VOLUME_UID_THRESHOLD
        )


def aggregate(real: list[dict]) -> tuple[list[dict], dict]:
    """Build the per-shape × polarity matrix + overall totals from real entries."""
    by_shape: dict[str, Counter] = defaultdict(Counter)
    for e in real:
        if e["polarity"] in POLARITIES:
            by_shape[e["shape"]][e["polarity"]] += 1
    rows = []
    for shape in SHAPE_ORDER:
        if shape not in by_shape:
            continue
        c = by_shape[shape]
        rows.append({
            "shape": shape,
            "label": SHAPE_LABELS[shape],
            "positive": c["positive"],
            "neutral": c["neutral"],
            "negative": c["negative"],
            "total": c["positive"] + c["neutral"] + c["negative"],
        })
    # rows sorted by total desc for chart readability
    rows.sort(key=lambda r: -r["total"])
    totals = Counter()
    for r in rows:
        for p in POLARITIES:
            totals[p] += r[p]
    grand = sum(totals.values())
    # Ratings whose emoji didn't parse into a known polarity are real entries but
    # carry no charted bucket. Track them explicitly so the headline reconciles:
    # counts.real == overall.total + overall.unknown. Without this, an unparsed
    # rating would inflate counts.real while silently vanishing from overall.total.
    unknown = sum(1 for e in real if e["polarity"] not in POLARITIES)
    overall = {
        "positive": totals["positive"],
        "neutral": totals["neutral"],
        "negative": totals["negative"],
        "unknown": unknown,
        "total": grand,
        "pct": {p: (round(100 * totals[p] / grand, 1) if grand else 0.0) for p in POLARITIES},
    }
    return rows, overall


def aggregate_renderers(real: list[dict]) -> list[dict]:
    """Break profile ratings down by the rated renderer, most-rated first.

    A profile rating with no `renderer:` in its footer rated the whole profile,
    not one card — bucketed under `whole-profile` rather than dropped.
    """
    by_renderer: dict[str, Counter] = defaultdict(Counter)
    for e in real:
        if e["shape"] != "profile" or e["polarity"] not in POLARITIES:
            continue
        by_renderer[e.get("renderer_key") or "whole-profile"][e["polarity"]] += 1
    rows = [
        {
            "renderer": renderer,
            "positive": c["positive"],
            "neutral": c["neutral"],
            "negative": c["negative"],
            "total": sum(c[p] for p in POLARITIES),
        }
        for renderer, c in by_renderer.items()
    ]
    rows.sort(key=lambda r: -r["total"])
    return rows


def render_pngs(rows: list[dict], month_label: str, total: int, outdir: str) -> list[str]:
    """Render absolute + normalized horizontal stacked bar charts. Returns file paths."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(outdir, exist_ok=True)
    labels = [r["label"] for r in rows]
    y = range(len(rows))
    paths = []

    for normalize in (False, True):
        fig, ax = plt.subplots(figsize=(8, max(2.2, 0.7 * len(rows) + 1.4)))
        left = [0.0] * len(rows)
        for p in POLARITIES:
            if normalize:
                vals = [(100 * r[p] / r["total"] if r["total"] else 0.0) for r in rows]
            else:
                vals = [r[p] for r in rows]
            ax.barh(list(y), vals, left=left, color=POLARITY_COLORS[p],
                    label=p.capitalize(), height=0.62)
            left = [a + b for a, b in zip(left, vals)]
        ax.set_yticks(list(y))
        ax.set_yticklabels(labels)
        ax.invert_yaxis()
        kind = "normalized (share)" if normalize else "absolute counts"
        ax.set_title(f"Aurie ratings by type — {month_label}  ·  {total} ratings  ·  {kind}",
                     fontsize=12, loc="left", pad=12)
        ax.set_xlabel("share" if normalize else "ratings", fontsize=10)
        if normalize:
            ax.set_xlim(0, 100)
            ax.xaxis.set_major_formatter(lambda v, _: f"{int(v)}%")
        ax.legend(ncol=3, frameon=False, fontsize=9, loc="upper center",
                  bbox_to_anchor=(0.5, -0.22))
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        fig.tight_layout()
        suffix = "normalized" if normalize else "absolute"
        path = os.path.join(outdir, f"ratings_{month_label}_{suffix}.png")
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        paths.append(path)
    return paths


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(description="Aggregate one month of conversation ratings by type + polarity.")
    ap.add_argument("--channel", default=DEFAULT_CHANNEL)
    ap.add_argument("--month", default=None, help="YYYY-MM (default: last complete month)")
    ap.add_argument("--token", default=os.environ.get("SLACK_BOT_TOKEN"))
    ap.add_argument("--png", default=None, metavar="OUTDIR",
                    help="Render absolute + normalized PNG charts into OUTDIR.")
    ap.add_argument(
        "--internal-ids",
        default="",
        help=(
            "Comma-separated Firebase UIDs / provider ids from the `Team/Internal "
            "Accounts` allowlist. The only entries dropped as test. Omit and nothing "
            "is dropped. Prefer --internal-accounts-file."
        ),
    )
    ap.add_argument(
        "--internal-accounts-file",
        default=None,
        metavar="PAGE_MD",
        help=(
            "The `Team/Internal Accounts` page as markdown (the retrieve-knowledge "
            "cache file or a saved notion-fetch). Every id on it joins the allowlist."
        ),
    )
    args = ap.parse_args()

    if not args.token:
        raise SystemExit("SLACK_BOT_TOKEN not set (pass --token or export it).")

    oldest, latest, month_label = resolve_month(args.month)

    try:
        raw = fetch_messages(args.token, args.channel, oldest, latest)
    except SlackReadError as exc:
        raise SystemExit(str(exc))

    all_entries = [e for m in raw for e in [parse_entry(m, args.channel)] if e]

    # Warn-only: surface an id family UID_SHAPE_RE doesn't know. Masked so the
    # warning names the shape, never a real id.
    unrecognised = Counter(
        re.sub(r"[A-Za-z0-9]+", "X", e["uid"]) + f"|len{len(e['uid'])}"
        for e in all_entries
        if e["uid"] and not UID_SHAPE_RE.fullmatch(e["uid"])
    )
    for shape, count in unrecognised.most_common():
        print(
            f"warning: {count} rating(s) carry a user id of unrecognised shape {shape} — "
            "parsed as-is; add the family to UID_SHAPE_RE if it is legitimate",
            file=sys.stderr,
        )
    try:
        allowlist = internal_accounts.resolve(args.internal_ids, args.internal_accounts_file)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"--internal-accounts-file: {exc}")
    allowlist_report = check_allowlist(allowlist)
    apply_test_filter(all_entries, allowlist["ids"])
    real = [e for e in all_entries if not e["is_test"]]
    tests = [e for e in all_entries if e["is_test"]]

    rows, overall = aggregate(real)

    png_paths: list[str] = []
    if args.png:
        png_paths = render_pngs(rows, month_label, overall["total"], args.png)

    out = {
        "channel": args.channel,
        "month": month_label,
        "counts": {
            "parsed": len(all_entries),
            "real": len(real),
            "test_dropped": len(tests),
        },
        "allowlist": allowlist_report,
        "test_reasons": dict(
            Counter(e["test_reason"] for e in tests if e["test_reason"]).most_common()
        ),
        # Report-only: prolific UIDs are surfaced, never dropped (ENG-1048/ENG-1050).
        "high_volume_uids": dict(
            Counter(e["uid"] for e in real if e.get("high_volume") and e["uid"]).most_common()
        ),
        "overall": overall,
        "by_type": rows,
        "profile_by_renderer": aggregate_renderers(real),
        "png_paths": png_paths,
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
