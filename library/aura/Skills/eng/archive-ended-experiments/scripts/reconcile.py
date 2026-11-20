"""Reconcile three sources of experiment truth and emit archive tranches.

Firebase says what clients actually read. Postgres says when an experiment was
stamped ended. AI Knowledge says what a human concluded. They disagree, and each
disagreement is informative -- so the output records which source supplied each
date rather than flattening them.

Usage:
    python3 reconcile.py --export experiments.json --repos-root ~/Development/Aura \
        [--out tranches.json]

`--export` is the file written by `aura firebaseExport <path> /experiments`.
This script never touches Firebase itself; the CLI owns those credentials.
"""

import argparse
import collections
import json
import os
import sys
import urllib.request
import pathlib
import re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import match  # noqa: E402


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


AURA_CATALOG = "https://services.aurahealth.io/experiments?limit=100000"
EXPERIMENTS_DS = "c0209685-17ac-49e2-849a-85fa8178decd"  # Learnings/Experiments

# Every client that reads the /experiments node. Aurie (AuraConsumerApp) is on
# POST /experiments/assign and does not sync the node, but it is scanned anyway:
# a name it still uses must not be archived regardless of how it fetches it.
# One row per client that can read an experiment name.
#
# `installed_base` is the field that decides how far back history matters, and it
# is a fact about DISTRIBUTION, not about the code:
#
#   True  -- a native app. Builds sit on phones for years, so a name its code
#            carried in 2023 can still be requested today. History is binding, and
#            only --cutoff (an explicit decision about which builds still matter)
#            releases it.
#   False -- a web client. Every visitor is served the current bundle; there is no
#            six-month-old deploy in the wild. Once a name is out of the source it
#            can never be requested again, so history carries no weight at all.
#            (A long-lived open tab or a service worker can hold a stale bundle for
#            hours. That is a different order of risk from a year-old install, but
#            it is not zero -- do not archive a name removed from web minutes ago.)
#
# Getting this wrong in the True direction only costs savings. In the False
# direction it drops users onto the control arm, so a new client defaults to True.
CLIENTS = {
    "Aura (RN)":        {"at": ["Mobile/Aura"], "src": "src",
                         "history": ["src", "app.js"], "installed_base": True},
    "Aurie (iOS)":      {"at": ["Mobile/AuraConsumerApp"], "src": "AuraConsumerApp",
                         "history": ["AuraConsumerApp"], "installed_base": True},
    "UserWeb":          {"at": ["Web/UserWeb"], "src": "src",
                         "history": ["src"], "installed_base": False},
    "AIWeb":            {"at": ["Web/AIWeb"], "src": "src",
                         "history": ["src"], "installed_base": False},
    "AuraWeb (legacy)": {"at": ["Legacy/AuraWeb"], "src": ".",
                         "history": ["."], "installed_base": False},
}


def resolve(root, candidates, must_be_git=False):
    """First candidate that exists, or None. Clients live under different parents."""
    for rel in candidates:
        path = os.path.join(root, rel)
        if os.path.isdir(os.path.join(path, ".git") if must_be_git else path):
            return path
    return None


def load_catalog():
    token = os.environ.get("AURA_API_TOKEN")
    if not token:
        print("warning: AURA_API_TOKEN unset -- no endedAt dates from Postgres", file=sys.stderr)
        return {}
    req = urllib.request.Request(AURA_CATALOG, headers={"Authorization": f"Bearer {token}", "User-Agent": user_agent()})
    rows = json.load(urllib.request.urlopen(req))
    if isinstance(rows, dict):
        rows = rows.get("data") or rows.get("results") or []
    return {r["id"]: r for r in rows}


def load_knowledge():
    """AI Knowledge is not optional here.

    It is the only source that carries a human `Status`, and the `Running` abort
    below is a safety gate, not a nicety. Degrading to {} would not merely lose a
    cross-check -- it would silently disable the gate, so a candidate a human
    marked `Running` would sail through into a tranche. Refuse instead.
    """
    if not os.environ.get("NOTION_TOKEN"):
        sys.exit("NOTION_TOKEN is not set. It is required: the `Running` abort is a "
                 "safety gate and an empty knowledge map silently disables it.")
    from notion_rest import notion
    if not notion.available:
        sys.exit("Notion is unreachable -- refusing to build tranches without the "
                 "`Running` cross-check.")
    index = {}
    for row in notion.query_data_source(EXPERIMENTS_DS, page_size=100):
        props = row["properties"]
        title = "".join(x.get("plain_text", "") for x in props.get("Title", {}).get("title", [])).strip()
        if not title:
            continue
        status = (props.get("Status", {}) or {}).get("select")
        end = (props.get("End Date", {}) or {}).get("date")
        index[title] = {"status": status["name"] if status else None,
                        "end": end["start"] if end else None}
    return index


# Variant letters run from 'a'. Mirrors data:sync-experiments/scripts/reconcile.py,
# which is where this rule was worked out (PROD-6116).
VARIANT_LETTERS = set("abcdefgh")


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


def variant_arms(active) -> int:
    """Distinct arms, not string length. `active` is a weighted arm list: repeats
    are traffic weight (`aab` is a at 2:1 over b), so `aab` is two arms."""
    return len(set(str(active or "").strip()))


def malformed_active(row: dict):
    """Why `active` cannot be read as an arm set, or None if it can.

    Two shapes are refused, both taken verbatim from data:sync-experiments:

    **A repeat collapsing to one arm** (`bb`, `aa`) is a workaround, not a typo.
    The service's `assign()` decides an experiment has ended with
    `active.length === 1` -- the STRING length -- so `bb` forces every user to `b`
    while dodging the ended check. Somebody wanted one arm without ending the
    experiment.

    **A non-variant character** (`none`) is written by the end command when no
    winner clause was supplied.
    """
    active = str(row.get("active") or "").strip()
    if not active:
        return None
    bad = sorted(set(active) - VARIANT_LETTERS)
    if bad:
        return f"non-variant character(s) {''.join(bad)!r} in {active!r}"
    if len(active) > 1 and len(set(active)) == 1:
        return (f"{active!r} forces one arm while evading the service's "
                f"`active.length === 1` ended check")
    return None


def ambiguous_active(row: dict) -> bool:
    """True when `active` cannot be read as an arm set. See `malformed_active`."""
    return malformed_active(row) is not None


# WHERE THIS SKILL COMES DOWN ON THE AMBIGUITY, and why it differs from a reader.
#
# `data:sync-experiments` is a read-side consumer and errs toward live. This skill
# REMOVES DATA, so it holds instead: an ambiguous row is never archived, and is
# reported rather than silently dropped from the count.
#
# `bb` is the case that decides it. It is not ended -- it evades the ended check on
# purpose -- yet it forces arm b to every user, so it is answering exactly like an
# ended experiment. Archiving it would drop those users to control, which is the
# failure this whole skill exists to avoid. And a non-variant `active` means the
# catalogue does not say what was meant; a writer that guesses puts an irreversible
# move behind a guess. Neither is resolved here. Both wait for a human.


def best_date(key, firebase, catalog, knowledge):
    """Postgres stamp first, then the human record, then whatever Firebase carries.

    Returns (source, YYYY-MM-DD) or ('none', None). Never guesses: an undated row
    stays undated and is tranched last, because an invented date reads as measured.
    """
    row = catalog.get(key, {})
    for source, value in (("pg.endedAt", row.get("endedAt")),
                          ("pg.deletedAt", row.get("deletedAt")),
                          ("knowledge", (knowledge.get(key) or {}).get("end")),
                          ("fb.endDate", firebase[key].get("endDate"))):
        if value and str(value)[:4].isdigit():
            return source, str(value)[:10]
    return "none", None


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser()
    ap.add_argument("--export", required=True, help="output of `aura firebaseExport ... /experiments`")
    ap.add_argument("--repos-root", default=os.path.expanduser("~/Development/Aura"))
    ap.add_argument("--out", default="tranches.json")
    ap.add_argument("--cutoff", metavar="YYYY-MM-DD|all",
                    help="also archive keys whose code left every installed-base client on or "
                         "before this date. Builds released before it still carry that code, so "
                         "check the installed-base share for that date first. `all` drops the "
                         "history gate entirely: ONLY current code holds a name alive, and every "
                         "user on an older build falls back to the control arm.")
    ap.add_argument("--allow-missing-clients", action="store_true",
                    help="proceed when a client checkout is absent -- WIDENS the archive set")
    args = ap.parse_args()

    firebase = json.load(open(args.export))
    keys = set(firebase)
    catalog = load_catalog()
    if not catalog:
        # The RTDB export may not carry `notes`, so without the catalogue a feature flag (the
        # "ended, winner" shape) would look ended and could be archived. Refuse rather than guess.
        sys.exit("REFUSING to build tranches: the experiment catalogue could not be read (AURA_API_TOKEN). "
                 "It is what marks a feature flag, and a flag must never be archived.")
    knowledge = load_knowledge()

    # One arm means "ended, serving this winner" -- the signal the clients themselves
    # act on, so it, not endedAt, decides what is ended. Ambiguous rows are neither:
    # they are held out of both sets and reported. See the comment above best_date.
    # The RTDB export may not carry `notes`; the catalogue row does.
    flags = {k for k, v in firebase.items() if is_feature_flag(v) or is_feature_flag(catalog.get(k))}
    ambiguous = {k for k, v in firebase.items() if ambiguous_active(v)} - flags
    live = {k for k, v in firebase.items()
            if k not in ambiguous and k not in flags and variant_arms(v.get("active")) > 1}
    ended = keys - live - ambiguous - flags
    if flags:
        print(f"\nSKIPPED -- feature flags, never archived ({len(flags)}): {', '.join(sorted(flags))}",
              file=sys.stderr)
    if ambiguous:
        print(f"\nHELD -- `active` cannot be read as an arm set ({len(ambiguous)}), "
              f"never archived, needs a human:", file=sys.stderr)
        for k in sorted(ambiguous):
            print(f"  {k}: {malformed_active(firebase[k])}", file=sys.stderr)

    current, history, missing = set(), {}, []
    for repo, spec in CLIENTS.items():
        base = resolve(args.repos_root, spec["at"])
        if base is None:
            missing.append(f"{repo} (looked in {', '.join(spec['at'])})")
            continue
        path = os.path.join(base, spec["src"])
        if not os.path.isdir(path):
            missing.append(f"{repo} source dir {path}")
            continue
        hits = match.match(match.walk_tree(path), keys)
        found = set().union(*hits.values())
        current |= found
        print(f"  {repo:26} {len(found):4} referenced "
              f"(read {len(hits['read'])}, registered {len(hits['registered'])}, "
              f"quoted {len(hits['quoted'])}, const {len(hits['const'])})")

    for repo, spec in CLIENTS.items():
        if not spec["installed_base"]:
            # No old deploys exist, so this client's history binds nothing. Its
            # CURRENT code was scanned above and is what holds a name alive.
            print(f"  {repo:26} history skipped -- no installed base")
            continue
        repo_path = resolve(args.repos_root, spec["at"], must_be_git=True)
        if repo_path is None:
            missing.append(f"{repo} git history (looked in {', '.join(spec['at'])})")
            continue
        found = match.history_last_seen(repo_path, spec["history"], keys)
        for key, when in found.items():
            if key not in history or when > history[key]:
                history[key] = when
        print(f"  {repo:26} {len(found):4} ever referenced (full history, installed base)")

    # A client that was not scanned contributes nothing to the keep-set, so every
    # name only IT uses becomes archivable. That is the one failure mode with no
    # symptom: the run looks clean and the tranche is quietly too big. Refuse.
    if missing and not args.allow_missing_clients:
        sys.exit("\nREFUSING to build tranches -- these were not scanned:\n  "
                 + "\n  ".join(missing)
                 + "\n\nAn unscanned client silently WIDENS the archive set: names only it"
                   "\nuses look unreferenced. Clone them, point --repos-root at the right"
                   "\nparent, or pass --allow-missing-clients if you can argue the client"
                   "\nnever read /experiments at all.")
    if missing:
        print("\nWARNING: --allow-missing-clients -- these were NOT scanned and the "
              "tranches may be too big:\n  " + "\n  ".join(missing), file=sys.stderr)

    # A key is archivable when no client can name it. Two ways that is true:
    #   * it was never referenced at all, by any client, ever -- no build argument
    #     is needed and none is made; or
    #   * its code was removed on or before --cutoff, and the operator has decided
    #     builds older than that no longer matter. That decision belongs to a human
    #     holding the installed-base numbers, so without --cutoff only the first
    #     group qualifies and the second is reported, never assumed.
    # `history` now holds ONLY installed-base clients, so a web-only name that is
    # out of the current web source lands in `never` -- correctly, because no web
    # visitor can ever request it again.
    never = ended - current - set(history)
    aged = set()
    if args.cutoff == "all":
        # Explicit operator decision: current code is the ONLY thing holding a name
        # alive. Every installed build older than a name's removal loses its winner
        # and falls back to control -- silently, since a missing name raises nothing.
        aged = (ended - current) & set(history)
    elif args.cutoff:
        aged = {k for k in (ended - current) & set(history) if history[k] <= args.cutoff}
    candidates = never | aged

    still_running = [k for k in candidates if (knowledge.get(k) or {}).get("status") in
                     ("Running", "In flight", "Active", "Live")]
    if still_running:
        sys.exit(f"ABORT: AI Knowledge marks {len(still_running)} candidate(s) as running: "
                 f"{sorted(still_running)[:10]}")

    tranches, dates = collections.defaultdict(list), {}
    for key in candidates:
        source, date = best_date(key, firebase, catalog, knowledge)
        dates[key] = {"source": source, "date": date}
        tranches[date[:4] if date else "undated"].append(key)

    payload = {"generated_from": os.path.abspath(args.export),
               "totals": {"rows": len(firebase), "live": len(live), "ended": len(ended),
                          "held_ambiguous_active": len(ambiguous),
                          "skipped": {"featureFlag": len(flags)},
                          "referenced_current": len(ended & current),
                          "referenced_history_only": len((ended & set(history)) - current),
                          "candidates": len(candidates),
                          "candidates_never_referenced": len(never),
                          "candidates_aged_out": len(aged)},
               "cutoff": args.cutoff,
               "last_seen_in_code": {k: history[k] for k in candidates if k in history},
               "dates": dates,
               "tranches": {y: sorted(v) for y, v in sorted(tranches.items())}}
    json.dump(payload, open(args.out, "w"), indent=1)

    print(f"\n{len(firebase)} rows | {len(live)} live | {len(ended)} ended"
          f" | {len(ambiguous)} held (ambiguous `active`) | skipped.featureFlag {len(flags)}")
    print(f"excluded: {len(ended & current)} in current code, "
          f"{len((ended & set(history)) - current)} in old versions only")
    print(f"ARCHIVABLE: {len(candidates)}  "
          f"({len(never)} never referenced" +
          (f" + {len(aged)} still in NO current code (history gate OFF)"
           if args.cutoff == "all" else
           f" + {len(aged)} whose code left by {args.cutoff}" if args.cutoff else
           "; pass --cutoff to include aged-out keys") + ")\n")
    for year in sorted(tranches):
        print(f"  {year:8} {len(tranches[year]):5} rows")
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
