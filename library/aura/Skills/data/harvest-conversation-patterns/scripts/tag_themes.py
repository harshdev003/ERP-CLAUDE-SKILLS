#!/usr/bin/env python3
"""Assign `Themes` to rated entries, deterministically, under one stated policy.

The policy, in full:

  Topical tags only, matched by keyword against the entry's bot-analysis text
  (plus its feedback and signal line). The eight tags below are the whole
  auto-derivable set. Every other tag in the taxonomy is left to a human or to a
  later analysis pass.

Why only eight. The technique/UX half of the taxonomy — `reflection_depth`,
`therapeutic_honesty`, `distress_containment`, `pacing_mismatch`,
`coping_tools_gap`, `session_length`, `realism` and the rest — are semantic
judgements about how Aurie performed. Fitting them statistically against a
hand-tagged corpus returns only boilerplate artifacts of the analysis template,
not topical signal, so a keyword rule for them would be noise wearing a tag's
name. An absent technique tag therefore means "not derivable here", never "did
not occur", and any reader aggregating this column needs to know that.

`somatic_relief` and `breakthrough_moment` are topical but sit below the bar:
measured at 0.31 and 0.25 precision against a hand-tagged corpus, so they are
deliberately excluded rather than written at a quality the counts would hide.

Accuracy, measured against 512 independently hand-tagged rows drawn from the same
period the rules were fitted on:

    micro precision 0.82, recall 0.98
    boundaries              0.97 / 1.00      grief_loss          0.94 / 0.99
    identity_acceptance     0.98 / 0.89      betrayal_recovery   0.91 / 1.00
    self_trust              0.88 / 0.94      relationship_attachment 0.81 / 1.00
    faith                   0.75 / 0.60      parenting_family    0.63 / 0.99

`parenting_family` is the loosest rule in the set and the one to tighten first if
this is revisited: it fires on any family noun, so a session that merely mentions
a mother while being about something else picks it up.

Read those as the ceiling, not a guarantee: they were measured where the rules
were fitted. Applied to an older stretch of the corpus tagged under a different
policy, precision fell to 0.64 and 0.30 — mostly because that corpus assigned
far fewer topical tags per row, not because the rules degraded. The lesson is
that this column is only comparable across a span tagged by one policy, which is
exactly what running this at harvest time is for.

The analysis text is what carries the topic. Feedback alone does not: the
majority of entries say nothing more than "Helpful response", and scoring on
feedback plus the signal line alone measures recall 0.05. An entry whose thread
carries no analysis is therefore left untagged rather than guessed at.

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys

# The auto-derivable set. Keep in sync with the taxonomy table in SKILL.md and
# the seed list in the Signals/Conversation Patterns schema.
RULES: dict[str, str] = {
    "relationship_attachment": (
        r"\brelationship|\bpartner\b|\bboyfriend|\bgirlfriend|\bhusband|\bwife\b|\bspouse"
        r"|\bex\b|\bex-|\bdating\b|\battachment|\bbreakup|\bbroke up|\bmarriage|\bmarried"
        r"|\bfianc|\bsituationship|\bcrush\b"
    ),
    "parenting_family": (
        r"\bfamily|\bmother\b|\bmom\b|\bfather\b|\bdad\b|\bparent|\bdaughter|\bson\b"
        r"|\bchild|\bkids?\b|\bsibling|\bsister\b|\bbrother\b|\bgrandmother|\bgrandma"
        r"|\bin-law|\bnephew|\bniece"
    ),
    "grief_loss": (
        r"\bgrief|\bgrieving|\bbereave|\bmourn|\bdied\b|\bdeath\b|\bpassed away"
        r"|\bloss of|\bfuneral|\bwidow"
    ),
    "boundaries": r"\bboundar",
    "identity_acceptance": (
        r"\bself-worth|\bself worth|\bself-accept|\bidentity\b|\bnot good enough"
        r"|\bworthless|\bself-esteem|\bself esteem|\bwho (?:i|she|he|they) (?:am|is|are)\b"
    ),
    "betrayal_recovery": (
        r"\binfidelity|\bcheat(?:ed|ing)\b|\baffair\b|\bbetray|\bbroken trust"
        r"|\blied to (?:her|him|them|me)\b"
    ),
    "self_trust": (
        r"\bself-trust|\btrust (?:her|his|their|my|him|herself|himself|themselves)? ?own\b"
        r"|\bsecond-guess|\bdoubt (?:her|his|their|my)\w* (?:own )?judg"
    ),
    "faith": r"\bfaith\b|\bgod\b|\bprayer\b|\bpray(?:ing|ed)?\b|\bspiritual|\bchurch\b|\bscripture",
}

_COMPILED = {name: re.compile(pat, re.I) for name, pat in RULES.items()}


def tag(text: str) -> list[str]:
    """Return the sorted topical tags matching `text`. Empty list is a valid answer."""
    if not text:
        return []
    return sorted(name for name, pat in _COMPILED.items() if pat.search(text))


def tag_entry(entry: dict) -> list[str]:
    """Tag one `fetch_sweep_analyses` entry.

    An entry whose thread carries no bot analysis is left untagged — feedback
    alone scores recall 0.05, so a tag derived from it would be a guess.
    """
    analysis = (entry.get("bot_analysis") or "").strip()
    if not analysis:
        return []
    parts = [analysis, entry.get("feedback") or "", entry.get("signal") or ""]
    return tag(" ".join(parts))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--input", required=True, help="fetch_sweep_analyses.py JSON")
    ap.add_argument("--json", dest="out", help="write {rating_ts: [themes]} here")
    args = ap.parse_args()

    with open(args.input, encoding="utf-8") as fh:
        entries = json.load(fh)["entries"]

    mapping: dict[str, list[str]] = {}
    no_analysis = 0
    for e in entries:
        if not (e.get("bot_analysis") or "").strip():
            no_analysis += 1
        themes = tag_entry(e)
        if themes:
            mapping[str(e["rating_ts"]).strip()] = themes

    counts: dict[str, int] = {}
    for themes in mapping.values():
        for t in themes:
            counts[t] = counts.get(t, 0) + 1

    print(f"entries: {len(entries)}")
    print(f"  no analysis (left untagged): {no_analysis}")
    print(f"  tagged (>=1 theme): {len(mapping)}")
    for name, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"    {n:5d}  {name}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(mapping, fh)
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
