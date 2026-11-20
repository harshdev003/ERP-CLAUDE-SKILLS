# The cleanup step

Read by `features-loop` at step 3e of every `run`. The learn step files a cleanup card for each
loop experiment it decides; this step removes that experiment's code. The card is filed first,
then worked on, and the loop's own background agent is the one that works on it.

**No approval is needed at any point before the PR.** Filing the card, removing the code,
pushing and opening the PR are all the loop's job; the agent runs them end to end and never
stops to ask. The human gate is the review and merge of the ready PR.

## The card

The learn step files it with `eng:sweep-ended-experiments key=<that row's experiment>` — one
key, never a full sweep — and links it on the row with `learn_idea.py record <ref>
--cleanup-issue <url>`. The card sits directly under the current cycle's PROD **Quality / Tech
Debt [Cycle N]** card, never under the sweep's batch parent, which the loop cancels. Details: [`learn-step.md`](learn-step.md).

**The loop does not route the card to Cursor.** `eng:sweep-ended-experiments` Step 9 delegates a
ready card to an execution agent (`delegate: Cursor`); when the loop files the card it skips that
call, because the loop's own agent implements it. A card already delegated elsewhere, or one a
person has picked up (assignee changed, or In Progress with a PR from someone else), is left to
them and only reported in the tick summary.

## When the tick dispatches

A loop row is a candidate when it has a `Cleanup issue` link, the card is still open, and no
removal PR exists for its key: no open or merged PR in the app repo whose title starts
`Remove experiment: <key>`. Dispatch **one** removal agent at a time:

- **In flight:** a removal agent this session dispatched is still running → dispatch nothing.
- **Unfinished:** a draft removal PR for the key exists but is not ready → resume it (the
  agent picks up the branch and carries on from the PR's state).
- **After a restart** the session's agents are gone; the card and the PR are the state. A card
  In Progress with no PR and no live agent is resumed from scratch.

Only the loop's own experiments: the key always comes from a row in the loop's ideas database.
Every other experiment in the app — ended or not — is out of scope, and the agent never edits it.

## What the agent does

Give it everything in the prompt: the card id and URL, the key, the winning variant, the idea
(`IDEA-N`, thread URL), the build PR that added the experiment, and the app repo. It follows the
card's own checklist and implementation process:

1. Move the card to **In Progress** and post a short plan comment on it: every reference from
   `git grep <key>`, plus what the experiment's build PR added only for it.
2. Work in its own worktree of the app repo (`<repo>/.claude/worktrees/<card>-remove-<key>`).
3. Remove only that experiment and what its build PR added for it. Keep the winning path's
   code, protect every sibling experiment that shares a code path (its behaviour must not
   change), and remove nothing else.
4. Confirm `git grep <key>` returns zero hits; run lint, the simulator build and the unit
   tests, and gate on their exit codes.
5. Push the branch. The app repo's pre-push hook builds with `xcodebuild` and exits 74 when the
   Swift packages are not resolved in the worktree; run
   `xcodebuild -resolvePackageDependencies -project AuraConsumerApp.xcodeproj -scheme AuraConsumerApp`
   there and push again. Never `--no-verify`. If the host blocks the push itself, the agent
   reports that to the orchestrator, which surfaces it to the operator; it never hands the push
   to another session.
6. Open a draft PR titled `Remove experiment: <key> (<winner> won) [<CARD>]`, with an
   **Experiment result** section from the live API row, links to the card, its Quality / Tech
   Debt parent and the build PR, and a test plan. Link the PR on the card.
7. Run `bugbot run` as its own comment and one adversarial review (the card's three
   questions: zero hits, no shared sibling path, no audience newly exposed). Fix every
   finding and repeat until the current head is clean with no unresolved threads.
8. `gh pr ready`, request **no** reviewer, and **never merge** — the merge is the human gate.
9. Reply in the idea's Slack thread with the card and the PR link.

**Ending the experiment on dev is not the loop's job.** Setting the key in dev Firebase or on
the dev experiment service belongs to the experiment owners' workflow (Yawar); the PR lists it
as a manual step.

## After the merge

The card follows the normal release flow; the loop does not move it. The tick summary lists the
removal PR while it waits for review. Once it merges, remove the removal worktree and its local
branch (`git worktree remove`, `git branch -D`).

## Worked example

IDEA-6 `paywallArchetypeHeadline` lost on 2026-10-10. Card [ENGMOB-2996](https://linear.app/aura-health/issue/ENGMOB-2996)
(batch parent PROD-7410) → removal PR [AuraConsumerApp #1835](https://github.com/AuraHealth/AuraConsumerApp/pull/1835):
four files deleted, sixteen edited, `aiPriceStrikethrough` and `aiPriceIncrease3` unchanged, zero
hits, tests and Bugbot clean, dev Firebase left as a manual step.

IDEA-31 `valuePropsExplicitCTA` ended inconclusive on 2026-10-20 (b kept). Card
[ENGMOB-3020](https://linear.app/aura-health/issue/ENGMOB-3020) under PROD-7380 (Quality / Tech
Debt [Cycle 49]), batch parent cancelled → removal PR
[AuraConsumerApp #1888](https://github.com/AuraHealth/AuraConsumerApp/pull/1888): three files
deleted, `onbSelfCommitment` unchanged, Swift packages resolved before the push, Bugbot and CI
clean, merged the same evening.
