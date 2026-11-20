"""Markdown -> Notion block JSON converter for summarize-youtube-to-notion.

Notion's REST `POST /pages` takes the page body as an array of block objects
(`children`), not Markdown — unlike the MCP `notion-create-pages` tool, which
converts Markdown server-side. This module does that conversion locally so the
skill can create pages via REST and drop its MCP dependency.

It supports only the bounded construct set this skill emits (see SKILL.md Step 5):

    # / ## / ###      -> heading_1 / heading_2 / heading_3
    paragraphs        -> paragraph (consecutive non-blank lines joined by space)
    - / *  bullets    -> bulleted_list_item
    > quote lines     -> quote (consecutive `>` lines joined with newline)
    --- / *** / ___   -> divider

Inline: **bold**, *italic* / _italic_, `code`, and [text](url) links.

It is intentionally NOT a general Markdown parser. Tables, nested lists, images,
and HTML are out of scope — the skill never produces them.

Stdlib only. No third-party deps so it stays trivially testable offline.
"""

from __future__ import annotations

from typing import Any, Optional

# Notion caps a single rich_text text object at 2000 chars and a single
# `children` array at 100 blocks. The first is handled here (chunking); the
# block cap is handled by the caller (create then append in batches).
TEXT_LIMIT = 2000


def _text_obj(
    content: str,
    *,
    bold: bool = False,
    italic: bool = False,
    code: bool = False,
    link: Optional[str] = None,
) -> dict[str, Any]:
    obj: dict[str, Any] = {"type": "text", "text": {"content": content}}
    if link:
        obj["text"]["link"] = {"url": link}
    ann: dict[str, bool] = {}
    if bold:
        ann["bold"] = True
    if italic:
        ann["italic"] = True
    if code:
        ann["code"] = True
    if ann:
        obj["annotations"] = ann
    return obj


def _chunk(segments: list[dict[str, Any]], limit: int = TEXT_LIMIT) -> list[dict[str, Any]]:
    """Split any text object whose content exceeds Notion's per-object char limit."""
    out: list[dict[str, Any]] = []
    for seg in segments:
        content = seg["text"]["content"]
        if len(content) <= limit:
            out.append(seg)
            continue
        for start in range(0, len(content), limit):
            piece = {**seg, "text": {**seg["text"], "content": content[start : start + limit]}}
            out.append(piece)
    return out


def inline_to_rich_text(text: str) -> list[dict[str, Any]]:
    """Parse inline Markdown into a list of Notion rich_text objects.

    Positional scanner (not regex) so bold (`**`) and italic (`*`) don't fight,
    and so `[15:28]`-style brackets that aren't links fall through to plain text.
    """
    segments: list[dict[str, Any]] = []
    buf: list[str] = []
    i, n = 0, len(text)

    def flush_plain() -> None:
        if buf:
            segments.append(_text_obj("".join(buf)))
            buf.clear()

    while i < n:
        ch = text[i]

        # bold **...**  (checked before single * so it wins)
        if text.startswith("**", i):
            end = text.find("**", i + 2)
            if end != -1:
                flush_plain()
                segments.append(_text_obj(text[i + 2 : end], bold=True))
                i = end + 2
                continue

        # inline code `...`
        if ch == "`":
            end = text.find("`", i + 1)
            if end != -1:
                flush_plain()
                segments.append(_text_obj(text[i + 1 : end], code=True))
                i = end + 1
                continue

        # link [label](url) — only when a "](" actually follows
        if ch == "[":
            close = text.find("]", i + 1)
            if close != -1 and close + 1 < n and text[close + 1] == "(":
                end = text.find(")", close + 2)
                if end != -1:
                    flush_plain()
                    segments.append(
                        _text_obj(text[i + 1 : close], link=text[close + 2 : end])
                    )
                    i = end + 1
                    continue

        # italic *...* or _..._
        if ch in "*_":
            end = text.find(ch, i + 1)
            if end != -1 and end > i + 1:
                flush_plain()
                segments.append(_text_obj(text[i + 1 : end], italic=True))
                i = end + 1
                continue

        buf.append(ch)
        i += 1

    flush_plain()
    return _chunk(segments) or [_text_obj("")]


def _heading(level: int, text: str) -> dict[str, Any]:
    key = f"heading_{level}"
    return {"object": "block", "type": key, key: {"rich_text": inline_to_rich_text(text.strip())}}


def _block(block_type: str, rich: list[dict[str, Any]]) -> dict[str, Any]:
    return {"object": "block", "type": block_type, block_type: {"rich_text": rich}}


def markdown_to_blocks(md: str) -> list[dict[str, Any]]:
    """Convert the skill's summary Markdown into a list of Notion block objects."""
    lines = md.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[dict[str, Any]] = []
    para: list[str] = []
    i, n = 0, len(lines)

    def flush_para() -> None:
        if para:
            text = " ".join(line.strip() for line in para).strip()
            if text:
                blocks.append(_block("paragraph", inline_to_rich_text(text)))
            para.clear()

    while i < n:
        raw = lines[i]
        stripped = raw.strip()

        if stripped == "":
            flush_para()
            i += 1
            continue

        if stripped in ("---", "***", "___"):
            flush_para()
            blocks.append({"object": "block", "type": "divider", "divider": {}})
            i += 1
            continue

        if stripped.startswith("### "):
            flush_para()
            blocks.append(_heading(3, stripped[4:]))
            i += 1
            continue
        if stripped.startswith("## "):
            flush_para()
            blocks.append(_heading(2, stripped[3:]))
            i += 1
            continue
        if stripped.startswith("# "):
            flush_para()
            blocks.append(_heading(1, stripped[2:]))
            i += 1
            continue

        if stripped.startswith(("- ", "* ")):
            flush_para()
            while i < n and lines[i].strip().startswith(("- ", "* ")):
                item = lines[i].strip()[2:]
                blocks.append(_block("bulleted_list_item", inline_to_rich_text(item)))
                i += 1
            continue

        if stripped.startswith(">"):
            flush_para()
            qlines: list[str] = []
            while i < n and lines[i].strip().startswith(">"):
                q = lines[i].strip()[1:]
                if q.startswith(" "):
                    q = q[1:]
                qlines.append(q)
                i += 1
            blocks.append(_block("quote", inline_to_rich_text("\n".join(qlines).strip())))
            continue

        para.append(raw)
        i += 1

    flush_para()
    return blocks
