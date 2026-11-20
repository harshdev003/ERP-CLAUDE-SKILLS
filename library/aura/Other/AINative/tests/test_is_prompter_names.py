"""Every IntelligenceService stored prompter name is classified in AINative's lists.

A new stored name has twice gone missing from `CONVERSATION_PROMPTERS`, and
nothing noticed: `guardrails-handler-prompter` (#766, rated intercepted sessions
came back with empty transcripts) and `quick-processing-sentinel-prompter`
(#1192, 0 turns for every Quick Processing conversation).

CI cannot reach IntelligenceService, so this runs against a checked-in snapshot,
`fixtures/is_prompter_names.json`, written by `scripts/refresh-is-prompter-names.py`
from an IntelligenceService checkout. Refreshing the snapshot is the step that
brings a new name in; this test then names every list that still needs it.

Each stored name must be exactly one of: a transcript turn (`CONVERSATION_PROMPTERS`,
every `responses_reader.py` copy), a journal entry (`JOURNAL_PROMPTERS`), or
declared below as not a turn. The AINative lists are read with `ast`, never
imported, so no skill dependency is needed.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from reader_harness import REPO_ROOT

SNAPSHOT = REPO_ROOT / "tests" / "fixtures" / "is_prompter_names.json"
REPORT = REPO_ROOT / "plugins/data/skills/report-aurie-conversations/scripts/report.py"
QA_CHOICES = [REPO_ROOT / f"plugins/qa/skills/{skill}/scripts/{script}"
              for skill in ("chat-with-aurie", "chat-with-aura")
              for script in ("aura_chat.py", "aura_transcript.py")]
REFRESH = REPO_ROOT / "scripts" / "refresh-is-prompter-names.py"

# Stored names whose output never reaches the user as a conversation turn:
# single-shot generators, renderers, ingest and sorting passes, guardrail
# classifiers, onboarding steps and the Quick Processing done screen. A new name
# goes here only once it is known not to be a turn; when in doubt it is a turn.
NOT_TRANSCRIPT_TURNS = frozenset({
    "affirmation-prompter",
    "assistant-guardrail-prompter",
    "content-search-prompter",
    "content-suggestions-prompter",
    "conversation-starters-feed-prompter",
    "core-quote-renderer-prompter",
    "custom-practice-guardrail-prompter",
    "growing-renderer-prompter",
    "inner-tensions-renderer-prompter",
    "insight-card-prompter",
    "insights-compiler-prompter",
    "intent-prompter",
    "journal-insight-prompter",
    "memory-ingest-prompter",
    "memory-ingestion-sorting-prompter",
    "memory-prompter",
    "memory-sorting-prompter",
    "onboarding-prompter",
    "onboarding-results-prompter",
    "profile-ingest-prompter",
    "profile-prompter",
    "quick-processing-done-prompter",
    "score-prompter",
    "starters-prompter",
    "suggestions-prompter",
    "summary-prompter",
    "tending-renderer-prompter",
    "track-recommendation-prompter",
    "user-guardrail-prompter",
    "watching-gently-renderer-prompter",
    "you-best-renderer-prompter",
})


def _literal(path: Path, name: str):
    """The literal assigned to `name` at module level; `frozenset({...})` unwraps."""
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            value = node.value
            if isinstance(value, ast.Call) and getattr(value.func, "id", None) == "frozenset":
                value = value.args[0]
            return ast.literal_eval(value)
    raise AssertionError(f"{path.relative_to(REPO_ROOT)} assigns no {name}")


def _prompter_choices(path: Path) -> set[str]:
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if (isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "add_argument"
                and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == "--prompter"):
            for kw in node.keywords:
                if kw.arg == "choices":
                    return set(ast.literal_eval(kw.value))
    raise AssertionError(f"{path.relative_to(REPO_ROOT)} has no --prompter choices")


def reader_copies() -> list[Path]:
    copies = sorted(REPO_ROOT.glob("plugins/*/skills/*/scripts/responses_reader.py"))
    assert copies, "no responses_reader.py found"
    return copies


def stored_names(snapshot: dict) -> dict[str, str]:
    """Stored name -> the `config.prompt` key that stores it."""
    return {name: key for key, names in snapshot["prompters"].items() for name in names}


def unclassified(snapshot: dict, conversation: set[str], journal: set[str],
                 not_turns: set[str] = NOT_TRANSCRIPT_TURNS) -> list[str]:
    known = conversation | journal | not_turns
    return [f"{name} (IntelligenceService prompt.{key})"
            for name, key in sorted(stored_names(snapshot).items()) if name not in known]


def load_snapshot() -> dict:
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


class SnapshotTests(unittest.TestCase):
    def test_the_snapshot_is_generated_and_complete(self):
        snap = load_snapshot()
        self.assertRegex(snap["source"]["commit"], r"^[0-9a-f]{40}$")
        self.assertGreater(len(snap["prompters"]), 10)
        self.assertIn("coach-prompter", stored_names(snap))
        # A merged prompter's type stays allowed and resolves through prompterAliases.
        aliases = snap["prompterAliases"]
        missing = {t for t in snap["conversationTypes"] if aliases.get(t, t) not in snap["prompters"]}
        self.assertEqual(missing, set(), "a conversation type with no prompter in the snapshot")


class ClassificationTests(unittest.TestCase):
    def setUp(self):
        self.snap = load_snapshot()
        self.copies = reader_copies()

    def test_every_stored_name_is_classified_in_every_reader_copy(self):
        for path in self.copies:
            with self.subTest(copy=str(path.relative_to(REPO_ROOT))):
                missing = unclassified(self.snap, set(_literal(path, "CONVERSATION_PROMPTERS")),
                                       set(_literal(path, "JOURNAL_PROMPTERS")))
                self.assertEqual(missing, [], (
                    "IntelligenceService stores replies under a name AINative does not know. If its "
                    "output reaches the user as a turn, add it to CONVERSATION_PROMPTERS in every "
                    "responses_reader.py copy and to report-aurie-conversations CONVERSATION_TYPES "
                    "(or SECONDARY_TYPES); otherwise add it to NOT_TRANSCRIPT_TURNS in this test."))

    def test_no_name_is_classified_twice(self):
        conv = set(_literal(self.copies[0], "CONVERSATION_PROMPTERS"))
        journal = set(_literal(self.copies[0], "JOURNAL_PROMPTERS"))
        self.assertEqual(conv & NOT_TRANSCRIPT_TURNS, set())
        self.assertEqual(journal & NOT_TRANSCRIPT_TURNS, set())
        self.assertEqual(conv & journal, set())

    def test_every_classified_name_is_still_stored(self):
        stored = set(stored_names(self.snap))
        self.assertEqual(NOT_TRANSCRIPT_TURNS - stored, set(), "a declared name IntelligenceService no longer stores")
        for path in self.copies:
            with self.subTest(copy=str(path.relative_to(REPO_ROOT))):
                self.assertEqual(set(_literal(path, "CONVERSATION_PROMPTERS")) - stored, set(),
                                 "a conversation name IntelligenceService does not store (typo or retired)")

    def test_report_types_cover_every_conversation_name(self):
        conv = set(_literal(self.copies[0], "CONVERSATION_PROMPTERS")) & set(stored_names(self.snap))
        types = set(_literal(REPORT, "CONVERSATION_TYPES")) | set(_literal(REPORT, "SECONDARY_TYPES"))
        self.assertEqual(conv - types, set(),
                         "add it to report-aurie-conversations CONVERSATION_TYPES (or SECONDARY_TYPES)")

    def test_qa_prompter_choices_match_the_allowed_conversation_types(self):
        allowed = set(self.snap["conversationTypes"])
        for path in QA_CHOICES:
            with self.subTest(script=str(path.relative_to(REPO_ROOT))):
                self.assertEqual(_prompter_choices(path), allowed,
                                 "--prompter choices must equal IntelligenceService conversation.allowedTypes")



class SkillPointerTests(unittest.TestCase):
    """The skill that adds a prompter to IntelligenceService says to run this check."""

    def test_update_intelligence_prompt_names_the_refresh_script(self):
        skill = REPO_ROOT / "plugins/eng/skills/update-intelligence-prompt/references"
        for ref in ("experiment.md", "post-merge.md"):
            text = (skill / ref).read_text(encoding="utf-8")
            with self.subTest(ref=ref):
                self.assertTrue("refresh-is-prompter-names.py --check" in text
                                or "a-new-stored-name-has-to-reach-ainative-too" in text)
        self.assertIn(f"scripts/{REFRESH.name} --check", (skill / "experiment.md").read_text(encoding="utf-8"))
        self.assertTrue(REFRESH.exists())

class DetectorTests(unittest.TestCase):
    """The detector, fed the shapes of the two incidents."""

    SNAP = {"prompters": {"coach": ["coach-prompter"], "memory": ["memory-prompter"],
                          "guardrailsHandler": ["guardrails-handler-prompter"]}}

    def test_a_new_stored_name_is_reported_with_its_key(self):
        self.assertEqual(unclassified(self.SNAP, {"coach-prompter"}, set(), {"memory-prompter"}),
                         ["guardrails-handler-prompter (IntelligenceService prompt.guardrailsHandler)"])

    def test_a_legacy_name_is_checked_like_a_name(self):
        snap = {"prompters": {"quickProcessing": ["quick-processing-prompter", "quick-processing-sentinel-prompter"]}}
        self.assertEqual(unclassified(snap, {"quick-processing-prompter"}, set(), set()),
                         ["quick-processing-sentinel-prompter (IntelligenceService prompt.quickProcessing)"])

    def test_a_classified_snapshot_passes(self):
        self.assertEqual(unclassified(self.SNAP, {"coach-prompter", "guardrails-handler-prompter"}, set(),
                                      {"memory-prompter"}), [])


@unittest.skipUnless(shutil.which("node") and shutil.which("git"), "needs node and git")
class RefreshScriptTests(unittest.TestCase):
    """The refresh script, run against a small IntelligenceService-shaped repo."""

    CONFIG = """
const {name} = require('../package.json');
const extra = require('../config/extra.json');
module.exports = {
  conversation: {allowedTypes: ['coach', 'quickProcessing'], prompterAliases: {old: 'coach'}},
  prompt: {
    coach: {name: 'coach-prompter', experiments: {x: {a: {filename: 'coach.x.a.txt'}, c: {name: 'coach-arm-c'}}}},
    quickProcessing: {name: 'quick-processing-prompter', legacyNames: ['quick-processing-sentinel-prompter']},
    notAPrompter: 3,
  },
};
"""

    def setUp(self):
        spec = importlib.util.spec_from_file_location("refresh_is_prompter_names", REFRESH)
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)
        self.tmp = tempfile.TemporaryDirectory()
        repo = Path(self.tmp.name)
        (repo / "src").mkdir()
        (repo / "config").mkdir()
        (repo / "src/config.js").write_text(self.CONFIG)
        (repo / "package.json").write_text('{"name": "intelligence-service"}')
        (repo / "config/extra.json").write_text("{}")
        git = ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t"]
        subprocess.run([*git[:3], "init", "-q"], check=True)
        subprocess.run([*git, "add", "."], check=True)
        subprocess.run([*git, "commit", "-qm", "init"], check=True)
        self.repo = repo

    def tearDown(self):
        self.tmp.cleanup()

    def test_extract_reads_names_legacy_names_arm_names_and_types(self):
        snap = self.mod.extract(self.repo, "HEAD")
        self.assertEqual(snap["prompters"], {
            "coach": ["coach-arm-c", "coach-prompter"],
            "quickProcessing": ["quick-processing-prompter", "quick-processing-sentinel-prompter"],
        })
        self.assertEqual(snap["conversationTypes"], ["coach", "quickProcessing"])
        self.assertEqual(snap["prompterAliases"], {"old": "coach"})
        self.assertRegex(snap["source"]["commit"], r"^[0-9a-f]{40}$")

    def test_its_output_feeds_the_detector(self):
        snap = self.mod.extract(self.repo, "HEAD")
        self.assertEqual(unclassified(snap, {"coach-prompter", "quick-processing-prompter"}, set(), set()), [
            "coach-arm-c (IntelligenceService prompt.coach)",
            "quick-processing-sentinel-prompter (IntelligenceService prompt.quickProcessing)",
        ])

    def test_diff_names_added_and_removed_names(self):
        old = {"prompters": {"coach": ["coach-prompter"]}, "conversationTypes": ["coach"], "prompterAliases": {}}
        new = {"prompters": {"coach": ["coach-prompter"], "qp": ["qp-prompter"]},
               "conversationTypes": ["coach"], "prompterAliases": {}}
        self.assertEqual(self.mod.diff(old, new), ["+ qp: qp-prompter"])
        self.assertEqual(self.mod.diff(new, old), ["- qp: qp-prompter"])

    def test_a_config_with_no_prompters_is_refused(self):
        (self.repo / "src/config.js").write_text("module.exports = {prompt: {}};")
        git = ["git", "-C", str(self.repo), "-c", "user.email=t@t", "-c", "user.name=t"]
        subprocess.run([*git, "commit", "-qam", "empty"], check=True)
        with self.assertRaises(self.mod.RefreshError):
            self.mod.extract(self.repo, "HEAD")


if __name__ == "__main__":
    unittest.main()
