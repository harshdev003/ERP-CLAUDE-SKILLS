---
name: analyze-user-usage-limits
description: >
  Reconstruct a single Aura user's voice usage-limit history and explain whether and when the
  daily (1.5h) or weekly (13.5h) voice cap would have been hit — and whether the in-app gate
  was actually live (it shipped in app v1.20.0). Accepts a userId and a time frame. Pulls the
  user's timezone from Mixpanel, lists every voice usage row from the Aura API, reads their
  usage-limits experiment assignment, finds the date their app version crossed to v1.20.0+,
  computes per-day and weekly-to-date usage against the limits using the exact backend
  semantics, and renders a per-day table plus a summary. Trigger on "analyze usage limits for
  {user}", "did {user} hit the voice limit", "why did {user} use N hours over the cap", "usage
  limit history for {userId}", "how many times did {user} see the usage limit modal", "when
  did the usage limit start applying to {user}", or any per-user voice usage-cap
  investigation. Do NOT use for cost analysis or changing a user's experiment bucket
  (`eng:set-user-experiment`).

metadata:
  version: "3.1.0"
knowledge:
  reads:
    - Guide/Index          # baseline routing aid (universal)
    - Tools/Aura API       # endpoint resolution + auth for /ai/usage/list, experiments/assignments
    - Tools/Mixpanel       # event/property taxonomy (project_id, UserID, version, modal event)
    - Compliance/Privacy   # PII handling — truncate Firebase UID, never render full email/name
---

# Analyze User Usage Limits

Reconstruct one user's **voice** usage-limit timeline and answer the recurring
support/eng question: *"how did this user rack up N hours when the cap is
1.5h/day?"* The usual answer is that the in-app gate **shipped in app v1.20.0**
— usage before the user upgraded was unrestricted — but this skill proves it
per-user with data instead of guessing.

It stitches four sources together:

| Source | What it gives | How |
|---|---|---|
| Aura API `POST /ai/usage/list/:userId` | every voice usage row (per-call `duration`, `createdAt`) | curl, system token |
| Aura API `GET /experiments/assignments/:userId` | `usageLimitsNewUsers` / `usageLimitsOldUsers` bucket | curl (read-only GET) |
| Mixpanel (`$timezone` user profile) | the user's timezone (limit windows are tz-local) | Run-Query |
| Mixpanel (`Login` event → version) | date the app crossed to **v1.20.0+** (gate live) | Run-Query |
| Mixpanel (`AI Usage Limit Modal Seen`) | per-day count of times the gate actually blocked them | Run-Query |

The deterministic math (per-day daily + weekly-to-date vs the limits) lives in
[`scripts/compute_limits.py`](scripts/compute_limits.py). See
[`references/usage-limit-semantics.md`](references/usage-limit-semantics.md) for
the exact backend rules this mirrors and all the magic strings (event names,
property names, project id, the gate's shipping commit/version).

> **Privacy.** This skill handles a Firebase UID and may touch the user's
> profile. Per `Compliance/Privacy`, truncate the UID to its first 8 chars in
> any rendered output (the script's `--user-id` does this) and never print full
> email, given name, or family name.

## Step 0 — Prerequisites

```bash
test -n "$AURA_API_TOKEN" && echo "OK: AURA_API_TOKEN set" || echo "MISSING AURA_API_TOKEN"
python3 -c "import zoneinfo" 2>/dev/null && echo "OK: python3 zoneinfo" || echo "MISSING python3 (3.9+)"
```

- `AURA_API_TOKEN` — Firebase ID token for an internal-role user. The `:userId`
  usage-list endpoint needs `admin|service|system`; the experiments-assignments
  read (Step 4) is stricter and needs role **`agent` or `system`**, so use a
  token that satisfies both (a `system`-role token covers everything here). If
  missing, stop and hand off to `update-config` / see `/CLAUDE.md` → "Secrets".
- **Mixpanel MCP** must be connected (tools `Run-Query`, `Get-Events`,
  `List-Properties`). All Mixpanel work uses **project_id `963821` (Project
  Aura, prod)**.
- **IntelligenceService checkout** *(optional)* — lets the limits track the
  latest config (Step 6). Try to locate `src/config.js`:

  ```bash
  CONFIG_JS=""
  for d in "$HOME/Work/AuraHealth/IntelligenceService" "$HOME/AuraHealth/IntelligenceService" \
           "$HOME/IntelligenceService" "$HOME/code/IntelligenceService"; do
    [ -f "$d/src/config.js" ] && { CONFIG_JS="$d/src/config.js"; break; }
  done
  [ -n "$CONFIG_JS" ] && echo "CONFIG_JS=$CONFIG_JS" || echo "NO_CONFIG_JS — using built-in defaults"
  ```

  If found, `git -C <dir> pull` first so the parsed limits are current.
  **If not found, do NOT clone or block** — just skip `--config-js` in Step 6;
  the script uses its built-in defaults (1.5h / 13.5h). Note in the output which
  source was used.

## Step 1 — Inputs

Required: **`userId`** (Firebase UID, e.g. `KKBjkNBBBHShNfAVEoD41dwmdEk1`).
Optional: **time frame** — default **last 30 days**. Accept absolute
(`--since/--until` ISO) or relative ("last 2 weeks", "May 2026"). If `userId`
is missing, ask; never invent one.

## Step 2 — Resolve the user's timezone (Mixpanel)

Limit windows are evaluated in the user's **local** timezone (backend default
`America/New_York` when unknown). The timezone lives on the Mixpanel **user
profile** (`$timezone`); Aura events do not carry a timezone property. Resolve
in this precedence:

1. **User-profile `$timezone` (preferred).** Run-Query (insights, project
   963821): event `AI Conversation Message Sent` (or any frequent event for the
   user), filter `UserID == <userId>`, breakdown by the **user** property
   `$timezone` (`resource: user`), chartType `table`, last 30–90 days. Take the
   dominant value (e.g. `America/Boise`).
2. **Event property fallback.** If `$timezone` is empty, try the event
   properties `timeZone`, then `Timezone` (`resource: event`).
3. **Default.** If all are empty, fall back to `America/New_York` and **note it**
   in the output — the fallback can shift per-day buckets across midnight versus
   the user's real tz, so flag the uncertainty.

> The Mixpanel user identifier is the **`UserID`** event property — **not**
> `$distinct_id`. Filtering on `$distinct_id` returns nothing.
>
> `$timezone` is a **user** property (Mixpanel geo-derived); the breakdown must
> use `resource: user`. Confirm available names with
> `List-Properties resource_type=User query="zone"` if unsure.

## Step 3 — List the user's voice usage (Aura API)

```bash
curl -A "AuraClaude/$(printf %s "${AINATIVE_UA_WHO:-$(git config --global user.email || git config --global user.name || id -un)}" | sed -E 's/@.*//; s/^[0-9]+\+//; s/[^A-Za-z0-9._-]+/-/g; s/^[-.]+|[-.]+$//g' | grep . || echo unknown)/eng:analyze-user-usage-limits (session ${AINATIVE_SESSION_NAME:-adhoc})" -sS -X POST "https://services.aurahealth.io/ai/usage/list/<userId>" \
  -H "Authorization: Bearer $AURA_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"mode":"voice","sort":"asc","since":"<ISO>","until":"<ISO>"}' \
  -o /tmp/usage_<userId>.json
```

`POST /ai/usage/list/:userId` is the **system-authorized** variant (path
`:userId`), returning row-level usage for that user. `since`/`until` are ISO
(`2026-05-23T00:00:00.000Z`); omit for all-time. The response is a **top-level
JSON array** of rows, each with `createdAt`, `duration` (ms), `mode`. Save to a
file for Step 6.

> The plain `POST /ai/usage/list` (no `:userId`) is **self-scoped** to the token
> holder — only the `:userId` system variant works for analyzing another user.

## Step 4 — Read the experiment assignment (Aura API)

Read-only `GET` — returns the user's **full** experiment map as flat
`{experimentName: value}`; no request body, no per-experiment param. Filter for
the two keys we care about client-side.

```bash
curl -A "AuraClaude/$(printf %s "${AINATIVE_UA_WHO:-$(git config --global user.email || git config --global user.name || id -un)}" | sed -E 's/@.*//; s/^[0-9]+\+//; s/[^A-Za-z0-9._-]+/-/g; s/^[-.]+|[-.]+$//g' | grep . || echo unknown)/eng:analyze-user-usage-limits (session ${AINATIVE_SESSION_NAME:-adhoc})" -sS "https://services.aurahealth.io/experiments/assignments/<userId>" \
  -H "Authorization: Bearer $AURA_API_TOKEN"
```

> **Use the `GET …/assignments/` endpoint, not `POST …/assign/`.** The POST
> variant *mutates state* — calling it on an unbucketed user retroactively
> assigns them, perturbing the very history this skill reconstructs. The
> read-only GET (shipped in ExperimentService v2.36.1, PROD-2481) never writes.

Read the buckets for **`usageLimitsNewUsers`** and **`usageLimitsOldUsers`**
(note: plural — the client gate only calls `/ai/usage/limits` when one resolves
to variant `"a"`). The map contains a key **only for experiments the user is
assigned to**; a missing key means the user is **not** in that treatment — e.g.
`{…,"usageLimitsOldUsers":"a"}` means in the old-users treatment, unassigned to
new-users. A user in neither treatment is never gated server-side regardless of
app version.

The default `?includeBackup=true` unions the live table with archived
assignments, so an ended/deleted usage-limit experiment still shows up — exactly
what you want for a historical reconstruction. Leave the default; don't pass
`includeBackup=false` (that hides ended experiments).

> **Auth & quirks.** This endpoint is gated to internal callers — the
> `AURA_API_TOKEN` user must have role **`agent` or `system`** (stricter than the
> other Experiments endpoints; a missing or invalid token returns `401`, a wrong-role
> token `403`, and a UserService outage `503`).
> And an internal caller reading *its own* UID always gets `{}` — always point
> the endpoint at the target user's UID (which this skill does).

## Step 5 — Find when the gate went live for this user (Mixpanel)

The usage-limit gate shipped in **app v1.20.0** (see reference). Find the first
date this user was on **≥ 1.20.0**:

Run-Query (insights, project 963821): event `AI Login` (and/or `Login`), filter
`UserID == <userId>`, breakdown by `AI Current Version` (fall back to
`$app_version_string`), chartType `line`, unit `day`, over the time frame (pad a
few days earlier). The first day a value `>= 1.20.0` appears is
`gate-active-date`. (Cross-check: `AI Usage Limit Modal Seen` only ever fires on
the gated build — breaking it down by version confirms the cutover.)

## Step 6 — Compute per-day limit analysis (deterministic)

First get the per-day `AI Usage Limit Modal Seen` counts (Run-Query: event
`AI Usage Limit Modal Seen`, filter `UserID == <userId>`, chartType `line`, unit
`day`, over the time frame) and write them as a JSON map to `/tmp/modal.json`,
e.g. `{"2026-05-30": 13, "2026-05-31": 14, "2026-06-01": 4}`.

Then run, passing the **live limits** via `--config-js` (the `CONFIG_JS` path
from Step 0) so the daily/weekly caps always track the latest IntelligenceService
config rather than a stale hardcode:

```bash
# Include --config-js only when Step 0 found it; otherwise the script uses
# its built-in defaults. The array keeps the path correctly quoted.
CFG_ARGS=(); [ -n "$CONFIG_JS" ] && CFG_ARGS+=(--config-js "$CONFIG_JS")

python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-user-usage-limits/scripts/compute_limits.py \
  --usage /tmp/usage_<userId>.json \
  --timezone <tz from Step 2> \
  "${CFG_ARGS[@]}" \
  --gate-active-date <YYYY-MM-DD from Step 5> \
  --modal-json /tmp/modal.json \
  --user-id <userId>
```

**Limit resolution (precedence):** explicit `--daily-ms`/`--weekly-ms` flag →
`--config-js` (parses the `dailyMs`/`weeklyMs` defaults from `src/config.js`) →
built-in fallback (1.5h / 13.5h). The header line prints which source was used
for each (`daily<-config.js, weekly<-config.js`). If `CONFIG_JS` wasn't found in
Step 0, `CFG_ARGS` stays empty so the flag is dropped and the script uses the
fallback — call that out in the summary. **Caveat:** `config.js` is the
*code* default; if a deployed
environment overrides `DAILY_LIMIT_VOICE` / `WEEKLY_LIMIT_VOICE` (no endpoint
exposes the running numbers), pass the real value via the explicit flags.

The weekly window is fixed to the backend rule — **Sunday 00:00** in the user's
tz, which keeps the paid-package window aligned with the free-tier limiter (see
reference). The script prints the per-day table and summary directly; add
`--out-json` to also save structured output.

## Step 7 — Present

Show the script's table and summary, then add a one-paragraph plain-English
read tying it together, e.g.:

> User `KKBjkNBB…` was in `usageLimitsOldUsers=a`. The voice cap would have
> triggered on 7 of 8 active days, but the gate only went live on **2026-05-30**
> (app v1.20.0). The five pre-gate days (May 25–29, peaking at 7h40m on the
> 29th) were **unrestricted** because the feature wasn't in their build. From
> May 30 the gate worked: it blocked them **27 times** across May 30–Jun 1
> (incl. 14 blocks on May 31 with **zero** completed calls). No backend/SQL
> defect — the overage predates enforcement reaching the device.

Keep the UID truncated in any external write (Slack/Linear/Notion).

## Edge cases

- **No usage rows** → user never made a voice call in the window; report that.
- **Gate-active-date unknown** (no Login event / never upgraded) → omit
  `--gate-active-date`; the table's "Gate live" column shows `✗` throughout and
  the summary treats all breaches as unrestricted.
- **Mixpanel project tz vs user tz** — the per-day buckets here are computed in
  the *user's* tz from raw `createdAt` (authoritative), independent of how
  Mixpanel buckets its own charts; small day-boundary differences vs the
  Mixpanel modal-seen chart are expected near midnight.
- **Modal-seen days with no usage** are still shown (blocked-but-no-call days —
  the strongest evidence the gate is working).
