---
description: LiveKit Voice Agent — canonical Python development rules
globs:
alwaysApply: true
---

# LiveKit Voice Agent — Development Rules

This is the canonical baseline rule file for this repository. Other files in `.cursor/rules/`
add non-overlapping, topic-specific guidance; if anything conflicts, **this file wins**.

## 🚨 Before any code change

1. Work inside the project venv: `venv/bin/...` (created by `make setup` / `make venv`). The `make`
   targets now resolve tools out of `venv/` themselves and print which one they used, so
   `make test` / `make lint` are safe to invoke bare. If a local result ever disagrees with CI, run
   **`make which-env`** first — a tool resolved off `PATH` instead of `venv/` gates against the wrong
   dependency set, and that failure is otherwise silent.
   **In a worktree there is no `venv/`, and the pre-push hook still needs one.** The hook's `pylint`
   is `language: system`, so it resolves off `PATH`; in `.claude/worktrees/<slug>` that finds a
   Python without `livekit` installed and the push fails on import errors, not on your code. Push
   with the main clone's venv first on `PATH`: `PATH="<main-clone>/venv/bin:$PATH" git push`.
   *Why:* two pushes from worktrees failed this way on the same day, each costing a diagnosis
   round before the PR could open.
2. After any change, run **`make lint`** (pylint, `fail-under=9.0` in `.pylintrc`) and **`make test`** (pytest).
3. Auto-format with **`make lint-fix`** (black + isort, line length **120**). Not optional: CI gates
   formatting through **`make format-check`** (the read-only half of the same pair), so unformatted
   code fails the build rather than landing and surfacing as noise in someone else's PR.
4. Only proceed once formatting, lint and tests are all green. CI (CircleCI) gates all three and
   blocks deploy on failure.

## What this is

A production LiveKit voice agent (livekit-agents ~1.x) with multi-provider TTS (ElevenLabs,
OpenAI, Cartesia, Inworld), Deepgram STT, and OpenAI/Aura LLM. It integrates with the Aura
Health backend (IntelligenceService) for LLM calls and per-turn metrics.

## Stack invariants

- **Python 3.12+**, async/await throughout.
- Config, logging, context, and errors follow the canonical Aura conventions (see the
  per-topic rules) — aligned with EmotionSystem / IntelligenceService.
- Tests: unit (`test/unit/`) + behavioral (`test/behavioral/`, driving `AgentSession.run()`).
- Deployment: LiveKit Cloud via `livekit.toml` (entrypoint `src/main.py`); CI deploys on `v*` tags.

## Don't regress the agent's good parts

`pyproject.toml` (not pinned `requirements.txt`), slim + non-root Dockerfile with model
prewarm, the Makefile, and black/isort config are deliberate. Keep them.
