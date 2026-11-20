"""Backfill the engaged / active / activity_source columns on an existing
per-user daily-cost store.

For each date already in the store, fetches that day's engagement cohorts from
Mixpanel (engagement.day_engagement) and stamps every row:

  engaged          1 if the user sent a conversation message that day
  active           1 if the user did any user-initiated AI action that day
  activity_source  engaged / active / insights-batch / ambient / usage / reached
                   (reconcile for the per-day Other row)

The activity_source reason tag for reached-but-inactive users needs the user's
dominant project (top_project). That comes from the OpenAI per-user walk JSONs
(cpu_<date>.json) if present in --walk-dir; for dates whose walk JSON is gone
(beyond OpenAI's ~30d stored-completions retention) reached-inactive users are
tagged the generic "reached" — engaged/active are still exact from Mixpanel.

Idempotent: re-running recomputes from the same sources. Rewrites the CSV in
place (via a temp file + atomic replace). Missing Mixpanel creds → the run
aborts rather than blanking existing data.

Usage:
    backfill_engagement.py --csv per_user_daily_cost.csv [--walk-dir /tmp]
                           [--only 2026-07-01,2026-07-02] [--dry-run]
"""
import argparse
import csv
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engagement  # noqa: E402
import paths as store_paths  # noqa: E402  (BQ-primary CSV cache path resolution)

COLS = ["date", "user_id", "llm_cost", "voice_11labs_agent", "voice_11labs_intelligence",
        "voice_11labs_livekit",
        "voice_cartesia", "voice_deepgram", "voice_livekit", "voice_cost",
        "total_cost", "subscription_state", "engaged", "active", "activity_source"]
NEW = ("engaged", "active", "activity_source")


def top_proj_for(day, walk_dir):
    """Return {uid: top_project} from cpu_<day>.json in walk_dir, or {} if absent."""
    path = os.path.join(walk_dir, f"cpu_{day}.json")
    if not os.path.exists(path):
        return {}
    try:
        j = json.load(open(path))
    except Exception:  # noqa: BLE001
        return {}
    return {u["uid"]: u.get("top_project", "") for u in j.get("users_all", [])}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Backfill engaged/active/activity_source on the per-user cost store.")
    ap.add_argument("--csv", default=None,
                    help="per-user store (default: $AURIE_COST_CSV or the ~/.cache/aurie-cost cache)")
    ap.add_argument("--walk-dir", default="/tmp", help="dir holding cpu_<date>.json walk JSONs (for the reason tag)")
    ap.add_argument("--only", help="comma-separated YYYY-MM-DD list to limit the backfill")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    # BigQuery is the source of truth; this rewrites the local copy in place.
    args.csv = store_paths.resolve("per_user", args.csv)
    if not os.path.exists(args.csv):
        sys.exit(f"store not found: {args.csv} — run bq_pull.py to rebuild the local copy from BigQuery")
    # Migrate to the current schema FIRST. This script rewrites the whole file with
    # the current COLS header, so on an older file it would otherwise write the new
    # columns blank and leave ENG-982's mis-booked voice_11labs_livekit in place —
    # permanently, since store.py's migration only fires on a recognised old header.
    import store as _store_mod
    try:
        _store_mod._migrate_old_csv(args.csv)
    except _store_mod.SchemaError as e:
        sys.exit(f"cannot backfill: {e}")
    if not os.environ.get("MIXPANEL_SERVICE_ACCOUNT_USERNAME"):
        sys.exit("MISSING MIXPANEL_SERVICE_ACCOUNT_USERNAME/_SECRET — cannot backfill engagement")

    with open(args.csv, newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = list(csv.DictReader(f, fieldnames=header))
    known = set(header)
    if not {"date", "user_id"} <= known:
        sys.exit(f"unexpected header in {args.csv}: {header}")

    only = set(args.only.split(",")) if args.only else None
    dates = sorted({r["date"] for r in rows if not only or r["date"] in only})
    if not dates:
        sys.exit("no matching dates in store")
    print(f"backfilling {len(dates)} date(s) in {args.csv}"
          + (f" (limited to {sorted(only)})" if only else ""))

    # ONE windowed JQL for the whole range — avoids the per-day 429 storm.
    win, note = engagement.window_engagement(dates[0], dates[-1])
    if note:
        sys.exit(f"engagement fetch failed: {note} — aborting so existing data isn't blanked")

    # date -> (engaged set, active set, top_proj map)
    cohorts = {}
    for d in dates:
        eng, act = win.get(d, (set(), set()))
        tp = top_proj_for(d, args.walk_dir)
        cohorts[d] = (eng, act, tp)
        tag = "" if tp else " [no walk JSON — reason tag = reached for inactive]"
        print(f"  {d}: engaged {len(eng)} / active {len(act)}{tag}")

    changed = 0
    for r in rows:
        if only and r["date"] not in only:
            continue
        eng, act, tp = cohorts.get(r["date"], (set(), set(), {}))
        uid = r["user_id"]
        if uid == "Other":
            newvals = {"engaged": "0", "active": "0", "activity_source": "reconcile"}
        else:
            newvals = {
                "engaged": "1" if uid in eng else "0",
                "active": "1" if uid in act else "0",
                "activity_source": engagement.activity_source(uid, eng, act, tp.get(uid, "")),
            }
        if any(r.get(k, "") != v for k, v in newvals.items()):
            changed += 1
        r.update(newvals)

    print(f"rows updated: {changed} / {len(rows)}")
    if args.dry_run:
        print("dry-run — no write.")
        return

    # atomic rewrite with the full 14-col schema
    d = os.path.dirname(os.path.abspath(args.csv))
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".csv")
    with os.fdopen(fd, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in COLS})
    os.replace(tmp, args.csv)
    print(f"wrote {args.csv} with {', '.join(NEW)} columns.")


if __name__ == "__main__":
    main()
