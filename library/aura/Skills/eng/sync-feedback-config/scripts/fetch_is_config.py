#!/usr/bin/env python3
"""Fetch Intelligence Service feedback configs via gh CLI and diff against the AI Knowledge Notion page.

Reads AuraHealth/IntelligenceService/config/dialogue-feedback.json and
journal-feedback.json via `gh api`, then compares against the current content
of Tools/Aura API/Dialogue Feedback Labels in Notion (via NOTION_TOKEN +
notion_rest.py). Outputs a structured JSON diff to stdout.

Exit codes:
  0  — success (changed or unchanged)
  1  — gh CLI error (repo unreachable, not authenticated)
  2  — Notion error (token missing is non-fatal; page comparison is skipped)

Stdlib only (no third-party deps beyond requests inside notion_rest.py).
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Notion page ID for Tools/Aura API/Dialogue Feedback Labels
NOTION_PAGE_ID = "38975e23-af20-816e-9662-e038875662e8"

IS_REPO = "AuraHealth/IntelligenceService"
DIALOGUE_PATH = "config/dialogue-feedback.json"
JOURNAL_PATH = "config/journal-feedback.json"

# Baseline snapshot (2026-06-24, updated for IS PR #298) used when Notion is unreachable.
# Update this when a sync is applied so drift detection stays accurate.
# IS PR #298 removed recommendation_not_helpful, invalid_intent, notification_not_good.
BASELINE: dict[str, dict] = {
    "dialogue": {
        "positive": [
            {"key": "helpful_response", "label": "Helpful response"},
            {"key": "accurate_information", "label": "Accurate information"},
            {"key": "good_explanation", "label": "Good explanation"},
            {"key": "solved_problem", "label": "Solved my problem"},
            {"key": "clear_concise", "label": "Clear and concise"},
        ],
        "negative": [
            {"key": "harmful_unsafe", "label": "This is harmful/unsafe"},
            {"key": "not_true", "label": "This isn't true"},
            {"key": "not_helpful", "label": "This isn't helpful"},
            {"key": "voice_issue", "label": "Voice issue"},
        ],
    },
    "journal": {
        "positive": [
            {"key": "helpful_insight", "label": "Helpful insight"},
            {"key": "accurate_information", "label": "Accurate information"},
            {"key": "clear_concise", "label": "Clear and concise"},
        ],
        "negative": [
            {"key": "insight_not_helpful", "label": "Insight not helpful"},
            {"key": "harmful_unsafe", "label": "This is harmful/unsafe"},
            {"key": "not_true", "label": "This isn't true"},
            {"key": "not_helpful", "label": "This isn't helpful"},
        ],
    },
}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(description="Diff Intelligence Service feedback configs against AI Knowledge.")
    ap.add_argument(
        "--mode",
        choices=["interactive", "diff"],
        default="interactive",
        help="interactive: full plan+confirm flow; diff: JSON output only (for harvest-knowledge).",
    )
    args = ap.parse_args()

    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

    # --- Step 1: fetch Intelligence Service configs via gh ---
    dialogue_raw, dialogue_sha = _gh_read_json(IS_REPO, DIALOGUE_PATH)
    journal_raw, journal_sha = _gh_read_json(IS_REPO, JOURNAL_PATH)

    is_dialogue = _parse_feedback_json(dialogue_raw)
    is_journal = _parse_feedback_json(journal_raw)

    # --- Step 2: read current Notion page (best-effort) ---
    notion_state, notion_warning = _read_notion_state()

    # Fall back to hardcoded baseline when Notion is unreachable.
    baseline = notion_state or BASELINE
    if notion_warning:
        print(f"WARNING: {notion_warning}", file=sys.stderr)

    # --- Step 3: diff ---
    diff = {
        "dialogue_positive": _diff_section(
            baseline["dialogue"]["positive"], is_dialogue["positive"]
        ),
        "dialogue_negative": _diff_section(
            baseline["dialogue"]["negative"], is_dialogue["negative"]
        ),
        "journal_positive": _diff_section(
            baseline["journal"]["positive"], is_journal["positive"]
        ),
        "journal_negative": _diff_section(
            baseline["journal"]["negative"], is_journal["negative"]
        ),
    }
    changed = any(
        diff[k]["added"] or diff[k]["removed"] or diff[k]["relabelled"]
        for k in diff
    )

    result = {
        "as_of": today,
        "changed": changed,
        "notion_page_id": NOTION_PAGE_ID,
        "notion_source": "live" if notion_state else "baseline",
        "configs": {
            "dialogue": {
                "path": DIALOGUE_PATH,
                "sha": dialogue_sha,
                "positive": is_dialogue["positive"],
                "negative": is_dialogue["negative"],
            },
            "journal": {
                "path": JOURNAL_PATH,
                "sha": journal_sha,
                "positive": is_journal["positive"],
                "negative": is_journal["negative"],
            },
        },
        "diff": diff,
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))


def _gh_read_json(repo: str, path: str) -> tuple[list, str]:
    """Fetch a JSON file from GitHub via gh CLI. Returns (parsed_data, sha)."""
    try:
        out = subprocess.check_output(
            ["gh", "api", f"repos/{repo}/contents/{path}"],
            stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError as exc:
        msg = exc.stderr.decode("utf-8", errors="replace").strip()
        raise SystemExit(
            f"gh CLI error reading {repo}/{path}: {msg}\n"
            "Make sure `gh auth login` is complete and you have access to the repo."
        ) from exc

    payload = json.loads(out)
    content = base64.b64decode(payload["content"]).decode("utf-8")
    return json.loads(content), payload.get("sha", "")


def _parse_feedback_json(raw: list) -> dict[str, list]:
    """Normalise [{"rating": 1, "options": [...]}, ...] into {"positive": [...], "negative": [...]}."""
    result: dict[str, list] = {"positive": [], "negative": []}
    for block in raw:
        rating = block.get("rating")
        key = "positive" if rating == 1 else "negative" if rating == -1 else None
        if key:
            result[key] = [{"key": o["key"], "label": o["label"]} for o in block.get("options", [])]
    return result


def _read_notion_state() -> tuple[dict | None, str]:
    """Read the current label state from the Notion page. Returns (state_dict, warning_str)."""
    token = os.environ.get("NOTION_TOKEN")
    if not token:
        return None, "NOTION_TOKEN not set — falling back to hardcoded baseline for diff."

    try:
        from notion_rest import notion  # noqa: PLC0415

        if not notion.available:
            return None, "notion_rest: token present but client unavailable — using baseline."

        blocks = notion.get_block_children(NOTION_PAGE_ID.replace("-", ""), recursive=True)

        # Parse bullet lists out of the page by section heading context.
        # Sections in the page: "Dialogue (Quick Processing + Conversation)" and "Journal"
        state: dict[str, dict] = {
            "dialogue": {"positive": [], "negative": []},
            "journal": {"positive": [], "negative": []},
        }
        current_section: str | None = None  # "dialogue" or "journal"
        current_polarity: str | None = None  # "positive" or "negative"

        for block in blocks:
            btype = block.get("type", "")
            texts = block.get(btype, {}).get("rich_text", []) if btype not in ("child_page",) else []
            text = "".join(t.get("plain_text", "") for t in texts).strip()

            if "heading" in btype:
                lower = text.lower()
                if "dialogue" in lower or "quick processing" in lower:
                    current_section = "dialogue"
                    current_polarity = None
                elif "journal" in lower and "feedback" not in lower:
                    current_section = "journal"
                    current_polarity = None
                # Polarity detection from sub-headings or bullet lead text
                elif "positive" in lower or "rating: 1" in lower or "thumbs-up" in lower or "thumbs up" in lower:
                    current_polarity = "positive"
                elif "negative" in lower or "rating: -1" in lower or "thumbs-down" in lower or "thumbs down" in lower:
                    current_polarity = "negative"
                else:
                    # Any other heading ends the label-collecting region. The page
                    # carries non-label sections after the Journal labels —
                    # "Interpretation guide for AI skills", "Voice issue",
                    # "Historical note: removed options", and "Sources". Without
                    # this reset, current_section/current_polarity stay pinned to
                    # journal/negative and every bullet under those sections is
                    # mis-collected as a journal-negative label, producing phantom
                    # "removed" diff entries (and an apply would delete the page's
                    # curated notes).
                    current_section = None
                    current_polarity = None

            elif btype == "bulleted_list_item":
                lower = text.lower()
                # Detect polarity pivots from top-level bullet text
                if "rating: 1" in lower or "thumbs-up" in lower or "thumbs up" in lower or "positive option" in lower:
                    current_polarity = "positive"
                elif "rating: -1" in lower or "thumbs-down" in lower or "thumbs down" in lower or "negative option" in lower:
                    current_polarity = "negative"
                elif current_section and current_polarity and text:
                    # Strip leading whitespace / indent markers
                    label = text.lstrip(" \t•-").strip()
                    if label:
                        state[current_section][current_polarity].append({"label": label})

        # Fall back if any section/polarity yielded zero labels — a partial parse produces
        # misleading diffs (missing sections look like all options were removed).
        empty = [
            f"{sect}/{pol}"
            for sect in state
            for pol, labels in state[sect].items()
            if not labels
        ]
        if empty:
            return None, (
                f"Incomplete Notion parse — no labels found for: {', '.join(empty)}. "
                "Using baseline to avoid a partial diff."
            )

        return state, ""

    except Exception as exc:  # noqa: BLE001
        return None, f"Notion read failed ({exc}) — using baseline."


def _norm_label(s: str) -> str:
    """Normalise typographic quotes for comparison only (output keeps the original).

    The Intelligence Service config stores curly glyphs ("isn't", U+2019) while the
    Notion page renders straight ASCII ("isn't"). Without this, a glyph-only
    difference surfaces as a phantom add + remove on every run.
    """
    return (
        s.replace("’", "'")
        .replace("‘", "'")
        .replace("“", '"')
        .replace("”", '"')
        .strip()
    )


def _diff_section(
    baseline: list[dict], current: list[dict]
) -> dict[str, list]:
    """Diff two label lists. Returns {"added": [...], "removed": [...], "relabelled": [...]}."""
    # Notion-parsed baseline has only "label" (no "key"); Intelligence Service baseline and current have both.
    # Match priority: (1) key-to-key, (2) label-to-label (handles Notion-only baseline).
    # An empty `old_key` ("") is falsy — don't treat it as a key-change signal.
    # Label-based matching is done on normalised text (curly vs straight quotes).
    base_by_key = {o["key"]: o["label"] for o in baseline if "key" in o}
    base_by_label = {_norm_label(o["label"]): o.get("key") or None for o in baseline}
    curr_by_key = {o["key"]: o["label"] for o in current}
    curr_by_label = {_norm_label(o["label"]): o["key"] for o in current}

    added = []
    removed = []
    relabelled = []

    # Forward pass: every Intelligence Service option against the baseline.
    for key, label in curr_by_key.items():
        if key in base_by_key:
            if _norm_label(base_by_key[key]) != _norm_label(label):
                relabelled.append({"key": key, "old": base_by_key[key], "new": label})
        elif _norm_label(label) in base_by_label:
            # Label exists in baseline — either key changed or label-only baseline (Notion parse).
            old_key = base_by_label[_norm_label(label)]
            if old_key and old_key != key:
                relabelled.append({"key": key, "old_key": old_key, "label": label})
            # else: unchanged (key absent in baseline, label matches → idempotent)
        else:
            added.append({"key": key, "label": label})

    # Reverse pass: baseline options absent from current.
    for entry in baseline:
        key = entry.get("key") or None
        label = entry.get("label", "")
        if key and key in curr_by_key:
            continue  # accounted for above
        if _norm_label(label) in curr_by_label:
            continue  # label still present
        removed.append({"key": key, "label": label} if key else {"label": label})

    return {"added": added, "removed": removed, "relabelled": relabelled}


if __name__ == "__main__":
    main()
