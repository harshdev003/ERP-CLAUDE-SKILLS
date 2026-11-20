#!/usr/bin/env python3
"""Run one step of a plan_hold.py plan, after the person approving it said go. One step per call.

  run_step.py --plan plan.json --step 05            show exactly what step 05 does (default: nothing runs)
  run_step.py --plan plan.json --step 05 --apply    run it (a service-account step)
  run_step.py --plan plan.json --step 03 --done     record that a person ran a PERSON step
  run_step.py --plan plan.json status               every step's state, and each project's copy operations

State lives next to the plan (<plan>.state.json, mode 600). A step runs only when every step it waits on
is done, and a copy only when no other copy of that project is running (Logging allows one active copy
per project: a second fails with "Active copy operations count is at the limit"). PERSON steps are IAM
grants and removals, run by a person with their own gcloud; this script prints them and never runs them.
GCP calls run as GCP_KEY (gcloud_sa.py). Output is counts and names: never a uid, address or log line.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shlex
import sys
import tempfile
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gcloud_sa  # noqa: E402

# Never written to the hold: credentials, not evidence.
AUTH_DROP = {"passwordHash", "salt", "passwordUpdatedAt", "validSince"}
AUTH_BATCH = 100       # Identity Toolkit accounts:lookup takes at most 100 ids per call
READ_LIMIT = 1000000   # a sample-hour count at this cap is truncated: unverified, never compared
JOB_POLL_S = 10


class Refused(SystemExit):
    pass


# --- state ----------------------------------------------------------------------------------------
def load(path):
    with open(path, encoding="utf-8") as f:
        p = json.load(f)
    st_path = path + ".state.json"
    st = {}
    if os.path.exists(st_path):
        with open(st_path, encoding="utf-8") as f:
            st = json.load(f)
    return p, st, st_path


def save(st, st_path):
    fd = os.open(st_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=1)


# --- GCP ------------------------------------------------------------------------------------------
def gcloud(argv, run=None, quiet=False):
    """quiet: a focus step's arguments and gcloud's echo of them hold uids, so its error says only which
    command failed."""
    out = (run or gcloud_sa.run)(argv, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if out.returncode:
        why = "(details withheld: the filter holds the focus accounts)" if quiet else (out.stderr or "").strip()[-300:]
        raise Refused(f"gcloud {' '.join(argv[:2])} failed as {gcloud_sa.credential()}: {why}")
    return out.stdout


def caller(run=None) -> str:
    email = gcloud(["config", "get-value", "account"], run).strip()
    return f"serviceAccount:{email}" if email.endswith("gserviceaccount.com") else f"user:{email}"


def http(method, url, body=None, run=None, opener=None):
    token = gcloud(["auth", "print-access-token"], run).strip().splitlines()[-1]
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                                          "User-Agent": gcloud_sa.user_agent()})
    with (opener or urllib.request.urlopen)(req) as r:
        return json.loads(r.read() or b"{}")


def upload(obj, bucket, name, run=None):
    """Write a JSON object to gs://bucket/name through a private temp file."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, os.path.basename(name))
        fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f)
        gcloud(["storage", "cp", path, f"gs://{bucket}/{name}"], run)


# --- steps ----------------------------------------------------------------------------------------
def resolve(argv, member):
    return [x.replace("<CALLER>", member) if member else x for x in argv]


def command(step, member, argv=None):
    """The step as a gcloud line to show. A focus copy's filter holds uids: never printed."""
    argv = resolve(argv or step["argv"], member)
    if step.get("scope") == "focus":
        argv = ["--log-filter=<window AND the focus accounts>" if x.startswith("--log-filter=") else x for x in argv]
    return "gcloud " + " ".join(argv)


def person_copy_command(step, st_path):
    """The copy line for a person to paste into a shell: every argument is shell-quoted, because the filter
    carries quotes, spaces and < >. A focus copy's filter is written to a private file, never printed, and
    the line reads it with $(cat …) inside double quotes."""
    parts = []
    for x in step["argv"]:
        if step.get("scope") == "focus" and x.startswith("--log-filter="):
            path = f"{st_path[:-len('.state.json')]}.filter-{step['id']}.txt"
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(x[len("--log-filter="):])
            parts.append(f'--log-filter="$(cat {shlex.quote(path)})"')
        else:
            parts.append(shlex.quote(x))
    return "gcloud " + " ".join(parts)


def copy_running(plan, st, project, sid):
    return [s["id"] for s in plan["steps"] if s["kind"] == "copy" and s["project"] == project
            and s["id"] != sid and st.get(s["id"], {}).get("status") == "running"]


def run_copy(step, run=None):
    out = json.loads(gcloud([*step["argv"], "--format=json"], run, quiet=step.get("scope") == "focus") or "{}")
    name = out.get("name") or (out.get("operation") or {}).get("name") or ""
    if not name:
        raise Refused("the copy started but returned no operation name: check `status`")
    return {"status": "running", "op": name}


def run_verify(step, run=None, sample_hour=None):
    """Objects and bytes in the bucket, and the sample hour: entries in the source vs lines copied.
    Copied objects are named <log name>/YYYY/MM/DD/HH:00:00_HH:59:59_S<n>.json, one entry per line."""
    du = gcloud(["storage", "du", "-s", f"gs://{step['bucket']}"], run).split()
    objects = [l.strip() for l in gcloud(["storage", "ls", "-r", f"gs://{step['bucket']}/**"], run).splitlines()
               if l.startswith("gs://")]
    if not objects:
        raise Refused(f"gs://{step['bucket']} is empty: UNVERIFIED, never a match")
    h = sample_hour or step["sample_hour"]  # 2026-10-01T10
    nxt = (dt.datetime.strptime(h, "%Y-%m-%dT%H") + dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H")
    tag = f"/{h[:4]}/{h[5:7]}/{h[8:10]}/{h[11:13]}:00:00_"
    copied = sum(len([l for l in gcloud(["storage", "cat", o], run).splitlines() if l.strip()])
                 for o in objects if tag in o)
    flt = f'{step["filter"]} AND timestamp>="{h}:00:00Z" AND timestamp<"{nxt}:00:00Z"'
    source = len(gcloud(["logging", "read", flt, f"--bucket={step['log_bucket']}", "--location=global",
                         "--view=_AllLogs", "--project", step["project"], "--format=value(insertId)",
                         f"--limit={READ_LIMIT}"], run, quiet=step.get("scope") == "focus").split())
    result = {"status": "done", "objects": len(objects), "bytes": int(du[0]) if du and du[0].isdigit() else None,
              "sample_hour": h, "sample_source": source, "sample_copied": copied}
    if source >= READ_LIMIT:
        result["status"] = "unverified"
        print(f"UNVERIFIED {h}: the source count hit the {READ_LIMIT} read cap. Re-run with --sample-hour on a quieter hour.")
    elif not source and not copied:
        # An empty read is unverified, never a match: the hour may be outside the source's retention.
        result["status"] = "unverified"
        print(f"UNVERIFIED {h}: no entries in the source or the copy. Re-run with --sample-hour on an hour that has some.")
    elif source != copied:
        result["status"] = "mismatch"
        print(f"MISMATCH {h}: {source} entries in the source, {copied} copied. Not verified.")
    return result


def run_analytics(step, run=None, opener=None, sleep=time.sleep):
    """Submit one extract job per day, then wait for every job: the caller's write access is removed
    after this step, so it must not end while a job is still writing."""
    jobs = []
    for day in step["days"]:
        body = {"configuration": {"extract": {
            "sourceTable": {"projectId": step["project"], "datasetId": step["dataset"], "tableId": f"events_{day}"},
            "destinationUris": [f"gs://{step['bucket']}/events_{day}/part-*.avro"], "destinationFormat": "AVRO"}}}
        job = http("POST", f"https://bigquery.googleapis.com/bigquery/v2/projects/{step['project']}/jobs", body,
                   run, opener)
        ref = job.get("jobReference") or {}
        jobs.append((ref.get("jobId", "?"), ref.get("location", "US")))
    failed = []
    for job_id, loc in jobs:
        while True:
            j = http("GET", f"https://bigquery.googleapis.com/bigquery/v2/projects/{step['project']}/jobs/{job_id}"
                     f"?location={loc}", None, run, opener)
            st = j.get("status") or {}
            if st.get("state") == "DONE":
                if st.get("errorResult"):
                    failed.append(job_id)
                break
            sleep(JOB_POLL_S)
    if failed:
        raise Refused(f"{len(failed)} of {len(jobs)} extract job(s) failed: {', '.join(failed)}")
    return {"status": "done", "extract_jobs": len(jobs), "job_ids": [j for j, _ in jobs]}


def run_metrics(step, run=None, opener=None):
    total = 0
    for metric in step["metrics"]:
        series, token = [], ""
        while True:
            q = {"filter": f'metric.type="{metric}"', "interval.startTime": step["start"],
                 "interval.endTime": step["end"], "pageSize": "1000"}
            if token:
                q["pageToken"] = token
            r = http("GET", f"https://monitoring.googleapis.com/v3/projects/{step['project']}/timeSeries?"
                     + urllib.parse.urlencode(q), None, run, opener)
            series += r.get("timeSeries", [])
            token = r.get("nextPageToken", "")
            if not token:
                break
        upload({"metric": metric, "start": step["start"], "end": step["end"], "timeSeries": series},
               step["bucket"], metric.replace("/", "_") + ".json", run)
        total += len(series)
    return {"status": "done", "metric_types": len(step["metrics"]), "series": total}


def run_auth(step, run=None, opener=None):
    with open(step["uids_file"], encoding="utf-8") as f:
        uids = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    # accounts:lookup resolves a Firebase uid (localId) or an email; a provider-prefixed id has no lookup
    # field here, so it is counted as unsupported rather than silently missed.
    emails = [u for u in uids if "@" in u]
    local = [u for u in uids if "@" not in u and ":" not in u]
    unsupported = len(uids) - len(emails) - len(local)
    users = []
    for field, ids in (("localId", local), ("email", emails)):
        for i in range(0, len(ids), AUTH_BATCH):
            r = http("POST", f"https://identitytoolkit.googleapis.com/v1/projects/{step['project']}/accounts:lookup",
                     {field: ids[i:i + AUTH_BATCH]}, run, opener)
            users += [{k: v for k, v in u.items() if k not in AUTH_DROP} for u in r.get("users", [])]
    upload({"users": users}, step["bucket"], "auth-records.json", run)
    result = {"status": "done", "asked": len(uids), "found": len(users), "unsupported": unsupported}
    if len(users) < len(uids):
        # Fewer records than asked is never a quiet success: the hold says what it does not hold.
        result["status"] = "partial"
        print(f"PARTIAL: {len(users)} of {len(uids)} account(s) found ({unsupported} provider-prefixed id(s) "
              "can't be looked up); the rest are deleted or unknown. Say so in the hold write-up.")
    return result


def main(argv=None, run=None, opener=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--step")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--done", action="store_true", help="record that a person ran this PERSON step")
    ap.add_argument("--sample-hour", help="verify: YYYY-MM-DDTHH to compare instead of the plan's")
    ap.add_argument("cmd", nargs="?", choices=["status"])
    a = ap.parse_args(argv)
    plan, st, st_path = load(a.plan)
    if a.cmd == "status":
        return status(plan, st, st_path, run)
    step = next((s for s in plan["steps"] if s["id"] == a.step), None)
    if not step:
        raise Refused(f"no step {a.step} in the plan")
    if st.get(step["id"], {}).get("status") == "done":
        print(f"{step['id']} already done")
        return 0
    # Cleanup (revoke, restrict) must never be stuck behind an outcome: it waits only until the steps before
    # it have run or can no longer run (something they wait on failed). Anything else waits for done, or
    # partial (an export that wrote what it could and said so).
    by_id = {s["id"]: s for s in plan["steps"]}

    def settled(d):
        rec = st.get(d, {}).get("status")
        if rec in ("done", "partial", "mismatch", "unverified", "failed"):
            return True
        return rec is None and any(st.get(x, {}).get("status") == "failed" or (not st.get(x) and settled(x))
                                   for x in by_id[d]["after"])

    if step["kind"] in ("revoke", "restrict", "check-iam"):
        waiting = [d for d in step["after"] if not settled(d)]
    else:
        waiting = [d for d in step["after"] if st.get(d, {}).get("status") not in ("done", "partial")]
    if waiting:
        raise Refused(f"{step['id']} waits on step(s) {', '.join(waiting)}")
    member = caller(run) if any("<CALLER>" in x for argv in (step.get("argvs") or [step.get("argv") or []])
                                for x in argv) else ""
    print(f"{step['id']} {step['kind']}: {step['desc']}")
    if step["runs_as"] == "person":
        if a.done:
            st[step["id"]] = {"status": "done", "by": "person"}
            save(st, st_path)
            print("recorded as done")
            return 0
        print("PERSON step: run it with your own gcloud, then re-run with --done:")
        if step["kind"] == "copy":
            # The service account can't copy log entries (ENGBACK-2333). A focus copy's filter holds uids, so
            # it goes to a mode-600 file next to the plan and the printed line reads it from there.
            print("  # user-gcloud: logging copy (needs logging.buckets.copyLogEntries on the source project)")
            print("  " + person_copy_command(step, st_path))
            print("  It returns at once. Record --done only when the copy operation has finished:")
            print(f"  gcloud logging operations list --project={step['project']} --location=global "
                  "--operation-filter='request_type=CopyLogEntries'")
            return 0
        print("  # user-gcloud: " + ("create the hold bucket" if step["kind"] == "create" else "IAM on the legal-hold bucket"))
        for argv in step.get("argvs") or [step["argv"]]:
            print("  " + command(step, member, argv))
        if step.get("expect"):
            # The removal can be undone behind our back (ENGBACK-2176): the person confirms the end state.
            print("  Confirm the bindings hold only: " + ", ".join(step["expect"]["members"]))
            print("  and that none is a " + ", ".join(step["expect"]["absent"]) + " binding. If one is, remove")
            print("  it, re-read, and only then record --done.")
        return 0
    if step["kind"] == "copy" and copy_running(plan, st, step["project"], step["id"]):
        raise Refused(f"another copy of {step['project']} is running: one active copy per project; check `status`")
    if not a.apply:
        print("dry run; pass --apply after the go:")
        print("  " + (command(step, member) if step["argv"] else f"{step['kind']} for {step['project']}"))
        return 0
    try:
        result = execute(step, a, run, opener)
    except Refused as e:
        # A step that ran and failed is recorded failed, so its cleanup can still go ahead.
        st[step["id"]] = {"status": "failed", "error": str(e)[:300]}
        save(st, st_path)
        raise
    st[step["id"]] = result
    save(st, st_path)
    print(" ".join(f"{k}={v}" for k, v in result.items() if k not in ("op", "job_ids")))
    return 0


def execute(step, a, run=None, opener=None):
    if step["kind"] == "copy":
        result = run_copy(step, run)
    elif step["kind"] == "verify":
        result = run_verify(step, run, a.sample_hour)
    elif step["kind"] in ("analytics", "metrics", "auth"):
        result = {"analytics": run_analytics, "metrics": run_metrics, "auth": run_auth}[step["kind"]](step, run, opener)
    else:
        gcloud(step["argv"], run)
        result = {"status": "done"}
    return result


def status(plan, st, st_path, run=None) -> int:
    """Refresh running copies from Logging and print every step's state (counts only)."""
    ops = {}
    for project in {s["project"] for s in plan["steps"] if st.get(s["id"], {}).get("status") == "running"}:
        # List and match by full name: describe by short id returned NOT_FOUND (ENGBACK-2176).
        for op in json.loads(gcloud(["logging", "operations", "list", "--location=global", "--project", project,
                                     "--operation-filter=request_type=CopyLogEntries", "--format=json"], run) or "[]"):
            ops[op.get("name")] = op
    for s in plan["steps"]:
        rec = st.get(s["id"], {})
        if s["kind"] == "copy" and rec.get("status") == "running" and rec.get("op") in ops:
            op, meta = ops[rec["op"]], ops[rec["op"]].get("metadata") or {}
            rec["progress"] = meta.get("progress")
            if op.get("done") or meta.get("state") in ("OPERATION_STATE_SUCCEEDED", "OPERATION_STATE_FAILED",
                                                       "OPERATION_STATE_CANCELLED"):
                rec["status"] = "done" if meta.get("state") == "OPERATION_STATE_SUCCEEDED" and not op.get("error") \
                    else "failed"
            st[s["id"]] = rec
    save(st, st_path)
    for s in plan["steps"]:
        rec = st.get(s["id"], {})
        extra = " ".join(f"{k}={v}" for k, v in rec.items() if k in ("progress", "objects", "bytes", "series", "found"))
        print(f"{s['id']} {rec.get('status', 'todo'):8} {s['kind']:10} {s['desc']} {extra}".rstrip())
    return 1 if any(st.get(s["id"], {}).get("status") == "failed" for s in plan["steps"]) else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Refused as e:
        print(f"REFUSED: {e}")
        sys.exit(2)
