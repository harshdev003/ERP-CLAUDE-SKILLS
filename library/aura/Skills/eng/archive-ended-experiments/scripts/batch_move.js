#!/usr/bin/env node
/**
 * Move experiments to /backup/experiments in multi-location batches.
 *
 * WHY THIS EXISTS, rather than looping `aura firebaseBackup`:
 *
 * The Aura RN app holds `.on('value')` on the WHOLE /experiments node. A value
 * listener fires on every child change and delivers the entire node, so the app
 * re-runs chooseAll and re-triggers every subscribed screen once per removal.
 * Moving 2,000 rows one at a time is 2,000 of those per connected client.
 *
 * A multi-location update is one write: one value event, one re-render, whatever
 * the batch size. It is also atomic -- the backup write and the removal land
 * together, so there is no window in which a row exists in neither place. The
 * per-ref CLI path does them as two calls and can be interrupted between.
 *
 * Usage:
 *   node batch_move.js --credentials <sa.json> --database-url <url> \
 *     --manifest <ids.json> [--batch-size 250] [--execute] [--restore]
 */

const fs = require('fs');

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  return i === -1 ? fallback : process.argv[i + 1];
}
const has = (name) => process.argv.includes(`--${name}`);

const credentials = arg('credentials');
const databaseURL = arg('database-url');
const manifestPath = arg('manifest');
const batchSize = parseInt(arg('batch-size', '250'), 10);
// Seconds to wait BETWEEN batches. Batching already collapses thousands of child
// events into one value event per batch, but each of those still delivers the
// whole node to every connected client and re-runs chooseAll on every subscribed
// screen. Spacing them keeps that to one re-render per interval instead of a
// burst, and leaves room to watch the first batches land before the rest go.
const throttle = parseFloat(arg('throttle', '30'));
const execute = has('execute');
const restore = has('restore');

if (!credentials || !databaseURL || !manifestPath) {
  console.error('need --credentials, --database-url and --manifest');
  process.exit(2);
}

// firebase-admin v14 is modular: no admin.credential.cert, no admin.database().
// Matches how Tools/CLI src/services/firebase.js initialises it -- read that
// file rather than guessing the API if this ever stops working.
const { initializeApp, cert } = require('firebase-admin/app');
const { getDatabase } = require('firebase-admin/database');

const app = initializeApp({ credential: cert(require(credentials)), databaseURL });
const db = getDatabase(app);

const ids = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
const from = restore ? '/backup/experiments' : '/experiments';
const to = restore ? '/experiments' : '/backup/experiments';

(async () => {
  const moved = [];
  const skipped = [];

  // One read of the whole source node, not one per id. 2,000+ round trips is
  // both slow and pointless when the node is under a megabyte -- and reading it
  // once gives every batch a consistent view rather than a drifting one.
  const source = (await db.ref(from).once('value')).val() || {};
  console.log(`read ${Object.keys(source).length} rows from ${from}`);

  for (let i = 0; i < ids.length; i += batchSize) {
    const slice = ids.slice(i, i + batchSize);
    const updates = {};
    for (const id of slice) {
      const value = source[id];
      if (value === undefined || value === null) {
        skipped.push({ id, why: `absent from ${from}` });
        continue;
      }
      // set-semantics, not merge: the destination may already hold an older
      // snapshot of this experiment, and a merge would leave a hybrid row that
      // is neither the old state nor the current one.
      updates[`${to}/${id}`] = value;
      updates[`${from}/${id}`] = null;
      moved.push(id);
    }
    const paths = Object.keys(updates).length;
    if (!paths) continue;
    if (execute) {
      await db.ref('/').update(updates);
    }
    const n = Math.floor(i / batchSize) + 1;
    const total = Math.ceil(ids.length / batchSize);
    console.log(
      `${execute ? 'wrote' : 'would write'} batch ${n}/${total}: ` +
        `${paths / 2} experiments, ${paths} paths, 1 value event` +
        ` (${moved.length} moved so far)`
    );
    const more = i + batchSize < ids.length;
    if (execute && throttle > 0 && more) {
      console.log(`  waiting ${throttle}s before the next batch`);
      await new Promise((r) => setTimeout(r, throttle * 1000));
    }
  }
  console.log(`\n${execute ? 'moved' : 'would move'} ${moved.length}, skipped ${skipped.length}`);
  for (const s of skipped.slice(0, 10)) console.log(`  SKIP ${s.id}: ${s.why}`);
  if (execute) {
    const out = `batch-manifest-${Date.now()}.json`;
    fs.writeFileSync(out, JSON.stringify({ from, to, moved, skipped }, null, 1));
    console.log(`manifest: ${out}  <- rollback needs this`);
  }
  process.exit(0);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
