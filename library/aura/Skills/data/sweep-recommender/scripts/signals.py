"""Signal definitions, the materiality filter, and the priors check.

This is the judgment layer of the sweep and the part worth testing: deciding
what counts as a breach, what counts as an idea rather than a tuning artefact,
and what has already been answered. Everything here is pure — no BigQuery, no
GCP logs, no Notion.

The sweep's failure mode is not missing a signal. It is proposing so many
marginal ones that the channel stops being read, so the filters below are
deliberately strict and every rejection carries its reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

# Pipeline layers — the `Domain` values in the Aura Recommendations Ideas database,
# and what the sweep rotates over (Features rotates over product screens).
LAYERS = (
    "retrieval",
    "ranking",
    "blend weights",
    "filters",
    "candidate supply",
    "instrumentation",
)

# Rejection reasons that retire an idea permanently. `timing` and `tech cost`
# mean re-propose later, so a row carrying one of those does NOT suppress.
RETIRING_REASONS = ("already tested", "no longer valid")

# A candidate must clear the offline bar by this much, not by a hair inside the
# confidence interval. Expressed as a ratio to the baseline.
DEFAULT_MATERIALITY_MARGIN = 0.03


@dataclass
class Signal:
    """One measured production quantity with a threshold and a stored prior."""

    key: str
    layer: str
    value: float
    threshold: float
    direction: str  # "above" = breach when value > threshold, "below" = inverse
    prior: Optional[float] = None
    surface: str = ""
    unit: str = ""
    evidence: str = ""

    def __post_init__(self):
        if self.direction not in ("above", "below"):
            raise ValueError(f"direction must be 'above' or 'below', got {self.direction!r}")
        if self.layer not in LAYERS:
            raise ValueError(f"unknown layer {self.layer!r}; expected one of {LAYERS}")

    @property
    def breached(self) -> bool:
        if self.direction == "above":
            return self.value > self.threshold
        return self.value < self.threshold

    @property
    def moved(self) -> bool:
        """True when the value has drifted from its stored prior.

        A breach that has held steady for months is a known condition, not news.
        With no prior every breach is news, which is right for a first run.
        """
        if self.prior is None:
            return True
        if self.prior == 0:
            return self.value != 0
        return abs(self.value - self.prior) / abs(self.prior) >= 0.10


@dataclass
class Candidate:
    """A proposed idea, in the shape `product:record-ideas --from-json` accepts."""

    title: str
    description: str
    layer: str
    mechanism: str = ""
    surface: str = ""
    category: str = ""
    lever: str = ""
    impact: str = "medium"
    effort: str = "M"
    rationale: str = ""
    hypothesis: str = ""
    offline_ratio: Optional[float] = None
    evidence: str = ""
    source: str = "telemetry"

    def to_idea(self) -> dict:
        """The record-ideas idea object. `screen` carries the surface and
        `domain` the pipeline layer — this loop's reading of the shared schema."""
        return {
            "title": self.title,
            "description": self.description,
            "screen": self.surface,
            "domain": self.layer,
            "category": self.category,
            "lever": self.lever,
            "impact": self.impact,
            "effort": self.effort,
            "rationale": self.rationale or self.evidence,
            "hypothesis": self.hypothesis,
        }


# --------------------------------------------------------------------------
# the materiality filter
# --------------------------------------------------------------------------

# A mechanism has to name something that changes for a user. These are the
# words a grid search reaches for when it has nothing to say.
_TUNING_ONLY = (
    "tune", "tuning", "adjust the constant", "bump the weight", "increase the weight",
    "decrease the weight", "change the parameter", "optimise the value", "optimize the value",
)


def is_material(
    candidate: Candidate,
    *,
    margin: float = DEFAULT_MATERIALITY_MARGIN,
) -> Tuple[bool, str]:
    """Should this reach the database, or only the digest?

    Two hard rules, both required:

    1. **Clears the bar by a margin.** A grid search will happily surface a
       ratio of 1.004 forever.
    2. **Names a mechanism.** One sentence saying what changes for a user.
       "Tune a constant" is not a mechanism; "surface content from coaches the
       user already follows" is.

    A candidate passing the first and failing the second is a tuning finding —
    real, but it belongs in the weekly digest, not in the backlog.
    """
    if candidate.offline_ratio is not None and candidate.offline_ratio < 1.0 + margin:
        return False, (
            f"offline ratio {candidate.offline_ratio:.3f} is inside the "
            f"{margin:.0%} materiality margin"
        )

    mech = (candidate.mechanism or "").strip()
    if not mech:
        return False, "no mechanism stated"
    if len(mech.split()) < 4:
        return False, f"mechanism too thin to judge: {mech!r}"
    low = mech.lower()
    if any(phrase in low for phrase in _TUNING_ONLY) and candidate.source == "harness":
        return False, f"mechanism describes tuning, not a change for a user: {mech!r}"
    return True, "clears the margin and names a mechanism"


# --------------------------------------------------------------------------
# priors
# --------------------------------------------------------------------------


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())


def check_priors(
    candidates: Sequence[Candidate],
    existing: Iterable[Mapping[str, str]],
) -> Tuple[List[Candidate], List[Tuple[Candidate, str]]]:
    """Drop candidates the loop has already answered.

    `existing` is the loop database's rows — at minimum `title`, `status` and
    `rejection_reason`. Matching is on normalised title within the same layer,
    which is deliberately conservative: the fingerprint dedup in
    `record-ideas` is the real safety net, and suppressing too eagerly here
    would hide a genuinely new idea that happens to be worded similarly.

    An idea rejected for `timing` or `tech cost` is NOT suppressed — those mean
    re-propose later, which is the whole reason the vocabulary is fixed.
    """
    index: Dict[Tuple[str, str], Mapping[str, str]] = {}
    for row in existing:
        index[(_norm(row.get("title", "")), _norm(row.get("domain", "")))] = row

    kept: List[Candidate] = []
    suppressed: List[Tuple[Candidate, str]] = []
    for c in candidates:
        row = index.get((_norm(c.title), _norm(c.layer)))
        if row is None:
            kept.append(c)
            continue
        status = _norm(row.get("status", ""))
        reason = _norm(row.get("rejection_reason", ""))
        if status in ("rejected", "abandoned") and reason in RETIRING_REASONS:
            suppressed.append((c, f"already answered: {status} / {reason}"))
        elif status in ("rejected", "abandoned"):
            kept.append(c)  # timing / tech cost / no reason — re-proposable
        else:
            suppressed.append((c, f"already in the backlog as '{status}'"))
    return kept, suppressed


# --------------------------------------------------------------------------
# rotation
# --------------------------------------------------------------------------


def next_layer(last_seen: Mapping[str, str]) -> str:
    """The least-recently-swept pipeline layer.

    `last_seen` maps layer -> ISO date of the most recent idea first seen in it.
    A layer that has never been swept sorts first, so a new loop covers the
    whole pipeline before repeating itself.
    """
    return min(LAYERS, key=lambda layer: (last_seen.get(layer) or ""))
