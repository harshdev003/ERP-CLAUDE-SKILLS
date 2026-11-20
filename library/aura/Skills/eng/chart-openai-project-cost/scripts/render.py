#!/usr/bin/env python3
"""Render a self-contained HTML report with daily + weekly OpenAI project cost charts.

Reads the JSON emitted by `costs.py` (or explicit --daily/--weekly JSON) and
writes one theme-aware HTML file: a tile row (total, avg/day, peak day, peak
week) plus two hand-built inline SVG bar charts (daily, weekly). No external
or CDN dependencies — Artifacts enforce a strict CSP, so everything is inline
SVG + inline CSS/JS. Aggregate project-level $ only, no UID/PII.

Stdlib only. No sibling-skill imports (self-contained per Architecture
principle 1).

Usage:
    python3 render.py --data /tmp/insights_costs.json --out /tmp/insights_report.html
    python3 render.py --daily /tmp/daily.json --weekly /tmp/weekly.json --out /tmp/report.html --title "Insights — OpenAI cost"
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import sys

SKILL_ID = "eng:chart-openai-project-cost"


def _skill_ident() -> str:
    """`<plugin>:<skill>:<version>` for the report footer, version from the sibling
    SKILL.md `metadata.version` (scoped to the metadata block so a `version:` token
    in the description can't shadow it). Falls back to the bare id if unreadable."""
    try:
        text = open(os.path.join(os.path.dirname(__file__), os.pardir, "SKILL.md"),
                    encoding="utf-8").read()
    except OSError:
        return SKILL_ID
    fm = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    fm = fm.group(1) if fm else text
    block = re.search(r"(?m)^metadata:[ \t]*\n((?:[ \t]+\S.*\n?)+)", fm)
    if not block:
        return SKILL_ID
    ver = re.search(r'''(?m)^[ \t]+version:[ \t]*["']?([^"'\s]+)["']?[ \t]*$''', block.group(1))
    return f"{SKILL_ID}:{ver.group(1)}" if ver else SKILL_ID


def _load(path):
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:  # noqa: BLE001
        print(f"  ! failed to read {path}: {e}", file=sys.stderr)
        return None


def usd(v, d=2):
    try:
        return "$" + format(float(v), f",.{d}f")
    except (TypeError, ValueError):
        return "—"


def esc(s):
    return html.escape(str(s), quote=True)


def nice_max(v: float) -> float:
    if v <= 0:
        return 1.0
    import math
    e = 10 ** math.floor(math.log10(v))
    n = v / e
    s = 1 if n <= 1 else 2 if n <= 2 else 5 if n <= 5 else 10
    return s * e


# --------------------------------------------------------------------------
# SVG bar chart builder
# --------------------------------------------------------------------------

def bar_chart(
    points: list[dict],
    value_key: str,
    label_key: str,
    partial_flags: list[bool] | None = None,
    w: int = 760,
    h: int = 300,
    bar_cls: str = "cost",
    x_tick_step: int | None = None,
) -> str:
    """One hand-built inline SVG bar chart. `points` is a list of dicts with
    `value_key` (float) and `label_key` (str). `partial_flags`, if given,
    marks the corresponding bar with a hatched/lighter fill + dashed outline."""
    if not points:
        return '<div class="nd">no data</div>'

    n = len(points)
    p_l, p_r, p_t, p_b = 52, 14, 14, 30
    pw, ph = w - p_l - p_r, h - p_t - p_b
    values = [float(p.get(value_key) or 0) for p in points]
    max_v = nice_max(max(values) if values else 0)
    gwid = pw / n
    # Cap bar width at 90% of the slot so a long series (default --all can be 500+ daily
    # points) never paints over its neighbours; the 2px floor only applies while it fits.
    bw = min(gwid * 0.9, max(2.0, gwid * 0.68))

    def bx(i):
        return p_l + gwid * (i + 0.5)

    def sy(v):
        return p_t + ph - (v / max_v * ph if max_v else 0)

    g = []
    for t in range(5):
        v = max_v * t / 4
        yy = sy(v)
        g.append(f'<line class="grid" x1="{p_l}" y1="{yy:.1f}" x2="{w - p_r}" y2="{yy:.1f}"/>')
        g.append(f'<text class="tick ty" x="{p_l - 8}" y="{yy:.1f}">{usd(v, 0)}</text>')

    for i, p in enumerate(points):
        v = values[i]
        x0 = bx(i) - bw / 2
        y0 = sy(v)
        bh = max(0.0, sy(0) - y0)
        partial = bool(partial_flags[i]) if partial_flags else False
        cls = f"f-{bar_cls}" + (" f-partial" if partial else "")
        g.append(f'<rect class="{cls}" x="{x0:.1f}" y="{y0:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="2"/>')

    step = x_tick_step or max(1, round(n / 8))
    for i, p in enumerate(points):
        if i % step == 0 or i == n - 1:
            g.append(f'<text class="tick tx" x="{bx(i):.1f}" y="{h - 9}">{esc(p.get(label_key, ""))}</text>')

    g.append(f'<line class="axis" x1="{p_l}" y1="{sy(0):.1f}" x2="{w - p_r}" y2="{sy(0):.1f}"/>')
    return f'<svg viewBox="0 0 {w} {h}" class="chart" role="img">' + "".join(g) + "</svg>"


# --------------------------------------------------------------------------
# Analysis (tiles)
# --------------------------------------------------------------------------

def build_tiles(daily: list[dict], weekly: list[dict], monthly: list[dict], total: float) -> list[dict]:
    tiles = []
    tiles.append({"k": "Total cost", "v": usd(total, 2), "d": f"{len(daily)} days" if daily else "", "cls": "cost"})
    if daily:
        avg = sum(e.get("cost", 0) for e in daily) / len(daily)
        peak = max(daily, key=lambda e: e.get("cost", 0))
        tiles.append({"k": "Avg / day", "v": usd(avg, 2), "d": "", "cls": ""})
        tiles.append({"k": "Peak day", "v": usd(peak.get("cost", 0), 2), "d": peak.get("date", ""), "cls": "acc"})
    if weekly:
        peak_w = max(weekly, key=lambda e: e.get("cost", 0))
        note = "partial week" if peak_w.get("partial") else ""
        tiles.append({"k": "Peak week", "v": usd(peak_w.get("cost", 0), 2), "d": f"wk of {peak_w.get('week_start', '')}" + (f" · {note}" if note else ""), "cls": "acc"})
    if monthly:
        peak_m = max(monthly, key=lambda e: e.get("cost", 0))
        note = "partial month" if peak_m.get("partial") else ""
        tiles.append({"k": "Peak month", "v": usd(peak_m.get("cost", 0), 2), "d": (peak_m.get("month", "")) + (f" · {note}" if note else ""), "cls": "acc"})
    return tiles


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------

STYLE = """
:root{
  --bg:#f7f9fa;--surface:#fff;--surface-2:#eef2f5;--ink:#16202a;--muted:#5a6b78;--faint:#8798a3;
  --line:#dde5ea;--grid:#e9eef1;--accent:#0d7d8c;--cost:#df7f38;--cost-partial:#efc79b;
  --shadow:0 1px 2px rgba(20,32,42,.06),0 8px 24px -12px rgba(20,32,42,.16);
  --mono:ui-monospace,"SF Mono","JetBrains Mono",Menlo,Consolas,monospace;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,"Helvetica Neue",sans-serif;
}
@media (prefers-color-scheme:dark){:root{
  --bg:#0d1117;--surface:#161c23;--surface-2:#1c242c;--ink:#e8eff3;--muted:#93a4b0;--faint:#6b7c88;
  --line:#28323b;--grid:#232c34;--accent:#2fbccb;--cost:#efa25c;--cost-partial:#7a5a34;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 10px 30px -14px rgba(0,0,0,.6);
}}
:root[data-theme="light"]{
  --bg:#f7f9fa;--surface:#fff;--surface-2:#eef2f5;--ink:#16202a;--muted:#5a6b78;--faint:#8798a3;
  --line:#dde5ea;--grid:#e9eef1;--accent:#0d7d8c;--cost:#df7f38;--cost-partial:#efc79b;
  --shadow:0 1px 2px rgba(20,32,42,.06),0 8px 24px -12px rgba(20,32,42,.16);
}
:root[data-theme="dark"]{
  --bg:#0d1117;--surface:#161c23;--surface-2:#1c242c;--ink:#e8eff3;--muted:#93a4b0;--faint:#6b7c88;
  --line:#28323b;--grid:#232c34;--accent:#2fbccb;--cost:#efa25c;--cost-partial:#7a5a34;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 10px 30px -14px rgba(0,0,0,.6);
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);line-height:1.55;font-size:15px;-webkit-font-smoothing:antialiased}
.wrap{max-width:900px;margin:0 auto;padding:48px 24px 80px}
.num{font-variant-numeric:tabular-nums}
header{margin-bottom:30px}
.eyebrow{font-family:var(--mono);font-size:11.5px;letter-spacing:.14em;text-transform:uppercase;color:var(--accent);font-weight:600}
h1{font-size:clamp(25px,4vw,36px);line-height:1.08;letter-spacing:-.02em;font-weight:800;margin:.28em 0 .12em;text-wrap:balance}
.sub{color:var(--muted);font-size:15px;max-width:64ch}
.prov{display:flex;flex-wrap:wrap;gap:8px;margin-top:16px}
.chip{font-family:var(--mono);font-size:11.5px;color:var(--muted);background:var(--surface-2);border:1px solid var(--line);border-radius:999px;padding:4px 11px}
.chip b{color:var(--ink);font-weight:600}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(158px,1fr));gap:12px;margin:22px 0 6px}
.tile{background:var(--surface);border:1px solid var(--line);border-radius:13px;padding:14px 15px;box-shadow:var(--shadow)}
.tile .k{font-size:11.5px;color:var(--muted)}
.tile .v{font-family:var(--mono);font-size:22px;font-weight:600;letter-spacing:-.01em;margin-top:3px}
.tile .d{font-size:11.5px;color:var(--faint);margin-top:3px}
.tile .v.acc{color:var(--accent)}.tile .v.cost{color:var(--cost)}
.cards{display:grid;gap:20px;margin-top:22px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:16px;padding:22px 22px 20px;box-shadow:var(--shadow)}
.card-hd{display:flex;justify-content:space-between;align-items:baseline;gap:14px;flex-wrap:wrap}
.skill{font-family:var(--mono);font-size:12px;color:var(--accent);font-weight:600}
.tag{font-family:var(--mono);font-size:10.5px;color:var(--faint);border:1px solid var(--line);border-radius:6px;padding:2px 7px}
.card h2{font-size:18px;font-weight:700;letter-spacing:-.01em;margin:8px 0 2px}
.q{color:var(--muted);font-size:13px;margin:0 0 12px}
.legend{display:flex;flex-wrap:wrap;gap:14px;margin:2px 0 10px;font-size:12.5px;color:var(--muted)}
.legend span{display:inline-flex;align-items:center;gap:6px}
.sw{width:12px;height:12px;border-radius:3px;display:inline-block}
.sw.partial{background:var(--cost-partial);border:1px dashed var(--cost)}
.chart{width:100%;height:auto;display:block;overflow:visible}
.grid{stroke:var(--grid);stroke-width:1}.axis{stroke:var(--line);stroke-width:1}
.tick{fill:var(--faint);font-size:11px;font-family:var(--mono)}
.ty{text-anchor:end;dominant-baseline:middle}.tx{text-anchor:middle}
.f-cost{fill:var(--cost)}
.f-partial{fill:var(--cost-partial);stroke:var(--cost);stroke-width:1;stroke-dasharray:3 2}
.note{font-size:12px;color:var(--faint);margin-top:8px}
.nd{color:var(--faint);font-size:13px;font-family:var(--mono);padding:24px 0;text-align:center}
footer{margin-top:36px;color:var(--faint);font-size:12px;font-family:var(--mono);text-align:center}
"""


def render_html(title: str, meta: dict, daily: list[dict], weekly: list[dict], monthly: list[dict], total: float) -> str:
    tiles = build_tiles(daily, weekly, monthly, total)
    tiles_html = "".join(
        f'<div class="tile"><div class="k">{esc(t["k"])}</div>'
        f'<div class="v {t.get("cls","")} num">{esc(t["v"])}</div>'
        f'<div class="d">{esc(t.get("d",""))}</div></div>'
        for t in tiles
    )

    # Strip the leading year (YYYY-) only when the whole series is within one calendar year;
    # a multi-year window (default --all can span years) keeps YYYY-MM-DD so the ~8 thinned
    # axis labels stay unambiguous — consistent with the monthly chart, which keeps YYYY-MM.
    _d_strip = len({e["date"][:4] for e in daily}) <= 1
    daily_labels = [{"date": (e["date"][5:] if _d_strip else e["date"]), "cost": e["cost"]} for e in daily]
    daily_svg = bar_chart(daily_labels, "cost", "date", w=820, h=300) if daily else '<div class="nd">no data in this window</div>'

    _w_strip = len({e["week_start"][:4] for e in weekly}) <= 1
    weekly_labels = [{"week": (e["week_start"][5:] if _w_strip else e["week_start"]), "cost": e["cost"]} for e in weekly]
    weekly_partial = [bool(e.get("partial")) for e in weekly]
    weekly_svg = (
        bar_chart(weekly_labels, "cost", "week", partial_flags=weekly_partial, w=820, h=300, x_tick_step=1)
        if weekly else '<div class="nd">no data in this window</div>'
    )

    monthly_labels = [{"month": e["month"], "cost": e["cost"]} for e in monthly]
    monthly_partial = [bool(e.get("partial")) for e in monthly]
    monthly_svg = (
        bar_chart(monthly_labels, "cost", "month", partial_flags=monthly_partial, w=820, h=300, x_tick_step=1)
        if monthly else '<div class="nd">no data in this window</div>'
    )

    project = meta.get("project", "")
    project_id = meta.get("project_id", "")
    window = (meta.get("window") or {})
    window_label = window.get("label", "")
    generated = meta.get("generated", "")

    partial_note_w = ""
    if any(e.get("partial") for e in weekly):
        partial_note_w = '<p class="note">Hatched bars are partial weeks (fewer than 7 days of data in the window).</p>'

    partial_note_m = ""
    if any(e.get("partial") for e in monthly):
        partial_note_m = '<p class="note">Hatched bars are partial months (fewer than the full calendar month present in the window).</p>'

    prov_chips = [
        f'project <b>{esc(project)}</b>',
        f'window <b>{esc(window_label)}</b>',
    ]
    if project_id:
        prov_chips.append(f'<b>{esc(project_id)}</b>')
    prov_chips.append('aggregate-only · no UIDs')
    prov_html = "".join(f'<span class="chip">{c}</span>' for c in prov_chips)

    body = f"""<title>{esc(title)}</title>
<style>{STYLE}</style>
<div class="wrap">
  <header>
    <div class="eyebrow">OpenAI project spend</div>
    <h1>{esc(title)}</h1>
    <p class="sub">Daily and weekly cost for the <b>{esc(project)}</b> OpenAI project, straight from
      the Costs API — same numbers OpenAI bills off.</p>
    <div class="prov">{prov_html}</div>
  </header>

  <section class="tiles" aria-label="Key numbers">{tiles_html}</section>

  <div class="cards">
    <article class="card">
      <div class="card-hd"><span class="skill">eng:chart-openai-project-cost</span><span class="tag">daily</span></div>
      <h2>Daily cost</h2>
      <p class="q">Spend per day for this project over the window.</p>
      <div class="legend"><span><i class="sw" style="background:var(--cost)"></i>Daily cost</span></div>
      <div class="viz">{daily_svg}</div>
    </article>

    <article class="card">
      <div class="card-hd"><span class="skill">eng:chart-openai-project-cost</span><span class="tag">weekly</span></div>
      <h2>Weekly cost (Monday-anchored)</h2>
      <p class="q">Same spend rolled up by ISO week.</p>
      <div class="legend">
        <span><i class="sw" style="background:var(--cost)"></i>Full week (7 days)</span>
        <span><i class="sw partial"></i>Partial week</span>
      </div>
      <div class="viz">{weekly_svg}</div>
      {partial_note_w}
    </article>

    <article class="card">
      <div class="card-hd"><span class="skill">eng:chart-openai-project-cost</span><span class="tag">monthly</span></div>
      <h2>Monthly cost (calendar month)</h2>
      <p class="q">Same spend rolled up by calendar month.</p>
      <div class="legend">
        <span><i class="sw" style="background:var(--cost)"></i>Full month</span>
        <span><i class="sw partial"></i>Partial month</span>
      </div>
      <div class="viz">{monthly_svg}</div>
      {partial_note_m}
    </article>
  </div>

  <footer>Generated {esc(generated)} · {esc(_skill_ident())} · OpenAI Costs API (/v1/organization/costs) · aggregate project-level $, no user identifiers</footer>
</div>
"""
    return body


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(description="Render OpenAI project cost charts to a self-contained HTML file")
    ap.add_argument("--data", help="Path to the JSON emitted by costs.py (has meta/daily/weekly/monthly/total)")
    ap.add_argument("--daily", help="Path to a JSON file with just the daily series (list of {date,cost})")
    ap.add_argument("--weekly", help="Path to a JSON file with just the weekly series (list of {week_start,cost,days,partial})")
    ap.add_argument("--monthly", help="Path to a JSON file with just the monthly series (list of {month,cost,days,partial})")
    ap.add_argument("--out", required=True, help="Output HTML path")
    ap.add_argument("--title", help="Report title (defaults to '<project> — OpenAI cost')")
    args = ap.parse_args()

    meta: dict = {}
    daily: list[dict] = []
    weekly: list[dict] = []
    monthly: list[dict] = []
    total = 0.0

    if args.data:
        data = _load(args.data)
        if data is None:
            sys.exit(f"--data {args.data}: file is missing or unreadable — refusing to "
                     f"render an empty report (check the path / the costs.py run that produced it)")
        meta = data.get("meta", {})
        daily = data.get("daily", [])
        weekly = data.get("weekly", [])
        monthly = data.get("monthly", [])
        total = data.get("total", sum(e.get("cost", 0) for e in daily))
    else:
        daily = _load(args.daily) or []
        weekly = _load(args.weekly) or []
        monthly = _load(args.monthly) or []
        total = sum(e.get("cost", 0) for e in daily)

    if not meta.get("generated"):
        meta["generated"] = dt.datetime.now(dt.timezone.utc).isoformat()

    title = args.title or f"{meta.get('project', 'OpenAI project')} — OpenAI cost"
    html_out = render_html(title, meta, daily, weekly, monthly, total)

    with open(args.out, "w") as f:
        f.write(html_out)

    print(f"Wrote {args.out} ({len(daily)} daily points, {len(weekly)} weekly points, "
          f"{len(monthly)} monthly points, total {usd(total)})")


if __name__ == "__main__":
    main()
