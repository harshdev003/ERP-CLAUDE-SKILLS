# Note conventions

The house format for the weekly Growth ROI post. The canonical narrative lives at
`Procedures/Marketing` → *Weekly Growth ROI notes*; this file is the executable
version plus a worked example per app.

## Destination

| App | Channel | ID | cc |
|---|---|---|---|
| Aura | `#data-analysis` | `C05TMC2Q33K` | Steve `U1BCYETM2`, Daniel `U1BD3BZRN` |
| Aurie | `#ai-analytics` | `C0A3AV9KW31` | Steve `U1BCYETM2`, Daniel `U1BD3BZRN` |

Post via `shared:post-to-slack` **with `--as-user`** so the note is attributed to
the author rather than to an app — the `username`/`icon_url` bot path still shows
an "APP" badge. cc as **real mentions** (`<@U1BCYETM2>`) so they notify; a
plain-text `@steve` does nothing. Cadence is Monday, after the weekly tabs are
refreshed.

**Pass no footer flag** — no `--footer`, `--footer-skill-md` or
`--footer-version`. This is the same deliberate exception `shared:daily-status`
takes: the integration posting on the user's behalf auto-appends its own
signature, so a skill-stamped footer duplicates the tail. Every historical post
in both channels is unfooted, and adding one makes the note read as
machine-generated. Do not hand-type a footer either — the rule is *no footer*,
not *a different footer*.

## Two rules that shape every post

**1. Sheet data only.** Every figure in the post, and every "why" clause, comes
from the growth sheet itself — spend, trials, CPT, CAC, ASP, the CR columns, the
channel blocks. The work in Steps 3, 4 and 4b (decompositions, live experiments,
bugs, Viktor's ad-account and Mixpanel numbers) is for getting the reading right,
and it is reported in chat; none of it goes into the post. The author cut a line
that explained a WAL drop with a player incident and Viktor's per-platform
listener counts: "Why are you putting that there? Just use the data from the
growth sheet." When an outside explanation matters, put it in chat and let the
author add it.

One outside figure is asked for: when Aura's trials no channel claims are mostly
Aura trials from the upsell inside Aurie, the Aura post names that source with its
Mixpanel count (`sheet-contract.md` → unattributed trials).

**2. Short, and nested.** Three to five top-level bullets. The top line carries
the headline figure, its move and a plain-English why ("spend went up but paid
came down"); every supporting figure goes in a nested sub-bullet, one figure or
one pair per line, up to three sub-bullets. A headline line with the supporting
numbers packed into it comes back as "should be in more bullet points nested,
no?". Casual, plain voice. The decomposition behind a bullet stays in chat. The
model is the author's own posts, which read like this: "CPT rose to $31 again",
"CAC was at $51! Our lowest in a very long time".

## Shape

```
Quick notes about {App} Growth ROI :
• {metric} {old} -> {new}, {record or range context} - {why, in plain words}
    ◦ {the sheet figures behind the why: Aurie spend / registrations / paid; on Aura, subs or a rate, never spend or trial counts}
    ◦ {the funnel step or channel that moved}
• {a week-late metric, labelled with its week} - {why, in plain words}
    ◦ {the sheet figure that shows it}
• {an open question, kept open: "not sure why yet"}

cc : <@U1BCYETM2> <@U1BD3BZRN>
```

Conventions inside that skeleton:

- `•` for top level, `◦` for the second level. Through the Slack MCP connector,
  write them as a markdown list — `- ` for a bullet, four spaces then `- ` for a
  sub-bullet — which renders as `•` / `◦` with real nesting, like the author's own
  posts. Literal `•` / `◦` characters render flat.
- **`->` for a transition, not `→`.** Both for funnel steps
  (`reg -> trial`) and for deltas (`$53 -> $62`).
- A space before the colon (`Growth ROI :`) — house habit, keep it.
- Currency to one decimal when the sheet shows one (`$38.5`), whole dollars for
  per-channel CPT/CAC (`$62`), percentages to one decimal (`24.8%`).
- Label the week on every figure from a column that fills a week late
  (`CAC for 9/13-9/19 ...`) — the two-speed fill in `sheet-contract.md`.
- A record claim ("highest since May 2022") is checked against the full column
  before it is written.
- Plain, hedged voice: "could be normal", "not sure why yet", "back to normal
  after two high weeks".

## What earns a bullet

| Earns one | Does not |
|---|---|
| A reading outside its own trailing band | A reading inside its band, however far from target |
| A run of consecutive moves one way | A single-week wobble inside the band |
| A "why" the sheet itself shows (spend flat, trials down) | An explanation from outside the sheet — Viktor, Linear, the experiment board, Mixpanel |
| A prior-week figure that changed, in one line | A correction an earlier follow-up post already made |
| FB blended CAC against its target; an ASP move that explains a sub or revenue move | WAL / WAPU moves — both fall most weeks |
| A multi-week run in a funnel step, kept as an open question | An ASP slide on its own, with nothing it explains |
| A move big enough to change a decision | A few-percent move in a component metric |

When several volume metrics fall in a week spend was cut, attribute them to the
spend once and move on. Repeating the attribution per metric reads as padding.

Explicitly cut, learned from author edits:

- **Spend totals and streaks.** "Spend up again to $21.1k, 4th week up in a row"
  was cut — the team sets the budget, so it is an input, not a finding.
- **Channel-mix percentages as their own bullet.** Mix belongs inside a CPT line
  where it explains something, not as its own observation.
- **Component metrics that barely moved.** Paid-channel trials going 357 -> 345
  was cut — CAC is what carries channel efficiency.
- **Small absolute counts dressed as a ratio story.** Web family subscribers
  33 -> 35 was cut; two subscribers is not a move.
- **Ticket IDs the reader has no context for.** Describe the defect in a clause,
  or leave it out.
- **Causal claims that cannot be shown.** Give the sheet arithmetic instead.
- **WAL and WAPU.** "WAL dropped 3.6% to 17.2k, biggest weekly drop this year"
  was cut: "this happens most of the weeks".
- **Outside explanations.** The player-incident reading of that WAL drop, Meta
  campaign-level detail from Viktor, and experiment-board confounders were all
  cut — "Just use the data from the growth sheet."
- **Corrections already posted.** The withdrawal of a prior week's web-funnel
  drop was cut because the author's own follow-up post had already made it.
- **Length.** A five-bullet, eleven-sub-bullet draft came back as "really long,
  make them a lot shorter, check my past messages in the channel".
- **A standalone ASP slide.** "iOS ASP down 5 weeks in a row, $89.6 -> $78.2;
  Stripe ASP eased to $107.9" was cut: with no sub or revenue move it explained,
  it was a level, and the mechanism (discount and product mix) sat outside the
  sheet.
- **Supporting figures packed into the headline line.** "Appstack spend $15.2k ->
  $16.3k (highest so far) but paid 291 -> 270, CAC $52 -> $61 - registrations flat
  964 -> 963, reg -> paid 30.2% -> 28.0%" came back as "should be in more bullet
  points nested, no?". The headline carries the move and the plain-English why;
  each figure gets its own sub-bullet.
- **A data-completeness caveat folded into a CAC line.** "CAC for 10/4-10/10
  reads $87.3, but App Store subs still look incomplete for a 2nd week, so it's
  overstated" was cut back to the figure and its subs sub-bullet. A sheet
  defect the owner already has a card for gets its own short bullet (below), not
  a qualifier on the metric it touches.
- **A recovering funnel step after an open-question streak.** "Web sign up start
  -> reg up to 25.4% (from 23.3%), first rise after 6 weeks of decline" was cut.
  One week back up does not close the question, and the prior week's value had
  moved since it was posted.
- **A mechanism the cells suggest but the owner has not confirmed.** A
  column-level theory for the Aurie LLM jump was rejected: the owner knew the
  cause (the 10/7 token change) and it was on record. Check the incident record
  first (`confounders.md` §8).
- **Spend and trial-count figures in the Aura note.** "Spend $23.6k -> $20.2k,
  trials 784 -> 602", the per-channel trial counts and "FB spend $10.9k -> $9.5k,
  trials 130 -> 85" were all cut: "remove the spends on aura, same for trial
  amount". The CPT or CAC headline keeps its why in plain words ("spend went down
  but trials fell faster") with no figures behind it; subs counts and rates stay.
  This is Aura only — the Aurie note keeps its Appstack spend / registrations /
  paid figures.

Asked for or kept by the author:

- **FB blended CAC against its target** — "FB blended CAC is at $200, 2nd week
  in a row (target $90)".
- **An ASP move when it explains something** — Stripe ASP alongside web subs and
  web trial -> paid, never on its own.
- **A prior-week figure the owner corrected**, stated as the owner's correction:
  "I corrected last week's margin to 21% (I posted 33%) and updated the skill
  that calculates it, so it's right going forward". When the Step 1 diff finds a
  moved figure, ask the owner whether it was their correction before drafting
  the line.
- **A known sheet defect as its own one-line bullet, with the card noted** —
  "iOS ASP shows $0 for 9/27 and 10/4 (I made a card for it already)". Name the
  cell and the weeks, say a card exists, and give no ticket ID.
- **The columns a sheet defect breaks, nested under it** — "So Profit/user in 7D
  w/ discount is wrong for those weeks too: $5-6 instead of the usual $66-71". The
  iOS ASP cell (`R`) feeds the profit and ROI block, so a `$0` there makes
  `Profit/user in 7D w/ discount` (`W`) read near zero. Name the dependent column
  by its row-2 header with its broken and usual values.
- **A documented cause for a cost move, in the owner's words** — "Margin still
  reads -18% (from -17%) - LLM cost is still high since the 10/7 change made each
  conversation use more tokens". When the owner has named the mechanism of a cost
  step, it goes in the headline's why clause, with the sheet's cost figures
  nested beneath it.
- **The `W-1` New Subs move first on Aura.** When `New Subs` (`E`) is off-band it
  leads the note, labelled with its week, with that week's CAC nested beneath it.
- **A spend shift between channels, in words** — "FB spend went down while Google
  spend went up - FB got cheaper, Google got pricier", with each channel's CPT
  nested beneath it. On Aura the spends stay words, never figures.
- **The source of the trials no channel claims, with its count** — "Most trials no
  channel claims are Aura trials from the upsell inside Aurie (~168, from ~148)".
- **Aurie milestones, nested under the ARR line** — "Over 4k paying subscribers
  (4.2k)", "2k weekly active conversers", "1k weekly active voice conversers". A
  milestone is the first crossing of a round thousand, checked against the full
  column (`sheet-contract.md`).
- **A closing "Sheet updates" bullet for the owner's changes to the sheet** — a
  block added back, a column known to overstate, spend not yet in the sheet — one
  nested line each, with the owner's plan in a few words ("I need to fix it"). It
  carries the understated-CAC caveat instead of a separate bullet. Ask the owner
  what changed in the sheet before drafting it; the cells do not say.

## An open question stays open

Where the cause is genuinely unknown, say so in the bullet ("not sure why yet")
rather than substituting a guess. If a check in Step 4 or 4b later explains it,
**surface the answer in chat and let the author decide** — do not silently
rewrite their open question into an answer, and do not put the outside answer in
the post.

## Worked example — Aura, week of 2026-11-01

Posted 2026-11-09.

```
Quick notes about Aura Growth ROI :
• New Subs for 10/25-10/31 down to 217 (from 264), lowest in the sheet - trials fell the week before
    ◦ Web family subs halved 56 -> 28, not sure why yet
    ◦ CAC still dipped to $74.0 (from $76.5) since spend fell too
• CPT down to $23.9 (from $26.7), lowest since mid-January - trials rose a lot more than spend
    ◦ Most trials no channel claims are Aura trials from the upsell inside Aurie (~168, from ~148)
• FB spend went down while Google spend went up - FB got cheaper, Google got pricier
    ◦ FB CPT $143 -> $77, back to normal after 4 rises in a row
    ◦ Google CPT $57 -> $70, highest since May 2024
    ◦ FB CAC for 10/25-10/31 at $251 (target $90), highest since April 2022

cc : <@U1BCYETM2> <@U1BD3BZRN>
```

Why it works:

- Three bullets — "the executives don't like reading long posts".
- The week-late New Subs move leads and carries its week; that week's CAC sits
  beneath it.
- No spend or trial-count figures from the sheet: the channel shift is in words,
  with each channel's CPT nested. The one count is the Aurie-upsell trials from
  Mixpanel, which the author asks for.
- Every record claim ("lowest in the sheet", "highest since May 2024") was checked
  against the full column, not the trailing band.
- What stayed in chat: the mobile reg -> trial drop (provisional on its 7-day
  window), the FB CAC revision for 10/18-10/24 after the iOS fix, and Viktor's
  Google and family-plan readings.

## Aurie differences

Same rules, different spine: WoW ARR growth with any milestones, paying subs,
WAC, the Appstack spend / paid / CAC trio, margin, % annual, and a closing sheet
updates bullet that carries the blended-CAC caveat.

Worked example — Aurie, week of 2026-11-01, posted 2026-11-09:

```
Aurie Growth ROI notes :
• ARR +6.8% WoW ($514k -> $549k), with a few milestones:
    ◦ Over 4k paying subscribers (4.2k)
    ◦ 2k weekly active conversers
    ◦ 1k weekly active voice conversers
    ◦ New subs 294 -> 368, highest so far
• Appstack CAC up to $65.5 (from $64.5), highest since May - spend went up again but paid stayed flat
    ◦ Spend $16.8k -> $17.0k, paid 260 -> 260
• Margin down to 4% (from 6%), 3rd drop in a row - paid LLM cost grew faster than paid users
    ◦ Paid LLM $5.1k -> $6.0k, paid conversers 1,355 -> 1,486
    ◦ I restated 10/4-10/18 with product-only LLM cost: 5% / 22% / 8% (I posted -17% / -18% / -9%)
• Sheet updates:
    ◦ Added the web section back; totals like blended spend already include web, only the web section itself still needs a few changes
    ◦ UGC Paid / Survey Answered is overstated, it also counts TikTok ads - I need to fix it
    ◦ Blended CAC ($48) is understated, UGC and TikTok spend aren't in yet

cc : <@U1BCYETM2> <@U1BD3BZRN>
```

The CAC caveat says the printed figure is understated and stops there — no
estimate of where it lands (`sheet-contract.md`). The milestones are first
crossings checked against the full column, and the paying-subs figure is the
sheet's (4,216 reads 4.2k). The margin restatement is the owner's own correction,
stated in their voice. A small move (Appstack reg -> paid 31.1% -> 32.7%, inside
its band) earns no line; when a small move does, "could be normal" is a legitimate verdict.
