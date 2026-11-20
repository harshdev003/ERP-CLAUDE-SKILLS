# Reading Code Review Metrics out of the Review Loop artifact

Companion to [`../prepare-monday-deck.md`](../prepare-monday-deck.md). This file owns one
step: getting the five figures and two series the Code Review Metrics slide needs out of the
[Review Loop artifact](https://claude.ai/code/artifact/4e997ead-1408-44ba-81de-e0c88631eaf2),
without recomputing anything.

The artifact is built by the `build-pr-metrics-dashboard` skill, which lives in
[AuraHealth/OKRs](https://github.com/AuraHealth/OKRs) — not in this repo. That skill owns the data, the metric definitions
and the publish. This file is a **reader** — it never redefines a metric, and it must not
reimplement the aggregation.

## Why the numbers are read from the page, not recomputed

Every figure on the page is computed in the browser from an embedded fact table, under rules
that are easy to get subtly wrong: draft PRs excluded, every clock starting at
ready-for-review, weeks bucketed by the ready moment, self-reviews dropped, and PRs still
waiting folded in at their elapsed-so-far once past 24 hours (which is what puts a `≥` on a
figure). A Python or by-hand re-derivation would agree for a while and then quietly drift,
and the slide would contradict the artifact people can open for themselves.

So the deck calls the page's own functions and reports what they return. The definitions live
in the skill's [`references/metric-definitions.md`](https://github.com/AuraHealth/OKRs/blob/main/.claude/skills/build-pr-metrics-dashboard/references/metric-definitions.md);
read that before answering any question about what a number means.

## 1. Refresh first

Run **steps 1–5** of `build-pr-metrics-dashboard` (resolve mode → `fetch_repos.py` →
`fetch_prs.py` → `compute_metrics.py` → `build_dashboard.py`). That rebuilds
`dashboard.html` locally.

That skill is in [AuraHealth/OKRs](https://github.com/AuraHealth/OKRs), not in this plugin, so it cannot be invoked with
the Skill tool from an AINative session. **Resolve the checkout — do not assume a path.**

```bash
# In order: an explicit override, then a sibling of this checkout, then give up and say so.
OKRS="${METRICS_STORE_REPO:-}"
[ -n "$OKRS" ] || OKRS=$(cd "$(git rev-parse --show-toplevel)/../OKRs" 2>/dev/null && pwd)
[ -n "$OKRS" ] || { echo "No OKRs checkout found. Clone AuraHealth/OKRs beside this repo,"
                    echo "or set METRICS_STORE_REPO to an existing one."; }
echo "$OKRS"
```

The sibling guess works because AuraHealth repos are cloned side by side; it is a guess, not
an assumption, and it fails loudly rather than inventing a path.

The skills live in `$OKRS/.claude/skills/`, so a session with that repo as its **working
directory** picks them up as project skills and can invoke `build-pr-metrics-dashboard` by
bare name. Moving there with `change_directory` is the reliable way to get that: a shell
`cd` changes one command's directory, not the session's project settings, so the bare name
still will not resolve. From anywhere else, read
`$OKRS/.claude/skills/build-pr-metrics-dashboard/SKILL.md` and follow it.

Two things to carry from that skill:

- **Do not pass `--data-dir`.** Every script defaults to `$OKRS/data/pr/`, resolved from the
  script's own location, and `build_dashboard.py` writes the page to `$OKRS/build/pr/`.
  There is no cache to locate and no directory to choose — passing a path is how this went
  wrong before. Read `weeks` from `$OKRS/data/pr/state.json` and pass that same `--weeks`
  to both `fetch_prs.py` and `compute_metrics.py`.
- **If `fetch_prs.py` prints a `⚠️` naming failed repos, stop.** The watermark is held
  deliberately and the build is incomplete; fix and re-run rather than putting a partial
  fetch on a slide.

The deck reads the freshly built **local** page. Publishing the artifact is that skill's own
gated step 6 and is not a prerequisite for the deck — but if the numbers are going on a slide
the room can check, publish it in the same session so the link and the slide agree.

**This step does write to GitHub**, which it did not used to. `data/pr/` in the OKRs repo is
the durable store, so a clean refresh commits and pushes the refreshed history to that
repo's `main`. Nothing is written to AINative.

## 2. Open the built page

```
preview_start(url: "file://$OKRS/build/pr/dashboard.html")
```

Use the **absolute** path from the `$OKRS` resolved in step 1, expanded. `{cwd}` is wrong
here: the page is built inside the OKRs checkout, and the deck may be running from anywhere
— against `{cwd}` this either fails to open or, worse, finds an unrelated `dashboard.html`
and puts its numbers on the slide.

Leave every control at its default — repo *all*, base *any*, still-waiting *counted after
24h*, window *weekly*, statistic *average*. The slide is the default view of the artifact;
if a control is changed, the slide has to say so in its subtitle.

## 3. Read the five headline figures

They are the artifact's own KPI tiles, already carrying their week-over-week delta:

```
javascript_tool: JSON.stringify({
  cov: document.getElementById("kpis-cov").innerText,
  eff: document.getElementById("kpis-eff").innerText
})
```

`kpis-cov` returns the three coverage tiles; `kpis-eff` returns four timing tiles, of which
the slide takes the last two. Both are computed for the **latest complete week** — the final
week in the data is partial and is excluded.

## 4. Read the two eight-week series

```
javascript_tool: (() => {
  const ga = G(A), TA = w => ga.T(w);
  const ws = WEEKS.slice(-9, -1);            // last 8 COMPLETE weeks
  const agg = p => { const r = AGGP(p, stat); return r == null ? null
    : { v: Math.round(r.v * 10) / 10, n: r.n, floor: !!r.floor }; };
  return JSON.stringify({
    latestComplete: WEEKS[WEEKS.length - 2],
    prior:          WEEKS[WEEKS.length - 3],
    partial:        WEEKS[WEEKS.length - 1],   // excluded everywhere
    weeks: ws,
    coverage: ws.map(w => { const t = TA(w); return { week: w, merged: t.merged,
      human:  +(100 * t.covHum  / t.merged).toFixed(1),
      aiOnly: +(100 * t.covAI   / t.merged).toFixed(1),
      none:   +(100 * t.covNone / t.merged).toFixed(1) }; }),
    ttfr: ws.map(w => ({ week: w, human: agg(TA(w).ttfrH), ai: agg(TA(w).ttfrAI) })),
    merged: ws.map(w => ({ week: w, merged: TA(w).merged, opened: TA(w).opened }))
  }, null, 1);
})()
```

`A`, `G`, `WEEKS` and `AGGP` are page globals and `stat` is the page's top-level statistic
setting (`"mean"` at the default view), so this is the page's own aggregation — the numbers it
returns are the numbers the page draws. Coverage is a share of **merged** PRs, the denominator
the `kpis-cov` tiles print ("18 of 213 merged"); dividing by `opened` instead produces a series
that never matches the tiles. **Cross-check** the last row of
`coverage` and `ttfr.human` against the tiles read in step 3; a mismatch means the page was
read before it finished rendering, so re-run rather than reconciling by hand.

`WEEKS.slice(-9, -1)` is deliberate on both ends. The last element is the **partial** week,
so the trailing `-1` drops it — slicing `-8` instead would put a one-day bucket on the chart
and read as a collapse, the same trap the Velocity slide guards against. And it takes **8**
complete weeks, not 5: weekly `n` is small enough that a single stale PR swings a point, and
eight points show whether a move is a trend or a bounce. Widen it further only if the axis
labels still read at projection size — see the layout spec.

## 5. Which artifact sections feed which part of the slide


| Slide element | Artifact section | Source |
|---|---|---|
| Time to merge tile | 01b Process efficiency → *Average time to merge* tile | `kpis-eff` |
| Time to merge, human-reviewed tile | 01b → *Average time to merge — human-reviewed* tile | `kpis-eff` |
| Three coverage tiles | 01a Review coverage tiles | `kpis-cov` |
| Coverage chart | 01a → *Coverage over time* | `coverage` series |
| Time to first review chart | 01b → *Time to first review* | `ttfr` series |
| Footer counts | 01a/01b tile subtexts | `opened`, `merged`, `n`, still-waiting counts |


Everything else the artifact carries — time to first approval, PR size vs approval time,
review load, repo activity, PRs reviewed / opened / changes requested, and the whole
monitoring section (open PRs, ready-but-nobody-asked, waiting over a day) — is **out of
scope for the slide** and stays in the artifact. See the scope rationale in
[`../prepare-monday-deck.md`](../prepare-monday-deck.md).

## 6. Direction and colour

The deck's convention is green = improvement, red = regression. Which direction *is* the
improvement differs per figure, and for one of them it is genuinely undefined:


| Figure | Improvement | Rendering |
|---|---|---|
| Time to merge | down | `▼` green / `▲` red |
| Time to merge — human-reviewed | down | `▼` green / `▲` red |
| Reviewed by a human | up | `▲` green / `▼` red |
| No review at all | down | `▼` green / `▲` red |
| **Reviewed by AI only** | **neither** | glyph only, in black |


AI-only coverage has no fixed direction: a rise can be PRs moving up out of *no review*
(better) or down out of *human review* (worse), and the tile alone cannot say which. Colour
it black and read it against the other two rather than asserting a direction the metric does
not carry. The glyph still shows movement, so direction is never colour-only anywhere on the
slide.

## 7. Two things that must survive onto the slide

- **The `≥` prefix.** A figure the artifact writes as `≥24.1h` rests on PRs that are still
  waiting, so it is a floor — the true value is at least that. Carry the `≥` verbatim and put
  the still-waiting count in the footer. Silently dropping it turns a floor into a claim.
- **The cohort rule.** A PR counts in the week it became *ready*, carrying however long its
  review took, so a weekly figure can legitimately exceed seven days. The slide answers
  "how fast were the PRs that entered review that week served", not "how fast did reviewers
  work that week".

## 8. What these figures are comparable to

Every figure here is a **cohort** figure on **Monday-start UTC** weeks, over **every active
repo in the org**, with drafts excluded and every clock started at ready-for-review. That
basis is what makes one week comparable to the next, and it is the thing to state on the
slide — the subtitle names the week and the repo scope for exactly this reason.

The rule that follows from it: **a slide built on a different basis is not a comparison.**
Before framing any move as a trend, check that the deck's previous PR figures came from this
same source. If they did not — an older deck's slide read a 30-day rolling window over a
single repo, counted drafts, and started its clocks at PR creation — then the change between
the two slides is measurement, not performance. Say so once, on that deck, rather than
letting the room infer a regression from a redefinition. Once both slides come from here,
week-over-week stands on its own and the caveat is not needed.
