"""Rules a recommendations experiment flag has to satisfy before it can ship.

Pure, testable, and separated from the ship mechanics on purpose: the failure
this file exists to prevent is completely silent in production, so it is the
part that must be covered by tests rather than by a careful reader.

ContentService decides whether to assign an experiment to a user in
`src/models/experiment.js`:

    experiment.startsWith(EXPERIMENT_STARTS_WITH)          // config: "content"
      || experiments.includes(experiment)                  // config: includeList

A flag satisfying neither is assigned to **nobody**. Nothing errors. The
experiment runs to completion, every user sees control, and the readout is a
dead heat that looks like a genuine null result.
"""

from __future__ import annotations

import re
from typing import Iterable, List, Sequence, Tuple

# config.experiment.startsWith in ContentService.
REQUIRED_PREFIX = "content"

# Experiment names are camelCase identifiers in the Aura experiment service.
NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9]*$")

# `ab:{experimentName}: {title}` — the Linear card convention both loops share.
CARD_TITLE_RE = re.compile(r"^ab:(?P<flag>[^:]+):\s*(?P<title>.+)$")


def flag_from_card_title(title: str) -> str:
    """Pull the flag out of an `ab:{flag}: {title}` card title, or '' if absent."""
    m = CARD_TITLE_RE.match((title or "").strip())
    return m.group("flag").strip() if m else ""


def check_flag(name: str, include_list: Sequence[str] = ()) -> Tuple[bool, List[str]]:
    """Can ContentService actually assign this flag? Returns (ok, problems).

    `include_list` is `config.experiment.includeList`. Passing it lets a
    deliberately-named legacy flag through, which is the documented escape
    hatch — but the prefix is preferred, because the convention is what makes
    the gate readable to someone reading the config six months later.
    """
    problems: List[str] = []
    flag = (name or "").strip()

    if not flag:
        return False, ["no experiment name"]
    if not NAME_RE.match(flag):
        problems.append(
            f"{flag!r} is not a valid experiment name — expected camelCase letters and digits")

    listed = flag in set(include_list)
    prefixed = flag.startswith(REQUIRED_PREFIX)
    if not prefixed and not listed:
        problems.append(
            f"{flag!r} does not start with {REQUIRED_PREFIX!r} and is not in "
            f"experiment.includeList — ContentService would assign it to NOBODY, "
            f"silently, and the experiment would read as a dead heat")
    elif not prefixed and listed:
        problems.append(
            f"NOTE: {flag!r} relies on experiment.includeList rather than the "
            f"{REQUIRED_PREFIX!r} prefix. It will be assigned, but the convention "
            f"is what makes the gate readable — prefer renaming.")

    hard = [p for p in problems if not p.startswith("NOTE:")]
    return not hard, problems


def suggest_flag(name: str) -> str:
    """A compliant name for a flag that would be assigned to nobody."""
    flag = (name or "").strip()
    if not flag:
        return ""
    if flag.startswith(REQUIRED_PREFIX):
        return flag
    return REQUIRED_PREFIX + flag[0].upper() + flag[1:]
