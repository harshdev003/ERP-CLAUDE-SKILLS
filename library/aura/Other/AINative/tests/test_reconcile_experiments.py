"""`data:sync-experiments`'s catalogue reconcile — PROD-6116.

Every defect this guards against ships a plausible number rather than an error:
a verdict inferred from `active` is a well-formed lie about an experiment nobody
ran, and a headline count with a permanent non-zero floor stops being read.
"""
import importlib.util
import json
import pathlib
import re
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
RECONCILE = REPO / "plugins/data/skills/sync-experiments/scripts/reconcile.py"


def _load(path):
    spec = importlib.util.spec_from_file_location(f"_m_{path.stem}_{abs(hash(path))}", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


R = _load(RECONCILE)


def api(name, active, ended=None):
    return {"id": name, "active": active, "endedAt": ended}


def row(status=None, end_date=None, page_id="p"):
    return {"page_id": page_id, "status": status, "end_date": end_date}


class TestVariantArms(unittest.TestCase):
    """Distinct letters, not string length."""

    def test_a_repeated_letter_counts_one_arm_but_decides_nothing(self):
        # The arithmetic is unambiguous; what it MEANS is not. See
        # TestAmbiguousActiveIsHeld for what the script does with it.
        self.assertEqual(R.variant_arms("bb"), 1)

    def test_arms(self):
        for active, arms in (("a", 1), ("ab", 2), ("abc", 3), ("", 0), (None, 0), (" a ", 1)):
            self.assertEqual(R.variant_arms(active), arms, active)

    def test_ended_is_never_either(self):
        r = api("x", "ab", ended="2026-01-01T00:00:00Z")
        self.assertFalse(R.stopped_splitting(r))
        self.assertFalse(R.splitting(r))

    def test_stopped_and_splitting_partition_the_unambiguous_unended(self):
        for active in ("", "a", "ab", "abcdef"):
            r = api("x", active)
            self.assertNotEqual(R.stopped_splitting(r), R.splitting(r), active)


class TestNoVerdictIsEverInferred(unittest.TestCase):
    """The card's second acceptance criterion, as a property of the source.

    `active` collapsing to one arm covers a rollout, a pre-release force, a kill
    by unreachable `minVersion` and a permanent feature flag. An early draft of
    PROD-6116 proposed reading it as `Won`, which would have marked
    `chatImageUpload` a win the day after it was created.
    """

    def test_the_written_status_is_a_state_not_a_verdict(self):
        self.assertEqual(R.STOPPED, "Stopped splitting")
        for verdict in ("Won", "Lost", "Inconclusive", "Turned off"):
            self.assertNotEqual(R.STOPPED, verdict)

    def test_no_verdict_string_is_ever_written(self):
        # Verdicts may be NAMED (in RESOLVED, in prose) but never handed to a
        # write. Catch the write itself, not the mention.
        src = RECONCILE.read_text()
        for verdict in ("Won", "Lost", "Inconclusive", "Turned off"):
            self.assertIsNone(
                re.search(rf'make_select\(\s*["\']{verdict}["\']', src),
                f"reconcile.py writes {verdict}",
            )

    def test_feature_flag_is_set_by_a_human_only(self):
        src = RECONCILE.read_text()
        self.assertIsNone(re.search(r'make_select\(\s*FEATURE_FLAG', src))
        self.assertIsNone(re.search(r'make_select\(\s*["\']Feature flag["\']', src))


class TestClassify(unittest.TestCase):
    def test_buckets_are_disjoint_and_total(self):
        catalogue = [
            api("stopped", "a"), api("splitting", "ab"),
            api("ended", "ab", "2026-01-01T00:00:00Z"),
            api("nonotion", "ab"),
        ]
        notion = {
            "stopped": row("Running"), "splitting": row("Running"),
            "ended": row("Won", "2026-01-01"), "orphan": row("Won"),
        }
        b = R.classify(catalogue, notion)
        seen = [n for names in b.values() for n in names]
        # `missing_end_date` overlaps `ended_*` on purpose — it is a second fact
        # about the same row — so exclude it from the partition check.
        partition = [n for k, names in b.items() if k != "missing_end_date" for n in names]
        self.assertEqual(sorted(partition), sorted(set(partition)), "buckets overlap")
        self.assertEqual(set(partition), {"stopped", "splitting", "ended", "nonotion", "orphan"})
        self.assertIn("stopped", b["to_mark"])
        self.assertIn("splitting", b["live"])
        self.assertIn("ended", b["ended_resolved"])
        self.assertIn("nonotion", b["missing_row"])
        self.assertIn("orphan", b["absent_untested"])
        self.assertTrue(all(isinstance(v, list) for v in b.values()))
        self.assertTrue(seen)

    def test_a_resolved_row_is_never_offered_for_a_rewrite(self):
        """A human's answer outranks a restatement of the catalogue."""
        catalogue = [api(s, "a") for s in
                     ("Won", "Lost", "Inconclusive", "Turned off", "Feature flag", "Stopped splitting")]
        notion = {s: row(s) for s in
                  ("Won", "Lost", "Inconclusive", "Turned off", "Feature flag", "Stopped splitting")}
        b = R.classify(catalogue, notion)
        self.assertEqual(b["to_mark"], [])
        self.assertEqual(b["feature_flags"], ["Feature flag"])
        self.assertEqual(b["already_marked"], ["Stopped splitting"])
        self.assertEqual(sorted(b["resolved_stopped"]),
                         ["Inconclusive", "Lost", "Turned off", "Won"])

    def test_a_flag_never_re_surfaces(self):
        """The card's 'marked once, never re-surfaces' criterion."""
        b = R.classify([api("usageLimitsOldUsers", "a")],
                       {"usageLimitsOldUsers": row(R.FEATURE_FLAG)})
        self.assertEqual(b["to_mark"], [])
        self.assertEqual(R.disagreement_count(b), 0)

    def test_blank_and_in_flight_are_writable(self):
        b = R.classify([api("a1", "a"), api("a2", "a"), api("a3", "a")],
                       {"a1": row(None), "a2": row(""), "a3": row("In flight")})
        self.assertEqual(sorted(b["to_mark"]), ["a1", "a2", "a3"])

    def test_ended_splits_running_from_blank(self):
        cat = [api("r", "ab", "2026-01-01T00:00:00Z"), api("b", "ab", "2026-01-01T00:00:00Z")]
        b = R.classify(cat, {"r": row("Running", "2026-01-01"), "b": row(None, "2026-01-01")})
        self.assertEqual(b["ended_but_running"], ["r"])
        self.assertEqual(b["ended_unanalysed"], ["b"])

    def test_a_splitting_row_marked_ended_is_a_disagreement(self):
        b = R.classify([api("x", "ab")], {"x": row("Won", "2026-01-01")})
        self.assertEqual(b["live_disagree"], ["x"])
        self.assertEqual(R.disagreement_count(b), 1)

    def test_an_absent_row_claiming_to_run_is_a_disagreement_and_the_rest_are_not(self):
        """Unprobed, absence alone still holds a Running row in the headline.

        What changed in ENG-1370 is what the report may SAY about the rest —
        see TestAbsenceIsNotEvidenceOfOrigin — not whether a live-looking row
        absent from the catalogue is still worth someone's attention.
        """
        b = R.classify([], {"runs": row("Running"), "sheet": row(None), "won": row("Won")})
        self.assertEqual(b["untested_running"], ["runs"])
        self.assertEqual(sorted(b["absent_untested"]), ["sheet", "won"])
        self.assertEqual(R.disagreement_count(b), 1)


class TestDisagreementCount(unittest.TestCase):
    def test_a_backlog_is_not_a_contradiction(self):
        """Counting the analyse backlog would put a floor under the headline.

        A number nobody can drive to zero stops being read, which is the failure
        this report exists to end.
        """
        self.assertNotIn("ended_unanalysed", R.DISAGREEING)
        self.assertNotIn("absent_untested", R.DISAGREEING)
        b = R.classify([api("e", "ab", "2026-01-01T00:00:00Z")],
                       {"e": row(None, "2026-01-01"), "sheet": row(None)})
        self.assertEqual(R.disagreement_count(b), 0)

    def test_agreement_is_reported_not_silence(self):
        """A clean run must still print the count it reconciled."""
        out = R.render(R.classify([api("x", "ab")], {"x": row("Running")}))
        self.assertIn("0 row(s) disagree", out)
        self.assertIn("Nothing to reconcile", out)

    def test_the_headline_names_every_disagreeing_bucket(self):
        b = R.classify(
            [api("s", "a"), api("er", "ab", "2026-01-01T00:00:00Z"), api("ld", "ab"),
             api("nr", "ab")],
            {"s": row("Running"), "er": row("Running", "2026-01-01"),
             "ld": row("Won", "2026-01-01"), "orph": row("Running")},
        )
        # to_mark 1 + ended_but_running 1 + live_disagree 1 + untested_running 1 + missing_row 1
        self.assertEqual(R.disagreement_count(b), 5)
        out = R.render(b)
        self.assertIn("5 row(s) disagree", out)
        for name in ("s", "er", "ld", "orph", "nr"):
            self.assertIn(name, out)


class TestEndDateIsNeverFabricated(unittest.TestCase):
    """`updatedAt` is a last-touch stamp, not an end.

    Median age of a stopped row's `updatedAt` on 2026-09-17 was 916 days, and
    `usageLimitsOldUsers` — a permanent kill switch — has no end at all. A date
    written here would drive `analyze-experiment`'s Mixpanel window.
    """

    def test_apply_writes_status_alone(self):
        src = RECONCILE.read_text()
        self.assertNotIn("make_date", src)
        self.assertNotIn('"End Date"', src.split("def cmd_apply")[1])


class TestActiveIsAWeightedArmList(unittest.TestCase):
    """`active` is letters from 'a' up, repeats as weight, gaps allowed.

    `aab` is variant a at 2:1 over b — two arms, not a malformed one. Reading a
    repeat as malformed would hold three quarters of the legal shapes.
    """

    def test_repeats_are_weights_not_errors(self):
        for active, expected_arms in (("aab", 2), ("aabd", 3), ("aabcde", 5),
                                      ("ab", 2), ("abc", 3), ("abcdef", 6)):
            self.assertIsNone(R.malformed_active(api("x", active)), active)
            self.assertEqual(R.variant_arms(active), expected_arms, active)

    def test_gaps_are_allowed(self):
        # 'c' missing is legal — arms need not be contiguous.
        self.assertIsNone(R.malformed_active(api("x", "abd")))
        self.assertEqual(R.variant_arms("abd"), 3)

    def test_a_single_letter_is_fine(self):
        # The winning variant on an ended row: 1,543 rows carry 'b' alone.
        for v in ("a", "b", "c", "d"):
            self.assertIsNone(R.malformed_active(api("x", v)), v)


class TestMalformedActiveIsRefused(unittest.TestCase):
    """Two shapes cannot be read as an arm list, and neither is guessed at."""

    def test_a_repeat_collapsing_to_one_arm_is_refused(self):
        """`bb` is a workaround, not a typo.

        `assign()` decides an experiment has ended with `active.length === 1` —
        the STRING length. So `bb` forces every user to b (a pick from "bb" is
        always b) while the length-2 string evades that check. Both rows that
        carried it — `newPricingPage` (16,964 users) and `placeholder` (0) —
        read as "splitting across 2 arms" to anything asking what was live.
        """
        for active in ("bb", "aa", "ccc"):
            why = R.malformed_active(api("x", active))
            self.assertIsNotNone(why, active)
            self.assertIn("one arm", why)

    def test_a_non_variant_character_is_refused(self):
        """`none` — written by the end command with no `and winner <x>` clause.

        One row in 3,007 carried it (`targetAppTest9`), corrected to `b`.
        """
        why = R.malformed_active(api("x", "none"))
        self.assertIsNotNone(why)
        self.assertIn("non-variant", why)

    def test_refused_rows_are_neither_stopped_nor_splitting(self):
        for active in ("bb", "none"):
            r = api("x", active)
            self.assertTrue(R.ambiguous_active(r), active)
            self.assertFalse(R.stopped_splitting(r), active)
            self.assertFalse(R.splitting(r), active)

    def test_refused_rows_are_reported_and_never_written(self):
        b = R.classify([api("newPricingPage", "bb")], {"newPricingPage": row("Running")})
        self.assertEqual(b["ambiguous"], ["newPricingPage"])
        self.assertEqual(b["to_mark"], [])
        self.assertIn("newPricingPage", R.render(b))
        self.assertIn("needs a human", R.render(b))

    def test_an_empty_active_is_not_malformed(self):
        # Absent is a different thing from wrong; `endedAt` decides those rows.
        self.assertIsNone(R.malformed_active(api("x", "")))
        self.assertIsNone(R.malformed_active(api("x", None)))


class TestAResumedExperimentIsNotAgreement(unittest.TestCase):
    """The original failure, pointed the other way.

    A row marked `Stopped splitting` whose experiment starts splitting again
    carries no `End Date` and no verdict — so on the obvious classification it
    looks like agreement, the stale status is never reported, and nothing ever
    corrects it. That is the same shape as the 133 rows this script exists to
    end, which is why it gets its own bucket rather than a note.
    """

    def test_it_is_a_disagreement_and_the_marker_is_withdrawn(self):
        b = R.classify([api("x", "ab")], {"x": row(R.STOPPED)})
        self.assertEqual(b["to_unmark"], ["x"])
        self.assertEqual(b["live"], [])
        self.assertEqual(R.disagreement_count(b), 1)
        self.assertIn("to_unmark", R.DISAGREEING)
        self.assertIn("x", R.render(b))

    def test_only_this_script_s_own_marker_is_withdrawn(self):
        """A human's answer is reported, never reverted to `Running`."""
        names = ("Won", "Lost", "Inconclusive", "Turned off", R.FEATURE_FLAG)
        b = R.classify([api(n, "ab") for n in names], {n: row(n) for n in names})
        self.assertEqual(b["to_unmark"], [])
        self.assertEqual(sorted(b["live_disagree"]), sorted(names))
        self.assertEqual(R.disagreement_count(b), len(names))

    def test_a_row_already_reading_running_is_left_alone(self):
        b = R.classify([api("x", "ab")], {"x": row("Running")})
        self.assertEqual(b["to_unmark"], [])
        self.assertEqual(b["live"], ["x"])
        self.assertEqual(R.disagreement_count(b), 0)


class TestTheOnlyTwoWrites(unittest.TestCase):
    """`apply` writes this script's marker or withdraws it. Nothing else."""

    def test_apply_plans_both_directions_and_no_third(self):
        src = RECONCILE.read_text()
        body = src[src.index("def cmd_apply"):]
        import re as _re
        written = set(_re.findall(r'make_select\(([^)]*)\)', body))
        # The plan is built as (name, status) pairs, so the only literals that
        # reach a write are the ones in `todo`.
        self.assertTrue(written.issubset({"status"}), written)
        self.assertIn('(n, STOPPED) for n in sorted(b["to_mark"])', body)
        self.assertIn('(n, "Running") for n in sorted(b["to_unmark"])', body)


class TestTheDocumentedResolvePathDoesNotLoop(unittest.TestCase):
    """`In flight` on a stopped row would flip back on the next pass.

    `Stopped splitting` rows have a null `endedAt` by definition, so a skill
    that reads `endedAt` alone concludes `In flight` — which is in this
    script's writable set. The row would then alternate between the two
    forever, and an oscillation reads as drift rather than as the collision it
    is. `data:analyze-experiment` is the documented way out of the state, so it
    is the one that has to check `active` first.
    """

    ANALYZE = REPO / "plugins/data/skills/analyze-experiment/SKILL.md"

    def test_in_flight_is_writable_which_is_what_makes_the_loop_possible(self):
        self.assertIn("In flight", R.OVERWRITABLE)
        b = R.classify([api("x", "a")], {"x": row("In flight")})
        self.assertEqual(b["to_mark"], ["x"])

    def test_analyze_experiment_checks_active_before_calling_it_running(self):
        src = self.ANALYZE.read_text(encoding="utf-8")
        clause = src[src.index("`endedAt: null`"):][:3000]
        self.assertIn("distinct", clause,
                      "analyze-experiment Step 1d still reads `endedAt: null` as running "
                      "without checking how many arms `active` carries")
        self.assertIn("Stopped splitting", clause)
        self.assertIn("do not infer the verdict from `active`", clause.lower()
                      .replace("**", "").replace("do **not**", "do not"))


class TestEveryImplementationConfrontsTheAmbiguity(unittest.TestCase):
    """The `diff -q` guards compare copies; this compares implementations.

    Three files in two plugins measure `active` to decide whether an experiment
    is splitting traffic, under three filenames, and they do NOT agree on the
    repeated-letter case — deliberately. A read-side consumer errs toward live;
    a writer holds for a human. What every one of them must do is *say* which
    side it took, so a fourth cannot pick one by accident at `len()`.

    Discovery is by behaviour, not by a list, so a new implementation under any
    filename is caught. `resolve_running.py` was found this way — it was not in
    PROD-6116's scope and nobody knew it was a third copy of the rule.
    """

    CANONICAL_CASE = "bb"

    def _implementations(self):
        """Files that MEASURE `active` to decide whether an experiment is live.

        Two conditions, because either alone is wrong. `active` on its own hits
        unrelated dicts (a Linear query, a cost aggregation); `endedAt` on its
        own hits a script that merely prints the catalogue row
        (`eng:remove-package-experiment`). A decider does both — it reads the
        experiment catalogue and applies `len`/`set` to the variant string.
        """
        found = []
        for path in sorted(REPO.glob("plugins/**/*.py")):
            src = path.read_text(encoding="utf-8", errors="replace")
            if "endedAt" not in src:
                continue
            if re.search(r"(?:len|set)\([^\n]*active", src):
                found.append((path, src))
        return found

    def test_the_three_known_implementations_are_found(self):
        names = {p.name for p, _ in self._implementations()}
        self.assertEqual({"reconcile.py", "build_idea.py", "resolve_running.py"} & names,
                         {"reconcile.py", "build_idea.py", "resolve_running.py"})

    def test_each_names_the_repeated_letter_case(self):
        impls = self._implementations()
        self.assertTrue(impls, "no implementation found — the discovery regex has rotted")
        for path, src in impls:
            self.assertIn(
                self.CANONICAL_CASE, src,
                f"{path.relative_to(REPO)} measures `active` to decide whether an experiment "
                f'is splitting, without saying what it does with active="bb" — two characters, '
                f"one distinct arm. Pick a side and write down why (see reconcile.py's "
                f"`ambiguous_active`).",
            )


if __name__ == "__main__":
    unittest.main()


class TestAbsenceIsNotEvidenceOfOrigin(unittest.TestCase):
    """The defect the provenance probe exists to end — ENG-1370.

    On 2026-09-21 a tranche moved ended experiments out of the Firebase
    `/experiments` node into `/backup/experiments`: every Aura client reads that
    node in full on the conversion-critical paywall path, so its size is latency
    and cost. `GET /experiments` fell from ~3,000 rows to ~660.

    Nothing was lost. The Notion record is intact (3,087 rows, verdicts and all),
    sync only ever creates and updates Notion, and the API still answers for
    every archived row by id. But `classify` split the now-absent rows on Notion
    `Status` alone, with no provenance test of any kind, and `render` printed the
    result as "row(s) the experiment server never sourced" — filed under "not a
    disagreement". That claim was false for 2,347 of the 2,421 rows it was made
    about, and it grows with every tranche.

    `TestRealCorpus` below is the load-bearing one: a synthetic fixture cannot
    assert that the classifier gets the real corpus right.
    """

    def test_a_row_that_was_sourced_and_later_archived_is_never_called_unsourced(self):
        """The one-line statement of the bug, as a test."""
        b = R.classify([], {"travelBackgroundSounds": row("Lost", "2022-11-30")},
                       sourced=lambda n: True)
        out = R.render(b)
        self.assertEqual(b["absent_archived"], ["travelBackgroundSounds"])
        self.assertEqual(b["absent_unsourced"], [])
        self.assertNotIn("never sourced", out)
        self.assertNotIn("never held", out)
        self.assertIn("archived (server-sourced, since deleted)", out)

    def test_an_untested_row_is_named_untested_not_unsourced(self):
        """No probe ran, so the report may say only that it does not know."""
        b = R.classify([], {"whoKnows": row("Lost")})
        out = R.render(b)
        self.assertEqual(b["absent_untested"], ["whoKnows"])
        self.assertEqual(b["absent_unsourced"], [])
        self.assertIn("untested", out)
        self.assertNotIn("the experiment server never held", out)

    def test_a_failed_probe_reads_untested_never_unsourced(self):
        """An unreachable server cannot state that the server never held a row.

        `Prober` returns None on any non-404 failure. A check that could not run
        must not be indistinguishable from a check that ran and said no.
        """
        p = R.Prober(token="t", scope={"x"})
        p._cache["x"] = None
        b = R.classify([], {"x": row("Lost")}, sourced=p)
        self.assertEqual(b["absent_untested"], ["x"])
        self.assertEqual(b["absent_unsourced"], [])

    def test_a_deleted_row_that_reads_running_is_a_disagreement(self):
        """The one case in this family that is genuinely wrong.

        `stripeExistingPriceIncreaseTo9999` on 2026-09-22: created 2026-09-09,
        soft-deleted 2026-09-10, Notion still `Running`.
        """
        b = R.classify([], {"stripeExistingPriceIncreaseTo9999": row("Running")},
                       sourced=lambda n: True)
        self.assertEqual(b["deleted_but_running"], ["stripeExistingPriceIncreaseTo9999"])
        self.assertEqual(R.disagreement_count(b), 1)

    def test_a_running_row_the_server_never_held_is_not_a_disagreement(self):
        """Three of the four rows in the old status-only bucket were these.

        `AurieInvitePushCopy` is a push-copy test run outside the experiment
        server. Its `Running` is a human's record of something the catalogue was
        never asked about, so counting it put a permanent floor of three false
        disagreements under the headline.
        """
        b = R.classify([], {"AurieInvitePushCopy": row("Running")}, sourced=lambda n: False)
        self.assertEqual(b["unsourced_running"], ["AurieInvitePushCopy"])
        self.assertEqual(R.disagreement_count(b), 0)
        self.assertIn("never held them", R.render(b))

    def test_an_untested_running_row_stays_in_the_headline(self):
        """Not probing is not clearing. Conservative in the safe direction."""
        b = R.classify([], {"x": row("Running")})
        self.assertEqual(b["untested_running"], ["x"])
        self.assertEqual(R.disagreement_count(b), 1)
        self.assertIn("untested_running", R.DISAGREEING)

    def test_the_absent_buckets_are_not_disagreements(self):
        for key in ("absent_archived", "absent_unsourced", "absent_untested",
                    "unsourced_running"):
            self.assertNotIn(key, R.DISAGREEING, key)

    def test_the_six_absent_buckets_partition_the_absent_rows(self):
        notion = {f"r{i}": row(s) for i, s in
                  enumerate(("Running", "In flight", "Won", "Lost", None, "Turned off"))}
        for verdict in (True, False, None):
            b = R.classify([], notion, sourced=lambda n, v=verdict: v)
            got = [n for k in ("deleted_but_running", "unsourced_running", "untested_running",
                               "absent_archived", "absent_unsourced", "absent_untested")
                   for n in b[k]]
            self.assertEqual(sorted(got), sorted(notion), verdict)
            self.assertEqual(len(got), len(set(got)), verdict)


class TestRealCorpus(unittest.TestCase):
    """The classifier, run over every row that actually left the catalogue.

    The fixture is the real 2026-09-22 state: all 2,425 `Learnings/Experiments`
    rows absent from `GET /experiments?limit=10000`, each carrying the verdict of
    `GET /experiments/<id>` and its Notion `Status`. Real rows, produced by the
    real producer — the only thing that can assert the 2,347-row cluster the old
    wording mislabelled, which is the whole point of the fix.
    """

    @classmethod
    def setUpClass(cls):
        with open(REPO / "tests/fixtures/experiments_absent_provenance.json",
                  encoding="utf-8") as fh:
            cls.fx = json.load(fh)["rows"]
        cls.notion = {r["name"]: row(r["status"]) for r in cls.fx}
        cls.truth = {r["name"]: r["sourced"] for r in cls.fx}
        cls.b = R.classify([], cls.notion, sourced=lambda n: cls.truth[n])

    def test_the_corpus_is_the_one_the_defect_was_found_in(self):
        self.assertEqual(len(self.fx), 2425)
        self.assertEqual(sum(1 for r in self.fx if r["sourced"]), 2347)

    def test_the_mislabelled_cluster_is_classified_as_archived(self):
        """2,347 rows the old line called "never sourced". None may be again."""
        self.assertEqual(len(self.b["absent_archived"]) + len(self.b["deleted_but_running"]),
                         2347)
        self.assertEqual(len(self.b["absent_unsourced"]) + len(self.b["unsourced_running"]), 78)

    def test_every_sourced_row_lands_in_a_sourced_bucket(self):
        for name, was_sourced in self.truth.items():
            bucket = next(k for k in ("deleted_but_running", "unsourced_running",
                                      "absent_archived", "absent_unsourced")
                          if name in self.b[k])
            self.assertEqual(bucket in ("deleted_but_running", "absent_archived"),
                             was_sourced, name)

    def test_the_headline_holds_one_real_disagreement_not_four(self):
        """Status alone gave 4; three were rows the server never held."""
        self.assertEqual(self.b["deleted_but_running"], ["stripeExistingPriceIncreaseTo9999"])
        self.assertEqual(sorted(self.b["unsourced_running"]),
                         ["App Store Screenshots (Round 3)", "AurieInvitePushCopy",
                          "annualDunningEmail3Arm"])
        self.assertEqual(R.disagreement_count(self.b), 1)

    def test_the_render_makes_no_false_claim_about_2347_rows(self):
        out = R.render(self.b)
        self.assertIn("2421 absent from the live catalogue", out)
        self.assertIn("2346 archived (server-sourced, since deleted)", out)
        self.assertNotIn("never sourced", out)

    def test_the_archive_tranche_did_not_take_a_row_notion_called_running(self):
        """`eng:archive-ended-experiments` aborts if AI Knowledge says Running.

        The gate held. Every row the 2026-09-21 tranche removed is terminal in
        Notion; the single `Running` row that left the catalogue was soft-deleted
        2026-09-10, eleven days earlier and by a different mechanism.
        """
        tranche = [r for r in self.fx if (r["deleted_at"] or "").startswith("2026-09-21")]
        self.assertEqual(len(tranche), 2346)
        self.assertEqual([r["name"] for r in tranche
                          if r["status"] in ("Running", "In flight")], [])
