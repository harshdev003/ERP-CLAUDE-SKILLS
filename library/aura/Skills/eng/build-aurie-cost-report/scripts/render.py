#!/usr/bin/env python3
"""Render one self-contained HTML report from the Aurie cost/economics chart JSONs.

Reads the JSON emitted by the eng:chart-aurie-* family (collected by the skill's
SKILL.md workflow) and writes a single theme-aware HTML file: a key-reads strip,
a daily cost + active-users chart, the daily contribution-margin chart, the
cohort retention heatmap, and the seven per-user economics charts — each with a
data-driven analysis paragraph computed here (not hardcoded), so the report
regenerates correctly for any window.

Self-contained: stdlib only, no sibling-skill imports. It only reads the JSON
files it is given and writes HTML. Aggregate-only — the inputs carry no UID, and
neither does the output.

Inputs (any subset; a missing one degrades that section to "data unavailable"):
  --data-dir DIR   directory holding <key>.json for the keys below, and/or
  --<key> PATH     explicit path per key.
Keys: daily cohort curve retention cumulative lifetime concentration tenure payback mix free channel margin revenue revenuetier
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys

KEYS = ["daily", "cohort", "curve", "retention", "cumulative", "lifetime", "concentration", "tenure", "payback", "mix", "free", "channel", "margin", "revenue", "revenuetier"]

# key -> the composed skill id whose <span class="skill"> label carries it in the report. Used to
# stamp each card label with the skill's own metadata.version, which the chart script emits as
# "skill_version" in its JSON (so render.py never reads a sibling SKILL.md — self-contained).
KEY_SKILL = {
    "daily": "eng:chart-aurie-daily-cost", "cohort": "eng:chart-aurie-cohort-cost",
    "curve": "eng:chart-aurie-cost-curve", "retention": "eng:chart-aurie-retention",
    "cumulative": "eng:chart-aurie-cumulative-cost", "lifetime": "eng:chart-aurie-lifetime-cost",
    "concentration": "eng:chart-aurie-cost-concentration", "tenure": "eng:chart-aurie-tenure-cost",
    "payback": "eng:chart-aurie-cohort-payback", "mix": "eng:chart-aurie-cost-mix",
    "free": "eng:chart-aurie-free-user-cost", "channel": "eng:chart-aurie-cost-by-channel",
    "margin": "eng:chart-aurie-daily-margin", "revenue": "eng:chart-aurie-revenue",
    "revenuetier": "eng:chart-aurie-revenue-by-tier",
}

SKILL_ID = "eng:build-aurie-cost-report"


def _skill_ident() -> str:
    """`<plugin>:<skill>:<version>` for the report footer, version read from the
    sibling SKILL.md `metadata.version` (scoped to the metadata block so a `version:`
    token in the description can't shadow it). Falls back to the bare id."""
    try:
        text = open(os.path.join(os.path.dirname(__file__), os.pardir, "SKILL.md"),
                    encoding="utf-8").read()
    except OSError:
        return SKILL_ID
    fm = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    fm = fm.group(1) if fm else text
    block = re.search(r"(?m)^metadata:[ \t]*\n((?:[ \t]+\S.*\n?)+)", fm)
    if not block:
        return SKILL_ID
    ver = re.search(r'''(?m)^[ \t]+version:[ \t]*["']?([^"'\s]+)["']?[ \t]*$''', block.group(1))
    return f"{SKILL_ID}:{ver.group(1)}" if ver else SKILL_ID


# ------------------------------- helpers ------------------------------------
def _load(path):
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:  # noqa: BLE001
        print(f"  ! failed to read {path}: {e}", file=sys.stderr)
        return None


def usd(v, d=2):
    try:
        return "$" + format(float(v), f",.{d}f")
    except (TypeError, ValueError):
        return "—"


def pct(v, d=1):
    try:
        return format(float(v) * 100, f".{d}f") + "%"
    except (TypeError, ValueError):
        return "—"


def num(v):
    try:
        return format(int(round(float(v))), ",")
    except (TypeError, ValueError):
        return "—"


def susd(v, d=0):
    """Signed dollar format: -$185 instead of $-185, for values that can be negative (net margin)."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "—"
    return ("-" if v < 0 else "") + "$" + format(abs(v), f",.{d}f")


def _get(lst, key, val):
    for x in lst or []:
        if x.get(key) == val:
            return x
    return None


# --------------------------- analysis (reads) -------------------------------
# Display labels for margin.json's `excluded.other_by_component` keys, in render order.
OTHER_COMPONENT_LABELS = (
    ("llm", "LLM"),
    ("livekit_unused_capacity", "LiveKit unused capacity"),
    ("elevenlabs_intelligence", "ElevenLabs Intelligence Service TTS"),
    ("elevenlabs_agent", "ElevenLabs agent"),
    ("elevenlabs_livekit", "ElevenLabs LiveKit TTS"),
    ("cartesia", "Cartesia"),
    ("fish", "Fish"),
    ("inworld", "Inworld"),
    ("deepgram", "Deepgram"),
    ("livekit", "LiveKit"),
)


def _other_components(by_component):
    """`LLM $1,266.22 · LiveKit unused capacity $849.37 · …`, largest first, skipping
    components under half a cent. None when margin.json predates `other_by_component`."""
    if not by_component:
        return None
    items = [(label, by_component.get(key) or 0) for key, label in OTHER_COMPONENT_LABELS]
    items = [(label, v) for label, v in items if abs(v) >= 0.005]
    items.sort(key=lambda x: -x[1])
    return " · ".join(f"{label} {'−' if v < 0 else ''}{usd(abs(v), 2)}"
                      for label, v in items) or None


def _collapsed_line(block, shown=5):
    """The flag for vendor-days where a voice path billed and reached no user (PROD-6992).
    The store keeps those days; their dollars are inside the `Other` figure above, so this
    says how much of it is a failed attribution rather than a by-design remainder. None when
    there are none, or margin.json predates the block."""
    if not block or not block.get("count"):
        return None
    labels = dict(OTHER_COMPONENT_LABELS)
    days = block.get("days") or []
    listed = "; ".join(
        f"{labels.get(d['path'], d['path'])} {d['date']} {usd(d['dollars'], 2)}"
        for d in days[:shown])
    more = f"; +{len(days) - shown} more" if len(days) > shown else ""
    n = block["count"]
    dust_n = block.get("below_floor_count") or 0
    dust = (f" Another {dust_n} under {usd(block.get('floor_usd'), 2)} "
            f"({usd(block.get('below_floor_dollars'), 2)}) are not listed." if dust_n else "")
    return (
        "<div><b>Voice billed to no user:</b> "
        f"{n} vendor-day{'s' if n != 1 else ''}, {usd(block.get('dollars'), 2)} of the "
        f"<code>Other</code> figure — days a vendor billed and no user carried any of it: "
        f"an attribution miss, or a charge with no session behind it. Not a by-design "
        f"remainder ({listed}{more}).{dust}</div>"
    )


def _excluded_callout(margin):
    """Preformatted HTML for the prod-only exclusions callout (PROD-3589 Part B), built from
    chart-aurie-daily-margin's `excluded` block — the base-vs-prod-view $ delta for the same
    window. Returns None if `margin` is missing or carries no `excluded` block (an older
    margin.json collected before this field existed degrades silently, same pattern as every
    other optional field in this renderer).

    Deliberately splits two very different things the raw delta conflates:
      1. Dev + QA/unknown *users* — actual non-prod-user traffic (dev + unknown env tags).
      2. The `Other` reconciling row — each cost column's remainder (authoritative total −
         per-user attribution), LLM included and usually its largest part. NOT dev/QA; it has
         no per-user grain to live in the prod view. Split by column when margin.json carries
         `other_by_component`.
    `untagged` (a user_id not yet in aurie_user_env) is folded into line 1's note as a
    coverage caveat when non-zero, since it's a backfill gap, not an environment class."""
    exc = (margin or {}).get("excluded")
    if not exc or exc.get("total_excluded") is None:
        return None
    dev = exc.get("dev_cost") or 0
    unknown = exc.get("unknown_cost") or 0
    other = exc.get("other_cost") or 0
    untagged = exc.get("untagged_cost") or 0
    all_cost = exc.get("all_cost") or 0
    non_prod_users = dev + unknown
    np_pct = pct(non_prod_users / all_cost, 3) if all_cost else "—"
    lines = [
        "<div><b>Dev + QA/unknown users excluded:</b> "
        f"{usd(non_prod_users, 2)} ({np_pct}) — actual non-prod-user traffic "
        f"(dev {usd(dev, 2)} + unknown {usd(unknown, 2)}).</div>"
    ]
    if untagged:
        u_pct = pct(untagged / all_cost, 3) if all_cost else "—"
        lines.append(
            "<div><b>Not-yet-tagged users:</b> "
            f"{usd(untagged, 2)} ({u_pct}) — a coverage gap in the env backfill, not a dev/QA "
            "classification; re-run <span class=\"skill\">eng:backfill-aurie-user-env</span> to shrink it.</div>"
        )
    if other:
        o_pct = pct(other / all_cost, 3) if all_cost else "—"
        parts = _other_components(exc.get("other_by_component"))
        lines.append(
            "<div><b>Unattributed remainder (the <code>Other</code> reconciling row):</b> "
            f"{usd(other, 2)} ({o_pct}) — what each authoritative vendor total (OpenAI and every "
            "voice vendor) has left after per-user attribution: non-user LLM projects (evals, "
            "dev twins), attribution misses, Intelligence Service TTS, and unused LiveKit "
            "capacity. It has no per-user grain, so the prod aggregates exclude it. This is NOT "
            "dev/QA user traffic."
            + (f" Split: {parts}." if parts else "")
            + "</div>"
        )
    collapsed = _collapsed_line(exc.get("collapsed_voice_paths"))
    if collapsed:
        lines.append(collapsed)
    lines.append(
        "<div class=\"callout-note\">Every chart above reads the prod-only "
        "<code>aurie_per_user_daily_cost_prod</code> view (env='prod'); see "
        "<span class=\"skill\">eng:backfill-aurie-user-env</span>.</div>"
    )
    return "".join(lines)


def build_report(data, window_label):
    reads, tiles = {}, []

    margin = data.get("margin")
    if margin:
        seg = margin.get("segment_summary", {})
        conc_m = margin.get("concentration", {})
        avg_net = seg.get("avg_net_per_day")
        avg_paid = seg.get("avg_paid_contribution_per_day")
        avg_free = seg.get("avg_free_drag_per_day")
        days_n = seg.get("days")
        worst = conc_m.get("worst_loss_share") or {}
        top1 = worst.get("top_1_pct")
        neg_users = conc_m.get("negative_net_users")
        total_users = conc_m.get("total_users")
        if avg_net is not None:
            sentence = (
                f"<b>Net margin averages {susd(avg_net)}/day</b>"
                + (f" over the last {days_n} days" if days_n else "")
                + "."
            )
            parts = []
            if avg_paid is not None:
                parts.append(f"paid-tier contribution {susd(avg_paid)}/day")
            if avg_free is not None:
                parts.append(f"free-tier drag {susd(avg_free)}/day")
            if parts:
                sentence += " Split: " + ", ".join(parts) + "."
            if top1 is not None:
                sentence += (
                    f" The worst 1% of users by loss drive <b>{pct(top1,0)}</b> of total loss"
                    + (f" ({num(neg_users)} of {num(total_users)} users are net-negative)"
                       if neg_users is not None and total_users else "")
                    + "."
                )
            reads["margin"] = sentence
            tiles.append({"k": "Net margin, avg/day", "v": susd(avg_net),
                          "d": (f"{days_n} days" if days_n else ""),
                          "cls": "pos" if avg_net >= 0 else "neg"})
            if avg_paid is not None:
                tiles.append({"k": "Paid contribution/day", "v": susd(avg_paid),
                              "d": (f"free drag {susd(avg_free)}/day" if avg_free is not None else ""),
                              "cls": "pos" if avg_paid >= 0 else "neg"})
            if top1 is not None:
                tiles.append({"k": "Worst-1% loss share", "v": pct(top1, 0),
                              "d": (f"{num(neg_users)} users net-negative" if neg_users is not None else ""),
                              "cls": "neg"})

    conc = data.get("concentration")
    if conc:
        s = conc.get("summary", {})
        t1 = _get(conc.get("top_shares"), "pct", 1)
        t10 = _get(conc.get("top_shares"), "pct", 10)
        g = s.get("gini")
        if t1 and g is not None:
            reads["concentration"] = (
                f"<b>Extreme concentration (Gini {g:.3f}).</b> The top 1% of users "
                f"({num(t1['users'])}) account for <b>{pct(t1['share'],0)}</b> of all cost"
                + (f"; the top 10% account for {pct(t10['share'],0)}" if t10 else "")
                + f". Median user cost is {usd(s.get('median_cost'))}. Cost control means "
                "watching the head of the tail, not the average."
            )
            tiles.append({"k": "Cost concentration (Gini)", "v": f"{g:.3f}",
                          "d": f"top 1% = {pct(t1['share'],0)} of spend", "cls": "acc"})

    curve = data.get("curve")
    if curve:
        def w0(ck):
            c = (curve.get(ck) or {}).get("curve") or []
            return c[0]["avg_per_user"] if c else None

        def wlast(ck):
            c = (curve.get(ck) or {}).get("curve") or []
            return c[-1]["avg_per_user"] if c else None
        a0, b0, c0 = w0("chart_a"), w0("chart_b"), w0("chart_c")
        alast = wlast("chart_a")
        if a0 is not None:
            seg = []
            if b0 is not None:
                seg.append(f"current-premium users start at <b>{usd(b0)}</b>/user in week 0")
            seg.append(f"all users at {usd(a0)}")
            if c0 is not None:
                seg.append(f"free users at just {usd(c0)}")
            sentence = ", ".join(seg)
            sentence = sentence[0].upper() + sentence[1:] + "."
            tail = (f" By the last complete week the all-users curve is down to {usd(alast)}."
                    if alast is not None else "")
            reads["curve"] = ("<b>Cost is front-loaded across every segment.</b> " + sentence + tail
                              + " The expensive moment is the onboarding week, not a standing burden."
                              + " Plotted on a log axis so premium, all-users, and free are all legible"
                              " despite the ~10× gap.")
            if b0 is not None:
                tiles.append({"k": "Premium cost, week 0", "v": usd(b0),
                              "d": (f"→ {usd(wlast('chart_b'))} by last week" if wlast('chart_b') is not None else ""), "cls": "cost"})
            tiles.append({"k": "New-user cost, week 0", "v": usd(a0),
                          "d": (f"decays to {usd(alast)}" if alast is not None else ""), "cls": ""})

    ret = data.get("retention")
    if ret:
        def r_at(ck, wk):
            for pnt in (ret.get(ck) or {}).get("curve") or []:
                if pnt.get("week_n") == wk:
                    return pnt.get("retention")
            return None
        b1, c1 = r_at("chart_b", 1), r_at("chart_c", 1)
        if b1 is not None or c1 is not None:
            seg = []
            if b1 is not None:
                seg.append(f"premium holds <b>{pct(b1,0)}</b> into week 1")
            if c1 is not None:
                seg.append(f"free-never-subscribed drops to {pct(c1,0)}")
            sent = ", ".join(seg)
            sent = (sent[0].upper() + sent[1:] + ".") if sent else ""
            ratio_txt = f" Premium retains ~{b1/c1:.0f}× better than free." if (b1 and c1) else ""
            reads["retention"] = (
                "<b>Premium users stick; free users churn after week 0.</b> " + sent + ratio_txt
                + " Retention is the second factor for lifetime cost — a premium user keeps incurring "
                "their weekly cost because they stay, while free users are gone (and near-zero cost anyway)."
            )
            if b1 is not None:
                tiles.append({"k": "Premium retention, wk 1", "v": pct(b1, 0),
                              "d": (f"free {pct(c1,0)}" if c1 is not None else ""), "cls": "pos"})

    cum = data.get("cumulative")
    if cum:
        def _cum_last(ck):
            c = (cum.get(ck) or {}).get("curve") or []
            return c[-1]["cum_per_user"] if c else None
        pb, pa, pc = _cum_last("chart_b"), _cum_last("chart_a"), _cum_last("chart_c")
        if pa is not None:
            reads["cumulative"] = (
                "<b>Cumulative cost-to-date per joined user.</b> By the last complete week, "
                + (f"premium (all-activated base) has cost <b>{usd(pb)}</b>/user, " if pb is not None else "")
                + f"all users {usd(pa)}"
                + (f", free {usd(pc)}" if pc is not None else "")
                + ". All-users and free are the running total of the decay curve above; premium here uses "
                "the <b>all-activated</b> base (the decay curve above is <b>current-premium</b>), so it "
                "reads lower and isn't that line's integral. Each flattens as weekly cost falls toward zero."
            )

    life = data.get("lifetime")
    if life:
        hz = life.get("horizons") or [52, 104]
        def _proj(ck, h):
            for pr in (life.get(ck) or {}).get("projections", []):
                if pr["horizon_weeks"] == h:
                    return pr["projected_lifetime_per_user"]
            return None
        b0, a0, c0 = _proj("chart_b", hz[0]), _proj("chart_a", hz[0]), _proj("chart_c", hz[0])
        b1 = _proj("chart_b", hz[-1])
        if b0 is not None or a0 is not None:
            reads["lifetime"] = (
                f"<b>Projected lifetime cost per user (to {hz[0]}w).</b> "
                + (f"Premium ≈ <b>{usd(b0)}</b>" if b0 is not None else "")
                + (f", all users {usd(a0)}" if a0 is not None else "")
                + (f", free {usd(c0)}" if c0 is not None else "")
                + f". {hz[-1]}w ≈ {hz[0]}w"
                + (f" (premium {usd(b1)})" if b1 is not None else "")
                + " — per-joined-user cost decays to ~0, so lifetime ≈ cost-to-date + a small tail. "
                "Projection, fit on a few weeks."
            )
            if b0 is not None:
                tiles.append({"k": f"Premium lifetime/user (~{hz[0]}w)", "v": usd(b0),
                              "d": (f"all users {usd(a0)}" if a0 is not None else ""), "cls": "cost"})

    ch = data.get("channel")
    if ch:
        paid = _get(ch.get("by_channel"), "channel", "paid")
        org = _get(ch.get("by_channel"), "channel", "organic")
        if paid and org and paid.get("avg_cost_per_user") and org.get("avg_cost_per_user"):
            ratio = paid["avg_cost_per_user"] / org["avg_cost_per_user"]
            reads["channel"] = (
                f"<b>Paid-acquired users cost ~{ratio:.1f}× organic to serve</b> "
                f"({usd(paid['avg_cost_per_user'])} vs {usd(org['avg_cost_per_user'])} per user) — "
                "they engage harder, especially on voice. Pair this with payback to judge "
                "true per-channel margin."
            )
            tiles.append({"k": "Paid vs organic / user", "v": f"{ratio:.1f}×",
                          "d": f"{usd(paid['avg_cost_per_user'])} paid · {usd(org['avg_cost_per_user'])} organic",
                          "cls": "cost"})

    free = data.get("free")
    if free:
        cv = free.get("converters", {})
        nb = free.get("never_bought", {})
        cr = free.get("conversion_rate")
        if cr is not None:
            ratio = (nb.get("avg_free_cost", 0) / cv["avg_free_cost"]) if cv.get("avg_free_cost") else None
            reads["free"] = (
                f"<b>Conversion is nearly free to acquire.</b> Converters cost just "
                f"{usd(cv.get('avg_free_cost'))} during their free period"
                + (f" and convert in ~{cv.get('avg_days_to_convert')} days" if cv.get("avg_days_to_convert") is not None else "")
                + (f"; never-buyers cost {ratio:.1f}× more ({usd(nb.get('avg_free_cost'))})" if ratio else "")
                + f". Free→paid conversion runs {pct(cr)}."
            )
            tiles.append({"k": "Free→paid conversion", "v": pct(cr), "d":
                          (f"converters cost {usd(cv.get('avg_free_cost'))} to acquire"), "cls": "pos"})

    mix = data.get("mix")
    if mix:
        paid = _get(mix.get("by_segment"), "subscription_state", "paid")
        freeg = _get(mix.get("by_segment"), "subscription_state", "free")
        if paid:
            free_vs = freeg.get("voice_share") if freeg else None
            lead = f"<b>Paid users are voice-led ({pct(paid.get('voice_share'),0)})"
            if free_vs is not None:
                lead += f", free users LLM-led ({pct(1 - free_vs, 0)})"
            lead += ".</b> "
            reads["mix"] = (lead + "Voice is the marginal cost of engagement — the more someone "
                            "talks to Aurie, the more their mix tilts to voice.")
            tiles.append({"k": "Paid voice share", "v": pct(paid.get("voice_share"), 0),
                          "d": "free users skew LLM", "cls": ""})

    pay = data.get("payback")
    if pay:
        cohorts = pay.get("cohorts", [])
        mature = [c for c in cohorts if c.get("payback_week") is not None]
        if mature:
            pws = sorted({c["payback_week"] for c in mature})
            margins = [c["margin_latest"] for c in mature if c.get("margin_latest") is not None]
            pw_txt = f"week {pws[0]}" if len(pws) == 1 else f"weeks {pws[0]}–{pws[-1]}"
            if margins:
                mn, mx = min(margins), max(margins)
                margin_txt = (f"a <b>{pct(mn,0)}</b> margin" if abs(mx - mn) < 1e-9
                              else f"latest margins of <b>{pct(mn,0)} to {pct(mx,0)}</b>")
            else:
                margin_txt = "a positive margin"
            reads["payback"] = (
                f"<b>{len(mature)} of {len(cohorts)} cohorts have paid back</b> — by {pw_txt}, at "
                f"{margin_txt}. Revenue is amortized evenly across each subscription period, so newer "
                "cohorts still read negative simply because their year/month hasn't been recognized yet."
            )
        else:
            reads["payback"] = ("Revenue is amortized (recognized) evenly across each subscription "
                                "period; cohorts turn positive once enough of the period is recognized.")

    ten = data.get("tenure")
    if ten and ten.get("days"):
        days = ten["days"]
        first_new = days[0].get("new_cost", 0)
        last_new = days[-1].get("new_cost", 0)
        reads["tenure"] = (
            "<b>New-user cost tracks acquisition.</b> Daily spend from users in their first week "
            f"moved from {usd(first_new)} to {usd(last_new)} across the window while established-user "
            "cost stayed comparatively flat — growth, not per-head drift, drives the daily total."
        )

    coh = data.get("cohort")
    if coh:
        cov = coh.get("coverage", {})
        reads["cohort"] = (
            "<b>Read down a column to see the decay.</b> Each cell is average cost per joined user "
            "at that age; darker = more expensive. Week 0 dominates every segment, premium runs "
            "several× hotter than free, and the triangle shortens because younger cohorts have fewer "
            "complete weeks. The pooled curve below is this triangle's column-average."
        )

    daily = data.get("daily")
    if daily:
        s = daily.get("summary", {})
        days = daily.get("days", [])
        llm_t, voice_t = s.get("llm_total"), s.get("voice_total")
        split = ""
        if llm_t is not None and voice_t is not None and (llm_t + voice_t) > 0:
            tot = llm_t + voice_t
            split = f" — LLM {pct(llm_t/tot,0)} / voice {pct(voice_t/tot,0)}"
        act = [d.get("active", 0) for d in days if d.get("active") is not None]
        avg_act = sum(act) / len(act) if act else None
        reads["daily"] = (
            f"<b>{usd(s.get('total'),0)} over {s.get('days')} days</b> (~{usd(s.get('avg'),0)}/day{split}). "
            + (f"Active users averaged {num(avg_act)}/day. " if avg_act else "")
            + "Bars are daily cost (LLM + voice); the lines overlay active and paid users on the right axis."
        )
        if s.get("total") is not None:
            tiles.insert(0, {"k": f"Total cost · {s.get('days')}d", "v": usd(s.get('total'), 0),
                             "d": f"~{usd(s.get('avg'),0)}/day", "cls": "cost"})

    revenue = data.get("revenue")
    if revenue:
        tot = revenue.get("totals", {})
        bg, bn = tot.get("bookings_gross"), tot.get("bookings_net")
        rg = tot.get("recognized_gross")
        rec = revenue.get("recognized", []) or []
        latest = rec[-1] if rec else {}
        arpu, subs = latest.get("arpu_net"), latest.get("active_subscribers")
        reads["revenue"] = (
            f"<b>{usd(bg,0)} booked</b> (net {usd(bn,0)} after fees) vs <b>{usd(rg,0)} recognized</b> "
            "to date — the gap is deferred annual revenue not yet earned. Bars are bookings (cash on "
            "the purchase day); the line is recognized (amortized) revenue."
            + (f" Latest {revenue.get('grain','')} net ARPU {usd(arpu)} across {num(subs)} active subscribers." if arpu is not None else "")
        )
        if bg is not None:
            tiles.append({"k": "Revenue · booked", "v": usd(bg, 0),
                          "d": f"net {usd(bn,0)} · {usd(rg,0)} recognized", "cls": "pos"})

    rtier = data.get("revenuetier")
    if rtier:
        by_tier = sorted(rtier.get("by_tier", []), key=lambda x: -(x.get("bookings_gross") or 0))
        by_bill = {b.get("billing"): b for b in rtier.get("by_billing", [])}
        tot = rtier.get("totals", {}).get("bookings_gross") or 0
        top = by_tier[0] if by_tier else {}
        top_share = (top.get("bookings_gross") or 0) / tot if tot else 0
        ann = (by_bill.get("annual", {}).get("bookings_gross") or 0)
        mon = (by_bill.get("monthly", {}).get("bookings_gross") or 0)
        ann_share = ann / (ann + mon) if (ann + mon) else 0
        reads["revenuetier"] = (
            f"<b>{top.get('tier','—')}</b> leads with {usd(top.get('bookings_gross'),0)} "
            f"({pct(top_share,0)} of booked revenue). "
            f"Annual is {pct(ann_share,0)} of bookings ({usd(ann,0)} vs {usd(mon,0)} monthly). "
            "Each bar is a tier's booked revenue, split annual vs monthly."
        )
        if ann + mon > 0:
            tiles.append({"k": "Annual share", "v": pct(ann_share, 0),
                          "d": f"{usd(ann,0)} annual · {usd(mon,0)} monthly", "cls": "acc"})

    meta = {"window": window_label, "generated": data.get("_generated", ""),
            "skill_id": _skill_ident(), "coverage": ""}
    if coh and coh.get("coverage"):
        cov = coh["coverage"]
        meta["coverage"] = f"{num(cov.get('cost_users'))} users · {pct((cov.get('with_ai_created',0)/cov['cost_users']) if cov.get('cost_users') else 0,0)} attributed"
    # per-skill versions for the card labels — from each chart's emitted "skill_version" (never a
    # sibling SKILL.md read here). Keyed by skill id so the JS can match each <span class="skill">.
    versions = {}
    for key, skill_id in KEY_SKILL.items():
        v = (data.get(key) or {}).get("skill_version")
        if v:
            versions[skill_id] = v

    return {"meta": meta, "tiles": tiles, "reads": reads,
            "excluded_html": _excluded_callout(margin), "versions": versions}


# --------------------------------- HTML -------------------------------------
HTML_TEMPLATE = r"""<title>__TITLE__</title>
<style>
  :root{
    --bg:#f7f9fa;--surface:#fff;--surface-2:#eef2f5;--ink:#16202a;--muted:#5a6b78;--faint:#8798a3;
    --line:#dde5ea;--grid:#e9eef1;--accent:#0d7d8c;--cost:#df7f38;--revenue:#1f9d6b;--neg:#d1495b;
    --voice:#7b68d4;--llm:#0d7d8c;--seg-paid:#0d7d8c;--seg-free:#8aa0ac;--seg-trial:#df7f38;--seg-grace:#d1495b;
    --curveA:#0d7d8c;--curveB:#df7f38;--curveC:#1f9d6b;
    --shadow:0 1px 2px rgba(20,32,42,.06),0 8px 24px -12px rgba(20,32,42,.16);
    --mono:ui-monospace,"SF Mono","JetBrains Mono",Menlo,Consolas,monospace;
    --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,"Helvetica Neue",sans-serif;
  }
  @media (prefers-color-scheme:dark){:root{
    --bg:#0d1117;--surface:#161c23;--surface-2:#1c242c;--ink:#e8eff3;--muted:#93a4b0;--faint:#6b7c88;
    --line:#28323b;--grid:#232c34;--accent:#2fbccb;--cost:#efa25c;--revenue:#46c088;--neg:#e86577;
    --voice:#9a88e8;--llm:#2fbccb;--seg-paid:#2fbccb;--seg-free:#7d909c;--seg-trial:#efa25c;--seg-grace:#e86577;
    --curveA:#2fbccb;--curveB:#efa25c;--curveC:#46c088;
    --shadow:0 1px 2px rgba(0,0,0,.4),0 10px 30px -14px rgba(0,0,0,.6);
  }}
  :root[data-theme="light"]{
    --bg:#f7f9fa;--surface:#fff;--surface-2:#eef2f5;--ink:#16202a;--muted:#5a6b78;--faint:#8798a3;
    --line:#dde5ea;--grid:#e9eef1;--accent:#0d7d8c;--cost:#df7f38;--revenue:#1f9d6b;--neg:#d1495b;
    --voice:#7b68d4;--llm:#0d7d8c;--seg-paid:#0d7d8c;--seg-free:#8aa0ac;--seg-trial:#df7f38;--seg-grace:#d1495b;
    --curveA:#0d7d8c;--curveB:#df7f38;--curveC:#1f9d6b;
    --shadow:0 1px 2px rgba(20,32,42,.06),0 8px 24px -12px rgba(20,32,42,.16);
  }
  :root[data-theme="dark"]{
    --bg:#0d1117;--surface:#161c23;--surface-2:#1c242c;--ink:#e8eff3;--muted:#93a4b0;--faint:#6b7c88;
    --line:#28323b;--grid:#232c34;--accent:#2fbccb;--cost:#efa25c;--revenue:#46c088;--neg:#e86577;
    --voice:#9a88e8;--llm:#2fbccb;--seg-paid:#2fbccb;--seg-free:#7d909c;--seg-trial:#efa25c;--seg-grace:#e86577;
    --curveA:#2fbccb;--curveB:#efa25c;--curveC:#46c088;
    --shadow:0 1px 2px rgba(0,0,0,.4),0 10px 30px -14px rgba(0,0,0,.6);
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);line-height:1.55;font-size:15px;-webkit-font-smoothing:antialiased}
  .wrap{max-width:1080px;margin:0 auto;padding:48px 24px 80px}
  .num{font-variant-numeric:tabular-nums}
  header{margin-bottom:30px}
  .eyebrow{font-family:var(--mono);font-size:11.5px;letter-spacing:.14em;text-transform:uppercase;color:var(--accent);font-weight:600}
  h1{font-size:clamp(27px,4.2vw,40px);line-height:1.05;letter-spacing:-.022em;font-weight:800;margin:.28em 0 .12em;text-wrap:balance}
  .sub{color:var(--muted);font-size:15.5px;max-width:64ch}
  .prov{display:flex;flex-wrap:wrap;gap:8px;margin-top:18px}
  .chip{font-family:var(--mono);font-size:11.5px;color:var(--muted);background:var(--surface-2);border:1px solid var(--line);border-radius:999px;padding:4px 11px}
  .chip b{color:var(--ink);font-weight:600}
  .callout{display:none;flex-direction:column;gap:6px;margin-top:14px;padding:11px 15px;font-size:13px;color:var(--muted);background:var(--surface-2);border:1px solid var(--line);border-left:3px solid var(--cost);border-radius:8px}
  .callout:not(:empty){display:flex}
  .callout b{color:var(--ink)}
  .callout code{font-family:var(--mono);font-size:12px;background:var(--surface);border:1px solid var(--line);border-radius:4px;padding:1px 5px}
  .callout .callout-note{font-size:11.5px;color:var(--faint);margin-top:2px}
  .reads{display:grid;grid-template-columns:repeat(auto-fit,minmax(158px,1fr));gap:12px;margin:24px 0 8px}
  .tile{background:var(--surface);border:1px solid var(--line);border-radius:13px;padding:14px 15px;box-shadow:var(--shadow)}
  .tile .k{font-size:11.5px;color:var(--muted)}
  .tile .v{font-family:var(--mono);font-size:23px;font-weight:600;letter-spacing:-.01em;margin-top:3px}
  .tile .d{font-size:11.5px;color:var(--faint);margin-top:3px}
  .tile .v.pos{color:var(--revenue)}.tile .v.cost{color:var(--cost)}.tile .v.acc{color:var(--accent)}.tile .v.neg{color:var(--neg)}
  .cards{display:grid;gap:20px;margin-top:22px}
  .card{background:var(--surface);border:1px solid var(--line);border-radius:16px;padding:22px 22px 20px;box-shadow:var(--shadow)}
  .card-hd{display:flex;justify-content:space-between;align-items:baseline;gap:14px;flex-wrap:wrap}
  .skill{font-family:var(--mono);font-size:12px;color:var(--accent);font-weight:600}
  .tag{font-family:var(--mono);font-size:10.5px;color:var(--faint);border:1px solid var(--line);border-radius:6px;padding:2px 7px}
  .card h2{font-size:19px;font-weight:700;letter-spacing:-.015em;margin:8px 0 2px}
  .q{color:var(--muted);font-size:13.5px;margin:0 0 12px}
  .read{color:var(--muted);font-size:13px;margin-top:12px;padding-top:12px;border-top:1px solid var(--line)}
  .read b{color:var(--ink);font-weight:600}
  .foot{color:var(--faint);font-size:11.5px;margin-top:7px}
  .two{display:grid;grid-template-columns:1fr 1fr;gap:20px}
  @media(max-width:720px){.two{grid-template-columns:1fr}}
  .legend{display:flex;flex-wrap:wrap;gap:14px;margin:2px 0 10px;font-size:12.5px;color:var(--muted)}
  .legend span{display:inline-flex;align-items:center;gap:6px}
  .sw{width:12px;height:12px;border-radius:3px;display:inline-block}
  .sw.ln{width:14px;height:0;border-top:3px solid;border-radius:0}
  .chart{width:100%;height:auto;display:block;overflow:visible}
  .grid{stroke:var(--grid);stroke-width:1}.axis{stroke:var(--line);stroke-width:1}
  .line{fill:none;stroke-width:2.25;stroke-linejoin:round;stroke-linecap:round}
  .dot{stroke:var(--surface);stroke-width:1.5}.area{opacity:.13;stroke:none}
  .tick{fill:var(--faint);font-size:11px;font-family:var(--mono)}
  .ty{text-anchor:end;dominant-baseline:middle}.tyr{text-anchor:start;dominant-baseline:middle}.tx{text-anchor:middle}
  .vlabel{fill:var(--ink);font-size:11px;font-family:var(--mono);font-weight:600}
  .barlbl{fill:var(--muted);font-size:11.5px;font-family:var(--sans)}
  .s-cost{stroke:var(--cost)}.f-cost{fill:var(--cost)}.d-cost{fill:var(--cost)}
  .s-rev{stroke:var(--revenue)}.f-rev{fill:var(--revenue)}.d-rev{fill:var(--revenue)}
  .s-acc{stroke:var(--accent)}.f-acc{fill:var(--accent)}.d-acc{fill:var(--accent)}
  .s-est{stroke:var(--seg-free)}.d-est{fill:var(--seg-free)}
  .s-A{stroke:var(--curveA)}.d-A{fill:var(--curveA)}.s-B{stroke:var(--curveB)}.d-B{fill:var(--curveB)}.s-C{stroke:var(--curveC)}.d-C{fill:var(--curveC)}
  .f-llm{fill:var(--llm)}.f-voice{fill:var(--voice)}
  .f-paid{fill:var(--seg-paid)}.f-free{fill:var(--seg-free)}.f-trial{fill:var(--seg-trial)}.f-grace{fill:var(--seg-grace)}
  .f-neg{fill:var(--neg)}.s-neg{stroke:var(--neg)}.d-neg{fill:var(--neg)}.diag{stroke:var(--faint);stroke-width:1;stroke-dasharray:4 4;fill:none}.f-muted{fill:var(--surface-2)}
  .heatwrap{overflow-x:auto}
  .heat{border-collapse:separate;border-spacing:2px;font-family:var(--mono);font-size:10.5px}
  .heat th{color:var(--faint);font-weight:500;font-size:10px;padding:2px 4px;text-align:center}
  .heat td.rh{color:var(--muted);text-align:right;padding-right:6px;white-space:nowrap}
  .heat td.c{width:44px;height:26px;text-align:center;border-radius:4px;color:var(--ink);white-space:nowrap}
  .heat td.c.hi{color:#fff}
  .heat td.c.empty{background:transparent!important}
  .heatgrid{display:grid;gap:18px;grid-template-columns:1fr}
  @media(min-width:900px){.heatgrid{grid-template-columns:1fr 1fr}}
  .heatseg .cap{font-size:12.5px;font-weight:600;margin:0 0 6px}
  .heatseg .cap small{color:var(--faint);font-weight:400}
  .nd{color:var(--faint);font-size:13px;font-family:var(--mono);padding:24px 0;text-align:center}
  footer{margin-top:40px;color:var(--faint);font-size:12px;font-family:var(--mono);text-align:center}
</style>

<div class="wrap">
  <header>
    <div class="eyebrow">Aurie · AI cost &amp; margin analytics</div>
    <h1>__TITLE__</h1>
    <p class="sub">The full per-user cost picture in one place — daily spend and active users, daily
      contribution margin, the cohort decay heatmap, and seven economics views — each generated live
      from the cost warehouse with a short read of what it shows.</p>
    <div class="prov" id="prov"></div>
    <div class="callout" id="callout-excluded"></div>
  </header>

  <section class="reads" id="tiles" aria-label="Key reads"></section>

  <div class="cards">
    <article class="card">
      <div class="card-hd"><span class="skill">eng:chart-aurie-daily-cost</span><span class="tag">daily</span></div>
      <h2>Daily cost with active users</h2>
      <p class="q">How much does Aurie spend per day, and how does that track the users who showed up?</p>
      <div class="legend">
        <span><i class="sw f-llm" style="background:var(--llm)"></i>LLM cost</span>
        <span><i class="sw" style="background:var(--voice)"></i>Voice cost</span>
        <span><i class="sw ln" style="border-color:var(--accent)"></i>Active users</span>
        <span><i class="sw ln" style="border-color:var(--revenue)"></i>Paid users</span>
      </div>
      <div class="viz" id="viz-daily"></div>
      <p class="read" id="read-daily"></p>
    </article>

    <article class="card">
      <div class="card-hd"><span class="skill">eng:chart-aurie-daily-margin</span><span class="tag">margin</span></div>
      <h2>Daily contribution margin — net revenue vs cost</h2>
      <p class="q">After amortized subscription revenue, are we net-positive or net-negative each day — and how is the cumulative total trending?</p>
      <div class="two">
        <div>
          <div class="legend">
            <span><i class="sw ln" style="border-color:var(--accent)"></i>Net margin/day</span>
            <span><i class="sw ln" style="border-color:var(--faint);border-top-style:dashed"></i>Break-even</span>
          </div>
          <div class="viz" id="viz-margin-daily"></div>
          <p class="foot">Amortized net revenue minus total cost, per day.</p>
        </div>
        <div>
          <div class="legend"><span><i class="sw ln" style="border-color:var(--neg)"></i>Cumulative net</span></div>
          <div class="viz" id="viz-margin-cumulative"></div>
          <p class="foot">Running total of daily net since the window start.</p>
        </div>
      </div>
      <p class="read" id="read-margin"></p>
    </article>

    <article class="card">
      <div class="card-hd"><span class="skill">eng:chart-aurie-revenue</span><span class="tag">revenue</span></div>
      <h2>Subscription revenue over time — bookings vs recognized</h2>
      <p class="q">How much revenue is Aurie bringing in — cash booked on the purchase day vs amortized (recognized) to date?</p>
      <div class="legend">
        <span><i class="sw" style="background:var(--revenue)"></i>Bookings gross</span>
        <span><i class="sw ln" style="border-color:var(--accent)"></i>Recognized gross</span>
        <span><i class="sw ln" style="border-color:var(--faint);border-top-style:dashed"></i>Active subscribers</span>
      </div>
      <div class="viz" id="viz-revenue"></div>
      <p class="read" id="read-revenue"></p>
    </article>

    <article class="card">
      <div class="card-hd"><span class="skill">eng:chart-aurie-revenue-by-tier</span><span class="tag">by tier</span></div>
      <h2>Revenue by tier &amp; billing period</h2>
      <p class="q">Which plan brings in the most booked revenue, and is annual or monthly bigger?</p>
      <div class="legend">
        <span><i class="sw" style="background:var(--revenue)"></i>Annual</span>
        <span><i class="sw" style="background:var(--accent)"></i>Monthly</span>
      </div>
      <div class="viz" id="viz-revenuetier"></div>
      <p class="read" id="read-revenuetier"></p>
    </article>

    <article class="card">
      <div class="card-hd"><span class="skill">eng:chart-aurie-cohort-cost</span><span class="tag">heatmap</span></div>
      <h2>Cohort cost heatmap — weeks since join</h2>
      <p class="q">Read a row across to watch a join-cohort's average cost/user decay week by week.</p>
      <div class="heatgrid" id="viz-cohort"></div>
      <p class="read" id="read-cohort"></p>
    </article>

    <article class="card">
      <div class="card-hd"><span class="skill">eng:chart-aurie-cost-curve</span><span class="tag">curve</span></div>
      <h2>Cost decay curve — pooled by segment</h2>
      <p class="q">The heatmap's column-average: one decay line per segment.</p>
      <div class="legend">
        <span><i class="sw" style="background:var(--curveB)"></i>Current premium</span>
        <span><i class="sw" style="background:var(--curveA)"></i>All AI users</span>
        <span><i class="sw" style="background:var(--curveC)"></i>Free, never subscribed</span>
      </div>
      <div class="viz" id="viz-curve"></div>
      <p class="read" id="read-curve"></p>
    </article>

    <article class="card">
      <div class="card-hd"><span class="skill">eng:chart-aurie-retention</span><span class="tag">retention</span></div>
      <h2>Activity retention — pooled by segment</h2>
      <p class="q">Of a cohort active at join, what share is still active each week after? (the second factor for lifetime cost)</p>
      <div class="legend">
        <span><i class="sw" style="background:var(--curveB)"></i>Premium (since activation)</span>
        <span><i class="sw" style="background:var(--curveA)"></i>All AI users</span>
        <span><i class="sw" style="background:var(--curveC)"></i>Free, never subscribed</span>
      </div>
      <div class="viz" id="viz-retention"></div>
      <p class="read" id="read-retention"></p>
    </article>

    <article class="card">
      <div class="card-hd"><span class="skill">eng:chart-aurie-retention</span><span class="tag">heatmap</span></div>
      <h2>Retention heatmap — weeks since join</h2>
      <p class="q">Read a row across to watch one join-cohort's active-retention decay week by week.</p>
      <div class="heatgrid" id="viz-retention-heat"></div>
    </article>

    <article class="card">
      <div class="card-hd"><span class="skill">eng:chart-aurie-cumulative-cost</span><span class="tag">cumulative</span></div>
      <h2>Cumulative cost-to-date — per joined user</h2>
      <p class="q">Running total of the decay curve: how much have we spent, cumulatively, per joined user by week N?</p>
      <div class="legend">
        <span><i class="sw" style="background:var(--curveB)"></i>Premium (all-activated)</span>
        <span><i class="sw" style="background:var(--curveA)"></i>All AI users</span>
        <span><i class="sw" style="background:var(--curveC)"></i>Free, never subscribed</span>
      </div>
      <div class="viz" id="viz-cumulative"></div>
      <p class="read" id="read-cumulative"></p>
    </article>

    <article class="card">
      <div class="card-hd"><span class="skill">eng:chart-aurie-lifetime-cost</span><span class="tag">projection</span></div>
      <h2>Projected lifetime cost per user — to 52w / 104w</h2>
      <p class="q">Extrapolating the decay to a horizon: estimated lifetime cost per joined user (projection, directional — always fit on all data since store start).</p>
      <div class="viz" id="viz-lifetime"></div>
      <p class="read" id="read-lifetime"></p>
    </article>

    <article class="card">
      <div class="card-hd"><span class="skill">eng:chart-aurie-cost-concentration</span><span class="tag">Gini</span></div>
      <h2>Cost concentration — Lorenz curve</h2>
      <p class="q">Is spend spread across users, or does a tiny tail dominate?</p>
      <div class="legend">
        <span><i class="sw" style="background:var(--accent)"></i>Cost held by top X% of users</span>
        <span><i class="sw ln" style="border-color:var(--faint);border-top-style:dashed"></i>Perfect equality</span>
      </div>
      <div class="viz" id="viz-concentration"></div>
      <p class="read" id="read-concentration"></p>
    </article>

    <article class="card">
      <div class="card-hd"><span class="skill">eng:chart-aurie-tenure-cost</span><span class="tag">tenure</span></div>
      <h2>Daily cost — new vs established users</h2>
      <p class="q">Each day, how much cost comes from users in their first week vs everyone older?</p>
      <div class="legend">
        <span><i class="sw" style="background:var(--cost)"></i>New (&lt; 7 days)</span>
        <span><i class="sw" style="background:var(--seg-free)"></i>Established</span>
      </div>
      <div class="viz" id="viz-tenure"></div>
      <p class="read" id="read-tenure"></p>
    </article>

    <article class="card">
      <div class="card-hd"><span class="skill">eng:chart-aurie-cohort-payback</span><span class="tag">payback</span></div>
      <h2>Cohort payback — cost vs recognized revenue</h2>
      <p class="q">For a paid cohort, when does cumulative recognized revenue cross cumulative cost?</p>
      <div class="two">
        <div>
          <div class="legend">
            <span><i class="sw" style="background:var(--cost)"></i>Cum cost/user</span>
            <span><i class="sw" style="background:var(--revenue)"></i>Cum revenue/user</span>
          </div>
          <div class="viz" id="viz-payback"></div>
          <p class="foot" id="foot-payback"></p>
        </div>
        <div>
          <div class="legend"><span>Latest margin by cohort</span></div>
          <div class="viz" id="viz-margin"></div>
          <p class="foot">Younger cohorts are immature — only 1–3 weeks of revenue recognized.</p>
        </div>
      </div>
      <p class="read" id="read-payback"></p>
    </article>

    <article class="card">
      <div class="card-hd"><span class="skill">eng:chart-aurie-cost-mix</span><span class="tag">mix</span></div>
      <h2>Cost mix — LLM vs voice by segment</h2>
      <p class="q">Which cost driver dominates, and does it differ between paid and free users?</p>
      <div class="legend">
        <span><i class="sw" style="background:var(--llm)"></i>LLM</span>
        <span><i class="sw" style="background:var(--voice)"></i>Voice</span>
      </div>
      <div class="viz" id="viz-mix"></div>
      <p class="read" id="read-mix"></p>
    </article>

    <article class="card">
      <div class="card-hd"><span class="skill">eng:chart-aurie-free-user-cost</span><span class="tag">free</span></div>
      <h2>Free-user cost — converters vs never-bought</h2>
      <p class="q">What do we spend on free users before they convert (or don't)?</p>
      <div class="two">
        <div>
          <div class="legend"><span>Avg free-period cost / user</span></div>
          <div class="viz" id="viz-free-avg"></div>
        </div>
        <div>
          <div class="legend">
            <span><i class="sw" style="background:var(--revenue)"></i>Converters</span>
            <span><i class="sw" style="background:var(--seg-free)"></i>Never bought</span>
          </div>
          <div class="viz" id="viz-free-dist"></div>
          <p class="foot">Users bucketed by total free-period cost ($).</p>
        </div>
      </div>
      <p class="read" id="read-free"></p>
    </article>

    <article class="card">
      <div class="card-hd"><span class="skill">eng:chart-aurie-cost-by-channel</span><span class="tag">channel</span></div>
      <h2>Cost by acquisition channel &amp; campaign</h2>
      <p class="q">Do paid-acquired users cost more to serve than organic ones — and which campaigns?</p>
      <div class="two">
        <div>
          <div class="legend"><span>Avg cost / user by channel</span></div>
          <div class="viz" id="viz-channel"></div>
        </div>
        <div>
          <div class="legend"><span>Top campaigns — avg cost / user</span></div>
          <div class="viz" id="viz-campaign"></div>
        </div>
      </div>
      <p class="read" id="read-channel"></p>
    </article>
  </div>

  <footer id="footer"></footer>
</div>

<script>
(function(){
  "use strict";
  var DATA = __DATA_JSON__;
  var REPORT = __REPORT_JSON__;
  var USD=function(v,d){return '$'+(v==null?0:v).toFixed(d==null?2:d);};
  var PCT=function(v){return (v*100).toFixed(0)+'%';};
  var el=function(id){return document.getElementById(id);};
  var nd=function(){return '<div class="nd">data unavailable</div>';};

  // ---- header / tiles ----
  try{
    var m=REPORT.meta||{}, prov=[];
    if(m.window) prov.push('window <b>'+m.window+'</b>');
    if(m.coverage) prov.push(m.coverage.replace(/^(\S+ \S+)/,'<b>$1</b>'));
    prov.push('source <b>aura-main.aurie_cost</b>');
    prov.push('<b>aggregate-only</b> · no UIDs');
    el('prov').innerHTML = prov.map(function(p){return '<span class="chip">'+p+'</span>';}).join('');
    if(REPORT.excluded_html) el('callout-excluded').innerHTML = REPORT.excluded_html;
    el('tiles').innerHTML = (REPORT.tiles||[]).map(function(t){
      return '<div class="tile"><div class="k">'+t.k+'</div><div class="v '+(t.cls||'')+' num">'+t.v+
             '</div><div class="d">'+(t.d||'')+'</div></div>';
    }).join('');
    el('footer').textContent='Generated '+(m.generated||'')+' · '+(m.skill_id||'eng:build-aurie-cost-report')+' · aura-main.aurie_cost · aggregate-only, no user identifiers';
    // stamp each card's skill label with its own version (from the chart's emitted skill_version)
    var V=REPORT.versions||{};
    Array.prototype.forEach.call(document.querySelectorAll('.skill'),function(s){
      var name=s.textContent.trim(), v=V[name];
      if(v && name.indexOf(':'+v)<0) s.textContent=name+':'+v;
    });
  }catch(e){}

  function setRead(key){var n=el('read-'+key); if(n&&REPORT.reads&&REPORT.reads[key]) n.innerHTML=REPORT.reads[key];}

  // ---- generic cartesian (lines/areas) ----
  function cartesian(o){
    var w=o.w||760,h=o.h||280,pL=o.pL==null?46:o.pL,pR=o.pR==null?14:o.pR,pT=o.pT==null?14:o.pT,pB=o.pB==null?28:o.pB;
    var pw=w-pL-pR,ph=h-pT-pB,x=o.x,y=o.y;
    var sx=function(v){return pL+(v-x[0])/(x[1]-x[0])*pw;},sy=function(v){return pT+ph-(v-y[0])/(y[1]-y[0])*ph;};
    var g='';
    (o.yticks||[]).forEach(function(t){var yy=sy(t.v);g+='<line class="grid" x1="'+pL+'" y1="'+yy+'" x2="'+(w-pR)+'" y2="'+yy+'"/>';g+='<text class="tick ty" x="'+(pL-8)+'" y="'+yy+'">'+t.label+'</text>';});
    (o.xticks||[]).forEach(function(t){var xx=sx(t.v);g+='<text class="tick tx" x="'+xx+'" y="'+(h-9)+'">'+t.label+'</text>';});
    (o.series||[]).forEach(function(s){
      var pts=s.pts.map(function(p){return [sx(p[0]),sy(p[1])];});
      if(s.area){var d='M '+pts[0][0]+' '+sy(y[0])+' '+pts.map(function(p){return 'L '+p[0]+' '+p[1];}).join(' ')+' L '+pts[pts.length-1][0]+' '+sy(y[0])+' Z';g+='<path class="area '+(s.acls||('f-'+s.key))+'" d="'+d+'"/>';}
      var dl='M '+pts.map(function(p){return p[0].toFixed(1)+' '+p[1].toFixed(1);}).join(' L ');
      g+='<path class="line s-'+s.key+'" d="'+dl+'"'+(s.dash?' stroke-dasharray="'+s.dash+'"':'')+'/>';
      if(s.dots) pts.forEach(function(p){g+='<circle class="dot d-'+s.key+'" cx="'+p[0]+'" cy="'+p[1]+'" r="3.1"/>';});
    });
    g+='<line class="axis" x1="'+pL+'" y1="'+sy(y[0])+'" x2="'+(w-pR)+'" y2="'+sy(y[0])+'"/>';
    (o.marks||[]).forEach(function(mk){g+='<text class="vlabel" x="'+sx(mk.x)+'" y="'+(sy(mk.y)-8)+'" text-anchor="'+(mk.anchor||'middle')+'">'+mk.t+'</text>';});
    return '<svg viewBox="0 0 '+w+' '+h+'" class="chart" role="img">'+g+(o.extra||'')+'</svg>';
  }
  function svg(g,w,h){return '<svg viewBox="0 0 '+w+' '+h+'" class="chart" role="img">'+g+'</svg>';}
  function niceMax(v){if(v<=0)return 1;var e=Math.pow(10,Math.floor(Math.log10(v)));var n=v/e;var s=n<=1?1:n<=2?2:n<=5?5:10;return s*e;}

  // ---- DAILY: stacked cost bars + active/paid user lines (dual axis) ----
  function renderDaily(){
    var d=DATA.daily; if(!d||!d.days||!d.days.length){el('viz-daily').innerHTML=nd();return;}
    var days=d.days,n=days.length,w=760,h=310,pL=52,pR=52,pT=14,pB=30,pw=w-pL-pR,ph=h-pT-pB;
    var maxCost=niceMax(Math.max.apply(null,days.map(function(x){return x.total||0;})));
    var maxUsers=niceMax(Math.max.apply(null,days.map(function(x){return Math.max(x.active||0,x.paid||0);})));
    var gwid=pw/n,bw=Math.max(2,gwid*0.68);
    var bx=function(i){return pL+gwid*(i+0.5);};
    var syC=function(v){return pT+ph-v/maxCost*ph;},syU=function(v){return pT+ph-v/maxUsers*ph;};
    var g='';
    for(var t=0;t<=4;t++){var cv=maxCost*t/4,yy=syC(cv);g+='<line class="grid" x1="'+pL+'" y1="'+yy+'" x2="'+(w-pR)+'" y2="'+yy+'"/>';g+='<text class="tick ty" x="'+(pL-7)+'" y="'+yy+'">$'+Math.round(cv)+'</text>';var uv=maxUsers*t/4;g+='<text class="tick tyr" x="'+(w-pR+7)+'" y="'+syU(uv)+'">'+Math.round(uv)+'</text>';}
    days.forEach(function(x,i){
      var llm=x.llm||0,voice=x.voice||0,hl=llm/maxCost*ph,hv=voice/maxCost*ph,x0=bx(i)-bw/2;
      g+='<rect class="f-llm" x="'+x0+'" y="'+syC(llm)+'" width="'+bw+'" height="'+hl+'" rx="1.5"/>';
      g+='<rect class="f-voice" x="'+x0+'" y="'+(syC(llm)-hv)+'" width="'+bw+'" height="'+hv+'" rx="1.5"/>';
    });
    function lineFor(field,cls){var pts=days.map(function(x,i){return [bx(i),syU(x[field]||0)];});return '<path class="line s-'+cls+'" d="M '+pts.map(function(p){return p[0].toFixed(1)+' '+p[1].toFixed(1);}).join(' L ')+'"/>';}
    g+=lineFor('active','acc');
    g+='<path class="line s-rev" stroke-dasharray="5 3" d="M '+days.map(function(x,i){return bx(i).toFixed(1)+' '+syU(x.paid||0).toFixed(1);}).join(' L ')+'"/>';
    var step=Math.ceil(n/6);
    days.forEach(function(x,i){if(i%step===0||i===n-1){var lab=(x.date||'').slice(5);g+='<text class="tick tx" x="'+bx(i)+'" y="'+(h-9)+'">'+lab+'</text>';}});
    g+='<line class="axis" x1="'+pL+'" y1="'+syC(0)+'" x2="'+(w-pR)+'" y2="'+syC(0)+'"/>';
    el('viz-daily').innerHTML=svg(g,w,h);
  }

  // ---- MARGIN: daily net (amortized net revenue - cost) with a zero baseline ----
  function renderMarginDaily(){
    var d=DATA.margin; if(!d||!d.daily||!d.daily.length){el('viz-margin-daily').innerHTML=nd();return;}
    var days=d.daily,n=days.length,w=520,h=270,pL=54,pR=14,pT=14,pB=28,ph=h-pT-pB;
    var maxAbs=Math.max.apply(null,days.map(function(x){return Math.abs(x.net||0);}).concat([1]));
    var maxY=niceMax(maxAbs);
    var pts=days.map(function(x,i){return [i,x.net||0];});
    var yt=[-1,-0.5,0,0.5,1].map(function(f){return {v:maxY*f,label:'$'+Math.round(maxY*f)};});
    var step=Math.max(1,Math.ceil(n/6)),xt=[],i;
    for(i=0;i<n;i+=step)xt.push({v:i,label:(days[i].date||'').slice(5)});
    if(xt.length&&xt[xt.length-1].v!==n-1)xt.push({v:n-1,label:(days[n-1].date||'').slice(5)});
    var sy0=pT+ph-(0-(-maxY))/(maxY-(-maxY))*ph;
    var base=cartesian({w:w,h:h,pL:pL,pR:pR,pT:pT,pB:pB,x:[0,n-1],y:[-maxY,maxY],yticks:yt,xticks:xt,
      series:[{key:'acc',pts:pts}]});
    base=base.replace('<line class="axis"','<line class="diag" x1="'+pL+'" y1="'+sy0+'" x2="'+(w-pR)+'" y2="'+sy0+'"/><line class="axis"');
    el('viz-margin-daily').innerHTML=base;
  }

  // ---- MARGIN: cumulative net-to-date ----
  function renderMarginCumulative(){
    var d=DATA.margin; if(!d||!d.cumulative||!d.cumulative.length){el('viz-margin-cumulative').innerHTML=nd();return;}
    var days=d.cumulative,n=days.length;
    var pts=days.map(function(x,i){return [i,x.cumulative_net||0];});
    var vals=days.map(function(x){return x.cumulative_net||0;});
    var minV=Math.min.apply(null,vals.concat([0])),maxV=Math.max.apply(null,vals.concat([0]));
    var pad=(maxV-minV)*0.08||1,yMin=minV-pad,yMax=maxV+pad;
    var yt=[],t;for(t=0;t<=4;t++){var v=yMin+(yMax-yMin)*t/4;yt.push({v:v,label:'$'+Math.round(v)});}
    var step=Math.max(1,Math.ceil(n/6)),xt=[],i;
    for(i=0;i<n;i+=step)xt.push({v:i,label:(days[i].date||'').slice(5)});
    if(xt.length&&xt[xt.length-1].v!==n-1)xt.push({v:n-1,label:(days[n-1].date||'').slice(5)});
    var cls=vals[vals.length-1]<0?'neg':'rev';
    el('viz-margin-cumulative').innerHTML=cartesian({h:270,x:[0,n-1],y:[yMin,yMax],yticks:yt,xticks:xt,
      series:[{key:cls,pts:pts}]});
  }

  // ---- COHORT heatmap (3 segments) ----
  function heatSeg(seg,segCls){
    if(!seg||!seg.cohorts||!seg.cohorts.length) return '<div class="heatseg">'+nd()+'</div>';
    var cohorts=seg.cohorts, maxW=0, vmax=0;
    cohorts.forEach(function(c){(c.weeks||[]).forEach(function(wk){if(wk.week_n>maxW)maxW=wk.week_n;if(wk.avg_per_user>vmax)vmax=wk.avg_per_user;});});
    var head='<tr><th></th>';for(var k=0;k<=maxW;k++)head+='<th>Wk'+k+'</th>';head+='</tr>';
    var rows='';
    cohorts.forEach(function(c){
      var byW={};(c.weeks||[]).forEach(function(wk){byW[wk.week_n]=wk;});
      rows+='<tr><td class="rh">'+(c.cohort_week||'').slice(5)+' <small>·'+ (c.cohort_size||0) +'</small></td>';
      for(var k=0;k<=maxW;k++){
        var wk=byW[k];
        if(!wk){rows+='<td class="c empty"></td>';continue;}
        var frac=vmax?wk.avg_per_user/vmax:0, p=Math.round(8+frac*82);
        var hi=p>=52?' hi':'';
        rows+='<td class="c'+hi+'" style="background:color-mix(in srgb, var('+segCls+') '+p+'%, transparent)">'+USD(wk.avg_per_user)+'</td>';
      }
      rows+='</tr>';
    });
    return '<div class="heatseg"><p class="cap">'+(seg.scope||'')+' <small>· since '+(seg.anchor||'').replace(/\s*\(.*\)/,'')+'</small></p>'+
           '<div class="heatwrap"><table class="heat"><thead>'+head+'</thead><tbody>'+rows+'</tbody></table></div></div>';
  }
  function renderCohort(){
    var d=DATA.cohort; if(!d){el('viz-cohort').innerHTML=nd();return;}
    el('viz-cohort').innerHTML=heatSeg(d.chart_b,'--curveB')+heatSeg(d.chart_a,'--curveA')+heatSeg(d.chart_c,'--curveC');
  }

  // ---- CURVE ----
  function renderCurve(){
    var d=DATA.curve; if(!d){el('viz-curve').innerHTML=nd();return;}
    // Log10 y-axis: premium (wk0 ~$8-9) is 10x+ the free line (~$0.7), so a shared
    // LINEAR axis squashes all-users/free flat against zero. Floor tiny/zero values
    // at $0.01 so every segment stays legible on one chart.
    var FLOOR=0.01, lg=function(v){return Math.log10(Math.max(v||0,FLOOR));};
    function pts(ck){return ((d[ck]||{}).curve||[]).map(function(p){return [p.week_n,lg(p.avg_per_user)];});}
    var A=pts('chart_a'),B=pts('chart_b'),C=pts('chart_c');
    var all=A.concat(B,C); if(!all.length){el('viz-curve').innerHTML=nd();return;}
    var maxW=Math.max.apply(null,all.map(function(p){return p[0];}));
    var rawMax=Math.max.apply(null,[].concat((d.chart_a||{}).curve||[],(d.chart_b||{}).curve||[],
      (d.chart_c||{}).curve||[]).map(function(p){return p.avg_per_user||0;}).concat([1]));
    var loMin=-2, loMax=Math.max(1,Math.ceil(Math.log10(rawMax)));
    var yt=[],t;for(t=loMin;t<=loMax;t++){var vv=Math.pow(10,t);yt.push({v:t,label:'$'+(vv<1?vv.toFixed(2):vv.toFixed(0))});}
    var xt=[],i;for(i=0;i<=maxW;i++)xt.push({v:i,label:'Wk '+i});
    var series=[];
    if(B.length)series.push({key:'B',pts:B,dots:true});
    if(A.length)series.push({key:'A',pts:A,dots:true});
    if(C.length)series.push({key:'C',pts:C,dots:true});
    el('viz-curve').innerHTML=cartesian({h:300,x:[0,maxW],y:[loMin,loMax],yticks:yt,xticks:xt,series:series,
      extra:'<text class="tick" x="6" y="11">log $/user</text>'});
  }

  // ---- RETENTION (share still active by week) ----
  function renderRetention(){
    var d=DATA.retention; if(!d){el('viz-retention').innerHTML=nd();return;}
    function pts(ck){return ((d[ck]||{}).curve||[]).map(function(p){return [p.week_n,p.retention];});}
    var A=pts('chart_a'),B=pts('chart_b'),C=pts('chart_c');
    var all=A.concat(B,C); if(!all.length){el('viz-retention').innerHTML=nd();return;}
    var maxW=Math.max.apply(null,all.map(function(p){return p[0];}));
    var yt=[0,.25,.5,.75,1].map(function(v){return {v:v,label:PCT(v)};});
    var xt=[],i;for(i=0;i<=maxW;i++)xt.push({v:i,label:'Wk '+i});
    var series=[];
    if(B.length)series.push({key:'B',pts:B,dots:true});
    if(A.length)series.push({key:'A',pts:A,dots:true});
    if(C.length)series.push({key:'C',pts:C,dots:true});
    el('viz-retention').innerHTML=cartesian({h:300,x:[0,maxW],y:[0,1],yticks:yt,xticks:xt,series:series,
      extra:'<text class="tick" x="6" y="11">% active</text>'});
  }

  // ---- RETENTION heatmap (per-cohort triangle) ----
  function heatSegRet(seg,segCls){
    if(!seg||!seg.triangle||!seg.triangle.length) return '<div class="heatseg">'+nd()+'</div>';
    var cohorts=seg.triangle, maxW=0;
    cohorts.forEach(function(c){(c.weeks||[]).forEach(function(wk){if(wk.week_n>maxW)maxW=wk.week_n;});});
    var head='<tr><th></th>';for(var k=0;k<=maxW;k++)head+='<th>Wk'+k+'</th>';head+='</tr>';
    var rows='';
    cohorts.forEach(function(c){
      var byW={};(c.weeks||[]).forEach(function(wk){byW[wk.week_n]=wk;});
      rows+='<tr><td class="rh">'+(c.cohort_week||'').slice(5)+' <small>·'+(c.cohort_size||0)+'</small></td>';
      for(var k=0;k<=maxW;k++){
        var wk=byW[k];
        if(!wk){rows+='<td class="c empty"></td>';continue;}
        var frac=wk.retention||0, p=Math.round(8+frac*82), hi=p>=52?' hi':'';
        rows+='<td class="c'+hi+'" style="background:color-mix(in srgb, var('+segCls+') '+p+'%, transparent)">'+PCT(frac)+'</td>';
      }
      rows+='</tr>';
    });
    return '<div class="heatseg"><p class="cap">'+(seg.scope||'')+' <small>· since '+(seg.anchor||'').replace(/\s*\(.*\)/,'')+'</small></p>'+
           '<div class="heatwrap"><table class="heat"><thead>'+head+'</thead><tbody>'+rows+'</tbody></table></div></div>';
  }
  function renderRetentionHeat(){
    var d=DATA.retention; if(!d){el('viz-retention-heat').innerHTML=nd();return;}
    el('viz-retention-heat').innerHTML=heatSegRet(d.chart_b,'--curveB')+heatSegRet(d.chart_a,'--curveA')+heatSegRet(d.chart_c,'--curveC');
  }

  // ---- CUMULATIVE cost-to-date (running total of the decay curve) ----
  function renderCumulative(){
    var d=DATA.cumulative; if(!d){el('viz-cumulative').innerHTML=nd();return;}
    function pts(ck){return ((d[ck]||{}).curve||[]).map(function(p){return [p.week_n,p.cum_per_user];});}
    var A=pts('chart_a'),B=pts('chart_b'),C=pts('chart_c');
    var all=A.concat(B,C); if(!all.length){el('viz-cumulative').innerHTML=nd();return;}
    var maxW=Math.max.apply(null,all.map(function(p){return p[0];}));
    var maxY=niceMax(Math.max.apply(null,all.map(function(p){return p[1];})));
    var yt=[],t;for(t=0;t<=4;t++){var v=maxY*t/4;yt.push({v:v,label:'$'+(v>=10?v.toFixed(0):v.toFixed(1))});}
    var xt=[],i;for(i=0;i<=maxW;i++)xt.push({v:i,label:'Wk '+i});
    var series=[];
    if(B.length)series.push({key:'B',pts:B,dots:true});
    if(A.length)series.push({key:'A',pts:A,dots:true});
    if(C.length)series.push({key:'C',pts:C,dots:true});
    el('viz-cumulative').innerHTML=cartesian({h:300,x:[0,maxW],y:[0,maxY],yticks:yt,xticks:xt,series:series,
      extra:'<text class="tick" x="6" y="11">cum $/user</text>'});
  }

  // ---- LIFETIME projection (bars per segment at the first horizon) ----
  function renderLifetime(){
    var d=DATA.lifetime; if(!d){el('viz-lifetime').innerHTML=nd();return;}
    var hz=(d.horizons&&d.horizons[0])||52;
    function proj(ck){var l=(d[ck]||{}).projections||[];for(var i=0;i<l.length;i++)if(l[i].horizon_weeks===hz)return l[i].projected_lifetime_per_user;return null;}
    var rows=[];[['chart_b','Premium'],['chart_a','All users'],['chart_c','Free']].forEach(function(s){
      var v=proj(s[0]); if(v!=null)rows.push({seg:s[1]+' ('+hz+'w)',v:v});});
    if(!rows.length){el('viz-lifetime').innerHTML=nd();return;}
    hbars('viz-lifetime',rows,'seg','v','f-acc',null);
  }

  // ---- CONCENTRATION (Lorenz) ----
  function renderConc(){
    var d=DATA.concentration; if(!d||!d.lorenz){el('viz-concentration').innerHTML=nd();return;}
    // x = cumulative user share (top_user_pct) to match eng:chart-aurie-cost-concentration;
    // fall back to pctile/100 only if the field is absent.
    var pts=[[0,0]];d.lorenz.forEach(function(r){pts.push([r.top_user_pct!=null?r.top_user_pct:(r.pctile||0)/100,r.cost_share]);});
    var yt=[0,.25,.5,.75,1].map(function(v){return {v:v,label:PCT(v)};});
    var xt=[0,.25,.5,.75,1].map(function(v){return {v:v,label:PCT(v)};});
    var t10=null;(d.top_shares||[]).forEach(function(s){if(s.pct===10)t10=s;});
    var base=cartesian({h:290,x:[0,1],y:[0,1],yticks:yt,xticks:xt,series:[{key:'acc',pts:pts,area:true,acls:'f-acc'}],
      marks:t10?[{x:.1,y:t10.share,t:'top 10% → '+PCT(t10.share),anchor:'start'}]:[]});
    base=base.replace('<line class="axis"','<line class="diag" x1="46" y1="262" x2="746" y2="14" /><line class="axis"');
    el('viz-concentration').innerHTML=base;
  }

  // ---- TENURE ----
  function renderTenure(){
    var d=DATA.tenure; if(!d||!d.days||!d.days.length){el('viz-tenure').innerHTML=nd();return;}
    var days=d.days,n=days.length;
    var pn=days.map(function(x,i){return [i,x.new_cost||0];}),pe=days.map(function(x,i){return [i,x.established_cost||0];});
    var maxY=niceMax(Math.max.apply(null,days.map(function(x){return Math.max(x.new_cost||0,x.established_cost||0);})));
    var yt=[];for(var i=0;i<=3;i++)yt.push({v:maxY*i/3,label:'$'+Math.round(maxY*i/3)});
    var step=Math.ceil(n/5),xt=[];for(i=0;i<n;i+=step)xt.push({v:i,label:(days[i].date||'').slice(5)});
    el('viz-tenure').innerHTML=cartesian({h:280,x:[0,n-1],y:[0,maxY],yticks:yt,xticks:xt,
      series:[{key:'est',pts:pe,acls:'f-muted',area:true},{key:'cost',pts:pn}]});
  }

  // ---- PAYBACK (flagship cohort) + margin bars ----
  function renderPayback(){
    var d=DATA.payback; if(!d||!d.cohorts||!d.cohorts.length){el('viz-payback').innerHTML=nd();el('viz-margin').innerHTML=nd();return;}
    var flag=d.cohorts.reduce(function(a,b){return (b.weeks||[]).length>(a.weeks||[]).length?b:a;});
    var wks=flag.weeks||[];
    if(wks.length){
      var pc=wks.map(function(x){return [x.week_n,x.cum_cost_per_user];}),pr=wks.map(function(x){return [x.week_n,x.cum_rev_per_user];});
      var maxW=Math.max.apply(null,wks.map(function(x){return x.week_n;}));
      var maxY=niceMax(Math.max.apply(null,wks.map(function(x){return Math.max(x.cum_cost_per_user,x.cum_rev_per_user);})));
      var yt=[];for(var i=0;i<=4;i++)yt.push({v:maxY*i/4,label:'$'+Math.round(maxY*i/4)});
      var xt=[];for(i=0;i<=maxW;i++)xt.push({v:i,label:'Wk '+i});
      el('viz-payback').innerHTML=cartesian({w:520,h:270,x:[0,maxW],y:[0,maxY],yticks:yt,xticks:xt,
        series:[{key:'cost',pts:pc,dots:true},{key:'rev',pts:pr,dots:true}]});
      el('foot-payback').textContent='Flagship cohort — '+(flag.cohort_week||'')+' (n='+(flag.cohort_size||0)+').';
    }
    // margin bars
    var data=d.cohorts.map(function(c){return {c:(c.cohort_week||'').slice(5),m:c.margin_latest};}).filter(function(x){return x.m!=null;});
    if(!data.length){el('viz-margin').innerHTML=nd();return;}
    var w=520,h=270,pL=54,pR=48,pTt=14,pB=14,ph=h-pTt-pB,pw=w-pL-pR;
    var min=Math.min.apply(null,data.map(function(d0){return d0.m;}).concat([0]));
    var max=Math.max.apply(null,data.map(function(d0){return d0.m;}).concat([0.3]));
    min=Math.min(min,-0.05);max=Math.max(max,0.05);
    var zx=pL+(0-min)/(max-min)*pw,bh=Math.min(30,(ph-(data.length+1)*8)/data.length),gap=(ph-data.length*bh)/(data.length+1);
    var g='<line class="axis" x1="'+zx+'" y1="'+pTt+'" x2="'+zx+'" y2="'+(h-pB)+'"/>';
    data.forEach(function(d0,i){
      var y=pTt+gap+(bh+gap)*i,vx=pL+(d0.m-min)/(max-min)*pw,x0=Math.min(zx,vx),ww=Math.abs(vx-zx);
      g+='<rect class="'+(d0.m>=0?'f-rev':'f-neg')+'" x="'+x0+'" y="'+y+'" width="'+ww+'" height="'+bh+'" rx="3"/>';
      g+='<text class="barlbl" x="'+(pL-8)+'" y="'+(y+bh/2)+'" text-anchor="end" dominant-baseline="middle">'+d0.c+'</text>';
      g+='<text class="vlabel" x="'+(d0.m>=0?vx+6:vx-6)+'" y="'+(y+bh/2)+'" text-anchor="'+(d0.m>=0?'start':'end')+'" dominant-baseline="middle">'+Math.round(d0.m*100)+'%</text>';
    });
    el('viz-margin').innerHTML=svg(g,w,h);
  }

  // ---- MIX (100% stacked per segment) ----
  function renderMix(){
    var d=DATA.mix; if(!d||!d.by_segment||!d.by_segment.length){el('viz-mix').innerHTML=nd();return;}
    var seg=d.by_segment.filter(function(s){return (s.total_cost||0)>0;});
    var w=760,h=Math.max(150,40*seg.length+40),pL=58,pR=96,pT=10,pB=10,pw=w-pL-pR,ph=h-pT-pB;
    var bh=Math.min(30,(ph-(seg.length+1)*10)/seg.length),gap=(ph-seg.length*bh)/(seg.length+1),g='';
    seg.forEach(function(s,i){
      var tot=(s.llm_cost||0)+(s.voice_cost||0)||1,y=pT+gap+(bh+gap)*i,lw=(s.llm_cost||0)/tot*pw;
      g+='<rect class="f-llm" x="'+pL+'" y="'+y+'" width="'+lw+'" height="'+bh+'" rx="3"/>';
      g+='<rect class="f-voice" x="'+(pL+lw)+'" y="'+y+'" width="'+(pw-lw)+'" height="'+bh+'" rx="3"/>';
      g+='<text class="barlbl" x="'+(pL-8)+'" y="'+(y+bh/2)+'" text-anchor="end" dominant-baseline="middle" style="text-transform:capitalize">'+(s.subscription_state||'')+'</text>';
      g+='<text class="vlabel" x="'+(pL+8)+'" y="'+(y+bh/2)+'" dominant-baseline="middle" fill="#fff">'+Math.round((s.llm_cost||0)/tot*100)+'%</text>';
      g+='<text class="vlabel" x="'+(pL+pw-8)+'" y="'+(y+bh/2)+'" text-anchor="end" dominant-baseline="middle" fill="#fff">'+Math.round((s.voice_cost||0)/tot*100)+'%</text>';
      g+='<text class="tick" x="'+(pL+pw+8)+'" y="'+(y+bh/2)+'" dominant-baseline="middle">'+USD(tot,0)+' · '+(s.users||0).toLocaleString()+'u</text>';
    });
    el('viz-mix').innerHTML=svg(g,w,h);
  }

  // ---- FREE (avg + distribution) ----
  function renderFree(){
    var d=DATA.free; if(!d){el('viz-free-avg').innerHTML=nd();el('viz-free-dist').innerHTML=nd();return;}
    var cv=d.converters||{},nb=d.never_bought||{};
    var bars=[{n:'Converters',v:cv.avg_free_cost||0,c:'f-rev'},{n:'Never bought',v:nb.avg_free_cost||0,c:'f-free'}];
    var w=520,h=250,pL=14,pB=28,pTt=14,pw=w-pL-14,ph=h-pTt-pB,max=niceMax(Math.max(bars[0].v,bars[1].v)),bw=120,gap=(pw-bars.length*bw)/(bars.length+1),g='';
    for(var t=0;t<=3;t++){var vv=max*t/3,yy=pTt+ph-vv/max*ph;g+='<line class="grid" x1="'+pL+'" y1="'+yy+'" x2="'+(w-14)+'" y2="'+yy+'"/>';g+='<text class="tick ty" x="'+(pL+34)+'" y="'+yy+'">$'+vv.toFixed(2)+'</text>';}
    bars.forEach(function(b,i){var x=pL+gap+(bw+gap)*i,bh=b.v/max*ph,y=pTt+ph-bh;g+='<rect class="'+b.c+'" x="'+x+'" y="'+y+'" width="'+bw+'" height="'+bh+'" rx="4"/>';g+='<text class="vlabel" x="'+(x+bw/2)+'" y="'+(y-7)+'" text-anchor="middle">'+USD(b.v)+'</text>';g+='<text class="barlbl" x="'+(x+bw/2)+'" y="'+(h-9)+'" text-anchor="middle">'+b.n+'</text>';});
    el('viz-free-avg').innerHTML=svg(g,w,h);
    // distribution grouped by bucket
    var dist=d.distribution||[];
    var buckets=[];dist.forEach(function(r){if(buckets.indexOf(r.bucket)<0)buckets.push(r.bucket);});
    if(!buckets.length){el('viz-free-dist').innerHTML=nd();return;}
    function cnt(grp,bk){var r=dist.filter(function(x){return x.group===grp&&x.bucket===bk;})[0];return r?r.users:0;}
    var maxU=0;dist.forEach(function(r){if(r.users>maxU)maxU=r.users;});maxU=niceMax(maxU);
    var W=520,H=250,PL=40,PR=12,PB=30,PT=12,PW=W-PL-PR,PH=H-PT-PB,gw=PW/buckets.length,gbw=gw*0.34,gg='';
    for(var k=0;k<=3;k++){var uv=maxU*k/3,yy2=PT+PH-uv/maxU*PH;gg+='<line class="grid" x1="'+PL+'" y1="'+yy2+'" x2="'+(W-PR)+'" y2="'+yy2+'"/>';gg+='<text class="tick ty" x="'+(PL-6)+'" y="'+yy2+'">'+(uv>=1000?(uv/1000)+'k':uv)+'</text>';}
    buckets.forEach(function(bk,i){var cx=PL+gw*i+gw/2,c1=cnt('converters',bk),c2=cnt('never_bought',bk),h1=c1/maxU*PH,h2=c2/maxU*PH;
      gg+='<rect class="f-rev" x="'+(cx-gbw-2)+'" y="'+(PT+PH-h1)+'" width="'+gbw+'" height="'+h1+'" rx="2.5"/>';
      gg+='<rect class="f-free" x="'+(cx+2)+'" y="'+(PT+PH-h2)+'" width="'+gbw+'" height="'+h2+'" rx="2.5"/>';
      gg+='<text class="tick tx" x="'+cx+'" y="'+(H-10)+'">'+bk+'</text>';});
    el('viz-free-dist').innerHTML=svg(gg,W,H);
  }

  // ---- CHANNEL + campaigns ----
  function hbars(container,rows,labelKey,valKey,cls,subKey){
    if(!rows||!rows.length){el(container).innerHTML=nd();return;}
    var w=520,h=Math.max(150,52*rows.length+20),pL=100,pR=64,pTt=14,pB=14,pw=w-pL-pR,ph=h-pTt-pB;
    var max=niceMax(Math.max.apply(null,rows.map(function(r){return r[valKey]||0;}))),bh=Math.min(40,(ph-(rows.length+1)*10)/rows.length),gap=(ph-rows.length*bh)/(rows.length+1),g='';
    rows.forEach(function(r,i){var y=pTt+gap+(bh+gap)*i,bw=(r[valKey]||0)/max*pw;
      g+='<rect class="'+cls+'" x="'+pL+'" y="'+y+'" width="'+bw+'" height="'+bh+'" rx="3.5"/>';
      g+='<text class="barlbl" x="'+(pL-8)+'" y="'+(y+bh/2)+'" text-anchor="end" dominant-baseline="middle">'+r[labelKey]+'</text>';
      g+='<text class="vlabel" x="'+(pL+bw+6)+'" y="'+(y+bh/2)+'" dominant-baseline="middle">'+USD(r[valKey])+'</text>';
      if(subKey&&r[subKey]!=null)g+='<text class="tick" x="'+(pL+bw+6)+'" y="'+(y+bh/2+14)+'" dominant-baseline="middle">'+r[subKey].toLocaleString()+' users</text>';
    });
    el(container).innerHTML=svg(g,w,h);
  }
  function renderChannel(){
    var d=DATA.channel; if(!d){el('viz-channel').innerHTML=nd();el('viz-campaign').innerHTML=nd();return;}
    var chan=(d.by_channel||[]).slice().sort(function(a,b){return (b.avg_cost_per_user||0)-(a.avg_cost_per_user||0);})
      .map(function(r){return {channel:(r.channel||'').charAt(0).toUpperCase()+(r.channel||'').slice(1),avg:r.avg_cost_per_user,users:r.users};});
    hbars('viz-channel',chan,'channel','avg','f-acc','users');
    var camp=(d.by_campaign||[]).filter(function(r){return (r.users||0)>=20;}).slice()
      .sort(function(a,b){return (b.avg_cost_per_user||0)-(a.avg_cost_per_user||0);}).slice(0,5)
      .map(function(r){return {name:(r.campaign||'').replace(/^Aurie_/,'').replace(/_/g,' '),avg:r.avg_cost_per_user};});
    hbars('viz-campaign',camp,'name','avg','f-trial',null);
  }

  // ---- REVENUE over time: bookings bars + recognized line + active-subs line (dual axis) ----
  function renderRevenue(){
    var d=DATA.revenue; if(!d){el('viz-revenue').innerHTML=nd();return;}
    var bk=d.bookings||[],rc=d.recognized||[];
    var order=[],seen={};
    rc.concat(bk).forEach(function(r){if(!seen[r.bucket]){seen[r.bucket]=1;order.push(r.bucket);}});
    order.sort();
    if(!order.length){el('viz-revenue').innerHTML=nd();return;}
    var bMap={},rMap={};
    bk.forEach(function(r){bMap[r.bucket]=r;});rc.forEach(function(r){rMap[r.bucket]=r;});
    var monthly=(d.grain==='monthly');
    var lab=function(s){return monthly?(s||'').slice(0,7):(s||'').slice(5);};
    var n=order.length,w=760,h=310,pL=56,pR=52,pT=14,pB=30,pw=w-pL-pR,ph=h-pT-pB;
    var maxUSD=niceMax(Math.max.apply(null,order.map(function(b){return Math.max((bMap[b]||{}).gross||0,(rMap[b]||{}).gross||0);}).concat([1])));
    var maxSub=niceMax(Math.max.apply(null,rc.map(function(r){return r.active_subscribers||0;}).concat([1])));
    var gwid=pw/n,bw=Math.max(2,gwid*0.6),bx=function(i){return pL+gwid*(i+0.5);};
    var syD=function(v){return pT+ph-v/maxUSD*ph;},syS=function(v){return pT+ph-v/maxSub*ph;};
    var g='',t;
    for(t=0;t<=4;t++){var cv=maxUSD*t/4,yy=syD(cv);g+='<line class="grid" x1="'+pL+'" y1="'+yy+'" x2="'+(w-pR)+'" y2="'+yy+'"/>';g+='<text class="tick ty" x="'+(pL-7)+'" y="'+yy+'">$'+Math.round(cv)+'</text>';g+='<text class="tick tyr" x="'+(w-pR+7)+'" y="'+syS(maxSub*t/4)+'">'+Math.round(maxSub*t/4)+'</text>';}
    order.forEach(function(b,i){var gv=(bMap[b]||{}).gross||0,x0=bx(i)-bw/2;g+='<rect class="f-rev" x="'+x0+'" y="'+syD(gv)+'" width="'+bw+'" height="'+(gv/maxUSD*ph)+'" rx="1.5"/>';});
    var rpts=order.filter(function(b){return rMap[b];}).map(function(b,i){return [bx(order.indexOf(b)),syD(rMap[b].gross||0)];});
    if(rpts.length)g+='<path class="line s-acc" d="M '+rpts.map(function(p){return p[0].toFixed(1)+' '+p[1].toFixed(1);}).join(' L ')+'"/>';
    var spts=order.filter(function(b){return rMap[b];}).map(function(b){return [bx(order.indexOf(b)),syS(rMap[b].active_subscribers||0)];});
    if(spts.length)g+='<path class="line s-est" stroke-dasharray="5 3" d="M '+spts.map(function(p){return p[0].toFixed(1)+' '+p[1].toFixed(1);}).join(' L ')+'"/>';
    var step=Math.max(1,Math.ceil(n/8));
    order.forEach(function(b,i){if(i%step===0||i===n-1)g+='<text class="tick tx" x="'+bx(i)+'" y="'+(h-9)+'">'+lab(b)+'</text>';});
    g+='<line class="axis" x1="'+pL+'" y1="'+syD(0)+'" x2="'+(w-pR)+'" y2="'+syD(0)+'"/>';
    el('viz-revenue').innerHTML=svg(g,w,h);
  }

  // ---- REVENUE by tier: horizontal bars per tier, split annual vs monthly (booked) ----
  function renderRevenueTier(){
    var d=DATA.revenuetier; if(!d||!d.by_tier||!d.by_tier.length){el('viz-revenuetier').innerHTML=nd();return;}
    var rows=(d.rows||[]);
    var billOf=function(tier,bill){var r=rows.filter(function(x){return x.tier===tier&&x.billing===bill;})[0];return r?(r.bookings_gross||0):0;};
    var tiers=d.by_tier.slice().sort(function(a,b){return (b.bookings_gross||0)-(a.bookings_gross||0);})
      .filter(function(x){return (x.bookings_gross||0)>0;});
    if(!tiers.length){el('viz-revenuetier').innerHTML=nd();return;}
    var w=760,h=Math.max(150,46*tiers.length+30),pL=150,pR=88,pT=10,pB=10,pw=w-pL-pR,ph=h-pT-pB;
    var max=niceMax(Math.max.apply(null,tiers.map(function(x){return x.bookings_gross||0;})));
    var bh=Math.min(34,(ph-(tiers.length+1)*10)/tiers.length),gap=(ph-tiers.length*bh)/(tiers.length+1),g='';
    tiers.forEach(function(x,i){
      var y=pT+gap+(bh+gap)*i,ann=billOf(x.tier,'annual'),mon=billOf(x.tier,'monthly');
      var wa=ann/max*pw,wm=mon/max*pw;
      g+='<rect class="f-rev" x="'+pL+'" y="'+y+'" width="'+wa+'" height="'+bh+'" rx="3"/>';
      g+='<rect class="f-acc" x="'+(pL+wa)+'" y="'+y+'" width="'+wm+'" height="'+bh+'" rx="3"/>';
      g+='<text class="barlbl" x="'+(pL-8)+'" y="'+(y+bh/2)+'" text-anchor="end" dominant-baseline="middle">'+x.tier+'</text>';
      g+='<text class="vlabel" x="'+(pL+wa+wm+6)+'" y="'+(y+bh/2)+'" dominant-baseline="middle">'+USD(x.bookings_gross,0)+'</text>';
    });
    el('viz-revenuetier').innerHTML=svg(g,w,h);
  }

  var jobs=[renderDaily,renderMarginDaily,renderMarginCumulative,renderRevenue,renderRevenueTier,renderCohort,renderCurve,renderRetention,renderRetentionHeat,renderCumulative,renderLifetime,renderConc,renderTenure,renderPayback,renderMix,renderFree,renderChannel];
  jobs.forEach(function(fn){try{fn();}catch(e){}});
  ['daily','margin','revenue','revenuetier','cohort','curve','retention','cumulative','lifetime','concentration','tenure','payback','mix','free','channel'].forEach(setRead);
})();
</script>
"""


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Render the Aurie cost/economics HTML report from collected chart JSONs.")
    ap.add_argument("--data-dir", help="directory holding <key>.json for keys: " + " ".join(KEYS))
    for k in KEYS:
        ap.add_argument(f"--{k}", help=f"explicit path to the {k} JSON (overrides --data-dir)")
    ap.add_argument("--out", required=True, help="output HTML path")
    ap.add_argument("--title", default="Aurie Cost & Economics Report")
    ap.add_argument("--window", default="", help="window label for the header (else inferred from daily/cohort)")
    ap.add_argument("--generated", default="", help="generation date label (YYYY-MM-DD); default today (UTC)")
    a = ap.parse_args()

    data = {}
    for k in KEYS:
        path = getattr(a, k) or (os.path.join(a.data_dir, f"{k}.json") if a.data_dir else None)
        j = _load(path)
        if j is not None:
            data[k] = j
    if not data:
        sys.exit("no input JSON found — pass --data-dir or per-key paths (nothing to render)")

    generated = a.generated or dt.datetime.now(dt.timezone.utc).date().isoformat()
    data["_generated"] = generated

    # window label: prefer explicit, else from daily summary, else cohort span
    window = a.window
    if not window:
        s = (data.get("daily") or {}).get("summary", {})
        if s.get("from") and s.get("to"):
            window = f"{s['from']} → {s['to']}"
        else:
            coh = data.get("cohort") or {}
            if coh.get("cost_data_start") and coh.get("cost_data_end"):
                window = f"{coh['cost_data_start']} → {coh['cost_data_end']}"

    report = build_report(data, window)

    # Trim _generated out of DATA (kept only for meta) to avoid leaking into charts
    data_for_js = {k: v for k, v in data.items() if k != "_generated"}

    html = (HTML_TEMPLATE
            .replace("__TITLE__", a.title)
            .replace("__DATA_JSON__", json.dumps(data_for_js, ensure_ascii=False))
            .replace("__REPORT_JSON__", json.dumps(report, ensure_ascii=False)))

    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(html)
    present = ", ".join(k for k in KEYS if k in data)
    missing = ", ".join(k for k in KEYS if k not in data) or "none"
    print(f"wrote {a.out}  ({len(html):,} bytes)")
    print(f"  sections present: {present}")
    print(f"  sections missing: {missing}")
    print(f"  window: {window or '(unknown)'}  generated: {generated}")


if __name__ == "__main__":
    main()
