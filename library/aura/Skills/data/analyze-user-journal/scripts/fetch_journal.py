#!/usr/bin/env python3
"""Fetch one rendered journal entry from the Aura API by its journal id.

This is the authoritative source for what the user actually read and rated:
the persisted `journal` row, carrying the title, summary, insights and
affirmations exactly as the app displayed them, plus the rating and the
feedback comment the user typed.

The id comes from the k8bot `*JOURNAL RATED*` footer `[id: <uuid>, user: <uid>]`
-- the `journal` table primary key -- and the footer's `user:` is the full uid
of its owner. `GET /ai/journal/:id` answers only the entry's owner, so the row
is read from that user's `POST /ai/journals/list/:userId`.

Complementary to `fetch_logs.py`, which reads the OpenAI side: the *input*
context the journal was generated from. This script answers "what did the
user see?"; that one answers "what was it made from?".

Usage:
    python3 fetch_journal.py --footer '[id: <uuid>, user: <uid>]' [--json out.json]
    python3 fetch_journal.py --journal-id <uuid> --uid <uid> [--json out.json]

Environment:
    AURA_API_TOKEN  (required) -- Bearer token for services.aurahealth.io
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import pathlib
import re


def _ua_who() -> str:
    """Login name of whoever runs this, for the user agent: AINATIVE_UA_WHO, else the global git email's local part, git user.name, the OS account, or "unknown"."""
    who = re.sub(r"^\d+\+", "", os.environ.get("AINATIVE_UA_WHO", "").strip().partition("@")[0])
    if not who:
        if not hasattr(_ua_who, "found"):
            import getpass, subprocess
            found = ""
            for key in ("user.email", "user.name"):
                try:
                    val = subprocess.run(["git", "config", "--global", key], capture_output=True, encoding="utf-8", errors="replace", timeout=5).stdout.strip()
                except (OSError, subprocess.SubprocessError):
                    val = ""
                found = re.sub(r"^\d+\+", "", val.partition("@")[0]) if key == "user.email" else val
                if found:
                    break
            if not found:
                try:
                    found = getpass.getuser()
                except Exception:
                    found = ""
            _ua_who.found = found
        who = _ua_who.found
    return re.sub(r"[^A-Za-z0-9._-]+", "-", who).strip("-.") or "unknown"


def user_agent() -> str:
    """`AuraClaude/<who>/<plugin>:<skill>@<version> (session <id>)` -- labels this skill's calls to RTDB, GCP and the Aura API.

    Same format as gcloud_sa.user_agent(). A label, not proof: anyone can send it.
    """
    skill = pathlib.Path(__file__).resolve().parents[1]
    root = skill.parents[1]
    try:
        plugin = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["name"]
    except (OSError, ValueError, KeyError, TypeError):
        plugin = root.name
    try:
        front = (skill / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[1]
        version = re.search(r'version:\s*"([^"]+)"', front).group(1)
    except (OSError, IndexError, AttributeError):
        version = "unknown"
    session = os.environ.get("AINATIVE_SESSION_NAME") or (os.environ.get("CLAUDE_CODE_SESSION_ID") or "")[:8]
    session = re.sub(r"[^A-Za-z0-9._-]+", "-", session).strip("-") or "none"
    return f"AuraClaude/{_ua_who()}/{plugin}:{skill.name}@{version} (session {session})"


AURA_BASE_URL = "https://services.aurahealth.io"
REQUEST_TIMEOUT_S = 30


class FetchJournalError(Exception):
    """Raised when the journal cannot be resolved."""


def _normalize(raw: dict) -> dict:
    """Reshape the API row into the fields the analysis consumes.

    `insights` and `affirmations` arrive already unwrapped from their JSONB
    `{data: [...]}` envelope by the API layer.
    """
    return {
        "journal_id": raw.get("id"),
        "user_id": raw.get("userId"),
        "type": raw.get("type"),
        "created_at": raw.get("createdAt"),
        "read_at": raw.get("readAt"),
        "title": raw.get("title"),
        "summary": raw.get("summary"),
        "insights": raw.get("insights") or [],
        "affirmations": raw.get("affirmations") or [],
        "notes": raw.get("notes"),
        "mood": raw.get("mood"),
        "emotions": raw.get("emotions") or [],
        "impacts": raw.get("impacts") or [],
        "rating": raw.get("rating"),
        "feedback": raw.get("feedback"),
    }


PAGE = 200


def _post(path: str, token: str, body: dict):
    request = urllib.request.Request(
        f"{AURA_BASE_URL}{path}",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                 "User-Agent": user_agent()},
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:
        return json.loads(response.read().decode("utf-8"))


def _call(path: str, token: str, body: dict, what: str):
    try:
        return _post(path, token, body)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        if exc.code in (401, 403):
            raise FetchJournalError(
                f"Not authorized for {what} (HTTP {exc.code}). AURA_API_TOKEN is missing or "
                "expired, or lacks the admin/agent role the route needs."
            ) from exc
        raise FetchJournalError(f"{what} failed with HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise FetchJournalError(f"Could not reach {AURA_BASE_URL}: {exc.reason}") from exc


FOOTER = re.compile(r"\[\s*id:\s*([^,\]\s]+)\s*,\s*user:\s*([^,\]\s]+)")


def parse_footer(text: str) -> tuple[str, str]:
    """(journal id, uid) from a k8bot `*JOURNAL RATED*` footer `[id: <uuid>, user: <uid>]`."""
    m = FOOTER.search(text or "")
    if not m:
        raise FetchJournalError("No `[id: <uuid>, user: <uid>]` footer found in --footer.")
    return m.group(1), m.group(2)


def fetch_journal(journal_id: str, user_id: str, token: str) -> dict:
    """Page the owner's journals (newest first, removed ones included: a rated entry may since have
    been removed) until the row with this id. The owner is the footer's `user:`; no lookup by id."""
    path = f"/ai/journals/list/{urllib.parse.quote(user_id, safe='')}"
    page = 0
    while True:
        try:
            rows = _call(path, token, {"limit": PAGE, "page": page, "sort": "desc", "includeRemoved": True},
                         "the journal list")
        except FetchJournalError as exc:
            if "HTTP 404" in str(exc):
                raise FetchJournalError(
                    f"No user {uid_prefix(user_id)}… (404 from the journal list): check the footer's `user:`."
                ) from exc
            raise
        if not isinstance(rows, list):
            raise FetchJournalError("The journal list answered with something other than a list.")
        for raw in rows:
            if raw.get("id") == journal_id:
                return _normalize(raw)
        if len(rows) < PAGE:
            raise FetchJournalError(
                f"Journal {journal_id} is not in user {uid_prefix(user_id)}…'s journal list: check that "
                "the footer's id and user were copied whole, and that the entry was not hard-deleted."
            )
        page += 1


def uid_prefix(uid: str) -> str:
    """A uid prefix that still tells users apart: for a provider id (`google:112...`) the 8
    characters come after the provider, which stays as a label. Canonical:
    report-conversation-patterns/scripts/sample.py (docs/canonical-sources.md)."""
    uid = uid or ""
    if ":" in uid:
        provider, _, rest = uid.partition(":")
        return f"{provider}:{rest[:8]}"
    return uid[:8]


def has_content(journal: dict) -> bool:
    """True when the row carries something the analysis can actually read."""
    return any(
        journal.get(field)
        for field in ("title", "summary", "insights", "affirmations", "notes")
    )


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser()
    ap.add_argument("--footer", help="The k8bot footer `[id: <uuid>, user: <uid>]`; gives both ids.")
    ap.add_argument("--journal-id", help="Journal UUID (the footer's `id:`), with --uid.")
    ap.add_argument("--uid", help="The entry owner's uid (the footer's `user:`), with --journal-id.")
    ap.add_argument("--json", dest="json_out", help="Write JSON output here.")
    args = ap.parse_args()
    if args.footer and (args.journal_id or args.uid):
        ap.error("pass --footer, or --journal-id with --uid, not both")
    if not args.footer and not (args.journal_id and args.uid):
        ap.error("pass --footer, or --journal-id with --uid (the footer's `user:` is the owner)")

    token = os.environ.get("AURA_API_TOKEN")
    if not token:
        sys.exit(
            "AURA_API_TOKEN is not set. It is required -- the rendered journal "
            "is the primary evidence for a journal-rating analysis, and there "
            "is no other source for it."
        )

    try:
        journal_id, user_id = parse_footer(args.footer) if args.footer else (args.journal_id, args.uid)
        journal = fetch_journal(journal_id, user_id, token)
    except FetchJournalError as exc:
        sys.exit(str(exc))

    if not has_content(journal):
        print(
            "# WARNING: the journal row resolved but carries no title, summary, "
            "insights, affirmations or notes. Report this as an unresolved "
            "journal -- do not write the analysis as though the entry were empty "
            "by design.",
            file=sys.stderr,
        )

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(journal, f, indent=2, default=str)
        print(f"Wrote {args.json_out}")
        print(
            f"  journal={journal['journal_id']}  user={uid_prefix(journal['user_id'])}…  "
            f"type={journal['type']}  rating={journal['rating']}  "
            f"title={journal['title']!r}"
        )
    else:
        json.dump(journal, sys.stdout, indent=2, default=str)
        print()


if __name__ == "__main__":
    main()
