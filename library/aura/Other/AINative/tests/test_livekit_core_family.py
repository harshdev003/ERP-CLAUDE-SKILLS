#!/usr/bin/env python3
"""One `livekit_core.py`, six copies, and no way to add a seventh unnoticed.

**Why a test and not another `diff -q`.** The CLAUDE.md one-liner for this family
answers "do the files named `livekit_core.py` still match?" — and it answered
"yes" for the entire life of `report-livekit-quota`, whose copy the prose list
never named. The copy was byte-identical, so the guard was green while the list a
propagating editor actually reads was wrong. That is the failure mode CLAUDE.md
already records for `search-gcp-by-user`, and its closing rule is to write the
test rather than the note. So membership here is decided by **shape** — a module
defining the `LIVEKIT_LOG_NAME` anchor and `build_sessions` — and
`test_family_membership_is_complete` fails the moment an undeclared copy appears
under any filename.

**The two behavioural pins are the ones that fail silently.** A wrong log-stream
anchor returns zero entries while erroring on nothing, so every skill in the
family reports a quiet day instead of a broken query. And `build_sessions` is the
minutes proxy that `report-livekit-quota` bills its plan against, that
`analyze-livekit-cost` allocates vendor dollars by, and that
`eng:store-aurie-user-daily-cost` lifts into `livekit_minutes.py` — a copy that
grouped by the wrong key or measured duration differently would move real money
without raising.

**Declared drift, not blessed drift.** `KNOWN_DRIFT` can exempt a copy on a
named constant, and is currently empty — ENG-1218 widened the canonical rather
than freezing a narrower tuple into an exception. A declared exception that stops
being real fails the suite, so it gets removed instead of quietly covering the
next divergence.

Stdlib `unittest`, no network, no keys. Modules import their transports but never
call them; only pure parsing runs.
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

# The forwarded LiveKit Cloud log stream. Deliberately spelled out here rather
# than imported from the module under test: a test that reads the anchor from the
# module it is checking asserts nothing about the anchor.
LOG_NAME = "projects/firebase-auratech16/logs/aurie-voice-agent"

# Deliberately OUT of the byte-identical family, with a reason rather than a
# silent non-match — an unexplained new hit must still fail membership.
#
# `store-aurie-user-daily-cost`'s `livekit_minutes.py` is a PARTIAL derived copy
# (CLAUDE.md, "Canonical source for `livekit_core.py`"): it lifts the auth ladder,
# the query helpers and the per-room minutes aggregation, and intentionally drops
# turn-latency extraction, percentiles and PII redaction. It shares this family's
# log anchor and session semantics, so shape-discovery finds it — which is what we
# want; it is excluded from byte-identity only.
PARTIAL_DERIVED = {
    PLUGINS / "eng/skills/store-aurie-user-daily-cost/scripts/livekit_minutes.py",
}

# Module-level names a copy may legitimately differ on, declared per file AND per
# name — so an undeclared change in the same file still fails.
#
# Empty on purpose. `analyze-livekit-latency` diverged on LATENCY_METRICS; ENG-1218
# widened the canonical to the five metrics the agent actually emits rather than
# carving out a permanent exception, which is the better resolution — an exception
# would have frozen the narrower tuple into the guard.
#
# `test_declared_drift_is_still_real` is what keeps this honest: a declared
# exception that stops describing a real difference fails, forcing its removal
# instead of leaving a stale skip that would wave through the next divergence.
KNOWN_DRIFT: dict[pathlib.Path, set[str]] = {}


# Every module in this family imports the Google Cloud SDK at module scope and
# calls sys.exit(2) if any part of it is missing — so importing one for a pure
# parsing test would take the whole suite down on a machine without the SDK.
#
# Stubbed via a meta_path finder rather than an enumerated list of submodules.
# The list version broke the moment `livekit_minutes.py` added
# `google.api_core.exceptions` (#899): a new import is a routine change, and the
# guard should not need editing to survive one. The finder claims anything under
# `google`, so it cannot fall behind.
#
# Applied unconditionally, not only-when-absent: a conditional stub makes a
# developer machine with the SDK take a different path from CI, which is how the
# first version of this file passed locally and died in CI. No assertion here
# touches the transport — the stubs only have to satisfy the import.
class _StubModule(types.ModuleType):
    def __getattr__(self, name):
        # Exception subclasses so `except gexc.NotFound:` clauses stay valid.
        cls = type(name, (Exception,), {"__module__": self.__name__})
        setattr(self, name, cls)
        return cls


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
    """Import a skill script under a unique name, with its own dir on sys.path.

    Unique name + wholesale sys.path restore: these scripts import siblings from
    their own directory, and a leaked path entry would let a later load resolve a
    sibling against an earlier script's folder. Register before exec — @dataclass
    resolves cls.__module__ through sys.modules and raises on a module that isn't
    there yet.
    """
    name = f"_lkcore_{uuid.uuid4().hex}"
    saved = list(sys.path)
    try:
        sys.path.insert(0, str(path.parent))
        with _stubbed_google():
            spec = importlib.util.spec_from_file_location(name, path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path[:] = saved


def is_family_member(path: pathlib.Path) -> bool:
    """Identify the module by what it *is*, not by its filename.

    Two marks together, so an unrelated module mentioning the log stream in a
    docstring (a SKILL.md-adjacent helper, a comment) doesn't get dragged in: the
    module binds the anchor as a constant AND defines the session aggregation.

    The underscore is optional on purpose. `livekit_minutes.py` privatises every
    name it lifted, so a predicate keyed on the public spelling would not have
    found the one copy in this family that nothing else checks — and "the guard
    didn't look there" is the exact failure this file exists to close.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    return bool(
        re.search(rf'^LIVEKIT_LOG_NAME\s*=\s*["\']{re.escape(LOG_NAME)}["\']', text, re.M)
        and re.search(r"^def _?build_sessions\s*\(", text, re.M)
    )


def api(mod, name: str):
    """Resolve a lifted name, public spelling first.

    The partial derived copy marks its lifted helpers private; they are the same
    functions, so the behavioural assertions must reach them rather than skip the
    module that allocates real per-user vendor dollars.
    """
    for attr in (name, f"_{name}"):
        if hasattr(mod, attr):
            return getattr(mod, attr)
    raise AttributeError(f"{mod.__file__} defines neither {name} nor _{name}")


# The copies, DECLARED — never derived from the discovery sweep below.
#
# This list existing separately is the entire mechanism. Deriving it from what
# `rglob` finds makes the membership assertion vacuous (`found - declared` is then
# empty by construction), which is the same shape of green-but-blind guard that let
# `report-livekit-quota` go unnamed in the first place. Discovery is compared
# *against* this list, in both directions.
BYTE_IDENTICAL = (
    PLUGINS / "eng/skills/analyze-livekit-latency/scripts/livekit_core.py",
    PLUGINS / "eng/skills/analyze-livekit-cost/scripts/livekit_core.py",
    PLUGINS / "eng/skills/report-livekit-health/scripts/livekit_core.py",
    PLUGINS / "eng/skills/check-livekit-health/scripts/livekit_core.py",
    PLUGINS / "eng/skills/report-livekit-quota/scripts/livekit_core.py",
)

DECLARED = {CANONICAL, *BYTE_IDENTICAL, *PARTIAL_DERIVED}

# What the repo actually contains, found by shape.
ALL_MEMBERS = sorted(p for p in PLUGINS.rglob("*.py") if is_family_member(p))


def entry(room: str | None, ts: str, **payload) -> dict:
    """A forwarded LiveKit log line as `.to_api_repr()` returns it."""
    body = {"room": room, **payload} if room is not None else dict(payload)
    return {"timestamp": ts, "severity": "INFO", "jsonPayload": body}


class Membership(unittest.TestCase):
    def test_family_membership_is_complete(self) -> None:
        """A new livekit_core-shaped module must be declared, not left unguarded.

        This is the assertion the CLAUDE.md `diff -q` structurally cannot make: it
        looks for the *module*, wherever it lives and whatever the file is called.
        `report-livekit-quota` went unnamed in that list for the skill's whole life
        precisely because the guard was filename-keyed.
        """
        undeclared = {p.relative_to(REPO) for p in set(ALL_MEMBERS) - DECLARED}
        self.assertEqual(
            undeclared,
            set(),
            "a livekit_core-shaped module exists outside the declared family — add it "
            "to BYTE_IDENTICAL and the CLAUDE.md copy list, or to PARTIAL_DERIVED "
            "with a reason",
        )

    def test_every_declared_copy_is_still_discoverable(self) -> None:
        """The other direction, and the one that is easy to leave out.

        A copy whose log anchor or session function changed name stops matching the
        shape predicate — so it silently drops out of every assertion below and the
        suite goes green having tested one file fewer. A declared file that
        discovery can no longer see is a failure, not a smaller family.
        """
        missing = {p.relative_to(REPO) for p in DECLARED - set(ALL_MEMBERS)}
        self.assertEqual(
            missing,
            set(),
            "a declared copy no longer matches the family shape — it lost the "
            "LIVEKIT_LOG_NAME anchor or its build_sessions definition",
        )

    def test_the_declared_copies_all_exist(self) -> None:
        for path in (CANONICAL, *PARTIAL_DERIVED, *KNOWN_DRIFT):
            with self.subTest(path=str(path.relative_to(REPO))):
                self.assertTrue(path.is_file(), "declared file is gone — update the declaration")

    def test_report_livekit_quota_is_in_the_family(self) -> None:
        """The regression this file shipped for. Pinned by name so a future
        rename or deletion is a decision, not an accident."""
        quota = PLUGINS / "eng/skills/report-livekit-quota/scripts/livekit_core.py"
        self.assertIn(quota, ALL_MEMBERS)

    def test_discovery_actually_finds_the_family(self) -> None:
        """Assert on what discovery *found*, never on the declared tuple.

        The declared list is a literal, so its length is a constant and an
        assertion about it can never fail — the same vacuous shape as deriving
        DECLARED from the sweep, caught here by review rather than by the guard.
        `ALL_MEMBERS` is the set that shrinks when the predicate stops matching.
        """
        self.assertGreaterEqual(
            len(ALL_MEMBERS),
            len(DECLARED),
            "shape discovery found fewer modules than are declared — the predicate "
            "stopped matching, and every per-module assertion below is now a no-op",
        )


class CopiesMatchCanonical(unittest.TestCase):
    """Byte-identity, with drift declared per file and per name."""

    @staticmethod
    def _assignments(text: str) -> dict[str, str]:
        return {
            m.group(1): m.group(2).rstrip()
            for m in re.finditer(r"^([A-Z_][A-Z0-9_]*)\s*=\s*(.+)$", text, re.M)
        }

    def test_every_copy_matches_except_declared_drift(self) -> None:
        want = CANONICAL.read_text(encoding="utf-8")
        want_assign = self._assignments(want)
        for path in BYTE_IDENTICAL:
            rel = str(path.relative_to(REPO))
            with self.subTest(copy=rel):
                got = path.read_text(encoding="utf-8")
                allowed = KNOWN_DRIFT.get(path, set())
                if got == want:
                    continue
                got_assign = self._assignments(got)
                differing = {
                    k
                    for k in set(want_assign) | set(got_assign)
                    if want_assign.get(k) != got_assign.get(k)
                }
                # Reconstruct the copy with the declared names reset to the
                # canonical's values: anything left over is undeclared drift,
                # including a change that isn't a module-level assignment at all.
                patched = got
                for name in allowed & differing:
                    patched = re.sub(
                        rf"^{re.escape(name)}\s*=\s*.+$",
                        f"{name} = {want_assign[name]}",
                        patched,
                        count=1,
                        flags=re.M,
                    )
                self.assertEqual(
                    patched,
                    want,
                    f"{rel} drifts from the canonical beyond its declared exceptions "
                    f"{sorted(allowed)} — propagate the canonical, or declare it",
                )

    def test_declared_drift_is_still_real(self) -> None:
        """A resolved exception must be removed, not left as a stale skip.

        A KNOWN_DRIFT entry that no longer describes a real difference silently
        widens the allowance: the next divergence on that name would be waved
        through. So resolving the drift fails here, on purpose.
        """
        want = self._assignments(CANONICAL.read_text(encoding="utf-8"))
        for path, names in KNOWN_DRIFT.items():
            got = self._assignments(path.read_text(encoding="utf-8"))
            for name in names:
                with self.subTest(copy=str(path.relative_to(REPO)), name=name):
                    self.assertNotEqual(
                        got.get(name),
                        want.get(name),
                        f"{name} now matches the canonical — delete this KNOWN_DRIFT entry",
                    )


class LogStreamAnchor(unittest.TestCase):
    """A wrong anchor returns zero entries and raises nothing."""

    def test_every_member_targets_the_forwarded_livekit_stream(self) -> None:
        for path in (CANONICAL, *BYTE_IDENTICAL, *PARTIAL_DERIVED):
            with self.subTest(module=str(path.relative_to(REPO))):
                self.assertEqual(load(path).LIVEKIT_LOG_NAME, LOG_NAME)

    def test_base_filter_scopes_the_query_to_that_stream(self) -> None:
        # The constant being right is not enough — the filter builder has to use
        # it. An unscoped filter reads the whole project's logs instead.
        for path in (CANONICAL, *BYTE_IDENTICAL, *PARTIAL_DERIVED):
            with self.subTest(module=str(path.relative_to(REPO))):
                built = api(load(path), "base_filter")('timestamp>="2026-08-30T00:00:00Z"')
                self.assertIn(f'logName="{LOG_NAME}"', built)
                self.assertIn('timestamp>="2026-08-30T00:00:00Z"', built)

    def test_this_is_not_the_gke_log_family(self) -> None:
        # `gcp.py`/`normalize.py` read the k8s cluster; conflating the two streams
        # is the mistake CLAUDE.md calls out for this section.
        self.assertNotIn("k8s", LOG_NAME)
        self.assertIn("aurie-voice-agent", LOG_NAME)


class SessionMinutes(unittest.TestCase):
    """`build_sessions` is the billing key — room grouping and the
    last−first duration proxy both move money."""

    ENTRIES = [
        entry("room-a", "2026-08-30T10:00:00Z", userId="u1", conversationId="c1"),
        entry("room-a", "2026-08-30T10:02:30Z", userId="u1"),
        entry("room-b", "2026-08-30T10:01:00Z", userId="u2"),
        entry("room-b", "2026-08-30T10:06:00Z", userId="u2"),
        # Worker-level line with no room: must not become a session.
        entry(None, "2026-08-30T10:03:00Z", message="worker registered"),
    ]

    def _members(self):
        return (CANONICAL, *BYTE_IDENTICAL, *PARTIAL_DERIVED)

    def test_one_room_is_one_session(self) -> None:
        for path in self._members():
            with self.subTest(module=str(path.relative_to(REPO))):
                sessions = api(load(path), "build_sessions")(self.ENTRIES)
                self.assertEqual(sorted(sessions), ["room-a", "room-b"])

    def test_roomless_entries_are_skipped(self) -> None:
        # Not merged under a None/"" key — that would invent a phantom session
        # whose span covers the whole window and inflate every minute total.
        for path in self._members():
            with self.subTest(module=str(path.relative_to(REPO))):
                sessions = api(load(path), "build_sessions")(self.ENTRIES)
                self.assertNotIn(None, sessions)
                self.assertNotIn("", sessions)

    def test_minutes_are_last_minus_first(self) -> None:
        for path in self._members():
            with self.subTest(module=str(path.relative_to(REPO))):
                sessions = api(load(path), "build_sessions")(self.ENTRIES)
                self.assertAlmostEqual(sessions["room-a"].minutes, 2.5, places=6)
                self.assertAlmostEqual(sessions["room-b"].minutes, 5.0, places=6)

    def test_a_single_line_session_is_zero_not_negative(self) -> None:
        for path in self._members():
            with self.subTest(module=str(path.relative_to(REPO))):
                sessions = api(load(path), "build_sessions")(
                    [entry("solo", "2026-08-30T10:00:00Z", userId="u9")]
                )
                self.assertEqual(sessions["solo"].minutes, 0.0)

    def test_out_of_order_entries_still_span_correctly(self) -> None:
        # Cloud Logging does not guarantee ordering, so the span must come from
        # min/max rather than first/last seen.
        shuffled = list(reversed(self.ENTRIES))
        for path in self._members():
            with self.subTest(module=str(path.relative_to(REPO))):
                sessions = api(load(path), "build_sessions")(shuffled)
                self.assertAlmostEqual(sessions["room-b"].minutes, 5.0, places=6)

    def test_user_id_is_carried_onto_the_session(self) -> None:
        # The per-user allocation key for LiveKit-path vendor dollars.
        for path in self._members():
            with self.subTest(module=str(path.relative_to(REPO))):
                sessions = api(load(path), "build_sessions")(self.ENTRIES)
                self.assertEqual(sessions["room-a"].user_id, "u1")
                self.assertEqual(sessions["room-b"].user_id, "u2")

    def test_every_copy_agrees_on_the_same_entries(self) -> None:
        answers = {}
        for path in self._members():
            sessions = api(load(path), "build_sessions")(self.ENTRIES)
            answers[str(path.relative_to(REPO))] = tuple(
                sorted((r, round(s.minutes, 6), s.user_id) for r, s in sessions.items())
            )
        self.assertEqual(
            len(set(answers.values())), 1, f"copies disagree on session minutes: {answers}"
        )


class QuotaConsumerSurface(unittest.TestCase):
    """`report-livekit-quota` uses a strict subset — the reason CLAUDE.md now
    distinguishes it the way it distinguishes `monitor-recommendation-health`."""

    QUOTA = PLUGINS / "eng/skills/report-livekit-quota/scripts/report.py"
    # `TruncatedReadError` joined the set in ENG-1297: the skill now reads agent-session
    # minutes from the cost store and keeps the log walk only as a fallback, which must
    # report its own truncation rather than pass a short total off as the period's.
    USES = {"AuthError", "base_filter", "build_sessions", "get_client", "list_entries",
            "range_filter", "TruncatedReadError"}
    # Present in the module, deliberately unused by the quota skill.
    UNUSED = {"latency_values", "percentiles", "LATENCY_METRICS", "short_uid", "redact"}

    def test_the_documented_subset_is_what_the_skill_actually_calls(self) -> None:
        text = self.QUOTA.read_text(encoding="utf-8")
        called = set(re.findall(r"\blk\.([A-Za-z_][A-Za-z0-9_]*)", text))
        self.assertEqual(
            called,
            self.USES,
            "report-livekit-quota's use of livekit_core changed — update the "
            "CLAUDE.md sentence that scopes it to auth + query + build_sessions",
        )
        self.assertEqual(called & self.UNUSED, set())

    def test_the_unused_names_still_exist_in_the_canonical(self) -> None:
        # Keeps UNUSED honest: a name deleted upstream would otherwise make the
        # exclusion above pass for the wrong reason.
        mod = load(CANONICAL)
        for name in self.UNUSED:
            with self.subTest(name=name):
                self.assertTrue(hasattr(mod, name))


if __name__ == "__main__":
    unittest.main()
