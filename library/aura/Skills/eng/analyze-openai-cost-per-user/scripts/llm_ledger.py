"""Read the IntelligenceService `llmCall` ledger from BigQuery (`aura-main.llm.llm_call`).

`rows(params)` takes filters (`userId`, `conversationId`, `prompterName`, `project`,
`transaction`, `openaiId`, `openaiAPI`, `dialogueId`, `provider`, `serviceTier`) and the
controls `from`, `to`, `limit`, `offset`, `order`, and returns one dict per row: the
table's camelCase columns, `createdAt` as an ISO string ending in `Z`, `usage` /
`callChain` as parsed JSON. `stats(params)` returns per-`openaiAPI` totals for the same
filters.

Three table properties every reader has to respect:

- **Partition filter required.** The table is day-partitioned on `createdAt` with
  `require_partition_filter`, so every query carries a `createdAt` bound. A call with no
  `from` reads from `EPOCH_FLOOR`: a full scan of the table (about 1.7 GB at 97 days of history,
  `MAX_BYTES` is the ceiling), since only `userId` and `project` are clustered.
- **Every row counts.** Rows are not deduplicated on `openaiId`: a repeat can be a real
  handoff to another prompt (Jerome, 2026-10-27).
- **Filters are exact-match**, like the endpoint. A caller that has to merge UUID casings
  still queries each casing.

Auth is `GCP_KEY` through the sibling `bq.py`. Failures raise `LedgerError`.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import bq

TABLE = "aura-main.llm.llm_call"
EPOCH_FLOOR = "2000-01-01T00:00:00Z"
FILTERS = ("userId", "conversationId", "prompterName", "project", "transaction",
           "openaiId", "openaiAPI", "dialogueId", "provider", "serviceTier")
# Request keys that are not filters. Anything else raises: the endpoint this replaces
# dropped unknown params and answered with the UNFILTERED total, so `prompter=` for
# `prompterName=` read as a plausible whole-window number.
CONTROL = ("from", "to", "limit", "offset", "order")
# The table is clustered by userId and project; anything else scans the window's partitions.
MAX_BYTES = 50 * 1024**3


class LedgerError(RuntimeError):
    pass


def _ts(value: str, *, end: bool = False) -> str:
    """The endpoint took a plain date or a timestamp. A plain-date `to` covers that whole day."""
    s = str(value).strip()
    if len(s) == 10:
        d = dt.date.fromisoformat(s)
        if end:
            d += dt.timedelta(days=1)
        return f"{d.isoformat()}T00:00:00Z"
    return s


def _iso(v: Any) -> Any:
    if isinstance(v, dt.datetime):
        return v.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{v.microsecond // 1000:03d}Z"
    return v


def _where(params: dict) -> tuple[list[str], dict]:
    unknown = sorted(set(params) - set(FILTERS) - set(CONTROL))
    if unknown:
        raise LedgerError(f"unsupported ledger filter(s): {', '.join(unknown)}; "
                          f"supported: {', '.join(FILTERS)}")
    where = ["createdAt >= TIMESTAMP(@from_ts)"]
    qp: dict[str, Any] = {"from_ts": _ts(params.get("from") or EPOCH_FLOOR)}
    if params.get("to"):
        # A plain date is inclusive of the day; a timestamp is an exclusive upper bound.
        where.append("createdAt < TIMESTAMP(@to_ts)")
        qp["to_ts"] = _ts(params["to"], end=True)
    for key in FILTERS:
        if params.get(key) not in (None, ""):
            where.append(f"{key} = @{key}")
            qp[key] = str(params[key])
    return where, qp


def build(params: dict) -> tuple[str, dict]:
    """SQL and query parameters for one `rows` request."""
    where, qp = _where(params)
    order = "ASC" if str(params.get("order") or "").lower() == "asc" else "DESC"
    sql = (f"SELECT * FROM `{TABLE}` WHERE {' AND '.join(where)}\n"
           f"ORDER BY createdAt {order}, id {order}")
    if params.get("limit") not in (None, ""):
        sql += "\nLIMIT @lim"
        qp["lim"] = int(params["limit"])
        if params.get("offset") not in (None, ""):
            sql += " OFFSET @off"
            qp["off"] = int(params["offset"])
    return sql, qp


def rows(params: dict | None = None, *, max_bytes: int = MAX_BYTES) -> list[dict]:
    """Ledger rows for one request, newest first unless `order=asc`."""
    sql, qp = build(params or {})
    try:
        out = bq.query(sql, max_bytes=max_bytes, params=qp)
    except Exception as e:  # noqa: BLE001 -- job.result() raises google exceptions bq.query doesn't wrap
        raise LedgerError(f"BigQuery {TABLE} read failed ({type(e).__name__}: {e})") from e
    return [{k: _iso(v) for k, v in r.items()} for r in out]


def stats(params: dict | None = None, *, max_bytes: int = MAX_BYTES) -> dict[str, dict]:
    """Per-`openaiAPI` totals for the same filters: `{api: {calls, inputTokens, outputTokens}}`."""
    where, qp = _where({k: v for k, v in (params or {}).items() if k not in ("limit", "offset", "order")})
    sql = (f"SELECT openaiAPI, COUNT(*) AS calls, SUM(inputTokens) AS inputTokens, "
           f"SUM(outputTokens) AS outputTokens FROM `{TABLE}` WHERE {' AND '.join(where)} GROUP BY openaiAPI")
    try:
        out = bq.query(sql, max_bytes=max_bytes, params=qp)
    except Exception as e:  # noqa: BLE001 -- job.result() raises google exceptions bq.query doesn't wrap
        raise LedgerError(f"BigQuery {TABLE} stats failed ({type(e).__name__}: {e})") from e
    return {(r.get("openaiAPI") or "unknown"): {k: v for k, v in r.items() if k != "openaiAPI"} for r in out}
