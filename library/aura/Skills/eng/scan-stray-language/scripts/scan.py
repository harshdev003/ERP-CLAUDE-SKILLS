#!/usr/bin/env python3
"""Find Aurie replies containing a non-Latin script the conversation never used.

  scan.py [--days N | --since YYYY-MM-DD [--until YYYY-MM-DD]] [--model M] [--prompter P] [--json PATH]

Reads Braintrust (Conversation project) LLM spans named `openaiStreamResponse` (the inner
spans: on a guardrails handoff the outer streamResponse span is labelled coach-prompter).
A reply is flagged when it contains a word in Arabic, Hebrew, Cyrillic, CJK, Hangul,
Devanagari, Thai or Greek script that is a STRAY:
  - the script appears nowhere in the span's input (user messages, context and prompt), so a
    reply to a user writing in it, or quoting the user's own words, never flags; and
  - the use is isolated: at most MAX_STRAY_WORDS words of that script, outside quotes, brackets
    and bold, with no language name nearby ("the Greek word ...").
The second test exists because a span's input does not carry the whole conversation: a reply the
user asked for in Arabic, a Persian song title or "the Greek word for faith" all fail the first
test alone. Those are counted as `deliberate`, never silently dropped.

Prints a table per (model, prompter): replies scanned (no script filter), flagged, rate; then each
flagged reply with a short excerpt and its Braintrust trace link. No user ids, emails or
conversation text beyond the excerpt. Read-only. Needs BRAINTRUST_API_KEY.
"""

import argparse
import collections
import datetime as dt
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BT_API = "https://api.braintrust.dev"
PROJECT_ID = "3bee3818-a9b4-40c4-ac14-9e274c52c560"  # Conversation
ORG_NAME = "Aura Health 1"
SPAN_FILTER = "span_attributes.type = 'llm' AND span_attributes.name = 'openaiStreamResponse'"
PAGE = 1000  # BTQL caps limit at 1000
MIN_SLICE = dt.timedelta(minutes=5)
EXCERPT = 40
MAX_STRAY_WORDS = 2
# A quoted, bracketed or bold run on one line. Straight single quotes are left out: they are
# apostrophes far more often than quotes.
REGION = re.compile(r'“[^”\n]{1,200}”|"[^"\n]{1,200}"|«[^»\n]{1,200}»|„[^“”\n]{1,200}[“”]|‘[^’\n]{1,200}’'
                    r"|\([^)\n]{1,200}\)|\[[^\]\n]{1,200}\]|\*\*[^*\n]{1,200}\*\*")
LANGUAGE_NAMES = re.compile(
    r"\b(arabic|persian|farsi|urdu|hebrew|yiddish|russian|ukrainian|cyrillic|chinese|mandarin|cantonese|"
    r"japanese|korean|hindi|sanskrit|marathi|nepali|thai|greek)\b", re.I)

# One character class per script. Kana sit with CJK: a stray Japanese word is the same defect.
SCRIPTS = {
    "Arabic": "؀-ۿݐ-ݿ",
    "Hebrew": "֐-׿",
    "Cyrillic": "Ѐ-ӿ",
    "CJK": "぀-ヿ一-鿿",
    "Hangul": "ᄀ-ᇿ가-힯",
    "Devanagari": "ऀ-ॿ",
    "Thai": "฀-๿",
    "Greek": "Ͱ-Ͽ",
}
WORD = {name: re.compile(f"[{chars}]+") for name, chars in SCRIPTS.items()}
# BTQL pre-filter: a few frequent letters per script. Recall over the scripts' common words is
# what matters; the exact check runs locally on every candidate.
PROBES = {
    "Arabic": "الملنيت", "Hebrew": "יוהלא", "Cyrillic": "оаеинт", "CJK": "的是一不我你の",
    "Hangul": "이다는가", "Devanagari": "ािनकर", "Thai": "านอร", "Greek": "αοεικ",
}


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


# MARK: - Detection

def text_of(value) -> str:
    """Every string in a span's input or output, joined. Structured outputs (a JSON object such as
    {"affirmation": ...}) hide their text under arbitrary keys, so no key is skipped."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(text_of(v) for v in value)
    if isinstance(value, dict):
        return "\n".join(text_of(v) for v in value.values())
    return str(value)


def _sheltered(text: str, start: int, end: int, rx: re.Pattern) -> bool:
    """A deliberate use: next to a language name, or inside a quoted, bracketed or bold run that is
    mostly this script (a quoted title or phrase). A foreign word inside an English quote is not
    sheltered: that is the stray, even when the whole sentence sits in a JSON string."""
    if LANGUAGE_NAMES.search(text[max(0, start - 60):start] + " " + text[end:end + 60]):
        return True
    for m in REGION.finditer(text):
        if m.start() < start and end < m.end():
            inner = m.group()
            letters = sum(c.isalpha() for c in inner)
            return letters > 0 and sum(len(w) for w in rx.findall(inner)) / letters >= 0.5
    return False


def classify(output: str, input_text: str) -> tuple[list[tuple[str, str]], list[str]]:
    """(stray (script, word) pairs, scripts used deliberately) for one reply.

    Only scripts absent from the input are considered. A script with more than MAX_STRAY_WORDS
    words, or whose every occurrence is sheltered, is deliberate.
    """
    strays, deliberate = [], []
    for name, rx in WORD.items():
        hits = list(rx.finditer(output or ""))
        if not hits or rx.search(input_text or ""):
            continue
        loose = [m.group() for m in hits if not _sheltered(output, m.start(), m.end(), rx)]
        if len(hits) > MAX_STRAY_WORDS or not loose:
            deliberate.append(name)
        else:
            strays.extend((name, w) for w in loose)
    return strays, deliberate


def excerpt(text: str, word: str) -> str:
    i = text.find(word)
    s = text[max(0, i - EXCERPT): i + len(word) + EXCERPT]
    return re.sub(r"\s+", " ", s).strip()


def trace_link(root_span_id: str) -> str:
    return (f"https://www.braintrust.dev/app/{urllib.parse.quote(ORG_NAME)}/p/Conversation/logs"
            f"?r={root_span_id}")


# MARK: - Braintrust

def btql(query: str) -> list[dict]:
    key = os.environ.get("BRAINTRUST_API_KEY")
    if not key:
        raise RuntimeError("BRAINTRUST_API_KEY is not set")
    req = urllib.request.Request(f"{BT_API}/btql", data=json.dumps({"query": query, "fmt": "json"}).encode(),
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                          "User-Agent": user_agent()})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.loads(r.read().decode("utf-8")).get("data", [])
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 4:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"Braintrust {e.code}: {e.read().decode('utf-8', 'replace')[:300]}") from e
    return []


def _q(s: str) -> str:
    return s.replace("'", "''")


def _where(a: dt.datetime, b: dt.datetime, model: str | None, prompter: str | None) -> str:
    w = f"{SPAN_FILTER} AND created >= '{a.isoformat()}Z' AND created < '{b.isoformat()}Z'"
    if model:
        w += f" AND metadata.model = '{_q(model)}'"
    if prompter:
        w += f" AND metadata.prompterName = '{_q(prompter)}'"
    return w


def denominators(a, b, model, prompter) -> dict:
    rows = btql(f"dimensions: metadata.model as model, metadata.prompterName as prompter\n"
                f"measures: count(1) as n\nfrom: project_logs('{PROJECT_ID}') spans\n"
                f"filter: {_where(a, b, model, prompter)}")
    return {(r.get("model") or "?", r.get("prompter") or "?"): r["n"] for r in rows}


def candidates(a, b, model, prompter) -> list[dict]:
    """Spans whose output holds a probe letter, paged by halving any slice that fills a page."""
    probe = " OR ".join(f"output ILIKE '%{c}%'" for letters in PROBES.values() for c in letters)
    out, stack = [], [(a, b)]
    while stack:
        lo, hi = stack.pop()
        rows = btql(f"select: id, root_span_id, created, metadata.model as model, "
                    f"metadata.prompterName as prompter, input, output\n"
                    f"from: project_logs('{PROJECT_ID}') spans\n"
                    f"filter: {_where(lo, hi, model, prompter)} AND ({probe})\nlimit: {PAGE}")
        if len(rows) >= PAGE:
            if hi - lo <= MIN_SLICE:
                raise RuntimeError(f"{len(rows)} candidates in {lo}..{hi}; can't page further")
            mid = lo + (hi - lo) / 2
            stack += [(lo, mid), (mid, hi)]
            continue
        out.extend(rows)
    return out


# MARK: - Report

def scan(a, b, model=None, prompter=None) -> dict:
    denom = denominators(a, b, model, prompter)
    flagged, seen, deliberate = [], set(), collections.Counter()
    for r in candidates(a, b, model, prompter):
        if r["id"] in seen:
            continue
        seen.add(r["id"])
        output = text_of(r.get("output"))
        hits, kept = classify(output, text_of(r.get("input")))
        for name in kept:
            deliberate[(r.get("model") or "?", r.get("prompter") or "?")] += 1
        if hits:
            script, word = hits[0]
            flagged.append({"date": (r.get("created") or "")[:10], "model": r.get("model") or "?",
                            "prompter": r.get("prompter") or "?", "script": script,
                            "words": sorted({w for _, w in hits}), "excerpt": excerpt(output, word),
                            "trace": r.get("root_span_id"), "link": trace_link(r.get("root_span_id") or "")})
    per = collections.Counter((f["model"], f["prompter"]) for f in flagged)
    table = [{"model": m, "prompter": p, "scanned": n, "flagged": per.get((m, p), 0),
              "rate": per.get((m, p), 0) / n if n else 0.0, "deliberate": deliberate.get((m, p), 0)}
             for (m, p), n in sorted(denom.items(), key=lambda kv: -kv[1])]
    return {"window": {"from": f"{a.isoformat()}Z", "to": f"{b.isoformat()}Z"},
            "candidates": len(seen), "table": table, "flagged": sorted(flagged, key=lambda f: f["date"])}


def render(rep: dict) -> str:
    w = rep["window"]
    lines = [f"# Stray-script replies, {w['from'][:10]} to {w['to'][:10]} (Braintrust Conversation, "
             f"{rep['candidates']:,} non-Latin candidates checked)", "",
             "| Model | Prompter | Replies scanned | Flagged | Rate | Deliberate (not flagged) |",
             "|---|---|---:|---:|---:|---:|"]
    for t in rep["table"]:
        lines.append(f"| {t['model']} | {t['prompter']} | {t['scanned']:,} | {t['flagged']} | "
                     f"{100 * t['rate']:.3f}% | {t['deliberate']} |")
    lines += ["", f"## Flagged replies ({len(rep['flagged'])})"]
    for f in rep["flagged"]:
        lines.append(f"- {f['date']} · {f['model']} · {f['prompter']} · {f['script']} {', '.join(f['words'])}: "
                     f"\"…{f['excerpt']}…\" [{f['trace']}]({f['link']})")
    if not rep["flagged"]:
        lines.append("- none")
    return "\n".join(lines)


def _day(s: str) -> dt.datetime:
    return dt.datetime.strptime(s, "%Y-%m-%d")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--days", type=int, default=7, help="last N days (default 7)")
    ap.add_argument("--since", help="window start, YYYY-MM-DD UTC (overrides --days)")
    ap.add_argument("--until", help="window end, YYYY-MM-DD UTC, exclusive (default now)")
    ap.add_argument("--model")
    ap.add_argument("--prompter")
    ap.add_argument("--json", dest="json_out")
    a = ap.parse_args()
    if "BRAINTRUST_API_KEY" not in os.environ:
        try:
            os.environ["BRAINTRUST_API_KEY"] = json.loads(
                (pathlib.Path.home() / ".claude/settings.json").read_text(encoding="utf-8"))["env"]["BRAINTRUST_API_KEY"]
        except (OSError, ValueError, KeyError):
            pass
    end = _day(a.until) if a.until else dt.datetime.now(dt.UTC).replace(tzinfo=None, microsecond=0)
    start = _day(a.since) if a.since else end - dt.timedelta(days=a.days)
    try:
        rep = scan(start, end, a.model, a.prompter)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if a.json_out:
        pathlib.Path(a.json_out).write_text(json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")
    print(render(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
