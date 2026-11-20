#!/usr/bin/env node
// Plan the Notion RTDB Probe Log writes for one profiler run. Pure: reads two files, prints a JSON plan,
// writes nothing.
//
// Usage: node plan-probe-log.js <probes.jsonl> <rows.json>
//        node plan-probe-log.js --self-test
//
// probes.jsonl  lines from profile-firebase-rtdb's root-attempts.js (--probe-json):
//               {path, verdict, kind, op, client, tag, n, ips, t0, t1}
// rows.json     the Probe Log rows from a notion-query-data-sources SQL query: the whole response
//               ({results: [...]}) or the results array. Keys are the SQL column names
//               (url, Path, Verdict, Kind, Op, Client, "date:First seen:start", "date:Last seen:start",
//               "Attempts seen", "Days seen", "Max IPs in a window").
//
// Output: {creates: [{key, properties}], updates: [{key, url, page_id, properties}], new_paths, summary}.
// `properties` use the Notion MCP property format, ready for notion-create-pages / notion-update-page.
// Matching rules: references/probe-log-schema.md.
'use strict';
const fs = require('fs');

const UNCLASSIFIED = /^Unclassified/;
// The Security Incident Log group a kind's attempts also land in (by type), named in a new row's Notes so
// a reader can go from a path to the addresses behind it. Routine and denied-below-a-node rows have none.
const INCIDENT_TYPE = {
  'Root read': 'root-read', 'Guessed name (scanner)': 'guessed-name', 'Whole real node via REST': 'node-read',
  'Whole real node via SDK listen': 'node-read', 'Denied read of a real node': 'node-read',
  'REST read below a node': 'rest-burst',
};
function incidentNote(kind) {
  const t = INCIDENT_TYPE[kind];
  return t ? `Security Incident Log: Group key "Firebase RTDB|${t}|…" (addresses and user agents are there, not here).` : undefined;
}

// Notion returns "2026-10-12 17:19:00Z"; the profiler writes full ISO. Both become epoch ms.
function toMs(v) {
  if (v === null || v === undefined || v === '') return null;
  const ms = Date.parse(String(v).trim().replace(' ', 'T'));
  return Number.isNaN(ms) ? null : ms;
}
// The log keeps minute precision, like the rows entered by hand.
function floorMinute(ms) { return ms - (ms % 60000); }
function isoMinute(ms) { return new Date(floorMinute(ms)).toISOString(); }
function utcDay(ms) { return new Date(ms).toISOString().slice(0, 10); }
function num(v) { const n = Number(v); return Number.isFinite(n) ? n : 0; }
// The apps' routine reads get a row of their own, so an outside read of the same node keeps its Kind,
// its count and its Incident Log note instead of being absorbed into the routine row.
const ROUTINE = new Set(['Routine app read']);
function keyOf(path, op, verdict, kind) { return `${path}|${op}|${verdict}${ROUTINE.has(kind) ? '|routine' : ''}`; }
function pageId(url) {
  const m = String(url || '').match(/([0-9a-f]{32})(?:[?#].*)?$/i);
  return m ? m[1] : null;
}
function hasClient(list, client) {
  const c = client.toLowerCase();
  return list.toLowerCase().split(';').some((x) => x.trim() === c);
}

// Several client groups of one path+op+verdict become one probe.
function mergeProbes(lines) {
  const groups = new Map();
  let skipped = 0, invalid = 0;
  for (const p of lines) {
    if (!p || !p.path || !p.op || !p.verdict) { invalid += 1; continue; }
    if (toMs(p.t0) === null || toMs(p.t1) === null) { invalid += 1; continue; }
    if (UNCLASSIFIED.test(p.kind || '')) { skipped += p.n || 0; continue; }
    const k = keyOf(p.path, p.op, p.verdict, p.kind);
    const g = groups.get(k) || { path: p.path, op: p.op, verdict: p.verdict, kind: p.kind, clients: [], n: 0, ips: 0, t0: Infinity, t1: -Infinity };
    g.n += num(p.n);
    g.ips += num(p.ips); // upper bound: two client groups can share an address
    g.t0 = Math.min(g.t0, toMs(p.t0));
    g.t1 = Math.max(g.t1, toMs(p.t1));
    if (p.client && !g.clients.includes(p.client)) g.clients.push(p.client);
    groups.set(k, g);
  }
  return { groups: [...groups.values()], skippedUnclassifiedAttempts: skipped, invalidLines: invalid };
}

function normalizeRow(r) {
  return {
    url: r.url,
    path: r.Path,
    op: r.Op,
    verdict: r.Verdict,
    kind: r.Kind || '',
    client: r.Client || '',
    first: toMs(r['date:First seen:start'] ?? r['First seen']),
    last: toMs(r['date:Last seen:start'] ?? r['Last seen']),
    attempts: num(r['Attempts seen']),
    days: num(r['Days seen']),
    maxIps: num(r['Max IPs in a window']),
  };
}

function dateProps(name, ms) {
  return { [`date:${name}:start`]: isoMinute(ms), [`date:${name}:is_datetime`]: 1 };
}

function plan(probeLines, rawRows) {
  const { groups, skippedUnclassifiedAttempts, invalidLines } = mergeProbes(probeLines);
  const index = new Map();
  const duplicates = [];
  for (const r of rawRows.map(normalizeRow)) {
    const k = keyOf(r.path, r.op, r.verdict, r.kind);
    if (index.has(k)) duplicates.push(k); else index.set(k, r);
  }
  const creates = [], updates = [], newPaths = [], alreadyApplied = [];
  for (const g of groups.sort((a, b) => b.n - a.n)) {
    const k = keyOf(g.path, g.op, g.verdict, g.kind);
    const row = index.get(k);
    if (!row) {
      newPaths.push({ key: k, kind: g.kind, n: g.n });
      creates.push({
        key: k,
        properties: {
          Path: g.path, Verdict: g.verdict, Kind: g.kind, Op: g.op, Client: g.clients.join('; '),
          ...dateProps('First seen', g.t0), ...dateProps('Last seen', g.t1),
          'Attempts seen': g.n, 'Days seen': 1, 'Max IPs in a window': g.ips,
          ...(incidentNote(g.kind) ? { Notes: incidentNote(g.kind) } : {}),
        },
      });
      continue;
    }
    // A run whose last attempt is not after the row's Last seen was applied already (or is older).
    if (row.last !== null && floorMinute(g.t1) <= row.last) { alreadyApplied.push(k); continue; }
    const props = { 'Attempts seen': row.attempts + g.n };
    Object.assign(props, dateProps('Last seen', g.t1));
    if (row.first === null) Object.assign(props, dateProps('First seen', g.t0));
    if (row.last === null) props['Days seen'] = Math.max(1, row.days);
    else if (utcDay(g.t1) > utcDay(row.last)) props['Days seen'] = row.days + 1;
    if (g.ips > row.maxIps) props['Max IPs in a window'] = g.ips;
    const added = g.clients.filter((c) => !hasClient(row.client, c));
    if (added.length) props.Client = [row.client, ...added].filter(Boolean).join('; ');
    if (!row.kind && g.kind) props.Kind = g.kind;
    updates.push({ key: k, url: row.url, page_id: pageId(row.url), properties: props });
  }
  return {
    creates,
    updates,
    new_paths: newPaths,
    summary: {
      probe_groups: groups.length,
      attempts: groups.reduce((s, g) => s + g.n, 0),
      creates: creates.length,
      updates: updates.length,
      already_applied: alreadyApplied.length,
      skipped_unclassified_attempts: skippedUnclassifiedAttempts,
      invalid_probe_lines: invalidLines,
      rule_unknown_lines: probeLines.filter((p) => p && p.tag === 'rule unknown').length,
      duplicate_rows: duplicates,
    },
  };
}

function readProbes(path) {
  if (!fs.existsSync(path)) throw new Error(`probe file not found: ${path} (did the profile run reach the summary?)`);
  return fs.readFileSync(path, 'utf8').split('\n').filter((l) => l.trim()).map((l, i) => {
    try { return JSON.parse(l); } catch { throw new Error(`probe line ${i + 1} is not JSON`); }
  });
}
function readRows(path) {
  const j = JSON.parse(fs.readFileSync(path, 'utf8'));
  if (j && j.has_more) throw new Error('rows.json has has_more=true: query every row before planning');
  const rows = Array.isArray(j) ? j : j.results;
  if (!Array.isArray(rows)) throw new Error('rows.json: expected an array or {results: [...]}');
  return rows;
}

function selfTest() {
  const assert = require('assert');
  const rows = [
    { url: 'https://app.notion.com/3f775e23af208156b431d7952615afc8', Path: '/clients', Verdict: 'Denied', Kind: 'Guessed name (scanner)', Op: 'rest-read', Client: 'desktop/windows browser', 'date:First seen:start': '2026-10-08 16:05:00Z', 'date:Last seen:start': '2026-10-12 14:20:00Z', 'Attempts seen': 5, 'Days seen': 3, 'Max IPs in a window': 1 },
    { url: 'https://app.notion.com/3f775e23af20812ea343f78eadb62eef', Path: '/coaches', Verdict: 'Allowed', Kind: 'Whole real node via REST', Op: 'rest-read', Client: 'unknown browser (Mozilla/5.0)', 'date:First seen:start': '2026-10-08 15:53:00Z', 'date:Last seen:start': '2026-10-12 18:25:00Z', 'Attempts seen': 52, 'Days seen': 2, 'Max IPs in a window': 6 },
  ];
  const probes = [
    // Same path+op+verdict from two clients: one row, client appended once.
    { path: '/clients', verdict: 'Denied', kind: 'Guessed name (scanner)', op: 'rest-read', client: 'desktop/windows browser', tag: 'NO RULE (guessed name)', n: 2, ips: 1, t0: '2026-10-13T09:00:10.000Z', t1: '2026-10-13T09:05:30.000Z' },
    { path: '/clients', verdict: 'Denied', kind: 'Guessed name (scanner)', op: 'rest-read', client: 'mobile/android browser', tag: 'NO RULE (guessed name)', n: 1, ips: 2, t0: '2026-10-13T08:59:00.000Z', t1: '2026-10-13T09:01:00.000Z' },
    // Same day as Last seen: no Days seen bump; fewer IPs: max kept.
    { path: '/coaches', verdict: 'Allowed', kind: 'Whole real node via REST', op: 'rest-read', client: 'unknown/unknown browser', tag: 'rule exists', n: 4, ips: 2, t0: '2026-10-12T19:00:00.000Z', t1: '2026-10-12T19:10:00.000Z' },
    // New path.
    { path: '/', verdict: 'Allowed', kind: 'Root read', op: 'rest-read', client: 'desktop/linux curl/8.0', tag: 'ROOT', n: 1, ips: 1, t0: '2026-10-13T10:00:00.000Z', t1: '2026-10-13T10:00:00.000Z' },
    // Unclassified (no rules file): counted, not planned.
    { path: '/catalog', verdict: 'Allowed', kind: 'Unclassified (rule unknown)', op: 'listener-listen', client: 'ios/ios Aura', tag: 'rule unknown', n: 7, ips: 3, t0: '2026-10-13T10:00:00.000Z', t1: '2026-10-13T10:00:00.000Z' },
  ];
  const p = plan(probes, rows);
  assert.strictEqual(p.summary.probe_groups, 3);
  assert.strictEqual(p.summary.skipped_unclassified_attempts, 7);
  assert.strictEqual(p.creates.length, 1);
  const c = p.creates[0].properties;
  assert.deepStrictEqual([c.Path, c.Verdict, c.Kind, c['Days seen'], c['Attempts seen']], ['/', 'Allowed', 'Root read', 1, 1]);
  assert.ok(/^Security Incident Log: Group key "Firebase RTDB\|root-read\|…"/.test(c.Notes), 'a new row names its Incident Log group');
  assert.strictEqual(incidentNote('Routine app read'), undefined);
  assert.strictEqual(c['date:First seen:start'], '2026-10-13T10:00:00.000Z');
  const clients = p.updates.find((u) => u.key === '/clients|rest-read|Denied');
  assert.strictEqual(clients.page_id, '3f775e23af208156b431d7952615afc8');
  assert.deepStrictEqual(clients.properties, {
    'Attempts seen': 8,
    'date:Last seen:start': '2026-10-13T09:05:00.000Z', 'date:Last seen:is_datetime': 1,
    'Days seen': 4,
    'Max IPs in a window': 3,
    Client: 'desktop/windows browser; mobile/android browser',
  });
  const coaches = p.updates.find((u) => u.key === '/coaches|rest-read|Allowed');
  assert.strictEqual(coaches.properties['Attempts seen'], 56);
  assert.strictEqual(coaches.properties['Days seen'], undefined);
  assert.strictEqual(coaches.properties['Max IPs in a window'], undefined);
  assert.strictEqual(coaches.properties.Client, 'unknown browser (Mozilla/5.0); unknown/unknown browser');
  assert.ok(!('date:First seen:start' in coaches.properties));
  assert.deepStrictEqual(p.new_paths.map((x) => x.key), ['/|rest-read|Allowed']);

  // Idempotent: the same probes against the rows after applying are a no-op.
  const applied = rows.map((r) => {
    const u = p.updates.find((x) => x.url === r.url);
    return u ? { ...r, ...u.properties } : r;
  }).concat(p.creates.map((x, i) => ({ url: `https://app.notion.com/${String(i).padStart(32, '0')}`, ...x.properties })));
  const again = plan(probes, applied);
  assert.strictEqual(again.creates.length, 0);
  assert.strictEqual(again.updates.length, 0);
  assert.strictEqual(again.summary.already_applied, 3);

  // A later day bumps Days seen by one; a row with no Last seen gets at least 1.
  const later = plan([{ ...probes[0], t0: '2026-10-14T00:00:00.000Z', t1: '2026-10-14T00:01:00.000Z' }], applied);
  assert.strictEqual(later.updates[0].properties['Days seen'], 5);
  const blank = plan([probes[3]], [{ url: 'https://app.notion.com/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', Path: '/', Op: 'rest-read', Verdict: 'Allowed', 'Attempts seen': null, 'Days seen': null }]);
  assert.deepStrictEqual(blank.updates[0].properties['Days seen'], 1);
  assert.strictEqual(blank.updates[0].properties['date:First seen:start'], '2026-10-13T10:00:00.000Z');

  // Verdict is part of the key: an allowed read of a path logged as denied is a new row.
  const flip = plan([{ ...probes[0], verdict: 'Allowed' }], rows);
  assert.strictEqual(flip.creates.length, 1);

  // Duplicate rows for one key are reported, not merged.
  assert.strictEqual(p.summary.rule_unknown_lines, 1);

  // A line without times is counted as invalid, never planned with a 1970 date.
  const bad = plan([{ ...probes[0], t0: null }], rows);
  assert.strictEqual(bad.summary.invalid_probe_lines, 1);
  assert.strictEqual(bad.updates.length + bad.creates.length, 0);

  const dup = plan([], [rows[0], { ...rows[0], url: 'https://app.notion.com/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb' }]);
  assert.deepStrictEqual(dup.summary.duplicate_rows, ['/clients|rest-read|Denied']);
  console.log('self-test OK');
}

function main() {
  const args = process.argv.slice(2);
  if (args[0] === '--self-test') return selfTest();
  if (args.length !== 2) {
    console.error('usage: node plan-probe-log.js <probes.jsonl> <rows.json> | --self-test');
    process.exit(2);
  }
  try {
    const result = plan(readProbes(args[0]), readRows(args[1]));
    // Without the rules file every real node reads as "rule unknown" and a scanner's guessed name
    // looks like a real node, so the Kind would be wrong. Refuse rather than write wrong rows.
    if (result.summary.rule_unknown_lines) {
      throw new Error(`${result.summary.rule_unknown_lines} probe line(s) tagged "rule unknown": the profile ran without rules.json. Fix the AuraHealth/Firebase checkout (or pass --rules) and re-run.`);
    }
    console.log(JSON.stringify(result, null, 2));
  } catch (e) {
    console.error(`plan-probe-log: ${e.message}`);
    process.exit(2);
  }
}

if (require.main === module) main();
module.exports = { plan, mergeProbes };
