"""A capped LiveKit log read must be loud, and every call site must handle it.

`livekit_core.list_entries` stops at `max_entries` and used to return what it had. The
policy for *which* entries to keep was correct and carefully reasoned — newest-first, so a
busy day keeps recent activity — but nothing told the caller a choice had been made. So
`eng:report-livekit-quota` measured the most recent ~4.5 days of September and rendered it
as the month: **2,689 minutes against LiveKit's own 8,037**, a 3x under-report from the one
instrument whose job is to warn before an allotment binds, in the reassuring direction.

Two properties are pinned here because each fails silently:

1. **The helper raises**, so the contract cannot be forgotten. The sibling `gcp.py` family
   checks `len(raw) >= max_entries` at each call site and that works — but six callers of
   `livekit_core` never did, which is the difference between a convention and a contract.
2. **Every call site handles it explicitly, ahead of any broad `except`.** This is the half
   a helper cannot enforce: every one of these sites already wrapped the call in
   `except Exception`, and one in `except Exception: return None`. Raising without fixing
   those converts a silent undercount into a silent *total failure* — strictly worse. The
   call-site set is discovered by walking the scripts, so a seventh consumer is covered the
   day it lands rather than the day someone remembers this file.

Stdlib `unittest`, no network, no keys.
"""

from __future__ import annotations

import contextlib
import importlib.abc
import importlib.util
import pathlib
import re
import sys
import types
import unittest
import uuid

REPO = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = REPO / "plugins"
CANONICAL = PLUGINS / "eng/skills/analyze-livekit-usage/scripts/livekit_core.py"

COPIES = sorted(PLUGINS.glob("eng/skills/*/scripts/livekit_core.py"))

# Scripts that call lk.list_entries. Discovered, not listed: a hand-maintained list is the
# artifact that goes stale, and staleness here means a new consumer silently truncates.
CALL_SITES = sorted(
    p for p in PLUGINS.glob("eng/skills/*/scripts/*.py")
    if p.name != "livekit_core.py"
    and re.search(r"\blk\.list_entries\(|(?<!def )\blist_entries\(auth", p.read_text(encoding="utf-8"))
    and (p.parent / "livekit_core.py").exists()
)


class _StubModule(types.ModuleType):
    def __getattr__(self, item):
        stub = types.SimpleNamespace()
        setattr(self, item, stub)
        return stub


class _GoogleStubFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "google" or fullname.startswith("google."):
            return importlib.util.spec_from_loader(fullname, self, is_package=True)
        return None

    def create_module(self, spec):
        mod = _StubModule(spec.name)
        mod.__path__ = []
        return mod

    def exec_module(self, module):
        pass


@contextlib.contextmanager
def _stubbed_google():
    saved = {k: v for k, v in sys.modules.items() if k == "google" or k.startswith("google.")}
    finder = _GoogleStubFinder()
    for k in saved:
        del sys.modules[k]
    sys.meta_path.insert(0, finder)
    try:
        yield
    finally:
        sys.meta_path.remove(finder)
        for k in [k for k in sys.modules if k == "google" or k.startswith("google.")]:
            del sys.modules[k]
        sys.modules.update(saved)


def load(path: pathlib.Path):
    name = f"_lktrunc_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    saved_path = list(sys.path)
    sys.path.insert(0, str(path.parent))
    sys.modules[name] = mod
    try:
        with _stubbed_google():
            spec.loader.exec_module(mod)
    finally:
        sys.path[:] = saved_path
        sys.modules.pop(name, None)
    return mod


class _FakeEntry:
    def __init__(self, i):
        self.i = i

    def to_api_repr(self):
        return {"jsonPayload": {"n": self.i}, "timestamp": f"2026-09-15T00:00:{self.i % 60:02d}Z"}


def _auth_yielding(n):
    """An auth whose client yields exactly n entries."""
    client = types.SimpleNamespace(list_entries=lambda **kw: (_FakeEntry(i) for i in range(n)))
    return types.SimpleNamespace(client=client)


class TheHelperRaisesOnACappedRead(unittest.TestCase):
    """Property 1, held against every copy — a copy is only useful if it behaves."""

    def test_hitting_the_cap_raises(self):
        for path in COPIES:
            with self.subTest(copy=path.parent.parent.name):
                lk = load(path)
                with self.assertRaises(lk.TruncatedReadError):
                    list(lk.list_entries(_auth_yielding(50), "f", max_entries=10))

    def test_a_read_under_the_cap_does_not_raise(self):
        # The negative case is what stops the guard being "always raise", which would pass
        # every other assertion in this file.
        for path in COPIES:
            with self.subTest(copy=path.parent.parent.name):
                lk = load(path)
                got = list(lk.list_entries(_auth_yielding(5), "f", max_entries=10))
                self.assertEqual(len(got), 5)

    def test_a_read_exactly_at_the_cap_raises(self):
        # Boundary: n == max_entries is indistinguishable from "there was more", so it must
        # be treated as truncated. Off-by-one here restores the silent undercount.
        lk = load(CANONICAL)
        with self.assertRaises(lk.TruncatedReadError):
            list(lk.list_entries(_auth_yielding(10), "f", max_entries=10))

    def test_the_exception_carries_the_partial_entries(self):
        lk = load(CANONICAL)
        try:
            list(lk.list_entries(_auth_yielding(50), "f", max_entries=10))
            self.fail("expected TruncatedReadError")
        except lk.TruncatedReadError as exc:
            self.assertEqual(len(exc.entries), 10)
            self.assertEqual(exc.max_entries, 10)

    def test_allow_truncated_opts_out(self):
        lk = load(CANONICAL)
        got = list(lk.list_entries(_auth_yielding(50), "f", max_entries=10, allow_truncated=True))
        self.assertEqual(len(got), 10)

    def test_the_newest_entries_are_the_ones_kept(self):
        # The pre-existing policy this change must not disturb.
        lk = load(CANONICAL)
        got = list(lk.list_entries(_auth_yielding(50), "f", max_entries=10, allow_truncated=True))
        self.assertEqual([e["jsonPayload"]["n"] for e in got], list(range(9, -1, -1)))


class EveryCallSiteHandlesIt(unittest.TestCase):
    """Property 2 — the half the helper cannot enforce, and the half that was broken."""

    def test_call_sites_were_discovered(self):
        # A sweep that finds nothing would make every assertion below vacuously true.
        self.assertGreaterEqual(len(CALL_SITES), 5, f"only found {[p.name for p in CALL_SITES]}")

    def test_each_call_site_catches_truncation_explicitly(self):
        for path in CALL_SITES:
            with self.subTest(skill=path.parent.parent.name):
                text = path.read_text(encoding="utf-8")
                self.assertIn(
                    "TruncatedReadError", text,
                    f"{path.parent.parent.name} calls list_entries but never handles "
                    "TruncatedReadError — a truncated read there is a silent undercount, "
                    "or a hard failure if a broad `except Exception` swallows it",
                )

    def test_truncation_is_caught_before_any_broad_except(self):
        # Ordering is the whole point: `except Exception` placed first swallows the signal
        # and turns it into a generic query error, which reads as an outage rather than a
        # partial window.
        for path in CALL_SITES:
            with self.subTest(skill=path.parent.parent.name):
                text = path.read_text(encoding="utf-8")
                for m in re.finditer(r"list_entries\(", text):
                    tail = text[m.end():m.end() + 1400]
                    trunc = tail.find("except lk.TruncatedReadError")
                    if trunc == -1:
                        trunc = tail.find("except TruncatedReadError")
                    broad = tail.find("except Exception")
                    if broad == -1:
                        continue
                    self.assertNotEqual(
                        trunc, -1,
                        f"{path.name}: a list_entries call is followed by a broad "
                        "`except Exception` with no TruncatedReadError handler before it",
                    )
                    self.assertLess(
                        trunc, broad,
                        f"{path.name}: `except Exception` precedes the TruncatedReadError "
                        "handler, so truncation is swallowed as a generic error",
                    )


class TheQuotaSkillDoesNotBillFromALogWalk(unittest.TestCase):
    """A month of this stream cannot fit under any cap, so the walk is not the source."""

    QUOTA = PLUGINS / "eng/skills/report-livekit-quota/scripts/report.py"

    def test_minutes_come_from_the_cost_store(self):
        text = self.QUOTA.read_text(encoding="utf-8")
        self.assertIn("def stored_session_minutes(", text)

    def test_the_store_read_is_wired_into_main(self):
        # The #989 lesson: a helper that exists but is never called by main() ships as a
        # no-op that looks like a fix.
        text = self.QUOTA.read_text(encoding="utf-8")
        body = text[text.index("def main("):]
        self.assertIn("stored_session_minutes(", body)

    def test_the_store_read_uses_the_warehouse_project(self):
        # Also #989: the logging project is not the warehouse project, and getting this
        # wrong degrades to UNMEASURED rather than erroring.
        text = self.QUOTA.read_text(encoding="utf-8")
        body = text[text.index("def main("):]
        call = body[body.index("stored_session_minutes("):]
        self.assertIn("bq_project()", call[:400])

    def test_an_unreachable_store_does_not_report_zero_minutes(self):
        text = self.QUOTA.read_text(encoding="utf-8")
        fn = text[text.index("def stored_session_minutes("):]
        fn = fn[:fn.index("\ndef ", 10)]
        self.assertIn("return None", fn)
        self.assertNotIn("return 0.0, 0", fn)


class ATruncatedFallbackDoesNotUnderstateBurn(unittest.TestCase):
    """The undercount this change removes must not reappear on the fallback path.

    A capped read keeps the NEWEST entries, so its minutes describe the tail of the period.
    Dividing them by the full elapsed days understates burn, projection and the `--alert`
    verdict — in the reassuring direction, which is the whole defect. Found by review on
    the first cut of this change (the denominator stayed at `period_days_elapsed`).
    """

    QUOTA = PLUGINS / "eng/skills/report-livekit-quota/scripts/report.py"

    def _load_quota(self):
        return load(self.QUOTA)

    def test_span_days_measures_the_retained_window(self):
        q = self._load_quota()
        entries = [{"timestamp": "2026-09-12T00:00:00Z"}, {"timestamp": "2026-09-15T00:00:00Z"}]
        self.assertEqual(q._span_days(entries), 3)

    def test_span_days_is_at_least_one_for_a_sub_day_window(self):
        # 0 would make burn a division by zero, and the caller's `or 1` must have something
        # sane to fall back to.
        q = self._load_quota()
        entries = [{"timestamp": "2026-09-15T01:00:00Z"}, {"timestamp": "2026-09-15T05:00:00Z"}]
        self.assertEqual(q._span_days(entries), 1)

    def test_span_days_degrades_to_zero_on_unusable_stamps(self):
        q = self._load_quota()
        self.assertEqual(q._span_days([]), 0)
        self.assertEqual(q._span_days([{"timestamp": "not-a-date"}, {"timestamp": "x"}]), 0)

    def test_the_truncated_branch_reassigns_days_covered(self):
        # The source-level guard: the fallback must narrow the denominator, not inherit the
        # period's. A comment claiming it does is not the same as doing it.
        text = self.QUOTA.read_text(encoding="utf-8")
        branch = text[text.index("if truncated:"):]
        branch = branch[:branch.index("print(f\"# fetched")]
        self.assertIn("days_covered = _span_days(entries)", branch)

    def test_the_truncated_branch_says_it_is_a_lower_bound(self):
        text = self.QUOTA.read_text(encoding="utf-8")
        branch = text[text.index("if truncated:"):]
        branch = branch[:branch.index("print(f\"# fetched")]
        self.assertIn("LOWER BOUND", branch)


if __name__ == "__main__":
    unittest.main()
