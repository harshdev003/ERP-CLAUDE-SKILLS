#!/usr/bin/env node
// Notion REST access to the Security Incident Log (NOTION_TOKEN).
//
// Usage: node incident-log.js schema               check the live columns and select options
//        node incident-log.js rows <rows.json>     save every Firebase RTDB row (mode 600)
//        node incident-log.js apply <plan.json> [--apply]
//                                                  print the writes; with --apply, make them
//
// Exit 2 when the schema lacks a column or option the planner writes, or on any API error. It never
// changes the schema: eng:flag-suspicious-traffic and this skill share it.
'use strict';
const fs = require('fs');

const DATA_SOURCE = '40175e23-af20-8197-8d7e-000b96ac48bf';
const API = 'https://api.notion.com/v1';
const REQUIRED = {
  Incident: 'title', 'Detected (UTC)': 'date', 'Ended (UTC)': 'date', Category: 'select', System: 'select',
  Outcome: 'select', Status: 'select', 'IP addresses': 'rich_text', 'User agent': 'rich_text',
  'Source network': 'rich_text', 'What they did': 'rich_text', 'Route / path': 'rich_text',
  'Found by': 'rich_text', Actor: 'rich_text', Notes: 'rich_text', Requests: 'number', 'Max IPs': 'number',
  Severity: 'select', 'Group key': 'rich_text',
};
// "Other attack" is renamed in place to OTHER, spelled exactly as the live option (US "z"; Jerome,
// 2026-11-10). Either name passes the check, and the old one is written while the live option still has
// it: a write naming a missing option makes Notion create it, so a new name the list lacks would leave
// two options.
const OTHER = 'Other unrecognized access';
const OLD_OTHER = 'Other attack';
const OPTIONS = {
  Category: ['Forced browsing', 'Vulnerability scanner', 'Scraping / abuse'],
  System: ['Firebase RTDB'],
  Outcome: ['Denied by rules', 'Data accessed', 'Partial impact', 'Allowed (no data)', 'Allowed (public data)'],
  Status: ['Monitoring', 'Open'],
  Severity: ['Info', 'Low', 'Medium', 'High'],
};

async function notion(method, path, body) {
  if (!process.env.NOTION_TOKEN) throw new Error('NOTION_TOKEN is not set');
  for (let attempt = 0; ; attempt += 1) {
    const r = await fetch(`${API}${path}`, {
      method,
      headers: { Authorization: `Bearer ${process.env.NOTION_TOKEN}`, 'Notion-Version': '2025-09-03', 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (r.status === 429 && attempt < 5) {
      await new Promise((res) => setTimeout(res, 1000 * Number(r.headers.get('retry-after') || 2 ** attempt)));
      continue;
    }
    const j = await r.json();
    if (!r.ok) throw new Error(`${method} ${path} -> ${r.status}: ${JSON.stringify(j).slice(0, 300)}`);
    return j;
  }
}

// Staff is written only once Librarian has added the column with that option: a write naming a missing
// column fails, and one naming a missing option makes Notion create it.
const STAFF_VALUES = ['Staff', 'Not staff'];
function fitStaff(ds, properties) {
  const p = ds.properties.Staff;
  const name = properties.Staff && properties.Staff.select && properties.Staff.select.name;
  if (!properties.Staff) return false;
  if (p && p.type === 'select' && STAFF_VALUES.includes(name) && optionNames(ds, 'Staff').has(name)) return false;
  delete properties.Staff;
  return true;
}
const optionNames = (ds, name) => new Set(((ds.properties[name] || {}).select || { options: [] }).options.map((o) => o.name));
const liveCategory = (ds, name) => (name === OTHER && !optionNames(ds, 'Category').has(OTHER) && optionNames(ds, 'Category').has(OLD_OTHER) ? OLD_OTHER : name);

function checkSchema(ds) {
  const problems = [];
  for (const [name, type] of Object.entries(REQUIRED)) {
    const p = ds.properties[name];
    if (!p) problems.push(`missing column "${name}"`);
    else if (p.type !== type) problems.push(`"${name}" is ${p.type}, expected ${type}`);
  }
  for (const [name, opts] of Object.entries(OPTIONS)) {
    const have = optionNames(ds, name);
    for (const o of opts) if (!have.has(o)) problems.push(`"${name}" has no option "${o}"`);
  }
  const cats = optionNames(ds, 'Category');
  if (!cats.has(OTHER) && !cats.has(OLD_OTHER)) problems.push(`"Category" has no option "${OTHER}"`);
  return problems;
}

async function rows() {
  const results = [];
  let cursor;
  do {
    const q = await notion('POST', `/data_sources/${DATA_SOURCE}/query`, {
      filter: { property: 'System', select: { equals: 'Firebase RTDB' } }, page_size: 100, ...(cursor ? { start_cursor: cursor } : {}),
    });
    results.push(...q.results);
    cursor = q.has_more ? q.next_cursor : null;
  } while (cursor);
  return { results, has_more: false };
}

async function main() {
  const [cmd, file, flag] = process.argv.slice(2);
  const schema = await notion('GET', `/data_sources/${DATA_SOURCE}`);
  const problems = checkSchema(schema);
  if (problems.length) throw new Error(`schema check failed: ${problems.join('; ')}`);
  if (cmd === 'schema') { console.log('schema OK'); return; }
  if (cmd === 'rows' && file) {
    const fd = fs.openSync(file, 'w', 0o600);
    fs.fchmodSync(fd, 0o600);
    const r = await rows();
    fs.writeSync(fd, JSON.stringify(r));
    fs.closeSync(fd);
    console.log(`${r.results.length} Firebase RTDB row(s) saved`);
    return;
  }
  if (cmd === 'apply' && file) {
    const plan = JSON.parse(fs.readFileSync(file, 'utf8'));
    const write = flag === '--apply';
    let skipped = 0;
    for (const c of plan.creates) {
      console.log(`create ${c.key} (${(c.sweeps || []).length} sweep(s))`);
      const cat = c.properties.Category;
      if (cat) cat.select.name = liveCategory(schema, cat.select.name);
      if (fitStaff(schema, c.properties)) skipped += 1;
      if (write) await notion('POST', '/pages', { parent: { type: 'data_source_id', data_source_id: DATA_SOURCE }, properties: c.properties });
    }
    for (const u of plan.updates) {
      if (fitStaff(schema, u.properties)) skipped += 1;
      if (!Object.keys(u.properties).length) continue;
      console.log(`update ${u.page_id} (${(u.sweeps || []).length} sweep(s))`);
      if (write) await notion('PATCH', `/pages/${u.page_id}`, { properties: u.properties });
    }
    if (skipped) console.log(`Staff left out of ${skipped} write(s): the log has no Staff column with that option yet`);
    if (!write) console.log('(dry run: pass --apply to write)');
    return;
  }
  console.error('usage: node incident-log.js schema | rows <rows.json> | apply <plan.json> [--apply]');
  process.exit(2);
}

if (require.main === module) {
  main().catch((e) => { console.error(`incident-log: ${e.message}`); process.exit(2); });
}
module.exports = { checkSchema, liveCategory, fitStaff, STAFF_VALUES, REQUIRED, OPTIONS, OTHER, OLD_OTHER };
