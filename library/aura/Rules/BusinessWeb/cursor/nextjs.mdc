---
description: Build, env and runtime rules shared by every Aura Next.js web app
globs:
alwaysApply: true
---

# Next.js web app rules

> **Shared by every Aura Next.js web app.** This file is identical in AIWeb, BusinessWeb, CRMWeb, CoachWeb, InvestWeb and UserWeb. A rule for one app goes in that app's `repo-specific.md`, not here. Security rules are in `security.md`.

## Environment and build
- **`NEXT_PUBLIC_*` values are inlined at build time.** Setting one on `next start` or `node server/index.js` changes nothing. To run against another API, rebuild with the variable set and delete `.next` first, because pages generated with `fallback` stay cached in `.next` across runs (ENGWEB-334).
- **An env prefix only applies to the command it sits on.** `NODE_ENV=production export X=1 && node app` passes `NODE_ENV` to `export`, not to `node`, in both bash and busybox ash. Write `NODE_ENV=production X=1 node app`, or set `ENV NODE_ENV=production` in the Dockerfile. Why: the CoachWeb and UserWeb `serve` scripts never set `NODE_ENV` in prod (ENGWEB-330).
- **Dev mode is `NODE_ENV === 'development'`, never `NODE_ENV !== 'production'`.** A missing variable then falls back to production instead of `next dev`. Also set `ENV NODE_ENV=production` in the image. Why: CoachWeb stayed out of `next dev` in prod only because the k8s secret set the variable (CoachWeb#930).
- **Never pass `CI` into a Next.js `docker build`** (no `ARG CI`, `--build-arg CI` or `ENV CI`). Gate release-only build steps (source-map upload, build records) on an explicit opt-in arg instead: `ARG BUGSNAG_UPLOAD` in the Dockerfile and `--build-arg BUGSNAG_UPLOAD=true` only from the release job, the signal CRMWeb already uses on Heroku, plus a structure test that both exist and that `CI` is absent. Why: the images run `npm install --production`, so devDependencies such as `typescript` are missing, and `next build` auto-installs missing TypeScript only when it doesn't detect CI. With `CI=true` it fails ("It looks like you're trying to use TypeScript but do not have the required package(s) installed"), which broke the BusinessWeb v1.3.11 and InvestWeb v2.2.9 releases on 2026-10-25. A gate whose signal the release build never receives silently turns the feature off in prod instead (ENGWEB-335, ENGWEB-348). All five Docker apps (AIWeb, BusinessWeb, CoachWeb, InvestWeb, UserWeb) use `BUGSNAG_UPLOAD`, and none passes `CI`. Don't add new `CI` build args.
- **Gate the error-reporting SDK on that same opt-in signal,** not on the release stage or the API key. Every build carries the key, the main `.env` holds the prod one, and the release stage can be unset during `docker build`, so a laptop build looks exactly like prod. Inline a flag from `BUGSNAG_UPLOAD=true` in `next.config.js` `env` (e.g. `BUGSNAG_REPORTING`), and have `onError` drop every event when it's off. Why: one local run put 198 fake events into prod Bugsnag (ENGWEB-336, ENGWEB-349). **Move the runtime gate in the same PR as the build arg,** with a test that pins the two together: Switching only the build arg would silently stop prod error reporting. UserWeb shows the pattern: `shouldReportToBugsnag` in `next.config.js` reads `BUGSNAG_UPLOAD` and inlines `BUGSNAG_REPORTING`, with a test pinning it to the Dockerfile `ARG` (#2507).

## Local runs
- **Never copy the main checkout's `.env` into a worktree to build or run the app.** It carries the prod Bugsnag key, and `next start` with `NODE_ENV=production` reports as release stage `prod`. Use a dev env file, or leave `NEXT_PUBLIC_BUGSNAG_KEY` blank. Why: a local production-build run sent 198 events with laptop paths into prod Bugsnag and looked like a prod incident (ENGWEB-336).
- **Never run a prod-mode build locally.** A prod-mode build can upload source maps and build records for the live version. Build in dev mode to check a change (ENGWEB-334, ENGWEB-335).

## Shutdown
- **The app drains on `SIGTERM`.** Kubernetes sends `SIGTERM` after the Deployment's `preStop` sleep; the server stops accepting connections, finishes in-flight requests and exits. Measured on user-web: a 10 s `preStop` sleep plus a 45 s grace period took refused connections per rollout from about 67 to 0 (ENGWEB-329).
- **When the image `CMD` runs `node` directly, register the `SIGTERM` handler before any async startup.** Node as PID 1 ignores `SIGTERM` when no listener is registered, so a signal during `app.prepare()` waits out the whole grace period. Running `node` or `next` directly instead of `npm run` cut pod stop time from 45 s (SIGKILL under npm) to 12 s. `next start` 13.4 has no `SIGTERM` handler, so keep it under npm; 14.2 calls `server.close` itself (CoachWeb#929).

## Calling Aura services
- **The app never calls an Aura service directly, from the browser, server rendering or the build.** Every call goes web → the app's own backend-for-web (its server proxy, e.g. UserWeb's `/auraServices`) → the service, so the proxy owns tokens and routing. The one exception: when running on Kubernetes, server-side code may call the backend directly. Why: 13 UserWeb call sites had drifted to direct service URLs and skipped the proxy's token handling (Jerome, 2026-10-23; ENGWEB-337, ENGWEB-338). Known gaps: CRMWeb (ENGWEB-339) and AIWeb (ENGWEB-340).

## Static and server-rendered pages
- **A paged `getStaticProps` route with `fallback: 'blocking'` needs two guards.** Every `notFound` return carries `revalidate`, or a transient API failure is cached as a 404 until the next deploy. The page param has a hard cap, or each arbitrary number costs API calls and leaves a cached 404 (UserWeb#2485).
- **Public page props never carry a null URL-looking field** (`url`, `photo`, `picture`, `image`, `video`, `videoUrl` and similar). Leave the key out instead of sending `null`: crawlers follow any URL-looking value in `__NEXT_DATA__`, so `videoUrl: null` became requests to `/pt-br/track/null`. UserWeb filters these with `usableUrlFields` in `src/utils/imageUrl.js` (ENGWEB-351, UserWeb#2512).

## Locales
- Locale codes, URL forms and conversion rules for every layer are canonical in AI Knowledge → Procedures/Engineering/Locales: store and send `pt_BR`, and convert only at the edge (URL `pt-br`, `Intl` `pt-BR`, moment `pt-br`). Build Next's `i18n.locales` from each locale's `urlPathId`, and make any custom server route that takes a locale match its `/pt-br/` prefix too.
