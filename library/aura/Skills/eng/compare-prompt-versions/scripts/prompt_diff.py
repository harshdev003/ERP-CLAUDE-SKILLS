"""What each candidate changed against the baseline prompt version.

Written next to the readout so the result of a comparison can be carried back
into the production prompt: the diff is the edit to make, and the readout says
whether it was worth making. Deterministic; the short prose summary of the
change is written from this file, not from memory of the edit.
"""
from __future__ import annotations

import difflib
import json
from typing import Any


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "".join(p.get("text", "") for p in content or [] if isinstance(p, dict))


def message_diff(base: list[dict[str, Any]], cand: list[dict[str, Any]]) -> list[str]:
    """Unified diff of the template messages, one block per message position."""
    out: list[str] = []
    for i in range(max(len(base), len(cand))):
        b = base[i] if i < len(base) else {"role": "(none)", "content": ""}
        c = cand[i] if i < len(cand) else {"role": "(none)", "content": ""}
        lines = list(difflib.unified_diff(_text(b.get("content")).splitlines(), _text(c.get("content")).splitlines(),
                                          f"baseline message {i} ({b.get('role')})",
                                          f"candidate message {i} ({c.get('role')})", n=2, lineterm=""))
        out += lines
    return out


def changes(base: Any, cand: Any) -> dict[str, Any]:
    """Message diff, tools added/removed, and params that differ."""
    bt, ct = {t.name for t in base.tools}, {t.name for t in cand.tools}
    params = {k: (base.params.get(k), cand.params.get(k)) for k in sorted(set(base.params) | set(cand.params))
              if base.params.get(k) != cand.params.get(k)}
    diff = message_diff(base.messages, cand.messages)
    return {"model": (base.model, cand.model) if base.model != cand.model else None,
            "tools_added": sorted(ct - bt), "tools_removed": sorted(bt - ct), "params": params,
            "lines_added": sum(1 for line in diff if line.startswith("+") and not line.startswith("+++")),
            "lines_removed": sum(1 for line in diff if line.startswith("-") and not line.startswith("---")),
            "diff": diff}


def render(bundles: list[tuple[str, Any]]) -> str:
    """Markdown for every candidate against the first (baseline) version."""
    if not bundles:
        return ""
    base_label, base = bundles[0]
    lines = [f"# Prompt changes against `{base_label}` ({base.slug} @ {base.version})", ""]
    for label, cand in bundles[1:]:
        c = changes(base, cand)
        lines += [f"## {label} ({cand.slug} @ {cand.version})", "",
                  f"- Lines added / removed: {c['lines_added']} / {c['lines_removed']}"]
        if c["model"]:
            lines.append(f"- Model: `{c['model'][0]}` → `{c['model'][1]}`")
        if c["tools_added"] or c["tools_removed"]:
            lines.append(f"- Tools added: {c['tools_added'] or 'none'} · removed: {c['tools_removed'] or 'none'}")
        for k, (old, new) in c["params"].items():
            lines.append(f"- Param `{k}`: `{json.dumps(old)}` → `{json.dumps(new)}`")
        if not c["diff"] and not c["model"] and not c["params"] and not (c["tools_added"] or c["tools_removed"]):
            lines.append("- No change: identical to the baseline.")
        if c["diff"]:
            lines += ["", "```diff", *c["diff"], "```"]
        lines.append("")
    return "\n".join(lines)
