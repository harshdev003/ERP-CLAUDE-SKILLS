"""Extract a React Native app's design tokens from its JS theme modules.

The JS counterpart of the Swift readers in `extract_tokens.py`, for an app whose
`apps.py` entry has `"format": "rn"`. It reads the theme folder's object literals
as text (no Node, no bundler) and writes the same `tokens.json` / `tokens.css`
schema, so `build_bundle.py` renders either app from one manifest.

Every key of every object it reads is accounted for: parsed into a token, or listed
in `unparsed` with its file and line. A new value form is a loud gap, never a
silently smaller design system.

Layout (Aura 4.0, `src/styles/`):

  redesign/palette.js        literal colours                     -> color
  redesignDark/color.js      semantic roles, `Color.<palette>`   -> role (dark value)
  redesignLight/color.js     the same roles                      -> role (light value)
  redesign/dimensions.js     `scaledDimension(n)`                -> spacing (dN), layout
  redesign/borderRadius.js   `scaledDimension(n)`                -> radius
  redesign/gradients.js      `gradient(angle, [..], [..])`, radial stop lists -> gradient
  redesign/shadows.js        `shadow(y, radius, color, elevation)`             -> shadow
  redesign/topicGlows.js     `glow('r, g, b', 'r, g, b')`        -> colorArray
  redesign/fonts.js          `{fontFamily, fontSize, lineHeight}` -> fontFamily, fontStyle
"""
import os
import re

unparsed: list[dict] = []

# PostScript name -> (family, CSS weight, style). The app names faces by PostScript
# name, so this is the whole mapping; a name missing here is reported, not guessed.
FACES = {
    "CharterRegular": ("Charter", 400, "normal"),
    "CharterItalic": ("Charter", 400, "italic"),
    "NunitoSans-Light": ("Nunito Sans", 300, "normal"),
    "NunitoSans-Regular": ("Nunito Sans", 400, "normal"),
    "NunitoSans-Medium": ("Nunito Sans", 500, "normal"),
    "NunitoSans-SemiBold": ("Nunito Sans", 600, "normal"),
    "NunitoSans-Bold": ("Nunito Sans", 700, "normal"),
}
STACK = {"Charter": "Charter, Georgia, serif",
         "Nunito Sans": '"Nunito Sans", -apple-system, system-ui, sans-serif'}


# ------------------------------------------------------------------ text ----

def strip_comments(src: str) -> str:
    """Drop // and /* */ comments outside string and template literals, keeping
    newlines so line numbers survive."""
    out, i, n, quote = [], 0, len(src), None
    while i < n:
        c = src[i]
        if quote:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(src[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "'\"`":
            quote = c
            out.append(c)
            i += 1
        elif src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j == -1 else j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j == -1 else j + 2
            out.append("\n" * src.count("\n", i, j))
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def match_close(s: str, start: int) -> int:
    """Index of the bracket closing the one at `start`, skipping strings."""
    pairs = {"{": "}", "[": "]", "(": ")"}
    stack, quote, i = [], None, start
    while i < len(s):
        c = s[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "'\"`":
            quote = c
        elif c in pairs:
            stack.append(pairs[c])
        elif stack and c == stack[-1]:
            stack.pop()
            if not stack:
                return i
        i += 1
    raise ValueError(f"unbalanced bracket at {start}")


def split_top(s: str) -> list[tuple[str, int]]:
    """Split on top-level commas: [(piece, offset)]."""
    parts, depth, quote, start, i = [], 0, None, 0, 0
    while i < len(s):
        c = s[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "'\"`":
            quote = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            parts.append((s[start:i], start))
            start = i + 1
        i += 1
    parts.append((s[start:], start))
    return [(p, o) for p, o in parts if p.strip()]


def object_entries(src: str, at: int):
    """[(key, value, offset)] of the object literal whose `{` is at `at`. A spread
    yields key `...`, a shorthand property yields the key as its own value."""
    end = match_close(src, at)
    body = src[at + 1:end]
    out = []
    for piece, off in split_top(body):
        lead = len(piece) - len(piece.lstrip())
        p = piece.strip()
        pos = at + 1 + off + lead
        if p.startswith("..."):
            out.append(("...", p[3:].strip(), pos))
            continue
        m = re.match(r"^(['\"]?)([A-Za-z_$][\w$]*)\1\s*:\s*(.*)$", p, re.S)
        if m:
            out.append((m.group(2), m.group(3).strip(), pos))
        elif re.fullmatch(r"[A-Za-z_$][\w$]*", p):
            out.append((p, p, pos))
        else:
            out.append(("?", p, pos))
    return out


def find_object(src: str, name: str = None) -> int:
    """Offset of the `{` of `export default {…}`, `export const NAME = {…}` or the
    `const NAME = {…}` that `export default NAME` names."""
    if name:
        m = re.search(r"(?:export\s+)?const\s+%s\s*=\s*\{" % re.escape(name), src)
    else:
        m = re.search(r"export\s+default\s*\{", src)
        if not m:
            ref = re.search(r"export\s+default\s+([A-Za-z_$][\w$]*)", src)
            if ref:
                return find_object(src, ref.group(1))
    if not m:
        raise ValueError(f"no object literal for {name or 'export default'}")
    return m.end() - 1


def consts(src: str) -> dict:
    """Top-level `const NAME = <string or number>` and template strings over them."""
    out = {}
    for name, raw in re.findall(r"^const\s+([A-Za-z_$][\w$]*)\s*=\s*(.+?)\s*$", src, re.M):
        if re.fullmatch(r"'[^']*'|\"[^\"]*\"", raw):
            out[name] = raw[1:-1]
        elif re.fullmatch(r"`[^`]*`", raw):
            out[name] = re.sub(r"\$\{(\w+)\}", lambda m: str(out.get(m.group(1), m.group(0))), raw[1:-1])
        elif re.fullmatch(r"-?[0-9.]+", raw):
            out[name] = float(raw)
    return out


def line_of(src: str, pos: int) -> int:
    return src.count("\n", 0, pos) + 1


# ---------------------------------------------------------------- values ----

COLOR = re.compile(r"^(?:#[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?|rgba?\(\s*[0-9.]+\s*,\s*[0-9.]+\s*,\s*[0-9.]+"
                   r"\s*(?:,\s*[0-9.]+\s*)?\))$")


def string(v: str):
    m = re.fullmatch(r"\s*(['\"`])(.*)\1\s*", v, re.S)
    return m.group(2) if m and "${" not in m.group(2) else None


def css_color(c: str) -> str:
    """Canonical CSS for a literal: `#RRGGBB` when opaque, `rgba(r, g, b, a)` otherwise."""
    c = c.strip()
    m = re.fullmatch(r"#([0-9A-Fa-f]{6})([0-9A-Fa-f]{2})?", c)
    if m:
        if not m.group(2) or m.group(2).upper() == "FF":
            return "#" + m.group(1).upper()
        r, g, b = (int(m.group(1)[i:i + 2], 16) for i in (0, 2, 4))
        return f"rgba({r}, {g}, {b}, {round(int(m.group(2), 16) / 255, 3):g})"
    nums = [float(x) for x in re.findall(r"[0-9.]+", c)]
    r, g, b = (int(round(x)) for x in nums[:3])
    a = nums[3] if len(nums) > 3 else 1.0
    return "#%02X%02X%02X" % (r, g, b) if a >= 0.999 else f"rgba({r}, {g}, {b}, {a:g})"


def scaled(v: str, names: dict = None):
    """`scaledDimension(n)`, a bare number, or a const bound to one -> float."""
    v = v.strip()
    m = re.fullmatch(r"scaledDimension\(\s*(-?[0-9.]+)\s*\)", v)
    if m:
        return float(m.group(1))
    if re.fullmatch(r"-?[0-9.]+", v):
        return float(v)
    if names and v in names and isinstance(names[v], float):
        return names[v]
    return None


def px(v: float) -> str:
    return f"{v:g}px"


def args_of(call: str, fn: str):
    m = re.match(r"^\s*%s\s*\(" % re.escape(fn), call)
    if not m:
        return None
    end = match_close(call, m.end() - 1)
    return [a.strip() for a, _ in split_top(call[m.end():end])]


def list_of(v: str):
    v = v.strip()
    if not (v.startswith("[") and v.endswith("]")):
        return None
    return [a.strip() for a, _ in split_top(v[1:-1])]


# ---------------------------------------------------------------- readers ----

class Reader:
    def __init__(self, theme_dir: str):
        self.dir = theme_dir

    def src(self, rel: str) -> str:
        with open(os.path.join(self.dir, rel), encoding="utf-8") as fh:
            return strip_comments(fh.read())

    def gap(self, rel, src, pos, name, literal):
        unparsed.append({"file": rel, "line": line_of(src, pos), "name": name,
                         "literal": " ".join(literal.split())[:160]})


def read_palette(rd: Reader, rel="redesign/palette.js"):
    src = rd.src(rel)
    out, values = [], {}
    for key, val, pos in object_entries(src, find_object(src)):
        lit = string(val)
        if key in ("...", "?") or lit is None or not COLOR.match(lit.strip()):
            rd.gap(rel, src, pos, key, val)
            continue
        css = css_color(lit)
        values[key] = css
        out.append({"name": key, "kind": "color", "value": css, "css": css,
                    "literal": lit, "source": f"{rel}:{line_of(src, pos)}"})
    return out, values


def read_roles(rd: Reader, palette: dict, dark="redesignDark/color.js", light="redesignLight/color.js"):
    """Semantic roles: one token per role, the dark value as the default (the app's
    default theme) and the light value beside it."""
    sides = {}
    for theme, rel in (("dark", dark), ("light", light)):
        src = rd.src(rel)
        vals = {}
        for key, val, pos in object_entries(src, find_object(src)):
            m = re.fullmatch(r"Color\.([A-Za-z_$][\w$]*)", val.strip())
            lit = string(val)
            if m and m.group(1) in palette:
                vals[key] = (palette[m.group(1)], m.group(1), line_of(src, pos))
            elif lit and COLOR.match(lit.strip()):
                vals[key] = (css_color(lit), lit, line_of(src, pos))
            else:
                rd.gap(rel, src, pos, key, val)
        sides[theme] = (rel, vals)
    (drel, d), (lrel, lt) = sides["dark"], sides["light"]
    out = []
    for key in sorted(set(d) | set(lt), key=lambda k: (k not in d, k)):
        if key not in d or key not in lt:
            # The app's own rule: a role on one side only renders `undefined` on the other.
            have, rel = ("dark", drel) if key in d else ("light", lrel)
            unparsed.append({"file": rel, "line": (d.get(key) or lt.get(key))[2], "name": key,
                             "literal": f"role defined for {have} only"})
            continue
        out.append({"name": key, "kind": "role", "value": d[key][0], "css": d[key][0],
                    "dark": d[key][0], "light": lt[key][0],
                    "darkRef": d[key][1], "lightRef": lt[key][1],
                    "source": f"{drel}:{d[key][2]}"})
    return out


def read_dimensions(rd: Reader, rel="redesign/dimensions.js"):
    src = rd.src(rel)
    names = {}
    for name, raw in re.findall(r"^const\s+([A-Za-z_$][\w$]*)\s*=\s*(scaledDimension\([^)]*\))", src, re.M):
        names[name] = scaled(raw)
    spacing, layout = [], []
    for key, val, pos in object_entries(src, find_object(src)):
        v = scaled(val, names)
        if key in ("...", "?") or v is None:
            rd.gap(rel, src, pos, key, val)
            continue
        tok = {"name": key, "value": int(v) if v == int(v) else v, "css": px(v),
               "literal": val, "source": f"{rel}:{line_of(src, pos)}"}
        if re.fullmatch(r"d[0-9]+", key):
            spacing.append({**tok, "kind": "spacing"})
        else:
            layout.append({**tok, "kind": "layout"})
    return spacing, layout


def read_radius(rd: Reader, rel="redesign/borderRadius.js"):
    src = rd.src(rel)
    out = []
    for key, val, pos in object_entries(src, find_object(src)):
        v = scaled(val)
        if key in ("...", "?") or v is None:
            rd.gap(rel, src, pos, key, val)
            continue
        out.append({"name": key, "kind": "radius", "value": int(v) if v == int(v) else v,
                    "css": px(v), "literal": val, "source": f"{rel}:{line_of(src, pos)}"})
    return out


def linear(rel, src, pos, key, val, rd):
    a = args_of(val, "gradient")
    if not a or len(a) < 2:
        return None
    try:
        angle = float(a[0])
    except ValueError:
        return None
    colors = [string(c) for c in (list_of(a[1]) or [])]
    if not colors or any(c is None or not COLOR.match(c.strip()) for c in colors):
        return None
    locs = None
    if len(a) > 2 and a[2] not in ("undefined", "null"):
        try:
            locs = [float(x) for x in list_of(a[2]) or []]
        except (TypeError, ValueError):
            return None
    n = len(colors)
    if not locs or len(locs) != n:
        locs = [0.0] if n == 1 else [i / (n - 1) for i in range(n)]
    stops = [css_color(c) for c in colors]
    pairs = ", ".join(f"{c} {l * 100:g}%" for c, l in zip(stops, locs))
    return {"name": key, "kind": "gradient", "angle": angle, "stops": stops, "locations": locs,
            "css": f"linear-gradient({angle:g}deg, {pairs})", "source": f"{rel}:{line_of(src, pos)}"}


def radial(rel, src, pos, key, val):
    """A list of `{color, opacity, offset}` stops, drawn from the centre out."""
    items = list_of(val)
    if not items:
        return None
    stops = []
    for it in items:
        if not it.startswith("{"):
            return None
        fields = {k: v for k, v, _ in object_entries(it, 0)}
        c = string(fields.get("color", ""))
        try:
            op, off = float(fields.get("opacity", "1")), float(fields["offset"])
        except (KeyError, ValueError):
            return None
        if not c or not COLOR.match(c.strip()):
            return None
        base = css_color(c)
        m = re.fullmatch(r"#([0-9A-F]{2})([0-9A-F]{2})([0-9A-F]{2})", base)
        col = (f"rgba({int(m.group(1), 16)}, {int(m.group(2), 16)}, {int(m.group(3), 16)}, {op:g})"
               if m else base)
        stops.append((col, off))
    pairs = ", ".join(f"{c} {o * 100:g}%" for c, o in stops)
    return {"name": key, "kind": "gradient", "radial": True, "stops": [c for c, _ in stops],
            "locations": [o for _, o in stops], "css": f"radial-gradient(circle, {pairs})",
            "source": f"{rel}:{line_of(src, pos)}"}


def vector(rel, src, pos, key, val):
    """`{colors, locations, start: {x, y}, end: {x, y}}`: a linear gradient along a
    start -> end vector, turned into the CSS angle (0deg points up, 90deg right)."""
    if not val.strip().startswith("{"):
        return None
    f = {k: v for k, v, _ in object_entries(val, 0)}
    colors = [string(c) for c in (list_of(f.get("colors", "")) or [])]
    if not colors or any(c is None or not COLOR.match(c.strip()) for c in colors):
        return None
    try:
        s = {k: float(v) for k, v, _ in object_entries(f["start"], 0)}
        e = {k: float(v) for k, v, _ in object_entries(f["end"], 0)}
        locs = [float(x) for x in (list_of(f["locations"]) if "locations" in f else [])]
    except (KeyError, ValueError):
        return None
    import math
    angle = round(math.degrees(math.atan2(e["x"] - s["x"], -(e["y"] - s["y"]))) % 360, 1)
    n = len(colors)
    if len(locs) != n:
        locs = [0.0] if n == 1 else [i / (n - 1) for i in range(n)]
    stops = [css_color(c) for c in colors]
    pairs = ", ".join(f"{c} {l * 100:g}%" for c, l in zip(stops, locs))
    return {"name": key, "kind": "gradient", "angle": angle, "stops": stops, "locations": locs,
            "css": f"linear-gradient({angle:g}deg, {pairs})", "source": f"{rel}:{line_of(src, pos)}"}


def read_gradients(rd: Reader, rel="redesign/gradients.js"):
    src = rd.src(rel)
    out = []

    def walk(at, prefix=""):
        for key, val, pos in object_entries(src, at):
            if key == "...":
                walk(find_object(src, val))
                continue
            name = prefix + key
            if re.fullmatch(r"[A-Za-z_$][\w$]*", val.strip()) and val.strip() != key:
                try:
                    walk(find_object(src, val.strip()), prefix=key + ".")
                    continue
                except ValueError:
                    pass
            tok = (linear(rel, src, pos, name, val, rd) or radial(rel, src, pos, name, val)
                   or vector(rel, src, pos, name, val))
            if tok is None:
                rd.gap(rel, src, pos, name, val)
            else:
                out.append(tok)
    walk(find_object(src))
    return out


def read_shadows(rd: Reader, rel="redesign/shadows.js"):
    src = rd.src(rel)
    out = []
    for key, val, pos in object_entries(src, find_object(src)):
        a = args_of(val, "shadow")
        y = scaled(a[0]) if a and len(a) >= 3 else None
        r = scaled(a[1]) if a and len(a) >= 3 else None
        c = string(a[2]) if a and len(a) >= 3 else None
        if key in ("...", "?") or None in (y, r, c) or not COLOR.match(c.strip()):
            rd.gap(rel, src, pos, key, val)
            continue
        col = css_color(c)
        # RN's shadowRadius is half the design's blur, so the CSS blur is twice it.
        out.append({"name": key, "kind": "shadow", "color": col, "radius": r,
                    "offsetX": 0.0, "offsetY": y, "css": f"0px {y:g}px {r * 2:g}px {col}",
                    "source": f"{rel}:{line_of(src, pos)}"})
    return out


def light_glow(val):
    """`lightGlow([[offset, 'r, g, b', opacity], ...])` -> [(rgba, offset)], or None."""
    a = args_of(val, "lightGlow")
    items = list_of(a[0]) if a and len(a) == 1 else None
    if not items:
        return None
    stops = []
    for it in items:
        parts = list_of(it)
        m = re.fullmatch(r"\s*['\"`]\s*([0-9]+)\s*,\s*([0-9]+)\s*,\s*([0-9]+)\s*['\"`]\s*",
                         parts[1]) if parts and len(parts) == 3 else None
        if not m:
            return None
        try:
            off, op = float(parts[0]), float(parts[2])
        except ValueError:
            return None
        stops.append((f"rgba({m.group(1)}, {m.group(2)}, {m.group(3)}, {op:g})", off))
    return stops


def read_topic_glows(rd: Reader, rel="redesign/topicGlows.js"):
    """Per-topic glows. Dark: `glow('r, g, b', 'r, g, b')` (core, edge), a colour pair.
    Light: `lightGlow([[offset, 'r, g, b', opacity], ...])`, a radial stop list."""
    src = rd.src(rel)
    out = []
    for obj in ("TOPIC_GLOWS", "TOPIC_GLOWS_LIGHT"):
        try:
            at = find_object(src, obj)
        except ValueError:
            continue
        for key, val, pos in object_entries(src, at):
            cap = key[0].upper() + key[1:]
            lg = light_glow(val) if obj == "TOPIC_GLOWS_LIGHT" else None
            if lg:
                pairs = ", ".join(f"{c} {o * 100:g}%" for c, o in lg)
                out.append({"name": f"topicGlowLight{cap}", "kind": "gradient", "radial": True,
                            "stops": [c for c, _ in lg], "locations": [o for _, o in lg],
                            "css": f"radial-gradient(circle, {pairs})",
                            "source": f"{rel}:{line_of(src, pos)}"})
                continue
            a = args_of(val, "glow")
            rgb = [re.fullmatch(r"\s*['\"`]\s*([0-9]+)\s*,\s*([0-9]+)\s*,\s*([0-9]+)\s*['\"`]\s*", x)
                   for x in (a or [])]
            name = f"{obj}.{key}"
            if not a or len(a) != 2 or not all(rgb):
                rd.gap(rel, src, pos, name, val)
                continue
            css = ["#%02X%02X%02X" % tuple(int(g) for g in m.groups()) for m in rgb]
            out.append({"name": f"topicGlow{cap}", "kind": "colorArray",
                        "value": css, "css": ", ".join(css), "literal": f"[{len(css)} colors]",
                        "source": f"{rel}:{line_of(src, pos)}"})
    return out


def read_fonts(rd: Reader, rel="redesign/fonts.js"):
    src = rd.src(rel)
    names = consts(src)
    families, styles = {}, []
    for key, val, pos in object_entries(src, find_object(src)):
        if not val.strip().startswith("{"):
            rd.gap(rel, src, pos, key, val)
            continue
        fields = {k: v for k, v, _ in object_entries(val, 0) if k != "..."}
        italic = "fontStyle" in val and "italic" in val
        ps = names.get(fields.get("fontFamily", "").strip()) or string(fields.get("fontFamily", "") or "")
        size = scaled(fields.get("fontSize", ""))
        lh = scaled(fields.get("lineHeight", "")) if "lineHeight" in fields else None
        ls = scaled(fields.get("letterSpacing", "")) if "letterSpacing" in fields else None
        face = FACES.get(ps or "")
        if face is None or size is None or ("lineHeight" in fields and lh is None):
            rd.gap(rel, src, pos, key, val)
            continue
        fam, weight, style = face
        style = "italic" if italic or style == "italic" else "normal"
        families[ps] = fam
        line = f"{lh:g}px" if lh else "normal"
        styles.append({
            "name": key, "kind": "fontStyle", "family": fam, "face": ps, "size": size,
            "lineHeight": lh, "letterSpacing": ls, "cssWeight": weight, "italic": style == "italic",
            "css": f"{'italic ' if style == 'italic' else ''}{weight} {size:g}px/{line} {STACK[fam]}",
            "literal": " ".join(val.split())[:160], "source": f"{rel}:{line_of(src, pos)}"})
    fams = [{"name": ps, "kind": "fontFamily", "value": ps, "css": f'"{ps}"',
             "literal": ps, "source": rel} for ps in sorted(families)]
    return fams, styles


def extract(theme_dir: str, skip=()):
    """Every token the theme folder defines, and the gaps, in the shared schema."""
    unparsed.clear()
    rd = Reader(theme_dir)
    palette, values = read_palette(rd)
    roles = read_roles(rd, values)
    spacing, layout = read_dimensions(rd)
    radius = read_radius(rd)
    gradients = read_gradients(rd)
    shadows = read_shadows(rd)
    glows = read_topic_glows(rd)
    fams, fonts = read_fonts(rd)
    # A file in the theme folders that no reader opened is a gap of its own: a new
    # token module would otherwise ship nothing and say nothing.
    for folder in sorted({os.path.dirname(f) for f in READ}):
        for name in sorted(os.listdir(os.path.join(theme_dir, folder))):
            rel = f"{folder}/{name}"
            if name.endswith(".js") and rel not in READ and rel not in skip:
                unparsed.append({"file": rel, "line": 0, "name": rel,
                                 "literal": "no reader for this file", "gap": "no parser reads this file"})
    return {"color": palette, "role": roles,
            "colorArray": [g for g in glows if g["kind"] == "colorArray"],
            "spacing": spacing, "layout": layout, "radius": radius,
            "gradient": gradients + [g for g in glows if g["kind"] == "gradient"],
            "shadow": shadows, "fontFamily": fams, "fontStyle": fonts}, list(unparsed)


READ = ("redesign/palette.js", "redesignDark/color.js", "redesignLight/color.js",
        "redesign/dimensions.js", "redesign/borderRadius.js", "redesign/gradients.js",
        "redesign/shadows.js", "redesign/topicGlows.js", "redesign/fonts.js")
