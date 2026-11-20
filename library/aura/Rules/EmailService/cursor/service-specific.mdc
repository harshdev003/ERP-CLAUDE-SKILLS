---
description: Rules that apply to EmailService only
globs:
alwaysApply: true
---

# EmailService rules

Rules for this service only. The rules shared by every backend service are in the other files in this directory and are identical in all 13 services; a general rule goes there, not here. How emails, templates, campaigns and localization work is in `CLAUDE.md`.

- **Dev and prod share one SendGrid account.** `SENDGRID_API_KEY` is the same key in every environment, so any change in SendGrid (a new template, a new version, an activation) is a prod change and needs Jerome's go. Only the emailDB rows have a dev copy.
- **Never edit the active version of a live SendGrid template.** Add a new version, test it from dev, then activate it. Rollback is re-activating the previous version. An in-place edit reaches every recipient of that template at once and leaves nothing to roll back to.
- **The `active` flag doesn't stop a send.** `POST /emails` looks up the campaign and the template by slug and ignores `active` on both. `campaign.active` only decides whether a campaign job (activation, retention, winback, …) runs the campaign, so transactional campaigns stay inactive. `template.active` only decides whether the localize job translates it. To stop a send, change the caller or remove the row.
- **Translatable text lives in the template row's `localization`, not in SendGrid.** Each key must be one of `localization.types.template.attributes` in `src/config.js`. A key outside that list is never translated and always goes out in English. The SendGrid template prints the strings with triple braces (`{{{localization.message}}}`, subject `{{{localization.subject}}}`), because EmailService has already rendered and escaped them.
- **Changing an English string re-translates it in every locale on the next hourly localize run.** It overwrites any hand-edited translation and clears `isReviewed`. Until the job runs, every reader gets the old wording, English included, because the stored English copy wins over the template row. Change English copy knowingly, and re-apply reviewed translations afterwards.
- **Machine translations go live unreviewed.** The localize job writes the 6 translations straight into the `localization` table, and the next send uses them. Nothing reads `isReviewed`. Have new copy reviewed in dev before the prod row goes in.
- **A locale outside `localization.locales` gets English, and so does a missing string.** The language is the caller's `locale`, then the user's `locale` in emailDB, then `en`. A missing translation falls back to English per string and never blocks the send.
