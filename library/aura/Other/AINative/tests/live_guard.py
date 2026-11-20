"""Keeps every test away from live services (ENG-1746).

A date-dependent test in test_ip_block_skills.py reached a real `gcloud ... security-policies rules
update` on prod Cloud Armor as GCP_KEY; only a missing IAM permission stopped it. Stubbing each call site
is per-test discipline and missed one, so this guard is installed for the whole run instead:

- **Credentials:** every secret-looking environment variable is removed, and gcloud is pointed at an empty
  config, so nothing can authenticate even by a path the other two layers miss.
- **Commands:** a shim directory goes first on PATH with `gcloud`, `gsutil`, `bq`, `curl`, `wget`,
  `firebase`, `kubectl` and `whois` that fail with exit 97. It covers child processes too (bash and node
  scripts). A test that installs its own fake on PATH puts it in front of these, as before.
- **Network:** Python sockets may only reach loopback or a Unix socket. Anything else raises.

`test_000_live_guard.py` installs it at discovery, before any test module is imported, and asserts it is
active. A module that can do live writes when run alone also calls `install()` itself.
"""
from __future__ import annotations

import os
import re
import socket
import stat
import tempfile

BLOCKED = ("gcloud", "gsutil", "bq", "curl", "wget", "firebase", "kubectl", "whois")
EXIT = 97
SECRET = re.compile(r"(TOKEN|SECRET|PASSWORD|API_KEY|_KEY$|^GCP_KEY|CREDENTIALS)", re.I)
_installed = False


class LiveCallBlocked(AssertionError):
    pass


def _loopback(address) -> bool:
    if isinstance(address, (str, bytes)):
        return True  # AF_UNIX path
    host = str(address[0]).lower()
    return host in ("localhost", "::1") or host.startswith("127.") or host == "0.0.0.0"


def install() -> None:
    global _installed
    if _installed:
        return
    for k in [k for k in os.environ if SECRET.search(k)]:
        del os.environ[k]
    home = tempfile.mkdtemp(prefix="ainative-live-guard-")
    os.environ["CLOUDSDK_CONFIG"] = os.path.join(home, "gcloud")
    shims = os.path.join(home, "bin")
    os.makedirs(shims)
    for name in BLOCKED:
        path = os.path.join(shims, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"#!/bin/sh\necho 'live_guard: {name} is blocked in tests (ENG-1746)' >&2\nexit {EXIT}\n")
        os.chmod(path, stat.S_IRWXU)
    os.environ["PATH"] = shims + os.pathsep + os.environ.get("PATH", "")

    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def connect(self, address):
        if not _loopback(address):
            raise LiveCallBlocked(f"live_guard: a test opened a network connection to {address[0]} (ENG-1746)")
        return real_connect(self, address)

    def connect_ex(self, address):
        if not _loopback(address):
            raise LiveCallBlocked(f"live_guard: a test opened a network connection to {address[0]} (ENG-1746)")
        return real_connect_ex(self, address)

    socket.socket.connect, socket.socket.connect_ex = connect, connect_ex
    _installed = True


def installed() -> bool:
    return _installed
