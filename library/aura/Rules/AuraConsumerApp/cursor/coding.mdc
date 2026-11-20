---
description: Swift code style guidelines and patterns for AuraConsumerApp
globs:
alwaysApply: true
---

# Code Style Guidelines

## Related Rules

- `comments.md` for comment preservation and context-comment requirements.
- `concurrency-actors.md` for actor-isolation and thread-bound API constraints.
- `data-flow.md` for wrapper ownership and service-to-ViewModel state flow.
- `documentation.md` for headers, DocC, and context-comment expectations.
- `accessibility.md` for the contrast bar the colour roles are measured against, and the type-size floor.

## Formatting

- Use 4-space indentation (Xcode default)
- No unnecessary blank lines between enum cases or between single-line properties
- Trailing commas in multi-line collections
- Prefer single-line closures when readable; use multi-line for complex bodies
- Keep lines under 120 characters (SwiftLint enforced)

## Enum Spacing

Do NOT add blank lines between enum cases:

```swift
// ✅ GOOD
enum LoadingState {
    case error
    case idle
    case loaded
    case loading
}

// ❌ BAD — unnecessary blank lines
enum LoadingState {

    case error

    case idle

    case loaded

    case loading

}
```

## Alphabetical Sorting (MANDATORY)

All code elements MUST be alphabetically sorted within their MARK section:

- **Properties**: sorted within `Public Properties`, `Internal Properties`, `Private Properties`, `Dependencies`
- **Enum cases**: sorted A–Z by case name
- **Switch cases**: must match the alphabetical order of the enum
- **Methods**: sorted within `Public Methods`, `Internal Methods`, `Private Methods`

**Scope rule**: when working on a feature or bugfix, do NOT reorder existing enum cases or switch cases — that is a refactoring PR. New cases/properties must be inserted in alphabetical position.

## Swift Patterns

- Prefer `async/await` over completion handlers or raw Combine for new code.
- Use `Task { }` to bridge sync contexts to async — always with `[weak self]` when referencing `self`.
- Use `guard let` for early exits instead of nested `if let`.
- Prefer `struct` over `class` for models; use `class` for ViewModels and Services.
- Conform domain models to `Codable` and `Sendable`.
- Put a model's initializers in the **type body** (constructors section) — not in a separate `extension`. An explicit memberwise `init(...)` with `= nil` defaults serves both full-field callers and single-field callers (e.g. `UpdateUserProfileRequest(givenName:)`) without an extension initializer.
- Avoid force unwrap (`!`) and force cast (`as!`) — SwiftLint enforced.

## Concurrency

- Follow `concurrency-actors.md` for actor isolation and thread-bound API guidance.
- Follow `data-flow.md` for state ownership, stream boundaries, and wrapper selection rules.
- Mark domain models `Sendable` for safe cross-actor use.
- Use `await MainActor.run { }` only when bridging from non-main isolated contexts.
- Capture `[weak self]` in escaping closures and long-lived tasks when needed to prevent retain cycles.

```swift
// ✅ GOOD - Task with weak self
Task { [weak self] in
    guard let self else { return }
    await self.loadData()
}
```

## Full-Screen Backgrounds

A full-screen background **image must never inflate its container's layout.** A bare
`Image(...).resizable().aspectRatio(contentMode: .fill)` reports its *intrinsic, oversized*
dimensions to the parent. On the wider iPad canvas a portrait-aspect asset (e.g.
`chatBackground`) fills the width and forces a much taller height; if that image is a `ZStack`
sibling of the screen's content, the inflated height propagates up the `ZStack` and pushes the
content — and any pinned-bottom CTA — off-screen. This shipped an App Store rejection once
(iPad onboarding paywall rendered with the Continue button below the screen edge); do not
reintroduce it.

Use one of these three safe compositions for a full-screen background image:

```swift
// ✅ GOOD — `.background()` modifier: the background can never affect the host's size.
content
    .background(
        Image("chatBackground").resizable().aspectRatio(contentMode: .fill).ignoresSafeArea()
    )

// ✅ GOOD — explicit bounded frame (e.g. from a GeometryReader) + clip.
Image("chatBackground")
    .resizable().aspectRatio(contentMode: .fill)
    .frame(width: geo.size.width, height: geo.size.height)
    .clipped()

// ✅ GOOD — flexible Color.clear host + overlay + clip. Reports the *offered* size,
// so it fills the screen without inflating siblings. Use inside a ZStack.
Color.clear
    .overlay(Image("chatBackground").resizable().aspectRatio(contentMode: .fill))
    .clipped()
```

```swift
// ❌ BAD — bare aspect-fill as a ZStack sibling of scrollable / pinned-CTA content.
ZStack {
    Image("chatBackground").resizable().aspectRatio(contentMode: .fill).ignoresSafeArea()
    VStack { ScrollView { ... }; continueButton }   // CTA gets pushed off-screen on iPad
}
```

**`.frame(maxWidth: .infinity, maxHeight: .infinity)` does NOT contain an aspect-fill image** —
an infinite max still admits the image's oversized intrinsic height. Only an explicit finite
frame, a `Color.clear` overlay host, or the `.background()` modifier bounds it.

Spacer-centered content (`VStack { Spacer(); content; Spacer() }`) happens to survive an
inflating sibling background because the extra height is absorbed symmetrically and the content
stays centered — but don't rely on that; prefer one of the three safe compositions above so the
screen is correct regardless of its content shape.

## Fonts & Typography

The text role is **SF Pro — the system font — drawn through Apple's Dynamic Type text styles**; the display role is a bundled custom face. Both are reached through central helpers in `Services/Theme/Global/Fonts.swift`. **Never** call `.system(...)`, `.systemFont(...)`, or `.custom("SomeFont", size:)` at a call site — route through the helpers/tokens below.

| Role | Helper(s) | Backing font |
|------|-----------|--------------|
| Functional UI / body text | `Font.text(_:weight:)`, taking a `Font.TextStyle` — UIKit: `UIFont.text(ofSize:weight:)` | SF Pro (the system font) |
| Display headings / titles | `Font.display(size:)`, `Font.displayLight(size:)`, `Font.displayLightItalic(size:)` | ABC Otto |

- **The text role takes a text style, never a point size.** `Font.system(size:)` does not scale with Dynamic Type, and SF Pro cannot be bundled as a custom face (Apple's font licence forbids embedding it), so the only scaling form is the text style: `.text(.subheadline, weight: .medium)`. The style carries both the size and the scaling curve, and it honours the `.dynamicTypeSize` clamps. Apple's ladder at the default size: largeTitle 34 · title 28 · title2 22 · title3 20 · headline 17 · body 17 · callout 16 · subheadline 15 · footnote 13 · caption 12 · caption2 11.
- **Single source of truth**: every custom font-name string lives in the `FontName` enum in `Fonts.swift`. Helpers and call sites reference *roles*, not typefaces.
- **To swap the display family, edit only the `display*` `FontName` values** (point `displayRegular` / `displayLight` / … at the new font's PostScript names). The display API is **role-named** (`display*`, not the typeface) precisely so a swap touches **zero** call sites, tokens, or helpers. Verify the new font's exact PostScript names before wiring them in (e.g. `fontTools` or `strings` on the `.otf`) — a wrong name silently falls back to the system font.
- Also register/unregister the `.otf`s in `Info.plist` `UIAppFonts` and add/remove the files under `Resources/Fonts/`, then `make proj`.
- `TextStyle` plain cases (`.body`, `.title`, …) render in SF Pro; `*Serif` cases render in the display face. `Fonts.*` tokens (`calloutRegular`, `subheadlineSemibold`, `displayLight28`, …) are pre-built style/weight pairings — prefer them or the helpers.
- `Font.textFixed(_:weight:)` is the one non-scaling form — for the sub-11 pt decorative text, the hero glyphs (48 pt and up) and a symbol sized from the geometry it sits in, where no style exists to land on.
- Weights pass straight through to SF Pro — `.semibold` is semibold (600) and `.bold` is bold (700). On the display helpers, always pass `relativeTo:` so the custom size scales with Dynamic Type.

```swift
// ❌ BAD — hardcoded system / font-name strings, or a point size, at the call site
Text("Hi").font(.system(size: 16, weight: .medium))
Text("Hi").font(.custom("Inter-Medium", size: 16))
Text("Hi").font(.text(16, weight: .medium))      // a size cannot scale — name the style

// ✅ GOOD — role-based central helpers
Text("Hi").font(.text(.callout, weight: .medium))
Text("Heading").font(.display(size: 28))
```

## Colour

Every colour comes from `Services/Theme/Global/Colors.swift`. **Never a literal `Color(hex:)`, `Color(red:green:blue:)` or `.black` / `.white` at a call site** — a literal is a colour nobody can find again when design changes it. (The app still carries ~111 literal `Color(hex:)` outside the theme; that is debt to migrate, not a precedent to follow.)

### On a sky surface, name the role

Aurie's screens sit on the time-of-day sky (`SkyState`), so a foreground colour depends on which sky is behind it. The roles mirror Figma's `Environment` collection and resolve per sky:

| Role | Use |
|------|-----|
| `skyState.textPrimary` | body copy, titles, controls |
| `skyState.textSecondary` | supporting copy |
| `skyState.textTertiary` | large or non-essential text only (it is tuned to 3:1, not 4.5:1) |
| `skyState.textInverted` | text on a surface filled with `textPrimary` |
| `skyState.figurePrimary` … `figureQuaternary` | the glyph beside the text, down to hairlines and glass fills |

**Never hand-write the branch.** `prefersLightForeground ? .white : Colors.blackSecondary` is a role spelled out longhand, and it is a place the contrast has to be re-measured by hand — the roles carry per-sky opacities set to the least that clears the bar on that sky's *worst* gradient stop. 67 of those branches were replaced by roles at once; don't start the next one.

A component that is not on the sky but carries its own light/dark Bool calls the static form, `Colors.textPrimary(lightForeground:)`. A screen that is off the environment entirely — settings, the paywall — takes `Colors.appBgDefaultColor`.

```swift
// ❌ BAD — a literal, and a hand-written sky branch
Text(title).foregroundColor(Color(hex: "#1C1B1F"))
Text(title).foregroundColor(skyState.prefersLightForeground ? .white : Colors.blackSecondary)

// ✅ GOOD — the role resolves for the sky, and carries its own contrast
Text(title).foregroundColor(skyState.textPrimary)
Text(caption).foregroundColor(skyState.textSecondary)
```

### The sky's own colours are a design decision

`SkyState.stops` holds three gradient stops per sky. Changing one is not a tweak: content scrolls across all three, so a sky needs one foreground that passes on **every** stop. Two stops have been moved for exactly that reason — dawn's top (`#2E3D6E` → `#8092CA`) and dusk's horizon (`#8C6980` → `#705466`), both because dark or secondary copy could not reach the contrast bar on them. Measure before proposing a change, and mirror it in Figma's `Environment` collection.

## Corner Radius

The scale, as design defines it: **XS 8 · S 12 · M 18 · L 20 · XL 28 · Full**, declared in `BorderRadius.swift`. Pick the step by element size — chips XS, controls and compact cards S, grouped content M, larger panels L, sheets XL.

- **Always `style: .continuous`.** The scale was chosen against a continuous curve; the default circular corner draws a different shape at the same number.
- **A pill is `Capsule()`, never a large radius.** A circular corner clamps to half the shorter side, so `RoundedRectangle(cornerRadius: 28)` on a 48 pt control *used to* render as a pill by accident. A continuous corner does not clamp that way, so the same code now draws a squircle — 41 controls had to be corrected once for relying on the accident. If the shape is meant to be a pill, say so.
- **Never a literal.** The twelve that survive in the app are annotated one by one (Figma's 30s on the Profile cards, an iPad-sensitive 156, a device-corner 36, two values derived from a parent's radius); each carries a comment saying why, and none is precedent.
- **`.cornerRadius(_:)` is gone from this repo** — all 82 calls were removed, and Apple deprecated it. Use `RoundedRectangle(cornerRadius:style:)` or a shape.

```swift
// ❌ BAD — literal, circular corner, and a pill faked with a radius
.clipShape(RoundedRectangle(cornerRadius: 16))
.clipShape(RoundedRectangle(cornerRadius: 28, style: .continuous))   // on a 48pt control

// ✅ GOOD
.clipShape(RoundedRectangle(cornerRadius: BorderRadius.borderRadius18, style: .continuous))
.clipShape(Capsule())
```

## Spacing

Padding, gaps and offsets come from `Spacing.*` (`Services/Theme/Global/Spacing.swift`): 0, 2, 4, 6, 8, 10, 12, 16, 20, 24, 32, 40, 48, 56, 64, 72. Pick the nearest step rather than inventing a value; a one-off number in a layout is how a scale stops meaning anything. A value genuinely off the scale — a frame matched to an asset, a device-specific inset — takes a comment saying what it is matched to.

## Accessibility Identifiers (MANDATORY)

Every SwiftUI View component **must** include an `accessibilityIdentifier` for UI testing support:

- Add `.accessibilityIdentifier("{feature}_{component}")` to all interactive elements (buttons, text fields, toggles, etc.).
- Add `.screenAccessibilityIdentifier("{feature}_screen")` to the root container of each screen-level View — not `.accessibilityIdentifier`, which overrides the ids of the controls inside (see `accessibility.md` → Screen-Level Identifiers).
- Follow the `{feature}_{component}` naming format (e.g., `"journal_deleteButton"`, `"home_screen"`).
- See `naming.md` for the full identifier naming convention.

```swift
// ✅ GOOD - Screen with accessibility identifiers
struct JournalView: View {
    var body: some View {
        VStack {
            TextField("Title", text: $title)
                .accessibilityIdentifier("journal_titleField")
            Button("Save") { save() }
                .accessibilityIdentifier("journal_saveButton")
        }
        .screenAccessibilityIdentifier("journal_screen")
    }
}

// ❌ BAD - No accessibility identifiers
struct JournalView: View {
    var body: some View {
        VStack {
            TextField("Title", text: $title)
            Button("Save") { save() }
        }
    }
}
```

## File Structure Order (MANDATORY MARK sections)

```
// MARK: - Public Properties
// MARK: - Enums
// MARK: - Internal Properties
// MARK: - Private Properties
// MARK: - Dependencies
// MARK: - Listeners
// MARK: - Initialization
// MARK: - Public Methods
// MARK: - Internal Methods
// MARK: - Private Methods
```

## Reviewability — small files, reuse, safe renames (MANDATORY)

Reviewers reject large files and duplicated logic. Keep every unit small, and prefer what already exists over what you'd add.

### Split large types by concern

A `ViewModel` or `Service` that grows past **~350 lines** must be split into concern-based extension files — one cohesive concern per `{Type}+{Concern}.swift` file (the same pattern `service-patterns.md` mandates for services). The core file keeps state + init + the primary flow; each other concern moves to its own file. Do this **as you write it**, not as a later cleanup — a 600-line view model is a review reject.

- Declare members the extensions need as **`internal`**, not `private` — Swift `private` is file-scoped and unreachable from another file. `@Published` is already internal.
- xcodegen globs the folder, so a new `{Type}+{Concern}.swift` under the feature's `ViewModels/` (or `Services/{Domain}/`) is picked up by `make proj` — no `project.yml` edit needed.

```
// ❌ BAD — one 660-line EditPracticesViewModel.swift holding every concern
// ✅ GOOD — one small core + concern files
EditPracticesViewModel.swift               // state, init, load
EditPracticesViewModel+CustomPractice.swift
EditPracticesViewModel+Reminders.swift
EditPracticesViewModel+Analytics.swift
```

### Reuse before you create

Before adding a new type, component, or helper, **grep for an existing one** — the repo already has shared bottom sheets, cards, pickers, formatters, and domain/error enums. Duplicating them is a review reject. When two screens are nearly identical (e.g. a **create** and an **edit** of the same thing), share one view model or extract the common logic into a shared type — don't fork two parallel view models that then drift apart.

### Check the name before you rename

Before renaming a type, **grep the target name across the codebase first** to confirm it isn't already taken. A rename that collides with an existing type is a build break, not an improvement (e.g. renaming a UI picker model to a name already used by a backend DTO). If a reviewer's suggested name is already used by a different type, keep the current name (or pick a distinct one) and say why — don't force a colliding rename.
