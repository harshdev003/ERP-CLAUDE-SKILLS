"""Run a GPT scorer over the run's transcript and persist the grade.

A single Chat Completions call against a scorer template under
references/scorers/. The orchestrator (Claude) calls this after the model
finishes the run and before write_report.py — the report renders every
scorer that has been recorded against state.critiques.

Scorers are selected with --scorer <slug>. The registry today:

  aurie-conversation       (default) 4-axis rubric for Aurie conversation runs
  candidate-selection      did the memory prompt pick the right candidates?
  granularity-compression  right unit of personalization per memory?
  object-quality           memory objects well-formed and retrieval-friendly?
  redundancy               internal overlap among proposed memories
  structured-plan-match    is the structured output a DEFENSIBLE result for
                           a structured-output prompt (memory-sorting plans,
                           classifiers, intent routers)? Judgment-graded
                           against the prompt's own written rules; the dataset
                           `expected` is one reference example, NOT exact-match
                           ground truth.

The aurie-conversation scorer returns a 4-axis JSON object
(`in_character`, `tool_usage`, `coherence`, `brand_voice`, `summary`).
The memory-extraction and structured-plan-match scorers return a single-score
object (`score`, `rationale`, `failure_tags`).

State storage:
  state.critiques[<slug>] — the full result for every scorer run.
  state.critique          — back-compat: the most-recent scorer's result
                            (so write_comparison_report.py keeps working).

Usage:

    python3 self_critique.py --state-file path/to/state.json \\
        [--scorer aurie-conversation] [--model gpt-5.2]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

# Make sibling modules importable when invoked as a script.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from openai import OpenAI, OpenAIError

import service_tier


SCORERS = {
    "aurie-conversation": {
        "template": "aurie-conversation.md",
        "shape": "four-axis",
    },
    "candidate-selection": {
        "template": "candidate-selection.md",
        "shape": "single-score",
    },
    "granularity-compression": {
        "template": "granularity-compression.md",
        "shape": "single-score",
    },
    "object-quality": {
        "template": "object-quality.md",
        "shape": "single-score",
    },
    "redundancy": {
        "template": "redundancy.md",
        "shape": "single-score",
    },
    "structured-plan-match": {
        "template": "structured-plan-match.md",
        "shape": "single-score",
    },
}


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Self-critique the QA run with a GPT scorer pass.")
    p.add_argument("--state-file", required=True)
    p.add_argument(
        "--scorer",
        default="aurie-conversation",
        choices=sorted(SCORERS.keys()),
        help="Scorer slug. Defaults to aurie-conversation (legacy 4-axis rubric).",
    )
    p.add_argument(
        "--model",
        default="gpt-5.2",
        help="OpenAI model for the scoring pass. Default gpt-5.2.",
    )
    service_tier.add_argument(p)
    return p.parse_args()


def _client() -> OpenAI:
    key = os.environ.get("OPENAI_EVALUATIONS_API_KEY")
    if not key:
        raise RuntimeError(
            "OPENAI_EVALUATIONS_API_KEY is not set. Add it to ~/.claude/settings.json `env` "
            "and restart Claude Code; inside a Cowork sandbox, paste-export it once per session."
        )
    return OpenAI(api_key=key)


def _scorer_template_path(slug: str) -> Path:
    here = Path(__file__).resolve().parent.parent
    return here / "references" / "scorers" / SCORERS[slug]["template"]


def _build_transcript_summary(state: dict[str, Any], scorer: str = "") -> str:
    """A compact, model-readable rendering of the run for the scoring pass.

    `structured-plan-match` grades against the prompt's own written rules, so it
    needs the full (untruncated) system prompt and treats the dataset `expected`
    as one reference example rather than ground truth. Other scorers keep the
    compact rendering (system prompt truncated; `expected` omitted/ignored).
    """
    is_plan_match = scorer == "structured-plan-match"
    chunks: list[str] = []
    prompt = state.get("prompt") or {}
    chunks.append(f"# Run for prompt: {prompt.get('name')} ({prompt.get('slug')})\n")
    chunks.append(f"Mode: {state.get('mode')}\n")
    if state.get("scenario"):
        chunks.append(f"Scenario: {state['scenario']}\n")

    chunks.append("\n## Rendered messages (system + seeded turns)\n")
    for m in state.get("messages") or []:
        role = m.get("role")
        if role == "tool":
            # Tool responses are large; summarize.
            content = m.get("content", "")
            preview = content if len(content) < 500 else content[:500] + "..."
            chunks.append(f"- tool({m.get('name')}): {preview}")
            continue
        content = m.get("content") or ""
        # structured-plan-match grades against the prompt's own rules, so it must
        # see the full system prompt. Other scorers keep the 800-char preview.
        if (
            isinstance(content, str)
            and len(content) > 800
            and role == "system"
            and not is_plan_match
        ):
            content = content[:800] + f"\n... [system prompt truncated; total {len(content)} chars]"
        chunks.append(f"- **{role}**: {content}")

    # Surface the dataset row's `expected` output (when present) as ONE reference
    # example for structured-plan-match — NOT ground truth. The scorer grades
    # whether the output is defensible per the prompt's rules, using `expected`
    # only to understand the shape and a known-good answer. Conversation scorers
    # ignore this section.
    dataset_row = state.get("dataset_row") or {}
    expected = dataset_row.get("expected")
    if expected is not None:
        chunks.append("\n## Reference output (state.dataset_row.expected — one valid answer, NOT exact-match ground truth)\n")
        if isinstance(expected, (dict, list)):
            chunks.append("```json")
            chunks.append(json.dumps(expected, indent=2, ensure_ascii=False))
            chunks.append("```")
        else:
            chunks.append(str(expected))

    chunks.append("\n## Turn timeline\n")
    for i, turn in enumerate(state.get("turns") or [], start=1):
        kind = turn.get("kind") or "assistant_turn"
        if kind == "user_turn":
            chunks.append(f"- Turn {i} (simulated user): {turn.get('user_text', '')}")
            continue
        text = turn.get("assistant_text") or ""
        chunks.append(f"- Turn {i} (assistant) finish={turn.get('finish_reason')}: {text}")
        for tc in turn.get("tool_calls") or []:
            chunks.append(f"    · tool_call {tc.get('name')}({json.dumps(tc.get('arguments') or {})})")
        for resp in turn.get("tool_responses") or []:
            r = resp.get("response")
            preview = r if isinstance(r, str) else json.dumps(r, default=str)
            if len(preview) > 400:
                preview = preview[:400] + "..."
            chunks.append(f"    · tool_response {resp.get('name')} (source={resp.get('source')}): {preview}")

    return "\n".join(chunks)


def _stub(shape: str, reason: str, raw: str = "") -> dict[str, Any]:
    """Return a shape-appropriate placeholder when the scoring call fails."""
    body = f"{reason}: {raw[:500]}" if raw else reason
    if shape == "four-axis":
        return {
            "in_character": {"score": 0, "evidence": reason},
            "tool_usage": {"score": 0, "evidence": reason},
            "coherence": {"score": 0, "evidence": reason},
            "brand_voice": {"score": 0, "evidence": reason},
            "summary": body,
        }
    return {"score": 0, "rationale": body, "failure_tags": []}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # model rationales may contain — / emoji; avoid cp1252 on Windows
    args = _parse_args()
    state_path = Path(args.state_file)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    shape = SCORERS[args.scorer]["shape"]

    try:
        client = _client()
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    template_path = _scorer_template_path(args.scorer)
    try:
        template = template_path.read_text(encoding="utf-8")
    except OSError as e:
        print(f"error: could not read scorer template at {template_path}: {e}", file=sys.stderr)
        return 2

    summary = _build_transcript_summary(state, args.scorer)

    # Scoring is a batch pass with nobody waiting on it — flex unless the
    # caller says otherwise. See service_tier.py.
    try:
        tier = service_tier.wire_value(
            args.service_tier or state.get("service_tier"),
            policy=args.on_tier_unavailable,
        )
    except service_tier.ServiceTierError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    def _score(tier: str):
        return client.chat.completions.create(
            model=args.model,
            messages=[
                {"role": "system", "content": template},
                {"role": "user", "content": summary},
            ],
            response_format={"type": "json_object"},
            service_tier=tier,
        )

    try:
        try:
            response = _score(tier)
        except OpenAIError as e:
            reason = service_tier.flex_retry_reason(tier, e)
            if reason is None:
                raise
            # Without this the refusal becomes a stubbed grade that reads like
            # the scorer ran and had nothing to say.
            print(
                f"warning: {reason}; retrying the scoring pass on "
                f"{service_tier.FLEX_FALLBACK_TIER} (standard rate).",
                file=sys.stderr,
            )
            response = _score(service_tier.FLEX_FALLBACK_TIER)
        raw = response.choices[0].message.content or "{}"
        try:
            grade = json.loads(raw)
        except json.JSONDecodeError:
            grade = _stub(shape, "parse error", raw)
    except OpenAIError as e:
        print(f"error: scoring OpenAI call failed: {e}", file=sys.stderr)
        grade = _stub(shape, "api error", str(e))

    critiques = state.get("critiques")
    if not isinstance(critiques, dict):
        critiques = {}
    critiques[args.scorer] = grade
    state["critiques"] = critiques
    state["critique"] = grade  # back-compat: most-recent scorer
    state_path.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    sys.stdout.write(json.dumps(grade, indent=2))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
