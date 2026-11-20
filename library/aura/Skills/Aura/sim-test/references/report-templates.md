# Report Templates

## Goal Mode Report

```
## Sim Test Report — {Goal}

**Result:** PASSED / FAILED
**Steps:** {total}
**Date:** YYYY-MM-DD HH:MM
**Email:** {email used for auth}
**Session ID:** {UDID}
**Screenshots:** AuraSimTests/Sessions/{UDID}/Screenshots/
**Recording:** AuraSimTests/Sessions/{UDID}/Screenshots/recording_*.mp4 (if recorded)

### Screenshot Flow
| # | Screen | Action | Result |
|---|--------|--------|--------|
| 1 | Welcome | Tap Get Started | ✓ |
| ... | ... | ... | ... |

### App Logs (last 50 lines)
If `Sessions/{UDID}/app.log` exists, include the last 50 lines in a collapsed block:

<details>
<summary>App Logs (last 50 lines)</summary>

\```
{tail -50 app.log output}
\```
</details>

### Summary
Brief description of outcome.
```

## Explore Mode Report

```
## Exploration Report

**Result:** COMPLETE / PARTIAL / FAILED
**Coverage:** {screens} screens, {explored}/{total} elements ({pct}%)
**Findings:** {count} ({critical} critical, {major} major, {minor} minor, {suggestions} suggestions)
**Steps:** {total}
**Crashes:** {count}
**Email:** {email used for auth}
**Session ID:** {UDID}
**Screenshots:** AuraSimTests/Sessions/{UDID}/Screenshots/
**Recording:** AuraSimTests/Sessions/{UDID}/Screenshots/recording_*.mp4 (if recorded)

### Coverage Map
| Screen | Visits | Explored | Unexplored | Status |
|--------|--------|----------|------------|--------|
| home_screen | 5 | 8/10 | settings_tab, profile | Hub |
| journal_screen | 2 | 4/4 | — | Complete |
| ... | ... | ... | ... | ... |

### Findings

#### Critical
1. **[crash]** App crashed on journal_screen after tapping delete (step 42)

#### Major
2. **[bug]** Settings page shows stale data after profile update (step 28)

#### Minor
3. **[ux]** No confirmation before clearing journal draft (step 15)

#### Suggestions
4. **[accessibility]** Home greeting card has no VoiceOver label (step 8)
5. **[ux]** Tab bar icons have no selected state indicator (step 12)

### Navigation Graph
Screens visited: A → B → C → A → D → E → ...
Hub screens: home_screen (5 outgoing), settings_screen (3 outgoing)

### Screenshot Highlights
Show screenshots for key findings and important transitions.

### App Logs (last 50 lines)
If `Sessions/{UDID}/app.log` exists, include the last 50 lines in a collapsed block:

<details>
<summary>App Logs (last 50 lines)</summary>

\```
{tail -50 app.log output}
\```
</details>

### Summary
Overall assessment of app quality, key issues found, areas not reachable.
```

## UX Analysis Report

```
## UX Analysis Report

**Result:** COMPLETE / PARTIAL / FAILED
**Screens analyzed:** {count}
**UX Findings:** {count} ({critical} critical, {major} major, {minor} minor, {suggestions} suggestions)
**Steps:** {total}
**Email:** {email used for auth}
**Session ID:** {UDID}
**Screenshots:** AuraSimTests/Sessions/{UDID}/Screenshots/
**Recording:** AuraSimTests/Sessions/{UDID}/Screenshots/recording_*.mp4 (if recorded)

### UX Scorecard (How It Works)
| Dimension | Rating | Notes |
|-----------|--------|-------|
| User Flows | ⭐⭐⭐⭐ | Tasks completable, clear happy path |
| Navigation & Structure | ⭐⭐⭐ | Back behavior inconsistent on 2 screens |
| Ease of Use | ⭐⭐⭐⭐ | First-time user would understand most flows |
| Friction & Confusion | ⭐⭐⭐ | Some unnecessary steps in onboarding |
| Efficiency | ⭐⭐⭐⭐ | Key features reachable quickly |
| Missing Actions | ⭐⭐⭐⭐ | No critical missing buttons found |
| Thumb-friendliness | ⭐⭐⭐⭐ | Primary actions within reach |

### UX Findings

#### Critical (stuck / no way out)
1. **[ux]** Description (step N, screen_name)

#### Major (confusing / missing action)
2. **[ux]** Description (step N, screen_name)

#### Minor (suboptimal but workable)
3. **[ux]** Description (step N, screen_name)

#### Suggestions (smoother flow)
4. **[ux]** Description (step N, screen_name)

### Navigation Map Issues
Screens with inconsistent back behavior, orphan screens, or deep hierarchies.

### Missing / Non-Obvious Interactions
Buttons or actions that should exist but don't, or hidden gestures with no visual hint.

### App Logs (last 50 lines)
If `Sessions/{UDID}/app.log` exists, include the last 50 lines in a collapsed block:

<details>
<summary>App Logs (last 50 lines)</summary>

\```
{tail -50 app.log output}
\```
</details>

### Summary
Overall UX assessment, top 3 improvements recommended.
```

## Copy Analysis Report

```
## Copy Analysis Report

**Result:** COMPLETE / PARTIAL / FAILED
**Screens analyzed:** {count}
**Copy Findings:** {count} ({critical} critical, {major} major, {minor} minor, {suggestions} suggestions)
**Steps:** {total}

### Copy Scorecard (What It Says)
| Dimension | Rating | Notes |
|-----------|--------|-------|
| Grammar & Spelling | ⭐⭐⭐⭐⭐ | No typos found |
| Capitalization | ⭐⭐⭐⭐ | Minor inconsistency on 1 screen |
| Tone & Voice | ⭐⭐⭐⭐⭐ | Warm, supportive throughout |
| Clarity | ⭐⭐⭐⭐ | 2 ambiguous button labels |
| Consistency | ⭐⭐⭐ | "Chat" vs "Conversation" used interchangeably |
| Error Messages | ⭐⭐⭐⭐ | Most are friendly, 1 is too vague |
| Accessibility Labels | ⭐⭐⭐⭐ | Most descriptive, a few generic |

### Copy Findings
(grouped by severity, with screen name and step number)

### Terminology Map
| Concept | Terms Used | Screens | Recommendation |
|---------|-----------|---------|----------------|
| Chat session | "Chat", "Conversation" | home, journal | Standardize to "Conversation" |

### App Logs (last 50 lines)
If `Sessions/{UDID}/app.log` exists, include the last 50 lines in a collapsed block:

<details>
<summary>App Logs (last 50 lines)</summary>

\```
{tail -50 app.log output}
\```
</details>

### Summary
Overall copy quality assessment, top improvements recommended.
```
