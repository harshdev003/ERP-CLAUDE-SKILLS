"""Every raw Mixpanel ``/engage`` write in the repo disables request-IP geolocation.

Mixpanel's ``/engage`` ingestion endpoint defaults to ``ip=1``: the profile's
``$city`` / ``$region`` / ``$country_code`` / ``$timezone`` are re-derived from
the IP of whoever sent the request. From a laptop that is the author's home ISP;
from a pod it is the cluster egress. ``mixpanel-node`` hardcodes ``ip=0`` on
every request, so the service wrappers are safe — the hole is a hand-rolled
``POST https://api.mixpanel.com/engage`` in a script, which is exactly how
2,063,384 profiles were stamped West Vancouver, BC on 2026-09-11 (PROD-6322).

The rule this pins: any occurrence of the *ingestion* URL in a script carries
``ip=0`` (query string) or ``$ip`` (payload) somewhere in the same file. The
*query* API (``mixpanel.com/api/query/engage``, ``/api/2.0/engage``) is a read
and is not in scope.

The detector is checked against a fixture in the shape of the 2026-09-11
script — a guard that cannot find the incident it exists for is worth nothing.
"""

import pathlib
import re
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = REPO / "plugins"

# The ingestion host only. Reads go to mixpanel.com/api/... and never set geo.
INGEST_ENGAGE = re.compile(r"api\.mixpanel\.com/engage")
# `ip=0` as a whole token (not `skip=0`), or `$ip` set to 0 (not merely mentioned,
# and not `$ip: 1`, which would geolocate on purpose).
GUARD = re.compile(r"(?<![\w$])ip=0(?!\d)|\$ip[\"']?\s*[:=]\s*0(?!\d)")
SCANNED_SUFFIXES = {".py", ".sh", ".js", ".mjs", ".ts"}


def find_unguarded_engage_writes(root):
    """Return ``[(relative_path, line_no)]`` for every ingestion-URL occurrence
    in a file that nowhere disables geolocation."""
    hits = []
    for path in sorted(root.rglob("*")):
        if path.suffix not in SCANNED_SUFFIXES or not path.is_file():
            continue
        if "node_modules" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if not INGEST_ENGAGE.search(text) or GUARD.search(text):
            continue
        for no, line in enumerate(text.splitlines(), 1):
            if INGEST_ENGAGE.search(line):
                hits.append((str(path.relative_to(root)), no))
    return hits


# The 2026-09-11 backfill, reduced to the lines that mattered.
INCIDENT_SHAPE = """
import json, urllib.request, urllib.parse
def write_batch(ids):
    payload = [{'$token': TOKEN, '$distinct_id': i, '$ignore_time': True,
                '$set': {'$iterable_user_id': i}} for i in ids]
    body = urllib.parse.urlencode({'data': json.dumps(payload)}).encode()
    req = urllib.request.Request('https://api.mixpanel.com/engage', data=body)
    return urllib.request.urlopen(req, timeout=300).read()
"""

GUARDED_SHAPE = INCIDENT_SHAPE.replace(
    "https://api.mixpanel.com/engage", "https://api.mixpanel.com/engage?ip=0"
)

READ_ONLY_SHAPE = """
import urllib.request
req = urllib.request.Request('https://mixpanel.com/api/query/engage?project_id=1')
"""


class TestDetector(unittest.TestCase):
    def _scan(self, **files):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            for name, text in files.items():
                (root / name).write_text(text)
            return find_unguarded_engage_writes(root)

    def test_flags_the_incident_shape(self):
        hits = self._scan(**{"backfill.py": INCIDENT_SHAPE})
        self.assertEqual([h[0] for h in hits], ["backfill.py"])

    def test_ip0_in_the_query_string_is_a_guard(self):
        self.assertEqual(self._scan(**{"backfill.py": GUARDED_SHAPE}), [])

    def test_dollar_ip_in_the_payload_is_a_guard(self):
        shape = INCIDENT_SHAPE.replace("'$ignore_time': True", "'$ignore_time': True, '$ip': 0")
        self.assertEqual(self._scan(**{"backfill.py": shape}), [])

    def test_lookalike_tokens_are_not_guards(self):
        for decoy in ("skip=0", "'$ip_address': ip", "'$ip': 1", "$ip"):
            shape = INCIDENT_SHAPE.replace("'$ignore_time': True", f"'$ignore_time': True, # {decoy}")
            with self.subTest(decoy=decoy):
                self.assertEqual([h[0] for h in self._scan(**{"backfill.py": shape})], ["backfill.py"])

    def test_the_query_api_is_a_read_and_out_of_scope(self):
        self.assertEqual(self._scan(**{"lookup.py": READ_ONLY_SHAPE}), [])

    def test_node_modules_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "node_modules").mkdir()
            (root / "node_modules" / "x.js").write_text("fetch('https://api.mixpanel.com/engage')")
            self.assertEqual(find_unguarded_engage_writes(root), [])


class TestRepo(unittest.TestCase):
    def test_no_unguarded_engage_write_in_plugins(self):
        hits = find_unguarded_engage_writes(PLUGINS)
        self.assertEqual(
            hits,
            [],
            "raw /engage write without ip=0 or $ip — Mixpanel will geolocate the "
            "profile from the sender's IP (PROD-6322): "
            + ", ".join(f"{p}:{n}" for p, n in hits),
        )


if __name__ == "__main__":
    unittest.main()
