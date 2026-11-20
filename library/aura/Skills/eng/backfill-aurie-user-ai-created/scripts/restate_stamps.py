#!/usr/bin/env python3
"""Restate stored `ai_created_at` values that are batch stamps, not real join dates.

PROD-6110, the data half of PROD-6109. That card stopped the skill CREATING wrong rows;
this pass corrects the ones already stored.

Postgres stamps batches of users with an exact-noon `aiCreatedAt` that is not a real join
date. Rows written before the shape detector landed still carry one, and a stamped value
puts that user in the wrong cohort in every `chart-aurie-*` cohort / tenure / retention /
payback chart, with nothing raised. Scope at the time of writing: 631 users, $703.37 of
prod cost, 66% of them in the wrong cohort week.

HOW A ROW IS CORRECTED
----------------------
Through the same ladder `backfill.py` now uses, minus the Postgres rung that produced the
bad value: `onboarding.shown.consumerAIOnboarding` off `GET /users/:id`, then the Mixpanel
first-AI-login proxy for whoever has no onboarding timestamp. Nothing is invented — a user
this pass cannot resolve is left alone and reported, never guessed at.

APPEND, NEVER UPDATE
--------------------
The dimension is append-only and `ai_created_at` is documented immutable, so a correction
is a NEW row with a later `fetched_at`. Every one of the 8 readers resolves the dimension
with `ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY fetched_at DESC) = 1` (audited on
PROD-6110), so the appended row wins and nothing double-counts. `tests/test_ai_created_dedup.py`
pins that property so a NEW reader cannot silently omit it.

IDEMPOTENCE
-----------
Re-running is safe and is a no-op: the pass selects rows whose CURRENT winning value is a
batch stamp, and a corrected row is by construction not one. It therefore cannot walk a
value further on each run, and it cannot "restate" a user twice. `--confirm` is still
required to write.

DRY RUN BY DEFAULT. Prints what would change and writes nothing until `--confirm`.

Env: AURA_API_TOKEN, GCP_KEY. Run with the framework python that has `requests`.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backfill  # noqa: E402  (sibling; the ladder and the stamp detector live there)
import bq  # noqa: E402
from bq import BQError, BQUnavailable  # noqa: E402


def stamped_rows(table: str, stamps: set) -> dict:
    """{user_id: current ai_created_at} for users whose WINNING row is a batch stamp.

    Selects against `stamps` — the set `backfill.known_batch_stamps` derives by clustering
    over the table's **whole history** — rather than re-clustering the winning rows here.

    That distinction is load-bearing under a partial write. The table is append-only, so a
    corrected user's original stamped row stays in history and the historical cluster never
    shrinks; the set of *winning* rows carrying a given stamp does. Re-clustering the
    winners would therefore drop a stamp below the threshold once most of its users were
    corrected — after `--limit --confirm`, or simply because some users are unresolvable —
    and the stragglers would silently stop being recognised as stamped, by this pass and by
    the post-run verification query alike. They would keep a fabricated join date with
    nothing left to report it.

    Reads through the dedup, so an already-corrected user is not re-selected.
    """
    if not stamps:
        return {}
    lit = ",".join("TIMESTAMP '" + s.replace("T", " ") + "'" for s in sorted(stamps))
    rows = bq.query(
        "SELECT user_id, ai_created_at FROM ("
        "  SELECT user_id, ai_created_at,"
        "         ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY fetched_at DESC) AS rn"
        f"  FROM `{table}`"
        f") WHERE rn = 1 AND ai_created_at IN ({lit})")
    out = {r["user_id"]: r["ai_created_at"] for r in rows}
    # The unconditional sentinel is never stored, so it cannot appear above; assert the
    # invariant rather than trusting it, since a violation would mean the skill regressed.
    assert not any(backfill._norm_ts(v).startswith(backfill.SENTINEL_PREFIX)
                   for v in out.values()), "sentinel found stored — backfill.py regressed"
    return out


def resolve(uids: list, workers: int) -> tuple:
    """(resolved{uid: iso}, unresolved[uid], transient[uid]).

    Mirrors `backfill.fetch_aura`'s ladder, including the part that matters most here:
    **a transient failure must never fall through to a weaker rung.** A 5xx, a 429 or a
    dropped connection is not evidence that the user has no onboarding timestamp — and
    because a correction is written once and the corrected row is by construction no
    longer a batch stamp, this pass would never revisit it. A blip during `--confirm`
    would therefore permanently cache a Mixpanel proxy date over the `consumerAIOnboarding`
    value that was there all along. Only a genuine 404 routes onward; anything else leaves
    the user untouched for a later run.
    """
    backfill._session(workers)

    def one(u):
        st, d = backfill._get_user(backfill.PROD_BASE, u)
        if st == 200 and d:
            return u, backfill._onboarding_ai_ts(d), False
        if st != 404:
            return u, None, True            # transient — do NOT try weaker rungs
        st, d = backfill._get_user(backfill.DEV_BASE, u)
        if st == 200 and d:
            return u, backfill._onboarding_ai_ts(d), False
        if st == 404:
            return u, None, False           # genuinely absent; the proxy may still answer
        return u, None, True                # transient on dev

    resolved, transient = {}, []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for u, ob, is_transient in ex.map(one, uids):
            if is_transient:
                transient.append(u)
            elif ob:
                resolved[u] = ob
    rest = [u for u in uids if u not in resolved and u not in transient]
    if rest:
        fb, answered = backfill.fallback_first_ai(rest)
        for u, t in fb.items():
            resolved[u] = t
        # `answered` is the subset the proxy actually got a verdict on. A UID missing
        # from it was never asked — its chunk failed, or table discovery did — which is
        # a transient failure, not "this user has no AI event". Collapsing the two would
        # report a BigQuery blip as a stable, untouched user, and a one-shot --confirm
        # would finish while their winning row is still a batch stamp. Same asked-vs-
        # answered distinction `backfill.fallback_first_ai` exists to preserve.
        transient += [u for u in rest if u not in answered and u not in resolved]
    unresolved = [u for u in uids if u not in resolved and u not in transient]
    return resolved, unresolved, transient


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Restate batch-stamped ai_created_at values.")
    ap.add_argument("--confirm", action="store_true", help="actually write (default: dry run)")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--limit", type=int, help="cap rows processed (test runs)")
    ap.add_argument("--project", default=bq.PROJECT)
    ap.add_argument("--dataset", default=bq.DATASET)
    args = ap.parse_args()

    if not os.environ.get("AURA_API_TOKEN"):
        print("AURA_API_TOKEN is not set — required to read the onboarding timestamp.",
              file=sys.stderr)
        return 1

    table = f"{args.project}.{args.dataset}.aurie_user_ai_created"
    backfill.TABLE = table
    try:
        stamps = backfill.known_batch_stamps()
        # `known_batch_stamps` DEGRADES on a BQError: it warns and returns the sentinel
        # alone. That is right for the normal backfill, which merely resolves fewer users
        # — but here it is indistinguishable from success. The sentinel is never stored,
        # so selecting against it finds nothing, and the pass would print "nothing to
        # restate" and exit 0 while leaving every wrong row in place. Under `--confirm`
        # that is a correction silently skipped, and the operator has no way to tell.
        #
        # The table is append-only, so a stamp's historical cluster survives restatement:
        # once this store has ever held one, discovery returning the bare sentinel means
        # the query failed, not that the work is done.
        if stamps == {backfill.SENTINEL_PREFIX} and bq.table_exists(table):
            rows = bq.query(f"SELECT COUNT(*) AS n FROM `{table}`")
            if rows and rows[0]["n"]:
                print("batch-stamp discovery returned nothing on a non-empty table — "
                      "treating as a failed query, not a clean run. Re-run when BigQuery "
                      "is healthy.", file=sys.stderr)
                return 1
        cur = stamped_rows(table, stamps)
    except BQUnavailable as e:
        print(f"BigQuery unavailable: {e}", file=sys.stderr); return 1
    except BQError as e:
        print(f"BigQuery error: {e}", file=sys.stderr); return 1

    print(f"batch stamps known: {len(stamps)}   stamped rows stored: {len(cur)}")
    if not cur:
        print("nothing to restate — no stored row resolves to a batch stamp.")
        return 0
    uids = list(cur)[: args.limit] if args.limit else list(cur)

    resolved, unresolved, transient = resolve(uids, max(1, args.workers))
    print(f"  resolved: {len(resolved)}   unresolved (left untouched): {len(unresolved)}"
          f"   transient API failures (retry next run): {len(transient)}")

    moved = whole_day = 0
    for u, iso in resolved.items():
        new = backfill._norm_ts(iso)
        old = backfill._norm_ts(cur[u])
        if new != old:
            moved += 1
            if new[:10] != old[:10]:
                whole_day += 1
    print(f"  values that change: {moved}   of which by a whole day: {whole_day}")

    if not args.confirm:
        print("\nDRY RUN — nothing written. Re-run with --confirm to append corrections.")
        for u in list(resolved)[:5]:
            print(f"    {u[:12]}…  {backfill._norm_ts(cur[u])}  ->  {backfill._norm_ts(resolved[u])}")
        return 0

    now = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()
    out = [{"user_id": u, "ai_created_at": iso, "created_at": None,
            "source": "ai_onboarding_restated", "fetched_at": now}
           for u, iso in resolved.items()]
    written = backfill.append_rows(out)
    print(f"\nappended {written} corrected rows (source=ai_onboarding_restated).")
    print("Readers resolve the dimension by latest fetched_at, so these now win.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
