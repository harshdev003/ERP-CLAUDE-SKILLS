"""Shared helpers for monitor-firebase scripts: findings, state dir, HTTP through curl.

A finding is one JSON line: {"check", "level", "key", "text"}.
  level  alert  Jerome needs to act (posted to #alerts-ai once per key per cooldown)
         note   worth a line in the report, no action
         fail   the check could not run: report it, never read it as a clean result
  key    stable id for de-duplicating alerts across passes (no IPs, ids or values)
  text   counts only: paths, ops, clients, counts, times, ASN and country
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import tempfile
import time

import gcloud_sa

PROJECT = "firebase-auratech16"
# A 429 waits for its Retry-After (else 2**attempt s), capped, like notion_rest.py.
MAX_RETRY_WAIT_S = 60


class RateLimited(RuntimeError):
    """Still HTTP 429 after every retry."""


def state_dir() -> pathlib.Path:
    d = pathlib.Path(os.environ.get("MONITOR_FIREBASE_STATE")
                     or pathlib.Path.home() / ".cache" / "aura" / "monitor-firebase")
    d.mkdir(parents=True, exist_ok=True)
    return d


def emit(check: str, level: str, key: str, text: str) -> None:
    print(json.dumps({"check": check, "level": level, "key": key, "text": text}), flush=True)


def access_token() -> str:
    out = gcloud_sa.run(["auth", "print-access-token"], capture_output=True, text=True, encoding="utf-8")
    if out.returncode != 0 or not out.stdout.strip():
        raise RuntimeError(f"no access token as {gcloud_sa.credential()}")
    return out.stdout.strip().splitlines()[-1]


def _status(head: str) -> tuple[int, float | None]:
    """The last response's status and Retry-After seconds from a curl -D header dump."""
    status, wait = 0, None
    for line in head.splitlines():
        m = re.match(r"HTTP/\S+ (\d{3})", line)
        if m:
            status, wait = int(m[1]), None
        m = re.match(r"(?i)retry-after:\s*(\d+(?:\.\d+)?)", line.strip())
        if m:
            wait = float(m[1])
    return status, wait


def curl_json(url: str, *, data: dict | None = None, headers: dict | None = None,
              params: dict | None = None, method: str | None = None, user_agent: str | None = None,
              retries_429: int = 0, sleep=time.sleep) -> dict:
    """GET/POST through curl (macOS system Python's urllib lacks the CA bundle), AuraClaude UA.
    retries_429: how many times to retry an HTTP 429 (never another status, so a write isn't repeated
    after the server may have applied it); RateLimited when the last try is still 429."""
    cmd = ["curl", "-sS", "--fail-with-body", "--max-time", "60", "-A", user_agent or gcloud_sa.user_agent()]
    for k, v in (headers or {}).items():
        cmd += ["-H", f"{k}: {v}"]
    if params:
        cmd += ["-G"] + [x for k, v in params.items() for x in ("--data-urlencode", f"{k}={v}")]
    if data is not None:
        cmd += ["-H", "Content-Type: application/json", "--data", json.dumps(data)]
    if method:
        cmd += ["-X", method]
    for attempt in range(retries_429 + 1):
        with tempfile.TemporaryDirectory() as d:
            head_file = os.path.join(d, "headers")
            out = subprocess.run(cmd + ["-D", head_file, url], capture_output=True, text=True, encoding="utf-8",
                                 errors="replace")
            head = pathlib.Path(head_file).read_text(encoding="utf-8", errors="replace") \
                if os.path.exists(head_file) else ""
        if out.returncode == 0:
            return json.loads(out.stdout or "{}")
        status, wait = _status(head)
        if status != 429:
            raise RuntimeError(f"HTTP failed ({out.returncode}): {(out.stdout or out.stderr)[:200]}")
        if attempt == retries_429:
            raise RateLimited(f"HTTP 429 rate-limited after {attempt + 1} tries: {(out.stdout or '')[:160]}")
        sleep(min(MAX_RETRY_WAIT_S, wait if wait is not None else 2 ** attempt))
    raise AssertionError("unreachable")
