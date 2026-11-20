# Braintrust dataset save — command recipe

The `save_row.py` commands and `--pattern` default heuristics for Step 9 (save the analysed conversation as a runnable eval case in the Braintrust `Feedback Conversation` dataset). Referenced from `SKILL.md` Step 9a and 9c. The behavioural rules (opt-in, ask-first, skip conditions) stay in `SKILL.md`.

## Step 9a — Pre-check dedup command

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/save-to-braintrust-dataset/scripts/save_row.py" \
  --check-dupe --conversation-id "<UUID>"
```

If `existing_count == 0` → frame the offer as **create**. If `existing_count >= 1` → frame as **update** / **skip**, surfacing the existing row(s) `case_id` + `pattern`.

## Step 9c — Compose command

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/save-to-braintrust-dataset/scripts/save_row.py" \
  --build \
  --conversation-id "<UUID>" \
  --observed-rating "<positive|negative|neutral>" \
  --source "<Slack permalink (Mode A); for Mode B, an internal-only string like `openai://conversationId=<UUID>` is acceptable since OpenAI has no public per-conversation deep link>" \
  --source-date "<YYYY-MM-DD from the k8bot message or today>" \
  --pattern "<best-guess pattern slug from the analysis>" \
  --expected "<distilled from 'What didn't' + 'Patterns / hypotheses' as pass criteria>" \
  --out /tmp/feedback_row_draft.json
```

## `--pattern` default heuristics

When seeding `--pattern`, look at the analysis's **Patterns / hypotheses** section and pick the most concrete falsifiable pattern. Examples that have appeared so far (not exhaustive — add to this list as the dataset grows):

- Closing-line minimising language for users wounded around inadequacy → `qp_closing_echoes_inner_critic`
- QP card fires on a user turn containing stacked exit tokens → `qp_entry_on_exit_tokens`
- coach-prompter runs validate → reframe → ask for 3+ turns then converts self-blame to inspection → `cadence_saturation`
- `recommendTracks` tag vector ignores the user's last-turn coping action → `recommend_tracks_honors_last_turn_intent`
- coach-prompter ends 4+ turns in a row with `?` despite "structured support" preference → `question_saturation`

When unsure, leave `--pattern` empty — the sub-skill will prompt for it.
