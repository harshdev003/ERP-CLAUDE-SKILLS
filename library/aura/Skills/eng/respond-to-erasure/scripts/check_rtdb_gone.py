#!/usr/bin/env python3
"""Check that an erased user's RTDB nodes are gone: one batch of exact-path reads through rtdb_get.js.

Covers the 90 per-user nodes except `/userDonations` (kept on erasure by decision), each read
as `/{node}/{uid}`: `/users/{uid}`, every node
FirebaseDenormalizer's `userDeleted` removes (same list, same order), the per-user nodes it never
touches, and the `/deletedUsers/{uid}` tombstone. A deleted node reads as nothing, so `null` is a
definite "gone". No query, shallow or REST read.

rtdb_get.js caps what it prints, not what it downloads, so a list node whose delete failed would
come down whole (`/userListened` can hold 17k entries). FirebaseDenormalizer's audit line decides
which list nodes are read (check_rtdb_audit.py):
- done: every node is read.
- failed, with an error line naming each failed node: those list nodes are not read and are
  reported present; every other node is read.
- anything else (no summary line after a crash or timeout, a log read error, failed nodes the
  error lines don't all name): list nodes are "unverified" and not read; small nodes still are.
The read wins over the log: a node the log called removed that still reads present is present.

The list nodes `userDeleted` never touches have no log to vouch for them, so they are never read
here: they come back under "for_firebase", for the Firebase owner to check. "status" covers only
the nodes this script reads; RTDB is closed when it is "gone" and the Firebase owner reports every
"for_firebase" node gone. The small ones are read.

The tombstone is reported on its own ("tombstone"), not in the status: the account delete writes
it, and the erasure removes it last (step 7). Step 8 needs it "gone".

Prints one JSON object with node names and counts, never the uid, the email, a path or a value:
  {"status": "gone" | "present" | "unverified", "audit": ..., "present": [...],
   "present_per_log": [...], "unverified": [...], "for_firebase": [...], "gone": n,
   "tombstone": "gone" | "present" | "unverified", "kept": ["userDonations"]}

Env for the read: RTDB_URL, GCP_KEY, AURA_CLI_DIR (see eng:read-rtdb-paths).

Usage:
  python3 check_rtdb_gone.py --uid UID --since 2026-10-24T06:05:00Z [--dry-run]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# FirebaseDenormalizer functions/src/triggers/user/userDeleted.js, in its order.
NODES = (
    "skippedContent", "userActivities", "userAddress", "userAISubscriptions", "userAuthorsExcluded",
    "userCategories", "userChallenges", "userChannelsExcluded", "userCoachFollowing", "userCoachListened",
    "userCoachSubscriptions", "userCoachTodayExcluded", "userCoachingSubscriptions", "userCoachingWaitlist",
    "userCommunitySubscriptions", "userConfirmedAppointment", "userCustomizeButtons", "userCustomizeHomeTabs",
    "userCustomizeTabs", "userDailyCompletedGoals", "userDailyGoalProgress", "userDaysListened",
    "userExperiments", "userFollowers", "userFollowing", "userGiftChallengeDays", "userGroupClassesWaitlist",
    "userHideRows", "userInappMessages", "userLatestPlayed", "userListened", "userMeditationPrograms",
    "userMeditationsDownloaded", "userMeditationsExcluded", "userMeditationsFavorited",
    "userMeditationsListened", "userMeditationsPlayed", "userMeditationsPlayedProgress",
    "userMeditationsRatings", "userMeditationsThumbedDown", "userMeditationsThumbedUp", "userMorningRoutine",
    "userOnboardingProgress", "userPlayed", "userPodcastPlayed", "userPodcastProgress", "userPrograms",
    "userProgramsFavorited", "userProgramsProgress", "userRatedCoaches", "userReelsListened",
    "userReelsPlayed", "userReflections", "userSavedQuotes", "userSearches", "userSeries",
    "userSeriesFavorited", "userSeriesProgress", "userShirtSize", "userSkipOnboardingCallReason", "userSkips",
    "userSleepMusicProgress", "userSubscriptions", "userThumbs", "userTopicMeditationProgress",
    "userTrackRating", "userUpdatedRows", "userUpdatedScreens", "userUpdatedTracks",
)
# Per-user nodes userDeleted never removes (userDonations is commented out there). Keyed by uid
# per rules.json and client code; the closed nodes at the end are keyed by uid going by the name.
UNTOUCHED = (
    "userEvents", "feed", "blockedCommunities", "customPlaylist", "downloadedTracks",
    "familyPlanInvites", "newContentTypeTapped", "referralFriends", "userCommunityBlockedList",
    "userCourses", "userDailyCompletedGoal", "userReelsLiked", "userSubscriptionProductChanged",
    "userAIUsageLimits", "userCategorySaved", "userMeditationsDone", "userMeditationsRecs",
    "userTrackProgress",
)
TOMBSTONE = "deletedUsers"
# Kept on erasure by decision ("I would keep", Jerome, 2026-11-19): neither read nor counted.
KEPT = ("userDonations",)
# Single values or a few settings: safe to read whatever the log says. Every other node is a list.
SMALL = frozenset({
    "users", TOMBSTONE, "userAddress", "userShirtSize", "userSkipOnboardingCallReason",
    "userOnboardingProgress", "userCustomizeButtons", "userCustomizeHomeTabs", "userCustomizeTabs",
    "userMorningRoutine", "blockedCommunities", "newContentTypeTapped", "userSubscriptionProductChanged",
})


def plan(uid: str, audit: dict) -> list[dict]:
    """One entry per node: {node, path, read, why}. `why` says why a node is not read."""
    status = audit.get("status")
    failed_nodes = audit.get("failed_nodes")
    # Every failed node must be named, or we can't tell which list nodes are safe to read.
    named = (status == "failed" and isinstance(failed_nodes, list)
             and len(failed_nodes) == audit.get("failed"))
    nodes = ("users", *NODES, *UNTOUCHED, TOMBSTONE)
    out = []
    for node in nodes:
        why = None
        if node not in SMALL:
            if node in UNTOUCHED:
                why = "for_firebase"
            elif named and node in failed_nodes:
                why = "present_per_log"
            elif status != "done" and not named:
                why = "unverified"
        out.append({"node": node, "path": f"/{node}/{uid}", "read": why is None, "why": why})
    return out


def read(paths: list[str]) -> dict[str, dict]:
    """Run rtdb_get.js on the paths (stdin), names and counts only. Returns {path: row}."""
    # This skill's own gcp.py by path: another skill's `gcp` may already be imported under that name.
    spec = importlib.util.spec_from_file_location("respond_to_erasure_gcp", os.path.join(HERE, "gcp.py"))
    gcp = sys.modules[spec.name] = importlib.util.module_from_spec(spec)  # dataclasses look it up there
    spec.loader.exec_module(gcp)
    env = {**os.environ, "AINATIVE_USER_AGENT": gcp.user_agent()}
    proc = subprocess.run(["node", os.path.join(HERE, "rtdb_get.js")], input="\n".join(paths),
                          capture_output=True, text=True, encoding="utf-8", env=env, timeout=600)
    rows = {}
    for line in proc.stdout.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("path") in paths:
            rows[row["path"]] = row
    return rows


def check(uid: str, since: str, dry_run: bool = False) -> dict:
    sys.path.insert(0, HERE)
    import check_rtdb_audit  # noqa: E402

    audit = check_rtdb_audit.check(uid, since)
    entries = plan(uid, audit)
    audit_out = {k: audit.get(k) for k in ("status", "removed", "failed", "failed_nodes", "reason") if k in audit}
    if dry_run:
        return {"audit": audit_out,
                "read": [e["node"] for e in entries if e["read"]],
                "not_read": {e["node"]: e["why"] for e in entries if not e["read"]},
                "kept": list(KEPT)}
    rows = read([e["path"] for e in entries if e["read"]])
    present, per_log, unverified, for_firebase, gone = [], [], [], [], 0
    tombstone = "unverified"
    for e in entries:
        if e["node"] == TOMBSTONE:
            row = rows.get(e["path"]) or {}
            if not row.get("error") and row.get("exists") in (True, False):
                tombstone = "present" if row["exists"] else "gone"
        elif e["why"] == "for_firebase":
            for_firebase.append(e["node"])
        elif e["why"] == "present_per_log":
            per_log.append(e["node"])
        elif e["why"] == "unverified":
            unverified.append(e["node"])
        else:
            row = rows.get(e["path"]) or {"error": "NO_RESULT"}
            if row.get("error"):
                unverified.append(e["node"])
            elif row.get("exists") is False:
                gone += 1
            elif row.get("exists") is True:
                present.append(f"{e['node']} ({row['count']})" if "count" in row else e["node"])
            else:
                unverified.append(e["node"])
    status = "present" if present or per_log else "unverified" if unverified else "gone"
    return {"status": status, "audit": audit_out, "present": present, "present_per_log": per_log,
            "unverified": unverified, "for_firebase": for_firebase, "gone": gone, "tombstone": tombstone,
            "kept": list(KEPT)}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--uid", required=True, help="the erased user's Firebase UID")
    p.add_argument("--since", required=True, help="ISO time the account delete was called (UTC)")
    p.add_argument("--dry-run", action="store_true", help="print which nodes would be read, read nothing")
    a = p.parse_args()
    print(json.dumps(check(a.uid, a.since, a.dry_run)))


if __name__ == "__main__":
    main()
