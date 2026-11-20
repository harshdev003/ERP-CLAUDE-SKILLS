"""
Identity Platform user activity logs (`resource.type="identitytoolkit_project"`).

PII guardrail: these payloads carry end-user emails, names, photo URLs,
federated ids and caller IPs. The renderer reads ONLY the allowlisted fields
below and never iterates the payload, so a new PII field upstream cannot leak.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

AUTH_RESOURCE_FILTER = 'resource.type="identitytoolkit_project"'

_METHOD_PREFIX = "google.cloud.identitytoolkit.v1."
# status.message is an error enum (EMAIL_EXISTS, INVALID_LOGIN_CREDENTIALS …),
# sometimes followed by " : <free text>"; keep only a whole leading enum token.
_ERROR_ENUM_RE = re.compile(r"^[A-Z][A-Z0-9_]+(?=$|[\s:])")


def uid_filter(uid: str) -> str:
    """The three payload paths that carry the uid. Custom-token sign-ins and
    failed calls carry none, so they can never match."""
    return (
        f'(jsonPayload.response.localId="{uid}"'
        f' OR jsonPayload.metadata.tokenInfo.claims.user_id="{uid}"'
        f' OR jsonPayload.request.localId="{uid}")'
    )


@dataclass
class AuthEvent:
    timestamp: str
    severity: str
    method: str
    provider: Optional[str]
    is_new_user: Optional[bool]
    email_verified: Optional[bool]
    status_code: Optional[int]
    error: Optional[str]


def summarize(entry: dict) -> AuthEvent:
    p = entry.get("jsonPayload") or {}
    resp = p.get("response") or {}
    claims = ((p.get("metadata") or {}).get("tokenInfo") or {}).get("claims") or {}
    status = p.get("status") or {}
    message = status.get("message") or ""
    m = _ERROR_ENUM_RE.match(message)
    verified = resp.get("emailVerified", claims.get("email_verified"))
    return AuthEvent(
        timestamp=entry.get("timestamp", ""),
        severity=entry.get("severity", "DEFAULT"),
        method=(p.get("methodName") or "?").replace(_METHOD_PREFIX, ""),
        provider=resp.get("providerId") or (claims.get("firebase") or {}).get("sign_in_provider"),
        is_new_user=resp.get("isNewUser"),
        email_verified=verified if isinstance(verified, bool) else None,
        status_code=status.get("code"),
        error=m.group(0) if m else ("(message withheld)" if message else None),
    )


def render(events: list[AuthEvent], uid: str, since_human: str, project: str,
           cap_hit: bool = False) -> str:
    if not events:
        return (
            f"No auth events matched User {uid} in {project} over the last {since_human}.\n"
            "Custom-token sign-ins, token refreshes and failed calls carry no uid, so they never "
            "match. Ask the user to sign out and back in to produce a sign-in event."
        )
    events.sort(key=lambda e: e.timestamp)
    errors = sum(1 for e in events if e.status_code is not None)
    lines = [
        f"User {uid} | {len(events)} auth event(s), {errors} error(s) | {project} | last {since_human}",
        "",
    ]
    if cap_hit:
        lines[0] += f" | CAP HIT — oldest events dropped; only the newest {len(events)} shown"
    for e in events:
        bits = []
        if e.provider:
            bits.append(f"provider={e.provider}")
        if e.is_new_user is not None:
            bits.append(f"new_user={str(e.is_new_user).lower()}")
        if e.email_verified is not None:
            bits.append(f"email_verified={str(e.email_verified).lower()}")
        if e.status_code is not None:
            bits.append(f"status={e.status_code} {e.error or ''}".rstrip())
        lines.append(f"{e.timestamp[:23]}  {e.severity:<7}  {e.method:<45}  {'  '.join(bits)}")
    lines.append("")
    lines.append(f"→ Drill in: search gcp by user --user {uid} --since {since_human}   # the k8s service logs")
    return "\n".join(lines)
