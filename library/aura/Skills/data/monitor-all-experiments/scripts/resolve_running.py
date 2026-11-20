#!/usr/bin/env python3
"""Resolve the set of A/B experiments that are currently running.

Fetches the whole Aura API experiment catalog in one request, keeps rows that
are neither ended nor deleted, and classifies each by target app. This is the
authoritative running set for `data:monitor-all-experiments` — spreadsheet cell
colour is not readable through the Sheets API and a stale sheet disagrees with
the API.

`?limit=N` is the only query knob that reliably returns the full catalog, so
the request asks for far more rows than exist and asserts the response came
back short of the limit. Walking `?page=` truncates: the catalog contains
repeated pages, so a loop that stops when a page yields no unseen ids drops
every page after the first repeat — measured at 100 missing experiments against
a single large-limit fetch.

Auth: AURA_API_TOKEN, read from the environment or from ~/.claude/settings.json
under `env`. Endpoint reference: `Tools/Aura API`.

Usage:
    python3 resolve_running.py                 # table
    python3 resolve_running.py --json          # machine-readable
    python3 resolve_running.py --app aurie     # aurie | aura
    python3 resolve_running.py --since 2026-01-01
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import pathlib
import re


def _ua_who() -> str:
    """Login name of whoever runs this, for the user agent: AINATIVE_UA_WHO, else the global git email's local part, git user.name, the OS account, or "unknown"."""
    who = re.sub(r"^\d+\+", "", os.environ.get("AINATIVE_UA_WHO", "").strip().partition("@")[0])
    if not who:
        if not hasattr(_ua_who, "found"):
            import getpass, subprocess
            found = ""
            for key in ("user.email", "user.name"):
                try:
                    val = subprocess.run(["git", "config", "--global", key], capture_output=True, encoding="utf-8", errors="replace", timeout=5).stdout.strip()
                except (OSError, subprocess.SubprocessError):
                    val = ""
                found = re.sub(r"^\d+\+", "", val.partition("@")[0]) if key == "user.email" else val
                if found:
                    break
            if not found:
                try:
                    found = getpass.getuser()
                except Exception:
                    found = ""
            _ua_who.found = found
        who = _ua_who.found
    return re.sub(r"[^A-Za-z0-9._-]+", "-", who).strip("-.") or "unknown"


def user_agent() -> str:
    """`AuraClaude/<who>/<plugin>:<skill>@<version> (session <id>)` -- labels this skill's calls to RTDB, GCP and the Aura API.

    Same format as gcloud_sa.user_agent(). A label, not proof: anyone can send it.
    """
    skill = pathlib.Path(__file__).resolve().parents[1]
    root = skill.parents[1]
    try:
        plugin = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["name"]
    except (OSError, ValueError, KeyError, TypeError):
        plugin = root.name
    try:
        front = (skill / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[1]
        version = re.search(r'version:\s*"([^"]+)"', front).group(1)
    except (OSError, IndexError, AttributeError):
        version = "unknown"
    session = os.environ.get("AINATIVE_SESSION_NAME") or (os.environ.get("CLAUDE_CODE_SESSION_ID") or "")[:8]
    session = re.sub(r"[^A-Za-z0-9._-]+", "-", session).strip("-") or "none"
    return f"AuraClaude/{_ua_who()}/{plugin}:{skill.name}@{version} (session {session})"


API = "https://services.aurahealth.io/experiments"
CATALOG_LIMIT = 10000


def load_token() -> str:
    token = os.environ.get("AURA_API_TOKEN", "").strip()
    if token:
        return token
    settings = os.path.expanduser("~/.claude/settings.json")
    try:
        with open(settings, encoding="utf-8") as fh:
            return json.load(fh).get("env", {}).get("AURA_API_TOKEN", "").strip()
    except (OSError, ValueError):
        return ""


def fetch_all(token: str) -> list[dict]:
    req = urllib.request.Request(
        f"{API}?limit={CATALOG_LIMIT}",
        headers={"Authorization": f"Bearer {token}", "User-Agent": user_agent()},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            catalog = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        raise SystemExit(
            f"Aura API returned {exc.code}. 401/403 means the token is missing "
            "or lacks scope; the skill falls back to the sheets for context only."
        )
    except urllib.error.URLError as exc:
        raise SystemExit(f"Could not reach the Aura API: {exc.reason}")

    if len(catalog) >= CATALOG_LIMIT:
        raise SystemExit(
            f"The catalog returned {len(catalog)} rows, at or above the "
            f"{CATALOG_LIMIT} limit, so it may be truncated and the running set "
            "would be incomplete. Raise CATALOG_LIMIT and re-run."
        )

    unique: dict[str, dict] = {}
    for exp in catalog:
        unique.setdefault(exp.get("id"), exp)
    return list(unique.values())


# A feature flag is stored as an experiment record with a single-letter `active` (the "ended, winner"
# shape), so every ended-experiment rule would act on it: remove its code, archive the record, analyse
# it. It is marked by `featureFlag: true`, or, while ExperimentService doesn't store that field, by
# `notes` (or `description`) starting "FEATURE FLAG". Skipped and counted, never dropped silently.
FEATURE_FLAG_PREFIX = "FEATURE FLAG"


def is_feature_flag(row) -> bool:
    if not isinstance(row, dict):
        return False
    if row.get("featureFlag") is True:
        return True
    return any(str(row.get(f) or "").lstrip().upper().startswith(FEATURE_FLAG_PREFIX)
               for f in ("notes", "description"))


def app_of(exp: dict) -> str:
    target = exp.get("targetApp")
    if target == "ai":
        return "aurie"
    if target is None:
        return "aura"
    return f"unknown:{target}"


def surface_of(exp: dict) -> str:
    """Surface is app plus platform, matching how `data:sync-experiments` maps it.

    `targetPlatform == "web"` is authoritative when set, but it is not always
    populated: `aurieNoTrialWeb` is a web experiment with a null platform. So a
    `web`-prefixed or `-web`-suffixed id counts as a second signal, and the
    caller still cross-checks the web sheet tabs. Surface drives which metric
    contract applies, so a wrong answer here produces rates for the wrong
    population.
    """
    app = app_of(exp)
    eid = str(exp.get("id") or "").lower()
    web = exp.get("targetPlatform") == "web" or eid.startswith("web") or eid.endswith("web")
    return f"{app}-{'web' if web else 'mobile'}"


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="emit JSON")
    ap.add_argument("--app", choices=["aurie", "aura"], help="filter by app")
    ap.add_argument(
        "--surface",
        choices=["aurie-mobile", "aurie-web", "aura-mobile", "aura-web"],
        help="filter by surface. Surface, not app, selects the metric contract",
    )
    ap.add_argument(
        "--since",
        metavar="YYYY-MM-DD",
        help="drop experiments created before this date; excludes legacy rows "
        "that were never formally end-dated",
    )
    ap.add_argument(
        "--splitting",
        action="store_true",
        help="keep only experiments whose `active` names more than one variant. "
        "A single-letter `active` means the app ships that variant, so traffic "
        "is not splitting and the experiment is over in all but the record",
    )
    args = ap.parse_args()

    token = load_token()
    if not token:
        raise SystemExit(
            "AURA_API_TOKEN is not set. Add it to ~/.claude/settings.json "
            "under `env`, or export it before running."
        )

    not_ended = [e for e in fetch_all(token) if e.get("endedAt") is None and e.get("deletedAt") is None]
    flags = sorted(str(e.get("id")) for e in not_ended if is_feature_flag(e))
    running = [e for e in not_ended if not is_feature_flag(e)]
    for exp in running:
        exp["app"] = app_of(exp)
        # By length, so a repeated-letter `active` counts as splitting.
        # `newPricingPage` and `placeholder` carry `active="bb"` — two
        # characters, one distinct arm — and the catalogue does not say which
        # was meant. A monitor errs toward showing the row; `sync-experiments`'
        # `reconcile.py` holds the same two for a human, because it writes.
        exp["splitting"] = len(str(exp.get("active") or "")) > 1
        exp["surface"] = surface_of(exp)
    if args.app:
        running = [e for e in running if e["app"] == args.app]
    if args.since:
        running = [e for e in running if (e.get("createdAt") or "")[:10] >= args.since]
    if args.surface:
        running = [e for e in running if e["surface"] == args.surface]
    if args.splitting:
        running = [e for e in running if e["splitting"]]

    running.sort(key=lambda e: (e["app"], not e["splitting"], e.get("createdAt") or ""))

    if flags:  # stderr, so --json stays the list it always was
        print(f"skipped.featureFlag {len(flags)}: {', '.join(flags)} (feature flags, not experiments)",
              file=sys.stderr)
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "id": e.get("id"),
                        "app": e["app"],
                        "active": e.get("active"),
                        "splitting": e["splitting"],
                        "surface": e["surface"],
                        "createdAt": e.get("createdAt"),
                        "minVersion": e.get("minVersion"),
                        "targetPlatform": e.get("targetPlatform"),
                        "targetLocales": e.get("targetLocales"),
                        "preventNewUsers": e.get("preventNewUsers"),
                        "preventOldUsers": e.get("preventOldUsers"),
                        "notes": e.get("notes"),
                    }
                    for e in running
                ],
                indent=2,
            )
        )
        return

    if not running:
        print("No running experiments matched.")
        return

    width = max(len(str(e.get("id"))) for e in running)
    print(f"{'experiment'.ljust(width)}  surface       arms  split  created     minVer")
    print("-" * (width + 52))
    for e in running:
        print(
            f"{str(e.get('id')).ljust(width)}  "
            f"{e['surface'].ljust(12)}  "
            f"{str(e.get('active') or '-').ljust(4)}  "
            f"{('yes' if e['splitting'] else 'NO').ljust(5)}  "
            f"{(e.get('createdAt') or '')[:10]}  "
            f"{e.get('minVersion') or '-'}"
        )
    split = sum(1 for e in running if e["splitting"])
    print(f"\n{len(running)} not ended  ({split} splitting traffic, {len(running) - split} shipping one variant)")
    web = sum(1 for e in running if e["surface"] == "aurie-web")
    if web:
        print(
            f"{web} are on the aurie-web surface. The metric contract does not "
            "cover it, so report those and do not measure them."
        )
    print("Splitting is a necessary condition, not a sufficient one — an experiment")
    print("can carry a multi-letter `active` and still serve nobody. Count assigned")
    print("users per arm before analysing any of these.")


if __name__ == "__main__":
    main()
