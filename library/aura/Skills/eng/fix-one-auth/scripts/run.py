"""fix-one-auth driver: the daily ONE auth check, and the fixes by shape.

Prints counts and uid prefixes only, never an email, a full uid, a hash or an IP. Lists and exports go
owner-only to the run directory; for prod that directory must be on an encrypted disk image.

  run.py daily          --env prod --out-dir <dir>
  run.py fix-e2         --env prod --run-dir <dir> [--limit N] [--apply --write-as <sa> --window-end <iso>]
  run.py fix-top-email  --env prod --run-dir <dir> [--limit N] [--allow-sign-out] [--apply --write-as <sa> --window-end <iso>]
  run.py profile-email  --env prod --run-dir <dir> --job-dir <UserService worktree> [--apply]
  run.py recheck        --env prod --run-dir <dir> --list <file>
"""
from __future__ import annotations

import argparse
import collections
import datetime
import gzip
import json
import os
import pathlib
import plistlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import gcloud_sa  # noqa: E402
import held_uids  # noqa: E402

PROJECT = {"dev": "aura-dev", "prod": "firebase-auratech16"}
RELAY = "privaterelay.appleid.com"
E2 = "E2 Auth several emails"
E5 = "E5 top-level Auth email empty, sign-in has it"


def p6(uid: str) -> str:
    return held_uids.label(uid) if uid else "-"


def encrypted_image_mounts() -> set[str]:
    """Mount points of encrypted disk images (hdiutil); empty when hdiutil is unavailable."""
    try:
        out = subprocess.run(["hdiutil", "info", "-plist"], capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return set()
    mounts = set()
    for image in plistlib.loads(out).get("images", []):
        if image.get("image-encrypted"):
            mounts.update(e["mount-point"] for e in image.get("system-entities", []) if e.get("mount-point"))
    return mounts


def require_safe_dir(env: str, d: pathlib.Path, mounts: set[str] | None = None) -> None:
    """A prod run directory sits on an encrypted disk image: it holds the full Auth export."""
    if env != "prod":
        return
    mounts = encrypted_image_mounts() if mounts is None else mounts
    real = str(d.resolve())
    if not any(real == m or real.startswith(m.rstrip("/") + "/") for m in mounts):
        sys.exit(f"REFUSED: {d} is not on an encrypted disk image; a prod export goes only there")


def node(script: str, env: str, *args: str, check: bool = True) -> int:
    """Run one worker with the AuraClaude label; its stdout is counts and prefixes only."""
    if not os.environ.get("USER_SERVICE_DIR"):
        sys.exit("USER_SERVICE_DIR (a UserService checkout with node_modules) is required")
    cmd = ["node", str(HERE / script), f"--env={env}", f"--user-agent={gcloud_sa.user_agent()}", *args]
    code = subprocess.run(cmd).returncode
    if check and code not in (0, 2):
        sys.exit(f"{script} failed (exit {code})")
    return code


def last_used_provider(uid: str, project: str) -> str | None:
    """The provider of the latest SignInWithIdp/SignInWithPassword in the Identity Toolkit logs (30 days)."""
    q = f'"{uid}" AND (jsonPayload.methodName:"SignInWithIdp" OR jsonPayload.methodName:"SignInWithPassword")'
    p = gcloud_sa.run(["logging", "read", q, "--project", project, "--freshness=30d", "--limit", "1", "--format=json"],
                      capture_output=True, text=True, encoding="utf-8")
    if p.returncode != 0:
        raise RuntimeError(f"logging read failed as {gcloud_sa.credential()}")
    rows = json.loads(p.stdout or "[]")
    if not rows:
        return None
    jp = rows[0]["jsonPayload"]
    return "password" if "Password" in jp["methodName"] else (jp.get("response") or {}).get("providerId")


def plan_e2(accounts: dict[str, dict], held: set[str], lookup) -> tuple[list[dict], collections.Counter]:
    """Each E2 Auth goes to the plan (keep its last-used sign-in) or to a for-Jerome count.

    R15 item 3 (Jerome, 2026-11-18): when a person's provider email changes, the email they last signed in
    with wins. Planned: one sign-in, or password plus exactly one other sign-in when that other one was last
    used. A password sign-in's email is the Auth email itself, so it moves with it and R2 holds. Two or more
    provider sign-ins with different emails would leave one behind, and removing a sign-in is Jerome's call
    per case, so those go to him with the last-used provider named.
    """
    plan, t = [], collections.Counter()
    for uid in sorted(accounts):
        r = accounts[uid]
        if r is None:
            t["gone"] += 1
            continue
        if uid in held:
            t["held or staff (check with SecurityAuth)"] += 1
            continue
        if r["disabled"]:
            t["for Jerome: disabled"] += 1
            continue
        prov = [p["providerId"] for p in r["providers"]]
        others = [p for p in prov if p != "password"]
        if len(others) > 1:
            last = lookup(uid)
            t[f"for Jerome: several sign-ins (last used: {last.split('.')[0] if last in prov else 'unclear'})"] += 1
            continue
        keep = prov[0] if len(prov) == 1 else lookup(uid) if prov else None
        if keep not in prov or keep == "password":
            t["for Jerome: no clear last-used provider sign-in"] += 1
            continue
        kept_email = next((p.get("email") or "" for p in r["providers"] if p["providerId"] == keep), "").lower()
        # A real email replaced by an Apple private relay is the person's privacy choice, not a provider rename.
        if RELAY in kept_email and RELAY not in (r.get("email") or "").lower():
            t["for Jerome: switched to Apple relay"] += 1
            continue
        plan.append({"uid": uid, "keep": keep})
        t["planned"] += 1
    return plan, t


def read_export(path: pathlib.Path, uids: set[str]) -> dict[str, dict | None]:
    found: dict[str, dict | None] = {u: None for u in uids}
    with gzip.open(path, "rt", encoding="utf-8") as f:
        next(f, None)
        for line in f:
            r = json.loads(line)
            if r.get("uid") in found:
                found[r["uid"]] = r
    return found


def write_private(path: pathlib.Path, data) -> None:
    path.write_text(json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8")
    os.chmod(path, 0o600)


def cmd_daily(a) -> None:
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d")
    d = pathlib.Path(a.out_dir) / stamp
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    require_safe_dir(a.env, d)
    export = d / "auth-export.jsonl.gz"
    node("export.js", a.env, f"--out={export}")
    node("classify.js", a.env, f"--dir={d}")
    subprocess.run(["node", str(HERE / "dup_emails.js"), str(export), str(d / "dup-emails.json")], check=True)
    violations = read_json(d / "violations.json")
    held = set(read_json(d / "staff-flagged.json")) | held_uids.load(required=False)
    accounts = read_export(export, set(violations.get(E2, [])))
    plan, t = plan_e2(accounts, held, lambda u: last_used_provider(u, PROJECT[a.env]))
    write_private(d / "e2-plan.json", plan)
    write_private(d / "e5-list.txt", "\n".join(u for u in violations.get(E5, []) if u not in held) + "\n")
    print("E2 plan", json.dumps(dict(t)))
    print("E2 proposed:", len(plan), " ".join(f"{p6(x['uid'])}/{x['keep'].split('.')[0]}" for x in plan))
    print("E5 missing top-level email:", len(violations.get(E5, [])))
    print("run dir:", d)


def read_json(path: pathlib.Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_lines(path: pathlib.Path) -> list[str]:
    return [l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()] if path.exists() else []


def next_batch(uids: list[str], d: pathlib.Path, attempted: str, limit: int, apply: bool) -> list[str]:
    """The next `limit` uids not handled by an earlier batch and not on the keep list (refused when a write
    can't trust the list)."""
    try:
        held = held_uids.load(required=apply)
    except held_uids.Refused as e:
        sys.exit(f"REFUSED: {e}")
    done = set(read_lines(d / attempted))
    todo, kept = held_uids.split([u for u in uids if u not in done], held)
    if kept:
        print("keep list:", len(kept), "skipped")
    return todo[:limit]


def write_flags(a) -> list[str]:
    if not a.apply:
        return []
    if a.env == "prod" and not (a.write_as and a.window_end):
        sys.exit("REFUSED: a prod write needs --write-as and --window-end (the IAM window Jerome opened)")
    out = ["--apply"]
    if a.write_as:
        out.append(f"--write-as={a.write_as}")
    if a.window_end:
        out.append(f"--window-end={a.window_end}")
    return out


def cmd_fix_e2(a) -> None:
    d = pathlib.Path(a.run_dir)
    require_safe_dir(a.env, d)
    flags = write_flags(a)
    plan = read_json(d / "e2-plan.json")
    keep = {x["uid"]: x["keep"] for x in plan}
    uids = next_batch([x["uid"] for x in plan], d, "e2-attempted.txt", a.limit, a.apply)
    batch = d / f"e2-batch-{len(list(d.glob('e2-batch-*.json')))}.json"
    write_private(batch, [{"uid": u, "keep": keep[u]} for u in uids])
    print("E2 batch:", len(uids), "of", len(plan), "planned", flush=True)
    node("fix_e2.js", a.env, f"--plan={batch}", f"--staff-flagged={d / 'staff-flagged.json'}",
         f"--before={d / 'e2-before.jsonl'}", f"--done={d / 'e2-done.txt'}",
         f"--attempted={d / 'e2-attempted.txt'}", *flags)


def cmd_fix_top_email(a) -> None:
    d = pathlib.Path(a.run_dir)
    require_safe_dir(a.env, d)
    flags = write_flags(a)
    extra = ["--allow-sign-out"] if a.allow_sign_out else []
    all_uids = read_lines(d / "e5-list.txt")
    uids = next_batch(all_uids, d, "e5-attempted.txt", a.limit, a.apply)
    batch = d / f"e5-batch-{len(list(d.glob('e5-batch-*.txt')))}.txt"
    write_private(batch, "\n".join(uids) + "\n")
    print("E5 batch:", len(uids), "of", len(all_uids), flush=True)
    node("fix_top_email.js", a.env, f"--list={batch}", f"--staff-flagged={d / 'staff-flagged.json'}",
         f"--changed={d / 'e5-changed.txt'}", f"--prior={d / 'e5-prior.jsonl'}",
         f"--attempted={d / 'e5-attempted.txt'}", *extra, *flags)


def require_job_worktree(job_dir: pathlib.Path) -> None:
    """The profile-email job runs from a fresh worktree of UserService origin/master, never the shared checkout."""
    git = lambda *x: subprocess.run(["git", "-C", str(job_dir), *x], capture_output=True, text=True,
                                    encoding="utf-8").stdout.strip()
    if git("rev-parse", "--git-dir") == git("rev-parse", "--git-common-dir"):
        sys.exit("REFUSED: --job-dir is the shared UserService checkout; use a worktree of origin/master")
    if git("rev-parse", "HEAD") != git("rev-parse", "origin/master"):
        sys.exit("REFUSED: --job-dir is not at origin/master")


def service_env(env: str) -> dict:
    """UserService's env file for this project, parsed by its own dotenv (a worktree has no .env of its own;
    dotenv never overrides a variable already set, so these win)."""
    us = os.environ.get("USER_SERVICE_DIR")
    if not us:
        sys.exit("USER_SERVICE_DIR is required")
    f = pathlib.Path(us) / (".env.development" if env == "dev" else ".env")
    js = ("const r=require('module').createRequire(process.argv[1]+'/package.json');"
          "process.stdout.write(JSON.stringify(r('dotenv').parse(require('fs').readFileSync(process.argv[2],'utf8'))))")
    parsed = json.loads(subprocess.run(["node", "-e", js, us, str(f)], capture_output=True, text=True, encoding="utf-8",
                                       check=True).stdout)
    if parsed.get("FIREBASE_PROJECT_ID") != PROJECT[env]:
        sys.exit("REFUSED: the UserService env file is for another project")
    return parsed


def cmd_profile_email(a) -> None:
    d = pathlib.Path(a.run_dir)
    require_safe_dir(a.env, d)
    job = pathlib.Path(a.job_dir)
    require_job_worktree(job)
    profiles = d / a.profiles
    if not os.environ.get("GCP_KEY"):
        sys.exit("GCP_KEY is required: the job's Google credentials come from it, never the personal login")
    # The job reads Google credentials from a key file: a 0600 copy of GCP_KEY, removed when the job ends.
    fd, key = tempfile.mkstemp(prefix="gcp-key-", suffix=".json", dir=os.environ.get("CLAUDE_CODE_TMPDIR") or None)
    try:
        os.write(fd, os.environ["GCP_KEY"].encode())
        os.close(fd)
        env = {**os.environ, **service_env(a.env), "GOOGLE_APPLICATION_CREDENTIALS": key}
        cmd = ["npm", "run", "--silent", "job:profile-email", "--", f"--profiles={profiles}"] + (["--apply"] if a.apply else [])
        code = subprocess.run(cmd, cwd=job, env=env).returncode
    finally:
        os.unlink(key)
    if code:
        sys.exit(f"profile-email job failed (exit {code})")


def cmd_recheck(a) -> None:
    d = pathlib.Path(a.run_dir)
    require_safe_dir(a.env, d)
    if node("recheck.js", a.env, f"--list={d / a.list}"):
        sys.exit(2)


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("daily", "fix-e2", "fix-top-email", "profile-email", "recheck"):
        s = sub.add_parser(name)
        s.add_argument("--env", choices=("dev", "prod"), required=True)
        if name == "daily":
            s.add_argument("--out-dir", required=True)
            continue
        s.add_argument("--run-dir", required=True)
        if name in ("fix-e2", "fix-top-email"):
            s.add_argument("--limit", type=int, default=100)
            s.add_argument("--apply", action="store_true")
            s.add_argument("--write-as")
            s.add_argument("--window-end")
        if name == "fix-top-email":
            s.add_argument("--allow-sign-out", action="store_true")
        if name == "profile-email":
            s.add_argument("--job-dir", required=True)
            s.add_argument("--profiles", default="e2-done.txt")
            s.add_argument("--apply", action="store_true")
        if name == "recheck":
            s.add_argument("--list", default="e2-done.txt")
    a = ap.parse_args(argv)
    {"daily": cmd_daily, "fix-e2": cmd_fix_e2, "fix-top-email": cmd_fix_top_email,
     "profile-email": cmd_profile_email, "recheck": cmd_recheck}[a.cmd](a)


if __name__ == "__main__":
    main()
