// Missing top-level email (E5): set it from the Auth's single sign-in email. --list=<file of uids>.
// Live re-check per Auth (still no top-level email, not disabled, one distinct sign-in email). Sessions are kept
// (validSince) when tokensValidAfter exists; without it the Auth is skipped unless --allow-sign-out, which needs
// Jerome's go accepting a one-time sign-out. Rollback = remove the email: every changed uid goes owner-only to
// --changed and its prior tokensValidAfter to --prior; every uid handled goes to --attempted, so the next batch
// moves on. --skip/--limit, --conc (at most 5), --pace-ms. Counts only.
const fs = require('fs');
const c = require('./one_auth');

(async () => {
  const all = fs.readFileSync(c.arg('list'), 'utf8').split('\n').map((l) => l.split('\t')[0].trim()).filter(Boolean);
  const skip = +(c.arg('skip') || 0);
  const ids = all.slice(skip, skip + +(c.arg('limit') || all.length));
  const pace = +(c.arg('pace-ms') || 100);
  const allowSignOut = c.flag('allow-sign-out');
  const flagged = new Set(c.arg('staff-flagged') ? JSON.parse(fs.readFileSync(c.arg('staff-flagged'), 'utf8')) : []);
  if (c.apply && !(c.arg('changed') && c.arg('prior') && c.arg('attempted'))) c.fail('REFUSED: --apply needs --changed, --prior and --attempted files');
  const held = c.held();
  const t = {};
  const inc = (k) => { t[k] = (t[k] || 0) + 1; };
  let stop = '';
  let errors = 0;
  let i = 0;
  const one = async (u) => {
    if (held.has(u)) return inc('held');
    if (flagged.has(u)) return inc('staff or test (check with SecurityAuth)');
    const a = await c.getUserOrNull(u);
    if (!a) return inc('gone');
    if (a.email) return inc('already has email');
    if (a.disabled) return inc('disabled');
    const em = [...new Set(a.providerData.map((p) => c.norm(p.email)).filter(Boolean))];
    if (em.length !== 1) return inc('not one sign-in email');
    if (em[0].endsWith('@aurahealth.io') || em[0].startsWith('test+')) return inc('staff or test (check with SecurityAuth)');
    const tva = a.tokensValidAfterTime;
    if (!tva && !allowSignOut) return inc('no tokensValidAfter (skipped)');
    if (!c.apply) return inc(tva ? 'would set' : 'would set, may sign out');
    // Another worker may have stopped the batch while this one was reading: never start a write after a stop.
    if (stop) { inc('not run (stopped)'); return 'not run'; }
    const src = a.providerData.find((p) => c.norm(p.email) === em[0]).email;
    const r = await c.setAuthEmail(u, src, tva);
    if (!r.ok) {
      fs.appendFileSync(c.arg('changed') + '.errors', r.status + ' ' + c.scrub(r.body) + '\n', {mode: 0o600});
      if (/EMAIL_EXISTS/.test(r.body)) return inc('collision (skipped)');
      if (++errors > 50) stop = 'errors';
      inc('error ' + r.status);
      return 'not run';
    }
    const b = await c.auth.getUser(u);
    if (c.norm(b.email) !== em[0]) return inc('read-back mismatch');
    if (tva && b.tokensValidAfterTime !== tva) { stop = 'SESSIONS CHANGED'; return inc('SESSIONS CHANGED'); }
    c.record(c.arg('prior'), {uid: u, tokensValidAfter: tva || null});
    c.record(c.arg('changed'), u);
    inc(tva ? 'set, sessions kept' : (b.tokensValidAfterTime ? 'set, sessions reset' : 'set, sessions kept'));
  };
  const worker = async () => {
    while (i < ids.length && !stop) {
      if (!c.windowOpen()) { stop = 'window end'; break; }
      const u = ids[i++];
      // Handled = an outcome, so the next batch moves on; a uid not run or failed stays for the next batch.
      const outcome = await one(u).catch((e) => { inc('error ' + (e.code || 'x')); return 'not run'; });
      if (c.apply && outcome !== 'not run') c.record(c.arg('attempted'), u);
      await c.sleep(pace);
    }
  };
  const timer = setInterval(() => console.error(JSON.stringify({done: i, of: ids.length, ...t})), 60000);
  await Promise.all(Array.from({length: c.conc()}, worker));
  clearInterval(timer);
  if (stop) console.log('STOP:', stop);
  console.log(c.envName, c.apply ? 'APPLY' : 'DRY', JSON.stringify({from: skip, count: ids.length, ...t}));
  process.exit(stop ? 2 : 0);
})().catch((e) => c.fail('fix_top_email ERR ' + (e.code || c.scrub(e.message))));
