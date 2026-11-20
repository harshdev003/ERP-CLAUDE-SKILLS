#!/usr/bin/env python3
"""Extract an app's design tokens from its own theme source.

The app's Swift theme files are the source of truth. This reads them and emits
two projections:

  tokens.json  — typed manifest: name, kind, resolved value, the literal it came
                 from, and the file:line it lives at
  tokens.css   — CSS custom properties, the shape Claude Design compiles

Anything it cannot parse is reported as an `unparsed` entry rather than dropped,
and a completeness gate fails the run if any `static let` in the theme goes
unaccounted for — a new declaration form shows up as a loud gap, never as a
silently smaller design system.

    python3 extract_tokens.py --app aurie --out DIR
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from apps import resolve  # noqa: E402

import stops  # noqa: E402

THEME = ""  # set from --app in main()

# Windows consoles default to cp1252; this module prints em-dashes, arrows and
# whatever non-ASCII shows up in an unparsed Swift literal. Without this a run
# dies on UnicodeEncodeError rather than on anything to do with the design system.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


unparsed: list[dict] = []


# ---------------------------------------------------------------- numbers ----

def num(s: str) -> float:
    """Evaluate a Swift numeric literal, including the `17/255` channel form."""
    s = s.strip()
    if "/" in s:
        a, b = s.split("/", 1)
        return float(a) / float(b)
    return float(s)


def hex8(r: float, g: float, b: float, a: float) -> str:
    out = "#%02X%02X%02X" % (round(r * 255), round(g * 255), round(b * 255))
    return out if a >= 0.999 else out + "%02X" % round(a * 255)


def css_color(r: float, g: float, b: float, a: float) -> str:
    if a >= 0.999:
        return hex8(r, g, b, 1.0)
    return "rgba(%d, %d, %d, %s)" % (
        round(r * 255), round(g * 255), round(b * 255), ("%.3f" % a).rstrip("0").rstrip("."))


# ----------------------------------------------------------------- colors ----

NAMED = {"white": (1.0, 1.0, 1.0), "black": (0.0, 0.0, 0.0), "clear": (0.0, 0.0, 0.0)}


def parse_color(expr: str):
    """Swift Color literal -> (r, g, b, a) in 0..1, or None if unrecognised."""
    expr = expr.strip()

    # A trailing .opacity(N) multiplies whatever precedes it.
    mult = 1.0
    m = re.search(r"\.opacity\(\s*([0-9./]+)\s*\)\s*$", expr)
    if m:
        mult = num(m.group(1))
        expr = expr[: m.start()].strip()

    m = re.match(r"^(?:Color)?\(?hex:\s*\"#?([0-9A-Fa-f]{6,8})\"\s*\)$", expr) or \
        re.match(r"^Color\(hex:\s*\"#?([0-9A-Fa-f]{6,8})\"\s*\)$", expr)
    if m:
        h = m.group(1)
        r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
        a = int(h[6:8], 16) / 255 if len(h) == 8 else 1.0
        return r, g, b, a * mult

    m = re.match(
        r"^Color\(\s*(?:\.sRGB\s*,\s*)?red:\s*([0-9./]+)\s*,\s*green:\s*([0-9./]+)\s*,"
        r"\s*blue:\s*([0-9./]+)\s*(?:,\s*opacity:\s*([0-9./]+)\s*)?\)$", expr)
    if m:
        r, g, b = (num(m.group(i)) for i in (1, 2, 3))
        a = num(m.group(4)) if m.group(4) else 1.0
        return r, g, b, a * mult

    m = re.match(r"^(?:Color)?\.(white|black|clear)$", expr)
    if m:
        r, g, b = NAMED[m.group(1)]
        a = 0.0 if m.group(1) == "clear" else 1.0
        return r, g, b, a * mult

    return None


# ------------------------------------------------------------------ files ----

def read(name: str) -> list[str]:
    with open(os.path.join(THEME, name)) as fh:
        return fh.read().splitlines()


def simple_decls(lines: list[str]):
    """Yield (name, rhs, lineno) for one-line `static let X = rhs` declarations."""
    for i, ln in enumerate(lines, 1):
        m = re.match(r"\s*static let ([A-Za-z0-9_]+)\s*(?::\s*[A-Za-z<>?\[\] ]+)?=\s*(.+?)\s*$", ln)
        # A bare `[` or `Ctor(` opens a multi-line declaration, handled elsewhere.
        if m and m.group(2) not in ("[",) and not re.match(r"^[A-Za-z]+\($", m.group(2)):
            yield m.group(1), m.group(2), i


def multiline_decls(lines: list[str], ctor: str):
    """Yield (name, body, lineno) for `static let X = Ctor(...)` declarations,
    whether written across several lines or collapsed onto one."""
    i = 0
    while i < len(lines):
        one = re.match(r"\s*static let ([A-Za-z0-9_]+)\s*=\s*%s\((.+)\)\s*$" % ctor, lines[i])
        if one:
            yield one.group(1), one.group(2), i + 1
            i += 1
            continue
        m = re.match(r"\s*static let ([A-Za-z0-9_]+)\s*=\s*%s\($" % ctor, lines[i])
        if not m:
            i += 1
            continue
        name, start, depth, body = m.group(1), i + 1, 1, []
        i += 1
        while i < len(lines) and depth > 0:
            depth += lines[i].count("(") - lines[i].count(")")
            if depth > 0:
                body.append(lines[i])
            i += 1
        yield name, "\n".join(body), start


# ----------------------------------------------------------------- extract ---

# One `Gradient.Stop` written as `.init(color:location:)` or `Gradient.Stop(color:location:)`.
STOP = re.compile(r"^(?:\.init|Gradient\.Stop)\(\s*color:\s*(.+?)\s*,\s*location:\s*([0-9./]+)\s*\)$")


def extract_color_arrays(lines):
    """`static let X = [Color, Color]` — the stop pairs behind shadow and
    placeholder overlays. Not single colors, so they get their own kind.

    `static let X: [Gradient.Stop] = [.init(color:location:), …]` is a gradient
    with explicit stop locations and no direction: the call site sets the
    direction, so it is emitted as a `gradient` drawn leading → trailing (90deg),
    the way `LinearGradient(stops:startPoint:.leading,endPoint:.trailing)` draws it."""
    out = []
    i = 0
    while i < len(lines):
        m = re.match(r"\s*static let ([A-Za-z0-9_]+)\s*(?::\s*\[[A-Za-z.]+\])?\s*=\s*\[$", lines[i])
        if not m:
            i += 1
            continue
        name, start, body = m.group(1), i + 1, []
        i += 1
        while i < len(lines) and not re.match(r"\s*\]\s*$", lines[i]):
            body.append(lines[i])
            i += 1
        i += 1
        exprs = [e.strip() for e in split_top(" ".join(x.strip() for x in body)) if e.strip()]
        pairs = [STOP.match(e) for e in exprs]
        if exprs and all(pairs):
            colors = [parse_color(p.group(1)) for p in pairs]
            try:
                locs = [num(p.group(2)) for p in pairs]
            except ValueError:
                colors = [None]
            if any(c is None for c in colors):
                unparsed.append({"file": "Colors.swift", "line": start, "name": name,
                                 "literal": " ".join(exprs)[:120]})
                continue
            css = [css_color(*c) for c in colors]
            stops_css = ", ".join(f"{c} {l * 100:g}%" for c, l in zip(css, locs))
            out.append({
                "name": name, "kind": "gradient", "angle": 90.0,
                "stops": css, "locations": locs,
                "css": f"linear-gradient(90deg, {stops_css})",
                "source": f"Colors.swift:{start}",
            })
            continue
        stops = [parse_color(e) for e in exprs]
        if any(s is None for s in stops):
            unparsed.append({"file": "Colors.swift", "line": start, "name": name,
                             "literal": " ".join(exprs)[:120]})
            continue
        css = [css_color(*s) for s in stops]
        out.append({
            "name": name, "kind": "colorArray", "value": css, "css": ", ".join(css),
            "literal": f"[{len(css)} colors]", "source": f"Colors.swift:{start}",
        })
    return out


def extract_colors():
    lines = read("Colors.swift")
    out = extract_color_arrays(lines)
    for name, rhs, ln in simple_decls(lines):
        parsed = parse_color(rhs)
        if parsed is None:
            unparsed.append({"file": "Colors.swift", "line": ln, "name": name, "literal": rhs})
            continue
        r, g, b, a = parsed
        out.append({
            "name": name, "kind": "color",
            "value": hex8(r, g, b, a), "css": css_color(r, g, b, a),
            "alpha": round(a, 3), "literal": rhs, "source": f"Colors.swift:{ln}",
        })
    return out


def extract_scale(fname: str, kind: str):
    out = []
    lines = read(fname)
    for name, rhs, ln in simple_decls(lines):
        m = re.match(r"^([0-9.]+)$", rhs.rstrip())
        if not m:
            unparsed.append({"file": fname, "line": ln, "name": name, "literal": rhs})
            continue
        v = float(m.group(1))
        out.append({
            "name": name, "kind": kind,
            "value": int(v) if v == int(v) else v,
            "css": f"{int(v) if v == int(v) else v}px",
            "literal": rhs, "source": f"{fname}:{ln}",
        })
    return out


# SF Pro's real weights. The previous map collapsed semibold to 500 and bold to
# 600 because Inter shipped only five cuts and `Fonts.swift` resolved onto them;
# SF Pro carries every weight, so a token named bold is bold. Used by the body
# face only — the display face carries its own numeric map per role.
WEIGHT_CSS = {"ultraLight": 100, "thin": 100, "light": 300, "regular": 400,
              "medium": 500, "semibold": 600, "bold": 700,
              "heavy": 800, "black": 900}
# The body face is SF Pro — the system font — reached through a Dynamic Type text
# style rather than a point size, so the size below is the style's size at the
# default Dynamic Type setting. It scales from there on device; the number is the
# anchor, not a fixed value. SF Pro carries every weight, so a token named bold is
# bold on screen.
TEXT_STYLE_PT = {
    "largeTitle": 34, "title": 28, "title2": 22, "title3": 20, "headline": 17,
    "body": 17, "callout": 16, "subheadline": 15, "footnote": 13,
    "caption": 12, "caption2": 11,
}
BODY_STACK = '-apple-system, "SF Pro Text", system-ui, sans-serif'


def extract_fonts():
    out = []
    lines = read("Fonts.swift")
    families = {}
    for name, rhs, ln in simple_decls(lines):
        m = re.match(r'^"([^"]+)"$', rhs)
        if m:
            families[name] = m.group(1)
            out.append({
                "name": name, "kind": "fontFamily", "value": m.group(1),
                "css": f'"{m.group(1)}"', "literal": rhs, "source": f"Fonts.swift:{ln}",
            })
            continue
        m = re.match(r"^Font\.text\(\s*\.([A-Za-z0-9]+)\s*,\s*weight:\s*\.([a-z]+)\s*\)$", rhs)
        if m:
            style, weight = m.group(1), m.group(2)
            if style not in TEXT_STYLE_PT:
                # A text style this table does not know would otherwise be emitted at
                # 0 pt — a token that looks parsed, reads as a real size, and leaves
                # `unparsed` at zero so the completeness gate still passes. Report it.
                unparsed.append({"file": "Fonts.swift", "line": ln, "name": name,
                                 "literal": rhs})
                continue
            size = float(TEXT_STYLE_PT[style])
            out.append({
                "name": name, "kind": "fontStyle", "family": "SF Pro", "style": style,
                "size": size, "weight": weight, "cssWeight": WEIGHT_CSS.get(weight, 400),
                "css": f"{WEIGHT_CSS.get(weight, 400)} {size:g}px/1.4 {BODY_STACK}",
                "literal": rhs, "source": f"Fonts.swift:{ln}",
            })
            continue
        # The decorative tokens are the one non-scaling form: a fixed size below the
        # 11 pt floor, allowed for decoration only.
        m = re.match(r"^Font\.textFixed\(\s*([0-9.]+)\s*,\s*weight:\s*\.([a-z]+)\s*\)$", rhs)
        if m:
            size, weight = float(m.group(1)), m.group(2)
            out.append({
                "name": name, "kind": "fontStyle", "family": "SF Pro", "fixed": True,
                "size": size, "weight": weight, "cssWeight": WEIGHT_CSS.get(weight, 400),
                "css": f"{WEIGHT_CSS.get(weight, 400)} {size:g}px/1.4 {BODY_STACK}",
                "literal": rhs, "source": f"Fonts.swift:{ln}",
            })
            continue
        m = re.match(r"^Font\.(display|displayLight|displayMedium|displayLightItalic)"
                     r"\(\s*size:\s*([0-9.]+)\s*(?:,\s*relativeTo:[^)]*)?\)$", rhs)
        if m:
            role, size = m.group(1), float(m.group(2))
            face = {"display": "displayRegular", "displayLight": "displayLight",
                    "displayMedium": "displayMedium",
                    "displayLightItalic": "displayLightItalic"}[role]
            weight = {"display": 400, "displayLight": 300,
                      "displayMedium": 500, "displayLightItalic": 300}[role]
            out.append({
                "name": name, "kind": "fontStyle", "family": "Display",
                "face": face, "size": size, "weight": role, "cssWeight": weight,
                "italic": role.endswith("Italic"),
                "css": (f"{'italic ' if role.endswith('Italic') else ''}{weight} "
                        f"{size:g}px/1.2 Display, Georgia, serif"),
                "literal": rhs, "source": f"Fonts.swift:{ln}",
            })
            continue
        unparsed.append({"file": "Fonts.swift", "line": ln, "name": name, "literal": rhs})
    return out, families


def extract_gradients():
    out = []
    lines = read("Gradients.swift")
    for name, body, ln in multiline_decls(lines, "GradientStyle"):
        flat = " ".join(x.strip() for x in body.splitlines())
        angle = re.search(r"angle:\s*(-?[0-9.]+)", flat)
        colors_m = re.search(r"colors:\s*\[(.*?)\]\s*,\s*locations", flat)
        locs_m = re.search(r"locations:\s*(?:\[([^\]]*)\]|(nil))", flat)
        if not (angle and colors_m and locs_m):
            unparsed.append({"file": "Gradients.swift", "line": ln, "name": name,
                             "literal": flat[:160]})
            continue
        exprs = [e.strip() for e in split_top(colors_m.group(1)) if e.strip()]
        stops, ok = [], True
        for e in exprs:
            p = parse_color(e)
            if p is None:
                ok = False
                unparsed.append({"file": "Gradients.swift", "line": ln,
                                 "name": f"{name} (stop)", "literal": e})
                break
            stops.append(css_color(*p))
        if not ok:
            continue
        # `locations: nil` — and any count mismatch — means evenly distributed,
        # which is exactly what GradientStyle.linear falls back to.
        # Skip empty segments: Swift style mandates a trailing comma in
        # multi-line collections, and `float("")` would abort the whole
        # extraction before the completeness gate ever ran.
        try:
            locs = ([num(x) for x in locs_m.group(1).split(",") if x.strip()]
                    if locs_m.group(1) else [])
        except ValueError as e:
            unparsed.append({"file": "Gradients.swift", "line": ln, "name": name,
                             "literal": f"locations: {locs_m.group(1)} ({e})"})
            continue
        if len(locs) != len(stops):
            n = len(stops)
            locs = [0.0] if n == 1 else [i / (n - 1) for i in range(n)]
        deg = float(angle.group(1))
        pairs = ", ".join(f"{c} {l * 100:g}%" for c, l in zip(stops, locs))
        out.append({
            "name": name, "kind": "gradient", "angle": deg,
            "stops": stops, "locations": locs,
            "css": f"linear-gradient({deg:g}deg, {pairs})",
            "source": f"Gradients.swift:{ln}",
        })
    return out


def split_top(s: str) -> list[str]:
    """Split on commas that are not inside parentheses."""
    out, depth, cur = [], 0, []
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return out


def extract_shadows():
    out = []
    lines = read("Shadows.swift")
    for name, body, ln in multiline_decls(lines, "Shadow"):
        flat = " ".join(x.strip() for x in body.splitlines())
        cm = re.search(r"color:\s*(.+?)\s*,\s*radius:", flat)
        nums = {k: re.search(rf"{k}:\s*(-?[0-9.]+)", flat) for k in
                ("radius", "offsetX", "offsetY")}
        if not cm or not all(nums.values()):
            unparsed.append({"file": "Shadows.swift", "line": ln, "name": name,
                             "literal": flat[:160]})
            continue
        p = parse_color(cm.group(1))
        if p is None:
            unparsed.append({"file": "Shadows.swift", "line": ln, "name": name,
                             "literal": cm.group(1)})
            continue
        r = float(nums["radius"].group(1))
        ox = float(nums["offsetX"].group(1))
        oy = float(nums["offsetY"].group(1))
        out.append({
            "name": name, "kind": "shadow", "color": css_color(*p),
            "radius": r, "offsetX": ox, "offsetY": oy,
            # SwiftUI's `radius` is a Gaussian sigma; CSS blur-radius is ~2x it.
            "css": f"{ox:g}px {oy:g}px {r * 2:g}px {css_color(*p)}",
            "source": f"Shadows.swift:{ln}",
        })
    return out


# -------------------------------------------------------------------- emit ---

def kebab(name: str) -> str:
    """camelCase -> kebab-case, keeping digit runs whole (`white50` -> `white-50`,
    not `white-5-0`)."""
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Za-z])(?=[0-9])", "-", name)
    return s.lower().strip("-")


def var_name(token: dict, prefix: str) -> str:
    """`--<prefix>-<name>`, without repeating the prefix the Swift name already
    carries (`spacing.space0` -> `--space-0`, not `--space-space-0`)."""
    n = kebab(token["name"])
    for dup in (prefix, "border-radius", "font"):
        if n.startswith(dup + "-"):
            n = n[len(dup) + 1:]
            break
    return f"--{prefix}-{n}"


def main():
    global THEME
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--app", default="aurie", help="app key from apps.py")
    ap.add_argument("--out", default=".", help="directory to write tokens.{json,css} into")
    ap.add_argument("--repo", default="", help="read this checkout or worktree instead of "
                                              "the app's configured repo")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 on a parser gap instead of recording it and continuing")
    args = ap.parse_args()
    cfg = resolve(args.app, args.repo)
    THEME = os.path.join(cfg["repo"], cfg["themeDir"])
    if not os.path.isdir(THEME):
        raise SystemExit(f"theme directory not found: {THEME}")
    if cfg.get("format") == "rn":
        return main_rn(cfg, args)

    colors = extract_colors()
    # A `[Gradient.Stop]` in Colors.swift is a gradient, so it sits with the gradients.
    stop_gradients = [t for t in colors if t["kind"] == "gradient"]
    colors = [t for t in colors if t["kind"] != "gradient"]
    fonts, families = extract_fonts()
    spacing = extract_scale("Spacing.swift", "spacing")
    radius = extract_scale("BorderRadius.swift", "radius")
    gradients = extract_gradients() + stop_gradients
    shadows = extract_shadows()

    all_tokens = colors + fonts + spacing + radius + gradients + shadows

    # Completeness gate. `gGray0` was a one-line GradientStyle the block parser
    # never matched, and it vanished without even landing in `unparsed` — so
    # assert every `static let` in every source file is accounted for, rather
    # than trusting the parsers to notice their own blind spots.
    accounted = {t["name"] for t in all_tokens} | {u["name"].split(" ")[0] for u in unparsed}
    gaps = []
    for fname in ("Colors.swift", "Fonts.swift", "Spacing.swift",
                  "BorderRadius.swift", "Gradients.swift", "Shadows.swift"):
        lines = read(fname)
        declared = set(re.findall(r"static let ([A-Za-z0-9_]+)", "\n".join(lines)))
        for name in sorted(declared - accounted):
            ln = next((i for i, l in enumerate(lines, 1) if re.search(rf"static let {name}\b", l)), 0)
            gaps.append({"file": fname, "line": ln, "name": name,
                         "literal": lines[ln - 1].strip() if ln else "", "gap": "no parser reads this declaration"})
    if gaps:
        print("PARSER GAP — no parser reads these declarations:", file=sys.stderr)
        for g in gaps:
            print(f"  {g['file']}:{g['line']}  {g['name']}", file=sys.stderr)
        if args.strict:
            sys.exit(f"--strict: {len(gaps)} declaration(s) no parser reads")
        # Recorded, not dropped: the gap is in the manifest, the count and a FOLLOWUP line, and the
        # build goes on without that one token rather than stopping every other card behind it.
        unparsed.extend(gaps)

    manifest = {
        "app": cfg["key"],
        "source": cfg["themeDir"],
        "complete": not gaps,
        "counts": {
            "color": len(colors), "font": len(fonts), "spacing": len(spacing),
            "radius": len(radius), "gradient": len(gradients), "shadow": len(shadows),
            "unparsed": len(unparsed), "parser_gaps": len(gaps),
        },
        "tokens": all_tokens,
        "unparsed": unparsed,
    }

    out_dir = args.out
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "tokens.json"), "w") as fh:
        json.dump(manifest, fh, indent=2)

    css = [f"/* {cfg['displayName']} design tokens — generated from {cfg['themeDir']}.",
           "   Do not edit by hand: regenerate with extract_tokens.py. */", "", ":root {"]

    def block(title, rows, prefix):
        css.append(f"  /* {title} */")
        for t in rows:
            css.append(f"  {var_name(t, prefix)}: {t['css']};")
        css.append("")

    block("Color", colors, "color")
    block("Spacing", spacing, "space")
    block("Radius", radius, "radius")
    block("Gradient", gradients, "gradient")
    block("Shadow", shadows, "shadow")
    css.append("  /* Type */")
    for t in fonts:
        if t["kind"] == "fontFamily":
            css.append(f"  --font-family-{kebab(t['name'])}: {t['css']};")
    for t in fonts:
        if t["kind"] == "fontStyle":
            css.append(f"  {var_name(t, 'font')}: {t['css']};")
    css.append("}")

    with open(os.path.join(out_dir, "tokens.css"), "w") as fh:
        fh.write("\n".join(css) + "\n")

    print(json.dumps(manifest["counts"], indent=2))
    if unparsed:
        print("\nUNPARSED:")
        for u in unparsed:
            print(f"  {u['file']}:{u['line']}  {u['name']} = {u['literal'][:90]}")
    # One line per gap a person must fix; an unattended run raises a follow-up for each.
    for g in gaps:
        print(f"FOLLOWUP token {g['file']}:{g['name']}: {g['gap']} ({g['literal'][:80]})")


def main_rn(cfg, args):
    """A React Native theme: the same manifest and CSS, read by `extract_rn`. Semantic
    roles carry a dark and a light value; `:root` holds the dark ones (the app's default
    theme) and `[data-theme="light"]` overrides them."""
    import extract_rn
    groups, gaps = extract_rn.extract(THEME, skip=cfg.get("themeSkip", ()))
    for g in gaps:
        g.setdefault("gap", "no parser reads this declaration")
    if gaps:
        print("PARSER GAP — no parser reads these declarations:", file=sys.stderr)
        for g in gaps:
            print(f"  {g['file']}:{g['line']}  {g['name']}", file=sys.stderr)
        if args.strict:
            sys.exit(f"--strict: {len(gaps)} declaration(s) no parser reads")
    tokens = [t for kind in groups for t in groups[kind]]
    manifest = {
        "app": cfg["key"], "source": cfg["themeDir"], "format": "rn", "complete": not gaps,
        "counts": {**{k: len(v) for k, v in groups.items()},
                   "unparsed": len(gaps), "parser_gaps": len(gaps)},
        "tokens": tokens, "unparsed": gaps,
    }
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "tokens.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)

    css = [f"/* {cfg['displayName']} design tokens — generated from {cfg['themeDir']}.",
           "   Do not edit by hand: regenerate with extract_tokens.py. */", "", ":root {"]

    def block(title, rows, prefix, field="css"):
        css.append(f"  /* {title} */")
        for t in rows:
            css.append(f"  {var_name(t, prefix)}: {t[field]};")
        css.append("")

    block("Palette", groups["color"], "color")
    block("Topic glows", groups["colorArray"], "color")
    block("Roles (dark, the default theme)", groups["role"], "role", "dark")
    # `d16` is the 16pt step: `--space-16`, not `--space-d-16`.
    block("Spacing", [{**t, "name": t["name"][1:]} for t in groups["spacing"]], "space")
    block("Layout", groups["layout"], "layout")
    block("Radius", groups["radius"], "radius")
    block("Gradient", groups["gradient"], "gradient")
    block("Shadow", groups["shadow"], "shadow")
    css.append("  /* Type */")
    for t in groups["fontFamily"]:
        css.append(f"  --font-family-{kebab(t['name'])}: {t['css']};")
    for t in groups["fontStyle"]:
        css.append(f"  {var_name(t, 'font')}: {t['css']};")
    css += ["}", "", '[data-theme="light"] {']
    for t in groups["role"]:
        css.append(f"  {var_name(t, 'role')}: {t['light']};")
    css.append("}")
    with open(os.path.join(args.out, "tokens.css"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(css) + "\n")

    print(json.dumps(manifest["counts"], indent=2))
    for g in gaps:
        print(f"FOLLOWUP token {g['file']}:{g['name']}: {g['gap']} ({g['literal'][:80]})")


if __name__ == "__main__":
    stops.run(main, "extract-tokens")
