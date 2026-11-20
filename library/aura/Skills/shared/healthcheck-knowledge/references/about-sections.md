# Resolving About's sections

How to turn the AI Knowledge About (router) page into the `{section title → page id}` map that Step 2 builds the taxonomy from.

## The sections are body links, not child pages

About's ten sections are inline links in its body rich_text. They are **not** child pages of About — a `child_page` walk over About returns exactly one result, `Test Page - Delete`, and zero sections. A walk built that way hands every downstream step an empty taxonomy while looking like it succeeded.

Resolve them from the body: walk `get_block_children(ABOUT, recursive=True)` and collect every `link_to_page` block target plus every `rich_text[].href` that points at a Notion page.

**Skip `child_page` subtrees while walking.** `recursive=True` descends into child pages as well, so a link sitting inside `Test Page - Delete` would be collected as a section. That child is empty of page links today, which means an implementation missing this guard passes a live check and only inflates the section map later, once anyone drops a link into it. Note the asymmetry: a `link_to_page` block **is** a section pointer and must be collected; a `child_page` block is a nested page whose body belongs to it, not to About.

## Recipe

```python
import re
from notion_rest import notion, title_text

NOTION_ID_RE = re.compile(r"([0-9a-f]{32})", re.I)

def section_links(page_id: str) -> dict[str, str]:
    """{section title -> page id} resolved from a router page's body links."""
    ids: list[str] = []
    seen: set[str] = set()

    def walk(blocks):
        for b in blocks:
            if b["type"] == "child_page":
                # Skip the subtree. get_block_children(recursive=True) fetches a
                # child page's body too, and those links are that page's, not
                # About's — harvesting them would inflate the section map.
                continue
            if b["type"] == "link_to_page":
                target = (b["link_to_page"].get("page_id") or "").replace("-", "")
                if target and target not in seen:
                    seen.add(target); ids.append(target)
            payload = b.get(b["type"], {})
            if isinstance(payload, dict):
                for rt in payload.get("rich_text", []):
                    href = rt.get("href") or ""
                    if "notion.so" not in href and not href.startswith("/"):
                        continue  # external link — not a section
                    found = NOTION_ID_RE.findall(href.split("#")[0].split("?")[0])
                    if found and found[-1] not in seen:
                        seen.add(found[-1]); ids.append(found[-1])
            walk(b.get("_children", []))

    walk(notion.get_block_children(page_id, recursive=True))
    return {title_text(notion.get_page(pid), "title"): pid for pid in ids}

sections = section_links(AI_KNOWLEDGE_ABOUT_PAGE)
if not sections:
    raise SystemExit(
        "About resolved 0 sections — the walk is broken or About was restructured. "
        "Aborting: every downstream step would run against an empty taxonomy."
    )
```

The ten sections that resolve against the live page: Compliance, Domain, Guide, Identity, Learnings, Procedures, Products, Signals, Team, Tools. `Guide` is linked twice — once in the intro paragraph, once in its own bullet — so dedupe by page id.

The count is **discovered, not asserted**. A tenth section added to About flows through without a code change; do not hardcode nine anywhere.

## An empty section map is a hard error

Abort the run and say why. Never continue against an empty map, and never report a result computed from one — a zero-section walk is indistinguishable from a quiet teamspace in every downstream output, which is what makes it dangerous rather than merely wrong. A `Guide/Index` regen driven by an empty map will happily replace a full index with nothing.

Treat a sharp drop in section count the same way. Falling from nine to one or two is the same failure wearing a different number, not a real restructure.

This abort is deliberately **not** covered by the graceful-degradation / fail-open rules in the skill body. Those cover one source, one page, or one detector failing while the rest of the run stays meaningful. A broken entry point is different in kind: nothing downstream is meaningful, so continuing manufactures a false clean result.

## MCP fallback

`notion-fetch(AI_KNOWLEDGE_ABOUT_PAGE)` renders About's body as markdown with the section links intact — read the links out of the rendered body. Both rules above still hold: do not enumerate children, and abort on zero.
