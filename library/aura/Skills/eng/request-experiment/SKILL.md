---
name: request-experiment
description: >
  Ask Viktor in #support-bot to create an A/B experiment for an Aurie or Aura change, posted as
  you, then verify what was created. Reads the experiment's minimum version from git (the next
  release's MARKETING_VERSION, checked to carry the PR's merge), asks for the experiment ACTIVE on
  all arms with that minimum version, waits for Viktor's and AuraBot's "Created experiment"
  confirmation, checks the server config (active arms, minVersion, targetApp), and posts the thread
  link on the card or PR. Never addresses AuraBot itself. Trigger on "request an experiment",
  "ask Viktor to create the experiment", "create the experiment for this PR", "set up the A/B for
  {name}", "request experiment {name} with arms a/b". Do NOT use for a loop idea row
  (`product:ship-idea` owns that request), for assigning one user to a bucket
  (`eng:set-user-experiment`), or for ending an experiment.
metadata:
  version: "1.1.0"
knowledge:
  reads:
    - Guide/Index
    - Tools/Slack                    # post format and footer
    - Tools/Aura API                 # GET /experiments/{name} with AURA_API_TOKEN
---

# Request Experiment

Asks **Viktor** in `#support-bot` (`C02FQE15YGP`) to create an experiment, posting as
you. Viktor issues the AuraBot command and documents the experiment (Mixpanel dashboard,
AB sheet). The skill never sends AuraBot a command: Viktor owns creation and documentation,
and the setup trail stays in that channel.

```bash
SCRIPT="python3 ${CLAUDE_PLUGIN_ROOT}/skills/request-experiment/scripts/request_experiment.py"
```

## Inputs

| Input | Flag | Notes |
|---|---|---|
| Experiment name | `--experiment` | The exact name the app code reads |
| Summary | `--summary` | One sentence: what it tests |
| Arms with meanings | `--arms` | `'a: <treatment>, b: control'`. At least two, consecutive letters from `a`, one named control |
| App | `--target-app ai\|aura` | `ai` = Aurie (`targetApp ai`); `aura` = Aura (no targetApp) |
| Platform | `--platform ios\|android` | Default `ios`. Aura's iOS and Android versions differ, so request one platform at a time |
| PR | `--pr <url>` | The merged PR carrying the change; the minimum version is checked against it |
| Card | `--card <url>` | Linear card, quoted in the request and where the thread link is posted |

Ask for anything missing. Don't invent arm meanings.

## Rules

- **Active on all arms.** The request asks for `variants` = every arm (e.g. `ab`). Never
  "create it off on the control arm": AuraBot has no update command, so an experiment created
  on one arm needs a manual edit to start. That happened on the precedent thread.
- **The minimum version comes from git, before posting.** A wrong minVersion can't be fixed
  through AuraBot either.
- **Posted as you** (`SLACK_USER_TOKEN`, no APP badge), as the team's requests are.

## Step 1 — the minimum version

```bash
$SCRIPT version --target-app ai --pr https://github.com/AuraHealth/AuraConsumerApp/pull/1904
```

It reads the version file on the app repo's default branch (`project.yml`, or Aura's
`project.pbxproj` / `build.gradle`) and accepts it only when:

- the PR is merged, and the branch carries the merge commit;
- no build of that version is already tagged without the merge. minVersion is a version, not
  a build, so an earlier `1.33.11-1` without the change would let its users in.

Anything unreadable refuses (exit 2) with the reason. `--version X` overrides the check.
Use it only with a stated reason, such as the release being cut from a branch the check can't
see.

## Step 2 — show the request, then post it

```bash
$SCRIPT request --target-app ai --experiment aiVoiceAudioMode \
  --summary "Aurie iOS voice echo test" \
  --arms "a: .voiceChat + .defaultToSpeaker with Apple echo cancellation, b: control (.videoChat)" \
  --pr <pr-url> --card <card-url>
```

Show the rendered text to the user. Post it only on an explicit yes, by adding `--post`. Keep the
printed `ts`.

## Step 3 — wait for the confirmation, then verify

```bash
$SCRIPT check --target-app ai --experiment aiVoiceAudioMode --ts <ts> \
  --arms "<same arms>" --version <minVersion> --wait 15
```

| exit | meaning | do |
|---|---|---|
| 0 | AuraBot confirmed and the server config matches | Step 4 |
| 1 | No AuraBot "Created experiment" in the thread yet | Read Viktor's last reply (printed). If it asks something, answer in the thread as the user. Otherwise re-run later |
| 2 | Config differs (printed per field), or the record can't be read | Reply in the thread with the exact fix for Viktor. An unreadable record is **unverified**, never verified |

## Step 4 — link it on the card

Post the printed thread link on the card (Linear MCP `save_comment`) or the PR (`gh pr comment`),
with the verified active arms and minVersion.

## Sources

- Precedent thread: `#support-bot`, 2026-11-04, `aiVoiceAudioMode`. It was created on `b` only, and
  its minVersion was added by hand afterwards because AuraBot has no update command.
- AuraBot grammar: SupportService `src/commands/createExperiment.js`.
- Request and check logic copied from `product:ship-idea` (`request-experiment`, `check-experiment`).
