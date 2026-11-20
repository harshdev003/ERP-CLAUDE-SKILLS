"""Claude's calls to Aura RTDB, GCP and the Aura API carry `AuraClaude/<who>/<plugin>:<skill>@<version> (session <id>)`.

A prod RTDB alert from our own session showed only `curl/8.7.1`, so attributing it took a transcript
search. The rule lives in more than one file (gcloud_sa.py's copies plus inline copies in scripts that
have no gcloud_sa.py), so every implementation is held to the same format here, and every script that
names an RTDB or GCP REST host, or calls the Aura API, must send it.
"""
from __future__ import annotations

import contextlib
import glob
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys
import types
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parents[1]
HOSTS = re.compile(r"firebaseio\.com|firebasedatabase\.app|identitytoolkit\.googleapis\.com|monitoring\.googleapis\.com")
AURA_API = re.compile(r"services(\.dev)?\.aurahealth\.io|(environ(\.get)?|getenv|_env)\(?\[?\(?[\"']AURA_[A-Z_]*(TOKEN|URL|BASE)[\"']")
HTTP = re.compile(r"urllib\.request|\brequests\.|\bcurl\b|fetch\(|axios")
FORMAT = re.compile(r"^AuraClaude/(?P<who>[A-Za-z0-9._-]+)/(?P<plugin>[a-z]+):(?P<skill>[a-z0-9-]+)@(?P<version>[0-9][^ ]*) \(session (?P<session>[A-Za-z0-9._-]+)\)$")


# The google-cloud helpers import these at module level, and CI installs no google libraries.
_GOOGLE = ("google", "google.auth", "google.cloud", "google.cloud.logging", "google.cloud.bigquery",
           "google.oauth2", "google.oauth2.service_account", "google.api_core", "google.api_core.exceptions",
           "google.api_core.gapic_v1", "google.api_core.gapic_v1.client_info")


class _ClientInfo:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Recorder:
    """Stands in for a google-cloud Client; keeps the kwargs it was built with."""
    built = []

    def __init__(self, *a, **kw):
        _Recorder.built.append(kw)


@contextlib.contextmanager
def _stub_google():
    """Install fake `google.*` modules for the load, then restore whatever was there."""
    saved = {n: sys.modules.get(n) for n in _GOOGLE}
    try:
        for n in _GOOGLE:
            mod = types.ModuleType(n)
            mod.__path__ = []
            if "." in n:
                setattr(sys.modules[n.rpartition(".")[0]], n.rpartition(".")[2], mod)
            sys.modules[n] = mod
        sys.modules["google.api_core.gapic_v1.client_info"].ClientInfo = _ClientInfo
        sys.modules["google.cloud.logging"].Client = _Recorder
        sys.modules["google.cloud.bigquery"].Client = _Recorder
        sa = sys.modules["google.oauth2.service_account"]
        sa.Credentials = type("Credentials", (), {"from_service_account_info": staticmethod(lambda *a, **k: "creds")})
        sys.modules["google.auth"].default = lambda *a, **k: ("creds", "adc-project")
        exc = sys.modules["google.api_core.exceptions"]
        for name in ("ResourceExhausted", "ServiceUnavailable", "DeadlineExceeded", "NotFound"):
            setattr(exc, name, type(name, (Exception,), {}))
        yield
    finally:
        for n, prev in saved.items():
            if prev is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = prev


def _load(path):
    """Load a script as its own module, with its directory importable and google stubbed."""
    name = f"ua_{abs(hash(path))}"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclasses look their module up here
    sys.path.insert(0, os.path.dirname(path))
    try:
        with _stub_google():
            spec.loader.exec_module(mod)
    finally:
        sys.path.remove(os.path.dirname(path))
    return mod


def _skill(path):
    skill = pathlib.Path(path).resolve().parents[1]
    plugin = json.loads((skill.parents[1] / ".claude-plugin/plugin.json").read_text())["name"]
    version = re.search(r'version:\s*"([^"]+)"', (skill / "SKILL.md").read_text().split("---", 2)[1]).group(1)
    return plugin, skill.name, version


IMPLEMENTATIONS = sorted(
    p for p in glob.glob(str(REPO / "plugins/**/scripts/*.py"), recursive=True)
    if re.search(r"^def user_agent\(", pathlib.Path(p).read_text(encoding="utf-8"), re.M))


class Format(unittest.TestCase):
    def test_every_implementation_names_its_own_skill(self):
        self.assertGreaterEqual(len(IMPLEMENTATIONS), 62)
        for path in IMPLEMENTATIONS:
            with self.subTest(path=os.path.relpath(path, REPO)), \
                    mock.patch.dict(os.environ, {"AINATIVE_SESSION_NAME": "PROD-6926 Security Review",
                                                 "AINATIVE_UA_WHO": "j.doe@example.com"}):
                m = FORMAT.match(_load(path).user_agent())
                self.assertIsNotNone(m)
                self.assertEqual((m["plugin"], m["skill"], m["version"]), _skill(path))
                self.assertEqual(m["session"], "PROD-6926-Security-Review")
                self.assertEqual(m["who"], "j.doe")


def _who_sources():
    out = {}
    for path in IMPLEMENTATIONS + sorted(glob.glob(str(REPO / "plugins/**/scripts/gcloud_sa.py"), recursive=True)):
        tree = ast.parse(pathlib.Path(path).read_text(encoding="utf-8"))
        fns = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_ua_who"]
        out[path] = ast.get_source_segment(pathlib.Path(path).read_text(encoding="utf-8"), fns[0]) if fns else None
    return out


SHELL_WHO = """$(printf %s "${AINATIVE_UA_WHO:-$(git config --global user.email || git config --global user.name || id -un)}" | sed -E 's/@.*//; s/^[0-9]+\\+//; s/[^A-Za-z0-9._-]+/-/g; s/^[-.]+|[-.]+$//g' | grep . || echo unknown)"""


class ShellWho(unittest.TestCase):
    """The doc curl lines resolve the person the way _ua_who() does."""

    def test_every_doc_curl_uses_the_one_expression(self):
        lines = [l for p in glob.glob(str(REPO / "plugins/**/*.md"), recursive=True)
                 for l in pathlib.Path(p).read_text(encoding="utf-8").splitlines() if 'curl -A "AuraClaude/' in l]
        self.assertGreaterEqual(len(lines), 23)
        for line in lines:
            self.assertIn(f'curl -A "AuraClaude/{SHELL_WHO}/', line)

    def test_it_matches_python(self):
        g = REPO / "plugins/eng/skills/investigate-conversation-guardrail/scripts/gcloud_sa.py"
        for value in ("j.doe@example.com", "12345+jdoe@users.noreply.github.com", "Jo Doe", "-x-"):
            with self.subTest(value=value), mock.patch.dict(os.environ, {"AINATIVE_UA_WHO": value}):
                shell = subprocess.run(["bash", "-c", f'printf %s "{SHELL_WHO}"'], capture_output=True, text=True).stdout
                self.assertEqual(shell, _load(str(g))._ua_who())


class NoWhoInPosts(unittest.TestCase):
    """Slack and the Incident Log name the skill, never the teammate (Security, 11-11)."""

    def test_python_strippers(self):
        for rel in ("plugins/eng/skills/monitor-firebase/scripts/check_networks.py",
                    "plugins/eng/skills/flag-suspicious-traffic/scripts/detect.py"):
            src = pathlib.Path(REPO / rel).read_text(encoding="utf-8")
            pat = re.search(r're\.sub\(r"(\^AuraClaude/[^"]+)", "AuraClaude/"', src)[1]
            with self.subTest(rel=rel):
                strip = lambda ua: re.sub(pat, "AuraClaude/", ua)
                self.assertEqual(strip("AuraClaude/jdoe/eng:x@1.0.0 (session s)"), "AuraClaude/eng:x@1.0.0 (session s)")
                self.assertEqual(strip("AuraClaude/eng:x@1.0.0 (session s)"), "AuraClaude/eng:x@1.0.0 (session s)")
                self.assertEqual(strip("Mozilla/5.0 (a/b:c)"), "Mozilla/5.0 (a/b:c)")

    CASES = [("AuraClaude/jdoe/eng:x@1.0.0 (session s)", "AuraClaude/eng:x@1.0.0 (session s)"),
             ("AuraClaude/jdoe/adhoc (session s)", "AuraClaude/adhoc (session s)"),
             ("AuraClaude/jdoe/adhoc:ENG-1 (session s)", "AuraClaude/adhoc:ENG-1 (session s)"),
             ("AuraClaude/eng:x@1.0.0 (session s)", "AuraClaude/eng:x@1.0.0 (session s)"),
             ("AuraClaude/adhoc:coach-list", "AuraClaude/adhoc:coach-list"),
             ("AuraClaude/adhoc (session s)", "AuraClaude/adhoc (session s)"),
             ("curl/8.7.1", "curl/8.7.1")]

    def test_every_stripper_cuts_the_person_from_every_form(self):
        found = 0
        for path in glob.glob(str(REPO / "plugins/**/scripts/*.*"), recursive=True):
            src = pathlib.Path(path).read_text(encoding="utf-8", errors="ignore")
            py = re.search(r're\.sub\(r"(\^AuraClaude/[^"]+)", "AuraClaude/"', src)
            js = re.search(r"const noWho = \(u\) => (?:String\(u\)|u)\.replace\(/(.+?)/, 'AuraClaude/'\)", src)
            for pat in [m[1].replace("\\/", "/") for m in (py, js) if m]:
                found += 1
                for ua, want in self.CASES:
                    with self.subTest(path=os.path.relpath(path, REPO), ua=ua):
                        self.assertEqual(re.sub(pat, "AuraClaude/", ua, count=1), want)
        self.assertEqual(found, 6)


class Who(unittest.TestCase):
    """The person who ran the call: a login name, never an email; "unknown" rather than a failure."""

    def _who(self, env=None, git=None, user=None):
        path = IMPLEMENTATIONS[0]
        m = _load(path)
        if hasattr(m._ua_who, "found"):
            del m._ua_who.found
        git = git or {}

        def run(argv, **kw):
            return subprocess.CompletedProcess(argv, 0, stdout=git.get(argv[-1], "") + "\n", stderr="")

        def getuser():
            if user is None:
                raise OSError("no user")
            return user

        clean = {k: v for k, v in os.environ.items() if k != "AINATIVE_UA_WHO"}
        with mock.patch.dict(os.environ, {**clean, **(env or {})}, clear=True), \
                mock.patch("subprocess.run", run), mock.patch("getpass.getuser", getuser):
            return FORMAT.match(m.user_agent())["who"]

    def test_every_copy_resolves_the_same_way(self):
        sources = _who_sources()
        self.assertEqual([p for p, s in sources.items() if s is None], [])
        self.assertEqual(len(set(sources.values())), 1, "the _ua_who copies differ")
        # A non-ASCII git name under a C or Windows locale must not raise: the helper never fails.
        self.assertIn('encoding="utf-8", errors="replace"', next(iter(sources.values())))

    def test_git_email_local_part(self):
        self.assertEqual(self._who(git={"user.email": "12345+jdoe@users.noreply.github.com"}), "jdoe")

    def test_override_wins_and_drops_a_domain(self):
        self.assertEqual(self._who(env={"AINATIVE_UA_WHO": "ci@example.com"}, git={"user.email": "a@b.c"}), "ci")

    def test_falls_back_to_git_name_then_os_account_then_unknown(self):
        self.assertEqual(self._who(git={"user.name": "Jo Doe"}), "Jo-Doe")
        self.assertEqual(self._who(user="jdoe"), "jdoe")
        self.assertEqual(self._who(), "unknown")

    def test_the_profiler_still_recognises_it_and_names_only_the_skill(self):
        ua = "AuraClaude/jdoe/eng:delete-coach@1.2.0 (session s)"
        for js in glob.glob(str(REPO / "plugins/eng/skills/profile-firebase-rtdb/scripts/*.js")):
            src = pathlib.Path(js).read_text(encoding="utf-8")
            found = re.search(r"const OURS = /(.+)/;", src)
            if found:
                with self.subTest(js=os.path.basename(js)):
                    self.assertEqual(re.match(found[1].replace("\\/", "/"), ua)[1], "eng:delete-coach")
                    self.assertEqual(re.match(found[1].replace("\\/", "/"), "AuraClaude/eng:x@1 (session s)")[1], "eng:x")


class Gate(unittest.TestCase):
    def test_scripts_naming_an_rtdb_or_gcp_host_send_it(self):
        found = []
        for path in glob.glob(str(REPO / "plugins/**/scripts/*"), recursive=True):
            if not path.endswith((".py", ".sh", ".js", ".mjs")):
                continue
            text = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
            if HOSTS.search(text):
                found.append(path)
                with self.subTest(path=os.path.relpath(path, REPO)):
                    self.assertRegex(text, r"user_agent\(\)|--user-agent")
        self.assertGreaterEqual(len(found), 4)

    def test_scripts_calling_the_aura_api_send_it(self):
        """A script that names the Aura API host or reads an Aura token, and makes HTTP calls, sends it."""
        found = []
        for path in glob.glob(str(REPO / "plugins/**/scripts/*"), recursive=True):
            if not path.endswith((".py", ".sh", ".js", ".mjs")):
                continue
            text = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
            if AURA_API.search(text) and HTTP.search(text):
                found.append(path)
                with self.subTest(path=os.path.relpath(path, REPO)):
                    self.assertRegex(text, r"user_agent\(\)|--user-agent")
        # A floor that the glob still finds the callers. ENG-1657 moved the llmCall ledger readers
        # to BigQuery, which took ~25 scripts off the Aura API.
        self.assertGreaterEqual(len(found), 35)


CLIENT = re.compile(r"\b(?:cloud_logging|logging|bigquery|storage|firestore|pubsub_v1|monitoring_v3)\.[A-Za-z]*Client\(")
LABELLED = "client_info=ClientInfo(user_agent=user_agent())"


class ClientLibraries(unittest.TestCase):
    def test_every_google_cloud_client_is_built_with_the_label(self):
        found = 0
        for path in glob.glob(str(REPO / "plugins/**/scripts/*.py"), recursive=True):
            for n, line in enumerate(pathlib.Path(path).read_text(encoding="utf-8").splitlines(), 1):
                if CLIENT.search(line):
                    found += 1
                    with self.subTest(at=f"{os.path.relpath(path, REPO)}:{n}"):
                        self.assertIn(LABELLED, line)
        self.assertGreaterEqual(found, 80)

    def _built(self, call):
        _Recorder.built.clear()
        with _stub_google(), mock.patch.dict(os.environ, {"GCP_KEY": json.dumps({"project_id": "p", "client_email": "e"})}):
            call()
        self.assertTrue(_Recorder.built)
        for kw in _Recorder.built:
            self.assertRegex(kw["client_info"].user_agent, FORMAT)

    def test_each_family_passes_it(self):
        skills = REPO / "plugins/eng/skills"
        for rel, call in (
            ("investigate-gcp-logs/scripts/gcp.py", lambda m: m.get_client()),
            ("analyze-livekit-usage/scripts/livekit_core.py", lambda m: m.get_client()),
            ("store-aurie-user-daily-cost/scripts/livekit_minutes.py", lambda m: m.get_client()),
            ("flag-suspicious-traffic/scripts/detect.py", lambda m: m.client_for(None)),
            ("query-bigquery/scripts/bq.py", lambda m: m.client()),
            ("report-aurie-weekly-costs/scripts/bq.py", lambda m: m.client()),
        ):
            with self.subTest(rel=rel):
                m = _load(str(skills / rel))
                self._built(lambda: call(m))


class Sent(unittest.TestCase):
    """The header reaches the request, not just the helper."""

    def _capture(self):
        seen = []

        class Resp:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b"{}"

        def fake(req, *a, **k):
            seen.append(req.get_header("User-agent"))
            return Resp()
        return seen, mock.patch("urllib.request.urlopen", fake)

    def _script(self, rel):
        return _load(str(REPO / "plugins/eng/skills" / rel))

    def test_audit_coach_payout(self):
        m = self._script("audit-coach-payout/scripts/audit.py")
        seen, patch = self._capture()
        with patch, mock.patch("json.load", lambda r: {}):
            m.request_json("https://auratech16.firebaseio.com/x.json")
        self.assertRegex(seen[0], FORMAT)

    def test_fb_deletion(self):
        m = self._script("delete-facebook-user-data/scripts/fb_deletion.py")
        seen, patch = self._capture()
        with patch, mock.patch.object(m, "_gcp_token", lambda: "t"):
            m._idtk("accounts:lookup", {})
            m._aura("GET", "/x", "t")
        self.assertEqual(len(seen), 2)
        for ua in seen:
            self.assertRegex(ua, FORMAT)

    def test_orphan_cleanup(self):
        m = self._script("cleanup-orphaned-auth/scripts/orphan_cleanup.py")
        seen, patch = self._capture()
        with patch, mock.patch.object(m, "_aura_token", lambda: "t"):
            m._post("https://identitytoolkit.googleapis.com/v1/x", {})
            m._has_profile("u")
            m._email_resolves("a@example.com")
        self.assertEqual(len(seen), 3)
        for ua in seen:
            self.assertRegex(ua, FORMAT)


class DocCurls(unittest.TestCase):
    def test_every_doc_curl_to_the_aura_api_sends_it(self):
        """A curl in a SKILL.md or reference that calls the Aura API passes `-A`."""
        found = []
        for path in glob.glob(str(REPO / "plugins/**/*.md"), recursive=True):
            lines = pathlib.Path(path).read_text(encoding="utf-8").split("\n")
            i = 0
            while i < len(lines):
                if re.search(r"(^\s*|\$\()curl\b", lines[i]):
                    j = i
                    while lines[j].rstrip().endswith("\\") and j + 1 < len(lines):
                        j += 1
                    block = "\n".join(lines[i:j + 1])
                    if AURA_API.search(block) or "$AURA_API_TOKEN" in block:
                        found.append(path)
                        with self.subTest(at=f"{os.path.relpath(path, REPO)}:{i + 1}"):
                            self.assertRegex(block, r"curl -A \"(AuraClaude/|\$\(python3 [^)]*--user-agent\))")
                    i = j + 1
                else:
                    i += 1
        self.assertGreaterEqual(len(found), 20)


class SentToAuraApi(unittest.TestCase):
    """The Aura API readers put it on the request itself."""

    def _requests(self, m, payload=()):
        seen = []

        class Resp:
            status_code = 200
            text = "[]"
            def json(self): return list(payload) if isinstance(payload, tuple) else payload

        def get(url, headers=None, **k):
            seen.append((headers or {}).get("User-Agent"))
            return Resp()
        return seen, mock.patch.object(m.requests, "get", get)

    def test_responses_reader_ledger_no_longer_calls_the_aura_api(self):
        """The ledger moved to BigQuery (ENG-1657), whose client carries the label
        (`ClientLibraries` below). No HTTP call is left to label."""
        for path in sorted(glob.glob(str(REPO / "plugins/**/scripts/responses_reader.py"), recursive=True)):
            with self.subTest(path=os.path.relpath(path, REPO)):
                m = _load(path)
                seen, patch = self._requests(m)
                with patch, mock.patch.object(m.llm_ledger, "rows", lambda params: []):
                    m.fetch_ledger_rows("u")
                    m.fetch_ledger_rows_by_conversation("c")
                self.assertEqual(seen, [])

    def test_fetch_conversation_user_lookup_without_a_token(self):
        for path in sorted(glob.glob(str(REPO / "plugins/**/scripts/fetch_conversation.py"), recursive=True)):
            with self.subTest(path=os.path.relpath(path, REPO)):
                m = _load(path)
                seen, patch = self._requests(m, payload={})
                with patch, mock.patch.dict(os.environ, {}, clear=True):
                    m.fetch_aura_user("u")
                self.assertRegex(seen[0], FORMAT)


if __name__ == "__main__":
    unittest.main()


# MARK: - Per call site
#
# The file-level gate above passes a script that sends the header on one Aura call and not on the
# next. This one checks every request whose URL is an Aura API URL (a literal host, or a name bound
# to one) carries `User-Agent`: in the call's own headers, in the headers variable it passes, or
# through `add_header` on the Request it builds.

import ast  # noqa: E402

AURA_HOST = re.compile(r"services(\.dev)?\.aurahealth\.io")
SENDERS = {("requests", "get"), ("requests", "post"), ("requests", "put"), ("requests", "patch"),
           ("requests", "delete"), ("requests", "request")}


def _aura_names(tree, src):
    """Module-level names bound to an Aura API URL (or to the env var that holds one)."""
    names = set()
    top = [n for n in tree.body if isinstance(n, ast.Assign)]
    for _ in range(2):  # one level of derivation: USERS_URL = f"{AURA_API}/users"
        for node in top:
            text = ast.get_source_segment(src, node.value) or ""
            if AURA_HOST.search(text) or re.search(r"[\"']AURA_[A-Z_]*(URL|BASE)[\"']", text) or \
                    any(re.search(rf"\b{re.escape(n)}\b", text) for n in names):
                names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    return names


def _sets_ua(text):
    return bool(re.search(r"User-Agent|user_agent\(", text))


def _mentions(text, names):
    return bool(AURA_HOST.search(text) or any(re.search(rf"\b{re.escape(n)}\b", text) for n in names))


def _url_arg(node, is_request_func):
    """The URL expression of a call: `url=` keyword, else positional (2nd for requests.request)."""
    for k in node.keywords:
        if k.arg == "url":
            return k.value
    i = 1 if is_request_func else 0
    return node.args[i] if len(node.args) > i else None


def _var_has_ua(fn, src, name, before):
    """Does variable `name`, as bound most recently before line `before` in `fn`, carry a User-Agent?"""
    binds, adds = [], []
    for n in ast.walk(fn):
        if getattr(n, "lineno", before) >= before:
            continue
        if isinstance(n, (ast.Assign, ast.AnnAssign)):
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            for t in targets:
                if isinstance(t, ast.Name) and t.id == name and n.value is not None:
                    binds.append((n.lineno, ast.get_source_segment(src, n.value) or ""))
                if isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name) and t.value.id == name:
                    adds.append((n.lineno, ast.get_source_segment(src, t) or ""))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name) \
                and n.func.value.id == name and n.func.attr in ("update", "add_header", "setdefault"):
            adds.append((n.lineno, ast.get_source_segment(src, n) or ""))
    last = max(binds, default=(0, ""))
    return _sets_ua(last[1]) or any(_sets_ua(t) for ln, t in adds if ln >= last[0])


def missing_user_agent(src):
    """Line numbers of Aura API calls in `src` that send no User-Agent."""
    tree = ast.parse(src)
    aura = _aura_names(tree, src)
    funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    parent = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}

    def scope(node):
        best = None
        for f in funcs:
            if f.lineno <= node.lineno <= (f.end_lineno or f.lineno) and (best is None or f.lineno > best.lineno):
                best = f
        return best

    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        key = (f.value.id, f.attr) if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) else None
        is_request = isinstance(f, ast.Attribute) and f.attr == "Request" or isinstance(f, ast.Name) and f.id == "Request"
        if key not in SENDERS and not is_request:
            continue
        url_node = _url_arg(node, key == ("requests", "request"))
        if url_node is None:
            continue
        fn = scope(node)
        url = ast.get_source_segment(src, url_node) or ""
        is_aura = _mentions(url, aura)
        if not is_aura and isinstance(url_node, ast.Name) and fn is not None:
            # a local bound in the same function: url = f"{AURA_API}/users/{uid}"
            for a in ast.walk(fn):
                if isinstance(a, ast.Assign) and any(isinstance(t, ast.Name) and t.id == url_node.id for t in a.targets) \
                        and a.lineno < node.lineno and _mentions(ast.get_source_segment(src, a.value) or "", aura):
                    is_aura = True
        if not is_aura:
            continue
        if _sets_ua(ast.get_source_segment(src, node) or ""):
            continue
        scope_node = fn or tree
        hdr = next((k.value for k in node.keywords if k.arg == "headers"), None)
        if isinstance(hdr, ast.Name) and _var_has_ua(scope_node, src, hdr.id, node.lineno):
            continue
        if is_request:
            # the Request's own variable, labelled after it is built: req.add_header("User-Agent", ...)
            p = parent.get(node)
            target = p.targets[0].id if isinstance(p, ast.Assign) and isinstance(p.targets[0], ast.Name) else None
            if target:
                # only add_header calls between this binding and the next rebinding of the same name
                rebinds = [n.lineno for n in ast.walk(scope_node) if isinstance(n, ast.Assign) and n.lineno > node.lineno
                           and any(isinstance(t, ast.Name) and t.id == target for t in n.targets)]
                until = min(rebinds, default=10**9)
                if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "add_header"
                       and isinstance(n.func.value, ast.Name) and n.func.value.id == target
                       and node.lineno < n.lineno < until and _sets_ua(ast.get_source_segment(src, n) or "")
                       for n in ast.walk(scope_node)):
                    continue
        bad.append(node.lineno)
    return bad


def sessions_without_user_agent(src):
    """`requests.Session()` objects in `src` that never get a User-Agent header."""
    tree = ast.parse(src)
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and \
                (ast.get_source_segment(src, node.value.func) or "").endswith("requests.Session"):
            for t in node.targets:
                name = ast.get_source_segment(src, t) or ""
                if not re.search(rf"{re.escape(name)}\.headers(\[[\"']User-Agent|\.update\([^)]*(User-Agent|user_agent\())", src, re.S):
                    bad.append(node.lineno)
    return bad


class PerCallSite(unittest.TestCase):
    def test_every_aura_api_call_sends_it(self):
        checked = 0
        for path in glob.glob(str(REPO / "plugins/**/scripts/*.py"), recursive=True):
            src = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
            if not (AURA_HOST.search(src) or re.search(r"[\"']AURA_[A-Z_]*(URL|BASE)[\"']", src)):
                continue
            checked += 1
            with self.subTest(path=os.path.relpath(path, REPO)):
                self.assertEqual(missing_user_agent(src), [], "Aura API call without a User-Agent")
        self.assertGreaterEqual(checked, 40)

    def test_every_aura_session_sets_it(self):
        checked = 0
        for path in glob.glob(str(REPO / "plugins/**/scripts/*.py"), recursive=True):
            src = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
            if "requests.Session" not in src or not (AURA_HOST.search(src) or re.search(r"[\"']AURA_[A-Z_]*(TOKEN|URL|BASE)[\"']", src)):
                continue
            checked += 1
            with self.subTest(path=os.path.relpath(path, REPO)):
                self.assertEqual(sessions_without_user_agent(src), [], "Aura session without a User-Agent")
        self.assertGreaterEqual(checked, 5)

    def test_the_session_detector_finds_a_bare_session(self):
        good = 's = requests.Session()\ns.headers["User-Agent"] = user_agent()\n'
        also = 'session = requests.Session()\nsession.headers.update({"Authorization": t, "User-Agent": user_agent()})\n'
        bare = 'session = requests.Session()\nsession.headers.update({"Authorization": t})\n'
        self.assertEqual(sessions_without_user_agent(good), [])
        self.assertEqual(sessions_without_user_agent(also), [])
        self.assertEqual(sessions_without_user_agent(bare), [1])

    def test_the_detector_finds_a_bare_call_next_to_a_good_one(self):
        """The shape PROD-7898 found: one call labelled, the next one bare (python-requests UA)."""
        src = '''
import requests
LLM_CALLS_URL = "https://services.aurahealth.io/ai/llm-calls"
def user_agent(): return "AuraClaude/x"
def good(token):
    return requests.get(LLM_CALLS_URL, headers={"Authorization": token, "User-Agent": user_agent()})
def bad(token):
    headers = {"Authorization": f"Bearer {token}"}
    return requests.get(LLM_CALLS_URL, headers=headers, params={"limit": 1000})
'''
        self.assertEqual(missing_user_agent(src), [9])

    def test_the_detector_reads_the_url_from_the_right_argument(self):
        src = '''
import requests
BASE = "https://services.aurahealth.io"
def a(t):
    return requests.request("GET", BASE + "/users", headers={"Authorization": t})
def b(t):
    return requests.get(url=BASE + "/users", headers={"Authorization": t})
'''
        self.assertEqual(missing_user_agent(src), [5, 7])

    def test_one_labelled_call_does_not_cover_a_bare_one_in_the_same_function(self):
        src = '''
import requests, urllib.request
BASE = "https://services.aurahealth.io"
def both(t):
    h = {"Authorization": t, "User-Agent": user_agent()}
    requests.get(BASE + "/a", headers=h)
    h = {"Authorization": t}
    requests.get(BASE + "/b", headers=h)
    good = urllib.request.Request(BASE + "/c")
    good.add_header("User-Agent", user_agent())
    bare = urllib.request.Request(BASE + "/d")
    return good, bare
'''
        self.assertEqual(missing_user_agent(src), [8, 11])

    def test_a_labelled_request_does_not_cover_a_rebuilt_bare_one(self):
        src = '''
import urllib.request
BASE = "https://services.aurahealth.io"
def f():
    req = urllib.request.Request(BASE + "/a")
    req.add_header("User-Agent", user_agent())
    urllib.request.urlopen(req)
    req = urllib.request.Request(BASE + "/b")
    return urllib.request.urlopen(req)
'''
        self.assertEqual(missing_user_agent(src), [8])

    def test_headers_variable_and_add_header_count(self):
        src = '''
import requests, urllib.request
BASE = "https://services.aurahealth.io"
def a(t):
    h = {"Authorization": t, "User-Agent": user_agent()}
    return requests.get(f"{BASE}/users", headers=h)
def b():
    req = urllib.request.Request(BASE + "/users/1")
    req.add_header("User-Agent", user_agent())
    return urllib.request.urlopen(req)
'''
        self.assertEqual(missing_user_agent(src), [])
