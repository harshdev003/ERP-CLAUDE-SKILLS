#!/usr/bin/env python3
"""The OpenAI changelog monitor posts only entries that could affect Aura.

**The rule.** An entry is posted when it mentions prompt caching or names, exactly, a model
IntelligenceService's live config uses (from `GET /ai/openai/models`). `gpt-5.2` must match `gpt-5.2` and not `gpt-5.2-codex`. A
`chat-latest` entry gets the alert header, because an unannounced `chat-latest` snapshot
update on 2026-10-07 halved coach's prompt-cache hit rate (PROD-7290).

**Why a test.** The parser reads another system's HTML, so the fixture is a verbatim excerpt
of the live page (captured 2026-10-20), not hand-written markup. A layout change that breaks
the date split would otherwise surface as a monitor that never posts.

Stdlib `unittest`, no network: `--html-file` replaces the fetch, `--models` replaces the API
call, and Slack is never called.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / 'plugins/eng/skills/monitor-openai-changelog/scripts/monitor_changelog.py'
FIXTURE = REPO_ROOT / 'tests/fixtures/openai_changelog/excerpt.html'
MODELS = ['chat-latest', 'gpt-5.2', 'gpt-5.4', 'gpt-5.4-mini']

spec = importlib.util.spec_from_file_location('monitor_changelog', SCRIPT)
mc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mc)


class ParseRealExcerpt(unittest.TestCase):
    def setUp(self):
        self.entries = mc.parse_entries(FIXTURE.read_text(encoding='utf-8'), MODELS)

    def find(self, needle):
        return next(e for e in self.entries if needle in e['text'])

    def test_every_block_becomes_one_dated_entry(self):
        self.assertEqual(len(self.entries), 5)
        self.assertTrue(all(mc.DATE.match(e['date']) for e in self.entries))

    def test_caching_entry_is_relevant(self):
        entry = self.find('12-hour prompt caching')
        self.assertTrue(entry['caching'])
        self.assertTrue(mc.is_relevant(entry))

    def test_chat_latest_entry_is_tagged(self):
        entry = self.find('chat-latest snapshot')
        self.assertEqual(entry['date'], 'Oct 7')
        self.assertIn('chat-latest', entry['models'])

    def test_exact_model_match_only(self):
        entry = self.find('40% faster')
        self.assertEqual(entry['models'], ['gpt-5.2'])
        self.assertNotIn('January, 2026', entry['text'])

    def test_unrelated_entries_are_dropped(self):
        for needle in ('Ultrafast mode', 'pre-warm'):
            self.assertFalse(mc.is_relevant(self.find(needle)), needle)

    def test_chat_latest_gets_the_alert_header(self):
        message = mc.format_message([self.find('chat-latest snapshot')])
        self.assertTrue(message.startswith(':rotating_light:'))
        plain = mc.format_message([self.find('12-hour prompt caching')])
        self.assertTrue(plain.startswith(':newspaper:'))


class DryRunTouchesNothing(unittest.TestCase):
    def test_too_few_entries_is_an_error_not_unchanged(self):
        # The excerpt has 5 entries, under MIN_ENTRIES: a layout change must not read as "no news".
        with tempfile.TemporaryDirectory() as tmp:
            state = pathlib.Path(tmp) / 'seen.json'
            run = subprocess.run([sys.executable, str(SCRIPT), '--html-file', str(FIXTURE), '--state', str(state),
                                  '--models', 'chat-latest', '--dry-run'], capture_output=True, text=True, check=False)
            self.assertEqual(run.returncode, 1)
            self.assertEqual(json.loads(run.stdout)['status'], 'error')
            self.assertFalse(state.exists())


class SlackFailureIsReported(unittest.TestCase):
    def test_system_exit_from_the_helper_becomes_a_failed_post(self):
        # slack_post exits on a missing token; the run must still print a JSON status.
        def exits(*_args, **_kwargs):
            raise SystemExit('SLACK_BOT_TOKEN is not set')
        saved, mc.post_message = mc.post_message, exits
        try:
            self.assertEqual(mc.post('hi'), {'ok': False, 'error': 'SLACK_BOT_TOKEN is not set'})
        finally:
            mc.post_message = saved


class NoModelListIsAnError(unittest.TestCase):
    def test_missing_token_raises_instead_of_falling_back(self):
        # A stale fallback list would silently drop news about newly adopted models.
        saved = mc.os.environ.pop('AURA_API_TOKEN', None)
        try:
            with self.assertRaises(ValueError):
                mc.our_models('https://example.invalid')
        finally:
            if saved is not None:
                mc.os.environ['AURA_API_TOKEN'] = saved


if __name__ == '__main__':
    unittest.main()
