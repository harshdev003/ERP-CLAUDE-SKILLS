"""Field names only: mask anything in a scanned string that looks like a value, and refuse to write it.

Everything this skill emits comes from code (event names, property names, paths) or from vendor
aggregates (counts). A code literal can still carry a value (a test email, a hard-coded uid), so
every string is masked on the way in and every file is checked on the way out.
"""
import json
import os
import re

PATTERNS = {
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "uuid": re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"),
    # A Firebase uid: 28 alphanumerics with a digit, an upper and a lower case letter.
    "uid": re.compile(r"\b(?=[A-Za-z0-9]{28}\b)(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Z])(?=[A-Za-z0-9]*[a-z])[A-Za-z0-9]{28}\b"),
    "ipv4": re.compile(r"\b(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}\b"),
    "phone": re.compile(r"\+\d[\d ()-]{8,}\d"),
    "token": re.compile(r"\b(?:sk|pk|ntn|xox[abp])[-_][A-Za-z0-9_-]{16,}\b|\b[0-9a-f]{32,}\b"),
}


class GuardError(Exception):
    """Output would carry something that looks like a value or an id."""


def mask(text, counter=None):
    """Replace anything value-like with <masked:kind>; count each mask in `counter` (a dict)."""
    if not isinstance(text, str):
        return text
    for kind, pattern in PATTERNS.items():
        text, n = pattern.subn(f"<masked:{kind}>", text)
        if n and counter is not None:
            counter[kind] = counter.get(kind, 0) + n
    return text


def find(text):
    """[(kind, count)] of value-like strings still in `text`."""
    return [(kind, len(p.findall(text))) for kind, p in PATTERNS.items() if p.search(text)]


def check(text):
    hits = find(text)
    if hits:
        raise GuardError("refusing to write output that looks like it holds values or ids: "
                         + ", ".join(f"{k} x{n}" for k, n in hits))
    return text


def write_text(path, text):
    """Write `text` (mode 600) only after check() passes."""
    check(text)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def write_json(path, data):
    write_text(path, json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
