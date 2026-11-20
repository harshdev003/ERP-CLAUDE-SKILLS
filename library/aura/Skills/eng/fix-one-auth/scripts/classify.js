// Read-only: classify every Auth and live profile against the ONE auth rules from an export (--dir holds
// auth-export.jsonl.gz). Counts on stdout; the uid list of each class goes owner-only to <dir>/violations.json,
// and the staff and test profiles (aurahealth.io, test+, or a staff role) to <dir>/staff-flagged.json.
const fs = require('fs');
const zlib = require('zlib');
const readline = require('readline');
const path = require('path');
const c = require('./one_auth');

// Roles a customer can hold. Any other role is staff: check with SecurityAuth before any change.
const CUSTOMER_ROLES = new Set(['', 'user', 'creator', 'influencer']);
const isStaff = (email, role) => email.endsWith('@aurahealth.io') || email.startsWith('test+') || !CUSTOMER_ROLES.has(role);

(async () => {
  const dir = c.arg('dir');
  if (!dir) c.fail('usage: classify.js --env=… --user-agent=… --dir=<run dir>');
  const {Pool} = c.rq('pg');
  const pool = new Pool({connectionString: c.env.POSTGRES_DATABASE_URI, max: 1});
  const prof = new Map();
  const staff = new Set();
  let last = '';
  for (;;) {
    const rows = (await pool.query('SELECT id, lower(trim(email)) em, coalesce(role, \'\') role FROM "user" WHERE "deletedAt" IS NULL AND id > $1 ORDER BY id LIMIT 50000', [last])).rows;
    if (!rows.length) break;
    last = rows[rows.length - 1].id;
    for (const r of rows) {
      prof.set(r.id, r.em || '');
      if (isStaff(r.em || '', r.role)) staff.add(r.id);
    }
  }
  await pool.end();
  const profByEmail = new Map();
  for (const [, em] of prof) if (em) profByEmail.set(em, (profByEmail.get(em) || 0) + 1);

  const ems = (a) => [...new Set([a.email, ...(a.providers || []).map((p) => p.email)].filter(Boolean).map(c.norm))];
  const totals = {};
  const lists = {};
  const add = (k, id) => {
    totals[k] = (totals[k] || 0) + 1;
    if (id) (lists[k] = lists[k] || []).push(id);
  };
  const atId = new Set();
  const claimTo = new Set();
  let n = 0;
  let first = true;
  const rl = readline.createInterface({input: fs.createReadStream(path.join(dir, 'auth-export.jsonl.gz')).pipe(zlib.createGunzip())});
  for await (const line of rl) {
    if (first) { first = false; continue; }
    const a = JSON.parse(line);
    n++;
    if (a.disabled) {
      if (prof.has(a.uid)) atId.add(a.uid);
      add('D disabled (kept, not checked)', a.uid);
      continue;
    }
    const e = ems(a);
    if (prof.has(a.uid)) {
      atId.add(a.uid);
      const pe = prof.get(a.uid);
      if (!e.length && !pe) add('OK both empty');
      else if (e.length === 1 && e[0] === pe) add(c.norm(a.email) === pe ? 'OK match' : 'E5 top-level Auth email empty, sign-in has it', c.norm(a.email) === pe ? null : a.uid);
      else if (!e.length) add('E1 Auth no email, profile has', a.uid);
      else if (e.length > 1) add('E2 Auth several emails', a.uid);
      else if (!pe) add('E3 profile empty, Auth has', a.uid);
      else add('E4 emails differ', a.uid);
      continue;
    }
    const cl = a.claimUserId;
    if (cl && prof.has(cl)) {
      claimTo.add(cl);
      const pe = prof.get(cl);
      add(e.length && e.every((x) => x === pe) ? 'I1 extra Auth, claim to live profile, same email' : 'I2 extra Auth, claim to live profile, other email', a.uid);
      continue;
    }
    if (cl) { add('I3 claim to deleted/missing profile', a.uid); continue; }
    if (!e.length) { add('O1 no profile, no email', a.uid); continue; }
    add(e.some((x) => profByEmail.has(x)) ? 'O2 no profile, email of a live profile' : 'O3 no profile, own email', a.uid);
  }
  for (const [id] of prof) {
    if (!atId.has(id)) add(claimTo.has(id) ? 'P1 profile without Auth, reached by claim' : 'P2 profile without Auth, unreachable (orphaned, OK)', claimTo.has(id) ? id : null);
  }
  fs.writeFileSync(path.join(dir, 'violations.json'), JSON.stringify(lists), {mode: 0o600});
  fs.writeFileSync(path.join(dir, 'staff-flagged.json'), JSON.stringify([...staff]), {mode: 0o600});
  const ok = (totals['OK both empty'] || 0) + (totals['OK match'] || 0);
  const live = n - (totals['D disabled (kept, not checked)'] || 0);
  console.log(JSON.stringify({auths: n, liveProfiles: prof.size, staffOrTestProfiles: staff.size, ...totals}, null, 1));
  console.log('profiles meeting both rules', ok, 'of', prof.size, '=', (100 * ok / prof.size).toFixed(3) + '%',
    '| enabled Auths meeting both rules', ok, 'of', live, '=', (100 * ok / live).toFixed(3) + '%');
  process.exit(0);
})().catch((e) => c.fail('classify ERR ' + (e.code || c.scrub(e.message))));
