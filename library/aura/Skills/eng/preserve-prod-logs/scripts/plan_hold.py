#!/usr/bin/env python3
"""Plan a legal-hold copy of prod logs (and optional Analytics, RTDB metrics and per-account bundle). Pure.

  plan_hold.py --project firebase-auratech16 [--project ...] --start 2026-09-20T00:00Z --end 2026-10-08T16:05Z
               --card <card> --member user:<redacted-email> [--member ...]
               [--log-buckets _Default,_Required] [--analytics-dataset analytics_153603506]
               [--metrics firebasedatabase.googleapis.com/network/api_hits_count,...]
               [--uids-file ~/.claude/security/uids.txt] [--project-number P=123] [--out plan.json]

Prints a numbered plan (counts and names, never a uid) and writes it as JSON for run_step.py. Every step
that creates, grants, copies or exports is run one at a time, after the person approving it says go; the
plan itself touches nothing. It never changes a log bucket's retention (Jerome, 2026-11-03).

Steps, per source (project x log bucket), each into its own GCS bucket because `gcloud logging copy`
takes a bucket and no path prefix, and two sources would collide on object names:
  create      the bucket: Coldline, uniform access, public access prevention, versioning, retention
              UNLOCKED (never locked here: locking is irreversible, only on counsel's request)
  grant       named members and the service account read access (person-run IAM)
  grant-temp  objectCreator for the source's Logging service agent and the caller (person-run IAM);
              the copy checks the caller can write too
  copy        `gcloud logging copy` for the window; one active copy per project, _Default first
  verify      object count and bytes, and a sample hour compared with the source
  revoke      the temporary objectCreator grants (person-run IAM)
Optional: analytics (BigQuery events_* tables extracted to their own bucket), metrics (Cloud Monitoring
time series as JSON; kept 6 weeks only, so export early), and with --uids-file a focused bundle: the
accounts' Auth records (no password hashes) and a filtered copy of each source.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import writeup  # noqa: E402

PREFIX = "aura-legal-hold"
# Exactly 400 days: "400d" came back as 34495200 s (about 399.25 days) on ENGBACK-2176.
RETENTION = "34560000s"
LOCATION = "US"
# Where the hold buckets live, and the short names ENGBACK-2176's buckets use (aura-legal-hold-logs-fa16-default).
HOLD_PROJECT = "aura-main"
SHORT = {"firebase-auratech16": "fa16"}
# Each source's Logging service agent writes the copy: service-<number>@gcp-sa-logging.
PROJECT_NUMBERS = {"firebase-auratech16": "1002430946654", "aura-main": "73980405738"}
# A new bucket gives the hold project's owners, editors and viewers access through these. They are removed
# only AFTER the named members are granted: removing them first locked everyone out (ENGBACK-2176).
LEGACY = (("projectOwner", "roles/storage.legacyBucketOwner"), ("projectOwner", "roles/storage.legacyObjectOwner"),
          ("projectEditor", "roles/storage.legacyBucketOwner"), ("projectEditor", "roles/storage.legacyObjectOwner"),
          ("projectViewer", "roles/storage.legacyBucketReader"), ("projectViewer", "roles/storage.legacyObjectReader"))
VIEW_ROLE = "roles/storage.objectViewer"
ADMIN_ROLE = "roles/storage.admin"
TEMP_ROLE = "roles/storage.objectCreator"
DEFAULT_METRICS = ("firebasedatabase.googleapis.com/network/api_hits_count",
                   "firebasedatabase.googleapis.com/network/sent_bytes_count")


def ts(v: str) -> dt.datetime:
    t = dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def bucket_name(*parts: str) -> str:
    """A valid GCS name (lowercase, 3-63 chars); a long one keeps a hash of the full name."""
    name = re.sub(r"[^a-z0-9-]+", "-", "-".join(p.strip("_").lower() for p in (PREFIX, *parts))).strip("-")
    if len(name) > 63:
        name = name[:54].rstrip("-") + "-" + hashlib.sha256(name.encode()).hexdigest()[:8]
    return name


def read_uids(path: str | None) -> list[str]:
    if not path:
        return []
    with open(os.path.expanduser(path), encoding="utf-8") as f:
        uids = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    # Firebase uids, provider-prefixed ids and emails; never a quote, so the log filter can't be broken.
    bad = [i for i, u in enumerate(uids, 1) if not re.fullmatch(r"[A-Za-z0-9:_.=@+-]{1,128}", u)]
    if bad:
        raise SystemExit(f"--uids-file: line(s) {bad} are not uids")
    return uids


def plan(a: argparse.Namespace, uids: list[str]) -> dict:
    start, end = ts(a.start), ts(a.end)
    if not start < end:
        raise SystemExit("--start must be before --end")
    window = f'timestamp>="{iso(start)}" AND timestamp<"{iso(end)}"'
    # The verify step compares one hour of the source with what was copied: the window's last full hour,
    # the likeliest still to be in a log bucket's retention. run_step.py --sample-hour picks another.
    sample = a.sample_hour or (max(start, end - dt.timedelta(hours=1))).strftime("%Y-%m-%dT%H")
    numbers = {**PROJECT_NUMBERS, **dict(x.split("=", 1) for x in a.project_number)}
    hold = a.hold_project
    hid = a.hold_id or a.card  # in every bucket name: a second hold must never write into the first's bucket
    steps: list[dict] = []

    def add(kind, project, desc, argv=None, runs_as="sa", after=(), **extra):
        sid = f"{len(steps) + 1:02d}"
        steps.append({"id": sid, "kind": kind, "project": project, "desc": desc, "argv": argv,
                      "runs_as": runs_as, "after": list(after), **extra})
        return sid

    admin = a.admin or a.member[0]

    def bind(name, member, role, remove=False):
        return ["storage", "buckets", "remove-iam-policy-binding" if remove else "add-iam-policy-binding",
                f"gs://{name}", f"--member={member}", f"--role={role}"]

    def new_bucket(project, name, purpose):
        """Create, version and grant. The admin's bucket-level storage.admin is what lets a person change
        the bucket's IAM once the legacy project access is gone (ENGBACK-2176 lost it and was locked out)."""
        made = add("create", project, f"create gs://{name} ({purpose})",
                   ["storage", "buckets", "create", f"gs://{name}", "--project", hold, "--location", LOCATION,
                    "--default-storage-class", "COLDLINE", "--uniform-bucket-level-access",
                    "--public-access-prevention", "--retention-period", RETENTION], runs_as=a.create_as)
        ver = add("create", project, f"turn on versioning for gs://{name}",
                  ["storage", "buckets", "update", f"gs://{name}", "--versioning"], runs_as=a.create_as,
                  after=[made])
        grants = [add("grant", project, f"grant {m} read on gs://{name}", bind(name, m, VIEW_ROLE),
                      runs_as="person", after=[ver]) for m in a.member]
        grants.append(add("grant", project, f"grant {admin} admin on gs://{name} (keeps IAM changeable)",
                          bind(name, admin, ADMIN_ROLE), runs_as="person", after=[ver]))
        return grants

    def restrict(project, name, after):
        """Last for each bucket: the hold project's legacy access goes once every grant, copy and revoke is done,
        then a person re-reads the bucket's IAM. On ENGBACK-2176 the legacy bindings came back after the first
        removal, so the removal alone is never taken as the end state."""
        gone = add("restrict", project, f"remove the hold project's legacy access to gs://{name}", runs_as="person",
                   after=after, argvs=[bind(name, f"{who}:{hold}", role, remove=True) for who, role in LEGACY])
        add("check-iam", project, f"re-read gs://{name}'s IAM: only the readers and the admin remain (last)",
            ["storage", "buckets", "get-iam-policy", f"gs://{name}", "--format=json"], runs_as="person",
            after=[gone], expect={"members": sorted({*a.member, admin}),
                                  "absent": ["projectOwner:", "projectEditor:", "projectViewer:", TEMP_ROLE]})

    def caller_temp(project, name, ready):
        """The caller writes (objectCreator) and, to verify, reads (objectViewer); both removed after."""
        return add("grant-temp", project, f"temporary write and read for the caller on gs://{name}",
                   runs_as="person", after=ready,
                   argvs=[bind(name, "<CALLER>", TEMP_ROLE), bind(name, "<CALLER>", VIEW_ROLE)])

    def caller_revoke(project, name, done):
        return add("revoke", project, f"remove the caller's temporary write and read on gs://{name}",
                   runs_as="person", after=[done],
                   argvs=[bind(name, "<CALLER>", TEMP_ROLE, True), bind(name, "<CALLER>", VIEW_ROLE, True)])

    for project in a.project:
        agent = (f"serviceAccount:service-{numbers[project]}@gcp-sa-logging.iam.gserviceaccount.com"
                 if project in numbers else "serviceAccount:service-<PROJECT_NUMBER>@gcp-sa-logging.iam.gserviceaccount.com")
        prev_copy = None
        short = SHORT.get(project, project)
        for lb in a.log_buckets:
            sources = [("full", window, bucket_name("logs", short, lb, hid))]
            if uids:
                terms = " OR ".join(f'"{u}"' for u in uids)
                sources.append(("focus", f"{window} AND ({terms})", bucket_name("logs", short, lb, hid, "focus")))
            for scope, flt, name in sources:
                ready = new_bucket(project, name, f"{lb} {scope}")
                temps = [add("grant-temp", project, f"temporary objectCreator for the Logging service agent on gs://{name}",
                             bind(name, agent, TEMP_ROLE), runs_as="person", after=ready),
                         caller_temp(project, name, ready)]
                copy = add("copy", project, f"copy {lb} ({scope}) to gs://{name}",
                           ["logging", "copy", lb, f"storage.googleapis.com/{name}", "--location=global",
                            f"--log-filter={flt}", "--project", project],
                           runs_as=a.copy_as,
                           after=[*temps, *([prev_copy] if prev_copy else [])], bucket=name, scope=scope)
                prev_copy = copy  # one active copy per project: each waits for the previous one
                verify = add("verify", project, f"verify gs://{name} (objects, bytes, sample hour)",
                             after=[copy], bucket=name, log_bucket=lb, filter=flt, sample_hour=sample, scope=scope)
                revokes = [add("revoke", project, f"remove temporary objectCreator for the Logging service agent on gs://{name}",
                               bind(name, agent, TEMP_ROLE, True), runs_as="person", after=[verify]),
                           caller_revoke(project, name, verify)]
                restrict(project, name, revokes)

        def export(kind, purpose, desc, **extra):
            name = bucket_name(kind, SHORT.get(project, project), hid)
            temp = caller_temp(project, name, new_bucket(project, name, purpose))
            done = add(kind, project, desc.format(name=name), after=[temp], bucket=name, **extra)
            restrict(project, name, [caller_revoke(project, name, done)])

        if a.analytics_dataset:
            days = [(start.date() + dt.timedelta(days=i)).strftime("%Y%m%d")
                    for i in range((end - dt.timedelta(microseconds=1)).date().toordinal() - start.date().toordinal() + 1)]
            export("analytics", "Analytics events",
                   f"extract {len(days)} events_* table(s) of {a.analytics_dataset} to gs://{{name}}",
                   dataset=a.analytics_dataset, days=days)
        if a.metrics:
            export("metrics", "Monitoring metrics", f"export {len(a.metrics)} metric type(s) for the window to gs://{{name}}",
                   metrics=a.metrics, start=iso(start), end=iso(end))
        if uids:
            export("auth", "Auth records", f"export {len(uids)} account(s)' Auth records (no password hashes) to gs://{{name}}",
                   uids_file=os.path.abspath(os.path.expanduser(a.uids_file)))
    return {"card": a.card, "start": iso(start), "end": iso(end), "projects": a.project,
            "log_buckets": a.log_buckets, "uid_count": len(uids), "steps": steps}


def render(p: dict) -> str:
    lines = [f"Legal hold for {p['card']}: {p['start']} -> {p['end']}, projects {', '.join(p['projects'])}, "
             f"log buckets {', '.join(p['log_buckets'])}" + (f", {p['uid_count']} account(s) in focus" if p["uid_count"] else ""),
             "Retention of the log buckets themselves is not changed. GCS retention stays UNLOCKED: locking is",
             "irreversible and is done only if counsel asks.", ""]
    for s in p["steps"]:
        who = "PERSON" if s["runs_as"] == "person" else "sa"
        dep = f" (after {', '.join(s['after'])})" if s["after"] else ""
        lines.append(f"{s['id']} [{who:6}] {s['kind']:10} {s['desc']}{dep}")
    # An incident document is labelled and fact-only.
    return writeup.render("\n".join(lines))


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", action="append", required=True)
    ap.add_argument("--log-buckets", default="_Default,_Required")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--card", required=True)
    ap.add_argument("--member", action="append", required=True,
                    help="who may read the hold (user:/group:/serviceAccount:), repeatable")
    ap.add_argument("--analytics-dataset")
    ap.add_argument("--metrics", nargs="?", const=",".join(DEFAULT_METRICS), default="")
    ap.add_argument("--uids-file")
    ap.add_argument("--project-number", action="append", default=[], help="PROJECT=NUMBER, for the Logging agent")
    ap.add_argument("--hold-project", default=HOLD_PROJECT, help="where the hold buckets are created")
    ap.add_argument("--hold-id", help="short id in every bucket name (default: --card)")
    ap.add_argument("--admin", help="who keeps storage.admin on the hold buckets (default: the first --member)")
    ap.add_argument("--sample-hour", help="YYYY-MM-DDTHH for the verify step (default: the window's last full hour)")
    # GCP_KEY has no logging.buckets.copyLogEntries since ENGBACK-2333 (2026-11-05): a person runs the copy,
    # unless the service account is granted it for this run.
    ap.add_argument("--copy-as", choices=("person", "sa"), default="person",
                    help="who runs `logging copy`: Jerome (default), or GCP_KEY when granted for this run")
    # GCP_KEY holds no bucket-create role in the hold project either (ENGBACK-2333): Jerome creates the buckets.
    ap.add_argument("--create-as", choices=("person", "sa"), default="person",
                    help="who creates and versions the hold buckets: Jerome (default), or GCP_KEY when granted")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    a.log_buckets = [b.strip() for b in a.log_buckets.split(",") if b.strip()]
    a.metrics = [m.strip() for m in a.metrics.split(",") if m.strip()]
    p = plan(a, read_uids(a.uids_file))
    print(render(p))
    if a.out:
        fd = os.open(a.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(p, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
