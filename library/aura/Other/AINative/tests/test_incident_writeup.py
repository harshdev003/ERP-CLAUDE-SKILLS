"""Incident write-ups follow the house rules (ENG-1818): a label header and fact-only wording.

Pinned over every copy of `writeup.py`, and through the renderers that use it: each skill's plan output
starts with the label, and a refused word stops the document instead of shipping it.
"""
from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "plugins/eng/skills/preserve-incident-account/scripts/writeup.py"
COPIES = [CANONICAL, ROOT / "plugins/eng/skills/preserve-prod-logs/scripts/writeup.py"]


def load(path):
    spec = importlib.util.spec_from_file_location(f"writeup_{path.parts[-3]}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class CopiesTest(unittest.TestCase):
    def test_every_copy_is_listed_and_identical(self):
        found = sorted(ROOT.glob("plugins/**/writeup.py"))
        self.assertEqual(found, sorted(COPIES))
        for c in COPIES[1:]:
            self.assertEqual(c.read_bytes(), CANONICAL.read_bytes(), c)


class RulesTest(unittest.TestCase):
    def test_each_copy(self):
        for path in COPIES:
            w = load(path)
            with self.subTest(copy=path.parts[-3]):
                out = w.render("Observed 931 requests on 10-30.", date="2026-11-05")
                self.assertEqual(out.split("\n")[0],
                                 "**Draft — Privileged & Confidential** — 2026-11-05 — reflects information currently known.")
                for bad in ("This was a data breach.", "An unauthorized read.", "No authorization was given.",
                            "It violated the policy.", "A Violation of the rules.", "Two authorisations.",
                            "A data-breach timeline.", "A policy-violation.", "Unauthorised access.",
                            "Authorizations: none", "authorisations: two"):
                    with self.assertRaises(ValueError, msg=bad):
                        w.render(bad)
                # Not prose: an HTTP header in a code span, and words that merely contain the letters.
                self.assertEqual(w.problems("`Authorization: Bearer x` and `curl -H \"Authorization: Bearer\"`"), [])
                self.assertEqual(w.problems("A tightly scoped read; an authoritative source."), [])

    def test_the_full_list_is_refused_with_its_replacement(self):
        w = load(CANONICAL)
        cases = {
            "A data breach.": "incident", "Access was unauthorised.": "the firm's access",
            "Records were exposed.": "accessible", "The account was compromised.": "accessible",
            "Data leaked.": "accessible", "Files were stolen.": "downloaded", "They exfiltrated rows.": "downloaded",
            "The attacker returned.": "the firm's activity", "They hacked the API.": "the firm's activity",
            "PHI was read.": "mood entries", "Health data and sensitive data.": "mood entries",
            "A violation.": "factually", "It was noncompliant.": "factually", "We failed to rotate.": "factually",
            "That was negligent.": "never", "We should have known.": "never", "Our mistake.": "never",
            "Victims were told.": "users whose data was involved",
            "Affected individuals: 3.": "users whose data was involved",
            "Notification required.": "whether notice is advisable or required",
        }
        for text, use in cases.items():
            with self.subTest(text=text):
                with self.assertRaises(ValueError) as e:
                    w.render(text)
                self.assertIn(use, str(e.exception))

    def test_the_firms_own_words_pass_when_quoted_as_theirs(self):
        w = load(CANONICAL)
        quoted = "Their report:\n> The firm's words:\n> we hacked the API and exfiltrated data\nAccessed 931 times."
        self.assertEqual(w.problems(quoted), [])
        # Any other blockquote, such as a plan's paste lines, is checked.
        self.assertEqual(w.problems("**the Firebase owner**\n> go 7: snapshot the leaked account"), ["leaked"])
        self.assertEqual(w.problems("> The firm's words:\n> attack\n\n> attack"), ["attack"])
        self.assertEqual(w.problems("Accessed, downloaded, the firm's activity, users whose data was involved."), [])

    def test_check_cli_wants_the_header_first(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write("Timeline\n\n| Time | Action |\n")
        r = subprocess.run([sys.executable, str(CANONICAL), "check", f.name], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("missing header", r.stdout)
        # Naming the label is not the header: the date and the closing clause are part of it.
        pathlib.Path(f.name).write_text("Draft — Privileged & Confidential\n\nTimeline", encoding="utf-8")
        r = subprocess.run([sys.executable, str(CANONICAL), "check", f.name], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        w = load(CANONICAL)
        pathlib.Path(f.name).write_text(w.render("Timeline"), encoding="utf-8")
        r = subprocess.run([sys.executable, str(CANONICAL), "check", f.name], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)


def _run(copy, cmd, text, name="timeline.md"):
    d = tempfile.mkdtemp()
    f = pathlib.Path(d, name)
    f.write_text(text, encoding="utf-8")
    return subprocess.run([sys.executable, str(copy), cmd, str(f)], capture_output=True, text=True, encoding="utf-8")


class PersonalDataTest(unittest.TestCase):
    """A write-up for counsel carries no email, full uid or IP address, and no card id in its file name.
    Only the kind is named, never the value."""

    UID = "TESTUIDaaaaaaaaaaaaaaaaaaaa01"

    def test_each_kind_is_refused_by_check_and_wrap(self):
        cases = {"an email address": "Reported by person@example.com.",
                 "a full uid": f"The account {self.UID} was disabled.",
                 "a full uid ": "Its uuid is 3f2504e0-4f89-11d3-9a0c-0305e82c3301.",
                 "an IP address": "Requests came from 203.0.113.7 and 2001:db8:0:0:1:0:0:1.",
                 "an IP address ": "Then from 2001:db8::1.",
                 "an IP address  ": "Loopback ::1 and fe80::1%en0 answered.",
                 "an IP address   ": "Seen at 203.0.113.7:443 and ip:198.51.100.2 then.",
                 "an IP address    ": "Source 198.51.100.9: refused.",
                 "an IP address     ": "Source 2001:db8::1: refused, then fe80::1:.",
                 "an IP address      ": "Prefix 2001:db8:::, then refused.",
                 "an IP address       ": "Then 2001:db8::."}
        for path in COPIES:
            w = load(path)
            for kind, text in cases.items():
                with self.subTest(copy=path.parts[-3], kind=kind):
                    self.assertEqual(w.personal_data(text), [kind.strip()])
                    for cmd, body in (("check", w.render(text)), ("wrap", text)):
                        r = _run(path, cmd, body)
                        self.assertEqual(r.returncode, 1, cmd)
                        self.assertIn(kind.strip(), r.stdout + r.stderr)
                        for value in ("person@example.com", self.UID, "203.0.113.7", "3f2504e0"):
                            self.assertNotIn(value, r.stdout + r.stderr)

    def test_prefixes_masks_and_versions_pass(self):
        w = load(CANONICAL)
        self.assertEqual(w.personal_data("Account TESTUI… (j***@example.com), release 2.304.8, sha 07bc101d."), [])
        self.assertEqual(w.personal_data("At 10:30:00 UTC, release 1.2.3.4.5 and ratio 3:2."), [])
        r = _run(CANONICAL, "wrap", "Account TESTUI… was disabled on 11-05.")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_the_firms_quoted_words_are_not_checked(self):
        w = load(CANONICAL)
        self.assertEqual(w.personal_data("> The firm's words:\n> from 203.0.113.7"), [])

    def test_a_card_id_in_the_file_name_is_refused(self):
        w = load(CANONICAL)
        body = w.render("Timeline")
        for name in ("prod-6926-timeline.md", "PROD-6926.md", "engback-2292-notes.md"):
            with self.subTest(name):
                r = _run(CANONICAL, "check", body, name)
                self.assertEqual(r.returncode, 1)
                self.assertIn("card id in the file name", r.stdout)
                self.assertEqual(_run(CANONICAL, "wrap", "Timeline", name).returncode, 1)
        self.assertEqual(_run(CANONICAL, "check", body, "timeline-2026-11-05.md").returncode, 0)

    def test_plans_keep_their_local_paste_lines(self):
        # render() alone (the plans) is unchanged: a go line typed into a local session carries the full uid.
        w = load(CANONICAL)
        self.assertIn(self.UID, w.render(f"> go 1: snapshot {self.UID}"))


class RenderersTest(unittest.TestCase):
    def test_preserve_prod_logs_plan_is_labelled(self):
        sys.path.insert(0, str(COPIES[1].parent))
        plan_hold = load(COPIES[1].with_name("plan_hold.py"))
        p = {"card": "TEST-1", "start": "a", "end": "b", "projects": ["p"], "log_buckets": ["_Default"],
             "uid_count": 0, "steps": []}
        self.assertIn("Draft — Privileged & Confidential", plan_hold.render(p).split("\n")[0])


if __name__ == "__main__":
    unittest.main()
