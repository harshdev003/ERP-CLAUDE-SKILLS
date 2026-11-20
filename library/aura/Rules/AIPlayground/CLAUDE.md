# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

AIVoiceDemo is an AI voice assistant web app for Aura Health. It combines a Next.js 14 (App Router) frontend with a custom Node.js server that handles WebSocket-based real-time voice streaming. The platform supports multiple LLM, STT, and TTS providers via a pluggable factory pattern.

## Commands

- **Dev**: `npm run dev` — starts custom Node.js server (with Next.js + Socket.io) via ts-node
- **Build**: `npm run build` — builds Next.js app (`.next/`) and compiles server to `dist/` (CommonJS)
- **Build server only**: `npm run build:server`
- **Start production**: `npm start` — runs `node dist/server/index.js`
- **Lint**: `npm run lint` (or `npm run lint:cache` for cached runs)
- **Release**: `npm run release` (uses release-it)
- **No test suite** — `npm test` is a no-op

Node version: 24.1.0 (see `.nvmrc`)

## Architecture

### Two-process design

The app runs a single HTTP server (`server/index.ts`) that serves both Next.js pages and a Socket.io WebSocket server at `/api/voice/stream`. The server compiles separately via `tsconfig.server.json` → `dist/`. On startup, `app/api/lib/init.ts` initializes global services (Braintrust, OpenAI, VertexAI eval loggers).

### Voice streaming pipeline (`server/sockets/`)

`VoiceAgentSocket` manages per-client Socket.io connections. Each connection accepts query params `userId`, `ttsProvider`, `sttProvider` and initializes the appropriate service instances. `VoiceAgentTaskImpl` (`VoiceAgentTask.ts`) orchestrates the streaming pipeline: audio frames (msgpack-lite serialized) → STT → LLM (streaming) → TTS, emitting `llm-chunk`, `llm-response`, `tts-audio-stream`, and `error` events. Token usage and TTS duration are written to Supabase after each turn. A `cancel()` method handles mid-stream cleanup.

### Provider factories (`server/services/`)

Three pluggable service layers, each with a factory + multiple provider implementations:
- **LLM** (`server/services/llm/`): GPT only. `GPTService` maintains per-session conversation history (truncated to 10 messages), supporting both single-shot and streaming modes.
- **STT** (`server/services/stt/`): AssemblyAI (default), Deepgram
- **TTS** (`server/services/tts/`): Google Cloud (default), ElevenLabs, Deepgram, Fish Audio, OpenAI GPT. All providers implement `TTSStream` with `updateStream(text)` / `finishStream(text)` for real-time chunked synthesis.

### Multi-agent system (`app/chat/`)

- **Agents** (`app/chat/agents/`): `BaseAgent` wraps OpenAI completions with name, model, system instructions, tools, and version details. Concrete agents: `companion`, `companionV2`, `coaching`, `coachingV1`. Agents can hand off to each other via handoff tools, but `enableHandoffs` is currently set to `false` in `useAgentsAndTools.ts`.
- **Tools** (`app/chat/tools/`): `BaseTool` defines `name`, `description`, `parameters` (OpenAI function schema), and an abstract `execute()`. Tools include memory CRUD (`saveMemory`, `retrieveMemory`, `deleteAllUserMemory`), content `recommend`, and agent handoffs (`handoffToCoaching`, `handoffToCompanion`).
- **Prompt management** (`app/chat/lib/promptManager.ts`): `configurePrompts()` syncs agent instructions and tool definitions with Braintrust on startup, persisting fetched prompts to localStorage. This is called from `useAgentsAndTools`.
- **Shared instructions**: `data/instructions.ts` holds system prompt strings referenced by multiple agents.
- **State**: React Context — `TranscriptContext` for chat history (`TranscriptItem` with role, content, guardrail results, status), `EventContext` for event logs. No Redux.

### GPT Realtime API path (`app/gpt-voice/`)

This page uses OpenAI's Realtime API directly (not the WebSocket server). Agent configs live in `app/gpt-voice/agentConfigs/` including guardrails (`guardrails.ts`) and tools (`recommendTracks.ts`). It has its own `TranscriptContext` and `EventContext` instances separate from `app/chat/`.

### Simulation subsystem (`app/simulate/`)

Multi-step wizard for Braintrust-backed prompt evaluation:
1. Select Braintrust project → fetch prompts
2. Select and edit a prompt
3. Configure synthetic user personas
4. Review generated users
5. Run conversations (calls `app/api/simulate/run`)
6. View results

`hooks/useSimulation.ts` owns all wizard state. Types in `app/simulate/types.ts` cover both Braintrust API shapes (`BraintrustProject`, `BraintrustPrompt`) and simulation domain types (`SyntheticUser`, `ConversationMessage`, `SimulationConfig`).

### API routes (`app/api/`)

- `/braintrust/projects`, `/braintrust/prompts`, `/braintrust/prompts/[id]`, `/braintrust/functions/[id]` — Braintrust project/prompt/tool fetch
- `/simulate/run` — execute a simulation run
- `/open-ai/*` — OpenAI chat/completions proxy
- `/vertex-ai/*` — Vertex AI integration
- `/eval/*` — evaluation utilities
- `/elevenlabs-token`, `/token` — token generation for ElevenLabs/Hume

### Database

Supabase (PostgreSQL). Schema in `database/create.sql`. Key tables:
- `session`: id, userId, model, voice, createdAt, endedAt
- `usage`: sessionId (FK), userId, token counts (input/output/total/cached variants, text/audio breakdowns)
- `memory` / `user_memories`: per-user persistent memory with category, preference, and tag filtering; soft-deleted via `deletedAt`

`services/supabase.ts` is the singleton client with typed helpers: `insertSession`, `updateSession`, `insertUsage` (with 2-retry logic), `insertMemory`, `retrieveMemory`, `deleteAllUserMemory`.

## Lint Rules (enforced strictly)

- **Sorted keys**: object keys must be alphabetically sorted (`sort-keys` rule)
- **Sorted imports**: imports sorted by member syntax order (`sort-imports` rule)
- **Sorted JSX props**: alphabetical, callbacks last (`react/jsx-sort-props`)
- **Sorted destructuring**: keys in destructured objects must be sorted
- **Prefer destructuring**: always destructure when possible
- **Prettier**: trailing commas (ES5), single quotes, 2-space tabs, print width 80

## Key External Integrations

OpenAI, Google Cloud (TTS + Generative AI), ElevenLabs, Hume AI (emotion detection), AssemblyAI, Deepgram, Fish Audio, Supabase, Braintrust (evals + prompt management), Vercel (analytics + hosting). API keys configured via `.env` (see `.env.example` for template).

## Path Aliases

`@/*` maps to the project root (configured in `tsconfig.json`).
