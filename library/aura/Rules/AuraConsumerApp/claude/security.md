---
description: Security and privacy requirements for AuraConsumerApp
globs:
alwaysApply: true
---

# Security & Privacy Requirements

## Sensitive Data

- **Never log PII**: No user emails, names, health data, tokens, or passwords in log statements.
- **Never hardcode secrets**: API keys, tokens, and credentials belong in `.xcconfig` files or Secret Manager — never in source code.
- **Keychain for sensitive storage**: Use the Keychain (via `KeychainService` or Security framework) for tokens, credentials, and session data — never `UserDefaults`.

## Network Security

- All API calls go through `APIService` — never use raw `URLSession` in features.
- Always use HTTPS — no HTTP traffic permitted.
- Validate server certificates; do not disable ATS (App Transport Security).

## Authentication

- Token refresh and session management are handled by `AuthService` — never roll custom auth logic in ViewModels.
- On logout, call `ServiceContainer.rebuildUserServices()` to clear all user-scoped service state.
- Firebase Auth tokens are short-lived — let `AuthService` handle refresh transparently.

## Privacy

- Request permissions only when needed (just-in-time), not at app launch.
- Use `PermissionsService` for all system permission requests — never call `AVCaptureDevice.requestAccess` directly in a View.
- Respect ATT (App Tracking Transparency) — no attribution tracking before post-consent phase.
- Comply with CCPA/GDPR: the two-phase bootstrap ensures no third-party SDKs init before consent.

## Input Validation

- Sanitize and validate all user-generated text before sending to the API.
- Do not use `try!` or force unwrap on data parsed from external sources (API responses, deep links).

## Error Messages

- User-facing error messages must be friendly and non-technical — never expose internal error details, stack traces, or server messages to users.
- Use `LocalizedError.errorDescription` for user-facing strings; log the full error internally.
