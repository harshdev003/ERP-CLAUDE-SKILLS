#!/usr/bin/env python3
"""Detector #13 — repeated workaround notes across knowledge database rows.

A finding about *how a tool behaves*, written into the output the tool produced,
protects that one row and nothing else. The pathology has a written signature:
the note gets copy-pasted forward into the next row, and the next, until it
reads as a house convention rather than an open defect. `Learnings/Experiments`
carried one such note **word for word in 13 rows** for months.

Repetition — not the wording — is the signal. Two rows carrying the same
workaround sentence is a cluster; a cluster is a live defect in the mechanism
that produced those rows.

Rows are supplied as **blocks, carrying their hrefs** — Step 4's capture already
collects those from `rich_text[].href`, page mentions and `link_to_page`. Flat
`text` is accepted and treated as one block per line, but it **cannot acquit**:
in this teamspace a Linear reference reaches visible text as a bare `PROD-1234`
with the URL only in the href (measured on the real corpus — 12 card links, all
in hrefs, none in visible text; detector #8 puts the general figure at ~97%). A
text-only corpus therefore reports `acquittal: blind`, because a detector whose
close path silently cannot fire looks exactly like one whose rows are all
genuinely unlinked.

**A note that links its card is not a finding.** What the detector looks for is
a mechanism-level finding recorded *only* in the output. Once the note carries a
Linear link, the defect is filed and the next reader of that row can follow it —
the rule is satisfied, and the row is acquitted. This is not a convenience: the
suggested fix leaves the notes in place, so without acquittal the detector keeps
firing forever on rows whose defect is already filed, and the host reads a
still-firing `Approved + Fixed` finding as a *regression* and re-raises it at
full severity on every later run. A High finding that can never be closed is one
nobody reads.

Usage
-----
    workaround_notes.py --corpus rows.json [--min-rows 2] [--json]
    cat rows.json | workaround_notes.py

Corpus shape (built by the caller — this module never talks to Notion):

    {
      "database": {"path": "Learnings/Experiments",
                   "url":  "https://www.notion.so/..."},
      "scanned": 298,                  # rows whose body was actually fetched
      "total": 3087,                   # rows the database holds
      "rows": [
        {"path": "Learnings/Experiments",
         "title": "paywallTestimonial",
         "url":   "https://www.notion.so/...",
         "blocks": [{"text": "…", "hrefs": ["https://linear.app/…"]}]}
      ]
    }

The finding anchors on `database`, not on a row, and that is what makes its key
stable. Step 5 de-dupes and Step 3 reconciles on
`(page_url, category, subcategory)`, so anything in the key that moves when the
cluster gains a member loses the prior decision — silently, which is the one
failure mode this detector cannot afford. A row anchor moves exactly that way
(the 14th row to carry the note may sort before all thirteen), and no row is
"the" row anyway: the defect is in the mechanism that wrote every row in the
database. Callers that omit `database` get a row anchor and `key_stable: false`
on every finding.

`scanned` < `total` makes the run PARTIAL: an empty result then means "no
cluster in the part we read", never "no cluster". Reporting that as clean is the
same shape of failure the detector exists to catch.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import OrderedDict

# --------------------------------------------------------------------------
# Tuning
# --------------------------------------------------------------------------

# Workaround language. Deliberately loose: a marker only nominates a sentence
# for clustering, and a nominated sentence that appears once is never reported.
# So a false-positive marker costs nothing, while a missing one costs the whole
# cluster. Widen this list freely; tightening it needs an argument.
MARKERS = (
    "recoverable",
    "raw cut",
    "known issue",
    "known limitation",
    "instrumentation gap",
    "until this is fixed",
    "until that is fixed",
    "until it is fixed",
    "workaround",
    "work around",
    "not measurable",
    "cannot be measured",
    "can't be measured",
    "no readout",
    "pending a fix",
    "stopgap",
)

# Two sentences count as the same note when their word sets overlap this far.
# Copy-paste drift is the case: the same note carrying a different experiment
# name in one backticked token. Set high — this merges clusters, and an
# over-merge hands a reviewer two unrelated defects under one heading.
NEAR_DUPLICATE_JACCARD = 0.85

# A note is acquitted when its own block links a card. A bare `PROD-1234` does
# not count: the rule is that the note *links* the card, so a later reader can
# follow it, and a bare id is also indistinguishable from `GPT-4` or `nova-3`
# under any pattern loose enough to catch every team prefix. Erring toward
# still-reporting is the safe direction here — the reviewer closes it by making
# the reference a real link, which is what was asked for anyway.
CARD_LINK = "linear.app"

# Distinct rows needed before a repeated sentence is a finding.
MIN_ROWS = 2

# Cluster size at which the finding stops being "worth a look".
HIGH_SEVERITY_ROWS = 3

CATEGORY = "Repeated workaround note"

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_MD_NOISE = re.compile(r"[`*_~]+")
_NON_WORD = re.compile(r"[^a-z0-9]+")


# --------------------------------------------------------------------------
# Sentence handling
# --------------------------------------------------------------------------


def split_sentences(text):
    """Split body text into candidate sentences, one block at a time.

    Block-first matters: knowledge rows are written as bullets and table cells,
    and a bullet is a sentence whether or not it ends in a full stop.
    """
    return [s for s, _ in _sentences_with_block(row_blocks({"text": text}))]


def row_blocks(row):
    """A row's blocks as `{text, hrefs}`, whichever shape the caller supplied.

    Flat `text` degrades to one block per line with no hrefs — parseable, but
    unable to acquit (see `acquittal_blind`).
    """
    blocks = row.get("blocks")
    if blocks is not None:
        return [{"text": b.get("text") or "", "hrefs": list(b.get("hrefs") or ())}
                for b in blocks]
    return [{"text": line, "hrefs": []} for line in (row.get("text") or "").splitlines()]


def _sentences_with_block(blocks):
    """(sentence, containing block) pairs, in document order.

    The block comes along because acquittal is judged on it: "the note links its
    card" means the link sits in the same paragraph or bullet, not merely
    somewhere in a long row. A card named three sections away does not help the
    reader of the note.
    """
    out = []
    for block in blocks:
        for part in _SENTENCE_SPLIT.split(block["text"]):
            part = part.strip()
            if part:
                out.append((part, block))
    return out


def links_card(block):
    """Does this block link a card?

    The href arm is the one that matters. A Notion link shows human-readable
    text and carries the URL in `href`, so the visible text of a filed card is
    almost always the bare id — which deliberately does not acquit on its own.
    The text arm only catches a URL someone pasted literally.
    """
    if any(CARD_LINK in (h or "").lower() for h in block.get("hrefs") or ()):
        return True
    return CARD_LINK in (block.get("text") or "").lower()


def is_workaround(sentence):
    low = sentence.lower()
    return any(m in low for m in MARKERS)


def workaround_sentences(row):
    """Unacquitted workaround sentences — the ones whose block links no card."""
    return [s for s, block in _sentences_with_block(_as_blocks(row))
            if is_workaround(s) and not links_card(block)]


def acquitted_sentences(row):
    """Workaround sentences whose block already links a card. Counted, not dropped."""
    return [s for s, block in _sentences_with_block(_as_blocks(row))
            if is_workaround(s) and links_card(block)]


def _as_blocks(row):
    """Accept a row dict or a bare body string, for callers and tests alike."""
    return row_blocks(row if isinstance(row, dict) else {"text": row})


def fingerprint(sentence):
    """Normalise a sentence to a comparison key.

    Strips markdown emphasis and code markers (the same note reaches the
    detector as `` `SUL` `` from one writer and `SUL` from another), lowercases,
    and collapses every run of non-word characters to a single space — em-dashes
    and curly quotes are routine in these bodies and are not a difference in
    the note.
    """
    s = _MD_NOISE.sub("", (sentence or "").lower())
    s = _NON_WORD.sub(" ", s)
    return s.strip()


def _tokens(fp):
    return set(fp.split())


def _jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# --------------------------------------------------------------------------
# Clustering
# --------------------------------------------------------------------------


def cluster(rows, min_rows=MIN_ROWS):
    """Group workaround sentences that recur across *distinct* rows.

    A row repeating itself is not a cluster — the copy-forward pathology is
    between rows, and a single analysis restating its own caveat under two
    headings is ordinary writing.
    """
    # fingerprint -> {"sentence": first spelling seen, "rows": OrderedDict(url -> row)}
    groups = OrderedDict()

    for row in rows:
        seen_here = set()
        for sentence in workaround_sentences(row):
            fp = fingerprint(sentence)
            if not fp or fp in seen_here:
                continue
            seen_here.add(fp)
            key = _match_existing(groups, fp)
            if key is None:
                key = fp
                groups[key] = {"rows": OrderedDict(), "spellings": {}}
            g = groups[key]
            g["rows"].setdefault(_row_key(row), row)
            seen, _ = g["spellings"].get(fp, (0, sentence))
            g["spellings"][fp] = (seen + 1, sentence)

    # Second pass: rows whose note already links a card. They are deliberately
    # NOT cluster members — the finding must name only the rows still needing a
    # link — but they are counted, because an acquittal nobody totals is just a
    # quieter drop (*Tag, don't drop, wherever the output persists*).
    for row in rows:
        for sentence in acquitted_sentences(row):
            fp = fingerprint(sentence)
            key = _match_existing(groups, fp) if fp else None
            if key is None:
                continue
            groups[key].setdefault("linked", OrderedDict())
            groups[key]["linked"].setdefault(_row_key(row), row)

    clusters = []
    for g in groups.values():
        if len(g["rows"]) < min_rows:
            continue
        members = sorted(g["rows"].values(), key=_row_key)
        fp, sentence = _dominant(g["spellings"])
        clusters.append(
            {
                "fingerprint": fp,
                "sentence": sentence,
                "rows": members,
                "size": len(members),
                "linked_rows": len(g.get("linked") or ()),
            }
        )
    # Biggest first; ties broken by the anchor so the report is reproducible.
    clusters.sort(key=lambda c: (-c["size"], _row_key(c["rows"][0])))
    return clusters


def _dominant(spellings):
    """The cluster's representative spelling: the one most rows carry.

    Not the first one seen. Arrival order is a property of the scan, not of the
    defect — a corpus built from a different subset (rows edited since the last
    run, say) hands the same cluster a different first member, and a key derived
    from it would change under the caller rather than under the world. Ties go
    to the lexicographic minimum so the pick never depends on dict order.
    """
    fp = min(spellings, key=lambda k: (-spellings[k][0], k))
    return fp, spellings[fp][1]


def _match_existing(groups, fp):
    """Return the key of an existing near-duplicate group, or None."""
    if fp in groups:
        return fp
    toks = _tokens(fp)
    for key in groups:
        if _jaccard(toks, _tokens(key)) >= NEAR_DUPLICATE_JACCARD:
            return key
    return None


def _row_key(row):
    """Stable identity for a row. URL first — a title can be edited."""
    return (row.get("url") or "", row.get("title") or "")


# --------------------------------------------------------------------------
# Findings
# --------------------------------------------------------------------------


def severity(size):
    return "High" if size >= HIGH_SEVERITY_ROWS else "Medium"


def subcategory(fp):
    """Short stable hash of the note — half of the finding's identity.

    Step 5 de-dupes and Step 3 reconciles on `(page_url, category,
    subcategory)`, so the key has to survive the cluster *growing*: the note
    identifies the defect, the cluster size does not. That is what makes a
    2-row cluster dismissed in one run come back as High once it reaches three —
    Step 5 re-surfaces a rejected finding whose severity escalated, and it can
    only do that if the key held. `fp` is the cluster's *dominant* spelling
    (`_dominant`), never its first-seen one, and the other half of the key is
    the database rather than a member row (`_anchor`) — both for the same
    reason: anything keyed to which rows this particular scan happened to read
    changes under the caller instead of under the world.
    """
    return "note-" + hashlib.sha1(fp.encode("utf-8")).hexdigest()[:8]


def acquittal_blind(corpus):
    """True when no row was supplied as blocks, so no href can ever be seen.

    Judged on the corpus *shape*, not on whether any href happened to be
    present: a caller that passes blocks is href-aware even on a row set that
    links nothing, while a caller that passes flat text can never acquit no
    matter what the rows say.
    """
    rows = corpus.get("rows") or []
    return bool(rows) and not any(r.get("blocks") is not None for r in rows)


def findings(corpus, min_rows=MIN_ROWS):
    rows = corpus.get("rows") or []
    db = corpus.get("database") or {}
    blind = acquittal_blind(corpus)
    clusters = cluster(rows, min_rows=min_rows)
    out = []
    for c in clusters:
        anchor_title, anchor_url, stable = _anchor(db, c["rows"])
        out.append(
            {
                "severity": severity(c["size"]),
                "category": CATEGORY,
                "subcategory": subcategory(c["fingerprint"]),
                "key_stable": stable,
                "linked_rows": c["linked_rows"],
                "acquittal_blind": blind,
                "page_title": anchor_title,
                "page_url": anchor_url,
                "note": _trim(c["sentence"]),
                "description": (
                    f"{c['size']} rows carry the same workaround note: "
                    f"“{_trim(c['sentence'])}”. A note about how a tool behaves, "
                    "written into the tool's output, protects that one row — the next "
                    "reader gets the same broken result plus a caveat."
                ),
                "suggested_fix": (
                    "File a card against the mechanism the note describes, fix it there, "
                    "and add the card's Linear link to the note in every row listed below. "
                    "Leave the notes themselves: each is a true statement about that "
                    "analysis. What they must not stay is the only place the defect is "
                    "recorded — and the link is what closes this finding, since a linked "
                    "note is acquitted and the detector stops naming that row."
                ),
                "related_pages": [r.get("url") for r in c["rows"]],
                "rows": [
                    {"title": r.get("title"), "url": r.get("url"), "path": r.get("path")}
                    for r in c["rows"]
                ],
            }
        )
    return out


def _anchor(db, members):
    """The page a finding points at, and whether its key is stable.

    The database when the caller declared one — constant as the cluster grows.
    Otherwise the member whose URL sorts first, which is the best available
    guess and is *not* stable: the next row to carry the note may sort before
    every current member, and the prior run's decision is then keyed to a page
    this run no longer names.
    """
    if db.get("url"):
        return db.get("path") or db.get("title"), db["url"], True
    first = members[0]
    return first.get("title"), first.get("url"), False


def _acquittal_note(blind):
    """The acquittal verdict, derived from the corpus shape and nothing else.

    Deliberately independent of whether coverage was declared. The two are
    unrelated properties, and the combination most likely to arrive from a
    caller still passing the old flat `text` rows is *both* undeclared coverage
    *and* blindness — so letting the coverage branch decide this note silences
    the warning in exactly the case it exists for.
    """
    if blind:
        return ("BLIND — rows were supplied as flat text, so a card linked the normal way "
                "(a Notion hyperlink whose visible text is the bare id) cannot be seen; "
                "no row can be acquitted and every finding here may already be filed")
    return "reads block hrefs"


def coverage(corpus):
    """Scan coverage, and whether a clean result may be reported as clean."""
    scanned = corpus.get("scanned")
    total = corpus.get("total")
    blind = acquittal_blind(corpus)
    complete = scanned is not None and total is not None and scanned >= total
    if scanned is None or total is None:
        note = "coverage not declared by the caller — treat as PARTIAL"
    elif complete:
        note = "full scan"
    else:
        note = (f"PARTIAL — scanned {scanned} of {total} rows; "
                "an empty result means no cluster in the part that was read")
    return {
        "scanned": scanned,
        "total": total,
        "complete": complete,
        "acquittal_blind": blind,
        "acquittal_note": _acquittal_note(blind),
        "note": note,
    }


def _trim(sentence, width=160):
    s = re.sub(r"\s+", " ", sentence).strip()
    return s if len(s) <= width else s[: width - 1] + "…"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def render(corpus, results, cov):
    lines = []
    lines.append(f"Detector #13 — {CATEGORY}")
    lines.append(f"Coverage: {cov['note']}")
    lines.append(f"Acquittal: {cov['acquittal_note']}")
    lines.append("")
    if not results:
        verdict = "No repeated workaround notes" if cov["complete"] else "No repeated workaround notes in the scanned rows"
        lines.append(verdict + ".")
        return "\n".join(lines)
    for f in results:
        key = f"{f['subcategory']}{'' if f['key_stable'] else ' (row-anchored — key not stable)'}"
        lines.append(f"[{f['severity']}] {len(f['rows'])} rows — {key}")
        lines.append(f"  in {f['page_title']}")
        if f["linked_rows"]:
            lines.append(f"  ({f['linked_rows']} further row(s) already link a card)")
        lines.append(f"  “{f['note']}”")
        for r in f["rows"]:
            lines.append(f"    - {r['title']}  {r['url']}")
        lines.append("")
    return "\n".join(lines).rstrip()


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--corpus", help="JSON corpus file (default: stdin)")
    ap.add_argument("--min-rows", type=int, default=MIN_ROWS,
                    help=f"distinct rows before a repeated note is a finding (default {MIN_ROWS})")
    ap.add_argument("--json", action="store_true", help="emit findings as JSON")
    args = ap.parse_args(argv)

    raw = open(args.corpus, encoding="utf-8").read() if args.corpus else sys.stdin.read()
    corpus = json.loads(raw)

    results = findings(corpus, min_rows=args.min_rows)
    cov = coverage(corpus)

    if args.json:
        print(json.dumps({"coverage": cov, "findings": results}, ensure_ascii=False, indent=2))
    else:
        print(render(corpus, results, cov))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
