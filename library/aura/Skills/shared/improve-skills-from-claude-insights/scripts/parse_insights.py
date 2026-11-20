#!/usr/bin/env python3
"""Extract the actionable sections of a Claude Code usage-insights report.

The `/insights` command writes an HTML report to
``~/.claude/usage-data/report.html`` (or a dated ``report-<date>.html``). This script pulls the structured,
improvement-relevant sections out of that fixed template and emits them as JSON,
so the skill reasons over signal instead of raw HTML.

Sections extracted:
  * meta       -- title/subtitle (date range, message + session counts)
  * glance     -- the At-a-Glance paragraphs (what's working / hindering / etc.)
  * friction   -- "Where Things Go Wrong": {title, desc, examples[]}
  * claude_md  -- "Suggested CLAUDE.md Additions": {text, why}
  * patterns   -- "New Ways to Use Claude Code": {title, summary, detail}
  * horizon    -- "On the Horizon": {title, possible, tip}

Stdlib only (html.parser) so it runs on any interpreter. The report is a stable,
self-generated template, so class-name-driven extraction is reliable.

Usage:
    parse_insights.py [--report <path>]     # newest report if omitted
    parse_insights.py --list                # list available reports, newest first
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from html.parser import HTMLParser

DEFAULT_DIR = os.path.expanduser("~/.claude/usage-data")

# container class -> the result list each instance appends a record to
_CONTAINERS = {
    "friction-category": "friction",
    "pattern-card": "patterns",
    "horizon-card": "horizon",
    "claude-md-item": "claude_md",
}
# leaf class -> (field name on the current record, append-to-list?). The two
# `__…__` sentinels are standalone (not inside a container) and route straight
# into the result. Structural association (a leaf fills a field on whatever
# container is currently open) keeps each category's evidence grouped, instead
# of flattening then re-chunking by count.
_FIELDS = {
    "glance-section": ("__glance__", False),
    "subtitle": ("__subtitle__", False),
    "friction-title": ("title", False),
    "friction-desc": ("desc", False),
    "friction-examples": ("examples", True),   # <ul>; each <li> is one example
    "cmd-why": ("why", False),
    "pattern-title": ("title", False),
    "pattern-summary": ("summary", False),
    "pattern-detail": ("detail", False),
    "horizon-title": ("title", False),
    "horizon-possible": ("possible", False),
    "horizon-tip": ("tip", False),
}


def newest_report(directory: str = DEFAULT_DIR) -> str | None:
    reports = sorted(
        glob.glob(os.path.join(directory, "report*.html")),
        key=os.path.getmtime,
        reverse=True,
    )
    return reports[0] if reports else None


def list_reports(directory: str = DEFAULT_DIR) -> list[str]:
    return sorted(
        glob.glob(os.path.join(directory, "report*.html")),
        key=os.path.getmtime,
        reverse=True,
    )


class _InsightsParser(HTMLParser):
    """Structurally associate each leaf's text with its containing record.

    A container class (friction-category / pattern-card / horizon-card /
    claude-md-item) opens a fresh record appended to the matching result list.
    Leaf classes inside it fill named fields on that record; `friction-examples`
    is a <ul> whose every <li> appends one example. Standalone leaves
    (glance-section, subtitle) route straight into the result. No cross-category
    flattening, so counts never have to line up.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.result: dict = {
            "meta": {}, "glance": [], "friction": [],
            "patterns": [], "horizon": [], "claude_md": [],
        }
        # stack entries: (tag, kind, payload) where kind in {None,"container","field"}
        self._stack: list[tuple[str, str | None, object]] = []
        self._rec: dict | None = None            # current open container record
        self._field: tuple[str, bool] | None = None  # (name, is_list)
        self._buf: list[str] = []

    # -- helpers ---------------------------------------------------------
    def _norm(self, chunks: list[str]) -> str:
        return " ".join("".join(chunks).split()).strip()

    def _emit(self, text: str) -> None:
        """Route a completed field value to the right place."""
        if not text or self._field is None:
            return
        name, is_list = self._field
        if name == "__glance__":
            self.result["glance"].append(text)
        elif name == "__subtitle__":
            self.result["meta"]["subtitle"] = text
        elif self._rec is not None:
            if is_list:
                self._rec.setdefault(name, []).append(text)
            else:
                self._rec[name] = text

    # -- HTMLParser hooks ------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        ad = dict(attrs)
        classes = (ad.get("class") or "").split()

        # CLAUDE.md suggestion text lives on the checkbox's data-text attr,
        # inside the current claude-md-item record.
        if tag == "input" and "cmd-checkbox" in classes and ad.get("data-text") and self._rec is not None:
            self._rec["text"] = ad["data-text"]

        container = next((c for c in classes if c in _CONTAINERS), None)
        field = next((c for c in classes if c in _FIELDS), None)

        if container:
            rec: dict = {}
            self.result[_CONTAINERS[container]].append(rec)
            self._rec = rec
            self._stack.append((tag, "container", rec))
            return
        if field:
            self._buf = []
            self._field = _FIELDS[field]
            self._stack.append((tag, "field", self._field))
            return
        # within a list field (friction-examples <ul>), each <li> is one item
        if tag == "li" and self._field is not None and self._field[1]:
            self._buf = []
            self._stack.append((tag, "li", None))
            return
        self._stack.append((tag, None, None))

    def handle_endtag(self, tag: str) -> None:
        # pop back to the matching opener
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag:
                kind = self._stack[i][1]
                if kind == "li":
                    self._emit(self._norm(self._buf))
                    self._buf = []
                elif kind == "field":
                    if not (self._field and self._field[1]):
                        self._emit(self._norm(self._buf))  # scalar field
                    self._buf = []
                    self._field = None
                elif kind == "container":
                    self._rec = None
                del self._stack[i:]
                return

    def handle_data(self, data: str) -> None:
        if self._field is not None:
            self._buf.append(data)


def parse(html: str) -> dict:
    p = _InsightsParser()
    p.feed(html)
    # normalise: ensure friction records always carry an examples list
    for f in p.result["friction"]:
        f.setdefault("examples", [])
    return p.result


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report", help="Path to a report*.html (default: newest by mtime).")
    ap.add_argument("--dir", default=DEFAULT_DIR, help="Reports directory.")
    ap.add_argument("--list", action="store_true", help="List reports, newest first.")
    args = ap.parse_args()

    if args.list:
        for r in list_reports(args.dir):
            print(r)
        return

    path = args.report or newest_report(args.dir)
    if not path:
        sys.exit(
            f"No insights report found in {args.dir}. Run /insights first, or pass "
            "--report <path>."
        )
    if not os.path.isfile(path):
        sys.exit(f"Report not found: {path}")

    with open(path, encoding="utf-8") as f:
        data = parse(f.read())
    data["meta"]["report_path"] = path
    print(json.dumps(data, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
