#!/usr/bin/env python3
"""Seed `aura-main.anthropic_usage.model_pricing` with public list prices.

**This table is hand-read and dated on purpose.** There is no pricing API, so
nothing here can refresh itself, and an undated rate table ages into fiction
without ever changing. `verified_on` records when a human last checked the
published price page; a report that prices usage with stale rates produces a
plausible number rather than an error, which is exactly the failure that is hard
to notice. Same discipline as `livekit_plan.py`'s `PLAN_FACTS_VERIFIED`.

Rows are effective-dated. To change a rate, add a NEW row with a later
`effective_from` rather than editing the old one — historical months must keep
pricing at the rate that was actually in force.

What these prices are for: valuing usage that sits **inside** the seat
allowance, where the export's dollar columns are $0 by construction. The output
is an API-equivalent list value, not an invoice. It answers "what would this
usage have cost on the API", which is the number that makes a $100 premium seat
comparable to what it delivers.
"""
from __future__ import annotations

import datetime as dt
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bq  # noqa: E402

TABLE = "aura-main.anthropic_usage.model_pricing"

VERIFIED_ON = "2026-10-23"
SOURCE = "https://www.anthropic.com/pricing + platform.claude.com/docs pricing"

# (model, input_$/MTok, output_$/MTok, cache_read_$/MTok or None for 0.1x input)
# cache write 5m = 1.25x input, 1h = 2x input (standard multipliers).
RATES = [
    ("claude-fable-5-1",          10.0, 50.0, 0.25),
    ("claude-fable-5",            10.0, 50.0, 0.25),
    ("claude-opus-5-5",            4.0, 20.0, 0.20),
    ("claude-opus-5",              5.0, 25.0, None),
    ("claude-opus-4-8",            5.0, 25.0, None),
    ("claude-opus-4-7",            5.0, 25.0, None),
    ("claude-opus-4-6",            5.0, 25.0, None),
    ("claude-sonnet-5-5",          2.0, 10.0, 0.10),
    ("claude-sonnet-5",            2.0, 10.0, None),
    ("claude-sonnet-4-6",          3.0, 15.0, None),
    ("claude-opus-4-5-20251101",   5.0, 25.0, None),
    ("claude-sonnet-4-5-20250929", 3.0, 15.0, None),
    ("claude-sonnet-4-20250514",   3.0, 15.0, None),
    # Haiku 5.5 is tiered by prompt length; the export cannot tell the tiers
    # apart, so this is the <=100k tier and its value is a lower bound.
    ("claude-haiku-5-5",           0.10, 0.50, 0.01),
    ("claude-haiku-4-5-20251001",  1.0,  5.0, None),
    ("claude-haiku-4-5",           1.0,  5.0, None),
]

# One effective_from covers the whole history: no model's list price has changed
# since our first data day, and a model released later has no usage before its
# release, so dating its row from the start prices nothing it should not.
EFFECTIVE_FROM = "2026-03-01"

SCHEMA = [
    ("effective_from", "DATE"), ("model", "STRING"),
    ("input_usd_per_mtok", "NUMERIC"), ("output_usd_per_mtok", "NUMERIC"),
    ("cache_read_usd_per_mtok", "NUMERIC"),
    ("cache_write_5m_usd_per_mtok", "NUMERIC"),
    ("cache_write_1h_usd_per_mtok", "NUMERIC"),
    ("verified_on", "DATE"), ("source", "STRING"),
]
ORDER = [c for c, _ in SCHEMA]


def build_rows() -> list:
    rows = []
    for model, inp, out, cread in RATES:
        rows.append({
            "effective_from": EFFECTIVE_FROM,
            "model": model,
            "input_usd_per_mtok": f"{inp:.4f}",
            "output_usd_per_mtok": f"{out:.4f}",
            "cache_read_usd_per_mtok": f"{(cread if cread is not None else inp * 0.1):.4f}",
            "cache_write_5m_usd_per_mtok": f"{inp * 1.25:.4f}",
            "cache_write_1h_usd_per_mtok": f"{inp * 2.0:.4f}",
            "verified_on": VERIFIED_ON,
            "source": SOURCE,
        })
    return rows


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    import csv, io
    rows = build_rows()
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    for r in rows:
        w.writerow([r[c] for c in ORDER])
    age = (dt.date.today() - dt.date.fromisoformat(VERIFIED_ON)).days
    if age > 120:
        print(f"! rates last verified {age} days ago ({VERIFIED_ON}) — re-check the "
              f"price page before trusting downstream valuations.", file=sys.stderr)
    n = bq.load_partition(TABLE, buf.getvalue().encode("utf-8"), SCHEMA,
                          EFFECTIVE_FROM, "aura-main")
    print(f"seeded {n} pricing rows effective {EFFECTIVE_FROM} (verified {VERIFIED_ON})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
