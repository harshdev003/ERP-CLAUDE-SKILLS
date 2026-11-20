# Key references

Sibling skills, endpoints, canonical Slack threads, and the Linear tickets behind `profile-user`.

- **Sibling skill (behaviour):** [`data:analyze-user-behavior`](../../analyze-user-behavior/SKILL.md)
- **Sibling skill (conversations):** [`data:analyze-user-conversations`](../../analyze-user-conversations/SKILL.md)
- **Sibling skill (journal analysis):** [`data:analyze-user-journal`](../../analyze-user-journal/SKILL.md) — same pattern, OpenAI `Journal [Production]` project
- **Sibling skill (cost):** [`eng:report-user-costs`](../../../../eng/skills/report-user-costs/SKILL.md)
- **Orchestrators that call this skill:** `data:investigate-cancellation-feedback` (sweeps cancellations, `context.kind=cancellation`) and `data:investigate-pmf-survey` (sweeps PMF responses, `context.kind=pmf`) — both call this skill per user with `side_effects: off` and own their own threaded Slack replies + aggregate digests.
- **Aura API base URL:** `https://services.aurahealth.io`
- **Tenure + engagement endpoint:** `GET /ai/usage/engagement/:userId` — one privileged call (roles `admin` / `agent` / `service` / `system`, `AURA_API_TOKEN`) returning `tenure` / `conversations` / `messages` / `voice` / `feed` / `overall`. Shipped in Intelligence Service 1.6.1 (2026-07-30). Canonical doc: AI Knowledge → `Tools/Aura API` → Usage → Engagement; Postman: Aura Services → Usage [AI] → Engagement. Used by SKILL.md Step 3d.
- **`aiCreatedAt` sentinel:** [`eng:backfill-aurie-user-ai-created`](../../../../eng/skills/backfill-aurie-user-ai-created/scripts/backfill.py) — canonical home of the bulk-stamp constant (`SENTINEL_PREFIX`). **Reference only** for `profile-user`: read it to confirm the constant, never import it, compose the skill, or replicate its Mixpanel first-AI-login fallback (Architecture principle 1; the fallback was ruled out for this skill in ENG-1019 — a lower-bound tenure is enough for a one-user qualitative read).
- **Mixpanel project:** the Aura production project — exact ID lives in `Tools/Mixpanel`.
- **OpenAI project:** `Conversation [Production]` (`proj_esFyd4eq2VIAjKUn69X1ZFfk`) — the source of the Aurie transcripts. Logs UI: <https://platform.openai.com/logs> (OpenAI exposes no per-conversation deep link; filter by `metadata.conversationId` / `metadata.user`).
- **Originating Slack thread (cancellation feedback flow):** <https://auramobile.slack.com/archives/C0AENS8D48N/p1778072519577519> — k8bot canonical example input + Steve & Alyssa's request to analyse all trial + paid cancellations.
- **Originating Slack thread (rating flow):** <https://auramobile.slack.com/archives/C07MB0TMF5J/p1778207250134199> — k8bot `*QUICK PROCESSING RATED*` example that surfaced the gap fixed in [PROD-1649](https://linear.app/aura-health/issue/PROD-1649/extend-profile-user-to-handle-quick-processing-coaching-rated-k8bot).
- **Linear ticket (initial skill):** [PROD-1625](https://linear.app/aura-health/issue/PROD-1625/add-dataprofile-user-skill)
- **Linear ticket (rating-shape extension — QP/Coaching/Conversation Rated):** [PROD-1649](https://linear.app/aura-health/issue/PROD-1649/extend-profile-user-to-handle-quick-processing-coaching-rated-k8bot)
- **Linear ticket (rating-shape extension — Chat Message Rated):** [PROD-1704](https://linear.app/aura-health/issue/PROD-1704/extend-profile-user-to-handle-chat-message-rated-k8bot-urls)
- **Linear ticket (Shape 6 — Journal Rated):** [PROD-2412](https://linear.app/aura-health/issue/PROD-2412/extend-dataprofile-user-with-journal-rated-shape-shape-6)
- **Linear ticket (Step 8 — save to Braintrust dataset):** [PROD-1707](https://linear.app/aura-health/issue/PROD-1707/offer-to-save-analysis-to-feedback-conversation-dataset-after-analyze) (sub-skill: [PROD-1708](https://linear.app/aura-health/issue/PROD-1708/build-datasave-to-braintrust-feedback-dataset-skill))
- **Linear ticket (attribution surfacing):** [ENG-525](https://linear.app/aura-health/issue/ENG-525/add-attribution-to-profile-user)
- **Linear ticket (tenure + conversation volume — Step 3d / section 2b):** [ENG-1019](https://linear.app/aura-health/issue/ENG-1019)
- **Linear ticket (flag-driven generalization — this refactor):** [PROD-2409](https://linear.app/aura-health/issue/PROD-2409)
- **Linear tickets (orchestrators built on top):** [PROD-2398](https://linear.app/aura-health/issue/PROD-2398) (cancellation sweep), [PROD-2399](https://linear.app/aura-health/issue/PROD-2399) (PMF sweep)
- **Parent epic:** [PROD-1522](https://linear.app/aura-health/issue/PROD-1522/add-update-skills)
