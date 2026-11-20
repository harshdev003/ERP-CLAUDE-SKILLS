#!/usr/bin/env python3
"""What the propose leg reads before it drafts a change.

Four things, per the loop's process: the prompt's architecture, its deployed
configuration, its change log, and what has already been tried. The first three
say what the thing IS. The fourth is the one that saves the week.

  brief <prompter>     all four, for one prompter
  process              the nine-stage process, mapped onto the loop's legs
  register             what is settled, and what each entry refuses
  check <text>         refuse a candidate that restates a settled question
  config-drift         the recorded configuration against the live src/config.js

The architecture map is read from the live prompt file rather than stored here.
The prompts run to 1500 lines and this loop changes them weekly; a section list
copied into a reference file would be wrong within a fortnight and would be
trusted anyway. The configuration snapshot IS stored, because its whole value is
the comparison - "a behaviour change with no prompt change is a model change
until proven otherwise" is only answerable against a previously recorded value.

Self-contained per AINative principle 1: the YAML reader is a per-skill copy,
and so is `references/register.yaml` wherever another skill enforces it.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
REGISTER = HERE.parent / "references" / "register.yaml"
PROMPTERS = HERE.parent / "references" / "prompters.yaml"
PROCESS = HERE.parent / "references" / "process_map.yaml"

DEFAULT_IS_REPO = pathlib.Path.home() / "Development/Aura/Services/IntelligenceService"

# Verdicts that stop a candidate dead, versus ones that send it back for a rewrite
# or demand an answer first. Keeping them apart matters: a hard stop is a refusal
# and the others are not, and a check that refuses everything gets ignored.
REFUSING = {"hard_stop"}
EXIT_REFUSED = 1


# ---- YAML -------------------------------------------------------------------

def _load_yaml(path: pathlib.Path) -> dict:
    try:
        import yaml
    except ImportError:
        return _tiny_yaml(path.read_text())
    return yaml.safe_load(path.read_text())


def _tiny_yaml(text: str) -> dict:
    """Enough YAML for these files: nested maps, scalars, inline maps and lists,
    block lists, and folded block scalars.

    CI has no PyYAML, so this is the reader that runs where it matters and the one
    nobody exercises by hand. The tests compare it against PyYAML directly.
    """
    root: dict = {}
    stack = [[-1, root, None, None]]
    block = None

    def flush():
        nonlocal block
        if block is not None:
            _, parent, key, lines = block
            parent[key] = " ".join(lines).strip()
            block = None

    for raw in text.splitlines():
        stripped = raw.strip()
        indent = len(raw) - len(raw.lstrip())
        if block is not None:
            if stripped and indent > block[0]:
                block[3].append(stripped)
                continue
            flush()
        if not stripped or stripped.startswith("#"):
            continue
        line = stripped
        if line.startswith("- "):
            while len(stack) > 1 and indent <= stack[-1][0]:
                stack.pop()
            top = stack[-1]
            if isinstance(top[1], dict) and not top[1] and top[2] is not None:
                top[1] = []
                top[2][top[3]] = top[1]
            if isinstance(top[1], list):
                top[1].append(_scalar(line[2:]))
            continue
        if ":" not in line:
            continue
        key, _, rest = line.partition(":")
        # Keys go through the same scalar rules as values. Left raw, `"1"` and `1`
        # name different entries depending on which reader ran, and the one that
        # runs in CI is the one nobody exercises by hand.
        key, rest = _scalar(key), rest.strip()
        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if not isinstance(parent, dict):
            continue
        if rest in (">-", ">", "|", "|-"):
            block = (indent, parent, key, [])
        elif rest == "":
            node: dict = {}
            parent[key] = node
            stack.append([indent, node, parent, key])
        else:
            parent[key] = _scalar(rest)
    flush()
    return root


def _scalar(s: str):
    """A YAML scalar, with the booleans cast.

    Casting them is not cosmetic. Left as strings, `false` is truthy - a failure
    that appears only where PyYAML is absent, which is CI and nowhere a human
    would notice.
    """
    s = s.strip()
    if s in ("true", "True", "yes"):
        return True
    if s in ("false", "False", "no"):
        return False
    if s in ("null", "~"):
        return None
    if s.startswith("{") and s.endswith("}"):
        out = {}
        for part in s[1:-1].split(","):
            if ":" in part:
                k, _, v = part.partition(":")
                out[k.strip()] = _scalar(v)
        return out
    if s.startswith("[") and s.endswith("]"):
        return [_scalar(x) for x in s[1:-1].split(",") if x.strip()]
    if len(s) > 1 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    for cast in (int, float):
        try:
            return cast(s)
        except ValueError:
            pass
    return s


def load_register() -> dict:
    return _load_yaml(REGISTER)


def load_prompters() -> dict:
    return _load_yaml(PROMPTERS)


def load_process() -> dict:
    return _load_yaml(PROCESS)


# ---- the register -----------------------------------------------------------

def matches(text: str, prompter: str | None = None) -> list:
    """Register entries whose settled question this text restates.

    Pattern matching, not judgement. It catches the phrasings that have actually
    been proposed; it will not catch a paraphrase nobody has written yet, which is
    why the skill tells the agent to READ the register rather than only run this.
    """
    reg = load_register()
    low = text.lower()
    hits = []
    for eid, entry in reg["entries"].items():
        if prompter and prompter not in (entry.get("applies_to") or []):
            continue
        for pat in entry.get("patterns") or []:
            m = re.search(pat, low)
            if m:
                hits.append((eid, entry, m.group(0)))
                break
    return hits


def render_entry(eid: str, entry: dict, matched: str | None = None) -> str:
    out = [f"\n{eid}  [{entry['verdict']}]  {entry['title']}"]
    if matched:
        out.append(f'  matched: "{matched}"')
    out.append(f"  finding : {entry['finding']}")
    if entry.get("also"):
        out.append(f"  also    : {entry['also']}")
    out.append(f"  cost    : {entry['cost']}")
    if entry.get("instead"):
        out.append(f"  instead : {entry['instead']}")
    out.append(f"  reopens : {entry['reopens_if']}")
    out.append(f"  source  : {entry['source']}")
    return "\n".join(out)


def cmd_register(args) -> int:
    reg = load_register()
    entries = reg["entries"]
    if args.entry:
        entry = entries.get(args.entry)
        if not entry:
            raise SystemExit(f"no such entry {args.entry!r}; have {', '.join(entries)}")
        print(render_entry(args.entry, entry))
        return 0
    for eid, entry in entries.items():
        if args.prompter and args.prompter not in (entry.get("applies_to") or []):
            continue
        if args.verdict and entry["verdict"] != args.verdict:
            continue
        print(f"{eid:<7} {entry['verdict']:<17} {entry['title']}")
        for claim in entry.get("refuses") or []:
            print(f"         refuses: {claim}")
    print(f"\n{len(entries)} entries. The human mirror is {reg['mirrors']}.")
    print("A candidate restating a hard_stop is refused with the reason quoted,"
          " not debated.")
    return 0


def cmd_check(args) -> int:
    text = " ".join(args.text)
    hits = matches(text, args.prompter)
    if not hits:
        print("No registered hypothesis matches this candidate.")
        print("That is not a clearance. The patterns catch phrasings that have "
              "been proposed before; read the register yourself for the rest:")
        print("  python3 prompt_knowledge.py register")
        return 0

    refusing = [h for h in hits if h[1]["verdict"] in REFUSING]
    for eid, entry, matched in hits:
        print(render_entry(eid, entry, matched))

    print()
    if refusing:
        ids = ", ".join(eid for eid, _, _ in refusing)
        print(f"REFUSED — this candidate restates {ids}.")
        print("Record it as rejected with reason `registered hard stop`, quoting "
              "the finding above, or rewrite it so it proposes something the "
              "register has not already answered.")
        return EXIT_REFUSED
    verdicts = {entry["verdict"] for _, entry, _ in hits}
    if "rewrite_required" in verdicts:
        print("REWRITE — the aim may be sound and the form proposed does not "
              "work. See `instead` above.")
    if "check_first" in verdicts:
        print("ANSWER FIRST — this cannot be diagnosed until the precondition "
              "above is settled.")
    if verdicts == {"known_fyi"}:
        print("KNOWN — measured and accepted. Not a defect unless Product says so.")
    return 0


# ---- the brief --------------------------------------------------------------

HEADING = re.compile(r"^(#{1,3})\s+(.*\S)\s*$")


def sections(prompt_text: str, layers: dict) -> list:
    """The prompt's headings, each tagged with the layer it is load-bearing for."""
    out = []
    for line in prompt_text.splitlines():
        m = HEADING.match(line)
        if not m:
            continue
        level, title = len(m.group(1)), m.group(2)
        low = title.lower()
        layer = None
        for kw in sorted(layers, key=len, reverse=True):
            if kw in low:
                layer = layers[kw]
                break
        out.append((level, title, layer))
    return out


def live_config(is_repo: pathlib.Path, name: str) -> dict | None:
    """The prompter's block from the live src/config.js `prompt` map.

    A regex over a JS object literal, which is fair here: the block is literal
    data with no computation in it, and the alternative is a Node dependency in a
    skill that must run from a checkout with nothing installed.
    """
    path = is_repo / "src" / "config.js"
    if not path.exists():
        return None
    src = path.read_text()
    try:
        i = src.index("\n  prompt: {")
    except ValueError:
        return None
    depth, start = 0, i + len("\n  prompt: ")
    end = len(src)
    for k in range(start, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                end = k + 1
                break
    block = src[start:end]
    m = re.search(r"\n    %s: \{(.*?)\n    \},\n" % re.escape(name), block, re.S)
    if not m:
        return None
    body, out = m.group(1), {}
    for key in ("model", "openaiAPI", "reasoningEffort", "verbosity"):
        mm = re.search(r"^\s*%s: '([^']*)'," % key, body, re.M)
        out[key] = mm.group(1) if mm else None
    tools = re.search(r"^\s*tools: \[(.*?)\]", body, re.S | re.M)
    out["tools"] = re.findall(r"'([^']+)'", tools.group(1)) if tools else []
    out["tools_commented_out"] = bool(re.search(r"^\s*// tools:", body, re.M))
    return out


def _diff_config(recorded: dict, live: dict, recorded_tools=None) -> list:
    """Every field the propose leg is told to read, tools included.

    Tools are part of the deployed configuration REG-4 points at: a tool
    appearing or disappearing changes behaviour with the prompt body untouched,
    which is the exact shape of failure that entry exists to catch.
    """
    keys = ("model", "openaiAPI", "reasoningEffort", "verbosity")
    out = [(k, recorded.get(k), live.get(k)) for k in keys
           if (recorded.get(k) or None) != (live.get(k) or None)]
    if recorded_tools is not None and (recorded_tools or []) != (live.get("tools") or []):
        out.append(("tools", ", ".join(recorded_tools or []) or "none",
                    ", ".join(live.get("tools") or []) or "none"))
    return out


def cmd_brief(args) -> int:
    know = load_prompters()
    spec = know["prompters"].get(args.prompter)
    if not spec:
        raise SystemExit(f"unknown prompter {args.prompter!r}; have "
                         f"{', '.join(know['prompters'])}")
    repo = pathlib.Path(args.is_repo).expanduser()

    print(f"{args.prompter}  [{spec['status']}]  serves {', '.join(spec['serves'])}")
    print(f"  {spec['role']}")

    print("\n2 · DEPLOYED CONFIGURATION")
    print(f"  recorded {know['config_snapshot_taken']} from {know['config_source']}")
    rec = spec["config"]
    live = live_config(repo, args.prompter)
    for key in ("model", "openaiAPI", "reasoningEffort", "verbosity"):
        print(f"    {key:<16} {rec.get(key) if rec.get(key) is not None else '—'}")
    print(f"    {'tools':<16} {', '.join(spec['tools']) or '—'}")
    if live is None:
        print(f"  Could not read {repo}/src/config.js — the values above are the "
              "snapshot and may be stale. Pass --is-repo.")
    else:
        drift = _diff_config(rec, live, spec["tools"])
        if drift:
            print("  DRIFT against the live config:")
            for key, was, now in drift:
                print(f"    {key}: recorded {was or '—'} -> live {now or '—'}")
            print("  A behaviour change with no prompt change is a model or "
                  "reasoning change until proven otherwise (REG-4). If this moved "
                  "in the window the defect appeared, the class is model/config.")
        else:
            print("  matches the live config.")
        if live.get("tools_commented_out"):
            print("  NOTE: a tools line is commented out in config.js. A candidate "
                  "assuming that tool fires is proposing a code change too.")

    print("\n1 · ARCHITECTURE")
    body = repo / spec["prompt_file"]
    if not body.exists():
        print(f"  {spec['prompt_file']} not found under {repo} — pass --is-repo to "
              "read the live section map.")
    else:
        text = body.read_text()
        secs = sections(text, know["section_layers"])
        print(f"  {spec['prompt_file']} — {len(text.splitlines())} lines, "
              f"{len(secs)} headings")
        print(f"  dependency order: {' -> '.join(know['dependency_order'])}")
        print("  A change to a later layer cannot fix a defect that originates in "
              "an earlier one.")
        for level, title, layer in secs:
            if level > args.depth:
                continue
            print(f"    {'  ' * (level - 1)}{title[:60]:<62}{layer or ''}")
        unmapped = [t for lvl, t, l in secs if l is None and lvl <= args.depth]
        if unmapped:
            print(f"  {len(unmapped)} heading(s) map to no layer. That is a gap in "
                  "`section_layers`, not a gap in the prompt.")

    print("\n3 · CHANGE LOG")
    print(f"  Braintrust project: {spec['braintrust_project']}")
    print("  Versions and their activity labels are in Braintrust; the IS release "
          "that carried each one is in the repo's release history.")
    print("  Each change is saved individually with a clear activity label, so a "
          "problem traces to the change that introduced it.")

    print("\n4 · WHAT HAS ALREADY BEEN TRIED")
    reg = load_register()
    applicable = {eid: e for eid, e in reg["entries"].items()
                  if args.prompter in (e.get("applies_to") or [])}
    if not applicable:
        print("  Nothing registered for this prompter.")
    for eid, entry in applicable.items():
        print(f"  {eid:<7} [{entry['verdict']:<16}] {entry['title']}")
    print("\n  Read the full entry before drafting:")
    print("    python3 prompt_knowledge.py register --entry REG-1")
    print("  And check the candidate's own wording:")
    print('    python3 prompt_knowledge.py check "<the change you mean to make>"')

    if spec.get("notes"):
        print(f"\nNOTES\n  {spec['notes']}")
    return 0


def cmd_config_drift(args) -> int:
    know = load_prompters()
    repo = pathlib.Path(args.is_repo).expanduser()
    if not (repo / "src" / "config.js").exists():
        raise SystemExit(f"no src/config.js under {repo}; pass --is-repo")
    drifted = 0
    for name, spec in know["prompters"].items():
        live = live_config(repo, name)
        if live is None:
            print(f"  {name:<24} NOT FOUND in the live config")
            drifted += 1
            continue
        diff = _diff_config(spec["config"], live, spec["tools"])
        if not diff:
            print(f"  {name:<24} ok")
            continue
        drifted += 1
        print(f"  {name:<24} DRIFT")
        for key, was, now in diff:
            print(f"      {key}: recorded {was or '—'} -> live {now or '—'}")
    print(f"\nsnapshot taken {know['config_snapshot_taken']}")
    if drifted:
        print(f"{drifted} prompter(s) drifted. Update references/prompters.yaml and "
              "move `config_snapshot_taken`, in the same change that reads the "
              "drift — a snapshot nobody refreshes answers REG-4 wrongly.")
        return 1
    print("No drift.")
    return 0


MODE_MARK = {"automated": "loop", "assisted": "loop + human", "human": "HUMAN"}


def cmd_process(args) -> int:
    """The nine stages, and who does each one.

    The interesting column is the last: a stage with no leg and no named gate is
    a stage that quietly stops happening the week nobody remembers it.
    """
    proc = load_process()
    stages = proc["stages"]
    if args.stage:
        key = args.stage if args.stage.startswith("stage_") else f"stage_{args.stage}"
        spec = stages.get(key)
        if not spec:
            raise SystemExit(f"no such stage {args.stage!r}; have 1-{len(stages)}")
        print(f"\n{key.replace('stage_', 'Stage ')} — {spec['name']}  [{spec['mode']}]")
        print(f"  legs      {', '.join(spec['legs']) or '— none'}")
        print(f"  gate      {spec['gate']}")
        print(f"  the loop  {spec['what_the_loop_does']}")
        print(f"  the human {spec['what_stays_human']}")
        if spec.get("note"):
            print(f"  note      {spec['note']}")
        return 0

    print(f"{'stage':<7}{'':<34}{'mode':<14}{'gate':<9}legs")
    for key, spec in stages.items():
        n = key.replace("stage_", "")
        print(f"  {n:<5}{spec['name'][:32]:<34}{MODE_MARK[spec['mode']]:<14}"
              f"{spec['gate']:<9}{', '.join(spec['legs']) or '—'}")

    print("\nJUDGMENT NO LEG MAY ASSUME")
    for name, h in proc["human_judgment"].items():
        print(f"\n  {name.replace('_', ' ')}  ({h['stage'].replace('stage_', 'stage ')})")
        print(f"    what  : {h['what']}")
        print(f"    why   : {h['why_not_automated']}")
        print(f"    where : {h['where_it_sits']}")

    print(f"\nPACE\n  {proc['pace']}")
    print(f"\nSource: {proc['source']}")
    print(f"Human mirror: {proc['mirrors']}")
    return 0


def build_parser():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("brief", help="what to read before drafting, for one prompter")
    sp.add_argument("prompter")
    sp.add_argument("--is-repo", default=str(DEFAULT_IS_REPO))
    sp.add_argument("--depth", type=int, default=2,
                    help="heading depth to print (default 2)")
    sp.set_defaults(func=cmd_brief)

    sp = sub.add_parser("process", help="the nine stages, mapped onto the loop")
    sp.add_argument("--stage", help="one stage in full, e.g. 4")
    sp.set_defaults(func=cmd_process)

    sp = sub.add_parser("register", help="what is settled")
    sp.add_argument("--entry")
    sp.add_argument("--prompter")
    sp.add_argument("--verdict")
    sp.set_defaults(func=cmd_register)

    sp = sub.add_parser("check", help="refuse a candidate that restates a settled question")
    sp.add_argument("text", nargs="+")
    sp.add_argument("--prompter")
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("config-drift", help="the snapshot against the live config")
    sp.add_argument("--is-repo", default=str(DEFAULT_IS_REPO))
    sp.set_defaults(func=cmd_config_drift)
    return p


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
