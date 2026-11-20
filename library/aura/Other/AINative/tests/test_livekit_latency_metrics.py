#!/usr/bin/env python3
"""One `LATENCY_METRICS` tuple, six copies of `livekit_core.py`, and a lookalike next door.

**The rule.** `LATENCY_METRICS` names the per-turn latency metrics the Aurie LiveKit voice
agent emits on its forwarded log lines. All five are real: `e2e_latency_ms`, `eou_delay_ms`,
`transcription_delay_ms`, `llm_ttft_ms`, `tts_ttfb_ms`. `analyze-livekit-latency` builds its
percentile table by iterating the tuple, so a narrowed tuple silently drops columns from that
report — no error, no empty section, just a shorter table that still looks complete.

**Why this needed reconciling.** The canonical carried only two of the five while
`analyze-livekit-latency` carried all five, so the CLAUDE.md `diff -q` guard for this family
reported permanently red — and a permanently-red guard is one people learn to ignore. The
canonical was the stale side: its own comment said "Extend when the agent emits more", one
copy was extended, and the other five were not. Promoting the full set was a provable no-op,
because `LATENCY_METRICS` is referenced nowhere outside `analyze-livekit-latency`.

**Why a test on top of the `diff -q` guard.** The byte-diff is keyed on the filename
`livekit_core.py`, so it structurally cannot see a second `LATENCY_METRICS` under a different
name — the blind spot CLAUDE.md documents under *The drift guards compare copies, never
implementations*. There is one such lookalike already: `eng:investigate-voice-issue`'s
`correlate.py`. It is declared out of the family below, with a reason, rather than left as a
silent non-match, because the propagation instruction CLAUDE.md gives is to grep the
*behaviour* (`grep -rn LATENCY_METRICS`) — which surfaces it — and the obvious way to "fix" a
mismatch is to overwrite an unrelated module.

**The load-bearing assertions are the last two.** Every copy agreeing (`test_copies_agree`)
holds fine while all six agree on a *narrowed* tuple — which is the state this reconciled, and
which no byte-diff can flag. So the emitted set is pinned by name, and the sole consumer is
held to deriving its table from the tuple: the moment `report.py` hardcodes metric names, the
tuple is decorative everywhere and the next narrowing is invisible again.

Stdlib `unittest`, no network, no keys. Modules are parsed with `ast`, never imported —
`livekit_core.py` calls `sys.exit(2)` at import when the google-cloud libs are absent, and they
are not a CI dependency.
"""
from __future__ import annotations

import ast
import pathlib
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PLUGINS = REPO_ROOT / "plugins"

CANONICAL = PLUGINS / "eng/skills/analyze-livekit-usage/scripts/livekit_core.py"

# The set the LiveKit voice agent actually emits. Verified against the forwarded session-summary
# line in projects/firebase-auratech16/logs/aurie-voice-agent, which carries the `*_avg_ms`
# companion of each name. A narrowing here is the regression this file exists to catch.
EMITTED_METRICS = (
    "e2e_latency_ms",
    "eou_delay_ms",
    "transcription_delay_ms",
    "llm_ttft_ms",
    "tts_ttfb_ms",
)

# The only module that reads the tuple. Everything else in the family carries it unused.
SOLE_CONSUMER = PLUGINS / "eng/skills/analyze-livekit-latency/scripts/report.py"

# Deliberately OUT of this family, with a reason rather than a silent non-match — an
# unexplained new hit must still fail `test_family_membership_is_complete`.
#
# `eng:investigate-voice-issue`'s `correlate.py` names ElevenLabs Conversational AI metrics
# (`convai_ttf_audio_since_silence` and its components), read off the 11Labs conversation API.
# It shares only the constant name: different vendor, different transport, different units of
# meaning. It joins this family only if the LiveKit agent starts emitting `convai_*` names.
NOT_LIVEKIT = {
    PLUGINS / "eng/skills/investigate-voice-issue/scripts/correlate.py",
}


def _module_level_latency_metrics(path: pathlib.Path) -> tuple[str, ...] | None:
    """The literal string tuple assigned to a module-level LATENCY_METRICS, or None.

    Returns () when the assignment exists but is not a tuple of plain string literals —
    `correlate.py` builds its tuple from named constants, so it is present but not readable
    here. That is enough to identify it as a definer for membership purposes.
    """
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "LATENCY_METRICS" for t in node.targets):
            continue
        if not isinstance(node.value, ast.Tuple):
            return ()
        names = [e.value for e in node.value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        return tuple(names) if len(names) == len(node.value.elts) else ()
    return None


def _definers() -> dict[pathlib.Path, tuple[str, ...]]:
    """Every module under plugins/ defining a module-level LATENCY_METRICS — by shape, not name."""
    found: dict[pathlib.Path, tuple[str, ...]] = {}
    for path in sorted(PLUGINS.rglob("*.py")):
        metrics = _module_level_latency_metrics(path)
        if metrics is not None:
            found[path] = metrics
    return found


class LiveKitLatencyMetricsTest(unittest.TestCase):
    def test_family_membership_is_complete(self) -> None:
        """A new LATENCY_METRICS under any filename must be classified, not silently ignored."""
        unclassified = {
            p for p in _definers()
            if p.name != "livekit_core.py" and p not in NOT_LIVEKIT
        }
        self.assertEqual(
            set(),
            unclassified,
            "New module(s) define LATENCY_METRICS outside livekit_core.py. Either they are part "
            "of the LiveKit family (make them a copy of the canonical, covered by the CLAUDE.md "
            "diff -q guard) or they are not (add them to NOT_LIVEKIT with a reason). Do NOT "
            "reconcile an unrelated module into this family: "
            f"{sorted(str(p.relative_to(REPO_ROOT)) for p in unclassified)}",
        )

    def test_canonical_is_present(self) -> None:
        """The path CLAUDE.md names as canonical must exist and define the tuple."""
        self.assertIsNotNone(
            _module_level_latency_metrics(CANONICAL),
            f"{CANONICAL.relative_to(REPO_ROOT)} no longer defines LATENCY_METRICS. CLAUDE.md's "
            "'Canonical source for livekit_core.py' section names it as canonical — update both "
            "together.",
        )

    def test_copies_agree(self) -> None:
        """Every livekit_core.py copy resolves LATENCY_METRICS identically."""
        copies = {p: m for p, m in _definers().items() if p.name == "livekit_core.py"}
        self.assertGreater(len(copies), 1, "expected the canonical plus at least one copy")
        canonical = copies[CANONICAL]
        for path, metrics in copies.items():
            with self.subTest(copy=str(path.relative_to(REPO_ROOT))):
                self.assertEqual(
                    canonical,
                    metrics,
                    "This copy disagrees with the canonical. Edit the canonical first, then "
                    "propagate to every copy in the same PR — extending one copy alone is what "
                    "left this family's drift guard permanently red.",
                )

    def test_full_emitted_set_is_covered(self) -> None:
        """The tuple names every metric the agent emits — a narrowing drops report columns silently."""
        self.assertEqual(
            EMITTED_METRICS,
            _module_level_latency_metrics(CANONICAL),
            "LATENCY_METRICS no longer matches the set the LiveKit agent emits. Narrowing it "
            "removes rows from analyze-livekit-latency's percentile table with no error and no "
            "empty section — the table just looks complete and shorter. Widen it back, or update "
            "EMITTED_METRICS here if the agent genuinely stopped emitting one.",
        )

    def test_sole_consumer_derives_its_table_from_the_tuple(self) -> None:
        """report.py must iterate LATENCY_METRICS, not hardcode metric names.

        If it hardcodes, the tuple is unused in all six copies, the copies can agree on anything,
        and the next narrowing is invisible to every guard including this file.
        """
        tree = ast.parse(SOLE_CONSUMER.read_text(), filename=str(SOLE_CONSUMER))
        reads = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Attribute) and n.attr == "LATENCY_METRICS"
        ]
        # Three today: the percentile dict, the markdown table loop, and the JSON payload. An
        # attribute read is checked rather than a source substring so renaming the import alias
        # does not trip this, while hardcoding the metric names still does.
        self.assertGreaterEqual(
            len(reads),
            3,
            f"{SOLE_CONSUMER.relative_to(REPO_ROOT)} reads LATENCY_METRICS {len(reads)} time(s), "
            "expected at least 3 (percentile dict, markdown table, JSON payload). That tuple is "
            "load-bearing in exactly one module; every place this consumer hardcodes metric names "
            "instead is a place a narrowed tuple stops being observable, and once the last read "
            "goes the tuple is decorative in all six copies.",
        )


if __name__ == "__main__":
    unittest.main()
