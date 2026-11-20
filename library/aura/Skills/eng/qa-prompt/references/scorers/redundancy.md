You are evaluating redundancy and overlap within the model's proposed or stored memories.

Your job is to judge whether the output contains multiple memories that capture the same underlying user reality without adding meaningful retrieval or personalization value.

Focus only on redundancy within the output itself. Do not evaluate whether the model correctly discarded, replaced, refreshed, or deleted existing memories unless those actions are included and relevant. This scorer is about whether the output is internally redundant.

Penalize outputs that contain:
- two or more memories with nearly the same meaning
- one broad memory and one narrower memory where the narrower one adds no useful retrieval value
- repeated versions of the same active thread
- multiple recalls that would retrieve the same memory for the same reason
- unnecessary splitting of one coherent fact into overlapping memories
- duplicate preference memories with different wording but the same practical guidance

Do not penalize:
- related but meaningfully distinct memories
- one memory about a user context and another about a support preference connected to that context
- one memory about a durable fact and another about an active thread when each serves a different future function
- separate memories for distinct facts from the same paragraph
- memories that overlap slightly but answer different future retrieval questions

Examples:
Redundant:
- The user has chronic illness that limits energy.
- The user's health condition reduces their daily capacity.

Better as one:
- The user has reported a chronic multisystem illness that severely limits energy and daily capacity.

Related but distinct:
- The user has reported a chronic multisystem illness that severely limits energy and daily capacity.
- The user prefers adaptive structure that respects energy limits.

Return JSON only:

{
  "score": 1-5,
  "rationale": "One concise sentence explaining the score.",
  "failure_tags": []
}

Scoring guide:
5 = No meaningful redundancy; related memories are distinct and useful.
4 = Minor overlap, but little practical harm.
3 = Some redundant or overlapping memories that should likely be merged.
2 = Frequent redundancy that would create memory clutter.
1 = Severe duplication; output repeats the same few user realities in multiple forms.

Allowed failure_tags:
- "duplicate_memory"
- "overlapping_recall"
- "redundant_split"
- "broad_and_narrow_duplicate"
- "repeated_active_thread"
- "duplicate_preference"
- "none"
