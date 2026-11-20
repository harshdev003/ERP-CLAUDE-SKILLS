#!/usr/bin/env python3
"""Extract every matchable identifier from the `Team/Internal Accounts` page.

The consumers of that page (`fetch_ratings.py`, `fetch_pmf_responses.py`,
`chart_ratings.py`) used to take `--internal-ids` as a CSV the model assembled by
reading the page each run. That step is not deterministic: on 2026-09-28 a tick kept
the 28-char Firebase UIDs and dropped both `google:` provider ids, so two founders'
ratings reached the sweep as customer feedback. Parsing lives here instead, and the
consumers read the page file directly via `--internal-accounts-file`.

Input is the page body as markdown — the `retrieve-knowledge` cache file
(`~/.cache/ai-knowledge/team__internal-accounts.md`) or a saved `notion-fetch`
result. Both the Notion `<table>` rendering and pipe tables work: extraction is by
identifier *shape* over the allowlist sections, not by table structure.

Scope: from the "Employee accounts" heading to the "Sources" heading. The prose
above it discusses accounts as worked examples, and Sources repeats ids as
citations — neither is the allowlist. A page with no "Employee accounts" heading
raises rather than returning nothing, because an empty allowlist is silent.

CLI (masked output only — never prints a full id or address):

    python3 internal_accounts.py [page.md]      # default: the retrieve-knowledge cache

Canonical: `data/skills/investigate-user-rating/scripts/internal_accounts.py`.
Byte-identical copies ship with every skill whose script reads the page; see
`docs/canonical-sources.md`.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys

CACHE_FILENAME = "team__internal-accounts.md"

SECTION_START_RE = re.compile(r"^#{1,6}\s*Employee accounts\b.*$", re.M | re.I)
SECTION_END_RE = re.compile(r"^#{1,6}\s*Sources\b.*$", re.M | re.I)

# 28-char Firebase UID. Not preceded by `:` / `.` / `/` so the tail of a provider id
# or a URL segment is never read as one, and at least one letter so a number isn't.
FIREBASE_UID_RE = re.compile(
    r"(?<![A-Za-z0-9:./])(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{28}(?![A-Za-z0-9])"
)
PROVIDER_ID_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:google|apple|facebook|twitter|microsoft|github):[A-Za-z0-9._-]*[A-Za-z0-9]"
)
# `\*` is a markdown-escaped wildcard (`someone.for.test+\*@gmail.com`).
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-*\\]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def default_page_path() -> str:
    root = os.environ.get("AI_KNOWLEDGE_CACHE_DIR") or os.path.expanduser("~/.cache/ai-knowledge")
    return os.path.join(root, CACHE_FILENAME)


def allowlist_section(text: str) -> str:
    start = SECTION_START_RE.search(text)
    if not start:
        raise ValueError(
            "no 'Employee accounts' heading — not the Team/Internal Accounts page, "
            "or the page was restructured; refusing to return an empty allowlist"
        )
    end = SECTION_END_RE.search(text, start.end())
    return text[start.start(): end.start() if end else len(text)]


def extract(text: str) -> dict[str, list[str]]:
    """Return {firebase_uids, provider_ids, emails, email_patterns}, sorted, deduped."""
    section = allowlist_section(text)
    emails, patterns = set(), set()
    for m in EMAIL_RE.findall(section):
        addr = m.replace("\\", "").lower()
        (patterns if "*" in addr else emails).add(addr)
    return {
        "firebase_uids": sorted(set(FIREBASE_UID_RE.findall(section))),
        "provider_ids": sorted(set(PROVIDER_ID_RE.findall(section))),
        "emails": sorted(emails),
        "email_patterns": sorted(patterns),
    }


def load(path: str) -> dict[str, list[str]]:
    with open(path, encoding="utf-8") as fh:
        return extract(fh.read())


def split_ids(csv: str) -> tuple[set[str], set[str]]:
    """Split an `--internal-ids` CSV into (ids, emails). Emails are lowercased."""
    ids, emails = set(), set()
    for item in (s.strip() for s in (csv or "").split(",")):
        if not item:
            continue
        if "@" in item:
            emails.add(item.lower())
        else:
            ids.add(item)
    return ids, emails


def resolve(csv: str, page_file: str | None) -> dict[str, set[str]]:
    """Merge `--internal-ids` with `--internal-accounts-file` into one allowlist.

    Returns {ids, emails, email_patterns, provider_ids, explicit_emails}.
    `provider_ids` is the subset of `ids` that is provider-prefixed, kept so callers
    can report it: zero provider ids on a page that lists some is the 2026-09-28
    failure, and a count shows it. `explicit_emails` counts addresses the caller put
    in the CSV itself — a surface whose footer carries no email warns on those, and
    only those, since the page's email rows are expected there.
    """
    ids, emails = split_ids(csv)
    explicit = len(emails)
    patterns = {e for e in emails if "*" in e}
    emails -= patterns
    if page_file:
        page = load(page_file)
        ids |= set(page["firebase_uids"]) | set(page["provider_ids"])
        emails |= set(page["emails"])
        patterns |= set(page["email_patterns"])
    return {
        "ids": ids,
        "emails": emails,
        "email_patterns": patterns,
        "provider_ids": {i for i in ids if PROVIDER_ID_RE.fullmatch(i)},
        "explicit_emails": explicit,
    }


def email_matches(email: str, emails: set[str], patterns: set[str]) -> bool:
    email = (email or "").strip().lower()
    return bool(email) and (email in emails or any(fnmatch.fnmatchcase(email, p) for p in patterns))


def mask(value: str) -> str:
    """Display form: provider label + 8 chars, a uid's first 8, an address's domain.
    Never the whole value."""
    if "@" in value:
        return "*@" + value.rsplit("@", 1)[-1]
    if ":" in value:
        provider, _, rest = value.partition(":")
        return f"{provider}:{rest[:8]}"
    return value[:8]


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Summarise the Team/Internal Accounts allowlist (masked).")
    ap.add_argument("page", nargs="?", default=None,
                    help=f"Page markdown (default: the retrieve-knowledge cache, {CACHE_FILENAME}).")
    args = ap.parse_args()
    path = args.page or default_page_path()
    try:
        ids = load(path)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"internal_accounts: {path}: {exc}")
    print(json.dumps({k: [mask(v) for v in vals] for k, vals in ids.items()}, indent=2))


if __name__ == "__main__":
    main()
