---
description: Rules that apply to UserWeb only
globs:
alwaysApply: true
---

# UserWeb rules

Rules for this repo only. The checklist shared by every web app is `security.md`, and the Next.js build and runtime rules shared by every web app are in `nextjs.md`; both are identical in each web repo, and a general rule goes there, not here.

- **A profile loaded with `usePageQuery({ fetchUserFromQuery: true })` is read-only.** Those pages (CRM push links to `/your-plan`, `/subscribe/<pricing>?userId=`) are not signed in as that user, so the RTDB rules deny any browser write to that profile. Persist anything for that user through a server call that checks the caller. Why: `/subscribe/[pricing]` wrote `webYearlyPriceVariant2` to the URL user and was denied in a loop (ENGWEB-326).
- **A model function that needs auth checks `Auth.getUserAuthToken()` first.** When it returns null, return the signed-out result without making the request. Why: `checkExistingCard()` sent `Bearer null` from crawler-rendered coach pages, about 100 403s a day, which set off PaymentService and UserService alerts (ENGWEB-327, #2473).
- **VideoPlayer and other media errors log plain fields:** `MediaError.code`, `networkState`, `readyState` and the asset file name, never `new Error(...)` or the signed URL (ENGWEB-324).
- **Check a build locally with `NEXT_PUBLIC_MODE=DEV next build`, never `PROD`.** `next.config.js` adds the Bugsnag build reporter and source-map uploader whenever `isProdMode()` is true, so a local prod-mode build uploads source maps (with `--overwrite`) and build records for the live version. Why: one local build overwrote 160 maps for 4.48.5 (ENGWEB-334; moving that upload to CI only is ENGWEB-335).
- **List pages fetch their own slice of `/contents/list`.** The Aura API takes `page` and `limit`, and the paging is stable (`page=1, limit=500` returns items 500–999 of the full list, for meditations and music alike). Never fetch the whole catalogue to render one page (UserWeb#2485).
