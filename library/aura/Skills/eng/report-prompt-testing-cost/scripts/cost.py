"""Rate derivation for `eng:report-prompt-testing-cost`. No network, no I/O.

OpenAI exposes dollars per **project**, never per **API key**. So a per-key
dollar figure has to be derived: back-calculate an empirical rate per
`(model, service_tier, dimension)` from org-wide spend divided by org-wide
tokens, then apply that rate to one key's own tokens.

Two properties of that derivation are load-bearing, and both fail silently.

**The rate must be per dimension, never blended.** Cached input is an order of
magnitude cheaper than uncached input, and the observed cache-hit rate on the
prompt-testing keys swings between 30% and 96% day to day. A single blended
$/token collapses that spread, so a cache-heavy day is over-charged and a
cache-light day under-charged — by multiples, not percentages. `token_fields`
and `LINE_ITEM_DIMENSIONS` keep the four dimensions apart end to end.

**The service tier has to be normalised across the two endpoints.** They spell
it differently: `/usage/completions` returns `service_tier: "flex-tier"`, while
`/costs` prefixes the line item `"flex | gpt-5.4, input"`. Unnormalised, the
usage side looks up a rate the cost side filed under a different key, misses,
and the traffic bills as **$0.00** — real requests, real tokens, no dollars, no
error. `normalize_tier` is the fix and `tests/test_prompt_testing_cost.py`
pins it.
"""
from __future__ import annotations

import re
from collections import defaultdict

# The four billed token dimensions, kept distinct end to end.
DIMENSIONS = ("uncached", "cached", "cachewrite", "output")

# `/usage/completions` result field -> dimension.
USAGE_FIELDS = {
    "input_uncached_tokens": "uncached",
    "input_cached_tokens": "cached",
    "input_cache_write_tokens": "cachewrite",
    "output_tokens": "output",
}

# Trailing segment of a `/costs` line item -> dimension. OpenAI has shipped
# several spellings; unknown ones are dropped rather than guessed at.
LINE_ITEM_DIMENSIONS = {
    "input": "uncached",
    "input tokens": "uncached",
    "uncached input": "uncached",
    "cached input": "cached",
    "cached input tokens": "cached",
    "input cached": "cached",
    "cache write": "cachewrite",
    "cache writes": "cachewrite",
    "cache write tokens": "cachewrite",
    "output": "output",
    "output tokens": "output",
}

# Tier spellings that all mean "the ordinary tier".
_DEFAULT_TIER_ALIASES = {"", "standard", "auto", "default"}

_TIER_SUFFIX = "-tier"

# "<model>, <dimension>" — model names contain hyphens and dots but no comma,
# so split on the LAST comma.
_LINE_ITEM_RE = re.compile(r"^(?P<model>.*),\s*(?P<dim>[^,]+)$")


def normalize_tier(raw: str | None) -> str:
    """Fold both endpoints' tier spellings onto one token.

    `/usage/completions` says `flex-tier`; `/costs` says `flex`. Both must land
    on `flex` or the rate lookup misses and real traffic bills as $0.00.
    """
    tier = (raw or "").strip().lower()
    if tier.endswith(_TIER_SUFFIX):
        tier = tier[: -len(_TIER_SUFFIX)].strip()
    if tier in _DEFAULT_TIER_ALIASES:
        return "default"
    return tier


def parse_line_item(line_item: str | None) -> tuple[str, str, str] | None:
    """Parse a `/costs` line item into `(model, tier, dimension)`.

    Handles both shapes OpenAI emits:
        "gpt-5.4-2026-03-05, cached input"          -> default tier
        "flex | gpt-5.4-2026-03-05, cached input"   -> flex tier

    Returns None for anything not recognisable as a token line item (fixed
    per-request charges, web-search calls, unknown dimensions) so those dollars
    stay out of the derived rates instead of inflating them.
    """
    text = (line_item or "").strip()
    if not text:
        return None

    head, sep, tail = text.partition(" | ")
    tier_raw, body = (head, tail) if sep else ("default", text)

    match = _LINE_ITEM_RE.match(body.strip())
    if not match:
        return None

    model = match.group("model").strip()
    dimension = LINE_ITEM_DIMENSIONS.get(match.group("dim").strip().lower())
    if not model or not dimension:
        return None
    return model, normalize_tier(tier_raw), dimension


def token_fields(result: dict) -> dict[str, int]:
    """Extract the four token dimensions from one `/usage/completions` result."""
    return {
        dimension: int(result.get(field) or 0)
        for field, dimension in USAGE_FIELDS.items()
    }


class RateTable:
    """Empirical $/token per `(day, model, tier, dimension)`.

    Built from org-wide totals on both sides, then applied to one key's tokens.
    Days too thin to price on their own fall back to the window-wide rate for
    the same `(model, tier, dimension)`; a `(model, tier, dimension)` with no
    rate on either basis is recorded as **unpriced** rather than silently
    costed at zero.
    """

    def __init__(self) -> None:
        self._cost: dict[tuple, float] = defaultdict(float)
        self._tokens: dict[tuple, int] = defaultdict(int)
        self._window_cost: dict[tuple, float] = defaultdict(float)
        self._window_tokens: dict[tuple, int] = defaultdict(int)
        self.fallbacks: set[tuple] = set()
        self.unpriced: set[tuple] = set()

    def add_cost(self, day: str, model: str, tier: str, dimension: str, usd: float) -> None:
        self._cost[(day, model, tier, dimension)] += usd
        self._window_cost[(model, tier, dimension)] += usd

    def add_tokens(self, day: str, model: str, tier: str, dimension: str, tokens: int) -> None:
        self._tokens[(day, model, tier, dimension)] += tokens
        self._window_tokens[(model, tier, dimension)] += tokens

    def rate(self, day: str, model: str, tier: str, dimension: str) -> float:
        """$ per token, preferring the day's own rate over the window's."""
        daily_tokens = self._tokens.get((day, model, tier, dimension), 0)
        if daily_tokens > 0:
            daily_cost = self._cost.get((day, model, tier, dimension), 0.0)
            if daily_cost:
                return daily_cost / daily_tokens

        window_tokens = self._window_tokens.get((model, tier, dimension), 0)
        window_cost = self._window_cost.get((model, tier, dimension), 0.0)
        if window_tokens > 0 and window_cost:
            self.fallbacks.add((day, model, tier, dimension))
            return window_cost / window_tokens

        self.unpriced.add((model, tier, dimension))
        return 0.0

    def cost_of(self, day: str, model: str, tier: str, fields: dict[str, int]) -> float:
        """Price one usage row, one dimension at a time.

        Only dimensions with a non-zero token count consult the rate table — a
        model that legitimately never writes cache should not be reported as
        having an unpriced cache-write dimension.
        """
        total = 0.0
        for dimension, tokens in fields.items():
            if tokens:
                total += self.rate(day, model, tier, dimension) * tokens
        return total


def is_claude_key(item: dict, target: str = "claude") -> bool:
    """True if an `/api_keys` row is one of the prompt-testing `Claude` keys.

    Matches the key's own name OR its owning service-account / user name, both
    case-insensitively. Names are matched rather than ids because the ids
    change whenever a project is added and a 27th key is minted — a hardcoded
    list would silently under-report from that day on.
    """
    wanted = target.strip().lower()
    if (item.get("name") or "").strip().lower() == wanted:
        return True
    owner = item.get("owner") or {}
    for slot in ("service_account", "user"):
        holder = owner.get(slot) or {}
        if (holder.get("name") or "").strip().lower() == wanted:
            return True
    return False
