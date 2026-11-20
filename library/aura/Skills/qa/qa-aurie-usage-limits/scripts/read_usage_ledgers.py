#!/usr/bin/env python3
"""Read AuraConsumerApp's persisted usage-limit ledgers off a physical device.

The limiter stores its counters in UserDefaults inside the app container, so this
pulls Library/Preferences via ios-deploy's house-arrest and decodes the two
JSON-encoded records. Reading here rather than from the UI matters: the UI can be
right while the persisted ledger is wrong, and the ledger is what gates the user.

Requires a development-signed build (house arrest only works for those) and
ios-deploy on PATH (`brew install ios-deploy`).

Every read is pinned to one user. A device keeps the records of every account that
has ever signed in on it, and they are indistinguishable by shape — so folding them
together yields one user's free counters beside another's package counters, which
looks like a plausible baseline and is not. The signed-in user is resolved from
`BugsnagUserUserId` (which the app rewrites on login) and any other UIDs present are
reported as `staleUserIds` rather than merged.

Usage:
    python3 read_usage_ledgers.py --udid 00008150-000025293C42401C
    python3 read_usage_ledgers.py --udid <udid> --uid <firebaseUserId>
    python3 read_usage_ledgers.py --udid <udid> --json
"""

from __future__ import annotations

import argparse
import json
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BUNDLE_ID = "com.aurahealth.AuraConsumerApp"
DEBUG_FLAG_KEY = "debugDisableUsageLimits"
# The app sets this on login, so it names the currently signed-in user even when the
# container still holds records for accounts that have since logged out.
CURRENT_USER_KEY = "BugsnagUserUserId"


def pull_preferences(udid: str, bundle_id: str, timeout: int) -> Path:
    """Download the app container's Library/Preferences to a temp dir."""
    dest = Path(tempfile.mkdtemp(prefix="aurie-usage-"))
    proc = subprocess.run(
        [
            "ios-deploy",
            "--id", udid,
            "--bundle_id", bundle_id,
            "--download=/Library/Preferences",
            "--to", str(dest),
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    plist = dest / "Library" / "Preferences" / f"{bundle_id}.plist"
    if not plist.exists():
        shutil.rmtree(dest, ignore_errors=True)
        detail = (proc.stderr or proc.stdout or "").strip()[-400:]
        raise SystemExit(
            f"Could not read the app container.\n"
            f"Check: device paired and unlocked, app installed from a development-signed\n"
            f"build, and the UDID is the iOS UDID (from `ios-deploy -c`) rather than the\n"
            f"devicectl identifier.\n\nios-deploy said:\n{detail}"
        )
    return plist


def decode(plist_path: Path, pinned_uid: str | None = None) -> dict:
    """Pull one user's usage records out of the preferences plist.

    Values are JSON blobs stored as Data, under per-user keys
    (`usageLimits_<userId>_chatMessageUsage` / `_packageUsage`). `pinned_uid` selects
    which user to read; without it the signed-in user is taken from
    ``CURRENT_USER_KEY``, and failing that from the records themselves when they all
    belong to one user. Records for any other user are counted in `staleUserIds` and
    never merged into the result.

    `readAt` is stamped after the pull has already completed, so the data it labels
    predates it — see references/interpreting-results.md.
    """
    with open(plist_path, "rb") as handle:
        prefs = plistlib.load(handle)

    ledger_keys: dict[str, list[tuple[str, bytes]]] = {}
    for key, value in prefs.items():
        if "usageLimits" not in key or not isinstance(value, bytes):
            continue
        parts = key.split("_")
        if len(parts) < 3:
            continue
        ledger_keys.setdefault(parts[1], []).append((key, value))

    uid = pinned_uid or prefs.get(CURRENT_USER_KEY)
    if not isinstance(uid, str) or not uid:
        # No signed-in marker: safe only when there is exactly one user on record.
        uid = next(iter(ledger_keys)) if len(ledger_keys) == 1 else None

    out: dict = {
        "readAt": time.strftime("%H:%M:%S"),
        "debugDisableUsageLimits": prefs.get(DEBUG_FLAG_KEY),
        "userId": uid,
        "free": None,
        "package": None,
        "staleUserIds": sorted(other for other in ledger_keys if other != uid),
    }

    for key, value in ledger_keys.get(uid, []):
        try:
            record = json.loads(value.decode())
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if "voiceUsedSeconds" in record:
            out["free"] = record
        if "voice" in record:
            out["package"] = record

    return out


def render(data: dict) -> str:
    lines = [f"read @ {data['readAt']}"]
    flag = data["debugDisableUsageLimits"]
    if flag is None:
        lines.append(
            "  debugDisableUsageLimits : (unset) — limits are OFF; relaunch with "
            "-ENABLE_USAGE_LIMITS"
        )
    else:
        state = "limits ON" if flag is False else "limits OFF (stub service)"
        lines.append(f"  debugDisableUsageLimits : {flag}  → {state}")

    if data["userId"]:
        # UID prefix only — never the full identifier (Compliance/Privacy).
        lines.append(f"  user                    : {data['userId'][:8]}…")
    else:
        lines.append(
            "  user                    : (unresolved — pass --uid; records for "
            "several users are present)"
        )

    stale = data.get("staleUserIds") or []
    if stale:
        listed = ", ".join(other[:8] + "…" for other in stale)
        lines.append(
            f"  stale records on device : {listed}  ← not included; confirm the "
            "numbers below are the account under test"
        )

    free = data["free"]
    if free:
        cap = free.get("voiceLimitSeconds", 0)
        cap_note = "no cap (disabled)" if not cap else f"cap {cap}s"
        lines.append(
            f"  free  voiceUsedSeconds  : {free.get('voiceUsedSeconds', 0):.4f}s  ({cap_note})"
        )
        lines.append(
            f"  free  messages          : {free.get('usedCount', 0)}/{free.get('limitCount', 0)}"
        )

    pkg = data["package"]
    if pkg:
        voice, msg = pkg.get("voice", {}), pkg.get("message", {})
        for label, bucket in (("voice", voice), ("msg  ", msg)):
            unit = "ms" if label.strip() == "voice" else ""
            daily_used, daily_cap = bucket.get("dailyUsed", 0), bucket.get("dailyLimit")
            week_used, week_cap = bucket.get("weeklyUsed", 0), bucket.get("weeklyLimit")
            lines.append(
                f"  pkg   {label}             : daily {daily_used}/{daily_cap}{unit}"
                f"   weekly {week_used}/{week_cap}{unit}"
            )
        # Whichever bucket has less room is the one that will actually bind.
        room = [
            (bucket.get(f"{period}Limit") - bucket.get(f"{period}Used", 0), f"{period} voice")
            for period in ("daily", "weekly")
            for bucket in (voice,)
            if bucket.get(f"{period}Limit") is not None
        ]
        if room:
            tightest, which = min(room)
            lines.append(
                f"  → binding constraint    : {which}, {tightest / 1000:.0f}s of headroom"
            )
    elif data["free"] is None:
        lines.append("  (no usage records yet — has the user signed in and made a call?)")

    return "\n".join(lines)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--udid", required=True, help="iOS UDID (see `ios-deploy -c`)")
    parser.add_argument("--bundle-id", default=BUNDLE_ID)
    parser.add_argument(
        "--uid",
        default=None,
        help="Firebase userId to pin the read to (default: the signed-in user)",
    )
    parser.add_argument("--timeout", type=int, default=180, help="seconds (Wi-Fi can be slow)")
    parser.add_argument("--json", action="store_true", help="emit raw records")
    args = parser.parse_args()

    if shutil.which("ios-deploy") is None:
        raise SystemExit("ios-deploy not found on PATH. Install with: brew install ios-deploy")

    plist = pull_preferences(args.udid, args.bundle_id, args.timeout)
    try:
        data = decode(plist, args.uid)
    finally:
        shutil.rmtree(plist.parents[2], ignore_errors=True)

    print(json.dumps(data, indent=2, sort_keys=True) if args.json else render(data))


if __name__ == "__main__":
    main()
