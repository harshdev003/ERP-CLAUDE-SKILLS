// Usage: node delete-raw.js <marker-file> <pid>[,<pid>...]
// Deletes the Firebase CLI's leftover stream copies ($TMPDIR/tmp-<pid>-*) owned by the given CLI pid(s)
// and modified after <marker-file> was created.
// `firebase database:profile` streams to these files and never deletes them; they hold IPs and keys.
// Only this run's pids: another profiler running on the machine re-opens its own file by name at the
// end, so deleting it mid-run makes that run fail with 0 operations.
const fs = require('fs');
const os = require('os');
const path = require('path');
const [marker, pidArg] = process.argv.slice(2);
const pids = (pidArg || '').split(',').filter((p) => /^\d+$/.test(p));
if (!marker || pids.length === 0) {
  console.error('usage: delete-raw.js <marker-file> <pid>[,<pid>...]');
  process.exit(2);
}
const t = process.env.TMPDIR || os.tmpdir();
const since = fs.statSync(marker).mtimeMs;
const prefixes = pids.map((p) => `tmp-${p}-`);
let n = 0;
for (const f of fs.readdirSync(t)) {
  if (!prefixes.some((pre) => f.startsWith(pre))) continue;
  const p = path.join(t, f);
  try {
    const s = fs.statSync(p);
    if (s.isFile() && s.mtimeMs >= since) { fs.unlinkSync(p); n++; }
  } catch { /* already gone */ }
}
console.log(`Deleted ${n} CLI temp file(s).`);
