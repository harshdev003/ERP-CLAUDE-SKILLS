#!/usr/bin/env python3
"""Correlate a reported Aurie voice complaint across four sources.

Reads (all read-only): Bugsnag client errors, the ElevenLabs convai conversation
record, the Aura prod GCP logs, and the client device/audio context carried on the
Bugsnag event. Emits one digest that names the user-perceived symptom separately
from instrumentation artifacts.

Every leg degrades on its own: a missing token narrows the report and is recorded
under Sources rather than aborting the run.

Usage:
  investigate.py --user <firebase-uid> [--conversation <aura-conversation-id>]
                 [--at <iso8601>] [--since 24h] [--project <slug>] [--json]
  investigate.py --rating-text "<pasted k8bot rating message>"
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import correlate
import format as fmt

# ---------------------------------------------------------------------------
# Input resolution
# ---------------------------------------------------------------------------

# k8bot rating messages carry the identifiers in a trailing backticked block:
# `[dialogue: <uuid>, conversation: <uuid>, user: <uid>, prompt:<name>:<version>]`
#
# The value is delimited by the comma or the closing bracket, never by a
# character class: a Firebase uid is not always alphanumeric. Push keys contain
# `_` and `-`, and a provider-prefixed id looks like `google:1123…`. Matching
# `[A-Za-z0-9-]+` truncates both, and the damage is silent — the lookups run
# against a prefix, find nothing, and the run reads as "this user has no history".
_RATING_FIELD_RE = re.compile(
    r"\b(dialogue|conversation|user)\s*:\s*([^,\]]+)", re.I
)
_DURATION_RE = re.compile(r"^(\d+)([smhd])$")


def parse_rating_text(text: str) -> dict[str, str]:
    """Pull dialogue / conversation / user out of a pasted k8bot rating message."""
    found: dict[str, str] = {}
    for key, value in _RATING_FIELD_RE.findall(text or ""):
        cleaned = value.strip().strip("`'\"")
        if cleaned:
            found.setdefault(key.lower(), cleaned)
    return found


def duration_to_timedelta(value: str) -> timedelta:
    m = _DURATION_RE.match(value.strip())
    if not m:
        raise ValueError(f"Bad duration {value!r} — use forms like 30m, 6h, 2d")
    n, unit = int(m.group(1)), m.group(2)
    return {"s": timedelta(seconds=n), "m": timedelta(minutes=n),
            "h": timedelta(hours=n), "d": timedelta(days=n)}[unit]


def resolve_window(since: str, at: str | None) -> tuple[datetime, datetime, str]:
    """Return (start, end, human label) as tz-aware UTC datetimes.

    `--at` anchors a tight window around a known incident time; without it the
    window is the trailing `--since` period ending now.
    """
    now = datetime.now(timezone.utc)
    span = duration_to_timedelta(since)
    if at:
        anchor = _parse_iso(at)
        half = span / 2
        return anchor - half, anchor + half, f"{span} centred on {anchor.isoformat()}"
    return now - span, now, f"last {since}"


def _parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 timestamp and return it converted to UTC.

    A naive value is treated as already UTC. An offset value (e.g. a rating
    time pasted straight from Slack in PDT) is converted to true UTC, which
    matters because downstream call sites format the result with
    `strftime("...Z")` — that prints wall-clock numbers verbatim and appends
    a literal Z, so it only queries the intended instant when the value
    passed in is already UTC.
    """
    text = value.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# Bugsnag's `event.since` filter takes a trailing duration from *now* — it has no
# absolute-range form. So a window centred on `--at` cannot be expressed directly:
# it has to be widened to "now back to the window start", and the extra span
# trimmed client-side. Passing the raw `--since` instead would look at the last N
# hours from now, which for a historical incident is the wrong period entirely and
# silently returns no client errors.
BUGSNAG_MAX_SINCE_DAYS = 30


def bugsnag_since(start: datetime) -> str:
    """Trailing Bugsnag duration wide enough to include `start`."""
    elapsed = datetime.now(timezone.utc) - start
    hours = max(1, -(-int(elapsed.total_seconds()) // 3600))  # ceil
    if hours <= 24:
        return f"{hours}h"
    days = min(BUGSNAG_MAX_SINCE_DAYS, -(-hours // 24))
    return f"{days}d"


def _received_at(event: dict[str, Any]) -> Optional[datetime]:
    stamp = event.get("received_at") or event.get("timestamp")
    if not stamp:
        return None
    try:
        return _parse_iso(str(stamp))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Bugsnag leg
# ---------------------------------------------------------------------------

# The metadata sections a voice event carries. `metadata` holds the watchdog's
# own accounting (replyStartTime / timestamp / agentState); `audio` holds the
# route, whose device name must be redacted before render.
_META_SECTION = "metadata"
_AUDIO_SECTION = "audio"
_CONVERSATION_SECTION = "conversation"


def _windowed_counts(client: Any, project_id: str, error_id: str, since: str) -> dict[str, Any]:
    """Org-wide events/users for one signature inside the window.

    Uses the client's request helper directly rather than extending `bugsnag.py`,
    which is a byte-identical copy of the canonical helper and must stay that way.
    Filter keys still go through `build_filters`, so a typo raises here instead of
    silently returning the unfiltered org-wide counts.
    """
    params = {
        **client.build_filters(project_id, {
            "event.since": since,
            "error": error_id,
        }),
        "per_page": 1,
    }
    rows = client._get(f"/projects/{project_id}/errors", params).json()
    if isinstance(rows, list) and rows:
        return {"org_events": rows[0].get("events"), "org_users": rows[0].get("users")}
    return {}


def _user_events(
    client: Any,
    project_id: str,
    error_id: str,
    user_id: str,
    since: str,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    want: int = 5,
    max_pages: int = 10,
) -> list[dict]:
    """The user's events for one signature, inside the requested window.

    Events come back newest-first, so a client-side trim alone is not enough: on an
    endemic signature the first page of a historical query is entirely
    out-of-window events, and trimming them leaves nothing — losing the watchdog
    metadata and device context without any sign that it happened.

    Both ends are therefore bounded server-side — `event.since` (trailing) for the
    lower end and `event.before` (absolute) for the upper — so the first page
    already starts at the window's end. Pagination follows `Link: rel="next"` as a
    backstop and stops as soon as events predate the window.
    """
    wanted: dict[str, Any] = {"user.id": user_id, "event.since": since}
    if end is not None:
        wanted["event.before"] = end.strftime("%Y-%m-%dT%H:%M:%SZ")
    params: dict[str, Any] = {
        **client.build_filters(project_id, wanted),
        "per_page": 100,
    }

    kept: list[dict] = []
    for row in client._paginate(
        f"/projects/{project_id}/errors/{error_id}/events", params, max_pages=max_pages
    ):
        if not isinstance(row, dict):
            continue
        stamp = _received_at(row)
        if stamp is not None:
            if end and stamp > end:
                continue
            if start and stamp < start:
                break  # newest-first, so everything beyond here is older too
        kept.append(row)
        if len(kept) >= want:
            break
    return kept


def _classify(message: str) -> tuple[str, bool]:
    """Return (reading, is_voice_relevant) for an error signature."""
    if correlate.WATCHDOG_MESSAGE in message:
        return ("Fixed-duration reply watchdog — verify against the vendor transcript "
                "before calling it a stall (PROD-4395)", True)
    for transport in correlate.TRANSPORT_MESSAGES:
        if transport in message:
            return ("Transport-level — describes the connection, not the agent", True)
    return ("", False)


def collect_bugsnag(
    user_id: str,
    since: str,
    project_slug: str | None,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict[str, Any]:
    """Signatures the user hit, with org-wide scale and the richest voice event.

    `since` is the trailing duration handed to Bugsnag; `start`/`end` are the
    window the caller asked for and trim the per-event fetches back to it.
    """
    import bugsnag as bs

    client = bs.BugsnagClient()
    org_id = client.find_org_id()
    projects = client.list_projects(org_id)
    if project_slug:
        chosen = [p for p in projects if p["slug"] == project_slug]
        if not chosen:
            # Fuzzy so `userweb` resolves to `aura-userweb`, same affordance as
            # the sibling search skills.
            near = difflib.get_close_matches(project_slug, [p["slug"] for p in projects], n=1, cutoff=0.4)
            substring = [p for p in projects if project_slug.lower() in p["slug"].lower()]
            chosen = substring or [p for p in projects if p["slug"] in near]
        if not chosen:
            raise bs.BugsnagError(f"No Bugsnag project matches {project_slug!r}")
        projects = chosen

    rows: list[dict[str, Any]] = []
    event_context: dict[str, Any] | None = None
    warnings: list[str] = []   # real failures
    notes: list[str] = []      # expected, explanatory
    watchdog_meta: list[dict[str, Any]] = []

    for project in projects:
        try:
            errors = client.list_errors(project["id"], since=since, user_id=user_id, max_results=15)
        except bs.BugsnagError:
            continue
        for err in errors:
            message = err.get("message") or ""
            reading, voice_relevant = _classify(message)
            row = {
                "project": project["slug"],
                "error_id": err.get("id"),
                "error_class": err.get("error_class"),
                "message": message,
                "user_events": err.get("events"),
                "reading": reading,
                "voice_relevant": voice_relevant,
            }
            if voice_relevant:
                # A swallowed failure here is the worst outcome available: the
                # watchdog verdict and device context vanish and the digest looks
                # like a clean call. Record every miss so Sources can say so.
                try:
                    row.update(_windowed_counts(client, project["id"], err["id"], since))
                except Exception as e:
                    warnings.append(f"org-wide scale for `{message[:40]}` unavailable — {e}")
                try:
                    found = _user_events(client, project["id"], err["id"], user_id, since,
                                         start=start, end=end)
                    if not found:
                        # Not a failure. The signature list is queried over the
                        # widened trailing period, so a signature whose events all
                        # fall outside the centred window legitimately has none in
                        # it — common with --at. Record it as context, not a fault,
                        # so the reader knows why there is no metadata for it.
                        notes.append(
                            f"`{message[:40]}` has no events inside the window "
                            f"(its events fall in the wider trailing period), so it "
                            f"contributes no watchdog or device metadata"
                        )
                    for stub in found:
                        detail = client.get_event(project["id"], stub["id"])
                        meta = detail.get("metaData") or {}
                        section = meta.get(_META_SECTION) or {}
                        if correlate.WATCHDOG_MESSAGE in message and section:
                            watchdog_meta.append(section)
                        if event_context is None:
                            event_context = _event_context(detail)
                except Exception as e:
                    warnings.append(f"event detail for `{message[:40]}` unavailable — {e}")
            rows.append(row)

    rows.sort(key=lambda r: (not r["voice_relevant"], -(r.get("user_events") or 0)))
    return {
        "rows": rows,
        "event_context": event_context,
        "watchdog_meta": watchdog_meta,
        "warnings": warnings,
        "notes": notes,
    }


def _event_context(detail: dict[str, Any]) -> dict[str, Any]:
    meta = detail.get("metaData") or {}
    audio = meta.get(_AUDIO_SECTION) or {}
    conversation = meta.get(_CONVERSATION_SECTION) or {}
    app = detail.get("app") or {}
    device = detail.get("device") or {}
    rate, preferred = audio.get("sampleRate"), audio.get("preferredSampleRate")
    mismatch = False
    try:
        mismatch = rate is not None and preferred is not None and float(rate) != float(preferred)
    except (TypeError, ValueError):
        pass
    return {
        "app_version": app.get("version"),
        "device": " ".join(str(x) for x in (device.get("model"), device.get("os_version")) if x),
        "route_outputs": audio.get("routeOutputs"),
        "sample_rate": rate,
        "preferred_sample_rate": preferred,
        "sample_rate_mismatch": mismatch,
        "startup_total": conversation.get("startupTotalTime"),
        "elevenlabs_conversation_id": conversation.get("elevenLabsConversationId"),
        "aura_conversation_id": conversation.get("conversationId"),
    }


# ---------------------------------------------------------------------------
# GCP leg
# ---------------------------------------------------------------------------

def collect_gcp(user_id: str, start: datetime, end: datetime, max_entries: int) -> Any:
    import gcp
    import normalize

    auth = gcp.get_client()
    filter_str = (
        'resource.type="k8s_container" '
        'AND resource.labels.cluster_name="aura-prod-cluster-a" '
        f'AND (jsonPayload.userId="{user_id}" OR jsonPayload.signInUid="{user_id}") '
        f'AND timestamp>="{start.strftime("%Y-%m-%dT%H:%M:%SZ")}" '
        f'AND timestamp<="{end.strftime("%Y-%m-%dT%H:%M:%SZ")}"'
    )
    # Newest-first, so if the cap truncates it drops the oldest entries rather than
    # the ones nearest the reported incident. `summarize_our_leg` derives its span
    # from the stamps, so the order does not otherwise matter.
    raw = list(
        gcp.list_entries(auth, filter_str, max_entries=max_entries, order_by="timestamp desc")
    )
    leg = correlate.summarize_our_leg([normalize.normalize(e) for e in raw])
    # A truncated read cannot support a negative conclusion — no slow call and no
    # token fetch might simply be past the cap.
    leg.truncated = len(raw) >= max_entries
    return leg


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(description="Investigate an Aurie voice complaint end-to-end.")
    ap.add_argument("--user", help="Firebase user id")
    ap.add_argument("--conversation", help="Aura conversation id from the rating")
    ap.add_argument("--rating-text", help="Pasted k8bot rating message to parse identifiers from")
    ap.add_argument("--at", help="Incident time (ISO8601) to centre the window on")
    ap.add_argument("--since", default="24h", help="Window size (default 24h)")
    ap.add_argument("--project", help="Limit Bugsnag to one project slug")
    ap.add_argument("--max-entries", type=int, default=6000, help="Cap GCP entries fetched")
    ap.add_argument("--max-detail-fetches", type=int, default=40,
                    help="Cap ElevenLabs detail fetches while searching for the call")
    ap.add_argument("--json", action="store_true", help="Emit the raw correlated payload as JSON")
    args = ap.parse_args()

    user_id, conversation_id = args.user, args.conversation
    if args.rating_text:
        parsed = parse_rating_text(args.rating_text)
        user_id = user_id or parsed.get("user")
        conversation_id = conversation_id or parsed.get("conversation")
    if not user_id and not conversation_id:
        ap.error("need --user and/or --conversation (or --rating-text carrying them)")

    start, end, window_label = resolve_window(args.since, args.at)
    sources: dict[str, str] = {}

    # --- Bugsnag ---------------------------------------------------------
    bugsnag_out: dict[str, Any] = {"rows": [], "event_context": None, "watchdog_meta": []}
    if user_id:
        bs_since = bugsnag_since(start)
        capped = (datetime.now(timezone.utc) - start) > timedelta(days=BUGSNAG_MAX_SINCE_DAYS)
        try:
            bugsnag_out = collect_bugsnag(user_id, bs_since, args.project, start=start, end=end)
            note = f"{len(bugsnag_out['rows'])} signature(s) for this user"
            if capped:
                note += (
                    f" — ⚠️ the incident predates the {BUGSNAG_MAX_SINCE_DAYS}d query cap, so "
                    f"client errors for it are not retrievable and an empty result proves nothing"
                )
            if bs_since != args.since:
                # Say which period the counts describe. Bugsnag has no absolute
                # range, so signature totals cover the widened trailing period
                # even though the per-event detail is trimmed to the window.
                note += (
                    f"; queried `event.since={bs_since}` to reach the window, "
                    f"so signature/org counts cover that trailing period while "
                    f"per-event detail is trimmed to {window_label}"
                )
            else:
                note += f" in {window_label}"
            for w in bugsnag_out.get("warnings", []):
                note += f"; ⚠️ {w}"
            for n in bugsnag_out.get("notes", []):
                note += f"; {n}"
            sources["Bugsnag"] = note
        except Exception as e:
            sources["Bugsnag"] = f"unavailable — {e}"
    else:
        sources["Bugsnag"] = "skipped — needs a user id"

    # The client event often names the vendor conversation directly, which beats
    # searching for it.
    ctx = bugsnag_out.get("event_context") or {}
    conversation_id = conversation_id or ctx.get("aura_conversation_id")
    known_vendor_id = ctx.get("elevenlabs_conversation_id")

    # --- ElevenLabs ------------------------------------------------------
    # A list, not a single call: one Aura conversation routinely spans several
    # vendor calls, and judging a timed event against the wrong one invents both
    # verdicts (see correlate.classify_watchdog).
    convs: list = []
    try:
        import elevenlabs as el

        client = el.ElevenLabsClient()
        if known_vendor_id:
            # The client event names the vendor record, which beats searching for
            # it — but the id can be stale or past retention, so a failure here
            # falls through to the search rather than losing the whole leg.
            try:
                named = client.get_conversation(known_vendor_id)
                convs = [named]
                sources["ElevenLabs"] = f"resolved directly from the client event → `{known_vendor_id}`"
                # The named record is one call of a possibly longer session, so its
                # own conversationId is what finds the siblings.
                conversation_id = conversation_id or named.aura_conversation_id
            except el.ElevenLabsError as e:
                sources["ElevenLabs"] = f"direct fetch of `{known_vendor_id}` failed ({e}); searching instead"
                known_vendor_id = None
        if conversation_id or not convs:
            search = client.find_conversation(
                aura_conversation_id=conversation_id,
                user_id=user_id,
                start_unix=int(start.timestamp()),
                end_unix=int(end.timestamp()),
                max_detail_fetches=args.max_detail_fetches,
            )
            found = {c.conversation_id: c for c in convs}
            for c in search.conversations:
                found.setdefault(c.conversation_id, c)
            merged = sorted(found.values(), key=lambda c: c.start_unix or 0)
            # The client-named record can belong to a different Aura conversation
            # than the one we searched. The search's own grouping cannot see it,
            # so drop it here rather than letting one stray call inflate the
            # reconnect count and pull unrelated turns into every aggregate.
            target = (conversation_id or "").strip().lower()
            if target:
                # A call carrying no conversationId is *unknown*, not *other* —
                # ~1.5% of sessions start without conversation_initiation_client_data
                # (ENGMOB-2794), and the client event pointed at this record, so
                # dropping it would discard the one call we were handed.
                keep = [c for c in merged
                        if (c.aura_conversation_id or "").lower() in (target, "")]
                dropped = len(merged) - len(keep)
                if keep and dropped:
                    merged = keep
                    sources["ElevenLabs (note)"] = (
                        f"{dropped} call(s) in the window belong to a different Aura "
                        f"conversation and were left out of this session"
                    )
            convs = merged
            if search.conversations:
                if len(convs) > 1:
                    note = (
                        f"matched via dynamic_variables → {len(convs)} calls "
                        f"({len(convs) - 1} reconnect(s)) on this Aura conversation"
                    )
                else:
                    note = f"matched via dynamic_variables → `{convs[0].conversation_id}`"
                if search.sessions_seen > 1 and not conversation_id:
                    # Narrowing is right — mixing sessions would corrupt the
                    # reconnect count and the aggregates — but a narrowed corpus
                    # reads exactly like the whole one unless it is said.
                    note += (
                        f" (the window held {search.sessions_seen} distinct Aura "
                        f"conversations for this user; narrowed to the largest — "
                        f"pass --conversation to pick a different one)"
                    )
                if search.capped or search.page_limit_hit:
                    # The walk stopped early *and* returned calls, so the session
                    # may be missing legs. Every aggregate below — reconnect
                    # count, dead-call rows, latency — is then a lower bound, and
                    # a truncated session reads exactly like a complete one.
                    note += (
                        " — ⚠️ the search stopped early, so this session may be "
                        "incomplete; raise --max-detail-fetches or narrow --at/--since "
                        "before trusting the reconnect count or an absent call"
                    )
                if search.server_side_user_filter_effective is False:
                    note += (
                        " (server-side user filter not in effect — search was "
                        "workspace-wide and slower than it should be)"
                    )
                sources["ElevenLabs"] = note
            elif convs:
                pass  # the client event already named the call; nothing more found
            else:
                # Never collapse a give-up into an absence: `.capped` is the
                # difference between "no such call" and "stopped looking", and
                # only the first is a finding.
                sources["ElevenLabs"] = search.miss_reason(window_label, el.RETENTION_DAYS)
    except Exception as e:
        sources["ElevenLabs"] = f"unavailable — {e}"

    # --- GCP -------------------------------------------------------------
    our_leg = correlate.OurLeg()
    if user_id:
        try:
            our_leg = collect_gcp(user_id, start, end, args.max_entries)
            gcp_note = f"{our_leg.entry_count} entries in {window_label}"
            if our_leg.truncated:
                gcp_note += (
                    f"; ⚠️ hit the {args.max_entries}-entry cap, so the window is only "
                    f"partly read — narrow it with --at/--since or raise --max-entries "
                    f"before concluding anything from an absent signal"
                )
            sources["GCP prod logs"] = gcp_note
        except Exception as e:
            sources["GCP prod logs"] = f"unavailable — {e}"
    else:
        sources["GCP prod logs"] = "skipped — needs a user id"

    # --- Correlate -------------------------------------------------------
    watchdogs = [correlate.classify_watchdog(m, convs) for m in bugsnag_out.get("watchdog_meta", [])]
    latency = correlate.assess_latency(convs)
    verdict = correlate.build_verdict(
        convs, latency, watchdogs, our_leg, client_errors=len(bugsnag_out.get("rows", []))
    )

    if args.json:
        print(json.dumps({
            "user_prefix": correlate.uid_prefix(user_id),
            "conversation": correlate.short_id(conversation_id),
            "window": window_label,
            "calls": [
                {
                    "conversation_id": c.conversation_id,
                    "start_unix": c.start_unix,
                    "duration_secs": c.duration_secs,
                    "turns": len(c.turns),
                    "agent_turns": sum(1 for t in c.turns if t.role == "agent"),
                    "in_progress": c.in_progress,
                    "termination_reason": c.termination_reason,
                }
                for c in convs
            ],
            "reconnects": max(len(convs) - 1, 0),
            "verdict": asdict(verdict),
            "latency": _dump(latency),
            "watchdogs": [asdict(w) for w in watchdogs],
            "our_leg": _dump(our_leg),
            "bugsnag": bugsnag_out["rows"],
            "sources": sources,
        }, indent=2, default=str))
        return

    print(fmt.render_header(user_id=user_id, aura_conversation_id=conversation_id, window=window_label))
    print(fmt.render_verdict(verdict))
    print(fmt.render_vendor_call(convs))
    print(fmt.render_latency(latency))
    print(fmt.render_watchdogs(watchdogs))
    print(fmt.render_client_errors(bugsnag_out["rows"], event_context=bugsnag_out["event_context"]))
    print(fmt.render_our_leg(our_leg, truncated=our_leg.truncated))
    print(fmt.render_sources(sources))
    print(fmt.render_next_steps(user_id=user_id, convs=convs))


def _dump(obj: Any) -> Any:
    """Dataclass → dict, leaving plain values alone."""
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    return obj


if __name__ == "__main__":
    main()
