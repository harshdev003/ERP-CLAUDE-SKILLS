# Capture snippets

Paste each block into the browser's JavaScript tool, in the page under test. They only
inspect traffic and code. They do not change the app's UI.

## 1. Network hook: fetch, XHR and beacon

Install on the landing page, before clicking the CTA. It survives client-side navigation
and is lost on a full reload.

```js
window.__cap = [];
const push = (kind, url, body) => {
  let b = body;
  try {
    if (b instanceof URLSearchParams) b = b.toString();
    if (typeof b !== 'string') b = b == null ? '' : (b instanceof Blob ? '[blob]' : JSON.stringify(b));
  } catch (e) { b = String(b); }
  window.__cap.push({ t: Date.now(), kind, url: String(url), body: b.slice(0, 20000), path: location.pathname + location.search });
};
const of = window.fetch;
window.fetch = function (i, o) { push('fetch', typeof i === 'string' ? i : i.url, o && o.body); return of.apply(this, arguments); };
const oo = XMLHttpRequest.prototype.open, os = XMLHttpRequest.prototype.send;
XMLHttpRequest.prototype.open = function (m, u) { this.__u = u; return oo.apply(this, arguments); };
XMLHttpRequest.prototype.send = function (b) { push('xhr', this.__u, b); return os.apply(this, arguments); };
if (navigator.sendBeacon) { const ob = navigator.sendBeacon.bind(navigator); navigator.sendBeacon = (u, d) => { push('beacon', u, d); return ob(u, d); }; }
'hooked';
```

## 2. Mixpanel decoder

Returns the events captured since the last call. It drops the noisy default properties so
each event shows only what the funnel adds. Edit `skip` for another funnel's super properties.

```js
window.__seen = window.__seen || 0;
window.__mp = function (all) {
  const skip = new Set(['$os','$browser','$current_url','$browser_version','$screen_height','$screen_width','mp_lib','$lib_version','$insert_id','time','distinct_id','$device_id','$initial_referrer','$initial_referring_domain','token','mp_sent_by_lib_version','Source App','Day','Time','Sent From','Platform']);
  const out = [];
  window.__cap.slice(all ? 0 : window.__seen).forEach(c => {
    if (!/mixpanel\.com/.test(c.url)) return;
    let raw = c.body.replace(/^data=/, ''); try { raw = decodeURIComponent(raw); } catch (e) {}
    let arr; try { arr = JSON.parse(raw); } catch (e) { try { arr = JSON.parse(atob(raw)); } catch (e2) { out.push({ unparsed: raw.slice(0, 200) }); return; } }
    (Array.isArray(arr) ? arr : [arr]).forEach(e => {
      const p = e.properties || {};
      out.push({ event: e.event || Object.keys(e).join(','), props: Object.fromEntries(Object.entries(p).filter(([k]) => !skip.has(k) && !k.startsWith('Experiment: '))) });
    });
  });
  window.__seen = window.__cap.length;
  return out;
};
window.__mp();
```

Mixpanel batches events, so a screen's event can arrive one or two screens later. Count by
`Quiz Screen Order`, not by call order.

To list every other destination the funnel talks to:

```js
[...new Set(window.__cap.map(c => c.url.replace(location.origin, '').split('?')[0]))]
```

## 3. Clipboard hook

Records and suppresses clipboard writes, so a stray copy cannot overwrite the user's clipboard.

```js
window.__clip = [];
if (navigator.clipboard) {
  navigator.clipboard.writeText = t => { window.__clip.push({ via: 'writeText', t: String(t).slice(0, 300), path: location.search }); return Promise.resolve(); };
  navigator.clipboard.write = () => { window.__clip.push({ via: 'write', path: location.search }); return Promise.resolve(); };
}
const oe = document.execCommand.bind(document);
document.execCommand = (c, ...a) => /copy|cut/i.test(c) ? (window.__clip.push({ via: 'execCommand', c, path: location.search }), true) : oe(c, ...a);
document.addEventListener('copy', e => { window.__clip.push({ via: 'copyEvent', path: location.search }); e.preventDefault(); }, true);
'clipboard hooked';
```

## 4. Persisted super properties and campaign tags

Load the landing page with dummy tags first, for example `?utm_source=qa_test&utm_medium=qa&utm_campaign=qa_check`.

```js
const ck = decodeURIComponent((document.cookie.match(/mp_[0-9a-f]+_mixpanel=([^;]*)/) || [])[1] || '');
let j = {}; try { j = JSON.parse(ck); } catch (e) {}
({ superProps: Object.fromEntries(Object.entries(j).filter(([k]) => !/^__|device|distinct/.test(k))), setOnce: j.__mpso, deviceId: j.$device_id });
```

Use `deviceId` with the Mixpanel MCP `Get-Live-Events` (`search`) to confirm which project the
events landed in. Never paste the project token into a card or the repo.

## 5. Build fingerprint (dev only)

Sections 5 to 7 run inside an open page, so use them on dev only. Opening a live page fires
production analytics. For live, or to compare two hosts, run
`python3 "${CLAUDE_PLUGIN_ROOT}/skills/qa-web-onboarding/scripts/compare_builds.py"`, which
does all three without a browser.

The same hashed filename means the same code.

```js
const mf = [...document.scripts].map(s => s.src).find(s => /_buildManifest/.test(s));
const mt = await (await fetch(mf)).text();
({
  build: mf.split('/static/')[1].split('/')[0],
  routes: [...new Set([...mt.matchAll(/static\/chunks\/(pages\/[^"\\]+\.js)/g)].map(m => m[1]))].filter(p => /signup|_app/.test(p)),
  scripts: [...document.scripts].map(s => s.src.split('/chunks/')[1]).filter(Boolean),
});
```

## 6. Search shipped code for strings (dev only)

Loads the page chunks, the lazily loaded chunks and the route chunks, then reports which
file holds each pattern. Keep the checks list to the findings you are verifying.

```js
const wp = [...document.scripts].map(s => s.src).find(s => /webpack-/.test(s));
const wpt = await (await fetch(wp)).text();
const lazy = [...new Set([...wpt.matchAll(/(\d+):"([0-9a-f]{16})"/g)].map(m => `/_next/static/chunks/${m[1]}.${m[2]}.js`))];
const mf = [...document.scripts].map(s => s.src).find(s => /_buildManifest/.test(s));
const pages = [...new Set([...(await (await fetch(mf)).text()).matchAll(/static\/chunks\/(pages\/[^"\\]+\.js)/g)].map(m => '/_next/static/chunks/' + m[1]))];
const loaded = [...document.scripts].map(s => s.src).filter(u => /\/_next\/static\/chunks\//.test(u));
const files = [...new Set([...pages, ...lazy, ...loaded])];
const texts = await Promise.all(files.map(u => fetch(u).then(r => r.ok ? r.text() : '').catch(() => '')));
const checks = { 'Sleep depravation': /depravation/i, 'relative /privacy link': /href:"\/privacy"/, 'Over 56': /Over 56/ };
Object.fromEntries(Object.entries(checks).map(([k, re]) => [k, (files.find((f, i) => re.test(texts[i])) || 'not found').split('/').pop()]));
```

## 7. Legal links (dev only)

```js
const out = {};
for (const p of ['/privacy', '/terms', '/privacy-policy', '/terms-of-service']) {
  const r = await fetch(p, { redirect: 'follow' }); out[p] = { status: r.status, final: r.url };
}
out;
```
