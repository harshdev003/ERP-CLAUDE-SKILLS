"""Verdict rules for `eng:verify-service-accounts`.

The skill exists because `verified: true` is not the same as "this identity can
do its job". During PROD-6468 the support-service token decoded fine and
resolved to a real, live, non-deleted account for the whole 14h45m outage —
what was broken was `role: null`. That branch is unreachable from live data
(every identity is healthy most of the time), so it is injected here.

The mirror trap is asserted too. `user-service` persists a token on first
successful verification and thereafter answers from its token table, a path
that returns `id` **without** `tokenUserId` — the shape 26 of 28 real service
identities come back as. A check requiring `tokenUserId == id` therefore fails
every long-lived service account while the accounts are perfectly healthy, so
`TestPersistedTokenShape` pins that the pair is compared only when the server
sent both.
"""

import os
import sys
import time
import unittest

sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "plugins/eng/skills/verify-service-accounts/scripts",
    ),
)

import verify  # noqa: E402

HOUR = 3600


def row(service="support-service", **over):
    """A healthy identity in the shape `probe()` returns, before `judge()`."""
    base = {
        "service": service,
        "group": "services",
        "env_file": f"k8s/apps/services/{service}/prod/.env.prod",
        "fingerprint": "abc123abc123",
        "claims": {
            "shape": "jwt",
            "email": f"{service}@services.aurahealth.io",
            "aud": "firebase-auratech16",
            "sub": "UID000",
            "iat": int(time.time()) - HOUR,
            "exp": int(time.time()) + HOUR,
        },
        "verify": {
            "status": 200,
            "error": None,
            "verified": True,
            "id": "UID000",
            "tokenUserId": None,
        },
        "account": {
            "status": 200,
            "error": None,
            "id": "UID000",
            "role": "service",
            "email": f"{service}@services.aurahealth.io",
            "deletedAt": None,
        },
        "sealed": ("present", None),
    }
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = {**base[key], **value}
        else:
            base[key] = value
    return base


def judged(service="support-service", expected=None, baseline=None, **over):
    return verify.judge(row(service, **over), expected, baseline)


class TestHealthy(unittest.TestCase):
    def test_a_healthy_identity_is_ok(self):
        self.assertEqual(judged()["verdict"], "OK")


class TestRoleIsAsserted(unittest.TestCase):
    """The PROD-6468 fault: authenticates fine, authorizes nothing."""

    def test_null_role_fails_even_though_the_token_verifies(self):
        result = judged(account={"role": None})
        self.assertEqual(result["verdict"], "FAIL")
        self.assertTrue(result["verify"]["verified"], "the token still verifies")
        self.assertTrue(any("role" in f for f in result["fails"]))

    def test_a_wrong_role_fails(self):
        self.assertEqual(judged(account={"role": "user"})["verdict"], "FAIL")

    def test_a_soft_deleted_account_fails(self):
        result = judged(account={"deletedAt": "2026-09-22T21:59:36Z"})
        self.assertEqual(result["verdict"], "FAIL")


class TestPersistedTokenShape(unittest.TestCase):
    """The absent `tokenUserId` must not read as a fault."""

    def test_absent_token_user_id_is_not_a_failure(self):
        result = judged(verify={"tokenUserId": None})
        self.assertEqual(result["verdict"], "OK")

    def test_a_present_but_mismatched_token_user_id_fails(self):
        result = judged(verify={"tokenUserId": "SOMEONE_ELSE"})
        self.assertEqual(result["verdict"], "FAIL")

    def test_a_present_and_matching_token_user_id_is_ok(self):
        self.assertEqual(judged(verify={"tokenUserId": "UID000"})["verdict"], "OK")


class TestExpiryIsInformational(unittest.TestCase):
    """A past `exp` is the normal state, not a fault.

    26 of 28 real identities carry an `exp` from 2021-2023 and every one of them
    works, because the persisted path never re-checks it. Failing on expiry
    would report a fleet-wide outage that is not happening.
    """

    def test_a_long_expired_token_is_still_ok(self):
        result = judged(claims={"exp": int(time.time()) - 1573 * 86400})
        self.assertEqual(result["verdict"], "OK")
        self.assertEqual(result["fails"], [])
        self.assertTrue(any("exp" in n for n in result["notes"]), "but it is reported")

    def test_expiry_does_not_mask_a_real_fault(self):
        result = judged(claims={"exp": 1}, account={"role": None})
        self.assertEqual(result["verdict"], "FAIL")


class TestIdentityBinding(unittest.TestCase):
    """A token stored under the wrong service's directory."""

    def test_a_cross_wired_token_fails(self):
        result = verify.judge(
            row(
                "payment-service",
                account={
                    "email": "<redacted-email>",
                    "id": "UID999",
                },
                verify={"id": "UID999"},
            ),
            None,
            None,
        )
        self.assertEqual(result["verdict"], "FAIL")
        self.assertTrue(any("identity mismatch" in f for f in result["fails"]))

    def test_the_two_probes_must_agree_on_the_uid(self):
        result = judged(account={"id": "UID111"})
        self.assertEqual(result["verdict"], "FAIL")

    def test_a_baseline_uid_change_fails(self):
        result = verify.judge(row(), None, {"support-service": "OLD_UID"})
        self.assertEqual(result["verdict"], "FAIL")
        self.assertTrue(any("UID changed" in f for f in result["fails"]))

    def test_a_matching_baseline_is_ok(self):
        result = verify.judge(row(), None, {"support-service": "UID000"})
        self.assertEqual(result["verdict"], "OK")


class TestUnusableCredential(unittest.TestCase):
    def test_verified_false_fails(self):
        result = judged(
            verify={"verified": False, "id": None},
            account={"status": 403, "id": None, "role": None, "email": None},
        )
        self.assertEqual(result["verdict"], "FAIL")

    def test_a_403_is_reported_as_a_refusal_not_as_unreachable(self):
        result = judged(account={"status": 403, "id": None, "role": None, "email": None})
        self.assertTrue(any("403" in f for f in result["fails"]))
        self.assertFalse(any("unreachable" in f for f in result["fails"]))

    def test_a_failing_endpoint_is_not_blamed_on_the_token(self):
        """verifyToken answers 200 for both verdicts, so a 502 is the endpoint."""
        result = judged(verify={"status": 502, "verified": None, "id": None})
        self.assertEqual(result["verdict"], "FAIL")
        self.assertTrue(any("endpoint is failing" in f for f in result["fails"]))
        self.assertFalse(any("resolves to no account" in f for f in result["fails"]))

    def test_a_transport_error_fails(self):
        result = judged(verify={"status": None, "error": "timeout", "verified": None, "id": None})
        self.assertEqual(result["verdict"], "FAIL")
        self.assertTrue(any("unreachable" in f for f in result["fails"]))


class TestDriftIsAWarningNotAFailure(unittest.TestCase):
    """Drift means the record is inconsistent, not that the identity is down."""

    def test_a_missing_committed_sealed_secret_warns(self):
        result = judged(sealed=("missing", "no committed *-secret.json beside the .env file"))
        self.assertEqual(result["verdict"], "WARN")

    def test_a_live_secret_differing_from_the_env_file_warns(self):
        result = judged(cluster={"fingerprint": "deadbeefdead", "error": None})
        self.assertEqual(result["verdict"], "WARN")

    def test_a_matching_live_secret_is_ok(self):
        result = judged(cluster={"fingerprint": "abc123abc123", "error": None})
        self.assertEqual(result["verdict"], "OK")


class TestNoTokenIsEverPrinted(unittest.TestCase):
    """The rendered table and the per-identity detail carry fingerprints only."""

    def test_the_rendered_report_contains_no_token(self):
        rendered = verify.render("prod", [judged(), judged("feed-service")])
        self.assertNotIn("eyJ", rendered)
        self.assertIn("UID000", rendered)

    def test_probe_drops_the_token_from_its_result(self):
        captured = {}

        def fake(url, timeout, headers=None, body=None):
            captured[url] = (headers, body)
            return 200, {"verified": True, "id": "UID000", "role": "service"}, None

        original, verify.http_json = verify.http_json, fake
        try:
            result = verify.probe(
                {
                    "service": "feed-service",
                    "group": "services",
                    "env_file": "x/.env.prod",
                    "token": "eyJ-secret-token-value",
                },
                "https://example.invalid",
                5,
            )
        finally:
            verify.http_json = original

        self.assertNotIn("token", result)
        self.assertNotIn("eyJ-secret-token-value", repr(result))
        self.assertEqual(result["fingerprint"], verify.fingerprint("eyJ-secret-token-value"))


if __name__ == "__main__":
    unittest.main()


class TestExpectedRoleIsPerIdentity(unittest.TestCase):
    """One global expected role reports a healthy `system` account as broken.

    Aura files service identities under the sub-domain their account belongs
    to, and the role follows it: the thirteen `@services.*` accounts carry
    `service`, and `<redacted-email>` legitimately carries
    `system`. Holding all fourteen to `service` is a false positive on a live,
    correctly-configured identity.
    """

    def emotion_system(self, **over):
        base = {
            "account": {
                "role": "system",
                "email": "<redacted-email>",
            },
            "claims": {"email": "<redacted-email>"},
        }
        for key, value in over.items():
            base[key] = {**base.get(key, {}), **value} if isinstance(value, dict) else value
        return judged("emotion-system", **base)

    def test_a_systems_account_expects_the_system_role(self):
        result = self.emotion_system()
        self.assertEqual(result["expected_role"], "system")
        self.assertEqual(result["verdict"], "OK")

    def test_a_services_account_still_expects_service(self):
        self.assertEqual(judged()["expected_role"], "service")

    def test_a_systems_account_with_the_service_role_fails(self):
        self.assertEqual(self.emotion_system(account={"role": "service"})["verdict"], "FAIL")

    def test_the_expectation_survives_an_unreachable_account(self):
        """A 403 leaves no account email, so the JWT's own claim carries it."""
        result = judged(
            "emotion-system",
            claims={"email": "<redacted-email>"},
            account={"status": 403, "id": None, "role": None, "email": None},
        )
        self.assertEqual(result["expected_role"], "system")

    def test_the_dev_address_resolves_the_same_as_prod(self):
        """Dev addresses carry an extra label: `@systems.dev.aurahealth.io`.

        Both emotion-system accounts are live and carry `system` — prod
        `Vq4GsXRqUPT8Oo0fylrXogMYcDS2`, dev `aRe7NVIq1aX2t6tTMh4pX3S9sq93` — so
        the sub-domain has to be read from the first label, not the
        second-to-last, which would answer `dev` here and `aurahealth` in prod.
        """
        for email in (
            "<redacted-email>",
            "<redacted-email>",
        ):
            with self.subTest(email=email):
                result = judged(
                    "emotion-system",
                    account={"role": "system", "email": email},
                    claims={"email": email},
                )
                self.assertEqual(result["expected_role"], "system")
                self.assertEqual(result["verdict"], "OK")

    def test_the_dev_services_address_still_resolves_to_service(self):
        email = "<redacted-email>"
        result = judged("report-service", account={"email": email}, claims={"email": email})
        self.assertEqual(result["expected_role"], "service")

    def test_an_unknown_sub_domain_falls_back_to_service(self):
        result = judged(
            "odd-service",
            account={"email": "odd-service@example.com"},
            claims={"email": "odd-service@example.com"},
        )
        self.assertEqual(result["expected_role"], "service")

    def test_an_explicit_flag_overrides_every_identity(self):
        self.assertEqual(self.emotion_system(expected="service")["verdict"], "FAIL")

    def test_a_pinned_baseline_role_beats_the_sub_domain(self):
        result = judged(baseline={"support-service": {"uid": "UID000", "role": "admin"}})
        self.assertEqual(result["expected_role"], "admin")
        self.assertEqual(result["verdict"], "FAIL")


class TestBaselineEntryShapes(unittest.TestCase):
    def test_a_bare_uid_string_is_read_as_the_uid(self):
        self.assertEqual(verify.baseline_entry({"s": "UID9"}, "s"), {"uid": "UID9"})

    def test_an_object_entry_is_returned_whole(self):
        entry = {"uid": "UID9", "role": "service"}
        self.assertEqual(verify.baseline_entry({"s": entry}, "s"), entry)

    def test_a_missing_service_is_empty(self):
        self.assertEqual(verify.baseline_entry({}, "s"), {})
        self.assertEqual(verify.baseline_entry(None, "s"), {})

    def test_a_pinned_uid_object_still_catches_a_uid_change(self):
        result = judged(baseline={"support-service": {"uid": "OLD", "role": "service"}})
        self.assertTrue(any("UID changed" in f for f in result["fails"]))
