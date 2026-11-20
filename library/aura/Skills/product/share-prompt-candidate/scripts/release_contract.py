#!/usr/bin/env python3
"""The release contract: what blocks a release, per change class.

A per-skill copy of `eng:qa-conversation`'s reader and of the contract file
itself, per AINative principle 1 - a skill may not import across plugins. The
copy is kept honest by `tests/test_release_contract.py`, which fails if the two
YAML files differ by a byte. That is the same treatment the rubric mirror gets,
and for the same reason: a silent copy of a release rule is how Gate 2 turns into
an argument about which table was right.
"""
from __future__ import annotations

import pathlib

HERE = pathlib.Path(__file__).resolve().parent
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


def load() -> dict:
    return _load_yaml(CONTRACT)


def requirements(klass: str) -> dict | None:
    """The evidence one class must produce, rendered from the contract.

    Returns None for an unknown class, which is what an unclassified row is:
    the class is declared at Gate 1 precisely so that this lookup can succeed.
    """
    c = load()
    spec = c["classes"].get(klass)
    if not spec:
        return None
    ss, mt = spec["safety_suite"], spec["multi_turn"]
    if ss.get("blocking"):
        suite = f"{ss['draws']} draws per arm - BLOCKING"
    else:
        suite = str(ss.get("role", "regression check")).replace("_", " ")
    labels = c["locked_suite_labels"]
    return {
        "safety_suite": suite if ss.get("required") else None,
        "locked_suite": labels.get(spec["locked_suite"]) or None,
        "locked_suite_scope": spec["locked_suite"],
        "drives": (f">={mt['drives_per_arm']} drives per arm"
                   if mt.get("required") else None),
        "source_cases": (c.get("source_cases_label") or "required")
                        if (spec.get("source_cases") or {}).get("required") else None,
        "ships": spec["ships"],
    }


def reporting_contract() -> list:
    return list(load()["reporting_contract"])
