"""`product:sync-design-system` records a token parser gap and keeps building, unless --strict.

On 2026-11-09 one new `[Gradient.Stop]` token (PROD-8423) stopped the weekly Aurie build at the
completeness gate. The gap must stay loud (manifest, counts, a FOLLOWUP line) without stopping
every other card. The theme below carries that token's real declaration from AuraConsumerApp,
which the extractor now reads, plus a declaration form no parser reads (`futureMesh`) to force
the gap.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "plugins/product/skills/sync-design-system/scripts"
THEME = "AuraConsumerApp/Services/Theme/Global"

COLORS = '''import SwiftUI
extension Color {
    static let blackPrimary = Color(hex: "#1A1A1A")
    static let aurieAccentGradientStops: [Gradient.Stop] = [
        .init(color: Color(hex: "#CA44BC"), location: 0.00),
        .init(color: Color(hex: "#FF38B9"), location: 0.14),
        .init(color: Color(hex: "#FFB046"), location: 1.00)
    ]
    static let futureMesh = MeshGradient(
        width: 2
    )
}
'''


def run(*extra):
    repo = tempfile.mkdtemp()
    theme = pathlib.Path(repo, THEME)
    theme.mkdir(parents=True)
    (theme / "Colors.swift").write_text(COLORS, encoding="utf-8")
    for f in ("Fonts.swift", "Spacing.swift", "BorderRadius.swift", "Gradients.swift", "Shadows.swift"):
        (theme / f).write_text("import SwiftUI\n", encoding="utf-8")
    out = tempfile.mkdtemp()
    res = subprocess.run([sys.executable, str(SCRIPTS / "extract_tokens.py"), "--app", "aurie",
                          "--repo", repo, "--out", out, *extra], capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    manifest = pathlib.Path(out, "tokens.json")
    return res, (json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else None)


class ParserGap(unittest.TestCase):
    def test_a_gap_is_recorded_and_the_extraction_succeeds(self):
        res, m = run()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertFalse(m["complete"])
        self.assertEqual(m["counts"]["parser_gaps"], 1)
        gap = next(u for u in m["unparsed"] if u.get("gap"))
        self.assertEqual((gap["file"], gap["name"], gap["line"]), ("Colors.swift", "futureMesh", 9))
        self.assertIn("FOLLOWUP token Colors.swift:futureMesh:", res.stdout)
        self.assertIn("blackPrimary", {t["name"] for t in m["tokens"]})  # the rest still builds

    def test_strict_stops_and_writes_no_manifest(self):
        res, m = run("--strict")
        self.assertEqual(res.returncode, 1)
        self.assertIsNone(m)
        self.assertIn("futureMesh", res.stderr)

    def test_no_gap_means_complete_and_no_followup(self):
        res, m = run()
        self.assertNotIn("FOLLOWUP", res.stdout.replace("FOLLOWUP token Colors.swift:futureMesh", ""))
        global COLORS
        saved, COLORS = COLORS, COLORS.split("    static let futureMesh")[0] + "}\n"
        try:
            res, m = run()
        finally:
            COLORS = saved
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(m["complete"])
        self.assertEqual(m["counts"]["parser_gaps"], 0)
        self.assertNotIn("FOLLOWUP", res.stdout)



class GradientStops(unittest.TestCase):
    """`[Gradient.Stop]` written as `.init(color:location:)`: a gradient token with its stop locations."""

    def test_the_real_aurie_token_is_a_gradient_with_its_locations(self):
        res, m = run()
        self.assertEqual(res.returncode, 0, res.stderr)
        tok = next(t for t in m["tokens"] if t["name"] == "aurieAccentGradientStops")
        self.assertEqual(tok["kind"], "gradient")
        self.assertEqual(tok["stops"], ["#CA44BC", "#FF38B9", "#FFB046"])
        self.assertEqual(tok["locations"], [0.0, 0.14, 1.0])
        self.assertEqual(tok["css"], "linear-gradient(90deg, #CA44BC 0%, #FF38B9 14%, #FFB046 100%)")
        self.assertEqual(tok["source"], "Colors.swift:4")
        self.assertEqual(m["counts"]["gradient"], 1)
        self.assertNotIn("aurieAccentGradientStops", {u["name"] for u in m["unparsed"]})

    def test_it_is_written_as_a_gradient_variable_not_a_color(self):
        res, _ = run()
        out = res.args[res.args.index("--out") + 1]
        css = pathlib.Path(out, "tokens.css").read_text(encoding="utf-8")
        self.assertIn("--gradient-aurie-accent", css)
        self.assertNotIn("--color-aurie-accent", css)

    def test_a_stop_with_an_unknown_color_is_unparsed_not_dropped(self):
        global COLORS
        saved = COLORS
        COLORS = COLORS.replace('.init(color: Color(hex: "#FF38B9"), location: 0.14)',
                                '.init(color: Colors.brandPink, location: 0.14)')
        try:
            res, m = run()
        finally:
            COLORS = saved
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("aurieAccentGradientStops", {u["name"] for u in m["unparsed"]})
        self.assertNotIn("aurieAccentGradientStops", {t["name"] for t in m["tokens"]})


if __name__ == "__main__":
    unittest.main()
