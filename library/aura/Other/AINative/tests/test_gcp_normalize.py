"""Tests for the GCP-log `normalize.py`, across every copy in that family.

**Membership is the subtle part.** Six files in `plugins/` are named
`normalize.py` and only four are this module. `investigate-bugsnag-errors`
normalizes Bugsnag stacktraces and `investigate-openai-trace` normalizes stored
OpenAI completions — unrelated modules that happen to share a filename. A
name-based sweep would drag both in and either fail forever or, worse, invite
someone to "reconcile" them into each other.

So the family is identified by what the module *is* (it defines `Normalized`
with a `transaction` field), not by its filename, and the check asserts the two
non-members are excluded on purpose rather than by omission.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
CANONICAL = REPO / "plugins/eng/skills/investigate-gcp-logs/scripts/normalize.py"

# Unrelated modules that merely share the filename.
NOT_MEMBERS = {"investigate-bugsnag-errors", "investigate-openai-trace"}


def load(path: pathlib.Path):
    name = f"gcpnorm_{path.parts[-3].replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    # Register before exec: @dataclass resolves cls.__module__ through
    # sys.modules, and raises on a module that isn't there yet.
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def is_family_member(path: pathlib.Path) -> bool:
    """Identify the module by its shape, not its path."""
    text = path.read_text()
    return "Log entry normalization" in text and "transaction" in text


ALL_NAMED = sorted(REPO.glob("plugins/*/skills/*/scripts/normalize.py"))
FAMILY = [p for p in ALL_NAMED if is_family_member(p)]


def entry(**payload):
    """A Format-A Cloud Logging entry as `.to_api_repr()` returns it."""
    return {
        "timestamp": "2026-08-10T23:10:07.184587Z",
        "severity": "INFO",
        "insertId": "abc",
        "resource": {"labels": {"container_name": "user-service"}},
        "jsonPayload": payload,
    }


class MembershipTest(unittest.TestCase):
    def test_the_two_unrelated_modules_are_not_treated_as_copies(self) -> None:
        for path in ALL_NAMED:
            skill = path.parts[-3]
            with self.subTest(skill=skill):
                if skill in NOT_MEMBERS:
                    self.assertFalse(
                        is_family_member(path),
                        f"{skill}/normalize.py is a different module and must not be reconciled",
                    )
                else:
                    self.assertTrue(is_family_member(path))

    def test_the_family_has_several_members(self) -> None:
        self.assertGreaterEqual(len(FAMILY), 2)


class CopiesMatchTest(unittest.TestCase):
    def test_every_family_copy_is_byte_identical_to_the_canonical(self) -> None:
        want = CANONICAL.read_bytes()
        for path in FAMILY:
            with self.subTest(copy=str(path.relative_to(REPO))):
                self.assertEqual(path.read_bytes(), want)


class UserIdExtractionTest(unittest.TestCase):
    """Every path a Firebase uid arrives on must resolve, in every copy.

    The flat `tokenUserId` path is the one two copies had lost. It is the uid on
    user-service's verifyToken entries, so without it exactly the orphan-signal
    lines ("No profile for the token") report no user at all.
    """

    CASES = [
        ("flat userId", {"userId": "uid-a", "message": "m"}, "uid-a"),
        ("flat signInUid", {"signInUid": "uid-e", "message": "No profile for the token"}, "uid-e"),
        ("flat tokenUserId (older entries)", {"tokenUserId": "uid-b", "message": "No profile for the token"}, "uid-b"),
        ("verifiedToken.tokenUserId", {"verifiedToken": {"tokenUserId": "uid-c"}, "message": "m"}, "uid-c"),
        ("context.userId", {"context": {"userId": "uid-d"}, "message": "m"}, "uid-d"),
    ]

    def test_each_uid_path_resolves(self) -> None:
        for path in FAMILY:
            mod = load(path)
            for label, payload, want in self.CASES:
                with self.subTest(copy=path.parts[-3], case=label):
                    got = mod.normalize(entry(**payload))
                    self.assertEqual(got.user_id, want)

    def test_absent_uid_is_none_not_a_crash(self) -> None:
        for path in FAMILY:
            mod = load(path)
            with self.subTest(copy=path.parts[-3]):
                self.assertIsNone(mod.normalize(entry(message="m")).user_id)


class NormalizeShapeTest(unittest.TestCase):
    def test_format_a_fields_survive(self) -> None:
        for path in FAMILY:
            mod = load(path)
            with self.subTest(copy=path.parts[-3]):
                got = mod.normalize(entry(
                    message="Asking a question",
                    transaction="11111111-2222-3333-4444-555555555555",
                    component="dialogueModel",
                    method="ask",
                ))
                self.assertEqual(got.message, "Asking a question")
                self.assertEqual(got.transaction, "11111111-2222-3333-4444-555555555555")
                self.assertEqual(got.component, "dialogueModel")
                self.assertEqual(got.severity, "INFO")
                self.assertEqual(got.container, "user-service")

    def test_format_b_stringified_json_is_reparsed(self) -> None:
        """`CRITICAL - {...}` carries its real payload inside the message."""
        for path in FAMILY:
            mod = load(path)
            with self.subTest(copy=path.parts[-3]):
                # Format B nests the real payload — the uid lives at meta.userId,
                # not at the top level, and the message is the inner one.
                raw = entry(message='CRITICAL - {"message": "boom", "meta": {"userId": "uid-e"}}')
                got = mod.normalize(raw)
                self.assertEqual(got.format, "B")
                self.assertEqual(got.user_id, "uid-e")
                self.assertEqual(got.message, "boom")

    def test_format_b_with_unparseable_json_falls_back(self) -> None:
        """A truncated payload must degrade, not raise."""
        for path in FAMILY:
            mod = load(path)
            with self.subTest(copy=path.parts[-3]):
                got = mod.normalize(entry(message='CRITICAL - {"message": "trunc'))
                self.assertIn(got.format, ("A", "B-fallback"))


if __name__ == "__main__":
    unittest.main()
