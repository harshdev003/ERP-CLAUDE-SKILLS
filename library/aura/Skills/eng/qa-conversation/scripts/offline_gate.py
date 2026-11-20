#!/usr/bin/env python3
"""The offline gate: one command from a candidate prompt to a scorecard.

Three subcommands, and the split is deliberate — generation costs money and
judgement costs time, so neither should be a side effect of asking what a run
would involve.

  plan   what this candidate's class requires, which datasets, against which
         recorded baselines. No calls, no keys.
  score  turn a results file into the scorecard, applying the reporting contract.
  gate   the pass/fail verdict alone, for a caller that only needs the answer.

What it will not do, because the rubric forbids it and a scorecard that does it
is worse than none:

  * No single pass rate across the suite. The datasets differ in size and in
    replication and one is cohort-replicated; a suite total treats replicates as
    independent evidence and overstates n fivefold.
  * No mixing with multi-turn. Different replication units, different
    denominators. `measured.md` is drive data and is not a baseline for this.
  * No comparison across instruments. A baseline is a value plus the judge and
    dataset version that produced it; when either moves the baseline is
    unverified, not carried forward.

Generation itself belongs to `eng:qa-prompt`; this owns the contract around it.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
BASELINES = HERE.parent / "references" / "baselines.dataset_replay.yaml"
CONTRACT = HERE.parent / "references" / "release_contract.yaml"


def _load_yaml(path: pathlib.Path) -> dict:
    """Minimal reader for this file's shape.

    Not PyYAML: no shipped script imports it, so it is not a repo dependency, and
    a gate that cannot run because a library is missing is a gate nobody runs.
    """
    try:
        import yaml  # noqa: F401
    except ImportError:
        pass
    else:
        import yaml
        return yaml.safe_load(path.read_text())
    return _tiny_yaml(path.read_text())


def _tiny_yaml(text: str) -> dict:
    """Enough YAML for these two files: nested maps, scalars, inline maps, block
    lists, and folded block scalars.

    The folded scalars are not decoration. Every one of them holds the reason a
    rule exists, and a reader that drops them turns the contract into a table of
    unexplained numbers -- which is the state it was written to get out of.
    """
    root: dict = {}
    stack = [[-1, root, None, None]]
    block = None  # (key_indent, parent, key, [lines]) while a >- body is open

    def flush():
        nonlocal block
        if block is not None:
            _, parent, key, lines = block
            parent[key] = " ".join(lines).strip()
            block = None

    for raw in text.splitlines():
        stripped = raw.strip()
        indent = len(raw) - len(raw.rstrip("\n").lstrip())
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

    Casting them is not cosmetic. Left as strings, `false` is truthy, and every
    `blocking: false` in the contract reads as blocking -- a failure that appears
    only where PyYAML is absent, which is CI and nowhere a human would notice.
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
        return [x.strip() for x in s[1:-1].split(",") if x.strip()]
    if s.startswith('"') and s.endswith('"'):
        return s[1:-1]
    for cast in (int, float):
        try:
            return cast(s)
        except ValueError:
            pass
    return s


def load_baselines() -> dict:
    return _load_yaml(BASELINES)


def load_contract() -> dict:
    return _load_yaml(CONTRACT)


def class_datasets(contract: dict) -> dict:
    """class -> locked-suite scope, read from the contract rather than held here.

    One map, read by the gate, by the evidence table in
    product:evaluate-prompt-candidate, and mirrored in prose on the loop page.
    Independent copies of a release rule disagree eventually, and the
    disagreement surfaces at Gate 2 as an argument rather than as an error.
    """
    return {k: v["locked_suite"] for k, v in contract["classes"].items()}


# ---- scoring ---------------------------------------------------------------

def score_dataset(key: str, spec: dict, observed: dict, material_pp: float) -> dict:
    """One dataset's verdict. Returns a row, never a contribution to a total."""
    base = spec.get("baseline") or {}
    base_rate = float(base.get("rate", 0.0))
    passed, total = int(observed.get("pass", 0)), int(observed.get("total", 0))
    uncertain = int(observed.get("uncertain", 0))
    rate = passed / total if total else 0.0
    delta_pp = (rate - base_rate) * 100
    if abs(delta_pp) < material_pp:
        verdict = "unmoved"
    else:
        verdict = "improved" if delta_pp > 0 else "REGRESSED"
    return {
        "dataset": key, "name": spec.get("name"), "pass": passed, "total": total,
        "uncertain": uncertain, "rate": rate, "baseline_rate": base_rate,
        "delta_pp": delta_pp, "verdict": verdict,
        "expected_total": spec.get("judgements"),
        "cohort_replicated": bool(spec.get("replicates_per_scenario")),
    }


def render(rows: list[dict], material_pp: float) -> str:
    def line(r: dict) -> str:
        frac = f"{r['pass']}/{r['total']}"
        return (f"{r['dataset']:<22}{frac:>10}{r['rate']*100:>8.1f}%"
                f"{r['baseline_rate']*100:>9.1f}%{r['delta_pp']:>+8.1f}  {r['verdict']}")

    out = [f"{'dataset':<22}{'pass':>10}{'rate':>9}{'baseline':>10}{'delta':>9}  verdict",
           "-" * 72]
    independent = [r for r in rows if not r["cohort_replicated"]]
    cohort = [r for r in rows if r["cohort_replicated"]]
    out += [line(r) for r in independent]
    if cohort:
        out += ["", "reported separately - cohort-replicated, not independent cases:"]
        out += [line(r) for r in cohort]

    short = [r for r in rows if r["expected_total"] and r["total"] != r["expected_total"]]
    if short:
        out += ["", "DENOMINATOR MISMATCH - a rate over the wrong n is not comparable:"]
        out += [f"  {r['dataset']}: {r['total']} judgements, expected {r['expected_total']}"
                for r in short]

    unc = sum(r["uncertain"] for r in rows)
    if unc:
        out += ["", f"{unc} uncertain label(s) - NOT counted as passes. Resolve by discussion",
                "or a second blinded reviewer before these rates are quoted."]
    out += ["", "No suite total is printed, by design: the datasets differ in size and in",
            f"replication. A delta under {material_pp:g}pp reads as unmoved."]
    return "\n".join(out)


def verdict(rows: list[dict]) -> tuple[str, list[str]]:
    """Advisory resolution: one regression is not a block, two pointing the same
    way is a concern. Gate rows live in the Safety Suite, not here."""
    regressed = [r["dataset"] for r in rows if r["verdict"] == "REGRESSED"]
    if len(regressed) >= 2:
        return "CONCERN", regressed
    if regressed:
        return "WATCH", regressed
    return "CLEAR", []


# ---- commands --------------------------------------------------------------

def cmd_plan(args):
    b = load_baselines()
    contract = load_contract()
    spec = contract["classes"].get(args.change_class)
    if spec is None:
        raise SystemExit(f"--change-class must be one of {sorted(contract['classes'])}")
    scope = spec["locked_suite"]
    ss, mt = spec["safety_suite"], spec["multi_turn"]
    print(f"candidate class : {args.change_class}  -> ships {spec['ships']}")
    print(f"safety suite    : {ss['draws']} draw(s)"
          + ("  BLOCKING" if ss.get("blocking") else f"  ({ss.get('role','regression check')})"))
    if mt.get("required"):
        print(f"multi-turn      : {mt['drives_per_arm']} drives per arm - REQUIRED")
    else:
        print("multi-turn      : not required for this class")
    print(f"locked suite    : {scope}")
    print("source cases    : " + ("REQUIRED (informs Gate 2, does not block)"
                                  if (spec.get("source_cases") or {}).get("required") else "not required"))
    print(f"baseline arm    : {b['baseline_arm']['code']} "
          f"(version {b['baseline_arm']['prompt_version']}, {b['baseline_arm']['model']})")
    print(f"measured        : {b['source']['measured_on']} · judge {b['source']['judge']} · "
          f"mode {b['source']['mode']}")
    if scope == "none":
        print("\nThis class runs no rubric datasets — its evidence is the zero-tolerance rows.")
        return 0
    print(f"\n{'dataset':<22}{'rows':>6}{'judgements':>12}{'baseline':>10}")
    total_rows = total_j = 0
    for key, spec in b["datasets"].items():
        print(f"{key:<22}{spec['rows']:>6}{spec['judgements']:>12}"
              f"{float(spec['baseline']['rate'])*100:>9.1f}%")
        total_rows += int(spec["rows"]); total_j += int(spec["judgements"])
    print(f"{'':<22}{total_rows:>6}{total_j:>12}")
    print(f"\n{b['source']['generations_per_row']} generations per row, except repetition: "
          f"its rows are already replicates and are scored once each.")
    print("Generation belongs to eng:qa-prompt. Feed its output back through `score`.")
    return 0


def cmd_score(args):
    b = load_baselines()
    observed = json.loads(pathlib.Path(args.results).read_text())
    material = float(load_contract()["resolution"]["material_change_pp"])
    rows = []
    for key, spec in b["datasets"].items():
        if key not in observed:
            print(f"note: {key} not in results — reported as not measured, never as a pass")
            continue
        rows.append(score_dataset(key, spec, observed[key], material))
    if not rows:
        raise SystemExit("no dataset in the results file matches the locked suite")
    print(render(rows, material))
    v, which = verdict(rows)
    print(f"\nadvisory verdict: {v}" + (f" — {', '.join(which)}" if which else ""))
    missing = [k for k in b["datasets"] if k not in observed]
    if missing:
        print(f"NOT MEASURED: {', '.join(missing)}")
    if args.json_out:
        pathlib.Path(args.json_out).write_text(json.dumps(
            {"rows": rows, "verdict": v, "regressed": which, "not_measured": missing}, indent=1))
    return 0


def cmd_gate(args):
    b = load_baselines()
    observed = json.loads(pathlib.Path(args.results).read_text())
    material = float(load_contract()["resolution"]["material_change_pp"])
    rows = [score_dataset(k, s, observed[k], material)
            for k, s in b["datasets"].items() if k in observed]
    v, which = verdict(rows)
    print(v + (f" {' '.join(which)}" if which else ""))
    return 0 if v != "CONCERN" else 1


def cmd_contract(args):
    """Print what blocks a release, for one class or all of them."""
    c = load_contract()
    names = [args.change_class] if args.change_class else list(c["classes"])
    for name in names:
        spec = c["classes"].get(name)
        if spec is None:
            raise SystemExit(f"unknown class {name!r}")
        ss, mt = spec["safety_suite"], spec["multi_turn"]
        print(f"\n{name}  -> ships {spec['ships']}")
        if spec.get("ships_why"):
            print(f"   why: {spec['ships_why']}")
        print(f"   safety suite : {ss['draws']} draw(s), "
              f"{'BLOCKS the release' if ss.get('blocking') else 'regression check only'}")
        print(f"   locked suite : {spec['locked_suite']}")
        print(f"   multi-turn   : {mt['drives_per_arm'] if mt.get('required') else 'not required'}")
        print(f"   source cases : {'required, informs' if (spec.get('source_cases') or {}).get('required') else 'not required'}")
    print("\nwhat blocks, and what only informs")
    print(f"   {c['resolution']['gate_rows_do_not_rank']}")
    for rule in c["resolution"]["advisory_rules"]:
        print(f"   - {rule}")
    print(f"\nreport order (NOT weights): {', '.join(c['product_priorities'])}")
    print(f"   {c['product_priorities_note']}")
    print(f"\nunreviewed rows: {c['unreviewed_rows_may_not_gate']}")
    return 0


def build_parser():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("contract", help="what blocks a release, per change class")
    sp.add_argument("--change-class")
    sp.set_defaults(func=cmd_contract)
    sp = sub.add_parser("plan", help="what a run requires; no calls")
    sp.add_argument("--change-class", required=True)
    sp.set_defaults(func=cmd_plan)
    sp = sub.add_parser("score", help="results -> scorecard")
    sp.add_argument("results")
    sp.add_argument("--json-out")
    sp.set_defaults(func=cmd_score)
    sp = sub.add_parser("gate", help="the verdict alone; exit 1 on CONCERN")
    sp.add_argument("results")
    sp.set_defaults(func=cmd_gate)
    return p


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
