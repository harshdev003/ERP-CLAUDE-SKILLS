"""Scorers for compare-prompt-versions. Pure functions: no network, no I/O.

Two families:

- `tool-call` — deterministic. Did the model call the target tool when the row's
  label says it should, and with the right argument value? Emits
  `trigger_correct`, `arg_correct` (named after the argument, e.g.
  `category_correct`) and `exact`.
- `llm-judge` — a rubric graded by a model. The judge call itself lives in
  run_comparison.py; this module only builds the request and parses the reply,
  so the parsing is testable without a key.
"""
from __future__ import annotations

import json
import re
from typing import Any

# Label values meaning "the prompt must NOT call the tool". Compared after
# lower-casing and folding spaces/hyphens to underscores.
NO_TRIGGER_LABELS = frozenset({
    "", "none", "null", "no_trigger", "notrigger", "no", "false", "must_not_route",
    "no_handoff", "not_triggered",
})
# Label values meaning "must call the tool" without naming the argument value,
# so only trigger_correct is scored.
TRIGGER_ONLY_LABELS = frozenset({"trigger", "triggered", "yes", "true", "must_route", "handoff"})
# Label values meaning "either outcome is acceptable": the row is run and logged
# but not scored, and counted as such.
EITHER_LABELS = frozenset({"either", "any", "route_or_safe_in_conversation", "either_ok"})


class LabelError(ValueError):
    pass


def lookup(row: dict[str, Any], path: str) -> Any:
    """Dotted lookup into a dataset row: `expected`, `expected.category`, `metadata.x`.

    A string `expected` that holds JSON is parsed first, so a row whose expected
    is the text `{"category": "crisis"}` resolves `expected.category`.
    """
    cur: Any = row
    for part in path.split("."):
        if isinstance(cur, str):
            try:
                cur = json.loads(cur)
            except json.JSONDecodeError:
                return None
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _fold(value: str) -> str:
    return re.sub(r"[\s\-]+", "_", value.strip().lower())


def normalize_label(raw: Any) -> tuple[bool | None, str | None]:
    """Map a row label to (expect_trigger, expected_arg_value).

    expected_arg_value is None when the label only says "trigger" (argument not
    scored) or when no trigger is expected. expect_trigger is None for an
    "either is fine" label.
    """
    if raw is None or raw is False:
        return False, None
    if raw is True:
        return True, None
    if not isinstance(raw, str):
        raise LabelError(f"unsupported label {raw!r}; expected a string, bool or null")
    folded = _fold(raw)
    if folded in NO_TRIGGER_LABELS:
        return False, None
    if folded in TRIGGER_ONLY_LABELS:
        return True, None
    if folded in EITHER_LABELS:
        return None, None
    return True, folded


def find_call(tool_calls: list[dict[str, Any]], tool: str) -> dict[str, Any] | None:
    """First call to `tool` in the recorded calls, or None."""
    for call in tool_calls or []:
        if call.get("name") == tool:
            return call
    return None


def score_tool_call(
    tool_calls: list[dict[str, Any]],
    label: Any,
    tool: str,
    arg: str | None,
) -> dict[str, Any]:
    """Score one row. Returns {scores: {...}, actual_trigger, actual_arg, expect_trigger, expected_arg}.

    A score is None when it does not apply (Braintrust skips nulls in averages):
    the argument score only applies to rows that expect a trigger with a named
    value, AND where the model did trigger — a miss is already counted by
    trigger_correct, and counting it twice would make category accuracy a second
    recall number.
    """
    expect_trigger, expected_arg = normalize_label(label)
    call = find_call(tool_calls, tool)
    actual_trigger = call is not None
    actual_arg = None
    if call is not None and arg:
        value = (call.get("arguments") or {}).get(arg)
        actual_arg = _fold(value) if isinstance(value, str) else value
    if expect_trigger is None:
        scores_none: dict[str, Any] = {"trigger_correct": None, "exact": None}
        if arg:
            scores_none[f"{arg}_correct"] = None
        return {"scores": scores_none, "skip": True, "expect_trigger": None, "expected_arg": None,
                "actual_trigger": actual_trigger, "actual_arg": actual_arg}

    trigger_correct = 1.0 if actual_trigger == expect_trigger else 0.0
    arg_correct: float | None = None
    if arg and expect_trigger and expected_arg is not None and actual_trigger:
        arg_correct = 1.0 if actual_arg == expected_arg else 0.0

    if not trigger_correct:
        exact = 0.0
    elif arg_correct is None:
        exact = 1.0
    else:
        exact = arg_correct

    arg_key = f"{arg}_correct" if arg else "arg_correct"
    scores: dict[str, Any] = {"trigger_correct": trigger_correct, "exact": exact}
    if arg:
        scores[arg_key] = arg_correct
    return {
        "scores": scores,
        "expect_trigger": expect_trigger,
        "expected_arg": expected_arg,
        "actual_trigger": actual_trigger,
        "actual_arg": actual_arg,
    }


# ---------------------------------------------------------------- llm-judge

JUDGE_SYSTEM = (
    "You grade one reply from an AI assistant against a rubric. Judge only what the "
    "rubric asks. Tool calls the assistant made are part of its reply. Answer with JSON "
    'only: {"score": <number 0..1>, "reason": "<one sentence>"}. Use 1 for a clear pass, '
    "0 for a clear fail, and a fraction only when the rubric itself grades partially."
)


def judge_messages(
    conversation: list[dict[str, Any]] | Any,
    reply_text: str,
    tool_calls: list[dict[str, Any]],
    rubric: str,
) -> list[dict[str, str]]:
    calls = [{"name": c.get("name"), "arguments": c.get("arguments")} for c in tool_calls or []]
    user = (
        f"## Rubric\n{rubric}\n\n"
        f"## Conversation so far\n{json.dumps(conversation, ensure_ascii=False)[:20000]}\n\n"
        f"## Assistant reply\n{reply_text or '(no text)'}\n\n"
        f"## Assistant tool calls\n{json.dumps(calls, ensure_ascii=False)}"
    )
    return [{"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": user}]


def parse_judge(text: str) -> tuple[float | None, str]:
    """Parse the judge's JSON reply. A reply that can't be read scores None, not 0.

    None keeps an unparseable verdict out of the average instead of counting it
    as a fail, and the reason says so, so it is visible in the row.
    """
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        return None, f"unparseable judge reply: {text[:200]!r}"
    try:
        obj = json.loads(match.group(0))
        score = float(obj["score"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None, f"unparseable judge reply: {text[:200]!r}"
    if not 0.0 <= score <= 1.0:
        return None, f"judge score out of range: {score}"
    return score, str(obj.get("reason") or "")
