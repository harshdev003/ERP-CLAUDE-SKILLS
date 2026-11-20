#!/usr/bin/env python3
r"""Hardened `gh api` wrapper.

Two failure modes make a naive wrapper report success on failure. Both were
observed live during the 2026-08-04 force-push incident response, and both make a
protection audit report "clean" when it has actually seen nothing:

1. A `--jq` parse error empties stdout. `gh api` does exit non-zero, but the
   common wrapper shape `return p.stdout if p.returncode == 0 else ""` collapses
   it to an empty string — **indistinguishable from a legitimate "no matches"**.
   A carrier sweep once reported "0 infected across 35 repos" this way, because
   the jq regex used `\.` where jq requires `\\.` in a string literal.
   The fix is not to check the return code harder; it is to never map a failure
   onto the same value as an empty result.

2. GitHub's **secondary** rate limiter returns 403 while `/rate_limit` still
   reports ~5000 core remaining. It is a concurrency/burst limit, distinct from
   the hourly quota.

So this module fails CLOSED. `gh()` raises rather than returning a false empty,
and `gh_optional()` returns None ONLY on a genuine 404 — a 403 or a rate limit
re-raises, because "the resource is absent" and "we could not find out" must
never collapse to the same value.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time

# gh gives GITHUB_TOKEN precedence over its keyring credential, so a stale or
# under-scoped token in the environment silently shadows a working one.
_STRIP = ("GITHUB_TOKEN", "GH_TOKEN")

JQ_ERRORS = ("failed to parse jq", "jq: error")


class GhError(RuntimeError):
    """Any non-success from gh. Never swallowed into an empty result.

    Carries `status` so callers can distinguish "the resource is genuinely
    absent" (404) from "we could not find out" (403, 429, 5xx). Collapsing
    those is the fail-open bug this module exists to prevent.
    """

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _status_of(body: str) -> int | None:
    """GitHub error bodies carry {"status":"404"}; gh also prints 'HTTP 403'."""
    m = re.search(r'"status"\s*:\s*"?(\d{3})', body) or re.search(r"HTTP (\d{3})", body)
    return int(m.group(1)) if m else None


def _env() -> dict:
    import os
    return {k: v for k, v in os.environ.items() if k not in _STRIP}


def gh(path: str, *, method: str = "GET", jq: str | None = None,
       paginate: bool = False, input_json: dict | None = None,
       retries: int = 5) -> object:
    """Call `gh api`. Raises GhError on any failure — never returns a false empty.

    jq string literals need a DOUBLE backslash for a literal dot (``\\\\.``);
    a single ``\\.`` is rejected by jq, which empties stdout — a wrapper that
    maps failure to "" would surface that as "no matches".
    """
    cmd = ["gh", "api", path]
    if method != "GET":
        cmd += ["-X", method]
    if paginate:
        cmd.append("--paginate")
    if jq:
        cmd += ["--jq", jq]
    stdin = None
    if input_json is not None:
        cmd += ["--input", "-"]
        stdin = json.dumps(input_json)

    last, last_status = "", None
    for attempt in range(retries):
        p = subprocess.run(cmd, capture_output=True, text=True, env=_env(), input=stdin)
        err = p.stderr or ""
        out = (p.stdout or "").strip()

        # (1) jq parse failure. gh exits non-zero, but an empty stdout is what a
        #     caller sees — raise rather than let it look like 'no matches'.
        if any(m in err for m in JQ_ERRORS):
            raise GhError(f"jq expression rejected (exit {p.returncode}): "
                          f"{err.strip()[:200]}")

        if p.returncode == 0 and not out.lstrip().startswith('{"message"'):
            if not out:
                return [] if paginate or jq else {}
            if jq:
                return out
            try:
                return json.loads(out)
            except json.JSONDecodeError:
                # --paginate concatenates JSON arrays: ][ -> ,
                try:
                    return json.loads("[" + out.replace("][", ",") + "]")
                except json.JSONDecodeError as e:
                    raise GhError(f"unparseable response from {path}: {e}") from e

        blob = (out + err).lower()
        # (2) secondary rate limit — 403 while /rate_limit still shows quota left
        if "rate limit" in blob or "secondary" in blob:
            time.sleep(15 * (attempt + 1))
            last = "rate limited"
            continue
        last = (out or err).strip()[:220]
        last_status = _status_of(out + err)
        break

    raise GhError(f"{method} {path} failed: {last}", last_status)


def gh_optional(path: str, **kw) -> object | None:
    """Return None ONLY on a genuine 404; re-raise everything else.

    The distinction is the whole point. 'does classic protection exist on this
    branch?' is answered by a 404. A 403 or a rate-limit answers nothing — and
    mapping it to None would report an unprotected repo as having no protection
    to find, which is a false clean.
    """
    try:
        return gh(path, **kw)
    except GhError as e:
        if e.status == 404 or "Not Found" in str(e):
            return None
        raise


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) < 2:
        print("usage: gh_api.py <api-path> [jq]", file=sys.stderr)
        raise SystemExit(2)
    jq = sys.argv[2] if len(sys.argv) > 2 else None
    print(json.dumps(gh(sys.argv[1], jq=jq), indent=2, default=str))


if __name__ == "__main__":
    main()
