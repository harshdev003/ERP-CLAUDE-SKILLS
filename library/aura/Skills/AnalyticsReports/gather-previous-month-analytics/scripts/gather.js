#!/usr/bin/env node
/**
 * gather.js — pull last month's analytics rows from PaymentService Postgres
 * and write the seven monthly JSON reports plus their CSVs into AnalyticsReports.
 *
 * See ../SKILL.md for the full workflow and type → path mapping.
 */

'use strict';

const fs = require('fs');
const path = require('path');
const {execSync} = require('child_process');

function parseArgs(argv) {
  const args = {};
  for (const raw of argv.slice(2)) {
    if (!raw.startsWith('--')) continue;
    const [key, value] = raw.slice(2).split('=');
    args[key] = value === undefined ? true : value;
  }
  return args;
}

function previousMonth(now = new Date()) {
  const year = now.getUTCFullYear();
  const month = now.getUTCMonth(); // 0-based — already "previous month" in 1-based terms
  if (month === 0) return {year: year - 1, month: 12};
  return {year, month};
}

function pad2(n) {
  return String(n).padStart(2, '0');
}

// Map analytics.type → relative path template + whether to unwrap `result`.
function fileSpec(type, year, month, repoRoot) {
  const stamp = `${year}-${pad2(month)}`;
  const map = {
    monthlyCoachSubscriptionDetails: {
      relPath: `reports/coaches/subscriptions/coach/${stamp}-users.json`,
      unwrap: false,
    },
    monthlyCoachingSubscriptionDetails: {
      relPath: `reports/coaches/subscriptions/coaching/${stamp}-users.json`,
      unwrap: false,
    },
    monthlyVideoSubscriptionDetails: {
      relPath: `reports/coaches/subscriptions/video/${stamp}-users.json`,
      unwrap: false,
    },
    monthlySession: {
      relPath: `reports/coaches/sessions/${stamp}-sessions.json`,
      unwrap: false,
    },
    monthlyCombined: {
      relPath: `reports/coaches/combined/${stamp}-combined.json`,
      unwrap: true,
    },
    payout: {
      relPath: `reports/coaches/payouts/${stamp}-payout.json`,
      unwrap: true,
    },
    monthlyCommunityPayout: {
      relPath: `reports/communities/payouts/${stamp}-community.json`,
      unwrap: true,
    },
  };
  const spec = map[type];
  if (!spec) return null;
  return {...spec, absPath: path.join(repoRoot, spec.relPath)};
}

const REQUIRED_TYPES = [
  'monthlyCoachSubscriptionDetails',
  'monthlyCoachingSubscriptionDetails',
  'monthlyVideoSubscriptionDetails',
  'monthlySession',
  'monthlyCombined',
  'payout',
  'monthlyCommunityPayout',
];

function ensureDir(filePath) {
  fs.mkdirSync(path.dirname(filePath), {recursive: true});
}

async function main() {
  const args = parseArgs(process.argv);

  const paymentServicePath =
    args['payment-service-path'] ||
    process.env.PAYMENT_SERVICE_PATH ||
    '/Users/jeromecurlier/Development/Aura/Services/PaymentService';

  const repoRoot =
    args['repo-root'] ||
    path.resolve(__dirname, '..', '..', '..', '..');

  // Resolve pg + dotenv from PaymentService so this repo doesn't need a node_modules.
  const dotenvPath = path.join(paymentServicePath, 'node_modules', 'dotenv');
  const pgPath = path.join(paymentServicePath, 'node_modules', 'pg');
  if (!fs.existsSync(dotenvPath) || !fs.existsSync(pgPath)) {
    console.error(
      `Could not find pg/dotenv under ${paymentServicePath}/node_modules. ` +
        `Run \`npm install\` in PaymentService, or pass --payment-service-path=<dir>.`,
    );
    process.exit(1);
  }
  // eslint-disable-next-line import/no-dynamic-require, global-require
  require(dotenvPath).config({path: path.join(paymentServicePath, '.env')});
  // eslint-disable-next-line import/no-dynamic-require, global-require
  const {Client} = require(pgPath);

  if (!process.env.POSTGRES_DATABASE_URI) {
    console.error(
      `POSTGRES_DATABASE_URI not set after loading ${paymentServicePath}/.env`,
    );
    process.exit(1);
  }

  const defaults = previousMonth();
  const year = parseInt(args.year || defaults.year, 10);
  const month = parseInt(args.month || defaults.month, 10);
  if (!Number.isInteger(year) || !Number.isInteger(month) || month < 1 || month > 12) {
    console.error(`Invalid year/month: year=${args.year} month=${args.month}`);
    process.exit(1);
  }

  console.log(`Gathering analytics for ${year}-${pad2(month)} (repo: ${repoRoot})`);

  const client = new Client({connectionString: process.env.POSTGRES_DATABASE_URI});
  await client.connect();

  let rows;
  try {
    const result = await client.query(
      `select "type", "year", "month", "data"
       from "analytics"
       where "day" is null and "year" = $1 and "month" = $2
       order by "type"`,
      [year, month],
    );
    rows = result.rows;
  } finally {
    await client.end();
  }

  console.log(`Fetched ${rows.length} analytics rows for ${year}-${pad2(month)}`);

  const seenTypes = new Set();
  let written = 0;
  let skippedExisting = 0;

  for (const row of rows) {
    const spec = fileSpec(row.type, year, month, repoRoot);
    if (!spec) continue; // Type not in our mapping (e.g. monthlyListenTotal); ignore.
    seenTypes.add(row.type);

    const payload = spec.unwrap
      ? row.data && row.data.result
        ? row.data.result
        : row.data
      : row.data;

    if (fs.existsSync(spec.absPath) && !args.overwrite) {
      console.log(`  skip (exists): ${spec.relPath}`);
      skippedExisting += 1;
      continue;
    }

    ensureDir(spec.absPath);
    fs.writeFileSync(spec.absPath, JSON.stringify(payload, null, 2));
    console.log(`  wrote: ${spec.relPath}`);
    written += 1;
  }

  const missing = REQUIRED_TYPES.filter((t) => !seenTypes.has(t));
  if (missing.length > 0) {
    console.warn(`Missing types not present in DB for ${year}-${pad2(month)}:`);
    for (const t of missing) console.warn(`  - ${t}`);
  }

  console.log(
    `Done writing JSON. wrote=${written} skipped=${skippedExisting} missing=${missing.length}`,
  );

  if (args['skip-csv']) {
    console.log('Skipping CSV step (--skip-csv).');
    return;
  }

  const jsontocsv = path.join(repoRoot, 'src', 'jsontocsv.js');
  if (!fs.existsSync(jsontocsv)) {
    console.warn(`Did not find ${jsontocsv} — skipping CSV step.`);
    return;
  }
  console.log(`Running: node ${path.relative(repoRoot, jsontocsv)}`);
  execSync(`node ${JSON.stringify(jsontocsv)}`, {stdio: 'inherit', cwd: repoRoot});
  console.log('CSV generation complete.');
}

main().catch((err) => {
  console.error(err && err.stack ? err.stack : err);
  process.exit(1);
});
