# App-family routing

The Bugsnag project name is the **app-routing key** for this skill. It tells the renderers which app the error belongs to, which determines stacktrace handling, the suspect repo for downstream code investigation, and the default Linear team for any card created from a triage.

The runtime project list comes from `GET /organizations/<org-id>/projects`. The mapping below is what `scripts/projects.py` uses to classify each slug. Snapshot from 2026-08-25.

## Project → app family → suspect repo

| Slug | App family | Stack | Repo | Verified by | High-PII? |
|---|---|---|---|---|---|
| `ai-aurie-ios` | Aurie / mobile | iOS native (Swift) | `AuraHealth/AuraConsumerApp` | api_key in `Config/Secrets.{dev,prod}.xcconfig` | yes |
| `ai-aurie-macos` | Aurie / desktop | Electron | `AuraHealth/ConsumerAIDesktop` | sole `@bugsnag/electron` repo; `package.json` name `aurie`, productName `Aurie` | yes |
| `aiweb` | Aurie / web | React | `AuraHealth/AIWeb` | name + `NEXT_PUBLIC_BUGSNAG_KEY` wiring | yes |
| `aura-ios` | Aura / mobile | React Native | `AuraHealth/Aura` | api_key in `ios/Aura/Info.plist` | no |
| `aura-android` | Aura / mobile | React Native | `AuraHealth/Aura` | api_key in `android/app/src/main/AndroidManifest.xml` | no |
| `aura-aimacos` | Aura / desktop | macOS native | TBD | — see *Why `aura-aimacos` stays TBD* below | no |
| `aura-userweb` | Aura / web (consumer) | React | `AuraHealth/UserWeb` | name + `NEXT_PUBLIC_BUGSNAG_KEY` wiring | no |
| `aura-coachweb` | Aura / web (coach) | React | `AuraHealth/CoachWeb` | name + `NEXT_PUBLIC_BUGSNAG_KEY` wiring | no |
| `aura-businessweb` | Aura / web (B2B) | React | `AuraHealth/BusinessWeb` | api_key in `.env.dev` | no |
| `aura-crmweb` | Aura / web (internal) | React | `AuraHealth/CRMWeb` | name + `NEXT_PUBLIC_BUGSNAG_KEY` wiring | no |
| `aura-investweb` | Aura / web (investor) | React | `AuraHealth/InvestWeb` | api_key in `.env.dev` | no |
| `aura-front-plugin` | Aura / embed | React | `AuraHealth/AuraFrontPlugin` | name + `VITE_BUGSNAG_API_KEY` wiring in `src/services/bugsnag.jsx` | no |

### How a row is verified

Read the project's `api_key` from `GET /organizations/<org-id>/projects`, then
grep the candidate repo for that literal key. Bugsnag keys are committed in
`Info.plist`, `AndroidManifest.xml`, `*.xcconfig` and `.env.dev`, so this is a
direct match rather than an inference. The web apps inject the key at build time
(`NEXT_PUBLIC_BUGSNAG_KEY` / `VITE_BUGSNAG_API_KEY`), so nothing is committed to
grep — those rows rest on the repo being the org's only app of that name and
stack that ships Bugsnag at all.

**Do not fill a row from the slug.** Slug and repo names do not track each other:
`aura-ios` / `aura-android` read as "the iOS and Android app" and `AuraConsumerApp`
reads as "the consumer app", but they are different products — the Aura mobile
projects belong to `AuraHealth/Aura`. The `Stack` column is the cheap tell for a
mismatch: `react-native` next to a Swift-only repo is a contradiction on a single
row, and `tests/test_bugsnag_projects.py` fails on exactly that shape.

### Why `aura-aimacos` stays TBD

Two Swift candidates (`AuraHealth/AIDesktop`, `AuraHealth/AIDesktopPOC`) and no
way to choose between them: neither commits its key, and the project has received
**zero events since it was created** (2024-12-24), so there is no event metadata
to match against either. `AIDesktopPOC` does commit a `BUGSNAG_API_KEY` in
`fastlane/.env.secret`, but it matches no project in the org — stale, not
evidence. TBD is the correct value here: Mode 2.5 skips with a "no repo mapping"
note, which beats a coin-flip between two repos. Fill it in if either app ever
reports an event, or if a key turns up committed.

## Why each column matters

- **App family** — display label in digests; users recognise "Aurie / mobile" faster than `ai-aurie-ios`.
- **Stack** — formatter dispatch key in `scripts/normalize.py`. Today the difference is small (mostly used to decide whether `inProject` defaults to `True` for missing fields), but the seam is in place for per-stack frame rendering when the formatter grows.
- **Repo** — passed to `eng:code-investigator` (Mode 2.5, separate PR). The subagent uses it to find the local checkout, refresh `origin/main`, and look at the focus stacktrace frame in source. `TBD` entries cause Mode 2.5 to skip with a "no repo mapping for `<slug>`" note rather than guessing.
- **High-PII** — Aurie projects carry user-conversation text in event metadata. Mode 3 (trace) on those projects names the metadata sections present but does not echo the deep payload — the Bugsnag UI is the audit-trail-friendly place to read it.

## Keeping this in sync

The mapping is hardcoded in `scripts/projects.py` — of which **five byte-identical
copies** exist, one per Bugsnag skill (`investigate-bugsnag-errors`,
`report-bugsnag-errors`, `search-bugsnag-by-user`, `analyze-bugsnag-releases`,
`resolve-bugsnag-errors`), because skills are self-contained. It is also mirrored
in this doc and on the [Tools/Bugsnag](https://www.notion.so/35a75e23af2081b1a021d7b2b8999e2f)
page in AI Knowledge. When a project is added or its repo is confirmed:

1. Verify the repo by api_key grep — see *How a row is verified* above. Never fill from the slug.
2. Update `scripts/projects.py` in the canonical skill (`investigate-bugsnag-errors`),
   then propagate to the other four copies **in the same PR**. `tests/test_bugsnag_projects.py`
   fails if they diverge, and fails if a sixth copy appears undeclared.
3. Update this table, including the `Verified by` column.
4. Update `Tools/Bugsnag` in AI Knowledge.
5. Bump the snapshot date at the top of this doc.

If the three disagree, `scripts/projects.py` wins — it is the only one loaded at runtime.

Note that steps 2–3 are two implementations of one mapping, in Python and in prose,
so no `diff` can hold them together. `tests/test_bugsnag_projects.py` parses this
table and asserts it agrees with `PROJECTS` row for row, which is what makes the
doc a checked mirror rather than a stale one.

## Adding a brand-new project

When the Aura org spins up a 13th Bugsnag project:

1. The skill keeps working — unknown slugs fall through to `UNKNOWN` (family `Unknown`, no repo, defaults to high-PII for safety).
2. Add a row to `PROJECTS` in `scripts/projects.py` with the right family, stack, and repo. If the repo isn't checked in yet, leave `repo=None`.
3. Update this doc and `Tools/Bugsnag`.
4. PR the change with a `[PROD-1659]` ticket reference (or open a follow-up under the next maintenance card).

## Family taxonomy

Six families. The Aurie/Aura split mirrors the two product surfaces; mobile/desktop/web/embed mirrors how the team thinks about the runtime stack. The five Aura web families (consumer / coach / B2B / internal / investor) are split because each has a different audience + Linear team, and the default routing for "create a Linear card from this Bugsnag error" needs to land on the right team.
