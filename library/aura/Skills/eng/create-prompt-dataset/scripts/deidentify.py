#!/usr/bin/env python3
"""De-identify prompt-dataset rows and check for residue before anything goes to Braintrust.

For each row, an LLM lists the identifying spans in metadata.contextInstructions and any
`input` turns (people's names, specific places, employers, schools, contacts, exact dates
of birth). Code then replaces them, longest first: people get a fixed pseudonym, other
kinds a neutral placeholder. The same mapping is applied to every string in `input`,
metadata.contextInstructions, metadata.structured and the metadata item lists, so the
context, its structured source and the turns stay consistent. A residue check then counts
any replaced span, or the user's own given name, still present. Rows with residue, or whose
call failed, are held back and listed by case_id only.

`metadata.userId` is cut to its first 8 characters. Prints counts and case_ids only, never
row content.

usage:
  deidentify.py --rows rows.json --out rows.deid.json [--model gpt-5.4] [--key-env OPENAI_EVALUATIONS_API_KEY]

Paid inference: one Responses call per row on service_tier flex, falling back to auto only
when flex itself is refused (429 or a service_tier error).
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

API = 'https://api.openai.com/v1/responses'
NAMES = ['Alex', 'Sam', 'Jordan', 'Taylor', 'Morgan', 'Casey', 'Riley', 'Jamie', 'Avery', 'Quinn',
         'Drew', 'Rowan', 'Sky', 'Blake', 'Reese']
PLACEHOLDER = {'place': 'the city', 'org': 'the company', 'contact': '[contact]', 'date': '[date]'}
PII_PROMPT = (
    'List every piece of personally identifying information in the text: people\'s names (first or last, '
    'including nicknames), specific places (cities, towns, streets, states, countries only if paired with a city), '
    'employers, schools, emails, phone numbers, usernames, exact birth dates. Do NOT list generic words, '
    'pronouns, relationship words (husband, son), product names, or the assistant\'s name Aurie. '
    'Return JSON only: {"spans":[{"text":"<exact substring>","type":"person|place|org|contact|date"}]}'
)
GIVEN_NAME = re.compile(r'Given name is ([^\s.,]+)')
# metadata keys that can carry user text; everything else in metadata is ids and labels
METADATA_TEXT = ('contextInstructions', 'structured', 'mustNotRepeat', 'steerAwayFrom')


class DeidentifyError(Exception):
    """One row could not be de-identified."""


def post(body, key):
    """One Responses call. Flex is retried on auto only when flex itself is refused."""
    for tier in ('flex', 'auto'):
        req = urllib.request.Request(API, data=json.dumps({**body, 'service_tier': tier}).encode(), method='POST',
                                     headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=300) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                text = e.read().decode('utf-8', 'replace')
                if tier == 'flex' and (e.code == 429 or (e.code == 400 and 'service_tier' in text)):
                    break  # flex refused: try auto
                if e.code in (500, 502, 503, 504):
                    time.sleep(3 * (attempt + 1))
                    continue
                raise DeidentifyError(f'HTTP {e.code}') from e
            except (urllib.error.URLError, TimeoutError):
                time.sleep(3 * (attempt + 1))
        else:
            raise DeidentifyError(f'gave up on tier {tier}')
    raise DeidentifyError('flex and auto both refused')


def build_mapping(spans, account=()):
    """Span text -> replacement. Longest first, so a full name wins over its first name.

    Pseudonyms skip any name that occurs in the row's own spans, so a replacement never
    reintroduces a real name the residue check is looking for.
    """
    items = {((s.get('text') or '').strip(), s.get('type')) for s in spans} | {(a, 'person') for a in account}
    taken = {w.lower() for text, _ in items for w in re.findall(r'\w+', text)}
    pool = [n for n in NAMES if n.lower() not in taken] or NAMES
    mapping, people = {}, 0
    for text, kind in sorted(items, key=lambda x: -len(x[0])):
        if len(text) < 2 or text in mapping:
            continue
        if kind == 'person':
            mapping[text] = pool[people % len(pool)]
            people += 1
        else:
            mapping[text] = PLACEHOLDER.get(kind, '[redacted]')
    return mapping


def apply_mapping(text, mapping):
    for old in sorted(mapping, key=len, reverse=True):
        text = re.sub(r'(?<!\w)' + re.escape(old) + r'(?!\w)', mapping[old], text)
    return text


def walk(value, fn):
    """Apply fn to every string inside a JSON value."""
    if isinstance(value, str):
        return fn(value)
    if isinstance(value, list):
        return [walk(v, fn) for v in value]
    if isinstance(value, dict):
        return {k: walk(v, fn) for k, v in value.items()}
    return value


def residue(text, account, spans):
    """How many identifiers still appear. Counts only, never the values."""
    hits = sum(1 for a in account if len(a) > 2 and re.search(r'(?<!\w)' + re.escape(a) + r'(?!\w)', text, re.I))
    return hits + sum(1 for sp in set(spans) if re.search(r'(?<!\w)' + re.escape(sp) + r'(?!\w)', text))


def pii_text(row):
    """What the LLM reads: the rendered context, then the turns (empty for single-response rows)."""
    context = row['metadata']['contextInstructions']
    turns = '\n'.join(f"{t.get('role')}: {t.get('content')}" for t in row.get('input') or [])
    return context + (f'\n\n## Conversation\n{turns}' if turns else '')


def deidentify_row(row, key, model):
    meta = dict(row.get('metadata') or {})
    if not isinstance(meta.get('contextInstructions'), str):
        raise KeyError('metadata.contextInstructions')
    account = GIVEN_NAME.findall(meta['contextInstructions'])
    out = post({'model': model, 'reasoning': {'effort': 'low'}, 'store': False,
                'text': {'format': {'type': 'json_object'}},
                'input': [{'role': 'system', 'content': PII_PROMPT}, {'role': 'user', 'content': pii_text(row)}]}, key)
    reply = ''.join(c.get('text', '') for x in out.get('output', []) if x.get('type') == 'message'
                    for c in x.get('content', []))
    try:
        spans = json.loads(reply).get('spans') or []
    except ValueError as e:
        raise DeidentifyError('model reply was not JSON') from e
    mapping = build_mapping(spans, account)  # the user's given name is replaced, listed or not
    fix = lambda s: apply_mapping(s, mapping)  # noqa: E731
    new = dict(row)
    new['input'] = walk(row.get('input') or [], fix)
    for k in METADATA_TEXT:
        if k in meta:
            meta[k] = walk(meta[k], fix)
    if meta.get('userId'):
        meta['userId'] = str(meta['userId'])[:8]
    meta['deidentified'] = {'model': model, 'replaced': len(mapping)}
    new['metadata'] = meta
    left = residue(json.dumps(new['input'], ensure_ascii=False) + json.dumps(meta, ensure_ascii=False),
                   account, list(mapping))
    return new, len(mapping), left


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--rows', required=True, help='JSON array of rows (row contract)')
    ap.add_argument('--out', required=True, help='de-identified rows that passed the residue check')
    ap.add_argument('--model', default='gpt-5.4')
    ap.add_argument('--key-env', default='OPENAI_EVALUATIONS_API_KEY', help='env var holding the OpenAI key')
    args = ap.parse_args()
    key = os.environ.get(args.key_env)
    if not key:
        sys.exit(f'deidentify: {args.key_env} is not set')
    rows = json.load(open(args.rows, encoding='utf-8'))
    if not isinstance(rows, list) or not rows:
        sys.exit(f'{args.rows}: expected a non-empty JSON array of rows')

    def work(row):
        cid = (row.get('metadata') or {}).get('case_id', '?')
        try:
            new, replaced, left = deidentify_row(row, key, args.model)
        except (DeidentifyError, KeyError) as e:
            return cid, None, 0, f'error: {e}'
        return cid, (new if left == 0 else None), replaced, (f'residue: {left}' if left else None)

    with ThreadPoolExecutor(6) as ex:
        results = list(ex.map(work, rows))
    clean = [new for _, new, _, _ in results if new is not None]
    held = [(cid, why) for cid, new, _, why in results if new is None]
    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(clean, f, ensure_ascii=False, indent=1)
    print(json.dumps({'rows': len(rows), 'clean': len(clean), 'heldBack': len(held),
                      'spansReplaced': sum(r for _, _, r, _ in results), 'out': args.out}))
    for cid, why in held:
        print(f'  held back {cid}: {why}')


if __name__ == '__main__':
    main()
