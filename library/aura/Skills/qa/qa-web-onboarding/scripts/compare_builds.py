#!/usr/bin/env python3
"""Compare the shipped Next.js code of web signup builds without running them.

Downloads each page's HTML and JavaScript chunks over plain HTTP. No browser and no page
scripts run, so no analytics event fires on the host. Use it to check a live site against
dev without sending test traffic to production analytics.

Examples:
  python3 compare_builds.py \
      --page dev=https://aura-ai-dev-989cf9e0116c.herokuapp.com/signup \
      --page live=https://www.aurie.ai/signup \
      --find "Sleep depravation=depravation" --find 'relative privacy link=href:"/privacy"' \
      --path privacy --path privacy-policy

Give --path without its leading slash: Git Bash on Windows rewrites "/privacy" into a
Windows file path before Python sees it.

Prints JSON: per page, the build ID, the chunk file for each matching route, which file holds
each --find pattern, and the HTTP status of each --path on that page's host. With two pages it
also lists the routes whose chunk files are identical or different.
"""
import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

UA = "Mozilla/5.0 (compatible; qa-web-onboarding/1.0)"


def get(url: str, timeout: int = 30) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:  # network error: report as status 0
        return 0, ""


def snapshot(name: str, page: str, route_filter: str) -> dict:
    origin = "{0.scheme}://{0.netloc}".format(urllib.parse.urlparse(page))
    status, html = get(page)
    if status != 200:
        return {"name": name, "page": page, "error": f"HTTP {status}"}
    scripts = re.findall(r'src="(/_next/static/[^"]+\.js)"', html)
    manifest = next((s for s in scripts if s.endswith("_buildManifest.js")), None)
    build = manifest.split("/")[3] if manifest else None
    routes: dict[str, str] = {}
    files = set(s for s in scripts if "/chunks/" in s)
    if manifest:
        _, mt = get(origin + manifest)
        for route, body in re.findall(r'"(/[^"]*)":\[([^\]]*)\]', mt):
            if re.search(route_filter, route):
                routes[route] = ",".join(re.findall(r'static/chunks/([^"\\]+\.js)', body))
        # Shared chunks such as "2490-<hash>.js" are often passed into the manifest as
        # function arguments rather than written inside each route's list, so take every
        # chunk path the manifest mentions anywhere.
        files.update(f"/_next/static/chunks/{c}" for c in re.findall(r'static/chunks/([^"\\]+\.js)', mt))
    webpack = next((s for s in scripts if "/webpack-" in s), None)
    if webpack:
        _, wt = get(origin + webpack)
        files.update(f"/_next/static/chunks/{i}.{h}.js" for i, h in re.findall(r'(\d+):"([0-9a-f]{16})"', wt))

    bodies: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=16) as pool:
        # Two rounds: chunks can name further chunks by path, so fetch any new ones found.
        for _ in range(2):
            todo = sorted(f for f in files if f not in bodies)
            if not todo:
                break
            bodies.update(zip(todo, pool.map(lambda f: get(origin + f)[1], todo)))
            for text in list(bodies.values()):
                files.update("/_next/" + c for c in re.findall(r'static/chunks/[\w./-]+?\.js', text))
    shared = sorted(f.split("/")[-1] for f in bodies if bodies[f] and re.search(r"/chunks/\d+-[0-9a-f]+\.js$", f))
    return {"name": name, "page": page, "origin": origin, "build": build, "routes": routes,
            "shared_chunks": shared, "_bodies": bodies}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--page", action="append", required=True, help="name=URL of the page to snapshot (repeatable)")
    ap.add_argument("--find", action="append", default=[], help="label=regex to locate in the shipped code (repeatable)")
    ap.add_argument("--path", action="append", default=[], help="path to fetch on each host, without the leading slash, e.g. privacy (repeatable)")
    ap.add_argument("--routes", default=r"signup|_app", help="regex selecting build-manifest routes to list")
    args = ap.parse_args()

    finds = []
    for f in args.find:
        label, _, pattern = f.partition("=")
        finds.append((label, re.compile(pattern or label)))

    out = []
    for p in args.page:
        name, _, url = p.partition("=")
        snap = snapshot(name, url, args.routes)
        bodies = snap.pop("_bodies", {})
        if "error" not in snap:
            snap["found"] = {
                label: [f.split("/")[-1] for f, t in sorted(bodies.items()) if rx.search(t)] or None
                for label, rx in finds
            }
            paths = ["/" + p.lstrip("/") for p in args.path]
            snap["paths"] = {path: get(snap["origin"] + path)[0] for path in paths}
        out.append(snap)

    result = {"pages": out}
    if len(out) == 2 and all("routes" in s for s in out):
        a, b = out
        keys = sorted(set(a["routes"]) | set(b["routes"]))
        result["same_routes"] = [k for k in keys if a["routes"].get(k) == b["routes"].get(k)]
        result["different_routes"] = [k for k in keys if a["routes"].get(k) != b["routes"].get(k)]
        result["same_shared_chunks"] = sorted(set(a["shared_chunks"]) & set(b["shared_chunks"]))
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
