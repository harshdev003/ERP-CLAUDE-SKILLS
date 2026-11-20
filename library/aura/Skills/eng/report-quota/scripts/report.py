"""Unified vendor subscription-consumption report for the Aurie voice + LLM stack.

Answers "how much of each vendor subscription have we consumed this billing period?" in one pane by
COMPOSING the per-vendor quota skills rather than reimplementing them. Each vendor skill already
derives its own quota/runway and emits a `--json` payload plus an exit code that encodes an alert
verdict (0=OK, 10=WARN, 20=CRITICAL); this runner invokes each as a subprocess with
`--alert --json <tmp>`, reads the payload for the numbers, and takes the verdict from the exit code —
so there is a single source of truth per vendor and zero duplicated derivation.

Vendors: Cartesia (report-cartesia-quota), ElevenLabs (report-elevenlabs-quota), LiveKit
(report-livekit-quota), Deepgram (report-deepgram-quota). A vendor whose skill can't run (missing
key, API/scope error) is shown as UNAVAILABLE with the reason — it never sinks the whole report.

With `--alert`, the runner's own exit code is the WORST vendor severity (10=WARN, 20=CRITICAL; a
vendor that errored is surfaced but doesn't set the exit severity), and `--slack` posts the combined
table to SLACK_WEBHOOK_URL. Config error (bad flag) stays exit 2.

Each vendor skill reads its own credentials from env; this runner passes the environment through and
holds no secrets itself.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile

SKILLS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# Map a vendor sub-skill's exit code to a verdict. 10/20 are the family's WARN/CRITICAL; 0 is OK.
# Anything else (1 API failure, 2 config/scope, non-zero misc) is treated as UNAVAILABLE — the vendor
# couldn't be evaluated, which is surfaced but does not set the runner's alert severity.
def _verdict_from_exit(code: int) -> str:
    return {0: "OK", 10: "WARN", 20: "CRITICAL"}.get(code, "UNAVAILABLE")


def _run_vendor(skill: str, extra_args: list[str]) -> dict:
    """Run one vendor quota skill with --alert --json; return {status, payload, error}."""
    script = os.path.join(SKILLS_DIR, skill, "scripts", "report.py")
    if not os.path.exists(script):
        return {"status": "UNAVAILABLE", "payload": None, "error": f"skill not found: {skill}"}
    with tempfile.NamedTemporaryFile("r", suffix=".json", delete=False) as tf:
        tmp = tf.name
    try:
        proc = subprocess.run(
            [sys.executable, script, "--alert", "--json", tmp, *extra_args],
            capture_output=True, text=True, timeout=180, check=False,
        )
        status = _verdict_from_exit(proc.returncode)
        payload = None
        try:
            with open(tmp, encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, ValueError):
            payload = None
        error = None
        if status == "UNAVAILABLE":
            # Surface the last stderr line as the reason (skills print "ERROR: ..." to stderr).
            err_lines = [ln for ln in (proc.stderr or "").splitlines() if ln.strip()
                         and not ln.startswith("#")]
            error = err_lines[-1] if err_lines else f"exit {proc.returncode}"
        return {"status": status, "payload": payload, "error": error}
    except subprocess.TimeoutExpired:
        return {"status": "UNAVAILABLE", "payload": None, "error": "timed out"}
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Per-vendor one-line summary of the payload (each vendor's model differs)
# ---------------------------------------------------------------------------

def _fmt_int(n) -> str:
    try:
        return f"{int(round(float(n))):,}"
    except (TypeError, ValueError):
        return "?"


def _summ_cartesia(p: dict) -> str:
    return (f"{_fmt_int(p.get('consumed'))}/{_fmt_int(p.get('allotment'))} credits "
            f"({float(p.get('pct_used', 0)):.0f}%), proj ~{_fmt_int(p.get('projected_period_end'))}")


def _summ_elevenlabs(p: dict) -> str:
    # Going past the limit is normal on-demand overage, so the summary leads with overage $.
    ov = p.get("overage_usd")
    ov_bit = f", overage ${float(ov):,.0f}" if isinstance(ov, (int, float)) and ov > 0 else ""
    proj = p.get("projected_overage_usd")
    proj_bit = f", proj overage ~${float(proj):,.0f}" if isinstance(proj, (int, float)) else ""
    # pct_used is against the base grant; the live limit is raised as usage reaches it.
    return (f"{_fmt_int(p.get('used'))}/{_fmt_int(p.get('base_credits', p.get('limit')))} credits "
            f"({float(p.get('pct_used', 0)):.0f}%){ov_bit}{proj_bit}")


def _summ_livekit(p: dict) -> str:
    return (f"{_fmt_int(p.get('consumed_minutes'))}/{_fmt_int(p.get('included_minutes'))} min "
            f"({float(p.get('pct_used', 0)):.0f}%), proj ~{_fmt_int(p.get('projected_minutes'))}")


def _summ_deepgram(p: dict) -> str:
    bal = p.get("balance_usd")
    rw = p.get("runway_days")
    if isinstance(rw, (int, float)):
        rw_bit = ">10y" if rw > 3650 else f"~{float(rw):.0f}d"
    else:
        rw_bit = "n/a"
    bal_bit = f"${float(bal):,.2f}" if isinstance(bal, (int, float)) else "?"
    return f"balance {bal_bit}, runway {rw_bit}"


VENDORS = [
    {"key": "cartesia", "label": "Cartesia (TTS)", "skill": "report-cartesia-quota",
     "args": [], "summ": _summ_cartesia},
    {"key": "elevenlabs", "label": "ElevenLabs (TTS/agent)", "skill": "report-elevenlabs-quota",
     "args": [], "summ": _summ_elevenlabs},
    {"key": "livekit", "label": "LiveKit (transport)", "skill": "report-livekit-quota",
     "args": [], "summ": _summ_livekit},
    {"key": "deepgram", "label": "Deepgram (STT)", "skill": "report-deepgram-quota",
     "args": [], "summ": _summ_deepgram},
]

_ICON = {"OK": "✅", "WARN": "⚠️", "CRITICAL": "🚨", "UNAVAILABLE": "❓"}
_SEVERITY = {"OK": 0, "WARN": 1, "CRITICAL": 2}  # UNAVAILABLE deliberately not ranked


def render(rows: list[dict]) -> str:
    lines = ["# Vendor Subscription Consumption — voice + LLM stack", ""]
    lines.append("| Vendor | Status | Consumption |")
    lines.append("|---|---|---|")
    for r in rows:
        icon = _ICON[r["status"]]
        if r["status"] == "UNAVAILABLE":
            detail = f"_unavailable — {r['error']}_"
        elif r["payload"] is not None:
            detail = r["vendor"]["summ"](r["payload"])
        else:
            detail = "_(no data)_"
        lines.append(f"| {r['vendor']['label']} | {icon} {r['status']} | {detail} |")
    lines.append("")
    worst = _worst_status(rows)
    lines.append(f"**Overall: {_ICON[worst]} {worst}**")
    unavailable = [r["vendor"]["label"] for r in rows if r["status"] == "UNAVAILABLE"]
    if unavailable:
        lines.append(f"_Unavailable vendors (not counted in overall): {', '.join(unavailable)}_")
    lines.append("")
    lines.append("> Composed from the per-vendor quota skills (report-{cartesia,elevenlabs,livekit,"
                 "deepgram}-quota) — each derives its own figures; see those skills for methodology + "
                 "source-of-truth caveats. Cartesia/LiveKit are DERIVED estimates; ElevenLabs/Deepgram "
                 "balances are ACTUAL from the vendor API.")
    return "\n".join(lines)


def _worst_status(rows: list[dict]) -> str:
    worst = "OK"
    for r in rows:
        if r["status"] in _SEVERITY and _SEVERITY[r["status"]] > _SEVERITY[worst]:
            worst = r["status"]
    return worst


def post_to_slack(text: str, webhook: str) -> bool:
    try:
        import requests
    except ImportError:
        print("# requests not installed; cannot post to Slack", file=sys.stderr)
        return False
    resp = requests.post(webhook, json={"text": text}, timeout=15)
    return 200 <= resp.status_code < 300


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="Unified vendor subscription-consumption report (composes the quota skills)."
    )
    parser.add_argument("--only", help="Comma-separated vendor keys to include "
                        "(cartesia,elevenlabs,livekit,deepgram); default all")
    parser.add_argument("--json", help="Write the combined structured payload to this path")
    parser.add_argument("--alert", action="store_true",
                        help="Exit with the WORST vendor severity (10=WARN, 20=CRITICAL) so a wrapper "
                             "can gate on $?; an UNAVAILABLE vendor is surfaced but doesn't set it.")
    parser.add_argument("--slack", action="store_true",
                        help="Post the combined table to SLACK_WEBHOOK_URL (requires --alert) when the "
                             "overall status != OK.")
    args = parser.parse_args()

    selected = VENDORS
    if args.only:
        keys = {k.strip() for k in args.only.split(",") if k.strip()}
        selected = [v for v in VENDORS if v["key"] in keys]
        if not selected:
            print(f"ERROR: --only matched no vendors (known: "
                  f"{', '.join(v['key'] for v in VENDORS)})", file=sys.stderr)
            return 2

    rows: list[dict] = []
    for vendor in selected:
        print(f"# running {vendor['skill']} ...", file=sys.stderr)
        result = _run_vendor(vendor["skill"], vendor["args"])
        rows.append({"vendor": vendor, **result})

    print(render(rows))

    if args.json:
        out = [{"vendor": r["vendor"]["key"], "label": r["vendor"]["label"],
                "status": r["status"], "error": r.get("error"), "payload": r["payload"]}
               for r in rows]
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"vendors": out, "overall": _worst_status(rows)}, f, indent=2, default=str)
        print(f"\nWrote {args.json}", file=sys.stderr)

    worst = _worst_status(rows)
    if args.alert:
        if args.slack and worst != "OK":
            webhook = os.environ.get("SLACK_WEBHOOK_URL")
            if not webhook:
                print("# --slack given but SLACK_WEBHOOK_URL not set; skipping post", file=sys.stderr)
            elif post_to_slack(render(rows), webhook):
                print("# posted combined report to Slack", file=sys.stderr)
            else:
                print("# Slack post failed", file=sys.stderr)
        return {"OK": 0, "WARN": 10, "CRITICAL": 20}[worst]

    return 0


if __name__ == "__main__":
    sys.exit(main())
