#!/usr/bin/env python3
"""Run the ApplePromptTester Swift CLI against a prompt + fixtures, append results.md.

Deterministic plumbing for the run-apple-intelligence-prompt skill. The model
upstream owns:
  - parsing user-provided prompt + inputs into the fixture file
  - choosing the prompt-slug (or accepting the auto-derived one)
  - deciding when to loop vs batch

This script owns:
  - invoking `swift run` with the right env
  - parsing the JSON output the binary writes
  - appending a new run section to results.md (never overwriting)

Usage:
    run_prompt.py \\
        --package-dir <path> \\
        --prompt-file <path> \\
        --fixtures-file <path> \\
        --output-file <path> \\
        [--label <str>] \\
        [--temperature <float>]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path


def short_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def run_swift(package_dir: Path, prompt: str, fixtures_path: Path, temperature: float) -> tuple[list[dict], str, str]:
    """Run `swift run` in package_dir with INSTRUCTIONS + FIXTURES env. Return (results, stdout, stderr)."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, dir=package_dir) as tf:
        results_json_path = Path(tf.name)
    try:
        env = os.environ.copy()
        env["INSTRUCTIONS"] = prompt
        env["FIXTURES"] = str(fixtures_path.resolve())
        env["RESULTS_JSON"] = str(results_json_path.resolve())
        env["TEMPERATURE"] = str(temperature)

        proc = subprocess.run(
            ["swift", "run", "-q"],
            cwd=package_dir,
            env=env,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            sys.stderr.write(proc.stderr)
            raise SystemExit(
                f"swift run failed (exit {proc.returncode}). See stderr above. "
                f"Common causes: macOS < 26, Xcode CLI tools instead of full Xcode 26, "
                f"Apple Intelligence disabled in System Settings."
            )

        if not results_json_path.exists() or results_json_path.stat().st_size == 0:
            raise SystemExit("swift run produced no results.json — check stderr for clues.")

        results = json.loads(results_json_path.read_text())
        return results, proc.stdout, proc.stderr
    finally:
        try:
            results_json_path.unlink()
        except FileNotFoundError:
            pass


def append_results(output_file: Path, prompt: str, label: str, temperature: float, results: list[dict]) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)

    is_new = not output_file.exists()
    ts = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    prompt_hash = short_hash(prompt)

    lines: list[str] = []
    if is_new:
        lines.append(f"# Results — {label}\n")
        lines.append("")
        lines.append("Each run below is appended in chronological order. Each section captures")
        lines.append("the prompt that was active for that run plus the per-input outputs.")
        lines.append("")
        lines.append("---")
        lines.append("")

    latencies = [int(r.get("latencyMs", 0)) for r in results]
    total_ms = sum(latencies)
    median_ms = int(statistics.median(latencies)) if latencies else 0
    max_ms = max(latencies) if latencies else 0

    lines.append(f"## Run {ts}  · prompt `{prompt_hash}`  · temperature {temperature}")
    lines.append("")
    lines.append(
        f"**Latency** — total {total_ms} ms across {len(results)} fixture(s) · "
        f"median {median_ms} ms · max {max_ms} ms"
    )
    lines.append("")
    lines.append("<details><summary>Prompt</summary>")
    lines.append("")
    lines.append("```")
    lines.append(prompt.rstrip())
    lines.append("```")
    lines.append("")
    lines.append("</details>")
    lines.append("")

    for r in results:
        latency = r.get("latencyMs")
        header = f"### `{r['id']}`"
        if latency is not None:
            header += f"  · {int(latency)} ms"
        lines.append(header)
        lines.append("")
        lines.append("**Input**")
        lines.append("")
        lines.append("```")
        lines.append(r["input"])
        lines.append("```")
        lines.append("")
        lines.append("**Output**")
        lines.append("")
        lines.append("```")
        lines.append(r["output"])
        lines.append("```")
        lines.append("")

    lines.append("---")
    lines.append("")

    with output_file.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--package-dir", required=True, type=Path)
    ap.add_argument("--prompt-file", required=True, type=Path)
    ap.add_argument("--fixtures-file", required=True, type=Path)
    ap.add_argument("--output-file", required=True, type=Path)
    ap.add_argument("--label", default="")
    ap.add_argument("--temperature", type=float, default=0.0)
    args = ap.parse_args()

    pkg = args.package_dir.expanduser().resolve()
    if not (pkg / "Package.swift").exists():
        raise SystemExit(f"no Package.swift at {pkg} — run scaffold_package.sh first.")

    prompt = args.prompt_file.read_text().rstrip()
    if not prompt:
        raise SystemExit(f"prompt file {args.prompt_file} is empty.")

    results, _stdout, _stderr = run_swift(pkg, prompt, args.fixtures_file, args.temperature)

    label = args.label or args.output_file.parent.name
    append_results(args.output_file, prompt, label, args.temperature, results)

    latencies = [int(r.get("latencyMs", 0)) for r in results]
    print(json.dumps({
        "results_file": str(args.output_file),
        "num_fixtures": len(results),
        "prompt_hash": short_hash(prompt),
        "latency_ms": {
            "total": sum(latencies),
            "median": int(statistics.median(latencies)) if latencies else 0,
            "max": max(latencies) if latencies else 0,
        },
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
