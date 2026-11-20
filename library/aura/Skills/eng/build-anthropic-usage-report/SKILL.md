---
name: build-anthropic-usage-report
description: >
  Build one self-contained HTML report on Claude seat usage from
  `aura-main.anthropic_usage` and publish it as an Artifact — seat fit (per-user API-equivalent
  value against seat price), adoption over time, model mix, and usage concentration. Use when
  someone says "build the Claude usage report", "chart our Claude usage", "seat usage report",
  "who should be on a premium seat", "are our Claude seats worth it", "Claude usage dashboard",
  "visualise Claude usage", or asks for a shareable view of per-person Claude usage. Reads
  BigQuery via the bundled `bq.py`; the loader is `eng:harvest-anthropic-usage` and the terminal
  readout is `eng:report-anthropic-usage`. Output names individuals — internal only. Do NOT use
  for Aurie product cost (`eng:build-aurie-cost-report`) or OpenAI API spend
  (`eng:report-openai-costs`).
metadata:
  version: "1.10.0"
knowledge:
  reads:
    - Guide/Index      # baseline routing aid (universal)
    - Tools/BigQuery   # dataset conventions, GCP_KEY auth, bq.py
---

# Build the Claude usage report

Turns `aura-main.anthropic_usage` into one HTML artifact. Three scripts:
`collect.py` queries BigQuery and writes JSON, `collect_git.py` reads skill
authorship straight from an AINative checkout, and `render.py` turns both into a
self-contained page. You publish the page with the Artifact tool.

## Why one report rather than a chart-skill family

The `chart-aurie-*` family is fifteen skills because it serves a large product
with many separate questions. This dataset serves a fifteen-person org and
essentially one question: **is each seat the right tier?** Four separate skills
would be more machinery than the problem deserves. One report is one thing to
maintain and one link to share.

## Step 0 — Prerequisites

Needs `GCP_KEY` and `google-cloud-bigquery` (API-first; there is no BigQuery
MCP). `render.py` is stdlib-only.

Harvest first if the data is behind — the report reads whatever
`eng:harvest-anthropic-usage` has loaded and will say so if that is incomplete:

```bash
PY=/Library/Frameworks/Python.framework/Versions/3.12/bin/python3
$PY ${CLAUDE_PLUGIN_ROOT}/skills/harvest-anthropic-usage/scripts/harvest.py --check
```

## Step 1 — Collect

```bash
PY=/Library/Frameworks/Python.framework/Versions/3.12/bin/python3
S=${CLAUDE_PLUGIN_ROOT}/skills/build-anthropic-usage-report/scripts
D=$(mktemp -d)

$PY $S/collect.py --out $D/usage.json                      # full history
$PY $S/collect.py --since 2026-08-01 --out $D/usage.json   # a window
$PY $S/collect.py --grain week --out $D/usage.json         # weekly buckets
```

### Step 1b — Collect skill authorship (optional)

Who created and updated skills in AINative, by week, read from git rather than a
warehouse. Git is already the authoritative record of who changed what and when,
it is always available, and it needs no sync — so **do not "improve" this by
adding a store**.

```bash
$PY $S/collect_git.py --out $D/git.json                     # in-tree: repo auto-resolved
$PY $S/collect_git.py --repo /path/to/AINative --out $D/git.json
```

It refuses a checkout whose `origin` is not `AuraHealth/AINative` — two diverged
clones exist on some machines and reporting on the wrong one produces a confident
wrong answer with no error. A `--repo` you *named* is authoritative: if it does
not resolve the script raises rather than quietly falling back to its own
checkout.

**Exit `3` means no usable checkout, and it still writes `--out`** — a payload
recording why. The section is optional, so a git miss must never cost the whole
report: `render.py` draws the card as unavailable and renders everything else.
Passing `--git` at a path that does not exist degrades the same way. So the
command below is safe to run unconditionally.

**Two measurement traps it is built around**, both verified against real history
and both of which fail by producing a plausible number:

- **A mechanical sweep is not authoring.** One commit trimming every SKILL.md
  description touched 149 skills. Counting raw skills-touched lets a repo-wide
  find-and-replace outrank a month of real work, so a commit touching
  `SWEEP_THRESHOLD` (10) or more skills is classed a sweep, excluded from the bar
  and **reported in its own column** — tagged, never dropped.
- **One person is two git identities, silently.** Jerome commits under two
  emails; Rohan under two names on one email. Email alone cannot merge the first,
  name alone cannot merge the second, so identity goes through an explicit
  `IDENTITY_ALIASES` map and anything unmapped is **reported** rather than folded
  into someone else. A split person understates both halves and nothing errors.

- **One person can hold several seats.** Usage is keyed by seat email, so a person
  with four seats read as four light users. `SEAT_PEOPLE` in `collect.py` maps each
  extra seat to its person, and every per-person view (seat fit, usage and overage by
  person, concentration, active users) groups by it. Seat cost stays per seat, since
  each seat is billed: the person's seat price is the sum, and the tier tile counts seats.

When `unmapped_identities` is non-empty the script warns on stderr and the report
draws a flag. Add the person to `IDENTITY_ALIASES` and to the **Team page in AI
Knowledge**, which is the human-facing record. The map is deliberately not read
from Notion at run time: that would mean bundling the Notion helper into this
skill and making a git-only section depend on a token it otherwise never needs.

`--since` defaults to the first data day (2026-03-02) and clamps if you go
earlier. `--through` defaults to the **newest harvested day**, not to yesterday:
the export settles a day late, so ending at yesterday would stamp every report
with a permanent INCOMPLETE banner for a day that was never missing. Real
staleness is reported separately as `lag_days`.

Exit codes: `0` fine, `1` query failed, `3` BigQuery unavailable.

## Step 2 — Render

```bash
$PY $S/render.py --data $D/usage.json --out $D/claude-usage.html
$PY $S/render.py --data $D/usage.json --git $D/git.json --out $D/claude-usage.html
```

`--git` is optional. Omit it and the authorship card is left out entirely rather
than rendered empty.

### Two grains in one page

**Usage by person** and **Skill authorship** each carry a Monthly/Weekly toggle,
resting on monthly. Neither grain subsumes the other — a month says who carried
the quarter, a week says who is carrying it now, and a monthly bar hides someone
who only appeared three weeks ago — so the page ships both and the reader picks.
Both datasets are embedded, because a toggle that refetched would be dead in a
self-contained artifact.

**Both grains share one ranking, and that is load-bearing.** Colour follows the
entity, never its rank. If each grain ranked independently, a person near the
eight-slot fold could hold a named hue in one view and sit in the neutral
"Other" band in the other, so flipping the toggle would appear to recolour them.
`user_mix` reads BigQuery once at day grain and buckets both ways in Python —
one scan instead of two, and divergence becomes impossible rather than merely
unlikely. `collect_git.build_both` walks the commits **once** and ranks **once**, then
buckets that one walk twice, for the same reason.

Sharing the *ranking* is what matters, and reordering one grain's output to
match the other's is not enough — by then each has already decided its own fold.
Counts de-duplicate per bucket (one skill touched in four separate weeks is 4
weekly and 1 monthly), so two grains really can rank people differently, and a
person either side of the eighth slot would then be named in one view and inside
"Other" in the next: their row reads zero in one grain and their work is lost
from it.

**De-duplication is per person, never across the fold.** One person touching the
same skill twice in a week is one touch; two people touching it are two. Counting
the folded people as a single merged set would undercount the "Other" band alone,
so its bar would stop meaning what the named bars beside it mean — and that is the
one row nobody can eyeball against a name.

The report-wide `--grain` still governs **adoption** and **model mix**.

## Step 3 — Publish

Publish `$D/claude-usage.html` with the **Artifact tool** — `file_path` is the
rendered HTML, `favicon` 📊 on a first publish only. To update an existing
report in place rather than mint a new URL, pass that artifact's `url`.

**Artifacts are private by default. Leave it that way** unless the person asks
otherwise — see the privacy note below.

## What the sections answer

| Section | Question | Notes |
|---|---|---|
| **Seat fit** | Is each person on the right tier? | Bar = API-equivalent value/month, log scale; dashed marker = seat price |
| **Adoption** | Is this growing, and how? | Tokens and active people as **two plots** |
| **Model mix** | Where is the cost going? | Stacked value by model; the one real lever |
| **Usage by person** | Who is carrying each month, or right now? | Stacked value by user, **monthly or weekly** — 7 named + a neutral "Other" fold |
| **Concentration** | How dependent are we on a few people? | Cumulative share against an even-spread reference |
| **Skill authorship** | Who is building the skills? | Stacked skills-touched by person, **monthly or weekly**, from git; sweeps excluded and listed separately |

## Charting rules that are binding here

These come from the `dataviz` skill and are not stylistic preferences:

- **No dual-axis chart.** Tokens and active people are different scales, so
  adoption is two stacked plots. A shared y-axis would misstate both. This is
  the single most common charting mistake and the reason that section looks the
  way it does.
- **Categorical hues in fixed slot order, never cycled**, and **the cap depends
  on the chart's form**. A stacked bar reads on the *adjacent* pairlist, where
  the full eight-slot order validates in both modes, so **usage-by-person names
  seven and folds the rest**. Model mix keeps a four-slot cap. Conflating the
  two budgets either wastes six usable slots or ships a palette that fails.
- **"Other" takes a reserved neutral, never a categorical slot.** It is not an
  entity, and the fold's size is stated — an unnamed band hiding an unknown
  number of people is only honest with its count beside it.
- **The palette is validated by script, not by eye.** Both modes pass the
  skill's validator — worst adjacent CVD ΔE 9.1 light / 8.4 dark against an ≥8
  target. Two light-mode hues sit under 3:1 contrast, so the **relief rule**
  applies: every series is directly labelled and every chart has a table view.
- **Dark mode is selected**, stepped for the dark surface, not an automatic flip.
- **A bar's label names what the bar encodes.** Seat fit labels the value, then
  the ratio — labelling the ratio alone put a number on the bar that did not
  describe its length, since a standard seat can out-rank a premium one on ratio
  while drawing a far shorter bar.

If you change a chart, re-run the validator rather than reasoning about it:

```bash
# four-slot (model mix)
node <dataviz-skill>/scripts/validate_palette.js "#2a78d6,#eb6834,#1baf7a,#eda100" --mode light
# eight-slot (usage by person) — adjacent pairlist
node <dataviz-skill>/scripts/validate_palette.js \
  "#2a78d6,#eb6834,#1baf7a,#eda100,#e87ba4,#008300,#4a3aa7,#e34948" --mode light
```

## The two dollar figures

Every section prints both, and the report says this in its own header because
conflating them produces confident nonsense:

- **API-equivalent value** — the same usage priced at published API rates.
  Derived, not an invoice. Use it only to compare against a seat price.
- **Billed overage** — real money charged beyond the allowance, and `\$0` for
  anyone inside their limit.

Ranking people by billed dollars ranks who overflowed a personal cap, not who
uses Claude. On this plan most heavy users bill nothing at all.

## Banners the report raises on its own

| Banner | Means |
|---|---|
| **Incomplete** | Days in the window were never harvested; figures are a lower bound |
| **Stale** | Newest harvested day is more than two days behind settled data |
| **Rates are N days old** | `model_pricing.verified_on` is over 120 days back |
| **Unpriced models** | A model carrying tokens has no `model_pricing` row, so it is valued at zero — add it to `harvest-anthropic-usage/scripts/seed_pricing.py` and reseed |
| **Seat tiers from one snapshot** | Long window, single tier reading — anyone who changed tier is misattributed |

## This output names people

Seat fit, usage-by-person and concentration are all keyed by work email and rank
colleagues by usage. That is the point — it is a seat-allocation tool — but it
makes the artifact **internal**. Do not widen sharing beyond the people making
the seat decision without asking first.

**Adoption** and **model mix** are the only sections carrying no individual
attribution. Anything else, including a screenshot of it, names people — so
check which section you are sharing rather than assuming a crop is safe.

## Scope limits to repeat when sharing

- **Claude Code dominates** — roughly 95% of all tokens. The rest is Cowork,
  Chat and Claude Tag.
- **API-key usage is absent.** Claude Code authenticated with an API key reports
  to the Console organization, not this dataset.
- **Prices are hand-entered and dated.** Nothing can refresh them; a stale rate
  yields a plausible wrong number rather than an error.
