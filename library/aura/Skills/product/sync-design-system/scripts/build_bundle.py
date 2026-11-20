#!/usr/bin/env python3
"""Build an app's design-system bundle for Claude Design.

Three sources, none of them hand-copied:
  tokens.json            <- extract_tokens.py, from the app's Swift theme files
  screenshots/*.png      <- the sim-test capture store
  BRAND_BY_APP below     <- AI Knowledge: Identity/Brand and Identity/Apps/<app>

Everything under the output dir is generated. To change the system, change the
app or the knowledge base and re-run — never edit the output.

    python3 build_bundle.py --app aurie --out DIR
"""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import urllib.request
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from apps import resolve  # noqa: E402
import stops  # noqa: E402

OUT = ""    # set from --out in main()
UPLOAD_GLOBS = ("README.md", "tokens.css", "tokens.json", "_adherence.oxlintrc.json",
                "_ds_manifest.json", "preview/*.html", "fonts/*.otf", "uploads/*.png")
REPO = ""   # set from --app in main()
SHOTS = ""
CFG: dict = {}

# Windows consoles default to cp1252; this module prints em-dashes, arrows and
# whatever non-ASCII shows up in an unparsed Swift literal. Without this a run
# dies on UnicodeEncodeError rather than on anything to do with the design system.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

CARDS: list = []   # every card() call, for _ds_manifest.json

# --------------------------------------------------------------- knowledge ---
# Sourced from Notion. Each block names its page so a reader can go check it.
# Keyed by app so a second app cannot silently inherit this one's voice.
BRAND_BY_APP = {
  "aurie": {
    "source_identity": "AI Knowledge › Identity › Brand",
    "source_app": "AI Knowledge › Identity › Apps › Aurie",
    "promise": "Feel lighter today.",
    "is": "Aurie is an emotional processing guide. It combines emotional attunement "
          "with structured guidance to create real internal shifts — then steps back. "
          "Its strength is intentional structure, precise timing, and clean endings.",
    "is_not": [
        "A therapist or a replacement for therapy",
        "A companion designed to form emotional attachment or reliance",
        "A productivity coach or accountability partner",
        "A chatbot optimized for endless conversation",
        "Generic self-care, journaling, or a wellness routine",
    ],
    "philosophy": [
        ("Emotional healing, not escapism",
         "Aurie helps users process emotions, not avoid them."),
        ("Connection over codependency",
         "Not optimised for users to bond with the AI — it encourages reconnection "
         "with self, community, and purpose."),
        ("Behaviour change rooted in grace",
         "Nudges are soul-based, not shame-based. Never engineered to drive addiction "
         "or time-in-app."),
    ],
    "voice": [
        ("Warm and empathetic", "I'm here to help you rediscover your light"),
        ("Non-judgmental", "accepts where you are without pushing"),
        ("Grounded", "rooted in therapeutic expertise without being clinical"),
        ("Invitational", "Your light is worth tending. Shall we begin?"),
        ("Supportive", "Let me send gentle support right when you need it"),
    ],
    "taglines": [
        ("Hero", "Feel lighter today"),
        ("Desktop hero", "Feel lighter … in minutes."),
        ("Subscription", "Feel understood. Feel supported. Feel lighter."),
        ("Welcome", "I'm here to help you rediscover your light"),
        ("Closing", "Your light is worth tending. Shall we begin?"),
    ],
    "principles": [
        ("Specific over abstract", "Concrete sensory details beat wellness abstractions."),
        ("Outcome over feature", "What the person will feel, not what the app does."),
        ("Low commitment over aspirational",
         "“Try it for 3 minutes” beats “Start your wellness journey.”"),
        ("Honest over hype", "No miracle claims. No “cure” language."),
        ("Invitational over imperative",
         "“If you'd like…” beats “You must…”"),
    ],
    "banned": ["Journey", "Unlock", "Transform / transformation", "Self-care",
               "Holistic", "Wellness journey", "In today's busy world…",
               "We all struggle with…", "Therapist in your pocket"],
    "cta_good": ["Try it free", "Just listen", "Start a 3-minute session"],
    "cta_bad": ["Change your life", "Transform how you feel forever",
                "Begin your Aura journey today"],
    "naming": [
        ("Aura", "The meditation, sleep and CBT app — or the parent company."),
        ("Aurie", "The AI emotional companion. Always <em>Aurie</em> — never "
                  "“Aura AI”, “Aura Companion” or “Aura+”."),
        ("Both", "Write “Aura and Aurie”, in that order."),
        ("Company", "“Aura Health” or “Aura Health, Inc.”"),
    ],
    "modes": [
        ("Quick Relief", "10–90 seconds",
         "Anxiety spikes, late-night vulnerability. Near-zero effort.",
         "“I'm steadier. I can continue.”"),
        ("Quick Processing", "2–5 minutes",
         "Max 3 user turns. Name what's present → locate in body → soften → "
         "identify need → close with a deliverable.",
         "“I'm less stuck.”"),
        ("Go Deeper", "8–15 minutes",
         "Invited, earned, explicitly consented. Strong ending and cooldown.",
         "“That shifted something real.”"),
    ],
    "loop": ["Trigger — something feels off",
             "Downshift — reduce activation",
             "Small Clarity — name what's present without rumination",
             "Integration — let it settle into body and memory",
             "Return — end cleanly and re-enter life"],
  },
}

DOMAIN_RULES = [
    ("Paywall", ("paywall", "pricing", "package", "subscription", "cancellation",
                 "unsubscribe", "payment", "plans", "chatlimit", "usagelimit",
                 "usagewarning")),
    ("Onboarding", ("onboarding", "welcome", "signup", "signin", "splash", "agegate",
                    "selfcommitment", "quiz", "valueprops", "didyouknow", "tutorial",
                    "promptresults", "archetype", "supportstyle", "usecases", "intro")),
    ("Conversation", ("conversation", "chat", "livesession", "voice", "quickprocessing",
                      "qp", "crisis", "incognito")),
    ("Journal", ("journal",)),
    ("Grounding", ("grounding", "meditation", "player", "prayer", "practice", "track")),
    ("Affirmations", ("affirmation", "bookmark")),
    ("Profile & Settings", ("profile", "about", "settings", "preference", "memory",
                            "notification")),
    ("Home & Feed", ("home", "feed", "fortoday", "checkin", "reflection",
                     "dailypractices")),
    ("Growth & Feedback", ("rating", "pmf", "survey", "review", "referral", "invite",
                           "share")),
]


def domain_of(name):
    n = re.sub(r"[^a-z0-9]", "", name.lower())
    for dom, keys in DOMAIN_RULES:
        if any(k in n for k in keys):
            return dom
    return "Other"


# ------------------------------------------------------------------ helpers --

def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _figma_cell():
    """The design file, for the README's source-of-truth table.

    Upstream of this whole bundle: Figma decides, the app mirrors, the generator
    reads the app. Naming it without linking it — which is what every page here
    used to do — leaves the reader to go and find it.
    """
    f = CFG.get("figmaFile")
    if not f:
        return "Figma — file not identified for this app"
    return f"[Figma — {f['name']}]({f['url']}) ({f['owner']}'s)"


def _figma_link_name():
    """The design file linked by its own name — for prose already naming it."""
    f = CFG.get("figmaFile")
    if not f:
        return CFG["displayName"]
    return f'<a href="{f["url"]}">{f["name"].replace(" - ", " — ")}</a>'


def _figma_note():
    """The design file as an HTML link, for a card body."""
    f = CFG.get("figmaFile")
    if not f:
        return "Figma"
    return f'<a href="{f["url"]}">Figma</a>'


def card(path, group, name, subtitle, viewport, body, extra_css=""):
    """Write one preview card with the @dsCard marker the pane indexes on."""
    html = f"""<!doctype html>
<!-- @dsCard group="{group}" name="{esc(name)}" subtitle="{esc(subtitle)}" viewport="{viewport}" -->
<html><head><meta charset="utf-8"><title>{esc(name)}</title>
<link rel="stylesheet" href="../tokens.css">
<style>
  body {{ margin:0; padding:28px 32px; background:#FBFBFC; color:#222222;
         font-family:-apple-system, 'SF Pro Text', system-ui, sans-serif;
         font-size:13px; line-height:1.5; }}
  h2 {{ font-size:13px; font-weight:600; letter-spacing:.06em; text-transform:uppercase;
       color:#8A8F98; margin:0 0 14px; }}
  .note {{ font-size:11px; color:#8A8F98; margin-top:18px; line-height:1.6; }}
  code {{ font-family:ui-monospace, SFMono-Regular, Menlo, monospace; font-size:11px; }}
{extra_css}
</style></head>
<body>
{body}
</body></html>
"""
    CARDS.append({"path": path, "group": group, "viewport": viewport,
                  "subtitle": subtitle, "name": name})
    full = os.path.join(OUT, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as fh:
        fh.write(html)


def by_kind(tokens, kind):
    return [t for t in tokens if t["kind"] == kind]


def var_of(token, prefix):
    n = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Za-z])(?=[0-9])", "-", token["name"]).lower()
    for dup in (prefix, "border-radius", "font"):
        if n.startswith(dup + "-"):
            n = n[len(dup) + 1:]
            break
    return f"--{prefix}-{n}"


# -------------------------------------------------------------------- cards --

def color_cards(tokens):
    solid = [t for t in by_kind(tokens, "color") if t.get("alpha", 1) >= 0.999]
    alpha = [t for t in by_kind(tokens, "color") if t.get("alpha", 1) < 0.999]
    arrays = by_kind(tokens, "colorArray")

    def swatches(rows):
        out = ['<div class="grid">']
        for t in rows:
            out.append(
                f'<div class="sw"><div class="chip" style="background:{t["css"]}"></div>'
                f'<div class="nm">{esc(t["name"])}</div>'
                f'<div class="hx"><code>{esc(t["css"])}</code></div>'
                f'<div class="src">{esc(t["source"])}</div></div>')
        out.append("</div>")
        return "\n".join(out)

    css = """
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(148px,1fr));gap:14px}
  .chip{height:52px;border-radius:8px;border:1px solid rgba(0,0,0,.08);
        background-image:linear-gradient(45deg,#eee 25%,transparent 25%,transparent 75%,#eee 75%),
                         linear-gradient(45deg,#eee 25%,transparent 25%,transparent 75%,#eee 75%);
        background-size:10px 10px;background-position:0 0,5px 5px}
  .nm{font-weight:500;margin-top:7px;font-size:12px}
  .hx{color:#5C6370;font-size:11px}
  .src{color:#A8AEB8;font-size:10px;margin-top:2px}
"""
    card("preview/color-solid.html", "Color", "Color — Solid",
         f"{len(solid)} opaque tokens", "900x620",
         f"<h2>Solid colors — {len(solid)} tokens</h2>{swatches(solid)}"
         '<p class="note">Generated from <code>Colors.swift</code>. These are the value layer — '
         'on a sky surface, name the role instead. See '
         '<em>Color — the semantic layer</em>.</p>', css)

    card("preview/color-alpha.html", "Color", "Color — Alpha",
         f"{len(alpha)} translucent tokens", "900x620",
         f"<h2>Translucent colors — {len(alpha)} tokens</h2>{swatches(alpha)}"
         '<p class="note">Checkerboard shows through the alpha channel.</p>', css)

    rows = []
    for t in arrays:
        stops = t["value"]
        grad = f"linear-gradient(90deg, {', '.join(stops)})"
        rows.append(f'<div class="sw"><div class="chip" style="background:{grad}"></div>'
                    f'<div class="nm">{esc(t["name"])}</div>'
                    f'<div class="hx"><code>{esc(", ".join(stops))}</code></div>'
                    f'<div class="src">{esc(t["source"])}</div></div>')
    card("preview/color-pairs.html", "Color", "Color — Fade pairs",
         "Shadow and placeholder stop pairs", "900x260",
         f'<h2>Fade pairs — {len(arrays)} tokens</h2><div class="grid">{"".join(rows)}</div>'
         '<p class="note">Declared as <code>[Color]</code> arrays and consumed as '
         'gradient stops, not as single colors.</p>', css)


# The sky is carried by hand, and the cost of that is countable. Measured at
# build time for the same reason the adherence and accessibility rows are: a
# number written into a card is wrong by the next sprint, and this one moves
# every time someone adds a screen.
SKY_REF = re.compile(r"\bSkyState\b|\bskyState\b")
SKY_FLAG = re.compile(r"prefersLightForeground")
# A `?` directly after the flag is a ternary and nothing else: the property is a
# non-optional Bool, so it can be neither an optional type nor optional chaining.
# Matching any later `?` on the line counted `Bool?` and `foo?.bar` as branches,
# and unanchored `if`/`guard` matched inside `modifier` and `specific`.
SKY_BRANCH = re.compile(
    r"prefersLightForeground\s*\?|\b(?:if|guard)\b[^\n]*prefersLightForeground")


def sky_roles():
    """The sky-resolving colour roles the app declares, read from `Colors.swift`.

    Measured rather than described, like the census below: the roles carry a
    per-sky opacity for the secondary and tertiary levels — the least that
    clears the contrast bar on that sky's worst gradient stop — and those
    numbers move when design corrects them.
    """
    path = os.path.join(REPO, "AuraConsumerApp/Services/Theme/Global/Colors.swift")
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        return {"roles": [], "opacity": {}}
    roles = sorted(set(re.findall(r"var ((?:text|figure)[A-Z]\w*): Color", text)))
    opacity = {}
    for level in ("secondary", "tertiary"):
        block = re.search(rf"var {level}Opacity: Double {{(.+?)\n    }}", text, re.S)
        if not block:
            continue
        for sky, value in re.findall(r"case \.(\w+): return ([\d.]+)", block.group(1)):
            opacity.setdefault(sky, {})[level] = float(value)
    return {"roles": roles, "opacity": opacity}


def sky_census():
    """How much of the app is branching on the sky by hand.

    `branches` are the ternaries that pick a colour — `prefersLightForeground ?
    .white : Colors.blackSecondary` — each one a place a sky-aware role would
    replace. `handoffs` are the rest: the flag passed down as `isDarkMode:`,
    plumbing that a role also removes.
    """
    files = branches = handoffs = 0
    for root, dirs, names in os.walk(os.path.join(REPO, "AuraConsumerApp")):
        dirs[:] = [d for d in dirs if d not in (".build", "DerivedData")]
        for name in names:
            if not name.endswith(".swift"):
                continue
            # `Colors.swift` is where the roles are *defined*; every mention of
            # the flag in it is the semantic layer itself, not a surface
            # carrying the sky by hand.
            if name == "Colors.swift":
                continue
            try:
                text = open(os.path.join(root, name), encoding="utf-8").read()
            except (OSError, UnicodeDecodeError):
                continue
            # Either signal counts. Four files take the flag as a parameter and
            # never name `skyState`, so gating on that alone lost 20 of its own
            # plumbing lines — the very thing the handoff count is measuring.
            if not (SKY_REF.search(text) or SKY_FLAG.search(text)):
                continue
            files += 1
            for line in text.split("\n"):
                if not SKY_FLAG.search(line):
                    continue
                if SKY_BRANCH.search(line):
                    branches += 1
                else:
                    handoffs += 1
    return {"files": files, "branches": branches, "handoffs": handoffs}


def semantic_layer_card(tokens):
    """The semantic layer: decided in Figma, and how much of it the app mirrors.

    `product:designer` reads this before it chooses a colour, so the card has to
    say both halves — the roles that resolve in code, and the ones still only in
    Figma. Both the roles and the per-sky opacities are read from the app, and
    the remaining hand-written branches counted, for the same reason the
    adherence and accessibility rows are measured: the card falls out of date on
    its own otherwise.
    """
    c = sky_census()
    r = sky_roles()
    order = ["dawn", "morning", "midday", "afternoon", "dusk", "night"]
    rows = "".join(
        f"<tr><td>{sky.title()}</td>"
        f"<td class=\"n\">{round(r['opacity'][sky]['secondary'] * 100)}%</td>"
        f"<td class=\"n\">{round(r['opacity'][sky]['tertiary'] * 100)}%</td></tr>"
        for sky in order if sky in r["opacity"]
    )
    card("preview/color-semantic-layer.html", "Color", "Color — the semantic layer",
         f"{len(r['roles'])} roles resolving per sky", "760x620", f"""
<h2>Decided in Figma, mirrored in code</h2>
<p><strong>{_figma_link_name()}</strong> carries an <code>Environment</code> variable collection with
<strong>seven modes</strong>: Default, Dawn, Morning, Midday, Afternoon, Dusk, Night. Roles
resolve per mode, and the app mirrors them on <code>SkyState</code>:</p>
<table class="roles">
  <tr><td><code>BG/</code></td><td>Top · Mid · Bottom</td>
      <td class="yes">mirrored — <code>SkyState.stops</code></td></tr>
  <tr><td><code>Text/</code></td><td>Primary · Secondary · Tertiary · Inverted</td>
      <td class="yes">mirrored — <code>skyState.textPrimary</code>…</td></tr>
  <tr><td><code>Figure/</code></td><td>Primary – Quaternary</td>
      <td class="yes">mirrored — <code>skyState.figurePrimary</code>…</td></tr>
  <tr><td><code>Visible/</code></td><td>Light · Dark</td>
      <td class="no">not mirrored</td></tr>
</table>
<p>Glass, scrims, borders and feedback colours get Environment roles too — being designed.</p>

<h2 style="margin-top:22px">The opacity is the measurement</h2>
<p>Hue is the same on every sky — white on dusk and night, <code>#1C1B1F</code> on the daytime
skies. What moves per mode is the opacity of the secondary and tertiary levels: the least that
clears the contrast bar (4.5:1 and 3:1) on the <em>worst</em> of that sky's three gradient
stops, so a role cannot be applied to a sky it fails on.</p>
<table class="cost">
  <tr><th></th><th class="n">Secondary</th><th class="n">Tertiary</th></tr>
  {rows}
</table>
<p class="note">Two stops were moved rather than weakening a role to fit them: dawn's top
(<code>#2E3D6E</code> → <code>#8092CA</code>) and dusk's horizon
(<code>#8C6980</code> → <code>#705466</code>).</p>

<h2 style="margin-top:22px">What is left</h2>
<table class="cost">
  <tr><td class="n">{c['branches']}</td><td>colour branches still written by hand
      (<code>prefersLightForeground ? … : …</code>) — none of them a colour role: colour
      schemes, rendering modes, opacities, a control-flow guard, and foreground pairs no
      role covers</td></tr>
  <tr><td class="n">{c['handoffs']}</td><td>other lines naming the flag — components that
      take it as a parameter, and the deliberate overrides where a surface under a sky-aware
      ancestor is not itself on the sky</td></tr>
</table>

<h2 style="margin-top:22px">Choosing a colour</h2>
<p><strong>Name the role, not the value.</strong> On a sky surface reach for
<code>skyState.textPrimary</code> / <code>textSecondary</code> / <code>figurePrimary</code>;
the measurement is already in it. The value-named tokens below it are the palette the roles
resolve to, not the thing to apply. A colour chosen on one sky is still not chosen — the
roles handle the six skies, anything else you pick does not.</p>
<p class="note">{_figma_note()} is the source of truth for palette, gradients and icons; the app
mirrors Figma, and this system is generated from the app. The roles, their per-sky opacities
and the counts above are read from <code>AuraConsumerApp/**/*.swift</code> at build time.</p>
""", extra_css="""
  table.roles, table.cost{border-collapse:collapse;font-size:12px}
  table.roles td, table.cost td, table.cost th{padding:3px 16px 3px 0;vertical-align:top}
  table.cost th{font-weight:500;color:#6B7280;text-align:right}
  td.yes{color:#1E8E5A;font-weight:600}
  td.no{color:#C0392B;font-weight:600}

  td.n{font-weight:600;font-size:15px;text-align:right;width:44px}
""")


# The accessibility bar is NOT defined here. It lives on one Notion page —
# Design/Accessibility Baseline — which is also what humans read and what
# .claude/rules/accessibility.md points at. A copy in code would be a second
# source that silently disagrees the first time someone edits one of them.
#
# The page carries a JSON code block beside the human-readable table; we read
# that. An app with no `a11yBaseline` in apps.py simply gets no accessibility
# cards, which is the right behaviour for an app whose bar has not been agreed.
A11Y_KEYS = ("textContrast", "largeTextContrast", "largeTextDef", "nonTextContrast",
             "targetApple", "targetMaterial", "targetWcagAA", "minText", "scaling")


def notion_token():
    tok = os.environ.get("NOTION_TOKEN")
    if tok:
        return tok
    for p in (os.path.expanduser("~/.claude/settings.json"),):
        try:
            tok = json.load(open(p)).get("env", {}).get("NOTION_TOKEN", "")
            if tok:
                return tok
        except (OSError, ValueError):
            pass
    return ""


def fetch_a11y(page_id):
    """Read the bar from Design/Accessibility Baseline.

    Fails loudly. A silent fallback to hardcoded numbers is exactly the drift
    this function exists to remove: the cards would keep rendering, quietly
    citing a bar nobody agreed to.
    """
    tok = notion_token()
    if not tok:
        sys.exit("NOTION_TOKEN is not set, and this app's accessibility bar lives in Notion "
                 "(Design/Accessibility Baseline). Set it, or drop `a11yBaseline` from apps.py "
                 "to build without the accessibility cards.")
    req = urllib.request.Request(
        f"https://api.notion.com/v1/blocks/{page_id}/children?page_size=100",
        headers={"Authorization": f"Bearer {tok}", "Notion-Version": "2022-06-28"})
    try:
        blocks = json.load(urllib.request.urlopen(req)).get("results", [])
    except Exception as e:                                   # noqa: BLE001 - reported verbatim
        sys.exit(f"could not read the accessibility baseline page {page_id}: {e}")
    for b in blocks:
        if b.get("type") != "code":
            continue
        raw = "".join(x.get("plain_text", "") for x in b["code"].get("rich_text", []))
        try:
            bar = json.loads(raw)
        except ValueError as e:
            sys.exit(f"the accessibility baseline page has a code block that is not valid JSON: {e}")
        missing = [k for k in A11Y_KEYS if k not in bar]
        if missing:
            sys.exit(f"the accessibility baseline is missing key(s): {', '.join(missing)}")
        return bar
    sys.exit(f"no JSON code block on the accessibility baseline page {page_id} — "
             "the generator reads the bar from there, see its 'Machine-readable' section.")


def accessibility_cards(tokens, A11Y):
    """The bar, and where this app currently misses it.

    Both numbers are computed from the token manifest rather than written down, so
    the card cannot quietly go stale: raise the small type and the violation row
    disappears on the next build instead of lingering as a stale claim.
    """
    styles = by_kind(tokens, "fontStyle")
    small = sorted((t for t in styles if (t.get("size") or 99) < A11Y["minText"]),
                   key=lambda t: (t.get("size"), t["name"]))
    rows = "".join(
        f"<tr><td><code>{esc(t['name'])}</code></td><td>{t.get('size'):g} pt</td></tr>"
        for t in small)
    # A named exception, when the bar carries one. Rendered from the baseline
    # rather than written here, for the same reason the floor is: two copies of
    # a rule disagree the first time someone edits one.
    floor_note = A11Y.get("minTextDecorativeNote")
    decorative = (
        f"<p class='note'><strong>Exception — decorative text down to "
        f"{A11Y['minTextDecorative']} pt.</strong> {esc(floor_note)}</p>"
        if A11Y.get("minTextDecorative") and floor_note else "")
    dec_suffix = (f" <span class='exc'>({A11Y['minTextDecorative']} pt decorative)</span>"
                  if A11Y.get("minTextDecorative") else "")
    breach = (f"""
<h2>Where this app misses the bar</h2>
<p><strong>{len(small)} type token(s) sit below the {A11Y['minText']} pt floor.</strong>
Apple's guidance is that text below this is not legible at a typical viewing distance
without zooming.</p>
<table>{rows}</table>
<p>Raise them and accept the reflow, or keep them and record the exception — but decide.
A token below the floor that nobody has ruled on is the state that ships.</p>{decorative}""" if small
        else "<h2>Where this app misses the bar</h2><p>No type token is below the "
             f"{A11Y['minText']} pt floor.</p>{decorative}")

    card("preview/a11y-bar.html", "Accessibility", "Accessibility — the bar",
         "Minimums every screen must clear", "820x760", f"""
<h2>Every screen must clear these</h2>
<table>
  <tr><th>Rule</th><th>Minimum</th><th>Where it comes from</th></tr>
  <tr><td>Body text contrast</td><td><strong>{A11Y['textContrast']}</strong></td>
      <td>WCAG 1.4.3 AA; Apple and Material concur</td></tr>
  <tr><td>Large text contrast</td><td><strong>{A11Y['largeTextContrast']}</strong>
      <span class="q">({A11Y['largeTextDef']})</span></td>
      <td>WCAG defines large; Apple gives the ratio but never defines it</td></tr>
  <tr><td>Non-text: icons, borders, focus, states</td>
      <td><strong>{A11Y['nonTextContrast']}</strong></td><td>WCAG 1.4.11 AA</td></tr>
  <tr><td>Tap target — iOS</td><td><strong>{A11Y['targetApple']}</strong></td>
      <td>Apple's floor. Pad with <code>.contentShape</code> when the visual is smaller</td></tr>
  <tr><td>Tap target — Android</td><td>{A11Y['targetMaterial']}</td>
      <td>Material's floor, larger than Apple's</td></tr>
  <tr><td>Minimum text size</td><td><strong>{A11Y['minText']} pt</strong>{dec_suffix}</td>
      <td>Apple. Material states none</td></tr>
  <tr><td>Text scaling</td><td><strong>{A11Y['scaling']}</strong> without loss</td>
      <td>WCAG 1.4.4 AA — Dynamic Type via <code>relativeTo:</code></td></tr>
  <tr><td>Colour alone</td><td><strong>never the only signal</strong></td>
      <td>WCAG 1.4.1, Level <strong>A</strong> — the easiest one, and the one most missed</td></tr>
</table>
<p class="note">WCAG AA asks only {A11Y['targetWcagAA']} for a target — roughly a quarter of
Apple's area. Passing AA is not passing this bar.</p>
{breach}
""", """
  table {{ border-collapse:collapse; width:100%; font-size:12px; }}
  th {{ text-align:left; font-weight:600; color:#8A8F98; font-size:10px;
       text-transform:uppercase; letter-spacing:.06em; padding:6px 10px 6px 0; }}
  td {{ padding:6px 10px 6px 0; border-top:1px solid #EDEFF2; vertical-align:top; }}
  .q {{ color:#8A8F98; }}
  .exc {{ color:#8A8F98; font-weight:400; }}
""".replace("{{","{").replace("}}","}"))

    card("preview/a11y-sky.html", "Accessibility", "Accessibility — the sky rule",
         "Contrast is measured in every sky", "820x520", """
<h2>A colour is only legible if it is legible in every sky</h2>
<p>Aurie is locked to light mode but reads the time-of-day sky
(<code>SkyState</code>), so <strong>dusk and night are its dark mode</strong>. A token that
passes at midday and fails at night has failed.</p>
<p>Measure against the <strong>worst case</strong>, and behind any blur or scrim that sits over
the colour — not against the flat token on white.</p>
<h2>This is not hypothetical</h2>
<p>Four contrast bugs shipped because the check was done on one sky: the Quick Processing intro
sheet, the home sheets (dark text on dark glass), the PMF survey sheet, and the sign-up password
rules at <strong>1.6:1</strong> on the night sky.</p>
<p>It is why the capture store keeps <strong>three pictures per screen</strong> — anytime,
light and dark. Apple asks for the same in its own words: test in both light and dark modes,
with Bold Text, Increase Contrast and Reduce Transparency enabled.</p>
<p class="note">The <code>Text/</code> and <code>Figure/</code> roles now carry this: each one's
per-sky opacity is the least that clears the bar on that sky's worst gradient stop, so naming the
role applies the measurement. See <em>Color — the semantic layer</em>.</p>
""")

# ------------------------------------------------------- token adherence census

# A design system that lists its tokens says what the app *could* use. What it
# actually uses is a different number, and the gap is the finding: a scale that
# nobody reaches for is not the scale on screen, whatever the token file says.
# Counted from the app at build time so the card self-clears as the codebase
# converges, the same way the accessibility violation row does.
RADIUS_LITERAL = re.compile(r"cornerRadius[:( ]\s*([0-9]+(?:\.[0-9]+)?)")
RADIUS_TOKEN = re.compile(r"\bborderRadius[A-Za-z0-9]")
HEX_LITERAL = re.compile(r'Color\(hex:\s*"(#[0-9A-Fa-f]{3,8})"')
COLOR_TOKEN = re.compile(r"\bColors\.[A-Za-z][A-Za-z0-9]*")


def norm_number(raw):
    """A numeric literal as one canonical string, so `16` and `16.0` are one row.

    Swift writes both, and comparing the captured text would split a value in
    two and report the declared step as "not a token".
    """
    try:
        v = float(raw)
    except ValueError:
        return raw
    return str(int(v)) if v == int(v) else str(round(v, 2))


def literal_census():
    """Token uses vs hard-coded literals across the app's Swift, per kind.

    Swift only, and only inside the app target — the theme files themselves are
    where the literals are supposed to live, so they are excluded.
    """
    theme = os.path.join(REPO, CFG["themeDir"])
    radius_lit, radius_tok, hex_lit, color_tok = [], 0, [], 0
    for root, dirs, files in os.walk(os.path.join(REPO, "AuraConsumerApp")):
        dirs[:] = [d for d in dirs if d not in (".build", "DerivedData")]
        if os.path.abspath(root).startswith(os.path.abspath(theme)):
            continue
        for name in files:
            if not name.endswith(".swift"):
                continue
            try:
                text = open(os.path.join(root, name), encoding="utf-8").read()
            except (OSError, UnicodeDecodeError):
                continue
            radius_lit += [norm_number(v) for v in RADIUS_LITERAL.findall(text)]
            radius_tok += len(RADIUS_TOKEN.findall(text))
            hex_lit += HEX_LITERAL.findall(text)
            color_tok += len(COLOR_TOKEN.findall(text))
    return {
        "radius": {"literals": radius_lit, "tokenUses": radius_tok},
        "color": {"literals": [h.upper() for h in hex_lit], "tokenUses": color_tok},
    }


def adherence_card(tokens):
    """What the app reaches for, against what the token file offers."""
    c = literal_census()
    scale = sorted(float(t["value"]) for t in by_kind(tokens, "radius")
                   if str(t["value"]).replace(".", "").isdigit() and float(t["value"]) < 1000)
    in_scale = {norm_number(v) for v in scale}
    r, col = c["radius"], c["color"]
    rl, cl = r["literals"], col["literals"]
    top_r = Counter(rl).most_common(6)
    top_c = Counter(cl).most_common(4)
    rows = "".join(
        f"<tr><td><code>{esc(v)}</code></td><td>{n}</td>"
        f"<td>{'a token' if v in in_scale else '<strong>not a token</strong>'}</td></tr>"
        for v, n in top_r)
    hexes = "".join(f"<tr><td><code>{esc(h)}</code></td><td>{n}</td></tr>" for h, n in top_c)
    r_share = round(100 * r["tokenUses"] / max(1, r["tokenUses"] + len(rl)))
    c_share = round(100 * col["tokenUses"] / max(1, col["tokenUses"] + len(cl)))
    card("preview/token-adherence.html", "Scale", "Tokens — used, or just declared",
         "Measured from the app, not from the token file", "760x560", f"""
<h2>What the app reaches for</h2>
<table class="ad">
  <tr><th></th><th>token uses</th><th>literals</th><th>distinct literals</th><th>token share</th></tr>
  <tr><td>Radius</td><td>{r['tokenUses']}</td><td>{len(rl)}</td>
      <td>{len(set(rl))}</td><td class="{'bad' if r_share < 50 else 'ok'}">{r_share}%</td></tr>
  <tr><td>Colour</td><td>{col['tokenUses']}</td><td>{len(cl)}</td>
      <td>{len(set(cl))}</td><td class="{'bad' if c_share < 50 else 'ok'}">{c_share}%</td></tr>
</table>
<p>Colour has a token culture; radius does not. The radius scale declares
{len(scale)} steps and the app writes the number instead
{'most' if r_share < 50 else 'some'} of the time.</p>
<h2 style="margin-top:22px">The radii actually on screen</h2>
<table class="ad"><tr><th>value</th><th>uses</th><th></th></tr>{rows}</table>
<h2 style="margin-top:22px">The hexes written by hand</h2>
<table class="ad"><tr><th>value</th><th>uses</th></tr>{hexes}</table>
<p class="note">Counted over <code>AuraConsumerApp/**/*.swift</code> at build time, excluding
the theme files themselves. A value that is used often and is not a token is a candidate
step, not a mistake — which of them are real steps is a design decision. Very large radii
(<code>1250</code>, <code>1041.66</code>) are Figma's way of writing “fully rounded”, where
the app means <code>borderRadiusFull</code> or <code>Capsule()</code>.</p>
""", extra_css="""
  table.ad{border-collapse:collapse;font-size:12px}
  table.ad th{text-align:left;font-weight:600;color:#8A8F98;font-size:11px;
              padding:0 18px 6px 0;white-space:nowrap}
  table.ad td{padding:3px 18px 3px 0;vertical-align:top}
  td.bad{color:#C0392B;font-weight:600}
  td.ok{color:#1E8E5A;font-weight:600}
""")


def type_cards(tokens):
    fams = [t for t in tokens if t["kind"] == "fontFamily"]
    styles = [t for t in tokens if t["kind"] == "fontStyle"]
    ui = [t for t in styles if t.get("family") == "SF Pro"]
    disp = [t for t in styles if t.get("family") == "Display"]

    css = """
  .row{display:flex;align-items:baseline;gap:20px;padding:7px 0;border-bottom:1px solid #EDEFF2}
  .row:last-child{border-bottom:none}
  .tag{font-size:10px;font-weight:500;letter-spacing:.07em;text-transform:uppercase;
       color:#A8AEB8;width:190px;flex:0 0 190px}
  .samp{color:#222}
  .warn{background:#FFF6E5;border:1px solid #F2D9A0;border-radius:8px;padding:12px 14px;
        margin-bottom:18px;font-size:12px;color:#6B4E12}
"""
    rows = []
    for t in sorted(ui, key=lambda x: -x["size"]):
        rows.append(
            f'<div class="row"><div class="tag">{esc(t["name"])}</div>'
            f'<div class="samp" style="font-size:{t["size"]:g}px;font-weight:{t["cssWeight"]}">'
            f'Feel lighter today</div></div>')
    card("preview/type-ui.html", "Type", "Type — UI scale (SF Pro)",
         f"{len(ui)} tokens on Apple's text styles", "900x900",
         f"<h2>SF Pro — {len(ui)} tokens</h2>" + "".join(rows) +
         '<p class="note">SF Pro is the system font, so it is not embedded here or in the '
         'app — Apple\'s licence forbids it, which is why the app reaches it through a '
         '<strong>Dynamic Type text style</strong> rather than a point size. The size shown '
         'is the style\'s size at the default setting; on device it scales from there, up '
         'to 200%. SF Pro carries every weight, so a token named bold is bold on screen.</p>',
         css)

    rows = []
    for t in sorted(disp, key=lambda x: -x["size"]):
        it = "font-style:italic;" if t.get("italic") else ""
        rows.append(
            f'<div class="row"><div class="tag">{esc(t["name"])}</div>'
            f'<div class="samp" style="font-family:Display,Georgia,serif;'
            f'font-size:{t["size"]:g}px;font-weight:{t["cssWeight"]};{it}">'
            f'I\'m here to help you rediscover your light</div></div>')
    card("preview/type-display.html", "Type", "Type — Display scale",
         f"{len(disp)} tokens · ABC Otto", "900x620",
         f"<h2>Display — {len(disp)} tokens</h2>" + "".join(rows), css)

    rows = []
    for t in fams:
        rows.append(f'<div class="row"><div class="tag">{esc(t["name"])}</div>'
                    f'<div class="samp"><code>{esc(t["value"])}</code></div></div>')
    card("preview/type-families.html", "Type", "Type — Font names",
         "The PostScript names the app registers", "820x420",
         f"<h2>Registered faces — {len(fams)}</h2>" + "".join(rows) +
         '<p class="note">The display API is deliberately <em>role</em>-named '
         '(<code>display*</code>, not the typeface) so swapping the family touches only these '
         'four strings — zero call sites.</p>', css)


def scale_cards(tokens):
    sp = by_kind(tokens, "spacing")
    rd = by_kind(tokens, "radius")
    css = """
  .bars{display:flex;flex-direction:column;gap:5px}
  .b{display:flex;align-items:center;gap:12px}
  .lbl{width:110px;flex:0 0 110px;font-size:11px;color:#5C6370}
  .bar{height:15px;background:linear-gradient(90deg,#4CCAFF,#1DF5ED);border-radius:3px}
  .rgrid{display:flex;flex-wrap:wrap;gap:18px}
  .rbox{width:86px;height:86px;background:linear-gradient(135deg,#946EFF,#FF38B9);
        display:flex;align-items:flex-end;justify-content:center;color:#fff;font-size:10px;
        padding-bottom:5px;box-sizing:border-box}
"""
    bars = "".join(
        f'<div class="b"><div class="lbl">{esc(t["name"])}</div>'
        f'<div class="bar" style="width:{max(t["value"], 1)}px"></div>'
        f'<div class="lbl" style="width:auto">{t["value"]}px</div></div>'
        for t in sorted(sp, key=lambda x: x["value"]))
    card("preview/spacing.html", "Scale", "Spacing", f"{len(sp)} steps, 0–100px", "760x560",
         f'<h2>Spacing — {len(sp)} tokens</h2><div class="bars">{bars}</div>'
         '<p class="note">A linear px ladder, not a ratio scale — the name is the value.</p>',
         css)

    boxes = "".join(
        f'<div class="rbox" style="border-radius:{min(t["value"], 43)}px">{t["value"]}</div>'
        for t in sorted(rd, key=lambda x: x["value"]))
    card("preview/radius.html", "Scale", "Radius", f"{len(rd)} steps", "760x300",
         f'<h2>Border radius — {len(rd)} tokens</h2><div class="rgrid">{boxes}</div>'
         '<p class="note"><code>borderRadiusFull</code> is 9999 — a capsule, clamped here '
         'for display.</p>', css)


def gradient_card(tokens):
    gs = by_kind(tokens, "gradient")
    css = """
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(178px,1fr));gap:15px}
  .g{height:76px;border-radius:10px;border:1px solid rgba(0,0,0,.06)}
  .nm{font-weight:500;margin-top:7px;font-size:12px}
  .dt{color:#A8AEB8;font-size:10px}
"""
    cells = "".join(
        f'<div><div class="g" style="background:{t["css"]}"></div>'
        f'<div class="nm">{esc(t["name"])}</div>'
        f'<div class="dt">{t["angle"]:g}° · {len(t["stops"])} stops · {esc(t["source"])}</div>'
        f'</div>' for t in gs)
    card("preview/gradient.html", "Gradient", "Gradients", f"{len(gs)} tokens", "980x860",
         f'<h2>Gradients — {len(gs)} tokens</h2><div class="grid">{cells}</div>'
         '<p class="note">Angles are SwiftUI degrees mapped to CSS. A <code>locations: nil</code> '
         'gradient distributes its stops evenly, matching <code>GradientStyle.linear</code>.</p>',
         css)


def shadow_card(tokens):
    sh = by_kind(tokens, "shadow")
    css = """
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(178px,1fr));gap:24px}
  .s{height:74px;background:#fff;border-radius:12px}
  .nm{font-weight:500;margin-top:11px;font-size:12px}
  .dt{color:#A8AEB8;font-size:10px}
"""
    cells = "".join(
        f'<div><div class="s" style="box-shadow:{t["css"]}"></div>'
        f'<div class="nm">{esc(t["name"])}</div>'
        f'<div class="dt">r{t["radius"]:g} · {t["offsetX"]:g},{t["offsetY"]:g} · '
        f'{esc(t["source"])}</div></div>' for t in sh)
    card("preview/shadow.html", "Shadow", "Shadows", f"{len(sh)} tokens", "980x880",
         f'<h2>Shadows — {len(sh)} tokens</h2><div class="grid">{cells}</div>'
         '<p class="note">SwiftUI\'s <code>radius</code> is a Gaussian sigma; CSS blur is '
         'roughly twice it, which is the conversion applied here.</p>', css)


def brand_cards():
    b = BRAND_BY_APP.get(CFG["key"])
    if not b:
        print(f"WARNING — no brand facts for '{CFG['key']}' in BRAND_BY_APP; "
              "skipping brand cards. Add them from AI Knowledge rather than "
              "letting another app's voice stand in.", file=sys.stderr)
        return
    css = """
  .lede{font-size:26px;font-family:Georgia,serif;line-height:1.3;margin:0 0 20px;color:#222}
  ul{margin:0 0 16px;padding-left:19px} li{margin:5px 0}
  .two{display:grid;grid-template-columns:1fr 1fr;gap:26px}
  .good li{color:#1F7A4D} .bad li{color:#B4433C}
  table{border-collapse:collapse;width:100%;font-size:12px}
  td,th{text-align:left;padding:7px 10px;border-bottom:1px solid #EDEFF2;vertical-align:top}
  th{font-size:10px;text-transform:uppercase;letter-spacing:.07em;color:#A8AEB8;font-weight:500}
  .pill{display:inline-block;background:#F1F3F6;border-radius:99px;padding:3px 10px;
        margin:0 5px 6px 0;font-size:11px}
"""
    phil = "".join(f"<li><strong>{esc(t)}</strong> — {esc(d)}</li>" for t, d in b["philosophy"])
    isnt = "".join(f"<li>{esc(x)}</li>" for x in b["is_not"])
    card("preview/brand-identity.html", "Brand", "Brand — What Aurie is",
         "Core promise, philosophy, boundaries", "860x600", f"""
<p class="lede">{esc(b['promise'])}</p>
<p>{esc(b['is'])}</p>
<h2>Product philosophy</h2><ul>{phil}</ul>
<h2>What Aurie is not</h2><ul>{isnt}</ul>
<p class="note">Source: {esc(b['source_app'])}</p>
""", css)

    voice = "".join(f"<tr><td><strong>{esc(a)}</strong></td><td>{esc(x)}</td></tr>"
                    for a, x in b["voice"])
    tags = "".join(f"<tr><td>{esc(p)}</td><td>{esc(t)}</td></tr>" for p, t in b["taglines"])
    card("preview/brand-voice.html", "Brand", "Brand — Voice",
         "How Aurie sounds, with approved lines", "860x560", f"""
<h2>Voice attributes</h2>
<p>Aurie speaks in <strong>first person</strong> as a warm, emotionally attuned companion.
Gentle yet grounded — never clinical, never preachy. Light metaphors, invitational language.</p>
<table>{voice}</table>
<h2 style="margin-top:22px">Approved lines</h2>
<table><tr><th>Placement</th><th>Phrase</th></tr>{tags}</table>
<p class="note">Source: {esc(b['source_app'])}. Aurie speaks in first person; Aura does not.</p>
""", css)

    nm = "".join(f"<tr><td><strong>{esc(n)}</strong></td><td>{d}</td></tr>"
                 for n, d in b["naming"])
    pr = "".join(f"<li><strong>{esc(t)}</strong> — {esc(d)}</li>" for t, d in b["principles"])
    card("preview/brand-naming.html", "Brand", "Brand — Naming & principles",
         "Aura vs Aurie, and the five shared beliefs", "860x520", f"""
<h2>Aura vs Aurie</h2>
<p>Two distinct products. Mixing them up is the single most common mistake in customer-facing
copy.</p>
<table>{nm}</table>
<h2 style="margin-top:22px">Shared principles</h2><ul>{pr}</ul>
<p class="note">Source: {esc(b['source_identity'])}</p>
""", css)

    banned = "".join(f'<span class="pill">{esc(x)}</span>' for x in b["banned"])
    good = "".join(f"<li>{esc(x)}</li>" for x in b["cta_good"])
    bad = "".join(f"<li>{esc(x)}</li>" for x in b["cta_bad"])
    card("preview/brand-copy.html", "Brand", "Brand — Copy rules",
         "Banned words and CTA style", "860x480", f"""
<h2>Never use</h2><div>{banned}</div>
<h2 style="margin-top:22px">CTAs</h2>
<div class="two">
  <div><h2>Do</h2><ul class="good">{good}</ul></div>
  <div><h2>Don't</h2><ul class="bad">{bad}</ul></div>
</div>
<h2 style="margin-top:14px">Numbers</h2>
<p>Pair every metric with why it matters. A number on its own is noise.<br>
<span style="color:#1F7A4D">“7 million people use Aura — and the top reason they keep coming
back is sleep.”</span><br>
<span style="color:#B4433C">“Join 7 million users today.”</span></p>
<p class="note">Source: {esc(b['source_identity'])}</p>
""", css)

    modes = "".join(
        f"<tr><td><strong>{esc(n)}</strong></td><td>{esc(d)}</td><td>{esc(x)}</td>"
        f"<td><em>{esc(f)}</em></td></tr>" for n, d, x, f in b["modes"])
    loop = "".join(f"<li>{esc(x)}</li>" for x in b["loop"])
    card("preview/brand-modes.html", "Brand", "Brand — Experience modes",
         "The three arcs, and the core loop", "900x460", f"""
<h2>Core loop</h2><ol>{loop}</ol>
<h2 style="margin-top:20px">Three experience modes</h2>
<table><tr><th>Mode</th><th>Length</th><th>Shape</th><th>Felt result</th></tr>{modes}</table>
<p class="note">Design consequence: a surface must declare which mode it serves. ICP1 fails
silently — if a screen raises activation, they leave without complaining. Source:
{esc(b['source_app'])}</p>
""", css)


def screen_cards():
    # The capture store is optional: an app can have a theme long before it has
    # a sim-test suite. Missing or unreadable index -> no screen cards, not a
    # crash two minutes into a build whose tokens already succeeded.
    idx_path = os.path.join(SHOTS, "index.json")
    if not os.path.exists(idx_path):
        print(f"WARNING — no capture index at {idx_path}; building without screens",
              file=sys.stderr)
        return 0
    try:
        index = json.load(open(idx_path))["screens"]
    except (json.JSONDecodeError, KeyError) as e:
        print(f"WARNING — capture index unreadable ({e}); building without screens",
              file=sys.stderr)
        return 0
    # The store keys each sky separately (`x`, `x_light`, `x_dark`). Grouping by
    # the base name puts a screen's three skies in one tile: the whole point of
    # the variants is comparison, and 240 alphabetical tiles interleave every
    # screen's dark sky between its anytime and light one instead.
    groups = {}
    for name, meta in index.items():
        if not os.path.exists(os.path.join(OUT, "uploads", f"{name}.png")):
            continue
        base, sky = name, "Anytime"
        for suffix, label in (("_light", "Light"), ("_dark", "Dark")):
            if name.endswith(suffix):
                base, sky = name[: -len(suffix)], label
                break
        dom = groups.setdefault(domain_of(base), {})
        dom.setdefault(base, {})[sky] = (name, meta)

    css = """
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:26px}
  .skies{display:grid;grid-template-columns:repeat(3,1fr);gap:6px}
  .skies figure{margin:0}
  .skies figcaption{color:#A8AEB8;font-size:9px;text-align:center;margin-top:3px}
  .sc img{width:100%;border-radius:10px;border:1px solid #E4E7EC;display:block;
          box-shadow:0 5px 18px rgba(0,0,0,.07)}
  .nm{font-weight:500;margin-top:9px;font-size:11px;word-break:break-word}
  .dt{color:#A8AEB8;font-size:10px}
"""
    total = 0
    for dom in sorted(groups):
        screens = groups[dom]
        total += len(screens)
        cells = ""
        for base in sorted(screens):
            skies = screens[base]
            shots = "".join(
                f'<figure><img src="../uploads/{esc(skies[k][0])}.png" alt="{esc(skies[k][0])}">'
                f'<figcaption>{esc(k)}</figcaption></figure>'
                for k in ("Anytime", "Light", "Dark") if k in skies)
            captured = max((m.get("captured", "") for _, m in skies.values()), default="")
            cells += (f'<div class="sc"><div class="skies">{shots}</div>'
                      f'<div class="nm">{esc(base)}</div>'
                      f'<div class="dt">{esc(captured)}</div></div>')
        slug = re.sub(r"[^a-z0-9]+", "-", dom.lower()).strip("-")
        card(f"preview/screens-{slug}.html", "Screens", f"Screens — {dom}",
             f"{len(screens)} captured", "980x900",
             f'<h2>{esc(dom)} — {len(screens)} screens</h2><div class="grid">{cells}</div>'
             '<p class="note">Live captures from the sim-test store, not mockups. Each is a '
             'real screen on a real build, shown in every sky it was captured in.</p>', css)
    return total


# ------------------------------------------------------------------ adherence -

def agent_grounding(tokens, path, a11y=None):
    """Emit the token reference the `product:designer` agent reads.

    Deliberately NOT a replacement for `design-system-<app>.md`. That file holds
    renderer knowledge this cannot derive — 3x capture scaling, feathering a
    moved band against a gradient background, which glyphs are pre-exported,
    the real copy inventory and its sources. This supplies the half that goes
    stale by hand: every token's current value, traced to the line that defines
    it, plus the brand rules that bind any copy.
    """
    b = BRAND_BY_APP.get(CFG["key"], {})
    app = CFG["displayName"]
    L = [f"# {app} design tokens — generated",
         "",
         f"**Generated from `{CFG['themeDir']}` by `product:sync-design-system`.**",
         "Do not edit: re-run the generator. Values here are what the app ships today.",
         "",
         f"Companion to `design-system-{CFG['key']}.md`, which holds the renderer",
         "mechanics and the real copy inventory. This file holds values only —",
         "**cite tokens by name**, never by raw hex or px.", ""]

    disp = [t for t in tokens if t["kind"] == "fontStyle" and t.get("family") == "Display"]
    ui = [t for t in tokens if t["kind"] == "fontStyle" and t.get("family") == "SF Pro"]
    fams = [t for t in tokens if t["kind"] == "fontFamily"]

    L += ["## Type", "",
          "Captures from the iPhone 17 Pro simulator are **3x** — multiply every pt below by 3.",
          "", "### Faces", "", "| Token | PostScript name |", "|---|---|"]
    L += [f"| `{t['name']}` | `{t['value']}` |" for t in fams]
    L += ["", "### Display scale (ABC Otto)", "", "| Token | Size | Weight |", "|---|---|---|"]
    role_label = {"display": "Regular", "displayLight": "Light",
                  "displayMedium": "Medium", "displayLightItalic": "Light italic"}
    L += [f"| `{t['name']}` | {t['size']:g}pt | {role_label.get(t['weight'], t['weight'])} |"
          for t in sorted(disp, key=lambda x: -x["size"])]
    L += ["", "### UI scale (SF Pro)", "",
          "| Token | Text style | Size at default | Weight |", "|---|---|---|---|"]
    L += [f"| `{t['name']}` | "
          f"{('*fixed*' if t.get('fixed') else '`.' + t.get('style', '?') + '`')} | "
          f"{t['size']:g}pt | {t['weight']} |"
          for t in sorted(ui, key=lambda x: -x["size"])]
    L += ["",
          "SF Pro is the system font: not embedded here or in the app, because Apple's",
          "licence forbids it. The app names a **Dynamic Type text style**, never a point",
          "size, so the size above is the style's size at the default setting and scales",
          "from there. The two *fixed* tokens are the decorative exception below 11 pt.", ""]

    solid = [t for t in tokens if t["kind"] == "color" and t.get("alpha", 1) >= 0.999]
    alpha = [t for t in tokens if t["kind"] == "color" and t.get("alpha", 1) < 0.999]
    L += ["## Colour", "",
          f"{len(solid)} opaque, {len(alpha)} translucent. Names describe the **value**.",
          "These are the palette the roles resolve to — on a sky surface name the",
          "role (`skyState.textPrimary`, `textSecondary`, `figurePrimary`…), which",
          "carries its own per-sky contrast measurement.", "",
          "| Token | Value | Source |", "|---|---|---|"]
    L += [f"| `{t['name']}` | `{t['css']}` | `{t['source']}` |" for t in solid + alpha]

    arrays = [t for t in tokens if t["kind"] == "colorArray"]
    if arrays:
        L += ["", "### Fade pairs", "", "| Token | Stops |", "|---|---|"]
        L += [f"| `{t['name']}` | `{t['css']}` |" for t in arrays]

    for kind, title, note in (
            ("spacing", "Spacing", "A linear px ladder — the name is the value."),
            ("radius", "Radius", "`borderRadiusFull` is 9999: a capsule.")):
        rows = [t for t in tokens if t["kind"] == kind]
        L += ["", f"## {title}", "", note, "",
              "  ".join(f"`{t['name']}`={t['value']}" for t in sorted(rows, key=lambda x: x["value"]))]

    grads = [t for t in tokens if t["kind"] == "gradient"]
    L += ["", "## Gradients", "", "| Token | CSS |", "|---|---|"]
    L += [f"| `{t['name']}` | `{t['css']}` |" for t in grads]

    shadows = [t for t in tokens if t["kind"] == "shadow"]
    L += ["", "## Shadows", "",
          "SwiftUI `radius` is a Gaussian sigma; the CSS blur below is ~2x it.", "",
          "| Token | CSS |", "|---|---|"]
    L += [f"| `{t['name']}` | `{t['css']}` |" for t in shadows]

    if b:
        L += ["", "## Brand rules that bind any copy", "",
              f"**Core promise:** {b['promise']}", "",
              "### Never use", "",
              ", ".join(f"*{x}*" for x in b["banned"]), "",
              "### CTAs", "",
              "| Do | Don't |", "|---|---|"]
        for good, bad in zip(b["cta_good"], b["cta_bad"]):
            L += [f"| {good} | {bad} |"]
        L += ["", "### Naming", ""]
        L += [f"- **{n}** — {re.sub(r'<[^>]+>', '', d)}" for n, d in b["naming"]]
        L += ["", "### Voice", "",
              f"{app} speaks in **first person** — warm, grounded, invitational; "
              "never clinical, never preachy.", ""]
        L += [f"- **{a}** — “{x}”" if x[0].isupper() else f"- **{a}** — {x}"
              for a, x in b["voice"]]
        L += ["", "### Approved lines", "", "| Placement | Phrase |", "|---|---|"]
        L += [f"| {p} | {t} |" for p, t in b["taglines"]]
        L += ["", f"Source: {b['source_identity']}, {b['source_app']}."]

    if a11y:
        # The designer picks tokens; without this it can pick one that is legible
        # at midday and unreadable at night and still be "correct" by its own
        # rules. Same numbers as the Accessibility cards and
        # .claude/rules/accessibility.md — all three read Design/Accessibility
        # Baseline, so there is one place to change them.
        # The exception belongs in the grounding file too. The card carries it,
        # and a grounding file that states a flatter rule would have the agent
        # reporting a ruled exception as a violation.
        grounding_dec = (f" — {a11y['minTextDecorative']} pt for decoration only"
                         if a11y.get("minTextDecorative") else "")
        L += ["", "## The bar every design must clear", "",
              "These are **not** suggestions, and they bind token choice. A proposal that "
              "picks a legible-looking token which fails one of these is wrong.", "",
              "| Rule | Minimum |", "|---|---|",
              f"| Body text contrast | **{a11y['textContrast']}** |",
              f"| Large text ({a11y['largeTextDef']}) | **{a11y['largeTextContrast']}** |",
              f"| Icons, borders, focus rings, control states | **{a11y['nonTextContrast']}** |",
              f"| Tap target | **{a11y['targetApple']}** |",
              f"| Minimum text size | **{a11y['minText']} pt**{grounding_dec} |",
              f"| Text scaling | **{a11y['scaling']}** without loss |",
              "| Colour alone | never the only signal — pair it with an icon, text or shape |",
              ""]
        if a11y.get("minTextDecorativeNote"):
            L += [f"**The decorative exception.** {a11y['minTextDecorativeNote']} It is not a "
                  "licence to shrink a label; if a person has to read it to use the app, the "
                  f"floor is {a11y['minText']} pt.", ""]
        L += [
              "### Contrast is measured in every sky, not on white", "",
              f"{app} is locked to light mode but reads the time-of-day sky, so **dusk and "
              "night are its dark mode**. A token that passes at midday and fails at night "
              "has failed. Measure against the worst case, and behind any blur or scrim that "
              "sits over it.", "",
              "This is the single most repeated mistake in this app: eleven surfaces have "
              "been fixed one at a time for it. When a proposal puts text or a glyph over "
              "the sky, over glass, or over a gradient, say which sky you checked.", "",
              "### Say so when nothing clears it", "",
              "If no token in the list above meets the bar for what the idea needs, **say "
              "that** rather than picking the closest one. A missing token is a finding "
              "about the design system, and a more useful output than a sheet that cannot "
              "ship.", "",
              f"Source: AI Knowledge → Design/Accessibility Baseline; WCAG 2.2 AA, Apple HIG "
              f"(target {a11y['targetApple']}, text floor {a11y['minText']} pt), Material "
              f"({a11y['targetMaterial']}). WCAG AA alone asks only {a11y['targetWcagAA']} "
              "for a target — passing AA is not passing this bar."]

    # Refuse to invent the directory. This file is read by `product:designer`
    # from a committed location; if the parent does not already exist the path
    # is wrong, and creating it would write a plausible file somewhere nobody
    # reads while the agent carries on against the stale committed one.
    parent = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(parent):
        raise SystemExit(
            f"--grounding-out parent does not exist: {parent}\n"
            "The grounding file belongs next to the hand-kept design system, at\n"
            "  <AINative>/plugins/product/skills/design-idea/references/\n"
            "Pass an absolute path (or ${CLAUDE_PLUGIN_ROOT}/skills/design-idea/"
            "references/...) rather than one relative to the current directory.")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    return len(L)
def ds_manifest(tokens, faces):
    """Write `_ds_manifest.json` — the index the Design System pane renders from.

    The app compiles this itself from each card's first-line `@dsCard` comment,
    but only when its own self-check runs. A freshly uploaded project shows
    "No cards yet" until then, so the manifest is written here too: same schema,
    derived from the same card definitions, so the two cannot disagree.
    """
    kind_map = {"color": "color", "colorArray": "other", "fontFamily": "font",
                "fontStyle": "font", "spacing": "spacing", "radius": "radius",
                "gradient": "other", "shadow": "shadow"}
    prefix = {"color": "color", "colorArray": "color", "spacing": "space",
              "radius": "radius", "gradient": "gradient", "shadow": "shadow",
              "fontStyle": "font"}
    tok = []
    for t in tokens:
        if t["kind"] == "fontFamily":
            name = "--font-family-" + re.sub(
                r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Za-z])(?=[0-9])", "-", t["name"]).lower()
        else:
            name = var_of(t, prefix[t["kind"]])
        tok.append({"name": name, "value": t["css"], "kind": kind_map[t["kind"]],
                    "definedIn": "tokens.css"})

    pid = (CFG.get("designProjectId") or "")[:6]
    families = sorted({f[0] for f in faces})
    manifest = {
        "namespace": f"{CFG['displayName']}_{pid}" if pid else CFG["displayName"],
        "components": [],
        "startingPoints": [],
        "cards": CARDS,
        "templates": [],
        "globalCssPaths": ["tokens.css"],
        "tokens": tok,
        "themes": [],
        "fonts": [{"family": fam, "weight": str(w), "style": st,
                   "cssPath": "tokens.css", "files": [f"fonts/{fn}"]}
                  for fam, fn, w, st in faces],
        "brandFonts": [{"family": fam, "status": "ok", "tokens": [], "path": "tokens.css"}
                       for fam in families],
        "source": "spa",
    }
    with open(os.path.join(OUT, "_ds_manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    return len(CARDS)
def adherence(tokens):
    kind_map = {"color": "color", "colorArray": "other", "fontFamily": "font",
                "fontStyle": "font", "spacing": "spacing", "radius": "radius",
                "gradient": "other", "shadow": "shadow"}
    prefix = {"color": "color", "colorArray": "color", "spacing": "space",
              "radius": "radius", "gradient": "gradient", "shadow": "shadow",
              "fontStyle": "font"}
    names, kinds = [], {}
    for t in tokens:
        if t["kind"] == "fontFamily":
            v = "--font-family-" + re.sub(
                r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Za-z])(?=[0-9])", "-", t["name"]).lower()
        else:
            v = var_of(t, prefix[t["kind"]])
        names.append(v)
        kinds[v] = kind_map[t["kind"]]
    cfg = {
        "plugins": ["react", "import"],
        "rules": {
            "no-restricted-syntax": ["warn", [
                {"selector": "Literal[value=/#[0-9a-fA-F]{3,8}\\b/]",
                 "message": "Raw hex color — use an Aurie color token via var()."},
                {"selector": "Literal[value=/\\b\\d+px\\b/]",
                 "message": "Raw px value — use an Aurie spacing or radius token via var()."},
                {"selector": "Literal[value=/font-family\\s*:\\s*(?!['\\\"]?(?:SF ?Pro|-apple-system|system-ui|Display))/i]",
                 "message": "Font not in the Aurie system. Available: SF Pro (the system "
                            "font, via the system stack), Display (ABC Otto)."},
            ]],
        },
        "x-omelette": {
            "components": {},
            "tokens": sorted(set(names)),
            "tokenKinds": kinds,
            "fontFamilies": ["SF Pro", "Display"],
        },
    }
    with open(os.path.join(OUT, "_adherence.oxlintrc.json"), "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
    return len(set(names))


# ----------------------------------------------------------------------- main -

def resize_screenshots():
    """Copy the capture store into uploads/, downscaled. A 3x capture is ~1.5 MB
    and a gallery card shows it at a few hundred px — shipping the originals
    would be ~62 MB of bytes nobody sees."""
    if not os.path.isdir(SHOTS):
        print(f"WARNING — no screenshot store at {SHOTS}; skipping screens",
              file=sys.stderr)
        return 0
    dst = os.path.join(OUT, "uploads")
    os.makedirs(dst, exist_ok=True)
    n, unresized = 0, []
    for f in sorted(os.listdir(SHOTS)):
        if not f.endswith(".png"):
            continue
        src, out = os.path.join(SHOTS, f), os.path.join(dst, f)
        # `sips` is macOS-only. A non-zero exit means it ran and refused; a
        # missing binary raises FileNotFoundError instead, which is what CI and
        # any Linux checkout hit. Both fall back to shipping the original.
        try:
            rc = subprocess.run(["sips", "-Z", str(CFG["screenshotWidth"]), src, "--out", out],
                                capture_output=True).returncode
        except OSError:
            rc = 1
        if rc != 0:
            shutil.copy(src, out)
            unresized.append(f)
        n += 1
    if unresized:
        print(f"WARNING — sips unavailable or failed for {len(unresized)} capture(s); "
              "shipped at full resolution, so the bundle is larger than intended.",
              file=sys.stderr)
    return n


def embed_fonts(cfg, repo, out):
    """Copy the app's font files into the bundle; [(family, file, weight, style)].

    The display face ships so the cards render in the app's real letterforms; the body
    face is the system font and is never embedded. A file the config names but the repo
    no longer has stops the build: shipping without it makes every display card fall
    back to Georgia, and the bundle still looks complete."""
    os.makedirs(os.path.join(out, "fonts"), exist_ok=True)
    faces, missing = [], []
    for family, rel, weight, style in cfg["fonts"]:
        src = os.path.join(repo, cfg["fontsDir"], rel)
        if not os.path.exists(src):
            missing.append(rel)
            continue
        base = os.path.basename(rel)
        shutil.copy(src, os.path.join(out, "fonts", base))
        faces.append((family, base, weight, style))
    if missing:
        raise SystemExit(f"font files in apps.py not found in {cfg['fontsDir']}: {', '.join(missing)}"
                         " — update the app's fonts list")
    return faces


def main():
    global OUT, REPO, SHOTS, CFG
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--app", default="aurie", help="app key from apps.py")
    ap.add_argument("--out", required=True, help="bundle directory (created if absent)")
    ap.add_argument("--grounding-out", default=None,
                    help="also write the designer agent's generated token reference "
                         "to this path (typically design-idea/references/"
                         "design-tokens-<app>.generated.md)")
    ap.add_argument("--repo", default="", help="read this checkout or worktree instead of "
                                              "the app's configured repo (theme, fonts, "
                                              "screenshots)")
    ap.add_argument("--strict", action="store_true",
                    help="stop on a token parser gap instead of building without that token")
    args = ap.parse_args()
    CFG = resolve(args.app, args.repo)
    OUT, REPO = args.out, CFG["repo"]
    SHOTS = os.path.join(REPO, CFG["screenshots"])
    os.makedirs(OUT, exist_ok=True)

    # Every generated directory is cleared first. Without this, a card that was
    # renamed or dropped survives in the bundle and gets uploaded again — the
    # output would accumulate rather than mirror the current sources. Only the
    # directories this script owns are touched.
    for d in ("preview", "uploads", "fonts"):
        shutil.rmtree(os.path.join(OUT, d), ignore_errors=True)

    # Tokens first — every card reads the manifest it writes.
    rc = subprocess.run(
        [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "extract_tokens.py"),
         "--app", args.app, "--out", OUT, "--repo", REPO] + (["--strict"] if args.strict else [])).returncode
    if rc != 0:
        raise stops.Reported("token extraction failed — not building cards on a "
                             "manifest with known gaps")

    resize_screenshots()
    tokens = json.load(open(os.path.join(OUT, "tokens.json")))["tokens"]

    color_cards(tokens)
    semantic_layer_card(tokens)
    if CFG.get("a11yBaseline"):
        accessibility_cards(tokens, fetch_a11y(CFG["a11yBaseline"]))
    type_cards(tokens)
    scale_cards(tokens)
    adherence_card(tokens)
    gradient_card(tokens)
    shadow_card(tokens)
    brand_cards()
    n_screens = screen_cards()
    n_tokens = adherence(tokens)

    faces = embed_fonts(CFG, REPO, OUT)

    # Append the @font-face block so every card gets both families for free.
    with open(os.path.join(OUT, "tokens.css"), "a") as fh:
        fh.write("\n/* Font faces. 'Display' is role-named, matching Fonts.swift —\n"
                 "   the typeface behind it can change without touching a call site. */\n")
        for family, fname, weight, style in faces:
            fh.write(f"@font-face {{ font-family: '{family}'; "
                     f"src: url('fonts/{fname}') format('opentype'); "
                     f"font-weight: {weight}; font-style: {style}; font-display: swap; }}\n")

    # After the faces are known — the manifest records them alongside the cards.
    n_cards = ds_manifest(tokens, faces)

    n_grounding = 0
    if args.grounding_out:
        n_grounding = agent_grounding(
            tokens, args.grounding_out,
            fetch_a11y(CFG["a11yBaseline"]) if CFG.get("a11yBaseline") else None)

    with open(os.path.join(OUT, "README.md"), "w", encoding="utf-8") as fh:
        fh.write(f"""# {CFG["displayName"]}

The design system for the {CFG["displayName"]} iOS app. **Generated, not maintained.**

| Layer | Source of truth |
|---|---|
| Tokens ({len(tokens)}) | `{CFG["themeDir"]}/*.swift` |
| Screens ({n_screens}) | the sim-test capture store, `knowledge/screenshots/` |
| Brand & voice | AI Knowledge — Identity › Brand, Identity › Apps › Aurie |
| Palette, gradients, icons, `Environment` roles | {_figma_cell()} |

Nothing here is hand-authored. To change the system, change the app or the
knowledge base and re-run the generator — edits made here are overwritten.

## Known gaps

- **`Visible/` roles and the glass, scrim, border and feedback roles** are decided
  in Figma but not mirrored in code yet — the `Text/` and `Figure/` roles are.
  See *Color — the semantic layer*.
- **The per-sky role values are derived, not confirmed.** Hue comes from the app;
  the secondary and tertiary opacities were computed against the contrast bar and
  are awaiting design's confirm-or-correct.
- **Components not modelled yet.** Tokens, brand and screens first.

## Scope

{CFG["displayName"]} is the **mobile** system; the sibling app gets its own. A web system, if it comes,
should inherit this token layer rather than fork it — the tokens carry no
platform-specific units.
""")

    # What the upload's finalize_plan globs match: the count write_files batches.
    upload = [f for g in UPLOAD_GLOBS for f in glob.glob(os.path.join(OUT, g))]
    summary = {"tokens": len(tokens), "registryVars": n_tokens,
               "screens": n_screens, "cards": n_cards, "files": len(upload)}
    if args.grounding_out:
        summary["groundingLines"] = n_grounding
        summary["groundingPath"] = args.grounding_out
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    stops.run(main, "build-bundle")
