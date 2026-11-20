---
name: lookup-ip-location
description: >
  Look up where one or more IP addresses are: country code and name, region, city, ISP and ASN,
  from ipgeolocation.io (the service UserService uses). Returns JSON per IP with a reason code when
  a field is unknown (no key, private address, quota, auth, network), and never blocks the caller.
  Caches per IP for the run, since each lookup costs one credit. Trigger on "look up this IP",
  "where is this IP", "geolocate an IP", "which city is this address in", "ISP for this IP", or
  "IP location". Do NOT use for whois or reverse DNS (`eng:respond-to-sar` `collect.py whois`), or
  for suspicious-traffic sweeps (`eng:flag-suspicious-traffic`). Other skills bundle a copy of `scripts/ip_location.py`
  rather than calling this skill.
metadata:
  version: "1.0.1"
knowledge:
  reads:
    - Guide/Index          # baseline routing aid (universal)
    - Tools/ipgeolocation  # endpoint, fields, quota and status codes
    - Compliance/Privacy   # an IP address is personal data
---

# lookup-ip-location

Answers "where is this address?" with country, region, city, ISP and ASN.

## Run

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/lookup-ip-location/scripts/ip_location.py 203.0.113.7 198.51.100.4
```

The output is a JSON list in input order, one object per IP:
`{country_code, country, region, city, isp, asn, reason}`. It does not repeat the IP, so pasting the
output somewhere doesn't spread the address. Map results back by position.

`reason` is `ok`, or why the fields are blank:

| reason | meaning |
|---|---|
| `no_key` | `IPGEOLOCATION_API_KEY` isn't set |
| `private` | a private or reserved address (HTTP 423) |
| `quota` | the day's credits are used up (HTTP 429; 1,000 a day on the free plan) |
| `auth` | the key was refused (HTTP 401/403) |
| `http_<n>` / `network` / `bad_json` | any other failure |

## Rules

- **Only the IP goes to the service.** Never write an address into a log line, Slack, Notion or a
  Linear card. Report the location, not the address.
- **One lookup is one credit.** Results are cached per IP for the process. Look up distinct
  addresses, not every request.
- **ISP is `asn.organization`.** ipgeolocation's `network` field is paid-only.
- **A missing key degrades, it doesn't fail.** Every field is blank with `no_key`, and the caller
  carries on. Add the key to `~/.claude/settings.json` under `env` (see `Tools/ipgeolocation`) and
  restart Claude Code.

## Using it from another skill

Skills are self-contained, so a skill that needs locations bundles its own copy of
`scripts/ip_location.py` and imports `lookup` / `lookup_many` from its own `scripts/` directory.
This skill holds the canonical copy (`docs/canonical-sources.md`); edit it first and propagate.
