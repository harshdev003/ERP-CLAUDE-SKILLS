"""No skill edits or deletes a Slack message (ENG-1834).

The rule: existing messages in #security-alerts, #engineering-security,
#claude-monitoring, #engineering-mobile and incident threads must not be edited or deleted. A later
update is a new threaded reply. This fails on any script under plugins/ that names a Slack edit or delete
method, so a new one can't slip in.
"""
from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FORBIDDEN = re.compile(r"chat\.(update|delete|deleteScheduledMessage)|files\.delete|chat_update|chat_delete")
CODE = (".py", ".js", ".mjs", ".ts", ".sh")


class NoSlackEdits(unittest.TestCase):
    def test_no_script_edits_or_deletes_a_slack_message(self):
        hits = []
        for p in (ROOT / "plugins").rglob("*"):
            if p.suffix in CODE and p.is_file():
                for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if FORBIDDEN.search(line):
                        hits.append(f"{p.relative_to(ROOT)}:{n}: {line.strip()[:120]}")
        self.assertEqual(hits, [], "a script edits or deletes a Slack message:\n" + "\n".join(hits))

    def test_the_detector_catches_the_shape_it_replaced(self):
        self.assertTrue(FORBIDDEN.search('return sp._api("chat.update", token=t, body=body)'))
        self.assertTrue(FORBIDDEN.search("client.chat_delete(channel=c, ts=ts)"))
        self.assertFalse(FORBIDDEN.search('sp.post_message(channel, text, thread_ts=ts)'))


if __name__ == "__main__":
    unittest.main()
