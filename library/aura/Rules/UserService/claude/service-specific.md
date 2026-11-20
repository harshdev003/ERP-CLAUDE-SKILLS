---
description: Rules that apply to UserService only
globs:
alwaysApply: true
---

# UserService rules

Rules for this service only. The rules shared by every backend service are in the other files in this directory and are identical in all 13 services; a general rule goes there, not here.

- **`DELETE /users/:uid` is an account delete, not an erasure.** Every service database only marks the rows deleted (`deletedAt`); the `*Backup` tables (`emailBackup`, `notificationBackup`, `feedBackup` and others) are untouched; and the delete itself writes new events about the person into BigQuery `firebase.raw_events`. A privacy erasure needs more than this route: follow AI Knowledge → Compliance → Privacy → Access & Erasure Requests and the `eng:respond-to-erasure` skill (ENGSUP-160).
