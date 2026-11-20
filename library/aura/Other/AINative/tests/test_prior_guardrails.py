#!/usr/bin/env python3
"""`prior_guardrails.py`: a user's earlier alerts across both guardrail channels.

ENG-1540. The same person can trip `#guardrails-memory-ingestion` and
`#guardrails-conversation`, and each alert gets its own CS draft — so CS could
email them twice in a day. The triage reply now carries a prior-guardrails line
and, when an active-risk draft went out in the last 24h, a once-a-day line.

The failure modes worth pinning, all silent:

* the alert being triaged counting itself, or a *later* alert shown as history;
* a prefix uid (`abc` vs `abc:x`) matching another account;
* an unreadable channel reported as "none" — it must read "lookup incomplete";
* a draft suppressed by the 24h rule — the script only adds a line.

Alert and reply shapes mirror the real channels: k8bot alert bodies, the triage
skills' verdict lines with emoji as Slack returns them (shortcodes), and the ✅
reviewers put on the alert once the email went out. Stdlib unittest, no network.
"""
from __future__ import annotations

import glob
import importlib.util
import os
import pathlib
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
CANONICAL = REPO / "plugins/eng/skills/investigate-conversation-guardrail/scripts/prior_guardrails.py"

spec = importlib.util.spec_from_file_location("prior_guardrails", CANONICAL)
pg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pg)

MEM, CONV = "C0B1D3JEHLP", "C0C7HEZ7CEA"
NOW = 1_791_420_000.0
BOT, REVIEWER = "U0ATMPESR37", "U019LLC8S5C"


def mem_alert(user, ts, **kw):
    return {"ts": ts, "text": f":warning: *Memory ingestion guardrail triggered*\n*User:* {user}\n*Transaction:* t-{ts}", **kw}


def conv_alert(user, ts, **kw):
    return {"ts": ts, "text": (
        "*Conversation guardrail triggered*\n"
        f"*User:* {user}\n*Transaction:* t-{ts}\n*Conversation:* c-{ts}\n"
        "_First guardrail trigger in this conversation. Later triggers are not posted, so review the whole conversation._"
    ), **kw}


def bot(text):
    return {"text": text, "bot_id": "B0ATUPK38P6", "user": BOT}


def ts_hours_ago(h):
    return f"{NOW - h * 3600:.6f}"


class FakeSlack:
    def __init__(self, history, threads, fail=()):
        self.history, self.threads, self.fail = history, threads, set(fail)

    def __call__(self, method, params, token):
        ch = params["channel"]
        if ch in self.fail:
            return {"ok": False, "error": "not_in_channel"}
        if method == "conversations.history":
            latest = params.get("latest")
            msgs = [m for m in self.history.get(ch, [])
                    if float(m["ts"]) >= float(params["oldest"])
                    and (latest is None or float(m["ts"]) < float(latest))]
            return {"ok": True, "messages": msgs}
        parent = next(m for m in self.history[ch] if m["ts"] == params["ts"])
        return {"ok": True, "messages": [parent] + self.threads.get(params["ts"], [])}


def run(fake, user="uidA", before=None, hours=24.0):
    priors, errors = pg.find_priors(user, "x", before_ts=before, now=NOW, get=fake)
    line, email, hit = pg.render(priors, errors, days=30.0, email_window_hours=hours)
    return priors, errors, line, email, hit


class PriorGuardrails(unittest.TestCase):
    def test_reads_both_channels_newest_first_with_outcomes(self):
        old, recent, current = ts_hours_ago(72), ts_hours_ago(5), ts_hours_ago(0.1)
        fake = FakeSlack(
            {MEM: [mem_alert("uidA", old, reply_count=1,
                             reactions=[{"name": "white_check_mark", "users": [REVIEWER]}])],
             CONV: [conv_alert("uidA", recent, reply_count=2), conv_alert("uidA", current)]},
            {old: [bot(":rotating_light: active risk — review draft below before CS sends.")],
             recent: [bot(":white_check_mark: no active risk — false positive, no action needed.")]},
        )
        priors, errors, line, email, hit = run(fake, before=current)
        self.assertEqual(errors, {})
        self.assertEqual([p["channel"] for p in priors], ["conversation", "memory-ingestion"])
        self.assertEqual(priors[0]["outcome"], pg.NO_RISK)
        self.assertEqual(priors[1]["outcome"], pg.ACTIVE)
        self.assertTrue(priors[1]["marked_handled"])
        self.assertIn("2 — conversation 5h ago — no active risk; memory-ingestion 3d ago — active risk draft, ✅ marked handled (email sent)", line)
        self.assertIsNone(email)  # the draft was 72h ago, outside the day
        self.assertFalse(hit)

    def test_the_alert_never_counts_itself_or_later_alerts(self):
        current, later = ts_hours_ago(2), ts_hours_ago(1)
        fake = FakeSlack({CONV: [conv_alert("uidA", current), conv_alert("uidA", later)]}, {})
        priors, _, line, _, _ = run(fake, before=current)
        self.assertEqual(priors, [])
        self.assertTrue(line.endswith(": none"))

    def test_other_users_and_prefix_uids_do_not_match(self):
        fake = FakeSlack({MEM: [mem_alert("uidA:google", ts_hours_ago(3)), mem_alert("uidB", ts_hours_ago(4))]}, {})
        self.assertEqual(run(fake)[0], [])

    def test_once_a_day_line_names_a_recent_active_draft_without_suppressing(self):
        recent = ts_hours_ago(6)
        fake = FakeSlack({MEM: [mem_alert("uidA", recent, reply_count=2)]},
                         {recent: [bot(":rotating_light: active risk — review draft below before CS sends."),
                                   bot("<!channel> :rotating_light: HIGH PRIORITY — wish to die · UID `uidA…`")]})
        priors, _, line, email, hit = run(fake)
        self.assertTrue(hit)
        self.assertTrue(priors[0]["high_priority"])
        self.assertEqual(priors[0]["outcome"], pg.ACTIVE)  # the page is not the verdict
        self.assertIn("6h ago in #guardrails-memory-ingestion (not yet marked sent, "
                      f"<https://auramobile.slack.com/archives/{MEM}/p{recent.replace('.', '')}|open thread>)", email)
        self.assertIn("never withhold help", email)
        self.assertIn("paged", line)

    def test_resources_in_place_and_untriaged_never_trigger_the_email_line(self):
        a, b = ts_hours_ago(2), ts_hours_ago(3)
        fake = FakeSlack({CONV: [conv_alert("uidA", a, reply_count=1), conv_alert("uidA", b)]},
                         {a: [bot(":ballot_box_with_check: active risk — resources already in place, no outreach drafted.")]})
        priors, _, _, email, hit = run(fake)
        self.assertEqual([p["outcome"] for p in priors], [pg.IN_PLACE, pg.UNANSWERED])
        self.assertIsNone(email)
        self.assertFalse(hit)

    def test_same_conversation_alert_is_a_duplicate_not_history(self):
        # 2026-10-19: IS posted two alerts two seconds apart for one conversation.
        # The first one's draft must not read as a prior incident or trip the
        # once-a-day line on the second.
        first, second = ts_hours_ago(0.5), ts_hours_ago(0.4)
        same = conv_alert("uidA", first, reply_count=1)
        same["text"] = same["text"].replace(f"c-{first}", "c-shared")
        fake = FakeSlack({CONV: [same]},
                         {first: [bot(":rotating_light: active risk — review draft below before CS sends.")]})
        priors, errors = pg.find_priors("uidA", "x", now=NOW, get=fake, conversation="c-shared")
        kept, dup = pg.split_duplicates(priors, second)
        self.assertEqual(kept, [])
        self.assertEqual(dup["ts"], first)
        line, email, hit = pg.render(kept, errors, days=30.0, email_window_hours=24.0)
        self.assertIn("none", line)
        self.assertIsNone(email)
        self.assertFalse(hit)

    def test_other_conversation_is_still_history(self):
        a, b = ts_hours_ago(2), ts_hours_ago(0.1)
        fake = FakeSlack({CONV: [conv_alert("uidA", a, reply_count=1)]},
                         {a: [bot(":rotating_light: active risk — review draft below before CS sends.")]})
        priors, _ = pg.find_priors("uidA", "x", now=NOW, get=fake, conversation="c-other")
        kept, dup = pg.split_duplicates(priors, b)
        self.assertEqual(len(kept), 1)
        self.assertIsNone(dup)

    def _two_alerts_one_conversation(self):
        first, second = ts_hours_ago(0.5), ts_hours_ago(0.49)
        alerts = []
        for ts in (first, second):
            a = conv_alert("uidA", ts)
            a["text"] = a["text"].replace(f"c-{ts}", "c-shared")
            alerts.append(a)
        return first, second, FakeSlack({CONV: alerts}, {})

    def test_sweep_order_newest_first_still_triages_only_the_oldest(self):
        # Bugbot on #1307: a newest-first sweep reaches the later alert while the
        # oldest is unanswered, then the oldest with the later one hidden. Both
        # used to draft and page. Neither is triaged yet in this fixture.
        first, second, fake = self._two_alerts_one_conversation()
        priors, _ = pg.find_priors("uidA", "x", now=NOW, get=fake, conversation="c-shared")
        _, dup_later = pg.split_duplicates(priors, second)
        _, dup_oldest = pg.split_duplicates(priors, first)
        self.assertEqual(dup_later["ts"], first)   # later alert -> pointer, even untriaged
        self.assertIsNone(dup_oldest)              # oldest -> full triage
        self.assertEqual(dup_later["outcome"], pg.UNANSWERED)

    def test_mode_b_without_an_alert_ts_is_never_a_duplicate(self):
        # Bugbot on #1307: Mode B passes --conversation but no --before-ts.
        first, _, fake = self._two_alerts_one_conversation()
        priors, _ = pg.find_priors("uidA", "x", now=NOW, get=fake, conversation="c-shared")
        _, dup = pg.split_duplicates(priors, None)
        self.assertIsNone(dup)

    def test_newer_alerts_never_enter_history(self):
        a, current, later = ts_hours_ago(3), ts_hours_ago(2), ts_hours_ago(1)
        fake = FakeSlack({CONV: [conv_alert("uidA", a), conv_alert("uidA", current),
                                 conv_alert("uidA", later)]}, {})
        priors, _ = pg.find_priors("uidA", "x", now=NOW, get=fake, conversation="c-none")
        kept, dup = pg.split_duplicates(priors, current)
        self.assertEqual([p["ts"] for p in kept], [a])
        self.assertIsNone(dup)

    def test_duplicate_pointer_reply_classifies_as_duplicate(self):
        self.assertEqual(pg.classify([bot(":leftwards_arrow_with_hook: duplicate alert — same conversation as …")])[0],
                         pg.DUPLICATE)

    def test_bot_reaction_is_not_a_reviewer_mark(self):
        a = ts_hours_ago(2)
        fake = FakeSlack({MEM: [mem_alert("uidA", a, reply_count=1,
                                          reactions=[{"name": "white_check_mark", "users": [BOT]}])]},
                         {a: [bot(":rotating_light: active risk — review draft below before CS sends.")]})
        self.assertFalse(run(fake)[0][0]["marked_handled"])

    def test_unreadable_channel_is_incomplete_never_none(self):
        fake = FakeSlack({MEM: []}, {}, fail={CONV})
        _, errors, line, _, _ = run(fake)
        self.assertIn("conversation", errors)
        self.assertIn("lookup incomplete (conversation unreadable)", line)

    def test_not_analysable_verdict(self):
        a = ts_hours_ago(2)
        fake = FakeSlack({CONV: [conv_alert("uidA", a, reply_count=1)]},
                         {a: [bot(":warning: *Conversation guardrail alert not analysable — UID `uidA…`*")]})
        self.assertEqual(run(fake)[0][0]["outcome"], pg.NOT_ANALYSABLE)


def last_email(fake, user="uidA", before=None, email_days=90.0):
    priors, errors = pg.find_priors(user, "x", before_ts=before, days=email_days, now=NOW, get=fake)
    return pg.render_last_email(priors, errors, email_days=email_days)


DRAFT = ":rotating_light: active risk — review draft below before CS sends."
SENT = [{"name": "white_check_mark", "users": [REVIEWER]}]


class LastCsEmail(unittest.TestCase):
    """ENG-1541: the last email CS sent stands out on its own line, with its date."""

    def test_names_the_newest_sent_draft_with_date_age_and_channel(self):
        older, newer = ts_hours_ago(24 * 50), ts_hours_ago(24 * 3)
        fake = FakeSlack({MEM: [mem_alert("uidA", older, reply_count=1, reactions=SENT)],
                          CONV: [conv_alert("uidA", newer, reply_count=1, reactions=SENT)]},
                         {older: [bot(DRAFT)], newer: [bot(DRAFT)]})
        line = last_email(fake)
        self.assertTrue(line.startswith("📧 *Last CS email:* draft posted "))
        self.assertIn(pg._when(newer), line)
        self.assertIn("(3d ago) in #guardrails-conversation — marked sent ✅ "
                      f"(<https://auramobile.slack.com/archives/{CONV}/p{newer.replace('.', '')}|open thread>)", line)
        self.assertIn("2 emails in 90d", line)

    def test_reaches_past_the_30_day_alert_list(self):
        old = ts_hours_ago(24 * 60)
        fake = FakeSlack({MEM: [mem_alert("uidA", old, reply_count=1, reactions=SENT)]}, {old: [bot(DRAFT)]})
        priors, errors = pg.find_priors("uidA", "x", days=90.0, now=NOW, get=fake)
        prior_line, _, _ = pg.render(priors, errors, days=30.0, email_window_hours=24.0)
        self.assertTrue(prior_line.endswith(": none"))  # outside the 30d list…
        self.assertIn("(60d ago)", pg.render_last_email(priors, errors, email_days=90.0))  # …but not the email line

    def test_unsent_or_non_draft_alerts_say_none_marked_sent(self):
        a, b = ts_hours_ago(5), ts_hours_ago(6)
        fake = FakeSlack({CONV: [conv_alert("uidA", a, reply_count=1),
                                 conv_alert("uidA", b, reply_count=1, reactions=SENT)]},
                         {a: [bot(DRAFT)],
                          b: [bot(":white_check_mark: no active risk — false positive, no action needed.")]})
        self.assertEqual(last_email(fake), "📧 *Last CS email:* none marked sent in 90d")

    def test_flags_a_newer_draft_not_yet_marked_sent(self):
        sent, pending = ts_hours_ago(48), ts_hours_ago(2)
        fake = FakeSlack({MEM: [mem_alert("uidA", sent, reply_count=1, reactions=SENT)],
                          CONV: [conv_alert("uidA", pending, reply_count=1)]},
                         {sent: [bot(DRAFT)], pending: [bot(DRAFT)]})
        self.assertIn("a newer draft (" + pg._when(pending) + ", #guardrails-conversation, "
                      f"<https://auramobile.slack.com/archives/{CONV}/p{pending.replace('.', '')}|open thread>) is not marked sent",
                      last_email(fake))

    def test_no_history_omits_the_line_but_an_unreadable_channel_never_does(self):
        self.assertIsNone(last_email(FakeSlack({MEM: [], CONV: []}, {})))
        self.assertIn("unknown — lookup incomplete", last_email(FakeSlack({MEM: []}, {}, fail={CONV})))


class ErrorsAreScopedToTheirWindow(unittest.TestCase):
    """BugBot on #1245: the lookup reads 90 days for the email line, so a thread
    that fails on an alert older than 30 days must not mark the 30-day list
    incomplete — while a channel failure still marks both."""

    class ThreadFails(FakeSlack):
        def __call__(self, method, params, token):
            if method == "conversations.replies" and params["ts"] in self.fail:
                return {"ok": False, "error": "ratelimited"}
            return FakeSlack.__call__(self, method, params, token)

    def test_old_thread_failure_only_touches_the_email_line(self):
        old, recent = ts_hours_ago(24 * 60), ts_hours_ago(24 * 2)
        fake = self.ThreadFails({MEM: [mem_alert("uidA", old, reply_count=1),
                                       mem_alert("uidA", recent, reply_count=1)]},
                                {recent: [bot(DRAFT)]}, fail={old})
        priors, errors = pg.find_priors("uidA", "x", days=90.0, now=NOW, get=fake)
        prior_line, _, _ = pg.render(priors, errors, days=30.0, email_window_hours=24.0)
        self.assertNotIn("lookup incomplete", prior_line)
        self.assertIn("lookup incomplete", pg.render_last_email(priors, errors, email_days=90.0))
        self.assertEqual(priors[-1]["outcome"], pg.UNREADABLE)  # never "not yet triaged"

    def test_recent_thread_failure_marks_the_30_day_list(self):
        recent = ts_hours_ago(24 * 2)
        fake = self.ThreadFails({MEM: [mem_alert("uidA", recent, reply_count=1)]}, {}, fail={recent})
        priors, errors = pg.find_priors("uidA", "x", days=90.0, now=NOW, get=fake)
        prior_line, _, _ = pg.render(priors, errors, days=30.0, email_window_hours=24.0)
        self.assertIn("lookup incomplete (some threads unreadable)", prior_line)


class CopiesAreIdentical(unittest.TestCase):
    def test_every_copy_matches_the_canonical(self):
        canonical = CANONICAL.read_bytes()
        copies = glob.glob(os.path.join(REPO, "plugins", "**", "prior_guardrails.py"), recursive=True)
        self.assertGreaterEqual(len(copies), 2)
        for c in copies:
            self.assertEqual(pathlib.Path(c).read_bytes(), canonical, c)


if __name__ == "__main__":
    unittest.main()
