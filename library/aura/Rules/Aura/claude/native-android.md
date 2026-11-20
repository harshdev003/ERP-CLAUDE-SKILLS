---
description: Android native conventions for Aura (Gradle, jetifier, release bundle)
globs: android/**
alwaysApply: false
---

# Native Android

## Firebase config comes from the cask

`android/app/google-services.json` is **not** a file you edit or hand-place. It is
a symlink into `src/cask`, maintained by `scripts/sync-cask.sh switch <dev|prod>`,
so an env change switches Android's native Firebase config the same way it
switches iOS'. Both are gitignored; the cask is the source of truth.

```bash
scripts/sync-cask.sh status          # what the Android build would read right now
scripts/sync-cask.sh switch dev      # repoint it (and the iOS plist, and meals.js)
```

The build asserts this at configure time (`android/app/build.gradle`, just above
`apply plugin: 'com.google.gms.google-services'`) and **fails** if the file the
plugin will read is not the cask's. The gms plugin can't catch it: it validates
the package name and nothing else, so a config for the wrong Firebase *project*
builds perfectly and ships an app whose native SDKs (Auth, FCM, Storage) talk to
that project while `code.firebaseURL` from `meals.js` sends REST traffic
somewhere else. That is exactly what shipped before PROD-4870 — every Android dev
build authenticated against **production** Firebase Auth.

So if a build stops with a `google-services.json` message, the fix is a
`sync-cask.sh switch`, never a copied-in file. Adding a new env's config means
putting the console export at
`src/cask/<env>/google-services.<env>.json` — full detail in
[.claude/skills/sync-cask/SKILL.md](../skills/sync-cask/SKILL.md).

## Building

```bash
# Debug build + install on a connected device/emulator
npm run android

# Debug APK from CLI
cd android && ./gradlew assembleDebug

# Release bundle (used for Play Store; runs the deploy script after)
npm run android:bundleRelease
```

## Jetifier

`npx jetify` runs automatically on `postinstall` (see `package.json`). It rewrites legacy support-library references in `node_modules/` to AndroidX. **Don't disable it** — several pinned RN modules still ship as legacy.

If you hit `Could not resolve com.android.support:...` during a Gradle build, run `npx jetify` manually then retry.

## Common Failures + Recovery

- **Stale Gradle cache** → `cd android && ./gradlew clean && cd ..`
- **Broken merge after a pull** → delete `android/build/`, `android/app/build/`, then rebuild.
- **`Duplicate class` errors** → almost always a transitive native module conflict. Check the recently added module's docs for `exclude` instructions.

## Don't Touch Without Reason

- `android/app/build.gradle` — version bumps and ABI splits live here. Touch only when shipping a release.
- `android/app/src/main/AndroidManifest.xml` — adding permissions changes the Play Store listing. Coordinate before adding any new permission.
- `MainApplication.{java,kt}`, `MainActivity.{java,kt}` — autolinking handles most native module registration. Manual entries are usually a sign something is misconfigured.

## Versioning

`versionCode` and `versionName` in `android/app/build.gradle` are bumped per release. Don't bump them in feature PRs.

## Native Module Linking

React Native uses autolinking. If a module needs manual linking, document it in the PR description so reviewers know.
