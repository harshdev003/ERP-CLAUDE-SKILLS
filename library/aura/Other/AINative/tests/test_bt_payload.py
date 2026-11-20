"""`update-intelligence-prompt`'s prompt-sync payload keeps every field the release does not set.

A `POST /v1/prompt` replaces the whole record, so a field left out of the payload
is cleared on the target. A sync that sent no `description` wiped a guard note on
Coach [Development] that had to be restored by hand.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from reader_harness import REPO_ROOT

SKILL = REPO_ROOT / "plugins/eng/skills/update-intelligence-prompt"
SCRIPT = SKILL / "scripts/bt_payload.py"
GUARD = "Restore searchTracks tool instructions. Do not delete again."
TOOLS = [{"type": "function", "id": "searchTracks"}, {"type": "function", "id": "endSession"}]
OLD = {"type": "chat", "messages": [{"role": "system", "content": "old body"},
                                     {"role": "system", "content": "## Conversation\n{{input}}"}]}
NEW = {"type": "chat", "messages": [{"role": "system", "content": "new body"},
                                     {"role": "system", "content": "## Conversation\n{{input}}"}]}
TARGET = {"objects": [{
    "id": "uuid-1", "_xact_id": "100", "created": "2026-10-01", "org_id": "org",
    "project_id": "proj", "name": "Coach [Development]", "slug": "coach-development",
    "description": GUARD, "tags": ["guarded"],
    "prompt_data": {"prompt": OLD, "tool_functions": TOOLS,
                    "options": {"model": "gpt-5.1", "params": {"reasoning_effort": "low", "temperature": 1},
                                "position": "0|hzzzzz:"}},
}]}

spec = importlib.util.spec_from_file_location("bt_payload", SCRIPT)
bt_payload = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(bt_payload)


class BuildPayloadTests(unittest.TestCase):
    def test_a_release_that_sets_no_description_keeps_the_targets(self):
        out = bt_payload.build_payload(TARGET, NEW, "gpt-5.2", {"reasoning_effort": "medium"})
        self.assertEqual(out["description"], GUARD)
        self.assertEqual(out["tags"], ["guarded"])

    def test_an_explicit_description_replaces_it_and_an_empty_one_clears_it(self):
        self.assertEqual(bt_payload.build_payload(TARGET, description="new note")["description"], "new note")
        self.assertEqual(bt_payload.build_payload(TARGET, description="")["description"], "")

    def test_tools_position_and_identity_come_from_the_target(self):
        out = bt_payload.build_payload(TARGET, NEW, "gpt-5.2", {"reasoning_effort": "medium"})
        self.assertEqual(out["prompt_data"]["tool_functions"], TOOLS)
        self.assertEqual(out["prompt_data"]["options"],
                         {"model": "gpt-5.2", "params": {"reasoning_effort": "medium"}, "position": "0|hzzzzz:"})
        self.assertEqual((out["project_id"], out["name"], out["slug"]), ("proj", "Coach [Development]", "coach-development"))
        self.assertEqual(out["prompt_data"]["prompt"], NEW)
        self.assertEqual((out["function_type"], out["function_data"]), (None, {"type": "prompt"}))

    def test_server_fields_are_not_sent(self):
        out = bt_payload.build_payload(TARGET)
        for key in ("id", "_xact_id", "created", "org_id"):
            self.assertNotIn(key, out)

    def test_config_only_sync_sends_the_targets_own_prompt(self):
        out = bt_payload.build_payload(TARGET, model="gpt-5.2")
        self.assertEqual(out["prompt_data"]["prompt"], OLD)
        self.assertEqual(out["prompt_data"]["options"]["params"], {"reasoning_effort": "low", "temperature": 1})

    def test_a_target_without_a_description_sends_none(self):
        bare = json.loads(json.dumps(TARGET))
        bare["objects"][0]["description"] = None
        self.assertNotIn("description", bt_payload.build_payload(bare))

    def test_an_empty_or_unidentified_target_is_refused(self):
        with self.assertRaises(bt_payload.PayloadError):
            bt_payload.build_payload({"objects": []})
        broken = json.loads(json.dumps(TARGET))
        del broken["objects"][0]["slug"]
        with self.assertRaises(bt_payload.PayloadError):
            bt_payload.build_payload(broken)


class CliTests(unittest.TestCase):
    def _run(self, *extra):
        with tempfile.TemporaryDirectory() as d:
            t, p = Path(d, "target.json"), Path(d, "prompt.json")
            t.write_text(json.dumps(TARGET))
            p.write_text(json.dumps({"prompt_data": {"prompt": NEW}}))
            return subprocess.run([sys.executable, str(SCRIPT), "--target", str(t), "--prompt", str(p), *extra],
                                  capture_output=True, text=True)

    def test_the_cli_keeps_the_description(self):
        r = self._run("--model", "gpt-5.2", "--params", '{"reasoning_effort":"medium"}')
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["description"], GUARD)
        self.assertEqual(out["prompt_data"]["prompt"], NEW)

    def test_bad_params_exit_2(self):
        self.assertEqual(self._run("--params", "[1]").returncode, 2)


class DocTests(unittest.TestCase):
    def test_step_7c_builds_its_payload_with_the_script(self):
        doc = (SKILL / "references/post-merge.md").read_text(encoding="utf-8")
        step = doc[doc.index("### 7c — "):doc.index("### 7c-verify")]
        self.assertIn("scripts/bt_payload.py", step)
        self.assertNotIn("PAYLOAD=$(jq -n", step)
        self.assertIn("VERIFIED: description intact", step)


if __name__ == "__main__":
    unittest.main()
