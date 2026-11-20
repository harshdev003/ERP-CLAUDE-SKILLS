# Report format — section-by-section spec

The full formatting spec for the report `profile-user` returns: what each `full_profile` synthesis section must contain (with its PII rules), the `conversation_only` / `journal_only` thin-wrapper prefaces, the Feedback-explanation skeleton and per-kind headings, and the per-route Output-format shapes. `SKILL.md` Step 4 carries the routing instruction + section order; this file carries the verbatim detail.

## Full-profile synthesis sections (Step 4a)

Synthesis sections (always in this order):

0. **Employee / internal banner** *(only when `internal.is_internal` from Step 3.5 — omit entirely otherwise)* — the very first line of the report, above the Headline, so no reader misses it:

   ```
   > ⚠️ **Employee / internal account** — this entry is from **<who>** (<confidence>; matched `<reason>`). Treat it as internal feedback, **not** a genuine user signal.
   ```

   Use `possible` phrasing ("likely an employee — name match only, unconfirmed") when `internal.confidence=possible`. First name only, per the Step 3.5 PII note.

1. **Headline** — a one-line TL;DR. Lead with UID prefix only. Combine plan/vertical (from behaviour) + the dominant signal across both sources + (if applicable) the cancellation reason. Example: *`0VwbDpK…` — Sleep monthly, day 4 of 7 trial; cancelled citing "not conversational" — transcripts show 3 friction moments around scripted multi-choice replies; behaviour shows high content engagement but only 2 Aurie sessions.*

2. **Identity & subscription state** — pulled from the behaviour report. UID prefix (first 8 chars), email domain (not full email), signup date (**render it via the tenure table in [`references/engagement.md`](engagement.md) → "Tenure rendering" — the Aura clock carries bulk stamps, and the rule follows the value, not the section it arrived in**), vertical, plan, platform, locale, **acquisition** (the **Acquisition** sub-line + raw payload block produced by `analyze-user-behavior` Step 6 from `attributionData` — render both verbatim here; the headline format depends on which schema the payload happens to carry, and the raw block is the verifiability artifact, so don't strip it down to a single line), current subscription state, trial/cancellation dates. **PII rule (hard):** never paste `givenName`, `familyName`, or the full email from `/users/<id>` — applies to this section AND to every downstream surface (Step 6 Slack reply, Step 7 Notion row, any forwarded report). See `Compliance/Privacy` for the canonical rule. `attributionData` is campaign metadata, not PII — safe to render verbatim across all surfaces, but **never** invent or normalise field names you don't see in the raw payload; the schema is fluid and the verbatim block is what lets a reader audit the interpretation. **Authority:** the sibling skill's rule — rows from `POST /subscriptions/list/:userId` (its Step 5) are source of truth across **all six** Aura Health subscription types: Aurie's `ai`, plus Aura's `content` / `coaching` / `business` / `community` / `coach`. The Aura API user row's `premium*` fields mirror only the `type:"content"` row and are `null` for users whose paid products are any of the other five types — never read them as the canonical "is this user paying?" answer. Mixpanel events and engage profile are weaker cross-references that may lag by minutes. Preserve any disagreement note from the behaviour report verbatim. When Mode A surfaced a `Subscription` / `Type` / `Product` / `Provider` / `Purchased` block from the k8bot message, those are an extract from the same Stripe-side record that flows into the subscriptions endpoint — they should agree with the behaviour report's Step 5 rows; if they don't, flag it as a tracking-mirror bug. **`deletedAt` is a backend artifact, not deletion intent.** A `deletedAt` timestamp on a `/subscriptions/list/:userId` row means the backend completed an async cleanup (Stripe webhook completion / PaymentService cancel-finalization), **not** that the user requested data deletion. The real signal for user-initiated deletion is the Mixpanel event chain: account deletion is `Open Delete Account Modal` → `Delete Account Confirmation Open` → `Delete User`; data/conversation deletion is `AI Manage My Data Tapped` → `AI Conversation Deleted` / `AI Journal Deleted`. Cross-check Mixpanel before claiming a user "requested deletion" — a `deletedAt` 10–30 min after a cancel is normal backend cleanup, not intent. Origin: 2026-06-09 misread documented in [PROD-2546](https://linear.app/aura-health/issue/PROD-2546).

2b. **Tenure & engagement volume** *(always renders — gated by neither `sources` nor route, so an orchestrator that dropped `behavior` or `cost` still gets it, and the two thin wrappers carry it in their preface)* — from the `GET /ai/usage/engagement/:userId` call in SKILL.md Step 3d. Two facts nothing else in the report produces: **how long the user has had to form a habit**, and **how much they actually used Aurie**.

   Shape:

   ```
   **Tenure:** <Aura clause> · <Aurie clause>
   **Aurie volume (lifetime):** <N> completed conversations · <N> messages · <N> active days · streak <N> (max <N>) · last conversation <YYYY-MM-DD>
   ```

   **Both Tenure clauses are resolved from the table in [`references/engagement.md`](engagement.md) → "Tenure rendering"** — one lookup per clock, six possible states between them. Never hand-write either clause: the two clocks are stamped independently, so a clean Aurie join says nothing about whether the Aura sign-up is real.

   **Only the Tenure line varies — the volume line is identical in every state and must always render.** Sentinel users are pre-instrumentation accounts, i.e. the longest-standing ones, so they are exactly where lifetime volume carries the most signal. Dropping it would delete the half of this section that is still fully trustworthy.

   The tenure table's wording is required verbatim on **every** surface this reaches — this section, the Slack reply, the Notion row, any forwarded report. A bare number reads as fact.

   **Label the conversation count "completed conversations"**, never "conversations started" — `conversations.total` only counts a conversation once it has a title and summary, both written at conversation end (SKILL.md Step 3d). It is a floor.

   Zeroes render as zeroes: an account with no Aurie activity gets the section with `0 completed conversations · 0 messages · 0 active days` and the plain line *"no Aurie activity on record"*. Never drop the section — for a paying or long-tenured account, the zeroes **are** the finding. When the endpoint call failed, render the section with `**Tenure & engagement volume:** unavailable — <reason>`, fall back to the Aura sign-up date the behavioural report carried — **still resolved through the tenure table in [`references/engagement.md`](engagement.md)**, since the rule follows the value, not the source it arrived from — and repeat the miss in **Sources**.

   Convert `voice.daily` / `voice.weekly` from milliseconds to minutes before rendering, and only render them when non-zero — for a text-only user the two zeroes are already covered by the volume line.

   **What the rest of the report should do with it.** Cross-reference, don't just report: a cancellation citing "not for me" from a 4-day-old account with 2 completed conversations is a trial-timing signal, not a product-fit verdict; a thumbs-down profile rating from an account with 1800 completed conversations across 277 active days deserves far more weight than one from a two-session account; a long Aura tenure with a near-zero Aurie volume says the user never crossed over. Feed these reads into **Feedback explanation**, **Notable signals**, and **Recommended follow-up** rather than leaving the numbers stranded in this section.

3. **Behavioural profile** — distilled from `analyze-user-behavior`. Four sub-bullets: engagement (sessions over window, days active), top journey events (timestamped, in order), subscription history (the timeline from the sibling skill's section 6 — *primary* source is the `/subscriptions/list/:userId` rows with `type` / `provider` / `cancelReason` / `cancelFeedback` / `subscriptionCampaign` / `purchaseAt` / `expirationAt`; *cross-reference* is Mixpanel subscription events for user-side timing; keep both — together they're the most actionable single block when feedback is set), and top features (3–5 highest-count non-journey events with one-line meaning, already re-ranked toward the cancellation reason when feedback was passed). Don't paste the full behavioural report; pull the 8–12 facts that matter for the unified picture.

3b. **Rated profile** *(only when `insights` is in `sources` — omit the whole section otherwise; in practice this is the `*PROFILE RATED*` pivot)* — the artifact the rating is *about*, from `eng:build-aurie-user-insights` `mode=read`.

   Lead with what was rated and how:

   ```
   **Rated:** 👎 whole profile · <YYYY-MM-DD> · feedback: "<verbatim, or 'No feedback'>"
   **Artifact:** rated snapshot (rating <id>) | LIVE current profile — NOT necessarily what was rated
   ```

   Then, per rendered card: the card key and a 1–2 line characterisation of what it actually said — **paraphrased, never verbatim** if it contains anything personal (the renderers are generated *about* the user and routinely name people, places, and circumstances; `Compliance/Privacy` applies in full). When `renderer_key` is set, lead with that card and mark it as the rated one, but keep the others — a card is rated in the context of the profile around it.

   **The analytical job here is to connect the artifact to the rating.** A thumbs-down with `No feedback` — the common case — has no stated reason, so the *only* available evidence is the content itself read against what the behavioural and conversational sections say about this user. Look for the specific mismatch: a card asserting something the transcripts contradict, a confident claim built on one thin session, generic filler for a user with rich history, or a tone that lands wrong for someone in acute distress. Name the concrete mismatch or say plainly that the content looks reasonable and the rating is unexplained — **never** pad with generic profile-quality commentary that would read the same for any user.

   **Carry the artifact label into Sources verbatim.** If the sub-skill returned the live profile rather than the rated snapshot, the analysis is about content the user may never have seen; say so in this section too, not only in Sources.

4. **Cost over window** *(only when `cost` is in `sources` — omit the whole section otherwise)* — distilled from `eng:report-user-costs`. One paragraph + a compact per-source row. Format:

   ```
   **Cost (over <window label>, <Nd>):** **$X.XX total** ($X.XX/day)

   | Source | Cost | Notes |
   |---|---:|---|
   | OpenAI inference | $X.XX | <e.g. "75% of total; Conversation project leads"> |
   | ElevenLabs (ConvAI voice) | $X.XX | <e.g. "—" or "text-only user, no voice"> |
   | LiveKit (voice) | $X.XX or pending | <e.g. "9 LiveKit min" or "2026-09-25 not yet in the cost store"> |
   | Listens (Aura content) | $X.XX | <e.g. "—" or "Aurie-only user, no content listens"> |
   ```

   **Source-of-truth rule.** The $ figures come from `eng:report-user-costs` only — **never** compute or quote a cost number derived from Braintrust's `metrics.estimated_cost`. That field is unreliable (pricing-table gaps, wrong rate tier, single-project framing); see AI Knowledge [`Tools/Braintrust` → "Cost accounting"](https://www.notion.so/34c75e23af2081968f7bf7ee43e1442a). When the cost sub-skill couldn't run for any source (missing env, retention cutoff, API down), render that row as "unavailable" with a one-line reason — don't fall back to Braintrust. A LiveKit row whose days are not yet in the daily cost store renders as **pending** with the dates, never `$0.00`.

   **Save-attempt framing.** When `steering` is present (a cancellation reason, or a churn-risk PMF sentiment) and total cost is materially below the user's monthly subscription price, call that out explicitly — it's the strongest single argument for a save attempt (e.g., *"Per-day cost is $0.30; a monthly Aurie sub at $X would cover ~30× their actual usage cost. Strong save-attempt economics."*). If cost is *above* the monthly price, the opposite signal — they're a heavy outlier and the save calculus is different. Be honest either way.

   Carry forward any **Caveats** the sub-skill surfaced (e.g. "Window touches OpenAI's 2026-05-12 metadata-user rollout cutoff; pre-rollout portion extrapolated uniformly from OpenAI Costs/Usage data (no Braintrust fallback)" / "ElevenLabs days past retention read from the cost store; 2 of them pending") verbatim into the report's overall **Caveats / Notable signals** flow so the reader knows what the number does and doesn't include.

5. **Conversational profile** — distilled from `analyze-user-conversations`. Three sub-bullets: friction moments (count + 1–2 representative quotes with timestamps), positive moments (count + one quote), patterns (1–3 falsifiable hypotheses, already steered toward the `steering` string when set). Same rule: distil, don't paste.

   **Note on raw PII in transcripts.** The OpenAI stored completions that `analyze-user-conversations` now reads are **not** PII-masked (unlike the old Braintrust integration, which injected `[USER_NAME]` / `[USER_EMAIL]` / `[USER_LAST_NAME]` placeholders via `setMaskingFunction`). So when the conversational sub-report quotes Aurie's replies or the user's turns, it may contain the user's **real** name, email, or other identifying detail. These are genuine content, not placeholders or bugs — do **not** flag a real name as an identity anomaly. Instead, the synthesis layer MUST apply PII redaction before that text reaches any rendered surface (the **Feedback explanation**, **Notable signals**, the Step 6 Slack reply, the Step 7 Notion row): UID prefix only, email domain only, and never echo a full name or email. See `Compliance/Privacy` for the canonical rule. A name that is clearly *invented* by the model (a hallucinated name the user never gave) is still a real anomaly worth flagging — but it must be redacted in the same way before being surfaced.

6. **Feedback explanation** *(only when `context.stated` is non-empty — omit the section entirely when `stated` is empty or `context.kind=generic`)* — the synthesis layer this skill exists for. The heading and framing follow `context.kind`; in every kind, cross-reference the stated text against evidence from whichever sub-reports ran.

   Shared skeleton (used by every kind):

   ```
   ### Stated <reason | answer | feedback>
   > <verbatim context.stated string>

   ### What the data shows
   - **Acquisition context** *(when `attributionData` carries any creative / campaign / referral signal — skip the bullet entirely for organic users, `attribution unavailable` payloads, or unrecognised shapes where no semantically informative value resolved)*: <1 bullet quoting the specific key + value from the raw payload (`af_ad_type="..."`, `campaignName="..."`, `appsflyerAdName="..."`, `fbAdName="..."`, `adsetName="..."`, `promocode="..."`, `referralCode="..."`, or whatever key actually carries the signal in this user's payload), naming the mental model that key would have set on signup, then a sentence on whether the stated text is consistent with or contradicts that promise. Quote the key name verbatim so the reader can find it in the raw block above; do not paraphrase to a canonical name the payload doesn't actually use. Example: *"Acquired via `af_ad_type=\"ChatGPT Relationships -F- 04-15-26\"` (`media_source=fb`, `campaign=\"Aurie_WO_Scale_04-17-26 - Purchase\"`). Creative implies a conversational-companion mental model; the stated reason 'not conversational' is an expectations-mismatch signal, not a product-quality signal."* Alternate example (legacy schema): *"Acquired via `appsflyerAdName=\"Aurie_Sleep_TestimonialV2\"` (`installSource=fb`, `campaignName=\"Sleep_Scale_03-22-26\"`). Creative emphasised sleep relief; stated reason 'too expensive' is unrelated to the creative's promise — read this as a price signal, not a fit signal."*>
   - **Behavioural evidence:** <2–4 bullets pulled from the behavioural profile that corroborate, contradict, or contextualise the stated text — each bullet cites the specific event/property/count>
   - **Conversational evidence:** <2–4 bullets pulled from the conversational profile — each bullet cites a specific moment with a short quote>
   - **Convergence / divergence:** <1–2 sentences calling out where the sources agree or disagree — when an acquisition-context bullet is present, note whether the creative / campaign / referral angle aligns with or pulls against the behavioural + conversational evidence>

   ### Most plausible explanation
   <2–3 sentences: the most evidence-backed reading, falsifiable against the same sub-reports. If the data contradicts the stated text, say so explicitly rather than glossing over it.>
   ```

   Per-kind heading + framing:

   - **`context.kind=cancellation` → heading "Cancellation explanation".** Stated line: `### Stated reason`. Read the stated reason as the question "why did this user cancel?" Be honest about contradictions — if a user says "too expensive" but behaviour shows zero paywall views and conversations show repeated "Aurie didn't get me", the most plausible explanation is fit, not price — name that. (The 2026-06 sweep found "Too expensive" frequently masks a creative-mismatch fit problem; the acquisition-context bullet is what surfaces it.)
   - **`context.kind=pmf` → heading "PMF explanation — why this sentiment".** Stated line: `### Stated answer` (the PMF free-text). When the answer came through empty — a canned survey answer like `No Feedback` / `only_tried_briefly` / `other` — the orchestrator passes `context.stated=""`, so this section is driven by sentiment + behaviour alone; note "no free text — canned survey answer" in place of the blockquote. The "Most plausible explanation" MUST address two things: **(1) Tenure check** — read tenure straight off the **Tenure & engagement volume** section (2b), never by re-deriving it: copy **both** figures verbatim from section 2b, never re-derive either. The hedged forms matter in opposite directions here: a stamped Aurie join makes a long-time user look brand new, while a stamped Aura sign-up makes a brand-new account look like a veteran — and this read is sensitive to both (rules: [`references/engagement.md`](engagement.md) → "Tenure rendering"). Pair it with the lifetime volume — a low day count *and* 1–2 completed conversations is the survey-timing case; a low day count with 40 conversations is not. The PMF survey often fires Day 0–8, so a "Not disappointed" or "Somewhat disappointed" from a brand-new user is frequently a *survey-timing artifact* (no habit formed yet) rather than a genuine fit signal. Call this out explicitly and, when tenure is low, recommend a tenure-gated re-survey. **(2) Champion / at-risk read** — for "Very disappointed", which behaviours define this champion (daily retention, memory-continuity re-reads, depth of disclosure) and is it a paying or free-but-paywall-blocked save/convert target? For "Somewhat", what's missing to reach "Very"? For "Not disappointed", is it genuine non-fit or just early tenure?
   - **`context.kind=rating` → heading "Rating explanation".** The framing depends on what was rated. For the conversation shapes, ratings route `conversation_only` and the thin-wrapper preface already covers it. For **`*PROFILE RATED*` (Shape 7) it routes `full_profile` with the `insights` source**, so cross-reference the polarity (`context.category`) + any feedback against the **Rated profile** section — the artifact — and against the behavioural and conversational picture. Don't frame it around a single conversation; there isn't one.
   - **`context.kind=generic`** → no section (the empty-`stated` guard already omits it).

7. **Notable signals** — 2–4 bullets calling out things worth a follow-up that **only emerge from the combination** (i.e. things neither sub-skill could surface on its own). Examples: "active in content but no Aurie sessions despite vertical=Anxiety", "trial started 4 days ago and 11 paywall views — converting or churning is likely this week", "transcripts show emotional engagement but Mixpanel shows no app opens since the conversation". Each bullet should be falsifiable. **Cost-cross-source signals** are particularly valuable here — flag mismatches like *"high OpenAI cost ($X) but zero ElevenLabs spend — pure-text user, didn't try voice"*, *"listens cost ($X) ~5× their Aurie OpenAI cost — primarily an Aura content user with light Aurie experimentation"*, or *"per-day cost just hit 2× their monthly-sub-price equivalent — outlier worth a closer look"*.

8. **Recommended follow-up** — 2–4 concrete next steps for the team. Examples: "tag user for a save-attempt outreach before the cancellation finalises", "add a Braintrust eval case for the friction pattern in conversation 2", "review the paywall variant this user saw — they hit it 11 times". Concrete and actionable; each step names the owner artifact (a doc, a dashboard, a config).

9. **Sources** — every URL and path used. Required entries (each sub-skill entry only when it was in `sources`):
   - `GET https://services.aurahealth.io/ai/usage/engagement/<UID>` (or "unavailable — <reason>") — **always** on the `full_profile` route, since Step 3d isn't gated by `sources`. When a sentinel guard fired, append which one and the value that tripped it, e.g. *"`aiCreatedAt` bulk-stamped `<stamp>` (Guard A) and contradicted by 277 active days (Guard B) — Aurie tenure reported as a lower bound"*, so a reader can tell the join date was inferred rather than read.
   - `data:analyze-user-behavior` invocation with the window and `steering` (if used) — when `behavior` ran
   - `data:analyze-user-conversations` invocation with the window and `steering` (if used) — when `conversations` ran
   - `eng:report-user-costs` invocation with the window (and the structured JSON path it wrote) — when `cost` ran
   - `eng:build-aurie-user-insights` `mode=read` invocation — when `insights` ran. **Include the artifact label verbatim**: `rated snapshot (rating <id>)` or `LIVE current profile — NOT necessarily what was rated`. A reader must be able to tell from Sources alone whether the analysis saw what the user saw.
   - Mode A only: the Slack permalink that seeded the run (so a reader can click back to the original k8bot message)
   - Mixpanel project link with the `distinct_id` filter applied (relayed from the behaviour report)
   - OpenAI Logs URL for the user's completions (relayed from the conversations report)
   - AI Knowledge paths consulted (`Products/Aurie`, `Tools/Braintrust` for the cost-accounting rule)
   - Aura API user profile URL (or "unavailable") (relayed)
   - Window-mapping note: *"Behaviour: <Nd>. Conversations: 60d (fixed — Braintrust fallback needs the wider window)."*

## conversation_only wrapper (Step 4b)

The sibling skill's report is already the right shape — friction moments, positives, patterns, follow-up, sources — for "what happened in this rated conversation". The orchestrator's job is just to add a one-paragraph context preface so the reader knows it came from a rating message, then return the sibling's report as-is.

**Preface (always)** — four short lines above the sibling's `Headline`. When Step 3.5 set `internal.is_internal`, prepend the employee banner as the **first** line so it can't be missed:

```
> ⚠️ **Employee / internal account** — rated by **<who>** (<confidence>; matched `<reason>`). Internal feedback, not a genuine user signal.
> _Rating: <:+1:|:-1:> <Positive|Negative> on `<prompter:version>` — see [original Slack message](<slack_permalink>)._
> _Conversation ID: `<UUID>`_
> _Steering feedback: "<rating_feedback verbatim, or "(no feedback typed by user)">"_
> _Tenure & engagement: <Aura clause> · <Aurie clause> · <N> completed conversations · <N> messages · <N> active days_
```

(Omit the first line when `internal.is_internal` is false. Same banner rule applies to the `route = "journal_only"` preface in Step 4c.)

**The `Tenure & engagement` line is required, on this route as much as on `full_profile`** — it is section 2b condensed, from the Step 3d call that runs on every route. It is what tells a reader how much weight one rated conversation carries: the same thumbs-down means different things from a two-session account and a several-hundred-conversation one, and nothing else in this wrapper distinguishes them. It is also what `data:investigate-user-rating` and the `run-*-analysis-cadence` skills copy into their threaded Slack reply. Both clauses come from 2b verbatim — resolved through [`engagement.md`](engagement.md) → "Tenure rendering", never re-derived and never reduced to a bare date. Zeroes render as zeroes; a failed call renders `unavailable — <reason>`.

Then paste the `analyze-user-conversations` report verbatim. **Do not** rewrite or re-synthesise — the sibling already produced a focused friction analysis with the steering applied. Adding a profile-user synthesis layer would just duplicate work.

**Sources block addition** — append two lines to whatever Sources block the sibling produced:

- `Slack rating message:` → the permalink that seeded the run
- `Prompter:` → the `prompt:<name>:<version>` from the rating-message footer (e.g. `quick-processing-prompter:0.235.1`) so the reader can locate the exact prompt revision

The sibling's existing entries (OpenAI Logs URL filtered by conversation ID, Aura API user profile, AI Knowledge paths) stay as the sibling produced them.

## journal_only wrapper (Step 4c)

The sibling skill's report is already the right shape. The orchestrator's job is just to add a one-paragraph context preface so the reader knows it came from a journal rating, then return the sibling's report as-is.

**Preface (always)** — four short lines above the sibling's `Headline`. When Step 3.5 set `internal.is_internal`, prepend the employee banner as the **first** line (same rule as Step 4b):

```
> ⚠️ **Employee / internal account** — journal rated by **<who>** (<confidence>; matched `<reason>`). Internal feedback, not a genuine user signal.
> _Rating: <👎|👍> <Negative|Positive> — see [original Slack message](<slack_permalink>)._
> _Journal ID: `<UUID>`_
> _Journal text (excerpt): "<journal_text or "(not available)">"_
> _Tenure & engagement: <Aura clause> · <Aurie clause> · <N> completed conversations · <N> messages · <N> active days_
```

(Omit the banner line when `internal.is_internal` is false.) Then paste the `analyze-user-journal` report verbatim. Do not rewrite or re-synthesise.

**The `Tenure & engagement` line is required here too**, on the same terms as Step 4b — section 2b condensed, from the Step 3d call. `analyze-user-journal` carries no engagement data of its own, so this preface is the only place it can come from.

**Sources block addition** — append two lines to the sibling's Sources block:
- `Slack rating message:` → the permalink that seeded the run
- `Journal ID:` → the UUID from the `[id: ...]` footer

## Output format (per-route shapes)

The skill's primary return value is a single Markdown document. Shape depends on the route:

- **`route = "full_profile"`** — sections in this exact order: Headline → Identity & subscription state → Tenure & engagement volume → Behavioural profile → Rated profile [when `insights` in `sources`] → Cost over window [when `cost` in `sources`] → Conversational profile → Feedback explanation [per `context.kind`, when `context.stated` is set] → Notable signals → Recommended follow-up → Sources. Length depends on the window, `sources`, and how active the user is: light user `30d` no steering ≈90 lines; typical cancelling / PMF user `30d` with steering ≈130–190 lines; power user `90d` with steering ≈210–290 lines.
- **`route = "conversation_only"`** — four-line preface (rating, conversation ID, steering feedback, tenure & engagement) + the `analyze-user-conversations` report verbatim, with two extra entries appended to its Sources block (Slack permalink, prompter version). Length matches the sibling skill's `last_conversation` shape: ≈30–60 lines.
- **`route = "journal_only"`** — four-line preface (rating, journal ID, journal text excerpt, tenure & engagement) + the `analyze-user-journal` report verbatim, with two extra entries appended to its Sources block (Slack permalink, journal ID). Length matches the sibling skill's `last_journal` shape: ≈25–50 lines.

Step 5 returns the report inline. Step 6 (Mode A only) optionally posts a route-appropriate extract back to the source Slack thread on user approval. Step 7 (Mode A and B) optionally records the per-conversation analysis in `AI Knowledge → Learnings → Conversations` on user approval — see [`Learnings/Conversations` schema](../../../../shared/skills/record-knowledge/references/schemas/conversations.md) for the row shape. Step 8 (Mode A and B, conversation_only route only) optionally saves the conversation as a runnable eval case in a Braintrust dataset (default `Feedback Conversation`) on user approval — see [`data:save-to-braintrust-dataset` SKILL.md](../../save-to-braintrust-dataset/SKILL.md) for the row contract.

The **Headline** (full-profile) or the sibling's existing `Headline` line (conversation-only) is always a single line, so the caller can grab a TL;DR for Slack without re-reading the whole report.
