#!/usr/bin/env python3
"""Check a Test Cases database (Aurie by default, Aura with --app aura) against the app it tests.

A case can rot in two ways the suite cannot see on its own:

  * its experiment gate refers to an arm that has since started, ended or
    rolled out, so the premise it asserts is no longer the live behaviour;
  * it names a screen whose knowledge file has been renamed or removed.

Both have happened. Cases asserted a composer that an experiment removed two
weeks earlier, and a case asserted an experiment did not exist while it was
running. Nothing flagged either until an agent tried to run them and reported
"Not run", which reads like a scheduling gap rather than a stale assertion.

This is the Notion-side counterpart to sim_experiments.py check, which already
stops an experiment being added to or removed from the app without the docs
moving.
"""
import argparse, json, os, re, subprocess, sys, urllib.error, urllib.request

# Per app: the cases database, and where that app's repo keeps its sim-test scripts
# and screen knowledge. The two harnesses are the same design in different folders.
APPS = {
    'aurie': {'db': '3e875e23-af20-817f-ad0e-e235265c7e9b',
              'simtests': 'AuraConsumerAppSimTests',
              'swift_keys': 'AuraConsumerApp/Services/Experiments/Models/Experiments.swift'},
    'aura':  {'db': '3f075e23-af20-81c3-9ca1-e5657d32aa63',
              'simtests': 'AuraSimTests',
              'swift_keys': None},   # React Native — keys come from `sim_experiments.py keys --plain`
}
APP = APPS['aurie']
CASES_DB = APP['db']
API = 'https://api.notion.com/v1'


def token():
    tok = os.environ.get('NOTION_TOKEN')
    if tok:
        return tok
    path = os.path.expanduser('~/.claude/settings.json')
    try:
        with open(path, encoding='utf-8') as fh:
            return json.load(fh).get('env', {}).get('NOTION_TOKEN')
    except (OSError, ValueError):
        return None


def call(method, path, body=None):
    tok = token()
    if not tok:
        sys.exit('NOTION_TOKEN is not set and ~/.claude/settings.json has no env.NOTION_TOKEN')
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f'{API}/{path}', data=data, method=method,
        headers={'Authorization': f'Bearer {tok}', 'Notion-Version': '2022-06-28',
                 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        sys.exit(f'{exc.code} {method} {path}: {exc.read().decode()[:400]}')


def plain(prop):
    """A Notion rich_text / select / title property as a flat string."""
    if not prop:
        return ''
    kind = prop.get('type')
    if kind in ('rich_text', 'title'):
        return ''.join(x.get('plain_text', '') for x in prop[kind])
    if kind == 'select':
        return (prop['select'] or {}).get('name', '')
    return ''


def fetch_cases():
    rows, cursor = [], None
    while True:
        body = {'page_size': 100}
        if cursor:
            body['start_cursor'] = cursor
        page = call('POST', f'databases/{CASES_DB}/query', body)
        rows += page['results']
        if not page.get('has_more'):
            break
        cursor = page['next_cursor']
    out = []
    for row in rows:
        props = row['properties']
        out.append({
            'id': plain(props.get('ID')),
            'name': plain(props.get('Name')),
            'status': plain(props.get('Status')),
            'gate': plain(props.get('Experiment gate')),
            'screens': plain(props.get('Screens')),
            'url': row['url'],
        })
    return [c for c in out if c['id']]


def aura_experiments(repo, script):
    """Aura's sim_experiments.py prints `key active='ab' ...` rather than a status
    word, so read the arm string: one letter is ROLLED OUT, several is RUNNING,
    none is ENDED. Keys come from `keys --plain`, the same list sim_test.sh
    validates --experiment against, so there is no second key source to drift.
    'aa' is one arm, not two — see the comment in the loop."""
    env = dict(os.environ)
    if not env.get('AURA_API_TOKEN'):
        try:
            with open(os.path.expanduser('~/.claude/settings.json'), encoding='utf-8') as fh:
                tok = json.load(fh).get('env', {}).get('AURA_API_TOKEN')
            if tok:
                env['AURA_API_TOKEN'] = tok
        except (OSError, ValueError):
            pass
    state, keys, why = {}, set(), None
    if not os.path.exists(script):
        return state, keys, f'{script} not found'
    try:
        res = subprocess.run([sys.executable, script, 'keys', '--plain'],
                             capture_output=True, text=True, timeout=60, cwd=repo)
        keys = {l.strip() for l in res.stdout.splitlines() if re.fullmatch(r'\w+', l.strip())}
        res = subprocess.run([sys.executable, script, 'state'],
                             capture_output=True, text=True, timeout=120, cwd=repo, env=env)
        for line in res.stdout.splitlines():
            m = re.match(r"\s*(\w+)\s+active=(?:'([a-z]*)'|None)", line)
            if not m:
                continue
            # Count distinct arms: `active` is a bucket-weight string, so 'aab' is two
            # arms and 'aa' is one. One arm is ROLLED OUT, not merely paused: the
            # experiment server assigns it to every user without version or traffic
            # gating, which is the same rule sim_experiments.py prints its label from.
            arms = set(m.group(2) or '')
            state[m.group(1)] = ('ROLLED OUT' if len(arms) == 1
                                 else 'RUNNING' if len(arms) > 1 else 'ENDED')
        if not state:
            why = (f'sim_experiments.py state returned no experiment lines (exit {res.returncode})'
                   + (f': {res.stderr.strip()[:200]}' if res.stderr.strip() else ''))
    except (subprocess.SubprocessError, OSError) as exc:
        why = f'could not run sim_experiments.py: {exc}'
    return state, keys, why


def repo_experiments(repo):
    """Live experiment state, keyed by experiment name.

    Uses sim_experiments.py, which is the app repo's own source of truth and
    already runs in its pre-commit hook. Falls back to the key list in
    Experiments.swift if the live call is unavailable, because an offline
    answer about which keys exist is still worth having.
    """
    script = os.path.join(repo, APP['simtests'], 'Scripts/sim_experiments.py')
    if not APP['swift_keys']:
        return aura_experiments(repo, script)
    state, why = {}, None
    if not os.path.exists(script):
        why = f'{script} not found'
    else:
        try:
            res = subprocess.run([sys.executable, script, 'state'],
                                 capture_output=True, text=True, timeout=120, cwd=repo)
            for line in res.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 3 and parts[1] in ('RUNNING', 'ENDED', 'ROLLED', 'DARK'):
                    name = parts[0]
                    status = 'ROLLED OUT' if parts[1] == 'ROLLED' else parts[1]
                    state[name] = status
            # Judge success on whether experiments were parsed, not on the exit
            # code: sim_experiments.py exits non-zero when it *finds drift*, which
            # is a healthy run with something to say. Gating on returncode warns on
            # every normal invocation, which is worse than the silence it replaces.
            if not state:
                why = (f'sim_experiments.py state returned no experiment lines '
                       f'(exit {res.returncode})'
                       + (f': {res.stderr.strip()[:200]}' if res.stderr.strip() else ''))
        except (subprocess.SubprocessError, OSError) as exc:
            why = f'could not run sim_experiments.py state: {exc}'
    swift = os.path.join(repo, APP['swift_keys'])
    keys = set()
    if os.path.exists(swift):
        with open(swift, encoding='utf-8') as fh:
            keys = {m.group(1) for m in re.finditer(r'static let \w+\s*=\s*"(\w+)"', fh.read())}
    return state, keys, why


def repo_screens(repo):
    d = os.path.join(repo, APP['simtests'], 'knowledge/screens')
    if not os.path.isdir(d):
        return set()
    return {f[:-3] for f in os.listdir(d) if f.endswith('.md') and f != 'README.md'}


# Phrases a case uses to assert an experiment is absent. If the experiment is
# live, a case saying this is asserting something false.
ABSENT = re.compile(r'not (yet )?(created|live|running)|does not exist|no longer exists|404', re.I)


def check(repo, as_json=False, ignore=(), include_drafts=False):
    skip = ('Retired',) if include_drafts else ('Retired', 'Draft')
    cases = fetch_cases()
    state, keys, state_error = repo_experiments(repo)
    screens = repo_screens(repo)
    findings = []

    for case in cases:
        if case['status'] in skip:
            # A retired case is allowed to describe behaviour that no longer exists,
            # and a draft is not yet claiming to. Neither is drift.
            continue

        gate = case['gate']
        if gate:
            # Whole-token match. A raw substring test lets a short key match a
            # longer one that merely starts with it, crediting the wrong experiment.
            named = {k for k in (set(state) | keys)
                     if k and re.search(rf'(?<![0-9A-Za-z]){re.escape(k)}(?![0-9A-Za-z])', gate)}
            for key in sorted(named):
                live = state.get(key)
                if key not in keys and keys:
                    findings.append((case, 'gate-key-gone',
                                     f'gate names {key}, which the app no longer reads'))
                elif live == 'RUNNING' and ABSENT.search(gate):
                    # Only RUNNING contradicts a gate that claims absence. An ENDED,
                    # ROLLED OUT or DARK experiment is one nobody is bucketed into, so
                    # "not live" is a fair description of it and the case is not wrong.
                    findings.append((case, 'gate-says-absent',
                                     f'gate says {key} is absent, but it is RUNNING'))
                elif live in ('ENDED', 'ROLLED OUT'):
                    findings.append((case, 'gate-settled',
                                     f'{key} is {live} — confirm the case asserts the surviving arm'))
            if not named and (state or keys):
                findings.append((case, 'gate-unrecognised',
                                 'gate text names no known experiment key'))

        for tok in [t.strip() for t in case['screens'].split(',') if t.strip()]:
            if screens and tok not in screens:
                fold = lambda x: x.replace('_', '').lower()
                near = [s for s in screens if fold(s) == fold(tok)]
                if near:
                    findings.append((case, 'screen-cased-differently',
                                     f'names "{tok}" but the knowledge file is "{near[0]}" — '
                                     'same screen, different spelling, so a lookup by name misses it'))
                else:
                    findings.append((case, 'screen-missing',
                                     f'names screen "{tok}", which has no knowledge file'))

    # Coverage speaks about the LIVE suite against LIVE experiments. A retired or
    # draft row must not credit an experiment as covered, and an ended, rolled-out
    # or dark experiment is not something the suite is failing to test.
    active = [c for c in cases if c['status'] not in skip]
    running = {k for k, v in state.items() if v == 'RUNNING'}
    covered = {k for c in active for k in running
               if re.search(rf'(?<![0-9A-Za-z]){re.escape(k)}(?![0-9A-Za-z])', c['gate'] or '')}
    uncovered = sorted(running - covered)

    # Screens the other way round: knowledge files no active case names. This is
    # where new cases come from — a documented surface nothing asserts. Compared
    # case-insensitively, since the casing check above already reports mismatches.
    named = {t.strip().lower() for c in active for t in (c['screens'] or '').split(',') if t.strip()}
    unreferenced = sorted(d for d in screens if d.lower() not in named and d not in ignore)

    if as_json:
        print(json.dumps({
            'cases': len(cases),
            'live_state_error': state_error,
            'findings': [{'id': c['id'], 'kind': k, 'detail': d, 'url': c['url']}
                         for c, k, d in findings],
            'experiments_without_a_case': uncovered,
            'screens_without_a_case': unreferenced,
        }, indent=2))
        return 1 if findings else 0

    print(f'{len(cases)} cases checked against {repo}')
    if state_error:
        print(f'\n  WARNING: live experiment state unavailable — {state_error}')
        print('  Gate findings that need live state (gate-says-absent, gate-settled) and the')
        print('  coverage list are omitted. This run cannot tell you they are clean.')
    if not findings and not state_error:
        print('\nNo drift: every gate names a live experiment and every screen has a knowledge file.')
    elif not findings:
        print('\nNo drift found in what could be checked — see the warning above.')
    else:
        print(f'\n{len(findings)} case(s) may no longer match the app:\n')
        for case, kind, detail in findings:
            print(f'  {case["id"]:8s} [{kind}]')
            print(f'           {detail}')
            print(f'           {case["url"]}')
    if uncovered:
        print(f'\n{len(uncovered)} RUNNING experiment(s) no active case mentions:')
        print('  ' + ', '.join(uncovered))
        print('  Not necessarily wrong — but a live experiment nothing tests is a gap.')
    if unreferenced:
        print(f'\n{len(unreferenced)} screen doc(s) no active case names:')
        for u in unreferenced: print(f'  {u}')
        print('  Each is a surface the suite asserts nothing about. Write a case, or pass it in')
        print('  --ignore-screens with the reason recorded on the card that decided so.')
    print('\nThis reports; it does not edit. A stale case is retired or rewritten by hand,')
    print('because deciding which arm survives is a judgement about the product.')
    return 1 if findings else 0


def main():
    # Output carries em dashes and Notion case titles; without this a non-UTF-8
    # console raises UnicodeEncodeError instead of printing the report.
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except (AttributeError, ValueError):
        pass

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    chk = sub.add_parser('check', help='report cases whose premise no longer matches the app')
    chk.add_argument('--repo', required=True,
                     help='path to the app checkout — AuraConsumerApp, or Aura with --app aura')
    chk.add_argument('--app', choices=sorted(APPS), default=os.environ.get('QA_APP', 'aurie'),
                     help='which suite to check (default aurie, or $QA_APP)')
    chk.add_argument('--json', action='store_true')
    chk.add_argument('--ignore-screens', default='',
                     help='comma-separated screen docs to leave out of the coverage list — '
                          'DEBUG-only harnesses, or screens whose experiment rolled out away')
    chk.add_argument('--include-drafts', action='store_true',
                     help='treat Draft cases as live — for checking a new suite before review')
    args = ap.parse_args()
    if args.cmd == 'check':
        global APP, CASES_DB
        APP = APPS[args.app]
        CASES_DB = APP['db']
        ignore = tuple(x.strip() for x in args.ignore_screens.split(',') if x.strip())
        sys.exit(check(os.path.abspath(args.repo), args.json, ignore, args.include_drafts))


if __name__ == '__main__':
    main()
