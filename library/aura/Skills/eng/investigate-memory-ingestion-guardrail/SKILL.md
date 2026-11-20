---
name: investigate-memory-ingestion-guardrail
description: >
  Triage one Aurie memory-ingestion guardrail alert and, on active risk, draft a warm,
  location-aware Customer Support email for human review. Accepts a k8bot guardrail Slack URL,
  or a user_id plus transaction_id. Pulls the memory-ingest completion, parses the guardrail
  outcome, trusts the upstream serious-harm flag rather than re-deriving it, maps it to one of
  seven categories, and for an active risk drafts a CS email with resources picked by country
  and category. Posts the result as a thread reply on the alert. The skill never sends the
  email — a human reviewer approves it first. Trigger on "investigate memory ingestion
  guardrail", "memory guardrail triggered", "what tripped this memory guardrail", "triage this
  guardrail alert", "draft CS email for this guardrail trip", or a pasted guardrail Slack URL.
  Do NOT use for conversation-rating analysis (`data:analyze-user-conversations`), editing the
  guardrail rules (`eng:update-intelligence-prompt`), or general trace debugging
  (`eng:investigate-openai-trace`).

metadata:
  version: "3.7.1"
knowledge:
  reads:
    - Compliance/Privacy   # PII handling — UID prefix only, never echo full user content into Slack/Notion
    - Guide/Index          # baseline routing aid (universal)
    - Tools/OpenAI         # auth + stored chat-completions + Responses API access pattern + project inventory
    - Tools/Braintrust     # BTQL — genuine last resort when OpenAI is unreachable or a transaction predates the ledger
    - Tools/Slack          # alert channel directory — #guardrails-memory-ingestion ID + bot-membership requirement
    - Tools/Aura API       # GET /users/:userId (first name, country, CS email) (the llmCall ledger, transaction → response id, is BigQuery llm.llm_call)
---

# investigate-memory-ingestion-guardrail

> **memory-ingest runs on the OpenAI Responses API** ([ENGBACK-1243](https://linear.app/aura-health/issue/ENGBACK-1243)). The `memory-ingest-prompter` call is a stored **response** (`resp_…`), and OpenAI does **not** let you enumerate stored responses by metadata — so a `GET /v1/chat/completions?metadata[transaction]=…` query never matches it. The skill resolves the transaction through the IntelligenceService per-call LLM ledger (BigQuery `aura-main.llm.llm_call`, semantics in AI Knowledge `Tools/Aura API/LLM Calls`), which records every call across both APIs and is the queryable `transaction → openaiId` map, then reads the stored object directly: `GET /v1/responses/{id}` for the `{memories, seriousHarmFlagSet, seriousHarmEvidence}` envelope and `GET /v1/responses/{id}/input_items` for the pasted summary (the system message where contextInstructions are concatenated). The `fetch_guardrail.py` resolution ladder is: (1) chat-completions metadata query — for any pre-migration / non-migrated transaction; (2) ledger → Responses (or chat) fetch by id — the normal memory-ingest path; (3) Braintrust BTQL — the genuine last resort, reached only when a transaction predates the ledger deploy or OpenAI is unreachable. Each rung distinguishes an authoritative miss (advance) from an outage (reported as one). OpenAI auth is `OPENAI_MEMORY_API_KEY` (works for both `/chat/completions` and `/responses`); the ledger uses `AURA_API_TOKEN`.

> **Auth changed 2026-07-15** ([PROD-3572](https://linear.app/aura-health/issue/PROD-3572)). OpenAI enforces a `model.request` scope on stored-object reads (`GET /v1/chat/completions`, `GET /v1/responses`) that Admin keys (`OPENAI_ADMIN_API_KEY`) cannot hold — Admin keys can never gain this scope, by OpenAI design. This skill authenticates with `OPENAI_MEMORY_API_KEY`, a service-account key scoped to the `Memory [Production]` project. `OPENAI_ADMIN_API_KEY` remains valid for other skills' org-level cost endpoints, but no longer works here.

> **History — migrated off Braintrust 2026-06-05** ([PROD-2463](https://linear.app/aura-health/issue/PROD-2463), umbrella [PROD-2456](https://linear.app/aura-health/issue/PROD-2456)). OpenAI stored objects in the `Memory [Production]` project (`proj_f0PpUSyYnJNifUZj44FrjUhr`) are the source of truth; Braintrust logging runs in parallel and backs rung 3.

Triage skill for a single Aurie memory-ingestion guardrail trip. One Slack alert in, one of two outcomes out:

- **Active risk** — draft a warm, location-aware CS outreach email, post it on the alert thread for human review.
- **No active risk** (past disclosure / false positive) **or resources already in place** — post a short reply, no draft.

The skill never sends the email. A human reviewer approves drafts before Customer Support sends.

## What this skill is, and is not

The memory-ingest guardrail fires on a **passive upload** — a summary the user imported from another LLM, not a live conversation (in-conversation risk is a separate real-time system, the Guardrails Handler). The single question is: **did the user upload something indicating imminent or ongoing harm?** If yes, offer resources via a reviewed CS email. If no, leave them alone.

**The upstream flag already did the hard part.** The `memory-ingest-prompter` sets `seriousHarmFlagSet` **only** for active, ongoing, or unresolved risk — it explicitly does not flag past crisis history (see `classification_rubric.md`). So this skill **trusts the flag** and does not re-derive past-vs-active, dive into the paste to second-guess it, infer risk the prompt didn't flag, or reason about what "might" escalate. Its judgment is narrow: confirm the flag isn't an obvious false positive, map it to a resource category, and respond.

### The paste-use rule (analysis vs composition) — read this once, it governs Steps 3–6

The pasted user summary is **off-limits for risk analysis** — never re-classify from it, and never quote, paraphrase, or echo it into Slack or the email. **Exactly two fields are the exception: the account holder's own first name and their country/location.** You read those two from the summary to compose the CS email whenever no separate profile block supplies them.

- Reading name + country for composition is a **different act** from analysing risk. It is **required**, not optional — sourcing them from the paste is the intended behaviour, never a policy violation.
- A missing or placeholder value is **never** a reason to withhold a draft. The memory-ingest prompt usually templates the name out to the literal `[USER_NAME]` placeholder and current traces carry no separate profile block, so in practice first name → `Hi there,` and country comes from a location line in the summary. Both are normal.
- Nothing else ever leaves the paste — no risk signals, no situational specifics, no third-party details.

Whenever a step below says "the profile block," understand that current traces usually don't have one; the summary is the normal identity source, and the fetch script surfaces the two allowed fields in its **Identity for email (compose-only)** section (Step 3) so you never have to decide whether reading them is allowed. It is.

## Goal

Take an isolated `k8bot` memory-ingestion guardrail alert and produce a same-screen answer to: **is there an active risk, and (if so) what email should CS consider sending.** No clicking through the OpenAI Logs UI; no engineering judgement required to act on the output.

This skill is the Slack-front-door + guardrail-specific synthesis layer on top of [`eng:investigate-openai-trace`](../investigate-openai-trace/SKILL.md). It hands a structured, one-screen view + an actionable draft; the sibling is for free-form span debugging.

## Taxonomy: the seven serious-harm categories

The skill's category axis is the seven serious-harm flag categories from the Memory Ingestion prompt — the thing that actually fires the alert:

1. Suicide or serious self-harm
2. Risk of harming someone else
3. Abuse / DV / coercive control / stalking / trafficking / sexual violence
4. Child, elder, or dependent-person abuse or neglect
5. Medical emergency / overdose / poisoning / dangerous intoxication or withdrawal
6. Psychosis, mania, or severe disorientation with a safety concern
7. Immediate basic-safety risk (stranded, trapped, no shelter / food / water / medication)

The **Guardrails Handler** prompt (its 8-path priority order) is a **secondary reference** only — used to break ties on priority and to distinguish acute crisis (its 8.1) from passive-existential (8.2) and complex-condition (8.7). It is *not* the trigger list: most Handler paths (diagnosis, wellness, misinformation, boundary violations, neutral input) are real-time conversation refusals that can't apply to a passive upload. Do not presume a skewed distribution of categories; apply them neutrally as the flag reports them.

## Inputs

The caller provides one of:

- **Mode A — Slack URL.** A permalink to the `k8bot` guardrail alert, shape `https://<workspace>.slack.com/archives/<channel>/p<ts_no_dot>`. The skill parses `channel_id` + `message_ts` out of the URL, fetches the parent message via `shared:read-slack`, and extracts the Firebase UID + transaction ID from the message body.
- **Mode B — direct pair.** `user_id=<firebase-uid> transaction_id=<uuid>` when the caller already has them (e.g. paged via PagerDuty, or testing).

Mode A is the primary path — that's how guardrail trips surface day-to-day. Mode B is for cases where the Slack message isn't reachable (private channel, deleted, etc).

These alerts land in **`#guardrails-memory-ingestion`** — the canonical channel ID and the requirement that the `aura_ai` bot be a member (needed for `conversations.history` reads and `chat.postMessage` replies) are recorded in `Tools/Slack` in AI Knowledge. Resolve the ID from there rather than hardcoding it here; if the bot isn't a member, `conversations.history` returns `not_in_channel` and a human must `/invite @aura_ai` first.

## Workflow

### Step 0 — Load context

Before doing anything else:

1. Ensure `retrieve-knowledge` has pre-fetched `Compliance/Privacy`, `Tools/OpenAI`, `Tools/Slack`, `Tools/Aura API`, and `Guide/Index`. The privacy rules are load-bearing for Steps 5 and 6 (the Slack reply must redact the UID to its first 8 chars + `…`, must never echo the user's pasted content, and carries only the category + the non-graphic `seriousHarmEvidence` phrase — **plus, on the active-risk draft path only, the account holder's email** from the Aura record, so the reviewer can send without a further lookup; the email body uses the account holder's first name only — from the Aura record, or the profile block / pasted summary when it's blank — and no other user detail). `Tools/OpenAI` carries the stored-completions access pattern + the Memory project ID. `Tools/Slack` carries the `#guardrails-memory-ingestion` channel ID and the `aura_ai` bot-membership requirement. `Tools/Aura API` documents `GET /users/:userId` — the primary source for the account holder's first name (`givenName`), country (`countryCode` + `timezone`), and email.
2. Read the three reference files bundled with this skill — they're the source of truth:
   - `references/classification_rubric.md` — trust-the-flag logic, the seven categories, the false-positive and resources-in-place downgrades
   - `references/email_template.md` — category-matched email assembly + tone/privacy rules + placeholder rules + validation checklist
   - `references/resource_map.md` — country × category resources (hotlines first, ongoing-care directories second), plus the "country not in map" branch

### Step 1 — Resolve input

If the caller pasted a Slack URL, parse it and fetch the message body.

**URL shape.** `https://<workspace>.slack.com/archives/<channel_id>/p<ts_no_dot>` (optionally `?thread_ts=<...>`). Convert:

- `channel_id` = the path segment after `/archives/` (e.g. `C0B1D3JEHLP`).
- `message_ts` = the `p<digits>` slug with a decimal point inserted before the last 6 digits. Example: `p1779144885524169` → `1779144885.524169`.

**Fetch the alert** by **invoking [`shared:read-slack`](../../../shared/skills/read-slack/SKILL.md)** in `message` mode for `channel_id` + `message_ts` — only the parent message is needed. That skill owns the connection (MCP-first, `SLACK_BOT_TOKEN` script fallback).

**Extract from the k8bot body.** The message body looks like:

```
:warning: *Memory ingestion guardrail triggered*
*User:* <firebase-uid>
*Transaction:* <uuid>
```

Pull the UID and the transaction UUID with a straightforward regex. If either is missing, surface a clear "couldn't parse k8bot body — fall back to Mode B with `user_id=<...> transaction_id=<...>`" message and stop.

Persist `channel_id` + `message_ts` for Step 6.

For Mode B, skip the URL parse — the caller already supplied the pair.

### Step 2 — Confirm prereqs

```bash
test -n "$OPENAI_MEMORY_API_KEY" && echo "OPENAI OK" || echo "OPENAI MISSING"
test -n "$GCP_KEY" && echo "GCP_KEY OK" || echo "GCP_KEY MISSING (rung 2 unavailable)"
test -n "$AURA_API_TOKEN" && echo "AURA OK" || echo "AURA MISSING (identity lookup unauthenticated)"
```

If `OPENAI_MEMORY_API_KEY` is missing, walk the user through adding it to `~/.claude/settings.json` via the `update-config` skill, then have them restart Claude Code. The skill reads stored objects via a project-scoped OpenAI key; there's no MCP fallback — this is a hard fail. **Do not fall back to `OPENAI_ADMIN_API_KEY`** — Admin keys lack the `model.request` scope these reads require and will 401 (PROD-3572).

`GCP_KEY` powers rung 2 (the llmCall ledger in BigQuery that resolves the transaction → response id — the normal memory-ingest path). Without it the skill still runs but skips straight to Braintrust (rung 3) every time, so add it too. It's not a hard fail — the skill degrades — but the common case wants it.

Key creation: <https://platform.openai.com/api-keys> → Create new secret key → Owned by: Service account → Project: `Memory [Production]`. Distinct from the org-level `OPENAI_ADMIN_API_KEY` the sibling cost skills (`eng:analyze-openai-costs`, `eng:report-openai-user-cost`) use — that key still works for `/v1/organization/*` cost endpoints, just not for stored completions.

Cowork caveat ([PROD-1305](https://linear.app/aura-health/issue/PROD-1305)): `mcp__workspace__bash` strips host env. Paste-export per session if running in Cowork:

```bash
export OPENAI_MEMORY_API_KEY='<paste from host ~/.claude/settings.json>'
```

### Step 3 — Fetch the guardrail trace

Invoke the bundled script:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/investigate-memory-ingestion-guardrail/scripts/fetch_guardrail.py \
  --transaction <uuid> \
  [--user <firebase-uid>]
```

The script runs the resolution ladder (rung 1 chat-completions metadata query → rung 2 llmCall ledger (BigQuery) → Responses/chat fetch by id → rung 3 Braintrust). For memory-ingest the normal path is rung 2: the transaction is a stored **response**, so the chat query misses, the ledger returns its `openaiId`, and the script reads `GET /v1/responses/{id}` + `/input_items`. `--user` is what lets the ledger resolve (it filters by `userId`); the k8bot alert always carries the UID, so pass it. It returns a markdown report with:

- Header (transaction, UID, stored-object id — `resp_…` or `chatcmpl-…`, created timestamp, model, token usage, OpenAI Logs URL, and a **Source** line naming which rung produced it).
- **Guardrail outcome** — parsed from the assistant `choices[0].message.content` JSON: `seriousHarmFlagSet`, `seriousHarmEvidence`, and the `memories` count. (Any unrecognised fields are dumped under "Additional fields — schema may have drifted" so a future rev surfaces immediately.)
- **Aura user record (primary identity source)** — the script fetches `GET /users/:userId` (see `Tools/Aura API`) and surfaces `givenName`, `familyName`, `email`, `countryCode` (mapped to the `resource_map.md` key, e.g. `GB → UK`), and `timezone`. This is the **primary source** for `{first_name}`, the country, and the **CS email address** (Steps 5b/5d/6). The endpoint is unauthenticated, so the lookup works for any UID; a missing UID, a 404, or an error degrades cleanly — the section says so, and the first name/country then fall back to the paste-derived Identity section below, with no email surfaced for the Slack reply.
- **User-provided summary** — the full appended user block from the system message, fetched from `/responses/{id}/input_items` on the Responses path (or `/chat/completions/{id}/messages` on the chat path). Read it to confirm the flag isn't an obvious false positive and (as a fallback) to source the account holder's first name + country/location for the email (per the paste-use rule above) — never to re-classify risk or to be echoed into Slack.
- **Identity for email (compose-only — fallback source)** — a dedicated section the script extracts from the paste: a best-effort first-name candidate (or a note that the name is the `[USER_NAME]` placeholder / absent) and a location/region string. **Fallback only** — use these to fill a first name or country the Aura record above left blank (or when it was unavailable). A blank field is expected, not a blocker (no name → `Hi there,`; no location → the country-not-in-map path).
- **User context** — a separate internal `## User Information` profile block, rendered only when the trace actually carries one. Current memory-ingest traces usually have **no** such block — identity then comes from the summary via the Identity section above. Its absence is normal and never blocks a draft.

If `--user` is passed it's compared against the completion's `metadata.user` and a one-line warning is printed when they disagree (a paranoia check). When more than one memory-ingest completion shares a transaction, `--user` also disambiguates which one to render.

**Braintrust fallback (rung 3 — genuine last resort).** Braintrust logging still runs in parallel and backs rung 3. It's reached only when rungs 1–2 can't produce the trace from OpenAI — i.e. the transaction predates the ledger deploy (the ledger is forward-only, no backfill), or OpenAI itself is unreachable. It is **not** an every-trip fallback: a normal memory-ingest trip resolves on rung 2 (Responses API) and never touches Braintrust. The fallback needs `BRAINTRUST_API_KEY`; if it's unset the script says so and prints the terminal 0-match message. The Braintrust report's `**Source:**` banner states **why** rung 3 fired — one of: OpenAI unreachable (the ledger *confirmed* the record exists but the fetch failed → an availability problem, not a missing record); OpenAI unreachable with presence unconfirmed; ledger unreachable; or genuinely absent from both chat completions and the ledger (predates the ledger / never recorded — the benign expected case). A recurring **outage**-flavoured banner is worth escalating (OpenAI or the ledger is down); a recurring **genuinely-absent** banner just means those transactions predate the ledger deploy — expected, not a regression.

The script is deterministic and side-effect-free. Re-running it on the same transaction returns the same output. Typical runtime is sub-second: rung 1 is one metadata query, rung 2 is one ledger call + one stored-object read (+ one `/input_items` page); rung 3 only runs when the OpenAI rungs can't produce the trace.

### Step 4 — Confirm the flag and pick the category

Apply the rubric in [`references/classification_rubric.md`](references/classification_rubric.md). The flag is set on every alert, so the **default outcome is active risk → draft the email** (Step 5). Do this:

1. **Map the category.** Read `seriousHarmEvidence` and map it to one of the seven categories via `resource_map.md` → "Mapping evidence → category." This is a lookup, not a re-judgment.
2. **False-positive check (the only downgrade for "no risk").** Fall to the short-post path **only** when the paste unmistakably matches the Memory Ingestion prompt's own "do not flag" historical patterns — the event is described as past, over, recovered-from, or background, with no current-tense danger; or the paste is empty / template-only (the flag fired on retrieved prior memory, nothing current to act on). This is a conservative catch of an over-firing flag, not a re-classification. If the paste shows the risk as current, unresolved, or ongoing, trust the flag and draft. Do not weigh past against present or project future risk.
3. **Resources-in-place check.** If the paste states the user is **already in active professional care for this matter** (a therapist for the mental-health concern, a treatment program for the substance concern, a lawyer for the legal/custody matter), fall to the short-post path — the email's purpose is to offer resources to someone who has none. **Carve-out:** this does NOT apply to imminent violence to self or others (categories 1, 2, and the imminent-physical-danger slice of 3 and 4) — those always draft and page regardless of existing resources.

Keep the synthesis to ≤2 sentences. State the category and, if downgrading, which downgrade applies (false positive / resources in place). Cite the category and the evidence phrase — never quote or paraphrase the paste.

**On active risk, a draft is always produced.** The only no-draft outcomes are the two downgrades above — clear false positive, or resources already in place. Missing identity is **not** a third one: an unavailable first name → `Hi there,`; an unknown or unmapped country → the country-not-in-map path (Step 5c / Step 6: draft with the global backstop, reviewer localizes). "Reviewer, please verify name/country" is an *addendum to a draft*, never a substitute for one — never post an active-risk reply that withholds the draft because name or country couldn't be sourced.

**Minors.** If the paste is first-person from someone who appears to be a minor (self-identified age under 18, first-person school/grade context, "my mom won't let me"), classify and **draft exactly as normal** — every draft is human-reviewed and CS sends, so a person confirms the channel is safe before anything reaches the user. Attach the minor reviewer flag in Step 6 so CS can confirm a safe channel and consider any reporting obligation. A minor referenced in the *third person* (an adult worried about their child) is a caregiver case → Step 4.6.

### Step 4.6 — At-risk subject: self, third party, or caregiver

For an active-risk case, decide **who the resources are for**. This sets `{audience_suffix}` and the resource selection in Step 5. **Category 2 skips this step:** the person they wrote about hurting is not someone the resources are for, so a category-2 email never takes the third-party suffix, the caregiver line or the reframed closing (`email_template.md` → *Category 2 body*).

- **The account holder is the (only) person at risk** → `{audience_suffix}` empty; standard lines for the category.
- **A third party in their life is (also) at risk** — a partner, friend, or family member the user is worried about or caring for → `{audience_suffix}` = `, for you or for anyone you care about`; add the country's caregiver-oriented support line to the list in Step 5c (US: NAMI Family Support Line). Never name or characterize that person; the generic phrase is the only acknowledgment.
- **Unclear who the at-risk person is** → treat as the third-party case (fail toward inclusion): same suffix, the reframed `{closing_line}`, and the caregiver support line. The hedged phrase is safe both ways; a bare self-risk framing is not.

**Caregiver-of-a-minor sub-case.** When the at-risk third party is a **minor** in the account holder's care (e.g., a parent worried about a child's active self-harm), draft the caregiver email (third-party framing) **and** attach the safeguarding flag to the Step 6 reply — a minor's active risk still warrants human safeguarding eyes even when it reaches us through an adult. (These cases page via Step 6b whenever the case is high-priority — e.g. category 1 or 2.)

### Step 5 — For an active risk, draft the CS email

Only on the active-risk path (Step 4). Skip on the short-post path (no active risk / resources in place).

The email is **category-matched**: its language is assembled from the category, not from one fixed block. [`references/email_template.md`](references/email_template.md) is the source of truth — work through its Step 1 (pick category) and Step 2 (fill placeholders) in order. The sub-steps below map the trace fields onto that template.

**5a. Confirm the category.** From Step 4, one of the seven. It drives `{risk_phrase}`, `{resource_intro}`, and `{closing_line}` per `email_template.md` — except **category 2, which uses its own body** (`email_template.md` → *Category 2 body*) with none of the three. **Do not use suicide/self-harm language outside category 1.** When multiple categories co-occur, pick the most immediate (Handler priority: crisis 8.1 > passive-existential 8.2 > complex-condition 8.7) and let the resource list carry a line from each relevant category, capped at 2–3.

**Category 2 — close relationship.** Record `close relationship: yes | no | unknown` — `yes` when the target is a current or former partner, spouse, or a family or household member. Read it from `seriousHarmEvidence` and the flagged statement Step 4.1 already mapped — the same lookup that picked the category, not a re-read of the paste for anything else. `yes` adds the country's row-2a line in 5c; `no` and `unknown` use row 2 alone. It never withholds the draft or the page.

**5b. Detect the user's country.** The **Aura user record is the primary source** — use its `countryCode` (already mapped to the `resource_map.md` key in the script's *Aura user record* section, e.g. `GB → UK`), and use its `timezone` to confirm an ambiguous or missing code (`America/New_York` → US, `Australia/Sydney` → AU, `Asia/Riyadh` → SA, `Europe/London` → UK). Fall back only when the Aura record didn't supply a country (blank, 404, or unavailable): then prefer a profile block's `timezone` line if the trace carries one, and otherwise read the country from the **pasted summary** — a stated location, region, or named institution (the paste-use rule permits this; the script's *Identity for email* fallback section surfaces the location string for you). When the summary names more than one place, use the **account holder's** current or primary location — the second-person subject's own home — and ignore a third party's location, childhood or past locations, and travel mentions; if it's genuinely unclear which is theirs, treat the country as unknown. Country + first name are the only user details read for the email — never risk signals or other situational specifics from the paste. When no source yields a country, use the country-not-in-map path (Step 5c / Step 6): still draft, with the global backstops, and mark the country unknown.

**5c. Pick resources.**

- **Country in the map** (US / CA / UK / AU / NZ / IE): take 2–3 lines from the `(country, category)` cell in `resource_map.md`. **Lead with the hotline.** Add an ongoing-care directory line only for the non-acute categories (5, 6, and any non-imminent case) where the country has a trustworthy one — never on an acute category-1/2 or imminent-danger email. Don't duplicate the emergency-services backstop already in the template body. **Only draft a line that fits the case.** `resource_map.md` → "Coverage gaps" lists `(country, category)` cells with no verified specialized line — for those, first use the country's related same-category lines if the table has them (e.g. a trafficking case → that country's DV / sexual-violence lines), then its category-1 crisis line if it fits; only if nothing in the table fits, use the global backstops (the country-not-in-map path below). Never fabricate or substitute a mismatched line (e.g. a child line for an elder case).
- **Country NOT in the map** (anything else, or country unknown): still draft — never with a mapped country's national lines (US/UK/etc.). Pick the single global lead per `resource_map.md` → "Global backstops": for the **emotional-need categories** (1, 2, 6, and interim support for 3/4) use **Befrienders Worldwide** (befrienders.org); for a **physical medical emergency (5)** use the prompt-medical-care guidance line (the user's physician, an urgent-care clinic, or a telehealth service) and for **basic-safety (7)** the practical-help / emergency-services guidance — Befrienders is emotional support, not medical or basic-needs, so it is never the lead for 5/7. Whichever lead applies keeps `{resource_list}` non-empty so the template grammar and closing hold. The dial-able immediate action is the emergency-services line the template body already carries. Flag in the Step 6 reply that the resource is global and a reviewer should add a country-correct specialized line before CS sends — especially categories 3/4 (DV / abuse / trafficking / child / elder), where Befrienders is only interim support, and 5/7, where a country-correct medical / basic-needs line is still needed.
- **Coverage-gap in a *mapped* country** (a `(country, category)` cell under `resource_map.md` → "Coverage gaps" — e.g. US category 5, a general medical emergency): follow the same routing as the country-not-in-map path for the missing line's category — a physical medical (5) or basic-safety (7) gap leads with medical-care / practical-help guidance, never Befrienders. Keep `country: <country>` in the Step 6 reply (the country *is* mapped) and add the coverage-gap reviewer line.
- **Category 2 with `close relationship: yes`** (5a): add the country's **row 2a** line from `resource_map.md` as the last resource. Ireland and every unmapped country have no 2a line — use row 2 (or the single global line) alone and add the reviewer line from `email_template.md` → *Category 2 body* to the Step 6 reply.
- **Third-party / caregiver cases** (`{audience_suffix}` present, from Step 4.6 — never category 2): in addition to the line(s) for the category, add the country's caregiver-oriented support line where one exists (US: NAMI Family Support Line). Keep it one flat, unlabelled list — never split "for you" vs "for them."

**5d. Assemble the template** per `email_template.md` → Step 2 — **category 2 uses its own body** (`email_template.md` → *Category 2 body*) and fills only `{first_name}`, `{resource_intro}` (that section's lines) and `{resource_list}`. Fill each placeholder from that file's rules:

- `{first_name}` — the **account holder's own** first name. Take it from the **Aura user record's `givenName`** (primary). Fall back only when that's blank or unavailable: then the profile block's given name if the trace has one, otherwise the pasted summary (the paste-use rule permits this) — but only the account holder's own name (the summary is written in the second person *about* them, so their name is the one addressed as "you"), never a third party the paste names (a partner, child, friend). The memory-ingest prompt usually templates the name out to the literal `[USER_NAME]` placeholder, so in practice the summary rarely yields a real name — that's expected, and the Aura record is why it usually doesn't matter. Fall back to `there` ("Hi there,") when no source yields the account holder's name or it's unclear which is theirs. A missing name **never** blocks the draft.
- `{audience_suffix}` — from Step 4.6: empty when the account holder is the only person at risk; `, for you or for anyone you care about` when a third party is (also) at risk or it's unclear. Never name the third party.
- `{subject}` — the discreet default; never a risk word.
- `{risk_phrase}` — the neutral, category-matched phrase (categories 3/4 stay deliberately vague for monitored-device safety).
- `{resource_intro}` — match count grammar and the type label ("crisis resources" only for genuine crisis lines).
- `{resource_list}` — the line(s) from Step 5c: 2–3 for a mapped country; a **single global line** for an unmapped or unknown country (do not pad it, and never add a mapped country's national line to reach a count) — **Befrienders Worldwide** for the emotional-need categories, or the **prompt-medical-care / practical-help guidance line** for a physical medical (5) or basic-safety (7) case.
- `{closing_line}` — the line for the category from 5a; reframe the recipient-directed clause per `email_template.md` for third-party/caregiver cases. If one resource, switch "the resources above" → "the resource above."

**5e. Run the final validation checklist** in `email_template.md` (all 13 items) plus the Tone and Privacy sections. High-frequency failures it catches: self-harm language on a non-suicide trip, an ongoing-care directory line on an acute mapped-country email (the Befrienders crisis-line directory on an unmapped-country draft is allowed), "crisis resources" labelling a non-crisis list, count-grammar mismatch, a non-discreet subject, and any PII or paste content leaking into the email body beyond the allowed account-holder first name (Step 5d). The country is used only to route resources and appears on the Slack reviewer reply — never written in the email body. If any check fails, rewrite until it passes.

### Step 5.5 — Prior guardrails for this user (every outcome)

The same person can trip this channel and `#guardrails-conversation`, or one of them repeatedly, and each alert gets its own draft. Look across **both** channels before posting:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/investigate-memory-ingestion-guardrail/scripts/prior_guardrails.py \
  --user <firebase-uid> --before-ts <message_ts>
```

Mode B: omit `--before-ts`. It lists the user's alerts in the last 30 days strictly older than this one, with each one's outcome (from the triage reply on its thread), whether it was paged, and whether a reviewer put the ✅ on it (the mark that the CS email went out). It prints three ready lines, used verbatim in Step 6:

- `last_email_line` — the newest active-risk draft a reviewer marked ✅ (CS's sent mark) in either channel in the last **90 days**: `📧 *Last CS email:* draft posted <YYYY-MM-DD HH:MM UTC> (<age>) in #guardrails-<channel> — marked sent ✅ (<open thread>)`, plus the email count and any newer draft not yet marked sent. Every earlier draft this line or `email_line` names carries a Slack link to its alert thread, so the reviewer can read the email that already went out. When the user has earlier alerts but none marked sent it reads `none marked sent in 90d`; with no history at all it is `null` — omit the line. It goes **directly under the UID line** on every format so the reviewer sees it first. The date is when the draft was posted: Slack does not timestamp reactions, so the actual send time is not known.
- `prior_line` — always present, on **every** format, including `none`. When a channel could not be read it says `lookup incomplete`, and that is never reported as "no prior guardrails".
- `email_line` — present only when an active-risk draft for this user was posted in either channel in the last 24 hours. **One CS email per user per day**: this flags the duplicate for the reviewer. It does not withhold the draft — the draft is still produced and the reviewer decides whether to fold it into the earlier email, hold it, or send (a high-priority case is always their call).

Never add anything from an earlier thread beyond what the script prints — the same Slack content rule applies.

### Step 6 — Post on the alert thread (Mode A) / render to caller (Mode B)

Mode A captured `channel_id` + `message_ts` in Step 1; post the reply by **invoking [`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md)** with `channel=<channel_id>` and `thread_ts=<message_ts>`. Use its posting **mechanics**, but **skip its draft-and-confirm gate** — this is a real-time-agent flow that owns the always-post decision below. The `thread_ts` is critical. **Always post** — the human review happens on the email *draft*, after it's posted to the thread.

**Card guards and scratch files (mandatory in cadence mode).** Pass `--expect "<uid8>…"` (this alert's redacted UID), `--max-chars 8000` and `--max-chunks 3` on every post: the helper refuses, exit 2 and nothing sent, a body that is not this alert's reply. A refusal leaves the alert without a bot reply, so the next tick retries it. Report it to the caller as **not posted: card guard**, never as handled, because a guardrail reply is a safety path. When the caller gives an **output folder** (its worker folder, e.g. `g5_b1/`), every file this skill writes goes there under a typed name (`g5_tx_<uid8>.txt` for the guardrail fetch output, `g5_reply_<uid8>.txt` for the reply), never the scratchpad root or a generic name, and the transcript file is deleted once the reply is posted or skipped. Without one, pipe the reply on stdin (`--text -`). See `data:run-all-analysis-cadence` → *Scratch files and posting*.

**Footer.** Every reply gets the canonical `Tools/Slack` attribution footer (`_Generated by `eng:investigate-memory-ingestion-guardrail:<version>` · <YYYY-MM-DD>_`). **Pass `--footer eng:investigate-memory-ingestion-guardrail` to `shared:post-to-slack`** — it stamps the versioned footer deterministically from `metadata.version`. Do **not** compose the footer into the reply body: a model-typed footer silently drops the `:<version>`, and combining both double-stamps.

**Slack content rule (non-negotiable).** Every reply carries only the **category**, the non-graphic **`seriousHarmEvidence`** phrase (which the upstream prompt is spec'd to keep non-graphic), on category 2 the categorical `close relationship` value from Step 5a, and — on an active-risk draft — the **coarse country or timezone** needed to route resources. Never include a city, neighbourhood, name, a quote or paraphrase of the paste, or any other specific from the pasted summary or user profile. No "rationale citing the summary." The redacted UID (first 8 chars + `…`) is the only identifier.

**One documented exception — the account holder's email.** On the **active-risk draft formats only**, the reply also carries the account holder's **email address** (from the Aura user record) on the UID line, so the reviewer can send the CS draft without a further lookup. This is the single piece of user PII permitted on Slack, and it is scoped deliberately: include it **only** when the Aura record actually resolved an email, and **only** on the two active-risk draft formats — never on the no-active-risk or resources-in-place short posts (nothing is sent there, so the email would be gratuitous). The email is the account holder's own, from the Aura record — never an address parsed out of the paste. This channel (`#guardrails-memory-ingestion`) is restricted to the reviewers who action these alerts.

Reply format depends on Step 4's outcome. There are exactly four, and **both active-risk formats include the filled draft** — an active-risk case never posts a reply that omits the draft (a missing name or country routes to `Hi there,` / the country-not-in-map draft per Step 4, it does not suppress the draft):

- **No active risk** (clear false positive / past disclosure):
  ```
  ✅ no active risk — no action needed.
  UID `<first8>…` · category: <category> · evidence: "<seriousHarmEvidence>"
  <last_email_line from Step 5.5 — omit if null>
  Reason: <one short categorical reason — e.g. "paste describes a past event, framed as resolved">
  <prior_line from Step 5.5>
  ```

- **Active risk, but resources already in place** (no outreach drafted — the risk is real, outreach is just redundant):
  ```
  ☑️ active risk — resources already in place, no outreach drafted.
  UID `<first8>…` · category: <category> · evidence: "<seriousHarmEvidence>"
  <last_email_line from Step 5.5 — omit if null>
  Reason: <one short categorical reason — e.g. "user reports being in active care for this">
  <prior_line from Step 5.5>
  ```

- **Active risk** (country in map):
  ```
  🚨 active risk — review draft below before CS sends.
  UID `<first8>…` · category: <category> · country: <country> · email: <account-holder email from the Aura record — omit this field if none resolved>
  <last_email_line from Step 5.5 — omit if null>
  Evidence: "<seriousHarmEvidence>"
  <Harm-to-others: close relationship <yes|no|unknown> — category 2 only>
  <prior_line from Step 5.5>
  <email_line from Step 5.5 — omit if none>
  Template deviations: <none — or: what fixed prose changed from references/email_template.md, and why>

  ---
  **Draft email (not yet sent — review and approve):**

  Subject: <discreet subject per email_template.md>

  Hi <first_name>,
  …
  Warmly,
  The Aura team
  ```
  The category-2 line is categorical only — never the target's name or relationship. Add the 2a-gap reviewer line under it when 5c found no row-2a line for the country (Ireland, or any unmapped country).

  **Coverage-gap variant.** When a mapped country has no specialized line for the category (Step 5c), use this same format — keep `country: <country>` — and add, after `Evidence:`, a line: `Reviewer: <country> has no <category> line in the map — the draft uses its related/global lines; add the specialized line before CS sends.`

- **Active risk** (country NOT in map — draft with global resources):
  ```
  🚨 active risk — country not in resource map; draft below uses global resources, reviewer please localize.
  UID `<first8>…` · category: <category> · suspected country: <country or "unknown"> · email: <account-holder email from the Aura record — omit this field if none resolved>
  <last_email_line from Step 5.5 — omit if null>
  Evidence: "<seriousHarmEvidence>"
  Reviewer: the map has no country-specific lines for <country> — the draft uses the global backstop(s) from references/resource_map.md; add or swap in a country-correct specialized line before CS sends.
  <Harm-to-others: close relationship <yes|no|unknown> — category 2 only>
  <prior_line from Step 5.5>
  <email_line from Step 5.5 — omit if none>
  Template deviations: <none — or: what fixed prose changed from references/email_template.md, and why>

  ---
  **Draft email (not yet sent — review and approve):**

  Subject: <discreet subject per email_template.md>

  Hi <first_name>,
  …
  Warmly,
  The Aura team
  ```
  Append ` · timezone: <tz>` to the UID line only when the profile block supplied a timezone. When the country was inferred from the summary (or is unknown), omit it — never place a city, region, institution, or any other paste detail in the reply.

  Use *this* format only when the country itself isn't in the map, or is unknown. A **coverage-gap** case in a *mapped* country (Step 5c — the country is in the map but has no specialized line for the category) uses the **country-in-map** format above instead: keep `country: <country>` (it genuinely is mapped) and add the coverage-gap `Reviewer:` line described there, so the reviewer sees a gap flag without being told the country is unmapped.

**Minor flag.** When the account holder appears to be a minor (Step 4) **and there is active risk** — i.e. either active-risk draft format above **or** the "resources already in place" short-post — append this line immediately after the `Evidence:` / `Reason:` line (the draft, if any, is still produced). Do not append it to the plain "no active risk" reply — a minor with no active risk needs no safeguarding follow-up, consistent with leaving no-risk cases alone.
```
⚠️ Reviewer: the account holder appears to be a minor — confirm a safe channel and consider any reporting obligation before sending.
```

**Caregiver-of-a-minor flag.** When the at-risk third party is a minor in an adult account holder's care (Step 4.6), append this line to the active-risk format instead, after the `Evidence:` line — the caregiver draft is still produced:
```
⚠️ Safeguarding: the at-risk person is a minor in the account holder's care. A minor's active risk warrants safeguarding review — route to whoever owns that alongside sending. Do not name or contact the minor.
```

For Mode B (no Slack URL captured), render the same reply text to the caller as console output instead of posting; flag that no Slack anchor was available.

### Step 6b — High-priority @channel alert

After the thread reply posts, check whether the case is **high-priority**. If it is, post a second reply to the **same alert thread** that mentions `<!channel>` so the on-call reviewer is paged immediately.

**High-priority = active imminent threat of violence to self or others.** This is evaluated **only for cases Step 4 already classified as active risk** (the drafted formats). It never runs on a no-active-risk short post, and it does **not** reopen Step 4's active-vs-historical determination — a paste Step 4 downgraded as historical (e.g. a *past* history of self-harm with no current urges) is not active, so it never reaches this step and never pages. Among active-risk cases, only these qualify:

- **Category 1** (suicide / serious self-harm) — always, once Step 4 has classified the case as active. Step 4 already made the active-vs-historical call (a plainly historical self-harm disclosure was downgraded there and is not here), so an active category-1 case pages without a second imminence check. Do not re-read the paste to decide whether to page.
- **Category 2** (risk of harming someone else) — always.
- **Category 3 or 4** *only when there is imminent physical violence to a person* — the user is an imminent victim, or the perpetrator has current access and there is an acute physical-danger signal (a threat to kill/harm, recent physical violence such as choking/strangulation, active stalking by someone with access, an attempted abduction). Coercive control, financial/sexual coercion, or emotional abuse **without** an acute physical-danger signal is standard active risk (thread reply only).

Everything else — categories 5, 6, 7, and non-imminent 3/4 — is standard active risk and does **not** page. A passive upload never carries a live medical emergency, so acute-medical/overdose/basic-safety cases get the email but not the page. When the account holder is a minor, the same rule applies: page only if the case is imminent violence to self or others.

When high-priority, post a second reply to the **same alert thread** using the same `shared:post-to-slack` mechanics — same `channel_id` + `thread_ts`, skip the draft-and-confirm gate, still pass `--footer eng:investigate-memory-ingestion-guardrail` (the deterministic versioned footer). Do not fall back to the full interactive post skill or a raw `chat.postMessage` call — urgent paging must not be gated:

```
<!channel> 🚨 HIGH PRIORITY — <short evidence phrase> · UID `<first8>…`
Reviewer needed immediately — details above in this thread.
```

Keep the body to two lines. Copy `<!channel>` exactly: this posts through `slack_post.py` (`chat.postMessage`), and the API only pages on Slack's special-mention syntax — a typed mention is plain text and notifies no one. Posted correctly, `<!channel>` fires the notification even inside the thread. Do not restate the draft or summary — the thread already has it from Step 6.

When NOT high-priority, skip this step entirely.

**PII rules (non-negotiable).** Per `Compliance/Privacy`:
- **First name only** in the email body — the account holder's own, from the Aura user record's `givenName` (primary), or the profile block / pasted summary when it's blank; no other user detail.
- **Slack carries only the category + the non-graphic `seriousHarmEvidence` phrase** — **plus, on the active-risk draft formats only, the account holder's email** from the Aura record (so the reviewer can send without a further lookup). Never echo the user's pasted summary or profile content — no quoting, paraphrasing, or specifics — and never put the email on the no-active-risk or resources-in-place short posts.
- **UID redacted** to first 8 chars + `…` everywhere it appears in Slack.
- **Email body is reviewer-visible only** until a human approves it; the skill never sends.

## Output

The skill returns to the caller (in order):

1. The script's markdown report (header, guardrail outcome, Aura user record, summary, user context, drill-in commands).
2. A **decision** line — active risk (with the category) or no active risk / resources in place (with the reason).
3. The Step 5.5 last-CS-email line (when not null), the prior-guardrails line, and the once-a-day line when it fired.
4. (Active risk) the drafted CS email, filled and tone-checked.
5. (Mode A) Confirmation that the reply was posted to the alert thread, with the message text.
6. (Mode A, high-priority only) Confirmation that the `<!channel>` alert was posted.

The pasted summary is read for the false-positive and country/name checks but is never reproduced in the output beyond the category + evidence phrase.

## Setup (one-time)

**OpenAI project-scoped API key.** `OPENAI_MEMORY_API_KEY` (`sk-svcacct-…` / `sk-proj-…`), a service-account key scoped to the `Memory [Production]` project — see `Tools/OpenAI` in AI Knowledge for the canonical walkthrough. Add it to `~/.claude/settings.json` under `env`, restart Claude Code. Create at <https://platform.openai.com/api-keys> (Create new secret key → Owned by: Service account → Project: `Memory [Production]`). **Not** the same key as `OPENAI_ADMIN_API_KEY` — that org-level Admin key lacks the `model.request` scope this skill's stored-completions reads now require (PROD-3572), though it remains valid for the sibling cost skills' `/v1/organization/*` calls.

**Braintrust API key (optional — rung 3 last resort).** `BRAINTRUST_API_KEY` enables the rung-3 Braintrust fallback (see Step 3), reached only when a transaction predates the ledger deploy or OpenAI is unreachable. Without it the skill still resolves every transaction OpenAI + the ledger can serve (the common case); you only lose recovery of those edge transactions. Get one at <https://www.braintrust.dev/app/aurahealth/settings/api-keys>; add it under `env` and restart. The BTQL endpoint is `https://api.braintrust.dev/btql` (no `/v1`).

**`GCP_KEY` (needed for rung 2).** The service account reads the llmCall ledger from BigQuery (`aura-main.llm.llm_call`) to resolve a memory-ingest transaction to its `openaiId` (the normal Responses-API path). Without it, rung 2 can't run and the skill falls to Braintrust (rung 3) for every trip — so it's effectively required for the common case, even though the skill degrades rather than hard-failing. `AURA_API_TOKEN` is sent (for parity) on the `GET /users/:userId` identity lookup, which is **unauthenticated** on prod today, so identity enrichment works with or without it; a 404/error there degrades to the paste-derived first name/country and surfaces no email.

**Python deps.** `python3 -m pip install --user -r ${CLAUDE_PLUGIN_ROOT}/skills/investigate-memory-ingestion-guardrail/scripts/requirements.txt`. Just `requests`.

**Slack.** Step 1 reads the alert via `shared:read-slack` and Step 6 posts the reply via `shared:post-to-slack` — both MCP-first with a `SLACK_BOT_TOKEN` script fallback.

## Edge cases

- **Transaction not resolvable anywhere.** The script walks all three rungs (chat completions → ledger/Responses → Braintrust) before giving up, and only prints the terminal "0 matched" message — exiting non-zero — when every rung comes up empty. The message names the ledger status (`found`/`not_found`/`unreachable`/`skipped`) so the cause is legible. Causes for a true triple-miss: the UUID is malformed, the trace predates *both* the ledger deploy and OpenAI's metadata rollout, it's past the retention horizon, or it was never persisted. A rung-2 resolve is the normal path and is not a fallback; a rung-3 Braintrust render whose banner says **OpenAI/ledger unreachable** is worth escalating (an outage), while one that says **genuinely absent** just means the transaction predates the ledger — expected.
- **Multiple memory-ingest completions for one transaction.** Should not happen (one transaction = one ingest call), but if it does the script picks the most recent and lists the others on stderr. Pass `--user <uid>` to disambiguate. Don't silently assume the first.
- **Assistant `content` isn't valid JSON.** The memoryIngest output envelope was changed in [PROD-1944](https://linear.app/aura-health/issue/PROD-1944) and may change again. If JSON parsing fails the script prints the raw content and notes the schema mismatch — mark the outcome "unknown — schema drift" rather than guessing. (The current envelope is `{memories, seriousHarmFlagSet, seriousHarmEvidence}`.)
- **No `User-Provided Summary` section in the system prompt.** Older completions won't have one. The script reports "no summary block found" and the false-positive check relies on `seriousHarmEvidence` alone — the read is weaker but not blocked. The script accepts the section at any heading depth per [ENGPROM-58](https://linear.app/aura-health/issue/ENGPROM-58), capturing the full multi-section paste to end-of-prompt.
- **Aura user record unavailable (404, error, or no UID).** The identity lookup is best-effort. When it returns nothing — a Braintrust-fallback trace with an empty `metadata.user`, a `USER_NOT_FOUND` 404, or a transient error — the *Aura user record* section says so, the first name and country fall back to the paste-derived *Identity for email* section, and **no email is surfaced** (so the active-risk reply omits the `email:` field). This never blocks the draft or the readout. If the endpoint is ever tightened to require auth and `AURA_API_TOKEN` is scoped too narrowly to read another user, the same graceful degrade applies.
- **Scripting `--user` from a shell variable.** Don't name the shell variable `UID` — it's readonly on bash 3+ and expands to the process owner's numeric uid (e.g. `501` on macOS), triggering a spurious mismatch warning. Use `USER_ID`. ([ENGPROM-59](https://linear.app/aura-health/issue/ENGPROM-59))
- **Slack URL points at a non-k8bot message.** Step 1 falls through to "couldn't parse k8bot body — fall back to Mode B". Don't scrape arbitrary message shapes.
- **Slack URL parse failure (malformed permalink, MCP error).** Surface the error and ask the caller to re-paste or fall back to Mode B. Don't silently lose the reply anchor.
- **Re-running the skill twice on the same input.** Read-only and idempotent; the script returns the same bytes.
- **Caller provides Mode B but the UID and the completion's `metadata.user` disagree.** Print a warning. The transaction ID is authoritative — proceed against the completion's actual UID and note the mismatch.

## When to use this vs siblings

- **Use this** when the entry point is a `k8bot :warning: Memory ingestion guardrail triggered` Slack alert, or you already have a memory-ingest transaction ID + UID and want the guardrail-specific readout.
- **Use [`eng:investigate-openai-trace`](../investigate-openai-trace/SKILL.md)** for any other OpenAI stored-completion / trace debugging. It's the lower-level skill — this one is the guardrail-specific synthesis layer on top.
- **Use [`data:analyze-user-conversations`](../../../data/skills/analyze-user-conversations/SKILL.md)** when the entry point is a k8bot **rating** message or you want user-level conversation analysis.
- **Use [`eng:update-intelligence-prompt`](../update-intelligence-prompt/SKILL.md)** when the conclusion is "the guardrail rule itself needs adjusting" — that's a prompt change, out of scope here.

## References

- [`scripts/prior_guardrails.py`](scripts/prior_guardrails.py) — the Step 5.5 lookup across both guardrail channels (copy; canonical in `investigate-conversation-guardrail`, see `docs/canonical-sources.md`). [ENG-1540](https://linear.app/aura-health/issue/ENG-1540).
- [`scripts/fetch_guardrail.py`](scripts/fetch_guardrail.py) — the resolution ladder (chat-completions → llmCall ledger (BigQuery) → Responses/chat fetch by id → Braintrust) + parse, single source of truth for the structured report shape.
- [`references/classification_rubric.md`](references/classification_rubric.md) — trust-the-flag logic, the seven categories, the false-positive and resources-in-place downgrades; de-identified sanity-check cases.
- [`references/email_template.md`](references/email_template.md) — category-matched email assembly (seven categories), placeholder rules, tone/privacy rules (non-negotiable), final validation checklist.
- [`references/resource_map.md`](references/resource_map.md) — country × category resources (US, CA, UK, AU, NZ, IE), hotlines first with ongoing-care directories, + global backstops + "country not in map" branch.
- [PROD-1980](https://linear.app/aura-health/issue/PROD-1980) — this skill's ticket and the dry-run trace fixture.
- [PROD-1944](https://linear.app/aura-health/issue/PROD-1944) — recent change to the memoryIngest output envelope; useful context if the JSON schema drifts again.
- Sibling: [`eng:investigate-openai-trace`](../investigate-openai-trace/SKILL.md) — free-form trace debugging across OpenAI projects.
- `Tools/OpenAI` in AI Knowledge — auth, the "Reading stored completions" access pattern, the metadata field reference, project inventory.
- `Compliance/Privacy` in AI Knowledge — PII redaction rules for Slack/Notion outputs.
