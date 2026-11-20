#!/usr/bin/env python3
"""
Per-conversation transcript bookkeeping for the chat-with-aurie skill.

The transcript file lives at <outputs_dir>/<conversation_id>.json and is built
incrementally across the conversation lifecycle:

  init      — write metadata + first message + the /start response
  append    — record one user turn + Aurie's /ask response
  finalize  — record the /end response, stop reason, and final turn count;
              also renders the human-readable <conversation_id>.md
  render    — (re)write a clean, reviewable <conversation_id>.md from the JSON
  show      — print metadata, insight fields, and the full dialogue to stdout

The JSON at <outputs_dir>/<conversation_id>.json stays the raw-data artifact;
the .md is the human-readable transcript meant to be opened/linked for review.

Output directory:
  --outputs-dir <path>   (default: ./outputs, relative to cwd)

Always-redacted fields:
  No request headers or auth tokens are written. Only API response bodies
  (which the server already deemed safe to return), plus user-typed message
  text and metadata supplied by the caller.

Schema (final):
  {
    conversationId, environment, scenario, prompterName, createInsightCard,
    userId, firstMessage,
    startResponse, turnsLog: [{turn, user, aurie, askedAt, askResponse}, ...],
    endResponse, turns, stoppedBecause,
    initializedAt, finalizedAt
  }
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _path(outputs_dir: str, conv_id: str) -> str:
    os.makedirs(outputs_dir, exist_ok=True)
    return os.path.join(outputs_dir, f"{conv_id}.json")


def _read(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read().rstrip("\n")


def _read_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _extract_aurie_message(ask_body: dict) -> str | None:
    """
    Best-effort extraction of Aurie's reply text from a /ask response body.
    Prefers `message`, falls back to `markup`, then any string field that
    looks reply-shaped.
    """
    if not isinstance(ask_body, dict):
        return None
    for key in ("message", "markup", "text", "reply"):
        v = ask_body.get(key)
        if isinstance(v, str) and v.strip():
            return v
    return None


def _format_tool_calls(ask_body: dict) -> str | None:
    """Render a stop-turn `toolCalls` array as readable text.

    On the stop turn `body.message` is null and `body.toolCalls` carries the
    server's end-of-conversation signal (most often `createInsightCard`). The
    `function.arguments` is a JSON-encoded string, so parse it before showing
    the fields — e.g. `Aurie called createInsightCard(affirmation="…", trackId="…")`.
    """
    if not isinstance(ask_body, dict):
        return None
    tool_calls = ask_body.get("toolCalls")
    if not tool_calls:
        return None
    rendered: list[str] = []
    for tc in tool_calls:
        fn = (tc.get("function") or {}) if isinstance(tc, dict) else {}
        name = fn.get("name") or tc.get("name") or "?"
        raw_args = fn.get("arguments")
        if raw_args is None:
            raw_args = tc.get("arguments")
        args: dict = {}
        if isinstance(raw_args, str):
            try:
                args = json.loads(raw_args)
            except (ValueError, TypeError):
                args = {"_raw": raw_args}
        elif isinstance(raw_args, dict):
            args = raw_args
        arg_str = ", ".join(f'{k}={json.dumps(v, ensure_ascii=False)}' for k, v in args.items())
        rendered.append(f"_Aurie called_ `{name}({arg_str})`")
    return "\n\n".join(rendered) if rendered else None


def _aurie_turn_text(turn: dict) -> str:
    """Aurie's contribution for one turn: the message text and/or the stop-turn
    tool-call rendering.

    On the stop turn `body.message` is null but `markup`/`text` may still be
    populated, so `append` can store a non-empty `aurie` string for that turn.
    Returning it early would drop the `createInsightCard` tool-call rendering,
    so we always inspect `toolCalls` too and join both — message first, then the
    tool call — instead of preferring the cached string."""
    ask_body = (turn.get("askResponse") or {}).get("body", {}) if isinstance(turn.get("askResponse"), dict) else {}
    tool_str = _format_tool_calls(ask_body if isinstance(ask_body, dict) else {})
    aurie = turn.get("aurie")
    message = aurie if (isinstance(aurie, str) and aurie.strip()) else None
    parts = [p for p in (message, tool_str) if p]
    return "\n\n".join(parts) if parts else "_(no message)_"


def _insight_lines(data: dict) -> list[str]:
    """summary / followup / affirmation from the /end response, when present."""
    end_body = (data.get("endResponse") or {}).get("body", {}) or {}
    if not isinstance(end_body, dict):
        return []
    insight = end_body.get("insight") if isinstance(end_body.get("insight"), dict) else {}
    out: list[str] = []
    for label, val in (
        ("Summary", end_body.get("summary")),
        ("Follow-up", end_body.get("followup")),
        ("Affirmation", (insight or {}).get("affirmation")),
    ):
        if isinstance(val, str) and val.strip():
            out.append(f"- **{label}:** {val}")
    return out


# ---------- subcommands ---------------------------------------------------- #

def cmd_init(args: argparse.Namespace) -> None:
    """Create the transcript file with metadata, first message, and /start response."""
    out_path = _path(args.outputs_dir, args.conversation_id)
    if os.path.exists(out_path) and not args.force:
        sys.exit(
            f"error: transcript already exists at {out_path} "
            f"(pass --force to overwrite)"
        )

    start_resp = _read_json(args.start_response)
    start_body = start_resp.get("body", {}) if isinstance(start_resp, dict) else {}
    first_message = _read_text(args.first_message_file)

    data = {
        "conversationId": args.conversation_id,
        "environment": args.base_url,
        "scenario": args.scenario,
        "prompterName": args.prompter,
        "createInsightCard": args.create_insight_card,
        "userId": start_body.get("userId") if isinstance(start_body, dict) else None,
        "firstMessage": first_message,
        "startResponse": start_resp,
        "turnsLog": [],
        "endResponse": None,
        "turns": 0,
        "stoppedBecause": None,
        "initializedAt": _now_iso(),
        "finalizedAt": None,
    }
    _write(out_path, data)
    print(json.dumps({"ok": True, "path": out_path}))


def cmd_append(args: argparse.Namespace) -> None:
    """Append one user turn + Aurie's /ask response to the transcript."""
    out_path = _path(args.outputs_dir, args.conversation_id)
    if not os.path.exists(out_path):
        sys.exit(f"error: no transcript at {out_path} — run `init` first")

    data = _read(out_path)
    user_text = _read_text(args.user_message_file)
    ask_resp = _read_json(args.ask_response)
    ask_body = ask_resp.get("body", {}) if isinstance(ask_resp, dict) else {}

    aurie = _extract_aurie_message(ask_body if isinstance(ask_body, dict) else {})
    asked_at = ask_body.get("createdAt") if isinstance(ask_body, dict) else None

    data["turnsLog"].append({
        "turn": args.turn,
        "user": user_text,
        "aurie": aurie,
        "askedAt": asked_at,
        "askResponse": ask_resp,
    })
    data["turns"] = max(data["turns"], args.turn)
    _write(out_path, data)
    print(json.dumps({
        "ok": True,
        "turn": args.turn,
        "aurie_reply_chars": len(aurie or ""),
        "path": out_path,
    }))


def cmd_finalize(args: argparse.Namespace) -> None:
    """Write the /end response and close the transcript."""
    out_path = _path(args.outputs_dir, args.conversation_id)
    if not os.path.exists(out_path):
        sys.exit(f"error: no transcript at {out_path} — run `init` first")

    data = _read(out_path)
    end_resp = _read_json(args.end_response)
    data["endResponse"] = end_resp
    if args.stop_reason:
        data["stoppedBecause"] = args.stop_reason
    data["finalizedAt"] = _now_iso()
    _write(out_path, data)

    # Always render the human-readable transcript so a reviewable .md exists
    # after every normal run, not just when `render` is called explicitly.
    md_path = _render_markdown(args.outputs_dir, args.conversation_id, data)

    end_body = end_resp.get("body", {}) if isinstance(end_resp, dict) else {}
    print(json.dumps({
        "ok": True,
        "path": out_path,
        "transcript_md": md_path,
        "turns": data["turns"],
        "summary_present": bool(isinstance(end_body, dict) and end_body.get("summary")),
        "followup_present": bool(isinstance(end_body, dict) and end_body.get("followup")),
    }, indent=2))


def _render_markdown(outputs_dir: str, conv_id: str, data: dict) -> str:
    """Build outputs/<conversationId>.md — a clean, reviewable transcript."""
    end_body = (data.get("endResponse") or {}).get("body", {}) or {}
    stopped = data.get("stoppedBecause")
    if not stopped and isinstance(end_body, dict) and end_body.get("toolCalls"):
        stopped = "toolCalls"

    lines: list[str] = []
    lines.append(f"# Aurie conversation transcript — `{data.get('conversationId')}`\n")
    lines.append(f"- Environment: `{data.get('environment')}`")
    lines.append(f"- Scenario: {data.get('scenario')}")
    lines.append(f"- Prompter: `{data.get('prompterName')}`")
    lines.append(f"- createInsightCard: `{data.get('createInsightCard')}`")
    lines.append(f"- User id: `{data.get('userId')}`")
    lines.append(f"- Turns: `{data.get('turns')}`")
    lines.append(f"- Stopped because: `{stopped}`")
    first = data.get("firstMessage")
    if isinstance(first, str) and first.strip():
        # firstMessage is the user's opener registered on /start; it's the same
        # text as turn 1's user message, so it's metadata here, not its own turn.
        lines.append(f"- First message (user opener): {first}")
    lines.append(f"- Initialized: `{data.get('initializedAt')}` · Finalized: `{data.get('finalizedAt')}`")

    lines.append("\n---\n")
    for entry in data.get("turnsLog") or []:
        n = entry.get("turn")
        user = entry.get("user") or "_(none)_"
        lines.append(f"**Turn {n} — user:** {user}\n")
        lines.append(f"**Turn {n} — Aurie:** {_aurie_turn_text(entry)}\n")

    insight = _insight_lines(data)
    if insight:
        lines.append("\n## Insight\n")
        lines.extend(insight)

    md_path = os.path.join(outputs_dir, f"{conv_id}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return md_path


def cmd_render(args: argparse.Namespace) -> None:
    """Write outputs/<conversationId>.md from the JSON transcript."""
    out_path = _path(args.outputs_dir, args.conversation_id)
    if not os.path.exists(out_path):
        sys.exit(f"error: no transcript at {out_path} — run `init` first")
    data = _read(out_path)
    md_path = _render_markdown(args.outputs_dir, args.conversation_id, data)
    print(f"TRANSCRIPT_MD: {md_path}")


def cmd_show(args: argparse.Namespace) -> None:
    """Print a human-readable transcript summary (for verifying after a run)."""
    out_path = _path(args.outputs_dir, args.conversation_id)
    if not os.path.exists(out_path):
        sys.exit(f"error: no transcript at {out_path}")
    data = _read(out_path)
    end_body = (data.get("endResponse") or {}).get("body", {}) or {}
    print(f"conversationId:    {data.get('conversationId')}")
    print(f"environment:       {data.get('environment')}")
    print(f"userId:            {data.get('userId')}")
    print(f"scenario:          {data.get('scenario')}")
    print(f"prompter:          {data.get('prompterName')}")
    print(f"createInsightCard: {data.get('createInsightCard')}")
    print(f"turns:             {data.get('turns')}")
    print(f"stoppedBecause:    {data.get('stoppedBecause')}")
    print(f"initializedAt:     {data.get('initializedAt')}")
    print(f"finalizedAt:       {data.get('finalizedAt')}")
    print()
    print(f"summary:  {end_body.get('summary')!r}")
    print(f"followup: {end_body.get('followup')!r}")
    print()
    first = data.get("firstMessage")
    if isinstance(first, str) and first.strip():
        print(f"firstMessage (user opener): {first}")
    print()
    print("--- conversation ---")
    for entry in data.get("turnsLog") or []:
        n = entry.get("turn")
        print(f"\nTurn {n} — user:  {entry.get('user') or '(none)'}")
        print(f"Turn {n} — Aurie: {_aurie_turn_text(entry)}")


# ---------- entry point ---------------------------------------------------- #

def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--outputs-dir", default="outputs",
                   help="directory for transcript files (default: ./outputs)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("init", help="Create the transcript with metadata + /start response")
    sp.add_argument("conversation_id")
    sp.add_argument("--base-url", required=True)
    sp.add_argument("--scenario", required=True)
    sp.add_argument("--prompter", default="coach",
                    choices=["companion", "chat", "coach", "quickProcessing", "quickProcessingSentinel"])
    sp.add_argument("--create-insight-card", type=lambda s: s.lower() == "true",
                    default=False,
                    help="record whether the run is opting into insight (true/false)")
    sp.add_argument("--first-message-file", required=True)
    sp.add_argument("--start-response", required=True,
                    help="path to JSON file with the /start response (aura_chat.py output)")
    sp.add_argument("--force", action="store_true",
                    help="overwrite an existing transcript at this path")
    sp.set_defaults(fn=cmd_init)

    sp = sub.add_parser("append", help="Append one user turn + /ask response")
    sp.add_argument("conversation_id")
    sp.add_argument("--turn", type=int, required=True)
    sp.add_argument("--user-message-file", required=True)
    sp.add_argument("--ask-response", required=True,
                    help="path to JSON file with the /ask response")
    sp.set_defaults(fn=cmd_append)

    sp = sub.add_parser("finalize", help="Record /end response + close")
    sp.add_argument("conversation_id")
    sp.add_argument("--end-response", required=True,
                    help="path to JSON file with the /end response")
    sp.add_argument("--stop-reason", default=None)
    sp.set_defaults(fn=cmd_finalize)

    sp = sub.add_parser("render", help="Write a clean human-readable <id>.md transcript from the JSON")
    sp.add_argument("conversation_id")
    sp.set_defaults(fn=cmd_render)

    sp = sub.add_parser("show", help="Print a human-readable transcript summary + the dialogue")
    sp.add_argument("conversation_id")
    sp.set_defaults(fn=cmd_show)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
