#!/usr/bin/env python3
"""
Store one day of per-user Aurie cost (LLM + voice) to a maintained CSV.

Row schema:
  date, user_id, llm_cost, voice_11labs_agent, voice_11labs_livekit,
  voice_cartesia, voice_deepgram, voice_livekit, voice_cost, total_cost,
  subscription_state

`voice_cost` is the sum of the FIVE per-vendor voice columns and is kept so
downstream consumers that only read a single voice figure keep working.

Voice cost is MULTI-VENDOR, and ElevenLabs is used in TWO distinct roles:
  (a) the ConvAI AGENT bundle (the legacy ElevenLabs voice path), and
  (b) as a selectable TTS provider on the LiveKit path (the LiveKit agent is
      multi-TTS: Cartesia / ElevenLabs / OpenAI / Inworld, chosen per session).
ElevenLabs is ALSO called directly by the Intelligence Service for raw TTS (what
mobile calls) — a THIRD caller. `product_type` cannot tell the two raw-TTS callers
apart, so the daily total is split by CALLER instead (ENG-982): has_api_key=False
is the ConvAI agent, and the API-called remainder is split per calling key into
voice_11labs_intelligence and voice_11labs_livekit. Canonical write-up:
`Tools/ElevenLabs` in AI Knowledge → "Attributing spend to the calling service".

As voice traffic shifts from the ElevenLabs ConvAI path to the LiveKit path
(Deepgram STT + a per-session TTS provider + LiveKit transport), a user's total
voice cost is:

    voice = 11labs_agent$    (their ConvAI sessions)
          + 11labs_livekit$  (their share of the day's ElevenLabs-TTS $, among
                              the LiveKit sessions that used ElevenLabs TTS)
          + Cartesia$        (their share of the day's Cartesia $, among the
                              LiveKit sessions that used Cartesia TTS)
          + Fish$ / Inworld$ (the same shape, among their own TTS sessions)
          + Deepgram$        (their LiveKit voice-minute share of the day's
                              Deepgram $ — STT runs on ALL LiveKit sessions)
          + LiveKit$         (their LiveKit voice-minute share of the day's
                              LiveKit transport $ — runs on ALL LiveKit sessions)

Attribution model (PROPORTIONAL ALLOCATION):
  - ElevenLabs day total is split via character-stats by CALLER: has_api_key=False
    → agent (ConvAI runs inside the platform, no caller key); the API-called slice
    is split per key via all_api_keys into Intelligence Service vs LiveKit.
  - voice_11labs_intelligence: Intelligence Service raw TTS. NOT attributable per
    user from the ElevenLabs side, so the whole slice lands on the `Other` row.
  - voice_11labs_agent: the existing ConvAI per-user walk (per-conversation
    userId × voice-minute share; day total = the ConvAI product bucket) —
    the per-user walk is UNCHANGED; only the day-total it reconciles to now
    excludes the small raw-TTS slice.
  - voice_11labs_livekit: the ElevenLabs-TTS product bucket allocated across the
    LiveKit sessions that used ElevenLabs TTS, by those users' minute share among
    ElevenLabs-TTS LiveKit minutes.
  - voice_cartesia: the Cartesia daily total allocated across the LiveKit sessions
    that used Cartesia TTS, by those users' minute share among Cartesia-TTS
    LiveKit minutes.
  - voice_deepgram / voice_livekit (Deepgram STT / LiveKit transport): compute the
    vendor's DAILY WORKSPACE TOTAL $, then allocate across users by their overall
    LiveKit voice-minute share for the day (these run on every session regardless
    of the TTS choice). Per-user LiveKit minutes come from the forwarded LiveKit
    GCP logs (see livekit_minutes.py):
        user_vendor$ = vendor_total$ × user_livekit_min / total_livekit_min

Rates for the LiveKit-path vendors are PRICING-SHEET ESTIMATES (labelled as such
everywhere) — swap for contract rates via the flags below when they land:
  - Cartesia: --cartesia-rate-per-million (default 38.00, the Scale overage rate
    $38/1M; Tools/Cartesia). Credits are ACTUAL from the API; $ is DERIVED.
  - Inworld: --inworld-rate-per-million (default 25.00, TTS-2 On-Demand list price).
    Characters are ACTUAL from Inworld's usage API; agent-logged characters when not.
  - Deepgram: --deepgram-rate-per-hour (default 0.348, Nova-3 MULTILINGUAL
    streaming PAYG $0.0058/min — our current model). Hours are ACTUAL from the
    API; $ is DERIVED. The production Deepgram project is 7e42b984-… ('Aura Health'),
    which the settings key reads. Deepgram is LiveKit-path-only, so it contributes a
    plain $0 (no note) on a day with no LiveKit traffic; a `deepgram_pending` note is
    emitted only on real degradation — a missing or unauthorised key.
  - LiveKit: --livekit-rate-per-min (default 0.01, LiveKit Cloud Scale
    agent-session rate). $ is DERIVED from session-minutes.

Plus one reconciling "Other" row per day carrying every cost column's remainder
(vendor_total − attributed) — the LLM remainder (projects no user owns, e.g.
Evaluations and [Development] twins, plus attribution misses) as well as each voice
vendor's — so GROUP BY date SUM(total_cost) still equals the real multi-vendor
daily spend.

Backward compatibility: NEW files get the new header. When appending to an
existing file that carries an OLDER header, the file is MIGRATED in place to the
new schema first:
  - ORIGINAL 6-col (date, user_id, llm_cost, voice_cost, total_cost,
    subscription_state): pre-migration ElevenLabs was 100% ConvAI agent, so
    voice_11labs_agent = voice_cost and every other voice_* column = 0.
  - INTERMEDIATE 10-col (…, voice_11labs, voice_cartesia, voice_deepgram,
    voice_livekit, …): the single voice_11labs column predates the agent/LiveKit
    split, so voice_11labs_agent = voice_11labs and voice_11labs_livekit = 0;
    the LiveKit-path vendor columns carry over unchanged.
Consumers that read an un-migrated CSV should apply the same rules.

Usage:
    python3 store.py --date 2026-06-30 --llm-json /tmp/cpu_2026-06-30.json \
        --csv /path/to/per_user_daily_cost.csv

Idempotent: refuses to re-append a date already present in --csv.

Env: ELEVENLABS_ADMIN_API_KEY, AURA_API_TOKEN (required); CARTESIA_ADMIN_API_KEY,
INWORLD_API_KEY, DEEPGRAM_OWNER_API_KEY/DEEPGRAM_API_KEY (LiveKit-path vendors — missing keys
degrade that vendor to $0 with a note); GCP_KEY / GOOGLE_APPLICATION_CREDENTIALS
/ ADC for the LiveKit minutes.
"""
import argparse, calendar, csv, datetime, json, os, random, statistics, sys, time
import pathlib
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import livekit_minutes  # noqa: E402  (path insert must precede)
import livekit_plan  # noqa: E402  (path insert must precede)


def _ua_who() -> str:
    """Login name of whoever runs this, for the user agent: AINATIVE_UA_WHO, else the global git email's local part, git user.name, the OS account, or "unknown"."""
    who = re.sub(r"^\d+\+", "", os.environ.get("AINATIVE_UA_WHO", "").strip().partition("@")[0])
    if not who:
        if not hasattr(_ua_who, "found"):
            import getpass, subprocess
            found = ""
            for key in ("user.email", "user.name"):
                try:
                    val = subprocess.run(["git", "config", "--global", key], capture_output=True, encoding="utf-8", errors="replace", timeout=5).stdout.strip()
                except (OSError, subprocess.SubprocessError):
                    val = ""
                found = re.sub(r"^\d+\+", "", val.partition("@")[0]) if key == "user.email" else val
                if found:
                    break
            if not found:
                try:
                    found = getpass.getuser()
                except Exception:
                    found = ""
            _ua_who.found = found
        who = _ua_who.found
    return re.sub(r"[^A-Za-z0-9._-]+", "-", who).strip("-.") or "unknown"


def user_agent() -> str:
    """`AuraClaude/<who>/<plugin>:<skill>@<version> (session <id>)` -- labels this skill's calls to RTDB, GCP and the Aura API.

    Same format as gcloud_sa.user_agent(). A label, not proof: anyone can send it.
    """
    skill = pathlib.Path(__file__).resolve().parents[1]
    root = skill.parents[1]
    try:
        plugin = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["name"]
    except (OSError, ValueError, KeyError, TypeError):
        plugin = root.name
    try:
        front = (skill / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[1]
        version = re.search(r'version:\s*"([^"]+)"', front).group(1)
    except (OSError, IndexError, AttributeError):
        version = "unknown"
    session = os.environ.get("AINATIVE_SESSION_NAME") or (os.environ.get("CLAUDE_CODE_SESSION_ID") or "")[:8]
    session = re.sub(r"[^A-Za-z0-9._-]+", "-", session).strip("-") or "none"
    return f"AuraClaude/{_ua_who()}/{plugin}:{skill.name}@{version} (session {session})"



class LiveKitUnavailable(Exception):
    """The forwarded-log query failed, so the day's LiveKit minutes are UNKNOWN.

    Raised rather than degraded to zero minutes: zero minutes now means zero
    allocation of a subscription fee that is owed anyway, which would silently push
    a whole day's platform cost onto the `Other` row and look like a quiet day."""


class LiveKitTruncated(LiveKitUnavailable):
    """The day's log read hit its entry ceiling, so only its newest slice was read.

    A subclass so library callers that degrade LiveKitUnavailable to a note keep
    doing so; the per-user store catches it first and refuses the day, because a
    stored day built from a slice understates minutes, users and transport $."""
import engagement  # noqa: E402  (per-day engaged/active cohorts from Mixpanel)

# csv defaults to \r\n. These stores are read by shell tooling (awk -F, / cut) as
# often as by the csv module, and a CRLF file leaves a stray \r on the LAST column
# — activity_source reads as "ambient\\r" to awk while csv strips it. Write LF.
LF = "\n"

# New multi-vendor schema with ElevenLabs split into its two roles (ConvAI agent
# vs LiveKit-path TTS). voice_cost = voice_11labs_agent + voice_11labs_livekit +
# voice_cartesia + voice_deepgram + voice_livekit (kept for single-figure consumers).
# The single reconciling catch-all row appended per day.
OTHER_USER = "Other"


class SchemaError(Exception):
    """Unrecognised CSV header — raised, not sys.exit, because _migrate_old_csv is
    called as a LIBRARY by bq_sink.py / backfill_engagement.py. SystemExit derives
    from BaseException, so an `except Exception` guard in those callers could not
    catch it and the process would die instead of degrading."""
COLS = ["date", "user_id", "llm_cost", "voice_11labs_agent", "voice_11labs_intelligence",
        "voice_11labs_livekit", "voice_cartesia", "voice_fish", "voice_inworld",
        "voice_deepgram", "deepgram_inference_drawdown", "voice_livekit",
        "voice_livekit_platform", "livekit_minutes",
        "voice_cost", "total_cost", "subscription_state", "engaged", "active",
        "activity_source"]
# The pre-Inworld schema (no `voice_inworld`). Inworld TTS ran on the LiveKit path long
# before it had a column, and the aiVoiceProvider experiment (Aurie 1.33.0, 2026-10-15)
# made it arm `a` — with nowhere to land, its spend was booked NOWHERE (not even the
# `Other` row, which only carries remainders of booked totals), so the arm read as free.
# Migrated on append by adding the column at 0.0; re-store the affected days with
# --force to record what they actually spent.
PRE_INWORLD_COLS = [c for c in COLS if c != "voice_inworld"]
# The pre-Fish schema (no `voice_fish`): Fish Audio joined the LiveKit agent's TTS
# providers for the PROD-6068 voice experiment. Without its own column its spend has
# nowhere to land but the `Other` row's remainder, so the Fish arm would read as
# roughly free rather than cheap — silently, since the day still reconciles. Migrated
# on append by adding the column at 0.0 (no Fish traffic predates the column).
PRE_FISH_COLS = ["date", "user_id", "llm_cost", "voice_11labs_agent", "voice_11labs_intelligence",
                 "voice_11labs_livekit", "voice_cartesia", "voice_deepgram",
                 "deepgram_inference_drawdown", "voice_livekit",
                 "voice_livekit_platform", "livekit_minutes",
                 "voice_cost", "total_cost", "subscription_state", "engaged", "active",
                 "activity_source"]
# The pre-PROD-6008 schema: no stored Inference drawdown, so `deepgram_inference_day`
# was handed a month-to-date of 0.0 and priced every day against an empty credit block.
# Correct while a month stays inside the block and silently $0 the moment it does not.
# Migrated on append by adding the column at 0.0.
PRE_DRAWDOWN_COLS = ["date", "user_id", "llm_cost", "voice_11labs_agent",
                     "voice_11labs_intelligence", "voice_11labs_livekit",
                     "voice_cartesia", "voice_deepgram", "voice_livekit",
                     "voice_livekit_platform", "livekit_minutes",
                     "voice_cost", "total_cost", "subscription_state", "engaged",
                     "active", "activity_source"]
# The pre-PROD-5998 schema: one `voice_livekit` column holding minutes x the OVERAGE
# rate, which prices minutes that are in fact inside the plan allotment and therefore
# marginally free. August 2026 booked $119.90 against a $501.14 invoice whose variable
# part was $1.14. Migrated on append by inserting the two new columns at 0.0; the
# `--rebook-livekit` pass restates historical days from the invoice.
PRE_PLATFORM_COLS = ["date", "user_id", "llm_cost", "voice_11labs_agent",
                     "voice_11labs_intelligence", "voice_11labs_livekit", "voice_cartesia",
                     "voice_deepgram", "voice_livekit", "voice_cost", "total_cost",
                     "subscription_state", "engaged", "active", "activity_source"]
# The pre-ENG-982 schema (no voice_11labs_intelligence — raw ElevenLabs TTS was
# mis-booked wholly to voice_11labs_livekit). Migrated on append by inserting the
# new column blank; `--recreate` + a re-store re-splits historical days.
PRE_CALLER_COLS = ["date", "user_id", "llm_cost", "voice_11labs_agent",
                   "voice_11labs_livekit", "voice_cartesia", "voice_deepgram",
                   "voice_livekit", "voice_cost", "total_cost", "subscription_state",
                   "engaged", "active", "activity_source"]
# The ORIGINAL pre-PROD-3180 schema (100% ElevenLabs ConvAI voice). Detected on
# append so an existing store is migrated in place before new rows land.
OLD_COLS = ["date", "user_id", "llm_cost", "voice_cost", "total_cost", "subscription_state"]
# The INTERMEDIATE schema (single voice_11labs column, before the agent/LiveKit
# ElevenLabs split). Also migrated on append.
MID_COLS = ["date", "user_id", "llm_cost", "voice_11labs", "voice_cartesia",
            "voice_deepgram", "voice_livekit", "voice_cost", "total_cost",
            "subscription_state"]
# The pre-engagement schema (multi-vendor cost, no engaged/active/activity_source
# columns). Migrated on append by adding the three columns blank; run
# backfill_engagement.py to fill them for historical rows.
PREV_COLS = ["date", "user_id", "llm_cost", "voice_11labs_agent", "voice_11labs_livekit",
             "voice_cartesia", "voice_deepgram", "voice_livekit", "voice_cost",
             "total_cost", "subscription_state"]
_TIER_RANK = {"paid": 0, "trial": 1, "grace": 2, "free": 3}

# --- Estimated vendor rates (pricing-sheet ESTIMATES — replace with contract
# --- rates via the CLI flags when they land). ---
# Cartesia Scale overage rate ($38 / 1M credits, list). Plan of record is Scale
# ($299/mo, 8M credits) since the voice go-live — `Tools/Cartesia` → Plan & pricing is
# canonical. The period runs well past the 8M allotment, so the overage rate is the
# marginal cost per credit. (Other tiers: Startup $45/1M, Pro $65/1M.)
DEFAULT_CARTESIA_RATE_PER_MILLION = 38.00
# Inworld realtime TTS-2 (`inworld-tts-2`), On-Demand list price: $25 per 1M characters
# (inworld.ai/pricing, checked 2026-10-19). Inworld's own usage API prices its
# `characters_est_cost` at this rate (295,464 chars → $7.3866 on 2026-10-18), and per its
# docs that estimate uses public pricing — so a cheaper subscription tier (Builder $17.50,
# Developer $15, Growth $12.50) needs this flag. Characters are ACTUAL from the API.
DEFAULT_INWORLD_RATE_PER_MILLION = 25.00
# Fish Audio s2-pro: $15 per 1M UTF-8 BYTES of input text (fish.audio/developers,
# checked 2026-09-15). Fish has no usage API — only a credit-balance snapshot — so the
# day's usage is the agent's own `tts_characters` (LiveKit UsageCollector) summed over
# Fish-TTS sessions. Characters, not bytes: one character is one byte for Latin text,
# so non-Latin sessions are UNDER-counted here; the rate itself is the vendor's.
DEFAULT_FISH_RATE_PER_MILLION = 15.00
# Deepgram Nova-3 MULTILINGUAL streaming, on-demand PAYG (our current model/plan):
# $0.0058/min × 60 = $0.348/hr (confirmed cartesia... deepgram.com/pricing 2026-07-02).
# Monolingual would be $0.0048/min ($0.288/hr, ~17% less); Growth-multilingual $0.0050/min.
DEFAULT_DEEPGRAM_RATE_PER_HOUR = 0.348
# LiveKit Cloud agent-session OVERAGE rate for the plan of record, from the shared plan table
# (`livekit_plan.py`) rather than a local constant — this file's comment said Scale while
# `eng:report-livekit-quota` defaulted to Ship, and at ~12,000 min/month that is the difference
# between 23% utilization and ~7,000 minutes of overage.
#
# NOTE this is the OVERAGE rate, so it prices minutes that are in fact inside the allotment and
# marginally free: Aug 2026 booked $119.90 here against a $501.14 invoice whose variable part was
# $1.14. Replacing this derivation with the actual invoice is PROD-5998; this change reconciles the
# plan fact only and leaves the arithmetic exactly as it was.
DEFAULT_LIVEKIT_RATE_PER_MIN = livekit_plan.overage_per_unit("agent_session_minutes", livekit_plan.DEFAULT_PLAN)

# The forwarded GCP log minutes overcount LiveKit's billed agent-session minutes. Fitted on
# the Sep and Oct 2026 invoices (3.2-3.6% over; Sep rebooked at x0.965705 matched to $0.02).
# Billed minutes = logged minutes x this, and they price every LiveKit $ figure; the stored
# `livekit_minutes` column stays as logged. Re-checked monthly against the invoice.
LIVEKIT_LOG_TO_BILLED = 0.966

CARTESIA_API = "https://api.cartesia.ai"
CARTESIA_VERSION = "2026-03-01"
DEEPGRAM_API = "https://api.deepgram.com"
INWORLD_API = "https://api.inworld.ai"


def _unix(d):
    return int(datetime.datetime.fromisoformat(d).replace(tzinfo=datetime.timezone.utc).timestamp())


# Share of attributed spend allowed to come from a stored-completions FALLBACK
# (a ledger read that raised) before this store refuses the day. Not zero: a
# ledger-served project can legitimately bill a rounding-level amount, and
# refusing a whole day over $0.01 is how a good day gets withheld. Not lax
# either — post-epoch the fallback cannot see Responses-API traffic, so any
# material share means the per-user split for those dollars is guesswork.
MAX_DEGRADED_ATTRIBUTION_PCT = 0.5


def assert_attribution_usable(llm_json_obj, *, allow_degraded=False):
    """Refuse to PERSIST a day whose per-user split came from a degraded walk.

    Defence in depth for ENG-1054: `analyze-openai-cost-per-user` already exits 2
    when the ledger is wholly unreachable, but this store is the thing that writes
    to `aurie_per_user_daily_cost`, which the entire `chart-aurie-*` family reads.
    An upstream guard that is bypassed, patched, or run with an override should not
    be the only thing standing between a degraded walk and permanent warehouse
    history — so the writer checks too.

    Gates on the degraded *share* (ENG-1114), never on the presence of a marker in
    `attribution.sources`. `completions-non-ledger` means a project the ledger does
    not serve by design, and on 2026-08-09 that marker was set by `Front` at $0.01
    while every project with real spend went through the ledger ($350.11 of
    $350.12) — a substring test would refuse that day, and did."""
    attribution = (llm_json_obj or {}).get("attribution") or {}
    materiality = attribution.get("materiality") or {}
    if not materiality:
        # Walk JSON predates ENG-1114 and carries no shares. Fall back to the
        # coarse signal rather than silently accepting: `completions-fallback`
        # is the outage marker, and it is the only one worth refusing on.
        if "completions-fallback" in (attribution.get("sources") or []) and not allow_degraded:
            sys.exit(
                "REFUSING to store: the LLM walk fell back to stored chat completions "
                "for at least one project (attribution.sources contains "
                "'completions-fallback') and the JSON carries no materiality block to "
                "size it. Re-run the walk once the ledger is reachable, or pass "
                "--allow-degraded-attribution to persist it deliberately."
            )
        return
    degraded_pct = float(materiality.get("degraded_share_pct") or 0.0)
    if degraded_pct > MAX_DEGRADED_ATTRIBUTION_PCT and not allow_degraded:
        dollars = (materiality.get("dollars") or {}).get("completions-fallback", 0.0)
        sys.exit(
            f"REFUSING to store: {degraded_pct:.2f}% of attributed spend "
            f"(${float(dollars):,.2f}) was split using stored chat completions after a "
            f"ledger read FAILED, which post-epoch cannot see Responses-API traffic. "
            f"Threshold is {MAX_DEGRADED_ATTRIBUTION_PCT}%. This day would become "
            f"permanent per-user history in aurie_per_user_daily_cost. Re-run the walk "
            f"once the ledger is reachable (the store is idempotent per day), or pass "
            f"--allow-degraded-attribution to persist it deliberately."
        )
    non_ledger_pct = float(materiality.get("non_ledger_share_pct") or 0.0)
    if non_ledger_pct > 0:
        # Reported, never fatal: these projects have no ledger coverage by design.
        print(
            f"note: {non_ledger_pct:.2f}% of attributed spend came from projects the "
            f"ledger does not serve (by design, not an outage).",
            file=sys.stderr,
        )


# Share of WALKABLE LLM spend that must actually reach a user before this store
# will persist the day. Not a source check — a walk can use exactly the right
# source for the window and still resolve nothing (ENG-1120).
#
# The number is calibrated against the store itself. Over the 70 days held on
# 2026-08-10, healthy days ran 91.44%–99.78% and the two collapsed days
# (2026-06-24, 2026-06-25) ran 0.00%, with nothing in between. 75% sits ~16
# points below the worst healthy day and 75 above the collapsed ones, so it
# refuses an unambiguous collapse without discarding a good day whose untagged
# system/batch traffic happened to run high.
MIN_ATTRIBUTION_COVERAGE_PCT = 75.0


def assert_attribution_coverage(llm_json_obj, *, allow_low_coverage=False):
    """Refuse to PERSIST a day whose per-user LLM attribution collapsed (ENG-1120).

    The gap this closes is that ENG-1114's `assert_attribution_usable` gates on
    attribution *source* — `materiality.degraded_share_pct`, the share split by a
    stored-completions fallback after a ledger read FAILED. Two things make it
    blind to a collapse:

    * a pre-epoch window legitimately attributes from stored completions (no
      ledger existed before 2026-07-23T03:25Z), so there is no fallback marker;
    * if the walk resolves ZERO users there is no degraded *share* either — the
      denominator is source-classified dollars, not dollars that reached anyone.

    So both guards are needed, and they are deliberately separate: source answers
    "was this the right way to attribute", coverage answers "did it attribute".

    Nothing else catches it downstream. The dollars still reconcile to the
    authoritative bill because the unattributed remainder lands on the store's
    `Other` row, so no total is wrong and no error is raised — only the
    distribution is empty, which is exactly what `chart-aurie-cost-concentration`,
    `-cost-mix`, `-cohort-cost`, `-tenure-cost`, `-cost-curve`, `-cumulative-cost`,
    `-lifetime-cost` and `-cohort-payback` consume."""
    attribution = (llm_json_obj or {}).get("attribution") or {}
    coverage = attribution.get("coverage") or {}
    if not coverage:
        # Walk JSON predates ENG-1120. Say so rather than reporting a pass — a
        # missing check is not a clean one, and this store is what makes a
        # collapsed day permanent.
        print(
            "note: walk JSON carries no attribution.coverage block (predates "
            "ENG-1120) — per-user LLM coverage was NOT verified for this day.",
            file=sys.stderr,
        )
        return
    eligible = float(coverage.get("eligible_dollars") or 0.0)
    if eligible <= 0:
        return  # no walkable LLM spend — nothing to cover, not a collapse
    pct = float(coverage.get("coverage_pct") or 0.0)
    if pct < MIN_ATTRIBUTION_COVERAGE_PCT and not allow_low_coverage:
        unreached = float(coverage.get("unreached_dollars") or 0.0)
        sys.exit(
            f"REFUSING to store: only {pct:.2f}% of walkable LLM spend reached a "
            f"user (${unreached:,.2f} of ${eligible:,.2f} reached none; "
            f"{coverage.get('users_with_cost')} users carry cost). Threshold is "
            f"{MIN_ATTRIBUTION_COVERAGE_PCT}%. The day's dollars would still "
            f"reconcile to the bill — the unattributed remainder lands on the "
            f"`Other` row — so this failure is silent once written, and every "
            f"per-user reader would average over an empty distribution. Re-run "
            f"the walk (the store is idempotent per day), or pass "
            f"--allow-low-coverage to persist it deliberately."
        )
    if pct < 99.0:
        print(
            f"note: per-user LLM coverage {pct:.2f}% "
            f"(${float(coverage.get('unreached_dollars') or 0.0):,.2f} reached no user).",
            file=sys.stderr,
        )


# A day's attributed-user count, as a fraction of the trailing median, below
# which the day is treated as a PARTIAL attribution collapse (ENG-1120).
#
# Coverage alone cannot see this case. Allocation is by token share of the
# project's dollars, so when only SOME completions survive, the survivors absorb
# 100% of the money: coverage still reads ~99% while the cost is concentrated on
# a fraction of the users. 2026-06-23 is exactly that — 99.34% coverage, but 317
# users instead of ~1,630, so each carried roughly 5x their true cost.
#
# Calibrated on the same 70 days: the three affected days scored 0.00 / 0.00 /
# 0.19 and the lowest legitimate day scored 0.69 (2026-07-23), so 0.40 sits in a
# 50-point gap with no observed value near it.
MIN_USER_COUNT_RATIO = 0.40
USER_COUNT_BASELINE_DAYS = 14
USER_COUNT_MIN_BASELINE_DAYS = 7

# Share of the day's ENGAGED users (messaged Aurie, per Mixpanel) who carry LLM
# cost, at or above which a low user count is read as a population shift rather
# than a collapse (ENG-1559).
#
# The count ratio cannot tell the two apart. When the ambient daily feed and
# notification jobs were retired on 2026-10-07 (PROD-6948), users with LLM cost
# fell from ~2,300 to ~900 — every one of them a user nobody had talked to, billed
# by a cron — and the ratio refused every day until the trailing median caught up.
# Engaged users are the population a lost completion actually drops: a user who
# messaged Aurie always produces one. So the ENG-1120 shape drops THEIR share,
# while a retired background job leaves it alone.
#
# Calibrated on all 128 days stored on 2026-10-12: healthy days ran 85.8%-100%,
# the collapsed days 72.3% (2026-06-23), 0% and 0%; 2026-10-08/09 ran 89.4% and
# 89.5%. The gap is narrower than the count ratio's, which is why this only
# exonerates a low count — it never replaces the count check.
MIN_ENGAGED_COST_SHARE = 0.80
# Below this many engaged users the share is too noisy to vouch for a day.
MIN_ENGAGED_FOR_SHARE = 30


def engaged_cost_share(engaged, llm):
    """Share of `engaged` uids carrying LLM cost in `llm`, or None if unjudgeable.

    None — never 0.0 — when Mixpanel returned no cohort or too few engaged users:
    an unknown share must not exonerate a low count, and must not read as a
    collapse either. The guard then falls back to the count alone."""
    if not engaged or len(engaged) < MIN_ENGAGED_FOR_SHARE:
        return None
    return sum(1 for u in engaged if llm.get(u, 0) > 0) / len(engaged)


def walk_eligible_dollars(llm_json):
    """The walk's walkable-LLM total, or None when the JSON predates ENG-1120.

    Deliberately separate from `llm_costs`' `total`: that is the org-wide OpenAI
    figure, which includes the `[Development]` and internal-tooling projects the
    walk skips on purpose. Only the walkable slice is a valid denominator for
    "should anyone have been attributed today"."""
    try:
        with open(llm_json) as fh:
            j = json.load(fh)
    except (OSError, ValueError):
        return None
    cov = ((j or {}).get("attribution") or {}).get("coverage") or {}
    v = cov.get("eligible_dollars")
    return float(v) if v is not None else None


def assert_user_count_plausible(day, user_count, table, project, *,
                                eligible_dollars=None, llm_total=None,
                                engaged_share=None, allow_low=False):
    """Refuse a day whose attributed-user count collapsed against its own history.

    The second half of the ENG-1120 guard, and the reason it is not folded into
    the coverage check: a PARTIAL loss of stored completions leaves coverage
    looking healthy while silently concentrating the day's cost onto whoever
    survived. Only a count comparison sees it.

    The baseline is the trailing median of the days already stored, so it needs
    no configuration and follows organic growth. Skips itself (rather than
    failing) when fewer than `USER_COUNT_MIN_BASELINE_DAYS` prior days exist —
    a first backfill has no history to compare against, and refusing there would
    block the store from ever being seeded.

    Also skips when the day had no WALKABLE spend to attribute: zero users is then
    the correct answer, not a collapse, and refusing would block a legitimately
    quiet day. `eligible_dollars` must be the walk's `attribution.coverage
    .eligible_dollars` — NOT its `total`, which is the org-wide OpenAI figure and
    still counts the skipped `[Development]` and internal-tooling projects. A day
    whose only spend sat in skipped projects has zero walkable dollars and zero
    users legitimately, but a positive org total, so gating on the org total would
    refuse it.

    `llm_total` is only a FALLBACK, for a pre-ENG-1120 walk JSON that carries no
    coverage block. It is safe in one direction only: walkable spend is a subset
    of the org total, so `llm_total <= 0` does imply nothing was walkable, while
    the converse does not hold — which is why it is never preferred when
    `eligible_dollars` is known.

    `engaged_share` (from `engaged_cost_share`) is the second signal (ENG-1559):
    a low count with engaged users still covered at their usual rate is a
    population shift, so the day is stored with a note. None (Mixpanel down, or
    too few engaged users) keeps the count-only behaviour — an unknown never
    exonerates."""
    spend = eligible_dollars if eligible_dollars is not None else llm_total
    if spend is not None and spend <= 0:
        return
    from bq import query
    sql = f"""
    SELECT COUNTIF(user_id != 'Other' AND llm_cost > 0) AS users
    FROM `{table}`
    WHERE date < DATE(@day)
      AND date >= DATE_SUB(DATE(@day), INTERVAL @window DAY)
    GROUP BY date
    HAVING users > 0
    """
    # `@day` binds as STRING (bq._param_type maps str -> STRING). BigQuery does
    # coerce it against a DATE column here — both forms were verified to return
    # identical rows — but the cast is explicit anyway: it matches the sibling
    # skills, and a guard should not depend on an implicit coercion rule to run.
    rows = query(sql, project,
                 params={"day": day, "window": USER_COUNT_BASELINE_DAYS})
    counts = sorted(int(r["users"]) for r in rows)
    if len(counts) < USER_COUNT_MIN_BASELINE_DAYS:
        print(
            f"note: only {len(counts)} prior day(s) in {table} — skipping the "
            f"user-count plausibility check for {day}.",
            file=sys.stderr,
        )
        return
    median = statistics.median(counts)
    ratio = (user_count / median) if median > 0 else 1.0
    if (ratio < MIN_USER_COUNT_RATIO and engaged_share is not None
            and engaged_share >= MIN_ENGAGED_COST_SHARE):
        print(
            f"note: {day} attributes LLM cost to {user_count} users, {ratio:.0%} "
            f"of the trailing {len(counts)}-day median ({median:.0f}), but "
            f"{engaged_share:.1%} of engaged users carry cost (threshold "
            f"{MIN_ENGAGED_COST_SHARE:.0%}) — read as a population shift, not a "
            f"collapse; storing.",
            file=sys.stderr,
        )
        return
    if ratio < MIN_USER_COUNT_RATIO and not allow_low:
        engaged_detail = (
            f" Only {engaged_share:.1%} of engaged users carry cost (a population "
            f"shift needs {MIN_ENGAGED_COST_SHARE:.0%})."
            if engaged_share is not None else
            f" The engaged-user share could not be measured (Mixpanel cohort "
            f"unavailable or under {MIN_ENGAGED_FOR_SHARE} users), so a "
            f"population shift cannot be told apart from a collapse."
        )
        # `1/ratio` is undefined at the total-collapse end of this guard's own
        # range — user_count 0 is 2026-06-24, the case it exists for — so the
        # inflation hint is only meaningful when somebody survived.
        detail = (
            f"A partial loss of stored completions concentrates the day's whole "
            f"LLM bill onto whoever survived, so per-user cost is inflated by "
            f"roughly {1 / ratio:.1f}x while total spend and coverage both still "
            f"look correct."
            if ratio > 0 else
            f"No user carries any LLM cost, so the day's whole LLM bill would "
            f"land on the `Other` reconciling row: the total still balances to "
            f"the authoritative bill while the per-user distribution is empty."
        )
        sys.exit(
            f"REFUSING to store: {day} attributes LLM cost to {user_count} users, "
            f"{ratio:.0%} of the trailing {len(counts)}-day median ({median:.0f}). "
            f"{detail}{engaged_detail} Threshold is {MIN_USER_COUNT_RATIO:.0%}. Re-run the "
            f"walk (the store is idempotent per day), or pass --allow-low-coverage "
            f"to persist it deliberately."
        )


# Voice counterparts of the two ENG-1120 LLM thresholds (ENG-1134). The voice
# side had no collapse guard at all: all three guards above read the LLM walk,
# and the third counts users with LLM cost, so a voice-only collapse moved none
# of them. The day would still reconcile to the cent — every unattributed voice
# dollar lands on the `Other` row — so nothing raised and only the distribution
# was empty, which is the exact shape ENG-1120 exists to refuse.
#
# Calibration is deliberately the LLM guard's numbers rather than tighter ones.
# Voice coverage ran 97.9%-100.0% across all 70 days held on 2026-08-10, so the
# observed floor is 22.9 points above 75.0 — and the failure this catches is a
# hard 0% (a vendor's whole bill allocated over a zero-minute denominator), not
# a gentle slide, so anything between ~1 and ~90 would separate the two cases
# equally well. Reusing 75.0 keeps one number to reason about instead of two.
MIN_VOICE_COVERAGE_PCT = 75.0
MIN_VOICE_USER_COUNT_RATIO = 0.40


def summarize_voice_coverage(paths, *, unattributable_dollars=0.0):
    """Per-vendor-path voice attribution coverage: did the dollars reach anyone?

    `paths` maps a path name to `(eligible_dollars, {uid: $})`. Each voice vendor
    is allocated independently — ElevenLabs ConvAI by its own per-user walk, and
    the four LiveKit-path vendors by minute share — so a collapse can take one
    path while the others stay healthy. Rolling them into a single ratio would
    let a large healthy path mask a smaller broken one, hence `paths` in the
    result and `collapsed_paths` alongside the blended figure.

    `unattributable_dollars` is spend that is **correctly** unattributed and must
    stay out of the denominator: Intelligence Service TTS has no per-user signal
    on the ElevenLabs side at all, so its whole slice lands on `Other` by design.
    Counting it as uncovered would make every ordinary day look partly collapsed
    — the same false-refusal shape the LLM guard avoids by excluding skipped
    projects."""
    out, attributed, eligible, users = {}, 0.0, 0.0, set()
    for name, (path_eligible, by_user) in paths.items():
        path_eligible = float(path_eligible or 0.0)
        path_attributed = float(sum((by_user or {}).values()))
        eligible += path_eligible
        attributed += path_attributed
        users |= {u for u, v in (by_user or {}).items() if v > 0}
        out[name] = {
            "eligible_dollars": round(path_eligible, 4),
            "attributed_dollars": round(path_attributed, 4),
            # A path with nothing to attribute is complete, not collapsed — the
            # same convention the LLM walk uses for a zero-spend day.
            "coverage_pct": (round(100.0 * path_attributed / path_eligible, 2)
                             if path_eligible > 0 else 100.0),
        }
    return {
        "eligible_dollars": round(eligible, 4),
        "attributed_dollars": round(attributed, 4),
        "unreached_dollars": round(max(eligible - attributed, 0.0), 4),
        "coverage_pct": (round(100.0 * attributed / eligible, 2)
                         if eligible > 0 else 100.0),
        "unattributable_dollars": round(float(unattributable_dollars or 0.0), 4),
        "users_with_voice_cost": len(users),
        "paths": out,
        # Only a path with real money in it can collapse; a $0 path is quiet.
        "collapsed_paths": sorted(
            n for n, p in out.items()
            if p["eligible_dollars"] > 0 and p["coverage_pct"] <= 0.0
        ),
    }


def assert_voice_coverage(cov, *, allow_low_coverage=False):
    """Refuse to PERSIST a day whose per-user VOICE attribution collapsed.

    The LiveKit path is the realistic trigger and the reason this is not merely
    symmetry-for-its-own-sake: Deepgram, Cartesia, ElevenLabs-TTS and LiveKit
    transport are all allocated by `user_min / total_min` from the forwarded GCP
    logs. A GCP auth failure, a renamed `logName`, or a change to the
    `TTS provider created:` line yields zero per-user minutes, so every one of
    those vendors divides by a zero denominator and attributes nothing — while
    each vendor's own billing API keeps returning a perfectly good daily total.

    That distinction is what the guard keys on, and why it cannot be replaced by
    "did the log query fail": a vendor total of $0 means a genuinely quiet day
    and is allowed through, whereas a vendor total above $0 with no minutes to
    spread it over means traffic happened and we could not say whose it was."""
    if not cov:
        return
    eligible = float(cov.get("eligible_dollars") or 0.0)
    if eligible <= 0:
        return  # no attributable voice spend — a quiet day, not a collapse
    pct = float(cov.get("coverage_pct") or 0.0)
    if pct >= MIN_VOICE_COVERAGE_PCT:
        # One dead vendor inside an otherwise healthy day is exactly what a single
        # ratio hides. Store and flag (PROD-6992), don't refuse: the day's other
        # vendors are sound, and the dead path's dollars are on the `Other` row,
        # where chart-aurie-daily-margin finds them again and the cost report
        # counts them. So the stored data is the durable flag; this is the loud one.
        if cov.get("collapsed_paths"):
            dead = ", ".join(
                f"{n} ${cov['paths'][n]['eligible_dollars']:,.2f}"
                for n in cov["collapsed_paths"])
            print(f"WARNING: voice path(s) billed but reached no user: {dead} "
                  f"(blended coverage {pct:.2f}%). Storing the day; those dollars "
                  f"land on the `Other` row and the cost report flags the day.",
                  file=sys.stderr)
        return
    detail = (f" Collapsed path(s): {', '.join(cov['collapsed_paths'])}."
              if cov.get("collapsed_paths") else "")
    if allow_low_coverage:
        print(f"note: voice coverage {pct:.2f}% is below "
              f"{MIN_VOICE_COVERAGE_PCT}% — persisting anyway "
              f"(--allow-low-coverage).{detail}", file=sys.stderr)
        return
    sys.exit(
        f"REFUSING to store: only {pct:.2f}% of attributable voice spend reached "
        f"a user (${cov['unreached_dollars']:,.2f} of ${eligible:,.2f} reached "
        f"none; {cov['users_with_voice_cost']} users carry voice cost)."
        f"{detail} Threshold is {MIN_VOICE_COVERAGE_PCT}%. The day's dollars "
        f"would still reconcile to the vendor bills — the unattributed remainder "
        f"lands on the `Other` row — so this failure is silent once written. The "
        f"usual cause is the LiveKit minute map coming back empty (GCP auth, or a "
        f"changed log shape), which zeroes the denominator every LiveKit-path "
        f"vendor allocates on. Re-run (the store is idempotent per day), or pass "
        f"--allow-low-coverage to persist it deliberately."
    )


def assert_voice_user_count_plausible(day, user_count, table, project, *,
                                      eligible_dollars=None, allow_low=False):
    """Refuse a day whose voice-attributed user count collapsed against history.

    The distribution half of the voice guard, and it exists for the reason the
    LLM side proved: 2026-06-23 read 99.34% coverage while concentrating the
    whole bill onto a fifth of the users. Voice allocates by minute share, so the
    same shape appears when the minute map is partially recovered — the sessions
    that survived absorb every vendor dollar and each looks several times more
    expensive than it was. Coverage cannot see that; only the count can."""
    if eligible_dollars is not None and eligible_dollars <= 0:
        return
    from bq import query
    sql = f"""
    SELECT COUNTIF(user_id != 'Other' AND voice_cost > 0) AS users
    FROM `{table}`
    WHERE date < DATE(@day)
      AND date >= DATE_SUB(DATE(@day), INTERVAL @window DAY)
    GROUP BY date
    HAVING users > 0
    """
    rows = query(sql, project,
                 params={"day": day, "window": USER_COUNT_BASELINE_DAYS})
    counts = sorted(int(r["users"]) for r in rows)
    if len(counts) < USER_COUNT_MIN_BASELINE_DAYS:
        print(f"note: only {len(counts)} prior day(s) in {table} — skipping the "
              f"voice user-count plausibility check for {day}.", file=sys.stderr)
        return
    median = statistics.median(counts)
    ratio = (user_count / median) if median > 0 else 1.0
    if ratio < MIN_VOICE_USER_COUNT_RATIO and not allow_low:
        detail = (
            f"A partial loss of the LiveKit minute map concentrates every "
            f"minute-allocated vendor's bill onto whoever survived, inflating "
            f"their per-user voice cost by roughly {1 / ratio:.1f}x while the "
            f"day's totals and coverage both still look correct."
            if ratio > 0 else
            f"No user carries any voice cost, so the whole voice bill would land "
            f"on the `Other` reconciling row: the total still balances while the "
            f"per-user distribution is empty."
        )
        sys.exit(
            f"REFUSING to store: {day} attributes voice cost to {user_count} "
            f"users, {ratio:.0%} of the trailing {len(counts)}-day median "
            f"({median:.0f}). {detail} Threshold is "
            f"{MIN_VOICE_USER_COUNT_RATIO:.0%}. Re-run (the store is idempotent "
            f"per day), or pass --allow-low-coverage to persist it deliberately."
        )


def llm_costs(llm_json, *, allow_degraded=False, allow_low_coverage=False):
    """Return ({uid: $}, authoritative_total, {uid: top_project}) from an
    analyze-openai-cost-per-user JSON. top_project drives the activity_source
    reason tag for users reached by an ambient/batch job (no engagement event)."""
    if not os.path.exists(llm_json):
        sys.exit(
            f"missing LLM JSON {llm_json}. Run the OpenAI per-user walk first:\n"
            f"  eng:analyze-openai-cost-per-user report.py --since <date> --until <date+1> "
            f"--no-stats --no-headline --top 1 --json {llm_json}"
        )
    with open(llm_json) as fh:
        j = json.load(fh)
    assert_attribution_usable(j, allow_degraded=allow_degraded)
    assert_attribution_coverage(j, allow_low_coverage=allow_low_coverage)
    ua = j.get("users_all", [])
    return ({u["uid"]: u["amount"] for u in ua}, j.get("total", 0.0),
            {u["uid"]: u.get("top_project", "") for u in ua})


# --- ElevenLabs caller attribution (ENG-982) -------------------------------
# ElevenLabs is called by THREE Aura systems, and `product_type` cannot tell the
# raw-TTS callers apart. Canonical write-up (incl. the evidence): `Tools/ElevenLabs`
# in AI Knowledge → "Attributing spend to the calling service".
#
#   ConvAI agent          → runs INSIDE the ElevenLabs platform, so there is no
#                           caller API key: it is exactly the has_api_key=False
#                           slice, and bills to the agent resource.
#   Intelligence Service  → raw TTS over the API (what mobile calls).
#   LiveKit voice agent   → raw TTS over the API. One of several selectable TTS
#                           providers there, so this column swings between $0 and
#                           the whole LiveKit TTS bill as the selection changes.
#
# `product_type=TTS` is NOT LiveKit — that assumption booked ~8 weeks of
# Intelligence Service spend into voice_11labs_livekit. Attribute by CALLER KEY.
#
# A label is EITHER a key hash OR a key NAME, and which one ElevenLabs returns is
# not stable — it varies per query for the same key. Observed live: the LiveKit key
# came back as "7e7d1476d1b33139 (...7e7d)" for the 2026-08-04/05/06 single-day
# windows but as "TTS [Prod]" for 2026-07-31, and the Intelligence Service TTS [Dev]
# key came back as "TTS [Dev]" for single days but as its hash over a six-week
# window. So BOTH forms have to be handled; neither can be assumed away.
#
# Hashes are exact. `GET /v1/service-accounts` exposes `hashed_xi_api_key`, whose
# first 16 hex chars are byte-for-byte the label, so hash → caller is a lookup
# rather than an inference from relative volume (which cannot distinguish two keys
# belonging to the same caller, and had Prod/Dev backwards).
#
# Names are NOT all resolvable: "TTS [Prod]" is the name of a key under BOTH service
# accounts, and the service-account name ("LiveKit" / "Intelligence Service") never
# appears in the label. So that one name is irreducibly ambiguous and must never be
# guessed — see _EL_AMBIGUOUS_NAMES. Renaming the keys per `Tools/ElevenLabs` →
# "rename the keys to identify the caller" is what removes the ambiguity at source;
# it is console-only (`/v1/workspace/api-keys` 401s for this token).
_EL_INTELLIGENCE_KEYS = (
    "b81e082fbbfddd02",                 # SA Intelligence Service / TTS [Prod]   (…7d22)
    "0bcda5a1f7649c3c",                 # SA Intelligence Service / TTS [Dev]    (…4697)
    "9408b12da82bc96a",                 # SA Intelligence Service / Conversational [Prod]
    "0d611bc23559de9d",                 # SA Intelligence Service / Conversational [Dev]
    # Pre-cutover user keys on <redacted-email>. They are deleted, so ElevenLabs
    # no longer resolves their names and reports the hash instead. Both are
    # Intelligence Service: their spend ends at the 2026-07-28 cutover, which is
    # before LiveKit's first ElevenLabs call, and the raw-TTS bucket was entirely
    # Intelligence Service for that whole period. The Prod/Dev labels come from the
    # billing-cadence discriminator — TTS [Prod] bills daily, TTS [Dev] is sporadic
    # — and are cosmetic here, since both resolve to the same caller.
    "944c051ecf733e9c",                 # pre-cutover user key, daily     → TTS [Prod]
    "482373b5eec12192",                 # pre-cutover user key, sporadic  → TTS [Dev]
)
# ElevenLabs is ONE OF the LiveKit agent's selectable TTS providers (Cartesia /
# ElevenLabs / OpenAI / Inworld), and which one is selected changes with the voice
# roadmap rather than settling. So expect this column to swing between $0 and the
# entire LiveKit TTS bill, in both directions, and do not read either extreme as a
# bug: the per-session provider detection in `livekit_minutes` is what decides the
# allocation, so a provider flip needs NO change here. Sanity-check a surprising
# value against `minutes_by_provider` for the day before assuming misattribution.
# Matched by hash; the `"livekit" in norm` substring fallback below catches its name
# too, since every live key is now named after its caller.
_EL_LIVEKIT_KEYS = (
    "7e7d1476d1b33139",                 # SA LiveKit / TTS [LiveKit Prod]        (…ab4d)
)

# CALLER TOKENS — the durable half of the name matching. Every live service-account
# key is named after the caller that holds it ("TTS [LiveKit Prod]",
# "Conversational [Intelligence Service Prod]", …), so a name label carries its own
# attribution and a new key needs no code change to be split correctly. This is the
# whole point of that naming convention; keep it when adding keys.
_EL_CALLER_TOKENS = (
    ("livekit", "livekit"),
    ("intelligenceservice", "intelligence"),
)

# LEGACY bare NAME labels, from the pre-cutover user keys on <redacted-email>.
# Those keys are deleted, so these labels only ever appear in a window that reaches
# back before 2026-07-28, and both were Intelligence Service — LiveKit had no
# ElevenLabs key at all until the 2026-07-28 cutover, so no LiveKit spend can hide
# in them. Confirmed against the per-key hashes: "TTS [Dev]" is 482373b5eec12192
# ($19.12) and "TTS [Prod]" is 944c051ecf733e9c ($16.41).
#
# These are only safe while NO LIVE KEY carries a bare environment-only name. If one
# is ever created (a plain "TTS [Prod]" again), it collides with the legacy label and
# the caller becomes unrecoverable from a name response — so name the key after its
# caller instead. `test_every_live_key_name_is_caller_qualified` states the rule.
_EL_INTELLIGENCE_NAMES = (
    "tts[prod]",
    "tts[dev]",
    "conversational[prod]",
    "conversational[dev]",
)


def _el_caller_of(raw_key):
    """Map an all_api_keys bucket label to 'livekit' | 'intelligence' | None.

    The label is either a key HASH with a suffix hint — "944c051ecf733e9c (...944c)"
    — or a key NAME — "TTS [LiveKit Prod]". ElevenLabs picks per query, and applies a
    rename retroactively to historical windows, so both forms must be matched and
    neither can be assumed stable. None means unrecognised.
    """
    norm = str(raw_key).strip().lower().replace("-", "").replace("_", "").replace(" ", "")
    # 1) Hashes — exact, so they win over any name reading.
    if any(k in norm for k in _EL_LIVEKIT_KEYS):
        return "livekit"
    if any(k in norm for k in _EL_INTELLIGENCE_KEYS):
        return "intelligence"
    # 2) Caller token in the name. Checked before the legacy bare names so that
    #    "TTS [Intelligence Service Prod]" resolves on its caller token rather than
    #    falling through — and so a caller-qualified name never matches a bare one.
    for token, caller in _EL_CALLER_TOKENS:
        if token in norm:
            return caller
    # 3) Legacy bare names from the deleted pre-cutover user keys (all Intelligence
    #    Service — see the tuple's comment for why no LiveKit spend can hide here).
    if any(n in norm for n in _EL_INTELLIGENCE_NAMES):
        return "intelligence"
    return None


# ElevenLabs usage endpoints flap with 500s for minutes at a time (2026-09-29: every
# breakdown_type, one 200 after ~2 min, then 500 again), so one day's store must ride
# out an outage of that length rather than die on the first 5xx.
EL_RETRY_ATTEMPTS = 10
EL_RETRY_BASE_S = 2.0
EL_RETRY_CAP_S = 60.0
EL_RETRY_BUDGET_S = 240.0


class ElevenLabsUnavailable(RuntimeError):
    """A retryable ElevenLabs failure (5xx / 429 / transport) outlasted the budget."""


def _el_get(url, headers, params=None, timeout=90, label="elevenlabs"):
    """GET with bounded retry. Returns the Response for 2xx and non-429 4xx.

    Retries 5xx, 429 (honouring Retry-After) and connection errors / timeouts with
    capped exponential backoff + jitter, until EL_RETRY_ATTEMPTS or
    EL_RETRY_BUDGET_S of waiting is spent; then raises ElevenLabsUnavailable
    naming the last status and the attempt count. A non-429 4xx is returned at
    once — it will not get better by waiting, and callers decide how to degrade.
    """
    t0, waited, last = time.monotonic(), 0.0, "no response"
    for attempt in range(1, EL_RETRY_ATTEMPTS + 1):
        retry_after = None
        try:
            r = requests.get(url, headers=headers, params=params, timeout=timeout)
        except (requests.ConnectionError, requests.Timeout) as e:
            last = f"{type(e).__name__}: {e}"
        else:
            if r.status_code < 500 and r.status_code != 429:
                return r
            last = f"HTTP {r.status_code}"
            if r.status_code == 429:
                try:
                    retry_after = float(r.headers.get("Retry-After"))
                except (TypeError, ValueError):
                    retry_after = None
        if attempt == EL_RETRY_ATTEMPTS:
            break
        # With sleep patched out, monotonic time doesn't advance — count the waits too.
        remaining = EL_RETRY_BUDGET_S - max(time.monotonic() - t0, waited)
        if remaining <= 0:
            break
        if retry_after is not None:
            delay = min(max(retry_after, 0.0), EL_RETRY_CAP_S)
        else:
            d = min(EL_RETRY_BASE_S * 2 ** (attempt - 1), EL_RETRY_CAP_S)
            delay = d / 2 + random.uniform(0, d / 2)
        delay = min(delay, remaining)
        print(f"# {label}: {last} (attempt {attempt}/{EL_RETRY_ATTEMPTS}), "
              f"retrying in {delay:.1f}s", file=sys.stderr)
        time.sleep(delay)
        waited += delay
    raise ElevenLabsUnavailable(
        f"{label}: {last} after {attempt} attempt(s) over "
        f"{max(time.monotonic() - t0, waited):.0f}s")


def elevenlabs_caller_split(day, el_key):
    """Return (agent$, intelligence$, livekit$, note) — the day split by CALLER.

    Two API reads:
      - breakdown_type=has_api_key → False is the ConvAI agent slice (platform-
        internal, no caller key), True is everything called over the API.
      - breakdown_type=all_api_keys → splits that API slice per calling key.

    Degrades rather than failing. `all_api_keys` requires the workspace **Admin**
    role, which a Service Account cannot hold; if the configured key is not an
    admin it 403s. In that case the whole API slice falls back to `intelligence`
    with a note, so the day still reconciles to the workspace total instead of
    silently zeroing a vendor. That fallback is lossy — it absorbs LiveKit's share
    into Intelligence Service — which is why it returns a note rather than storing
    the day as though the split were known.

    An unrecognised key is also folded into `intelligence` (the dominant caller)
    with a stderr warning, so a newly-minted key can never vanish from the total.
    """
    h = {"xi-api-key": el_key}
    nxt = (datetime.date.fromisoformat(day) + datetime.timedelta(days=1)).isoformat()
    params = {"start_unix": _unix(day) * 1000, "end_unix": _unix(nxt) * 1000,
              "metric": "fiat_units_spent", "aggregation_interval": "day",
              "include_workspace_metrics": "true"}

    def _usage(breakdown):
        """(usage, None) on 2xx; (None, 'HTTP <code>') on a non-retryable 4xx.

        Retryable failures are retried inside _el_get and raise when exhausted —
        a transient outage must not be stored as the lossy degraded split below."""
        r = _el_get("https://api.elevenlabs.io/v1/usage/character-stats",
                    h, {**params, "breakdown_type": breakdown}, timeout=90,
                    label=f"elevenlabs character-stats {breakdown} {day}")
        if not r.ok:
            return None, f"HTTP {r.status_code}"
        return (r.json() or {}).get("usage", {}) or {}, None

    # 1) ConvAI (platform-internal) vs API-called, by presence of a caller key.
    by_haskey, err = _usage("has_api_key")
    if by_haskey is None:
        raise RuntimeError(f"elevenlabs has_api_key breakdown unavailable for {day}: "
                           f"{err} (client error, not retried)")
    agent_cents = api_cents = 0.0
    for raw_key, series in by_haskey.items():
        cents = sum(v or 0 for v in series)
        if str(raw_key).strip().lower() in ("false", "0", "none"):
            agent_cents += cents
        else:
            api_cents += cents

    # 2) Split the API slice per calling key.
    note = ""
    intel_cents = livekit_cents = 0.0
    by_key, err = _usage("all_api_keys")
    if by_key is None:
        intel_cents = api_cents
        note = (f"11labs_caller_degraded: all_api_keys {err} (needs workspace "
                "admin — a Service Account key cannot hold it); API-called TTS "
                "attributed wholly to Intelligence Service")
        if api_cents:
            print(f"# {note}", file=sys.stderr)
    else:
        unmapped_cents = 0.0
        for raw_key, series in by_key.items():
            cents = sum(v or 0 for v in series)
            caller = _el_caller_of(raw_key)
            if caller == "livekit":
                livekit_cents += cents
            elif caller == "intelligence":
                intel_cents += cents
            else:
                # Booked to Intelligence Service so the day still reconciles, and
                # recorded in `note` so it lands in the STORED day rather than only
                # on stderr — an unread stderr line is what let a misattribution of
                # this column run for six days.
                unmapped_cents += cents
                intel_cents += cents
                print(f"# 11labs unmapped caller key {raw_key!r} "
                      f"({cents/100.0:.2f}$) folded into Intelligence Service — add it "
                      "to _EL_INTELLIGENCE_KEYS / _EL_LIVEKIT_KEYS, or name the key "
                      "after its caller so the name resolves on its own",
                      file=sys.stderr)
        if unmapped_cents:
            note = (f"11labs_caller_unmapped: ${unmapped_cents/100.0:.2f} under a caller key "
                    "this build cannot attribute; booked to Intelligence Service. Name "
                    "the key after its caller (see Tools/ElevenLabs) or add its hash.")
        # Keep the day reconciling to the workspace total even if the two
        # breakdowns disagree (rounding, or a key-less API call): push any
        # unexplained remainder into the dominant caller.
        drift = api_cents - (intel_cents + livekit_cents)
        if abs(drift) > 1.0:  # cents
            print(f"# 11labs caller drift {drift/100.0:+.2f}$ between has_api_key and "
                  "all_api_keys — folded into Intelligence Service", file=sys.stderr)
        intel_cents += drift

    return (agent_cents / 100.0, max(intel_cents, 0.0) / 100.0,
            max(livekit_cents, 0.0) / 100.0, note)


def elevenlabs_product_split(day, el_key):
    """Back-compat shim: (agent$, raw_tts$) where raw_tts = intelligence+livekit.

    Kept so callers that only need the ConvAI-vs-raw-TTS split (which
    `product_type` answered correctly) don't have to care about the caller
    dimension. New code should call `elevenlabs_caller_split`.
    """
    agent, intel, lk, _note = elevenlabs_caller_split(day, el_key)
    return agent, intel + lk


def voice_costs(day, el_key, agent_day_cost):
    """Return ({uid: voice$}, agent_day$) for the date by walking ElevenLabs ConvAI.

    This is the ElevenLabs ConvAI-AGENT slice ONLY. The per-user walk is unchanged
    from before the agent/LiveKit-TTS split — only the day total it reconciles to
    is now the ConvAI product bucket (`agent_day_cost`) rather than the whole
    ElevenLabs workspace total, since the small raw-TTS slice is a LiveKit-path
    role handled separately.
    """
    h = {"xi-api-key": el_key}
    nxt = (datetime.date.fromisoformat(day) + datetime.timedelta(days=1)).isoformat()
    # list conversations for the day (duration only; userId is in the detail)
    convs, cur = [], None
    while True:
        pr = {"page_size": 100, "call_start_after_unix": _unix(day), "call_start_before_unix": _unix(nxt)}
        if cur:
            pr["cursor"] = cur
        r = _el_get("https://api.elevenlabs.io/v1/convai/conversations", h, pr, timeout=60,
                    label=f"elevenlabs convai conversations {day}")
        r.raise_for_status()
        d = r.json()
        for c in d.get("conversations", []):
            convs.append((c["conversation_id"], (c.get("call_duration_secs", 0) or 0) / 60.0))
        if d.get("has_more") and d.get("next_cursor"):
            cur = d["next_cursor"]
        else:
            break

    def uid_of(cid):
        # A 5xx used to return None here, silently moving the conversation's minutes
        # onto Other. Retryable failures now raise once the retry budget is spent.
        r = _el_get(f"https://api.elevenlabs.io/v1/convai/conversations/{cid}", h, timeout=60,
                    label=f"elevenlabs convai conversation {cid}")
        if r.status_code == 200:
            cic = r.json().get("conversation_initiation_client_data") or {}
            return cic.get("user_id") or (cic.get("dynamic_variables") or {}).get("userId")
        return None

    mins, total = defaultdict(float), 0.0
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(uid_of, cid): (cid, m) for cid, m in convs}
        try:
            for f in as_completed(futs):
                _, m = futs[f]; total += m; u = f.result()
                if u:
                    mins[u] += m
        except BaseException:
            # Don't let the executor drain every queued lookup through its own retry
            # budget before the outage surfaces.
            for f in futs:
                f.cancel()
            raise
    # Day ConvAI voice $ is the product_type=Conversational-AI bucket (passed in);
    # allocate it across ConvAI users by their conversation-minute share.
    cpm = agent_day_cost / total if total else 0.0
    return {u: m * cpm for u, m in mins.items()}, agent_day_cost


def cartesia_day_total(day, rate_per_million):
    """Return (day_cartesia$, note) — DERIVED from actual daily credits × rate.

    Credits are ACTUAL from GET /usage/credits (interval=day); $ is DERIVED
    (credits / 1e6 × rate). Missing key or API failure → ($0, note).
    """
    key = os.environ.get("CARTESIA_ADMIN_API_KEY")
    if not key:
        return 0.0, "cartesia_pending: CARTESIA_ADMIN_API_KEY not set"
    nxt = (datetime.date.fromisoformat(day) + datetime.timedelta(days=1)).isoformat()
    params = {"interval": "day", "start_ts": f"{day}T00:00:00Z", "end_ts": f"{nxt}T00:00:00Z"}
    headers = {"Authorization": f"Bearer {key}", "Cartesia-Version": CARTESIA_VERSION}
    try:
        for a in range(5):
            r = requests.get(f"{CARTESIA_API}/usage/credits", headers=headers, params=params, timeout=60)
            if r.status_code == 429:
                time.sleep(min(float(r.headers.get("Retry-After", 2 ** a)), 30)); continue
            r.raise_for_status(); break
        else:  # every attempt was throttled — a 429 body is not a day of zero credits
            raise RuntimeError("Cartesia usage API still rate-limited after 5 attempts")
        buckets = r.json().get("data") or []
    except Exception as ex:  # noqa: BLE001
        return 0.0, f"cartesia_pending: fetch failed ({type(ex).__name__})"
    credits = sum(float(b.get("credits") or 0) for b in buckets)
    return credits / 1_000_000 * rate_per_million, ""


def fish_day_total(lk_meta, rate_per_million):
    """Return (day_fish$, note) — DERIVED from the agent's own synthesised-character count.

    Fish exposes no usage API (only `GET /wallet/self/api-credit`, a balance snapshot), so
    the day's usage is `tts_characters_by_provider["fish"]` from the LiveKit logs: the
    `Session usage totals` row each session emits, summed over Fish-TTS sessions. $ is
    DERIVED (characters / 1e6 × rate); the rate is per UTF-8 byte, so this under-counts
    non-Latin text. A Fish session that never logged the row is COUNTED and noted, never
    folded in as zero — a day where half the sessions predate the field is partial, not
    cheap. `lk_meta` = {} (log query failed) → ($0, note).
    """
    if not lk_meta:
        return 0.0, "fish_pending: no LiveKit log data (Fish usage is read from the agent logs)"
    sessions = int((lk_meta.get("tts_sessions_by_provider") or {}).get("fish", 0))
    measured = int((lk_meta.get("tts_sessions_measured_by_provider") or {}).get("fish", 0))
    chars = int((lk_meta.get("tts_characters_by_provider") or {}).get("fish", 0))
    dollars = round(chars / 1_000_000 * rate_per_million, 6)
    if sessions and measured < sessions:
        return dollars, (f"fish_partial: {sessions - measured} of {sessions} Fish-TTS sessions logged no "
                         f"tts_characters — UNMEASURED TTS, ${dollars:.4f} covers the {measured} that did")
    return dollars, ""


def _inworld_usage_characters(day, key):
    """Return the day's ACTUAL synthesised TTS characters from Inworld's usage API.

    `GET /usage/v1/records` (experimental) with `services=tts`, one UTC day bucket, summed
    over every model. The key is sent as-is after `Basic ` — Inworld keys are already the
    base64 credential. Raises on any HTTP or shape failure; the caller degrades.
    """
    nxt = (datetime.date.fromisoformat(day) + datetime.timedelta(days=1)).isoformat()
    params = {"startTime": f"{day}T00:00:00Z", "endTime": f"{nxt}T00:00:00Z",
              "granularity": "GRANULARITY_DAY", "services": "tts", "metrics": "characters"}
    headers = {"Authorization": f"Basic {key}"}
    chars, token = 0, None
    for _page in range(50):
        q = dict(params, **({"pageToken": token} if token else {}))
        for a in range(5):
            r = requests.get(f"{INWORLD_API}/usage/v1/records", headers=headers, params=q, timeout=60)
            if r.status_code == 429:
                time.sleep(min(float(r.headers.get("Retry-After", 2 ** a)), 30)); continue
            r.raise_for_status(); break
        else:  # every attempt was throttled — a 429 body is not a page of zero usage
            raise RuntimeError("Inworld usage API still rate-limited after 5 attempts")
        body = r.json()
        for rec in body.get("usageRecords") or []:
            chars += int((rec.get("metrics") or {}).get("characters", {}).get("value") or 0)
        token = body.get("nextPageToken")
        if not token:
            return chars
    raise RuntimeError("Inworld usage API paginated past 50 pages")


def inworld_day_total(day, lk_meta, rate_per_million):
    """Return (day_inworld$, note) — ACTUAL characters × rate, agent-derived as a fallback.

    Primary: the day's characters from Inworld's usage API (`INWORLD_API_KEY`), × the rate.
    It is the vendor's own meter, so it covers every Inworld TTS call on the workspace,
    including sessions that never logged their characters.
    Fallback (no key, or the API fails): `tts_characters_by_provider["inworld"]` from the
    LiveKit logs × the rate, the way `fish_day_total` derives Fish — noted, and a session
    that never logged the figure is counted as UNMEASURED rather than folded in as zero.
    """
    key = os.environ.get("INWORLD_API_KEY")
    why = "INWORLD_API_KEY not set"
    if key:
        try:
            chars = _inworld_usage_characters(day, key)
            return round(chars / 1_000_000 * rate_per_million, 6), ""
        except Exception as ex:  # noqa: BLE001
            why = f"usage API failed ({type(ex).__name__})"
    if not lk_meta:
        return 0.0, f"inworld_pending: {why} and no LiveKit log data to derive from"
    sessions = int((lk_meta.get("tts_sessions_by_provider") or {}).get("inworld", 0))
    measured = int((lk_meta.get("tts_sessions_measured_by_provider") or {}).get("inworld", 0))
    chars = int((lk_meta.get("tts_characters_by_provider") or {}).get("inworld", 0))
    dollars = round(chars / 1_000_000 * rate_per_million, 6)
    if not sessions:
        return dollars, ""  # no Inworld traffic: the agent logs are a complete answer
    note = f"inworld_derived: {why} — ${dollars:.4f} from the agent's own tts_characters"
    if measured < sessions:
        note += (f"; {sessions - measured} of {sessions} Inworld-TTS sessions logged no "
                 f"tts_characters — UNMEASURED TTS")
    return dollars, note


def deepgram_day_total(day, rate_per_hour):
    """Return (day_deepgram$, note) — DERIVED from actual audio-hours × rate.

    The production project is 7e42b984-d5d4-4cdb-aba3-b5bd8408933f ('Aura Health'),
    which DEEPGRAM_OWNER_API_KEY is an Owner on, so usage is normally readable.
    Deepgram is LiveKit-path-only, so zero usage on a day with no LiveKit traffic is
    expected rather than a gap. The note therefore distinguishes absence-of-usage from
    failure-to-read:

      - no note        — every project answered; hours may be 0 (a quiet day) or >0.
      - deepgram_pending — real degradation: missing key, /projects unauthorised or
                         unfetchable, key in no project, NO project usage read
                         succeeded (403 NOT_FOUND / 403 INSUFFICIENT_PERMISSIONS /
                         5xx / exception), or a PARTIAL read where some project
                         failed and the total may undercount.

    Never returns a bare $0-with-no-note for a failed read — that would be
    indistinguishable from a quiet day and could be persisted to the append-only
    BigQuery store. A zero that is suspicious for the *other* reason — no Deepgram $
    on a day that had LiveKit minutes — is the caller's cross-check, since this helper
    doesn't see LiveKit volume. Hours are ACTUAL; $ is DERIVED.
    """
    key = os.environ.get("DEEPGRAM_OWNER_API_KEY") or os.environ.get("DEEPGRAM_API_KEY")
    if not key:
        return 0.0, "deepgram_pending: DEEPGRAM_OWNER_API_KEY not set"
    headers = {"Authorization": f"Token {key}"}

    def _get(path, params=None):
        for a in range(5):
            r = requests.get(f"{DEEPGRAM_API}{path}", headers=headers, params=params or {}, timeout=60)
            if r.status_code == 429:
                time.sleep(min(float(r.headers.get("Retry-After", 2 ** a)), 30)); continue
            return r
        return r

    try:
        pr = _get("/v1/projects")
        if pr.status_code in (401, 403):
            return 0.0, "deepgram_pending: key not authorised for /projects"
        projects = (pr.json() or {}).get("projects") or []
    except Exception as ex:  # noqa: BLE001
        return 0.0, f"deepgram_pending: projects fetch failed ({type(ex).__name__})"
    if not projects:
        return 0.0, ("deepgram_pending: key is a member of no project — expected the prod "
                     "project 7e42b984-… ('Aura Health')")

    # Track read outcomes per project, not just the hours total: a skipped project
    # (403/404/5xx/exception) contributes 0 hours exactly like a quiet day would, so
    # without this a FAILED read would masquerade as a legitimate $0 — and that
    # undercount can be written into the append-only BigQuery store, then refused on
    # re-run because the partition already exists.
    total_hours = 0.0
    read_ok = False
    failures = []
    for p in projects:
        pid = p.get("project_id") or "?"
        try:
            ur = _get(f"/v1/projects/{pid}/usage", {"start": day, "end": day})
        except Exception as ex:  # noqa: BLE001
            failures.append(f"{pid[:8]}:{type(ex).__name__}")
            continue
        # 403 INSUFFICIENT_PERMISSIONS / 404 NOT_FOUND on this project, or any other
        # non-OK status — a real read failure, not an absence of usage.
        if not ur.ok:
            failures.append(f"{pid[:8]}:{ur.status_code}")
            continue
        read_ok = True
        for row in (ur.json() or {}).get("results") or []:
            for k in ("total_hours", "hours"):
                if k in row:
                    try:
                        total_hours += float(row.get(k) or 0)
                    except (TypeError, ValueError):
                        pass
                    break
    if not read_ok:
        return 0.0, ("deepgram_pending: no project usage read succeeded "
                     f"({', '.join(failures)})")
    if failures:
        # Partial read: we have SOME hours but at least one project failed, so the
        # total may be undercounted. Flag it rather than store a silent undercount.
        return total_hours * rate_per_hour, ("deepgram_pending: partial usage read, total "
                                             f"may undercount ({', '.join(failures)})")
    if total_hours <= 0:
        # Every project answered and reported no audio — Deepgram is LiveKit-path-only,
        # so this is a legitimate $0 (no LiveKit traffic), NOT degradation. Emitting
        # `deepgram_pending` here would raise a false degraded-vendor warning in the
        # store + the daily/weekly reports on every quiet day, so return no note.
        return 0.0, ""
    return total_hours * rate_per_hour, ""


def livekit_costs(day, rate=None, plan=None, minutes_before=0.0):
    """Back-compat 5-tuple for callers that want ONE LiveKit dollar figure for a day:
    ``({uid: minutes}, total_minutes, day_livekit$, note, meta)``.

    `day_livekit$` is the subscription accrual for that day PLUS any agent-session
    overage — i.e. what LiveKit actually bills for the day, not `minutes x rate`.
    Callers outside this module (`unit_costs.py`, `eng:report-aurie-daily-costs`,
    `eng:report-aurie-weekly-costs`) keep their existing call shape and inherit the
    invoice model for free; a positional `rate` still overrides the overage rate.

    `minutes_before` defaults to 0, which under-states overage for a live day late in a
    month that is already past the allotment. That is deliberate for these callers: they
    walk the logs for days the store does not hold (normally just today's partial), have
    no month-to-date context, and 0 is the honest floor rather than a guess. The stored
    path in `main()` reads the real month-to-date from BigQuery.

    Degrades to a note rather than raising, because these are best-effort report paths.
    """
    plan = plan or livekit_plan.DEFAULT_PLAN
    try:
        by_user, total_min, meta = livekit_minutes_for_day(day)
    except LiveKitUnavailable as ex:
        return {}, 0.0, 0.0, str(ex), {}
    # The day's FULL LiveKit cost — consumed credits plus the capacity paid for and not
    # used. Their sum is the day's share of the subscription, which is what a per-day
    # vendor column should show: the business pays it whether or not anyone talked.
    #
    # It matches what a STORED day sums to (voice_livekit + voice_livekit_platform
    # across all rows, the latter sitting on the reconciling row), so a window mixing
    # stored days with this live fallback stays on one scale.
    # Billed minutes, as the stored path prices them; total_min is returned as logged.
    before = minutes_before * LIVEKIT_LOG_TO_BILLED
    billed = total_min * LIVEKIT_LOG_TO_BILLED
    consumed = livekit_consumed_day(plan, billed, rate=rate)
    unused = livekit_plan.unused_capacity_usd(plan, day, before, billed)
    pools = livekit_plan.session_proxy_overage_usd(plan, before, billed, day)
    return by_user, total_min, consumed + unused + pools, "", meta


def livekit_minutes_for_day(day):
    """Return ({uid: minutes}, total_minutes, meta).

    Per-user LiveKit minutes + total from the forwarded GCP logs. Minutes ONLY — the
    caller decides the dollars, because LiveKit's are not a per-minute price: the
    subscription is a fixed monthly fee and the per-minute figure is an OVERAGE rate
    that applies to nothing inside the allotment. Auth failure → empty attribution
    with a note (LiveKit path simply not reported that day). `meta` carries the
    per-TTS-provider minute buckets (`by_user_tts`, `minutes_by_provider`) used to
    allocate the ElevenLabs-TTS and Cartesia $ to only the sessions that used each
    provider; it is {} when the log query failed.
    """
    try:
        by_user, total_min, meta = livekit_minutes.day_minutes_by_user(day)
    except livekit_minutes.AuthError as ex:
        raise LiveKitUnavailable(f"livekit_pending: GCP auth failed ({ex})") from ex
    except livekit_minutes.TruncatedReadError as ex:
        raise LiveKitTruncated(f"livekit_truncated: {ex}") from ex
    except Exception as ex:  # noqa: BLE001
        raise LiveKitUnavailable(
            f"livekit_pending: log query failed ({type(ex).__name__}: {ex})") from ex
    mbp = meta.get("minutes_by_provider", {})
    print(f"# livekit: auth={meta['auth_source']} entries={meta['entries']} "
          f"sessions={meta['sessions']} total_min={total_min:.1f} "
          f"no_provider_sessions={meta.get('sessions_without_provider', 0)} "
          f"min_by_tts={{{', '.join(f'{p}:{m:.1f}' for p, m in mbp.items() if m)}}}",
          file=sys.stderr)
    # The per-voice split is only worth a line when a provider actually ran more than one
    # voice that day (an experiment arm); otherwise it repeats the provider figure.
    for provider, by_voice in (meta.get("minutes_by_provider_voice") or {}).items():
        voices = {v: m for v, m in by_voice.items() if m}
        if len(voices) > 1:
            print(f"# livekit voices[{provider}]: "
                  f"{{{', '.join(f'{v}:{m:.1f}' for v, m in sorted(voices.items()))}}} "
                  f"(no_voice_sessions={meta.get('sessions_without_voice_id', 0)}, "
                  f"requested_voice_only={meta.get('sessions_with_requested_voice_only', 0)})",
                  file=sys.stderr)
    return by_user, total_min, meta


def mtd_livekit_minutes_before(day, table, project):
    """Month-to-date LiveKit minutes stored for the days BEFORE `day`.

    The denominator for the marginal overage: the allotment is monthly, so whether
    today's minutes are billable depends on how much of the month is already spent.
    Reads the stored `livekit_minutes` rather than re-walking the logs, which would
    be one query per prior day against a 60/min read cap.

    Raises on a query failure rather than defaulting to 0. Zero would silently place
    the whole month back inside the allotment, so a late-month day that IS in overage
    would book $0 — the same shape of silent undercount this card exists to remove.
    """
    from bq import query
    sql = f"""
    SELECT COALESCE(SUM(livekit_minutes), 0) AS minutes
    FROM `{table}`
    WHERE date >= DATE_TRUNC(DATE(@day), MONTH) AND date < DATE(@day)
    """
    rows = query(sql, project, params={"day": day})
    return float(rows[0]["minutes"]) if rows else 0.0


def mtd_inference_drawdown_before(day, table, project):
    """Month-to-date LiveKit Inference credit drawdown stored for the days BEFORE `day`.

    The twin of `mtd_livekit_minutes_before`, and for the same reason: the credit block
    is monthly, so whether today's STT spend is billable depends on how much of the block
    the month has already drawn.

    **It has to be stored — there is nothing to derive it from.** `voice_deepgram` is $0
    inside the block by design, so the dollar column erases the very quantity needed to
    reconstruct how full the block is. That is why this reads a column rather than
    recomputing from the day's own figures.

    Raises on a query failure rather than defaulting to 0, same as its twin: zero would
    place the whole month back inside the block, so a day that IS past it books $0 —
    the silent undercount this column exists to remove.
    """
    from bq import query
    sql = f"""
    SELECT COALESCE(SUM(deepgram_inference_drawdown), 0) AS drawdown
    FROM `{table}`
    WHERE date >= DATE_TRUNC(DATE(@day), MONTH) AND date < DATE(@day)
    """
    rows = query(sql, project, params={"day": day})
    return float(rows[0]["drawdown"]) if rows else 0.0


def livekit_unused_label(unused_usd):
    """Run-log label for the day's LiveKit unused-capacity delta. The value is a signed
    month-to-date delta (livekit_plan.unused_capacity_usd): a day consuming more than its
    share of the accrual goes negative and reverses capacity earlier days booked as unused,
    so "nobody used it" would misread that day."""
    if round(unused_usd, 2) < 0:
        return "reverses earlier unused capacity: consumption above today's accrual"
    return "paid-for capacity nobody used"


def _fmt_rate(r):
    """Format a credit rate for the run log. `build` is a valid --livekit-plan and has no
    subscription, so credit_rate_per_min returns None there — formatting it with :.4f
    raised TypeError before the day was written."""
    return f"${r:.4f}/min" if r is not None else "n/a ($0 plan buys no capacity)"


def deepgram_inference_day(plan, stt_minutes, mtd_drawdown_before, *, model="deepgram/nova-3",
                           multilingual=False):
    """Return (billed$, drawdown$, note) for STT served by LiveKit Inference.

    **billed$ is $0 while the day's drawdown stays inside the plan's included credit
    block, and that is not an approximation — it is the only non-double-counting answer.**
    The credit block is part of the subscription, and PROD-5998 already books the whole
    subscription (consumed + unused). Pricing these minutes again would have put $12.65
    on top of a $500 September invoice.

    So the column carries only what is genuinely incremental: drawdown BEYOND the block.
    `drawdown$` is reported separately so the block filling is visible before it
    overflows, rather than the first sign being a bigger bill.

    Marginal against the month to date, same shape as the agent-session overage: a
    month's daily values sum to the month's overage exactly.
    """
    if model not in livekit_plan.INFERENCE_RATES:
        return 0.0, 0.0, f"deepgram_inference_pending: no rate card for {model}"
    # The band/plan lookup stays in livekit_plan — the rate card is the plan of record,
    # and a second reader of it is a second place to get the multilingual band wrong.
    day_draw = livekit_plan.inference_usd(model, plan, stt_minutes,
                                          multilingual=multilingual)
    pool = "inference_credits_usd"
    before = livekit_plan.overage_usd(pool, plan, mtd_drawdown_before)
    after = livekit_plan.overage_usd(pool, plan, mtd_drawdown_before + day_draw)
    return max(0.0, after - before), day_draw, ""


def inference_note(minutes, drawdown, billed, block, measured_sessions, *, mixed=False):
    """One line saying which of the credit block's three states the day is in.

    A single note that always reads "$0, inside the block" makes an overflow look like
    another included-block zero — the column and the note disagreeing is the same silent
    shape as an unexplained zero, so the three states get three notes.
    """
    also = "some STT also billed direct to Deepgram; " if mixed else ""
    if measured_sessions == 0:
        return (f"deepgram_via_livekit_inference: {also}transport is LiveKit Inference but "
                f"no session logged an STT duration, so the drawdown is UNMEASURED. Billed "
                f"$0 — the credit block is inside the subscription, which is booked in full "
                f"either way.")
    if billed > 0:
        return (f"deepgram_inference_overage: {also}{minutes:,.1f} STT min = "
                f"${drawdown:,.2f} drawn, PAST the ${block:,.2f} included credit block — "
                f"${billed:,.2f} billed beyond the subscription.")
    return (f"deepgram_via_livekit_inference: {also}{minutes:,.1f} STT min = "
            f"${drawdown:,.2f} drawn against the ${block:,.2f} included credit block "
            f"(inside the subscription; billed $0 to avoid double-counting)")


def stt_transport_split(meta):
    """Return (on_inference, inference_minutes, direct_minutes, by_transport).

    STT is billed by whoever the transport routed it through, so the transport decides
    which source is authoritative — read the wrong one and you get a well-formed $0.
    The split comes from the agent's own structured log fields, never from a regex over
    a message, so a wording change cannot silently reclassify a day.

    A transport seen with NO measured duration still counts as inference: the session
    config predates the duration field, and trusting the absent measurement over the
    stated transport is what turns a routing change into a silent zero.
    """
    by_transport = dict(meta.get("stt_minutes_by_transport") or {})
    total = float(meta.get("stt_minutes") or 0.0)
    measured = int(meta.get("stt_sessions_measured") or 0)
    inference = sum(v for t, v in by_transport.items() if "inference" in t)
    direct = total - inference
    on_inference = inference > 0 or (
        measured == 0 and any("inference" in t for t in by_transport))
    return on_inference, inference, direct, by_transport


def unexplained_deepgram_zero(day, day_deepgram, livekit_minutes, note, by_transport):
    """Return a NOTE for a $0 Deepgram day nothing explains, else None.

    A $0 STT day with real LiveKit traffic and no note is the shape the Inference cutover
    produced: correct-looking, indistinguishable from a vendor that had nothing to bill,
    unnoticed for four days. The column must read $0 *because something says so*.

    **It tags; it does not refuse.** An earlier cut raised `SystemExit` here, and the blast
    radius was wrong: an *unexplained* zero stopped the whole day — LLM and ElevenLabs included — over one
    vendor's unknown. Measured on 2026-09-14, that blocked the entire cost store because
    the voice agent had not yet shipped the STT log fields. Halting the financial pipeline
    to report an unknown in one column is the withholding-the-deliverable shape; writing
    the row with the zero **tagged and counted** is the rule this repo already applies
    wherever the output persists. A *failed read* is different: it is retryable, so
    `assert_vendor_reads` refuses the day rather than storing the leg's $0.

    A note is still a real change from the defect, which carried none at all.
    """
    if day_deepgram > 0 or livekit_minutes <= 0 or note:
        return None
    return (
        f"deepgram_transport_unknown: voice_deepgram is $0 on {day}, a day with "
        f"{livekit_minutes:,.1f} LiveKit minutes, and nothing explains why. Either the "
        f"Deepgram read failed silently, or STT moved to a transport this store does not "
        f"recognise. Transports seen in the logs: {by_transport or '(none)'}. The day is "
        f"STORED with the zero tagged — read it as UNMEASURED STT, never as $0 of speech."
    )


def assert_vendor_reads(day, reads, *, allow_unmeasured=False):
    """Refuse to PERSIST a day where a vendor's usage read failed while it carried traffic.

    `reads` is [(vendor, note, traffic_minutes)], where traffic is None when it is unknown
    (the LiveKit log read failed). A `<vendor>_pending` note means the read
    failed (missing key, 401/403, 5xx, rate limit, partial read), and the leg then returns
    $0 — a figure the store would write as a measurement, since notes are printed but not
    persisted. On a day the vendor carried traffic that $0 is wrong, so the day is refused:
    the read is retryable, and a re-run once the key or the vendor recovers stores it
    correctly. Unknown traffic counts as traffic. A failed read on a day with no traffic for
    that vendor changes nothing and is left as a note. `--allow-unmeasured-vendor` persists the day deliberately.
    """
    failed = [(v, n, m) for v, n, m in reads
              if n.startswith(f"{v}_pending") and (m is None or m > 0)]
    if not failed:
        return
    listed = "; ".join(
        f"{v} ({'traffic unknown' if m is None else f'{m:,.1f} min of traffic'}): {n}"
        for v, n, m in failed)
    msg = (f"REFUSING to store {day}: a vendor usage read failed on a day that vendor "
           f"carried traffic, so its column would be written as $0 — {listed}. Fix the "
           f"key or wait for the vendor, then re-run; or pass --allow-unmeasured-vendor to "
           f"persist the day with those columns UNMEASURED.")
    if allow_unmeasured:
        print(f"WARNING: {msg.replace('REFUSING to store', 'Storing (--allow-unmeasured-vendor)')}",
              file=sys.stderr)
        return
    raise SystemExit(msg)


def inference_tts_note(lk_meta):
    """Return a NOTE when any session's TTS ran through LiveKit Inference, else None.

    The Inference-credit leg prices STT minutes only (`deepgram_inference_day`), and each
    TTS vendor column reads that vendor's own meter. A voice moved onto `inference.TTS`
    is billed by LiveKit, so its vendor's usage API reads $0, and its drawdown is not in
    `deepgram_inference_drawdown` either — the block fills faster than the store says.
    Every Aurie voice is a clone on a direct plugin today, so this should never fire.
    Credits cannot be split by model from the logs, so the day is stored and TAGGED.
    """
    by_provider = {p: n for p, n in (lk_meta.get("tts_inference_sessions_by_provider") or {}).items()
                   if n}
    if not by_provider:
        return None
    listed = ", ".join(f"{p}={n}" for p, n in sorted(by_provider.items()))
    return (f"tts_via_livekit_inference: {sum(by_provider.values())} session(s) synthesised TTS "
            f"through LiveKit Inference ({listed}). That spend draws on the Inference credit "
            f"block, which this store prices as Deepgram STT only, while the vendor's own usage "
            f"API reads $0 for it — those voices' TTS is MISSING from the vendor columns and "
            f"from deepgram_inference_drawdown. Price Inference TTS before trusting the split.")


def livekit_consumed_day(plan, minutes, rate=None):
    """Return the day's LiveKit cost: minutes x the plan's CREDIT rate.

    The subscription is a block of prepaid credits, so a used minute costs what the block
    paid for it (Scale: $500 / 50,000 = $0.01/min). Stable by construction — it does not
    move with how busy the month was, which is what a per-minute cost question wants.

    This supersedes the daily-accrual allocation: spreading the subscription across a
    day's users made a quiet day look expensive per user and made $/min swing with the
    reporting window (September read $0.0681/min month-to-date against $0.0318
    projected — the same month, two answers).

    `rate` overrides the credit rate for a what-if; it does not change the invoice.
    """
    r = livekit_plan.credit_rate_per_min(plan) if rate is None else rate
    return minutes * (r or 0.0)


def livekit_day_dollars(plan, day, minutes_before, minutes_today,
                        factor=LIVEKIT_LOG_TO_BILLED):
    """Return (consumed$, unused_capacity$, pool_overage$) for one day, from BILLED minutes.

    `minutes_before` / `minutes_today` are logged minutes; all three figures are priced on
    logged x `factor`, so the unused capacity and the overage crossing move with the
    consumed dollars and a month still reconciles to the invoice.
    """
    before, today = minutes_before * factor, minutes_today * factor
    return (livekit_consumed_day(plan, today),
            livekit_plan.unused_capacity_usd(plan, day, before, today),
            livekit_plan.session_proxy_overage_usd(plan, before, today, day))


def livekit_platform_day_total(day, plan):
    """Return (day_platform$, note) — the LiveKit subscription accrued for ONE day.

    LiveKit bills a flat monthly subscription that BUYS the allotments; it is a real
    cost the business pays whether or not they are used, so it belongs in a per-user
    cost store. Spread evenly across the calendar month rather than as a
    month-to-date effective rate, because this store is append-only per day: an
    effective rate computed against partial-month minutes would have to be restated
    every subsequent day, while a fixed daily accrual reconciles to the invoice at
    month end and never rewrites a stored row.

    The whole month's fee is booked even on a day with no traffic — the fee is owed
    regardless. Such a day's amount lands on the reconciling `Other` row, because
    there are no minutes to allocate it by.
    """
    y, m, _ = (int(x) for x in day.split("-"))
    return livekit_plan.subscription_usd(plan) / calendar.monthrange(y, m)[1], ""


def livekit_overage_day(plan, minutes_before, minutes_today, rate=None):
    """Return the agent-session OVERAGE $ attributable to `minutes_today`.

    Marginal, not cumulative: the day's overage is the month-to-date overage
    INCLUDING today minus the month-to-date overage before today, so a month's daily
    values sum to the month's overage exactly and no day double-counts.

    Returns 0.0 for every day of a month that stays inside the allotment, which is
    every month so far (Sept 2026: 7,344 of 50,000). That zero is the correct answer
    and the entire point — the previous model charged $0.01 for each of those minutes.
    """
    pool = "agent_session_minutes"
    allot = livekit_plan.included(pool, plan)
    r = livekit_plan.overage_per_unit(pool, plan) if rate is None else rate
    before = max(0.0, minutes_before - allot) * r
    after = max(0.0, minutes_before + minutes_today - allot) * r
    return max(0.0, after - before)


def allocate_by_minutes(vendor_total, minutes_by_user, total_minutes):
    """Spread a vendor's daily-workspace $ across users by LiveKit minute share.

    Returns {uid: $}. When there are no LiveKit minutes for the day, nothing is
    attributed (the full amount lands in the Other row via the caller).
    """
    if vendor_total <= 0 or total_minutes <= 0:
        return {}
    rate = vendor_total / total_minutes
    return {u: m * rate for u, m in minutes_by_user.items()}


LEDGER_TABLE = "aura-main.llm.llm_call"
# Every LiveKit coach user with a reply, and their reply characters: the last call of each
# dialogue that stored one (a retry writes a second row). The createdAt range is the
# partition filter the table requires.
LEDGER_REPLY_CHARS_SQL = f"""
WITH calls AS (
  SELECT userId, dialogueId, replyChars, createdAt FROM `{LEDGER_TABLE}`
  WHERE createdAt >= TIMESTAMP(@day) AND createdAt < TIMESTAMP_ADD(TIMESTAMP(@day), INTERVAL 1 DAY)
    AND platform = 'livekit' AND prompterName = 'coach-prompter' AND userId IS NOT NULL),
replies AS (
  SELECT userId, replyChars FROM calls WHERE replyChars IS NOT NULL
  QUALIFY ROW_NUMBER() OVER (PARTITION BY dialogueId ORDER BY createdAt DESC) = 1)
SELECT u.userId AS user_id, COALESCE(SUM(r.replyChars), 0) AS chars
FROM (SELECT DISTINCT userId FROM calls) u LEFT JOIN replies r USING (userId)
GROUP BY u.userId
"""


def ledger_reply_chars(day, project):
    """{uid: reply chars} for the day's LiveKit coach calls, from the llmCall ledger in
    BigQuery. A user with calls but no stored reply is present with 0. Raises on failure."""
    from bq import query
    rows = query(LEDGER_REPLY_CHARS_SQL, project, params={"day": day}, max_bytes=5 * 10**9)
    return {r["user_id"]: float(r["chars"] or 0) for r in rows}


# ENG-1859: the LiveKit log read for 2026-10-04 came back short (147 users against the
# ledger's 228; a re-read found 263). Normal days run at >= 1.0, because the logs also see
# sessions with no LLM turn.
MIN_LIVEKIT_LEDGER_USER_RATIO = 0.85


def assert_livekit_users_match_ledger(day, log_users, ledger_users, *, allow_short=False):
    """Refuse a day whose LiveKit-log user count is below MIN_LIVEKIT_LEDGER_USER_RATIO of the
    ledger's LiveKit coach users: every minute-allocated column would be built on a short read."""
    if not ledger_users or log_users >= MIN_LIVEKIT_LEDGER_USER_RATIO * ledger_users:
        return
    msg = (f"REFUSING to store {day}: the LiveKit logs found {log_users} users with minutes, "
           f"below {MIN_LIVEKIT_LEDGER_USER_RATIO:.2f}x the {ledger_users} LiveKit coach users in "
           f"the llmCall ledger, so the log read is probably short. Re-run later, or pass "
           f"--allow-short-livekit-log to persist it deliberately.")
    if not allow_short:
        sys.exit(msg)
    print(f"WARNING: {msg.replace('REFUSING to store', 'Storing (--allow-short-livekit-log)')}",
          file=sys.stderr)


def allocate_tts_by_chars(totals, by_user_tts, chars):
    """Split each TTS provider's day total by reply characters (ENG-1859): vendors bill
    characters, and characters per minute vary by user and over time.

    The ledger has no provider, so a user's characters are split across providers by their
    minute share on each: chars[u][p] = chars[u] * min[u][p] / min[u]. A user with minutes on
    a provider but no ledger characters keeps their minute share of that provider's total;
    the characters split the rest. Returns ({provider: {uid: $}}, users without chars, their $)."""
    min_u = defaultdict(float)
    for p in totals:
        for u, m in by_user_tts.get(p, {}).items():
            min_u[u] += m
    out, no_chars, no_chars_usd = {}, set(), 0.0
    for p, total in totals.items():
        mins = {u: m for u, m in by_user_tts.get(p, {}).items() if m > 0}
        pm = sum(mins.values())
        if total <= 0 or pm <= 0:
            out[p] = {}
            continue
        weights = {u: chars[u] * m / min_u[u] for u, m in mins.items() if chars.get(u, 0) > 0}
        missing = {u: total * m / pm for u, m in mins.items() if u not in weights}
        no_chars |= set(missing)
        no_chars_usd += sum(missing.values())
        rest, w = total - sum(missing.values()), sum(weights.values())
        out[p] = {**missing, **{u: rest * x / w for u, x in weights.items()}} if w else missing
    return out, no_chars, no_chars_usd


# Per-user columns whose source keeps its records for a limited time (days). A --force
# re-store past that keeps the stored split rather than moving it to `Other` (ENG-1859).
EXPIRING_SOURCES = {"voice_11labs_agent": ("ElevenLabs ConvAI", 10)}


def source_expired(day, retention_days, now=None):
    """Whether part of `day` is already past the source's retention: the day falls before the
    first UTC midnight after now - retention (the split report-elevenlabs-user-cost uses)."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return datetime.date.fromisoformat(day) <= (now - datetime.timedelta(days=retention_days)).date()


def stored_split(table, project, day, col):
    """({uid: stored $}, stored day total incl. `Other`) for one column of a stored day."""
    from bq import query
    rows = query(f"SELECT user_id, {col} AS v FROM `{table}` WHERE date = DATE(@day)",
                 project, params={"day": day})
    split = {r["user_id"]: float(r["v"] or 0) for r in rows
             if r["user_id"] != OTHER_USER and float(r["v"] or 0) > 0}
    return split, sum(float(r["v"] or 0) for r in rows)


def keep_stored_split(col, source, stored, stored_total, fresh_total):
    """The stored per-user split for `col`, kept as is when its day total matches the fresh
    vendor total within a cent, otherwise scaled to it. Returns (split, note)."""
    if not stored:
        return None, (f"{col}: {source} records have expired and no stored per-user split "
                      f"exists, so the day's ${fresh_total:.2f} lands on `Other`")
    if abs(stored_total - fresh_total) < 0.01:
        return dict(stored), f"{col}: {source} records have expired; kept the stored per-user split"
    f = fresh_total / stored_total if stored_total else 0.0
    return ({u: v * f for u, v in stored.items()},
            f"{col}: {source} records have expired; scaled the stored per-user split from "
            f"${stored_total:.2f} to the fresh total ${fresh_total:.2f}")


def mixpanel_active_zero_cost_users(engaged, active, cost_bearing_users, mp_ok):
    """Which Mixpanel-active users need a $0 activity row (PROD-6143).

    A user genuinely active in Mixpanel today but billed nothing — no cost
    today, or billed by an async pipeline on an adjacent day — is otherwise
    indistinguishable from a user never reached at all: without an explicit
    row, "no row" and "inactive" read the same to every downstream reader.
    Returns the set to ADD to the cost-bearing user set; never removes
    anyone already there (a cost-bearing user keeps their real row
    regardless of engaged/active).

    Gated on `mp_ok`: when Mixpanel failed, `engaged`/`active` are already
    empty sets by construction (see day_engagement's degrade path), so this
    naturally returns empty rather than needing its own separate check —
    but the explicit gate makes that non-obvious invariant visible here
    rather than relying on an empty-set coincidence.
    """
    if not mp_ok:
        return set()
    return (engaged | active) - set(cost_bearing_users)


def sub_state(uid, day, tok):
    """Authoritative AI subscription state on `day`: paid | trial | grace | free.

    Uses Aura /subscriptions/list — NOT the OpenAI aiPremium metadata, which
    mis-tags some paying users (cross-service sync gap).
    """
    try:
        r = requests.post(f"https://services.aurahealth.io/subscriptions/list/{uid}",
                          headers={"Authorization": f"Bearer {tok}", "User-Agent": user_agent(), "Content-Type": "application/json"},
                          data="{}", timeout=60)
        subs = r.json() if r.status_code == 200 else []
    except Exception:
        subs = []
    best = None
    for s in (subs if isinstance(subs, list) else []):
        if not (str(s.get("id", "")).endswith("-ai") or s.get("type") == "ai"):
            continue
        pa = (s.get("purchaseAt") or s.get("createdAt") or "")[:10]
        ea = (s.get("expirationAt") or "")[:10]
        if not (pa and ea and pa <= day <= ea):
            continue
        if s.get("paymentPending") or s.get("graceAt"):
            lab = "grace"
        elif s.get("trial"):
            lab = "trial"
        elif s.get("free"):
            lab = "free"
        else:
            lab = "paid"
        best = lab if best is None or _TIER_RANK[lab] < _TIER_RANK[best] else best
    return best or "free"


def _apply_caller_correction(rows):
    """Move the `Other` row's mis-booked voice_11labs_livekit into
    voice_11labs_intelligence (ENG-982), leaving per-user rows alone.

    Per-user rows are allocated to LiveKit sessions that really used ElevenLabs
    TTS, so they ARE LiveKit (e.g. $2.34 on 2026-07-07, a manual provider test).
    The `Other` row is the unattributable remainder, and LiveKit-path $ is only
    ever booked against a session that used the provider — so the remainder is
    Intelligence Service. Returns (rows, moved$, kept$).
    """
    out, moved, kept = [], 0.0, 0.0
    for r in rows:
        lk = float(r.get("voice_11labs_livekit") or 0)
        if r.get("user_id") == OTHER_USER:
            moved += lk
            out.append({**r, "voice_11labs_intelligence": round(lk, 4),
                        "voice_11labs_livekit": 0.0})
        else:
            kept += lk
            out.append({**r, "voice_11labs_intelligence": 0.0,
                        "voice_11labs_livekit": round(lk, 4)})
    return out, moved, kept


def _migrate_old_csv(path):
    """If `path` carries an OLDER header, rewrite it in place with the current
    multi-vendor + ElevenLabs-split schema (COLS).

    - ORIGINAL 6-col (OLD_COLS): pre-migration ElevenLabs was 100% ConvAI agent,
      so voice_11labs_agent = voice_cost and every other voice_* column = 0.
    - INTERMEDIATE 10-col (MID_COLS, single voice_11labs): predates the
      agent/LiveKit-TTS split, so voice_11labs_agent = voice_11labs,
      voice_11labs_livekit = 0, and the LiveKit-path vendor columns carry over.

    Returns True if a migration happened (or the file already matches the current
    schema), False if the file doesn't exist. Exits on an unrecognised header.
    Preserves voice_cost / total_cost and idempotency (date-set unchanged).
    """
    if not os.path.exists(path):
        return False
    with open(path, newline="") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            return False  # empty file — will get a fresh header on write
        rows = list(csv.DictReader(f, fieldnames=header))
    if header == COLS:
        return True  # already current schema
    if header == OLD_COLS:
        migrated = [{
            "date": r["date"], "user_id": r["user_id"], "llm_cost": r["llm_cost"],
            "voice_11labs_agent": round(float(r.get("voice_cost") or 0), 4),
            "voice_11labs_intelligence": 0.0,
            "voice_11labs_livekit": 0.0, "voice_cartesia": 0.0,
            "voice_deepgram": 0.0, "voice_livekit": 0.0,
            "voice_cost": r["voice_cost"], "total_cost": r["total_cost"],
            "subscription_state": r["subscription_state"],
        } for r in rows]
        detail = "voice_11labs_agent=voice_cost, other voice_* 0"
    elif header == MID_COLS:
        migrated = [{
            "date": r["date"], "user_id": r["user_id"], "llm_cost": r["llm_cost"],
            "voice_11labs_agent": round(float(r.get("voice_11labs") or 0), 4),
            "voice_11labs_intelligence": 0.0,
            "voice_11labs_livekit": 0.0,
            "voice_cartesia": r.get("voice_cartesia") or 0.0,
            "voice_deepgram": r.get("voice_deepgram") or 0.0,
            "voice_livekit": r.get("voice_livekit") or 0.0,
            "voice_cost": r["voice_cost"], "total_cost": r["total_cost"],
            "subscription_state": r["subscription_state"],
        } for r in rows]
        detail = "voice_11labs_agent=voice_11labs, voice_11labs_livekit=0, LiveKit-path cols carried over"
    elif header == PRE_PLATFORM_COLS:
        # Add the two new columns at 0.0. Zero is honest rather than reconstructed:
        # these rows genuinely carry no platform fee, and their minute count is only
        # RECOVERABLE (voice_livekit / the overage rate), not stored. `rebook_livekit.py`
        # does that recovery deliberately and reports it; a migration that silently
        # back-derived minutes here would make a reconstruction look like a record.
        migrated = [{**r, "voice_livekit_platform": 0.0, "livekit_minutes": 0.0}
                    for r in rows]
        detail = ("added voice_livekit_platform + livekit_minutes at 0 — run "
                  "rebook_livekit.py to restate historical days from the invoice")
    elif header == PRE_INWORLD_COLS:
        # Zero is "never booked", not "no spend": Inworld served LiveKit sessions before
        # the column existed and those dollars reached no column at all.
        migrated = [{**r, "voice_inworld": 0.0} for r in rows]
        detail = ("added voice_inworld at 0 — Inworld TTS before the column was never "
                  "booked; re-store those days with --force to record it")
    elif header == PRE_FISH_COLS:
        # Zero is exact, not reconstructed: no Fish traffic predates the column (the
        # provider shipped with it), so these rows genuinely carry no Fish spend.
        migrated = [{**r, "voice_fish": 0.0} for r in rows]
        detail = "added voice_fish at 0 — no Fish traffic predates the column"
    elif header == PRE_DRAWDOWN_COLS:
        # Zero is honest rather than reconstructed. These rows predate the column, and
        # the drawdown is not recoverable from any dollar they carry -- `voice_deepgram`
        # is $0 inside the block by construction. Re-store the affected days to record it.
        migrated = [{**r, "deepgram_inference_drawdown": 0.0} for r in rows]
        detail = ("added deepgram_inference_drawdown at 0 — re-store the days since the "
                  "Inference cutover to record the real drawdown")
    elif header == PREV_COLS:
        # Two migrations at once: add the engagement columns AND apply the ENG-982
        # caller correction, since a PREV_COLS file also predates it.
        staged = [{**r, "engaged": "", "active": "", "activity_source": ""} for r in rows]
        migrated, moved, kept = _apply_caller_correction(staged)
        detail = ("added blank engaged/active/activity_source — run backfill_engagement.py "
                  f"to fill; ENG-982 caller correction moved ${moved:.2f} to "
                  f"voice_11labs_intelligence, kept ${kept:.2f} as LiveKit")
    elif header == PRE_CALLER_COLS:
        migrated, moved, kept = _apply_caller_correction(rows)
        detail = (f"ENG-982 caller correction: moved ${moved:.2f} from the Other row's "
                  f"voice_11labs_livekit to voice_11labs_intelligence; kept ${kept:.2f} of "
                  "genuine per-user LiveKit-ElevenLabs TTS")
    else:
        raise SchemaError(
            f"unrecognised CSV header in {path}: {header}\n"
            f"expected 6-col {OLD_COLS}, 10-col {MID_COLS}, 11-col {PREV_COLS}, "
            f"15-col {PRE_PLATFORM_COLS}, 17-col {PRE_DRAWDOWN_COLS}, "
            f"18-col {PRE_FISH_COLS}, 19-col {PRE_INWORLD_COLS}, 14-col {PRE_CALLER_COLS}, "
            f"or current {COLS}")
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS, lineterminator=LF)
        w.writeheader()
        w.writerows(migrated)
    print(f"# migrated {len(migrated)} existing row(s) in {path} → current schema "
          f"({detail})", file=sys.stderr)
    return True


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Store one day of per-user Aurie cost (LLM+voice, multi-vendor).")
    ap.add_argument("--date", help="YYYY-MM-DD (UTC). Default: yesterday.")
    ap.add_argument("--llm-json", required=True, help="analyze-openai-cost-per-user JSON for the date")
    ap.add_argument("--allow-degraded-attribution", action="store_true",
                    help="persist the day even when a material share of spend was "
                         "split using a stored-completions fallback after a ledger "
                         "read failed (ENG-1114). Off by default: a degraded day "
                         "becomes permanent per-user history.")
    ap.add_argument("--allow-unmeasured-vendor", action="store_true",
                    help="persist the day even when a voice vendor's usage read failed "
                         "(Cartesia, direct Deepgram) on a day it carried traffic. Off by "
                         "default: the failed column would be stored as $0.")
    ap.add_argument("--allow-low-coverage", action="store_true",
                    help="persist the day even when per-user attribution "
                         "collapsed — on either side. LLM: little or none of the "
                         "day's walkable LLM spend reached a user, or the "
                         "attributed-user count fell far below its trailing "
                         "median (ENG-1120). Voice: little or none of the "
                         "attributable voice spend reached a user (typically an "
                         "empty LiveKit minute map zeroing every minute-allocated "
                         "vendor), or the voice-attributed user count collapsed "
                         "(ENG-1134). One flag covers both rather than a second "
                         "idiom. Off by default: the dollars still reconcile, so "
                         "a collapsed day is silent once written.")
    ap.add_argument("--allow-short-livekit-log", action="store_true",
                    help="persist the day even when the LiveKit logs found fewer than "
                         f"{MIN_LIVEKIT_LEDGER_USER_RATIO}x the llmCall ledger's LiveKit coach "
                         "users (ENG-1859). Off by default: a short log read under-attributes "
                         "every minute-allocated column.")
    ap.add_argument("--force", action="store_true",
                    help="recompute and REPLACE the rows for --date even when the day is "
                         "already in BigQuery or the local CSV. Requires an explicit --date. "
                         "Use to correct a day stored from bad inputs; sync the result with "
                         "bq_sink.py --date <d> afterwards.")
    ap.add_argument("--csv", default=None,
                    help="store path (default: $AURIE_COST_CSV, else $AURIE_COST_CACHE_DIR/"
                         "per_user_daily_cost.csv, else ~/.cache/aurie-cost/per_user_daily_cost.csv)")
    ap.add_argument("--cartesia-rate-per-million", type=float, default=DEFAULT_CARTESIA_RATE_PER_MILLION,
                    help=f"ESTIMATE — USD per 1M Cartesia credits (default {DEFAULT_CARTESIA_RATE_PER_MILLION}, "
                         "Scale overage, $299/8M plan). Replace with the contract rate.")
    ap.add_argument("--fish-rate-per-million", type=float, default=DEFAULT_FISH_RATE_PER_MILLION,
                    help=f"ESTIMATE — USD per 1M Fish Audio UTF-8 bytes (default {DEFAULT_FISH_RATE_PER_MILLION}, "
                         "s2-pro list price; usage is the agent's own tts_characters, so Latin ≈ bytes).")
    ap.add_argument("--inworld-rate-per-million", type=float, default=DEFAULT_INWORLD_RATE_PER_MILLION,
                    help=f"USD per 1M Inworld TTS characters (default {DEFAULT_INWORLD_RATE_PER_MILLION}, "
                         "TTS-2 On-Demand list price). Characters are ACTUAL from Inworld's usage API.")
    ap.add_argument("--deepgram-rate-per-hour", type=float, default=DEFAULT_DEEPGRAM_RATE_PER_HOUR,
                    help=f"ESTIMATE — USD per Deepgram audio-hour (default {DEFAULT_DEEPGRAM_RATE_PER_HOUR}, "
                         "nova-3 streaming $0.0048/min). Replace with the contract rate.")
    ap.add_argument("--livekit-rate-per-min", type=float, default=DEFAULT_LIVEKIT_RATE_PER_MIN,
                    help=f"USD per LiveKit session-minute BEYOND the plan allotment "
                         f"(default {DEFAULT_LIVEKIT_RATE_PER_MIN}, the plan's published overage "
                         "rate). Applies only to overage; minutes inside the allotment are "
                         "covered by the subscription and cost nothing marginally.")
    ap.add_argument("--livekit-plan", default=livekit_plan.DEFAULT_PLAN,
                    choices=sorted(livekit_plan.PLANS),
                    help=f"LiveKit Cloud plan of record (default {livekit_plan.DEFAULT_PLAN}). "
                         "Sets the credit rate minutes are billed at (subscription / included "
                         "allotment) and the unused capacity booked to the reconciling row.")
    args = ap.parse_args()
    # --force rewrites a stored day, so it never rides the implicit default date:
    # the operator must name the day being corrected.
    if args.force and not args.date:
        ap.error("--force requires an explicit --date (refusing to force the default "
                 "yesterday-UTC run)")
    day = args.date or (datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=1)).isoformat()

    el_key = os.environ.get("ELEVENLABS_ADMIN_API_KEY")
    tok = os.environ.get("AURA_API_TOKEN")
    if not el_key or not tok:
        sys.exit("MISSING ELEVENLABS_ADMIN_API_KEY and/or AURA_API_TOKEN")

    # Idempotency arbiter is BigQuery (the source of truth), not the local CSV.
    # BQ is a hard requirement here — the daily flow must not run BQ-less.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import paths as store_paths
    # BigQuery is the source of truth; the CSV is a local cache (default under
    # ~/.cache/aurie-cost). --csv > $AURIE_COST_CSV > cache dir.
    args.csv = store_paths.resolve("per_user", args.csv, ensure=True)
    from bq import partition_exists, BQUnavailable, BQError, PROJECT as BQ_PROJECT, DATASET as BQ_DATASET
    from bq_sink import STORES
    bq_project = os.environ.get("AURIE_BQ_PROJECT", BQ_PROJECT)
    bq_dataset = os.environ.get("AURIE_BQ_DATASET", BQ_DATASET)
    per_user_table = f"{bq_project}.{bq_dataset}.{STORES['per_user']['table']}"
    try:
        if partition_exists(per_user_table, day, bq_project) and not args.force:
            sys.exit(f"{day} already recorded in BigQuery ({per_user_table}) — nothing to do. "
                     "(run bq_pull.py to refresh the local CSV if it's missing the day; "
                     "pass --force to recompute and replace it)")
    except BQUnavailable as e:
        sys.exit(f"BigQuery is the source of truth but is unavailable: {e} — "
                 "fix GCP_KEY / install google-cloud-bigquery and re-run.")
    except BQError as e:
        sys.exit(f"BigQuery error during the idempotency check: {e}")

    # Migrate an old-schema local CSV to the current columns, then guard against a
    # CSV that's ahead of BigQuery (written locally but never synced up).
    try:
        _migrate_old_csv(args.csv)
    except SchemaError as e:  # CLI path: an unknown header is still fatal here
        sys.exit(str(e))
    if os.path.exists(args.csv) and not args.force:
        with open(args.csv) as f:
            if any(row.get("date") == day for row in csv.DictReader(f)):
                sys.exit(f"{day} is in the local CSV but NOT BigQuery — run "
                         f"bq_sink.py --date {day} to sync it to the source of truth "
                         f"(or --force to recompute and replace it).")

    llm, llm_total, top_proj = llm_costs(
        args.llm_json,
        allow_degraded=args.allow_degraded_attribution,
        allow_low_coverage=args.allow_low_coverage,
    )

    # Per-day engagement cohorts (who actually did something in the app today),
    # keyed by Firebase UID. Degrades to empty cohorts + a note if Mixpanel is
    # unreachable — the cost write still proceeds with blank engagement columns.
    # Fetched before the user-count guard, which reads it as its second signal.
    engaged, active, eng_note = engagement.day_engagement(day)

    # ENG-1120, second half: a PARTIAL loss of stored completions leaves coverage
    # healthy (the survivors absorb the whole bill by token share) and is only
    # visible as a collapsed user count. Checked here rather than in `llm_costs`
    # because the baseline comes from BigQuery, which is already this store's
    # source of truth for idempotency a few lines above.
    try:
        assert_user_count_plausible(
            day, sum(1 for v in llm.values() if v > 0), per_user_table, bq_project,
            eligible_dollars=walk_eligible_dollars(args.llm_json),
            llm_total=llm_total,
            engaged_share=engaged_cost_share(engaged, llm) if eng_note is None else None,
            allow_low=args.allow_low_coverage,
        )
    except BQUnavailable as e:
        # Genuinely can't reach BigQuery. The write needs it too and will fail
        # with a better message, so warn rather than raise a second one here.
        print(f"note: could not read the user-count baseline ({e}) — "
              f"skipping the plausibility check for {day}.", file=sys.stderr)
    except BQError as e:
        # NOT swallowed. BigQuery was already proven reachable by the idempotency
        # check above, so a query error here is a defect in the query, not an
        # environment condition — and silently skipping would leave the guard
        # permanently disabled while every run looked clean. That is the exact
        # shape of failure this whole ticket is about, so it fails loudly.
        if args.allow_low_coverage:
            print(f"note: user-count baseline query failed ({e}) — skipping the "
                  f"plausibility check for {day} (--allow-low-coverage).",
                  file=sys.stderr)
        else:
            sys.exit(
                f"REFUSING to store: the user-count baseline query failed ({e}). "
                f"BigQuery is reachable (the idempotency check above succeeded), "
                f"so this is a defect in the guard rather than an outage — fix it "
                f"rather than storing {day} unchecked, or pass "
                f"--allow-low-coverage to skip the guard deliberately."
            )

    # When Mixpanel failed, the empty cohorts do NOT mean "nobody engaged" — they
    # mean "unknown". Write the engagement columns BLANK (matching the migration /
    # SKILL.md contract) so a later backfill_engagement.py can fill them, rather
    # than persisting false engaged=0 for every reached user.
    mp_ok = eng_note is None

    # Split the ElevenLabs day total by CALLER (ENG-982): ConvAI agent vs the two
    # raw-TTS callers. day_11_intel is Intelligence Service TTS (what mobile calls)
    # — NOT attributable per user from the ElevenLabs side, so it lands whole on the
    # reconciling `Other` row. day_11_tts is the LiveKit-path slice only.
    day_11_agent, day_11_intel, day_11_tts, el_note = elevenlabs_caller_split(day, el_key)
    # ConvAI-agent slice: existing per-user walk, reconciling to the agent bucket.
    voice_11_agent, day_11_agent = voice_costs(day, el_key, day_11_agent)
    # A re-store past a source's retention keeps the stored per-user split (ENG-1859): the
    # fresh walk finds nothing and would move the whole day onto `Other` for good.
    kept_notes = []
    if args.force:
        fresh = {"voice_11labs_agent": (voice_11_agent, day_11_agent)}
        for col, (source, retention) in EXPIRING_SOURCES.items():
            if not source_expired(day, retention):
                continue
            try:
                stored, stored_total = stored_split(per_user_table, bq_project, day, col)
            except (BQUnavailable, BQError) as e:
                sys.exit(f"REFUSING to store {day}: {source} records have expired and the stored "
                         f"{col} split could not be read ({e}); a re-store would move it to `Other`.")
            split, note = keep_stored_split(col, source, stored, stored_total, fresh[col][1])
            if split is not None:
                fresh[col] = (split, fresh[col][1])
            kept_notes.append(note)
            print(f"# {note}", file=sys.stderr)
        voice_11_agent = fresh["voice_11labs_agent"][0]

    # LiveKit-path vendors: daily workspace totals, allocated by LiveKit minute share.
    #
    # LiveKit's own dollars are booked from the INVOICE, not from a per-minute rate
    # (PROD-5998). Two separate things, because they behave differently:
    #   voice_livekit          — minutes x the plan's CREDIT rate (subscription /
    #     included allotment; Scale = $0.01/min). Per-user, because these minutes were
    #     actually consumed by someone.
    #   voice_livekit_platform — the capacity that was paid for and NOT used. Booked on
    #     the reconciling `Other` row only: it belongs to nobody, and charging it by
    #     minute share would bill the users who showed up for the ones who did not.
    #     consumed + unused = the subscription, so the month still reconciles.
    try:
        lk_min_by_user, lk_total_min, lk_meta = livekit_minutes_for_day(day)
        lk_note = ""
    except LiveKitTruncated as ex:
        # Not a pending path: degrading to $0 LiveKit would store a day whose transport
        # cost and LiveKit-path TTS allocation are both wrong, so refuse the day.
        sys.exit(f"REFUSING to store {day}: {ex}")
    except LiveKitUnavailable as ex:
        lk_min_by_user, lk_total_min, lk_meta, lk_note = {}, 0.0, {}, str(ex)

    lk_minutes_before = mtd_livekit_minutes_before(day, per_user_table, bq_project)
    # Credit model (PROD-5998): a used minute costs what the subscription block paid for
    # it, and the capacity nobody used is booked separately rather than charged to the
    # people who did show up.
    # Priced on BILLED minutes (logged x LIVEKIT_LOG_TO_BILLED); the raw figures are kept
    # only to print the before/after.
    raw = livekit_day_dollars(args.livekit_plan, day, lk_minutes_before, lk_total_min, 1.0)
    day_livekit, day_livekit_unused, day_livekit_pools = livekit_day_dollars(
        args.livekit_plan, day, lk_minutes_before, lk_total_min)
    print(f"# livekit log-to-billed: factor={LIVEKIT_LOG_TO_BILLED} "
          f"today_min logged={lk_total_min:,.0f} billed={lk_total_min * LIVEKIT_LOG_TO_BILLED:,.0f} "
          f"livekit$ (consumed+unused+pools) logged=${sum(raw):.2f} "
          f"billed=${day_livekit + day_livekit_unused + day_livekit_pools:.2f}",
          file=sys.stderr)
    # Krisp + observability-audio overage, accrued from session minutes (ENG-1589);
    # observability audio stops on its SESSION_PROXY_OFF_FROM day (ENG-1861).
    # Allocated per user by LiveKit minute share below, alongside voice_livekit: the
    # dollar figure is itself computed from session minutes (both pools run 0.93-0.99x
    # agent minutes on the invoices), so minute share is the same proxy that produced
    # it. Booking it whole on `Other` instead pushed ~$60/day — 12% of voice in the
    # week of 2026-10-18 — out of every tier and understated paid voice by ~$400/week.
    print(f"# livekit booking: plan={args.livekit_plan} "
          f"credit_rate={_fmt_rate(livekit_plan.credit_rate_per_min(args.livekit_plan))} "
          f"log_to_billed={LIVEKIT_LOG_TO_BILLED} "
          f"mtd_min_before={lk_minutes_before:,.0f} today_min={lk_total_min:,.0f} "
          f"consumed=${day_livekit:.2f} unused_capacity=${day_livekit_unused:.2f} "
          f"[{livekit_unused_label(day_livekit_unused)}] "
          f"pool_overage=${day_livekit_pools:.2f} [krisp+obs-audio, accrued]",
          file=sys.stderr)
    day_cartesia, ct_note = cartesia_day_total(day, args.cartesia_rate_per_million)
    day_fish, fh_note = fish_day_total(lk_meta, args.fish_rate_per_million)
    day_inworld, iw_note = inworld_day_total(day, lk_meta, args.inworld_rate_per_million)
    on_inference, inference_minutes, direct_minutes, stt_by_transport = \
        stt_transport_split(lk_meta)
    print(f"# stt: minutes={float(lk_meta.get('stt_minutes') or 0.0):,.1f} "
          f"measured_sessions={lk_meta.get('stt_sessions_measured', 0)}/"
          f"{lk_meta.get('sessions', 0)} "
          f"with_transport={lk_meta.get('stt_sessions_with_transport', 0)} "
          f"inference={inference_minutes:,.1f} "
          f"direct={direct_minutes:,.1f}", file=sys.stderr)

    # The month-to-date drawdown decides whether today's STT spend is billable at all.
    # Read before the branch so a failure surfaces as a BigQuery error rather than as a
    # day quietly priced against an empty block.
    mtd_draw_before = mtd_inference_drawdown_before(day, per_user_table, bq_project)

    # The drawdown is a property of the INFERENCE minutes, so it is computed before any
    # branch decides who bills the day. A MIXED day — some speech direct to Deepgram, some
    # through Inference — still draws against the block, and a branch that zeroes it there
    # loses that usage: the month under-counts and its crossing day arrives late, silently.
    # That is the failure this column exists to remove, so the two questions are separated.
    block = livekit_plan.included("inference_credits_usd", args.livekit_plan) or 0.0
    inference_billed, dg_draw, dg_note = deepgram_inference_day(
        args.livekit_plan, inference_minutes, mtd_draw_before)
    has_inference = on_inference or inference_minutes > 0

    direct_note = ""
    if has_inference and direct_minutes <= 0:
        # Every minute went through Inference, so Deepgram's own usage API has nothing to
        # report — reading it would return a well-formed $0 that means nothing.
        day_deepgram = inference_billed
        if not dg_note:
            dg_note = inference_note(inference_minutes, dg_draw, inference_billed, block,
                                     lk_meta.get("stt_sessions_measured", 0))
    else:
        # Some or all speech reached Deepgram directly, so their invoice is authoritative
        # for that part. Any Inference overage is ADDITIVE to it, never an alternative.
        direct_total, direct_note = deepgram_day_total(day, args.deepgram_rate_per_hour)
        day_deepgram = round(direct_total + inference_billed, 4)
        if has_inference and not dg_note:
            dg_note = inference_note(inference_minutes, dg_draw, inference_billed, block,
                                     lk_meta.get("stt_sessions_measured", 0), mixed=True)
        if direct_note:
            dg_note = f"{direct_note}; {dg_note}" if dg_note else direct_note

    print(f"# inference credits: mtd_before=${mtd_draw_before:,.2f} "
          f"today=${dg_draw:,.2f} of "
          f"${livekit_plan.included('inference_credits_usd', args.livekit_plan) or 0.0:,.2f} "
          f"block — billed ${day_deepgram:,.2f}", file=sys.stderr)

    # Tag, don't drop — and don't halt the whole store either. The warning goes to stderr
    # at the point of detection as well as into the run's notes, because a line at the end
    # of a long run is exactly what went unread for four days.
    zero_note = unexplained_deepgram_zero(
        day, day_deepgram, lk_total_min, dg_note, stt_by_transport)
    if zero_note:
        dg_note = zero_note
        print(f"WARNING: {zero_note}", file=sys.stderr)
    lk_known = not lk_note
    # A LiveKit log failure alone keeps its store-and-flag path (PROD-6992): the vendor
    # totals are still read, only their owners are lost. It does make every LiveKit-path
    # vendor's traffic unknown, so a failed vendor read on top of it is refused.
    assert_vendor_reads(day, [
        ("cartesia", ct_note,
         float(lk_meta.get("minutes_by_provider", {}).get("cartesia", 0.0)) if lk_known else None),
        ("deepgram", direct_note,
         (direct_minutes if has_inference else lk_total_min) if lk_known else None),
    ], allow_unmeasured=args.allow_unmeasured_vendor)
    tts_inf_note = inference_tts_note(lk_meta)
    # The llmCall ledger's reply characters: the TTS split, and a check that the LiveKit log
    # read is not short (ENG-1859). A failed read keeps the minute split and skips the check.
    ledger_note = ""
    try:
        reply_chars = ledger_reply_chars(day, bq_project)
    except (BQUnavailable, BQError) as e:
        reply_chars = None
        ledger_note = (f"ledger_pending: the llmCall ledger read failed ({e}); TTS split by "
                       f"minutes and the LiveKit-vs-ledger user check skipped")
        print(f"WARNING: {ledger_note}", file=sys.stderr)
    if reply_chars is not None and lk_known:
        assert_livekit_users_match_ledger(
            day, sum(1 for m in lk_min_by_user.values() if m > 0), len(reply_chars),
            allow_short=args.allow_short_livekit_log)
    if tts_inf_note:
        print(f"WARNING: {tts_inf_note}", file=sys.stderr)

    # Per-TTS-provider minute buckets: allocate a TTS vendor's $ only to the
    # sessions that used it. Deepgram STT + LiveKit transport run on ALL sessions,
    # so they allocate on the overall minute map.
    by_user_tts = lk_meta.get("by_user_tts", {})
    min_by_provider = lk_meta.get("minutes_by_provider", {})
    el_tts_by_user = by_user_tts.get("elevenlabs", {})
    el_tts_min = min_by_provider.get("elevenlabs", 0.0)
    ct_by_user = by_user_tts.get("cartesia", {})
    ct_min = min_by_provider.get("cartesia", 0.0)
    fh_by_user = by_user_tts.get("fish", {})
    fh_min = min_by_provider.get("fish", 0.0)
    iw_by_user = by_user_tts.get("inworld", {})
    iw_min = min_by_provider.get("inworld", 0.0)

    # Each TTS provider's $ goes only to the sessions that used it. Vendors bill characters,
    # so the split is by ledger reply characters (ENG-1859); without the ledger, by minutes.
    tts_totals = {"elevenlabs": day_11_tts, "cartesia": day_cartesia, "fish": day_fish,
                  "inworld": day_inworld}
    tts_alloc_basis, tts_note = "minutes", ""
    if reply_chars is not None:
        tts, no_chars, no_chars_usd = allocate_tts_by_chars(tts_totals, by_user_tts, reply_chars)
        tts_alloc_basis = "chars"
        if no_chars:
            tts_note = (f"tts: {len(no_chars)} users with TTS minutes but no ledger reply chars "
                        f"kept their minute share (${no_chars_usd:.2f})")
    else:
        tts = {"elevenlabs": allocate_by_minutes(day_11_tts, el_tts_by_user, el_tts_min),
               "cartesia": allocate_by_minutes(day_cartesia, ct_by_user, ct_min),
               "fish": allocate_by_minutes(day_fish, fh_by_user, fh_min),
               "inworld": allocate_by_minutes(day_inworld, iw_by_user, iw_min)}
    print(f"# tts_alloc_basis: {tts_alloc_basis}"
          + (f" (llmCall ledger, BigQuery {LEDGER_TABLE})" if tts_alloc_basis == "chars" else ""),
          file=sys.stderr)
    voice_11_livekit, cartesia, fish, inworld = (
        tts["elevenlabs"], tts["cartesia"], tts["fish"], tts["inworld"])
    # Deepgram STT + LiveKit transport: every session, overall minute share.
    deepgram = allocate_by_minutes(day_deepgram, lk_min_by_user, lk_total_min)
    # The drawdown rides the same minute share as the dollars it draws against. Unlike
    # the Krisp overage — a pool nothing measures per user — STT seconds ARE per-session
    # and therefore per-user, so this is a measured split rather than an asserted one.
    inference_draw = allocate_by_minutes(dg_draw, lk_min_by_user, lk_total_min)
    # Consumed credits plus the session-proxied pool overage, both per session minute.
    livekit = allocate_by_minutes(day_livekit + day_livekit_pools, lk_min_by_user, lk_total_min)
    # Unused capacity is NOT allocated: it belongs to nobody. Charging idle capacity by
    # minute share would bill the users who showed up for the ones who did not, and it is
    # the per-user figure that feeds margin, payback and lifetime-cost.
    livekit_platform = {}

    # ENG-1134: the voice counterpart of the ENG-1120 guards. Checked here — after
    # every allocation, before the ~2,000 per-user subscription lookups below —
    # so a collapsed day costs one query rather than a full walk, and can never
    # reach the CSV/BigQuery write further down.
    #
    # `day_11_intel` is excluded from the denominator on purpose: Intelligence
    # Service TTS carries no per-user signal, so its slice is unattributable by
    # construction rather than by failure. The LiveKit path's eligible dollars are
    # what it allocates, pool overage included, or coverage reads above 100%.
    voice_cov = summarize_voice_coverage(
        {"11labs_agent": (day_11_agent, voice_11_agent),
         "11labs_livekit": (day_11_tts, voice_11_livekit),
         "cartesia": (day_cartesia, cartesia),
         "fish": (day_fish, fish),
         "inworld": (day_inworld, inworld),
         "deepgram": (day_deepgram, deepgram),
         "livekit": (day_livekit + day_livekit_pools, livekit)},
        unattributable_dollars=day_11_intel,
    )
    per_path = ", ".join(
        "{}:{:.0f}%".format(n, p["coverage_pct"])
        for n, p in voice_cov["paths"].items() if p["eligible_dollars"] > 0
    )
    print(f"# voice coverage: {voice_cov['coverage_pct']:.2f}% "
          f"(${voice_cov['attributed_dollars']:,.2f} of "
          f"${voice_cov['eligible_dollars']:,.2f} attributable; "
          f"{voice_cov['users_with_voice_cost']} users; "
          f"${voice_cov['unattributable_dollars']:,.2f} unattributable by design) "
          f"{{{per_path}}}", file=sys.stderr)
    assert_voice_coverage(voice_cov, allow_low_coverage=args.allow_low_coverage)
    try:
        assert_voice_user_count_plausible(
            day, voice_cov["users_with_voice_cost"], per_user_table, bq_project,
            eligible_dollars=voice_cov["eligible_dollars"],
            allow_low=args.allow_low_coverage,
        )
    except BQUnavailable as e:
        # Same split as the LLM baseline above: unreachable BigQuery is an
        # environment condition the write itself will report better.
        print(f"note: could not read the voice user-count baseline ({e}) — "
              f"skipping the plausibility check for {day}.", file=sys.stderr)
    except BQError as e:
        # NOT swallowed, for the same reason as its LLM twin: BigQuery was proven
        # reachable by the idempotency check, so a query error is a defect in the
        # guard, and silently skipping would leave it permanently disabled while
        # every run looked clean.
        if args.allow_low_coverage:
            print(f"note: voice user-count baseline query failed ({e}) — skipping "
                  f"the plausibility check for {day} (--allow-low-coverage).",
                  file=sys.stderr)
        else:
            sys.exit(
                f"REFUSING to store: the voice user-count baseline query failed "
                f"({e}). BigQuery is reachable (the idempotency check above "
                f"succeeded), so this is a defect in the guard rather than an "
                f"outage — fix it rather than storing {day} unchecked, or pass "
                f"--allow-low-coverage to skip the guard deliberately."
            )

    users = (set(llm) | set(voice_11_agent) | set(voice_11_livekit)
             | set(cartesia) | set(fish) | set(inworld) | set(deepgram) | set(livekit)
             | set(livekit_platform))

    # A user reached in Mixpanel but billed $0 today still needs a row, or
    # activity with no same-day cost is invisible in a store keyed on cost.
    # $0 rows never move a dollar total: total_cost is genuinely 0.0 for
    # them, so the llm_total / day_11_agent / etc. reconciliation above
    # (already computed) is unaffected either way. PROD-6143.
    mp_active_zero_cost = mixpanel_active_zero_cost_users(engaged, active, users, mp_ok)
    if mp_active_zero_cost:
        print(f"# activity zero-cost rows: {len(mp_active_zero_cost)} "
              f"Mixpanel-active users added with $0 cost today (users with "
              f"cost: {len(users)})", file=sys.stderr)
    users = users | mp_active_zero_cost

    with ThreadPoolExecutor(max_workers=8) as ex:
        fut = {ex.submit(sub_state, u, day, tok): u for u in users}
        states = {fut[f]: f.result() for f in as_completed(fut)}

    rows = []
    for u in users:
        L = llm.get(u, 0.0)
        v11a = voice_11_agent.get(u, 0.0)
        v11l = voice_11_livekit.get(u, 0.0)
        vct = cartesia.get(u, 0.0)
        vfh = fish.get(u, 0.0)
        viw = inworld.get(u, 0.0)
        vdg = deepgram.get(u, 0.0)
        vlk = livekit.get(u, 0.0)
        vlkp = livekit_platform.get(u, 0.0)
        lkm = lk_min_by_user.get(u, 0.0)
        vtot = v11a + v11l + vct + vfh + viw + vdg + vlk + vlkp
        rows.append({"date": day, "user_id": u, "llm_cost": round(L, 4),
                     "voice_11labs_agent": round(v11a, 4),
                     # Intelligence Service TTS has no per-user attribution from the
                     # ElevenLabs side — the whole slice sits on the `Other` row.
                     "voice_11labs_intelligence": 0.0,
                     "voice_11labs_livekit": round(v11l, 4),
                     "voice_cartesia": round(vct, 4),
                     "voice_fish": round(vfh, 4),
                     "voice_inworld": round(viw, 4),
                     "voice_deepgram": round(vdg, 4),
                     # Spend against a PREPAID block, not incremental cost -- so it is
                     # deliberately absent from voice_cost/total_cost, the same way
                     # livekit_minutes is. Adding it would double-count the subscription.
                     "deepgram_inference_drawdown": round(
                         inference_draw.get(u, 0.0), 4),
                     "voice_livekit": round(vlk, 4),
                     "voice_livekit_platform": round(vlkp, 4),
                     "livekit_minutes": round(lkm, 4),
                     "voice_cost": round(vtot, 4), "total_cost": round(L + vtot, 4),
                     "subscription_state": states.get(u, "free"),
                     "engaged": ("1" if u in engaged else "0") if mp_ok else "",
                     "active": ("1" if u in active else "0") if mp_ok else "",
                     "activity_source": engagement.activity_source(
                         u, engaged, active, top_proj.get(u, "")) if mp_ok else ""})

    # Reconciling "Other" row = per-vendor (authoritative total − attributed).
    lo = max(round(llm_total - sum(llm.values()), 4), 0.0)
    o11a = max(round(day_11_agent - sum(voice_11_agent.values()), 4), 0.0)
    o11l = max(round(day_11_tts - sum(voice_11_livekit.values()), 4), 0.0)
    oct = max(round(day_cartesia - sum(cartesia.values()), 4), 0.0)
    ofh = max(round(day_fish - sum(fish.values()), 4), 0.0)
    oiw = max(round(day_inworld - sum(inworld.values()), 4), 0.0)
    odg = max(round(day_deepgram - sum(deepgram.values()), 4), 0.0)
    # The remainder matters more here than for a cost column: this row is summed back
    # month-to-date, so drawdown left on the floor would under-state how full the block
    # is and push the crossing day later than it really is.
    odraw = max(round(dg_draw - sum(inference_draw.values()), 4), 0.0)
    olk = round(max(day_livekit + day_livekit_pools - sum(livekit.values()), 0.0), 4)
    # On a day with no LiveKit minutes the whole day's subscription accrual lands
    # here — the fee is owed regardless, and there is nothing to allocate it by.
    # Unused subscription capacity. NOT floored at 0: once a month crosses into overage,
    # unused-to-date drops to zero and this day carries the negative that reverses what
    # the month's earlier, lighter days booked. Flooring it would leave those days
    # permanently over-booked and the month would quietly stop reconciling.
    olkp = round(day_livekit_unused, 4)
    # Intelligence Service TTS is unattributable per user — the whole day's slice is
    # the remainder by construction.
    o11i = max(round(day_11_intel, 4), 0.0)
    ov = round(o11a + o11i + o11l + oct + ofh + oiw + odg + olk + olkp, 4)
    # abs(), not `> 0.005`: unused capacity is signed now. A day that crosses into
    # overage carries a NEGATIVE that reverses what the month's earlier, lighter days
    # booked, and a positive-only gate silently drops it — taking any other leftovers on
    # that row with it and stopping the month reconciling, which is the failure this
    # column was added to prevent.
    # `odraw` joins the gate: it is not part of `ov` (it is not a cost), so a day whose
    # only unattributed quantity is drawdown would otherwise write no Other row and lose
    # it -- and a lost drawdown delays the month's crossing day, silently.
    if abs(lo) > 0.005 or abs(ov) > 0.005 or abs(odraw) > 0.005:
        rows.append({"date": day, "user_id": OTHER_USER, "llm_cost": lo,
                     "voice_11labs_agent": o11a,
                     "voice_11labs_intelligence": o11i,
                     "voice_11labs_livekit": o11l,
                     "voice_cartesia": oct, "voice_fish": ofh, "voice_inworld": oiw,
                     "voice_deepgram": odg,
                     "deepgram_inference_drawdown": odraw,
                     "voice_livekit": olk, "voice_livekit_platform": olkp,
                     "livekit_minutes": 0.0,
                     "voice_cost": ov, "total_cost": round(lo + ov, 4),
                     "subscription_state": "other",
                     "engaged": "0", "active": "0", "activity_source": "reconcile"})
    rows.sort(key=lambda r: -r["total_cost"])

    new = not os.path.exists(args.csv)
    os.makedirs(os.path.dirname(os.path.abspath(args.csv)), exist_ok=True)
    if not new:
        # Re-check the header immediately before the append. This is NOT redundant
        # with the _migrate_old_csv call at the top of main(): that runs a few
        # MINUTES earlier, before the OpenAI/voice walks, and validates the file as
        # it was THEN. The append below writes this build's COLS regardless of what
        # the file says now, so anything that rewrites the store inside that window
        # — most realistically a concurrently-running build whose migration bumps
        # the schema — silently produces rows with the wrong field count.
        #
        # That is the 2026-07-27 (ENG-982) failure: a 14-column build passed its
        # early check against a then-14-column file, a newer build migrated the
        # store to 15 columns mid-run (both mtimes land in the same minute), and the
        # first build then appended 2,644 rows with 14 fields under the new header.
        # It aborted the per-user BigQuery sync and poisoned every derived figure.
        # Checking at the top only proves the file was sane before the work started.
        with open(args.csv, newline="") as f:
            header = next(csv.reader(f), [])
        if header and header != COLS:
            sys.exit(
                f"refusing to append to {args.csv}: its header has {len(header)} column(s) "
                f"but this build writes {len(COLS)}.\n"
                f"  file: {','.join(header)}\n"
                f"  code: {','.join(COLS)}\n"
                "The store was migrated by a different build of store.py. Re-run with the "
                "matching version (or rebuild the local copy with bq_pull.py); nothing written."
            )
    existing = []
    replacing = False
    if not new and args.force:
        # A forced date is normally already in the store; appending would duplicate
        # its ~1,700-2,600 rows. Keep every other date, drop this one, rewrite.
        with open(args.csv, newline="") as f:
            existing = [r for r in csv.DictReader(f)]
        kept = [r for r in existing if r.get("date") != day]
        replacing = len(kept) != len(existing)
        if replacing:
            # Rewriting the file re-serializes every row, so match its existing line
            # terminator — csv defaults to \r\n, and blindly taking that default would
            # flip an LF store's ~110k untouched lines as a side effect of correcting
            # one day. Correcting a date must not rewrite the rest of the store.
            with open(args.csv, "rb") as f:
                term = "\r\n" if f.readline().endswith(b"\r\n") else "\n"
            merged = kept + [{c: r.get(c, "") for c in COLS} for r in rows]
            merged.sort(key=lambda r: r["date"])
            tmp = f"{args.csv}.tmp"  # temp + atomic move: an interrupted run can't truncate the store
            with open(tmp, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore",
                                   lineterminator=term)
                w.writeheader()
                w.writerows(merged)
            os.replace(tmp, args.csv)
    if not replacing:
        with open(args.csv, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLS, lineterminator=LF)
            if new:
                w.writeheader()
            w.writerows(rows)

    day_voice = (day_11_agent + day_11_intel + day_11_tts
                 + day_cartesia + day_fish + day_inworld + day_deepgram + day_livekit
                 + day_livekit_unused
                 + day_livekit_pools)
    day_total = llm_total + day_voice
    csv_total = sum(r["total_cost"] for r in rows)
    # Every row's voice_cost must equal the sum of its nine voice_* cost cols.
    sum_ok = all(
        abs(r["voice_cost"] - (r["voice_11labs_agent"] + r["voice_11labs_intelligence"]
                               + r["voice_11labs_livekit"] + r["voice_cartesia"]
                               + r["voice_fish"] + r["voice_inworld"] + r["voice_deepgram"]
                               + r["voice_livekit"]
                               + r["voice_livekit_platform"])) < 0.0002
        for r in rows)
    notes = [n for n in (el_note, ct_note, fh_note, iw_note, dg_note, tts_inf_note, lk_note,
                         ledger_note, tts_note, *kept_notes) if n]
    verb = "replaced" if replacing else "appended"
    print(f"{verb} {len(rows)} rows for {day} -> {args.csv}")
    if args.force:
        print(f"NOTE: {day} rewritten locally — run bq_sink.py --date {day} to replace "
              "the BigQuery partition (it is still the pre-force version until you do).",
              file=sys.stderr)
    print(f"  LLM ${llm_total:.2f} + voice ${day_voice:.2f} "
          f"(11labs-agent ${day_11_agent:.2f} + 11labs-intelligence ${day_11_intel:.2f} + "
          f"11labs-livekit ${day_11_tts:.2f} + "
          f"cartesia ${day_cartesia:.2f} [est] + fish ${day_fish:.2f} [est, agent chars] + "
          f"inworld ${day_inworld:.2f} [{'est, agent chars' if iw_note else 'actual chars x rate'}] + "
          f"deepgram ${day_deepgram:.2f} [est] + "
          f"livekit ${day_livekit:.2f} [billed minutes x credit rate] + "
          f"livekit-unused ${day_livekit_unused:.2f} [{livekit_unused_label(day_livekit_unused)}] + "
          f"livekit-pools ${day_livekit_pools:.2f} [krisp+obs-audio, accrued]) "
          f"= ${day_total:.2f}")
    print(f"  CSV day total ${csv_total:.2f} (reconciles: {abs(csv_total - day_total) < 0.02})")
    lk_billed = day_livekit + day_livekit_unused + day_livekit_pools
    print(f"  livekit log-to-billed x{LIVEKIT_LOG_TO_BILLED}: livekit ${sum(raw):.2f} -> "
          f"${lk_billed:.2f}, day total ${day_total - lk_billed + sum(raw):.2f} -> ${day_total:.2f}")
    print(f"  voice_cost == Σ(9 voice_* cols) per row: {sum_ok}")
    print(f"  tts_alloc_basis: {tts_alloc_basis}")
    if mp_ok:
        n_engaged = sum(1 for r in rows if r["engaged"] == "1")
        n_active = sum(1 for r in rows if r["active"] == "1")
        print(f"  engaged {n_engaged} / active {n_active} / reached {len(rows)} "
              f"(engaged = sent a message; reached = billed incl. ambient/batch jobs)")
    else:
        print(f"  engagement: UNKNOWN (columns left blank) / reached {len(rows)} — "
              f"backfill with backfill_engagement.py once Mixpanel is reachable")
        print(f"  note: {eng_note}")
    print("  rates: cartesia ${:.2f}/M credits, fish ${:.2f}/M bytes, inworld ${:.2f}/M chars, "
          "deepgram ${:.3f}/hr, livekit ${:.3f}/min "
          "(ALL ESTIMATES from pricing sheet — override via --*-rate-* flags)".format(
              args.cartesia_rate_per_million, args.fish_rate_per_million,
              args.inworld_rate_per_million, args.deepgram_rate_per_hour,
              args.livekit_rate_per_min))
    for n in notes:
        print(f"  note: {n}")


if __name__ == "__main__":
    main()
