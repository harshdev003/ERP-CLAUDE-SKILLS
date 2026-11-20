// Usage: node watched-nodes.js <profiler-dir> <node1,node2,...>
// Allowed/denied counts per watched top-level node, how many distinct ids and IPs the denies came from, and
// denied ops grouped by op, path shape and client. Counts only: no IPs, no keys.
const fs = require('fs');
const readline = require('readline');
const CHANGED = (process.argv[3] || '').split(',').map((s) => s.trim()).filter(Boolean);
const dir = process.argv[2];
const label = (c) => { const a = c && c.userAgent; if (!a) return 'unknown'; return `${a.platform || '?'}/${a.os || '?'} ${(a.userAgentString || '').split(' ')[0]}`; };
const isKey = (seg) => !/^[A-Za-z][A-Za-z0-9_]{0,40}$/.test(seg) || (/\d/.test(seg) && seg.length >= 16);
// Top-level node, then $id for the record key, then plain field names only; key-like segments become $id.
const shape = (p) => p.slice(0, 3).map((s, i) => (i === 0 ? s : (i === 1 || isKey(String(s)) ? '$id' : s))).join('/') + (p.length > 3 ? '/…' : '');
const c = {}; const tot = {};
const files = fs.readdirSync(dir); let pending = files.length;
for (const f of files) {
  const rl = readline.createInterface({ input: fs.createReadStream(`${dir}/${f}`), terminal: false });
  rl.on('line', (l) => {
    const s = l.indexOf('{'), e = l.lastIndexOf('}');
    if (s < 0) return;
    let d; try { d = JSON.parse(l.slice(s, e + 1)); } catch { return; }
    if (!d || !Array.isArray(d.path) || !CHANGED.includes(d.path[0]) || typeof d.allowed !== 'boolean') return;
    const n = d.path[0]; tot[n] = tot[n] || { ok: 0, denied: 0, ids: new Set(), ips: new Set() }; d.allowed ? tot[n].ok++ : tot[n].denied++;
    if (!d.allowed) {
      if (d.path.length > 1) tot[n].ids.add(String(d.path[1]));
      const ip = d.client && d.client.remoteAddress && d.client.remoteAddress.address;
      if (ip) tot[n].ips.add(ip);
    }
    if (!d.allowed) { const k = `${d.name} /${shape(d.path)} (${label(d.client)})`; c[k] = (c[k] || 0) + 1; }
  });
  rl.on('close', () => {
    if (--pending) return;
    console.log('Watched nodes, rule-checked ops:');
    for (const [n, v] of Object.entries(tot)) {
      const from = v.denied ? ` (from ${v.ids.size} ids, ${v.ips.size} IPs)` : '';
      console.log(`  ${n}: ${v.ok} allowed, ${v.denied} denied${from}`);
    }
    console.log('Denied on watched nodes:'); Object.entries(c).sort((a, b) => b[1] - a[1]).forEach(([k, v]) => console.log(`  ${v}\t${k}`));
  });
}
