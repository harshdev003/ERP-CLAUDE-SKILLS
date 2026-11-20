"""Move one tranche of ended experiments to /backup/experiments, or put it back.

Wraps the Aura CLI rather than reimplementing credentialed RTDB access:

    aura firebaseBackup  /experiments/<id>   ->  copies to /backup/experiments/<id>, removes original
    aura firebaseRestore /experiments/<id>   ->  exact inverse

Both are no-ops unless DO_UPDATE=true, so a dry run is the default here too.

Every removal fans a child_removed delta out to every keepSynced client and fires
the activeExperiments Cloud Function, so the loop is throttled by default.

Usage:
    python3 archive.py --tranches tranches.json --year 2022 --export fresh-export.json
    python3 archive.py --tranches tranches.json --year 2022 --export fresh-export.json --execute
    python3 archive.py --manifest run-2022.json --rollback --execute
"""

import argparse
import glob
import json
import os
import subprocess
import sys
import time

CLI = os.environ.get("AURA_CLI", "aura")


def run(command, execute):
    env = dict(os.environ, DO_UPDATE="true" if execute else "false")
    return subprocess.run(command, shell=True, env=env, capture_output=True, text=True)


def already_moved(tranche, directory="."):
    """Ids this tranche already moved, from every manifest a previous run wrote.

    The canary flow (`--limit 10`, verify, then the full run) reruns the SAME
    tranche against the SAME export, so preflight cannot drop the ten already
    archived -- in that stale export they still look ended. Re-archiving them
    fails noisily at best and, if `/backup` already holds them, re-copies over a
    good backup. Read the manifests instead.
    """
    done = set()
    for path in glob.glob(os.path.join(directory, f"manifest-{tranche}-*.json")):
        try:
            done |= set(json.load(open(path)).get("moved", []))
        except (OSError, ValueError):
            print(f"  warning: could not read {path}", file=sys.stderr)
    return done


def preflight(ids, export_path):
    """Gate 1 of 2 -- ENDED. Re-verified against a FRESH export, because a manifest
    is evidence about the node as it was when the manifest was built, and the node
    moves. A row that went back to splitting, or vanished, is dropped here.

    Counts distinct arms, not string length: `bb` is one arm but length 2, and it
    exists precisely to evade a length check (see reconcile.py's malformed_active).
    """
    firebase = json.load(open(export_path))
    ok, problems = [], []
    for key in ids:
        row = firebase.get(key)
        if row is None:
            problems.append((key, "absent from the export"))
            continue
        active = str(row.get("active") or "").strip()
        arms = len(set(active))
        if arms != 1:
            problems.append((key, f"active={active!r} is {arms} arms -- not ended"))
        elif len(active) != 1:
            problems.append((key, f"active={active!r} evades the ended check -- held"))
        else:
            ok.append(key)
    return ok, problems


def verify_removed_from_code(ids, repos_root):
    """Gate 2 of 2 -- REMOVED FROM CODE. Independent of whatever built the tranche.

    reconcile.py already excluded everything current code can name, but that ran
    against the tree as it was then. Between building a tranche and running it a
    branch can land that reintroduces a name, and the whole failure mode here is
    silent. So re-derive it here, from the working trees, at move time.

    Returns the ids found in code -- non-empty means refuse.
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import match
    import reconcile

    found, scanned, missing = {}, [], []
    for client, spec in reconcile.CLIENTS.items():
        base = reconcile.resolve(repos_root, spec["at"])
        path = os.path.join(base, spec["src"]) if base else None
        if not path or not os.path.isdir(path):
            missing.append(client)
            continue
        scanned.append(client)
        hits = match.match(match.walk_tree(path), set(ids))
        for key in set().union(*hits.values()):
            found.setdefault(key, []).append(client)
    if missing:
        sys.exit(f"REFUSING: cannot verify removal-from-code -- not scanned: "
                 f"{', '.join(missing)}. An unscanned client cannot clear an id.")
    print(f"  code check: scanned {', '.join(scanned)}")
    return found


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser()
    ap.add_argument("--tranches", help="reconcile.py output")
    ap.add_argument("--year", help="tranche key: a year, or 'undated'")
    ap.add_argument("--manifest", help="a previous run's manifest (required for --rollback)")
    ap.add_argument("--export", help="FRESH export, for preflight")
    ap.add_argument("--execute", action="store_true", help="actually write (sets DO_UPDATE=true)")
    ap.add_argument("--rollback", action="store_true")
    ap.add_argument("--throttle", type=float, default=0.5, help="seconds between writes")
    ap.add_argument("--repos-root", default=os.path.expanduser("~/Development/Aura"),
                    help="parent of the client checkouts, for the removed-from-code gate")
    ap.add_argument("--skip-code-check", action="store_true",
                    help="skip the removed-from-code gate -- do not use for a real run")
    ap.add_argument("--manifest-dir", default=".",
                    help="where this tranche's previous manifests live (read to skip re-moves)")
    ap.add_argument("--limit", type=int, help="stop after N ids -- use for a canary batch")
    args = ap.parse_args()

    if args.rollback:
        if not args.manifest:
            sys.exit("--rollback needs the --manifest of the run being undone")
        ids = json.load(open(args.manifest))["moved"]
        verb, action = "restore", "firebaseRestore"
    else:
        if not (args.tranches and args.year and args.export):
            sys.exit("need --tranches, --year and a fresh --export")
        ids = json.load(open(args.tranches))["tranches"].get(args.year)
        if not ids:
            sys.exit(f"no tranche {args.year!r} in {args.tranches}")
        ids, problems = preflight(ids, args.export)
        for key, why in problems:
            print(f"  SKIP {key}: {why}")
        if problems:
            print(f"\n{len(problems)} id(s) failed preflight and are excluded.\n")
        if not args.skip_code_check:
            in_code = verify_removed_from_code(ids, args.repos_root)
            if in_code:
                print(f"\nREFUSING -- {len(in_code)} id(s) are STILL REFERENCED in current code:",
                      file=sys.stderr)
                for key, clients in sorted(in_code.items()):
                    print(f"  {key}: {', '.join(clients)}", file=sys.stderr)
                sys.exit("\nAn archived experiment stops serving its winner. These would "
                         "drop users onto the control arm with no error.")
            print(f"  code check: all {len(ids)} id(s) absent from every client's code\n")
        done = already_moved(args.year, args.manifest_dir)
        if done:
            before = len(ids)
            ids = [k for k in ids if k not in done]
            print(f"  skipping {before - len(ids)} id(s) a previous run of this "
                  f"tranche already moved (from manifest-{args.year}-*.json)\n")
        verb, action = "archive", "firebaseBackup"

    if args.limit:
        ids = ids[:args.limit]

    mode = "EXECUTING" if args.execute else "DRY RUN (nothing will be written)"
    print(f"{mode}: {verb} {len(ids)} experiment(s)\n")

    moved, failed = [], []
    for i, key in enumerate(ids, 1):
        result = run(f"{CLI} {action} /experiments/{key}", args.execute)
        if result.returncode == 0:
            moved.append(key)
        else:
            failed.append({"id": key, "stderr": result.stderr.strip()[:300]})
            print(f"  FAIL {key}: {result.stderr.strip()[:160]}")
        if i % 25 == 0 or i == len(ids):
            print(f"  {i}/{len(ids)}  ok={len(moved)} failed={len(failed)}")
        if args.execute and args.throttle:
            time.sleep(args.throttle)

    if args.execute and not args.rollback:
        name = os.path.join(args.manifest_dir,
                            f"manifest-{args.year}-{time.strftime('%Y%m%dT%H%M%S')}.json")
        json.dump({"tranche": args.year, "moved": moved, "failed": failed},
                  open(name, "w"), indent=1)
        print(f"\nmanifest: {name}  <- rollback needs this file, keep it")

    print(f"\n{verb}d {len(moved)}, failed {len(failed)}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
