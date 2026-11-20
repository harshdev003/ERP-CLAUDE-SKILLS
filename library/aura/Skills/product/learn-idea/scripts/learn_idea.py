"""Learn leg: an experiment's verdict travels back to the idea that started it.

Without this the loop has no memory. An idea that lost still reads as
`experiment_running` forever, the ideation prior-knowledge block never learns it
was answered, and the same proposal comes back next month wearing different
words — which is exactly how a paywall testimonial got proposed again after
`aiTestimonials` had already lost 21 days of traffic proving it.

  plan     What the write-back would do for one row. No writes.
  sweep    Every `experiment_running` row whose experiment has ended. Dry by default.
  record   Write one row's verdict, optionally replying in its Slack thread.
  funnel   proposed -> shared -> approved -> built -> released -> decided, by month.

The verdict is *read* from `Learnings/Experiments`, never decided here. This skill
has no opinion about whether an experiment won; it only makes sure the answer
reaches the row that asked the question.
"""

from __future__ import annotations

import argparse
import collections
import datetime
import os
import re
import sys
from typing import Any, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from notion_rest import (  # noqa: E402
    MCPFallback,
    block_paragraph,
    make_date,
    make_rich_text,
    make_select,
    make_title,
    notion,
    rich_text_value,
    select_value,
    title_text,
)

AURIE_FEATURES_DS = "8d14f3eb-0c55-4f25-9322-ab2b2a69f092"
EXPERIMENTS_DS = "c0209685-17ac-49e2-849a-85fa8178decd"

# Experiments `Status` -> Ideas `Status`. Counts are the live database, so the
# vocabulary is what is actually in use rather than what a schema allows.
#
# `Turned off` (122 rows) is deliberately absent. It is not a verdict: it covers
# both "it lost, we killed it" and "it was broken, we killed it" — `aurieCustomWebPaywall`
# was turned off for a social-login checkout bug and re-run as v2, which is not a
# result about the idea at all. Mapping it to `lost` would write a false answer
# into the one place the loop consults before proposing again, and a false
# "already answered" is worse than no answer: it silently removes an untested
# idea from circulation.
VERDICT = {
    "Won": "won",
    "Lost": "lost",
    "Inconclusive": "inconclusive",
}

# Results carry their own marks, deliberately NOT :white_check_mark: / :x:.
#
# Those two are the loop's terminal reactions — a check mark means *done* (released
# and analysed, the thread closes) and a cross means *abandoned* (dropped without
# ever being answered). Posting a cross for a lost experiment is therefore the most
# misleading pairing available: a loss is an ANSWER, which is the opposite of
# abandoned, and reading one as the other would retire a question the loop actually
# settled.
RESULT_EMOJI = {
    "won": ":chart_with_upwards_trend:",
    "lost": ":chart_with_downwards_trend:",
    "inconclusive": ":shrug:",
}

# Not ended. Nothing to write back yet, and not an error.
PENDING_EXPERIMENT_STATUSES = {"Running", "In flight", None, ""}

# Written by `data:sync-experiments`' reconcile when the catalogue shows an
# experiment has stopped splitting traffic without an `endedAt` — the shape a
# rollout takes, and also a pre-release force, a kill by unreachable minVersion
# and a permanent feature flag. It is a STATE, not a verdict, so it is refused
# here for the same reason `Turned off` is: one signal, several meanings, and
# guessing writes a false "already answered" onto an idea nobody tested.
#
# The difference from a pending status is that this one will not resolve on its
# own. A `Running` row is worth waiting for; this one waits for a person, so
# `sweep` has to say so rather than listing it as not-ended-yet forever.
STOPPED_SPLITTING = "Stopped splitting"

# Set by a human on a kill switch kept live on purpose. It will NEVER produce a
# verdict — there is no experiment to end — so an idea pointing at one must be
# taken off the loop by hand (`--status`, or `record-ideas` abandon), not waited
# on. Silence here is the failure PROD-6116 names: a row that never changes and
# an idea that sits at `experiment_running` forever.
FEATURE_FLAG = "Feature flag"

# Only a row whose experiment is actually live can receive a verdict. A row still
# at `released` has no experiment running to have produced one.
LEARNABLE_STATUS = "experiment_running"

# How far down the loop each status means a row travelled. The funnel is
# cumulative — a live row also counts as proposed, shared, approved and built —
# because a stage-by-current-status chart shows one row per stage and answers
# nothing.
#
# Three placements carry an argument.
#
# `rejected` and `deferred` sit at `shared`: proposed, shown to the team, then
# not approved. That is what makes the shared -> approved drop the real decision
# rate rather than an artefact.
#
# `shipped_dormant` and `abandoned` sit at `built`, NOT at `live`. Both were
# built; neither was ever put in front of a user. Counting them as live would
# report a release rate that includes ideas nobody saw, which is the one number
# on this chart somebody would act on.
#
# `implementing` covers build, review, TestFlight and every gate round, so it is
# `built` too — a row on its third round has not travelled further than one on
# its first, it has just cost more.
STAGE_REACHED = {
    "pending": 0,
    "shared": 1, "rejected": 1, "deferred": 1,
    "approved": 2,
    "implementing": 3, "shipped_dormant": 3, "abandoned": 3,
    "experiment_running": 4,
    "won": 5, "lost": 5, "inconclusive": 5,
}
STAGES = ["proposed", "shared", "approved", "built", "live", "decided"]


# ---------- pure decisions --------------------------------------------------

def verdict_for(experiment_status: Optional[str]) -> tuple[Optional[str], str]:
    """(ideas_status, why). `None` means do not write.

    Three outcomes, not two, for the same reason `ship-idea`'s existence check
    has three: "no verdict yet" and "a verdict this skill refuses to interpret"
    are different situations, and collapsing them writes a guess into the log.
    """
    if experiment_status in PENDING_EXPERIMENT_STATUSES:
        return None, f"experiment status is '{experiment_status or '(blank)'}' — not ended yet."
    if experiment_status in VERDICT:
        return VERDICT[experiment_status], f"experiment {experiment_status.lower()}."
    if experiment_status == "Turned off":
        return None, ("experiment status is 'Turned off', which is not a verdict — it covers "
                      "both 'it lost' and 'it was broken'. Pass --status explicitly once you "
                      "know which; guessing would write a false 'already answered' onto an "
                      "idea that may never have been tested.")
    if experiment_status == STOPPED_SPLITTING:
        return None, ("experiment status is 'Stopped splitting' — it is no longer splitting "
                      "traffic, but the catalogue does not say whether it rolled out, was "
                      "forced to one arm before release, or was killed. WILL NOT RESOLVE ON "
                      "ITS OWN: ask whoever owns it, then pass --status (or mark the "
                      "experiment 'Feature flag' in Learnings/Experiments if it is a kill "
                      "switch rather than a test).")
    if experiment_status == FEATURE_FLAG:
        return None, ("experiment status is 'Feature flag' — a kill switch kept live on "
                      "purpose, not a test. It will NEVER produce a verdict, so this idea "
                      "will wait forever: decide it by hand with --status, or abandon it.")
    return None, f"unrecognised experiment status '{experiment_status}' — not writing a guess."


def default_learning(status: str) -> str:
    """The note written when the caller states no learning.

    Thin, but it must still be *true* and traceable. `Quick status note` is the
    field the prior-knowledge block surfaces as the answer to the question the
    idea asked, so a placeholder there is not cosmetic — it is a fabricated
    finding that reads exactly like a real one.
    """
    return f"Experiment {status} — see the Learnings/Experiments row for the analysis."


def funnel_counts(rows: list[dict[str, Any]]) -> "collections.OrderedDict[str, int]":
    """Cumulative counts per stage: how many rows reached at least this far."""
    out: "collections.OrderedDict[str, int]" = collections.OrderedDict()
    for i, stage in enumerate(STAGES):
        out[stage] = sum(1 for r in rows if STAGE_REACHED.get(r["status"], -1) >= i)
    return out


def unmapped_statuses(rows: list[dict[str, Any]]) -> "collections.Counter[str]":
    """Statuses no stage claims. These vanish from the funnel without a trace.

    A row on an unmapped status is counted at no stage, so the chart renders
    perfectly and is quietly short. That is not hypothetical: when `released`
    was folded into `experiment_running`, every row still on it would have
    dropped out silently — the funnel would simply have reported fewer ideas
    reaching release, which reads as a finding rather than a bug.
    """
    return collections.Counter(
        r["status"] for r in rows if r["status"] not in STAGE_REACHED)


def month_of(row: dict[str, Any]) -> str:
    """The cohort month: when the idea was first proposed, not when it ended.

    A funnel keyed on the end date makes every stage a different population and
    the conversion rates meaningless.
    """
    return (row.get("first_seen") or "")[:7] or "unknown"


# ---------- reads -----------------------------------------------------------

# An idea is addressed by its immutable, human-friendly `Idea ID` (`42`, `#42`,
# `IDEA-42`). Bounded to ≤6 digits so it can never collide with a 12-hex
# fingerprint, which still resolves for references predating the id.
_IDEA_REF_RE = re.compile(r"^(?:idea[-\s]?|#)?(\d{1,6})$", re.IGNORECASE)


def _ref_idea_num(ref: str) -> Optional[int]:
    """The integer of an Idea-# reference (`42`, `#42`, `IDEA-42`), or None."""
    m = _IDEA_REF_RE.match((ref or "").strip())
    return int(m.group(1)) if m else None


def _row_idea(row: dict[str, Any]) -> tuple[Optional[int], str]:
    """(number, 'IDEA-42') from a row's auto-`Idea ID` property; (None, '') if unset."""
    uid = (row.get("properties", {}).get("Idea ID") or {}).get("unique_id") or {}
    num = uid.get("number")
    if num is None:
        return None, ""
    prefix = uid.get("prefix")
    return num, (f"{prefix}-{num}" if prefix else str(num))


def load_ideas(ds_id: str) -> list[dict[str, Any]]:
    out = []
    for row in notion.query_data_source(ds_id, page_size=100):
        props = row.get("properties", {})
        idea_num, idea_id = _row_idea(row)
        out.append({
            "page_id": row["id"],
            "idea_num": idea_num,
            "idea_id": idea_id,
            "title": title_text(row, "Title"),
            "fingerprint": rich_text_value(row, "Fingerprint"),
            "status": select_value(row, "Status"),
            "experiment": rich_text_value(row, "Experiment name"),
            "note": rich_text_value(row, "Quick status note"),
            "slack": (props.get("Slack thread") or {}).get("url") or "",
            "cleanup_issue": (props.get("Cleanup issue") or {}).get("url") or "",
            "first_seen": ((props.get("First seen") or {}).get("date") or {}).get("start") or "",
            "screen": rich_text_value(row, "Screen"),
            "domain": select_value(row, "Domain"),
            "variants": rich_text_value(row, "Variants"),
            "learning": rich_text_value(row, "Learning"),
            "url": row.get("url") or "",
        })
    return out


def load_experiment_status() -> dict[str, str]:
    """name (lowercased) -> Status, for every Learnings/Experiments row."""
    out: dict[str, str] = {}
    for row in notion.query_data_source(EXPERIMENTS_DS, page_size=100):
        name = title_text(row, "Title").strip().lower()
        if name:
            out[name] = select_value(row, "Status")
    return out


def resolve(rows: list[dict[str, Any]], ref: str) -> dict[str, Any]:
    num = _ref_idea_num(ref)
    if num is not None:
        hit = [r for r in rows if r["idea_num"] == num]
        if len(hit) == 1:
            return hit[0]
        raise SystemExit(f"no idea has id {num} (IDEA-{num})")
    exact = [r for r in rows if r["fingerprint"] == ref.lower()]
    if len(exact) == 1:
        return exact[0]
    matches = [r for r in rows if ref.lower() in (r["title"] or "").lower()]
    if not matches:
        raise SystemExit(f"no idea matches '{ref}'")
    if len(matches) > 1:
        lines = "\n".join(f"  {r['idea_id'] or r['fingerprint']}  [{r['status']}] {r['title'][:70]}"
                          for r in matches)
        raise SystemExit(f"'{ref}' matched {len(matches)} rows — use an Idea ID:\n{lines}")
    return matches[0]


# ---------- why it ended ----------------------------------------------------
#
# The verdict comes from the people who ended the test, and they say so in Slack:
# the experiment monitor posts "Experiment Won|Lost|Inconclusive: <key>" in
# #abtest-monitoring, and #data-experiment logs "Experiment ended: `<key>` — why".
# The Learnings/Experiments row is one input; when an end post names a different
# verdict, the end post wins and the disagreement is reported, never written over.

MONITOR_CHANNEL = "C05TX32DJVA"      # #abtest-monitoring
DATA_EXPERIMENT_CHANNEL = "G017L0Q12FL"  # #data-experiment
YAWAR = "U1BCYETM2"
ASKED_WHY = "Asked why it ended"

MONITOR_RE = re.compile(r"Experiment\s+(Won|Lost|Inconclusive)\s*:\s*`?([A-Za-z0-9_]+)`?", re.I)
ENDED_RE = re.compile(r"Experiment ended:\s*`([A-Za-z0-9_]+)`\s*(.*)", re.I | re.S)


def parse_monitor_post(text: str) -> Optional[dict[str, Any]]:
    """{"verdict", "key", "evidence"} from an experiment-monitor end post, or None."""
    m = MONITOR_RE.search(text or "")
    if not m:
        return None
    lines = [ln.strip(" •\t") for ln in (text or "").splitlines()[1:] if ln.strip()]
    winner = next((ln for ln in lines if ln.lower().startswith("winner")), "")
    metric = next((ln for ln in lines if "%" in ln and "confidence" in ln), "")
    closing = lines[-1] if lines and lines[-1] not in (winner, metric) else ""
    evidence = " ".join(x.rstrip(".") + "." for x in (winner, metric, closing) if x)
    return {"verdict": m.group(1).lower(), "key": m.group(2), "evidence": evidence}


def parse_ended_post(text: str) -> Optional[dict[str, Any]]:
    """{"key", "reason"} from a #data-experiment "Experiment ended" post, or None."""
    m = ENDED_RE.search(text or "")
    if not m:
        return None
    reason = re.sub(r"^\[[^\]]*\]\s*[-–—:]?\s*", "", " ".join(m.group(2).split())).strip()
    return {"key": m.group(1), "reason": reason}


def find_end_posts(key: str, search: Any = None) -> Optional[dict[str, Any]]:
    """The monitor and #data-experiment end posts for `key`. None when Slack can't be searched.

    Returns {"monitor": {...} | None, "ended": {...} | None}; each carries its permalink
    and timestamp. Matches the key exactly, so a sibling experiment never answers for it.
    """
    if search is None:
        try:
            from slack_post import search_messages  # noqa: PLC0415
        except Exception:  # noqa: BLE001
            return None
        search = search_messages
    try:
        resp = search(f'"{key}"', count=50)
    except (Exception, SystemExit):  # noqa: BLE001 — no user token, network
        return None
    if not (resp or {}).get("ok", False):  # Slack reports errors as ok:false on a 200
        return None
    matches = ((resp or {}).get("messages") or {}).get("matches") or []
    out: dict[str, Any] = {"monitor": None, "ended": None}
    for msg in sorted(matches, key=lambda x: float(x.get("ts") or 0), reverse=True):
        chan = ((msg.get("channel") or {}).get("id")) or ""
        text = msg.get("text") or ""
        if chan == MONITOR_CHANNEL and out["monitor"] is None:
            got = parse_monitor_post(text)
            if got and got["key"] == key:
                out["monitor"] = {**got, "permalink": msg.get("permalink", ""), "ts": msg.get("ts")}
        elif chan == DATA_EXPERIMENT_CHANNEL and out["ended"] is None:
            got = parse_ended_post(text)
            if got and got["key"] == key:
                out["ended"] = {**got, "permalink": msg.get("permalink", ""), "ts": msg.get("ts")}
    return out


def decide(row_verdict: Optional[str], posts: Optional[dict[str, Any]]) -> dict[str, Any]:
    """The verdict to write and why, from the Learnings row and the end posts.

    {"verdict": str|None, "learning": str, "disagreement": str, "ask": bool}.
    An explicit monitor post wins over the row. No post at all means a person is asked.
    """
    posts = posts or {}
    mon, ended = posts.get("monitor"), posts.get("ended")
    verdict = mon["verdict"] if mon else row_verdict
    disagreement = ""
    if mon and row_verdict and row_verdict != mon["verdict"]:
        disagreement = (f"Learnings row says {row_verdict}, the monitor's end post says "
                        f"{mon['verdict']} — recorded {mon['verdict']}; the row is the "
                        f"experiment owners' to correct.")
    if not mon and not ended:
        return {"verdict": None, "learning": "", "disagreement": "", "ask": True}
    if not verdict:
        return {"verdict": None, "learning": "", "disagreement": "", "ask": True}
    parts = [f"Experiment {verdict}."]
    if mon and mon["evidence"]:
        parts.append(mon["evidence"])
    if ended and ended["reason"]:
        parts.append(f"Ended: {ended['reason'].rstrip('.')}.")
    links = [x["permalink"] for x in (mon, ended) if x and x.get("permalink")]
    if links:
        parts.append("Source: " + " · ".join(links))
    return {"verdict": verdict, "learning": " ".join(parts), "disagreement": disagreement,
            "ask": False}


def ask_text(key: str) -> str:
    return (f"<@{YAWAR}> `{key}` has ended, but there is no end post for it in "
            f"#abtest-monitoring or #data-experiment. Why did it end, and what was the result? "
            f"The loop records the verdict from your answer.")


def ask_why(idea: dict[str, Any], reply: Any = None) -> bool:
    """Ask Yawar once in the idea's thread. True when asked now; never raises.

    The marker is written before the post, so a note write that fails after a successful
    post can never lead to a second ping. A post that fails puts the note back.
    """
    note = idea.get("note") or ""
    if ASKED_WHY in note:
        return False
    parsed = parse_permalink(idea.get("slack") or "")
    if not parsed:
        return False
    channel, ts = parsed
    try:
        notion.update_page_props(idea["page_id"], {"Quick status note": make_rich_text(
            f"{note.rstrip()}\n{ASKED_WHY} (Yawar, in the thread).".strip())})
    except (Exception, SystemExit):  # noqa: BLE001 — no marker, no ask
        return False
    try:
        if reply is None:
            from slack_post import post_message as reply  # noqa: PLC0415
        resp = reply(channel=channel, text=ask_text(idea["experiment"]), thread_ts=ts,
                     footer_skill="product:learn-idea")
        ok = bool((resp or {}).get("ok"))
    except (Exception, SystemExit):  # noqa: BLE001
        ok = False
    if not ok:
        try:
            notion.update_page_props(idea["page_id"], {"Quick status note": make_rich_text(note)})
        except (Exception, SystemExit):  # noqa: BLE001
            pass
    return ok


def cleanup_keys(rows: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """(fingerprint, experiment key) to clean up — only the loop's own rows, each by its own key.

    The tick files cleanup with `eng:sweep-ended-experiments key=<key>` for these and nothing
    else: an experiment outside the loop's ideas database is never touched.
    """
    return [(r["fingerprint"], r["experiment"].strip()) for r in rows
            if (r.get("experiment") or "").strip() and not r.get("cleanup_issue")]


# ---------- the learning -----------------------------------------------------

LOOP_LEARNINGS_DS = "23dbc38c-4f20-4a05-b346-b7bb9a34a8e8"  # Learnings/Loop/Loop Learnings
LOOP_NAME = {AURIE_FEATURES_DS: "Aurie Features"}

IMPLICATION = {
    "won": "This lever moved the metric on this surface: keep it as the default, and build "
           "the next idea on it rather than re-testing it.",
    "lost": "This lever hurt the metric on this surface: do not re-propose it, or a reworded "
            "version of it, here without new evidence that changes the case.",
    "inconclusive": "No evidence either way: a re-test needs a sharper arm, a better-aimed "
                    "metric or more traffic, not the same arm again.",
}


def compose_learning(idea: dict[str, Any], verdict: str, posts: Optional[dict[str, Any]],
                     implication: str = "") -> dict[str, str]:
    """What the loop learned from one ended experiment, in the shape ideation reads.

    `tested` comes from the idea row, `result` from the monitor's end post, `ended_by` from
    the #data-experiment end post. `implication` is the tick's judgement when supplied, else
    the verdict's default. Pure: no Notion, no Slack.
    """
    posts = posts or {}
    mon, ended = posts.get("monitor"), posts.get("ended")
    tested = " ".join((idea.get("variants") or "").replace("- ", "").split()) \
        or f"`{idea.get('experiment', '')}` arm a vs control"
    result = (mon or {}).get("evidence", "").strip()
    ended_by = ((ended or {}).get("reason") or "").strip().rstrip(".")
    implication = (implication or IMPLICATION.get(verdict, "")).strip()
    source = next((x["permalink"] for x in (mon, ended) if x and x.get("permalink")), "")
    head = f"{verdict.capitalize()}: {idea.get('title', '').strip()}"
    text = " ".join(x for x in (
        f"{head}.",
        f"Tested: {tested.rstrip('.')}." if tested else "",
        f"Result: {result.rstrip('.')}." if result else "",
        f"Ended: {ended_by}." if ended_by else "",
        f"Implication: {implication.rstrip('.')}." if implication else "",
    ) if x)
    return {"verdict": verdict, "tested": tested, "result": result, "ended_by": ended_by,
            "implication": implication, "source": source, "text": text}


def write_learning(idea: dict[str, Any], learning: dict[str, str], ds_id: str) -> str:
    """Log the learning on the idea row and in `Learnings/Loop/Loop Learnings`. Never raises.

    The Loop Learnings row is keyed by the idea's `Idea ID`, so a correction rewrites the
    same row instead of adding a second answer for one idea.
    """
    rt = make_rich_text
    try:
        notion.update_page_props(idea["page_id"], {"Learning": rt(learning["text"])})
        props = {
            "Idea ID": rt(idea.get("idea_id") or ""),
            "Idea": {"url": idea.get("url") or None},
            "Loop": make_select(LOOP_NAME.get(ds_id, "Aurie Features")),
            "Experiment": rt(idea.get("experiment") or ""),
            "Verdict": make_select(learning["verdict"]),
            "Screen": rt(idea.get("screen") or ""),
            "Domain": rt(idea.get("domain") or ""),
            "Tested": rt(learning["tested"]),
            "Result": rt(learning["result"]),
            "Ended by": rt(learning["ended_by"]),
            "Implication": rt(learning["implication"]),
            "Source": {"url": learning["source"] or None},
            "Decided at": make_date(datetime.date.today().isoformat()),
        }
        found = list(notion.query_data_source(LOOP_LEARNINGS_DS, filter={
            "property": "Idea ID", "rich_text": {"equals": idea.get("idea_id") or "-"}}))
        if found:
            notion.update_page_props(found[0]["id"], {**props, "Title": make_title(idea.get("title", ""))})
            return "updated"
        notion.create_database_row(LOOP_LEARNINGS_DS, {"title": make_title(idea.get("title", "")), **props},
                                   [block_paragraph(learning["text"])])
        return "logged"
    except Exception as exc:  # noqa: BLE001 — the verdict is written; the log is retried by `learning`
        return f"not logged ({type(exc).__name__}: {exc}) — re-run `learning <ref>`"


# ---------- writes ----------------------------------------------------------

def write_verdict(idea: dict[str, Any], status: str, learning: str) -> None:
    notion.update_page_props(idea["page_id"], {
        "Status": make_select(status),
        "Quick status note": make_rich_text(learning),
    })


PERMALINK_RE = re.compile(r"/archives/(C[A-Z0-9]+)/p(\d{10})(\d{6})")


def parse_permalink(url: str) -> Optional[tuple[str, str]]:
    m = PERMALINK_RE.search(url or "")
    return (m.group(1), f"{m.group(2)}.{m.group(3)}") if m else None


def announce(idea: dict[str, Any], status: str, learning: str) -> tuple[bool, str]:
    """Reply the verdict in the idea's thread. Never raises.

    `SystemExit` is caught deliberately alongside `Exception`: `slack_post` calls
    `sys.exit()` on a missing token or a Slack error, and `SystemExit` derives
    from `BaseException`, so a bare `except Exception` does not stop it. In
    `sweep --apply --announce` that would kill the process after the first row —
    the rows already written would be correct and every remaining one would stay
    at `experiment_running`, a half-applied sweep that looks like a crash rather
    than a partial result. Same guard, same reason, as `share-ideas`.

    The Notion write always happens first, so a Slack failure costs the reply,
    never the record.
    """
    parsed = parse_permalink(idea["slack"])
    if not parsed:
        return False, "no usable Slack thread on the row"
    channel, ts = parsed
    emoji = RESULT_EMOJI[status]
    text = f"{emoji} *{status}* — `{idea['experiment']}`. {learning}"
    try:
        from slack_post import post_message  # noqa: PLC0415
        resp = post_message(channel=channel, text=text, thread_ts=ts,
                            footer_skill="product:learn-idea")
    except (Exception, SystemExit) as exc:  # noqa: BLE001
        return False, f"not posted ({type(exc).__name__}: {exc})"
    return bool(resp.get("ok")), resp.get("error") or "posted"


# ---------- commands --------------------------------------------------------

def _require_notion() -> None:
    if not notion.available:
        raise MCPFallback("NOTION_TOKEN is not set.")


def cmd_plan(args: argparse.Namespace) -> int:
    _require_notion()
    rows = load_ideas(args.data_source_id)
    idea = resolve(rows, args.ref)
    exp_status = load_experiment_status().get((idea["experiment"] or "").strip().lower())
    status, why = verdict_for(exp_status)

    print(f"Idea       : {idea['title']}")
    print(f"Fingerprint: {idea['fingerprint']}")
    print(f"Status     : {idea['status']}")
    print(f"Experiment : {idea['experiment'] or '-'}  (Learnings row: {exp_status or 'not found'})")
    print(f"\nVerdict    : {status or 'none'} — {why}")
    posts = find_end_posts(idea["experiment"]) if idea["experiment"] else None
    if posts is None:
        print("End posts  : Slack could not be searched (SLACK_USER_TOKEN)")
    else:
        d = decide(status, posts)
        print(f"End posts  : monitor={'yes' if posts['monitor'] else 'no'} "
              f"data-experiment={'yes' if posts['ended'] else 'no'}")
        print(f"Would write: {d['verdict'] or 'nothing — ask Yawar'}  {d['learning']}")
        if d["disagreement"]:
            print(f"! {d['disagreement']}")
    if status and idea["status"] != LEARNABLE_STATUS:
        print(f"\n! row is '{idea['status']}', not '{LEARNABLE_STATUS}' — record would refuse.")
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    _require_notion()
    rows = load_ideas(args.data_source_id)
    idea = resolve(rows, args.ref)

    # Linking a cleanup card is independent of recording a verdict, and normally
    # happens AFTER it — the removal card only exists once the sweep has run, which
    # is after the result is recorded. So a `--cleanup-issue` re-run on an
    # already-decided row links the card and stops, rather than hitting the
    # experiment_running gate (which would refuse it — the row is no longer running).
    RESULT_STATUSES = {"won", "lost", "inconclusive"}
    if args.cleanup_issue and idea["status"] in RESULT_STATUSES:
        if idea.get("cleanup_issue"):
            print(f"= {idea['fingerprint']} cleanup already linked: {idea['cleanup_issue']} "
                  f"— not overwriting.")
        else:
            notion.update_page_props(idea["page_id"], {"Cleanup issue": {"url": args.cleanup_issue}})
            print(f"~ {idea['fingerprint']} cleanup card linked: {args.cleanup_issue}")
        return 0

    if args.status:
        # The override exists for `Turned off`, where the verdict is a human
        # judgement the data does not carry. Writing one without saying what it
        # rests on puts an unexplained answer in front of every future ideation
        # run — so the reason is required, not optional, on this path only.
        if not args.learning:
            print("REFUSED: --status requires --learning. An explicit verdict is a judgement "
                  "the database does not support on its own, and `Quick status note` is what "
                  "the ideation prior-knowledge block shows as the answer. Say what it rests "
                  "on.", file=sys.stderr)
            return 2
        status, why = args.status, "supplied explicitly"
        derived_learning = ""
        posts = find_end_posts(idea["experiment"]) if idea["experiment"] else None
    else:
        exp_status = load_experiment_status().get((idea["experiment"] or "").strip().lower())
        status, why = verdict_for(exp_status)
        derived_learning = ""
        posts = find_end_posts(idea["experiment"]) if idea["experiment"] else None
        if posts is None:
            print("  ! Slack could not be searched for why it ended — writing the Learnings "
                  "row's verdict without a reason.", file=sys.stderr)
        else:
            d = decide(status, posts)
            if d["disagreement"]:
                print(f"  ! {d['disagreement']}")
            if d["verdict"]:
                status, why, derived_learning = d["verdict"], "from the end posts", d["learning"]
    if not status:
        print(f"REFUSED: {why}", file=sys.stderr)
        return 2
    correcting = args.correct and idea["status"] in RESULT_STATUSES
    if args.correct and not (args.status and args.learning):
        print("REFUSED: --correct needs --status and --learning — a corrected verdict says "
              "what it rests on.", file=sys.stderr)
        return 2
    if idea["status"] != LEARNABLE_STATUS and not correcting:
        print(f"REFUSED: status is '{idea['status']}', not '{LEARNABLE_STATUS}'. A verdict "
              f"belongs only on a row whose experiment actually ran. To fix a recorded verdict, "
              f"pass --correct with --status and --learning.", file=sys.stderr)
        return 2

    learning = args.learning or derived_learning or default_learning(status)
    write_verdict(idea, status, learning)
    print(f"~ {idea['fingerprint']} [{idea['status']} -> {status}]  {idea['title'][:60]}")
    print(f"  {learning}")
    if posts is None and idea["experiment"]:
        print("  learning: not logged — Slack could not be searched for the end posts; "
              "re-run `learning <ref> --apply` once it can.")
    else:
        lesson = compose_learning(idea, status, posts, args.implication or "")
        print(f"  learning: {write_learning(idea, lesson, args.data_source_id)} — {lesson['text']}")
    if args.announce:
        ok, detail = announce(idea, status, learning)
        print(f"  slack: {detail}", file=sys.stderr if not ok else sys.stdout)

    # A result means the experiment has ended, so its code can come out. Unlike an
    # abandon, this path CAN use the sweep: the server reports it ended, so
    # `eng:sweep-ended-experiments` detects it and files the proper checklist card.
    # The loop links that card back on the row, so idea -> shipped code -> removed
    # code is one unbroken trail. Idempotent: an existing link is never overwritten.
    exp = idea["experiment"] or "<experiment>"
    if args.cleanup_issue:
        if idea.get("cleanup_issue"):
            print(f"  cleanup already linked: {idea['cleanup_issue']} — not overwriting.")
        else:
            notion.update_page_props(idea["page_id"], {"Cleanup issue": {"url": args.cleanup_issue}})
            print(f"  cleanup card linked: {args.cleanup_issue}")
    elif idea.get("cleanup_issue"):
        print(f"  cleanup already linked: {idea['cleanup_issue']}")
    else:
        print(f"\n  Code cleanup: run `eng:sweep-ended-experiments key={exp}` to file the "
              f"removal card, then re-run `record {args.ref} --cleanup-issue <url>` to link it "
              f"on the row.")
    return 0


def cmd_sweep(args: argparse.Namespace) -> int:
    _require_notion()
    rows = load_ideas(args.data_source_id)
    statuses = load_experiment_status()
    live = [r for r in rows if r["status"] == LEARNABLE_STATUS]
    if not live:
        print(f"No rows at '{LEARNABLE_STATUS}'. Nothing to sweep.")
        return 0

    # Three outcomes, not two. A row whose experiment is still `Running` resolves
    # on its own; one at `Stopped splitting` or `Feature flag` never will, and
    # folding the two together is how an idea waits on a row that cannot change.
    ready, waiting, blocked = [], [], []
    for r in live:
        status = statuses.get((r["experiment"] or "").strip().lower())
        verdict, why = verdict_for(status)
        posts = find_end_posts(r["experiment"]) if r["experiment"] else None
        ended_in_slack = bool(posts and (posts["monitor"] or posts["ended"]))
        if verdict or ended_in_slack:
            if posts is None:
                waiting.append((r, None, "ended, but Slack could not be searched for why "
                                         "(SLACK_USER_TOKEN) — not writing a verdict without it."))
                continue
            d = decide(verdict, posts)
            d["posts"] = posts
            if d["verdict"]:
                ready.append((r, d, why))
            else:
                blocked.append((r, d, "ended with no end post saying why — Yawar is asked "
                                      "in the thread."))
        elif status in (STOPPED_SPLITTING, FEATURE_FLAG, "Turned off"):
            blocked.append((r, None, why))
        else:
            waiting.append((r, None, why))

    for r, d, why in ready:
        print(f"~ {r['fingerprint']} [{r['status']} -> {d['verdict']}]  {r['title'][:55]}")
        print(f"    {d['learning']}")
        if d["disagreement"]:
            print(f"    ! {d['disagreement']}")
        if args.apply:
            write_verdict(r, d["verdict"], d["learning"])
            lesson = compose_learning(r, d["verdict"], d.get("posts"))
            print(f"    learning: {write_learning(r, lesson, args.data_source_id)}")
            if args.announce:
                ok, detail = announce(r, d["verdict"], d["learning"])
                print(f"    slack: {detail}")
    for r, _, why in waiting:
        print(f"= {r['fingerprint']} held — {why}")
    for r, d, why in blocked:
        print(f"! {r['fingerprint']} NEEDS A DECISION — {why}")
        if d and d.get("ask") and args.apply and args.announce:
            print(f"    asked Yawar: {'yes' if ask_why(r) else 'already asked or not posted'}")

    print(f"\n{len(ready)} to write, {len(waiting)} waiting on an experiment still running, "
          f"{len(blocked)} waiting on a person.")
    if blocked:
        print("  The blocked rows will not clear on their own. Decide each with "
              "`record <ref> --status <won|lost|inconclusive> --learning \"…\"`, or drop it.")
    if ready and not args.apply:
        print("-- dry run. Add --apply to write. --", file=sys.stderr)
    elif args.apply:
        # Each row just decided has ended code to remove. Surface the routing here —
        # a batch sweep would otherwise decide silently and leave the cleanup untraced.
        needs = cleanup_keys([r for r, _, _ in ready])
        if needs:
            print("\n  Code cleanup for the rows just decided — for each, run "
                  "`eng:sweep-ended-experiments key=<key>` (that key only, never a full sweep), "
                  "then link it back with `record <ref> --cleanup-issue <url>`:")
            for fp, key in needs:
                print(f"    - {fp}  key={key}")
    return 0


def cmd_learning(args: argparse.Namespace) -> int:
    """(Re)write the learning for an already-decided row, without touching its verdict."""
    _require_notion()
    rows = load_ideas(args.data_source_id)
    idea = resolve(rows, args.ref)
    if idea["status"] not in ("won", "lost", "inconclusive"):
        print(f"REFUSED: status is '{idea['status']}' — a learning follows a recorded verdict.",
              file=sys.stderr)
        return 2
    posts = find_end_posts(idea["experiment"]) if idea["experiment"] else None
    if posts is None and idea["experiment"]:
        print("REFUSED: Slack could not be searched for the end posts (SLACK_USER_TOKEN) — "
              "not rewriting the learning with the result and reason blank.", file=sys.stderr)
        return 2
    lesson = compose_learning(idea, idea["status"], posts, args.implication or "")
    if not args.apply:
        print(f"Would log: {lesson['text']}")
        print("-- dry run. Add --apply to write. --", file=sys.stderr)
        return 0
    print(f"~ {idea['fingerprint']} learning: {write_learning(idea, lesson, args.data_source_id)}")
    print(f"  {lesson['text']}")
    return 0


def cmd_funnel(args: argparse.Namespace) -> int:
    _require_notion()
    rows = load_ideas(args.data_source_id)
    if args.month:
        rows = [r for r in rows if month_of(r) == args.month]
    buckets: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for r in rows:
        buckets[month_of(r)].append(r)

    header = "month     " + "".join(f"{s:>11}" for s in STAGES)
    print(header)
    print("-" * len(header))
    for month in sorted(buckets):
        counts = funnel_counts(buckets[month])
        print(f"{month:10}" + "".join(f"{counts[s]:>11}" for s in STAGES))
    print("-" * len(header))
    total = funnel_counts(rows)
    print(f"{'all':10}" + "".join(f"{total[s]:>11}" for s in STAGES))
    print("\nCumulative: a row counts at every stage it reached, so the drop between "
          "columns is the conversion at that step.")

    stray = unmapped_statuses(rows)
    if stray:
        detail = ", ".join(f"{k or '(blank)'} ×{v}" for k, v in stray.most_common())
        print(f"\n! {sum(stray.values())} row(s) counted at NO stage — their status has no "
              f"place in the funnel: {detail}.\n"
              f"  The chart above is short by that many. Map them or fix the rows.",
              file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="learn_idea", description=__doc__.split("\n")[0])
    p.add_argument("--data-source-id", default=AURIE_FEATURES_DS)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("plan", help="What the write-back would do. No writes.")
    sp.add_argument("ref")
    sp.set_defaults(func=cmd_plan)

    sp = sub.add_parser("record", help="Write one row's verdict")
    sp.add_argument("ref")
    sp.add_argument("--status", choices=sorted(set(VERDICT.values())),
                    help="Override — required when the experiment is 'Turned off'")
    sp.add_argument("--learning", help="One line for Quick status note")
    sp.add_argument("--cleanup-issue", dest="cleanup_issue",
                    help="URL of the eng:sweep-ended-experiments cleanup card, to link "
                         "on the row (write-once; an existing link is never overwritten)")
    sp.add_argument("--announce", action="store_true", help="Reply in the idea's Slack thread")
    sp.add_argument("--implication",
                    help="What the result means for the next ideas (the tick's judgement); "
                         "defaults to the verdict's generic implication")
    sp.add_argument("--correct", action="store_true",
                    help="Replace an already-recorded verdict (needs --status and --learning)")
    sp.set_defaults(func=cmd_record)

    sp = sub.add_parser("sweep", help="Every experiment_running row whose experiment ended")
    sp.add_argument("--apply", action="store_true", help="Actually write (default: dry run)")
    sp.add_argument("--announce", action="store_true")
    sp.set_defaults(func=cmd_sweep)

    sp = sub.add_parser("learning", help="(Re)write a decided row's learning log entry")
    sp.add_argument("ref")
    sp.add_argument("--implication", help="What the result means for the next ideas")
    sp.add_argument("--apply", action="store_true", help="Actually write (default: dry run)")
    sp.set_defaults(func=cmd_learning)

    sp = sub.add_parser("funnel", help="proposed -> ... -> decided, by month")
    sp.add_argument("--month", help="YYYY-MM; omit for every month")
    sp.set_defaults(func=cmd_funnel)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except MCPFallback as e:
        print(f"learn-idea: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
