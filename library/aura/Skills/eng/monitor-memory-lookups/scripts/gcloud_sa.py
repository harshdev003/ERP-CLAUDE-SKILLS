#!/usr/bin/env python3
"""Run the gcloud CLI as the GCP_KEY service account, not the user's login.

A user login falls under Google's reauth policy; once it lapses every
non-interactive call fails with "Reauthentication failed", and an unattended
skill silently degrades. A service account is not subject to reauth.

  GCP_KEY set    -> the key (inline JSON, or a path to the key file) is
                    activated into a private temp CLOUDSDK_CONFIG, once per
                    process, and every call runs there. The user's own gcloud
                    config is never read or written. If activation fails the
                    call fails, naming GCP_KEY -- it never falls through to the
                    user login.
  GCP_KEY unset  -> gcloud runs as-is, on the user's active login.

Every call is labelled in GCP logs: gcloud's user agent carries
"environment/AuraClaude environment-version/<who>/<plugin>:<skill>@<version>+<session>".
user_agent() gives the same label for raw HTTP calls. A label, not proof: anyone
can send it.

Library:  run([...gcloud args...], **subprocess_kwargs) -> CompletedProcess
          credential() -> label naming the credential, for error lines
          user_agent() -> "AuraClaude/<who>/<plugin>:<skill>@<version> (session <id>)"
CLI:      python3 gcloud_sa.py <gcloud args...>   (exit code passes through)
          python3 gcloud_sa.py --user-agent       (prints user_agent(), for curl -A)
"""
from __future__ import annotations

import atexit
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import threading

_lock = threading.Lock()
_state: dict = {}  # config_dir, email, error -- set once per process


def _ua_who() -> str:
    """Login name of whoever runs this, for the user agent: AINATIVE_UA_WHO, else the global git email's local part, git user.name, the OS account, or "unknown"."""
    who = re.sub(r"^\d+\+", "", os.environ.get("AINATIVE_UA_WHO", "").strip().partition("@")[0])
    if not who:
        if not hasattr(_ua_who, "found"):
            import getpass, subprocess
            found = ""
            for key in ("user.email", "user.name"):
                try:
                    val = subprocess.run(["git", "config", "--global", key], capture_output=True, encoding="utf-8", errors="replace", timeout=5).stdout.strip()
                except (OSError, subprocess.SubprocessError):
                    val = ""
                found = re.sub(r"^\d+\+", "", val.partition("@")[0]) if key == "user.email" else val
                if found:
                    break
            if not found:
                try:
                    found = getpass.getuser()
                except Exception:
                    found = ""
            _ua_who.found = found
        who = _ua_who.found
    return re.sub(r"[^A-Za-z0-9._-]+", "-", who).strip("-.") or "unknown"


def _caller() -> tuple[str, str, str]:
    """(who/plugin:skill, version, session) for the skill this copy ships in."""
    skill = pathlib.Path(__file__).resolve().parents[1]
    root = skill.parents[1]
    try:
        plugin = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["name"]
    except (OSError, ValueError, KeyError, TypeError):
        plugin = root.name
    try:
        front = (skill / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[1]
        version = re.search(r'version:\s*"([^"]+)"', front).group(1)
    except (OSError, IndexError, AttributeError):
        version = "unknown"
    session = os.environ.get("AINATIVE_SESSION_NAME") or (os.environ.get("CLAUDE_CODE_SESSION_ID") or "")[:8]
    session = re.sub(r"[^A-Za-z0-9._-]+", "-", session).strip("-") or "none"
    return f"{_ua_who()}/{plugin}:{skill.name}", version, session


def user_agent() -> str:
    """User-Agent for raw HTTP calls to RTDB or GCP from this skill."""
    name, version, session = _caller()
    return f"AuraClaude/{name}@{version} (session {session})"


def _labelled(env: dict) -> dict:
    name, version, session = _caller()
    env["CLOUDSDK_METRICS_ENVIRONMENT"] = "AuraClaude"
    env["CLOUDSDK_METRICS_ENVIRONMENT_VERSION"] = f"{name}@{version}+{session}"
    return env


def _key_info() -> dict | None:
    raw = (os.environ.get("GCP_KEY") or "").strip()
    if not raw:
        return None
    if raw.startswith("{"):
        info = json.loads(raw)
    else:
        with open(os.path.expanduser(raw), encoding="utf-8") as f:
            info = json.load(f)
    if not isinstance(info, dict) or not info.get("client_email"):
        raise ValueError("not a service-account key (no client_email)")
    return info


def _activate() -> dict:
    """Activate GCP_KEY into a temp config once; cache the outcome (success or error)."""
    with _lock:
        if _state:
            return _state
        try:
            info = _key_info()
        except (OSError, ValueError) as exc:
            _state.update(error=f"GCP_KEY unusable: {type(exc).__name__}: {exc}")
            return _state
        if info is None:
            _state.update(config_dir=None)
            return _state
        email = info["client_email"]
        config_dir = tempfile.mkdtemp(prefix="gcloud-sa-")
        atexit.register(shutil.rmtree, config_dir, True)
        key_path = os.path.join(config_dir, "key.json")
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(info, f)
        env = _labelled({**os.environ, "CLOUDSDK_CONFIG": config_dir, "CLOUDSDK_CORE_DISABLE_PROMPTS": "1"})
        try:
            out = subprocess.run(
                ["gcloud", "auth", "activate-service-account", email,
                 f"--key-file={key_path}", "--quiet"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                env=env, timeout=60,
            )
        except subprocess.TimeoutExpired:
            out = None
        finally:
            os.remove(key_path)
        if out is None or out.returncode != 0:
            # All of stderr: its last line is often only gcloud's generic hint, not the cause.
            lines = [] if out is None else [l.strip() for l in out.stderr.splitlines() if l.strip()]
            detail = "timeout" if out is None else (" | ".join(lines)[:2000] or "exit %d" % out.returncode)
            _state.update(error=f"GCP_KEY ({email}) activation failed: {detail}", email=email)
            return _state
        if info.get("project_id"):
            subprocess.run(
                ["gcloud", "config", "set", "project", info["project_id"], "--quiet"],
                capture_output=True, env=env, timeout=30,
            )
        _state.update(config_dir=config_dir, email=email)
        return _state


def credential() -> str:
    """Which credential gcloud runs as -- put this in any GCP error line."""
    st = _activate()
    if st.get("email"):
        return f"GCP_KEY ({st['email']})"
    if "error" in st:
        return "GCP_KEY (unusable)"
    return "user gcloud login (GCP_KEY unset)"


def run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    """subprocess.run(["gcloud", *args]) as the GCP_KEY service account when set.

    An unusable GCP_KEY returns a failed CompletedProcess whose stderr names it,
    so callers keep their existing non-zero-exit degrade path. A missing gcloud
    binary raises FileNotFoundError, as a bare subprocess.run would.
    """
    st = _activate()
    if "error" in st:
        text = kwargs.get("text") or kwargs.get("encoding") or kwargs.get("universal_newlines")
        msg = st["error"] + "\n"
        return subprocess.CompletedProcess(
            ["gcloud", *args], 1, "" if text else b"", msg if text else msg.encode())
    env = _labelled(dict(kwargs.pop("env", None) or os.environ))
    if st.get("config_dir"):
        env["CLOUDSDK_CONFIG"] = st["config_dir"]
        env["CLOUDSDK_CORE_DISABLE_PROMPTS"] = "1"
    kwargs["env"] = env
    return subprocess.run(["gcloud", *args], **kwargs)


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if argv == ["--user-agent"]:
        print(user_agent())
        return 0
    try:
        out = run(argv)
    except FileNotFoundError:
        print("gcloud_sa: gcloud not on PATH", file=sys.stderr)
        return 127
    if out.returncode != 0 and out.stderr:
        sys.stderr.write(out.stderr if isinstance(out.stderr, str) else out.stderr.decode())
    if out.returncode != 0:
        print(f"gcloud_sa: gcloud exited {out.returncode} as {credential()}", file=sys.stderr)
    return out.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
