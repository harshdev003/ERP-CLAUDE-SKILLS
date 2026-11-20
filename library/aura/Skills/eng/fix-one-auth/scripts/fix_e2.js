// E2, provider email changed (R15 item 3): set each Auth's email to its last-used sign-in's email.
// --plan=<[{uid, keep}]> from run.py's E2 plan. Dry run without --apply. Per Auth: live re-check, the before state
// recorded owner-only to --before, one accounts:update that keeps validSince, then a read-back. Prefixes only.
// Fixed uids go to --done (the input for the profile-email job); every uid handled goes to --attempted, so the
// next batch moves on.
const fs = require('fs');
const c = require('./one_auth');

(async () => {
  const plan = JSON.parse(fs.readFileSync(c.arg('plan'), 'utf8'));
  const flagged = new Set(c.arg('staff-flagged') ? JSON.parse(fs.readFileSync(c.arg('staff-flagged'), 'utf8')) : []);
  if (c.apply && !(c.arg('before') && c.arg('done') && c.arg('attempted'))) c.fail('REFUSED: --apply needs --before, --done and --attempted files');
  const held = c.held();
  const t = {};
  const inc = (k) => { t[k] = (t[k] || 0) + 1; };
  for (const {uid, keep} of plan) {
    const p = c.p6(uid);
    if (!c.windowOpen()) { console.log('STOP: the IAM window has ended'); break; }
    if (c.apply) c.record(c.arg('attempted'), uid);
    if (held.has(uid)) { console.log(p, 'held, skipped'); inc('held'); continue; }
    if (flagged.has(uid)) { console.log(p, 'staff or test, skipped: check with SecurityAuth'); inc('staff or test'); continue; }
    const a = await c.getUserOrNull(uid);
    if (!a) { console.log(p, 'gone'); inc('gone'); continue; }
    if (a.disabled) { console.log(p, 'disabled, skipped'); inc('disabled'); continue; }
    if (c.authEmails(a).some((e) => e.endsWith('@aurahealth.io') || e.startsWith('test+'))) { console.log(p, 'staff or test, skipped: check with SecurityAuth'); inc('staff or test'); continue; }
    // One sign-in, or password plus one other (a password sign-in's email is the Auth email and moves with it).
    if (a.providerData.filter((x) => x.providerId !== 'password').length > 1 || keep === 'password') {
      console.log(p, 'SKIP: several sign-ins (for Jerome)'); inc('skip: several sign-ins'); continue;
    }
    const k = a.providerData.find((x) => x.providerId === keep);
    if (!k || !k.email) { console.log(p, 'SKIP: kept sign-in missing or no email'); inc('skip: kept sign-in missing'); continue; }
    const other = await c.auth.getUserByEmail(k.email).catch(() => null);
    if (other && other.uid !== uid) { console.log(p, 'SKIP: email on another Auth (for Jerome)'); inc('skip: email on another Auth'); continue; }
    const tva = a.tokensValidAfterTime;
    if (!tva) { console.log(p, 'SKIP: no tokensValidAfter (for Jerome)'); inc('skip: no tokensValidAfter'); continue; }
    const changes = c.norm(a.email) !== c.norm(k.email);
    console.log(p, 'keep', keep, '| email changes', changes);
    if (!c.apply) { inc('would set'); continue; }
    c.record(c.arg('before'), {t: new Date().toISOString(), uid, before: a.toJSON()});
    const r = await c.setAuthEmail(uid, k.email, tva);
    if (!r.ok) { console.log(p, 'ERROR', r.status, /EMAIL_EXISTS/.test(r.body) ? 'EMAIL_EXISTS' : ''); inc('error'); continue; }
    const b = await c.auth.getUser(uid);
    const one = new Set(c.authEmails(b)).size === 1;
    const kept = b.tokensValidAfterTime === tva;
    console.log(p, 'read back: one email', one, '| sessions kept', kept);
    if (!kept) { console.log('STOP: SESSIONS CHANGED'); inc('SESSIONS CHANGED'); break; }
    if (!one) { inc('set, read-back not one email (for Jerome)'); continue; }
    inc('set');
    c.record(c.arg('done'), uid);
  }
  console.log(c.envName, c.apply ? 'APPLY' : 'DRY', JSON.stringify(t));
  process.exit(0);
})().catch((e) => c.fail('fix_e2 ERR ' + (e.code || c.scrub(e.message))));
