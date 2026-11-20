#!/usr/bin/env python3
"""Where an IP address is: country, region, city, ISP and ASN, from ipgeolocation.io v3.

  ip_location.py <ip> [<ip> ...]      JSON list, one object per IP, in input order

As a module: `lookup(ip)` or `lookup_many(ips)` -> {ip: result}. Each result has
`country_code, country, region, city, isp, asn` (blank when unknown) and `reason`:

  ok        found
  no_key    IPGEOLOCATION_API_KEY is not set
  private   a private or reserved address (HTTP 423); nothing to locate
  quota     daily credits used up (HTTP 429); the free plan has 1,000 a day
  auth      the key was refused (HTTP 401/403)
  http_<n>  any other HTTP status
  network   the request failed
  bad_json  the body was not the expected shape

Never raises, so a caller keeps going without a location. Only the IP goes to the
service. The CLI output carries no IP: callers map results by position, and nothing
here writes an address to a log, Slack or Notion. Results are cached per IP for the
process (one lookup is one credit). The key comes from the environment and is never
printed. The `network` field is paid-only, so ISP is `asn.organization`.

Canonical copy: eng:lookup-ip-location. Other skills carry byte-identical copies
(docs/canonical-sources.md).
"""
from __future__ import annotations

import json
import os
import sys

URL = "https://api.ipgeolocation.io/v3/ipgeo"
FIELDS = ("country_code", "country", "region", "city", "isp", "asn")
_CACHE: dict[str, dict] = {}


def _blank(reason: str) -> dict:
    return {**{k: "" for k in FIELDS}, "reason": reason}


def _parse(body) -> dict:
    if not isinstance(body, dict):
        return _blank("bad_json")
    loc, asn = body.get("location") or {}, body.get("asn") or {}
    if not isinstance(loc, dict) or not isinstance(asn, dict):
        return _blank("bad_json")
    return {
        "country_code": loc.get("country_code2") or "",
        "country": loc.get("country_name") or "",
        "region": loc.get("state_prov") or "",
        "city": loc.get("city") or "",
        "isp": asn.get("organization") or "",
        "asn": str(asn.get("as_number") or "").removeprefix("AS"),
        "reason": "ok",
    }


def _get(ip: str, key: str) -> dict:
    try:
        import requests
    except ImportError:
        return _blank("network")
    try:
        r = requests.get(URL, params={"apiKey": key, "ip": ip}, timeout=20)
    except requests.RequestException:
        return _blank("network")
    if r.status_code == 423:
        return _blank("private")
    if r.status_code == 429:
        return _blank("quota")
    if r.status_code in (401, 403):
        return _blank("auth")
    if r.status_code != 200:
        return _blank(f"http_{r.status_code}")
    try:
        return _parse(r.json())
    except ValueError:
        return _blank("bad_json")


def lookup(ip: str) -> dict:
    ip = (ip or "").strip()
    if ip in _CACHE:
        return _CACHE[ip]
    key = os.environ.get("IPGEOLOCATION_API_KEY", "").strip()
    if not key:
        return _blank("no_key")  # not cached: the key may be set later in the run
    out = _get(ip, key) if ip else _blank("private")
    if out["reason"] not in ("quota", "network"):
        _CACHE[ip] = out
    return out


def lookup_many(ips) -> dict[str, dict]:
    return {ip: lookup(ip) for ip in dict.fromkeys(ips)}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ips = sys.argv[1:]
    if not ips:
        sys.exit("usage: ip_location.py <ip> [<ip> ...]")
    print(json.dumps([lookup(ip) for ip in ips], indent=1))


if __name__ == "__main__":
    main()
