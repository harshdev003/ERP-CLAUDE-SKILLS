#!/usr/bin/env python3
"""Resolve the OpenAI `service_tier` for a QA/eval call. Flex by default.

Why this exists
---------------
QA and eval inference is batch-shaped: nobody is waiting on a sweep, a judge
pass or a self-critique. Standard processing bills 2x what flex does for the
same tokens, and an OMITTED `service_tier` bills Standard — so the expensive
choice is the one you get by writing nothing. `eng:report-prompt-testing-cost`
measured ~$837 in a single day from `eng:qa-conversation` alone before the tier
was made explicit.

So every spending call in these skills routes its tier through here, and the
default is `flex`.

The values are OpenAI's, not ours
---------------------------------
Verified against OpenAI's own docs 2026-09-04:

  * flex     — "service_tier": "flex". Priced at Batch API rates (half
               Standard). Beta, limited model availability. Under capacity
               pressure it returns **429 Resource Unavailable**, and OpenAI
               does not charge for that response. Their remediation is
               exponential backoff or a retry with `service_tier: "auto"`.
  * standard — "default". The 1x baseline. `auto` is the neighbouring value
               OpenAI names for the flex fallback path.
  * fast     — "fast", and "priority" is still accepted for the same thing
               (the tier was renamed; both strings work). ~2x Standard.

`FLEX_FALLBACK_TIER` is `auto` rather than `default` deliberately: it is the
string OpenAI's own flex guide names for the retry, and it lets OpenAI place
the request rather than pinning it.

Not every model is flex-eligible. The gpt-4.1 and gpt-4o families bill Standard
regardless of what is requested, so sending `flex` on those is a no-op, not an
error — which is why this module never tries to gate on the model.

Canonical tier table (values, multipliers, model eligibility, the 429
behaviour): AI Knowledge `Tools/OpenAI` -> Per-tier list prices. Do not restate
the multipliers in a skill body; point at that page.

Latency measurement is exempt
-----------------------------
A run whose *subject* is latency must use the tier production uses — the tier is
the variable under test, so quietly moving it to flex measures the wrong thing.
Such a caller passes its production tier explicitly and reports it; it does not
inherit the default. `require_explicit()` is the entry point for that case.

Copies
------
Byte-identical copies live in every skill that spends on OpenAI inference
(self-contained per Architecture principle 1). Canonical copy:
`plugins/eng/skills/qa-prompt/scripts/service_tier.py`. Edit the canonical
first, then propagate in the same PR.

Stdlib only.
"""
from __future__ import annotations

import os
import sys

# Choice name -> the string sent as `service_tier` on the API call.
TIERS: dict[str, str] = {
    "flex": "flex",
    "standard": "default",
    "fast": "fast",
}

# Relative token cost vs standard. Display only — the authoritative per-model
# rates live on `Tools/OpenAI`.
COST_FACTOR: dict[str, float] = {
    "flex": 0.5,
    "standard": 1.0,
    "fast": 2.0,
}

DEFAULT_TIER = "flex"

# What a flex call degrades to when OpenAI has no flex capacity. OpenAI's flex
# guide names `auto` for this retry.
FLEX_FALLBACK_TIER = "auto"

# Process-wide override, for callers that take positional args only and for
# unattended sweeps.
ENV_VAR = "AINATIVE_OPENAI_SERVICE_TIER"

# What to do when the requested tier cannot be honoured — i.e. the flag or the
# env var names something that is not a tier.
#
#   stop     — refuse the run and say what was wrong (default)
#   standard — carry on at the 1x tier, loudly
#
# The default is `stop` because this module exists to stop paying standard
# rates by accident, and a fallback that is on by default reintroduces exactly
# that: a one-character typo would silently double the bill of every run, which
# is indistinguishable from a correct run. Stopping costs one word to fix.
#
# `standard` is nonetheless a real need, not a hypothetical: an unattended sweep
# would rather finish at 1x than abort a night's work over a typo. That is a
# decision about the run, so the run states it.
FALLBACK_ENV = "AINATIVE_OPENAI_TIER_FALLBACK"
UNAVAILABLE_POLICIES = ("stop", "standard")
DEFAULT_UNAVAILABLE_POLICY = "stop"

CHOICES = tuple(TIERS)


class ServiceTierError(ValueError):
    """An unrecognised tier name."""


def _normalize(name: str) -> str:
    key = (name or "").strip().lower()
    # Accept the wire values too, so a caller that already holds `default` or
    # the pre-rename `priority` does not have to translate.
    aliases = {"default": "standard", "auto": "standard", "priority": "fast"}
    key = aliases.get(key, key)
    if key not in TIERS:
        raise ServiceTierError(
            f"unknown service tier {name!r}; expected one of {', '.join(CHOICES)}"
        )
    return key


def unavailable_policy(explicit: str | None = None) -> str:
    """What to do when the requested tier is not a tier: flag, env, else stop."""
    raw = (explicit or os.environ.get(FALLBACK_ENV) or DEFAULT_UNAVAILABLE_POLICY)
    key = raw.strip().lower()
    # "normal" is what people call the standard tier out loud.
    key = {"normal": "standard", "default": "standard"}.get(key, key)
    if key not in UNAVAILABLE_POLICIES:
        raise ServiceTierError(
            f"unknown tier-fallback policy {raw!r}; expected one of "
            f"{', '.join(UNAVAILABLE_POLICIES)}"
        )
    return key


def resolve(
    explicit: str | None = None,
    default: str = DEFAULT_TIER,
    policy: str | None = None,
) -> str:
    """Choice name to use: explicit flag, else env override, else the default.

    An unrecognised value is a configuration error, and what happens next is the
    run's decision — see `unavailable_policy`. Under `standard` the fallback is
    announced on stderr: continuing at 1x is a doubling of the bill, so it is
    never allowed to be the quiet outcome.
    """
    chosen = explicit or os.environ.get(ENV_VAR) or default
    try:
        return _normalize(chosen)
    except ServiceTierError as err:
        if unavailable_policy(policy) != "standard":
            raise
        print(
            f"warning: {err}. Continuing on the standard tier per "
            f"{FALLBACK_ENV}=standard — that is {COST_FACTOR['standard'] / COST_FACTOR[DEFAULT_TIER]:g}x "
            f"the cost of {DEFAULT_TIER}. Fix the tier name to get the cheaper rate.",
            file=sys.stderr,
        )
        return "standard"


def wire_value(
    explicit: str | None = None,
    default: str = DEFAULT_TIER,
    policy: str | None = None,
) -> str:
    """The string to put in the request body's `service_tier` field."""
    return TIERS[resolve(explicit, default, policy)]


def require_explicit(explicit: str | None, purpose: str) -> str:
    """Wire value for a caller that must NOT inherit the flex default.

    Latency measurement is the case: the tier is the variable under test, so an
    unstated one is a silent methodology error rather than a saving. Raises
    unless the caller (flag or env) said which tier it wants.
    """
    # The fallback policy deliberately does NOT apply here. It covers a tier
    # that could not be understood; this is a tier that was never stated, and
    # defaulting it is the exact silence the exemption exists to prevent.
    chosen = explicit or os.environ.get(ENV_VAR)
    if not chosen:
        raise ServiceTierError(
            f"{purpose} must state its service tier explicitly — it is measuring "
            f"latency, so the tier is the variable under test and the flex default "
            f"would measure the wrong thing. Pass one of {', '.join(CHOICES)} "
            f"(use the tier production runs for this prompter), or set {ENV_VAR}."
        )
    return TIERS[_normalize(chosen)]


def add_argument(parser, *, required: bool = False) -> None:
    """Attach a `--service-tier` flag with a consistent help string.

    The flag defaults to **None**, not to `DEFAULT_TIER`. Baking the default in
    here makes an unset flag indistinguishable from an explicit `--service-tier
    flex`, so it is passed on as an explicit choice and silently outranks
    `AINATIVE_OPENAI_SERVICE_TIER` — the documented unattended override then
    does nothing, with no error to say so. Let `resolve()` apply the default,
    which is the only place that sees the whole precedence chain.
    """
    parser.add_argument(
        "--service-tier",
        choices=CHOICES,
        default=None,
        required=required,
        help=(
            "OpenAI service tier for this run. "
            f"Default {DEFAULT_TIER}. "
            "flex = half the cost of standard, slower, the default for QA/eval work; "
            "standard = 1x; fast = 2x, only when the run is about speed. "
            f"Unset, it falls back to {ENV_VAR}, then to {DEFAULT_TIER}. "
            "Rates and model eligibility: AI Knowledge Tools/OpenAI."
        ),
    )
    parser.add_argument(
        "--on-tier-unavailable",
        choices=UNAVAILABLE_POLICIES,
        default=None,
        help=(
            f"What to do when the requested tier is not a recognised tier. "
            f"stop (default) refuses the run; standard continues at 1x with a "
            f"warning, for unattended runs that would rather finish than abort "
            f"on a typo. Also settable with {FALLBACK_ENV}."
        ),
    )


def _error_text(err: Exception) -> str:
    """Everything the error can tell us, lowercased.

    `str()` is not enough. `urllib.error.HTTPError` stringifies to the status
    line alone — "HTTP Error 429: Too Many Requests" — while the code that says
    *why* (`resource_unavailable`) is only in the response body. Matching on the
    string alone therefore never fires for the urllib callers, which is every
    script in `eng:qa-conversation`.

    HTTPError is file-like, so the body is read from it directly. The read is
    one-shot and defensive: a consumed or unreadable body degrades to the string
    form rather than raising inside an error handler.
    """
    parts = [str(err)]
    for attr in ("read", "body", "message"):
        value = getattr(err, attr, None)
        if value is None:
            continue
        try:
            got = value() if callable(value) else value
        except Exception:
            continue
        if isinstance(got, (bytes, bytearray)):
            got = got.decode("utf-8", "replace")
        if got:
            parts.append(str(got))
    return " ".join(parts).lower()


def flex_retry_reason(tier: str, err: Exception) -> str | None:
    """Why a flex call should be retried on the fallback tier, or None.

    Two distinct causes, both of which leave the call unable to proceed on flex
    while standard would work:

      * `no flex capacity` — the documented 429 `resource_unavailable`. OpenAI
        does not bill it, and its own guide says to back off or retry on `auto`.
      * `this model does not offer flex` — flex is beta with limited model
        availability, so a non-eligible model 400s on the parameter.

    Narrow on purpose: only a flex request, and only these two signals. An
    ordinary rate-limit or quota 429 must keep failing — retrying that on
    standard pays 2x to hit the same wall.

    Reads the status off either transport: the OpenAI SDK puts it on
    `status_code`, `urllib.error.HTTPError` on `code` — and the reason off
    `_error_text`, because the two transports put it in different places.
    """
    if tier != TIERS["flex"]:
        return None
    status = getattr(err, "status_code", None) or getattr(err, "code", None)
    text = _error_text(err)
    if status == 429 and "resource_unavailable" in text:
        return "no flex capacity for this call"
    if status == 400 and "service_tier" in text:
        return "this model does not offer the flex tier"
    return None


def flex_refusal_hint(tier: str, err: Exception) -> str | None:
    """A remediation line when a call failed because flex was unavailable.

    For callers driving raw `urllib` with their own retry loop, where swapping
    the tier mid-loop would mean rebuilding a request they serialized once.
    Those callers surface this instead of retrying, so a refused flex call reads
    as a refused flex call rather than as a model that would not answer — the
    two are indistinguishable in an error string, and one of them is a
    one-word fix.

    The SDK-driven callers hold their kwargs, so they retry on
    `FLEX_FALLBACK_TIER` automatically instead.
    """
    why = flex_retry_reason(tier, err)
    if why is None:
        return None
    return (
        f"{why} — this call was refused on the flex tier, not by the model. "
        f"Re-run with {ENV_VAR}=standard (1x cost) to get past it."
    )


def warn_if_flex_refused(tier: str, err: Exception, stream=None) -> None:
    """Print `flex_refusal_hint` to stderr when it applies."""
    hint = flex_refusal_hint(tier, err)
    if hint:
        import sys as _sys
        print(f"warning: {hint}", file=stream or _sys.stderr)


def describe(choice: str) -> str:
    """One line naming the tier and its cost, for a run header."""
    key = _normalize(choice)
    return f"service tier: {key} ({TIERS[key]}, {COST_FACTOR[key]:g}x standard cost)"
