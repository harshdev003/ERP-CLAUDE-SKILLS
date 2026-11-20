"""Add a section to an existing AI Knowledge page above its `# Sources`, and its source lines into that list.

`notion_rest.append_block_children` always appends at the end of a page, so on a page that ends
with Sources a new section lands below it. This places the section with one positioned insert
(`after` = the block just before the Sources heading) and appends the source lines after the last
block of the Sources list. Both anchors come from one fresh read of the page, taken before any
write, and each insert is a single batch: a positioned insert's `results[-1]` is the parent's tail,
not the last block inserted, so it is never used as the next anchor.

    from insert_section import add_section
    add_section(notion, page_id, [block_heading("New section", 2), block_paragraph("…")],
                [block_bullet("[Source](https://…) — what it is")])
"""

from __future__ import annotations

import json

from notion_rest import block_heading

MAX_BATCH = 100  # Notion's limit on children per request
HEADINGS = ("heading_1", "heading_2", "heading_3")


def _text(block: dict) -> str:
    body = block.get(block.get("type"), {}) or {}
    return "".join(r.get("plain_text") or r.get("text", {}).get("content", "")
                   for r in body.get("rich_text", []) or [])


def _urls(block: dict) -> set[str]:
    body = block.get(block.get("type"), {}) or {}
    out = set()
    for r in body.get("rich_text", []) or []:
        link = r.get("href") or (r.get("text", {}).get("link") or {}).get("url")
        if link:
            out.add(link)
    return out


def _is_sources(block: dict) -> bool:
    return block.get("type") in HEADINGS and _text(block).strip().lstrip("#").strip().lower() == "sources"


def find_sources(top: list[dict]) -> tuple[int, int] | None:
    """(index of the last top-level Sources heading, index of the last block of its list).

    The list runs until the next heading or the end of the page; an empty list ends at the heading.
    """
    idx = next((i for i in range(len(top) - 1, -1, -1) if _is_sources(top[i])), None)
    if idx is None:
        return None
    end = idx
    for j in range(idx + 1, len(top)):
        if top[j].get("type") in HEADINGS:
            break
        end = j
    return idx, end


def _insert_after(notion, page_id: str, anchor: str, children: list[dict]) -> None:
    notion._request("PATCH", f"/blocks/{page_id}/children",
                    data=json.dumps({"children": children, "after": anchor}))


def add_section(notion, page_id: str, section: list[dict], sources: list[dict] | None = None) -> dict:
    """Insert `section` above the page's Sources and add new `sources` lines to the end of its list.

    With no Sources heading, the section is appended at the end, followed by a new Sources heading
    and the source lines. An empty `section` adds only the source lines. A source line whose link or
    text is already in the list is skipped. Raises ValueError when a batch exceeds Notion's 100-block
    limit or a section would go above a Sources heading that is the page's first block (nothing to
    anchor it after); the caller holds the write for review.
    """
    sources = list(sources or [])
    if len(section) > MAX_BATCH or len(sources) + 1 > MAX_BATCH:
        raise ValueError(f"more than {MAX_BATCH} blocks in one insert; split the section")

    top = notion.get_block_children(page_id)
    found = find_sources(top)
    if found is None:
        if section:
            notion.append_block_children(page_id, section)
        if sources:
            notion.append_block_children(page_id, [block_heading("Sources", 1)] + sources)
        return {"placement": "end", "sources_added": len(sources), "sources_skipped": 0}

    heading, last = found
    if heading == 0 and section:
        raise ValueError("Sources is the page's first block; nothing to place the section after")
    list_anchor = top[last]["id"]

    listed = top[heading + 1:last + 1]
    known_urls = set().union(*(_urls(b) for b in listed)) if listed else set()
    known_text = {_text(b).strip() for b in listed}
    new = [s for s in sources if not (_urls(s) & known_urls) and _text(s).strip() not in known_text]

    if section:
        _insert_after(notion, page_id, top[heading - 1]["id"], section)
    if new:
        _insert_after(notion, page_id, list_anchor, new)
    return {"placement": "before_sources", "sources_added": len(new),
            "sources_skipped": len(sources) - len(new)}
