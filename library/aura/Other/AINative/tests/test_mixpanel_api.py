#!/usr/bin/env python3
"""`mixpanel_api.py` — the Export window clamp, and the Export read dedupe.

Two defects, both on the Export path, both silent in the same way: the result
looks like a normal answer. The clamp is covered first; the dedupe from
`MixpanelDedupeTests` down.

Defect 1 — the project-calendar clamp
-------------------------------------
Mixpanel validates `from_date` / `to_date` against the **project's** calendar
day, not UTC. Aurie's project (963821) is `US/Pacific`, so a UTC-derived date is
a full day ahead of the project for the whole US evening and the export rejects
the request:

    400 to_date cannot be later than today; to_date=2026-08-05; today=2026-08-04

Observed 2026-08-04 ~18:20 PDT (= 2026-08-05 01:20 UTC) during a
`data:run-all-analysis-cadence` tick: `find_events_by_uid` raised an unhandled
`HTTPError` and aborted the card build, on exactly the `tenure_days=0` PMF
entries the same-day-signup exception exists to rescue. [ENG-1071]

Why it needs failure injection rather than a live check
-------------------------------------------------------
The bug is only reachable in the hours where the project's calendar and UTC's
disagree — roughly 17:00–24:00 Pacific. A live call at any other hour passes
with the clamp removed, which is precisely how it shipped. Stubbing the session
puts "what date went on the wire" under the test's control at every hour.

`_session.get` is the single chokepoint — Engage, Export and the `/api/app/me`
timezone read all route through it — so one fake session covers every network
path with no `requests` monkeypatching and no live creds.

Scrub the environment, or the suite reaches the network
-------------------------------------------------------
`_MixpanelClient(username=None, ...)` does **not** mean "no creds" — every
constructor argument falls back to `os.environ`, so on a developer machine with
`MIXPANEL_SERVICE_ACCOUNT_*` set it builds a fully live client. The no-creds
test then issued a real six-day Export with no `where` clause, i.e. every event
in the project, and the run hung until it was killed.

That is the `tests/README.md` "a stub must fail, not hang" rule in a second
guise, so it gets the same treatment: `setUp` clears the Mixpanel variables for
every case, and the no-creds test asserts `available is False` before it
exercises anything. A leaked credential now fails on that assertion instead of
silently downloading a project.

Defect 2 — the Export endpoint repeats rows
-------------------------------------------
The raw Export stream emits whole day-shards more than once. Observed
2026-08-14 during a `data:investigate-pmf-survey` cadence tick: a 7-day window
for one UID returned 732 rows for 365 real events. The repeats are byte-equal
including `$insert_id` and `$mp_api_timestamp_ms`, so they are one ingest
surfacing twice, not two client sends.

Every count a consumer derives — sessions, messages, cap hits, purchase taps —
was inflated, with nothing raising and every number staying internally
consistent. The factor is not even constant: 2x and 3x rows appeared in one
response, and it varies per user, so relative comparisons between users were
distorted too.

Why failure injection rather than a live call
---------------------------------------------
Same reason as the clamp, from the other end. A live call *does* reproduce this
today, but a test that depends on it asserts a property of Mixpanel's current
serving behaviour: the day Mixpanel stops duplicating, a live test passes
whether or not the dedupe still exists, and the guard silently stops guarding.
Stubbing the transport puts the duplication under the test's control, so the
assertions are about the helper.

The key is the load-bearing choice, and `$insert_id` alone is wrong
-------------------------------------------------------------------
Clients reuse one `$insert_id` across the several event names of a lifecycle
(`Notification Delivered` / `Received`). On a duplicate-free single-day export
of 150 real events there were only 148 distinct `$insert_id`s — that key drops
2 real events to remove 0 duplicates.

The key is Mixpanel's own four-field ingest tuple: event name, `$distinct_id`,
`time`, `$insert_id`. `time` is in there on the asymmetry rather than on
evidence — no group in the sampled corpus spanned two timestamps — because a
key that is too fine leaves a duplicate (loud, and the state we started from)
while one that is too coarse deletes a real event (silent, and the whole point
of the change).

So the tests below assert the *negative* case too: two event names sharing an
id must both survive, two users sharing one must both survive, the same id at
two timestamps must both survive, and a clean response must come back
untouched. Per-construct dedupe tests pass against the wrong key; those do not.

Runs against **every** copy of `mixpanel_api.py` in `plugins/` (the file is
duplicated per skill under Architecture principle 1), so a copy that diverges
behaviourally fails here even when the byte-diff has been reconciled.

Set `AINATIVE_MIXPANEL_API_PATH` to point the suite at one specific file — used
to check the tests actually fail against the pre-fix commit (see
`tests/README.md`).
"""
from __future__ import annotations

import json
import os
import pathlib
import unittest
from datetime import date, datetime, timedelta, timezone

from reader_harness import load_reader, script_paths


def _targets() -> list[pathlib.Path]:
    override = os.environ.get("AINATIVE_MIXPANEL_API_PATH")
    return [pathlib.Path(override)] if override else script_paths("mixpanel_api.py")


MIXPANEL_PATHS = _targets()

# The production instant from the ticket. `PROJECT` is what Mixpanel called
# today; `UTC` is what a `datetime.now(timezone.utc).date()` caller produced.
PROJECT_TODAY = date(2026, 8, 4)
UTC_TODAY = date(2026, 8, 5)

# Every one of these is a constructor fallback, so any left set turns a client
# the test meant to be inert into a live one. See the module docstring.
MIXPANEL_ENV_VARS = (
    "MIXPANEL_SERVICE_ACCOUNT_USERNAME",
    "MIXPANEL_SERVICE_ACCOUNT_SECRET",
    "MIXPANEL_PROJECT_ID",
    "MIXPANEL_PROJECT_TIMEZONE",
)


class FakeResponse:
    def __init__(self, payload=None, *, lines=None, status_code=200):
        self.status_code = status_code
        self.headers: dict[str, str] = {}
        self._payload = payload
        self._lines = lines or []

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(
                f"the client sent a request the stub rejected with "
                f"{self.status_code} — it should never have been constructed"
            )

    def json(self):
        return self._payload

    def iter_lines(self, decode_unicode=False):
        return iter(self._lines)


class FakeSession:
    """Records every request and answers it from canned data.

    `raise_on_settings` reproduces a host where `/api/app/me` is unreachable —
    the branch that decides whether an unresolvable timezone degrades to the
    documented default or takes the whole data pull down with it.
    """

    def __init__(self, *, timezone_name="US/Pacific", utc_offset=-420.0,
                 project_id="963821", events=(), raise_on_settings=False):
        self.timezone_name = timezone_name
        self.utc_offset = utc_offset
        self.project_id = project_id
        self.events = list(events)
        self.raise_on_settings = raise_on_settings
        self.calls: list[tuple[str, dict]] = []
        self.auth = None
        self.headers: dict[str, str] = {}

    def get(self, url, params=None, timeout=None, stream=False):
        self.calls.append((url, dict(params or {})))

        if "/api/app/me" in url:
            if self.raise_on_settings:
                raise OSError("stubbed: project settings unreachable")
            project: dict = {"name": "Project Aura"}
            if self.timezone_name is not None:
                project["timezone"] = self.timezone_name
            if self.utc_offset is not None:
                project["utc_offset"] = self.utc_offset
            return FakeResponse({"results": {"projects": {self.project_id: project}}})

        if "/export" in url:
            self._assert_in_range(params or {})
            return FakeResponse(lines=[json.dumps(e) for e in self.events])

        raise AssertionError(f"unrouted URL in test: {url}")

    def _assert_in_range(self, params: dict) -> None:
        """Reject a future date exactly the way the real endpoint does.

        A stub that quietly accepts any date cannot tell a clamping client from
        a non-clamping one — the whole point of the suite. So the 400 is
        modelled here, and a test that reaches it fails with the real message
        rather than a bare assertion.

        Params are stringified first, the way `requests` serialises them onto
        the query string. Comparing them raw would make an unclamped `date`
        object fail with a `TypeError` about `>` — true, but it buries the
        actual defect under an artefact of the stub.
        """
        to_date = params.get("to_date")
        to_date = to_date.isoformat() if hasattr(to_date, "isoformat") else to_date
        if to_date and to_date > PROJECT_TODAY.isoformat():
            raise AssertionError(
                "to_date cannot be later than today; "
                f"to_date={to_date}; today={PROJECT_TODAY.isoformat()} "
                "(the clamp did not fire)"
            )

    def export_params(self) -> list[dict]:
        return [p for u, p in self.calls if "/export" in u]

    def export_windows(self) -> list[dict]:
        """Just the date keys, so `project_id` doesn't have to be restated."""
        return [{k: p[k] for k in ("from_date", "to_date") if k in p}
                for p in self.export_params()]


def build_client(mod, session: FakeSession, *, project_today=PROJECT_TODAY):
    """A client with fake creds, a stubbed session, and a pinned calendar.

    `project_today` is pinned rather than left on the wall clock so the
    assertions hold at every hour of the day. The resolution of the timezone
    *itself* is covered separately, against the stub.
    """
    client = mod._MixpanelClient(username="u", secret="s", project_id=session.project_id)
    client._session = session
    if project_today is not None:
        client.project_today = lambda: project_today
    return client


class MixpanelClampTests(unittest.TestCase):
    """Behaviour that must hold for every copy of the helper."""

    def test_repo_has_copies(self):
        self.assertTrue(MIXPANEL_PATHS, "no mixpanel_api.py found under plugins/")


def _clamp_case(path):
    class _Case(unittest.TestCase):
        maxDiff = None

        def setUp(self):
            self.mod = load_reader(path, siblings=())
            # Hermetic by construction, not by care — see the module docstring.
            self._saved_env = {k: os.environ.pop(k, None) for k in MIXPANEL_ENV_VARS}

        def tearDown(self):
            for key, value in self._saved_env.items():
                os.environ.pop(key, None)
                if value is not None:
                    os.environ[key] = value

        # -- the headline case ------------------------------------------

        def test_to_date_past_project_today_is_clamped_not_raised(self):
            """The production failure: a UTC-derived to_date, one day ahead.

            Before the clamp this reached the endpoint and came back 400, which
            `find_events_by_uid` let escape as an unhandled `HTTPError`.
            """
            session = FakeSession()
            client = build_client(self.mod, session)

            events = list(client.export_events(
                from_date=(UTC_TODAY - timedelta(days=6)).isoformat(),
                to_date=UTC_TODAY.isoformat(),
            ))

            self.assertEqual(events, [])
            self.assertEqual(
                session.export_params()[0]["to_date"], PROJECT_TODAY.isoformat()
            )

        def test_same_day_window_clamps_both_ends_and_stays_valid(self):
            """`from == to == the caller's today` — the `tenure_days=0` shape.

            Clamping only the upper end would leave `from=2026-08-05`,
            `to=2026-08-04`: an inverted window Mixpanel rejects just as loudly
            as the date it replaced. Both ends move.
            """
            session = FakeSession()
            client = build_client(self.mod, session)

            list(client.export_events(from_date=UTC_TODAY.isoformat(),
                                      to_date=UTC_TODAY.isoformat()))

            params = session.export_params()[0]
            self.assertEqual(params["from_date"], PROJECT_TODAY.isoformat())
            self.assertEqual(params["to_date"], PROJECT_TODAY.isoformat())
            self.assertLessEqual(params["from_date"], params["to_date"])

        def test_in_range_window_is_passed_through_untouched(self):
            """No over-clamping — the silent direction, and the dangerous one.

            An out-of-range date fails loudly; a window narrowed for no reason
            just returns fewer events and nothing says so.
            """
            session = FakeSession()
            client = build_client(self.mod, session)

            list(client.export_events(from_date="2026-07-29", to_date="2026-08-03"))

            self.assertEqual(
                session.export_windows()[0],
                {"from_date": "2026-07-29", "to_date": "2026-08-03"},
            )

        def test_date_objects_are_clamped_too(self):
            """A `date` is the natural thing to compute a window with.

            Comparing a `date` against a `YYYY-MM-DD` string would raise, or —
            worse, under a `str()`-first implementation — skip the clamp.
            """
            session = FakeSession()
            client = build_client(self.mod, session)

            list(client.export_events(from_date=date(2026, 7, 30), to_date=UTC_TODAY))

            self.assertEqual(
                session.export_windows()[0],
                {"from_date": "2026-07-30", "to_date": PROJECT_TODAY.isoformat()},
            )

        def test_find_events_by_uid_inherits_the_clamp(self):
            """The entry point `data:investigate-pmf-survey` actually calls.

            It is where the production `HTTPError` surfaced, so the clamp has
            to hold through the property-fallback chain, not just on the raw
            `export_events` call.
            """
            hit = {"event": "AI Conversation Session Started",
                   "properties": {"$distinct_id": "d1", "UserID": "uid-1"}}
            session = FakeSession(events=[hit])
            client = build_client(self.mod, session)

            events, prop = client.find_events_by_uid(
                "uid-1",
                from_date=UTC_TODAY - timedelta(days=6),
                to_date=UTC_TODAY,
            )

            self.assertEqual(events, [hit])
            self.assertEqual(prop, "UserID")
            self.assertTrue(session.export_params())
            for params in session.export_params():
                self.assertLessEqual(params["to_date"], PROJECT_TODAY.isoformat())

        def test_clamp_warns_on_stderr(self):
            """A narrowed window is invisible in the result, so it must announce itself."""
            session = FakeSession()
            client = build_client(self.mod, session)
            emitted: list[str] = []
            self.mod._warn = emitted.append

            client.clamp_window(UTC_TODAY, UTC_TODAY)

            self.assertEqual(len(emitted), 1)
            self.assertIn(PROJECT_TODAY.isoformat(), emitted[0])

        def test_no_warning_when_the_clamp_does_not_fire(self):
            session = FakeSession()
            client = build_client(self.mod, session)
            emitted: list[str] = []
            self.mod._warn = emitted.append

            client.clamp_window("2026-07-29", "2026-08-03")

            self.assertEqual(emitted, [])

        # -- resolving the project's calendar ---------------------------

        def test_project_timezone_is_read_from_mixpanel(self):
            """Asked, not guessed. A constant here cannot track a project setting."""
            session = FakeSession(timezone_name="US/Eastern", utc_offset=-240.0)
            client = build_client(self.mod, session, project_today=None)

            # The legacy name, or its canonical zone where the host lacks the legacy link.
            self.assertIn(str(client.project_timezone()), {"US/Eastern", "America/New_York"})
            self.assertEqual(len([u for u, _ in session.calls if "/api/app/me" in u]), 1)

        def test_project_timezone_is_resolved_once_per_process(self):
            session = FakeSession()
            client = build_client(self.mod, session, project_today=None)

            for _ in range(3):
                client.project_timezone()

            self.assertEqual(len([u for u, _ in session.calls if "/api/app/me" in u]), 1)

        def test_env_override_beats_the_api(self):
            os.environ["MIXPANEL_PROJECT_TIMEZONE"] = "UTC"
            session = FakeSession(timezone_name="US/Pacific")
            client = build_client(self.mod, session, project_today=None)

            self.assertEqual(str(client.project_timezone()), "UTC")
            self.assertEqual([u for u, _ in session.calls if "/api/app/me" in u], [])

        def test_unreachable_settings_fall_back_to_the_default_timezone(self):
            """An unreadable project setting is a reason to degrade, not to fail.

            The pull itself is still perfectly answerable; taking it down over a
            metadata read would be worse than using the documented default.
            """
            session = FakeSession(raise_on_settings=True)
            client = build_client(self.mod, session, project_today=None)

            self.assertIn(
                str(client.project_timezone()),
                {self.mod.DEFAULT_PROJECT_TIMEZONE, "America/Los_Angeles"},
            )

        def test_utc_offset_is_used_when_the_zone_name_is_unusable(self):
            """Hosts without a tz database still get the project's calendar.

            Slim containers ship no `tzdata`, so `ZoneInfo` raises on every
            name. The offset in the same payload is a live snapshot and is
            correct for "today" even though it cannot model a DST transition.
            """
            session = FakeSession(timezone_name="Not/AZone", utc_offset=-420.0)
            client = build_client(self.mod, session, project_today=None)

            resolved = client.project_timezone()
            self.assertEqual(
                resolved.utcoffset(datetime(2026, 8, 4, 12, 0)), timedelta(minutes=-420)
            )

        def test_legacy_us_names_resolve_without_the_legacy_links(self):
            """Ubuntu 26.04's tz database has no `US/*` links; the zone must still resolve.

            Mixpanel reports `US/Pacific`. Without the link, falling to the UTC
            offset would lose DST, so the canonical zone is used instead.
            """
            real = self.mod.ZoneInfo

            def no_legacy(name):
                if name.startswith("US/"):
                    raise KeyError(name)
                return real(name)

            self.mod.ZoneInfo = no_legacy
            try:
                session = FakeSession(timezone_name="US/Eastern", utc_offset=-240.0)
                client = build_client(self.mod, session, project_today=None)
                self.assertEqual(str(client.project_timezone()), "America/New_York")
                fallback = build_client(self.mod, FakeSession(raise_on_settings=True), project_today=None)
                self.assertEqual(str(fallback.project_timezone()), "America/Los_Angeles")
            finally:
                self.mod.ZoneInfo = real

        def test_project_today_uses_the_project_clock_not_utc(self):
            """The assertion the whole change rests on.

            Pinned to the production instant: 2026-08-05 01:20 UTC is still
            2026-08-04 in `US/Pacific`, which is the date Mixpanel accepts.
            """
            session = FakeSession(timezone_name="US/Pacific", utc_offset=-420.0)
            client = build_client(self.mod, session, project_today=None)
            instant = datetime(2026, 8, 5, 1, 20, tzinfo=timezone.utc)

            self.assertEqual(instant.date(), UTC_TODAY)
            self.assertEqual(instant.astimezone(client.project_timezone()).date(),
                             PROJECT_TODAY)

        def test_missing_creds_still_raise_mcp_fallback(self):
            """The clamp runs before the request — it must not mask the creds error.

            `_fetch_project_settings` touches `self._session`, which is `None`
            without creds; an unguarded read there would turn the documented
            `MCPFallback` into an `AttributeError`.
            """
            client = self.mod._MixpanelClient(username=None, secret=None, project_id=None)
            # `None` means "fall back to os.environ", so this is the assertion
            # that keeps a leaked credential from turning the call below into a
            # real, unfiltered, six-day Export.
            self.assertFalse(client.available, "test env is not scrubbed of Mixpanel creds")

            with self.assertRaises(self.mod.MCPFallback):
                list(client.export_events(from_date="2026-07-29", to_date="2026-08-03"))

    _Case.__name__ = f"MixpanelClamp_{path.parent.parent.name}"
    _Case.__qualname__ = _Case.__name__
    return _Case


def _event(name, *, insert_id, distinct_id="d1", time=1_754_900_000, **props):
    """One exported event in the shape the Export endpoint actually returns."""
    return {
        "event": name,
        "properties": {
            "$distinct_id": distinct_id,
            "$insert_id": insert_id,
            "time": time,
            **props,
        },
    }


def _day_block(day: int, n: int) -> list[dict]:
    """`n` distinct events that all belong to one day-shard.

    Shards are the unit the endpoint repeats, so the fixtures duplicate whole
    blocks rather than individual rows — a helper that happened to drop only
    *adjacent* repeats would pass a row-level fixture and fail in production,
    where the two copies of a block sit hundreds of rows apart.
    """
    return [
        _event(f"Event {day}.{i}", insert_id=f"iid-{day}-{i}", time=1_754_900_000 + day * 86_400 + i)
        for i in range(n)
    ]


def _dedupe_case(path):
    class _Case(unittest.TestCase):
        maxDiff = None

        def setUp(self):
            self.mod = load_reader(path, siblings=())
            self._saved_env = {k: os.environ.pop(k, None) for k in MIXPANEL_ENV_VARS}
            self.warnings: list[str] = []
            self.mod._warn = self.warnings.append

        def tearDown(self):
            for key, value in self._saved_env.items():
                os.environ.pop(key, None)
                if value is not None:
                    os.environ[key] = value

        def export(self, events, **kwargs):
            self.session = FakeSession(events=events)
            client = build_client(self.mod, self.session)
            return list(client.export_events(
                from_date=kwargs.get("from_date", "2026-07-29"),
                to_date=kwargs.get("to_date", "2026-08-03"),
            ))

        # -- the headline case ------------------------------------------

        def test_repeated_day_blocks_are_yielded_once(self):
            """The production shape: two whole day-shards, each emitted twice.

            Interleaved the way the real stream interleaves them — block A,
            block B, block B again, block A again — so the fixture reproduces
            the offsets seen on the wire (a repeat 249 rows after its original)
            rather than a convenient adjacent pair.
            """
            a, b = _day_block(1, 3), _day_block(2, 4)
            events = self.export(a + b + b + a)

            self.assertEqual(events, a + b)
            self.assertEqual(len(events), 7)

        def test_a_row_repeated_three_times_is_still_yielded_once(self):
            """Multiplicity is not fixed at 2 — 3x was seen in a real response.

            A `//2` correction, or a dedupe that only remembers the previous
            row, would land on 2 of these instead of 1.
            """
            block = _day_block(1, 5)
            events = self.export(block + block + block)

            self.assertEqual(events, block)

        def test_first_occurrence_wins_and_order_is_preserved(self):
            """Consumers read these as a time-ordered stream.

            Dropping the *first* copy of each row and keeping the last would
            deduplicate just as correctly by count while reordering the stream,
            which is a different wrong answer with the same row total.
            """
            a, b = _day_block(1, 2), _day_block(2, 2)
            events = self.export(a + b + a + b)

            self.assertEqual(events, a + b)

        # -- the negative cases: what must NOT be dropped ----------------

        def test_a_clean_response_is_passed_through_untouched(self):
            """No over-dedupe — the silent direction, and the dangerous one.

            A response with nothing to remove must come back byte-for-byte.
            Under-counting looks exactly as plausible as the 2x it replaced.
            """
            block = _day_block(1, 6)
            events = self.export(block)

            self.assertEqual(events, block)
            self.assertEqual(self.warnings, [])

        def test_distinct_event_names_sharing_an_insert_id_both_survive(self):
            """The assertion that rejects `$insert_id` as the key.

            A push SDK stamps one id on `Notification Delivered` and
            `Notification Received`; measured on a duplicate-free single-day
            export, 150 real events carried only 148 distinct ids. Keyed on the
            id alone this returns 1 event and silently loses the other.
            """
            shared = "225c6332-6188-4869-8956-04761607ebc5"
            delivered = _event("Notification Delivered", insert_id=shared)
            received = _event("Notification Received", insert_id=shared)

            events = self.export([delivered, received])

            self.assertEqual(events, [delivered, received])

        def test_same_id_and_name_on_different_users_both_survive(self):
            """`$distinct_id` completes the triple.

            Ids are client-generated, so two users' streams can collide on one.
            Dropping the second would delete a real event belonging to somebody
            else entirely.
            """
            mine = _event("AI Login", insert_id="b05837e0103bef8f", distinct_id="d1")
            theirs = _event("AI Login", insert_id="b05837e0103bef8f", distinct_id="d2")

            events = self.export([mine, theirs])

            self.assertEqual(events, [mine, theirs])

        def test_same_id_at_a_different_time_survives(self):
            """`time` is the fourth field Mixpanel dedupes on, so it is in the key.

            No group in the sampled corpus (7854 events) spanned more than one
            timestamp, so this case is not one that was observed — it is here
            because the corpus is 7 days against a 90-day default window, and a
            client reusing an id on the same event and user across two days is
            exactly what it is too short to rule out. Erring fine leaves a
            duplicate; erring coarse deletes an event.
            """
            first = _event("AI Login", insert_id="reused", time=1_754_900_000)
            later = _event("AI Login", insert_id="reused", time=1_755_000_000)

            events = self.export([first, later])

            self.assertEqual(events, [first, later])

        def test_events_without_an_insert_id_are_never_collapsed(self):
            """A missing property must not become data loss.

            Every event in the sampled corpus had an `$insert_id`, so this is
            the defensive branch — and the one where a plain
            `key = (event, distinct_id, insert_id)` with no guard would fold a
            user's entire run of one event type onto a single `None` key.
            """
            unkeyed = [
                {"event": "AI Login", "properties": {"$distinct_id": "d1", "time": 1_754_900_000 + i}}
                for i in range(4)
            ]

            events = self.export(unkeyed)

            self.assertEqual(events, unkeyed)

        def test_ingest_twins_differing_only_in_ingest_metadata_are_one_event(self):
            """The repeats are not always byte-identical.

            Mixpanel stamps `$mp_api_timestamp_ms` / `mp_processing_time_ms` at
            ingest, and the two copies of one logical event can differ there.
            A payload-equality dedupe keeps both; the triple collapses them,
            which is what makes the deduped per-day totals reconcile against an
            independent single-day export.
            """
            first = _event("AI Login", insert_id="b05837e0103bef8f",
                           **{"$mp_api_timestamp_ms": 1_754_900_100_000})
            twin = _event("AI Login", insert_id="b05837e0103bef8f",
                          **{"$mp_api_timestamp_ms": 1_754_900_900_000})

            events = self.export([first, twin])

            self.assertEqual(events, [first])

        # -- the drop has to be visible ---------------------------------

        def test_dropped_repeats_are_counted_on_stderr(self):
            """A silent dedupe replaces one invisible miscount with another.

            The duplication went unnoticed because nothing said the numbers had
            changed; removing it quietly would leave the next shift in export
            behaviour just as unobservable.
            """
            block = _day_block(1, 5)
            self.export(block + block)

            self.assertEqual(len(self.warnings), 1)
            self.assertIn("10", self.warnings[0])
            self.assertIn("5", self.warnings[0])

        # -- the entry point consumers actually call ---------------------

        def test_find_events_by_uid_returns_the_deduped_stream(self):
            """`data:profile-user` and the PMF cadence read through this.

            `export_events` is a generator, so a dedupe applied anywhere the
            caller re-iterates or bypasses would leave this path doubled while
            the direct call looked fixed.
            """
            block = _day_block(1, 4)
            for e in block:
                e["properties"]["UserID"] = "uid-1"
            session = FakeSession(events=block + block)
            client = build_client(self.mod, session)

            events, prop = client.find_events_by_uid(
                "uid-1", from_date="2026-07-29", to_date="2026-08-03",
            )

            self.assertEqual(events, block)
            self.assertEqual(prop, "UserID")

        def test_distinct_id_counts_are_not_inflated(self):
            """The doubling reached the derived aggregates, not just the list.

            `top_distinct_id` / `distinct_id_counts` feed the Engage re-query
            and the cross-device note in **Sources**; counting each event twice
            is exactly the plausible-looking wrongness this whole suite is for.
            """
            block = [_event(f"E{i}", insert_id=f"iid-{i}", distinct_id="d1") for i in range(3)]
            block.append(_event("E9", insert_id="iid-9", distinct_id="d2"))
            events = self.export(block + block)

            self.assertEqual(self.mod._MixpanelClient.distinct_id_counts(events),
                             [("d1", 3), ("d2", 1)])

    _Case.__name__ = f"MixpanelDedupe_{path.parent.parent.name}"
    _Case.__qualname__ = _Case.__name__
    return _Case


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(MixpanelClampTests))
    for path in MIXPANEL_PATHS:
        suite.addTests(loader.loadTestsFromTestCase(_clamp_case(path)))
        suite.addTests(loader.loadTestsFromTestCase(_dedupe_case(path)))
    return suite


if __name__ == "__main__":
    unittest.main()
