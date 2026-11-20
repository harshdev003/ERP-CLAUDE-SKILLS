// Read-only: a full Auth export (listUsers, 1000 a page, with retries) to --out, gzip JSON lines, owner-only.
// Writes a .part file, then renames it. Prints counts only.
const fs = require('fs');
const zlib = require('zlib');
const c = require('./one_auth');

(async () => {
  const out = c.arg('out');
  if (!out) c.fail('usage: export.js --env=… --user-agent=… --out=<file.jsonl.gz>');
  const builtAt = new Date().toISOString();
  const gz = zlib.createGzip();
  const ws = fs.createWriteStream(out + '.part', {mode: 0o600});
  gz.pipe(ws);
  gz.write(JSON.stringify({builtAt, project: c.project}) + '\n');
  let token;
  let n = 0;
  do {
    let page;
    for (let t = 0; ; t++) {
      try {
        page = await c.auth.listUsers(1000, token);
        break;
      } catch (e) {
        if (t >= 8) throw e;
        await c.sleep([2, 5, 15, 30, 60, 60, 60, 60][t] * 1000);
      }
    }
    for (const u of page.users) {
      const claims = u.customClaims || {};
      gz.write(JSON.stringify({
        uid: u.uid, email: u.email || null, emailVerified: !!u.emailVerified, disabled: !!u.disabled,
        providers: (u.providerData || []).map((p) => ({providerId: p.providerId, email: p.email || null})),
        claimUserId: claims.userId === undefined ? null : claims.userId,
        created: u.metadata.creationTime, lastSignIn: u.metadata.lastSignInTime || null,
        tokensValidAfter: u.tokensValidAfterTime || null,
      }) + '\n');
    }
    n += page.users.length;
    token = page.pageToken;
    if (n % 200000 < 1000) console.error(new Date().toISOString(), 'exported', n);
  } while (token);
  gz.end();
  await new Promise((r) => ws.on('finish', r));
  fs.renameSync(out + '.part', out);
  console.log('export done', n, 'builtAt', builtAt);
  process.exit(0);
})().catch((e) => c.fail('export ERR ' + (e.code || c.scrub(e.message))));
