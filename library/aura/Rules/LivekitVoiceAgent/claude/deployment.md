---
description: Deployment and release
globs:
alwaysApply: true
---

# Deployment & release

The agent is a **LiveKit Cloud Agent** (not GKE), deployed from this repo by CircleCI on `v*` tags.
The live agent is **`aurie-voice-agent`** (`CA_6vDm9dFvw6FN`); the legacy **`lk-voice-agent`**
(`CA_NQ9eyfBoKUjp`) is retired (do not deploy to it — see ENG-774).

## Topology

- Dispatch is keyed on the worker's **`agent_name`** (`AGENT_NAME`, default `aurie-voice-agent`),
  **not** the `livekit.toml` `[agent.rooms]` glob. `lk agent create` normalizes `livekit.toml` to
  just `[project]` + `[agent] id` — the `language`/`entrypoint`/`[agent.rooms]` keys are not
  persisted (LiveKit routes by name + explicit dispatch, so the committed room glob was a no-op).
- `LIVEKIT_URL`/`LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET` are **auto-injected** into Cloud agents —
  never push them as agent secrets.

## ⚠️ The Node mis-detection gotcha

`lk agent create`/`deploy` auto-detect the language and **trip on `package.json`** (see Node, look
for `@livekit/agents`, abort). Move `package.json` aside for the duration of the `lk` call. The CI
deploy job, `scripts/bootstrap-agent.sh`, and `make deploy` all do this; replicate it for any manual
`lk agent` build.

Moving `package.json` aside leaves the git working tree dirty, and a newer `lk agent deploy` warns
on a dirty tree and **prompts `Continue deploying anyway?`** — which in CI (no interactive answer)
cancels the deploy. Pass **`--yes`** (the CLI's documented CI/non-interactive escape hatch) to
auto-accept. It's a **global** flag, so it must precede the subcommand: **`lk --yes agent deploy`**,
not `lk agent deploy --yes` (the latter errors as an unknown flag). A dirty-tree deploy is still
tagged with a working-tree content hash, so the deployed state stays identifiable.

## First-time bootstrap (one-off)

`make bootstrap-agent` (dry run) → `make bootstrap-agent-apply` runs `lk agent create`, which creates
the agent, injects `.env` secrets, deploys the first image, and **writes the new `id =` into
`livekit.toml`**. Commit that id so the CI `lk agent deploy` targets it. Without an id, `lk agent
deploy` fails fast rather than touching the wrong agent.

## Secrets

`make sync-secrets` (dry run) / `make sync-secrets-apply` → `lk agent update-secrets --id <id>
--secrets-file <filtered .env> --overwrite --ignore-empty-secrets` (this **restarts** the agent).
Only keys defined in `.env.sample` sync (drops removed/legacy keys); `LIVEKIT_*` excluded. The script
**hard-refuses the legacy `lk-voice-agent` id**. Note `lk agent deploy --secrets K=V` is an
**upsert** — it adds K=V without wiping the agent's other secrets (unlike `sync-secrets-apply`'s
`--overwrite`, which replaces the whole set). Because `--ignore-empty-secrets` drops empty-valued
keys from the pushed payload and `--overwrite` then removes any key absent from it, **setting a key
empty in `.env` deletes that secret from the agent** (it does not linger as a stale value) — that's
how you clear a deployed secret; the agent falls back to the code default. Verified: clearing
`EXPERIMENT_NAMES=` removed the key outright (ENG-885).

**A secrets sync reaches only new sessions.** The restart drains in-flight jobs on their original env for
up to ~1h, so a flag rollback does not stop the behaviour at once. On 2026-10-27 the talk-over guard
(ENG-1618) was set `false` at 00:54:43 UTC, and 15 sessions that had started earlier kept cancelling replies
until 01:28:29 (226 events). Judge a flag change only on sessions that started after the sync. Anything that
must stop immediately needs a switch read during the session, not just the env at start, and a risky
behaviour ships in shadow (log-only) mode first.

## Versioned release (ongoing)

A `v*` tag triggers CircleCI **test → `lk agent deploy`**. The agent logs its `version:` by reading
`pyproject.toml` at startup (`src/version.py`) — no secret injection. Slack notifies on test + deploy.

### ⚠️ Do NOT use `npm run release`

**release-it cannot release this repo.** Its only mechanism for getting the version-bump commit onto
`master` is `git push`, and `master`'s ruleset requires a pull request with **no bypass actors**
(hardened as part of the PAT-compromise response — see PROD-4192), so every push is rejected with
`GH013` — for every account, repo admins included. release-it has no concept of a pull request, so no
flag or config fixes this. Release with the skill below instead.

Failed attempts are also actively misleading. Its default `git.pushArgs` is `["--follow-tags"]`, so
one `git push --follow-tags` carries both refs; git updates refs independently, so **the tag lands**
(triggering the CircleCI tag pipeline) while `master` is rejected — then rollback **deletes the tag**,
and CI dies at `error: pathspec 'v<version>' did not match any file(s) known to git`, firing a red
"test job failed" Slack alert for a test that never ran. Don't read that as "I can't tag anymore":
**tags are never blocked** — the ruleset's rules are all branch-scoped. release-it deleted its own tag.

### Release with `eng:release-version`

```
Skill: eng:release-version
```

It detects this repo's shape (`package.json` **and** `pyproject.toml` — the two version files that
must move together), proposes the version from the commits since the last tag, shows a plan for
approval, lands the bump through a pull request, tags the squash-merge commit, creates the GitHub
release, and verifies the CircleCI pipeline. `--version X.Y.Z` overrides the proposal.

It also enforces two things a hand-run cannot: the tag goes on the **squash-merge SHA** (a new commit
— not the branch head you pushed), and only after confirming that commit actually carries the bumped
version. That second check is what keeps the agent from misreporting itself, since `src/version.py`
reads `pyproject.toml` at runtime.

**Never tag before the merge.** The tagged commit must be on `master` and carry the bumped version —
CircleCI builds the image from the tagged commit — and a tag on a commit that never reaches `master`
leaves CI checking out a ref that then disappears. The skill enforces this ordering; a hand-run has to
hold it deliberately.

#### By hand, if the AINative plugins aren't installed

Same ordering, no safety checks — bump both version files on a branch, merge, then tag:

```bash
git fetch --tags && git log --oneline "$(git describe --tags --abbrev=0)"..master
git checkout master && git pull && git checkout -b chore/release-X.Y.Z
npm version X.Y.Z --no-git-tag-version && python3 scripts/bump-pyproject.py X.Y.Z
git commit -am "chore: release X.Y.Z" && git push -u origin chore/release-X.Y.Z
gh pr create --title "chore: release X.Y.Z" --fill
gh pr merge <pr-number> --squash --delete-branch
git checkout master && git pull          # AFTER the merge
git tag -a vX.Y.Z -m "Release X.Y.Z" && git push origin vX.Y.Z
gh release create vX.Y.Z --generate-notes
```

Push the branch and the tag as **separate** steps — never one `git push --follow-tags`. That is what
strands a tag when the branch is rejected, and it is the phantom-alert mechanism described above.

## The LiveKit SDK is pinned; bump it deliberately

`livekit-agents[...]`, `livekit-plugins-noise-cancellation` and `openai` are **exact** pins in
`pyproject.toml`, and `livekit-agents` in turn pins the bridged `livekit` core. The image installs those
specs at build time with no lock file, so a range let the SDK under a release drift: v1.13.2 shipped on
1.8.6 while 1.8.8 had been out a week, held back by an `openai<3` pin nobody connected to it, and which
SDK served a session could only be inferred from traceback line numbers. Every session now logs the
installed versions on its `Session starting` row (`sdk_*` fields), so group by them the same way as
`serviceContext.version`.

A bump is its own PR and its own **minor** release:
- read the release notes for anything touching our workarounds (log-screen vendor strings, the STT/TTS
  stall recoveries, `clear_user_turn()`, AEC warmup);
- resolve the new pins in a **fresh Python 3.12 venv**, not the cached one (see the CI-cache memory), and
  run the full suite there, log-screen conformance and behavioral included;
- grep the new SDK for every vendor string `src/log/*_logging.py` matches; the conformance suite proves
  shape, not wording;
- after deploy, confirm the `sdk_*` fields and the screens' stamps in production.

## QA the deployed agent

Drive it via the **talk-to-aurie** skill (text or voice, `remote` target → `aurie-voice-agent`).
⚠️ **Use a real Aura `user_id`** in the metadata: the backend coach prompter returns **empty**
content for a non-existent user, so LLM turns come back blank (the static greeting still plays,
masking it). A real `user_id` gets real replies.
