"""Post pending ideas from the Notion `Learnings/Loop/<Loop>` database to Slack.

For each row with ``Status = pending`` and ``Slack thread`` empty:

1. Render a top-level Slack message.
2. POST it via ``chat.postMessage``.
3. Fetch the public permalink.
4. PATCH the row in Notion with the permalink.

Idempotent — rows that already have a ``Slack thread`` URL are skipped.
Each post is its own top-level message so replies can thread per-idea
(matches the previous AuraConsumerApp `ideas_share.py` behaviour for the
``#ai-ideas`` channel).

Environment
-----------
``SLACK_BOT_TOKEN``           — xoxb-... bot token (required for posting).
``NOTION_TOKEN``              — required to read pending rows + write back URL.

The destination is the loop's own channel ``#ai-loop-aurie-features``
(``C0C1JTK3YCC``), hardcoded per Architecture Principle 1 — see ``Tools/Slack`` in
AI Knowledge for the canonical channel directory. ``#ai-ideas`` (``C0ATUL8T936``)
is now the **read-only archive** of the April 2026 threads: those rows carry a
``Slack thread`` URL already, and the pending-and-unposted filter excludes them,
so nothing is ever re-posted into the new channel.

Slack calls go through the shared ``slack_post`` helper (copied into this skill's
``scripts/`` dir — see AINative ``CLAUDE.md`` → "Canonical source for
``slack_post.py``"), which owns transport (requests, curl fallback; never urllib)
and auth.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from typing import Any, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from notion_rest import (  # noqa: E402
    MCPFallback,
    make_select,
    notion,
    rich_text_value,
    select_value,
    title_text,
)
from slack_post import get_permalink, post_message, upload_files  # noqa: E402


AURIE_FEATURES_DS = "8d14f3eb-0c55-4f25-9322-ab2b2a69f092"

# One channel per loop. Siblings exist and are wired as their loops come online:
# #ai-loop-aura-features C0C0ZSMNZM5, #ai-loop-aurie-conversations C0C29JPEUQG,
# #ai-loop-aura-recommendations C0C0ZSMU7C7. See Tools/Slack in AI Knowledge.
LOOP_CHANNEL_ID = "C0C1JTK3YCC"     # #ai-loop-aurie-features — default
ARCHIVE_CHANNEL_ID = "C0ATUL8T936"  # #ai-ideas — April 2026 archive, never posted to

# Each loop posts to its own channel. Keyed by data source so the destination is
# derived from the database being shared FROM, not passed alongside it: a caller
# that gets --data-source-id right cannot then post Aura ideas into the Aurie
# channel. --channel overrides for a loop not yet listed here.
AURA_RECOMMENDATIONS_DS = "3e375e23-af20-815b-80d5-000ba798e625"
CHANNEL_BY_DATA_SOURCE = {
    AURIE_FEATURES_DS: LOOP_CHANNEL_ID,
    AURA_RECOMMENDATIONS_DS: "C0C0ZSMU7C7",  # #ai-loop-aura-recommendations
}


def resolve_channel(data_source_id: str, override: str = "") -> str:
    """Which channel this run posts to.

    Explicit --channel wins; otherwise the loop's own channel; otherwise the
    Aurie default. An unknown data source with no override is a real risk of
    cross-posting, so the caller is warned rather than silently defaulted.
    """
    if override:
        return override
    if data_source_id in CHANNEL_BY_DATA_SOURCE:
        return CHANNEL_BY_DATA_SOURCE[data_source_id]
    print(
        f"WARNING: no channel mapped for data source {data_source_id}; "
        f"falling back to {LOOP_CHANNEL_ID} (#ai-loop-aurie-features). "
        f"Pass --channel to post elsewhere.",
        file=sys.stderr,
    )
    return LOOP_CHANNEL_ID

EFFORT_LONG = {"S": "Small", "M": "Medium", "L": "Large"}

# The channel intro / topic carries this. It is NOT repeated in every post any more:
# under each idea it buried the idea (Jerome, 2026-09-17). Kept here as the canonical
# wording for the channel topic and for the docs.
#
# The wording is load-bearing in two ways.
#
# There are two gates: approve to build, then approve to release after trying it
# in TestFlight. A bare "yes" cannot say which one it means, and the second gate
# arrives weeks after the first on the same thread.
#
# And the decision is a REPLY, never a reaction. Reactions mark endings —
# :white_check_mark: is done, :x: is abandoned — so a decision expressed as one
# is ambiguous at best. :+1: is not a decision at all: the harvester reads it as
# sentiment, and in the April backlog five ideas stalled because a thumbs-up read
# as approval to the person leaving it and as nothing to the machine.
# Slack caps a channel topic at 250 characters, and this string IS the topic — so it is
# written to fit, and `TOPIC_MAX` holds it there. What had to go to make room for the
# marks: "it gets redesigned in this thread" (the ask is still named) and "AI harvests
# this thread daily" (visible in the thread itself, every day).
TOPIC_MAX = 250

DECISION_PROTOCOL = (
    "Reply to decide — Steve's counts: *I approve for implementation* / *no because …* / "
    "*later*. Like the idea but not the design? Say so. :+1: is not a decision. "
    "The loop marks the thread: :rocket: approved · :x: closed · :white_check_mark: done."
)

# Slack rate limit is ~1 msg/sec per channel. 1.2s gives a small safety margin
# for bursts; matches the cadence the old repo-side share script used.
POST_INTERVAL_SECONDS = 1.2


def _idea_id_str(row: dict[str, Any]) -> str:
    """The row's human-friendly address ('IDEA-42'), or '' if unset. Minted by
    Notion's auto `Idea ID` — the id the thread should carry, not the fingerprint."""
    uid = (row.get("properties", {}).get("Idea ID") or {}).get("unique_id") or {}
    num = uid.get("number")
    if num is None:
        return ""
    prefix = uid.get("prefix")
    return f"{prefix}-{num}" if prefix else str(num)


def _row_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "page_id": row["id"],
        "idea_id": _idea_id_str(row),
        "title": title_text(row, "Title"),
        "status": select_value(row, "Status"),
        "screen": rich_text_value(row, "Screen"),
        "category": rich_text_value(row, "Category"),
        "lever": rich_text_value(row, "Lever"),
        "impact": select_value(row, "Impact"),
        "effort": select_value(row, "Effort"),
        "description": rich_text_value(row, "Description"),
        "rationale": rich_text_value(row, "Rationale"),
        "hypothesis": rich_text_value(row, "Hypothesis"),
        "fingerprint": rich_text_value(row, "Fingerprint"),
        "slack_thread": (row.get("properties", {}).get("Slack thread") or {}).get("url") or "",
        "screenshot_url": _first_file_url(row, "Screenshot"),
        "screenshot_urls": _file_urls(row, "Screenshot"),
        "screenshot_files": _files(row, "Screenshot"),
    }


def _files(row: dict[str, Any], prop: str) -> list[tuple[str, str]]:
    """`(name, url)` for every file on a `files` property, in row order.

    Notion signs these URLs and they expire within the hour, which is fine — the
    only consumer downloads them immediately to hand to Slack. The design leg puts
    the Current-vs-Proposed sheet first, then each variant, so order is meaning —
    and it names each file by its variant label, which becomes the reply's caption.
    """
    files = ((row.get("properties", {}).get(prop) or {}).get("files")) or []
    out = []
    for f in files:
        url = (f.get("file") or f.get("external") or {}).get("url", "")
        if url:
            out.append((f.get("name") or "", url))
    return out


def _file_urls(row: dict[str, Any], prop: str) -> list[str]:
    """URLs only — see `_files`."""
    return [u for _, u in _files(row, prop)]


def _files_to_post(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Which of the row's files reach the thread. The combined *Current vs Proposed*
    sheet carries every single-screen variant, so those get no reply of their own; a
    variant that is a **flow** (several screens after a tap) is its own reply. The
    other skies and the bare current capture stay on the row for the Linear card
    (Jerome, 2026-09-30). A row with none of the named files posts what it has."""
    main = [p for p in pairs if p[0] == "Current vs Proposed.png"]
    flows = [p for p in pairs if p[0].startswith("Flow ")]
    if not main and not flows:
        return pairs
    return main + flows


def _caption_for(name: str) -> str:
    """The reply caption for one design file: its name without the extension.

    `design-idea attach` names files by label — `a · Trust badge, above the orb.png` —
    so the caption is already the human sentence. A bare stem like `idea1_c.png` is
    turned into `Variant c` rather than shown raw.
    """
    import re  # noqa: PLC0415
    stem = os.path.splitext(os.path.basename(name or ""))[0]
    m = re.fullmatch(r"idea\d+_([a-z])", stem, re.I)
    if m:
        return f"Variant {m.group(1).lower()}"
    if re.fullmatch(r"idea\d+_compare", stem, re.I):
        return "Current vs Proposed"
    if re.fullmatch(r"idea\d+_current", stem, re.I):
        return "Current · control (b)"
    return stem or "Design"


def _first_file_url(row: dict[str, Any], prop: str) -> str:
    """URL of the first file on a `files` property, or ''."""
    urls = _file_urls(row, prop)
    return urls[0] if urls else ""


def render_message(idea: dict[str, Any]) -> str:
    """The top post: what the idea is, readable in the channel at a glance.

    Deliberately short. The reasoning ("why it lifts the lever") and the hypothesis
    are posted as the first two replies (`render_replies`) rather than inlined, so
    the parent stays a summary a reviewer can take in without scrolling and each
    part can be discussed on its own. The Current-vs-Proposed sheet, when the row
    has one, is uploaded into the thread right after.
    """
    screen = idea.get("screen") or "?"
    title = idea.get("title") or "(untitled)"
    # The address the team refers to the idea by in this thread. Falls back to the
    # fingerprint for any pre-id row that somehow reaches here.
    idea_ref = idea.get("idea_id") or idea.get("fingerprint") or "?"
    effort_short = idea.get("effort") or "?"
    effort = EFFORT_LONG.get(effort_short, effort_short)
    impact = (idea.get("impact") or "?").capitalize()
    lines = [
        f"*{idea_ref} · {title}*",
        f"`{screen}` · {idea.get('category') or '?'} · {idea.get('lever') or '?'} · "
        f"Impact: {impact} · Effort: {effort}",
        "",
        "*The idea*",
        idea.get("description") or "",
        "",
    ]
    # Nothing else. No decision protocol (the channel intro carries it; repeating it
    # under every idea buried the idea), no trailing `_Idea IDEA-N_` (the header
    # already is the address), and no line announcing the pictures — they are the very
    # next thing in the thread, so a sentence saying so is one more line between the
    # reader and the idea. Jerome's call, 2026-09-17 and 2026-09-30.
    return "\n".join(lines).rstrip()


def render_replies(idea: dict[str, Any]) -> list[str]:
    """The two threaded replies that follow the top post: the reasoning, then the
    hypothesis. Each is its own message so a reviewer can answer one without
    quoting the other. An empty field posts nothing rather than an empty header."""
    out = []
    if idea.get("rationale"):
        out.append(f"*Why it lifts the lever*\n{idea['rationale']}")
    if idea.get("hypothesis"):
        out.append(f"*Hypothesis*\n{idea['hypothesis']}")
    return out


# ---------- pending fetch ----------------------------------------------------

def _idea_number(row: dict[str, Any]) -> int:
    """The number in `IDEA-N`, for ordering; a row with no id sorts last."""
    ref = (row.get("idea_id") or "").upper().replace("IDEA-", "").strip()
    return int(ref) if ref.isdigit() else 10 ** 9


def _fetch_unposted(ds_id: str) -> list[dict[str, Any]]:
    """All rows with Status = pending and Slack thread unset, in Idea ID order — a
    batch reads as IDEA-1, IDEA-13, IDEA-19 … in the channel, not in whatever order
    Notion last touched the rows (Jerome, 2026-09-30)."""
    filter_ = {
        "and": [
            {"property": "Status", "select": {"equals": "pending"}},
            {"property": "Slack thread", "url": {"is_empty": True}},
        ],
    }
    rows = [_row_summary(r) for r in notion.query_data_source(ds_id, filter=filter_, page_size=100)]
    return sorted(rows, key=_idea_number)


def _attach_screenshots(channel: str, thread_ts: str, files: list[Any], fingerprint: str,
                        comment: Optional[str] = None) -> bool:
    """Download the Notion-hosted screenshots and post them into the idea's thread.

    Threaded replies rather than part of the parent message: the parent stays the
    canonical idea text that `harvest-idea-decisions` reads, and a failed upload
    cannot cost the post itself. The pictures are context, not the artefact.

    **One reply per posted file.** The combined Current-vs-Proposed sheet carries the
    intro and every single-screen arm; each `Flow …` file is its own reply captioned
    with its label. Per-arm images and the other skies are not posted — `_files_to_post`
    decides — they stay on the row for the Linear card. `files` is `(name, url)` pairs;
    bare URLs are accepted and captioned by position.
    """
    try:
        import requests  # noqa: PLC0415
    except ImportError:
        return False
    pairs = _files_to_post([(f if isinstance(f, tuple) else ("", f)) for f in files])
    ok_all = True
    for i, (name, url) in enumerate(pairs):
        if i == 0:
            text = comment or (
                "Current vs Proposed — the real screen with the change on it; "
                "b is the current screen (control)."
                if name.startswith("Current vs Proposed") else (_caption_for(name) if name else "Screen this was observed on."))
        else:
            text = _caption_for(name) if name else f"Variant {i}"
        tmp = ""
        # One try per file: a failed download or upload costs THAT reply, never the
        # ones after it — the parent is already `shared`, so a re-run will not come
        # back for the missing pictures. SystemExit is caught deliberately:
        # `upload_files` calls sys.exit() on a Slack error, and SystemExit derives
        # from BaseException, so a bare `except Exception` would let it unwind and
        # kill the rest of the loop.
        try:
            resp = requests.get(url, timeout=60)
            resp.raise_for_status()
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False,
                                             prefix=f"idea_{fingerprint}_{i}_") as fh:
                fh.write(resp.content)
                tmp = fh.name
            out = upload_files(channel, [tmp], thread_ts=thread_ts, comment=text)
            if not out.get("ok"):
                ok_all = False
                print(f"  ! upload of {name or url} not ok: {out.get('error')}", file=sys.stderr)
        except (Exception, SystemExit) as exc:  # noqa: BLE001
            ok_all = False
            print(f"  ! screenshot {i} ({name or url}) failed ({exc}); continuing", file=sys.stderr)
        finally:
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        if i < len(pairs) - 1:
            time.sleep(POST_INTERVAL_SECONDS)
    return ok_all


def _attach_screenshot(channel: str, thread_ts: str, url: str, fingerprint: str) -> bool:
    """Single-file form, kept for callers that have one URL."""
    return _attach_screenshots(channel, thread_ts, [url], fingerprint)


# ---------- subcommands ------------------------------------------------------

def cmd_preview(args: argparse.Namespace) -> int:
    if not notion.available:
        raise MCPFallback("NOTION_TOKEN is not set.")
    _channel = resolve_channel(args.data_source_id, getattr(args, "channel", ""))
    rows = _fetch_unposted(args.data_source_id)
    if not rows:
        print("No pending unposted ideas.")
        return 0
    for r in rows[: args.limit or len(rows)]:
        print("=" * 70)
        print(render_message(r))
        shot = "yes" if r.get("screenshot_url") else "none on the row"
        print(f"\n  [screenshot: {shot} · posts to {_channel} · Status -> shared]")
        print()
    print(f"{len(rows)} pending unposted, showing {min(args.limit or len(rows), len(rows))}.")
    return 0


def cmd_post_pending(args: argparse.Namespace) -> int:
    if not notion.available:
        raise MCPFallback("NOTION_TOKEN is not set.")
    channel = resolve_channel(args.data_source_id, getattr(args, "channel", ""))
    rows = _fetch_unposted(args.data_source_id)
    if not rows:
        print("No pending unposted ideas.")
        return 0

    target = rows[: args.limit] if args.limit else rows
    print(f"{len(rows)} pending unposted. About to post {len(target)} to channel {channel}.")

    if not args.confirm:
        print("Run with --confirm to actually post. Preview message bodies with `preview`.")
        return 0

    posted = 0
    failed = 0
    for i, idea in enumerate(target, 1):
        text = render_message(idea)
        print(f"\n[{i}/{len(target)}] posting {idea['fingerprint']}  {idea['title'][:60]}")
        resp = post_message(channel, text, footer_skill="product:share-ideas")
        if not resp.get("ok"):
            print(f"  ! Slack error: {resp.get('error')}", file=sys.stderr)
            failed += 1
            time.sleep(POST_INTERVAL_SECONDS)
            continue

        ts = resp["ts"]
        chan_id = resp.get("channel", channel)
        link = get_permalink(chan_id, ts)
        if not link.get("ok"):
            sys.exit(f"chat.getPermalink failed: {link.get('error')}")
        permalink = link.get("permalink", "")

        # Persist BEFORE the screenshot, never after. The message is already in
        # the channel by this point, so anything that can fail between the post
        # and this write risks a row that was posted but does not know it — and
        # the next run would post it again. Status -> shared in the same write as
        # the permalink: that pair is what makes a re-run a no-op, since the row
        # then fails both the pending filter and the empty-thread filter.
        notion.update_page_props(idea["page_id"], {
            "Slack thread": {"url": permalink},
            "Status": make_select("shared"),
        })
        print(f"  + posted ts={ts} permalink={permalink}")
        posted += 1

        # The reasoning and the hypothesis go in as the first two replies — the
        # parent stays a readable summary, and each can be discussed on its own.
        # No footer on these: the parent already carries the attribution for the
        # thread, and a footer under each reply is noise (Jerome, 2026-09-18).
        for body in render_replies(idea):
            time.sleep(POST_INTERVAL_SECONDS)
            rep = post_message(chan_id, body, thread_ts=ts)
            if not rep.get("ok"):
                print(f"  ! reply failed: {rep.get('error')}", file=sys.stderr)

        files = idea.get("screenshot_files") or idea.get("screenshot_urls") or (
            [idea["screenshot_url"]] if idea.get("screenshot_url") else [])
        if files and _attach_screenshots(chan_id, ts, files, idea["fingerprint"]):
            print(f"  + {len(files)} screenshot(s) posted, one reply each")

        if i < len(target):
            time.sleep(POST_INTERVAL_SECONDS)

    print(f"\nResult: {posted} posted, {failed} failed, {len(rows) - len(target)} not attempted")
    return 1 if failed else 0


def _thread_from_permalink(url: str) -> Optional[tuple[str, str]]:
    """`(channel_id, thread_ts)` from a Slack permalink, or None.

    `…/archives/C0C1JTK3YCC/p1789694236837849` → `("C0C1JTK3YCC", "1789694236.837849")`.
    The `p` form is the ts with the dot removed; the last six digits are the fraction.
    """
    import re  # noqa: PLC0415
    m = re.search(r"/archives/([A-Z0-9]+)/p(\d{10})(\d{6})", url or "")
    if not m:
        return None
    return m.group(1), f"{m.group(2)}.{m.group(3)}"


def _find_row(ds_id: str, ref: str) -> Optional[dict[str, Any]]:
    """Resolve `IDEA-N` (or a bare number) to its row summary."""
    num = ref.upper().replace("IDEA-", "").strip()
    if not num.isdigit():
        sys.exit(f"idea ref must look like IDEA-12, got {ref!r}")
    for r in notion.query_data_source(ds_id):
        uid = (r["properties"].get("Idea ID") or {}).get("unique_id") or {}
        if str(uid.get("number")) == num:
            return _row_summary(r)
    return None


def cmd_post_redesign(args: argparse.Namespace) -> int:
    """Post the row's current Screenshot files into its existing thread as a reply.

    This is the ONE place a redesign reaches Slack. The chain is: a reviewer says
    "like the idea, not the design" → harvest reads `needs_redesign` → the designer
    re-renders and `design-idea attach` replaces the row's Screenshot → this posts
    the new sheet into the thread. It is a bot reply, which is also what clears
    `needs_redesign` on the next harvest — a redesign nobody posted would otherwise
    be re-rendered every tick.

    The row's Status is not touched: a redesign is not a decision.
    """
    if not notion.available:
        raise MCPFallback("NOTION_TOKEN is not set.")
    idea = _find_row(args.data_source_id, args.idea)
    if not idea:
        print(f"{args.idea}: no row found", file=sys.stderr)
        return 2
    if idea["status"] != "shared":
        print(f"REFUSED: {args.idea} is '{idea['status']}', not 'shared' — a redesign only "
              f"makes sense while the thread is waiting on Gate 1.", file=sys.stderr)
        return 2
    where = _thread_from_permalink(idea.get("slack_thread") or "")
    if not where:
        print(f"REFUSED: {args.idea} has no Slack thread to reply in.", file=sys.stderr)
        return 2
    files = idea.get("screenshot_files") or []
    if args.flows_only:
        # A thread that asked for the screens after the tap already has the combined
        # sheet; only the flows are new.
        files = [f for f in files if (f[0] if isinstance(f, tuple) else "").startswith("Flow ")]
    else:
        # The same selection the upload applies, so the count the dry-run shows is
        # the count that posts.
        files = _files_to_post([(f if isinstance(f, tuple) else ("", f)) for f in files])
    if not files:
        print(f"REFUSED: {args.idea} has nothing on Screenshot — run `design-idea attach` first.",
              file=sys.stderr)
        return 2
    channel, ts = where
    print(f"{args.idea}: {len(files)} file(s) → one reply each in {channel} thread {ts}")
    if not args.confirm:
        print("Run with --confirm to actually post.")
        return 0
    ok = _attach_screenshots(
        channel, ts, files, idea["fingerprint"],
        comment=args.comment or (
            "The screens after the tap, as asked — one reply per flow; b stays the current screen."
            if args.flows_only else
            "Revised Current vs Proposed, as asked. Same idea — the design is what "
            "changed. Reply to decide, or say what still needs to move."))
    if not ok:
        return 1
    print("  + redesign posted")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="share_ideas",
        description="Post pending ideas to Slack and record the thread URL on the Notion row.",
    )
    p.add_argument(
        "--data-source-id",
        default=AURIE_FEATURES_DS,
        help="Notion data source ID (default: Aurie Features Ideas)",
    )
    p.add_argument(
        "--channel",
        default="",
        help="Slack channel to post to. Defaults to the loop's own channel, "
             "resolved from --data-source-id.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("preview", help="Print rendered Slack message bodies (no post)")
    sp.add_argument("--limit", type=int, help="Cap the preview to N rows")
    sp.set_defaults(func=cmd_preview)

    sp = sub.add_parser("post-pending", help="Post all pending unposted ideas to Slack")
    sp.add_argument("--limit", type=int, help="Cap the post batch to N rows")
    sp.add_argument(
        "--confirm",
        action="store_true",
        help="Required to actually post — otherwise this is a dry-run",
    )
    sp.set_defaults(func=cmd_post_pending)

    sp = sub.add_parser("post-redesign",
                        help="Post the row's current Screenshot files into its thread as a reply")
    sp.add_argument("--idea", required=True, help="IDEA-N")
    sp.add_argument("--confirm", action="store_true",
                    help="Required to actually post — otherwise this is a dry-run")
    sp.add_argument("--flows-only", action="store_true",
                    help="post only the row's `Flow …` files — the thread already has the sheet")
    sp.add_argument("--comment", help="the first reply's text (default depends on --flows-only)")
    sp.set_defaults(func=cmd_post_redesign)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except MCPFallback as e:
        print(f"share-ideas: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
