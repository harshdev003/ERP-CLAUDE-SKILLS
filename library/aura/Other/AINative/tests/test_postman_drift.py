"""eng:check-postman-drift compares each service's routes in code with the Postman collection.

Route extraction runs on real CoachService router files (`fixtures/postman_drift/CoachService`,
copied from its default branch); the collection side on a saved collection shape. Failure paths
are forced at their seams: no token, a 429 then success, a 401, and a repo that will not fetch.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import pathlib
import unittest
import unittest.mock
import urllib.error

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO / "plugins/eng/skills/check-postman-drift/scripts/drift.py"
FIX = pathlib.Path(__file__).resolve().parent / "fixtures/postman_drift"
COLLECTION = json.loads((FIX / "collection.json").read_text(encoding="utf-8"))


def load():
    spec = importlib.util.spec_from_file_location("drift", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


drift = load()


def fixture_reader(svc_dir):
    root = FIX / pathlib.Path(svc_dir).name

    def read(path):
        f = root / path
        return f.read_text(encoding="utf-8") if f.exists() else None
    return read


def postman_ok(key, path, **_):
    if path == "/collections":
        return {"collections": [{"name": "Aura Services", "uid": "1-abc"}, {"name": "Other", "uid": "2-def"}]}
    return {"collection": COLLECTION}


class RouteExtraction(unittest.TestCase):
    def setUp(self):
        self.unresolved = []
        self.routes = drift.routes(fixture_reader("CoachService"), unresolved=self.unresolved)
        self.by_path = {(m, p): a for m, p, a in self.routes}

    def test_the_notes_router_is_mounted_under_its_parents(self):
        self.assertIn(("POST", "/coaching/notes/list"), self.by_path)
        self.assertIn(("DELETE", "/coaching/notes/:coachingNoteId"), self.by_path)
        self.assertIn(("PUT", "/coaching/notes/:coachingNoteId"), self.by_path)
        self.assertIn(("POST", "/coaching/notes"), self.by_path)

    def test_probes_are_not_routes(self):
        for _, p, _ in self.routes:
            self.assertFalse(p.endswith("/ready") or p.endswith("/status") or p == "/", p)

    def test_auth_middleware_is_read(self):
        self.assertTrue(self.by_path[("PUT", "/coaching/notes/:coachingNoteId")])

    def test_a_mount_whose_file_is_absent_is_reported_not_dropped(self):
        # The fixture holds only the notes router; every other mount is listed as not followed.
        self.assertTrue(any("not found" in u for u in self.unresolved))

    def test_segments_owned_come_from_the_code(self):
        self.assertEqual(drift.owned_segments(self.routes), {"coaching"})


class Compare(unittest.TestCase):
    def setUp(self):
        code = {"CoachService": drift.routes(fixture_reader("CoachService"))}
        self.r = drift.compare(code, drift.requests_in(COLLECTION))
        self.c = self.r["CoachService"]

    def test_missing_extra_stale_and_deleted(self):
        self.assertEqual(self.c["missing"], ["DELETE /coaching/notes/:coachingNoteId"])
        self.assertEqual(self.c["extra"], ["GET /coaching/notes/{{note.id}}"])
        self.assertEqual(self.c["stale"], ["PUT /coaching/notes/:coachingNoteId (code needs auth; Postman sends none)"])
        self.assertEqual(self.c["deleted"], ["POST /coaching/notes/archive"])

    def test_a_deleted_request_is_never_extra(self):
        self.assertNotIn("POST /coaching/notes/archive", self.c["extra"])

    def test_a_request_no_service_mounts_is_unmapped(self):
        self.assertEqual(self.r["unmapped"], ["GET /quotes"])

    def test_the_report_names_routes_only(self):
        text = drift.markdown(self.r, [], {}, "2026-11-18")
        self.assertNotIn("user.token", text)
        self.assertIn("| CoachService | 1 | 1 | 1 | 1 |", text)


class DeletedFolders(unittest.TestCase):
    """Folders whose requests are all [DELETED]: the owner renames them, an admin deletes them."""

    def setUp(self):
        self.coll = json.loads((FIX / "collection_deleted_folders.json").read_text(encoding="utf-8"))
        self.f = drift.deleted_folders(self.coll)

    def test_fully_deleted_folders_are_listed_and_flagged_when_not_renamed(self):
        self.assertEqual(self.f, [
            {"folder": "Archive", "requests": 2, "renamed": False},
            {"folder": "[DELETED] Legacy", "requests": 1, "renamed": True},
            {"folder": "Notes / Drafts", "requests": 1, "renamed": False},
        ])

    def test_a_mixed_folder_and_an_empty_one_are_not_listed(self):
        names = [x["folder"] for x in self.f]
        self.assertNotIn("Notes", names)
        self.assertNotIn("Empty", names)

    def test_only_the_outermost_fully_deleted_folder_is_listed(self):
        self.assertNotIn("Archive / Old", [x["folder"] for x in self.f])

    def test_the_markdown_flags_the_unrenamed_ones(self):
        text = drift.markdown({"deleted_folders": self.f, "unmapped": []}, [], {}, "2026-11-19")
        self.assertIn("## Folders with every request [DELETED] (3)", text)
        self.assertIn("- `Archive` (2 requests) — **not renamed**", text)
        self.assertIn("- `[DELETED] Legacy` (1 request)\n", text)

    def test_run_reports_them_from_the_fetched_collection(self):
        def get(key, path, **_):
            if path == "/collections":
                return {"collections": [{"name": "Aura Services", "uid": "1-abc"}]}
            return {"collection": self.coll}
        r, stops, _ = drift.run(["CoachService"], str(FIX), "k", reader_for=fixture_reader, get=get,
                                date="2026-11-19")
        self.assertEqual(stops, [])
        self.assertEqual(r["deleted_folders"], self.f)
        self.assertNotIn("deleted_folders", drift.markdown(r, [], {}, "2026-11-19").split("## ")[0])


class Stops(unittest.TestCase):
    def run_(self, **seams):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = drift.main(["--services", "CoachService", "--services-dir", str(FIX)],
                              date="2026-11-18", reader_for=seams.pop("reader_for", fixture_reader), **seams)
        return code, out.getvalue()

    def test_a_clean_run_is_exit_0_with_no_followup(self):
        code, out = self.run_(key="k", get=postman_ok)
        self.assertEqual(code, 0)
        self.assertNotIn("FOLLOWUP", out)

    def test_no_token_is_a_token_stop(self):
        with unittest.mock.patch.object(drift, "load_key", return_value=""):
            code, out = self.run_(key="", get=postman_ok)
        self.assertEqual(code, 3)
        self.assertIn("FOLLOWUP stop token: POSTMAN_API_KEY is not set", out)

    def test_a_429_is_retried_then_succeeds(self):
        calls = []

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def urlopen(req, timeout):
            calls.append(req.full_url)
            if len(calls) == 1:
                raise urllib.error.HTTPError(req.full_url, 429, "Too Many", {"Retry-After": "0"}, None)
            return Resp(json.dumps({"collections": []}).encode())

        with unittest.mock.patch.object(drift.urllib.request, "urlopen", urlopen):
            out = drift.postman_get("k", "/collections", sleep=lambda s: None)
        self.assertEqual((len(calls), out), (2, {"collections": []}))

    def test_a_401_is_a_collection_stop(self):
        def get(key, path, **_):
            raise drift.Stop("collection", "GET /collections -> HTTP 401")
        code, out = self.run_(key="k", get=get)
        self.assertEqual(code, 3)
        self.assertIn("FOLLOWUP stop collection: GET /collections -> HTTP 401", out)

    def test_a_repo_that_will_not_fetch_stops_that_repo_and_the_run_goes_on(self):
        def reader_for(svc_dir):
            raise drift.Stop("repo", "git fetch failed: could not read from remote")
        code, out = self.run_(key="k", get=postman_ok, reader_for=reader_for)
        self.assertEqual(code, 3)
        self.assertIn("FOLLOWUP stop repo:coachservice: git fetch failed", out)

    def test_a_missing_services_dir_is_one_stop_not_thirteen_silent_failures(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = drift.main(["--services", "CoachService", "--services-dir", "/nonexistent/services"],
                              date="2026-11-18", key="k", get=postman_ok)
        self.assertEqual(code, 3)
        self.assertIn("FOLLOWUP stop services-dir: /nonexistent/services not found", out.getvalue())

    def test_a_fetch_failure_names_the_last_error_line(self):
        fake = unittest.mock.Mock(returncode=128, stderr="warning: x\nfatal: could not read from remote\n")
        with unittest.mock.patch.object(drift.subprocess, "run", return_value=fake):
            with self.assertRaises(drift.Stop) as cm:
                drift.git_reader("/repo")
        self.assertEqual(str(cm.exception), "git fetch failed: fatal: could not read from remote")

    def test_a_deleted_request_no_scanned_service_owns_is_still_listed(self):
        coll = {"item": [{"name": "[DELETED] Old", "request": {"method": "GET", "url": {"raw": "{{default.host}}/gone/x"}}}]}
        r = drift.compare({}, drift.requests_in(coll))
        self.assertEqual(r["unmapped"], ["[DELETED] GET /gone/x"])

    def test_unfollowed_mounts_are_kept_when_no_route_parses(self):
        def reader_for(svc_dir):
            return lambda path: "const x = require('./missing');\nrouter.use('/x', x);\n" if path == drift.ENTRY else None
        report, stops, unresolved = drift.run(["CoachService"], str(FIX), "k", reader_for=reader_for,
                                              get=postman_ok, date="2026-11-18")
        self.assertTrue(any("router:coachservice" in s for s in stops))
        self.assertTrue(unresolved["CoachService"])

    def test_a_worktree_copy_is_refused(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(drift.main(["--services", "PaymentService-wt-2234"]), 2)


if __name__ == "__main__":
    unittest.main()
