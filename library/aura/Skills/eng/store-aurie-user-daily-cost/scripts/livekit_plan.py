"""LiveKit Cloud plan facts — the plan of record, its allotments, and its rate card.

Canonical copy lives in
`plugins/eng/skills/report-livekit-quota/scripts/livekit_plan.py`; sibling copies are
byte-identical (skills are self-contained, so no skill reads another's files). Edit the
canonical first and propagate in the same PR — `tests/test_livekit_plan.py` enforces both
the copy check and the facts below.

LiveKit exposes NO billing or usage API: the Cloud dashboard is server-rendered with no
JSON endpoint behind it, so every number here is config, read off the dashboard by hand and
dated. `Tools/LiveKit` in AI Knowledge is the canonical prose home for plan + pricing; this
module exists because a script needs real values, and it is the one place in the repo that
carries them.

WHY A TABLE AND NOT A RATE
--------------------------
LiveKit bills a **monthly subscription** that buys allotments across several independent
pools, plus per-pool overage beyond each. Two consequences that a single $/min constant
cannot express, and both have been got wrong here:

1. **Inside an allotment the marginal cost of a unit is $0.** The published per-minute
   figures are OVERAGE rates. Pricing every minute at the overage rate books a charge that
   does not exist.
2. **The pools bind at different times.** Agent-session minutes are the pool everyone
   watches and the one furthest from binding (Aug 2026: 11,381 of 50,000). The pool that
   actually blew was Krisp — 10,000 of 10,000, 955 minutes over, $1.14, the only variable
   dollar ever paid to LiveKit. A verdict driven by session minutes alone reads OK while a
   different pool is in overage.

The honest per-unit figure is therefore the EFFECTIVE rate — actual invoice dollars over
actual units — which keeps the dollar total an actual and derives only the allocation. See
`effective_rate_per_min()`.

Note the plans are priced so `subscription / included_minutes` equals the overage rate
exactly at the allotment (Scale: $500 / 50,000 = $0.01/min). The effective rate is therefore
always >= the overage rate, equal only at 100% utilization — so quoting the overage rate as
a cost per minute books the theoretical floor.

Stdlib only.

Sources:
  - LiveKit Cloud dashboard, project p_5bw0of3laoy (Aurie) -> Settings -> Billing, read
    2026-09-14: plan Scale, next billing cycle 2026-10-01, next invoice $500.00.
  - Same page, read 2026-10-16: the September 2026 statement ($915.84) and October usage
    to date ($1,940.34). Its line items publish the observability-audio overage
    ($0.005/min). Logs, spans and transcripts share ONE 5M pool: October's three
    "incl." lines sum to exactly 5,000,000, and the overage was billed to spans alone
    because they were the entries written after the pool ran out.
  - https://livekit.com/pricing and https://livekit.com/pricing/inference, read 2026-09-14.
"""

from __future__ import annotations

# Date the plan table below was last reconciled against the LiveKit Cloud dashboard. A
# reader needs this to know whether they are looking at a fact or an artifact: nothing here
# can refresh itself, so an undated table ages into fiction without changing.
PLAN_FACTS_VERIFIED = "2026-10-16"

# ---------------------------------------------------------------------------
# Monthly dashboard readings
# ---------------------------------------------------------------------------
#
# Six of the seven pools cannot be derived from anything we collect, and LiveKit exposes no
# billing or usage API — the Cloud dashboard is the only source. That made "watch the pools"
# a habit with nowhere to record its result, so the one pool that actually blew (Krisp,
# August 2026, 955 minutes over) was found by opening the dashboard rather than by anything
# noticing. A threshold with no observer never fires.
#
# So a reading lives here: one entry per billing period, hand-read and dated. It does three
# things a habit could not. It lets the quota report print an actual for a pool it cannot
# measure. It makes a period with NO reading detectable, which is the trigger that was
# missing. And it records the invoiced overage, so "we chose not to book $1.14" is
# distinguishable from "nobody looked".
#
# Read from: LiveKit Cloud -> project p_5bw0of3laoy -> Settings -> Billing -> <Month> Usage.
# Units match the dashboard's own rows. Omit a key you did not read rather than guessing --
# a missing key reports as unread, and a wrong one reports as fact.
DASHBOARD_READINGS = {
    "2026-11": {
        "read_on": "2026-11-09",
        "partial": True,  # period to date; only the figures read are recorded
        "agent_session_minutes": 110231,
        # Session recording off since 2026-11-04 20:03Z (PROD-8277): 0.38x session minutes.
        "observability_audio_minutes": 42222,
    },
    "2026-10": {
        "read_on": "2026-11-09",
        "partial": False,  # the closed October statement ($5,265.89)
        "agent_session_minutes": 263719,
        # The statement's own line: 213,719 min over x $0.01 rounds a cent higher.
        "agent_overage_usd": 2137.18,
        "webrtc_participant_minutes": 260829,
        "downstream_data_gb": 279,
        "krisp_minutes": 259396,
        "observability_entries": 4707212 + 2917854 + 9670632,  # logs + transcripts + spans
        "observability_audio_minutes": 259736,
        "inference_credits_usd": 50.00 + 911.90 + 0.01,  # included + nova-3 + assemblyai
        # Krisp $299.27 + observability audio $1,048.67 + spans $290.11 + transcripts $78.75.
        # Agent minutes ($2,137.18 over) and Inference ($911.91) are their own lines.
        "invoiced_overage_usd": 1716.80,
    },
    "2026-09": {
        "read_on": "2026-10-16",
        "partial": False,  # the closed September statement ($915.84)
        "agent_session_minutes": 61838,
        "agent_overage_usd": 118.37,  # the statement's line; 11,838 min x $0.01 rounds a cent higher
        "webrtc_participant_minutes": 59668,
        "downstream_data_gb": 59,
        "krisp_minutes": 57378,
        "observability_entries": 1219930 + 2391785 + 722718,  # logs + spans + transcripts
        "observability_audio_minutes": 61271,
        "inference_credits_usd": 50.00 + 184.13 + 0.14,  # nova-3 55,745 min + assemblyai 53 min
        # Krisp $56.85 + observability audio $56.35. Agent minutes ($118.37) and Inference
        # ($184.27) are booked by store.py, so they are not "extra".
        "invoiced_overage_usd": 113.20,
    },
    "2026-08": {
        "read_on": "2026-09-15",
        "partial": False,
        "krisp_minutes": 10955,  # 10,000 included + 955 over
        "invoiced_overage_usd": 1.14,
    },
}

# Invoiced overage below this is reported but NOT booked into the daily cost store.
#
# August's $1.14 is 0.23% of a $501.14 invoice, and booking it costs a hand-edited dict plus
# a restatement of the month -- disproportionate to the number. But "ignore it" and "drop it"
# are different: below the threshold the figure is still REPORTED, so a month reads
# "reconciles to $500.00; $1.14 invoiced overage not booked (below threshold)" rather than
# quietly reconciling to a total that is not the invoice. Same rule as tagging an unmeasured
# STT zero -- a labelled gap is recoverable, a silent one is not.
OVERAGE_BOOKING_THRESHOLD_USD = 25.0


def dashboard_reading(period: str):
    """The hand-read dashboard figures for a billing period, or None if never read."""
    return DASHBOARD_READINGS.get(period)


def unbooked_overage(period: str):
    """Returns (amount, booked) for a period's invoiced overage, or None if unread.

    `booked` is False below the threshold, and on a partial (mid-period) reading: the amount is real and is reported, it simply
    does not enter the per-day store. A caller must render it either way -- an unreported
    un-booked overage is the silent shortfall the threshold exists to avoid, not to create.
    """
    reading = DASHBOARD_READINGS.get(period)
    if reading is None or "invoiced_overage_usd" not in reading:
        return None
    amount = float(reading["invoiced_overage_usd"])
    # A mid-period read is overage to date on a statement that has not closed: it is
    # booked from the final invoice, never from a partial one.
    if reading.get("partial"):
        return amount, False
    return amount, amount >= OVERAGE_BOOKING_THRESHOLD_USD


# The plan of record for the Aurie project (p_5bw0of3laoy) and the day its period renews.
# Previously the two consumers disagreed — store.py's comment said Scale while
# report-livekit-quota defaulted to Ship — which at ~12,000 min/month is the difference
# between 23% utilization and ~7,000 minutes of overage.
DEFAULT_PLAN = "scale"
DEFAULT_RENEWAL_DAY = 1

# Per-pool allotments and overage rates per plan.
#
# `unit` is what the pool is metered in; `overage_per_unit` is the published rate beyond the
# allotment (None where LiveKit publishes no overage rate for that pool on that plan).
#
# `derivable` marks whether the forwarded agent logs can measure the pool at all. A pool we
# cannot measure is declared here and reported as UNMEASURED rather than omitted: an absent
# pool is indistinguishable from a healthy one, which is exactly how the Krisp overage went
# unnoticed.
POOLS = {
    "agent_session_minutes": {
        "label": "Agent session minutes",
        "unit": "min",
        "derivable": True,  # (last_ts - first_ts) per room, from the forwarded logs
        "included": {"build": 1_000, "ship": 5_000, "scale": 50_000},
        "overage_per_unit": {"build": 0.0100, "ship": 0.0100, "scale": 0.0100},
    },
    "webrtc_participant_minutes": {
        "label": "WebRTC participant minutes",
        "unit": "min",
        "derivable": False,
        "included": {"build": 5_000, "ship": 150_000, "scale": 1_500_000},
        "overage_per_unit": {"build": None, "ship": None, "scale": None},
    },
    "downstream_data_gb": {
        "label": "Downstream data transfer",
        "unit": "GB",
        "derivable": False,
        "included": {"build": 50, "ship": 250, "scale": 3_000},
        "overage_per_unit": {"build": 0.10, "ship": 0.10, "scale": 0.10},
    },
    "krisp_minutes": {
        "label": "Krisp background minutes",
        "unit": "min",
        "derivable": False,
        "included": {"build": 100, "ship": 1_000, "scale": 10_000},
        "overage_per_unit": {"build": 0.0012, "ship": 0.0012, "scale": 0.0012},
    },
    # ONE pool shared by logs, spans and transcripts. The invoice prints an "incl." line per
    # entry type, but those lines sum to the allotment (Oct 2026: 2,027,330 + 1,700,733 +
    # 1,271,937 = 5,000,000) and the overage lands on whichever type was written after it
    # ran out (Oct: spans only).
    "observability_entries": {
        "label": "Observability entries (logs + spans + transcripts)",
        "unit": "entries",
        "derivable": False,
        "included": {"build": 100_000, "ship": 500_000, "scale": 5_000_000},
        "overage_per_unit": {"build": 0.00003, "ship": 0.00003, "scale": 0.00003},
    },
    "observability_audio_minutes": {
        "label": "Observability audio minutes",
        "unit": "min",
        "derivable": False,
        "included": {"build": None, "ship": None, "scale": 50_000},
        # Published on the Scale invoice ($0.005 per minute); unpublished on the others.
        "overage_per_unit": {"build": None, "ship": None, "scale": 0.005},
    },
    # Metered in DOLLARS, not units: inference is priced per model (STT by duration, TTS by
    # characters, LLM by tokens) and every model's spend draws down one shared credit
    # balance. See INFERENCE_RATES for the per-model card.
    "inference_credits_usd": {
        "label": "Inference credits",
        "unit": "USD",
        "derivable": False,
        "included": {"build": 2.50, "ship": 5.00, "scale": 50.00},
        "overage_per_unit": {"build": 1.0, "ship": 1.0, "scale": 1.0},  # dollars are 1:1
    },
}

PLANS = {
    "build": {"label": "Build", "subscription_usd": 0.0},
    "ship": {"label": "Ship", "subscription_usd": 50.0},
    "scale": {"label": "Scale", "subscription_usd": 500.0},
}

# Inference rate card, USD per unit, by plan tier. Only the models Aurie can actually reach
# are listed — extend from https://livekit.com/pricing/inference when a new one is wired up.
#
# The dashboard's billing line reads "$0.252 per hour" for deepgram/nova-3, which is the
# MONOLINGUAL Scale rate ($0.0042/min x 60). Language is therefore load-bearing: the same
# model in multi mode bills ~19% more, so a rate applied without checking the language is
# wrong in a direction nothing reports.
INFERENCE_RATES = {
    "deepgram/nova-3": {
        "unit": "min",
        "monolingual": {"build": 0.0048, "ship": 0.0048, "scale": 0.0042},
        "multilingual": {"build": 0.0058, "ship": 0.0058, "scale": 0.0050},
    },
}


def plan_label(plan: str) -> str:
    """Display label for a plan key (``scale`` -> ``Scale``)."""
    return PLANS[plan]["label"]


def subscription_usd(plan: str) -> float:
    """Monthly subscription for a plan, in USD."""
    return PLANS[plan]["subscription_usd"]


def included(pool: str, plan: str):
    """Included allotment for `pool` on `plan`, or None when the plan does not list one."""
    return POOLS[pool]["included"].get(plan)


def overage_per_unit(pool: str, plan: str):
    """Published overage rate per unit for `pool` on `plan`, or None when unpublished."""
    return POOLS[pool]["overage_per_unit"].get(plan)


def overage_usd(pool: str, plan: str, units: float) -> float:
    """Dollars owed for `units` consumed of `pool`, i.e. the amount beyond the allotment.

    Returns 0.0 inside the allotment — the whole point of the table. An unpublished overage
    rate also yields 0.0, since inventing one would be worse than reporting nothing; callers
    that need to distinguish "free" from "unknown" read `overage_per_unit` themselves.
    """
    incl = included(pool, plan)
    rate = overage_per_unit(pool, plan)
    if incl is None or rate is None:
        return 0.0
    return max(0.0, units - incl) * rate


def inference_usd(model: str, plan: str, units: float, *, multilingual: bool = False) -> float:
    """Inference spend for `units` of `model` at `plan` rates, before credits are applied.

    This is the DRAWDOWN against the credit balance, not necessarily a charge: whether any
    of it is billed depends on the balance, which is why the caller applies
    `overage_usd("inference_credits_usd", ...)` to the total rather than charging per model.
    """
    card = INFERENCE_RATES[model]
    band = "multilingual" if multilingual else "monolingual"
    return units * card[band][plan]


def credit_rate_per_min(plan: str):
    """Subscription / included minutes — the price of one minute of bought capacity.

    The subscription is a block of prepaid credits, so a used minute costs what the
    block paid for it: Scale is $500 / 50,000 = $0.01/min. This is the figure to bill a
    user's minutes at, and it is STABLE — it does not move with how busy the month was,
    which is the property a per-minute cost question actually wants.

    Note the identity that makes the two regimes agree: each plan is priced so this rate
    EQUALS its published overage rate. Past the allotment, `minutes x credit_rate` is
    therefore the invoice exactly, and inside it the shortfall is unused capacity rather
    than a different rate. Returns None on a $0 plan, which buys no capacity.
    """
    allot = included("agent_session_minutes", plan)
    sub = subscription_usd(plan)
    if not allot or sub <= 0:
        return None
    return sub / allot


def unused_capacity_usd(plan: str, day: str, minutes_before: float, minutes_today: float):
    """The day's share of subscription capacity that was PAID FOR but not consumed.

    Booked so a month still reconciles to the invoice: consumed (minutes x credit rate)
    plus unused equals the subscription. It belongs to nobody, so callers put it on the
    reconciling row rather than allocating it across users — attributing idle capacity by
    minute share would charge the people who *did* show up for the ones who did not.

    Computed as a month-to-date DELTA rather than per-day independently, which is what
    keeps it exact in both regimes. A month that later crosses into overage drives
    unused-to-date to zero, so this returns a NEGATIVE value on that day, reversing what
    earlier days booked. A per-day `max(0, accrual - consumed)` cannot do that: it leaves
    the early light days permanently over-booked, and the month quietly stops
    reconciling — the exact silent-drift failure this whole column exists to remove.
    """
    import calendar as _cal
    y, m, d = (int(x) for x in day.split("-"))
    dim = _cal.monthrange(y, m)[1]
    sub = subscription_usd(plan)
    credit = credit_rate_per_min(plan)
    if credit is None:
        return 0.0
    before = max(0.0, sub * (d - 1) / dim - minutes_before * credit)
    after = max(0.0, sub * d / dim - (minutes_before + minutes_today) * credit)
    return after - before


# Pools metered per minute of an agent session, so agent-session minutes are their proxy.
# Krisp runs on the user's audio and observability records the session's audio: the
# Sep 2026 invoice billed them at 0.93x and 0.99x agent minutes, Oct-to-date 0.98x and
# 0.99x. Their overage is ACCRUED daily from session minutes (an estimate, so slightly
# high for Krisp) and replaced by the invoiced figure once a statement is read
# (`rebook_livekit.INVOICED_EXTRA_OVERAGE`). Spans, logs and transcripts are per-entry
# and have no proxy; they stay invoice-only.
SESSION_PROXY_POOLS = ("krisp_minutes", "observability_audio_minutes")

# The UTC day from which a pool no longer tracks session minutes, so it stops accruing. Kept
# as a dated fact rather than a removal, so earlier days keep their accrual. Prod session
# recording was turned off on 2026-11-04 at 20:03Z (PROD-8277): November's observability
# audio stood at 0.38x session minutes against 0.985x in October, inside its allotment.
# If recording is turned back on, end the entry rather than deleting it.
SESSION_PROXY_OFF_FROM = {"observability_audio_minutes": "2026-11-05"}


def session_proxy_overage_usd(plan: str, minutes_before: float, minutes_today: float,
                              day: str | None = None) -> float:
    """The day's accrued overage on the session-proxied pools, as a month-to-date delta.

    Each pool's overage to date is `max(0, mtd_minutes - allotment) x rate`; the day books
    the change in that across its own minutes, so a month's days sum to the month's
    overage whatever order they are stored in. A pool with no published overage rate on
    `plan` contributes nothing rather than a guess, and neither does a pool on or after its
    `SESSION_PROXY_OFF_FROM` day (`day` is the UTC date being booked, YYYY-MM-DD).
    """
    total = 0.0
    for pool in SESSION_PROXY_POOLS:
        off = SESSION_PROXY_OFF_FROM.get(pool)
        if off and day and day >= off:
            continue
        allot = included(pool, plan)
        rate = overage_per_unit(pool, plan)
        if allot is None or not rate:
            continue
        after = max(0.0, minutes_before + minutes_today - allot)
        before = max(0.0, minutes_before - allot)
        total += (after - before) * rate
    return total


def effective_rate_per_min(plan: str, minutes: float, extra_overage_usd: float = 0.0):
    """Fully-loaded $/min: (subscription + overage) / minutes actually used.

    The number to quote as "what a voice minute costs us", and the one a per-user cost store
    should book from — because the subscription is a real cost the business pays whether or
    not the allotment is used. The *marginal* cost of one more minute inside the allotment is
    $0, which is the right answer to a different question (should we send more traffic) and
    the wrong basis for margin, payback or lifetime-cost reporting.

    Returns None for zero minutes rather than dividing: a month with no traffic has no
    meaningful per-minute cost, and 0.0 would read as "free".
    """
    if minutes <= 0:
        return None
    return (subscription_usd(plan) + extra_overage_usd) / minutes
