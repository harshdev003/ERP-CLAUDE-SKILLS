# Reviewing a conversation guardrail draft — send or don't send

**Purpose: make sure people in crisis receive information about how to get support from a human.** Aurie is not a crisis service. When someone in a conversation is at risk, they need a way to reach a real person — a crisis line, a hotline, emergency services — and this review makes sure they get one, by email when the conversation did not give it to them.

**When unsure, send it to Talia to triage.**

The rules a human reviewer applies to each triage reply in `#guardrails-conversation` before Customer Support sends anything. Written to train a new reviewer and applied by `eng:review-conversation-guardrails` when it walks someone through the queue.

The triage reply (from `eng:investigate-conversation-guardrail`) already did the heavy lifting: it read the whole conversation, picked a category, checked whether Aurie gave the person a way to reach a human, and drafted an email for the person's country. **Your job is the judgement call it cannot make on its own: is outreach right for this person, right now, and is the email right?**

## Who does what

| Check | Who | How |
|---|---|---|
| Is the risk current? | **You** | Read the key turns yourself — never decide from the summary line |
| When did they last get an email? | **You** | The `📧 Last CS email` line, then open its thread |
| Does the draft carry every resource this topic needs? | **You** | The topic triage below |
| Are the resources right for the person's **country**? | **Claude** | Checks the draft against `resource_map.md` for the country on the UID line, and flags any line from the wrong country |
| Did Aurie give them a way to reach a human in the conversation? | **Claude** shows it, **you** confirm | Crisis-reply check (a) on the triage reply, plus the key turns. Decides step 3. A 🛑 FAIL is also a product finding |

## Which triage replies need you

| Triage reply | What you do |
|---|---|
| 🚨 **active risk** with a draft, tagged to you **for review** (level 2) | Review it — every one |
| 🚨 **HIGH PRIORITY**, tagged to you (level 1) | Review it **first**, before anything else in the queue |
| ☑️ resources already in place, no immediate danger (level 3) | No email. Nothing to do |
| ✅ no active risk | No email. Nothing to do |
| ⚠️ not analysable | Send to Talia — there was no transcript to judge |

## The five steps

### 1. Is the risk current?

The triage already applies this test (`outreach_levels.md` → *Not current*): a case that is not current is posted as ☑️ and never reaches you. Read the key turns yourself anyway — if a case that reaches you turns out not to be current by that test, it is a `no send`.

**Unsure → send it to Talia to triage.**

### 2. When did they last get an email?

The triage already applies this too (`outreach_levels.md` → *Covered by a recent email or a waiting draft*): when the person was emailed in the last 14 days, or already has a draft waiting for you, and nothing is new or worse, the alert is posted as ☑️ covered and never reaches you.

For a case that does reach you, read the `📧 Last CS email` line and open the thread it links to:

- **New or heightened** since that email or draft — a different topic, or the same topic but worse (each topic triage below says what "heightened" means) → go to step 3.
- **Another draft for this person is still waiting** → decide that one first; don't send two.
- **Emailed (✅) in the last 14 days and nothing new or worse** → `no send` (the triage should have caught it).

### 3. Did they get a way to reach a human in the chat?

This is the heart of the review. The email exists to give a person in crisis a way to reach a human; if the conversation already did that, the email usually adds nothing.

**Send** when any of these is true:

- **The right line wasn't given in the chat** — for at least one topic they raised, the chat never gave that topic's line for their country (`outreach_levels.md` → *The right lines were given*, condition 1). This covers no line at all (crisis-reply check (a) FAILED) and a line for the wrong topic: 911 or 988 alone for a domestic violence case, a DV hotline with no RAINN for a partner pressuring for sex. "See a licensed professional" doesn't count, and neither does a US number for someone outside the US.
- **The case always gets an email** — it is level 1 (HIGH PRIORITY), or it is on the *Always an email* list in [`outreach_levels.md`](outreach_levels.md). The triage reply's `Level:` line names which; confirm it against the key turns.
- **The risk came back worse after the line was given** — Aurie offered 988 at turn 10, and at turn 30 they said it again, or raised a new danger. Continuing to talk about the same situation is not the risk coming back.

**No email** when the case meets level 3 in `outreach_levels.md`: the right lines for its topic and country were given in the chat and nothing above applies. The triage posts most of these as ☑️, so they never reach you; a draft that does and turns out to meet level 3 is a `no send`.

The passive-wish case in the suicide triage below is a worked example of this step.

### 4. Does the draft have every resource this topic needs?

Use the topic triage below — the step 3 send rule decides *whether* to send; this step checks *what* is sent. Read the draft's resource list against the "Relevant resources" for the topic.

- Something relevant is missing (e.g. RAINN when there was sexual violence) → **correct the draft**.
- Something doesn't fit (e.g. Poison Control with no overdose or poisoning) → **correct the draft**.
- Two topics in one conversation → the email can carry a line from each, 2–3 lines total.
- Keep a resource in the email even if Aurie already gave it in the conversation — the email is what they keep.

The resource lists below are the **US** lines. For any other country Claude checks the draft against the resource map — never send a US number to someone outside the US.

**Correcting a draft.** You can add, remove or swap resource lines, fix the grammar, and reword where the draft needs it, **as long as the result still passes the email template.** The rules that decide are in [`email_template.md`](email_template.md), the same template the triage skill drafted from: its fixed wording, *Tone rules*, *Privacy and safety rules*, *What the email never says* and *Final validation checklist*. Claude checks each corrected draft against all of them before showing it to you and won't post one that fails. If what you want can't be written that way, send it to Talia. (Where the template says "SKILL.md Step 4" or "Step 6", it means the triage skill's steps, which are already done by the time a draft reaches you.) For checklist item 13, the deviation note for a change made here is the `(<what changed>)` in the corrected-draft post (step 5): name every change from the template's fixed wording there in categorical terms ("softened the opening", "added RAINN"), never anything the person said.

#### What to watch for — a summary for learning

This list is to help you spot problems quickly. **It is not the rules** — when it and `email_template.md` differ, the template decides.

- Nothing about what they shared beyond the template's own opening sentence.
- No mention of a chat, of Aurie being told anything, or of anything being reviewed or flagged.
- The emergency line stays word for word.
- Aurie is general support only, never crisis care.
- A discreet subject line.
- Wording that matches the risk: self-harm wording only for suicide / self-harm, "crisis resources" only for real crisis lines.
- Offer the resources; don't tell them what to do.
- "You don't need to reply to this email." stays.
- A harm-to-others email keeps its own wording.

### 5. Tag Customer Support

Reply on the alert thread with **exactly one** of these, starting with the `@cs` group tag (`<!subteam^S0AJ2TR6DB3>`):

- `Reviewer approved the draft email above. Please send it as written.`
- `Reviewer update — corrected draft (<what changed>):` followed by the full corrected email, then the approval line above as a separate reply
- `No email needed here.`

Unsure at any step → instead tag Talia (`<@U0C6BHV1E58>`): `Reviewer unsure — please triage.` No `@cs` tag, so nothing goes out until Talia decides.

Never put anything the person said into Slack — turn numbers and categories only.

---

## When someone else is at risk

The account holder is talking about a family member, partner, friend or child. The triage reply marks these (`subject: other`, or `(third party)` on the category). Applies to every topic below.

**1. Is the other person's risk current?** Same step 1 test, applied to them. A son who attempted last month is current; a sister who struggled years ago and is fine now is not.

**2. Does the account holder get an email?** Only when one of these is true:

- **They are responsible for the person** — a parent of the child, a caregiver, someone the person lives with and depends on
- **They are very distressed themselves** — the step 1 signs (can't cope, can't sleep, overwhelmed in the conversation)

Otherwise → **No email.** A calm supporter asking how to help, who isn't responsible for the person, is not someone at risk.

**3. Check the account holder's own risk too.** If they are in danger themselves — a partner who is "a different person" when drinking, a household member being violent — run their own topic triage. That is their risk, not only the other person's.

**4. Resources** go to the account holder *as the supporter*:

- a support line for them: **NAMI Family Support Line** (1-800-950-6264), or **SAMHSA** (1-800-662-4357) when it is about someone's substance use
- plus the at-risk person's line for the topic (988, the DV hotline, Childhelp…) so they can pass it on

The email never names or describes the other person — only "for you or for anyone you care about". These caregiver lines are US. Outside the US, Claude checks what the resource map has.

**5. A child in danger** always gets the safeguarding route, whatever you decide about the email.

---

## Suicide / self-harm triage

**Look for:**

- wanting to die, disappear, or "not wake up"; "everyone would be better off without me"
- suicidal thoughts — now, or coming back
- a plan, a means, or a time
- self-harm (cutting, burning, hitting) or urges to
- "I can't keep myself safe"
- a recent attempt

**Not current:** "I was suicidal in college, I'm okay now", said calmly, with nothing else anywhere in the conversation.

**Passive wish, denied intent.** The person wishes they could die or disappear, but when asked they deny they would harm themselves. Not on the always-email list, so step 3 turns on **whether they were given a line in the chat**:

| Aurie gave a line in the chat (check (a) PASS) | No line in the chat (check (a) FAIL) |
|---|---|
| **No email.** They already have a way to reach someone. | **Send**, even when they're in therapy and denied self-harm. Nobody has given them a way to reach someone *now*, and the email is the only place they'll get it. |

The wish coming up **more than once** in the conversation pushes further toward send.

> **Worked example — sent.** For sending: she was never given a line in the chat, and the wish to die came up twice. For not sending: she's in therapy, she denied self-harm, and the wish was passive. Earlier passive cases with someone already in care were passed on, but they had been given 988 in the chat. This one hadn't, so it was sent.

**Heightened** (sends even inside 14 days):

- passive thoughts have become active ones
- a plan or means appears for the first time
- an attempt, or new self-harm
- timing words — "tonight", "this weekend", "after the kids are asleep"
- they say the help they had isn't working

**Relevant resources (US):**

- 988 Suicide & Crisis Lifeline — call, text or chat 988
- Crisis Text Line — text HOME to 741741
- When the person at risk is someone they care about: NAMI Family Support Line — 1-800-950-6264

## Relationship triage

Abuse, domestic violence, sexual violence, stalking, coercive control, trafficking.

**Look for:**

- living with, or still in contact with, the person harming them
- being afraid of a partner or family member
- being controlled — money, phone, who they can see
- being followed, tracked or watched
- recent sexual assault
- threats
- being trafficked or held

**Not current:** contact has fully ended, they are safe, and there is no current distress (step 1).

**Heightened** (sends even inside 14 days):

- new physical violence — especially **choking or strangulation**
- a threat to kill
- a weapon
- the person harming them has found them after they left
- danger rising around leaving
- children now involved

**Relevant resources (US):**

- National Domestic Violence Hotline — 1-800-799-7233, text START to 88788
- RAINN — 1-800-656-4673 — **when there is any sexual violence**
- National Human Trafficking Hotline — 1-888-373-7888 — when there is trafficking
- loveisrespect — 1-866-331-9474 — for young adults
- 988 as well when they *also* expressed suicidal thoughts

**Discretion:** assume someone else may read their email. The draft must not hint at the relationship, the person, or leaving.

## Child / elder danger or abuse triage

A child, an older adult, or a dependent being harmed or neglected. The account holder is often the worried adult, not the person at risk.

**Look for:**

- a child, elder or dependent being hit, hurt, neglected or left unsafe — now
- the account holder witnessing it, or caring for the person it is happening to
- the person harming them still has access

**Not current:** the child or elder is no longer in contact with the person harming them, and is safe. (The case where the person "cut contact with the adult concerned" was a No email for this reason.)

**Heightened** (sends even inside 14 days):

- new injury or violence
- the person harming them has regained access
- a second child or dependent now involved

**Relevant resources (US):**

- Childhelp National Child Abuse Hotline — 1-800-422-4453 — a child
- Eldercare Locator — 1-800-677-1116 (routes to Adult Protective Services) — an older adult
- NAMI Family Support Line — 1-800-950-6264 — for the adult supporting them

**Always:** the triage reply carries a ⚠️ Safeguarding line on these. Route to whoever owns safeguarding as well as deciding on the email. Never name or contact the child.

## Substance triage

**Look for:**

- using now, or a relapse
- taking too much, or mixing — especially alcohol with pills or opioids
- dangerous withdrawal — alcohol or benzodiazepines
- blackouts
- "I can't stop"
- using to cope with something else on this page
- **"a different person" on substances** — "he's a different person when he drinks", "I become a different person when I use". Applies to the account holder or someone in their life. When it's a partner or family member, also read it through the relationship triage: a person who changes when using may be the one causing harm.

**Not current:** a casual or past mention; someone in long recovery talking about it calmly.

**Heightened** (sends even inside 14 days):

- an overdose or a near-miss
- relapse after a sober stretch
- a riskier substance or way of using
- using alone
- suicidal thoughts alongside the use

**Relevant resources (US):**

- SAMHSA National Helpline — 1-800-662-4357
- Poison Control — 1-800-222-1222 — **only** when there was an overdose, poisoning or a dangerous mix
- 988 or Crisis Text Line as well when there is emotional crisis or suicidal thoughts alongside

## Other categories

Rarer. Same five steps; the triage reply names the category.

| Category | Look for | Relevant resources (US) |
|---|---|---|
| Harm to others | fear of, or intent toward, hurting someone | 988 |
| Medical emergency | acute physical symptoms happening now | Prompt-medical-care guidance (doctor, urgent care, telehealth) — no free national line exists |
| Psychosis / mania with a safety concern | losing touch with reality *and* a danger attached | 988; NAMI HelpLine 1-800-950-6264 |
| Basic safety | no shelter, food, water or medication; stranded | 211 |

## Quick card

```
0. Unsure at any step?   → tag Talia to triage
1. Current?              no (or only an animal) → No email
2. Emailed <14 days     yes → No email, unless NEW or HEIGHTENED
   or draft waiting?     (decide the waiting draft first)
3. Right line in chat?   missing for a topic · level 1 · always-email list · came back worse → Send
                         right lines given, meets level 3 → No email
4. Draft complete?       missing / wrong line → corrected draft
                         any rewording still passes email_template.md, or → Talia
5. Tag @cs               approved · corrected · no email needed
```
