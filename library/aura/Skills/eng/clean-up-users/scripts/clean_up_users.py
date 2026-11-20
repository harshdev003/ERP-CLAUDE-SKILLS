#!/usr/bin/env python3
"""Monthly all-users cleanup around `aura cleanupUsers` (Tools/CLI).

Subcommands:
  prepare          resolve the CLI checkout and the exports folder, create it (700)
  export-mixpanel  BigQuery Mixpanel people export -> <exports>/mixpanel.jsonl (600)
  check-inputs     validate users.json and mixpanel.jsonl; counts only
  exclude-held     remove the held uids (held-uids.json) from users.json in place; counts only
  check-held       count held uids in a list (default: output/deleted.json); exit 1 if any
  run              run cleanupUsers or cleanupDeletedUsers; full log to a 600 file, counts on stdout
                   (--sample ROWS: a cleanupUsers dry run on the head of users.json, with timing)
  remove           print the path-checked rm for the exports folder (never runs it)

Nothing here prints a uid, a location or an age. The CLI's own log lines carry uids, so
they go to a private log file.
"""

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import socket
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

EXPORTS_NAME = "cleanup-users"
MAX_AGE_DAYS = 14
MIXPANEL_KEYS = ("userid", "mp_country_code", "mp_region", "mp_city", "age_min", "age_max")
MIXPANEL_SQL = (
    "SELECT userid, mp_country_code, age_min, age_max, mp_region, mp_city "
    "FROM `mixpanel-prod-1.mixpanel_nessie_day_partitioned_963821.mp_people_data_view` "
    "where userid is not null and age_min is not null"
)
# The view is read from aura-main: the service account can't create jobs in firebase-auratech16
BQ_PROJECT = "aura-main"
BQ_MAX_BYTES = 5 * 1024 ** 3
TASKS = ("cleanupUsers", "cleanupDeletedUsers")
NODE_HEAP = "--max-old-space-size=12192"
# The keep list (held_uids.py, shared with cleanup-orphaned-auth): a JSON array of uids, mode 600, written
# by the hold owner. Missing or empty refuses every command that reads or writes users.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import held_uids  # noqa: E402  (a copy; canonical in cleanup-orphaned-auth)

HELD_UIDS = held_uids.path()
# Durable, outside the exports folder (which is deleted after a run): the RTDB profiler reads
# it to label this run's Admin SDK traffic, whose user agent the CLI can't set.
RUN_LOG = pathlib.Path(os.environ.get("CLEAN_UP_USERS_RUN_LOG")
                       or pathlib.Path.home() / ".claude" / "clean-up-users" / "runs.jsonl")


class Refused(Exception):
    """A precondition failed; the message says which."""


def cli_dir(arg=None) -> pathlib.Path:
    raw = arg or os.environ.get("AURA_CLI_DIR")
    if not raw:
        raise Refused("Name the Aura CLI checkout: --cli-dir or AURA_CLI_DIR")
    d = pathlib.Path(raw).expanduser().resolve()
    if not (d / "src" / "cli" / "commander.js").is_file():
        raise Refused(f"No Aura CLI checkout at {d}")
    if not (d / "src" / "tasks" / "user" / "cleanupUsers.js").is_file():
        raise Refused(f"{d} has no cleanupUsers task: pull master")
    config = (d / "src" / "config.js").read_text(encoding="utf-8")
    if "CLEANUP_EXPORTS" not in config:
        raise Refused(f"{d} predates the cleanupUsers export paths: pull master")
    return d


def exports_dir(cli: pathlib.Path) -> pathlib.Path:
    """Same resolution as the CLI's config.js: CLEANUP_EXPORTS, else <cli>/data/cleanup-users."""
    env = os.environ.get("CLEANUP_EXPORTS")
    return pathlib.Path(env).expanduser().resolve() if env else cli / "data" / EXPORTS_NAME


def input_paths(cli: pathlib.Path) -> dict:
    ex = exports_dir(cli)
    return {
        "users": pathlib.Path(os.environ.get("CLEANUP_USERS_JSON") or ex / "users.json"),
        "mixpanel": pathlib.Path(os.environ.get("CLEANUP_MIXPANEL_JSONL") or ex / "mixpanel.jsonl"),
        "output": pathlib.Path(os.environ.get("CLEANUP_PRIVATE_DIR") or ex / "output"),
    }


def is_git_ignored(repo: pathlib.Path, path: pathlib.Path) -> bool:
    """A `dir/` rule only matches a path git knows is a directory, so a folder that
    doesn't exist yet is checked through a child path as well."""
    for p in (path, path / ".check"):
        r = subprocess.run(["git", "-C", str(repo), "check-ignore", "-q", str(p)],
                           capture_output=True, text=True)
        if r.returncode == 0:
            return True
    return False


def check_private(cli: pathlib.Path, path: pathlib.Path):
    """A path inside the CLI checkout must be gitignored, so an export can't be committed."""
    try:
        path.resolve().relative_to(cli)
    except ValueError:
        return
    if not is_git_ignored(cli, path):
        raise Refused(f"{path} is inside the CLI repo but not gitignored")


def age_days(path: pathlib.Path, now=None) -> float:
    now = now or dt.datetime.now().timestamp()
    newest = path.stat().st_mtime
    oldest = newest
    for p in path.rglob("*") if path.is_dir() else [path]:
        oldest = min(oldest, p.stat().st_mtime)
    return (now - oldest) / 86400


def cmd_prepare(args) -> int:
    cli = cli_dir(args.cli_dir)
    ex = exports_dir(cli)
    check_private(cli, ex)
    existed = ex.exists()
    ex.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(ex, 0o700)
    out = {"cliDir": str(cli), "exports": str(ex), "existed": existed}
    if existed and any(ex.iterdir()):
        days = age_days(ex)
        out["oldestFileDays"] = round(days, 1)
        if days > MAX_AGE_DAYS:
            out["overdue"] = f"files older than {MAX_AGE_DAYS} days: remove them (`remove`) before a new run"
    print(json.dumps(out, indent=2))
    return 0


def write_jsonl(rows, dest: pathlib.Path) -> int:
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    n = 0
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps({k: row.get(k) for k in MIXPANEL_KEYS}, default=str) + "\n")
            n += 1
    os.replace(tmp, dest)
    os.chmod(dest, 0o600)
    return n


def mixpanel_rows():
    import bq
    est = bq.query(MIXPANEL_SQL, project=BQ_PROJECT, dry_run=True)[0]["bytes"]
    if est > BQ_MAX_BYTES:
        raise Refused(f"Mixpanel export would scan {est} bytes, over the {BQ_MAX_BYTES} cap")
    from google.cloud import bigquery
    job = bq.client(BQ_PROJECT).query(
        MIXPANEL_SQL, job_config=bigquery.QueryJobConfig(maximum_bytes_billed=BQ_MAX_BYTES))
    for row in job.result(page_size=50000):
        yield dict(row)


def cmd_export_mixpanel(args) -> int:
    cli = cli_dir(args.cli_dir)
    dest = input_paths(cli)["mixpanel"]
    check_private(cli, dest)
    dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    n = write_jsonl(mixpanel_rows(), dest)
    print(json.dumps({"mixpanel": str(dest), "rows": n}))
    return 0 if n else 1


def read_user_ids(path: pathlib.Path) -> list:
    """The CLI's accepted shapes: {"<query>": [{"id": ...}]}, [id, ...] or [{"id": ...}, ...]."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        if len(data) != 1:
            raise Refused(f"{path.name}: expected one query key, got {len(data)}")
        data = next(iter(data.values()))
    if not isinstance(data, list):
        raise Refused(f"{path.name}: expected a list of ids")
    ids = [x.get("id") if isinstance(x, dict) else x for x in data]
    bad = sum(1 for i in ids if not isinstance(i, str) or not i.strip())
    if bad:
        raise Refused(f"{path.name}: {bad} entries without a string id")
    return ids


def check_mixpanel(path: pathlib.Path) -> dict:
    rows = missing = bad = 0
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                bad += 1
                continue
            rows += 1
            if not isinstance(rec, dict) or not rec.get("userid"):
                missing += 1
    return {"rows": rows, "unparsable": bad, "withoutUserid": missing}


def cmd_check_inputs(args) -> int:
    cli = cli_dir(args.cli_dir)
    paths = input_paths(cli)
    out, problems = {}, []
    for key in ("users", "mixpanel"):
        p = paths[key]
        if not p.is_file():
            problems.append(f"missing {key} input: {p}")
            continue
        check_private(cli, p)
        os.chmod(p, 0o600)
    if paths["users"].is_file():
        try:
            ids = read_user_ids(paths["users"])
            out["users"] = {"ids": len(ids), "distinct": len(set(ids))}
            if not ids:
                problems.append("users.json holds no ids")
        except (Refused, ValueError) as e:
            problems.append(str(e))
    if paths["mixpanel"].is_file():
        m = check_mixpanel(paths["mixpanel"])
        out["mixpanel"] = m
        if not m["rows"] or m["unparsable"] or m["withoutUserid"]:
            problems.append(f"mixpanel.jsonl: {m}")
    out["problems"] = problems
    print(json.dumps(out, indent=2))
    return 1 if problems else 0


def last_json_object(text: str):
    """The last pretty-printed summary object (it has `counts`) in the CLI's output.
    Logger lines ("uid lists written", "Good bye") follow it."""
    dec = json.JSONDecoder()
    for start in reversed([m.start() for m in re.finditer(r"(?m)^\{", text)]):
        try:
            obj, _ = dec.raw_decode(text, start)
        except ValueError:
            continue
        if isinstance(obj, dict) and "counts" in obj:
            return obj
    return None


DELETED_PATTERNS = {
    "lite": re.compile(r"Lite user '"),
    "withoutName": re.compile(r"User found '[^']*' without name"),
    "existing": re.compile(r"User found '[^']*'$", re.M),
    "referencesRemoved": re.compile(r"Should remove all references for user '|Removed all references for user '"),
    # logger.error lines only ("error: ..." with optional colour codes), never profile text
    "errors": re.compile(r"^(?:\x1b\[\d+m)?error(?:\x1b\[\d+m)?:", re.M),
}


def deleted_summary(text: str) -> dict:
    m = re.search(r"Number of deleted users: (\d+)", text)
    counts = {k: len(p.findall(text)) for k, p in DELETED_PATTERNS.items()}
    counts["deletedUsers"] = int(m.group(1)) if m else None
    return counts


def load_held(path: pathlib.Path = None) -> frozenset:
    """The keep list. Missing, empty, unreadable, malformed or readable by others is refused."""
    try:
        return held_uids.load(p=path or HELD_UIDS)
    except held_uids.Refused as e:
        raise Refused(str(e))


def held_count(path: pathlib.Path, held) -> int:
    return sum(1 for i in read_user_ids(path) if i.strip() in held) if path.is_file() else 0


def cmd_exclude_held(args) -> int:
    """Remove the held uids from users.json in place, keeping its shape and mode 600. Counts only."""
    cli = cli_dir(args.cli_dir)
    users = input_paths(cli)["users"]
    held = load_held()
    data = json.loads(users.read_text(encoding="utf-8"))
    key = next(iter(data)) if isinstance(data, dict) else None
    rows = data[key] if key is not None else data
    left = []
    for r in rows:
        uid = str(r.get("id") if isinstance(r, dict) else r).strip()
        if uid in held:
            print(held_uids.skip_line(uid), file=sys.stderr)
        else:
            left.append(r)
    tmp = users.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(left if key is None else {key: left}, f)
    os.replace(tmp, users)
    os.chmod(users, 0o600)
    counts = {"held": len(held), "before": len(rows), "removed": len(rows) - len(left), "after": len(left),
              "kept": len(rows) - len(left)}
    append_run_log({"event": "exclude-held", "utc": utc_now(), "host": socket.gethostname(), **counts})
    print(json.dumps(counts))
    return 0


def cmd_check_held(args) -> int:
    """How many held uids a list holds (users.json, or a CLI output such as deleted.json). Exit 1 if any."""
    cli = cli_dir(args.cli_dir)
    path = pathlib.Path(args.file) if args.file else input_paths(cli)["output"] / "deleted.json"
    held = load_held()
    found = held_count(path, held)
    print(json.dumps({"file": path.name, "held": len(held), "heldFound": found}))
    return 1 if found else 0


def positive_int(value: str) -> int:
    """A sample size: a whole number above 0. A 0 or negative value would run (nearly) the full list."""
    try:
        n = int(value)
    except ValueError:
        n = 0
    if n <= 0:
        raise argparse.ArgumentTypeError("ROWS must be a whole number above 0")
    return n


def write_sample(cli: pathlib.Path, users: pathlib.Path, rows: int) -> pathlib.Path:
    """The first `rows` ids of users.json, mode 600. The export is ordered by updatedAt desc, so the
    head is the most active profiles: the worst case for read load."""
    sample = exports_dir(cli) / f"users-sample-{rows}.json"
    check_private(cli, sample)
    fd = os.open(sample, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(read_user_ids(users)[:rows], f)
    return sample


def cmd_run(args) -> int:
    cli = cli_dir(args.cli_dir)
    if args.apply:
        if not (args.go or "").strip():
            raise Refused("--apply is a prod write: pass --go with Jerome's exact go text")
    if args.sample is not None and (args.apply or args.task != "cleanupUsers"):
        raise Refused("--sample is for a cleanupUsers dry run only")
    paths = input_paths(cli)
    held = load_held()
    # A held account is never read or written by a run: users.json must already be filtered, and the
    # follow-up acts on deleted.json, which must not name one either.
    target = paths["users"] if args.task == "cleanupUsers" else paths["output"] / "deleted.json"
    found = held_count(target, held)
    if found:
        raise Refused(f"{target.name} holds {found} held uid(s): run exclude-held (and re-run the "
                      f"dry run for deleted.json) first")
    print(f"keep list: {len(held)} uid(s) loaded; kept: 0 in {target.name} (exclude-held removed them)", file=sys.stderr)
    ex = exports_dir(cli)
    logs = ex / "logs"
    check_private(cli, logs)
    logs.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    log = logs / f"{args.task}-{'apply' if args.apply else 'dry'}-{stamp}.log"
    env = dict(os.environ, LOGGER_LEVEL="info")
    env.pop("DO_UPDATE", None)
    if args.sample is not None:
        env["CLEANUP_USERS_JSON"] = str(write_sample(cli, paths["users"], args.sample))
        env["CLEANUP_PRIVATE_DIR"] = str(ex / "output-sample")
    if args.apply:
        env["DO_UPDATE"] = "true"
    cmd = ["node", NODE_HEAP, str(cli / "src" / "cli" / "commander.js"), args.task]
    run_entry = {"runId": f"{args.task}-{stamp}", "task": args.task, "apply": args.apply,
                 "sample": args.sample,
                 "command": f"{'DO_UPDATE=true ' if args.apply else ''}aura {args.task}",
                 "host": socket.gethostname(), "startedAt": utc_now()}
    append_run_log(dict(run_entry, event="start"))
    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        if args.apply:
            f.write(f"# go: {args.go}\n")
            f.flush()
        t0 = time.monotonic()
        proc = subprocess.run(cmd, cwd=cli, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True,
                              encoding="utf-8", errors="replace")
        seconds = time.monotonic() - t0
        f.write(proc.stdout)
    out = summarize(args.task, proc.stdout, proc.returncode)
    counts = (out.get("summary") or {}).get("counts") or out.get("counts")
    out["timing"] = timing(seconds, counts, paths["users"] if args.sample is not None else None)
    append_run_log(dict(run_entry, event="end", endedAt=utc_now(), exitCode=proc.returncode,
                        verdict=out["verdict"], counts=counts,
                        **({"resumeRow": out["resumeRow"]} if "resumeRow" in out else {})))
    out.update(task=args.task, apply=args.apply, exitCode=proc.returncode, log=str(log),
               runLog=str(RUN_LOG))
    print(json.dumps(out, indent=2))
    return 0 if out["verdict"] == "ok" else 1


def timing(seconds: float, counts, users_file=None) -> dict:
    """Duration and users per second; for a sample, the full run's projected hours at the same rate.
    The read rate and the RTDB load come from the Firebase owner watching the run, not from here."""
    users = (counts or {}).get("users") or 0
    t = {"seconds": round(seconds, 1),
         "usersPerSecond": round(users / seconds, 1) if seconds and users else None}
    if users_file and t["usersPerSecond"]:
        total = len(read_user_ids(users_file))
        t.update(fullUsers=total, projectedFullHours=round(total / t["usersPerSecond"] / 3600, 1))
    return t


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def append_run_log(entry: dict):
    """One JSON line per run start and end: times, host, command, counts. Never a uid."""
    RUN_LOG.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(RUN_LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


STOP_EXITS = (143, 130)  # SIGTERM, SIGINT


def summarize(task: str, stdout: str, rc: int) -> dict:
    """Counts only. Both tasks print a summary object and exit 1 on a failure. A cleanupDeletedUsers
    run without one comes from a CLI checkout that exits 0 on a caught error: its counts come from
    the log lines and it is never more than `unverified`."""
    summary = last_json_object(stdout)
    if task == "cleanupUsers":
        # SIGTERM or Ctrl-C ends a window on purpose: the CLI writes the uid lists and the summary
        # with resumeRow, the row the next window starts at
        if rc in STOP_EXITS and summary and "counts" in summary and "resumeRow" in summary:
            return {"verdict": "stopped", "summary": summary, "resumeRow": summary["resumeRow"]}
        if rc != 0:
            return {"verdict": "failed", "summary": summary}
        if not summary or "counts" not in summary:
            return {"verdict": "unverified", "reason": "no summary object on stdout"}
        return {"verdict": "ok", "summary": summary}
    if summary and "counts" in summary:
        return {"verdict": "failed" if rc != 0 else "ok", "summary": summary}
    counts = deleted_summary(stdout)
    if rc != 0:
        return {"verdict": "failed", "counts": counts}
    return {"verdict": "unverified", "counts": counts,
            "reason": "no summary object: this CLI checkout exits 0 on a caught error; read errors "
                      "in the log count"}


def row_count(path: pathlib.Path):
    """Lines for JSONL and logs; entries for a JSON list (or a dict of lists)."""
    if path.suffix in (".jsonl", ".log"):
        with path.open("rb") as f:
            return sum(1 for line in f if line.strip())
    if path.suffix == ".json":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return None
        if isinstance(data, list):
            return len(data)
        if isinstance(data, dict):
            return sum(len(v) for v in data.values() if isinstance(v, list))
    return None


def manifest(folder: pathlib.Path) -> list:
    """What a deletion removes, for its log: name, rows, sha256, size, time. No content."""
    import hashlib
    rows = []
    for p in sorted(x for x in folder.rglob("*") if x.is_file()):
        h = hashlib.sha256()
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        st = p.stat()
        rows.append({"file": str(p.relative_to(folder)), "rows": row_count(p), "sha256": h.hexdigest(),
                     "bytes": st.st_size,
                     "modified": dt.datetime.fromtimestamp(st.st_mtime, dt.timezone.utc).isoformat(timespec="seconds")})
    return rows


def cmd_remove(args) -> int:
    """Prints the rm; never runs it. The folder must be the exports folder itself."""
    cli = cli_dir(args.cli_dir)
    ex = exports_dir(cli)
    if not ex.exists():
        print(json.dumps({"exports": str(ex), "exists": False}))
        return 0
    if ex.is_symlink() or ex.name != EXPORTS_NAME:
        raise Refused(f"{ex} is not a '{EXPORTS_NAME}' folder; refusing to print an rm for it")
    if ex in (pathlib.Path.home(), pathlib.Path("/"), cli, cli / "data"):
        raise Refused(f"{ex} is too broad")
    check_private(cli, ex)
    out = {"exports": str(ex), "oldestFileDays": round(age_days(ex), 1),
           "manifest": manifest(ex), "manifestAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "command": f"rm -rf -- '{ex}'"}
    print(json.dumps(out, indent=2))
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cli-dir")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prepare")
    sub.add_parser("export-mixpanel")
    sub.add_parser("check-inputs")
    r = sub.add_parser("run")
    r.add_argument("task", choices=TASKS)
    r.add_argument("--apply", action="store_true", help="DO_UPDATE=true: a prod write")
    r.add_argument("--go", help="Jerome's exact go text, recorded in the private log")
    r.add_argument("--sample", type=positive_int, metavar="ROWS",
                   help="dry run on the first ROWS ids of users.json (outputs to output-sample/)")
    sub.add_parser("exclude-held")
    c = sub.add_parser("check-held")
    c.add_argument("file", nargs="?", help="a uid list (default: output/deleted.json)")
    sub.add_parser("remove")
    args = ap.parse_args()
    handlers = {"prepare": cmd_prepare, "export-mixpanel": cmd_export_mixpanel,
                "check-inputs": cmd_check_inputs, "run": cmd_run, "remove": cmd_remove,
                "exclude-held": cmd_exclude_held, "check-held": cmd_check_held}
    try:
        return handlers[args.cmd](args)
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
