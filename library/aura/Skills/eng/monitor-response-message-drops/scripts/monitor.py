#!/usr/bin/env python3
"""One monitoring tick for IntelligenceService's `Dropped extra response message items` log.

IntelligenceService 1.16.9 delivers only the first `message` item of an OpenAI
Responses turn and logs the rest as shape (`droppedCount`, `keptChars`,
`dropped[].chars / duplicate / looksLikeOpener`, `responseId`) — never text.
This script turns that log into a readout an engineer or a cron loop can act on,
and remembers the previous tick so it can report a delta.

The readout is shape and counts only, and the log it reads carries no text. Text
leaves the process on exactly two paths, both explicit: `--show-text` prints it
for an operator at the terminal, and `--post` reports each new drop — user turn,
kept reply, dropped item — to the one private Slack channel that exists for it
(`SLACK_CHANNEL`), deduplicated by responseId through the per-version state file.

Stdout is forced to UTF-8 (Windows cp1252 raises on the arrows and dashes here).
"""

import argparse
import collections
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import gcp  # noqa: E402  (byte-identical copy of investigate-gcp-logs/scripts/gcp.py)

CONTAINER = "intelligence-service"
CLUSTER = "aura-prod-cluster-a"
DROP_MSG = "Dropped extra response message items"
TURN_MSG = "OpenAI streaming response started"
# Prompter-name warnings, counted over a fixed window on every version, by `prompterName`.
PROMPTER_MSGS = {"unknown": "Unknown prompter name", "retired": "Retired prompter name counted"}
PROMPTER_HOURS = 24
PROMPTER_STATE = "prompters.json"   # retired names already raised; not per version
KEPT_ALIASES = {"chat"}   # aliases that stay by decision: counted, never raised as removable

OPENAI_LOG_URL = "https://platform.openai.com/logs/{rid}"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses/{rid}"
OPENAI_INPUT_ITEMS_URL = "https://api.openai.com/v1/responses/{rid}/input_items?limit=5&order=desc"

# --post: the private channel that holds the dropped text, and nowhere else.
SLACK_CHANNEL = "C0BV9DHUT8C"   # #ai-conversation-message-drops
SKILL_ID = "eng:monitor-response-message-drops"
SKILL_DIR = pathlib.Path(__file__).resolve().parent.parent   # holds SKILL.md → footer version
# Texts at or under this length post verbatim; longer ones need an operator summary.
LONG_TEXT_CHARS = 600
# Verdicts the script may assign on its own (feature-decided). Everything else is `read`
# and waits for a verdict in the --verdicts file.
SELF_DECIDED = frozenset({"repeat", "opener"})
CATEGORY = {
    "paraphrase":   (":repeat:", "Paraphrase", "the dropped item restates the kept reply's points in different words; nothing new lost."),
    "continuation": (":warning:", "Genuine continuation", "the dropped item adds a point, question or turn the kept reply does not contain — this is the real cost of the drop."),
    "opener":       (":wave:", "Opener", "a greeting / presence line that would have arrived *after* a complete reply (the class behind the original :-1:)."),
    "sign-off":     (":checkered_flag:", "Sign-off", "a one-line closer after a complete reply; additive in tone only."),
    "repeat":       (":repeat_one:", "Repeat", "byte-equal (or trivially so) to the kept reply — the reported defect, spoken twice by TTS."),
}
# The same crude opener shape IntelligenceService uses for `looksLikeOpener`.
OPENER_RE = re.compile(r"^\s*(hey|hi|hello)\b|i['\u2019]m here|still here with|popping in with", re.I)
STOPWORDS = frozenset("""a an and are as at be but by can could do for from had has have he her his i if in is
it its just me my not of on or our she so that the their them then there they this to too up us was we were what
when who will with would you your""".split())

DEFAULT_STATE_DIR = pathlib.Path(
    os.environ.get("AI_KNOWLEDGE_CACHE_DIR", os.path.expanduser("~/.cache/ai-knowledge"))
) / "monitor-response-message-drops"


def parse_since(text: str, now: dt.datetime) -> dt.datetime:
    """Accept an ISO-8601 instant or a shorthand window such as `30m`, `4h`, `2d`."""
    unit = text[-1:]
    if unit in "smhd" and text[:-1].isdigit():
        n = int(text[:-1])
        delta = {"s": dt.timedelta(seconds=n), "m": dt.timedelta(minutes=n),
                 "h": dt.timedelta(hours=n), "d": dt.timedelta(days=n)}[unit]
        return now - delta
    parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def version_of(entry: dict) -> str:
    ctx = (entry.get("jsonPayload") or {}).get("serviceContext") or {}
    return str(ctx.get("version") or "?")


def quantiles(xs):
    if not xs:
        return None
    xs = sorted(xs)
    return {"min": xs[0], "median": int(statistics.median(xs)), "max": xs[-1], "n": len(xs)}


def fmt_q(q) -> str:
    return "—" if not q else f"min {q['min']} · median {q['median']} · max {q['max']}"


def signature(message: str) -> str:
    """A log message with its numbers masked, so `Execution time for X: 200341 ms`
    and `… 215738 ms` are one signature rather than a new one every tick."""
    return re.sub(r"\d+", "#", str(message))[:80]


def content_words(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9']+", text.lower()) if w not in STOPWORDS and len(w) > 2}


def classify_pair(kept: str, dropped: str) -> dict:
    """Verdict on a dropped item from features alone — and only where features can decide.

    repeat  — same text once whitespace/punctuation are ignored
    opener  — short and shaped like a greeting / presence line
    read    — everything else: paraphrase vs genuine continuation needs the text read

    Word overlap does NOT separate paraphrase from continuation. The first two
    production drops were paraphrases by a human read and scored coverage 0.51
    and 0.30 — they restate the points in different words. A threshold here
    would have labelled both as continuations, so none is applied; `coverage`
    (share of the dropped item's content words already in the kept reply) and
    `jaccard` are reported as evidence for the reader, not as a decision.
    """
    norm = lambda t: re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()
    k, d = content_words(kept), content_words(dropped)
    coverage = (len(k & d) / len(d)) if d else 0.0
    jaccard = (len(k & d) / len(k | d)) if (k | d) else 0.0
    if norm(kept) == norm(dropped):
        verdict = "repeat"
    elif len(dropped) < 160 and OPENER_RE.search(dropped[:60] or ""):
        verdict = "opener"
    else:
        verdict = "read"
    return {"verdict": verdict, "coverage": round(coverage, 2), "jaccard": round(jaccard, 2),
            "keptChars": len(kept), "droppedChars": len(dropped)}


def _openai_get(url: str, api_key: str) -> dict:
    import urllib.request
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _item_text(item: dict) -> str:
    content = item.get("content")
    if isinstance(content, str):
        return content
    return "".join(c.get("text", "") for c in (content or [])
                   if isinstance(c, dict) and c.get("type") in ("output_text", "input_text"))


def fetch_response(rid: str, api_key: str) -> dict:
    """A stored Response: every `message` item's text in output order, its metadata
    (dialogue / conversation / user / prompt name + version), the resolved model, and
    the user turn that preceded it (newest `role: user` input item)."""
    data = _openai_get(OPENAI_RESPONSES_URL.format(rid=rid), api_key)
    items = [_item_text(i) for i in data.get("output", []) if i.get("type") == "message"]
    user_turn = None
    try:
        inputs = _openai_get(OPENAI_INPUT_ITEMS_URL.format(rid=rid), api_key).get("data", [])
        user_turn = next((_item_text(i) for i in inputs if i.get("role") == "user"), None)
    except Exception:  # noqa: BLE001 — the turn is context for the post, not the verdict
        user_turn = None
    return {"texts": items, "metadata": data.get("metadata") or {}, "model": data.get("model") or "?",
            "user_turn": user_turn}


def classify_drops(drops: list, api_key: str) -> list:
    """Fetch each dropped response and attach a provisional verdict per extra item.

    Failures are recorded per drop rather than raised: one unreadable response
    must not take the whole tick down, and a missing verdict is visible as such.
    """
    for d in drops:
        rid = d.get("responseId")
        if not rid:
            d["analysis"] = {"error": "no responseId"}
            continue
        try:
            resp = fetch_response(rid, api_key)
        except Exception as exc:  # noqa: BLE001 — report, don't crash the tick
            d["analysis"] = {"error": f"{type(exc).__name__}: {exc}"[:120]}
            continue
        items = resp["texts"]
        if len(items) < 2:
            d["analysis"] = {"error": f"{len(items)} message item(s) in stored response"}
            continue
        d["analysis"] = {"items": [classify_pair(items[0], extra) for extra in items[1:]],
                         "_private": resp}   # stripped before persisting; only --show-text / --post use it
    return drops


# ---------- --post: one Slack message per new drop --------------------------------

def _code(text: str) -> str:
    """A Slack code block; a fence inside the text would end it early."""
    return "```" + text.strip().replace("```", "'" * 3) + "```"


def _section(label: str, text: str, summary: str | None) -> str:
    """A titled code block. The summary applies only above the verbatim budget, so a
    verdicts file carrying one for a short text (the schema example shows all three
    keys) cannot replace a line the channel should have seen in full."""
    if summary and len(text) > LONG_TEXT_CHARS:
        return f"*{label}* _(summary of {len(text)} chars)_\n{_code(summary)}"
    return f"*{label}*\n{_code(text)}"


def resolve_verdict(entry: dict, verdicts: dict) -> str | None:
    """The category to post under, or None when an operator still has to decide.

    The --verdicts file wins over the feature verdict. Without a file entry the
    script posts only what features decide (`repeat`, `opener`); `read` waits.
    """
    rid = entry.get("responseId")
    filed = (verdicts.get(rid) or {}).get("verdict")
    if filed:
        if filed not in CATEGORY:
            raise ValueError(f"{rid}: unknown verdict {filed!r} — one of {sorted(CATEGORY)}")
        return filed
    items = (entry.get("analysis") or {}).get("items") or []
    feature = {it["verdict"] for it in items}
    if feature and feature <= SELF_DECIDED:
        return "repeat" if feature == {"repeat"} else "opener"
    return None


def missing_summaries(private: dict, filed: dict) -> list[str]:
    """Which of user / kept / dropped exceed LONG_TEXT_CHARS with no operator summary."""
    summaries = filed.get("summaries") or {}
    texts = {"user": private.get("user_turn") or "", "kept": private["texts"][0],
             "dropped": "\n\n".join(private["texts"][1:])}
    return [k for k, t in texts.items() if len(t) > LONG_TEXT_CHARS and not summaries.get(k)]


def format_post(entry: dict, private: dict, verdict: str, filed: dict) -> str:
    emoji, label, definition = CATEGORY[verdict]
    kept, dropped = private["texts"][0], private["texts"][1:]
    meta, summaries = private.get("metadata") or {}, filed.get("summaries") or {}
    rid, ts = entry.get("responseId") or "?", entry["ts"]
    lines = ["*MESSAGE DROPPED*", f"{emoji} *{label}* — {definition}"]
    if filed.get("note"):
        lines.append(f"_{filed['note']}_")
    lines.append(f"{ts[:10]} {ts[11:19]}Z · kept {len(kept)} chars · dropped "
                 f"{sum(len(d) for d in dropped)} chars · <{OPENAI_LOG_URL.format(rid=rid)}|OpenAI log>")
    lines += ["", _section("User turn", private.get("user_turn") or "(no user turn in the stored input)", summaries.get("user")),
              "", _section("Assistant — kept (delivered)", kept, summaries.get("kept"))]
    if len(dropped) == 1:
        lines += ["", _section("Assistant — dropped", dropped[0], summaries.get("dropped"))]
    else:
        lines += ["", _section(f"Assistant — dropped ({len(dropped)} items)", "\n\n".join(dropped), summaries.get("dropped"))]
    lines += ["", f"`[dialogue: {meta.get('dialogueId', '?')}, conversation: {meta.get('conversationId', '?')}, "
                  f"user: {meta.get('user', '?')}, prompt:{meta.get('name', '?')}:{meta.get('version', '?')}, "
                  f"model: {private.get('model', '?')}]`"]
    return "\n".join(lines)


def post_drops(entries: list, private_by_rid: dict, verdicts: dict, posted: dict, *, dry_run: bool,
               send=None, persist=None) -> dict:
    """Post every not-yet-posted drop that has a verdict and every needed summary.

    Returns {"posted": [...], "held": [(rid, reason)], "skipped": n}. `posted` is
    mutated in place with the new ids. `send` is the Slack call (injected for tests).

    Two properties keep a mid-batch failure from re-publishing a user's conversation
    on the next tick. `persist` is called with `posted` after **each** successful
    post, so the record of what went out survives a later abort rather than waiting
    for the loop to return. And the send is guarded against `SystemExit` as well as
    `Exception`: the Slack helper exits the process on a transport fault, and
    `SystemExit` does not inherit from `Exception`, so an `except Exception` alone
    would let it past the per-drop handler with the id unrecorded.
    """
    if send is None:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
        import slack_post  # noqa: E402  (byte-identical copy of shared:post-to-slack's canonical)

        def send(text):
            resp = slack_post.post_message(SLACK_CHANNEL, text, footer_skill=SKILL_ID,
                                           footer_skill_md=str(SKILL_DIR), footer_extra="prod")
            if resp.get("ok"):
                link = slack_post.get_permalink(SLACK_CHANNEL, resp["ts"])
                resp["permalink"] = link.get("permalink") if link.get("ok") else None
            return resp
    out = {"posted": [], "held": [], "skipped": 0}
    for entry in sorted(entries, key=lambda e: e["ts"]):   # oldest first, like the channel reads
        rid = entry.get("responseId")
        if not rid or rid in posted:
            out["skipped"] += 1
            continue
        private = private_by_rid.get(rid)
        if not private:
            out["held"].append((rid, "no stored text (see analysis error)"))
            continue
        filed = verdicts.get(rid) or {}
        verdict = resolve_verdict(entry, verdicts)
        if verdict is None:
            out["held"].append((rid, "needs a verdict (read the pair below)"))
            continue
        missing = missing_summaries(private, filed)
        if missing:
            out["held"].append((rid, f"needs a summary for: {', '.join(missing)} (> {LONG_TEXT_CHARS} chars)"))
            continue
        text = format_post(entry, private, verdict, filed)
        if dry_run:
            print(f"\n# --post-dry-run · {rid}\n{text}")
            out["posted"].append({"responseId": rid, "verdict": verdict, "dry_run": True})
            continue
        try:
            resp = send(text)
        except (Exception, SystemExit) as exc:  # noqa: BLE001 — one failed post must not stop the rest
            resp = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:120]}
        if not resp.get("ok"):
            out["held"].append((rid, f"slack error: {resp.get('error', '?')}"))
            continue
        rec = {"responseId": rid, "verdict": verdict, "slack_ts": resp.get("ts"),
               "permalink": resp.get("permalink"), "posted_at": iso(dt.datetime.now(dt.timezone.utc)),
               "drop_ts": entry["ts"]}
        posted[rid] = rec
        out["posted"].append(rec)
        if persist:
            persist(posted)
    return out


def render_post_summary(outcome: dict, private_by_rid: dict, entries: list) -> str:
    verdicts = collections.Counter(p["verdict"] for p in outcome["posted"])
    lines = [f"(5) posted to {SLACK_CHANNEL}: {len(outcome['posted'])} {dict(verdicts) or ''}   "
             f"already posted: {outcome['skipped']}   held: {len(outcome['held'])}"]
    for p in outcome["posted"]:
        lines.append(f"    · {p['responseId']}  {p['verdict']:<13} {p.get('permalink') or ('(dry run)' if p.get('dry_run') else '')}")
    if verdicts.get("continuation"):
        lines.append(f"    NOTE: {verdicts['continuation']} genuine continuation(s) posted — record on the monitoring card")
    by_rid = {e.get("responseId"): e for e in entries}
    for rid, reason in outcome["held"]:
        lines.append(f"    HELD {rid}: {reason}")
        private = private_by_rid.get(rid)
        if private and reason.startswith(("needs a verdict", "needs a summary")):
            e = by_rid.get(rid, {})
            lines.append(f"      {OPENAI_LOG_URL.format(rid=rid)}  ({e.get('ts', '?')[11:19]}Z)")
            for label, text in (("USER   ", private.get("user_turn") or ""), ("KEPT   ", private["texts"][0]),
                                *(("DROPPED", t) for t in private["texts"][1:])):
                lines.append(f"      [{label}] ({len(text)} chars)\n          " + text.replace("\n", "\n          "))
    if outcome["held"]:
        lines.append(f"    → write a verdicts file for the held ids and re-run with --post --verdicts <file> "
                     f"(schema: references/slack-post-format.md)")
    return "\n".join(lines)


def fetch(auth, filter_str: str, cap: int, truncated: dict, label: str):
    """Yield entries and record in `truncated` whether the cap was reached.

    `list_entries` returns newest-first and stops at the cap, so a capped query
    keeps the newest entries and silently loses the oldest. A rate computed over
    a truncated numerator or denominator is not a rate, so the caller refuses to
    print one rather than print a wrong one.
    """
    count = 0
    for e in gcp.list_entries(auth, filter_str, max_entries=cap):
        count += 1
        yield e
    truncated[label] = count >= cap


def collect(auth, since: dt.datetime, version: str | None, recent_minutes: int):
    base = (
        f'resource.type="k8s_container" AND resource.labels.cluster_name="{CLUSTER}" '
        f'AND resource.labels.container_name="{CONTAINER}" AND timestamp>="{iso(since)}" '
    )
    now = dt.datetime.now(dt.timezone.utc)
    recent_cut = iso(now - dt.timedelta(minutes=recent_minutes))

    # (1) turns by version — also decides the target version when none was given
    truncated: dict[str, bool] = {}
    turns, recent = collections.Counter(), collections.Counter()
    for e in fetch(auth, base + f'AND jsonPayload.message="{TURN_MSG}"', 50000, truncated, 'turns'):
        v = version_of(e)
        turns[v] += 1
        if e["timestamp"] >= recent_cut:
            recent[v] += 1
    # Auto-pick from the recent window first: during a rollout the whole window is
    # dominated by the build being replaced, which is the wrong one to watch.
    pool = recent or turns
    target = version or (max(pool, key=pool.get) if pool else "?")
    others = {v: n for v, n in turns.items() if v != target}

    # (2) errors on the target version
    err_sigs = collections.Counter()
    for e in fetch(
        auth, base + f'AND severity>=ERROR AND jsonPayload.serviceContext.version="{target}"', 5000, truncated, 'errors'
    ):
        p = e.get("jsonPayload") or {}
        err_sigs[(str(p.get("component") or "?"), signature(p.get("message") or "?"))] += 1

    # (3) drops on the target version
    drops, kept_zero, classes = [], [], collections.Counter()
    kept_chars, dropped_chars = [], []
    for e in fetch(auth, base + f'AND jsonPayload.message="{DROP_MSG}"', 20000, truncated, 'drops'):
        if version_of(e) != target:
            continue
        p = e.get("jsonPayload") or {}
        kc = int(p.get("keptChars") or 0)
        kept_chars.append(kc)
        if kc == 0:
            kept_zero.append(p.get("responseId"))
        for item in p.get("dropped") or []:
            dropped_chars.append(int(item.get("chars") or 0))
            if item.get("duplicate"):
                classes["exact duplicate"] += 1
            elif item.get("looksLikeOpener"):
                classes["opener"] += 1
            else:
                classes["neither"] += 1
        drops.append({
            "ts": e["timestamp"],
            "responseId": p.get("responseId"),
            "droppedCount": int(p.get("droppedCount") or 0),
            "keptChars": kc,
            "component": str(p.get("component") or "?"),
        })

    # (4) warning signatures on the target version
    warn_sigs = set()
    for e in fetch(
        auth, base + f'AND severity=WARNING AND jsonPayload.serviceContext.version="{target}"', 20000, truncated, 'warnings'
    ):
        warn_sigs.add(signature((e.get("jsonPayload") or {}).get("message") or "?"))

    n_target = turns.get(target, 0)
    openai_errors = sum(n for (comp, _), n in err_sigs.items() if comp.startswith("openaiService"))
    # A capped numerator or denominator makes the rate meaningless; refuse to compute it.
    rate_ok = n_target and not truncated.get("turns") and not truncated.get("drops")
    return {
        "ts": iso(now),
        "since": iso(since),
        "version": target,
        "turns": {"target": n_target, "others": others, "recent_target": recent.get(target, 0),
                  "recent_others": {v: n for v, n in recent.items() if v != target}},
        "errors": {"total": sum(err_sigs.values()), "openaiService": openai_errors,
                   "signatures": [{"component": c, "message": m, "count": n} for (c, m), n in err_sigs.most_common(10)]},
        "drops": {"total": len(drops),
                  "rate_pct": round(100 * len(drops) / n_target, 2) if rate_ok else None,
                  "items_by_class": dict(classes),
                  "keptChars": quantiles(kept_chars),
                  "droppedChars": quantiles(dropped_chars),
                  "keptZero": kept_zero,
                  "responseIds": [d["responseId"] for d in drops],
                  "entries": drops},
        "warn_sigs": sorted(warn_sigs),
        "truncated": sorted(k for k, v in truncated.items() if v),
    }


def prompter_name(entry: dict) -> str:
    """The `prompterName` field as a printable token. IntelligenceService caps it at 64
    chars, but it is caller-supplied, so anything outside a name's alphabet is masked."""
    raw = str((entry.get("jsonPayload") or {}).get("prompterName") or "?")[:64]
    return re.sub(r"[^A-Za-z0-9_.:-]", "_", raw)


def collect_prompters(auth, now: dt.datetime, hours: int = PROMPTER_HOURS) -> dict:
    """Counts of the two prompter-name warnings over the last `hours`, by name.
    Every version counts: an unknown name is a caller's fault, not a build's."""
    since = now - dt.timedelta(hours=hours)
    base = (
        f'resource.type="k8s_container" AND resource.labels.cluster_name="{CLUSTER}" '
        f'AND resource.labels.container_name="{CONTAINER}" AND timestamp>="{iso(since)}" '
    )
    out, truncated = {"since": iso(since), "hours": hours}, {}
    for kind, msg in PROMPTER_MSGS.items():
        counts = collections.Counter(
            prompter_name(e) for e in fetch(auth, base + f'AND jsonPayload.message="{msg}"', 20000, truncated, kind))
        out[kind] = dict(counts.most_common())
    out["truncated"] = sorted(k for k, v in truncated.items() if v)
    return out


def followup_key(kind: str, name: str) -> str:
    """A `shared:raise-followup` key: lowercase, at most 81 chars. The hash keeps two names
    that differ only in case, or in a cut-off tail, on separate keys."""
    tag = hashlib.sha1(name.encode()).hexdigest()[:8]
    return f"response-drops:{kind}-prompter:{name.lower()[:40]}-{tag}"


# The shapes shared:raise-followup refuses in a text field (its TOKEN, FULL_ID, IPV4 and
# IPV6); a test runs its own check on what these let through.
_REFUSED = re.compile(
    r"\b(?:xox[abposr]-[\w-]+|sk-[\w-]{20,}|gh[pousr]_\w{20,}|github_pat_\w+|ntn_\w{20,}"
    r"|lin_api_\w+|AIza[\w-]{30,})"
    r"|\b(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{20,}\b"
    r"|\b(?:\d{1,3}\.){3}\d{1,3}\b|\b(?:[0-9A-Fa-f]{1,4}:){3,}[0-9A-Fa-f:]*\b")


def shown_name(name: str) -> str:
    """The name as it appears in follow-up text: as is, unless a caller-supplied name has a
    shape raise-followup would refuse; then it is named by its key tag."""
    if not _REFUSED.search(name):
        return f"`{name}`"
    return f"an unusual name (key tag {hashlib.sha1(name.encode()).hexdigest()[:8]})"


def prompter_followups(p: dict, retired_seen: dict, now: str) -> list[dict]:
    """One `shared:raise-followup` per name to raise. The text names no count, so a
    repeat on the next tick is `unchanged` under the same key rather than a new reply.

    unknown — every name above 0, every tick: a caller asks for a prompter that does not exist.
    retired — only a name never raised before; `retired_seen` records it. A kept alias
    (KEPT_ALIASES) is counted but never raised: nobody is waiting to remove it.
    """
    out = []
    for name, n in p["unknown"].items():
        out.append({"kind": "decision", "key": followup_key("unknown", name),
                    "title": f"IntelligenceService was asked for an unknown prompter name: {shown_name(name)}",
                    "what": f"The `Unknown prompter name` warning fired for {shown_name(name)} in the last {p['hours']} h.",
                    "why": "A caller names a prompter IntelligenceService does not have, so that call has no prompt.",
                    "ask": "Find the caller and fix the name, or add the prompter."})
    for name in p["retired"]:
        if name in retired_seen or name in KEPT_ALIASES:
            continue
        retired_seen[name] = now
        out.append({"kind": "fyi", "key": followup_key("retired", name),
                    "title": f"The retired prompter name {shown_name(name)} is still called",
                    "what": f"The `Retired prompter name counted` warning fired for {shown_name(name)} in the last "
                            f"{p['hours']} h, so its alias cannot be removed yet. Raised once; the daily count "
                            "is in the eng:monitor-response-message-drops readout."})
    return out


def render_prompters(p: dict, followups: list[dict]) -> str:
    fmt = lambda c: " ".join(f"{k}={v}" for k, v in c.items()) or "0"
    removable = {k: v for k, v in p["retired"].items() if k not in KEPT_ALIASES}
    lines = [f"(6) prompter names, last {p['hours']} h, all versions:",
             f"    unknown: {fmt(p['unknown'])}",
             f"    retired: {fmt(p['retired'])}"
             # a capped read drops the oldest events, so a missing name is not a true 0
             + ("" if removable or "retired" in p["truncated"]
                else "   → no call to a removable alias: an alias with 0 calls can be removed")
             + (f" (kept by decision: {', '.join(sorted(KEPT_ALIASES))})" if KEPT_ALIASES else "")]
    if p["truncated"]:
        lines.append(f"    truncated at the fetch cap for {', '.join(p['truncated'])} — counts are partial")
    # Every field shared:raise-followup takes, so the text readout alone is enough to raise
    # it: a second run would find a retired name already recorded and print nothing.
    for f in followups:
        lines.append(f"FOLLOWUP {f['kind']} {f['key']}: {f['title']}")
        lines += [f"    {k}: {f[k]}" for k in ("what", "why", "ask") if f.get(k)]
    return "\n".join(lines)


def escalations(r: dict, max_rate: float, min_turns: int) -> list[str]:
    out = []
    if r["truncated"]:
        out.append("window truncated at the fetch cap for " + ", ".join(r["truncated"])
                   + " — counts are partial; narrow --since")
    if r["errors"]["openaiService"]:
        out.append(f"{r['errors']['openaiService']} openaiService ERROR(s) on {r['version']}")
    rate, n = r["drops"]["rate_pct"], r["turns"]["target"]
    if rate is not None and n >= min_turns and rate > max_rate:
        out.append(f"drop rate {rate:.1f}% > {max_rate:.0f}% over {n} turns")
    if r["drops"]["keptZero"]:
        out.append(f"{len(r['drops']['keptZero'])} drop(s) with keptChars==0 — an empty reply shipped: "
                   + ", ".join(str(x) for x in r["drops"]["keptZero"][:5]))
    return out


def render(r: dict, prev: dict | None, esc: list[str]) -> str:
    t, d, e = r["turns"], r["drops"], r["errors"]
    lines = [f"# {CONTAINER} {r['version']} · {r['ts']} · window since {r['since']}"]
    others = " ".join(f"{v}={n}" for v, n in sorted(t["others"].items())) or "—"
    r_others = " ".join(f"{v}={n}" for v, n in sorted(t["recent_others"].items())) or "—"
    drained = t["recent_target"] > 0 and not t["recent_others"]
    lines.append(f"(1) turns  {r['version']}={t['target']}  others: {others}   | recent: "
                 f"{r['version']}={t['recent_target']} others: {r_others}" + ("   → other versions DRAINED" if drained else ""))
    lines.append(f"(2) ≥ERROR on {r['version']}: {e['total']}  (openaiService: {e['openaiService']})")
    for s in e["signatures"]:
        lines.append(f"    {s['count']}× {s['component']} :: {s['message']}")
    rate = "—" if d["rate_pct"] is None else f"{d['rate_pct']:.1f}%"
    if d["rate_pct"] is None and r["truncated"]:
        rate = "withheld (truncated)"
    lines.append(f"(3) drops: {d['total']}  rate {rate} of {t['target']} turns   items: {d['items_by_class'] or {}}")
    lines.append(f"    keptChars {fmt_q(d['keptChars'])}   droppedChars {fmt_q(d['droppedChars'])}   keptChars==0: {len(d['keptZero'])}")
    for entry in d["entries"][:12]:
        rid = entry.get("responseId") or "?"
        line = f"    · {entry['ts'][11:19]}Z  kept {entry['keptChars']:>4}  {OPENAI_LOG_URL.format(rid=rid)}"
        a = entry.get("analysis")
        if a and "items" in a:
            for it in a["items"]:
                line += f"\n        → {it['verdict']:<13} dropped {it['droppedChars']:>4} chars · coverage {it['coverage']:.2f} · jaccard {it['jaccard']:.2f}"
        elif a and "error" in a:
            line += f"\n        → analysis unavailable: {a['error']}"
        lines.append(line)
    if len(d["entries"]) > 12:
        lines.append(f"    … {len(d['entries']) - 12} more")
    verdicts = collections.Counter(it["verdict"] for e in d["entries"] for it in (e.get("analysis") or {}).get("items", []))
    if verdicts:
        lines.append(f"    feature verdicts: {dict(verdicts)}   ('read' = paraphrase vs continuation, decide from the text — SKILL.md step 3)")
    new_warns = sorted(set(r["warn_sigs"]) - set((prev or {}).get("warn_sigs", [])))
    lines.append(f"(4) new WARNING signatures on {r['version']} since last tick: {new_warns or 'none'}")
    if prev:
        lines.append(f"Δ since {prev.get('ts', '?')}: turns +{t['target'] - prev['turns']['target']}, "
                     f"drops +{d['total'] - prev['drops']['total']}, errors +{e['total'] - prev['errors']['total']}")
    if esc:
        lines.append("ESCALATE: " + " | ".join(esc))
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--since", required=True, help="window start: ISO-8601 instant or shorthand (30m, 4h, 2d)")
    ap.add_argument("--version", help="serviceContext.version to monitor (default: the version with the most turns)")
    ap.add_argument("--recent-minutes", type=int, default=30, help="width of the 'recent' rollout window (default 30)")
    ap.add_argument("--max-rate", type=float, default=15.0, help="escalate when drop rate exceeds this %% (default 15)")
    ap.add_argument("--min-turns", type=int, default=100, help="…but only once this many turns are observed (default 100)")
    ap.add_argument("--state-dir", type=pathlib.Path, default=DEFAULT_STATE_DIR,
                    help="where the previous tick is kept for the delta (default under AI_KNOWLEDGE_CACHE_DIR)")
    ap.add_argument("--no-state", action="store_true", help="do not read or write tick state")
    ap.add_argument("--json", action="store_true", help="emit the full result as JSON instead of the readout")
    ap.add_argument("--classify", action="store_true",
                    help="fetch each dropped response from OpenAI (OPENAI_CONVERSATION_API_KEY) and label each extra "
                         "item repeat / opener / read (read = paraphrase-vs-continuation needs the text)")
    ap.add_argument("--show-text", action="store_true",
                    help="with --classify: print the kept and dropped text for an operator at the terminal. "
                         "Never use when the output goes to a card or a channel other than SLACK_CHANNEL.")
    ap.add_argument("--post", action="store_true",
                    help="post each not-yet-posted drop (user turn / kept / dropped, category, ids) to "
                         "#ai-conversation-message-drops via SLACK_BOT_TOKEN; implies --classify. Dedup by "
                         "responseId lives in the state file, so --no-state is refused.")
    ap.add_argument("--verdicts", type=pathlib.Path,
                    help="JSON file {responseId: {verdict, note?, summaries?{user,kept,dropped}}} supplying the "
                         "operator's verdict for `read` items and summaries for texts over LONG_TEXT_CHARS")
    ap.add_argument("--post-dry-run", action="store_true",
                    help="with --post: render the messages to the terminal instead of posting; marks nothing posted")
    args = ap.parse_args()
    if args.post and args.no_state:
        ap.error("--post needs the state file for dedup; drop --no-state")
    if args.post:
        args.classify = True
    verdicts = {}
    if args.verdicts:
        verdicts = json.loads(args.verdicts.read_text(encoding="utf-8"))
        bad = [rid for rid, v in verdicts.items() if v.get("verdict") not in CATEGORY]
        if bad:
            ap.error(f"--verdicts: unknown verdict on {bad[:3]} — one of {sorted(CATEGORY)}")

    now = dt.datetime.now(dt.timezone.utc)
    since = parse_since(args.since, now)
    auth = gcp.get_client()
    result = collect(auth, since, args.version, args.recent_minutes)

    state_file = args.state_dir / f"{result['version']}.json"
    prev = None
    if not args.no_state and state_file.exists():
        try:
            prev = json.loads(state_file.read_text())
        except ValueError:
            prev = None

    if args.classify and result["drops"]["entries"]:
        api_key = os.environ.get("OPENAI_CONVERSATION_API_KEY")
        if not api_key:
            print("warning: --classify needs OPENAI_CONVERSATION_API_KEY; skipping analysis", file=sys.stderr)
        else:
            classify_drops(result["drops"]["entries"], api_key)

    esc = escalations(result, args.max_rate, args.min_turns)
    result["escalations"] = esc

    prompter_state = args.state_dir / PROMPTER_STATE
    retired_seen = {}
    if not args.no_state and prompter_state.exists():
        try:
            retired_seen = json.loads(prompter_state.read_text()).get("retired_seen") or {}
        except ValueError:
            retired_seen = {}
    result["prompters"] = collect_prompters(auth, now)
    result["followups"] = prompter_followups(result["prompters"], retired_seen, iso(now))

    # Text never leaves the process except at the terminal (--show-text) or to SLACK_CHANNEL (--post).
    private = {e["responseId"]: e["analysis"].pop("_private")
               for e in result["drops"]["entries"] if "_private" in (e.get("analysis") or {})}
    # The posted map outlives any single tick: carry it forward so overlapping windows never double-post.
    posted = dict((prev or {}).get("posted") or {})

    def persist(current: dict) -> None:
        """Write the tick's state as it stands, so an abort after a post cannot lose
        the record of it. `result` already carries its escalations, and the text was
        stripped above, so what lands on disk is the same shape as the final write."""
        result["posted"] = current
        args.state_dir.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(result, indent=1))

    post_outcome = None
    if args.post:
        post_outcome = post_drops(result["drops"]["entries"], private, verdicts, posted,
                                  dry_run=args.post_dry_run, persist=None if args.no_state else persist)
    result["posted"] = posted

    if args.json:
        result["post_outcome"] = post_outcome
        print(json.dumps(result, indent=1))
    else:
        print(render(result, prev, esc))
        print(render_prompters(result["prompters"], result["followups"]))
        if post_outcome is not None:
            print(render_post_summary(post_outcome, private, result["drops"]["entries"]))
        if args.show_text and private:
            print("\n# --show-text: kept vs dropped, per response (operator view — paste only into SLACK_CHANNEL via --post)")
            for rid, p in private.items():
                print(f"\n## {OPENAI_LOG_URL.format(rid=rid)}")
                if p.get("user_turn"):
                    print(f"[user] ({len(p['user_turn'])} chars)\n    " + p["user_turn"].replace("\n", "\n    "))
                for i, t in enumerate(p["texts"]):
                    print(f"[{i}] {'KEPT   ' if i == 0 else 'DROPPED'} ({len(t)} chars)\n    " + t.replace("\n", "\n    "))

    if not args.no_state:
        args.state_dir.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(result, indent=1))
        prompter_state.write_text(json.dumps({"retired_seen": retired_seen}, indent=1))

    return 1 if esc else 0


if __name__ == "__main__":
    sys.exit(main())
