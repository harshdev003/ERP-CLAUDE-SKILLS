#!/usr/bin/env python3
"""Block IP ranges at Google's edge for an incident, with an expiry.

    armor_blocklist.py list [--json]
    armor_blocklist.py add --ranges <ip>,<cidr> | --ranges-file plan.json --expires 2026-12-01 \\
        --incident <Security Incident Log page URL> --reason "credential stuffing on /login" [--apply]
    armor_blocklist.py remove --priority 100 [--apply]
    armor_blocklist.py expire [--apply]

Incident blocks are deny(403) rules at priorities 100-149 in aura-web-edge, up to 10 ranges each.
Each rule is ONE row in the Notion "Cloud Armor Blocklist" database, listing every range, linked to
its Security Incident Log row, and each add, removal, expiry or extension is ONE row in the Security
Change Log, which never names a range (ENG-1759: never one row per IP). Residential, CGNAT and cloud addresses get reassigned to real users, so
every block carries an expiry and `expire` removes the ones past it. Without --apply every command
is a dry run.

Adapted from AuraInfra bin/armor-blocklist.py (AuraInfra#162): gcloud runs as GCP_KEY through
gcloud_sa, Notion goes through `requests`, the commands are importable, and nothing printed names an
address (a range is named by its position; the addresses stay in the Incident Log and Blocklist).
"""
import argparse
import datetime
import ipaddress
import json
import os
import re
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gcloud_sa  # noqa: E402

PROJECT = 'firebase-auratech16'
POLICY = 'aura-web-edge'
FIRST, LAST = 100, 149
MAX_RANGES = 10
MAX_DAYS = 90
BLOCKLIST_DS = '69c410c7-1621-46e4-9690-1d52c5ca8d6d'
INCIDENT_DS = '40175e23af2081978d7e000b96ac48bf'
CHANGELOG_DS = '3f775e23-af20-8176-9d69-000b45a0ebde'
PAGE_ID = re.compile(
    r'(?:^|-)([0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$')
# Cloud Armor caps a rule description at 64 characters: 'incident exp=' plus
# the date and the 32-hex Incident Log page id comes to 56.
DESC = re.compile(r'^incident exp=(\d{4}-\d{2}-\d{2}) ([0-9a-f]{32})$')


class Refused(SystemExit):
    pass


def utc_today():
    """Expiries are UTC dates (`Expires (UTC)`): compare against the UTC date, never the host's."""
    return datetime.datetime.now(datetime.timezone.utc).date()


def gcloud(*args):
    out = gcloud_sa.run(['compute', 'security-policies', *args, '--project', PROJECT],
                        capture_output=True, text=True, encoding='utf-8', errors='replace')
    if out.returncode:
        raise RuntimeError('gcloud %s failed as %s: %s' % (
            args[0], gcloud_sa.credential(), (out.stderr or '').strip()[-300:]))
    return out.stdout


def incident_rules():
    """The live incident rules: priority, ranges, expiry, Incident Log row id, preview."""
    policy = json.loads(gcloud('describe', POLICY, '--format', 'json'))
    # describe returns a one-element list for a global policy.
    if isinstance(policy, list):
        policy = policy[0]
    rules = []
    for r in policy.get('rules', []):
        if FIRST <= r['priority'] <= LAST:
            m = DESC.match(r.get('description', ''))
            rules.append({
                'priority': r['priority'],
                'ranges': r.get('match', {}).get('config', {}).get('srcIpRanges', []),
                'expires': m.group(1) if m else None,
                'row': m.group(2) if m else None,
                'preview': r.get('preview', False),
            })
    return rules


def check_ranges(raw):
    """Public, /16 or narrower (IPv4) or /48 (IPv6), 1-10 per rule. Errors name the position only."""
    items = [str(x).strip() for x in (raw.split(',') if isinstance(raw, str) else raw) if str(x).strip()]
    ranges = []
    for i, item in enumerate(items, 1):
        try:
            net = ipaddress.ip_network(item, strict=False)
        except ValueError:
            raise Refused('range #%d is not an address or CIDR' % i)
        if not net.is_global:
            raise Refused('range #%d is not a public address range' % i)
        if (net.version == 4 and net.prefixlen < 16) or (net.version == 6 and net.prefixlen < 48):
            raise Refused('range #%d (/%d) is too broad; use /16 or narrower for IPv4, /48 for IPv6'
                          % (i, net.prefixlen))
        ranges.append(str(net))
    if not 1 <= len(ranges) <= MAX_RANGES:
        raise Refused('a rule takes 1 to %d ranges, got %d' % (MAX_RANGES, len(ranges)))
    return ranges


def notion(method, path, body=None):
    """Notion REST, or None without NOTION_TOKEN."""
    token = os.environ.get('NOTION_TOKEN')
    if not token:
        return None
    import requests
    r = requests.request(method, 'https://api.notion.com/v1' + path, timeout=60,
                         data=json.dumps(body) if body is not None else None,
                         headers={'Authorization': 'Bearer ' + token, 'Notion-Version': '2025-09-03',
                                  'Content-Type': 'application/json'})
    if r.status_code >= 400:
        raise RuntimeError('Notion %s %s -> %d' % (method, path.split('?')[0], r.status_code))
    return r.json()


def page_id(url):
    """The incident page id from a Notion link.

    A peek link from the database view is <database id>?v=...&p=<page id>, so
    the p parameter wins over the path.
    """
    parts = urllib.parse.urlsplit(url)
    peek = urllib.parse.parse_qs(parts.query).get('p')
    # The id ends the last path segment, after the title: hyphens inside the
    # title must not be stripped, or a title ending in hex would merge into it.
    for candidate in (peek[0] if peek else '', parts.path.rstrip('/').rsplit('/', 1)[-1]):
        m = PAGE_ID.search(candidate)
        if m:
            return m.group(1).replace('-', '')
    raise Refused('cannot read a Notion page id from --incident')


def check_incident(incident):
    """Fail before any rule is created if the incident row is not readable."""
    if not os.environ.get('NOTION_TOKEN'):
        raise Refused('NOTION_TOKEN not set: a block needs a readable Security Incident Log row')
    try:
        page = notion('GET', '/pages/%s' % incident)
    except RuntimeError as e:
        raise Refused('cannot read the incident page (%s); check --incident' % e)
    parent = page.get('parent', {})
    if parent.get('data_source_id', '').replace('-', '') != INCIDENT_DS:
        raise Refused('--incident is not a Security Incident Log row')
    return page


def blocklist_rows():
    """Every Blocklist row: {id, priority, status, expires, incident}. Range and Reason are not read."""
    out, body = [], {'page_size': 100}
    while True:
        q = notion('POST', '/data_sources/%s/query' % BLOCKLIST_DS, body)
        if q is None:
            return None
        for r in q.get('results', []):
            p = r.get('properties', {})
            prio = (p.get('Rule priority') or {}).get('number')
            out.append({'id': r['id'],
                        'priority': int(prio) if prio is not None else None,
                        'status': ((p.get('Status') or {}).get('select') or {}).get('name'),
                        'policy': ((p.get('Policy') or {}).get('select') or {}).get('name'),
                        'expires': ((p.get('Expires (UTC)') or {}).get('date') or {}).get('start'),
                        'incident': [x['id'] for x in (p.get('Incident') or {}).get('relation') or []]})
        if not q.get('has_more'):
            return out
        body['start_cursor'] = q['next_cursor']


def cmd_list(a):
    today = utc_today().isoformat()
    rules = incident_rules()
    if getattr(a, 'json', False):
        print(json.dumps([{k: v for k, v in r.items() if k != 'ranges'} | {'n_ranges': len(r['ranges'])}
                          for r in rules], indent=1))
        return
    if not rules:
        print('no incident blocks')
    for r in rules:
        state = 'EXPIRED' if r['expires'] and r['expires'] < today else 'active'
        if not r['expires']:
            state = 'NO-EXPIRY'
        print('%d  %-9s  expires=%s  ranges=%d%s  row=%s' % (
            r['priority'], state, r['expires'], len(r['ranges']),
            '  (preview)' if r['preview'] else '', r['row']))


def plan_add(ranges, expires, incident, live):
    """(priority, description) for a new rule, or Refused. `live` is incident_rules()."""
    today = utc_today()
    if not today < expires <= today + datetime.timedelta(days=MAX_DAYS):
        raise Refused('--expires must be within the next %d days' % MAX_DAYS)
    for r in live:
        for i, net in enumerate(ranges, 1):
            new = ipaddress.ip_network(net)
            if any(new.version == old.version and new.overlaps(old)
                   for old in map(ipaddress.ip_network, r['ranges'])):
                raise Refused('range #%d is already blocked by rule %d (expires %s)'
                              % (i, r['priority'], r['expires']))
    taken = {r['priority'] for r in live}
    free = [p for p in range(FIRST, LAST + 1) if p not in taken]
    if not free:
        raise Refused('all incident priorities %d-%d are in use; run expire first' % (FIRST, LAST))
    return free[0], 'incident exp=%s %s' % (expires.isoformat(), incident)


def read_ranges(a):
    """--ranges, or the `ranges` of a preflight plan (--ranges-file), so no address is typed or shown."""
    if a.ranges_file:
        with open(a.ranges_file, encoding='utf-8') as fh:
            return check_ranges(json.load(fh)['ranges'])
    return check_ranges(a.ranges)


def cmd_add(a):
    ranges = read_ranges(a)
    expires = datetime.date.fromisoformat(a.expires)
    incident = page_id(a.incident)
    check_incident(incident)
    prio, desc = plan_add(ranges, expires, incident, incident_rules())
    print('rule %d: deny(403) %d range(s), %s' % (prio, len(ranges), desc))
    if not a.apply:
        print('dry run; pass --apply to create it')
        return prio
    gcloud('rules', 'create', str(prio), '--security-policy', POLICY,
           '--src-ip-ranges', ','.join(ranges), '--action', 'deny-403', '--description', desc)
    now = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    change_log('added', prio, incident, 'blocked %d range(s), expires %s' % (len(ranges), expires.isoformat()),
               card=a.card, expires=expires.isoformat(), at=now[:16])
    try:
        record(prio, ranges, incident, expires, now, a)
    except RuntimeError as e:
        # The rule is live; a re-run would refuse the same ranges, so say
        # exactly what is left to do.
        raise Refused('rule %d is live, but writing the Blocklist rows failed (%s): add them by hand'
                      % (prio, e))
    print('rule %d created; 1 Blocklist row written for %d range(s)' % (prio, len(ranges)))
    return prio


def record(prio, ranges, incident, expires, now, a):
    """One Blocklist row for the rule: every range in its Range list. Lift one range early by editing
    that list on the row and noting it, never by adding rows."""
    notion('POST', '/pages', {
        'parent': {'type': 'data_source_id', 'data_source_id': BLOCKLIST_DS},
        'properties': {
            'Range': {'title': [{'text': {'content': ', '.join(ranges)}}]},
            'Status': {'select': {'name': 'Active'}},
            'Incident': {'relation': [{'id': incident}]},
            'Policy': {'select': {'name': POLICY}},
            'Rule priority': {'number': prio},
            'Added (UTC)': {'date': {'start': now}},
            'Expires (UTC)': {'date': {'start': expires.isoformat()}},
            'Proposed by': {'rich_text': [{'text': {'content': a.proposed_by}}]},
            'Approved by': {'rich_text': [{'text': {'content': a.approved_by}}]},
            'Reason': {'rich_text': [{'text': {'content': a.reason[:200]}}]},
        }})


def change_log(action, prio, incident, what, card=None, expires=None, at=None):
    """One Security Change Log row per block action, keyed policy/priority/action/incident/expiry/time so
    a re-run of the same write records nothing while every real action gets its own row: an add after an
    early removal reuses the priority and can reuse the expiry, so the minute the rule changed (`at`) is in
    the key. Extensions pass no `at`: re-extending to the same date is the same action. Never a range or an address: those live only in the Incident Log and Blocklist.
    A failure is reported, never raised: the Cloud Armor change has already happened."""
    # The whole incident id: this workspace's Incident Log ids share their first characters.
    key = '%s/%d/%s/%s/%s%s' % (POLICY, prio, action, (incident or 'none').replace('-', ''), expires or 'no-expiry',
                                ('/' + at) if at else '')
    try:
        found = notion('POST', '/data_sources/%s/query' % CHANGELOG_DS, {
            'filter': {'property': 'Time source', 'rich_text': {'contains': key}}, 'page_size': 1})
        if found is None:
            print('NOTION_TOKEN not set: add the Security Change Log row for rule %d %s by hand' % (prio, action))
            return False
        if found.get('results'):
            print('Security Change Log row already written (%s)' % key)
            return False
        title = incident_title(incident)
        now = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        props = {
            'Change': {'title': [{'text': {'content': 'Cloud Armor %s rule %d: %s, %s for incident "%s"'
                                           % (POLICY, prio, action, what, title)}}]},
            'Component': {'rich_text': [{'text': {'content': 'Cloud Armor %s' % POLICY}}]},
            'System': {'select': {'name': 'Infrastructure'}},
            'Env': {'multi_select': [{'name': 'prod'}]},
            'User-facing': {'select': {'name': 'No'}},
            'Live (UTC)': {'date': {'start': now}},
            'Card': {'rich_text': [{'text': {'content': card or ''}}]},
            'Time source': {'rich_text': [{'text': {'content': 'armor_blocklist.py at %s; key %s' % (now, key)}}]},
        }
        if incident:
            props['PR / release'] = {'url': 'https://app.notion.com/p/%s' % incident.replace('-', '')}
        notion('POST', '/pages', {'parent': {'type': 'data_source_id', 'data_source_id': CHANGELOG_DS},
                                  'properties': props})
        print('Security Change Log row written (rule %d %s)' % (prio, action))
        return True
    except RuntimeError as e:
        print('Security Change Log row not written (%s): add it by hand for rule %d %s' % (e, prio, action))
        return False


def incident_title(incident):
    if not incident:
        return 'unknown incident'
    try:
        page = notion('GET', '/pages/%s' % incident) or {}
    except RuntimeError:
        return incident
    for prop in page.get('properties', {}).values():
        if prop.get('type') == 'title':
            return ''.join(x.get('plain_text', '') for x in prop.get('title', []))[:120] or incident
    return incident


def mark_rows(prio, props):
    """Patch every Active Blocklist row of rule `prio`; returns how many."""
    res = notion('POST', '/data_sources/%s/query' % BLOCKLIST_DS, {'filter': {'and': [
        {'property': 'Rule priority', 'number': {'equals': prio}},
        {'property': 'Status', 'select': {'equals': 'Active'}}]}})
    if res is None:
        print('NOTION_TOKEN not set: update the Blocklist rows for rule %d by hand' % prio)
        return 0
    for page in res.get('results', []):
        notion('PATCH', '/pages/%s' % page['id'], {'properties': props})
    return len(res.get('results', []))


def mark_removed(prio, status):
    now = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    return mark_rows(prio, {'Status': {'select': {'name': status}}, 'Removed (UTC)': {'date': {'start': now}}})


def remove(prio, status, apply, incident=None, expires=None):
    print('remove rule %d (%s)' % (prio, status))
    if apply:
        gcloud('rules', 'delete', str(prio), '--security-policy', POLICY, '--quiet')
        # The Change Log first: the rule is gone, so a remove can't be re-run, and a Blocklist error
        # below must not cost the audit row.
        now = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M')
        change_log(status.lower(), prio, incident, 'block removed (%s)' % status.lower(), expires=expires, at=now)
        mark_removed(prio, status)


def cmd_remove(a):
    if not FIRST <= a.priority <= LAST:
        raise Refused('incident blocks use priorities %d-%d' % (FIRST, LAST))
    live = {r['priority']: r for r in incident_rules()}
    if a.priority not in live:
        raise Refused('no incident block at priority %d' % a.priority)
    remove(a.priority, 'Removed', a.apply, live[a.priority]['row'], live[a.priority]['expires'])
    if not a.apply:
        print('dry run; pass --apply to remove it')


def cmd_expire(a):
    today = utc_today().isoformat()
    stale = [r for r in incident_rules() if r['expires'] and r['expires'] < today]
    if not stale:
        print('nothing has expired')
    for r in stale:
        remove(r['priority'], 'Expired', a.apply, r['row'], r['expires'])
    if stale and not a.apply:
        print('dry run; pass --apply to remove them')


def build_parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)
    ls = sub.add_parser('list')
    ls.add_argument('--json', action='store_true')
    add = sub.add_parser('add')
    src = add.add_mutually_exclusive_group(required=True)
    src.add_argument('--ranges', help='comma-separated IPs or CIDRs')
    src.add_argument('--ranges-file', help="a preflight.py plan (JSON with 'ranges')")
    add.add_argument('--expires', required=True, help=f'YYYY-MM-DD, at most {MAX_DAYS} days out')
    add.add_argument('--incident', required=True, help='Security Incident Log page URL')
    add.add_argument('--reason', required=True, help='short, no personal data')
    add.add_argument('--proposed-by', default='Security')
    add.add_argument('--approved-by', default='Jerome')
    add.add_argument('--card', default='', help='the Linear card for the Change Log row')
    add.add_argument('--apply', action='store_true')
    rm = sub.add_parser('remove')
    rm.add_argument('--priority', type=int, required=True)
    rm.add_argument('--apply', action='store_true')
    ex = sub.add_parser('expire')
    ex.add_argument('--apply', action='store_true')
    return p


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    a = build_parser().parse_args(argv)
    try:
        {'list': cmd_list, 'add': cmd_add, 'remove': cmd_remove, 'expire': cmd_expire}[a.cmd](a)
    except RuntimeError as e:
        print('ERROR: %s' % e, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
