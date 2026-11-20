#!/usr/bin/env python3
"""Upload monthly rating chart PNGs to Slack with a summary comment.

Separate from `chart_ratings.py` on purpose: chart generation is deterministic
and side-effect-free; *posting* to Slack is an opt-in side effect the SKILL.md
only runs after the user approves the drafted summary (draft-and-confirm). Keep
the two concerns split so a dry run never posts.

Posting goes through the shared `slack_post` helper (copied into this skill's
`scripts/` dir — the canonical `shared:post-to-slack` library; see AINative
`CLAUDE.md` → "Canonical source for `slack_post.py`"). It runs Slack's modern
external-upload flow (files.getUploadURLExternal → POST bytes →
files.completeUploadExternal; files.upload is deprecated) and owns transport
(prefers `requests`/certifi, falls back to `curl`; never `urllib`).

Auth: SLACK_BOT_TOKEN (env, or --token). The bot must be a member of the channel
and hold `files:write` + `chat:write`. Forces UTF-8 stdout.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from slack_post import upload_files  # noqa: E402

DEFAULT_CHANNEL = "C07MB0TMF5J"  # #ai-data-intelligence


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(description="Post monthly rating charts to Slack.")
    ap.add_argument("--channel", default=DEFAULT_CHANNEL)
    ap.add_argument("--token", default=os.environ.get("SLACK_BOT_TOKEN"))
    ap.add_argument("--comment", required=True, help="initial_comment (the approved summary).")
    ap.add_argument("--png", action="append", required=True, metavar="PATH",
                    help="A chart PNG to attach. Repeat for multiple files.")
    ap.add_argument("--thread-ts", default=None, help="Reply in a thread instead of top-level.")
    args = ap.parse_args()

    for path in args.png:
        if not os.path.exists(path):
            raise SystemExit(f"PNG not found: {path}")

    resp = upload_files(
        args.channel, args.png, token=args.token,
        comment=args.comment, thread_ts=args.thread_ts,
        footer_skill="data:chart-monthly-ratings",
    )
    if not resp.get("ok"):
        raise SystemExit(f"Slack files.completeUploadExternal failed: {resp.get('error')}")

    # Surface the permalink of the first attached file for confirmation
    first = (resp.get("files") or [{}])[0]
    print(first.get("permalink", "posted (no permalink returned)"))


if __name__ == "__main__":
    main()
