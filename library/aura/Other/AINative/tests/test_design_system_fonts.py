"""`product:sync-design-system` embeds the app's font files, and a missing one stops the build.

On 2026-11-18 the Aurie bundle shipped with no fonts: the app had renamed its font files and the
builder only warned, so every display card fell back to Georgia (ENG-1986). The fonts listed for
Aurie must exist in the app, and a listed file the repo lacks must be a stop with a FOLLOWUP line.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import pathlib
import sys
import tempfile
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "plugins/product/skills/sync-design-system/scripts"
sys.path.insert(0, str(SCRIPTS))


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bb, apps, stops = load("build_bundle"), load("apps"), load("stops")


def repo_with(files):
    repo = pathlib.Path(tempfile.mkdtemp())
    fonts = repo / "Fonts"
    fonts.mkdir()
    for f in files:
        (fonts / f).write_bytes(b"OTTO")
    return str(repo)


CFG = {"fontsDir": "Fonts", "fonts": [("Display", "ABCOtto-Light.otf", 300, "normal"),
                                      ("Display", "ABCOtto-Regular.otf", 400, "normal")]}


class EmbedFonts(unittest.TestCase):
    def test_every_listed_font_is_copied(self):
        out = tempfile.mkdtemp()
        faces = bb.embed_fonts(CFG, repo_with(["ABCOtto-Light.otf", "ABCOtto-Regular.otf"]), out)
        self.assertEqual([f[1] for f in faces], ["ABCOtto-Light.otf", "ABCOtto-Regular.otf"])
        self.assertTrue((pathlib.Path(out) / "fonts/ABCOtto-Regular.otf").exists())

    def test_a_missing_font_stops_with_a_followup_line(self):
        repo = repo_with(["ABCOtto-Light.otf"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as cm:
            stops.run(lambda: bb.embed_fonts(CFG, repo, tempfile.mkdtemp()), "build-bundle")
        self.assertIn("ABCOtto-Regular.otf", str(cm.exception.code))
        self.assertIn("FOLLOWUP stop build-bundle: font files in apps.py not found in Fonts: ABCOtto-Regular.otf",
                      out.getvalue())

    def test_the_aurie_config_names_licensed_files_not_trial_ones(self):
        names = [f[1] for f in apps.APPS["aurie"]["fonts"]]
        self.assertTrue(names)
        self.assertFalse([n for n in names if "Trial" in n], names)


if __name__ == "__main__":
    unittest.main()
