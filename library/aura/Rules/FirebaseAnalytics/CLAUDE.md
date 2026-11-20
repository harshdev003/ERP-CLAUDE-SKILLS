# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Security review:** `.claude/rules/security.md` is the checklist every reviewer applies before approving a PR: Claude, Cursor (`.cursor/rules/security.mdc`), BugBot (`.cursor/BUGBOT.md`) and people. All three are copies of AI Knowledge → Compliance → Security → Code Review Checklist, identical in every Firebase rules and Functions repo. Change that page first, then every copy.

## Commands

All commands run from `functions/`:

```bash
npm run lint          # ESLint (must pass before deploy)
npm run lint:cache    # ESLint with cache (used in CI)
npm run deploy        # Deploy all functions (coach + content)
npm run deploy:coach  # Deploy coach triggers only
npm run deploy:content # Deploy content triggers only
npm run deploy:dev    # Deploy to dev Firebase project
npm run deploy:prod   # Deploy to production
npm run release       # Semantic version bump + GitHub release (release-it)
npm run update        # Check for dependency updates (npm-check-updates)
```

Always run `nvm use` before any commands — the project requires Node 24 (see `.nvmrc`).

## Architecture

This is a **Firebase Cloud Functions analytics aggregation service** for the Aura Health meditation platform. It listens to Firebase Realtime Database events and fans out analytics data across multiple dimensions (content, coach, channel, tag, type, subscription tier) organized by year/month.

### Layer Structure

```
src/index.js          # onInit hook — initializes Firebase, exports all triggers
src/config.js         # Centralized config (memory, timeout, region, log level)
src/context.js        # Per-invocation Context with UUID for distributed tracing
src/logger.js         # Google Cloud Logging wrapper (crit/error/warn/info/debug)
src/services/
  firebase.js         # Firebase Admin SDK init + getDatabase()
src/triggers/
  index.js            # Re-exports all domain triggers
  coach/              # 3 triggers: live event analytics aggregation
  content/            # 2 triggers: meditation listening + feedback analytics
```

### Trigger Pattern

Each trigger follows this structure:

```javascript
const trigger = onValueCreated(
  {ref: '/path/{paramId}', ...options, region},
  async (event) => {
    const context = new Context('triggerName');
    const {paramId} = event.params;
    const data = event.data.val();

    if (!paramId || !data) return;  // Guard clauses first

    try {
      const meta = context.meta({paramId});
      logger.info('Message', meta);
      // ... service calls
    } catch (error) {
      logger.error('Message', {...meta, error});
      throw error;
    }
  }
);
```

### Analytics Update Pattern

```javascript
const snapshot = await db.ref(path).once('value');
const analytics = snapshot.val() || {};

// Nested year/month structure
let year = analytics[currentYear] || {};
let month = year[currentMonth] || {totalListens: 0, ...};
month.totalListens += 1;

await db.ref(path).update(analytics);
```

Use `Promise.all()` for concurrent analytics writes across multiple paths.

### Key Firebase Paths

**Read (trigger sources)**:
- `/liveEvents/{liveEventId}/analytics` — live event analytics data
- `/liveEvents/{liveEventId}/status` — live event status changes
- `/coachLiveEventsAnalytics/{coachId}` — per-coach event aggregation
- `/userListened/{userId}/{listenedId}` — user content listening events
- `/meditationFeedback/{meditationFeedbackId}` — user feedback/ratings

**Write (analytics targets)**:
- `/coachLiveEventsAnalytics/{coachId}/{liveEventId}`
- `/meditationAnalytics/{contentId}`, `/channelAnalytics/{channelId}`
- `/coachAnalytics/{coachId}`, `/coachTypeAnalytics/{coachId}/{type}`, `/coachTagAnalytics/{coachId}/{tag}`
- `/tagAnalytics/{tag}`, `/typeAnalytics/{type}`
- `/analytics/channels/{channelId}`, `/analytics/tracks/{contentId}`
- `/meditations/{contentId}/analytics`, `/channels/{channelId}/analytics`

### Logging

Log levels map to Google Cloud severity values: `debug=100`, `info=200`, `warn=400`, `error=500`, `crit=600`. Default threshold is 400 (warn). Always pass `context.meta({...})` as the second argument to attach `functionName` and `transaction` for distributed tracing.

### Adding a New Trigger

1. Create file in `src/triggers/{domain}/triggerName.js`
2. Export trigger with named export
3. Add to `src/triggers/{domain}/index.js`
4. Add deploy script to `functions/package.json`
5. Add deploy step to `.circleci/config.yml`

## Release Process

CI/CD is on CircleCI: test → deploy:dev → deploy:prod, triggered by `v*` git tags. Use `npm run release` (from `functions/`) which runs release-it to bump the version in `package.json`, create a git tag, and publish a GitHub release.
