#!/usr/bin/env python3
"""Compare conversations between groups — prompt variants, experiment arms, models.

Deterministic half of data:compare-conversations. Every source is first loaded into one
normalized JSONL file (references/conversation-format.md); everything after that works on
that file, whatever the source.

  load experiment        arms of a live IntelligenceService prompter experiment (prod DB)
  load openai-responses  stored OpenAI Responses (e.g. a dataset run against two prompts)
  load qa-transcripts    eng:qa-conversation transcripts.md ("### Arm X" sections)
  load qa-state          eng:qa-prompt state.json files, one or more per group
  load jsonl             an already-normalized file (validated, copied)
  health                 experiment-only: routing, fallbacks, failures, assignments
  metrics                per group x channel: volume, latency, tokens, length, style
  sample                 blinded file for a reviewer: per-conversation, or --paired
  unblind                join reviewer output with the key and aggregate by group
  cleanup                delete every conversation-text file this script wrote

All database access is read-only. Conversation text is written only to the --out-dir
scratch files, which `cleanup` removes.
"""

import argparse
import glob
import json
import os
import random
import re
import statistics
import subprocess
import sys
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

# Identifiers are bound as psql variables, never interpolated; these patterns only reject
# obvious garbage early with a clear message.
SAFE = re.compile(r"^[A-Za-z0-9_.:=@+-]{1,128}$")
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?)?(Z|[+-]\d{2}(:?\d{2})?)?$")
DEFAULT_ENV_FILE = Path.home() / "Development/Aura/Services/IntelligenceService/.env"
CONVERSATIONS = "conversations.jsonl"
SCRATCH_FILES = ("conversations.jsonl", "blind_transcripts.txt", "blind_key.json", "reviewer_scores.json")
OPENERS_YEAH = {"yeah", "yes", "yep", "yea"}
OPENERS_SOFT = {"ah", "ahh", "oh", "mmm", "mm", "hmm"}
STYLE_PATTERNS = {
    "directive": r"\b(you should|you need to|try to|i'd suggest|i would suggest|i recommend|consider)\b",
    "sounds_like": r"\b(that sounds|it sounds|sounds like)\b",
    "we_language": r"\b(we can|we could|let's|together)\b",
    "track_mention": r"\b(track|meditation|listen to|recording)s?\b",
}
STYLE_METRICS = ["open_yeah", "open_ah_mmm", "ends_question", "two_plus_questions", "no_question",
                 *STYLE_PATTERNS]
SCORE_DIMS = ["relational_warmth", "empathy", "non_directive", "opener_variety",
              "question_pattern", "boundaries_natural", "tracks_on_request", "overall_quality"]


def die(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(2)


def check(value, pattern, name):
    if value is not None and not pattern.match(value):
        die(f"invalid {name}: {value!r}")
    return value


def write_jsonl(convs, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / CONVERSATIONS
    with path.open("w", encoding="utf-8") as fh:
        for c in convs:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")
    groups = Counter(c["group"] for c in convs)
    paired = sum(1 for c in convs if c.get("pair_key"))
    print(json.dumps({"file": str(path), "conversations": len(convs), "groups": dict(groups),
                      "with_pair_key": paired}))


def read_jsonl(path):
    path = Path(path)
    if path.is_dir():
        path = path / CONVERSATIONS
    if not path.exists():
        die(f"no conversations file at {path} — run `load` first")
    convs = []
    # JSON permits raw U+2028/U+2029 in strings; splitlines() would treat them as line breaks.
    for n, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
        if not line.strip():
            continue
        c = json.loads(line)
        if not isinstance(c.get("turns"), list) or not c.get("group") or not c.get("id"):
            die(f"{path}:{n} is not a normalized conversation (needs id, group, turns)")
        convs.append(c)
    return convs


# --- Postgres (experiment source) -------------------------------------------------------


def resolve_uri(env_file):
    uri = os.environ.get("IS_DATABASE_URL")
    if uri:
        return uri
    path = Path(env_file) if env_file else DEFAULT_ENV_FILE
    if not path.exists():
        die(f"set IS_DATABASE_URL or pass --env-file (no {path})")
    lines = [l for l in path.read_text(encoding="utf-8").splitlines()
             if l.startswith("POSTGRES_DATABASE_URI=")]
    if not lines:
        die(f"no POSTGRES_DATABASE_URI in {path}")
    # The IS .env carries two: a cluster IP first, the 127.0.0.1 tunnel last. Take the last.
    return lines[-1].split("=", 1)[1].strip().strip('"')


def query(uri, sql, params=None):
    """Run SQL returning one json value per row. `params` are bound as psql variables and
    referenced as :'name'; the SQL goes in on stdin because psql does not interpolate
    variables in a -c command."""
    cmd = ["psql", uri, "-X", "-A", "-t", "-q", "-v", "ON_ERROR_STOP=1"]
    for name, value in (params or {}).items():
        cmd += ["-v", f"{name}={value}"]
    proc = subprocess.run(cmd + ["-f", "-"], input=sql, capture_output=True, text=True,
                          env={**os.environ, "PGCONNECT_TIMEOUT": "10"})
    if proc.returncode != 0:
        err = proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "psql failed"
        if "Connection refused" in proc.stderr:
            err += " — is the prod port-forward (127.0.0.1:5432) up?"
        die(err)
    # JSON permits raw U+2028/U+2029 in strings; splitlines() would treat them as line breaks.
    return [json.loads(l) for l in proc.stdout.split("\n") if l.strip()]


def resolve_since(uri, args):
    """Default the window to the experiment's first stored assignment. dialogue is large and
    only "createdAt" is indexed, so an unbounded scan on the prompt JSONB never finishes."""
    if args.since:
        return
    rows = query(uri, """SELECT json_build_object('first', MIN("createdAt"))
        FROM "userExperiment" WHERE "experimentId" = :'exp'""", {"exp": args.experiment})
    first = rows[0]["first"] if rows else None
    if not first:
        die(f"no userExperiment rows for {args.experiment} — pass --since explicitly")
    args.since = first
    print(f"(window starts at the first assignment: {first})", file=sys.stderr)


def experiment_window(args):
    params = {"exp": args.experiment, "prompter": args.prompter, "since": args.since}
    clauses = ['d."createdAt" >= :\'since\'::timestamptz',
               'd."prompt"->>\'name\' = :\'prompter\'',
               'd."prompt"->\'assignments\' ? :\'exp\'']
    if args.until:
        params["until"] = args.until
        clauses.append('d."createdAt" < :\'until\'::timestamptz')
    for i, uid in enumerate(args.exclude_user or []):
        params[f"ex{i}"] = uid
        clauses.append(f'd."userId" <> :\'ex{i}\'')
    return " AND ".join(clauses), params


# One row per assistant turn. llmCall is usually one row per turn (tool-call turns included,
# tokens combined) but a few turns carry several rows, so aggregate per dialogue over the
# window. The ledger is BigQuery `llm.llm_call` (ENG-1657; Postgres llmCall is being dropped):
# one GROUP BY there, joined to the dialogue rows here on dialogueId.
def llm_by_dialogue(prompter, since):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import bq  # noqa: E402  (bundled copy — canonical at eng:query-bigquery)
    import llm_ledger  # noqa: E402  (bundled copy — canonical at data:analyze-user-conversations)
    sql = f"""SELECT dialogueId, COUNT(*) AS calls, MAX(platform) AS platform, MAX(toolCalls) AS tc,
               MIN(ttftMs) AS ttft, SUM(latencyMs) AS lat, SUM(inputTokens) AS inp, SUM(outputTokens) AS out
        FROM `{llm_ledger.TABLE}`
        WHERE prompterName = @prompter AND createdAt >= TIMESTAMP(@since) AND dialogueId IS NOT NULL
        GROUP BY dialogueId"""
    try:
        rows = bq.query(sql, max_bytes=llm_ledger.MAX_BYTES, params={"prompter": prompter, "since": str(since)})
    except Exception as e:  # noqa: BLE001 -- a missing ledger must not read as "every turn failed"
        die(f"llmCall ledger (BigQuery {llm_ledger.TABLE}) read failed: {type(e).__name__}: {e}")
    return {r["dialogueId"]: r for r in rows}


def experiment_turns(uri, args):
    resolve_since(uri, args)
    where, params = experiment_window(args)
    turns = query(uri, f"""
    SELECT json_build_object(
      'id', d."id", 'uid', d."userId", 'cid', d."conversationId", 'at', d."createdAt",
      'arm', d."prompt"->'assignments'->>:'exp', 'filename', d."prompt"->>'filename',
      'version', d."prompt"->>'version', 'rating', d."rating", 'msg', d."message")
    FROM dialogue d
    WHERE d."role" = 'assistant' AND {where}""", params)
    llm = llm_by_dialogue(args.prompter, args.since)
    for t in turns:
        l = llm.get(str(t["id"]))
        t.update({"llm": l is not None, "calls": l and l["calls"], "platform": l and l["platform"],
                  "tc": (l and l["tc"]) or 0, "ttft": l and l["ttft"], "lat": l and l["lat"],
                  "inp": l and l["inp"], "out": l and l["out"]})
    return turns


def channel_of(platform):
    return {"livekit": "voice", "mobile": "chat"}.get(platform, platform)


def load_experiment(args):
    uri = resolve_uri(args.env_file)
    rows = experiment_turns(uri, args)
    if not rows:
        die("no assistant turns carry this experiment in the window — wrong prompter/experiment, or not deployed yet")
    cids = sorted({r["cid"] for r in rows})
    names = {f"c{i}": cid for i, cid in enumerate(cids)}
    users = []
    # Bounded IN lists keep each statement small on large windows.
    keys = list(names)
    for i in range(0, len(keys), 500):
        chunk = keys[i:i + 500]
        placeholders = ", ".join(f":'{k}'" for k in chunk)
        bound = {"since": args.since, **{k: names[k] for k in chunk}}
        until = ""
        if args.until:
            bound["until"] = args.until
            until = 'AND "createdAt" < :\'until\'::timestamptz'
        users += query(uri, f"""SELECT json_build_object('cid', "conversationId", 'at', "createdAt", 'msg', "message")
            FROM dialogue WHERE "role" = 'user' AND "createdAt" >= :'since'::timestamptz {until}
            AND "conversationId" IN ({placeholders})""", bound)
    by_cid = defaultdict(list)
    for r in rows:
        by_cid[r["cid"]].append(("assistant", r))
    for u in users:
        by_cid[u["cid"]].append(("user", u))
    convs = []
    for cid, items in by_cid.items():
        items.sort(key=lambda it: it[1]["at"])
        assistant = [it[1] for it in items if it[0] == "assistant"]
        if not assistant:
            continue
        arms = Counter(a["arm"] for a in assistant)
        channel = Counter(channel_of(a["platform"]) for a in assistant if a["platform"]).most_common(1)
        turns = []
        for role, r in items:
            t = {"role": role, "text": r.get("msg") or ""}
            if role == "assistant":
                t.update({k: v for k, v in {
                    "ttft_ms": r["ttft"], "latency_ms": r["lat"], "input_tokens": r["inp"],
                    "output_tokens": r["out"], "tool_calls": r["tc"], "llm_calls": r["calls"],
                    "rating": r["rating"]}.items() if v is not None})
            turns.append(t)
        # An empty arm means ExperimentService failed for that user; keep the turns visible
        # as their own group rather than dropping them or breaking the file.
        convs.append({"id": cid, "group": arms.most_common(1)[0][0] or "<unassigned>",
                      "channel": channel[0][0] if channel else None, "user": assistant[0]["uid"],
                      "turns": turns, "meta": {"prompt_files": sorted({a["filename"] for a in assistant}),
                                               "versions": sorted({a["version"] for a in assistant if a["version"]})}})
    write_jsonl(convs, args.out_dir)


def cmd_health(args):
    uri = resolve_uri(args.env_file)
    assign = query(uri, """SELECT json_build_object('arm', arm, 'users', users) FROM (
        SELECT COALESCE(NULLIF("value", ''), '<empty>') AS arm, COUNT(*) AS users
        FROM "userExperiment" WHERE "experimentId" = :'exp' GROUP BY 1) t ORDER BY arm""",
                   {"exp": args.experiment})
    print(f"## Assignments — `{args.experiment}` (userExperiment)\n")
    table(["Arm", "Users"], [(a["arm"], a["users"]) for a in assign])
    if any(a["arm"] == "<empty>" for a in assign):
        print("FLAG: empty assignments — ExperimentService calls failed for these users; they are pinned to the base prompt.\n")
    rows = experiment_turns(uri, args)
    if not rows:
        die("no assistant turns carry this experiment in the window")
    by_arm = defaultdict(list)
    for r in rows:
        by_arm[r["arm"]].append(r)
    out = []
    for arm in sorted(by_arm, key=str):
        rs = by_arm[arm]
        files = Counter(r["filename"] for r in rs)
        multi = [r for r in rs if (r["calls"] or 0) > 1]
        out.append((arm, len({r["uid"] for r in rs}), len({r["cid"] for r in rs}), len(rs),
                    ", ".join(f"`{f}` ×{n}" for f, n in files.most_common()),
                    sum(not (r["msg"] or "").strip() for r in rs), sum(not r["llm"] for r in rs),
                    f"{len(multi)} ({len({r['uid'] for r in multi})} users)"))
    print("## Routing and failures by arm\n")
    table(["Arm", "Users", "Convs", "Turns", "Prompt files served", "Empty replies",
           "No llmCall row", "Several llmCall rows"], out)
    print("Each arm should serve exactly one prompt file; a treatment arm serving the base file "
          "fell back because its variant file was missing.\n")


# --- OpenAI Responses source ------------------------------------------------------------


def openai_get(path, key):
    req = urllib.request.Request(f"https://api.openai.com/v1/{path}",
                                 headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        die(f"OpenAI {e.code} on {path.split('?')[0]}: {body}")


def content_text(content):
    if isinstance(content, str):
        return content
    parts = []
    for c in content or []:
        if isinstance(c, dict):
            parts.append(c.get("text") or c.get("output_text") or "")
    return "".join(parts)


def load_openai_responses(args):
    key = os.environ.get(args.api_key_env)
    if not key:
        die(f"${args.api_key_env} is not set — use the key of the project that stored the responses")
    convs = []
    for spec in args.group:
        if "=" not in spec:
            die(f"--group expects NAME=FILE, got {spec!r}")
        group, path = spec.split("=", 1)
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            parts = line.strip().split("\t")
            if not parts or not parts[0] or parts[0].startswith("#"):
                continue
            rid, pair_key = parts[0], (parts[1] if len(parts) > 1 else None)
            resp = openai_get(f"responses/{rid}", key)
            items, after = [], None
            while True:
                page = openai_get(f"responses/{rid}/input_items?limit=100&order=asc"
                                  + (f"&after={after}" if after else ""), key)
                items += page.get("data", [])
                if not page.get("has_more") or not page.get("last_id"):
                    break
                after = page["last_id"]
            # Prior assistant turns in the input are replayed history, not this variant's
            # output: keep them for the reviewer, exclude them from metrics.
            turns = [{"role": i["role"], "text": content_text(i.get("content")),
                      **({"context": True} if i["role"] == "assistant" else {})}
                     for i in items if i.get("type", "message") == "message" and i.get("role") in ("user", "assistant")]
            reply = "".join(content_text(o.get("content")) for o in resp.get("output", [])
                            if o.get("type") == "message")
            usage = resp.get("usage") or {}
            tool_calls = sum(1 for o in resp.get("output", []) if o.get("type") == "function_call")
            turns.append({"role": "assistant", "text": reply, "input_tokens": usage.get("input_tokens"),
                          "output_tokens": usage.get("output_tokens"), "tool_calls": tool_calls})
            convs.append({"id": rid, "group": group, "pair_key": pair_key, "channel": args.channel,
                          "turns": turns, "meta": {"model": resp.get("model")}})
    write_jsonl(convs, args.out_dir)


# --- QA skill outputs ---------------------------------------------------------------------


def load_qa_transcripts(args):
    """eng:qa-conversation transcripts.md: '## N. scenario', '### Arm X', '**User:**' / '**Aurie:**'."""
    convs, scen, cur = {}, None, None
    for ln in Path(args.file).read_text(encoding="utf-8").split("\n"):
        m = re.match(r"^## (\d+)\.\s", ln)
        if m:
            scen, cur = m.group(1), None
            continue
        m = re.match(r"^### Arm ([A-Za-z0-9_-]+)\b", ln)
        if m:
            cur = convs.setdefault((scen, m.group(1)), [])
            continue
        m = re.match(r"^\*\*(User|Aurie|Assistant):\*\*\s*(.*)$", ln)
        if m:
            if cur is None:
                die(f"turn before any '### Arm' header in scenario {scen}")
            cur.append({"role": "user" if m.group(1) == "User" else "assistant", "text": m.group(2).strip()})
            continue
        if cur and cur and ln.strip() and not ln.startswith(("#", "*", "---", "|")):
            cur[-1]["text"] += "\n" + ln.strip()
    out = [{"id": f"s{s}-{a}", "group": a, "pair_key": f"s{s}", "channel": args.channel, "turns": t}
           for (s, a), t in convs.items() if t]
    write_jsonl(out, args.out_dir)


def load_qa_state(args):
    """eng:qa-prompt state.json (OpenAI-shaped `messages`). One conversation per file; the
    pairing key is the file's run directory unless --pair-by-name is set."""
    convs = []
    for spec in args.group:
        if "=" not in spec:
            die(f"--group expects NAME=GLOB, got {spec!r}")
        group, pattern = spec.split("=", 1)
        files = sorted(glob.glob(pattern, recursive=True))
        if not files:
            die(f"no files match {pattern}")
        for f in files:
            state = json.loads(Path(f).read_text(encoding="utf-8"))
            turns = [{"role": m["role"], "text": content_text(m.get("content"))}
                     for m in state.get("messages", []) if m.get("role") in ("user", "assistant")
                     and content_text(m.get("content")).strip()]
            p = Path(f)
            pair = p.stem if args.pair_by_name else str(p.parent.parent)
            convs.append({"id": f"{group}:{p}", "group": group, "pair_key": pair,
                          "channel": args.channel, "turns": turns, "meta": {"model": state.get("model")}})
    write_jsonl(convs, args.out_dir)


def load_jsonl(args):
    write_jsonl(read_jsonl(args.file), args.out_dir)


# --- Metrics ------------------------------------------------------------------------------


def pct(values, q):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    k = (len(values) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return round(values[lo] + (values[hi] - values[lo]) * (k - lo))


def pair(a, b):
    return None if a is None and b is None else f"{a} / {b}"


def mean(values):
    values = [v for v in values if isinstance(v, (int, float))]
    return round(statistics.mean(values)) if values else None


def table(headers, rows):
    print("| " + " | ".join(headers) + " |")
    print("|" + "|".join("---" for _ in headers) + "|")
    for r in rows:
        print("| " + " | ".join("—" if v is None else str(v) for v in r) + " |")
    print()


def first_word(text):
    m = re.match(r"\s*([A-Za-z']+)", text or "")
    return m.group(1).lower() if m else ""


def style_flags(text):
    low, q = text.lower(), text.count("?")
    fw = first_word(text)
    flags = {"open_yeah": fw in OPENERS_YEAH, "open_ah_mmm": fw in OPENERS_SOFT,
             "ends_question": text.rstrip().endswith("?"), "two_plus_questions": q >= 2,
             "no_question": q == 0}
    flags.update({k: bool(re.search(p, low)) for k, p in STYLE_PATTERNS.items()})
    return flags


def cmd_metrics(args):
    convs = read_jsonl(args.input)
    cells = defaultdict(lambda: {"convs": 0, "turns": []})
    for c in convs:
        cell = cells[(c.get("channel") or "all", c["group"])]
        cell["convs"] += 1
        cell["turns"] += [t for t in c["turns"] if t["role"] == "assistant" and not t.get("context")]
    keys = sorted(cells, key=lambda k: (str(k[0]), str(k[1])))

    print("## Volume, latency, tokens and length by group × channel\n")
    out = []
    for key in keys:
        ts = cells[key]["turns"]
        plain = [t for t in ts if not t.get("tool_calls")]
        tool = [t for t in ts if t.get("tool_calls")]
        has_tools = any("tool_calls" in t for t in ts)
        out.append((key[0], key[1], cells[key]["convs"], len(ts),
                    round(len(ts) / cells[key]["convs"], 1) if cells[key]["convs"] else None,
                    sum(not t["text"].strip() for t in ts),
                    pair(pct([t.get('ttft_ms') for t in plain], .5), pct([t.get('ttft_ms') for t in plain], .9)),
                    f"{100 * len(tool) / len(ts):.1f}%" if ts and has_tools else None,
                    mean(t.get("input_tokens") for t in ts),
                    mean(t.get("output_tokens") for t in plain),
                    mean(len(t["text"]) for t in plain)))
    table(["Channel", "Group", "Convs", "Replies", "Replies/conv", "Empty", "TTFT p50/p90 no-tool (ms)",
           "Tool-call replies", "Avg input tok", "Avg output tok (no-tool)", "Avg reply chars (no-tool)"], out)
    print("Columns a source does not carry show —. Tool-call turns are two model calls (see "
          "references/interpreting.md); compare groups on the no-tool columns.\n")

    print("## Reply style by group × channel (share of replies)\n")
    out = []
    for key in keys:
        ts = [t for t in cells[key]["turns"] if t["text"].strip()]
        counts = Counter()
        for t in ts:
            for k, v in style_flags(t["text"]).items():
                counts[k] += int(v)
        out.append((key[0], key[1], len(ts), *[f"{100 * counts[m] / len(ts):.1f}%" if ts else None
                                                for m in STYLE_METRICS]))
    table(["Channel", "Group", "Replies", *STYLE_METRICS], out)
    ratings = Counter((c["group"], t["rating"]) for c in convs for t in c["turns"] if t.get("rating") is not None)
    if ratings:
        print("## Ratings on replies\n")
        table(["Group", "Rating", "Count"], [(g, v, n) for (g, v), n in sorted(ratings.items(), key=str)])


# --- Blinded review -----------------------------------------------------------------------


def render(conv):
    return [("USER" if t["role"] == "user" else "ASSISTANT") + ": " + " ".join(t["text"].split())
            for t in conv["turns"]]


def cmd_sample(args):
    convs = read_jsonl(args.input)
    rng = random.Random(args.seed)
    groups = args.groups.split(",") if args.groups else sorted({c["group"] for c in convs})
    lines, key = [], {}

    if args.paired:
        if len(groups) != 2:
            die("--paired compares exactly two groups; pass --groups A,B")
        by_pair = defaultdict(dict)
        for c in convs:
            if c.get("pair_key") and c["group"] in groups:
                by_pair[c["pair_key"]].setdefault(c["group"], c)
        pairs = [p for p, g in by_pair.items() if len(g) == 2]
        if not pairs:
            die("no pair_key is shared by both groups — this source is not paired; drop --paired")
        for i, p in enumerate(rng.sample(sorted(pairs), min(args.pairs, len(pairs)))):
            order = groups[:]
            rng.shuffle(order)
            label = f"P{i + 1}"
            key[label] = {"pair_key": p, "1": order[0], "2": order[1]}
            lines.append(f"\n########## {label} ##########")
            for slot, g in (("1", order[0]), ("2", order[1])):
                lines.append(f"--- Variant {slot} ---")
                lines += render(by_pair[p][g])
        mode = "paired"
    else:
        picks = []
        for g in groups:
            for ch in sorted({c.get("channel") or "all" for c in convs}):
                pool = [c for c in convs if c["group"] == g and (c.get("channel") or "all") == ch
                        and args.min_turns <= sum(t["role"] == "assistant" and not t.get("context") for t in c["turns"])
                <= args.max_turns]
                n = min(args.per_cell, len(pool))
                if n < args.per_cell:
                    print(f"note: only {n} eligible {ch} conversations in group {g}", file=sys.stderr)
                picks += rng.sample(pool, n)
        if not picks:
            die("no eligible conversations — widen --min-turns/--max-turns")
        rng.shuffle(picks)
        for i, c in enumerate(picks):
            label = f"X{i + 1}"
            key[label] = {"group": c["group"], "channel": c.get("channel") or "all", "id": c["id"]}
            lines.append(f"\n########## {label} ({c.get('channel') or 'conversation'}) ##########")
            lines += render(c)
        mode = "single"
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "blind_transcripts.txt").write_text("\n".join(lines), encoding="utf-8")
    (out_dir / "blind_key.json").write_text(json.dumps({"mode": mode, "items": key}), encoding="utf-8")
    print(json.dumps({"mode": mode, "transcripts": str(out_dir / "blind_transcripts.txt"),
                      "key": str(out_dir / "blind_key.json"), "items": len(key),
                      "chars": sum(len(l) for l in lines)}))


def parse_reviewer(path, pattern):
    raw = Path(path).read_text(encoding="utf-8")
    parsed = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
    # Keep only item labels; a reviewer may add keys such as "clusters" or "notes".
    return {k: v for k, v in parsed.items() if re.fullmatch(pattern, k) and isinstance(v, dict)}


def cmd_unblind(args):
    blob = json.loads(Path(args.key).read_text(encoding="utf-8"))
    mode, key = blob["mode"], blob["items"]
    if mode == "paired":
        scores = parse_reviewer(args.scores, r"P\d+")
        groups = sorted({key[p]["1"] for p in key} | {key[p]["2"] for p in key})
        dims = sorted({d for s in scores.values() for d in (s.get("dimensions") or {})})
        tallies = defaultdict(Counter)
        for label, s in scores.items():
            if label not in key:
                continue
            for dim, pick in [("overall", s.get("preferred"))] + list((s.get("dimensions") or {}).items()):
                winner = key[label].get(str(pick)) if str(pick) in ("1", "2") else "tie"
                tallies[dim][winner] += 1
        print("## Blinded paired preference — wins per group\n")
        table(["Dimension", *groups, "tie", "n"],
              [(d, *[tallies[d][g] for g in groups], tallies[d]["tie"], sum(tallies[d].values()))
               for d in ["overall", *dims] if tallies[d]])
        print("## Per pair (unblinded)\n")
        table(["Pair", "Preferred", "Reason"],
              [(l, key[l].get(str(s.get("preferred")), "tie"), s.get("reason", ""))
               for l, s in sorted(scores.items(), key=lambda kv: int(kv[0][1:])) if l in key])
        return
    scores = parse_reviewer(args.scores, r"X\d+")
    missing = sorted(set(key) - set(scores), key=lambda x: int(x[1:]))
    if missing:
        print(f"warning: no scores for {', '.join(missing)}", file=sys.stderr)
    cells = defaultdict(list)
    for label, s in scores.items():
        if label in key:
            cells[(key[label]["group"], "all")].append(s)
            cells[(key[label]["group"], key[label]["channel"])].append(s)

    def avg(vals):
        nums = [v for v in vals if isinstance(v, (int, float))]
        return f"{statistics.mean(nums):.2f}" if nums else "n/a"

    out = []
    for cell in sorted(cells, key=lambda c: (str(c[0]), c[1] != "all", c[1])):
        ss = cells[cell]
        turns = sum(s.get("assistant_turns", s.get("aurie_turns", 0)) for s in ss) or 1
        out.append((cell[0], cell[1], len(ss), *[avg([s["scores"].get(d) for s in ss]) for d in SCORE_DIMS],
                    f"{100 * sum(s.get('interjection_openers', 0) for s in ss) / turns:.0f}%",
                    f"{100 * sum(s.get('ends_with_question', 0) for s in ss) / turns:.0f}%"))
    print("## Blinded review — mean scores (1–5) by group\n")
    table(["Group", "Channel", "n", *SCORE_DIMS, "Interjection openers", "Ends with ?"], out)
    print("## Per conversation (unblinded)\n")
    table(["Label", "Group", "Channel", "Overall", "Strength", "Weakness"],
          [(l, key[l]["group"], key[l]["channel"], s["scores"].get("overall_quality"),
            s.get("strength", ""), s.get("weakness", ""))
           for l, s in sorted(scores.items(), key=lambda kv: int(kv[0][1:])) if l in key])


def cmd_cleanup(args):
    out_dir = Path(args.out_dir)
    removed = []
    for name in SCRATCH_FILES:
        p = out_dir / name
        if p.exists():
            p.unlink()
            removed.append(name)
    for p in out_dir.glob("chunk_*.txt"):
        p.unlink()
        removed.append(p.name)
    print(json.dumps({"removed": removed}))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def experiment_flags(sp):
        sp.add_argument("--experiment", required=True, help="experiment name, e.g. coachPrompterWarmth")
        sp.add_argument("--prompter", default="coach-prompter", help="dialogue.prompt->>'name'")
        sp.add_argument("--since", help="ISO timestamp (default: the experiment's first assignment)")
        sp.add_argument("--until", help="ISO timestamp, exclusive")
        sp.add_argument("--exclude-user", action="append", help="userId to exclude (repeatable)")
        sp.add_argument("--env-file", help="IS .env holding POSTGRES_DATABASE_URI (else IS_DATABASE_URL)")

    load = sub.add_parser("load").add_subparsers(dest="source", required=True)
    sp = load.add_parser("experiment")
    experiment_flags(sp)
    sp.add_argument("--out-dir", required=True)
    sp.set_defaults(fn=load_experiment)
    sp = load.add_parser("openai-responses")
    sp.add_argument("--group", action="append", required=True,
                    help="NAME=FILE of response ids, one per line, optional TAB pair_key (repeatable)")
    sp.add_argument("--api-key-env", default="OPENAI_API_KEY", help="env var holding the project's key")
    sp.add_argument("--channel", help="channel label for every conversation (e.g. chat)")
    sp.add_argument("--out-dir", required=True)
    sp.set_defaults(fn=load_openai_responses)
    sp = load.add_parser("qa-transcripts")
    sp.add_argument("--file", required=True)
    sp.add_argument("--channel")
    sp.add_argument("--out-dir", required=True)
    sp.set_defaults(fn=load_qa_transcripts)
    sp = load.add_parser("qa-state")
    sp.add_argument("--group", action="append", required=True, help="NAME=GLOB of state.json files (repeatable)")
    sp.add_argument("--pair-by-name", action="store_true", help="pair files by file stem instead of run directory")
    sp.add_argument("--channel")
    sp.add_argument("--out-dir", required=True)
    sp.set_defaults(fn=load_qa_state)
    sp = load.add_parser("jsonl")
    sp.add_argument("--file", required=True)
    sp.add_argument("--out-dir", required=True)
    sp.set_defaults(fn=load_jsonl)

    sp = sub.add_parser("health")
    experiment_flags(sp)
    sp.set_defaults(fn=cmd_health)
    sp = sub.add_parser("metrics")
    sp.add_argument("--input", required=True, help="conversations.jsonl, or the --out-dir holding it")
    sp.set_defaults(fn=cmd_metrics)
    sp = sub.add_parser("sample")
    sp.add_argument("--input", required=True)
    sp.add_argument("--out-dir", required=True)
    sp.add_argument("--groups", help="comma-separated groups to include (default all)")
    sp.add_argument("--paired", action="store_true", help="side-by-side pairs sharing a pair_key")
    sp.add_argument("--pairs", type=int, default=20, help="pairs to sample in --paired mode")
    sp.add_argument("--per-cell", type=int, default=6, help="conversations per group x channel")
    sp.add_argument("--min-turns", type=int, default=1)
    sp.add_argument("--max-turns", type=int, default=20)
    sp.add_argument("--seed", type=int, default=2026)
    sp.set_defaults(fn=cmd_sample)
    sp = sub.add_parser("unblind")
    sp.add_argument("--key", required=True)
    sp.add_argument("--scores", required=True, help="file holding the reviewer's JSON output")
    sp.set_defaults(fn=cmd_unblind)
    sp = sub.add_parser("cleanup")
    sp.add_argument("--out-dir", required=True)
    sp.set_defaults(fn=cmd_cleanup)
    args = p.parse_args()

    if getattr(args, "experiment", None) is not None:
        check(args.experiment, SAFE, "experiment")
        check(args.prompter, SAFE, "prompter")
        check(args.since, ISO, "since")
        check(args.until, ISO, "until")
        for uid in args.exclude_user or []:
            check(uid, SAFE, "exclude-user")
    args.fn(args)


if __name__ == "__main__":
    main()
