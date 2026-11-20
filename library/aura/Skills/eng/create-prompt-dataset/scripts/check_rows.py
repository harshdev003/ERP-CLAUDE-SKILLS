#!/usr/bin/env python3
"""Validate prompt-dataset rows against the row contract and print the preview.

Run it before de-identifying and again on the de-identified file before the bulk insert.
Checks: a non-empty JSON array; each row has a list `input` of {role, content} turns (`[]` for
a single-response prompter), metadata.contextInstructions, a non-empty metadata.structured
object with no null values (null means "not filled yet"), an `expected` object with a
`rubric` and integer `items` {min, max}, the required metadata, and a unique
metadata.case_id. Rows in the old shape (context and structured inside `input`, a string
`expected`, metadata.expectedCards) are named as such. With --require-deidentified, every
row must also carry metadata.deidentified (written by deidentify.py).

Prints problems (case_ids and field names only) or the preview: rows per slice, context
size (median and max) and rows with a pending product decision. Never prints row content.
Exits non-zero when any problem is found, so it can gate the insert.

usage:
  check_rows.py --rows rows.json
  check_rows.py --rows rows.deid.json --require-deidentified
"""
import argparse
import collections
import json
import sys

REQUIRED_METADATA = ('case_id', 'pattern', 'severity', 'asOf', 'userId')


def _check_input(cid, inp):
    if isinstance(inp, dict) and ('contextInstructions' in inp or 'structured' in inp):
        return [f'{cid}: input is the old shape; move contextInstructions and structured into metadata, input = []']
    if not isinstance(inp, list):
        return [f'{cid}: input must be a list of {{role, content}} turns ([] when there are none)']
    for j, m in enumerate(inp):
        if not (isinstance(m, dict) and isinstance(m.get('role'), str) and m['role']
                and isinstance(m.get('content'), str)):
            return [f'{cid}: input[{j}] is not a {{role, content}} turn']
    return []


def _check_expected(cid, expected, meta):
    problems = []
    if 'expectedCards' in meta:
        problems.append(f'{cid}: metadata.expectedCards is the old shape; move it to expected.items')
    if isinstance(expected, str):
        return problems + [f'{cid}: expected is a string (old shape); use {{rubric, items: {{min, max}}}}']
    if not isinstance(expected, dict):
        return problems + [f'{cid}: expected (rubric and item range) missing']
    rubric = expected.get('rubric')
    if not isinstance(rubric, str) or not rubric.strip():
        problems.append(f'{cid}: expected.rubric missing')
    items = expected.get('items')
    lo, hi = (items.get('min'), items.get('max')) if isinstance(items, dict) else (None, None)
    if not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in (lo, hi)):
        problems.append(f'{cid}: expected.items must be {{min, max}} non-negative integers')
    elif lo > hi:
        problems.append(f'{cid}: expected.items min {lo} is above max {hi}')
    return problems


def validate(rows, require_deidentified):
    problems = []
    ids = collections.Counter()
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            problems.append(f'row {i}: not an object')
            continue
        meta = r.get('metadata') if isinstance(r.get('metadata'), dict) else {}
        cid = meta.get('case_id') or f'row {i}'
        ids[cid] += 1
        for k in REQUIRED_METADATA:
            if not meta.get(k):
                problems.append(f'{cid}: metadata.{k} missing')
        if require_deidentified and not meta.get('deidentified'):
            problems.append(f'{cid}: not de-identified (run deidentify.py)')
        problems += _check_input(cid, r.get('input'))
        context = meta.get('contextInstructions')
        if not isinstance(context, str) or not context.strip():
            problems.append(f'{cid}: metadata.contextInstructions missing')
        structured = meta.get('structured')
        if not isinstance(structured, dict) or not structured:
            problems.append(f'{cid}: metadata.structured missing or empty')
            structured = {}
        for k, v in structured.items():
            if v is None:
                problems.append(f'{cid}: metadata.structured.{k} is null (not filled yet)')
        problems += _check_expected(cid, r.get('expected'), meta)
    problems += [f'{cid}: duplicate case_id x{n}' for cid, n in ids.items() if n > 1]
    return problems


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--rows', required=True)
    ap.add_argument('--require-deidentified', action='store_true')
    args = ap.parse_args()

    rows = json.load(open(args.rows, encoding='utf-8'))
    if not isinstance(rows, list) or not rows:
        sys.exit(f'{args.rows}: expected a non-empty JSON array of rows')
    problems = validate(rows, args.require_deidentified)
    if problems:
        print('\n'.join(problems[:40]))
        sys.exit(f'{len(problems)} problem(s); fix the rows before inserting')

    slices = collections.Counter(r['metadata']['pattern'] for r in rows)
    lengths = sorted(len(r['metadata']['contextInstructions']) for r in rows)
    print(json.dumps({
        'rows': len(rows),
        'slices': dict(sorted(slices.items())),
        'withTurns': sum(1 for r in rows if r['input']),
        'contextChars': {'p50': lengths[len(lengths) // 2], 'max': lengths[-1]},
        'pendingDecision': sum(1 for r in rows if r['metadata'].get('pendingDecision')),
        'deidentified': sum(1 for r in rows if r['metadata'].get('deidentified')),
    }, indent=1))


if __name__ == '__main__':
    main()
