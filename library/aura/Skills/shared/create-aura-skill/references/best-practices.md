# Skill authoring best practices

The principles that shape every skill in this repo. Apply them when scaffolding a new skill or revising an existing one. Read this **before** drafting `SKILL.md` content — the principles inform naming, scoping, and structure.

> **Narrative version with rationale and examples lives at `Procedures/AI Native/Skill Authoring`** in the AI Knowledge Notion teamspace. This file is the canonical executable — the Notion page cites it as `(parent)`. Edit here first, then propagate.

> **AINative skills follow the open [Agent Skills specification](https://agentskills.io/specification).** Anthropic's [Skills overview](https://claude.com/docs/skills/overview), [Creating custom skills](https://claude.com/docs/skills/how-to), and [Skill authoring best practices](https://docs.claude.com/en/docs/agents-and-tools/agent-skills/best-practices) are the upstream canonicals. This doc layers AINative-specific conventions on top — when the two diverge, the spec wins.

---

## Spec conformance: structure, frontmatter, naming

Before applying the principles, get the shape right. These are the spec's hard rules:

**Folder shape.** A skill is a directory containing `SKILL.md` (required) plus optional `scripts/`, `references/`, `assets/`. Anything else is non-standard. (See [`/CLAUDE.md`](../../../../../CLAUDE.md) → "Repo layout" for the AINative-wide tree.)

**Frontmatter.** `SKILL.md` must open with YAML frontmatter declaring at minimum `name` and `description`, plus a `metadata.version` (the Agent Skills spec's home for a version — a key inside the `metadata:` map, quoted):

```yaml
---
name: kebab-case-name
description: One sentence on what + one sentence on when. Concrete trigger phrases.
metadata:
  version: "1.0.0"
---
```

**`name` rules** (per the spec):

- Lowercase letters, numbers, and hyphens only.
- Max 64 characters.
- Directory name must match the `name` field exactly.

AINative naming conventions (`/CLAUDE.md` → "Naming conventions") are stricter — verb-first, 2–3 tokens — but they're a subset of the spec's rules; following ours satisfies both. The 2–3 token target has a sanctioned exception: metric/report family skills that name a `(verb)-(vendor)-(grain)-(metric)` point (e.g. `analyze-openai-cost-per-subscription-state`, `report-elevenlabs-user-cost`) and the `aurie-web-*` surface family legitimately run 4–6 tokens because each token disambiguates a real sibling. See `/CLAUDE.md` → "Naming conventions" → rule 5 for the carve-out — don't flag these as violations.

**`description` rules:**

- The spec allows up to **1024 characters**, but **Claude.ai limits uploaded skill descriptions to 200 characters**. AINative skills aren't uploaded to Claude.ai today, so we use the longer form for trigger specificity (see principle 7) — but note the limit if a skill ever gets packaged for Claude.ai distribution.
- The description is the only thing the model sees at registry time. It must say *both* what the skill does *and* when to use it. Concrete user phrases beat abstract descriptions.

**`metadata.version` frontmatter** carries the skill's semver. The Agent Skills spec has no top-level `version` field — versions live inside the optional `metadata:` map (an arbitrary string key-value map), so this is spec-conformant, not an AINative-only extension. New skills start at `"1.0.0"` and every change bumps it (patch/minor/major). It also becomes the `:<version>` segment of the `Tools/Slack` attribution footer, so a Slack post or report can be traced to the exact revision. Full rules — the bump table and the footer contract — live in [`/CLAUDE.md`](../../../../../CLAUDE.md) → "Skill versioning"; `update-aura-skill` owns the bump.

**Escape every `$` followed by a digit in the body: write `\$`.** When a skill or command is invoked with arguments, Claude Code replaces `$N` in the SKILL.md body with the N-th argument (0-indexed) before the model reads it, so `$2,287.50` reaches the model as `<third argument>,287.50` and `$0` as the first argument. Code spans and fenced blocks are substituted too, so an awk `$2` or a shell `$1` needs the same escape; `\$` reaches the model as a plain `$`, with or without arguments. Frontmatter and `references/` files are not substituted. `tests/test_skill_dollar_args.py` enforces it ([ENG-1463](https://linear.app/aura-health/issue/ENG-1463)).

**`knowledge:` frontmatter** is an AINative extension on top of the spec — see [`/CLAUDE.md`](../../../../../CLAUDE.md) → "Knowledge manifest". Every AINative skill declares at least `reads: [Guide/Index]`.

---

## 1. Atomic skills, composed workflows

One skill, one job. If a skill does three things well, it's three skills.

Compose higher-level workflows by *calling* atomic skills, not by re-implementing their logic. `data:analyze-experiment` calls `retrieve-knowledge` and `record-knowledge`; it doesn't have its own Notion code.

**Wrong:** a skill called `analyze-and-record-and-post-experiment` that owns the whole pipeline.
**Right:** `analyze-experiment` returns analysis → routes to `record-knowledge` for the write → posts via the messaging skill.

**Test:** Can you describe the skill's job in a single verb-phrase without "and"?

---

## 2. Give the model a role, context, and a cue to slow down

`SKILL.md` is a system prompt. Open with what the skill *is* and what it owns ("Read side of the AI Knowledge access layer…"). Tell the model when it's allowed to act, and when it must stop and plan or ask.

Built-in pause cues that work:

- "Resolve inputs first, then…"
- "Before any side-effect step, show the plan and wait for approval."
- "Confirm with the user before applying."

Models give bad answers more often by jumping to the answer than by lacking knowledge. Force a pause where it matters.

---

## 3. Clarity > cleverness

Use the same word for the same thing. Number multi-step workflows. Show one canonical example per recipe. Prefer tables and bulleted contracts over prose paragraphs.

Models (and humans) skim. Ambiguity costs reliability. If two sentences in the skill could mean the same thing, delete one and pin the other.

---

## 4. Plan before side-effects on multi-step work

Anything that touches more than one system or makes more than one write must have an explicit "build a plan and show it" step before any side-effect step.

`record-knowledge`, `harvest-knowledge`, and `ingest-knowledge` all enforce this. Without it, models drift halfway through multi-step tasks and partial writes leak.

**Test:** If a typo in the input would silently apply 12 wrong writes, the plan step is missing.

---

## 5. Progressive disclosure (lost-in-the-middle defence)

Keep `SKILL.md` under ~500 lines. Push schemas, edge cases, recipe details, and large examples into `references/<topic>.md` files. Link to them from `SKILL.md`.

Long monolithic files lose the middle — both attentionally (the model skips it) and contextually (token pressure). Bonus: the model only pays for what it actually reads.

**Test:** If `SKILL.md` scrolls past your terminal three times, split it.

---

## 6. Isolate scope; declare dependencies

Two layers of isolation are already enforced by this repo (see [`/CLAUDE.md`](../../../../../CLAUDE.md) → "Architecture principles"):

- **Folder isolation:** a skill folder ships everything it needs. No cross-skill reads at runtime.
- **Knowledge isolation:** every skill declares `knowledge.reads` / `knowledge.writes` so the orchestrator pre-fetches only what's needed and access-layer skills can refuse out-of-scope writes.

Add a third layer in your own design: **workflow scope is equally narrow**. If the skill is tempted to also "while we're here, sync X", that's a separate skill.

---

## 7. Trigger specificity in `description`

The `description` field in YAML frontmatter is the only registry — the model matches user intent against it by similarity, not regex. A phrase that appears verbatim in the description fires reliably; a close paraphrase only fires sometimes. So **enumerate, don't summarise**.

**Two hard limits the plugin loader enforces.** A `description` that breaks either is reported as a warning every time the marketplace syncs, and [`scripts/check-skill-description.py`](../../../../../scripts/check-skill-description.py) fails the PR:

- The `description` field is at most 1024 characters.
- No XML-looking tags. Angle-bracket placeholders — `<uid>`, `<name>`, `<userId>` — read as markup to the loader, so write them as `{uid}` / `{name}` instead. Backticks do not exempt them: the loader scans the raw string, not the rendered markdown.

That limit and the enumeration advice below pull against each other, and the tie-break is **trigger phrases stay, prose goes**. Cut mechanism detail, rationale, ticket links, env-var names, output format — the body already carries all of it, and the body is not part of the registry. Dropping a trigger phrase is a routing regression nothing reports; dropping a sentence of explanation costs nothing.

**Cover the surface, not a representative sample:**

- **Synonyms for the artifact** — "card", "ticket", "issue", "task", "row", "page", "doc" — whichever the user might say.
- **Verb variants** — add, create, open, file, make, new, track, log, ask, send, post, submit, draft, raise. Most actions have 4–8 plausible verbs; list the ones a user would actually type.
- **Short forms** — when the surrounding system is obvious, users drop the system name ("create a card" instead of "create a Linear card", "post to ideas" instead of "post to the ideas Slack channel"). Include those.
- **Question forms** — "can you add this to Linear?", "should we file a ticket?". The skill should still fire.

**Aim for ~10–20 concrete phrasings** for any skill whose triggers don't follow a tight, unambiguous pattern. Two or three is too few — the surface is wider than your intuition.

**Pattern hint up front, then enumerate.** When the trigger space is combinatorial (verb × artifact, verb × channel, etc.), lead with the pattern then list concrete examples. The pattern gives the model a generative cue; the examples anchor it. Example from `add-linear-issues`: *"Trigger on any reasonable phrasing that combines an action verb (add, create, open, file, …) with a Linear artifact (Linear card, Linear ticket, Linear issue, …). Concrete examples: '<phrase>', '<phrase>', …"*

**List non-triggers when sibling skills overlap.** "Do NOT use for X (use `<sibling-skill>`)" prevents wrong-skill-fires when a phrase could plausibly route either way.

**Wrong:** "Helps with experiments."
**Right:** "Use when the user says 'analyze experiment X', 'what did we learn from Y', or pastes a Notion URL of a Learnings/Experiments row. Do NOT use for syncing the experiment catalog (use `data:sync-experiments`)."

Vague descriptions = the wrong skill fires. Sparse-but-accurate descriptions = the right skill fires *sometimes*. Only enumeration gets you reliable matching.

---

## 8. Idempotent + re-runnable by default

Running the skill twice should not corrupt state or duplicate writes. If it would, add a guard.

- `data:sync-experiments` re-running on an unchanged catalog reports zero writes.
- `record-knowledge`'s `action: update` exists so a second analysis enriches the same row instead of inserting a duplicate.
- `harvest-knowledge` plans then applies; a re-run on already-applied changes is a no-op.

**An idempotency key on something that can grow must record its size.** When a write is keyed on "entity + start time" and the entity keeps growing (an IP still probing, a conversation still running), store a size marker with the key, such as a count or end time. A re-run compares it and rewrites the entry when it grew. Keyed on the start alone, the grown entity reads as "already applied" and the record stays under-counted: the RTDB incident-log planner did exactly this to an IP that kept probing after the first apply ([ENG-1622](https://linear.app/aura-health/issue/ENG-1622)).

**Changing an idempotency key must still recognise rows written under the old key.** The "already applied" check maps each new item to the key it would have had under the old scheme, in the old key's own shape, and matches old rows on that. Matching old rows with the new key's shape misses them, and the item is written a second time: when the incident-log planner moved from per-IP to per-group keys, an old per-IP row was keyed at a mixed sweep's first attempt of any type, while a per-type sweep starts later, so the new check missed it ([PROD-7822](https://linear.app/aura-health/issue/PROD-7822)).

**Anything sent to an outside service takes its list from the function that decides what gets written.** A whois lookup or another enrichment call reuses the writer's own filter, so it never sends values the writer excludes, such as console traffic or our own sessions ([PROD-7822](https://linear.app/aura-health/issue/PROD-7822)).

**A moving global watermark can't mark per-row work as done.** When writes to different rows are separate requests, "already processed" taken from the latest end time of any row hides other rows' unwritten items after a partial apply. Use a fixed cut-over date for the one historical gap, and per-row state after it ([PROD-7823](https://linear.app/aura-health/issue/PROD-7823)).

**A windowed run can split one logical unit across lines.** So an "extend" step skips items an existing line already holds, and folds that line in, instead of opening a second line for the same unit ([PROD-7825](https://linear.app/aura-health/issue/PROD-7825)).

**When a line stands for a time span, dedupe a new unit by containment, not by its start key.** A windowed run can begin mid-span and see only the tail of a unit already logged, and that tail starts at no recorded key. So treat a unit as covered when it falls inside an existing span for the same actor, whoever wrote that row. A run starting at 07:00Z re-logged the tail of a 06:34–07:29Z sweep as a new line and double-counted its requests, because the "covered up to Ended" fallback only applied to rows the skill had written itself ([PROD-7827](https://linear.app/aura-health/issue/PROD-7827)).

**Test:** "What happens if a user runs this skill twice in a row?" If the answer is "duplicates" or "data corruption", you're missing a guard.

---

## 9. Deterministic-first: scripts for code, LLM for reasoning

If a step is "pull X data, transform Y, write to Z" with no judgment required, it's a Python script in `scripts/` — not an LLM step. Reserve the model for actual reasoning: classification, synthesis, voice, judgment calls.

Determinism beats reasoning variability for everything the agent doesn't need to think about. Faster, cheaper, and reliably reproducible. ([Slack discussion, 2026-04-30](https://auramobile.slack.com/archives/C08P09ZJASC/p1777572574182699))

**Examples in this repo:**

| Skill | Deterministic part (script) | Reasoning part (LLM) |
|---|---|---|
| `data:analyze-experiment` | Mixpanel queries, Slack thread fetch | Synthesise the learning, write the readout |
| `data:sync-experiments` | Diff API catalog vs. Notion DB | Report what changed (almost no LLM) |
| Any Notion-touching skill | `notion_rest.py` REST queries | Decide what to write |

**Test for each LLM step:** is the model actually *judging* something there, or is it just translating data from one shape to another? If the latter, write a script.

---

## 10. Document new external dependencies in the same change

When a skill is the **first consumer** of an external endpoint, dataset, dashboard, or other team-shared resource that isn't already documented in AI Knowledge, document it on the corresponding `Tools/<service>` (or other appropriate) page **as part of the same PR** — not as a follow-up.

This applies most often to **Aura API endpoints** (each new endpoint family or new route gets a subpage under `Tools/Aura API/<family>`), but the same rule covers Mixpanel events the skill emits, Braintrust datasets the skill writes to, Slack channels the skill posts into, GCP log queries it owns, and so on. If the team would benefit from knowing this thing exists and how to use it, it goes in AI Knowledge, not in the skill's `references/` folder and not in personal memory.

**Why.** Undocumented endpoints create latent dependencies: the next skill author can't see the contract, can't audit auth/role/response-shape claims, and rediscovers the resource from scratch. `healthcheck-knowledge` and `search-knowledge` also lose visibility — they can only audit and route against paths that exist in the teamspace.

**How to apply:**

1. Before merging the skill PR, fetch the current state of the relevant `Tools/<service>` page (or other home).
2. If the family already has a subpage (e.g. `Tools/Aura API/Subscriptions`), update it. If not, create a new subpage following the existing shape: *Purpose → Endpoints → Auth → Sample response → Response field reference → Empty / error responses → Where it shows up → Related → Sources*.
3. Update the parent page's intro and Endpoints / inventory section to link the new subpage.
4. Cite the source PR + ticket in `# Sources` with `ingested <YYYY-MM-DD>`.
5. Add the new path to the skill's `knowledge.reads` frontmatter (e.g. `Tools/Aura API/Products`) so the dependency is declared and `healthcheck-knowledge` can audit it.

**Test:** grep your `SKILL.md` and `scripts/` for every external URL, env-var-secured token, channel ID, dataset slug, or event name. For each one, can a teammate find the canonical docs by following a path in `knowledge.reads`? If not, you have a hidden dependency — fix it in this PR.

This rule was codified after `investigate-missing-premium` shipped consuming `GET /products/:provider` without a corresponding `Tools/Aura API/Products` page ([PROD-2001](https://linear.app/aura-health/issue/PROD-2001), AINative [#241](https://github.com/AuraHealth/AINative/pull/241)).

### Corollary: point at the fact, don't restate it

Once a fact lives on its `Tools/<service>` page, that page is its **one canonical home**. When your skill needs to mention it — a project ID, an endpoint, a rate, a plan name — **link to the page instead of copying the value in**.

```markdown
✅  Rates: see `Tools/Deepgram` → Plan & pricing (canonical).
❌  Rates: nova-3 streaming $0.0048/min = $0.288/hr.
```

Inlining is fine where a value has to be inline to be useful (a script needs the real table ID; a prereq line needs the env-var name). But an inlined value is a **copy**, and copies inherit the propagation rule from `/CLAUDE.md` → *Canonical facts: point, don't restate*: **when the fact changes, grep the old value and fix every hit in the same PR.**

```bash
grep -rn --include='*.md' --include='*.py' '<old-value>' . | grep -v '.claude/worktrees'
```

**Why this matters more than it looks.** The Deepgram production project ID was corrected in one skill and in `Tools/Deepgram` on 2026-07-23, but 7 other repo locations kept the superseded value for 4 days — SKILL.md bodies, script comments, and `knowledge.reads` comments. By the time it surfaced, the stale belief had been encoded in *behaviour*: the store emitted a false degraded-vendor note on every quiet day because the code assumed prod was unreachable. Full write-up in `/CLAUDE.md`. A skill that had pointed at the page instead of restating the ID would never have needed touching.

**Test:** for every external-service fact your SKILL.md states as a value, ask whether the reader could instead follow a `knowledge.reads` path to get it. If yes, and the value isn't needed inline to run the skill, make it a pointer.

---

## 10b. Paid inference: state the service tier, and default to the cheap one

If a skill's script **spends** on an LLM — as opposed to reading stored completions or a
cost endpoint — it passes an explicit service tier, and the default is the cheapest one
the work tolerates. On OpenAI that is `flex`, at half the price of standard.

**An omitted `service_tier` bills at standard.** So the expensive choice is the one you
get by writing nothing, and nothing in the response says you took it. That is the whole
argument: this is not an optimisation you add later, it is a default you inherit unless
you write one line.

Almost all of our paid inference is batch-shaped — sweeps, judges, labellers, scorers,
self-critique. Nobody is waiting on any of it, so the latency flex trades away costs
nothing real. Before this rule, `eng:qa-conversation` alone measured ~$837 in a single
day, every dollar of it at twice the necessary rate.

**How to apply:**

1. Resolve the tier through a small bundled helper (`scripts/service_tier.py` in the
   `qa-*` skills), never a literal at the call site. A literal is a copy, and copies stop
   following the default.
2. **Ask the user once per run, with the cost attached** — flex 0.5x, standard 1x, fast
   2x — and pre-select flex. Offer a flag and an env var so an unattended run skips the
   question.
3. **Exempt anything measuring latency**, and make the exemption refuse to default rather
   than quietly inherit. A latency harness silently on the slow tier reports a real number
   for a tier production does not run, which is worse than no number because it is
   quotable.
4. **Degrade loudly.** A refused cheap-tier call must either retry the fallback with a
   visible note or explain the refusal. Left bare it is indistinguishable from a model
   that would not answer, and a whole judged row goes missing.
5. Put the rates, the model eligibility and the refusal behaviour on the `Tools/<service>`
   page and point at it, per §10's corollary. Do not restate multipliers in a skill body.

Codified in [ENG-1247](https://linear.app/aura-health/issue/ENG-1247), which made the two
QA skills explicit; the tier table lives on AI Knowledge `Tools/OpenAI`.

---

## 11. External service access: MCP-first, API token fallback

When a skill talks to an external service (Notion, Linear, Slack, GitHub, Braintrust, Mixpanel, OpenAI, Aura backend, …), the preference order is:

1. **Try the MCP server first.** Lowest-friction path — a new contributor doesn't need every token in `~/.claude/settings.json` populated before any skill works, and MCP handles auth out-of-band.
2. **Fall back to the direct API token** (`NOTION_TOKEN`, `LINEAR_API_KEY`, `BRAINTRUST_API_KEY`, …) when MCP can't answer the question — capped result sets, missing pagination, bulk operations, advanced filters, admin-scoped endpoints, or when MCP isn't reachable (Cowork).
3. **Ask the user only when blocked** — neither path can answer.

**Why.** Token-first by default forced new contributors to provision N personal tokens before the first skill would run. MCP-first lowers the bar: most read paths (Linear MCP, `notion-search`, Slack MCP) work out of the box, and skills only ask for a token when MCP truly can't do the job.

**API-first by necessity.** Some services have no MCP at all (Bugsnag, GCP, Braintrust, 11Labs, Mixpanel, Aura backend, OpenAI/Anthropic/Gemini direct calls). And some MCP servers can't do specific jobs — `notion-search` is capped at 25 results with no cursor, so bulk Notion DB sweeps must use REST. Skills in either bucket lead with the API path and document the rationale up front. The full list lives in [`/CLAUDE.md`](../../../../../CLAUDE.md) → *Tokens are soft requirements* → *API-first by necessity*; keep both in sync when adding a new exempt skill.

**GitHub is a special case: `gh` CLI, not MCP.** GitHub-touching skills (`eng:review-pr`, `eng:bugbot-review`, `eng:rerun-circleci`, `eng:sweep-bugsnag`, `eng:linear-task`, …) shell out to the `gh` CLI, which handles its own auth. This is the repo-standard GitHub access path, not a fallback — so a GitHub skill *without* MCP-first framing is expected, not a violation. The GitHub Connector/MCP is the fallback only where `gh` isn't available (Cowork workspace bash). Details in `/CLAUDE.md` → *API-first by necessity* → *GitHub*.

**Cowork degradation.** `mcp__workspace__bash` inside Cowork doesn't have most MCP connectors. Cowork-runnable skills fall through to the API-token path in that environment — the convention is about *order of preference*, not about disabling the API path. Skills like `shared:add-linear-issues` keep the API script as a working fallback for this reason ([PROD-2216](https://linear.app/aura-health/issue/PROD-2216)).

**How to apply.** In SKILL.md, name both paths in the workflow ("Try Linear MCP first; if unreachable or insufficient, call `scripts/linear_api.py`"). Don't pre-emptively check for the token at skill start — only when MCP has actually failed. The full spec, including the API-first-by-necessity list, is in [`/CLAUDE.md`](../../../../../CLAUDE.md) → "Tokens are soft requirements".

**Test:** if a fresh contributor with zero API tokens runs your skill, does it produce a useful result (even if degraded) before asking for a token? If it hard-fails on a missing token without first attempting MCP, it violates the convention.

This rule was codified in [PROD-2234](https://linear.app/aura-health/issue/PROD-2234) (decision) and [PROD-2236](https://linear.app/aura-health/issue/PROD-2236) (execution), flipping the prior API-first default. Earlier exemplar skills wrote the API-first language explicitly (`shared:add-linear-issues`, `shared:retrieve-knowledge`, `shared:daily-status`); those have been updated to lead with MCP.

---

## 12. Write what the skill does, not what changed

**A SKILL.md is a set of instructions for a reader who has never seen the previous version.** Write it in the present tense, describing current behaviour. Never describe the edit you are making.

```markdown
✅  The profile branch passes `rating_id` and `user_id` to the read-mode fetch.
❌  The profile branch **now also** passes `rating_id` (previously user-only).
❌  **New in this version:** a `sources` flag.
❌  Unlike the old behaviour, neutral ratings are no longer dropped.
```

The tells are all relative-to-a-past-state words: *now*, *now also*, *new*, *newly*, *previously*, *used to*, *no longer*, *instead of before*, *changed to*, *updated to*, *as of this version*, *has since*, *formerly*, *originally*, *historically*, *in the past*, *until recently*, *the old behaviour*. Same rule in `references/`, in script docstrings and comments, and in `knowledge.reads` trailing comments.

**The word list is a heuristic, not the rule.** The rule is the tense; no enumeration covers every way English narrates history, so a clean grep is not a pass. Narration hides especially well in a docstring justifying why some guard exists — "the one repo that forbade squash was dormant, and has since been retired" carries none of the classic tells and is still pure changelog. State what the guard does and why it still applies; the story of what prompted it belongs in the PR body.

**Why.** The reader is a model executing the skill, and it has no previous version to contrast against — so a changelog sentence spends tokens on a comparison it can't make, and worse, it plants a second, wrong behaviour in context. "Now also passes `rating_id`" describes two behaviours and leaves the model to infer which is live. The changelog *is* recorded, in three places that stay accurate for free: the commit message, the PR body, and `git blame`. A sentence in SKILL.md is the one copy nobody updates — a year later "now also" reads as a recent change when it's just how the skill works.

**Test:** read any sentence in your diff with the previous version deleted from history. If it still parses as an instruction, keep it. If it only makes sense as a comparison, rewrite it in the present tense and move the contrast to the PR body.

**Where this comes from.** Nine PRs shipped under [ENG-955](https://linear.app/aura-health/issue/ENG-955) (the `*PROFILE RATED*` rating shape); changelog framing was flagged by Bugbot **six separate times** across those PRs — in skill bodies, a script comment, read-mode docs, a schema reference, and a cadence hold condition. Every fix was a one-line rewrite, which is exactly why it kept recurring: it's cheap to fix and invisible while authoring. Catching it here is cheaper than catching it in review.

---

## 13. An unattended run never ends in silence

**A skill that runs unattended (a routine, cadence, loop or scheduled task) calls [`shared:raise-followup`](../../raise-followup/SKILL.md) on every stop or failure**: a hard stop, a failed or blocked step, a crash, an expired wait and an early end. A card comment or a local report alone is not enough: nobody reads it until something else goes wrong. Give each stop a stable dedupe key (`<skill>:<app>:stop:<stage>`), so a stop that repeats every run stays one post with replies. Make the stop deterministic where you can: a script that prints one `FOLLOWUP stop <stage>: <why>` line before it exits, which the skill turns into one follow-up per line. A wait that expires is a stop too: re-arm it or raise the follow-up, never end the turn on it. Test it by forcing the stop.

**Where this comes from.** The weekly Aurie screens routine ran on 2026-11-16, started its harvests in the background, and let a 30-minute wait expire with no event. The session then ended with nothing posted: no report, no syncs, no follow-up. Nobody knew until the next week. The two runs before it had not happened at all ([ENG-1973](https://linear.app/aura-health/issue/ENG-1973)).

---

## When to author a skill vs a script vs a slash command

| You want… | Use a… |
|---|---|
| Pure deterministic transform with no judgment | **Script** in some skill's `scripts/`, called by the skill |
| Repeated workflow with reasoning steps | **Skill** |
| One-off with a single trigger phrase, no real workflow | **Slash command** (`commands/<name>.md`) — though the skill format is preferred for anything non-trivial because it supports `references/` and progressive disclosure |

---

## Aligning with Anthropic's official guidance

The [Agent Skills specification](https://agentskills.io/specification) is the formal standard; [Anthropic's "Skill authoring best practices"](https://docs.claude.com/en/docs/agents-and-tools/agent-skills/best-practices) is the practical guide; the [`anthropics/skills` repo](https://github.com/anthropics/skills) has reference implementations.

If you use `anthropic-skills:skill-creator` to bootstrap a skill, this doc is compatible — and stricter where AINative has its own conventions (verb-first naming, declared knowledge reads, canonical-source pattern for shared utilities). When the two diverge, the spec wins on shape (folder layout, frontmatter); AINative wins on workflow (PR flow, knowledge manifests).

---

## Recommended `SKILL.md` skeleton

A starting structure that satisfies the spec and the AINative principles. Use it for new skills; existing skills don't need to be retrofitted.

```markdown
---
name: verb-object
description: Does X for Y. Use when the user asks for A, B, or C. Do NOT use for D (use sibling-skill).
metadata:
  version: "1.0.0"
knowledge:
  reads:
    - Guide/Index
---

# Skill title

## Goal
The outcome this skill produces in one sentence.

## Inputs
What the caller provides — user phrases, files, prior skill outputs.

## Workflow
1. Resolve inputs.
2. (For multi-step or side-effect work) Build a plan and show it.
3. Execute.
4. Confirm and summarise.

## Output
The exact shape of what the skill returns.

## Edge cases
- Missing input → ask, don't guess.
- Ambiguous match → present candidates, let the user pick.
- Re-run on already-applied state → no-op (cite the guard).

## References
- See `references/<topic>.md` for schemas, recipes, large examples.
```

---

## Testing your skill before merging

The spec calls these out; they apply equally to AINative:

- **Trigger test.** Type a few candidate user phrases at a Claude session and confirm the right skill fires. If a sibling skill fires first, tighten the description (principle 7).
- **Reference integrity.** Every link from `SKILL.md` to a file in `references/`, `scripts/`, or `assets/` resolves to an actual file on disk.
- **Frontmatter validity.** `name` matches the directory; `description` is non-empty; `knowledge.reads` includes `Guide/Index`.
- **Scripts run cleanly.** Every script under `scripts/` is invokable via `python3 ${CLAUDE_PLUGIN_ROOT}/skills/<skill-name>/scripts/<file>.py` (or the equivalent for non-Python), handles its own edge cases, and returns useful errors.
- **Idempotency probe.** Run the skill twice in a row on the same input. Expected: second run is a no-op or an explicit "already applied" message.

If Anthropic's `skills-ref` validator (see [Creating custom skills](https://claude.com/docs/skills/how-to)) is available locally, run it too — it catches schema issues the eyeball test misses.

---

## Security

- **No hardcoded secrets in skill files.** API keys, tokens, and passwords belong in `~/.claude/settings.json` under the `env` block (see [`/CLAUDE.md`](../../../../../CLAUDE.md) → "Secrets and environment"), never in `SKILL.md`, scripts, references, or assets. The whole skill folder is checked into git and visible to anyone who installs the plugin.
- **Review before enabling.** When installing a third-party plugin (or a skill someone else authored), read `SKILL.md` and any `scripts/` before running it. Skills can run shell commands and call external APIs.
- **Cowork sandbox caveat.** `mcp__workspace__bash` strips host env (PROD-1305), so secrets in `~/.claude/settings.json` aren't reachable inside Cowork. Skills that need authenticated calls there must degrade gracefully or use the per-session export workaround in `/CLAUDE.md` → "Known Cowork constraints".

---

## Cross-platform: UTF-8 stdout for Python scripts

Every Python script under `scripts/` that prints to stdout must force UTF-8 encoding at the top of `main()`:

```python
def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    # ... rest of main
```

**Why.** Python's default stdout encoding on Windows is `cp1252`. Skill scripts routinely emit non-ASCII — Markdown arrows (`→`), em-dashes (`—`), curly quotes, emoji in headers — and any of those raise `UnicodeEncodeError` at `print()` time on a Windows console. Originally fixed per-script in [#219](https://github.com/AuraHealth/AINative/pull/219); the right place is at the source, not in every caller's environment (`PYTHONIOENCODING=utf-8` workarounds).

**How to apply.** The first two statements inside `main()` — before argparse, before any output. The `hasattr` guard keeps the call safe on Python <3.7 and on streams that don't expose `reconfigure` (test capture, some piped wrappers).

**Skip when.** The script is library-only (imported, never run as `__main__`) and never prints — those are rare. If you're unsure, add the guard; it's a one-line no-op on macOS/Linux.

## Cross-platform: canonical time zone names

Use canonical IANA zone names (`America/New_York`, `America/Los_Angeles`), and map any legacy `US/*` name an API returns before handing it to `ZoneInfo`. Never assume the legacy links exist: GitHub's `ubuntu-26.04` runners, and Debian or Ubuntu `tzdata` without `tzdata-legacy`, have no `US/*` names, so `ZoneInfo("US/Pacific")` raises. When this shipped, every AINative PR's tests failed and `mixpanel_api.py` silently dropped daylight-saving time ([ENG-1630](https://linear.app/aura-health/issue/ENG-1630), [#1349](https://github.com/AuraHealth/AINative/pull/1349)).

---

## Authoring PR / commit / Notion / Slack bodies: write to a file, don't nest a heredoc

Never build a multi-line body with a heredoc **nested inside command substitution** — `gh pr create --body "$(cat <<'EOF' … EOF)"` — or inside process substitution — `--body-file <(cat <<'EOF' … EOF)`. Both mis-parse when the body contains an apostrophe, a backtick, or an em-dash, aborting with `unexpected EOF while looking for matching `''`. This is a recurring failure ("the heredoc apostrophe curse") documented in `/CLAUDE.md` → PR workflow → "PR body sourcing".

**How to apply.** Write the body to a temp file first, then pass it by path:

```bash
cat > /tmp/pr-body-<slug>.md <<'EOF'
## Summary
…body with 'apostrophes', `backticks`, and — em-dashes — all safe here…
EOF
gh pr create --draft --title "…" --body-file /tmp/pr-body-<slug>.md
# commits: git commit -F /tmp/commit-msg.txt   (never -m with embedded newlines/non-ASCII)
```

A plain `cat > file <<'EOF'` redirect is safe because the heredoc isn't nested inside `$()`/`<()`; the Write tool is equally fine. Short, ASCII-only commit subjects may still use `-m`. `update-aura-skill`'s Step 3 audit greps for the nested-heredoc form and flags it.

---

## Repo-state preflight: confirm branch + worktree before reading or asserting on source

Before a skill reads a repo's source and draws a conclusion from it ("LiveKit is on `main`", "this lint rule is violated", "the fix isn't there yet"), confirm *which* checkout and *which* branch it's actually looking at. Two failure modes bite repeatedly:

- **Wrong branch.** Reading a file from a stacked/feature branch and concluding it reflects `main` — or the reverse. A file's content is only meaningful once you know the branch it came from.
- **Silent wrong cwd.** In a session with several worktrees under `.claude/worktrees/`, the persistent shell cwd can be a *different* checkout than the one you think — a bare `git status` / `grep` / `sed` then reports another branch's files, and a commit/push can land on the wrong branch (worst case, `main`).

**How to apply.**

- Skills that own their checkout should isolate it — `EnterWorktree`, or the [`code-investigator`](../../../../eng/agents/code-investigator.md) agent for read-only investigation (it creates a fresh worktree off `origin/main`, so callers never read the user's dirty/stale working tree). Route source-reading investigations through it rather than reading the user's checkout in place.
- For any direct repo access, run a one-line preflight and confirm it matches the intended target before acting:

  ```bash
  git -C "$WT" rev-parse --abbrev-ref HEAD   # branch
  git -C "$WT" rev-parse --show-toplevel      # which checkout
  git -C "$WT" status --short                 # dirty state
  ```

- Address git/grep/sed at an explicit path (`git -C "$WT" …`, absolute paths) rather than trusting the implicit cwd — a bare command in a multi-worktree session is the trap above. Never commit or push from a bare `git` command when more than one worktree exists.

---

## Bugbot review: wait for the check-run, not for a quiet window

Bugbot (cursor-bot) posts its **PR Summary** issue-comment ~1 minute *before* the inline review comments — and the inline set can land minutes after that. Marking a PR ready as soon as the summary lands strands it with unaddressed findings. On a **re-run**, Bugbot **edits the summary in place** (the comment count does *not* change) and posts a fresh set of inline comments.

A Bugbot pass is clean only when **all three** hold:

1. the latest cursor/bugbot summary references the PR's **actual head commit SHA** — compare the `for commit <sha>` in the summary against `gh pr view --json headRefOid`, not just any SHA in the body;
2. the `Cursor Bugbot` check-run on that head has reached **`status == completed`**; and
3. no Bugbot inline review comment's **own footer** SHA (`Reviewed by Cursor Bugbot for commit <sha>`) equals the head SHA.

**Gate on the check-run's `status`, never on its `conclusion`.** No-findings runs have been observed with `conclusion` both `neutral` and `success`, so a `conclusion == "success"` test rejects clean runs while any `conclusion != null` test can pass before the findings post. `status` is the only field that answers *is it done*. Read it from the check-runs API (`GET /repos/{owner}/{repo}/commits/{sha}/check-runs`) — never by parsing `gh pr checks` output, whose rows are TAB-separated (`Cursor Bugbot<TAB>pending`), so whitespace-splitting yields `Bugbot` rather than a status.

**Why the third condition is a footer test and not `line != null`.** When a finding is fixed but its line still exists, GitHub keeps the old inline comment visible and re-anchors its `commit_id` to the new head, so `line != null` stays true for a finding the current run does not raise. The comment's own footer SHA is the signal that separates a genuine open finding from a carried-forward anchor; the latter needs no action.

**Why a quiet interval is not a substitute.** A hold after the summary measures elapsed time, not progress: the findings arrive when the run finishes, which can be minutes later. On [#859](https://github.com/AuraHealth/AINative/pull/859) (2026-08-14) the summary cited head `383aab7`, a 100s window returned **zero** findings while the check-run sat at `in_progress`, and **three Medium findings appeared at once** the moment it completed. Any recipe that reads an empty window as an answer marks that PR ready with all three unaddressed. The check-run is the only signal that reports whether the run is over, which is why it is a condition and a timer is not ([PROD-4540](https://linear.app/aura-health/issue/PROD-4540)).

Never gate readiness on the summary alone, on issue-comment counts, or on a quiet interval. The Bugbot step in the skill body has all three `gh api` checks.

---

## Quick checklist before opening the PR

**Spec conformance:**

- [ ] Folder is `<plugin>/skills/<skill-name>/SKILL.md` (+ optional `scripts/`, `references/`, `assets/`); no other top-level files.
- [ ] Frontmatter `name` matches the directory name exactly; lowercase + hyphens; ≤64 chars.
- [ ] Frontmatter `description` says what *and* when; concrete trigger phrases.
- [ ] Frontmatter `description` is at most 1024 characters and contains no `<angle-bracket>` placeholders — `python3 scripts/check-skill-description.py` passes.
- [ ] Frontmatter has `metadata.version` (order: `name` → `description` → `metadata` → `knowledge`) — `"1.0.0"` for a new skill, or a semver bump for an edit; any footer literal matches it.
- [ ] `knowledge.reads` includes `Guide/Index` (AINative baseline).
- [ ] No unescaped `$<digit>` in the body — amounts and shell positionals are written `\$` (`python3 -m unittest tests.test_skill_dollar_args`).

**AINative principles:**

- [ ] Skill name is verb-first kebab-case, 2–3 tokens (see `/CLAUDE.md` → "Naming conventions").
- [ ] `description` includes at least one non-trigger when sibling skills could overlap.
- [ ] `SKILL.md` is under 500 lines; overflow is in `references/`.
- [ ] Every multi-step or multi-write workflow has an explicit plan-and-confirm step before side-effects.
- [ ] Each LLM step earns its tokens with reasoning; deterministic parts are in `scripts/`.
- [ ] Re-running the skill twice in a row is safe (idempotent or guarded).
- [ ] No cross-skill file reads (folder isolation).
- [ ] Any step that reads a repo and asserts on its source confirms branch + worktree first (or routes through `code-investigator`) — see "Repo-state preflight".
- [ ] No changelog framing anywhere in the diff — no *now*, *now also*, *new in this version*, *previously*, *no longer*, *used to*, *has since*, *formerly*, *originally*; every sentence describes current behaviour in the present tense, script docstrings included (see "Write what the skill does, not what changed").
- [ ] External-service facts (project/table/channel IDs, endpoints, rates) point at their `Tools/<service>` page rather than restating the value; any value that *must* be inline is accounted for, and if this PR changes such a fact, `grep -rn '<old-value>'` comes back clean (see "Corollary: point at the fact, don't restate it").
- [ ] No hardcoded secrets anywhere in the skill folder.
- [ ] Every Python script under `scripts/` that prints to stdout calls `sys.stdout.reconfigure(encoding="utf-8")` at the top of `main()` (see "Cross-platform: UTF-8 stdout for Python scripts").
- [ ] If the skill can run unattended, every stop or failure (hard stop, failed or blocked step, crash, expired wait, early end) raises `shared:raise-followup` with a stable key, and a test forces one (see "An unattended run never ends in silence").
- [ ] No PR/commit/Notion/Slack body is built with a command-substitution heredoc (`--body "$(cat <<…)"` / `--body-file <(cat <<…)`) — write to a file + `--body-file`/`-F` (see "Authoring PR / commit / Notion / Slack bodies").

**Test:**

- [ ] Trigger phrases fire this skill (and not a sibling).
- [ ] Every reference and script path resolves.
- [ ] A second run on the same input is a no-op.

---

## References (upstream canonicals)

- [Skills overview](https://claude.com/docs/skills/overview) — Anthropic's introduction to skills.
- [Agent Skills specification](https://agentskills.io/specification) — formal open standard for `SKILL.md`, frontmatter, folder structure, validation.
- [Creating custom skills](https://claude.com/docs/skills/how-to) — practical guide: scaffolding, packaging, uploading, testing.
- [Skill authoring best practices](https://docs.claude.com/en/docs/agents-and-tools/agent-skills/best-practices) — Anthropic's best-practice guidance.
- [`anthropics/skills`](https://github.com/anthropics/skills) — example skills and reference SDK.
- [Equipping agents for the real world with Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills) — engineering blog on design rationale.
