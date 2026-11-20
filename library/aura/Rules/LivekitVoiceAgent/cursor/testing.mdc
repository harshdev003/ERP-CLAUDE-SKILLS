---
description: Testing standards
globs:
alwaysApply: true
---

# Testing

- **Run** `make test` (pytest). `asyncio_mode = auto` — no `@pytest.mark.asyncio` needed.
  Single file: `venv/bin/pytest test/unit/test_foo.py -v`.
- **Layout**:
  - `test/unit/` — pure logic (config validation, metrics conversion, room parsing, logger
    formatter, context filter, error catalog, TTS factory kwargs).
  - `test/behavioral/` — full agent turns via `AgentSession.run()` + the `RunResult` assertion DSL
    (`result.expect.contains_message(role="assistant").event().item.text_content`).
- **No network in tests.** The LLM routes through the stock `openai` plugin against the Aura
  OpenAI-compatible endpoint — there's no `IntelligenceService` LLM seam to stub, and
  `BaseAgent.llm_node` only wraps `Agent.default.llm_node` (monkeypatch that to replay chunks, as
  `test_base_agent_llm_node.py` does). Monkeypatch `get_config` to force a config path; the metrics client
  (`send_conversation_metrics`) is covered by guard-level unit tests (no real HTTP).
- **Mock at the seam, not the network.** Provider classes are monkeypatched so no real API keys
  are needed; assert the kwargs the factory assembles.
- **CI gates** (CircleCI), in the order the test job runs them: `make format-check` (black + isort,
  read-only), `make lint` (`pylint src test`, fail-under 9.0), `make test` (pytest) — all three must
  pass, and **deploy requires tests**. Add/extend tests with any behavior change; don't let the suite
  regress. Formatting is its own gate because nothing else here sees it: pylint and pytest are both
  blind to it, so four unformatted files reached master while the only thing checking was the
  pre-commit hook — and a hook is installed per clone (`make install-dev`), which makes it a
  convenience, not a gate. The formatter majors are upper-bounded in `pyproject.toml`'s `[dev]` for
  the same reason: once CI gates a formatter, an unpinned major can fail the build on files nobody
  touched. Bump those bounds and the `.pre-commit-config.yaml` revs together, running `make lint-fix`
  in the same change.
- Tests are excluded from pylint via `.pylintrc` `ignore-patterns=test_*`, so test ergonomics
  (private imports, fixtures) won't fight the linter.

## Manual / live QA

For manual QA, drive the agent with `python src/main.py console` (offline mic/speaker) or `dev` +
the LiveKit Agents Playground, and pass the same metadata a client sends via the **`AGENT_METADATA`**
env var (JSON used as job metadata when the dispatch carries none). Full runbook + metadata schema:
`docs/local-qa.md`. The **talk-to-aurie** skill (AINative) drives the deployed/local agent end-to-end
(text or voice).

⚠️ **Use a real Aura `user_id` for any QA that exercises the LLM.** The backend coach prompter returns
**empty** content for a non-existent user, so user turns come back blank while the static greeting
(`first_message`) still plays — which makes a blank turn look like a TTS/text bug when it's just an
unknown user. (The greeting is a static `say()`; only user turns hit the LLM.)
