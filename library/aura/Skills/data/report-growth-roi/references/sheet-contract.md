# Sheet contract

Which sheet, which tab, how to read it. Both trackers are Sunday–Saturday weekly
grids with one row per week and section-grouped columns.

## The two sheets

| App | Sheet | ID | Main tab |
|---|---|---|---|
| Aura | `Growth ROI tracker: Marketing Performance & CAC LTV Modeling (Aura)` | `1WFSWn9PgM4oYjyLzr3w-meyww7zXQFeyCDVQGfx3LQY` | `Overall by channel weekly (22-26)` |
| Aurie | `Growth tracker - Aurie` | `1_QT0_5zfG2wdcYsqxMnC3wvJUBn_53zt189eAKkcV_M` | `Overall by Channel, Weekly` (gid 0) |

Access is through the Google Sheets MCP (`read_rows`, `get_spreadsheet_info`) —
see `Tools/Google Sheets/Google Sheets (API access)` for auth, limits and the general gotchas.
Both sheets are owned by other people, so a 403 is a sharing boundary, not a
broken setup.

The Aura sheet has ~20 other tabs (per-locale, per-OS, LTV modelling, CPT
targets). This skill reads the weekly overall tab only; the per-locale tabs are a
separate, deeper cut that the note does not carry by default.

## Reading discipline

**Headers are in row 2, not row 1.** Row 1 carries section group labels
(`Overall`, `Facebook Blended`, `Google Paid search (web)`, …) and row 2 carries
the actual column names. The two are **offset from each other in places** — a
column map derived from row 1 alone is wrong by one or more columns. Always read
rows 1 and 2 in the *same* range call, then anchor every metric on its row-2
header.

**Resolve the week by label, not by row number.** Column A holds the week label
(`8/23 - 8/29`). Labels contain typos — the 2026-08-30–09-05 row reads
`8/30 - 8/5` — so match on the start date, and never assume row N is week N.

**Rows come back at unequal lengths.** The Sheets API trims trailing blanks, so a
row whose final columns are empty arrives shorter than its neighbours. Pad it from
the wrong end and an entire section shifts sideways while every value still looks
plausible — a shifted Aurie read once put 7,620 clicks in the downloads column.
Pad to the header width on the right, then verify a known identity before trusting
any block: on Aurie `Marketing spend` equals Appstack spend plus UGC spend, and on
Aura paid trials plus organic trials land within the usual untracked gap of the
total.
Aura data starts at row 3 (`12/26 - 1/1/2022`).

**Never assume a tab's gid is 0** on the Aura sheet; build the lookup from
`get_spreadsheet_info`. (Aurie's main tab genuinely is gid 0.)

## The two-speed fill — the trap that matters most

Within a single week's row, columns do **not** all populate at the same time.

For the week that has just closed, the Aura tab carries:

- **Filled** — `Spend`, `Trial starts`, `Blended CPT`, the per-channel spend /
  trials / CPT, the mobile and web CR columns.
- **Blank, and filling a week later** — `New Subs`, `Blended CAC`, `Paid CAC`,
  `trial-paid (after refunds)`, every `ASP …` column, the profit and ROI columns,
  `New stripe subscribers`, `Web family subscribers`.

So a Monday reading of the newest row **mixes two weeks**: CPT for week `W`, CAC
and ASP for `W-1`. This is not a bug and not a reason to wait — it is the shape of
the sheet, and the note has to label which week each figure belongs to.

Verified across consecutive weekly posts: the 2026-08-17 note quoted CPT `$28.6`
from the 8/9–8/15 row alongside CAC `$51` and iOS ASP `$83`, both of which are the
8/2–8/8 values (`$51.6` / `$83.3`). The same pattern holds on 2026-08-31 and
2026-09-07.

**Consequences to honour:**

- A blank in the newest row means *not loaded yet*, never zero. Do not average it
  in, do not treat it as a drop.
- Cost findings are about week `W`. Conversion and revenue findings are about
  `W-1`. Say so.
- `W-1` figures can still move after they were first reported (spend and sub
  reconciliation). Compare against what was posted last week and call out any
  material revision in one line.

## Aura column anchors

Section labels sit in row 1; these are the row-2 anchors this skill uses.

| Section | Starts at | Metrics used |
|---|---|---|
| Overall | `B` | `Spend`, `Trial starts`, `New Subs`, `trial-paid (after refunds)`, `Blended CPT`, `Blended CAC`, `Paid CAC`, the four `ASP …` columns, `New stripe subscribers`, `Web family subscribers`, `% Organic`, `% Web Paid`, `% Web family plan` |
| Mobile CRs | `AM` | `AM`-`AQ`: `Sign up start -> Registration`, `Registration -> Trial`, `Trial -> Paid (8D)`, `Trial -> Cancellation (7D)`, `Paid -> Cancellation (7D)` |
| Web CRs | `AR` | the same five at `AR`-`AV`, plus `Registation -> Mobile login` (`AW`) and `Trial -> Upsell (fp)` (`AX`) |
| Facebook Blended | `AY` | `Total Spend`, `Total trial starts`, `Total paid starts`, `CPT (target 50)`, `CAC (target 90)`, `% Paid spend` |
| Facebook Web | `BL` | `Spend - web`, `Web trial starts`, `CPT`, `CAC` |
| Facebook Mobile | `BW` | `Spend - mobile`, `Total Mobile Trials`, `CPT`, `CAC` |
| Facebook Appstack | `CK` | `Spend - Appstack`, `iOS Registrations`, `iOS Trial starts`, `CPR`, `CPT`, `CAC` |
| Google Paid search (web) | `CT` | `Spend`, `Registrations`, `Trial starts`, `Paid starts`, `CPR`, `CPT (target 43)`, `CAC (target 80)` |
| Influencer (YouTube) | `DL` | `Spend`, `Trial starts`, `CPT`, `CAC` |
| Affiliate | `DU` | `Spend`, `Trial starts`, `CPT`, `CAC` |
| Referrals | `ED` | `Trial starts`, `Coach referrals`, `Non-coach referrals` |
| SEO | `EK` | `Traffic`, `Onboarding starts`, `Trial starts`, `Full CR (View > Trial)` |
| Organic web (incl. SEO) | `EU` | `Trial starts`, `Paid starts`, `Trial-paid` |
| Organic iOS (incl. SKAN) | `FA` | `Registrations`, `Trial starts`, `Paid starts`, `Trials excluding SKAN` |
| Organic Android | `FK` | `Downloads`, `Trial starts`, `Paid start` |

**Subscriber columns in the Overall block nest.** `% Web Paid` (`AI`) is
`AF / E` and `% Web family plan` (`AJ`) is `AG / AF`, so web family subscribers
are a subset of new stripe subscribers, which are a subset of `New Subs`. That
gives a sub movement a clean two-level decomposition — web versus mobile from
`E - AF`, then family versus individual from `AF - AG` — which is how a CAC move
gets localised without a second data source.

It is also the identity that catches a bad cell. Both ratios must reproduce from
their own numerator and denominator; when one of the three columns is wrong the
ratio still computes and still looks plausible, so check the arithmetic against
a second source before reporting any of them as a record.

**Check `New Subs` is complete before reporting a `W-1` CAC or trial-paid.**
`E` combines the Stripe feed (`AF`) with the App Store feed, and the App Store part
can land late or partial while every downstream cell still computes. Two checks,
run on the `W-1` row before quoting `Blended CAC` (`N`) or `trial-paid` (`J`):

1. The non-Stripe residual `E` − `AF` sits in its usual range (40–73 a week from
   August to October 2026).
2. `ASP - iOS Content Annual w/ discount` (`R`) is neither `$0` nor blank.

When either check fails, the printed CAC is overstated and trial-paid understated.
Say so and stop there, as with pending UGC cost on Aurie — do not publish an
estimate of where it lands. Worked case: the 9/27–10/3 row (251) first read `E` 248
against `AF` 228 (residual 20) and `R` `$0.0`, with CAC printing `$92.0`. Once the
App Store feed was re-imported the row read 268 subs and `$85.2` CAC, and Mixpanel
Apple + Google paid users rose 96 -> 109 that week, so there was no real App Store
collapse. Tracked in PROD-7342.

`J` against the `Trial -> Paid (8D)` columns (`AO`, `AT`) is not a completeness
check. `J` is same-week subs over same-week trials, while `AO` and `AT` are matched
trial cohorts, and `AO` carries the Aura trials started from the upsell inside
Aurie (the CR cohort split below). `J` sits below both columns every week from
10/11 on with a complete App Store feed.

**Check the unattributed trials before crediting a trial move to performance.**
`Trial starts` (`D`) minus the tracked channels — Facebook blended (`AZ`), Google
(`CW`), Influencer (`DM`), Affiliate (`DV`), Referrals (`ED`), Organic web (`EU`),
Organic iOS (`FB`) and Organic Android (`FM`) — is the trials no channel claims.
Each of these is the block's `Trial starts` column; resolve it by that row-2
name, since its neighbours (`CV` Google `Registrations`, `FL` Android `% Change`)
hold plausible numbers that silently shift the residual.
It ran 43–70 a week from July to September 2026. A total-trials move that the
tracked channels do not share sits in this residual, and blended CPT improves
with it while no paid channel got cheaper. Compute it every run, from the same
eleven rows as the band.

Trials started outside Aura's own acquisition land here. Aura trials started from
the upsell inside Aurie (`aurieAuraUpsell` in the Aurie app from 2026-10-07, the
Aurie web upsell from 2026-11-05) carry no Aura install source. Count them in
Mixpanel project `963821`: unique users on `Subscription purchase` whose
`Paywall Type` contains `Aurie` ("Aurie Stripe Checkout Upsell", "Aurie Web
Upsell"), by day, summed Sunday to Saturday. They ran 44, 148 and 168 in the weeks
of 10/18, 10/25 and 11/1, and the residual net of them sat at 63–82. Run the count
every week the residual is above its pre-launch range; the post names this source
with the count (`note-conventions.md`).

**The Overall block ends in `WAPU (incl. premium trial)` (`AK`) and `WAL (incl.
free)` (`AL`).** Both decline almost every week, so an off-band reading on either
is not reportable (`note-conventions.md`).

**The CR block fills at three speeds.** `AM`, `AN`, `AR`, `AS` and `AW` fill for
the week that just closed; `AO`, `AP`, `AT`, `AU` and `AX` fill a week late
(trial -> paid needs its 8 days); `AQ` and `AV` fill two weeks late and sit on the
trial-start row, one row earlier than the week the number became available.
`data:update-growth-roi-sheet` owns the write.

`AM`, `AN`, `AR`, `AS` and `AW` come from saved reports with a 7-day conversion
window, so the newest row is written before its last sign-ups have had their seven
days. A sharp move in that row is provisional: keep it out of the post that week,
and re-read the saved report on the next run before reporting it.

**The mobile and web CR cohorts split on the profile's platform, and anything that
is not `UserWeb` counts as mobile** — including Aurie-origin profiles with no Aura
platform set. A cross-product trial source therefore lands in the mobile columns:
in the week of 10/4–10/10 the `aurieAuraUpsell` trials lifted mobile
`Trial -> Paid (8D)` (`AO`) to 48.1%, the 2026 high, while web (`AT`) read its 2026
low. Opposite moves on the two surfaces in the same week are a cohort-mix question
before they are a conversion finding.

**The CR block has a basis seam at row 211/212.** The 2026 rows (212 onward, the
week of 12/28/2025) come from the saved reports on Mixpanel dashboard `6594279`;
the pre-2026 rows were rebuilt separately and sit about 1.5pp below the
saved-report basis (PROD-6418). Week-over-week shape holds on both sides, so
compare moves, never an absolute level across the seam — check a "lowest in the
sheet" claim on a CR column within the 2026 block.

**Web `Sign up start -> Registration` (`AR`) reads roughly 1.5-3pp low from
August 2026.** The saved report does not exclude Ashburn, VA data-center traffic,
which adds sign-up starts that almost never register. A drop in `AR` is only a
finding once its step-1 volume has been checked for an Ashburn surge.

The Referrals block runs `ED`-`EJ`, seven columns wide — `Trial starts`,
`Paid starts`, `Trial-paid`, `Coach referrals`, `Non-coach referrals`,
`Cash impact (7D)`, `% New subs (all)` — which is what puts SEO at `EK` and every
section right of it one column further along than a six-wide reading implies.

Everything from the `Inactive >>` marker rightward (YouTube Ads, Twitter, Bing,
Apple Search Ads, Moloco, Taboola, Outbrain, Podcast, TikTok, Applovin,
Pinterest, Snapchat, …) is paused. Do not report a paused channel's zero as a
finding.

**Targets, for context not for bullets:** blended CPT `< $20`, blended CAC
`< $45`, Facebook CPT `50` / CAC `90`, Google CPT `43` / CAC `80`. Aura has been
above the blended targets all year — see Step 2 of `SKILL.md` on why the level
alone is not reportable.

## Aurie column anchors

Aurie's tab is one grid of ~206 columns, sectioned in row 1 as: `Topline -
growth`, `Topline - marketing efficiency`, `Weekly Targets`, `Topline - Mobile`,
`Mobile - CR`, `Mobile - Activation & Retention`, `Margin`, `Channels`, `Channel
Breakdown by Survey`, `UGC` (topline / in-house team / Playkit / company
accounts), `Paid` (`Meta - Appstack`, `Meta - Mobile`, `Meta - Web`), `Platforms`,
`Web`, and the `Inactive ->` marker.

Row-2 anchors, which are what to read against — row 1 is offset from row 2 here
too (`Weekly Targets` sits at `Z` over `[Expand column]` while its first real
column, `ARR target`, is at `AA`; `UGC >` sits over a blank `CO` while `Total
spend` is at `CP`). The grid gains and loses columns between runs, and the owner
reorders blocks, and a shift leaves every value plausible — so resolve each letter
from its row-2 name on every run and treat this table as the expected layout, not
the source of truth. Letters verified 2026-11-09:

| Section | Starts at | Metrics used |
|---|---|---|
| Topline - growth | `B` | `B` `ARR`, `C` `Growth rate (WoW)`, `D` `Paying subscribers`, `E` its `Growth rate (WoW)`, `F` `Weekly Active Conversers`, `G` its `Growth rate (WoW)`, `H` `WAPC (paid only)`, `I` its `Growth rate (WoW)`, `J` `Weekly Active Conversers (Voice Only)`, `K` its `Growth rate (WoW)`, `L` `Cumulative Downloads` |
| Topline - marketing efficiency | `M` | `M` `Marketing spend`, `N` `Free/trial LLM costs`, `O` `New registrations`, `P` `New subscribers` (`Q` / `R` split it into no-trial and with-trial), `S` `CPR`, `T` `Blended CAC (Marketing)`, `U` `Blended CAC (Fully-loaded)`, `V` `% Margin`, `W` `% Annual` |
| Weekly Targets | `AA` | targets only; not reported |
| Topline - Mobile | `AK` | `AK` `Spend`, `AL` `Appstore Impressions`, `AM` `New Downloads`, `AN` `Sign up starts (DL proxy)`, `AO` `Registrations`, `AQ` `Paid`, `AV` `CPD (start)`, `AW` `CPR`, `AY` `CAC` |
| Mobile - CR | `AZ` | `AZ` `Organic Impressions > Appstore Impressions`, `BA` `App Store Impression > Sign up start`; the funnel proper starts at `BB` — `Sign up start > Registration`, `BC` `Registration > Trial`, `BE` `Sign up Start -> Paid`, `BG` `Paid -> Cancel (7D)`, `BH` `Paid -> Refund (7D)` |
| Mobile - Activation & Retention | `BI` | `BI` `Registration -> First session started`, `BJ` `Purchase -> First session started`, `BK` / `BL` / `BM` `D1` / `W1` / `W4` retention, `BN` `Paid -> First session started`, `BO` `D1 retention (Paid -> conversation)` |
| Margin | `BR` | `BR` `Weekly revenue runrate`, `BU` `% Stripe (By Week)`, `BV` `LLM total cost` (`BW` free/trial, `BX` paid), `BY` `LLM cost per active user`, `BZ` `Voice total costs` (`CA` free/trial, `CB` paid), `CC` `Voice cost per active user`, `CD` `Payment processing costs`, `CF` `Margin (All) - Paid only ($)`, `CG` `Margin (All) (%)` |
| Channel Breakdown by Survey | `CI` | `CI` `% UGC/Social (Tiktok)` is blank; read `CJ` `% UGC/Social (Tiktok)`, `CK` `% Paid`, `CL` `% Other organic & Aura` |
| UGC | `CP` | `CP` `Total spend`, `CQ` `Total impressions`, then `UGC Topline Metrics` from `CS` `Survey Answered`, `CT` `Survey Answered / Registration`, `CU` `Paid / Survey Answered`, `DA` `Attributed Paid (Total)`, `DI` `CPM`, `DL` `CPR`, `DM` `CPT`, `DN` `CAC` |
| UGC - In house team | `DQ` | `Spend`, `Media Impressions`, `Creator costs`, `# Videos`, `CPM` |
| Playkit UGC | `EE` | `Spend`, `Media Impressions`, `# Videos` |
| Company accounts | `EL` | `Media Impressions`, `# Accounts`, `# Videos` |
| Meta - Appstack | `ET` | `ET` `Spend`, `EU` `Clicks`, `EV` `Downloads`, `EW` `Registrations`, `FA` `Paid (Total)`, `FB` `CR: Click > Downloads`, `FC` `CR: Registration > Paid`, `FF` `CPD`, `FG` `CPR`, `FH` `CPT`, `FI` `CAC` |
| Meta - Mobile | `FJ` | dormant; treat a zero as paused, not as a finding |
| Meta - Web | `FT` | `FT` `Spend`, `FU` `LP Visit` (all site visitors), `FV` `LP Visit (Meta Ads Only)`, `FW` `Sign up start`, `FX` `Registrations`, `GA` `Trials`, `GB` `Paid`, `GL` `CPT`, `GM` `CAC` |
| Web | `GO` | `LP`, `Sign Up Start`, `Registration`, `Trials`, `Paid`, and the CRs from `GT` |

**Margin reconciles to the paid-user costs.** `CF` = `BR` − `BX` − `CB` − `CD`, and
`CG` (mirrored in `V`) = `CF` / `BR`. Free/trial LLM cost sits outside the margin
and inside the fully-loaded CAC instead: `U` = (`M` + `N`) / `P`. Check the identity
before reporting a margin move, and check that the cost cells are complete: the
LLM and voice columns come from the daily cost store, which lags a day and is
re-stored for a range after a capture defect, so a cost column that falls while
`Weekly Active Conversers` rises is a completeness question before it is a margin
finding (`confounders.md` → Measurement changes). The owner corrects cost cells after the fact, so a
prior week's margin can move between runs; the post states that as the owner's
correction (`note-conventions.md`).

A cost move that pushes margin out of band has a mechanism, and it is usually
already documented: an LLM step-up is a model or prompt-caching change on the
engineering side, with its own Linear card and `#ai-cost` thread. Find that record
before reasoning from the cells (`confounders.md` → *Known incidents first*). The
week of 10/4–10/10 is the reference case: `LLM total cost` (`BV`) first printed
$3.7k -> $8.6k because
`chat-latest` started using far more tokens per conversation from 10/7
(PROD-7290, PROD-7343), and the post carried that cause.

The `Inactive ->` marker sits right of the `Web` block; resolve it by name, and
treat everything right of it as paused. `Marketing spend` (`M`) reconciles to
Appstack spend (`ET`) plus Meta Web spend (`FT`) plus UGC spend (`CP`). Meta Web
went live again in the week of 11/1–11/7 ($736); the owner is still changing that
block, while the totals that use it (`M` and everything built on it) already count
it.

TikTok ads (launched the week of 11/1) have no block, so their spend is not in `M`
and blended CAC (`T`) reads low by that amount. Their payers do reach the survey,
so UGC `Paid / Survey Answered` (`CU`) is overstated until the owner separates
them. Both are sheet defects the owner is fixing: name them in the post's sheet
updates bullet (`note-conventions.md`), never estimate the missing spend into a
figure.

**Three paid columns, none nested.** `New subscribers` (`P`) is the booked
new-subscriber series and the one the note's topline uses. Topline Mobile `Paid`
(`AQ`) is a broader mobile paid-event count and Appstack `Paid (Total)` (`FA`) is
Meta-attributed paid; their attribution, cohort and window definitions differ, so
`FA` can exceed `P` and `AQ` can sit well above both. Never subtract one from
another to make a channel split, and treat a widening `AQ` − `P` gap as a
definition warning for the sheet owner rather than as missing subscribers.
`Sign up Start -> Paid` (`BE`) is the same-week ratio of `AQ` to `AN`, not a
matched signup cohort, so a record on it is not a conversion finding on its own.

**The two-speed fill runs inside blocks here, not only across them.** In the UGC
block the survey-attribution columns (`Survey Answered` through
`Total Paid (Estimate from Survey)`) land on time while `Total spend`,
`Total impressions` and every derived CPM / CPD / CPR / CAC arrive a week later.
Since `Marketing spend` includes UGC, a missing UGC cost makes blended CAC and CPR
read far better than they are.

Verify it per run rather than assuming: `M` equals `ET` + `FT` + `CP` to the dollar
every week the block is complete, so `M` == `ET` + `FT` with `CP` blank or `$0` is
the signature of a pending UGC cost. The pull can lag five weeks, not one; say how many weeks
are pending.

**Say the printed figure is understated and stop there — do not publish an
estimate of where it lands.** `CP` is not cash and cannot be fetched early. It is
the sum of the in-house team's coach costs, creator costs and creator bonus
payouts, in which creator costs are that week's UGC *media impressions* priced at
a modelled `$5` CPM — so the number does not exist until the impressions column
is pulled. The real UGC *ad* spend is already inside `ET`, which is full Meta
account spend, so it is never added on top.

Estimating it has been wrong by a wide margin: the 9/6 week was published as
"about `$37` once UGC lands" and landed at `$7,000` of modelled cost, making the
week `$69` CAC and `$19` CPR. The modelled cost tracks UGC *output*, which can
double week over week, so a prior week is not a guide.

Aurie-specific reading notes:

- **Margin is a first-class metric here** and Aura has no equivalent. It belongs
  in the Aurie note.
- **Appstack is the main paid engine** (it is dormant on Aura), so its CPD / CPR /
  CAC trio usually carries the Aurie headline.
- **Channel attribution comes off a survey**, not UTMs — the `Channel Breakdown by
  Survey` block. Survey-answered rate is itself a metric worth watching, because a
  drop in it degrades every channel split downstream.
- **UGC** has its own topline plus per-source blocks. The canonical UGC estimation
  formula and the Appstack-exclusion rule live outside this sheet; do not
  re-derive them here.
- **Mobile free trials were removed around 2026-05-25.** Post-cutoff paid signups
  skew to direct paid starts rather than trial conversions, so any comparison
  spanning that date must split the two.
- **Round-number milestones are reportable.** When `Paying subscribers` (`D`),
  `Weekly Active Conversers` (`F`) or the voice-only conversers (`J`) first
  crosses a round thousand, check the full column to confirm it is the first
  crossing and list it in the post (`note-conventions.md`).

## Related

- `Tools/Google Sheets/Google Sheets (API access)` — auth models, limits, the general gotchas
  (including the two-speed fill and the row-2 header rule).
- `Procedures/Marketing` → *Daily Growth ROI update* — the automated 6am PT write
  that fills the daily tabs, and *Weekly Growth ROI notes* — the note conventions
  this skill implements.
