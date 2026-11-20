"""Harvest human decisions out of Slack idea threads into the Loop Notion database.

`share-ideas` posts a pending row to the loop channel; the team reacts or replies;
this reads that back and writes `Status`, `Rejection reason`, `Quick status note`,
`Decided by` and `Decided at` onto the row.

Split, matching `manage-ideas`: this script is the **mechanical** layer — fetch,
classify what is unambiguous, and write. Anything it cannot read confidently comes
back as `needs_judgment` for the Claude layer in SKILL.md to settle. It never
guesses a verdict to avoid saying "I don't know".

Subcommands
-----------
    plan     Fetch + classify + print the proposed transitions. Writes nothing.
    apply    The same, then write the confident ones to Notion.
    classify Run the classifier over a fixture file and print the verdicts.

Why a thumbs-up is not an approval
----------------------------------
The rules below are asymmetric on purpose: :white_check_mark: approves, :x: and
:-1: reject, and :+1: is recorded as sentiment but never decides anything. That
is not fastidiousness, it is what the archive shows. Of the 22 ideas shared to
`#ai-ideas` in April 2026, nine drew a :+1: and **five** drew a :+1: and nothing
else — and two of those five were later answered the other way by an experiment:
`aiTestimonials` lost (paid cancellation +55%) and the save-to-collection half of
`2990424c412f` was settled by `bookmarkChat` ending on control. A classifier that
read :+1: as approval would have queued both for build.

The cost is asymmetric too. A wrong rejection is recoverable — it lands in the
database with a reason attached and the next ideation pass sees it. A wrong
approval spends engineering weeks. So approval demands the stronger signal.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from typing import Any, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from notion_rest import (  # noqa: E402
    MCPFallback,
    date_start,
    make_date,
    make_rich_text,
    make_select,
    notion,
    rich_text_value,
    select_value,
    title_text,
)
import slack_read  # noqa: E402
from thread_marks import LOOP_BOT_USER, MARKS, mark_thread  # noqa: E402


AURIE_FEATURES_DS = "8d14f3eb-0c55-4f25-9322-ab2b2a69f092"

# The loop's own channel. Rows created before it existed carry an `#ai-ideas`
# thread URL instead, and are harvested from there — the row's own `Slack thread`
# is followed, so both channels work without a second code path.
LOOP_CHANNEL = "C0C1JTK3YCC"

# The April archive. Its threads mean what they meant when they were written, so
# they are read under the old protocol — a check mark there was an approval.
ARCHIVE_CHANNEL = "C0ATUL8T936"

# Only these are candidates. A row that already carries a verdict for the gate it
# is at is never revisited, which is what makes an hourly tick on a quiet channel
# a no-op.
#
# `implementing` is here because the SAME thread carries a second decision weeks
# later: a person tries the build in TestFlight and says whether it ships.
HARVESTABLE = ("pending", "shared", "implementing")

# Rows past their gate whose thread is still open: nothing here decides them, but a
# person can still write in the thread — a question about the plan, a change of
# mind, a fact — and it must not go unread. Read for follow-up only.
FOLLOW_UP_ONLY = ("approved", "shipped_dormant", "experiment_running")

# The loop's "read, nothing to do" mark on a reply it has handled without answering.
SEEN_MARK = "eyes"

# --- the two gates ----------------------------------------------------------
#
# One thread, two decisions, weeks apart. Which one a reply answers is decided by
# where the ROW is, not by when the reply arrived — a thread can carry both, and
# ordering alone cannot tell "yes, build it" from "yes, ship it".
GATE_IMPLEMENTATION = "implementation"
GATE_RELEASE = "release"

GATE_FOR_STATUS = {
    "pending": GATE_IMPLEMENTATION,
    "shared": GATE_IMPLEMENTATION,
    "implementing": GATE_RELEASE,
}


def gate_for_status(status: Optional[str]) -> Optional[str]:
    return GATE_FOR_STATUS.get(status or "")


# --- reaction vocabulary, scoped by channel ---------------------------------
#
# The two channels use the SAME emoji to mean opposite things, and both readings
# are correct in their own place.
#
# In the April archive a check mark meant "approved" — the threads are full of it,
# and re-reading them under today's protocol would rewrite decided history.
#
# In the loop channels the marks are the loop's own: a build mark once Gate 1 said
# yes, a cross once the idea is closed (rejected, or abandoned later), a check mark
# when it is done (released and analysed). `thread_marks` puts them there as the row
# moves. Decisions are replies, because a reaction cannot say which gate it answers. So a check mark on a `shared` row
# in a loop channel is not an approval — it is somebody using the wrong signal,
# and saying so is more useful than acting on it.
ARCHIVE_APPROVE_REACTIONS = {"white_check_mark", "heavy_check_mark", "ballot_box_with_check"}
ARCHIVE_REJECT_REACTIONS = {"x", "negative_squared_cross_mark", "-1", "thumbsdown"}

LOOP_TERMINAL_REACTIONS = {
    "white_check_mark": "done",
    "heavy_check_mark": "done",
    "ballot_box_with_check": "done",
    "x": "closed",
    "negative_squared_cross_mark": "closed",
    "rocket": "approved for implementation",
}

SENTIMENT_REACTIONS = {"+1", "thumbsup", "heart", "eyes", "raised_hands", "fire"}

# The four loop channels. Everything else — the archive included — reads under the
# old protocol, which is the safe default: it is what the historical threads mean.
LOOP_CHANNELS = {
    "C0C1JTK3YCC",  # ai-loop-aurie-features
    "C0C0ZSMNZM5",  # ai-loop-aura-features
    "C0C29JPEUQG",  # ai-loop-aurie-conversations
    "C0C0ZSMU7C7",  # ai-loop-aura-recommendations
}


def uses_loop_protocol(channel: Optional[str]) -> bool:
    return (channel or "") in LOOP_CHANNELS


# Who decides in a loop channel — and only they do. Steve asked for it in the channel
# ("move forward w me as the final decider", 2026-09-30) and Jerome narrowed it to
# exactly that: "only continue with steve".
#
# Without this the verdict belongs to whoever replied last, which makes it a property
# of typing order rather than of authority. With it, everyone else's reply is input:
# read, quoted and surfaced, but never written as the decision. A thread where someone
# else answered and the decider has not comes back `needs_judgment` naming both — an
# idea waiting on one person is a true and useful thing to report, where a verdict
# from the wrong person is neither.
DECIDER = {
    "C0C1JTK3YCC": "U1BCYETM2",   # ai-loop-aurie-features -> Steve
}


def decider_for(channel: Optional[str]) -> Optional[str]:
    """The one person whose reply decides in this channel, or None where anyone may."""
    return DECIDER.get(channel or "")

# --- reply vocabulary -------------------------------------------------------
# Deliberately small and high-precision. Everything unmatched falls through to
# `needs_judgment`, which is a better answer than a confident wrong one. Every
# pattern below is anchored in a phrase that actually appears in the archive.
LINEAR_ISSUE_RE = re.compile(r"linear\.app/[^/\s]+/issue/([A-Z]+-\d+)", re.I)

# Checked FIRST: "let's do it later" is a deferral, not an approval, and the
# approval half of that sentence would otherwise win.
DEFER_RE = re.compile(
    r"\b(low priority"
    r"|in the future"
    r"|for later\b"
    r"|not right now"
    r"|not now\b"
    r"|once we\b"
    r"|after we\b"
    r"|depends on\b"
    r"|park(?:ed|ing)? (?:this|it)\b"
    # "let's do it later" — the verb list keeps this from swallowing an approval
    # that merely mentions a later follow-up ("ship it, we can polish later").
    r"|(?:do|build|ship|try|run|revisit) (?:it|this|that)?\s*later\b"
    r"|(?:would like to|want to|need to|should|let'?s) [^.!?\n]{0,60}?\bfirst\b)",
    re.I,
)

REJECT_RE = re.compile(
    r"\b(leave (?:it |this )?as is"
    r"|leave as-is"
    r"|not doing (?:this|it)\b"
    r"|won'?t do (?:this|it)\b"
    r"|no thanks\b"
    r"|skip (?:this|it)\b"
    r"|drop (?:this|it)\b"
    r"|rejected\b)",
    re.I,
)

APPROVE_RE = re.compile(
    r"\b(let'?s (?:build|do it|ship|run|try|test)"
    r"|would love to (?:create|build|run|try|test)"
    r"|create a card\b"
    r"|wrote up requirements"
    r"|ship it\b"
    r"|go ahead\b"
    r"|approved\b)",
    re.I,
)

# An approval that NAMES its gate. These are what the channel topic asks for, and
# they are unambiguous where the generic ones above are not: "ship it" means
# "build it" on a shared row and "release it" on one in TestFlight, and only the
# row's status can tell those apart.
#
# Naming the gate also makes a mismatch detectable — a release approval on a row
# nobody has built yet is somebody looking at the wrong thread, or a row that is
# behind. Either way it is worth stopping on rather than resolving silently.
# What the channel topic tells people to write for the other two answers. Anchored to
# the start of the reply and kept narrow, so "no idea why this screen exists" and
# "we can look at the later slides" are not verdicts.
PROTOCOL_REJECT_RE = re.compile(r"^no\b(?:\s*[,\-–—:]?\s*because\b|\s*[.!]?$)", re.I)
PROTOCOL_DEFER_RE = re.compile(r"^later\b\s*[.!]?$", re.I)

# Choosing an arm and committing to it. "Let's go with a" is a decision; the patterns
# below it are the April archive's vocabulary and say nothing about picking one of
# several. Steve's approval of IDEA-1 matched none of them, which is how a clear yes
# sat in a thread doing nothing.
# Every alternative closes on a word boundary: "let's go with" is a prefix of "let's
# go without", and the boundary is what keeps a refusal from reading as a choice.
COMMIT_RE = re.compile(
    r"\b(let'?s go with\b"
    r"|we'?(?:ll|re) go(?:ing)? with\b"
    r"|going with\b"
    r"|let'?s use\b"
    r"|let'?s go ahead\b)", re.I)

# A hedge, and the reason this whole distinction exists. "I would like to test d" and
# "let's test d" differ by one clause and mean different things: the first is somebody
# thinking out loud in a thread, the second is a choice. Every approval below is vetoed
# by a hedge — except one that names its gate, because "I approve for implementation,
# though I could be wrong about the placement" is still an approval.
#
# Checked against every real reply in the loop channel on 2026-09-30: it holds back
# four preferences from two people and lets one commitment through, which is exactly
# what their authors meant.
HEDGE_RE = re.compile(
    r"\b(i'?d like|i would like|i'?d prefer|i would prefer"
    r"|we (?:could|can|might|may)\b"
    r"|could be\b|might be\b|may be\b"
    r"|not sure\b|unsure\b"
    r"|i (?:suggest|wonder)\b|suggest(?:ing|ion)?\b"
    r"|perhaps\b|maybe\b"
    r"|what if\b"
    r"|should we\b)", re.I)

# Plain approval and plain refusal — from the DECIDER only. Steve, whose channel it is:
# "'sounds great' from a decision maker is a move forward." That is right, and it is what
# the decider rule makes safe: the hedge guard below exists to stop a bystander's musing
# becoming a verdict, and the person whose job is to decide is not a bystander. Read both
# ways, or it would hear only yes — "suggest skipping" from the same person is a no.
DECIDER_APPROVE_RE = re.compile(
    r"\b(sounds? (?:great|good|right)"
    r"|looks? (?:great|good|right)"
    r"|love it\b|nice one\b|perfect\b"
    r"|agreed\b|works for me\b)", re.I)
DECIDER_REJECT_RE = re.compile(
    r"\b(suggest skipping|let'?s skip|skipping this|skip this one"
    r"|not keen\b|rather not\b"
    # "I don't think this experiment would produce results" — the decider's doubt, in
    # the words he used on IDEA-13, and read as the no it is.
    r"|(?:don'?t|do not) think (?:this|it|that)(?: experiment| idea| one)? "
    r"(?:would|will|can|could) (?:produce|work|move|lift|convert)\b)", re.I)

# A bare "Skip" / "Pass" is the decider's shortest refusal. Whole-clause only: `said` is
# the reply's statements, each ending in a period, so a question ("Skip?") never reaches
# it and "skip the badge, ship the copy" is not a verdict on the idea.
DECIDER_BARE_REFUSAL_RE = re.compile(
    r"(?:^|(?<=[.!]) )(?:skip|pass)(?: (?:it|this|this one|on (?:this|it)))?(?=[.!])", re.I)

APPROVE_IMPLEMENTATION_RE = re.compile(
    r"\bapprove[sd]?\s+(?:this\s+|it\s+)?for\s+(?:implementation|build|building)\b", re.I)
APPROVE_RELEASE_RE = re.compile(
    r"\bapprove[sd]?\s+(?:this\s+|it\s+)?for\s+releas(?:e|ing)\b", re.I)

# Every approval pattern above matches INSIDE its own negation: "Not approved for
# release" contains "approved for release", and the generic `approved\b` too. That
# is the same trap `ship-idea` hit with substring markers, and it fails the same
# way — a refusal read as a yes, which here would print the release command.
#
# So an approval is only an approval when nothing negates the verb just before it.
# Matched against the text PRECEDING the approval, anchored to its end, which is
# why each contraction is spelled out: a bare `n.t$` would also fire on "want".
NEGATOR_RE = re.compile(
    r"(?:\bnot|\bnever|\bcannot|\bno longer|"
    r"\b(?:do|does|did|wo|ca|is|are|was|were|has|have|had|would|could|should|ai)n[o']?t)"
    r"\s+$",
    re.I,
)

# "I like the idea but not the design" is neither a decision nor a question — it is
# a request for another picture. Read as a rejection it would retire an idea the
# reviewer just endorsed; read as nothing it leaves the thread quiet. Two shapes:
# an idea-yes / design-no sentence, or a plain ask for a redesign or another placement.
_DESIGN_NOUN = r"(?:design|mock-?ups?|layout|placement|look|visuals?|badge|styling)"
_CHANGE_WORD = (r"(?:not|but|though|hate|dislike|change|rework|redo|fix|tweak|adjust|iterate"
                r"|different|another|less|more|smaller|bigger|lower|higher|move)")
REDESIGN_RE = re.compile(
    # idea-yes / design-no: an endorsement of the idea, then — in the rest of that
    # sentence, in either order — a design noun and a change word.
    r"(?:\b(?:like|love|keep|good|great|approve of|yes to)\b[^.!?\n]{0,40}\bidea\b"
    r"(?=[^.!?\n]{0,80}\b" + _DESIGN_NOUN + r"\b)"
    r"(?=[^.!?\n]{0,80}\b" + _CHANGE_WORD + r"\b)"
    # a plain ask for another design
    r"|\b(?:re-?design"
    r"|(?:new|different|another) (?:design|mock-?up|placement|look|variant|layout)"
    r"|(?:design|mock-?up|layout|placement) (?:needs|could use|wants) (?:work|another pass|a rework|a redo)"
    r"|not (?:sold|convinced|keen) on the (?:design|placement|layout)"
    r"|(?:design|placement|layout) (?:isn'?t|is not|doesn'?t) (?:work|working|right|it|great|nice|good))\b)",
    re.I,
)


# A commitment to a DESIGN is not a commitment to an arm. "Let's go with a different
# placement" and "let's use a smaller badge" choose a picture — which is the redesign
# ask itself — while "let's go with a" chooses an arm. The discriminator is what the
# phrase lands on, so only the text after it, to the end of its clause, is read.
_DESIGN_NOUN_RE = re.compile(r"\b" + _DESIGN_NOUN + r"\b", re.I)


# Only a phrase that TAKES an object can land on one. "Let's go ahead with …",
# "let's go with …", "let's build …" have an object slot; "ship it", "approved",
# "let's do it" do not, and a design noun after one of those is a remark however it
# is punctuated. Matched on the phrase's last word, which is what the slot hangs off.
_TRANSITIVE_TAIL = ("with", "use", "build", "run", "try", "test", "create", "ahead")

# Inside that slot, what ends the object and starts a new thought. A comma, a semicolon
# or a subordinating conjunction does. The list is subordinators that cannot also sit
# INSIDE a noun phrase — `before`, `after` and `as` are deliberately absent, because
# "a badge before the CTA" and "a badge as small as possible" are one object each.
_NOT_AN_OBJECT_RE = re.compile(
    r"[,;:]|\b(?:but|though|although|however|while|because|since|unless|until|whereas|"
    r"whenever|so that|except|if only|even though|provided that)\b",
    re.I)

# How far past the phrase its object can sit. "go ahead with a different placement" is
# 26 characters; a design noun further away than this is a second thought, not the thing
# being chosen.
_OBJECT_WINDOW = 45


def _lands_on_a_design(text: str, match: "re.Match[str]") -> bool:
    """True when the verdict phrase at `match` takes a design noun as its object.

    Applies to the approval vocabulary as well as the commitments: `"let's go ahead"`
    is in both, so filtering only the commitments left the identical reply approving
    through `APPROVE_RE` instead.

    The object has to read as one: within the clause, within `_OBJECT_WINDOW`, and with
    nothing between the phrase and the noun that starts a new thought. So "ship it, the
    layout is wrong" ships — the comma makes the rest a remark — while "let's go ahead
    with a smaller badge" chooses a picture.

    A **negated** phrase is never filtered: "not approved, the design is wrong" is a
    refusal, and dropping the match here would hide it from `_verdict_for` and leave the
    reply looking like a design request.

    **Its one accepted limitation**, and the reason there is no cleverer test here: a
    coordinator is not read as ending the object, so a choice followed by a coordinated
    remark — "let's go with a and the layout is fine" — is filtered and comes back
    `needs_judgment` rather than `approved`. Telling that apart from "a clean and simple
    badge" needs a finite-verb test, and a finite verb is an open word class: every
    enumeration of it left another phrasing on the wrong side. This way the failure is
    always in the safe direction — a decision a person then makes, never one invented
    here — which is the asymmetry the whole module is built on.
    """
    if NEGATOR_RE.search(text[:match.start()]):
        return False
    if not match.group(0).rstrip().lower().endswith(_TRANSITIVE_TAIL):
        return False
    tail = _CLAUSE_SPLIT_RE.split(text[match.end():])[0][:_OBJECT_WINDOW]
    noun = _DESIGN_NOUN_RE.search(tail)
    return bool(noun) and not _NOT_AN_OBJECT_RE.search(tail[:noun.start()])


def _asks_for_redesign(text: str) -> bool:
    """A redesign request that is NOT also an explicit verdict.

    "Approve for implementation — but move the badge lower" and "no because … the
    design is wrong" both carry a decision with a design note, and the decision wins:
    any reply `_reply_signal` reads as approved or rejected is never a redesign ask.
    A deferral signal alone does not override it — "let's fix the design first" is
    the canonical way to ask for a redesign, and its "first" is what DEFER_RE keys
    on, so a deferral is the one verdict a redesign phrasing is allowed to shadow.
    """
    if not REDESIGN_RE.search(text):
        return False
    # Archive semantics deliberately (`loop` left off): this asks "is there a decision
    # here at all", and a generic yes still answers that. Reading it strictly would let
    # a redesign phrasing shadow "approve for implementation — but move the badge".
    # A commitment to a design carries no verdict for the same reason, so it never
    # shadows the ask it is part of.
    sig = _reply_signal(text)
    return not (sig and sig[0] in ("approved", "rejected"))


# A ship leg posts one of these per round, and it is the ONLY thing that opens the
# release gate. Before something exists to judge there is nothing to answer; after
# an `iterate` round the new post is what separates this round's verdict from the
# previous round's. Matching the sentence rather than the poster keeps a human who
# announces it by hand working too.
#
# Two anchors, because the two loop families ship differently. A Features idea is
# judged from a TestFlight build; a recommendations change is server-side, so
# there is no build to try — the equivalent moment is the backend release, where
# the code is live and the experiment exists but assigns nobody. Recognising both
# in one pattern keeps the gate logic identical across loops: which sentence
# opened the gate is the only thing that differs.
RELEASE_ANCHORS = (
    r"in TestFlight for review",          # product:ship-idea — mobile
    r"released and awaiting the release gate",  # product:ship-recommendation — backend
)
RELEASE_ANCHOR_RE = re.compile(r"\b(?:" + "|".join(RELEASE_ANCHORS) + r")\b", re.I)

# Which gate is further along. A reply naming a gate the row has already passed
# is stale — that gate's answer is WHY the row moved on — so it is skipped rather
# than re-read. A reply naming a gate the row has not reached is the opposite: it
# needs a person.
GATE_ORDER = {GATE_IMPLEMENTATION: 1, GATE_RELEASE: 2}

# A harvested verdict carries the quote, not a diagnosis. `other` is honest for a
# rejection read off an emoji or a one-liner; a human or a later skill sharpens it.
HARVEST_REJECT_REASON = "other"
HARVEST_DEFER_REASON = "timing"

VERDICTS = ("approved", "rejected", "deferred", "needs_answer", "needs_follow_up",
            "needs_redesign",
            "needs_judgment", "no_decision")

# Internal only — `classify` turns it into `needs_judgment`, and it must never reach a
# row. It marks a reply in a LOOP channel that reads like a yes without naming a gate.
UNNAMED_APPROVAL = "unnamed_approval"


# A question is neither a decision nor sentiment. It sits in the gap between `shared`
# and a verdict — the space the loop is best placed to fill from the priors — and must
# never be read as a soft rejection: "Have we tested this?" is a question, not a no.
# It is detected AFTER the decision scan, so a reply that both decides and asks
# something still counts as the decision. A leading interrogative catches a question
# typed without a "?"; the "?" catches the rest.
_INTERROGATIVE_RE = re.compile(
    r"^\s*(?:what|why|how|when|where|who|which|whose|is|are|isn'?t|aren'?t|was|were|"
    r"wasn'?t|weren'?t|do|does|did|don'?t|doesn'?t|didn'?t|have|has|had|haven'?t|"
    r"hasn'?t|hadn'?t|can|could|couldn'?t|should|shouldn'?t|would|wouldn'?t|will|"
    r"won'?t|shall|any(?:one|thing|body))\b", re.I)

# A "?" inside a URL query string (or a `<…>` Slack link/mention) is not a question.
# Stripped before the "?" test so "see https://x/y?z=1" and "<https://…?a|label>" do
# not read as needs_answer — a real question keeps its "?" outside the link.
_URL_OR_MENTION_RE = re.compile(r"<[^>]*>|https?://\S+", re.I)


# "Would love to create a card" opens on a modal and is not asking anything — it is
# "I would love to" with the I dropped, which is how people write in Slack. The
# leading-interrogative test alone read Steve's archive approval as a question.
_DROPPED_SUBJECT_RE = re.compile(
    r"^\s*(?:would|could|should|can|will|might)\s+"
    r"(?:love|like|prefer|be (?:great|good|nice|happy|keen))\b", re.I)


# Clause boundaries for `_statements`: sentence enders, line breaks, and a spaced dash,
# which is how a Slack reply joins a decision to the question after it ("Would love to
# create a card - is this something an LLM can do?").
_CLAUSE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+|\s+[-\u2013\u2014]\s+")


def _statements(text: str) -> str:
    """The reply with its questions taken out, clause by clause.

    A soft signal — the decider's sentiment, a commitment phrase, an archive
    approval word — only counts in a clause that is not asking. "Looks right?" and
    "which one are we going with?" leave nothing behind; "Agreed. Would love to run
    an A/B test - is this something an LLM can do?" keeps its first two clauses,
    because a reply that decides and then asks has still decided.
    """
    stripped = _URL_OR_MENTION_RE.sub(" ", (text or "").strip())
    kept = []
    for clause in _CLAUSE_SPLIT_RE.split(stripped):
        c = clause.strip()
        if not c or c.endswith("?"):
            continue
        if _INTERROGATIVE_RE.match(c) and not _DROPPED_SUBJECT_RE.match(c):
            continue
        # Each kept clause ends in a terminator, so `_CLAUSE_SPLIT_RE` still finds the
        # boundaries afterwards. Joining on a bare space erased them, and a design noun
        # two clauses later then read as following the commitment.
        kept.append(c if c.endswith((".", "!", "?")) else c + ".")
    return " ".join(kept)


def is_question(text: str) -> bool:
    """True if the reply reads as a question — a `?`, or a leading interrogative.

    URLs and `<…>` Slack links/mentions are stripped first, so a query-string "?"
    never counts. A dropped-subject opener ("Would love to …") is a statement.
    """
    t = (text or "").strip()
    if not t:
        return False
    stripped = _URL_OR_MENTION_RE.sub(" ", t)
    if "?" in stripped:
        return True
    head = stripped.lstrip()
    return bool(_INTERROGATIVE_RE.match(head)) and not _DROPPED_SUBJECT_RE.match(head)


# ---------- classification ---------------------------------------------------

def _verdict_for(text: str, match: "re.Match[str]") -> str:
    """`approved`, or `rejected` when a negator sits immediately before the verb."""
    return "rejected" if NEGATOR_RE.search(text[:match.start()]) else "approved"


def _reply_signal(text: str, loop: bool = False,
                  decides: bool = False) -> Optional[tuple[str, Optional[str]]]:
    """`(verdict, gate)` carried by one reply, or None.

    `loop` says the reply is in one of the four loop channels, where **an approval
    must name its gate**. There, a generic yes returns `UNNAMED_APPROVAL` and
    `classify` asks a human instead of deciding.

    `gate` is the gate the reply NAMES — `implementation`, `release`, or None when
    the wording does not say and the row's status has to decide.

    Order matters, and the first three are all before the generic approval:

    - a Linear URL is somebody having created the card, which only happens after
      an implementation approval;
    - a deferral sentence frequently contains an approval ("let's do it later"),
      so it is tested first;
    - an explicit gate beats a generic phrase in the same sentence, because
      "approve for release" contains no generic marker but "I approve for
      release, ship it" contains two.

    A negated approval is a REJECTION, not a miss. "Not approved for release" —
    the exact phrase `ship-idea`'s unwind path uses — matches every approval
    pattern here, so falling through to the generic one would read a refusal as a
    yes and print the release command for it.
    """
    for regex, named in ((APPROVE_IMPLEMENTATION_RE, GATE_IMPLEMENTATION),
                         (APPROVE_RELEASE_RE, GATE_RELEASE)):
        match = regex.search(text)
        if match:
            return _verdict_for(text, match), named
    # The two phrasings the channel actually asks for. They were not in the patterns
    # below, which are anchored in the April archive — so a person following the
    # posted protocol to the letter landed in `needs_judgment`. Both are narrow on
    # purpose: "no" only when "because" follows it or it is the whole reply, "later"
    # only as the whole reply, so neither can fire on a sentence merely containing
    # the word.
    #
    # Ahead of the Linear check — as is every other negative below. An explicit
    # refusal beats an inference: "no because we already did this —
    # linear.app/…/ENG-1" is a rejection citing its own evidence, and reading the
    # link first turned it into an approval.
    if PROTOCOL_DEFER_RE.match(text.strip()):
        return "deferred", None
    if PROTOCOL_REJECT_RE.match(text.strip()):
        return "rejected", None
    if DEFER_RE.search(text):
        return "deferred", None
    if REJECT_RE.search(text):
        return "rejected", None
    if LINEAR_ISSUE_RE.search(text):
        # Last of the positives, and below every negative: somebody made a card,
        # which normally only follows an approval — but "not doing this —
        # linear.app/…/ENG-1" is a refusal citing the ticket it duplicates, and a
        # link read first inverts it. In a loop channel it can as easily be a link
        # to related work, and a link is not a sentence saying yes.
        return (UNNAMED_APPROVAL if loop else "approved"), GATE_IMPLEMENTATION
    # Everything below is a SOFT signal — sentiment or a phrase — and a question is
    # neither. "Looks right?" and "which one are we going with?" are asking, and
    # `classify` only reaches its question check for a reply nothing here claimed;
    # so the questions come out here, clause by clause, before any soft form runs.
    said = _statements(text)
    if not said:
        return None
    # Every commitment in the reply is scored, not the first: "not going with a,
    # let's go with b" is a choice of b, and the negated one must not decide alone.
    # One that lands on a design noun is not a choice of arm at all, and is dropped.
    commits = [m for m in COMMIT_RE.finditer(said) if not _lands_on_a_design(said, m)]
    chose = any(_verdict_for(said, m) == "approved" for m in commits)
    # Only the decider's plain sentiment counts, and it is checked after the explicit
    # forms so "no because …" and "later" keep their meaning. A doubt about one
    # option does not outrank a choice of another: "I don't think a would work,
    # let's go with d" is a yes to d.
    if decides:
        if (DECIDER_REJECT_RE.search(said) or DECIDER_BARE_REFUSAL_RE.search(said)) and not chose:
            return "rejected", None
        m = DECIDER_APPROVE_RE.search(said)
        if m and not HEDGE_RE.search(said):
            return _verdict_for(said, m), None
    if commits:
        verdict = "approved" if chose else "rejected"
    else:
        match = next((m for m in APPROVE_RE.finditer(said)
                      if not _lands_on_a_design(said, m)), None)
        if not match:
            return None
        verdict = _verdict_for(said, match)
    # Strict in one direction only, which is the asymmetry this whole module is
    # built on. A generic NO is still a no — nobody says "not approved" by
    # accident. A generic YES is where the damage is, and the thing that separates
    # one from the other is a hedge, not the words around it: in a loop channel
    # "let's go with a" decides and "I would like to test d" does not.
    if loop and verdict == "approved" and HEDGE_RE.search(said):
        return UNNAMED_APPROVAL, None
    return verdict, None


def _without_the_loops_own(reactions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The reactions minus the loop bot's. A reaction it alone left disappears; one it
    shares with people keeps the people. A reaction carrying no `users` (an old
    fixture) is left as it is."""
    kept = []
    for r in reactions:
        users = r.get("users")
        if users is None:
            kept.append(r)
            continue
        others = [u for u in users if u != LOOP_BOT_USER]
        if others:
            kept.append({**r, "users": others, "count": len(others)})
    return kept


def classify(thread: dict[str, Any], gate: str = GATE_IMPLEMENTATION,
             channel: Optional[str] = None) -> dict[str, Any]:
    """Read one thread into a verdict plus the evidence for it.

    `thread` is the normalised shape produced by `fetch_thread` (and stored in the
    test fixture): `text`, `reactions`, `replies`.

    `gate` says which decision this row is waiting on, derived from its status —
    one thread carries both, weeks apart, and nothing in the text reliably
    separates them. `channel` selects the reaction protocol.

    Returns `verdict`, `evidence` (a human-readable quote or emoji), `decider`
    (Slack user id, or None), `at` (ISO date, or None), `gate`, and `sentiment`
    (reactions that were noted but did not decide).
    """
    replies = thread.get("replies") or []
    # The loop's own marks are read back as marks, not as a person's signal: without
    # this every rejected thread would come back "somebody left a cross", forever.
    reactions = _without_the_loops_own(thread.get("reactions") or [])
    loop = uses_loop_protocol(channel)

    sentiment = [
        f":{r['name']}: from {','.join(r.get('users') or [])}"
        for r in reactions
        if r.get("name") in SENTIMENT_REACTIONS
    ]

    def result(verdict, evidence, decider=None, at=None, ts=None):
        return {"verdict": verdict, "evidence": evidence, "decider": decider,
                "at": at, "gate": gate, "sentiment": sentiment, "ts": ts}

    # 1. Replies outrank reactions — words beat emoji, and they carry a reason.
    #    The LAST decisive reply wins: a thread argues its way to a conclusion,
    #    and `0fbcae45c2c2` is the live example (a misread, a correction, then
    #    "let's leave as is" five messages in).
    # The release gate can only be answered after a build exists to try, so the
    # build announcement is the floor. Everything above it in this thread answered
    # the IMPLEMENTATION gate — counting it here would read "yes, build it" as
    # "yes, ship it", and after an `iterate` round would replay the previous
    # round's refusal as a fresh one.
    floor = 0.0
    if gate == GATE_RELEASE:
        anchored = [_ts_float(r.get("ts")) for r in replies
                    if RELEASE_ANCHOR_RE.search(r.get("text") or "")]
        if not anchored:
            return result(
                "no_decision",
                "nothing announced in this thread yet — the release gate opens "
                "when the TestFlight build (mobile) or the backend release "
                "(recommendations) is posted")
        floor = max(anchored)

    # No floor means no filtering — a reply whose ts is missing or unreadable must
    # still count at the implementation gate, where there is nothing to be after.
    # The anchor above is found among ALL replies — the announcement is ours. From
    # here on only humans count: a bot reply is never a decision.
    human = [r for r in replies if not r.get("bot")]
    considered = (human if not floor
                  else [r for r in human if _ts_float(r.get("ts")) > floor])

    # A redesign request is not a verdict, so it is kept out of the decision scan:
    # "let's fix the design first" would otherwise read as a deferral of the idea.
    decisive = [r for r in considered if not _asks_for_redesign(r.get("text") or "")]

    last: Optional[dict[str, Any]] = None
    # A generic yes in a loop channel is discussion, so it is held apart: it must not
    # become `last` and overwrite a real verdict. "I approve for implementation" and
    # then, three replies later, "let's test d" is one approval followed by somebody
    # talking about it — reading the second as the newer answer would bury the first.
    unnamed: Optional[dict[str, Any]] = None
    # A verdict from somebody who is not this channel's decider. Kept so the thread can
    # say what was said and who it waits on, never written as the answer.
    from_other: Optional[dict[str, Any]] = None
    decider_id = decider_for(channel)
    for reply in decisive:
        sig = _reply_signal(reply.get("text") or "", loop=loop,
                            decides=bool(decider_id) and reply.get("user") == decider_id)
        if not sig:
            continue
        # A reply naming a gate the row has already passed is that gate's own
        # answer, still sitting in the thread — it is WHY the row is here. Reading
        # it again would make a correctly-worded approval an eternal
        # `needs_judgment`.
        if sig[1] and GATE_ORDER.get(sig[1], 0) < GATE_ORDER.get(gate, 0):
            continue
        if sig[0] == UNNAMED_APPROVAL:
            unnamed = {"reply": reply}
            continue
        entry = {"verdict": sig[0], "named_gate": sig[1], "reply": reply}
        if decider_id and reply.get("user") != decider_id:
            from_other = entry
            continue
        last = entry
    if last:
        reply = last["reply"]
        quote = (reply.get("text") or "").strip().replace("\n", " ")[:300]
        named = last["named_gate"]
        # Naming a gate the row has NOT reached is the opposite case: somebody is
        # on the wrong thread, or the row is behind what the team thinks. Both
        # want a person rather than a silent resolution.
        if named and named != gate:
            return result(
                "needs_judgment",
                f"reply decides the {named} gate, but this row is waiting on the "
                f"{gate} gate — “{quote}”",
                reply.get("user"), _ts_to_date(reply.get("ts")), reply.get("ts"))
        return result(last["verdict"], quote,
                      reply.get("user"), _ts_to_date(reply.get("ts")), reply.get("ts"))

    if from_other:
        # Somebody answered, but not the person this channel decides by. Saying who
        # it waits on is the useful answer; writing their verdict would not be true.
        reply = from_other["reply"]
        quote = (reply.get("text") or "").strip().replace("\n", " ")[:300]
        return result(
            "needs_judgment",
            f"<@{reply.get('user')}> answered “{quote}” — this channel is decided by "
            f"<@{decider_id}>, so the row waits on them",
            reply.get("user"), _ts_to_date(reply.get("ts")), reply.get("ts"))

    if unnamed:
        # Reached only when nothing decided. The reactions branch below answers the
        # same way and for the same reason: the person learns their signal did not
        # count, which a silent miss never teaches.
        reply = unnamed["reply"]
        quote = (reply.get("text") or "").strip().replace("\n", " ")[:300]
        return result(
            "needs_judgment",
            f"reads like a yes but does not name a gate, so it is discussion, not a "
            f"decision — reply “I approve for implementation” (or “for release”) to "
            f"decide — “{quote}”",
            reply.get("user"), _ts_to_date(reply.get("ts")), reply.get("ts"))

    # 1c. No decision in words. "I like the idea but not the design" is a request for
    #     another picture — not a rejection (the idea was just endorsed) and not a
    #     question. The designer answers it in-thread and the row stays `shared` for
    #     the human decision. Sits AFTER the decision scan so an explicit verdict
    #     always wins, and BEFORE the question scan so "can we try another
    #     placement?" is read as the redesign it is.
    redesigns = [r for r in considered if _asks_for_redesign(r.get("text") or "")]
    if redesigns:
        req = redesigns[-1]
        req_ts = _ts_float(req.get("ts"))
        # Idempotent: a bot reply after the request is the revised design landing —
        # do not re-flag it on every tick.
        redone = any(r.get("bot") and _ts_float(r.get("ts")) > req_ts for r in replies)
        if not redone:
            quote = (req.get("text") or "").strip().replace("\n", " ")[:300]
            return result("needs_redesign", f"redesign requested — “{quote}”",
                          req.get("user"), _ts_to_date(req.get("ts")))

    # 1b. No decision in words. A question waiting on an answer is its own outcome:
    #     the loop can answer most of them from the priors, and the row stays
    #     `shared` until a HUMAN decides — answering is not deciding. This sits
    #     AFTER the decision scan, so it can never turn a decision into a question,
    #     and a question is never read as a soft rejection.
    questions = [r for r in considered if is_question(r.get("text") or "")]
    if questions:
        q = questions[-1]
        q_ts = _ts_float(q.get("ts"))
        # Idempotent: if a bot reply (our answer) already landed after the question,
        # it has been handled — do not re-flag it on every hourly tick.
        answered = any(r.get("bot") and _ts_float(r.get("ts")) > q_ts for r in replies)
        if not answered:
            quote = (q.get("text") or "").strip().replace("\n", " ")[:300]
            return result("needs_answer", f"unanswered question — “{quote}”",
                          q.get("user"), _ts_to_date(q.get("ts")))

    # 2. Reactions, read under the protocol of the channel the thread is in.
    #    They carry no timestamp, so two that disagree cannot be ordered and must
    #    not be guessed between.
    #
    #    Skipped entirely at the release gate: a reaction sits on the PARENT
    #    message, which was posted before the build existed, so it cannot be an
    #    answer about that build however it is read.
    if gate == GATE_RELEASE:
        pass
    elif loop:
        # Here the marks are the loop's own — a build mark, a cross, a check mark — and
        # none is a decision about a gate. A person who leaves one is using the wrong
        # signal, and saying so is more useful than acting on it: they learn it did not
        # count, which a silently-ignored reaction never teaches.
        marked = [r for r in reactions if r.get("name") in LOOP_TERMINAL_REACTIONS]
        if marked:
            means = "/".join(sorted({LOOP_TERMINAL_REACTIONS[r["name"]] for r in marked}))
            emoji = " and ".join(f":{r['name']}:" for r in marked)
            return result("needs_judgment",
                          f"{emoji} marks {means} in this channel — the loop's own mark, "
                          f"not a decision — the {gate} gate is answered by replying")
    else:
        approvers = [u for r in reactions if r.get("name") in ARCHIVE_APPROVE_REACTIONS
                     for u in (r.get("users") or [])]
        rejecters = [u for r in reactions if r.get("name") in ARCHIVE_REJECT_REACTIONS
                     for u in (r.get("users") or [])]
        if approvers and rejecters:
            return result("needs_judgment",
                          f"conflicting reactions — approve from {','.join(approvers)}, "
                          f"reject from {','.join(rejecters)}")
        if approvers:
            return result("approved", ":white_check_mark: reaction, no written reason",
                          approvers[0], _ts_to_date(thread.get("ts")))
        if rejecters:
            return result("rejected", ":x:/:-1: reaction, no written reason",
                          rejecters[0], _ts_to_date(thread.get("ts")))

    # 3. Replies but nothing decisive in them — a real discussion the loop should
    #    not resolve on its own. Distinct from silence: somebody engaged, so the
    #    thread needs a person rather than another tick.
    if considered:
        return result("needs_judgment",
                      (considered[-1].get("text") or "").strip().replace("\n", " ")[:300])

    # 4. Silence, or thumbs only. Not a decision.
    return result("no_decision", "")


def _ts_float(ts: Optional[str]) -> float:
    """Slack ts as a float, or 0.0 — an unreadable ts sorts before every floor."""
    try:
        return float(ts)
    except (TypeError, ValueError):
        return 0.0


def _ts_to_date(ts: Optional[str]) -> Optional[str]:
    """Slack epoch-seconds string -> ISO date. Slack ts are UTC epochs."""
    if not ts:
        return None
    try:
        return dt.datetime.fromtimestamp(float(ts), dt.timezone.utc).date().isoformat()
    except (TypeError, ValueError):
        return None


# ---------- Slack + Notion I/O ----------------------------------------------

SLACK_URL_RE = re.compile(r"/archives/([A-Z0-9]+)/p(\d{10})(\d{6})")


def parse_slack_url(url: str) -> Optional[tuple[str, str]]:
    """`.../archives/C123/p1776465439961579` -> ("C123", "1776465439.961579")."""
    m = SLACK_URL_RE.search(url or "")
    if not m:
        return None
    return m.group(1), f"{m.group(2)}.{m.group(3)}"


def normalise_replies(msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Raw Slack replies in the shape `classify` reads, each flagged bot or human.

    Bot replies are **kept and flagged, not dropped**. They are never classified:
    `share-ideas` posts each idea's screenshot into its own thread, and counting
    that as discussion would re-surface every screenshotted idea as
    `needs_judgment` on every hourly tick, forever.

    But the build announcement that opens the release gate is also ours, so
    dropping bot replies outright would leave that gate permanently shut — the
    floor would never be found and every `implementing` row would read
    `no_decision` no matter what a human said.

    `bot_id` is the discriminator, NOT `app_id`: a human replying through a Slack
    integration carries an `app_id` too, and Rohan's PROD-2126 link — a genuine
    decision in the archive — is exactly that shape.
    """
    return [
        {"user": m.get("user"), "ts": m["ts"], "text": m.get("text", ""),
         "bot": bool(m.get("bot_id")),
         "seen": any(r.get("name") == SEEN_MARK and LOOP_BOT_USER in (r.get("users") or [])
                     for r in m.get("reactions") or [])}
        for m in msgs
    ]


def unaddressed(thread: dict[str, Any]) -> Optional[dict[str, Any]]:
    """The newest human reply after the loop's last reply that the loop has not marked
    seen, or None. A reply the loop answered (a bot reply after it) or marked 👀 is
    addressed; anything else is a person waiting on the loop."""
    replies = thread.get("replies") or []
    last_bot = max((_ts_float(r.get("ts")) for r in replies if r.get("bot")), default=0.0)
    waiting = [r for r in replies if not r.get("bot") and not r.get("seen")
               and _ts_float(r.get("ts")) > last_bot]
    return waiting[-1] if waiting else None


def follow_up(thread: dict[str, Any], gate: Optional[str] = None) -> dict[str, Any]:
    """`needs_follow_up` for an unaddressed human reply, else `no_decision`."""
    r = unaddressed(thread)
    if r is None:
        return {"verdict": "no_decision", "evidence": "", "decider": None, "at": None,
                "gate": gate, "sentiment": []}
    quote = (r.get("text") or "").strip().replace("\n", " ")[:300]
    return {"verdict": "needs_follow_up", "evidence": f"new message — “{quote}”",
            "decider": r.get("user"), "at": _ts_to_date(r.get("ts")), "gate": gate,
            "sentiment": [], "ts": r.get("ts")}


def mark_seen(channel: str, ts: str) -> bool:
    """React 👀 as the loop on a reply it read and needs to do nothing about."""
    from thread_marks import _call  # noqa: PLC0415
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token:
        return False
    res = _call("reactions.add", token, channel=channel, timestamp=ts, name=SEEN_MARK)
    return bool(res.get("ok")) or res.get("error") == "already_reacted"


def fetch_thread(channel: str, ts: str) -> dict[str, Any]:
    """One Slack call per thread. `conversations.replies` returns the parent at
    index 0 carrying its `reactions` (with the reacting user ids), so the reaction
    and reply reads do not need separate round trips."""
    msgs = slack_read.replies(channel, ts)
    if not msgs:
        return {"channel": channel, "ts": ts, "text": "", "reactions": [], "replies": []}
    parent = msgs[0]
    return {
        "channel": channel,
        "ts": ts,
        "text": parent.get("text", ""),
        "reactions": parent.get("reactions") or [],
        "replies": normalise_replies(msgs[1:]),
    }


def harvestable_rows(ds_id: str) -> list[dict[str, Any]]:
    """Pending / shared rows that carry a Slack thread to read."""
    rows = []
    for page in notion.query_data_source(ds_id, page_size=100):
        status = select_value(page, "Status")
        if status not in HARVESTABLE + FOLLOW_UP_ONLY:
            continue
        url = (page.get("properties", {}).get("Slack thread") or {}).get("url") or ""
        if not url:
            continue
        rows.append({
            "page_id": page["id"],
            "title": title_text(page, "Title"),
            "fingerprint": rich_text_value(page, "Fingerprint"),
            "status": status,
            "slack_thread": url,
            "decided_at": date_start(page, "Decided at"),
        })
    return rows


# email -> notion id, and normalised name -> notion id (or None when the name is
# not unique in the workspace, so an ambiguous name can never resolve).
def _norm_name(name: Optional[str]) -> str:
    """Lowercased, whitespace-collapsed name, for the Slack <-> Notion join."""
    return " ".join((name or "").split()).lower()


_USER_CACHE: Optional[dict[str, str]] = None
_NAME_CACHE: Optional[dict[str, Optional[str]]] = None


def slack_to_notion_user(slack_id: Optional[str]) -> Optional[str]:
    """Map a Slack user id to a Notion user id, by email then by exact name.

    Slack and Notion have unrelated id spaces, so the join has to go through
    something the two share. Email is the right key and is tried first — but this
    workspace's bot token has no `users:read.email` scope, so `profile.email`
    comes back absent and email alone would never match anyone. `real_name` is
    available and does match Notion's display names exactly for the whole team,
    so it is the fallback.

    The name match is **exact and unambiguous by construction**: normalised
    case/whitespace, and a name held by two Notion members resolves to nobody.
    Fuzzy matching here would mean attributing a decision to the wrong person,
    which is worse than leaving `Decided by` empty — and an unattributed verdict
    is still worth writing.
    """
    global _USER_CACHE, _NAME_CACHE
    if not slack_id:
        return None
    if _USER_CACHE is None:
        # Build into a local and only publish it once the whole listing
        # succeeded. Assigning the cache first would freeze a partial (or empty)
        # map for the rest of the run, so one transient failure would silently
        # drop attribution from every remaining row.
        built: dict[str, str] = {}
        by_name: dict[str, Optional[str]] = {}
        cursor: Optional[str] = None
        try:
            while True:
                params: dict[str, Any] = {"page_size": 100}
                if cursor:
                    params["start_cursor"] = cursor
                payload = notion._request("GET", "/users", params=params)  # noqa: SLF001
                for u in payload.get("results", []):
                    if u.get("type") != "person":
                        continue
                    email = ((u.get("person") or {}).get("email") or "").lower()
                    if email:
                        built[email] = u["id"]
                    name = _norm_name(u.get("name"))
                    if name:
                        # Second sighting of a name makes it ambiguous, and an
                        # ambiguous name must resolve to nobody rather than to
                        # whichever member happened to be listed first.
                        by_name[name] = None if name in by_name else u["id"]
                if not payload.get("has_more"):
                    break
                cursor = payload.get("next_cursor")
                if not cursor:
                    break
        except Exception:  # noqa: BLE001
            return None
        _USER_CACHE, _NAME_CACHE = built, by_name
    try:
        payload = slack_read._get(  # noqa: SLF001
            "users.info", {"user": slack_id}, token=slack_read._token(None)  # noqa: SLF001
        )
        user = payload.get("user") or {}
        profile = user.get("profile") or {}
    except Exception:  # noqa: BLE001
        return None

    email = (profile.get("email") or "").lower()
    if email and email in _USER_CACHE:
        return _USER_CACHE[email]

    name = _norm_name(profile.get("real_name") or user.get("real_name") or profile.get("display_name"))
    return (_NAME_CACHE or {}).get(name) if name else None


def write_verdict(row: dict[str, Any], result: dict[str, Any]) -> None:
    """Write one harvested verdict. Never touches a row a human already decided —
    `harvestable_rows` only yields undecided rows, so that is structural.

    Refuses at the release gate: those outcomes belong to `ship-idea`, which owns
    the note rounds, the approval marker and the required refusal reason. See
    `ship_command`.
    """
    if result.get("gate") == GATE_RELEASE:
        raise ValueError(
            f"release-gate verdicts are not written here — run "
            f"`{ship_command(result['verdict'], row['fingerprint'])}`")
    verdict = result["verdict"]
    note = (
        f"Harvested from Slack: {result['evidence']}"
        if result["evidence"] else "Harvested from Slack."
    )
    if result["sentiment"]:
        note += f" Sentiment: {'; '.join(result['sentiment'])}."
    if verdict in ("rejected", "deferred"):
        note += (
            " Reason is a harvest default — the thread carried no stated reason, "
            "so it needs sharpening by a human or a later pass."
        )

    props: dict[str, Any] = {
        "Status": make_select(verdict),
        "Quick status note": make_rich_text(note[:1900]),
    }
    if verdict == "rejected":
        props["Rejection reason"] = make_select(HARVEST_REJECT_REASON)
    elif verdict == "deferred":
        props["Rejection reason"] = make_select(HARVEST_DEFER_REASON)

    # Write-once, mirroring manage_ideas: a date already on the row is the real
    # decision date and must not be replaced by a later harvest.
    if not row.get("decided_at") and result.get("at"):
        props["Decided at"] = make_date(result["at"])

    notion_user = slack_to_notion_user(result.get("decider"))
    if notion_user:
        props["Decided by"] = {"people": [{"object": "user", "id": notion_user}]}

    notion.update_page_props(row["page_id"], props)


# ---------- subcommands ------------------------------------------------------

# Verdicts this skill writes ITSELF, and only at the implementation gate.
WRITABLE = {"approved", "rejected", "deferred"}


def ship_command(verdict: str, fingerprint: str) -> Optional[str]:
    """The `ship-idea` call a release-gate verdict turns into.

    The harvester reads the thread; it does not write release-gate outcomes. Those
    go through `ship-idea`, which appends the round to the note, carries the
    approval marker `request-experiment` later checks for, and REQUIRES a reason
    on a refusal. Reproducing any of that here would mean two writers for one
    state, drifting apart the first time either changes.

    So a gate-2 verdict comes out as the command to run, not as a write.
    """
    if verdict == "approved":
        return f"ship_idea.py release {fingerprint} --by <who>"
    if verdict in ("rejected", "deferred"):
        return f"ship_idea.py unwind {fingerprint} --reason \"<why it is not shipping>\""
    return None


def _gather(args: argparse.Namespace) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    if args.fixture:
        threads = json.load(open(args.fixture))
        return [
            ({"page_id": None, "title": t.get("slack_fingerprint", "?"),
              "fingerprint": t.get("slack_fingerprint", "?"), "status": "shared",
              "slack_thread": "", "decided_at": None},
             classify(t, GATE_IMPLEMENTATION, ARCHIVE_CHANNEL))
            for t in threads
        ]

    if not notion.available:
        raise MCPFallback("NOTION_TOKEN is not set.")
    out = []
    for row in harvestable_rows(args.data_source_id):
        parsed = parse_slack_url(row["slack_thread"])
        if not parsed:
            print(f"  ! {row['fingerprint']} unparseable Slack URL: {row['slack_thread']}", file=sys.stderr)
            continue
        channel, ts = parsed
        if row["status"] in FOLLOW_UP_ONLY:
            out.append((row, follow_up(fetch_thread(channel, ts))))
            continue
        gate = gate_for_status(row["status"])
        if gate is None:
            # harvestable_rows already filters, so this means the vocabulary and
            # the gate map disagree — worth saying rather than defaulting.
            print(f"  ! {row['fingerprint']} status '{row['status']}' maps to no gate; skipped",
                  file=sys.stderr)
            continue
        thread = fetch_thread(channel, ts)
        res = classify(thread, gate, channel)
        if res["verdict"] == "no_decision":
            # Nothing decides it, but a person may still have written something the
            # loop owes an answer to — a gate row's thread is read like any other.
            res = {**follow_up(thread, gate), "gate": gate} if unaddressed(thread) else res
        out.append((row, res))
    return out


def cmd_plan(args: argparse.Namespace) -> int:
    pairs = _gather(args)
    counts = dict.fromkeys(VERDICTS, 0)
    for row, res in pairs:
        counts[res["verdict"]] += 1
        gate2 = res.get("gate") == GATE_RELEASE
        cmd = ship_command(res["verdict"], row["fingerprint"]) if gate2 else None
        mark = "  " if (gate2 or res["verdict"] not in WRITABLE) else "->"
        tag = " [release gate]" if gate2 else ""
        print(f"{mark} {row['fingerprint']}  {res['verdict']:<15} {row['title'][:44]}{tag}")
        if res["evidence"]:
            print(f"     {res['evidence'][:140]}")
        if cmd:
            print(f"     run: {cmd}")
    print()
    print("  ".join(f"{k}={v}" for k, v in counts.items() if v))
    writable = sum(1 for _, r in pairs
                   if r["verdict"] in WRITABLE and r.get("gate") != GATE_RELEASE)
    gate2 = sum(1 for _, r in pairs
                if r.get("gate") == GATE_RELEASE and r["verdict"] in WRITABLE)
    print(f"\n{len(pairs)} threads read, {writable} would be written")
    if gate2:
        print(f"{gate2} release-gate decision(s) need `ship-idea` — this skill does not "
              f"write those, because ship-idea owns the note rounds, the approval marker "
              f"and the required refusal reason.")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    if args.fixture:
        raise SystemExit("--fixture is read-only; use plan or classify")
    pairs = _gather(args)
    written = 0
    for row, res in pairs:
        if res["verdict"] not in WRITABLE:
            continue
        if res.get("gate") == GATE_RELEASE:
            print(f"= {row['fingerprint']} release gate: {res['verdict']} — run "
                  f"`{ship_command(res['verdict'], row['fingerprint'])}`")
            continue
        write_verdict(row, res)
        print(f"~ {row['fingerprint']} [{row['status']} -> {res['verdict']}]  {row['title'][:60]}")
        # The deciding reply is handled: mark it 👀 so the follow-up pass, which reads
        # the thread again once the row has moved past the gate, never raises it.
        parsed = parse_slack_url(row["slack_thread"])
        if res.get("ts") and parsed:
            mark_seen(parsed[0], res["ts"])
        if res["verdict"] in MARKS:
            print(f"    thread: {mark_thread(row['slack_thread'], res['verdict'])}")
        written += 1
    print(f"\n{written} rows written, {len(pairs) - written} left for judgment")
    return 0


def cmd_mark_seen(args: argparse.Namespace) -> int:
    parsed = parse_slack_url(args.url)
    if not parsed:
        print(f"REFUSED: not a Slack permalink: {args.url}", file=sys.stderr)
        return 2
    m = re.search(r"/p(\d{10})(\d{6})", args.url)
    ts = f"{m.group(1)}.{m.group(2)}" if m else parsed[1]
    ok = mark_seen(parsed[0], ts)
    print("marked seen" if ok else "could not mark it", file=sys.stderr)
    return 0 if ok else 2


def cmd_classify(args: argparse.Namespace) -> int:
    threads = json.load(open(args.fixture))
    for t in threads:
        res = classify(t)
        print(f"{t.get('slack_fingerprint', t.get('ts')):<14} {res['verdict']:<15} {res['evidence'][:90]}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="harvest_idea_decisions",
        description="Harvest Slack idea-thread decisions into the Loop Notion database.",
    )
    p.add_argument("--data-source-id", default=AURIE_FEATURES_DS)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("plan", help="Classify and print; write nothing")
    sp.add_argument("--fixture", help="Read threads from a JSON fixture instead of Slack")
    sp.set_defaults(func=cmd_plan)

    sp = sub.add_parser("apply", help="Classify and write the confident verdicts")
    sp.add_argument("--fixture", help=argparse.SUPPRESS)
    sp.set_defaults(func=cmd_apply)

    sp = sub.add_parser("mark-seen", help="React 👀 on a reply the loop read and needs "
                                         "to do nothing about")
    sp.add_argument("url", help="Permalink of the reply")
    sp.set_defaults(func=cmd_mark_seen)

    sp = sub.add_parser("classify", help="Run the classifier over a fixture file")
    sp.add_argument("fixture")
    sp.set_defaults(func=cmd_classify)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except MCPFallback as e:
        print(f"harvest-idea-decisions: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
