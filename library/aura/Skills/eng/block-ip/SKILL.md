---
name: block-ip
description: >
  Block flagged IP ranges at Google's edge (Cloud Armor policy aura-web-edge, incident rules
  100-149) for one Security Incident Log row, with an expiry, and record each range in the Notion
  Cloud Armor Blocklist. Refuses without an incident row; checks every range first (public, not
  too broad, not one of our own addresses, not a teammate network, no real users behind it);
  shows a dry run and applies only on Jerome's explicit go; then confirms the edge denies at the
  new rule and notes the block on the incident row. Trigger on "block this IP", "ban {ip} in Cloud
  Armor", "block the IPs from incident X", "add a Cloud Armor block", or "block this address at
  the edge". Do NOT use to find suspicious traffic (eng:flag-suspicious-traffic), to review or expire existing
  blocks (eng:review-ip-blocks), or to change the policy's standing rules (AuraInfra
  armor/aura-web-edge.yaml).
metadata:
  version: "1.2.2"
knowledge:
  reads:
    - Guide/Index          # baseline routing aid (universal)
    - Tools/GCP            # GCP_KEY service account, Cloud Logging
    - Tools/Notion         # Security Incident Log, Cloud Armor Blocklist, Known Team Networks
    - Compliance/Privacy   # flagged source addresses only, and only in the Log and the Blocklist
---

# block-ip

You block flagged address ranges for **one incident**, for a limited time. The block is a
deny(403) rule at priority 100-149 in the `aura-web-edge` Cloud Armor policy, up to 10 ranges per
rule. The rule is **one** row in the Notion **Cloud Armor Blocklist** (Security), listing every
range and linked to its
[Security Incident Log](https://app.notion.com/p/40175e23af208180aeced339a099f573) row, and the block is
**one** row in the Security Change Log, which names no range (never one row per IP: ENG-1759). Residential,
CGNAT and cloud addresses get reassigned to real users, so every block expires (default 30 days,
at most 90); `eng:review-ip-blocks` removes it after that.

**Three rules, no exceptions:**

- **No incident row, no block.** The Incident Log is the audit trail. Find the row or ask for it.
- **Never apply without Jerome's explicit go** for this block (this incident, these ranges, this
  expiry). A go for an earlier block covers only that one. Without it, stop at the dry run.
- **Never print an address** outside the Incident Log and the Blocklist (Jerome's standing
  exception covers only those two). The scripts name a range as `#k /prefix` and pass the
  addresses through a plan file (mode 600). Never block on a user agent alone.

GCP runs as the `GCP_KEY` service account through the bundled `gcloud_sa.py`; Notion needs
`NOTION_TOKEN`. The commands are adapted from AuraInfra `bin/armor-blocklist.py`.

## Workflow

### 1. Find the incident row

The user names the row, or you find it in the Security Incident Log (eng:flag-suspicious-traffic and
eng:monitor-rtdb-probes write them). Use its page URL below. The ranges default to the row's
`IP addresses`; pass `--ranges` only when the user gives a narrower set.

### 2. Preflight (read-only)

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/block-ip/scripts/preflight.py \
  --incident "<incident row URL>" --plan /tmp/block-ip-plan.json
```

Each range gets a verdict, with its traffic over `--days` (default 7) from the LB logs:

| Verdict | Means |
|---|---|
| `REFUSE` | not public, wider than /16 (IPv4) or /48 (IPv6), or overlapping our own static external addresses or Cloud Monitoring uptime-check sources |
| `WARN` | its network is a Known Team Networks row, or it also got 2xx on our app routes or on webhook routes: a shared, NAT or CGNAT address may carry real users |
| `OK` | none of the above |

The plan holds the first 10 `OK`/`WARN` ranges for this rule (`rest` holds the others, for further
rules). A range the user wants dropped (a `WARN` they judge to be real users) is removed from the
plan's `ranges` before step 3. Exit `3` means something was refused; `2` that the incident row
could not be read. A large row takes a few minutes (one log read per 10 ranges).

### 3. Dry run, then ask for the go

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/block-ip/scripts/armor_blocklist.py add \
  --ranges-file /tmp/block-ip-plan.json --expires <YYYY-MM-DD> \
  --incident "<incident row URL>" --reason "<what they did, no personal data>"
```

It prints the rule it would create (`rule 100: deny(403) 10 range(s), incident exp=… <row id>`) and
refuses a range that an existing rule already blocks. Show the user the preflight verdicts and this
line, and ask Jerome for the go in these words:

> Go to block `<n>` range(s) from incident `<row title>` at the edge (aura-web-edge rule `<p>`),
> expiring `<date>`?

### 4. Apply (on the go only)

Re-run the same command with `--apply` (add `--card <id>` for the Change Log). It creates the rule,
writes one Security Change Log row (Component `Cloud Armor aura-web-edge`, System Infrastructure, the
Incident row linked, no range), then one Active Blocklist row listing every range (`Proposed by`
Security, `Approved by` Jerome). If a Notion write fails after the rule is live, it says so: add the
row by hand rather than re-running (a re-run refuses the same ranges). To lift one range early, edit
the row's Range list and note it; never add a row per range.

### 5. Confirm and note it on the incident row

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/block-ip/scripts/confirm.py --priority <p> \
  --since <when the rule was created, ISO UTC> --incident "<incident row URL>" --note
```

It counts enforced denies at the new priority in the LB logs and appends one line to the row's
Notes: the rule, its expiry and a link to its Blocklist rows. Exit `10` means no deny is logged
yet: the range may not have come back, so re-run later (`eng:flag-suspicious-traffic` also reports incident
rules joined to the Blocklist). A Notes field already at Notion's 100-run cap is refused, not
truncated.

### 6. Report

Incident, rule priority, range count, expiry, verdict counts from the preflight, and the deny count.
No addresses.

## Other commands

`armor_blocklist.py list` shows the live incident rules (priority, expiry, range count, row id).
`remove --priority <p> [--apply]` takes one rule down early and marks its rows Removed; it needs the
same explicit go. Expiry is `eng:review-ip-blocks`' job.
