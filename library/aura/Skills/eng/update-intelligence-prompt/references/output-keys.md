# Output JSON keys must be camelCase

A prompt's output keys become the API contract mobile reads, and the service surfaces
camelCase everywhere. Braintrust bodies are sometimes authored in snake_case, so check the
prompt's Output Format section before committing the `.txt`.

## What to do

When the authored prompt emits snake_case (`old_line`, `remember_quote`):

1. Rename the keys to camelCase in the `.txt` — **every** occurrence, not just the Output
   Format block. The keys are usually also named in the per-field creation steps, the
   validation checklists, and the worked examples. A partial rename leaves the prompt
   instructing the model in two vocabularies.
2. Update `src/prompters/<name>.schema.json` to match, so the on-disk contract agrees even
   while `jsonOutput` is `false`.
3. Ask prompt engineering to make the same rename in Braintrust, so the registry and the
   runtime agree. That is bookkeeping and does not block the release — this repo's `.txt` is
   what production reads.

## Why not translate in code

Rewriting keys inside the model that persists the response leaves the prompt and the API
contract permanently disagreeing, and gives the next reader two sources of truth for one
shape. It also hides the mismatch from anyone reading only the prompt.

Renaming in the prompt is verifiable in a way a shim is not: run the renamed body against a
real transcript and confirm the model returns the new keys with equivalent content. A rename
is cosmetic to the model, so the content should be materially unchanged — if it isn't, the
rename touched something it shouldn't have.

## Precedent

Every runtime prompter emits camelCase, most because they were authored that way:
`previewMessage` on summary; `expiryAt`, `sourceCandidateIds` on the memory
family. `quickProcessingDone` shipped as `header` / `oldLine` / `newLine` / `rememberQuote`
([#461](https://github.com/AuraHealth/IntelligenceService/pull/461)).
