#!/usr/bin/env node
/*
 * Read exact prod RTDB paths through the Admin SDK realtime protocol (getDatabase().ref(path).get()).
 * Never REST (https://<db>.firebaseio.com/<path>.json), never Python firebase_admin.db, which is REST too:
 * the RTDB REST read alert counts REST gets only and cannot tell our reads from a scraper's.
 *
 * A cooperative guard, not enforcement: admin keys bypass rules, so this only keeps our own reads in line.
 *   Refused: "/", any top-level node (fewer than 2 segments), /users itself, any shallow or query
 *            parameter (a '?', or &orderBy= and the like), a key with . # $ [ ].
 *   At most 20 reads in flight (--concurrency, 25 at most).
 *   More than 200 paths is a bulk read: refused unless --bulk is passed (announce it first).
 *   Prints field names and counts, never values, unless --values is passed.
 *
 * Canonical copy: eng:read-rtdb-paths. Other skills ship a byte-identical copy (docs/canonical-sources.md).
 *
 * CLI:     node rtdb_get.js [--values] [--concurrency N] [--bulk] [--self-test] [path ...]
 *          Paths come from the arguments, or one per line on stdin. One JSON line per path on stdout:
 *          {path, exists, type, count?, fields?} or {path, value} or {path, error}.
 * Module:  checkPath(path) -> null | reason; validate(paths, opts); userAgent(); summarize(value);
 *          readPaths(paths, opts) with opts.db an open firebase-admin Database.
 * Env:     RTDB_URL (the database URL; no default), GCP_KEY (service-account JSON or a key-file path),
 *          AURA_CLI_DIR or AINATIVE_FIREBASE_ADMIN_DIR (where firebase-admin's node_modules is).
 *          AINATIVE_USER_AGENT replaces the label (a Python caller passes its own user_agent()).
 * The label is AuraClaude/<who>/<plugin>:<skill>@<version> (session <id>), the format gcloud_sa.py builds.
 * A realtime connection sends no custom user agent: the label names the Firebase app and goes in the
 * run summary on stderr. A label, not proof: anyone can send it.
 */
'use strict';
const fs = require('fs');
const os = require('os');
const path = require('path');

const MAX_CONCURRENCY = 25;
const DEFAULT_CONCURRENCY = 20;
const BULK = 200;
const READ_TIMEOUT_MS = 30000;
const MAX_FIELDS = 50;

function checkPath(p) {
  if (typeof p !== 'string' || !p.startsWith('/')) return 'not an absolute path';
  // Base64 keys (/emailUser) end in '=', so a query is '?' or a known parameter, not any '='.
  if (p.includes('?') || /[&;](shallow|orderBy|limitTo\w*|startAt|startAfter|endAt|endBefore|equalTo)=/i.test(p)) {
    return 'shallow and query reads are refused';
  }
  if (/\.json$/i.test(p)) return 'a REST URL, not a path';
  const segs = p.split('/').slice(1);
  if (segs.some((s) => s === '')) return 'empty segment';
  if (segs.some((s) => /[.#$[\]]/.test(s))) return 'a key cannot contain . # $ [ ]';
  if (segs.length === 1 && segs[0] === 'users') return '/users itself is never read';
  if (segs.length < 2) return 'the root and top-level nodes are refused';
  return null;
}

// Same ladder as gcloud_sa.py's _ua_who(): a login name, never an email.
function who() {
  let w = (process.env.AINATIVE_UA_WHO || '').trim().split('@')[0].replace(/^\d+\+/, '');
  if (!w) {
    const { execFileSync } = require('child_process');
    for (const key of ['user.email', 'user.name']) {
      let val = '';
      try {
        val = execFileSync('git', ['config', '--global', key], { encoding: 'utf8', timeout: 5000, stdio: ['ignore', 'pipe', 'ignore'] }).trim();
      } catch (e) {
        val = '';
      }
      w = key === 'user.email' ? val.split('@')[0].replace(/^\d+\+/, '') : val;
      if (w) break;
    }
  }
  if (!w) {
    try {
      w = os.userInfo().username;
    } catch (e) {
      w = '';
    }
  }
  return w.replace(/[^A-Za-z0-9._-]+/g, '-').replace(/^[-.]+|[-.]+$/g, '') || 'unknown';
}

function userAgent() {
  if (process.env.AINATIVE_USER_AGENT) return process.env.AINATIVE_USER_AGENT;
  const skill = path.resolve(__dirname, '..');
  const root = path.resolve(skill, '..', '..');
  let plugin = path.basename(root);
  try {
    plugin = JSON.parse(fs.readFileSync(path.join(root, '.claude-plugin', 'plugin.json'), 'utf8')).name || plugin;
  } catch (e) { /* keep the folder name */ }
  let version = 'unknown';
  try {
    const front = fs.readFileSync(path.join(skill, 'SKILL.md'), 'utf8').split('---')[1];
    version = /version:\s*"([^"]+)"/.exec(front)[1];
  } catch (e) { /* unknown */ }
  const raw = process.env.AINATIVE_SESSION_NAME || (process.env.CLAUDE_CODE_SESSION_ID || '').slice(0, 8);
  const session = raw.replace(/[^A-Za-z0-9._-]+/g, '-').replace(/^-+|-+$/g, '') || 'none';
  return `AuraClaude/${who()}/${plugin}:${path.basename(skill)}@${version} (session ${session})`;
}

// Field names and counts only. A long name (usually an id) is cut to its first 6 characters.
function summarize(value) {
  if (value === null || value === undefined) return { exists: false };
  if (typeof value !== 'object') return { exists: true, type: typeof value };
  const keys = Object.keys(value);
  const out = { exists: true, type: Array.isArray(value) ? 'array' : 'object', count: keys.length };
  if (keys.length <= MAX_FIELDS) out.fields = keys.map((k) => (k.length > 15 ? `${k.slice(0, 6)}…` : k));
  return out;
}

function credentials() {
  const key = (process.env.GCP_KEY || '').trim();
  if (!key) throw new Error('GCP_KEY is unset');
  const file = key === '~' || key.startsWith('~/') ? path.join(os.homedir(), key.slice(2)) : key;
  return JSON.parse(key.startsWith('{') ? key : fs.readFileSync(file, 'utf8'));
}

function openDatabase(label) {
  const cli = (process.env.AURA_CLI_DIR || '').trim();
  const dir = process.env.AINATIVE_FIREBASE_ADMIN_DIR
    || (cli && path.join(cli.startsWith('~/') ? path.join(os.homedir(), cli.slice(2)) : cli, 'node_modules'));
  if (!dir) throw new Error('name where firebase-admin is installed: AURA_CLI_DIR or AINATIVE_FIREBASE_ADMIN_DIR');
  if (!process.env.RTDB_URL) throw new Error('RTDB_URL is unset');
  // Resolve as if from a file beside that node_modules, so the package's exports map applies.
  const req = require('module').createRequire(path.join(dir, '..', 'index.js'));
  const { initializeApp, cert } = req('firebase-admin/app');
  const { getDatabase } = req('firebase-admin/database');
  const app = initializeApp({ credential: cert(credentials()), databaseURL: process.env.RTDB_URL }, label);
  return { app, db: getDatabase(app) };
}

// Every path is checked before anything is read; one refusal refuses the whole run.
function validate(paths, { concurrency = DEFAULT_CONCURRENCY, bulk = false } = {}) {
  const bad = paths.map((p) => [p, checkPath(p)]).filter(([, r]) => r);
  if (bad.length) throw Object.assign(new Error(`refused: ${bad.map(([p, r]) => `${p} (${r})`).join('; ')}`), { code: 'REFUSED' });
  if (!(Number.isInteger(concurrency) && concurrency >= 1 && concurrency <= MAX_CONCURRENCY)) {
    throw Object.assign(new Error(`concurrency must be 1-${MAX_CONCURRENCY}`), { code: 'REFUSED' });
  }
  if (paths.length > BULK && !bulk) {
    throw Object.assign(new Error(`bulk read: ${paths.length} paths. Announce it first and pass --bulk`), { code: 'BULK' });
  }
}

async function readPaths(paths, { concurrency = DEFAULT_CONCURRENCY, values = false, bulk = false, db, emit,
  timeoutMs = READ_TIMEOUT_MS } = {}) {
  validate(paths, { concurrency, bulk });
  const results = new Array(paths.length);
  let next = 0;
  // A read that times out is not cancelled (the SDK can retry forever), so it still holds its slot.
  // After the first timeout no new read starts: the rest are reported, and the cap stays a cap.
  let timedOut = false;
  async function worker() {
    while (next < paths.length && !timedOut) {
      const i = next++;
      const p = paths[i];
      let row;
      try {
        // The race's loser must settle quietly: the timer is cleared when the read wins, and an
        // abandoned read's late rejection (say, when the app is deleted) is swallowed.
        const read = db.ref(p).get();
        read.catch(() => {});
        let timer;
        const timeout = new Promise((_, reject) => {
          timer = setTimeout(() => reject(Object.assign(new Error('timed out'), { code: 'TIMEOUT' })), timeoutMs);
          timer.unref();
        });
        let snap;
        try {
          snap = await Promise.race([read, timeout]);
        } finally {
          clearTimeout(timer);
        }
        const v = snap.exists() ? snap.val() : null;
        row = values ? { path: p, value: v } : { path: p, ...summarize(v) };
      } catch (e) {
        if (e.code === 'TIMEOUT') timedOut = true;
        row = { path: p, error: `${e.code || e.name}` };
      }
      results[i] = row;
      if (emit) emit(row);
    }
  }
  await Promise.all(Array.from({ length: Math.min(concurrency, paths.length) }, worker));
  for (let i = next; i < paths.length; i++) {
    results[i] = { path: paths[i], error: 'NOT_READ_AFTER_TIMEOUT' };
    if (emit) emit(results[i]);
  }
  return results;
}

function selfTest() {
  const refused = ['/', '', 'users/x', '/users', '/coaches', '/users/', '//x', '/users/abc?shallow=true',
    '/coaches/a?orderBy="$key"', '/coaches/a&limitToFirst=1', '/coaches/a&shallow=true', '/coaches/a.json',
    '/coaches/a.b', '/coaches/a#b', '/coaches/$x', '/coaches/a[0]'];
  const ok = ['/users/abc123/email', '/coaches/c1', '/emailUser/abc==', '/coaches/shallow'];
  const fails = [];
  for (const p of refused) if (!checkPath(p)) fails.push(`not refused: ${JSON.stringify(p)}`);
  for (const p of ok) if (checkPath(p)) fails.push(`refused: ${p} (${checkPath(p)})`);
  const s = summarize({ email: 'a@b.c', role: 'admin' });
  if (JSON.stringify(s).includes('a@b.c') || JSON.stringify(s).includes('admin')) fails.push('summary prints a value');
  if (JSON.stringify(summarize('secret')).includes('secret')) fails.push('summary prints a scalar');
  const many = Object.fromEntries(Array.from({ length: MAX_FIELDS + 1 }, (_, i) => [`k${i}`, i]));
  if (summarize(many).fields) fails.push('summary lists more than MAX_FIELDS names');
  if (summarize({ abcdefghijklmnopqrstuvwxyz: 1 }).fields[0] !== 'abcdef…') fails.push('long key not cut');
  if (!/^AuraClaude\/[A-Za-z0-9._-]+\/[a-z]+:[a-z0-9-]+@\S+ \(session [A-Za-z0-9._-]+\)$/.test(userAgent())) {
    fails.push(`user agent format: ${userAgent()}`);
  }
  for (const f of fails) process.stderr.write(`FAIL ${f}\n`);
  process.stdout.write(`${JSON.stringify({ selfTest: fails.length ? 'failed' : 'ok', checks: refused.length + ok.length + 5 })}\n`);
  return fails.length ? 1 : 0;
}

async function main(argv) {
  const opts = { values: false, bulk: false, concurrency: DEFAULT_CONCURRENCY };
  const paths = [];
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--self-test') return selfTest();
    if (a === '--values') opts.values = true;
    else if (a === '--bulk') opts.bulk = true;
    else if (a === '--concurrency') opts.concurrency = Number(argv[++i]);
    else paths.push(a);
  }
  if (!paths.length && !process.stdin.isTTY) {
    paths.push(...fs.readFileSync(0, 'utf8').split('\n').map((l) => l.trim()).filter(Boolean));
  }
  if (!paths.length) {
    process.stderr.write('usage: node rtdb_get.js [--values] [--concurrency N] [--bulk] [--self-test] path ...\n');
    return 2;
  }
  const label = userAgent();
  let app;
  try {
    // Refusals first: nothing connects for a run that would be refused.
    validate(paths, opts);
  } catch (e) {
    process.stderr.write(`${e.message}\n`);
    return 2;
  }
  try {
    ({ app, db: opts.db } = openDatabase(label));
  } catch (e) {
    process.stderr.write(`rtdb_get: ${e.code || e.name}: ${String(e.message || '').slice(0, 160)}\n`);
    return 2;
  }
  const results = await readPaths(paths, { ...opts, emit: (row) => process.stdout.write(`${JSON.stringify(row)}\n`) });
  await app.delete().catch(() => {});
  const errors = results.filter((r) => r.error).length;
  process.stderr.write(`rtdb_get: ${paths.length} paths, ${errors} errors, as ${label}\n`);
  return errors ? 1 : 0;
}

module.exports = { checkPath, validate, userAgent, summarize, readPaths, who };

if (require.main === module) {
  main(process.argv.slice(2)).then((code) => process.exit(code));
}
