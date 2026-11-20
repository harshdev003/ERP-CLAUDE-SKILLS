#!/usr/bin/env python3
"""The `Team/Internal Accounts` allowlist reaches every rating filter intact (ENG-1436).

**The failure.** Every script that drops internal accounts took `--internal-ids` as a
CSV the model assembled by reading the page each run. On 2026-09-28 a sweep kept the
28-char Firebase UIDs and dropped both `google:` provider ids, so two founders' ratings
reached the analysis as customer feedback. Nothing errored: the ids that were passed
matched, and an allowlist row that is absent has no shape.

**What holds it now.** `internal_accounts.py` reads the page, and each consumer takes
`--internal-accounts-file`. The load-bearing test drives each consumer's `main()` — the
path production takes — with a real-shape k8bot message whose footer carries a provider
id, and asserts that employee's rating is excluded. A unit test of the matcher alone
would have passed throughout: `e["uid"] in internal_ids` was never the defect.

**Why no id resolution.** The card assumed rating footers always carry a Firebase UID,
so `google:` rows could never match. They do carry the provider id — over the 365 days
to 2026-09-28, 87 ratings in #ai-conversation-ratings have `user: google:…`, and they
are exactly the two ids on the page (74 + 13). Accounts created on a provider are keyed
by that id, so exact match is correct and resolving to a Firebase UID would miss them.

The page fixture mirrors the `notion-fetch` rendering (a `<table>` of `<td>` cells) and
the message fixture is a real CHAT/CONVERSATION RATED text with ids replaced. All ids
here are synthetic. Stdlib `unittest`, no network: Slack is stubbed at `fetch_messages`.
"""
from __future__ import annotations

import contextlib
import filecmp
import importlib.util
import io
import json
import pathlib
import sys
import tempfile
import unittest
import uuid
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parents[1]
SKILLS = REPO / "plugins/data/skills"
CANONICAL = SKILLS / "investigate-user-rating/scripts/internal_accounts.py"
FETCH_RATINGS = [
    SKILLS / "investigate-user-rating/scripts/fetch_ratings.py",
    SKILLS / "run-feedback-analysis-cadence/scripts/fetch_ratings.py",
    SKILLS / "report-conversation-patterns/scripts/fetch_ratings.py",
]
CHART_RATINGS = SKILLS / "chart-monthly-ratings/scripts/chart_ratings.py"
PMF = SKILLS / "investigate-pmf-survey/scripts/fetch_pmf_responses.py"

FOUNDER = "google:100000000000000000001"      # provider id only, no Firebase UID
COFOUNDER = "google:100000000000000000002"
EMPLOYEE_UID = "AbCdEfGhIjKlMnOpQrStUvWxYz12"
TEST_UID = "TestTestTestTestTestTestTe12"
PROSE_UID = "ProseProseProseProseProsePr"[:27] + "1"  # a worked example, not a row
CUSTOMER_UID = "CustCustCustCustCustCustCu12"

PAGE = f"""The k8bot messages carry a Firebase UID, an auth-provider ID (e.g. google:…).
### These four rules are not exhaustive
`{PROSE_UID}` (9 submissions) was caught — its footer held an `@aurahealth.io` address.
## Employee accounts
<table header-row="true">
<tr>
<td>Name</td>
<td>Firebase UID(s)</td>
<td>Auth-provider ID(s)</td>
<td>Personal email</td>
</tr>
<tr>
<td>Founder</td>
<td>—</td>
<td>{FOUNDER}</td>
<td><redacted-email></td>
</tr>
<tr>
<td>Cofounder</td>
<td>—</td>
<td>{COFOUNDER}</td>
<td>—</td>
</tr>
<tr>
<td>Engineer</td>
<td>{EMPLOYEE_UID} (prod)</td>
<td>—</td>
<td>[<redacted-email>](mailto:<redacted-email>)</td>
</tr>
<tr>
<td>Researcher</td>
<td>—</td>
<td>—</td>
<td>qa.for.test+\\*@gmail.com (test-account pattern)</td>
</tr>
<tr>
<td>Placeholder</td>
<td>—</td>
<td>—</td>
<td>(likely @aurahealth.io — unconfirmed)</td>
</tr>
</table>
## Test / non-employee internal accounts
- TestAcct — {TEST_UID} (QA test account).
---
# Sources
- Upstream — https://app.notion.com/p/aurahealth/Aura-Users-11575e23af2080899f44df1f9fe8d24a .
- Slack: an unrelated id google:199999999999999999999 cited in #wins.
"""


def rated(shape_header: str, uid: str, ts: str) -> dict:
    """A real CHAT/CONVERSATION RATED message, ids replaced."""
    return {
        "type": "message",
        "ts": ts,
        "text": (
            f"{shape_header}\n:thumbsdown:\n\n```\nThis isn’t helpful\n```\n\n"
            "`[dialogue: 00000000-0000-4000-8000-000000000000, conversation: "
            f"00000000-0000-4000-8000-000000000001, user: {uid}, "
            "prompt:coach-prompter:1.22.3]`"
        ),
    }


MESSAGES = [
    rated("*CHAT MESSAGE RATED*", FOUNDER, "1790000001.000100"),
    rated("*CONVERSATION RATED*", COFOUNDER, "1790000002.000100"),
    rated("*CHAT MESSAGE RATED*", EMPLOYEE_UID, "1790000003.000100"),
    rated("*CHAT MESSAGE RATED*", CUSTOMER_UID, "1790000004.000100"),
]


SIBLINGS = ("internal_accounts", "slack_read")


def load(path: pathlib.Path):
    """Import a script with ITS OWN sibling modules. Without evicting the cached
    `internal_accounts`, every copy would run the first one loaded, and a drifted
    copy would pass the behavioural tests below."""
    name = f"_ia_{uuid.uuid4().hex}"
    saved = list(sys.path)
    for sib in SIBLINGS:
        sys.modules.pop(sib, None)
    try:
        sys.path.insert(0, str(path.parent))
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path[:] = saved
        for sib in SIBLINGS:
            sys.modules.pop(sib, None)


def page_file(text: str = PAGE) -> str:
    fh = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8")
    fh.write(text)
    fh.close()
    return fh.name


def run_main(mod, argv: list[str], messages=MESSAGES) -> tuple[dict, str]:
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(mod, "fetch_messages", lambda *a, **k: list(messages)), \
            mock.patch.object(sys, "argv", ["x", "--token", "stub", *argv]), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        mod.main()
    return json.loads(out.getvalue()), err.getvalue()


def copies() -> list[pathlib.Path]:
    return sorted(SKILLS.rglob("internal_accounts.py"))


class Extraction(unittest.TestCase):
    def test_every_copy_is_byte_identical_to_the_canonical(self):
        self.assertGreaterEqual(len(copies()), 5)
        for path in copies():
            with self.subTest(copy=str(path.relative_to(REPO))):
                self.assertTrue(filecmp.cmp(CANONICAL, path, shallow=False))

    def test_every_identifier_shape_is_extracted(self):
        for path in copies():
            with self.subTest(copy=str(path.relative_to(REPO))):
                got = load(path).extract(PAGE)
                self.assertEqual(got["provider_ids"], sorted([FOUNDER, COFOUNDER]))
                self.assertEqual(got["firebase_uids"], sorted([EMPLOYEE_UID, TEST_UID]))
                self.assertEqual(got["emails"], ["<redacted-email>", "<redacted-email>"])
                self.assertEqual(got["email_patterns"], ["qa.for.test+*@gmail.com"])

    def test_prose_and_sources_are_not_the_allowlist(self):
        got = load(CANONICAL).extract(PAGE)
        flat = {i for ids in got.values() for i in ids}
        self.assertNotIn(PROSE_UID, flat)
        self.assertNotIn("google:199999999999999999999", flat)

    def test_pipe_table_rendering_extracts_the_same_ids(self):
        pipe = PAGE.split("## Employee accounts")[0] + (
            "## Employee accounts\n| Name | UID | Provider | Email |\n|---|---|---|---|\n"
            f"| Founder | — | {FOUNDER} | <redacted-email> |\n"
            f"| Cofounder | — | {COFOUNDER} | — |\n"
            f"| Engineer | {EMPLOYEE_UID} | — | <redacted-email> |\n# Sources\n"
        )
        got = load(CANONICAL).extract(pipe)
        self.assertEqual(got["provider_ids"], sorted([FOUNDER, COFOUNDER]))
        self.assertEqual(got["firebase_uids"], [EMPLOYEE_UID])

    def test_a_restructured_page_fails_loudly_not_empty(self):
        with self.assertRaises(ValueError):
            load(CANONICAL).extract("# Some other page\n" + EMPLOYEE_UID)

    def test_cli_prints_prefixes_only(self):
        mod = load(CANONICAL)
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["x", page_file()]), contextlib.redirect_stdout(out):
            mod.main()
        text = out.getvalue()
        for full in (FOUNDER, COFOUNDER, EMPLOYEE_UID, "<redacted-email>"):
            self.assertNotIn(full, text)
        self.assertIn("google:10000000", text)


class RatingFilterExcludesProviderIdEmployees(unittest.TestCase):
    """The load-bearing half: through `main()`, as the cadences run it."""

    def test_provider_id_row_excludes_that_employees_rating(self):
        pf = page_file()
        for path in FETCH_RATINGS:
            with self.subTest(copy=str(path.relative_to(REPO))):
                out, _ = run_main(load(path), [
                    "--polarity", "all", "--no-enrich", "--internal-accounts-file", pf,
                ])
                real = {e["uid"] for e in out["entries"]}
                dropped = {e["uid"] for e in out["test_entries"]}
                self.assertEqual(real, {CUSTOMER_UID})
                self.assertEqual(dropped, {FOUNDER, COFOUNDER, EMPLOYEE_UID})
                self.assertEqual(out["allowlist"]["provider_ids"], 2)

    def test_the_two_founders_stay_distinguishable_in_test_reasons(self):
        out, _ = run_main(load(FETCH_RATINGS[0]), [
            "--polarity", "all", "--no-enrich", "--internal-accounts-file", page_file(),
        ])
        # A bare uid[:8] made both of these the literal "allowlist:google:1".
        self.assertEqual(
            set(out["test_reasons"]),
            {"allowlist:google:10000000", f"allowlist:{EMPLOYEE_UID[:8]}"},
        )
        founders = [e["test_reason"] for e in out["test_entries"] if e["uid"].startswith("google:")]
        self.assertEqual(len(founders), 2)

    def test_the_2026_09_28_hand_list_is_visible_in_the_output(self):
        # UIDs only, as the tick assembled it: the founder's rating is analysed as a
        # customer's, and `allowlist.provider_ids == 0` is the trace it leaves.
        out, _ = run_main(load(FETCH_RATINGS[0]), [
            "--polarity", "all", "--no-enrich", "--internal-ids", f"{EMPLOYEE_UID},{TEST_UID}",
        ])
        self.assertIn(FOUNDER, {e["uid"] for e in out["entries"]})
        self.assertEqual(out["allowlist"]["provider_ids"], 0)

    def test_chart_ratings_excludes_provider_id_rows_too(self):
        out, _ = run_main(load(CHART_RATINGS), [
            "--month", "2026-09", "--internal-accounts-file", page_file(),
        ])
        self.assertEqual(out["counts"]["test_dropped"], 3)
        self.assertEqual(out["counts"]["real"], 1)
        self.assertEqual(out["allowlist"]["provider_ids"], 2)


class UnmatchableRowsWarn(unittest.TestCase):
    def test_email_and_bad_shape_rows_warn_on_stderr_and_in_json(self):
        for path in [*FETCH_RATINGS, CHART_RATINGS]:
            with self.subTest(script=str(path.relative_to(REPO))):
                extra = ["--month", "2026-09"] if path == CHART_RATINGS else ["--no-enrich"]
                out, err = run_main(load(path), [
                    *extra, "--internal-accounts-file", page_file(),
                    "--internal-ids", "not a uid!,<redacted-email>",
                ])
                self.assertEqual(out["allowlist"]["unmatchable_email_rows"], 4)
                self.assertEqual(out["allowlist"]["unmatchable_ids"], ["not a ui"])
                self.assertIn("1 --internal-ids email(s) can never match", err)
                self.assertIn("matches no rating-footer id shape", err)
                self.assertNotIn("<redacted-email>", err)
                # PII: the warning names a prefix/count, never an address.
                self.assertNotIn("<redacted-email>", err)

    def test_the_real_page_shape_is_silent_on_stderr(self):
        # The page's email rows are inert on this surface by design; a warning on
        # every tick would be one everyone learns to ignore. Counted, not shouted.
        out, err = run_main(load(FETCH_RATINGS[0]), [
            "--no-enrich", "--internal-accounts-file", page_file(),
        ])
        self.assertEqual(out["allowlist"]["unmatchable_ids"], [])
        self.assertEqual(out["allowlist"]["unmatchable_email_rows"], 3)
        self.assertNotIn("warning", err)

    def test_a_missing_page_file_stops_the_run(self):
        with self.assertRaises(SystemExit):
            run_main(load(FETCH_RATINGS[0]), ["--internal-accounts-file", "/nonexistent/page.md"])


class PmfReadsTheSamePage(unittest.TestCase):
    """PMF footers carry an address, so the page's email rows are live there."""

    def test_email_pattern_row_flags_a_test_alias(self):
        pmf = load(PMF)
        ia = load(CANONICAL)
        allow = ia.resolve("", page_file())
        e = {"uid": CUSTOMER_UID, "first_name": "<redacted-email>"}
        pmf.apply_footer_identity([e], allow["emails"], allow["email_patterns"])
        self.assertTrue(e["is_internal"])
        self.assertEqual(e["internal_reason"], "allowlist:*@gmail.com")

    def test_provider_id_row_flags_the_founder(self):
        pmf = load(PMF)
        allow = load(CANONICAL).resolve("", page_file())
        e = {"uid": FOUNDER, "first_name": "Founder", "is_internal": False, "internal_reason": ""}
        pmf.apply_allowlist([e], allow["ids"], set())
        self.assertTrue(e["is_internal"])
        self.assertTrue(e["internal_reason"].startswith("allowlist:google:10000000"))

    def test_an_ordinary_gmail_respondent_is_not_flagged(self):
        pmf = load(PMF)
        allow = load(CANONICAL).resolve("", page_file())
        e = {"uid": CUSTOMER_UID, "first_name": "<redacted-email>"}
        pmf.apply_footer_identity([e], allow["emails"], allow["email_patterns"])
        self.assertFalse(e["is_internal"])


if __name__ == "__main__":
    unittest.main()
