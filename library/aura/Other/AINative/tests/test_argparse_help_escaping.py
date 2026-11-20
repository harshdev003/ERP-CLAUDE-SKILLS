#!/usr/bin/env python3
"""Every argparse `help=` string must survive argparse's own %-formatting.

**Why this has to be a test.** argparse %-formats help strings when it renders them,
so a literal percent sign must reach argparse as `%%`. Get that wrong and the failure
is not a cosmetic glitch in `--help` output: on Python 3.12+ `_check_help` validates
every help string while the parser is being BUILT, so `ap.parse_args()` raises
`ValueError: badly formed help string` and the script dies before it does any work.

That is what happened to `voice_tier_breakdown.py`. Its help was assembled as
`"... %.0f%% of walkable spend ..." % _COVERAGE_FLOOR_PCT`, which collapsed `%%` to a
single `%` before argparse ever saw it; argparse then read `99% of` as a format spec
and refused to start. It had presumably worked for as long as the machine running it
stayed on an older Python, which is the kind of breakage a version bump surfaces all
at once.

**The check is static, over every plugin script**, because the bug is cheap to
reintroduce anywhere and costs a whole run when it lands: any `%` in a help string
that is not `%%` and not the opening of a `%(name)s` reference is a defect. A static
sweep also covers scripts this suite cannot import - most need credentials at module
scope.
"""
from __future__ import annotations

import ast
import pathlib
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = REPO_ROOT / "plugins"


def _literal_text(node: ast.AST) -> str:
    """Concatenate every string literal reachable inside a help= expression.

    Deliberately crude: implicit concatenation, f-strings and `%`-built strings all
    contribute their literal parts, which is where a stray percent sign lives. A
    runtime-computed fragment contributes nothing, so this under-reports rather than
    inventing a defect that is not there.
    """
    out = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            out.append(sub.value)
    return "".join(out)


def _bad_percent(text: str) -> bool:
    """True when `text` holds a percent sign argparse cannot format."""
    i = 0
    while i < len(text):
        if text[i] == "%":
            nxt = text[i + 1:i + 2]
            if nxt == "%":       # escaped - fine
                i += 2
                continue
            if nxt == "(":       # %(default)s and friends - fine
                i += 1
                continue
            return True
        i += 1
    return False


class ArgparseHelpEscaping(unittest.TestCase):
    def test_no_unescaped_percent_in_any_help_string(self):
        offenders = []
        for path in sorted(PLUGINS.rglob("*.py")):
            try:
                tree = ast.parse(path.read_bytes())
            except SyntaxError:
                continue  # not ours to police here
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call)
                        and getattr(node.func, "attr", "") == "add_argument"):
                    continue
                for kw in node.keywords:
                    if kw.arg != "help":
                        continue
                    text = _literal_text(kw.value)
                    if _bad_percent(text):
                        rel = path.relative_to(REPO_ROOT)
                        offenders.append(f"{rel}:{node.lineno} -> {text[:70]!r}")

        sep = "\n  "
        self.assertEqual(
            offenders, [],
            "Unescaped % in an argparse help string. argparse %-formats help, and on "
            "Python 3.12+ this raises 'badly formed help string' while BUILDING the "
            "parser, so the script dies before doing any work. Write a literal percent "
            "as %%, and if the string is built with % or an f-string make sure the %% "
            "survives into the value argparse receives:\n  " + sep.join(offenders))


if __name__ == "__main__":
    unittest.main()
