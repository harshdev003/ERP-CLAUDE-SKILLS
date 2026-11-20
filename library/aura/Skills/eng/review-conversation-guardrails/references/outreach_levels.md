# Outreach levels — who is told, and whether an email is drafted

Every **active-risk** case (classification outcome A) gets exactly one of three levels. The level decides whether a CS email is drafted and whether Diana, the guardrail reviewer, is tagged. It never changes the classification, the crisis-reply check or the Slack content rule.

This file is shared, byte for byte, by `eng:investigate-conversation-guardrail` (which assigns the level) and `eng:review-conversation-guardrails` (which applies the same rules when Diana decides send or no send). The triage skill's copy is canonical; see `docs/canonical-sources.md`.

Customer Support is never tagged by the triage. CS is tagged only when the reviewer approves an email.

| Level | Draft? | Who is tagged | Reply |
|---|---|---|---|
| **1 — High priority** | Yes | Diana, with 🚨 HIGH PRIORITY | the 🚨 draft reply, then the high-priority tag on the same thread |
| **2 — Daily review** | Yes | Diana, "for review" | the 🚨 draft reply, then the review tag on the same thread |
| **3 — No email** | No | Nobody | ☑️ resources already in place, no immediate danger |

Decide in order: level 1 first, then level 3 (any of its four routes below: not current, covered by a recent email or a waiting draft, the right lines given, or already in care), and anything that is neither is level 2. **When unsure between two levels, take the one that does more.** A missing email cannot be noticed; an extra one is caught at review.

## Level 1 — High priority

Any of these, anywhere in the conversation:

- **Wants to act now** to hurt themselves or someone else: intent now, a plan, means or a time ("tonight"), or an attempt in progress.
- **Scared of someone right now**, or in danger right now: afraid of their partner now, being stalked now, being held or trafficked.
- **Overdose or poisoning happening now**, or a medical emergency now.
- **Stranded or without shelter, water or essential medication right now.**
- **A minor**: the account holder is under 18, says so, or appears to be (school or grade context, "my mom won't let me"). Suspected counts.
- **A child, elder or dependent at risk** (category 4) — the child, elder or dependent is the one in danger — including an adult worried about a child in their care. A child who is the one being violent, in a family already in professional care or with police involved, is judged like any other case: levels 2 or 3 by the rules below.

## Level 3 — No email

### Not current

**Current** = happening now, ongoing, or the person is still in it. A past event is still current when the person is very upset by it **now**: flashbacks, nightmares or panic attacks this week; "I can't stop thinking about it"; not sleeping, working or eating; crying or overwhelmed in the conversation itself; or reaching out *because* it is hitting them now.

**Not current → level 3:**

- plainly resolved history, said calmly ("that was years ago, I'm in a good place now")
- contact with the person harming them has fully ended **and** there is no current distress
- read in context, it was never risk at all: a quote, a joke, someone else's story told calmly
- the only one harmed or threatened is an animal

Unsure whether it is current → it is current.

### The right lines were given

Only when **every** one of these holds:

1. **The right lines were given in the chat.** For each topic the person raised, Aurie gave that topic's line for their country from `resource_map.md`: a DV case needs the domestic violence hotline, sexual violence needs RAINN (or the country's equivalent), both topics need both. A partner pressuring for sex after a no is both topics: it needs the DV hotline **and** RAINN. Abuse with no line of its own in the map (for example, an ex refusing to delete intimate images) is covered by the DV hotline. A line for another topic does not count (988 alone for a DV case), a line for another country does not count, and "talk to a professional" does not count. Unmapped country: the global backstop the map names counts.
2. **Not level 1.**
3. **Not on the always-email list below.**
4. **The risk did not come back worse after the line was given.** 988 at turn 10 and the wish again at turn 30 is not level 3; nor is a new danger raised after the line. Continuing to talk about the same situation is not the risk coming back.
5. **Nothing heightened**: no new plan or means, no timing words, and they did not say the help they had is not working.
6. **The transcript is complete.** A `TRANSCRIPT INCOMPLETE` read is never level 3.

**An unanswered safety question** ("are you safe right now?" deflected or skipped) does not by itself block level 3 when they keep talking normally and already have the line. It does when the conversation suggests they **cannot talk freely**: they say the person is in the room or nearby, or "I can't talk"; the conversation stops abruptly right after the question; or it comes with a danger sign or fear right now. Then it is level 2, or level 1 when it looks immediate.

### Always an email (level 2 at least), even when the right lines were given

- **Harm to others** (category 2), unless **all** of these hold: the act or threat is in the past, they express no intent to hurt anyone now, they no longer live with the person they hurt or threatened, and the chat gave the country's harm-to-others line from `resource_map.md` (row 2, or row 2a when that person is a partner, ex or family member). Anyone who says they want to hurt someone now is level 1 or 2, never 3.
- **Active suicidal thoughts that are recent or ongoing** ("I've been very suicidal for two weeks, but I won't act today"). Not acting today is not the same as safe. **Except** when **all** of these hold: they say the thoughts are not present now; there is no plan, means (a named method counts, including a gun whose access was never answered) or timing anywhere in the conversation; and the chat gave the country's suicide crisis line from `resource_map.md` — that line is their way to reach a human. Then it is level 3, whether or not a support person or care is mentioned.
- **Recent self-harm, or urges now.**
- **A recent sexual assault** (days or weeks ago), even when they are safe now. There can be time-sensitive medical care an email can point to.
- **Recent violence with a danger sign**: choking or strangulation, a weapon, or a threat to kill.
- **Psychosis, mania or disorientation** (category 6), unless the person is in active professional care for it and shows no risk of harming themselves (*Already in professional care* below). Someone disoriented may not be able to use a line they were given.

### What level 3 looks like, by topic

| Topic | No email when the right lines were given and… |
|---|---|
| Suicide / self-harm | a passive wish ("sometimes I wish I wouldn't wake up") they deny acting on; self-harm or suicidal thoughts in the past, none now; or thoughts that are not present now, with no plan or means and 988 given (for example, said yes to thoughts of ending their life, then no to having them right now) |
| Abuse / domestic violence | they are not in immediate danger and have the DV hotline — whether it was one incident or a pattern, and whether or not they are still in contact (for example, a partner grabbed them once; back in contact with a partner who was violent before, no danger now; an ex refusing to delete intimate images). Not when there is a danger sign or a recent sexual assault (always an email), or they are scared of the person right now or being stalked now (level 1) |
| Sexual violence | an assault in the past (months or years ago) they are working through |
| Substance | cravings, relapse or struggling with use, with no overdose now |
| Harm to others | a past act or threat, no intent now, no longer living with the person (for example, fired a gun in the air during a fight, has since moved out and is sober; DV hotline given) |
| Basic needs | money, housing or food worries, not stranded or without essentials right now |
| Psychosis / mania | in active care for it, and no risk of harming themselves (no lines needed — see below) |

### Already in professional care

A person in active professional care **for this exact matter** may also be level 3 without lines in the chat, but only for categories 3, 5, 6 and 7, and only when conditions 2–6 above hold (the psychosis item on the always-email list carries this exception). For category 6 (psychosis, mania, disorientation) there must also be no risk of harming themselves anywhere in the conversation. Care never stands in for a line on suicide or self-harm, harm to others, a minor or a child or elder: those people are owed a way to reach a human now.

### Covered by a recent email or a waiting draft

A case that is not level 1 is also level 3 when nothing is new or worse since an earlier draft for the **same person** (either guardrail channel) and that draft is either:

- **sent in the last 14 days** — marked sent (✅) by a reviewer, or
- **still waiting for review** — no send, no-send or escalation reply on its thread yet. The reviewer decides that one, and this alert is folded into it.

Nothing new or worse means: the same topic, no new plan, means or timing words, no new kind of risk, and they did not say the help is not working. The always-email list does not override this: the person already has, or is about to get, the email. Anything new or worse is judged on its own, and a draft the reviewer marked no email does not count.

## Level 2 — Daily review

Every other active-risk case: a draft, and Diana is tagged for her daily review. That includes every case where the right lines were **not** given in the chat, whatever the topic.
