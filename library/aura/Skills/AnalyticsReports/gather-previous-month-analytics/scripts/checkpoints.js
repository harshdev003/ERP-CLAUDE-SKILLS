#!/usr/bin/env node
/**
 * checkpoints.js — print the numbers the compensation sheets must show once the
 * month's exports have been pasted in, so each sheet step is checked against the
 * export instead of eyeballed.
 *
 * Reads only files already on disk (written by gather.js); no database access.
 * See ../SKILL.md step 5.
 *
 *   node .claude/skills/gather-previous-month-analytics/scripts/checkpoints.js --year=2026 --month=09
 */

'use strict';

const fs = require('fs');
const path = require('path');

function parseArgs(argv) {
  const args = {};
  for (const raw of argv.slice(2)) {
    if (!raw.startsWith('--')) continue;
    const [key, value] = raw.slice(2).split('=');
    args[key] = value === undefined ? true : value;
  }
  return args;
}

const pad2 = (n) => String(n).padStart(2, '0');

function previousMonth({year, month}) {
  return month === 1 ? {year: year - 1, month: 12} : {year, month: month - 1};
}

// 1-based column number -> sheet letter (1 = A, 27 = AA).
function columnLetter(n) {
  let s = '';
  while (n > 0) {
    const m = (n - 1) % 26;
    s = String.fromCharCode(65 + m) + s;
    n = Math.floor((n - 1) / 26);
  }
  return s;
}

function load(file) {
  const data = JSON.parse(fs.readFileSync(file, 'utf8'));
  return Array.isArray(data) ? data : data.result;
}

const round2 = (x) => Math.round(x * 100) / 100;
const sum = (rows, key) => rows.reduce((a, r) => a + (Number(r[key]) || 0), 0);
const fmt = (x) => round2(x).toLocaleString('en-US');

function pct(cur, prev) {
  if (prev === 0) return cur === 0 ? '' : '#DIV/0! (LIVE, keep)';
  return `${(((cur - prev) / prev) * 100).toFixed(2)}%`;
}

function main() {
  const args = parseArgs(process.argv);
  const repoRoot = path.resolve(args['repo-root'] || path.join(__dirname, '..', '..', '..', '..'));
  const year = parseInt(args.year, 10);
  const month = parseInt(args.month, 10);
  if (!year || !month) {
    console.error('Usage: checkpoints.js --year=YYYY --month=MM');
    process.exit(2);
  }
  const prev = previousMonth({year, month});
  const stamp = `${year}-${pad2(month)}`;
  const prevStamp = `${prev.year}-${pad2(prev.month)}`;
  const file = (dir, s, kind) => path.join(repoRoot, 'reports', dir, `${s}-${kind}.json`);

  const combined = load(file('coaches/combined', stamp, 'combined'));
  const combinedPrev = load(file('coaches/combined', prevStamp, 'combined'));
  const payout = load(file('coaches/payouts', stamp, 'payout'));
  const community = load(file('communities/payouts', stamp, 'community'));
  const communityPrev = load(file('communities/payouts', prevStamp, 'community'));

  const n = combined.length;
  console.log(`Sheet checkpoints for ${stamp} (vs ${prevStamp})\n`);

  // --- compensation tab ---------------------------------------------------
  console.log(`compensation ${stamp}`);
  console.log(`  last coach row after deleting pasted row 2 : ${n + 1}   (${n} coaches, data from row 2)`);
  console.log(`  totals row (blank rows deleted)            : ${n + 2}`);
  console.log(`  percentage row                             : ${n + 3}`);
  console.log(`  previous month totals row (repoint % here) : ${combinedPrev.length + 2} on 'compensation ${prevStamp}'`);

  // The CSV is pasted at A1-equivalent columns; the `Data` separator inserted at F
  // shifts every column from F onward one to the right.
  // Column order is the CSV header's (what actually gets pasted), not the JSON key order.
  const csvFile = file('coaches/combined', stamp, 'combined').replace(/\.json$/, '.csv');
  const keys = fs.readFileSync(csvFile, 'utf8').split('\n', 1)[0].trim().split(',');
  console.log(`\n  column totals AFTER the Data separator is inserted at F:`);
  console.log(`  ${'col'.padEnd(4)} ${'field'.padEnd(38)} ${'total'.padStart(12)}  ${'prev'.padStart(12)}  % row`);
  keys.forEach((key, i) => {
    const c = sum(combined, key);
    const p = sum(combinedPrev, key);
    if (typeof combined[0][key] === 'string' && c === 0 && p === 0) return; // id / name / email columns
    if (c === 0 && p === 0) return;
    const col = columnLetter(i + 1 >= 6 ? i + 2 : i + 1);
    console.log(`  ${col.padEnd(4)} ${key.padEnd(38)} ${fmt(c).padStart(12)}  ${fmt(p).padStart(12)}  ${pct(c, p)}`);
  });
  console.log(`  (fields that are 0 in both months are omitted: their % cell is #DIV/0! and can be cleared)`);

  const cur = new Map(combined.map((r) => [r.coachId, r]));
  const old = new Map(combinedPrev.map((r) => [r.coachId, r]));
  const dropped = [...old.keys()].filter((k) => !cur.has(k));
  const added = [...cur.keys()].filter((k) => !old.has(k));
  console.log(`\n  roster ${old.size} -> ${cur.size}: dropped ${dropped.length} ${JSON.stringify(dropped)}, added ${added.length} ${JSON.stringify(added)}`);
  console.log(`  a dropped coach's rollover disappears from BA; the BA total vs last month's BC total must differ by exactly their carried rollover`);

  // Rollover is computed in the sheet (the exports carry none), so the totals
  // come from the sheet: print the exact ranges, and each dropped coach's row
  // on last month's tab, assuming the tab kept the export's row order.
  const prevTab = `'compensation ${prevStamp}'`;
  const prevLast = combinedPrev.length + 1;
  const droppedRows = combinedPrev
    .map((r, i) => ({coachId: r.coachId, row: i + 2}))
    .filter((r) => !cur.has(r.coachId));
  console.log(`\n  rollover check (the exports carry no rollover, so these totals come from the sheet):`);
  console.log(`  sum last month's rollover out : ${prevTab}!BC2:BC${prevLast}   (not the whole column: the totals row is below)`);
  console.log(`  sum this month's rollover in  : BA2:BA${n + 1}`);
  droppedRows.forEach(({coachId, row}) => {
    console.log(`  dropped ${coachId}: carried rollover = ${prevTab}!BC${row}   (check column A is that id)`);
  });
  const droppedSum = droppedRows.length
    ? `-SUM(${droppedRows.map(({row}) => `${prevTab}!BC${row}`).join(',')})`
    : '';
  console.log(`  expected BA total             : =SUM(${prevTab}!BC2:BC${prevLast})${droppedSum}`);
  console.log(`  it must equal SUM(BA2:BA${n + 1}) to the cent; a gap means a coach's rollover was not carried`);

  // --- payout ground truth + the BB identity --------------------------------
  const K = 'totalCompensation';
  const total = sum(payout, K);
  const paid = payout.filter((r) => Number(r[K]) >= 10);
  const under = payout.filter((r) => Number(r[K]) > 0 && Number(r[K]) < 10);
  console.log(`\npayout ${stamp}`);
  console.log(`  last coach row after deleting pasted row 2 : ${payout.length + 1}`);
  console.log(`  export total (sum of AG)                   : $${fmt(total)}`);
  console.log(`  coaches >= $10 / under $10 (export only)   : ${paid.length} = $${fmt(sum(paid, K))} / ${under.length} = $${fmt(sum(under, K))}`);
  console.log(`\n  IDENTITY on 'compensation ${stamp}':  sum(BB) = $${fmt(total)} + sum(BA) + sum(adjustments)`);
  console.log(`  read sum(BA) (rollover in) off the sheet; sum(BB) must match within a few cents of rounding`);

  // --- community -------------------------------------------------------------
  console.log(`\ncommunity ${stamp}`);
  console.log(`  last community row after deleting row 2    : ${community.length + 1}   (${communityPrev.length} last month)`);
  for (const key of ['total-gross', 'total-payout']) {
    console.log(`  ${key.padEnd(43)}: $${fmt(sum(community, key))}   (prev $${fmt(sum(communityPrev, key))})`);
  }
  const cIds = new Set(community.map((r) => r.coachId));
  const pIds = new Set(communityPrev.map((r) => r.coachId));
  console.log(`  communities gone: ${[...pIds].filter((x) => !cIds.has(x)).length}, added: ${[...cIds].filter((x) => !pIds.has(x)).length}`);
}

main();
