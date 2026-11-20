# User Personas for Sim Tests

## Purpose

Each new-user onboarding test should use a **unique, realistic persona** instead of generic "TestUser" names and identical quiz answers. This makes tests more realistic and helps catch edge cases (e.g., long names, different personality profiles triggering different app paths).

## When to Create a Persona

- **Before every new-user onboarding flow** (goal: onboarding, or any flow that creates a new account)
- **NOT needed** for login-with-existing-user flows (use the persona already stored in `users.json`)

## Persona Structure

```json
{
  "persona": {
    "firstName": "Maya",
    "ageRange": "26 - 35",
    "quizAnswers": {
      "whatBringsYou": 2,
      "emotionalPlate": 2,
      "upsetResponse": 1,
      "pressureResponse": 0,
      "mentalSpace": [0, 4]
    },
    "attributionSource": "Google",
    "attributionDetail": "Ads in search",
    "personality": "Reflective creative professional who tends to overthink. Drawn to journaling and self-improvement."
  }
}
```

### Field Reference

| Field | Type | Description |
|-------|------|-------------|
| `firstName` | string | Realistic first name — vary gender, ethnicity, length |
| `ageRange` | string | One of: `"Under 26"`, `"26 - 35"`, `"36 - 45"`, `"46 - 55"`, `"Over 56"` |
| `quizAnswers.whatBringsYou` | int (0-5) | Q1 option index: 0=specific event, 1=carrying a lot, 2=better habits, 3=self-confidence, 4=recommended, 5=felt drawn |
| `quizAnswers.emotionalPlate` | int (0-3) | Q2 option index: 0=completely full, 1=mostly full, 2=moderate, 3=light |
| `quizAnswers.upsetResponse` | int (0-4) | Q3 option index: 0=process quickly, 1=settle eventually, 2=distract, 3=lingers, 4=push through |
| `quizAnswers.pressureResponse` | int (0-4) | Q4 option index: 0=overthink, 1=irritable, 2=withdraw, 3=overdrive, 4=feel in body |
| `quizAnswers.mentalSpace` | int[] (0-4) | Q5 multi-select indices: 0=work, 1=relationships, 2=family, 3=health, 4=self-worth |
| `attributionSource` | string | Top-level attribution: `"Google"`, `"Social media"`, `"Recommendations from people"`, `"Other"` |
| `attributionDetail` | string | Sub-option within attribution source |
| `personality` | string | Brief personality description — informs text input style during QP and conversations |

## Persona Pool

Pick randomly from this pool, or generate a new one. **Do not reuse the same persona consecutively.**

### Persona 1: Maya
- **Name:** Maya | **Age:** 26 - 35
- **Quiz:** better habits (2), moderate (2), settle eventually (1), overthink (0), work + self-worth [0, 4]
- **Attribution:** Google > Ads in search
- **Personality:** Reflective creative professional who tends to overthink. Drawn to journaling and self-improvement.

### Persona 2: James
- **Name:** James | **Age:** 36 - 45
- **Quiz:** specific event (0), mostly full (1), push through (4), irritable (1), relationships + family [1, 2]
- **Attribution:** Recommendations from people > Friend
- **Personality:** Pragmatic dad managing work-life balance. Recently went through a difficult period. Prefers direct, practical advice.

### Persona 3: Priya
- **Name:** Priya | **Age:** Under 26
- **Quiz:** carrying a lot (1), completely full (0), distract (2), withdraw (2), health + self-worth [3, 4]
- **Attribution:** Social media > TikTok
- **Personality:** College student dealing with anxiety and burnout. Introverted, expressive in writing. Looking for a safe space.

### Persona 4: Marcus
- **Name:** Marcus | **Age:** 46 - 55
- **Quiz:** self-confidence (3), light (3), process quickly (0), feel in body (4), work [0]
- **Attribution:** Other > Podcast
- **Personality:** Executive coach working on emotional intelligence. Physically active, notices stress somatically. Goal-oriented.

### Persona 5: Sofia
- **Name:** Sofia | **Age:** 26 - 35
- **Quiz:** recommended (4), mostly full (1), lingers (3), overthink (0), relationships + work [1, 0]
- **Attribution:** Recommendations from people > Therapist
- **Personality:** Bilingual marketing manager. Tends to ruminate on interpersonal conflicts. Therapist recommended the app.

### Persona 6: Kai
- **Name:** Kai | **Age:** Under 26
- **Quiz:** felt drawn (5), moderate (2), settle eventually (1), withdraw (2), self-worth [4]
- **Attribution:** Social media > Instagram
- **Personality:** Non-binary artist exploring emotional growth. Quiet, thoughtful, prefers gentle approaches.

### Persona 7: Elena
- **Name:** Elena | **Age:** 36 - 45
- **Quiz:** carrying a lot (1), completely full (0), push through (4), overdrive (3), family + health [2, 3]
- **Attribution:** Google > Blog or article
- **Personality:** Working mom and caregiver for aging parent. High achiever who ignores her own needs. Exhausted but determined.

### Persona 8: David
- **Name:** David | **Age:** Over 56
- **Quiz:** better habits (2), light (3), process quickly (0), feel in body (4), health [3]
- **Attribution:** Other > Other
- **Personality:** Retired teacher exploring mindfulness. Calm, curious, new to mental health apps. Types slowly and carefully.

## How to Use During Tests

### During Onboarding Quiz
Map `quizAnswers` indices to `onboarding_quizOption_{index}`:
```json
{"action": "tap", "target": "onboarding_quizOption_2", "reasoning": "Selecting 'I want better emotional habits' per persona Maya"}
```

### For Name Input
```json
{"action": "type", "target": "onboarding_nameField", "value": "Maya", "clearFirst": true, "reasoning": "Entering persona name"}
```

### For Multi-Select Questions (mentalSpace)
Tap each index in the array, then tap Next:
```json
{"action": "tap", "target": "onboarding_quizOption_0", "reasoning": "Selecting 'Work' per persona"}
{"action": "tap", "target": "onboarding_quizOption_4", "reasoning": "Selecting 'Self-worth' per persona"}
{"action": "tap", "target": "Next", "reasoning": "Advancing after multi-select"}
```

### During Quick Processing / Conversations
Use the `personality` field to inform how the persona would respond:
- **Priya** (anxious student): shorter, more emotional responses
- **Marcus** (executive coach): direct, action-oriented responses
- **David** (retired teacher): thoughtful, longer responses

### In users.json
Store the persona with the user entry after creation:
```json
{
  "email": "<redacted-email>",
  "password": "Sim258647x!",
  "createdAt": "2026-04-03T22:00:00Z",
  "onboardingCompleted": true,
  "persona": {
    "firstName": "Maya",
    "ageRange": "26 - 35",
    "personality": "Reflective creative professional who tends to overthink."
  },
  "notes": "Quiz: better habits, moderate, settle eventually, overthink, work+self-worth. Attribution: Google > Ads."
}
```

## Generating New Personas

If none of the pool personas fit or you need variety:
1. Pick a random first name (vary gender and cultural background)
2. Pick a random age range (weight toward 26-35 and Under 26 as primary demographics)
3. Randomly select quiz answers (one per single-select, 1-3 for multi-select)
4. Choose a plausible attribution source
5. Write a 1-2 sentence personality summary that's consistent with the quiz answers
6. The persona should feel like a real person — avoid generic descriptions
