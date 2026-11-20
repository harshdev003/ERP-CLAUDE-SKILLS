// Read-only: after a fix, re-check each listed uid (--list) against both rules: its Auth carries one email and that
// email equals the profile email in userDB (empty/empty is OK). Counts and failing prefixes only.
const fs = require('fs');
const c = require('./one_auth');

(async () => {
  const ids = fs.readFileSync(c.arg('list'), 'utf8').split('\n').map((l) => l.trim()).filter(Boolean);
  const {Pool} = c.rq('pg');
  const pool = new Pool({connectionString: c.env.POSTGRES_DATABASE_URI, max: 1});
  const t = {};
  const inc = (k) => { t[k] = (t[k] || 0) + 1; };
  for (const uid of ids) {
    const a = await c.getUserOrNull(uid);
    const row = (await pool.query('SELECT lower(trim(email)) em FROM "user" WHERE id = $1 AND "deletedAt" IS NULL', [uid])).rows[0];
    if (!a || !row) { inc(!a ? 'no Auth' : 'no live profile'); continue; }
    const e = c.authEmails(a);
    const pe = row.em || '';
    if ((!e.length && !pe) || (e.length === 1 && e[0] === pe && c.norm(a.email) === pe)) { inc('OK'); continue; }
    inc('still broken');
    console.log(c.p6(uid), 'still broken: Auth emails', e.length, '| matches profile', e.includes(pe), '| top-level set', !!a.email);
  }
  await pool.end();
  console.log('recheck', JSON.stringify(t));
  process.exit(t['still broken'] ? 2 : 0);
})().catch((e) => c.fail('recheck ERR ' + (e.code || c.scrub(e.message))));
