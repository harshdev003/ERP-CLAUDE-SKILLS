#!/usr/bin/env python3
"""Drive the PromptTesterIOS Xcode harness on a paired iPhone, append results.md.

Deterministic plumbing for the qa-apple-intelligence-prompt skill. The model
upstream owns:
  - parsing user-provided prompt + inputs into a pending-run.json payload
  - choosing the prompt-slug (or accepting the auto-derived one)
  - deciding when to loop vs batch
  - resolving the target device identifier

This script owns:
  - building the iOS app via `xcodebuild` for the target device
  - installing/launching via `xcrun devicectl`
  - pushing the pending-run.json into the app's Documents container
  - polling for results.json and pulling it back
  - appending a new run section to results.md (never overwriting)

Usage:
    run_prompt_ios.py \\
        --project-dir <path> \\
        --device-id <udid> \\
        --payload-file <pending-run.json> \\
        --output-file <results.md> \\
        [--label <str>] \\
        [--temperature <float>] \\
        [--timeout <seconds>] \\
        [--bundle-id <com.example.PromptTesterIOS>] \\
        [--scheme <ApplePromptTesterIOS>]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def short_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def run(cmd: list[str], *, check: bool = True, capture: bool = True, cwd: Path | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=capture, text=True, cwd=cwd)
    if check and proc.returncode != 0:
        sys.stderr.write(f"$ {' '.join(cmd)}\n")
        if proc.stdout:
            sys.stderr.write(proc.stdout)
        if proc.stderr:
            sys.stderr.write(proc.stderr)
        raise SystemExit(f"command failed (exit {proc.returncode}): {' '.join(cmd[:2])}")
    return proc


def discover_xcodeproj(project_dir: Path) -> Path:
    candidates = list(project_dir.glob("*.xcodeproj"))
    if not candidates:
        raise SystemExit(
            f"no .xcodeproj in {project_dir} — create it once in Xcode "
            f"per references/setup-xcode-project.md"
        )
    if len(candidates) > 1:
        raise SystemExit(f"multiple .xcodeproj in {project_dir}: {candidates}")
    return candidates[0]


def derive_bundle_id(xcodeproj: Path, scheme: str) -> str:
    proc = run([
        "xcodebuild",
        "-project", str(xcodeproj),
        "-scheme", scheme,
        "-showBuildSettings",
        "-configuration", "Debug",
        "-sdk", "iphoneos",
    ])
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("PRODUCT_BUNDLE_IDENTIFIER"):
            return line.split("=", 1)[1].strip()
    raise SystemExit("could not derive PRODUCT_BUNDLE_IDENTIFIER from xcodebuild settings")


def build_app(xcodeproj: Path, scheme: str, device_id: str, derived_data: Path) -> Path:
    run([
        "xcodebuild",
        "-project", str(xcodeproj),
        "-scheme", scheme,
        "-configuration", "Debug",
        "-destination", f"id={device_id}",
        "-derivedDataPath", str(derived_data),
        "build",
    ], capture=False)
    candidates = list((derived_data / "Build" / "Products").glob("Debug-iphoneos/*.app"))
    if not candidates:
        raise SystemExit(f"no .app produced under {derived_data}")
    return candidates[0]


def install_app(device_id: str, app_path: Path) -> None:
    run([
        "xcrun", "devicectl", "device", "install", "app",
        "--device", device_id,
        str(app_path),
    ], capture=False)


def copy_to_device(device_id: str, bundle_id: str, source: Path, dest: str) -> None:
    run([
        "xcrun", "devicectl", "device", "copy", "to",
        "--device", device_id,
        "--domain-type", "appDataContainer",
        "--domain-identifier", bundle_id,
        "--source", str(source),
        "--destination", dest,
    ], capture=False)


def copy_from_device(device_id: str, bundle_id: str, source: str, dest: Path) -> bool:
    proc = run([
        "xcrun", "devicectl", "device", "copy", "from",
        "--device", device_id,
        "--domain-type", "appDataContainer",
        "--domain-identifier", bundle_id,
        "--source", source,
        "--destination", str(dest),
    ], check=False)
    return proc.returncode == 0 and dest.exists() and dest.stat().st_size > 0


def remove_from_device(device_id: str, bundle_id: str, path: str) -> None:
    run([
        "xcrun", "devicectl", "device", "remove", "files",
        "--device", device_id,
        "--domain-type", "appDataContainer",
        "--domain-identifier", bundle_id,
        path,
    ], check=False, capture=False)


def launch_app(device_id: str, bundle_id: str) -> None:
    run([
        "xcrun", "devicectl", "device", "process", "launch",
        "--device", device_id,
        "--terminate-existing",
        bundle_id,
    ], capture=False)


def wait_for_results(device_id: str, bundle_id: str, dest_dir: Path, timeout_s: int) -> Path:
    deadline = time.time() + timeout_s
    out = dest_dir / "results.json"
    last_err = ""
    while time.time() < deadline:
        if copy_from_device(device_id, bundle_id, "/Documents/results.json", out):
            return out
        time.sleep(2)
    raise SystemExit(
        f"timed out after {timeout_s}s waiting for /Documents/results.json on device. "
        f"Check `xcrun devicectl device console --device {device_id}` for app-side errors. "
        f"Common causes: Apple Intelligence disabled in Settings, model still downloading, "
        f"app crashed on launch. Last error: {last_err}"
    )


def percentile(values: list[int], p: float) -> int:
    if not values:
        return 0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
    return s[k]


def append_results(output_file: Path, prompt: str, label: str, temperature: float, device_name: str, payload: dict) -> dict:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    is_new = not output_file.exists()
    ts = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    prompt_hash = short_hash(prompt)
    results = payload.get("results", [])
    latencies = [int(r.get("latencyMs", 0)) for r in results if r.get("latencyMs") is not None]
    first_call = latencies[0] if latencies else 0
    p50 = percentile(latencies, 50)
    p95 = percentile(latencies, 95)

    lines: list[str] = []
    if is_new:
        lines.append(f"# Results — {label}")
        lines.append("")
        lines.append("Each run below is appended in chronological order. Each section")
        lines.append("captures the prompt that was active for that run, the device it")
        lines.append("ran on, per-input outputs, and per-input latency.")
        lines.append("")
        lines.append("---")
        lines.append("")

    lines.append(f"## Run {ts}  · prompt `{prompt_hash}`  · device `{device_name}`  · temperature {temperature}")
    lines.append("")
    lines.append(f"**Latency:** first-call {first_call} ms · p50 {p50} ms · p95 {p95} ms · n={len(latencies)}")
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
        lines.append(f"### `{r['id']}`  · {r.get('latencyMs', '?')} ms")
        lines.append("")
        lines.append("**Input**")
        lines.append("")
        lines.append("```")
        lines.append(r.get("input", ""))
        lines.append("```")
        lines.append("")
        lines.append("**Output**")
        lines.append("")
        lines.append("```")
        lines.append(r.get("output", ""))
        lines.append("```")
        lines.append("")

    lines.append("---")
    lines.append("")

    with output_file.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return {
        "first_call_ms": first_call,
        "p50_ms": p50,
        "p95_ms": p95,
        "count": len(latencies),
        "prompt_hash": prompt_hash,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-dir", required=True, type=Path)
    ap.add_argument("--device-id", required=True)
    ap.add_argument("--payload-file", required=True, type=Path)
    ap.add_argument("--output-file", required=True, type=Path)
    ap.add_argument("--label", default="")
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--bundle-id", default="")
    ap.add_argument("--scheme", default="ApplePromptTesterIOS")
    args = ap.parse_args()

    if shutil.which("xcodebuild") is None:
        raise SystemExit("xcodebuild not on PATH — install Xcode 26+ from the App Store.")
    if shutil.which("xcrun") is None:
        raise SystemExit("xcrun not on PATH — install Xcode CLI tools.")

    project_dir = args.project_dir.expanduser().resolve()
    xcodeproj = discover_xcodeproj(project_dir)

    payload_path = args.payload_file.expanduser().resolve()
    if not payload_path.exists():
        raise SystemExit(f"payload file {payload_path} does not exist")
    payload = json.loads(payload_path.read_text())
    prompt = payload.get("instructions", "")
    if not prompt:
        raise SystemExit("payload is missing 'instructions' — refusing to run with empty prompt")

    bundle_id = args.bundle_id or derive_bundle_id(xcodeproj, args.scheme)

    with tempfile.TemporaryDirectory(prefix="prompttester-ios-") as tmp:
        tmp_path = Path(tmp)
        derived = tmp_path / "DerivedData"
        app_path = build_app(xcodeproj, args.scheme, args.device_id, derived)
        install_app(args.device_id, app_path)

        # Try copy first; if container not found yet, launch once and retry.
        try:
            copy_to_device(args.device_id, bundle_id, payload_path, "/Documents/pending-run.json")
        except SystemExit:
            launch_app(args.device_id, bundle_id)
            time.sleep(3)
            copy_to_device(args.device_id, bundle_id, payload_path, "/Documents/pending-run.json")

        # Clear any stale results from a previous run.
        remove_from_device(args.device_id, bundle_id, "/Documents/results.json")

        launch_app(args.device_id, bundle_id)
        results_path = wait_for_results(args.device_id, bundle_id, tmp_path, args.timeout)
        results_payload = json.loads(results_path.read_text())

    device_name = results_payload.get("device", args.device_id)
    label = args.label or args.output_file.parent.name
    summary = append_results(args.output_file, prompt, label, args.temperature, device_name, results_payload)

    print(json.dumps({
        "results_file": str(args.output_file),
        "num_fixtures": summary["count"],
        "prompt_hash": summary["prompt_hash"],
        "first_call_ms": summary["first_call_ms"],
        "p50_ms": summary["p50_ms"],
        "p95_ms": summary["p95_ms"],
        "device": device_name,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
