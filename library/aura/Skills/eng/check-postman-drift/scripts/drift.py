#!/usr/bin/env python3
"""Compare each backend service's routes in code with the Aura Services Postman collection.

Read-only on both sides. Code: each service's Express router tree, read from the repo's
`origin` default branch with `git show`, one repo at a time; no checkout, no grep, no worktree.
Postman: one GET of the collection with POSTMAN_API_KEY. Nothing is written to either.

    python3 drift.py [--services CoachService,UserService] [--services-dir DIR] [--out FILE]

Per service it reports, as METHOD + path only (no bodies, no example values):
  missing  in code, not in Postman
  extra    in Postman, not in code
  stale    same path, but Postman sends no auth where the code requires it
  deleted  requests named "[DELETED] …", waiting for a workspace admin; never counted as extra
Across the collection it also lists the folders whose requests are all "[DELETED]" (the owner
renames such a folder "[DELETED] <name>" and a workspace admin deletes it), flagging the ones not
yet renamed.
A request whose first path segment no service mounts is listed under `unmapped`.

Every stop prints `FOLLOWUP stop <stage>: <why>` (stages `services-dir`, `token`, `collection`, `repo:<service>`,
`router:<service>`, lowercase so the stage fits a follow-up key), and the run goes on past a single repo. Exit 0 when the run finished (drift
or not), 3 when a stage stopped, 2 on a bad argument.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import posixpath
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

API = "https://api.getpostman.com"
COLLECTION_NAME = "Aura Services"
SERVICES = ("BusinessService", "CoachService", "ContentService", "EmailService", "ExperimentService",
            "FeedService", "IntelligenceService", "IterableService", "NotificationService",
            "PaymentService", "ReportService", "SupportService", "UserService")
ENTRY = "src/http/router/index.js"
METHODS = ("get", "post", "put", "patch", "delete")
AUTH_MIDDLEWARE = re.compile(r"\b(authenticate|authorize|ownerAuthorize|adminOnly|requireAdmin)\b")


class Stop(Exception):
    """A stage that cannot finish: printed as one FOLLOWUP line."""

    def __init__(self, stage: str, why: str):
        super().__init__(why)
        self.stage = stage


# ---- code side ------------------------------------------------------------------------------

def git_reader(repo: str):
    """`fetch` then a reader that returns a file's text on origin's default branch, or None."""
    fetch = subprocess.run(["git", "-C", repo, "fetch", "--quiet", "origin"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    if fetch.returncode:
        lines = (fetch.stderr or "").strip().splitlines()
        raise Stop("repo", f"git fetch failed: {lines[-1] if lines else 'no error output'}")
    head = subprocess.run(["git", "-C", repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    ref = head.stdout.strip() or "origin/main"

    def read(path: str):
        p = subprocess.run(["git", "-C", repo, "show", f"{ref}:{path}"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        return p.stdout if p.returncode == 0 else None
    return read


def call_args(text: str, start: int) -> str:
    """The argument text of a call whose `(` is at `start`, matching nested parens."""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[start + 1:i]
    return text[start + 1:]


def join(prefix: str, path: str) -> str:
    p = "/" + "/".join(s for s in (prefix + "/" + path).split("/") if s)
    return p


def norm(path: str) -> str:
    """Route shape: every `:param` or `{{var}}` segment the same, no trailing slash."""
    segs = []
    for s in path.split("?", 1)[0].split("/"):
        if not s:
            continue
        segs.append(":" if s.startswith(":") or re.fullmatch(r"\{\{[^}]+\}\}", s) else s)
    return "/" + "/".join(segs)


def routes(read, entry: str = ENTRY, prefix: str = "", seen=None, unresolved=None):
    """[(METHOD, path, needs_auth)] for every route mounted under `entry`, recursively."""
    seen = set() if seen is None else seen
    unresolved = [] if unresolved is None else unresolved
    if entry in seen:
        return []
    seen.add(entry)
    text = read(entry)
    if text is None:
        unresolved.append(f"{entry}: not found")
        return []
    here = posixpath.dirname(entry)
    requires = {}
    for name, rel in re.findall(r"(?:const|let|var)\s+(\w+)\s*=\s*require\(\s*['\"](\.[^'\"]+)['\"]\s*\)", text):
        requires[name] = posixpath.normpath(posixpath.join(here, rel))
    routers = set(re.findall(r"(\w+)\s*=\s*(?:express\.)?Router\(", text)) or {"router"}
    out = []
    for m in re.finditer(r"\b(%s)\.(get|post|put|patch|delete|use)\s*\(" % "|".join(map(re.escape, routers)), text):
        args = call_args(text, m.end() - 1)
        verb = m.group(2)
        path_m = re.match(r"\s*['\"`]([^'\"`]*)['\"`]\s*,?", args)
        path = path_m.group(1) if path_m else ""
        rest = args[path_m.end():] if path_m else args
        if verb == "use":
            names = re.findall(r"\b(\w+)\b", rest)
            target = next((n for n in reversed(names) if n in requires), None)
            if target is None:
                if path:  # a mounted path with no router we can follow
                    unresolved.append(f"{entry}: use('{path}') → {rest.strip()[:40]}")
                continue
            base = requires[target]
            sub = next((c for c in (base + ".js", base + "/index.js") if read(c) is not None), None)
            if sub is None:
                unresolved.append(f"{entry}: {target} → {base} not found")
                continue
            auth_here = bool(AUTH_MIDDLEWARE.search(rest))
            for meth, p, auth in routes(read, sub, join(prefix, path), seen, unresolved):
                out.append((meth, p, auth or auth_here))
            continue
        full = join(prefix, path)
        # Kubernetes probes: `/ready` and `/status` on every router, and the root `/` liveness check.
        if verb == "get" and (path in ("/ready", "/status") or full == "/"):
            continue
        out.append((verb.upper(), full, bool(AUTH_MIDDLEWARE.search(rest))))
    return out


def owned_segments(code_routes) -> set[str]:
    return {p.split("/")[1] for _, p, _ in code_routes if p.count("/") >= 1 and p != "/"}


# ---- Postman side ---------------------------------------------------------------------------

def postman_get(key: str, path: str, tries: int = 4, sleep=time.sleep):
    req = urllib.request.Request(API + path, headers={"X-Api-Key": key})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                if attempt < tries - 1:
                    sleep(int(e.headers.get("Retry-After") or 2 ** attempt))
                    continue
            raise Stop("collection", f"GET {path.split('?')[0]} -> HTTP {e.code}") from None
        except (urllib.error.URLError, OSError) as e:
            if attempt < tries - 1:
                sleep(2 ** attempt)
                continue
            raise Stop("collection", f"GET {path.split('?')[0]} -> {getattr(e, 'reason', e)}") from None
    raise Stop("collection", f"GET {path} -> gave up")


def fetch_collection(key: str, get=postman_get) -> dict:
    cs = [c for c in get(key, "/collections").get("collections", []) if c.get("name") == COLLECTION_NAME]
    if len(cs) != 1:
        raise Stop("collection", f"expected one collection named {COLLECTION_NAME!r}, found {len(cs)}")
    return get(key, f"/collections/{cs[0]['uid']}")["collection"]


def requests_in(collection: dict):
    """[(METHOD, path, sends_auth, deleted, name)] with auth inherited from folders and the collection."""
    out = []

    def auth_of(node, inherited):
        a = node.get("auth") or (node.get("request") or {}).get("auth")
        if not a:
            return inherited
        return a.get("type") not in (None, "noauth")

    def walk(items, inherited):
        for it in items:
            if "item" in it:
                walk(it["item"], auth_of(it, inherited))
                continue
            req = it.get("request") or {}
            url = req.get("url")
            raw = url.get("raw", "") if isinstance(url, dict) else (url or "")
            raw = re.sub(r"^\{\{[^}]+\}\}", "", raw.strip())
            raw = re.sub(r"^https?://[^/]+", "", raw)
            name = it.get("name", "")
            out.append(((req.get("method") or "GET").upper(), raw.split("?", 1)[0] or "/",
                        auth_of(it, inherited), name.startswith("[DELETED]"), name))
    walk(collection.get("item", []), auth_of(collection, False))
    return out


def deleted_folders(collection: dict):
    """[{folder, requests, renamed}] for each outermost folder whose requests are all [DELETED]."""
    out = []

    def counts(node):
        total = deleted = 0
        for it in node.get("item", []):
            if "item" in it:
                t, d = counts(it)
            else:
                t, d = 1, int(it.get("name", "").startswith("[DELETED]"))
            total, deleted = total + t, deleted + d
        return total, deleted

    def walk(items, trail):
        for it in items:
            if "item" not in it:
                continue
            name = it.get("name", "")
            total, deleted = counts(it)
            if total and deleted == total:
                out.append({"folder": " / ".join([*trail, name]), "requests": total,
                            "renamed": name.startswith("[DELETED]")})
            else:
                walk(it["item"], [*trail, name])
    walk(collection.get("item", []), [])
    return out


# ---- compare --------------------------------------------------------------------------------

def compare(code: dict, postman) -> dict:
    """code: {service: [(METHOD, path, needs_auth)]}. Returns the per-service report."""
    owner = {}
    for svc, rs in code.items():
        for seg in owned_segments(rs):
            owner.setdefault(seg, set()).add(svc)
    report = {svc: {"missing": [], "extra": [], "stale": [], "deleted": []} for svc in code}
    report["unmapped"] = []
    live = {}
    for meth, path, auth, deleted, name in postman:
        seg = (path.split("/") + ["", ""])[1]
        svcs = owner.get(seg)
        if not svcs:  # a [DELETED] one too: the admin queue must not vanish with an unscanned service
            report["unmapped"].append(f"{'[DELETED] ' if deleted else ''}{meth} {path}")
            continue
        for svc in svcs:
            if deleted:
                report[svc]["deleted"].append(f"{meth} {path}")
            else:
                live.setdefault(svc, {})[(meth, norm(path))] = (path, auth)
    for svc, rs in code.items():
        documented = live.get(svc, {})
        in_code = {}
        for meth, path, needs in rs:
            in_code[(meth, norm(path))] = (path, needs)
        for k, (path, needs) in sorted(in_code.items()):
            if k not in documented:
                report[svc]["missing"].append(f"{k[0]} {path}")
            elif needs and not documented[k][1]:
                report[svc]["stale"].append(f"{k[0]} {path} (code needs auth; Postman sends none)")
        for k, (path, _) in sorted(documented.items()):
            if k not in in_code:
                report[svc]["extra"].append(f"{k[0]} {path}")
    for svc in report:
        if svc != "unmapped":
            for kind in report[svc]:
                report[svc][kind] = sorted(set(report[svc][kind]))
    report["unmapped"] = sorted(set(report["unmapped"]))
    return report


def markdown(report: dict, stops: list, unresolved: dict, date: str) -> str:
    L = [f"# Postman drift — {date}", ""]
    L.append("| Service | Missing | Extra | Stale | [DELETED] |")
    L.append("|---|---|---|---|---|")
    for svc, r in report.items():
        if svc not in ("unmapped", "deleted_folders"):
            L.append(f"| {svc} | {len(r['missing'])} | {len(r['extra'])} | {len(r['stale'])} | {len(r['deleted'])} |")
    for svc, r in report.items():
        if svc in ("unmapped", "deleted_folders") or not any(r.values()):
            continue
        L += ["", f"## {svc}"]
        for kind in ("missing", "extra", "stale", "deleted"):
            if r[kind]:
                L += ["", f"**{kind}** ({len(r[kind])})", *[f"- `{x}`" for x in r[kind]]]
    folders = report.get("deleted_folders") or []
    if folders:
        L += ["", f"## Folders with every request [DELETED] ({len(folders)})",
              "A workspace admin deletes a renamed folder; the owner renames one not yet renamed.",
              *[f"- `{f['folder']}` ({f['requests']} request{'' if f['requests'] == 1 else 's'})"
                + ("" if f["renamed"] else " — **not renamed**: rename it `[DELETED] <name>`") for f in folders]]
    if report.get("unmapped"):
        L += ["", f"## Unmapped requests ({len(report['unmapped'])})",
              "No service mounts their first path segment.", *[f"- `{x}`" for x in report["unmapped"]]]
    if any(unresolved.values()):
        L += ["", "## Router mounts not followed"]
        for svc, xs in unresolved.items():
            L += [f"- {svc}: `{x}`" for x in xs]
    if stops:
        L += ["", "## Stops", *[f"- {s}" for s in stops]]
    return "\n".join(L) + "\n"


def load_key() -> str:
    key = os.environ.get("POSTMAN_API_KEY", "").strip()
    if not key:
        try:
            env = json.loads((pathlib.Path.home() / ".claude/settings.json").read_text(encoding="utf-8")).get("env", {})
            key = str(env.get("POSTMAN_API_KEY") or "").strip()
        except (OSError, ValueError):
            key = ""
    return key


def run(services, services_dir, key, reader_for=git_reader, get=postman_get, date=None):
    """(report, stops, unresolved). Never raises: each stop is collected and the run goes on."""
    date = date or dt.date.today().isoformat()
    stops, unresolved, code = [], {}, {}
    if not os.path.isdir(services_dir):
        stops.append(f"postman-drift:{date}:services-dir — {services_dir} not found; pass --services-dir "
                     "or set AURA_SERVICES_DIR")
        return {}, stops, unresolved
    for svc in services:  # one repo at a time, never in parallel
        try:
            read = reader_for(os.path.join(services_dir, svc))
        except Stop as e:
            stops.append(f"postman-drift:{date}:repo:{svc.lower()} — {e}")
            continue
        un = []
        rs = routes(read, unresolved=un)
        unresolved[svc] = un
        if not rs:
            stops.append(f"postman-drift:{date}:router:{svc.lower()} — no routes read from {ENTRY}")
            continue
        code[svc] = rs
    if not key:
        stops.append(f"postman-drift:{date}:token — POSTMAN_API_KEY is not set")
        return {}, stops, unresolved
    try:
        collection = fetch_collection(key, get)
    except Stop as e:
        stops.append(f"postman-drift:{date}:{e.stage} — {e}")
        return {}, stops, unresolved
    report = compare(code, requests_in(collection))
    report["deleted_folders"] = deleted_folders(collection)
    return report, stops, unresolved


def main(argv=None, **seams) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--services", default=",".join(SERVICES), help="comma-separated service repos")
    ap.add_argument("--services-dir", default=os.environ.get("AURA_SERVICES_DIR")
                    or os.path.expanduser("~/Development/Aura/Services"),
                    help="the folder holding the service checkouts (default AURA_SERVICES_DIR, "
                         "else ~/Development/Aura/Services); a missing folder is a stop")
    ap.add_argument("--out", default="", help="also write the report as Markdown here")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    a = ap.parse_args(argv)
    services = [s.strip() for s in a.services.split(",") if s.strip()]
    bad = [s for s in services if not re.fullmatch(r"[A-Za-z]+Service", s)]
    if bad:  # a worktree copy (`PaymentService-wt-…`) or a typo is never scanned
        print(f"drift: not a service repo name: {', '.join(bad)}", file=sys.stderr)
        return 2
    date = seams.pop("date", None) or dt.date.today().isoformat()
    report, stops, unresolved = run(services, a.services_dir, seams.pop("key", None) or load_key(),
                                    date=date, **seams)
    if a.json:
        print(json.dumps({"report": report, "stops": stops, "unresolved": unresolved}, indent=2))
    else:
        print(markdown(report, stops, unresolved, date))
    if a.out:
        pathlib.Path(a.out).write_text(markdown(report, stops, unresolved, date), encoding="utf-8")
    for s in stops:
        key, why = s.split(" — ", 1)
        print(f"FOLLOWUP stop {key.split(':', 2)[2]}: {why}")
    return 3 if stops else 0


if __name__ == "__main__":
    sys.exit(main())
