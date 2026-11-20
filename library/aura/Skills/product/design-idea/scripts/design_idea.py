"""Mechanical half of the design leg: an idea in, Current-vs-Proposed screenshots out.

  render   Compose one or more TO-BE variants onto a real capture of the screen and
           emit a labelled comparison sheet (Current | A | B | …).
  attach   Put the rendered files on the idea's Notion row (Screenshot property), where
           share-ideas picks them up for the Gate 1 thread.

Why the TO-BE is *re-stacked from the real capture* rather than drawn from scratch
-------------------------------------------------------------------------------------
A mockup drawn in the wrong typeface on an approximated gradient reads as a sticker and
gets pulled before review — that is what happened on the first pilot. So every pixel that
already exists on the screen (orb, title, tagline, disclaimer, buttons, background) is
cropped from the capture itself, and only the *new* element is rendered — with the app's
own fonts and the app's own glyphs. When the new element needs room, the existing bands
move the way SwiftUI would move them: the top `Spacer()` absorbs it, the buttons stay
anchored to the safe area.

Why the copy is fixed and cited
-------------------------------
A claim in a mockup is a claim we ship. The laurel badge copy below is the string in
`PackagePaywallView.socialProofBadge` and the headline of aurie.ai/subscribe — Aura's
credentials, attributed to Aura. Free-text lines are allowed but every variant carries a
`source=` so the reviewer can see where a claim came from. Nothing here invents a number.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Any, Optional

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageStat
except ImportError as e:  # pragma: no cover
    raise SystemExit("design_idea requires Pillow. Install with: pip install pillow") from e

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "..", "references", "assets")

# The iOS app checkout — the display face lives there and is not vendored (ABC Otto is a
# licensed trial). Override with --app-root or AURA_APP_ROOT.
DEFAULT_APP_ROOT = os.path.expanduser("~/Development/Aura/Mobile/AuraConsumerApp")
FONT_DIR = "AuraConsumerApp/Resources/Fonts"
FONTS = {
    "display": f"{FONT_DIR}/ABCOtto-Regular-Trial.otf",
    "display-light-italic": f"{FONT_DIR}/ABCOtto-LightItalic-Trial.otf",
    # Fonts.swift `displayLight` — the package paywall headline (`displayLight28`).
    "display-light": f"{FONT_DIR}/ABCOtto-Light-Trial.otf",
}

# The body face is **SF Pro — the system font**, so it is not in the app checkout and
# never will be: Apple's licence forbids embedding it, which is why the app reaches it
# through `Font.text(_:weight:)` rather than a bundled file. Two ways to draw it here,
# tried in order:
#
#   1. `/Library/Fonts/SF-Pro-Text-*.otf` — Apple's download, if it is installed. One
#      file per weight, so the weight is exact.
#   2. `/System/Library/Fonts/SFNS.ttf` — the system font every Mac has. It is variable,
#      so the weight is selected by named instance ("Regular", "Medium", "Semibold").
#
# Off a Mac neither exists, and `font()` refuses with `SystemExit` rather than letting
# the imaging library raise.
SF_PRO_INSTALLED = "/Library/Fonts/SF-Pro-Text-{}.otf"
SF_PRO_SYSTEM = "/System/Library/Fonts/SFNS.ttf"
BODY_WEIGHTS = {"body-regular": "Regular", "body-medium": "Medium", "body-semibold": "Semibold"}

# Captures are 3x (iPhone 17 Pro). Point sizes from Fonts.swift are multiplied by this.
SCALE = 3
# Colors.blackSecondary — the welcome screen's foreground tint.
TINT = (0x1C, 0x1B, 0x1F)
# Colors.Black — the label colour PrimaryButton draws on its gradient.
BLACK = (0x1C, 0x1C, 0x1F)
# How far inside PrimaryButton's accessibility frame the mask cuts, in px at 3x. The
# drawn capsule ends ~4px inside the frame, and its anti-aliased rim blends with the
# donor's sky for a few px more — at 4px a darker crescent of welcome sky still showed
# at each tip on the value-props render. 8px cuts through solid gradient.
PILL_FRAME_INSET = 8
# Colors.grayMain — `textPrimaryMain`, the ink of the settings rows.
GRAY_MAIN = (47, 50, 55)
# The foreground roles, as opacities of the tint — Colors.swift: textSecondary is
# `textPrimary.opacity(0.75)`, textTertiary `0.55`; the welcome disclaimer draws its
# tint at `0.5` (WelcomeScreenContent.swift).
ROLE_ALPHA = {"primary": 255, "secondary": 191, "tertiary": 140, "disclaimer": 128}

# The six skies — AurieEnvironment.swift: `SkyState.stops` (top → horizon, evenly
# spaced) and `haloColor`, the base every environment screen paints under its blur;
# `light_fg` is `prefersLightForeground`, `secondary` the sky's `secondaryOpacity`
# (Colors.swift), the least that reads 4.5:1 on its worst stop. A shared card is drawn
# from these rather than sampled off a blurred capture, so it carries the sky clean.
SKIES: dict[str, dict[str, Any]] = {
    "dawn":      {"stops": [(0x80, 0x92, 0xCA), (0xB6, 0x86, 0xA0), (0xF5, 0xB6, 0x90)],
                  "halo": (0xFF, 0xEE, 0xDC), "light_fg": False, "secondary": 0.86},
    "morning":   {"stops": [(0x7C, 0xAB, 0xDB), (0xC0, 0xD7, 0xE6), (0xF1, 0xE5, 0xC8)],
                  "halo": (0xFF, 0xFA, 0xEC), "light_fg": False, "secondary": 0.76},
    "midday":    {"stops": [(0x5F, 0x9E, 0xCC), (0x9F, 0xC2, 0xDB), (0xCC, 0xDD, 0xE3)],
                  "halo": (0xF2, 0xF8, 0xFF), "light_fg": False, "secondary": 0.83},
    "afternoon": {"stops": [(0x9E, 0xB2, 0xC8), (0xC4, 0xB6, 0xB8), (0xE2, 0xB6, 0xA4)],
                  "halo": (0xFF, 0xEA, 0xD8), "light_fg": False, "secondary": 0.75},
    "dusk":      {"stops": [(0x46, 0x3E, 0x67), (0x67, 0x55, 0x7C), (0x70, 0x54, 0x66)],
                  "halo": (0xFF, 0xDC, 0xD2), "light_fg": True, "secondary": 0.75},
    "night":     {"stops": [(0x0C, 0x0F, 0x22), (0x18, 0x1D, 0x3A), (0x2E, 0x33, 0x55)],
                  "halo": (0xDC, 0xE0, 0xF0), "light_fg": True, "secondary": 0.75},
}
# `Figure/Quaternary` — the hairline role: `textPrimary` at 12 % on the light-foreground
# skies, 10 % on the others (Colors.swift).
HAIRLINE_ALPHA = {True: 31, False: 26}
# `AurieWordmark` draws the `aurieLogo` asset as a template image at 23pt (73×23) in the
# headers; a card signs itself a little larger. Rasterized from the app's PDF by `wordmark`.
WORDMARK_ASSET = os.path.join(ASSETS, "aurie_wordmark.png")


def sky_fg(sky: str) -> tuple[int, int, int]:
    """`SkyState.textPrimary` — white on the light-foreground skies, `blackSecondary` else."""
    return (255, 255, 255) if SKIES[sky]["light_fg"] else TINT

# The one piece of social-proof copy we already publish, verbatim, and where it lives.
# The wording is aurie.ai/subscribe's (Jerome's call for the welcome badge); the in-app
# paywall ships the same three lines with "8M+ Members" and no ampersand.
LAUREL_TEXT = "Created by Aura,\nBest of Apps Winner\n& 8M Members"
LAUREL_SOURCE = ("aurie.ai/subscribe headline “Created by Aura, Best of Apps Winner & 8M "
                 "Members”; PackagePaywallView.swift socialProofBadge (“8M+ Members”)")


# ---------- screen models ----------------------------------------------------
#
# Pixel regions measured on a real capture (1206×2622, iPhone 17 Pro at 3x). Two kinds
# of geometry:
#
#   * a **stack** screen (welcome) has movable bands: `hero` is the orb + title + tagline
#     block, which SwiftUI lets float up when something below it needs room (the top
#     Spacer shrinks); `buttons_top` is fixed — the CTA sits on the safe area and never
#     moves. The stack slots `top` / `mid` / `bottom` work on those bands.
#   * every screen has named **regions** — rectangles (x0, y0, x1, y1) around what is
#     already there — which the region ops address: `replace:<region>` (repaint, then
#     place), `at:<region>` (overlay), `after:<region>` (insert below, pushing the rest
#     down) and `row:<group>` (grow a list group by one row). A **list** screen (settings)
#     grows downwards; a **fixed** screen (value props, affirmation) only swaps or overlays.
#
# `clones` are bands the renderer copies out of the capture to build a new element in the
# app's own chrome — a settings row, a group card — rather than drawing a look-alike.
# Each is (region, cut, row_cy): the band, how much of it is inserted, and where the row's
# centre line sits inside it. Everything here is read off the XCTest hierarchy dump
# beside each capture (`knowledge/screenshots/hierarchy/<key>.txt`, points × 3) —
# re-measure when a screen's layout changes.

Region = tuple[int, int, int, int]


@dataclass(frozen=True)
class Screen:
    size: tuple[int, int]
    bg_sample: tuple[int, int]                     # a y-range that is pure background (for fills)
    layout: str = "stack"                          # stack | fixed | list
    hero: Optional[tuple[int, int]] = None         # stack: y-range of the movable orb+title+tagline
    disclaimer: Optional[tuple[int, int]] = None   # stack: y-range of the legal text (movable)
    buttons_top: Optional[int] = None              # stack: top edge of the primary CTA — anchored
    top_zone: Optional[tuple[int, int]] = None     # stack: empty background above the hero
    pad: int = 30                                  # breathing room around an inserted element
    regions: dict[str, Region] = field(default_factory=dict)
    clones: dict[str, tuple[Region, int, int]] = field(default_factory=dict)
    home_zone: int = 100                           # px at the bottom that never move (home indicator)
    bg_column: Optional[int] = None                # a content-free x: fill each row from it (exact sky gradient)
    fade: Optional[tuple[int, int]] = None         # rows where a frosted fade dissolves what scrolls under it
    footer: Optional[tuple[int, int]] = None       # list: y-range of a footer pinned under a flexible Spacer
    pre_consent: bool = False                      # drawn before the age-gate consent: no experiment can bucket or expose here

    def region(self, name: str) -> Region:
        if name == "screen":
            return (0, 0, self.size[0], self.size[1])
        if name in self.regions:
            return self.regions[name]
        if name in self.clones:
            return self.clones[name][0]
        known = sorted(self.regions) + [f"{k} (clone)" for k in sorted(self.clones)]
        raise SystemExit(f"unknown region {name!r}; this screen has: {', '.join(known)}")


SCREENS: dict[str, Screen] = {
    # Welcome: orb → "Hi, I'm Aurie" → tagline → legal disclaimer → Get Started → Login.
    # The disclaimer wraps to three lines in SF Pro (its first line starts at 567.3pt =
    # 1702px), so the background sample sits above it — a sample band that catches a line
    # of text paints that line wherever it fills, which is how an erased disclaimer once
    # came back as ghost copies of itself.
    "welcome": Screen(
        size=(1206, 2622),
        bg_sample=(1562, 1690),
        layout="stack",
        pre_consent=True,   # consent is the Get Started / Login tap; ExperimentService and Mixpanel start after it
        hero=(430, 1562),
        disclaimer=(1702, 1935),
        buttons_top=1985,
        top_zone=(190, 430),
        regions={
            "disclaimer": (0, 1702, 1206, 1935),   # the ALL-CAPS legal text and its two links
            "cta": (96, 2004, 1110, 2172),         # Get Started — PrimaryButton, 338×56pt
            "login": (96, 2196, 1110, 2364),
            "footer": (0, 2400, 1206, 2540),       # empty, between Login and the home indicator
            # Two empty slots for a control by the orb, measured off welcome.txt — the orb
            # is {107.7,159.7} 187pt (px 323–884 × 479–1040, centre 603,760, r 280).
            "orb_badge": (809, 966, 929, 1086),    # a 40pt disc 12pt clear of the rim at 45°
            "chrome_trailing": (1026, 180, 1146, 300),  # top-right, where affirmation keeps `back`
        },
    ),
    # Value props, last slide: the body copy, then a nav row — ← circle · page dots · ✓ circle.
    "onboarding_valueprops": Screen(
        size=(1206, 2622),
        bg_sample=(2100, 2260),
        layout="fixed",
        regions={
            "body": (132, 1349, 996, 2041),
            "nav": (0, 2280, 1206, 2400),          # the whole row: prev, dots, next
            "prev": (72, 2280, 192, 2400),         # CircularIconButton arrow.left, 40pt
            "dots": (480, 2280, 726, 2400),   # wider than the drawn dots, so an erase leaves no speck
            "next": (1014, 2280, 1134, 2400),      # CircularIconButton checkmark — "Complete onboarding"
            "back_chrome": (72, 180, 192, 300),    # empty top-left, where the rest of the app
                                                   # puts Back when the CTA takes the bottom row
        },
    ),
    # Affirmation before the press-and-hold: back chrome top-left, the affirmation, the hold button.
    "affirmation": Screen(
        size=(1206, 2622),
        # Empty sky between the back chrome and the affirmation. It used to sit at
        # 1500–1560, in the gap under a three-line affirmation — a five-line one
        # (the night capture) reaches 1620, so the text region had to grow past it.
        bg_sample=(700, 780),
        layout="fixed",
        regions={
            "back": (60, 180, 180, 300),               # CircularIconButton chevron.left, 40pt
            "chrome_trailing": (1026, 180, 1146, 300),  # its mirror slot top-right — empty today
            "text": (100, 960, 1106, 1620),             # the affirmation, centred at 1310; five lines
                                                        # at most, and a long one starts above 1135
            "below_text": (0, 1650, 1206, 2100),        # empty, between the affirmation and the hold
                                                        # button — starts below `text`, which a five-line
                                                        # affirmation fills to 1620
            "hold": (492, 2135, 714, 2406),             # press & hold and its caption
            "card_footer": (0, 2180, 1206, 2330),       # the old signature row — under the story safe zone
            "signature": (0, 1960, 1206, 2110),         # where a shared card signs itself: above the
                                                        # bottom 13 % a story app covers with its own controls
        },
    ),
    # Home: the wordmark in the pinned header — the brand mark a shared card carries.
    # Home: wordmark · orb · greeting · journal / voice / chat · Quick Processing card ·
    # FOR YOU · the feed, over a sky gradient. Measured off home_screen_light.txt. The sky
    # varies by ~15–20 levels top to bottom, so fills read it per row from `bg_column`.
    "home": Screen(
        size=(1206, 2622),
        bg_sample=(1480, 1560),                  # sky between the greeting and the voice row
        bg_column=8,
        layout="fixed",
        home_zone=227,                           # `home_tabBar` starts at 798.4pt — it never moves
        regions={
            "logo": (512, 236, 694, 304),        # `home_logo` — the Aurie wordmark, 56.7×18pt
            "orb": (323, 609, 884, 1170),        # home_orbButton 187pt
            "greeting": (106, 1290, 1101, 1451), # "Hey {Name}!\n{question}", display 22, two lines
            "journal_disc": (267, 1644, 411, 1788),  # home_journalButton's 48pt glass disc
            "voice": (483, 1596, 723, 1836),     # home_voiceButton 80pt
            "chat_disc": (795, 1644, 939, 1788), # home_chatButton's 48pt glass disc
            "voice_row": (0, 1596, 1206, 1836),  # journal · voice · chat
            "qp_card": (96, 1932, 1110, 2130),   # home_quickProcessingButton 338×66pt
            "for_you": (0, 2220, 1206, 2260),    # FOR YOU · n / m COMPLETED
            "feed": (60, 2308, 1146, 2395),      # feed cards, down to the tab bar
        },
    ),
    # Conversation (chat): back / new discs, Aurie's opener, the user's bubble, Aurie's
    # reply, the rating thumbs, the message field. Messages are display 18 (AIMessageView:
    # `Font.display(size: LayoutMetrics.subtitleMedium)`), tinted by prefersLightForeground.
    # The stored light and dark captures hold different conversations; the thread regions
    # span both (reply: 32,589 290×88pt light, 32,545 290×132pt dark).
    "conversation": Screen(
        size=(1206, 2622),
        bg_sample=(2040, 2068),
        bg_column=8,
        layout="fixed",
        regions={
            "back": (48, 288, 168, 408),
            "new": (1038, 288, 1158, 408),
            "opener": (96, 1358, 738, 1490),         # Aurie's first message, 32,452.7 214×44pt
            "user_bubble": (250, 1525, 1115, 1725),  # the white bubble
            "thread_top": (0, 1180, 1206, 1630),     # opener + user bubble
            "reply": (96, 1630, 1120, 2031),         # Aurie's reply
            "rating": (96, 2073, 312, 2169),         # like / dislike
            "input": (72, 2316, 1134, 2466),
        },
    ),
    # Settings sidebar: header, four glass groups of SettingsRowView rows, Log out, the help line.
    "settings": Screen(
        size=(1206, 2622),
        bg_sample=(720, 780),
        layout="list",
        regions={
            "header": (0, 186, 1206, 330),
            "group1": (60, 378, 1146, 715),        # Notifications · Account and Data
            "group2": (60, 787, 1146, 955),        # Import Memory from Other AI
            "group3": (60, 1027, 1146, 1195),      # Rate Aurie 5-Stars
            "group4": (60, 1267, 1146, 1773),      # About · Contact Us · Join Slack Community
            "logout": (482, 2230, 725, 2284),
            "help": (308, 2392, 899, 2472),
        },
        # SidebarView pins Log out and the help link under a flexible Spacer, so an insert
        # above them uses up the spacer (group4's bottom → 2200) before it pushes them.
        footer=(2200, 2530),
        clones={
            # A row inside a group: from the row above's bottom edge through this row's
            # bottom padding — 16pt pad · hairline · 16pt pad · 24pt row · 16pt pad. The
            # first 169px (through the row) are what a `row:` insert adds; the trailing
            # pad is the card's own and already there.
            "row": ((60, 1387, 1146, 1604), 169, 133),
            # A one-row group with the 24pt spacing above it — inserted whole by `after:`.
            "group": ((60, 955, 1146, 1195), 240, 156),
        },
    ),
}

SLOTS = ("top", "mid", "bottom")
REGION_OPS = ("replace", "at", "after", "row")

# The app's bottom sheet (`customPracticeBottomSheet` in the store): a half-screen sheet
# over the dimmed home — rounded top, grabber, an icon disc, a display-face title, a
# subtitle, a hairline, controls, and a full-width primary button. `element=sheet` clones
# it and re-fills it, so a proposed sheet is the app's own component, not a look-alike.
SCREENS["custompracticebottomsheet"] = Screen(
    size=(1206, 2622),
    bg_sample=(880, 900),                     # a donor, never a render target — nothing repaints here
    layout="fixed",
    regions={
        "sheet": (0, 1040, 1206, 2622),       # from just above the rounded top to the bottom
        "sheet_icon": (524, 1152, 682, 1313), # the icon disc (dpSheetIconBg)
        "sheet_title": (0, 1355, 1206, 1435),
        "sheet_subtitle": (0, 1440, 1206, 1505),
        "sheet_divider": (0, 1545, 1206, 1549),
        "sheet_body": (82, 1580, 1130, 2260), # the controls, replaced by the proposal's content
        "sheet_cta": (82, 2293, 1124, 2454),  # the Done button — a full-width PrimaryButton
        "sheet_fill": (600, 2000, 601, 2001), # one pixel of plain sheet fill
    },
)


# The package paywall: a header band (laurel · orb · "Aurie Plus" · Most popular) over the
# themed wash, then `lowerSection` on the flat `Colors.appBgDefaultColor` —
# PaywallNoTrialHeader's headline and four bullets, the plan cards, the value props. The
# CTA ends the scroll and sits below the capture. Regions measured off
# package_paywall_screen_light.txt (points × 3). The paywall forces `.light`, so its three
# stored captures are pixel-identical below the header and there is one sky to check.
# A two-line headline pushes the content ~35pt down; render with `--crop 0,0,1206,2522`
# so the fixed home zone does not hide a label the app would simply scroll to.
SCREENS["package_paywall"] = Screen(
    size=(1206, 2622),
    bg_sample=(1105, 1160),                   # between the headline and bullet 1 — pure surface
    layout="fixed",
    regions={
        "laurel": (361, 210, 845, 349),       # socialProofBadge
        "wordmark": (408, 681, 798, 798),     # "Aurie Plus"
        "badge": (430, 823, 776, 910),        # Most popular pill
        "headline": (86, 992, 1120, 1095),    # PaywallNoTrialHeader title — one line in control
        "bullet1": (144, 1167, 1134, 1231),   # row: TierBadgeCircle checkmark · text
        "bullet1_text": (249, 1167, 1062, 1231),  # the text only: 83pt + the 271pt column
        "bullets": (144, 1167, 1134, 1752),
        "plan_yearly": (69, 1801, 1137, 2115),
        "plan_monthly": (72, 2154, 1134, 2367),
    },
)


# Profile ("You" tab): pinned nav (wordmark · gear), identity header, the card stack on a
# flat appBgDefaultColor page, a frosted bottom fade under the tab bar. Measured on
# profile_screen_light.png; the dark and default captures share the layout and pixels.
SCREENS["profile"] = Screen(
    size=(1206, 2622),
    bg_sample=(350, 420),                        # flat page between the nav row and the avatar — uniform in all three captures
    layout="list",
    home_zone=300,                               # the bottom fade + tab bar, 774pt down — never moves
    fade=(2322, 2416),                           # ultraThinMaterial ramp, 774pt → the tab bar's glass top
    regions={
        "header": (0, 186, 1206, 330),           # wordmark + gear row
        "gear": (1032, 210, 1152, 330),          # CircularIconButton gearshape, 40pt
        "gear_leading": (888, 210, 1008, 330),   # a 40pt disc, 8pt leading of the gear — a second nav disc
        "identity": (0, 435, 1206, 1016),        # avatar · name · "1 week with Aurie · PLUS" and the 20pt below
        "core": (54, 1016, 1152, 1856),          # the Core card (338.7–602.7pt) and its 16pt spacing
        "core_glass": (40, 1004, 1166, 1826),    # the glass with its soft shadow margin — the clone source
        "core_inner": (54, 1016, 1152, 1808),    # the glass itself
        "core_disc": (524, 1082, 682, 1240),     # the 53pt icon chip
        "core_title": (54, 1270, 1152, 1384),    # "At your core"
        "core_body": (54, 1440, 1152, 1750),     # the core statement
        "reflection": (54, 1856, 1152, 2310),    # the quote card
    },
)


# Screens drawn before the age-gate consent. Canonical list: brainstorm-ideas
# `references/pre-consent-screens.md`; `build_idea.PRE_CONSENT_SCREENS` is a copy, and
# tests/test_pre_consent_screens.py pins all three to each other.
PRE_CONSENT_SCREENS = {"welcome", "splash", "geographic_restriction"}


def normalize_screen(value: str) -> str:
    """The row's `Screen` is an accessibility identifier — `welcome_screen`,
    `onboarding_quiz_screen` — while the models are keyed by the bare name. Accept
    either form, so the agent can pass the row value straight through."""
    v = (value or "").strip().lower()
    if v.endswith("_screen"):
        v = v[: -len("_screen")]
    return v


# ---------- fonts -------------------------------------------------------------

def font(app_root: str, key: str, points: float) -> ImageFont.FreeTypeFont:
    size = int(round(points * SCALE))
    if key in BODY_WEIGHTS:
        return _body_font(BODY_WEIGHTS[key], size)
    path = os.path.join(app_root, FONTS[key])
    if not os.path.exists(path):
        raise SystemExit(f"font not found: {path}\n  pass --app-root to the AuraConsumerApp checkout")
    return ImageFont.truetype(path, size)


def _body_font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    """SF Pro at a named weight — the installed face if there is one, else the system one."""
    installed = SF_PRO_INSTALLED.format(weight)
    if os.path.exists(installed):
        return ImageFont.truetype(installed, size)
    if not os.path.exists(SF_PRO_SYSTEM):
        # Same contract as a missing display face: a font this renderer cannot find is
        # `SystemExit`, not a stray `OSError` from the imaging library. SF Pro is Apple's
        # system font, so there is no copy to find off a Mac — CI included.
        raise SystemExit(
            f"SF Pro not found: neither {installed} nor {SF_PRO_SYSTEM}\n"
            "  the body face is the system font, so sheets render on macOS"
        )
    fnt = ImageFont.truetype(SF_PRO_SYSTEM, size)
    try:
        fnt.set_variation_by_name(weight)
    except Exception:
        # A build of the system font with no named instances still renders at Regular,
        # which is closer to right than failing the whole sheet over one weight.
        pass
    return fnt


# ---------- elements ----------------------------------------------------------
#
# Each element renders to an RGBA image with a transparent background, sized to its
# content, centred horizontally when placed.

def _text_block(lines: list[str], fnt: ImageFont.FreeTypeFont, color: tuple[int, ...],
                line_gap: float = 0.28, underline: tuple[str, ...] = ()) -> Image.Image:
    """Centred lines. `underline` names phrases to rule under wherever they occur — the
    welcome disclaimer's two links are drawn that way (`.underline()`), so a moved or
    re-cased disclaimer keeps them."""
    dummy = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    boxes = [dummy.textbbox((0, 0), ln, font=fnt) for ln in lines]
    widths = [b[2] - b[0] for b in boxes]
    heights = [b[3] - b[1] for b in boxes]
    lh = max(heights)
    gap = int(lh * line_gap)
    w = max(widths) + 4
    h = lh * len(lines) + gap * (len(lines) - 1) + 4
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    y = 2
    ascent = fnt.getmetrics()[0]
    for ln, b in zip(lines, boxes):
        x = (w - (b[2] - b[0])) // 2 - b[0]
        d.text((x, y - b[1]), ln, font=fnt, fill=color)
        for phrase in underline:
            i = ln.lower().find(phrase.lower())
            if i < 0:
                continue
            ux0 = x + fnt.getlength(ln[:i])
            ux1 = ux0 + fnt.getlength(ln[i:i + len(phrase)])
            uy = y - b[1] + ascent + 2 * SCALE
            d.line([(ux0, uy), (ux1, uy)], fill=color, width=SCALE)
        y += lh + gap
    return img


def element_laurel(app_root: str, text: str = LAUREL_TEXT) -> Image.Image:
    """The paywall's social-proof badge: laurel.leading · 3 centred lines · laurel.trailing.

    Mirrors `socialProofBadge`: 12pt SF Pro Regular (`Fonts.captionRegular`),
    28pt light SF laurels, 6pt spacing, tint at 0.9 opacity.
    """
    fnt = font(app_root, "body-regular", 12)
    txt = _text_block(text.split("\n"), fnt, (*TINT, 230))
    lead = Image.open(os.path.join(ASSETS, "laurel_leading.png")).convert("RGBA")
    trail = Image.open(os.path.join(ASSETS, "laurel_trailing.png")).convert("RGBA")
    # 28pt symbol ≈ 84px tall at 3x; the exported glyph is scaled to that height.
    lh = 28 * SCALE

    def _fit(im: Image.Image) -> Image.Image:
        return im.resize((max(1, int(im.width * lh / im.height)), lh), Image.LANCZOS)

    lead, trail = _fit(lead), _fit(trail)
    sp = 6 * SCALE
    w = lead.width + sp + txt.width + sp + trail.width
    h = max(lead.height, txt.height, trail.height)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    x = 0
    for part in (lead, txt, trail):
        out.alpha_composite(part, (x, (h - part.height) // 2))
        x += part.width + sp
    return out


def _tracked_width(ln: str, fnt: ImageFont.FreeTypeFont, track_px: float) -> float:
    return fnt.getlength(ln) + track_px * max(0, len(ln) - 1)


def _tracked_wrap(text: str, fnt: ImageFont.FreeTypeFont, track_px: float, max_w: int) -> list[str]:
    """Greedy word wrap measured with the tracking applied, as SwiftUI wraps it."""
    lines: list[str] = []
    cur = ""
    for word in text.split():
        cand = f"{cur} {word}".strip()
        if not cur or _tracked_width(cand, fnt, track_px) <= max_w:
            cur = cand
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _tracked_block(lines: list[str], fnt: ImageFont.FreeTypeFont, track_px: float,
                   color: tuple[int, ...], drop: int = 0, align: str = "center",
                   line_gap: float = 0.28, min_width: int = 0) -> Image.Image:
    """Lines with SwiftUI `.kerning` applied per glyph: each glyph sits at the face's own
    prefix advance (keeping its pair kerning) plus `i * track_px`. `drop` pads the top so
    the ink sits lower when the element is centred in its region. `min_width` keeps a
    left-aligned block as wide as its column, so a short line still starts at the
    column's edge wherever the block is placed."""
    dummy = ImageDraw.Draw(Image.new("RGBA", (1, 1)))

    def width(ln: str) -> float:
        return _tracked_width(ln, fnt, track_px)

    boxes = [dummy.textbbox((0, 0), ln, font=fnt) for ln in lines]
    lh = max(b[3] - b[1] for b in boxes)
    gap = int(lh * line_gap)
    w = max(int(max(width(ln) for ln in lines)) + 4, min_width)
    h = lh * len(lines) + gap * (len(lines) - 1) + 4 + 2 * drop
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    y = 2 + 2 * drop
    for ln, b in zip(lines, boxes):
        x0 = 0 if align == "left" else (w - width(ln)) / 2
        for i, ch in enumerate(ln):
            d.text((x0 + fnt.getlength(ln[:i]) + track_px * i, y - b[1]), ch, font=fnt, fill=color)
        y += lh + gap
    return img


# Per-glyph tracking, in points, for the styles calibrated against a capture. SwiftUI adds
# SF Pro's size-dependent tracking (-0.24pt at 15pt) on top of an explicit `.kerning`, and
# the imaging library applies neither, so an uncalibrated SF line renders wide.
# `headline` (ABC Otto, no system tracking) is the bare `.kerning(-0.56)`; `bullet` is
# `.kerning(-0.18)` + SF's -0.24. Both match the package paywall capture's ink widths
# to the pixel (headline 1030px; bullets 646 / 640 / 803 / 633px wide, and the two-line
# bullets 93 / 94 / 101px tall at a 0.40 line gap).
CALIBRATED_LINE_STYLES = {
    # style: (font key, points, tracking pt, drop px, wrap width px or 0, align, line gap)
    # titleText: displayLight28. A 0.33 gap puts two lines at the face's own 103px line
    # height (ascent + descent; the app's frame is 34.3pt) — the control is one line.
    "headline": ("display-light", 28, -0.56, 8, 0, "center", 0.33),
    # Package paywall bullet: Font.text(.subheadline, .regular), no bold emphasis,
    # left-aligned in a 271pt column — the 306pt row (48pt in from each edge) less the
    # 21pt TierBadgeCircle and 14pt spacing. Every stored bullet breaks where the app does.
    "bullet": ("body-regular", 15, -0.42, 2, 271 * 3, "left", 0.40),
}


def element_line(app_root: str, text: str, style: str = "display", points: float = 15) -> Image.Image:
    """A single centred line — a benefit or outcome statement in the app's type.

    `headline` and `bullet` are the package paywall's header styles, drawn in
    `Colors.blackSecondary` at full opacity; `\n` in a headline breaks the line where the
    app's own copy breaks it."""
    if style in CALIBRATED_LINE_STYLES:
        key, pts, track, drop, wrap_w, align, line_gap = CALIBRATED_LINE_STYLES[style]
        fnt = font(app_root, key, pts)
        lines = []
        for para in text.replace("\\n", "\n").split("\n"):
            lines += _tracked_wrap(para, fnt, track * SCALE, wrap_w) if wrap_w else [para]
        return _tracked_block(lines, fnt, track * SCALE, (*TINT, 255), drop=drop, align=align,
                              line_gap=line_gap, min_width=wrap_w if align == "left" else 0)
    key = {"display": "display", "inter": "body-medium", "body": "body-medium"}.get(style, style)
    fnt = font(app_root, key, points)
    return _text_block([text], fnt, (*TINT, 217))


def _wrap(text: str, fnt: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    """Greedy word wrap to `max_w` pixels. A single word wider than the box stays on
    its own line rather than being split — a review quote is never hyphenated."""
    lines: list[str] = []
    cur = ""
    for word in text.split():
        cand = f"{cur} {word}".strip()
        if not cur or fnt.getlength(cand) <= max_w:
            cur = cand
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _star_row(n: int = 5, size: int = 11 * SCALE, gap: int = 2 * SCALE,
              color: tuple[int, ...] = (0xE0, 0xA8, 0x3B, 255)) -> Image.Image:
    """Five-point stars drawn as polygons. Not a glyph: the app's fonts may not carry
    U+2605, and a tofu box on a review card would be worse than no stars."""
    import math  # noqa: PLC0415
    w = n * size + (n - 1) * gap
    img = Image.new("RGBA", (w, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r_out, r_in = size / 2, size / 2 * 0.45
    for i in range(n):
        cx, cy = i * (size + gap) + size / 2, size / 2
        pts = []
        for k in range(10):
            r = r_out if k % 2 == 0 else r_in
            ang = -math.pi / 2 + k * math.pi / 5
            pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
        d.polygon(pts, fill=color)
    return img


def element_review(app_root: str, quote: str, attribution: str = "— App Store review",
                   width: int = 334 * SCALE) -> Image.Image:
    """One verbatim ★5 review on a glass card — the "one human quote" that pairs with
    the institutional badge.

    Every surface value is a token from `design-tokens-aurie.generated.md`, so the card
    belongs to the same system as the screen under it:

      fill      whiteAlpha400  rgba(255,255,255,0.6)   the background reads through
      hairline  whiteAlpha300  rgba(255,255,255,0.24)
      radius    borderRadius16
      padding   space16, gaps space8

    The quote is set in the **display** face (token `display15`): on this screen Aurie's
    voice is the serif — the headline and tagline above it both are — and a quote in the
    UI face reads as an ad unit. The attribution stays Inter, because it is metadata
    about the quote rather than Aurie speaking.

    Returns the card at exactly its own bounds. Callers lay out against those bounds, so
    nothing here may bleed outside them — a drop shadow needs the compositor to own it,
    not this function to pad itself.
    """
    pad, gap = 16 * SCALE, 8 * SCALE
    stars = _star_row()
    qf = font(app_root, "display", 15)
    q = _text_block(_wrap(f"“{quote}”", qf, width - 2 * pad), qf, (*TINT, 235), line_gap=0.35)
    a = _text_block([attribution], font(app_root, "body-regular", 11), (*TINT, 150))
    h = pad + stars.height + gap + q.height + gap + a.height + pad
    card = Image.new("RGBA", (width, h), (0, 0, 0, 0))
    ImageDraw.Draw(card).rounded_rectangle((0, 0, width - 1, h - 1), radius=16 * SCALE,
                                           fill=(255, 255, 255, int(0.6 * 255)),
                                           outline=(255, 255, 255, int(0.24 * 255)), width=2)
    y = pad
    for part in (stars, q, a):
        card.alpha_composite(part, ((width - part.width) // 2, y))
        y += part.height + gap
    return card


def element_badge_review(app_root: str, quote: str,
                         attribution: str = "— App Store review") -> Image.Image:
    """The laurel badge over one review card — trust signal plus one human voice, which
    is the pairing the team uses on aurie.ai rather than a wall of quotes."""
    badge = element_laurel(app_root)
    card = element_review(app_root, quote, attribution)
    gap = 10 * SCALE
    w = max(badge.width, card.width)
    out = Image.new("RGBA", (w, badge.height + gap + card.height), (0, 0, 0, 0))
    out.alpha_composite(badge, ((w - badge.width) // 2, 0))
    out.alpha_composite(card, ((w - card.width) // 2, badge.height + gap))
    return out


# ---------- SF Symbols -------------------------------------------------------
#
# The app's glyphs are SF Symbols, which no font file here carries. `sfsymbol.swift`
# (the `symbols` command, macOS only) exports each one once with AppKit at 256pt as a
# black-on-alpha PNG into references/assets/symbols/; a call site asks for it at the
# app's point size, and the 256pt export tells us how tall that glyph is per point —
# a chevron and a share arrow at the same `iconSize` are not the same height.

SYMBOLS = os.path.join(ASSETS, "symbols")
SYMBOL_EXPORT_POINTS = 256
# SwiftUI draws a symbol larger than AppKit exports it at the same point size. Measured
# on the captures against `CircularIconButton`'s `iconSize` 16: chevron.left 47px where
# the export scales to 40.5, checkmark 47 / 39.9, arrow.left 42 / 35.4 — 1.16–1.19, one
# factor. Sizing by `points=` applies it; sizing by `height=` is exact by construction.
SYMBOL_SWIFTUI_SCALE = 1.17


def sf_symbol(name: str, weight: str = "regular", color: tuple[int, ...] = (*TINT, 255),
              points: Optional[float] = None, height: Optional[int] = None) -> Image.Image:
    """An exported SF Symbol, tinted, at `points` (the font size the app gives it, scaled
    by SCALE) or at an exact pixel `height`."""
    path = os.path.join(SYMBOLS, f"{name}-{weight}.png")
    if not os.path.exists(path):
        raise SystemExit(f"SF Symbol {name!r} ({weight}) is not exported: {path}\n"
                         f"  on a Mac: design_idea.py symbols --name {name} --weight {weight}")
    im = Image.open(path).convert("RGBA")
    bbox = im.getchannel("A").getbbox()
    if not bbox:
        raise SystemExit(f"SF Symbol export {path} is empty")
    im = im.crop(bbox)
    if height is None:
        if points is None:
            raise SystemExit("sf_symbol needs points= or height=")
        height = max(1, round(im.height * points * SCALE * SYMBOL_SWIFTUI_SCALE / SYMBOL_EXPORT_POINTS))
    w = max(1, round(im.width * height / im.height))
    im = im.resize((w, max(1, height)), Image.LANCZOS)
    out = Image.new("RGBA", im.size, (*color[:3], 0))
    alpha = im.getchannel("A")
    if len(color) == 4 and color[3] < 255:
        alpha = alpha.point(lambda a: a * color[3] // 255)
    out.putalpha(alpha)
    return out


def _auto_fg(src: Image.Image, region: Region) -> tuple[int, int, int]:
    """The foreground the app would draw on this ground. `prefersLightForeground` is the
    sky's call, and the capture already shows the sky — so measure it rather than ask."""
    lum = ImageStat.Stat(src.crop(region).convert("L")).mean[0]
    return (255, 255, 255) if lum < 118 else TINT


# ---------- cloned chrome ------------------------------------------------------
#
# A new control is built from a control that is already on a capture: its glass, its
# gradient, its hairline and padding are the app's own pixels, and only the content —
# a label, a glyph, a title — is erased and drawn again. Three erasers, each right for
# one kind of surface.

def _inpaint_vertical(im: Image.Image, x0: int, x1: int, y0: int, y1: int) -> None:
    """Each column blends from the row just above to the row just below — for a
    horizontal gradient (the pill), where a column is one colour apart from the text."""
    px = im.load()
    n = y1 - y0
    for x in range(x0, x1):
        a, b = px[x, y0 - 1], px[x, y1]
        for i, y in enumerate(range(y0, y1)):
            t = (i + 1) / (n + 1)
            px[x, y] = tuple(round(a[k] * (1 - t) + b[k] * t) for k in range(4))


def _inpaint_horizontal(im: Image.Image, x0: int, x1: int, y0: int, y1: int) -> None:
    """Each row blends from the column just left to the one just right — for a glass
    disc, whose fill varies down the gradient but not across."""
    px = im.load()
    n = x1 - x0
    for y in range(y0, y1):
        a, b = px[x0 - 1, y], px[x1, y]
        for i, x in enumerate(range(x0, x1)):
            t = (i + 1) / (n + 1)
            px[x, y] = tuple(round(a[k] * (1 - t) + b[k] * t) for k in range(4))


def _inpaint_from_column(im: Image.Image, x0: int, x1: int, y0: int, y1: int, xs: int) -> None:
    """Each row takes the colour it has at column `xs` — for a settings row, whose glass
    fill is a vertical gradient and whose column `xs` carries no content."""
    px = im.load()
    for y in range(y0, y1):
        c = px[xs, y]
        for x in range(x0, x1):
            px[x, y] = c


def element_pill(donor: Image.Image, region: Region, app_root: str, text: str,
                 width_pt: Optional[float] = None) -> Image.Image:
    """`PrimaryButton`, cloned from a capture that shows one (the welcome Get Started
    button, `welcome:cta`) and re-lettered. The gradient and glass edge are the app's own
    pixels; the label is drawn in what PrimaryButton draws — `TextStyle.headline`
    (17pt semibold) in `Colors.Black`. `width_pt` narrows the pill by stretching its
    middle between the two end caps, so the caps keep their curve."""
    pill = donor.crop(region).copy()
    w, h = pill.size
    _inpaint_vertical(pill, 0, w, int(h * 0.28), int(h * 0.74))
    if width_pt:
        target = int(round(width_pt * SCALE))
        cap = h // 2
        if target < 2 * cap + 1:
            raise SystemExit(f"pill width {width_pt}pt is narrower than its own end caps")
        left = pill.crop((0, 0, cap, h))
        right = pill.crop((w - cap, 0, w, h))
        mid = pill.crop((cap, 0, w - cap, h)).resize((target - 2 * cap, h), Image.BILINEAR)
        out = Image.new("RGBA", (target, h), (0, 0, 0, 0))
        out.paste(left, (0, 0))
        out.paste(mid, (cap, 0))
        out.paste(right, (target - cap, 0))
        pill, w = out, target
    # The crop is the control's accessibility frame, which runs a few px past the drawn
    # capsule, so its edge carries a sliver of the donor's sky and glass shadow. The
    # mask is inset by that margin: a capsule that ends on the frame showed a flat grey
    # tab at each end on the value-props render.
    inset = PILL_FRAME_INSET
    # Drawn at 4x and reduced, so the capsule's edge is anti-aliased: PIL's shapes are
    # not, and a stepped rim reads as a cut-out even at the sheet's scale.
    ss = 4
    mask = Image.new("L", (w * ss, h * ss), 0)
    ImageDraw.Draw(mask).rounded_rectangle((inset * ss, inset * ss, (w - inset) * ss - 1, (h - inset) * ss - 1),
                                           radius=(h // 2 - inset) * ss, fill=255)
    mask = mask.resize((w, h), Image.LANCZOS)
    pill.putalpha(ImageChops.multiply(pill.getchannel("A"), mask))
    label = _text_block([text], font(app_root, "body-semibold", 17), (*BLACK, 255))
    pill.alpha_composite(label, ((w - label.width) // 2, (h - label.height) // 2))
    return pill


def element_circle_glyph(src: Image.Image, region: Region, symbol: str,
                         points: float = 16, weight: str = "regular",
                         color: Optional[tuple[int, int, int]] = None) -> Image.Image:
    """`CircularIconButton`, cloned from one already on the screen (the back button) and
    given another glyph. The glass disc keeps its pixels; the old glyph is erased row by
    row from the disc's left to its right, and the new SF Symbol is drawn at the control's
    `iconSize` (16pt) in the `figurePrimary` role the sky resolves to."""
    disc = src.crop(region).copy()
    w, h = disc.size
    _inpaint_horizontal(disc, int(w * 0.28), int(w * 0.72), int(h * 0.26), int(h * 0.74))
    ink = color or _auto_fg(src, region)
    glyph = sf_symbol(symbol, weight, (*ink, 255), points=points)
    disc.alpha_composite(glyph, ((w - glyph.width) // 2, (h - glyph.height) // 2))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, w - 1, h - 1), fill=255)
    disc.putalpha(ImageChops.multiply(disc.getchannel("A"), mask))
    return disc


def element_chip(src: Image.Image, region: Region, app_root: str, text: str,
                 trailing: Optional[str] = None, points: float = 15, weight: str = "semibold",
                 glyph_points: float = 16) -> Image.Image:
    """A glass capsule in the home's own sky-aware glass: a `CircularIconButton` disc on
    the capture, its glyph erased, split at its centre column and stretched to the label
    (the pill mechanic on a disc — the glass varies down, not across). Label in
    `subheadlineSemibold` (GlassContinueButton's label token), ink the role the sky
    resolves to; an optional trailing SF Symbol at CircularIconButton's `iconSize`."""
    disc = src.crop(region).copy()
    w, h = disc.size
    _inpaint_horizontal(disc, int(w * 0.26), int(w * 0.74), int(h * 0.18), int(h * 0.82))
    ink = _auto_fg(src, region)
    label = _text_block([text], font(app_root, f"body-{weight}", points), (*ink, 255))
    glyph = sf_symbol(trailing, "regular", (*ink, 255), points=glyph_points) if trailing else None
    pad, gap = 4 * SCALE, 10 * SCALE
    inner = label.width + 2 * pad + ((gap + glyph.width) if glyph else 0)
    cap = w // 2
    mid = disc.crop((cap, 0, cap + 1, h)).resize((inner, h), Image.NEAREST)
    out = Image.new("RGBA", (2 * cap + inner, h), (0, 0, 0, 0))
    out.paste(disc.crop((0, 0, cap, h)), (0, 0))
    out.paste(mid, (cap, 0))
    out.paste(disc.crop((w - cap, 0, w, h)), (cap + inner, 0))
    ss = 4
    mask = Image.new("L", (out.width * ss, h * ss), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, out.width * ss - 1, h * ss - 1), radius=(h // 2) * ss, fill=255)
    mask = mask.resize((out.width, h), Image.LANCZOS)
    out.putalpha(ImageChops.multiply(out.getchannel("A"), mask))
    x = cap + pad
    out.alpha_composite(label, (x, (h - label.height) // 2))
    if glyph:
        x += label.width + gap
        out.alpha_composite(glyph, (x, (h - glyph.height) // 2))
    return out


def element_settings_row(src: Image.Image, scr: Screen, clone: str, app_root: str,
                         title: str, symbol: str, trailing: Optional[str] = "chevron.right",
                         sample_x: int = 900) -> Image.Image:
    """`SettingsRowView`, cloned from a row already on the screen and re-filled. The glass
    band, hairline and padding are the app's own pixels; the icon, title and trailing
    glyph are erased from a content-free column and drawn again the way `SidebarView`
    lays a row out: `Image(systemName:)` at `imageSize * 0.7` (16.8pt) in a 24pt frame
    36pt from the screen edge, the title in `Fonts.font375DefaultSemibold` overridden to
    `.medium` (15pt) in `Colors.grayMain` 16pt after it, the trailing glyph flush right —
    `chevron.right` for a screen, `arrow.up.right` for a link that leaves the app.
    Returns the full band (`cut` tall) at the card's width; the compositor lands it."""
    if clone not in scr.clones:
        raise SystemExit(f"screen has no clone {clone!r}; known: {sorted(scr.clones)}")
    region, cut, cy = scr.clones[clone]
    band = src.crop(region).copy().crop((0, 0, region[2] - region[0], cut))
    w = band.width
    _inpaint_from_column(band, 4, w - 4, cy - 40, min(cy + 40, band.height), sample_x - region[0])
    ink = GRAY_MAIN
    icon = sf_symbol(symbol, "regular", (*ink, 255), points=16.8)
    frame_x = 36 * SCALE - region[0]                       # the 24pt icon frame
    band.alpha_composite(icon, (frame_x + (24 * SCALE - icon.width) // 2, cy - icon.height // 2))
    label = _text_block([title], font(app_root, "body-medium", 15), (*ink, 255))
    band.alpha_composite(label, ((36 + 24 + 16) * SCALE - region[0], cy - label.height // 2))
    if trailing:
        # Measured on the capture: arrow.up.right draws 9pt tall, chevron.right 10.3pt.
        th = round((9 if trailing == "arrow.up.right" else 10.3) * SCALE)
        tr = sf_symbol(trailing, "semibold", (*ink, 255), height=th)
        band.alpha_composite(tr, (w - 16 * SCALE - tr.width, cy - tr.height // 2))
    return band


def element_text_block(app_root: str, text: str, points: float = 11, weight: str = "medium",
                       color: tuple[int, ...] = (*TINT, 255), max_w: int = 1206 - 2 * 32 * SCALE,
                       underline: tuple[str, ...] = (), style: str = "body",
                       align: str = "center") -> Image.Image:
    """A paragraph in the app's type, centred or left-aligned (a chat message). `\\n` in
    the text breaks a line; a blank line is a paragraph gap. Phrases in `underline` are
    ruled, as the welcome disclaimer's links are."""
    key = "display" if style == "display" else f"body-{weight}"
    fnt = font(app_root, key, points)
    lines: list[str] = []
    for para in text.replace("\\n", "\n").split("\n"):
        lines += _wrap(para, fnt, max_w) if para.strip() else [""]
    if align == "left":
        return _text_block_left(lines, fnt, color, line_gap=0.3)
    return _text_block(lines, fnt, color, line_gap=0.3, underline=underline)


def element_overlay(path: str, size: tuple[int, int]) -> Image.Image:
    """A prepared full-frame RGBA — a system sheet cut out of a capture by `cutout`,
    or a card — placed with `at:screen`. Resized to the capture only if it is a
    different size; a cutout of the same simulator never is."""
    im = Image.open(path).convert("RGBA")
    if im.size != size:
        im = im.resize(size, Image.LANCZOS)
    return im


def element_crop(donor: Image.Image, region: Region, key: Optional[str] = None,
                 color: Optional[tuple[int, int, int]] = None, height_pt: Optional[float] = None) -> Image.Image:
    """A region lifted off a capture — the wordmark, an icon — as an element. `key=ink`
    keeps only the dark strokes (alpha from the inverse luminance) and tints them, so a
    mark drawn on one sky can sit on another without bringing its ground along."""
    im = donor.crop(region).copy()
    if key == "ink":
        # The ground is whatever sky the mark was captured on (the home header's is
        # ~156 of 255), the strokes near-black: key relative to the crop's own border
        # — clear from 20 below the ground's median up, opaque under 45 % of it, a ramp
        # between — so no ghost of the ground rides along as a box.
        lum = im.convert("L")
        w, h = lum.size

        def _ground(l: Image.Image) -> int:
            border = sorted([l.getpixel((x, y)) for x in range(w) for y in (0, h - 1)] +
                            [l.getpixel((x, y)) for y in range(h) for x in (0, w - 1)])
            return border[len(border) // 2]

        ground = _ground(lum)
        if ground < 128:
            # White strokes on a night sky: flip the luminance so the same key — strokes
            # darker than the ground — reads them, and key against the flipped ground.
            lum = lum.point(lambda v: 255 - v)
            ground = _ground(lum)
        hi, lo = ground - 20, ground * 0.45
        alpha = lum.point(lambda v: 0 if v >= hi else (255 if v <= lo else int((hi - v) * 255 / max(1, hi - lo))))
        ink = color or TINT
        im = Image.new("RGBA", im.size, (*ink, 0))
        im.putalpha(alpha)
    if height_pt:
        h = int(round(height_pt * SCALE))
        im = im.resize((max(1, int(im.width * h / im.height)), h), Image.LANCZOS)
    return im


def element_sky(sky: str, size: tuple[int, int], halo: bool = True) -> Image.Image:
    """The environment's sky as a full frame: `SkyState.stops` top → horizon, evenly
    spaced the way `LinearGradient(colors:)` lays them, and the sky's `haloColor` as a
    soft glow low in the frame, where the orb's halo sits on every environment screen.
    A card drawn from this carries the app's sky, not a blurred capture of it."""
    if sky not in SKIES:
        raise SystemExit(f"unknown sky {sky!r}; one of {sorted(SKIES)}")
    stops = SKIES[sky]["stops"]
    w, h = size
    col = Image.new("RGB", (1, h))
    px = col.load()
    for y in range(h):
        t = y / max(1, h - 1) * (len(stops) - 1)
        i = min(int(t), len(stops) - 2)
        f = t - i
        a, b = stops[i], stops[i + 1]
        px[0, y] = tuple(round(a[k] * (1 - f) + b[k] * f) for k in range(3))
    im = col.resize((w, h)).convert("RGBA")
    if halo:
        # A radial falloff, widest across, centred at 78 % of the height: the halo the
        # orb wears, without the orb. Peak 40 % so it warms the horizon and stays a sky.
        # Built at low resolution and scaled up: a smooth falloff that reaches zero at
        # the ellipse's edge, so no seam where the glow's frame ends.
        rx, ry, cx, cy = 0.62, 0.34, 0.5, 0.78
        lw, lh = 160, 320
        small = Image.new("L", (lw, lh), 0)
        px = small.load()
        for y in range(lh):
            for x in range(lw):
                d = (((x + 0.5) / lw - cx) / rx) ** 2 + (((y + 0.5) / lh - cy) / ry) ** 2
                if d < 1:
                    t = 1 - d ** 0.5
                    px[x, y] = int(255 * 0.40 * t * t * (3 - 2 * t))
        layer = Image.new("RGBA", (w, h), (*SKIES[sky]["halo"], 0))
        layer.putalpha(small.resize((w, h), Image.BILINEAR))
        im.alpha_composite(layer)
    return im


def element_signature(app_root: str, fg: tuple[int, int, int], secondary: float,
                      caption: Optional[str], points: float = 26, rule: bool = True) -> Image.Image:
    """How a shared card signs itself: a hairline, the *Aurie* wordmark, and the
    address under it. The wordmark is the app's `aurieLogo` asset tinted with the sky's
    `textPrimary` (what `AurieWordmark` does), the hairline is `figureQuaternary`, the
    caption the body face at the sky's secondary opacity — every part a role the app
    already has, so the signature belongs to the screen it leaves."""
    if not os.path.exists(WORDMARK_ASSET):
        raise SystemExit(f"wordmark asset missing: {WORDMARK_ASSET}\n  run `design_idea.py wordmark`")
    mark = Image.open(WORDMARK_ASSET).convert("RGBA")
    mh = int(round(points * SCALE))
    mark = mark.resize((max(1, int(mark.width * mh / mark.height)), mh), Image.LANCZOS)
    tinted = Image.new("RGBA", mark.size, (*fg, 0))
    tinted.putalpha(mark.split()[-1])
    parts: list[Image.Image] = []
    if rule:
        line = Image.new("RGBA", (40 * SCALE, max(1, SCALE // 3 * 2)), (*fg, HAIRLINE_ALPHA[fg == (255, 255, 255)]))
        parts.append(line)
    parts.append(tinted)
    if caption:
        parts.append(_text_block([caption], font(app_root, "body-regular", 12), (*fg, int(255 * secondary))))
    gaps = [14 * SCALE, 8 * SCALE]
    w = max(p.width for p in parts) + 4
    h = sum(p.height for p in parts) + sum(gaps[:len(parts) - 1])
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    y = 0
    for i, p in enumerate(parts):
        out.alpha_composite(p, ((w - p.width) // 2, y))
        y += p.height + (gaps[i] if i < len(gaps) else 0)
    return out


def _text_block_left(lines: list[str], fnt: ImageFont.FreeTypeFont, color: tuple[int, ...],
                     line_gap: float = 0.3) -> Image.Image:
    dummy = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    boxes = [dummy.textbbox((0, 0), ln, font=fnt) for ln in lines]
    lh = max(b[3] - b[1] for b in boxes)
    gap = int(lh * line_gap)
    w = max(b[2] - b[0] for b in boxes) + 4
    img = Image.new("RGBA", (w, lh * len(lines) + gap * (len(lines) - 1) + 4), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    y = 2
    for ln, b in zip(lines, boxes):
        d.text((2 - b[0], y - b[1]), ln, font=fnt, fill=color)
        y += lh + gap
    return img


def element_sheet(donor: Image.Image, dscr: Screen, app_root: str, title: str, subtitle: str,
                  body: str, cta: str, symbol: str, cta_donor: Image.Image,
                  cta_region: Region, scrim: float = 0.35,
                  secondary_region: Optional[Region] = None) -> Image.Image:
    """The app's bottom sheet, cloned from `customPracticeBottomSheet` and re-filled:
    the rounded card, grabber, icon disc, hairline and layout are the capture's own
    pixels; the icon's glyph, the title (display face), the subtitle, the body — one
    white field-style card carrying the text, as the sheet's own name field is drawn —
    and the primary button (a `PrimaryButton` clone, re-lettered) are drawn again.
    Returns a full-frame overlay: the host screen shows through a scrim above the
    sheet and around its corners, the way the app dims the screen behind it."""
    W, H = donor.size
    y0 = dscr.region("sheet")[1]
    out = Image.new("RGBA", (W, H), (0, 0, 0, int(255 * scrim)))
    band = donor.crop((0, y0, W, H)).copy()
    # Outside the rounded corners the capture shows the dimmed host: key those rows on
    # luminance so the sheet keeps its shape and the new host shows through.
    corner_rows = 140
    mask = Image.new("L", band.size, 255)
    mask.paste(band.crop((0, 0, W, corner_rows)).convert("L").point(lambda v: 255 if v > 200 else 0), (0, 0))
    # The sheet is inset from the screen's sides (iOS draws it narrower than the host),
    # so below the corner rows its edges are read per row and the host columns outside
    # them are keyed out too — otherwise the donor's dimmed home shows at the sides
    # of a sheet placed over another screen, and a repaint across the full width
    # leaves a tab outside the sheet at every re-lettered row.
    lum = band.convert("L")
    edges: list[tuple[int, int]] = []
    for yy in range(band.height):
        row = [lum.getpixel((xx, yy)) for xx in range(0, W, 2)]
        xs = [i * 2 for i, v in enumerate(row) if v > 200]
        edges.append((xs[0], xs[-1] + 2) if xs else (0, 0))
    mpx = mask.load()
    for yy in range(corner_rows, band.height):
        e0, e1 = edges[yy]
        for xx in range(0, e0):
            mpx[xx, yy] = 0
        for xx in range(e1, W):
            mpx[xx, yy] = 0
    band.putalpha(mask)
    fx, fy = dscr.region("sheet_fill")[:2]
    fill = donor.getpixel((fx, fy))[:3]
    d = ImageDraw.Draw(band)
    e0s = sorted(e[0] for e in edges[corner_rows:] if e[1])
    e1s = sorted(e[1] for e in edges[corner_rows:] if e[1])
    sx0, sx1 = e0s[len(e0s) // 2] + 4, e1s[len(e1s) // 2] - 4   # the median row: the sheet's own edge

    def rel(name: str) -> Region:
        x0, r0, x1, r1 = dscr.region(name)
        return (max(x0, sx0), r0 - y0, min(x1, sx1), r1 - y0)

    for name in ("sheet_title", "sheet_subtitle", "sheet_body", "sheet_cta"):
        d.rectangle(rel(name), fill=(*fill, 255))
    # the icon disc keeps its gradient; its glyph is erased across and drawn again in white
    ix0, iy0, ix1, iy1 = rel("sheet_icon")
    iw, ih = ix1 - ix0, iy1 - iy0
    _inpaint_horizontal(band, ix0 + int(iw * 0.22), ix0 + int(iw * 0.78), iy0 + int(ih * 0.12), iy0 + int(ih * 0.88))
    glyph = sf_symbol(symbol, "regular", (255, 255, 255, 255), points=22)
    band.alpha_composite(glyph, (ix0 + (iw - glyph.width) // 2, iy0 + (ih - glyph.height) // 2))
    tx0, ty0, tx1, ty1 = rel("sheet_title")
    t = _text_block([title], font(app_root, "display", 24), (*TINT, 255))
    band.alpha_composite(t, ((W - t.width) // 2, ty0 + ((ty1 - ty0) - t.height) // 2))
    sx0, sy0, sx1, sy1 = rel("sheet_subtitle")
    s = _text_block([subtitle], font(app_root, "body-regular", 15), (*TINT, ROLE_ALPHA["secondary"]))
    band.alpha_composite(s, ((W - s.width) // 2, sy0 + ((sy1 - sy0) - s.height) // 2))
    cx0, cy0, cx1, cy1 = rel("sheet_cta")
    labels = [c.strip() for c in cta.split("|") if c.strip()]
    pill_h = cy1 - cy0
    stack_top = cy1 - len(labels) * pill_h - (len(labels) - 1) * 12 * SCALE
    bx0, by0, bx1, by1 = rel("sheet_body")
    pad = 16 * SCALE
    bf = font(app_root, "body-regular", 15)
    lines: list[str] = []
    for para in body.replace("\\n", "\n").split("\n"):
        lines += _wrap(para, bf, (bx1 - bx0) - 2 * pad) if para.strip() else [""]
    # The card is a preview and stops above the buttons: the cloned sheet has one
    # height, so a message that would run under the stack is cut with an ellipsis
    # rather than drawn through them.
    card_max = stack_top - 12 * SCALE - by0
    text = _text_block_left(lines, bf, (*TINT, 255))
    while text.height + 2 * pad > card_max and len(lines) > 1:
        lines = lines[:-1]
        lines[-1] = lines[-1].rstrip(" .") + " …"
        text = _text_block_left(lines, bf, (*TINT, 255))
    card_h = text.height + 2 * pad
    d.rounded_rectangle((bx0, by0, bx1 - 1, by0 + card_h), radius=12 * SCALE, fill=(255, 255, 255, 255))
    band.alpha_composite(text, (bx0 + pad, by0 + pad))
    y_bottom = cy1
    for i, label in reversed(list(enumerate(labels))):
        region = cta_region if i == 0 or secondary_region is None else secondary_region
        pill = element_pill(cta_donor, region, app_root, label, width_pt=(cx1 - cx0) / SCALE)
        band.alpha_composite(pill, (cx0, y_bottom - pill.height))
        y_bottom -= pill.height + 12 * SCALE
    out.alpha_composite(band, (0, y0))
    return out


def element_offer_card(src: Image.Image, scr: Screen, app_root: str, title: str, pill: str,
                       symbol: str, cta_donor: Image.Image, cta_region: Region,
                       dismiss: bool = True) -> Image.Image:
    """A dismissible gift card for the Profile stack, built from the Profile's own
    pixels: the Core card's glass + warm wash (`core_glass`), its 53pt icon chip given
    the gift glyph, a `displayLight22` heading, the forming card's compact 36pt gradient
    pill (the welcome PrimaryButton cloned and scaled, label `captionRegular`), and the
    Download Aura card's dismiss mark (`xmark` light 14.8pt, black @ 0.75) top-right.
    Returns the card with a 16pt spacing band below, so an `after:` insert keeps the
    stack's rhythm."""
    gx0, gy0, gx1, gy1 = scr.region("core_glass")
    card = src.crop((gx0, gy0, gx1, gy1)).copy()
    cw, ch = card.size
    def local(name: str) -> Region:
        x0, y0, x1, y1 = scr.region(name)
        return (x0 - gx0, y0 - gy0, x1 - gx0, y1 - gy0)

    ix0, iy0, ix1, iy1 = local("core_inner")      # the glass inside the crop
    disc = local("core_disc")
    title_r, body_r = local("core_title"), local("core_body")
    # erase the Core title and body — each column blends across, the wash is soft
    _inpaint_vertical(card, ix0 + 6, ix1 - 6, title_r[1], title_r[3])
    _inpaint_vertical(card, ix0 + 6, ix1 - 6, body_r[1], body_r[3])
    # swap the chip's glyph
    dx0, dy0, dx1, dy1 = disc
    dw, dh = dx1 - dx0, dy1 - dy0
    _inpaint_horizontal(card, dx0 + int(dw * 0.26), dx0 + int(dw * 0.74), dy0 + int(dh * 0.24), dy0 + int(dh * 0.76))
    glyph = sf_symbol(symbol, "regular", (*BLACK, 255), height=22 * SCALE)
    card.alpha_composite(glyph, (dx0 + (dw - glyph.width) // 2, dy0 + (dh - glyph.height) // 2))
    # heading
    head = _text_block([title], font(app_root, "display-light", 22), (*BLACK, 255))
    # the compact pill: PrimaryButton cloned, scaled to 36pt, narrowed to hug the label
    label = _text_block([pill], font(app_root, "body-regular", 12), (*BLACK, 255))
    big = element_pill(cta_donor, cta_region, app_root, " ")
    ph = 36 * SCALE
    small = big.resize((max(1, int(big.width * ph / big.height)), ph), Image.LANCZOS)
    target = label.width + 2 * 20 * SCALE
    cap = ph // 2
    mid = small.crop((cap, 0, small.width - cap, ph)).resize((target - 2 * cap, ph), Image.BILINEAR)
    pillim = Image.new("RGBA", (target, ph), (0, 0, 0, 0))
    pillim.paste(small.crop((0, 0, cap, ph)), (0, 0))
    pillim.paste(mid, (cap, 0))
    pillim.paste(small.crop((small.width - cap, 0, small.width, ph)), (target - cap, 0))
    ss = 4
    m = Image.new("L", (target * ss, ph * ss), 0)
    ImageDraw.Draw(m).rounded_rectangle((6 * ss, 6 * ss, (target - 6) * ss - 1, (ph - 6) * ss - 1),
                                        radius=(ph // 2 - 6) * ss, fill=255)
    pillim.putalpha(ImageChops.multiply(pillim.getchannel("A"), m.resize((target, ph), Image.LANCZOS)))
    pillim.alpha_composite(label, ((target - label.width) // 2, (ph - label.height) // 2))
    # shorten the glass to its content: 22 · chip 53 · 12 · heading · 16 · pill 36 · 22
    pad, gap_a, gap_b = 22 * SCALE, 12 * SCALE, 16 * SCALE
    want = pad + dh + gap_a + head.height + gap_b + ph + pad
    have = iy1 - iy0
    remove = have - want
    if remove < 0:
        raise SystemExit(f"offer card needs {-remove}px more than the Core card has")
    cut0 = body_r[1] + 12                   # inside the erased body band, well below the chip
    top = card.crop((0, 0, cw, cut0))
    bottom = card.crop((0, cut0 + remove, cw, ch))
    out = Image.new("RGBA", (cw, ch - remove), (0, 0, 0, 0))
    out.paste(top, (0, 0))
    feather = 40
    bottom.putalpha(_top_feather_mask(bottom.size, feather))
    out.paste(top.crop((0, cut0 - 1, cw, cut0)).resize((cw, feather)), (0, cut0))   # ground under the ramp
    out.alpha_composite(bottom, (0, cut0))
    # content
    y = iy0 + pad + dh + gap_a
    out.alpha_composite(head, ((ix0 + ix1 - head.width) // 2, y))
    y += head.height + gap_b
    out.alpha_composite(pillim, ((ix0 + ix1 - target) // 2, y))
    if dismiss:
        xm = sf_symbol("xmark", "light", (0, 0, 0, 191), height=round(14.8 * SCALE))
        inset = 20 * SCALE
        out.alpha_composite(xm, (ix1 - inset - xm.width, iy0 + inset))
    # the 16pt spacing below, in the page colour the crop's corner carries
    ground = card.getpixel((2, 2))
    band = Image.new("RGBA", (cw, out.height + gap_b), ground)
    band.alpha_composite(out, (0, 0))
    return band


# ---------- compositing -------------------------------------------------------

def _feather_mask(size: tuple[int, int], feather: int) -> Image.Image:
    """Opaque mask with a linear ramp on the top and bottom edges, so a band cropped
    from one y and pasted at another blends into the gradient instead of seaming."""
    w, h = size
    m = Image.new("L", size, 255)
    d = ImageDraw.Draw(m)
    for i in range(min(feather, h // 2)):
        a = int(255 * i / feather)
        d.line([(0, i), (w, i)], fill=a)
        d.line([(0, h - 1 - i), (w, h - 1 - i)], fill=a)
    return m


def _top_feather_mask(size: tuple[int, int], feather: int) -> Image.Image:
    """Opaque mask with a ramp on the top edge only. A band that slides up lands on
    background above it (blend that edge) and leaves background below it (which we
    paint ourselves) — feathering the bottom too is what let the original text ghost
    through under the moved copy."""
    w, h = size
    m = Image.new("L", size, 255)
    d = ImageDraw.Draw(m)
    for i in range(min(feather, h // 2)):
        d.line([(0, i), (w, i)], fill=int(255 * i / feather))
    return m


def _bg_fill(src: Image.Image, scr: Screen, y0: int, y1: int, uniform: bool = False) -> Image.Image:
    """A strip of pure background `y1-y0` tall. On a screen with a `bg_column` each row is
    read from that content-free column, so a tall erase keeps the sky's exact gradient;
    `uniform` makes the whole strip the colour of row `y0` — what an inserted strip
    needs, since both of its seams meet that row's sky. Otherwise it is stretched from
    the sampled range."""
    h = max(1, y1 - y0)
    if scr.bg_column is not None:
        if uniform:
            return src.crop((scr.bg_column, y0, scr.bg_column + 1, y0 + 1)).resize((src.width, h), Image.NEAREST)
        col = src.crop((scr.bg_column, y0, scr.bg_column + 1, min(y1, src.height)))
        if col.height != h:
            col = col.resize((1, h), Image.BILINEAR)
        return col.resize((src.width, h), Image.NEAREST)
    s0, s1 = scr.bg_sample
    strip = src.crop((0, s0, src.width, s1))
    return strip.resize((src.width, h), Image.BILINEAR)


def _shift_up(canvas: Image.Image, src: Image.Image, scr: Screen,
              y0: int, y1: int, delta: int, feather: int, already: int = 0) -> None:
    """Slide the band src[y0:y1] up by a further `delta` on `canvas`, the way a SwiftUI
    Spacer above it would shrink. `already` is how far this band has been moved by an
    earlier placement in the same variant — the band is always cropped from the
    original capture and pasted at its cumulative offset. Paints background over the
    strip the move exposes *before* pasting, so nothing of the previous position
    remains. A big move needs a wide ramp: the gradient differs more between the old
    and new y."""
    if delta <= 0:
        return
    W = src.width
    f = max(feather, min(delta, 96))
    # Crop with a ramp's worth of headroom above the content, so the fade falls on the
    # background margin and never across the first line of text.
    top = max(0, y0 - f)
    band = src.crop((0, top, W, y1))
    cur_bottom = y1 - already
    paste_y = top - already - delta
    # The status bar is not part of the layout — nothing may slide over it. If the
    # band's headroom would land above the top zone, trim that headroom instead of
    # painting background over the clock and battery.
    floor = scr.top_zone[0]
    if paste_y < floor:
        cut = floor - paste_y
        band = band.crop((0, cut, W, band.height))
        paste_y = floor
        # Keep a short ramp even when the trim ate the headroom: the hero band's first
        # rows are orb glow, not content, and a hard edge against the status bar reads
        # as a seam. Never the full ramp, or the top of the orb fades out.
        f = max(1, min(max(f - cut, 80), band.height // 2))
    # Only the strip the move exposes is repainted. The pasted band is opaque over the
    # rest of its old footprint, and its top ramp blends onto the canvas's *original*
    # pixels at the new y — true background there — rather than onto a fill sampled
    # from elsewhere on the gradient, which would tint the edge.
    canvas.paste(_bg_fill(src, scr, cur_bottom - delta, cur_bottom), (0, cur_bottom - delta))
    canvas.paste(band, (0, paste_y), _top_feather_mask(band.size, f))


def _fit_height(element: Image.Image, max_h: int) -> Image.Image:
    """Shrink an element (keeping its aspect) so it fits a gap the layout could not
    widen — the hero has a ceiling, the CTA is anchored, so past a point the only give
    is the element itself. Never below 35 %: a review card at a third of its size is
    still a review card; smaller is a smudge, and overlapping slightly is the lesser
    evil at that point."""
    if element.height <= max_h:
        return element
    # A zero or negative gap is the tightest case of all — it gets the floor, not a pass.
    r = max(0.35, max_h / element.height) if max_h > 0 else 0.35
    return element.resize((max(1, int(element.width * r)), max(1, int(element.height * r))),
                          Image.LANCZOS)


def compose_many(src: Image.Image, scr: Screen, placements: list[tuple[Image.Image, str]],
                 feather: int = 24) -> Image.Image:
    """Place one element per slot, moving the existing bands the way the layout would."""
    return _compose_stack(src, scr, placements, feather)[0]


def _compose_stack(src: Image.Image, scr: Screen, placements: list[tuple[Image.Image, str]],
                   feather: int = 24) -> tuple[Image.Image, int, int]:
    """`compose_many`, also returning how far the hero and the disclaimer moved up —
    a region op that follows a stack slot has to look for those bands where they are now.

    Slots are laid out **bottom → mid → top**, because a lower placement pushes the
    bands above it and the upper placements must then work with where those bands
    landed. Positions are tracked as cumulative offsets from the capture, so every band
    is cropped from the original once and pasted at its final place.
    """
    for _, slot in placements:
        if slot not in SLOTS:
            raise SystemExit(f"slot must be one of {SLOTS}, got {slot!r}")
    if len({s for _, s in placements}) != len(placements):
        raise SystemExit("one element per slot — two placements name the same slot")
    if scr.layout != "stack" or scr.hero is None:
        raise SystemExit(f"the stack slots {SLOTS} need a stack screen; this one is "
                         f"{scr.layout!r} — use a region op (replace:/at:/after:/row:)")
    W, H = src.size
    canvas = src.copy()
    h0, h1 = scr.hero
    d0, d1 = scr.disclaimer
    hero_off = 0   # how far the hero has moved up so far
    disc_off = 0   # how far the disclaimer has moved up so far
    order = {"bottom": 0, "mid": 1, "top": 2}

    for element, slot in sorted(placements, key=lambda p: order[p[1]]):
        eh = element.height
        ex = (W - element.width) // 2
        need = eh + 2 * scr.pad

        if slot == "bottom":
            # Between the disclaimer and the CTA. The disclaimer slides up into the mid
            # gap; the hero only moves by whatever that gap cannot absorb, so the orb
            # stays put for small elements instead of drifting into the status bar.
            cur_d1 = d1 - disc_off
            disc_delta = max(0, need - (scr.buttons_top - cur_d1))
            mid_gap = (d0 - disc_off) - (h1 - hero_off)
            hero_delta = max(0, disc_delta - (mid_gap - scr.pad))
            # The hero never rises into the status bar: the top zone is the ceiling.
            hero_delta = min(hero_delta, max(0, (h0 - hero_off) - scr.top_zone[0]))
            # …and the disclaimer never slides through the hero: it stops `pad` below
            # the hero's (new) bottom. Whatever room that leaves is what the element gets.
            disc_delta = min(disc_delta, max(0, (d0 - disc_off) - (h1 - hero_off - hero_delta) - scr.pad))
            _shift_up(canvas, src, scr, h0, h1, hero_delta, feather, already=hero_off)
            hero_off += hero_delta
            _shift_up(canvas, src, scr, d0, d1, disc_delta, feather, already=disc_off)
            disc_off += disc_delta
            new_d1 = d1 - disc_off
            element = _fit_height(element, (scr.buttons_top - new_d1) - 2 * scr.pad)
            eh, ex = element.height, (W - element.width) // 2
            ey = new_d1 + ((scr.buttons_top - new_d1) - eh) // 2
            canvas.alpha_composite(element, (ex, ey))
            continue

        if slot == "mid":
            cur_h1 = h1 - hero_off
            cur_d0 = d0 - disc_off
            delta = max(0, need - (cur_d0 - cur_h1))
            # Same ceiling as below: the hero stops at the top zone, never the status bar.
            delta = min(delta, max(0, (h0 - hero_off) - scr.top_zone[0]))
            _shift_up(canvas, src, scr, h0, h1, delta, feather, already=hero_off)
            hero_off += delta
            top = h1 - hero_off
            # If the ceiling stopped the hero short, the element shrinks to the gap it
            # actually has rather than drawing through the disclaimer.
            element = _fit_height(element, (cur_d0 - top) - 2 * scr.pad)
            eh, ex = element.height, (W - element.width) // 2
            ey = top + ((cur_d0 - top) - eh) // 2
            canvas.alpha_composite(element, (ex, ey))
            continue

        # top: the empty background above the hero, wherever the hero now is.
        z0, z1 = scr.top_zone[0], min(scr.top_zone[1], h0 - hero_off)
        if eh > (z1 - z0) - scr.pad:
            r = max(0.1, ((z1 - z0) - scr.pad) / eh)
            element = element.resize((max(1, int(element.width * r)), max(1, int(eh * r))),
                                     Image.LANCZOS)
            eh, ex = element.height, (W - element.width) // 2
        ey = z0 + ((z1 - z0) - eh) // 2
        canvas.alpha_composite(element, (ex, ey))
    return canvas, hero_off, disc_off


def compose(src: Image.Image, scr: Screen, element: Image.Image, slot: str,
            feather: int = 24) -> Image.Image:
    """Place one `element` in `slot` — the single-placement form of `compose_many`."""
    return compose_many(src, scr, [(element, slot)], feather)


# ---------- region ops -----------------------------------------------------------
#
# The ops that address a named region rather than a stack slot. `replace:` repaints the
# region from the background sample and places the element there; `at:` overlays it on
# whatever is there (a cloned disc lands on empty chrome; a paragraph on an empty
# footer); `after:` inserts a full-width strip below the region and pushes everything
# under it down, the way a list grows; `row:` grows a settings group by one row.

@dataclass
class Placement:
    element: Optional[Image.Image]   # None = `erase`: repaint the region and place nothing
    slot: str
    opts: dict[str, str] = field(default_factory=dict)

    def __iter__(self):
        """Unpacks as the `(element, slot)` pair the stack compositor and its callers
        have always taken; the ops read `opts` by name."""
        yield self.element
        yield self.slot


def _place_in(region: Region, el: Image.Image, align: str, valign: str = "center") -> tuple[int, int]:
    x0, y0, x1, y1 = region
    if align not in ("left", "center", "right"):
        raise SystemExit(f"align must be left|center|right, got {align!r}")
    ex = {"left": x0, "right": x1 - el.width, "center": (x0 + x1 - el.width) // 2}[align]
    if valign not in ("top", "center", "bottom"):
        raise SystemExit(f"valign must be top|center|bottom, got {valign!r}")
    ey = {"top": y0, "bottom": y1 - el.height, "center": (y0 + y1 - el.height) // 2}[valign]
    return ex, ey


def _check_fits(canvas: Image.Image, el: Image.Image, ex: int, ey: int, slot: str) -> None:
    """An element that lands past the capture's edge is refused by name, not left for
    the imaging library to raise on: a full-width pill cloned onto a 40pt disc needs
    `width=`, and the message says so."""
    W, H = canvas.size
    if ex < 0 or ey < 0 or ex + el.width > W or ey + el.height > H:
        raise SystemExit(f"{slot}: the element ({el.width}×{el.height}px) does not fit the "
                         f"capture ({W}×{H}px) at ({ex}, {ey}) — narrow it with width=, "
                         f"choose another align=, or place it in a wider region")


def _op_replace(canvas: Image.Image, src: Image.Image, scr: Screen, region: Region,
                el: Optional[Image.Image], align: str, slot: str = "replace:",
                valign: str = "center", offset: tuple[int, int] = (0, 0)) -> None:
    x0, y0, x1, y1 = region
    if el is not None:
        ex, ey = _place_in(region, el, align, valign)
        ex, ey = ex + offset[0], ey + offset[1]
        _check_fits(canvas, el, ex, ey, slot)
        x0, y0 = min(x0, ex), min(y0, ey)
        x1, y1 = max(x1, ex + el.width), max(y1, ey + el.height)
    canvas.paste(_bg_fill(src, scr, y0, y1).crop((x0, 0, x1, y1 - y0)), (x0, y0))
    if el is not None:
        canvas.alpha_composite(el, (ex, ey))


def _offset_px(opts: dict[str, str], slot: str) -> tuple[int, int]:
    """`dx=` / `dy=` in points, positive right / down — an element beside a region the
    model has no name for (`at:gear;dx=-48` is the disc 48pt leading of the gear)."""
    out = []
    for key in ("dx", "dy"):
        raw = opts.get(key, "0")
        try:
            out.append(int(round(float(raw) * SCALE)))
        except ValueError:
            raise SystemExit(f"{slot}: {key}= wants points, got {raw!r}")
    return out[0], out[1]


def _op_at(canvas: Image.Image, region: Region, el: Image.Image, align: str,
           slot: str = "at:", offset: tuple[int, int] = (0, 0)) -> None:
    ex, ey = _place_in(region, el, align)
    ex, ey = ex + offset[0], ey + offset[1]
    _check_fits(canvas, el, ex, ey, slot)
    canvas.alpha_composite(el, (ex, ey))


def _content_bottom(src: Image.Image, scr: Screen) -> int:
    """The lowest row that carries anything but background — used to tell whether an
    insert pushed real content off the bottom.

    Each row is compared to **its own margins**, not to one reference pixel: every
    Aurie sky is a vertical gradient, so a single sample drifts further from the row
    than any element does and every empty horizon reads as content.
    """
    lum = src.convert("L")
    W, H = src.size
    inset, gutter = W // 24, W // 8
    margin = [x for x in range(inset, gutter, max(1, W // 200))] + \
             [x for x in range(W - gutter, W - inset, max(1, W // 200))]
    interior = list(range(gutter, W - gutter, max(1, W // 86)))
    if not margin or not interior:
        # A canvas too small to have margins and an interior — nothing to measure.
        return 0
    for y in range(H - scr.home_zone - 1, 0, -4):
        ground = sorted(lum.getpixel((x, y)) for x in margin)[len(margin) // 2]
        if max(abs(lum.getpixel((x, y)) - ground) for x in interior) > 18:
            return y
    return 0


def _insert_strip(canvas: Image.Image, src: Image.Image, scr: Screen, y: int,
                  strip: Image.Image) -> None:
    """Push everything from `y` down by the strip's height and lay the strip in. What
    slides under the home zone is covered by it — that is the list scrolling, which is
    what the app does with content it can no longer fit."""
    W, H = canvas.size
    keep = H - scr.home_zone
    if scr.fade is None:
        below = canvas.crop((0, y, W, keep))
        canvas.paste(below, (0, y + strip.height))
        canvas.paste(strip, (0, y))
        canvas.paste(src.crop((0, keep, W, H)), (0, keep))
        return
    # A frosted fade (the Profile's bottom band) does not clip: content slides under it
    # and dissolves — blurred and lifted toward the material's grey as it goes. The ramp
    # is rebuilt from the pushed content, so the capture's own ghost of what used to sit
    # under the fade does not show through the new stack. Calibrated on the capture: the
    # flat page under the material stays within a few levels of itself, a pink card is
    # lifted ~halfway to the material's grey.
    f0, f1 = scr.fade
    below = canvas.crop((0, y, W, H))
    canvas.paste(below, (0, y + strip.height))
    canvas.paste(strip, (0, y))
    pad = 24
    band = canvas.crop((0, f0 - pad, W, f1 + pad)).filter(ImageFilter.GaussianBlur(5)).crop((0, pad, W, pad + f1 - f0))
    grey = Image.new("RGBA", band.size, (236, 236, 238, 255))
    ramp = Image.new("L", band.size, 0)
    d = ImageDraw.Draw(ramp)
    for i in range(f1 - f0):
        d.line([(0, i), (W, i)], fill=int(255 * 0.5 * i / (f1 - f0)))
    # the blur ramps with the material: sharp where the band begins, dissolved by its end
    sharp = canvas.crop((0, f0, W, f1))
    frosted = Image.composite(Image.composite(grey, band, ramp), sharp, ramp.point(lambda v: min(255, v * 2)))
    canvas.paste(frosted, (0, f0))
    canvas.paste(src.crop((0, f1, W, H)), (0, f1))


def _footer_spacer(scr: Screen) -> int:
    """Height of the flexible Spacer above a pinned footer: the gap between the lowest
    region that ends above the footer and the footer's top."""
    f0 = scr.footer[0]
    above = [r[3] for r in scr.regions.values() if r[3] <= f0]
    return max(0, f0 - max(above)) if above else 0


def _insert_strip_pinned(canvas: Image.Image, src: Image.Image, scr: Screen, y: int,
                         strip: Image.Image, footer_top: int, absorbed: int) -> None:
    """`_insert_strip` for a screen whose footer is pinned: the content below `y` moves
    down by the whole strip, the footer only by what the Spacer could not absorb
    (`strip.height - absorbed`)."""
    W, H = canvas.size
    keep = H - scr.home_zone
    push = strip.height - absorbed
    footer = canvas.crop((0, footer_top, W, keep))
    below = canvas.crop((0, y, W, footer_top))
    new_top = footer_top + push
    canvas.paste(below.crop((0, 0, W, new_top - y - strip.height)), (0, y + strip.height))
    canvas.paste(strip, (0, y))
    canvas.paste(footer.crop((0, 0, W, max(0, keep - new_top))), (0, new_top))
    canvas.paste(src.crop((0, keep, W, H)), (0, keep))


def _strip_for(src: Image.Image, scr: Screen, y: int, el: Image.Image, x: int, gap: int) -> Image.Image:
    """A full-width background strip carrying `el` at `x`, `gap` above and below."""
    W = src.width
    strip = _bg_fill(src, scr, y, y + gap + el.height + gap, uniform=True)
    strip.alpha_composite(el, (x, gap))
    return strip


def compose_all(src: Image.Image, scr: Screen, placements: list[Placement],
                feather: int = 24) -> Image.Image:
    """Stack slots first (they move whole bands), then the region ops. Inserts run
    lowest-on-screen first, so a shift never moves a region a later op still has to
    find; the overlays and replacements that follow read their regions through the
    shifts the inserts made."""
    for p in placements:
        if p.slot in SLOTS and p.element is None:
            raise SystemExit(f"{p.slot}: erase is for replace:<region> only — a stack slot "
                             f"places an element, it has nothing to repaint")
    stack = [(p.element, p.slot) for p in placements if p.slot in SLOTS]
    ops = [p for p in placements if p.slot not in SLOTS]
    for p in ops:
        op = p.slot.split(":", 1)[0]
        if ":" not in p.slot or op not in REGION_OPS:
            raise SystemExit(f"slot must be one of {SLOTS} or <op>:<region> with op in "
                             f"{REGION_OPS}, got {p.slot!r}")
        scr.region(p.slot.split(":", 1)[1])
    hero_off = disc_off = 0
    if stack:
        canvas, hero_off, disc_off = _compose_stack(src, scr, stack, feather)
    else:
        canvas = src.copy()
    shifts: list[tuple[int, int, int]] = []     # (y, shift for content above the footer, shift for the footer)
    spacer_left = _footer_spacer(scr) if scr.footer else 0
    footer_off = 0                              # how far the pinned footer has been pushed down

    def adj(r: Region) -> Region:
        """A region as it is on the canvas now: moved up with the band a stack slot
        slid (the hero's or the disclaimer's), then down by any insert above it."""
        dy = 0
        if scr.hero and scr.hero[0] <= r[1] < scr.hero[1]:
            dy -= hero_off
        elif scr.disclaimer and scr.disclaimer[0] <= r[1] < (scr.buttons_top or scr.size[1]):
            dy -= disc_off
        pinned = bool(scr.footer) and r[1] >= scr.footer[0]
        dy += sum(push if pinned else d for y, d, push in shifts if r[1] + dy >= y)
        return (r[0], r[1] + dy, r[2], r[3] + dy)

    # On a fixed or list screen, a replacement taller than its region grows the region:
    # the content below slides down by the difference, the way a VStack makes room for a
    # headline that wraps to a second line. Without it the element is centred over its
    # neighbours. A stack screen moves whole bands instead, so it never grows.
    grow: dict[str, int] = {}
    if scr.layout != "stack":
        for p in ops:
            op, name = p.slot.split(":", 1)
            if op == "replace" and p.element is not None:
                r = scr.region(name)
                extra = p.element.height - (r[3] - r[1])
                if extra > grow.get(name, 0):
                    grow[name] = extra
    inserts = [p for p in ops if p.slot.split(":", 1)[0] in ("after", "row")]
    inserts += [Placement(None, f"grow:{name}") for name in grow]
    for p in sorted(inserts, key=lambda p: -scr.region(p.slot.split(":", 1)[1])[1]):
        op, name = p.slot.split(":", 1)
        r = scr.region(name)
        if op == "grow":
            y = r[3]
            strip = _bg_fill(src, scr, y, y + grow[name], uniform=True)
        elif p.element is None:
            raise SystemExit(f"{p.slot}: nothing to insert (erase is for replace:)")
        elif p.element.width > canvas.width:
            raise SystemExit(f"{p.slot}: the band ({p.element.width}px) is wider than the capture")
        elif op == "row":
            if scr.layout != "list":
                raise SystemExit(f"row: needs a list screen; this one is {scr.layout!r}")
            # Before the group's bottom padding (16pt), so the card's own corners follow.
            y = r[3] - 16 * SCALE
            strip = _bg_fill(src, scr, y, y + p.element.height, uniform=True)
            strip.alpha_composite(p.element, (r[0], 0))
        else:
            gap = int(round(float(p.opts.get("gap", "0")) * SCALE))
            y = r[3]
            if p.element.width >= r[2] - r[0]:
                # A cloned band already carries its own spacing — lay it at the card's edge.
                strip = _bg_fill(src, scr, y, y + p.element.height, uniform=True)
                strip.alpha_composite(p.element, (r[0], 0))
            else:
                x = _place_in(r, p.element, p.opts.get("align", "center"))[0]
                _check_fits(canvas, p.element, x, 0, p.slot)
                strip = _strip_for(src, scr, y, p.element, x, gap)
        push = strip.height
        if scr.footer and y < scr.footer[0] + footer_off:
            absorbed = min(strip.height, spacer_left)
            spacer_left -= absorbed
            push = strip.height - absorbed
            _insert_strip_pinned(canvas, src, scr, y, strip, scr.footer[0] + footer_off, absorbed)
            footer_off += push
        else:
            _insert_strip(canvas, src, scr, y, strip)
        shifts.append((y, strip.height, push))
        # An insert pushes everything below it down, and what reaches the home zone is
        # covered — that is the list scrolling, which is what the app does. But it is
        # also how a treatment silently *deletes* something: the settings crisis-help
        # footer left the sheet that way, and a reviewer reads a missing control as a
        # proposal to remove it. Say what went under, so it is a decision.
        lost = _content_bottom(src, scr) + push - (canvas.height - scr.home_zone)
        if lost > 0:
            print(f"  ! {p.slot}: {lost // SCALE}pt of content slid under the home zone. "
                  f"The app scrolls, so nothing is removed — but the sheet no longer shows "
                  f"it. Say so in the design note, or place the element higher.", file=sys.stderr)

    for p in ops:
        op, name = p.slot.split(":", 1)
        if op in ("after", "row"):
            continue
        r = adj(scr.region(name))
        if name in grow:
            r = (r[0], r[1], r[2], r[3] + grow[name])
        align = p.opts.get("align", "center")
        if op == "replace":
            _op_replace(canvas, src, scr, r, p.element, align, p.slot, p.opts.get("valign", "center"),
                        _offset_px(p.opts, p.slot))
        else:
            if p.element is None:
                raise SystemExit(f"{p.slot}: at: needs an element (erase is for replace:)")
            _op_at(canvas, r, p.element, align, p.slot, _offset_px(p.opts, p.slot))
    return canvas


# ---------- the comparison sheet ---------------------------------------------

def sheet(app_root: str, panels: list[tuple], panel_w: int = 560) -> Image.Image:
    """Current | A | B | … with labels, on a warm neutral ground, soft shadows.

    A panel is `(label, image)` or `(label, image, control)`. Control panels are labelled
    in grey, treatments in the brand green; without the flag panel 0 is the control."""
    if not panels:
        raise SystemExit("nothing to lay out")
    scale = panel_w / panels[0][1].width
    ph = int(panels[0][1].height * scale)
    m, gap, label_h, top = 48, 64, 96, 36
    W = m * 2 + panel_w * len(panels) + gap * (len(panels) - 1)
    H = top + label_h + ph + m
    out = Image.new("RGB", (W, H), (0xF5, 0xF3, 0xEF))
    d = ImageDraw.Draw(out)
    x = m
    for i, panel in enumerate(panels):
        label, img = panel[0], panel[1]
        is_control = panel[2] if len(panel) > 2 else i == 0
        col = (0x6B, 0x6B, 0x6B) if is_control else (0x2C, 0x6F, 0x55)
        # A label that does not fit on one line at 10pt wraps to two before it shrinks:
        # four panels of long arm names at 8pt once ran into each other. Nothing is
        # cut — a label two lines cannot hold at 8pt is a label to shorten.
        pts, lines = 14.0, [label]
        while True:
            lab = font(app_root, "body-semibold", pts)
            lines = _wrap(label, lab, panel_w - 16) if pts <= 10 else [label]
            if (len(lines) <= 2 and all(lab.getlength(ln) <= panel_w - 16 for ln in lines)) or pts <= 8:
                break
            pts -= 0.5
        lh = sum(lab.getmetrics())
        ly = top + 20 - (lh // 2 if len(lines) > 1 else 0)
        for ln in lines:
            tb = d.textbbox((0, 0), ln, font=lab)
            d.text((x + (panel_w - (tb[2] - tb[0])) // 2, ly), ln, font=lab, fill=col)
            ly += lh
        # shadow
        sh = Image.new("RGBA", (panel_w + 40, ph + 40), (0, 0, 0, 0))
        ImageDraw.Draw(sh).rectangle((20, 24, 20 + panel_w, 24 + ph), fill=(0, 0, 0, 46))
        sh = sh.filter(ImageFilter.GaussianBlur(10))
        out.paste(sh, (x - 20, top + label_h - 20), sh)
        # Fit by aspect: a flow can mix a screen with a card of another shape, and a
        # stretched panel is a wrong picture.
        r = min(panel_w / img.width, ph / img.height)
        fitted = img.convert("RGB").resize((max(1, int(img.width * r)), max(1, int(img.height * r))), Image.LANCZOS)
        out.paste(fitted, (x + (panel_w - fitted.width) // 2, top + label_h + (ph - fitted.height) // 2))
        x += panel_w + gap
    return out


# ---------- variants ----------------------------------------------------------

@dataclass
class Variant:
    name: str
    slot: str
    element: str                     # laurel | line | review | badge_review | pill | circle_glyph | settings_row | text_block | erase
    text: Optional[str] = None
    style: Optional[str] = None      # None = the element's own default face
    label: Optional[str] = None
    source: Optional[str] = None
    extra: dict[str, str] = field(default_factory=dict)

    KNOWN = ("name", "slot", "element", "text", "style", "label", "source")
    TEXT_ELEMENTS = ("line", "review", "badge_review", "pill", "settings_row", "text_block", "chip",
                     "offer_card")
    # `sheet` carries copy in title=/subtitle=/cta= rather than text=, so it checks its own source.

    @classmethod
    def parse(cls, spec: str) -> "Variant":
        """`name=A;slot=top;element=laurel` — order-free key=value pairs."""
        kv: dict[str, str] = {}
        for part in spec.split(";"):
            if not part.strip():
                continue
            if "=" not in part:
                raise SystemExit(f"bad variant part {part!r} in {spec!r} (want key=value)")
            k, v = part.split("=", 1)
            kv[k.strip()] = v.strip()
        for req in ("name", "slot", "element"):
            if req not in kv:
                raise SystemExit(f"variant {spec!r} is missing {req}=")
        # Variants are lettered as the experiment arms they become, and in our
        # experiments `b` (and nil) is ALWAYS control — the Current panel. A proposal
        # lettered "B" would read as a treatment on the sheet and as control on the
        # server. Treatments are a, c, d, …
        if kv["name"].strip().lower() == "b":
            raise SystemExit("variant name 'b' is reserved: b (and nil) is always control — "
                             "that is the Current panel. Letter treatments a, c, d …")
        return cls(name=kv["name"], slot=kv["slot"], element=kv["element"],
                   text=kv.get("text"), style=kv.get("style"),
                   label=kv.get("label"), source=kv.get("source"),
                   extra={k: v for k, v in kv.items() if k not in cls.KNOWN})

    def _need(self, key: str) -> str:
        v = self.extra.get(key)
        if not v:
            raise SystemExit(f"variant {self.name}: element={self.element} needs {key}=")
        return v

    def _clone(self, ctx: Optional["RenderContext"]) -> tuple[Image.Image, Screen, str]:
        """`clone=<region>` on this screen, or `clone=<screen>:<region>` on a donor."""
        if ctx is None:
            raise SystemExit(f"variant {self.name}: element={self.element} clones chrome off a capture "
                             f"and needs the render context")
        spec = self._need("clone")
        if ":" in spec:
            screen, region = spec.split(":", 1)
            screen = normalize_screen(screen)
            return ctx.donor(screen), SCREENS[screen], region
        return ctx.src, ctx.scr, spec

    def render_element(self, app_root: str, ctx: Optional["RenderContext"] = None) -> Optional[Image.Image]:
        if self.element == "erase":
            return None
        if self.element == "laurel":
            self.source = self.source or LAUREL_SOURCE
            return element_laurel(app_root, self.text or LAUREL_TEXT)
        if self.element in self.TEXT_ELEMENTS:
            if not self.text:
                raise SystemExit(f"variant {self.name}: element={self.element} needs text=")
            if not self.source:
                # A free-text claim or quote with no provenance is exactly the thing this
                # leg exists to stop. Refuse rather than let it reach a reviewer as ours.
                raise SystemExit(f"variant {self.name}: element={self.element} needs source= "
                                 f"(where does this come from? for a review: the App Store; "
                                 f"for a label: the screen or component that already says it)")
        if self.element == "line":
            return element_line(app_root, self.text, self.style or "display")
        if self.element in ("review", "badge_review"):
            attribution = self.extra.get("attribution", "— App Store review")
            if self.element == "review":
                return element_review(app_root, self.text, attribution)
            return element_badge_review(app_root, self.text, attribution)
        if self.element == "pill":
            donor, dscr, region = self._clone(ctx)
            width = self.extra.get("width")
            return element_pill(donor, dscr.region(region), app_root, self.text,
                                float(width) if width else None)
        if self.element == "chip":
            donor, dscr, region = self._clone(ctx)
            return element_chip(donor, dscr.region(region), app_root, self.text,
                                self.extra.get("trailing"), float(self.extra.get("points", "15")),
                                self.extra.get("weight", "semibold"),
                                float(self.extra.get("glyph_points", "16")))
        if self.element == "offer_card":
            if ctx is None:
                raise SystemExit(f"variant {self.name}: offer_card clones the Profile and needs the render context")
            return element_offer_card(ctx.src, ctx.scr, app_root, self.text, self._need("pill"),
                                      self.extra.get("symbol", "gift"), ctx.donor("welcome"),
                                      SCREENS["welcome"].region("cta"),
                                      dismiss=self.extra.get("dismiss", "1") != "0")
        if self.element == "circle_glyph":
            donor, dscr, region = self._clone(ctx)
            return element_circle_glyph(donor, dscr.region(region), self._need("symbol"),
                                        points=float(self.extra.get("points", "16")),
                                        weight=self.extra.get("weight", "regular"))
        if self.element in ("settings_row", "text_block") and ctx is None:
            raise SystemExit(f"variant {self.name}: element={self.element} reads the capture and needs "
                             f"the render context")
        if self.element == "settings_row":
            return element_settings_row(ctx.src, ctx.scr, self.extra.get("clone", "row"), app_root,
                                        self.text, self._need("symbol"),
                                        self.extra.get("trailing", "chevron.right"))
        if self.element == "crop":
            donor, dscr, region = self._clone(ctx)
            fg = None
            if self.extra.get("key") == "ink":
                # On a redrawn sky the ground under the target is the sky named, not
                # the capture's — take the role from it rather than measuring pixels
                # that are about to be painted over.
                fg = sky_fg(self.extra["sky"]) if self.extra.get("sky") else _auto_fg(ctx.src, self.target_region(ctx.scr))
            h = self.extra.get("height")
            return element_crop(donor, dscr.region(region), self.extra.get("key"), fg, float(h) if h else None)
        if self.element == "overlay":
            if ctx is None:
                raise SystemExit(f"variant {self.name}: overlay needs the render context")
            return element_overlay(self._need("image"), ctx.src.size)
        if self.element == "sky":
            if ctx is None:
                raise SystemExit(f"variant {self.name}: sky fills the capture and needs the render context")
            return element_sky(self._need("sky"), ctx.src.size, halo=self.extra.get("halo", "1") != "0")
        if self.element == "signature":
            if ctx is None:
                raise SystemExit(f"variant {self.name}: signature reads the sky and needs the render context")
            if self.text and not self.source:
                raise SystemExit(f"variant {self.name}: a signature caption is copy and needs source= "
                                 f"(the address the app already publishes)")
            sky = self.extra.get("sky")
            if sky:
                fg, secondary = sky_fg(sky), SKIES[sky]["secondary"]
            else:
                fg, secondary = _auto_fg(ctx.src, self.target_region(ctx.scr)), ROLE_ALPHA["secondary"] / 255
            return element_signature(app_root, fg, secondary, self.text,
                                     points=float(self.extra.get("points", "26")),
                                     rule=self.extra.get("rule", "1") != "0")
        if self.element == "sheet":
            if ctx is None:
                raise SystemExit(f"variant {self.name}: sheet clones the app's bottom sheet and needs the render context")
            donor, dscr, _ = self._clone(ctx)
            for k in ("title", "cta", "symbol"):
                self._need(k)
            if not self.source:
                raise SystemExit(f"variant {self.name}: element=sheet carries copy and needs source=")
            cta_donor = ctx.donor("welcome")
            return element_sheet(donor, dscr, app_root, self.extra["title"], self.extra.get("subtitle", ""),
                                 self.text or "", self.extra["cta"], self.extra["symbol"],
                                 cta_donor, SCREENS["welcome"].region("cta"),
                                 secondary_region=SCREENS["welcome"].region("login"))
        if self.element == "text_block":
            role = self.extra.get("role", "primary")
            if role not in ROLE_ALPHA:
                raise SystemExit(f"variant {self.name}: role must be one of {sorted(ROLE_ALPHA)}")
            fg = _auto_fg(ctx.src, self.target_region(ctx.scr))
            underline = tuple(u for u in self.extra.get("underline", "").split("|") if u)
            kw: dict[str, Any] = {"align": self.extra.get("align", "center")}
            if self.extra.get("width"):
                kw["max_w"] = int(float(self.extra["width"]) * SCALE)
            return element_text_block(app_root, self.text, float(self.extra.get("points", "11")),
                                      self.extra.get("weight", "medium"), (*fg, ROLE_ALPHA[role]),
                                      underline=underline,
                                      style="display" if self.style == "display" else "body", **kw)
        raise SystemExit(f"variant {self.name}: unknown element {self.element!r}")

    def target_region(self, scr: Screen) -> Region:
        """Where this variant's main element lands — for auto-foreground on region ops;
        a stack slot reads the hero band, which is where its element sits."""
        if ":" in self.slot:
            return scr.region(self.slot.split(":", 1)[1])
        h = scr.hero or (0, 0)
        return (0, h[0], scr.size[0], h[1])

    def placements(self, app_root: str, ctx: Optional["RenderContext"] = None) -> list[Placement]:
        """Everything this variant places: the main element in its slot, plus an
        optional second element from `plus=<element>@<slot>` — `laurel` (the one
        fixed-copy element that needs no `text=`) or `erase`. Richer combinations are
        several `--variant` entries sharing one `name=`; `render` merges them."""
        out = [Placement(self.render_element(app_root, ctx), self.slot, dict(self.extra))]
        plus = self.extra.get("plus")
        if plus:
            if "@" not in plus:
                raise SystemExit(f"variant {self.name}: plus= wants <element>@<slot>, got {plus!r}")
            el, slot = plus.split("@", 1)
            el, slot = el.strip(), slot.strip()
            if el not in ("laurel", "erase"):
                raise SystemExit(f"variant {self.name}: plus= supports laurel or erase, got {el!r}; "
                                 f"for anything else add a second --variant with the same name=")
            if slot == self.slot:
                raise SystemExit(f"variant {self.name}: plus= slot must differ from slot={self.slot}")
            out.append(Placement(element_laurel(app_root) if el == "laurel" else None, slot))
        return out

    def display_label(self) -> str:
        base = self.label or f"{self.name} · {self.element}, {self.slot}"
        plus = self.extra.get("plus")
        return base if (self.label or not plus) else f"{base} + {plus.replace('@', ' @ ')}"


def _store_suffix(asis: str, screen: str) -> tuple[str, str]:
    """The screenshot store names a capture `<key>[_<sky>].png`, where the key is the
    screen's accessibility identifier — `welcome`, `settings_screen`. Split a capture's
    stem into (key, sky suffix) for the model `screen`, so a donor can be found beside it."""
    stem = os.path.splitext(os.path.basename(asis))[0].lower()
    for key in (f"{screen}_screen", screen):
        if stem.startswith(key):
            return key, stem[len(key):]
    return stem, ""


class RenderContext:
    """What an element may read while it renders: the capture, its model, and the donor
    captures other screens lend it (`--donor screen=path`, or found beside `--asis` in
    the store, same sky)."""

    def __init__(self, src: Image.Image, scr: Screen, screen: str, asis: str,
                 donors: dict[str, str]):
        self.src, self.scr, self.screen, self.asis = src, scr, screen, asis
        self._paths = dict(donors)
        self._images: dict[str, Image.Image] = {}

    def donor(self, screen: str) -> Image.Image:
        if screen == self.screen:
            return self.src
        if screen in self._images:
            return self._images[screen]
        if screen not in SCREENS:
            raise SystemExit(f"no screen model for donor {screen!r}; known: {sorted(SCREENS)}")
        path = self._paths.get(screen)
        if not path:
            _, sky = _store_suffix(self.asis, self.screen)
            d = os.path.dirname(os.path.abspath(self.asis))
            # The store names files by identifier with its own case
            # (`customPracticeBottomSheet_light.png`); match without it.
            listing = {f.lower(): f for f in os.listdir(d)} if os.path.isdir(d) else {}
            for cand in (f"{screen}{sky}.png", f"{screen}_screen{sky}.png"):
                if cand.lower() in listing:
                    path = os.path.join(d, listing[cand.lower()])
                    print(f"  donor {screen}: {listing[cand.lower()]} (beside --asis, same sky)")
                    break
        if not path:
            raise SystemExit(f"element needs a capture of {screen!r} to clone from: pass "
                             f"--donor {screen}=<png>, or render from the store where the "
                             f"same-sky capture sits beside --asis")
        im = Image.open(path).convert("RGBA")
        if im.size != SCREENS[screen].size:
            print(f"  ! donor {screen} is {im.size}, model expects {SCREENS[screen].size}",
                  file=sys.stderr)
        self._images[screen] = im
        return im


# ---------- commands ----------------------------------------------------------

def cmd_render(args: argparse.Namespace) -> int:
    screen = normalize_screen(args.screen)
    scr = SCREENS.get(screen)
    if not scr:
        raise SystemExit(f"unknown screen {args.screen!r} (read as {screen!r}); "
                         f"known: {sorted(SCREENS)}. Add a measured model to SCREENS.")
    args.screen = screen
    if scr.pre_consent:
        print(f"  ! {screen} is drawn before consent — no experiment can bucket or expose here. "
              f"The idea needs a bucketing path in testPlan.instrumentation, or a post-consent screen.",
              file=sys.stderr)
    src = Image.open(args.asis).convert("RGBA")
    if src.size != scr.size:
        print(f"  ! capture is {src.size}, screen model expects {scr.size} — regions may be off",
              file=sys.stderr)
    variants = [Variant.parse(s) for s in args.variant]
    if not variants:
        raise SystemExit("give at least one --variant")
    donors: dict[str, str] = {}
    for d in args.donor or []:
        if "=" not in d:
            raise SystemExit(f"--donor wants screen=path, got {d!r}")
        k, v = d.split("=", 1)
        donors[normalize_screen(k)] = v
    ctx = RenderContext(src, scr, screen, args.asis, donors)
    # Several --variant entries may share a name: they are one arm with several
    # placements (erase the disclaimer AND write it again below the buttons).
    groups: dict[str, list[Variant]] = {}
    for v in variants:
        groups.setdefault(v.name, []).append(v)
    os.makedirs(args.out, exist_ok=True)
    stem = args.idea.lower().replace("-", "")
    outputs: dict[str, Any] = {"idea": args.idea, "screen": args.screen, "files": {}, "variants": []}

    cur = os.path.join(args.out, f"{stem}_current.png")
    src.convert("RGB").save(cur)
    outputs["files"]["current"] = cur
    # The existing screen is the control arm, and control is `b` in every one of our
    # experiments — say so on the sheet, so the letters mean the same thing here and
    # on the experiment server.
    panels = [("Current · control (b)", src)]
    crop = None
    if args.crop:
        crop = tuple(int(v) for v in args.crop.split(","))
        if len(crop) != 4:
            raise SystemExit("--crop wants x0,y0,x1,y1")
        src_out = src.crop(crop)
        src_out.convert("RGB").save(cur)
        panels = [("Current · control (b)", src_out)]
    for name, parts in groups.items():
        v = parts[0]
        placements = [pl for part in parts for pl in part.placements(args.app_root, ctx)]
        img = compose_all(src, scr, placements)
        if crop:
            img = img.crop(crop)
        p = os.path.join(args.out, f"{stem}_{name}.png")
        img.convert("RGB").save(p)
        outputs["files"][name] = p
        outputs["variants"].append({
            "name": name, "slot": v.slot, "element": v.element, "plus": v.extra.get("plus"),
            "text": v.text or (LAUREL_TEXT if v.element == "laurel" else None),
            "source": v.source, "label": v.display_label(),
            "placements": [{"slot": part.slot, "element": part.element, "text": part.text,
                            "source": part.source, "clone": part.extra.get("clone"),
                            "symbol": part.extra.get("symbol")} for part in parts],
        })
        panels.append((v.display_label(), img))
        for part in parts:
            print(f"  {name}: {part.element} @ {part.slot}  -> {p}")
            print(f"      source: {part.source}")

    cmp_path = os.path.join(args.out, f"{stem}_compare.png")
    sheet(args.app_root, panels).save(cmp_path)
    outputs["files"]["compare"] = cmp_path
    print(f"  sheet: {cmp_path}")
    mpath = os.path.join(args.out, f"{stem}_design.json")
    # A re-render must not forget the flows `flow` recorded here: without them
    # `attach` would upload the flow sheets under their bare stems and share-ideas
    # would never post them.
    if os.path.exists(mpath):
        with open(mpath, encoding="utf-8") as fh:
            prev = json.load(fh)
        for key, path in (prev.get("files") or {}).items():
            if key.startswith("flow_"):
                outputs["files"][key] = path
        if prev.get("flows"):
            outputs["flows"] = prev["flows"]
    with open(mpath, "w", encoding="utf-8") as fh:
        json.dump(outputs, fh, indent=2, ensure_ascii=False)
    return 0


def _load_manifest(path: Optional[str], files: list[str]) -> dict[str, Any]:
    """The `{idea}_design.json` `render` wrote — given explicitly, or found beside the
    first file. Missing is fine: uploads then keep their file names."""
    cands = [path] if path else []
    if files:
        d = os.path.dirname(os.path.abspath(files[0]))
        cands += [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.endswith("_design.json")]
    for c in cands:
        if c and os.path.exists(c):
            with open(c, encoding="utf-8") as fh:
                return json.load(fh)
    return {}


# Notion refuses a file upload whose name is longer than this, and the whole attach fails.
NOTION_FILE_NAME_MAX = 100


def _fit_name(name: str) -> str:
    """Trim a caption-derived file name to Notion's cap, keeping the extension and
    ending the stem on an ellipsis so the reply caption still reads as cut."""
    if len(name) <= NOTION_FILE_NAME_MAX:
        return name
    stem, ext = os.path.splitext(name)
    return stem[: NOTION_FILE_NAME_MAX - len(ext) - 1].rstrip() + "…" + ext


def _upload_name(path: str, manifest: dict[str, Any]) -> str:
    return _fit_name(_caption_name(path, manifest))


def _caption_name(path: str, manifest: dict[str, Any]) -> str:
    """The name a file is stored under on the row — which share-ideas turns into the
    caption of that file's reply in the thread. So it is the human label from the
    manifest (`a · Trust badge, above the orb.png`), not `idea1_a.png`. The sheet and
    the current capture get fixed names; anything the manifest does not know keeps
    its file name."""
    base = os.path.basename(path)
    if not manifest:
        return base
    fm = manifest.get("files") or {}
    for key, p in fm.items():
        if os.path.basename(p) != base:
            continue
        if key == "compare":
            return "Current vs Proposed.png"
        if key == "current":
            return "Current · control (b).png"
        if key.startswith("flow_"):
            name = key[len("flow_"):]
            for fl in manifest.get("flows") or []:
                if fl.get("name") == name:
                    return f"Flow {name} · {fl.get('title')}".replace("/", "∕") + ".png"
            return f"Flow {name}.png"
        for v in manifest.get("variants") or []:
            if v.get("name") == key:
                label = (v.get("label") or f"{key} · {v.get('element')}, {v.get('slot')}")
                return label.replace("/", "∕") + ".png"
    return base


def cmd_attach(args: argparse.Namespace) -> int:
    """Upload the rendered files to the idea row's Screenshot property.

    Three-step Notion upload (create → bytes → attach), same as record-ideas. Replaces
    the property's file list so a re-render does not pile up stale variants.
    """
    sys.path.insert(0, HERE)
    from notion_rest import MCPFallback, notion  # noqa: PLC0415 — vendored beside this file
    if not notion.available:
        raise MCPFallback("NOTION_TOKEN is not set.")
    import requests  # noqa: PLC0415

    row = _find_row(notion, args.data_source_id, args.idea)
    if not row:
        raise SystemExit(f"{args.idea}: no row found")
    manifest = _load_manifest(args.manifest, args.files)
    tok = os.environ["NOTION_TOKEN"]
    hdr = {"Authorization": f"Bearer {tok}", "Notion-Version": "2025-09-03"}
    files = []
    for path in args.files:
        name = _upload_name(path, manifest)
        c = requests.post("https://api.notion.com/v1/file_uploads",
                          headers={**hdr, "Content-Type": "application/json"},
                          json={"filename": name, "content_type": "image/png"}, timeout=30)
        c.raise_for_status()
        up = c.json()
        with open(path, "rb") as fh:
            requests.post(up["upload_url"], headers=hdr,
                          files={"file": (name, fh, "image/png")}, timeout=180).raise_for_status()
        files.append({"type": "file_upload", "name": name, "file_upload": {"id": up["id"]}})
        print(f"  + {name}")
    notion.update_page_props(row["id"], {"Screenshot": {"files": files}})
    print(f"~ {args.idea}: Screenshot property set ({len(files)} file(s))")
    return 0


def _find_row(notion: Any, ds_id: str, ref: str) -> Optional[dict[str, Any]]:
    """Resolve `IDEA-N` (or a bare number) to its row."""
    num = ref.upper().replace("IDEA-", "").strip()
    if not num.isdigit():
        raise SystemExit(f"idea ref must look like IDEA-12, got {ref!r}")
    for r in notion.query_data_source(ds_id):
        uid = (r["properties"].get("Idea ID") or {}).get("unique_id") or {}
        if str(uid.get("number")) == num:
            return r
    return None


def cmd_wordmark(args: argparse.Namespace) -> int:
    """Rasterize the app's `aurieLogo` PDF (a template asset) into `references/assets/`
    as an alpha mask the `signature` element tints per sky. macOS only (AppKit)."""
    src = os.path.join(HERE, "wordmark.swift")
    exe = os.path.join(tempfile.gettempdir(), "aurie_wordmark_export")
    pdf = os.path.join(args.app_root, "AuraConsumerApp/Resources/Assets.xcassets/aurieLogo.imageset/aurieLogo.pdf")
    if not os.path.exists(pdf):
        raise SystemExit(f"asset not found: {pdf}\n  pass --app-root to the AuraConsumerApp checkout")
    try:
        subprocess.run(["swiftc", "-O", "-o", exe, src], check=True)
        subprocess.run([exe, pdf, WORDMARK_ASSET, str(args.height)], check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        raise SystemExit(f"could not build the exporter (swiftc, macOS): {e}") from e
    im = Image.open(WORDMARK_ASSET).convert("RGBA")
    out = Image.new("RGBA", im.size, (0, 0, 0, 0))
    out.putalpha(im.split()[-1])
    out.save(WORDMARK_ASSET)
    print(f"wrote {WORDMARK_ASSET} {im.size[0]}x{im.size[1]}")
    return 0


def cmd_symbols(args: argparse.Namespace) -> int:
    """Export SF Symbols with AppKit into references/assets/symbols — macOS only, once
    per glyph; renders then read the PNGs anywhere."""
    src = os.path.join(HERE, "sfsymbol.swift")
    exe = os.path.join(tempfile.gettempdir(), "design_idea_sfsymbol")
    try:
        subprocess.run(["swiftc", "-O", "-o", exe, src], check=True)
    except (OSError, subprocess.CalledProcessError) as e:
        raise SystemExit(f"could not build the exporter (swiftc, macOS): {e}") from e
    os.makedirs(SYMBOLS, exist_ok=True)
    for name in args.name:
        for w in args.weight:
            out = os.path.join(SYMBOLS, f"{name}-{w}.png")
            subprocess.run([exe, name, str(SYMBOL_EXPORT_POINTS), w, out, "1"], check=True)
            print(f"  + {os.path.relpath(out, HERE)}")
    return 0


def cmd_cutout(args: argparse.Namespace) -> int:
    """Turn a capture of system UI — the iOS share sheet over Safari, a permission
    alert — into a full-frame overlay: rows from `--top` down are kept (the first
    `--corners` rows keyed on luminance, so the sheet's rounded corners let the host
    through), everything above becomes a scrim. The system draws these; we never do."""
    im = Image.open(args.image).convert("RGBA")
    W, H = im.size
    out = Image.new("RGBA", (W, H), (0, 0, 0, int(255 * args.scrim)))
    band = im.crop((0, args.top, W, H))
    mask = Image.new("L", band.size, 255)
    if args.corners:
        mask.paste(band.crop((0, 0, W, args.corners)).convert("L").point(lambda v: 255 if v > args.key else 0), (0, 0))
    band.putalpha(mask)
    out.alpha_composite(band, (0, args.top))
    # The sheet's header names what is being shared. Re-letter it for the payload the
    # idea proposes — in the system font, which is what the system draws it in — and
    # swap its thumbnail for the thing shared. Each spec is x0,y0,x1,y1=value.
    for spec in args.relabel or []:
        rect, value = spec.split("=", 1)
        x0, y0, x1, y1 = (int(v) for v in rect.split(","))
        text, _, style = value.partition("|")
        pts, _, weight = (style or "17|semibold").partition("|")
        fill = out.getpixel((x1 - 2, (y0 + y1) // 2))
        ImageDraw.Draw(out).rectangle((x0, y0, x1, y1), fill=fill)
        ink = _auto_fg(out, (x0, y0, x1, y1))
        alpha = 255 if (weight or "semibold") != "regular" else ROLE_ALPHA["secondary"]
        block = _text_block_left([text], font(args.app_root, f"body-{weight or 'semibold'}", float(pts or 17)), (*ink, alpha))
        out.alpha_composite(block, (x0, y0 + ((y1 - y0) - block.height) // 2))
    for spec in args.paste or []:
        rect, path = spec.split("=", 1)
        x0, y0, x1, y1 = (int(v) for v in rect.split(","))
        im2 = Image.open(path).convert("RGBA")
        r = max((x1 - x0) / im2.width, (y1 - y0) / im2.height)
        im2 = im2.resize((max(1, int(im2.width * r)), max(1, int(im2.height * r))), Image.LANCZOS)
        im2 = im2.crop(((im2.width - (x1 - x0)) // 2, (im2.height - (y1 - y0)) // 2,
                        (im2.width - (x1 - x0)) // 2 + (x1 - x0), (im2.height - (y1 - y0)) // 2 + (y1 - y0)))
        radius = int(args.paste_radius * SCALE)
        m = Image.new("L", im2.size, 0)
        ImageDraw.Draw(m).rounded_rectangle((0, 0, im2.width - 1, im2.height - 1), radius=radius, fill=255)
        im2.putalpha(ImageChops.multiply(im2.getchannel("A"), m))
        out.alpha_composite(im2, (x0, y0))
    out.save(args.out)
    print(f"  cutout: {args.out} (kept rows >= {args.top}, scrim {args.scrim})")
    return 0


def _text_panel(app_root: str, text: str, size: tuple[int, int]) -> Image.Image:
    W, H = size
    out = Image.new("RGBA", size, (0xF5, 0xF3, 0xEF, 255))
    fnt = font(app_root, "body-regular", 17)
    pad = 20 * SCALE
    lines: list[str] = []
    for para in text.replace("\\n", "\n").split("\n"):
        lines += _wrap(para, fnt, W - 4 * pad) if para.strip() else [""]
    block = _text_block_left(lines, fnt, (*TINT, 255))
    bw, bh = block.width + 2 * pad, block.height + 2 * pad
    x, y = (W - bw) // 2, (H - bh) // 2
    ImageDraw.Draw(out).rounded_rectangle((x, y, x + bw, y + bh), radius=18 * SCALE, fill=(255, 255, 255, 255),
                                          outline=(0xE4, 0xDF, 0xD6, 255), width=2)
    out.alpha_composite(block, (x + pad, y + pad))
    return out


def cmd_flow(args: argparse.Namespace) -> int:
    """Lay rendered screens out as one flow — `1 · Tap Share Aurie` → `2 · The sheet`
    → `3 · iOS share sheet` — and record it in the idea's manifest, so `attach` names
    it `Flow <name> · <title>.png` and share-ideas posts it as one reply per flow."""
    panels: list[tuple[str, Image.Image]] = []
    size = None
    for spec in args.panel:
        kv = dict(part.split("=", 1) for part in spec.split(";") if "=" in part)
        if "image" in kv:
            im = Image.open(kv["image"]).convert("RGBA")
            size = size or im.size
            panels.append((kv.get("label", os.path.basename(kv["image"])), im))
        elif "text" in kv:
            # What a system app opens with — the prefilled message, an email's subject
            # and body — shown as text on a plain panel. Messages and Mail are not on the
            # simulator, and a drawn look-alike of either would be the sticker this leg
            # exists to avoid.
            panels.append((kv.get("label", "Payload"), _text_panel(args.app_root, kv["text"], size or (1206, 2622))))
        else:
            raise SystemExit(f"--panel needs image= or text= (got {spec!r})")
    if not panels:
        raise SystemExit("give at least one --panel")
    os.makedirs(args.out, exist_ok=True)
    stem = args.idea.lower().replace("-", "")
    path = os.path.join(args.out, f"{stem}_flow_{args.name}.png")
    sheet(args.app_root, panels, panel_w=args.panel_width).save(path)
    mpath = os.path.join(args.out, f"{stem}_design.json")
    manifest: dict[str, Any] = {"idea": args.idea, "files": {}, "variants": []}
    if os.path.exists(mpath):
        with open(mpath, encoding="utf-8") as fh:
            manifest = json.load(fh)
    manifest.setdefault("files", {})[f"flow_{args.name}"] = path
    flows = [f for f in manifest.get("flows") or [] if f.get("name") != args.name]
    flows.append({"name": args.name, "title": args.title, "panels": [p[0] for p in panels]})
    manifest["flows"] = flows
    with open(mpath, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
    print(f"  flow {args.name}: {path}")
    return 0


def _parse_panel_spec(spec: str) -> dict[str, str]:
    return {k.strip(): v.strip() for k, v in
            (part.split("=", 1) for part in spec.split(";") if "=" in part)}


def cmd_compare(args: argparse.Namespace) -> int:
    """The Current-vs-Proposed sheet for an arm that spans several screens (Profile and
    Settings), laid out from screens `render` already composed — one `render` per screen,
    each into its own `--out`. Records the sheet as the manifest's `compare`, so `attach`
    names it `Current vs Proposed.png`, which is the file name share-ideas keys on.

    `--panel label=…;image=<png>[;control=1][;arm=<letter>]`. Panels without `control=`
    are treatments; `control=1` greys the label. `arm=` also registers the image as that
    arm's file, labelled for the reply caption."""
    panels: list[tuple[str, Image.Image, bool]] = []
    arms: list[tuple[str, str, str]] = []
    for spec in args.panel:
        kv = _parse_panel_spec(spec)
        if "image" not in kv or "label" not in kv:
            raise SystemExit(f"--panel needs label= and image= (got {spec!r})")
        control = kv.get("control", "0").lower() in ("1", "true", "yes")
        panels.append((kv["label"], Image.open(kv["image"]).convert("RGBA"), control))
        if kv.get("arm"):
            if kv["arm"].strip().lower() == "b":
                raise SystemExit("arm 'b' is reserved: b (and nil) is always control")
            arms.append((kv["arm"], kv["label"], kv["image"]))
    if len(panels) < 2:
        raise SystemExit("give at least two --panel: the current screens and the proposed ones")
    if not any(c for _, _, c in panels):
        raise SystemExit("no panel is control=1 — the current screens are the control arm (b)")
    os.makedirs(args.out, exist_ok=True)
    stem = args.idea.lower().replace("-", "")
    path = os.path.join(args.out, f"{stem}_compare.png")
    sheet(args.app_root, panels, panel_w=args.panel_width).save(path)
    mpath = os.path.join(args.out, f"{stem}_design.json")
    manifest: dict[str, Any] = {"idea": args.idea, "files": {}, "variants": []}
    if os.path.exists(mpath):
        with open(mpath, encoding="utf-8") as fh:
            manifest = json.load(fh)
    manifest.setdefault("files", {})["compare"] = path
    manifest.setdefault("variants", [])
    for name, label, image in arms:
        manifest["files"][name] = image
        manifest["variants"] = [v for v in manifest["variants"] if v.get("name") != name]
        manifest["variants"].append({"name": name, "label": label})
    with open(mpath, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
    print(f"  sheet: {path}")
    return 0


AURIE_FEATURES_DS = "8d14f3eb-0c55-4f25-9322-ab2b2a69f092"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="design_idea",
                                description="Idea in, Current-vs-Proposed screenshots out.")
    p.add_argument("--app-root", default=os.environ.get("AURA_APP_ROOT", DEFAULT_APP_ROOT),
                   help="AuraConsumerApp checkout (fonts live there)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("render", help="Compose TO-BE variants onto a real capture")
    sp.add_argument("--idea", required=True, help="IDEA-N — names the output files")
    sp.add_argument("--screen", required=True,
                    help="the screen model — the row's Screen value is accepted as-is "
                         f"(`welcome_screen` → `welcome`); known: {', '.join(sorted(SCREENS))}")
    sp.add_argument("--asis", required=True, help="PNG capture of the current screen (3x)")
    sp.add_argument("--variant", action="append", default=[],
                    help="name=a;slot=<slot>;element=<element>[;text=…;source=…;label=…;…] "
                         "(repeatable; entries sharing a name= are one arm). slot: top|mid|bottom "
                         "on a stack screen, or replace:<region> | at:<region> | after:<region> | "
                         "row:<group>. element: laurel | line | review | badge_review | pill "
                         "(clone=welcome:cta[;width=pt]) | circle_glyph (clone=<region>;symbol=…) "
                         "| settings_row (symbol=…[;trailing=…;clone=row|group]) | text_block "
                         "([;points=;weight=;role=;underline=a|b]) | sheet (clone=custompracticebottomsheet:sheet;"
                         "title=…;subtitle=…;text=<body>;cta=…;symbol=…;source=…) | overlay (image=<png>) | erase. "
                         "Anything with text= "
                         "needs source=. Also: align=left|center|right, gap=pt, plus=laurel@<slot>")
    sp.add_argument("--crop", help="x0,y0,x1,y1 — cut every output to this frame (a 9:16 card)")
    sp.add_argument("--donor", action="append", default=[],
                    help="screen=path — a capture of another screen to clone chrome from "
                         "(default: the same-sky capture beside --asis in the store)")
    sp.add_argument("--out", required=True, help="output directory")
    sp.set_defaults(func=cmd_render)

    sp = sub.add_parser("cutout", help="Cut a captured system sheet into a full-frame overlay")
    sp.add_argument("--image", required=True, help="a capture with the system UI showing")
    sp.add_argument("--top", type=int, required=True, help="first row of the sheet (px)")
    sp.add_argument("--corners", type=int, default=140, help="rows keyed on luminance for the rounded corners")
    sp.add_argument("--key", type=int, default=200, help="luminance above which a corner-row pixel is sheet")
    sp.add_argument("--scrim", type=float, default=0.4, help="opacity of the scrim above the sheet")
    sp.add_argument("--relabel", action="append", default=[],
                    help="x0,y0,x1,y1=text[|points|weight] — re-letter a header line in the system font")
    sp.add_argument("--paste", action="append", default=[],
                    help="x0,y0,x1,y1=<png> — swap a thumbnail/preview for the thing shared (aspect-fill)")
    sp.add_argument("--paste-radius", type=float, default=10, help="corner radius of pasted images, pt")
    sp.add_argument("--out", required=True)
    sp.set_defaults(func=cmd_cutout)

    sp = sub.add_parser("flow", help="Lay rendered screens out as one flow sheet")
    sp.add_argument("--idea", required=True)
    sp.add_argument("--name", required=True, help="the arm this flow belongs to: a, c, d …")
    sp.add_argument("--title", required=True, help="what the flow shows, e.g. after the tap on Share Aurie")
    sp.add_argument("--panel", action="append", default=[], help="label=…;image=<png> (repeatable, in order)")
    sp.add_argument("--panel-width", type=int, default=560)
    sp.add_argument("--out", required=True)
    sp.set_defaults(func=cmd_flow)

    sp = sub.add_parser("compare", help="Current-vs-Proposed sheet for an arm spanning several screens")
    sp.add_argument("--idea", required=True)
    sp.add_argument("--panel", action="append", default=[],
                    help="label=…;image=<png>[;control=1][;arm=<letter>] (repeatable, in order)")
    sp.add_argument("--panel-width", type=int, default=560)
    sp.add_argument("--out", required=True)
    sp.set_defaults(func=cmd_compare)

    sp = sub.add_parser("wordmark", help="Rasterize the app's aurieLogo asset for the signature element (macOS)")
    sp.add_argument("--app-root", default=os.environ.get("AURA_APP_ROOT", DEFAULT_APP_ROOT))
    sp.add_argument("--height", type=int, default=480, help="pixel height of the mask")
    sp.set_defaults(func=cmd_wordmark)

    sp = sub.add_parser("symbols", help="Export SF Symbols to references/assets/symbols (macOS)")
    sp.add_argument("--name", nargs="+", required=True, help="symbol names, e.g. square.and.arrow.up")
    sp.add_argument("--weight", nargs="+", default=["regular", "medium", "semibold"])
    sp.set_defaults(func=cmd_symbols)

    sp = sub.add_parser("attach", help="Put rendered files on the row's Screenshot property")
    sp.add_argument("--idea", required=True)
    sp.add_argument("--files", nargs="+", required=True)
    sp.add_argument("--manifest", help="the {idea}_design.json from `render` (default: found "
                                       "beside the first file) — names uploads by variant label")
    sp.add_argument("--data-source-id", default=AURIE_FEATURES_DS)
    sp.set_defaults(func=cmd_attach)
    return p


def main(argv: Optional[list[str]] = None) -> int:
    # Sources and labels carry en-dashes and curly quotes; a cp1252 console would
    # otherwise raise on the first print.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
