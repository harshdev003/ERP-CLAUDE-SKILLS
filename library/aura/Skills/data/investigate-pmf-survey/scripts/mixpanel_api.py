"""Mixpanel API helper for AINative skills.

Usage from a skill script:

    from datetime import timedelta
    from mixpanel_api import mixpanel, MCPFallback

    if mixpanel.available:
        # Try the identified-user case first: distinct_id == Firebase UID.
        profile = mixpanel.get_engage_profile(uid)

        # Fall through to the property-fallback recipe for events. Build the
        # window off `mixpanel.project_today()`, never `date.today()` or a UTC
        # clock — see "Project calendar" below.
        end = mixpanel.project_today()
        events, hit_prop = mixpanel.find_events_by_uid(
            uid,
            from_date=end - timedelta(days=6),
            to_date=end,
        )
        if profile is None and events:
            # Anonymous distinct_id case (common for AurieWeb signups never
            # `identify()`'d) — re-query Engage with the resolved distinct_id.
            top = mixpanel.top_distinct_id(events)
            profile = mixpanel.get_engage_profile(top) if top else None
    else:
        raise MCPFallback("Mixpanel API creds unavailable; use MCP recipe")

Why this exists
---------------
The Mixpanel MCP can answer point reads but isn't always loaded into a Claude
Code session, and `mcp__workspace__bash` strips host env so MCP connectors are
unreachable from Cowork sandboxes (PROD-1305). Falling through to the REST API
with a project-scoped service account keeps `data:profile-user` and
`data:analyze-user-behavior` working when MCP is absent or capped.

The Mixpanel REST surface covered here:

- **Engage** (`https://mixpanel.com/api/2.0/engage`) — single profile lookup
  keyed by `distinct_id`. Returns the people property bag.
- **Export** (`https://data.mixpanel.com/api/2.0/export`) — JSONL stream of
  every event matching a `where` clause over a date range. Used both for the
  event stream and for property-fallback distinct_id resolution.

Project calendar
----------------
Mixpanel validates `from_date` / `to_date` against the **project's** calendar
day, not UTC. Aurie's project (963821) runs on a Pacific calendar; the exact
timezone and the `/api/app/me` read that resolves it are canonical in AI
Knowledge `Tools/Mixpanel` -> Project timezone. A UTC-derived date is therefore
a full day ahead of the project for the whole US evening, and the export
rejects the request outright:

    400 to_date cannot be later than today; to_date=2026-08-05; today=2026-08-04

`export_events` therefore clamps both ends of the window to `project_today()`,
so no caller can construct an out-of-range date. Build windows off
`project_today()` rather than `date.today()` / `datetime.now(timezone.utc)` —
the clamp is a backstop, not a substitute for using the right clock.

Export duplication
------------------
The raw Export endpoint repeats rows. Not a client bug and not double
instrumentation — the duplicated rows are byte-identical including
`$insert_id` *and* `$mp_api_timestamp_ms`, i.e. one ingest surfacing twice on
the way out. What comes back is whole day-shards emitted more than once: the
block boundaries land exactly on day boundaries, the repeat factor varies by
user (2x and 3x observed in the same response), and a single-day window comes
back clean. Two runs of the identical request duplicate the same rows in a
different order, so it is stable rather than transient.

`export_events` therefore dedupes on `_dedupe_key` — Mixpanel's own four-field
ingest-dedupe tuple, see that function for why nothing narrower will do —
before yielding, and warns with the count when it drops anything. Measured
2026-08-14 over three UIDs on a 7-day window: 732 -> 365, 2435 -> 1218,
2905 -> 1132. Deduped per-day counts match an independent single-day export of
the same day exactly, which is the check that says the dedupe removes only the
repeats.

The dedupe holds one key per distinct event for the life of the call, where the
generator was previously constant-memory. Bounding it is not an option: the two
copies of a repeated shard sit hundreds of rows apart (616 observed), so a
fixed-size window would miss exactly the duplicates this exists to remove. The
real cost is small — callers pass a per-user `where` clause and a bounded
window — but an unfiltered export over a wide window now holds a set the size
of the project's event count, which is one more reason the `where`-less form
stays documented as tiny-window-only.

The inflation is why this is worth a read-side fix rather than a note: nothing
errors, every count stays internally consistent, and the factor is *not*
uniform across users — so it silently distorts comparisons between users as
well as absolute totals.

Auth
----
HTTP Basic with a project-scoped service account:

    MIXPANEL_SERVICE_ACCOUNT_USERNAME=<service-account-username>
    MIXPANEL_SERVICE_ACCOUNT_SECRET=<service-account-secret>
    MIXPANEL_PROJECT_ID=963821          # Aurie project

Set these in `~/.claude/settings.json` under the `env` block. See
CLAUDE.md → "Token inventory" for the rotation + admin notes.

Cowork caveat
-------------
`mcp__workspace__bash` strips host env (see PROD-1305 / CLAUDE.md "Known Cowork
constraints"). The three Mixpanel env vars won't reach Cowork sandboxes
automatically; skills targeting Cowork must use the per-session paste-export
workaround.

Canonical copy lives at
    plugins/data/skills/analyze-user-behavior/scripts/mixpanel_api.py
Per CLAUDE.md → "Skill authoring quick reference", any other consumer skill
that needs Mixpanel API access carries a byte-identical copy under its own
`scripts/`. Sync edits across copies in the same PR.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterator, Optional, Union

try:
    import requests
    from requests.auth import HTTPBasicAuth
except ImportError as e:  # pragma: no cover - the helper is import-time
    raise SystemExit(
        "mixpanel_api requires the 'requests' library. Install with: pip install requests"
    ) from e

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9
    ZoneInfo = None  # type: ignore[assignment]


# US data center hosts. EU projects would use `eu.mixpanel.com` and
# `data-eu.mixpanel.com`; Aurie's project (963821) is US so we hard-code US.
ENGAGE_URL = "https://mixpanel.com/api/2.0/engage"
EXPORT_URL = "https://data.mixpanel.com/api/2.0/export"
INSIGHTS_URL = "https://mixpanel.com/api/2.0/insights"

# Undocumented but stable app endpoint. The documented Query / Export surface
# exposes no project-settings read, and this one takes the same service-account
# Basic auth and returns `results.projects.<project_id>.{timezone, utc_offset}`.
PROJECT_SETTINGS_URL = "https://mixpanel.com/api/app/me"

# Only the fallback for when that read is unavailable — see `_resolve_project_tz`.
# Canonical home for this value is AI Knowledge `Tools/Mixpanel` -> Project timezone;
# it is inlined here because the fallback needs a real value. Verified 2026-08-04
# against `/api/app/me` for project 963821 ("Project Aura"):
# `"timezone": "US/Pacific"`, `"utc_offset": -420.0`.
DEFAULT_PROJECT_TIMEZONE = "US/Pacific"

# Property fallback chain for resolving a Firebase UID to events when the user
# was never `identify()`'d (and so distinct_id != UID). First non-empty wins.
DEFAULT_PROPERTY_CHAIN = ("UserID", "$user_id", "userId")

DateLike = Union[str, date, datetime]


def _warn(message: str) -> None:
    """Emit a helper-level warning to stderr.

    Clamping and timezone fallbacks are the kind of thing that is invisible in
    the result — a narrower window returns fewer events, not an error — so they
    have to announce themselves somewhere the run log will keep.
    """
    print(f"mixpanel_api: {message}", file=sys.stderr)


def _dedupe_key(event: dict) -> Optional[tuple]:
    """Mixpanel's own ingest-dedupe tuple for one exported event, or None.

    ``(event name, $distinct_id, time, $insert_id)`` — all four fields Mixpanel
    deduplicates on at ingest ("if all four of these properties are identical
    across two or more events … Mixpanel considers them duplicates", *Event
    Deduplication*, docs.mixpanel.com/reference/event-deduplication). Matching
    the ingest contract exactly is what makes it safe to reapply on read: two
    rows sharing all four are by definition the same logical event, because a
    genuinely distinct one could not have survived ingestion.

    **Not `$insert_id` alone.** Clients reuse a single id across the several
    event names of one lifecycle — a push SDK emits `Notification Delivered`
    and `Notification Received` under one id, `Notification Opened` and
    `Notification Clicked` under another. Keyed on the id alone those collapse
    into one event. Measured on a duplicate-free single-day export: 150 real
    events, 148 distinct `$insert_id`s, so that key silently discards 2 of them.

    **And not the first three without `time`.** No group in the sampled corpus
    (7854 events) spanned more than one timestamp, so including it changes no
    measured result — it is here because the two ways this key can be wrong are
    not symmetric. Too fine and a duplicate survives, which is the loud,
    already-known failure. Too coarse and a real event disappears, which is the
    silent one this whole change exists to remove. A reused id on the same
    event and user across two days is exactly the shape the corpus is too short
    (7 days, against a 90-day default window) to rule out.

    Returns None when the event carries no `$insert_id`. Such an event is
    passed through rather than deduped: every event in the sampled corpus had
    one, so this is a defensive branch, and folding them onto a shared
    ``(event, distinct_id, time, None)`` key would collapse same-second events
    into a single row — turning a missing property into data loss.
    """
    props = event.get("properties") or {}
    insert_id = props.get("$insert_id")
    if not insert_id:
        return None
    return (
        event.get("event"),
        props.get("$distinct_id"),
        props.get("time"),
        insert_id,
    )


def _as_date_str(value: DateLike) -> str:
    """Normalise a date-ish value to ``YYYY-MM-DD``.

    `YYYY-MM-DD` strings order lexicographically the same way dates order
    chronologically, which is what lets the clamp be a plain ``min()``.
    Normalising first means a caller that passes a ``date`` — the natural thing
    when computing a trailing window — gets clamped too, instead of silently
    skipping it on a ``str`` vs ``date`` comparison.
    """
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


# Legacy names Mixpanel still reports (`US/Pacific`) -> their canonical zones. Some
# tz databases ship without the legacy links (Ubuntu 26.04 moves them to
# `tzdata-legacy`), so the canonical name is tried when the reported one fails.
_LEGACY_ZONES = {
    "US/Alaska": "America/Anchorage",
    "US/Arizona": "America/Phoenix",
    "US/Central": "America/Chicago",
    "US/Eastern": "America/New_York",
    "US/Hawaii": "Pacific/Honolulu",
    "US/Mountain": "America/Denver",
    "US/Pacific": "America/Los_Angeles",
}


def _zone(name: Optional[str]):
    """``ZoneInfo(name)`` or None — never raises, so callers can ladder."""
    if not name or ZoneInfo is None:
        return None
    for candidate in (name, _LEGACY_ZONES.get(name)):
        if not candidate:
            continue
        try:
            return ZoneInfo(candidate)
        except Exception:
            # Unknown zone name, or a host with no tz database (slim containers
            # ship none unless `tzdata` is installed).
            continue
    return None


class MCPFallback(RuntimeError):
    """Raised when the REST path is unavailable; caller should use the MCP path.

    The skill author writes:

        try:
            profile = mixpanel.get_engage_profile(uid)
        except MCPFallback:
            profile = mcp_engage_fallback(uid)
    """


class _MixpanelClient:
    """Thin REST client. One per process. Stateless beyond the creds."""

    def __init__(
        self,
        username: Optional[str] = None,
        secret: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> None:
        self._username = username or os.environ.get("MIXPANEL_SERVICE_ACCOUNT_USERNAME")
        self._secret = secret or os.environ.get("MIXPANEL_SERVICE_ACCOUNT_SECRET")
        self._project_id = project_id or os.environ.get("MIXPANEL_PROJECT_ID")
        self._project_tz = None            # resolved lazily, once per process
        self._project_settings = None
        self._project_settings_fetched = False
        self._session = requests.Session() if self.available else None
        if self.available:
            self._session.auth = HTTPBasicAuth(self._username, self._secret)
            self._session.headers.update({"Accept": "application/json"})

    @property
    def available(self) -> bool:
        """True when all three creds are set; callers must check before using."""
        return bool(self._username and self._secret and self._project_id)

    @property
    def project_id(self) -> Optional[str]:
        return self._project_id

    def _require(self) -> None:
        if not self.available:
            missing = [
                name
                for name, value in (
                    ("MIXPANEL_SERVICE_ACCOUNT_USERNAME", self._username),
                    ("MIXPANEL_SERVICE_ACCOUNT_SECRET", self._secret),
                    ("MIXPANEL_PROJECT_ID", self._project_id),
                )
                if not value
            ]
            raise MCPFallback(
                f"Mixpanel API creds missing ({', '.join(missing)}). Set them "
                "in ~/.claude/settings.json env block (see CLAUDE.md → 'Token "
                "inventory') or fall back to the MCP recipe documented in this "
                "skill's SKILL.md."
            )

    # ---- low-level wrappers ---------------------------------------------

    def _get_json(self, url: str, *, params: dict[str, Any]) -> Any:
        """GET a Mixpanel endpoint that returns a single JSON document.

        Used by Engage / Insights. Export is streamed via ``_iter_jsonl``.

        Retries on 429 with the server-suggested Retry-After, capped.
        """
        self._require()
        params = dict(params)
        params.setdefault("project_id", self._project_id)
        for attempt in range(4):
            r = self._session.get(url, params=params, timeout=60)
            if r.status_code == 429:
                wait = float(r.headers.get("Retry-After", 1.0))
                time.sleep(min(wait, 5.0))
                continue
            r.raise_for_status()
            return r.json()
        r.raise_for_status()
        return r.json()

    def _iter_jsonl(self, url: str, *, params: dict[str, Any]) -> Iterator[dict]:
        """Stream a Mixpanel JSONL endpoint (Export) line by line.

        Each non-empty line is one event. Yields the parsed dict; skips
        whitespace-only lines defensively.
        """
        self._require()
        params = dict(params)
        params.setdefault("project_id", self._project_id)
        for attempt in range(4):
            r = self._session.get(url, params=params, timeout=300, stream=True)
            if r.status_code == 429:
                wait = float(r.headers.get("Retry-After", 1.0))
                time.sleep(min(wait, 5.0))
                continue
            r.raise_for_status()
            for raw in r.iter_lines(decode_unicode=True):
                if not raw or not raw.strip():
                    continue
                try:
                    yield json.loads(raw)
                except json.JSONDecodeError:
                    # Mixpanel Export is line-delimited JSON; a parse error is
                    # almost always a truncation. Surface and stop.
                    raise
            return
        # If we exhausted retries on 429, raise the final response status.
        r.raise_for_status()

    # ---- project calendar -------------------------------------------------

    def _fetch_project_settings(self) -> Optional[dict]:
        """This project's row from ``/api/app/me``, or None. Never raises.

        Best-effort by design: a project timezone we could not read is a reason
        to fall back to the documented default, not to fail a data pull.
        """
        if self._project_settings_fetched:
            return self._project_settings
        self._project_settings_fetched = True
        if not self.available:
            return None
        try:
            r = self._session.get(PROJECT_SETTINGS_URL, timeout=20)
            r.raise_for_status()
            projects = (r.json().get("results") or {}).get("projects") or {}
            self._project_settings = projects.get(str(self._project_id))
        except Exception as exc:  # network, auth, or a shape change
            _warn(
                f"could not read the project timezone from Mixpanel ({exc}); "
                f"falling back to {DEFAULT_PROJECT_TIMEZONE!r}."
            )
        return self._project_settings

    def _resolve_project_tz(self):
        """Best-effort tzinfo for the project's calendar. Never raises.

        Ladder, first success wins:

        1. ``MIXPANEL_PROJECT_TIMEZONE`` — an explicit operator override.
        2. Mixpanel itself. Authoritative: it is the same setting the export
           endpoint validates against, so it cannot drift from the project the
           way a constant in this file can.
        3. ``DEFAULT_PROJECT_TIMEZONE``.
        4. UTC, loudly — wrong for a project on any US timezone, so it warns
           rather than looking like a success.

        Rung 2 earns its request. The two ways a guessed timezone can be wrong
        are not symmetric: a guess *ahead* of the project surfaces as the 400 in
        the module docstring and gets noticed, while a guess *behind* it clamps
        a live day out of the window and nothing errors at all. Asking removes
        both directions; guessing can only ever protect against the loud one.
        """
        override = os.environ.get("MIXPANEL_PROJECT_TIMEZONE")
        if override:
            tz = _zone(override)
            if tz is not None:
                return tz
            _warn(f"MIXPANEL_PROJECT_TIMEZONE={override!r} is not a known zone; ignoring it.")

        settings = self._fetch_project_settings() or {}
        tz = _zone(settings.get("timezone"))
        if tz is not None:
            return tz
        offset = settings.get("utc_offset")
        if isinstance(offset, (int, float)):
            # No usable tz database on this host. The offset comes from the same
            # payload and is a live snapshot, so it is right for "today" even
            # though a fixed offset cannot model a DST transition.
            return timezone(timedelta(minutes=offset))

        tz = _zone(DEFAULT_PROJECT_TIMEZONE)
        if tz is not None:
            return tz
        _warn(
            f"no tz database for {DEFAULT_PROJECT_TIMEZONE!r}; computing the project "
            "calendar in UTC. Windows may run a day ahead of the project — install "
            "`tzdata`, or set MIXPANEL_PROJECT_TIMEZONE."
        )
        return timezone.utc

    def project_timezone(self):
        """The tzinfo the project's calendar day is measured in (cached)."""
        if self._project_tz is None:
            self._project_tz = self._resolve_project_tz()
        return self._project_tz

    def project_today(self) -> date:
        """Today on the project's calendar — the export's inclusive upper bound.

        Use this to build windows. ``date.today()`` follows the host's clock and
        ``datetime.now(timezone.utc).date()`` follows UTC; neither is the clock
        Mixpanel validates against.
        """
        return datetime.now(tz=self.project_timezone()).date()

    def clamp_window(self, from_date: DateLike, to_date: DateLike) -> tuple[str, str]:
        """Clamp an inclusive window to the project's calendar, warning if it fires.

        **Both** ends are clamped, not just ``to_date``. Clamping only the upper
        end turns a single-day "today" request (``from == to ==`` the caller's
        today) into an inverted ``from > to`` window, which Mixpanel rejects just
        as loudly as the date it replaced — and that single-day shape is exactly
        what `data:investigate-pmf-survey`'s same-day-signup entries produce.
        """
        today = self.project_today().isoformat()
        raw_from, raw_to = _as_date_str(from_date), _as_date_str(to_date)
        clamped_from, clamped_to = min(raw_from, today), min(raw_to, today)
        if (clamped_from, clamped_to) != (raw_from, raw_to):
            _warn(
                f"window {raw_from}..{raw_to} runs past the project's current date "
                f"({today}); clamped to {clamped_from}..{clamped_to}. Mixpanel "
                "validates dates against the project calendar, not UTC."
            )
        return clamped_from, clamped_to

    # ---- high-level helpers ---------------------------------------------

    def get_engage_profile(self, distinct_id: str) -> Optional[dict]:
        """Return the engage profile keyed by ``distinct_id``, or None if absent.

        Wraps ``GET /api/2.0/engage?distinct_id=<id>``. Mixpanel always returns
        a paginated envelope (``{"results": [...]}``) — for a distinct_id
        lookup there's 0 or 1 row.
        """
        resp = self._get_json(ENGAGE_URL, params={"distinct_id": distinct_id})
        results = resp.get("results") or []
        return results[0] if results else None

    def export_events(
        self,
        *,
        from_date: DateLike,
        to_date: DateLike,
        where: Optional[str] = None,
    ) -> Iterator[dict]:
        """Stream events from ``GET /api/2.0/export`` over a date range.

        Both ends are clamped to ``project_today()`` before the request goes
        out, so no caller can construct a date Mixpanel will reject — see
        "Project calendar" in the module docstring for why that is not the same
        as clamping to today's UTC date.

        Args
        ----
        from_date: inclusive start date, ``YYYY-MM-DD`` or a ``date``.
        to_date: inclusive end date, ``YYYY-MM-DD`` or a ``date``.
        where: optional Mixpanel ``where`` expression
            (e.g. ``properties["UserID"] == "abc"``). Omit to stream every
            event in the window — only safe for tiny windows or projects.

        Yields
        ------
        One event dict per line of the JSONL response, **deduplicated** — see
        "Export duplication" in the module docstring. Shape:
        ``{"event": "...", "properties": {"$distinct_id": "...", ...}}``.
        """
        from_date, to_date = self.clamp_window(from_date, to_date)
        params: dict[str, Any] = {"from_date": from_date, "to_date": to_date}
        if where:
            params["where"] = where

        seen: set[tuple] = set()
        kept = dropped = 0
        for event in self._iter_jsonl(EXPORT_URL, params=params):
            key = _dedupe_key(event)
            if key is not None:
                if key in seen:
                    dropped += 1
                    continue
                seen.add(key)
            kept += 1
            yield event

        if dropped:
            # A silent drop and a silent 2x are equally invisible in the result,
            # so the count goes to the run log either way.
            _warn(
                f"export returned {kept + dropped} rows for "
                f"{from_date}..{to_date}, {dropped} of them repeats of a row "
                f"already in the stream; yielded {kept} distinct events "
                f"(deduped on event + $distinct_id + time + $insert_id)."
            )

    def find_events_by_uid(
        self,
        uid: str,
        *,
        from_date: DateLike,
        to_date: DateLike,
        property_chain: tuple[str, ...] = DEFAULT_PROPERTY_CHAIN,
    ) -> tuple[list[dict], Optional[str]]:
        """Resolve a Firebase UID to its event stream via property fallback.

        Tries each property in ``property_chain`` (default
        ``("UserID", "$user_id", "userId")``) in order. First non-empty match
        wins — returns the events plus which property matched. Mirrors the
        Mixpanel MCP recipe in `data:analyze-user-behavior` Step 2.1.

        Inherits ``export_events``' clamp to the project calendar, so an
        out-of-range ``to_date`` narrows the window instead of raising.

        Returns
        -------
        ``(events, property_name)`` — ``events`` is a fully materialised list
        (not a generator), ``property_name`` is the chain entry that hit, or
        ``None`` if all three returned empty.
        """
        for prop in property_chain:
            where = f'properties["{prop}"] == "{uid}"'
            events = list(self.export_events(
                from_date=from_date,
                to_date=to_date,
                where=where,
            ))
            if events:
                return events, prop
        return [], None

    @staticmethod
    def top_distinct_id(events: list[dict]) -> Optional[str]:
        """Pick the most common ``$distinct_id`` across the event list.

        For AurieWeb-style anonymous users the Firebase UID lives only as an
        event property (`UserID` / `$user_id` / `userId`); their actual
        Mixpanel ``$distinct_id`` is an auto-generated ``$device:...``. Use
        this to derive the right distinct_id for a follow-up Engage call.
        """
        counter: Counter[str] = Counter()
        for ev in events:
            did = (ev.get("properties") or {}).get("$distinct_id")
            if did:
                counter[did] += 1
        if not counter:
            return None
        return counter.most_common(1)[0][0]

    @staticmethod
    def distinct_id_counts(events: list[dict]) -> list[tuple[str, int]]:
        """Return ``(distinct_id, count)`` pairs sorted by count descending.

        Useful for surfacing cross-device usage or `identify()` misses in
        **Sources** when more than one distinct_id carries the UID.
        """
        counter: Counter[str] = Counter()
        for ev in events:
            did = (ev.get("properties") or {}).get("$distinct_id")
            if did:
                counter[did] += 1
        return counter.most_common()

    def insights(self, *, bookmark_id: Optional[str] = None, **params: Any) -> dict:
        """Fetch a saved Insights report.

        Thin wrapper around ``GET /api/2.0/insights``. The Engage + Export
        helpers cover the recipes the two consuming skills need today; this
        is here for parity with the documented surface so future callers
        don't need to add another helper.
        """
        body: dict[str, Any] = dict(params)
        if bookmark_id:
            body["bookmark_id"] = bookmark_id
        return self._get_json(INSIGHTS_URL, params=body)


# Module-level singleton. Most callers want this directly.
mixpanel = _MixpanelClient()
