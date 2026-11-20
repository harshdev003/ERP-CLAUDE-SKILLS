#!/usr/bin/env python3
"""Read one user's stored IntelligenceService data for a data request, read-only, into JSON files.

    python3 export_sql.py --uid-file <0600 file> --out-dir <private folder> [--sections a,b] [--print-only]

It writes one `.sql` file and runs it with psql against `$INTELLIGENCE_DB_URL` (the database owner's
own connection): one REPEATABLE READ READ ONLY transaction, `default_transaction_read_only=on`, fixed
SELECTs on named columns, every one filtered by the one uid. psql reads the uid from the uid file at
run time (`\\set uid `cat ...``), so it is never in the SQL text, the command line or this output. A uid
with no `user` row stops the run before any file is written.

Each section lands as `{version, entity, generatedAt, userIdPrefix, rows}` (the prefix is the
canonical `uid_prefix()`) in a 0600 file inside a 0700
folder; an existing file is never overwritten. The terminal shows paths and counts only.

When the database can't be reached (no URL, refused, login denied) it tries no other credential: it
prints the one psql command for someone with access to run, and `--check` reads the files afterwards.
The SELECTs are BackendIntelligence's (the schema owner), ported verbatim with ISO-Z timestamps.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shlex
import subprocess
import sys
import urllib.parse
from pathlib import Path

VERSION = 1
URL_ENV = "INTELLIGENCE_DB_URL"
PGOPTIONS = "-c default_transaction_read_only=on -c statement_timeout=180000"


def iso(column: str) -> str:
    """A timestamptz as ISO 8601 with a Z, NULL kept NULL."""
    return f"""to_char({column} AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"')"""


SECTIONS = {
    "profile": f"""
SELECT "profile", {iso('"profileIngestedAt"')} AS "profileIngestedAt",
  "insights", {iso('"insightsCompiledAt"')} AS "insightsCompiledAt",
  "insightsRenderers", {iso('"insightsRenderersCompiledAt"')} AS "insightsRenderersCompiledAt",
  "onboarding"->'conversation' AS "onboardingConversation"
FROM "user"
WHERE "id" = :'uid'""",
    "memory": f"""
SELECT "memory", {iso('"createdAt"')} AS "createdAt"
FROM "memory"
WHERE "userId" = :'uid'
  AND "deletedAt" IS NULL
  AND ("expiryAt" IS NULL OR "expiryAt" > NOW())
ORDER BY "createdAt" ASC, "id" ASC""",
    "insights": f"""
SELECT "id", "kind", "title", "message", "mood"::float8 AS "mood", "emotion",
  -- each highlight embeds transcript entries that carry the userId: conversation excerpts the
  -- conversations section already holds, so they are dropped here
  CASE WHEN jsonb_typeof("highlights"->'data') = 'array'
    THEN (SELECT COALESCE(jsonb_agg(CASE WHEN jsonb_typeof(h) = 'object' THEN h - 'transcripts' ELSE h END
                                    ORDER BY o), '[]'::jsonb)
          FROM jsonb_array_elements("highlights"->'data') WITH ORDINALITY AS x(h, o))
    ELSE "highlights"->'data'
  END AS "highlights",
  "recommendations"->'data' AS "recommendations",
  "chatStarters"->'data' AS "chatStarters",
  "timeline"->'data' AS "timeline",
  "day"::text AS "day", "week", "year", "rating"::float8 AS "rating", "feedback",
  {iso('"createdAt"')} AS "createdAt"
FROM "insight"
WHERE "userId" = :'uid' AND "removedAt" IS NULL AND "deletedAt" IS NULL
ORDER BY "createdAt" ASC, "id" ASC""",
    "affirmations": f"""
SELECT "id", "text", "rating"::float8 AS "rating", "feedback", {iso('"createdAt"')} AS "createdAt"
FROM "affirmation"
WHERE "userId" = :'uid' AND "removedAt" IS NULL AND "deletedAt" IS NULL
ORDER BY "createdAt" ASC, "id" ASC""",
    "conversations": f"""
SELECT c."id", c."title", c."previewMessage", c."type",
  c."isOnboarding" IS TRUE AS "isOnboarding",
  CASE
    WHEN c."firstMessageContext"->>'isMorningCheckIn' = 'true' THEN 'morning'
    WHEN c."firstMessageContext"->>'isEveningCheckIn' = 'true' THEN 'evening'
    WHEN c."firstMessageContext"->>'isWalkingPractice' = 'true' THEN 'walking'
  END AS "practice",
  {iso('c."startedAt"')} AS "startedAt", {iso('c."endedAt"')} AS "endedAt",
  {iso('c."createdAt"')} AS "createdAt",
  c."removedAt" IS NOT NULL AS "removed",
  c."rating"::float8 AS "rating", c."feedback",
  COALESCE((
    SELECT json_agg(json_build_object(
        'role', d."role", 'message', d."message", 'createdAt', {iso('d."createdAt"')},
        'rating', d."rating"::float8, 'feedback', d."feedback")
      ORDER BY d."createdAt", d."id")
    FROM "dialogue" d
    WHERE d."conversationId" = c."id"
      AND d."userId" = :'uid'
      AND d."removedAt" IS NULL
      AND d."message" IS NOT NULL AND d."message" <> ''
      AND d."role" IN ('user', 'assistant')
  ), '[]'::json) AS "messages"
FROM "conversation" c
WHERE c."userId" = :'uid'
  AND c."deletedAt" IS NULL
  AND c."incognito" IS NOT TRUE
ORDER BY c."createdAt" ASC, c."id" ASC""",
    "journal": f"""
SELECT j."id", j."type", j."title", j."summary", j."notes",
  j."insights"->'data' AS "insights",
  j."affirmations"->'data' AS "affirmations",
  j."mood"::float8 AS "mood",
  j."emotions"->'data' AS "emotions",
  j."impacts"->'data' AS "impacts",
  {iso('j."createdAt"')} AS "createdAt",
  (j."createdAt" AT TIME ZONE COALESCE(u."timezone", 'UTC'))::date::text AS "localDay",
  j."removedAt" IS NOT NULL AS "removed",
  j."rating"::float8 AS "rating", j."feedback"
FROM "journal" j
JOIN "user" u ON u."id" = j."userId"
WHERE j."userId" = :'uid'
  AND j."deletedAt" IS NULL
ORDER BY j."createdAt" ASC, j."id" ASC""",
    # The user's ratings of the Profile tab (rendererKey null) and of its cards. Never the rated snapshot.
    "profileRatings": f"""
SELECT "rendererKey", "rating"::float8 AS "rating", "feedback", {iso('"createdAt"')} AS "createdAt"
FROM "rendererRating"
WHERE "userId" = :'uid' AND "deletedAt" IS NULL
ORDER BY "createdAt" ASC, "id" ASC""",
}


# The order of each section's rows inside json_agg: an aggregate does not keep a subquery's ORDER BY.
# ISO-Z strings sort as the timestamps do.
AGG_ORDER = {
    "profile": "",
    "memory": ' ORDER BY t."createdAt", t."memory"',
    "insights": ' ORDER BY t."createdAt", t."id"',
    "affirmations": ' ORDER BY t."createdAt", t."id"',
    "conversations": ' ORDER BY t."createdAt", t."id"',
    "journal": ' ORDER BY t."createdAt", t."id"',
    "profileRatings": ' ORDER BY t."createdAt", t."rendererKey"',
}


def uid_prefix(uid: str) -> str:
    """A uid prefix that still tells users apart: for a provider id (`google:112...`) the 8
    characters come after the provider, which stays as a label. Canonical:
    report-conversation-patterns/scripts/sample.py (docs/canonical-sources.md)."""
    uid = uid or ""
    if ":" in uid:
        provider, _, rest = uid.partition(":")
        return f"{provider}:{rest[:8]}"
    return uid[:8]


def file_prefix(uid: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "-", uid_prefix(uid))


def psql_quote(text: str) -> str:
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def read_uid(path: Path) -> str:
    """The uid from its trusted file. Refuses a file others can read, or anything that isn't one id."""
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise SystemExit(f"Refusing: the uid file is readable by others (mode {mode:o}); chmod 600 it.")
    uid = path.read_text(encoding="utf-8").strip()
    if not uid or len(uid) > 256 or re.search(r"\s", uid):
        raise SystemExit("Refusing: the uid file does not hold one user id.")
    return uid


def plan(uid: str, out_dir: Path, sections: list[str], stamp: str) -> dict[str, Path]:
    """Where each section lands. Never an existing file: a second run gets its own timestamp."""
    paths = {s: out_dir / f"{file_prefix(uid)}-{s}-{stamp}.json" for s in sections}
    taken = [p.name for p in paths.values() if p.exists()]
    if taken:
        raise SystemExit(f"Refusing to overwrite {len(taken)} existing export file(s) in {out_dir}.")
    return paths


def build_sql(uid_file: Path, paths: dict[str, Path], generated_at: str, prefix: str) -> str:
    """The whole run as one psql script: uid from its file, existence check, one read-only snapshot."""
    out = [
        "\\set ON_ERROR_STOP on",
        "\\pset format unaligned",
        "\\pset tuples_only on",
        f"\\set uid `cat {shlex.quote(str(uid_file))}`",
        "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;",
        "SET LOCAL statement_timeout = '180s';",
        "-- A uid with no user row stops here: four empty files must never look like a real export.",
        "SELECT EXISTS (SELECT 1 FROM \"user\" WHERE \"id\" = :'uid') AS found \\gset",
        "\\if :found",
        "\\else",
        "\\echo 'NO_SUCH_USER: nothing written'",
        "ROLLBACK;",
        "\\quit",
        "\\endif",
    ]
    for entity, path in paths.items():
        out += [
            f"\\o {psql_quote(str(path))}",
            "SELECT json_build_object('version', " + str(VERSION) + f", 'entity', '{entity}', "
            f"'generatedAt', '{generated_at}', 'userIdPrefix', {psql_quote(prefix)}, "
            f"'rows', COALESCE(json_agg(t{AGG_ORDER[entity]}), '[]'::json))",
            f"FROM ({SECTIONS[entity].strip()}) t;",
            "\\o",
        ]
    out += ["ROLLBACK;", ""]
    return "\n".join(out)


def private_write(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def command(sql_path: Path) -> str:
    """The fallback command. The connection comes from libpq's environment (PGHOST, PGPORT, PGUSER,
    PGDATABASE, and PGPASSWORD or ~/.pgpass), never from a URL on the command line: a password in
    argv shows in process lists and logs."""
    return (f"umask 077; PGOPTIONS='{PGOPTIONS}' psql -X -q -v ON_ERROR_STOP=1 "
            f"-f {shlex.quote(str(sql_path))}")


def split_url(url: str) -> tuple[str, dict]:
    """(the URL without its password, extra env). The password goes to psql as PGPASSWORD, never in argv."""
    parts = urllib.parse.urlsplit(url)
    env = {}
    if parts.password is not None:
        env["PGPASSWORD"] = urllib.parse.unquote(parts.password)
        user = parts.username or ""
        host = parts.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        netloc = (f"{user}@" if user else "") + host + (f":{parts.port}" if parts.port else "")
        parts = parts._replace(netloc=netloc)
    return urllib.parse.urlunsplit(parts), env


def check(paths: dict[str, Path]) -> int:
    """Read back what was written: counts and paths only, never content."""
    missing = 0
    for entity, path in paths.items():
        if not path.exists():
            print(f"  {entity}: MISSING {path}")
            missing += 1
            continue
        os.chmod(path, 0o600)
        data = json.loads(path.read_text(encoding="utf-8"))
        ok = data.get("version") == VERSION and data.get("entity") == entity and isinstance(data.get("rows"), list)
        print(f"  {entity}: {len(data.get('rows') or [])} rows -> {path}" + ("" if ok else "  (UNEXPECTED SHAPE)"))
        missing += not ok
    return missing


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--uid-file", required=True, help="0600 file holding the user's uid (never typed in chat)")
    ap.add_argument("--out-dir", required=True, help="private folder for the .sql and the JSON files (created 0700)")
    ap.add_argument("--sections", default=",".join(SECTIONS), help=f"comma list from {', '.join(SECTIONS)}")
    ap.add_argument("--print-only", action="store_true", help="write the .sql and print the command; don't connect")
    ap.add_argument("--check", metavar="STAMP", help="read back the files of an earlier run (its timestamp)")
    args = ap.parse_args(argv)

    sections = [s.strip() for s in args.sections.split(",") if s.strip()]
    unknown = [s for s in sections if s not in SECTIONS]
    if unknown or not sections:
        raise SystemExit(f"Unknown section(s): {', '.join(unknown) or '(none)'}.")
    uid_file = Path(args.uid_file).expanduser().resolve()
    uid = read_uid(uid_file)
    out_dir = Path(args.out_dir).expanduser().resolve()
    if not out_dir.exists():
        out_dir.mkdir(parents=True, mode=0o700)
    os.chmod(out_dir, 0o700)

    if args.check:
        paths = {s: out_dir / f"{file_prefix(uid)}-{s}-{args.check}.json" for s in sections}
        return 1 if check(paths) else 0

    now = datetime.datetime.now(datetime.timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    paths = plan(uid, out_dir, sections, stamp)
    sql_path = out_dir / f"{file_prefix(uid)}-query-{stamp}.sql"
    private_write(sql_path, build_sql(uid_file, paths, now.strftime("%Y-%m-%dT%H:%M:%S.000Z"), uid_prefix(uid)))

    url = os.environ.get(URL_ENV)
    if args.print_only or not url:
        why = "--print-only" if args.print_only else f"${URL_ENV} is not set"
        print(f"Not connecting ({why}). Someone with read access to the IntelligenceService database runs:")
        print(f"  {command(sql_path)}")
        print(f"then: python3 {Path(__file__).name} --uid-file <same file> --out-dir {out_dir} "
              f"--sections {','.join(sections)} --check {stamp}")
        return 3

    old = os.umask(0o077)
    try:
        safe_url, secret = split_url(url)
        done = subprocess.run(["psql", safe_url, "-X", "-q", "-v", "ON_ERROR_STOP=1", "-f", str(sql_path)],
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              env=dict(os.environ, PGOPTIONS=PGOPTIONS, **secret))
    finally:
        os.umask(old)
    if "NO_SUCH_USER" in (done.stdout or ""):
        raise SystemExit("No user row for that uid: nothing was written. Check the uid file.")
    if done.returncode != 0:
        first = (done.stderr or "").strip().splitlines()[:1]
        reason = (first[0] if first else f"exit {done.returncode}").replace(uid, "<uid>")
        for value in secret.values():
            reason = reason.replace(value, "<secret>")
        for p in paths.values():
            p.unlink(missing_ok=True)
        print(f"The read failed ({reason[:200]}). No other credential is tried. Someone with access runs:")
        print(f"  {command(sql_path)}")
        print(f"then --check {stamp}")
        return 2
    print(f"Read-only export written ({len(paths)} sections):")
    return 1 if check(paths) else 0


if __name__ == "__main__":
    sys.exit(main())
