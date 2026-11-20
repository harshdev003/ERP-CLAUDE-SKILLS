"""Notion REST API helper for AINative skills.

Usage from a skill script:

    from notion_rest import notion, MCPFallback

    if notion.available:
        for row in notion.query_data_source(DS_ID, filter={"property": "Status", "select": {"is_empty": True}}):
            ...
    else:
        # fall back to the MCP path documented in the SKILL.md
        raise MCPFallback("NOTION_TOKEN unavailable; use MCP recipe")

Why this exists
---------------
The Notion MCP cannot enumerate or filter database rows — `notion-search` against
a `data_source_url` is semantic, capped at 25 results, and has no cursor. Any
sweep over a Learnings/* database (or any other AI Knowledge teamspace database)
either has to scrape the browser or burn thousands of MCP calls. The REST API
gives us structured `databases.query` with property filters and pagination —
minutes vs hours.

This module provides a thin wrapper around the REST API that:
- Reads `NOTION_TOKEN` from env once at import time.
- Exposes a single `notion` singleton with `.available` and high-level helpers.
- Yields rows across pages from `query_data_source` so callers don't handle
  cursors themselves.
- Raises `MCPFallback` when the token isn't available, so callers can branch
  cleanly into their existing MCP path.

Cowork caveat
-------------
`mcp__workspace__bash` strips host env (see PROD-1305 / CLAUDE.md "Known Cowork
constraints"). `NOTION_TOKEN` won't be visible inside Cowork sandboxes; skills
must keep the MCP fallback for that case.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Iterator, Optional

try:
    import requests
except ImportError as e:  # pragma: no cover - the helper is import-time
    raise SystemExit(
        "notion_rest requires the 'requests' library. Install with: pip install requests"
    ) from e


NOTION_VERSION = "2025-09-03"
NOTION_API = "https://api.notion.com/v1"


class MCPFallback(RuntimeError):
    """Raised when the REST path is unavailable; caller should use the MCP path.

    The skill author writes:

        try:
            rows = list(notion.query_data_source(ds_id, filter=...))
        except MCPFallback:
            rows = mcp_search_fallback(ds_id, ...)
    """


class _NotionClient:
    """Thin REST client. One per process. Stateless beyond the token."""

    def __init__(self, token: Optional[str] = None) -> None:
        self._token = token or os.environ.get("NOTION_TOKEN")
        self._session = requests.Session() if self._token else None
        if self._token:
            self._session.headers.update(
                {
                    "Authorization": f"Bearer {self._token}",
                    "Notion-Version": NOTION_VERSION,
                    "Content-Type": "application/json",
                }
            )

    @property
    def available(self) -> bool:
        """True if NOTION_TOKEN is set; callers should check this before using."""
        return self._token is not None

    def _require(self) -> None:
        if not self.available:
            raise MCPFallback(
                "NOTION_TOKEN is not set. Set it in ~/.claude/settings.json env "
                "block (see CLAUDE.md → 'Notion REST integration') or fall back "
                "to the MCP recipe documented in this skill's SKILL.md."
            )

    # ---- low-level wrappers ---------------------------------------------

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        self._require()
        url = f"{NOTION_API}{path}"
        # Retry on 429: honor full Retry-After, exponential backoff fallback.
        # 8 attempts so a bulk sweep (full DB query, full-teamspace audit) can
        # ride out a sustained throttle without the caller wrapping its own loop.
        for attempt in range(8):
            r = self._session.request(method, url, timeout=30, **kwargs)
            if r.status_code == 429:
                wait = float(r.headers.get("Retry-After", 2 ** attempt))
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        # Final raise after exhausted retries
        r.raise_for_status()
        return r.json()

    # ---- high-level helpers ---------------------------------------------

    def query_data_source(
        self,
        data_source_id: str,
        *,
        filter: Optional[dict] = None,
        sorts: Optional[list[dict]] = None,
        page_size: int = 100,
    ) -> Iterator[dict]:
        """Query a data source and yield rows across all pages.

        Args
        ----
        data_source_id: bare UUID, no `collection://` prefix.
        filter: a Notion filter object (see https://developers.notion.com/reference/post-database-query).
        sorts: optional list of sort objects.
        page_size: 1-100. Default 100 is right for sweeps.

        Yields
        ------
        Each row dict (with `id`, `properties`, etc.). Caller is responsible
        for cursor management — there isn't any to do.
        """
        body: dict[str, Any] = {"page_size": page_size}
        if filter is not None:
            body["filter"] = filter
        if sorts is not None:
            body["sorts"] = sorts
        cursor: Optional[str] = None
        while True:
            payload = dict(body)
            if cursor:
                payload["start_cursor"] = cursor
            resp = self._request(
                "POST",
                f"/data_sources/{data_source_id}/query",
                data=json.dumps(payload),
            )
            for row in resp.get("results", []):
                yield row
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
            if not cursor:
                break

    def get_page(self, page_id: str) -> dict:
        """Fetch a page's properties (not body — use get_block_children for that)."""
        return self._request("GET", f"/pages/{page_id}")

    def get_block_children(
        self,
        block_id: str,
        *,
        page_size: int = 100,
        recursive: bool = False,
        _depth: int = 0,
        max_depth: int = 3,
    ) -> list[dict]:
        """Fetch all children of a block (typically a page) across pagination.

        When ``recursive=True``, each returned block that has ``has_children``
        gets its own children fetched and attached under a synthetic
        ``"_children"`` key (Notion doesn't nest them in the list response).
        Recursion stops at ``max_depth`` (default 3 — enough for ingest's
        "follow children up to depth 3" read of a source doc). Non-recursive
        (the default) preserves the original one-level behaviour for callers
        that walk the tree themselves.
        """
        out: list[dict] = []
        cursor: Optional[str] = None
        while True:
            params = {"page_size": page_size}
            if cursor:
                params["start_cursor"] = cursor
            resp = self._request(
                "GET",
                f"/blocks/{block_id}/children",
                params=params,
            )
            out.extend(resp.get("results", []))
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
            if not cursor:
                break
        if recursive and _depth < max_depth:
            for blk in out:
                if blk.get("has_children"):
                    blk["_children"] = self.get_block_children(
                        blk["id"],
                        page_size=page_size,
                        recursive=True,
                        _depth=_depth + 1,
                        max_depth=max_depth,
                    )
        return out

    def create_page(
        self,
        parent_page_id: str,
        title: str,
        blocks: Optional[list[dict]] = None,
    ) -> dict:
        """Create a new page as a child of ``parent_page_id`` with body ``blocks``.

        This is the one page primitive the helper historically lacked — every
        page-*creation* recipe in the knowledge skills fell back to MCP
        ``notion-create-pages`` for want of it (see PROD-2207).

        Args
        ----
        parent_page_id: the page under which to create (a normal page parent,
            NOT a data_source — for database rows use the data-source parent
            shape + ``make_title`` per the title-by-ID gotcha in CLAUDE.md).
        title: the new page's title (plain text).
        blocks: a list of Notion block payloads (use the ``block_*`` builders
            below). Notion accepts up to 100 blocks on create; callers with
            longer bodies create with the first 100 then ``append_block_children``
            the rest.

        Returns the created page object (``id`` and ``url`` are the useful keys).
        """
        body: dict[str, Any] = {
            "parent": {"type": "page_id", "page_id": parent_page_id},
            "properties": {
                "title": {"title": [{"type": "text", "text": {"content": title}}]}
            },
        }
        if blocks:
            body["children"] = blocks[:100]
        page = self._request("POST", "/pages", data=json.dumps(body))
        if blocks and len(blocks) > 100:
            for i in range(100, len(blocks), 100):
                self.append_block_children(page["id"], blocks[i : i + 100])
        return page

    def create_database_row(
        self,
        data_source_id: str,
        properties: dict,
        blocks: Optional[list[dict]] = None,
    ) -> dict:
        """Create a row (page) in a database, parented to a ``data_source_id``.

        For Learnings/* rows and any other database write. ``properties`` maps
        property names to values built with ``make_title`` / ``make_select`` /
        ``make_rich_text`` / ``make_date``. Per the title-by-ID gotcha
        (CLAUDE.md → "Notion REST integration"), key the title property as
        ``"title"`` (lowercase) on creates, e.g.::

            notion.create_database_row(ds_id, {
                "title": make_title("Experiment X"),
                "Status": make_select("Live"),
            }, blocks=[block_heading("Sources", 2), block_bullet("…")])

        ``blocks`` is the row's page body (the content under the row). Same
        100-block create cap + append spillover as ``create_page``.
        """
        body: dict[str, Any] = {
            "parent": {"type": "data_source_id", "data_source_id": data_source_id},
            "properties": properties,
        }
        if blocks:
            body["children"] = blocks[:100]
        page = self._request("POST", "/pages", data=json.dumps(body))
        if blocks and len(blocks) > 100:
            for i in range(100, len(blocks), 100):
                self.append_block_children(page["id"], blocks[i : i + 100])
        return page

    def update_page_props(self, page_id: str, properties: dict) -> dict:
        """PATCH a page's properties. Pass only the properties you want changed."""
        return self._request(
            "PATCH",
            f"/pages/{page_id}",
            data=json.dumps({"properties": properties}),
        )

    def update_block(self, block_id: str, body: dict) -> dict:
        """PATCH a block. Body is the type-keyed payload, e.g.:

            {"paragraph": {"rich_text": [{"type": "text", "text": {"content": "..."}}]}}
        """
        return self._request("PATCH", f"/blocks/{block_id}", data=json.dumps(body))

    def append_block_children(self, block_id: str, children: list[dict]) -> dict:
        """Append child blocks to a parent. `children` is a list of block payloads."""
        return self._request(
            "PATCH",
            f"/blocks/{block_id}/children",
            data=json.dumps({"children": children}),
        )

    def search(
        self,
        query: str = "",
        *,
        filter: Optional[dict] = None,
        sort: Optional[dict] = None,
        page_size: int = 100,
    ) -> Iterator[dict]:
        """Workspace-level search. Yields results across pages.

        An **empty** ``query`` (the default) matches every page/database the
        integration can access — combined with ``sort`` by ``last_edited_time``
        descending, this is how callers enumerate "everything changed since
        date X": iterate and stop once ``last_edited_time`` drops below the
        cutoff (the caller does the stop test — see harvest-knowledge Step 4).

        ``sort`` is a Notion search sort object, e.g.::

            {"direction": "descending", "timestamp": "last_edited_time"}

        Note: Notion's ``search`` endpoint does **not** accept teamspace scoping
        or a created/edited date *filter* — only the ``last_edited_time`` sort
        above. Post-filter by teamspace ancestry and by an exact date cutoff in
        the caller.

        MCP ``notion-search`` is a different tool, not a faster version of this
        one. It scopes by ``teamspace_id`` server-side, which REST cannot do and
        which is genuinely useful. But it is semantic (a query string is
        required), caps at 25 results with no cursor, and exposes no
        ``last_edited_time`` sort or filter — so it cannot express "everything
        edited since X" in any form. Its only date filter is
        ``created_date_range``, which is **not** a stand-in: it filters on
        *creation*, so a page created before the cutoff and edited inside the
        window never enters the result set, and no client-side check can recover
        what was never returned. Edits to existing pages are usually the point of
        a changed-since sweep, so reaching for that filter silently drops most of
        the signal while returning a plausible-looking set. For a changed-since
        window, this REST path is the only one that can establish coverage.
        """
        body: dict[str, Any] = {"query": query, "page_size": page_size}
        if filter is not None:
            body["filter"] = filter
        if sort is not None:
            body["sort"] = sort
        cursor: Optional[str] = None
        while True:
            payload = dict(body)
            if cursor:
                payload["start_cursor"] = cursor
            resp = self._request("POST", "/search", data=json.dumps(payload))
            for row in resp.get("results", []):
                yield row
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
            if not cursor:
                break

    def root_page_of(self, page_id: str, *, max_hops: int = 8) -> Optional[str]:
        """Walk a page's parent chain to the top-level page under the workspace.

        Returns the id of the highest ancestor reachable before hitting a
        ``workspace`` parent (i.e. the teamspace's top-level page), or None if
        the chain can't be resolved within ``max_hops``. Used to map a search
        hit to its source teamspace when REST can't scope by teamspace_id —
        compare the returned id against a known set of canonical source root
        page ids. Each hop is one ``get_page`` call, so keep ``max_hops`` small.
        """
        current = page_id
        last_page_parent = page_id
        for _ in range(max_hops):
            parent = self.get_page(current).get("parent", {})
            ptype = parent.get("type")
            if ptype == "workspace":
                return last_page_parent
            if ptype == "page_id":
                last_page_parent = parent["page_id"]
                current = parent["page_id"]
            elif ptype in ("block_id", "database_id", "data_source_id"):
                current = parent.get(ptype)
                if not current:
                    return None
            else:
                return None
        return None


# Module-level singleton. Skills do `from notion_rest import notion`.
notion = _NotionClient()


# ---------- small helpers for common property shapes ----------

def title_text(page: dict, prop_name: str = "Title") -> str:
    """Extract plain text from a title property."""
    rt = page.get("properties", {}).get(prop_name, {}).get("title", [])
    return "".join(seg.get("plain_text", "") for seg in rt)


def rich_text_value(page: dict, prop_name: str) -> str:
    """Extract plain text from a rich_text property."""
    rt = page.get("properties", {}).get(prop_name, {}).get("rich_text", [])
    return "".join(seg.get("plain_text", "") for seg in rt)


def select_value(page: dict, prop_name: str) -> Optional[str]:
    sel = page.get("properties", {}).get(prop_name, {}).get("select")
    return sel.get("name") if sel else None


def date_start(page: dict, prop_name: str) -> Optional[str]:
    d = page.get("properties", {}).get(prop_name, {}).get("date")
    return d.get("start") if d else None


def make_title(content: str) -> dict:
    """Build a title property value.

    Note: under Notion API ``2025-09-03``, when creating a page whose parent
    is a ``data_source_id``, the title property must be keyed by its property
    ID (``"title"``, lowercase) — not by its display name. Example::

        properties = {"title": make_title("hideQP"), ...}  # not "Title"

    Updates via ``update_page_props`` accept the display name as the key.
    """
    return {"title": [{"type": "text", "text": {"content": content}}]}


def make_rich_text(content: str) -> dict:
    """Build a rich_text property value (e.g. ``{"Variants": make_rich_text("a:")}``)."""
    return {"rich_text": [{"type": "text", "text": {"content": content}}]}


def make_select(name: str) -> dict:
    return {"select": {"name": name}}


def make_date(start: str, end: Optional[str] = None) -> dict:
    body: dict[str, Any] = {"start": start}
    if end:
        body["end"] = end
    return {"date": body}


# ---------- block builders for page bodies (create_page / append) ----------
#
# These produce Notion block payloads for the rich page bodies that
# ingest-knowledge / harvest-knowledge / record-knowledge write. Inline
# markdown (`**bold**` and `[label](url)`) is parsed by `rich_text` so callers
# write readable strings instead of hand-assembling rich_text arrays.

import re as _re
import sys as _sys

# Order matters: `***` then `**` then `*`, or bold would parse as an
# italic run wrapping a stray asterisk. Italic requires non-space just
# inside each delimiter so prose ("a * b"), globs and `SELECT *` do not
# become emphasis.
_INLINE = _re.compile(
    r"(\*\*\*.+?\*\*\*"              # bold+italic
    r"|\*\*.+?\*\*"                 # bold
    r"|(?<!\*)\*(?:[^\W_]|[\[`])[^*]*?(?<!\s)\*(?!\*)"   # italic
    r"|\[[^\]]+\]\([^)]+\)"          # link
    r"|`[^`]+`)"                     # code
)

# Constructs an author might plausibly type that Notion supports but this
# parser does not. Leaving them unhandled is the silent failure the
# docstring below describes, so say so on stderr rather than shipping the
# markers as literal characters.
# Only `~~strike~~`: Notion accepts that as you-type markdown, so an author
# may reasonably type it. Underline has NO markdown form in Notion (it is
# Ctrl+U), so `__x__` is not a construct to warn about -- and treating it as
# one fires on every `__init__` and `__children` in the corpus.
_UNSUPPORTED = _re.compile(r"~~[^~]+~~")


def _with_annotation(run: dict, extra: dict) -> dict:
    """Fold an annotation into a run without dropping the ones already on it."""
    ann = dict(run.get("annotations") or {})
    ann.update(extra)
    run["annotations"] = ann
    return run


def _warn_unsupported(runs: list[dict]) -> None:
    """Warn when a run still carries markers for a construct we cannot parse.

    Without this the gap is silent in the one direction that matters: the page
    creates, the API returns 200, and the text reads correctly to whoever wrote
    it -- only a reader sees the raw markers. That is how ``*italic*`` shipped
    into AI Knowledge pages twice before anyone noticed.

    Code spans are exempt: their content is literal by definition.
    """
    for run in runs:
        if (run.get("annotations") or {}).get("code"):
            continue
        content = run.get("text", {}).get("content", "")
        m = _UNSUPPORTED.search(content)
        if m:
            print(f"notion_rest.rich_text: unsupported markdown left as literal text: "
                  f"{m.group(0)!r} -- Notion supports it but this parser does not",
                  file=_sys.stderr)


def rich_text(text: str, *, _nested: bool = False) -> list[dict]:
    """Parse ``**bold**``, ``*italic*``, ``[label](url)`` and ```code``` into rich_text.

    Bold, italic and link **recurse** into their content and fold their own attribute
    into whatever comes back, so nesting works in every direction: ``**[label](url)**``
    is a bold link, ``[**label**](url)`` is a linked bold run, ``***x***`` is both.

    A single flat pass cannot do that — it emits the inner markdown as literal
    characters. That failure is silent, which is what makes it worth the recursion:
    the page still creates, the call still returns 200, and the text still reads
    correctly to whoever wrote it. Only a reader notices the link is gone.

    Code spans are leaves. Backticks are literal in Markdown, so ``**`` inside a
    code span stays as containers rather than becoming an annotation. An unbalanced
    backtick matches nothing and is passed through as text.

    Italic is deliberately strict, in three ways, each closing a wrong answer that
    reads almost right:

    * The content must **open** with an alphanumeric, or with the delimiter of a
      construct it wraps (``[`` for a link, a backtick for code). The closer only
      has to be non-space. **The asymmetry is the point**: every glob and path that
      motivated this rule opens with ``/``, ``.`` or ``-`` — ``*.*``, ``*/*``,
      ``plugins/*/skills/*``, ``*-alert/*`` — so the opening test alone rejects all
      of them, while a symmetric rule would also reject ordinary emphasis ending in
      punctuation (``*Really?*``, ``*done.*``) and ship its markers into the page.
      Left unguarded, ``plugins/*/skills/*`` rendered as ``plugins//skills/`` with a
      path fragment italic and two slashes gone — and that failure cannot be caught
      downstream, because the delimiters are consumed so no leftover asterisk
      remains to warn about.
    * The opening ``*`` must not sit against another ``*``, or the italic closer
      eats the opening mark of a following ``**`` run and ``*x**y**`` parses ``y``
      as italic when bold was meant.
    * No ``*`` may appear inside the content, so emphasis never spans a bold run.

    Between them, ``a * b``, ``SELECT * FROM t``, ``*.md``, ``plugins/*/skills/*``
    and a trailing footnote asterisk all pass through untouched.

    Markdown does not define emphasis spanning emphasis (``*a **b** c*``), and this
    parser does not guess: the bold resolves and the outer delimiters stay literal.

    **A leftover asterisk is deliberately not warned about.** An earlier revision did
    warn, and measured against the 11,177 real text blocks in the AI Knowledge
    teamspace it produced ~30 false positives and not one true positive: engineering
    prose is full of field-name wildcards — ``premium*``, ``ai*``, ``*State``,
    ``track*``, ``videoSubscription*``, ``XCTAssert*``, ``.env*``, ``*Command.js`` —
    which are indistinguishable by shape from an unmatched emphasis delimiter. A
    warning that fires thirty times for every zero real defects gets muted, and then
    it protects nothing.

    ``~~strike~~`` *is* warned about: Notion accepts it as you-type markdown, this
    parser does not, and it had zero false positives on the same corpus. ``__x__`` is
    not — Notion has no markdown form for underline, so every ``__init__`` would fire.
    """
    out: list[dict] = []
    pos = 0
    for m in _INLINE.finditer(text):
        if m.start() > pos:
            out.append({"type": "text", "text": {"content": text[pos:m.start()]}})
        tok = m.group(0)
        # Dispatch on the delimiters the token actually CARRIES, never on its
        # prefix alone. The bold alternative can match a token that *starts*
        # with three asterisks -- `**` + `*a` + `**` spans all of `***a**` --
        # and routing that by prefix slices tok[3:-3] on a six-character token,
        # which is the empty string. The content is then silently destroyed.
        if tok.startswith("***") and tok.endswith("***") and len(tok) > 6:
            for run in rich_text(tok[3:-3], _nested=True):
                out.append(_with_annotation(run, {"bold": True, "italic": True}))
        elif tok.startswith("**") and tok.endswith("**") and len(tok) > 4:
            for run in rich_text(tok[2:-2], _nested=True):
                out.append(_with_annotation(run, {"bold": True}))
        elif tok.startswith("*"):
            for run in rich_text(tok[1:-1], _nested=True):
                out.append(_with_annotation(run, {"italic": True}))
        elif tok.startswith("`"):
            out.append({"type": "text", "text": {"content": tok[1:-1]},
                        "annotations": {"code": True}})
        else:
            lm = _re.match(r"\[([^\]]+)\]\(([^)]+)\)", tok)
            label, url = lm.group(1), lm.group(2)
            for run in rich_text(label, _nested=True):
                run["text"]["link"] = {"url": url}
                out.append(run)
        pos = m.end()
    if pos < len(text):
        out.append({"type": "text", "text": {"content": text[pos:]}})
    # Only the outermost call reports. The recursive calls that fold bold,
    # italic and link attributes each see the same leftover markers, so
    # warning in all of them makes one gap look like several.
    if not _nested:
        _warn_unsupported(out)
    return out or [{"type": "text", "text": {"content": ""}}]


def block_heading(text: str, level: int = 2) -> dict:
    key = f"heading_{level}"  # level 1-3
    return {"object": "block", "type": key, key: {"rich_text": rich_text(text)}}


def block_paragraph(text: str) -> dict:
    return {"object": "block", "type": "paragraph",
            "paragraph": {"rich_text": rich_text(text)}}


def block_bullet(text: str) -> dict:
    return {"object": "block", "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": rich_text(text)}}


def block_numbered(text: str) -> dict:
    return {"object": "block", "type": "numbered_list_item",
            "numbered_list_item": {"rich_text": rich_text(text)}}


def block_code(text: str, language: str = "plain text") -> dict:
    return {"object": "block", "type": "code",
            "code": {"rich_text": [{"type": "text", "text": {"content": text}}],
                     "language": language}}


def block_divider() -> dict:
    return {"object": "block", "type": "divider", "divider": {}}


def block_callout(text: str, emoji: str = "💡") -> dict:
    return {"object": "block", "type": "callout",
            "callout": {"rich_text": rich_text(text),
                        "icon": {"type": "emoji", "emoji": emoji}}}


def block_table(headers: list[str], rows: list[list[str]],
                has_column_header: bool = True) -> dict:
    """Build a table block. First row is the header when ``has_column_header``."""
    def _row(cells: list[str]) -> dict:
        return {"object": "block", "type": "table_row",
                "table_row": {"cells": [rich_text(c) for c in cells]}}
    children = [_row(headers)] + [_row(r) for r in rows]
    return {"object": "block", "type": "table",
            "table": {"table_width": len(headers),
                      "has_column_header": has_column_header,
                      "has_row_header": False,
                      "children": children}}


__all__ = [
    "notion",
    "MCPFallback",
    "title_text",
    "rich_text_value",
    "select_value",
    "date_start",
    "make_title",
    "make_rich_text",
    "make_select",
    "make_date",
    "rich_text",
    "block_heading",
    "block_paragraph",
    "block_bullet",
    "block_numbered",
    "block_code",
    "block_divider",
    "block_callout",
    "block_table",
]
