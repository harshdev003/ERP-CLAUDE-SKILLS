---
name: map-vendor-data-flows
description: >
  Build a dated data map of what personal data our apps and services send to each third party
  (Mixpanel, Firebase Analytics, Bugsnag, Iterable, AppsFlyer, Facebook, Google Tag Manager,
  Appstack, Branch, OneSignal): every event and property name with file:line, its class (free
  text, contact, health-adjacent, device or user id, geo), when the code added it, and how many
  users it reached in 90 days and all time, plus the SDK settings that matter (IP geolocation,
  automatic events, session replay, consent gating). Read-only discovery: field names and counts
  only, never a value or a user id. Trigger on "map vendor data flows", "what personal data do we
  send to Mixpanel", "data map of our third parties", "which vendors get user emails", "record of
  processing activities inventory", "audit what we send to analytics vendors", or "which events
  carry free text". Do NOT use to design or approve Mixpanel events (`data:mixpanel-data-design`,
  `data:mixpanel-event-approval`) or to change any SDK setting.
metadata:
  version: "1.0.0"
knowledge:
  reads:
    - Guide/Index           # baseline routing aid (universal)
    - Tools/Mixpanel        # project, service account, JQL limits for the reach step
    - Tools/GitHub          # repos read at origin's default branch
    - Compliance/Privacy    # field names and counts only; never a value, a user id or an email
---

# Map Vendor Data Flows

Answers one question, repeatably: **what personal data do our apps and services send to each
third party, since when, and to how many users.** The output is a data map, the inventory a
record of processing activities is built on.

**Discovery only.** The skill reads code and vendor aggregates and reports. It changes no code,
no SDK setting and no vendor configuration, and reads no production database.

**Field names and counts only.** No property value, user id or email is read or printed. This is
enforced in code, not left to the reader: every string from the code passes `guard.mask()` (an
email, uuid, Firebase uid, IP, phone or token becomes `<masked:kind>` and is counted), and
every file the skill writes (`scan.json`, `reach.json`, the history cache, `data-map.md`,
`data-map.csv`) passes `guard.check()` first or is not written. Files are mode 600.

## Prerequisites

| Need | For | Without it |
|---|---|---|
| `--base DIR` or `AURA_REPOS_DIR`: the folder holding the `Mobile/`, `Services/`, `Web/` and `Functions/` clones | the code scan | the scan exits naming the setting; a repo not cloned there is missing from the map, and the run lists what it scanned |
| `MIXPANEL_SERVICE_ACCOUNT_USERNAME` / `_SECRET` (`_PROJECT_ID`, default 963821) | reach | the reach columns read `unmeasured` with the reason, never 0 |

## Step 1 - Scan the code

```bash
OUT=~/.cache/ainative/vendor-data-map/$(date +%F)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/map-vendor-data-flows/scripts/scan.py --out "$OUT" --base "$AURA_REPOS_DIR" \
  [--repos Mobile/Aura,Mobile/AuraConsumerApp] [--previous OLD/scan.json]
```

- Every repo under `--base` is read at **origin's default branch from git objects**: `git fetch`, then
  `git ls-tree` and `git cat-file`. Nothing is checked out; the working tree and any local edits
  are never read. Clones named `<repo>-<suffix>` (worktree copies) are skipped.
- **What counts as a send** lives in [`references/sinks.json`](references/sinks.json): each vendor's
  SDK write calls, our own wrappers (Aura's `Trackings()`, Aurie's `trackEvent(name:)`, the
  services' `mixpanel.track(userId, event, props)`), and the properties a wrapper adds to every
  call. A local helper that passes its event-name parameter straight to a wrapper
  (`const logEvent = (event, data) => Trackings(event, …)`) is followed to its own call sites.
  **Add a wrapper there after reading its body**, never from its name alone.
- Properties are the **keys** of the map literal a call passes, of a map built in the same function
  (`let p = [...]`, `p["Key"] = …`), or returned by a same-file helper. Anything else is recorded in
  `unresolved` and counted in the report. An event name built at run time is `<dynamic>`. Both are
  tagged and counted, never dropped.
- **Class** comes from the property name alone, through [`references/classes.json`](references/classes.json).
  A name ending in `ID`, `Count`, `Type` and the like is about the thing, not its content, so it
  is not free text or health-adjacent. To change a class, edit that file and rerun; never edit
  a run's output.
- **History:** for each sensitive property, `git log -S` over the repo's source dirs gives the
  date the code first added the name. `--previous` takes an earlier `scan.json`; a sensitive row in
  it that is gone now is listed as removed, with the date of the commit that last changed it.
  Results are cached in `history-cache.json`, keyed by commit.

## Step 2 - Measure reach

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/map-vendor-data-flows/scripts/reach.py --out "$OUT" [--windows 90d,all]
```

One JQL query per window groups by user **inside Mixpanel** and returns only counts per
(event, `mp_lib`), plus one query for the users per `mp_lib` (the denominator). `mp_lib` maps to an
app through `libs` in `sinks.json`. Aura reports `android` and `iphone`, so its percentage adds two
libs and is an upper bound. The 90-day window takes seconds and all time about two minutes. A
window already in `reach.json` is reused only when it measured every event the current scan
found, and never with `--refresh`. Windows run on the project's calendar (Pacific), not the host's.

Reach is measured in Mixpanel. For another vendor, the same event's Mixpanel reach is a proxy,
and the report says so.

## Step 3 - Render

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/map-vendor-data-flows/scripts/render.py --out "$OUT" [--all-rows]
```

- `data-map.md` has a dated header naming each repo's commit, then one table per vendor of the
  sensitive rows. Each row shows event, property, class, file:line (plus how many more call sites),
  the date added, and users and % over 90 days and all time. A property sent with more than 20
  events through our wrappers is one line per vendor, not a row per event. Profile properties
  (`people.set`) show as `(user profile)`. `--all-rows` lists every property.
- After the vendor tables come the **SDK settings** (IP geolocation, automatic events, session
  replay, consent gating, advertising ids): each with file:line and its literal value, plus the
  flags no code sets, where the SDK default applies.
- `data-map.csv` holds every (vendor, app, event, property) row, for a spreadsheet.

Report the headline numbers to the user: vendors, events, sensitive pairs, the top sensitive
properties by reach, and every count of `<dynamic>`, `unresolved` and masked strings. Hand over
the two files. Posting them anywhere is a separate decision for the person who asked.

## Limits

- A static scan sees what the code says, not what ships: a build flag, a remote config or a
  server-side transform can change it. Reach is what Mixpanel received.
- Native SDK automatic collection (device model, OS, IP-derived city) is not a call in our code.
  The settings section shows whether it is turned off; the map does not list those fields.
- `git log -S` finds the first commit that added the name anywhere in the source dirs, which can
  predate this event's use of it.
