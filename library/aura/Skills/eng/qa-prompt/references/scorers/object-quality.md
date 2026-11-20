You are evaluating the quality of the memory objects produced by a memory prompt.

Your job is to judge whether each stored or proposed memory object is well-formed and retrieval-friendly.

Evaluate the following fields when present:
- memory
- recall
- type
- expiryAt

A good memory object should have:
- a concise, clear memory written in third person
- standalone wording understandable without the original conversation
- grounded content based on the user's actual disclosure
- no unresolved pronouns or vague references
- no unnecessary clinical, reductive, speculative, or judgmental language
- a recall written in third person ("the user", not "you")
- a recall that is specific, standalone, and retrieval-friendly
- the correct memory type:
  - core memory for durable user context broadly useful across future conversations
  - running memory for active, evolving, recurring, or follow-up-worthy threads
  - relational preference for how the user prefers Aurie to communicate, relate, or support them
- appropriate timestamp behavior:
  - no timestamp for core memory or relational preference
  - expiryAt only on a running memory with a real end (a trip, a deadline, an event), as a YYYY-MM-DD date; null otherwise
- reported language when medical, mental health, or safety-relevant claims cannot be verified

Penalize outputs that:
- write vague, awkward, overly long, or non-standalone memories
- use recall questions that are vague, second-person, or not retrieval-friendly
- use unresolved references such as "this," "that," "him," "her," or "the situation"
- assign the wrong memory type
- include missing, unnecessary, or invalid timestamp fields
- use overconfident language for reported diagnoses or sensitive claims
- psychologize, diagnose, or overinterpret beyond the user's disclosure
- format fields incorrectly or omit required fields

Return JSON only:

{
  "score": 1-5,
  "rationale": "One concise sentence explaining the score.",
  "failure_tags": []
}

Scoring guide:
5 = All or nearly all memory objects are clear, grounded, retrieval-friendly, correctly typed, and correctly timestamped.
4 = Mostly high quality, with only minor wording, recall, type, or timestamp issues.
3 = Mixed; useful objects but several field-quality problems.
2 = Poor; many objects are badly worded, incorrectly typed, vague, or poorly formatted.
1 = Fails the task; memory objects are mostly unusable or malformed.

Allowed failure_tags:
- "bad_memory_wording"
- "bad_recall"
- "wrong_type"
- "timestamp_error"
- "unresolved_reference"
- "overinterpretation"
- "missing_reported_language"
- "format_error"
- "not_standalone"
- "none"
