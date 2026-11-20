#!/usr/bin/env python3
"""House rules for an incident write-up: a label header and fact-only wording.

Every document about an incident (a plan, a timeline, a close-out summary) starts with
"Draft — Privileged & Confidential — <date> — reflects information currently known", and its prose
describes what happened, when and what was observed, in the house wording (WORDS lists each refused
word and what to write instead).
render() adds the header and refuses a text that has one of those words; problems() lists them.
A write-up for counsel also carries no personal data (an email address, a full uid, an IP address:
uid prefixes and masked emails only) and no card id in its file name; check and wrap refuse both.
render() alone does not: a plan's local paste lines carry the full uid.

  writeup.py check FILE      exit 1 naming each refused word, each kind of personal data, a card id in
                             the file name and a missing header
  writeup.py wrap FILE       print the file with the header on top, or exit 1 as check does
"""
from __future__ import annotations

import datetime as dt
import ipaddress
import os
import re
import sys

LABEL = "Draft — Privileged & Confidential"
# The wording list: each refused word and what to write instead. Only the exact HTTP header
# `Authorization:` is exempt, and the firm's own words in a blockquote that opens with "> The firm's words:".
WORDS = [
    (r"breach\w*", '"incident" or "the firm\'s access"'),
    (r"unauthori[sz]\w*", '"the firm\'s access" or "access without written permission"'),
    (r"authori[sz]ation(?!:)\w*", "the access, described factually"),
    (r"expos(?:ed|ure)|compromis\w*|leak\w*", '"accessible", "accessed" or "downloaded" (only where confirmed)'),
    (r"stolen|steal\w*|exfiltrat\w*", '"downloaded" or "retrieved" (only where logs show it)'),
    (r"hack\w*|attack\w*", '"the firm\'s activity"'),
    (r"phi|health data|sensitive data", '"mood entries", "user-written notes" or "self-reported details"'),
    (r"violat\w*|non-?complian\w*|failed to", "the setting or practice, described factually"),
    (r"negligen\w*|should have|our mistake", "nothing: never write this"),
    (r"victims?|affected individuals?", '"users whose data was involved"'),
    (r"notification (?:is )?required|required to notify", '"whether notice is advisable or required"'),
]
# Personal data, by kind; never the value. A masked email (j***@…) and a uid prefix pass.
PERSONAL = [
    ("an email address", re.compile(r"(?<![\w.+-])[\w.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")),
    # A Firebase uid (24-36 letters and digits, both present) or a full uuid.
    ("a full uid", re.compile(r"(?<![A-Za-z0-9])(?=[A-Za-z0-9]{24,36}(?![A-Za-z0-9]))(?=[A-Za-z0-9]*\d)"
                              r"(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{24,36}"
                              r"|\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")),
]
# An IP address, v4 or v6 (compressed forms included): every candidate token is parsed, so a time
# (10:30:00) or a version (2.304.8) is not one.
# A dotted quad may sit next to a colon (host:port, "ip:" labels); a v6 candidate is any run with two colons.
IP_CANDIDATE = re.compile(r"(?<![\w.])\d{1,3}(?:\.\d{1,3}){3}(?![\w]|\.\d)"
                          r"|(?<![\w.:])[0-9A-Fa-f]*:[0-9A-Fa-f:.]*:[0-9A-Fa-f.]*(?![\w:]|\.\d)")
CARD_ID = re.compile(r"(?i)(?<![a-z])(prod|eng|engback|engmob|engweb|engsup|engprom)-\d+")
FIRM_QUOTE = re.compile(r"(?i)>\s*the firm's words:")
REFUSED = re.compile(r"(?i)(?<![`\w])(" + "|".join(w for w, _ in WORDS) + r")\b")
# The whole header, date included: a first line that only names the label is not the header.
HEADER = re.compile(r"^\*\*Draft — Privileged & Confidential\*\* — \d{4}-\d{2}-\d{2} — reflects information currently known\.$")


def header(date: str | None = None) -> str:
    day = date or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    return f"**{LABEL}** — {day} — reflects information currently known."


def instead(word: str) -> str:
    """The replacement for a refused word."""
    return next(use for w, use in WORDS if re.fullmatch(f"(?i){w}", word))


def prose_lines(text: str) -> list[str]:
    """Every line but those of a blockquote marked as the firm's words."""
    out, quoting = [], False
    for line in text.splitlines():
        q = line.lstrip().startswith(">")
        if q and not quoting and FIRM_QUOTE.match(line.lstrip()):
            quoting = True
        elif not q:
            quoting = False
        if not (q and quoting):
            out.append(line)
    return out


def problems(text: str) -> list[str]:
    """The refused words in the text, each once, in the order they appear. A blockquote that opens with
    `> The firm's words:` quotes the firm, marked as theirs, and is not checked; any other blockquote
    (a plan's paste lines) is."""
    prose = "\n".join(prose_lines(text))
    seen = []
    for m in REFUSED.finditer(prose):
        w = m.group(1).lower()
        if w not in seen:
            seen.append(w)
    return seen


def personal_data(text: str) -> list[str]:
    """The kinds of personal data in the text (outside the firm's quoted words), each once."""
    prose = "\n".join(prose_lines(text))
    found = [kind for kind, rx in PERSONAL if rx.search(prose)]
    if any(_is_ip(m.group(0)) for m in IP_CANDIDATE.finditer(prose)):
        found.append("an IP address")
    return found


def _is_ip(token: str) -> bool:
    """A candidate is an address as written, or with sentence punctuation (a period, a label colon) cut."""
    t = token.split("%")[0]
    # One label colon cut at a time, so a compressed address ending in "::" keeps its compression.
    bare = t.rstrip(".")
    for cand in (t, bare, bare[:-1] if bare.endswith(":") else bare, t.rstrip(".:")):
        try:
            ipaddress.ip_address(cand)
            return True
        except ValueError:
            continue
    return False


def card_in_name(path: str) -> bool:
    """A card id in the document's file name ties it to a card outside the privileged group."""
    return bool(CARD_ID.search(os.path.basename(path)))


def render(text: str, date: str | None = None) -> str:
    bad = problems(text)
    if bad:
        raise ValueError("write-up refused (house wording): " + "; ".join(f'"{w}": use {instead(w)}' for w in bad))
    return header(date) + "\n\n" + text


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2 or argv[0] not in ("check", "wrap"):
        print(__doc__, file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as f:
        text = f.read()
    refusals = [f"personal data: {k}: use a uid prefix or a masked email, never the value"
                for k in personal_data(text)]
    if card_in_name(argv[1]):
        refusals.append("card id in the file name: name the document without it")
    if argv[0] == "wrap":
        try:
            out = render(text)
        except ValueError as e:
            print(e, file=sys.stderr)
            return 1
        if refusals:
            print("write-up refused: " + "; ".join(refusals), file=sys.stderr)
            return 1
        print(out)
        return 0
    bad = problems(text)
    for w in bad:
        print(f"refused word: {w}: use {instead(w)}")
    for r in refusals:
        print(r)
    bad += refusals
    if not HEADER.match(text.split("\n", 1)[0].strip()):
        print(f"missing header: the first line must be \"{header('YYYY-MM-DD')}\"")
        bad.append("header")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
