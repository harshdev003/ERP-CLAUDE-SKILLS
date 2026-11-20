#!/usr/bin/env python3
"""Per-day LiveKit-path voice cost readout from the stored BigQuery daily cost store.

Reads the reconciled daily store maintained by eng:store-aurie-user-daily-cost
(`aura-main.aurie_cost`, the source of truth) and surfaces ONLY the LiveKit voice
path, decomposed by role:

  transport  = voice_livekit          (LiveKit Cloud transport)
  STT        = voice_deepgram         (Deepgram speech-to-text)
  TTS        = voice_cartesia         (Cartesia)  +  voice_11labs_livekit (ElevenLabs-as-TTS)
  LiveKit-path total = the four summed

plus per-day session-minutes (derived from the unit-cost store) and per-day distinct
users on the LiveKit path, with a subscription-state split.

Two tables, both in `aura-main.aurie_cost`:
  - aurie_per_user_daily_cost  — per-user, per-day cost rows. The BASE table (not the
    `_prod` view), so each day's rows sum EXACTLY to the authoritative daily vendor totals:
    the single reconciling `Other` row carries whatever a vendor could not attribute to a
    user. In practice the three minute-allocated vendors (transport / Deepgram / Cartesia)
    land fully on real users and `Other` is $0 for them, but 11Labs-TTS can leave a
    remainder there (e.g. 2026-07-20: $0.43 on `Other`, no attributed users). So the $
    columns SUM over every row (incl. `Other`); the distinct-user / subscription counts
    EXCLUDE `Other` (it is not a real user) — which is why a day can show cost with 0
    users. Same base-table rationale as eng:chart-aurie-daily-cost.
  - aurie_daily_unit_costs      — one row per day; carries cost_per_voice_min_livekit, which
    is inverted to recover LiveKit session-minutes. NOTE its basis is transport + Deepgram
    + Cartesia only — it EXCLUDES voice_11labs_livekit — so the minutes numerator must
    match that, not the 4-vendor LiveKit-path total.

Cost basis (see eng:store-aurie-user-daily-cost): the LiveKit-path vendor $ are DERIVED
from PRICING-SHEET ESTIMATE rates for Cartesia / Deepgram / LiveKit-transport; only the
ElevenLabs-TTS column is an authoritative billed figure (the ElevenLabs day total split by
product_type). Deepgram bills to the prod project 7e42b984-… ('Aura Health') and is
LiveKit-path-only, so $0 means no LiveKit traffic that day.

Aggregate-only: per-day sums and state counts, never a Firebase UID (the base store is
UID-bearing — see Compliance/Privacy).

Reads BigQuery via bq.py (GCP_KEY service account) + google-cloud-bigquery. No BigQuery MCP
exists, so this is API-first; a missing key/library exits with a clear message.
"""

import argparse
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # for `from bq import ...`

BQ_PROJECT = os.environ.get("AURIE_BQ_PROJECT", "aura-main")
BQ_DATASET = os.environ.get("AURIE_BQ_DATASET", "aurie_cost")
OTHER_USER = "Other"  # the reconciling catch-all row appended per day by the store
STATES = ("paid", "trial", "free", "grace")


def _skill_version():
    """This skill's metadata.version (reads the sibling SKILL.md) for output provenance."""
    import re
    try:
        text = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "SKILL.md"),
                    encoding="utf-8").read()
    except OSError:
        return None
    fm = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    fm = fm.group(1) if fm else text
    block = re.search(r"(?m)^metadata:[ \t]*\n((?:[ \t]+\S.*\n?)+)", fm)
    if not block:
        return None
    v = re.search(r'''(?m)^[ \t]+version:[ \t]*["\']?([^"\'\s]+)["\']?[ \t]*$''', block.group(1))
    return v.group(1) if v else None


def _bounds(project, per_user_table):
    """Return (min_date, max_date) present in the per-user store as 'YYYY-MM-DD' strings."""
    from bq import query
    rows = query(f"SELECT MIN(date) AS mn, MAX(date) AS mx FROM `{per_user_table}`", project)
    r = rows[0] if rows else {}
    mn, mx = r.get("mn"), r.get("mx")
    if not mx:
        raise SystemExit(f"error: no rows in {per_user_table} — "
                         "populate it with eng:store-aurie-user-daily-cost.")
    return mn.isoformat(), mx.isoformat()


def _resolve_window(args, mn, mx):
    """Resolve the CLI window flags to concrete inclusive (from, to) date strings."""
    if args.date:
        return args.date, args.date
    if args.date_from or args.date_to:
        return (args.date_from or mn), (args.date_to or mx)
    if args.last is not None:
        if args.last < 1:
            raise SystemExit("error: --last must be >= 1")
        end = dt.date.fromisoformat(mx)
        start = end - dt.timedelta(days=args.last - 1)
        return max(start.isoformat(), mn), mx
    return mx, mx  # default: the latest stored day


def _fetch(project, per_user_table, unit_table, lo, hi):
    """Return per-day LiveKit-path rows over [lo, hi], keyed by date string."""
    from bq import query, BQError
    params = {"lo": lo, "hi": hi}

    # $ SUM over ALL rows (incl. `Other` remainder → reconciles to the day's vendor totals);
    # user/state counts EXCLUDE `Other`. COALESCE guards days that predate a vendor column.
    per_user_sql = f"""
    SELECT
      CAST(date AS STRING) AS date,
      -- PROD-5998: LiveKit's own cost is the subscription accrual plus any measured
      -- overage. voice_livekit alone is overage-only and reads $0 inside the
      -- allotment, so a transport column sourced from it would show $0 for a month
      -- that cost $500.
      ROUND(SUM(COALESCE(voice_livekit, 0) + COALESCE(voice_livekit_platform, 0)), 2) AS transport_usd,
      ROUND(SUM(COALESCE(voice_deepgram, 0)), 2)       AS stt_usd,
      ROUND(SUM(COALESCE(voice_cartesia, 0)), 2)       AS tts_cartesia_usd,
      ROUND(SUM(COALESCE(voice_11labs_livekit, 0)), 2) AS tts_11labs_usd,
      ROUND(SUM(COALESCE(livekit_minutes, 0)), 1)      AS livekit_minutes,
      ROUND(SUM(COALESCE(voice_livekit, 0) + COALESCE(voice_livekit_platform, 0)
                + COALESCE(voice_deepgram, 0)
              + COALESCE(voice_cartesia, 0) + COALESCE(voice_11labs_livekit, 0)), 2) AS livekit_path_usd,
      COUNTIF(user_id != '{OTHER_USER}' AND lk_total > 0)                             AS lk_users,
      COUNTIF(user_id != '{OTHER_USER}' AND lk_total > 0 AND subscription_state = 'paid')  AS lk_paid,
      COUNTIF(user_id != '{OTHER_USER}' AND lk_total > 0 AND subscription_state = 'trial') AS lk_trial,
      COUNTIF(user_id != '{OTHER_USER}' AND lk_total > 0 AND subscription_state = 'free')  AS lk_free,
      COUNTIF(user_id != '{OTHER_USER}' AND lk_total > 0 AND subscription_state = 'grace') AS lk_grace
    FROM (
      -- "was this user on the LiveKit path" — minutes are the direct signal since
      -- PROD-5998, with the dollar columns kept as the fallback for days stored
      -- before that card (livekit_minutes 0) and not yet restated.
      SELECT *, (COALESCE(livekit_minutes, 0) + COALESCE(voice_livekit, 0)
               + COALESCE(voice_livekit_platform, 0) + COALESCE(voice_deepgram, 0)
               + COALESCE(voice_cartesia, 0) + COALESCE(voice_11labs_livekit, 0)) AS lk_total
      FROM `{per_user_table}`
      WHERE date BETWEEN DATE(@lo) AND DATE(@hi)
    )
    GROUP BY date
    ORDER BY date
    """
    days = {}
    for r in query(per_user_sql, project, params=params):
        d = dict(r)
        days[d["date"]] = d

    # Unit-cost store: recover LiveKit session-minutes from the stored $/voice-min.
    unit = {}
    try:
        unit_sql = f"""
        SELECT CAST(date AS STRING) AS date, cost_per_voice_min_livekit
        FROM `{unit_table}`
        WHERE date BETWEEN DATE(@lo) AND DATE(@hi)
        """
        for r in query(unit_sql, project, params=params):
            unit[r["date"]] = dict(r)
    except BQError:
        unit = {}  # unit store optional — minutes degrade to null

    for date, d in days.items():
        u = unit.get(date)
        rate = (u or {}).get("cost_per_voice_min_livekit")
        # PROD-5998: minutes are STORED now. The unit-rate inversion below is a fallback
        # for days that predate the column, and it is no longer even self-consistent on a
        # restated day — transport_usd now carries the subscription accrual, while
        # cost_per_voice_min_livekit was derived from the old minutes x $0.01 basis, so
        # dividing one by the other inflates minutes by roughly the factor this change
        # exists to correct.
        stored_min = float(d.get("livekit_minutes") or 0.0)
        # Recover session-minutes by inverting the store's own rate — so the numerator here
        # MUST match the numerator the store divided by lk_minutes. That rate is
        # (cartesia + deepgram + livekit) / lk_minutes: it EXCLUDES voice_11labs_livekit.
        # Dividing the 4-vendor livekit_path_usd by it would overstate minutes ~2x.
        basis = round((d.get("transport_usd") or 0)
                      + (d.get("stt_usd") or 0)
                      + (d.get("tts_cartesia_usd") or 0), 2)
        d["lk_rate_basis_usd"] = basis
        # ≈ because the stored rate is rounded to 4 dp. Cross-checks to within a minute
        # against transport_usd / the $0.01-per-min transport rate. Null when no rate.
        d["lk_minutes"] = (round(stored_min, 1) if stored_min > 0
                           else (round(basis / rate, 1) if rate else None))
        d["lk_minutes_source"] = ("stored" if stored_min > 0
                                  else ("unit_rate_fallback" if rate else None))
        # The store's own rate, kept for provenance: 3-vendor basis, EXCLUDES 11Labs-TTS.
        d["stored_cost_per_voice_min_livekit"] = round(rate, 4) if rate else None
        # What the table shows: ALL-IN $/min over the full 4-vendor LiveKit-path total,
        # so the column is consistent with the LiveKit-path $ beside it and with the
        # totals row. Always >= the stored rate.
        d["cost_per_voice_min_all_in"] = (
            round((d.get("livekit_path_usd") or 0) / d["lk_minutes"], 4)
            if d["lk_minutes"] else None
        )
    return days


def _fnum(x):
    return f"{x:,.2f}" if x is not None else "—"


def _render_markdown(rows, summary):
    lines = []
    hdr = ("| Date | Transport $ | Deepgram (STT) $ | Cartesia (TTS) $ | 11Labs (TTS) $ "
           "| LiveKit-path $ | Session min | LK users | $/voice-min |")
    sep = "|---|---:|---:|---:|---:|---:|---:|---:|---:|"
    lines.append(hdr)
    lines.append(sep)
    for r in rows:
        mins = f"≈{r['lk_minutes']:,.0f}" if r["lk_minutes"] is not None else "—"
        cpm = (f"≈{r['cost_per_voice_min_all_in']:.4f}"
               if r["cost_per_voice_min_all_in"] is not None else "—")
        lines.append(
            f"| {r['date']} | {_fnum(r['transport_usd'])} | {_fnum(r['stt_usd'])} "
            f"| {_fnum(r['tts_cartesia_usd'])} | {_fnum(r['tts_11labs_usd'])} "
            f"| {_fnum(r['livekit_path_usd'])} | {mins} | {r['lk_users']:,} | {cpm} |"
        )
    if len(rows) > 1:  # a totals row over a single day just repeats it
        t = summary["totals"]
        tot_min = f"≈{t['lk_minutes']:,.0f}" if t["lk_minutes"] is not None else "—"
        blended = summary["blended_cost_per_voice_min"]
        tot_cpm = f"**≈{blended:.4f}**" if blended is not None else "—"
        lines.append(
            f"| **Total** | **{_fnum(t['transport_usd'])}** | **{_fnum(t['stt_usd'])}** "
            f"| **{_fnum(t['tts_cartesia_usd'])}** | **{_fnum(t['tts_11labs_usd'])}** "
            f"| **{_fnum(t['livekit_path_usd'])}** | **{tot_min}** | — | {tot_cpm} |"
        )
    return "\n".join(lines)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    p = argparse.ArgumentParser(description="Per-day LiveKit-path voice cost from the BigQuery daily store.")
    p.add_argument("--project", default=BQ_PROJECT, help="BigQuery project (default $AURIE_BQ_PROJECT or aura-main)")
    p.add_argument("--dataset", default=BQ_DATASET, help="BigQuery dataset (default $AURIE_BQ_DATASET or aurie_cost)")
    p.add_argument("--date", help="single day (YYYY-MM-DD)")
    p.add_argument("--from", dest="date_from", help="window start, inclusive (YYYY-MM-DD)")
    p.add_argument("--to", dest="date_to", help="window end, inclusive (YYYY-MM-DD)")
    p.add_argument("--last", type=int, default=None, help="most recent N calendar days ending at the latest stored day")
    p.add_argument("--json", dest="json_path", help="also write the structured payload to this path")
    args = p.parse_args()

    per_user_table = f"{args.project}.{args.dataset}.aurie_per_user_daily_cost"
    unit_table = f"{args.project}.{args.dataset}.aurie_daily_unit_costs"

    from bq import BQUnavailable, BQError
    try:
        mn, mx = _bounds(args.project, per_user_table)
        lo, hi = _resolve_window(args, mn, mx)
        days = _fetch(args.project, per_user_table, unit_table, lo, hi)
    except BQUnavailable as e:
        raise SystemExit(f"error: BigQuery unavailable: {e} — BigQuery is the source of truth for the "
                         "cost store (needs GCP_KEY + google-cloud-bigquery).")
    except BQError as e:
        raise SystemExit(f"error: BigQuery: {e}")

    rows = [days[d] for d in sorted(days)]
    if not rows:
        raise SystemExit(f"error: no stored days in {lo}..{hi}; widen the window or check "
                         "eng:store-aurie-user-daily-cost coverage.")

    def _sum(key):
        return round(sum(r.get(key) or 0 for r in rows), 2)
    any_min = any(r["lk_minutes"] is not None for r in rows)
    totals = {
        "transport_usd": _sum("transport_usd"),
        "stt_usd": _sum("stt_usd"),
        "tts_cartesia_usd": _sum("tts_cartesia_usd"),
        "tts_11labs_usd": _sum("tts_11labs_usd"),
        "livekit_path_usd": _sum("livekit_path_usd"),
        "lk_minutes": round(sum(r["lk_minutes"] or 0 for r in rows), 1) if any_min else None,
    }
    # Blended $/min must divide matched quantities: only days that actually have recovered
    # minutes may contribute to the numerator. A day with LiveKit-path spend but no minutes
    # (unit-store lag, or an 11Labs-TTS-only remainder on `Other`) would otherwise inflate
    # the rate — same rule the upstream unit-cost store applies. Report what was excluded.
    rated = [r for r in rows if r["lk_minutes"] is not None]
    rated_usd = round(sum(r.get("livekit_path_usd") or 0 for r in rated), 2)
    unrated_usd = round(totals["livekit_path_usd"] - rated_usd, 2)
    blended_cpm = (round(rated_usd / totals["lk_minutes"], 4)
                   if totals["lk_minutes"] else None)
    peak = max(rows, key=lambda r: r.get("livekit_path_usd") or 0)
    summary = {
        "from": lo, "to": hi, "days": len(rows),
        "totals": totals,
        "blended_cost_per_voice_min": blended_cpm,
        # Provenance for the blended rate: which $ it covers, and what it had to leave out.
        "blended_basis_usd": rated_usd,
        "blended_excluded_usd": unrated_usd,
        "blended_excluded_days": [r["date"] for r in rows if r["lk_minutes"] is None
                                  and (r.get("livekit_path_usd") or 0)],
        "peak": {"date": peak["date"], "livekit_path_usd": peak["livekit_path_usd"]},
        "latest": rows[-1],
    }
    payload = {
        "skill_version": _skill_version(),
        "source": per_user_table,
        "summary": summary,
        "days": rows,
    }

    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)

    print(_render_markdown(rows, summary))
    print()
    span = f"{lo} → {hi}" if lo != hi else lo
    n_days = f"{len(rows)} day{'s' if len(rows) != 1 else ''}"
    if not totals["livekit_path_usd"]:
        # Stored days exist but carry no LiveKit-path spend — a pre-traffic window, not a gap.
        headline = (f"LiveKit-path voice, {span} ({n_days}): **no LiveKit-path spend** — "
                    "the window predates LiveKit traffic (or the path was idle). "
                    "All voice ran on the ElevenLabs ConvAI path; see "
                    "`eng:report-aurie-daily-costs` for that")
    else:
        headline = f"LiveKit-path voice, {span} ({n_days}): **${totals['livekit_path_usd']:,.2f}** total"
        if totals["lk_minutes"]:
            headline += (f" over ≈{totals['lk_minutes']:,.0f} session-min "
                         f"(≈${blended_cpm:.4f}/min)")
        if len(rows) > 1:  # a peak day is only meaningful across a multi-day window
            headline += f". Peak {peak['date']} ${peak['livekit_path_usd']:,.2f}"
    print(headline + ".")
    print()
    notes = ["LiveKit-path $ are DERIVED from PRICING-SHEET ESTIMATE rates upstream "
             "(Cartesia / Deepgram / LiveKit-transport); only the 11Labs-TTS column is an "
             "authoritative billed figure"]
    # Only claim the Deepgram prod-key gap when actual LiveKit SESSIONS ran yet STT is $0.
    # Gate on transport (and recovered minutes) — NOT on livekit_path_usd, which a lone
    # 11Labs-TTS remainder on `Other` can make positive with no session traffic behind it
    # (e.g. 2026-07-20). The gap was also real historically but the key now reads the prod
    # project on recent days, and an all-zero pre-traffic window is not a Deepgram problem.
    if (totals["transport_usd"] or totals["lk_minutes"]) and not totals["stt_usd"]:
        notes.append("Deepgram reads $0 despite LiveKit traffic — likely the prod-STT-project "
                     "key gap (see eng:analyze-deepgram-costs)")
    notes.append("Session-minutes are ≈ recovered by inverting the store's stored "
                 "$/LiveKit-voice-min (whose basis is transport + Deepgram + Cartesia, "
                 "excluding 11Labs-TTS); the $/voice-min column is all-in over the full "
                 "LiveKit-path total, so it exceeds that stored rate")
    # Only qualify the blended rate when there IS one; with no minutes anywhere there is no
    # rate to caveat and the note would just restate the total.
    if unrated_usd and blended_cpm is not None:
        notes.append(f"The blended $/voice-min covers ${rated_usd:,.2f} of the "
                     f"${totals['livekit_path_usd']:,.2f} total — ${unrated_usd:,.2f} on "
                     f"{len(summary['blended_excluded_days'])} day(s) has no recoverable "
                     "minutes and is excluded from the rate (but not from the total)")
    notes.append("Aggregate-only, no UID")
    print("_" + ". ".join(notes) + "._")


if __name__ == "__main__":
    main()
