// Read-only: emails (top-level or any sign-in, lowercased) on more than one Auth, from an export. Counts on stdout;
// the uid groups go owner-only to --out. Duplicates are SecurityAuth's merge work, never fixed here.
const fs = require('fs');
const zlib = require('zlib');
const readline = require('readline');

(async () => {
  const [exportFile, out] = process.argv.slice(2);
  const by = new Map();
  let first = true;
  const rl = readline.createInterface({input: fs.createReadStream(exportFile).pipe(zlib.createGunzip())});
  for await (const line of rl) {
    if (first) { first = false; continue; }
    const a = JSON.parse(line);
    for (const e of new Set([a.email, ...(a.providers || []).map((p) => p.email)].filter(Boolean).map((s) => s.trim().toLowerCase()))) {
      const x = by.get(e);
      if (x === undefined) by.set(e, a.uid);
      else if (x !== a.uid) by.set(e, Array.isArray(x) ? [...new Set([...x, a.uid])] : [x, a.uid]);
    }
  }
  const dups = [...by.values()].filter(Array.isArray);
  const bySize = {};
  for (const d of dups) bySize[d.length] = (bySize[d.length] || 0) + 1;
  const appleRelay = [...by.entries()].filter(([e, v]) => Array.isArray(v) && e.endsWith('privaterelay.appleid.com')).length;
  if (out) fs.writeFileSync(out, JSON.stringify(dups), {mode: 0o600});
  console.log(JSON.stringify({emails: by.size, onSeveralAuths: dups.length, bySize, appleRelay}));
  process.exit(0);
})().catch((e) => { console.log('dup_emails ERR', e.code || 'x'); process.exit(1); });
