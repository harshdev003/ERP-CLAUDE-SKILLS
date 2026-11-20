"""`product:sync-design-system` reads a React Native theme (Aura 4.0) into the shared token manifest.

The fixtures under `fixtures/design_system_rn/src/styles` are Aura's real `redesign`,
`redesignDark` and `redesignLight` folders, copied from its default branch, so the extractor is
held to every declaration form the app actually writes. The gap tests copy them and add one new
form, which must be listed, never dropped.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "plugins/product/skills/sync-design-system/scripts"
FIX = pathlib.Path(__file__).resolve().parent / "fixtures/design_system_rn"
sys.path.insert(0, str(SCRIPTS))

import extract_rn  # noqa: E402

SKIP = ("redesign/styleSheet.js", "redesignDark/index.js", "redesignLight/index.js")


def extract(root=FIX):
    groups, gaps = extract_rn.extract(str(pathlib.Path(root) / "src/styles"), skip=SKIP)
    return groups, gaps, {t["name"]: t for kind in groups for t in groups[kind]}


def copy_fixture():
    root = pathlib.Path(tempfile.mkdtemp()) / "repo"
    shutil.copytree(FIX, root)
    return root


def run_cli(root, *extra):
    out = tempfile.mkdtemp()
    res = subprocess.run([sys.executable, str(SCRIPTS / "extract_tokens.py"), "--app", "aura",
                          "--repo", str(root), "--out", out, *extra],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    m = pathlib.Path(out, "tokens.json")
    return res, (json.loads(m.read_text(encoding="utf-8")) if m.exists() else None), out


class RealTheme(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.groups, cls.gaps, cls.by = extract()

    def test_every_declaration_is_read(self):
        self.assertEqual(self.gaps, [])
        for kind in ("color", "role", "spacing", "radius", "gradient", "shadow", "fontStyle"):
            self.assertTrue(self.groups[kind], kind)

    def test_a_role_carries_its_dark_and_light_values(self):
        t = self.by["textPrimary"]
        self.assertEqual((t["kind"], t["dark"], t["darkRef"]), ("role", "#FFFFFF", "whiteMain"))
        self.assertEqual(t["css"], t["dark"])  # dark is the app's default theme
        self.assertNotEqual(t["light"], t["dark"])

    def test_palette_literals_are_canonical_css(self):
        self.assertEqual(self.by["white50"]["css"], "rgba(255, 255, 255, 0.5)")
        self.assertEqual(self.by["blackMain"]["css"], "#000000")

    def test_gradient_forms(self):
        self.assertTrue(self.by["gOnboardingDark"]["css"].startswith("linear-gradient(180deg, "))
        self.assertEqual(self.by["gSettingsHeroPremium"]["css"],
                         "linear-gradient(144.5deg, #332938 0%, #59404D 50%, #8C6B61 100%)")
        self.assertTrue(self.by["radial.gLiveCardGlow"]["css"].startswith("radial-gradient(circle, "))
        self.assertTrue(self.by["topicGlowLightSleep"]["radial"])

    def test_fonts_name_their_faces(self):
        t = self.by["titleSerifItalic30"]
        self.assertEqual((t["family"], t["italic"], t["size"], t["lineHeight"]), ("Charter", True, 30, 36))
        self.assertEqual(t["css"], "italic 400 30px/36px Charter, Georgia, serif")
        self.assertEqual({f["value"] for f in self.groups["fontFamily"]} - set(extract_rn.FACES), set())

    def test_shadow_css_doubles_the_rn_radius(self):
        self.assertEqual(self.by["shadowOfferCard"]["css"], "0px 6px 18px rgba(0, 0, 0, 0.15)")


class Gaps(unittest.TestCase):
    def test_a_new_value_form_is_listed_not_dropped(self):
        root = copy_fixture()
        f = root / "src/styles/redesign/borderRadius.js"
        f.write_text(f.read_text(encoding="utf-8").replace(
            "export default {", "export default {\n  borderRadiusPill: Platform.select({ ios: 99 }),"), encoding="utf-8")
        _, gaps, by = extract(root)
        self.assertEqual([g["name"] for g in gaps], ["borderRadiusPill"])
        self.assertNotIn("borderRadiusPill", by)

    def test_a_role_on_one_theme_only_is_a_gap(self):
        root = copy_fixture()
        f = root / "src/styles/redesignDark/color.js"
        f.write_text(f.read_text(encoding="utf-8").replace(
            "export default {", "export default {\n  textBrandNew: Color.whiteMain,"), encoding="utf-8")
        _, gaps, _ = extract(root)
        self.assertEqual([(g["name"], g["literal"]) for g in gaps], [("textBrandNew", "role defined for dark only")])

    def test_a_new_theme_file_no_reader_opens_is_a_gap(self):
        root = copy_fixture()
        (root / "src/styles/redesign/motion.js").write_text("export default { fast: 120 }\n", encoding="utf-8")
        _, gaps, _ = extract(root)
        self.assertEqual([g["file"] for g in gaps], ["redesign/motion.js"])


class Cli(unittest.TestCase):
    def test_strict_run_is_complete_and_writes_both_themes(self):
        res, m, out = run_cli(FIX, "--strict")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(m["complete"])
        css = pathlib.Path(out, "tokens.css").read_text(encoding="utf-8")
        self.assertIn("--role-text-primary: #FFFFFF;", css)
        self.assertIn('[data-theme="light"] {', css)
        self.assertIn("--space-16:", css)
        self.assertNotIn("--space-d-", css)

    def test_strict_stops_on_a_gap_with_a_followup_line(self):
        root = copy_fixture()
        (root / "src/styles/redesign/motion.js").write_text("export default { fast: 120 }\n", encoding="utf-8")
        res, m, _ = run_cli(root, "--strict")
        self.assertEqual(res.returncode, 1)
        self.assertIsNone(m)
        self.assertIn("FOLLOWUP stop extract-tokens:", res.stdout)


if __name__ == "__main__":
    unittest.main()
