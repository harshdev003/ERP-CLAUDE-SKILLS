#!/usr/bin/env python3
"""The rejected-hypothesis register: what has already been tried, and what it cost.

A per-skill copy of `product:prompt-knowledge`'s reader and of the register file
itself, per AINative principle 1 - a skill may not import across plugins or
across skills. The copies are kept honest by `tests/test_prompt_register.py`,
which fails if any of them differs from the owner by a byte.

The owner is `product:prompt-knowledge`. Add or retire an entry there.
"""
from __future__ import annotations

import pathlib
import re

HERE = pathlib.Path(__file__).resolve().parent
REGISTER = HERE.parent / "references" / "register.yaml"

REFUSING = {"hard_stop"}


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




def entries() -> dict:
    return load_register()["entries"]


def matches(text: str, prompter: str | None = None) -> list:
    """Register entries whose settled question this text restates.

    Pattern matching, not judgement. It catches the phrasings that have actually
    been proposed; a paraphrase nobody has written yet gets through, which is why
    the skill also tells the agent to read the register.
    """
    low = (text or "").lower()
    hits = []
    for eid, entry in entries().items():
        if prompter and prompter not in (entry.get("applies_to") or []):
            continue
        for pat in entry.get("patterns") or []:
            m = re.search(pat, low)
            if m:
                hits.append((eid, entry, m.group(0)))
                break
    return hits


def refusals(text: str, prompter: str | None = None) -> list:
    """Only the hits that stop a candidate dead."""
    return [h for h in matches(text, prompter) if h[1]["verdict"] in REFUSING]


def hard_stops_by_theme() -> dict:
    """Theme -> the settled reading, for a caller that ranks themes.

    Only the entries that actually stop a candidate. A `known_fyi` theme is not a
    hard stop and printing it as one would teach the reader to ignore the label.
    """
    out = {}
    for eid, entry in entries().items():
        if entry["verdict"] not in REFUSING:
            continue
        reading = entry["finding"]
        if entry.get("instead"):
            reading += " " + entry["instead"]
        for theme in entry.get("themes") or []:
            out[theme] = f"{entry['title']}. {reading}"
    return out


def notes_by_theme() -> dict:
    """Theme -> a measured, accepted reading that is NOT a hard stop.

    `forced_choice` is the case: the share was measured, Product read it and
    accepted it. Surfacing it as a hard stop would teach the reader to discount
    the label; dropping it entirely would let it be re-raised as a discovery.
    """
    out = {}
    for eid, entry in entries().items():
        if entry["verdict"] != "known_fyi":
            continue
        for theme in entry.get("themes") or []:
            out[theme] = f"{entry['title']}. {entry['finding']}"
    return out
