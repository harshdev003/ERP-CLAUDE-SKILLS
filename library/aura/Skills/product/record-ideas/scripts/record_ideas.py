"""Record product ideas into the `Learnings/Loop/<Loop>` Notion database.

Source-agnostic — the same script ingests a sim-test session's
`findings_ideas.json`, a one-time `ideas.json` migration dump, or a single
hand-written idea passed via flags / stdin. Dedup is fingerprint-based, not
source-based, so the same idea surfaced from two different sources updates one
row rather than creating a duplicate.

Subcommands
-----------
    plan        Read input, compute fingerprints, print a CREATE/UPDATE/SKIP
                plan against Notion. No writes. (Default behaviour.)

    apply       Run the plan and execute the writes.

    list        Print the current contents of the Notion database.

The script targets the Aurie database by default
(``data_source_id = AURIE_FEATURES_DS``). Each loop gets its own database,
pass ``--data-source-id <id>``.

Input shapes
------------
``--from-json <path>``: JSON array of idea objects with at minimum
    {title, description, screen, category, lever, impact, effort,
     rationale?, hypothesis?, firstSeenAt?, lastSeenAt?, status?, statusNote?,
     slack? {permalink}}
plus the optional proposal fields `product:brainstorm-ideas` emits:
    {scope?, expectedImpact?, priorArt?, proposedBy?, portfolio?, betSize?,
     confidence?, evidenceGrade?, variants?, testPlan?}
Recognises both the legacy AuraConsumerApp `ideas.json` shape (with
``status``/``statusNote``) and the sim-test session ``findings_ideas.json``
shape (no status — defaults to ``pending``).

``--from-stdin``: same JSON shape via stdin.

``--idea '<json>'``: single idea inline.

Writes use ``NOTION_TOKEN`` (set in ``~/.claude/settings.json`` env block).
See the AINative root CLAUDE.md → "Notion REST integration" for setup.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from notion_rest import (  # noqa: E402
    MCPFallback,
    date_start,
    make_date,
    make_rich_text,
    make_select,
    make_title,
    notion,
    rich_text_value,
    select_value,
    title_text,
)


AURIE_FEATURES_DS = "8d14f3eb-0c55-4f25-9322-ab2b2a69f092"

# The file-upload endpoints are not in the vendored notion_rest client, so the two
# upload calls are made directly. Pinned to the same version the client uses, so a
# single upgrade moves both rather than leaving them on different API contracts.
NOTION_FILE_API_VERSION = "2025-09-03"

# Allowed enum values for the strictly-typed select properties in Notion.
# Category and Lever are intentionally free-text in Notion (the analyzer
# generates a much wider vocabulary than any fixed enum — 13+ categories
# including gamification, shareability, referral, etc.). Status / Impact /
# Effort stay strict because they drive UI colours + downstream sorting.
VALID_IMPACTS = {"high", "medium", "low"}
VALID_EFFORTS = {"S", "M", "L"}
# The altitude of an idea: an existing screen, an existing feature, or one that does not
# exist yet. Optional — rows from before the field, and callers that do not know, leave
# it blank rather than guess.
VALID_SCOPES = {"screen", "feature", "new"}

# The proposal's place in the run's portfolio, its size, how sure the proposer is, and
# how strong the evidence under it is (A first-party experiment or verified behaviour,
# B repeated first-party qualitative signal, C direct-competitor observation, D adjacent
# or market inspiration). Optional, like scope: a source that does not grade leaves them
# blank rather than guess.
VALID_PORTFOLIOS = {"evidence", "adjacent", "moonshot"}
VALID_BET_SIZES = {"small", "medium", "large"}
VALID_CONFIDENCES = {"high", "medium", "low"}
VALID_EVIDENCE_GRADES = {"A", "B", "C", "D"}

# Notion caps one rich_text run at 2000 characters and refuses the whole create past it.
RICH_TEXT_MAX = 2000

# Mirrors the `Domain` select in Notion. The sim-test ideas analyzer resolves the
# domain from the screen id and copies it in verbatim, so a variant spelling here
# silently lands the row in a new select option rather than the intended one.
VALID_DOMAINS = {
    "onboarding", "paywall", "home/feed", "conversation", "journal", "grounding",
    "affirmations", "profile", "settings", "notifications", "growth/referral", "other",
}

# A rejection is only useful if the next proposal can see it. These statuses mean
# "we already answered this", and `plan` warns when a new idea looks like one.
ANSWERED_STATUSES = {"rejected", "lost"}

# The full Loop lifecycle. A row only ever enters this database at "pending"
# (or at an explicit status on a migration import); everything past "shared" is
# written by the loop's own skills, never by an ingest. Listed here so a
# hand-written or migrated source carrying a later status still validates.
VALID_STATUSES = {
    # Proposal → review
    "pending", "shared", "approved", "rejected", "deferred",
    # Build → release
    "implementing", "shipped_dormant",
    # Measurement
    "experiment_running", "won", "lost", "inconclusive", "abandoned",
}


def _expected_impact(raw) -> str:
    """Render `expectedImpact` into the one rich-text line Notion holds.

    Taken as the object the brainstorm step emits — `{trial, paid, session, risk}` —
    or passed through when a caller already has a string. Anything else is dropped
    rather than guessed at: a malformed prediction is worse than none, because it
    reads as one.
    """
    if isinstance(raw, str):
        return raw.strip()
    if not isinstance(raw, dict):
        return ""
    parts = [f"{k.title()}: {str(raw[k]).strip()}"
             for k in ("trial", "paid", "session", "retention", "risk") if str(raw.get(k, "")).strip()]
    return "\n".join(parts)


def _test_plan(raw) -> str:
    """Render `testPlan` — how the experiment would be read — into one rich-text block.

    Taken as `{primaryMetric, direction, guardrails, instrumentation, decisionWindow,
    falsification}` or passed through as a string. Anything else is dropped, for the
    same reason as `_expected_impact`.
    """
    if isinstance(raw, str):
        return raw.strip()[:RICH_TEXT_MAX]
    if not isinstance(raw, dict):
        return ""
    labels = (("primaryMetric", "Primary metric"), ("direction", "Direction"),
              ("guardrails", "Guardrails"), ("instrumentation", "Instrumentation"),
              ("decisionWindow", "Decision window"), ("falsification", "Falsified if"))
    parts = []
    for key, label in labels:
        val = raw.get(key)
        if isinstance(val, list):
            val = "; ".join(str(v).strip() for v in val if str(v).strip())
        val = str(val or "").strip()
        if val:
            parts.append(f"{label}: {val}")
    return "\n".join(parts)[:RICH_TEXT_MAX]


def _variants(raw) -> str:
    """Render `variants` — the arms an experiment would compare — one per line."""
    if isinstance(raw, str):
        return raw.strip()[:RICH_TEXT_MAX]
    if not isinstance(raw, list):
        return ""
    return "\n".join(f"- {str(v).strip()}" for v in raw if str(v).strip())[:RICH_TEXT_MAX]


@dataclass
class Idea:
    """Canonical in-memory shape — source-agnostic.

    Sources convert their own representation to this shape before dedup.
    """

    title: str
    description: str
    screen: str = ""
    domain: str = ""
    screenshot_path: str = ""
    category: str = ""
    lever: str = ""
    impact: str = ""
    effort: str = ""
    rationale: str = ""
    hypothesis: str = ""
    # What the idea is predicted to do to each metric every experiment is read
    # against, and the one it might cost. A prediction that moves everything is a
    # prediction nobody made: `dailyPractices3` had its best arm on conversion and
    # its worst on engagement, and reading trial rate alone would have hidden it.
    expected_impact: str = ""
    # What we already know about this lever or job, and what is different about this
    # version. `brainstorm-ideas` requires it whenever anything it read touches the
    # same ground — it is the evidence that an idea is not a settled question being
    # asked again.
    prior_art: str = ""
    # The altitude: an existing screen, an existing feature, or one that does not exist
    # yet. Tells the rest of the loop what shape of design to expect — a sheet, a flow,
    # or something drawn from the tokens because there is no capture to compose onto.
    scope: str = ""
    # Who proposed it — `claude`, `viktor`, `sim-test`, a person. Two ideation routes
    # feed the same backlog, and comparing what each gets approved and what wins needs
    # the route on the row.
    proposed_by: str = ""
    portfolio: str = ""
    bet_size: str = ""
    confidence: str = ""
    evidence_grade: str = ""
    variants: str = ""
    test_plan: str = ""
    status: str = "pending"
    status_note: str = ""
    first_seen: str = ""  # ISO-8601 date
    last_seen: str = ""   # ISO-8601 date
    slack_thread: str = ""
    linear_issue: str = ""

    def fingerprint(self) -> str:
        """The idea's **dedup key** — not its address.

        Computed from `screen|category|title` so the same idea surfacing in a
        sim-test session and a manual brainstorm hashes the same and collapses to
        one row. Written on CREATE and then frozen — an update never rewrites the
        stored `Fingerprint`.

        The row's *address* is the separate, immutable `Idea ID` (`IDEA-42`)
        minted by Notion — that is what skills, Slack threads and Linear cards
        resolve by. Do not recompute this fingerprint to address a row: the inputs
        are editable, so a fingerprint derived from a renamed title differs from
        the stored one and resolves to nothing. A rename is instead detected on
        re-record (`_find_rename`) so it folds back onto its row rather than
        minting a duplicate.
        """
        key = f"{self.screen}|{self.category}|{self.title.strip().lower()}"
        return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]

    def validate(self) -> list[str]:
        """Return a list of human-readable validation errors (empty == valid)."""
        errs: list[str] = []
        if not self.title.strip():
            errs.append("title is required")
        if self.impact and self.impact not in VALID_IMPACTS:
            errs.append(f"impact '{self.impact}' not in {sorted(VALID_IMPACTS)}")
        if self.effort and self.effort not in VALID_EFFORTS:
            errs.append(f"effort '{self.effort}' not in {sorted(VALID_EFFORTS)}")
        if self.scope and self.scope not in VALID_SCOPES:
            errs.append(f"scope '{self.scope}' not in {sorted(VALID_SCOPES)}")
        for value, valid, name in (
            (self.portfolio, VALID_PORTFOLIOS, "portfolio"),
            (self.bet_size, VALID_BET_SIZES, "betSize"),
            (self.confidence, VALID_CONFIDENCES, "confidence"),
            (self.evidence_grade, VALID_EVIDENCE_GRADES, "evidenceGrade"),
        ):
            if value and value not in valid:
                errs.append(f"{name} '{value}' not in {sorted(valid)}")
        if self.status not in VALID_STATUSES:
            errs.append(f"status '{self.status}' not in {sorted(VALID_STATUSES)}")
        if self.domain and self.domain not in VALID_DOMAINS:
            errs.append(f"domain '{self.domain}' not in {sorted(VALID_DOMAINS)}")
        # A bad `screenshotPath` is deliberately NOT a validation error.
        # `load_ideas` aborts the whole batch on any validation failure, so a
        # stale or cwd-relative path would cost every idea in the file — including
        # the ones whose screenshots are fine. That contradicts the rule this
        # skill states elsewhere: the screenshot is enrichment and must never cost
        # the idea. `upload_screenshot` warns and returns None instead.
        return errs


def _coerce(raw: dict[str, Any]) -> Idea:
    """Convert a raw source dict to the canonical Idea shape.

    Handles both the legacy `ideas.json` schema (snake-ish, with slack block)
    and a freeform schema where fields may be missing.
    """
    slack = raw.get("slack") or {}
    return Idea(
        title=raw.get("title", "").strip(),
        description=raw.get("description", "").strip(),
        screen=raw.get("screen", "").strip(),
        domain=raw.get("domain", "").strip(),
        screenshot_path=(raw.get("screenshotPath") or "").strip(),
        expected_impact=_expected_impact(raw.get("expectedImpact")),
        scope=(raw.get("scope") or "").strip().lower(),
        category=raw.get("category", "").strip(),
        lever=raw.get("lever", "").strip(),
        impact=raw.get("impact", "").strip(),
        effort=raw.get("effort", "").strip(),
        rationale=raw.get("rationale", "").strip(),
        hypothesis=raw.get("hypothesis", "").strip(),
        prior_art=(raw.get("priorArt") or "").strip(),
        proposed_by=(raw.get("proposedBy") or "").strip().lower(),
        portfolio=(raw.get("portfolio") or "").strip().lower(),
        bet_size=(raw.get("betSize") or "").strip().lower(),
        confidence=(raw.get("confidence") or "").strip().lower(),
        evidence_grade=(raw.get("evidenceGrade") or "").strip().upper(),
        variants=_variants(raw.get("variants")),
        test_plan=_test_plan(raw.get("testPlan")),
        status=(raw.get("status") or "pending").strip(),
        status_note=(raw.get("statusNote") or "").strip(),
        first_seen=_date_only(raw.get("firstSeenAt", "")),
        last_seen=_date_only(raw.get("lastSeenAt", "")),
        slack_thread=slack.get("permalink", "").strip(),
        linear_issue=(raw.get("linear") or {}).get("issueUrl", "").strip(),
    )


def _date_only(s: str) -> str:
    """Strip the time component from an ISO-8601 timestamp; pass through ''."""
    if not s:
        return ""
    return s.split("T", 1)[0]


def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


# ---------- input adapters ---------------------------------------------------

def load_ideas(args: argparse.Namespace) -> list[Idea]:
    raw: list[dict[str, Any]]
    if args.from_json:
        with open(args.from_json) as f:
            raw = json.load(f)
        if not isinstance(raw, list):
            raise SystemExit("--from-json: expected a JSON array of ideas")
    elif args.from_stdin:
        raw = json.load(sys.stdin)
        if not isinstance(raw, list):
            raise SystemExit("--from-stdin: expected a JSON array of ideas")
    elif args.idea:
        one = json.loads(args.idea)
        raw = [one] if isinstance(one, dict) else list(one)
    else:
        raise SystemExit(
            "no input — pass one of --from-json <path>, --from-stdin, --idea '<json>'"
        )

    ideas = [_coerce(r) for r in raw]
    bad: list[tuple[int, list[str]]] = []
    for i, idea in enumerate(ideas):
        errs = idea.validate()
        if errs:
            bad.append((i, errs))
    if bad:
        msg = "\n".join(f"  [{i}] '{ideas[i].title[:40]}': {', '.join(errs)}" for i, errs in bad)
        raise SystemExit(f"validation errors:\n{msg}")
    return ideas


# ---------- Notion read ------------------------------------------------------

def idea_id_str(row: dict[str, Any]) -> str:
    """The row's human-friendly address, e.g. 'IDEA-42', or '' if unset.

    Minted by Notion's auto-incrementing `Idea ID` property — immutable, and the
    id every skill and thread should reference. Distinct from the Fingerprint,
    which is the content-derived dedup key.
    """
    uid = (row.get("properties", {}).get("Idea ID") or {}).get("unique_id") or {}
    num = uid.get("number")
    if num is None:
        return ""
    prefix = uid.get("prefix")
    return f"{prefix}-{num}" if prefix else str(num)


def load_existing(ds_id: str) -> dict[str, dict[str, Any]]:
    """Fingerprint -> {page_id, idea_id, status, slack, linear, last_seen} for every row."""
    out: dict[str, dict[str, Any]] = {}
    for row in notion.query_data_source(ds_id, page_size=100):
        fp = rich_text_value(row, "Fingerprint")
        if not fp:
            continue
        out[fp] = {
            "page_id": row["id"],
            "idea_id": idea_id_str(row),
            "title": title_text(row, "Title"),
            "status": select_value(row, "Status"),
            "screen": rich_text_value(row, "Screen"),
            "category": rich_text_value(row, "Category"),
            "reason": select_value(row, "Rejection reason"),
            "note": rich_text_value(row, "Quick status note"),
            "learning": rich_text_value(row, "Learning"),
            "slack_thread": (row.get("properties", {}).get("Slack thread") or {}).get("url") or "",
            "linear_issue": (row.get("properties", {}).get("Linear issue") or {}).get("url") or "",
            "last_seen": date_start(row, "Last seen"),
            "first_seen": date_start(row, "First seen"),
            "proposed_by": select_value(row, "Proposed by"),
        }
    return out


# ---------- planning ---------------------------------------------------------

@dataclass
class Plan:
    creates: list[Idea] = field(default_factory=list)
    updates: list[tuple[Idea, dict[str, Any]]] = field(default_factory=list)
    skips: list[tuple[Idea, str]] = field(default_factory=list)
    # (idea, prior-row title, why it was answered) — printed as a warning, never
    # an automatic skip. Near-duplicate is a judgement call, and a human making
    # it with the prior reason in front of them beats a fuzzy threshold making
    # it silently.
    warnings: list[tuple[Idea, str, str]] = field(default_factory=list)
    # (idea, prior fp, prior title) — a would-be CREATE that looks like a RENAMED
    # version of an existing row (same screen, high title overlap). Skipped by
    # default so a rename never lands as a duplicate; `--allow-similar` records it
    # as new. The fingerprint is a minted identity, frozen at creation — a rename
    # must fold back onto the row it belongs to, not mint a second one.
    renames: list[tuple[Idea, str, str]] = field(default_factory=list)


# Words that carry no signal when comparing two idea titles.
_STOPWORDS = frozenset({
    "a", "add", "an", "and", "as", "at", "by", "for", "from", "in", "into",
    "make", "of", "on", "or", "show", "surface", "the", "to", "use", "with",
})


def _title_tokens(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", title.lower()) if w not in _STOPWORDS and len(w) > 2}


def _similar(a: str, b: str) -> float:
    """Jaccard overlap of the meaningful words in two titles.

    Deliberately crude. The job is to raise a hand so a human reads the prior
    reason, not to decide — so a false positive costs one line of output and a
    false negative costs what it always cost.
    """
    ta, tb = _title_tokens(a), _title_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


# Two titles sharing this fraction of their meaningful words are worth a look.
SIMILARITY_WARN = 0.4

# A stronger bar for calling a would-be-new idea a RENAME of an existing row (as
# opposed to merely reminiscent of an answered one). Paired with same-screen
# equality — a rename keeps the surface and most of the words, so the miss case is
# a human who renamed the Screen too, which is rarer and still caught for answered
# rows by the cross-screen ALREADY-ANSWERED warning above.
RENAME_LIKELY = 0.6


def _find_rename(idea: Idea, existing: dict[str, dict[str, Any]]) -> Optional[tuple[str, dict[str, Any]]]:
    """(fp, row) of the existing row this idea is most likely a rename of, or None.

    A rename is a would-be CREATE (its computed fingerprint matches no stored one)
    whose Screen matches an existing row and whose title overlaps it above
    RENAME_LIKELY. Returns the strongest such match so a re-recorded, renamed idea
    folds back onto its row instead of minting a duplicate.
    """
    screen = (idea.screen or "").strip().lower()
    if not screen:
        # A blank screen is not an anchor — two ideas both missing a screen would
        # collapse on title alone. Without the surface to match on, treat it as new.
        return None
    best: Optional[tuple[str, dict[str, Any]]] = None
    best_score = RENAME_LIKELY
    for fp, prior in existing.items():
        prior_screen = (prior.get("screen") or "").strip().lower()
        if not prior_screen or prior_screen != screen:
            continue
        score = _similar(idea.title, prior.get("title", ""))
        if score >= best_score:
            best, best_score = (fp, prior), score
    return best


def plan_writes(ideas: list[Idea], existing: dict[str, dict[str, Any]],
                allow_similar: bool = False) -> Plan:
    """Classify each input idea as CREATE / UPDATE / SKIP / RENAME-suspect.

    Rules:
    - Fingerprint not in existing → CREATE.
    - Fingerprint exists → UPDATE only the fields that differ AND aren't already
      set on the Notion side (we never clobber human edits). Status and
      Quick status note are owned by Notion; never overwritten.
    - Last seen is bumped only if the input's effective lastSeen is **strictly
      newer** than what Notion already holds. The input's effective lastSeen is
      the explicit `lastSeenAt` from the source, or today if absent. This keeps
      re-runs idempotent: replaying the same source JSON the next day with
      unchanged contents is a no-op, but a genuinely fresh acknowledgement
      (new sim-test session today) bumps the date.
    - `Slack thread` / `Linear issue` only written if blank in Notion AND
      present on the source.
    """
    plan = Plan()
    today = _today_iso()
    seen_fps: set[str] = set()

    for idea in ideas:
        fp = idea.fingerprint()
        # Within one input batch, collapse duplicates to one CREATE.
        if fp in seen_fps:
            plan.skips.append((idea, f"duplicate of earlier input row (fp={fp})"))
            continue
        seen_fps.add(fp)

        if fp not in existing:
            # A minted fingerprint never changes once written, so a rename shows up
            # here as a would-be CREATE whose fingerprint matches no row. Catch it
            # before creating a second row for an idea that already has one.
            rename = None if allow_similar else _find_rename(idea, existing)
            if rename:
                prior_fp, prior = rename
                plan.renames.append((idea, prior_fp, prior.get("title", "")))
                continue
            plan.creates.append(idea)
            # Fingerprint dedup only catches the SAME wording. An idea reworded
            # since the team rejected it hashes differently and sails through —
            # which is how a paywall testimonial came back after aiTestimonials
            # had already lost. Look for near-misses against answered rows too.
            # Compared across EVERY answered row, not just ones on the same
            # screen. Screens get renamed — the paywall rows the team answered in
            # April sit on `paywall_container_screen`, while a run today reports
            # `packagePaywall_screen` for the same surface. A screen-equality
            # guard would have silenced the warning on exactly the case it exists
            # for, which is what testing this against the real backlog showed.
            for prior in existing.values():
                if prior.get("status") not in ANSWERED_STATUSES:
                    continue
                if _similar(idea.title, prior.get("title", "")) >= SIMILARITY_WARN:
                    why = prior.get("reason") or prior.get("status") or "answered"
                    learning = (prior.get("learning") or "").strip()
                    note = (learning.split("Implication:", 1)[-1].strip() if learning
                            else (prior.get("note") or "").strip())
                    plan.warnings.append(
                        (idea, prior.get("title", ""), f"{why}" + (f" — {note[:160]}" if note else ""))
                    )
                    break
            continue

        cur = existing[fp]
        diffs: dict[str, Any] = {}
        effective_last_seen = idea.last_seen or today
        cur_last_seen = cur.get("last_seen") or ""
        # ISO-8601 dates sort lexicographically — string compare is correct.
        if effective_last_seen > cur_last_seen:
            diffs["Last seen"] = make_date(effective_last_seen)
        if idea.slack_thread and not cur.get("slack_thread"):
            diffs["Slack thread"] = {"url": idea.slack_thread}
        if idea.linear_issue and not cur.get("linear_issue"):
            diffs["Linear issue"] = {"url": idea.linear_issue}

        if diffs:
            plan.updates.append((idea, diffs))
        else:
            plan.skips.append((idea, "exists, already in sync"))

    return plan


def render_plan(plan: Plan) -> str:
    lines: list[str] = []
    if plan.renames:
        lines.append("LIKELY RENAME — skipped so a renamed idea does not land as a duplicate.")
        lines.append("  If genuinely new, re-run with --allow-similar. If it IS the same idea,")
        lines.append("  edit the existing row rather than recording this one.")
        for idea, prior_fp, prior_title in plan.renames:
            lines.append(f"  ~ {idea.title[:64]}")
            lines.append(f"      looks like a rename of `{prior_fp}`: {prior_title[:64]}")
        lines.append("")
    if plan.warnings:
        lines.append("ALREADY ANSWERED — read these before applying:")
        for idea, prior_title, why in plan.warnings:
            lines.append(f"  ! {idea.title[:64]}")
            lines.append(f"      looks like: {prior_title[:64]}")
            lines.append(f"      {why}")
        lines.append("")
    total = len(plan.creates) + len(plan.updates) + len(plan.skips)
    for i, idea in enumerate(plan.creates, 1):
        lines.append(f"[{i:>3}/{total}] CREATE  fp={idea.fingerprint()}  {idea.title[:70]}")
    base = len(plan.creates)
    for i, (idea, diffs) in enumerate(plan.updates, 1):
        keys = ",".join(diffs.keys())
        lines.append(
            f"[{base + i:>3}/{total}] UPDATE  fp={idea.fingerprint()}  ({keys})  {idea.title[:60]}"
        )
    base += len(plan.updates)
    for i, (idea, reason) in enumerate(plan.skips, 1):
        lines.append(
            f"[{base + i:>3}/{total}] SKIP    fp={idea.fingerprint()}  ({reason})  {idea.title[:60]}"
        )
    lines.append("")
    summary = f"Plan: {len(plan.creates)} CREATE, {len(plan.updates)} UPDATE, {len(plan.skips)} SKIP"
    if plan.renames:
        summary += f", {len(plan.renames)} RENAME-suspect (not recorded)"
    lines.append(summary)
    return "\n".join(lines)


# ---------- Notion write -----------------------------------------------------

def _build_props(idea: Idea) -> dict[str, Any]:
    """Properties payload for a CREATE.

    Title is keyed by the lowercase property ID 'title' under the
    2025-09-03 API when the parent is a data_source_id — see notion_rest.py
    `make_title` docstring.
    """
    today = _today_iso()
    first_seen = idea.first_seen or today
    last_seen = idea.last_seen or today
    props: dict[str, Any] = {
        "title": make_title(idea.title),
        "Status": make_select(idea.status),
        "Fingerprint": make_rich_text(idea.fingerprint()),
        "First seen": make_date(first_seen),
        "Last seen": make_date(last_seen),
    }
    if idea.description:
        props["Description"] = make_rich_text(idea.description)
    if idea.rationale:
        props["Rationale"] = make_rich_text(idea.rationale)
    if idea.hypothesis:
        props["Hypothesis"] = make_rich_text(idea.hypothesis)
    if idea.expected_impact:
        props["Expected impact"] = make_rich_text(idea.expected_impact)
    if idea.prior_art:
        props["Prior art"] = make_rich_text(idea.prior_art)
    if idea.scope:
        props["Scope"] = make_select(idea.scope)
    for value, name in ((idea.proposed_by, "Proposed by"), (idea.portfolio, "Portfolio"),
                        (idea.bet_size, "Bet size"), (idea.confidence, "Confidence"),
                        (idea.evidence_grade, "Evidence grade")):
        if value:
            props[name] = make_select(value)
    if idea.variants:
        props["Variants"] = make_rich_text(idea.variants)
    if idea.test_plan:
        props["Test plan"] = make_rich_text(idea.test_plan)
    if idea.screen:
        props["Screen"] = make_rich_text(idea.screen)
    if idea.domain:
        props["Domain"] = make_select(idea.domain)
    if idea.category:
        props["Category"] = make_rich_text(idea.category)
    if idea.lever:
        props["Lever"] = make_rich_text(idea.lever)
    if idea.impact:
        props["Impact"] = make_select(idea.impact)
    if idea.effort:
        props["Effort"] = make_select(idea.effort)
    if idea.status_note:
        props["Quick status note"] = make_rich_text(idea.status_note)
    if idea.slack_thread:
        props["Slack thread"] = {"url": idea.slack_thread}
    if idea.linear_issue:
        props["Linear issue"] = {"url": idea.linear_issue}
    return props


def upload_screenshot(path: str) -> Optional[dict[str, Any]]:
    """Upload a local image to Notion and return the `files` property value.

    Notion's file upload is three steps and only the first two happen here: create
    the upload to get an id and a URL, then POST the bytes to that URL. The third
    step is attaching the id to a page property, which the caller does as part of
    the page create so the screenshot lands atomically with the row.

    Returns None on any failure. A screenshot is enrichment — losing it must never
    cost the idea itself, which is the thing worth recording.
    """
    try:
        import requests  # noqa: PLC0415 — optional at import time, like notion_rest
    except ImportError:
        print("  ! requests unavailable, skipping screenshot upload", file=sys.stderr)
        return None

    name = os.path.basename(path)
    ext = os.path.splitext(name)[1].lower()
    content_type = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                    ".gif": "image/gif", ".webp": "image/webp"}.get(ext)
    if not content_type:
        print(f"  ! {name}: unsupported image type, skipping upload", file=sys.stderr)
        return None

    headers = {
        "Authorization": f"Bearer {os.environ.get('NOTION_TOKEN', '')}",
        "Notion-Version": NOTION_FILE_API_VERSION,
    }
    try:
        created = requests.post(
            "https://api.notion.com/v1/file_uploads",
            headers={**headers, "Content-Type": "application/json"},
            json={"filename": name, "content_type": content_type},
            timeout=30,
        )
        created.raise_for_status()
        upload = created.json()
        with open(path, "rb") as fh:
            sent = requests.post(
                upload["upload_url"], headers=headers,
                files={"file": (name, fh, content_type)}, timeout=120,
            )
        sent.raise_for_status()
    except Exception as exc:  # noqa: BLE001 — never let enrichment fail the write
        print(f"  ! {name}: screenshot upload failed ({exc}); recording the idea without it",
              file=sys.stderr)
        return None

    return {"files": [{"type": "file_upload", "name": name,
                       "file_upload": {"id": upload["id"]}}]}


_SCHEMA_CACHE: dict[str, set[str]] = {}


def data_source_properties(ds_id: str) -> set[str]:
    """The property names the target database actually has — fetched once per run.

    Every loop database is meant to carry the same schema, and they drift: `Scope` and
    `Expected impact` were added to the Aurie database first. Notion refuses a page
    create that names a property the database lacks, and unlike a screenshot upload that
    is not enrichment — the whole idea fails to land. So a property the target does not
    have is dropped with a warning rather than sent. Empty on any error, which prunes
    nothing: the write then behaves exactly as it did before this guard existed.
    """
    if ds_id not in _SCHEMA_CACHE:
        try:
            resp = notion._request("GET", f"/data_sources/{ds_id}")  # noqa: SLF001
            props = resp.get("properties") or {}
            # A write may key a property by its NAME ("Status") or by its ID — and the
            # create payload keys the title property by ID, `title`, while the schema
            # names it "Title". Comparing against names alone stripped the title off
            # every create. Both spellings are the property, so both are allowed.
            _SCHEMA_CACHE[ds_id] = set(props.keys()) | {v.get("id") for v in props.values() if v.get("id")}
        except Exception as e:  # noqa: BLE001 — a schema read must never block a write
            print(f"  ! could not read the schema of {ds_id} ({e}); writing every property",
                  file=sys.stderr)
            _SCHEMA_CACHE[ds_id] = set()
    return _SCHEMA_CACHE[ds_id]


def prune_to_schema(ds_id: str, props: dict[str, Any], label: str = "") -> dict[str, Any]:
    """Drop the properties the target database does not have, saying which."""
    have = data_source_properties(ds_id)
    if not have:
        return props
    missing = [k for k in props if k not in have]
    for k in missing:
        print(f"  ! {label or 'row'}: database has no property {k!r} — not written. "
              f"Add it to the database to keep it.", file=sys.stderr)
    return {k: v for k, v in props.items() if k in have}


def create_page(ds_id: str, idea: Idea) -> tuple[str, str]:
    """POST /pages with parent.data_source_id. Returns (page_id, idea_id).

    The screenshot is uploaded first and attached in the same create, so a row
    never exists briefly without the screen it was observed on. The `idea_id`
    (e.g. 'IDEA-42') is minted by Notion on creation and read back off the
    response — it is the row's immutable, human-friendly address.
    """
    props = prune_to_schema(ds_id, _build_props(idea), idea.title[:40])
    if idea.screenshot_path:
        shot = upload_screenshot(idea.screenshot_path)
        if shot:
            props["Screenshot"] = shot
    body = {
        "parent": {"type": "data_source_id", "data_source_id": ds_id},
        "properties": props,
    }
    resp = notion._request("POST", "/pages", data=json.dumps(body))  # noqa: SLF001
    return resp["id"], idea_id_str(resp)


def apply_plan(plan: Plan, ds_id: str) -> dict[str, int]:
    """Execute creates and updates. Returns a counts summary."""
    counts = {"created": 0, "updated": 0, "skipped": len(plan.skips),
              "renamed": len(plan.renames), "failed": 0}
    for idea in plan.creates:
        try:
            page_id, idea_id = create_page(ds_id, idea)
            addr = idea_id or idea.fingerprint()
            print(f"  + created {addr} {idea.title[:60]} -> {page_id}")
            counts["created"] += 1
        except Exception as e:  # noqa: BLE001
            print(f"  ! CREATE failed for '{idea.title[:60]}': {e}", file=sys.stderr)
            counts["failed"] += 1
    for idea, diffs in plan.updates:
        # Need the page_id from the existing-row map; planner stored it on the
        # update tuple via the dict it returned, but we use existing-lookup
        # here for clarity.
        existing = load_existing(ds_id)
        cur = existing.get(idea.fingerprint())
        if not cur:
            print(
                f"  ! UPDATE skipped — row disappeared since planning: '{idea.title[:60]}'",
                file=sys.stderr,
            )
            counts["failed"] += 1
            continue
        try:
            diffs = prune_to_schema(ds_id, diffs, idea.title[:40])
            if not diffs:
                print(f"  ~ nothing the database can hold for {idea.fingerprint()}")
                continue
            notion.update_page_props(cur["page_id"], diffs)
            print(f"  ~ updated {idea.fingerprint()} ({','.join(diffs.keys())})")
            counts["updated"] += 1
        except Exception as e:  # noqa: BLE001
            print(f"  ! UPDATE failed for '{idea.title[:60]}': {e}", file=sys.stderr)
            counts["failed"] += 1
    return counts


# ---------- subcommands ------------------------------------------------------

def cmd_plan(args: argparse.Namespace) -> int:
    if not notion.available:
        raise MCPFallback(
            "NOTION_TOKEN is not set — record-ideas needs REST access to dedup. "
            "Add NOTION_TOKEN to ~/.claude/settings.json env block (see AINative "
            "CLAUDE.md → 'Notion REST integration')."
        )
    ideas = load_ideas(args)
    existing = load_existing(args.data_source_id)
    plan = plan_writes(ideas, existing, allow_similar=args.allow_similar)
    print(render_plan(plan))
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    if not notion.available:
        raise MCPFallback("NOTION_TOKEN is not set — apply requires REST writes.")
    ideas = load_ideas(args)
    existing = load_existing(args.data_source_id)
    plan = plan_writes(ideas, existing, allow_similar=args.allow_similar)
    print(render_plan(plan))
    if not args.confirm:
        print("\n--- not applying. Re-run with --confirm to write to Notion.")
        return 0
    print("\nApplying...")
    counts = apply_plan(plan, args.data_source_id)
    print(
        f"\nResult: {counts['created']} created, "
        f"{counts['updated']} updated, "
        f"{counts['skipped']} skipped, "
        f"{counts['renamed']} rename-suspect (not recorded), "
        f"{counts['failed']} failed"
    )
    return 1 if counts["failed"] else 0


def filter_rows(rows, proposed_by: str = "", since: str = "") -> list[dict[str, Any]]:
    """Rows from one ideation route and/or first seen on or after a date.

    This is how a route counts what it already recorded this week against its cap.
    """
    out = list(rows)
    if proposed_by:
        out = [r for r in out if (r.get("proposed_by") or "").lower() == proposed_by.lower()]
    if since:
        out = [r for r in out if (r.get("first_seen") or "") >= since]
    return out


def cmd_list(args: argparse.Namespace) -> int:
    if not notion.available:
        raise MCPFallback("NOTION_TOKEN is not set — list requires REST access.")
    existing = load_existing(args.data_source_id)
    rows = sorted(
        filter_rows(existing.values(), args.proposed_by, args.since),
        key=lambda r: (r["status"] or "", r["title"] or ""),
    )
    for r in rows:
        print(f"{(r['status'] or '?'):<9} {r['title'][:80]}")
    print(f"\n{len(rows)} rows in database")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="record_ideas",
        description="Record product ideas into the Notion AI Knowledge teamspace.",
    )
    p.add_argument(
        "--data-source-id",
        default=AURIE_FEATURES_DS,
        help="Notion data source ID (default: Aurie Features Ideas)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_input_flags(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--from-json", help="Path to a JSON array of ideas")
        sp.add_argument("--from-stdin", action="store_true", help="Read JSON array from stdin")
        sp.add_argument("--idea", help="Inline single idea as JSON string")
        sp.add_argument(
            "--allow-similar",
            action="store_true",
            help="Record an idea even when it looks like a rename of an existing row "
                 "(same screen, high title overlap). Off by default so a renamed idea "
                 "folds back onto its row instead of minting a duplicate.",
        )

    sp_plan = sub.add_parser("plan", help="Compute and print a CREATE/UPDATE/SKIP plan")
    add_input_flags(sp_plan)
    sp_plan.set_defaults(func=cmd_plan)

    sp_apply = sub.add_parser("apply", help="Compute the plan and write it to Notion")
    add_input_flags(sp_apply)
    sp_apply.add_argument(
        "--confirm",
        action="store_true",
        help="Required to actually write — otherwise this is a dry-run",
    )
    sp_apply.set_defaults(func=cmd_apply)

    sp_list = sub.add_parser("list", help="Print the rows in the database")
    sp_list.add_argument("--proposed-by", default="",
                         help="Only rows whose `Proposed by` matches (e.g. claude, viktor)")
    sp_list.add_argument("--since", default="",
                         help="Only rows first seen on or after this ISO date")
    sp_list.set_defaults(func=cmd_list)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except MCPFallback as e:
        print(f"record-ideas: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
