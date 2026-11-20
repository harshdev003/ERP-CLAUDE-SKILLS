"""`eng:monitor-response-message-drops` `--post`: what gets posted, what is held, and what
never reaches Slack.

The failure modes worth pinning live in branches a live run rarely exercises: a `read`
item with no verdict must be HELD (a wrong category posted to the channel looks exactly
like a right one); a text over the verbatim budget with no summary must be held rather than
posted in full; an id already in the posted map must never post again however wide the
window; and a Slack-level failure must be recorded per drop, not raised. The transport is
stubbed at its single chokepoint (`send`) so each branch is reached on demand.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import pathlib
import sys
import types
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = REPO / "plugins/eng/skills/monitor-response-message-drops/scripts/monitor.py"


def _load():
    """Import monitor.py with its `gcp` sibling stubbed out.

    That module imports `google-cloud-logging` at module scope and calls `sys.exit(2)`
    when it is absent, which is right for a run that is about to query Cloud Logging and
    wrong for a test: the suite is stdlib-only and never reaches GCP, so on a machine
    without the package the import kills the whole test module. Registering a stub under
    the name `gcp` satisfies monitor.py's `import gcp` without loading the real file.
    """
    sys.modules.setdefault("gcp", types.ModuleType("gcp"))
    spec = importlib.util.spec_from_file_location("monitor_drops", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = _load()

META = {"dialogueId": "d-1", "conversationId": "C-1", "user": "uid-1", "name": "coach-prompter", "version": "1.16.9"}


def entry(rid, verdicts, ts="2026-09-04T22:09:00.000Z"):
    return {"ts": ts, "responseId": rid, "keptChars": 10, "droppedCount": len(verdicts),
            "analysis": {"items": [{"verdict": v, "coverage": 0.3, "jaccard": 0.1, "keptChars": 10, "droppedChars": 5}
                                   for v in verdicts]}}


def private(kept="kept text", dropped=("dropped text",), user="user turn", model="gpt-5-chat-latest"):
    return {"texts": [kept, *dropped], "metadata": dict(META), "model": model, "user_turn": user}


class ResolveVerdict(unittest.TestCase):
    def test_feature_repeat_and_opener_are_self_decided(self):
        self.assertEqual(M.resolve_verdict(entry("r", ["repeat"]), {}), "repeat")
        self.assertEqual(M.resolve_verdict(entry("r", ["opener"]), {}), "opener")

    def test_read_waits_for_the_operator(self):
        self.assertIsNone(M.resolve_verdict(entry("r", ["read"]), {}))
        # a second item that needs reading holds the whole drop, even next to an opener
        self.assertIsNone(M.resolve_verdict(entry("r", ["opener", "read"]), {}))

    def test_file_verdict_wins_over_feature_verdict(self):
        self.assertEqual(M.resolve_verdict(entry("r", ["opener"]), {"r": {"verdict": "sign-off"}}), "sign-off")
        self.assertEqual(M.resolve_verdict(entry("r", ["read"]), {"r": {"verdict": "continuation"}}), "continuation")

    def test_unknown_verdict_is_refused_not_posted(self):
        with self.assertRaises(ValueError):
            M.resolve_verdict(entry("r", ["read"]), {"r": {"verdict": "meh"}})


class SummaryRule(unittest.TestCase):
    def test_short_texts_need_nothing(self):
        self.assertEqual(M.missing_summaries(private(), {}), [])

    def test_boundary_is_inclusive(self):
        self.assertEqual(M.missing_summaries(private(kept="k" * M.LONG_TEXT_CHARS), {}), [])
        self.assertEqual(M.missing_summaries(private(kept="k" * (M.LONG_TEXT_CHARS + 1)), {}), ["kept"])

    def test_each_long_text_is_named(self):
        p = private(kept="k" * 700, dropped=("d" * 700,), user="u" * 700)
        self.assertEqual(M.missing_summaries(p, {}), ["user", "kept", "dropped"])
        self.assertEqual(M.missing_summaries(p, {"summaries": {"user": "s", "kept": "s"}}), ["dropped"])


class FormatPost(unittest.TestCase):
    def test_shape_and_bracket_line(self):
        text = M.format_post(entry("resp_abc", ["read"]), private(), "paraphrase", {})
        self.assertTrue(text.startswith("*MESSAGE DROPPED*\n:repeat: *Paraphrase*"))
        self.assertIn("2026-09-04 22:09:00Z · kept 9 chars · dropped 12 chars", text)
        self.assertIn("<https://platform.openai.com/logs/resp_abc|OpenAI log>", text)
        self.assertIn("*User turn*\n```user turn```", text)
        self.assertIn("*Assistant — kept (delivered)*\n```kept text```", text)
        self.assertIn("*Assistant — dropped*\n```dropped text```", text)
        self.assertIn("`[dialogue: d-1, conversation: C-1, user: uid-1, prompt:coach-prompter:1.16.9, "
                      "model: gpt-5-chat-latest]`", text)

    def test_a_summary_for_a_short_text_is_ignored(self):
        # The verdicts schema shows all three summary keys, so an operator will supply
        # one for a short text too; substituting it would drop a line the channel is
        # meant to see in full.
        short = "movie night sorted"
        text = M.format_post(entry("r", ["read"]), private(dropped=(short,)), "sign-off",
                             {"summaries": {"dropped": "a closer"}})
        self.assertIn(f"*Assistant — dropped*\n```{short}```", text)
        self.assertNotIn("a closer", text)
        self.assertNotIn("summary of", text)

    def test_summary_replaces_verbatim_and_is_marked(self):
        long_kept = "k" * 700
        text = M.format_post(entry("r", ["read"]), private(kept=long_kept), "paraphrase",
                             {"summaries": {"kept": "the gist"}})
        self.assertIn("*Assistant — kept (delivered)* _(summary of 700 chars)_\n```the gist```", text)
        self.assertNotIn(long_kept, text)

    def test_note_renders_in_italics(self):
        text = M.format_post(entry("r", ["read"]), private(), "paraphrase", {"note": "safety-adjacent turn"})
        self.assertIn("\n_safety-adjacent turn_\n", text)

    def test_fence_inside_text_cannot_break_the_block(self):
        text = M.format_post(entry("r", ["read"]), private(kept="a ``` b"), "paraphrase", {})
        self.assertNotIn("```a ``` b```", text)


class PostDrops(unittest.TestCase):
    def run_post(self, entries, private_by_rid, verdicts=None, posted=None, fail=None, persist=None,
                 fail_after=None):
        sent = []
        def send(text):
            sent.append(text)
            if fail_after is not None and len(sent) > fail_after:
                raise SystemExit("Non-JSON response from chat.postMessage")
            if fail == "raise":
                raise RuntimeError("boom")
            if fail == "exit":
                raise SystemExit("Slack chat.postMessage failed: invalid_auth")
            if fail == "slack":
                return {"ok": False, "error": "channel_not_found"}
            return {"ok": True, "ts": f"171.{len(sent)}", "permalink": f"https://slack/p{len(sent)}"}
        posted = {} if posted is None else posted
        out = M.post_drops(entries, private_by_rid, verdicts or {}, posted, dry_run=False, send=send,
                           persist=persist)
        return out, posted, sent

    def test_self_decided_post_and_read_is_held(self):
        entries = [entry("op", ["opener"], ts="2026-09-04T22:14:34Z"), entry("rd", ["read"], ts="2026-09-04T22:09:00Z")]
        out, posted, sent = self.run_post(entries, {"op": private(), "rd": private()})
        self.assertEqual([p["responseId"] for p in out["posted"]], ["op"])
        self.assertEqual(out["held"], [("rd", "needs a verdict (read the pair below)")])
        self.assertEqual(len(sent), 1)
        self.assertIn("op", posted)
        self.assertNotIn("rd", posted)
        self.assertEqual(posted["op"]["permalink"], "https://slack/p1")

    def test_oldest_first(self):
        entries = [entry("b", ["opener"], ts="2026-09-04T23:00:00Z"), entry("a", ["opener"], ts="2026-09-04T22:00:00Z")]
        out, _, _ = self.run_post(entries, {"a": private(), "b": private()})
        self.assertEqual([p["responseId"] for p in out["posted"]], ["a", "b"])

    def test_already_posted_never_posts_again(self):
        out, posted, sent = self.run_post([entry("op", ["opener"])], {"op": private()}, posted={"op": {"slack_ts": "1"}})
        self.assertEqual(sent, [])
        self.assertEqual(out["skipped"], 1)
        self.assertEqual(posted["op"], {"slack_ts": "1"})

    def test_missing_summary_holds_even_with_a_verdict(self):
        out, _, sent = self.run_post([entry("r", ["read"])], {"r": private(kept="k" * 700)},
                                     verdicts={"r": {"verdict": "paraphrase"}})
        self.assertEqual(sent, [])
        self.assertEqual(out["held"], [("r", f"needs a summary for: kept (> {M.LONG_TEXT_CHARS} chars)")])

    def test_verdict_and_summary_release_the_hold(self):
        out, posted, sent = self.run_post([entry("r", ["read"])], {"r": private(kept="k" * 700)},
                                          verdicts={"r": {"verdict": "continuation", "summaries": {"kept": "gist"}}})
        self.assertEqual(out["posted"][0]["verdict"], "continuation")
        self.assertIn(":warning: *Genuine continuation*", sent[0])
        self.assertIn("r", posted)

    def test_no_stored_text_is_held_not_posted(self):
        out, _, sent = self.run_post([entry("r", ["opener"])], {})
        self.assertEqual(sent, [])
        self.assertEqual(out["held"], [("r", "no stored text (see analysis error)")])

    def test_slack_failure_is_recorded_and_the_rest_continue(self):
        entries = [entry("a", ["opener"], ts="2026-09-04T22:00:00Z"), entry("b", ["opener"], ts="2026-09-04T23:00:00Z")]
        out, posted, _ = self.run_post(entries, {"a": private(), "b": private()}, fail="slack")
        self.assertEqual(out["posted"], [])
        self.assertEqual(out["held"], [("a", "slack error: channel_not_found"), ("b", "slack error: channel_not_found")])
        self.assertEqual(posted, {})

    def test_transport_exception_does_not_raise(self):
        out, posted, _ = self.run_post([entry("a", ["opener"])], {"a": private()}, fail="raise")
        self.assertEqual(posted, {})
        self.assertTrue(out["held"][0][1].startswith("slack error: RuntimeError"))

    def test_helper_sys_exit_is_a_failed_post_not_a_dead_tick(self):
        # slack_post exits the process on a transport fault, and SystemExit is not an
        # Exception — unguarded it would escape the per-drop handler.
        entries = [entry("a", ["opener"], ts="2026-09-04T22:00:00Z"),
                   entry("b", ["opener"], ts="2026-09-04T23:00:00Z")]
        out, posted, sent = self.run_post(entries, {"a": private(), "b": private()}, fail="exit")
        self.assertEqual(posted, {})
        self.assertEqual(len(sent), 2)   # the second drop was still attempted
        self.assertTrue(all(r.startswith("slack error: SystemExit") for _, r in out["held"]))

    def test_a_mid_batch_abort_keeps_what_already_posted(self):
        # The state write used to happen only after the loop returned, so an abort
        # halfway lost the record and the next tick re-posted the same conversation.
        snapshots = []
        entries = [entry(r, ["opener"], ts=f"2026-09-04T2{i}:00:00Z") for i, r in enumerate("abc")]
        out, posted, _ = self.run_post(entries, {r: private() for r in "abc"},
                                       persist=lambda cur: snapshots.append(dict(cur)), fail_after=2)
        self.assertEqual([p["responseId"] for p in out["posted"]], ["a", "b"])
        self.assertEqual(sorted(snapshots[-1]), ["a", "b"])   # persisted before the abort
        self.assertEqual(len(snapshots), 2)                    # one write per successful post

    def test_dry_run_posts_nothing_and_records_nothing(self):
        posted = {}
        sent = []
        with contextlib.redirect_stdout(io.StringIO()):   # dry run renders to the terminal
            out = M.post_drops([entry("a", ["opener"])], {"a": private()}, {}, posted, dry_run=True,
                               send=lambda t: sent.append(t))
        self.assertEqual(sent, [])
        self.assertEqual(posted, {})
        self.assertTrue(out["posted"][0]["dry_run"])


class RenderPostSummary(unittest.TestCase):
    def test_held_read_pairs_print_their_text_and_continuations_are_flagged(self):
        entries = [entry("h", ["read"]), entry("c", ["read"])]
        outcome = {"posted": [{"responseId": "c", "verdict": "continuation", "permalink": "https://slack/p1"}],
                   "held": [("h", "needs a verdict (read the pair below)")], "skipped": 3}
        text = M.render_post_summary(outcome, {"h": private(kept="KEPT-H", dropped=("DROP-H",), user="USER-H")}, entries)
        self.assertIn("posted to C0BV9DHUT8C: 1 {'continuation': 1}   already posted: 3   held: 1", text)
        self.assertIn("NOTE: 1 genuine continuation(s) posted", text)
        for needle in ("USER-H", "KEPT-H", "DROP-H", "--post --verdicts"):
            self.assertIn(needle, text)

    def test_slack_hold_does_not_reprint_text(self):
        outcome = {"posted": [], "held": [("h", "slack error: ratelimited")], "skipped": 0}
        text = M.render_post_summary(outcome, {"h": private(kept="KEPT-H")}, [entry("h", ["opener"])])
        self.assertIn("HELD h: slack error: ratelimited", text)
        self.assertNotIn("KEPT-H", text)


class SlackPostCopy(unittest.TestCase):
    def test_bundled_copy_matches_the_canonical(self):
        canonical = REPO / "plugins/shared/skills/post-to-slack/scripts/slack_post.py"
        copy = SCRIPT.parent / "slack_post.py"
        self.assertEqual(copy.read_bytes(), canonical.read_bytes())



def log(message, name):
    return {"jsonPayload": {"message": message, "prompterName": name}}


class Prompters(unittest.TestCase):
    """The two prompter-name warnings: counted by name, with follow-ups raised for every
    unknown name and only once per retired name."""

    def _collect(self, entries_by_msg):
        calls = []

        def list_entries(auth, filter_str, max_entries):
            calls.append(filter_str)
            for msg, entries in entries_by_msg.items():
                if f'jsonPayload.message="{msg}"' in filter_str:
                    return iter(entries)
            return iter([])

        M.gcp.list_entries = list_entries
        now = M.dt.datetime(2026, 11, 18, 12, tzinfo=M.dt.timezone.utc)
        return M.collect_prompters(None, now), calls

    def test_counts_by_name_over_24h_on_every_version(self):
        p, calls = self._collect({
            "Unknown prompter name": [log("Unknown prompter name", "quickProcesing")] * 3,
            "Retired prompter name counted": [log("Retired prompter name counted", "quickProcessingSentinel")] * 2,
        })
        self.assertEqual(p["unknown"], {"quickProcesing": 3})
        self.assertEqual(p["retired"], {"quickProcessingSentinel": 2})
        self.assertEqual(p["since"], "2026-11-17T12:00:00Z")
        self.assertTrue(all("serviceContext.version" not in c for c in calls))

    def test_a_name_outside_the_alphabet_is_masked(self):
        p, _ = self._collect({"Unknown prompter name": [log("Unknown prompter name", "a`b <@U1>" + "x" * 100)]})
        (name,) = p["unknown"]
        self.assertEqual(len(name), 64)
        self.assertNotIn("`", name)
        self.assertNotIn("<", name)

    def test_unknown_raises_every_tick_with_a_stable_text(self):
        p = {"hours": 24, "unknown": {"x": 1}, "retired": {}}
        first = M.prompter_followups(p, {}, "t1")
        p["unknown"] = {"x": 9}
        second = M.prompter_followups(p, {}, "t2")
        self.assertEqual(len(first), 1)
        self.assertEqual(first, second)
        self.assertTrue(first[0]["key"].startswith("response-drops:unknown-prompter:x-"))

    def test_retired_raises_only_the_first_time(self):
        seen = {}
        p = {"hours": 24, "unknown": {}, "retired": {"quickProcessingSentinel": 2}}
        self.assertEqual(len(M.prompter_followups(p, seen, "t1")), 1)
        self.assertEqual(seen, {"quickProcessingSentinel": "t1"})
        self.assertEqual(M.prompter_followups(p, seen, "t2"), [])

    def test_zero_retired_says_the_alias_can_go(self):
        out = M.render_prompters({"hours": 24, "unknown": {}, "retired": {}, "truncated": []}, [])
        self.assertIn("retired: 0", out)
        self.assertIn("can be removed", out)
        self.assertNotIn("FOLLOWUP", out)

    def test_a_kept_alias_is_counted_but_never_raised(self):
        seen = {}
        p = {"hours": 24, "unknown": {}, "retired": {"chat": 40}, "truncated": []}
        self.assertEqual(M.prompter_followups(p, seen, "t1"), [])
        self.assertEqual(seen, {})
        out = M.render_prompters(p, [])
        self.assertIn("retired: chat=40", out)
        self.assertIn("can be removed", out)   # no removable alias was called
        self.assertIn("kept by decision: chat", out)

    def test_a_capped_retired_read_never_says_an_alias_can_go(self):
        p = {"hours": 24, "unknown": {}, "retired": {"chat": 20000}, "truncated": ["retired"]}
        out = M.render_prompters(p, [])
        self.assertNotIn("can be removed", out)
        self.assertIn("truncated at the fetch cap for retired", out)

    def test_followups_print_one_line_each(self):
        p = {"hours": 24, "unknown": {"x": 1}, "retired": {"y": 1}, "truncated": []}
        out = M.render_prompters(p, M.prompter_followups(p, {}, "t"))
        self.assertEqual(out.count("\nFOLLOWUP "), 2)
        self.assertNotIn("can be removed", out)

    def test_the_text_readout_carries_every_raise_followup_field(self):
        p = {"hours": 24, "unknown": {"x": 1}, "retired": {"y": 1}, "truncated": []}
        fus = M.prompter_followups(p, {}, "t")
        out = M.render_prompters(p, fus)
        for f in fus:
            for k in ("what", "why", "ask"):
                if f.get(k):
                    self.assertIn(f"    {k}: {f[k]}", out)
        self.assertIn("    ask: ", out)   # the decision carries an ask

    def test_keys_pass_raise_followups_key_rule(self):
        """The keys must be accepted by the caller they are meant for: a refused key raises
        nothing, and for a retired name the state already marks it raised."""
        src = (REPO / "plugins/shared/skills/raise-followup/scripts/followup.py").read_text()
        key_re = M.re.compile(M.re.search(r'KEY_RE = re\.compile\(r"(.+?)"\)', src).group(1))
        names = ["quickProcessingSentinel", "QuickProcessingSentinel", "a" * 64, "x"]
        p = {"hours": 24, "unknown": {n: 1 for n in names}, "retired": {n: 1 for n in names}}
        keys = [f["key"] for f in M.prompter_followups(p, {}, "t")]
        self.assertEqual(len(keys), 8)
        self.assertEqual(len(set(keys)), 8)
        for k in keys:
            self.assertRegex(k, key_re)

    def test_follow_up_text_passes_raise_followups_own_check(self):
        """raise-followup refuses a field that looks like an id, token or IP. Names are
        caller-supplied, so run its own check on what an odd name produces."""
        spec = importlib.util.spec_from_file_location(
            "followup", REPO / "plugins/shared/skills/raise-followup/scripts/followup.py")
        fu = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fu)
        cfg = fu.load_config()
        names = ["coach-prompter", "a1b2c3d4e5f6g7h8i9j0k1", "10.0.0.1", "ghp_" + "a" * 30,
                 "sk-" + "b" * 25, "x" * 64]
        p = {"hours": 24, "unknown": {n: 1 for n in names}, "retired": {n: 1 for n in names}}
        for f in M.prompter_followups(p, {}, "t"):
            with self.subTest(f["key"]):
                self.assertEqual(fu.problems({**f, "session": "AINative"}, cfg), [])
        for plain in ("quickProcessingSentinel", "coach-prompter", "quick-processing-sentinel-prompter",
                      "chat_v2", "coach.prompter"):
            self.assertEqual(M.shown_name(plain), f"`{plain}`")
        for odd in names[1:5]:
            self.assertIn("unusual name", M.shown_name(odd))


if __name__ == "__main__":
    unittest.main()
