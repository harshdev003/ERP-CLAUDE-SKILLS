"""eng:update-postman-collection: one request per run, per-item endpoints only, read back after."""

from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "postman_skill", REPO / "plugins/eng/skills/update-postman-collection/scripts/postman.py")
pm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pm)

CID = "col-1"
SPEC = {
    "folder": "Communities/Subscriptions/Retrieve",
    "method": "GET",
    "path": "/communities/subscriptions/:communityId",
    "path_variables": {"communityId": "{{community.id}}"},
    "description": "Returns the caller's own subscription.",
    "status_codes": {"200": "Their row", "404": "No subscription", "400": "Not a UUID"},
    "responses": [{"name": "200 OK", "code": 200, "body": {"userId": "{{user.id}}"}}],
}


def tree():
    return [{"name": "Communities", "id": "f-com", "item": [
        {"name": "Subscriptions", "id": "f-sub", "item": [
            {"name": "Retrieve", "id": "f-ret", "item": [
                {"name": "/communities/subscriptions/:userId/:coachId", "id": "r-old",
                 "request": {"method": "GET", "url": {"raw": "{{default.host}}/communities/subscriptions/:userId/:communityId"}},
                 "response": []}]}]}]},
        {"name": "Users", "id": "f-users", "item": [
            {"name": "Authenticate", "id": "f-auth", "item": [
                {"name": "/users/verifyToken", "id": "r-vt",
                 "request": {"method": "POST", "url": {"raw": "{{default.host}}/users/verifyToken"}},
                 "response": [{"name": "200 OK", "id": "resp-1"}]}]}]}]


class FakePostman:
    """Stands in for the HTTP seam; stores what was written so read-back sees it."""

    def __init__(self, items=None):
        self.items = items if items is not None else tree()
        self.calls = []
        self.requests = {"r-vt": {"name": "/users/verifyToken", "method": "POST",
                                  "url": "{{default.host}}/users/verifyToken", "description": "old"}}
        self.responses = {"resp-1": {"name": "200 OK", "responseCode": {"code": 200}, "text": "{}"}}
        self.corrupt = False
        self.fail_on = None

    def call(self, method, path, body=None):
        self.calls.append((method, path.split("?")[0], body))
        if self.fail_on and self.fail_on == (method, path.split("?")[0]):
            raise pm.PostmanError("boom")
        if path == "/collections":
            return {"collections": [{"name": "Aura Services", "id": CID}]}
        if method == "GET" and path == f"/collections/{CID}":
            return {"collection": {"item": copy.deepcopy(self.items)}}
        parts = path.split("?")[0].split("/")
        kind, rid = parts[3], (parts[4] if len(parts) > 4 else None)
        store = self.requests if kind == "requests" else self.responses
        if method == "GET":
            return {"data": copy.deepcopy(store[rid])}
        if method == "POST":
            new = f"new-{kind}-{len(store)}"
            if kind != "folders":
                store[new] = copy.deepcopy(body)
            return {"data": {"id": new}}
        if method == "PUT":
            store[rid] = {**store[rid], **copy.deepcopy(body)}
            if self.corrupt:
                store[rid]["description"] = "mangled"
            return {"data": {"id": rid}}
        raise AssertionError(f"unexpected {method} {path}")


def run_main(args, env=None):
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.dict(os.environ, env or {}, clear=False), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
            mock.patch("sys.argv", ["postman.py", *args]):
        code = pm.main()
    return code, out.getvalue(), err.getvalue()


def spec_file(spec):
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(spec, f, ensure_ascii=False)
    f.close()
    return f.name


class TestBuild(unittest.TestCase):
    def test_status_codes_render_as_a_sorted_table_under_the_description(self):
        req, _ = pm.build(SPEC)
        self.assertIn("## Status codes", req["description"])
        rows = [ln for ln in req["description"].splitlines() if ln.startswith("| 4") or ln.startswith("| 2")]
        self.assertEqual([r.split("|")[1].strip() for r in rows], ["200", "400", "404"])

    def test_auth_maps_to_the_collection_variables(self):
        for auth, value in (("user", "{{user.token}}"), ("admin", "{{admin.token}}")):
            req, _ = pm.build({**SPEC, "auth": auth})
            self.assertEqual(req["auth"]["bearer"][0]["value"], value)
        self.assertEqual(pm.build({**SPEC, "auth": "none"})[0]["auth"], {"type": "noauth"})

    def test_a_body_adds_json_content_type_and_raw_mode(self):
        req, _ = pm.build({**SPEC, "method": "POST", "body": {"token": "{{user.token}}"}})
        self.assertEqual(req["dataMode"], "raw")
        self.assertIn({"key": "Content-Type", "value": "application/json", "type": "text"}, req["headerData"])

    def test_no_body_leaves_the_body_fields_unmanaged(self):
        req, _ = pm.build(SPEC)
        self.assertNotIn("rawModeData", req)

    def test_invalid_specs_are_refused(self):
        for bad in ({**SPEC, "method": "FETCH"}, {**SPEC, "path": "https://x/y"},
                    {**SPEC, "path": "/a?b=1"}, {k: v for k, v in SPEC.items() if k != "folder"},
                    {**SPEC, "description": "", "status_codes": {}},
                    {**SPEC, "path_variables": {"nope": "1"}}, {**SPEC, "auth": "root"}):
            with self.subTest(bad=bad), self.assertRaises(pm.SpecError):
                pm.build(bad)

    def test_secrets_and_personal_data_are_refused(self):
        for value in ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.sig",
                      "Bearer abcdefghijk123", "sk-proj-abcdefghijklmnop", "<redacted-email>",
                      "IH02U0vzJzYLWfs5jKaUYzxXCNl1"):
            with self.subTest(value=value), self.assertRaises(pm.SpecError):
                pm.build({**SPEC, "body": {"x": value}, "method": "POST"})

    def test_placeholders_pass(self):
        pm.build({**SPEC, "method": "POST", "body": {"email": "tester@example.com", "uid": "{{user.id}}",
                                                     "auth": "Bearer {{user.token}}",
                                                     "id": "2b7c9e1a-3d4f-4a5b-8c6d-7e8f9a0b1c2d"}})


class TestMatch(unittest.TestCase):
    def test_route_shape_ignores_host_query_and_param_names(self):
        self.assertEqual(pm.norm_path("{{default.host}}/a/:id/b?x=1"), pm.norm_path("https://h/a/:other/b"))
        self.assertNotEqual(pm.norm_path("/c/subscriptions/:u/:c"), pm.norm_path("/c/subscriptions/:c"))

    def test_a_new_route_shape_is_a_create_not_an_update_of_the_old_one(self):
        steps = pm.plan(FakePostman(), CID, SPEC)
        self.assertEqual(steps["action"], "create")
        self.assertEqual(steps["folder_id"], "f-ret")

    def test_an_existing_route_is_updated_where_it_lives(self):
        spec = {**SPEC, "folder": "Users/Other", "method": "POST", "path": "/users/verifyToken", "path_variables": {},
                "responses": [{"name": "200 OK", "code": 200, "body": {"verified": True}}]}
        steps = pm.plan(FakePostman(), CID, spec)
        self.assertEqual((steps["action"], steps["request_id"]), ("update", "r-vt"))
        self.assertTrue(any("never moved" in n for n in steps["notes"]))
        self.assertEqual(steps["responses"][0]["action"], "update")

    def test_several_matches_need_a_request_id(self):
        items = tree()
        items[1]["item"][0]["item"].append(copy.deepcopy(items[1]["item"][0]["item"][0]) | {"id": "r-vt2"})
        spec = {**SPEC, "method": "POST", "path": "/users/verifyToken", "path_variables": {}}
        with self.assertRaises(pm.SpecError) as cm:
            pm.plan(FakePostman(items), CID, spec)
        self.assertEqual(cm.exception.args[1], pm.EXIT_AMBIGUOUS)
        steps = pm.plan(FakePostman(items), CID, {**spec, "request_id": "r-vt"})
        self.assertEqual(steps["request_id"], "r-vt")

    def test_a_missing_folder_is_refused_unless_asked_to_create_it(self):
        spec = {**SPEC, "folder": "Communities/Brand New"}
        with self.assertRaises(pm.SpecError):
            pm.plan(FakePostman(), CID, spec)
        steps = pm.plan(FakePostman(), CID, spec, create_folders=True)
        self.assertEqual((steps["folder_id"], steps["create_folders"]), ("f-com", ["Brand New"]))


class TestApply(unittest.TestCase):
    def test_dry_run_writes_nothing(self):
        fake = FakePostman()
        with mock.patch.object(pm, "Postman", return_value=fake):
            code, out, _ = run_main(["run", spec_file(SPEC)], {"POSTMAN_API_KEY": "k"})
        self.assertEqual(code, 0)
        self.assertIn("dry run", out)
        self.assertEqual({m for m, _, _ in fake.calls}, {"GET"})

    def test_apply_uses_only_per_item_endpoints_and_reads_back(self):
        fake = FakePostman()
        with mock.patch.object(pm, "Postman", return_value=fake):
            code, out, err = run_main(["run", spec_file(SPEC), "--apply"], {"POSTMAN_API_KEY": "k"})
        self.assertEqual(code, 0, err)
        self.assertIn("applied and read back", out)
        writes = [(m, p) for m, p, _ in fake.calls if m != "GET"]
        self.assertEqual(writes, [("POST", f"/collections/{CID}/requests"), ("POST", f"/collections/{CID}/responses")])
        self.assertFalse(any(m == "PUT" and p == f"/collections/{CID}" for m, p, _ in fake.calls))
        self.assertFalse(any(m == "DELETE" for m, _, _ in fake.calls))

    def test_an_update_puts_one_request(self):
        fake = FakePostman()
        spec = {**SPEC, "folder": "Users/Authenticate", "method": "POST", "path": "/users/verifyToken", "path_variables": {},
                "auth": "none", "body": {"token": "{{user.token}}"}, "responses": []}
        with mock.patch.object(pm, "Postman", return_value=fake):
            code, _, err = run_main(["run", spec_file(spec), "--apply"], {"POSTMAN_API_KEY": "k"})
        self.assertEqual(code, 0, err)
        self.assertEqual([(m, p) for m, p, _ in fake.calls if m != "GET"],
                         [("PUT", f"/collections/{CID}/requests/r-vt")])

    def test_an_unchanged_entry_writes_nothing(self):
        fake = FakePostman()
        spec = {**SPEC, "folder": "Users/Authenticate", "method": "POST", "path": "/users/verifyToken", "path_variables": {}, "responses": []}
        with mock.patch.object(pm, "Postman", return_value=fake):
            run_main(["run", spec_file(spec), "--apply"], {"POSTMAN_API_KEY": "k"})
            fake.calls.clear()
            code, out, _ = run_main(["run", spec_file(spec), "--apply"], {"POSTMAN_API_KEY": "k"})
        self.assertEqual(code, 0)
        self.assertIn("nothing to change", out)
        self.assertEqual({m for m, _, _ in fake.calls}, {"GET"})

    def test_a_read_back_mismatch_fails(self):
        fake = FakePostman()
        fake.corrupt = True
        spec = {**SPEC, "folder": "Users/Authenticate", "method": "POST", "path": "/users/verifyToken", "path_variables": {}, "responses": []}
        with mock.patch.object(pm, "Postman", return_value=fake):
            code, _, err = run_main(["run", spec_file(spec), "--apply"], {"POSTMAN_API_KEY": "k"})
        self.assertEqual(code, pm.EXIT_FAIL)
        self.assertIn("READ-BACK MISMATCH", err)

    def test_a_failed_write_says_the_state_must_be_rechecked(self):
        fake = FakePostman()
        fake.fail_on = ("POST", f"/collections/{CID}/requests")
        with mock.patch.object(pm, "Postman", return_value=fake):
            code, _, err = run_main(["run", spec_file(SPEC), "--apply"], {"POSTMAN_API_KEY": "k"})
        self.assertEqual(code, pm.EXIT_FAIL)
        self.assertIn("dry run again", err)

    def test_without_a_key_nothing_is_called_and_the_entry_is_printed(self):
        with mock.patch.object(pm, "Postman") as cls:
            code, out, err = run_main(["run", spec_file(SPEC)], {"POSTMAN_API_KEY": ""})
        self.assertEqual(code, pm.EXIT_NO_KEY)
        cls.assert_not_called()
        self.assertIn("nothing was read or written", err)
        self.assertIn("GET {{default.host}}/communities/subscriptions/:communityId", out)

    def test_a_non_ascii_spec_is_read_as_utf8_whatever_the_locale(self):
        spec = {**SPEC, "description": "Returns the caller\u2019s row \u2014 never another\u2019s \u2192 404"}
        real_open = open
        with mock.patch("builtins.open", lambda f, *a, **k: real_open(f, *a, **({"encoding": "ascii"} | k))), \
                mock.patch.object(pm, "Postman") as cls:
            code, out, _ = run_main(["run", spec_file(spec)], {"POSTMAN_API_KEY": ""})
        self.assertEqual(code, pm.EXIT_NO_KEY)
        cls.assert_not_called()
        self.assertIn("\u2014 never another", out)

    def test_a_refused_spec_never_reaches_postman(self):
        with mock.patch.object(pm, "Postman") as cls:
            code, _, err = run_main(["run", spec_file({**SPEC, "description": "mail <redacted-email>"})],
                                    {"POSTMAN_API_KEY": "k"})
        self.assertEqual(code, pm.EXIT_BAD_SPEC)
        cls.assert_not_called()


class TestTransport(unittest.TestCase):
    def test_a_read_is_retried_and_a_write_is_not(self):
        client = pm.Postman("k")
        boom = pm.PostmanError("timeout", retryable=True)
        with mock.patch.object(client, "_once", side_effect=[boom, {"ok": 1}]) as once, \
                mock.patch.object(pm.time, "sleep"):
            self.assertEqual(client.call("GET", "/x"), {"ok": 1})
        self.assertEqual(once.call_count, 2)
        with mock.patch.object(client, "_once", side_effect=[boom, {"ok": 1}]) as once, \
                mock.patch.object(pm.time, "sleep"), self.assertRaises(pm.PostmanError):
            client.call("POST", "/x", {})
        self.assertEqual(once.call_count, 1)

    def test_a_socket_timeout_becomes_a_retryable_postman_error(self):
        with mock.patch.object(pm.urllib.request, "urlopen", side_effect=TimeoutError("read timed out")), \
                self.assertRaises(pm.PostmanError) as cm:
            pm.Postman("k")._once("GET", "/x", None)
        self.assertTrue(cm.exception.retryable)


if __name__ == "__main__":
    unittest.main()
