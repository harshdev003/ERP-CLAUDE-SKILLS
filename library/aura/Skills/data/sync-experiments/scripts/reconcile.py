#!/usr/bin/env python3
"""Reconcile `Learnings/Experiments` against the live `GET /experiments` catalogue.

An experiment that stops splitting traffic never gets an `endedAt` — the server
collapses `active` to a single variant instead. `sync-experiments` keys on
`endedAt`, so those rows sat at `Running` forever: 133 of them on 2026-09-17,
invisible to every consumer that asks Notion what is running.

Two things follow, and the second is the reason this is a script rather than a
paragraph in the SKILL.md:

  * A stopped experiment gets `Status: Stopped splitting` — a state, never a
    verdict. `active` alone cannot tell a rollout from a pre-release force, a
    kill by unreachable `minVersion`, or a permanent feature flag, so nothing
    here infers which. See `stopped_splitting` for the evidence.
  * A run that reconciles nothing must not look like a run with nothing to
    reconcile. `report` prints the disagreement count whether or not it is zero.

Usage:
    python3 reconcile.py report            # counts + the unresolved list
    python3 reconcile.py apply             # write `Stopped splitting`
    python3 reconcile.py apply --dry-run   # the plan, no writes
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import pathlib
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Iterable, Optional


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


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

EXPERIMENTS_URL = "https://services.aurahealth.io/experiments?limit=10000"
EXPERIMENTS_DS = "c0209685-17ac-49e2-849a-85fa8178decd"

# Written by this script. Says what is true — the experiment is not splitting
# traffic — and stops there.
STOPPED = "Stopped splitting"

# Set by a HUMAN, never here. A kill switch kept live on purpose is not an
# experiment mid-flight and will never produce a verdict, so once someone says
# so the row must never be offered for resolution again.
FEATURE_FLAG = "Feature flag"

# The rows this script may write to. Anything else is a human's answer or
# `analyze-experiment`'s verdict, and overwriting one would replace a fact with
# a restatement of the catalogue.
OVERWRITABLE = {"Running", "In flight", None, ""}

# Terminal for the purposes of "still needs a human". `Stopped splitting` is
# deliberately absent — that is the unresolved state itself.
RESOLVED = {"Won", "Lost", "Inconclusive", "Turned off", FEATURE_FLAG}


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


def variant_arms(active: Any) -> int:
    """How many arms an experiment is splitting across — DISTINCT letters."""
    return len(set(str(active or "").strip()))


# Variant letters run from 'a'. Eight is already generous — the widest arm count
# in the catalogue is six (`abcdef`) — and the bound is what separates a variant
# string from a word: `none` collapses to {e,n,o}, which is not an arm set.
VARIANT_LETTERS = set("abcdefgh")


def malformed_active(row: dict) -> Optional[str]:
    """Why this `active` cannot be read as an arm set, or None if it can.

    `active` is a weighted arm list: letters from 'a' upward, repeats allowed as
    traffic weight (`aab` is a at 2:1 over b), gaps allowed (`abd`). So a repeat
    on its own is fine — `aabcde` is five arms, `aabd` is three.

    Two shapes are refused.

    **A repeat that collapses to one arm** — `bb`, `aa`. This is not a typo, it
    is a workaround, and the service explains it: `assign()` decides an
    experiment has ended with `active.length === 1` — the STRING length, not the
    distinct-letter count. So `bb` forces every user to `b` (a pick from "bb" is
    always b) while the length-2 string dodges the ended check. Somebody wanted
    one arm without ending the experiment. It works, and it makes every consumer
    that counts arms wrong: `newPricingPage` and `placeholder` both carry it.

    **A non-variant character** — `none`. Written by the end command when no
    `and winner <x>` clause is supplied. One row in 3,007 carried it
    (`targetAppTest9`, corrected to `b` on 2026-09-18); every other ended row
    holds a single letter.

    Both are reported, never resolved. The catalogue does not say what was meant,
    and a writer that guesses puts a durable status on a guess.
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


def stopped_splitting(row: dict) -> bool:
    """The experiment has stopped splitting traffic. It does NOT say why.

    `endedAt` unset and one arm. Checked against the live catalogue on
    2026-09-16, that one signal covers at least five situations:

      * forced to one arm before release — `chatImageUpload`, created the day
        before, `minVersion` later than any shipped tag
      * pending release though already past its minVersion — `aurieWebViewPaywall3`
      * disabled by an unreachable minimum — `noFeed` at `minVersion 20.0.0`
      * a permanent feature flag kept as a kill switch — `usageLimitsOldUsers`,
        which will never end
      * a genuine rollout — no confirmed example found

    `result` is empty in every one of them and `notes` records intent rather
    than state, which is what made `aurieWebViewPaywall3` read as a confirmed
    rollout when it is not. `minVersion` does not discriminate either: 5 of 15
    Aurie rows have none, three carry impossible values, and the version schemes
    are mixed, so a numeric comparison is not safe.

    So this returns a state, and a verdict is never derived from it. An early
    draft of PROD-6116 proposed inferring `Won` from exactly this signal, which
    would have marked `chatImageUpload` as a win the day after it was created.

    Ambiguous rows are excluded — see `ambiguous_active`.
    """
    return (not row.get("endedAt") and not ambiguous_active(row)
            and variant_arms(row.get("active")) <= 1)


def splitting(row: dict) -> bool:
    """Genuinely live: no `endedAt`, unambiguous, and more than one arm."""
    return (not row.get("endedAt") and not ambiguous_active(row)
            and variant_arms(row.get("active")) > 1)


# ---------- provenance ------------------------------------------------------

# `GET /experiments/<id>`. The LIST endpoint filters on `deletedAt IS NULL`, so
# an archived row vanishes from it; the per-id endpoint still answers. That gap
# is the only catalogue-independent provenance test available, and it is exact:
# 200 means the experiment server sourced this id, 404 means it never held it.
EXPERIMENT_URL = "https://services.aurahealth.io/experiments/{}"


class Prober:
    """Did the experiment server ever source this id? Tri-state, and the third
    state is the load-bearing one.

    `True` sourced, `False` never sourced, **`None` not tested**. Absence from
    the catalogue is not evidence of either: on 2026-09-21 a tranche moved ended
    experiments out of the Firebase `/experiments` node (every Aura client reads
    that node in full on the paywall path, so its size is latency and cost), and
    the catalogue fell from ~3,000 rows to ~660. 2,347 of the 2,425 rows that
    left WERE sourced — so a classifier that reads absence as "never sourced"
    gets it wrong 97% of the time, which is exactly what this one used to do.

    A transport failure returns `None`, never `False`. An unreachable server
    must not be able to state that the server never held something.
    """

    def __init__(self, token: Optional[str], scope: Optional[set] = None):
        self.token = token
        self.scope = scope          # None = probe anything asked for
        self._cache: dict[str, Optional[bool]] = {}
        self.failures: list[str] = []

    def __call__(self, name: str) -> Optional[bool]:
        if not self.token or (self.scope is not None and name not in self.scope):
            return None
        if name in self._cache:
            return self._cache[name]
        url = EXPERIMENT_URL.format(urllib.parse.quote(name, safe=""))
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {self.token}", "User-Agent": user_agent(), "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60):
                out: Optional[bool] = True
        except urllib.error.HTTPError as e:
            if e.code == 404:
                out = False
            else:
                self.failures.append(f"{name}: HTTP {e.code}")
                out = None
        except Exception as e:  # noqa: BLE001 — a probe that cannot run says so
            self.failures.append(f"{name}: {type(e).__name__}: {e}")
            out = None
        self._cache[name] = out
        return out

    def warm(self, names: Iterable[str]) -> None:
        """Fill the cache concurrently, so `classify`'s serial calls are free.

        `--probe-orphans` is 2,425 calls on 2026-09-22; serially that is minutes,
        which is how an opt-in check becomes one nobody opts into. Eight workers
        is deliberately modest — this is a read against a production service, and
        the point is to make the flag usable, not to saturate anything.
        """
        todo = [n for n in names if n not in self._cache]
        if not self.token or len(todo) < 2:
            return
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(self, todo))


# ---------- IO --------------------------------------------------------------

def aura_token() -> Optional[str]:
    return os.environ.get("AURA_API_TOKEN") or os.environ.get("AURA_ADMIN_TOKEN")


def fetch_catalogue(path: Optional[str] = None) -> list[dict]:
    if path:
        return json.load(open(path, encoding="utf-8"))
    token = aura_token()
    if not token:
        sys.exit("AURA_API_TOKEN is not set — cannot read the live catalogue.")
    req = urllib.request.Request(
        EXPERIMENTS_URL,
        headers={"Authorization": f"Bearer {token}", "User-Agent": user_agent(), "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            rows = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code} from the experiment server — {e.reason}")
    except Exception as e:  # noqa: BLE001 — any transport failure is fatal here
        sys.exit(f"{type(e).__name__} reading the experiment server: {e}")
    if not isinstance(rows, list):
        sys.exit(f"unexpected response shape: {type(rows).__name__}")
    return rows


def fetch_notion() -> dict[str, dict]:
    from notion_rest import notion, title_text, date_start
    if not notion.available:
        sys.exit("NOTION_TOKEN is not set — cannot read Learnings/Experiments.")
    out: dict[str, dict] = {}
    for row in notion.query_data_source(EXPERIMENTS_DS, page_size=100):
        title = title_text(row, "Title").strip()
        if not title:
            continue
        out[title] = {
            "page_id": row["id"],
            "status": (row["properties"].get("Status", {}).get("select") or {}).get("name"),
            "end_date": date_start(row, "End Date"),
        }
    return out


# ---------- classification --------------------------------------------------

def classify(catalogue: Iterable[dict], notion_rows: dict[str, dict],
             sourced=None) -> dict[str, list]:
    """Every way the two sides can differ, each as its own bucket.

    Buckets are disjoint over the catalogue, plus six over the Notion-only rows,
    so nothing can fall between them. Differing is not the same as disagreeing —
    `disagreement_count` decides which buckets are a contradiction.

    `sourced(name)` is the provenance oracle (see `Prober`): `True` sourced,
    `False` never sourced, `None` not tested. Default `None` for every row —
    which yields the two `*_untested` buckets, whose labels claim nothing about
    origin. Nothing here ever infers provenance from absence.
    """
    if sourced is None:
        def sourced(_name):  # noqa: E306 — no probe supplied: nothing is tested
            return None
    api = {r["id"]: r for r in catalogue if r.get("id")}
    b: dict[str, list] = {
        "to_mark": [],           # stopped splitting, Notion still says Running
        "already_marked": [],    # stopped splitting, already `Stopped splitting`
        "feature_flags": [],     # stopped splitting, a human called it a flag
        "resolved_stopped": [],  # stopped splitting, a human or analysis answered it
        "ended_but_running": [], # `endedAt` set, Notion still says Running
        "ended_unanalysed": [],  # `endedAt` set, Notion Status blank — awaiting analysis
        "ended_resolved": [],    # `endedAt` set, verdict written
        "missing_end_date": [],  # `endedAt` set, Notion `End Date` blank
        "live": [],              # genuinely splitting, Notion agrees
        "live_disagree": [],     # genuinely splitting, Notion says it ended
        # Absent from the LIVE catalogue, split by what the provenance probe said.
        # Absence alone says nothing about origin — see `Prober`.
        "deleted_but_running": [],  # server sourced it, since deleted; Notion says it runs
        "unsourced_running": [],    # server never held it; Notion says it runs — a manual row
        "untested_running": [],     # Notion says it runs, provenance not probed
        "absent_archived": [],      # server sourced it, since deleted; Notion agrees it is over
        "absent_unsourced": [],     # server never held it — sheet import, web tests, creative
        "absent_untested": [],      # absent, provenance not probed
        "missing_row": [],       # catalogue row with no Notion row at all
        "to_unmark": [],         # splitting again, still carries this script's own marker
        "ambiguous": [],         # `active` repeats a letter — held for a human
        "skipped_feature_flag": [],  # the catalogue marks it a feature flag: never synced or end-dated
    }
    for name, row in api.items():
        if is_feature_flag(row):
            b["skipped_feature_flag"].append(name)
            continue
        n = notion_rows.get(name)
        if n is None:
            b["missing_row"].append(name)
            continue
        status = n["status"]
        if ambiguous_active(row) and not row.get("endedAt"):
            b["ambiguous"].append(name)
        elif stopped_splitting(row):
            if status == FEATURE_FLAG:
                b["feature_flags"].append(name)
            elif status == STOPPED:
                b["already_marked"].append(name)
            elif status in OVERWRITABLE:
                b["to_mark"].append(name)
            else:
                b["resolved_stopped"].append(name)
        elif splitting(row):
            if status == STOPPED:
                # It resumed. Withdrawing this script's OWN marker is the one
                # write back toward `Running` it may make — nobody's judgement is
                # being overwritten, only a stale reading of the catalogue.
                b["to_unmark"].append(name)
            elif status in RESOLVED or n["end_date"]:
                b["live_disagree"].append(name)
            else:
                b["live"].append(name)
        else:  # `endedAt` is set
            if not n["end_date"]:
                b["missing_end_date"].append(name)
            if status in RESOLVED:
                b["ended_resolved"].append(name)
            elif status in ("Running", "In flight"):
                b["ended_but_running"].append(name)
            else:
                b["ended_unanalysed"].append(name)
    for name in sorted(set(notion_rows) - set(api)):
        claims_to_run = notion_rows[name]["status"] in ("Running", "In flight")
        was_sourced = sourced(name)
        if claims_to_run:
            b[{True: "deleted_but_running", False: "unsourced_running",
               None: "untested_running"}[was_sourced]].append(name)
        else:
            b[{True: "absent_archived", False: "absent_unsourced",
               None: "absent_untested"}[was_sourced]].append(name)
    return b


# The buckets where Notion and the catalogue contradict each other about what is
# running. Deliberately narrower than "the buckets that are not empty":
#
#   * `ended_unanalysed` is a row the sync end-dated correctly and analysis has
#     not reached yet. A missing verdict is a backlog, not a contradiction.
# `to_unmark` is counted: a row that resumed splitting while still reading
# `Stopped splitting` is the same failure as the original one, pointed the other
# way. Nothing sets `End Date` on these rows and `Stopped splitting` is not a
# verdict, so without an explicit bucket it would classify as agreement and the
# stale status would never be reported or corrected.
#
#   * `ambiguous` is held on purpose (see `ambiguous_active`). Two rows on
#     2026-09-17. It is listed in full every run so it stays in front of
#     someone, but it is a question rather than a contradiction.
#   * The three `absent_*` buckets are rows that are not in the live catalogue
#     and do not claim to be running. Archiving a tranche moves thousands of
#     rows here at once (2,425 on 2026-09-22) and nothing is wrong with any of
#     them: sync only ever creates and updates Notion, never deletes, so the
#     Notion record is intact and the API still answers for each one by id.
#
# Counting either would put a permanently non-zero floor under the headline,
# which is how a number nobody can drive to zero stops being read at all.
#
# `unsourced_running` is the same argument pointed the other way, and it is why
# the probe is worth its HTTP calls. A Notion row the experiment server never
# held — `AurieInvitePushCopy`, `App Store Screenshots (Round 3)` — is a human's
# record of something run outside the server, and its `Running` is that human's
# business, not a contradiction with a catalogue it was never in. Three of the
# four rows in the old status-only orphan bucket were these, so the headline
# carried a floor of three permanent false disagreements.
#
# `untested_running` IS counted. When the probe could not run, the row is
# exactly as suspicious as it was before the probe existed, and a check that
# failed to run must never read as a check that passed.
DISAGREEING = ("to_mark", "to_unmark", "ended_but_running", "live_disagree",
               "deleted_but_running", "untested_running", "missing_row",
               "missing_end_date")


def disagreement_count(b: dict[str, list]) -> int:
    return sum(len(b[k]) for k in DISAGREEING)


# ---------- commands --------------------------------------------------------

def _absent_line(b: dict[str, list]) -> str:
    """The rows that are not in the live catalogue and do not claim to run.

    Every word here is load-bearing. The line this replaced called all of them
    "row(s) the experiment server never sourced" — a claim about ORIGIN, from a
    test that only ever looked at the live catalogue and a Notion status. After
    the 2026-09-21 archive tranche that claim was false for 2,347 of the 2,421
    rows it was made about, and it was filed under "not a disagreement", so the
    report asserted a provenance it had not tested and then waved it through.

    So: say only what was tested. Untested rows are named as untested.
    """
    archived, unsourced, untested = (len(b["absent_archived"]), len(b["absent_unsourced"]),
                                     len(b["absent_untested"]))
    total = archived + unsourced + untested
    if not total:
        return "0 absent from the live catalogue"
    parts = []
    if archived:
        parts.append(f"{archived} archived (server-sourced, since deleted)")
    if unsourced:
        parts.append(f"{unsourced} the experiment server never held")
    if untested:
        parts.append(f"{untested} not probed, so archived or never held — untested")
    return f"{total} absent from the live catalogue: " + ", ".join(parts)


def render(b: dict[str, list], limit: int = 15) -> str:
    n = disagreement_count(b)
    lines = [
        f"Catalogue vs Learnings/Experiments — {n} row(s) disagree.",
        "",
        f"  stopped splitting, still reads Running   {len(b['to_mark']):>5}   <- `apply` writes these",
        f"  ended, Notion still says Running         {len(b['ended_but_running']):>5}   "
        f"<- needs `analyze-experiment`",
        f"  ended, End Date missing in Notion        {len(b['missing_end_date']):>5}",
        f"  splitting again, still marked stopped    {len(b['to_unmark']):>5}   "
        f"<- `apply` withdraws the marker",
        f"  splitting, but Notion says it ended      {len(b['live_disagree']):>5}",
        f"  deleted from the catalogue, reads Running {len(b['deleted_but_running']):>4}",
        f"  absent, reads Running, not probed        {len(b['untested_running']):>5}",
        f"  in the catalogue, no Notion row          {len(b['missing_row']):>5}",
        "",
        f"  agreed: {len(b['live'])} splitting, {len(b['already_marked'])} stopped, "
        f"{len(b['feature_flags'])} feature flag(s), "
        f"{len(b['resolved_stopped']) + len(b['ended_resolved'])} resolved",
        f"  not a disagreement: {len(b['ended_unanalysed'])} ended awaiting analysis, "
        + _absent_line(b),
    ]
    if b["ambiguous"]:
        lines += ["",
                  f"  {len(b['ambiguous'])} row(s) carry a repeated-letter `active` — held, "
                  f"left as-is, needs a human: " + ", ".join(sorted(b["ambiguous"]))]
    if b["skipped_feature_flag"]:
        lines += ["",
                  f"  skipped.featureFlag {len(b['skipped_feature_flag'])}: marked a feature flag in the "
                  f"catalogue, never synced or end-dated: " + ", ".join(sorted(b["skipped_feature_flag"]))]
    if b["unsourced_running"]:
        lines += ["",
                  f"  {len(b['unsourced_running'])} row(s) read Running but the experiment "
                  f"server never held them — rows for something run outside it, not a "
                  f"catalogue disagreement: " + ", ".join(sorted(b["unsourced_running"]))]
    for key, label in (("to_mark", "To mark `Stopped splitting`"),
                       ("to_unmark", "Splitting again — marker to withdraw"),
                       ("ended_but_running", "Ended but still Running"),
                       ("live_disagree", "Splitting but marked ended"),
                       ("deleted_but_running", "Deleted from the catalogue but reads Running"),
                       ("untested_running", "Absent and reads Running — provenance not probed"),
                       ("missing_row", "In the catalogue, no Notion row")):
        if b[key]:
            shown = sorted(b[key])[:limit]
            more = len(b[key]) - len(shown)
            lines += ["", f"{label}: " + ", ".join(shown) + (f" … +{more} more" if more > 0 else "")]
    if n == 0:
        lines += ["", "Nothing to reconcile."]
    return "\n".join(lines)


def _reconcile(args: argparse.Namespace) -> tuple[dict, dict[str, list], Prober]:
    """Fetch both sides and classify, with provenance probed where it is worth it.

    **Scope.** By default only the absent rows that claim to be RUNNING are
    probed — four calls on 2026-09-22, against 2,425 if every absent row were.
    That is where the question actually matters: a row Notion calls live and the
    catalogue has deleted is the one thing here that could be a live experiment
    silently serving its control arm. `--probe-orphans` widens it to every
    absent row, which resolves the `absent_untested` lump at the cost of one
    call per row.
    """
    catalogue = fetch_catalogue(args.catalogue)
    notion_rows = fetch_notion()
    absent = set(notion_rows) - {r["id"] for r in catalogue if r.get("id")}
    scope = absent if getattr(args, "probe_orphans", False) else {
        n for n in absent if notion_rows[n]["status"] in ("Running", "In flight")}
    prober = Prober(aura_token(), scope)
    prober.warm(scope)
    if scope and not prober.token:
        print("WARN: no Aura token — provenance is untested, so absent rows that read "
              "Running stay in the headline.", file=sys.stderr)
    return notion_rows, classify(catalogue, notion_rows, prober), prober


def _report_probe_failures(prober: Prober) -> None:
    if prober.failures:
        print(f"\nWARN: {len(prober.failures)} provenance probe(s) did not answer — those "
              f"rows are reported as untested, never as unsourced:", file=sys.stderr)
        for f in prober.failures[:10]:
            print(f"  - {f}", file=sys.stderr)


def cmd_report(args: argparse.Namespace) -> int:
    _, b, prober = _reconcile(args)
    print(render(b))
    _report_probe_failures(prober)
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    from notion_rest import notion, make_select
    notion_rows, b, prober = _reconcile(args)
    # (name, status to write). Only two writes exist: this script's marker, and
    # its withdrawal.
    todo = ([(n, STOPPED) for n in sorted(b["to_mark"])]
            + [(n, "Running") for n in sorted(b["to_unmark"])])
    print(render(b))
    _report_probe_failures(prober)
    if not todo:
        return 0
    print(f"\n{'Would write' if args.dry_run else 'Writing'} {len(todo)} row(s): "
          f"{len(b['to_mark'])} to `{STOPPED}`, {len(b['to_unmark'])} back to `Running`. "
          f"End Date is left blank on purpose — `updatedAt` is a last-touch stamp, not an end, "
          f"and a feature flag has no end at all.")
    if args.dry_run:
        return 0
    failed = []
    for i, (name, status) in enumerate(todo, 1):
        try:
            notion.update_page_props(notion_rows[name]["page_id"], {"Status": make_select(status)})
            print(f"[{i:>4}/{len(todo)}] {name} -> {status}")
        except Exception as e:  # noqa: BLE001 — one bad row must not strand the rest
            failed.append((name, f"{type(e).__name__}: {e}"))
            print(f"[{i:>4}/{len(todo)}] {name}  FAILED — {e}", file=sys.stderr)
    print(f"\n{len(todo) - len(failed)} written, {len(failed)} failed.")
    for name, why in failed:
        print(f"  - {name}: {why}")
    _invalidate_cache()
    return 1 if failed else 0


def _invalidate_cache() -> None:
    """The rows changed, so the retrieve-knowledge mirror of them is stale."""
    base = os.environ.get("AI_KNOWLEDGE_CACHE_DIR") or os.path.expanduser("~/.cache/ai-knowledge")
    path = os.path.join(base, "learnings__experiments.md")
    try:
        os.remove(path)
        print(f"cache invalidated: {path}")
    except FileNotFoundError:
        pass
    except OSError as e:
        print(f"WARN: could not invalidate {path}: {e}", file=sys.stderr)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--catalogue", help="Read GET /experiments from this JSON file instead of the API.")
    # On the subcommands, not the top level: `report --probe-orphans` is what
    # anyone types, and argparse only accepts a top-level flag BEFORE the verb.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--probe-orphans", action="store_true",
                        help="Probe EVERY row absent from the catalogue for provenance, not "
                             "just the ones that read Running. One HTTP call per absent row "
                             "(2,425 on 2026-09-22); resolves the untested lump into archived "
                             "vs never held.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("report", parents=[common], help="Print the disagreement counts. No writes.")
    a = sub.add_parser("apply", parents=[common],
                       help="Write `Stopped splitting` onto rows that stopped.")
    a.add_argument("--dry-run", action="store_true", help="Print the plan, write nothing.")
    args = ap.parse_args()
    return {"report": cmd_report, "apply": cmd_apply}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
