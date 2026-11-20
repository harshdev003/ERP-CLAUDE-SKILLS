# Classification — trust the flag, pick the category

Decision logic for Step 4 of the skill. The job here is **not** to re-derive whether the situation is a risk — the Memory Ingestion prompt already made that call when it set `seriousHarmFlagSet`. The job is to (a) confirm the flag isn't an obvious false positive, and (b) map it to one resource category.

## The flag already decided active-vs-past — trust it

The upstream `memory-ingest-prompter` sets the serious-harm flag **only** for content indicating *active danger, ongoing risk, or an unresolved safety concern*. It explicitly does **not** flag past crisis history — past abuse, past self-harm, a past suicide attempt, past hospitalization, a recovered-from event — unless the input shows the risk is currently active or unresolved. That past-vs-active discrimination is done. This skill does not repeat it, does not dive into the paste to second-guess it, and does not infer risk the prompt didn't flag.

The flag categories (verbatim from the Memory Ingestion prompt) are the skill's taxonomy:

| # | Serious-harm category | High-priority page? |
|---|---|---|
| 1 | Suicide or serious self-harm | **Yes** (violence to self) |
| 2 | Risk of harming someone else | **Yes** (violence to others) |
| 3 | Abuse / domestic violence / coercive control / stalking / trafficking / sexual violence | Only if imminent physical violence to a person |
| 4 | Child, elder, or dependent-person abuse or neglect | Only if imminent physical danger to the person |
| 5 | Medical emergency / overdose / poisoning / dangerous intoxication or withdrawal | No |
| 6 | Psychosis, mania, or severe disorientation with a safety concern | No |
| 7 | Immediate basic-safety risk (stranded, trapped, no shelter / food / water / medication) | No |

High-priority = **active imminent threat of violence to self or others** (including the user as the imminent victim). See SKILL.md Step 6b. Categories 5–7 get an email but do not page — a passive upload never carries a live medical emergency.

The Guardrails Handler prompt (its priority order 8.1 > 8.2 > … and its acute-8.1 / passive-8.2 / complex-8.7 distinctions) is a **secondary reference** for tie-breaking priority. It is not the trigger list — most of its paths (diagnosis requests, wellness advice, misinformation, boundary violations, neutral input) are real-time conversation refusals that can't apply to a passive upload.

## Outcome — binary

The flag is set on every alert (that's what tripped it), so the default outcome is **active risk → draft the email** (SKILL.md Step 5). There are only two reasons to fall to the **short-post path** (a short reply, no draft):

### A. Clear false positive — the paste is plainly historical

Downgrade to "no active risk" **only** when the pasted content unmistakably matches the Memory Ingestion prompt's own "do not flag" patterns — the event is described as past, over, recovered-from, or background context, with no current-tense danger. Mirror the prompt's examples:

- Past suicide attempt "several years ago," no current intent → not active.
- History of self-harm, no current urges → not active.
- Survived an abusive relationship "that has ended" → not active.
- A past medical emergency with no current emergency → not active.
- Empty / template-only paste (the user pasted just "Create a summary of everything you know about me…") — the flag fired on retrieved prior memory, and there is no current uploaded content to act on → not active.

This is a conservative false-positive catch, not a re-classification. If the paste shows the risk as current, unresolved, or ongoing — trust the flag and draft. Do **not** weigh past against present, project future risk, or reason about what "might" escalate. If it isn't plainly historical, it's active.

### B. Resources already in place

When the paste states the user is **already in active professional care for the matter** — e.g. currently seeing a therapist for the mental-health concern, in a treatment program for the substance concern, or working with a lawyer on the legal/custody matter — the email is redundant (its whole point is to offer resources to someone who has none). Fall to the short-post path.

**Carve-out:** this suppression does **not** apply to **imminent violence to self or others** (categories 1, 2, and the imminent-physical-danger slice of 3 and 4). Those always draft the email and page, regardless of existing resources — a person with a therapist can still be in acute danger tonight.

## Minors

If the paste is written in the first person by someone who appears to be a minor (a self-identified age under 18, first-person school/grade context, "my mom won't let me"), classify the risk exactly as above and **draft the email as normal** — every draft is human-reviewed and CS sends, so nothing reaches the user without a person deciding the channel is safe. Attach a one-line reviewer flag (SKILL.md Step 6) so CS can confirm a safe channel and consider any reporting obligation before sending. A minor referenced in the third person (an adult worried about their child) is a caregiver case — same: draft + flag.

## Sanity-check cases (de-identified, from the May 2026 review)

Running the skill on these should produce these outcomes:

| Pattern | Outcome |
|---|---|
| Sexual abuse as a teen; perpetrator convicted years ago; user now an advocate | not active (past) |
| DV + housing arrangement already exited; reflective autobiography | not active (past) |
| Trauma-bonded relationship the user has ended | not active (past) |
| Worry about a family member's past medical event; user investigating, not in danger | not active (past) |
| Past stalking-type experiences explicitly framed as past | not active (past) |
| DV + eviction in days, still with the partner (US) | **active — category 3**; page if imminent physical danger |
| Active custody case, felony-abuse ex with access to the child (US) | **active — category 4**; page if imminent danger to the child |
| Empty / template-only paste; evidence string mentions "ongoing relationship" | not active (no current uploaded content — false positive) |
| Evidence cites "physical violence in marriage" but the paste doesn't corroborate it and reads as past | not active (paste plainly historical) |

If running the skill on real traces produces different outcomes, update the rubric — not the cases.
