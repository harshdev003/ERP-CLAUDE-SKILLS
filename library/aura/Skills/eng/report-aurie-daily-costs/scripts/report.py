#!/usr/bin/env python3
"""
Daily Aurie cost report — OpenAI (LLM) + multi-vendor Voice per day.

Renders one row per calendar day over a window (month-to-date by default),
with OpenAI spend, the per-vendor voice split (ElevenLabs + Cartesia + Inworld
+ Deepgram + LiveKit), and the combined total, plus a total row.

Voice is MULTI-VENDOR (PROD-3180). As voice traffic shifts from the ElevenLabs
ConvAI path to the LiveKit path (Deepgram STT + Cartesia TTS + LiveKit
transport), this report additively surfaces each vendor's DAILY WORKSPACE TOTAL
alongside the existing ElevenLabs figure so the ElevenLabs-vs-LiveKit-path voice
split is visible over time.

Sources
-------
  - OpenAI:     GET /v1/organization/costs (bucket_width=1d), summed per UTC day.
                Authoritative — same numbers OpenAI bills. Needs OPENAI_ADMIN_API_KEY.
  - ElevenLabs: GET /v1/usage/character-stats (aggregation_interval=day),
                metric=fiat_units_spent. The API returns this in CENTS, so we
                divide by 100 -> USD (reconciles within ~1% of the dashboard
                "Cost before overage surcharge" tile). ACTUAL billed value.
  - Cartesia:   GET /usage/credits (interval=day) × --cartesia-rate-per-million.
                Credits ACTUAL; $ DERIVED from a PRICING-SHEET ESTIMATE rate.
  - Inworld:    GET /usage/v1/records (services=tts, one UTC day) characters ×
                --inworld-rate-per-million. Characters ACTUAL; $ DERIVED from the
                TTS-2 list rate. Without INWORLD_API_KEY, a day this run walked the
                logs for is derived from the agent's tts_characters; any other day
                renders "—".
  - Deepgram:   the BigQuery cost store's booked `voice_deepgram` for every day
                it holds — LiveKit Inference overage past the plan's included
                credit block, plus any direct Deepgram usage. Unstored days are
                priced the way the store prices them: the LiveKit walk's STT
                transport split decides the source; Inference STT goes through the
                store's credit-block helper, direct STT through GET
                /v1/projects/{id}/usage × --deepgram-rate-per-hour. Deepgram's API
                cannot see Inference STT and answers $0 for it, so a day whose
                transport or drawdown can't be established renders "—", not $0.
  - LiveKit:    session-minutes × --livekit-rate-per-min. $ DERIVED (ESTIMATE).
                Minutes come from the BigQuery cost store (aura-main.aurie_cost)
                for every day it holds, recovered by dividing the store's LiveKit
                rate back out of the stored voice_livekit; only the days it
                doesn't hold (normally just the current partial day) walk the
                forwarded GCP logs live. Reading
                stored MINUTES rather than stored dollars keeps
                --livekit-rate-per-min meaningful on every day of the window.
                `--no-bigquery` forces the live walk for every day.

The three LiveKit-path vendors reuse the daily-workspace-total helpers from the
sibling eng:store-aurie-user-daily-cost skill (self-contained — no cross-skill
import; the helpers there are the canonical seam for these vendor day totals).
Their rates are PRICING-SHEET ESTIMATES — replace with contract rates via the
flags.

An unreadable vendor-day is UNKNOWN, not $0
-------------------------------------------
A missing vendor key, a failed vendor API call or an exhausted Cloud Logging read
quota makes that vendor-day *unknown*. The report renders it "—", excludes it from
that vendor's column total, and marks the Voice / Combined figures "≥" as lower
bounds.

It used to render $0.00 and sum it as a zero. Because Deepgram and LiveKit are
LiveKit-path-only, a genuine $0 day is legitimate and common, so nothing about the
rendered table looked wrong — on a last-7d window on 2026-08-31 four consecutive
days read $0.00 against $34.84 actually stored, and the Voice and Combined totals
were short by the same amount. The store helpers already distinguish the two
cases (they return a note only on a real read failure, never for a quiet day);
this report just has to stop discarding that distinction. ENG-1214.

Both axes are bucketed in UTC for clean per-day alignment. (The ElevenLabs
dashboard is PT-anchored, so a given day may differ by a few hours' worth of
usage from what the dashboard shows for the same calendar date.)

Env
---
  OPENAI_ADMIN_API_KEY      Admin key (sk-admin-...). No MCP fallback.
  ELEVENLABS_ADMIN_API_KEY  Admin key, scope Workspace Analytics: Access
                            (+ User: Read for the plan/overage block).
                            Falls back to ELEVENLABS_API_KEY (may under-count).
  CARTESIA_ADMIN_API_KEY    Optional — Cartesia usage (else that vendor's days are
                            UNKNOWN: "—", not $0; see below).
  DEEPGRAM_OWNER_API_KEY    Optional — Deepgram usage (same UNKNOWN treatment).
  INWORLD_API_KEY           Optional — Inworld TTS usage (else derived from agent logs
                            on a walked day, UNKNOWN otherwise).
  GCP_KEY / GOOGLE_APPLICATION_CREDENTIALS / ADC  Optional — the BigQuery cost
                            store, and the live LiveKit log walk for unstored days.

Stdlib + requests, plus google-cloud-bigquery for the cost-store read (optional —
without it every day falls back to the live log walk).
"""
import argparse
import datetime as dt
import json
import os
import random
import sys
import time

import requests

# Reuse the sibling store skill's daily-workspace-total helpers for the three
# LiveKit-path vendors (self-contained per AINative Architecture principle 1 —
# we add the store skill's scripts dir to sys.path rather than duplicating the
# vendor-total math here; those helpers already carry the ESTIMATE-rate labels).
_STORE_SCRIPTS = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "..", "store-aurie-user-daily-cost", "scripts")
)
sys.path.insert(0, _STORE_SCRIPTS)
try:
    import store as _store  # noqa: E402
except Exception:  # noqa: BLE001 — LiveKit-path vendors are optional; degrade gracefully.
    _store = None

# The daily cost store in BigQuery is the authoritative record for LiveKit
# session-minutes, and reading it costs ONE query for a whole window instead of
# one Cloud Logging walk per day. Optional: if BigQuery is unreachable the report
# falls back to the live log walk day by day.
_BQ_IMPORT_ERROR = None
try:
    import bq as _bq  # noqa: E402  — sibling copy of the canonical eng:query-bigquery helper
except Exception as _ex:  # noqa: BLE001
    _bq, _BQ_IMPORT_ERROR = None, _ex

OPENAI_COSTS_URL = "https://api.openai.com/v1/organization/costs"
OPENAI_PROJECTS_URL = "https://api.openai.com/v1/organization/projects"

# Spike diagnosis. A day above SPIKE_RATIO x the window median is explained when one
# project owns at least CONCENTRATION_SHARE of the excess; RECURRING_LOOKBACK_DAYS of
# history is pulled so a same-weekday scheduled batch is recognisable as routine.
SPIKE_RATIO = 1.25
CONCENTRATION_SHARE = 0.5
RECURRING_LOOKBACK_DAYS = 35
ELEVENLABS_API = "https://api.elevenlabs.io/v1"


# ---------------------------------------------------------------------------
# Window resolution
# ---------------------------------------------------------------------------
def resolve_window(args) -> tuple[dt.date, dt.date, str]:
    """Return (from_date, to_date, label), both inclusive, UTC calendar dates."""
    today = dt.datetime.now(dt.timezone.utc).date()
    if args.from_ and args.to:
        f = dt.date.fromisoformat(args.from_)
        t = dt.date.fromisoformat(args.to)
        return f, t, f"custom {f.isoformat()} -> {t.isoformat()}"
    win = args.window
    if win == "mtd":
        return today.replace(day=1), today, f"month-to-date ({today.strftime('%B %Y')}, UTC)"
    if win == "last-7d":
        return today - dt.timedelta(days=6), today, "last 7 days (UTC)"
    if win == "last-30d":
        return today - dt.timedelta(days=29), today, "last 30 days (UTC)"
    raise SystemExit(f"unknown window: {win}")


def _unix(d: dt.date) -> int:
    return int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp())


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------
def fetch_openai_project_names() -> dict[str, str]:
    """Return {project_id: display name} so a spike can be attributed by name."""
    key = os.environ.get("OPENAI_ADMIN_API_KEY", "")
    out: dict[str, str] = {}
    after = None
    for _ in range(20):
        p = {"limit": 100, "include_archived": "true"}
        if after:
            p["after"] = after
        try:
            data = _get(OPENAI_PROJECTS_URL, {"Authorization": f"Bearer {key}"}, p).json()
        except Exception:
            return out          # names are a nicety; ids still work
        items = data.get("data", [])
        for pr in items:
            out[pr.get("id")] = pr.get("name") or pr.get("id")
        if data.get("has_more") and items:
            after = items[-1].get("id")
        else:
            break
    return out


def fetch_openai_daily(
    from_d: dt.date, to_d: dt.date
) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    """Return per-UTC-day OpenAI spend as (totals, by_project).

    totals     -> {YYYY-MM-DD: usd}
    by_project -> {YYYY-MM-DD: {project_id: usd}}

    Grouped by project in the same request the totals come from, so attributing a
    spike to a project costs no extra API calls.
    """
    key = os.environ.get("OPENAI_ADMIN_API_KEY")
    if not key:
        raise SystemExit("MISSING OPENAI_ADMIN_API_KEY (see eng:analyze-openai-costs Step 0)")
    start = _unix(from_d)
    end = _unix(to_d + dt.timedelta(days=1))  # exclusive -> includes to_d (today partial)
    params = {
        "start_time": start,
        "end_time": end,
        "bucket_width": "1d",
        "limit": 180,
        "group_by": ["project_id"],
    }
    out: dict[str, float] = {}
    per_proj: dict[str, dict[str, float]] = {}
    page = None
    for _ in range(40):
        p = dict(params)
        if page:
            p["page"] = page
        r = _get(OPENAI_COSTS_URL, {"Authorization": f"Bearer {key}"}, p)
        data = r.json()
        for bkt in data.get("data", []):
            st = bkt.get("start_time")
            if st is None:
                continue
            day = dt.datetime.fromtimestamp(st, dt.timezone.utc).date().isoformat()
            tot = 0.0
            for res in bkt.get("results", []):
                try:
                    amt = float(res.get("amount", {}).get("value", 0) or 0)
                except (TypeError, ValueError):
                    continue
                tot += amt
                pid = res.get("project_id") or "(unattributed)"
                per_proj.setdefault(day, {})
                per_proj[day][pid] = per_proj[day].get(pid, 0.0) + amt
            out[day] = out.get(day, 0.0) + tot
        if data.get("has_more") and data.get("next_page"):
            page = data["next_page"]
        else:
            break
    return out, per_proj


def _median(vals: list[float]) -> float:
    s = sorted(vals)
    if not s:
        return 0.0
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2


def diagnose_spikes(
    window_days: list[str],
    totals: dict[str, float],
    by_project: dict[str, dict[str, float]],
    names: dict[str, str],
    partial_day: str | None,
) -> list[str]:
    """Explain each in-window day whose total stands materially above the window.

    Distinguishes a RECURRING scheduled batch (one project, same weekday, repeatedly
    elevated) from an unexplained jump, so a known weekly job is reported as expected
    rather than escalated. Returns one markdown line per flagged day.
    """
    graded = [d for d in window_days if d != partial_day and d in totals]
    if len(graded) < 3:
        return []
    baseline = _median([totals[d] for d in graded])
    if baseline <= 0:
        return []

    notes: list[str] = []
    for ds in graded:
        total = totals[ds]
        if total < baseline * SPIKE_RATIO:
            continue
        excess = total - baseline
        day = dt.date.fromisoformat(ds)

        # Which project moved most against its own typical level across all history held?
        hist_days = sorted(by_project)
        best_pid, best_delta = None, 0.0
        for pid in by_project.get(ds, {}):
            others = [by_project[h].get(pid, 0.0) for h in hist_days if h != ds]
            delta = by_project[ds][pid] - _median(others)
            if delta > best_delta:
                best_pid, best_delta = pid, delta
        if not best_pid or best_delta < excess * CONCENTRATION_SHARE:
            notes.append(
                f"_⚠ **{ds} ({day.strftime('%a')})** ${total:,.2f} is "
                f"{total / baseline:.2f}× the window median (${baseline:,.2f}) and no single "
                f"project accounts for it — worth a root-cause pass "
                f"(`eng:analyze-openai-costs` / `eng:analyze-elevenlabs-costs`)._"
            )
            continue

        label = names.get(best_pid, best_pid)
        # Same weekday, other days: is this a recurring scheduled batch?
        same_dow = [
            by_project[h].get(best_pid, 0.0)
            for h in hist_days
            if h != ds and dt.date.fromisoformat(h).weekday() == day.weekday()
        ]
        off_dow = [
            by_project[h].get(best_pid, 0.0)
            for h in hist_days
            if dt.date.fromisoformat(h).weekday() != day.weekday()
        ]
        quiet = _median(off_dow)
        peers = [v for v in same_dow if v > max(quiet * 3, 1.0)]
        share = best_delta / excess * 100 if excess else 100.0
        moved = f"${by_project[ds][best_pid]:,.2f} (+${best_delta:,.2f} vs its usual)"

        if len(peers) >= 2:
            lo, hi = min(peers), max(peers)
            in_range = lo * 0.7 <= by_project[ds][best_pid] <= hi * 1.3

            # A batch's own quiet-to-batch delta is large enough to win the attribution
            # outright, which would let a concurrent jump elsewhere ride along under an
            # "expected" label. Re-apply the materiality test to what the batch does NOT
            # explain: if the day is still elevated without it, a second mover is named.
            residual_total = total - best_delta
            residual_elevated = residual_total >= baseline * SPIKE_RATIO
            second = None
            if residual_elevated:
                cand_pid, cand_delta = None, 0.0
                for pid in by_project.get(ds, {}):
                    if pid == best_pid:
                        continue
                    others = [by_project[h].get(pid, 0.0) for h in hist_days if h != ds]
                    delta = by_project[ds][pid] - _median(others)
                    if delta > cand_delta:
                        cand_pid, cand_delta = pid, delta
                if cand_pid:
                    second = (names.get(cand_pid, cand_pid), cand_delta, residual_total)

            # An elevated residual escalates on its own. Naming a second project is a
            # convenience, not the test: per-project deltas are measured against the
            # lookback median while the bar is the window median, so after a traffic
            # step-change every other project can sit at or below its own usual while
            # the day is still materially high without the batch. Gating on "a second
            # mover was found" would suppress exactly that case.
            clean = in_range and not residual_elevated
            verdict = "in range" if in_range else "**off its usual level**"
            line = (
                f"_{'' if clean else '⚠ '}**{ds} ({day.strftime('%a')})** ${total:,.2f} — "
                f"`{label}` {moved} accounts for "
                f"{share:.0f}% of the ${excess:,.2f} above the window median. Recurring "
                f"{day.strftime('%A')} batch ({len(peers)} prior "
                f"{day.strftime('%a')}s ${lo:,.2f}–${hi:,.2f}, other days ~${quiet:,.2f}); "
                f"this run is {verdict}."
            )
            if residual_elevated:
                line += (
                    f" Setting the batch aside the day is still ${residual_total:,.2f} "
                    f"({residual_total / baseline:.2f}× the median)"
                )
                if second:
                    s_label, s_delta, _ = second
                    line += f" — `{s_label}` +${s_delta:,.2f} vs its usual also moved."
                else:
                    line += (
                        ", and no other single project stands above its own usual level"
                        " — the remainder is spread across projects."
                    )
            if clean:
                line += " Expected — no root-cause pass needed._"
            else:
                reasons = []
                if not in_range:
                    reasons.append("the batch is off its usual level")
                if residual_elevated:
                    reasons.append(
                        "the day is still elevated with the batch set aside"
                    )
                line += (
                    f" Worth a root-cause pass ({' and '.join(reasons)}) — "
                    f"`eng:analyze-openai-costs`._"
                )
            notes.append(line)
        else:
            notes.append(
                f"_⚠ **{ds} ({day.strftime('%a')})** ${total:,.2f} — "
                f"`{label}` {moved} accounts for {share:.0f}% of the "
                f"${excess:,.2f} above the window median, with no recurring "
                f"{day.strftime('%A')} pattern in the history held. Worth a root-cause "
                f"pass (`eng:analyze-openai-costs`)._"
            )
    return notes


# ---------------------------------------------------------------------------
# ElevenLabs
# ---------------------------------------------------------------------------
def _el_key() -> str:
    return os.environ.get("ELEVENLABS_ADMIN_API_KEY") or os.environ.get("ELEVENLABS_API_KEY") or ""


def fetch_elevenlabs_daily(from_d: dt.date, to_d: dt.date) -> dict[str, float]:
    """Return {YYYY-MM-DD: usd} of voice consumption cost per UTC day (fiat/100)."""
    key = _el_key()
    if not key:
        raise SystemExit("MISSING ELEVENLABS_ADMIN_API_KEY (see eng:analyze-elevenlabs-costs Step 0)")
    start_ms = _unix(from_d) * 1000
    end_ms = _unix(to_d + dt.timedelta(days=1)) * 1000
    params = {
        "start_unix": start_ms,
        "end_unix": end_ms,
        "breakdown_type": "none",
        "metric": "fiat_units_spent",
        "aggregation_interval": "day",
        "include_workspace_metrics": "true",
    }
    r = _get(f"{ELEVENLABS_API}/usage/character-stats", {"xi-api-key": key}, params)
    data = r.json()
    times = data.get("time", []) or []
    usage = data.get("usage", {}) or {}
    out: dict[str, float] = {}
    for series in usage.values():  # sum across any buckets (breakdown=none -> "All")
        for t_ms, cents in zip(times, series):
            day = dt.datetime.fromtimestamp(t_ms / 1000, dt.timezone.utc).date().isoformat()
            out[day] = out.get(day, 0.0) + (float(cents or 0) / 100.0)  # cents -> USD
    return out


def fetch_elevenlabs_split_daily(from_d: dt.date, to_d: dt.date) -> dict[str, tuple[float, float]]:
    """Return {YYYY-MM-DD: (agent_usd, livekit_tts_usd)} splitting the ElevenLabs
    day total by CALLER via character-stats (ENG-982).

    ElevenLabs has three callers: the ConvAI agent bundle, the Intelligence
    Service raw-TTS path (what mobile calls), and the LiveKit agent's TTS. This
    surfaces all three alongside the single ElevenLabs figure. Reuses the store
    skill's `elevenlabs_caller_split` so the mapping stays in one place.
    Degrades to (day_total_as_agent, 0, 0) if the store helper is unavailable —
    the pre-split world was 100% ConvAI agent.
    """
    out: dict[str, tuple[float, float, float]] = {}
    if _store is None:
        return out
    d = from_d
    while d <= to_d:
        ds = d.isoformat()
        try:
            agent, intel, lk, _note = _store.elevenlabs_caller_split(ds, _el_key())
            out[ds] = (agent, intel, lk)
        except Exception as e:  # noqa: BLE001 — never let the split break the report.
            # An exhausted retry budget means the endpoint is down, not this day:
            # stop rather than wait the budget out again for every remaining day.
            if isinstance(e, getattr(_store, "ElevenLabsUnavailable", ())):
                print(f"# 11labs caller split skipped from {ds}: {e}", file=sys.stderr)
                break
        d += dt.timedelta(days=1)
    return out


def fetch_elevenlabs_subscription() -> dict | None:
    key = _el_key()
    if not key:
        return None
    try:
        r = requests.get(
            f"{ELEVENLABS_API}/user/subscription", headers={"xi-api-key": key}, timeout=60
        )
        if r.status_code == 200:
            return r.json()
    except requests.RequestException:
        return None
    return None


# ---------------------------------------------------------------------------
# HTTP helper (retry on 5xx / 429 / transport errors)
# ---------------------------------------------------------------------------
# ElevenLabs usage endpoints have flapped with 500s for minutes at a time, so the
# budget rides out a multi-minute outage; a non-429 4xx still fails at once.
RETRY_ATTEMPTS = 10
RETRY_BASE_S = 2.0
RETRY_CAP_S = 60.0
RETRY_BUDGET_S = 240.0


class RetryExhausted(requests.RequestException):
    """A retryable failure outlasted the budget. A RequestException (like the HTTPError
    this used to surface as), so callers that treat a lookup as optional still can."""


def _get(url: str, headers: dict, params: dict) -> requests.Response:
    t0, waited, last = time.monotonic(), 0.0, "no response"
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        retry_after = None
        try:
            r = requests.get(url, headers=headers, params=params, timeout=60)
        except (requests.ConnectionError, requests.Timeout) as e:
            last = f"{type(e).__name__}: {e}"
        else:
            if r.status_code < 500 and r.status_code != 429:
                r.raise_for_status()
                return r
            last = f"HTTP {r.status_code}"
            if r.status_code == 429:
                try:
                    retry_after = float(r.headers.get("Retry-After"))
                except (TypeError, ValueError):
                    retry_after = None
        if attempt == RETRY_ATTEMPTS:
            break
        remaining = RETRY_BUDGET_S - max(time.monotonic() - t0, waited)
        if remaining <= 0:
            break
        if retry_after is not None:
            delay = min(max(retry_after, 0.0), RETRY_CAP_S)
        else:
            d = min(RETRY_BASE_S * 2 ** (attempt - 1), RETRY_CAP_S)
            delay = d / 2 + random.uniform(0, d / 2)
        delay = min(delay, remaining)
        print(f"# GET {url.split('?')[0]}: {last} (attempt {attempt}/{RETRY_ATTEMPTS}), "
              f"retrying in {delay:.1f}s", file=sys.stderr)
        time.sleep(delay)
        waited += delay
    raise RetryExhausted(f"GET {url.split('?')[0]}: {last} after {attempt} attempt(s) over "
                         f"{max(time.monotonic() - t0, waited):.0f}s")


# ---------------------------------------------------------------------------
# LiveKit-path vendors (Cartesia / Deepgram / LiveKit) — daily workspace totals
# ---------------------------------------------------------------------------
# Per-day vendor totals are (value, note) pairs from the store helpers. Those
# helpers return a note ONLY on a real read failure — a quiet day with genuinely
# no usage comes back as ($0, "") — so the note is the signal that a $0 is a
# placeholder rather than a measurement. UNKNOWN is what this report carries for
# such a day, and it renders as "—", never as $0.00 (ENG-1214).
UNKNOWN = None


def _bq_livekit_minutes(
    from_d: dt.date, to_d: dt.date, project: str, dataset: str
) -> tuple[dict[str, float], str]:
    """Return ({day: session-minutes}, note) recovered from the daily cost store.

    One query covers the whole window, replacing one Cloud Logging walk per day.
    That matters for more than speed: a day of forwarded LiveKit logs is ~21k
    entries ≈ 20 read requests, against a 60/min project cap, so a per-day walk
    over a week exhausted the quota partway through and the remaining days used to
    render as $0.00 (ENG-1214).

    Minutes are recovered from the store's own `voice_livekit` — which `store.py`
    computes as `total_session_minutes × livekit_rate_per_min` — by dividing that
    rate back out. Summing every row of the day (the reconciling `Other` row
    included) is what makes the total whole: when a day has no minutes to allocate
    on, the vendor's whole amount lands on `Other`.

    Deliberately does NOT join `aurie_daily_unit_costs`, even though
    eng:report-livekit-daily-cost recovers minutes that way. Two reasons, both
    measured: that table carries a rate for 41 of 91 stored days, so the join sent
    50 stored days — 47 of them genuinely quiet — to a live log walk, which is the
    quota pressure this function exists to remove; and its rate is a BLENDED
    (cartesia + deepgram + livekit) / minutes figure rounded to 4 dp, so inverting
    it lands within ~0.2% where dividing out the LiveKit rate lands within 0.001%.

    A day absent from the result is a day the store does not hold, and the caller
    walks the logs for it. A day present with `voice_livekit` 0 is a recorded quiet
    day and stays $0 — never promoted to an unread `—`.
    """
    if _bq is None:
        return {}, f"livekit_store_unavailable: bq helper not importable ({_BQ_IMPORT_ERROR})"
    # The rate store.py applied when it wrote the rows. Read from the store module
    # so the two cannot drift apart silently; the report's own --livekit-rate-per-min
    # default comes from the same constant.
    store_rate = getattr(_store, "DEFAULT_LIVEKIT_RATE_PER_MIN", 0.01) if _store else 0.01
    if not store_rate:
        return {}, "livekit_store_unavailable: store LiveKit rate is zero — minutes unrecoverable"
    per_user = f"{project}.{dataset}.aurie_per_user_daily_cost"
    # The BASE table, not the env-filtered _prod view: this report's totals
    # reconcile to the authoritative daily vendor bill, same rationale as
    # eng:chart-aurie-daily-cost and eng:report-livekit-daily-cost.
    # PROD-5998: minutes are STORED now (`livekit_minutes`), so the division is a
    # transition fallback rather than the mechanism. It has to stay for a while: rows
    # written before that card carry 0 minutes and a `voice_livekit` that was still
    # minutes x rate, and `rebook_livekit.py` only restates days it has been run over.
    # Once every stored day carries minutes the fallback becomes dead code — but
    # removing it early turns an un-restated day into a silent zero, which is the exact
    # failure class this whole change is removing.
    sql = f"""
    SELECT CAST(date AS STRING) AS date,
           SUM(COALESCE(livekit_minutes, 0)) AS livekit_minutes,
           SUM(COALESCE(voice_livekit, 0)) AS livekit_usd,
           SUM(COALESCE(voice_livekit_platform, 0)) AS platform_usd
    FROM `{per_user}`
    WHERE date BETWEEN @from_d AND @to_d
    GROUP BY date
    """
    try:
        rows = _bq.query(sql, project,
                         params={"from_d": from_d.isoformat(), "to_d": to_d.isoformat()})
    except Exception as ex:  # noqa: BLE001 — BigQuery is an optimisation, not a requirement.
        return {}, f"livekit_store_unavailable: {type(ex).__name__}: {ex}"
    out = {}
    for r in rows:
        stored = float(r.get("livekit_minutes") or 0.0)
        # Prefer the stored count; fall back to the division only for a day that
        # predates PROD-5998 and has not been restated yet.
        minutes = stored if stored > 0 else float(r.get("livekit_usd") or 0.0) / store_rate
        out[r["date"]] = {
            "minutes": minutes,
            # The dollars actually BOOKED for the day: overage + the subscription
            # accrual. Re-deriving these as minutes x rate would price a stored day on
            # the retired rate-card model while the live fallback below prices today
            # from the invoice — the same window reporting LiveKit on two scales, with
            # today looking several times more expensive than a comparable stored day.
            "usd": float(r.get("livekit_usd") or 0.0) + float(r.get("platform_usd") or 0.0),
        }
    return out, ""


def _bq_deepgram_days(
    from_d: dt.date, to_d: dt.date, project: str, dataset: str
) -> tuple[dict[str, dict], str]:
    """Return ({day: {usd, drawdown, traffic}}, note) of the Deepgram the store BOOKED.

    Deepgram's own usage API only sees speech sent to it DIRECTLY. Since the LiveKit
    agent's STT moved to LiveKit Inference, that API answers a well-formed $0 for a day
    whose STT all went through Inference, and the only place the day's real Deepgram
    cost exists is the store: `voice_deepgram` is the overage past the plan's included
    Inference credit block (plus any direct Deepgram usage on a mixed day), and
    `deepgram_inference_drawdown` is how much of the block the day drew. On 2026-09-29
    the store booked $10.40 while this report, reading the vendor API, printed $0.00 —
    and the Voice and Combined totals were short by the same amount.

    A separate query from the LiveKit one on purpose: that query's numerator must stay
    `voice_livekit` alone (see `_bq_livekit_minutes`), and a failure here should cost
    only the Deepgram column its store read, not LiveKit's.

    The range reaches back to the 1st of `from_d`'s month so the month-to-date drawdown
    before any unstored day in the window is available without a second query — the
    credit block is monthly, so whether an unstored day's Inference STT is billable
    depends on how full the block already is.

    `traffic` is whether the day had LiveKit minutes. It is what separates a stored $0
    that means "inside the block" or "quiet day" from one that means "the store could
    not measure STT" — the note the store printed for that case is not persisted.
    """
    if _bq is None:
        return {}, f"deepgram_store_unavailable: bq helper not importable ({_BQ_IMPORT_ERROR})"
    per_user = f"{project}.{dataset}.aurie_per_user_daily_cost"
    sql = f"""
    SELECT CAST(date AS STRING) AS date,
           SUM(COALESCE(voice_deepgram, 0)) AS deepgram_usd,
           SUM(COALESCE(deepgram_inference_drawdown, 0)) AS drawdown_usd,
           SUM(COALESCE(livekit_minutes, 0)) AS livekit_minutes,
           SUM(COALESCE(voice_livekit, 0)) AS livekit_usd
    FROM `{per_user}`
    WHERE date BETWEEN @from_d AND @to_d
    GROUP BY date
    """
    try:
        rows = _bq.query(sql, project, params={
            "from_d": from_d.replace(day=1).isoformat(), "to_d": to_d.isoformat()})
    except Exception as ex:  # noqa: BLE001 — degrade to the unstored-day path.
        return {}, f"deepgram_store_unavailable: {type(ex).__name__}: {ex}"
    out = {}
    for r in rows:
        out[r["date"]] = {
            "usd": float(r.get("deepgram_usd") or 0.0),
            "drawdown": float(r.get("drawdown_usd") or 0.0),
            # voice_livekit is minutes x the credit rate, so it is > 0 exactly when the
            # day had minutes — covers rows written before livekit_minutes was stored.
            "traffic": float(r.get("livekit_minutes") or 0.0) > 0
                       or float(r.get("livekit_usd") or 0.0) > 0,
        }
    return out, ""


def _stored_deepgram(ds: str, row: dict) -> tuple[float | None, str]:
    """Return (value or UNKNOWN, note) for a day the store holds.

    A booked $0 is a measurement when the day drew on the credit block (inside the
    block, billed $0 by design — the subscription it belongs to is already in the
    LiveKit column) or had no LiveKit traffic at all. A booked $0 on a day WITH traffic
    and no drawdown is the shape the store tags `deepgram_transport_unknown` /
    `deepgram_pending` / unmeasured Inference at write time; those notes are not
    persisted, so it is UNKNOWN here rather than a quiet day.
    """
    if row["usd"] > 0 or row["drawdown"] > 0 or not row["traffic"]:
        return row["usd"], ""
    return UNKNOWN, (f"deepgram_store_unexplained_zero: {ds} is stored with $0 Deepgram, "
                     f"LiveKit traffic and no Inference drawdown — the STT read failed or "
                     f"went unmeasured when the day was stored")


def _live_deepgram(ds: str, rate_per_hour: float, meta: dict | None, lk_minutes: float,
                   plan: str, mtd_draw_before: float | None) -> tuple[float | None, float, str]:
    """Return (value or UNKNOWN, drawdown, note) for a day the store does NOT hold.

    Mirrors how `store.py` books the day, through the store's own helpers: the STT
    transport split from the LiveKit walk decides whether Deepgram's usage API is the
    authoritative source at all. Inference STT is priced as overage past the credit
    block given the month-to-date drawdown; direct STT is read from Deepgram. A day
    whose transport could not be read, or whose Inference STT could not be priced, is
    UNKNOWN — never the well-formed $0 the Deepgram API returns for Inference traffic.
    """
    if meta is None:
        # No LiveKit walk for this day (it failed, or the store held LiveKit but not
        # Deepgram), so nothing says which transport carried the speech.
        direct, note = _store.deepgram_day_total(ds, rate_per_hour)
        if note:
            return UNKNOWN, 0.0, note
        if direct > 0:
            return direct, 0.0, ("deepgram_transport_unread: direct Deepgram usage shown; "
                                 "any LiveKit Inference overage on those days is not included")
        return UNKNOWN, 0.0, ("deepgram_transport_unread: Deepgram's API read $0, but the "
                              "LiveKit walk that says whether STT ran on LiveKit Inference "
                              "(which that API cannot see) was not available")
    on_inf, inf_min, direct_min, by_transport = _store.stt_transport_split(meta)
    has_inf = on_inf or inf_min > 0
    billed, draw, note = 0.0, 0.0, ""
    if has_inf:
        measured = meta.get("stt_sessions_measured", 0)
        if not measured:
            return UNKNOWN, 0.0, ("deepgram_inference_unmeasured: STT ran on LiveKit Inference "
                                  "but no session logged an STT duration — drawdown unknown")
        if mtd_draw_before is None:
            return UNKNOWN, 0.0, ("deepgram_inference_unpriced: month-to-date credit drawdown "
                                  "not readable from the store — cannot tell whether the "
                                  "Inference STT is inside the included block")
        billed, draw, note = _store.deepgram_inference_day(plan, inf_min, mtd_draw_before)
        if note:
            return UNKNOWN, 0.0, note
    if has_inf and direct_min <= 0:
        value = billed
    else:
        direct, dnote = _store.deepgram_day_total(ds, rate_per_hour)
        if dnote:
            return UNKNOWN, draw, dnote
        value = direct + billed
    zero = _store.unexplained_deepgram_zero(ds, value, lk_minutes, "inference" if has_inf else "",
                                            by_transport)
    if zero:
        return UNKNOWN, draw, zero
    return value, draw, ""


def fetch_livekit_path_daily(
    from_d: dt.date, to_d: dt.date, rates: dict, *,
    bq_project: str, bq_dataset: str, use_store: bool = True, livekit_plan: str | None = None,
) -> tuple[dict, list[str]]:
    """Return ({day: {cartesia, inworld, deepgram, livekit}}, notes) per UTC day.

    Each vendor value is a float, or UNKNOWN when that day's read failed. Cartesia
    and Deepgram come from the sibling store skill's daily-workspace-total helpers
    (one cheap vendor API call per day). LiveKit session-minutes come from the
    BigQuery cost store for every day it has, and from a live Cloud Logging walk
    only for the days it doesn't — normally just the current partial day.

    Deepgram, like LiveKit, comes from the store for every day it holds — the dollars
    it BOOKED, which include LiveKit Inference overage the Deepgram API cannot see.
    Unstored days are priced the way the store would price them (`_live_deepgram`).
    """
    out: dict[str, dict] = {}
    notes: set[str] = set()
    days = []
    d = from_d
    while d <= to_d:
        days.append(d.isoformat())
        d += dt.timedelta(days=1)

    if _store is None:
        # Nothing to read any vendor with. UNKNOWN across the board — an
        # unreadable vendor is not a $0 vendor.
        notes.add("livekit_path_pending: store helpers unavailable — "
                  "Cartesia/Inworld/Deepgram/LiveKit not reported")
        return ({ds: {"cartesia": UNKNOWN, "inworld": UNKNOWN, "deepgram": UNKNOWN,
                      "livekit": UNKNOWN}
                 for ds in days}, sorted(notes))

    stored_minutes: dict[str, dict] = {}
    stored_dg: dict[str, dict] = {}
    dg_store_ok = False
    if use_store:
        stored_minutes, store_note = _bq_livekit_minutes(
            from_d, to_d, bq_project, bq_dataset)
        if store_note:
            notes.add(store_note + " — falling back to the live log walk per day")
        stored_dg, dg_store_note = _bq_deepgram_days(from_d, to_d, bq_project, bq_dataset)
        dg_store_ok = not dg_store_note
        if dg_store_note:
            notes.add(dg_store_note + " — unstored-day pricing for every day")

    plan = livekit_plan or getattr(getattr(_store, "livekit_plan", None), "DEFAULT_PLAN", "scale")
    # Inference drawdown by day: stored days from the store, unstored days as this run
    # prices them — so a second unstored day in a month sees the first one's draw.
    draws = {k: v["drawdown"] for k, v in stored_dg.items()}
    dg_from_store = inside_block = 0

    for ds in days:
        cartesia, ct_note = _store.cartesia_day_total(ds, rates["cartesia"])
        meta, lk_minutes = None, 0.0
        if ds in stored_minutes:
            livekit, lk_note = stored_minutes[ds]["usd"], ""
        else:
            _by_user, lk_minutes, livekit, lk_note, meta = _store.livekit_costs(
                ds, rates["livekit"])
            if lk_note:
                meta = None
        if ds in stored_dg:
            deepgram, dg_note = _stored_deepgram(ds, stored_dg[ds])
            dg_from_store += 1
            if deepgram == 0 and stored_dg[ds]["drawdown"] > 0:
                inside_block += 1
        else:
            # The month-to-date drawdown is known only when the store read succeeded;
            # without it an Inference day cannot be placed against the block.
            mtd = None
            if dg_store_ok:
                month = ds[:7]
                mtd = sum(v for k, v in draws.items() if k[:7] == month and k < ds)
            deepgram, draw, dg_note = _live_deepgram(
                ds, rates["deepgram"], meta, lk_minutes, plan, mtd)
            draws[ds] = draw
        # Inworld's usage API is the meter; the agent logs are only a fallback, and only
        # a day this run walked has them. A derived figure is still a figure (noted); a
        # pending one is unread.
        inworld, iw_note = _store.inworld_day_total(
            ds, meta or {}, rates.get("inworld", _store.DEFAULT_INWORLD_RATE_PER_MILLION))
        out[ds] = {
            "cartesia": UNKNOWN if ct_note else cartesia,
            "inworld": UNKNOWN if iw_note.startswith("inworld_pending") else inworld,
            # A Deepgram note is set only alongside UNKNOWN, except the informational
            # transport-unread note that accompanies a real direct read.
            "deepgram": deepgram,
            "livekit": UNKNOWN if lk_note else livekit,
        }
        for n in (ct_note, iw_note, dg_note, lk_note):
            if n:
                notes.add(n)
    if dg_from_store:
        notes.add(
            f"deepgram_source: {dg_from_store} of {len(days)} day(s) read from the "
            f"BigQuery cost store (the dollars BOOKED — LiveKit Inference overage past the "
            f"included credit block, plus any direct Deepgram usage); "
            f"--deepgram-rate-per-hour applies only to the unstored days' direct reads"
            + (f". {inside_block} day(s) drew on the Inference credit block and stayed "
               f"inside it: $0.00 billed, because the block is part of the LiveKit "
               f"subscription already in the LiveKit column" if inside_block else "")
        )
    if stored_minutes:
        notes.add(
            f"livekit_source: {len(stored_minutes)} of {len(days)} day(s) read from "
            f"the BigQuery cost store (the dollars BOOKED for the day — subscription "
            f"accrual + overage); the rest walked the forwarded GCP logs live and are "
            f"priced on the same basis. --livekit-rate-per-min sets the OVERAGE rate "
            f"only; it does not price minutes inside the plan allotment"
        )
    return out, sorted(notes)


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------
def _known(values: list) -> tuple[float, int]:
    """Return (sum of the known values, count of UNKNOWN ones)."""
    known = [v for v in values if v is not UNKNOWN]
    return sum(known), len(values) - len(known)


def _r2(v):
    """Round a vendor value, preserving UNKNOWN."""
    return UNKNOWN if v is UNKNOWN else round(v, 2)


def build(from_d: dt.date, to_d: dt.date, label: str, rates: dict, *,
         bq_project: str, bq_dataset: str, use_store: bool = True) -> dict:
    # Reach back beyond the window so a same-weekday scheduled batch is recognisable
    # as recurring rather than novel. Only the requested window is tabulated.
    hist_from = min(from_d, to_d - dt.timedelta(days=RECURRING_LOOKBACK_DAYS))
    oai, oai_by_project = fetch_openai_daily(hist_from, to_d)
    el = fetch_elevenlabs_daily(from_d, to_d)
    el_split = fetch_elevenlabs_split_daily(from_d, to_d)
    lkp, vendor_notes = fetch_livekit_path_daily(
        from_d, to_d, rates,
        bq_project=bq_project, bq_dataset=bq_dataset, use_store=use_store)
    sub = fetch_elevenlabs_subscription()
    today = dt.datetime.now(dt.timezone.utc).date()
    project_names = fetch_openai_project_names()

    rows = []
    d = from_d
    while d <= to_d:
        ds = d.isoformat()
        o = oai.get(ds, 0.0)
        e = el.get(ds, 0.0)
        # ElevenLabs by CALLER: ConvAI agent / Intelligence Service raw TTS / LiveKit
        # TTS (falls back to all-agent when the split is unavailable — the pre-split
        # world was 100% ConvAI agent).
        agent, intel, tts = el_split.get(ds, (e, 0.0, 0.0))
        v = {"cartesia": UNKNOWN, "inworld": UNKNOWN, "deepgram": UNKNOWN,
             "livekit": UNKNOWN, **lkp.get(ds, {})}
        # Voice $ sums only the vendors that were actually read. When one wasn't,
        # the sum is a LOWER BOUND, not the total — `voice_incomplete` says so, and
        # the renderer marks it. Treating an unread vendor as $0 here is the exact
        # understatement ENG-1214 removes.
        voice, missing = _known([e, v["cartesia"], v["inworld"], v["deepgram"], v["livekit"]])
        rows.append({
            "date": ds,
            "weekday": d.strftime("%a"),
            "openai_usd": round(o, 2),
            "elevenlabs_usd": round(e, 2),
            "elevenlabs_agent_usd": round(agent, 2),
            "elevenlabs_intelligence_usd": round(intel, 2),
            "elevenlabs_livekit_usd": round(tts, 2),
            "cartesia_usd": _r2(v["cartesia"]),
            "inworld_usd": _r2(v["inworld"]),
            "deepgram_usd": _r2(v["deepgram"]),
            "livekit_usd": _r2(v["livekit"]),
            "voice_usd": round(voice, 2),
            "combined_usd": round(o + voice, 2),
            "voice_incomplete": missing > 0,
            "unread_vendors": sorted(
                k for k in ("cartesia", "inworld", "deepgram", "livekit") if v[k] is UNKNOWN),
            "partial": d == today,
        })
        d += dt.timedelta(days=1)

    spike_notes = diagnose_spikes(
        [r["date"] for r in rows], oai, oai_by_project, project_names,
        next((r["date"] for r in rows if r["partial"]), None),
    )

    tot_o = sum(r["openai_usd"] for r in rows)
    tot_e = sum(r["elevenlabs_usd"] for r in rows)
    tot_e_agent = sum(r["elevenlabs_agent_usd"] for r in rows)
    tot_e_intel = sum(r["elevenlabs_intelligence_usd"] for r in rows)
    tot_e_lk = sum(r["elevenlabs_livekit_usd"] for r in rows)
    # Column totals skip the unread days and report how many they skipped, so a
    # total is never quietly short by a failed read.
    tot_ct, miss_ct = _known([r["cartesia_usd"] for r in rows])
    tot_iw, miss_iw = _known([r["inworld_usd"] for r in rows])
    tot_dg, miss_dg = _known([r["deepgram_usd"] for r in rows])
    tot_lk, miss_lk = _known([r["livekit_usd"] for r in rows])
    tot_voice = sum(r["voice_usd"] for r in rows)
    unread_days = {"cartesia": miss_ct, "inworld": miss_iw, "deepgram": miss_dg,
                   "livekit": miss_lk}
    any_incomplete = any(r["voice_incomplete"] for r in rows)
    return {
        "label": label,
        "from": from_d.isoformat(),
        "to": to_d.isoformat(),
        "rows": rows,
        "total_openai_usd": round(tot_o, 2),
        "total_elevenlabs_usd": round(tot_e, 2),
        # ElevenLabs split by role (ConvAI agent vs LiveKit-path TTS).
        "total_elevenlabs_agent_usd": round(tot_e_agent, 2),
        "total_elevenlabs_intelligence_usd": round(tot_e_intel, 2),
        "total_elevenlabs_livekit_usd": round(tot_e_lk, 2),
        "total_cartesia_usd": round(tot_ct, 2),
        "total_inworld_usd": round(tot_iw, 2),
        "total_deepgram_usd": round(tot_dg, 2),
        "total_livekit_usd": round(tot_lk, 2),
        "total_voice_usd": round(tot_voice, 2),
        "total_combined_usd": round(tot_o + tot_voice, 2),
        # A consumer must be able to tell a complete total from a floor without
        # parsing the rendered table.
        "voice_incomplete": any_incomplete,
        "unread_vendor_days": unread_days,
        "vendor_rates": rates,
        "vendor_notes": vendor_notes,
        "spike_notes": spike_notes,
        "elevenlabs_subscription": _sub_summary(sub),
    }


def _num(v, cast=float):
    """Coerce a possibly-string/None API value to a number (None on failure)."""
    if v is None:
        return None
    try:
        return cast(v)
    except (TypeError, ValueError):
        return None


def _sub_summary(sub: dict | None) -> dict | None:
    if not sub:
        return None
    inv = sub.get("next_invoice") or {}
    overage = sub.get("current_overage") or {}
    return {
        "tier": sub.get("tier"),
        "plan_monthly_usd": round((_num(inv.get("amount_due_cents")) or 0) / 100.0, 2),
        "current_overage_usd": _num(overage.get("amount")),
        "credits_used": _num(sub.get("character_count"), int),
        "credits_limit": _num(sub.get("character_limit"), int),
    }


# What an unread vendor cell renders as. Deliberately NOT "$0.00": a $0 LiveKit
# or Deepgram day is legitimate and common (both are LiveKit-path-only), so a
# placeholder zero is indistinguishable from a measurement and reads as a quiet
# day rather than a failed read (ENG-1214).
UNREAD_CELL = "—"


def _usd(v, *, bold: bool = False) -> str:
    """Render a vendor value as USD, or as UNREAD_CELL when it is UNKNOWN."""
    if v is UNKNOWN:
        return f"**{UNREAD_CELL}**" if bold else UNREAD_CELL
    return f"**${v:,.2f}**" if bold else f"${v:,.2f}"


def _usd_total(v, unread_days: int) -> str:
    """Render a vendor column total, marked >= when some of its days went unread."""
    if unread_days:
        return f"**\u2265 ${v:,.2f}**"
    return f"**${v:,.2f}**"


def _bound(v, incomplete: bool, *, bold: bool = False) -> str:
    """Render a total, prefixed with >= when a component was never read.

    A floor is still useful — the components that WERE read are real — so this
    keeps the number and qualifies it, rather than blanking the cell.
    """
    body = f"\u2265 ${v:,.2f}" if incomplete else f"${v:,.2f}"
    return f"**{body}**" if bold else body


def render_md(payload: dict) -> str:
    L = [f"# Daily AI cost — {payload['label']}", ""]
    # Voice split: ElevenLabs (actual) + Cartesia/Inworld/Deepgram/LiveKit (LiveKit path,
    # DERIVED from ESTIMATE rates). Voice $ = sum of the five vendor columns that
    # were read; "—" marks a vendor-day that could not be read, and a ">=" total
    # is a lower bound because one of its components is missing.
    L.append("| Day | OpenAI $ | 11Labs $ | Cartesia $ | Inworld $ | Deepgram $ | LiveKit $ "
             "| Voice $ | Combined $ |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in payload["rows"]:
        day = f"{r['date']} ({r['weekday']})" + (" *(partial)*" if r["partial"] else "")
        inc = r.get("voice_incomplete", False)
        L.append(
            f"| {day} | ${r['openai_usd']:,.2f} | ${r['elevenlabs_usd']:,.2f} | "
            f"{_usd(r['cartesia_usd'])} | {_usd(r.get('inworld_usd', 0.0))} | "
            f"{_usd(r['deepgram_usd'])} | {_usd(r['livekit_usd'])} | "
            f"{_bound(r['voice_usd'], inc)} | {_bound(r['combined_usd'], inc)} |"
        )
    tot_inc = payload.get("voice_incomplete", False)
    unread = payload.get("unread_vendor_days") or {}
    L.append(
        f"| **Total** | **${payload['total_openai_usd']:,.2f}** | "
        f"**${payload['total_elevenlabs_usd']:,.2f}** | "
        f"{_usd_total(payload['total_cartesia_usd'], unread.get('cartesia', 0))} | "
        f"{_usd_total(payload.get('total_inworld_usd', 0.0), unread.get('inworld', 0))} | "
        f"{_usd_total(payload['total_deepgram_usd'], unread.get('deepgram', 0))} | "
        f"{_usd_total(payload['total_livekit_usd'], unread.get('livekit', 0))} | "
        f"{_bound(payload['total_voice_usd'], tot_inc, bold=True)} | "
        f"{_bound(payload['total_combined_usd'], tot_inc, bold=True)} |"
    )
    if tot_inc:
        missing = ", ".join(f"{k} ({n} day{'s' if n != 1 else ''})"
                            for k, n in sorted(unread.items()) if n)
        L.append("")
        L.append(
            f"> **Incomplete voice total.** Could not read: {missing}. Those "
            f"vendor-days show `{UNREAD_CELL}` and are excluded from their column "
            f"total, so the Voice and Combined figures marked `\u2265` are lower "
            f"bounds, not totals. See the notes below for why each read failed. "
            f"A `{UNREAD_CELL}` is NOT a $0 day."
        )
    # ElevenLabs split by CALLER (ENG-982) — additive detail under the single
    # 11Labs column, since three different Aura systems call ElevenLabs.
    e_agent = payload.get("total_elevenlabs_agent_usd")
    e_intel = payload.get("total_elevenlabs_intelligence_usd") or 0.0
    e_lk = payload.get("total_elevenlabs_livekit_usd")
    if e_agent is not None and (e_agent or e_intel or e_lk):
        L.append("")
        L.append(
            f"_ElevenLabs split by caller: ConvAI agent **${e_agent:,.2f}** + "
            f"Intelligence Service TTS **${e_intel:,.2f}** + LiveKit TTS "
            f"**${e_lk:,.2f}** (= 11Labs column total). Intelligence Service is the "
            f"raw-TTS path mobile calls; LiveKit TTS is one of several providers the "
            f"LiveKit agent selects between, so it can be $0 or the whole LiveKit "
            f"TTS bill depending on the current selection._"
        )
    rates = payload.get("vendor_rates") or {}
    if rates:
        L.append("")
        L.append(
            f"_Voice vendor rates (ESTIMATES from pricing sheet — replace with contract rates): "
            f"Cartesia ${rates.get('cartesia', 0):.2f}/M credits, "
            f"Inworld ${rates.get('inworld', 0):.2f}/M chars, "
            f"Deepgram ${rates.get('deepgram', 0):.3f}/hr, "
            f"LiveKit ${rates.get('livekit', 0):.3f}/min. Cartesia/Inworld/Deepgram/LiveKit $ are DERIVED; "
            f"ElevenLabs $ is the actual billed value._"
        )
    for n in payload.get("vendor_notes") or []:
        L.append(f"_note: {n}_")
    for n in payload.get("spike_notes") or []:
        L.append("")
        L.append(n)
    sub = payload.get("elevenlabs_subscription")
    if sub:
        L.append("")
        line = (
            f"**ElevenLabs invoice context:** `{sub.get('tier')}` plan "
            f"${sub.get('plan_monthly_usd'):,.2f}/mo"
        )
        ov = sub.get("current_overage_usd")
        if ov:
            line += f" + current overage ${ov:,.2f}"
        used, lim = sub.get("credits_used"), sub.get("credits_limit")
        if used and lim:
            line += f" · quota {used / lim * 100:.0f}% used"
        L.append(line)
        L.append(
            "_ElevenLabs $ above is voice **consumption value** (fiat_units_spent ÷100), "
            "not the invoice. The actual bill is flat plan + overage._"
        )
    L.append("")
    L.append(
        "_OpenAI is usage-based (daily $ = what's owed); the latest day is partial "
        "(~24h Costs API lag). Both axes bucketed UTC. Voice is multi-vendor "
        "(ElevenLabs + Cartesia + Inworld + Deepgram + LiveKit); LiveKit-path $ is DERIVED "
        "from ESTIMATE rates. Generated via `eng:report-aurie-daily-costs`._"
    )
    return "\n".join(L)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Daily OpenAI + multi-vendor Voice cost table.")
    ap.add_argument("--window", choices=["mtd", "last-7d", "last-30d"], default="mtd")
    ap.add_argument("--from", dest="from_", help="custom start YYYY-MM-DD (inclusive)")
    ap.add_argument("--to", dest="to", help="custom end YYYY-MM-DD (inclusive)")
    ap.add_argument("--cartesia-rate-per-million", type=float,
                    default=(getattr(_store, "DEFAULT_CARTESIA_RATE_PER_MILLION", 38.00)),
                    help="ESTIMATE — USD per 1M Cartesia credits (default 38.00, Scale overage rate).")
    ap.add_argument("--inworld-rate-per-million", type=float,
                    default=(getattr(_store, "DEFAULT_INWORLD_RATE_PER_MILLION", 25.00)),
                    help="USD per 1M Inworld TTS characters (default 25.00, TTS-2 On-Demand list).")
    ap.add_argument("--deepgram-rate-per-hour", type=float,
                    default=(getattr(_store, "DEFAULT_DEEPGRAM_RATE_PER_HOUR", 0.348)),
                    help="USD per Deepgram audio-hour (default 0.348, Nova-3 Multilingual streaming PAYG).")
    ap.add_argument("--livekit-rate-per-min", type=float,
                    default=(getattr(_store, "DEFAULT_LIVEKIT_RATE_PER_MIN", 0.01)),
                    help="ESTIMATE — USD per LiveKit session-minute (default 0.01, LiveKit Cloud Scale).")
    ap.add_argument("--json", dest="json_path", help="also write structured JSON here")
    ap.add_argument("--bq-project", default=getattr(_bq, "PROJECT", "aura-main"),
                    help="BigQuery project holding the daily cost store (default aura-main).")
    ap.add_argument("--bq-dataset", default=getattr(_bq, "DATASET", "aurie_cost"),
                    help="BigQuery dataset holding the daily cost store (default aurie_cost).")
    ap.add_argument("--no-bigquery", action="store_true",
                    help="Skip the BigQuery cost store and walk the forwarded GCP logs for "
                         "every day. Re-derives LiveKit minutes from scratch, at ~20 Cloud "
                         "Logging read requests per day against a 60/min project cap — so a "
                         "window beyond ~2 days will be paced slowly or degrade to '—' days.")
    args = ap.parse_args()

    if bool(args.from_) ^ bool(args.to):
        raise SystemExit("--from and --to must be given together")

    rates = {"cartesia": args.cartesia_rate_per_million,
             "inworld": args.inworld_rate_per_million,
             "deepgram": args.deepgram_rate_per_hour,
             "livekit": args.livekit_rate_per_min}
    from_d, to_d, label = resolve_window(args)
    payload = build(from_d, to_d, label, rates,
                    bq_project=args.bq_project, bq_dataset=args.bq_dataset,
                    use_store=not args.no_bigquery)
    print(render_md(payload))
    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)


if __name__ == "__main__":
    main()
