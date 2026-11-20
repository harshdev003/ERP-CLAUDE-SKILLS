# Figma REST extraction (the fallback when the MCP is access-denied)

Extraction is **MCP-first** (see SKILL.md Phase 1): try `get_design_context` / `get_metadata` /
`get_variable_defs` / `get_screenshot` first. But MCP access is **file-dependent** - some files read
fine, others return "you don't have edit access to this file" (the MCP wants an editor seat, and
`download_assets` always does). When the MCP is denied or rate-limited, use the REST recipes here:
they work with a view-only personal access token. This is the fallback, not the only path - but it
is the reliable one when MCP access is missing, so keep it complete.

## Token

- Generate at figma.com -> Settings -> Security -> Personal access tokens -> File content: Read-only.
- **Set it ONCE so you never pass it again** - add the export to `~/.zshrc` (or a gitignored `.env`
  the shell sources), then every session has it. NEVER commit it, never put it in CI or in any file
  in the repo, never hardcode it in this skill. It is tied to one dev's view access and dies when
  their access changes.

```bash
echo 'export FIGMA_TOKEN=figd_xxx' >> ~/.zshrc && source ~/.zshrc   # one-time
```

## URL -> fileKey + nodeId

`https://www.figma.com/design/<fileKey>/<name>?node-id=<a>-<b>` -> fileKey = `<fileKey>`,
nodeId = `<a>:<b>` (the REST API uses a colon; the URL uses a hyphen).

**Default file key: `lwoV0lfkBSxNW2c2yWRl9C`** (Aura 2.0). Use this whenever the user gives only a
node id (e.g. `800:6686`) or a screen name - no need to ask for the file key. "Reskined screens"
root: `800:6462` (list its children to resolve a screen NAME -> node id). Only use a different file
key if the user pastes a full URL pointing at another file.

## Recipes

Node JSON (resolved values). Omit `depth` for the full subtree; use `depth=2` to scout structure:

```bash
curl -s -H "X-Figma-Token: $FIGMA_TOKEN" \
  "https://api.figma.com/v1/files/<fileKey>/nodes?ids=<nodeId>" -o /tmp/frame.json
```

Rendered PNG of a frame (returns a short-lived S3 URL; then download it):

```bash
curl -s -H "X-Figma-Token: $FIGMA_TOKEN" \
  "https://api.figma.com/v1/images/<fileKey>?ids=<nodeId>&format=png&scale=2"
# -> {"images":{"<nodeId>":"https://figma-alpha-api.s3..."}}  then curl that URL to a .png
```

List sections / children to find a target frame:

```bash
curl -s -H "X-Figma-Token: $FIGMA_TOKEN" \
  "https://api.figma.com/v1/files/<fileKey>/nodes?ids=<nodeId>&depth=2"
```

## Image fills (background photos, thumbnails) - the endpoint the pilot missed

IMAGE fills appear in node JSON as `{"type":"IMAGE","imageRef":"<ref>","scaleMode":"FILL"}`. The
`imageRef` is NOT a URL. Resolve all refs to URLs in one call:

```bash
curl -s -H "X-Figma-Token: $FIGMA_TOKEN" \
  "https://api.figma.com/v1/files/<fileKey>/images"
# -> {"meta":{"images":{"<imageRef>":"https://s3.../..png", ...}}}
```

Map each node's `imageRef` -> that URL, download the bytes (URLs expire ~7 days), and render the
real image (FastImage) at the node's box. Never substitute a flat color or placeholder for a photo.
The Home frame alone has 84 image fills across 18 unique refs.

## Icons / vectors: EXPORT them, never redraw (works at view access)

The mistake to avoid: hand-reproducing an icon as approximate SVG paths because "the asset export was
unavailable". The `/v1/images` RENDER endpoint exports ANY node - including a vector/icon - as SVG or
PNG, and it works with VIEW access. (Only the MCP `download_assets` needs an editor seat; the render
endpoint does not.) So every icon comes FROM Figma, not from guessing.

```bash
# Export icon nodes as SVG (vectors) - batch many ids in ONE call to save rate limit
curl -s -H "X-Figma-Token: $FIGMA_TOKEN" \
  "https://api.figma.com/v1/images/<fileKey>?ids=<n1>,<n2>,<n3>&format=svg"
# -> {"images":{"<n1>":"https://.../a.svg","<n2>":"...", ...}}  then curl each URL to a .svg file

# Raster asset (a rendered badge/illustration, not a clean vector): png at 3x
curl -s -H "X-Figma-Token: $FIGMA_TOKEN" \
  "https://api.figma.com/v1/images/<fileKey>?ids=<node>&format=png&scale=3"
```

Rules:
- Find icon/vector nodes in the node JSON (types `VECTOR`, `BOOLEAN_OPERATION`, or an INSTANCE of an
  icon component - names like `action.*`, `tapbar.*`, `specialty.*`, `icon/*`). Collect ALL their
  ids and export them, batched (many ids per call), rate-limit-paced, cached in the scratchpad.
- Use the exported SVG (via `SvgXml` / the repo's `Icon` set, `npm run bundleIcons`) - do NOT author,
  simplify, or approximate the paths by hand.
- Icon STYLING comes from the node too: the icon's fill/stroke color, stroke width, and size are in
  the node's `fills`/`strokes`/`strokeWeight` and bounding box. If the icon must be theme-tinted,
  strip the fill from the SVG and apply the mapped `theme.colors.icons*` token - but the GLYPH itself
  is always the exported vector, never a redraw.
- If EVERY export path fails for a specific asset (MCP denied AND `/v1/images` 429s after paced
  retries), do not fabricate it: flag it as a blocker with the exact node id (e.g. "export node
  1048:16089 as SVG and drop it in") and continue - the one place a placeholder is allowed, and only
  with that explicit TODO.

## Named-section inventory (run FIRST - the checklist the whole build/verify runs against)

A screen's sections are named nodes: the direct children of the scroll container (often a frame
named `Wrapper`, `Content`, `ScrollView`, or the frame that is much taller than the viewport). List
them in order, by name, with their heading text - so BUILD and VERIFY process one named section at a
time and nothing is skipped.

```python
import json
d=json.load(open('/tmp/frame.json'))
def firsttext(n):
    if n.get('type')=='TEXT': return n.get('characters') or n.get('name')
    for c in n.get('children',[]) or []:
        r=firsttext(c)
        if r: return r
    return None
def find_scroll(n):
    # the tallest vertical container = the scrolling section list
    best=None
    def rec(x):
        nonlocal best
        kids=x.get('children') or []
        if len(kids)>=3:
            h=(x.get('absoluteBoundingBox') or {}).get('height',0)
            if not best or h>(best.get('absoluteBoundingBox') or {}).get('height',0): best=x
        for c in kids: rec(c)
    rec(n); return best
for fid,nd in d['nodes'].items():
    doc=nd['document']; wrap=find_scroll(doc)
    print(f"\n=== {doc.get('name')} sections (in order) ===")
    for i,c in enumerate(wrap.get('children') or []):
        bb=c.get('absoluteBoundingBox') or {}
        kinds={}
        for ch in c.get('children') or []: kinds[ch.get('type')]=kinds.get(ch.get('type'),0)+1
        print(f"  {i:2} {c['id']:14} name={c.get('name','')!r:26} heading={firsttext(c)!r:24} h={round(bb.get('height',0))} children={kinds}")
```

Confirm the section list against the frame screenshot (the visible section headings should match the
`heading=` column). This inventory is Phase 1 artifact (a); the per-element spec below is grouped
under these section names.

## Per-element spec sheet (the fidelity fix - run this, don't eyeball)

Walk the node JSON and emit one row per visible node with its exact resolved properties. Skeleton:

```python
import json
d = json.load(open('/tmp/frame.json'))
def hexa(c,o=None):
    a=c.get('a',1) if o is None else o
    return f"#{round(c['r']*255):02X}{round(c['g']*255):02X}{round(c['b']*255):02X}"+("" if a>=1 else f"@{round(a,2)}")
def walk(n,depth=0,pbb=None):
    t=n.get('type'); st=n.get('style',{})
    font=f"{st.get('fontFamily')}/{st.get('fontSize')}/{st.get('fontWeight')}/lh{st.get('lineHeightPx')}/tr{st.get('letterSpacing')}" if t=='TEXT' else ''
    fills=[]
    for f in n.get('fills',[]) or []:
        if not f.get('visible',True): continue
        if f.get('type')=='SOLID': fills.append(hexa(f['color'],f.get('opacity')))
        elif f.get('type')=='IMAGE': fills.append(f"IMG:{f.get('imageRef')}")
        elif 'GRADIENT' in f.get('type',''): fills.append(f["type"]+":"+">".join(hexa(s['color']) for s in f.get('gradientStops',[])))
    sh=[f"{e['type']}({hexa(e.get('color',{'r':0,'g':0,'b':0}))} off{e.get('offset',{}).get('x',0)},{e.get('offset',{}).get('y',0)} r{e.get('radius')})" for e in (n.get('effects') or []) if e.get('visible',True)]
    lay=f"{n.get('layoutMode')} gap{n.get('itemSpacing')} rowgap{n.get('counterAxisSpacing')}" if n.get('layoutMode') else ''
    # exact size, position within parent, sizing mode, stroke, per-corner radii
    bb=n.get('absoluteBoundingBox') or {}
    w=bb.get('width'); h=bb.get('height')
    x=round(bb.get('x',0)-pbb.get('x',0),1) if (pbb and bb) else None
    y=round(bb.get('y',0)-pbb.get('y',0),1) if (pbb and bb) else None
    size=f"{round(w) if w else '?'}x{round(h) if h else '?'} @({x},{y})"
    mode=f"{n.get('layoutSizingHorizontal','')}/{n.get('layoutSizingVertical','')}".strip('/')  # FIXED/HUG/FILL
    stroke=''
    if n.get('strokes'):
        sc=[hexa(s['color'],s.get('opacity')) for s in n['strokes'] if s.get('type')=='SOLID' and s.get('visible',True)]
        if sc: stroke=f"{sc} w{n.get('strokeWeight')} {n.get('strokeAlign','')}"
    rad=n.get('rectangleCornerRadii') or n.get('cornerRadius')  # array = per-corner
    print('  '*depth, t, repr(n.get('name','')[:22]), '|', size, '| mode:',mode, '| font:',font, '| fill:',fills, '| stroke:',stroke, '| radius:',rad, '| fx:',sh, '| layout:',lay, '| pad:',[n.get('paddingLeft'),n.get('paddingTop'),n.get('paddingRight'),n.get('paddingBottom')])
    for c in n.get('children',[]) or []: walk(c,depth+1,bb)
for fid,nd in d['nodes'].items(): walk(nd['document'])
```

BUILD consumes this table row by row. The screenshot is for layout orientation + the final diff
ONLY - it is never the source of a value.

**Every numeric detail is here - use it, do not eyeball.** `size` = exact width x height; `@(x,y)` =
position within the parent (for absolutely-placed elements); `mode` = `FIXED`/`HUG`/`FILL` per axis
(FIXED -> a fixed `width`/`height`; FILL -> stretch to parent; HUG -> size to content); `stroke` =
border color + `strokeWeight` (thickness) + align; `radius` = a single value OR a 4-element array
`[tl,tr,br,bl]` when corners differ (`rectangleCornerRadii`); `pad` + `layout gap/rowgap` = the auto-
layout spacing. Apply the EXACT numbers (wrapped in `responsiveDimension`), never rounded to a
"nice" value and never inferred from the screenshot.

**Spacing note:** every `layoutMode` node encodes exact gaps - `itemSpacing` (gap between children
along the layout axis: section-to-section on the scroll container, card-to-card in a carousel,
row-to-row in a list) and `counterAxisSpacing` (row gap in a wrapped grid). Plus the four `padding*`.
Apply all of them from the spec, mapped to `theme.spaces.space<N>`; these gaps are the most commonly
approximated values and the section-to-section gap is the most commonly forgotten.

## What node JSON gives you (verified)

- TEXT nodes: `style.fontFamily`, `style.fontWeight`, `style.fontSize`, `style.lineHeightPx`,
  `style.letterSpacing`; `fills[].color` (rgba 0-1) + `fills[].opacity`.
- FRAME/RECT/INSTANCE: `cornerRadius`, `itemSpacing` (auto-layout gap), `paddingLeft/Top/Right/Bottom`,
  `fills` (SOLID or GRADIENT_LINEAR/RADIAL with `gradientStops`, or IMAGE with `imageRef`), `effects`
  (LAYER_BLUR, BACKGROUND_BLUR, DROP_SHADOW with `color`/`offset`/`radius`).
- INSTANCE nodes: `componentId` -> resolve name via the response's top-level `components` map. This
  is how the component matrix is built (which Figma component appears on which screens).

## What you do NOT get

- Variable names/values: `GET /v1/files/<key>/variables/local` returns **403** (Enterprise-gated).
- Published styles: `GET /v1/files/<key>/styles` returns 200 but `meta.styles: []` for this file.
- => Map by resolved VALUE to repo tokens. Use element context (text vs bg vs border vs icon) + the
  screenshot to disambiguate when two repo tokens share a value.

## Rate limits

Aggressive on large pulls. After a big node fetch, image renders and further node calls may 429 for
a few minutes. Fetch one frame at a time, space retries ~120s, and cache results in the scratchpad
so you never re-fetch the same node in a session.

## Convert Figma rgba (0-1) to hex

`#RRGGBB` from `round(r*255), round(g*255), round(b*255)`; keep the alpha separately (Figma fill
opacity or color.a) for the token match (e.g. white @ 0.05 -> a frosted surface token).
