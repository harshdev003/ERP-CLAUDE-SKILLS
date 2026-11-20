#!/usr/bin/env python3
"""claude.ai org-analytics export client (Team / Enterprise subscription orgs).

Canonical home for the two CSV export endpoints behind the claude.ai Analytics
and Members admin pages:

    GET /api/organizations/{org}/analytics/spend-report-export?start_date=&end_date=
    GET /api/organizations/{org}/members/export

**Why a cookie and not an API key.** These are claude.ai *subscription* org
endpoints, not Console/API ones. The Claude Enterprise Analytics API
(`read:analytics`) is Enterprise-only and explicitly unavailable on Teams, and an
Admin API key (`sk-ant-admin...`) addresses the separate Console organization —
the dashboard itself notes that API-key Claude Code usage reports to the Console
org, not here. So session-cookie auth is the only route to subscription usage,
and the key rotates. Same shape as `AURA_USER_TOKEN` (see repo CLAUDE.md token
inventory): the human pastes it, we pre-check it, and we never write a partition
on an auth failure.

**The one rule that matters.** A 403/401 must *raise*, never return an empty
result set. An expired cookie and a genuinely quiet day both produce zero rows;
only one of them should reach BigQuery. Writing an empty partition on an auth
failure silently overwrites real data with nothing and reads downstream exactly
like a day nobody worked. Every read path here raises `ClaudeAdminAuthError`
rather than degrading.

Transport prefers `requests`, falls back to `curl` — never `urllib` (matches the
`slack_post.py` / `slack_read.py` convention in this repo).
"""
from __future__ import annotations

import csv
import io
import json
import os
import subprocess
import time
from typing import Optional

BASE = "https://claude.ai"

# Env var names tried in order. CLAUDE_ADMIN_SESSION_KEY is the documented one;
# the others are accepted so a contributor who guessed a name still works.
_SESSION_ENV = (
    "CLAUDE_ADMIN_SESSION_KEY",
    "CLAUDE_SESSION_KEY",
    "ANTHROPIC_SESSION_KEY",
)
_ORG_ENV = ("CLAUDE_ADMIN_ORG_ID", "CLAUDE_ORG_ID")


class ClaudeAdminError(Exception):
    """An export request failed."""


class ClaudeAdminAuthError(ClaudeAdminError):
    """Missing, expired, or unauthorized session key.

    Distinct from `ClaudeAdminError` so a caller can tell "refresh your cookie"
    from "the request broke" — and so neither is ever mistaken for "no usage".
    """


def resolve_session_key(explicit: Optional[str] = None) -> str:
    if explicit:
        return explicit.strip()
    for name in _SESSION_ENV:
        v = os.environ.get(name)
        if v and v.strip():
            return v.strip()
    raise ClaudeAdminAuthError(
        "No session key. Set CLAUDE_ADMIN_SESSION_KEY in ~/.claude/settings.json "
        "(env block) and restart Claude Code. To get the value: sign in to "
        "claude.ai as an Owner, open DevTools > Application > Cookies > "
        "https://claude.ai, and copy the `sessionKey` value (starts with `sk-ant-sid...`)."
    )


def resolve_org_id(explicit: Optional[str] = None) -> str:
    if explicit:
        return explicit.strip()
    for name in _ORG_ENV:
        v = os.environ.get(name)
        if v and v.strip():
            return v.strip()
    raise ClaudeAdminError(
        "No organization id. Set CLAUDE_ADMIN_ORG_ID in ~/.claude/settings.json. "
        "Find it in any claude.ai admin URL, or in the filename of a manually "
        "exported spend report (spend-report-<org-id>-<dates>.csv)."
    )


class ClaudeAdmin:
    def __init__(self, session_key: Optional[str] = None, org_id: Optional[str] = None,
                 *, timeout: int = 90, max_retries: int = 4):
        self.session_key = resolve_session_key(session_key)
        self.org_id = resolve_org_id(org_id)
        self.timeout = timeout
        self.max_retries = max_retries

    # ---------------- transport ----------------

    def _headers(self) -> dict:
        return {
            "Cookie": f"sessionKey={self.session_key}",
            "Accept": "text/csv, application/json;q=0.9, */*;q=0.8",
            # claude.ai rejects some default agents; be explicit and honest.
            "User-Agent": "AINative-harvest-anthropic-usage/1.0",
        }

    def _get(self, path: str, params: Optional[dict] = None) -> str:
        url = f"{BASE}{path}"
        last = None
        for attempt in range(self.max_retries):
            status, body = self._once(url, params)
            if status in (401, 403):
                raise ClaudeAdminAuthError(
                    f"HTTP {status} on {path} — the session key is expired, or this "
                    f"account is not an Owner/Primary Owner of org {self.org_id}. "
                    "Analytics exports are Owner-only on Team plans (Admin is not "
                    "enough). Refresh CLAUDE_ADMIN_SESSION_KEY and restart Claude Code."
                )
            if status == 200:
                return body
            if status == 429 or status >= 500:
                last = f"HTTP {status}"
                time.sleep(2 ** attempt)
                continue
            raise ClaudeAdminError(f"HTTP {status} on {path}: {body[:300]}")
        raise ClaudeAdminError(f"{path} failed after {self.max_retries} attempts ({last})")

    def _once(self, url: str, params: Optional[dict]):
        try:
            import requests  # noqa: PLC0415
        except ImportError:
            return self._curl(url, params)
        try:
            r = requests.get(url, params=params or {}, headers=self._headers(),
                             timeout=self.timeout)
            return r.status_code, r.text
        except Exception as e:  # noqa: BLE001
            raise ClaudeAdminError(f"request failed: {e}")

    def _curl(self, url: str, params: Optional[dict]):
        if params:
            from urllib.parse import urlencode
            url = f"{url}?{urlencode(params)}"
        cmd = ["curl", "-sS", "-w", "\n%{http_code}", "--max-time", str(self.timeout)]
        for k, v in self._headers().items():
            cmd += ["-H", f"{k}: {v}"]
        cmd.append(url)
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, check=False)
        except Exception as e:  # noqa: BLE001
            raise ClaudeAdminError(f"curl failed: {e}")
        if out.returncode != 0:
            raise ClaudeAdminError(f"curl exit {out.returncode}: {out.stderr[:300]}")
        body, _, code = out.stdout.rpartition("\n")
        try:
            return int(code.strip()), body
        except ValueError:
            raise ClaudeAdminError(f"curl gave no status code: {out.stdout[:200]}")

    # ---------------- reads ----------------

    def check_auth(self) -> dict:
        """Cheap pre-flight. Raises ClaudeAdminAuthError if the key won't work.

        Call this once before a multi-day harvest so an expired cookie fails on
        day one instead of after a partial write.
        """
        body = self._get(f"/api/organizations/{self.org_id}/members/export")
        rows = parse_members_csv(body)
        if not rows:
            raise ClaudeAdminError(
                "members/export returned no rows — unexpected for a live org; "
                "refusing to treat this as a valid session."
            )
        return {"ok": True, "members": len(rows)}

    def spend_report(self, start_date: str, end_date: str) -> str:
        """Raw CSV for [start_date, end_date] inclusive (YYYY-MM-DD, UTC)."""
        return self._get(
            f"/api/organizations/{self.org_id}/analytics/spend-report-export",
            {"start_date": start_date, "end_date": end_date},
        )

    def members(self) -> str:
        """Raw members CSV (Name, Email, Role, Status, Seat Tier)."""
        return self._get(f"/api/organizations/{self.org_id}/members/export")


# ---------------- parsing ----------------

# Column order the spend-report export has shipped with. Parsed by NAME, never
# by position — a reordered or newly-added upstream column must not silently
# shift every value one to the left.
SPEND_COLUMNS = (
    "user_email", "account_uuid", "product", "model", "total_requests",
    "total_prompt_tokens", "total_completion_tokens", "total_net_spend_usd",
    "total_gross_spend_usd", "user_id", "total_uncached_input_tokens",
    "total_cache_read_tokens", "total_cache_write_5m_tokens",
    "total_cache_write_1h_tokens", "total_web_search_count",
    "slack_channel_id", "teams_channel_id",
)

# Without these we cannot build a usable row; their absence means the upstream
# shape changed and the run must stop rather than write nulls.
REQUIRED_SPEND_COLUMNS = (
    "user_email", "product", "model",
    "total_prompt_tokens", "total_completion_tokens",
)


def _int(v) -> int:
    v = (v or "").strip()
    if not v:
        return 0
    try:
        return int(float(v))
    except ValueError:
        return 0


def _dec(v) -> str:
    v = (v or "").strip()
    if not v:
        return "0"
    try:
        float(v)
    except ValueError:
        return "0"
    return v


def parse_spend_csv(text: str) -> list:
    """Parse a spend-report CSV into dicts. Returns [] for a genuinely empty day.

    Raises ClaudeAdminError if the header is missing required columns — a shape
    change must be loud, because silently-zero token columns would land in
    BigQuery looking like a real quiet day.
    """
    text = (text or "").strip()
    if not text:
        return []
    rdr = csv.DictReader(io.StringIO(text))
    if rdr.fieldnames is None:
        return []
    missing = [c for c in REQUIRED_SPEND_COLUMNS if c not in rdr.fieldnames]
    if missing:
        raise ClaudeAdminError(
            f"spend-report export is missing expected column(s): {missing}. "
            f"Got: {rdr.fieldnames}. Upstream shape changed — refusing to load."
        )
    out = []
    for r in rdr:
        out.append({
            "user_email": (r.get("user_email") or "").strip(),
            "account_uuid": (r.get("account_uuid") or "").strip(),
            "user_id": (r.get("user_id") or "").strip(),
            "product": (r.get("product") or "").strip(),
            "model": (r.get("model") or "").strip(),
            "slack_channel_id": (r.get("slack_channel_id") or "").strip(),
            "teams_channel_id": (r.get("teams_channel_id") or "").strip(),
            "requests": _int(r.get("total_requests")),
            "prompt_tokens": _int(r.get("total_prompt_tokens")),
            "completion_tokens": _int(r.get("total_completion_tokens")),
            "uncached_input_tokens": _int(r.get("total_uncached_input_tokens")),
            "cache_read_tokens": _int(r.get("total_cache_read_tokens")),
            "cache_write_5m_tokens": _int(r.get("total_cache_write_5m_tokens")),
            "cache_write_1h_tokens": _int(r.get("total_cache_write_1h_tokens")),
            "web_search_count": _int(r.get("total_web_search_count")),
            "net_spend_usd": _dec(r.get("total_net_spend_usd")),
            "gross_spend_usd": _dec(r.get("total_gross_spend_usd")),
        })
    return out


MEMBER_REQUIRED = ("Email", "Seat Tier")


def parse_members_csv(text: str) -> list:
    text = (text or "").strip()
    if not text:
        return []
    rdr = csv.DictReader(io.StringIO(text))
    if rdr.fieldnames is None:
        return []
    missing = [c for c in MEMBER_REQUIRED if c not in rdr.fieldnames]
    if missing:
        raise ClaudeAdminError(
            f"members export is missing expected column(s): {missing}. "
            f"Got: {rdr.fieldnames}. Upstream shape changed — refusing to load."
        )
    out = []
    for r in rdr:
        email = (r.get("Email") or "").strip()
        if not email:
            continue
        out.append({
            "user_email": email,
            "display_name": (r.get("Name") or "").strip(),
            "role": (r.get("Role") or "").strip(),
            "status": (r.get("Status") or "").strip(),
            "seat_tier": (r.get("Seat Tier") or "").strip().lower() or "unknown",
        })
    return out


if __name__ == "__main__":  # tiny manual probe
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    c = ClaudeAdmin()
    print(json.dumps(c.check_auth()))
