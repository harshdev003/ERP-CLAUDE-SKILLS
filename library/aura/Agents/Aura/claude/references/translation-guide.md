# Aura Localization — Shared Translation Guide

Shared reference for the per-locale translator agents (`translator-de`, `translator-es`,
`translator-fr`, `translator-ja`, `translator-ko`, `translator-pt-br`). Each locale agent
adds its own register/typography conventions on top of these shared rules.

## What Aura is

Aura is a meditation, sleep, and mental-wellness mobile app. The tone is calm, warm,
supportive, and encouraging — never clinical, alarming, or salesy. Translations must read
as if written by a native speaker for a wellness product, not as machine output.

## Brand terms — keep verbatim, never translate

- **Aura** — the app/product name. Always "Aura".
- **Aurie** — the in-app AI companion. Always "Aurie". Do NOT confuse with "Aura".
- Proper product/feature names that ship in English in the existing locale files (check the
  current translations before deciding). Common ones kept in English across locales:
  `Aura`, `Aurie`, `VoiceOver`, `TalkBack`, `Face ID`, `Touch ID`, `Apple`, `Google`.
- **"Premium"** — usually kept as "Premium" in es/fr/pt_BR/de, but verify against the
  locale's existing `subscription.json` and follow whatever is already established there.

## The translation contract (input / output)

You are given two files:

1. **Worklist** — `/tmp/l10n/work_<locale>.json`, shape `{ "<namespace>.json": { "<key>": "<English source>" } }`.
   Translate the value of every key.
2. **Fallback keys** — `/tmp/l10n/fallbackkeys_<locale>.json`, shape `{ "<namespace>.json": ["<key>", ...] }`.
   These keys currently hold the English text verbatim in the locale file (translation debt
   OR an intentional brand term). Translate them like any other key **unless** the value is a
   pure brand/proper noun per the rules above — then keep it identical.

You must **Write** the result to `/tmp/l10n/done_<locale>.json` with the **exact same
`{namespace: {key: value}}` shape** as the worklist, containing **every key** from the
worklist (same keys, translated values). Do not add, drop, rename, or reorder keys relative
to what you output — the orchestrator merges by key. Output valid UTF-8 JSON (real accented
characters, not `\uXXXX` escapes).

## Hard rules (breaking any of these is a bug)

1. **Preserve interpolation placeholders exactly.** i18next uses `{{name}}`, `{{count}}`,
   `{{price}}`, etc. Copy the `{{...}}` token verbatim — never translate, space, or alter the
   text inside the braces. Reorder placeholders within the sentence only if the target
   grammar requires it; keep the token spelling identical.
2. **Preserve format specifiers** if present: `%s`, `%d`, `%@`, `{0}`, `$t(...)` nesting refs.
3. **Preserve escapes and structure**: `\n` newlines, leading/trailing spaces, surrounding
   quotes, markdown (`**bold**`, `[text](url)`), and any HTML-ish tags (`<b>`, `<0>`). Never
   translate a URL.
4. **Plurals**: keys may end in `_one` / `_other` (i18next plural suffix). The suffix stays
   in the key; translate the value for that grammatical number in the target language (some
   languages need different plural forms than English — render the natural one for that key's
   suffix).
5. **Emoji / symbols** stay as-is and in the same position relative to text.
6. **Don't invent keys or content.** Only translate what's in the worklist.

## Quality bar

- Natural, idiomatic, native-quality — not literal. Re-order and rephrase for natural target
  grammar while keeping the meaning and the placeholders.
- UI strings (buttons, tab labels, short toasts) must stay **short** — match the English
  length budget. A button that says "Start" should not become a five-word phrase.
- Keep terminology **consistent** with what already ships in the locale. Before translating,
  `Read` 1–2 existing translated files in your locale (good choices:
  `src/localization/<locale>/subscription.json`, `.../content.json`, `.../onboarding.json`)
  and reuse the established wording for recurring terms (meditation, session, sleep story,
  streak, reminder, breathe, course, coach, etc.).
- Sentence case / capitalization and punctuation follow the **target language's** norms, not
  English's.

## Workflow for the agent

1. Read this guide and your locale's agent file.
2. `Read` `/tmp/l10n/work_<locale>.json` and `/tmp/l10n/fallbackkeys_<locale>.json`.
3. `Read` 1–2 existing translated namespace files in your locale to lock in terminology.
4. Translate every entry, applying the hard rules + your locale register.
5. `Write` the complete `{namespace: {key: value}}` map to `/tmp/l10n/done_<locale>.json`.
6. Report a one-line summary: locale, number of keys translated, any keys you intentionally
   left in English (brand terms), and any source strings that looked broken/ambiguous.
