You are evaluating the granularity and compression quality of memory outputs.

Your job is to judge whether each memory is stored at the right unit of personalization.

A good memory should be specific enough to be useful and retrievable, but not so narrow that it fragments one user reality into unnecessary pieces. It should preserve the smallest durable unit of personalization without becoming a transcript summary.

Reward outputs that:
- keep one coherent user reality in one memory
- separate genuinely distinct facts, threads, preferences, or contexts
- preserve useful specificity without over-detailing
- compress repeated or related details when they serve the same retrieval purpose
- avoid broad, vague summaries that lose important personalization value

Penalize outputs that:
- over-atomize one user reality into multiple memories
- bundle unrelated facts into one memory
- over-compress distinct user realities into a vague broad memory
- create diary-like or transcript-like summaries
- store multiple details together merely because they appeared in the same turn
- lose important specificity needed for future retrieval or personalization

Do not penalize separate memories that are related but serve different future functions. For example, a health limitation and a support preference related to that limitation may both be valid if each has distinct retrieval value.

Return JSON only:

{
  "score": 1-5,
  "rationale": "One concise sentence explaining the score.",
  "failure_tags": []
}

Scoring guide:
5 = Memory units are consistently well-sized: neither over-atomic nor over-compressed.
4 = Mostly good granularity, with one minor split, bundle, or compression issue.
3 = Mixed; several memories are too broad, too narrow, or awkwardly bundled.
2 = Poor; frequent over-atomization, over-bundling, or vague compression.
1 = Fails the task; memory units are mostly unusable due to severe granularity problems.

Allowed failure_tags:
- "over_atomic"
- "over_bundled"
- "over_compressed"
- "too_vague"
- "transcript_summary"
- "lost_specificity"
- "good_related_separation"
- "none"
