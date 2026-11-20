#!/usr/bin/env python3
"""Post new OpenAI API changelog entries about prompt caching or Aura's models to Slack.

One run: fetch the changelog, split it into dated entries, keep the ones not seen
before that mention prompt caching or a model Aura uses, post them
to #ai-prompt-engineering as the aura_ai bot, then record every entry as seen.

Prints one JSON line: {"status": seeded|unchanged|posted|dry-run|error, ...}.

State is kept outside the plugin (default ~/.claude/monitors/openai-changelog/seen.json),
because a plugin update replaces the install directory. The first run only seeds it.
"""
import argparse
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import pathlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from slack_post import post_message  # noqa: E402  (local copy of the canonical helper)


def _ua_who() -> str:
    """Login name of whoever runs this, for the user agent: AINATIVE_UA_WHO, else the global git email's local part, git user.name, the OS account, or "unknown"."""
    who = re.sub(r"^\d+\+", "", os.environ.get("AINATIVE_UA_WHO", "").strip().partition("@")[0])
    if not who:
        if not hasattr(_ua_who, "found"):
            import getpass, subprocess
            found = ""
            for key in ("user.email", "user.name"):
                try:
                    val = subprocess.run(["git", "config", "--global", key], capture_output=True, encoding="utf-8", errors="replace", timeout=5).stdout.strip()
                except (OSError, subprocess.SubprocessError):
                    val = ""
                found = re.sub(r"^\d+\+", "", val.partition("@")[0]) if key == "user.email" else val
                if found:
                    break
            if not found:
                try:
                    found = getpass.getuser()
                except Exception:
                    found = ""
            _ua_who.found = found
        who = _ua_who.found
    return re.sub(r"[^A-Za-z0-9._-]+", "-", who).strip("-.") or "unknown"


def user_agent() -> str:
    """`AuraClaude/<who>/<plugin>:<skill>@<version> (session <id>)` -- labels this skill's calls to RTDB, GCP and the Aura API.

    Same format as gcloud_sa.user_agent(). A label, not proof: anyone can send it.
    """
    skill = pathlib.Path(__file__).resolve().parents[1]
    root = skill.parents[1]
    try:
        plugin = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["name"]
    except (OSError, ValueError, KeyError, TypeError):
        plugin = root.name
    try:
        front = (skill / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[1]
        version = re.search(r'version:\s*"([^"]+)"', front).group(1)
    except (OSError, IndexError, AttributeError):
        version = "unknown"
    session = os.environ.get("AINATIVE_SESSION_NAME") or (os.environ.get("CLAUDE_CODE_SESSION_ID") or "")[:8]
    session = re.sub(r"[^A-Za-z0-9._-]+", "-", session).strip("-") or "none"
    return f"AuraClaude/{_ua_who()}/{plugin}:{skill.name}@{version} (session {session})"


URL = 'https://developers.openai.com/api/docs/changelog'
CHANNEL = 'C07EX6CL43A'  # #ai-prompt-engineering
DEFAULT_STATE = os.path.expanduser('~/.claude/monitors/openai-changelog/seen.json')
SKILL_ID = 'eng:monitor-openai-changelog'
# IntelligenceService's own list of the OpenAI models its live config uses (ENGBACK-1656).
DEFAULT_API_BASE = 'https://services.aurahealth.io'
MODELS_PATH = '/ai/openai/models'

DATE = re.compile(r'^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2}(, \d{4})?$')
# Month separators ("January, 2026") sit between entries; they are not entry text.
MONTH_HEADER = re.compile(r'^(January|February|March|April|May|June|July|August|September|October|'
                          r'November|December), \d{4}$')
CACHING = re.compile(r'prompt[_ ]cach|cache[_ ]retention|cached[- ]input|cache hit|caching', re.I)
# A page that parses to fewer entries than this has changed layout, not lost its history.
MIN_ENTRIES = 10


def page_lines(page):
    page = re.sub(r'(?s)<script.*?</script>|<style.*?</style>', '', page)
    text = html.unescape(re.sub(r'<[^>]+>', '\n', page))
    return [line.strip() for line in text.split('\n') if line.strip()]


def parse_entries(page, models):
    """Split the page into dated entries and tag each with what makes it relevant."""
    blocks, current = [], None
    for line in page_lines(page):
        if DATE.match(line):
            if current:
                blocks.append(current)
            current = {'date': line, 'lines': []}
        elif current and not MONTH_HEADER.match(line):
            current['lines'].append(line)
    if current:
        blocks.append(current)

    entries = []
    for block in blocks:
        text = re.sub(r'\s+([.,:;)])', r'\1', ' '.join(block['lines']))
        low = text.lower()
        entries.append({
            'date': block['date'],
            'text': text,
            'caching': bool(CACHING.search(text)),
            # Exact model ids only: gpt-5.2 must not match gpt-5.2-codex.
            'models': [m for m in models if re.search(re.escape(m) + r'(?![\w.-])', low)],
            'hash': hashlib.sha256((block['date'] + text).encode()).hexdigest()[:16],
        })
    return entries


def is_relevant(entry):
    return entry['caching'] or bool(entry['models'])


def format_message(new):
    flagged = any('chat-latest' in e['models'] for e in new)
    if flagged:
        head = (':rotating_light: *`chat-latest` changed* on the OpenAI API changelog. '
                'Check coach prompt caching (PROD-7290).')
    else:
        noun = 'entry' if len(new) == 1 else 'entries'
        head = f':newspaper: {len(new)} OpenAI API changelog {noun} about prompt caching or a model we use.'
    lines = [head]
    for e in new:
        tags = ''.join(f' `{m}`' for m in e['models']) + (' `caching`' if e['caching'] else '')
        lines.append(f'• *{e["date"]}*{tags} — {e["text"][:600]}')
    lines.append(f'<{URL}|Open the changelog>')
    return '\n'.join(lines)


def http_get(url, headers=None, timeout=60):
    """GET through curl: stock macOS Python's urllib can't find the system CA bundle.

    Headers go to curl on stdin, so a token never appears in the process list.
    """
    header_text = ''.join(f'{k}: {v}\n' for k, v in (headers or {}).items())
    try:
        run = subprocess.run(['curl', '-sS', '-L', '--max-time', str(timeout), '-H', '@-',
                              '-w', '\n%{http_code}', url],
                             input=header_text, capture_output=True, text=True, encoding='utf-8',
                             errors='replace', timeout=timeout + 10, check=False)
    except (OSError, subprocess.SubprocessError) as err:
        raise ValueError(f'GET {url} failed: {err}') from err
    body, _, code = run.stdout.rpartition('\n')
    if run.returncode != 0 or not code.startswith('2'):
        raise ValueError(f'GET {url} failed: HTTP {code or "?"} {run.stderr.strip()[:200]}')
    return body


def our_models(api_base):
    """The OpenAI models IntelligenceService's live config uses. Raises ValueError when unavailable.

    No fallback list on purpose: a stale list silently drops entries about models added
    since, which is exactly the news this monitor exists to catch.
    """
    token = os.environ.get('AURA_API_TOKEN')
    if not token:
        raise ValueError('AURA_API_TOKEN is not set; it is needed to read the model list')
    body = http_get(api_base.rstrip('/') + MODELS_PATH, {'Authorization': 'Bearer ' + token, 'User-Agent': user_agent()}, timeout=30)
    try:
        models = sorted({m['id'] for m in json.loads(body).get('data', []) if m.get('id')})
    except (ValueError, AttributeError, TypeError) as err:
        raise ValueError(f'unexpected model list response: {err}') from err
    if not models:
        raise ValueError('the model list came back empty')
    return models


def post(text):
    """Post through the canonical helper, which adds the Tools/Slack footer."""
    try:
        res = post_message(CHANNEL, text, footer_skill=SKILL_ID)
    # slack_post exits with SystemExit on a missing token or a transport failure; catch it so
    # the run still prints its JSON status and keeps the entries unseen for the next run.
    except (Exception, SystemExit) as err:  # noqa: BLE001
        return {'ok': False, 'error': str(err) or type(err).__name__}
    return {'ok': bool(res.get('ok')), 'error': res.get('error'), 'ts': res.get('ts')}


def fetch():
    return http_get(URL, {'User-Agent': 'Mozilla/5.0'})


def load_seen(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding='utf-8') as f:
        return set(json.load(f))


def save_seen(path, seen):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(sorted(seen), f)


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False))


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--state', default=DEFAULT_STATE, help='seen-entries file (default: %(default)s)')
    ap.add_argument('--api-base', default=os.environ.get('AURA_BASE_URL', DEFAULT_API_BASE),
                    help='Aura services base URL for the model list (default: $AURA_BASE_URL or %(default)s)')
    ap.add_argument('--models', help='comma-separated model ids instead of asking the API (tests)')
    ap.add_argument('--html-file', help='read the page from a file instead of fetching it')
    ap.add_argument('--dry-run', action='store_true', help='print what would be posted; write nothing')
    args = ap.parse_args()

    try:
        page = open(args.html_file, encoding='utf-8').read() if args.html_file else fetch()
    except (OSError, ValueError) as err:
        return report_error(f'could not read the changelog: {err}', args.dry_run)
    try:
        models = args.models.split(',') if args.models else our_models(args.api_base)
    except ValueError as err:
        return report_error(str(err), args.dry_run)
    entries = parse_entries(page, models)
    if len(entries) < MIN_ENTRIES:
        return report_error(f'parsed only {len(entries)} entries; the page layout may have changed', args.dry_run)

    seen = load_seen(args.state)
    if seen is None and not args.dry_run:
        save_seen(args.state, {e['hash'] for e in entries})
        emit({'status': 'seeded', 'entries': len(entries), 'models': len(models)})
        return 0
    new = [e for e in entries if e['hash'] not in (seen or set()) and is_relevant(e)]
    if args.dry_run:
        emit({'status': 'dry-run', 'entries': len(entries), 'models': models,
              'new': [{k: e[k] for k in ('date', 'models', 'caching')} for e in new],
              'message': format_message(new) if new else None})
        return 0
    if new:
        res = post(format_message(new))
        if not res['ok']:
            # Keep the entries unseen so the next run retries the post.
            emit({'status': 'error', 'error': f'Slack post failed: {res["error"]}'})
            return 1
    save_seen(args.state, seen | {e['hash'] for e in entries})
    emit({'status': 'posted' if new else 'unchanged', 'entries': len(entries), 'new': len(new),
          'models': len(models), **({'ts': res['ts']} if new else {})})
    return 0


def report_error(message, dry_run):
    if dry_run:
        emit({'status': 'error', 'error': message})
        return 1
    res = post(f':warning: OpenAI changelog monitor: {message}. <{URL}|Open the changelog>')
    emit({'status': 'error', 'error': message, 'warned': res['ok']})
    return 1


if __name__ == '__main__':
    sys.exit(main())
