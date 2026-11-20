#!/usr/bin/env python3
"""design_idea.py — the screen models, region ops and cloned chrome of the design leg.

**Why these tests exist.** The renderer composites a proposed change onto a real capture,
and its screen models are numbers measured off a hierarchy dump. A region that drifts
outside the capture, an insert that pushes the home indicator off the screen, or an
element that silently draws a claim with no source are all failures a reviewer sees as
a wrong picture, not as a stack trace. The geometry is checked on synthetic images so
the suite needs no capture, no app checkout and no font — only the SF Symbol exports
that ship with the skill, for the one test that pins glyph sizing to a measured value.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import sys
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "plugins" / "product" / "skills" / "design-idea" / "scripts" / "design_idea.py"

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None


def _load():
    spec = importlib.util.spec_from_file_location("design_idea", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    # dataclasses resolve `from __future__` annotations through sys.modules, so the
    # module must be registered before its body runs.
    sys.modules["design_idea"] = mod
    spec.loader.exec_module(mod)
    return mod


@unittest.skipUnless(Image is not None, "Pillow is not installed")
class ScreenModels(unittest.TestCase):
    def setUp(self):
        self.di = _load()

    def test_every_region_lies_inside_its_capture(self):
        for name, scr in self.di.SCREENS.items():
            W, H = scr.size
            for rname, (x0, y0, x1, y1) in scr.regions.items():
                self.assertTrue(0 <= x0 < x1 <= W and 0 <= y0 < y1 <= H, f"{name}.{rname}")
            for cname, (region, cut, cy) in scr.clones.items():
                x0, y0, x1, y1 = region
                self.assertTrue(0 <= x0 < x1 <= W and 0 <= y0 < y1 <= H, f"{name}.{cname}")
                self.assertTrue(0 < cut <= y1 - y0, f"{name}.{cname}: cut past the band")
                self.assertTrue(0 < cy < cut, f"{name}.{cname}: row centre outside the cut")
            self.assertTrue(0 <= scr.bg_sample[0] < scr.bg_sample[1] <= H, f"{name}.bg_sample")

    def test_background_sample_overlaps_nothing(self):
        # The sample band is stretched over every repaint. A band that catches a line
        # of text paints that line wherever it fills — the welcome disclaimer once came
        # back as ghost copies of its first line that way.
        for name, scr in self.di.SCREENS.items():
            s0, s1 = scr.bg_sample
            for rname, (_, y0, _, y1) in scr.regions.items():
                self.assertTrue(s1 <= y0 or s0 >= y1, f"{name}.bg_sample overlaps {rname}")
            for cname, ((_, y0, _, y1), _, _) in scr.clones.items():
                self.assertTrue(s1 <= y0 or s0 >= y1, f"{name}.bg_sample overlaps clone {cname}")
            if scr.disclaimer:
                self.assertTrue(s1 <= scr.disclaimer[0], f"{name}.bg_sample overlaps the disclaimer")

    def test_stack_screens_carry_their_bands_and_fixed_ones_do_not_need_them(self):
        for name, scr in self.di.SCREENS.items():
            if scr.layout == "stack":
                self.assertIsNotNone(scr.hero, name)
                self.assertIsNotNone(scr.buttons_top, name)
                self.assertIsNotNone(scr.top_zone, name)
            self.assertIn(scr.layout, ("stack", "fixed", "list"), name)

    def test_region_lookup_accepts_clones_and_refuses_unknown(self):
        scr = self.di.SCREENS["settings"]
        self.assertEqual(scr.region("row"), scr.clones["row"][0])
        with self.assertRaises(SystemExit):
            scr.region("nope")

    def test_normalize_screen_strips_the_identifier_suffix(self):
        n = self.di.normalize_screen
        self.assertEqual(n("welcome_screen"), "welcome")
        self.assertEqual(n("onboarding_valueProps_screen"), "onboarding_valueprops")
        self.assertEqual(n("settings"), "settings")

    def test_store_suffix_splits_key_and_sky(self):
        f = self.di._store_suffix
        self.assertEqual(f("/x/settings_screen_light.png", "settings"), ("settings_screen", "_light"))
        self.assertEqual(f("/x/welcome.png", "welcome"), ("welcome", ""))
        self.assertEqual(f("/x/onboarding_valueprops_dark.png", "onboarding_valueprops"),
                         ("onboarding_valueprops", "_dark"))


@unittest.skipUnless(Image is not None, "Pillow is not installed")
class Variants(unittest.TestCase):
    def setUp(self):
        self.di = _load()

    def test_b_is_control_and_refused(self):
        with self.assertRaises(SystemExit):
            self.di.Variant.parse("name=b;slot=top;element=laurel")

    def test_text_elements_refuse_a_missing_source(self):
        for el in ("line", "pill", "settings_row", "text_block"):
            v = self.di.Variant.parse(f"name=a;slot=at:x;element={el};text=Hello")
            with self.assertRaises(SystemExit, msg=el):
                v.render_element("/nowhere", None)

    def test_erase_renders_nothing(self):
        v = self.di.Variant.parse("name=c;slot=replace:disclaimer;element=erase")
        self.assertIsNone(v.render_element("/nowhere", None))

    def test_plus_accepts_laurel_or_erase_only(self):
        v = self.di.Variant.parse("name=c;slot=at:footer;element=erase;plus=review@mid")
        with self.assertRaises(SystemExit):
            v.placements("/nowhere", None)

    def test_help_names_the_region_ops(self):
        h = self.di.build_parser().format_help()
        for sub in self.di.build_parser()._subparsers._group_actions[0].choices.values():
            h += sub.format_help()
        for op in ("replace:<region>", "at:<region>", "after:<region>", "row:<group>", "--donor"):
            self.assertIn(op, h)


@unittest.skipUnless(Image is not None, "Pillow is not installed")
class Compositing(unittest.TestCase):
    """Geometry on synthetic captures: flat background, black markers."""

    def setUp(self):
        self.di = _load()
        self.BG = (200, 200, 200, 255)

    def _capture(self, size=(100, 300)):
        return Image.new("RGBA", size, self.BG)

    def _screen(self, **kw):
        base = dict(size=(100, 300), bg_sample=(10, 20), layout="fixed")
        base.update(kw)
        return self.di.Screen(**base)

    def test_after_inserts_a_strip_and_shifts_what_is_below_but_not_the_home_zone(self):
        src = self._capture()
        src.paste((0, 0, 0, 255), (0, 150, 100, 152))      # content below the region
        src.paste((255, 0, 0, 255), (0, 285, 100, 287))    # the home indicator
        scr = self._screen(layout="list", regions={"g": (0, 100, 100, 140)}, home_zone=30)
        el = Image.new("RGBA", (100, 20), (0, 0, 255, 255))
        out = self.di.compose_all(src, scr, [self.di.Placement(el, "after:g")])
        self.assertEqual(out.getpixel((50, 145))[:3], (0, 0, 255))     # strip at the region's foot
        self.assertEqual(out.getpixel((50, 170))[:3], (0, 0, 0))       # content moved down by 20
        self.assertEqual(out.getpixel((50, 150))[:3], (0, 0, 255))     # nothing of it left behind
        self.assertEqual(out.getpixel((50, 285))[:3], (255, 0, 0))     # home zone untouched

    def test_row_grows_a_group_before_its_bottom_padding(self):
        src = self._capture()
        src.paste((0, 0, 0, 255), (0, 190, 100, 192))
        scr = self._screen(layout="list", regions={"g": (10, 100, 90, 160)}, home_zone=30)
        el = Image.new("RGBA", (80, 30), (0, 255, 0, 255))
        out = self.di.compose_all(src, scr, [self.di.Placement(el, "row:g")])
        y = 160 - 16 * self.di.SCALE                                   # 16pt above the card's foot
        self.assertEqual(out.getpixel((50, y + 5))[:3], (0, 255, 0))
        self.assertEqual(out.getpixel((50, 220))[:3], (0, 0, 0))       # content below moved by 30

    def test_row_refuses_a_non_list_screen(self):
        scr = self._screen(regions={"g": (10, 100, 90, 160)})
        el = Image.new("RGBA", (80, 30), (0, 255, 0, 255))
        with self.assertRaises(SystemExit):
            self.di.compose_all(self._capture(), scr, [self.di.Placement(el, "row:g")])

    def test_replace_repaints_the_region_from_the_background_sample(self):
        src = self._capture()
        src.paste((0, 0, 0, 255), (20, 100, 80, 140))
        scr = self._screen(regions={"r": (20, 100, 80, 140)})
        out = self.di.compose_all(src, scr, [self.di.Placement(None, "replace:r")])
        self.assertEqual(out.getpixel((50, 120))[:3], self.BG[:3])
        el = Image.new("RGBA", (20, 10), (0, 0, 255, 255))
        out = self.di.compose_all(src, scr, [self.di.Placement(el, "replace:r", {"align": "right"})])
        self.assertEqual(out.getpixel((79, 120))[:3], (0, 0, 255))
        self.assertEqual(out.getpixel((30, 120))[:3], self.BG[:3])

    def test_at_overlays_without_repainting(self):
        src = self._capture()
        src.paste((0, 0, 0, 255), (20, 100, 80, 140))
        scr = self._screen(regions={"r": (20, 100, 80, 140)})
        el = Image.new("RGBA", (10, 10), (0, 0, 255, 255))
        out = self.di.compose_all(src, scr, [self.di.Placement(el, "at:r")])
        self.assertEqual(out.getpixel((50, 120))[:3], (0, 0, 255))
        self.assertEqual(out.getpixel((25, 120))[:3], (0, 0, 0))       # the rest of the region kept

    def test_pill_mask_leaves_the_frame_margin_transparent(self):
        # A donor whose frame is a flat colour: the cloned pill must be transparent at
        # the frame's edge and opaque inside the inset — the sliver of donor sky the
        # frame carries must not travel with the pill.
        donor = Image.new("RGBA", (400, 400), (90, 90, 90, 255))
        scr = self._screen(size=(400, 400), bg_sample=(10, 20), regions={"cta": (20, 100, 380, 156)})
        try:
            pill = self.di.element_pill(donor, scr.region("cta"), "/nowhere", "Go")
        except SystemExit:
            self.skipTest("SF Pro not available for the label")
        i = self.di.PILL_FRAME_INSET
        self.assertEqual(pill.getpixel((0, pill.height // 2))[3], 0)
        # The rim is anti-aliased, so read a few px inside it.
        self.assertGreaterEqual(pill.getpixel((i + 4, pill.height // 2))[3], 250)

    def test_oversized_element_is_refused_by_name(self):
        scr = self._screen(regions={"r": (40, 100, 60, 140)})
        el = Image.new("RGBA", (200, 10), (0, 0, 255, 255))         # wider than the capture
        for slot in ("replace:r", "at:r"):
            with self.assertRaises(SystemExit, msg=slot) as cm:
                self.di.compose_all(self._capture(), scr, [self.di.Placement(el, slot)])
            self.assertIn("width=", str(cm.exception))

    def test_erase_on_a_stack_slot_is_refused(self):
        scr = self._screen(layout="stack", hero=(30, 100), disclaimer=(120, 150), buttons_top=200,
                           top_zone=(5, 30), regions={"disclaimer": (0, 120, 100, 150)})
        with self.assertRaises(SystemExit) as cm:
            self.di.compose_all(self._capture(), scr, [self.di.Placement(None, "top")])
        self.assertIn("replace:", str(cm.exception))

    def test_region_op_follows_the_band_a_stack_slot_moved(self):
        # A `bottom` element needs 80px above a CTA gap of 50, so the disclaimer slides
        # up 30 (the mid gap absorbs it). An erase of `disclaimer` in the same arm must
        # repaint the band where it is now (90–120), not where the model has it.
        src = self._capture()
        src.paste((0, 0, 0, 255), (0, 120, 100, 150))                  # the disclaimer
        scr = self._screen(layout="stack", hero=(30, 100), disclaimer=(120, 150), buttons_top=200,
                           top_zone=(5, 30), regions={"disclaimer": (0, 120, 100, 150)})
        el = Image.new("RGBA", (40, 20), (0, 0, 255, 255))
        out = self.di.compose_all(src, scr, [self.di.Placement(el, "bottom"),
                                             self.di.Placement(None, "replace:disclaimer")])
        self.assertEqual(out.getpixel((50, 105))[:3], self.BG[:3])     # moved band erased
        self.assertEqual(out.getpixel((50, 125))[:3], self.BG[:3])     # nothing left at the old y

    def test_stack_slots_need_a_stack_screen_and_bad_slots_are_refused(self):
        scr = self._screen(regions={"r": (0, 0, 10, 10)})
        el = Image.new("RGBA", (10, 10), (0, 0, 255, 255))
        for slot in ("top", "beside:r", "r"):
            with self.assertRaises(SystemExit, msg=slot):
                self.di.compose_all(self._capture(), scr, [self.di.Placement(el, slot)])

    def test_inpaint_vertical_erases_text_rows_from_a_horizontal_gradient(self):
        im = Image.new("RGBA", (50, 40))
        px = im.load()
        for x in range(50):
            for y in range(40):
                px[x, y] = (x * 5, 100, 100, 255)
        im.paste((0, 0, 0, 255), (0, 15, 50, 25))
        self.di._inpaint_vertical(im, 0, 50, 15, 25)
        for x in (3, 25, 47):
            self.assertEqual(im.getpixel((x, 20)), (x * 5, 100, 100, 255))

    def test_auto_foreground_follows_the_ground(self):
        dark = Image.new("RGBA", (10, 10), (30, 30, 30, 255))
        light = Image.new("RGBA", (10, 10), (230, 230, 230, 255))
        self.assertEqual(self.di._auto_fg(dark, (0, 0, 10, 10)), (255, 255, 255))
        self.assertEqual(self.di._auto_fg(light, (0, 0, 10, 10)), self.di.TINT)


@unittest.skipUnless(Image is not None, "Pillow is not installed")
class Symbols(unittest.TestCase):
    def setUp(self):
        self.di = _load()

    def test_exports_ship_for_every_glyph_the_elements_draw(self):
        for name in ("square.and.arrow.up", "arrow.up.right", "chevron.right", "chevron.left", "checkmark"):
            for w in ("regular", "medium", "semibold"):
                self.assertTrue(os.path.exists(os.path.join(self.di.SYMBOLS, f"{name}-{w}.png")), f"{name}-{w}")

    def test_point_sizing_matches_the_capture(self):
        # CircularIconButton draws chevron.left at iconSize 16; the hierarchy dump measures
        # that glyph 15.7pt tall — 47px at 3x. The 256pt export must reproduce it.
        g = self.di.sf_symbol("chevron.left", "regular", points=16)
        self.assertAlmostEqual(g.height, round(15.7 * self.di.SCALE), delta=4)
        self.assertEqual(self.di.sf_symbol("checkmark", "medium", height=30).height, 30)

    def test_unknown_symbol_is_refused_with_the_export_command(self):
        with self.assertRaises(SystemExit) as cm:
            self.di.sf_symbol("no.such.symbol", "regular", points=16)
        self.assertIn("symbols --name", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
