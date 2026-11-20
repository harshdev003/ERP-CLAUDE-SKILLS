# CS outreach email template

Single source of truth for the email Customer Support sends when a conversation guardrail alert is active risk. Adapted from `eng:investigate-memory-ingestion-guardrail`'s template: the body, the per-category placeholders and the checklist are the same; the identity sources and the "shared" rule are specific to a live conversation. The skill renders this template, fills the placeholders, and posts the draft on the alert thread for human review. The skill **never sends** — a human reviews every draft and CS sends.

The defining rule of this file: **the language must match the user's actual risk.** Never hard-code suicide/self-harm closing language onto every draft — that wording is wrong for an interpersonal-safety, substance, or basic-needs trip. Pick the category first (below), then assemble the email from the matching pieces.

## Step 1 — Identify the category

The category comes from SKILL.md Step 4 — the most serious thing the person expressed anywhere in the conversation. These are the seven active-risk categories in `classification_rubric.md` — the same axis used in `resource_map.md`. The handoff's own routing label (`crisis`, `treatment`, …) is not this axis.

| # | Category | Language notes |
|---|---|---|
| 1 | **Suicide / serious self-harm** | Only category that may use self-harm language. Crisis lines. Level per `outreach_levels.md`. |
| 2 | **Risk of harming someone else** | Non-accusatory; never restate the intent. Crisis lines. Always emailed (`outreach_levels.md`). |
| 3 | **Abuse / DV / coercive control / stalking / trafficking / sexual violence** | Discreet, monitored-device-safe; never characterizes anyone. Crisis lines. |
| 4 | **Child / elder / dependent abuse or neglect** | Often reaches us via a caregiver (see `{audience_suffix}`); discreet. |
| 5 | **Medical emergency / overdose / poisoning / substance** | Help lines (Poison Control / SAMHSA), not "crisis resources"; reflection/encouragement closing. |
| 6 | **Psychosis / mania / severe disorientation w/ safety concern** | Gentle; points to the listed support lines generically — never self-harm language. |
| 7 | **Immediate basic-safety** (stranded, no shelter / food / water / medication) | "Resources," not "crisis resources." Practical-next-steps closing. |

**Multiple categories:** pick the most immediate (Handler priority: crisis 8.1 > passive-existential 8.2 > complex-condition 8.7), and let the resource list carry a line from each relevant category, capped at 2–3.

**Do not use suicide / self-harm language unless the evidence specifically indicates suicide, self-harm, inability to stay safe, or immediate danger to self (category 1).**

## Step 2 — Assemble the email

Build the message in this fixed order. Each `{placeholder}` has rules below.

```
Subject: {subject}

Hi {first_name},

Some of what you recently shared suggested that {risk_phrase}. We wanted to reach out — not to assume anything about your situation, but to make sure you know help is available{audience_suffix}. You matter, and you don't have to go through this alone.

{resource_intro}
{resource_list}

If you may be in immediate danger, please call your local emergency services or go to the nearest emergency room if it is safe to do so.

You don't need to reply to this email. {closing_line}

Warmly,
The Aura team
```

### Category 2 body (harm to others) — its own email

Categories 1 and 3–7 share the body above. **Category 2 does not.** That body is written for someone who may be in danger themselves ("You matter, and you don't have to go through this alone"); a person who said they want to hurt someone else reads it as a self-harm email that missed the point. The category-2 email speaks to the anger behind the statement, keeps the safety line about acting on it, and offers someone to talk it through with. Use this body **instead of** the one above, verbatim except the placeholders:

```
Subject: Checking in

Hi {first_name},

We wanted to check in. Some of what you recently shared suggested you may be carrying a lot of anger or hurt right now. Feelings that strong are human, and they can be hard to hold on your own. Talking them through with someone can help.

{resource_intro}
{resource_list}

If you ever feel you might act on these feelings, or anyone could be in immediate danger, please call your local emergency services right away.

You don't need to reply to this email. Aurie is still here for general support, and the people at the resources above are there any time you want to talk something through.

Warmly,
The Aura team
```

- **Subject** is `Checking in` — never "support", which reads as a welfare check.
- **`{risk_phrase}`, `{audience_suffix}` and `{closing_line}` are not used.** The resources are for the person who said it, never for the person they named, so there is no third-party widening and no caregiver line.
- **One resource** (an unmapped country's single global line): the closing reads "the people at the resource above", not "resources".
- **`{resource_intro}`** — 2+ lines: `If it would help to talk to someone, these are free, confidential and available 24/7:`; 1 line: `If it would help to talk to someone, this is free, confidential and available 24/7:`. Use these only when every listed line is free, confidential and 24/7 (US 988 and Crisis Text Line are); otherwise drop the words that are not true.
- **`{resource_list}`** — the country's category-2 row in `resource_map.md`, hotline first, as for every category.
- **Partner or family add-on.** When the Step 4.5 check says `close relationship yes` — the person they talked about hurting is a current or former partner, spouse, or a family or household member — add the country's **row 2a** line as the last resource. Every mapped country has one except Ireland, and unmapped countries have none: there, use row 2 only and add the reviewer line `Reviewer: no line for people worried about their own behaviour toward a partner or family member is mapped for <country> — add one if you know it.` Recheck `{resource_intro}` with the added line: drop "available 24/7" when the 2a line is not (the UK Respect Phoneline is weekdays only).
- **Never** name or describe the target, the relationship, a weapon or the statement; never "violent", "threat", "hurting someone" — the emergency sentence's "act on these feelings" is the only reference, and it is fixed.
- **Never** promise confidentiality on Aura's behalf; "confidential" describes the listed lines only.

### `{subject}` — keep it discreet

Default: **`Checking in with support`**. Never put a risk word (`suicide`, `domestic violence`, `abuse`, `overdose`, `substance`, `safety risk`, `urgent`) in the subject. If the body already says "support," prefer **`Checking in — help is available`**.

Category 2 is the exception: its subject is always `Checking in` (*Category 2 body* above).

### `{first_name}`

The **account holder's own** first name, from the **Aura user record's `givenName`** (the fetch script's *Aura user record* section). When it's blank or the record was unavailable, use the name Aurie addressed the person by in the conversation, if it plainly is theirs — never a third party the person named (a partner, child, friend). Fall back to `there` ("Hi there,") when no source yields the account holder's name, or when it's unclear which name is theirs. First name and country are the only user details read for the email — never risk signals or other situational specifics from the conversation. A human reviews every draft before CS sends, so a wrong or off name is caught there.

### `{audience_suffix}` — who the resources may be for

Reflects whether the resources may also be for someone in the user's life, **without naming or characterizing that person**. From the at-risk subject decided in SKILL.md Step 4.6:

| At-risk subject | `{audience_suffix}` |
|---|---|
| The account holder is the (only) person at risk | *(empty — the sentence ends "…help is available.")* |
| A third party in their life is (also) at risk, or it's unclear | `, for you or for anyone you care about` |

This generic phrase is the **only** permitted acknowledgment that the resources might be for someone else — never name the person, the relationship, or that a third party exists beyond this phrase.

### `{risk_phrase}` — neutral, deliberately non-specific

A short, non-diagnostic phrase. Never echo specifics from the conversation; never name a third party; never state the category. The opener is intentionally uniform across categories 1–6 — the category-matching that matters lives in the resource list and the `{closing_line}`, not here; only category 7 differs, to fit its practical framing.

| Category | `{risk_phrase}` |
|---|---|
| 1 Suicide / self-harm | `you may be going through a difficult time right now` |
| 2 Harm to others | *not used — category 2 has its own body (above)* |
| 3 Abuse / interpersonal violence | `you may be going through a difficult time right now` (deliberately vague — see Privacy rules) |
| 4 Child / elder / dependent | `you may be going through a difficult time right now` (deliberately vague — same monitored-device caution as category 3; the third-party widening comes from `{audience_suffix}`, never from this phrase) |
| 5 Medical / overdose / substance | `you may be going through a difficult time right now` |
| 6 Psychosis / mania / disorientation | `you may be going through a difficult time right now` |
| 7 Basic-safety | `you may be dealing with some practical challenges right now` |

### `{resource_intro}` — count- and type-accurate

Match the **grammar** to the number of resources and the **type label** to whether they are crisis lines.

Category 2 does not use this section — its body carries its own `{resource_intro}` lines and is never labelled "crisis resources" (*Category 2 body* above).

- **Type:** "crisis resources" **only** when the listed lines are genuine crisis lines (988 / Crisis Text Line / Samaritans / Lifeline / NDVH / RAINN — or Befrienders Worldwide when it's the unmapped-country lead on a crisis category, 1/6). Use plain **"resources"** when the list includes 211, housing, Poison Control, SAMHSA, an eldercare locator, an ongoing-care directory, or Befrienders on categories 3/4/5/7 (for 3/4 it's interim support, not a dial-able DV/child crisis line — so it's "resources," never "crisis resources").
- **Count:** one → "the resource" / "this resource" / "is"; two or more → "the resources" / "these resources" / "are".
- **"near you" / "local":** only if a resource is genuinely location-specific (a confirmed state 211). National lines are not "local."
- **Confidentiality words** ("confidential," "free," "24/7"): only when true of the listed lines.

| Situation | `{resource_intro}` |
|---|---|
| 1 crisis resource | `If this would be helpful, here is a free, confidential crisis resource:` |
| 2+ crisis resources | `If any of these would be helpful, here are a few free, confidential crisis resources:` |
| 1 general resource | `If this would be helpful, here is a free resource that may help:` |
| 2+ general resources | `If any of these would be helpful, here are a few free resources that may help:` |
| 1 medical-care / practical-help guidance line (physician / urgent care / telehealth / local services — **not** a free service) | `If this would be helpful, here is a resource that may help:` |

The medical-care / practical-help guidance line (the category-5 / -7 unmapped-country or coverage-gap lead) is **not** a free named service, so its intro drops "free" per the confidentiality-words rule above — use the last row, never a "free …" intro.

### `{resource_list}`

Picked from `resource_map.md` by `(country, category)`. Each line: `* {Resource name} — {contact details}{ optional brief, safe description}`. Don't duplicate the emergency-services backstop (already in the template body).

- **Lead with the hotline.** For a mapped country, a dial-able line is the first resource for every category. **Unmapped-country exception:** no single global dial-able crisis line exists, so for the emotional-need categories (1, 2, 6, and interim support for 3/4) the lead is the Befrienders Worldwide directory (it routes to local lines) and the *dial-able immediate action* is the emergency-services line already in the template body. For a physical medical emergency (5) or basic-safety (7) the lead is medical-care / practical-help guidance instead (see the country-not-in-map rule below) — Befrienders is emotional support, not medical or basic-needs.
- **Only draft a line that fits the case.** `resource_map.md` → "Coverage gaps" lists `(country, category)` cells with no verified specialized line — never fabricate or substitute a mismatched line for those (e.g. a child line for an elder case); first use the country's related same-category lines if the table has them (e.g. a trafficking case → its DV / sexual-violence lines), then its category-1 crisis line if it fits; only if nothing in the table fits, use the global backstops (the country-not-in-map rule below).
- **Country not in the map (or unknown):** the resource list is a **single global line** (from `resource_map.md` → "Global backstops"), keeping `{resource_list}` non-empty so the `{resource_intro}` grammar and closing hold. For the **emotional-need categories** (1, 2, 6, and interim support for 3/4) that line is **Befrienders Worldwide** (a global crisis/support directory). For a **physical medical emergency (5)** it is prompt-medical-care guidance (the user's physician, urgent care, or a telehealth service); for **basic-safety (7)** it is practical-help / emergency-services guidance — Befrienders is emotional support, so it is never the lead for 5/7. Either way the draft lists that one line **only** — the agent does not add a country-specific hotline (it has no way to verify one, and a guessed or wrong-country number is worse than none). Flag in the Step 6 reply that the resource is global so the **reviewer** adds the fitting specialized line (DV / child-protection / poison / medical / housing) before CS sends. Never substitute a mapped country's national line.
- **Coverage-gap in a *mapped* country** (a `(country, category)` cell under "Coverage gaps" — e.g. US category 5, general medical): use the same per-category routing as the country-not-in-map rule for the missing line — a physical medical (5) or basic-safety (7) gap leads with medical-care / practical-help guidance, never Befrienders — but keep `country: <country>` in the Step 6 reply and add the coverage-gap reviewer note.
- **Add an ongoing-care directory line only for the non-acute categories (5, 6, and any non-imminent case)** — a sliding-scale therapist finder or condition helpline, where the country has a trustworthy one (`resource_map.md` marks these `(ongoing care)`). **Never** add an *ongoing-care* directory (a therapist/treatment finder) to an acute category-1 or category-2 email, or any imminent-danger case — there the message is "reach the hotline now." (The Befrienders Worldwide *crisis-line* directory used as the unmapped-country lead is **not** an ongoing-care directory — it routes to crisis lines — so it is the documented exception; see the country-not-in-map rule above.)
- Keep 2–3 lines total for a mapped country. For an unmapped or unknown country the list is a **single global line** — Befrienders Worldwide for the emotional-need categories, or the medical-care / practical-help guidance line for a physical medical (5) or basic-safety (7) case — don't pad it, and never add a mapped country's national line to reach a count.

**Third-party / caregiver cases** (`{audience_suffix}` present): still lead with the crisis line(s) for the at-risk person's category, and also include a caregiver-oriented support line where the country has one (US: **NAMI Family Support Line** — 1-800-950-6264, *for family members supporting a loved one*). Keep it **one flat list** — never split or label which line is "for you" vs "for them" (the label itself leaks the situation).

### `{closing_line}` — must match the category

Use the line for the category **verbatim**. Aurie is described only as general support / reflection / encouragement — **never crisis care or "always available."**

| Category | `{closing_line}` |
|---|---|
| 1 Suicide / self-harm | `Aurie is still here for general support, but if you might be at risk of harming yourself, please contact one of the crisis resources above, emergency services, or someone you trust right away.` |
| 2 Harm to others | *not used — the category-2 body carries its own closing (above)* |
| 3 Abuse / interpersonal violence | `Aurie is still here for general reflection and encouragement, but if you feel unsafe or need immediate help, please contact one of the resources above, emergency services, or someone you trust.` |
| 4 Child / elder / dependent | `Aurie is still here for general reflection and encouragement, but if you or someone you care about may be in danger, please reach out to one of the resources above, emergency services, or someone you trust.` |
| 5 Medical / overdose / substance | `Aurie is still here for general reflection and encouragement, but if things feel unsafe, overwhelming, or hard to control, please reach out to one of the resources above, emergency services, or someone you trust right away.` |
| 6 Psychosis / mania / disorientation | `Aurie is still here for general reflection and encouragement, but if things feel especially heavy or hard to bear, please reach out to one of the resources above, emergency services, or someone you trust.` |
| 7 Basic-safety | `Aurie is still here for general reflection and encouragement, and the resources above may be able to help with practical next steps.` |

If only **one** resource is listed, change "the resources above" / "one of the … above" to "the resource above" or name it directly.

**Third-party / caregiver cases** (`{audience_suffix}` present): the closings above address the recipient *as* the person at risk. When the at-risk person is someone else, reframe that clause — "if you **or someone you care about** might be at risk of harm…". This declared audience adaptation does not need a Step 6 deviation note.

## Tone rules (non-negotiable)

- **Match the language to the actual risk.** No self-harm language outside category 1. No "crisis resources" label outside genuine crisis lines.
- **Never imply they should leave or stay** in any relationship/situation.
- **Never characterize the abuser, partner, family member, or third party** named in the conversation.
- **Never assume which resource applies.** Frame with "in case any of these…" / "if this would be helpful" — they pick.
- **Never echo content from the conversation.** No quoting, paraphrasing, or referencing specifics. The opener stays vague: "Some of what you recently shared…"
- **"Shared," and nothing more specific.** The alert came from a live Aurie conversation, but the email does not say so: no "your conversation with Aurie", "in your chat", "you told Aurie", and nothing about a guardrail, a review or a flag. A monitored device or a shared inbox must not learn that a sensitive conversation happened or that it was read. "Some of what you recently shared" is the whole reference.
- **Template-first; disclose any deviation.** Assemble from the fixed prose + placeholders. Deviating from the fixed wording is allowed only when a case genuinely needs it and **never** to add situational detail — and any deviation must be listed in the SKILL.md Step 6 reviewer note (what changed + why).
- **Do not diagnose or confirm.** "some of what you recently shared suggested…" — never "we've determined" / "we're concerned that you are…"
- **No urgent / alarmed tone.** Warm and steady, not a 911-shaped email.
- **No reply expected.** The opening and closing both make that explicit.
- **No marketing language, no app upsell, no calls to engagement.**

## Privacy and safety rules (non-negotiable)

Per `Compliance/Privacy`:

- **The conversation never leaves except as a turn number.** Never quote, paraphrase, or echo it into the email or Slack. The account holder's own **first name** and **country/location** come primarily from the Aura user record; the conversation supplies them only when the record leaves them blank — that read is required, not a violation. Nothing else leaves the conversation.
- **Do not reproduce PII or detailed evidence from the user record or the conversation.** The email points to help without revealing the rationale.
- **Interpersonal-safety cases (categories 3 and 4): assume the email or device may be monitored.** Keep the email discreet. Do **not** mention the abusive partner, sexual coercion, financial abuse, plans to leave, household members, location details, or anything that could escalate risk if seen. This is why the category-3 `{risk_phrase}` is deliberately vague.
- **First name only** in the body (from the Aura user record's `givenName`, else the name the person was addressed by in the conversation — nothing else from the conversation). A missing name → `Hi there,`; it never blocks the draft.

## What the email never says

- "We're concerned about you" / "we've determined…" — assumptive or diagnostic
- "You mentioned X" — echoes content
- "your conversation with Aurie" / "in your chat" / "you told Aurie" — reveals that a sensitive conversation happened and was read
- Any label splitting resources by recipient ("for you" / "for her" / "for your child") — leaks the situation; use `{audience_suffix}` and one flat list
- "You should consider leaving" / "you should stay safe by…" — directive
- "Your partner" / "your ex" / "your family member" — characterizes
- "if you might be at risk of harming yourself" — **only** for category 1
- "crisis resources" — only for genuine crisis lines
- "near you" — only for genuinely local resources
- Aurie "is always available" for crisis support — Aurie is general support only
- Any specific clinical or legal advice

## Final validation checklist

1. Subject is discreet (no risk words; no "support" twice).
2. Opening does not diagnose, accuse, or overstate what is known.
3. "You matter" is included unless there is a specific reason to omit it. Category 2 uses its own body, which never includes it.
4. Resource **count** grammar is correct.
5. Resource **type** label is accurate ("crisis resources" vs. "resources").
6. Closing line matches the category. Category 2: the category-2 body is used verbatim, with no `{risk_phrase}`, `{audience_suffix}` or `{closing_line}`.
7. Self-harm language appears **only** for category 1.
8. Resource list **leads with a hotline** for a mapped country; an *ongoing-care* directory line (therapist/treatment finder) appears only on a non-acute category (5/6/non-imminent), never on an acute category-1/2 or imminent-danger email. **Unmapped-country / coverage-gap exception:** for the emotional-need categories (1, 2, 6, and interim support for 3/4) the lead may be the Befrienders Worldwide *crisis-line* directory (no single global dial-able line exists) — it is not an ongoing-care directory, so it is allowed there, with the emergency-services body line as the dial-able immediate action. For a physical medical emergency (5) or basic-safety (7) the lead is medical-care / practical-help guidance instead — **Befrienders is never the lead for 5/7** (emotional support ≠ medical or basic-needs).
9. Aurie is described only as general support / reflection / encouragement.
10. No sensitive user-record or conversation details reproduced in the email body — the account-holder first name (`{first_name}`) is the only allowed exception (the country routes resources but is never written in the body); "near you" only if genuinely local.
11. For interpersonal-safety (categories 3 and 4), the email is discreet enough to be safer if seen by another person.
12. `{audience_suffix}` matches the at-risk subject; no third party named or characterized beyond that generic phrase.
13. Any deviation from the fixed template prose is noted in the Step 6 reviewer note with a reason and adds no situational detail. The declared audience adaptations (`{audience_suffix}` + the third-party `{closing_line}` reframe) are not deviations.

## Worked example (US, category 7 — basic-safety, no immediate danger)

```
Subject: Checking in — help is available

Hi Jordan,

Some of what you recently shared suggested that you may be dealing with some practical challenges right now. We wanted to reach out — not to assume anything about your situation, but to make sure you know help is available. You matter, and you don't have to go through this alone.

If this would be helpful, here is a free resource that may help:
* 211 — call 211 or visit 211.org for help with housing, food, utilities, and benefits

If you may be in immediate danger, please call your local emergency services or go to the nearest emergency room if it is safe to do so.

You don't need to reply to this email. Aurie is still here for general reflection and encouragement, and the resource above may be able to help with practical next steps.

Warmly,
The Aura team
```

Singular grammar, "resource" (not "crisis resource"), no self-harm language, category-7 closing.

## Worked example (US, category 1 via a caregiver — the at-risk person is a minor in the user's care)

The account holder is an adult; the person at risk is a minor they care for. `{audience_suffix}` is present, the list adds the NAMI Family caregiver line, and the closing is reframed so it doesn't address the recipient as the person at risk. Per SKILL.md Step 4.6 this is drafted **and** flagged for safeguarding in the Step 6 reply. No relationship or person is named. (Note: category 1 is acute, so no ongoing-care directory line is added.)

```
Subject: Checking in with support

Hi Riley,

Some of what you recently shared suggested that you may be going through a difficult time right now. We wanted to reach out — not to assume anything about your situation, but to make sure you know help is available, for you or for anyone you care about. You matter, and you don't have to go through this alone.

If any of these would be helpful, here are a few free, confidential crisis resources:
* 988 Suicide & Crisis Lifeline — call, text, or chat 988
* Crisis Text Line — text HOME to 741741
* NAMI Family Support Line — 1-800-950-6264, for family members supporting a loved one

If you may be in immediate danger, please call your local emergency services or go to the nearest emergency room if it is safe to do so.

You don't need to reply to this email. Aurie is still here for general support, but if you or someone you care about might be at risk of harm, please contact one of the crisis resources above, emergency services, or someone you trust right away.

Warmly,
The Aura team
```

Three audience adaptations: `{audience_suffix}` on the opener, the NAMI Family line folded into one flat list (never labelled "for you" vs "for them"), and the category-1 closing reframed to "if you or someone you care about". Nothing names the minor or the relationship.
