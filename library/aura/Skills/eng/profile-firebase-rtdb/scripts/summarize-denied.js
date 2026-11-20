// Denied-operation summary for RTDB profiler logs. Copied from AuraHealth/FirebaseExporter src/profiler.js
// (FirebaseExporter#1). Reads ./profiler/*firebase* in the current directory; prints no IPs or keys.
// Usage: (cd <run-dir> && node summarize-denied.js)
const fs = require('fs');
const readline = require('readline');

const FOLDER = 'profiler';

const files = fs.readdirSync(`./${FOLDER}`).filter(fn => fn.includes('firebase'));
const events = [];
const summary = {};
const denied = {};
let deniedCount = 0;
let checkedCount = 0;

function addSummary(statement) {
  if (summary[statement]) {
    summary[statement]++;
  } else {
    summary[statement] = 1;
  }
}

// Some keys are base64(email): /emailUser/{key}, familyPlanInvites/{uid}/{key}.
function isEncodedEmail(segment) {
  if (!/^[A-Za-z0-9+/]+={0,2}$/.test(segment)) {
    return false;
  }
  return /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(Buffer.from(segment, 'base64').toString('utf8'));
}

// Keys in a path are ids or emails. Collapse them to $id, judged by their
// shape rather than their length, so field names (fpMembersListenedShareDisabled)
// stay readable and the summary never prints an id or an email address.
// Top-level segments are node names from rules.json, never keys.
function isRecordKey(segment, parent, index) {
  if (index === 0) {
    return false;
  }
  return parent === 'emailUser'
    || segment.startsWith('-') // push id
    || /^\d+$/.test(segment)
    || /[@.=]/.test(segment)
    || (segment.length >= 16 && /\d/.test(segment)) // uid, device id, UUID
    || isEncodedEmail(segment);
}

function pathPattern(path) {
  if (!Array.isArray(path) || path.length === 0) {
    return '/';
  }
  return '/' + path.map((segment, i) => (isRecordKey(String(segment), path[i - 1], i) ? '$id' : segment)).join('/');
}

// The user agent identifies the app and platform. The IP address in
// data.client is deliberately left out.
function clientLabel(client) {
  const agent = client && client.userAgent;
  if (!agent) {
    return 'unknown client';
  }
  const product = (agent.userAgentString || '').split(' ')[0] || 'unknown';
  return `${agent.platform || '?'}/${agent.os || '?'} ${product}`;
}

// The profiler sets `allowed` on reads, writes, updates, transactions,
// listens and onDisconnect writes; false means the security rules blocked it.
function addDenied(data) {
  if (typeof data.allowed !== 'boolean') {
    return;
  }
  checkedCount++;
  if (data.allowed) {
    return;
  }
  deniedCount++;
  const statement = `${data.name} ${pathPattern(data.path)} (${clientLabel(data.client)})`;
  denied[statement] = (denied[statement] || 0) + 1;
}

function processLine(line) {
  if (line && line.length > 0 && line.includes('{') && line.includes('}')) {
    // clean up weird caracters [?25l[?25h
    const start = line.indexOf('{');
    const end = line.lastIndexOf('}') + 1;
    // {"path":["channels","-Ko8hcAaq0VhreIU2TH7","new"],"querySet":[{"orderBy":"$key"}],"millis":0,"pendingTime":1,"bytes":5,"client":{"remoteAddress":{"address":"203.0.113.10"},"userAgent":{"browser":"unknown","os":"os_x","platform":"desktop","userAgentString":"Aura/2 CFNetwork/978.0.7 Darwin/18.7.0","version":"unknown"}},"allowed":true,"name":"rest-read","timestamp":1568916335143}
    const data = JSON.parse(line.substring(start, end));
    if (data) {
      addDenied(data);
    }
    if (data && data.millis > 1000) {
      // events.push(data);
      const statement = `slow query ${data.millis} for path ${pathPattern(data.path)}`
      addSummary(statement);
      console.log(statement);
    }
    if (data && data.pendingTime > 2000) {
      // events.push(data);
      // console.log(line);
    }
    if (data && (data.millis > 2000 && data.pendingTime > 2000)) {
      console.log(new Date(data.timestamp - data.millis - data.pendingTime ));
    } else if (data && (data.pendingTime > 2000)) {
      // console.log(new Date(data.timestamp - data.pendingTime ));
    }
    if (data && data.timestamp > 1585872420000 && data.timestamp < 1585872440000) {
      //console.log(line);
    }
    if (data && data.bytes > 800000) {
      const statement = `large donwload ${data.bytes} for path ${pathPattern(data.path)}`;
      addSummary(statement);
      //console.log(statement);
    }
    
    if (data && data.name === 'realtime-write') {
      // console.log(line);
    }
  } 
}

files.forEach(file => {
  const lineReader = readline.createInterface({
    input : fs.createReadStream(`./${FOLDER}/${file}`),
    output: process.stdout,
    terminal: false
  })
  lineReader.on('line', processLine);
});

process.on('exit', function () {
  console.log('Summary');
  Object.keys(summary).forEach(statement => {
    const occurences = summary[statement];
    if (occurences > 10) {
      console.log(`${statement} : ${occurences} occurences`);
    }
  });
  console.log(`\nPermission denied: ${deniedCount} of ${checkedCount} rule-checked operations`);
  Object.keys(denied)
    .sort((a, b) => denied[b] - denied[a])
    .forEach(statement => {
      console.log(`${denied[statement]}\t${statement}`);
    });
});
