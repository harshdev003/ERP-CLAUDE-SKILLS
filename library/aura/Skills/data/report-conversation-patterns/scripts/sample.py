#!/usr/bin/env python3
"""Stratified sample from a fetch_ratings.py output JSON.

Selects up to N entries from the rated conversation pool, preserving:
  - Polarity proportions (positive : neutral : negative from the window)
  - Session-type diversity (conversation, qp, coaching, journal, chat)
  - One entry per user (dedup on a short, provider-aware uid prefix)
  - One entry per rated thread (dedup on source_id — both frames are per-rating,
    so a thread rated twice must not be read or quoted twice)
  - Preference for entries with a conversation_id (transcript fetchable)
  - Preference for entries with text signal (feedback, or journal_text on
    journal rows)

fetch_ratings.py emits the full `uid`; this script derives a short
`uid_prefix` for dedup (see `uid_prefix()` — provider-aware, so two
`google:` users don't collapse) and emits only that prefix (PII rule —
never the full UID). The full UID is dropped from the output.

Output is the sampled entries list as JSON, plus a summary block. The sample
never exceeds --n.

Usage:
    sample.py --input /tmp/rated_all.json --n 30 --output /tmp/sample.json
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict


def uid_prefix(uid: str) -> str:
    """Return a short, non-identifying prefix of `uid` that still distinguishes users.

    A bare `uid[:8]` assumes the first 8 characters are distinguishing. For
    provider-prefixed ids they are not: every `google:112...` yields `"google:1"`,
    so distinct users collapse onto one value — and here that value is the dedup
    key, so all but one of those users is dropped from the sample.

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

    Duplicated in `harvest-conversation-patterns/scripts/fetch_sweep_analyses.py`
    — both write `uid_prefix` into the same Signals table and skills are
    self-contained, so the two copies must stay in step.
    """
    uid = uid or ""
    if ":" in uid:
        provider, _, rest = uid.partition(":")
        return f"{provider}:{rest[:8]}"
    return uid[:8]


def _thread_key(e: dict) -> str:
    """Return the id of the rated object, or "" when the entry names none.

    The two frames spell it differently and neither spelling covers every shape, so
    take the first non-empty: `query_patterns.py` carries `source_id` for all six
    shapes; `fetch_ratings.py` splits it across `conversation_id` (conversation
    shapes), `journal_id` (journal) and `rating_id` (profile — its own field, the
    `rendererRating` row id, unrelated to this table's `Rating ID` column).

    Reading only `source_id` would silently make the per-thread collapse a no-op on
    the whole Slack fallback path — the frame used precisely when the table has no
    rows, so the gap would show up on exactly the runs nobody is watching.
    """
    for k in ("source_id", "conversation_id", "journal_id", "rating_id"):
        v = (e.get(k) or "").strip() if isinstance(e.get(k), str) else e.get(k)
        if v:
            return str(v)
    return ""


def _text_signal(e: dict) -> str:
    """Text carried by the entry — feedback, or journal_text on journal rows."""
    return (e.get("feedback") or "").strip() or (e.get("journal_text") or "").strip()


def _quality_key(e: dict) -> tuple[int, int]:
    """Rank an entry: prefer a fetchable transcript, then any text signal."""
    return (int(bool(e.get("conversation_id"))), int(bool(_text_signal(e))))


def _allocate(pool_sizes: dict[str, int], n_target: int) -> dict[str, int]:
    """Proportionally allocate n_target slots across polarities (largest-remainder).

    Guarantees sum(alloc) == n_target (given total pool >= n_target) and
    alloc[p] <= pool_sizes[p] for every polarity, so the sample never exceeds
    n_target and never over-draws a bucket.
    """
    total = sum(pool_sizes.values())
    if total == 0:
        return {p: 0 for p in pool_sizes}

    raw = {p: n_target * size / total for p, size in pool_sizes.items()}
    alloc = {p: min(pool_sizes[p], int(raw[p])) for p in pool_sizes}  # floor, clamped

    # Distribute the remainder by largest fractional part, respecting pool capacity.
    order = sorted(pool_sizes, key=lambda p: raw[p] - int(raw[p]), reverse=True)
    remaining = n_target - sum(alloc.values())
    i = 0
    guard = 0
    max_iter = len(order) * (n_target + 1) + 1
    while remaining > 0 and any(alloc[p] < pool_sizes[p] for p in pool_sizes) and guard < max_iter:
        p = order[i % len(order)]
        if alloc[p] < pool_sizes[p]:
            alloc[p] += 1
            remaining -= 1
        i += 1
        guard += 1
    return alloc


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Path to fetch_ratings.py JSON output")
    parser.add_argument("--n", type=int, default=30, help="Target sample size (default 30)")
    parser.add_argument("--output", help="Write JSON to this file instead of stdout")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")
    args = parser.parse_args()

    with open(args.input, encoding="utf-8") as f:
        data = json.load(f)

    entries = data.get("entries", [])
    if not entries:
        print("No entries in input — nothing to sample.", file=sys.stderr)
        sys.exit(1)

    rng = random.Random(args.seed)

    # ── Step 1: derive uid_prefix + dedup by user (keep best row per user) ──────
    # fetch_ratings.py sorts newest-first. Rather than blindly keep the newest
    # row per user, keep the highest-quality one (transcript, then text signal) —
    # otherwise a user's latest journal/profile rating could evict an earlier
    # conversation rating that carries a fetchable transcript. Newest wins ties.
    # Rows with a missing/empty uid can't be attributed to a distinct user, so
    # they all collapse under one sentinel key — at most one anonymous entry
    # enters, preserving the one-entry-per-user guarantee.
    best_by_key: dict[str, dict] = {}
    for e in entries:
        prefix = uid_prefix(e.get("uid") or "")
        key = prefix or "__no_uid__"
        # Emit only the prefix downstream — never the full UID (PII rule).
        clean = {k: v for k, v in e.items() if k != "uid"}
        clean["uid_prefix"] = prefix
        prev = best_by_key.get(key)
        if prev is None or _quality_key(clean) > _quality_key(prev):
            best_by_key[key] = clean
    unique = list(best_by_key.values())

    # ── Step 1b: one entry per rated thread ─────────────────────────────────────
    # Both frames are per-RATING, so several entries can share a `source_id`. Reading the same transcript twice would spend the sample budget
    # on one thread and let it speak twice in the narrative, so collapse to the
    # best entry per thread. This is a display/sampling decision only — the volume
    # and polarity counts stay per-rating, because the ratings are all real.
    #
    # Deliberately not folded into Step 1: the per-user dedup already makes a repeat
    # thread nearly impossible (it takes two users rating one thread), so this is a
    # guarantee rather than the mechanism. Keeping it separate means neither pass
    # silently depends on the other's key still being the stronger one.
    # Entries naming no thread (see `_thread_key`) are never collapsed together.
    best_by_thread: dict[str, dict] = {}
    passthrough: list[dict] = []
    for e in unique:
        src = _thread_key(e)
        if not src:
            passthrough.append(e)
            continue
        prev = best_by_thread.get(src)
        if prev is None or _quality_key(e) > _quality_key(prev):
            best_by_thread[src] = e
    thread_collapsed = len(unique) - len(best_by_thread) - len(passthrough)
    unique = list(best_by_thread.values()) + passthrough

    total_unique = len(unique)
    n_target = min(args.n, total_unique)

    # ── Step 2: proportional allocation per polarity (never exceeds n_target) ────
    by_polarity: dict[str, list[dict]] = defaultdict(list)
    for e in unique:
        by_polarity[e.get("polarity", "unknown")].append(e)

    pool_sizes = {p: len(rows) for p, rows in by_polarity.items()}
    polarity_targets = _allocate(pool_sizes, n_target)

    # ── Step 3: within each polarity, rank by quality then pick with shape spread ─
    sample: list[dict] = []
    shape_counts: Counter = Counter()

    for polarity, target in polarity_targets.items():
        pool = sorted(by_polarity.get(polarity, []), key=_quality_key, reverse=True)
        # Prefer session-type diversity: cap how many of one shape we take first.
        chosen: list[dict] = []
        per_shape_cap = max(1, target // 2 + 1)
        for e in pool:
            if len(chosen) >= target:
                break
            shape = e.get("shape", "unknown")
            if shape_counts[shape] < per_shape_cap:
                chosen.append(e)
                shape_counts[shape] += 1

        # If the diversity cap left us short, fill the remainder from what's left.
        if len(chosen) < target:
            chosen_ids = {id(e) for e in chosen}
            remaining_pool = [e for e in pool if id(e) not in chosen_ids]
            rng.shuffle(remaining_pool)
            chosen.extend(remaining_pool[: target - len(chosen)])

        sample.extend(chosen)

    # Shuffle final sample so polarities are interleaved.
    rng.shuffle(sample)

    # ── Step 4: summary ─────────────────────────────────────────────────────────
    sampled_polarities = Counter(e.get("polarity", "unknown") for e in sample)
    sampled_shapes = Counter(e.get("shape", "unknown") for e in sample)
    with_conv_id = sum(1 for e in sample if e.get("conversation_id"))
    with_text = sum(1 for e in sample if _text_signal(e))

    # Window-level totals, computed here rather than read from either frame's `counts`.
    # The two frames share only the `entries` key: the table frame emits
    # total/positive/neutral/negative/by_type/by_theme, the Slack fallback emits
    # parsed/real/test_dropped/infra_skipped/by_shape. Nothing named the same means
    # anything in both. So a report headline sourced from a `counts.*` path silently
    # has no value on one of the two paths — and it is the fallback path, taken exactly
    # when the table is empty. Derived from the entries this script was handed, these
    # numbers are identical in shape on both frames.
    # Polarity is reported **per shape and never pooled**. `Chat` and `Journal` are
    # rated on a two-option widget with no neutral; the rest are three-option. A single
    # blended polarity rate over mixed shapes measures which surfaces can express a
    # neutral at all, not quality — see the `Rating` is-not-comparable section in the
    # Signals/Conversation Patterns schema, which is canonical for that split. Emitting
    # only the per-shape breakdown means there is no pooled number available to quote
    # by accident; the report groups these into the two scale families itself.
    window_polarity: dict[str, Counter] = defaultdict(Counter)
    for e in entries:
        window_polarity[e.get("shape", "unknown")][e.get("polarity", "unknown")] += 1
    window_threads = {k for k in (_thread_key(e) for e in entries) if k}

    summary = {
        # Ratings in the window, before any dedup — the volume number.
        "ratings_in_window": len(entries),
        # Threads those ratings landed on. Lower than `ratings_in_window` by however
        # many threads were rated more than once; a headline that says "conversations"
        # means this one.
        "distinct_threads_in_window": len(window_threads),
        # {shape: {polarity: n}} — per shape, deliberately never pooled (see above).
        "by_polarity_by_shape_in_window": {s: dict(c) for s, c in window_polarity.items()},
        "total_in_pool": total_unique,
        # Extra ratings on an already-represented thread, dropped from the sample only.
        "thread_collapsed": thread_collapsed,
        "sampled": len(sample),
        "by_polarity": dict(sampled_polarities),
        "by_shape": dict(sampled_shapes),
        "with_conversation_id": with_conv_id,
        "with_text": with_text,
        "window": data.get("window", ""),
    }

    output = {"summary": summary, "entries": sample}

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        print(f"Sampled {len(sample)} entries → {args.output}", file=sys.stderr)
        print(f"  Window: {len(entries)} ratings on {len(window_threads)} threads"
              f" ({thread_collapsed} collapsed)", file=sys.stderr)
        print(f"  Polarity: {dict(sampled_polarities)}", file=sys.stderr)
        print(f"  Shapes: {dict(sampled_shapes)}", file=sys.stderr)
        print(f"  With transcript: {with_conv_id} | With text: {with_text}", file=sys.stderr)
    else:
        print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
