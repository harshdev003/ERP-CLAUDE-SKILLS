# RTDB Probe Log — columns and matching rules

The Notion database under AI Knowledge → Compliance → Security
(https://app.notion.com/p/61cadff749084dd7838222de6188bf29, data source
`collection://96b6746d-d7a1-402f-9b9b-0c625d6c8621`). One row per **Path + Op + Verdict**, with the apps'
routine reads (`Routine app read`) in a row of their own, so an outside read of the same node is not
absorbed into them.
`scripts/plan-probe-log.js` implements every rule below; its `--self-test` covers each one.

## Columns

| Column | Type | Written by | Value |
|---|---|---|---|
| Path | title | planner (create) | `/`, `/{node}`, or a templated path below a node (`/users/$id/email`): the segment after the node is always `$id`, and any later one is too unless it is a plain field name: letters, digits and `_`, under 20 characters, and under 16 when it has a digit (so `@`, `.` or a letters-only uid becomes `$id`); never a key |
| Verdict | select | planner (create) | `Denied` or `Allowed` |
| Kind | select | planner (create; filled if empty) | see *Kind* below |
| Op | text | planner (create) | profiler operation name, e.g. `rest-read`, `listener-listen` |
| Client | text | planner | client labels joined with `; ` |
| First seen | datetime | planner (create; filled if empty) | earliest attempt, minute precision, UTC |
| Last seen | datetime | planner | latest attempt, minute precision, UTC |
| Attempts seen | number | planner | running total |
| Days seen | number | planner | UTC days with at least one attempt |
| Max IPs in a window | number | planner | largest distinct-IP count in one profiler run |
| Card | text | human | Linear card for a follow-up, e.g. `ENGBACK-1552` |
| Notes | text | planner (create), then human | on create, the Security Incident Log group the kind also lands in (`Firebase RTDB|<type>|…`), when there is one; then context |

## Kind

What the Probe Log holds: **every read of the root or a whole top-level node**, routine ones included
and marked as routine so they can be told from attempts, and **every denied operation at any depth**,
reads and writes. Also allowed REST reads below a node from outside the apps. Not logged: the apps'
allowed reads and writes below a node, and allowed Admin SDK server traffic.

Derived in `profile-firebase-rtdb/scripts/root-attempts.js` and `rest-reads.js`, first match wins:

| Condition | Kind |
|---|---|
| a denied write (any path) | Denied write |
| a denied read below a top-level node | Denied read below a node |
| path is `/` | Root read |
| node has no rule in `rules.json` | Guessed name (scanner) |
| node has a rule (or the rules are unknown), not public, and the op is REST | Whole real node via REST |
| node has a rule and the read was denied | Denied read of a real node |
| an allowed SDK listen of a whole real node, not public, from outside the apps (the console's data viewer) | Whole real node via SDK listen |
| any other allowed SDK read (a one-shot get or query) of a whole real node, not public, from outside the apps | Whole real node via SDK read |
| an allowed SDK read of a whole node by the apps (`native`) | Routine app read |
| an allowed SDK or REST read of a public-by-rule node from outside the apps | Public node read |
| an allowed REST read below a node, not the Admin SDK (`rest-reads.js`) | REST read below a node |
| anything else (rules unknown) | Unclassified (rule unknown) — never written |

A kind that is not yet a select option is added by Notion on the first create; if the create is
rejected, add it with `notion-update-data-source`.

Expected size: one row per node + op + verdict + kind actually seen. The rules define about 235 top-level
nodes, and a 30-minute profile sees a few dozen of them, so the routine rows settle at tens, plus one
row per denied path shape. A path below a node keeps the first two segments after the node at most
(`/node/$id/field/…`), so it can't grow per user.

The planner refuses any probe file with a `rule unknown` line: without `rules.json` a guessed name
can't be told from a real node, so the Kind would be wrong.

## Client label

`{platform}/{os} {agent}` — `agent` is `browser` for a `Mozilla/*` user agent, otherwise the first token
of the user-agent string; a missing platform or os is `unknown`. Examples: `desktop/windows browser`,
`ios/ios Aura/3.2`. Rows typed by hand before the planner existed may carry other spellings; the first
planned update appends the machine label once, and they match from then on.

## Matching and merging, per run

1. **Merge** probe lines with the same Path + Op + Verdict (one per client group) into one probe:
   attempts and IP counts summed, earliest `t0`, latest `t1`, clients listed in order. The summed IP
   count is an upper bound, since two clients can share an address.
2. **Match** the probe to the row with the same Path + Op + Verdict (and, for a `Routine app read`, a
   routine row; any other kind matches the non-routine row). Verdict is part of the key: an
   allowed read of a path that was only ever denied is its own row.
3. **No row → create**: Days seen 1, First/Last seen from the probe, Notes and Card empty.
4. **Row exists → update**:
   - Attempts seen += attempts.
   - Last seen = the probe's latest attempt.
   - First seen kept; filled only when empty.
   - Days seen += 1 when the UTC date of the new Last seen is after the old Last seen's date; set to
     at least 1 when the row had no Last seen.
   - Max IPs = max(row, probe).
   - Client: each label not already in the list (case-insensitive) is appended with `; `.
   - Kind filled only when empty.
5. **Already applied**: a probe whose latest attempt (to the minute) is not after the row's Last seen
   is skipped and counted in `summary.already_applied`. Re-running a planned and applied run is a
   no-op; so is replaying an older run.
6. **Duplicate rows** for one key are listed in `summary.duplicate_rows` and the first is used. They
   need a human merge.
