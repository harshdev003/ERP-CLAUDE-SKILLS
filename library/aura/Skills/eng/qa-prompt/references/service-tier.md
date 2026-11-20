# Service tier

How this skill picks the OpenAI `service_tier` for every call that spends, and
what happens when the tier it asked for cannot be used.

Rates, model eligibility and the capacity behaviour are canonical on AI
Knowledge `Tools/OpenAI`. The multipliers below exist to make the question
askable, not to be a second copy of the price list.

## Ask once, offer flex first

QA inference has nobody waiting on it, and an omitted `service_tier` bills at
standard, so the expensive choice is the one you get by saying nothing. Put the
question to the user once per run, with the cost attached:

| Choice | Cost | Pick it when |
|---|---|---|
| **flex** (default) | 0.5x | Any ordinary QA run — sweeps, comparisons, scoring. Slower, and that does not matter here. |
| standard | 1x | Flex is too slow for an interactive drive you are watching live, or the model is not flex-eligible. |
| fast | 2x | Only when the run is about speed. |

Pass the answer as `--service-tier {flex,standard,fast}` to `setup_run.py` /
`setup_sweep.py`. It lands on the state, and every `send_turn.py` and
`self_critique.py` call inherits it.

## Precedence, and unattended runs

Flag, then `AINATIVE_OPENAI_SERVICE_TIER`, then flex. Setting the env var is how
an unattended run skips the question.

The flag deliberately defaults to `None` rather than to `flex`: an unset flag
that looks like an explicit choice outranks the env var, and the documented
override then does nothing with no error to say so.

The setup scripts record the **resolved** tier on the state, and validate it
before fetching the prompt, so a typo fails on the typo rather than after a
network round-trip.

## A `service_tier` in the prompt's own params is ignored

The call kwargs spread the Braintrust prompt's params, so one carried there
would land after ours and win — while the turn record still reported the tier we
asked for, meaning a report could claim flex over a request that billed
standard. It is dropped, with a warning: the run's tier is the run's decision,
and dropping it quietly would be the same class of defect.

## When the tier cannot be used

**A name that isn't a tier stops the run**, by default. Pass
`--on-tier-unavailable standard`, or set `AINATIVE_OPENAI_TIER_FALLBACK`, to
continue at 1x with a warning instead — worth it for an unattended sweep that
would rather finish than lose a night's work to a typo.

It is not the default because a one-character mistake would otherwise double the
bill of every run with nothing to show it, which is the failure this whole
mechanism exists to remove. The fallback is always announced.

**A flex call OpenAI refuses** — 429 `resource_unavailable` when there is no
flex capacity, which it does not bill, or a 400 when the model is not
flex-eligible — is retried once on `auto`, with a warning on stderr. Never
silently: a quiet retry doubles the bill with nothing in the output to show for
it. An ordinary rate-limit 429 is not treated this way, since retrying that on
standard would pay 2x to hit the same wall.

## Where the tier is recorded

`setup_run.py` and `setup_sweep.py` write the resolved choice to
`state.service_tier`. `send_turn.py` records both what it asked for
(`turn.service_tier`) and what OpenAI granted (`turn.service_tier_granted`).

The two can differ: a flex request may be served on another tier under capacity
pressure, so anything reporting on cost must read the granted value rather than
assume the request was honoured.
