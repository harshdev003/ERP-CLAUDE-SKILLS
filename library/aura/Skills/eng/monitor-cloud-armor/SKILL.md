---
name: monitor-cloud-armor
description: >
  Watch the Cloud Armor aura-web-edge policy (proxy-web, user-web, coach-web and the marketing
  hosts) for real users or our own apps being denied or throttled, read-only. Three modes: a
  5-minute watch that flags and exits 1 on a real-user signal, a daily 24 h summary against the
  day before, and before/after counts for a rule change with re-checks that link previews,
  search crawlers, our apps and /.well-known/ still get through. Counts and masked route shapes
  only. Trigger on "monitor cloud armor", "watch the armor rules", "is cloud armor blocking real
  users", "cloud armor daily summary", "did the rule change block anyone", "compare before and
  after the armor change", or "run the enforce watch". Do NOT use to find outside probing or
  write the Security Incident Log (eng:flag-suspicious-traffic), to add a block (eng:block-ip), or to
  review existing blocks (eng:review-ip-blocks).
metadata:
  version: "1.1.0"
knowledge:
  reads:
    - Guide/Index        # baseline routing aid (universal)
    - Tools/GCP          # Cloud Logging and Cloud Armor through the GCP_KEY service account
    - Compliance/Privacy # counts and route shapes only; never an address
---

# Monitor Cloud Armor

## Goal

Tell the running session, within one window, when a Cloud Armor rule on `aura-web-edge` starts
denying or throttling **our own users or apps**. It checks that our rules don't hurt our users;
finding outside probing is `eng:flag-suspicious-traffic`'s job. It is read-only: it never changes a
policy, writes no Notion rows and posts nothing to Slack. A flag is reported to the session
running it, which tells Lead about any real-user impact.

## Rules

- **Counts and masked route shapes only.** Never print an IP, user id, email, token or full URL.
  Routes print as `<host label> /<segment>/<segment>` with ids as `:x`; user agents with digits
  stripped. Addresses are used in memory only, for the scanner set and distinct counts.
- **Rule numbers, the crawler list and the thresholds live in
  [`references/rules.md`](references/rules.md)**, not in the script. Each run reads the live
  policy and warns about any priority that file doesn't list: add it there, don't guess.
- **A failed read is exit 2, never clean.** Auth is `GCP_KEY` (Logs Viewer and
  `compute.securityPolicies.get`) in a private `CLOUDSDK_CONFIG`, through the bundled
  `gcloud_sa.py`, with the AuraClaude user agent.

## Step 0 — Self-test

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/monitor-cloud-armor/scripts/armor.py selftest
```

Runs the checks on [`assets/fixture-log.json`](assets/fixture-log.json) with no GCP call. It must
print `selftest ok`; anything else means the checks changed, so stop and say so.

## Step 1 — Pick the mode

**watch** (the loop): one line per 5-minute window for `--checks` windows (12 = 1 h, 2016 = 7 days).

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/monitor-cloud-armor/scripts/armor.py watch --checks 12
```

Each line gives 5xx outside `/blog`, denies per rule, and for rule 4000 the distinct-IP count,
top masked routes and user agents. It prints `FLAG … flagged=[…]` and exits 1 when:

| Check | Flags when |
|---|---|
| `app_ua` | any deny by a rule other than 200 has one of our apps' user agents (Aura, Aurie) |
| `api_throttle` | rule-4000 throttles on `/api` or `/auraServices` from real clients exceed the threshold |
| `coach_deny` | coach-web denies by rules other than 200 exceed it (a scanner's rule-4000 hits don't count) |
| `static_throttle` | rule-4000 throttles of www static assets from real clients exceed it (ENGBACK-2502) |
| `e5xx` | 5xx outside `/blog` (Webflow) exceed it |

**daily**: the last 24 h against the 24 h before. One line if clean; three if flagged.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/monitor-cloud-armor/scripts/armor.py daily
```

**compare**: the effect of a rule change. Two windows of `--hours` (default 24), starting at
`--before` and `--after`. For the rule's match (`--match-ua` and/or `--match-path`, RE2 as in
the rule) it counts served vs denied, plus any `--count <path regex>`, and re-checks that
nothing on the must-not-deny list (link previews, AASA-Bot, search engines, our apps,
`/.well-known/`) was denied.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/monitor-cloud-armor/scripts/armor.py compare --rule 3390 \
  --before 2026-11-08T00:00:00Z --after 2026-11-09T00:00:00Z --match-ua '(?i)bot|crawl|spider' --count '^/track'
```

## Step 2 — Report

- **Clean**: one line per window, or the daily line. Nothing to escalate.
- **Flagged (exit 1)**: name the check, its count against the threshold, the rule numbers in
  `denies`, and the masked routes. Tell Lead if real users or our apps were hit; the policy owner
  decides on any rule change.
- **Exit 2**: say the read failed and why (the message names the credential). Never report clean.
- **A warning about an unknown rule**: add the rule to `references/rules.md` in a PR.

## Edge cases

- **The read limit** (`log_limit`, 20,000 entries) applies per hour: long windows are read an
  hour at a time. An hour that still hits it prints a warning that its counts are a floor.
- **Preview rules** log `previewSecurityPolicy`, not a deny; they never count here.
