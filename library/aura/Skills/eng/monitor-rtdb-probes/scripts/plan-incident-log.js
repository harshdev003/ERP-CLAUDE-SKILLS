#!/usr/bin/env node
// Plan the Security Incident Log writes for one profiler run. Pure: reads three files, prints a JSON
// plan, writes nothing.
//
// Usage: node plan-incident-log.js <detail.jsonl> <rows.json> [networks.json] [--verdicts verdicts.json]
//                                  [--rest-bursts rest-bursts.jsonl] [--reclassify reclassify.json]
//
// detail.jsonl   profile-firebase-rtdb root-attempts.js --probe-detail lines:
//                {t, node, op, verdict, kind, client, ua, ip, bytes}
// rows.json      the log's Firebase RTDB rows as Notion REST pages: {results: [...]} or the array
//                (incident-log.js rows prints this)
// networks.json  {ip: {asn, owner}} from lookup-networks.js; an IP missing from it has only its /16
// verdicts.json  {attributed: [ip], console: [ip], teammate: [ip], alert: [ip], fail: [ip]} from monitor-firebase's
//                check_networks.py --verdicts: addresses whose read was a known teammate's, those with a
//                console account in the audit log within 2 minutes, 0-byte reads from a Confirmed network
//                (`teammate`: a teammate's realtime read, never logged, counted), and every address it raised an ALERT on
//                (its whole-node reads are logged with the address), and every address it could not
//                classify (`fail`, logged the same way). Missing or unreadable: no verdicts
//                (root and guessed-name reads all logged; whole-node reads stay with the network check).
//                rest_teammate: REST-burst sources on a Confirmed Known Team Networks row (never logged)
//                confirmed: addresses on a Confirmed Known Team Networks row; any read of theirs that is
//                logged, allowed or denied, shows KNOWN_NETWORK in place of the address
// rest-bursts    monitor-firebase evaluate_profile.py --rest-bursts lines, one per IP + user agent + UTC
//                day of a REST burst: {ip, ua, client, day, t0, t1, n, shallow, listing, root, bytes, sized,
//                shapes, minutes, nonpublic}. One row per IP + user agent + UTC day.
//
// Output: {creates: [{key, sweeps, properties}], updates: [{page_id, sweeps, properties}], alerts,
// reopened, console, attributed, allowed_flags, summary}. `properties` use the Notion REST property format. Rules:
// references/incident-log-schema.md.
'use strict';
const fs = require('fs');

const SWEEP_GAP_MS = 30 * 60 * 1000;
// A group with no hit for this long is closed; its next hit opens a new row (shared with eng:flag-suspicious-traffic).
const GROUP_CLOSE_MS = 2 * 24 * 60 * 60 * 1000;  // Jerome, 2026-11-03
// One IP's denied sweep at or above this many attempts is a flood: Medium.
const FLOOD_ATTEMPTS = 100;
const SYSTEM = 'Firebase RTDB';
// The group rows hold every RTDB sweep up to this minute, some without a line of their own or with it in
// another type's row (the per-IP rows were merged into them). Later sweeps are judged per group row only:
// one row's write is a single request, so a partial apply can't make another group's sweep look logged.
const REGROUPED_UNTIL = Date.parse('2026-10-24T05:16:00Z');
const FOUND_BY = 'eng:monitor-rtdb-probes';
const SEVERITY = ['Info', 'Low', 'Medium', 'High'];
// REST bursts (Security, 2026-11-03): one IP over this many reads per 10 min is Medium, and a source of
// non-public data at or over LARGE_BYTES sent is High.
const BURST_IP_READS = 1000;
const LARGE_BYTES = 1000000;
// "<date> HH:MM[-HH:MM]Z <ip>: ..." — the date only when the sweep's day differs from the row's Detected.
const LINE = /^(?:(\d{4}-\d{2}-\d{2}) )?(\d{2}:\d{2})(?:-\d{2}:\d{2})?Z (see Known Team Networks|\S+): /;
// A staff address never goes in the log: a read from a Confirmed Known Team Networks row, allowed or
// denied, is written with this in place of its IP (Jerome, 2026-11-09). The network stays in Source network.
const KNOWN_NETWORK = 'see Known Team Networks';
// Staff reads get their own group row, with no Category (the log's categories are outside access) and the
// matched network named in Notes instead of a request to identify the reader.
const STAFF = 'staff';
// Actor is exactly "Staff" so the log filters on it (Jerome, 2026-11-10); a likely teammate's name and
// "not confirmed" go in Notes.
const STAFF_ACTOR = 'Staff';
// Staff / Not staff, a select beside Actor (Jerome, 2026-11-11). A staff group row, or Actor "Staff", is
// "Staff". An existing row gets it only when this skill writes the row anyway and it has no value, so a
// value a person set stays; the one change made is "Not staff" to "Staff" when a teammate match turns an
// unclassified Actor into "Staff". incident-log.js leaves it out until the column exists.
const staffOf = (r) => (r.staff || (r.group || '').includes(`|${STAFF}|`) || r.actor === STAFF_ACTOR ? 'Staff' : 'Not staff');
const staffNotes = (networks) => `A read from a Confirmed Known Team Networks row (${networks || 'network not resolved'}): a teammate's read, not outside access.`;

// Attack type from the profiler's kind. Shared format: `<system>|<attack-type>|<outcome>`.
const TYPE = { 'Root read': 'root-read', 'Guessed name (scanner)': 'guessed-name' };
const typeOf = (a) => TYPE[a.kind] || 'node-read';
const CATEGORY = { 'root-read': 'Other unrecognized access', 'guessed-name': 'Forced browsing', 'node-read': 'Other unrecognized access', 'rest-burst': 'Scraping / abuse' };
const TITLE = { 'root-read': 'RTDB root reads', 'guessed-name': 'RTDB guessed node names', 'node-read': 'RTDB whole-node reads', 'rest-burst': 'RTDB REST read bursts' };
const OUTCOME = {
  denied: 'Denied by rules', 'data-accessed': 'Data accessed', obtained: 'Partial impact', 'no-data': 'Allowed (no data)',
  'public-data': 'Allowed (public data)',
};
const NOTES = {
  denied: 'All denied by rules; no data returned. One line per sweep in "What they did".',
  'data-accessed': 'Rules allowed the read and data came back. Find out whose request it was first.',
  obtained: 'Rules allowed the read (no data recorded). Find out whose request it was first.',
  'no-data': 'Rules allowed the read and 0 bytes came back. Find out whose request it was.',
};
// Every path in this row is also a row of the RTDB Probe Log, counts only.
const PROBE_LOG_NOTE = 'Each path read is also a row in the RTDB Probe Log (Path + Op + Verdict, counts only).';
const BURST_NOTES = {
  'public-data': 'A REST burst of public-by-rule paths. One row per path family and outcome while it has a hit within 2 days; one line per source per pass.',
  'data-accessed': 'A REST burst read per-user or non-public paths. Check the rule for those paths and raise a card. One row per path family and outcome within 2 days.',
  'no-data': 'A REST burst whose reads came back with 0 bytes. One row per path family and outcome within 2 days.',
};
// Allowed reads group by network too: each row is one actor to identify, and a row's network and dates
// must not absorb another network's read. Denied rows hold every network's scanner sweeps of one type.
const BY_NETWORK = (outcome) => outcome !== 'denied';
// Our own outcome per allowed attempt: bytes > 0 is data, exactly 0 is none, missing is not recorded.
const allowedOutcome = (a) => (typeof a.bytes !== 'number' ? 'obtained' : a.bytes > 0 ? 'data-accessed' : 'no-data');

const ms = (v) => { const n = Date.parse(String(v || '').trim().replace(' ', 'T')); return Number.isNaN(n) ? null : n; };
const floorMin = (t) => t - (t % 60000);
const isoMin = (t) => new Date(floorMin(t)).toISOString();
const day = (t) => new Date(t).toISOString().slice(0, 10);
const hhmm = (t) => new Date(t).toISOString().slice(11, 16);
const sweepKey = (t, ip) => `${new Date(t).toISOString().slice(0, 16)}|${ip}`;
const rank = (s) => SEVERITY.indexOf(s);

function prefix16(ip) {
  if (!ip || ip === 'none-recorded') return '';
  if (ip.includes('.') && !ip.includes(':')) return `${ip.split('.').slice(0, 2).join('.')}.0.0/16`;
  return `${ip.split(':')[0]}::/16`;
}
function network(ip, networks) {
  const n = (networks && networks[ip]) || {};
  const prefix = prefix16(ip);
  const asn = n.asn && /^\d+$/.test(String(n.asn)) ? String(n.asn) : '';
  return {
    key: asn ? `AS${asn}` : prefix,
    owner: n.owner || '',
    label: [n.owner, asn && `AS${asn}`, prefix].filter(Boolean).join(' ') || 'unknown network',
  };
}
function inNetworkText(text, net) {
  if (!net.key) return false;
  if (net.key.startsWith('AS')) return new RegExp(`\\b${net.key}\\b`).test(text);
  // A /16 label must stand alone: 1.2.0.0/16 is a substring of 11.2.0.0/16.
  const esc = net.key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return new RegExp(`(^|[^0-9A-Fa-f.:])${esc}(?![0-9])`).test(text);
}

// --- Notion REST values -------------------------------------------------------------------------
function plain(prop) {
  if (!prop) return '';
  const arr = prop.rich_text || prop.title || [];
  return arr.map((x) => x.plain_text ?? (x.text && x.text.content) ?? '').join('');
}
// Notion caps one rich_text item at 2,000 characters and a property at 100 items. Text is split into
// items rather than truncated, so IP addresses and user agents are never lost on a merge (Jerome: they
// feed Cloud Armor bans). Shared with eng:flag-suspicious-traffic.
const CHUNK = 1990;
const MAX_CHUNKS = 100;
const MAX_TEXT = CHUNK * MAX_CHUNKS;
function text(s) {
  const str = String(s);
  const chunks = [];
  for (let i = 0; i < str.length && chunks.length < MAX_CHUNKS; i += CHUNK) chunks.push(str.slice(i, i + CHUNK));
  return { rich_text: (chunks.length ? chunks : ['']).map((c) => ({ text: { content: c } })) };
}
// What they did: the newest lines that fit the property. A sweep whose line was dropped is older than
// every kept one. IPs and user agents have their own columns, which are never trimmed.
function fitLines(s) {
  const lines = String(s).split('\n');
  while (lines.length > 1 && lines.join('\n').length > MAX_TEXT) lines.shift();
  return lines.join('\n').slice(0, MAX_TEXT);
}
const dateProp = (t) => ({ date: { start: isoMin(t) } });

// User agent column: one per line, "ua (n)". Entries without a count are kept as they are; rows written
// before grouping used "ua (n); ua (n)" on one line.
function parseUas(s) {
  const counted = new Map(), legacy = [];
  const toks = String(s || '').split('\n').flatMap((l) => l.split(/(?<=\(\d+\)); /));
  for (const tok of toks.filter((x) => x.trim())) {
    const m = tok.match(/^(.*) \((\d+)\)$/s);
    if (m) counted.set(m[1], (counted.get(m[1]) || 0) + Number(m[2])); else legacy.push(tok);
  }
  return { counted, legacy };
}
function uaText({ counted, legacy }) {
  // An uncounted line that repeats, or names a counted entry, is the same agent: listed once.
  const kept = [...new Set(legacy.map((l) => l.trim()))].filter((l) => !counted.has(l));
  return [...kept, ...[...counted].sort((a, b) => b[1] - a[1]).map(([u, c]) => `${u} (${c})`)].join('\n');
}
// Only a real address: a dotted version in a user agent (Chrome/140.0.0.0, AppleWebKit/537.36) is not one.
const IPV4 = /^(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)$/;
const isIp = (x) => IPV4.test(x) || (/^[0-9a-f:]+$/i.test(x) && (x.match(/:/g) || []).length >= 2);
function splitIps(s) {
  return String(s || '').split(/[,;]\s*/).map((x) => x.trim())
    .map((x) => (x === KNOWN_NETWORK ? x : x.split(' ')[0])).filter((x) => x === KNOWN_NETWORK || isIp(x));
}

function normalizeRow(page) {
  const p = page.properties || {};
  const sel = (k) => (p[k] && p[k].select && p[k].select.name) || '';
  const dt = (k) => ms(p[k] && p[k].date && p[k].date.start);
  const row = {
    page_id: page.id,
    system: sel('System'),
    status: sel('Status'),
    group: plain(p['Group key']).trim(),
    severity: sel('Severity'),
    detected: dt('Detected (UTC)'),
    ended: dt('Ended (UTC)'),
    ips: splitIps(plain(p['IP addresses'])),
    uas: parseUas(plain(p['User agent'])),
    route: plain(p['Route / path']),
    networks: plain(p['Source network']),
    what: plain(p['What they did']),
    requests: (p.Requests && p.Requests.number) || 0,
    maxIps: (p['Max IPs'] && p['Max IPs'].number) || 0,
    managed: plain(p['Found by']).includes(FOUND_BY),
    actor: plain(p.Actor).trim(),
    staffValue: sel('Staff'),
    notes: plain(p.Notes),
    changed: new Set(),
  };
  row.applied = appliedKeys(row);
  row.storedEnded = row.ended;
  return row;
}
function appliedKeys(row) {
  const keys = new Set();
  const base = row.detected === null ? '' : day(row.detected);
  for (const l of row.what.split('\n')) {
    const m = l.match(LINE);
    if (m && (m[1] || base)) keys.add(`${m[1] || base}T${m[2]}|${m[3]}`);
  }
  return keys;
}

// --- Sweeps ---------------------------------------------------------------------------------------
function bySweeps(lines) {
  const perIp = new Map();
  for (const l of lines) {
    const t = ms(l.t);
    if (t === null || !l.ip) continue;
    if (!perIp.has(l.ip)) perIp.set(l.ip, []);
    perIp.get(l.ip).push({ ...l, ts: t });
  }
  const sweeps = [];
  for (const [ip, list] of perIp) {
    list.sort((a, b) => a.ts - b.ts);
    let cur = null;
    for (const a of list) {
      if (!cur || a.ts - cur.t1 > SWEEP_GAP_MS) { cur = { ip, t0: a.ts, t1: a.ts, attempts: [] }; sweeps.push(cur); }
      cur.t1 = a.ts;
      cur.attempts.push(a);
    }
  }
  return sweeps.sort((a, b) => a.t0 - b.t0 || (a.ip < b.ip ? -1 : 1));
}

function countOrdered(values) {
  const m = new Map();
  for (const v of values) m.set(v, (m.get(v) || 0) + 1);
  return m;
}
function transport(attempts) {
  const rest = attempts.filter((a) => /^rest-/.test(a.op)).length;
  return rest === attempts.length ? 'REST' : rest === 0 ? 'realtime' : 'REST and realtime';
}
const verb = (outcome) => (outcome === 'denied' ? 'denied' : 'allowed');
// The person in our user agent (AuraClaude/<who>/...) stays in the local capture; rows and Slack get AuraClaude/<plugin>:<skill>.
const noWho = (u) => String(u).replace(/^AuraClaude\/[^/\s:]+\/(?=\S)/, 'AuraClaude/');
const shownUa = (a) => noWho(a.ua || a.client || 'no user agent');
function whatLine(s, withDate) {
  const n = s.attempts.length;
  const tr = transport(s.attempts);
  const desc = tr === 'REST' ? 'unauthenticated REST' : tr;
  const paths = [...countOrdered(s.attempts.map((a) => a.node))].map(([p, c]) => (c > 1 ? `${p} x${c}` : p)).join(', ');
  const uas = [...countOrdered(s.attempts.map((a) => shownUa(a)))].map(([u]) => u).join('; ');
  const span = floorMin(s.t1) > floorMin(s.t0) ? `${hhmm(s.t0)}-${hhmm(s.t1)}` : hhmm(s.t0);
  return `${withDate ? `${day(s.t0)} ` : ''}${span}Z ${s.ip}: ${n} ${verb(s.outcome)} ${desc} ${n === 1 ? 'read' : 'reads'} of ${paths} [${uas}]${unclassifiedTag(s.attempts)}`;
}
const TAG_RE = / \((?:un)?classified[^)]*\)$/;
// " (unclassified: Notion rate-limited)" on a line whose reads the network check could not classify.
function unclassifiedTag(attempts) {
  const why = (attempts.find((a) => a.unclassified) || {}).unclassified;
  return why ? ` (unclassified: ${why})` : '';
}
function sweepSeverity(s) {
  if (s.outcome === 'no-data') return 'Low';
  if (s.outcome !== 'denied') return 'High';
  return s.attempts.length >= FLOOD_ATTEMPTS ? 'Medium' : 'Low';
}
function routeText(paths, tr) { return `${paths.join(', ')} via ${tr}`; }
function mergeRoute(route, paths, tr) {
  if (!route) return routeText(paths, tr);
  const i = route.lastIndexOf(' via ');
  const base = i >= 0 ? route.slice(0, i) : route;
  const suffix = i >= 0 ? route.slice(i) : '';
  const have = new Set(base.split(/,\s*/).map((x) => x.trim()));
  if (/\(\+ root\)/.test(base)) have.add('/');
  const add = paths.filter((p) => !have.has(p));
  return add.length ? `${base}, ${add.join(', ')}${suffix}` : route;
}
function addAttempts(row, attempts, tr) {
  row.requests += attempts.length; row.changed.add('Requests');
  for (const [u, c] of countOrdered(attempts.map((a) => shownUa(a)))) {
    row.uas.counted.set(u, (row.uas.counted.get(u) || 0) + c);
  }
  row.changed.add('User agent');
  const route = mergeRoute(row.route, [...new Set(attempts.map((a) => a.node))], tr);
  if (route !== row.route) { row.route = route; row.changed.add('Route / path'); }
}
function raise(row, severity) {
  if (rank(severity) > rank(row.severity)) { row.severity = severity; row.changed.add('Severity'); }
}
// A sweep already logged that has since grown (the IP kept going inside the sweep gap after a run was
// applied): rewrite its line and add only the new attempts. The line's own count tells how many attempts
// it already holds; attempts are in time order, so the rest are the new ones.
function extendSweep(row, s, key) {
  const base = row.detected === null ? '' : day(row.detected);
  const lines = row.what.split('\n');
  const keyOf = (l) => { const m = l.match(LINE); return m ? `${m[1] || base}T${m[2]}|${m[3]}` : null; };
  const count = (l) => Number((l.match(/: (\d+) (?:denied|allowed) /) || [])[1]);
  const i = lines.findIndex((l) => keyOf(l) === key);
  if (i < 0) return false;
  const n = count(lines[i]);
  if (!n) return false;
  // Later lines of the same sweep, logged by runs that saw only part of it: their attempts are already
  // counted, so they are folded into this line and only attempts no line holds are added.
  const covered = new Set();
  const folded = [];
  for (let j = 0; j < lines.length; j += 1) {
    if (j === i) continue;
    const k = keyOf(lines[j]);
    const start = s.attempts.findIndex((a, idx) => idx >= n && !covered.has(idx) && sweepKey(a.ts, s.ip) === k);
    if (start < 0) continue;
    for (let c = 0; c < count(lines[j]) && start + c < s.attempts.length; c += 1) covered.add(start + c);
    folded.push(j);
  }
  const added = s.attempts.filter((a, idx) => idx >= n && !covered.has(idx));
  if (!added.length && !folded.length) return false;
  // A line's "(unclassified|classified …)" tag survives the rewrite unless this run tags it afresh.
  const kept = lines[i].match(TAG_RE);
  lines[i] = whatLine(s, Boolean(lines[i].match(LINE)[1]));
  if (kept && !TAG_RE.test(lines[i])) lines[i] += kept[0];
  row.what = lines.filter((l, j) => !folded.includes(j)).join('\n');
  row.changed.add('What they did');
  if (row.ended === null || floorMin(s.t1) > row.ended) { row.ended = floorMin(s.t1); row.changed.add('Ended (UTC)'); }
  if (added.length) addAttempts(row, added, transport(s.attempts));
  raise(row, sweepSeverity(s));
  (row.sweeps = row.sweeps || []).push(key);
  return true;
}

// A window that starts inside a logged sweep sees only its tail, which starts at no line's minute. The
// line for the same IP whose span holds the sweep's start already counts every attempt up to its end:
// only later attempts are added, and that line is stretched to hold them.
const SPAN = /^(?:(\d{4}-\d{2}-\d{2}) )?(\d{2}:\d{2})(?:-(\d{2}:\d{2}))?Z (see Known Team Networks|\S+): (\d+) (denied|allowed) (.+?) reads? of (.*) \[(.*)\]( \((?:un)?classified[^)]*\))?$/;
function coveringLine(row, s) {
  const base = row.detected === null ? '' : day(row.detected);
  const lines = row.what.split('\n');
  for (let i = 0; i < lines.length; i += 1) {
    const m = lines[i].match(SPAN);
    if (!m || m[4] !== s.ip || !(m[1] || base)) continue;
    const start = Date.parse(`${m[1] || base}T${m[2]}:00Z`);
    let end = m[3] ? Date.parse(`${m[1] || base}T${m[3]}:00Z`) : start;
    if (end < start) end += 24 * 60 * 60 * 1000;
    const t0 = floorMin(s.t0);
    if (start <= t0 && t0 <= end) return { i, m, start, end, lines };
  }
  return null;
}
function extendCovered(row, s, c) {
  const added = s.attempts.filter((a) => floorMin(a.ts) > c.end);
  if (!added.length) return false;
  const [, date, from, , ip, n, verbWord, desc, pathText, uaText, tag] = c.m;
  const paths = new Map();
  for (const p of pathText.split(', ')) {
    const pm = p.match(/^(.*) x(\d+)$/);
    paths.set(pm ? pm[1] : p, (paths.get(pm ? pm[1] : p) || 0) + (pm ? Number(pm[2]) : 1));
  }
  for (const a of added) paths.set(a.node, (paths.get(a.node) || 0) + 1);
  const uas = [...new Set([...uaText.split('; '), ...added.map((a) => shownUa(a))])];
  const total = Number(n) + added.length;
  const last = hhmm(Math.max(...added.map((a) => a.ts)));
  const shown = [...paths].map(([p, k]) => (k > 1 ? `${p} x${k}` : p)).join(', ');
  c.lines[c.i] = `${date ? `${date} ` : ''}${from}-${last}Z ${ip}: ${total} ${verbWord} ${desc} reads of ${shown} [${uas.join('; ')}]${tag || ''}`;
  row.what = c.lines.join('\n');
  row.changed.add('What they did');
  const t1 = floorMin(Math.max(...added.map((a) => a.ts)));
  if (row.ended === null || t1 > row.ended) { row.ended = t1; row.changed.add('Ended (UTC)'); }
  addAttempts(row, added, transport(s.attempts));
  raise(row, sweepSeverity({ outcome: s.outcome, attempts: { length: total } }));
  return true;
}

// --- Console and allowed attempts -------------------------------------------------------------------
// The Firebase console's data viewer: an allowed root listener-listen plus a 0-byte rest-read, both from
// a desktop browser, from one IP within one sweep. A root listener-listen alone is the console too when
// the audit log shows a console account at that time (verdicts.console): the viewer doesn't always send
// the rest-read. From an address check_networks named the console (verdicts.console), a sweep of desktop
// browser listens and data-less rest-reads on any node is the console too: the viewer opened on a
// mistyped node is a guessed name, not root (2026-10-30 17:13Z). A read that returned data is still
// logged. A read check_networks attributed to a known teammate (verdicts.attributed) is never logged.
const desktopBrowser = (a) => /^desktop\//.test(a.client || '') && / browser$/.test(a.client || '');
// An allowed read of a whole node that has a rule (tag "rule exists") is admin access to a real node: the
// console's data viewer opened on one node. monitor-firebase's network check names it, and it is logged only
// when that check raised an ALERT on the address (verdicts.alert) or could not classify it (verdicts.fail):
// an unconfirmed read stays in the log with its address, a confirmed teammate's never does (Jerome, 2026-10-30).
const isNodeRead = (a) => a.tag === 'rule exists';
function splitAllowed(allowed, verdicts) {
  const console = [], internal = [], logged = [], attributed = [], teammate = [];
  const known = new Set(verdicts.attributed), audited = new Set(verdicts.console);
  // A 0-byte realtime read from a Confirmed Known Team Networks row: a teammate, never logged, counted. The
  // same rule REST bursts from a Confirmed network get (Jerome, 2026-11-05: a teammate's root read was logged).
  const mates = new Set(verdicts.teammate || []);
  const alerted = new Set([...(verdicts.alert || []), ...(verdicts.fail || [])]);
  // A read logged only because the network check could not classify it is tagged with why.
  const failed = new Set(verdicts.fail || []), alertOnly = new Set(verdicts.alert || []);
  const why = verdicts.fail_reason || 'network check failed';
  const unconfirmed = allowed.filter((a) => isNodeRead(a) && alerted.has(a.ip) && !known.has(a.ip))
    .map((a) => (failed.has(a.ip) && !alertOnly.has(a.ip) ? { ...a, unclassified: why } : a));
  logged.push(...unconfirmed);
  const nodeReads = allowed.filter(isNodeRead).length - unconfirmed.length;
  for (const s of bySweeps(allowed.filter((a) => !isNodeRead(a)))) {
    const listen = s.attempts.some((a) => a.op === 'listener-listen' && a.node === '/' && desktopBrowser(a));
    const viewerOnly = s.attempts.every((a) => desktopBrowser(a) && !a.bytes
      && (a.op === 'listener-listen' || a.op === 'rest-read'));
    const isConsole = (listen && (audited.has(s.ip)
      || s.attempts.some((a) => a.op === 'rest-read' && a.bytes === 0 && desktopBrowser(a))))
      || (audited.has(s.ip) && viewerOnly && s.attempts.some((a) => a.op === 'listener-listen'));
    const entry = {
      ip: s.ip, t0: isoMin(s.t0), t1: isoMin(s.t1), n: s.attempts.length,
      paths: [...new Set(s.attempts.map((a) => a.node))], ops: [...new Set(s.attempts.map((a) => a.op))],
      clients: [...new Set(s.attempts.map((a) => a.client))],
    };
    if (known.has(s.ip)) { attributed.push(entry); continue; }
    if (isConsole) { console.push(entry); continue; }
    if (mates.has(s.ip) && s.attempts.every((a) => !a.bytes)) { teammate.push(entry); continue; }
    // root-attempts only labels an ALLOWED AuraClaude request internal; tagged and reported, never logged.
    entry.internal = s.attempts.every((a) => / internal \(/.test(a.client || ''));
    if (entry.internal) internal.push(entry); else logged.push(...s.attempts);
  }
  return { console, internal, logged, attributed, teammate, nodeReads };
}

// --- Plan -------------------------------------------------------------------------------------------
// Older ipwatch files spell the verdict in lower case.
const verdictOf = (l) => String((l && l.verdict) || '').toLowerCase();
const NO_VERDICTS = {
  attributed: [], console: [], teammate: [], alert: [], fail: [], rest_teammate: [], confirmed: [], fail_reason: '',
};
function select(lines, verdicts = NO_VERDICTS) {
  const denied = lines.filter((l) => verdictOf(l) === 'denied' && !/^native\//.test(l.client || ''));
  const native = lines.filter((l) => verdictOf(l) === 'denied' && /^native\//.test(l.client || '')).length;
  return { denied, native, allowed: splitAllowed(lines.filter((l) => verdictOf(l) === 'allowed'), verdicts) };
}
// The addresses a plan would write: what lookup-networks.js may send to whois.
function loggedIps(lines, verdicts = NO_VERDICTS, bursts = []) {
  const { denied, allowed } = select(lines, verdicts);
  return new Set([...denied, ...allowed.logged, ...splitBursts(bursts, verdicts).logged]
    .map((a) => a.ip).filter((ip) => ip && ip !== 'none-recorded'));
}

// --- REST bursts --------------------------------------------------------------------------------------
const burstUa = shownUa;
const isInternal = (b) => /^AuraClaude\//.test(b.ua || '');
function splitBursts(bursts, verdicts) {
  const mates = new Set(verdicts.rest_teammate || []);
  const logged = [], teammate = [], internal = [];
  for (const b of bursts) {
    if (!b || !b.ip || ms(b.t0) === null) continue;
    if (isInternal(b)) internal.push(b); else if (mates.has(b.ip)) teammate.push(b); else logged.push(b);
  }
  return { logged, teammate, internal };
}
// 0 bytes sent (and the profiler recorded sizes): no data. Only public-by-rule paths: public data. Any
// per-user or non-public path, or the root: data accessed.
function burstOutcome(b) {
  if (b.sized > 0 && !b.bytes) return 'no-data';
  return (b.nonpublic || []).length || b.root ? 'data-accessed' : 'public-data';
}
// An address already on an Incident Log row for a probe: a denied scanner sweep, a High row, or a row
// written before grouping (those were all probes).
function knownSource(ip, rows) {
  return rows.some((r) => r.ips.includes(ip) && !r.group.includes('|rest-burst|')
    && (!r.group || r.group.endsWith('|denied') || r.severity === 'High'));
}
function burstSeverity(b, perIp10, rows) {
  const nonpublic = b.nonpublic || [];
  if (nonpublic.includes('users') || (nonpublic.length && b.bytes >= LARGE_BYTES) || knownSource(b.ip, rows)) return 'High';
  if (nonpublic.length || b.root || b.shallow || b.listing || perIp10 > BURST_IP_READS) return 'Medium';
  return 'Low';
}
// The path family a burst belongs to: the top-level node it read most ("/" for the root).
function burstFamily(b) {
  const tops = new Map();
  for (const [shape, n] of Object.entries(b.shapes || {})) {
    const top = `/${shape.split('/')[1] || ''}`;
    tops.set(top, (tops.get(top) || 0) + n);
  }
  return [...tops].sort((x, y) => y[1] - x[1] || (x[0] < y[0] ? -1 : 1))[0]?.[0] || '/';
}
function burstLine(b, t0, t1, withDate = false) {
  const span = floorMin(t1) > floorMin(t0) ? `${hhmm(t0)}-${hhmm(t1)}` : hhmm(t0);
  const shapes = Object.entries(b.shapes || {}).sort((x, y) => y[1] - x[1])
    .map(([p, c]) => (c > 1 ? `${p} x${c}` : p)).join(', ');
  const extra = [`${b.bytes || 0} bytes`, b.shallow && `${b.shallow} shallow`, b.listing && `${b.listing} queries`,
    b.root && `${b.root} root`].filter(Boolean).join(', ');
  return `${withDate ? `${day(t0)} ` : ''}${span}Z ${b.ip}: ${b.n} allowed REST ${b.n === 1 ? 'read' : 'reads'} of ${shapes} (${extra}) [${burstUa(b)}]`;
}
function burstEntry(b) {
  return { ip: b.ip, t0: isoMin(ms(b.t0)), t1: isoMin(ms(b.t1)), n: b.n, paths: Object.keys(b.shapes || {}),
    ops: ['rest-read'], clients: [b.client], rest_burst: true };
}
// Before grouping, a row's line was one IP's sweep across every attack type. Its key is the start of that
// mixed sweep, which can be earlier than the start of a per-type sweep, so map each attempt to it.
function legacyKeys(denied) {
  const keys = new Map();
  for (const s of bySweeps(denied)) for (const a of s.attempts) keys.set(attemptId(a), sweepKey(s.t0, s.ip));
  return keys;
}
const attemptId = (a) => `${a.t}|${a.ip}|${a.node}|${a.op}`;

// monitor-firebase reclassify.py: lines logged "(unclassified: …)" (or untagged, from before the tag) whose
// address it has since matched to Known Team Networks. Each listed line's tag becomes
// "(classified <date>: <verdict>)". A line no longer in its row is skipped.
const VERDICT_TEXT = {
  teammate: (v) => `teammate network ${v.network}, Known Team Networks ${v.who}`,
  'not-a-teammate': (v) => `${v.network} is marked Not a teammate`,
  unknown: (v) => `${v.network}, not a known teammate network`,
};
function reclassifyRows(all, re, today) {
  const done = [];
  for (const x of (re && re.lines) || []) {
    const v = re.ips && re.ips[x.ip];
    const row = all.find((r) => r.page_id === x.page_id);
    if (!v || !row || !VERDICT_TEXT[v.verdict]) continue;
    const lines = row.what.split('\n');
    const i = lines.indexOf(x.line);
    if (i < 0) continue;
    const base = x.line.replace(/ \(unclassified: [^)]*\)$/, '');
    lines[i] = `${base} (classified ${today}: ${VERDICT_TEXT[v.verdict](v).replace(/[()]/g, '')})`;
    row.what = lines.join('\n');
    row.changed.add('What they did');
    // A Known Team Networks teammate makes Actor "Staff", named or not, never over a value already set;
    // a name is a likely match only, so it goes in Notes marked not confirmed (Jerome, 2026-11-10).
    if (v.verdict === 'teammate' && (!row.actor || row.actor === 'Unknown')) {
      row.actor = STAFF_ACTOR;
      row.changed.add('Actor');
      if (row.staffValue !== 'Staff') row.changed.add('Staff');
      const who = v.name ? `likely teammate ${v.name}` : 'likely teammate (no name on the Known Team Networks row)';
      row.notes = [row.notes, `${today}: ${who}, not confirmed.`].filter(Boolean).join('\n');
      row.changed.add('Notes');
    }
    done.push({ page_id: x.page_id, ip: x.ip, verdict: v.verdict });
  }
  return done;
}

function plan(lines, rawRows, networks, verdicts = NO_VERDICTS, bursts = [], reclass = null, today = new Date().toISOString().slice(0, 10)) {
  const all = rawRows.map(normalizeRow).filter((r) => r.system === SYSTEM);
  // Before this run's sweeps, so a sweep that extends one of these lines extends the re-classified text.
  const reclassified = reclassifyRows(all, reclass, today);
  const { denied, native, allowed } = select(lines, verdicts);
  const legacy = legacyKeys(denied);
  // Only a table holding the merged group rows has that history: group rows detected on or before the
  // regroup that this skill did not create.
  const regrouped = all.some((r) => r.group && !r.managed && r.detected !== null && r.detected <= REGROUPED_UNTIL);

  // Attempts per group key; then each group's attempts split into per-IP sweeps.
  const byGroup = new Map();
  const put = (a, outcome) => {
    const staffRead = a.ip === KNOWN_NETWORK;
    const g = `${SYSTEM}|${typeOf(a)}|${staffRead ? `${STAFF}|` : ''}${outcome}`;
    if (!byGroup.has(g)) byGroup.set(g, { type: typeOf(a), outcome, staff: staffRead, attempts: [] });
    byGroup.get(g).attempts.push(a);
  };
  // A staff address is written as KNOWN_NETWORK; the real one stays on the attempt for its network.
  const staff = new Set(verdicts.confirmed || []);
  const shown = (a) => (staff.has(a.ip) ? { ...a, ip: KNOWN_NETWORK, realIp: a.ip } : a);
  for (const a of denied) put(shown(a), 'denied');
  for (const a of allowed.logged) put(shown(a), allowedOutcome(a));

  const created = [];
  const already = [];
  const extended = [];
  const alerts = new Map();
  const alertFor = (s, severity, row) => { if (rank(severity) >= rank('Medium')) addAlert(alerts, s, severity, row); };
  // A row a person set to Resolved is never written again (Jerome, 2026-11-09): its group's next sweep
  // opens a new row, and the Resolved row is listed in `reopened`.
  const reopened = new Map();
  let sweepCount = 0;
  const sweeps = [];
  for (const [g, grp] of byGroup) {
    for (const s of bySweeps(grp.attempts)) sweeps.push(Object.assign(s, { group: g, type: grp.type, outcome: grp.outcome, staff: grp.staff }));
  }
  sweeps.sort((a, b) => a.t0 - b.t0 || (a.ip < b.ip ? -1 : 1) || (a.group < b.group ? -1 : 1));
  for (const s of sweeps) {
    sweepCount += 1;
    let key = sweepKey(s.t0, s.ip);
    let severity = sweepSeverity(s);
    // A KNOWN_NETWORK sweep can hold several staff addresses: each one's network is named.
    const nets = [...new Set(s.attempts.map((a) => a.realIp || a.ip))].map((ip) => network(ip, networks));
    const net = nets[0];
    // The group's open row: the newest row with this key (and, for an allowed read, this address or
    // network), unless it has had no hit for GROUP_CLOSE_MS.
    const sameNetwork = (r) => !BY_NETWORK(s.outcome) || r.ips.includes(s.ip) || inNetworkText(r.networks, net);
    // A Resolved row holds the sweep's attempts up to its stored Ended; only later ones are logged, on a new
    // row. Every pass splits the sweep at the same minute, so its new row's line keeps one key.
    const resolvedEnd = Math.max(-Infinity, ...all.filter((r) => r.group === s.group && r.status === 'Resolved'
      && sameNetwork(r) && r.storedEnded !== null && floorMin(s.t0) <= r.storedEnded).map((r) => r.storedEnded));
    if (resolvedEnd > -Infinity) {
      const fresh = s.attempts.filter((a) => floorMin(a.ts) > resolvedEnd);
      if (!fresh.length) { already.push(key); continue; }
      Object.assign(s, { attempts: fresh, t0: fresh[0].ts });
      key = sweepKey(s.t0, s.ip);
      severity = sweepSeverity(s);
    }
    let row = [...all, ...created].filter((r) => r.group === s.group && sameNetwork(r))
      .sort((a, b) => (b.ended ?? 0) - (a.ended ?? 0))[0];
    if (row && row.ended !== null && s.t0 - row.ended > GROUP_CLOSE_MS) row = null;
    // A sweep logged before grouping: a per-IP row without a Group key holds a line for its mixed sweep.
    const old = all.some((r) => !r.group && s.attempts.some((a) => r.applied.has(key) || r.applied.has(legacy.get(attemptId(a)))));
    if (old) { already.push(key); continue; }
    if (regrouped && floorMin(s.t0) <= REGROUPED_UNTIL && !(row && row.applied.has(key))) { already.push(key); continue; }
    if (row && row.status === 'Resolved') { reopened.set(row.page_id, s.group); row = null; }
    if (row && row.applied.has(key)) {
      if (extendSweep(row, s, key)) {
        extended.push(key);
        alertFor(s, severity, row);
      } else already.push(key);
      continue;
    }
    const cover = row && coveringLine(row, s);
    if (cover) {
      if (extendCovered(row, s, cover)) {
        extended.push(key);
        (row.sweeps = row.sweeps || []).push(key);
        alertFor(s, severity, row);
      } else already.push(key);
      continue;
    }
    // A row this skill wrote covers everything up to its stored Ended: profiler runs don't overlap, and its
    // oldest lines may have been trimmed to fit, so the line keys alone can't prove a sweep is new.
    if (row && row.managed && row.storedEnded !== null && floorMin(s.t0) <= row.storedEnded) { already.push(key); continue; }
    const tr = transport(s.attempts);
    if (!row) {
      row = {
        key: s.group, isNew: true, group: s.group, type: s.type, outcome: s.outcome, staff: s.staff, actor: s.staff ? STAFF_ACTOR : '', severity: '',
        detected: floorMin(s.t0), ended: floorMin(s.t1), ips: [], maxIps: 0,
        uas: { counted: new Map(), legacy: [] }, route: '', networks: '', what: '', requests: 0,
        changed: new Set(), applied: new Set(), newId: `new${created.length}`,
      };
      created.push(row);
    }
    const withDate = row.detected !== null && day(s.t0) !== day(row.detected);
    row.what = [row.what, whatLine(s, withDate)].filter(Boolean).join('\n');
    row.applied.add(key);
    (row.sweeps = row.sweeps || []).push(key);
    row.changed.add('What they did');
    if (row.detected === null || (s.t0 < row.detected && day(s.t0) === day(row.detected))) {
      row.detected = floorMin(s.t0); row.changed.add('Detected (UTC)');
    }
    if (row.ended === null || floorMin(s.t1) > row.ended) { row.ended = floorMin(s.t1); row.changed.add('Ended (UTC)'); }
    addAttempts(row, s.attempts, tr);
    if (!row.ips.includes(s.ip)) {
      row.ips.push(s.ip); row.changed.add('IP addresses');
      row.maxIps = Math.max(row.maxIps || 0, row.ips.length);
    }
    for (const n of nets.filter((x) => !inNetworkText(row.networks, x) && !row.networks.includes(x.label))) {
      row.networks = [row.networks, n.label].filter(Boolean).join('; ');
      row.changed.add('Source network');
    }
    raise(row, severity);
    alertFor(s, severity, row);
  }

  // REST bursts: one row per path family + outcome, open while it has a hit within GROUP_CLOSE_MS
  // (Security and Jerome, 2026-11-03, ENG-1759). Every IP and user agent of the group joins that row.
  const rb = splitBursts(bursts, verdicts);
  const runIps = new Map();  // group -> this run's distinct IPs, for Max IPs (the peak in one window)
  const perIp = new Map();
  for (const b of rb.logged) perIp.set(b.ip, (perIp.get(b.ip) || 0) + b.n / (Math.max(Number(b.minutes) || 10, 1) / 10));
  let burstReads = 0;
  for (const b of rb.logged.sort((x, y) => ms(x.t0) - ms(y.t0) || (x.ip < y.ip ? -1 : 1))) {
    const t0 = ms(b.t0), t1 = ms(b.t1) ?? t0;
    const outcome = burstOutcome(b);
    const family = burstFamily(b);
    const group = `${SYSTEM}|rest-burst|${family}|${outcome}`;
    const ua = burstUa(b);
    const key = sweepKey(t0, b.ip);
    const s = { group, ip: b.ip, t0, t1, attempts: { length: b.n } };
    const severity = burstSeverity(b, perIp.get(b.ip), all);
    const rows = [...all, ...created].filter((r) => r.group === group);
    let row = [...rows].sort((x, y) => (y.ended ?? 0) - (x.ended ?? 0))[0];
    if (row && row.ended !== null && t0 - row.ended > GROUP_CLOSE_MS) row = null;
    // Already applied only when this exact source's line is on a row of the group (an older one too: a
    // Resolved row's group goes on in a newer row). A row holds many IPs and user agents, so an IP+minute key
    // or the row's stored Ended would drop a second user agent or another IP's burst.
    // Compared with its date: a stored line without one is on the row's Detected day.
    const core = burstLine(b, t0, t1);
    const sameLine = (r) => (l) => {
      const m = l.match(/^(\d{4}-\d{2}-\d{2}) /);
      const base = r.detected !== null ? day(r.detected) : '';
      return (m ? m[1] : base) === day(t0) && l.replace(/^\d{4}-\d{2}-\d{2} /, '').replace(TAG_RE, '') === core;
    };
    if (rows.some((r) => r.what.split('\n').some(sameLine(r)))) {
      already.push(key);
      continue;
    }
    if (row && row.status === 'Resolved') { reopened.set(row.page_id, group); row = null; }
    sweepCount += 1;
    burstReads += b.n;
    if (!row) {
      row = {
        key: group, isNew: true, group, type: 'rest-burst', family, outcome, severity: '',
        detected: floorMin(t0), ended: floorMin(t1), ips: [], maxIps: 0,
        uas: { counted: new Map(), legacy: [] }, route: '', networks: '', what: '', requests: 0,
        changed: new Set(), applied: new Set(), newId: `new${created.length}`,
      };
      created.push(row);
    }
    // The network check failed: no source could be checked against Confirmed teammate networks.
    const tag = verdicts.fail_reason ? ` (unclassified: ${verdicts.fail_reason})` : '';
    const withDate = row.detected !== null && day(t0) !== day(row.detected);
    row.what = [row.what, burstLine(b, t0, t1, withDate) + tag].filter(Boolean).join('\n');
    if (!row.ips.includes(b.ip)) { row.ips.push(b.ip); row.changed.add('IP addresses'); }
    const seen = runIps.get(group) || new Set();
    seen.add(b.ip); runIps.set(group, seen);
    if (seen.size > (row.maxIps || 0)) { row.maxIps = seen.size; row.changed.add('Max IPs'); }
    row.applied.add(key);
    (row.sweeps = row.sweeps || []).push(key);
    row.changed.add('What they did');
    // Detected moves earlier only within its own UTC day, as for every other group: undated lines are read
    // as that day, so moving it to another day would re-date them.
    if (floorMin(t0) < row.detected && day(t0) === day(row.detected)) {
      row.detected = floorMin(t0); row.changed.add('Detected (UTC)');
    }
    if (row.ended === null || floorMin(t1) > row.ended) { row.ended = floorMin(t1); row.changed.add('Ended (UTC)'); }
    row.requests += b.n; row.changed.add('Requests');
    row.uas.counted.set(ua, (row.uas.counted.get(ua) || 0) + b.n); row.changed.add('User agent');
    const route = mergeRoute(row.route, Object.keys(b.shapes || {}), 'REST');
    if (route !== row.route) { row.route = route; row.changed.add('Route / path'); }
    const net = network(b.ip, networks);
    if (!inNetworkText(row.networks, net) && !row.networks.includes(net.label)) {
      row.networks = [row.networks, net.label].filter(Boolean).join('; ');
      row.changed.add('Source network');
    }
    raise(row, severity);
    alertFor(s, severity, row);
  }

  const props = (r) => ({
    'Detected (UTC)': dateProp(r.detected),
    'Ended (UTC)': dateProp(r.ended),
    'What they did': text(fitLines(r.what)),
    Requests: { number: r.requests },
    'IP addresses': text(r.ips.join(', ')),
    'Max IPs': { number: r.maxIps },
    'User agent': text(uaText(r.uas)),
    'Route / path': text(r.route),
    'Source network': text(r.networks),
    Severity: { select: { name: r.severity } },
    Actor: text(r.actor || 'Unknown'),
    Staff: { select: { name: staffOf(r) } },
  });
  const creates = created.map((r) => ({
    key: r.key,
    sweeps: r.sweeps,
    properties: {
      Incident: { title: [{ text: { content: `${TITLE[r.type]}${r.family ? ` ${r.family}` : ''}${r.staff ? ', Known Team Networks' : ''} (${r.outcome})` } }] },
      'Group key': text(r.group),
      ...(r.staff ? {} : { Category: { select: { name: CATEGORY[r.type] } } }),
      System: { select: { name: SYSTEM } },
      Outcome: { select: { name: OUTCOME[r.outcome] } },
      Status: { select: { name: ['denied', 'no-data', 'public-data'].includes(r.outcome) ? 'Monitoring' : 'Open' } },
      Actor: text('Unknown'),
      'Found by': text(FOUND_BY),
      Notes: text(`${r.staff ? staffNotes(r.networks) : (r.type === 'rest-burst' ? BURST_NOTES : NOTES)[r.outcome]} ${PROBE_LOG_NOTE}`),
      ...props(r),
    },
  }));
  for (const r of all) if (r.changed.size && r.managed && !r.staffValue) r.changed.add('Staff');
  const updates = all.filter((r) => r.changed.size).map((r) => {
    const p = props(r);
    const out = {};
    for (const k of r.changed) out[k] = k === 'Notes' ? text(r.notes) : p[k];
    if (r.changed.has('IP addresses')) out['Max IPs'] = p['Max IPs'];
    return { sweeps: r.sweeps, page_id: r.page_id, properties: out };
  });
  const alertList = (m) => [...m.values()].map((a) => ({
    group_key: a.group_key, severity: a.severity, attempts: a.attempts, ip_count: a.ips.size, ips: [...a.ips].sort(),
    t0: isoMin(a.t0), t1: isoMin(a.t1), row: a.row,
  }));
  return {
    creates,
    updates,
    alerts: alertList(alerts),
    reopened: [...reopened].map(([page_id, group_key]) => ({ page_id, group_key })),
    console: allowed.console,
    attributed: [...allowed.attributed, ...allowed.teammate, ...rb.teammate.map(burstEntry)],
    allowed_flags: [...allowed.internal, ...rb.internal.map((b) => ({ ...burstEntry(b), internal: true }))],
    reclassified,
    summary: {
      attempts: denied.length + allowed.logged.length,
      known_network_reads: [...denied, ...allowed.logged].filter((a) => staff.has(a.ip)).length,
      sweeps: sweepCount,
      groups: byGroup.size,
      creates: creates.length,
      updates: updates.length,
      already_applied: already.length,
      extended: extended.length,
      native_excluded: native,
      console_reads: allowed.console.reduce((n, c) => n + c.n, 0),
      attributed_reads: allowed.attributed.reduce((n, c) => n + c.n, 0),
      teammate_reads: allowed.teammate.reduce((n, c) => n + c.n, 0),
      node_reads_not_logged: allowed.nodeReads,
      allowed_logged: allowed.logged.length,
      internal_flags: allowed.internal.length + rb.internal.length,
      unclassified_reads: allowed.logged.filter((a) => a.unclassified).length
        + (verdicts.fail_reason ? rb.logged.reduce((n, b) => n + b.n, 0) : 0),
      unclassified_reason: allowed.logged.some((a) => a.unclassified) || (verdicts.fail_reason && rb.logged.length)
        ? (verdicts.fail_reason || 'network check failed') : '',
      rest_burst_sources: rb.logged.length,
      reclassified: reclassified.length,
      rest_burst_reads: burstReads,
      rest_burst_teammate: rb.teammate.length,
      alerts: alerts.size,
      resolved_reopened: reopened.size,
    },
  };
}
// Medium and High only, one entry per row. `ips` holds the readers' addresses for the row writer only:
// a printed alert line or a Slack post uses the counts, never an address.
function addAlert(alerts, s, severity, row) {
  const id = `${s.group}|${row.page_id || row.newId}`;
  const a = alerts.get(id) || { group_key: s.group, severity, attempts: 0, ips: new Set(), t0: s.t0, t1: s.t1, row: row.page_id || 'new' };
  if (rank(severity) > rank(a.severity)) a.severity = severity;
  a.attempts += s.attempts.length;
  a.ips.add(s.ip);
  a.t0 = Math.min(a.t0, s.t0); a.t1 = Math.max(a.t1, s.t1);
  alerts.set(id, a);
}

function readLines(path) {
  if (!fs.existsSync(path)) throw new Error(`probe detail not found: ${path} (did the profile run reach the summary?)`);
  return fs.readFileSync(path, 'utf8').split('\n').filter((l) => l.trim()).map((l, i) => {
    try { return JSON.parse(l); } catch { throw new Error(`detail line ${i + 1} is not JSON`); }
  });
}
function readRows(path) {
  const j = JSON.parse(fs.readFileSync(path, 'utf8'));
  if (j && j.has_more) throw new Error('rows.json has has_more=true: read every row before planning');
  const rows = Array.isArray(j) ? j : j.results;
  if (!Array.isArray(rows)) throw new Error('rows.json: expected an array or {results: [...]}');
  return rows;
}

// A missing or broken file means no verdicts: every root and guessed-name read is logged.
function readVerdicts(path) {
  if (!path) return NO_VERDICTS;
  try {
    const j = JSON.parse(fs.readFileSync(path, 'utf8'));
    const list = (k) => (Array.isArray(j && j[k]) ? j[k].map(String) : []);
    return { attributed: list('attributed'), console: list('console'), teammate: list('teammate'),
      alert: list('alert'), fail: list('fail'), rest_teammate: list('rest_teammate'), confirmed: list('confirmed'),
      fail_reason: typeof (j && j.fail_reason) === 'string' ? j.fail_reason : '' };
  } catch (e) {
    console.error(`plan-incident-log: verdicts not read (${e.message}); every allowed read is judged without them`);
    return NO_VERDICTS;
  }
}
// No file means no burst. A broken file stops the plan: a burst's IPs must not be lost silently.
function readBursts(path) {
  if (!path || !fs.existsSync(path)) return [];
  return fs.readFileSync(path, 'utf8').split('\n').filter((l) => l.trim()).map((l, i) => {
    try { return JSON.parse(l); } catch { throw new Error(`rest-bursts line ${i + 1} is not JSON`); }
  });
}
// <detail> <rows> [networks] [--verdicts file] [--rest-bursts file] [--reclassify file]
function parseArgs(argv) {
  const pos = [];
  let verdicts = null, bursts = null, reclassify = null;
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === '--verdicts') { verdicts = argv[i + 1]; i += 1; } else if (argv[i] === '--rest-bursts') {
      bursts = argv[i + 1]; i += 1;
    } else if (argv[i] === '--reclassify') { reclassify = argv[i + 1]; i += 1; } else pos.push(argv[i]);
  }
  return { pos, verdicts, bursts, reclassify };
}

function main() {
  const { pos: args, verdicts, bursts, reclassify } = parseArgs(process.argv.slice(2));
  if (args.length < 2 || args.length > 3 || verdicts === undefined || bursts === undefined || reclassify === undefined) {
    console.error('usage: node plan-incident-log.js <detail.jsonl> <rows.json> [networks.json] [--verdicts verdicts.json] [--rest-bursts file] [--reclassify file]');
    process.exit(2);
  }
  try {
    const networks = args[2] ? JSON.parse(fs.readFileSync(args[2], 'utf8')) : {};
    // A pass with a REST burst and no probe writes no probe detail.
    const lines = bursts && !fs.existsSync(args[0]) ? [] : readLines(args[0]);
    // No reclassify file (an older pass, or Known Team Networks unreadable): nothing is re-classified.
    const reclass = reclassify && fs.existsSync(reclassify) ? JSON.parse(fs.readFileSync(reclassify, 'utf8')) : null;
    console.log(JSON.stringify(plan(lines, readRows(args[1]), networks, readVerdicts(verdicts),
      readBursts(bursts), reclass), null, 2));
  } catch (e) {
    console.error(`plan-incident-log: ${e.message}`);
    process.exit(2);
  }
}

if (require.main === module) main();
module.exports = { plan, bySweeps, network, prefix16, loggedIps, readVerdicts, readBursts, parseArgs, FLOOD_ATTEMPTS, GROUP_CLOSE_MS };
