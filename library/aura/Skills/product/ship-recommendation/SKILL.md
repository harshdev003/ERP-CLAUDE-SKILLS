---
name: ship-recommendation
description: >
  Takes a merged recommendations PR to a ramping experiment — the Aura Recommendations loop's
  ship leg, where a backend release replaces the TestFlight build. Cuts the release via
  eng:release-version, posts the release announcement that opens Gate 2 in the idea's thread,
  records the release on the row while leaving it harvestable, and once a human approves asks
  Viktor in #support-bot to
  create the experiment. Refuses a flag ContentService would assign to nobody. Trigger on "ship
  this recommendation idea", "release the recommender change", "take this rec idea to an
  experiment", "run the recommendations ship leg", or a merged PR on a loop row awaiting
  release. Do NOT use for a mobile idea (product:ship-idea), to create or end an experiment
  yourself (Yawar and Viktor own that), or to cut a release outside the loop
  (eng:release-version).

metadata:
  version: "1.0.7"
knowledge:
  reads:
    - Guide/Index       # baseline routing aid (universal)
    - Tools/Notion      # NOTION_TOKEN — the loop row
    - Tools/Slack       # loop channel + #support-bot
    - Tools/Linear      # LINEAR_API_KEY — the cleanup card
    - Learnings/Loop/Aura Recommendations Ideas   # the lifecycle this leg advances
---

# ship-recommendation

The Aura Recommendations loop's ship leg. `product:ship-idea` waits on a TestFlight build; a
recommendations change is server-side, so there is nothing to install and nothing to try. The
equivalent moment is the **backend release**: the code is deployed behind its flag and
**no experiment exists yet**.

Order matters and is easy to get backwards: release first, then Gate 2, then the experiment is
created. Creating it at release time would put users into an arm before the decision this leg
exists to protect.

**The row stays `implementing` through the release.** `harvest-idea-decisions` harvests only
`pending`, `shared` and `implementing`, and maps the release gate to `implementing` alone
(`HARVESTABLE` / `STATUS_GATE`). Moving the row to `shipped_dormant` at release time would take
it out of the harvester's view and **Gate 2 would never open** — silently, because a gate that
is never read is indistinguishable from a team that has not replied.

## What it does, and what it refuses

| Does | Never does |
|---|---|
| Cuts the release (`eng:release-version`) | Merges the PR |
| Posts the release announcement that opens Gate 2 | Answers Gate 2 |
| Records the release, leaving `Status` at `implementing` | Creates the experiment itself |
| Asks Viktor in `#support-bot` to create it, after approval | Ramps, halts or ends an experiment |
| Files the cleanup card on ENGBACK | Emits the AuraBot grammar directly |

Yawar and Viktor own the experiment. This leg asks for one and stops.

## Released is not live

The release puts the code in production behind its flag; **Gate 2 is what causes the experiment
to be created**. Two events with a human decision between them, and conflating them is the
mistake this leg exists to prevent — a release is not consent to assign users.

| After | Row status | State |
|---|---|---|
| the release | **`implementing`** | code live, flag present, **no experiment**. Harvestable, so Gate 2 can open |
| Gate 2 approval | `experiment_running` | Viktor created the experiment; set only once it exists |
| Gate 2 refusal | `shipped_dormant` | code stays, no experiment is ever created |

`shipped_dormant` is the **outcome of a declined Gate 2**, not a waiting room. A row parked
there is finished unless someone iterates on it.

## The flag name is load-bearing

`src/models/experiment.js` in ContentService assigns an experiment only when:

```js
experiment.startsWith("content")            // config.experiment.startsWith
  || includeList.includes(experiment)       // config.experiment.includeList
```

A flag satisfying neither is assigned to **nobody**. Nothing errors, the experiment runs to
completion, every user sees control, and the readout is a dead heat that looks exactly like a
genuine null result. `PROD-5985` is titled `ab:newRecommendationEngine` — as named, it would
have been assigned to no one.

So the ship leg **refuses** such a flag and suggests a compliant name. The rules live in
[`scripts/flag_rules.py`](scripts/flag_rules.py), covered by `tests/test_ship_recommendation.py`.

```bash
F="${CLAUDE_PLUGIN_ROOT}/skills/ship-recommendation/scripts/flag_rules.py"
python3 -c "
import sys; sys.path.insert(0, '$(dirname "$F")')
import flag_rules as fr
flag = fr.flag_from_card_title('ab:newRecommendationEngine: Build a new algorithm')
ok, problems = fr.check_flag(flag)
print(ok, problems, '->', fr.suggest_flag(flag))
"
```

The `includeList` escape hatch is honoured for a deliberately-named legacy flag, but it warns:
the prefix convention is what makes the gate readable to whoever reads the config next.

## The release announcement is the Gate 2 anchor

`product:harvest-idea-decisions` floors the release gate on an announcement in the thread.
Features anchors on `in TestFlight for review`; this leg anchors on:

```
{service} {version} released and awaiting the release gate.
```

**The wording is a contract.** `RELEASE_ANCHORS` in the harvester matches it; change one
without the other and the gate never opens — and nothing looks broken, because a gate that
never opens is indistinguishable from a team that has not replied.

## Plan and confirm before any write

This leg cuts a production release, posts to two Slack channels, mutates the Notion row and
files a Linear card. **List every write and wait for a matching confirmation before the first
one.** There is no unattended mode: unlike the digest, none of these writes is reversible by
re-running, and a release cannot be un-cut.

```
About to, for REC-12 "Rank the Today row by similarity":
  1. release  ContentService via eng:release-version        (production deploy)
  2. post     Gate 2 anchor -> #ai-loop-aura-recommendations (thread reply)
  3. update   Notion row -> status note only, Status STAYS implementing
Confirm with: ship REC-12
```

The numbers above are **writes**, not the step numbers below. Mapping, so the status write
cannot be delayed or replayed:

| Write | Step | When |
|---|---|---|
| release, Gate 2 anchor, status note on the row | 2–4 | **before** Gate 2, in one confirmed batch |
| ask Viktor, then row → `experiment_running` **or** `shipped_dormant`, plus the cleanup card | 6–7 | **after** Gate 2, confirmed separately |

The release batch records what happened in the status note and **leaves `Status` at
`implementing`** — that is what keeps the row harvestable so Gate 2 can open at all.

**Only set `experiment_running` once the experiment exists.** This leg asks Viktor to create it
and does not create it itself, so the status write must follow his confirmation, not the
approval reply. Flipping on approval alone marks a row live with nobody assigned — and that is
exactly the shape of a dead-heat readout nobody can explain later.

**Every Slack write composes `shared:post-to-slack`** with `footer=product:ship-recommendation`
— the Gate 2 anchor and the `#support-bot` ask both. Posting through the MCP connector or a
raw call leaves the announcement unattributed, and the anchor is load-bearing: the harvester
matches its text to decide the gate has opened.

## Steps

1. **Check the flag** before anything else. A refusal here is cheap; discovering it after a
   two-week experiment is not.
2. **Cut the release** by invoking `eng:release-version` for ContentService or 4U-engine.
3. **Post the anchor** into the idea's thread, with the version and what changed.
4. **Record the release** in the status note. **Leave `Status` at `implementing`** — see above.
5. **Wait for Gate 2.** The harvester reads the reply; this leg does not.
6. **On approval**, ask Viktor in `#support-bot` to create the experiment — never emit the
   AuraBot grammar yourself, and never create it before this point.
7. **Once Viktor confirms the experiment exists**, set `experiment_running`, then file the
   cleanup card on ENGBACK and write it to the row's `Cleanup issue`. On a Gate 2 refusal,
   set `shipped_dormant` instead and file the cleanup card only if the idea is abandoned.

## Notes

- **Cleanup cards go to ENGBACK**, not ENGMOB — this is backend work, and the card is what
  closes the idea → shipped code → removed code trail.
- **`targetApp` does not apply.** That field distinguishes Aurie from Aura in the mobile
  grammar; a ContentService change has no client target.
- **One content experiment at a time.** Roughly 6,800 people complete a listen on a given day;
  that will not carry two experiments competing for the same primary metric. The orchestrator
  enforces it — do not override.
