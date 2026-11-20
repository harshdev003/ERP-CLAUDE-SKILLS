#!/usr/bin/env python3
"""Apply an approved harvest plan to the Learnings/Aurie Cost Engineering page.

Write side of eng:harvest-cost-changes. Takes the plan JSON the skill built
(and the user approved) and performs the Notion writes:

- append new rows to the Cost Changes ledger database
- update properties on existing rows (e.g. Idea -> Shipped, fill Date/Refs)
- rewrite headline-metric / narrative blocks in place
- stamp the page footer with "Last harvested: YYYY-MM-DD"
- invalidate the local AI Knowledge cache for this page

Guards:
- Area / Status values are validated against the ledger's select options —
  an unknown value aborts before any write (so a typo can't silently create
  a new select option in Notion).
- New rows are re-checked against the live ledger right before the write:
  a row whose normalized Change title already exists is skipped, so re-running
  an applied plan is a no-op. A row sharing an identifier-shaped Refs token
  (PROD-1234, IntelligenceService#409) with an existing row is skipped only when the two titles
  also say the same thing (title_similarity >= TITLE_DUP_SIMILARITY); otherwise
  it is added with a SHARED REFS line naming the row it shares them with.

Usage:
  python3 apply_updates.py --plan /tmp/cost_harvest_plan.json [--dry-run]
  python3 apply_updates.py --self-test     # Refs dedup regression cases

Plan JSON schema: see references/page-structure.md in this skill.
"""

import argparse
import datetime as dt
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from notion_rest import (  # noqa: E402
    make_date,
    make_rich_text,
    make_select,
    make_title,
    notion,
    rich_text,
)

PAGE_ID = "37b75e23af208189b4e2de18e56546e0"
COST_CHANGES_DB_ID = "37b75e23af2081e2b6cbc58e36eaa69c"
AREAS = {"LLM", "Voice", "Limits", "Tooling", "Process"}
STATUSES = {"Shipped", "Reverted", "In progress", "Idea"}
WATERMARK_RE = re.compile(r"Last harvested: \d{4}-\d{2}-\d{2}")

# Dedup on identifier-shaped Refs tokens only. Splitting the whole field on
# punctuation made a URL tokenize into generic parts ("https:", a hostname,
# "archives", a Slack channel id), so any row citing a permalink collided with
# any other row that also cited one, and got skipped as a duplicate. The skip
# reported as "1 skipped", which reads like the guard working.
#
# Two shapes count, and nothing else:
#   Linear keys   PROD-4078, ENGBACK-1252, ENGPROM-46   uppercase prefix only
#   GitHub refs   IntelligenceService#409, ACA#1331, AINative#712   any case, needs a repo
#
# Uppercase-only on the dash form keeps prose out ("top-10" in a Refs comment is
# not an identifier). Bare PR refs (#376) are excluded: without a repo prefix
# they collide across repos. Version pins (v1.25.4, 0.287.0) are excluded too —
# three ledger rows legitimately cite the same release.
REFS_ID_RE = re.compile(r"\b(?:[A-Z][A-Z0-9]*-\d+|[A-Za-z][A-Za-z0-9]*#\d+)\b")
# Older rows wrote IntelligenceService PRs as IS#<n>; both spellings are one ref.
REPO_ALIASES = {"IS": "INTELLIGENCESERVICE"}


# Related changes cite the same parent ticket all the time (five distinct rows
# carry PROD-7290), so a shared ref alone is not evidence of a duplicate: it
# dropped approved rows twice, reported as "skipped". A shared ref skips only
# when the titles also overlap this much. On the live ledger (2026-10-23) the
# 35 pairs of distinct rows sharing a ref score at most 0.45; a reworded title
# for the same change ("Reduce/Trimmed context instructions") scores 0.5.
TITLE_DUP_SIMILARITY = 0.5
TITLE_STOPWORDS = frozenset(
    "a an the and or of to for in on at by with from into after before per its it "
    "is as vs via not no only once when than then this that be are was were".split()
)


def norm_title(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def title_words(s: str) -> set:
    return {w for w in norm_title(s).split() if len(w) > 1 and w not in TITLE_STOPWORDS}


def title_similarity(a: str, b: str) -> float:
    """Jaccard overlap of the two titles' content words, 0.0-1.0."""
    wa, wb = title_words(a), title_words(b)
    return len(wa & wb) / len(wa | wb) if wa | wb else 0.0


def dedup_verdict(change: str, refs: str, ledger: list):
    """Decide whether a new row duplicates one in `ledger` [(title, refs_tokens)].

    Returns ("title", title) for an exact normalized-title match, ("refs", title,
    shared, score) when it shares refs with a row whose title is similar enough,
    ("shared", [(title, shared, score), ...]) when it only shares refs with
    dissimilar rows (add it, but say so), or ("new",).
    """
    key = norm_title(change)
    for title, _ in ledger:
        if norm_title(title) == key:
            return ("title", title)
    tokens = refs_tokens(refs)
    overlaps = []
    for title, toks in ledger:
        shared = tokens & toks
        if shared:
            overlaps.append((title, sorted(shared), title_similarity(change, title)))
    for title, shared, score in sorted(overlaps, key=lambda o: -o[2]):
        if score >= TITLE_DUP_SIMILARITY:
            return ("refs", title, shared, score)
    return ("shared", overlaps) if overlaps else ("new",)


def refs_tokens(s: str) -> set:
    """Identifier-shaped tokens from a Refs value, uppercased for comparison.

    Returns an empty set for a Refs carrying no ticket or PR reference (e.g.
    "#ai-cost 2026-08-02" or a bare Slack permalink) — such a row is deduped on
    its title alone rather than on incidental word overlap.
    """
    out = set()
    for m in REFS_ID_RE.finditer(s or ""):
        tok = m.group(0).upper()
        repo, sep, num = tok.partition("#")
        out.add(f"{REPO_ALIASES.get(repo, repo)}#{num}" if sep else tok)
    return out


def load_ledger():
    db = notion._request("GET", f"/databases/{COST_CHANGES_DB_ID}")
    ds_id = db["data_sources"][0]["id"]
    ledger = []
    for r in notion.query_data_source(ds_id, page_size=100):
        props = r["properties"]
        title = "".join(
            t["plain_text"] for t in props.get("Change", {}).get("title", [])
        )
        refs = "".join(
            t["plain_text"] for t in props.get("Refs", {}).get("rich_text", [])
        )
        ledger.append((title, refs_tokens(refs)))
    return ds_id, ledger


def validate(plan: dict) -> list:
    errors = []
    for i, row in enumerate(plan.get("new_rows", [])):
        if not row.get("change"):
            errors.append(f"new_rows[{i}]: missing 'change'")
        if row.get("area") not in AREAS:
            errors.append(f"new_rows[{i}]: area {row.get('area')!r} not in {sorted(AREAS)}")
        if row.get("status") not in STATUSES:
            errors.append(f"new_rows[{i}]: status {row.get('status')!r} not in {sorted(STATUSES)}")
    for i, upd in enumerate(plan.get("row_updates", [])):
        if not upd.get("row_id"):
            errors.append(f"row_updates[{i}]: missing 'row_id'")
        props = upd.get("props", {})
        if "Area" in props and props["Area"] not in AREAS:
            errors.append(f"row_updates[{i}]: Area {props['Area']!r} invalid")
        if "Status" in props and props["Status"] not in STATUSES:
            errors.append(f"row_updates[{i}]: Status {props['Status']!r} invalid")
    for i, blk in enumerate(plan.get("block_updates", [])):
        if not blk.get("block_id") or not blk.get("type"):
            errors.append(f"block_updates[{i}]: needs 'block_id' and 'type'")
    return errors


def build_row_properties(row: dict) -> dict:
    # Title keyed as "title" (lowercase) on creates — the title-by-ID gotcha,
    # see CLAUDE.md -> "Notion REST integration".
    props = {
        "title": make_title(row["change"]),
        "Area": make_select(row["area"]),
        "Status": make_select(row["status"]),
    }
    if row.get("date"):
        props["Date"] = make_date(row["date"])
    if row.get("refs"):
        props["Refs"] = make_rich_text(row["refs"])
    if row.get("impact"):
        props["Impact"] = make_rich_text(row["impact"])
    return props


def build_update_properties(props: dict) -> dict:
    out = {}
    for key, value in props.items():
        if key in ("Area", "Status"):
            out[key] = make_select(value)
        elif key == "Date":
            out[key] = make_date(value)
        elif key in ("Refs", "Impact"):
            out[key] = make_rich_text(value)
        elif key == "Change":
            out[key] = make_title(value)
        else:
            raise SystemExit(f"row_updates: unsupported property {key!r}")
    return out


def stamp_watermark(date_str: str, dry_run: bool) -> str:
    """Set 'Last harvested: <date>' on the page footer paragraph."""
    footer = None
    for b in notion.get_block_children(PAGE_ID):
        if b["type"] != "paragraph":
            continue
        text = "".join(
            t.get("plain_text", "")
            for t in b["paragraph"].get("rich_text", [])
        )
        if "Compiled" in text or "Last harvested" in text:
            footer = (b["id"], text)
    stamp = f"Last harvested: {date_str} (eng:harvest-cost-changes)."
    if footer is None:
        if not dry_run:
            notion.append_block_children(
                PAGE_ID, [{"object": "block", "type": "paragraph",
                           "paragraph": {"rich_text": rich_text(stamp)}}]
            )
        return "footer created"
    block_id, text = footer
    if WATERMARK_RE.search(text):
        new_text = WATERMARK_RE.sub(f"Last harvested: {date_str}", text)
    else:
        new_text = text.rstrip() + " " + stamp
    if not dry_run:
        notion.update_block(block_id, {"paragraph": {"rich_text": rich_text(new_text)}})
    return "footer updated"


def invalidate_cache() -> int:
    """Delete-only cache invalidation (retrieve-knowledge cache spec)."""
    cache_dir = os.environ.get(
        "AI_KNOWLEDGE_CACHE_DIR",
        os.path.expanduser("~/.cache/ai-knowledge"),
    )
    removed = 0
    for path in glob.glob(os.path.join(cache_dir, "learnings__*")):
        if "cost" in os.path.basename(path).lower():
            try:
                os.remove(path)
                removed += 1
            except OSError:
                pass  # invalidation must not fail the write
    return removed


def get_block(block_id: str):
    """Fetch a single block live; return None if it's been deleted (404).

    Used as a pre-check before a block update so a concurrent archive/delete
    (the page is curated by several sessions at once) is handled gracefully
    instead of crashing on Notion's archived-block 400. `notion_rest.py` is a
    byte-identical consumer copy of the canonical, so this helper lives here
    rather than as a method on the client.
    """
    import requests

    try:
        return notion._request("GET", f"/blocks/{block_id}")
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return None
        raise


def self_test() -> int:
    """Regression cases for the Refs dedup tokenizer. Run with --self-test."""
    # (label, refs, expected tokens)
    extract = [
        ("URL only — the ENG-1031 regression",
         "Slack #ai-cost 2026-08-02 — "
         "https://auramobile.slack.com/archives/C0B3GN41MDX/p1785185496876159",
         set()),
        ("channel + date, no identifier", "#ai-cost 2026-08-02", set()),
        ("Linear keys + GitHub ref",
         "PROD-4078 · PROD-4081 · ACA#1331",
         {"PROD-4078", "PROD-4081", "ACA#1331"}),
        ("mixed-case repo ref",
         "ENG-991, ENG-992, AINative#712", {"ENG-991", "ENG-992", "AINATIVE#712"}),
        ("version pins are not identifiers", "PROD-1756, v1.25.4", {"PROD-1756"}),
        ("release numbers are not identifiers",
         "ENGBACK-1236 (0.287.0) · ENGBACK-1246 (PR #376, 0.287.1)",
         {"ENGBACK-1236", "ENGBACK-1246"}),
        ("lowercase prose is not an identifier",
         "PROD-3244 (top-10 cost-user profiling, Jul 3-9)", {"PROD-3244"}),
        ("Linear URL still yields its key",
         "https://linear.app/aura-health/issue/PROD-1234/some-slug", {"PROD-1234"}),
    ]
    # (label, new-row refs, existing-ledger refs, should the row be skipped?)
    collide = [
        ("URL vs unrelated URL must NOT collide",
         "Slack #ai-cost — https://auramobile.slack.com/archives/C0B3GN41MDX/p178",
         "https://auramobile.slack.com/archives/C0B3GN41MDX/p1782843647586269",
         False),
        ("same Linear key must still collide", "PROD-2525 follow-up", "PROD-2525", True),
        ("same GitHub ref must still collide", "IS#409 revert", "ENGBACK-1270 · IS#409", True),
        ("IS# and IntelligenceService# are one ref", "IntelligenceService#409", "ENGBACK-1270 · IS#409", True),
        ("different Linear keys must not collide", "PROD-2525", "PROD-2526", False),
    ]

    failures = 0
    for label, refs, want in extract:
        got = refs_tokens(refs)
        ok = got == want
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'} extract: {label}\n"
              f"       got={sorted(got)} want={sorted(want)}")
    for label, new_refs, existing_refs, want_skip in collide:
        got_skip = bool(refs_tokens(new_refs) & refs_tokens(existing_refs))
        ok = got_skip == want_skip
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'} collide: {label} "
              f"(skip={got_skip}, want={want_skip})")
    print("self-test: " + ("all passed" if not failures else f"{failures} FAILED"))
    return 1 if failures else 0


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--plan", help="Path to approved plan JSON")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--self-test", action="store_true",
                    help="Run the Refs dedup regression cases and exit")
    args = ap.parse_args()

    if args.self_test:
        raise SystemExit(self_test())
    if not args.plan:
        ap.error("--plan is required (or pass --self-test)")

    with open(args.plan, encoding="utf-8") as f:
        plan = json.load(f)

    errors = validate(plan)
    if errors:
        for e in errors:
            print(f"INVALID: {e}")
        raise SystemExit(1)

    if not notion.available:
        raise SystemExit("NOTION_TOKEN is not set — cannot apply.")

    ds_id, ledger = load_ledger()

    added = updated = skipped = shared_refs = blocks = 0
    for row in plan.get("new_rows", []):
        verdict = dedup_verdict(row["change"], row.get("refs", ""), ledger)
        if verdict[0] == "title":
            print(f"SKIP (title exists): {row['change']}")
            skipped += 1
            continue
        if verdict[0] == "refs":
            _, title, shared, score = verdict
            print(f"SKIP (refs {shared} + similar title {score:.2f} to {title!r}): "
                  f"{row['change']}")
            skipped += 1
            continue
        if verdict[0] == "shared":
            shared_refs += 1
            overlaps = verdict[1]
            refs = sorted({r for _, shared, _ in overlaps for r in shared})
            closest, _, score = max(overlaps, key=lambda o: o[2])
            print(f"SHARED REFS {refs} with {len(overlaps)} row(s), closest {closest!r} "
                  f"(title similarity {score:.2f}) — distinct change, adding: {row['change']}")
        if args.dry_run:
            print(f"DRY-RUN add row: {row['change']} [{row['area']}/{row['status']}]")
        else:
            notion.create_database_row(ds_id, build_row_properties(row))
            print(f"ADDED: {row['change']} [{row['area']}/{row['status']}]")
        ledger.append((row["change"], refs_tokens(row.get("refs", ""))))
        added += 1

    for upd in plan.get("row_updates", []):
        if args.dry_run:
            print(f"DRY-RUN update row {upd['row_id']}: {upd['props']}")
        else:
            notion.update_page_props(upd["row_id"], build_update_properties(upd["props"]))
            print(f"UPDATED row {upd['row_id']}: {sorted(upd['props'])}")
        updated += 1

    block_skipped = 0
    for blk in plan.get("block_updates", []):
        # Concurrency guard: this page is curated by multiple sessions at once,
        # so a block id captured in the (possibly stale) source fetch may have
        # been archived/replaced by a parallel edit before we apply. Pre-check
        # the live block and skip archived / missing ones with a clear note
        # rather than letting Notion's 400 ("Can't edit block that is archived")
        # crash the run — a crash here would also strand the watermark stamp and
        # cache invalidation below. Observed 2026-06-12 (PROD-2695).
        live = get_block(blk["block_id"])
        if live is None:
            print(f"BLOCK SKIP (not found — likely archived by a concurrent edit; "
                  f"re-fetch sources and re-plan): {blk['block_id']}")
            block_skipped += 1
            continue
        if live.get("archived") or live.get("in_trash"):
            print(f"BLOCK SKIP (archived by a concurrent edit; re-fetch sources and "
                  f"re-plan): {blk['block_id']}")
            block_skipped += 1
            continue
        if args.dry_run:
            print(f"DRY-RUN update block {blk['block_id']}: {blk['text'][:80]}")
            blocks += 1
            continue
        try:
            notion.update_block(
                blk["block_id"],
                {blk["type"]: {"rich_text": rich_text(blk["text"])}},
            )
            print(f"BLOCK updated: {blk['block_id']}")
            blocks += 1
        except Exception as exc:  # noqa: BLE001 — one bad block must not abort the run
            print(f"BLOCK SKIP (update failed: {exc}): {blk['block_id']}")
            block_skipped += 1

    watermark = plan.get("watermark_date") or dt.date.today().isoformat()
    note = stamp_watermark(watermark, args.dry_run)

    removed = 0 if args.dry_run else invalidate_cache()

    mode = "DRY-RUN " if args.dry_run else ""
    print(
        f"{mode}done: {added} rows added ({shared_refs} sharing refs with an existing "
        f"row), {updated} rows updated, {skipped} skipped, {blocks} blocks updated, {block_skipped} blocks skipped, "
        f"{note} (watermark {watermark}), {removed} cache file(s) invalidated"
    )


if __name__ == "__main__":
    main()
