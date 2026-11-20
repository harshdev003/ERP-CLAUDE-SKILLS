"""The product-list reads ask PaymentService for every row (`limit` 0).

With no limit, PaymentService's paged product lists return one page of 100, and Stripe alone has
200+ products: a skill reading without `limit` 0 would see a cut list and call a product missing.
`urlopen` is stubbed at each script's one chokepoint, so the test sees the request each script
actually builds, through its own `main()` or fetch function.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import pathlib
import sys
import unittest
import urllib.parse
from unittest import mock

SKILLS = pathlib.Path(__file__).resolve().parents[1] / "plugins/eng/skills"


def _load(rel: str):
    path = SKILLS / rel
    spec = importlib.util.spec_from_file_location(f"plist_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Resp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _capture(mod, payload=b"[]"):
    """Stub mod's urlopen; return the list the requests land in."""
    seen = []

    def fake(req, **_kw):
        seen.append(req)
        return _Resp(payload)

    return seen, mock.patch.object(mod.urllib.request, "urlopen", side_effect=fake)


def _products(seen):
    return [r for r in seen if urllib.parse.urlsplit(r.full_url).path.startswith("/products")]


class PostListsSendLimitZero(unittest.TestCase):
    def _run_main(self, rel, argv):
        mod = _load(rel)
        seen, patch = _capture(mod)
        with patch, mock.patch.dict(os.environ, {"AURA_API_TOKEN": "t"}), \
                mock.patch.object(sys, "argv", ["x", *argv]), contextlib.redirect_stdout(io.StringIO()):
            mod.main()
        reqs = _products(seen)
        self.assertEqual(len(reqs), 1)
        self.assertEqual(urllib.parse.urlsplit(reqs[0].full_url).path, "/products/list")
        self.assertEqual(json.loads(reqs[0].data), {"type": "ai", "limit": 0})

    def test_manage_ai_packages_catalogue(self):
        self._run_main("manage-ai-packages/scripts/fetch_catalogue.py", ["--json"])

    def test_remove_package_experiment(self):
        self._run_main("remove-package-experiment/scripts/fetch_experiment.py",
                       ["--experiment", "x", "--json"])


class ProviderListSendsLimitZero(unittest.TestCase):
    def test_investigate_missing_premium(self):
        mod = _load("investigate-missing-premium/scripts/investigate.py")
        seen, patch = _capture(mod)
        with patch:
            mod.fetch_products("stripe", "t")
        (req,) = _products(seen)
        url = urllib.parse.urlsplit(req.full_url)
        self.assertEqual(url.path, "/products/stripe")
        self.assertEqual(urllib.parse.parse_qs(url.query), {"limit": ["0"]})


if __name__ == "__main__":
    unittest.main()
