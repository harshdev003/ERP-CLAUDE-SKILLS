#!/usr/bin/env python3
"""Record prompt-change candidates into the Aurie Conversations Notion database.

A *candidate* is a proposed change to one named prompter, carrying a defect, a
diagnosis and (once drafted) a diff. It is the Conversations loop's unit of work,
the way an *idea* is the Features loop's.

Design is deliberately parallel to ``record-ideas`` — plan/apply split, a frozen
content fingerprint for dedup, and rename folding so a retitled candidate lands
back on its own row instead of minting a duplicate. The fields are not parallel:
a candidate has a Prompter, a Change class, a Defect, a Diagnosis and a named
Risk, where an idea has a Screen, a Domain and a Hypothesis.

Self-contained per AINative principle 1: ``notion_rest.py`` is a per-skill copy of
the canonical at ``plugins/shared/skills/retrieve-knowledge/scripts/``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Any, Optional

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import notion_rest  # noqa: E402
import register  # noqa: E402

# The Aurie Conversations database. Other loops pass --data-source-id.
AURIE_CONVERSATIONS_DS = "3e375e23-af20-81d5-b99a-000b1b861edc"

VALID_PROMPTERS = {
    "coach", "companion", "onboarding", "quickProcessing", "quickProcessingDone",
    "quickProcessingSentinel", "guardrailsHandler", "userGuardrail",
    "assistantGuardrail", "chat", "other",
}
VALID_CLASSES = {"safety", "correctness", "behavioural", "model/config"}
VALID_STATUSES = {
    "proposed", "shared", "approved", "rejected", "deferred", "drafted",
    "evaluated", "held", "in_review", "merged", "released", "experiment_running",
    "won", "lost", "inconclusive", "rolled_back", "abandoned",
}
VALID_DIAGNOSES = {"local", "structural", "not yet diagnosed"}
VALID_REJECTION_REASONS = {
    "already tried", "registered hard stop", "low impact", "off-strategy",
    "model not prompt", "timing", "duplicate", "other",
}
VALID_VERDICTS = {"won", "lost", "inconclusive", "rolled back"}

# Statuses that mean the loop has ANSWERED this question. A new candidate whose
# title is similar to one of these is surfaced by `plan` before it is written --
# the same guard record-ideas applies, for the same reason: a reworded repeat of
# something the team already rejected hashes differently and sails through.
ANSWERED_STATUSES = {"rejected", "lost", "abandoned", "rolled_back"}

# Parked, not answered. Re-proposing one of these is legitimate -- the blocker may
# have cleared -- but it must be a deliberate revival of THAT row rather than a
# silent duplicate, so `plan` surfaces it and `apply` still proceeds.
PARKED_STATUSES = {"deferred", "held"}

# Statuses a human decided. Re-recording never overwrites one.
HUMAN_DECIDED = {
    "approved", "rejected", "deferred", "held", "won", "lost", "inconclusive",
    "abandoned", "rolled_back",
}


@dataclass
class Candidate:
    """One proposed prompt change. Only `title` and `prompter` are required."""

    title: str = ""
    prompter: str = ""
    change_class: str = ""
    status: str = "proposed"
    status_note: str = ""
    defect: str = ""
    evidence: str = ""
    diagnosis: str = ""
    cause: str = ""
    risk_named: str = ""
    proposed_diff: str = ""
    gate_results: str = ""
    braintrust_prompt: str = ""
    braintrust_version: str = ""
    rejection_reason: str = ""
    slack_thread: str = ""
    linear_issue: str = ""
    pr: str = ""
    release_version: str = ""
    experiment_name: str = ""
    verdict: str = ""
    first_seen: str = ""
    last_seen: str = ""

    def fingerprint(self) -> str:
        """Dedup key, frozen at creation -- NOT the row's address.

        Hashes ``prompter|change_class|title`` so the same candidate arriving from
        the digest, a Slack thread and a hand-written note collapses onto one row.

        The address is the immutable ``Candidate ID`` (CONV-42) that Notion mints.
        Never recompute this to look a row up: the inputs are editable, so a
        fingerprint derived from a renamed title resolves to nothing. A rename is
        detected on re-record instead (see ``_find_rename``).
        """
        key = f"{self.prompter}|{self.change_class}|{self.title.strip().lower()}"
        return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]

    def validate(self) -> list[str]:
        errs: list[str] = []
        if not self.title.strip():
            errs.append("title is required")
        if not self.prompter.strip():
            errs.append("prompter is required")
        for value, valid, label in (
            (self.prompter, VALID_PROMPTERS, "prompter"),
            (self.change_class, VALID_CLASSES, "changeClass"),
            (self.status, VALID_STATUSES, "status"),
            (self.diagnosis, VALID_DIAGNOSES, "diagnosis"),
            (self.rejection_reason, VALID_REJECTION_REASONS, "rejectionReason"),
            (self.verdict, VALID_VERDICTS, "verdict"),
        ):
            if value and value not in valid:
                errs.append(f"{label} '{value}' not in {sorted(valid)}")
        # A safety-class candidate with no diagnosis is the shape the loop most
        # wants to refuse: it routes to the strictest gate and the most senior
        # reviewer, so arriving without a stated cause wastes both.
        if self.change_class == "safety" and not self.diagnosis:
            errs.append("a safety-class candidate needs a diagnosis")
        return errs


def _coerce(raw: dict[str, Any]) -> Candidate:
    """Accept camelCase (the digest's output) or snake_case (hand-written)."""

    def pick(*names: str) -> str:
        for n in names:
            v = raw.get(n)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return ""

    return Candidate(
        title=pick("title"),
        prompter=pick("prompter"),
        change_class=pick("changeClass", "change_class", "class"),
        status=pick("status") or "proposed",
        status_note=pick("statusNote", "status_note"),
        defect=pick("defect"),
        evidence=pick("evidence"),
        diagnosis=pick("diagnosis"),
        cause=pick("cause"),
        risk_named=pick("riskNamed", "risk_named", "risk"),
        proposed_diff=pick("proposedDiff", "proposed_diff", "diff"),
        gate_results=pick("gateResults", "gate_results"),
        braintrust_prompt=pick("braintrustPrompt", "braintrust_prompt"),
        braintrust_version=pick("braintrustVersion", "braintrust_version"),
        rejection_reason=pick("rejectionReason", "rejection_reason"),
        slack_thread=pick("slackThread", "slack_thread"),
        linear_issue=pick("linearIssue", "linear_issue"),
        pr=pick("pr", "prUrl"),
        release_version=pick("releaseVersion", "release_version"),
        experiment_name=pick("experimentName", "experiment_name"),
        verdict=pick("verdict"),
        first_seen=_date_only(pick("firstSeen", "first_seen", "firstSeenAt")),
        last_seen=_date_only(pick("lastSeen", "last_seen", "lastSeenAt")),
    )


def _date_only(s: str) -> str:
    return s[:10] if s else ""


def _today() -> str:
    return date.today().isoformat()


def load_candidates(args: argparse.Namespace) -> list[Candidate]:
    """Read candidates from --from-json, --from-stdin or --candidate."""
    if args.from_stdin:
        raw = json.load(sys.stdin)
    elif getattr(args, "from_json", None):
        with open(args.from_json, encoding="utf-8") as fh:
            raw = json.load(fh)
    elif getattr(args, "candidate", None):
        raw = json.loads(args.candidate)
    else:
        raise SystemExit("one of --from-json / --from-stdin / --candidate is required")

    if isinstance(raw, dict):
        raw = raw.get("candidates", [raw])
    if not isinstance(raw, list):
        raise SystemExit("input must be a JSON array of candidates")

    out, errors = [], []
    for i, item in enumerate(raw):
        c = _coerce(item)
        errs = c.validate()
        if errs:
            errors.append(f"  [{i}] {c.title or '(untitled)'}: {'; '.join(errs)}")
        out.append(c)
    if errors:
        # Abort the batch: a bad enum silently creates a new Notion select option
        # and the row lands somewhere nobody filters on.
        raise SystemExit("Validation failed:\n" + "\n".join(errors))
    return out


# ---- reading what is already there ---------------------------------------

def load_existing(ds_id: str) -> dict[str, dict[str, Any]]:
    """Map fingerprint -> row for everything currently in the database."""
    rows = {}
    for row in notion_rest.notion.query_data_source(ds_id):
        fp = notion_rest.rich_text_value(row, "Fingerprint")
        if fp:
            rows[fp] = row
    return rows


def candidate_id_str(row: dict[str, Any]) -> str:
    uid = (row.get("properties") or {}).get("Candidate ID", {}).get("unique_id") or {}
    prefix, number = uid.get("prefix"), uid.get("number")
    return f"{prefix}-{number}" if prefix and number is not None else "(no id)"


_SUFFIXES = ("ing", "ies", "es", "ed", "s", "e")


def _stem(token: str) -> str:
    """Crudest possible stemmer, and deliberately so.

    It exists to stop "update" and "updating" counting as different words, which
    is enough to make near-duplicate titles score as similar. Anything cleverer
    would need a dependency, and the cost of a miss here is only a warning that
    does not print.
    """
    for suffix in _SUFFIXES:
        if len(token) > len(suffix) + 2 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def _title_tokens(title: str) -> set[str]:
    return {_stem(t) for t in "".join(
        ch.lower() if ch.isalnum() else " " for ch in title
    ).split() if len(t) > 2}


def _similar(a: str, b: str) -> float:
    ta, tb = _title_tokens(a), _title_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _find_rename(c: Candidate, existing: dict[str, dict[str, Any]]) -> Optional[tuple[str, dict]]:
    """Find a row that is this candidate under an old title.

    Same prompter and class, high title overlap, and no exact fingerprint match.
    Folds the update back onto that row rather than minting a duplicate.
    """
    best: Optional[tuple[str, dict]] = None
    best_score = 0.0
    for fp, row in existing.items():
        if notion_rest.select_value(row, "Prompter") != c.prompter:
            continue
        if notion_rest.select_value(row, "Change class") != c.change_class:
            continue
        score = _similar(c.title, notion_rest.title_text(row, "Title"))
        if score >= 0.6 and score > best_score:
            best, best_score = (fp, row), score
    return best


def find_similar(c: Candidate, existing: dict[str, dict[str, Any]]) -> list[tuple[str, str, str, str]]:
    """Every row that resembles this candidate, whatever its status.

    Returns (status, candidate_id, title, reason). Status drives how the caller
    treats it: an ANSWERED row refuses the apply, a PARKED or open one only warns.
    Reporting all three matters because the duplicate that actually costs you is
    usually a row somebody opened last week, not one the team settled months ago.
    """
    own = c.fingerprint()
    hits = []
    for fp, row in existing.items():
        # Never flag the candidate against itself. Without this, re-recording an
        # answered candidate puts its own row in the answered group and `apply`
        # refuses the whole batch -- including every unrelated new row in it.
        if fp == own:
            continue
        title = notion_rest.title_text(row, "Title")
        if _similar(c.title, title) < 0.4:
            continue
        status = notion_rest.select_value(row, "Status") or "unknown"
        reason = (notion_rest.select_value(row, "Rejection reason")
                  or notion_rest.rich_text_value(row, "Quick status note")
                  or "")
        hits.append((status, candidate_id_str(row), title, reason))
    return hits


# ---- planning -------------------------------------------------------------

@dataclass
class Plan:
    creates: list[Candidate] = field(default_factory=list)
    updates: list[tuple[Candidate, dict]] = field(default_factory=list)
    renames: list[tuple[Candidate, dict]] = field(default_factory=list)
    skips: list[tuple[Candidate, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    parked: list[str] = field(default_factory=list)
    refusals: list[tuple[Candidate, str, dict, str]] = field(default_factory=list)


def _registered(c: Candidate) -> list:
    """Register entries this candidate restates, over everything it proposes.

    Read across the whole candidate rather than the title alone: the title is a
    label and the hypothesis usually lives in the diagnosis or the diff.
    """
    text = " ".join(x for x in (c.title, c.defect, c.diagnosis, c.cause,
                                c.proposed_diff, c.status_note) if x)
    return register.refusals(text, c.prompter or None)


def plan_writes(candidates: list[Candidate], existing: dict[str, dict[str, Any]]) -> Plan:
    plan = Plan()
    # Fingerprints already accounted for by THIS batch. Two identical rows in one
    # input would otherwise both land in `creates`, inserting two Notion pages
    # that share a Fingerprint -- after which `load_existing` keeps only one and
    # every later update silently misses the other.
    seen_in_batch: set[str] = set()
    for c in candidates:
        if not c.first_seen:
            c.first_seen = _today()
        c.last_seen = _today()

        for status, cid, title, reason in find_similar(c, existing):
            line = f"{cid} [{status}] {title}"
            if reason:
                line += f"\n      {reason}"
            if status in ANSWERED_STATUSES:
                plan.warnings.append(line)
            else:
                plan.parked.append(line)

        fp = c.fingerprint()
        if fp in seen_in_batch:
            plan.skips.append((c, "duplicate of an earlier row in this same input"))
            continue
        hits = _registered(c)
        if hits:
            for eid, entry, matched in hits:
                plan.refusals.append((c, eid, entry, matched))
            continue
        seen_in_batch.add(fp)
        if fp in existing:
            row = existing[fp]
            status = notion_rest.select_value(row, "Status")
            if status in HUMAN_DECIDED:
                plan.skips.append((c, f"{candidate_id_str(row)} is {status} — a human decided it"))
            else:
                plan.updates.append((c, row))
            continue

        renamed = _find_rename(c, existing)
        if renamed:
            _, row = renamed
            status = notion_rest.select_value(row, "Status")
            if status in HUMAN_DECIDED:
                plan.skips.append((c, f"{candidate_id_str(row)} is {status} — a human decided it"))
            else:
                plan.renames.append((c, row))
            continue

        plan.creates.append(c)
    return plan


def render_plan(plan: Plan) -> str:
    out = []
    if plan.refusals:
        out.append("REFUSED — the register has already answered this:")
        for c, eid, entry, matched in plan.refusals:
            out.append(f'  x {c.title}')
            out.append(f'      {eid} [{entry["verdict"]}] {entry["title"]}')
            out.append(f'      matched "{matched}"')
            out.append(f'      {entry["finding"]}')
            if entry.get("instead"):
                out.append(f'      instead: {entry["instead"]}')
            out.append(f'      reopens: {entry["reopens_if"]}')
        out.append("")
    if plan.warnings:
        out.append("ALREADY ANSWERED — read these before applying:")
        for w in plan.warnings:
            out.append(f"  ! {w}")
        out.append("")
    if plan.parked:
        out.append("SIMILAR — an open or parked candidate already covers this:")
        for w in plan.parked:
            out.append(f"  ~ {w}")
        out.append("")
    out.append(f"create  {len(plan.creates)}")
    for c in plan.creates:
        out.append(f"  + [{c.prompter}/{c.change_class or 'unclassified'}] {c.title}")
    out.append(f"update  {len(plan.updates)}")
    for c, row in plan.updates:
        out.append(f"  ~ {candidate_id_str(row)} {c.title}")
    out.append(f"rename  {len(plan.renames)}")
    for c, row in plan.renames:
        out.append(f"  > {candidate_id_str(row)} {notion_rest.title_text(row, 'Title')}")
        out.append(f"      -> {c.title}")
    out.append(f"skip    {len(plan.skips)}")
    for c, why in plan.skips:
        out.append(f"  = {c.title}  ({why})")
    return "\n".join(out)


# ---- writing --------------------------------------------------------------

def _props(c: Candidate, *, creating: bool) -> dict[str, Any]:
    """Notion properties for this candidate.

    On update we deliberately do NOT write Status, Quick status note, Rejection
    reason, Decided by/at or Verdict: those are the decision record, and
    automation never overwrites a human decision.
    """
    # On a create the title property must be keyed "title" (lowercase) --
    # the 2025-09-03 API resolves it by id when the parent is a data_source_id.
    # On a PATCH the display name is correct. Getting this backwards fails
    # silently-ish: the row lands with an empty title.
    p: dict[str, Any] = {
        ("title" if creating else "Title"): notion_rest.make_title(c.title),
        "Last seen": notion_rest.make_date(c.last_seen or _today()),
    }
    simple = [
        ("Defect", c.defect), ("Evidence", c.evidence), ("Cause", c.cause),
        ("Risk named", c.risk_named), ("Proposed diff", c.proposed_diff),
        ("Gate results", c.gate_results), ("Braintrust version", c.braintrust_version),
        ("Release version", c.release_version), ("Experiment name", c.experiment_name),
    ]
    for name, value in simple:
        if value:
            p[name] = notion_rest.make_rich_text(value)
    for name, value in (("Prompter", c.prompter), ("Change class", c.change_class),
                        ("Diagnosis", c.diagnosis)):
        if value:
            p[name] = notion_rest.make_select(value)
    for name, value in (("Slack thread", c.slack_thread), ("Linear issue", c.linear_issue),
                        ("PR", c.pr), ("Braintrust prompt", c.braintrust_prompt)):
        if value:
            p[name] = {"url": value}

    if creating:
        p["Fingerprint"] = notion_rest.make_rich_text(c.fingerprint())
        p["First seen"] = notion_rest.make_date(c.first_seen or _today())
        p["Status"] = notion_rest.make_select(c.status or "proposed")
        if c.status_note:
            p["Quick status note"] = notion_rest.make_rich_text(c.status_note)
        if c.rejection_reason:
            p["Rejection reason"] = notion_rest.make_select(c.rejection_reason)
        if c.verdict:
            p["Verdict"] = notion_rest.make_select(c.verdict)
    return p


def apply_plan(plan: Plan, ds_id: str) -> dict[str, int]:
    client = notion_rest.notion
    counts = {"created": 0, "updated": 0, "renamed": 0, "skipped": len(plan.skips)}

    for c in plan.creates:
        client.create_database_row(ds_id, _props(c, creating=True))
        counts["created"] += 1
        print(f"  created  {c.title}")
    for c, row in plan.updates:
        client.update_page_props(row["id"], _props(c, creating=False))
        counts["updated"] += 1
        print(f"  updated  {candidate_id_str(row)} {c.title}")
    for c, row in plan.renames:
        client.update_page_props(row["id"], _props(c, creating=False))
        counts["renamed"] += 1
        print(f"  renamed  {candidate_id_str(row)} -> {c.title}")
    return counts


# ---- commands -------------------------------------------------------------

def cmd_plan(args: argparse.Namespace) -> int:
    candidates = load_candidates(args)
    existing = load_existing(args.data_source_id)
    print(render_plan(plan_writes(candidates, existing)))
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    candidates = load_candidates(args)
    existing = load_existing(args.data_source_id)
    plan = plan_writes(candidates, existing)
    print(render_plan(plan))
    if plan.refusals:
        print("\nRefusing to apply: a candidate restates a registered hard stop.")
        print("This is not overridable by --force. The register is measurement, not")
        print("opinion, and the documented way past it is the `reopens` condition on")
        print("the entry — meet it and retire the entry in product:prompt-knowledge,")
        print("or rewrite the candidate so it proposes something not already answered.")
        return 1
    if plan.warnings and not args.force:
        print("\nRefusing to apply: this resembles something already answered.")
        print("Read the rows above. Re-run with --force if it is genuinely different,")
        print("and say what is different in statusNote.")
        return 1
    if not args.confirm:
        print("\n--- not applying. Re-run with --confirm to write to Notion.")
        return 0
    print("\napplying...")
    counts = apply_plan(plan, args.data_source_id)
    print(f"\n{counts}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    existing = load_existing(args.data_source_id)
    rows = sorted(existing.values(),
                  key=lambda r: (r.get("properties", {})
                                 .get("Candidate ID", {})
                                 .get("unique_id", {}) or {}).get("number") or 0)
    for row in rows:
        status = notion_rest.select_value(row, "Status") or "-"
        if args.status and status != args.status:
            continue
        print(f"  {candidate_id_str(row):<8} {status:<18} "
              f"{notion_rest.select_value(row, 'Prompter') or '-':<24} "
              f"{notion_rest.title_text(row, 'Title')}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--data-source-id", default=AURIE_CONVERSATIONS_DS,
                   help="target loop database (default: Aurie Conversations Ideas)")
    sub = p.add_subparsers(dest="cmd", required=True)

    for name, fn in (("plan", cmd_plan), ("apply", cmd_apply)):
        sp = sub.add_parser(name)
        sp.add_argument("--from-json")
        sp.add_argument("--from-stdin", action="store_true")
        sp.add_argument("--candidate", help="a single candidate as an inline JSON object")
        if name == "apply":
            sp.add_argument("--confirm", action="store_true",
                            help="required to actually write — otherwise this is a dry run")
            sp.add_argument("--force", action="store_true",
                            help="apply even though a similar question was already answered")
        sp.set_defaults(func=fn)

    sp_list = sub.add_parser("list")
    sp_list.add_argument("--status")
    sp_list.set_defaults(func=cmd_list)
    return p


def main(argv: Optional[list[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
