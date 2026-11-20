#!/usr/bin/env python3
"""Read and write the QA databases in AI Knowledge — Aurie by default, Aura with --app aura.

Canonical copy: plugins/qa/skills/write-test-cases/scripts/qa_notion.py
Duplicated (byte-identical) into plugins/qa/skills/run-test-cases/scripts/.
Propagate every edit across both copies in the same PR:

    diff plugins/qa/skills/{write,run}-test-cases/scripts/qa_notion.py

Auth: NOTION_TOKEN, from the shell env or ~/.claude/settings.json `env`.
"""
import argparse, json, os, re, sys, time, urllib.error, urllib.request

# One pair of databases per app. Both apps share the case and run schemas, so every
# command works unchanged against either; Aura's case rows add Locale scope and
# Environment, which `list --json` passes through when present.
APPS = {
    'aurie': ('3e875e23-af20-817f-ad0e-e235265c7e9b',   # QA/Test Plan and Test Cases/Aurie/Test Cases
              '3e875e23-af20-8133-849a-c2ecca7536e4'),  # QA/Test Execution & Reports/Aurie/Test Runs
    'aura':  ('3f075e23-af20-81c3-9ca1-e5657d32aa63',   # QA/Test Plan and Test Cases/Aura/Aura Mobile Test Cases
              '3f175e23-af20-8146-86a1-c134f5725a34'),  # QA/Test Execution & Reports/Aura/Test Runs
}
CASES_DB, RUNS_DB = APPS['aurie']
VERDICTS = ('Pass', 'Fail', 'Blocked', 'Not run')


def token():
    t = os.environ.get('NOTION_TOKEN')
    if t: return t
    p = os.path.expanduser('~/.claude/settings.json')
    if os.path.exists(p):
        t = json.load(open(p, encoding='utf-8')).get('env', {}).get('NOTION_TOKEN')
        if t: return t
    sys.exit('NOTION_TOKEN is not set (shell env or ~/.claude/settings.json)')


HDR = None
def hdr():
    global HDR
    if HDR is None:
        HDR = {'Authorization': f'Bearer {token()}', 'Notion-Version': '2022-06-28',
               'Content-Type': 'application/json'}
    return HDR


def call(method, path, body=None):
    req = urllib.request.Request(f'https://api.notion.com/v1/{path}',
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers=hdr(), method=method)
    for attempt in range(5):
        try:
            return json.load(urllib.request.urlopen(req))
        except urllib.error.HTTPError as e:
            msg = e.read().decode()
            if e.code in (429, 502, 503) and attempt < 4:
                time.sleep(1.5 * (attempt + 1)); continue
            raise SystemExit(f'Notion {e.code} on {method} {path}: {msg[:600]}')


# ---------- rich text ----------
# Italic needs a non-word character on both sides. Without that, the underscores in
# snake_case identifiers pair up as italic markers and are stripped, so a Screens
# value like `dashboard_home, login_screen` is stored as "dashboardhome, loginscreen".
TOKEN = re.compile(r'(\*\*.+?\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\)|(?<![0-9A-Za-z])_[^_\n]+_(?![0-9A-Za-z]))')

def rt(text):
    out = []
    for part in TOKEN.split(text or ''):
        if not part: continue
        ann, link, content = {}, None, part
        if part.startswith('**') and part.endswith('**'): ann, content = {'bold': True}, part[2:-2]
        elif part.startswith('`') and part.endswith('`'): ann, content = {'code': True}, part[1:-1]
        elif part.startswith('_') and part.endswith('_'): ann, content = {'italic': True}, part[1:-1]
        elif part.startswith('['):
            m = re.match(r'\[([^\]]+)\]\(([^)]+)\)', part); content, link = m.group(1), m.group(2)
        for i in range(0, max(len(content), 1), 1900):
            o = {'type': 'text', 'text': {'content': content[i:i+1900]}}
            if link: o['text']['link'] = {'url': link}
            if ann: o['annotations'] = ann
            out.append(o)
    return out or [{'type': 'text', 'text': {'content': ''}}]


def blk(t, **kw):
    return {'object': 'block', 'type': t, t: kw}


def md(text):
    """Markdown subset -> Notion blocks: headings, lists, quotes, code, tables, rules."""
    blocks, lines, i = [], text.split('\n'), 0
    while i < len(lines):
        s = lines[i].strip()
        if not s: i += 1; continue
        if s.startswith('```'):
            lang = s[3:].strip() or 'plain text'
            body, i = [], i + 1
            while i < len(lines) and not lines[i].strip().startswith('```'):
                body.append(lines[i]); i += 1
            i += 1
            blocks.append(blk('code', rich_text=rt('\n'.join(body)),
                              language=lang if lang in ('json','bash','shell','python','swift',
                                                        'markdown','yaml','plain text') else 'plain text'))
            continue
        if s.startswith('|') and i + 1 < len(lines) and set(lines[i+1].strip()) <= set('|-: '):
            rows = [[c.strip() for c in s.strip('|').split('|')]]; i += 2
            while i < len(lines) and lines[i].strip().startswith('|'):
                rows.append([c.strip() for c in lines[i].strip().strip('|').split('|')]); i += 1
            w = max(len(r) for r in rows)
            blocks.append(blk('table', table_width=w, has_column_header=True, has_row_header=False,
                              children=[blk('table_row', cells=[rt(c) for c in r + [''] * (w - len(r))])
                                        for r in rows]))
            continue
        if s == '---': blocks.append(blk('divider')); i += 1; continue
        for n, tag in ((4, 'heading_3'), (3, 'heading_2'), (2, 'heading_1')):
            if s.startswith('#' * (n - 1) + ' '):
                blocks.append(blk(tag, rich_text=rt(s[n:]))); break
        else:
            if s.startswith('> '):
                blocks.append(blk('callout', rich_text=rt(s[2:]), icon={'type':'emoji','emoji':'💡'}))
            elif re.match(r'^[-*] ', s):
                blocks.append(blk('bulleted_list_item', rich_text=rt(s[2:])))
            elif re.match(r'^\d+\. ', s):
                blocks.append(blk('numbered_list_item', rich_text=rt(re.sub(r'^\d+\. ', '', s))))
            else:
                blocks.append(blk('paragraph', rich_text=rt(s)))
        i += 1
    return blocks


def append(block_id, blocks):
    for i in range(0, len(blocks), 90):
        call('PATCH', f'blocks/{block_id}/children', {'children': blocks[i:i+90]})


# ---------- cases ----------
def _txt(prop):
    return ''.join(x['plain_text'] for x in prop.get('rich_text', []))

def _sel(prop):
    return (prop.get('select') or {}).get('name')

def query_cases(feature=None, priority=None, runner=None, automatable=None, status='Active'):
    out, cur = [], None
    while True:
        body = {'page_size': 100}
        if cur: body['start_cursor'] = cur
        d = call('POST', f'databases/{CASES_DB}/query', body)
        for r in d['results']:
            p = r['properties']
            row = {
                'page_id': r['id'], 'url': r['url'],
                'id': _txt(p['ID']),
                'name': ''.join(x['plain_text'] for x in p['Name']['title']),
                'feature': _sel(p['Feature']), 'priority': _sel(p['Priority']),
                'runner': [t['name'] for t in p['Runner']['multi_select']],
                'automatable': p['Automatable']['checkbox'],
                'status': _sel(p['Status']), 'precond': _sel(p['Preconditions']),
                'screens': _txt(p['Screens']), 'gate': _txt(p['Experiment gate']),
                'last_result': _sel(p['Last result']),
                'last_run': (p['Last run'].get('date') or {}).get('start'),
            }
            if 'Locale scope' in p:
                row['locale'] = _sel(p['Locale scope'])
            out.append(row)
        if not d.get('has_more'): break
        cur = d['next_cursor']
    def keep(r):
        if status and r['status'] != status: return False
        if feature and not r['feature'].startswith(feature): return False
        if priority and r['priority'] not in priority: return False
        if runner and not (set(runner) & set(r['runner'])): return False
        if automatable is not None and r['automatable'] != automatable: return False
        return True
    return sorted([r for r in out if keep(r)], key=lambda r: r['id'])


def next_id(prefix):
    used = [r['id'] for r in query_cases(status=None) if r['id'].startswith(prefix + '-')]
    n = max([int(i.split('-')[1]) for i in used] or [0])
    return f'{prefix}-{n+1:03d}'


def record_result(case_id, verdict, when=None, note=None):
    if verdict not in VERDICTS:
        sys.exit(f'verdict must be one of {VERDICTS}')
    rows = [r for r in query_cases(status=None) if r['id'] == case_id]
    if not rows: sys.exit(f'no case with ID {case_id}')
    props = {'Last result': {'select': {'name': verdict}}}
    if when: props['Last run'] = {'date': {'start': when}}
    call('PATCH', f'pages/{rows[0]["page_id"]}', {'properties': props})
    return rows[0]


def create_run(name, when, build, env, driver, scope, counts, result, body_md):
    page = call('POST', 'pages', {'parent': {'database_id': RUNS_DB}, 'properties': {
        'Name': {'title': rt(name)},
        'Run date': {'date': {'start': when}},
        'Build': {'rich_text': rt(build)},
        'Environment': {'select': {'name': env}},
        'Driver': {'select': {'name': driver}},
        'Scope': {'rich_text': rt(scope)},
        'Cases run': {'number': counts.get('run', 0)},
        'Passed': {'number': counts.get('Pass', 0)},
        'Failed': {'number': counts.get('Fail', 0)},
        'Blocked': {'number': counts.get('Blocked', 0)},
        'Not run': {'number': counts.get('Not run', 0)},
        'Result': {'select': {'name': result}},
    }})
    append(page['id'], md(body_md))
    return page


def main():
    # Case titles carry em dashes; without this, `list` and `get` raise
    # UnicodeEncodeError on a cp1252 console.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8')
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--app', choices=sorted(APPS), default=os.environ.get('QA_APP', 'aurie'),
                    help='which app\'s suite to read and write (default aurie, or $QA_APP)')
    sub = ap.add_subparsers(dest='cmd', required=True)

    q = sub.add_parser('list', help='list cases, filtered')
    q.add_argument('--feature'); q.add_argument('--priority', nargs='*')
    q.add_argument('--runner', nargs='*'); q.add_argument('--automatable-only', action='store_true')
    q.add_argument('--status', default='Active')
    q.add_argument('--json', action='store_true')

    g = sub.add_parser('get', help='print one case with its body as markdown')
    g.add_argument('--id', required=True)

    n = sub.add_parser('next-id', help='next free case id for a feature prefix')
    n.add_argument('--prefix', required=True)

    r = sub.add_parser('result', help='record a verdict against a case')
    r.add_argument('--id', required=True); r.add_argument('--verdict', required=True)
    r.add_argument('--date'); r.add_argument('--note')

    p = sub.add_parser('publish-run', help='write the run report into the Test Runs database')
    p.add_argument('--name', required=True)
    p.add_argument('--date', required=True)
    p.add_argument('--build', required=True)
    p.add_argument('--env', default='Simulator',
                   choices=['Simulator', 'TestFlight', 'Local DEBUG', 'Production'])
    p.add_argument('--driver', default='Agent', choices=['Agent', 'Human', 'Agent + Human'])
    p.add_argument('--scope', required=True)
    p.add_argument('--result', required=True, choices=['Green', 'Amber', 'Red'])
    p.add_argument('--body-file', required=True, help='markdown run report')
    for k in ('ran', 'passed', 'failed', 'blocked', 'not-run'):
        p.add_argument(f'--{k}', type=int, default=0)

    a = ap.parse_args()
    global CASES_DB, RUNS_DB
    CASES_DB, RUNS_DB = APPS[a.app]
    if a.cmd == 'list':
        rows = query_cases(a.feature, a.priority, a.runner,
                           True if a.automatable_only else None,
                           None if a.status == 'any' else a.status)
        if a.json: print(json.dumps(rows, indent=2)); return
        for x in rows:
            tags = ','.join(x['runner'])
            print(f"{x['id']:8} {x['priority']:3} {tags:22} {x['name'][:60]:60} {x['last_result'] or '-'}")
        print(f'\n{len(rows)} cases')
    elif a.cmd == 'get':
        rows = [x for x in query_cases(status=None) if x['id'] == a.id]
        if not rows: sys.exit(f'no case with ID {a.id}')
        c = rows[0]
        print(json.dumps({k: v for k, v in c.items() if k != 'page_id'}, indent=2))
        print('\n--- body ---')
        print(blocks_to_md(c['page_id']))
    elif a.cmd == 'next-id':
        print(next_id(a.prefix))
    elif a.cmd == 'result':
        c = record_result(a.id, a.verdict, a.date, a.note)
        print(f"{c['id']} -> {a.verdict}")
    elif a.cmd == 'publish-run':
        page = create_run(a.name, a.date, a.build, a.env, a.driver, a.scope,
                          {'run': a.ran, 'Pass': a.passed, 'Fail': a.failed,
                           'Blocked': a.blocked, 'Not run': getattr(a, 'not_run')},
                          a.result,
                          open(a.body_file, encoding='utf-8').read())
        print(page['url'])


def blocks_to_md(page_id):
    lines, cur = [], None
    while True:
        p = f'blocks/{page_id}/children?page_size=100' + (f'&start_cursor={cur}' if cur else '')
        d = call('GET', p)
        for b in d['results']:
            t = b['type']; o = b.get(t, {})
            txt = ''.join(x.get('plain_text', '') for x in o.get('rich_text', [])) if isinstance(o, dict) else ''
            if t == 'heading_2': lines.append(f'## {txt}')
            elif t == 'heading_3': lines.append(f'### {txt}')
            elif t == 'bulleted_list_item': lines.append(f'- {txt}')
            elif t == 'numbered_list_item': lines.append(f'1. {txt}')
            elif t == 'code': lines.append(f'```\n{txt}\n```')
            elif t == 'table':
                for row in call('GET', f'blocks/{b["id"]}/children?page_size=100')['results']:
                    cells = [''.join(x.get('plain_text', '') for x in c) for c in row['table_row']['cells']]
                    lines.append('| ' + ' | '.join(cells) + ' |')
            elif txt: lines.append(txt)
        if not d.get('has_more'): break
        cur = d['next_cursor']
    return '\n'.join(lines)


if __name__ == '__main__':
    main()
