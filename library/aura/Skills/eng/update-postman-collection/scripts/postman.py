#!/usr/bin/env python3
"""
Add or update ONE request (and its example responses) in the Aura Services Postman collection.

  postman.py find --path /users/verifyToken [--method POST]
  postman.py run spec.json            # dry run: the plan and a diff, changes nothing
  postman.py run spec.json --apply    # write, then read back and compare

Only the per-item endpoints are used: PUT/POST /collections/{id}/requests and /responses (and
POST /folders with --create-folders). Never a full-collection PUT, never a delete: a change to one
route must not touch the rest of the collection.

Token: POSTMAN_API_KEY (X-Api-Key). Without it nothing is read or written; the entry is printed so
it can be added by hand. Spec format: references/spec.md.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

API = "https://api.getpostman.com"
COLLECTION_NAME = "Aura Services"
HOST_VAR = "{{default.host}}"
AUTH_TOKENS = {"user": "{{user.token}}", "coach": "{{coach.token}}", "admin": "{{admin.token}}"}
METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
REASONS = {200: "OK", 201: "Created", 204: "No Content", 400: "Bad Request", 401: "Unauthorized",
           403: "Forbidden", 404: "Not Found", 409: "Conflict", 422: "Unprocessable Entity",
           429: "Too Many Requests", 500: "Internal Server Error"}

EXIT_OK, EXIT_FAIL, EXIT_NO_KEY, EXIT_BAD_SPEC, EXIT_AMBIGUOUS = 0, 1, 2, 3, 4

# A doc entry carries placeholders, never a real credential or a real person's data.
SECRET_PATTERNS = [
    ("a JWT", re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("a literal bearer token", re.compile(r"Bearer\s+(?!\{\{)[A-Za-z0-9._-]{8,}")),
    ("an API key", re.compile(r"\b(?:sk|pk|rk|ntn|xox[bpa])[-_][A-Za-z0-9_-]{10,}")),
    ("an email address", re.compile(r"[\w.+-]+@(?!example\.(?:com|org)\b)[\w-]+\.[\w.-]+")),
    ("a Firebase uid", re.compile(r"\b(?=[A-Za-z0-9]*[a-z])(?=[A-Za-z0-9]*[A-Z])(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{28}\b")),
]


class SpecError(Exception):
    pass


class PostmanError(Exception):
    def __init__(self, msg, retryable=False):
        super().__init__(msg)
        self.retryable = retryable


class Postman:
    """The HTTP calls this script makes, behind one seam the tests replace."""

    def __init__(self, key):
        self.key = key

    def call(self, method, path, body=None):
        # Only a read is retried: a write that timed out may still have landed.
        tries = 3 if method == "GET" else 1
        for attempt in range(tries):
            try:
                return self._once(method, path, body)
            except PostmanError as e:
                if not e.retryable or attempt == tries - 1:
                    raise
                time.sleep(2 ** attempt)

    def _once(self, method, path, body):
        where = f"{method} {path.split('?')[0]}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(API + path, data=data, method=method,
                                     headers={"X-Api-Key": self.key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read().decode()).get("error") or {}
            except ValueError:
                err = {}
            raise PostmanError(f"{where} -> HTTP {e.code} {err.get('name', '')}: {err.get('message', '')}"
                               .rstrip(": "), retryable=e.code == 429 or e.code >= 500) from None
        except (urllib.error.URLError, OSError) as e:
            raise PostmanError(f"{where} -> {getattr(e, 'reason', None) or e!r}", retryable=True) from None


# ---- spec -> desired entry ------------------------------------------------------------------

def check_secrets(spec):
    text = json.dumps(spec, ensure_ascii=False)
    for what, rx in SECRET_PATTERNS:
        if rx.search(text):
            raise SpecError(f"the spec contains {what}; use a placeholder such as {{{{user.token}}}}, "
                            "{{user.id}} or tester@example.com")


def status_table(codes):
    rows = "\n".join(f"| {c} | {str(w).strip()} |" for c, w in sorted(codes.items(), key=lambda kv: int(kv[0])))
    return f"## Status codes\n\n| Code | When |\n|---|---|\n{rows}"


def body_text(body):
    return body if isinstance(body, str) else json.dumps(body, indent=4, ensure_ascii=False)


def build(spec):
    """The managed fields of the request, in Postman's per-item (v1) shape, plus the responses."""
    if not isinstance(spec, dict):
        raise SpecError("the spec must be a JSON object")
    check_secrets(spec)
    for k in ("folder", "method", "path"):
        if not spec.get(k):
            raise SpecError(f"missing '{k}'")
    method = spec["method"].upper()
    if method not in METHODS:
        raise SpecError(f"method must be one of {sorted(METHODS)}")
    path = spec["path"]
    if not path.startswith("/") or "?" in path or "://" in path:
        raise SpecError("'path' is the route only, starting with '/', no host and no query")
    if not spec.get("description") and not spec.get("status_codes"):
        raise SpecError("give a 'description' and its 'status_codes'")

    params = re.findall(r":([A-Za-z_][A-Za-z0-9_]*)", path)
    pv = spec.get("path_variables") or {}
    unknown = set(pv) - set(params)
    if unknown:
        raise SpecError(f"path_variables not in the path: {sorted(unknown)}")
    query = spec.get("query") or {}
    if isinstance(query, dict):
        query = [{"key": k, "value": str(v)} for k, v in query.items()]
    url = HOST_VAR + path
    if query:
        url += "?" + "&".join(f"{q['key']}={q.get('value', '')}" for q in query)

    auth = spec.get("auth", "user")
    if auth in AUTH_TOKENS:
        auth_obj = {"type": "bearer", "bearer": [{"key": "token", "value": AUTH_TOKENS[auth], "type": "string"}]}
    elif auth == "none":
        auth_obj = {"type": "noauth"}
    elif auth == "inherit":
        auth_obj = None
    else:
        raise SpecError(f"auth must be one of {sorted(AUTH_TOKENS) + ['none', 'inherit']}")

    headers = dict(spec.get("headers") or {})
    body = spec.get("body")
    if body is not None and not any(h.lower() == "content-type" for h in headers):
        headers["Content-Type"] = "application/json"

    desc = (spec.get("description") or "").strip()
    if spec.get("status_codes"):
        desc = (desc + "\n\n" if desc else "") + status_table(spec["status_codes"])

    req = {
        "name": spec.get("name") or path,
        "method": method,
        "url": url,
        "auth": auth_obj,
        "headerData": [{"key": k, "value": str(v), "type": "text"} for k, v in headers.items()],
        "queryParams": [{"key": q["key"], "value": str(q.get("value", "")), "type": "text",
                         **({"description": q["description"]} if q.get("description") else {})} for q in query],
        "pathVariableData": [{"key": p, "value": str(pv.get(p, ""))} for p in params],
        "description": desc,
    }
    if body is not None:
        req.update({"dataMode": "raw", "rawModeData": body_text(body),
                    "dataOptions": {"raw": {"language": "json" if not isinstance(body, str) else "text"}}})

    responses = []
    for r in spec.get("responses") or []:
        code = int(r.get("code", 200))
        reason = REASONS.get(code, "")
        responses.append({
            "name": r.get("name") or f"{code} {reason}".strip(),
            "responseCode": {"code": code, "name": reason},
            "status": reason,
            "headers": [{"key": "Content-Type", "value": "application/json"}],
            "language": "json",
            "text": body_text(r.get("body", {})),
        })
    return req, responses


# ---- collection lookup ----------------------------------------------------------------------

def norm_path(url):
    """Route shape for matching: no host variable, no query, every :param the same."""
    u = (url or "").strip()
    u = re.sub(r"^\{\{[^}]+\}\}", "", u)
    u = re.sub(r"^https?://[^/]+", "", u)
    u = u.split("?", 1)[0].rstrip("/") or "/"
    return re.sub(r":[A-Za-z_][A-Za-z0-9_]*", ":", u)


def item_url(it):
    u = (it.get("request") or {}).get("url")
    return u.get("raw", "") if isinstance(u, dict) else (u or "")


def walk(items, path=()):
    for it in items:
        if "item" in it:
            yield ("folder", path + (it["name"],), it)
            yield from walk(it["item"], path + (it["name"],))
        else:
            yield ("request", path, it)


def collection_id(pm, override=None):
    if override:
        return override
    cs = [c for c in pm.call("GET", "/collections")["collections"] if c["name"] == COLLECTION_NAME]
    if len(cs) != 1:
        raise PostmanError(f"expected one collection named {COLLECTION_NAME!r}, found {len(cs)}; "
                           "pass --collection-id")
    return cs[0]["id"]


def resolve_folder(tree, folder):
    """Folder path -> (folder id or None, the missing tail)."""
    parts = [p for p in folder.split("/") if p]
    items, fid = tree, None
    for i, name in enumerate(parts):
        hits = [it for it in items if "item" in it and it["name"] == name]
        if len(hits) > 1:
            raise SpecError(f"folder {'/'.join(parts[:i + 1])!r} is ambiguous ({len(hits)} folders)")
        if not hits:
            return fid, parts[i:]
        fid, items = hits[0]["id"], hits[0]["item"]
    return fid, []


def match(tree, req, request_id=None):
    reqs = [(p, it) for kind, p, it in walk(tree) if kind == "request"]
    if request_id:
        hits = [(p, it) for p, it in reqs if it.get("id") == request_id]
        if not hits:
            raise SpecError(f"request_id {request_id} is not in the collection")
        return hits
    want = (req["method"], norm_path(req["url"]))
    return [(p, it) for p, it in reqs
            if ((it.get("request") or {}).get("method"), norm_path(item_url(it))) == want]


# ---- diff -----------------------------------------------------------------------------------

def managed_view(d, keys):
    out = {}
    for k in keys:
        v = d.get(k)
        if k in ("headerData", "queryParams", "pathVariableData"):
            v = [{"key": x.get("key"), "value": x.get("value")} for x in (v or [])]
        if k == "auth" and v and v.get("type") == "bearer":
            v = {"type": "bearer", "token": next((b.get("value") for b in v.get("bearer", [])
                                                if b.get("key") == "token"), None)}
        out[k] = v
    return out


def response_view(r):
    return {"name": r.get("name"), "code": (r.get("responseCode") or {}).get("code"), "text": r.get("text")}


def diff(label, old, new):
    a = json.dumps(old, indent=2, ensure_ascii=False, sort_keys=True).splitlines() if old is not None else []
    b = json.dumps(new, indent=2, ensure_ascii=False, sort_keys=True).splitlines()
    return list(difflib.unified_diff(a, b, f"{label} (now)", f"{label} (after)", lineterm="", n=2))


def masked(d):
    """A Postman response body for printing: an existing literal token is never echoed."""
    s = json.dumps(d, ensure_ascii=False)
    for _, rx in SECRET_PATTERNS[:3]:
        s = rx.sub("<redacted>", s)
    return json.loads(s)


# ---- plan / apply ---------------------------------------------------------------------------

def plan(pm, cid, spec, create_folders=False):
    req, responses = build(spec)
    tree = pm.call("GET", f"/collections/{cid}")["collection"]["item"]
    hits = match(tree, req, spec.get("request_id"))
    if len(hits) > 1:
        lines = "\n".join(f"  {it['id']}  {'/'.join(p)}  {it['name']}" for p, it in hits)
        raise SpecError(f"{len(hits)} requests match {req['method']} {norm_path(req['url'])}; "
                        f"pin one with request_id:\n{lines}", EXIT_AMBIGUOUS)
    steps = {"request": req, "responses": [], "notes": []}
    keys = [k for k in req if k != "dataOptions"]
    if hits:
        p, it = hits[0]
        cur = pm.call("GET", f"/collections/{cid}/requests/{it['id']}")["data"]
        steps.update(action="update", request_id=it["id"], folder="/".join(p),
                     diff=diff("request", managed_view(masked(cur), keys), managed_view(req, keys)))
        if "/".join(p) != "/".join(x for x in spec["folder"].split("/") if x):
            steps["notes"].append(f"the match lives in {'/'.join(p)!r}, not {spec['folder']!r}; "
                                  "it is updated where it is, never moved")
        existing = {r.get("name"): r.get("id") for r in it.get("response", [])}
        for r in responses:
            rid = existing.get(r["name"])
            old = response_view(pm.call("GET", f"/collections/{cid}/responses/{rid}")["data"]) if rid else None
            steps["responses"].append({"action": "update" if rid else "create", "id": rid, "body": r,
                                       "diff": diff(f"response {r['name']!r}", old, response_view(r))})
    else:
        fid, missing = resolve_folder(tree, spec["folder"])
        if missing and not create_folders:
            raise SpecError(f"folder {spec['folder']!r} not found (missing: {'/'.join(missing)}); "
                            "fix the path, or pass --create-folders to create it")
        steps.update(action="create", folder=spec["folder"], folder_id=fid, create_folders=missing,
                     diff=diff("request", None, managed_view(req, keys)))
        for r in responses:
            steps["responses"].append({"action": "create", "id": None, "body": r,
                                       "diff": diff(f"response {r['name']!r}", None, response_view(r))})
    steps["changed"] = bool(steps["diff"]) or any(r["diff"] for r in steps["responses"])
    return steps


def apply(pm, cid, steps):
    req = steps["request"]
    if steps["action"] == "create":
        fid = steps["folder_id"]
        for name in steps["create_folders"]:
            body = {"name": name, **({"folder": fid} if fid else {})}
            fid = pm.call("POST", f"/collections/{cid}/folders", body)["data"]["id"]
        q = f"?folder={fid}" if fid else ""
        rid = pm.call("POST", f"/collections/{cid}/requests{q}", req)["data"]["id"]
    else:
        rid = steps["request_id"]
        if steps["diff"]:
            pm.call("PUT", f"/collections/{cid}/requests/{rid}", req)
    ids = []
    for r in steps["responses"]:
        if r["action"] == "create":
            ids.append(pm.call("POST", f"/collections/{cid}/responses?request={rid}", r["body"])["data"]["id"])
        else:
            if r["diff"]:
                pm.call("PUT", f"/collections/{cid}/responses/{r['id']}", r["body"])
            ids.append(r["id"])
    return rid, ids


def read_back(pm, cid, steps, rid, response_ids):
    """Every managed field as Postman now stores it; returns the mismatches."""
    req = steps["request"]
    keys = [k for k in req if k != "dataOptions"]
    got = managed_view(pm.call("GET", f"/collections/{cid}/requests/{rid}")["data"], keys)
    bad = [f"request.{k}" for k, v in managed_view(req, keys).items() if got.get(k) != v]
    for r, i in zip(steps["responses"], response_ids):
        if response_view(pm.call("GET", f"/collections/{cid}/responses/{i}")["data"]) != response_view(r["body"]):
            bad.append(f"response {r['body']['name']!r}")
    return bad


# ---- output ---------------------------------------------------------------------------------

def print_entry(req, responses):
    print(f"{req['method']} {req['url']}")
    print(f"name: {req['name']}")
    tok = (req.get("auth") or {}).get("bearer", [{}])[0].get("value") if req.get("auth") else "inherit"
    print(f"auth: {tok if (req.get('auth') or {}).get('type') != 'noauth' else 'none'}")
    for h in req["headerData"]:
        print(f"header: {h['key']}: {h['value']}")
    for p in req["pathVariableData"]:
        print(f"path variable: {p['key']} = {p['value']}")
    if req.get("rawModeData"):
        print("body:\n" + req["rawModeData"])
    print("description:\n" + req["description"])
    for r in responses:
        print(f"example response {r['name']!r}:\n{r['text']}")


def print_plan(steps):
    req = steps["request"]
    where = steps.get("folder")
    if steps["action"] == "create":
        made = steps["create_folders"]
        print(f"CREATE {req['method']} {req['url']} in {where!r}"
              + (f" (new folders: {'/'.join(made)})" if made else ""))
    else:
        print(f"UPDATE request {steps['request_id']} in {where!r}: {req['method']} {req['url']}")
    for n in steps["notes"]:
        print(f"note: {n}")
    print("\n".join(steps["diff"]) or "request: no change")
    for r in steps["responses"]:
        print(f"{r['action'].upper()} response {r['body']['name']!r}")
        print("\n".join(r["diff"]) or "  no change")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--collection-id", default=os.environ.get("POSTMAN_COLLECTION_ID"),
                    help=f"default: the collection named {COLLECTION_NAME!r}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("find", help="list requests matching a route (read-only)")
    f.add_argument("--path", required=True)
    f.add_argument("--method")
    r = sub.add_parser("run", help="plan (dry run) or --apply one request from a spec file")
    r.add_argument("spec")
    r.add_argument("--apply", action="store_true")
    r.add_argument("--create-folders", action="store_true", help="create a missing folder path")
    a = ap.parse_args()

    spec = None
    if a.cmd == "run":
        try:
            with open(a.spec, encoding="utf-8") as fh:
                spec = json.load(fh)
            req, responses = build(spec)
        except (OSError, ValueError) as e:
            print(f"cannot read the spec: {e}", file=sys.stderr)
            return EXIT_BAD_SPEC
        except SpecError as e:
            print(f"spec refused: {e.args[0]}", file=sys.stderr)
            return EXIT_BAD_SPEC

    key = os.environ.get("POSTMAN_API_KEY")
    if not key:
        print("POSTMAN_API_KEY is not set: nothing was read or written. Add the entry by hand, or set "
              "the key (docs/token-inventory.md) and re-run.", file=sys.stderr)
        if spec is not None:
            print_entry(req, responses)
        return EXIT_NO_KEY

    pm = Postman(key)
    try:
        cid = collection_id(pm, a.collection_id)
        if a.cmd == "find":
            tree = pm.call("GET", f"/collections/{cid}")["collection"]["item"]
            want = norm_path(a.path)
            n = 0
            for kind, p, it in walk(tree):
                m = (it.get("request") or {}).get("method")
                if kind == "request" and norm_path(item_url(it)) == want and (not a.method or m == a.method.upper()):
                    print(f"{it['id']}  {m}  {'/'.join(p)}  {it['name']}  ({len(it.get('response', []))} examples)")
                    n += 1
            print(f"{n} match(es)")
            return EXIT_OK
        steps = plan(pm, cid, spec, a.create_folders)
        print_plan(steps)
        if not a.apply:
            print("\ndry run: nothing written. Re-run with --apply.")
            return EXIT_OK
        if not steps["changed"]:
            print("\nnothing to change.")
            return EXIT_OK
        rid, ids = apply(pm, cid, steps)
        bad = read_back(pm, cid, steps, rid, ids)
        if bad:
            print(f"\nREAD-BACK MISMATCH on request {rid}: {', '.join(bad)}", file=sys.stderr)
            return EXIT_FAIL
        print(f"\napplied and read back: request {rid}, {len(ids)} example response(s).")
        return EXIT_OK
    except SpecError as e:
        print(f"refused: {e.args[0]}", file=sys.stderr)
        return e.args[1] if len(e.args) > 1 else EXIT_BAD_SPEC
    except PostmanError as e:
        print(f"Postman API failed: {e}", file=sys.stderr)
        if a.cmd == "run" and a.apply:
            print("A write may have landed before the failure: run the dry run again to see the "
                  "collection's state before retrying.", file=sys.stderr)
        return EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
