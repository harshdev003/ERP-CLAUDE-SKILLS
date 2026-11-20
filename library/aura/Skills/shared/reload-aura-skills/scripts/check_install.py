#!/usr/bin/env python3
"""Verify that installed Claude Code plugins match their marketplace clone.

Why this exists
---------------
``/plugin install`` reports success whether or not it copied anything — an install
that skipped the copy is indistinguishable from one that worked. ``installed_plugins.json``
records a ``gitCommitSha`` per plugin and the marketplace clone has a HEAD, so
comparing them answers "did the install actually take?" deterministically.

Both links in the chain are checked. Installed-vs-clone alone is not enough: a clone
that was never refreshed matches a stale install perfectly, so the clone is also
compared against its remote.

Layouts
-------
::

    <config>/plugins/installed_plugins.json   per-plugin installPath + gitCommitSha
    <config>/plugins/known_marketplaces.json  marketplace -> clone installLocation
    <config>/plugins/cache/<marketplace>/<plugin>/<version>/   the installed copy

``<config>`` is ``$CLAUDE_CONFIG_DIR`` when set, else ``~/.claude``.

Commands
--------
``check`` (default)
    Print a per-plugin table and exit on the verdict so a caller can branch::

        current         0   installed == clone == remote. Nothing to do.
        stale           1   installed predates the clone. Wipe, reinstall, re-check.
        clone-behind    1   the CLONE is behind its remote. Refresh the marketplace
                            first — wiping now would reinstall the same old commit.
        none-installed  2   nothing installed; proceed to install.
        unverified      2   could not confirm. Never treat as current.

    Only ``current`` is 0. Anything the script could not verify — unreadable config,
    failed fetch, missing upstream — is 2, never 0 and never 1: reporting success
    without checking is the failure this script exists to prevent, and 1 would be
    read as "stale, go wipe". Also lists orphaned version directories.
``prune``
    Remove **orphaned version dirs** — the ``<plugin>/<version>/`` copies left behind
    by earlier installs, which accumulate on every refresh. Never touches the version
    recorded as the live install.
``wipe``
    Remove the cache directory of **stale plugins only**, so the next install has
    no matching path to skip. Never touches a plugin that is already current;
    ``--plugins`` narrows that set rather than widening it, and stepping outside it
    takes ``--force``.

Wiping is deliberately conditional. Hosts run parallel sessions and cron jobs; a
wipe pulls files out from under any session about to invoke
``${CLAUDE_PLUGIN_ROOT}/…/script.py``, so doing it when nothing is stale is pure
downside. Every deletion target is checked to live under the resolved cache root
at the expected depth before anything is removed.

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys

DEFAULT_MARKETPLACE = "aura-tools"


# ---------- locating the pieces ----------------------------------------------

def config_dir() -> str:
    return os.path.abspath(os.environ.get("CLAUDE_CONFIG_DIR") or
                           os.path.expanduser("~/.claude"))


def plugins_dir() -> str:
    return os.path.join(config_dir(), "plugins")


def cache_root() -> str:
    return os.path.join(plugins_dir(), "cache")


# ---------- hosts ---------------------------------------------------------------
#
# Claude Code is the only host with an `installed_plugins.json`, so it is the only
# one where the precise gitCommitSha comparison is possible. Its surveyor is left
# exactly as-is — that logic took several review rounds to get right and is not
# worth re-plumbing for the sake of symmetry.
#
# Codex gets a parallel, simpler surveyor: compare the version dir actually on disk
# against the version the clone's manifest declares for that flavour. That only
# became a usable signal once the manifests carried real versions again, and it
# needs no per-host state file.
#
# Claude Desktop / local-agent mode is deliberately NOT a surveyed host: it extracts
# a fresh per-session copy under
# `~/Library/Application Support/Claude/local-agent-mode-sessions/<session>/…/rpm/plugin_<id>/`
# with no clone and no install manifest to diff against. What it can answer is
# "which version did THIS session load", which `loaded_note()` reports instead.
#
# Cursor is out of scope: `~/.cursor/plugins/cache` exists but carries no aura-tools
# install, so any implementation would be untested against a layout inferred from
# unrelated plugins.

def host_specs(marketplace: str = DEFAULT_MARKETPLACE) -> list:
    """Hosts present on this machine, in reporting order.

    Claude Code is the primary host and is surveyed whenever its plugin tree
    exists — `none-installed` there is actionable (install it). A secondary host
    is only surveyed when it actually carries this marketplace: a Codex install
    that has never held aura-tools has nothing to be stale about, and reporting
    `none-installed` for it would fail the whole run over a host the user never
    asked to use. Same rule that keeps Cursor out — its cache exists but carries
    no aura-tools install.
    """
    out = []
    cc = os.path.abspath(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"))
    if os.path.isdir(os.path.join(cc, "plugins")):
        out.append({"name": "claude-code", "root": cc,
                    "cache": os.path.join(cc, "plugins", "cache"), "flavour": ".claude-plugin"})
    cx = os.path.abspath(os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex"))
    cx_cache = os.path.join(cx, "plugins", "cache")
    if os.path.isdir(os.path.join(cx_cache, marketplace)):
        out.append({"name": "codex", "root": cx,
                    "cache": cx_cache, "flavour": ".codex-plugin"})
    return out


def _manifest_version(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh).get("version") or ""
    except (OSError, json.JSONDecodeError):
        return ""


def survey_codex(host: dict, marketplace: str, fetch: bool = True) -> dict:
    """Version-dir vs clone-declared-version, for hosts with no install manifest.

    Same result shape as `survey()` so `render`/`_verdict`/`prune` work unchanged.
    `installed_sha` stays empty — Codex records no commit, so the sha column is
    honestly blank rather than filled with something inferred.
    """
    clone = os.path.join(host["root"], ".tmp", "marketplaces", marketplace)
    state = clone_state(clone, fetch=fetch)
    base = os.path.join(host["cache"], marketplace)

    plugins = []
    if os.path.isdir(base):
        for name in sorted(os.listdir(base)):
            pdir = os.path.join(base, name)
            if not os.path.isdir(pdir):
                continue
            versions = sorted(d for d in os.listdir(pdir) if os.path.isdir(os.path.join(pdir, d)))
            if not versions:
                continue
            # More than one version dir means an older install was left behind; the
            # live one is whichever matches the declared version, else the newest.
            declared = _manifest_version(
                os.path.join(clone, "plugins", name, host["flavour"], "plugin.json"))
            live = declared if declared in versions else versions[-1]
            status = ("unknown" if not declared
                      else "current" if live == declared else "stale")
            plugins.append({
                "plugin": name, "installed_sha": "", "version": live,
                "declared_version": declared,
                "install_path": os.path.join(pdir, live),
                "live_paths": [os.path.join(pdir, live)],
                "last_updated": "", "status": status,
                # No declared version means `live` is a guess (newest dir), so every
                # other dir is only *probably* dead. Same contract as the Claude Code
                # surveyor: an unidentified live install yields no orphans at all,
                # rather than a list that might name the copy in use.
                "orphans": [] if not declared
                           else sorted(v for v in versions if v != live),
                "orphans_unknown": not declared,
            })

    result = {
        "host": host["name"], "marketplace": marketplace, "clone_path": clone,
        "clone_head": state["head"], "clone_remote_head": state["remote_head"],
        "clone_behind": bool(state["remote_head"]) and state["head"] != state["remote_head"],
        "clone_note": state["note"], "clone_caveat": state["caveat"],
        "plugins": plugins,
        "stale": [p["plugin"] for p in plugins if p["status"] == "stale"],
        "unknown": [p["plugin"] for p in plugins if p["status"] == "unknown"],
    }
    result["verdict"] = _verdict(result)
    return result


def loaded_note() -> str:
    """What THIS session actually loaded, when the host extracts a per-session copy.

    Claude Desktop / local-agent mode has no clone to diff against, so the useful
    question isn't "is the install current" but "is the copy running right now
    current" — which `CLAUDE_PLUGIN_ROOT` answers directly.
    """
    root = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
    if not root or "local-agent-mode-sessions" not in root:
        return ""
    v = _manifest_version(os.path.join(root, ".claude-plugin", "plugin.json"))
    return (f"This session loaded a per-session copy of the plugin"
            f"{' at version ' + v if v else ''} from\n  {root}\n"
            f"That copy is extracted at session start and is NOT refreshed by a reload — "
            f"open a new session to pick up a newer version.")


def _die_unverified(msg: str):
    """Exit 2, never 1.

    ``SystemExit(<str>)`` exits with code 1, which in this script's contract means
    "stale" — so a missing or corrupt config file would read as "stale, go wipe and
    reinstall". A config we cannot read is `unverified`, which is exit 2.
    """
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(2)


def _load_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        _die_unverified(f"{path} not found — is this host running Claude Code with "
                        f"plugins installed? (set CLAUDE_CONFIG_DIR if your config "
                        f"lives elsewhere)")
    except json.JSONDecodeError as exc:
        _die_unverified(f"{path} is not valid JSON ({exc})")
    except OSError as exc:
        _die_unverified(f"{path} could not be read ({exc})")


def _git_run(clone_path: str, *args: str, timeout: int = 30):
    """(ok, stdout). ``ok`` is the exit status, which a bare stdout read can't give:
    ``git fetch --quiet`` prints nothing on success *and* on failure."""
    try:
        out = subprocess.run(["git", "-C", clone_path, *args],
                             capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return False, ""
    return out.returncode == 0, out.stdout.strip()


def _git(clone_path: str, *args: str, timeout: int = 30) -> str:
    return _git_run(clone_path, *args, timeout=timeout)[1]


def clone_state(clone_path: str, fetch: bool = True) -> dict:
    """Where the marketplace clone sits relative to its remote.

    "Installed matches the clone" only means the skills are current if the *clone*
    is itself current. A clone that was never refreshed matches a stale install
    perfectly, so comparing against it alone would report "current" forever — the
    same silent-no-op shape this script exists to catch, one layer up.
    """
    if not clone_path or not os.path.isdir(clone_path):
        return {"head": "", "remote_head": "", "note": "clone directory not found",
                "caveat": ""}
    head = _git(clone_path, "rev-parse", "HEAD")
    if not head:
        return {"head": "", "remote_head": "", "note": "could not read clone HEAD",
                "caveat": ""}
    if fetch:
        # A failed fetch must NOT pass as fresh. `@{upstream}` still resolves from
        # the last successful fetch, so an offline run would compare HEAD against a
        # stale cached ref, find them equal, and call the clone current. Offline is
        # not fatal — it is undeterminable, and reported as such.
        ok, _ = _git_run(clone_path, "fetch", "--quiet", timeout=90)
        if not ok:
            return {"head": head, "remote_head": "", "caveat": "",
                    "note": "git fetch failed (offline or no remote access) — clone "
                            "freshness unverified"}
    remote = _git(clone_path, "rev-parse", "@{upstream}")
    return {
        "head": head,
        "remote_head": remote,
        # `note` degrades the verdict to `unverified`; `caveat` only prints. The
        # difference is who decided: the tool tried and couldn't (undeterminable)
        # vs the caller explicitly opted out (their assertion to make).
        "note": "" if remote else "no upstream tracking ref — clone freshness unverified",
        "caveat": "" if fetch else "--no-fetch: remote ref not refreshed this run",
    }


# ---------- the check ---------------------------------------------------------

def survey(marketplace: str, fetch: bool = True) -> dict:
    """Compare every installed plugin's version against the version the clone declares.

    Deliberately NOT the recorded `gitCommitSha` vs clone HEAD: that sha is stamped
    only when a plugin is re-extracted, which happens only when *that plugin*
    changes, so any commit to the repo leaves every untouched plugin reading stale
    while current. Version equality is safe because the CI bump gate requires a
    version bump for any change under `plugins/<p>/`.
    """
    installed = _load_json(os.path.join(plugins_dir(), "installed_plugins.json"))
    markets = _load_json(os.path.join(plugins_dir(), "known_marketplaces.json"))

    entry = markets.get(marketplace) or {}
    clone = entry.get("installLocation", "")
    state = clone_state(clone, fetch=fetch)
    head, remote_head = state["head"], state["remote_head"]

    plugins = []
    for key, entries in (installed.get("plugins") or {}).items():
        name, _, mkt = key.partition("@")
        if mkt != marketplace or not entries:
            continue
        rec = entries[0]
        sha = rec.get("gitCommitSha") or ""
        install_path = rec.get("installPath") or ""
        # Every entry, not just the first: a plugin can be installed at user AND
        # project scope, and each scope's dir is live. Passing one would mark the
        # other's directory as prunable.
        live_paths = [e.get("installPath") or "" for e in entries]
        orphans = orphan_dirs(marketplace, name, live_paths)
        inst_ver = rec.get("version") or os.path.basename(install_path)
        declared = _manifest_version(
            os.path.join(clone, "plugins", name, ".claude-plugin", "plugin.json"))
        # No declared version means we cannot judge — say so rather than guessing
        # "current". Happens when the clone is missing, or the plugin was dropped
        # from the marketplace.
        status = ("unknown" if not declared or not inst_ver
                  else "current" if inst_ver == declared else "stale")
        plugins.append({
            "plugin": name,
            # Kept for diagnostics only — NEVER the currency signal. It records the
            # commit at last re-extract, so it legitimately lags HEAD.
            "installed_sha": sha,
            "version": inst_ver,
            "declared_version": declared,
            "install_path": install_path,
            "live_paths": live_paths,
            "last_updated": rec.get("lastUpdated") or "",
            "status": status,
            "orphans": orphans,
            # Surfaced so a suppressed prune is visible rather than looking like
            # "no orphans found".
            "orphans_unknown": not all(live_paths),
        })

    plugins.sort(key=lambda p: p["plugin"])
    result = {
        "host": "claude-code",
        "marketplace": marketplace,
        "clone_path": clone,
        "clone_head": head,
        "clone_remote_head": remote_head,
        "clone_behind": bool(remote_head) and head != remote_head,
        "clone_note": state["note"],
        "clone_caveat": state["caveat"],
        "plugins": plugins,
        "stale": [p["plugin"] for p in plugins if p["status"] == "stale"],
        "unknown": [p["plugin"] for p in plugins if p["status"] == "unknown"],
    }
    result["verdict"] = _verdict(result)
    return result


def _verdict(r: dict) -> str:
    """One word for the whole survey. Only ``current`` means "nothing to do".

    Order matters: a stale clone outranks per-plugin agreement, because agreeing
    with a stale clone is not being current. And "no plugins installed" is never
    ``current`` — a first-time or post-uninstall run must proceed to install, not
    stop because zero plugins disagreed with anything.
    """
    if r["clone_behind"]:
        return "clone-behind"
    if not r["plugins"]:
        return "none-installed"
    if r["stale"]:
        return "stale"
    if r["unknown"] or r["clone_note"]:
        return "unverified"
    return "current"


def orphan_dirs(marketplace: str, plugin: str, install_paths: list) -> list:
    """Version dirs under a plugin that no recorded install points at.

    ``install_paths`` must be **every** recorded ``installPath`` for the plugin —
    ``installed_plugins.json`` stores a list per plugin (user and project scope can
    both be present), and protecting only the first would classify another scope's
    live directory as an orphan.

    Returns nothing when the live set can't be established. An unknown live dir must
    never widen the orphan set: treating "no installPath" as "nothing is live" would
    make every version dir an orphan and delete the running install.
    """
    base = os.path.join(cache_root(), marketplace, plugin)
    if not os.path.isdir(base):
        return []
    if not install_paths or not all(install_paths):
        return []
    live = {os.path.basename(os.path.normpath(p)) for p in install_paths}
    return sorted(d for d in os.listdir(base)
                  if d not in live and os.path.isdir(os.path.join(base, d)))


# ---------- the wipe ----------------------------------------------------------

class UnsafeTarget(Exception):
    pass


def assert_safe_to_remove(target: str) -> str:
    """Refuse anything that isn't ``<cache>/<marketplace>/<plugin>[/<version>]``.

    Guards against an empty/relative path, a symlink escape, the cache root
    itself, and a target at the wrong depth — any of which would turn a cleanup
    into a much larger deletion.
    """
    if not target or not target.strip():
        raise UnsafeTarget("empty path")
    real = os.path.realpath(target)
    root = os.path.realpath(cache_root())
    if real in ("/", os.path.realpath(os.path.expanduser("~")), root):
        raise UnsafeTarget(f"refusing to remove {real}")
    try:
        if os.path.commonpath([real, root]) != root:
            raise UnsafeTarget(f"{real} is outside the plugin cache ({root})")
    except ValueError:                       # different drives / unrelated roots
        raise UnsafeTarget(f"{real} is outside the plugin cache ({root})")
    depth = len(os.path.relpath(real, root).split(os.sep))
    if depth not in (2, 3):                  # <marketplace>/<plugin>[/<version>]
        raise UnsafeTarget(f"{real} is at depth {depth} under the cache; "
                           f"expected <marketplace>/<plugin>[/<version>]")
    return real


def _dir_bytes(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass                      # vanished mid-walk; not worth failing over
    return total


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n / 1:.1f}{unit}"
        n /= 1024
    return f"{n}B"


class ProcessCheckFailed(Exception):
    pass


def _run(cmd: list) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProcessCheckFailed(f"{cmd[0]}: {exc}") from exc
    if out.returncode != 0 or not out.stdout.strip():
        raise ProcessCheckFailed(f"{cmd[0]} exited {out.returncode}")
    return out.stdout


def processes() -> list:
    """(pid, command line) of every process (`ps -axo pid=,command=`). Raises ProcessCheckFailed."""
    rows = []
    for line in _run(["ps", "-axo", "pid=,command="]).splitlines():
        pid, _, cmd = line.strip().partition(" ")
        if pid.isdigit():
            rows.append((int(pid), cmd.strip()))
    return rows


def working_dirs() -> list:
    """(pid, command name, cwd) of every process (`lsof -a -d cwd -Fpcn`). Raises ProcessCheckFailed."""
    rows, pid, name = [], None, ""
    for line in _run(["lsof", "-a", "-d", "cwd", "-Fpcn"]).splitlines():
        tag, val = line[:1], line[1:]
        if tag == "p":
            pid, name = (int(val) if val.isdigit() else None), ""
        elif tag == "c":
            name = val
        elif tag == "n" and pid is not None:
            rows.append((pid, name, val))
    return rows


def in_use(path: str, procs: list, cwds: list):
    """(pid, command) of a process using `path`: named in its arguments, or working inside it."""
    names = {path.rstrip("/"), os.path.realpath(path).rstrip("/")}
    me = os.getpid()
    for pid, cmd in procs:
        if pid != me and any(n + "/" in cmd or cmd.endswith(n) or f"{n} " in cmd for n in names):
            return pid, cmd
    for pid, name, cwd in cwds:
        if pid != me and any(cwd == n or cwd.startswith(n + "/") for n in names):
            return pid, f"{name} (working directory {cwd})"
    return None


def prune(result: dict, dry_run: bool = False) -> list:
    """Remove version dirs that aren't the recorded live install.

    Each install writes a new ``<plugin>/<version>/`` and leaves the previous one
    behind, so these accumulate on every refresh. ``wipe`` is the wrong tool for
    them — it removes the whole ``<plugin>`` dir, live copy included.

    Not entirely inert: a session or cron that started *before* the refresh still
    has ``CLAUDE_PLUGIN_ROOT`` pointing at an old dir, so removing it breaks that
    session's next bundled-script call. So a dir a running process names in its
    arguments, or works inside, is skipped (2026-10-28: a prune removed the version
    a monitor-firebase pass was running from). If the process listing itself fails,
    every dir is skipped rather than deleted. Still confirm-gated in the SKILL.md.
    """
    marketplace = result["marketplace"]
    done = []
    try:
        procs, cwds, check_error = processes(), working_dirs(), None
    except Exception as exc:  # noqa: BLE001 -- any listing failure means skip, never delete
        procs, cwds, check_error = [], [], f"{type(exc).__name__}: {exc}" if not isinstance(
            exc, ProcessCheckFailed) else str(exc)
    for p in result["plugins"]:
        for orphan in p["orphans"]:
            target = os.path.join(cache_root(), marketplace, p["plugin"], orphan)
            label = f"{p['plugin']}/{orphan}"
            try:
                safe = assert_safe_to_remove(target)
            except UnsafeTarget as exc:
                done.append((label, f"REFUSED ({exc})", 0))
                continue
            if check_error:
                done.append((label, f"skipped: process check failed ({check_error})", 0))
                continue
            user = in_use(safe, procs, cwds) or in_use(target, procs, cwds)
            if user:
                done.append((label, f"skipped: in use by pid {user[0]} ({user[1][:60]})", 0))
                continue
            size = _dir_bytes(safe)
            if dry_run:
                done.append((label, "would remove", size))
                continue
            shutil.rmtree(safe)
            done.append((label, "removed", size))
    return done


def wipe(marketplace: str, plugins: list, dry_run: bool = False) -> list:
    done = []
    for plugin in plugins:
        target = os.path.join(cache_root(), marketplace, plugin)
        if not os.path.isdir(target):
            done.append((plugin, "absent", target))
            continue
        try:
            safe = assert_safe_to_remove(target)
        except UnsafeTarget as exc:
            done.append((plugin, f"REFUSED ({exc})", target))
            continue
        if dry_run:
            done.append((plugin, "would remove", safe))
            continue
        shutil.rmtree(safe)
        done.append((plugin, "removed", safe))
    return done


# ---------- output ------------------------------------------------------------

VERDICT_HELP = {
    "current": "All plugins match the clone, and the clone matches its remote.",
    "stale": "The installed copy predates the clone. Run "
             "`claude plugin update <plugin>@aura-tools` — that is the verb that moves an "
             "installed plugin to a new version; `install` no-ops on an already-installed "
             "plugin, and a marketplace update alone does not move a version bump. "
             "Re-run this check after. Only wipe if update leaves it stale.",
    "clone-behind": "The marketplace CLONE is behind its remote, so 'installed matches "
                    "clone' would not mean current. Refresh the marketplace first "
                    "(`/plugin marketplace update <name>`), then re-run this check.",
    "none-installed": "No plugins from this marketplace are installed — nothing to "
                      "compare. Run the installs.",
    "unverified": "Could not confirm currency. Treat as unverified, not as current.",
}


def render(result: dict) -> str:
    lines = [f"host        : {result.get('host', 'claude-code')}",
             f"marketplace : {result['marketplace']}",
             f"clone       : {result['clone_path'] or '(not found)'}",
             f"clone HEAD  : {result['clone_head'][:12] or '(unreadable)'}",
             f"clone remote: {result['clone_remote_head'][:12] or '(undeterminable)'}"
             + ("  << clone is BEHIND its remote" if result["clone_behind"] else ""),
             ""]
    for label, text in (("note", result["clone_note"]),
                        ("caveat", result["clone_caveat"])):
        if text:
            lines += [f"{label:<12}: {text}", ""]
    if not result["plugins"]:
        lines.append(f"{result['verdict'].upper()}: {VERDICT_HELP[result['verdict']]}")
        return "\n".join(lines)

    lines.append(f"{'plugin':<14} {'installed':<10} {'declared':<10} {'status':<9} "
                 f"{'last extract':<13} at commit")
    lines.append("-" * 78)
    for p in result["plugins"]:
        lines.append(f"{p['plugin']:<14} {(p['version'] or '-'):<10} "
                     f"{(p.get('declared_version') or '-'):<10} {p['status']:<9} "
                     f"{(p['last_updated'][:10] or '-'):<13} {p['installed_sha'][:12] or '-'}")
    lines += ["", "status compares installed vs the version the clone declares. The commit "
                  "column is\nwhen the plugin was last re-extracted — it only moves when that "
                  "plugin changes, so\nit lags HEAD on every unchanged plugin and is NOT a "
                  "currency signal."]

    blocked = [p["plugin"] for p in result["plugins"] if p.get("orphans_unknown")]
    if blocked:
        lines += ["", f"Orphan detection suppressed for: {', '.join(blocked)} — a recorded "
                      f"install has no installPath, so the live dir can't be identified. "
                      f"`prune` will skip these rather than risk the live copy."]
    orphans = [(p["plugin"], d) for p in result["plugins"] for d in p["orphans"]]
    if orphans:
        lines += ["", "Orphaned version dirs (safe to remove — not the live install):"]
        lines += [f"  {plug}/{d}" for plug, d in orphans]

    lines.append("")
    verdict = result["verdict"]
    detail = ""
    if verdict == "stale":
        detail = f" ({', '.join(result['stale'])})"
    elif verdict == "unverified" and result["unknown"]:
        detail = f" ({', '.join(result['unknown'])})"
    lines.append(f"{verdict.upper()}{detail}: {VERDICT_HELP[verdict]}")
    return "\n".join(lines)


def _by_host(capture) -> dict:
    """Normalize a `check --json` capture to {host: survey}.

    `check --json` emits a bare object for one host and an array for several, so a
    delta that assumed the object shape would break on exactly the machines this
    multi-host survey exists for.
    """
    out = {}
    for s in (capture if isinstance(capture, list) else [capture]):
        if isinstance(s, dict) and ("plugins" in s or "host" in s):
            out.setdefault(s.get("host") or "claude-code", s)
    return out


def delta(before, after) -> str:
    """Per-host, per-plugin version before -> after, from two `check --json` captures."""
    b_all, a_all = _by_host(before), _by_host(after)
    hosts = sorted(set(b_all) | set(a_all))
    if not hosts:
        return "Neither capture holds a host survey — nothing to compare."
    return "\n\n".join(_delta_one(h, b_all.get(h), a_all.get(h), len(hosts) > 1)
                       for h in hosts)


def _delta_one(host: str, before, after, show_host: bool) -> str:
    """One host's version table.

    A plugin whose version didn't move is reported as `unchanged`, never dropped:
    a silent no-op looks identical to a successful update, which is the failure
    this whole flow exists to surface.
    """
    missing = ("before" if before is None else
               "after" if after is None else "")
    before, after = before or {}, after or {}

    def index(snap):
        return {p["plugin"]: p for p in (snap.get("plugins") or [])}

    b, a = index(before), index(after)
    prefix = [f"[{host}]"] if show_host else []
    names = sorted(set(b) | set(a))
    if not names:
        return "\n".join(prefix + ["No plugins in either capture — nothing to compare."])

    rows, moved = [], 0
    for n in names:
        bv = (b.get(n) or {}).get("version") or "(absent)"
        av = (a.get(n) or {}).get("version") or "(absent)"
        if bv == av:
            rows.append(f"  {n:<12} {bv:<16} unchanged")
        else:
            moved += 1
            rows.append(f"  {n:<12} {bv:<16} -> {av}")

    head = (f"Version delta — {moved} of {len(names)} plugin(s) moved"
            if moved else
            f"Version delta — nothing moved ({len(names)} plugin(s) already current)")
    tail = []
    bh = (before.get("clone_head") or "")[:12]
    ah = (after.get("clone_head") or "")[:12]
    if bh or ah:
        tail = ["", f"  clone        {bh or '(unknown)':<16} "
                    f"{'unchanged' if bh == ah else '-> ' + (ah or '(unknown)')}"]
    if missing:
        tail += ["", f"  NOTE: this host is absent from the {missing} capture, so the "
                     f"comparison is one-sided — the '(absent)' column is a missing "
                     f"survey, not a missing install."]
    return "\n".join(prefix + [head, ""] + rows + tail)


# ---------- CLI ---------------------------------------------------------------

def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", nargs="?", default="check",
                    choices=["check", "wipe", "prune", "delta"])
    ap.add_argument("--marketplace", default=DEFAULT_MARKETPLACE)
    ap.add_argument("--host", default=None,
                    help="limit to one host (claude-code, codex); default: all detected")
    ap.add_argument("--plugins", default="",
                    help="wipe: comma-separated plugins to NARROW the stale set. "
                         "Non-stale names are skipped unless --force.")
    ap.add_argument("--force", action="store_true",
                    help="wipe: also remove plugins that are NOT stale")
    ap.add_argument("--dry-run", action="store_true", help="wipe: show targets, remove nothing")
    ap.add_argument("--no-fetch", action="store_true",
                    help="skip the clone `git fetch` (use right after a marketplace update)")
    ap.add_argument("--before", help="delta: path to the earlier `check --json`")
    ap.add_argument("--after", help="delta: path to the later `check --json`")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()

    if args.command == "delta":
        if not args.before or not args.after:
            _die_unverified("delta needs --before and --after (two `check --json` captures)")
        snaps = []
        for p in (args.before, args.after):
            try:
                with open(p, encoding="utf-8") as fh:
                    snaps.append(json.load(fh))
            except FileNotFoundError:
                _die_unverified(f"{p} not found — capture it with `check --json > {p}`")
            except (json.JSONDecodeError, OSError) as exc:
                _die_unverified(f"{p} could not be read ({exc})")
        print(delta(*snaps))
        return 0

    if args.command == "check":
        present = host_specs(args.marketplace)
        hosts = [h for h in present if args.host in (None, "", "all", h["name"])]
        if args.host and args.host not in ("all",) and not hosts:
            _die_unverified(f"no host named {args.host!r} carries the {args.marketplace!r} "
                            f"marketplace on this machine "
                            f"(present: {', '.join(h['name'] for h in present) or 'none'})")
        results = []
        for h in hosts:
            results.append(
                survey(args.marketplace, fetch=not args.no_fetch) if h["name"] == "claude-code"
                else survey_codex(h, args.marketplace, fetch=not args.no_fetch))
        if args.as_json:
            print(json.dumps(results[0] if len(results) == 1 else results, indent=2))
        else:
            print("\n\n".join(render(r) for r in results))
            note = loaded_note()
            if note:
                print("\n" + note)
        # Worst verdict across hosts decides the exit code — a host left stale is
        # still stale even if another is current. The codes are KINDS, not a
        # severity scale, so this ranks by actionability rather than by max():
        # `stale` (1) is a concrete instruction (wipe, reinstall, re-check) and
        # must not be swallowed by an unverifiable sibling (2), which only says
        # "stop and say so". Every host's own verdict is still printed, so the
        # unverified one is never hidden — only the exit code has to pick one.
        codes = {{"current": 0, "stale": 1, "clone-behind": 1}.get(r["verdict"], 2)
                 for r in results}
        if not codes:
            return 2
        return 1 if 1 in codes else (2 if 2 in codes else 0)
    # wipe/prune are Claude Code-only: their containment guards are written against
    # that cache root, and Codex's install lifecycle hasn't been verified here.
    if args.host and args.host not in ("claude-code", "all"):
        _die_unverified(f"`{args.command}` is only supported on claude-code, not "
                        f"{args.host!r} — its cache layout is unverified for deletion")
    result = survey(args.marketplace, fetch=not args.no_fetch)

    if args.command == "prune":
        done = prune(result, dry_run=args.dry_run)
        freed = sum(size for _, outcome, size in done if outcome in ("removed", "would remove"))
        if args.as_json:
            print(json.dumps({"pruned": [{"dir": d, "outcome": o, "bytes": s}
                                         for d, o, s in done], "bytes_freed": freed},
                             indent=2))
        elif not done:
            print("Nothing to prune — no orphaned version dirs.")
        else:
            for label, outcome, size in done:
                print(f"{label:<30} {outcome:<14} {_human(size) if size else ''}".rstrip())
            print(f"\n{'would reclaim' if args.dry_run else 'reclaimed'} {_human(freed)}")
        return 1 if any(o.startswith("REFUSED") for _, o, _ in done) else 0

    stale = set(result["stale"])
    asked = [p.strip() for p in args.plugins.split(",") if p.strip()]
    skipped = []
    if asked:
        # --plugins narrows the stale set; it does not widen it. Deleting a plugin
        # that is already current would yank files from a live session for nothing.
        targets = [p for p in asked if p in stale or args.force]
        skipped = [p for p in asked if p not in targets]
    else:
        targets = sorted(stale)
    if not targets:
        msg = "Nothing to wipe — no stale plugins."
        if skipped:
            msg += (f" Skipped (not stale): {', '.join(skipped)}. "
                    f"Pass --force to remove them anyway.")
        print(json.dumps({"wiped": [], "skipped": skipped, "note": msg}, indent=2)
              if args.as_json else msg)
        return 0
    if skipped:
        print(f"Skipping (not stale): {', '.join(skipped)} — pass --force to override.\n",
              file=sys.stderr)

    done = wipe(args.marketplace, targets, dry_run=args.dry_run)
    if args.as_json:
        print(json.dumps({"wiped": [{"plugin": p, "outcome": o, "path": t}
                                    for p, o, t in done]}, indent=2))
    else:
        for plugin, outcome, path in done:
            print(f"{plugin:<14} {outcome:<12} {path}")
        print("\nNow reinstall those plugins, then re-run `check` to confirm the sha moved.")
    return 1 if any(o.startswith("REFUSED") for _, o, _ in done) else 0


if __name__ == "__main__":
    sys.exit(main())
