#!/usr/bin/env node
/**
 * inventory_prompters.js — Lists every IntelligenceService prompter with the
 * fields a prompt plan compares: model, reasoning effort, service tier, OpenAI
 * project, API, JSON output, and the static call sites that build it. Also lists
 * prompters deleted from git history, which are candidates to restore or strip.
 *
 * Reads `src/config.js` with require(). That module only requires local JSON
 * files and reads process.env lazily, so it loads in a checkout without
 * node_modules and never connects to anything.
 *
 * Usage:
 *   node inventory_prompters.js --repo /path/to/IntelligenceService
 *   node inventory_prompters.js --repo /path --key feed   # one prompter, full config
 *   node inventory_prompters.js --repo /path --deleted    # deleted prompters only
 *
 * Output: markdown on stdout.
 */

const {execFileSync} = require('child_process');
const fs = require('fs');
const path = require('path');

function parseArgs(argv) {
  const args = {deleted: false, key: null, repo: process.env.INTELLIGENCE_SERVICE_PATH};
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === '--repo') args.repo = argv[++i];
    else if (argv[i] === '--key') args.key = argv[++i];
    else if (argv[i] === '--deleted') args.deleted = true;
  }
  return args;
}

function listJsFiles(dir) {
  const out = [];
  for (const entry of fs.readdirSync(dir, {withFileTypes: true})) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...listJsFiles(full));
    else if (entry.name.endsWith('.js')) out.push(full);
  }
  return out;
}

function findCallers(repo) {
  const callers = {};
  const pattern = /prompterManager\.get\(\s*['"`]([A-Za-z0-9_-]+)['"`]/g;
  for (const file of listJsFiles(path.join(repo, 'src'))) {
    const text = fs.readFileSync(file, 'utf8');
    let match;
    while ((match = pattern.exec(text)) !== null) {
      const rel = path.relative(repo, file);
      (callers[match[1]] = callers[match[1]] || new Set()).add(rel);
    }
  }
  return callers;
}

function deletedPrompters(repo) {
  const log = execFileSync(
    'git',
    ['-C', repo, 'log', '--diff-filter=D', '--name-only', '--date=short',
      '--format=@@%h %ad %s', '--', 'src/prompters/*.txt'],
    {encoding: 'utf8'},
  );
  const rows = [];
  let commit = null;
  for (const line of log.split('\n')) {
    if (line.startsWith('@@')) commit = line.slice(2);
    else if (line.trim()) rows.push({commit, file: line.trim()});
  }
  return rows;
}

function cell(value) {
  if (value === undefined || value === null || value === '') return '—';
  return String(value);
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  if (!args.repo || !fs.existsSync(path.join(args.repo, 'src', 'config.js'))) {
    console.error('inventory_prompters: pass --repo (or set INTELLIGENCE_SERVICE_PATH) to an IntelligenceService checkout.');
    process.exit(2);
  }

  if (args.deleted) {
    const rows = deletedPrompters(args.repo);
    console.log('| Deleted prompt file | Deleting commit | Restore with |');
    console.log('|---|---|---|');
    for (const {commit, file} of rows) {
      const sha = commit.split(' ')[0];
      console.log(`| \`${file}\` | ${commit} | \`git show ${sha}^:${file}\` |`);
    }
    return;
  }

  const config = require(path.join(path.resolve(args.repo), 'src', 'config.js'));
  const prompts = config.prompt || {};

  if (args.key) {
    const entry = prompts[args.key];
    if (!entry) {
      console.error(`inventory_prompters: no prompt config named '${args.key}'.`);
      process.exit(1);
    }
    console.log('```json');
    console.log(JSON.stringify(entry, null, 2));
    console.log('```');
    return;
  }

  const callers = findCallers(args.repo);
  console.log('| Config key | Name | Model | Effort | Tier | Project | API | JSON | Static callers |');
  console.log('|---|---|---|---|---|---|---|---|---|');
  for (const key of Object.keys(prompts).sort()) {
    const p = prompts[key] || {};
    const sites = callers[key] || callers[p.key] || new Set();
    const callerCell = sites.size ? [...sites].map((s) => `\`${s}\``).join('<br>') : 'none found';
    console.log(
      `| \`${key}\` | ${cell(p.name)} | ${cell(p.model)} | ${cell(p.reasoningEffort)} | ` +
        `${cell(p.serviceTier || 'standard')} | ${cell(p.project)} | ${cell(p.openaiAPI)} | ` +
        `${p.jsonOutput ? 'yes' : '—'} | ${callerCell} |`,
    );
  }
  console.log('\n"none found" means no `prompterManager.get(\'<key>\'` literal. Conversation prompters ' +
    '(coach, companion, chat, quickProcessing…) are resolved from a variable; check before calling one dead.');
}

main();
