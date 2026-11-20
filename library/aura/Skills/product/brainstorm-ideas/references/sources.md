# Sources — what the evidence pack is built from

Read by step 2 of `SKILL.md`. Every proposal names which family its evidence came from,
and every evidence item in the pack carries a grade.

## The families

No family outranks another by type — each can produce a good idea. What is graded is the
**item**: how strong it is and whether anything else corroborates it.

| Family | Read | Feeds |
|---|---|---|
| **Listening to users** | the Top 10 — Conversations and Top 10 — Cancellations sections of `Learnings`; `Learnings/Conversations`; the rows underneath — `Signals/Cancellations`, `Signals/Conversation Patterns`, `Signals/Conversation Topics`; the latest `Signals/Aurie Conversations` weekly report | `new` and `feature` above all — this is where jobs show up |
| **What every experiment settled** | the Top 10 — Experiments section; `Learnings/Experiments` terminal rows from Aurie, Aura and Aurie Web — effect size per metric and arm, guardrail harms, segment differences, why a run was inconclusive or invalid, and the successor hypothesis each one implies | `screen` and `feature`; the experiment-successor lens |
| **What the app does today** | `Products/Aurie/Features`, `Products/Aurie/Screens` and the capture hierarchy dumps | the surface an idea lands on, and *why now* for a `new` one |
| **What acquisition promises** | `Learnings/Ad Campaigns` (winning and losing angles), `Identity/Brand/Messaging Hierarchy` | the promise-gap lens: what an ad promised that the product cannot yet deliver |
| **Reading the market** | `Domain/Competitors` and its children, `Domain/Market landscape`, the Top 10 — Growth Readings section, `Learnings/Growth Readings` | the market lens; `new` ideas |
| **Prior decisions** | `Learnings/Loop/Aurie Features Ideas` — every row on the scope, whatever its status, with its rejection or defer reason | prior art, never inspiration |

**Read the rows, not only the synthesis.** A Top 10 section is somebody's reading of the
evidence; the rows under it are the evidence. An idea that cites a row is answerable; one
that cites a heading is not.

**Report what is missing.** PMF survey responses, support threads, App Store reviews and
Mixpanel retention cuts have no curated home in AI Knowledge yet. When the scope needs one
of them and it is not in the knowledge base, say so in the readout rather than guessing
what it would have said — and do not run the sweeps that produce them from here.

## Evidence grade

Every item in the pack, and every proposal, carries one:

| Grade | What it is |
|---|---|
| **A** | a first-party experiment result, or behaviour verified in our own data |
| **B** | a first-party qualitative pattern that repeats across rows — cancellations, ratings, conversation topics |
| **C** | a direct competitor observed doing it, or reviews of one |
| **D** | adjacent or market inspiration — a trend, a fast-growing app's mechanic |

A proposal's grade is its strongest corroborated item. A single anecdote is never above B.

## The outside source — a different burden of proof

Everything else cites evidence about *our* users; this cites evidence about somebody
else's, which is a weaker claim about ours. **An outside pattern never becomes a proposal on
its own:** it must name the transfer mechanism — the same audience, the same moment in the
journey, the same job — **and** the first-party signal that makes it plausible for Aurie.
"Tolan does this" is the beginning of the argument, not the end.

**Scope.** Three things, and only these:

1. **Direct AI-companion competitors** — product-level inspiration: what they build, how
   their onboarding, paywall and return loops work. `Domain/Competitors` holds who we track.
2. **Fast-growing similar apps** — what makes them grow, and any killer feature behind it.
3. **High-level market and behaviour trends** — `Domain/Market landscape`, Growth Readings.

Not a feature scrape of unrelated categories. A habit app's streak mechanic is not a gap in
Aurie, and Replika is a **contrast**, not a model — Aurie is explicitly not a companion
optimised for attachment.

**The research library.** Competitor and market research is read from `Domain/Competitors`,
not redone every run. At trigger the skill shows the library's last-edited date and asks
whether to refresh it; a refresh is live web research for this run, and what it finds is
proposed for `shared:ingest-knowledge` afterwards so the next run reads it instead of
paying for it again.

## The March backlog

The first ~40 rows of the ideas database were generated in March 2026 by looking at every
app screenshot. Read them with that provenance:

- **Strong signal:** explicit rejection and defer reasons, decisions, and anything the team
  approved. These are the best negative priors in the loop.
- **Weak signal:** their category mix. They were bounded by the visible screens of an
  older app, so they skew to screen tweaks.
- **Revalidate before citing:** check the idea against today's app, roadmap and evidence.
- **No negative inference:** an opportunity absent from that backlog was not necessarily
  considered.

## What great looked like

The four features that shaped the product — and one honest note first: **how they were
conceived is not written down anywhere the loop can read.** What follows is a
reconstruction, which is why every `new` idea writes its own origin down in the row.

- **Feed** — somewhere to go when you do not know what you need. Turned "open the app"
  from a blank prompt into a place.
- **Daily practice** — a ritual with a shape and a duration, when users were asking for
  *methods* and getting reflections. A reason to come back at the same time tomorrow.
- **Profile** — a sense of self inside the app: who am I here, what am I working on.
- **Journal** — a place for the inner life the conversations were already producing, so it
  stopped disappearing when the session ended.

Each names a **job** the user already had and the app had no place for; each is a **bet**
on something not yet proven; each **built on** what the product could already do; and each
stays inside what Aurie is — none is a productivity coach, a streak machine, or a companion
to bond with. That is the shape a `new` idea has to have.

## Per app

The method is the same for every app; the sources and backlog are per app. Aurie is the
default because it is the only app with every source wired.

| | Aurie (default) | Aura |
|---|---|---|
| Ideas backlog | `Learnings/Loop/Aurie Features Ideas` | `Learnings/Loop/Aura Features Ideas` — pass its data source id to `record-ideas` |
| Screens | `Products/Aurie/Screens`, 80 in three skies | `Products/Aura/Screens` — its variant axis is locale, not sky |
| Design system | `Design/Aurie` and the Aurie Claude Design project | `Design/Aura`; no Claude Design project yet, so a `new` idea cannot be drawn from tokens — say so rather than borrow Aurie's |
| Identity | `Identity/Brand` — Aurie speaks in the first person | the same page; Aura does not |
| Conversations, cancellations, experiments | shared databases, **filtered on `App`** | the same, filtered the other way |
| Loop channel | `#ai-loop-aurie-features` | `#ai-loop-aura-features` |

The Top 10 Learnings pages are mostly Aurie-derived; when reading them for Aura, check
which app a claim came from before citing it. Aura is a content platform, so its outside
comparisons are Calm and Headspace. Never mix them: an Aura idea citing Aurie's
cancellations is a citation about the wrong users.
