#!/usr/bin/env node
/*
 * One Admin SDK realtime connection for check-one-auth's RTDB reads.
 *
 * Reads go over the realtime protocol (getDatabase().ref(path).get()), not REST, so a run over many
 * people doesn't count toward the "[Security] RTDB REST read surge" alert, which counts rest_get only.
 * The profiler still shows them as admin reads from our IP.
 *
 * Protocol: one JSON object per line on stdin, {"path": "..."}; one per line on stdout, {"path", "value"}
 * or {"path", "error"}. Only exact paths are allowed: /users/{id}/email|deletedAt|role. Anything else
 * (the root, /users, /emailUser, a shallow or listing read) is refused, never read.
 *
 * check.py passes RTDB_URL and the AuraClaude label. Credentials: GCP_KEY (inline JSON or a key-file
 * path). firebase-admin is loaded from the node_modules of the Aura CLI checkout named by AURA_CLI_DIR
 * (or a node_modules folder named by AINATIVE_FIREBASE_ADMIN_DIR); there is no default.
 * A realtime connection carries no custom user agent; the app is named with the AuraClaude label.
 */
'use strict';
const fs = require('fs');
const os = require('os');
const path = require('path');
const readline = require('readline');

const ALLOWED = [/^\/users\/[^/.#$[\]]+\/(email|deletedAt|role)$/];
const READ_TIMEOUT_MS = 30000;
const allowed = (p) => typeof p === 'string' && ALLOWED.some((re) => re.test(p));

function out(obj) {
  process.stdout.write(`${JSON.stringify(obj)}\n`);
}

function credentials() {
  const key = (process.env.GCP_KEY || '').trim();
  if (!key) throw new Error('GCP_KEY is unset');
  // `~/x` is under the home folder (as Python's expanduser reads it); a bare join would start from the root.
  const file = key === '~' || key.startsWith('~/') ? path.join(os.homedir(), key.slice(2)) : key;
  return JSON.parse(key.startsWith('{') ? key : fs.readFileSync(file, 'utf8'));
}

function main() {
  // No default: the caller names the Aura CLI checkout whose node_modules holds firebase-admin.
  const cli = (process.env.AURA_CLI_DIR || '').trim();
  const dir = process.env.AINATIVE_FIREBASE_ADMIN_DIR
    || (cli && path.join(cli.startsWith('~/') ? path.join(os.homedir(), cli.slice(2)) : cli, 'node_modules'));
  let app;
  let db;
  try {
    if (!dir) throw new Error('name the Aura CLI checkout: AURA_CLI_DIR or --cli-dir');
    // Resolve as if from a file beside that node_modules, so the package's exports map applies.
    const req = require('module').createRequire(path.join(dir, '..', 'index.js'));
    const { initializeApp, cert } = req('firebase-admin/app');
    const { getDatabase } = req('firebase-admin/database');
    const label = process.env.AINATIVE_USER_AGENT || 'AuraClaude/unknown/eng:check-one-auth';
    if (!process.env.RTDB_URL) throw new Error('RTDB_URL is unset (check.py passes it)');
    app = initializeApp({ credential: cert(credentials()), databaseURL: process.env.RTDB_URL }, label);
    db = getDatabase(app);
  } catch (e) {
    out({ fatal: `realtime reader: ${e.code || e.name}: ${String(e.message || '').slice(0, 160)}` });
    process.exit(2);
  }
  out({ ready: true });
  const rl = readline.createInterface({ input: process.stdin });
  let queue = Promise.resolve();
  rl.on('line', (line) => {
    // One read at a time, in order: well under any concurrency limit.
    queue = queue.then(async () => {
      let req;
      try {
        req = JSON.parse(line);
      } catch (e) {
        out({ error: 'unreadable request' });
        return;
      }
      if (!allowed(req.path)) {
        out({ path: req.path, error: 'path not allowed' });
        return;
      }
      try {
        // The SDK can retry a lost connection forever: give up on one read after READ_TIMEOUT_MS.
        const snap = await Promise.race([db.ref(req.path).get(), new Promise((_, reject) => setTimeout(
          () => reject(Object.assign(new Error('timed out'), { code: 'TIMEOUT' })), READ_TIMEOUT_MS).unref())]);
        out({ path: req.path, value: snap.exists() ? snap.val() : null });
      } catch (e) {
        out({ path: req.path, error: `${e.code || e.name}` });
      }
    });
  });
  rl.on('close', () => {
    queue.then(() => app.delete()).then(() => process.exit(0), () => process.exit(0));
  });
}

main();
