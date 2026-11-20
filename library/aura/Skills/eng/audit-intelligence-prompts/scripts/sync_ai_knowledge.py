#!/usr/bin/env python3
"""
sync_ai_knowledge.py — Keep the "Full prompter inventory & trigger model" table on
the Tools/OpenAI AI Knowledge page derived from IntelligenceService config.js.

Two modes:
  diff       (default) — report additions / stale models, optionally patch in place.
  regenerate — rebuild the whole table from config.js, preserving the hand-written
               Role and Trigger prose, and stamp the commit the values were read at.

Usage:
  python3 sync_ai_knowledge.py --repo /path/to/IntelligenceService
  python3 sync_ai_knowledge.py --repo /path/to/IntelligenceService --apply
  python3 sync_ai_knowledge.py --repo /path/to/IntelligenceService --regenerate
  python3 sync_ai_knowledge.py --repo /path/to/IntelligenceService --regenerate --apply
    ^ only after the plan above has been shown to the operator and approved:
      --regenerate --apply deletes and recreates the live table.

Output:
  Prints a plan to stdout. With --apply, writes the approved plan to Notion.
  Requires NOTION_TOKEN in env for any Notion access.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

# ── Notion IDs ── Tools/OpenAI page in AI Knowledge ──────────────────────────
TOOLS_OPENAI_PAGE_ID = "34c75e23-af20-812c-bac8-e0fcb0eefe1e"
# Fallback only. --regenerate deletes and recreates the table (Notion refuses to
# change table_width in place), so the live id moves; locate_inventory() resolves
# it from the section heading instead.
PROMPTER_TABLE_ID    = "36c75e23-af20-81ab-91a3-c7ffbf5eec43"
TOOLS_OPENAI_URL     = "https://app.notion.com/p/OpenAI-34c75e23af20812cbac8e0fcb0eefe1e"

INVENTORY_HEADING = "Full prompter inventory & trigger model"
# The script owns the paragraph directly under the table and rewrites it on every
# regenerate; this prefix is how it recognises its own block rather than appending
# a second stamp each run.
STAMP_PREFIX = "Derived from src/config.js at commit"

# Logical column names we resolve in the Notion table. Prompter/Model/Reasoning/
# Verbosity/Tier/Role match by exact title; the trigger column matches by prefix, so a
# live rename like "Trigger" → "Trigger / gating" still resolves. Reasoning,
# Verbosity and Tier are absent from a pre-regenerate table and resolve to None
# there. See resolve_columns().
TABLE_COLS = ["Prompter", "Model", "Reasoning", "Verbosity", "Tier", "Role", "Trigger"]

# Column order the regenerated table is built in. Config-derived columns first so
# the two prose columns stay adjacent at the end.
REGEN_HEADERS = ["Prompter", "Model", "Reasoning", "Verbosity", "Tier", "Role",
                 "Trigger / gating"]

# An absent `serviceTier` means the OpenAI default tier, not "unset" -- so this
# column renders a real value on every row rather than the em dash the other
# generated columns use for a missing key. Rendering it as "—" would read as
# "no tier decided", which is the opposite of what the config means, and would
# make every standard-tier row look like a gap to fill in.
DEFAULT_TIER = "standard"

# ── config.js parser ──────────────────────────────────────────────────────────
# Mirrors audit.py:parse_config_prompters — keep these two implementations in sync.
# re.S so .+? crosses newlines; re.M for ^/$ per-line anchors.
PROMPT_BLOCK_RE = re.compile(
    r"^    (\w+): \{(.+?)^    \},",
    re.M | re.S,
)


def _coerce(v: str):
    v = v.strip()
    if (v.startswith("'") and v.endswith("'")) or (v.startswith('"') and v.endswith('"')):
        return v[1:-1]
    if v in ("true", "false"):
        return v == "true"
    if v.lstrip("-").isdigit():
        return int(v)
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        if not inner:
            return []
        parts = [p.strip() for p in inner.split(",") if p.strip()]
        out = []
        for p in parts:
            if (p.startswith("'") and p.endswith("'")) or (p.startswith('"') and p.endswith('"')):
                out.append(p[1:-1])
            else:
                out.append(p)
        return out
    return v


def _parse_prompter_block(block: str) -> dict:
    out: dict = {}
    for raw_line in block.splitlines():
        line = raw_line.split("//", 1)[0].rstrip()
        m = re.match(r"\s+(\w+):\s*(.+?),?\s*$", line)
        if not m:
            continue
        k, v = m.group(1), m.group(2).rstrip(",")
        out[k] = _coerce(v)
    return out


def parse_config_prompters(repo_dir: str) -> dict[str, dict]:
    """Return {configKey: {name, model, project, ...}} from src/config.js."""
    path = os.path.join(repo_dir, "src", "config.js")
    if not os.path.exists(path):
        raise SystemExit(f"src/config.js not found at {path}")
    text = open(path, encoding="utf-8").read()

    m = re.search(r"^  prompt: \{$", text, re.M)
    if not m:
        raise SystemExit("could not locate `prompt: {` block in src/config.js")
    start = m.end()
    depth, i = 1, start
    while i < len(text) and depth > 0:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
        i += 1
    if depth != 0:
        raise SystemExit("unbalanced braces in src/config.js prompt block")
    block = text[start : i - 1]

    result: dict[str, dict] = {}
    for m in PROMPT_BLOCK_RE.finditer(block):
        key = m.group(1)
        result[key] = _parse_prompter_block(m.group(2))
    return result


# ── Notion helpers ─────────────────────────────────────────────────────────────

def _make_cell(text: str, code: bool = False) -> list:
    return [{"type": "text", "text": {"content": text}, "annotations": {
        "bold": False, "italic": False, "strikethrough": False,
        "underline": False, "code": code, "color": "default",
    }}]


def repo_commit(repo_dir: str) -> dict:
    """Describe the commit src/config.js was read at.

    A sha only describes the file if the working copy matches it, so `dirty` is
    reported alongside and --regenerate refuses on it unless --allow-dirty.
    """
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", repo_dir, *args],
            capture_output=True, text=True, check=False,
        ).stdout.strip()

    sha = git("rev-parse", "HEAD")
    if not sha:
        raise SystemExit(f"{repo_dir} is not a git checkout — cannot stamp a commit.")
    return {
        "sha": sha,
        "short": sha[:8],
        "date": git("log", "-1", "--format=%cs", "--", "src/config.js"),
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(git("status", "--porcelain", "--", "src/config.js")),
    }


def locate_inventory(notion) -> dict:
    """Resolve the inventory section's blocks by walking the page from its heading.

    Returns {heading_id, anchor_id, table_id, stamp_id}. `anchor_id` is the block
    immediately before the table — a positioned insert after it reproduces the
    table's exact position. `stamp_id` is the script-owned provenance paragraph if
    one already exists, else None.
    """
    blocks = list(notion.get_block_children(TOOLS_OPENAI_PAGE_ID, recursive=False))

    def text_of(b: dict) -> str:
        payload = b.get(b["type"]) or {}
        return "".join(t.get("plain_text", "") for t in (payload.get("rich_text") or []))

    def heading_level(b: dict) -> int | None:
        return int(b["type"][-1]) if b["type"].startswith("heading_") else None

    start = next(
        (i for i, b in enumerate(blocks)
         if heading_level(b) and text_of(b).strip() == INVENTORY_HEADING),
        None,
    )
    if start is None:
        raise SystemExit(f"could not find the {INVENTORY_HEADING!r} heading on Tools/OpenAI")

    # Stop at the next heading of the same or higher rank — that block ends the
    # section. Scanning to end-of-page instead would silently adopt whichever
    # table comes next (Tools/OpenAI carries several), and --regenerate --apply
    # deletes the table it resolves, so an unbounded search can destroy an
    # unrelated one.
    level = heading_level(blocks[start])
    end = next(
        (i for i in range(start + 1, len(blocks))
         if (hl := heading_level(blocks[i])) and hl <= level),
        len(blocks),
    )

    table_i = next((i for i in range(start + 1, end) if blocks[i]["type"] == "table"), None)
    if table_i is None:
        raise SystemExit(
            f"no table between {INVENTORY_HEADING!r} and the next heading — the section's "
            f"table is missing or was moved; fix the page before running this."
        )

    after = blocks[table_i + 1] if table_i + 1 < end else None
    stamp_id = None
    if after and after["type"] == "paragraph" and text_of(after).startswith(STAMP_PREFIX):
        stamp_id = after["id"]

    return {
        "heading_id": blocks[start]["id"],
        "anchor_id": blocks[table_i - 1]["id"],
        "table_id": blocks[table_i]["id"],
        "stamp_id": stamp_id,
    }


def fetch_table_rows(notion, table_id: str = PROMPTER_TABLE_ID) -> tuple[list[str], list[dict]]:
    """Return (headers, data_rows). Each data row has column names as keys plus _block_id."""
    raw = notion.get_block_children(table_id, recursive=False)
    if not raw:
        return [], []
    header_cells = raw[0].get("table_row", {}).get("cells", [])
    headers = ["".join(r.get("plain_text", "") for r in cell) for cell in header_cells]

    data: list[dict] = []
    for row in raw[1:]:
        cells = row.get("table_row", {}).get("cells", [])
        row_dict: dict = {}
        for i, cell in enumerate(cells):
            col = headers[i] if i < len(headers) else f"col{i}"
            row_dict[col] = "".join(r.get("plain_text", "") for r in cell)
        row_dict["_block_id"] = row["id"]
        row_dict["_cells"] = cells  # raw cells for PATCH writes
        data.append(row_dict)
    return headers, data


def resolve_columns(headers: list[str]) -> dict[str, int | None]:
    """Map each logical TABLE_COLS name to its index in the live table headers.

    Prompter/Model/Role match by exact title; the trigger column matches by
    prefix, so a rename such as "Trigger" → "Trigger / gating" still resolves.
    Returns {logical_name: index-or-None}; None means the column is absent.
    """
    resolved: dict[str, int | None] = {c: None for c in TABLE_COLS}
    for i, h in enumerate(headers):
        for col in TABLE_COLS:
            if resolved[col] is not None:
                continue
            matched = h.startswith("Trigger") if col == "Trigger" else h == col
            if matched:
                resolved[col] = i
                break
    return resolved


# ── Regenerate ─────────────────────────────────────────────────────────────────

def regenerate(notion, config: dict, commit: dict, apply: bool) -> None:
    """Rebuild the inventory table from config.js, keeping Role / Trigger prose.

    Notion refuses to change table_width on an existing table, so widening the
    table means creating a replacement and deleting the original.
    """
    loc = locate_inventory(notion)
    old_headers, old_rows = fetch_table_rows(notion, loc["table_id"])
    old_idx = resolve_columns(old_headers)

    # Carrying prose forward depends entirely on these four resolving. Unresolved,
    # the failure is silent and total: `kept` keys on an empty Prompter cell so
    # every row reads as new, prose() substitutes an em dash for every Role and
    # Trigger, and the rebuild then deletes the table those cells lived in. Diff
    # mode already aborts --apply on this; the destructive path needs it more.
    unresolved = [c for c in ("Prompter", "Model", "Role", "Trigger") if old_idx[c] is None]
    if unresolved:
        raise SystemExit(
            f"cannot rebuild: column(s) {unresolved} did not resolve against the live "
            f"headers {old_headers}. Rebuilding would replace every Role / Trigger cell "
            f"with a placeholder and delete the table holding the originals. Restore the "
            f"header titles (or update TABLE_COLS / resolve_columns) first."
        )

    def prose(row: dict, col: str) -> list:
        """Carry a hand-written cell over verbatim, annotations and links included."""
        i = old_idx[col]
        cells = row.get("_cells") or []
        if i is None or i >= len(cells) or not cells[i]:
            return _make_cell("—")
        return cells[i]

    kept = {row.get("Prompter", "").strip(): row for row in old_rows if row.get("Prompter", "").strip()}
    by_name = {p["name"]: p for p in config.values() if p.get("name")}

    # Existing order first — it is hand-curated (conversational, then guardrails,
    # memory, crons, onboarding, insights), and alphabetising would destroy it.
    order = [n for n in kept if n in by_name] + [n for n in by_name if n not in kept]
    dropped = [n for n in kept if n not in by_name]
    added = [n for n in by_name if n not in kept]

    rows = [{"object": "block", "type": "table_row",
             "table_row": {"cells": [_make_cell(h) for h in REGEN_HEADERS]}}]
    changes: list[str] = []
    for name in order:
        params = by_name[name]
        old = kept.get(name)
        model = params.get("model", "") or "—"
        effort = params.get("reasoningEffort", "") or "—"
        verbosity = params.get("verbosity", "") or "—"
        tier = params.get("serviceTier", "") or DEFAULT_TIER
        # Every generated column is diffed, not just Model. A table whose
        # Reasoning, Verbosity or Tier has drifted is exactly what motivates a
        # rebuild, so overwriting those cells with no plan line would hide the
        # change the operator is confirming.
        for col, new_val in (("Model", model), ("Reasoning", effort),
                             ("Verbosity", verbosity), ("Tier", tier)):
            if not old or old_idx[col] is None:
                continue
            was = old.get(col, "").strip() or "—"
            if was != new_val:
                changes.append(f"  {name}: {col.lower()} {was} → {new_val}")
        rows.append({"object": "block", "type": "table_row", "table_row": {"cells": [
            _make_cell(name, code=True),
            _make_cell(model),
            _make_cell(effort),
            _make_cell(verbosity),
            _make_cell(tier),
            prose(old, "Role") if old else _make_cell("— (fill in)"),
            prose(old, "Trigger") if old else _make_cell("— (fill in)"),
        ]}})

    # This line lands on the Notion page, where an agent reading Tools/OpenAI will
    # act on it — so it must not name the one-shot. The rebuild deletes and
    # recreates the table; it goes through eng:audit-intelligence-prompts' own
    # plan-then-approve step, never as a single command lifted from a page.
    stamp = (
        f"{STAMP_PREFIX} {commit['short']} ({commit['date']}), "
        f"{len(order)} prompters. Model / Reasoning / Verbosity / Tier are generated — refresh "
        f"them with eng:audit-intelligence-prompts rather than editing them by hand; that skill "
        f"plans the rebuild and asks for approval before touching this table. Role and Trigger "
        f"are hand-written and carried over. Tier is the OpenAI service tier: flex ⇔ the prompter "
        f"is reachable only from a cron job or an un-awaited side effect; standard ⇔ it is awaited "
        f"inside a request handler, so a user is blocked on it."
        + ("  ⚠️ read from a dirty working copy." if commit["dirty"] else "")
    )

    print(f"Rebuild plan — {len(old_headers)} column(s) → {len(REGEN_HEADERS)}, "
          f"{len(old_rows)} row(s) → {len(order)}")
    print(f"  columns: {' | '.join(REGEN_HEADERS)}")
    print(f"  commit:  {commit['short']} on {commit['branch']} (config.js last touched {commit['date']})")
    if added:
        print(f"  + added {len(added)}: {', '.join(added)}")
    if dropped:
        print(f"  - dropped {len(dropped)} (absent from config.js): {', '.join(dropped)}")
        print("    Their Role / Trigger prose is discarded with the row — check before applying.")
    if changes:
        print(f"  ~ corrections ({len(changes)}):")
        for c in changes:
            print(c)

    if not apply:
        print("\nThis is the plan only — nothing has been written. Show it to the operator, "
              "read back\nthe counts, any dropped row, and the commit above, and get an "
              "explicit yes.\nOnly then re-run this command with --apply, which deletes and "
              "recreates the table.")
        return

    # Anchor on the pre-fetched block before the table. Chaining on a previous
    # response's results[-1] silently appends at the end of the page instead —
    # see the positioned-insert note in AINative/CLAUDE.md.
    created = notion._request(
        "PATCH",
        f"/blocks/{TOOLS_OPENAI_PAGE_ID}/children",
        data=json.dumps({
            "after": loc["anchor_id"],
            "children": [{
                "object": "block",
                "type": "table",
                "table": {
                    "table_width": len(REGEN_HEADERS),
                    "has_column_header": True,
                    "has_row_header": False,
                    "children": rows,
                },
            }],
        }),
    )
    new_table = next(b for b in created.get("results", []) if b.get("type") == "table")
    print(f"✓ Created replacement table {new_table['id']} with {len(rows) - 1} row(s).")

    notion._request("DELETE", f"/blocks/{loc['table_id']}")
    print(f"✓ Removed the {len(old_headers)}-column table {loc['table_id']}.")

    stamp_block = {"object": "block", "type": "paragraph",
                   "paragraph": {"rich_text": _make_cell(stamp)}}
    if loc["stamp_id"]:
        notion._request("PATCH", f"/blocks/{loc['stamp_id']}",
                        data=json.dumps({"paragraph": stamp_block["paragraph"]}))
        print("✓ Updated the provenance stamp.")
    else:
        notion._request(
            "PATCH", f"/blocks/{TOOLS_OPENAI_PAGE_ID}/children",
            data=json.dumps({"after": new_table["id"], "children": [stamp_block]}),
        )
        print("✓ Added the provenance stamp.")

    print(f"\nDone: {TOOLS_OPENAI_URL}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="Path to IntelligenceService repo")
    parser.add_argument("--apply", action="store_true", help="Write approved changes to Notion")
    parser.add_argument("--json", dest="json_out", action="store_true", help="Print plan as JSON")
    parser.add_argument("--regenerate", action="store_true",
                        help="Rebuild the whole table from config.js (adds the Reasoning / "
                             "Verbosity / Tier columns and a commit stamp) instead of diffing it")
    parser.add_argument("--allow-dirty", action="store_true",
                        help="With --regenerate, stamp a commit even though src/config.js has "
                             "uncommitted changes (the stamp then says so)")
    args = parser.parse_args()

    if args.json_out and args.apply:
        parser.error("--json and --apply are mutually exclusive: --json only prints the plan.")
    if args.json_out and args.regenerate:
        parser.error("--json and --regenerate are mutually exclusive.")

    # ── 1. Parse config.js ──────────────────────────────────────────────────
    config = parse_config_prompters(args.repo)
    # name (kebab-case) → configKey mapping
    name_to_key = {v.get("name", ""): k for k, v in config.items() if v.get("name")}
    config_names = set(name_to_key)

    # ── 2. Fetch Notion table ───────────────────────────────────────────────
    _dir = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, _dir)
    from notion_rest import notion  # noqa: PLC0415

    if not notion.available:
        print("NOTION_TOKEN not set — cannot reach AI Knowledge.", file=sys.stderr)
        sys.exit(2)

    if args.regenerate:
        commit = repo_commit(args.repo)
        if commit["dirty"] and not args.allow_dirty:
            print(f"ERROR: src/config.js has uncommitted changes in {args.repo}, so commit "
                  f"{commit['short']} does not describe what would be published. Commit them, "
                  f"or pass --allow-dirty to stamp it as read from a dirty tree.", file=sys.stderr)
            sys.exit(2)
        regenerate(notion, config, commit, args.apply)
        return

    table_id = locate_inventory(notion)["table_id"]
    headers, table_rows = fetch_table_rows(notion, table_id)
    # Strip code-decoration from name cells (notion_rest returns plain_text already)
    table_names = {row.get("Prompter", "").strip() for row in table_rows}

    # Resolve logical columns against the live headers (tolerant of the Trigger
    # rename) before doing any writes. Reasoning / Verbosity / Tier are only present
    # once --regenerate has widened the table, so they are not required here.
    col_idx = resolve_columns(headers)
    required = ["Prompter", "Model", "Role", "Trigger"]
    missing_cols = [c for c in required if col_idx[c] is None]
    if missing_cols:
        if args.apply:
            print(f"ERROR: table missing expected column(s): {missing_cols} — aborting apply.", file=sys.stderr)
            sys.exit(2)
        else:
            print(f"WARNING: table missing expected column(s): {missing_cols}", file=sys.stderr)

    model_col_idx = col_idx["Model"] if col_idx["Model"] is not None else 1

    # ── 3. Compute diff ─────────────────────────────────────────────────────
    missing: list[dict] = []    # in config.js but absent from the table
    stale_model: list[dict] = []  # present but model cell differs from config.js
    stale_params: list[dict] = []  # Reasoning / Verbosity / Tier cell differs from config.js
    orphan: list[str] = []      # in table but no longer in config.js

    # Reasoning / Verbosity / Tier only exist once --regenerate has widened the
    # table. Whichever are present are checked here, so they cannot drift the way
    # the Model column silently did before they had columns at all.
    # The value maps to each column's own "key absent from config" rendering:
    # em dash for the tuning knobs, DEFAULT_TIER for the tier.
    PARAM_COLS = {
        "Reasoning": ("reasoningEffort", "—"),
        "Verbosity": ("verbosity", "—"),
        "Tier": ("serviceTier", DEFAULT_TIER),
    }

    for row in table_rows:
        name = row.get("Prompter", "").strip()
        if not name:
            continue
        if name not in config_names:
            orphan.append(name)
        else:
            key = name_to_key[name]
            cfg_model = config[key].get("model", "")
            tbl_model = row.get("Model", "").strip()
            # Flag stale when config has a model and it differs from the table
            # (blank table cell with non-blank config is also stale).
            if cfg_model and cfg_model != tbl_model:
                stale_model.append({
                    "name": name,
                    "table_model": tbl_model,
                    "config_model": cfg_model,
                    "_block_id": row["_block_id"],
                    "_cells": row["_cells"],
                })
            for col, (field, absent) in PARAM_COLS.items():
                if col_idx[col] is None:
                    continue
                # An unset key renders as whatever the regenerator writes for it.
                cfg_val = config[key].get(field, "") or absent
                tbl_val = row.get(col, "").strip() or absent
                if cfg_val != tbl_val:
                    stale_params.append({
                        "name": name, "column": col,
                        "table_value": tbl_val, "config_value": cfg_val,
                    })

    for key, params in config.items():
        name = params.get("name", "")
        if name and name not in table_names:
            missing.append({
                "name": name,
                "model": params.get("model", "—"),
                "project": params.get("project", ""),
                "reasoningEffort": params.get("reasoningEffort", ""),
                "verbosity": params.get("verbosity", ""),
                "serviceTier": params.get("serviceTier", ""),
                "openaiAPI": params.get("openaiAPI", ""),
            })

    # ── 4. Output ────────────────────────────────────────────────────────────
    if args.json_out:
        print(json.dumps({
            "missing": missing,
            "stale_model": [{k: v for k, v in s.items() if not k.startswith("_")} for s in stale_model],
            "stale_params": stale_params,
            "orphan": orphan,
        }, indent=2))
        return

    has_changes = bool(missing or stale_model or stale_params or orphan)
    if not has_changes:
        print("✓ AI Knowledge prompter inventory is in sync with config.js.")
        return

    if missing:
        print(f"\n📋 Missing from table — {len(missing)} prompter(s) in config.js not yet in AI Knowledge:")
        print(f"  {'Prompter':<45} {'Model':<20} Project / effort")
        print("  " + "─" * 80)
        for p in missing:
            hint = p["project"]
            if p["reasoningEffort"]:
                hint += f" / reasoningEffort:{p['reasoningEffort']}"
            print(f"  {p['name']:<45} {p['model']:<20} {hint}")
        print()
        print("  Role and Trigger columns will be added as '— (fill in)' placeholders.")
        print("  Fill them in at: " + TOOLS_OPENAI_URL)

    if stale_model:
        print(f"\n⚠️  Stale model — {len(stale_model)} row(s) where table disagrees with config.js:")
        for s in stale_model:
            tbl = s["table_model"] or "(blank)"
            print(f"  {s['name']}: table={tbl!r} → config={s['config_model']!r}")

    if stale_params:
        print(f"\n⚠️  Stale params — {len(stale_params)} cell(s) where table disagrees with config.js:")
        for s in stale_params:
            print(f"  {s['name']} [{s['column']}]: table={s['table_value']!r} → config={s['config_value']!r}")
        print("  --apply does not touch these. Fix them with a rebuild, which is a two-step,")
        print("  operator-approved path — it deletes and recreates the table:")
        print("    1. python3 sync_ai_knowledge.py --repo <repo> --regenerate    # plan")
        print("    2. show the plan, get an explicit yes, then re-run with --apply")

    if orphan:
        print(f"\n🗑️  Orphan rows — {len(orphan)} row(s) in the table but absent from config.js:")
        for name in orphan:
            print(f"  {name}")
        print("  These may be deprecated prompters kept for historical reference.")
        print("  Remove them manually if they are no longer needed.")

    # What --apply can actually write. stale_params and orphan are reported only,
    # so a run carrying nothing else has no work for --apply and must not claim it
    # did any — a "Done" on a run that wrote nothing reads exactly like a success.
    appliable = bool(missing or stale_model)

    if not args.apply:
        if appliable:
            print("\nRun with --apply to write missing rows and model updates to Notion.")
        else:
            print("\n--apply has nothing to write here — the findings above are report-only.")
        return

    if not appliable:
        print("\nNothing written: --apply covers missing rows and stale models, and this run "
              "found neither.\nThe findings above are fixed by a rebuild or by hand — see their "
              "remediation lines.")
        return

    # ── 5. Apply ─────────────────────────────────────────────────────────────
    PLACEHOLDER = "— (fill in)"

    if missing:
        new_rows = []
        for p in missing:
            # Fields that have no column of their own get parked in the Trigger
            # placeholder so the value is at least recorded somewhere. Once
            # --regenerate has added the Reasoning column, effort has a home and
            # is dropped from the note rather than duplicated into prose.
            parts = []
            if p["project"]:
                parts.append(f"project: {p['project']}")
            if p["reasoningEffort"] and col_idx["Reasoning"] is None:
                parts.append(f"reasoningEffort: {p['reasoningEffort']}")
            if p["verbosity"] and col_idx["Verbosity"] is None:
                parts.append(f"verbosity: {p['verbosity']}")
            if col_idx["Tier"] is None:
                # Unlike the tuning knobs, an absent key still carries meaning
                # here (it means the default tier), so this is noted even when
                # config sets nothing -- see DEFAULT_TIER.
                parts.append(f"serviceTier: {p['serviceTier'] or DEFAULT_TIER}")
            if p["openaiAPI"]:
                parts.append(f"openaiAPI: {p['openaiAPI']}")
            trigger = PLACEHOLDER + (f"  [{'; '.join(parts)}]" if parts else "")
            # Position cells by the live header order (tolerant of the Trigger
            # rename / extra columns), not the hardcoded TABLE_COLS order.
            content_by_col = {
                "Prompter": _make_cell(p["name"], code=True),
                "Model": _make_cell(p["model"]),
                "Reasoning": _make_cell(p["reasoningEffort"] or "—"),
                "Verbosity": _make_cell(p["verbosity"] or "—"),
                "Tier": _make_cell(p["serviceTier"] or DEFAULT_TIER),
                "Role": _make_cell(PLACEHOLDER),
                "Trigger": _make_cell(trigger),
            }
            # Every column that resolves against the live table must have cell
            # content defined above. A gap is only reachable with a missing row
            # AND a table already carrying that column, so neither an in-sync
            # nor a pre-regenerate table can surface it — fail up front and name
            # it rather than leaving it to land mid-append.
            uncovered = [c for c, i in col_idx.items()
                         if i is not None and c not in content_by_col]
            if uncovered:
                sys.exit(f"ERROR: no cell content defined for resolved column(s) "
                         f"{uncovered} — add them to content_by_col in apply_missing "
                         f"before adding them to TABLE_COLS.")
            cells = [_make_cell("") for _ in headers]
            for col, idx in col_idx.items():
                if idx is not None:
                    cells[idx] = content_by_col[col]
            new_rows.append({
                "object": "block",
                "type": "table_row",
                "table_row": {"cells": cells},
            })
        # Notion caps append_block_children at 100 children per request; chunk.
        added = 0
        for i in range(0, len(new_rows), 100):
            result = notion.append_block_children(table_id, new_rows[i : i + 100])
            added += len(result.get("results", []))
        print(f"✓ Added {added} new row(s).")

    if stale_model:
        for s in stale_model:
            cells = list(s["_cells"])  # shallow copy of raw cell list
            if len(cells) <= model_col_idx:
                print(f"⚠️  Skipped {s['name']}: row has {len(cells)} cell(s), "
                      f"no Model column at index {model_col_idx} — patch it manually.",
                      file=sys.stderr)
                continue
            cells[model_col_idx] = _make_cell(s["config_model"])
            notion._request(
                "PATCH",
                f"/blocks/{s['_block_id']}",
                data=json.dumps({"table_row": {"cells": cells}}),
            )
            print(f"✓ Updated model for {s['name']}: {s['table_model'] or '(blank)'} → {s['config_model']}")

    print(f"\nDone. Fill in Role / Trigger placeholders at:\n{TOOLS_OPENAI_URL}")


if __name__ == "__main__":
    main()
