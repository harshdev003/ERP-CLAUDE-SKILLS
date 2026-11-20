"""Tests for the shared `bugsnag.py` helper, across every copy.

Two jobs, and the second is the one a `diff -q` cannot do:

1. Assert every copy is byte-identical to the canonical — the file-level guard.
2. Assert the *behaviours* that the copies had actually drifted on, loaded from
   each copy in turn. A copy can be byte-identical to the wrong canonical, so the
   behaviour assertions run per-copy rather than once.

The drifted behaviours were real defects, not style: one copy parsed `Retry-After`
with `int()`, which raises on the fractional values Bugsnag sends and takes the
429 retry loop down with it; the same copy let `raise_for_status()` escape as a
bare `HTTPError`, which crashes a fan-out over projects instead of degrading.

No network: the HTTP session is stubbed at its single chokepoint.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest

import os as _os
import tempfile as _tempfile

# bugsnag.py keeps a disk cache and a rate-limit cooldown file; never touch the real ones.
_os.environ["AINATIVE_BUGSNAG_CACHE_DIR"] = _tempfile.mkdtemp(prefix="bugsnag-test-")
_os.environ["BUGSNAG_CACHE"] = "0"

REPO = pathlib.Path(__file__).resolve().parents[1]
CANONICAL = REPO / "plugins/eng/skills/investigate-bugsnag-errors/scripts/bugsnag.py"
COPIES = sorted(REPO.glob("plugins/*/skills/*/scripts/bugsnag.py"))


def load(path: pathlib.Path):
    """Import one copy under a unique module name so copies don't collide."""
    name = f"bugsnag_{path.parts[-3].replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    # Register before exec: @dataclass resolves cls.__module__ through
    # sys.modules, and raises on a module that isn't there yet.
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class _Resp:
    """Minimal requests.Response stand-in."""

    def __init__(self, status_code=200, headers=None, payload=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._payload = payload if payload is not None else []

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code}")


class _Session:
    """Captures params and replays a scripted sequence of responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params or {}})
        return self.responses.pop(0) if self.responses else _Resp()

    def request(self, method, url, json=None, timeout=None):
        self.calls.append({"method": method, "url": url, "json": json})
        return self.responses.pop(0) if self.responses else _Resp()


def client(mod, responses=()):
    c = mod.BugsnagClient(token="t")
    c.session = _Session(responses)
    return c


# A trimmed `GET /projects/<id>/event_fields` body in the real shape. The three
# properties that matter: a typed field declares `match_types`, a flat one has
# `filter_options` with none, and a non-filterable field has no `filter_options`
# at all. `app.version` is deliberately absent — it is not a Bugsnag field.
EVENT_FIELDS = [
    {"display_id": "event", "pivot_options": {"filterable": False}},
    {"display_id": "event.since", "filter_options": {"name": "Since"}},
    {"display_id": "search", "filter_options": {"name": "Search"}},
    {"display_id": "user.id",
     "filter_options": {"name": "User ID", "match_types": ["eq", "ne", "empty"]}},
    {"display_id": "error",
     "filter_options": {"name": "Error ID", "match_types": ["eq", "ne"]}},
    {"display_id": "event.before", "filter_options": {"name": "Before"}},
    {"display_id": "app.release_stage",
     "filter_options": {"name": "Stage", "match_types": ["eq", "ne"]}},
    {"display_id": "version.seen_in",
     "filter_options": {"name": "Seen in", "match_types": ["eq", "ne", "empty"]}},
    {"display_id": "version.introduced_in",
     "filter_options": {"name": "Introduced in", "match_types": ["eq", "ne", "empty"]}},
]


def client_with_fields(mod, responses=()):
    """A client whose `event_fields` are already cached, so the scripted
    responses line up with the calls under test rather than the guard's fetch."""
    c = client(mod, responses)
    c._event_fields["proj"] = EVENT_FIELDS
    return c


class CopiesMatchTest(unittest.TestCase):
    def test_at_least_two_copies_exist(self) -> None:
        self.assertGreaterEqual(len(COPIES), 2, "expected several bugsnag.py copies")

    def test_every_copy_is_byte_identical_to_the_canonical(self) -> None:
        want = CANONICAL.read_bytes()
        for path in COPIES:
            with self.subTest(copy=str(path.relative_to(REPO))):
                self.assertEqual(path.read_bytes(), want)


class RetryAfterTest(unittest.TestCase):
    """`int("5.301")` raises — the defect one copy carried."""

    def test_fractional_retry_after_parses(self) -> None:
        for path in COPIES:
            mod = load(path)
            with self.subTest(copy=path.parts[-3]):
                self.assertAlmostEqual(mod._retry_after_seconds({"Retry-After": "5.301"}), 5.301)

    def test_missing_and_non_numeric_fall_back(self) -> None:
        for path in COPIES:
            mod = load(path)
            with self.subTest(copy=path.parts[-3]):
                self.assertEqual(mod._retry_after_seconds({}, default=5.0), 5.0)
                # the HTTP-date form is legal and must not raise
                self.assertEqual(
                    mod._retry_after_seconds({"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}, 7.0),
                    7.0,
                )



class _Clock:
    """Fake time for the rate-limit paths: sleep advances now."""

    def __init__(self, t=1_000_000.0):
        self.t, self.slept = t, []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(round(s, 3))
        self.t += s


def limited_client(mod, responses, clock, state_dir):
    """A client with a fresh state dir and a fake clock; returns (client, restore)."""
    old = (mod._now, mod._sleep, _os.environ.get("AINATIVE_BUGSNAG_CACHE_DIR"))
    mod._now, mod._sleep = clock.now, clock.sleep
    _os.environ["AINATIVE_BUGSNAG_CACHE_DIR"] = state_dir
    c = client(mod, responses)

    def restore():
        mod._now, mod._sleep = old[0], old[1]
        _os.environ["AINATIVE_BUGSNAG_CACHE_DIR"] = old[2]
    return c, restore


def _extend_in_child(path, state_dir, until):
    _os.environ["AINATIVE_BUGSNAG_CACHE_DIR"] = state_dir
    mod = load(pathlib.Path(path))
    mod.BugsnagClient(token="t")._extend_cooldown(until)


class RateLimitTest(unittest.TestCase):
    """One BUGSNAG_TOKEN is shared by every session, and Bugsnag limits per token (PROD-7824)."""

    def _run(self, mod, responses, call=lambda c: c._get("/projects/p/errors"), cooldown=None):
        clock = _Clock()
        with _tempfile.TemporaryDirectory() as d:
            c, restore = limited_client(mod, responses, clock, d)
            try:
                if cooldown is not None:
                    _os.makedirs(c._state, exist_ok=True)
                    with open(c._cooldown_path(), "w") as f:
                        f.write(str(clock.t + cooldown))
                try:
                    out, err = call(c), None
                except mod.BugsnagError as e:
                    out, err = None, e
                left = c._cooldown_until() - clock.t
            finally:
                restore()
        return out, err, clock, c, left

    def test_retry_after_is_honoured_and_shared(self) -> None:
        for path in COPIES:
            mod = load(path)
            with self.subTest(copy=path.parts[-3]):
                out, err, clock, c, _ = self._run(mod, [_Resp(429, {"Retry-After": "3"}), _Resp(200)])
                self.assertIsNone(err)
                self.assertEqual(clock.slept, [3.0])
                self.assertEqual(len(c.session.calls), 2)

    def test_no_retry_after_backs_off_doubling(self) -> None:
        for path in COPIES:
            mod = load(path)
            with self.subTest(copy=path.parts[-3]):
                _, err, clock, _, _ = self._run(mod, [_Resp(429)] * 3 + [_Resp(200)])
                self.assertIsNone(err)
                self.assertEqual(clock.slept, [5.0, 10.0, 20.0])

    def test_past_the_budget_it_raises_rate_limited_not_a_generic_failure(self) -> None:
        for path in COPIES:
            mod = load(path)
            with self.subTest(copy=path.parts[-3]):
                _, err, clock, _, _ = self._run(mod, [_Resp(429, {"Retry-After": "120"})] * 10)
                self.assertIsInstance(err, mod.BugsnagRateLimited)
                self.assertIsInstance(err, mod.BugsnagError)  # fan-outs still degrade
                self.assertGreater(err.retry_after, 0)
                self.assertLessEqual(sum(clock.slept), mod.RATE_LIMIT_BUDGET_S)
                self.assertIn("rate-limited", str(err))

    def test_a_long_retry_after_is_shared_in_full_not_capped(self) -> None:
        for path in COPIES:
            mod = load(path)
            with self.subTest(copy=path.parts[-3]):
                _, err, clock, _, left = self._run(mod, [_Resp(429, {"Retry-After": "900"})])
                self.assertIsInstance(err, mod.BugsnagRateLimited)
                self.assertEqual(clock.slept, [])          # past the budget: no sleep
                self.assertAlmostEqual(left, 900.0)        # others wait the full 900 s
                self.assertAlmostEqual(err.retry_after, 900.0)

    def test_a_zero_or_negative_retry_after_cannot_busy_loop(self) -> None:
        for path in COPIES:
            mod = load(path)
            with self.subTest(copy=path.parts[-3]):
                for ra in ("0", "-5"):
                    _, err, clock, c, _ = self._run(mod, [_Resp(429, {"Retry-After": ra})] * 100)
                    self.assertIsInstance(err, mod.BugsnagRateLimited)
                    self.assertTrue(all(s >= mod.MIN_BACKOFF_S for s in clock.slept))
                    self.assertLessEqual(len(c.session.calls), mod.MAX_RATE_LIMITED + 1)

    def test_the_fallback_backoff_is_capped(self) -> None:
        mod = load(CANONICAL)
        self.assertEqual(mod._backoff_seconds({}, 10), mod.BACKOFF_MAX_S)
        self.assertEqual(mod._backoff_seconds({"Retry-After": "600"}, 0), 600.0)

    def test_a_cooldown_left_by_another_process_is_waited_out_first(self) -> None:
        for path in COPIES:
            mod = load(path)
            with self.subTest(copy=path.parts[-3]):
                _, err, clock, c, _ = self._run(mod, [_Resp(200)], cooldown=40)
                self.assertIsNone(err)
                self.assertEqual(clock.slept, [40.0])
                self.assertEqual(len(c.session.calls), 1)

    def test_a_cooldown_past_the_budget_sends_nothing(self) -> None:
        for path in COPIES:
            mod = load(path)
            with self.subTest(copy=path.parts[-3]):
                _, err, clock, c, _ = self._run(mod, [_Resp(200)], cooldown=1200)
                self.assertIsInstance(err, mod.BugsnagRateLimited)
                self.assertEqual(c.session.calls, [])
                self.assertEqual(clock.slept, [])

    def test_a_429_writes_the_cooldown_for_others(self) -> None:
        mod = load(CANONICAL)
        clock = _Clock()
        with _tempfile.TemporaryDirectory() as d:
            c, restore = limited_client(mod, [_Resp(429, {"Retry-After": "30"}), _Resp(200)], clock, d)
            try:
                written = []
                real = mod._write_private
                mod._write_private = lambda p, t: (written.append(float(t)), real(p, t))
                try:
                    c._get("/x")
                finally:
                    mod._write_private = real
                self.assertEqual(written, [1_000_030.0])
                self.assertEqual(_os.stat(c._cooldown_path()).st_mode & 0o777, 0o600)
            finally:
                restore()

    def test_a_shorter_wait_never_replaces_a_longer_cooldown(self) -> None:
        mod = load(CANONICAL)
        clock = _Clock()
        with _tempfile.TemporaryDirectory() as d:
            c, restore = limited_client(mod, [], clock, d)
            try:
                c._extend_cooldown(clock.t + 900)
                c._extend_cooldown(clock.t + 30)
                self.assertAlmostEqual(c._cooldown_until() - clock.t, 900.0)
            finally:
                restore()

    def test_concurrent_extends_keep_the_longest(self) -> None:
        # Real processes racing on the same state dir; the longest wait must win.
        import multiprocessing as mp
        mod = load(CANONICAL)
        old = _os.environ.get("AINATIVE_BUGSNAG_CACHE_DIR")
        with _tempfile.TemporaryDirectory() as d:
            _os.environ["AINATIVE_BUGSNAG_CACHE_DIR"] = d
            try:
                state = mod._state_dir("t")
                waits = [5, 900, 10, 20, 15, 30, 25, 40]
                ctx = mp.get_context("fork")
                procs = [ctx.Process(target=_extend_in_child, args=(str(CANONICAL), d, 1_000_000 + w))
                         for w in waits * 5]
                for pr in procs:
                    pr.start()
                for pr in procs:
                    pr.join(30)
                with open(_os.path.join(state, "cooldown-until")) as f:
                    self.assertEqual(float(f.read()), 1_000_900.0)
            finally:
                _os.environ["AINATIVE_BUGSNAG_CACHE_DIR"] = old

    def test_an_unwritable_cache_dir_never_blocks_a_read(self) -> None:
        for path in COPIES:
            mod = load(path)
            with self.subTest(copy=path.parts[-3]), _tempfile.TemporaryDirectory() as d:
                blocker = _os.path.join(d, "file")
                open(blocker, "w").close()  # a file where the folder should go
                old = _os.environ.get("AINATIVE_BUGSNAG_CACHE_DIR"), _os.environ.get("BUGSNAG_CACHE")
                _os.environ["AINATIVE_BUGSNAG_CACHE_DIR"] = blocker
                _os.environ["BUGSNAG_CACHE"] = "1"
                try:
                    c = client(mod, [_Resp(429, {"Retry-After": "1"}), _Resp(200, payload=[{"slug": "aura-health", "id": "o"}])])
                    real_sleep = mod._sleep
                    mod._sleep = lambda s: None
                    try:
                        self.assertIsNone(c._state)
                        self.assertEqual(c.find_org_id(), "o")
                    finally:
                        mod._sleep = real_sleep
                finally:
                    _os.environ["AINATIVE_BUGSNAG_CACHE_DIR"], _os.environ["BUGSNAG_CACHE"] = old

    def test_writes_back_off_too(self) -> None:
        mod = load(CANONICAL)
        _, err, clock, _, _ = self._run(
            mod, [_Resp(429, {"Retry-After": "2"}), _Resp(200)],
            call=lambda c: c._write("PATCH", "/errors/e", {}))
        self.assertIsNone(err)
        self.assertEqual(clock.slept, [2.0])


class LookupCacheTest(unittest.TestCase):
    """Org, projects and event fields are cached on disk for a day; error lists never."""

    def _with_cache(self, fn):
        mod = load(CANONICAL)
        clock = _Clock()
        old = _os.environ.get("BUGSNAG_CACHE")
        _os.environ["BUGSNAG_CACHE"] = "1"
        try:
            with _tempfile.TemporaryDirectory() as d:
                return fn(mod, clock, d)
        finally:
            _os.environ["BUGSNAG_CACHE"] = old

    def test_a_second_client_reads_the_org_from_disk(self) -> None:
        def go(mod, clock, d):
            orgs = [{"slug": "aura-health", "id": "o1"}]
            a, ra = limited_client(mod, [_Resp(200, payload=orgs)], clock, d)
            try:
                self.assertEqual(a.find_org_id(), "o1")
                b = client(mod, [])
                self.assertEqual(b.find_org_id(), "o1")
                self.assertEqual(b.session.calls, [])
                files = [f for f in _os.listdir(a._state) if f.endswith(".json")]
                self.assertEqual(len(files), 1)
                self.assertEqual(_os.stat(_os.path.join(a._state, files[0])).st_mode & 0o777, 0o600)
            finally:
                ra()
        self._with_cache(go)

    def test_an_expired_entry_is_fetched_again(self) -> None:
        def go(mod, clock, d):
            orgs = [{"slug": "aura-health", "id": "o1"}]
            a, ra = limited_client(mod, [_Resp(200, payload=orgs), _Resp(200, payload=orgs)], clock, d)
            try:
                a.list_organizations()
                clock.t += mod.CACHE_TTL_S + 1
                for f in _os.listdir(a._state):
                    _os.utime(_os.path.join(a._state, f), (0, 0))
                a.list_organizations()
                self.assertEqual(len(a.session.calls), 2)
            finally:
                ra()
        self._with_cache(go)

    def test_cache_off_fetches_every_time(self) -> None:
        mod = load(CANONICAL)
        c = client(mod, [_Resp(200, payload=[]), _Resp(200, payload=[])])
        c.list_organizations()
        c.list_organizations()
        self.assertEqual(len(c.session.calls), 2)

    def test_error_lists_are_never_cached(self) -> None:
        def go(mod, clock, d):
            a, ra = limited_client(mod, [_Resp(200, payload=[]), _Resp(200, payload=[])], clock, d)
            try:
                list(a._paginate("/projects/p/errors"))
                list(a._paginate("/projects/p/errors"))
                self.assertEqual(len(a.session.calls), 2)
            finally:
                ra()
        self._with_cache(go)

class ErrorWrappingTest(unittest.TestCase):
    """A fan-out over projects must degrade on one bad project, not crash."""

    def test_5xx_is_wrapped_as_bugsnag_error(self) -> None:
        for path in COPIES:
            mod = load(path)
            with self.subTest(copy=path.parts[-3]):
                c = client(mod, [_Resp(status_code=500)])
                with self.assertRaises(mod.BugsnagError):
                    c._get("/projects/p/errors")

    def test_401_and_404_are_wrapped_too(self) -> None:
        for path in COPIES:
            mod = load(path)
            with self.subTest(copy=path.parts[-3]):
                for code in (401, 404):
                    c = client(mod, [_Resp(status_code=code)])
                    with self.assertRaises(mod.BugsnagError):
                        c._get("/projects/p/errors")


class FilterShapeTest(unittest.TestCase):
    """The two Bugsnag filter shapes — now derived from the field's match_types."""

    def test_event_since_is_flat_and_user_id_is_array_shaped(self) -> None:
        for path in COPIES:
            mod = load(path)
            with self.subTest(copy=path.parts[-3]):
                c = client_with_fields(mod, [_Resp(payload=[])])
                c.list_errors("proj", since="24h", user_id="uid123")
                params = c.session.calls[0]["params"]
                self.assertEqual(params["filters[event.since]"], "24h")
                self.assertEqual(params["filters[user.id][][type]"], "eq")
                self.assertEqual(params["filters[user.id][][value]"], "uid123")

    def test_user_id_is_omitted_when_not_given(self) -> None:
        for path in COPIES:
            mod = load(path)
            with self.subTest(copy=path.parts[-3]):
                c = client_with_fields(mod, [_Resp(payload=[])])
                c.list_errors("proj", since="24h")
                self.assertNotIn("filters[user.id][][type]", c.session.calls[0]["params"])

    def test_shape_follows_match_types_not_a_hardcoded_field_list(self) -> None:
        # `search` is flat for the same reason `event.since` is: no match_types.
        for path in COPIES:
            mod = load(path)
            with self.subTest(copy=path.parts[-3]):
                c = client_with_fields(mod)
                got = c.build_filters("proj", {"search": "crash", "error": "e1"})
                self.assertEqual(got["filters[search]"], "crash")
                self.assertEqual(got["filters[error][][type]"], "eq")
                self.assertEqual(got["filters[error][][value]"], "e1")


class FilterGuardTest(unittest.TestCase):
    """Bugsnag drops an unknown `filters[...]` key and returns 200 with the
    UNFILTERED result set. Nothing about the response distinguishes that from a
    real answer, so the guard is the only place it can be caught.

    The load-bearing assertions here are the negative ones. A happy-path test
    against a valid filter passed throughout the period this was broken.
    """

    def test_unknown_key_raises_and_never_reaches_the_network(self) -> None:
        for path in COPIES:
            mod = load(path)
            with self.subTest(copy=path.parts[-3]):
                c = client_with_fields(mod, [_Resp(payload=[{"id": "x"}])])
                with self.assertRaises(mod.BugsnagError):
                    c.list_errors("proj", filters={"app.versionn": "1.29.0"})
                self.assertEqual(c.session.calls, [],
                                 "an unknown key must not be sent at all")

    def test_the_two_myth_fields_are_refused_by_name(self) -> None:
        # Both read like the obvious way to filter by version and are not fields.
        for path in COPIES:
            mod = load(path)
            for bad in ("app.version", "event.app_version"):
                with self.subTest(copy=path.parts[-3], field=bad):
                    c = client_with_fields(mod)
                    with self.assertRaises(mod.BugsnagError) as ctx:
                        c.build_filters("proj", {bad: "1.29.0"})
                    self.assertIn("version.seen_in", str(ctx.exception),
                                  "the error should name the field that works")

    def test_a_valid_filter_is_still_sent(self) -> None:
        # A guard that quietly dropped filters would pass every test above.
        for path in COPIES:
            mod = load(path)
            with self.subTest(copy=path.parts[-3]):
                c = client_with_fields(mod, [_Resp(payload=[])])
                c.list_errors("proj", filters={"version.seen_in": "1.29.0"})
                params = c.session.calls[0]["params"]
                self.assertEqual(params["filters[version.seen_in][][type]"], "eq")
                self.assertEqual(params["filters[version.seen_in][][value]"], "1.29.0")

    def test_a_bogus_but_valid_field_value_returns_empty_not_everything(self) -> None:
        # The probe that settles it: a value that cannot match. Comparing a
        # filtered result against an unfiltered one cannot tell "filter ignored"
        # from "filter scopes groups only" — both show identical counts.
        for path in COPIES:
            mod = load(path)
            with self.subTest(copy=path.parts[-3]):
                c = client_with_fields(mod, [_Resp(payload=[])])
                got = c.list_errors("proj", filters={"version.seen_in": "99.99.99"})
                self.assertEqual(got, [])
                self.assertEqual(
                    c.session.calls[0]["params"]["filters[version.seen_in][][value]"],
                    "99.99.99",
                    "the impossible value must actually reach the wire",
                )

    def test_repeated_values_are_refused_rather_than_silently_last_winning(self) -> None:
        # Repeating a filter pair does not OR in Bugsnag — it keeps the last
        # value and returns 200. A multi-value ask must fan out and union.
        for path in COPIES:
            mod = load(path)
            for many in (["1.29.0", "1.28.0"], ("1.29.0", "1.28.0")):
                with self.subTest(copy=path.parts[-3], form=type(many).__name__):
                    c = client_with_fields(mod, [_Resp(payload=[])])
                    with self.assertRaises(mod.BugsnagError):
                        c.list_errors("proj", filters={"version.seen_in": many})
                    self.assertEqual(c.session.calls, [])

    def test_the_typed_form_cannot_smuggle_a_list_past_the_guard(self) -> None:
        # {"type": ..., "value": [...]} is the documented typed form with a list
        # in it. Checking only the top-level value lets it through, `requests`
        # repeats the key, and Bugsnag keeps the last one — the same silent
        # last-wins the bare-list case is refused for.
        for path in COPIES:
            mod = load(path)
            with self.subTest(copy=path.parts[-3]):
                c = client_with_fields(mod, [_Resp(payload=[])])
                with self.assertRaises(mod.BugsnagError):
                    c.list_errors("proj", filters={
                        "version.seen_in": {"type": "eq",
                                            "value": ["1.29.0", "1.28.0"]}})
                self.assertEqual(c.session.calls, [])

    def test_unsupported_match_type_is_refused_locally(self) -> None:
        # `gte` on version.seen_in is an HTTP 400 upstream; catch it here with a
        # message that says what to do instead.
        for path in COPIES:
            mod = load(path)
            with self.subTest(copy=path.parts[-3]):
                c = client_with_fields(mod)
                with self.assertRaises(mod.BugsnagError):
                    c.build_filters(
                        "proj", {"version.seen_in": {"type": "gte", "value": "1.29.0"}})
                # a supported one goes through
                got = c.build_filters(
                    "proj", {"version.seen_in": {"type": "ne", "value": "1.29.0"}})
                self.assertEqual(got["filters[version.seen_in][][type]"], "ne")

    def test_a_non_filterable_field_is_refused(self) -> None:
        # `event` appears in event_fields but carries no filter_options.
        for path in COPIES:
            mod = load(path)
            with self.subTest(copy=path.parts[-3]):
                c = client_with_fields(mod)
                with self.assertRaises(mod.BugsnagError):
                    c.build_filters("proj", {"event": "1"})

    def test_event_fields_is_fetched_once_and_cached_per_project(self) -> None:
        for path in COPIES:
            mod = load(path)
            with self.subTest(copy=path.parts[-3]):
                c = client(mod, [_Resp(payload=EVENT_FIELDS),
                                 _Resp(payload=[]), _Resp(payload=[])])
                c.list_errors("proj", since="24h")
                c.list_errors("proj", since="24h")
                fetches = [k for k in c.session.calls
                           if k["url"].endswith("/event_fields")]
                self.assertEqual(len(fetches), 1)

    def test_an_unreadable_event_fields_fails_closed(self) -> None:
        # Failing open would restore the silent-widening this guard exists to
        # remove. BugsnagError is what fan-outs already catch and degrade on.
        for path in COPIES:
            mod = load(path)
            with self.subTest(copy=path.parts[-3]):
                c = client(mod, [_Resp(status_code=500)])
                with self.assertRaises(mod.BugsnagError):
                    c.list_errors("proj", since="24h")


class ReleaseScopingTest(unittest.TestCase):
    """Release scoping, and the union that `>=` has to become.

    Bugsnag has no OR and no range on the version fields, so a multi-version ask
    is N requests merged here. The assertions that matter are the ones a
    single-version happy path cannot make: that a bogus version does not fall
    back to everything, and that the merge does not double-count.
    """

    @staticmethod
    def _row(err_id, events, users, **extra):
        row = {"id": err_id, "events": events, "users": users}
        row.update(extra)
        return row

    def test_mode_picks_the_field_and_they_are_different_questions(self) -> None:
        for path in COPIES:
            mod = load(path)
            with self.subTest(copy=path.parts[-3]):
                self.assertEqual(mod.RELEASE_MODES["seen-in"], "version.seen_in")
                self.assertEqual(mod.RELEASE_MODES["introduced-in"],
                                 "version.introduced_in")
                for mode, field in mod.RELEASE_MODES.items():
                    c = client_with_fields(mod, [_Resp(payload=[])])
                    c.list_errors_for_releases("proj", ["1.29.0"], mode=mode)
                    self.assertEqual(
                        c.session.calls[0]["params"][f"filters[{field}][][value]"],
                        "1.29.0")

    def test_an_unknown_mode_is_refused_before_any_request(self) -> None:
        for path in COPIES:
            mod = load(path)
            with self.subTest(copy=path.parts[-3]):
                c = client_with_fields(mod, [_Resp(payload=[])])
                with self.assertRaises(mod.BugsnagError):
                    c.list_errors_for_releases("proj", ["1.29.0"], mode="seen_in")
                self.assertEqual(c.session.calls, [])

    def test_a_bogus_version_returns_empty_not_the_unfiltered_set(self) -> None:
        # The failure this whole card exists for: a version that cannot match
        # must not fall back to every error in the project.
        for path in COPIES:
            mod = load(path)
            with self.subTest(copy=path.parts[-3]):
                c = client_with_fields(mod, [_Resp(payload=[])])
                got = c.list_errors_for_releases("proj", ["99.99.99"])
                self.assertEqual(got, [])
                params = c.session.calls[0]["params"]
                self.assertEqual(params["filters[version.seen_in][][value]"],
                                 "99.99.99")

    def test_each_version_is_its_own_request(self) -> None:
        for path in COPIES:
            mod = load(path)
            with self.subTest(copy=path.parts[-3]):
                c = client_with_fields(mod, [_Resp(payload=[]), _Resp(payload=[])])
                c.list_errors_for_releases("proj", ["1.29.0", "1.28.0"])
                sent = [k["params"]["filters[version.seen_in][][value]"]
                        for k in c.session.calls]
                self.assertEqual(sent, ["1.29.0", "1.28.0"],
                                 "no single query can express both")

    def test_union_dedupes_by_error_id_and_sums_events(self) -> None:
        for path in COPIES:
            mod = load(path)
            with self.subTest(copy=path.parts[-3]):
                c = client_with_fields(mod, [
                    _Resp(payload=[self._row("a", 11, 3), self._row("b", 5, 2)]),
                    _Resp(payload=[self._row("a", 7, 1), self._row("c", 4, 4)]),
                ])
                got = c.list_errors_for_releases(
                    "proj", ["1.29.0", "1.28.0"], max_results=10)
                by_id = {r["id"]: r for r in got}
                self.assertEqual(sorted(by_id), ["a", "b", "c"],
                                 "the shared row must appear once, not twice")
                # events partition by version, so they add
                self.assertEqual(by_id["a"]["events"], 18)
                self.assertEqual(by_id["a"]["events_by_version"],
                                 {"1.29.0": 11, "1.28.0": 7})
                self.assertEqual(by_id["a"]["release_versions"], ["1.29.0", "1.28.0"])

    def test_users_is_a_flagged_max_never_a_sum(self) -> None:
        # One person on two versions is one user, and separate queries cannot
        # tell that from two people. Summing would overstate; the flag is what
        # stops a renderer implying precision it doesn't have.
        for path in COPIES:
            mod = load(path)
            with self.subTest(copy=path.parts[-3]):
                c = client_with_fields(mod, [
                    _Resp(payload=[self._row("a", 11, 3)]),
                    _Resp(payload=[self._row("a", 7, 1)]),
                ])
                got = c.list_errors_for_releases("proj", ["1.29.0", "1.28.0"])
                self.assertEqual(got[0]["users"], 3)
                self.assertNotEqual(got[0]["users"], 4, "must not sum users")
                self.assertTrue(got[0]["users_is_lower_bound"])

    def test_a_single_version_row_is_not_flagged_a_lower_bound(self) -> None:
        for path in COPIES:
            mod = load(path)
            with self.subTest(copy=path.parts[-3]):
                c = client_with_fields(mod, [_Resp(payload=[self._row("a", 11, 3)])])
                got = c.list_errors_for_releases("proj", ["1.29.0"])
                self.assertFalse(got[0]["users_is_lower_bound"])

    def test_a_repeated_version_is_queried_once(self) -> None:
        # The releases endpoint can report one app_version across several
        # release records; querying it twice would double its own event counts.
        for path in COPIES:
            mod = load(path)
            with self.subTest(copy=path.parts[-3]):
                c = client_with_fields(mod, [_Resp(payload=[self._row("a", 11, 3)])])
                got = c.list_errors_for_releases("proj", ["1.29.0", "1.29.0"])
                self.assertEqual(len(c.session.calls), 1)
                self.assertEqual(got[0]["events"], 11)

    def test_first_and_last_seen_span_the_versions(self) -> None:
        for path in COPIES:
            mod = load(path)
            with self.subTest(copy=path.parts[-3]):
                c = client_with_fields(mod, [
                    _Resp(payload=[self._row("a", 1, 1,
                                             first_seen="2026-08-20T00:00:00.000Z",
                                             last_seen="2026-09-01T00:00:00.000Z")]),
                    _Resp(payload=[self._row("a", 1, 1,
                                             first_seen="2026-07-01T00:00:00.000Z",
                                             last_seen="2026-08-25T00:00:00.000Z")]),
                ])
                got = c.list_errors_for_releases("proj", ["1.29.0", "1.28.0"])
                self.assertEqual(got[0]["first_seen"], "2026-07-01T00:00:00.000Z")
                self.assertEqual(got[0]["last_seen"], "2026-09-01T00:00:00.000Z")

    def test_no_versions_is_refused(self) -> None:
        for path in COPIES:
            mod = load(path)
            with self.subTest(copy=path.parts[-3]):
                c = client_with_fields(mod, [_Resp(payload=[])])
                with self.assertRaises(mod.BugsnagError):
                    c.list_errors_for_releases("proj", [])
                self.assertEqual(c.session.calls, [])


class ReleaseWalkDepthTest(unittest.TestCase):
    """`max_results` must not silently exceed the page budget.

    The releases endpoint caps per_page at 10. With a hardcoded 10-page walk,
    asking for 200 records quietly returned at most 100 — and a version past
    that boundary reads as a version that does not exist, which is a confident
    wrong answer rather than an error.
    """

    @staticmethod
    def _page(n, start):
        return [{"app_version": f"9.{start + i}.0"} for i in range(n)]

    def test_the_walk_reaches_max_results_beyond_ten_pages(self) -> None:
        for path in COPIES:
            mod = load(path)
            with self.subTest(copy=path.parts[-3]):
                pages = [
                    _Resp(payload=self._page(10, i * 10),
                          headers={"Link": '<https://api.bugsnag.com/next>; rel="next"'})
                    for i in range(20)
                ]
                c = client(mod, pages)
                got = c.list_releases("proj", per_page=10, max_results=200)
                self.assertEqual(len(got), 200)

    def test_it_still_stops_at_max_results(self) -> None:
        for path in COPIES:
            mod = load(path)
            with self.subTest(copy=path.parts[-3]):
                pages = [
                    _Resp(payload=self._page(10, i * 10),
                          headers={"Link": '<https://api.bugsnag.com/next>; rel="next"'})
                    for i in range(20)
                ]
                c = client(mod, pages)
                got = c.list_releases("proj", per_page=10, max_results=25)
                self.assertEqual(len(got), 25)
                self.assertLessEqual(len(c.session.calls), 3,
                                     "must not keep paging past what was asked for")


class MutationGuardTest(unittest.TestCase):
    """The write half is shared, so its guard must hold in every copy."""

    def test_unsupported_operation_is_refused_before_any_request(self) -> None:
        for path in COPIES:
            mod = load(path)
            with self.subTest(copy=path.parts[-3]):
                c = client(mod, [_Resp()])
                with self.assertRaises(mod.BugsnagError):
                    c.update_error("err", "delete")
                self.assertEqual(c.session.calls, [], "must not reach the network")

    def test_supported_operations_are_the_three_everyday_ones(self) -> None:
        for path in COPIES:
            mod = load(path)
            with self.subTest(copy=path.parts[-3]):
                self.assertEqual(set(mod.BugsnagClient.OPERATIONS), {"fix", "open", "ignore"})


class UrlParseTest(unittest.TestCase):
    def test_parses_project_and_error_from_a_bugsnag_url(self) -> None:
        url = "https://app.bugsnag.com/aura-health/ai-aurie-ios/errors/691798b39d8c847ab990538e"
        for path in COPIES:
            mod = load(path)
            with self.subTest(copy=path.parts[-3]):
                got = mod.parse_bugsnag_url(url)
                self.assertEqual(got["project"], "ai-aurie-ios")
                self.assertEqual(got["error_id"], "691798b39d8c847ab990538e")
                self.assertEqual(got["org"], "aura-health")
                self.assertIsNone(got["event_id"])


if __name__ == "__main__":
    unittest.main()
