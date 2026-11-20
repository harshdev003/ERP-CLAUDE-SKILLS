---
name: review-ip-blocks
description: >
  Weekly review of the Cloud Armor incident blocks (aura-web-edge rules 100-149) against the Notion
  Cloud Armor Blocklist: flags Active rows with no rule, rules with no row and rules with no
  expiry, and for each expired block counts the last 7 days of traffic from its ranges, proposing
  an extension if its traffic continues or removal if it went quiet. Changes nothing without
  Jerome's go; posts a counts-only summary to #alerts-security. Trigger on "review IP blocks",
  "expire Cloud Armor blocks", "which IP blocks have expired", "weekly blocklist review", or the
  weekly review-ip-blocks routine. Do NOT use to add a block (eng:block-ip) or to find suspicious sources
  (eng:flag-suspicious-traffic).
metadata:
  version: "1.2.1"
knowledge:
  reads:
    - Guide/Index          # baseline routing aid (universal)
    - Tools/GCP            # GCP_KEY service account, Cloud Logging
    - Tools/Notion         # Cloud Armor Blocklist
    - Tools/Slack          # #alerts-security post
    - Compliance/Privacy   # counts only; addresses stay in the Log and the Blocklist
---

# review-ip-blocks

The incident blocks `eng:block-ip` creates are deny rules at priority 100-149 in `aura-web-edge`,
each with an expiry in its description and one Active row per rule, listing its ranges, in the
Notion **Cloud Armor Blocklist** (Security). Addresses get reassigned to real users, so a block must not outlive its
reason. This review keeps the rules and the Blocklist in step and decides, with Jerome, what to do
with each expired block.

Nothing changes without **Jerome's explicit go** for that rule. Output is counts and rule numbers
only, never an address. GCP runs as `GCP_KEY` through the bundled `gcloud_sa.py`; the Blocklist
needs `NOTION_TOKEN` (without it the row checks are skipped and the summary says so).

## Workflow

### 1. Review (read-only)

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/review-ip-blocks/scripts/review.py [--days 7] [--extend-days 30]
```

It prints the summary:

- **Active rows with no rule**: a rule was removed outside the scripts; mark the rows Removed by hand.
- **Rules with no Active row**: a block nobody recorded; find its incident or remove it.
- **Rules with no expiry**: a description not in the `incident exp=<date> <row id>` shape.
- **Expired rules**: requests from its ranges in the last `--days` and how many the edge denied,
  with a proposal: `extend to <date>` when any request came, `expire` when none did.

Exit `0` means nothing needs a decision, `10` that something does, `1` a read failure (report it;
an unread review is not a clean one).

### 2. Ask for the go, then act

For each expired rule, ask Jerome: "Go to expire rule `<p>` (expired `<date>`, quiet for 7 days)?"
or "Go to extend rule `<p>` to `<date>` (`<n>` requests in 7 days)?". Then, on the go only:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/review-ip-blocks/scripts/review.py --expire <p>,<p> --apply
python3 ${CLAUDE_PLUGIN_ROOT}/skills/review-ip-blocks/scripts/review.py --extend <p> --expires <YYYY-MM-DD> --apply
```

Without `--apply` both are dry runs. `--expire` refuses a rule that is not expired; it deletes the
rule and marks its Blocklist row Expired. `--extend` refuses a date more than 90 days out; it
rewrites the rule's description and the row's `Expires (UTC)`. Each removal, expiry and extension
also writes one Security Change Log row (no range), keyed so a re-run writes none.

### 3. Post the summary

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/review-ip-blocks/scripts/review.py --post
```

Posts the counts-only summary to #alerts-security (`C0C72QYDJQK`) with the skill footer.

## Running weekly

Run step 1 with `--post` once a week. Proposals still wait for Jerome's go: a routine never
expires or extends a block on its own.

## Commands underneath

`scripts/armor_blocklist.py` (the same file `eng:block-ip` ships, adapted from AuraInfra
`bin/armor-blocklist.py`) has `list`, `remove --priority <p>` and `expire`; `review.py` uses it.
