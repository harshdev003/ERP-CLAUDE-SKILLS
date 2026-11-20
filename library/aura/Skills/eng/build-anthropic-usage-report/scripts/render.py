#!/usr/bin/env python3
"""Render one self-contained HTML report from collect.py's JSON. Stdlib only.

Charts are inline SVG built in the page, so the artifact needs no CDN and works
offline. Four sections, in the order a reader needs them: seat fit (the
decision), adoption (is this growing), model mix (the cost lever), concentration
(key-person risk).

Charting rules this file is built to, from the `dataviz` skill:

* **No dual-axis chart anywhere.** Tokens and active users are different scales,
  so they are two stacked plots sharing an x-axis, never one plot with two y-axes.
* **Categorical hues in fixed slot order, never cycled.** Four slots plus an
  "Other" fold; a fifth model never mints a new hue.
* **Dark mode is selected, not flipped** — its own steps, validated against the
  dark surface.
* Two light-mode hues sit under 3:1 against the surface, so the **relief rule**
  applies: every series is directly labelled and a table view exists.
* A legend is present wherever two or more series are, so identity is never
  carried by colour alone.

Palette validated with the skill's own validator, both modes:
light `#2a78d6,#eb6834,#1baf7a,#eda100` — worst adjacent CVD ΔE 9.1, normal 22.9;
dark `#3987e5,#d95926,#199e70,#c98500` — worst adjacent CVD ΔE 8.4, normal 19.8.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _skill_ident() -> str:
    """`<plugin>:<skill>:<version>` read from the sibling SKILL.md at run time.

    Read from the frontmatter slice only — this file's own docstring and the
    SKILL.md body both contain `version:`-looking text, and a whole-file grep
    would happily return the wrong one.
    """
    try:
        p = os.path.join(os.path.dirname(HERE), "SKILL.md")
        with open(p, encoding="utf-8") as fh:
            parts = fh.read().split("---", 2)
        m = re.search(r'version:\s*"([^"]+)"', parts[1])
        return f"eng:build-anthropic-usage-report:{m.group(1)}" if m else \
            "eng:build-anthropic-usage-report"
    except Exception:  # noqa: BLE001
        return "eng:build-anthropic-usage-report"


def e(s) -> str:
    return html.escape(str(s if s is not None else ""))


def json_for_script(obj) -> str:
    """JSON safe to embed inside a <script> tag.

    `json.dumps` leaves `<` and `>` alone, so a value containing `</script>`
    closes the tag early and everything after it is parsed as markup. The data
    here is an external export — emails, product names, model strings — so no
    field can be assumed tag-free. Escaping to `\\uXXXX` keeps the JSON valid
    and identical once parsed.

    U+2028/U+2029 need no handling here: they are literal newlines inside a JS
    string, but `json.dumps` defaults to `ensure_ascii=True` and has already
    escaped every non-ASCII character by this point.
    """
    return (json.dumps(obj)
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
            .replace("&", "\\u0026"))


def usd(v, d=0) -> str:
    try:
        return f"${float(v):,.{d}f}"
    except (TypeError, ValueError):
        return "—"


def toks(n) -> str:
    n = float(n or 0)
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if n >= div:
            return f"{n/div:.1f}{unit}"
    return f"{n:.0f}"


CSS = """
:root{
  --surface-0:#ffffff; --surface-1:#fcfcfb; --surface-2:#f4f4f1;
  --line:#e3e2dd; --text-primary:#0b0b0b; --text-secondary:#52514e; --text-muted:#78766f;
  --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a; --s4:#eda100; --s5:#e87ba4;
  --s6:#008300; --s7:#4a3aa7; --s8:#e34948; --sother:#8a8780;
  --good:#1a7f4b; --warn:#b06d00; --crit:#c0342f;
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    --surface-0:#121211; --surface-1:#1a1a19; --surface-2:#242422;
    --line:#35342f; --text-primary:#ffffff; --text-secondary:#c3c2b7; --text-muted:#96948a;
    --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#c98500; --s5:#d55181;
    --s6:#008300; --s7:#9085e9; --s8:#e66767; --sother:#9a978f;
    --good:#4ec38a; --warn:#e0a54a; --crit:#f08079;
  }
}
:root[data-theme="dark"]{
  --surface-0:#121211; --surface-1:#1a1a19; --surface-2:#242422;
  --line:#35342f; --text-primary:#ffffff; --text-secondary:#c3c2b7; --text-muted:#96948a;
  --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#c98500; --s5:#d55181;
  --s6:#008300; --s7:#9085e9; --s8:#e66767; --sother:#9a978f;
  --good:#4ec38a; --warn:#e0a54a; --crit:#f08079;
}
*{box-sizing:border-box}
body{margin:0;background:var(--surface-0);color:var(--text-primary);
  font:15px/1.55 ui-sans-serif,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  padding-block:32px;padding-left:20px;padding-right:20px}
.wrap{max-width:1040px;margin:0 auto}
h1{font-size:26px;margin:0 0 4px;letter-spacing:-.01em}
h2{font-size:18px;margin:0 0 4px;letter-spacing:-.01em}
.sub{color:var(--text-secondary);font-size:14px;margin:0 0 24px}
.card{background:var(--surface-1);border:1px solid var(--line);border-radius:12px;
  padding:20px;margin:0 0 20px}
.lede{color:var(--text-secondary);font-size:14px;margin:2px 0 16px;max-width:76ch}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:0 0 20px}
.tile{background:var(--surface-1);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.tile .v{font-size:24px;font-weight:650;letter-spacing:-.02em}
.tile .k{color:var(--text-secondary);font-size:12px;margin-top:2px}
.tile .n{color:var(--text-muted);font-size:11px;margin-top:6px}
.banner{border-radius:10px;padding:11px 14px;margin:0 0 18px;font-size:13.5px;
  border:1px solid var(--line);background:var(--surface-2);color:var(--text-secondary)}
.banner.warn{border-color:var(--warn);color:var(--text-primary)}
.banner.crit{border-color:var(--crit);color:var(--text-primary)}
.banner b{color:var(--text-primary)}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th,td{padding:7px 8px;text-align:right;border-bottom:1px solid var(--line);white-space:nowrap}
th:first-child,td:first-child{text-align:left;white-space:normal}
th{color:var(--text-secondary);font-weight:600;font-size:12px;text-transform:uppercase;
  letter-spacing:.04em}
tbody tr:last-child td{border-bottom:0}
tfoot td{font-weight:650;border-top:2px solid var(--line);border-bottom:0}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
.chart{width:100%;height:auto;display:block;overflow:visible}
.grid line{stroke:var(--line);stroke-width:1}
.axis text{fill:var(--text-muted);font-size:11px}
.vlabel{fill:var(--text-secondary);font-size:11px;font-weight:600}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:8px;
  overflow:hidden;margin:0 0 12px}
.seg button{appearance:none;border:0;background:var(--surface-1);cursor:pointer;
  font:600 12px/1 ui-sans-serif,system-ui;color:var(--text-secondary);
  padding:7px 14px;letter-spacing:.02em}
.seg button+button{border-left:1px solid var(--line)}
.seg button:hover{color:var(--text-primary)}
.seg button.on{background:var(--surface-2);color:var(--text-primary)}
.seg button:focus-visible{outline:2px solid var(--s1);outline-offset:-2px}
.legend{display:flex;flex-wrap:wrap;gap:14px;margin:12px 0 0;font-size:12.5px;
  color:var(--text-secondary)}
.legend i{width:11px;height:11px;border-radius:3px;display:inline-block;margin-right:6px;
  vertical-align:-1px}
.pill{display:inline-block;font-size:11px;padding:1px 8px;border-radius:999px;
  border:1px solid var(--line);color:var(--text-secondary);background:var(--surface-2)}
.pill.prem{border-color:var(--s1);color:var(--s1)}
.pill.std{border-color:var(--s2);color:var(--s2)}
.flag{font-size:12px;color:var(--warn)}
.flag.up{color:var(--good)}
details{margin-top:14px}
summary{cursor:pointer;color:var(--text-secondary);font-size:13px}
footer{color:var(--text-muted);font-size:12px;margin-top:28px;text-align:center}
.note{color:var(--text-muted);font-size:12px;margin-top:10px;max-width:76ch}
@media (max-width:620px){ body{font-size:14px} h1{font-size:22px} .tile .v{font-size:20px} }
"""

JS = r"""
(function(){
  var D = window.__DATA__;
  var css = getComputedStyle(document.documentElement);
  function c(n){ return css.getPropertyValue(n).trim() || '#888'; }
  // Eight categorical slots, in fixed order, plus a neutral reserved for the
  // 'Other' fold. A 9th entity never mints a hue — it folds. The eight-slot
  // order validates on the ADJACENT pairlist (stacks and bars), which is why
  // a stacked chart may use all eight where an all-pairs form could not.
  var SER = ['--s1','--s2','--s3','--s4','--s5','--s6','--s7','--s8'];
  function slot(i, name){ return c(name === 'Other' ? '--sother' : SER[Math.min(i, 7)]); }

  function esc(s){ return String(s).replace(/[&<>]/g, function(m){
    return {'&':'&amp;','<':'&lt;','>':'&gt;'}[m]; }); }
  function fmtT(n){ n=+n||0;
    if(n>=1e9) return (n/1e9).toFixed(1)+'B';
    if(n>=1e6) return (n/1e6).toFixed(1)+'M';
    if(n>=1e3) return (n/1e3).toFixed(1)+'K'; return String(Math.round(n)); }
  function fmtU(n){ n=+n||0;
    if(n>=1e6) return '$'+(n/1e6).toFixed(1)+'M';
    if(n>=1000) return '$'+Math.round(n/1000)+'k'; return '$'+Math.round(n); }
  // fmtU rounds to thousands, which is right for a derived counterfactual
  // spanning four orders of magnitude and wrong for an invoice: $1,476 and
  // $1,529 both render as "$1k"/"$2k", so two nearly equal months read as a
  // twofold change on the one card whose job is to say what Claude costs.
  // Money that was actually charged is shown to the dollar.
  function exactU(n){
    n = Math.round(+n || 0);
    return '$' + n.toLocaleString('en-US');
  }

  // Period labels must follow the grain. Truncating to 7 characters collapses
  // every week in a month to the same label ("2026-08"), so a weekly chart drew
  // four identical ticks and four identical tooltips. --grain week is a
  // documented option, so the label reads the grain rather than assuming month.
  var GRAIN = (D.window && D.window.grain) || 'month';
  function shortP(p){
    var s = String(p);
    return GRAIN === 'month' ? s.slice(0,7) : s.slice(5);
  }

  // Weekly grain puts ~29 buckets on the same 980px axis a monthly report fills
  // with 7, and a label is about as wide as the bar. Draw every Nth tick so they
  // stay legible; every bucket keeps its own tooltip, so nothing is lost — only
  // the printed tick thins out.
  function tickStep(n, w){
    var per = w / Math.max(n, 1);
    return Math.max(1, Math.ceil(34 / per));
  }

  // ---- tooltip (one shared element; every chart uses it) ----
  var tip = document.createElement('div');
  tip.style.cssText = 'position:fixed;pointer-events:none;z-index:99;opacity:0;'+
    'background:'+c('--surface-0')+';color:'+c('--text-primary')+';border:1px solid '+
    c('--line')+';border-radius:8px;padding:7px 10px;font:12.5px/1.45 ui-sans-serif,system-ui;'+
    'box-shadow:0 4px 16px rgba(0,0,0,.18);max-width:280px;transition:opacity .08s';
  document.body.appendChild(tip);
  function show(ev, html){
    tip.innerHTML = html; tip.style.opacity = 1;
    var r = tip.getBoundingClientRect();
    var x = ev.clientX + 14, y = ev.clientY + 14;
    if (x + r.width > innerWidth - 8) x = ev.clientX - r.width - 14;
    if (y + r.height > innerHeight - 8) y = ev.clientY - r.height - 14;
    tip.style.left = x+'px'; tip.style.top = y+'px';
  }
  function hide(){ tip.style.opacity = 0; }
  function wire(el, htmlFn){
    el.style.cursor='default';
    el.addEventListener('mousemove', function(ev){ show(ev, htmlFn()); });
    el.addEventListener('mouseleave', hide);
  }

  function svg(inner, w, h, cls){
    return '<svg viewBox="0 0 '+w+' '+h+'" class="chart '+(cls||'')+
           '" preserveAspectRatio="xMidYMid meet" role="img">'+inner+'</svg>';
  }

  // ================= 1. SEAT FIT =================
  // Horizontal bars: value per month per user, with the seat price as a marker.
  // One series, so no legend; tier is carried by colour AND a text pill in the
  // table below, never colour alone.
  (function(){
    var host = document.getElementById('seatfit'); if(!host) return;
    var rows = D.seat_fit.filter(function(r){ return r.value_per_month > 0 || r.active_days > 0; });
    if(!rows.length){ host.innerHTML = '<p class="note">No seat data.</p>'; return; }
    var W=980, rowH=26, pT=16, pB=30, pL=178, pR=132;
    var H = pT + rows.length*rowH + pB;
    var max = Math.max.apply(null, rows.map(function(r){ return r.value_per_month; }));
    var lg = function(v){ return Math.log10(Math.max(v,1)+1); };
    var maxL = lg(max);
    var bx = function(v){ return pL + (W-pL-pR) * (lg(v)/maxL); };
    var g = '';
    // log gridlines, because the range spans four orders of magnitude
    [1,10,100,1000,10000].forEach(function(t){
      if(t > max*1.6) return;
      var x = bx(t);
      g += '<line x1="'+x+'" y1="'+pT+'" x2="'+x+'" y2="'+(H-pB)+'" stroke="'+c('--line')+
           '" stroke-width="1"/>';
      g += '<text class="axis" x="'+x+'" y="'+(H-pB+15)+'" text-anchor="middle" fill="'+
           c('--text-muted')+'" font-size="11">'+fmtU(t)+'</text>';
    });
    rows.forEach(function(r,i){
      var y = pT + i*rowH, bh = rowH-9;
      var col = (r.tiers||[r.tier]).indexOf('premium')>=0 ? c('--s1') : c('--s2');
      var w = Math.max(bx(r.value_per_month)-pL, 2);
      g += '<rect x="'+pL+'" y="'+y+'" width="'+w+'" height="'+bh+'" rx="4" fill="'+col+'"/>';
      // seat price marker — the line the bar has to clear to pay for itself
      var sx = bx(r.seat_usd);
      if(r.seat_usd>0){
        g += '<line x1="'+sx+'" y1="'+(y-2)+'" x2="'+sx+'" y2="'+(y+bh+2)+'" stroke="'+
             c('--text-primary')+'" stroke-width="2" stroke-dasharray="3 2" opacity=".55"/>';
      }
      g += '<text class="axis" x="'+(pL-10)+'" y="'+(y+bh-3)+'" text-anchor="end" fill="'+
           c('--text-secondary')+'" font-size="12">'+esc(r.user.split('@')[0])+'</text>';
      // Label the quantity the bar actually encodes (value), then the ratio.
      // Showing the ratio alone put a number on the bar that did not describe
      // its length — a standard seat can out-rank a premium one on ratio while
      // drawing a much shorter bar, so the two read as contradicting each other.
      g += '<text class="vlabel" x="'+(Math.min(bx(r.value_per_month)+7, W-pR+56))+'" y="'+
           (y+bh-3)+'" fill="'+c('--text-secondary')+'" font-size="11">'+
           fmtU(r.value_per_month)+(r.ratio!=null? '  ·  '+r.ratio+'x' : '')+'</text>';
      g += '<rect class="hit" data-i="'+i+'" x="'+pL+'" y="'+(y-3)+'" width="'+(W-pL-pR+56)+
           '" height="'+rowH+'" fill="transparent"/>';
    });
    host.innerHTML = svg(g, W, H) +
      '<div class="legend">'+
      '<span><i style="background:'+c('--s1')+'"></i>premium seat</span>'+
      '<span><i style="background:'+c('--s2')+'"></i>standard seat</span>'+
      '<span><i style="background:'+c('--text-primary')+';opacity:.55"></i>seat price</span>'+
      '</div>';
    host.querySelectorAll('.hit').forEach(function(el){
      var r = rows[+el.dataset.i];
      wire(el, function(){
        return '<b>'+esc(r.user)+'</b><br>'+esc(r.tier)+' seat · '+fmtU(r.seat_usd)+'/mo<br>'+
          'value '+fmtU(r.value_per_month)+'/mo ('+(r.ratio!=null?r.ratio+'x':'—')+')<br>'+
          fmtT(r.tokens)+' tokens · active '+r.active_days+' days<br>'+
          'billed overage '+fmtU(r.billed);
      });
    });
  })();

  // ============ 2. ADOPTION — two stacked plots, never a dual axis ============
  (function(){
    var host = document.getElementById('adoption'); if(!host) return;
    var A = D.adoption; if(!A.length){ host.innerHTML='<p class="note">No data.</p>'; return; }
    var W=980, H=200, pT=14, pB=26, pL=52, pR=14;
    function plot(key, fmt, colour, label){
      var vals = A.map(function(x){ return +x[key]||0; });
      var max = Math.max.apply(null, vals) || 1;
      var n = A.length;
      var bw = (W-pL-pR)/n;
      var g = '';
      [0,.5,1].forEach(function(f){
        var y = pT + (H-pT-pB)*(1-f);
        g += '<line x1="'+pL+'" y1="'+y+'" x2="'+(W-pR)+'" y2="'+y+'" stroke="'+c('--line')+
             '" stroke-width="1"/>';
        g += '<text class="axis" x="'+(pL-8)+'" y="'+(y+4)+'" text-anchor="end" fill="'+
             c('--text-muted')+'" font-size="11">'+fmt(max*f)+'</text>';
      });
      var tstep = tickStep(A.length, W-pL-pR);
      A.forEach(function(x,i){
        var v = +x[key]||0, bh = (H-pT-pB)*(v/max);
        var bx0 = pL + i*bw + 3;
        g += '<rect x="'+bx0+'" y="'+(H-pB-bh)+'" width="'+Math.max(bw-6,2)+'" height="'+
             Math.max(bh,1)+'" rx="4" fill="'+colour+'"/>';
        if(i % tstep === 0){
          g += '<text class="axis" x="'+(bx0+(bw-6)/2)+'" y="'+(H-pB+15)+'" text-anchor="middle" fill="'+
               c('--text-muted')+'" font-size="10.5">'+esc(shortP(x.period))+'</text>';
        }
        g += '<rect class="hit-'+key+'" data-i="'+i+'" x="'+(pL+i*bw)+'" y="'+pT+'" width="'+bw+
             '" height="'+(H-pT-pB)+'" fill="transparent"/>';
      });
      return {svg: svg(g, W, H), label: label};
    }
    var p1 = plot('tokens', fmtT, c('--s1'), 'Tokens');
    var p2 = plot('users', function(v){ return Math.round(v); }, c('--s3'), 'Active people');
    host.innerHTML =
      '<div class="note" style="margin:0 0 4px">Tokens</div>'+p1.svg+
      '<div class="note" style="margin:14px 0 4px">Active people</div>'+p2.svg;
    ['tokens','users'].forEach(function(k){
      host.querySelectorAll('.hit-'+k).forEach(function(el){
        var x = A[+el.dataset.i];
        wire(el, function(){
          return '<b>'+esc(shortP(x.period))+'</b><br>'+fmtT(x.tokens)+' tokens<br>'+
                 x.users+' active people<br>value '+fmtU(x.api_value)+
                 '<br>billed '+fmtU(x.billed);
        });
      });
    });
  })();

  // ================= 3. MODEL MIX — stacked area of value share =================
  (function(){
    var host = document.getElementById('modelmix'); if(!host) return;
    var M = D.model_mix;
    if(!M.periods.length){ host.innerHTML='<p class="note">No data.</p>'; return; }
    var W=980, H=260, pT=14, pB=28, pL=52, pR=14;
    var n = M.periods.length, S = M.series;
    var totals = M.periods.map(function(_,i){
      return S.reduce(function(a,s){ return a + (s.values[i]||0); }, 0); });
    var max = Math.max.apply(null, totals) || 1;
    var bw = (W-pL-pR)/n;
    var g = '';
    [0,.5,1].forEach(function(f){
      var y = pT + (H-pT-pB)*(1-f);
      g += '<line x1="'+pL+'" y1="'+y+'" x2="'+(W-pR)+'" y2="'+y+'" stroke="'+c('--line')+
           '" stroke-width="1"/>';
      g += '<text class="axis" x="'+(pL-8)+'" y="'+(y+4)+'" text-anchor="end" fill="'+
           c('--text-muted')+'" font-size="11">'+fmtU(max*f)+'</text>';
    });
    var tstep = tickStep(n, W-pL-pR);
    M.periods.forEach(function(p,i){
      var acc = 0, x0 = pL + i*bw + 4, w = Math.max(bw-8, 2);
      S.forEach(function(s,si){
        var v = s.values[i]||0; if(v<=0) return;
        var h = (H-pT-pB)*(v/max);
        var y = H-pB-((H-pT-pB)*(acc/max)) - h;
        // 2px surface gap between stacked segments keeps adjacent fills readable
        g += '<rect x="'+x0+'" y="'+y+'" width="'+w+'" height="'+Math.max(h-2,1)+
             '" rx="3" fill="'+slot(si, s.model)+'"/>';
        acc += v;
      });
      if(i % tstep === 0){
        g += '<text class="axis" x="'+(x0+w/2)+'" y="'+(H-pB+16)+'" text-anchor="middle" fill="'+
             c('--text-muted')+'" font-size="10.5">'+esc(shortP(p))+'</text>';
      }
      g += '<rect class="hitm" data-i="'+i+'" x="'+(pL+i*bw)+'" y="'+pT+'" width="'+bw+
           '" height="'+(H-pT-pB)+'" fill="transparent"/>';
    });
    var leg = S.map(function(s,si){
      return '<span><i style="background:'+slot(si, s.model)+'"></i>'+esc(s.model)+'</span>';
    }).join('');
    host.innerHTML = svg(g, W, H) + '<div class="legend">'+leg+'</div>';
    host.querySelectorAll('.hitm').forEach(function(el){
      var i = +el.dataset.i;
      wire(el, function(){
        var lines = S.map(function(s){
          return esc(s.model)+' '+fmtU(s.values[i]||0); }).join('<br>');
        return '<b>'+esc(shortP(M.periods[i]))+'</b><br>'+lines+
               '<br><b>total '+fmtU(totals[i])+'</b>';
      });
    });
  })();

  // ---- grain toggle, shared by the two dual-grain sections ----
  // Both charts answer the same question at two resolutions: a month says who
  // carried the quarter, a week says who is carrying it now. Neither subsumes
  // the other — a monthly bar hides a person who only appeared three weeks ago
  // — so the section carries both and the reader picks. Monthly is the resting
  // state because it is the one that reads at a glance.
  function grainToggle(host, onPick){
    var bar = document.createElement('div');
    bar.className = 'seg';
    bar.setAttribute('role','group');
    bar.setAttribute('aria-label','Time grain');
    [['month','Monthly'],['week','Weekly']].forEach(function(o, i){
      var b = document.createElement('button');
      b.type = 'button'; b.textContent = o[1]; b.dataset.g = o[0];
      b.setAttribute('aria-pressed', i === 0 ? 'true' : 'false');
      if (i === 0) b.className = 'on';
      b.addEventListener('click', function(){
        bar.querySelectorAll('button').forEach(function(x){
          x.className = ''; x.setAttribute('aria-pressed','false'); });
        b.className = 'on'; b.setAttribute('aria-pressed','true');
        onPick(o[0]);
      });
      bar.appendChild(b);
    });
    host.appendChild(bar);
  }

  // Period labels follow the grain of the CHART, not of the collect window —
  // the two dual-grain sections switch grain without the window changing.
  function label(p, grain){
    var s = String(p);
    var m = /^(\d{4})-W(\d{2})$/.exec(s);
    if (m) return 'W' + m[2];
    return grain === 'month' ? s.slice(0,7) : s.slice(5);
  }

  // One stacked-bar renderer, used by both dual-grain sections. They differ
  // only in what a value means and how a tooltip reads it, so those are
  // arguments rather than two near-identical copies that can drift apart.
  function stackedBars(el, periods, series, opt){
    var W=980, H=280, pT=14, pB=30, pL=opt.padL||52, pR=14;
    var n = periods.length;
    if(!n){ el.innerHTML = '<p class="note">No data in this window.</p>'; return; }
    var totals = periods.map(function(_,i){
      return series.reduce(function(a,s){ return a + opt.value(s,i); }, 0); });
    var max = Math.max.apply(null, totals) || 1;
    var bw = (W-pL-pR)/n, tstep = tickStep(n, W-pL-pR), g = '';
    [0,.5,1].forEach(function(f){
      var y = pT + (H-pT-pB)*(1-f);
      g += '<line x1="'+pL+'" y1="'+y+'" x2="'+(W-pR)+'" y2="'+y+'" stroke="'+c('--line')+
           '" stroke-width="1"/>';
      g += '<text class="axis" x="'+(pL-8)+'" y="'+(y+4)+'" text-anchor="end" fill="'+
           c('--text-muted')+'" font-size="11">'+opt.fmtAxis(max*f)+'</text>';
    });
    periods.forEach(function(p,i){
      var acc = 0, x0 = pL + i*bw + 4, w = Math.max(bw-8, 2);
      series.forEach(function(s,si){
        var v = opt.value(s,i); if(v<=0) return;
        var h = (H-pT-pB)*(v/max);
        var y = H-pB-((H-pT-pB)*(acc/max)) - h;
        g += '<rect x="'+x0+'" y="'+y+'" width="'+w+'" height="'+Math.max(h-2,1)+
             '" rx="3" fill="'+slot(si, opt.name(s))+'"/>';
        acc += v;
      });
      if(i % tstep === 0){
        g += '<text class="axis" x="'+(x0+w/2)+'" y="'+(H-pB+16)+'" text-anchor="middle" fill="'+
             c('--text-muted')+'" font-size="10.5">'+esc(label(p, opt.grain))+'</text>';
      }
      g += '<rect class="hit" data-i="'+i+'" x="'+(pL+i*bw)+'" y="'+pT+'" width="'+bw+
           '" height="'+(H-pT-pB)+'" fill="transparent"/>';
    });
    var leg = series.map(function(s,si){
      return '<span><i style="background:'+slot(si, opt.name(s))+'"></i>'+
             esc(opt.legend(s))+'</span>';
    }).join('');
    el.innerHTML = svg(g, W, H) + '<div class="legend">'+leg+'</div>';
    el.querySelectorAll('.hit').forEach(function(hit){
      var i = +hit.dataset.i;
      wire(hit, function(){ return opt.tip(i, periods[i], totals[i]); });
    });
  }

  // ============ 3b. USER MIX — value by person, month or week ============
  (function(){
    var host = document.getElementById('usermix'); if(!host) return;
    var M = D.user_mix;
    if(!M || !M.grains){ host.innerHTML='<p class="note">No data.</p>'; return; }
    var plot = document.createElement('div');
    grainToggle(host, draw); host.appendChild(plot);
    function draw(g){
      var G = M.grains[g]; if(!G) return;
      stackedBars(plot, G.periods, G.series, {
        grain: g,
        value:  function(s,i){ return s.values[i]||0; },
        name:   function(s){ return s.user; },
        fmtAxis: fmtU,
        legend: function(s){
          return s.user === 'Other'
            ? 'Other'+(M.folded && M.folded.count ? ' ('+M.folded.count+')' : '')
            : String(s.user).split('@')[0];
        },
        tip: function(i, period, total){
          var lines = G.series.filter(function(s){ return (s.values[i]||0) > 0; })
            .map(function(s){
              var nm = s.user === 'Other' ? 'Other' : String(s.user).split('@')[0];
              return esc(nm)+' '+fmtU(s.values[i]||0); }).join('<br>');
          return '<b>'+esc(label(period, g))+'</b><br>'+(lines||'no usage')+
                 '<br><b>total '+fmtU(total)+'</b>';
        },
      });
    }
    draw('month');
  })();

  // ============ 3c. SKILL AUTHORSHIP — skills touched, month or week ============
  // A different subject from the charts above (git, not token usage), so it
  // gets its own scale and its own card. The bar is AUTHORING only — created +
  // updated. Sweep touches are excluded from the height and reported in their
  // own column, because one repo-wide find-and-replace would otherwise outrank
  // a month of real work.
  (function(){
    var host = document.getElementById('authorship'); if(!host) return;
    var A = D.git;
    if(!A || !A.grains){
      host.innerHTML = '<p class="note">'+esc((A && A.unavailable) ||
        'No git data for this window.')+'</p>'; return; }
    var plot = document.createElement('div');
    grainToggle(host, draw); host.appendChild(plot);
    function draw(g){
      var G = A.grains[g]; if(!G) return;
      stackedBars(plot, G.periods, G.series, {
        grain: g, padL: 46,
        value:  function(s,i){ return (s.created[i]||0) + (s.updated[i]||0); },
        name:   function(s){ return s.person; },
        fmtAxis: function(v){ return Math.round(v); },
        legend: function(s){
          return s.person === 'Other'
            ? 'Other'+(A.folded && A.folded.count ? ' ('+A.folded.count+')' : '')
            : s.person;
        },
        tip: function(i, period, total){
          var lines = G.series.filter(function(s){
              return (s.created[i]||0) + (s.updated[i]||0) + (s.swept[i]||0) > 0; })
            .map(function(s){
              var bits = [];
              if(s.created[i]) bits.push(s.created[i]+' new');
              if(s.updated[i]) bits.push(s.updated[i]+' updated');
              if(s.swept[i])   bits.push(s.swept[i]+' swept');
              return esc(s.person)+' — '+bits.join(', '); }).join('<br>');
          return '<b>'+esc(label(period, g))+'</b><br>'+(lines||'no skill changes')+
                 '<br><b>'+total+' authored</b>';
        },
      });
    }
    draw('month');
  })();


  // ============ 3d. BILLED OVERAGE — real money, month or week ============
  // The only per-person chart here denominated in money that was ACTUALLY
  // charged. Everything else per-person is API-equivalent value, a derived
  // counterfactual. This is the invoice.
  //
  // It ranks by overage on its own and deliberately does NOT inherit the usage
  // chart's slot order: sharing it would fold the fourth-largest payer into
  // "Other" for being a light user, which is the very fact this chart exists to
  // surface. So a person's colour differs between the two charts, and the lede
  // says so rather than leaving a reader to trip over it.
  (function(){
    var host = document.getElementById('overagemix'); if(!host) return;
    var O = D.overage_mix;
    if(!O || !O.grains || !O.payers){
      host.innerHTML = '<p class="note">No billed overage in this window — '+
        'everyone stayed inside their seat allowance.</p>'; return; }
    var plot = document.createElement('div');
    grainToggle(host, draw); host.appendChild(plot);
    function draw(g){
      var G = O.grains[g]; if(!G) return;
      stackedBars(plot, G.periods, G.series, {
        grain: g,
        value:   function(s,i){ return s.values[i]||0; },
        name:    function(s){ return s.user; },
        fmtAxis: fmtU,
        legend:  function(s){
          return s.user === 'Other'
            ? 'Other'+(O.folded && O.folded.count ? ' ('+O.folded.count+')' : '')
            : String(s.user).split('@')[0];
        },
        tip: function(i, period, total){
          var lines = G.series.filter(function(s){ return (s.values[i]||0) > 0; })
            .map(function(s){
              var nm = s.user === 'Other' ? 'Other' : String(s.user).split('@')[0];
              return esc(nm)+' '+fmtU(s.values[i]||0); }).join('<br>');
          return '<b>'+esc(label(period, g))+'</b><br>'+(lines||'no overage')+
                 '<br><b>total '+fmtU(total)+'</b>';
        },
      });
    }
    draw('month');
  })();

  // ======= 3d-bis. NEW SKILLS — creates only, month or week =======
  // Skill authorship counts created + updated in one bar, and updates dominate
  // it (~224 creates against ~500 updates). A reader asking what we actually
  // ADDED cannot recover that from a combined bar, and the answer has a
  // different shape: creation is lumpy and campaign-like, updating is
  // continuous.
  //
  // A create is also the least ambiguous signal in the dataset — it comes from
  // --diff-filter=A on the SKILL.md rather than being inferred from which files
  // moved — so this chart needs no sweep threshold at all.
  //
  // It SHARES the authorship slot order, unlike the overage chart. There the
  // sets genuinely diverged and ranking separately was the only way to keep a
  // top payer from folding away. Here the set is identical, nobody is at zero,
  // and only three of eight shuffle by a place or two — so sharing costs
  // nothing and keeps a person the same colour across two adjacent views of one
  // subject.
  (function(){
    var host = document.getElementById('newskills'); if(!host) return;
    var A = D.git;
    if(!A || !A.grains){
      host.innerHTML = '<p class="note">'+esc((A && A.unavailable) ||
        'No git data for this window.')+'</p>'; return; }
    var plot = document.createElement('div');
    grainToggle(host, draw); host.appendChild(plot);
    function draw(g){
      var G = A.grains[g]; if(!G) return;
      stackedBars(plot, G.periods, G.series, {
        grain: g, padL: 46,
        value:   function(s,i){ return s.created[i]||0; },
        name:    function(s){ return s.person; },
        fmtAxis: function(v){ return Math.round(v); },
        legend:  function(s){
          return s.person === 'Other'
            ? 'Other'+(A.folded && A.folded.count ? ' ('+A.folded.count+')' : '')
            : s.person;
        },
        tip: function(i, period, total){
          var lines = G.series.filter(function(s){ return (s.created[i]||0) > 0; })
            .map(function(s){
              var n = s.created[i];
              return esc(s.person)+' — '+n+' new skill'+(n === 1 ? '' : 's'); })
            .join('<br>');
          return '<b>'+esc(label(period, g))+'</b><br>'+
                 (lines||'no new skills')+'<br><b>'+total+' created</b>';
        },
      });
    }
    draw('month');
  })();

  // ============ 3e. TRUE COST — seats + overage, monthly only ============
  // The bill, not half of it. Seats are the large predictable half and overage
  // the small variable one; the report showed only the latter.
  //
  // Monthly only on purpose — seats are billed monthly, and splitting a seat
  // charge across weeks would be a derived allocation dressed as a fact. Hence
  // no grain toggle here, unlike the three cards above.
  //
  // Months with no seat snapshot are drawn HATCHED and labelled, because their
  // seat figure is today's roster carried backwards. Dropping them would hide
  // most of the window; drawing them like the measured month would be a
  // confident fiction.
  (function(){
    var host = document.getElementById('truecost'); if(!host) return;
    var T = D.true_cost;
    if(!T || !T.months || !T.months.length){
      host.innerHTML = '<p class="note">'+esc((T && T.note) || 'No cost data.')+'</p>';
      return; }
    var M = T.months, n = M.length;
    var W=980, H=300, pT=16, pB=34, pL=64, pR=14;
    var max = Math.max.apply(null, M.map(function(m){ return m.total; })) || 1;
    var bw = (W-pL-pR)/n, g = '';

    // hatch pattern for the assumed months
    // The hatch is the SEATS hue, not the overage hue. Painting it --s2 made an
    // assumed seat bar the same colour as the overflow stacked on top of it, and
    // six of seven months are assumed — so most of the chart read as one flat
    // band and the split the card exists to show disappeared.
    g += '<defs><pattern id="assumed" width="6" height="6" patternUnits="userSpaceOnUse" '+
         'patternTransform="rotate(45)"><rect width="6" height="6" fill="'+c('--s1')+
         '" opacity="0.22"/><line x1="0" y1="0" x2="0" y2="6" stroke="'+c('--s1')+
         '" stroke-width="3" opacity="0.85"/></pattern></defs>';

    [0,.5,1].forEach(function(f){
      var y = pT + (H-pT-pB)*(1-f);
      g += '<line x1="'+pL+'" y1="'+y+'" x2="'+(W-pR)+'" y2="'+y+'" stroke="'+c('--line')+
           '" stroke-width="1"/>';
      g += '<text class="axis" x="'+(pL-8)+'" y="'+(y+4)+'" text-anchor="end" fill="'+
           c('--text-muted')+'" font-size="11">'+exactU(max*f)+'</text>';
    });

    M.forEach(function(m,i){
      var x0 = pL + i*bw + 6, w = Math.max(bw-12, 2);
      var hs = (H-pT-pB)*(m.seats/max), ho = (H-pT-pB)*(m.overage/max);
      var ys = H-pB-hs;
      // seats on the bottom — the floor you pay before anyone types anything
      g += '<rect x="'+x0+'" y="'+ys+'" width="'+w+'" height="'+Math.max(hs-2,1)+
           '" rx="3" fill="'+(m.measured ? c('--s1') : 'url(#assumed)')+'"/>';
      if(!m.measured){
        g += '<rect x="'+x0+'" y="'+ys+'" width="'+w+'" height="'+Math.max(hs-2,1)+
             '" rx="3" fill="none" stroke="'+c('--s1')+'" stroke-width="1" '+
             'stroke-dasharray="3 2" opacity=".8"/>';
      }
      // No overage, no band. The 1px floor that keeps a small charge visible
      // would otherwise paint an orange sliver on a month that was charged
      // nothing — and now that idle months appear on the axis at all, that is
      // every quiet month. A zero must read as zero.
      if(m.overage > 0){
        g += '<rect x="'+x0+'" y="'+(ys-ho)+'" width="'+w+'" height="'+Math.max(ho-2,1)+
             '" rx="3" fill="'+c('--s2')+'"/>';
      }
      g += '<text class="vlabel" x="'+(x0+w/2)+'" y="'+(ys-ho-6)+'" text-anchor="middle">'+
           exactU(m.total)+'</text>';
      g += '<text class="axis" x="'+(x0+w/2)+'" y="'+(H-pB+16)+'" text-anchor="middle" fill="'+
           c('--text-muted')+'" font-size="10.5">'+esc(String(m.period).slice(0,7))+'</text>';
      if(!m.measured || m.overage_known === false){
        g += '<text class="axis" x="'+(x0+w/2)+'" y="'+(H-pB+29)+'" text-anchor="middle" fill="'+
             c('--warn')+'" font-size="9.5">'+
             (m.overage_known === false ? 'unread' : 'assumed')+'</text>';
      }
      g += '<rect class="hitc" data-i="'+i+'" x="'+(pL+i*bw)+'" y="'+pT+'" width="'+bw+
           '" height="'+(H-pT-pB)+'" fill="transparent"/>';
    });

    host.innerHTML = svg(g, W, H) +
      '<div class="legend">'+
      '<span><i style="background:'+c('--s1')+'"></i>Seat subscription</span>'+
      '<span><i style="background:'+c('--s2')+'"></i>Billed overage</span>'+
      '<span><i style="background:'+c('--s1')+';opacity:.35"></i>hatched = seat count assumed</span>'+
      '</div>';
    host.querySelectorAll('.hitc').forEach(function(el){
      var i = +el.dataset.i, m = M[i];
      wire(el, function(){
        return '<b>'+esc(String(m.period).slice(0,7))+'</b><br>'+
               'seats '+exactU(m.seats)+' ('+m.members+')<br>'+
               'overage '+exactU(m.overage)+'<br><b>total '+exactU(m.total)+'</b>'+
               (m.measured ? '' : '<br><i>seat count carried back from '+
                esc(T.first_snapshot)+' — assumed, not measured</i>')+
               (m.overage_known === false
                 ? '<br><i>no day of this month was harvested — the $0 of overage '+
                   'is unread, not zero</i>' : '');
      });
    });
  })();

  // ================= 4. CONCENTRATION — cumulative share =================
  (function(){
    var host = document.getElementById('concentration'); if(!host) return;
    var P = D.concentration.points;
    if(!P.length){ host.innerHTML='<p class="note">No data.</p>'; return; }
    var W=980, H=240, pT=14, pB=30, pL=48, pR=14;
    var n = P.length;
    var X = function(i){ return pL + (W-pL-pR)*(n>1 ? i/(n-1) : 0); };
    var Y = function(v){ return pT + (H-pT-pB)*(1-v/100); };
    var g = '';
    [0,25,50,75,100].forEach(function(t){
      var y = Y(t);
      g += '<line x1="'+pL+'" y1="'+y+'" x2="'+(W-pR)+'" y2="'+y+'" stroke="'+c('--line')+
           '" stroke-width="1"/>';
      g += '<text class="axis" x="'+(pL-8)+'" y="'+(y+4)+'" text-anchor="end" fill="'+
           c('--text-muted')+'" font-size="11">'+t+'%</text>';
    });
    // equality reference — what the curve would be if usage were evenly spread
    g += '<line x1="'+X(0)+'" y1="'+Y(100/n)+'" x2="'+X(n-1)+'" y2="'+Y(100)+'" stroke="'+
         c('--text-muted')+'" stroke-width="1.5" stroke-dasharray="4 4" opacity=".6"/>';
    var d = P.map(function(p,i){ return (i?'L ':'M ')+X(i).toFixed(1)+' '+Y(p.cum_share).toFixed(1); }).join(' ');
    g += '<path d="'+d+'" fill="none" stroke="'+c('--s1')+'" stroke-width="2"/>';
    P.forEach(function(p,i){
      g += '<circle cx="'+X(i)+'" cy="'+Y(p.cum_share)+'" r="4" fill="'+c('--s1')+
           '" stroke="'+c('--surface-1')+'" stroke-width="2"/>';
      g += '<circle class="hitc" data-i="'+i+'" cx="'+X(i)+'" cy="'+Y(p.cum_share)+
           '" r="13" fill="transparent"/>';
    });
    g += '<text class="axis" x="'+pL+'" y="'+(H-pB+18)+'" fill="'+c('--text-muted')+
         '" font-size="11">most usage</text>';
    g += '<text class="axis" x="'+(W-pR)+'" y="'+(H-pB+18)+'" text-anchor="end" fill="'+
         c('--text-muted')+'" font-size="11">least</text>';
    host.innerHTML = svg(g, W, H) +
      '<div class="legend"><span><i style="background:'+c('--s1')+'"></i>cumulative share</span>'+
      '<span><i style="background:'+c('--text-muted')+'"></i>even spread (reference)</span></div>';
    host.querySelectorAll('.hitc').forEach(function(el){
      var p = P[+el.dataset.i];
      wire(el, function(){
        return '<b>#'+p.rank+' '+esc(p.user)+'</b><br>'+fmtU(p.value)+' ('+p.share+'% of total)'+
               '<br>top '+p.rank+' together: <b>'+p.cum_share+'%</b>';
      });
    });
  })();
})();
"""


def _tiles(d) -> str:
    ad = d["adoption"]
    tot_tokens = sum(x["tokens"] for x in ad)
    tot_value = sum(x["api_value"] for x in ad)
    tot_billed = sum(x["billed"] for x in ad)
    seats = d["seat_fit"]
    seat_cost = sum(s["seat_usd"] for s in seats)
    months = max(len(ad), 1)
    tiles = [
        (toks(tot_tokens), "tokens", f"{d['window']['days']} days"),
        (usd(tot_value), "API-equivalent value", "at list price, derived"),
        (usd(tot_billed, 2), "billed overage", "what you actually paid extra"),
        (usd(seat_cost), "seats per month",
         f"{sum(s.get('tiers', [s['tier']]).count('premium') for s in seats)} premium · "
         f"{sum(s.get('tiers', [s['tier']]).count('standard') for s in seats)} standard"),
        (f"{d['concentration']['top5']}%", "from the top 5 people", "usage concentration"),
        (f"{d['token_mix']['cache_read_pct']}%", "of tokens are cache reads",
         "why billed overage is small"),
    ]
    return "".join(
        f'<div class="tile"><div class="v">{e(v)}</div><div class="k">{e(k)}</div>'
        f'<div class="n">{e(n)}</div></div>' for v, k, n in tiles)


def _banners(d) -> str:
    out = []
    cov = d["coverage"]
    if not cov["complete"]:
        out.append(
            f'<div class="banner crit"><b>Incomplete.</b> Only {cov["days_harvested"]} of '
            f'{cov["days_in_window"]} days in this window were harvested, so every figure '
            f'below is a lower bound, not a total.</div>')
    if cov.get("stale"):
        out.append(
            f'<div class="banner warn"><b>Stale.</b> Newest harvested day is '
            f'{e(cov["newest_harvested"])}, {cov["lag_days"]} days behind settled data. '
            f'Run the harvest before relying on recent periods.</div>')
    pr = d.get("pricing") or {}
    if pr.get("age_days") is not None and pr["age_days"] > 120:
        out.append(
            f'<div class="banner warn"><b>Rates are {pr["age_days"]} days old</b> '
            f'(last verified {e(pr["verified_on"])}). Prices are hand-entered and nothing '
            f'refreshes them; re-check before quoting value outside the team.</div>')
    unpriced = pr.get("unpriced") or []
    if unpriced:
        names = ", ".join(f'{e(u["model"])} ({toks(u["tokens"])} since {e(u["first_day"])})'
                          for u in unpriced)
        out.append(
            f'<div class="banner crit"><b>Unpriced models.</b> {names} have no list '
            f'price on file, so their usage counts as $0 and every API-equivalent value '
            f'below is understated.</div>')
    sm = d.get("seats_meta") or {}
    if sm.get("snapshots", 0) <= 1 and d["window"]["days"] > 45:
        out.append(
            f'<div class="banner"><b>Seat tiers come from one snapshot</b> '
            f'({e(sm.get("as_of"))}). Over a window this long, anyone who changed tier is '
            f'attributed to their current one.</div>')
    return "".join(out)


def _seat_table(d) -> str:
    rows = []
    for s in d["seat_fit"]:
        tiers = s.get("tiers") or [s["tier"]]
        # Any premium seat makes the person premium for styling and the under-use check,
        # which compares against the sum of their seats.
        premium = "premium" in tiers
        pill = "prem" if premium else "std"
        note = ""
        if premium and s["value_per_month"] < s["seat_usd"]:
            note = ('<span class="flag">under-using a premium seat</span>' if len(tiers) == 1
                    else '<span class="flag">under-using their seats</span>')
        elif s["tier"] == "standard" and s["value_per_month"] > d["seat_prices"]["premium"]:
            note = '<span class="flag up">heavy — premium may pay off</span>'
        elif not s["active_days"]:
            note = '<span class="flag">no usage in window</span>'
        seat_label = (s["tier"] if len(tiers) == 1
                      else ", ".join(f"{tiers.count(t)} {t}" for t in sorted(set(tiers))))
        who = s["user"] + (f" ({len(tiers)} seats)" if len(tiers) > 1 else "")
        rows.append(
            f'<tr><td>{e(who)}</td>'
            f'<td><span class="pill {pill}">{e(seat_label)}</span></td>'
            f'<td>{usd(s["seat_usd"])}</td><td>{usd(s["value_per_month"])}</td>'
            f'<td>{e(str(s["ratio"])+"x" if s["ratio"] is not None else "—")}</td>'
            f'<td>{e(toks(s["tokens"]))}</td><td>{s["active_days"]}</td>'
            f'<td style="text-align:left">{note}</td></tr>')
    tot_seat = sum(s["seat_usd"] for s in d["seat_fit"])
    tot_val = sum(s["value_per_month"] for s in d["seat_fit"])
    # No seats, or every tier unknown, makes tot_seat 0. Unguarded this raises
    # and no HTML is written at all — the report disappears rather than
    # rendering an empty one, which is the worse failure of the two.
    ratio = f"{tot_val / tot_seat:.0f}x" if tot_seat else "—"
    return (
        '<div class="scroll"><table><thead><tr><th>Person</th><th>Seat</th>'
        '<th>Seat $/mo</th><th>Value $/mo</th><th>Ratio</th><th>Tokens</th>'
        '<th>Active days</th><th style="text-align:left">Note</th></tr></thead><tbody>'
        + "".join(rows) +
        f'</tbody><tfoot><tr><td>Total</td><td></td><td>{usd(tot_seat)}</td>'
        f'<td>{usd(tot_val)}</td>'
        f'<td>{ratio}</td><td></td><td></td><td></td></tr>'
        '</tfoot></table></div>')


def _authorship_card(d) -> str:
    """Who is creating and updating skills, from git.

    A different subject from the usage charts — a different source, a different
    unit, a different question — so it is its own card rather than another band
    on an existing chart. The section is optional: a report built without a
    readable AINative checkout says so and still renders everything else.
    """
    g = d.get("git")
    if not g:
        return ""
    if g.get("unavailable"):
        return f"""<div class="card">
    <h2>Skill authorship</h2>
    <p class="lede">Who created and updated skills in AINative, by week.</p>
    <p class="note">Not available for this run: {e(g['unavailable'])}</p>
  </div>"""

    # Either grain totals the same work; month is the resting view.
    month = (g.get("grains") or {}).get("month") or {}
    tot_c = sum(s["total_created"] for s in month.get("series", []))
    tot_u = sum(s["total_updated"] for s in month.get("series", []))
    rows = "".join(
        f'<tr><td>{e(p["person"])}</td><td>{p["created"]}</td><td>{p["updated"]}</td>'
        f'<td>{p["swept"]}</td><td>{p["commits"]}</td></tr>'
        for p in g.get("everyone", []))
    sweeps = "".join(
        f'<tr><td>{e(s["date"])}</td><td>{e(s["who"])}</td><td>{s["skills"]}</td>'
        f'<td style="text-align:left">{e(s["subject"])}</td></tr>'
        for s in g.get("sweeps", []))
    folded = g.get("folded") or {}
    fold_note = (f" {folded['count']} more fold into Other."
                 if folded.get("count") else "")
    unmapped = g.get("unmapped_identities") or []
    warn = ("" if not unmapped else
            '<p class="flag">' + e(f"{len(unmapped)} git identity(ies) are not in the "
            "alias map, so that work is filed under a raw git name: "
            + ", ".join(u["identity"] for u in unmapped)) + "</p>")

    return f"""<div class="card">
    <h2>Skill authorship</h2>
    <p class="lede">Who created and updated skills in AINative, by week, read straight from
    git. The bar height is authoring — {tot_c} skills created and {tot_u} updated across the
    window.{fold_note} A commit touching {g['sweep_threshold']} or more skills is a mechanical
    sweep and is <em>excluded</em> from the bar: {g['sweep_count']} such commits in this window
    would otherwise have outranked everyone's real work. They are kept, in their own column
    below.</p>
    <div id="authorship"></div>
    {warn}
    <details><summary>Every person, whole window</summary>
      <div class="scroll"><table><thead><tr><th>Person</th><th>Created</th><th>Updated</th>
      <th>Swept</th><th>Commits</th></tr></thead><tbody>{rows}</tbody></table></div></details>
    <details><summary>The largest sweeps</summary>
      <div class="scroll"><table><thead><tr><th>Date</th><th>Who</th><th>Skills</th>
      <th>Commit</th></tr></thead><tbody>{sweeps}</tbody></table></div></details>
    <p class="note">Read from {e(g['ref'])} — {g['commits_read']} commits touching
    <code>plugins/*/skills/*</code>.</p>
  </div>"""


def _overage_card(d) -> str:
    """Billed overage per person — the only per-person money that was charged."""
    o = d.get("overage_mix")
    if not o:
        return ""
    rows = "".join(
        f'<tr><td>{e(r["user"])}</td><td>{usd(r["billed"], 2)}</td></tr>'
        for r in o.get("all_time", []) if r["billed"] > 0)
    fold = o.get("folded") or {}
    fold_note = (f" {fold['count']} smaller payers fold into Other, "
                 f"{usd(fold.get('total'), 2)} between them."
                 if fold.get("count") else "")
    return f"""<div class="card">
    <h2>Billed overage by person</h2>
    <p class="lede">Real money, charged beyond the seat allowance —
    {usd(o['total'], 2)} across the window, from {o['payers']} of {o['people']} people.
    This is the one per-person chart here that is an invoice rather than a
    counterfactual, so it answers a different question from the two above: not who
    uses Claude, but who overflowed a personal cap.{fold_note}</p>
    <p class="lede"><b>The order is not the usage order, and the colours are not the
    usage colours.</b> A heavy user well inside their cap owes nothing while a lighter
    one who overflowed owes real money, so this ranks by overage on its own. Sharing
    the usage chart's bands would have folded the fourth-largest payer into "Other"
    for being a light user.</p>
    <div id="overagemix"></div>
    <details><summary>Every payer, whole window</summary>
      <div class="scroll"><table><thead><tr><th>Person</th><th>Billed overage</th>
      </tr></thead><tbody>{rows}</tbody></table></div></details>
  </div>"""


def _truecost_card(d) -> str:
    """Seats + overage: the actual bill, monthly."""
    tc = d.get("true_cost")
    if not tc or not tc.get("months"):
        return ""
    ms = tc["months"]
    latest = ms[-1]
    unmeasured = tc.get("unmeasured", 0)
    unharvested = tc.get("unharvested", 0)
    caveat = ""
    if unmeasured:
        caveat = (f""" <b>{unmeasured} of {len(ms)} months are estimates.</b> The seat
    roster is only known from {e(tc['first_snapshot'])}, when the first snapshot was
    taken, so earlier months are priced by carrying today's roster backwards —
    headcount was almost certainly lower. Those bars are hatched and labelled
    <em>assumed</em>. They are shown rather than dropped because hiding most of the
    window would mislead more than marking it does, and they become measured on
    their own as snapshots accumulate.""")
    def basis(m):
        return "measured" if m["measured"] else f'assumed ({m["members"]} seats)'

    rows = "".join(
        f'<tr><td>{e(m["period"][:7])}</td><td>{usd(m["seats"])}</td>'
        f'<td>{usd(m["overage"], 2)}</td><td>{usd(m["total"], 2)}</td>'
        f'<td style="text-align:left">{basis(m)}</td></tr>'
        for m in ms)
    return f"""<div class="card">
    <h2>What we actually pay</h2>
    <p class="lede">Seat subscription plus billed overage — the whole bill, which no
    other card here shows. Seats are the large predictable half and overage the
    small variable one: {e(latest["period"][:7])} came to {usd(latest["total"], 2)},
    of which {usd(latest["overage"], 2)} was overflow.{caveat}</p>
    <p class="note">Monthly only. Seats are billed monthly, so splitting a seat
    charge across weeks would be a derived allocation presented as a fact — this is
    the one time-series card here with no weekly view.</p>
    {f'<p class="flag">{unharvested} month(s) were never harvested, so their overage '
      f'reads $0 because nothing was read — not because nothing was charged. The seat '
      f'cost is still owed and still shown.</p>' if unharvested else ''}
    <div id="truecost"></div>
    <details><summary>Month by month</summary>
      <div class="scroll"><table><thead><tr><th>Month</th><th>Seats</th>
      <th>Overage</th><th>Total</th><th style="text-align:left">Seat basis</th>
      </tr></thead><tbody>{rows}</tbody></table></div></details>
  </div>"""


def _newskills_card(d) -> str:
    """Creates only — the least ambiguous signal in the git data."""
    g = d.get("git")
    if not g or g.get("unavailable") or not g.get("grains"):
        return ""
    month = (g.get("grains") or {}).get("month") or {}
    series = month.get("series", [])
    total = sum(s["total_created"] for s in series)
    if not total:
        return ""
    top = max(series, key=lambda s: s["total_created"], default=None)
    rows = "".join(
        f'<tr><td>{e(s["person"])}</td><td>{s["total_created"]}</td>'
        f'<td>{s["total_updated"]}</td></tr>'
        for s in sorted(series, key=lambda x: -x["total_created"])
        if s["total_created"])
    return f"""<div class="card">
    <h2>New skills created</h2>
    <p class="lede">Only skills whose <code>SKILL.md</code> was added — {total} across
    the window. Skill authorship above counts creation and maintenance together, and
    maintenance dominates it, so this separates the question of what we
    <em>added</em> from what we kept working on. The two move differently: creation
    comes in campaigns, updating is continuous.{
      f' {e(top["person"])} created {top["total_created"]} of them.' if top else ''}</p>
    <p class="note">A create is the least ambiguous signal here — taken from the
    SKILL.md being added, not inferred from which files a commit moved — so unlike
    the chart above this one needs no sweep threshold. Colours match Skill
    authorship: the same people in the same bands.</p>
    <div id="newskills"></div>
    <details><summary>Created vs updated, whole window</summary>
      <div class="scroll"><table><thead><tr><th>Person</th><th>Created</th>
      <th>Updated</th></tr></thead><tbody>{rows}</tbody></table></div></details>
  </div>"""


def build(d) -> str:
    w = d["window"]
    ident = _skill_ident()
    prods = d.get("products") or []
    top_prod = prods[0] if prods else None
    prod_share = (top_prod["tokens"] / max(sum(p["tokens"] for p in prods), 1) * 100
                  if top_prod else 0)
    mm = d["model_mix"]["all_time_totals"]
    um = d.get("user_mix") or {}
    fold = um.get("folded") or {}
    folded_note = (f' {fold["count"]} people are folded there, '
                   f'{usd(fold.get("total"))} between them.') if fold.get("count") else ""
    user_rows = "".join(
        f'<tr><td>{e(r["user"])}</td><td>{usd(r["value"])}</td>'
        f'<td>{e(toks(r["tokens"]))}</td></tr>'
        for r in (um.get("all_time") or []))
    top_model = next(iter(mm), None)
    authorship_card = _authorship_card(d)
    overage_card = _overage_card(d)
    truecost_card = _truecost_card(d)
    newskills_card = _newskills_card(d)

    # No <!doctype>/<html>/<head>/<body> — the Artifact tool wraps the file in its
    # own skeleton at publish time, so those tags would be nested duplicates.
    # <title> and <style> go at the top of the file instead.
    return f"""<title>Claude Seat Usage</title>
<style>{CSS}</style>
<div class="wrap">
  <h1>Claude seat usage</h1>
  <p class="sub">{e(w['since'])} to {e(w['through'])} · {w['days']} days ·
     {len(d['seat_fit'])} members · generated {e(d['generated_at'])}</p>

  {_banners(d)}

  <div class="tiles">{_tiles(d)}</div>

  <div class="banner"><b>Two dollar figures appear throughout and are not the same thing.</b>
  <em>API-equivalent value</em> is what this usage would have cost at published API rates —
  derived, not an invoice, and useful only for comparing against a seat price.
  <em>Billed overage</em> is real money charged beyond the seat allowance, which is $0 for
  anyone inside their limit. Ranking people by billed dollars ranks who overflowed a personal
  cap, not who uses Claude.</div>

  <div class="card">
    <h2>Seat fit</h2>
    <p class="lede">Each bar is what that person's usage would have cost on the API, projected
    to a 30-day month. The dashed marker is their seat price — a bar past it means the seat
    costs less than the same work would. The scale is logarithmic because the range spans four
    orders of magnitude. This is not a cash saving; it is a counterfactual.</p>
    <div id="seatfit"></div>
    {_seat_table(d)}
  </div>

  <div class="card">
    <h2>Adoption</h2>
    <p class="lede">Tokens and active people, plotted separately. They are different scales and
    a shared axis would misstate both. Read them together: if tokens climb while the people
    count stays flat, the growth is intensity per person rather than more adopters.</p>
    <div id="adoption"></div>
  </div>

  <div class="card">
    <h2>Model mix</h2>
    <p class="lede">API-equivalent value by model over time. This is the one cost lever visible
    in the data: the Opus family and Sonnet differ by roughly five times per token, so the mix
    is worth choosing rather than inheriting.{
      f' {e(top_model)} leads at {usd(mm[top_model])} across the window.' if top_model else ''}</p>
    <div id="modelmix"></div>
  </div>

  <div class="card">
    <h2>Usage by person</h2>
    <p class="lede">The same stack as model mix, split by person instead. Eight bands are
    named and everyone past that folds into one neutral "Other" — a ninth person never gets
    a colour of their own.{folded_note} Read it for shape rather than rank: who is carrying a
    month, and whether that changed.</p>
    <div id="usermix"></div>
    <details><summary>Every person, whole window</summary>
      <div class="scroll"><table><thead><tr><th>Person</th><th>API value</th>
      <th>Tokens</th></tr></thead><tbody>{user_rows}</tbody></table></div></details>
  </div>

  {authorship_card}

  {newskills_card}

  {truecost_card}

  {overage_card}

  <div class="card">
    <h2>Concentration</h2>
    <p class="lede">Cumulative share of usage, heaviest user first. The dashed line is what an
    even spread would look like. The gap between them is key-person dependency: the top
    {len(d['concentration']['points']) and 5} people account for
    {d['concentration']['top5']}% of all usage.</p>
    <div id="concentration"></div>
    <details><summary>Per-person shares</summary>
      <div class="scroll"><table><thead><tr><th>#</th><th>Person</th><th>Value</th>
      <th>Share</th><th>Cumulative</th></tr></thead><tbody>{
        "".join(f'<tr><td>{p["rank"]}</td><td>{e(p["user"])}</td><td>{usd(p["value"])}</td>'
                f'<td>{p["share"]}%</td><td>{p["cum_share"]}%</td></tr>'
                for p in d["concentration"]["points"])
      }</tbody></table></div></details>
  </div>

  <div class="card">
    <h2>Worth knowing</h2>
    <p class="lede">Three facts that shape how to read everything above.</p>
    <table><tbody>
      <tr><td style="text-align:left">{e(top_prod['product']) if top_prod else '—'} is
        {prod_share:.0f}% of all tokens</td>
        <td style="text-align:left;color:var(--text-secondary)">This is a report about that
        product with a little else attached.</td></tr>
      <tr><td style="text-align:left">{d['token_mix']['cache_read_pct']}% of tokens are
        cache reads</td>
        <td style="text-align:left;color:var(--text-secondary)">Cache reads cost a fraction of
        fresh input, which is why real usage dwarfs billed overage.</td></tr>
      <tr><td style="text-align:left">API-key usage is absent</td>
        <td style="text-align:left;color:var(--text-secondary)">Claude Code authenticated with
        an API key reports to the Console organization, not here.</td></tr>
    </tbody></table>
    <p class="note">Rates last verified {e((d.get('pricing') or {}).get('verified_on'))}.
    Seat tiers from the {e((d.get('seats_meta') or {}).get('as_of'))} snapshot.</p>
  </div>

  <footer>Internal — this report names individuals and ranks them by usage.<br>
  {e(ident)} · {e(d['generated_at'])}</footer>
</div>
<script>window.__DATA__ = {json_for_script(d)};</script>
<script>{JS}</script>
"""


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="Render the Claude usage report")
    p.add_argument("--data", required=True, help="JSON from collect.py")
    p.add_argument("--out", required=True, help="HTML output path")
    p.add_argument("--git", help="JSON from collect_git.py (skill-authorship section). "
                                 "Optional — omitted, the section is left out entirely.")
    a = p.parse_args()
    with open(a.data, encoding="utf-8") as fh:
        d = json.load(fh)
    if a.git:
        # The authorship section is optional, so nothing about it may take down
        # the report. An absent or unreadable file is reported in the page as an
        # unavailable section rather than raised — a missing section has no
        # shape, and no HTML at all is the worst of both.
        try:
            with open(a.git, encoding="utf-8") as fh:
                d["git"] = json.load(fh)
        except (OSError, ValueError) as e:
            print(f"warning: --git {a.git} unreadable ({e}); "
                  f"rendering the section as unavailable", file=sys.stderr)
            d["git"] = {"unavailable": f"the authorship collector produced no usable output"}
    html_doc = build(d)
    with open(a.out, "w", encoding="utf-8") as fh:
        fh.write(html_doc)
    print(f"wrote {a.out}  ({len(html_doc):,} bytes, "
          f"{d['window']['since']} → {d['window']['through']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
