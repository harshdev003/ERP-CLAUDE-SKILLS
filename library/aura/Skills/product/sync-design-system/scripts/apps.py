#!/usr/bin/env python3
"""Per-app configuration for the design-system generator.

Adding an app means adding an entry here — not editing the generator. Aurie is
complete; Aura is stubbed with the paths it will have, so `--app aura` fails
with a clear message rather than silently producing an Aurie bundle.
"""
import os

HOME = os.path.expanduser("~")

APPS = {
    "aurie": {
        "displayName": "Aurie",
        "repo": f"{HOME}/Development/Aura/Mobile/AuraConsumerApp",
        # Swift theme files, in the order they should appear in the manifest.
        "themeDir": "AuraConsumerApp/Services/Theme/Global",
        "fontsDir": "AuraConsumerApp/Resources/Fonts",
        "screenshots": "AuraConsumerAppSimTests/knowledge/screenshots",
        # Claude Design project. Set once the project exists so `files` can
        # remind the operator which project the bundle belongs to.
        # The standalone Claude Design project (DesignSync), kept until it closes;
        # the Design System artifact is where the system is published now.
        "designProjectId": "963e5951-4785-4308-adfa-2ec8bef9b69d",
        "designSystemArtifact": "https://claude.ai/artifact/U7eLisVGjZm4JSN989MZrD",
        "designProjectName": "Aurie",
        # AI Knowledge -> Design/Accessibility Baseline. The generator reads the
        # bar from the JSON code block on that page rather than holding a copy,
        # so the design system and the knowledge base cannot disagree. Drop this
        # key to build without the accessibility cards.
        "a11yBaseline": "3e875e23-af20-8108-8bc2-db948489d345",
        # Which font files to embed, and the CSS weight/style each maps to.
        # Only the display face is embedded. The body face is SF Pro, the system
        # font: Apple's licence forbids embedding it, so cards name the system
        # stack and the reader's own Mac supplies the face.
        "fonts": [
            ("Display", "ABCOtto-Light.otf", 300, "normal"),
            ("Display", "ABCOtto-LightItalic.otf", 300, "italic"),
            ("Display", "ABCOtto-Regular.otf", 400, "normal"),
        ],
        # The design file itself — upstream of everything here. Figma is where the
        # palette, the gradients, the icon system and the `Environment` collection
        # are decided; the app mirrors it, and this system is generated from the
        # app. So a token that looks wrong here is usually right in Figma first.
        #
        # Recorded as config rather than prose so the generator can link it and the
        # designer agent can open it, instead of every reader being told Figma is
        # the source of truth and left to find out where.
        "figmaFile": {
            "key": "RuQbR69spDVb2hdKoIifLD",
            "name": "Aurie - 2026",
            "url": "https://www.figma.com/design/RuQbR69spDVb2hdKoIifLD/Aurie---2026",
            "owner": "Alex",
        },
        # Rendered at this width; 3x captures are far larger than a card needs.
        "screenshotWidth": 420,
    },
    "aura": {
        "displayName": "Aura",
        "repo": f"{HOME}/Development/Aura/Mobile/Aura",
        # React Native: `extract_rn.py` reads the 4.0 redesign layer. `styles/globals` is
        # the older layer and `styles/aurie` the in-app Aurie components' own theme.
        "format": "rn",
        "themeDir": "src/styles",
        # Composed styles and re-exports, not tokens.
        "themeSkip": ["redesign/styleSheet.js", "redesignDark/index.js", "redesignLight/index.js"],
        "fontsDir": "assets/fonts",
        # The 4.0 ladder's two families, both freely redistributable (Bitstream's
        # Charter grant; SIL OFL for Nunito Sans). The app names faces by PostScript
        # name, which is also each file's name.
        "fonts": [
            ("Charter", "CharterRegular.ttf", 400, "normal"),
            ("Charter", "CharterItalic.ttf", 400, "italic"),
            ("Nunito Sans", "NunitoSans-Light.ttf", 300, "normal"),
            ("Nunito Sans", "NunitoSans-Regular.ttf", 400, "normal"),
            ("Nunito Sans", "NunitoSans-Medium.ttf", 500, "normal"),
            ("Nunito Sans", "NunitoSans-SemiBold.ttf", 600, "normal"),
            ("Nunito Sans", "NunitoSans-Bold.ttf", 700, "normal"),
        ],
        "screenshots": "AuraSimTests/knowledge/screenshots",
        # The capture store's variant axes (its variants.json): one design, two themes.
        # Captures without a suffix predate 4.0 and are not shown.
        "screenVariants": ["_v4_dark", "_v4_light"],
        # Standalone Claude Design closes by 2026-12-14: Aura's system is a Design System
        # artifact, filled by to_design_system.py.
        "designSystemArtifact": "https://claude.ai/artifact/GLTSm8cu9WULXdVqAU2LZH",
        "designProjectId": None,
        "designProjectName": "Aura",
        "figmaFile": None,        # not identified
        "screenshotWidth": 420,
    },
}


def resolve(name: str, repo: str = "") -> dict:
    """The app's config, with `repo` (a checkout or worktree path) overriding the
    configured one when given. A scheduled run harvests in its own worktree, so it
    must be able to point the generator there rather than at the main checkout."""
    if name not in APPS:
        raise SystemExit(f"unknown app '{name}' — known: {', '.join(sorted(APPS))}")
    cfg = dict(APPS[name])
    cfg["key"] = name
    if repo:
        cfg["repo"] = os.path.abspath(os.path.expanduser(repo))
    if not cfg["themeDir"]:
        raise SystemExit(
            f"{cfg['displayName']} has no theme directory configured yet.\n"
            f"Find where it declares its colors/fonts/spacing, set `themeDir` in "
            f"apps.py, and confirm the declaration forms match what "
            f"extract_tokens.py parses — Aura is a different codebase and may not "
            f"use the same `static let` shapes.")
    if not os.path.isdir(cfg["repo"]):
        raise SystemExit(f"repo not found: {cfg['repo']}")
    return cfg
