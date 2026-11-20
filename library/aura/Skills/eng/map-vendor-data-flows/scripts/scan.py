#!/usr/bin/env python3
"""Find what each repo sends to each vendor: events, property NAMES, file:line. Read-only.

Each repo is read at origin's default branch straight from git objects (`git fetch`, then
`git grep` / `git show` on the ref), so nothing is checked out and the working tree is untouched.

    python3 scan.py --out DIR [--repos Mobile/Aura,Services/UserService] [--no-fetch]
                    [--no-history] [--previous OLD_scan.json]

Writes DIR/scan.json. Property values are never read: only the keys of the literal map a call
passes. A map built elsewhere is recorded as unresolved, and counted, never dropped.
"""
import argparse
import datetime
import glob
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import guard  # noqa: E402

REFS = os.path.join(os.path.dirname(HERE), "references")
EXTS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".swift", ".kt", ".java", ".m", ".html", ".plist", ".xml", ".gradle")
CODE_EXTS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".swift", ".kt", ".java", ".m", ".html")
SKIP = re.compile(r"(^|/)(node_modules|Pods|build|dist|vendor|__tests__|__mocks__|test|tests|spec|specs|e2e|fixtures?)(/|$)|\.(test|spec)\.\w+$|Tests?\.swift$")
WINDOW = 4000  # characters read after a call's opening parenthesis


def load(name):
    with open(os.path.join(REFS, name), encoding="utf-8") as f:
        return json.load(f)


def git(repo, *args, check=True):
    p = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and p.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed in {os.path.basename(repo)}: {p.stderr.strip()[:200]}")
    return p.stdout


# ------------------------------------------------------------------ repos

def discover(base, spec, only=None):
    """[(name, path, conf)] for every clone the repos map names. A copy named Repo-suffix is skipped."""
    out = []
    for pattern, conf in spec.items():
        if pattern.startswith("_"):
            continue
        for path in sorted(glob.glob(os.path.join(base, pattern))):
            name = os.path.basename(path)
            if not os.path.isdir(os.path.join(path, ".git")) or "-" in name:
                continue
            url = git(path, "remote", "get-url", "origin", check=False).strip()
            if not re.search(rf"[/:]{re.escape(name)}(\.git)?$", url, re.I):
                continue
            rel = os.path.relpath(path, base)
            if only and rel not in only and name not in only:
                continue
            out.append((name, path, conf))
    return out


def default_ref(path, fetch=True):
    if fetch:
        git(path, "fetch", "--quiet", "origin")
    ref = git(path, "symbolic-ref", "--short", "refs/remotes/origin/HEAD", check=False).strip()
    if not ref:
        for cand in ("origin/main", "origin/master"):
            if git(path, "rev-parse", "--verify", "--quiet", cand, check=False).strip():
                return cand
        raise RuntimeError(f"no default branch for {os.path.basename(path)}")
    return ref


# ------------------------------------------------------------------ parsing

def _close(text, start):
    """Index just past the bracket that closes the one opened at text[start-1], or len(text)."""
    depth, i, quote = 1, start, None
    while i < len(text):
        c = text[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "'\"`":
            quote = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return len(text)


def split_args(text):
    """Top-level comma-separated pieces of an argument list."""
    parts, depth, quote, cur, i = [], 0, None, [], 0
    while i < len(text):
        c = text[i]
        if quote:
            cur.append(c)
            if c == "\\" and i + 1 < len(text):
                cur.append(text[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "'\"`":
            quote = c
            cur.append(c)
        elif c in "([{":
            depth += 1
            cur.append(c)
        elif c in ")]}":
            depth -= 1
            cur.append(c)
        elif c == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(c)
        i += 1
    if "".join(cur).strip():
        parts.append("".join(cur).strip())
    return parts


STR = re.compile(r"""^\s*(?:\w+:\s*)?(['"`])((?:\\.|(?!\1).)*)\1\s*$""")
ENTRY = re.compile(r"""^\s*(?:\[\s*)?(?:(['"])((?:\\.|(?!\1).)+)\1|([A-Za-z_$][\w$]*))\s*(?:\]\s*)?(?::(?!:)|$)""", re.S)


def literal_keys(text):
    """(keys, unresolved) of the first map literal in `text`: top-level keys only, never values."""
    m = re.search(r"[{\[]", text)
    if not m:
        return [], []
    end = _close(text, m.end())
    keys, unresolved = [], []
    for entry in split_args(text[m.end():end]):
        if entry.startswith("..."):
            unresolved.append(re.sub(r"\s+", " ", entry)[:60])
            continue
        e = ENTRY.match(entry)
        if not e:
            continue
        name = e.group(2) or e.group(3)
        if name and name not in keys:
            keys.append(name)
    return keys, unresolved


def local_map(lines, lineno, ident):
    """Keys of `ident = {...}` assigned in the 80 lines above, plus `ident["Key"] = ...` after it."""
    pat = re.compile(rf"\b(?:let|var|const)\s+{re.escape(ident)}\b[^=\n]*=\s*")
    sub = re.compile(rf"""\b{re.escape(ident)}\[\s*(['"])([^'"\n]+)\1\s*\]\s*=(?!=)""")
    for j in range(lineno - 1, max(-1, lineno - 81), -1):
        m = pat.search(lines[j])
        if not m:
            continue
        rest = "\n".join(lines[j:j + 60])
        after = rest[rest.find(m.group(0)) + len(m.group(0)):]
        if re.match(r"\s*[{\[]", after):
            keys, unresolved = literal_keys(after)
        else:
            call = re.match(r"\s*(?:self\.)?([A-Za-z_]\w*)\(", after)
            got = helper_map(lines, call.group(1)) if call else None
            keys, unresolved = got if got else ([], [re.sub(r"\s+", " ", after.split("\n")[0])[:60]])
        for k in range(j, lineno + 1):
            for s in sub.finditer(lines[k]):
                if s.group(2) not in keys:
                    keys.append(s.group(2))
        return keys, unresolved
    return None


def helper_map(lines, name):
    """Keys a same-file helper `func name(` / `function name(` returns in a literal map."""
    pat = re.compile(rf"\b(?:func|function)\s+{re.escape(name)}\s*\(|\b(?:const|let)\s+{re.escape(name)}\s*=\s*(?:async\s*)?\(")
    for j, line in enumerate(lines):
        if pat.search(line):
            body = lines[j:j + 80]
            text = "\n".join(body)
            m = re.search(r"\breturn\s+[\[{]|\b(?:let|var|const)\s+\w+[^=\n]*=\s*[\[{]", text)
            if not m:
                return None
            keys, unresolved = literal_keys(text[m.end() - 1:])
            for s in re.finditer(r"""\w\[\s*(['"])([^'"\n]+)\1\s*\]\s*=(?!=)""", text[:_close(text, text.find("{") + 1) if "{" in text else len(text)]):
                if s.group(2) not in keys:
                    keys.append(s.group(2))
            return keys, unresolved
    return None


def resolve_event(arg, consts, lines=None, i=0):
    if arg is None:
        return "<none>"
    arg = re.sub(r"^\s*\w+:\s*", "", arg)
    m = STR.match(arg)
    if m:
        if m.group(1) == "`" and "${" in m.group(2):
            return "<dynamic>"
        return m.group(2)
    m = re.match(r"^\s*(?:[\w$]+\.)*([\w$]+)\s*$", arg)
    if m and m.group(1) in consts:
        return consts[m.group(1)]
    if m and lines is not None:
        found = []
        pat = re.compile(rf"""(?<![\w$.]){re.escape(m.group(1))}\s*=\s*(['"])([^'"\n]+)\1""")
        for j in range(max(0, i - 30), i + 1):
            for a in pat.finditer(lines[j]):
                if a.group(2) not in found:
                    found.append(a.group(2))
        if found:
            return " | ".join(found)
    return "<dynamic>"


def constants(path, ref, files):
    """{NAME: "literal"} for constants defined once (Swift static let, JS const / object member)."""
    seen = {}
    pat = re.compile(r"""(?:static\s+let|const|let)\s+([A-Za-z_]\w*)\s*(?::\s*String)?\s*=\s*(['"])([^'"\n]{1,80})\2|^\s*([A-Z_][A-Z0-9_]+)\s*:\s*(['"])([^'"\n]{1,80})\5""", re.M)
    for f in files:
        for m in pat.finditer(f[1]):
            name, val = (m.group(1), m.group(3)) if m.group(1) else (m.group(4), m.group(6))
            seen.setdefault(name, set()).add(val)
    return {k: next(iter(v)) for k, v in seen.items() if len(v) == 1}


# ------------------------------------------------------------------ scan

def compile_sinks(sinks, repo, app):
    """Wrappers first: a wrapper call (ours) must not be read as the raw SDK call it resembles."""
    out = []
    for w in sinks["wrappers"]:
        if w.get("repo") == repo or w.get("app") == app:
            out.append({"re": re.compile(w["re"]), "kind": w["kind"], "vendors": w["vendors"], "adds": w["adds"],
                        "via": "wrapper", "event_arg": w.get("event_arg", 0), "skip_args": w.get("skip_args", 0),
                        "props_count": w.get("props_count"),
                        "skip_files": re.compile(w["skip_files"]) if w.get("skip_files") else None})
    for vendor, v in sinks["vendors"].items():
        for c in v["calls"]:
            out.append({"re": re.compile(c["re"]), "kind": c["kind"], "vendors": [vendor], "adds": [], "via": "direct",
                        "event_arg": 0, "skip_args": 0, "skip_files": None})
    return out


def source_files(path, ref, src):
    names = git(path, "ls-tree", "-r", "--name-only", ref, "--", *src, check=False).splitlines()
    return [n for n in names if n.endswith(EXTS) and not SKIP.search(n)]


def read_files(path, ref, names):
    """[(name, text)] via one `git cat-file --batch` (no checkout)."""
    if not names:
        return []
    p = subprocess.run(["git", "-C", path, "cat-file", "--batch"], input="".join(f"{ref}:{n}\n" for n in names).encode(),
                       capture_output=True)
    out, data, i = [], p.stdout, 0
    for n in names:
        nl = data.index(b"\n", i)
        header = data[i:nl].decode().split()
        if len(header) < 3 or header[1] != "blob":
            i = nl + 1
            continue
        size = int(header[2])
        out.append((n, data[nl + 1:nl + 1 + size].decode("utf-8", "replace")))
        i = nl + 1 + size + 1
    return out


def scan_repo(name, path, conf, sinks, fetch=True):
    ref = default_ref(path, fetch)
    sha = git(path, "rev-parse", ref).strip()
    files = read_files(path, ref, source_files(path, ref, conf["src"]))
    consts = constants(path, ref, [f for f in files if f[0].endswith(CODE_EXTS)])
    patterns = compile_sinks(sinks, name, conf["app"])
    calls, settings, masked = [], [], {}
    for fname, text in files:
        lines = text.split("\n")
        code = fname.endswith(CODE_EXTS)
        forwarders, skipped = [], set()
        in_block = None
        for i, line in enumerate(lines):
            in_block, commented = comment_state(line, in_block)
            if commented:
                skipped.add(i)
                continue
            if code:
                for p in patterns:
                    m = p["re"].search(line)
                    if not m or (p["skip_files"] and p["skip_files"].search(fname)):
                        continue
                    args = call_args(line[m.end():], lines, i)
                    fwd = forwarder(p, args, lines, i)
                    if fwd:
                        forwarders.append(fwd)
                    else:
                        calls.append(parse_call(p, args, consts, lines, i, fname, masked))
                    break
            for s in sinks["settings"]:
                m = re.search(s["re"], line)
                if m:
                    settings.append({"vendor": s["vendor"], "flag": s["flag"], "setting": m.group(0),
                                     "value": setting_value(m.group(0), lines, i), "file": f"{fname}:{i + 1}"})
        for fwd in forwarders:
            for i, line in enumerate(lines):
                m = fwd["re"].search(line)
                if not m or i == fwd["line"] or i in skipped:
                    continue
                calls.append(parse_call(fwd, call_args(line[m.end():], lines, i), consts, lines, i, fname, masked))
    return {"repo": name, "app": conf["app"], "ref": ref, "sha": sha[:12], "files": len(files),
            "calls": calls, "settings": settings, "masked": masked, "src": conf["src"], "path": path}


BLOCKS = {"/*": "*/", "<!--": "-->"}


def comment_state(line, in_block):
    """(in_block, skip): whether this line is commented out (//, #, *, a /* */ or <!-- --> block)."""
    s = line.strip()
    if in_block:
        return (None if BLOCKS[in_block] in s else in_block), True
    for start, end in BLOCKS.items():
        if s.startswith(start):
            return (None if end in s[len(start):] else start), True
    return None, bool(re.match(r"(//|#(?!import|if|else|endif)|\*)", s))


VALUE = r"(true|false|YES|NO|\d+(?:\.\d+)?)"


def setting_value(name, lines, i):
    """The literal a flag is set to: inline code (`name: false`), an Info.plist `<key>` followed by
    `<true/>`, or Android `<meta-data android:name=... android:value="false"/>`. None when not a literal."""
    here = lines[i]
    after = "\n".join(lines[i:i + 3])
    m = re.search(rf"{re.escape(name)}\W{{0,6}}\s*[:=(]?\s*{VALUE}\b", here + " " + (lines[i + 1] if i + 1 < len(lines) else ""))
    if m:
        return m.group(1)
    if "<key>" in here:
        m = re.search(r"</key>\s*<(true|false)\s*/>|</key>\s*<(?:string|integer)>\s*" + VALUE + r"\s*<", after)
        if m:
            return m.group(1) or m.group(2)
    m = re.search(r'android:value\s*=\s*"' + VALUE + '"', after.split("/>")[0])
    return m.group(1) if m else None


def call_args(rest, lines, i):
    tail = rest + "\n" + "\n".join(lines[i + 1:i + 80])
    return split_args(tail[:_close(tail[:WINDOW], 0)]) if not tail.lstrip().startswith(")") else []


FUNC = re.compile(r"""^\s*(?:static\s+)?([A-Za-z_$][\w$]*)\s*=\s*(?:useCallback\(\s*)?(?:async\s*)?\(([^)]*)\)\s*=>|\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:useCallback\(\s*)?(?:async\s*)?\(([^)]*)\)\s*=>|\bfunction\s+([A-Za-z_$][\w$]*)\s*\(([^)]*)\)|\bfunc\s+([A-Za-z_]\w*)\s*\(([^)]*)\)""")


def forwarder(p, args, lines, i):
    """A local function whose event-name parameter goes straight into this call: its call sites are the real events.

    `const logEvent = (event, data) => { Trackings(event, {...data}) }` returns a pattern for `logEvent(`
    with this call's vendors, and the literal keys this call adds."""
    if p["kind"] != "event" or not args:
        return None
    n = p.get("event_arg", 0)
    if len(args) <= n:
        return None
    ev = re.sub(r"^\s*\w+:\s*", "", args[n]).strip()
    if not re.match(r"^[A-Za-z_$][\w$]*$", ev):
        return None
    for j in range(i, max(-1, i - 15), -1):
        m = FUNC.search(lines[j])
        if not m:
            continue
        name = m.group(1) or m.group(3) or m.group(5) or m.group(7)
        params = [x.split(":")[0].strip().split()[-1] if ":" in x else x.strip()
                  for x in (m.group(2) or m.group(4) or m.group(6) or m.group(8) or "").split(",") if x.strip()]
        params = [re.sub(r"=.*", "", x).strip() for x in params]
        if ev not in params:
            return None
        adds = list(p["adds"])
        for a in args[n + 1:]:
            a = re.sub(r"^\w+:\s*", "", a)
            if a.startswith(("{", "[")):
                adds += [k for k in literal_keys(a)[0] if k not in adds]
        return {"re": re.compile(rf"(?:(?<=this\.)|(?<=self\.)|(?<![\w$.])){re.escape(name)}\("), "kind": "event", "vendors": p["vendors"],
                "adds": adds, "via": p["via"], "line": j, "event_arg": params.index(ev), "skip_args": 0, "skip_files": None}
    return None


def parse_call(p, args, consts, lines, i, fname, masked):
    keys, unresolved, event = [], [], None
    if p["kind"] == "event":
        n = p.get("event_arg", 0)
        event = resolve_event(args[n] if len(args) > n else None, consts, lines, i)
        rest = args[n + 1:]
    else:
        rest = args[p.get("skip_args", 0):]
    if p.get("props_count") is not None:
        rest = rest[:p["props_count"]]
    for a in rest:
        a = re.sub(r"^\w+:\s*", "", a)  # Swift labels (properties:, parameters:, metadata:)
        if re.match(r"^[{\[]", a):
            k, u = literal_keys(a)
            keys += [x for x in k if x not in keys]
            unresolved += u
        elif re.match(r"^(?:self\.)?[A-Za-z_]\w*\(", a) and p["kind"] != "id":
            got = helper_map(lines, re.match(r"^(?:self\.)?([A-Za-z_]\w*)", a).group(1))
            if got:
                keys += [x for x in got[0] if x not in keys]
                unresolved += got[1]
            else:
                unresolved.append(re.sub(r"\s+", " ", a)[:60])
        elif re.match(r"^[A-Za-z_$][\w$]*$", a) and p["kind"] != "id":
            got = local_map(lines, i, a)
            if got:
                keys += [x for x in got[0] if x not in keys]
                unresolved += got[1]
            elif a not in ("nil", "null", "undefined", "true", "false"):
                unresolved.append(a)
        elif a and p["kind"] != "id" and not STR.match(a):
            unresolved.append(re.sub(r"\s+", " ", a)[:60])
    if p["kind"] == "props" and args and STR.match(args[0]) and len(args) >= 2:
        keys = [STR.match(args[0]).group(2)] + [k for k in keys if k != STR.match(args[0]).group(2)]
    if p["kind"] == "id":
        keys = ["<identifier>"]
    return {"vendors": p["vendors"], "via": p["via"], "kind": p["kind"],
            "event": guard.mask(event, masked) if event else None,
            "properties": [guard.mask(k, masked) for k in keys + [a for a in p["adds"] if a not in keys]],
            "unresolved": [guard.mask(u, masked) for u in unresolved],
            "file": f"{fname}:{i + 1}"}


# ------------------------------------------------------------------ classify

def words(name):
    s = re.sub(r"^\$", "", name)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", s)
    return [w for w in re.split(r"[^A-Za-z0-9]+", s.lower()) if w]


def classify(name, classes):
    if name.startswith("<"):
        return "identifier" if name == "<identifier>" else "other"
    ws = words(name)
    joined = " " + " ".join(ws) + " "
    neutral = ws and ws[-1] in classes.get("neutral_last_words", [])
    for cls in classes["order"]:
        c = classes["classes"][cls]
        if neutral and cls in classes.get("neutral_applies_to", []):
            continue
        if name.lower() in c.get("exact", []) or " ".join(ws) in c.get("exact", []):
            return cls
        if any(f" {w} " in joined for w in c["words"]):
            return cls
    return "other"


def sensitive(cls, classes):
    return cls not in classes["not_sensitive"]


# ------------------------------------------------------------------ history

def history(path, ref, src, needle, cache):
    """(added, last_change) dates of `needle` in the source dirs, from git log -S."""
    key = f"{os.path.basename(path)}|{git(path, 'rev-parse', ref).strip()[:12]}|{needle}"
    if key not in cache:
        out = git(path, "log", "-S", needle, "--format=%as", "--reverse", ref, "--", *src, check=False).split()
        cache[key] = (out[0], out[-1]) if out else (None, None)
    return cache[key]


def build(repos, classes, with_history=True, previous=None, cache=None):
    rows, cache = [], ({} if cache is None else cache)
    for r in repos:
        for c in r["calls"]:
            for vendor in c["vendors"]:
                props = c["properties"] or [None]
                for prop in props:
                    cls = classify(prop, classes) if prop else None
                    rows.append({"repo": r["repo"], "app": r["app"], "vendor": vendor, "via": c["via"],
                                 "kind": c["kind"], "event": c["event"], "property": prop, "class": cls,
                                 "sensitive": bool(prop and sensitive(cls, classes)),
                                 "unresolved": c["unresolved"], "file": c["file"]})
    if with_history:
        for row in rows:
            if row["sensitive"] and row["property"] and not row["property"].startswith("<"):
                r = next(x for x in repos if x["repo"] == row["repo"])
                added, _ = history(r["path"], r["ref"], r["src"], row["property"], cache)
                row["added"] = added
    removed = []
    if previous:
        now = {(r["repo"], r["vendor"], r["event"], r["property"]) for r in rows}
        for old in previous.get("rows", []):
            k = (old["repo"], old["vendor"], old["event"], old["property"])
            if old.get("sensitive") and k not in now:
                r = next((x for x in repos if x["repo"] == old["repo"]), None)
                last = history(r["path"], r["ref"], r["src"], old["property"], cache)[1] if r and old["property"] else None
                removed.append({**old, "removed": last or "before this run"})
    return rows, removed


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--base", default=os.environ.get("AURA_REPOS_DIR"),
                    help="the folder holding Mobile/, Services/, Web/ and Functions/ (or set AURA_REPOS_DIR)")
    ap.add_argument("--repos", help="comma list: Mobile/Aura or a bare repo name")
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--no-history", action="store_true")
    ap.add_argument("--previous", help="an earlier scan.json, to date what was removed since")
    a = ap.parse_args()
    if not a.base:
        sys.exit("name where the repos are cloned: --base DIR or AURA_REPOS_DIR (the folder holding Mobile/, Services/, Web/, Functions/)")
    sinks, classes = load("sinks.json"), load("classes.json")
    only = set(a.repos.split(",")) if a.repos else None
    found = discover(a.base, sinks["repos"], only)
    if not found:
        sys.exit(f"no repos found under {a.base}")
    repos, errors = [], []
    for name, path, conf in found:
        try:
            repos.append(scan_repo(name, path, conf, sinks, fetch=not a.no_fetch))
            print(f"{name}: {len(repos[-1]['calls'])} calls, {len(repos[-1]['settings'])} settings", file=sys.stderr)
        except Exception as e:  # one repo failing never hides the others: it is listed as an error
            errors.append({"repo": name, "error": guard.mask(str(e))[:200]})
    previous = json.load(open(a.previous, encoding="utf-8")) if a.previous else None
    cache_path = os.path.join(a.out, "history-cache.json")
    cache = {}
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as f:
            cache = {k: tuple(v) for k, v in json.load(f).items()}
    rows, removed = build(repos, classes, not a.no_history, previous, cache)
    guard.write_json(cache_path, {k: list(v) for k, v in cache.items()})
    masked = {}
    for r in repos:
        for k, n in r["masked"].items():
            masked[k] = masked.get(k, 0) + n
    out = {"generated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d"),
           "repos": [{k: r[k] for k in ("repo", "app", "ref", "sha", "files")} | {"calls": len(r["calls"])} for r in repos],
           "errors": errors, "rows": rows, "removed": removed,
           "settings": [s | {"repo": r["repo"]} for r in repos for s in r["settings"]],
           "masked": masked,
           "unresolved_calls": sum(1 for r in repos for c in r["calls"] if c["unresolved"])}
    guard.write_json(os.path.join(a.out, "scan.json"), out)
    print(json.dumps({"repos": len(repos), "errors": len(errors), "rows": len(rows),
                      "sensitive": sum(r["sensitive"] for r in rows), "masked": masked}))


if __name__ == "__main__":
    main()
