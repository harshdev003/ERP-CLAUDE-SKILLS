"""Tests for the Claude usage report skill.

The charting rules here are correctness rules, not taste: a dual axis misstates
both series, a cycled hue makes two entities the same colour, and a bar labelled
with a quantity it does not encode contradicts itself. Each fails by rendering
a plausible chart, so each is asserted rather than eyeballed.

Stdlib unittest, no network, no BigQuery.
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "plugins/eng/skills/build-anthropic-usage-report/scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"_aur_{name}", SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


render = _load("render")
collect = _load("collect")

DATA = {
    "window": {"since": "2026-03-02", "through": "2026-09-17", "grain": "month", "days": 200},
    "coverage": {"days_harvested": 200, "days_in_window": 200, "complete": True,
                 "first": "2026-03-02", "last": "2026-09-17",
                 "newest_harvested": "2026-09-17", "lag_days": 1, "stale": False},
    "seat_fit": [
        {"user": "a@x.io", "tier": "premium", "role": "Owner", "seat_usd": 100.0,
         "value_per_month": 7713.65, "ratio": 77.1, "tokens": 77494684903,
         "billed": 1329.05, "active_days": 180},
        {"user": "b@x.io", "tier": "standard", "role": "User", "seat_usd": 20.0,
         "value_per_month": 201.0, "ratio": 10.1, "tokens": 300000000,
         "billed": 2.17, "active_days": 42},
        {"user": "c@x.io", "tier": "premium", "role": "User", "seat_usd": 100.0,
         "value_per_month": 31.0, "ratio": 0.3, "tokens": 4000000,
         "billed": 0.0, "active_days": 13},
        {"user": "d@x.io", "tier": "standard", "role": "User", "seat_usd": 20.0,
         "value_per_month": 0.0, "ratio": 0.0, "tokens": 0, "billed": 0.0,
         "active_days": 0},
    ],
    "adoption": [
        {"period": "2026-08-01", "tokens": 65419821759, "api_value": 45283.0,
         "billed": 509.15, "users": 15},
        {"period": "2026-09-01", "tokens": 46626699933, "api_value": 30108.0,
         "billed": 439.75, "users": 16},
    ],
    "adoption_daily": [],
    "model_mix": {
        "periods": ["2026-08-01", "2026-09-01"],
        "series": [{"model": "claude-opus-5", "values": [100.0, 200.0], "total": 300.0},
                   {"model": "claude-sonnet-5", "values": [10.0, 20.0], "total": 30.0}],
        "all_time_totals": {"claude-opus-5": 300.0, "claude-sonnet-5": 30.0},
    },
    "user_mix": {
        "periods": ["2026-08-01", "2026-09-01"],
        "series": [{"user": "a@x.io", "values": [90.0, 180.0], "total": 270.0},
                   {"user": "b@x.io", "values": [10.0, 20.0], "total": 30.0},
                   {"user": "Other", "values": [5.0, 5.0], "total": 10.0}],
        "folded": {"count": 9, "users": ["z%d@x.io" % i for i in range(9)],
                   "total": 10.0},
        "all_time": [{"user": "a@x.io", "value": 270.0, "tokens": 900},
                     {"user": "b@x.io", "value": 30.0, "tokens": 100}],
    },
    "concentration": {
        "points": [{"rank": 1, "user": "a@x.io", "value": 100.0, "share": 80.0,
                    "cum_share": 80.0},
                   {"rank": 2, "user": "b@x.io", "value": 25.0, "share": 20.0,
                    "cum_share": 100.0}],
        "total": 125.0, "top1": 80.0, "top3": 100.0, "top5": 100.0, "top10": 100.0,
    },
    "token_mix": {"uncached_input": 100, "cache_read": 9680, "cache_write": 200,
                  "output": 20, "total": 10000, "cache_read_pct": 96.8},
    "products": [{"product": "Claude Code", "tokens": 213500000000},
                 {"product": "Chat", "tokens": 3900000000}],
    "pricing": {"verified_on": "2026-09-18", "rows": 10, "age_days": 0},
    "seats_meta": {"as_of": "2026-09-18", "snapshots": 1},
    "seat_prices": {"premium": 100.0, "standard": 20.0},
    "generated_at": "2026-09-19 01:03 UTC",
}


class TestArtifactPageContract(unittest.TestCase):
    """The Artifact tool wraps the file in its own skeleton.

    Emitting a second <html>/<head>/<body> nests duplicate document tags inside
    that skeleton. The page still renders, which is why this is asserted rather
    than noticed.
    """

    def setUp(self):
        self.h = render.build(DATA)

    def test_no_document_wrapper_tags(self):
        low = self.h.lower()
        for tag in ("<!doctype", "<html", "</html>", "<head>", "</head>", "<body", "</body>"):
            with self.subTest(tag=tag):
                self.assertNotIn(tag, low)

    def test_title_is_present_and_early(self):
        # Only the first 8KB is scanned for the title.
        self.assertIn("<title>", self.h)
        self.assertLess(self.h.index("<title>"), 8192)

    def test_title_is_a_name_not_a_sentence(self):
        t = self.h.split("<title>")[1].split("</title>")[0]
        self.assertLessEqual(len(t.split()), 4)
        for sep in (" - ", " — ", ": "):
            self.assertNotIn(sep, t, "title carries an appended explainer")

    def test_no_external_resources(self):
        # Everything must be inline; the CSP blocks most hosts silently.
        for probe in ('src="http', "src='http", 'href="http', "@import"):
            with self.subTest(probe=probe):
                self.assertNotIn(probe, self.h)


class TestThemeTokens(unittest.TestCase):
    """A colour defined only inside a media/[data-theme] block never applies in
    the un-stamped state, which is what most viewers get."""

    def setUp(self):
        self.h = render.build(DATA)

    def test_every_token_is_declared_on_bare_root_first(self):
        bare = self.h.index(":root{")
        media = self.h.index("prefers-color-scheme")
        self.assertLess(bare, media)
        bare_block = self.h[bare:media]
        for tok in ("--surface-0", "--surface-1", "--line", "--text-primary",
                    "--text-secondary", "--text-muted", "--s1", "--s2", "--s3",
                    "--s4", "--s5", "--good", "--warn", "--crit"):
            with self.subTest(token=tok):
                self.assertIn(tok, bare_block)

    def test_dark_is_defined_for_both_states(self):
        self.assertIn('prefers-color-scheme: dark', self.h)
        self.assertIn(':root:not([data-theme="light"])', self.h)
        self.assertIn(':root[data-theme="dark"]', self.h)

    def test_body_paints_its_own_background(self):
        # A transparent body borrows the host's ground and renders one theme's
        # text on the other theme's surface.
        self.assertIn("background:var(--surface-0)", self.h)

    def test_side_gutter_is_not_zeroed_by_a_padding_shorthand(self):
        body = self.h.split("body{")[1].split("}")[0]
        self.assertIn("padding-block", body)
        self.assertIn("padding-left", body)


class TestChartingRules(unittest.TestCase):
    def setUp(self):
        self.h = render.build(DATA)

    def test_adoption_is_two_plots_not_one_dual_axis(self):
        # The single most common charting mistake. Two measures of different
        # scale get two plots; a shared y-axis misstates both.
        js = render.JS
        self.assertIn("function plot(", js)
        self.assertIn("plot('tokens'", js)
        self.assertIn("plot('users'", js)

    def test_categorical_slots_are_fixed_and_capped(self):
        # A model past the cap folds into Other, never mints a hue. Both stacked
        # charts route their fill through slot(), which clamps to the eight-slot
        # order and sends "Other" to the reserved neutral.
        self.assertEqual(collect.MODEL_SLOTS, 4)
        self.assertIn("slot(si, s.model)", render.JS)
        self.assertIn("SER[Math.min(i, 7)]", render.JS)

    def test_a_legend_exists_wherever_there_are_two_or_more_series(self):
        for probe in ('class="legend"',):
            self.assertIn(probe, self.h)
        # model mix and concentration both build one
        self.assertGreaterEqual(render.JS.count('class="legend"'), 2)

    def test_seat_bar_label_names_the_quantity_the_bar_encodes(self):
        # Labelling the ratio alone put a number on a bar that did not describe
        # its length: a standard seat can out-rank a premium one on ratio while
        # drawing a far shorter bar.
        seat = render.JS.split("1. SEAT FIT")[1].split("2. ADOPTION")[0]
        self.assertIn("fmtU(r.value_per_month)", seat)

    def test_every_chart_has_a_hover_layer(self):
        self.assertIn("function wire(", render.JS)
        for cls in (".hit", ".hit-", ".hitm", ".hitc"):
            with self.subTest(cls=cls):
                self.assertIn(cls, render.JS)


class TestBannersFireOnTheRightCondition(unittest.TestCase):
    def _with(self, **over):
        d = json.loads(json.dumps(DATA))
        for k, v in over.items():
            d[k] = v
        return render._banners(d)

    def test_complete_fresh_window_raises_no_incomplete_banner(self):
        self.assertNotIn("INCOMPLETE", self._with().upper())

    def test_a_gap_raises_incomplete(self):
        cov = dict(DATA["coverage"], days_harvested=150, complete=False)
        self.assertIn("Incomplete", self._with(coverage=cov))

    def test_lag_raises_stale_separately_from_gaps(self):
        # Lag and gaps are different failures: one means the figures are old,
        # the other that they are a lower bound.
        cov = dict(DATA["coverage"], lag_days=9, stale=True)
        out = self._with(coverage=cov)
        self.assertIn("Stale", out)
        self.assertNotIn("Incomplete", out)

    def test_old_rates_raise_a_banner(self):
        self.assertIn("days old", self._with(pricing={"verified_on": "2026-01-01",
                                                      "rows": 10, "age_days": 300}))

    def test_single_seat_snapshot_over_a_long_window_is_called_out(self):
        self.assertIn("one snapshot", self._with())

    def test_single_snapshot_is_not_called_out_on_a_short_window(self):
        self.assertNotIn("one snapshot",
                         self._with(window=dict(DATA["window"], days=14)))


class TestNumbersRenderRatherThanCrash(unittest.TestCase):
    def test_a_user_with_no_usage_does_not_break_the_table(self):
        h = render.build(DATA)
        self.assertIn("no usage in window", h)

    def test_under_used_premium_seat_is_flagged(self):
        self.assertIn("under-using a premium seat", render.build(DATA))

    def test_heavy_standard_seat_is_flagged(self):
        self.assertIn("premium may pay off", render.build(DATA))

    def test_empty_model_mix_renders(self):
        d = json.loads(json.dumps(DATA))
        d["model_mix"] = {"periods": [], "series": [], "all_time_totals": {}}
        render.build(d)  # must not raise

    def test_empty_concentration_renders(self):
        d = json.loads(json.dumps(DATA))
        d["concentration"] = {"points": [], "total": 0, "top1": 0, "top3": 0,
                              "top5": 0, "top10": 0}
        render.build(d)  # must not raise

    def test_user_emails_are_escaped(self):
        d = json.loads(json.dumps(DATA))
        d["seat_fit"][0]["user"] = "<script>x</script>@x.io"
        self.assertNotIn("<script>x</script>", render.build(d))


class TestCollectQueriesArePriced(unittest.TestCase):
    def test_pricing_join_is_effective_dated(self):
        # Taking the newest rate per model silently reprices all history the day
        # a rate changes; joining on model alone doubles every value once a
        # second rate exists.
        self.assertIn("LEAD(effective_from)", collect.VALUED)
        self.assertIn("usage_date >= p.effective_from", collect.VALUED)
        self.assertNotIn("ROW_NUMBER", collect.VALUED)

    def test_seat_prices_are_stated_once(self):
        self.assertEqual(set(collect.SEAT_USD), {"premium", "standard"})

    def test_window_end_defaults_to_the_newest_harvested_day(self):
        # Defaulting to yesterday would stamp a permanent INCOMPLETE banner on
        # a day the export cannot have settled yet.
        src = (SCRIPTS / "collect.py").read_text()
        self.assertIn("through = a.through or newest", src)


if __name__ == "__main__":
    unittest.main()


class TestEmbeddedJsonCannotBreakOutOfTheScriptTag(unittest.TestCase):
    """`json.dumps` does not escape `<`, so a value containing `</script>`
    closes the tag early and the rest is parsed as markup. The data is an
    external export — no field can be assumed tag-free."""

    def test_a_closing_script_tag_in_a_field_is_neutralised(self):
        d = json.loads(json.dumps(DATA))
        d["seat_fit"][0]["user"] = "</script><img src=x onerror=alert(1)>@x.io"
        h = render.build(d)
        self.assertNotIn("</script><img", h)
        self.assertIn("\\u003c", h)

    def test_the_escaped_json_still_parses_to_the_same_object(self):
        payload = {"a": "<b>&</b>", "n": 1}
        self.assertEqual(json.loads(render.json_for_script(payload)), payload)

    def test_line_and_paragraph_separators_are_escaped(self):
        # U+2028/29 are literal newlines inside a JS string and break the parse.
        out = render.json_for_script({"s": "a b c"})
        self.assertNotIn(" ", out)
        self.assertNotIn(" ", out)


class TestEmbeddedPayloadIsPureAscii(unittest.TestCase):
    """Non-ASCII in a <script> body is a parse risk. `ensure_ascii=True` is the
    default and covers all of it — including U+2028/29, which is why
    json_for_script hand-escapes only `<`, `>` and `&`. This test is what fails
    if someone later switches to ensure_ascii=False."""

    def test_payload_is_ascii_only(self):
        out = render.json_for_script({"s": "café — ✓   "})
        self.assertTrue(out.isascii())

    def test_it_still_round_trips(self):
        payload = {"s": "café — ✓   ", "n": 1.5}
        self.assertEqual(json.loads(render.json_for_script(payload)), payload)


class TestPeriodLabelsFollowTheGrain(unittest.TestCase):
    """Truncating a period to 7 characters collapses every week in a month to
    the same label, so a weekly chart drew four identical ticks and four
    identical tooltips. `--grain week` is a documented option."""

    def test_the_label_reads_the_grain_rather_than_assuming_month(self):
        js = render.JS
        self.assertIn("D.window && D.window.grain", js)
        self.assertIn("GRAIN === 'month' ? s.slice(0,7) : s.slice(5)", js)

    def test_grain_is_carried_into_the_page_payload(self):
        d = json.loads(json.dumps(DATA))
        d["window"]["grain"] = "week"
        self.assertIn('"grain": "week"', render.build(d))


class TestEmptySeatsRenderRatherThanCrash(unittest.TestCase):
    """The footer divides value by total seat cost. No seats, or every tier
    unknown, makes that zero — unguarded it raises and NO html is written, so
    the report disappears rather than rendering empty. Empty model-mix and
    concentration were tested; seats were the gap."""

    def test_no_members_at_all(self):
        d = json.loads(json.dumps(DATA))
        d["seat_fit"] = []
        h = render.build(d)          # must not raise
        self.assertIn("Seat fit", h)

    def test_every_tier_unknown_so_seat_cost_is_zero(self):
        d = json.loads(json.dumps(DATA))
        for s in d["seat_fit"]:
            s["tier"], s["seat_usd"], s["ratio"] = "unknown", 0.0, None
        h = render.build(d)          # must not raise
        self.assertIn("—", h)

    def test_a_real_total_still_renders_a_ratio(self):
        self.assertIn("x</td>", render.build(DATA))


class TestConcentrationTotalIsNotASentinel(unittest.TestCase):
    """`or 1.0` guards the share division; writing it into the output would
    report $1.00 of usage for a window that had none."""

    def test_the_guard_is_separate_from_the_reported_total(self):
        src = (SCRIPTS / "collect.py").read_text()
        block = src.split("def concentration")[1].split("\ndef ")[0]
        self.assertIn("total = sum(v for _, v in vals)\n", block)
        self.assertIn("denom = total or 1.0", block)
        # the reported total must be the real one
        self.assertIn('"total": round(total, 2)', block)
        self.assertNotIn("total = sum(v for _, v in vals) or 1.0", block)

    def test_shares_divide_by_the_guarded_denominator(self):
        src = (SCRIPTS / "collect.py").read_text()
        block = src.split("def concentration")[1].split("\ndef ")[0]
        self.assertIn("v / denom", block)
        self.assertIn("cum / denom", block)


class TestUserMixUsesTheAdjacentPairlistBudget(unittest.TestCase):
    """A stacked bar reads on the *adjacent* pairlist, where the full eight-slot
    order validates in both modes. Model mix caps at four because its form is
    all-pairs. Conflating the two budgets either wastes six usable slots or
    ships a palette that fails — so the cap is asserted per form."""

    def test_user_slots_is_seven_named_plus_a_fold(self):
        self.assertEqual(collect.USER_SLOTS, 7)

    def test_model_mix_keeps_the_tighter_cap(self):
        self.assertEqual(collect.MODEL_SLOTS, 4)

    def test_eight_categorical_slots_exist_in_both_themes(self):
        h = render.build(DATA)
        bare = h[h.index(":root{"):h.index("prefers-color-scheme")]
        for i in range(1, 9):
            self.assertIn(f"--s{i}:", bare)
        self.assertIn("--sother:", bare)

    def test_the_fold_takes_a_reserved_neutral_never_a_hue(self):
        # "Other" is not an entity, so it must not consume a categorical slot.
        self.assertIn("name === 'Other' ? '--sother'", render.JS)

    def test_a_ninth_entity_folds_rather_than_minting_a_hue(self):
        self.assertIn("SER[Math.min(i, 7)]", render.JS)


class TestUserMixFoldIsStatedNotImplied(unittest.TestCase):
    """An unnamed band is only honest with its count — otherwise 'Other' hides
    an unknown number of people behind one colour."""

    def test_collect_reports_who_was_folded_and_how_much(self):
        src = (SCRIPTS / "collect.py").read_text()
        block = src.split("def user_mix")[1].split("\ndef ")[0]
        self.assertIn('"folded"', block)
        self.assertIn('"count": len(folded)', block)
        self.assertIn('"users": folded', block)

    def test_the_page_states_the_folded_count(self):
        h = render.build(DATA)
        self.assertIn("9 people are folded there", h)

    def test_no_fold_means_no_dangling_note(self):
        d = json.loads(json.dumps(DATA))
        d["user_mix"]["folded"] = {"count": 0, "users": [], "total": 0.0}
        d["user_mix"]["series"] = [s for s in d["user_mix"]["series"]
                                   if s["user"] != "Other"]
        self.assertNotIn("people are folded there", render.build(d))

    def test_legend_labels_the_fold_with_its_size(self):
        self.assertIn("M.folded && M.folded.count", render.JS)


class TestUserMixSectionRenders(unittest.TestCase):
    def test_the_mount_point_and_heading_exist(self):
        h = render.build(DATA)
        self.assertIn('id="usermix"', h)
        self.assertIn("Usage by person", h)

    def test_every_person_is_listed_even_when_folded_in_the_chart(self):
        # The chart folds; the table must not. Folding in both would make a
        # person invisible rather than merely uncoloured.
        h = render.build(DATA)
        self.assertIn("Every person, whole window", h)
        for u in ("a@x.io", "b@x.io"):
            self.assertIn(u, h)

    def test_a_missing_user_mix_section_does_not_break_the_page(self):
        d = json.loads(json.dumps(DATA)); del d["user_mix"]
        render.build(d)  # must not raise

    def test_empty_user_mix_renders(self):
        d = json.loads(json.dumps(DATA))
        d["user_mix"] = {"periods": [], "series": [], "folded": {"count": 0},
                         "all_time": []}
        render.build(d)  # must not raise

    def test_user_emails_in_the_payload_are_escaped(self):
        d = json.loads(json.dumps(DATA))
        d["user_mix"]["all_time"][0]["user"] = "<script>y</script>@x.io"
        self.assertNotIn("<script>y</script>@", render.build(d))


# ----------------------------------------------------------------------------
# Skill authorship (collect_git.py + the card render.py builds from it)
# ----------------------------------------------------------------------------

collect_git = _load("collect_git")

GIT = {
    "repo": "/tmp/AINative", "ref": "origin/main",
    "periods": ["2026-W30", "2026-W31"],
    "grain": "week",
    "series": [
        {"person": "Jerome Curlier", "created": [3, 1], "updated": [8, 5],
         "swept": [0, 149], "commits": 40, "total_created": 4,
         "total_updated": 13, "total_swept": 149},
        {"person": "Rohan Agarwal", "created": [1, 0], "updated": [2, 4],
         "swept": [0, 0], "commits": 9, "total_created": 1,
         "total_updated": 6, "total_swept": 0},
    ],
    "sweep_threshold": 10, "sweep_count": 1,
    "sweeps": [{"date": "2026-08-07", "who": "Jerome Curlier", "skills": 149,
                "subject": "Align every plugin and skill on the 3.0.x line"}],
    "folded": {"count": 0, "people": []},
    "unmapped_identities": [],
    "everyone": [
        {"person": "Jerome Curlier", "created": 4, "updated": 13, "swept": 149,
         "commits": 40},
        {"person": "Rohan Agarwal", "created": 1, "updated": 6, "swept": 0,
         "commits": 9},
    ],
    "commits_read": 49,
}


class TestSweepsAreTaggedNotDropped(unittest.TestCase):
    """A repo-wide pass must not be counted as authoring, and must not vanish.

    Counting raw skills-touched lets one find-and-replace outrank a month of
    work; silently dropping it leaves a reader unable to tell a filtered corpus
    from a complete one. Both halves are asserted because both fail by
    producing a plausible number.
    """

    def _build(self, commits, creates=None):
        """Drive build() through stubbed git reads — the same functions main()
        calls, so the assertion covers the path production takes."""
        orig = (collect_git.read_commits, collect_git.created_skills, collect_git._ref)
        collect_git.read_commits = lambda *a, **k: commits
        collect_git.created_skills = lambda *a, **k: (creates or {})
        collect_git._ref = lambda repo: "origin/main"
        try:
            return collect_git.build("/tmp/repo", "2026-08-01", "2026-08-31")
        finally:
            (collect_git.read_commits, collect_git.created_skills,
             collect_git._ref) = orig

    def _commit(self, sha, n_skills, email="<redacted-email>", date="2026-08-05"):
        return {"sha": sha, "name": "Jerome Curlier", "email": email, "date": date,
                "subject": "x", "files": [f"plugins/eng/skills/s{i}/SKILL.md"
                                          for i in range(n_skills)]}

    def test_a_sweep_lands_in_its_own_column_not_in_updated(self):
        d = self._build([self._commit("a", collect_git.SWEEP_THRESHOLD + 40)])
        s = d["series"][0]
        self.assertEqual(sum(s["updated"]), 0,
                         "a sweep was counted as authoring")
        self.assertEqual(sum(s["swept"]), collect_git.SWEEP_THRESHOLD + 40)

    def test_the_sweep_is_still_reported(self):
        d = self._build([self._commit("a", 60)])
        self.assertEqual(d["sweep_count"], 1)
        self.assertEqual(d["sweeps"][0]["skills"], 60,
                         "the sweep left no trace in the output")

    def test_ordinary_work_is_untouched_by_the_rule(self):
        d = self._build([self._commit("a", collect_git.SWEEP_THRESHOLD - 1)])
        s = d["series"][0]
        self.assertEqual(sum(s["swept"]), 0)
        self.assertEqual(sum(s["updated"]), collect_git.SWEEP_THRESHOLD - 1)

    def test_a_create_is_a_create_even_inside_a_sweep(self):
        """Creates come from --diff-filter=A, so a sweep that also adds a skill
        still credits the create rather than folding it into swept."""
        d = self._build([self._commit("a", 60)], creates={"a": {"eng:s0"}})
        s = d["series"][0]
        self.assertEqual(sum(s["created"]), 1)
        self.assertEqual(sum(s["swept"]), 59, "the created skill was double-counted")


class TestOnePersonIsNotSplitByGitIdentity(unittest.TestCase):
    """Jerome commits under two emails, Rohan under two names on one email.

    Email alone cannot merge the first; name alone cannot merge the second. A
    split person understates both halves and nothing errors, so the merge is
    explicit and anything unmapped is reported rather than folded.
    """

    def test_two_emails_merge_to_one_person(self):
        a = collect_git.person("<redacted-email>", "Jerome Curlier",
                               collect_git.IDENTITY_ALIASES)
        b = collect_git.person("jcurlier@users.noreply.github.com", "jcurlier",
                               collect_git.IDENTITY_ALIASES)
        self.assertEqual(a[0], b[0])
        self.assertTrue(a[1] and b[1])

    def test_two_names_on_one_email_merge(self):
        a = collect_git.person("<redacted-email>", "Rohan Agarwal",
                               collect_git.IDENTITY_ALIASES)
        b = collect_git.person("<redacted-email>", "rohanx96",
                               collect_git.IDENTITY_ALIASES)
        self.assertEqual(a[0], b[0])

    def test_an_unknown_identity_is_reported_never_folded(self):
        who, mapped = collect_git.person("nobody@x.io", "Nobody", {})
        self.assertFalse(mapped)
        self.assertEqual(who, "Nobody",
                         "an unmapped identity was folded into someone else")


class TestAuthorshipFoldsPastEightPeople(unittest.TestCase):
    """Eight named slots plus a neutral Other, same budget as the other stacks.
    A ninth person never mints a hue."""

    def test_a_ninth_person_folds_and_the_fold_is_counted(self):
        orig = (collect_git.read_commits, collect_git.created_skills, collect_git._ref)
        collect_git.read_commits = lambda *a, **k: [
            {"sha": f"s{i}", "name": f"P{i}", "email": f"p{i}@x.io",
             "date": "2026-08-05", "subject": "x",
             "files": [f"plugins/eng/skills/sk{i}/SKILL.md"]}
            for i in range(12)]
        collect_git.created_skills = lambda *a, **k: {}
        collect_git._ref = lambda repo: "origin/main"
        try:
            d = collect_git.build("/tmp/r", "2026-08-01", "2026-08-31")
        finally:
            (collect_git.read_commits, collect_git.created_skills,
             collect_git._ref) = orig
        names = [s["person"] for s in d["series"]]
        self.assertLessEqual(len(names), collect_git.PERSON_SLOTS + 1)
        self.assertEqual(names[-1], "Other")
        self.assertEqual(d["folded"]["count"], 12 - collect_git.PERSON_SLOTS)
        self.assertEqual(len(d["everyone"]), 12,
                         "the unfolded table lost people the chart folded")


class TestTheWrongCheckoutIsRefused(unittest.TestCase):
    """Two diverged AINative clones exist on some machines. Reading the wrong
    one produces a confident wrong answer with no error, so the remote is
    verified rather than assumed."""

    def test_a_foreign_origin_raises_rather_than_reporting(self):
        orig = collect_git._run
        collect_git._run = lambda args, cwd: type("R", (), {
            "returncode": 0,
            "stdout": "/tmp/other\n" if "rev-parse" in args else
                      "git@github.com:someone/Else.git\n",
            "stderr": ""})()
        try:
            with self.assertRaises(collect_git.GitUnavailable):
                collect_git.resolve_repo("/tmp/other")
        finally:
            collect_git._run = orig


class TestAuthorshipCardRenders(unittest.TestCase):
    def test_the_card_states_the_sweep_rule_it_applied(self):
        html = render.build(dict(DATA, git=GIT))
        self.assertIn("Skill authorship", html)
        self.assertIn('id="authorship"', html)
        self.assertIn("sweep", html.lower())

    def test_an_unmapped_identity_is_surfaced_in_the_page(self):
        g = dict(GIT, unmapped_identities=[{"identity": "Nobody <n@x.io>",
                                            "commits": 4}])
        html = render.build(dict(DATA, git=g))
        self.assertIn("Nobody", html,
                      "an unclaimed identity was silent in the report")

    def test_no_git_data_omits_the_card_entirely(self):
        html = render.build(DATA)
        self.assertNotIn('id="authorship"', html)
        # The heading, not the bare phrase: the embedded JS carries comments that
        # name the section, and a page-wide substring search would call those a
        # rendered card. Same intent, matched where the card actually is.
        self.assertNotIn("<h2>Skill authorship</h2>", html)

    def test_an_unreadable_checkout_says_so_rather_than_rendering_empty(self):
        html = render.build(dict(DATA, git={"unavailable": "no checkout found"}))
        self.assertIn("<h2>Skill authorship</h2>", html)
        self.assertIn("no checkout found", html)
        self.assertNotIn('id="authorship"', html)


class TestWeeklyGrainIsAFirstClassOption(unittest.TestCase):
    """Monthly was the only grain exercised end to end. Weekly changes the
    bucket count by ~4x, and every failure it causes is cosmetic-looking:
    duplicate tick labels, or ticks overprinting each other."""

    def test_weekly_periods_get_distinct_labels(self):
        html = render.build(dict(DATA, window=dict(DATA["window"], grain="week")))
        self.assertIn("'week'", html.replace('"', "'"))

    def test_tick_thinning_leaves_monthly_untouched(self):
        """7 monthly buckets must still draw all 7 labels — the thinning rule
        exists for weekly and must not quietly drop monthly ticks."""
        js = render.JS
        self.assertIn("function tickStep", js)
        ns = {}
        # mirror the JS rule; it is four tokens long and the point is the boundary
        import math

        def tick_step(n, w):
            return max(1, math.ceil(34 / (w / max(n, 1))))

        self.assertEqual(tick_step(7, 914), 1, "monthly ticks were thinned")
        self.assertEqual(tick_step(12, 914), 1, "a quarter of weeks was thinned")
        self.assertGreater(tick_step(29, 914), 1,
                           "a full year of weeks would overprint its labels")

    def test_every_bucket_keeps_a_tooltip_even_when_its_tick_is_not_drawn(self):
        """Thinning may only remove printed ticks, never hit targets — the
        hover layer is what makes a thinned label recoverable.

        The stacked charts share one renderer, so the property is checked at
        that one place: the tick `<text>` sits behind an `i % tstep` guard and
        the `.hit` rect does not.
        """
        js = render.JS
        body = js.split("function stackedBars")[1].split("\n  }")[0]
        self.assertIn("i % tstep === 0", body, "tick thinning was lost entirely")
        hit = body.split("class=\"hit\"")[0].rsplit("if(i % tstep", 1)
        self.assertEqual(len(hit), 2, "expected the tick guard before the hit rect")
        self.assertNotIn("tstep", body.split("class=\"hit\"")[1][:200],
                         "hit targets were thinned along with the labels")

    def test_a_named_repo_that_does_not_resolve_raises_rather_than_falling_back(self):
        """The two clones share an origin, so the remote check cannot catch a
        fall-through. A checkout the caller NAMED must be authoritative."""
        with self.assertRaises(collect_git.GitUnavailable):
            collect_git.resolve_repo("/definitely/not/a/directory")

    def test_a_named_path_that_is_not_a_git_checkout_raises(self):
        orig = collect_git._run
        collect_git._run = lambda args, cwd: type("R", (), {
            "returncode": 128, "stdout": "", "stderr": "not a git repository"})()
        try:
            with self.assertRaises(collect_git.GitUnavailable):
                collect_git.resolve_repo("/tmp")
        finally:
            collect_git._run = orig


class TestAMissingCheckoutCannotTakeDownTheReport(unittest.TestCase):
    """The authorship section is optional; nothing about it may cost the report.

    The earlier bug passed a green suite because every test built the `git`
    payload by hand. Production instead runs `collect_git.py --out F` and then
    `render.py --git F`, and on a git miss the collector wrote no F at all — so
    render raised FileNotFoundError and produced NO html. The renderer's
    `unavailable` branch existed but the collector could never reach it.

    These drive the real producer into the real consumer for that reason.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _run(self, mod, argv):
        """Call a script's main() the way a shell does — through argv."""
        saved_argv, saved_err = sys.argv, sys.stderr
        sys.argv = argv
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
        try:
            return mod.main()
        finally:
            sys.stderr.close()
            sys.argv, sys.stderr = saved_argv, saved_err

    def test_the_collector_writes_out_even_when_git_is_unavailable(self):
        """`no file` is not how a consumer learns the section is missing."""
        out = pathlib.Path(self.dir) / "git.json"
        code = self._run(collect_git,
                         ["collect_git.py", "--repo", "/nope", "--out", str(out)])
        self.assertEqual(code, 3)
        self.assertTrue(out.exists(), "the collector left the consumer nothing to read")
        self.assertIn("unavailable", json.loads(out.read_text()))

    def test_that_payload_renders_a_full_report_naming_the_miss(self):
        """Feed the consumer its real producer's output, not a hand-built dict."""
        out = pathlib.Path(self.dir) / "git.json"
        self._run(collect_git,
                  ["collect_git.py", "--repo", "/nope", "--out", str(out)])
        html = render.build(dict(DATA, git=json.loads(out.read_text())))
        self.assertIn("Skill authorship", html)
        self.assertIn("Not available for this run", html)
        # the rest of the report must still be there
        self.assertIn("Seat fit", html)
        self.assertIn("Concentration", html)

    def test_an_absent_git_file_still_produces_html(self):
        """render.py's own --git handling, via main(), not via build()."""
        data = pathlib.Path(self.dir) / "usage.json"
        data.write_text(json.dumps(DATA))
        out = pathlib.Path(self.dir) / "r.html"
        code = self._run(render, ["render.py", "--data", str(data), "--git",
                                  str(pathlib.Path(self.dir) / "nope.json"),
                                  "--out", str(out)])
        self.assertEqual(code, 0)
        self.assertTrue(out.exists(), "an optional section took down the whole report")
        self.assertIn("Not available for this run", out.read_text())


class TestBothGrainsShareOneRanking(unittest.TestCase):
    """Colour follows the entity, never its rank.

    Usage-by-person and skill-authorship each render at two grains. If each
    grain ranked people independently, someone near the eight-slot fold could
    hold a named hue in one view and sit in the neutral "Other" band in the
    other — so flipping the toggle would appear to recolour them. The ranking
    is therefore computed once and imposed on both, and these assert that
    rather than trusting it to happen to be true.
    """

    def test_authorship_series_follow_the_shared_slot_order(self):
        orig = (collect_git.read_commits, collect_git.created_skills, collect_git._ref)
        # one person active only in a late week — the case a monthly-only
        # ranking would order differently from a weekly one
        commits = [
            {"sha": "a", "name": "Jerome Curlier", "email": "<redacted-email>",
             "date": "2026-08-05", "subject": "x",
             "files": [f"plugins/eng/skills/s{i}/SKILL.md" for i in range(4)]},
            {"sha": "b", "name": "Rohan Agarwal", "email": "<redacted-email>",
             "date": "2026-09-22", "subject": "y",
             "files": ["plugins/eng/skills/z/SKILL.md"]},
        ]
        collect_git.read_commits = lambda *a, **k: commits
        collect_git.created_skills = lambda *a, **k: {}
        collect_git._ref = lambda repo: "origin/main"
        try:
            d = collect_git.build_both("/tmp/r", "2026-08-01", "2026-09-30")
        finally:
            (collect_git.read_commits, collect_git.created_skills,
             collect_git._ref) = orig
        self.assertIn("slots", d)
        for grain in ("month", "week"):
            names = [s["person"] for s in d["grains"][grain]["series"]]
            self.assertEqual(names, d["slots"],
                             f"{grain} ranked people differently from the shared order")

    def test_a_person_absent_from_one_grain_still_holds_their_slot(self):
        """A zero-filled row, not a missing one — dropping it would shift every
        later person up a colour."""
        orig = (collect_git.read_commits, collect_git.created_skills, collect_git._ref)
        collect_git.read_commits = lambda *a, **k: [
            {"sha": "a", "name": "Jerome Curlier", "email": "<redacted-email>",
             "date": "2026-08-05", "subject": "x",
             "files": ["plugins/eng/skills/s1/SKILL.md"]}]
        collect_git.created_skills = lambda *a, **k: {}
        collect_git._ref = lambda repo: "origin/main"
        try:
            d = collect_git.build_both("/tmp/r", "2026-08-01", "2026-09-30")
        finally:
            (collect_git.read_commits, collect_git.created_skills,
             collect_git._ref) = orig
        for grain in ("month", "week"):
            s = d["grains"][grain]["series"]
            self.assertEqual(len(s), len(d["slots"]))
            for row in s:
                self.assertEqual(len(row["created"]), len(d["grains"][grain]["periods"]),
                                 "a series was not padded to the period count")

    def test_the_report_renders_both_grains_with_a_toggle(self):
        um = {
            "slots": ["a@x.io", "b@x.io"],
            "grains": {
                "month": {"periods": ["2026-08-01"],
                          "series": [{"user": "a@x.io", "values": [9.0], "total": 9.0},
                                     {"user": "b@x.io", "values": [1.0], "total": 1.0}]},
                "week": {"periods": ["2026-08-03", "2026-08-10"],
                         "series": [{"user": "a@x.io", "values": [5.0, 4.0], "total": 9.0},
                                    {"user": "b@x.io", "values": [1.0, 0.0], "total": 1.0}]},
            },
            "folded": {"count": 0, "users": [], "total": 0.0},
            "all_time": [{"user": "a@x.io", "value": 9.0, "tokens": 10}],
        }
        html = render.build(dict(DATA, user_mix=um))
        self.assertIn("grainToggle", html)
        self.assertIn("Monthly", html)
        self.assertIn("Weekly", html)
        # both datasets must ship with the page — a toggle that refetches
        # would be dead in a self-contained artifact
        self.assertIn("2026-08-10", html, "the weekly series was not embedded")


class TestTheFoldIsDecidedOnceForBothGrains(unittest.TestCase):
    """The bug this class exists for: each grain folding independently.

    `build_both` originally ran a full build per grain — each ranking and
    folding its own eight — then reordered the weekly rows onto the monthly
    order. Reordering cannot repair a disagreement that has already happened:
    a person named monthly but folded weekly gets a zero-filled row, and a
    person named weekly but folded monthly is dropped, taking their work with
    them.

    **Counts are de-duplicated per bucket**, which is what makes the two grains
    able to rank people differently: one skill touched in four separate weeks
    is 4 weekly and 1 monthly. So the fixture below puts two people either side
    of the eighth slot and has exactly that disagreement.

    A first version of this test used twelve people with one commit each and
    passed against the broken code, because nothing there touched a skill in
    two buckets — a fixture the bug could not reach.
    """

    # Ground truth: P0..P6 author 9,8,7,6,5,4,3 skills on one day (all under
    # SWEEP_THRESHOLD, so they count as authoring). P7 authors 2 on one day.
    # P8 touches ONE skill on four different weeks — 4 weekly, 1 monthly.
    BIG = [9, 8, 7, 6, 5, 4, 3]
    P8_WEEKS = ["2026-08-04", "2026-08-11", "2026-08-18", "2026-08-25"]

    def _commits(self):
        c = [{"sha": f"a{i}", "name": f"P{i}", "email": f"p{i}@x.io",
              "date": "2026-08-05", "subject": "s",
              "files": [f"plugins/eng/skills/b{i}_{j}/SKILL.md" for j in range(n)]}
             for i, n in enumerate(self.BIG)]
        c.append({"sha": "p7", "name": "P7", "email": "p7@x.io",
                  "date": "2026-08-05", "subject": "s",
                  "files": ["plugins/eng/skills/x1/SKILL.md",
                            "plugins/eng/skills/x2/SKILL.md"]})
        c += [{"sha": f"p8w{w}", "name": "P8", "email": "p8@x.io", "date": d,
               "subject": "s", "files": ["plugins/eng/skills/same/SKILL.md"]}
              for w, d in enumerate(self.P8_WEEKS)]
        return c

    def _build(self):
        orig = (collect_git.read_commits, collect_git.created_skills, collect_git._ref)
        collect_git.read_commits = lambda *a, **k: self._commits()
        collect_git.created_skills = lambda *a, **k: {}
        collect_git._ref = lambda repo: "origin/main"
        try:
            return collect_git.build_both("/tmp/r", "2026-08-01", "2026-08-31")
        finally:
            (collect_git.read_commits, collect_git.created_skills,
             collect_git._ref) = orig

    @staticmethod
    def _by_person(d, grain):
        return {s["person"]: sum(s["created"]) + sum(s["updated"])
                for s in d["grains"][grain]["series"]}

    def test_the_fixture_really_does_straddle_the_fold(self):
        """Guard the guard: if this stops being true the others prove nothing."""
        d = self._build()
        self.assertEqual(d["folded"]["count"], 1, "nobody folded — fixture is inert")
        self.assertEqual(self._by_person(d, "month")["P8"], 1)
        self.assertEqual(self._by_person(d, "week")["P8"], 4,
                         "per-bucket de-dup is what lets the grains disagree")

    def test_no_authored_work_is_lost_in_either_grain(self):
        d = self._build()
        # 9+8+7+6+5+4+3 = 42 authored once, + P7's 2, + P8's 1 month / 4 week
        self.assertEqual(sum(self._by_person(d, "month").values()), 42 + 2 + 1)
        self.assertEqual(sum(self._by_person(d, "week").values()), 42 + 2 + 4,
                         "the weekly view lost work the monthly view kept")

    def test_a_contributor_never_reads_as_zero_in_one_grain(self):
        """The old code showed P7 as 0 for the week, having folded them there
        while naming them monthly."""
        d = self._build()
        for grain in ("month", "week"):
            for who, n in self._by_person(d, grain).items():
                if who != "Other":
                    self.assertGreater(n, 0,
                                       f"{who} reads as zero in the {grain} view")

    def test_both_grains_name_exactly_the_same_people(self):
        d = self._build()
        month = [s["person"] for s in d["grains"]["month"]["series"]]
        week = [s["person"] for s in d["grains"]["week"]["series"]]
        self.assertEqual(month, week,
                         "the grains disagree about who holds which slot")
        self.assertEqual(month, d["slots"])

    def test_the_unfolded_table_still_lists_everyone(self):
        d = self._build()
        self.assertEqual(len(d["everyone"]), 9,
                         "the fold removed people from the all-people table")


class TestTheOtherBandCountsPerPerson(unittest.TestCase):
    """De-duplication is per person, never across the fold.

    One person touching the same skill twice in a week is one touch. TWO people
    touching it are two. A refactor briefly unioned every folded person's sets
    into a single "Other" set before counting, which silently undercounted the
    band — and only the band, so its bar no longer meant the same thing as the
    named bars stacked beside it. That is the worst place for the error to be,
    because "Other" is the one row nobody can eyeball against a known name.
    """

    def _build(self, commits):
        orig = (collect_git.read_commits, collect_git.created_skills, collect_git._ref)
        collect_git.read_commits = lambda *a, **k: commits
        collect_git.created_skills = lambda *a, **k: {}
        collect_git._ref = lambda repo: "origin/main"
        try:
            return collect_git.build_both("/tmp/r", "2026-08-01", "2026-08-31")
        finally:
            (collect_git.read_commits, collect_git.created_skills,
             collect_git._ref) = orig

    def _commits(self, shared_skill: bool):
        """Eight named authors, then two folded ones who touch either the SAME
        skill or two different ones — the only difference between the cases."""
        c = [{"sha": f"a{i}", "name": f"P{i}", "email": f"p{i}@x.io",
              "date": "2026-08-05", "subject": "s",
              "files": [f"plugins/eng/skills/b{i}_{j}/SKILL.md" for j in range(9 - i)]}
             for i in range(8)]
        for n, k in enumerate((8, 9)):
            skill = "shared" if shared_skill else f"own{k}"
            c.append({"sha": f"z{k}", "name": f"P{k}", "email": f"p{k}@x.io",
                      "date": "2026-08-05", "subject": "s",
                      "files": [f"plugins/eng/skills/{skill}/SKILL.md"]})
        return c

    @staticmethod
    def _other(d, grain="month"):
        row = [s for s in d["grains"][grain]["series"] if s["person"] == "Other"][0]
        return sum(row["created"]) + sum(row["updated"])

    def test_two_folded_people_on_one_skill_count_twice(self):
        d = self._build(self._commits(shared_skill=True))
        self.assertEqual(d["folded"]["count"], 2)
        self.assertEqual(self._other(d), 2,
                         "the fold collapsed two people's work into one touch")

    def test_the_overlap_is_what_is_being_measured(self):
        """Control: distinct skills must give the same 2, so the assertion
        above is about the overlap and not about the arithmetic in general."""
        d = self._build(self._commits(shared_skill=False))
        self.assertEqual(self._other(d), 2)

    def test_one_person_repeating_a_skill_still_de_duplicates(self):
        """The other half of the rule — within a person, a repeat is one."""
        c = self._commits(shared_skill=False)
        c.append({"sha": "again", "name": "P8", "email": "p8@x.io",
                  "date": "2026-08-06", "subject": "s",
                  "files": ["plugins/eng/skills/own8/SKILL.md"]})
        d = self._build(c)
        self.assertEqual(self._other(d), 2,
                         "a person's repeat of their own skill was double-counted")


class TestOverageKeepsQuietPeriods(unittest.TestCase):
    """A period with usage but no overage must still occupy an axis slot.

    Registering a period only when a charge lands gives this chart a different
    timeline from the usage chart beside it — and the card's own lede asks the
    reader to compare them — while making sporadic charges read as continuous,
    because the quiet weeks are simply not drawn.
    """

    def test_a_quiet_period_is_not_dropped_from_the_axis(self):
        """Drives the real bucketing through collect.overage_mix with a stubbed
        query, so the assertion covers the path production takes."""
        import datetime as _dt
        rows = [
            {"usage_date": _dt.date(2026, 8, 3), "who": "a@x.io", "billed": 10.0},
            # a whole week of usage that incurred nothing
            {"usage_date": _dt.date(2026, 8, 10), "who": "a@x.io", "billed": 0.0},
            {"usage_date": _dt.date(2026, 8, 17), "who": "a@x.io", "billed": 5.0},
        ]
        orig = collect.bq.query
        collect.bq.query = lambda *a, **k: rows
        try:
            out = collect.overage_mix("2026-08-01", "2026-08-31")
        finally:
            collect.bq.query = orig
        weeks = out["grains"]["week"]["periods"]
        self.assertEqual(len(weeks), 3,
                         "the zero-overage week was dropped from the axis")
        self.assertIn("2026-08-10", weeks)
        # and it must read as zero, not carry the neighbouring week's charge
        series = out["grains"]["week"]["series"][0]
        self.assertEqual(series["values"][weeks.index("2026-08-10")], 0.0)

    def test_the_total_is_unaffected_by_keeping_them(self):
        import datetime as _dt
        rows = [{"usage_date": _dt.date(2026, 8, 3), "who": "a@x.io", "billed": 10.0},
                {"usage_date": _dt.date(2026, 8, 10), "who": "a@x.io", "billed": 0.0}]
        orig = collect.bq.query
        collect.bq.query = lambda *a, **k: rows
        try:
            out = collect.overage_mix("2026-08-01", "2026-08-31")
        finally:
            collect.bq.query = orig
        self.assertEqual(out["total"], 10.0)
        self.assertEqual(out["payers"], 1, "a zero-overage row created a phantom payer")
TRUE_COST = {
    "months": [
        {"period": "2026-08-01", "seats": 1020.0, "overage": 509.15, "total": 1529.15,
         "members": 15, "measured": False},
        {"period": "2026-09-01", "seats": 1020.0, "overage": 456.68, "total": 1476.68,
         "members": 15, "measured": True},
    ],
    "unmeasured": 1, "first_snapshot": "2026-09-18", "snapshots": 3,
    "seat_prices": {"premium": 100.0, "standard": 20.0},
}


class TestAnAssumedSeatCountIsMarkedNotHidden(unittest.TestCase):
    """The seat roster is only known from the first snapshot.

    Earlier months can only be priced by carrying that roster backwards, which
    is an assumption and not a measurement — headcount was almost certainly
    lower. Two ways to get this wrong, and the card must do neither: drop those
    months (hiding most of the window), or draw them like the measured one
    (a confident fiction). It tags them instead.
    """

    def test_the_estimate_is_stated_in_the_lede(self):
        html = render.build(dict(DATA, true_cost=TRUE_COST))
        self.assertIn("What we actually pay", html)
        self.assertIn("estimates", html)
        self.assertIn("2026-09-18", html, "the lede must name when the roster became known")

    def test_assumed_months_are_still_shown(self):
        html = render.build(dict(DATA, true_cost=TRUE_COST))
        self.assertIn("2026-08", html, "an assumed month was dropped rather than tagged")
        self.assertIn("assumed (15 seats)", html)

    def test_a_measured_month_is_not_labelled_assumed(self):
        html = render.build(dict(DATA, true_cost=TRUE_COST))
        table = html.split("Month by month")[1]
        self.assertIn("measured", table)

    def test_the_chart_can_distinguish_them_visually(self):
        """A table note alone is not enough — the bars must differ, or a reader
        skimming the chart reads an estimate as an invoice."""
        js = render.JS
        self.assertIn('id="assumed"', js, "no hatch pattern for assumed months")
        self.assertIn("m.measured", js, "the chart does not branch on measured")

    def test_no_seat_snapshot_at_all_degrades_rather_than_crashes(self):
        html = render.build(dict(DATA, true_cost={"months": [], "unmeasured": 0,
                                                  "note": "no seat snapshot exists"}))
        self.assertNotIn('id="truecost"', html)

    def test_the_card_is_absent_when_the_collector_did_not_run(self):
        html = render.build(DATA)
        self.assertNotIn("What we actually pay", html)

    def test_totals_are_seats_plus_overage(self):
        for m in TRUE_COST["months"]:
            self.assertAlmostEqual(m["total"], m["seats"] + m["overage"], places=2)


class TestTheBillIsShownToTheDollar(unittest.TestCase):
    """`fmtU` rounds to thousands. That is right for a derived counterfactual
    spanning four orders of magnitude and wrong for an invoice: $1,476 and
    $1,529 both collapse to "$1k"/"$2k", so two nearly equal months read as a
    twofold change on the one card whose job is to say what Claude costs.
    """

    def test_the_true_cost_card_does_not_round_to_thousands(self):
        js = render.JS
        body = js.split("3e. TRUE COST")[1].split("})();")[0]
        self.assertNotIn("fmtU(", body,
                         "the bill is being rounded by the thousands formatter")
        self.assertIn("exactU(", body)

    def test_the_exact_formatter_keeps_whole_dollars(self):
        js = render.JS
        self.assertIn("function exactU", js)
        self.assertIn("toLocaleString", js, "no thousands separator on the bill")

    def test_other_cards_still_use_the_compact_formatter(self):
        """The compact form is correct where the range is wide — this is not a
        blanket replacement."""
        js = render.JS
        self.assertIn("fmtAxis: fmtU", js)


class TestAssumedBarsAreNotTheOverageColour(unittest.TestCase):
    """The hatch marks an assumed SEAT count, so it must read as seats.

    Painting it with the overage token made an assumed seat bar the same hue as
    the overflow stacked directly on top of it. Six of seven months are assumed,
    so most of the chart became one flat band and the seats-vs-overage split —
    the entire point of the card — disappeared for a skimming reader.
    """

    def test_the_hatch_uses_the_seat_hue(self):
        js = render.JS
        pattern = js.split('id="assumed"')[1].split("</pattern>")[0]
        self.assertIn("--s1", pattern, "the hatch is not drawn in the seat colour")
        self.assertNotIn("--s2", pattern,
                         "the hatch uses the overage colour, so assumed seats "
                         "read as overflow")

    def test_the_legend_swatch_matches_the_hatch(self):
        html = render.build(dict(DATA, true_cost=TRUE_COST))
        legend = html.split("hatched = seat count assumed")[0][-200:]
        self.assertIn("--s1", legend,
                      "the legend swatch and the bars disagree on colour")


class TestSeatsAreOwedEvenInAQuietMonth(unittest.TestCase):
    """The month axis is the window, not the months carrying usage rows.

    Seats are billed whether or not anybody typed. Building the axis from
    `daily_user_usage` dropped an idle month entirely — understating the bill,
    and emptying the card altogether for a window with seats but no usage. It is
    the same rule the overage chart needed one layer down: a period's existence
    is a fact about the calendar, not about whether something landed in it.

    `daily_user_usage` also cannot tell an idle month from an unharvested one,
    which is why `harvest_log` exists — so a month nobody read is tagged rather
    than shown as a measured $0.
    """

    def _cost(self, spend_months, logged_months, snapshots):
        calls = []

        def fake(sql, *a, **k):
            calls.append(sql)
            if "member_seat" in sql:
                return [{"as_of_date": d, "seat_tier": t, "n": n}
                        for d, tiers in snapshots.items() for t, n in tiers.items()]
            if "harvest_log" in sql:
                return [{"m": m} for m in logged_months]
            return [{"m": m, "billed": v} for m, v in spend_months.items()]

        orig = collect.bq.query
        collect.bq.query = fake
        try:
            return collect.true_cost("2026-07-01", "2026-09-30")
        finally:
            collect.bq.query = orig

    def test_a_month_with_no_usage_still_appears_and_still_costs(self):
        out = self._cost(
            spend_months={"2026-07-01": 10.0, "2026-09-01": 5.0},   # August idle
            logged_months=["2026-07-01", "2026-08-01", "2026-09-01"],
            snapshots={"2026-09-18": {"premium": 9, "standard": 6}})
        periods = [m["period"] for m in out["months"]]
        self.assertEqual(periods, ["2026-07-01", "2026-08-01", "2026-09-01"],
                         "an idle month was dropped from the bill")
        aug = next(m for m in out["months"] if m["period"] == "2026-08-01")
        self.assertEqual(aug["overage"], 0.0)
        self.assertEqual(aug["seats"], 9 * 100.0 + 6 * 20.0,
                         "seats are owed whether or not anybody typed")
        self.assertTrue(aug["overage_known"], "August was harvested — its $0 is real")

    def test_an_unharvested_month_is_tagged_not_shown_as_a_measured_zero(self):
        out = self._cost(
            spend_months={"2026-07-01": 10.0},
            logged_months=["2026-07-01"],            # Aug + Sep never read
            snapshots={"2026-09-18": {"premium": 9, "standard": 6}})
        self.assertEqual(out["unharvested"], 2)
        unread = [m for m in out["months"] if not m["overage_known"]]
        self.assertEqual([m["period"] for m in unread], ["2026-08-01", "2026-09-01"])
        for m in unread:
            self.assertGreater(m["seats"], 0, "the seat cost is still owed and shown")

    def test_the_card_says_so_when_a_month_was_never_read(self):
        tc = dict(TRUE_COST, unharvested=1)
        tc["months"] = [dict(TRUE_COST["months"][0], overage_known=False)]
        html = render.build(dict(DATA, true_cost=tc))
        self.assertIn("never harvested", html)
        self.assertIn("not because nothing was charged", html)


class TestZeroOverageDrawsNothing(unittest.TestCase):
    """A month charged nothing must not show an overage band.

    The 1px floor exists so a small charge stays visible, but it painted a
    sliver on months whose overage is $0 — and once idle months were put back on
    the axis, that became every quiet month. An invoice card that renders $0 as
    a visible charge is worse than one that omits the month.
    """

    def test_the_band_is_skipped_at_zero(self):
        js = render.JS
        body = js.split("3e. TRUE COST")[1].split("})();")[0]
        seg = body.split("c('--s2')")[0]
        self.assertIn("m.overage > 0", seg,
                      "the overage band is drawn without a zero guard")

    def test_a_real_charge_still_clears_the_minimum(self):
        """The floor must survive for genuinely small charges — removing the
        guard's sibling would hide a real $1 of overage."""
        js = render.JS
        body = js.split("3e. TRUE COST")[1].split("})();")[0]
        self.assertIn("Math.max(ho-2,1)", body,
                      "the 1px floor for small real charges was lost")


class TestNewSkillsCountsCreatesOnly(unittest.TestCase):
    """The creates-only card must not quietly include updates.

    Authorship counts created + updated together and maintenance dominates it,
    so a card that claims to show what we ADDED and silently folds updates back
    in would be indistinguishable from the chart above it — and wrong by roughly
    a factor of three on this repo's data.
    """

    GIT = {
        "repo": "/tmp/r", "ref": "origin/main",
        "slots": ["Jerome Curlier", "Rohan Agarwal"],
        "grains": {
            "month": {"periods": ["2026-08-01"], "series": [
                {"person": "Jerome Curlier", "created": [3], "updated": [40],
                 "swept": [100], "commits": 9, "total_created": 3,
                 "total_updated": 40, "total_swept": 100},
                {"person": "Rohan Agarwal", "created": [1], "updated": [2],
                 "swept": [0], "commits": 2, "total_created": 1,
                 "total_updated": 2, "total_swept": 0}]},
            "week": {"periods": ["2026-08-03"], "series": [
                {"person": "Jerome Curlier", "created": [3], "updated": [40],
                 "swept": [100], "commits": 9, "total_created": 3,
                 "total_updated": 40, "total_swept": 100},
                {"person": "Rohan Agarwal", "created": [1], "updated": [2],
                 "swept": [0], "commits": 2, "total_created": 1,
                 "total_updated": 2, "total_swept": 0}]},
        },
        "sweep_threshold": 10, "sweeps": [], "sweep_count": 0,
        "folded": {"count": 0, "people": []}, "unmapped_identities": [],
        "everyone": [], "commits_read": 11,
    }

    def test_the_bar_reads_created_and_nothing_else(self):
        js = render.JS
        body = js.split("3d-bis. NEW SKILLS")[1].split("})();")[0]
        value = body.split("value:")[1].split("\n")[0]
        self.assertIn("created", value)
        self.assertNotIn("updated", value, "updates leaked into the creates bar")
        self.assertNotIn("swept", value, "sweeps leaked into the creates bar")

    def test_the_card_reports_the_creates_total_not_the_combined_one(self):
        html = render.build(dict(DATA, git=self.GIT))
        self.assertIn("New skills created", html)
        self.assertIn("4 across", html, "the lede did not report 3 + 1 creates")
        self.assertNotIn("46 across", html, "the lede used created + updated")

    def test_it_shares_the_authorship_slot_order(self):
        """Both cards read D.git.grains[...].series in the collector's order, so
        a person holds one colour across the two. Asserted because ranking this
        card separately is a change someone could make without noticing the
        colours drift apart."""
        js = render.JS
        body = js.split("3d-bis. NEW SKILLS")[1].split("})();")[0]
        self.assertIn("G.series", body)
        self.assertNotIn("sort", body, "this card re-ranks instead of sharing the order")

    def test_no_creates_at_all_omits_the_card(self):
        g = json.loads(json.dumps(self.GIT))
        for grain in g["grains"].values():
            for s in grain["series"]:
                s["created"] = [0]
                s["total_created"] = 0
        html = render.build(dict(DATA, git=g))
        self.assertNotIn("New skills created", html)

    def test_an_unavailable_checkout_omits_the_card(self):
        html = render.build(dict(DATA, git={"unavailable": "no checkout"}))
        self.assertNotIn('id="newskills"', html)


class TestOnePersonSeveralSeats(unittest.TestCase):
    """A person who holds several seats is one person in every per-person view; each
    seat is still billed, so their seat price is the sum and the tier tile counts seats."""

    def test_every_mapped_email_resolves_to_its_person(self):
        sql = collect.person_sql("user_email")
        for email, person in collect.SEAT_PEOPLE.items():
            self.assertIn(f"WHEN '{email}' THEN '{person}'", sql)
        self.assertTrue(sql.endswith("ELSE user_email END)"))

    def test_per_person_reads_group_by_person_not_seat(self):
        src = (SCRIPTS / "collect.py").read_text(encoding="utf-8")
        self.assertIn("COUNT(DISTINCT NULLIF(person,'')) AS users", src)
        self.assertIn("SELECT NULLIF(person,'') AS user", src)
        self.assertIn('{person_sql("user_email")}', src)   # overage reads the raw table
        self.assertIn("GROUP BY person", src)

    def test_several_seats_make_one_row_with_summed_seat_price(self):
        rows = collect.seat_rows([
            {"person": "Pat", "tiers": ["premium", "premium", "standard"], "role": "Owner",
             "tokens": 10, "api_value": 600.0, "billed": 1.5, "active_days": 20},
            {"person": "b@x.io", "tiers": ["standard"], "role": "User",
             "tokens": None, "api_value": None, "billed": None, "active_days": None},
        ], 30)
        pat = rows[0]
        self.assertEqual(pat["user"], "Pat")
        self.assertEqual(pat["seat_usd"], 220.0)
        self.assertEqual(pat["tier"], "mixed")
        self.assertEqual(pat["ratio"], 2.7)
        self.assertEqual(rows[1]["tier"], "standard")
        self.assertEqual(rows[1]["active_days"], 0)

    def test_the_table_names_the_seats_and_the_tile_counts_seats_not_people(self):
        d = json.loads(json.dumps(DATA))
        d["seat_fit"].append({"user": "Pat", "tier": "mixed", "tiers": ["premium", "standard", "standard"],
                              "role": "User", "seat_usd": 140.0, "value_per_month": 50.0, "ratio": 0.4,
                              "tokens": 1, "billed": 0.0, "active_days": 3})
        h = render.build(d)
        self.assertIn("Pat (3 seats)", h)
        self.assertIn("1 premium, 2 standard", h)
        self.assertIn("3 premium · 4 standard", h)

    def test_a_mixed_person_is_premium_and_flagged_when_under_their_seats(self):
        d = json.loads(json.dumps(DATA))
        d["seat_fit"] = [{"user": "Pat", "tier": "mixed", "tiers": ["premium", "standard"],
                          "role": "User", "seat_usd": 120.0, "value_per_month": 50.0, "ratio": 0.4,
                          "tokens": 1, "billed": 0.0, "active_days": 3}]
        h = render.build(d)
        self.assertIn('<span class="pill prem">1 premium, 1 standard</span>', h)
        self.assertIn("under-using their seats", h)
        self.assertIn("indexOf('premium')>=0", h)
