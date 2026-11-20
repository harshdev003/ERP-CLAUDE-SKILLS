# Classification — what did this conversation carry?

Decision logic for SKILL.md Step 4. Unlike the memory-ingestion guardrail, **there is no upstream serious-harm flag to trust here.** The handoff fires when the conversational prompt (coach / quick-processing) decides a turn matches one of its numbered guardrail sections, and it reports a routing label (`crisis`, `treatment`, `diagnosis`, `boundary`, `substance`, `complex_condition`). That label is telemetry for routing, not a risk assessment — so the classification is made here, from the **whole conversation**.

## Read the whole conversation, not the alert turn

The alert is posted on the conversation's **first** handoff only; later handoffs are never posted. A conversation that opened on a `boundary` handoff can reach a suicidal disclosure forty turns later, after the alert went out. So:

- Read **every** turn the fetch script returns, including those after the alert turn and the "Ledger turns absent from the stored transcript" section.
- Classify on the **most serious thing the person expressed at any point**, not on the first handoff's category.
- The handoff categories (one per guardrail-handled turn, from GCP) are pointers to where to look, not the answer.

## Three outcomes

### A. Active risk

The person expressed, at any point in the conversation, something that puts their safety or someone else's at stake **now** — present or ongoing, not resolved history. Map it to one of seven categories (same axis as `resource_map.md` and `email_template.md`):

| # | Category | Typical expressions in a conversation |
|---|---|---|
| 1 | Suicide or serious self-harm | wanting to die, wanting to disappear / not wake up, suicidal thoughts, a plan or means, active self-harm or urges, "I can't keep myself safe" |
| 2 | Risk of harming someone else | fear of, or intent toward, hurting another person |
| 3 | Abuse / DV / coercive control / stalking / trafficking / sexual violence | a current abusive partner or household, being followed or controlled, recent sexual violence, being trafficked or held |
| 4 | Child, elder, or dependent abuse or neglect | a child, elder or dependent being harmed or neglected now (the person may be the victim, a witness, or a caregiver) |
| 5 | Medical emergency / overdose / poisoning / dangerous intoxication or withdrawal | took too much, can't breathe, chest pain, dangerous withdrawal — happening now |
| 6 | Psychosis, mania, or severe disorientation with a safety concern | losing touch with reality *and* a safety concern attached |
| 7 | Immediate basic-safety risk | stranded, trapped, no shelter / food / water / medication; "I'm in danger" with no further detail |

**Passive death wishes count as category 1 for this skill.** The Guardrails Handler treats "I wish I could disappear" as its non-acute 8.2 path; this skill does not downgrade it. A person who said they want to die or disappear is owed a way to reach a human (`crisis_reply_check.md`), whatever path the handler took.

**Ambiguity fails toward active risk.** If you genuinely cannot tell whether something is current or historical, or whether "I don't want to be here anymore" means the room or their life, classify as active risk. Every draft is human-reviewed; a missed crisis is not. The rule covers what the conversation leaves open; it does not override what the person settles, so the cases below are not ambiguous.

#### Not active risk on their own

Each of these is **B** (or **C** when unmistakable), unless one of its exceptions applies. Read the whole conversation for the exceptions before ruling one out.

1. **Resolved past.** Violence, abuse or suicidal thoughts that the person describes as over — the relationship has ended or there is no contact, the feelings passed, they say they are safe now — with nothing current anywhere in the conversation. Writing "historical" or "no current threat" in the Risk line and then classifying A is a contradiction: pick one.
   *Still A when:* the person still lives with, is in contact with, or shares custody exchanges with the person who harmed them; there are current threats, stalking or fear of that person; a suicide attempt, plan or means falls within the last month; or the danger is described as having recurred before and the conditions for it are back.
2. **Clear denial of an ambiguous statement.** The person said something that could mean wanting to die or being in danger ("I don't want this life anymore", "I don't want to keep doing any of this") and then plainly said it did not mean that ("I'm not suicidal", "no, nothing like that", "I didn't mean actually die") — in reply to Aurie's safety question or unprompted.
   *Still A when:* the first statement was not ambiguous (an explicit wish to die, to kill themselves, or to hurt someone); the person mentioned a plan, means, intent or self-harm; or a later turn expresses it again. A denial settles an ambiguous statement only; it never cancels an explicit one.
3. **Speculation about someone else.** The person wonders or guesses whether a third party might be at risk ("she could take her own life, I don't know") without reporting anything that person said or did that points to current risk.
   *Still A when:* the third party said they want to die, disappear or hurt someone, wrote a note, self-harmed, made an attempt, or is being harmed now; or the third party is a child or dependent in the person's care. A reported statement counts even when the person thinks it was a joke.
4. **Relationship conflict without danger.** Arguments, yelling, cursing, name-calling, belittling, defensiveness, infidelity, a breakup, or a dispute over divorce or custody through lawyers or courts — between partners, or a parent or caregiver who yells at or speaks harshly to a child — where the person describes no fear for their safety and nothing below. Painful and worth support, but it is a relationship topic, not category 3 or 4. "Classified toward risk per rubric" is not a reason on its own: name the danger signal or classify B.
   *Still A when:* the person is afraid of the other person, or says an argument has frightened them; there is physical violence, or a threat of it, while they are still together or in contact; there are threats to harm them, their children or pets, or to take or hide a child outside the courts; or there is control — monitoring their phone or location, deciding who they may see or talk to, stopping them from leaving or ending a conversation, controlling their money, breaking things to intimidate, or sexual pressure after a no. For a child: hitting, injuries, neglect, or the person fearing the child will be physically hurt.

### B. Non-crisis guardrail

The handoff was for a treatment, diagnosis, boundary, substance or complex-condition question and **nothing in the whole conversation** carries a current danger to self or others. Examples: asking whether to change an SSRI dose, asking Aurie to diagnose ADHD, sexual-content boundary, asking about drinking with no danger signal, describing a chronic condition.

### C. False positive

The handoff fired on something that, read in context, carried no guardrail condition at all — a quote, a song lyric, a book or film plot, a joke the person immediately clarified, or plainly historical material ("I was suicidal in college; I'm in a good place now") with nothing current anywhere in the conversation.

Use C only when it is **unmistakable**. Anything in doubt is B (if the topic was a guardrail topic but not dangerous) or A (if danger is plausible).

## Resources already in place — decides the level, never the check

Whether an active-risk case gets an email is [`outreach_levels.md`](outreach_levels.md)'s call: the right lines given in the chat, or active professional care for the exact matter in categories 3, 5, 6 and 7, can make it level 3 (no email). Neither ever changes the crisis-reply check: whether Aurie gave a way to reach a human is a question about Aurie's replies, not about the person's support network.

## Minors

If the person appears to be under 18 (self-identified age, school/grade context, "my mom won't let me"), classify exactly as above, then the case is **always level 1** (`outreach_levels.md`): draft, attach the minor reviewer flag (SKILL.md Step 6), and tag Diana as high priority — never level 3, even when the right lines were given. A minor referenced in the third person (an adult worried about their child) is a caregiver case, drafted with the third-party framing and the safeguarding flag: level 1 when the child is the one in danger; when the child is the one being violent and the family is already in care or the police are involved, the level follows `outreach_levels.md` like any other case.

## At-risk subject

Decide who the resources are for — the person (empty `{audience_suffix}`), a third party in their life (`, for you or for anyone you care about` + the country's caregiver line), or unclear (treat as third-party). Never name or characterise the third party.
