---
name: creative-qa
description: "Score and evaluate ad creatives on strategy dimensions. Updates the QA section in data.json with score (1-10), target market, legal risk, hook quality, freshness, and QA notes. Does NOT review visuals — that's the creative-review skill. Use when: score creatives, update QA, rate the ads, check legal risk, assess target market, fill QA section, audit briefs. MANDATORY TRIGGERS: score, rate, QA, evaluate, assess, risk check, audit briefs."
---

# Creative QA Skill — Strategy Scoring

Evaluate every brief+creative pair on strategy dimensions and populate the QA array in `output/data.json`. This skill focuses on **copy quality, legal risk, audience fit, and strategic scoring** — not visuals (that's the creative-review skill).

---

## When to Use

- After adding new briefs/creatives to `data.json`
- When the user says "score the creatives", "update QA", "rate the ads", "fill the QA section", "check legal risk"
- Periodically to re-score after copy or strategy edits

---

## Step 0: Load the Data

1. Read `output/data.json` — parse the `BRIEFS`, `CREATIVES`, and `QA` arrays.
2. Read `CLAUDE.md` for the latest copy guidelines, ICP definitions, and risk thresholds.
3. For each Brief # in BRIEFS, find the matching CREATIVE row (join on index 1 — the Brief #).

### Knowledge Files to Load

Load these before scoring — they contain ICP definitions, banned copy, and the copy bank you'll need:

- `references/messaging/aurie/messaging.md` — **Canonical Aurie messaging spine + 7 messaging concept categories.**
- `references/brand/aurie.md` — Aurie product details, ICP1/ICP2 definitions, value props, ad guidelines
- `references/brand/aura.md` — Aura product details, social proof claims, tone
- `references/messaging/aurie/verticals-and-copy.md` — 11 emotional verticals and reusable copy bank

---

## Step 1: Evaluate Each Brief+Creative Pair

For every brief, work through each QA dimension below. Be specific and direct — no filler.

### 1A: Hook Check (QA index 3)

Does the hook match the stated `hook_type` (BRIEFS index 4)? Evaluate:

- **Tone alignment**: Does it match the brief's tone field (DARK/LIGHT)? Does it fit the app's voice (Aurie = warmer/conversational, Aura = calmer/grounded)?
- **No banned language**: Check against the banned word list: "journey," "unlock," "transform," "wellness," "self-care," "holistic," and anything that sounds like a therapy claim.
- **Visceral specificity**: Does the hook use concrete sensory details, not abstractions? ("2am" > "late at night", "locked the bathroom door" > "needed a break")
- **Scroll-stop potential**: Would this actually stop someone mid-scroll? Be honest.

Format: Start with ✓ or ✗, then 2-4 words describing the tone. Example: `✓ Confessional, vulnerable` or `✗ Generic, reads like AI copy`

### 1B: Legal / Medical Risk (QA index 4)

Scan the full creative (primary text, headline, description) for:

- **Therapy replacement claims**: Anything implying the app replaces professional therapy or medical treatment. "Aura brings CBT to your phone" is okay; "You don't need a therapist anymore" is not.
- **Medical claims**: Specific health outcome promises ("cures insomnia," "reduces cortisol"). General wellness framing is fine ("helps you wind down," "feel calmer").
- **Nervous system / trauma language**: Higher risk. Flag mechanistic claims ("rewires your brain," "heals your nervous system") vs. accessible framing ("helps you feel more regulated").
- **Testimonial substantiation**: If the ad uses stats ("8 million+ people"), flag whether these need verification. Personal narrative claims are lower risk.
- **Comparison claims**: "Better than therapy" or "unlike other apps" — these need substantiation or careful framing.

Format: `Low — [reason]`, `Medium — [specific concern]`, or `High — [specific risk that must be addressed before launch]`

### 1C: Target Audience (QA index 5)

Based on the angle, hook, and copy, identify the primary audience segment:

- **ICP1**: Quietly overloaded professionals — late-night anxiety, burnout, can't switch off. 28-42, working, likely don't identify as "needing help."
- **ICP2**: Emotionally literate seekers — already in therapy or self-aware, looking for daily tools. Interested in nervous system, inner child, somatic work.
- **Specific sub-segments**: Parents, breakup recovery, sleep-seekers, meditation-curious, therapy-cost-sensitive, overthinkers, stressed professionals.

Format: `ICP1 (specific descriptor)`, `ICP2 (specific descriptor)`, or a named segment.

### 1D: Freshness (QA index 6)

How novel is this creative in context?

- Check the `competitor_intel` field (BRIEFS index 10) — is this angle already saturated?
- Check the `status` field (BRIEFS index 14) — is this a `New` angle or a `Variation` of something proven?
- Check other briefs in the batch — are we over-indexing on any single angle or hook type?

Format: `Fresh — [what's novel]`, `Variation of [what]`, or `Saturated — [concern]`

### 1E: Score (QA index 7)

Score each creative on a 10-point scale. Be calibrated — most creatives should land 6-8. Reserve 9-10 for genuinely exceptional work.

| Score | Meaning |
|-------|---------|
| 9-10 | Exceptional — strong hook, low risk, fresh angle, high ICP match. Ship immediately. |
| 8 | Strong — minor tweaks possible but ready to test. |
| 7 | Good — solid foundation, one dimension needs work. |
| 6 | Decent — multiple small issues or one significant gap. Worth iterating. |
| 5 | Mediocre — unclear audience, weak hook, or unresolved risk. Needs rework. |
| 4- | Weak — fundamental problems. Re-brief or kill. |

Factors that raise the score: visceral hook language, low legal risk, clear ICP match, novel angle, proven script adapted to new format.

Factors that lower the score: generic/AI-sounding copy, Medium/High legal risk, unclear audience, saturated angle, banned language.

Format: `N/10`

### 1E.1: Messaging-Playbook Sanity Check

Before finalizing the score, run the creative through the 7-question heuristic from the canonical messaging playbook. A "no" on any of these should drop the score and surface in QA notes:

1. Does this feel like a true preview of the product?
2. Does it earn trust before asking for depth?
3. Is the personalization reflective rather than diagnostic?
4. Is any vulnerable material clearly necessary and carefully held?
5. Would a thoughtful Aura coach feel good promoting this?
6. Would a user feel more respected after seeing this, or more worked?
7. Is the promise honest about what the product actually is?

### 1F: QA Notes (QA index 8)

2-3 sentences max:
1. **Verdict**: One line on the creative's strongest quality.
2. **Action items**: Specific things to fix or watch. Actionable, not vague.
3. **Testing suggestion** (optional): What to A/B test or which audience to start with.

---

## Step 2: Build the QA Rows

Each QA row is a JSON array:

```
[date, brief#, app, hook_check, risk, audience, freshness, score, notes, source]
```

- **date** (index 0): Today's date `YYYY-MM-DD`
- **brief#** (index 1): Must match Brief # from BRIEFS and CREATIVES
- **app** (index 2): Copy from BRIEFS row
- **hook_check** (index 3): From Step 1A
- **risk** (index 4): From Step 1B
- **audience** (index 5): From Step 1C
- **freshness** (index 6): From Step 1D
- **score** (index 7): From Step 1E
- **notes** (index 8): From Step 1F
- **source** (index 9): Leave as `""` (populated later when ad goes live)

---

## Step 3: Update data.json

1. Open `output/data.json`.
2. Update QA rows — preserve existing rows that haven't changed, only update where brief/creative content has been modified or where QA is missing.
3. Ensure every Brief # has a corresponding QA row. No gaps.
4. Validate: QA row count must equal BRIEFS row count.

---

## Step 4: Rebuild the Tracker

```bash
python3 scripts/build-tracker.py
```

---

## Step 5: Verification

- [ ] Every Brief # has a QA row
- [ ] QA row count equals BRIEFS row count
- [ ] No banned language slipped through
- [ ] All Medium/High risk items have specific mitigation notes
- [ ] Scores are calibrated (honest spread, not everything 8/10)
- [ ] `data.json` is valid JSON
- [ ] Tracker rebuilt successfully

---

## Pre-Launch QA Checklist

Run every ad through these five checks before approving for launch. Each maps directly to Step 1 above.

**1. Ad Strength Score** — Score 1–10 (Step 1E). Most ads land 6–8. 9+ = ship immediately.

**2. Legal Check** (Step 1B)
- No medical claims ("cures," "treats," "diagnoses")
- No implied therapy replacement
- Risk level: Low / Medium / High — Medium/High needs a mitigation note
- "Trauma" and "nervous system" = Medium risk by default

**3. Target Market Check** (Step 1C)
- Does this speak to ICP1 (quietly overloaded professionals)?
- Does this speak to ICP2 (emotionally literate seekers)?
- ICP1 fails silently — no complaints, they just leave. Low activation energy required.

**4. Brand Voice Check** (Step 1A)
- Warm but grounded? No salesy language?
- No "healing journey," "self-care," "wellness routine"?
- No generic chatbot framing? Sounds like Aurie / Aura?

**5. Fatigue Check** (Step 1D)
- Is a near-identical version already running?
- Differentiated enough from current ads?
- Has this exact angle been tested recently?

**Copywriting techniques to apply:**
- **Wound/Healing Mirror:** Match specific failures in the decline beat with opposite outcomes in the transformation beat. Example: "In a minute" (dismissive) → "I said yes" (present).
- **Imperfect Recovery:** Never claim full restoration. Credibility comes from honesty about limits. Show progress, not perfection.

---

## Quality Standards

- **Be honest, not generous.** A 6/10 with clear notes is more useful than an inflated 8/10.
- **Be specific, not vague.** "Medium — 'rewire anxious thinking' could be read as medical claim; soften to 'helps you notice anxious patterns'" is useful. "Medium risk" alone is not.
- **Catch what humans miss.** Actually check for banned words. Flag subtle therapy-replacement implications.

## Output File Links

After completing scoring and rebuilding the tracker, always end your response with clickable markdown links for the files updated:

```
Updated: [output/data.json](output/data.json)
Tracker rebuilt: [output/tracker.html](output/tracker.html)
```
- **Think like Meta's ad reviewer.** Flag health claims, before/after implications, and personal attributes targeting.
