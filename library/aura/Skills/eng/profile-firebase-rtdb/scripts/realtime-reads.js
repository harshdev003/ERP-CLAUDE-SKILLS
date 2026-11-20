// Usage: node realtime-reads.js <profiler-dir>
// Allowed realtime reads (realtime-read, realtime-query, listener-listen) grouped by op, path shape, auth
// and client, at any depth, so a realtime read surge (the "RTDB realtime read surge" alert) can be
// attributed after the fact: the raw profile stream is deleted when the run ends (ENG-1765).
// Counts only: no IPs, keys, uids or values. "ids" is how many distinct key segments a group touched;
// "bytes" is what the profiler recorded as sent.
// auth is "admin" for Admin SDK traffic and "client" for everything else. The profiler records no auth
// field, so it is read from the user agent, with the same pattern root-attempts.js uses to set aside
// allowed Admin SDK reads (plus an "admin" platform, should the profiler report one).
const fs = require('fs');
const readline = require('readline');
const dir = process.argv[2];
const TOP = 10;
const OPS = /^(realtime-read|realtime-query|listener-listen)$/;
const isKey = (seg) => !/^[A-Za-z][A-Za-z0-9_]{0,40}$/.test(seg) || (/\d/.test(seg) && seg.length >= 16);
// Same shape as rest-reads.js and watched-nodes.js: $id for the record key and key-like segments.
const shape = (p) => p.slice(0, 3).map((s, i) => (i === 0 ? s : (i === 1 || isKey(String(s)) ? '$id' : s))).join('/') + (p.length > 3 ? '/…' : '');
// AuraClaude/<who>/<plugin>:<skill>@...: the label names the skill, never the person.
const OURS = /^AuraClaude\/(?:[^/\s:]+\/)?(\S+?)(?:@\S*)?(?:\s|$)/;
const noWho = (u) => u.replace(/^AuraClaude\/[^/\s:]+\/(?=\S)/, 'AuraClaude/');
function clientLabel(a) {
  const ua = a.userAgentString || '';
  const first = noWho(ua.split(' ')[0]);
  const ours = ua.match(OURS);
  const agent = ours ? `internal (${ours[1]})` : (/^Mozilla\//.test(first) ? 'browser' : (first || 'unknown'));
  return `${a.platform || 'unknown'}/${a.os || 'unknown'} ${agent}`;
}
const ADMIN_UA = /AdminNode|firebase-admin|Node\.js|node-fetch|axios/i;
const authOf = (a) => (a.platform === 'admin' || ADMIN_UA.test(a.userAgentString || '') ? 'admin' : 'client');
const groups = {};
const total = { n: 0, bytes: 0, ips: new Set(), admin: 0 };
const files = fs.readdirSync(dir); let pending = files.length;
const hm = (t) => (Number.isFinite(t) && t ? new Date(t).toISOString().slice(11, 16) : '?');
function report() {
  console.log('Allowed realtime reads by path shape, counts only:');
  if (!total.n) { console.log('  none'); return; }
  console.log(`  total ${total.n} ops (${total.admin} admin), ${total.bytes} bytes, from ${total.ips.size} IPs`);
  const rows = Object.entries(groups).sort((p, q) => q[1].n - p[1].n);
  for (const [k, g] of rows.slice(0, TOP)) {
    console.log(`  ${g.n}\t${k}: ${g.bytes} bytes, ${g.ids.size} ids, ${g.ips.size} IPs, ${hm(g.t0)}-${hm(g.t1)} UTC`);
  }
  if (rows.length > TOP) {
    const rest = rows.slice(TOP).reduce((x, [, g]) => x + g.n, 0);
    console.log(`  (+ ${rows.length - TOP} more shapes, ${rest} ops)`);
  }
}
if (!pending) report();
for (const f of files) {
  const rl = readline.createInterface({ input: fs.createReadStream(`${dir}/${f}`), terminal: false });
  rl.on('line', (l) => {
    const s = l.indexOf('{'), e = l.lastIndexOf('}'); if (s < 0) return;
    let d; try { d = JSON.parse(l.slice(s, e + 1)); } catch { return; }
    if (!d || typeof d.name !== 'string' || !OPS.test(d.name) || d.allowed === false) return;
    // The profiler logs a root operation with no "path" field.
    const path = Array.isArray(d.path) ? d.path.map(String) : (d.path == null ? [] : null);
    if (!path) return;
    const a = (d.client && d.client.userAgent) || {};
    const ip = d.client && d.client.remoteAddress && d.client.remoteAddress.address;
    const auth = authOf(a);
    const k = `${d.name} /${shape(path)} ${auth} (${clientLabel(a)})`;
    const g = groups[k] = groups[k] || { n: 0, bytes: 0, ids: new Set(), ips: new Set(), t0: Infinity, t1: 0 };
    g.n++;
    if (typeof d.bytes === 'number') { g.bytes += d.bytes; total.bytes += d.bytes; }
    path.forEach((seg, i) => { if (i > 0 && (i === 1 || isKey(seg))) g.ids.add(seg); });
    if (ip) { g.ips.add(ip); total.ips.add(ip); }
    if (typeof d.timestamp === 'number') { g.t0 = Math.min(g.t0, d.timestamp); g.t1 = Math.max(g.t1, d.timestamp); }
    total.n++; if (auth === 'admin') total.admin++;
  });
  rl.on('close', () => { if (!--pending) report(); });
}
