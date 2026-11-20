# Security Incident Log — Firebase RTDB rows

The Notion database under AI Knowledge → Compliance → Security
(https://app.notion.com/p/40175e23af208180aeced339a099f573, data source
`40175e23-af20-8197-8d7e-000b96ac48bf`), shared with `eng:flag-suspicious-traffic`. This skill reads and writes
only rows whose `System` is `Firebase RTDB`. `scripts/plan-incident-log.js` implements every rule
below, and `tests/test_rtdb_incident_log.py` covers each one. The schema is shared, so neither writer ever
changes it: `incident-log.js` exits 2 when a column or option it writes is missing.

## Input

`profile-firebase-rtdb/scripts/root-attempts.js --probe-detail` writes one line per attempt:
`{t, node, op, verdict, kind, tag, client, ua, ip, bytes}`, mode 600. It holds:

- every **denied** read of the root, a whole top-level node or a guessed name, except the Aura apps'
  own SDK (`platform` `native`): those are signed-out users, not probes;
- every **allowed** read of the root or a guessed name;
- every **allowed** `rest-read` or `listener-listen` of one whole real node that is not public by rule,
  from outside the apps (not `native`), tagged `rule exists`: the console's data viewer opened on one node.

`bytes` is the response size the profiler records, a number; it is never data.

## Groups, not IPs

Jerome, 2026-10-23: "same type of attack and no change in what's been gathered or leaked is the same".
A row is one **group**, keyed by the `Group key` column, `<system>|<attack-type>|<outcome>`, or
`<system>|<attack-type>|<family>|<outcome>` where what was gathered differs by path family (REST bursts,
below; Jerome, 2026-11-03). The format is shared with `eng:flag-suspicious-traffic` (its `Web|…` rows), whose
`references/rules.md` is the spec.

| Attack type (from the profiler's `kind`) | Category | Title |
|---|---|---|
| `root-read` (Root read) | Other unrecognized access | RTDB root reads |
| `guessed-name` (Guessed name (scanner)) | Forced browsing | RTDB guessed node names |
| `node-read` (any other kind: a whole real node) | Other unrecognized access | RTDB whole-node reads |

The option was named `Other attack` until Jerome renamed it in place (2026-11-10), with the US spelling
`unrecognized`; the code must match the live option exactly. Until the live
option is renamed, `incident-log.js` writes the old name: a write naming an option the database lacks
makes Notion create it.

**Staff reads** (an address on a Confirmed Known Team Networks row, shown as `see Known Team Networks`)
get their own group, `Firebase RTDB|<type>|staff|<outcome>`, so a teammate's read never joins an outside
reader's row. Its row has **no Category** (the categories are outside access), Actor
`Staff`, the title `<title>, Known Team Networks (<outcome>)`, and Notes naming
the matched network instead of asking whose request it was.

| Outcome | When | Outcome column | Status on create |
|---|---|---|---|
| `denied` | rules denied the read | Denied by rules | Monitoring |
| `data-accessed` | allowed, and the profiler recorded `bytes` > 0 | Data accessed | Open |
| `no-data` | allowed, and the profiler recorded `bytes` = 0 | Allowed (no data) | Monitoring |
| `obtained` | allowed, no byte count recorded | Partial impact | Open |

The title is `<title> (<outcome>)`. A sweep that mixes types (a root read then guessed names) puts each
attempt in its own group.

## Sweeps and rows

1. **Sweep:** inside a group, one IP's attempts, split where two consecutive attempts are more than 30
   minutes apart. Key: IP + sweep start (UTC minute). Each sweep is one line in `What they did`.
2. **The group's open row:** the newest row with the same `Group key`, unless its Ended is more than
   **2 days** before the sweep (Jerome, 2026-11-03). Then the group is closed, and the sweep opens a new row. A denied
   group's row holds every network's sweeps, so a new IP or a new sweep never opens a row on its own.
   **An allowed read's row must also be the same network:** the row's `IP addresses` holds the address,
   or its `Source network` names the AS (or the /16 when the AS is unknown). Otherwise the sweep opens a
   new row with the same key: each allowed row is one actor to identify, and one network's read must not
   move another network's row (ENGBACK-2146: a 11-01 AS23674 listen was appended to a 10-26 AS206092
   row). Archived (merged) rows aren't returned by the query.
   **A row a person set to `Resolved` is never written again** (Jerome, 2026-11-09): the sweep opens a new
   row with the same key, and only its attempts after the Resolved row's stored Ended go there (the earlier
   ones are on it). Every pass splits a sweep at that minute, so a later pass extends the new row's line. The Resolved row is listed in `reopened` and
   counted in `summary.resolved_reopened`. A REST burst's line already on any row of its group, the
   Resolved one included, is already applied.
3. **Already applied:** skipped, and counted in `summary.already_applied`, when:
   - the sweep's key is a line in the group row's `What they did`;
   - it is a line on a row written before grouping (no `Group key`);
   - or the group row was written by this skill (`Found by` `eng:monitor-rtdb-probes`) and the sweep
     starts in or before the minute of the row's stored Ended;
   - or the sweep starts at or before **2026-10-24 05:16Z** and the table holds the merged group rows
     (detected by then, and not created by this skill). Those rows hold every sweep up to that minute,
     some without a line of their own or in another type's row. Later sweeps are judged per group row
     only, so a partially applied plan never makes another group's sweep look logged.

   Daily profiler runs don't overlap, so the last check keeps a re-run idempotent once old lines have
   been trimmed. Re-running a plan writes nothing.

   **Extended:** a sweep whose line is found but which now holds more attempts than the line's count
   rewrites that line and adds only the new attempts. It is counted in `summary.extended`. When runs saw
   the sweep in parts, its later lines (same IP, starting inside the sweep) are folded into the first one,
   and their attempts are not counted again.
   **Covered:** a sweep whose start falls inside an existing line's span for the same IP (a run whose
   window began mid-sweep sees only its tail) is already applied up to that line's end. Later attempts
   are added and the line is stretched to hold them.
4. Sweeps of one run join rows created earlier in the same run.

## Severity

The `Severity` select is `Info` / `Low` / `Medium` / `High`, shared with `eng:flag-suspicious-traffic`. On a row it
**only goes up**.

| Sweep | Severity |
|---|---|
| denied | Low |
| denied, one IP with at least 100 attempts in the sweep (a flood) | Medium |
| allowed, 0 bytes returned (`no-data`) | Low |
| allowed: rules let someone who is neither the console nor us read non-public data (bytes returned or not recorded) | High |

**Alerts:** a row whose sweeps reach Medium or High this run is listed in the plan's `alerts`, one
entry per row: group key, severity, attempts, IP count, the readers' addresses (`ips`), times and row.
Only alerts go to Slack, as Jerome's trial; Info and Low stay in the log. The Slack post carries the
counts, never an address, and so does any alert line the session prints: `ips` is for the row
writer only, and raw IPs stay in the Incident Log.

**IP addresses** are read back from a row only as real IPv4 or IPv6 addresses (or `see Known Team
Networks`), so a dotted version in a user agent (`Chrome/140.0.0.0`, `AppleWebKit/537.36`) is never taken
for one.

**A staff address is never written** (Jerome, 2026-11-09). An address the network check found on a
Confirmed Known Team Networks row (`verdicts.json` `confirmed`) is written as `see Known Team Networks`,
in `IP addresses` and as the address of its `What they did` line, for every read of it that is logged:
allowed (it returned data, or the check alerted on it) or denied in the same pass. Its network is still
named in `Source network`; the staff reads of one group share that line's sweep key and row.
`summary.known_network_reads` counts them. The check looks up only addresses with an allowed read, so a
denied sweep from an address that made none keeps its IP.

## Columns

| Column | Create | Update |
|---|---|---|
| Incident | `<title> (<outcome>)` | kept |
| Group key | `Firebase RTDB|<type>|<outcome>` (staff: `…|staff|<outcome>`) | kept |
| Severity | the sweep's | raised, never lowered |
| Detected (UTC) / Ended (UTC) | first / last attempt, minute precision | Ended = max; Detected moves earlier only within its own UTC day |
| Category / Outcome / Status | from the tables above | kept |
| System | `Firebase RTDB` | kept |
| Requests | attempts | += attempts |
| IP addresses / Max IPs | the IPs, comma-separated / their count | union / count |
| User agent | full UA strings, one per line, `ua (n)` | counts added; entries without a count kept as they are |
| Route / path | `<paths> via REST` (or `realtime`) | missing paths appended |
| Source network | `<owner> AS<asn> <a.b>.0.0/16` (IPv6: `<hextet>::/16`), from `lookup-networks.js` | a network not yet named is appended with `; ` |
| What they did | one line per sweep | line appended |
| Found by | `eng:monitor-rtdb-probes` | kept |
| Actor | `Unknown` (staff: `Staff`) | kept, except: when `--reclassify` matches a line's network to a Known Team Networks teammate (named or not) and Actor is empty or `Unknown`, Actor becomes `Staff` and Notes gets `<date>: likely teammate <name>, not confirmed.` Actor is exactly `Staff` so the log filters on it (Jerome, 2026-11-10). A value already set is never overwritten. |
| Staff (select) | `Staff` on a staff row (staff group or Actor `Staff`), else `Not staff` | filled only when empty, on a row this skill writes; `Not staff` becomes `Staff` when `--reclassify` makes Actor `Staff`. A value a person set otherwise stays. Left out until the column exists with that option (`incident-log.js` says how many writes skipped it) (Jerome, 2026-11-11) |
| Notes | per outcome (denied: `All denied by rules; no data returned. …`); staff: the matched network | kept |
| Card, Fix / PR | empty (human) | kept |

A `What they did` line:

```
HH:MM[-HH:MM]Z <ip>: N denied unauthenticated REST reads of <paths, xN for repeats> [<ua>; <ua>]
```

prefixed with `YYYY-MM-DD ` when the sweep's UTC day is not the row's Detected day. Realtime ops read
`realtime reads`, and allowed reads say `allowed` instead of `denied`.

**Nothing is truncated that a ban needs.** Notion caps a rich_text item at 2,000 characters, so text is
split across items (1,990 characters each, up to 100). `IP addresses` and `User agent` are never trimmed:
Jerome, for Cloud Armor bans. Only `What they did` drops its oldest lines once it would pass 100 items.
`eng:flag-suspicious-traffic` writes the same way.

## Allowed attempts

- **Firebase console:** an allowed root `listener-listen` plus a 0-byte `rest-read`, both from a desktop
  browser, from one IP in one sweep, is the console's data viewer. So is the listen alone when the
  verdicts file (`eng:monitor-firebase`'s network check) puts a console account in the audit log within
  2 minutes of that address's read. It's listed in `console` and never logged.
- **A known teammate:** an address the network check attributed to an allow-listed account on a
  Confirmed or Likely Known Team Networks row is listed in `attributed` and never logged. A missing or
  unreadable verdicts file means no verdicts: the read is judged, and logged, as before.
- **A teammate testing the app:** a 0-byte allowed realtime read with no console account, from a network
  with a Confirmed row and no Not a teammate row, is in `teammate`. Like a REST burst from a Confirmed
  network, it is **never logged**: the sweep is listed in `attributed` and counted in
  `summary.teammate_reads`. A sweep from that address that returned data is still logged.
- **Our own Claude sessions:** `profile-firebase-rtdb` labels an attempt's client
  `internal (<plugin>:<skill>)` only when it was allowed and its user agent starts `AuraClaude/`. Rows and Slack
  never carry the person in `AuraClaude/<who>/…`: it is cut before writing; the local capture keeps it. A sweep
  of only such attempts goes to `allowed_flags` (`internal: true`, counted in `summary.internal_flags`)
  and is reported, never logged.
- **A whole real node** (`tag` `rule exists`) is admin access, never a probe. `eng:monitor-firebase`'s
  network check names it. It is logged, with its address, only when that check raised an ALERT on the
  address (`alert` in the verdicts file) or could not classify it at all (`fail`: its whois or Known Team
  Networks read failed): a read that might be a teammate but can't be confirmed stays in
  the log with all its information, for later comparison (Jerome, 2026-10-30). A read logged only
  because of `fail` ends its line with ` (unclassified: <reason>)`, the reason being `verdicts.json`
  `fail_reason` (for example `Notion rate-limited`) or `network check failed`. The tag is kept when a later
  pass extends the line, and `summary.unclassified_reads` counts the reads (ENG-1755). On a later pass
  where Known Team Networks answers, `eng:monitor-firebase`'s `reclassify.py` judges each tagged line's
  address, and `--reclassify` rewrites the tag to `(classified <date>: <verdict>)`: a teammate network, a
  network marked Not a teammate, or an unknown network. Listed in `reclassified`, counted in
  `summary.reclassified`; a line that changed since it was read is left for the next pass (ENG-1756). It goes to the
  `node-read` group (`RTDB whole-node reads`), `no-data`, `data-accessed` or `obtained` by its bytes. A confirmed
  teammate's, a console read and any read without an alert are counted in
  `summary.node_reads_not_logged`, never logged.
- **Anything else allowed** is logged as High in its `data-accessed` or `obtained` group and raised as an
  alert, or as Low in its `no-data` group when 0 bytes came back. A **denied** request with our user agent is an ordinary probe: anyone can send the header.

## REST bursts

Security, 2026-11-03 (ENG-1745). `eng:monitor-firebase` raises a REST-burst ALERT above 300 allowed REST
reads per 10 min. Its `rest-bursts.jsonl` (`--rest-bursts`) holds each source with 100+ reads per 10 min,
one line per IP + user agent + UTC day, with the IP, written by `profile-firebase-rtdb`'s `rest-reads.js`
while the raw log still exists: `{ip, ua, client, day, t0, t1, n, shallow, listing, root, bytes, sized,
shapes, minutes, nonpublic}`. `nonpublic` is the top-level nodes read that aren't public by rule
(`evaluate_profile.py`'s `PUBLIC`).

- **Row:** one per path family and outcome, never one per IP (Security and Jerome, 2026-11-03, ENG-1759).
  The Group key is `Firebase RTDB|rest-burst|<family>|<outcome>`, the family being the top-level node the
  source read most (`/channels`; `/` for the root). A burst joins the newest row with that key unless
  its Ended is more than 2 days before the burst (the shared group window). Every IP and user agent of
  the group is on that row, with each network named. `Max IPs` is the most distinct IPs in one pass.
  Requests and Ended are updated on each merge, Detected too but only within its own UTC day (undated
  lines are read as that day), and Severity is the highest of the merged
  bursts. Each source adds one line to `What they did`, dated when its day differs from Detected. A
  line already there, or a pass that starts at or before the stored Ended, is already applied.
- **Outcome:** `Allowed (no data)` when the profiler recorded sizes and 0 bytes came back; otherwise
  `Allowed (public data)` when every path is public by rule, and `Data accessed` (check the rule for
  those paths and raise a card) when any path is per-user or non-public, or the root. Category `Scraping / abuse`, title
  `RTDB REST read bursts (<outcome>)`, Status Monitoring except `Data accessed` (Open).
- **Severity** (only goes up on a row):

  | Burst source | Severity |
  |---|---|
  | public-by-rule paths only, no shallow, query or root reads | Low |
  | any non-public path or the root, shallow or query (listing) reads, or one IP over 1,000 reads per 10 min | Medium |
  | `/users`, a non-public path with 1,000,000+ bytes sent, or an address already on a probe row (a denied group, a High row, or a row from before grouping) | High |

- **Unclassified:** when the network check failed (`fail_reason` set), no source could be checked
  against Confirmed teammate networks, so each burst line ends ` (unclassified: <reason>)` and counts
  in `summary.unclassified_reads`.
- **Not logged:** a source on a Confirmed Known Team Networks row (`verdicts.json` `rest_teammate`, from
  the network check) is listed in `attributed`; our own sessions (AuraClaude user agent) in
  `allowed_flags`. Any other source keeps its IP, a failed network lookup included.
- `lookup-networks.js --rest-bursts` looks up the same addresses as the planner.

## Never written

uids, emails, tokens, keys or data values. The input holds none; the planner writes only the fields
above.
