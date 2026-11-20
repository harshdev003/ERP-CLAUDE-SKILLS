#!/usr/bin/env python3
"""This skill's own leg: BigQuery and Mixpanel rows for one account, straight to the hold bucket.

  export_analytics.py --dir D                 counts only: what each source holds for the account
  export_analytics.py --dir D --apply         after Jerome's go typed in this session: export, then record

Reads account.json (and email.txt) from D, written by `account.py resolve`. Matches on the uid, the
uuid, the email, the referral code and the Mixpanel device ids merged with the uid. No row is written
locally: BigQuery writes with EXPORT DATA, Mixpanel streams into the bucket. Each object goes into
D/manifest-analytics.tsv (path, bytes, crc32c, rows; a zero is a row with no path), the format
`account.py verify` reads. Prints counts only. GCP_KEY for BigQuery and GCS; MIXPANEL_SERVICE_ACCOUNT_*
and MIXPANEL_PROJECT_ID for Mixpanel.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import io
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bq  # noqa: E402

JOB_PROJECT = "aura-main"        # GCP_KEY can't create jobs in firebase-auratech16
MAX_BYTES = 3 * 1024 ** 4
CHUNK = 50                       # Mixpanel tables per query; ~400 fails with "metadata too large"
# Mixpanel's BigQuery copy, used for the device ids merged with the uid. It runs a few days behind;
# the Export API below covers the rest. The older aura-main `mixpanel` dataset stopped in 2022.
MP_BQ = "mixpanel-prod-1.mixpanel_nessie_day_partitioned_963821"
DATASETS = ("aura-main.firebase", "aura-main.llm", "aura-main.aurie_cost", "aura-main.activation")
USER_COL = re.compile(r"(?i)(^|_)(uid|user_?id)$|[a-z]userid$")
RAW_FIELDS = ["userId", "coachId", "postUserId", "commentUserId", "parentPostUserId"]
RAW_ARRAYS = ["engagedUserIds", "parentPostEngagedUserIds"]
MP_UID_COLS = ["distinct_id", "mp_user_id", "userid", "user_id"]
MP_DEVICE_COLS = ["distinct_id", "mp_device_id", "mp_distinct_id_before_identity"]
MP_EMAIL_COLS = ["email", "mp_email", "work_email", "user_email", "useremail"]
PROJECT_TZ = "America/Los_Angeles"   # the Mixpanel project's timezone; MIXPANEL_PROJECT_TZ overrides


def project_date(iso_utc: str) -> dt.date:
    """A UTC cutoff as a date in the Mixpanel project's timezone."""
    t = dt.datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
    return t.astimezone(ZoneInfo(os.environ.get("MIXPANEL_PROJECT_TZ", PROJECT_TZ))).date()


class Ctx:
    def __init__(self, d, acct, email, until, apply):
        self.d, self.acct, self.apply = d, acct, apply
        self.run = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.tag = f"{acct['uidPrefix']}-{self.run}"
        self.until = until
        self.params = {"uid": acct["uid"], "uuid": (acct.get("uuid") or "__none__").lower(),
                       "email": (email or "__none__").lower(), "ref": acct.get("referralCode") or "__none__"}
        self.has = {"email": bool(email), "uuid": bool(acct.get("uuid")), "ref": bool(acct.get("referralCode"))}
        self.counts = {}


def query(ctx, sql, params=None, extra=None):
    from google.cloud import bigquery
    p = dict(ctx.params, **(params or {}))
    qp = [bigquery.ArrayQueryParameter(k, "STRING", v) if isinstance(v, list)
          else bigquery.ScalarQueryParameter(k, "STRING", v) for k, v in p.items()]
    cfg = bigquery.QueryJobConfig(query_parameters=qp, maximum_bytes_billed=MAX_BYTES, **(extra or {}))
    job = bq.client(JOB_PROJECT).query(sql, job_config=cfg)
    return list(job.result()), job


def until_sql(ctx, col, typ="TIMESTAMP"):
    if not ctx.until:
        return ""
    return f" AND {col} <= {typ}('{ctx.until[:10] if typ == 'DATE' else ctx.until}')"


def bucket(ctx):
    from google.api_core.client_info import ClientInfo
    from google.cloud import storage
    from google.oauth2 import service_account
    creds = service_account.Credentials.from_service_account_info(bq._load_gcp_key())
    user_agent = bq.user_agent
    client = storage.Client(project=JOB_PROJECT, credentials=creds, client_info=ClientInfo(user_agent=user_agent()))
    return client.bucket(ctx.acct["bucket"])


def record(ctx, path, size, crc, rows, source):
    line = "\t".join([path, str(size), crc or "", str(rows), source]) + "\n"
    p = os.path.join(ctx.d, "manifest-analytics.tsv")
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(line)


def export(ctx, name, select_sql, params=None):
    """Count first. A zero writes no object (BigQuery would write one empty file per worker) and is
    recorded as a zero. ORDER BY + LIMIT makes BigQuery write a single file, so the job never needs
    storage.objects.delete for temp-file cleanup: the hold bucket grants create only."""
    n = query(ctx, f"SELECT COUNT(*) n FROM ({select_sql})", params)[0][0]["n"]
    ctx.counts[name] = n
    if not ctx.apply or not n:
        if ctx.apply:
            record(ctx, "", 0, "", 0, name)
        print(json.dumps({name: n}))
        return n
    prefix = f"bigquery/{name}/{ctx.tag}-"
    query(ctx, f"EXPORT DATA OPTIONS(uri='gs://{ctx.acct['bucket']}/{prefix}*.json', format='JSON', "
               f"overwrite=false) AS SELECT * FROM ({select_sql}) ORDER BY 1 LIMIT {n}", params)
    # The writer here is BigQuery, so the checksum comes from the listing (list is granted, read isn't).
    for b in bucket(ctx).client.list_blobs(ctx.acct["bucket"], prefix=prefix):
        record(ctx, f"gs://{ctx.acct['bucket']}/{b.name}", b.size, b.crc32c, n, name)
    print(json.dumps({name: n, "exported": True}))
    return n


def raw_events_sql(ctx):
    m = [f"IF(JSON_VALUE(data, '$.{f}') = @uid, '{f}', NULL)" for f in RAW_FIELDS]
    m += [f"IF(@uid IN UNNEST(IFNULL(JSON_VALUE_ARRAY(data, '$.{a}'), [])), '{a}', NULL)" for a in RAW_ARRAYS]
    # Only the ids that were resolved: an unresolved one is a sentinel, and a substring match on a
    # sentinel would export strangers' events.
    if ctx.has["email"]:
        m += ["IF(LOWER(JSON_VALUE(data, '$.email')) = @email, 'email', NULL)",
              "IF(LOWER(JSON_VALUE(data, '$.paypalEmail')) = @email, 'paypalEmail', NULL)"]
    if ctx.has["uuid"]:
        m += ["IF(LOWER(JSON_VALUE(data, '$.uuid')) = @uuid, 'uuid', NULL)"]
    if ctx.has["ref"]:
        m += ["IF(STRPOS(data, @ref) > 0, 'referral', NULL)"]
    matched = "ARRAY_TO_STRING([" + ", ".join(f"IFNULL({x}, '')" for x in m) + "], ' ')"
    return (f"SELECT createdAt, name, data, TRIM(REGEXP_REPLACE({matched}, r'\\s+', ' ')) AS matched_fields "
            f"FROM `aura-main.firebase.raw_events` WHERE TRIM({matched}) != ''{until_sql(ctx, 'createdAt')}")


def user_tables(ctx):
    """Every table in DATASETS with a STRING user-id column (userId, user_id, uid, followingUserId…).
    A partitioned table gets a filter on its partition column, or llm.llm_call refuses the query."""
    tables = {}
    for ds in DATASETS:
        rows, _ = query(ctx, f"SELECT c.table_name, c.column_name, c.data_type, c.is_partitioning_column "
                             f"FROM `{ds}.INFORMATION_SCHEMA.COLUMNS` c JOIN `{ds}.INFORMATION_SCHEMA.TABLES` t "
                             f"USING (table_name) WHERE t.table_type = 'BASE TABLE'")
        for r in rows:
            t = tables.setdefault(f"{ds}.{r['table_name']}", {"cols": [], "part": None})
            if r["is_partitioning_column"] == "YES":
                t["part"] = (r["column_name"], r["data_type"])
            if r["data_type"] == "STRING" and USER_COL.search(r["column_name"]):
                t["cols"].append(r["column_name"])
    return {k: v for k, v in tables.items() if v["cols"] and not k.endswith("firebase.raw_events")}


def table_sql(ctx, fqn, t):
    where = " OR ".join(f"`{c}` = @uid" for c in t["cols"])
    part = ""
    if t["part"]:
        col, typ = t["part"]
        typ = "DATE" if typ == "DATE" else "TIMESTAMP"
        part = f" AND `{col}` >= {typ}('1970-01-01'){until_sql(ctx, col, typ)}"
    return f"SELECT * FROM `{fqn}` WHERE ({where}){part}"


def mp_columns(ctx):
    rows, _ = query(ctx, f"SELECT c.table_name, c.column_name FROM `{MP_BQ}.INFORMATION_SCHEMA.COLUMNS` c "
                         f"JOIN `{MP_BQ}.INFORMATION_SCHEMA.TABLES` t USING (table_name) "
                         f"WHERE t.table_type = 'BASE TABLE' AND c.column_name IN UNNEST(@cols)",
                    {"cols": sorted(set(MP_UID_COLS + MP_DEVICE_COLS + MP_EMAIL_COLS + ["uuid", "time"]))})
    cols = {}
    for r in rows:
        cols.setdefault(r["table_name"], set()).add(r["column_name"])
    return cols


def mp_cond(ctx, cols):
    c = [f"{x} = @uid" for x in MP_UID_COLS if x in cols]
    c += [f"{x} IN UNNEST(@devices)" for x in MP_DEVICE_COLS if x in cols]
    c += [f"LOWER({x}) = @email" for x in MP_EMAIL_COLS if x in cols] if ctx.has["email"] else []
    c += ["LOWER(uuid) = @uuid"] if "uuid" in cols and ctx.has["uuid"] else []
    w = " OR ".join(c) or "FALSE"
    return f"({w}){until_sql(ctx, 'time')}" if "time" in cols else w


def chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def mixpanel_bq(ctx):
    cols = mp_columns(ctx)
    devices = set()
    for part in chunks(sorted(t for t in cols if "mp_device_id" in cols[t]), CHUNK):
        q = " UNION ALL ".join(f"SELECT mp_device_id d FROM `{MP_BQ}.{t}` WHERE mp_device_id IS NOT NULL "
                               f"AND ({' OR '.join(f'{x} = @uid' for x in MP_UID_COLS if x in cols[t]) or 'FALSE'})"
                               for t in part)
        devices |= {r["d"] for r in query(ctx, f"SELECT DISTINCT d FROM ({q})")[0] if r["d"]}
    p = {"devices": sorted(devices) or ["__none__"]}
    ctx.counts["mixpanel:mergedDeviceIds"] = len(devices)
    hits = {}
    for part in chunks(sorted(cols), CHUNK):
        q = " UNION ALL ".join(f"SELECT '{t}' t, COUNT(*) n FROM `{MP_BQ}.{t}` WHERE {mp_cond(ctx, cols[t])}"
                               for t in part)
        hits.update({r["t"]: r["n"] for r in query(ctx, q, p)[0] if r["n"]})
    ctx.counts["mixpanel_events:tables"] = len(hits)
    for i, part in enumerate(chunks(sorted(hits), CHUNK)):
        q = " UNION ALL ".join(f"SELECT '{t}' AS mp_table, TO_JSON_STRING(x) AS row FROM `{MP_BQ}.{t}` x "
                               f"WHERE {mp_cond(ctx, cols[t])}" for t in part)
        export(ctx, f"mixpanel_events/chunk{i:02d}", q, p)
    export(ctx, "mixpanel_people", f"SELECT * FROM `{MP_BQ}.mp_people_data_view` WHERE userid = @uid "
                                   f"OR user_id = @uid OR mp_distinct_id = @uid"
                                   + (" OR LOWER(email) = @email" if ctx.has["email"] else ""), p)
    return sorted(devices)


class Counting:
    """File-like over an HTTP response: counts lines and bytes as it streams to GCS. Nothing on disk."""

    def __init__(self, resp):
        self.resp, self.lines, self.bytes = resp, 0, 0

    def tell(self):
        return self.bytes

    def read(self, n=-1):
        b = self.resp.read(n)
        self.lines += b.count(b"\n")
        self.bytes += len(b)
        return b


def upload(ctx, path, src, source, rows=None):
    """create-only (if_generation_match=0); this side is the uploader, so the crc32c comes from it.
    rows: the record count when it isn't one per line (an Engage reply is one JSON object)."""
    blob = bucket(ctx).blob(path)
    blob.chunk_size = 8 * 1024 * 1024
    c = Counting(src)
    blob.upload_from_file(c, content_type="application/x-ndjson", if_generation_match=0)
    record(ctx, f"gs://{ctx.acct['bucket']}/{path}", c.bytes, blob.crc32c, c.lines if rows is None else rows, source)
    return c


def mixpanel_api(ctx, devices, email):
    project = os.environ.get("MIXPANEL_PROJECT_ID")
    user, secret = os.environ.get("MIXPANEL_SERVICE_ACCOUNT_USERNAME"), os.environ.get("MIXPANEL_SERVICE_ACCOUNT_SECRET")
    if not (project and user and secret):
        ctx.counts["mixpanel_api"] = "not run: MIXPANEL_PROJECT_ID / MIXPANEL_SERVICE_ACCOUNT_* unset"
        print("Mixpanel API: credentials unset, NOT RUN (recorded; not a zero)")
        return
    h = {"Authorization": "Basic " + base64.b64encode(f"{user}:{secret}".encode()).decode(),
         "User-Agent": bq.user_agent()}
    uid = ctx.acct["uid"]
    ids = [uid] + devices + [f"$device:{d}" for d in devices if not d.startswith("$device:")]
    for kind, form in (("ids", {"distinct_ids": json.dumps(ids)}),
                       ("email", {"where": f'properties["$email"] == "{email}" or properties["email"] == "{email}"'})):
        if kind == "email" and not email:
            continue
        req = urllib.request.Request(f"https://mixpanel.com/api/2.0/engage?project_id={project}",
                                     data=urllib.parse.urlencode(form).encode(), headers=h)
        with urllib.request.urlopen(req, timeout=300) as resp:
            body = resp.read()
        total = len(json.loads(body).get("results") or [])
        ctx.counts[f"mixpanel_people_api:{kind}"] = total
        upload(ctx, f"mixpanel/people/{ctx.tag}-engage-{kind}.json", io.BytesIO(body), f"mixpanel_people_api:{kind}",
               rows=total)
    ors = [f'properties["{k}"] == "{uid}"' for k in ("distinct_id", "$user_id")]
    ors += [f'properties["{k}"] == "{d}"' for d in devices for k in ("distinct_id", "$device_id")]
    ors += [f'properties["{k}"] == "{email}"' for k in ("$email", "email")] if email else []
    ors += [f'properties["uuid"] == "{ctx.params["uuid"]}"'] if ctx.acct.get("uuid") else []
    first = int(((ctx.acct.get("auth") or {}).get("createdAt") or "2016")[:4])
    # Export dates are in the project's timezone, and a to_date after its today is a 400.
    today = dt.datetime.now(ZoneInfo(os.environ.get("MIXPANEL_PROJECT_TZ", PROJECT_TZ))).date()
    end = min(project_date(ctx.until), today) if ctx.until else today
    for y in range(first, end.year + 1):
        q = urllib.parse.urlencode({"project_id": project, "from_date": f"{y}-01-01",
                                    "to_date": min(dt.date(y, 12, 31), end).isoformat(), "where": " or ".join(ors)})
        req = urllib.request.Request(f"https://data.mixpanel.com/api/2.0/export?{q}", headers=h)
        with urllib.request.urlopen(req, timeout=3600) as resp:
            c = upload(ctx, f"mixpanel/events/{ctx.tag}-{y}.jsonl", resp, f"mixpanel_events_api:{y}")
        ctx.counts[f"mixpanel_events_api:{y}"] = c.lines
        print(json.dumps({f"mixpanel_events_api:{y}": c.lines}))


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--bucket", default="aura-legal-hold-prod6926")
    ap.add_argument("--until", help="ISO UTC cutoff, e.g. 2026-11-05T12:00:00Z (default: now)")
    ap.add_argument("--apply", action="store_true", help="export; only after Jerome's go typed in this session")
    a = ap.parse_args(argv)
    if a.until and not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", a.until):
        print("--until must look like 2026-11-05T12:00:00Z", file=sys.stderr)
        return 2
    d = os.path.expanduser(a.dir)
    with open(os.path.join(d, "account.json"), encoding="utf-8") as f:
        acct = dict(json.load(f), bucket=a.bucket)
    email = ""
    if acct.get("emailFile") and os.path.exists(acct["emailFile"]):
        with open(acct["emailFile"], encoding="utf-8") as f:
            email = f.read().strip().lower()
    ctx = Ctx(d, acct, email, a.until, a.apply)
    print(f"{'EXPORT' if a.apply else 'COUNTS ONLY (no --apply)'} for {acct['uidPrefix']}…")
    export(ctx, "firebase_raw_events", raw_events_sql(ctx))
    for fqn, t in sorted(user_tables(ctx).items()):
        export(ctx, fqn.split(".", 1)[1].replace(".", "/"), table_sql(ctx, fqn, t))
    devices = mixpanel_bq(ctx)
    if a.apply:
        mixpanel_api(ctx, devices, email)
        body = json.dumps({"account": acct["uidPrefix"], "run": ctx.run, "until": a.until, "counts": ctx.counts},
                          indent=1).encode()
        upload(ctx, f"bigquery/{ctx.tag}-counts.json", io.BytesIO(body), "counts")
    else:
        print("Mixpanel API (People, Export) runs with --apply only.")
    print(json.dumps({"sources": len(ctx.counts),
                      "rows": sum(v for v in ctx.counts.values() if isinstance(v, int))}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
