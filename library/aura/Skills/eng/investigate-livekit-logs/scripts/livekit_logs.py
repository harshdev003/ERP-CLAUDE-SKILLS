#!/usr/bin/env python3
"""Capture a bounded window of LiveKit Cloud agent logs, redact PII, and emit a triage digest.

`lk agent logs` only *tails* (streams) — there is no time-window or historical query API.
So this script launches the tail, captures for a bounded window (N seconds or N lines,
whichever comes first), terminates it, redacts user PII (the agent currently logs raw user
messages / first_message / metadata at INFO — ENG-707), groups lines by room/session, and
surfaces ERROR / WARNING / Traceback / disconnect events.

Read-only: it never writes anywhere and never disables redaction.

Usage:
    python3 livekit_logs.py [--mode triage|build] [--seconds N] [--max-lines N]
                            [--project poc] [--id CA_xxx] [--room poc-room-...] [--grep RE]

Examples:
    python3 livekit_logs.py                         # 20s triage snapshot of runtime logs
    python3 livekit_logs.py --seconds 45            # longer window (catch a live call)
    python3 livekit_logs.py --mode build            # build logs (debug a failed deploy)
    python3 livekit_logs.py --room poc-room-17 --seconds 30
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import Counter

# No hardcoded project/agent. The Aura LiveKit project is `Aurie` (id p_5bw0of3laoy, URL
# poc-pf65xo6x.livekit.cloud — the `poc-` subdomain is a fixed legacy name, not a separate
# project). Locally `lk` may alias it under any name (e.g. `poc`), so pinning a name here
# would rot. Instead:
#   --project omitted  -> `lk` uses its currently-selected default project (`lk project list`)
#   --id omitted       -> `lk agent logs` reads the agent id from `livekit.toml` in the cwd
# Pass --project <your-lk-alias> / --id <CA_…> to target explicitly. See references/lk-cli.md.

# ---------------------------------------------------------------------------
# PII redaction — the agent logs raw user content at INFO (ENG-707). Strip it
# before anything reaches stdout. Patterns are intentionally broad: over-redact
# rather than leak. Each entry is (compiled regex, replacement).
# ---------------------------------------------------------------------------
_REDACTIONS = [
    # Agent log lines that embed the user's utterance / first message.
    (re.compile(r"(User message:\s*).+", re.I), r"\1[REDACTED]"),
    (re.compile(r"((?:Found|Sending)?\s*first[_ ]message:?\s*).+", re.I), r"\1[REDACTED]"),
    (re.compile(r"(Streamed?\s+Aura\s+response:?\s*).+", re.I), r"\1[REDACTED]"),
    # Metadata / metrics blobs (JSON dumps that can carry names, ids, content).
    (re.compile(r"((?:agent_?[Mm]etadata|metadataObj|[Mm]etrics payload|metadata):\s*).+", re.I), r"\1[REDACTED]"),
    # JWT / LiveKit access tokens: header.payload.signature in base64url.
    (re.compile(r"\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\b"), "[JWT_REDACTED]"),
    # Bearer / api secrets in the open.
    (re.compile(r"(?i)\b(api[_-]?secret|authorization|bearer)\b[=:\s]+\S+"), r"\1=[REDACTED]"),
    # Email addresses.
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "[EMAIL_REDACTED]"),
]

# Firebase UIDs (28-char alnum) → keep an 8-char prefix only (Compliance/Privacy).
_FIREBASE_UID = re.compile(r"\b([A-Za-z0-9]{8})[A-Za-z0-9]{20}\b")

# ANSI colour codes the lk/livekit logger emits.
_ANSI = re.compile(r"\x1b\[[0-9;]*m")

_ROOM = re.compile(r"\b(poc-room-[\w-]+)\b")
_ERROR = re.compile(r"\b(error|traceback|exception|failed|fatal|critical|unauthorized|refused)\b", re.I)
_WARN = re.compile(r"\b(warn|warning|retrying|timeout|disconnect|did not join)\b", re.I)


def redact(line: str) -> str:
    """Strip ANSI, redact PII, and shorten Firebase UIDs. Always applied — no opt-out."""
    line = _ANSI.sub("", line)
    for pattern, repl in _REDACTIONS:
        line = pattern.sub(repl, line)
    line = _FIREBASE_UID.sub(r"\1…", line)  # prefix + ellipsis
    return line.rstrip("\n")


def build_command(args: argparse.Namespace) -> list:
    cmd = ["lk", "agent", "logs", "--log-type", args.mode if args.mode == "build" else "deploy"]
    if args.project:
        cmd += ["--project", args.project]
    if args.id:
        cmd += ["--id", args.id]
    return cmd


def capture(cmd: list, seconds: float, max_lines: int) -> tuple:
    """Run `lk agent logs` and collect redacted lines until the time or line budget is hit.

    Returns (lines, error_message_or_None). `lk` is a streaming tail, so we read in a thread
    and stop it ourselves once the window closes.
    """
    if shutil.which("lk") is None:
        return [], "the `lk` CLI is not installed (brew install livekit-cli)"

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
    except OSError as exc:
        return [], f"failed to launch `{' '.join(cmd)}`: {exc}"

    lines: list = []

    def reader():
        for raw in proc.stdout:  # type: ignore[union-attr]
            lines.append(redact(raw))
            if len(lines) >= max_lines:
                break

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    deadline = time.monotonic() + seconds
    while t.is_alive() and time.monotonic() < deadline and len(lines) < max_lines:
        time.sleep(0.2)
    hit_deadline = time.monotonic() >= deadline

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()

    # Only return a hard error for an unambiguous auth/setup failure — and ONLY from a
    # CLI-specific signature, never from words like "unauthorized"/"login" that also appear in
    # healthy runtime output (e.g. a provider 401). An early exit on its own is NOT an error:
    # `lk agent logs --mode build` legitimately finishes fast with a few lines, and a short
    # deploy tail is normal — those just flow through to triage as the captured output.
    exited_early = not hit_deadline and len(lines) < max_lines
    if exited_early and len(lines) < 5:
        diag = "\n".join(lines).lower()
        # Keep these signatures SPECIFIC to auth/project resolution. Do NOT add loose tokens
        # like "livekit.toml" or "no agent" — those appear in healthy short build-log output and
        # would misclassify a successful `--mode build` tail as a setup failure. A genuine
        # agent-resolution error (no --id, no livekit.toml) simply flows through to the triage
        # digest, where the user sees lk's own message plus the --project/--id hints below.
        setup_sigs = (
            "not authenticated", "no project", "could not find project", "project not found",
            "lk cloud auth", "invalid api key", "please login", "no default project",
        )
        if any(s in diag for s in setup_sigs):
            return lines, ("`lk` couldn't authenticate / resolve the project. Run `lk cloud auth`, "
                           "then pass `--project <name>` / `--id <CA_…>` or run from the agent repo "
                           "(which has a `livekit.toml`). Check `lk project list` / `lk agent list`.")
    return lines, None


def triage(lines: list, captured_total=None, scope_desc=None) -> str:
    """Build the redacted digest: window summary, errors, warnings, rooms/sessions seen.

    `captured_total` (the pre-filter line count) and `scope_desc` are passed only when a
    `--room`/`--grep` scope was applied, so an empty result can be reported as "no match for
    the scope" rather than "agent idle".
    """
    errors = [ln for ln in lines if _ERROR.search(ln)]
    warns = [ln for ln in lines if _WARN.search(ln) and not _ERROR.search(ln)]
    rooms = Counter(m.group(1) for ln in lines for m in [_ROOM.search(ln)] if m)

    out = []
    # Only use the scoped "(of N captured)" wording when something was actually captured;
    # captured_total == 0 is an idle window and should read as idle, not as a filter miss.
    shown = f"{len(lines)} line(s) (of {captured_total} captured)" if captured_total else f"{len(lines)} log line(s)"
    out.append(f"{shown}.  errors={len(errors)}  warnings={len(warns)}  rooms={len(rooms)}")
    out.append("")

    if rooms:
        out.append("### Rooms / sessions seen")
        for room, n in rooms.most_common(15):
            out.append(f"- `{room}`  ({n} line{'s' if n != 1 else ''})")
        out.append("")

    if errors:
        out.append(f"### Errors / failures ({len(errors)})")
        out.extend(f"- {ln}" for ln in errors[:40])
        if len(errors) > 40:
            out.append(f"- … and {len(errors) - 40} more")
        out.append("")

    if warns:
        out.append(f"### Warnings ({len(warns)})")
        out.extend(f"- {ln}" for ln in warns[:25])
        if len(warns) > 25:
            out.append(f"- … and {len(warns) - 25} more")
        out.append("")

    if not lines:
        if captured_total:  # >0 captured, but the scope filtered them all out
            out.append(f"_Captured {captured_total} line(s), but none matched {scope_desc}._")
            out.append("_Check the room id / pattern, or re-run without the scope._")
        else:
            out.append("_No log output in the capture window. The agent may be idle (no active calls)._")
            out.append("_`lk agent logs` only tails the latest live instance — for history use the dashboard "
                       "(Agent Observability) or runtime log forwarding (see references)._")
        out.append("")

    return "\n".join(out)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(description="Triage LiveKit Cloud agent logs (PII-redacted).")
    ap.add_argument("--mode", choices=["triage", "build"], default="triage",
                    help="triage = runtime/deploy logs (default); build = deploy build logs")
    ap.add_argument("--seconds", type=float, default=20.0, help="capture window in seconds (default 20)")
    ap.add_argument("--max-lines", type=int, default=2000, help="stop after this many lines (default 2000)")
    ap.add_argument("--project", default=None,
                    help="lk project name (e.g. Aurie for prod, poc for the POC). Omit to use lk's default project.")
    ap.add_argument("--id", default=None,
                    help="agent id (CA_…). Omit to read it from livekit.toml in the cwd (run from the agent repo).")
    ap.add_argument("--room", help="only keep lines mentioning this room id (e.g. poc-room-17)")
    ap.add_argument("--grep", help="only keep lines matching this regex (applied after redaction)")
    args = ap.parse_args()

    # Validate the grep pattern up front so a bad regex fails fast (not after a 20s capture).
    grep_rx = None
    if args.grep:
        try:
            grep_rx = re.compile(args.grep, re.I)
        except re.error as exc:
            print(f"⚠️  invalid --grep regex: {exc}")
            sys.exit(2)

    # `lk agent logs` needs an agent id. With no --id it reads `livekit.toml` from the cwd —
    # but when this skill runs outside the agent repo (e.g. from the plugin dir) there is none,
    # and the project may host several agents, so we can't guess. Resolve it explicitly: show
    # the agent list and ask for --id rather than silently tailing nothing.
    if not args.id and not os.path.exists("livekit.toml"):
        print("No --id given and no livekit.toml in the current directory — can't resolve which "
              "agent to tail. Available agents:\n")
        if shutil.which("lk"):
            subprocess.run(["lk", "agent", "list"] + (["--project", args.project] if args.project else []))
        else:
            print("(install the `lk` CLI first: brew install livekit-cli)")
        print("\nRe-run with `--id <CA_…>` (the Aura voice agent's dispatch name is "
              "`lk-voice-agent`), or run from the agent repo (which has a `livekit.toml`).")
        sys.exit(2)

    cmd = build_command(args)
    print(f"$ {' '.join(cmd)}   (capturing ~{args.seconds:g}s, max {args.max_lines} lines)\n")

    lines, err = capture(cmd, args.seconds, args.max_lines)
    if err:
        print(f"⚠️  {err}")
        if lines:
            print("\nRaw output captured before the error:")
            print("\n".join(lines[:30]))
        sys.exit(1)

    captured_total = len(lines)
    scopes = []
    if args.room:
        lines = [ln for ln in lines if args.room in ln]
        scopes.append(f"room `{args.room}`")
    if grep_rx is not None:
        lines = [ln for ln in lines if grep_rx.search(ln)]
        scopes.append(f"--grep /{args.grep}/")

    scoped = bool(scopes)
    print(triage(
        lines,
        captured_total=captured_total if scoped else None,
        scope_desc=" + ".join(scopes) if scoped else None,
    ))

    print("### Next steps")
    print("- `lk agent status` — rollout / health of the deployed agent")
    print("- `lk agent versions` — deployed versions (for rollback)")
    print("- `python3 livekit_logs.py --mode build` — build logs if a deploy failed")
    print("- LiveKit Cloud dashboard → project → Agents → Logs — for historical logs (CLI only tails live)")


if __name__ == "__main__":
    main()
