You are evaluating the output of a memory extraction prompt.

Your job is to judge whether the model selected the right memory-worthy candidates from the recent interaction window.

Evaluate only the recent analysis window as the extraction source. Earlier context or existing memories may be used only to judge novelty, update value, or redundancy, not as an additional extraction pool.

A good output should capture user information that is likely to improve future attunement, continuity, personalization, or retrieval quality.

Reward outputs that identify:
- durable facts or life context
- active, evolving, or recurring life threads
- meaningful relationship or support-relevant context
- user-specific communication or support preferences
- new, updated, or meaningfully clarified information
- information grounded in what the user actually disclosed

Penalize outputs that:
- miss major memory-worthy signals from the recent window
- incorrectly return no memories when qualifying candidates exist
- store low-value conversational residue
- store fleeting or one-off details with little future value
- store emotionally vivid details that are not future-useful
- store weak inferences, tone-based guesses, or assistant-shaped interpretations
- extract from outside the recent analysis window

Do not penalize the output for omitting details that are temporary, vague, redundant, already fully represented in existing memory, or unlikely to improve future personalization.

Return JSON only:

{
  "score": 1-5,
  "rationale": "One concise sentence explaining the score.",
  "failure_tags": []
}

Scoring guide:
5 = Captures all or nearly all important memory-worthy candidates and excludes low-value material.
4 = Captures the main candidates with only minor omissions or minor low-value inclusion.
3 = Mixed; captures some useful candidates but misses or includes several important/low-value items.
2 = Poor; misses many important candidates or stores many non-memory-worthy details.
1 = Fails the task; no useful candidate selection or mostly wrong/irrelevant extraction.

Allowed failure_tags:
- "major_omission"
- "minor_omission"
- "over_inclusion"
- "low_value_detail"
- "weak_inference"
- "assistant_shaped_interpretation"
- "window_leakage"
- "incorrect_noop"
- "none"
