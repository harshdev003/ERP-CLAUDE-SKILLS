#!/usr/bin/env node
// Look up the owner and ASN of every IP the planner will log from a --probe-detail file (denied probes,
// and allowed reads that are neither the Firebase console nor our own sessions) and from monitor-firebase's
// REST-burst sources, for the Security Incident Log's "Source network" column. Console and internal
// addresses are never sent out.
//
// Usage: node lookup-networks.js <detail.jsonl> <networks.json> [--verdicts verdicts.json] [--rest-bursts file]
// (the same verdicts and bursts as the planner, so a known teammate's address isn't sent out either)
//
// Writes {ip: {asn, owner}} (mode 600: it holds IPs) via Team Cymru's whois service. An IP whose lookup
// fails is left out; the planner then names its network by the /16 alone.
'use strict';
const fs = require('fs');
const { execFileSync } = require('child_process');
const { loggedIps, readVerdicts, readBursts, parseArgs } = require('./plan-incident-log');

// "AS | IP | BGP Prefix | CC | Registry | Allocated | AS Name" -> {asn, owner}
function parseCymru(out) {
  const last = String(out).trim().split('\n').filter((l) => l.includes('|')).pop();
  if (!last) return null;
  const cols = last.split('|').map((c) => c.trim());
  if (!/^\d+$/.test(cols[0])) return null;
  const owner = (cols[6] || '').replace(/,\s*[A-Z]{2}$/, '').trim();
  return { asn: cols[0], owner };
}

function lookup(ip) {
  try {
    return parseCymru(execFileSync('whois', ['-h', 'whois.cymru.com', ` -v ${ip}`], { encoding: 'utf8', timeout: 15000 }));
  } catch {
    return null;
  }
}

function main() {
  const { pos: [detail, out], verdicts, bursts } = parseArgs(process.argv.slice(2));
  if (!detail || !out || verdicts === undefined || bursts === undefined) {
    console.error('usage: node lookup-networks.js <detail.jsonl> <networks.json> [--verdicts verdicts.json] [--rest-bursts file]');
    process.exit(2);
  }
  // A pass with a REST burst and no probe writes no probe detail.
  const lines = bursts && !fs.existsSync(detail) ? []
    : fs.readFileSync(detail, 'utf8').split('\n').filter((l) => l.trim()).map((l) => JSON.parse(l));
  const ips = loggedIps(lines, readVerdicts(verdicts), readBursts(bursts));
  const result = {};
  let missed = 0;
  for (const ip of ips) {
    const r = lookup(ip);
    if (r) result[ip] = r; else missed += 1;
  }
  const fd = fs.openSync(out, 'w', 0o600);
  fs.fchmodSync(fd, 0o600);
  fs.writeSync(fd, JSON.stringify(result, null, 1));
  fs.closeSync(fd);
  console.log(`networks: ${Object.keys(result).length} looked up, ${missed} not found (named by /16 only)`);
}

if (require.main === module) main();
module.exports = { parseCymru };
