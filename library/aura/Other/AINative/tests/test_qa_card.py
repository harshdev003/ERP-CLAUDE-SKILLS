"""Guard tests for `qa:qa-linear-task`'s checklist operations.

The QA card's checklist is the run's state, and every execution mode rewrites it.
The silent failure is a tick that changes more than one box, or a parse that
misreads a case — the card still renders, and a case reads as passed that never
ran. Fixtures are real card bodies as Linear serialises them (`[X]`, `\\~`),
fetched from ENGMOB-2908 / ENGMOB-2916, not hand-written Markdown.

The transport is stubbed at `gql`, the one chokepoint; no request leaves.
"""

from __future__ import annotations

import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO, "plugins", "qa", "skills", "qa-linear-task", "scripts")
FIXTURES = os.path.join(REPO, "tests", "fixtures", "qa_card")
sys.path.insert(0, SCRIPTS)

import qa_card  # noqa: E402


def fixture(name: str) -> str:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


class FakeLinear:
    """Holds one issue body; records every description write.

    `interleave` simulates a second runner: on the first stale-check read it
    rewrites the body (and bumps updatedAt) before this runner writes.
    """

    def __init__(self, body: str, interleave=None):
        self.body = body
        self.version = 0
        self.writes: list[str] = []
        self.interleave = interleave

    def __call__(self, query, variables=None, soft=False):
        if "issueUpdate" in query:
            self.body = variables["d"]
            self.version += 1
            self.writes.append(self.body)
            return {"issueUpdate": {"success": True}}
        if "description" not in query and self.interleave:
            self.body, self.interleave = self.interleave(self.body), None
            self.version += 1
        return {
            "issue": {
                "id": "uuid",
                "identifier": "ENGMOB-1",
                "description": self.body,
                "updatedAt": str(self.version),
            }
        }


def run(fn, fake: FakeLinear, **kw) -> dict:
    qa_card.gql = fake
    buf = io.StringIO()
    with redirect_stdout(buf):
        fn(SimpleNamespace(**kw))
    return json.loads(buf.getvalue())


class TestParseRealCards(unittest.TestCase):
    def test_first_pass_card(self):
        cases = qa_card.parse_cases(fixture("engmob_2908.md"))
        s = qa_card.summarise(cases)
        self.assertEqual((s["total"], s["done"], s["open"], s["moved"]), (28, 20, 8, 0))
        self.assertEqual(
            [c["id"] for c in cases if c["status"] == "open"],
            ["3.2", "3.3", "3.4", "5.1", "5.2", "6.1", "6.2", "7.2"],
        )

    def test_uppercase_x_is_checked(self):
        cases = {c["id"]: c for c in qa_card.parse_cases(fixture("engmob_2908.md"))}
        self.assertEqual(cases["1.1"]["status"], "done")

    def test_struck_case_is_moved_not_open(self):
        cases = qa_card.parse_cases(fixture("engmob_2916.md"))
        moved = [c for c in cases if c["status"] == "moved"]
        self.assertEqual([c["id"] for c in moved], ["3.2"])
        self.assertEqual(qa_card.summarise(cases)["open"], 4)

    def test_sections_are_carried(self):
        cases = {c["id"]: c for c in qa_card.parse_cases(fixture("engmob_2908.md"))}
        self.assertTrue(cases["6.1"]["section"].startswith("6. Usage limits"))

    def test_non_case_bullets_ignored(self):
        body = "- [ ] no id here\n* Setup line\n- [ ] 1.1 real case"
        self.assertEqual([c["id"] for c in qa_card.parse_cases(body)], ["1.1"])


class TestTick(unittest.TestCase):
    def test_tick_changes_only_the_named_lines(self):
        before = fixture("engmob_2908.md")
        fake = FakeLinear(before)
        out = run(qa_card.cmd_tick, fake, issue="ENGMOB-1", case=["3.3", "7.2"], untick=False)
        self.assertEqual(sorted(out["changed"]), ["3.3", "7.2"])
        diff = [
            (a, b) for a, b in zip(before.split("\n"), fake.body.split("\n")) if a != b
        ]
        self.assertEqual(len(diff), 2)
        for a, b in diff:
            self.assertEqual(a.replace("[ ]", "[x]", 1), b)
        self.assertEqual(out["open"], 6)

    def test_tick_is_idempotent(self):
        fake = FakeLinear(fixture("engmob_2908.md"))
        out = run(qa_card.cmd_tick, fake, issue="ENGMOB-1", case=["1.1"], untick=False)
        self.assertEqual(out["changed"], [])
        self.assertEqual(fake.writes, [], "no-op tick must not write the body")

    def test_unknown_case_reported_missing(self):
        fake = FakeLinear(fixture("engmob_2908.md"))
        out = run(qa_card.cmd_tick, fake, issue="ENGMOB-1", case=["9.9"], untick=False)
        self.assertEqual(out["missing"], ["9.9"])

    def test_untick(self):
        fake = FakeLinear(fixture("engmob_2908.md"))
        out = run(qa_card.cmd_tick, fake, issue="ENGMOB-1", case=["1.1"], untick=True)
        self.assertEqual(out["changed"], ["1.1"])
        self.assertIn("- [ ] 1.1", fake.body)

    def test_id_prefix_does_not_collide(self):
        fake = FakeLinear("- [ ] 1.1 a\n- [ ] 1.10 b\n- [ ] 11.1 c")
        run(qa_card.cmd_tick, fake, issue="ENGMOB-1", case=["1.1"], untick=False)
        self.assertEqual(fake.body, "- [x] 1.1 a\n- [ ] 1.10 b\n- [ ] 11.1 c")


class TestConcurrentTick(unittest.TestCase):
    def test_other_runners_tick_survives(self):
        """A tick written between our read and our write must not be overwritten."""
        other = lambda body: body.replace("- [ ] 5.1", "- [x] 5.1", 1)
        fake = FakeLinear(fixture("engmob_2908.md"), interleave=other)
        out = run(qa_card.cmd_tick, fake, issue="ENGMOB-1", case=["3.3"], untick=False)
        self.assertEqual(out["changed"], ["3.3"])
        self.assertIn("- [x] 5.1", fake.body, "the other runner's tick was lost")
        self.assertIn("- [x] 3.3", fake.body)
        self.assertEqual(len(fake.writes), 1)

    def test_gives_up_when_the_card_never_settles(self):
        class Churning(FakeLinear):
            def __call__(self, query, variables=None, soft=False):
                if "description" not in query and "issueUpdate" not in query:
                    self.version += 1
                return super().__call__(query, variables, soft)

        fake = Churning(fixture("engmob_2908.md"))
        qa_card.gql = fake
        with self.assertRaises(SystemExit), redirect_stdout(io.StringIO()):
            qa_card.cmd_tick(SimpleNamespace(issue="ENGMOB-1", case=["3.3"], untick=False))
        self.assertEqual(fake.writes, [], "never write over a body that keeps changing")


class TestPagination(unittest.TestCase):
    def test_children_follows_every_page(self):
        def child(n):
            return {
                "id": f"u{n}", "identifier": f"ENGMOB-{n}", "title": f"c{n}", "url": "",
                "state": {"name": "Todo", "type": "unstarted"},
                "labels": {"nodes": [{"name": "QA"}] if n == 250 else []},
            }

        pages = {None: (range(0, 100), "c1"), "c1": (range(100, 200), "c2"), "c2": (range(200, 251), None)}

        def fake(query, variables=None, soft=False):
            ids, nxt = pages[variables["after"]]
            return {"issue": {"children": {
                "nodes": [child(n) for n in ids],
                "pageInfo": {"hasNextPage": nxt is not None, "endCursor": nxt},
            }}}

        qa_card.gql = fake
        buf = io.StringIO()
        with redirect_stdout(buf):
            qa_card.cmd_children(SimpleNamespace(issue="ENGMOB-1"))
        out = json.loads(buf.getvalue())
        self.assertEqual(len(out), 251)
        self.assertEqual([c["identifier"] for c in out if "QA" in c["labels"]], ["ENGMOB-250"])


class TestPickLabel(unittest.TestCase):
    WS = {"id": "ws", "name": "QA", "team": None}
    MOB = {"id": "mob", "name": "QA", "team": {"key": "ENGMOB"}}
    ENG = {"id": "eng", "name": "QA", "team": {"key": "ENG"}}

    def test_workspace_label_wins_over_earlier_team_label(self):
        self.assertEqual(qa_card.pick_label([self.ENG, self.WS], "ENGMOB")["id"], "ws")

    def test_own_team_label_when_no_workspace_label(self):
        self.assertEqual(qa_card.pick_label([self.ENG, self.MOB], "ENGMOB")["id"], "mob")

    def test_never_another_teams_label(self):
        self.assertIsNone(qa_card.pick_label([self.ENG], "ENGMOB"))
        self.assertIsNone(qa_card.pick_label([self.ENG], None))


class TestMove(unittest.TestCase):
    def test_move_strikes_and_points(self):
        fake = FakeLinear(fixture("engmob_2908.md"))
        out = run(qa_card.cmd_move, fake, issue="ENGMOB-1", case=["3.3", "1.1"], to="ENGMOB-2916")
        self.assertEqual(out["changed"], ["3.3"], "a passed case is never moved")
        self.assertEqual((out["open"], out["moved"]), (7, 1))
        line = next(l for l in fake.body.split("\n") if "3.3" in l and "~~" in l)
        self.assertTrue(line.startswith("- [ ] ~~3.3 Airplane mode"))
        self.assertTrue(line.endswith("~~ → moved to ENGMOB-2916"))

    def test_moved_line_round_trips_as_moved(self):
        fake = FakeLinear("- [ ] 3.3 thing")
        run(qa_card.cmd_move, fake, issue="ENGMOB-1", case=["3.3"], to="X-1")
        self.assertEqual(qa_card.parse_cases(fake.body)[0]["status"], "moved")
        again = run(qa_card.cmd_move, fake, issue="ENGMOB-1", case=["3.3"], to="X-1")
        self.assertEqual(again["changed"], [])


if __name__ == "__main__":
    unittest.main()
