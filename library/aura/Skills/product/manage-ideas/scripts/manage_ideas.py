"""Manage rows in the `Learnings/Loop/<Loop>` Notion database.

Day-to-day operations on the backlog: list / filter / show, plus the two
curation verbs (``approve`` / ``reject``) that flip ``Status`` and write a
reviewer note.

The clustering / duplicate-detection / review pass is **Claude-orchestrated**
via SKILL.md — this script is the substrate. It exposes only deterministic
read + single-row write subcommands; Claude is the judgment layer.

Subcommands
-----------
    list                       Print rows, optionally filtered + sorted.
    show <ref>                 Print one row in full. ``ref`` is a page UUID,
                               a fingerprint, or a title substring.
    approve <ref> --note "..."  Flip Status -> approved, write Quick status note.
    reject  <ref> --reason X    Flip Status -> rejected, write Rejection reason.
    set-status <ref> <status> --note "..."   General-purpose status writer
                               (e.g. flipping released manually).

Deciding a row writes four properties, not one. ``Status`` is the outcome,
``Quick status note`` the prose, and the two the loop actually learns from:
``Rejection reason`` (a fixed vocabulary, so the next ideation run can filter
on it) and ``Decided by`` / ``Decided at`` (so a verdict is attributable months
later). ``--reason`` is REQUIRED on ``rejected`` and ``deferred`` — a rejection
with no recorded reason teaches the loop nothing, which is the failure this
database exists to prevent.

``ref`` resolution is permissive: try as page UUID first, then exact
fingerprint, then case-insensitive title contains. Ambiguous title matches
fail loudly with the candidate list so the user disambiguates.
"""

from __future__ import annotations

import argparse
import datetime as dt
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
from thread_marks import MARKS, mark_thread  # noqa: E402


AURIE_FEATURES_DS = "8d14f3eb-0c55-4f25-9322-ab2b2a69f092"

# Mirrors the Loop lifecycle in record_ideas.py — keep the two in step.
VALID_STATUSES = {
    # Proposal → review
    "pending", "shared", "approved", "rejected", "deferred",
    # Build → release
    "implementing", "shipped_dormant",
    # Measurement
    "experiment_running", "won", "lost", "inconclusive", "abandoned",
}

# Mirrors the `Rejection reason` select options in Notion. A fixed vocabulary
# rather than free text because the ideation pass filters on it — "already
# tested" and "no longer valid" retire an idea permanently, while "timing" and
# "tech cost" mean re-propose later. The prose goes in Quick status note.
# The Domain select's options, and the same set `record-ideas` validates against.
# Copied for the same reason its siblings are: skills are self-contained. It must
# stay identical to the Notion select — a value not in the select is rejected by
# the API, and one missing from here is refused before it gets there.
VALID_DOMAINS = {
    "onboarding", "paywall", "home/feed", "conversation", "journal", "grounding",
    "affirmations", "profile", "settings", "notifications", "growth/referral", "other",
}

# Screen -> Domain, first match wins. A deliberate copy of `DOMAIN_PATTERNS` in
# AuraConsumerApp's `AuraConsumerAppSimTests/Scripts/sim_priors.py`, which is the
# canonical mapping: the ideation analyzer tags new ideas with it, so a backfill
# using anything else would file old rows under names the new ones never use, and
# the priors block would silently show a screen's history under two domains.
#
# Copied rather than imported because the two live in different repositories —
# the same trade-off, and the same reason, as `DOMAIN_KEYWORDS` in `build-idea`.
# If one changes, change both.
#
# Note there is no `welcome` domain and there should not be: `welcome_screen` is
# the first screen of onboarding, and the second pattern below claims it. A new
# `welcome` value would split one funnel across two domains.
SCREEN_DOMAIN_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"paywall|packagePaywall|pricingTiers|subscription|subscriptionDetail|packageUsage"
     r"|packageConfig|paymentPending|cancellationReason|unsubscribe", "paywall"),
    (r"onboarding|welcome|signIn|signUp|splash|quiz|valueProps|didYouKnow|selfCommitment"
     r"|tutorial", "onboarding"),
    (r"home|useCases|encourageRating|dailyPractices|editPractices|customPractice", "home/feed"),
    (r"conversation|liveSession|quickProcessing|qpDone|chat|voice|orbReactions", "conversation"),
    (r"journal|practiceJournal", "journal"),
    (r"grounding|meditation|prayer|player|practiceRedo", "grounding"),
    (r"affirmation|bookmarks", "affirmations"),
    (r"profile|editProfileName|bringYourMemory", "profile"),
    (r"settings|preferences|about", "settings"),
    (r"notification", "notifications"),
)


def screen_domain(screen: str) -> Optional[str]:
    """The domain a screen belongs to, or None when nothing claims it.

    `sim_priors.screen_domain` returns "other" here. This returns None instead,
    because the two are answering different questions: the priors block needs a
    bucket for every screen it renders, while a backfill writing "other" would
    record a *decision* that the screen has no domain — and "other" is a real
    value in the vocabulary that the collision gate and the priors block both
    treat as a domain. An unmatched screen should be looked at, not labelled.
    """
    s = screen or ""
    for pattern, domain in SCREEN_DOMAIN_PATTERNS:
        if re.search(pattern, s, re.I):
            return domain
    return None


REJECTION_REASONS = (
    "already tested",
    "no longer valid",
    "low impact",
    "off-strategy",
    "tech cost",
    "duplicate",
    "timing",
    # The only reason automation may write without a human. The Aura
    # Recommendations loop closes a candidate that failed the offline bar with
    # it, so the value has to exist HERE as well as in that database's select —
    # the check below is enforced twice (argparse choices + the guard in
    # cmd_set), so a value present only in Notion is refused and the row stays
    # pending for share-ideas to post.
    "offline bar",
    "other",
)

# Statuses that represent a human verdict, so they carry attribution and — for
# the two negative ones — a reason.
#
# `merged` is deliberately NOT here. In this lifecycle it is a build milestone
# (in_progress -> in_review -> merged -> released), not a decision about the
# idea — "the PR landed", not "someone chose to fold this into another row".
# Treating it as a verdict would restamp `Decided at` at merge time and destroy
# the Slack date the field exists to preserve. An idea that is a duplicate of
# another is `rejected --reason duplicate`.
DECISION_STATUSES = {"approved", "rejected", "deferred"}

# A Gate 1 reply's own permalink, as Slack's "Copy link" gives it:
# https://<workspace>.slack.com/archives/<CHANNEL>/p<ts>. Checked for shape only —
# the point is that the evidence is a link someone can open, not a sentence.
SLACK_PERMALINK = re.compile(r"^https://[\w.-]+\.slack\.com/archives/[A-Z0-9]+/p\d+")
REASON_REQUIRED_STATUSES = {"rejected", "deferred"}

UUID_RE = re.compile(r"^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$", re.I)
FP_RE = re.compile(r"^[0-9a-f]{12}$", re.I)
# The immutable, human-friendly `Idea ID` (`42`, `#42`, `IDEA-42`). Bounded to
# ≤6 digits so it never collides with a 12-hex fingerprint.
IDEA_REF_RE = re.compile(r"^(?:idea[-\s]?|#)?(\d{1,6})$", re.IGNORECASE)


def _ref_idea_num(ref: str) -> Optional[int]:
    """The integer of an Idea-# reference (`42`, `#42`, `IDEA-42`), or None."""
    m = IDEA_REF_RE.match((ref or "").strip())
    return int(m.group(1)) if m else None


def _people_names(page: dict[str, Any], prop_name: str) -> list[str]:
    """Display names held by a `people` property. Empty list when unset."""
    prop = (page.get("properties") or {}).get(prop_name) or {}
    return [u.get("name") or u.get("id", "") for u in (prop.get("people") or [])]


def _make_people(user_ids: list[str]) -> dict[str, Any]:
    """Notion `people` property value. Not in notion_rest because that file is
    a byte-identical vendored copy across ~12 skills; a helper added to one
    copy would be silently dropped the next time it is re-vendored."""
    return {"people": [{"object": "user", "id": uid} for uid in user_ids]}


_USER_CACHE: Optional[list[dict[str, Any]]] = None


def _workspace_people() -> list[dict[str, Any]]:
    """Every human member of the workspace, paged. Bots are filtered out — a
    verdict is attributed to a person, and the integration itself is never the
    decider even though it is the account doing the write."""
    global _USER_CACHE
    if _USER_CACHE is not None:
        return _USER_CACHE
    people: list[dict[str, Any]] = []
    cursor: Optional[str] = None
    while True:
        params: dict[str, Any] = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        # Reaching past the client's public surface on purpose: /users has no
        # wrapper, and adding one would fork the vendored notion_rest.py.
        payload = notion._request("GET", "/users", params=params)  # noqa: SLF001
        people.extend(u for u in payload.get("results", []) if u.get("type") == "person")
        if not payload.get("has_more"):
            break
        cursor = payload.get("next_cursor")
    _USER_CACHE = people
    return people


def _resolve_user(ref: str) -> dict[str, Any]:
    """Resolve a name or email to one workspace member, or exit non-zero.

    Exact email wins outright so `<redacted-email>` can never be ambiguous;
    otherwise a case-insensitive substring of the display name, which fails
    loudly with candidates rather than picking one.
    """
    needle = ref.strip().lower()
    people = _workspace_people()
    if not people:
        raise SystemExit(
            "could not list workspace members — the integration may lack user "
            "read access. Pass --by-id <notion-user-uuid> instead."
        )
    exact = [u for u in people if ((u.get("person") or {}).get("email") or "").lower() == needle]
    if len(exact) == 1:
        return exact[0]
    matches = [u for u in people if needle in (u.get("name") or "").lower()]
    if not matches:
        raise SystemExit(f"no workspace member matches '{ref}'")
    if len(matches) > 1:
        lines = "\n".join(
            f"  {(u.get('person') or {}).get('email', '?'):<32} {u.get('name')}" for u in matches
        )
        raise SystemExit(f"'{ref}' matched {len(matches)} members — use the email:\n{lines}")
    return matches[0]


def _row_idea(row: dict[str, Any]) -> tuple[Optional[int], str]:
    """(number, 'IDEA-42') from a row's auto-`Idea ID` property; (None, '') if unset."""
    uid = (row.get("properties", {}).get("Idea ID") or {}).get("unique_id") or {}
    num = uid.get("number")
    if num is None:
        return None, ""
    prefix = uid.get("prefix")
    return num, (f"{prefix}-{num}" if prefix else str(num))


def _row_summary(row: dict[str, Any]) -> dict[str, Any]:
    """Compact representation of a Notion row for printing + matching."""
    idea_num, idea_id = _row_idea(row)
    return {
        "page_id": row["id"],
        "idea_num": idea_num,
        "idea_id": idea_id,
        "title": title_text(row, "Title"),
        "status": select_value(row, "Status"),
        "status_note": rich_text_value(row, "Quick status note"),
        "rejection_reason": select_value(row, "Rejection reason"),
        "decided_by": _people_names(row, "Decided by"),
        "decided_at": date_start(row, "Decided at"),
        "screen": rich_text_value(row, "Screen"),
        "category": rich_text_value(row, "Category"),
        "lever": rich_text_value(row, "Lever"),
        "impact": select_value(row, "Impact"),
        "effort": select_value(row, "Effort"),
        "domain": select_value(row, "Domain"),
        "fingerprint": rich_text_value(row, "Fingerprint"),
        "first_seen": date_start(row, "First seen"),
        "last_seen": date_start(row, "Last seen"),
        "slack_thread": (row.get("properties", {}).get("Slack thread") or {}).get("url") or "",
        "linear_issue": (row.get("properties", {}).get("Linear issue") or {}).get("url") or "",
        "description": rich_text_value(row, "Description"),
        "rationale": rich_text_value(row, "Rationale"),
        "hypothesis": rich_text_value(row, "Hypothesis"),
    }


def _build_filter(status: Optional[str], category: Optional[str], screen: Optional[str]) -> Optional[dict[str, Any]]:
    """Compose a Notion filter object from optional CLI filters."""
    parts: list[dict[str, Any]] = []
    if status:
        parts.append({"property": "Status", "select": {"equals": status}})
    if category:
        parts.append({"property": "Category", "rich_text": {"contains": category}})
    if screen:
        parts.append({"property": "Screen", "rich_text": {"contains": screen}})
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    return {"and": parts}


def _query_all(ds_id: str, filter_: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
    return [_row_summary(r) for r in notion.query_data_source(ds_id, filter=filter_, page_size=100)]


def _resolve_ref(ds_id: str, ref: str) -> dict[str, Any]:
    """Resolve a user-supplied ref to a single row summary, or exit non-zero."""
    if UUID_RE.match(ref.replace("-", "")) and "-" in ref:
        # Page UUID
        try:
            page = notion.get_page(ref)
            return _row_summary(page)
        except Exception as e:  # noqa: BLE001
            raise SystemExit(f"could not fetch page {ref}: {e}") from e

    rows = _query_all(ds_id)

    idea_num = _ref_idea_num(ref)
    if idea_num is not None:
        matches = [r for r in rows if r["idea_num"] == idea_num]
        if not matches:
            raise SystemExit(f"no idea has id {idea_num} (IDEA-{idea_num})")
        return matches[0]

    if FP_RE.match(ref):
        matches = [r for r in rows if r["fingerprint"] == ref.lower()]
        if not matches:
            raise SystemExit(f"no row with fingerprint '{ref}'")
        if len(matches) > 1:
            raise SystemExit(
                f"fingerprint '{ref}' matched {len(matches)} rows — should be 1. "
                "Investigate duplicate fingerprints."
            )
        return matches[0]

    # Title substring (case-insensitive)
    needle = ref.lower()
    matches = [r for r in rows if needle in (r["title"] or "").lower()]
    if not matches:
        raise SystemExit(f"no row title contains '{ref}'")
    if len(matches) > 1:
        lines = "\n".join(
            f"  {r['idea_id'] or r['fingerprint']}  [{r['status'] or '?':<9}] {r['title'][:80]}"
            for r in matches
        )
        raise SystemExit(
            f"'{ref}' matched {len(matches)} rows — disambiguate with an Idea ID:\n{lines}"
        )
    return matches[0]


# ---------- subcommands ------------------------------------------------------

def cmd_list(args: argparse.Namespace) -> int:
    if not notion.available:
        raise MCPFallback("NOTION_TOKEN is not set.")
    filter_ = _build_filter(args.status, args.category, args.screen)
    rows = _query_all(args.data_source_id, filter_)
    if args.missing_reason:
        # The PROD-5920 acceptance gate, made checkable: any row carrying a
        # negative verdict but no reason for it. Exits non-zero so it can be
        # used as a gate rather than only read by a human.
        rows = [
            r for r in rows
            if (r["status"] in REASON_REQUIRED_STATUSES) and not r["rejection_reason"]
        ]
    rows.sort(key=lambda r: (r["status"] or "z", r["screen"] or "", r["title"] or ""))
    for r in rows:
        print(
            f"{(r['idea_id'] or r['fingerprint']):<10}  "
            f"[{(r['status'] or '?'):<9}]  "
            f"{(r['screen'] or '-'):<28}  "
            f"{r['title'][:70]}"
        )
    print(f"\n{len(rows)} rows")
    if args.missing_reason and rows:
        print("Every rejected or deferred row needs a Rejection reason.", file=sys.stderr)
        return 1
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    if not notion.available:
        raise MCPFallback("NOTION_TOKEN is not set.")
    r = _resolve_ref(args.data_source_id, args.ref)
    print(f"Idea ID:     {r['idea_id'] or '(none)'}")
    print(f"Title:       {r['title']}")
    print(f"Status:      {r['status']}")
    if r["rejection_reason"]:
        print(f"Rejection reason: {r['rejection_reason']}")
    if r["status_note"]:
        print(f"Quick status note: {r['status_note']}")
    if r["decided_by"] or r["decided_at"]:
        who = ", ".join(r["decided_by"]) or "unattributed"
        print(f"Decided:     {who}{' on ' + r['decided_at'] if r['decided_at'] else ''}")
    print(f"Fingerprint: {r['fingerprint']}")
    print(f"Page ID:     {r['page_id']}")
    print(f"Screen:      {r['screen'] or '-'}")
    print(f"Domain:      {r['domain'] or '-'}")
    print(f"Category:    {r['category'] or '-'}")
    print(f"Lever:       {r['lever'] or '-'}")
    print(f"Impact:      {r['impact'] or '-'}")
    print(f"Effort:      {r['effort'] or '-'}")
    print(f"First seen:  {r['first_seen'] or '-'}")
    print(f"Last seen:   {r['last_seen'] or '-'}")
    if r["slack_thread"]:
        print(f"Slack:       {r['slack_thread']}")
    if r["linear_issue"]:
        print(f"Linear:      {r['linear_issue']}")
    if r["description"]:
        print(f"\nDescription:\n  {r['description']}")
    if r["rationale"]:
        print(f"\nRationale:\n  {r['rationale']}")
    if r["hypothesis"]:
        print(f"\nHypothesis:\n  {r['hypothesis']}")
    return 0


def _set_status(args: argparse.Namespace, status: str) -> int:
    # `Decided by` is the record of a person's Gate 1 reply, not of whoever ran this
    # command. Naming someone needs the thread where they decided; an agent triaging
    # rows passes neither. (A triage pass once named a person on 26 rows for decisions
    # they never made, and Notion paged them for each.) Checked before the token, because
    # nothing about a missing flag needs a network.
    named = getattr(args, "by", None) or getattr(args, "by_id", None)
    decided_in = getattr(args, "decided_in", None)
    if named and not decided_in:
        raise SystemExit("--by/--by-id name a person as the decider, which needs --decided-in "
                         "<Slack permalink of the reply where they decided>. Agent triage "
                         "passes neither: Status, --reason and --note only.")
    if decided_in and not SLACK_PERMALINK.match(decided_in):
        raise SystemExit(
            f"--decided-in {decided_in!r} is not a Slack permalink. Use the reply's own "
            "\u201cCopy link\u201d: https://<workspace>.slack.com/archives/<CHANNEL>/p<ts>"
        )
    if getattr(args, "at", None) and not named:
        raise SystemExit("--at dates a decision, so it needs the person who made it: pass --by "
                         "(with --decided-in), or leave both off.")

    if not notion.available:
        raise MCPFallback("NOTION_TOKEN is not set.")
    if status not in VALID_STATUSES:
        raise SystemExit(f"invalid status '{status}'")

    reason = getattr(args, "reason", None)
    if status in REASON_REQUIRED_STATUSES and not reason:
        raise SystemExit(
            f"'{status}' requires --reason ({', '.join(REJECTION_REASONS)}). "
            "A negative verdict with no recorded reason is the one thing this "
            "database exists to prevent — the next ideation run re-proposes it."
        )
    if reason:
        if reason not in REJECTION_REASONS:
            raise SystemExit(f"invalid --reason '{reason}' — one of: {', '.join(REJECTION_REASONS)}")
        if status not in REASON_REQUIRED_STATUSES:
            raise SystemExit(f"--reason applies to {'/'.join(sorted(REASON_REQUIRED_STATUSES))}, not '{status}'")

    r = _resolve_ref(args.data_source_id, args.ref)
    props: dict[str, Any] = {"Status": make_select(status)}
    note = args.note if args.note else r["status_note"]
    if reason:
        props["Rejection reason"] = make_select(reason)

    decider = None
    if named:
        if getattr(args, "by", None):
            decider = _resolve_user(args.by)
            props["Decided by"] = _make_people([decider["id"]])
        else:
            props["Decided by"] = _make_people([args.by_id])

    # The link is the evidence for the name beside it, so it lands on the row rather
    # than only in the shell history. Appended to whatever note is already there — a
    # verdict must never be the thing that erases the note explaining it.
    if decided_in and decided_in not in (note or ""):
        note = (note + "\n\n" if note else "") + f"Decided in: {decided_in}"
    if note != r["status_note"]:
        props["Quick status note"] = make_rich_text(note[:1990])

    stamped: Optional[str] = None
    if status in DECISION_STATUSES and "Decided by" in props:
        # Stamp the date on a verdict, but never overwrite one already there.
        # Re-running `reject` to correct a note, or moving approved -> deferred
        # later, must not silently rewrite when the call was actually made.
        # An explicit --at always wins, since that is someone asserting the date
        # on purpose; otherwise today's date fills an empty field only, so a row
        # is never "decided by nobody, at no time".
        if args.at:
            stamped = args.at
        elif not r["decided_at"]:
            stamped = dt.date.today().isoformat()
        if stamped:
            props["Decided at"] = make_date(stamped)

    notion.update_page_props(r["page_id"], props)

    print(f"~ {r['fingerprint']} [{r['status'] or '?'} -> {status}]  {r['title'][:70]}")
    # The thread shows the verdict at a glance: a build mark on approval, a cross on a
    # close. Best-effort and after the Notion write — a missing token or scope is
    # reported on the next line, never raised over a decision already recorded.
    if status in MARKS:
        print(f"  thread: {mark_thread(r.get('slack_thread') or '', status)}")
    if reason:
        print(f"  reason: {reason}")
    if args.note:
        print(f"  note: {args.note}")
    if status in DECISION_STATUSES:
        # What the ROW ends up saying, which is not the same as what this call passed:
        # a note fix or a later verdict on an already-attributed row leaves the
        # existing decider in place, and reporting it gone would be a lie about Notion.
        named_now = (decider or {}).get("name") or getattr(args, "by_id", None)
        on_row = ", ".join(r["decided_by"])   # _people_names gives a list, as cmd_show joins it
        when = stamped or r["decided_at"]
        kept = " (kept)" if not stamped else ""
        if named_now:
            print(f"  decided by {named_now} on {when}{kept}")
            print(f"  decided in {decided_in}")
        elif on_row:
            print(f"  decided by {on_row} on {when}{kept} — already on the row, unchanged")
        elif when:
            print(f"  decided on {when}{kept} — no decider on the row")
        else:
            print("  unattributed — a decider comes from a Gate 1 reply "
                  "(--by with --decided-in), never from running this command")
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    return _set_status(args, "approved")


def cmd_reject(args: argparse.Namespace) -> int:
    return _set_status(args, "rejected")


def cmd_set_status(args: argparse.Namespace) -> int:
    return _set_status(args, args.status_value)


def cmd_set_domain(args: argparse.Namespace) -> int:
    """Write Domain on one row."""
    row = _resolve_ref(args.data_source_id, args.ref)
    if args.domain not in VALID_DOMAINS:
        print(f"REFUSED: '{args.domain}' is not a domain. Valid: {sorted(VALID_DOMAINS)}",
              file=sys.stderr)
        return 2
    notion.update_page_props(row["page_id"], {"Domain": make_select(args.domain)})
    print(f"~ {row['fingerprint']} domain {row['domain'] or '-'} -> {args.domain}  "
          f"{row['title'][:55]}")
    return 0


def cmd_backfill_domains(args: argparse.Namespace) -> int:
    """Derive Domain from Screen for rows that have none. Dry by default.

    Every row predating the Domain column has an empty one, and an empty Domain
    is not cosmetic: `build-idea plan` renders `domain: ?`, `guard-collision`
    cannot be driven from the row at all, and the priors block loses the grouping
    it uses to scope a screen's history.
    """
    rows = _query_all(args.data_source_id)
    todo, unmatched, skipped = [], [], 0
    for r in rows:
        if r["domain"] and not args.overwrite:
            skipped += 1
            continue
        d = screen_domain(r["screen"])
        (todo if d else unmatched).append((r, d))

    for r, d in todo:
        print(f"~ {r['fingerprint']}  {r['screen']:30} -> {d:14} {r['title'][:45]}")
        if args.apply:
            notion.update_page_props(r["page_id"], {"Domain": make_select(d)})
    for r, _ in unmatched:
        print(f"? {r['fingerprint']}  {r['screen'] or '(no screen)':30} -> no pattern claims it; "
              f"set it by hand with `set-domain`", file=sys.stderr)

    print(f"\n{len(todo)} to write, {len(unmatched)} unmatched, {skipped} already set.")
    if todo and not args.apply:
        print("-- dry run. Add --apply to write. --", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="manage_ideas",
        description="Manage rows in the Notion Learnings/Loop/<Loop> database.",
    )
    p.add_argument(
        "--data-source-id",
        default=AURIE_FEATURES_DS,
        help="Notion data source ID (default: Aurie Features Ideas)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("list", help="List rows, optionally filtered")
    sp.add_argument("--status", help=f"Filter by status ({'/'.join(sorted(VALID_STATUSES))})")
    sp.add_argument("--category", help="Filter by category")
    sp.add_argument("--screen", help="Filter by screen substring")
    sp.add_argument(
        "--missing-reason",
        action="store_true",
        help="Only rejected/deferred rows with no Rejection reason. Exits 1 if any exist.",
    )
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("show", help="Print one row in full")
    sp.add_argument("ref", help="Page UUID, fingerprint, or title substring")
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("set-domain", help="Write Domain on one row")
    sp.add_argument("ref", help="Page UUID, fingerprint, or title substring")
    sp.add_argument("--domain", required=True, choices=sorted(VALID_DOMAINS))
    sp.set_defaults(func=cmd_set_domain)

    sp = sub.add_parser("backfill-domains", help="Derive Domain from Screen where it is empty")
    sp.add_argument("--apply", action="store_true", help="Actually write (default: dry run)")
    sp.add_argument("--overwrite", action="store_true",
                    help="Also rewrite rows that already have a Domain")
    sp.set_defaults(func=cmd_backfill_domains)

    def add_decision_flags(parser: argparse.ArgumentParser, *, with_reason: bool) -> None:
        """Attribution flags shared by every verb that records a verdict."""
        parser.add_argument("--note", required=False, help="Reviewer rationale (prose)")
        if with_reason:
            parser.add_argument(
                "--reason",
                choices=REJECTION_REASONS,
                metavar="REASON",
                help=f"Rejection reason — required. One of: {', '.join(REJECTION_REASONS)}",
            )
        parser.add_argument("--by", help="Who decided — Notion member name or email (needs --decided-in)")
        parser.add_argument("--by-id", dest="by_id", help="Notion user UUID, when --by cannot resolve")
        parser.add_argument("--decided-in", dest="decided_in",
                            help="Slack permalink of the reply where that person decided — required with --by")
        parser.add_argument("--at", help="Decision date, ISO YYYY-MM-DD (default: today, only with --by)")

    sp = sub.add_parser("approve", help="Flip Status -> approved, record who and when")
    sp.add_argument("ref")
    add_decision_flags(sp, with_reason=False)
    sp.set_defaults(func=cmd_approve)

    sp = sub.add_parser("reject", help="Flip Status -> rejected, record the reason, who and when")
    sp.add_argument("ref")
    add_decision_flags(sp, with_reason=True)
    sp.set_defaults(func=cmd_reject)

    sp = sub.add_parser(
        "set-status",
        help="General-purpose status writer (use approve/reject for the common cases)",
    )
    sp.add_argument("ref")
    sp.add_argument("status_value", choices=sorted(VALID_STATUSES))
    add_decision_flags(sp, with_reason=True)
    sp.set_defaults(func=cmd_set_status)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except MCPFallback as e:
        print(f"manage-ideas: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
