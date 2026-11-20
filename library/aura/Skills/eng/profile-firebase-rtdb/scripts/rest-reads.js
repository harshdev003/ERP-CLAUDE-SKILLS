// Usage: node rest-reads.js <profiler-dir> [--source-detail <file>]
// Allowed REST reads (rest-read, rest-query, shallow gets) grouped by op, path shape and client, at any
// depth, so a REST burst of exact-path reads below the top level can be attributed after the fact.
// Counts only: no IPs, keys, uids or values. "ids" is how many distinct key segments a group touched.
// --source-detail also writes one JSON line per IP + user agent + UTC day (mode 600: it holds IPs), for
// the Security Incident Log: {ip, ua, client, day, t0, t1, n, shallow, listing, root, bytes, sized, shapes}.
// It must be written here: the raw profiler log is deleted when the profile ends.
// --probe-json appends one counts-only line per op + path shape + client for the RTDB Probe Log: every
// allowed REST read below a top-level node, except the Admin SDK's (root-attempts.js covers the root
// and whole nodes): {path, verdict, kind, op, client, tag, n, ips, t0, t1}.
const fs = require('fs');
const readline = require('readline');
const dir = process.argv[2];
const di = process.argv.indexOf('--source-detail');
const sourceDetail = di > 0 ? process.argv[di + 1] : null;
const pi = process.argv.indexOf('--probe-json');
const probeOut = pi > 0 ? process.argv[pi + 1] : null;
const ADMIN = /AdminNode|firebase-admin|Node\.js|node-fetch|axios/i;
const probes = {};
const TOP = 10;
// A key, not a field name: anything but a short plain identifier (a letters-only uid is 28 characters;
// an email key carries @ or .), or a long one with a digit.
const isKey = (seg) => !/^[A-Za-z][A-Za-z0-9_]{0,40}$/.test(seg) || seg.length >= 20 || (/\d/.test(seg) && seg.length >= 16);
// Same shape as watched-nodes.js: top-level node, $id for the record key, plain field names, key-like segments as $id.
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
// A shallow get (?shallow=true) is marked in the op name or its query; either way it is counted apart.
const shallowOf = (d) => /shallow/i.test(d.name) || /shallow/i.test(JSON.stringify(d.querySet || d.query || ''));
const groups = {};
const sources = {};
const total = { n: 0, shallow: 0, ips: new Set() };
const files = fs.readdirSync(dir); let pending = files.length;
function writeSources() {
  const fd = fs.openSync(sourceDetail, 'w', 0o600);
  fs.fchmodSync(fd, 0o600);
  for (const src of Object.values(sources)) {
    const iso = (t) => (Number.isFinite(t) && t ? new Date(t).toISOString() : null);
    fs.writeSync(fd, `${JSON.stringify({ ...src, t0: iso(src.t0), t1: iso(src.t1) })}\n`);
  }
  fs.closeSync(fd);
}
function writeProbes() {
  const lines = Object.values(probes).map((g) => JSON.stringify({
    ...g.probe, n: g.n, ips: g.ips.size, t0: new Date(g.t0).toISOString(), t1: new Date(g.t1).toISOString(),
  }) + '\n');
  fs.appendFileSync(probeOut, lines.join(''));
}
if (!pending) {
  if (sourceDetail) writeSources();
  if (probeOut) writeProbes();
  console.log('Allowed REST reads by path shape, counts only:\n  none');
}
for (const f of files) {
  const rl = readline.createInterface({ input: fs.createReadStream(`${dir}/${f}`), terminal: false });
  rl.on('line', (l) => {
    const s = l.indexOf('{'), e = l.lastIndexOf('}'); if (s < 0) return;
    let d; try { d = JSON.parse(l.slice(s, e + 1)); } catch { return; }
    if (!d || typeof d.name !== 'string' || !/^rest-/.test(d.name) || d.allowed === false) return;
    if (!/read|query|get|shallow/.test(d.name)) return;
    // The profiler logs a root operation with no "path" field.
    const path = Array.isArray(d.path) ? d.path.map(String) : (d.path == null ? [] : null);
    if (!path) return;
    const shallow = shallowOf(d);
    const op = shallow && !/shallow/i.test(d.name) ? `${d.name} (shallow)` : d.name;
    const a = (d.client && d.client.userAgent) || {};
    const ip = d.client && d.client.remoteAddress && d.client.remoteAddress.address;
    const k = `${op} /${shape(path)} (${clientLabel(a)})`;
    const g = groups[k] = groups[k] || { n: 0, ids: new Set(), ips: new Set(), t0: Infinity, t1: 0 };
    g.n++;
    path.forEach((seg, i) => { if (i > 0 && (i === 1 || isKey(seg))) g.ids.add(seg); });
    if (ip) { g.ips.add(ip); total.ips.add(ip); }
    if (typeof d.timestamp === 'number') { g.t0 = Math.min(g.t0, d.timestamp); g.t1 = Math.max(g.t1, d.timestamp); }
    total.n++; if (shallow) total.shallow++;
    if (probeOut && path.length > 1 && !ADMIN.test(a.userAgentString || '') && typeof d.timestamp === 'number') {
      const pk = `${op} /${shape(path)} (${clientLabel(a)})`;
      const pg = probes[pk] = probes[pk] || { n: 0, ips: new Set(), t0: Infinity, t1: 0, probe: {
        path: `/${shape(path)}`, verdict: 'Allowed', kind: 'REST read below a node', op, client: clientLabel(a), tag: 'below a node' } };
      pg.n++; if (ip) pg.ips.add(ip);
      pg.t0 = Math.min(pg.t0, d.timestamp); pg.t1 = Math.max(pg.t1, d.timestamp);
    }
    if (sourceDetail && ip) {
      const ua = a.userAgentString || '';
      const ts = typeof d.timestamp === 'number' ? d.timestamp : null;
      const dayOf = ts === null ? '' : new Date(ts).toISOString().slice(0, 10);
      const src = sources[`${ip}|${ua}|${dayOf}`] = sources[`${ip}|${ua}|${dayOf}`]
        || { ip, ua, client: clientLabel(a), day: dayOf, t0: Infinity, t1: 0, n: 0, shallow: 0, listing: 0, root: 0, bytes: 0, sized: 0, shapes: {} };
      src.n++;
      if (shallow) src.shallow++;
      if (/query/.test(d.name)) src.listing++;
      if (!path.length) src.root++;
      if (typeof d.bytes === 'number') { src.bytes += d.bytes; src.sized++; }
      const sh = `/${shape(path)}`;
      src.shapes[sh] = (src.shapes[sh] || 0) + 1;
      if (ts !== null) { src.t0 = Math.min(src.t0, ts); src.t1 = Math.max(src.t1, ts); }
    }
  });
  rl.on('close', () => {
    if (--pending) return;
    if (sourceDetail) writeSources();
    if (probeOut) writeProbes();
    console.log('Allowed REST reads by path shape, counts only:');
    if (!total.n) { console.log('  none'); return; }
    console.log(`  total ${total.n} (${total.shallow} shallow) from ${total.ips.size} IPs`);
    const rows = Object.entries(groups).sort((p, q) => q[1].n - p[1].n);
    const hm = (t) => (Number.isFinite(t) && t ? new Date(t).toISOString().slice(11, 16) : '?');
    for (const [k, g] of rows.slice(0, TOP)) {
      console.log(`  ${g.n}\t${k}: ${g.ids.size} ids, ${g.ips.size} IPs, ${hm(g.t0)}-${hm(g.t1)} UTC`);
    }
    if (rows.length > TOP) {
      const rest = rows.slice(TOP).reduce((x, [, g]) => x + g.n, 0);
      console.log(`  (+ ${rows.length - TOP} more shapes, ${rest} ops)`);
    }
  });
}
