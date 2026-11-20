"""Tests for the design-idea renderer.

Synthetic captures stand in for the real screen: a 1206x2622 canvas with distinct flat
colours in each measured band, so a test can assert *which pixels moved where* without
depending on fonts or on the real screenshot. Font-dependent paths (the laurel badge, the
sheet labels) are exercised only when the app checkout is present.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO, "plugins", "product", "skills", "design-idea", "scripts")
sys.path.insert(0, SCRIPTS)

# The renderer hard-exits without Pillow (a clear message beats a stack trace for a
# user), which would turn this whole module into a collection *error* on a machine
# that lacks it. Skip the module instead — CI installs Pillow, so there it runs.
try:
    from PIL import Image  # noqa: E402
except ImportError as _e:  # pragma: no cover
    raise unittest.SkipTest("Pillow not installed; design-idea renderer tests skipped") from _e

import design_idea as d  # noqa: E402

# The display face is the only one that lives in the app checkout — the body face is
# SF Pro, read from the system, so it needs no checkout at all.
APP_ROOT_PRESENT = os.path.exists(os.path.join(d.DEFAULT_APP_ROOT, d.FONTS["display"]))

WELCOME = d.SCREENS["welcome"]
BG = (200, 200, 210, 255)
HERO = (255, 0, 0, 255)
DISC = (0, 0, 255, 255)
CTA = (0, 255, 0, 255)


def synthetic_capture() -> Image.Image:
    """Flat colour per band so moves are assertable by sampling one pixel."""
    im = Image.new("RGBA", WELCOME.size, BG)
    px = im.load()
    W, H = im.size
    for y in range(*WELCOME.hero):
        for x in range(W):
            px[x, y] = HERO
    for y in range(*WELCOME.disclaimer):
        for x in range(W):
            px[x, y] = DISC
    for y in range(WELCOME.buttons_top, H):
        for x in range(W):
            px[x, y] = CTA
    return im


def element(w: int = 400, h: int = 200) -> Image.Image:
    return Image.new("RGBA", (w, h), (10, 10, 10, 255))


class VariantParse(unittest.TestCase):
    def test_parses_order_free_pairs(self):
        v = d.Variant.parse("slot=top;name=A;element=laurel;label=A · top")
        self.assertEqual((v.name, v.slot, v.element, v.label), ("A", "top", "laurel", "A · top"))

    def test_missing_required_key_refuses(self):
        with self.assertRaises(SystemExit):
            d.Variant.parse("name=A;element=laurel")

    def test_bad_part_refuses(self):
        with self.assertRaises(SystemExit):
            d.Variant.parse("name=A;slot=top;element=laurel;oops")

    def test_b_is_reserved_for_control(self):
        # In every experiment `b` (and nil) is control — the existing screen. A proposal
        # lettered b would read as a treatment on the sheet and as control on the server.
        for name in ("b", "B", " b "):
            with self.assertRaises(SystemExit) as cm:
                d.Variant.parse(f"name={name};slot=top;element=laurel")
            self.assertIn("control", str(cm.exception))
        d.Variant.parse("name=c;slot=top;element=laurel")  # treatments are a, c, d …

    def test_line_without_source_is_refused(self):
        # A claim with no provenance is the failure this leg exists to stop.
        v = d.Variant.parse("name=c;slot=mid;element=line;text=Calm in 2 minutes")
        with self.assertRaises(SystemExit) as cm:
            v.render_element(d.DEFAULT_APP_ROOT)
        self.assertIn("source=", str(cm.exception))

    def test_line_without_text_is_refused(self):
        v = d.Variant.parse("name=c;slot=mid;element=line;source=App Store")
        with self.assertRaises(SystemExit):
            v.render_element(d.DEFAULT_APP_ROOT)

    def test_laurel_gets_default_source(self):
        v = d.Variant.parse("name=A;slot=top;element=laurel")
        if not APP_ROOT_PRESENT:
            self.skipTest("app checkout with fonts not present")
        v.render_element(d.DEFAULT_APP_ROOT)
        self.assertEqual(v.source, d.LAUREL_SOURCE)

    def test_display_label_falls_back(self):
        v = d.Variant.parse("name=C;slot=bottom;element=laurel")
        self.assertEqual(v.display_label(), "C · laurel, bottom")


class Compose(unittest.TestCase):
    def setUp(self):
        self.src = synthetic_capture()

    def _px(self, im, y, x=600):
        return im.getpixel((x, y))

    def test_unknown_slot_refuses(self):
        with self.assertRaises(SystemExit):
            d.compose(self.src, WELCOME, element(), "left")

    def test_top_moves_nothing(self):
        out = d.compose(self.src, WELCOME, element(), "top")
        h0, h1 = WELCOME.hero
        self.assertEqual(self._px(out, h0 + 5), HERO)
        self.assertEqual(self._px(out, h1 - 5), HERO)
        self.assertEqual(self._px(out, WELCOME.disclaimer[0] + 5), DISC)
        # element landed in the empty top zone
        z0, z1 = WELCOME.top_zone
        self.assertEqual(self._px(out, (z0 + z1) // 2), (10, 10, 10, 255))

    def test_mid_slides_hero_up_and_repaints_exposed_strip(self):
        el = element(h=200)  # needs 260px; the gap is 140 -> hero moves up 120
        out = d.compose(self.src, WELCOME, el, "mid")
        h0, h1 = WELCOME.hero
        delta = 200 + 2 * WELCOME.pad - (WELCOME.disclaimer[0] - h1)
        self.assertEqual(delta, 120)
        # hero now starts `delta` higher (past its feather ramp)
        self.assertEqual(self._px(out, h0 - delta + 100), HERO)
        # the strip it exposed is background again, not a red ghost — sampled beside
        # the element (400px wide, centred), which now occupies that strip's middle
        self.assertEqual(self._px(out, h1 - 5, x=100), BG)
        # the disclaimer did not move
        self.assertEqual(self._px(out, WELCOME.disclaimer[0] + 5), DISC)
        # CTA untouched
        self.assertEqual(self._px(out, WELCOME.buttons_top + 5), CTA)

    def test_mid_with_room_moves_nothing(self):
        out = d.compose(self.src, WELCOME, element(h=60), "mid")  # 120 <= 140 gap
        h0, h1 = WELCOME.hero
        self.assertEqual(self._px(out, h0 + 5), HERO)
        self.assertEqual(self._px(out, h1 - 5), HERO)

    def test_bottom_slides_disclaimer_and_hero_only_as_needed(self):
        el = element(h=200)  # needs 260; CTA gap is 50 -> disclaimer up 210
        out = d.compose(self.src, WELCOME, el, "bottom")
        d0, d1 = WELCOME.disclaimer
        disc_delta = 200 + 2 * WELCOME.pad - (WELCOME.buttons_top - d1)
        self.assertEqual(disc_delta, 210)
        # disclaimer moved up by disc_delta
        self.assertEqual(self._px(out, d0 - disc_delta + 100), DISC)
        # what it exposed is background — sampled beside the element, which is centred
        self.assertEqual(self._px(out, d1 - 5, x=100), BG)
        # the hero only moves by what the mid gap cannot absorb: 210 - (140 - 30) = 100
        h0, h1 = WELCOME.hero
        hero_delta = disc_delta - ((d0 - h1) - WELCOME.pad)
        self.assertEqual(hero_delta, 100)
        self.assertEqual(self._px(out, h0 - hero_delta + 150), HERO)
        # the top of the strip the hero exposed is background; its lower part is now
        # under the disclaimer, which slid up into the mid gap
        self.assertEqual(self._px(out, h1 - hero_delta + 5, x=100), BG)
        # CTA never moves
        self.assertEqual(self._px(out, WELCOME.buttons_top + 5), CTA)
        # element sits between the moved disclaimer and the CTA
        mid = (d1 - disc_delta + WELCOME.buttons_top) // 2
        self.assertEqual(self._px(out, mid), (10, 10, 10, 255))

    def test_bottom_small_element_leaves_hero_alone(self):
        # 20px element needs 80; CTA gap 50 -> disclaimer up 30; mid gap absorbs it
        out = d.compose(self.src, WELCOME, element(h=20), "bottom")
        h0, h1 = WELCOME.hero
        self.assertEqual(self._px(out, h0 + 5), HERO)
        self.assertEqual(self._px(out, h1 - 5), HERO)

    def test_top_oversized_element_is_scaled_to_fit(self):
        z0, z1 = WELCOME.top_zone
        out = d.compose(self.src, WELCOME, element(h=(z1 - z0) * 2), "top")
        # nothing below the zone was touched
        self.assertEqual(self._px(out, WELCOME.hero[0] + 5), HERO)


class NormalizeScreen(unittest.TestCase):
    def test_row_screen_values_map_to_models(self):
        # The row stores accessibility identifiers; the models are keyed by bare name.
        self.assertEqual(d.normalize_screen("welcome_screen"), "welcome")
        self.assertEqual(d.normalize_screen("welcome"), "welcome")
        self.assertEqual(d.normalize_screen(" Welcome_Screen "), "welcome")
        self.assertEqual(d.normalize_screen("onboarding_quiz_screen"), "onboarding_quiz")

    def test_render_accepts_the_row_value(self):
        # Refused as unknown *screen*, never as a bad argparse choice — and the message
        # names the known models so the agent can stop rather than guess.
        with tempfile.TemporaryDirectory() as td:
            asis = os.path.join(td, "asis.png")
            synthetic_capture().convert("RGB").save(asis)
            with self.assertRaises(SystemExit) as cm:
                d.main(["render", "--idea", "IDEA-9", "--screen", "paywall_screen", "--asis", asis,
                        "--variant", "name=a;slot=top;element=laurel", "--out", td])
            self.assertIn("unknown screen", str(cm.exception))
            self.assertIn("welcome", str(cm.exception))


class ReviewElements(unittest.TestCase):
    def test_review_and_badge_review_need_text_and_source(self):
        for el in ("review", "badge_review"):
            with self.assertRaises(SystemExit):
                d.Variant.parse(f"name=e;slot=mid;element={el};source=App Store").render_element(
                    d.DEFAULT_APP_ROOT)
            with self.assertRaises(SystemExit) as cm:
                d.Variant.parse(f"name=e;slot=mid;element={el};text=Great app").render_element(
                    d.DEFAULT_APP_ROOT)
            self.assertIn("source=", str(cm.exception))

    def test_star_row_is_five_stars_wide(self):
        row = d._star_row(size=30, gap=6)
        self.assertEqual(row.size, (5 * 30 + 4 * 6, 30))
        # something gold was actually drawn
        self.assertTrue(any(px[3] > 0 for px in row.getdata()))

    @unittest.skipUnless(APP_ROOT_PRESENT, "app checkout with fonts not present")
    def test_wrap_respects_width_and_never_splits_words(self):
        fnt = d.font(d.DEFAULT_APP_ROOT, "body-medium", 13)
        text = "Honestly, it understands me better than ChatGPT and it is always there"
        lines = d._wrap(text, fnt, 600)
        self.assertGreater(len(lines), 1)
        self.assertEqual(" ".join(lines), text)
        for ln in lines:
            self.assertLessEqual(fnt.getlength(ln), 600)

    @unittest.skipUnless(APP_ROOT_PRESENT, "app checkout with fonts not present")
    def test_badge_review_stacks_badge_over_card(self):
        badge = d.element_laurel(d.DEFAULT_APP_ROOT)
        card = d.element_review(d.DEFAULT_APP_ROOT, "Honestly, it understands me better than ChatGPT.")
        both = d.element_badge_review(d.DEFAULT_APP_ROOT, "Honestly, it understands me better than ChatGPT.")
        self.assertEqual(both.height, badge.height + 10 * d.SCALE + card.height)
        self.assertEqual(both.width, max(badge.width, card.width))
        # The card is glass, not a panel: whiteAlpha400 (0.6) lets the screen's gradient
        # through. Pinned to the token exactly — an opaque fill here is the regression
        # that made the card out-contrast the Get Started CTA and read as a foreign
        # object among the app's translucent surfaces.
        self.assertEqual(card.getpixel((card.width // 2, card.height // 2))[3], int(0.6 * 255))


class UploadNames(unittest.TestCase):
    MANIFEST = {
        "files": {"current": "/x/idea1_current.png", "a": "/x/idea1_a.png",
                  "e": "/x/idea1_e.png", "compare": "/x/idea1_compare.png"},
        "variants": [
            {"name": "a", "slot": "top", "element": "laurel", "label": "a · Trust badge, above the orb"},
            {"name": "e", "slot": "mid", "element": "badge_review", "label": None},
        ],
    }

    def test_named_by_label_sheet_and_current_fixed(self):
        self.assertEqual(d._upload_name("/y/idea1_a.png", self.MANIFEST),
                         "a · Trust badge, above the orb.png")
        self.assertEqual(d._upload_name("/y/idea1_compare.png", self.MANIFEST), "Current vs Proposed.png")
        self.assertEqual(d._upload_name("/y/idea1_current.png", self.MANIFEST), "Current · control (b).png")

    def test_missing_label_falls_back_to_element_and_slot(self):
        self.assertEqual(d._upload_name("/y/idea1_e.png", self.MANIFEST), "e · badge_review, mid.png")

    def test_unknown_file_or_no_manifest_keeps_its_name(self):
        self.assertEqual(d._upload_name("/y/other.png", self.MANIFEST), "other.png")
        self.assertEqual(d._upload_name("/y/idea1_a.png", {}), "idea1_a.png")

    def test_load_manifest_finds_it_beside_the_files(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "idea1_design.json")
            json.dump(self.MANIFEST, open(p, "w"))
            open(os.path.join(td, "idea1_a.png"), "wb").close()
            self.assertEqual(d._load_manifest(None, [os.path.join(td, "idea1_a.png")])["files"]["a"],
                             "/x/idea1_a.png")
            self.assertEqual(d._load_manifest(None, [os.path.join(td, "nothing.png")]), self.MANIFEST)
        self.assertEqual(d._load_manifest(None, []), {})


class ComposeMany(unittest.TestCase):
    """Two placements in one variant: the bands move once for both, and the elements
    land where they should without overlapping each other or the CTA."""

    def setUp(self):
        self.src = synthetic_capture()

    def _px(self, im, y, x=100):
        return im.getpixel((x, y))

    def test_mid_and_bottom_together(self):
        mid_el = Image.new("RGBA", (400, 200), (10, 10, 10, 255))
        bot_el = Image.new("RGBA", (400, 120), (20, 20, 20, 255))
        out = d.compose_many(self.src, WELCOME, [(mid_el, "mid"), (bot_el, "bottom")])
        h0, h1 = WELCOME.hero
        d0, d1 = WELCOME.disclaimer
        # bottom first: disclaimer up by need-gap = (120+60) - 50 = 130; mid gap 140-30=110
        # absorbs 110, the hero moves 20 for it.
        disc_off = 130
        hero_off_bottom = 20
        # then mid: gap between moved hero bottom and moved disclaimer top is
        # (d0-130) - (h1-20) = 140-110 = 30; need 260 -> hero moves a further 230.
        hero_off = hero_off_bottom + 230
        self.assertEqual(self._px(out, h0 - hero_off + 150), HERO)          # hero at its final spot
        self.assertEqual(self._px(out, d0 - disc_off + 100), DISC)          # disclaimer at its spot
        self.assertEqual(self._px(out, WELCOME.buttons_top + 5), CTA)       # CTA never moves
        # the bottom element sits between disclaimer and CTA
        y_bot = (d1 - disc_off + WELCOME.buttons_top) // 2
        self.assertEqual(self._px(out, y_bot, x=600), (20, 20, 20, 255))
        # the mid element sits between the moved hero and the moved disclaimer
        y_mid = ((h1 - hero_off) + (d0 - disc_off)) // 2
        self.assertEqual(self._px(out, y_mid, x=600), (10, 10, 10, 255))
        # nothing of the old hero remains where the mid element's gap now is
        self.assertEqual(self._px(out, h1 - 5), BG)

    def test_hero_never_rises_into_the_status_bar(self):
        # A very tall mid element would ask the hero to move above the top zone. It stops
        # there, and the status bar (everything above the top zone) is left untouched —
        # no band headroom painted over the clock.
        src = synthetic_capture()
        px = src.load()
        for y in range(0, WELCOME.top_zone[0]):        # paint a fake status bar
            for x in range(src.width):
                px[x, y] = (1, 2, 3, 255)
        huge = Image.new("RGBA", (400, 900), (10, 10, 10, 255))
        out = d.compose_many(src, WELCOME, [(huge, "mid")])
        h0, _ = WELCOME.hero
        self.assertEqual(out.getpixel((600, 80)), (1, 2, 3, 255))                 # status bar intact
        self.assertEqual(out.getpixel((600, WELCOME.top_zone[0] - 1)), (1, 2, 3, 255))
        # hero starts at the ceiling (sampled past the 80px blend ramp over its glow rows)
        self.assertEqual(out.getpixel((100, WELCOME.top_zone[0] + 100)), HERO)
        _, h1 = WELCOME.hero
        self.assertEqual(out.getpixel((100, h1 - 5)), BG)                         # …and did move: its old bottom is exposed

    def test_clamped_hero_shrinks_the_element_instead_of_overlapping(self):
        # A 900px mid element cannot be given room once the hero hits its ceiling, so
        # it is scaled to the gap: the disclaimer is untouched and no element pixel
        # lands inside it.
        huge = Image.new("RGBA", (400, 900), (10, 10, 10, 255))
        out = d.compose_many(self.src, WELCOME, [(huge, "mid")])
        d0, d1 = WELCOME.disclaimer
        self.assertEqual(self._px(out, d0 + 5), DISC)
        self.assertEqual(self._px(out, d1 - 5), DISC)
        for y in range(d0, d1, 10):
            self.assertNotEqual(out.getpixel((600, y)), (10, 10, 10, 255), f"element drawn into disclaimer at {y}")
        # the element is present in the gap, and shorter than it was
        hero_top = WELCOME.top_zone[0]
        gap_top = WELCOME.hero[1] - (WELCOME.hero[0] - hero_top)
        col = [out.getpixel((600, y)) for y in range(gap_top, d0)]
        h = sum(1 for p in col if p == (10, 10, 10, 255))
        self.assertGreater(h, 0)
        self.assertLess(h, 900)

    def test_bottom_disclaimer_never_slides_through_the_hero(self):
        # A very tall bottom element: the disclaimer stops `pad` below the hero.
        tall = Image.new("RGBA", (400, 900), (20, 20, 20, 255))
        out = d.compose_many(self.src, WELCOME, [(tall, "bottom")])
        h0, h1 = WELCOME.hero
        d0, d1 = WELCOME.disclaimer
        hero_top = WELCOME.top_zone[0]
        hero_bottom = h1 - (h0 - hero_top)
        # everything from the hero's new bottom down to +pad is not disclaimer
        for y in range(hero_bottom, hero_bottom + WELCOME.pad, 5):
            self.assertNotEqual(out.getpixel((100, y)), DISC)
        # CTA anchored
        self.assertEqual(self._px(out, WELCOME.buttons_top + 5), CTA)

    def test_fit_height_floors_even_with_no_gap(self):
        el = Image.new("RGBA", (400, 900), (0, 0, 0, 255))
        self.assertIs(d._fit_height(el, 1000), el)                     # fits: untouched
        self.assertEqual(d._fit_height(el, 450).height, 450)           # half the gap → half size
        self.assertEqual(d._fit_height(el, 0).height, int(900 * 0.35))     # no gap → the floor
        self.assertEqual(d._fit_height(el, -50).height, int(900 * 0.35))   # negative gap → the floor
        self.assertEqual(d._fit_height(el, 10).height, int(900 * 0.35))    # below the floor → the floor

    def test_same_slot_twice_refuses(self):
        el = Image.new("RGBA", (10, 10), (0, 0, 0, 255))
        with self.assertRaises(SystemExit):
            d.compose_many(self.src, WELCOME, [(el, "mid"), (el, "mid")])

    def test_single_placement_matches_compose(self):
        el = Image.new("RGBA", (400, 200), (10, 10, 10, 255))
        a = d.compose(self.src, WELCOME, el, "mid")
        b = d.compose_many(self.src, WELCOME, [(el, "mid")])
        self.assertEqual(list(a.getdata())[::5000], list(b.getdata())[::5000])


class VariantPlus(unittest.TestCase):
    def test_plus_parses_into_extra_and_labels(self):
        v = d.Variant.parse("name=f;slot=mid;element=review;text=Q;source=S;plus=laurel@bottom")
        self.assertEqual(v.extra["plus"], "laurel@bottom")
        self.assertEqual(v.display_label(), "f · review, mid + laurel @ bottom")

    def test_plus_refusals(self):
        for plus in ("laurel", "line@bottom", "laurel@mid"):
            v = d.Variant.parse(f"name=f;slot=mid;element=laurel;plus={plus}")
            if not APP_ROOT_PRESENT and plus != "laurel":
                continue  # the main laurel needs fonts; the malformed ones fail before that
            with self.assertRaises(SystemExit):
                v.placements(d.DEFAULT_APP_ROOT)

    @unittest.skipUnless(APP_ROOT_PRESENT, "app checkout with fonts not present")
    def test_placements_returns_two(self):
        v = d.Variant.parse("name=f;slot=mid;element=review;text=Q;source=S;plus=laurel@bottom")
        p = v.placements(d.DEFAULT_APP_ROOT)
        self.assertEqual([slot for _, slot in p], ["mid", "bottom"])


class TopFeatherMask(unittest.TestCase):
    def test_ramps_top_only(self):
        m = d._top_feather_mask((10, 100), 20)
        self.assertEqual(m.getpixel((5, 0)), 0)
        self.assertLess(m.getpixel((5, 10)), 255)
        self.assertEqual(m.getpixel((5, 50)), 255)
        self.assertEqual(m.getpixel((5, 99)), 255)  # bottom edge is hard


class FindRow(unittest.TestCase):
    def _rows(self):
        return [
            {"id": "p1", "properties": {"Idea ID": {"unique_id": {"prefix": "IDEA", "number": 1}}}},
            {"id": "p12", "properties": {"Idea ID": {"unique_id": {"prefix": "IDEA", "number": 12}}}},
        ]

    def test_resolves_idea_ref_forms(self):
        notion = mock.Mock()
        notion.query_data_source.return_value = self._rows()
        self.assertEqual(d._find_row(notion, "ds", "IDEA-12")["id"], "p12")
        self.assertEqual(d._find_row(notion, "ds", "idea-1")["id"], "p1")
        self.assertEqual(d._find_row(notion, "ds", "12")["id"], "p12")

    def test_missing_returns_none(self):
        notion = mock.Mock()
        notion.query_data_source.return_value = self._rows()
        self.assertIsNone(d._find_row(notion, "ds", "IDEA-99"))

    def test_bad_ref_refuses(self):
        with self.assertRaises(SystemExit):
            d._find_row(mock.Mock(), "ds", "welcome")


@unittest.skipUnless(APP_ROOT_PRESENT, "app checkout with fonts not present")
class RenderEndToEnd(unittest.TestCase):
    def test_render_writes_files_and_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            asis = os.path.join(td, "asis.png")
            synthetic_capture().convert("RGB").save(asis)
            rc = d.main(["render", "--idea", "IDEA-7", "--screen", "welcome", "--asis", asis,
                         "--variant", "name=a;slot=top;element=laurel",
                         "--variant", "name=c;slot=bottom;element=line;text=Move from overwhelmed "
                                      "to calm in 1–2 minutes;source=App Store listing",
                         "--out", td])
            self.assertEqual(rc, 0)
            for f in ("idea7_current.png", "idea7_a.png", "idea7_c.png", "idea7_compare.png"):
                self.assertTrue(os.path.exists(os.path.join(td, f)), f)
            man = json.load(open(os.path.join(td, "idea7_design.json")))
            self.assertEqual([v["name"] for v in man["variants"]], ["a", "c"])
            self.assertEqual(man["variants"][0]["source"], d.LAUREL_SOURCE)
            self.assertEqual(man["variants"][1]["source"], "App Store listing")
            sheet = Image.open(os.path.join(td, "idea7_compare.png"))
            self.assertGreater(sheet.width, sheet.height)  # three panels side by side

    def test_laurel_badge_shape(self):
        badge = d.element_laurel(d.DEFAULT_APP_ROOT)
        # 28pt laurels at 3x = 84px tall; the badge is at least that tall and wider than tall
        self.assertGreaterEqual(badge.height, 84)
        self.assertGreater(badge.width, badge.height)


PAYWALL = d.SCREENS["package_paywall"]
PAYWALL_CAPTURE = os.path.join(d.DEFAULT_APP_ROOT, "AuraConsumerAppSimTests", "knowledge",
                               "screenshots", "package_paywall_screen_light.png")
SF_PRESENT = os.path.exists(d.SF_PRO_SYSTEM) or os.path.exists(d.SF_PRO_INSTALLED.format("Regular"))


class PaywallModel(unittest.TestCase):
    def test_the_row_identifier_resolves_to_the_model(self):
        self.assertEqual(d.normalize_screen("package_paywall_screen"), "package_paywall")

    def test_every_region_sits_inside_the_capture(self):
        for scr_name, names in (("package_paywall", PAYWALL.regions),
                                ("welcome", ("orb_badge", "chrome_trailing"))):
            scr = d.SCREENS[scr_name]
            for name in names:
                x0, y0, x1, y1 = scr.region(name)
                self.assertTrue(0 <= x0 < x1 <= scr.size[0] and 0 <= y0 < y1 <= scr.size[1],
                                f"{scr_name}:{name}")


class ReplaceGrowsOnAFixedScreen(unittest.TestCase):
    """A replacement taller than its region pushes what is below it down, as a VStack
    makes room for a headline that wraps — rather than centring over its neighbours."""

    def setUp(self):
        self.src = Image.new("RGBA", PAYWALL.size, BG)
        x0, y0, x1, y1 = PAYWALL.region("bullet1")
        self.src.paste(Image.new("RGBA", (x1 - x0, y1 - y0), CTA), (x0, y0))

    def test_a_taller_headline_slides_the_bullets_down(self):
        h0, h1 = PAYWALL.region("headline")[1::2]
        extra = 120
        el = Image.new("RGBA", (600, h1 - h0 + extra), (10, 10, 10, 255))
        out = d.compose_all(self.src, PAYWALL, [d.Placement(el, "replace:headline")])
        b0 = PAYWALL.region("bullet1")[1]
        self.assertEqual(out.getpixel((600, b0 + extra + 5)), CTA)   # moved down by the overflow
        self.assertEqual(out.getpixel((600, h1 + extra - 5)), (10, 10, 10, 255))  # headline fills it

    def test_a_region_further_down_is_read_where_it_moved_to(self):
        h0, h1 = PAYWALL.region("headline")[1::2]
        extra = 90
        head = Image.new("RGBA", (600, h1 - h0 + extra), (10, 10, 10, 255))
        bullet = Image.new("RGBA", (300, 40), (0, 0, 255, 255))
        out = d.compose_all(self.src, PAYWALL, [d.Placement(head, "replace:headline"),
                                                d.Placement(bullet, "replace:bullet1_text")])
        x0, y0, x1, y1 = PAYWALL.region("bullet1_text")
        self.assertEqual(out.getpixel(((x0 + x1) // 2, (y0 + y1) // 2 + extra)), (0, 0, 255, 255))

    def test_an_element_that_fits_moves_nothing(self):
        el = Image.new("RGBA", (600, 40), (10, 10, 10, 255))
        out = d.compose_all(self.src, PAYWALL, [d.Placement(el, "replace:headline")])
        b0 = PAYWALL.region("bullet1")[1]
        self.assertEqual(out.getpixel((600, b0 + 5)), CTA)


@unittest.skipUnless(APP_ROOT_PRESENT and SF_PRESENT and os.path.exists(PAYWALL_CAPTURE),
                     "needs the app checkout, SF Pro and the stored paywall capture")
class PaywallStylesMatchTheCapture(unittest.TestCase):
    """The calibrated styles reproduce the shipped paywall header to the pixel: the ink
    of each rendered line is as wide as the capture's, and bullets break where the app
    breaks them."""

    @staticmethod
    def _ink_width(im, dark: bool) -> int:
        g = im.convert("L") if dark else im.getchannel("A")
        bb = g.point((lambda v: 255 if v < 120 else 0) if dark else (lambda v: 255 if v > 135 else 0)).getbbox()
        return bb[2] - bb[0]

    def test_headline_and_bullets(self):
        cap = Image.open(PAYWALL_CAPTURE)
        cases = [
            ("headline", "A shift you can actually feel", PAYWALL.region("headline")),
            ("bullet", "Finds the root cause, not flattery", PAYWALL.region("bullet1_text")),
        ]
        for style, text, region in cases:
            got = self._ink_width(d.element_line(d.DEFAULT_APP_ROOT, text, style), dark=False)
            want = self._ink_width(cap.crop(region), dark=True)
            self.assertLessEqual(abs(got - want), 2, f"{style}: {got} vs capture {want}")

    def test_a_short_bullet_starts_at_the_column_edge_with_default_align(self):
        el = d.element_line(d.DEFAULT_APP_ROOT, "Short line", "bullet")
        src = Image.new("RGBA", PAYWALL.size, BG)
        out = d.compose_all(src, PAYWALL, [d.Placement(el, "replace:bullet1_text")])
        x0, y0, x1, y1 = PAYWALL.region("bullet1_text")
        ink = out.crop((x0, y0, x1, y1)).convert("L").point(lambda v: 255 if v < 120 else 0).getbbox()
        self.assertLess(ink[0], 12)   # starts at the column edge, not centred

    def test_bullets_break_where_the_app_breaks_them(self):
        fnt = d.font(d.DEFAULT_APP_ROOT, "body-regular", 15)
        _, _, _, _, wrap_w, _, _ = d.CALIBRATED_LINE_STYLES["bullet"]
        track = d.CALIBRATED_LINE_STYLES["bullet"][2] * d.SCALE
        # From the capture: package_paywall_screen_light.png, PaywallNoTrialHeader.
        expected = {
            "Remembers your story, so every conversation builds":
                ["Remembers your story, so every", "conversation builds"],
            "Informed by clinicians and coaches from Yale and UCLA":
                ["Informed by clinicians and coaches from", "Yale and UCLA"],
            "Grounded in CBT, ACT, and 20+ therapeutic approaches":
                ["Grounded in CBT, ACT, and 20+", "therapeutic approaches"],
        }
        for text, lines in expected.items():
            self.assertEqual(d._tracked_wrap(text, fnt, track, wrap_w), lines)


class HomeConversationProfileModels(unittest.TestCase):
    def test_row_identifiers_resolve(self):
        for row, key in (("home_screen", "home"), ("conversation_screen", "conversation"),
                         ("profile_screen", "profile")):
            self.assertEqual(d.normalize_screen(row), key)
            self.assertIn(key, d.SCREENS)

    def test_every_region_sits_inside_the_capture(self):
        for key in ("home", "conversation", "profile"):
            scr = d.SCREENS[key]
            for name, (x0, y0, x1, y1) in scr.regions.items():
                self.assertTrue(0 <= x0 < x1 <= scr.size[0] and 0 <= y0 < y1 <= scr.size[1],
                                f"{key}:{name}")
            if scr.fade:
                self.assertTrue(0 < scr.fade[0] < scr.fade[1] <= scr.size[1], f"{key}: fade")


def gradient_capture(size=(1206, 2622)) -> Image.Image:
    """A sky that changes every row — what a single stretched sample cannot reproduce."""
    im = Image.new("RGBA", size)
    px = im.load()
    for y in range(size[1]):
        v = 60 + (y * 150) // size[1]
        for x in range(size[0]):
            px[x, y] = (v, v, 255 - v // 2, 255)
    return im


class SkyFill(unittest.TestCase):
    def setUp(self):
        self.src = gradient_capture()
        self.scr = d.SCREENS["home"]

    def test_bg_column_keeps_the_gradient_row_by_row(self):
        strip = d._bg_fill(self.src, self.scr, 1000, 1400)
        for dy in (0, 200, 399):
            self.assertEqual(strip.getpixel((600, dy)), self.src.getpixel((8, 1000 + dy)))

    def test_an_inserted_strip_is_the_insertion_rows_colour(self):
        strip = d._bg_fill(self.src, self.scr, 1000, 1100, uniform=True)
        self.assertEqual(strip.getpixel((600, 0)), strip.getpixel((600, 99)))
        self.assertEqual(strip.getpixel((600, 50)), self.src.getpixel((8, 1000)))


class InsertSeams(unittest.TestCase):
    """Every insert on a gradient screen is one colour — the insertion row's — so its top
    and bottom both meet that row's sky. A full-width band and a narrow element alike."""

    def test_full_width_and_narrow_after_inserts(self):
        scr = d.SCREENS["home"]
        src = gradient_capture()
        y = scr.region("greeting")[3]
        for w in (scr.size[0], 300):          # full-width band, narrow element
            el = Image.new("RGBA", (w, 120), (0, 0, 0, 0))
            out = d.compose_all(src, scr, [d.Placement(el, "after:greeting")])
            top, bottom = out.getpixel((4, y)), out.getpixel((4, y + 119))
            self.assertEqual(top, bottom, f"width {w}")
            self.assertEqual(top, src.getpixel((8, y)), f"width {w}")


class Placement_(unittest.TestCase):
    def test_valign_bottom_and_top(self):
        el = Image.new("RGBA", (100, 40))
        r = (0, 1000, 600, 1200)
        self.assertEqual(d._place_in(r, el, "left", "bottom")[1], 1160)
        self.assertEqual(d._place_in(r, el, "left", "top")[1], 1000)
        with self.assertRaises(SystemExit):
            d._place_in(r, el, "left", "middle")


class TextBlockFace(unittest.TestCase):
    """`style=display` on a text_block must reach the display face — it never did, because
    the check looked for `style` in `extra`, which the parser strips it from."""

    def _render(self, spec):
        v = d.Variant.parse(spec)
        ctx = mock.Mock()
        ctx.src = Image.new("RGBA", (1206, 2622), BG)
        ctx.scr = d.SCREENS["conversation"]
        with mock.patch.object(d, "element_text_block", return_value=Image.new("RGBA", (1, 1))) as tb:
            v.render_element("/app", ctx)
        return tb.call_args.kwargs

    def test_display_is_reachable(self):
        kw = self._render("name=a;slot=replace:reply;element=text_block;style=display;text=x;source=s")
        self.assertEqual(kw["style"], "display")

    def test_default_is_the_body_face(self):
        kw = self._render("name=a;slot=replace:reply;element=text_block;text=x;source=s")
        self.assertEqual(kw["style"], "body")

    def test_width_and_align_pass_through(self):
        kw = self._render("name=a;slot=replace:reply;element=text_block;align=left;width=290;text=x;source=s")
        self.assertEqual((kw["align"], kw["max_w"]), ("left", 290 * d.SCALE))

    def test_line_still_defaults_to_display(self):
        v = d.Variant.parse("name=a;slot=top;element=line;text=x;source=s")
        with mock.patch.object(d, "element_line", return_value=Image.new("RGBA", (1, 1))) as el:
            v.render_element("/app", None)
        self.assertEqual(el.call_args.args[2], "display")


class FrostedFade(unittest.TestCase):
    """On the Profile an insert slides content under the frosted fade instead of clipping
    it flat: the fade rows change, and everything below the fade is the capture's own."""

    def test_insert_under_the_fade(self):
        scr = d.SCREENS["profile"]
        src = Image.new("RGBA", scr.size, (240, 240, 242, 255))
        src.paste(Image.new("RGBA", (1098, 400), (230, 120, 160, 255)), (54, 1900))
        canvas = src.copy()
        strip = Image.new("RGBA", (scr.size[0], 300), (240, 240, 242, 255))
        d._insert_strip(canvas, src, scr, 1016, strip)
        f0, f1 = scr.fade
        self.assertEqual(canvas.crop((0, f1, *scr.size)).tobytes(), src.crop((0, f1, *scr.size)).tobytes())
        # the pink card was pushed 300px down into the fade, where it is dissolved, not cut
        self.assertNotEqual(canvas.getpixel((600, f0 + 40)), src.getpixel((600, f0 + 40)))


class NotionFileName(unittest.TestCase):
    def test_long_caption_is_capped_with_extension(self):
        long = "Flow a · " + "after the tap on the speaker disc — plays once " * 4 + ".png"
        out = d._fit_name(long)
        self.assertLessEqual(len(out), d.NOTION_FILE_NAME_MAX)
        self.assertTrue(out.endswith("….png"))

    def test_short_name_untouched(self):
        self.assertEqual(d._fit_name("Current vs Proposed.png"), "Current vs Proposed.png")


@unittest.skipUnless(os.path.exists(d.SF_PRO_SYSTEM) or os.path.exists(d.SF_PRO_INSTALLED.format("Regular")),
                     "the chip label is SF Pro")
class Chip(unittest.TestCase):
    def test_capsule_hugs_its_label_and_keeps_the_disc_height(self):
        src = Image.new("RGBA", (1206, 2622), (180, 200, 230, 255))
        region = d.SCREENS["home"].region("journal_disc")
        short = d.element_chip(src, region, d.DEFAULT_APP_ROOT, "Hi", trailing="xmark")
        long = d.element_chip(src, region, d.DEFAULT_APP_ROOT, "Last time: {theme}. Pick it up?", trailing="xmark")
        self.assertEqual(short.height, region[3] - region[1])
        self.assertGreater(long.width, short.width)
        self.assertEqual(long.getpixel((0, 0))[3], 0)   # rounded: the corner is transparent

FOOTER_COLOR = (255, 0, 255, 255)
CONTENT_COLOR = (0, 120, 0, 255)


def synthetic_settings() -> Image.Image:
    scr = d.SCREENS["settings"]
    im = Image.new("RGBA", scr.size, BG)
    g = scr.regions["group4"]
    im.paste(Image.new("RGBA", (g[2] - g[0], g[3] - g[1]), CONTENT_COLOR), (g[0], g[1]))
    f0, f1 = scr.footer
    im.paste(Image.new("RGBA", (scr.size[0], f1 - f0), FOOTER_COLOR), (0, f0))
    return im


class SecondNavDisc(unittest.TestCase):
    """PROD-7026: a second disc beside the Profile gear."""

    def test_gear_leading_is_one_disc_left_of_the_gear(self):
        r = d.SCREENS["profile"].regions
        self.assertEqual(r["gear_leading"], (888, 210, 1008, 330))
        self.assertEqual(r["gear_leading"][1:4:2], r["gear"][1:4:2])
        self.assertEqual(r["gear"][0] - r["gear_leading"][2], 24)  # 8pt at 3x

    def test_dx_offsets_an_overlay_in_points(self):
        scr = d.SCREENS["profile"]
        src = Image.new("RGBA", scr.size, BG)
        disc = Image.new("RGBA", (120, 120), (1, 2, 3, 255))
        out = d.compose_all(src, scr, [d.Placement(disc, "at:gear", {"dx": "-48"})])
        self.assertEqual(out.getpixel((900, 270)), (1, 2, 3, 255))
        self.assertEqual(out.getpixel((1090, 270)), BG)
        # no offset: it sits on the gear itself
        out = d.compose_all(src, scr, [d.Placement(disc, "at:gear")])
        self.assertEqual(out.getpixel((1090, 270)), (1, 2, 3, 255))

    def test_dx_offsets_a_replace_too(self):
        scr = d.SCREENS["profile"]
        src = Image.new("RGBA", scr.size, BG)
        disc = Image.new("RGBA", (120, 120), (1, 2, 3, 255))
        out = d.compose_all(src, scr, [d.Placement(disc, "replace:gear", {"dx": "-48"})])
        self.assertEqual(out.getpixel((900, 270)), (1, 2, 3, 255))
        self.assertEqual(out.getpixel((1090, 270)), BG)

    def test_a_bad_offset_is_refused(self):
        scr = d.SCREENS["profile"]
        with self.assertRaises(SystemExit):
            d.compose_all(Image.new("RGBA", scr.size, BG), scr,
                          [d.Placement(Image.new("RGBA", (10, 10), BG), "at:gear", {"dx": "left"})])


class PinnedSettingsFooter(unittest.TestCase):
    """PROD-7026: `SidebarView` pins Log out and the help link under a flexible Spacer, so
    an insert uses the Spacer up before it pushes the footer."""

    def setUp(self):
        self.scr = d.SCREENS["settings"]
        self.src = synthetic_settings()
        self.f0 = self.scr.footer[0]

    def _insert(self, h):
        return d.compose_all(self.src, self.scr, [d.Placement(element(400, h), "after:group4")])

    def test_the_spacer_is_the_gap_above_the_footer(self):
        self.assertEqual(d._footer_spacer(self.scr), self.f0 - self.scr.regions["group4"][3])

    def test_an_insert_that_fits_the_spacer_leaves_the_footer_where_it_is(self):
        out = self._insert(200)
        self.assertEqual(out.getpixel((10, self.f0 + 5)), FOOTER_COLOR)
        self.assertEqual(out.getpixel((10, self.f0 - 5)), BG)
        self.assertEqual(out.getpixel((600, self.scr.regions["group4"][3] + 100))[:3], (10, 10, 10))

    def test_an_insert_past_the_spacer_pushes_the_footer_by_the_excess(self):
        spacer = d._footer_spacer(self.scr)
        out = self._insert(spacer + 150)
        self.assertEqual(out.getpixel((10, self.f0 + 5)), BG)
        self.assertEqual(out.getpixel((10, self.f0 + 150 + 5)), FOOTER_COLOR)

    def test_the_regions_below_follow_the_footer_not_the_strip(self):
        # An overlay on `logout` lands on the footer's new position.
        spacer = d._footer_spacer(self.scr)
        mark = Image.new("RGBA", (50, 50), (9, 9, 9, 255))
        out = d.compose_all(self.src, self.scr, [
            d.Placement(element(400, spacer + 150), "after:group4"),
            d.Placement(mark, "at:logout")])
        lx, ly = self.scr.regions["logout"][0], self.scr.regions["logout"][1]
        cx = lx + (self.scr.regions["logout"][2] - lx) // 2
        self.assertEqual(out.getpixel((cx, ly + 150 + 27)), (9, 9, 9, 255))

    def test_a_screen_without_a_footer_still_pushes_everything(self):
        scr = d.SCREENS["profile"]
        self.assertIsNone(scr.footer)


@unittest.skipUnless(os.path.exists(d.SF_PRO_SYSTEM) or os.path.exists(d.SF_PRO_INSTALLED.format("Regular")),
                     "sheet labels are SF Pro")
class MultiScreenCompare(unittest.TestCase):
    GREEN = (0x2C, 0x6F, 0x55)

    def _label_has_green(self, sheet_img, col, n):
        pw = 560
        x0 = 48 + col * (pw + 64)
        return any(sheet_img.getpixel((x, y))[:3] == self.GREEN
                   for y in range(36, 36 + 70) for x in range(x0, x0 + pw))

    def test_each_panel_is_coloured_by_its_own_flag(self):
        img = Image.new("RGBA", (300, 600), BG)
        sh = d.sheet(d.DEFAULT_APP_ROOT, [("Profile", img, True), ("Settings", img, True),
                                          ("a · Profile", img, False), ("a · Settings", img, False)])
        self.assertFalse(self._label_has_green(sh, 0, 4))
        self.assertFalse(self._label_has_green(sh, 1, 4))   # the second control panel
        self.assertTrue(self._label_has_green(sh, 2, 4))
        self.assertTrue(self._label_has_green(sh, 3, 4))

    def test_two_tuples_keep_panel_zero_as_the_control(self):
        img = Image.new("RGBA", (300, 600), BG)
        sh = d.sheet(d.DEFAULT_APP_ROOT, [("Current", img), ("a", img)])
        self.assertFalse(self._label_has_green(sh, 0, 2))
        self.assertTrue(self._label_has_green(sh, 1, 2))

    def test_compare_writes_the_sheet_share_ideas_keys_on(self):
        import argparse
        with tempfile.TemporaryDirectory() as td:
            paths = {}
            for k in ("cp", "cs", "ap", "as"):
                paths[k] = os.path.join(td, f"{k}.png")
                Image.new("RGB", (300, 600), (240, 240, 240)).save(paths[k])
            args = argparse.Namespace(
                idea="IDEA-41", app_root=d.DEFAULT_APP_ROOT, panel_width=300, out=td,
                panel=[f"label=Current · Profile;image={paths['cp']};control=1",
                       f"label=Current · Settings;image={paths['cs']};control=1",
                       f"label=a · Profile;image={paths['ap']};arm=a",
                       f"label=a · Settings;image={paths['as']}"])
            self.assertEqual(d.cmd_compare(args), 0)
            manifest = json.load(open(os.path.join(td, "idea41_design.json")))
            self.assertEqual(manifest["files"]["compare"], os.path.join(td, "idea41_compare.png"))
            self.assertEqual(d._upload_name(manifest["files"]["compare"], manifest),
                             "Current vs Proposed.png")
            self.assertEqual(d._upload_name(paths["ap"], manifest), "a · Profile.png")

    def test_compare_needs_a_control_panel_and_refuses_arm_b(self):
        import argparse
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "x.png")
            Image.new("RGB", (30, 60)).save(p)
            def run(panels):
                return d.cmd_compare(argparse.Namespace(
                    idea="IDEA-1", app_root=d.DEFAULT_APP_ROOT, panel_width=100, out=td, panel=panels))
            with self.assertRaises(SystemExit):
                run([f"label=a;image={p}", f"label=c;image={p}"])
            with self.assertRaises(SystemExit):
                run([f"label=x;image={p};control=1", f"label=y;image={p};arm=b"])


if __name__ == "__main__":
    unittest.main()
