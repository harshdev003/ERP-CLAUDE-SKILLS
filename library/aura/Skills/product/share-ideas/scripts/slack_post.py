#!/usr/bin/env python3
"""Canonical Slack posting helper for AINative skills.

This is the ONE place the org's Slack-posting *mechanics* live: post a message,
upload files, reply in a thread, fetch a permalink, resolve a channel id, search
messages, and build the ``Tools/Slack`` attribution footer. Skills that post to
Slack **copy this file into their own ``scripts/`` dir** (skills are
self-contained — AINative Architecture Principle 1); the canonical copy lives at
``plugins/shared/skills/post-to-slack/scripts/slack_post.py``. Edit the canonical
first, then propagate to the copies in the same PR (see AINative ``CLAUDE.md`` →
"Canonical source for ``slack_post.py``").

What this helper does NOT own: the **channel directory** and the **footer spec** —
both live in AI Knowledge → ``Tools/Slack``. This helper takes channel IDs and
footer fields as *inputs*; it never hardcodes a channel list.

Two footer fields are **auto-resolved** rather than taken from the caller, because a
model-typed value silently drifts or gets dropped: the skill's ``:<version>`` (from its
SKILL.md ``metadata.version``) and the ``who@surface`` runner tag (from the git identity
+ ``CLAUDE_CODE_ENTRYPOINT``). Pass an explicit value to override either, or ``""`` to
suppress it.

Two ways to use it
------------------
- **As a library** — ``from slack_post import post_message, get_permalink,
  upload_files, resolve_channel, search_messages, build_footer``. The public
  functions return the raw Slack response dict (check ``resp["ok"]`` yourself), so
  a caller looping over rows can keep going past a single failed call. This is the
  path migrated skills use (e.g. ``product:share-ideas``).
- **As a CLI** — ``python3 slack_post.py <post|upload|permalink|resolve-channel|
  search|footer> …``. The subcommands wrap the same functions and exit non-zero on
  any Slack error.

Transport
---------
HTTPS goes through ``requests`` (which bundles ``certifi``) when it's importable,
otherwise it falls back to ``curl`` via ``subprocess``. This resolves a
long-standing inconsistency across skills: some used curl ("urllib lacks the
macOS CA bundle"), others used requests ("never urllib/curl"). Both work — requests
is preferred for connection reuse and clean certs; curl is the dependency-free
fallback. ``urllib`` is deliberately NOT used: its default SSL context can't find
the system CA bundle on stock macOS Python, which is what bit the curl-using skills
in the first place.

Auth
----
- ``SLACK_BOT_TOKEN`` (``xoxb-…``) — bot token for post / upload / permalink /
  resolve-channel. The bot must be a member of the target channel.
- ``SLACK_USER_TOKEN`` (``xoxp-…``) — user token, REQUIRED for ``search`` only.
  ``search.messages`` rejects bot tokens with ``not_allowed_token_type``. It runs
  as the authorizing user, so it can see that user's DMs + private channels.

Tokens are read from the environment (or ``token=``/``--token``) and never printed.

MCP-first note
--------------
The repo default is MCP-first (best-practices principle 11). Prefer the Slack MCP
connector when it's available in the session; this script is the documented
*fallback* for when MCP can't do the job or isn't reachable (e.g. Cowork, cron).

Stdlib only, plus optional ``requests``. Forces UTF-8 stdout.
"""
from __future__ import annotations

import argparse
import datetime
import getpass
import glob
import json
import os
import re
import subprocess
import sys
import time
from typing import Any, Optional

SLACK_API = "https://slack.com/api"

# chat.postMessage accepts a longer body but splits it itself, mid-token — so a
# `<url|label>` straddling the boundary is torn into two unusable halves. We cut
# first, at a boundary of our choosing.
SLACK_TEXT_LIMIT = 4000

# chat.postMessage is rate-limited to roughly one message per channel per second,
# and chunking is what makes a burst likely — so the chunk loop paces itself and
# retries a `ratelimited` chunk rather than dropping it.
SLACK_CHUNK_PAUSE_SECONDS = 1.0
SLACK_CHUNK_RETRIES = 4

try:  # Preferred transport — bundles certifi, reuses connections.
    import requests  # type: ignore

    _HAVE_REQUESTS = True
except Exception:  # pragma: no cover - environment without requests
    requests = None  # type: ignore
    _HAVE_REQUESTS = False


# ---------- low-level transport ----------------------------------------------

def _today() -> str:
    return datetime.date.today().isoformat()


def _token(explicit: Optional[str], env_var: str = "SLACK_BOT_TOKEN") -> str:
    tok = explicit or os.environ.get(env_var)
    if not tok:
        sys.exit(f"{env_var} not set (pass --token or export it).")
    return tok


def _user_token(explicit: Optional[str]) -> str:
    """Resolve a USER token for as-user posting (explicit override or SLACK_USER_TOKEN).

    Guards the as-user promise: a bot token (``xoxb-…``) — even one passed via
    ``--token`` — would post as the app (with an "APP" badge), not the genuine user,
    so reject it rather than silently mis-attributing the message.
    """
    tok = _token(explicit, env_var="SLACK_USER_TOKEN")
    if tok.startswith("xoxb-"):
        sys.exit("--as-user needs a user token (SLACK_USER_TOKEN, xoxp-…); got a bot token (xoxb-…).")
    return tok


def _api(
    method: str,
    *,
    token: str,
    body: Optional[dict[str, Any]] = None,
    params: Optional[dict[str, Any]] = None,
    http: str = "POST",
) -> dict[str, Any]:
    """Call a Slack Web API method and return the parsed JSON dict.

    ``body`` is sent as a JSON POST payload; ``params`` as a URL query string (GET).
    Does not check ``ok`` — callers use ``_check`` (or inspect ``resp["ok"]``) when
    they want to react to a Slack-level error.
    """
    url = f"{SLACK_API}/{method}"

    if _HAVE_REQUESTS:
        headers = {"Authorization": f"Bearer {token}"}
        if http == "GET":
            resp = requests.get(url, headers=headers, params=params or {}, timeout=30)
        else:
            headers["Content-Type"] = "application/json; charset=utf-8"
            resp = requests.post(
                url, headers=headers,
                data=json.dumps(body or {}).encode("utf-8"), timeout=30,
            )
        try:
            return resp.json()
        except ValueError:
            sys.exit(f"Non-JSON response from {method}: {resp.text[:200]!r}")

    # curl fallback
    cmd = ["curl", "-sS", "-H", f"Authorization: Bearer {token}"]
    stdin: Optional[bytes] = None
    if http == "GET":
        cmd += ["-G", url]
        for key, value in (params or {}).items():
            cmd += ["--data-urlencode", f"{key}={value}"]
    else:
        cmd += [
            "-X", "POST", url,
            "-H", "Content-Type: application/json; charset=utf-8",
            "--data-binary", "@-",
        ]
        stdin = json.dumps(body or {}).encode("utf-8")
    try:
        result = subprocess.run(
            cmd, input=stdin, capture_output=True, check=True, timeout=30,
        )
    except subprocess.CalledProcessError as exc:
        sys.exit(f"curl failed for {method}: {exc.stderr.decode()}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        sys.exit(f"Non-JSON response from {method}: {result.stdout[:200]!r}")


def _check(resp: dict[str, Any], method: str) -> dict[str, Any]:
    if not resp.get("ok"):
        sys.exit(f"Slack {method} failed: {resp.get('error')}")
    return resp


def _put_file_bytes(upload_url: str, data: bytes) -> None:
    """POST raw file bytes to a Slack external-upload URL (step 2 of upload)."""
    if _HAVE_REQUESTS:
        resp = requests.post(upload_url, data=data, timeout=60)
        if resp.status_code != 200:
            sys.exit(f"Upload POST returned HTTP {resp.status_code}")
        return
    try:
        result = subprocess.run(
            ["curl", "-sS", "-X", "POST", "--data-binary", "@-",
             "-o", "/dev/null", "-w", "%{http_code}", upload_url],
            input=data, capture_output=True, check=True, timeout=60,
        )
    except subprocess.CalledProcessError as exc:
        sys.exit(f"curl upload failed: {exc.stderr.decode()}")
    # curl exits 0 on HTTP error responses, so check the status code explicitly.
    code = result.stdout.decode().strip()
    if code != "200":
        sys.exit(f"Upload POST returned HTTP {code}")


def _skill_md_path(plugin: str, skill: str) -> str:
    """Locate ``<plugin>:<skill>``'s SKILL.md *as the running host loaded it*.

    Resolves both layouts the helper actually runs under::

        in-repo    <repo>/plugins/<plugin>/skills/<skill>/SKILL.md
        installed  <cache>/<marketplace>/<plugin>/<version>/skills/<skill>/SKILL.md

    The cross-plugin hop is the case that matters: `eng:*` / `data:*` skills post via
    ``shared:post-to-slack``, so the running script sits in the **shared** plugin root
    while the footer names a skill in another plugin. Resolution therefore hops to the
    sibling plugin *within the same install root*, preferring a version segment
    matching this plugin's.

    A third layout exists and encodes plugin **names nowhere in the path** — Claude
    local-agent-mode sessions, where every plugin root is an opaque id::

        <session>/rpm/plugin_012wfr5LB97yGLz3yaNrqvnk   <- shared
        <session>/rpm/plugin_01SQmXX5J1pQ8ByojhrVuWyy   <- data

    Name-to-directory guessing cannot work there, so it is not attempted: the sibling
    ``manifest.json`` maps id to name, and ``_manifest_plugin_paths`` reads it. Before
    that was wired every footer posted from such a session silently lost its version
    ([ENG-1110](https://linear.app/aura-health/issue/ENG-1110)).

    Candidate roots are derived **only** from this script's own path and
    ``CLAUDE_PLUGIN_ROOT`` — never cwd, never a hardcoded repo path. A run executing
    from the install cache must not read a version out of a fresher git checkout:
    doing so is what let a stale 1.2.0 run stamp itself ``1.3.0`` and cost two days
    of misdiagnosis. Returns ``""`` when nothing resolves.
    """
    rel = os.path.join("skills", skill, "SKILL.md")
    # This file always sits at <plugin-root>/skills/<its-skill>/scripts/<this>.py.
    here = os.path.dirname(os.path.abspath(__file__))
    roots = [os.path.dirname(os.path.dirname(os.path.dirname(here)))]
    cpr = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if cpr:
        roots.append(os.path.abspath(cpr))

    seen: list = []
    for root in roots:
        # in-repo: `plugins/`. installed: `<marketplace>/<plugin>` (root is the version dir).
        siblings = os.path.dirname(root)
        versioned = glob.glob(os.path.join(os.path.dirname(siblings), plugin, "*", rel))
        # `False` sorts first, so a version segment matching ours wins.
        versioned.sort(key=lambda p: p.split(os.sep)[-4] != os.path.basename(root))
        for path in (os.path.join(root, rel),              # skill lives in the running plugin
                     os.path.join(siblings, plugin, rel),  # flat siblings (in-repo)
                     *versioned,                           # versioned siblings (installed)
                     # opaque-id siblings (local-agent-mode), resolved via manifest.json
                     *(os.path.join(p, rel) for p in _manifest_plugin_paths(siblings, plugin))):
            if path not in seen:
                seen.append(path)
    for path in seen:
        if os.path.isfile(path):
            return path
    return ""


def _manifest_plugin_paths(siblings: str, plugin: str) -> list:
    """Plugin roots under ``siblings`` whose ``manifest.json`` entry names ``plugin``.

    Claude local-agent-mode sessions lay plugins out as ``<siblings>/plugin_<opaque-id>``
    with a sibling ``manifest.json`` holding ``{"plugins": [{"id", "name", ...}]}``. The
    id is the only thing in the path, so the name has to come from the manifest.

    Returns every matching root rather than the first, because a name *can* repeat across
    marketplaces; the caller tries them in order and takes the first that has the skill.
    Any malformed/absent manifest yields ``[]`` — the footer then degrades to the bare id,
    which is the intended failure mode (a wrong version is worse than no version).
    """
    try:
        with open(os.path.join(siblings, "manifest.json"), encoding="utf-8") as fh:
            entries = json.load(fh).get("plugins", [])
    except (OSError, ValueError, AttributeError):
        return []
    return [os.path.join(siblings, e["id"])
            for e in entries
            if isinstance(e, dict) and e.get("name") == plugin and e.get("id")]


def _resolve_skill_version(skill_id: str, skill_md: Optional[str] = None) -> str:
    """Best-effort read of a skill's ``metadata.version`` from its SKILL.md.

    ``skill_id`` is ``<plugin>:<skill>``. ``skill_md`` is an explicit path to that
    skill's SKILL.md — pass it whenever you have one, because the caller *always*
    does: every skill invocation is handed ``Base directory for this skill: <path>``,
    so ``<base>/SKILL.md`` is known for free and needs no path guessing at all. When
    omitted, the file is located by ``_skill_md_path``.

    Returns ``""`` when it can't find/parse the file — so the footer degrades to the
    bare id rather than failing (``build_footer`` warns on stderr when that happens).
    Scoped to the ``metadata:`` block so a ``version:`` token in the description can't
    shadow it.
    """
    if not skill_id or ":" not in skill_id:
        return ""
    if skill_md:
        path = os.path.abspath(os.path.expanduser(skill_md))
        # A directory is accepted too: callers hold the *base dir*, not the file.
        if os.path.isdir(path):
            path = os.path.join(path, "SKILL.md")
        if not os.path.isfile(path):
            print(f"slack_post: warning: --footer-skill-md '{skill_md}' is not a "
                  f"readable SKILL.md — falling back to path resolution.",
                  file=sys.stderr)
            path = ""
    else:
        path = ""
    plugin, _, skill = skill_id.partition(":")
    path = path or _skill_md_path(plugin, skill)
    if not path:
        return ""
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        return ""
    fm = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    fm = fm.group(1) if fm else text
    block = re.search(r"(?m)^metadata:[ \t]*\n((?:[ \t]+\S.*\n?)+)", fm)
    if not block:
        return ""
    m = re.search(r'''(?m)^[ \t]+version:[ \t]*["']?([^"'\s]+)["']?[ \t]*$''', block.group(1))
    return m.group(1) if m else ""


def _git_config(key: str, *, scope: str = "--global") -> str:
    """Read one git config value, or ``""``.

    Defaults to ``--global`` **on purpose**: an unscoped ``git config --get`` resolves
    against the current working directory, so the same helper returned a different
    identity depending on where the process happened to start. A repo carrying a local
    ``user.email`` (e.g. a GitHub ``noreply`` address) answered one way from inside the
    checkout and another from a scratchpad or ``/tmp`` — and a scratchpad cwd is the
    normal case for a cadence sweep, so posts drifted between two spellings of the same
    person. Global config is a property of the operator, not of where they happened to
    be standing (ENG-1039).
    """
    try:
        out = subprocess.run(["git", "config", scope, "--get", key],
                             capture_output=True, text=True, timeout=5)
    except Exception:  # git missing, or hung
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def _sanitize_runner_part(value: str, *, allow_dots: bool) -> str:
    """Collapse a runner component to one safe footer token (or ``""``).

    Anything outside ``[A-Za-z0-9.+-]`` becomes ``-``, with two deliberate exclusions
    from that set:

    - **Underscore.** The footer is wrapped in a ``_…_`` italic span and the runner is
      *not* backticked, so an interior ``_`` closes the italic early — a plain-legal
      local-part or OS account like ``john_doe`` would break formatting on every post.
    - **Dot, in the surface half only.** Keeps the ``who@surface`` tag from looking like
      an email address to Slack's linkifier: ``jcurlier@claude-desktop`` stays literal
      text, where a dotted right-hand side would render as a mailto link.
    """
    keep = r"A-Za-z0-9.+\-" if allow_dots else r"A-Za-z0-9+\-"
    return re.sub(rf"[^{keep}]+", "-", value.strip()).strip("-")[:40]


def _normalize_runner(value: str) -> str:
    """Fold a caller-supplied runner through the same rules as the auto-resolved one.

    ``--footer-runner`` / ``AINATIVE_FOOTER_RUNNER`` must not be able to produce a footer
    the auto path would have prevented, so each half gets the same treatment: ``_`` folded
    everywhere (it closes the italic span) and dots folded on the surface side (they
    mailto-linkify). Split on the **last** ``@`` so an operator who mistakenly passes a
    real email address still has its domain sanitized as the surface half. A value with no
    ``@`` cannot linkify at all, so it only needs the underscore fold.
    """
    value = value.strip()
    if "@" not in value:
        return _sanitize_runner_part(value, allow_dots=True)
    who, _, surface = value.rpartition("@")
    who = _sanitize_runner_part(who, allow_dots=True)
    surface = _sanitize_runner_part(surface, allow_dots=False)
    return f"{who or 'unknown'}@{surface or 'unknown'}"


def _resolve_runner() -> str:
    """Resolve the ``<who>@<surface>`` tag for whoever is running this skill.

    Answers the question a versioned footer can't: *was this a person at the keyboard
    or an unattended loop?* — the first thing you want to know when a post looks wrong
    in a channel that mixes hand-run sweeps with cron ticks.

    - **who** — the git identity, because it is the only signal in a Claude Code
      session that names a *person* rather than a *machine*: the email local-part
      first (always a single handle, and the one that shows up in git log / on the
      PR), then ``user.name``, then the OS account as a last resort. A GitHub noreply
      address's numeric prefix (``12345+handle@…``) is dropped. Read from **global**
      git config, so the tag does not change with the process's working directory
      (ENG-1039) — a repo-local ``user.email`` override no longer leaks into it.
    - **surface** — ``CLAUDE_CODE_ENTRYPOINT`` (``cli``, ``claude-desktop``, cron, SDK).

    Deliberately excludes the hostname: it is machine-level rather than personal, and
    degrades to a random pod name under cron/Cowork — the surface already carries the
    signal that matters there. ``AINATIVE_FOOTER_RUNNER`` overrides the whole tag, for
    runtimes that know better than this heuristic (a named cron job, CI).
    """
    override = os.environ.get("AINATIVE_FOOTER_RUNNER", "").strip()
    if override:
        return _normalize_runner(override)

    who = ""
    email = _git_config("user.email")
    if "@" in email:
        who = _sanitize_runner_part(re.sub(r"^\d+\+", "", email.partition("@")[0]),
                                    allow_dots=True)
    if not who:
        who = _sanitize_runner_part(_git_config("user.name"), allow_dots=True)
    if not who:
        try:
            who = _sanitize_runner_part(getpass.getuser(), allow_dots=True)
        except Exception:  # no passwd entry and no USER/LOGNAME in env
            who = ""

    surface = _sanitize_runner_part(os.environ.get("CLAUDE_CODE_ENTRYPOINT", ""),
                                   allow_dots=False)
    if not who and not surface:
        return ""
    return f"{who or 'unknown'}@{surface or 'unknown'}"


class FooterVersionMismatch(ValueError):
    """An explicit ``--footer-version`` contradicts the skill's ``metadata.version``.

    A plain ``Exception`` subclass on purpose. ``build_footer`` is a public library
    entry point that ``post_message`` / ``upload_files`` sit on, and batch callers
    wrap each item in ``except Exception`` so one bad entry degrades that item rather
    than the sweep. Raising ``SystemExit`` would sail straight through those handlers
    and take the whole run down. The CLI turns it into a non-zero exit at the boundary.
    """


def build_footer(
    skill: str,
    date: Optional[str] = None,
    extra: Optional[str] = None,
    version: Optional[str] = None,
    runner: Optional[str] = None,
    skill_md: Optional[str] = None,
) -> str:
    """Build the ``Tools/Slack`` attribution footer line (Slack mrkdwn).

    Shape:
    ``_Generated by `<plugin>:<skill>[:<version>]` · <YYYY-MM-DD>[ · <runner>][ · <extra>]_``

    ``skill`` is the fully-qualified id (e.g. ``shared:post-to-slack``). ``version``
    is the running skill's semver: pass it explicitly to override, or leave it ``None``
    and it is **auto-resolved** from ``skill``'s SKILL.md ``metadata.version`` — so a
    caller that passes only ``--footer <plugin>:<skill>`` still gets the versioned
    ``<plugin>:<skill>:<version>`` id (e.g. ``data:investigate-user-rating:1.0.3``)
    deterministically, without the model having to reproduce it. Pass ``version=""`` to
    force no version.

    ``runner`` is the ``who@surface`` operator tag (e.g. ``jcurlier@claude-desktop``),
    auto-resolved by ``_resolve_runner`` on the same terms: explicit value wins,
    ``None`` resolves, ``""`` omits. Auto-resolved for the same reason the version is —
    a model-typed field silently drifts or gets dropped.

    ``skill_md`` is an explicit path to ``skill``'s SKILL.md (the file, or the base dir
    holding it). Prefer it over letting the path be guessed: the caller always knows its
    own base directory, and passing it keeps the version coming from the file rather than
    from the model retyping it — the property ``--footer-version`` gives up.

    ``extra`` is the trailing env/source-link slot (``prod``, a Linear link). The operator
    belongs in ``runner``, not here.
    """
    if version:
        # An explicit version SUPPRESSES the resolver, so a wrong value produces a
        # genuine, script-built footer wrapped around a version that never shipped —
        # strictly harder to spot than the dropped-version failure this flag exists to
        # work around, because nothing about it looks broken. Cross-check when we can
        # (ENG-1048's sibling, ENG-1038). When the SKILL.md is unreachable — the rpm
        # plugin-snapshot layout that motivates the flag — there is nothing to compare
        # against, so the explicit value stands.
        resolved = _resolve_skill_version(skill, skill_md)
        if resolved and resolved != version:
            raise FooterVersionMismatch(
                f"slack_post: refusing to post — --footer-version '{version}' "
                f"contradicts '{skill}' metadata.version '{resolved}'. Drop the flag "
                f"and let it auto-resolve, or pass the real version."
            )
    if version is None:
        version = _resolve_skill_version(skill, skill_md)
        if not version:
            # Never fail silently here: an unversioned footer previously read as "this
            # skill has no version" and invited the model to type a plausible one, which
            # is how a stale run came to claim a version it wasn't running.
            print(f"slack_post: warning: could not resolve metadata.version for "
                  f"'{skill}' — footer will omit the version. Pass an explicit "
                  f"--footer-version if you need one.", file=sys.stderr)
    if runner is None:
        runner = _resolve_runner()
    elif runner:
        runner = _normalize_runner(runner)
    ident = f"{skill}:{version}" if version else skill
    line = f"_Generated by `{ident}` · {date or _today()}"
    if runner:
        line += f" · {runner}"
    if extra:
        line += f" · {extra}"
    return line + "_"


# ---------- public API (import these from a consuming skill) -----------------

# A whole line in *bold* opens a section; a whole line in _italic_ opens a bucket
# inside one. A chunk should start at one of these, and never end on one.
_SECTION_LINE = re.compile(r"^\*[^\s*][^\n]*\*[ \t]*$", re.M)
_BUCKET_LINE = re.compile(r"^_[^\s_][^\n]*_[ \t]*$", re.M)
_HEADING_LINE = re.compile(r"^(?:\*[^\s*][^\n]*\*|_[^\s_][^\n]*_)[ \t]*$")


def _last_line_start(pattern: re.Pattern, window: str, floor: int) -> int:
    """Start of the last line in ``window`` matching ``pattern``, at or past ``floor``."""
    starts = [m.start() for m in pattern.finditer(window) if m.start() >= floor]
    return starts[-1] if starts else -1


def _pick_cut(window: str, limit: int) -> tuple[int, int]:
    """Return ``(cut, skip)``: the chunk is ``window[:cut]``, the next starts at ``cut + skip``.

    Best is the start of a section, then of a bucket, then a blank line, a line
    start (a bullet) and finally a space. A boundary in the first half of a chunk
    is rejected in favour of a finer one — one early break would otherwise waste
    most of the chunk.
    """
    for pattern in (_SECTION_LINE, _BUCKET_LINE):
        start = _last_line_start(pattern, window, max(1, int(limit * 0.5)))
        if start > 0:
            return start, 0
    for sep, floor in (("\n\n", 0.5), ("\n", 0.5), (" ", 0.9)):
        cut = window.rfind(sep)
        if cut >= limit * floor:
            return cut, len(sep)
    return limit, 0  # no usable separator — an unbroken run, cut it hard


def _unorphan(text: str, cut: int, skip: int) -> tuple[int, int]:
    """Move trailing heading lines off the end of a chunk and onto the next.

    A chunk ending on ``*Blocked [10]*`` reads as an empty section, with its
    content arriving in the next message.
    """
    head = text[:cut].rstrip()
    while True:
        start = head.rfind("\n") + 1
        if start == 0 or not _HEADING_LINE.match(head[start:]):
            return cut, skip
        head = head[:start].rstrip()
        cut, skip = start, 0


def split_for_slack(text: str, limit: int = SLACK_TEXT_LIMIT) -> list[str]:
    """Split ``text`` into chunks of at most ``limit`` characters.

    Slack splits an over-long body on its own, mid-token, which destroys a
    ``<url|label>`` link that happens to straddle the cut and leaves both halves
    rendering as raw text. So we cut first, at the latest *readable* boundary
    under the limit (see ``_pick_cut``), and never leave a section or bucket
    heading as the last line of a chunk.
    """
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    rest = text
    while len(rest) > limit:
        cut, skip = _unorphan(rest, *_pick_cut(rest[:limit], limit))
        head, rest = rest[:cut], rest[cut + skip:]
        chunks.append(head.rstrip())
        rest = rest.lstrip("\n")
    if rest.strip():
        chunks.append(rest)
    return chunks


def post_message(
    channel: str,
    text: str,
    *,
    token: Optional[str] = None,
    thread_ts: Optional[str] = None,
    unfurl: bool = False,
    username: Optional[str] = None,
    icon_url: Optional[str] = None,
    as_user: bool = False,
    footer_skill: Optional[str] = None,
    footer_extra: Optional[str] = None,
    footer_version: Optional[str] = None,
    footer_skill_md: Optional[str] = None,
    footer_runner: Optional[str] = None,
    date: Optional[str] = None,
    thread_overflow: bool = False,
) -> dict[str, Any]:
    """Post a message (chat.postMessage). Returns the raw Slack response.

    Three posting identities:

    - **default** — as the bot.
    - **``username``/``icon_url``** — as the bot with a custom name + avatar (needs
      the ``chat:write.customize`` scope). Still shows an "APP" badge — it only
      *looks* like a person.
    - **``as_user=True``** — as the **genuine user**, via ``SLACK_USER_TOKEN``. A user
      token on ``chat.postMessage`` posts as the authorizing user (real name +
      avatar, **no** "APP" badge). Mutually exclusive with ``username``/``icon_url``
      (the identity is the user's own). Prefer the user's Slack MCP connector when
      it's available; this token path is the fallback.

    Unfurl is OFF by default so links don't expand into preview cards. Pass
    ``footer_skill`` to append the Tools/Slack footer; its ``:<version>`` segment and
    the ``who@surface`` runner tag are auto-resolved unless ``footer_version`` /
    ``footer_runner`` say otherwise. The caller inspects
    ``resp["ok"]`` — this does not exit on a Slack-level error.

    A body over ``SLACK_TEXT_LIMIT`` is chunked by ``split_for_slack`` and posted as
    consecutive messages, so no chunk ever ends inside a link. The **first**
    response is returned: its ``ts`` is what a permalink or a follow-up
    ``thread_ts`` should anchor to, and Slack's own split would hand back the last
    one instead. Every chunk after the first is reported under ``"chunk_ts"``. Any
    footer is appended before the split, so it stays on the final chunk.

    By default the continuation chunks are top-level messages. ``thread_overflow``
    posts them as replies under the first chunk instead, so a long post stays one
    message plus its thread. With ``thread_ts`` every chunk is already in that
    thread, and the flag changes nothing.

    A chunk that fails stops the run, and the returned response is the **failure** —
    never the first chunk's success — so a caller checking ``resp["ok"]`` cannot read
    a partial post as a whole one. It carries ``partial``/``posted_chunks``/
    ``total_chunks`` and the ts of what did land, so the remains can be found and
    cleaned up. A ``ratelimited`` chunk is retried with backoff before that point:
    chunking is what creates the burst, so a chunk dropped to the rate limit would be
    a hole this function itself opened.
    """
    tok = _user_token(token) if as_user else _token(token)
    if footer_skill:
        text = text.rstrip() + "\n\n" + build_footer(
            footer_skill, date, footer_extra, footer_version, footer_runner,
            footer_skill_md)
    body: dict[str, Any] = {
        "channel": channel,
        "text": text,
        "unfurl_links": bool(unfurl),
        "unfurl_media": bool(unfurl),
    }
    if thread_ts:
        body["thread_ts"] = thread_ts
    # username/icon_url are the bot-impersonation path; with a user token the
    # identity is already the user's own, so they don't apply.
    if not as_user:
        if username:
            body["username"] = username
        if icon_url:
            body["icon_url"] = icon_url

    chunks = split_for_slack(text)
    if len(chunks) == 1:
        return _api("chat.postMessage", token=tok, body=body)

    first: dict[str, Any] = {}
    posted: list[str] = []
    for index, chunk in enumerate(chunks):
        if index:
            time.sleep(SLACK_CHUNK_PAUSE_SECONDS)
        chunk_body = {**body, "text": chunk}
        if index and thread_overflow and not thread_ts:
            chunk_body["thread_ts"] = first["ts"]
        resp = _post_chunk(tok, chunk_body)
        if not resp.get("ok"):
            if index == 0:
                return resp  # nothing posted, nothing to anchor to
            # Stop here: a later chunk posted after a gap would be out of order.
            # The caller's `resp["ok"]` must not read this as a clean post, so the
            # failure is returned — with what DID land, so it can be found.
            return {
                **resp, "ts": first["ts"], "chunk_ts": posted, "partial": True,
                "posted_chunks": index, "total_chunks": len(chunks),
            }
        if index == 0:
            first = resp
        else:
            posted.append(resp["ts"])
    first["chunk_ts"] = posted
    return first


def _post_chunk(token: str, body: dict[str, Any]) -> dict[str, Any]:
    """Post one chunk, retrying a `ratelimited` rejection with backoff.

    Slack allows roughly one `chat.postMessage` per channel per second. Chunking is
    what produces a burst in the first place, so a dropped chunk here would be a
    hole the split itself created — and a hole in a posted message has no shape to
    a reader. Any other Slack error is returned to the caller unretried.
    """
    for attempt in range(SLACK_CHUNK_RETRIES):
        resp = _api("chat.postMessage", token=token, body=body)
        if resp.get("ok") or resp.get("error") != "ratelimited":
            return resp
        time.sleep(2 ** attempt)
    return resp


def get_permalink(
    channel: str, message_ts: str, *, token: Optional[str] = None, as_user: bool = False
) -> dict[str, Any]:
    """Fetch a message permalink (chat.getPermalink). Returns the raw response.

    Pass ``as_user=True`` to resolve the token from ``SLACK_USER_TOKEN`` — match it to
    the token the message was posted with (a bot-token permalink call can fail after a
    user post if no bot token is set or the bot isn't in the channel).
    """
    tok = _user_token(token) if as_user else _token(token)
    return _api(
        "chat.getPermalink", token=tok, http="GET",
        params={"channel": channel, "message_ts": message_ts},
    )


def upload_files(
    channel: str,
    paths: list[str],
    *,
    token: Optional[str] = None,
    comment: Optional[str] = None,
    thread_ts: Optional[str] = None,
    footer_skill: Optional[str] = None,
    footer_extra: Optional[str] = None,
    footer_version: Optional[str] = None,
    footer_skill_md: Optional[str] = None,
    footer_runner: Optional[str] = None,
    date: Optional[str] = None,
) -> dict[str, Any]:
    """Upload file(s) via Slack's external-upload flow; return the final response.

    files.getUploadURLExternal → POST bytes → files.completeUploadExternal.
    (files.upload is deprecated and removed for new apps — do not use it.) A
    ``footer_skill`` (or a ``comment``) is sent as ``initial_comment``; a footer
    is appended even when no comment is given (one-footer-per-post rule).
    """
    tok = _token(token)
    files_payload = []
    for path in paths:
        if not os.path.exists(path):
            sys.exit(f"File not found: {path}")
        size = os.path.getsize(path)
        filename = os.path.basename(path)
        got = _check(
            _api(
                "files.getUploadURLExternal", token=tok, http="GET",
                params={"filename": filename, "length": size},
            ),
            "files.getUploadURLExternal",
        )
        with open(path, "rb") as handle:
            _put_file_bytes(got["upload_url"], handle.read())
        files_payload.append({"id": got["file_id"], "title": filename})

    body: dict[str, Any] = {"channel_id": channel, "files": files_payload}
    if comment or footer_skill:
        text = (comment or "").rstrip()
        if footer_skill:
            footer = build_footer(footer_skill, date, footer_extra, footer_version,
                                  footer_runner, footer_skill_md)
            text = f"{text}\n\n{footer}" if text else footer
        body["initial_comment"] = text
    if thread_ts:
        body["thread_ts"] = thread_ts
    return _api("files.completeUploadExternal", token=tok, body=body)


def resolve_channel(name: str, *, token: Optional[str] = None) -> Optional[str]:
    """Resolve a #channel name → channel ID via conversations.list (paginated).

    Returns the ID, or ``None`` if not found. Prefer the hardcoded C… ID from
    Tools/Slack; this is for channels not yet in that directory.
    """
    tok = _token(token)
    target = name.lstrip("#")
    cursor: Optional[str] = None
    while True:
        params: dict[str, Any] = {
            "limit": 1000,
            "types": "public_channel,private_channel",
        }
        if cursor:
            params["cursor"] = cursor
        resp = _check(
            _api("conversations.list", token=tok, params=params, http="GET"),
            "conversations.list",
        )
        for channel in resp.get("channels", []):
            if channel.get("name") == target:
                return channel["id"]
        cursor = (resp.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            return None


def search_messages(query: str, *, count: int = 20, token: Optional[str] = None) -> dict[str, Any]:
    """search.messages — USER token only (bot tokens are rejected). Raw response."""
    tok = _token(token, env_var="SLACK_USER_TOKEN")
    return _api(
        "search.messages", token=tok, http="GET",
        params={"query": query, "count": count},
    )


# ---------- CLI wrappers -----------------------------------------------------

def _read_text(args: argparse.Namespace) -> str:
    """Resolve message text from --text, --text-file, or stdin ('-')."""
    if args.text_file:
        with open(args.text_file, encoding="utf-8") as handle:
            return handle.read()
    if args.text == "-" or (args.text is None and not sys.stdin.isatty()):
        return sys.stdin.read()
    if args.text is None:
        sys.exit("Provide message text via --text, --text-file, or stdin ('-').")
    return args.text


def card_guard_errors(text: str, *, expect=None, expect_header=None, max_chars=None, max_chunks=None) -> list[str]:
    """Why this body must not be posted as the caller's card; empty means post.

    Each check catches a body the caller did not write: a missing entry id (`expect`), a first
    line that is not this card's header (`expect_header`), a body far longer than a card
    (`max_chars`), or one Slack would split into many replies (`max_chunks`). The 2026-10-27
    incident was a 69k-character guardrail transcript posted as 19 replies under a cancellation
    card's thread: any one of these would have stopped it.
    """
    errors = []
    missing = missing_expected(text, expect)
    if missing:
        errors.append(f"the body does not contain {', '.join(repr(m) for m in missing)}")
    first = next((l for l in text.splitlines() if l.strip()), "")
    for h in expect_header or []:
        if h and h not in first:
            errors.append(f"the first line does not contain {h!r} (it starts {first[:60]!r})")
    if max_chars and len(text) > max_chars:
        errors.append(f"the body is {len(text):,} characters, over --max-chars {max_chars:,}")
    if max_chunks:
        n = len(split_for_slack(text))
        if n > max_chunks:
            errors.append(f"the body would post as {n} replies, over --max-chunks {max_chunks}")
    return errors


def missing_expected(text: str, expect: list[str] | None) -> list[str]:
    """The --expect tokens the body does not contain.

    A worker that posts a body it did not write, because another worker reused the same scratch
    file, sends one entry's analysis into another entry's thread. The caller names something only
    its own body carries (the entry's redacted UID, its alert ts) and the post is refused when the
    body lacks it.
    """
    return [t for t in (expect or []) if t and t not in text]


def cmd_body_path(args: argparse.Namespace) -> int:
    """Print a fresh, private scratch path for a message body. Never reuse a fixed name."""
    import tempfile
    tag = re.sub(r"[^A-Za-z0-9._-]+", "-", args.tag or "body").strip("-")[:60] or "body"
    fd, path = tempfile.mkstemp(prefix=f"slack-{tag}-", suffix=".txt")
    os.close(fd)
    print(path)
    return 0


def cmd_post(args: argparse.Namespace) -> int:
    text = _read_text(args)
    errors = card_guard_errors(text, expect=getattr(args, "expect", None),
                               expect_header=getattr(args, "expect_header", None),
                               max_chars=getattr(args, "max_chars", None),
                               max_chunks=getattr(args, "max_chunks", None))
    if errors:
        print("Refusing to post: " + "; ".join(errors) + ". It may be another entry's body "
              "(a shared or stale --text-file). Nothing was sent.", file=sys.stderr)
        return 2
    resp = post_message(
        args.channel, text, token=args.token, thread_ts=args.thread_ts,
        unfurl=args.unfurl, username=args.username, icon_url=args.icon_url,
        as_user=args.as_user,
        footer_skill=args.footer, footer_extra=args.footer_extra,
        footer_version=args.footer_version, footer_runner=args.footer_runner,
        footer_skill_md=args.footer_skill_md,
        date=args.date, thread_overflow=args.thread_overflow,
    )
    if resp.get("partial"):
        # `_check` would exit on the error name alone and drop the rest. Some
        # chunks ARE on the channel: a caller told only "it failed" retries the
        # whole post and duplicates them. Print what landed, on stdout, then fail.
        print(json.dumps({
            "ok": False, "error": resp.get("error"), "partial": True,
            "channel": resp.get("channel", args.channel), "ts": resp["ts"],
            "chunk_ts": resp.get("chunk_ts", []),
            "posted_chunks": resp["posted_chunks"],
            "total_chunks": resp["total_chunks"],
        }))
        print(
            f"Slack chat.postMessage failed after {resp['posted_chunks']} of "
            f"{resp['total_chunks']} chunks: {resp.get('error')}. "
            f"Delete ts={resp['ts']} (and chunk_ts) before retrying — do not repost "
            f"blind, the landed chunks will duplicate.", file=sys.stderr,
        )
        return 1
    resp = _check(resp, "chat.postMessage")
    out = {"ok": True, "ts": resp["ts"], "channel": resp.get("channel", args.channel)}
    if resp.get("chunk_ts"):
        # `ts` is the FIRST chunk — thread replies and permalinks anchor there.
        out["chunk_ts"] = resp["chunk_ts"]
    if args.permalink:
        link = _check(
            get_permalink(out["channel"], out["ts"], token=args.token, as_user=args.as_user),
            "chat.getPermalink",
        )
        out["permalink"] = link.get("permalink", "")
    print(json.dumps(out))
    return 0


def cmd_upload(args: argparse.Namespace) -> int:
    done = _check(
        upload_files(
            args.channel, args.file, token=args.token, comment=args.comment,
            thread_ts=args.thread_ts, footer_skill=args.footer,
            footer_extra=args.footer_extra, footer_version=args.footer_version,
            footer_skill_md=args.footer_skill_md,
            footer_runner=args.footer_runner, date=args.date,
        ),
        "files.completeUploadExternal",
    )
    first = (done.get("files") or [{}])[0]
    print(json.dumps({
        "ok": True,
        "files": len(args.file),
        "permalink": first.get("permalink", ""),
    }))
    return 0


def cmd_permalink(args: argparse.Namespace) -> int:
    resp = _check(
        get_permalink(args.channel, args.ts, token=args.token, as_user=args.as_user),
        "chat.getPermalink",
    )
    print(resp.get("permalink", ""))
    return 0


def cmd_resolve_channel(args: argparse.Namespace) -> int:
    cid = resolve_channel(args.name, token=args.token)
    if not cid:
        sys.exit(f"Channel #{args.name.lstrip('#')} not found (does it exist? is the bot a member?).")
    print(cid)
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    resp = search_messages(args.query, count=args.count, token=args.token)
    if not resp.get("ok"):
        err = resp.get("error")
        if err == "not_allowed_token_type":
            sys.exit(
                "search.messages requires a USER token (SLACK_USER_TOKEN, xoxp-…); "
                "bot tokens are rejected."
            )
        sys.exit(f"Slack search.messages failed: {err}")
    matches = (resp.get("messages") or {}).get("matches", [])
    print(json.dumps(matches, indent=2))
    return 0


def cmd_footer(args: argparse.Namespace) -> int:
    print(build_footer(args.skill, args.date, args.extra, args.version, args.runner,
                       args.skill_md))
    return 0


def _add_footer_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--footer", metavar="SKILL",
        help="Append the Tools/Slack footer for this fully-qualified skill id "
             "(e.g. shared:post-to-slack).",
    )
    parser.add_argument(
        "--footer-extra", default=None,
        help="Optional env/user/source-link slot for the footer (e.g. prod, @jerome).",
    )
    parser.add_argument(
        "--footer-skill-md", default=None, metavar="PATH",
        help="Path to the footer skill's SKILL.md, or the base directory holding it. "
             "Prefer this over --footer-version: every skill invocation is handed its "
             "own base directory, so passing it keeps the version coming from the file "
             "instead of being retyped. Falls back to path resolution if unreadable.",
    )
    parser.add_argument(
        "--footer-version", default=None,
        help="The running skill's semver (its SKILL.md version:), appended to the "
             "footer skill id as <plugin>:<skill>:<version>. Last resort — prefer "
             "--footer-skill-md, which can't drift from the file.",
    )
    parser.add_argument(
        "--footer-runner", default=None,
        help="Operator tag for the footer (who@surface). Auto-resolved from the git "
             "identity + CLAUDE_CODE_ENTRYPOINT; pass '' to omit. Override the "
             "auto-resolution globally with AINATIVE_FOOTER_RUNNER.",
    )
    parser.add_argument(
        "--date", default=None,
        help="Footer date (YYYY-MM-DD). Defaults to today.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Canonical Slack posting helper.")
    parser.add_argument("--token", default=None, help="Override the token from env.")
    sub = parser.add_subparsers(dest="command", required=True)

    post = sub.add_parser("post", help="Post a message (chat.postMessage).")
    post.add_argument("--channel", required=True, help="Channel ID (C…) or name.")
    post.add_argument("--text", default=None, help="Message text, or '-' for stdin.")
    post.add_argument("--text-file", default=None, help="Read message text from a file.")
    post.add_argument("--thread-ts", default=None, help="Reply under this parent ts.")
    post.add_argument("--thread-overflow", action="store_true", help="Post chunks 2..N of an over-limit body as thread replies under chunk 1 (default: consecutive top-level messages).")
    post.add_argument("--unfurl", action="store_true", help="Enable link/media unfurling (off by default).")
    post.add_argument("--username", default=None, help="Post as this bot name (chat:write.customize; still shows an 'APP' badge).")
    post.add_argument("--icon-url", default=None, help="Avatar URL for the --username bot-impersonation path.")
    post.add_argument("--as-user", action="store_true", help="Post as the GENUINE user via SLACK_USER_TOKEN (no 'APP' badge). Mutually exclusive with --username/--icon-url.")
    post.add_argument("--permalink", action="store_true", help="Also fetch + return the message permalink.")
    post.add_argument("--expect", action="append", default=None, metavar="TOKEN",
                      help="Refuse to post unless the body contains TOKEN (repeatable): an id only this entry's body carries, e.g. its redacted UID.")
    post.add_argument("--expect-header", action="append", default=None, metavar="TEXT",
                      help="Refuse to post unless the body's first non-empty line contains TEXT (repeatable): the card's header and uid.")
    post.add_argument("--max-chars", type=int, default=None, help="Refuse a body longer than this (a card is ~6000).")
    post.add_argument("--max-chunks", type=int, default=None, help="Refuse a body Slack would split into more than this many replies.")
    _add_footer_flags(post)
    post.set_defaults(func=cmd_post)

    body = sub.add_parser("body-path", help="Print a fresh private scratch path for a message body (mkstemp).")
    body.add_argument("--tag", default="body", help="Readable part of the file name, e.g. <skill>-<entry id>.")
    body.set_defaults(func=cmd_body_path)

    upload = sub.add_parser("upload", help="Upload file(s) via the external-upload flow.")
    upload.add_argument("--channel", required=True, help="Channel ID (C…).")
    upload.add_argument("--file", action="append", required=True, metavar="PATH", help="File to upload (repeatable).")
    upload.add_argument("--comment", default=None, help="initial_comment posted with the file(s).")
    upload.add_argument("--thread-ts", default=None, help="Attach in this thread.")
    _add_footer_flags(upload)
    upload.set_defaults(func=cmd_upload)

    permalink = sub.add_parser("permalink", help="Get a message permalink (chat.getPermalink).")
    permalink.add_argument("--channel", required=True, help="Channel ID (C…).")
    permalink.add_argument("--ts", required=True, help="Message ts.")
    permalink.add_argument("--as-user", action="store_true", help="Resolve via SLACK_USER_TOKEN (match a message posted with --as-user).")
    permalink.set_defaults(func=cmd_permalink)

    resolve = sub.add_parser("resolve-channel", help="Resolve a #name → channel ID.")
    resolve.add_argument("--name", required=True, help="Channel name (with or without #).")
    resolve.set_defaults(func=cmd_resolve_channel)

    search = sub.add_parser("search", help="search.messages (SLACK_USER_TOKEN only).")
    search.add_argument("--query", required=True, help="Slack search query.")
    search.add_argument("--count", type=int, default=20, help="Max matches (default 20).")
    search.set_defaults(func=cmd_search)

    footer = sub.add_parser("footer", help="Print the Tools/Slack footer line.")
    footer.add_argument("--skill", required=True, help="Fully-qualified skill id.")
    footer.add_argument("--extra", default=None, help="env/user/source-link slot.")
    footer.add_argument("--skill-md", default=None, metavar="PATH", help="Path to the skill's SKILL.md (or its base dir); preferred over --version.")
    footer.add_argument("--version", default=None, help="Skill semver, appended as <plugin>:<skill>:<version>.")
    footer.add_argument("--runner", default=None, help="Operator tag (who@surface). Auto-resolved; '' to omit.")
    footer.add_argument("--date", default=None, help="YYYY-MM-DD (defaults to today).")
    footer.set_defaults(func=cmd_footer)

    return parser


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args()
    try:
        raise SystemExit(args.func(args))
    except FooterVersionMismatch as exc:
        # Library callers catch this and skip the item; at the CLI it is a hard stop,
        # because posting with a fabricated version is the thing we are preventing.
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
