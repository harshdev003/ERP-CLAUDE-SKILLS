// Usage: node signedout-users.js <profiler-dir>
// For ENGBACK-1536: denied writes to users/{id}, counts only (no ids, IPs or values).
const fs = require('fs');
const readline = require('readline');
const dir = process.argv[2];
const g = {};
const files = fs.readdirSync(dir); let pending = files.length;
for (const f of files) {
  const rl = readline.createInterface({ input: fs.createReadStream(`${dir}/${f}`), terminal: false });
  rl.on('line', (l) => {
    const s = l.indexOf('{'), e = l.lastIndexOf('}'); if (s < 0) return;
    let d; try { d = JSON.parse(l.slice(s, e + 1)); } catch { return; }
    if (!d || d.allowed !== false || !Array.isArray(d.path) || d.path[0] !== 'users' || !/write|update/.test(d.name || '')) return;
    const a = (d.client && d.client.userAgent) || {};
    const k = `${d.name} ${a.platform || '?'}/${a.os || '?'} ${(a.userAgentString || '').split(' ')[0]}`;
    const x = g[k] = g[k] || { n: 0, ids: new Set(), ips: new Set(), t0: Infinity, t1: 0, depth: {} };
    x.n++; x.ids.add(d.path[1]); x.ips.add(d.client && d.client.remoteAddress && d.client.remoteAddress.address);
    x.t0 = Math.min(x.t0, d.timestamp); x.t1 = Math.max(x.t1, d.timestamp);
    // Keep plain field names only; any key-like segment (ids, push ids, emails, numbers) becomes $id,
    // and anything deeper than one field is collapsed, so no identifier can reach the output.
    const isKey = (seg) => !/^[A-Za-z][A-Za-z0-9_]{0,40}$/.test(seg) || /\d/.test(seg) && seg.length >= 16;
    const rest = d.path.slice(2).map(String);
    const shape = !rest.length ? '(users/{id} itself)' : (isKey(rest[0]) ? '$id' : rest[0]) + (rest.length > 1 ? '/…' : '');
    x.depth[shape] = (x.depth[shape] || 0) + 1;
  });
  rl.on('close', () => {
    if (--pending) return;
    console.log('Signed-out writes to users/{id} (ENGBACK-1536), counts only:');
    for (const [k, x] of Object.entries(g)) console.log(`  ${x.n}\t${k}: ${x.ids.size} ids, ${x.ips.size} IPs, ${new Date(x.t0).toISOString().slice(11, 16)}-${new Date(x.t1).toISOString().slice(11, 16)} UTC, paths ${JSON.stringify(x.depth)}`);
    if (!Object.keys(g).length) console.log('  none');
  });
}
