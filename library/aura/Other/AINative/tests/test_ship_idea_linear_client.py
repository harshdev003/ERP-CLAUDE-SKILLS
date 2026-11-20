"""ship-idea reads the feature card with its own vendored Linear client.

`card_done` calls `get-issue` and `file_cleanup_card` calls
`create-issue`. A copy of the older client without `get-issue` made every card read
a silent no-op ("could not resolve ENGMOB-…"), so the subcommands it uses are
pinned here.
"""

from __future__ import annotations

import os
import re
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO, "plugins", "product", "skills", "ship-idea", "scripts")


class TheVendoredClientHasWhatShipIdeaCalls(unittest.TestCase):
    def test_every_subcommand_ship_idea_invokes_exists(self):
        client = open(os.path.join(SCRIPTS, "linear_api.py"), encoding="utf-8").read()
        offered = set(re.findall(r'add_parser\("([a-z-]+)"', client))
        caller = open(os.path.join(SCRIPTS, "ship_idea.py"), encoding="utf-8").read()
        used = set(re.findall(r'script, "([a-z-]+)"', caller))
        self.assertTrue({"get-issue", "create-issue"} <= used, used)
        self.assertEqual(used - offered, set())


if __name__ == "__main__":
    unittest.main()
