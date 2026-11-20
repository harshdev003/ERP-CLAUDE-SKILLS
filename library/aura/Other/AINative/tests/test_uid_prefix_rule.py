#!/usr/bin/env python3
"""One `uid_prefix` derivation, three files, and no way to add a fourth unnoticed.

**The rule.** A bare `uid[:8]` assumes the first 8 characters distinguish users. For a
provider-prefixed auth id they do not — every `google:112…` yields the literal string
`google:1` — so the 8 characters are taken from *after* the provider and the provider is
kept as a readable label. Firebase-28 ids (no colon) are untouched, which is ~99.8% of
the corpus. ENG-1060.

**Why a test and not another `diff -q`.** Three files in `plugins/` derive this prefix,
under three different filenames, so no filename-keyed byte-diff can see the set. CLAUDE.md
already conceded the gap in prose — its `uid_prefix` section ended with "diff the function
bodies by eye rather than the files" — and a by-eye instruction is not a guard. It is also
the *same pair of files* that produced ENG-1132, where a decision propagated perfectly to
every copy of one filename and never reached the reimplementation next door. So membership
is decided by **shape** (a module defining `uid_prefix`), the copies are held to the same
answers, and `test_family_membership_is_complete` fails the moment a fourth appears.

**The load-bearing assertion is the collapse one.** Two distinct provider ids must not map
to one string. Every other property here (Firebase ids unchanged, prefix never the whole
uid) can hold while that one is broken — that is exactly what shipped.

Stdlib `unittest`, no network, no keys. Modules are imported with their transports intact
but never called; only the pure derivation runs.
"""
from __future__ import annotations

import importlib.util
import ast
import pathlib
import tempfile
import re
import sys
import unittest
import uuid

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PLUGINS = REPO_ROOT / "plugins"

# Every file in `plugins/` that derives a uid display prefix which can receive a
# provider-prefixed id. Discovered by shape, not filename — see the module docstring.
# Keep in step with `test_family_membership_is_complete`, which fails on a new one.
DERIVERS = [
    PLUGINS / "data/skills/report-conversation-patterns/scripts/sample.py",  # canonical
    PLUGINS / "data/skills/harvest-conversation-patterns/scripts/fetch_sweep_analyses.py",
    PLUGINS / "data/skills/investigate-pmf-survey/scripts/fetch_pmf_responses.py",
    PLUGINS / "data/skills/harvest-cancellation-feedback/scripts/harvest.py",
    # The rating-filter family labels allowlist drops `allowlist:<prefix>`, and the
    # accounts that path exists for are `google:` logins (ENG-1436).
    PLUGINS / "data/skills/investigate-user-rating/scripts/fetch_ratings.py",
    PLUGINS / "data/skills/run-feedback-analysis-cadence/scripts/fetch_ratings.py",
    PLUGINS / "data/skills/report-conversation-patterns/scripts/fetch_ratings.py",
    PLUGINS / "data/skills/chart-monthly-ratings/scripts/chart_ratings.py",
    # Names the exported user on the summary line and in the default filename.
    PLUGINS / "eng/skills/export-user-journal/scripts/export_journal.py",
    # The userIdPrefix and file names of the data-request export files.
    PLUGINS / "eng/skills/query-user-export-data/scripts/export_sql.py",
    PLUGINS / "eng/skills/export-user-conversations/scripts/export_conversations.py",
    # The data-request exports for insights and affirmations (one canonical copy).
    PLUGINS / "eng/skills/export-user-insights/scripts/export_ai_items.py",
    PLUGINS / "eng/skills/export-user-affirmations/scripts/export_ai_items.py",
    PLUGINS / "eng/skills/export-user-memory/scripts/export_ai_items.py",
    PLUGINS / "eng/skills/export-user-profile/scripts/export_profile.py",
    # The journal owner on fetch_journal.py's summary line.
    PLUGINS / "data/skills/analyze-user-journal/scripts/fetch_journal.py",
]

# Deliberately OUT of this family, with a reason rather than a silent non-match — an
# unexplained new hit must still fail `test_family_membership_is_complete`.
#
# `eng:investigate-voice-issue`'s `correlate.uid_prefix` is a display-eliding helper: it
# takes a `length` parameter and appends an ellipsis, and its inputs are Firebase uids read
# off token claims in GCP logs, not the provider-prefixed auth ids that k8bot feedback
# footers carry. It joins this family the moment a provider id can reach it.
NOT_PROVIDER_FACING = {
    PLUGINS / "eng/skills/investigate-voice-issue/scripts/correlate.py",
}

# Two real allowlist ids from `Team/Internal Accounts` — the accounts the
# `@aurahealth.io` / role rules cannot catch, and the ones a collapse makes
# indistinguishable in a PMF `internal_excluded` block.
DANIEL = "google:111555896239269076429"
STEVE = "google:112487695856241402886"
FIREBASE = "Za0DIgYucXRZduFOOlHKvjPbymV2"
PUSH_KEY = "-NxK3pQr7sTuVwXyZ012"


def load(path: pathlib.Path):
    """Import a skill script under a unique name, with its own dir on sys.path.

    Unique name + wholesale sys.path restore: these scripts import siblings from their
    own directory, and a leaked path entry would let a later load resolve a sibling
    against an earlier script's folder.
    """
    name = f"_uidp_{uuid.uuid4().hex}"
    saved = list(sys.path)
    try:
        sys.path.insert(0, str(path.parent))
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path[:] = saved


class DerivationAgrees(unittest.TestCase):
    """Every copy must answer identically — a copy can be byte-identical to the
    wrong canonical, and can also diverge while still parsing."""

    def test_distinct_provider_ids_do_not_collapse(self):
        for path in DERIVERS:
            with self.subTest(script=str(path.relative_to(REPO_ROOT))):
                fn = load(path).uid_prefix
                a, b = fn(DANIEL), fn(STEVE)
                self.assertNotEqual(
                    a, b,
                    "two distinct provider ids collapse onto one prefix — this is the "
                    "ENG-1060 defect the derivation exists to prevent",
                )
                self.assertNotEqual(a, "google:1")

    def test_provider_label_is_kept_and_eight_chars_follow_it(self):
        for path in DERIVERS:
            with self.subTest(script=str(path.relative_to(REPO_ROOT))):
                fn = load(path).uid_prefix
                self.assertEqual(fn(DANIEL), "google:11155589")
                self.assertEqual(fn(STEVE), "google:11248769")

    def test_firebase_ids_are_untouched(self):
        # ~99.8% of the corpus. The fix must cost no churn on the common case.
        for path in DERIVERS:
            with self.subTest(script=str(path.relative_to(REPO_ROOT))):
                fn = load(path).uid_prefix
                self.assertEqual(fn(FIREBASE), FIREBASE[:8])
                self.assertEqual(fn(PUSH_KEY), PUSH_KEY[:8])

    def test_empty_and_none_are_safe(self):
        for path in DERIVERS:
            with self.subTest(script=str(path.relative_to(REPO_ROOT))):
                fn = load(path).uid_prefix
                self.assertEqual(fn(""), "")
                self.assertEqual(fn(None), "")

    def test_prefix_is_never_the_whole_uid(self):
        # PII: the return value is a prefix. A copy that returned the input would
        # pass every positive assertion above for the Firebase case.
        for path in DERIVERS:
            with self.subTest(script=str(path.relative_to(REPO_ROOT))):
                fn = load(path).uid_prefix
                for uid in (DANIEL, STEVE, FIREBASE):
                    self.assertNotEqual(fn(uid), uid)
                    self.assertLess(len(fn(uid)), len(uid))

    def test_all_copies_return_the_same_answer(self):
        for uid in (DANIEL, STEVE, FIREBASE, PUSH_KEY, "", "no-colon-short"):
            answers = {str(p.relative_to(REPO_ROOT)): load(p).uid_prefix(uid) for p in DERIVERS}
            self.assertEqual(
                len(set(answers.values())), 1,
                f"copies disagree on {uid!r}: {answers}",
            )


class FamilyMembership(unittest.TestCase):
    def _uid_prefix_files(self):
        for path in sorted(PLUGINS.rglob("*.py")):
            text = path.read_text(encoding="utf-8", errors="replace")
            if re.search(r"^\s*def uid_prefix\s*\(", text, re.M):
                yield path

    def test_family_membership_is_complete(self):
        """A new uid-prefix derivation must be declared, not left unguarded.

        This is the assertion the CLAUDE.md `diff -q` checks structurally cannot make:
        it looks for the *derivation*, wherever it lives and whatever the file is called.
        """
        found = set(self._uid_prefix_files())
        declared = set(DERIVERS) | NOT_PROVIDER_FACING
        undeclared = {p.relative_to(REPO_ROOT) for p in found - declared}
        self.assertEqual(
            undeclared, set(),
            "a uid_prefix derivation exists outside the tested family — add it to "
            "DERIVERS (and to CLAUDE.md), or to NOT_PROVIDER_FACING with a reason",
        )
        self.assertEqual({p.relative_to(REPO_ROOT) for p in declared - found}, set())

    def test_no_bare_truncation_survives_in_the_family(self):
        """`uid[:8]` was the collapsing form. It must not reappear in these files."""
        for path in DERIVERS:
            with self.subTest(script=str(path.relative_to(REPO_ROOT))):
                text = path.read_text(encoding="utf-8")
                # The canonical's own `return uid[:8]` is the no-colon branch and is
                # correct; what must not exist is a *call site* re-truncating a uid.
                call_sites = re.findall(r"""\be\[["']uid["']\]\[:\d+\]""", text)
                self.assertEqual(
                    call_sites, [],
                    "a call site truncates a uid directly instead of calling "
                    "uid_prefix() — that is where the collapse ships from",
                )



# ---------------------------------------------------------------------------
# The hole the derivation-shaped guard above cannot see.
#
# `test_family_membership_is_complete` discovers members by looking for a module
# that *defines* `uid_prefix`. A writer that never defines one — it just inlines
# `uid[:8]` — is invisible to it. That is exactly how
# `harvest-cancellation-feedback` sat outside the family from the day it shipped,
# writing a bare truncation into a durable Notion column (ENG-1324).
#
# A blanket "no `uid[:8]` anywhere" sweep is the wrong instrument: 44 call sites
# in `plugins/` truncate a uid, and almost all are ephemeral — log lines, console
# output, a filename. Failing on those makes a permanently-red guard, which
# CLAUDE.md warns is a guard everyone learns to ignore.
#
# The property that actually matters is narrower: does the truncated value get
# **persisted as a record field**? A console warning that collapses two Google
# users is noise; a Notion column that does is a durable wrong answer.
# ---------------------------------------------------------------------------

UIDISH = re.compile(r"^(uid|user_id|userId|uid8|_uid)$")
NOTION_PROPERTY_BUILDERS = {"make_rich_text", "make_title"}


def _truncates_uid(node) -> bool:
    """`uid[:N]` / `x["uid"][:N]` / `.get("uid", "")[:N]`, not routed through uid_prefix()."""
    for n in ast.walk(node):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "uid_prefix":
            return False
    for n in ast.walk(node):
        if isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Slice):
            base, name = n.value, None
            if isinstance(base, ast.Name):
                name = base.id
            elif isinstance(base, ast.Subscript) and isinstance(base.slice, ast.Constant):
                if isinstance(base.slice.value, str):
                    name = base.slice.value
            elif isinstance(base, ast.Call) and isinstance(base.func, ast.Attribute):
                if base.func.attr == "get" and base.args:
                    a0 = base.args[0]
                    if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                        name = a0.value
            if name and UIDISH.match(name):
                return True
    return False


def _tainted_names(fn) -> set:
    """Locals carrying a bare uid truncation, to a fixed point.

    One hop is not enough. The real shape assigns `uid8 = entry["uid"][:8]` and then
    builds the row title as `f"{uid8} - ..."`, so the *title* — also a persisted
    field — is a second hop from the truncation and a one-hop check waves it through.
    """
    assigns = []
    for n in ast.walk(fn):
        if isinstance(n, ast.Assign):
            targets, value = n.targets, n.value
        elif isinstance(n, ast.AnnAssign) and n.value is not None:
            targets, value = [n.target], n.value
        else:
            continue
        names = [x.id for x in targets if isinstance(x, ast.Name)]
        if names:
            assigns.append((names, value))
    out = {n for names, value in assigns for n in names if _truncates_uid(value)}
    changed = True
    while changed:
        changed = False
        for names, value in assigns:
            if any(n in out for n in names):
                continue
            if any(isinstance(x, ast.Name) and x.id in out for x in ast.walk(value)):
                out.update(names)
                changed = True
    return out


def persisted_truncations(path: pathlib.Path) -> list:
    """Every place this module writes a bare-truncated uid into a Notion property."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    hits = []
    for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        tainted = _tainted_names(fn)

        def flag(expr, label, lineno):
            if _truncates_uid(expr):
                hits.append((lineno, f"{label} — inline truncation"))
                return
            for n in ast.walk(expr):
                if isinstance(n, ast.Name) and n.id in tainted:
                    hits.append((lineno, f"{label} — via `{n.id}`"))
                    return

        for n in ast.walk(fn):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
                if n.func.id in NOTION_PROPERTY_BUILDERS:
                    for a in n.args:
                        flag(a, f"{n.func.id}(...)", n.lineno)
            if isinstance(n, ast.Dict):
                for k, v in zip(n.keys, n.values):
                    if isinstance(k, ast.Constant) and isinstance(k.value, str):
                        flag(v, f'property "{k.value}"', getattr(v, "lineno", n.lineno))
    return hits


class TestNoBareTruncationReachesADurableColumn(unittest.TestCase):
    """A uid prefix written into Notion must come from `uid_prefix()`.

    This is the half the derivation sweep misses. It is scoped to *persisted*
    values on purpose — see the comment above for why a blanket sweep would be
    permanently red and therefore worthless.
    """

    def test_no_plugin_script_persists_a_bare_uid_truncation(self):
        offenders = []
        for path in sorted(PLUGINS.rglob("scripts/*.py")):
            if "__pycache__" in str(path):
                continue
            for lineno, why in persisted_truncations(path):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}  {why}")
        self.assertEqual(
            sorted(set(offenders)), [],
            "a bare uid truncation reaches a Notion property. Route it through "
            "`uid_prefix()` — a provider-prefixed id collapses every `google:112…` "
            "onto `google:1`, and in a durable column that is two users reading as one:\n  "
            + "\n  ".join(sorted(set(offenders))),
        )

    def test_the_detector_sees_the_shape_it_was_written_for(self):
        """Guard against the detector going green for the wrong reason.

        The first version of this check anchored on the property-write expression
        alone and reported zero — including against the original defect, because the
        truncation happened one assignment earlier. A detector that cannot fail is
        not a detector, so the shape is pinned here rather than trusted.
        """
        src = (
            "def write_row(entry):\n"
            "    uid8 = entry['uid'][:8]\n"
            "    title = f'{uid8} - x'\n"
            "    return {'title': make_title(title), 'User ID': make_rich_text(uid8)}\n"
        )
        tmp = pathlib.Path(tempfile.mkdtemp()) / "scripts_probe.py"
        tmp.write_text(src, encoding="utf-8")
        found = persisted_truncations(tmp)
        labels = {why for _, why in found}
        self.assertTrue(
            any("uid8" in l for l in labels),
            f"detector missed the direct property write: {labels}",
        )
        self.assertTrue(
            any("title" in l for l in labels),
            f"detector missed the second-hop title write: {labels}",
        )

    def test_the_detector_does_not_fire_on_an_ephemeral_truncation(self):
        """A log line is not a record. Firing on one is what makes a guard ignored."""
        src = (
            "def go(uid):\n"
            "    print(f'[warn] failed for {uid[:8]}')\n"
            "    return {'User ID': make_rich_text(uid_prefix(uid))}\n"
        )
        tmp = pathlib.Path(tempfile.mkdtemp()) / "scripts_probe2.py"
        tmp.write_text(src, encoding="utf-8")
        self.assertEqual(persisted_truncations(tmp), [])


if __name__ == "__main__":
    unittest.main()
