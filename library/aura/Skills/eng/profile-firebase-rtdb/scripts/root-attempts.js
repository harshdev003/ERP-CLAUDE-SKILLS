// Usage: node root-attempts.js <profiler-dir> [rules.json] [probe-json-out] [--probe-detail <file>]
//        (or PROBE_OUT=<file>, PROBE_DETAIL=<file>)
// Every read of "/" or a whole top-level node ("/clients", "/users"...), and every DENIED operation at any
// depth (reads and writes; a path below the node is templated, keys as $id). Counts only: no IPs or keys.
// With an output path, also appends one JSON line per group, for the RTDB Probe Log:
//   {path, verdict, kind, op, client, tag, n, ips, t0, t1}
// where ips is a count of distinct addresses, never an address. The file is created even when there
// are no groups, so its presence means the summary ran. Routine reads (the apps' own SDK listens on
// whole nodes) are recorded too, with Kind "Routine app read", so they can be told from attempts.
// Shown in detail: every DENIED read, every read of ROOT, every read of a name with NO RULE (a guessed
// name: scanner probe), and every REST read of a whole node. Allowed app-SDK listens on whole nodes
// (catalog and config nodes the apps sync) are expected and only counted. Allowed Admin SDK traffic is
// excluded; a DENIED request is never excluded on its user agent, which anyone can send.
//
// Client is "internal (<plugin>:<skill>)" only for an ALLOWED request carrying the AuraClaude user agent:
// the agent is a label, and only admin credentials are allowed on these paths. A denied AuraClaude
// request keeps its plain label.
//
// --probe-detail appends one line per attempt, for the Security Incident Log only (mode 600; the caller
// deletes it after the run): {t, node, op, verdict, kind, client, ua, ip, bytes}. It holds every DENIED
// read shown in detail except the Aura apps' own SDK (platform "native": signed-out users, not probes),
// every ALLOWED read of the root or a guessed name, so the planner can tell the Firebase console's
// data viewer from anything else, and every ALLOWED rest-read or listener-listen of a whole real node
// that is not public by rule, from outside the apps (not "native"): the console's data viewer opened on one node. Those carry
// tag "rule exists"; the network check names them, and the planner logs one only when that check raised
// an ALERT on its address.
const fs = require('fs');
const readline = require('readline');
const argv = process.argv.slice(2);
let detailOut = process.env.PROBE_DETAIL || '';
const di = argv.indexOf('--probe-detail');
if (di >= 0) { detailOut = argv[di + 1] || ''; argv.splice(di, 2); }
const [dir, rulesPath, outArg] = argv;
const probeOut = outArg || process.env.PROBE_OUT || '';
const probeDetail = [];
let known = null;
// Nodes anyone may read (`.read: true`): an allowed read of one is not admin access.
const publicNodes = new Set();
try {
  const rules = JSON.parse(fs.readFileSync(rulesPath, 'utf8')).rules;
  known = new Set(Object.keys(rules).filter((k) => !k.startsWith('.')));
  for (const k of known) if (rules[k] && String(rules[k]['.read']) === 'true') publicNodes.add(k);
} catch { /* no rules file: tag as unknown */ }
// Probe Log "Kind" (see monitor-rtdb-probes references/probe-log-schema.md).
function kindOf(tag, isRest, denied, deep, write) {
  if (write) return 'Denied write';
  if (deep) return 'Denied read below a node';
  if (tag === 'ROOT') return 'Root read';
  if (tag === 'NO RULE (guessed name)') return 'Guessed name (scanner)';
  if (isRest) return 'Whole real node via REST';
  if (denied) return 'Denied read of a real node';
  return 'Unclassified (rule unknown)';
}
// Probe Log "Client": "{platform}/{os} {agent}", agent "browser" for Mozilla/* user agents.
// AuraClaude/<who>/<plugin>:<skill>@...: the label names the skill, never the person.
const OURS = /^AuraClaude\/(?:[^/\s:]+\/)?(\S+?)(?:@\S*)?(?:\s|$)/;
const noWho = (u) => u.replace(/^AuraClaude\/[^/\s:]+\/(?=\S)/, 'AuraClaude/');
function clientLabel(a, allowed) {
  const first = noWho((a.userAgentString || '').split(' ')[0]);
  const ours = allowed && (a.userAgentString || '').match(OURS);
  const agent = ours ? `internal (${ours[1]})` : (/^Mozilla\//.test(first) ? 'browser' : (first || 'unknown'));
  return `${a.platform || 'unknown'}/${a.os || 'unknown'} ${agent}`;
}
// Allowed whole-real-node reads written to probe detail (the console opened on one node).
const NODE_OPS = new Set(['rest-read', 'listener-listen']);
function detailRow(d, node, name, kind, tag, a, ip) {
  const denied = d.allowed === false;
  return {
    t: new Date(d.timestamp).toISOString(), node: `/${node}`, op: name, verdict: denied ? 'Denied' : 'Allowed',
    kind, tag, client: clientLabel(a, !denied), ua: a.userAgentString || '', ip: ip || 'none-recorded',
    bytes: typeof d.bytes === 'number' ? d.bytes : null,
  };
}
const READ = /^(rest-read|realtime-read|listener-listen|realtime-query|rest-query)|read|query/;
// A path below the node: the node, $id for a record key, plain field names, key-like segments as $id
// (the same shape as rest-reads.js and watched-nodes.js). Never a key.
// A key, not a field name: anything but a short plain identifier (a letters-only uid is 28 characters;
// an email key carries @ or .), or a long one with a digit.
const isKey = (seg) => !/^[A-Za-z][A-Za-z0-9_]{0,40}$/.test(seg) || seg.length >= 20 || (/\d/.test(seg) && seg.length >= 16);
const shape = (p) => p.slice(0, 3).map((s, i) => (i === 0 ? s : (i === 1 || isKey(String(s)) ? '$id' : s))).join('/') + (p.length > 3 ? '/…' : '');
function bump(k, probe, d, ip) {
  const x = detail[k] = detail[k] || { n: 0, ips: new Set(), t0: Infinity, t1: 0, probe };
  x.n++; x.ips.add(ip);
  x.t0 = Math.min(x.t0, d.timestamp); x.t1 = Math.max(x.t1, d.timestamp);
  return x;
}
const detail = {};
const routine = { n: 0, nodes: new Set() };
const files = fs.readdirSync(dir); let pending = files.length;
for (const f of files) {
  const rl = readline.createInterface({ input: fs.createReadStream(`${dir}/${f}`), terminal: false });
  rl.on('line', (l) => {
    const s = l.indexOf('{'), e = l.lastIndexOf('}'); if (s < 0) return;
    let d; try { d = JSON.parse(l.slice(s, e + 1)); } catch { return; }
    if (!d || !d.name) return;
    // The profiler logs an operation on the root with no "path" field at all; treat it as [].
    const path = Array.isArray(d.path) ? d.path.map(String) : (d.path == null ? [] : null);
    if (!path) return;
    const name = d.name || '';
    if (/unlisten/.test(name)) return;
    const a = (d.client && d.client.userAgent) || {};
    const ua = a.userAgentString || '';
    const denied = d.allowed === false;
    const write = !READ.test(name);
    // Below a node, or a write: only a denied one is an attempt; allowed ones are the apps' normal traffic.
    if (path.length > 1 || write) {
      if (!denied) return;
      const ip0 = d.client && d.client.remoteAddress && d.client.remoteAddress.address;
      const p = `/${shape(path)}`;
      bump(`DENIED ${name} ${p} (${a.platform || '?'}/${a.os || '?'} ${ua.split(' ')[0]})`, {
        path: p, verdict: 'Denied', kind: kindOf('', false, true, path.length > 1, write), op: name,
        client: clientLabel(a, false), tag: path.length > 1 ? 'below a node' : 'write' }, d, ip0);
      return;
    }
    if (!denied && /AdminNode|firebase-admin|Node\.js|node-fetch|axios/i.test(ua)) return;
    const node = path[0] || '';
    const tag = node === '' ? 'ROOT' : (!known ? 'rule unknown' : (known.has(node) ? 'rule exists' : 'NO RULE (guessed name)'));
    const isRest = /^rest-/.test(name);
    const ip = d.client && d.client.remoteAddress && d.client.remoteAddress.address;
    const nodeRead = !denied && tag === 'rule exists' && !publicNodes.has(node) && a.platform !== 'native' && NODE_OPS.has(name);
    if (!denied && !isRest && tag === 'rule exists') {
      // An SDK read of a whole real node: the apps' own sync is routine and logged as such; one from outside
      // the apps (the console's data viewer) also reaches probe detail for the network check.
      const outside = a.platform !== 'native';
      const kind = !outside ? 'Routine app read' : (publicNodes.has(node) ? 'Public node read'
        : (nodeRead ? 'Whole real node via SDK listen' : 'Whole real node via SDK read'));
      if (detailOut && nodeRead) probeDetail.push(detailRow(d, node, name, kind, tag, a, ip));
      if (!outside) { routine.n++; routine.nodes.add(node); }
      bump(`${kind} ${name} /${node} (${a.platform || '?'}/${a.os || '?'} ${ua.split(' ')[0]})`, {
        path: `/${node}`, verdict: 'Allowed', kind, op: name, client: clientLabel(a, true), tag }, d, ip);
      return;
    }
    const k = `${denied ? 'DENIED ' : 'allowed'} ${name} /${node} [${tag}] (${a.platform || '?'}/${a.os || '?'} ${ua.split(' ')[0]})`;
    // An allowed REST read of a public node is not an attempt and never reaches the Incident Log.
    const kind = !denied && tag === 'rule exists' && publicNodes.has(node) ? 'Public node read' : kindOf(tag, isRest, denied);
    const x = bump(k, { path: `/${node}`, verdict: denied ? 'Denied' : 'Allowed', kind, op: name, client: clientLabel(a, !denied), tag }, d, ip);
    if (detailOut && (denied ? a.platform !== 'native' : (tag === 'ROOT' || tag === 'NO RULE (guessed name)' || nodeRead))) {
      probeDetail.push(detailRow(d, node, name, x.probe.kind, tag, a, ip));
    }
  });
  rl.on('close', () => {
    if (--pending) return;
    console.log('Root / whole top-level node reads and denied operations (allowed Admin SDK excluded), counts only:');
    const all = Object.entries(detail).sort((p, q) => q[1].n - p[1].n);
    // The text summary shows attempts; routine reads go to the probe JSON and a one-line count.
    const rows = all.filter(([, x]) => x.probe.kind !== 'Routine app read' && x.probe.kind !== 'Public node read');
    if (!rows.length) console.log('  none (no denied, root, guessed-name or REST whole-node reads)');
    for (const [k, x] of rows) console.log(`  ${x.n}\t${k}: ${x.ips.size} IPs, ${new Date(x.t0).toISOString().slice(11, 16)}-${new Date(x.t1).toISOString().slice(11, 16)} UTC`);
    console.log(`  (+ ${routine.n} allowed app-SDK listens on ${routine.nodes.size} whole catalog/config nodes, expected; in the probe JSON as routine reads)`);
    if (probeOut) {
      const lines = all.map(([, x]) => JSON.stringify({
        ...x.probe, n: x.n, ips: x.ips.size, t0: new Date(x.t0).toISOString(), t1: new Date(x.t1).toISOString(),
      }) + '\n');
      fs.appendFileSync(probeOut, lines.join(''));
    }
    if (detailOut) {
      // Created even when empty, like the probe JSON: its presence means the summary ran.
      probeDetail.sort((p, q) => (p.t < q.t ? -1 : p.t > q.t ? 1 : 0));
      const fd = fs.openSync(detailOut, 'a', 0o600);
      fs.fchmodSync(fd, 0o600);
      fs.writeSync(fd, probeDetail.map((r) => JSON.stringify(r) + '\n').join(''));
      fs.closeSync(fd);
      console.log(`  probe detail: ${probeDetail.length} attempt(s) written for the Security Incident Log`);
    }
  });
}
