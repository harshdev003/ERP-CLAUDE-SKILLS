#!/usr/bin/env python3
"""Turn an app's extracted token manifest into a Claude Design System artifact's files.

Standalone Claude Design closes by 2026-12-14; a design system now lives in Claude as an
artifact made from the "Design System" type, its content in files under `project/`. This
writes those files from what `extract_tokens.py` read out of the app, so the artifact is
regenerated from code exactly as the standalone bundle was:

    python3 to_design_system.py --app aura --tokens DIR/tokens.json --repo CHECKOUT --out OUT

writes OUT/project/tokens.json, README.md, fonts/<file>, components/Cover/preview.html, and
OUT/screens.json: the capture-store PNGs to upload as the "Screens" asset group. Then:

    python3 to_design_system.py index --app aura --uploads uploads.json --out OUT [--existing index.json]

writes OUT/project/design-system.json, the index, naming each uploaded screen. The index of an
artifact that already has one keeps every key it had (its marker, title, other asset groups);
only `lastChange` and the Screens group are rewritten.

The tokens follow the type's grammar (`artifact-type/reference/format.md`): every family a
LIST of {name, value, usage}; colours per theme, the app's default theme first; a usage note on
every token, from the source line's own comment where the app wrote one.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from apps import resolve  # noqa: E402

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
FAMILY_CAP = 60          # tokens per non-colour family
OTHER_VALUE = re.compile(r"^[A-Za-z0-9 #%(),./+_-]{1,200}$")


# ------------------------------------------------------------------ notes ----

def source_note(repo, theme_dir, source):
    """The comment the app wrote for a token: on its line after `//`, else the comment
    lines directly above it. `source` is `file:line` relative to the theme folder."""
    m = re.fullmatch(r"(.+):(\d+)", source or "")
    if not m:
        return ""
    path = os.path.join(repo, theme_dir, m.group(1))
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except OSError:
        return ""
    i = int(m.group(2)) - 1
    if not 0 <= i < len(lines):
        return ""
    tail = re.search(r"//+\s*(.+)$", lines[i])
    if tail and "'" not in tail.group(0) and '"' not in tail.group(0):
        return clean(tail.group(1))
    above = []
    j = i - 1
    while j >= 0 and re.match(r"^\s*//", lines[j]):
        above.insert(0, re.sub(r"^\s*//+\s?", "", lines[j]))
        j -= 1
    # A short first line without a full stop heads the block (`// Aurie Care`); the note
    # is what follows it.
    while len(above) > 1 and len(above[0].split()) <= 6 and not above[0].rstrip().endswith((".", ":")):
        above.pop(0)
    text = clean(" ".join(above))
    # A short line without a full stop is a section heading (`// Text`, `// New Design Black
    # Colors`, `// MARK: …`), not a note about the token under it.
    if text.startswith("MARK") or (len(above) == 1 and len(text.split()) <= 5 and not text.endswith(".")):
        return ""
    return text


def clean(s):
    s = re.sub(r"\s+", " ", s).strip()
    s = s.replace("⚠️", "Note:").strip()
    return s[:600]


# --------------------------------------------------------------- contrast ----

def rgba(css):
    m = re.fullmatch(r"#([0-9A-Fa-f]{6})", css)
    if m:
        h = m.group(1)
        return tuple(int(h[k:k + 2], 16) for k in (0, 2, 4)) + (1.0,)
    nums = [float(x) for x in re.findall(r"[0-9.]+", css)]
    return (nums[0], nums[1], nums[2], nums[3] if len(nums) > 3 else 1.0)


def over(fg, bg):
    a = fg[3]
    return tuple(fg[k] * a + bg[k] * (1 - a) for k in range(3)) + (1.0,)


def lum(c):
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(c[0]) + 0.7152 * ch(c[1]) + 0.0722 * ch(c[2])


def contrast(fg_css, bg_css):
    bg = rgba(bg_css)
    fg = over(rgba(fg_css), bg)
    a, b = sorted((lum(fg), lum(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


# ----------------------------------------------------------------- tokens ----

def kebab(name):
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Za-z])(?=[0-9])", "-", name).lower()


def build_rn(manifest, repo, cfg):
    """Aura: palette + dark/light roles + scales + gradients + type."""
    theme = cfg["themeDir"]
    toks = manifest["tokens"]
    by = {}
    for t in toks:
        by.setdefault(t["kind"], []).append(t)
    note = lambda t: source_note(repo, theme, t.get("source"))
    out = {"name": cfg["displayName"], "version": 1,
           "meta": {"source": "code", "app": cfg["key"], "themeDir": theme,
                    "generator": "product:sync-design-system"}}

    roles = by.get("role", [])
    canvas = {t["name"]: t for t in roles}.get("canvas")
    colors = []
    for t in roles:
        usage = note(t) or role_fallback(t["name"])
        # Measured only for text that sits on the page: the On* roles sit on a coloured or
        # inverse fill and textGlow on imagery, so the canvas is the wrong ground for them.
        if (t["name"].startswith("text") and canvas and "On" not in t["name"]
                and t["name"] != "textGlow"):
            cd, cl = contrast(t["dark"], canvas["dark"]), contrast(t["light"], canvas["light"])
            flag = " — below 4.5:1 for body text" if min(cd, cl) < 4.5 else ""
            usage = usage.rstrip(".") + f". Contrast on canvas: dark {cd:.1f}:1, light {cl:.1f}:1{flag}."
        colors.append({"name": t["name"],
                       "value": {"dark": f"{{palette-{t['darkRef']}}}" if t.get("darkRef") in pal_names(by) else t["dark"],
                                 "light": f"{{palette-{t['lightRef']}}}" if t.get("lightRef") in pal_names(by) else t["light"]},
                       "usage": usage})
    for t in by.get("color", []):
        colors.append({"name": f"palette-{t['name']}", "value": t["css"],
                       "usage": note(t) or "A literal in the palette. Screens use the role that resolves to it, never this value directly."})
    out["color"] = {"themes": [{"id": "dark", "name": "Dark"}, {"id": "light", "name": "Light"}],
                    "note": "Dark is the default theme: onboarding, the tab bar and the players always render dark. "
                            "Roles (textPrimary, canvas, surfaceGlass…) resolve per theme; palette-* are the literals behind them.",
                    "tokens": colors}

    out["spacing"] = {"note": "Authored at a 402pt-wide frame; on device each step grows by 30% of the screen's extra width (scaledDimension).",
                      "tokens": [{"name": f"space-{t['name'][1:]}", "value": t["css"],
                                  "usage": note(t) or f"{t['css']} step."} for t in by.get("spacing", [])]}
    out["radius"] = {"tokens": [{"name": re.sub(r"^borderRadius", "radius-", t["name"]), "value": t["css"],
                                 "usage": note(t) or f"{t['css']} corner."} for t in by.get("radius", [])]}
    out["shadow"] = {"note": "React Native's shadowRadius is half the design's blur, so the CSS blur is twice it. Android draws elevation instead.",
                     "tokens": [{"name": kebab(t["name"]), "value": t["css"],
                                 "usage": note(t) or "Drop shadow."} for t in by.get("shadow", [])]}
    out["layout"] = {"note": "Measured layout constants for the tab bar and mini player, in the 402pt frame.",
                     "tokens": [{"name": kebab(t["name"]), "value": t["css"],
                                 "usage": note(t) or "Layout constant."} for t in by.get("layout", [])]}

    grads = by.get("gradient", [])
    linear = [g for g in grads if not g.get("radial") and not g["name"].startswith("topicGlow")]
    radial = [g for g in grads if g.get("radial") and not g["name"].startswith("topicGlow")]
    glows_light = [g for g in grads if g["name"].startswith("topicGlowLight")]
    glows_dark = by.get("colorArray", [])
    fam = lambda rows, fn: [{"name": fn(g), "value": g["css"], "usage": note(g) or "Gradient."} for g in rows]
    out["gradient"] = {"note": "Angles are CSS degrees (0 points up, 90 right).",
                       "tokens": fam(linear, lambda g: kebab(g["name"]))}
    out["glow"] = {"note": "Radial glows drawn from the centre out.",
                   "tokens": fam(radial, lambda g: kebab(g["name"].replace("radial.", "")))}
    out["topicGlowDark"] = {"note": "Each topic's glow on the dark theme: its core and edge colour, drawn as a radial wash.",
                            "tokens": [{"name": kebab(g["name"]).replace("topic-glow-", "topic-dark-"),
                                        "value": f"radial-gradient(circle, {g['value'][0]} 0%, {g['value'][1]} 100%)",
                                        "usage": note(g) or "Topic glow, dark theme."} for g in glows_dark]}
    out["topicGlowLight"] = {"note": "Each topic's glow on the light theme, with its stop opacities.",
                             "tokens": fam(glows_light, lambda g: kebab(g["name"]).replace("topic-glow-light-", "topic-light-"))}

    styles = by.get("fontStyle", [])
    families = {}
    for t in styles:
        families.setdefault(t["family"], []).append(t)
    keys = {"Charter": "serif", "Nunito Sans": "sans"}
    stacks = {"serif": "Charter, Georgia, serif", "sans": "\"Nunito Sans\", -apple-system, system-ui, sans-serif"}
    groups = []
    for fam_name, rows in families.items():
        k = keys.get(fam_name, kebab(fam_name).replace(" ", "-"))
        st = []
        for t in rows:
            s = {"name": t["name"], "fontSize": f"{t['size']:g}px", "fontWeight": t["cssWeight"],
                 "usage": note(t) or f"{fam_name} {t['cssWeight']}, {t['size']:g}px."}
            if t.get("lineHeight"):
                s["lineHeight"] = f"{t['lineHeight']:g}px"
            if t.get("letterSpacing") is not None:
                s["letterSpacing"] = f"{t['letterSpacing']:g}px"
            if t.get("italic"):
                s["fontStyle"] = "italic"
            st.append(s)
        groups.append({"name": "Serif — Charter" if k == "serif" else "Sans — Nunito Sans", "family": k, "styles": st})
    out["type"] = {"fonts": [{"family": fam, "file": f"fonts/{os.path.basename(rel)}", "weight": str(w), "style": s}
                             for fam, rel, w, s in cfg["fonts"]],
                   "families": {k: stacks[k] for k in ("serif", "sans")},
                   "groups": groups}
    return out


def build_swift(manifest, repo, cfg):
    """Aurie: one light theme (its time-of-day skies resolve in code, not as themes here),
    the palette, scales, gradients and shadows, the SF Pro UI scale and the ABC Otto display scale."""
    theme = cfg["themeDir"]
    by = {}
    for t in manifest["tokens"]:
        by.setdefault(t["kind"], []).append(t)
    note = lambda t: source_note(repo, theme, t.get("source"))
    out = {"name": cfg["displayName"], "version": 1,
           "meta": {"source": "code", "app": cfg["key"], "themeDir": theme,
                    "generator": "product:sync-design-system"}}
    out["color"] = {"themes": [{"id": "light", "name": "Light"}],
                    "note": "Aurie is locked to light mode and reads the time-of-day sky instead; on a sky "
                            "surface name the sky role (skyState.textPrimary…), which carries its own contrast "
                            "per sky. These are the values the roles resolve to.",
                    "tokens": [{"name": t["name"], "value": t["css"],
                                "usage": note(t) or ("Translucent palette value." if t.get("alpha", 1) < 0.999
                                                     else "Palette value.")} for t in by.get("color", [])]}
    out["spacing"] = {"tokens": [{"name": kebab(t["name"]).replace("space", "space-", 1).replace("--", "-"),
                                  "value": t["css"], "usage": note(t) or f"{t['css']} step."}
                                 for t in by.get("spacing", [])]}
    out["radius"] = {"tokens": [{"name": re.sub(r"^borderRadius", "radius-", t["name"]).lower(),
                                 "value": t["css"], "usage": note(t) or f"{t['css']} corner."}
                                for t in by.get("radius", [])]}
    out["shadow"] = {"note": "SwiftUI's shadow radius is a Gaussian sigma; the CSS blur is about twice it.",
                     "tokens": [{"name": kebab(t["name"]), "value": t["css"], "usage": note(t) or "Drop shadow."}
                                for t in by.get("shadow", [])]}
    out["gradient"] = {"note": "SwiftUI angles mapped to CSS degrees.",
                       "tokens": [{"name": kebab(t["name"]), "value": t["css"], "usage": note(t) or "Gradient."}
                                  for t in by.get("gradient", [])]}
    out["stops"] = {"note": "Colour sets declared as [Color] arrays: fade pairs and the sky swatches, read as gradient stops.",
                    "tokens": [{"name": kebab(t["name"]), "value": f"linear-gradient(90deg, {', '.join(t['value'])})",
                                "usage": note(t) or "Gradient stops."} for t in by.get("colorArray", [])]}
    styles = by.get("fontStyle", [])
    ui = [t for t in styles if t.get("family") == "SF Pro"]
    disp = [t for t in styles if t.get("family") == "Display"]

    def style(t):
        s = {"name": t["name"], "fontSize": f"{t['size']:g}px", "fontWeight": t["cssWeight"],
             "lineHeight": "1.2" if t.get("family") == "Display" else "1.4",
             "usage": note(t) or (f"Dynamic Type `{t['style']}` style at its default size."
                                  if t.get("style") else f"{t['size']:g}px.")}
        if t.get("italic"):
            s["fontStyle"] = "italic"
        return s
    out["type"] = {"fonts": [{"family": "ABC Otto", "file": f"fonts/{os.path.basename(rel)}", "weight": str(w), "style": s}
                             for _, rel, w, s in cfg["fonts"]],
                   "families": {"display": "\"ABC Otto\", Georgia, serif",
                                "ui": "-apple-system, \"SF Pro Text\", system-ui, sans-serif"},
                   "groups": [{"name": "Display — ABC Otto", "family": "display", "styles": [style(t) for t in disp]},
                              {"name": "UI — SF Pro (system)", "family": "ui", "styles": [style(t) for t in ui]}]}
    return out


def pal_names(by):
    return {t["name"] for t in by.get("color", [])}


def role_fallback(name):
    for prefix, text in (("text", "Text colour."), ("surface", "Surface fill."), ("border", "Border or hairline."),
                         ("accent", "Accent."), ("icon", "Icon colour."), ("aurora", "Paywall aurora sweep stop."),
                         ("canvas", "Page canvas."), ("progress", "Progress track."), ("skeleton", "Loading skeleton."),
                         ("on", "Content on a coloured or light fill."), ("switch", "iOS switch track.")):
        if name.startswith(prefix):
            return text
    return "Semantic colour role."


def check(tokens):
    """The type's own refusals, checked before anything is written: a name it rejects or a
    name used twice across families would drop a token silently on the page."""
    seen, problems = {}, []
    for fam, body in tokens.items():
        if fam in ("name", "version", "meta", "type") or not isinstance(body, dict):
            continue
        if fam != "color" and len(body.get("tokens", [])) > FAMILY_CAP:
            problems.append(f"{fam}: {len(body['tokens'])} tokens, the page shows at most {FAMILY_CAP}")
        for t in body.get("tokens", []):
            if fam not in ("color", "shadow", "spacing", "radius") and not OTHER_VALUE.match(str(t.get("value", ""))):
                problems.append(f"{fam}: {t['name']!r} value the page cannot read ({str(t.get('value'))[:60]})")
            if not NAME.match(t["name"]):
                problems.append(f"{fam}: bad name {t['name']!r}")
            if t["name"] in seen:
                problems.append(f"{fam}: {t['name']!r} also in {seen[t['name']]}")
            seen[t["name"]] = fam
            if not t.get("usage"):
                problems.append(f"{fam}: {t['name']!r} has no usage note")
    return problems


def compile_css(tokens):
    """The first `tokens.css`, laid out as the type compiles it, so previews and the cover
    have their variables before anyone saves on the page (which then takes the file over)."""
    def val(v):
        return re.sub(r"\{([^}]+)\}", r"var(--\1)", v)
    themes = [th["id"] for th in tokens["color"]["themes"]]
    first = themes[0]
    out = [f"/* {tokens['name']} — generated from tokens.json */", "",
           f':root, [data-theme="{first}"] {{']
    for c in tokens["color"]["tokens"]:
        v = c["value"]
        out.append(f"  --{c['name']}: {val(v if isinstance(v, str) else v.get(first, ''))};")
    for s in tokens.get("shadow", {}).get("tokens", []):
        out.append(f"  --{s['name']}: {s['value']};")
    out.append("}")
    for th in themes[1:]:
        out += ["", f'[data-theme="{th}"] {{']
        for c in tokens["color"]["tokens"]:
            if isinstance(c["value"], dict) and th in c["value"]:
                out.append(f"  --{c['name']}: {val(c['value'][th])};")
        out.append("}")
    out += ["", ":root {"]
    for fam, body in tokens.items():
        if fam in ("color", "shadow", "type", "name", "version", "meta") or not isinstance(body, dict):
            continue
        for x in body.get("tokens", []):
            out.append(f"  --{x['name']}: {x['value']};")
    for k, stack in tokens["type"]["families"].items():
        out.append(f"  --font-{k}: {stack};")
    out.append("}")
    for g in tokens["type"]["groups"]:
        for s in g["styles"]:
            decl = [f"font-family: var(--font-{s.get('family', g['family'])})", f"font-size: {s['fontSize']}",
                    f"font-weight: {s['fontWeight']}"]
            if s.get("lineHeight"):
                decl.append(f"line-height: {s['lineHeight']}")
            if s.get("letterSpacing"):
                decl.append(f"letter-spacing: {s['letterSpacing']}")
            if s.get("fontStyle"):
                decl.append(f"font-style: {s['fontStyle']}")
            out.append(f".{s['name']} {{ {'; '.join(decl)}; }}")
    for f in tokens["type"]["fonts"]:
        fmt = {"ttf": "truetype", "otf": "opentype", "woff": "woff", "woff2": "woff2"}[f["file"].rsplit(".", 1)[1]]
        out.append(f"@font-face {{ font-family: \"{f['family']}\"; src: url(\"{f['file']}\") format(\"{fmt}\"); "
                   f"font-weight: {f['weight']}; font-style: {f['style']}; font-display: swap; }}")
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------ files ----

def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def screens(cfg, repo):
    """The current captures: names carrying one of the app's variant suffixes."""
    shots = os.path.join(repo, cfg["screenshots"])
    suffixes = tuple(cfg.get("screenVariants") or ())
    out = []
    for f in sorted(os.listdir(shots)) if os.path.isdir(shots) else []:
        if f.endswith(".png") and (not suffixes or f[:-4].endswith(suffixes)):
            out.append(f)
    return shots, out


def main_build(a):
    cfg = resolve(a.app, a.repo)
    manifest = json.load(open(a.tokens, encoding="utf-8"))
    if not manifest.get("complete"):
        raise SystemExit("the token manifest has parser gaps — fix them (extract_tokens.py --strict) first")
    tokens = (build_rn if cfg.get("format") == "rn" else build_swift)(manifest, cfg["repo"], cfg)
    problems = check(tokens)
    if problems:
        raise SystemExit("tokens the Design System page would drop:\n  " + "\n  ".join(problems))
    proj = os.path.join(a.out, "project")
    write(os.path.join(proj, "tokens.json"), json.dumps(tokens, indent=2, ensure_ascii=False) + "\n")
    write(os.path.join(proj, "tokens.css"), compile_css(tokens))
    for fam, rel, _, _ in cfg["fonts"]:
        dst = os.path.join(proj, "fonts", os.path.basename(rel))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy(os.path.join(cfg["repo"], cfg["fontsDir"], rel), dst)
    brand = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "references",
                         f"design-system-{cfg['key']}.md")
    write(os.path.join(proj, "README.md"), open(brand, encoding="utf-8").read())
    cover = os.path.join(os.path.dirname(brand), f"design-system-{cfg['key']}-cover.html")
    write(os.path.join(proj, "components", "Cover", "preview.html"), open(cover, encoding="utf-8").read())
    shots, names = screens(cfg, cfg["repo"])
    up = os.path.join(a.out, "screens")
    os.makedirs(up, exist_ok=True)
    for f in names:
        # Downscaled like the standalone bundle: a 3x capture is ~1.5 MB and a tile shows
        # it at a few hundred px. `sips` is macOS-only; without it the original ships.
        src, dst = os.path.join(shots, f), os.path.join(up, f)
        try:
            ok = subprocess.run(["sips", "-Z", str(cfg["screenshotWidth"]), src, "--out", dst],
                                capture_output=True).returncode == 0
        except OSError:
            ok = False
        if not ok:
            shutil.copy(src, dst)
    write(os.path.join(a.out, "screens.json"), json.dumps(names, indent=1) + "\n")
    variants = ", ".join(f"`{s}`" for s in cfg.get("screenVariants") or ()) or "one per screen"
    write(os.path.join(proj, "assets", "Screens", "README.md"),
          f"# Screens\n\nLive captures of the current {cfg['displayName']} app from its simulator capture "
          f"store ({variants}), downscaled to {cfg['screenshotWidth']}px wide. Each name is the screen, then its "
          "variant. Use them to see tokens in place and match real layouts; they are not mockups, and their "
          "copy is not approved marketing language.\n")
    counts = {k: len(v["tokens"]) for k, v in tokens.items() if isinstance(v, dict) and "tokens" in v}
    counts["typeStyles"] = sum(len(g["styles"]) for g in tokens["type"]["groups"])
    counts["fonts"] = len(tokens["type"]["fonts"])
    counts["screens"] = len(names)
    print(json.dumps(counts, indent=2))


def main_index(a):
    cfg = resolve(a.app, a.repo)
    uploads = json.load(open(a.uploads, encoding="utf-8"))   # {file: {"blob": id, "size": n}}
    if a.existing:
        idx = json.load(open(a.existing, encoding="utf-8"))
        if not (idx.get("createdOnFiles") or idx.get("convertedFrom")):
            raise SystemExit("the existing index has no marker — not an index; writing nothing over it")
    else:
        idx = {"v": 3, "layout": "files",
               "createdOnFiles": {"v": 1, "at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
               "title": cfg["displayName"], "namespace": cfg["displayName"], "libraries": [],
               "sections": {}, "groups": [], "assetGroups": {}, "blobs": {}, "docs": {"readme": "project/README.md", "sections": []}}
    files = {}
    for name in sorted(uploads):
        key = re.sub(r"[^A-Za-z0-9_./-]", lambda m: "~%02x" % ord(m.group(0)), name)
        files[key] = {"name": name, "blob": uploads[name]["blob"], "size": uploads[name]["size"], "type": "image/png"}
    idx.setdefault("assetGroups", {})["Screens"] = {"name": "Screens", "tile": "s", "order": sorted(files), "files": files}
    if "Screens" not in idx.setdefault("groups", []):
        idx["groups"].append("Screens")
    idx["lastChange"] = {"by": a.by, "at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "via": "Claude Code · product:sync-design-system", "note": a.note}
    write(os.path.join(a.out, "project", "design-system.json"), json.dumps(idx, indent=2, ensure_ascii=False) + "\n")
    print(f"index: {len(files)} screens")


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    b = sub.add_parser("build", help="tokens, README, fonts, cover, screens list")
    for p in (ap, b):
        p.add_argument("--app", default="aura")
        p.add_argument("--repo", default="")
        p.add_argument("--out", required=p is b)
        p.add_argument("--tokens")
    i = sub.add_parser("index", help="the index, naming the uploaded screens")
    i.add_argument("--app", default="aura")
    i.add_argument("--repo", default="")
    i.add_argument("--out", required=True)
    i.add_argument("--uploads", required=True)
    i.add_argument("--existing")
    i.add_argument("--by", default="Claude")
    i.add_argument("--note", default="regenerated from code")
    a = ap.parse_args(argv)
    if a.cmd == "index":
        return main_index(a)
    if not a.out or not a.tokens:
        ap.error("--out and --tokens are required")
    return main_build(a)


if __name__ == "__main__":
    main()
