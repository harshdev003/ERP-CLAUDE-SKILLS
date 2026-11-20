# detect-attacks rules

All rules run per client IP over one window of `http_load_balancer` entries. An IP is flagged
when any rule fires. Its `Category` is set as described under **Categories**.

| Rule | Fires when | Default | Flag |
|---|---|---|---|
| Scanner user agent | Any UA matches the tool list (Nuclei, sqlmap, nikto, masscan, zgrab, wpscan, gobuster, ffuf, Burp, ZAP, httpx, nmap, `SECURITYTEST`, …) | 1 request | — |
| Attack paths | Requests for paths/payloads no Aura client sends: `/.env`, `/.git/`, `wp-admin`, `xmlrpc.php`, `phpinfo`, `/cgi-bin/`, `../`, `/actuator`, `${jndi`, `<script`, `union select`, `/nuclei` … | 3 requests | `--attack-paths` |
| Mostly refused | ≥ 150 responses with 4xx from one IP, not counting 404s for build assets (`/_next/static/`, `/static/`), which come from pages cached before a deploy | 150 | `--min-refused` |
| Brute force | 401/403 on auth routes (`/users/signup`, `signupOrLogin`, `session`, `token`, `login`, `oauth`) ≥ 30, or any 401/403 ≥ 90 | 30 | `--auth-fail` |
| UA rotation | ≥ 3 distinct UAs, no Aura app UA, ≥ 100 requests, ≥ half refused or non-GET. A request answered 2xx counts as neither: an authenticated client posting to its API is not probing | 3 | `--ua-rotation` |
| Odd methods | ≥ 50 non-GET requests answered 404/405 (bodies ≥ 16 KB called out) | 50 | `--odd-method`, `--big-body` |
| Scraping | ≥ 300 distinct id segments (20+ char ids) in paths | 300 | `--scrape-ids` |
| Flood | ≥ 2000 error (4xx/5xx) responses to one IP. Errors only: scripted clients of our APIs send thousands of answered requests under python/curl UAs | 2000 | `--flood` |

## Grouping into campaigns

Flagged IPs are merged into one campaign when either holds (a campaign is one plan entry; rows
and Slack messages are per group, below):
- **Same requests:** identical set of method + path (host and ids stripped), at any time in the
  window. A tool or script run from several IPs.
- **Similar and at the same time:** Jaccard similarity >= 0.5 on those sets, and the IPs were
  active within 30 minutes of each other.


## Categories

Each campaign gets one category (the `Category` column and the Group key's attack type), first match wins:

| Category | Attack type | When |
|---|---|---|
| Vulnerability scanner | `vuln-scanner` | a scanner tool user agent (Nuclei, sqlmap, httpx, `SECURITYTEST`, …) |
| Brute force / credential stuffing | `brute-force` | ≥ 30 401/403 on auth routes, or ≥ 90 401/403 anywhere from an IP that sent no attack paths. 403s answering `wp-login.php`-style probes are proxy-web (nginx) refusing a scanner, so that IP is Forced browsing |
| Scraping / abuse | `scraping` | many distinct ids requested |
| Injection / fuzzing | `injection-fuzzing` | refused non-GET requests with large bodies |
| Forced browsing | `forced-browsing` | requests for known files and paths (`/.env`, `/.git/config`, `xmlrpc.php`, `wp-login.php`, VPN login pages, `/mcp`) |
| Flood / DoS | `flood` | very high volume from one IP |
| Other attack | `other` | flagged by another rule. Written to the Category column as `Other unrecognised access`, the option's name since 2026-11-10 (`Other attack` while the live list still has the old name) |

## Path families

Every request gets one family from `scripts/attack_rules.py` (`path_family`), shared by the
detector and the Incident Log writer so they name a request the same way. First match
wins; the raw and the twice-URL-decoded path and query are both tried, so `%252e%252e`, `%2e%2e`
and `../` land together.

| Family | Examples |
|---|---|
| Path traversal | `../`, `%2e%2e`, `/etc/passwd`, `win.ini` |
| Injection payloads | `${jndi`, `<script`, `union select`, `/bin/sh` |
| .env files | `/.env`, `/api/.env.production` |
| Git / VCS | `/.git/config`, `/.svn` |
| Cloud credentials | `/.aws/credentials`, `/.ssh`, `id_rsa`, metadata endpoints |
| phpMyAdmin | `/phpmyadmin`, `/pma/`, `adminer` |
| WordPress | `/wp-*`, `xmlrpc.php` |
| Backups and dumps | `*.sql`, `*.zip`, `*.tar.gz`, `backup` |
| Config files | `config.json`, `*.ini`, `/.DS_Store`, `phpinfo` |
| Actuator / API docs | `/actuator`, `swagger`, `api-docs`, `/server-status` |
| VPN and appliance exploits | Fortinet, GlobalProtect, Cisco, Citrix, `/cgi-bin/`, Jenkins, Solr |
| MCP / AI endpoints | `/mcp`, `/sse`, `/v1/models`, `/api/chat`, `/ollama` |
| PHP scripts | any other `*.php` |
| Other paths | everything else |

## Group key (shared spec)

**This section is the single source of truth for the Incident Log's grouping**, shared with
`eng:monitor-rtdb-probes` (its `references/incident-log-schema.md` implements the Firebase half).
Jerome, 2026-10-23: "same type of attack and no change in what's been gathered or leaked is the
same".

`Group key` = `<system>|<attack-type>|<outcome>`, exact strings, lowercase hyphenated type, or
`<system>|<attack-type>|<family>|<outcome>` where what was gathered differs by path family: the family
is the top-level path the attempts read (`/channels`), so bursts reading different data are different
groups (Jerome, 2026-11-03). Only REST bursts use the family part today:
`Firebase RTDB|rest-burst|/channels|public-data`. A hand-written row uses the same shape, or it won't
merge with the writers' rows.

| System | Attack types | Outcomes |
|---|---|---|
| `Web` (this skill) | `forced-browsing`, `vuln-scanner`, `brute-force`, `injection-fuzzing`, `scraping`, `flood`, `other` (Categories above); `reported-pentest` (testing that was reported to us) is hand-written | `refused`, `obtained`, `data-accessed`; `unknown` is hand-written |
| `Firebase RTDB` (`eng:monitor-rtdb-probes`) | `root-read`, `guessed-name`, `node-read`; `rest-burst` (with a path family); `privilege-escalation` (a write that grants a role) is hand-written | `denied`, `obtained`, `data-accessed`, `no-data`; for `rest-burst` also `public-data`; `unknown` is hand-written |

| Outcome | Web | Firebase RTDB | Outcome column |
|---|---|---|---|
| `refused` | every request got 3xx/4xx/5xx, or only public pages and assets answered 2xx | — | No impact (refused / 404) |
| `denied` | — | rules denied the read | Denied by rules |
| `obtained` | a non-public route answered 2xx (see "Public routes") | allowed, no data recorded | Partial impact |
| `data-accessed` | a sensitive-family path answered 2xx with a body (`responseSize` > 0) | allowed, `bytes` > 0 | Data accessed |
| `unknown` | the action succeeded or may have, but its effect can't be determined from the available logs; the gap is stated in Notes | same | set by hand |

**Soft 404s are `refused`.** A web app can answer a probe like any other page: CoachWeb serves its
app shell for `/live/.env` (200 text/html), and `/?phpinfo=1` gets the home page. On web hosts
(never `services.aurahealth.io`), a sensitive-family GET answered 2xx whose `responseSize` is within
±10% of either the same path's usual 200 size or the host's usual HTML page size, both measured in
the same window from the LB log, is a soft 404: it counts as refused, not `obtained` or
`data-accessed`, and the plan counts it in `soft_404`. Only a clearly different size (a small text
body like a real `.env`) escalates. Sizes are as the LB logs them (compressed, so the 44 KB CoachWeb
shell logs at about 13.5 KB); `detect.py` reads the baselines with a few extra queries, only when
such a 2xx exists: the median of up to 200 ordinary 200 GETs per host (assets, `/auraServices/`,
`/api/`, `/status`, `/ready`, `/health` and RSC requests excluded) and up to 50 per path. With no
baseline (no ordinary traffic in the window) the hit stays escalated. On 2026-10-23 all four web
2xx on sensitive paths were soft 404s (13,502 B vs 13,870 B on coach; `/?phpinfo=1` 37,917 B vs
36,900 B for `/`).

`data-accessed` that survives the guard is still a strong hint, not proof. It is High either way.

**Writer rule (both skills).** Find the open, non-archived row with the same Group key and update it:
union of IPs and user agents, requests added, latest Ended, Max IPs, severity only raised. Create a
row only for a key with no open row. **A group is open until 2 days pass with no hits** (Jerome, 2026-11-03); the next
hit after that starts a new row. Archived (merged) rows are not returned by queries. If `Group key`
or `Severity` (with its four options) is missing, the writer refuses: exit 2, no schema change.

## Severity

`Severity` select: `Info`, `Low`, `Medium`, `High`. On a row and on a Slack group it **only goes up**
until the group ends. Our own traffic (allow-list below) is removed before scoring and never has one.

| Severity | Web (this skill) | Firebase RTDB |
|---|---|---|
| Info | same type as an existing group, nothing obtained (`refused`) | — |
| Low | a category or path family not seen in the last 14 days (`--history-days`), still `refused` | denied |
| Medium | brute force / credential stuffing over the auth-fail threshold; a flood that moved the LB (below) | one IP with ≥ 100 denied attempts in one sweep |
| High | `obtained` or `data-accessed`; an answered sign-in after refused ones (below) | allowed read of non-public data |

**Flood (web):** ≥ 2000 error responses to one IP (`--flood`) flags it; it is Medium only when it
moved the load balancer: LB-wide 5xx per minute while active ≥ 5 (`--flood-5xx-per-min`) and ≥ 3× the
rest of the window, or the campaign's median latency ≥ 2 s (`--flood-latency`). Every 5xx passes
the pre-filter, so the 5xx count is complete; latency covers only the campaign's own requests.

**Sign-in after brute force (heuristic).** For IPs flagged as brute force, `detect.py` runs one more
query for their answered (2xx) requests on auth routes, which the pre-filter drops. A write (POST, PUT, …) answered
2xx on an auth route after that IP's first 401/403 there is High; loading `/login` as a page is not. Limits: the LB cannot tell a session from a 200
error page or a signup; UserService's `/users/*` routes are not in the LB log at all (blind spot
below); only the first 20 brute-force IPs of a window are checked.

## Public routes

A 2xx tells an attacker nothing on a GET or HEAD to any host the load balancer fronts except
`services.aurahealth.io` (web pages, static assets, `robots.txt`; bare-IP requests reach the web
default backend). Not public: anything on `services.aurahealth.io`, any write (POST, PUT, PATCH,
DELETE), and a GET whose family is sensitive (traversal, injection, `.env`, Git, cloud credentials,
backups, config, phpMyAdmin, actuator) even on a web host. A CORS preflight (OPTIONS 204) is not a
success.

## Incident Log rows (web)

One row per open group (Writer rule above). Per run, `apply.py`:

- counts requests **by minute** per IP and per user agent (`groups.json` keeps the last minute
  counted), so the 5-minute overlap of hourly windows and a retried plan never count twice; on a
  row it has not counted yet, only minutes after the row's Ended count;
- writes `IP addresses` as `ip (n req, last YYYY-MM-DDTHH:MMZ)` per line and `User agent` as
  `ua (n)` per line, the full union, never trimmed (Cloud Armor bans need them). An older entry
  without a count keeps no count; it is not guessed;
- writes `Source network` as the /24s with IP and request counts;
- appends one `What they did` line per campaign (oldest lines dropped past 100 items: narrative,
  not ban data), and the top routes to `Route / path`;
- creates with `Category`, `System`, `Outcome`, `Status` (`Monitoring` for refused, `Open`
  otherwise), `Found by` `eng:detect-attacks (load-balancer logs)`; keeps them on update.

**Ban data never truncates.** Notion caps a rich_text item at 2,000 characters and a property at 100
items. Text is split at line ends into 1,990-character items. Past 100 items, the first 99 stay and
the last item reads `… N more in Notion page <id> (IP addresses)`: a child page of the row, "Ban
data (IPs and user agents)", holds the full union as JSON. Each update reads it back, merges, writes
a new child page and archives the old one.

`blocklist.py` exports the open rows' ban data (IPs, /24 CIDRs, user agents, counts, last seen) as
JSON or CSV, read-only, our own user agents excluded.

## Slack

**Threshold:** `--slack-min-severity` / `DETECT_ATTACKS_SLACK_MIN_SEVERITY`, default `medium` (a
trial, Jerome 2026-10-24). Groups below it go to the Incident Log only. Every message starts with
`[INFO]`, `[LOW]`, `[MEDIUM]` or `[HIGH]`.

**Same group:** same Group key (attack type + outcome), whatever the IPs or /24s. A Slack group ends
after `--gap-hours` (3) with no hits, and at the UTC day boundary, so one message covers at most one
day.

| Event | Slack |
|---|---|
| first hit of a group at or above the threshold | new post (Medium and High: + @mention if configured; High and all others: + Incident Log row link) |
| more hits, same group, same severity | a threaded reply under the group message with the group's totals: requests, IPs and /24s, families, statuses, first/last seen. No ping |
| a new path family joins the group, or this run's requests ≥ 10× the last run's (and ≥ 100) | the same threaded reply, with a line saying what changed. No channel ping |
| the group's severity rises | new post at the new level ("Escalated from [LOW] earlier message" with a link); later replies go under it |
| severity of new hits is lower | threaded reply only; the group keeps its highest level |
| the group ended (gap or new UTC day) | the next hit is a new post |

Novelty is part of severity: a type unseen for 14 days makes the group Low, so at threshold `low`
it posts (or escalates an Info group). Seen types are recorded only after the run's Slack step for
that group succeeded, so a failed post raises the same severity next run.

**Never edit or delete a message.** By counsel's instruction (2026-11-06), no existing message in
#security-alerts or any other incident channel is edited or deleted: no `chat.update`, no
`chat.delete`. A group's later counts are new threaded replies (`chat.postMessage` with `thread_ts`).

**Failures.** A failed post or reply is not recorded and is retried next run; a partial one that
landed is recorded, so it is never posted twice. A Notion failure never holds back the Slack step.

**Mention:** `--mention-user` / `DETECT_ATTACKS_MENTION`, a Slack user id (or a literal `<!subteam^…>`).
Unset means no mention. Look one up with `users.lookupByEmail`.

## Server-side pre-filter

Prod serves ~150k load-balancer requests an hour and Cloud Logging allows 60 read pages a minute,
so `detect.py` asks GCP only for traffic a rule can act on: 4xx/5xx responses, scanner or scripting
user agents, and attack paths (`SUSPECT_LQL`). Totals in the report are suspicious requests, not all
traffic. `--full` reads everything — short windows only — and is the only mode where the scraping
rule sees successful GETs. Quota errors are retried after 60 s, resuming from the last timestamp.

## Exemptions

- Known crawlers and probes (Googlebot, bingbot, Applebot, GoogleHC, kube-probe, UptimeRobot, link
  unfurlers) are exempt from the **volume** rules only. A crawler UA that hits an attack path is
  still flagged — UAs are free to spoof.
- An IP whose traffic includes the Aura app UA (`Aura/…`, `CFNetwork`) is exempt from UA rotation:
  one phone legitimately sends several app and WebView UAs, and office NATs mix many.

## Our own traffic

Exempt before any rule runs, and counted in the report and plan (`own_traffic`) rather than
silently dropped. Matched on user agent **and** route, never IP alone (cloud IPs rotate). Only
answered requests (< 400) are exempt, so a brute force wearing the same user agent on these routes
still shows up as 401/403s. Entries live in `attack_rules.OWN_TRAFFIC`:

| Name | User agent | Host | Routes | Added |
|---|---|---|---|---|
| LiveKit voice agent | `AsyncOpenAI/Python x.y` or `OpenAI/Python x.y` | `services.aurahealth.io` | `/ai/openai/responses`, `/ai/openai/models`, `/ai/openai/chat/completions`, `/ai/conversations/:id/metrics` | 2026-10-24: Oracle Cloud IPs (150.136.115.37, 129.213.103.212, 129.213.38.225) were logged as "Other attack" by UA rotation |

## Known blind spot

The load balancer does not log `services.aurahealth.io/users/*` (UserService). Attempts on those
routes have no IP in GCP; check the UserService container logs by UA when the user asks about them.

## Cloud Armor (`armor.py`)

The `aura-web-edge` policy writes its verdict on every response it evaluated. detect.py reads it
twice: on every row it already reads (where each campaign was stopped), and in a second query for
denies and over-limit rate rules across all traffic. Counts only, same counting as AuraInfra
`bin/armor-report.py`.

| Field | Meaning |
|---|---|
| `jsonPayload.statusDetails` | `denied_by_security_policy`: the edge answered. `response_sent_by_backend`: the app did |
| `jsonPayload.enforcedSecurityPolicy` | `outcome` (ACCEPT/DENY), `priority` (a float in the log), `configuredAction`, `preconfiguredExprIds`, `matchedFieldType`, `rateLimitAction.{key,outcome}` |
| `jsonPayload.previewSecurityPolicy` | same shape, for rules in preview: what the edge *would* have done |

What the report adds:

1. **Edge per campaign**: `edge: N blocked by Cloud Armor (rules …), M reached the backend`. Only an
   enforced deny or `denied_by_security_policy` counts as blocked; a preview deny reached the app.
2. **Denies by rule**: enforced and preview, by priority, signature, matched field type, backend
   and a masked path (two short lowercase segments, anything else `:x`).
3. **Bans and saturated throttles**: an **enforced** `BAN_THRESHOLD_EXCEED` (IP ban) or
   `RATE_LIMIT_THRESHOLD_EXCEED` (throttle at its cap; rule 3100's JA4 key, rule 4000's per-IP key)
   is an `ALERT`. It makes detect.py exit 10, and `apply.py` posts one Medium message per rule and
   UTC day (no Incident Log row). Preview rate rules are reported, never alerted.
4. **Own-caller denies**: a deny on an app user agent (`Aura/`, `AuraConsumerApp/`, `Aurie/`) is a
   likely false positive only when the same address also got a backend 2xx in the window (checked
   for up to 20 addresses). The user agent alone is a label, never proof.
5. **Policy coverage per backend**: the share of the newest `--coverage-sample` responses (default
   2,000) that carry `enforcedSecurityPolicy`.
6. **Incident block rules** (priorities 100–149) joined to the Notion Cloud Armor Blocklist (under
   Security) by Rule priority: hits, Status, expiry and the linked incident. Flags a rule that
   denies with no row, a rule still denying while its row is Expired or Removed, and an Active row
   with no hits. Needs `NOTION_TOKEN`; without it the counts stand and the report says so.

**Never kept:** VERBOSE `matchedFieldValue` / `matchedFieldName` (user input, cookies), a
rate-limit key (an address for a per-IP rule; hashed and only counted), a Blocklist row's Range or
Reason. A failed Cloud Armor read prints `read FAILED` and the plan carries `armor.error`: missing,
never clean. `--no-armor` skips it; `--armor-limit` (default 50,000) caps the read.

## Tuning

Raise a threshold flag rather than adding exemptions in code. If a recurring false positive needs a
code exemption, add it here with the reason and the date.
