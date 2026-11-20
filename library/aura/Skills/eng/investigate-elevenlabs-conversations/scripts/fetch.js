#!/usr/bin/env node
/**
 * fetch.js — Fetches ElevenLabs conversation data and prints raw JSON to stdout.
 *
 * Used by the /11labs-conversations Claude command. Claude does all analysis.
 *
 * Usage:
 *   node fetch.js monitor
 *   node fetch.js review-errors [--hours N] [--days N]
 *
 * Environment variables:
 *   ELEVENLABS_API_KEY   — ElevenLabs API key (required)
 *   ELEVENLABS_AGENT_ID  — Filter by agent ID (optional)
 */

const API_KEY = process.env.ELEVENLABS_API_KEY || 'YOUR_ELEVENLABS_API_KEY';
const BASE_URL = 'https://api.elevenlabs.io/v1';
const PAGE_SIZE = 100;

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

async function apiFetch(path, queryParams = {}) {
  const url = new URL(`${BASE_URL}${path}`);
  for (const [key, value] of Object.entries(queryParams)) {
    if (value !== undefined) url.searchParams.set(key, String(value));
  }
  const response = await fetch(url.toString(), {headers: {'xi-api-key': API_KEY}});
  if (!response.ok) {
    const body = await response.text().catch(() => '');
    throw new Error(`ElevenLabs API ${response.status}: ${body}`);
  }
  return response.json();
}

async function fetchAllConversations({agentId, callSuccessful, endTime, startTime} = {}) {
  const all = [];
  let cursor = null;
  do {
    const params = {page_size: PAGE_SIZE};
    if (agentId) params.agent_id = agentId;
    if (startTime) params.call_start_after_unix = startTime;
    if (endTime) params.call_start_before_unix = endTime;
    if (callSuccessful) params.call_successful = callSuccessful;
    if (cursor) params.cursor = cursor;
    const data = await apiFetch('/convai/conversations', params);
    all.push(...(data.conversations || []));
    cursor = data.next_cursor || null;
  } while (cursor);
  return all;
}

async function fetchConversationDetail(conversationId) {
  return apiFetch(`/convai/conversations/${conversationId}`);
}

// ---------------------------------------------------------------------------
// Fetch modes
// ---------------------------------------------------------------------------

async function fetchMonitor(agentId) {
  const now = Math.floor(Date.now() / 1000);
  const startTime = now - 10 * 60; // last 10 minutes

  const conversations = await fetchAllConversations({agentId, endTime: now, startTime});
  const failed = conversations.filter((c) => c.call_successful === 'failure');

  const failedDetails = await Promise.allSettled(
    failed.map((c) => fetchConversationDetail(c.conversation_id)),
  ).then((results) =>
    results.map((r, i) => ({
      conversationId: failed[i].conversation_id,
      detail: r.status === 'fulfilled' ? r.value : null,
      fetchError: r.status === 'rejected' ? r.reason?.message : null,
    })),
  );

  return {
    mode: 'monitor',
    fetchedAt: new Date().toISOString(),
    period: {
      description: 'Last 10 minutes',
      endTime: now,
      startTime,
    },
    agentFilter: agentId || null,
    summary: {
      failed: failed.length,
      succeeded: conversations.filter((c) => c.call_successful === 'success').length,
      total: conversations.length,
      unknown: conversations.filter((c) => c.call_successful === 'unknown').length,
    },
    conversations,
    failedDetails,
  };
}

async function fetchReviewErrors({agentId, days, hours}) {
  let lookbackSeconds;
  let periodDescription;
  if (hours) {
    lookbackSeconds = hours * 3600;
    periodDescription = `Last ${hours} hours`;
  } else if (days) {
    lookbackSeconds = days * 86400;
    periodDescription = `Last ${days} days`;
  } else {
    lookbackSeconds = 48 * 3600;
    periodDescription = 'Last 48 hours';
  }

  const now = Math.floor(Date.now() / 1000);
  const startTime = now - lookbackSeconds;

  const conversations = await fetchAllConversations({agentId, endTime: now, startTime});
  const failed = conversations.filter((c) => c.call_successful === 'failure');

  const failedDetails = await Promise.allSettled(
    failed.map((c) => fetchConversationDetail(c.conversation_id)),
  ).then((results) =>
    results.map((r, i) => ({
      conversationId: failed[i].conversation_id,
      detail: r.status === 'fulfilled' ? r.value : null,
      fetchError: r.status === 'rejected' ? r.reason?.message : null,
    })),
  );

  return {
    mode: 'review-errors',
    fetchedAt: new Date().toISOString(),
    period: {
      description: periodDescription,
      endTime: now,
      startTime,
    },
    agentFilter: agentId || null,
    summary: {
      failed: failed.length,
      succeeded: conversations.filter((c) => c.call_successful === 'success').length,
      total: conversations.length,
      unknown: conversations.filter((c) => c.call_successful === 'unknown').length,
    },
    conversations,
    failedDetails,
  };
}

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------

function parseArgs(argv) {
  const [, , command, ...rest] = argv;
  const options = {};
  for (let i = 0; i < rest.length; i++) {
    if (rest[i] === '--hours' && rest[i + 1]) options.hours = parseInt(rest[i + 1], 10);
    if (rest[i] === '--days' && rest[i + 1]) options.days = parseInt(rest[i + 1], 10);
    if (rest[i] === '--agent-id' && rest[i + 1]) options.agentId = rest[i + 1];
  }
  return {command, options};
}

async function main() {
  const {command, options} = parseArgs(process.argv);

  // Agent ID: CLI arg takes precedence over env var
  const agentId = options.agentId || process.env.ELEVENLABS_AGENT_ID || undefined;

  let result;
  if (command === 'monitor') {
    result = await fetchMonitor(agentId);
  } else if (command === 'review-errors') {
    result = await fetchReviewErrors({...options, agentId});
  } else {
    process.stderr.write(`Usage: node fetch.js <monitor|review-errors> [--hours N] [--days N]\n`);
    process.exit(1);
  }

  process.stdout.write(JSON.stringify(result, null, 2));
}

main().catch((err) => {
  process.stderr.write(`Fatal: ${err.message}\n`);
  process.exit(1);
});
