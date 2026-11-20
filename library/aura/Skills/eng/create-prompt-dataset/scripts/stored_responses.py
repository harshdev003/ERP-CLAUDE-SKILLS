#!/usr/bin/env python3
"""Fetch stored OpenAI Responses into a disk cache, and probe their context blocks.

Library: import it for `response()`, `system_text()`, `section()` and `headings()` when
writing a feature's case builder. Every fetch is cached as `{id}.{input|output}.json`.
`response()` returns None only when OpenAI answers 404 (expired or never stored) and raises
`StoredResponseError` for anything else (missing key, wrong project, retries exhausted), so a
caller can skip one row and carry on.

CLI:
  stored_responses.py probe --project insights --ledger ic.json --sample 50 --cache DIR [--after "## Input Data"]
      For a random sample of the ledger's Responses calls, prints per heading: how often it
      appears, how often its body is empty, and the bold field labels it renders. --after
      starts at the data section so instruction headings aren't counted. Prints headings and
      counts only, never block content.
  stored_responses.py fetch --project feed --ledger feed.json --cache DIR [--part input|output|both]
      Fills the cache for every Responses call in the ledger file. Prints counts only.

Both commands read only ledger rows whose openaiAPI is `responses`; Chat Completions calls are
read through their own endpoints and are reported as skipped. The key for a project is read
from OPENAI_{PROJECT}_API_KEY (e.g. OPENAI_FEED_API_KEY).
"""
import argparse
import collections
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

API = 'https://api.openai.com/v1/responses'
EMPTY = re.compile(r'^(empty\.?|none\.?|no .* found\.?)$', re.I)
LABEL = re.compile(r'^\s*-\s+\*\*([A-Za-z][A-Za-z ]{0,30}):\*\*', re.M)
HEADING = re.compile(r'^(#{2,5}) (.+?)\s*$', re.M)
RETRYABLE = {408, 409, 429, 500, 502, 503, 504}
PAGE_LIMIT = 100  # input_items page size (the API maximum)


class StoredResponseError(Exception):
    """A stored response could not be read for a reason other than expiry."""


def key_for(project):
    name = f'OPENAI_{project.upper()}_API_KEY'
    key = os.environ.get(name)
    if not key:
        raise StoredResponseError(f'{name} is not set')
    return key


def _get(url, key):
    req = urllib.request.Request(url, headers={'Authorization': f'Bearer {key}'})
    last = None
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None  # expired or never stored
            if e.code not in RETRYABLE:
                raise StoredResponseError(f'HTTP {e.code} (wrong project key for this response?)') from e
            last = f'HTTP {e.code}'
        except (urllib.error.URLError, TimeoutError) as e:
            last = str(e)
        time.sleep(2 * (attempt + 1))
    raise StoredResponseError(f'gave up after 6 attempts: {last}')


def _get_input_items(rid, key):
    """Every input item of a response, oldest first, following `has_more` / `after` pages."""
    items, after = [], None
    while True:
        url = f'{API}/{rid}/input_items?limit={PAGE_LIMIT}&order=asc' + (f'&after={after}' if after else '')
        page = _get(url, key)
        if page is None:
            if items:
                raise StoredResponseError(f'{rid}: input_items page vanished mid-pagination')
            return None
        items.extend(page.get('data') or [])
        if not page.get('has_more') or not page.get('data'):
            return {'data': items, 'has_more': False}
        after = page.get('last_id') or page['data'][-1].get('id')
        if not after:
            raise StoredResponseError(f'{rid}: input_items says has_more but gives no cursor')


def response(rid, project, part, cache):
    """part: 'input' (input_items, oldest first) or 'output' (the response object)."""
    os.makedirs(cache, exist_ok=True)
    path = os.path.join(cache, f'{rid}.{part}.json')
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    key = key_for(project)
    data = _get_input_items(rid, key) if part == 'input' else _get(f'{API}/{rid}', key)
    if data is not None:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
    return data


def text_of(item):
    return ''.join(c.get('text', '') for c in (item.get('content') or []) if isinstance(c, dict))


def system_text(inp):
    """The prompt plus its context: every system and developer message, in send order.

    Up to Intelligence Service 1.35 the context sits inside the system message; from 1.36
    it is a developer message after it, and from 1.40 a developer `---` (a cache delimiter,
    skipped here) comes first. Items are oldest first (`_get_input_items`).
    """
    return '\n\n'.join(t for it in (inp or {}).get('data', []) if it.get('role') in ('system', 'developer')
                       and (t := text_of(it)) and t != '---')


def section(text, heading):
    """Body under an exact heading line, up to the next heading of the same or higher level."""
    m = re.search(r'^' + re.escape(heading) + r'\s*$', text, re.M)
    if not m:
        return None
    level = len(heading) - len(heading.lstrip('#'))
    rest = text[m.end():]
    stop = re.search(r'^#{1,%d} ' % level, rest, re.M)
    return (rest[:stop.start()] if stop else rest).strip()


def headings(text):
    return [(m.group(1), m.group(2)) for m in HEADING.finditer(text)]


def responses_rows(ledger_path):
    """Ledger rows readable through the Responses endpoint, plus the count skipped."""
    rows = [r for r in json.load(open(ledger_path, encoding='utf-8')) if r.get('openaiId')]
    keep = [r for r in rows if (r.get('openaiAPI') or '').lower() == 'responses'
            or (not r.get('openaiAPI') and r['openaiId'].startswith('resp_'))]
    return keep, len(rows) - len(keep)


def _try(rid, project, part, cache):
    """('ok', data) | ('expired', None) | ('error', message) for one id."""
    try:
        data = response(rid, project, part, cache)
    except StoredResponseError as e:
        return 'error', str(e)
    return ('ok', data) if data is not None else ('expired', None)


def probe(args):
    rows, skipped = responses_rows(args.ledger)
    random.seed(args.seed)
    sample = random.sample(rows, min(args.sample, len(rows)))
    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(lambda r: _try(r['openaiId'], args.project, 'input', args.cache), sample))
    status = collections.Counter(s for s, _ in results)
    errors = collections.Counter(msg for s, msg in results if s == 'error')
    texts = [system_text(data) for s, data in results if s == 'ok']
    after_missing = 0
    if args.after:  # skip the instruction headings above the data section
        kept = []
        for t in texts:
            if args.after in t:
                kept.append(t[t.index(args.after):])
            else:
                after_missing += 1
        texts = kept
    seen, empty, labels = collections.Counter(), collections.Counter(), collections.defaultdict(collections.Counter)
    for t in texts:
        for hashes, title in headings(t):
            h = f'{hashes} {title}'
            body = section(t, h) or ''
            seen[h] += 1
            # A heading whose body is blank or ends in an empty marker is empty.
            last = body.strip().splitlines()[-1] if body.strip() else ''
            if not body.strip() or EMPTY.match(last.strip()):
                empty[h] += 1
            for label in set(LABEL.findall(body)):
                labels[h][label] += 1
    print(json.dumps({
        'sampled': len(sample), 'probed': len(texts), 'expired': status['expired'],
        'errors': status['error'], 'afterHeadingMissing': after_missing,
        'skippedNonResponses': skipped,
    }))
    for msg, n in errors.most_common(3):
        print(f'  error x{n}: {msg}')
    for h, n in seen.most_common():
        flag = '  <-- empty in most requests' if empty[h] >= 0.9 * n and n >= 5 else ''
        print(f'{n:4d} seen  {empty[h]:4d} empty  {h}{flag}')
        if labels[h]:
            print(f'            fields: {", ".join(k for k, _ in labels[h].most_common(8))}')


def fetch(args):
    rows, skipped = responses_rows(args.ledger)
    parts = ('input', 'output') if args.part == 'both' else (args.part,)
    jobs = [(r['openaiId'], p) for r in rows for p in parts]
    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(lambda j: _try(j[0], args.project, j[1], args.cache), jobs))
    status = collections.Counter(s for s, _ in results)
    print(json.dumps({'requested': len(jobs), 'cached': status['ok'], 'expired': status['expired'],
                      'errors': status['error'], 'skippedNonResponses': skipped}))
    for msg, n in collections.Counter(m for s, m in results if s == 'error').most_common(3):
        print(f'  error x{n}: {msg}')


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    for name in ('probe', 'fetch'):
        p = sub.add_parser(name)
        p.add_argument('--project', required=True, help='OpenAI project key: feed, insights, summary, conversation, …')
        p.add_argument('--ledger', required=True, help='JSON array written by ledger.py')
        p.add_argument('--cache', required=True, help='cache directory (keep it out of any repo)')
    sub.choices['probe'].add_argument('--sample', type=int, default=50)
    sub.choices['probe'].add_argument('--seed', type=int, default=7)
    sub.choices['probe'].add_argument('--after', help='probe only from this heading on, e.g. "## Input Data"')
    sub.choices['fetch'].add_argument('--part', choices=('input', 'output', 'both'), default='input')
    args = ap.parse_args()
    try:
        key_for(args.project)  # fail fast on a missing key before starting the pool
        probe(args) if args.cmd == 'probe' else fetch(args)
    except StoredResponseError as e:
        sys.exit(f'stored_responses: {e}')


if __name__ == '__main__':
    main()
