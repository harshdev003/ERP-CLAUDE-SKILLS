#!/usr/bin/env node
/**
 * report.js — Prints a markdown table of every prompt defined in
 * IntelligenceService's `src/config.js` (`config.prompt.*`).
 *
 * Built for the ENGBACK-1043 per-prompt Response API rollout. Reads the live
 * config module via require() — no regex parsing — so the table always
 * reflects what's actually on disk.
 *
 * The IntelligenceService config destructures process.env at module top and
 * only enforces required keys inside `config.validate()`, which we never call.
 * Loading with no env set is safe — empty fields stay undefined and we never
 * touch postgres / redis / pubsub.
 *
 * Usage:
 *   node report.js
 *
 * Env:
 *   INTELLIGENCE_SERVICE_PATH   Optional. Path to a local IntelligenceService
 *                               checkout. Defaults to
 *                               ~/Development/Aura/Services/IntelligenceService.
 */

const fs = require('fs');
const os = require('os');
const path = require('path');

const DEFAULT_PATH = path.join(
  os.homedir(),
  'Development',
  'Aura',
  'Services',
  'IntelligenceService',
);

const COLUMNS = [
  {key: 'name', header: 'Prompter'},
  {key: 'api', header: 'API'},
  {key: 'model', header: 'Model'},
  {key: 'verbosity', header: 'verbosity'},
  {key: 'reasoningEffort', header: 'reasoningEffort'},
  {key: 'temperature', header: 'temperature'},
];

function resolveServicePath() {
  const fromEnv = process.env.INTELLIGENCE_SERVICE_PATH;
  const candidate = fromEnv && fromEnv.trim().length > 0 ? fromEnv : DEFAULT_PATH;
  const configPath = path.join(candidate, 'src', 'config.js');
  if (!fs.existsSync(configPath)) {
    console.error(
      `report-intelligence-prompters: could not find src/config.js under ${candidate}.\n` +
        'Set INTELLIGENCE_SERVICE_PATH to the absolute path of your IntelligenceService checkout, ' +
        'or run this from a machine with the repo cloned at the default location.',
    );
    process.exit(1);
  }
  return {servicePath: candidate, configPath};
}

function loadPromptConfig(configPath) {
  // require() resolves relative paths inside the module against its own
  // directory, so passing the absolute path of config.js works correctly.
  // eslint-disable-next-line global-require, import/no-dynamic-require
  const config = require(configPath);
  if (!config || !config.prompt || typeof config.prompt !== 'object') {
    throw new Error(
      `Loaded config from ${configPath} but it has no .prompt object. ` +
        'Has the config structure changed?',
    );
  }
  return config.prompt;
}

function classifyPrompt(entry) {
  // A prompt is "OpenAI" if it has an openaiAPI field OR its model id starts
  // with "gpt-". Anything else (e.g. gemini-*) is treated as non-OpenAI and
  // surfaced separately in the summary.
  if (typeof entry.openaiAPI === 'string' && entry.openaiAPI.length > 0) {
    return 'openai';
  }
  if (typeof entry.model === 'string' && entry.model.startsWith('gpt-')) {
    return 'openai-unset';
  }
  return 'non-openai';
}

function renderCell(value) {
  if (value === undefined || value === null || value === '') return '—';
  return String(value);
}

function buildRow(name, entry) {
  return {
    name: `\`${name}\``,
    api: entry.openaiAPI || '*(unset)*',
    model: renderCell(entry.model),
    verbosity: renderCell(entry.verbosity),
    reasoningEffort: renderCell(entry.reasoningEffort),
    temperature: renderCell(entry.temperature),
  };
}

function renderTable(rows) {
  const header = `| ${COLUMNS.map(c => c.header).join(' | ')} |`;
  const sep = `|${COLUMNS.map(() => '---').join('|')}|`;
  const body = rows
    .map(row => `| ${COLUMNS.map(c => row[c.key]).join(' | ')} |`)
    .join('\n');
  return [header, sep, body].join('\n');
}

function main() {
  const {servicePath, configPath} = resolveServicePath();
  const promptConfig = loadPromptConfig(configPath);

  const entries = Object.entries(promptConfig).sort(([a], [b]) =>
    a.localeCompare(b),
  );

  const openaiRows = [];
  const nonOpenAI = [];
  let onResponse = 0;
  let onChatCompletion = 0;
  let unset = 0;

  for (const [name, entry] of entries) {
    const kind = classifyPrompt(entry);
    if (kind === 'non-openai') {
      nonOpenAI.push({name, model: entry.model});
      continue;
    }
    openaiRows.push(buildRow(name, entry));
    if (entry.openaiAPI === 'responses') onResponse += 1;
    else if (entry.openaiAPI === 'chatCompletions') onChatCompletion += 1;
    else unset += 1;
  }

  const headerLine = `Source: ${path.relative(process.cwd(), configPath) || configPath} (${servicePath})`;

  const lines = [];
  lines.push(headerLine);
  lines.push('');
  lines.push(renderTable(openaiRows));
  lines.push('');
  lines.push('### Summary');
  lines.push(`- On Response API: ${onResponse}`);
  lines.push(`- On Chat Completions: ${onChatCompletion}`);
  lines.push(`- Unset (fall through to runtime default): ${unset}`);
  if (nonOpenAI.length > 0) {
    const nonOpenAILabel = nonOpenAI
      .map(p => `\`${p.name}\` — ${p.model}`)
      .join(', ');
    lines.push(`- Non-OpenAI: ${nonOpenAI.length} (${nonOpenAILabel})`);
  } else {
    lines.push('- Non-OpenAI: 0');
  }

  process.stdout.write(`${lines.join('\n')}\n`);
}

try {
  main();
} catch (err) {
  console.error('report-intelligence-prompters failed:');
  console.error(err.stack || err.message || err);
  process.exit(1);
}
