"""Project → app-family + suspect-repo routing.

Snapshot of the 12 aura-health Bugsnag projects as of 2026-08-25.
The runtime project list is fetched live via the Bugsnag API; this
module provides the slug → family + repo mapping that drives:

- stacktrace formatter selection (per family)
- PII guardrail tightness (Aurie projects = high-PII, carry user-conversation text)
- default Linear team for any card created from a triage (Mode 4, future)
- suspect repo passed to eng:code-investigator (Mode 2.5)

`repo` is the field eng:code-investigator clones and greps, and a wrong
value there does not error — the subagent searches the wrong codebase and
reports a confident "no matching code found". Verify each entry against
the project's own `api_key` rather than inferring it from the slug: read
`api_key` from `GET /organizations/<org-id>/projects`, then grep the
candidate repo for that literal (Info.plist, AndroidManifest.xml,
*.xcconfig, .env.dev). Slug and repo names do not track each other.
Per-row evidence: ../references/app-family-routing.md.

When a new Bugsnag project is added, append it to PROJECTS below.
The skill gracefully degrades for unknown slugs — still works for
triage / focus / trace, just no family-aware formatting.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AppFamily:
    """The app-family classification for one Bugsnag project."""
    family: str          # display label, e.g. "Aura / web (consumer)"
    stack: str           # formatter dispatch key: ios-native | electron | react | react-native | macos-native | unknown
    repo: str | None     # AuraHealth/<repo> if known, else None
    high_pii: bool       # tighten redaction even in Mode 3 (Aurie projects carry user-conversation text)


PROJECTS: dict[str, AppFamily] = {
    # Aurie surfaces (high-PII — carry user-conversation text in metadata)
    "ai-aurie-ios":     AppFamily("Aurie / mobile",        "ios-native",   "AuraHealth/AuraConsumerApp",   True),
    "ai-aurie-macos":   AppFamily("Aurie / desktop",       "electron",     "AuraHealth/ConsumerAIDesktop", True),
    "aiweb":            AppFamily("Aurie / web",           "react",        "AuraHealth/AIWeb",             True),
    # Aura mobile (RN, single codebase for iOS + Android)
    "aura-ios":         AppFamily("Aura / mobile",         "react-native", "AuraHealth/Aura",              False),
    "aura-android":     AppFamily("Aura / mobile",         "react-native", "AuraHealth/Aura",              False),
    # Aura desktop
    "aura-aimacos":     AppFamily("Aura / desktop",        "macos-native", None,                           False),
    # Aura web (five audience-specific apps)
    "aura-userweb":     AppFamily("Aura / web (consumer)", "react",        "AuraHealth/UserWeb",           False),
    "aura-coachweb":    AppFamily("Aura / web (coach)",    "react",        "AuraHealth/CoachWeb",          False),
    "aura-businessweb": AppFamily("Aura / web (B2B)",      "react",        "AuraHealth/BusinessWeb",       False),
    "aura-crmweb":      AppFamily("Aura / web (internal)", "react",        "AuraHealth/CRMWeb",            False),
    "aura-investweb":   AppFamily("Aura / web (investor)", "react",        "AuraHealth/InvestWeb",         False),
    # Aura embed
    "aura-front-plugin":AppFamily("Aura / embed",          "react",        "AuraHealth/AuraFrontPlugin",   False),
}

# Default for unknown slugs — pessimistic on PII so we don't accidentally
# dump user-conversation text for a project we haven't classified yet.
UNKNOWN = AppFamily("Unknown", "unknown", None, True)


def lookup(slug: str | None) -> AppFamily:
    """Return the AppFamily for a Bugsnag project slug (or UNKNOWN if missing)."""
    if not slug:
        return UNKNOWN
    return PROJECTS.get(slug, UNKNOWN)


def fuzzy_match(name: str, available: list[str]) -> str | None:
    """Cheap fuzzy match for free-text project names against live slugs.

    Used when the user types e.g. "userweb" — we resolve to "aura-userweb".
    Returns None if there's no match or multiple ambiguous matches.
    """
    if not name:
        return None
    n = name.lower().replace(" ", "-").replace("_", "-")
    if n in available:
        return n
    contains = [s for s in available if n in s or s in n]
    return contains[0] if len(contains) == 1 else None
