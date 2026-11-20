"""
Log entry normalization.

Aura's log format is NOT uniform across services. Two known shapes:

Format A (Node services like user-service):
  jsonPayload.message       — the human message
  jsonPayload.transaction   — full UUID
  jsonPayload.transactionSlice  — first 8 chars
  jsonPayload.userId        — Firebase UID (sometimes)
  jsonPayload.signInUid     — Firebase UID, flat (verifyToken model layer, e.g.
                              "No profile for the token" — the highest-volume orphan signal)
  jsonPayload.tokenUserId   — the same uid under its old key (before ENGBACK-2797)
  jsonPayload.verifiedToken.tokenUserId  — Firebase UID (auth middleware)
  jsonPayload.context.userId             — Firebase UID (other components, hypothesised)
  jsonPayload.component, .method
  jsonPayload.serviceContext.{service, version}
  jsonPayload.error         — sometimes {}, sometimes populated, sometimes ABSENT

Format B (Python services like neural-model):
  jsonPayload.message = "<LEVEL> - {<stringified-JSON>}"
  Inner JSON has its own .message, .meta.transaction, .meta.transactionSplit, .meta.userId, etc.
  Per-call params make every line unique → naive grouping by jsonPayload.message
  produces 1,000 groups of 1 instead of 1 group of 1,000.

The normalizer collapses both formats into a single canonical dict (`Normalized`)
with provenance tracking (which fallback path matched the userId, etc.).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

# Detect Format B: "CRITICAL - {...}", "ERROR - {...}", "WARNING - {...}", etc.
_FORMAT_B_PREFIX_RE = re.compile(r"^([A-Z]+)\s*-\s*(\{.*\})\s*$", re.DOTALL)


@dataclass
class Normalized:
    """Canonical log entry shape used by all formatters and groupers."""

    timestamp: str  # RFC3339 nanos from top-level timestamp
    severity: str  # GCP severity (uppercase)
    container: Optional[str]  # resource.labels.container_name
    namespace: Optional[str]  # resource.labels.namespace_name
    pod: Optional[str]  # resource.labels.pod_name
    insert_id: str  # for dedup

    message: str  # canonical (post-normalization) message — the GROUPING KEY
    transaction: Optional[str]  # full UUID
    tx_slice: Optional[str]  # short form (transactionSlice or transactionSplit)
    user_id: Optional[str]
    user_id_path: Optional[str]  # provenance: which path matched
    component: Optional[str]
    method: Optional[str]
    service_name: Optional[str]  # serviceContext.service
    service_version: Optional[str]  # serviceContext.version

    format: str  # "A" | "B" | "B-fallback" (Format B re-parse failed)
    raw: dict = field(default_factory=dict)  # the original GCP log entry, for trace mode


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def normalize(entry: dict) -> Normalized:
    """Normalize one Cloud Logging entry (as returned by .to_api_repr()) into canonical shape."""

    json_payload = entry.get("jsonPayload") or {}
    text_payload = entry.get("textPayload") or ""
    # Most Aura services use jsonPayload.message; some (proxy-web, certain models, fluentbit)
    # emit textPayload instead. Fall back so the digest doesn't show empty rows.
    raw_message = json_payload.get("message") or text_payload or ""
    inner: Optional[dict] = None
    fmt = "A"

    # Detect Format B and try to re-parse the embedded JSON
    m = _FORMAT_B_PREFIX_RE.match(raw_message) if isinstance(raw_message, str) else None
    if m:
        try:
            inner = json.loads(m.group(2))
            fmt = "B"
        except json.JSONDecodeError:
            inner = None
            fmt = "B-fallback"

    inner = inner or {}
    inner_meta = inner.get("meta") if isinstance(inner.get("meta"), dict) else {}

    # Resolve canonical fields with fallbacks (Format B values shadow A only when A is missing)
    message = _coalesce(
        inner.get("message") if fmt == "B" else None,
        json_payload.get("message"),
        default="",
    )
    if fmt == "B-fallback":
        # Re-parse failed — keep the raw message but truncate so the digest doesn't leak
        # the embedded JSON (handled at format time too, but defense-in-depth)
        message = (raw_message or "")[:200]

    transaction = _coalesce(
        json_payload.get("transaction"),
        inner_meta.get("transaction"),
    )
    tx_slice = _coalesce(
        json_payload.get("transactionSlice"),
        json_payload.get("transactionSplit"),
        inner_meta.get("transactionSlice"),
        inner_meta.get("transactionSplit"),
    )
    if not tx_slice and transaction:
        tx_slice = transaction.split("-")[0]

    user_id, user_id_path = _extract_user_id(json_payload, inner_meta)

    component = _coalesce(
        json_payload.get("component"),
        inner_meta.get("component"),
    )
    method = _coalesce(
        json_payload.get("method"),
        inner_meta.get("method"),
    )

    service_ctx = (json_payload.get("serviceContext") or {}) or (
        inner.get("serviceContext") or {}
    )
    service_name = service_ctx.get("service")
    service_version = service_ctx.get("version")

    resource_labels = (entry.get("resource") or {}).get("labels") or {}

    return Normalized(
        timestamp=entry.get("timestamp", ""),
        severity=(entry.get("severity") or "DEFAULT").upper(),
        container=resource_labels.get("container_name"),
        namespace=resource_labels.get("namespace_name"),
        pod=resource_labels.get("pod_name"),
        insert_id=entry.get("insertId", ""),
        message=message,
        transaction=transaction,
        tx_slice=tx_slice,
        user_id=user_id,
        user_id_path=user_id_path,
        component=component,
        method=method,
        service_name=service_name,
        service_version=service_version,
        format=fmt,
        raw=entry,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


_USER_ID_PATHS = [
    # (path-as-tuple, human-readable label)
    (("userId",), "userId"),
    # Flat signInUid — set by user-service verifyToken, so the orphan signals it logs
    # ("No profile for the token", "Decoded token without user Id") resolve their
    # Firebase UID. Without this, those entries report no userId. Older entries carry
    # the same uid as `tokenUserId`, a key the log redaction hides.
    (("signInUid",), "signInUid"),
    (("tokenUserId",), "tokenUserId"),
    (("verifiedToken", "tokenUserId"), "verifiedToken.tokenUserId"),
    (("context", "userId"), "context.userId"),
]


def _extract_user_id(
    json_payload: dict, inner_meta: dict
) -> tuple[Optional[str], Optional[str]]:
    """Walk the user-id fallback chain on jsonPayload, then on inner.meta. Return (uid, path-label)."""
    for path, label in _USER_ID_PATHS:
        v = _walk(json_payload, path)
        if v:
            return v, label
    for path, label in _USER_ID_PATHS:
        v = _walk(inner_meta, path)
        if v:
            return v, f"meta.{label}"
    return None, None


def _walk(d: Any, path: tuple) -> Optional[str]:
    cur = d
    for k in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur if isinstance(cur, str) and cur else None


def _coalesce(*values, default=None):
    """Return the first non-None, non-empty value."""
    for v in values:
        if v:
            return v
    return default
