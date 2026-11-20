#!/usr/bin/env node
/**
 * Pre-flight for eng:setup-monthly-tasks.
 *
 * Answers one question: does the PaymentService `analytics` table actually hold the
 * monthly rows for the month being closed? There is no backfill path, so this has to be
 * checked before a pass is scaffolded. See ../references/traps.md trap 2.
 *
 * Read-only. Never prints the connection string.
 *
 * Usage:
 *   node check_analytics_rows.js [--year=2026] [--month=8] [--payment-service-path=<dir>]
 *
 * The PaymentService checkout is resolved in this order: --payment-service-path, then
 * $PAYMENT_SERVICE_PATH, then a short list of conventional locations under $HOME. A
 * candidate only counts if it has both .env and node_modules/pg.
 *
 * Months are 1-12 (jan=1, dec=12) — see ../references/traps.md trap 1.
 * Defaults to the previous calendar month.
 *
 * Exit 0 = every expected type present. Exit 1 = something missing, or a connection error.
 */

'use strict';

const fs = require('fs');
const path = require('path');

// Conventional locations, relative to $HOME. No machine-specific absolute paths.
const CANDIDATE_SUFFIXES = [
  'Development/Aura/Services/PaymentService',
  'Aura/Services/PaymentService',
  'PaymentService',
  'code/PaymentService',
  'src/PaymentService',
  'repos/PaymentService',
];

/**
 * Resolve the PaymentService checkout. A candidate must carry both `.env` and
 * `node_modules/pg` to count, so a same-named but unrelated directory is skipped.
 *
 * An EXPLICIT path (flag or env var) is never fallen back from: if the caller named a
 * directory and it does not validate, that is an error. Falling through to a $HOME
 * candidate could silently point the check at a different environment than the one asked
 * for. Only the implicit $HOME search is allowed to try several.
 *
 * Returns {dir} or {dir: null, tried: [...], explicit: bool}.
 */
function isCheckout(dir) {
  return fs.existsSync(path.join(dir, '.env')) && fs.existsSync(path.join(dir, 'node_modules', 'pg'));
}

function resolvePaymentServicePath(flag) {
  const named = flag || process.env.PAYMENT_SERVICE_PATH;
  if (named) {
    return isCheckout(named)
      ? {dir: named, tried: [named], explicit: true}
      : {dir: null, tried: [named], explicit: true};
  }

  const home = process.env.HOME || process.env.USERPROFILE || '';
  const tried = home ? CANDIDATE_SUFFIXES.map((s) => path.join(home, s)) : [];
  for (const dir of tried) {
    if (isCheckout(dir)) return {dir, tried, explicit: false};
  }
  return {dir: null, tried, explicit: false};
}

// The 18 monthly types that are actually emitted, plus the aggregate payout row.
const EXPECTED = [
  'monthlyCoachingSubscription',
  'monthlyCoachingSubscriptionDetails',
  'monthlyCoachSubscription',
  'monthlyCoachSubscriptionDetails',
  'monthlyDonation',
  'monthlyListenBreakdown',
  'monthlyListenBreakdownNoCap',
  'monthlyListenTotal',
  'monthlyListenTotalNoCap',
  'monthlyReferral',
  'monthlySession',
  'monthlySessionDetails',
  'monthlyCommunityPayout',
  'monthlyCommunityPayoutDetails',
  'monthlyUpload',
  'monthlyUploadDetails',
  'monthlyCombinedNoCap',
  'monthlyCombined',
];

// Declared in config but retired — never emitted, and their absence is not a problem.
const RETIRED = ['monthlyVideoSubscription', 'monthlyVideoSubscriptionDetails'];

const PAYOUT = 'payout';

function parseArgs(argv) {
  const out = {};
  for (const arg of argv.slice(2)) {
    const match = /^--([^=]+)=(.*)$/.exec(arg);
    if (match) out[match[1]] = match[2];
  }
  return out;
}

function previousMonth() {
  const now = new Date();
  // Month 0 of the current year rolls back to December of the prior year correctly.
  const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - 1, 1));
  return {year: d.getUTCFullYear(), month: d.getUTCMonth() + 1};
}

async function main() {
  const args = parseArgs(process.argv);

  const {dir: psPath, tried, explicit} = resolvePaymentServicePath(args['payment-service-path']);
  if (!psPath) {
    if (explicit) {
      console.error(`The PaymentService path you named is not a usable checkout: ${tried[0]}`);
      console.error('It needs both .env and node_modules/pg. Not falling back to a guess.');
    } else {
      console.error('Could not find a PaymentService checkout with both .env and node_modules/pg.');
      console.error('Pass --payment-service-path=<dir> or set PAYMENT_SERVICE_PATH.');
      console.error('Tried:');
      tried.forEach((d) => console.error(`  - ${d}`));
    }
    process.exit(1);
  }

  const fallback = previousMonth();
  const year = Number(args.year || fallback.year);
  const month = Number(args.month || fallback.month);

  if (!Number.isInteger(year) || !Number.isInteger(month) || month < 1 || month > 12) {
    console.error(`Bad --year/--month: got year=${args.year} month=${args.month}. Months are 1-12.`);
    process.exit(1);
  }

  let Client;
  try {
    // override: true is deliberate. Without it an ambient POSTGRES_DATABASE_URI wins over
    // the resolved checkout's .env, and the pre-flight would silently report CLEAN or NOT
    // CLEAN for a different database than the one asked about. Having resolved a specific
    // checkout, that checkout's credentials are the authority.
    require(`${psPath}/node_modules/dotenv`).config({
      path: `${psPath}/.env`,
      quiet: true,
      override: true,
    });
    ({Client} = require(`${psPath}/node_modules/pg`));
  } catch (error) {
    console.error(`Could not load pg/dotenv from ${psPath}. Pass --payment-service-path or set PAYMENT_SERVICE_PATH.`);
    console.error(error.message);
    process.exit(1);
  }

  if (!process.env.POSTGRES_DATABASE_URI) {
    console.error(`POSTGRES_DATABASE_URI not found in ${psPath}/.env`);
    process.exit(1);
  }

  // No ssl option: the server rejects ssl:{rejectUnauthorized:false}.
  const client = new Client({connectionString: process.env.POSTGRES_DATABASE_URI});

  let rows;
  try {
    await client.connect();
    const result = await client.query(
      `select "type", count(*)::int n
         from "analytics"
        where "day" is null and "year" = $1 and "month" = $2
        group by 1`,
      [year, month]
    );
    rows = result.rows;
  } catch (error) {
    console.error(`Query failed: ${error.message}`);
    process.exit(1);
  } finally {
    await client.end().catch(() => {});
  }

  const present = new Set(rows.map((r) => r.type));
  const missing = EXPECTED.filter((t) => !present.has(t));
  const retiredPresent = RETIRED.filter((t) => present.has(t));
  const hasPayout = present.has(PAYOUT);

  const label = `${year}-${String(month).padStart(2, '0')}`;
  console.log(`Analytics pre-flight for ${label} (month=${month}, 1-12 convention)\n`);
  console.log(`  rows found          : ${rows.length}`);
  console.log(`  expected types      : ${EXPECTED.length - missing.length}/${EXPECTED.length} present`);
  console.log(`  payout row          : ${hasPayout ? 'present' : 'MISSING'}`);
  console.log(`  retired types       : ${retiredPresent.length === 0 ? 'absent (expected)' : `PRESENT (unexpected): ${retiredPresent.join(', ')}`}`);

  if (missing.length) {
    console.log(`\n  MISSING (${missing.length}):`);
    missing.forEach((t) => console.log(`    - ${t}`));
  }

  const clean = missing.length === 0 && hasPayout;
  if (clean) {
    console.log(`\nCLEAN — ${label} is complete. Safe to scaffold the pass.`);
    process.exit(0);
  }

  console.log(
    `\nNOT CLEAN — do not scaffold yet.\n` +
      `The monthly types are only queued on the 1st of the month and each SQL file hardcodes\n` +
      `its own window, so re-running the job later produces a DIFFERENT month rather than\n` +
      `backfilling this one. Report this to the user before creating any cards.\n` +
      `See ../references/traps.md trap 2.`
  );
  process.exit(1);
}

main().catch((error) => {
  console.error(`Unexpected failure: ${error.message}`);
  process.exit(1);
});
