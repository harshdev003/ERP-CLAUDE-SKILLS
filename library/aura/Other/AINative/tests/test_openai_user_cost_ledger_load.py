"""`eng:report-openai-user-cost` must not fan out over the IntelligenceService ledger.

Every profiled user used to page `/ai/llm-calls` at limit=1000 across every IS project for
30 days, unfiltered by user, to build the cost-share denominator. A cadence tick profiling a
few users made 1,200-3,600 such calls and scaled IntelligenceService to 8 pods (PROD-7898).

Now the denominator comes from OpenAI's Usage API, and the numerator is one userId-filtered
read per run, split by project locally. The ledger is BigQuery `llm.llm_call` (ENG-1657), read
through the bundled `llm_ledger.py`. These tests run the whole `main()` for one user over a
30-day window with every network call stubbed, and count the ledger reads.

Stdlib `unittest`; the ledger is stubbed at `llm_ledger.rows` and OpenAI at `_request`.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
from reader_harness import REPO_ROOT, load_reader  # noqa: E402

SCRIPT = REPO_ROOT / "plugins/eng/skills/report-openai-user-cost/scripts/report.py"
UID = "u" * 28


def _openai(projects):
    """Stub of `_request` for the OpenAI admin endpoints the script calls."""
    def fake(method, url, key, *, params=None, json_body=None, **kw):
        if url.endswith("/organization/projects"):
            return {"data": [{"id": f"p{i}", "name": f"{n} [Production]"} for i, n in enumerate(projects)],
                    "has_more": False}
        if "/organization/costs" in url:
            return {"data": [{"results": [{"amount": {"value": 10.0}}]}], "has_more": False}
        if "/organization/usage/completions" in url:
            return {"data": [{"results": [{"service_tier": "default", "input_tokens": 1_000_000,
                                           "output_tokens": 0}]}], "has_more": False}
        return {"data": [], "has_more": False}
    return fake


class LedgerLoad(unittest.TestCase):
    def setUp(self):
        self.mod = load_reader(SCRIPT, siblings=())
        self.mod._USER_ROWS.clear()
        self.ledger_calls = []
        projects = ["Conversation", "Memory", "Profile", "Summary", "Journal", "Feed", "Insights",
                    "Onboarding", "Intent", "Suggestions", "Notification", "Guardrail"]
        self.mod._request = _openai(projects)
        self.mod._resolve_completions_api_key = lambda name: None

    def run_report(self, rows):
        calls = self.ledger_calls

        class LedgerError(RuntimeError):
            pass

        fake = types.SimpleNamespace(LedgerError=LedgerError,
                                     rows=lambda params: calls.append(dict(params)) or list(rows))
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            out = f.name
        self.addCleanup(os.unlink, out)
        with mock.patch.dict(sys.modules, {"llm_ledger": fake}), \
                mock.patch.object(self.mod.requests, "get", side_effect=AssertionError("no HTTP ledger call")), \
                mock.patch.dict(os.environ, {"OPENAI_ADMIN_API_KEY": "sk-admin-x"}), \
                mock.patch.object(sys, "argv", ["report.py", UID, "--window", "last-30d", "--json", out]), \
                mock.patch("sys.stdout"), mock.patch("sys.stderr"):
            self.mod.main()
        with open(out, encoding="utf-8") as f:
            return json.load(f)

    def test_a_single_user_report_makes_one_ledger_call(self):
        rows = [{"provider": "openai", "userId": UID, "project": p, "openaiId": f"r{i}",
                 "inputTokens": 1000, "outputTokens": 0, "serviceTier": "default",
                 "createdAt": "2026-10-20T00:00:00Z"} for i, p in enumerate(["conversation", "memory"] * 50)]
        report = self.run_report(rows)
        self.assertEqual(len(self.ledger_calls), 1, "one userId-filtered read for the whole run")
        params = self.ledger_calls[0]
        self.assertEqual(params.get("userId"), UID)
        self.assertNotIn("project", params, "the user read is not repeated per project")
        by = {p["name"]: p["user_tokens"] for p in report["per_project"]}
        self.assertEqual((by["Conversation"], by["Memory"], by["Profile"]), (50_000, 50_000, 0))

    def test_no_ledger_call_is_ever_unfiltered_by_user(self):
        self.run_report([])
        self.assertTrue(self.ledger_calls)
        self.assertTrue(all(p.get("userId") == UID for p in self.ledger_calls))


if __name__ == "__main__":
    unittest.main()
