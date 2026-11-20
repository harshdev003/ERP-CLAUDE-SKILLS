"""Stacktrace + breadcrumb normalization per app family.

Bugsnag events have a nested shape: error class, message, and the
stacktrace live under `event.exceptions[0]`, not at the event root.
This module exposes small extractors that walk the right path (with
top-level fallback for older payloads), plus the shared Frame type
that format.py consumes.

The `stack` parameter on parse_frames is for future per-family
specialisation (e.g., Electron `main` vs `renderer` disambiguation);
today the normalization is mostly stack-agnostic.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Frame:
    method: str
    file: str
    line: int | None
    column: int | None
    in_project: bool


# --- exception extractors --------------------------------------------
#
# Bugsnag's event payload (returned by `/projects/<id>/events/<id>` and
# nested inside `/errors/<id>/events`) puts error_class, message, and
# stacktrace under `exceptions[0]`. The pre-fix renderers read these
# from the top level and rendered empty. These helpers fix that, with a
# top-level fallback in case some older payload variant exposes them
# directly.

def extract_exception(event: dict[str, Any]) -> dict[str, Any]:
    """Return the first exception dict from a Bugsnag event payload."""
    exceptions = event.get("exceptions") or []
    if exceptions:
        return exceptions[0] or {}
    return {}


def extract_error_class(event: dict[str, Any]) -> str:
    return (
        extract_exception(event).get("error_class")
        or event.get("error_class")
        or ""
    )


def extract_message(event: dict[str, Any]) -> str:
    return (
        extract_exception(event).get("message")
        or event.get("message")
        or ""
    )


def extract_stacktrace(event: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull the raw stacktrace frame list out of an event payload."""
    return (
        extract_exception(event).get("stacktrace")
        or event.get("stacktrace")
        or []
    )


def _first(frame: dict[str, Any], *keys: str) -> Any:
    """First key present in the frame, so both wire spellings are accepted."""
    for key in keys:
        if key in frame:
            return frame[key]
    return None


def parse_frames(raw_frames: list[dict[str, Any]] | None,
                 stack: str = "unknown") -> list[Frame]:
    """Normalize Bugsnag's `stacktrace` array to a list of Frames.

    The Data Access API — the only API these skills call — spells frame fields in
    snake_case: `line_number`, `column_number`, `in_project`. Those are read
    first. The notifier's own ingestion payload uses a different spelling for the
    same fields; it is accepted as a fallback so a hand-pasted notifier body
    still parses, and is never what this API returns.

    Field-name order matters because a miss here is silent and total rather than
    loud: an unmatched name yields no line number on *every* frame and sends
    `in_project` to the `stack` default, while the stacktrace still renders with
    plausible function names. There is no error and no empty result to notice.

    `stack` is the family's stack key (see projects.py); it defaults `in_project`
    only when the field is genuinely absent, which the Data Access API does not do.
    """
    if not raw_frames:
        return []
    default_in_project = stack in {"react", "react-native", "ios-native"}
    out: list[Frame] = []
    for f in raw_frames:
        line = _first(f, "line_number", "lineNumber")
        col = _first(f, "column_number", "columnNumber")
        in_project = _first(f, "in_project", "inProject")
        out.append(Frame(
            method=f.get("method") or f.get("function") or "<anonymous>",
            file=f.get("file") or f.get("fileName") or "",
            line=line if isinstance(line, int) else None,
            column=col if isinstance(col, int) else None,
            in_project=default_in_project if in_project is None else bool(in_project),
        ))
    return out


def top_in_project_frame(frames: list[Frame]) -> Frame | None:
    """Pick the most informative frame: first in-project, else first frame."""
    for f in frames:
        if f.in_project:
            return f
    return frames[0] if frames else None


def format_frame(frame: Frame, indent: str = "  ") -> str:
    line_part = f":{frame.line}" if frame.line else ""
    col_part = f":{frame.column}" if frame.column else ""
    in_proj = "" if frame.in_project else "  (vendor)"
    return f"{indent}{frame.file}{line_part}{col_part}  {frame.method}{in_proj}"


def parse_breadcrumbs(raw: list[dict[str, Any]] | None,
                      max_count: int = 8) -> list[dict[str, Any]]:
    """Normalize the breadcrumbs array, keeping only the last N.

    Preserves the raw `metaData` dict so renderers can surface
    request URL / method / status on `[request]`-type breadcrumbs
    (which is where the failed-XHR target lives).
    """
    if not raw:
        return []
    out: list[dict[str, Any]] = []
    for b in raw[-max_count:]:
        out.append({
            "ts":       str(b.get("timestamp") or ""),
            "type":     str(b.get("type") or ""),
            "name":     str(b.get("name") or ""),
            "metaData": b.get("metaData") or b.get("metadata") or {},
        })
    return out


def request_breadcrumb_summary(metadata: dict[str, Any]) -> str:
    """Pull a 'METHOD URL → STATUS' summary out of a [request] breadcrumb's metaData.

    The Bugsnag JS SDK varies the exact shape over time; this is forgiving:
    looks at top-level keys, plus a nested `request` block, and grabs whatever's
    URL-, method-, and status-shaped. Returns "" if nothing useful is there.
    """
    if not metadata:
        return ""
    # Some shapes nest under `request`, others put fields at top level.
    nested = metadata.get("request") if isinstance(metadata.get("request"), dict) else {}

    def first(*keys: str) -> str:
        for source in (metadata, nested):
            for k in keys:
                v = source.get(k)
                if v:
                    return str(v)
        return ""

    url = first("url", "request_url", "endpoint")
    method = first("method", "request_method", "verb").upper() or ""
    status = first("status_code", "status", "response_status")
    parts: list[str] = []
    if method:
        parts.append(method)
    if url:
        parts.append(url)
    if status:
        parts.append(f"→ {status}")
    return " ".join(parts)
