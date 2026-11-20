"""Release scoping in eng:report-bugsnag-errors — the CLI layer above the helper.

`tests/test_bugsnag_helper.py` pins the union itself. This file pins the layer
that decides *which versions to ask for* and *what the digest says about it*,
because both fail quietly:

- A `>=` range has to become an explicit version list before it can be queried
  at all (Bugsnag has no range operator and no OR). Getting the expansion wrong
  produces a digest that is simply narrower or wider than asked for, with every
  number internally consistent.
- A release-scoped digest that does not *say* it is release-scoped reads as the
  project's numbers. That is the original defect in a new costume: 49,762 events
  unscoped vs 166 on one version, with nothing on the page to tell them apart.

No network — the releases endpoint is stubbed at its single chokepoint.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest
from datetime import datetime, timedelta, timezone

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "plugins/eng/skills/report-bugsnag-errors/scripts"
sys.path.insert(0, str(SCRIPTS))

import format as fmt  # noqa: E402
import report  # noqa: E402
from bugsnag import BugsnagError  # noqa: E402


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


NOW = datetime.now(timezone.utc)
OLD = NOW - timedelta(days=400)


class _ReleasesClient:
    """Stands in for the releases endpoint only."""

    def __init__(self, releases):
        self.releases = releases
        self.calls = []

    def list_releases(self, project_id, *, per_page=10, max_results=30):
        self.calls.append({"per_page": per_page, "max_results": max_results})
        return self.releases


def _rel(version, released):
    return {"app_version": version, "release_time": _iso(released)}


# Newest-first, as the endpoint returns them.
FRESH = [
    _rel("1.29.1", NOW - timedelta(days=1)),
    _rel("1.29.0", NOW - timedelta(days=5)),
    _rel("1.28.0", NOW - timedelta(days=40)),
    _rel("1.27.6", NOW - timedelta(days=80)),
]
STALE = [
    _rel("0.9.0", OLD),
    _rel("0.8.0", OLD - timedelta(days=30)),
]


class ResolveScopeTest(unittest.TestCase):

    def test_a_bare_version_scopes_to_exactly_that_version(self) -> None:
        got = report._resolve_release_scope(
            _ReleasesClient(FRESH), "p", "1.29.0", "seen-in")
        self.assertEqual(got["versions"], ["1.29.0"])
        self.assertFalse(got["expanded"])
        self.assertEqual(got["field"], "version.seen_in")

    def test_ge_expands_to_that_version_and_everything_after(self) -> None:
        got = report._resolve_release_scope(
            _ReleasesClient(FRESH), "p", ">=1.28.0", "seen-in")
        self.assertEqual(got["versions"], ["1.29.1", "1.29.0", "1.28.0"])
        self.assertTrue(got["expanded"])

    def test_ge_on_the_newest_version_is_just_that_version(self) -> None:
        got = report._resolve_release_scope(
            _ReleasesClient(FRESH), "p", ">=1.29.1", "seen-in")
        self.assertEqual(got["versions"], ["1.29.1"])

    def test_expansion_follows_release_time_order_not_version_strings(self) -> None:
        # A hotfix cut from an old branch ships *after* a higher version. It
        # belongs in "and everything after", which semver arithmetic would drop.
        hotfix = [
            _rel("1.27.7", NOW - timedelta(days=2)),   # newest, lowest string
            _rel("1.29.0", NOW - timedelta(days=5)),
            _rel("1.28.0", NOW - timedelta(days=40)),
        ]
        got = report._resolve_release_scope(
            _ReleasesClient(hotfix), "p", ">=1.29.0", "seen-in")
        self.assertEqual(got["versions"], ["1.27.7", "1.29.0"])

    def test_a_version_with_several_release_records_is_listed_once(self) -> None:
        # Querying it twice would double its own event counts in the union.
        dupes = [
            _rel("1.29.0", NOW - timedelta(days=1)),
            _rel("1.29.0", NOW - timedelta(days=3)),
            _rel("1.28.0", NOW - timedelta(days=40)),
        ]
        got = report._resolve_release_scope(
            _ReleasesClient(dupes), "p", ">=1.28.0", "seen-in")
        self.assertEqual(got["versions"], ["1.29.0", "1.28.0"])

    def test_an_undated_first_record_does_not_duplicate_its_version(self) -> None:
        # Ordering used to be keyed off the release_time map, which only gains
        # an entry for a *dated* record — so a version whose first record was
        # undated was appended twice, over-counting the slice and cutting the
        # range at the wrong occurrence. Needs both conditions at once: a
        # repeated version AND a missing timestamp on its first record.
        mixed = [
            {"app_version": "1.29.0"},                       # undated, first
            _rel("1.29.0", NOW - timedelta(days=5)),         # same version, dated
            _rel("1.28.0", NOW - timedelta(days=40)),
        ]
        got = report._resolve_release_scope(
            _ReleasesClient(mixed), "p", ">=1.28.0", "seen-in")
        self.assertEqual(got["versions"], ["1.29.0", "1.28.0"])

    def test_an_undated_repeat_does_not_shift_where_the_range_cuts(self) -> None:
        mixed = [
            _rel("1.30.0", NOW - timedelta(days=1)),
            {"app_version": "1.29.0"},
            _rel("1.29.0", NOW - timedelta(days=5)),
            _rel("1.28.0", NOW - timedelta(days=40)),
        ]
        # The cut has to land *past* the duplicate for the index to shift, which
        # is what makes this discriminating rather than a restatement of the
        # test above.
        got = report._resolve_release_scope(
            _ReleasesClient(mixed), "p", ">=1.28.0", "seen-in")
        self.assertEqual(got["versions"], ["1.30.0", "1.29.0", "1.28.0"])
        self.assertEqual(len(got["versions"]), len(set(got["versions"])))

    def test_the_lookback_ceiling_is_reported_when_it_is_hit(self) -> None:
        # "not found" and "not found in the newest N" are different answers,
        # and only one of them means the version is wrong.
        many = [_rel(f"9.{i}.0", NOW - timedelta(days=i + 1))
                for i in range(report._RELEASE_LOOKBACK)]
        with self.assertRaises(BugsnagError) as ctx:
            report._resolve_release_scope(
                _ReleasesClient(many), "p", "1.0.0", "seen-in")
        self.assertIn("ceiling", str(ctx.exception))

    def test_a_short_release_list_does_not_claim_truncation(self) -> None:
        with self.assertRaises(BugsnagError) as ctx:
            report._resolve_release_scope(
                _ReleasesClient(FRESH), "p", "1.0.0", "seen-in")
        self.assertNotIn("ceiling", str(ctx.exception))

    def test_an_unknown_version_raises_and_names_the_known_ones(self) -> None:
        # It would otherwise render as a clean, empty, plausible digest.
        with self.assertRaises(BugsnagError) as ctx:
            report._resolve_release_scope(
                _ReleasesClient(FRESH), "p", "99.99.99", "seen-in")
        msg = str(ctx.exception)
        self.assertIn("99.99.99", msg)
        self.assertIn("1.29.0", msg)

    def test_an_empty_spec_raises(self) -> None:
        with self.assertRaises(BugsnagError):
            report._resolve_release_scope(_ReleasesClient(FRESH), "p", ">=", "seen-in")

    def test_releases_are_paged_at_ten(self) -> None:
        # per_page above 10 is a flat HTTP 400 on this endpoint.
        c = _ReleasesClient(FRESH)
        report._resolve_release_scope(c, "p", "1.29.0", "seen-in")
        self.assertEqual(c.calls[0]["per_page"], 10)

    def test_a_recent_release_raises_the_testflight_caveat(self) -> None:
        got = report._resolve_release_scope(
            _ReleasesClient(FRESH), "p", "1.29.0", "seen-in")
        self.assertTrue(got["testflight_caveat"])
        self.assertEqual(got["recent_versions"], ["1.29.0"])

    def test_an_old_release_does_not(self) -> None:
        got = report._resolve_release_scope(
            _ReleasesClient(STALE), "p", "0.9.0", "seen-in")
        self.assertFalse(got["testflight_caveat"])

    def test_a_release_with_no_timestamp_is_not_called_recent(self) -> None:
        # Missing metadata must not manufacture a caveat.
        undated = [{"app_version": "1.29.0"}]
        got = report._resolve_release_scope(
            _ReleasesClient(undated), "p", "1.29.0", "seen-in")
        self.assertFalse(got["testflight_caveat"])


class ScopeIsAlwaysStatedTest(unittest.TestCase):
    """A release-scoped digest that doesn't say so is the original defect."""

    @staticmethod
    def _data(scope):
        return {
            "since": "30d", "scope": "`ai-aurie-ios`",
            "rollup": {"total": {"sig": 0, "events": 0, "users": 0}, "buckets": {}},
            "new_errors": [], "new_total": 0, "top_errors": [], "by_project": [],
            "failed_projects": 0, "scope_count": 1, "release_scope": scope,
        }

    def _scope(self, **kw):
        base = {"spec": "1.29.0", "mode": "seen-in", "field": "version.seen_in",
                "versions": ["1.29.0"], "expanded": False,
                "testflight_caveat": False, "recent_versions": []}
        base.update(kw)
        return base

    def test_unscoped_output_says_nothing_about_releases(self) -> None:
        for render in (fmt.render_report, fmt.render_report_slack):
            with self.subTest(render=render.__name__):
                out = render(self._data(None))
                self.assertNotIn("Release scope", out)

    def test_the_scope_appears_in_both_renderers_even_on_an_empty_digest(self) -> None:
        # An empty *scoped* digest is exactly where the reader most needs to know
        # a filter was applied — otherwise it reads as "the project is quiet".
        for render in (fmt.render_report, fmt.render_report_slack):
            with self.subTest(render=render.__name__):
                out = render(self._data(self._scope()))
                self.assertIn("Release scope", out)
                self.assertIn("1.29.0", out)
                self.assertIn("version.seen_in", out)

    def test_the_two_modes_are_described_differently(self) -> None:
        seen = fmt.render_report(self._data(self._scope()))
        intro = fmt.render_report(self._data(self._scope(
            mode="introduced-in", field="version.introduced_in")))
        self.assertIn("seen on", seen)
        self.assertIn("first introduced in", intro)
        self.assertNotEqual(seen, intro)

    def test_a_range_reports_its_span_not_one_version(self) -> None:
        out = fmt.render_report(self._data(self._scope(
            spec=">=1.28.0", versions=["1.29.1", "1.29.0", "1.28.0"], expanded=True)))
        self.assertIn("3 versions", out)
        self.assertIn("1.28.0", out)
        self.assertIn("1.29.1", out)

    def test_the_testflight_caveat_renders_when_flagged(self) -> None:
        for render in (fmt.render_report, fmt.render_report_slack):
            with self.subTest(render=render.__name__):
                out = render(self._data(self._scope(
                    testflight_caveat=True, recent_versions=["1.29.0"])))
                self.assertIn("TestFlight", out)
        self.assertNotIn("TestFlight", fmt.render_report(self._data(self._scope())))


class FlagPairingTest(unittest.TestCase):
    """Neither half of the release pair is usable alone."""

    def test_release_without_mode_exits_nonzero_without_touching_the_network(self) -> None:
        rc = report.main(["--project", "ai-aurie-ios", "--release", "1.29.0"])
        self.assertEqual(rc, 2)

    def test_mode_without_release_exits_nonzero(self) -> None:
        rc = report.main(["--project", "ai-aurie-ios", "--release-mode", "seen-in"])
        self.assertEqual(rc, 2)

    def test_argparse_rejects_an_unknown_mode(self) -> None:
        with self.assertRaises(SystemExit):
            report.main(["--project", "x", "--release", "1.0.0",
                         "--release-mode", "seen_in"])


if __name__ == "__main__":
    unittest.main()
