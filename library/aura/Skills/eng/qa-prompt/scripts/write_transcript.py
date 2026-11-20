#!/usr/bin/env python3
"""Emit a clean, standalone conversation transcript from a QA run state.

This is the artifact a reviewer reads first: ONLY the conversation — a short
header (prompt, model, version, scenario, timestamp) followed by the paired
user/assistant turns with inline tool calls/responses. No scores, no token
tables, no rendered system prompt. The full QA report (written by
write_report.py) still contains the transcript as one of its sections; this
file exists so the conversation is always available on its own, as a single
link, without scrolling a long report.

Filename:
  <output-dir>/transcript.md              (no --label)
  <output-dir>/transcript-<label>.md      (with --label, e.g. a variant or scenario)

Prints the written path as the last stdout line, prefixed `TRANSCRIPT: `, so the
orchestrator can reliably surface it as a clickable markdown link.

Reuses _format_transcript + _safe_filename from write_report.py (same skill).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from write_report import _render_standalone_transcript, _safe_filename  # noqa: E402


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Write a standalone conversation transcript from run state.")
    p.add_argument("--state-file", required=True)
    p.add_argument("--output-dir", default="outputs")
    p.add_argument(
        "--label",
        default=None,
        help="Optional label (variant or scenario). Without it the file is "
             "`transcript.md`; with it, `transcript-<label>.md`.",
    )
    return p.parse_args()


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    args = _parse_args()
    state = json.loads(Path(args.state_file).read_text(encoding="utf-8"))

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"transcript-{_safe_filename(args.label)}.md" if args.label else "transcript.md"
    out_path = out_dir / fname
    out_path.write_text(_render_standalone_transcript(state), encoding="utf-8")

    print(f"TRANSCRIPT: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
