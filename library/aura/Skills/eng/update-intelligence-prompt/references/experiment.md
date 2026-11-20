# Mode C — ship a prompt as an A/B experiment arm

Read by [`update-intelligence-prompt`](../SKILL.md) when the operator asks to release a
prompt **as an experiment** ("release as an A/B", "ship as an experiment", "AB test this
prompt"). Mode A replaces the production prompt for everyone; Mode C adds the candidate
as an arm next to it and leaves production untouched, so the two run side by side.

Reference implementation: IntelligenceService [#555](https://github.com/AuraHealth/IntelligenceService/pull/555)
(`coachPrompterWarmth`, [PROD-6888](https://linear.app/aura-health/issue/PROD-6888)), on the
per-arm wiring from [#540](https://github.com/AuraHealth/IntelligenceService/pull/540).

## How IntelligenceService runs an arm

`Prompter.buildPrompt()` reads `config.prompt.<key>.experiments`, assigns the user through
ExperimentService (`POST /experiments/assign/:userId`) and merges the assigned arm's object
**over** the base config. An arm may override any key except `name`: `filename` swaps the whole
prompt body, `model` / `reasoningEffort` / `temperature` swap the params.

**An arm never overrides the prompter's `name`.** `dialogue.count` filters on the stored prompt
name, so replies stored under a new name drop out of the Limited Free counter restore and the QP
unlock gate. When a new stored name is unavoidable (a new prompter, a rename, a merge), the same
IntelligenceService PR adds it to the family list (e.g. `conversation.quickProcessingTypes`) and to
`STORED_PROMPT_NAMES` in `test/unit/models/dialogue.count.spec.js`, and a retired name moves to
`prompt.<key>.legacyNames`. QP Sentinel's replies were stored as `quick-processing-sentinel-prompter`
and never counted (IS [#757](https://github.com/AuraHealth/IntelligenceService/pull/757),
[ENGBACK-2083](https://linear.app/aura-health/issue/ENGBACK-2083)).

**A new stored name has to reach AINative too.** AINative's transcript readers keep their own list
of the names whose replies are conversation turns, and a name missing from it reads as 0 turns.
Once the IntelligenceService PR merges, run the check from an AINative checkout (not the plugin
install):

```bash
python3 scripts/refresh-is-prompter-names.py --check   # exit 1 = drift
```

On drift, run it without `--check` to refresh the snapshot, then
`python3 -m unittest tests.test_is_prompter_names`; the failure names every list the name still
needs. Those changes go in an AINative PR of their own.

- **Arm `b` is the control** and has no entry: it runs the base config, so the base block and
  base `.txt` stay exactly as they are.
- **Arm `a` (and `c`, `d`)** carries only what differs, usually `{filename: '<key>.<experimentName>.a.txt'}`.
- **Every failure serves the base prompt.** An unassigned user, an experiment that does not
  exist yet, an ExperimentService outage, or a missing arm file all fall back to control. So
  the IS PR is safe to merge **before** the experiment exists — nobody gets the arm until it
  is created.
- `test/unit/config.promptFilenames.spec.js` fails the build on any `filename`, base or arm,
  that resolves to no file. Run `NODE_ENV=test npx jest test/unit/config.promptFilenames.spec.js` before
  pushing.
- The dialogue's `prompt` JSONB records both the assigned arm and the body that ran.

## C1 — Name the experiment and arms

- **Experiment name:** camelCase, `<key>Prompter<Change>` (e.g. `coachPrompterWarmth`,
  `coachPrompterGuardrails`). Confirm with the operator; it becomes the Mixpanel people
  property `Experiment: <name>`.
- **Arms:** `ab` by default (`a` = candidate, `b` = control). One extra arm per extra
  candidate.
- **Refuse an existing experiment block on the same prompter** unless the operator says to
  replace it — two live experiments on one prompter merge in key order and confound the readout.
  Check `git grep -n "experiments:" src/config.js` in the worktree.

## C2 — Fetch the candidate (Mode A Steps 1–3)

Run Steps 1–3 unchanged: fetch the Braintrust prompt over REST, map it to the prompter, make
the worktree. Then **diff the candidate's model and params against the base config block**.
Anything that differs goes into the arm object, not the base block. When the candidate is
the production prompt with one section rewritten, the arm is usually `filename` alone.

## C3 — Write the arm

1. Write the candidate body to `src/prompters/<key>.<experimentName>.<arm>.txt`, applying
   Step 4's rules (strip `{{metadata.contextInstructions}}` and its `---` separator; camelCase
   output keys).
2. **Diff the arm file against the base `.txt`** and show the operator the hunks. The diff must
   match what the experiment claims to change ("only the Guardrails section"). An unrelated
   hunk means the candidate was branched from a stale production copy — stop and ask.
3. Add the block at the top of the prompter's config object:

   ```js
   coach: {
     experiments: {
       // 'b' is the control and runs the base config below.
       <experimentName>: {
         a: {filename: '<key>.<experimentName>.a.txt'},
       },
     },
     key: 'coach',
     ...
   ```

4. Run the Step 5 validator on **the merged arm config** (base + arm), not only the base.
5. Do **not** touch the base `.txt` or base params.

## C4 — Ship (Step 6)

Same lint → commit → PR as Step 6, staging the arm file and `src/config.js`. Commit:
`feat(<key>): A/B test <one-line change> [<TICKET>]`. In the PR body, list each arm with its
Braintrust prompt id and say `b` is control. No direct-push path for Mode C.

## C5 — Create the experiment (after the merge)

The repo convention is to **ask Viktor** (`U0AV303B4VB`) in `#support-bot` rather than emit the
`@AuraBot create experiment` command — see `product:ship-idea` → "The chain" for why. Draft
the ask and **stop for the operator's explicit yes** before posting — this ping is what
creates a production experiment:

- `<@U0AV303B4VB>` and the experiment name, `variants ab`, `targetApp ai` for Aurie (omit for
  Aura), no minimum version unless the prompt depends on a client release or this is a
  new-users holdout (below)
- notes spelling out each arm and naming `b` as control, plus the IS PR and Linear links

Once approved, post it by composing **`shared:post-to-slack`** — never a raw Slack call — to `#support-bot`
(`C02FQE15YGP`, the channel `product:ship-idea` posts its requests to; the Aura AI bot is a
member) with `--footer eng:update-intelligence-prompt`. Record the message permalink on the
Linear card.

Confirm by **name**, not by listing: `GET /experiments/<experimentName>` through
`eng:call-aura-api` (authenticated with `AURA_API_TOKEN`). `404` means not created yet —
wait; never `POST /experiments/assign`, which assigns. The record's `active` must be the
split (`ab`), not a single pinned arm.

## C5b — New-users holdout: ship to everyone, hold the old prompt back for new users

Use when the operator wants the new prompt live for every existing user **and** an A/B on new
users only ("release to all old users, and do an experiment for new users"). It inverts C3:
the **new** prompt becomes the base, and arm `a` carries the **previous** prompt.

- **Base = the new prompt**, shipped as Mode A (Steps 4–7 run, including the Braintrust
  Production sync, since Production now advertises what most users run).
- **Arm `a` = the previous production prompt, as is**, at
  `src/prompters/<key>.<experimentName>.a.txt`. `b` stays the control and runs the base, so it
  is the new prompt here.
- **Name the experiment after what arm `a` keeps**, e.g. `coachPrompterNoEmojiRule` (arm `a`
  has no emoji rule), `guardrailsHandlerPrompterAbusePath`.
- **Eligibility is the minimum version only:** the next Aurie release, with no other filter.
  ExperimentService compares it with the version the user **joined** on (`aiJoinedVersion`,
  written once at sign-up), so only users who install on that release or later are split;
  existing users who update keep the base. Splitting everyone who *runs* the release would
  need an ExperimentService change.
- **Create the experiment before the release ships** (after the IS merge, through C5). Until
  it exists every user falls back to the base, so new users who join first are never split.
- **After the release deploys:** run Step 7 for **both** Braintrust prompts, Production and
  Development, from the candidate's whole `prompt` object, so both carry the new base. Then hand
  the operator three activity comments to paste (7d: the API refuses keys), Production's in this
  order:
  1. **Production, the release.** The [7d ITERATION template](post-merge.md#7d--add-an-activity-comment-manual):
     released to all users today, then what changed in the prompt.
  2. **Production, the experiment.** A separate comment:

     ```
     A/B TEST - <IS version> - <DD/MM/YYYY>

     Experiment <experimentName> starts with the next Aurie release (<minVersion>).
     - b (control): this prompt. All existing users, and 50% of new users.
     - a: the previous Production prompt (<what it lacks>). 50% of new users.
     - New users = those who join on Aurie <minVersion> or later. Until it ships, everyone gets b.
     Runs <duration>, ends ~<date> with <arm> (<who decided>); then arm a is removed.
     Viktor's thread: <permalink>
     ```

  3. **Development.** The ITERATION template again: synced to `<candidate prompt name>`, released
     to all users today, same text as Production, then what changed.

  Leave the candidate prompt's comment optional ("shipped to Production in IS v<version>").

## C6 — What does not run

- **Step 7 is skipped.** Braintrust Production must keep advertising what most users run —
  the control. Promoting the candidate now would make the registry lie for the duration
  of the test. Add an activity comment on the **candidate** prompt instead ("shipped as arm
  `a` of `<experimentName>`, IS v<version>").
- **Step 8** may run against the arm only if the QA user is pinned to it
  (`eng:set-user-experiment`); otherwise say which arm was exercised.

## C7 — End the experiment (promote the winner or revert, then remove the arm)

Run when the operator says the experiment has ended and names the winner ("`<experimentName>`
ended, arm a won — promote it", "control won, remove the experiment"). Both outcomes end in the
same place: **no `experiments` block, no arm file, one base prompt.** They differ only in which
body becomes the base.

**How ending works in Intelligence Service.** Viktor ends the experiment in ExperimentService
(the same `#support-bot` path as C5), setting `active` to the single winning arm. The
`ExperimentEnded` listener then **reconciles every user's assignment to that arm**, so from that
moment every coach turn already runs the winner through the `experiments` block. The code change
below is clean-up, plus a promotion when the arm won. It never decides who gets what.

**Routing.** An end-of-experiment request often arrives with the experiment's Linear card or the
arm's Braintrust link. Those must not route it to Mode A: Mode A rewrites the base `.txt` and
leaves the `experiments` block in place, so users keep splitting after the test should have ended.

### C7.1 — Confirm the experiment ended, and with which arm

```bash
curl -A "AuraClaude/$(printf %s "${AINATIVE_UA_WHO:-$(git config --global user.email || git config --global user.name || id -un)}" | sed -E 's/@.*//; s/^[0-9]+\+//; s/[^A-Za-z0-9._-]+/-/g; s/^[-.]+|[-.]+$//g' | grep . || echo unknown)/eng:update-intelligence-prompt (session ${AINATIVE_SESSION_NAME:-adhoc})" -sS "https://services.aurahealth.io/experiments/<experimentName>" \
  -H "Authorization: Bearer $AURA_API_TOKEN"
```

- `active` must be **one letter**, and it must match the winner the operator named. A split
  (`ab`) means the test is still live: **stop**. Removing the block now would switch every
  user to the base body mid-test, and the readout would lose its arm-a data. Proceed only if
  the operator explicitly confirms ending it early. Even then, ask Viktor to end it first, so
  assignments reconcile before the code moves.
- If `active` disagrees with the named winner (`b` rolled out, operator says a won), **stop and
  ask**. Shipping the other body would contradict what every user is already running.
- Record `endedAt`, `active` and the readout link (Linear / Mixpanel) for the PR and the comments.

### C7.2 — Show the plan, then branch on the winner

Print the plan before any write: the winner, the clean-up card this step will file, which files
change, whether Braintrust moves, and the follow-ups. Wait for a yes.

**Then file the clean-up card.** It is its own PR and release, and by now the experiment's card
is usually Done, so an untracked PR is the default outcome unless this step files one. Create it
with `shared:add-linear-issues`:

- **Parent:** the experiment card's parent (the cycle's prompt-improvements card). Use the
  experiment card itself only when it has no parent.
- **Title:** the outcome. `Promote <experimentName> arm a and remove the experiment` when the
  arm won, or `Remove the <experimentName> experiment (control won)`.
- **Body:** `endedAt`, `active` and the readout link from C7.1, and a link to the experiment card.
- **Related to** the experiment card, and started from the start, in the parent team's own
  started state. PROD names it `Eng In Progress`, and a state name taken from another team is
  rejected, which leaves the PR without a card.

This card is the `<TICKET>` in the branch, the commits and the PR. Incident: the
coachPrompterGuardrails promotion (IS #745, v1.44.6) shipped with no card, and PROD-7995 had to
be filed afterwards ([ENG-1681](https://linear.app/aura-health/issue/ENG-1681)).

Then resolve `$IS_ROOT` and create the worktree with Step 3's resolution block (`$IS_DIR` =
`$IS_ROOT/.claude/worktrees/<ticket-id>-<slug>` off fresh `origin/master`). Every file change below
happens there, never in the shared checkout.

**Control won (`b`)**: revert. Production already runs the base body.
1. Delete `src/prompters/<key>.<experimentName>.<arm>.txt` for every arm, and remove the
   `<experimentName>` entry from `config.prompt.<key>.experiments`. Remove the whole
   `experiments` key if it is now empty. Reference: IS [#564](https://github.com/AuraHealth/IntelligenceService/pull/564).
2. Base `.txt`, base params and Braintrust Production stay as they are. **Skip Step 7.**
3. Commit `chore(<key>): remove the <experimentName> experiment [<TICKET>]`. Say in the body
   that control won and that assignments were already reconciled to `b`.

**Arm won (`a`)**: promote, then remove.
1. **Promote the arm's body into the base**, from the file that actually ran rather than a
   fresh Braintrust fetch: copy `src/prompters/<key>.<experimentName>.a.txt` over
   `src/prompters/<key>.txt`. Then check the arm's Braintrust prompt still matches it (Step 2
   fetch, Step 4 stripping, then `diff`). If they differ, someone edited the candidate after
   release: stop and ask which one wins.
2. Move any arm-only params (`model`, `reasoningEffort`, …) from the arm object into the base
   block, and run the Step 5 validator on the result.
3. Delete the arm file(s) and the `experiments` entry, as in the control case.
4. Commit `feat(<key>): promote the <experimentName> arm a prompt [<TICKET>]`.
5. **Run Step 7 after the merge**: Braintrust Production now takes the arm's text and
   options. This is the one place Mode C syncs Production. Sync the **Development** prompt to
   the same body, in the same post-merge pass and never earlier: before the merge,
   `origin/master` still holds the control. Otherwise the next Mode A release, which reads Dev,
   would quietly revert the winner. For each
   prompt, follow [7c](post-merge.md#7c--commit-a-new-production-prompt-version-via-rest-api):
   `POST` that prompt's **whole** `prompt` object and `tool_functions`. Build the `prompt`
   with `python3 ${CLAUDE_PLUGIN_ROOT}/skills/update-intelligence-prompt/scripts/bt_scaffold.py
   set-body --body-file <body> < prompt.json`, never a raw
   `messages[0]` replace. `<body>` is the merged file, not the worktree copy (Step 6 removes the
   worktree after the merge): `git -C "$IS_ROOT" fetch origin && git -C "$IS_ROOT" show
   origin/master:src/prompters/<key>.txt > <body>`. On an appended-shape prompt the
   `{{metadata.contextInstructions}}` scaffold lives inside `messages[0]`, and `set-body`
   keeps whichever shape the prompt already has. Every other message, tool and option stays
   as it is. Then re-fetch, and diff the whole `prompt` object and
   `tool_functions` against what was sent.

**Either way:** when the removed arm stored its replies under a name nothing else uses now, keep
that name in `prompt.<key>.legacyNames`, so `dialogue.count` still counts them. Then
`NODE_ENV=test npx jest test/unit/config.promptFilenames.spec.js test/unit/models/dialogue.count.spec.js`
and lint pass, then Step 6 (PR only).

### C7.3 — Release, then verify the path production takes

After the release deploys (`eng:release-version` → `verify` says serving):
- llmCall rows for the prompter since the deploy carry **only** the base file:
  BigQuery `aura-main.llm.llm_call` where `prompterName = <config name>` and `createdAt >= <deploy time>`
  (`eng:query-bigquery`, `GCP_KEY`), where `<config name>` is
  the block's `name` (`coach-prompter`), not the key or file stem (`coach`). **Require at least
  one row**: zero rows means the filter is wrong, not that the check passed. Then every
  `promptFilename` must be `<key>.txt`.
- New guardrail alerts no longer carry a `*Prompt:*` line.
- If the arm won, one stored response contains a sentence unique to the arm, which proves the
  promoted text is what ships.

### C7.4 — Close the loop

- **Braintrust activity comments** (manual, Step 7d): on Production ("experiment ended,
  `<winner>` kept, IS v<version>") and on the candidate ("won, promoted in IS v<version>" or
  "lost, removed in IS v<version>").
- **Prompters with a copy of the changed section** (e.g. the Guardrails section lives in
  `coach.txt`, `quickProcessing.txt` and `quickProcessingSentinel.txt`). When the arm won, list
  them for the operator as a follow-up. They are not part of this experiment's cleanup.
- Move the clean-up card from C7.2 to **Done** once C7.3 passes and, when the arm won,
  Braintrust Development and Production are synced. When control won there is no sync, so
  C7.3 alone closes it. Put the PR and the release tag on it.
- Add the outcome and the clean-up card to the experiment's Linear card, and close it if it is
  still open.
