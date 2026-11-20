#!/usr/bin/env python3
"""Manage chat.json for the convene-panel skill.

The orchestrator (LLM) drives persona spawning via the Agent tool, then uses
this script for all deterministic state management of the shared chat log.

Subcommands:
  init <output_dir>            --topic <text> --personas <personas.json> [--rounds <N>]
  append <chat_path>           --name <Iris> --round <N> [--content <text> | --content-file <path>]
                               [--yield-to <name|any|pass>] [--done]
  append-batch <chat_path>     --batch <batch.json>
  render <chat_path>           [--from-round <N>] [--include-briefs]
  set-synthesis <chat_path>    [--content <text> | --content-file <path>]
  set-convergence <chat_path>  --round <N> --converged <true|false> [--reason <text>]
  status <chat_path>

All file writes are atomic (write to .tmp, then rename).
Pure stdlib — no third-party deps.
"""

import argparse
import json
import os
import sys
from pathlib import Path


# ---------- IO ----------

def atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, path)


def load_chat(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_chat(path: Path, chat: dict) -> None:
    atomic_write(path, json.dumps(chat, indent=2, ensure_ascii=False) + "\n")


def read_content(args) -> str:
    if getattr(args, "content", None) is not None:
        return args.content
    if getattr(args, "content_file", None):
        return Path(args.content_file).read_text(encoding="utf-8")
    return sys.stdin.read()


def find_persona(chat: dict, name: str) -> dict:
    for p in chat["personas"]:
        if p["name"] == name:
            return p
    sys.exit(f"Unknown persona: {name!r}. Known: {[p['name'] for p in chat['personas']]}")


# ---------- Subcommands ----------

def cmd_init(args):
    personas = json.loads(Path(args.personas).read_text(encoding="utf-8"))
    if not personas:
        sys.exit("--personas file is empty")
    for p in personas:
        if "name" not in p or "role" not in p:
            sys.exit(f"Persona missing required fields (name, role): {p}")
    chat = {
        "topic": args.topic,
        "rounds_max": args.rounds,
        "personas": personas,
        "turns": [],
        "convergence": [],
        "synthesis": None,
    }
    chat_path = Path(args.output_dir) / "chat.json"
    save_chat(chat_path, chat)
    print(str(chat_path))


def cmd_append(args):
    chat_path = Path(args.chat_path)
    chat = load_chat(chat_path)
    persona = find_persona(chat, args.name)
    chat["turns"].append({
        "round": args.round,
        "name": persona["name"],
        "role": persona["role"],
        "content": read_content(args),
        "yield_to": args.yield_to,
        "i_am_done": bool(args.done),
    })
    save_chat(chat_path, chat)


def cmd_append_batch(args):
    chat_path = Path(args.chat_path)
    chat = load_chat(chat_path)
    batch = json.loads(Path(args.batch).read_text(encoding="utf-8"))
    if not isinstance(batch, list):
        sys.exit("--batch must be a JSON array of turn objects")
    for raw in batch:
        persona = find_persona(chat, raw["name"])
        chat["turns"].append({
            "round": raw["round"],
            "name": persona["name"],
            "role": persona["role"],
            "content": raw["content"],
            "yield_to": raw.get("yield_to"),
            "i_am_done": bool(raw.get("i_am_done", False)),
        })
    save_chat(chat_path, chat)


def cmd_render(args):
    chat = load_chat(Path(args.chat_path))
    out = [f"# Topic\n\n{chat['topic']}\n"]
    if args.include_briefs:
        out.append("# Personas\n")
        for p in chat["personas"]:
            brief = p.get("brief") or "(no brief)"
            out.append(f"- **{p['name']}** ({p['role']}): {brief}")
        out.append("")
    out.append("# Conversation so far\n")
    turns = chat["turns"]
    if args.from_round is not None:
        turns = [t for t in turns if t["round"] >= args.from_round]
    if not turns:
        out.append("_(no turns yet)_")
    else:
        current_round = None
        for t in turns:
            if t["round"] != current_round:
                out.append(f"\n## Round {t['round']}\n")
                current_round = t["round"]
            out.append(f"**{t['name']}** ({t['role']}):\n{t['content']}")
            extras = []
            if t.get("yield_to"):
                extras.append(f"yields to: {t['yield_to']}")
            if t.get("i_am_done"):
                extras.append("done")
            if extras:
                out.append(f"_{' · '.join(extras)}_\n")
            else:
                out.append("")
    print("\n".join(out))


def cmd_set_synthesis(args):
    chat_path = Path(args.chat_path)
    chat = load_chat(chat_path)
    chat["synthesis"] = read_content(args)
    save_chat(chat_path, chat)


def cmd_set_convergence(args):
    chat_path = Path(args.chat_path)
    chat = load_chat(chat_path)
    chat["convergence"].append({
        "after_round": args.round,
        "converged": args.converged.lower() == "true",
        "reason": args.reason or "",
    })
    save_chat(chat_path, chat)


def cmd_status(args):
    chat = load_chat(Path(args.chat_path))
    rounds_so_far = max((t["round"] for t in chat["turns"]), default=0)
    done_personas = sorted({t["name"] for t in chat["turns"] if t.get("i_am_done")})
    print(f"chat:           {args.chat_path}")
    print(f"topic:          {chat['topic']}")
    print(f"personas:       {', '.join(p['name'] for p in chat['personas'])}")
    print(f"rounds_max:     {chat['rounds_max']}")
    print(f"rounds_so_far:  {rounds_so_far}")
    print(f"turns:          {len(chat['turns'])}")
    print(f"done_personas:  {', '.join(done_personas) or '(none)'}")
    converged = any(c.get("converged") for c in chat["convergence"])
    print(f"converged:      {converged}")
    print(f"synthesis:      {'yes' if chat['synthesis'] else 'no'}")


# ---------- CLI ----------

def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="Create a fresh chat.json")
    s.add_argument("output_dir")
    s.add_argument("--topic", required=True)
    s.add_argument("--personas", required=True, help="JSON file: [{name, role, brief}, ...]")
    s.add_argument("--rounds", type=int, default=3)
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("append", help="Append a single turn (sequential mode)")
    s.add_argument("chat_path")
    s.add_argument("--name", required=True)
    s.add_argument("--round", type=int, required=True)
    g = s.add_mutually_exclusive_group()
    g.add_argument("--content")
    g.add_argument("--content-file")
    s.add_argument("--yield-to", default=None, help="Persona name, 'any', or 'pass'")
    s.add_argument("--done", action="store_true")
    s.set_defaults(func=cmd_append)

    s = sub.add_parser("append-batch", help="Append multiple turns at once (parallel round)")
    s.add_argument("chat_path")
    s.add_argument("--batch", required=True, help="JSON file: [{name, round, content, yield_to?, i_am_done?}, ...]")
    s.set_defaults(func=cmd_append_batch)

    s = sub.add_parser("render", help="Render chat.json as markdown for a persona prompt")
    s.add_argument("chat_path")
    s.add_argument("--from-round", type=int, default=None)
    s.add_argument("--include-briefs", action="store_true")
    s.set_defaults(func=cmd_render)

    s = sub.add_parser("set-synthesis", help="Persist final synthesis")
    s.add_argument("chat_path")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--content")
    g.add_argument("--content-file")
    s.set_defaults(func=cmd_set_synthesis)

    s = sub.add_parser("set-convergence", help="Record the convergence-check verdict for a round")
    s.add_argument("chat_path")
    s.add_argument("--round", type=int, required=True)
    s.add_argument("--converged", required=True, choices=["true", "false", "True", "False"])
    s.add_argument("--reason", default=None)
    s.set_defaults(func=cmd_set_convergence)

    s = sub.add_parser("status", help="Print a one-screen summary")
    s.add_argument("chat_path")
    s.set_defaults(func=cmd_status)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
