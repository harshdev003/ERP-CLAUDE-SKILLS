#!/usr/bin/env python3
"""The ideation leg of the Aura Recommendations loop.

The Features loop ideates by walking the app with a sim test. This loop has no
screen to walk, so it proposes from what production and the offline harness
measure. Without it the loop is a backlog somebody filled once by hand.

  signals [--layer L]            read-only: every signal, value vs threshold
  telemetry --layer L            breached-and-moved signals -> candidates
  sweep --layer L --out f.json   telemetry + priors + materiality -> findings JSON
  layers                         the rotation, least-recently-swept first

The output of `sweep` is the shape `product:record-ideas --from-json` accepts,
so the record leg needs no change and fingerprint dedup comes for free.

Thresholds and their provenance live in references/signals.md. Every number in
SIGNAL_SPECS was measured against production on 2026-09-22; re-measure before
trusting a drift.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import bq
import signals as sig

CONTENT_SERVICE = (
    'resource.type="k8s_container" '
    'AND resource.labels.namespace_name="aura-services" '
    'AND resource.labels.container_name="content-service"'
)
MIXPANEL = "`mixpanel-prod-1.mixpanel_nessie_day_partitioned_963821"


# --------------------------------------------------------------------------
# signal specs — what the sweep watches, and where the number comes from
# --------------------------------------------------------------------------
#
# `kind` selects the collector. `threshold`/`direction` decide a breach;
# `prior` is filled from the previous run's stored value when one exists.
SIGNAL_SPECS = [
    dict(key="empty_model_rate", layer="ranking", kind="log_count", unit="/h",
         threshold=50.0, direction="above",
         log_message="No results from this model",
         title="A model in the blend is returning nothing",
         mechanism="restore or remove the model so the blend stops silently "
                   "re-ranking around an empty contributor"),
    dict(key="empty_response_rate", layer="filters", kind="log_count", unit="/h",
         threshold=50.0, direction="above",
         log_message="No recommendation",
         title="Recommendation responses come back empty",
         mechanism="widen the eligibility intersection so a specific request "
                   "can be satisfied without stripping the user's intent"),
    dict(key="catalogue_coverage", layer="candidate supply", kind="coverage", unit="",
         threshold=0.30, direction="below",
         title="Most of the catalogue is never recommended",
         mechanism="surface tail content the current ranking never reaches"),
    dict(key="sub3s_skip_rate", layer="ranking", kind="skip_rate", unit="",
         threshold=0.18, direction="above",
         title="Plays are being abandoned within three seconds",
         mechanism="stop serving tracks users reject on sight"),
]


# Windows tried in order. A high-volume signal saturates the entry cap over 24h
# — activation-model alone runs ~5,290/h, so a 24h read is ~127k entries against
# a 20k cap and would report a stuck ~833/h. Narrowing the window until the count
# comes back UNCAPPED yields a real rate instead of a truncated one.
LOG_WINDOWS_HOURS = (24, 6, 1)
LOG_ENTRY_CAP = 20000


def _log_count_per_hour(message: str) -> float:
    """Audit-log occurrences per hour, measured over the widest uncapped window.

    Imported lazily so `signals`/`layers` work without cluster credentials.
    """
    import gcp  # noqa: PLC0415

    auth = gcp.get_client()
    last_rate = 0.0
    for hours in LOG_WINDOWS_HOURS:
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        flt = f'{CONTENT_SERVICE} AND jsonPayload.message="{message}" AND timestamp>="{since}"'
        n = sum(1 for _ in gcp.list_entries(auth, flt, max_entries=LOG_ENTRY_CAP))
        last_rate = n / hours
        if n < LOG_ENTRY_CAP:
            return last_rate
        print(f"# '{message}' saturated the {LOG_ENTRY_CAP}-entry cap over {hours}h "
              f"— narrowing the window", file=sys.stderr)

    # Every window saturated. The value is a lower bound, and reporting it as a
    # rate would let the `moved` check compare two truncated numbers and
    # conclude nothing changed. Refuse instead — collect() reports the skip.
    raise RuntimeError(
        f"'{message}' saturated the entry cap at every window "
        f"({', '.join(f'{h}h' for h in LOG_WINDOWS_HOURS)}); the rate would be a "
        f"lower bound, not a measurement")


def _catalogue_coverage(days: int = 30) -> float:
    rows = list(bq.query(f"""
        WITH heard AS (
          SELECT DISTINCT session AS track_id
          FROM {MIXPANEL}.meditation_done_single`
          WHERE _PARTITIONTIME >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY)
            AND session IS NOT NULL
        )
        SELECT (SELECT COUNT(*) FROM heard) AS heard,
               (SELECT COUNT(*) FROM `aura-main.firebase.tracks`) AS catalogue
    """))
    r = rows[0]
    return (r["heard"] / r["catalogue"]) if r["catalogue"] else 0.0


def _sub3s_skip_rate(days: int = 7) -> float:
    rows = list(bq.query(f"""
        SELECT
          (SELECT COUNT(*) FROM {MIXPANEL}.track_skip`
           WHERE _PARTITIONTIME >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY)
             AND duration < 3) AS quick_skips,
          (SELECT COUNT(*) FROM {MIXPANEL}.meditation_play`
           WHERE _PARTITIONTIME >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY)
          ) AS plays
    """))
    r = rows[0]
    return (r["quick_skips"] / r["plays"]) if r["plays"] else 0.0


COLLECTORS = {
    "log_count": lambda spec: _log_count_per_hour(spec["log_message"]),
    "coverage": lambda spec: _catalogue_coverage(),
    "skip_rate": lambda spec: _sub3s_skip_rate(),
}


def collect(layer: Optional[str], priors: Dict[str, float]) -> List[sig.Signal]:
    out: List[sig.Signal] = []
    for spec in SIGNAL_SPECS:
        if layer and spec["layer"] != layer:
            continue
        try:
            value = COLLECTORS[spec["kind"]](spec)
        except (Exception, SystemExit) as exc:  # noqa: BLE001
            # One unreachable source must not abort the sweep — report it and
            # carry on, so a GCP outage does not read as "nothing to propose".
            #
            # SystemExit is caught explicitly and is NOT redundant: the lazily
            # imported gcp helper calls sys.exit() when its library or key is
            # missing, and SystemExit derives from BaseException, not Exception.
            # Without it the first log signal would kill the process before any
            # BigQuery signal ran — the exact opposite of the contract above.
            print(f"# SKIPPED {spec['key']}: {exc}", file=sys.stderr)
            continue
        out.append(sig.Signal(
            key=spec["key"], layer=spec["layer"], value=value,
            threshold=spec["threshold"], direction=spec["direction"],
            prior=priors.get(spec["key"]), unit=spec.get("unit", ""),
            evidence=f"{spec['key']} = {value:.4g}{spec.get('unit','')} "
                     f"(threshold {spec['threshold']:.4g}, measured {datetime.now(timezone.utc):%Y-%m-%d})",
        ))
    return out


def to_candidates(found: List[sig.Signal]) -> List[sig.Candidate]:
    by_key = {s["key"]: s for s in SIGNAL_SPECS}
    cands = []
    for s in found:
        spec = by_key[s.key]
        cands.append(sig.Candidate(
            title=spec["title"],
            description=f"{spec['title']}. {s.evidence}",
            layer=s.layer,
            mechanism=spec["mechanism"],
            category="recommendation quality",
            rationale=s.evidence,
            hypothesis=f"If we {spec['mechanism']}, {s.key} moves back inside "
                       f"{s.threshold:.4g} without a loss elsewhere.",
            evidence=s.evidence,
            source="telemetry",
        ))
    return cands


def load_priors(path: Optional[str]) -> Dict[str, float]:
    if not path:
        return {}
    try:
        with open(path) as fh:
            return {k: float(v) for k, v in json.load(fh).items()}
    except FileNotFoundError:
        return {}


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("layers")
    for name in ("signals", "telemetry", "sweep"):
        s = sub.add_parser(name)
        s.add_argument("--layer", choices=sig.LAYERS, default=None)
        s.add_argument("--priors-file", default=None,
                       help="JSON {signal_key: previous_value} from the last run")
        if name == "sweep":
            s.add_argument("--out", required=True)
            s.add_argument("--backlog-file", default=None,
                           help="JSON array of existing loop rows "
                                "(title, domain, status, rejection_reason)")
            s.add_argument("--margin", type=float, default=sig.DEFAULT_MATERIALITY_MARGIN)

    a = p.parse_args(argv)

    if a.cmd == "layers":
        for layer in sig.LAYERS:
            print(layer)
        return 0

    found = collect(a.layer, load_priors(a.priors_file))
    if not found:
        print("no signals collected — every source was unreachable or filtered out",
              file=sys.stderr)
        return 2

    if a.cmd == "signals":
        for s in found:
            flag = "BREACH" if s.breached else "ok"
            moved = "" if s.moved else "  (steady vs prior — not news)"
            print(f"{s.key:<24} {s.value:>10.4g}{s.unit:<4} "
                  f"{s.direction:>5} {s.threshold:<8.4g} {flag}{moved}")
        return 0

    actionable = [s for s in found if s.breached and s.moved]
    candidates = to_candidates(actionable)

    if a.cmd == "telemetry":
        print(json.dumps([c.to_idea() for c in candidates], indent=2))
        return 0

    backlog = []
    if a.backlog_file:
        with open(a.backlog_file) as fh:
            backlog = json.load(fh)
    kept, suppressed = sig.check_priors(candidates, backlog)

    material, held = [], []
    for c in kept:
        ok, why = sig.is_material(c, margin=a.margin)
        (material if ok else held).append((c, why))

    with open(a.out, "w") as fh:
        json.dump([c.to_idea() for c, _ in material], fh, indent=2)

    print(f"signals collected      {len(found)}")
    print(f"  breached and moved   {len(actionable)}")
    print(f"  suppressed by priors {len(suppressed)}")
    for c, why in suppressed:
        print(f"    - {c.title}: {why}")
    print(f"  held back (digest)   {len(held)}")
    for c, why in held:
        print(f"    - {c.title}: {why}")
    print(f"  written to {a.out}   {len(material)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
