#!/usr/bin/env python3
"""Voice-cost-by-AI-premium-tier breakdown for the weekly Aurie cost report.

Walks ElevenLabs `/v1/convai/conversations` for per-user voice $, then joins
on Firebase UID against OpenAI completion metadata for the AI premium tier.
Renders a Markdown table identical in shape to the LLM tier breakdown that
the parent `report.py` emits.

Slow (~15-20 min depending on weekly conversation volume). The parent skill
gates this behind an explicit user opt-in in Step 3 — do not chain it
automatically into the default flow.

Architecture: invokes the sibling skills' per-user scripts as subprocesses,
each with `--json` so we get structured data without re-implementing their
pagination + retry + classification logic.

- `eng:analyze-elevenlabs-cost-per-user/scripts/report.py` → per-user voice $
- `eng:analyze-openai-cost-per-user/scripts/report.py`     → uid → ai_tier

Both sibling skills must be installed in the same plugin (eng/skills/...).

Caveat on the join: the AI premium tier is sourced from OpenAI completion
metadata in the same week. Users who upgraded mid-week show their
last-logged tier on the OpenAI side. Voice users with no OpenAI activity
in the window bucket into "(no OpenAI activity in window)".
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Make the sibling `report.py` importable so we can reuse its window
# resolution and stay aligned with the parent report's Sun-Sat semantics.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from report import (  # noqa: E402  (path insert must precede)
    CHARACTER_STATS_PATH,
    ELEVENLABS_API,
    _get,
    _to_pacific_window_unix_ms,
    resolve_window,
)


# Per-user voice $ is priced from BILLED CREDITS, not wall-clock minutes.
#
# ElevenLabs bills Conversational AI per credit consumed, not per minute of
# call duration. An earlier version of this script priced each user at
# $0.09/min in-plan (mirroring `analyze-elevenlabs-cost-per-user`), which
# overstated every tier by ~20%: the week of 2026-09-06 ran 455 credits/min
# against the ~545 credits/min that $0.09 implies, an effective $0.0751/min.
# See PROD-6019.
#
# The dollars-per-credit rate is DERIVED per run from the workspace billing
# endpoint (ConvAI fiat / ConvAI credits) rather than hardcoded, so a plan or
# price change self-corrects with no code edit.
CONVAI_PRODUCT_TYPE = "Conversational AI"

TIER_ORDER = [
    "AI Premium Paid",
    "AI Premium Trial",
    "AI Premium Free",
    "AI Premium Payment Pending",
    "Non AI Premium",
    "Never AI Premium",
    "(untagged)",
    "(no OpenAI activity in window)",
]


def _sibling_script(skill_name: str) -> Path:
    """Locate a sibling skill's report.py under the same plugin tree.

    Layout: `<plugin>/skills/<skill>/scripts/report.py`. Walk two levels up
    from this file's parent (`scripts/`) to reach `<plugin>/skills/`, then
    dive into the requested sibling.
    """
    here = Path(__file__).resolve().parent          # .../report-aurie-weekly-costs/scripts
    skills_root = here.parent.parent                # .../skills
    target = skills_root / skill_name / "scripts" / "report.py"
    if not target.exists():
        sys.exit(
            f"Sibling script not found: {target}\n"
            f"This skill depends on `eng:{skill_name}` being installed."
        )
    return target


def _run_sibling(
    script_path: Path,
    since: str,
    until: str,
    extra_args: list[str],
    label: str,
) -> dict:
    """Run a sibling script with `--since/--until --json <tmp>`; return JSON."""
    fd, tmp_path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        cmd = [
            sys.executable, str(script_path),
            "--since", since, "--until", until,
            "--json", tmp_path,
            *extra_args,
        ]
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        # Stream stderr through so the user sees "...50 fetched / ...100 fetched"
        # progress lines from the long-running ElevenLabs walk. Suppress stdout
        # (it's the sibling's rendered Markdown report, irrelevant here).
        result = subprocess.run(
            cmd, env=env, stdout=subprocess.DEVNULL, stderr=None, text=True,
        )
        if result.returncode != 0 and not os.path.exists(tmp_path):
            sys.exit(
                f"{label} failed (exit {result.returncode}) — see stderr above."
            )
        # Some siblings write JSON before their render step; tolerate render
        # failures when the JSON nevertheless landed.
        if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
            sys.exit(f"{label} produced no JSON at {tmp_path}.")
        with open(tmp_path, encoding="utf-8") as fh:
            return json.load(fh)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def fetch_convai_billing(start, end, api_key: str) -> tuple[float, float]:
    """Return (billed_usd, billed_credits) for Conversational AI in the window.

    Reads `/v1/usage/character-stats` with `breakdown_type=product_type` and
    keeps ONLY the `Conversational AI` series. That matters twice over:

    - It is the denominator the per-user walk should reconcile against. The
      walk covers ConvAI conversations and nothing else, so reconciling it
      against the all-product workspace total silently credits it with TTS
      spend it never counted — chiefly the LiveKit-path TTS key, which grows
      as that experiment scales and makes the gap look smaller than it is.
    - Its credits figure gives the dollars-per-credit rate used to price each
      user, so the pricing tracks whatever ElevenLabs actually charges.

    The window is re-anchored at Pacific midnight (same as the parent
    report's voice column) so both numbers describe the same seven days.
    """
    start_ms, end_ms = _to_pacific_window_unix_ms(start, end)
    headers = {"xi-api-key": api_key}

    def _sum_convai(metric: str) -> float:
        data = _get(
            f"{ELEVENLABS_API}{CHARACTER_STATS_PATH}",
            headers,
            {
                "start_unix": start_ms,
                "end_unix": end_ms,
                "breakdown_type": "product_type",
                "metric": metric,
                "aggregation_interval": "day",
                "include_workspace_metrics": "true",
            },
            label=f"ElevenLabs /usage/character-stats ({metric}, product_type)",
        )
        series = (data.get("usage") or {}).get(CONVAI_PRODUCT_TYPE) or []
        return sum(float(v) for v in series if v is not None)

    billed_cents = _sum_convai("fiat_units_spent")
    billed_credits = _sum_convai("credits")
    return billed_cents / 100.0, billed_credits


def _voice_cost_per_user(el: dict, usd_per_credit: float) -> dict[str, dict]:
    """uid -> {cost_usd, conversations, duration_secs} from sibling JSON.

    Cost is `billed credits x usd_per_credit`. The sibling already aggregates
    `total_credits` per user straight off each conversation's
    `metadata.cost`, so this is ElevenLabs' own charge for that user's calls
    rather than a rate applied to call duration.
    """
    out: dict[str, dict] = {}
    for u in el.get("by_user", []):
        in_plan_secs = u.get("duration_in_plan_secs", 0.0) or 0.0
        burst_secs = u.get("duration_burst_secs", 0.0) or 0.0
        credits = float(u.get("total_credits", 0.0) or 0.0)
        out[u["user_id"]] = {
            "cost_usd": credits * usd_per_credit,
            "credits": credits,
            "conversations": u.get("conversations", 0),
            "duration_secs": in_plan_secs + burst_secs,
        }
    return out


# Attribution coverage floor for the UID -> tier join. The sibling walks each
# production project with a project-scoped OPENAI_<PROJECT>_API_KEY (PROD-3572);
# a project whose key is unset is skipped, contributes no tier signal, and its
# users fall into "(no OpenAI activity in window)" or an understated paid tier.
# Dollars still reconcile against billed ConvAI, so the table looks healthy while
# the split is wrong -- the same failure shape report.py's ledger guard exists to
# stop. `attribution.coverage.coverage_pct` (ENG-1120) is the sibling's own
# measure of walkable spend that reached a real UID; gate on it.
_COVERAGE_FLOOR_PCT = 99.0


def _fail_closed_on_thin_tier_lookup(oa: dict, allow_degraded: bool) -> float | None:
    """Exit 2 when too little OpenAI spend resolved to a UID to trust the split.

    Returns the coverage percentage so the caller can surface it, or None when
    the sibling reports no walkable spend (a genuinely zero-LLM window, where a
    coverage ratio is undefined rather than bad).
    """
    coverage = (oa.get("attribution") or {}).get("coverage") or {}
    if not coverage or float(coverage.get("eligible_dollars") or 0.0) <= 0:
        return None
    pct = float(coverage.get("coverage_pct") or 0.0)
    if pct >= _COVERAGE_FLOOR_PCT or allow_degraded:
        return pct
    unreached = float(coverage.get("unreached_dollars") or 0.0)
    print(
        f"FATAL: only {pct:.1f}% of walkable OpenAI spend resolved to a user "
        f"({_fmt_money(unreached)} unreached); the tier floor is "
        f"{_COVERAGE_FLOOR_PCT:.0f}%.\n"
        "  Voice dollars are correct and will still reconcile against billed\n"
        "  Conversational AI, but the paid/non-paid split would be understated:\n"
        "  users in an unwalked project carry voice cost with no tier signal and\n"
        "  land in the free bucket. Check the stderr above for\n"
        "  'OPENAI_<PROJECT>_API_KEY not set' and set the named keys, or read the\n"
        "  split from `report.py --from-bigquery`, which needs no per-project key.\n"
        "  To accept a degraded split deliberately: --allow-degraded-attribution,\n"
        "  or set AINATIVE_ALLOW_DEGRADED_ATTRIBUTION=1.",
        file=sys.stderr,
    )
    raise SystemExit(2)


def _uid_to_tier(oa: dict) -> dict[str, str]:
    """uid -> ai_tier from OpenAI per-user JSON.

    Uses `users_all` (the full ranked list) rather than `top_users` so even
    long-tail Firebase UIDs are joinable.
    """
    return {u["uid"]: u.get("ai_tier", "(untagged)") for u in oa.get("users_all", [])}


def _fmt_money(amount: float) -> str:
    """Render USD with the sign OUTSIDE the symbol ("-$4.04", not "$-4.04").

    The reconciliation delta is routinely negative, which is where the naive
    form shows up.
    """
    sign = "-" if amount < 0 else ""
    return f"{sign}${abs(amount):,.2f}"


def _render(
    by_tier: dict[str, dict],
    total_cost: float,
    total_users: int,
    voice_workspace_total: float | None,
    convai_billed_usd: float | None,
    window_label: str,
) -> str:
    lines: list[str] = []
    lines.append(
        f"**Voice cost by AI premium tier — {window_label}** "
        f"(joined via Firebase UID)"
    )
    lines.append("")
    lines.append("| Tier | Voice spend | Share | Active voice users | $/user |")
    lines.append("|---|---:|---:|---:|---:|")

    # Render canonical order first, then any unknown variants.
    rows = [by_tier[t] for t in TIER_ORDER if t in by_tier]
    seen = {r["tier"] for r in rows}
    for t, b in by_tier.items():
        if t not in seen:
            rows.append(b)
    for b in rows:
        share = (b["cost"] / total_cost * 100) if total_cost > 0 else 0
        per_user = (b["cost"] / b["users"]) if b["users"] > 0 else 0
        lines.append(
            f"| {b['tier']} | {_fmt_money(b['cost'])} | {share:.1f}% | "
            f"{b['users']:,} | {_fmt_money(per_user)} |"
        )

    lines.append("")
    lines.append(
        f"_Total: {_fmt_money(total_cost)} across {total_users:,} voice users._"
    )

    if convai_billed_usd is not None and convai_billed_usd > 0:
        delta = total_cost - convai_billed_usd
        pct = abs(delta) / convai_billed_usd * 100
        lines.append("")
        lines.append(
            f"_Reconciliation: per-user sum {_fmt_money(total_cost)} vs. "
            f"billed Conversational AI {_fmt_money(convai_billed_usd)} \u2014 "
            f"{_fmt_money(delta)} / {pct:.2f}% gap. Expect well under 1%; the "
            f"residual is conversations with no `userId` (agent-internal or "
            f"test calls), which bill to the workspace but cannot be "
            f"attributed to a user._"
        )

    if voice_workspace_total is not None and voice_workspace_total > 0:
        uncovered = voice_workspace_total - (convai_billed_usd or 0.0)
        lines.append("")
        lines.append(
            f"_Coverage: the spreadsheet row's voice total is "
            f"{_fmt_money(voice_workspace_total)} (all ElevenLabs products). "
            f"This table covers Conversational AI only; the remaining "
            f"{_fmt_money(uncovered)} is non-ConvAI TTS \u2014 chiefly the "
            f"LiveKit-path and Intelligence Service keys \u2014 which carries "
            f"no per-user attribution and cannot be split by tier._"
        )

    return "\n".join(lines)


def main() -> None:
    # Force UTF-8 stdout so the "—", arrows, and bullets in the rendered
    # Markdown survive Windows consoles that default to cp1252.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(
        description=(
            "Voice-cost-by-AI-premium-tier breakdown for the weekly cost "
            "report. Walks ElevenLabs `/v1/convai/conversations` for "
            "per-user voice $, then joins on Firebase UID against OpenAI "
            "completion metadata for the AI premium tier. Slow (~15-20 "
            "min) — gated by an explicit user opt-in in the parent "
            "skill's Step 3."
        )
    )
    ap.add_argument(
        "--week",
        help="Sunday of the week (YYYY-MM-DD); same semantics as report.py.",
    )
    ap.add_argument(
        "--since",
        help="Start date (YYYY-MM-DD, UTC). Use with --until.",
    )
    ap.add_argument(
        "--until",
        help="End date (YYYY-MM-DD, UTC, exclusive). Use with --since.",
    )
    ap.add_argument(
        "--voice-workspace-total", type=float, default=None,
        dest="voice_workspace_total",
        help=(
            "Pass the workspace-endpoint voice total ($) from the main "
            "report. Used only for the coverage line showing how much of the "
            "spreadsheet row is non-ConvAI TTS; the reconciliation itself "
            "uses the billed Conversational AI figure this script fetches "
            "directly."
        ),
    )
    ap.add_argument(
        "--allow-degraded-attribution", action="store_true",
        dest="allow_degraded_attribution",
        help=(
            # argparse %-formats help strings itself, so a literal percent sign has
            # to survive as %% all the way into the value argparse receives. Building
            # this with `... %% ... % _COVERAGE_FLOOR_PCT` collapsed it to a single %
            # BEFORE argparse saw it, and argparse then read "99% of" as a format spec
            # and raised "badly formed help string" at import time on Python 3.12+,
            # where _check_help validates. The f-string does the substitution and
            # leaves %% intact for argparse to render as one percent sign.
            "Accept the tier split when the OpenAI per-user walk covered less "
            f"than {_COVERAGE_FLOOR_PCT:.0f}%% of walkable spend. Env: "
            "AINATIVE_ALLOW_DEGRADED_ATTRIBUTION=1."
        ),
    )
    ap.add_argument(
        "--json", dest="json_out",
        help="Write structured JSON to this path.",
    )
    args = ap.parse_args()

    # Reuse the parent script's window resolution so the breakdown lines
    # up exactly with the row the main report emitted.
    start_utc, end_utc, label, _ = resolve_window(args.week, args.since, args.until)
    since = start_utc.strftime("%Y-%m-%d")
    until = end_utc.strftime("%Y-%m-%d")

    print(
        f"# Voice tier breakdown for {label} "
        f"({since} -> {until}, UTC half-open).",
        file=sys.stderr,
    )
    api_key = os.environ.get("ELEVENLABS_ADMIN_API_KEY")
    if not api_key:
        sys.exit(
            "ELEVENLABS_ADMIN_API_KEY is not set - required to read the "
            "billed Conversational AI total that per-user cost is priced "
            "from. See eng:analyze-elevenlabs-costs Step 0."
        )

    # Pull the billing basis FIRST: it is cheap, and it decides the rate every
    # per-user figure below is priced at. Failing here costs seconds; failing
    # after the conversation walk costs ~20 minutes.
    convai_billed_usd, convai_credits = fetch_convai_billing(
        start_utc, end_utc, api_key
    )
    if convai_credits <= 0:
        sys.exit(
            f"No Conversational AI credits billed in {label} - cannot derive "
            "a per-credit rate. Check the window and the API key's Workspace "
            "Analytics scope."
        )
    usd_per_credit = convai_billed_usd / convai_credits
    print(
        f"# Billed Conversational AI: ${convai_billed_usd:,.2f} over "
        f"{convai_credits:,.0f} credits "
        f"(${usd_per_credit * 1000:.5f} per 1k credits).",
        file=sys.stderr,
    )

    print(
        "# Step 1/2: walking ElevenLabs conversations "
        "(this is the slow step, ~15-20 min)...",
        file=sys.stderr,
    )
    el_script = _sibling_script("analyze-elevenlabs-cost-per-user")
    el = _run_sibling(
        el_script, since, until,
        extra_args=[],
        label="analyze-elevenlabs-cost-per-user",
    )

    print(
        "# Step 2/2: pulling OpenAI per-user AI tier lookup...",
        file=sys.stderr,
    )
    oa_script = _sibling_script("analyze-openai-cost-per-user")
    oa = _run_sibling(
        oa_script, since, until,
        extra_args=["--no-stats", "--no-headline"],
        label="analyze-openai-cost-per-user",
    )

    allow_degraded = bool(
        args.allow_degraded_attribution
        or os.environ.get("AINATIVE_ALLOW_DEGRADED_ATTRIBUTION")
    )
    coverage_pct = _fail_closed_on_thin_tier_lookup(oa, allow_degraded)

    voice_by_user = _voice_cost_per_user(el, usd_per_credit)
    uid_to_tier = _uid_to_tier(oa)

    by_tier: dict[str, dict] = {}
    total_cost = 0.0
    matched_cost = 0.0
    matched_users = 0
    for uid, info in voice_by_user.items():
        cost = info["cost_usd"]
        total_cost += cost
        if uid in uid_to_tier:
            tier = uid_to_tier[uid]
            matched_cost += cost
            matched_users += 1
        else:
            tier = "(no OpenAI activity in window)"
        bucket = by_tier.setdefault(
            tier,
            {
                "tier": tier, "cost": 0.0, "users": 0,
                "duration_secs": 0.0, "credits": 0.0,
            },
        )
        bucket["cost"] += cost
        bucket["users"] += 1
        bucket["duration_secs"] += info["duration_secs"]
        bucket["credits"] += info["credits"]

    md = _render(
        by_tier, total_cost, len(voice_by_user),
        voice_workspace_total=args.voice_workspace_total,
        convai_billed_usd=convai_billed_usd,
        window_label=label,
    )
    print(md)
    if coverage_pct is not None:
        print(
            f"\n_Tier-lookup coverage: {coverage_pct:.1f}% of walkable OpenAI "
            "spend resolved to a user. A figure below "
            f"{_COVERAGE_FLOOR_PCT:.0f}% means unwalked projects pushed paid "
            "users into the free bucket._"
        )

    if args.json_out:
        payload = {
            "window_label": label,
            "start_iso": start_utc.isoformat(),
            "end_iso": end_utc.isoformat(),
            "voice_per_user_sum_usd": total_cost,
            "voice_workspace_total_usd": args.voice_workspace_total,
            "convai_billed_usd": convai_billed_usd,
            "convai_billed_credits": convai_credits,
            "usd_per_credit": usd_per_credit,
            "pricing_basis": "billed credits (metadata.cost) x derived $/credit",
            "voice_users_total": len(voice_by_user),
            "voice_users_matched": matched_users,
            "voice_cost_matched_usd": matched_cost,
            "tier_lookup_coverage_pct": coverage_pct,
            "by_ai_premium_tier": [
                {
                    "tier": b["tier"],
                    "cost_usd": b["cost"],
                    "active_users": b["users"],
                    "duration_secs": b["duration_secs"],
                    "credits": b["credits"],
                    "share": (b["cost"] / total_cost * 100) if total_cost > 0 else 0,
                }
                for b in (
                    [by_tier[t] for t in TIER_ORDER if t in by_tier]
                    + [b for t, b in by_tier.items() if t not in TIER_ORDER]
                )
            ],
        }
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)


if __name__ == "__main__":
    main()
