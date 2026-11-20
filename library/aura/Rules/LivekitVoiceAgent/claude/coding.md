---
description: Python coding standards
globs:
alwaysApply: true
---

# Coding standards

- **Python 3.12+**, async/await throughout. Don't block the event loop; detached tasks must be
  referenced (don't let `asyncio.create_task` results get garbage-collected).
- **Formatting**: black + isort, line length **120** (`make lint-fix`). Don't hand-format.
- **Lint**: pylint via `.pylintrc` (`fail-under=9.0`, `fail-on=E,F`). Fix warnings you introduce;
  don't lower the floor to pass.
- **Type hints** on public functions and dataclass fields. Config is typed dataclasses (`src/config/`).
- **Logging, not print.** Use `logging.getLogger(__name__)`; never `print`. No emoji in log messages
  (see `logging` rule).
- **Imports**: standard-lib → third-party → first-party (`src.*`), isort-ordered. Prefer absolute
  `from src.x import y` at call sites; relative imports inside a package are fine.
- **Small, focused functions.** The few large functions (e.g. the entrypoint) are pragmatic
  exceptions, not a license to grow more.
- **One class per file.** In the domain packages (`agents/`, `sessions/`, `tts/`, `stt/`, `llm/`,
  `metrics/`, `services/`, `config/`) each class lives in its own `snake_case.py` module named
  after it (e.g. `manager.py` per package = the `XManager` + its `create_*()` seam); a package
  `__init__.py` re-exports the public surface. Class-free helpers/constants get their own module
  (e.g. `metrics/serialization.py`, `tts/settings_keys.py`). Even a framework class pair is split
  — when this repo vendored an Inworld plugin, its `TTS` and `ChunkedStream` lived in separate files
  (the cycle broken with a `TYPE_CHECKING` import). The singular cross-cutting modules `logger.py` /
  `context.py` / `errors.py` are the deliberate exception (see the `naming` rule).
- **Member ordering (mirrors the Aura consumer app).** Lay out every class in this canonical
  section order, sorting members **alphabetically within each section**:

  ```
  # MARK: - Public Properties     (class attrs / dataclass fields)
  # MARK: - Enums                 (nested enums; cases A–Z, no blank lines between cases)
  # MARK: - Private Properties    (class-level _-prefixed attrs, e.g. a registry)
  # MARK: - Initialization        (__init__)
  # MARK: - Computed Properties   (@property methods)
  # MARK: - Public Methods
  # MARK: - Private Methods        (_-prefixed)
  ```

  Label each populated section with a `# MARK: -` header — the Python mirror of the iOS app's
  `// MARK: -` — once a class spans **3+ sections**; smaller classes follow the order without
  headers. Catalog constants (e.g. `AgentErrors`) and module-level enum cases are alphabetical too;
  `match`/`if` branches over an enum follow the enum's case order. Python has no `internal`
  visibility, and the consumer app's `Dependencies`/`Listeners` sections are Swift-specific (folded
  into Initialization / the entrypoint here). Reorder dataclass fields only when every construction
  site uses keyword args (never positional). **Scope rule:** in a feature/bugfix PR insert new
  members in their alphabetical position but do NOT reorder existing ones — a full reorder is its
  own refactor PR.
- Keep third-party plugin imports guarded where optional (e.g. TTS/STT providers degrade to a clear
  warning + a `None` result rather than crashing).
