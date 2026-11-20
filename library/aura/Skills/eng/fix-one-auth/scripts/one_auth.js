// Shared setup for the fix-one-auth workers. Never prints an email, a full uid, a hash or an IP.
//
// --env=dev   aura-dev, the UserService checkout's .env.development Firebase account
// --env=prod  firebase-auratech16: reads as the GCP_KEY service account; writes only with
//             --write-as=<service account> (impersonated from GCP_KEY, inside the IAM window Jerome opens)
// --user-agent=<AuraClaude label>  passed by run.py; sent on every Identity Toolkit REST call
const fs = require('fs');
const path = require('path');

const arg = (name) => {
  const found = process.argv.find((a) => a.startsWith(`--${name}=`));
  return found ? found.slice(name.length + 3) : undefined;
};
const flag = (name) => process.argv.includes(`--${name}`);
const fail = (msg) => {
  console.log(msg);
  process.exit(1);
};

const usDir = process.env.USER_SERVICE_DIR;
if (!usDir || !fs.existsSync(path.join(usDir, 'package.json'))) fail('USER_SERVICE_DIR (a UserService checkout with node_modules) is required');
const rq = require('module').createRequire(path.join(usDir, 'package.json'));
const {initializeApp, cert} = rq('firebase-admin/app');
const {getAuth} = rq('firebase-admin/auth');

const envName = arg('env');
if (!['dev', 'prod'].includes(envName)) fail('usage: --env=dev|prod');
const project = envName === 'dev' ? 'aura-dev' : 'firebase-auratech16';
const env = rq('dotenv').parse(fs.readFileSync(path.join(usDir, envName === 'dev' ? '.env.development' : '.env'), 'utf8'));
if (env.FIREBASE_PROJECT_ID !== project) fail('REFUSED: the UserService env file is for another project');

const userAgent = arg('user-agent');
if (!userAgent || !userAgent.startsWith('AuraClaude/')) fail('--user-agent=<AuraClaude label> is required (run.py passes it)');

const apply = flag('apply');
const writeAs = arg('write-as');
const SCOPES = ['https://www.googleapis.com/auth/cloud-platform', 'https://www.googleapis.com/auth/firebase', 'https://www.googleapis.com/auth/identitytoolkit'];

// The IAM window: a write past --window-end stops, whatever is left.
const windowEnd = arg('window-end') ? Date.parse(arg('window-end')) : NaN;
const windowOpen = () => !apply || envName === 'dev' || Date.now() < windowEnd;
if (apply && envName === 'prod') {
  if (!writeAs) fail('REFUSED: a prod write needs --write-as=<service account> (the IAM window identity)');
  if (!writeAs.endsWith(`@${project}.iam.gserviceaccount.com`)) fail('REFUSED: --write-as is not a service account of this project');
  if (Number.isNaN(windowEnd)) fail('REFUSED: a prod write needs --window-end=<ISO time the IAM window closes>');
  if (!windowOpen()) fail('REFUSED: the IAM window has ended');
}
if (!apply && writeAs) fail('REFUSED: --write-as is only for --apply');

function credentialFor() {
  if (envName === 'dev') {
    return cert({clientEmail: env.FIREBASE_CLIENT_EMAIL, privateKey: env.FIREBASE_PRIVATE_KEY.replace(/\\n/g, '\n'), projectId: project});
  }
  if (!process.env.GCP_KEY) fail('GCP_KEY is required for prod');
  if (!writeAs) return cert(JSON.parse(process.env.GCP_KEY));
  // Impersonate the window identity from GCP_KEY, never from the personal default login.
  const {GoogleAuth, Impersonated} = rq('google-auth-library');
  let target;
  return {getAccessToken: async () => {
    if (!target) {
      const source = await new GoogleAuth({credentials: JSON.parse(process.env.GCP_KEY), scopes: SCOPES}).getClient();
      target = new Impersonated({lifetime: 3600, sourceClient: source, targetPrincipal: writeAs, targetScopes: SCOPES});
    }
    const token = (await target.getAccessToken()).token;
    const expiry = (target.credentials || {}).expiry_date;
    return {access_token: token, expires_in: expiry ? Math.max(0, Math.floor((expiry - Date.now()) / 1000)) : 0};
  }};
}

// The Admin SDK sends no user agent of ours, so the app carries the label as its name.
const app = initializeApp({credential: credentialFor(), projectId: project}, userAgent.slice(0, 120));
const auth = getAuth(app);

// The held-account keep list, read under held_uids.py's contract (HELD_UIDS_JSON overrides; mode 600; a JSON
// array of uid strings; missing or empty is refused before any write), plus the staff and test uids. run.py
// already drops held uids from a batch; this is the second check, re-read when a worker starts.
const heldFile = () => (process.env.HELD_UIDS_JSON || path.join(process.env.HOME, '.claude/held-accounts/held-uids.json')).replace(/^~/, process.env.HOME);
function readList(file, what, {required, allowEmpty}) {
  let st;
  try { st = fs.statSync(file); } catch (e) {
    if (required) fail(`REFUSED: no ${what} at ${file}`);
    return [];
  }
  if (st.mode & 0o077) fail(`REFUSED: ${what} ${file} is readable by group or others; chmod 600 it`);
  let data;
  try { data = JSON.parse(fs.readFileSync(file, 'utf8')); } catch (e) { fail(`REFUSED: ${what} ${file} is unreadable or not JSON`); }
  if (!Array.isArray(data) || data.some((u) => typeof u !== 'string' || !u.trim())) fail(`REFUSED: ${what} ${file} must be a JSON array of uid strings`);
  if (required && !allowEmpty && !data.length) fail(`REFUSED: ${what} ${file} is empty`);
  return data.map((u) => u.trim());
}
function held() {
  const set = new Set(readList(heldFile(), 'keep list', {required: apply, allowEmpty: false}));
  const staff = process.env.ONE_AUTH_STAFF_FILE;
  if (staff) for (const u of readList(staff, 'staff list', {required: true, allowEmpty: true})) set.add(u);
  else if (apply && envName === 'prod') fail('REFUSED: ONE_AUTH_STAFF_FILE (staff and test uids) is required for a prod write');
  return set;
}

// A uid as printed (held_uids.py's label): any `provider:` prefix, then 6 characters, never a short id in full.
const p6 = (s) => {
  if (!s) return '-';
  const str = String(s).trim();
  const i = str.lastIndexOf(':');
  const rest = str.slice(i + 1);
  return str.slice(0, i + 1) + rest.slice(0, Math.min(6, Math.floor(rest.length / 2))) + '…';
};
const norm = (s) => (s || '').trim().toLowerCase();
const authEmails = (a) => [...new Set([a.email, ...(a.providerData || []).map((p) => p.email)].filter(Boolean).map(norm))];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const record = (file, obj) => fs.appendFileSync(file, (typeof obj === 'string' ? obj : JSON.stringify(obj)) + '\n', {mode: 0o600});
const scrub = (s) => String(s).replace(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+/g, '<e>').replace(/\s+/g, ' ').slice(0, 200);

// At most 5 concurrent calls, whatever --conc asks for.
const conc = () => Math.min(5, Math.max(1, +(arg('conc') || 5)));

async function getUserOrNull(uid) {
  return auth.getUser(uid).catch((e) => {
    if (e.code === 'auth/user-not-found') return null;
    throw e;
  });
}

// Sets an Auth's top-level email with accounts:update. validSince is sent when the account has
// tokensValidAfter, so refresh tokens stay valid (updateUser alone signs the person out).
async function setAuthEmail(uid, email, tokensValidAfter) {
  const token = (await app.options.credential.getAccessToken()).access_token;
  const body = {localId: uid, email};
  if (tokensValidAfter) body.validSince = String(Math.floor(Date.parse(tokensValidAfter) / 1000));
  const r = await fetch(`https://identitytoolkit.googleapis.com/v1/projects/${project}/accounts:update`, {
    method: 'POST',
    headers: {'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json', 'User-Agent': userAgent},
    body: JSON.stringify(body),
  });
  return r.ok ? {ok: true} : {ok: false, status: r.status, body: await r.text()};
}

module.exports = {arg, flag, fail, apply, envName, project, env, rq, auth, held, p6, norm, authEmails, sleep, record, scrub,
  conc, getUserOrNull, setAuthEmail, windowOpen};
