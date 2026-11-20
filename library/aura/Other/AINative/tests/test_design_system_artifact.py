"""`product:sync-design-system` turns an app's token manifest into a Design System artifact's files.

Runs the real chain on Aura's real theme files (`fixtures/design_system_rn`, copied from its default
branch): extract_tokens.py --strict, then to_design_system.py build and index. Holds the output to the
artifact type's grammar: every family a list of {name, value, usage}, names the page accepts and never
duplicated, the default theme first, aliases that resolve, and a usage note on every token.
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "plugins/product/skills/sync-design-system/scripts"
FIX = pathlib.Path(__file__).resolve().parent / "fixtures/design_system_rn"
sys.path.insert(0, str(SCRIPTS))

import to_design_system as tds  # noqa: E402
from apps import APPS  # noqa: E402

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def run(*args):
    return subprocess.run([sys.executable, *map(str, args)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def fake_repo():
    """The fixture theme, plus the font files and two captures the converter copies."""
    repo = pathlib.Path(tempfile.mkdtemp()) / "repo"
    shutil.copytree(FIX, repo)
    fonts = repo / APPS["aura"]["fontsDir"]
    fonts.mkdir(parents=True)
    for _, rel, _, _ in APPS["aura"]["fonts"]:
        (fonts / rel).write_bytes(b"\x00\x01\x00\x00")
    shots = repo / APPS["aura"]["screenshots"]
    shots.mkdir(parents=True)
    for name in ("home_screen_v4_dark.png", "home_screen_v4_light.png", "home_screen.png"):
        (shots / name).write_bytes(b"\x89PNG\r\n\x1a\n")
    return repo


class Build(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo, cls.out = fake_repo(), pathlib.Path(tempfile.mkdtemp())
        res = run(SCRIPTS / "extract_tokens.py", "--app", "aura", "--repo", cls.repo, "--out", cls.out / "m", "--strict")
        assert res.returncode == 0, res.stderr
        res = run(SCRIPTS / "to_design_system.py", "build", "--app", "aura", "--repo", cls.repo,
                  "--tokens", cls.out / "m/tokens.json", "--out", cls.out)
        assert res.returncode == 0, res.stderr
        cls.proj = cls.out / "project"
        cls.tokens = json.loads((cls.proj / "tokens.json").read_text(encoding="utf-8"))

    def families(self):
        return {k: v for k, v in self.tokens.items() if isinstance(v, dict) and "tokens" in v}

    def test_every_family_is_a_list_the_page_reads(self):
        seen = set()
        for fam, body in self.families().items():
            self.assertIsInstance(body["tokens"], list, fam)
            for t in body["tokens"]:
                self.assertRegex(t["name"], NAME, fam)
                self.assertNotIn(t["name"], seen, f"{t['name']} twice")
                seen.add(t["name"])
                self.assertTrue(t.get("usage"), f"{fam}.{t['name']} has no usage note")
                self.assertLessEqual(len(t["usage"]), 1000)
            if fam != "color":
                self.assertLessEqual(len(body["tokens"]), 60, fam)

    def test_dark_is_the_first_theme_and_roles_carry_both(self):
        self.assertEqual([t["id"] for t in self.tokens["color"]["themes"]], ["dark", "light"])
        role = next(t for t in self.tokens["color"]["tokens"] if t["name"] == "textPrimary")
        self.assertEqual(role["value"], {"dark": "{palette-whiteMain}", "light": "{palette-blackMain}"})

    def test_every_alias_names_a_colour_that_exists(self):
        names = {t["name"] for t in self.tokens["color"]["tokens"]}
        for t in self.tokens["color"]["tokens"]:
            vals = t["value"].values() if isinstance(t["value"], dict) else [t["value"]]
            for v in vals:
                m = re.fullmatch(r"\{(.+)\}", v)
                if m:
                    self.assertIn(m.group(1), names, t["name"])

    def test_text_roles_report_their_contrast_on_canvas(self):
        by = {t["name"]: t for t in self.tokens["color"]["tokens"]}
        self.assertIn("Contrast on canvas: dark 18.6:1, light 19.8:1.", by["textPrimary"]["usage"])
        self.assertIn("below 4.5:1", by["textPlaceholder"]["usage"])
        self.assertNotIn("Contrast", by["textOnInverse"]["usage"])  # sits on an inverse fill

    def test_usage_comes_from_the_source_comment(self):
        by = {t["name"]: t for t in self.tokens["color"]["tokens"]}
        self.assertEqual(by["textEmphasis"]["usage"].split(". Contrast")[0], "featured-card date + attendee count")
        self.assertNotEqual(by["textPrimary"]["usage"].split(".")[0], "Text")  # a section heading is not a note

    def test_type_names_the_bundled_fonts(self):
        files = {f["file"] for f in self.tokens["type"]["fonts"]}
        self.assertEqual(files, {f"fonts/{rel}" for _, rel, _, _ in APPS["aura"]["fonts"]})
        for f in files:
            self.assertTrue((self.proj / f).exists(), f)
        self.assertEqual(sum(len(g["styles"]) for g in self.tokens["type"]["groups"]), 39)

    def test_tokens_css_is_the_first_compile(self):
        css = (self.proj / "tokens.css").read_text(encoding="utf-8")
        self.assertTrue(css.startswith("/* Aura — generated from tokens.json */"))
        self.assertIn(':root, [data-theme="dark"] {', css)
        self.assertIn('[data-theme="light"] {', css)
        self.assertIn("--textPrimary: var(--palette-whiteMain);", css)
        self.assertIn('format("truetype")', css)

    def test_only_current_captures_are_listed_and_the_rest_is_written(self):
        self.assertEqual(json.loads((self.out / "screens.json").read_text()),
                         ["home_screen_v4_dark.png", "home_screen_v4_light.png"])
        for rel in ("README.md", "assets/Screens/README.md", "components/Cover/preview.html"):
            self.assertTrue((self.proj / rel).exists(), rel)
        self.assertTrue((self.proj / "components/Cover/preview.html").read_text().startswith("<!-- @dsCard height="))


class Index(unittest.TestCase):
    def uploads(self):
        p = pathlib.Path(tempfile.mkdtemp()) / "u.json"
        p.write_text(json.dumps({"home_screen_v4_dark.png": {"blob": "a" * 32, "size": 10}}))
        return p

    def test_a_new_index_has_the_marker_and_the_screens_group(self):
        out = pathlib.Path(tempfile.mkdtemp())
        res = run(SCRIPTS / "to_design_system.py", "index", "--app", "aura", "--repo", fake_repo(),
                  "--out", out, "--uploads", self.uploads())
        self.assertEqual(res.returncode, 0, res.stderr)
        idx = json.loads((out / "project/design-system.json").read_text())
        self.assertEqual((idx["v"], idx["layout"], idx["title"]), (3, "files", "Aura"))
        self.assertIn("createdOnFiles", idx)
        self.assertEqual(idx["assetGroups"]["Screens"]["files"]["home_screen_v4_dark.png"]["blob"], "a" * 32)
        self.assertEqual(idx["groups"], ["Screens"])

    def test_an_existing_index_keeps_its_keys(self):
        out = pathlib.Path(tempfile.mkdtemp())
        ex = out / "old.json"
        ex.write_text(json.dumps({"v": 3, "layout": "files", "createdOnFiles": {"v": 1, "at": "2026-01-01T00:00:00Z"},
                                  "title": "Aura (team)", "groups": ["Logos"],
                                  "assetGroups": {"Logos": {"name": "Logos", "order": [], "files": {}}}}))
        res = run(SCRIPTS / "to_design_system.py", "index", "--app", "aura", "--repo", fake_repo(),
                  "--out", out, "--uploads", self.uploads(), "--existing", ex)
        self.assertEqual(res.returncode, 0, res.stderr)
        idx = json.loads((out / "project/design-system.json").read_text())
        self.assertEqual((idx["title"], idx["createdOnFiles"]["at"]), ("Aura (team)", "2026-01-01T00:00:00Z"))
        self.assertEqual(idx["groups"], ["Logos", "Screens"])
        self.assertIn("Logos", idx["assetGroups"])

    def test_an_index_without_a_marker_is_never_written_over(self):
        out = pathlib.Path(tempfile.mkdtemp())
        ex = out / "old.json"
        ex.write_text(json.dumps({"title": "not an index"}))
        res = run(SCRIPTS / "to_design_system.py", "index", "--app", "aura", "--repo", fake_repo(),
                  "--out", out, "--uploads", self.uploads(), "--existing", ex)
        self.assertNotEqual(res.returncode, 0)
        self.assertFalse((out / "project/design-system.json").exists())


class Refusals(unittest.TestCase):
    def test_a_duplicate_or_bad_name_or_missing_usage_is_refused(self):
        bad = {"color": {"tokens": [{"name": "ink", "value": "#000", "usage": "x"}]},
               "spacing": {"tokens": [{"name": "ink", "value": "4px", "usage": "x"},
                                      {"name": "bad name", "value": "4px", "usage": "x"},
                                      {"name": "space-8", "value": "8px", "usage": ""}]}}
        problems = tds.check(bad)
        self.assertEqual(len(problems), 3, problems)

    def test_a_value_the_page_cannot_read_or_a_family_over_its_cap_is_refused_not_dropped(self):
        long = {"gradient": {"tokens": [{"name": "g-x", "value": "linear-gradient(" + "1, " * 120 + ")", "usage": "x"}]},
                "glow": {"tokens": [{"name": f"g-{i}", "value": "#fff", "usage": "x"} for i in range(61)]}}
        problems = tds.check(long)
        self.assertTrue(any("cannot read" in p for p in problems), problems)
        self.assertTrue(any("at most 60" in p for p in problems), problems)

    def test_a_manifest_with_gaps_is_not_converted(self):
        out = pathlib.Path(tempfile.mkdtemp())
        m = out / "tokens.json"
        m.write_text(json.dumps({"complete": False, "tokens": []}))
        res = run(SCRIPTS / "to_design_system.py", "build", "--app", "aura", "--repo", fake_repo(),
                  "--tokens", m, "--out", out)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("parser gaps", res.stderr)



class SwiftApp(unittest.TestCase):
    """Aurie: a Swift manifest, one light theme, notes from `//` comments, headings skipped."""

    def setUp(self):
        self.repo = pathlib.Path(tempfile.mkdtemp())
        theme = self.repo / APPS["aurie"]["themeDir"]
        theme.mkdir(parents=True)
        (theme / "Colors.swift").write_text(
            "extension Color {\n"
            "    // MARK: - New Design Black Colors\n"
            '    static let blackPrimary = Color(hex: "#222222")\n'
            "    // Aurie Care\n"
            "    // The faint violet Figma lays over the glass cards.\n"
            '    static let aurieCareGlassTint = Color(hex: "#7B5CD6")\n'
            "}\n", encoding="utf-8")
        self.manifest = {"complete": True, "tokens": [
            {"name": "blackPrimary", "kind": "color", "css": "#222222", "alpha": 1.0, "source": "Colors.swift:3"},
            {"name": "aurieCareGlassTint", "kind": "color", "css": "#7B5CD6", "alpha": 1.0, "source": "Colors.swift:6"},
            {"name": "gBlue45", "kind": "gradient", "css": "linear-gradient(45deg, #4CCAFF 0%, #1DF5ED 100%)", "source": "x"},
            {"name": "swatchApricot", "kind": "colorArray", "value": ["#FFD3A8", "#C46B5A"], "source": "x"},
            {"name": "borderRadius8", "kind": "radius", "css": "8px", "source": "x"},
            {"name": "bodyRegular", "kind": "fontStyle", "family": "SF Pro", "style": "body", "size": 17.0,
             "cssWeight": 400, "source": "x"},
            {"name": "displayLightItalic22", "kind": "fontStyle", "family": "Display", "size": 22.0,
             "cssWeight": 300, "italic": True, "source": "x"}]}
        self.cfg = {**APPS["aurie"], "key": "aurie", "repo": str(self.repo)}
        self.t = tds.build_swift(self.manifest, str(self.repo), self.cfg)

    def test_one_light_theme_and_notes_from_comments(self):
        self.assertEqual([th["id"] for th in self.t["color"]["themes"]], ["light"])
        by = {x["name"]: x["usage"] for x in self.t["color"]["tokens"]}
        self.assertEqual(by["aurieCareGlassTint"], "The faint violet Figma lays over the glass cards.")
        self.assertEqual(by["blackPrimary"], "Palette value.")  # a MARK heading is not a note

    def test_names_split_before_digits_and_families_are_lists(self):
        self.assertEqual(self.t["gradient"]["tokens"][0]["name"], "g-blue-45")
        self.assertEqual(self.t["radius"]["tokens"][0]["name"], "radius-8")
        self.assertEqual(self.t["stops"]["tokens"][0]["value"], "linear-gradient(90deg, #FFD3A8, #C46B5A)")
        self.assertEqual(tds.check(self.t), [])

    def test_type_keeps_sf_pro_unembedded_and_otto_bundled(self):
        groups = {g["family"]: g for g in self.t["type"]["groups"]}
        self.assertEqual([s["name"] for s in groups["ui"]["styles"]], ["bodyRegular"])
        self.assertEqual(groups["display"]["styles"][0]["fontStyle"], "italic")
        self.assertTrue(all(f["family"] == "ABC Otto" for f in self.t["type"]["fonts"]))
        self.assertIn("SF Pro", self.t["type"]["families"]["ui"])

if __name__ == "__main__":
    unittest.main()
