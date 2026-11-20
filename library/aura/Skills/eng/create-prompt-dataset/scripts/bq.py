#!/usr/bin/env python3
"""Shared BigQuery helper for the Aura cost-data warehouse (`aura-main`).

Canonical home for BigQuery access across the skill family: auth (via the
`GCP_KEY` service account), read-only `query()`, partition-existence checks,
freshness gating for rebuilt dimension tables (`assert_fresh` /
`max_build_stamp`), and the write primitives the cost-store skills need
(`load_partition` for idempotent per-day partition replace, `recreate_table` for
a schema migration). One place so BQ access isn't hand-rolled per skill.

Auth uses the same `GCP_KEY` inline-JSON service account as the GCP-logs /
LiveKit-minutes paths (`claude@firebase-auratech16`), granted
`bigquery.jobUser` + `bigquery.dataEditor` on `aura-main`. There is no BigQuery
MCP, so this is API-first by necessity — a missing key/library is a hard error
(callers that must degrade should catch `BQUnavailable`).

Requires `google-cloud-bigquery`: run under `~/.claude/python-env/bin/python3` (built by
`shared:setup-ainative`), not a bare `python3` outside a session.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sys
from typing import Iterable, Optional

PROJECT = "aura-main"
DATASET = "aurie_cost"

# A fully-qualified table name is interpolated into SQL (BigQuery identifiers
# can't be query-parameterized), so it must be a plain project.dataset.table:
# project allows letters/digits/hyphens, dataset/table letters/digits/underscores.
_TABLE_FQN_RE = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_]+\.[A-Za-z0-9_]+$")


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
    """`AuraClaude/<who>/<plugin>:<skill>@<version> (session <id>)` -- labels this skill's calls in RTDB and GCP logs.

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


class BQError(Exception):
    """A BigQuery operation failed (auth OK, but the request errored)."""


class BQUnavailable(BQError):
    """BigQuery can't be reached at all — missing GCP_KEY or google-cloud-bigquery."""


class BQStale(BQError):
    """A rebuilt dimension table is older than the caller's freshness tolerance.

    Distinct from `BQError` so a reader can offer an `--allow-stale` escape hatch
    without also swallowing genuine query failures.
    """


def _load_gcp_key() -> dict:
    raw = os.environ.get("GCP_KEY")
    if not raw:
        raise BQUnavailable("GCP_KEY not set")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        try:
            return json.load(open(os.path.expanduser(raw)))
        except Exception as e:  # noqa: BLE001
            # The GCP_KEY value must never reach an error surface. `open(raw)` embeds its
            # argument in the OSError, and when GCP_KEY holds the service-account JSON
            # that argument is the private key.
            #
            # `from None` is load-bearing, not tidiness: without it the original OSError
            # stays on __context__ and a default traceback still prints it in full under
            # "During handling of the above exception". The message would be redacted and
            # the traceback would not be.
            #
            # The exception type and the value's length keep this diagnosable — a
            # truncated paste and a shell-mangled newline look different — with nothing
            # sensitive in the string.
            raise BQUnavailable(
                "GCP_KEY is set but is neither valid JSON nor a readable file path "
                f"({type(e).__name__}; value not shown, {len(raw)} chars)") from None


_CLIENTS: dict = {}


def client(project: str = PROJECT):
    """Return a cached BigQuery client authed as the GCP_KEY service account."""
    if project in _CLIENTS:
        return _CLIENTS[project]
    try:
        from google.api_core.gapic_v1.client_info import ClientInfo
        from google.cloud import bigquery
        from google.oauth2 import service_account
    except ImportError as e:
        raise BQUnavailable(
            f"google-cloud-bigquery is not installed for {sys.executable} ({e}); "
            "run it with ~/.claude/python-env/bin/python3 (built by shared:setup-ainative; "
            "if that one lacks it: uv pip install --python ~/.claude/python-env/bin/python "
            "google-cloud-bigquery)"
        )
    creds = service_account.Credentials.from_service_account_info(_load_gcp_key())
    c = bigquery.Client(project=project, credentials=creds, client_info=ClientInfo(user_agent=user_agent()))
    _CLIENTS[project] = c
    return c


def schema_fields(schema: Iterable[tuple]):
    """Turn ``[(name, type), ...]`` into ``[SchemaField, ...]``."""
    from google.cloud import bigquery
    return [bigquery.SchemaField(name, typ) for name, typ in schema]


def query(sql: str, project: str = PROJECT, *, dry_run: bool = False,
          max_bytes: Optional[int] = None, params: Optional[dict] = None) -> list:
    """Run `sql` and return rows as a list of plain dicts.

    `dry_run=True` validates + prices the query without running it and returns a
    single-row list `[{"bytes": <estimated bytes processed>}]`. `max_bytes` caps
    `maximum_bytes_billed` so a runaway scan fails fast instead of billing the
    whole warehouse. `params` maps names to values for `@name` query parameters.
    """
    from google.cloud import bigquery
    c = client(project)
    qp = None
    if params:
        qp = [bigquery.ScalarQueryParameter(k, _param_type(v), v) for k, v in params.items()]
    job_config = bigquery.QueryJobConfig(
        dry_run=dry_run,
        use_query_cache=not dry_run,
        query_parameters=qp or [],
    )
    if max_bytes is not None:  # leaving it None sends the literal "None" and 400s
        job_config.maximum_bytes_billed = max_bytes
    try:
        job = c.query(sql, job_config=job_config)
    except Exception as e:  # noqa: BLE001
        raise BQError(str(e))
    if dry_run:
        return [{"bytes": job.total_bytes_processed}]
    return [dict(row) for row in job.result()]


def _param_type(v) -> str:
    if isinstance(v, bool):
        return "BOOL"
    if isinstance(v, int):
        return "INT64"
    if isinstance(v, float):
        return "FLOAT64"
    return "STRING"


def _check_table_fqn(table_fqn: str) -> str:
    """Reject a table name that could break out of the SQL identifier it's
    interpolated into (backtick, quote, whitespace, trailing SQL)."""
    if not _TABLE_FQN_RE.match(table_fqn or ""):
        raise BQError(f"unsafe table name {table_fqn!r} — expected project.dataset.table")
    return table_fqn


def table_exists(table_fqn: str, project: str = PROJECT) -> bool:
    from google.api_core.exceptions import NotFound
    c = client(project)  # may raise BQUnavailable — let it propagate
    try:
        c.get_table(table_fqn)
        return True
    except NotFound:
        return False
    except Exception as e:  # noqa: BLE001
        raise BQError(str(e))


def partition_exists(table_fqn: str, date: str, project: str = PROJECT) -> bool:
    """True if the `date` (YYYY-MM-DD) day-partition holds any rows."""
    _check_table_fqn(table_fqn)
    if not table_exists(table_fqn, project):
        return False
    rows = query(
        f"SELECT COUNT(*) AS n FROM `{table_fqn}` WHERE date = @d",
        project, params={"d": date},
    )
    return bool(rows and rows[0]["n"])


def max_build_stamp(table_fqn: str, project: str = PROJECT, *,
                    column: str = "fetched_at") -> Optional[str]:
    """Max value of a rebuilt table's build-stamp column, as an ISO string.

    Returns None when the table is absent or empty. `column` must be a plain
    identifier — it is interpolated into SQL (BigQuery can't parameterize those).
    """
    _check_table_fqn(table_fqn)
    if not re.match(r"^[A-Za-z0-9_]+$", column or ""):
        raise BQError(f"unsafe column name {column!r}")
    if not table_exists(table_fqn, project):
        return None
    rows = query(f"SELECT MAX(`{column}`) AS stamp FROM `{table_fqn}`", project)
    stamp = rows[0]["stamp"] if rows else None
    return None if stamp is None else str(stamp)


def assert_fresh(table_fqn: str, *, max_age_hours: float = 36.0,
                 project: str = PROJECT, column: str = "fetched_at",
                 rebuild_hint: str = "") -> str:
    """Raise `BQStale` unless `table_fqn` was rebuilt within `max_age_hours`.

    For tables rebuilt wholesale (`CREATE OR REPLACE`) rather than appended per
    day: a stale one still answers every query, just with missing recent rows, so
    a reader that doesn't check silently under-reports. `aurie_user_subscription`
    went 12 days stale in July 2026 and cost ~23% of revenue off every margin
    readout before anyone noticed — hence a hard failure rather than a warning.

    The default 36h tolerance is one daily cadence run plus slack, so a single
    missed run warns rather than a same-day re-read tripping on clock skew.

    Returns the build stamp on success. Callers wanting to proceed anyway should
    catch `BQStale` behind an explicit `--allow-stale` flag.
    """
    stamp = max_build_stamp(table_fqn, project, column=column)
    hint = f" Rebuild with: {rebuild_hint}" if rebuild_hint else ""
    if stamp is None:
        raise BQStale(f"{table_fqn} is empty or missing — nothing to read.{hint}")
    from datetime import datetime, timezone
    try:
        built = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return stamp  # unparseable stamp: don't block on a format we don't own
    if built.tzinfo is None:
        built = built.replace(tzinfo=timezone.utc)
    age_h = (datetime.now(timezone.utc) - built).total_seconds() / 3600
    if age_h > max_age_hours:
        raise BQStale(
            f"{table_fqn} was last rebuilt {age_h/24:.1f} days ago ({stamp}); "
            f"tolerance is {max_age_hours:.0f}h. Any revenue it feeds is understated."
            f"{hint}"
        )
    return stamp


def list_tables(dataset: str = DATASET, project: str = PROJECT) -> list:
    c = client(project)
    try:
        return [t.table_id for t in c.list_tables(f"{project}.{dataset}")]
    except Exception as e:  # noqa: BLE001
        raise BQError(str(e))


def describe_table(table_fqn: str, project: str = PROJECT) -> dict:
    c = client(project)
    try:
        t = c.get_table(table_fqn)
    except Exception as e:  # noqa: BLE001
        raise BQError(str(e))
    return {
        "table": table_fqn,
        "rows": t.num_rows,
        "partitioning": t.time_partitioning.field if t.time_partitioning else None,
        "clustering": t.clustering_fields or [],
        "schema": [(f.name, f.field_type, f.mode) for f in t.schema],
    }


def load_partition(table_fqn: str, csv_bytes: bytes, schema: Iterable[tuple],
                   date: str, project: str = PROJECT) -> int:
    """Replace the `date` day-partition of `table_fqn` with `csv_bytes` (headerless
    CSV in `schema` column order) via a WRITE_TRUNCATE load job. Idempotent."""
    import io
    from google.cloud import bigquery
    c = client(project)
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.CSV,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        schema=schema_fields(schema),
        skip_leading_rows=0,
    )
    target = f"{table_fqn}${date.replace('-', '')}"  # partition decorator
    try:
        job = c.load_table_from_file(io.BytesIO(csv_bytes), target, job_config=job_config)
        job.result()
    except Exception as e:  # noqa: BLE001
        raise BQError(str(e))
    return job.output_rows or 0


def recreate_table(table_fqn: str, schema: Iterable[tuple], *, partition_field: str = "date",
                   cluster: Optional[list] = None, project: str = PROJECT) -> None:
    """Drop `table_fqn` if it exists and recreate it empty with the given schema,
    day-partitioning, and clustering. For schema migrations — reload afterwards
    (the local CSV copy / bq_pull is the reload source)."""
    from google.cloud import bigquery
    c = client(project)
    try:
        c.delete_table(table_fqn, not_found_ok=True)
        table = bigquery.Table(table_fqn, schema=schema_fields(schema))
        if partition_field:
            table.time_partitioning = bigquery.TimePartitioning(
                type_=bigquery.TimePartitioningType.DAY, field=partition_field
            )
        if cluster:
            table.clustering_fields = cluster
        c.create_table(table)
    except Exception as e:  # noqa: BLE001
        raise BQError(str(e))
