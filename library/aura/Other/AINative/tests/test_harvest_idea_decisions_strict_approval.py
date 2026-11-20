"""In a loop channel, a hedge never approves — and a commitment does.

Two real replies, one day apart, are the whole specification:

    "i would like to test `d` ... and yes we can test sharing as an image"   -> not a decision
    "Let's go with a only since other two are too messy"                     -> a decision

They are a clause apart and mean different things. The first is somebody thinking out
loud in a thread; the second picks an arm and commits to it. The author of each said so
in as many words — *"I just want to make sure this is not considered an approval"* and
*"'Let's go with a' from Steve is like an approval"*.

So the discriminator is the **hedge**, not who is speaking and not whether a gate is
named: "I would like", "we could", "not sure", "suggest", "maybe" hold a reply back;
without one, a commitment decides and the row's status says which gate it answers. A
hedge cannot veto an approval that names its gate, because "I approve for
implementation, though I could be wrong about the placement" is still an approval.

The strictness is **one-directional**, which is the asymmetry the whole module is built
on. A generic *no* still rejects — nobody writes "not doing this" by accident — and the
cost of missing a rejection is an idea re-proposed, while the cost of inventing an
approval is work started that nobody asked for.
"""

from __future__ import annotations

import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(
    REPO, "plugins", "product", "skills", "harvest-idea-decisions", "scripts"))

import harvest_idea_decisions as h  # noqa: E402

LOOP = "C0C1JTK3YCC"      # ai-loop-aurie-features
ARCHIVE = "C08KQGJ0V1B"   # anything outside the four loop channels


STEVE = "U1BCYETM2"     # the loop channel's decider
JEROME = "UEW6170R3"


def thread(*reply_texts, user=STEVE):
    """Replies from the channel's decider — these tests are about the words."""
    return {"text": "💡 *IDEA-19 · …*", "reactions": [],
            "replies": [{"text": t, "user": user, "ts": f"179078680{i}.000100"}
                        for i, t in enumerate(reply_texts)]}


class AHedgeNeverApproves(unittest.TestCase):
    """Every one of these is a real reply from the loop channel, or one clause off it."""

    HEDGED = (
        "i would like to test `d` for the butuon and yes we can test sharing as an image",
        "We could test d - looks nicer to me",
        "We could test, not sure if any legal implications",
        "A could be interesting to me! Others seem too messy",
        "Suggest skipping as conversion is high on this screen",
        "maybe let's go with a",
        "what if we tried d?",
        "should we go with a?",
    )

    COMMITTED = (
        "Let's go with a only since other two are too messy",
        "let's go with d",
        "we'll go with c",
        "going with a",
        "let's test d",
        "ship it",
    )

    def test_a_hedge_does_not_decide(self):
        for text in self.HEDGED:
            out = h.classify(thread(text), channel=LOOP)
            self.assertNotEqual(out["verdict"], "approved", text)

    def test_a_commitment_decides(self):
        for text in self.COMMITTED:
            self.assertEqual(h.classify(thread(text), channel=LOOP)["verdict"], "approved", text)

    def test_the_answer_says_what_to_write_instead(self):
        out = h.classify(thread("maybe let's go with a"), channel=LOOP)
        self.assertEqual(out["verdict"], "needs_judgment")
        self.assertIn("I approve for implementation", out["evidence"])

    def test_a_named_gate_survives_a_hedge(self):
        out = h.classify(
            thread("I approve for implementation, though I could be wrong about the placement"),
            channel=LOOP)
        self.assertEqual(out["verdict"], "approved")

    def test_the_archive_is_untouched_by_the_hedge_rule(self):
        # Re-reading April's threads under today's protocol would rewrite decided
        # history, which is the reason the channel split exists at all.
        self.assertEqual(h.classify(thread("let's build it"), channel=ARCHIVE)["verdict"], "approved")

    def test_a_named_gate_approves_in_both(self):
        for channel in (LOOP, ARCHIVE):
            out = h.classify(thread("I approve for implementation"), channel=channel)
            self.assertEqual(out["verdict"], "approved", channel)

    def test_a_linear_link_alone_is_not_an_approval_in_a_loop_channel(self):
        link = "related: https://linear.app/aura-health/issue/ENG-1426"
        self.assertEqual(h.classify(thread(link), channel=LOOP)["verdict"], "needs_judgment")
        self.assertEqual(h.classify(thread(link), channel=ARCHIVE)["verdict"], "approved")

    def test_the_marker_never_reaches_a_row(self):
        self.assertNotIn(h.UNNAMED_APPROVAL, h.VERDICTS)
        for text in self.HEDGED:
            self.assertNotEqual(h.classify(thread(text), channel=LOOP)["verdict"],
                                h.UNNAMED_APPROVAL, text)


class StrictOnlyAboutYes(unittest.TestCase):
    """A no is still a no. Missing a rejection re-proposes an idea; inventing an
    approval starts work nobody asked for."""

    def test_a_generic_rejection_still_rejects(self):
        for text in ("not doing this", "won't do it", "no thanks", "drop this"):
            self.assertEqual(h.classify(thread(text), channel=LOOP)["verdict"], "rejected", text)

    def test_a_negated_approval_is_still_a_rejection(self):
        self.assertEqual(h._reply_signal("not approved for release", loop=True),
                         ("rejected", h.GATE_RELEASE))

    def test_a_reply_answering_the_other_gate_asks_a_human(self):
        # Existing behaviour, asserted here because the strict path must not change it.
        out = h.classify(thread("not approved for release"), channel=LOOP)
        self.assertEqual(out["verdict"], "needs_judgment")

    def test_a_deferral_still_defers(self):
        for text in ("let's do it later", "not now", "low priority"):
            self.assertEqual(h.classify(thread(text), channel=LOOP)["verdict"], "deferred", text)


class DiscussionNeverSupersedesADecision(unittest.TestCase):
    """Holding a generic yes apart is not enough — it must also not overwrite one.

    The scan keeps the LAST decisive reply, because a later reply supersedes an
    earlier one. A generic yes is not decisive, so it cannot take that slot: an
    approval followed three replies later by "let's test d" is one decision and then
    somebody talking about it. Letting the chatter win would bury an approval that
    had already landed, and the plan would quote only the chatter.
    """

    def test_chatter_after_an_approval_does_not_bury_it(self):
        out = h.classify(thread("I approve for implementation", "maybe let's go with a"),
                         channel=LOOP)
        self.assertEqual(out["verdict"], "approved")

    def test_an_approval_after_chatter_still_decides(self):
        out = h.classify(thread("maybe let's go with a", "I approve for implementation"),
                         channel=LOOP)
        self.assertEqual(out["verdict"], "approved")

    def test_a_real_verdict_of_any_kind_wins_over_chatter(self):
        for decision, verdict in (("no because we tested it", "rejected"),
                                  ("let's do it later", "deferred")):
            out = h.classify(thread("maybe let's go with a", decision), channel=LOOP)
            self.assertEqual(out["verdict"], verdict, decision)


class AnExplicitRefusalBeatsAnInference(unittest.TestCase):
    """A pasted Linear link is evidence that somebody made a card, which normally
    follows an approval. A refusal that cites its own ticket is not that."""

    def test_no_because_with_a_ticket_is_a_rejection(self):
        out = h.classify(thread(
            "no because we already did this — https://linear.app/aura-health/issue/ENG-1"),
            channel=LOOP)
        self.assertEqual(out["verdict"], "rejected")

    def test_later_with_a_ticket_is_a_deferral(self):
        self.assertEqual(h._reply_signal(
            "later", loop=True), ("deferred", None))

    def test_every_refusal_beats_the_link_not_just_the_protocol_wording(self):
        """The link sits below *all* the negatives, not only the two the channel
        asks for. A refusal that cites the ticket it duplicates is the natural way
        to write one, and reading the link first inverted it — which, with a prior
        approval in the thread, left the row `approved` after somebody had said no."""
        link = "https://linear.app/aura-health/issue/ENG-1"
        for text, verdict in ((f"not doing this — {link}", "rejected"),
                              (f"no thanks, see {link}", "rejected"),
                              (f"let's do it later, tracked in {link}", "deferred")):
            self.assertEqual(h.classify(thread(text), channel=LOOP)["verdict"], verdict, text)

    def test_a_refusal_citing_a_ticket_overturns_an_earlier_approval(self):
        link = "https://linear.app/aura-health/issue/ENG-1"
        out = h.classify(thread("I approve for implementation", f"not doing this — {link}"),
                         channel=LOOP)
        self.assertEqual(out["verdict"], "rejected")

    def test_a_bare_link_still_asks_a_human_in_a_loop_channel(self):
        out = h.classify(thread("https://linear.app/aura-health/issue/ENG-1"), channel=LOOP)
        self.assertEqual(out["verdict"], "needs_judgment")


class OnlyTheDeciderDecides(unittest.TestCase):
    """One person decides a loop channel, and only they do.

    Steve asked for it (*"move forward w me as the final decider"*) and Jerome
    narrowed it to exactly that (*"only continue with steve"*), naming the trade in
    the same breath: it puts every idea on one person's critical path. That is the
    intent, not an oversight.

    Everyone else's reply is input — read, quoted, and surfaced as what the row waits
    on. Reporting "X said this, it needs Steve" is both true and actionable; writing
    X's verdict would be neither.
    """

    @staticmethod
    def mixed(*pairs):
        return {"text": "💡 *IDEA-1 · …*", "reactions": [],
                "replies": [{"text": txt, "user": user, "ts": f"179078680{i}.000100"}
                            for i, (user, txt) in enumerate(pairs)]}

    def test_the_decider_decides(self):
        out = self.mixed((STEVE, "let's go with a"))
        self.assertEqual(h.classify(out, channel=LOOP)["verdict"], "approved")

    def test_anyone_else_is_input_not_a_verdict(self):
        out = h.classify(self.mixed((JEROME, "no because it duplicates journal share")),
                         channel=LOOP)
        self.assertEqual(out["verdict"], "needs_judgment")
        self.assertIn("waits on", out["evidence"])
        self.assertIn(STEVE, out["evidence"])

    def test_the_decider_wins_whenever_they_reply(self):
        for pairs in (((JEROME, "no because it duplicates"), (STEVE, "let's go with a")),
                      ((STEVE, "let's go with a"), (JEROME, "no because it duplicates"))):
            self.assertEqual(h.classify(self.mixed(*pairs), channel=LOOP)["verdict"],
                             "approved", pairs)

    def test_a_channel_with_no_decider_lets_anyone_decide(self):
        # The archive, and any loop channel not named in DECIDER.
        self.assertIsNone(h.decider_for(ARCHIVE))
        self.assertEqual(h.classify(self.mixed((JEROME, "not doing this")),
                                    channel=ARCHIVE)["verdict"], "rejected")


class TheDecidersPlainWordCounts(unittest.TestCase):
    """Steve, whose channel it is: *"'sounds great' from a decision maker is a move
    forward."*

    He is right, and the decider rule is what makes it safe. The hedge guard exists to
    stop a bystander's musing becoming a verdict — and the person whose job is to
    decide is not a bystander. So their plain sentiment decides, **both ways**: hearing
    only the yes would be a rule that can approve and never refuse.

    Genuine uncertainty still does not decide, even from them. A decider asking a
    question is asking, not answering.
    """

    @staticmethod
    def said(text, user=STEVE):
        return {"text": "💡 *IDEA-31 · …*", "reactions": [],
                "replies": [{"text": text, "user": user, "ts": "1790786800.000100"}]}

    def test_plain_approval_from_the_decider(self):
        for text in ("D sounds great", "looks good", "works for me", "love it"):
            self.assertEqual(h.classify(self.said(text), channel=LOOP)["verdict"],
                             "approved", text)

    def test_plain_refusal_from_the_decider(self):
        for text in ("Suggest skipping as conversion is high on this screen",
                     "let's skip this one", "not keen"):
            self.assertEqual(h.classify(self.said(text), channel=LOOP)["verdict"],
                             "rejected", text)

    def test_uncertainty_from_the_decider_still_does_not_decide(self):
        for text in ("A could be interesting to me! Others seem too messy",
                     "not sure, should we test this?"):
            self.assertNotIn(h.classify(self.said(text), channel=LOOP)["verdict"],
                             ("approved", "rejected"), text)

    def test_the_same_words_from_anyone_else_are_input(self):
        # Jerome wrote almost exactly this on IDEA-31 and it must not decide.
        out = h.classify(self.said("looks nicer to me, sounds great", user=JEROME), channel=LOOP)
        self.assertNotIn(out["verdict"], ("approved", "rejected"))


class TheProtocolsOwnWordingWorks(unittest.TestCase):
    """The channel asks for “no because …” and “later”. Neither classified before —
    somebody following the posted instructions to the letter landed in
    `needs_judgment`, so the protocol was describing a vocabulary the reader did not
    have."""

    def test_no_because_rejects(self):
        for text in ("no because it duplicates journal share", "no, because we tested it", "No"):
            self.assertEqual(h.classify(thread(text), channel=LOOP)["verdict"], "rejected", text)

    def test_later_defers(self):
        for text in ("later", "Later."):
            self.assertEqual(h.classify(thread(text), channel=LOOP)["verdict"], "deferred", text)

    def test_the_words_inside_a_sentence_decide_nothing(self):
        # The narrow anchoring that keeps the two patterns above from firing widely.
        for text in ("no idea why this screen exists", "we can look at the later slides"):
            self.assertNotIn(h.classify(thread(text), channel=LOOP)["verdict"],
                             ("rejected", "deferred"), text)


if __name__ == "__main__":
    unittest.main()


class ACommitmentIsAWholeWord(unittest.TestCase):
    """"Let's go with" is a prefix of "let's go without". Every alternative in
    `COMMIT_RE` closes on a word boundary, so a refusal cannot read as a choice."""

    def test_without_is_not_with(self):
        for text in ("let's go without the badge", "we'll go without it for now",
                     "let's go within budget on this one"):
            out = h.classify(thread(text), channel=LOOP)
            self.assertNotEqual(out["verdict"], "approved", text)

    def test_with_still_commits(self):
        self.assertEqual(h.classify(thread("let's go with a"), channel=LOOP)["verdict"], "approved")


class AQuestionNeverDecidesSoftly(unittest.TestCase):
    """The decider's sentiment and a commitment phrase are soft signals, and a
    question is neither. "Looks right?" asks; it does not approve. Only the clause
    that asks is set aside — a reply that decides and then asks has still decided."""

    def test_a_question_from_the_decider_is_a_question(self):
        for text in ("looks good?", "does this look right", "which one are we going with?",
                     "are we going with a or d?", "should we go with d?"):
            out = h.classify(thread(text), channel=LOOP)
            self.assertEqual(out["verdict"], "needs_answer", text)

    def test_a_decision_followed_by_a_question_still_decides(self):
        out = h.classify(thread("let's go with a. Can we ship this week?"), channel=LOOP)
        self.assertEqual(out["verdict"], "approved")
        # The April corpus shape: an approval, a dash, then the question.
        self.assertEqual(h._reply_signal(
            "Agreed. Would love to create a card & run ab test - is this something LLM can do?"),
            ("approved", None))

    def test_would_love_to_is_a_statement(self):
        self.assertFalse(h.is_question("Would love to create a card"))
        self.assertTrue(h.is_question("Would this create a card"))


class EveryCommitmentIsScored(unittest.TestCase):
    """"Not going with a, let's go with b" chooses b. Scoring only the first match
    read the negated one and rejected the whole reply."""

    def test_a_later_commitment_wins_over_an_earlier_negated_one(self):
        out = h.classify(thread("not going with a, let's go with b"), channel=LOOP)
        self.assertEqual(out["verdict"], "approved")

    def test_all_negated_is_still_a_no(self):
        out = h.classify(thread("not going with a, and we're not going with b either"),
                         channel=LOOP)
        self.assertEqual(out["verdict"], "rejected")


class ACommitmentToADesignIsARedesignAsk(unittest.TestCase):
    """"Let's go with a different placement" commits to a design, which is the
    ask itself — not a verdict on the idea."""

    def test_committing_to_another_design_asks_for_one(self):
        for text in ("let's go with a different placement", "let's use a new design for this"):
            out = h.classify(thread(text), channel=LOOP)
            self.assertEqual(out["verdict"], "needs_redesign", text)

    def test_an_explicit_approval_with_a_design_note_still_approves(self):
        out = h.classify(thread("I approve for implementation — but move the badge lower"),
                         channel=LOOP)
        self.assertEqual(out["verdict"], "approved")


class TheDecidersDoubtIsANo(unittest.TestCase):
    """Steve on IDEA-13: "I don't think this experiment would produce results
    without incentives above". Jerome: take that as a rejection. It is one."""

    def test_the_idea_13_wording_rejects(self):
        text = ("I don't think this experiment would produce results without incentives "
                "above - like free conversations with aurie.")
        self.assertEqual(h.classify(thread(text), channel=LOOP)["verdict"], "rejected")

    def test_only_from_the_decider(self):
        text = "I don't think this experiment would produce results"
        self.assertEqual(h.classify(thread(text, user=JEROME), channel=LOOP)["verdict"],
                         "needs_judgment")


class AChoiceOutranksTheDesignQuestionBesideIt(unittest.TestCase):
    def test_a_commitment_then_a_design_question_is_the_commitment(self):
        out = h.classify(thread("let's go with a. Should the badge sit lower, a different placement?"),
                         channel=LOOP)
        self.assertEqual(out["verdict"], "approved")

    def test_a_design_question_alone_is_a_redesign_ask(self):
        out = h.classify(thread("could we try a different placement?"), channel=LOOP)
        self.assertEqual(out["verdict"], "needs_redesign")


class AChoiceOutranksTheDecidersDoubt(unittest.TestCase):
    def test_doubt_about_one_option_then_a_choice_is_the_choice(self):
        out = h.classify(thread("I don't think a would work, let's go with d"), channel=LOOP)
        self.assertEqual(out["verdict"], "approved")

    def test_doubt_alone_is_still_a_no(self):
        out = h.classify(thread("I don't think this would work"), channel=LOOP)
        self.assertEqual(out["verdict"], "rejected")


class ACommitmentToAPictureIsNotAChoiceOfArm(unittest.TestCase):
    """"Let's go with a smaller badge" lands on a design noun. It chooses a picture,
    so it carries no verdict on the idea — whether or not the reply also matches the
    redesign vocabulary."""

    def test_a_design_commitment_never_approves(self):
        for text in ("let's go with a smaller badge",
                     "should we redesign this? let's go with a lower placement",
                     "let's use a different layout here"):
            self.assertNotEqual(h.classify(thread(text), channel=LOOP)["verdict"], "approved", text)

    def test_an_arm_commitment_beside_a_design_noun_in_another_clause_still_decides(self):
        out = h.classify(thread("let's go with a. the badge can move later."), channel=LOOP)
        self.assertEqual(out["verdict"], "approved")


class TheDesignTestReadsOneClause(unittest.TestCase):
    """The filter asks what the phrase lands on *in its own clause*. Two clauses
    later is a different sentence, and the boundaries have to survive the question
    stripping to say so."""

    def test_a_design_noun_two_clauses_later_does_not_disarm_the_choice(self):
        out = h.classify(thread("let's go with a - the layout is fine as it is"), channel=LOOP)
        self.assertEqual(out["verdict"], "approved")

    def test_the_approval_vocabulary_is_filtered_too(self):
        # "go ahead" is in APPROVE_RE as well as COMMIT_RE; filtering one and not the
        # other let the identical reply approve through the other pattern.
        out = h.classify(thread("let's go ahead with a different placement"), channel=LOOP)
        self.assertNotEqual(out["verdict"], "approved")

    def test_a_plain_go_ahead_still_approves_in_the_archive(self):
        self.assertEqual(h.classify(thread("go ahead"), channel=ARCHIVE)["verdict"], "approved")


class ADesignNoteBesideAVerdictIsNotADesignVerdict(unittest.TestCase):
    """"Approved, though the design isn't right yet" approves and remarks; "let's go
    with a different placement" chooses a picture. The conjunction is the difference."""

    def test_a_contrastive_note_leaves_the_verdict_alone(self):
        for text in ("Approved, though the design isn't right yet",
                     "approved but the layout could be better"):
            self.assertEqual(h.classify(thread(text), channel=ARCHIVE)["verdict"], "approved", text)

    def test_a_design_noun_far_from_the_phrase_is_a_second_thought(self):
        out = h.classify(thread("let's go with a and we can revisit everything else about the badge later"),
                         channel=LOOP)
        self.assertNotEqual(out["verdict"], "needs_redesign")


class OnlyAPhrasesOwnObjectDisarmsIt(unittest.TestCase):
    """Several approval phrases take no object — "ship it", "approved", "let's do it" —
    so a design noun after one of them is a remark, not what it chose. A comma or a
    conjunction in between is what says so."""

    def test_an_objectless_approval_survives_a_design_remark(self):
        for text in ("ship it, the layout is wrong",
                     "approved and the design is fine as it is",
                     "let's do it — the placement can change later"):
            self.assertEqual(h.classify(thread(text), channel=ARCHIVE)["verdict"], "approved", text)

    def test_a_real_object_still_disarms(self):
        out = h.classify(thread("let's go ahead with a smaller badge"), channel=ARCHIVE)
        self.assertNotEqual(out["verdict"], "approved")


class ANegatedApprovalIsNeverFiltered(unittest.TestCase):
    """Dropping a negated match before `_verdict_for` reads it hides the refusal, and
    the reply then looks like a request for another picture."""

    def test_a_negated_approval_beside_a_design_noun_still_rejects(self):
        for text in ("not approved, the layout is wrong",
                     "not approved — a different placement won't help"):
            self.assertEqual(h.classify(thread(text), channel=ARCHIVE)["verdict"], "rejected", text)


class ACoordinatorInsideTheObjectIsStillTheObject(unittest.TestCase):
    """"A clean and simple badge" is one object. Reading its "and" as the start of a
    new thought left the phrase scoring as a verdict on the idea."""

    def test_a_coordinated_object_still_disarms(self):
        for text in ("let's go with a clean and simple badge",
                     "let's go ahead with a new or different placement"):
            self.assertNotEqual(h.classify(thread(text), channel=ARCHIVE)["verdict"], "approved", text)

    def test_an_intransitive_approval_is_untouched_by_a_coordinator(self):
        # The same "and" after a phrase with no object slot — the verdict stands.
        self.assertEqual(h.classify(thread("approved and the design is fine as it is"),
                                    channel=ARCHIVE)["verdict"], "approved")


class ACoordinatedRemarkLandsOnAHuman(unittest.TestCase):
    """The accepted limitation, asserted so it stays a decision rather than a surprise.

    A coordinator does not end the object, so "let's go with a and the layout is fine"
    is filtered and comes back `needs_judgment`. Telling it apart from "a clean and
    simple badge" needs a finite-verb test, and a finite verb is an open word class —
    three enumerations of it each left another phrasing on the wrong side. The failure
    is in the safe direction: a person decides, nothing is invented.
    """

    def test_a_coordinated_remark_asks_a_human_rather_than_approving(self):
        for text in ("let's go with a and we can revisit the badge",
                     "let's go with a and the layout is fine"):
            self.assertNotEqual(h.classify(thread(text), channel=ARCHIVE)["verdict"],
                                "approved", text)

    def test_a_coordinated_object_still_disarms(self):
        for text in ("let's go with a clean and simple badge",
                     "let's go ahead with a new or different placement",
                     "let's go with a clean and simple badge that is fine"):
            self.assertNotEqual(h.classify(thread(text), channel=ARCHIVE)["verdict"],
                                "approved", text)

    def test_an_intransitive_approval_is_untouched_by_any_of_it(self):
        # No object slot, so none of this applies — the verdict stands.
        for text in ("approved and the design is fine as it is",
                     "ship it, the layout is wrong"):
            self.assertEqual(h.classify(thread(text), channel=ARCHIVE)["verdict"],
                             "approved", text)


class ASubordinatorEndsTheObject(unittest.TestCase):
    """A reason or time clause after the choice is not what the choice landed on.
    The list covers subordinators that cannot also sit inside a noun phrase —
    `before`, `after` and `as` are left out on purpose, since "a badge before the CTA"
    and "a badge as small as possible" are each one object."""

    def test_a_subordinate_clause_leaves_the_choice_alone(self):
        for text in ("let's go with a since the placement is better",
                     "let's go with a unless the layout breaks",
                     "let's go with a until the badge is ready",
                     "let's go with a even though the layout is odd"):
            self.assertEqual(h.classify(thread(text), channel=ARCHIVE)["verdict"], "approved", text)

    def test_a_preposition_inside_the_object_still_disarms(self):
        self.assertNotEqual(h.classify(thread("let's go with a badge before the CTA"),
                                       channel=ARCHIVE)["verdict"], "approved")
