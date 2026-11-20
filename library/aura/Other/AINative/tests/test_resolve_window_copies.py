"""Every resolve_window.py copy is the canonical, byte for byte (ENG-1414).

The drift command in docs/canonical-sources.md reported 8 of 9 copies as different for
months. The differences were docstring-only, but a drift check that always fires stops
being read, so a real code divergence would have shipped behind it. This makes the
check a gate: copies are discovered by glob (never listed), each must equal the
canonical, and the behavioural cases run against every copy so the suite still means
something if the identity rule is ever relaxed.

`data:report-aurie-usage` carries a different implementation (inclusive `to`) and is
the one documented exception in docs/canonical-sources.md.
"""

from __future__ import annotations

import importlib.util
import pathlib
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
CANONICAL = REPO / "plugins/eng/skills/analyze-listens/scripts/resolve_window.py"
EXCEPTIONS = {"report-aurie-usage"}


def _copies() -> list[pathlib.Path]:
    return sorted(p for p in (REPO / "plugins").glob("*/skills/*/scripts/resolve_window.py")
                  if p.parent.parent.name not in EXCEPTIONS)


def _load(path: pathlib.Path):
    name = "rw_" + path.parent.parent.name.replace("-", "_")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestResolveWindowCopies(unittest.TestCase):

    def test_discovery_finds_the_family(self):
        copies = _copies()
        self.assertIn(CANONICAL, copies)
        self.assertGreaterEqual(len(copies), 2)

    def test_every_copy_is_byte_identical_to_the_canonical(self):
        want = CANONICAL.read_bytes()
        drifted = [str(p.relative_to(REPO)) for p in _copies() if p.read_bytes() != want]
        self.assertEqual(drifted, [], "copy the canonical over these (docs/canonical-sources.md)")

    def test_every_copy_resolves_a_custom_range_half_open(self):
        for path in _copies():
            with self.subTest(copy=path.parent.parent.name):
                w = _load(path).resolve(None, "2026-09-01", "2026-09-08")
                self.assertEqual((w["from"], w["to"]), ("2026-09-01", "2026-09-08"))
                self.assertEqual(w["from_iso"], "2026-09-01T00:00:00Z")
                self.assertEqual(w["to_iso"], "2026-09-08T00:00:00Z")
                self.assertEqual(w["days_in"], 7)

    def test_every_copy_refuses_an_empty_or_unknown_window(self):
        for path in _copies():
            mod = _load(path)
            with self.subTest(copy=path.parent.parent.name):
                with self.assertRaises(SystemExit):
                    mod.resolve(None, "2026-09-08", "2026-09-01")
                with self.assertRaises(SystemExit):
                    mod.resolve("no-such-window", None, None)


if __name__ == "__main__":
    unittest.main()
