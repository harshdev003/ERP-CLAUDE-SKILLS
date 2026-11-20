#!/usr/bin/env python3
"""Spending OpenAI calls in the QA/eval skills default to the flex tier.

The defect this guards
----------------------
An OMITTED `service_tier` bills Standard, so the expensive choice is the one you
get by writing nothing. QA and eval inference is batch-shaped — nobody waits on
a sweep, a judge pass, or a self-critique — and flex is priced at half Standard,
so every one of those dollars was double what it needed to be.
`eng:report-prompt-testing-cost` measured ~$837 in one day from
`eng:qa-conversation` alone (ENG-1171 moved that spend to `Evaluations
[Production]`, which is what made it visible; ENG-1247 halved it).

Why these assertions and not a simpler "flex appears somewhere"
--------------------------------------------------------------
Three properties are load-bearing and each fails silently:

* **Every spending call sends a tier.** A call site that quietly drops the
  parameter still runs, still returns, and still produces a correct-looking
  transcript — at 2x. So the sweep is over call sites, not over the module.
* **Latency measurement does NOT inherit the default.** Flex is slower by
  design. A latency harness silently on flex reports a real number for a tier
  production does not run, which is worse than no number: it is quotable.
* **A refused flex call is distinguishable from a refused model.** OpenAI
  returns 429 `resource_unavailable` when flex has no capacity and does not
  bill it. If that surfaces as a bare error, a whole judged row goes missing
  and reads as a model that would not answer.

Filename-independent by design
------------------------------
The family is discovered by walking the QA skills' scripts for OpenAI call
sites, so a new spending script is covered automatically and a rename cannot
quietly widen the set — the failure mode CLAUDE.md describes under *The drift
guards compare copies, never implementations*.
"""
from __future__ import annotations

import importlib.util
import pathlib
import re
import unittest

from reader_harness import REPO_ROOT, script_paths

QA_SKILLS = ("qa-prompt", "qa-conversation", "compare-prompt-versions")

# Scripts that call OpenAI but must NOT inherit the flex default, with the
# reason. Latency is the whole exemption: the tier is the variable under test.
EXPLICIT_TIER_SCRIPTS = {
    "measure_latency.py": "measures latency, so the tier is the variable under test",
}

# An OpenAI inference call, in either of the two shapes these skills use:
# the SDK (`client.chat.completions.create` / `client.responses.create`) and a
# raw POST to the chat-completions endpoint.
SDK_CALL_RE = re.compile(r"\.(?:chat\.completions|responses)\.create\(")
RAW_CALL_RE = re.compile(r"api\.openai\.com/v1/(?:chat/completions|responses)")

# The tier reaching a REQUEST: a body-dict key (`"service_tier": ...`, both the
# raw-POST scripts and send_turn's kwargs dict) or an SDK keyword argument
# (`service_tier=...`). Deliberately NOT a bare substring search — `import
# service_tier` and `service_tier.wire_value()` both contain the name while
# sending nothing, so a substring check passes on a call site that dropped the
# parameter. That exact mutation slipped through the first version of this test.
TIER_SENT_RE = re.compile(r"""["']service_tier["']\s*:|\bservice_tier\s*=(?!=)""")


def _skill_scripts(name: str) -> list[pathlib.Path]:
    return sorted((REPO_ROOT / "plugins" / "eng" / "skills" / name / "scripts").glob("*.py"))


def _spending_scripts() -> list[pathlib.Path]:
    out = []
    for skill in QA_SKILLS:
        for path in _skill_scripts(skill):
            if path.name == "service_tier.py":
                continue
            text = path.read_text(encoding="utf-8")
            if SDK_CALL_RE.search(text) or RAW_CALL_RE.search(text):
                out.append(path)
    return out


def _load_module(path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(f"st_{path.parent.parent.name}", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class HelperContractTests(unittest.TestCase):
    """The tier values are OpenAI's, verified against their docs 2026-09-04."""

    def setUp(self):
        self.copies = script_paths("service_tier.py")

    def test_the_helper_exists_in_both_qa_skills(self):
        """Guard the guard: a move or rename must fail here, not silently pass."""
        owners = {p.parent.parent.name for p in self.copies}
        for skill in QA_SKILLS:
            self.assertIn(skill, owners, f"{skill} has no service_tier.py — is it self-contained?")

    def test_every_copy_is_byte_identical_to_the_canonical(self):
        canonical = REPO_ROOT / "plugins/eng/skills/qa-prompt/scripts/service_tier.py"
        self.assertIn(canonical, self.copies)
        want = canonical.read_bytes()
        for path in self.copies:
            with self.subTest(copy=str(path.relative_to(REPO_ROOT))):
                self.assertEqual(want, path.read_bytes(), "copy has drifted from the canonical")

    def test_the_default_is_flex(self):
        for path in self.copies:
            with self.subTest(copy=path.parent.parent.name):
                mod = _load_module(path)
                self.assertEqual("flex", mod.DEFAULT_TIER)
                self.assertEqual("flex", mod.wire_value())

    def test_wire_values_match_openais_api(self):
        """`standard` is sent as `default` — the API has no literal "standard"."""
        for path in self.copies:
            with self.subTest(copy=path.parent.parent.name):
                mod = _load_module(path)
                self.assertEqual("flex", mod.wire_value("flex"))
                self.assertEqual("default", mod.wire_value("standard"))
                self.assertEqual("fast", mod.wire_value("fast"))
                # `priority` is the pre-rename spelling of fast; OpenAI still
                # accepts it, so a caller holding it must not be rejected.
                self.assertEqual("fast", mod.wire_value("priority"))

    def test_flex_is_half_and_fast_is_double(self):
        for path in self.copies:
            with self.subTest(copy=path.parent.parent.name):
                mod = _load_module(path)
                self.assertEqual(0.5, mod.COST_FACTOR["flex"])
                self.assertEqual(1.0, mod.COST_FACTOR["standard"])
                self.assertEqual(2.0, mod.COST_FACTOR["fast"])

    def test_the_flex_fallback_is_auto(self):
        """OpenAI's flex guide names `auto` for the capacity retry, not `default`."""
        for path in self.copies:
            with self.subTest(copy=path.parent.parent.name):
                self.assertEqual("auto", _load_module(path).FLEX_FALLBACK_TIER)

    def test_an_unknown_tier_raises_rather_than_falling_back(self):
        """A typo must not silently bill at 2x."""
        for path in self.copies:
            with self.subTest(copy=path.parent.parent.name):
                mod = _load_module(path)
                with self.assertRaises(mod.ServiceTierError):
                    mod.wire_value("cheap")

    def test_env_override_applies_and_the_flag_still_wins(self):
        for path in self.copies:
            with self.subTest(copy=path.parent.parent.name):
                mod = _load_module(path)
                import os
                os.environ[mod.ENV_VAR] = "standard"
                try:
                    self.assertEqual("default", mod.wire_value())
                    self.assertEqual("fast", mod.wire_value("fast"),
                                     "an explicit choice must beat the env override")
                finally:
                    del os.environ[mod.ENV_VAR]


class UnavailableTierPolicyTests(unittest.TestCase):
    """A tier name that is not a tier: stop by default, or continue at 1x.

    A typo in `AINATIVE_OPENAI_SERVICE_TIER` used to abort the run outright,
    which is right for an operator at a keyboard and wrong for an unattended
    sweep that would rather finish at standard than lose a night's work. The
    behaviour is therefore a stated policy rather than a default either way.

    The direction of the default matters. Falling back automatically would mean
    one wrong character silently doubles the bill of every run — the exact
    invisible-standard-billing this module exists to remove — so `standard` is
    opt-in, and taking it is announced.
    """

    # Loaded ONCE per test: `_load_module` builds a fresh module object each
    # call, so two loads give two distinct `ServiceTierError` classes and an
    # `assertRaises` against one never catches the other.
    _cached = None

    def _mod(self):
        if UnavailableTierPolicyTests._cached is None:
            UnavailableTierPolicyTests._cached = _load_module(
                REPO_ROOT / "plugins/eng/skills/qa-prompt/scripts/service_tier.py")
        return UnavailableTierPolicyTests._cached

    def _resolve(self, tier, policy=None):
        import contextlib
        import io
        import os
        mod = self._mod()
        os.environ[mod.ENV_VAR] = tier
        os.environ.pop(mod.FALLBACK_ENV, None)
        if policy:
            os.environ[mod.FALLBACK_ENV] = policy
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                return mod.resolve(), err.getvalue()
        finally:
            os.environ.pop(mod.ENV_VAR, None)
            os.environ.pop(mod.FALLBACK_ENV, None)

    def test_the_default_policy_is_stop(self):
        mod = self._mod()
        self.assertEqual("stop", mod.DEFAULT_UNAVAILABLE_POLICY)
        with self.assertRaises(mod.ServiceTierError):
            self._resolve("flexx")

    def test_the_standard_policy_continues_at_one_x(self):
        got, _ = self._resolve("flexx", policy="standard")
        self.assertEqual("standard", got)

    def test_the_fallback_is_announced(self):
        """Continuing at 1x doubles the bill; it may not be the quiet outcome."""
        _, err = self._resolve("flexx", policy="standard")
        self.assertIn("warning", err.lower())
        self.assertIn("standard", err.lower())

    def test_normal_is_accepted_for_standard(self):
        """`normal` is what people call the tier out loud."""
        got, _ = self._resolve("flexx", policy="normal")
        self.assertEqual("standard", got)

    def test_a_bogus_policy_is_itself_refused(self):
        """A misspelled policy must not silently read as one of the two."""
        mod = self._mod()
        with self.assertRaises(mod.ServiceTierError):
            self._resolve("flexx", policy="carry-on")

    def test_a_valid_tier_is_untouched_under_either_policy(self):
        for policy in (None, "standard"):
            with self.subTest(policy=policy):
                got, err = self._resolve("fast", policy=policy)
                self.assertEqual("fast", got)
                self.assertEqual("", err, "a valid tier must not warn")

    def test_the_policy_does_not_weaken_the_latency_exemption(self):
        """Unstated is not unrecognised — the exemption must still refuse."""
        import os
        mod = self._mod()
        os.environ[mod.FALLBACK_ENV] = "standard"
        os.environ.pop(mod.ENV_VAR, None)
        try:
            with self.assertRaises(mod.ServiceTierError):
                mod.require_explicit(None, "measure_latency.py")
        finally:
            os.environ.pop(mod.FALLBACK_ENV, None)

    def test_every_argparse_caller_exposes_the_policy(self):
        base = REPO_ROOT / "plugins/eng/skills/qa-prompt/scripts"
        for name in ("setup_run.py", "setup_sweep.py", "send_turn.py", "self_critique.py"):
            with self.subTest(script=name):
                text = " ".join((base / name).read_text(encoding="utf-8").split())
                self.assertIn("policy=args.on_tier_unavailable", text,
                              f"{name} takes the flag but ignores it")


class LatencyExemptionTests(unittest.TestCase):
    def test_require_explicit_refuses_to_default(self):
        """The exemption is worthless if it silently inherits flex."""
        for path in script_paths("service_tier.py"):
            with self.subTest(copy=path.parent.parent.name):
                mod = _load_module(path)
                with self.assertRaises(mod.ServiceTierError):
                    mod.require_explicit(None, "measure_latency.py")
                self.assertEqual("default", mod.require_explicit("standard", "x"))

    def test_the_latency_script_uses_require_explicit(self):
        for name, why in EXPLICIT_TIER_SCRIPTS.items():
            matches = [p for p in _spending_scripts() if p.name == name]
            with self.subTest(script=name):
                self.assertTrue(matches, f"{name} no longer calls OpenAI — update EXPLICIT_TIER_SCRIPTS")
                for path in matches:
                    text = path.read_text(encoding="utf-8")
                    self.assertIn("require_explicit", text,
                                  f"{name} {why}, so it must not inherit the flex default")


class CallSiteTests(unittest.TestCase):
    """Every spending call site states a tier."""

    def test_the_sweep_finds_the_call_sites_it_should(self):
        """Guard the guard: an empty sweep would make every test below vacuous."""
        found = {p.name for p in _spending_scripts()}
        for expected in ("send_turn.py", "self_critique.py", "measure_latency.py",
                         "judge_row.py", "drive_reactive.py"):
            self.assertIn(expected, found, "the call-site sweep missed a known spender")

    def test_every_spending_script_states_a_tier(self):
        for path in _spending_scripts():
            rel = str(path.relative_to(REPO_ROOT))
            with self.subTest(script=rel):
                text = path.read_text(encoding="utf-8")
                self.assertRegex(text, TIER_SENT_RE,
                                 f"{rel} calls OpenAI without putting a service tier in "
                                 "the request — an omitted tier bills at standard (2x flex)")

    def test_every_spending_script_resolves_through_the_helper(self):
        """A hardcoded literal is a copy that no longer follows the default."""
        for path in _spending_scripts():
            rel = str(path.relative_to(REPO_ROOT))
            with self.subTest(script=rel):
                text = path.read_text(encoding="utf-8")
                self.assertIn("import service_tier", text,
                              f"{rel} must resolve its tier through service_tier.py, "
                              "not hardcode one")

    def test_a_refused_flex_call_is_never_silent(self):
        """Every spender either retries the fallback or explains the refusal."""
        for path in _spending_scripts():
            rel = str(path.relative_to(REPO_ROOT))
            if path.name in EXPLICIT_TIER_SCRIPTS:
                continue  # not on flex by default; nothing to degrade from
            with self.subTest(script=rel):
                text = path.read_text(encoding="utf-8")
                self.assertTrue(
                    "FLEX_FALLBACK_TIER" in text or "warn_if_flex_refused" in text,
                    f"{rel} would surface a flex capacity refusal as a bare error, "
                    "indistinguishable from a model that would not answer",
                )


class SetupPrecedenceTests(unittest.TestCase):
    """flag > env > flex, and an unset flag must not masquerade as a choice.

    `add_argument` defaulting to `flex` made an unset flag indistinguishable
    from an explicit `--service-tier flex`. The setup scripts persisted that to
    the state, `send_turn.py` passed it on as an explicit choice, and
    `AINATIVE_OPENAI_SERVICE_TIER` silently lost — the documented unattended
    override did nothing, with no error to say so.
    """

    def _mod(self):
        return _load_module(REPO_ROOT / "plugins/eng/skills/qa-prompt/scripts/service_tier.py")

    def _choice(self, argv, env=None):
        import argparse
        import os
        mod = self._mod()
        os.environ.pop(mod.ENV_VAR, None)
        if env:
            os.environ[mod.ENV_VAR] = env
        try:
            parser = argparse.ArgumentParser()
            mod.add_argument(parser)
            return mod.resolve(parser.parse_args(argv).service_tier)
        finally:
            os.environ.pop(mod.ENV_VAR, None)

    def test_the_flag_does_not_default_to_a_tier(self):
        """The load-bearing half: `None` is what lets the env be consulted."""
        import argparse
        parser = argparse.ArgumentParser()
        self._mod().add_argument(parser)
        self.assertIsNone(parser.parse_args([]).service_tier)

    def test_unset_flag_falls_through_to_flex(self):
        self.assertEqual("flex", self._choice([]))

    def test_unset_flag_lets_the_env_override_win(self):
        self.assertEqual("standard", self._choice([], env="standard"))

    def test_an_explicit_flag_beats_the_env(self):
        self.assertEqual("fast", self._choice(["--service-tier", "fast"], env="standard"))

    def test_setup_scripts_persist_the_resolved_choice(self):
        """Storing the raw flag would put `None` (or a fake choice) on the state."""
        for name in ("setup_run.py", "setup_sweep.py"):
            path = REPO_ROOT / "plugins/eng/skills/qa-prompt/scripts" / name
            with self.subTest(script=name):
                # Whitespace-insensitive: the call wraps across lines once it
                # also passes the fallback policy.
                text = " ".join(path.read_text(encoding="utf-8").split())
                self.assertIn("service_tier.resolve( args.service_tier", text,
                              f"{name} must persist the RESOLVED tier, so the env "
                              "override reaches the state")


class PromptParamsCannotOverrideTheTierTests(unittest.TestCase):
    """A Braintrust `params.service_tier` must not silently replace the run's.

    The call kwargs spread the prompt's params, so a `service_tier` among them
    landed *after* ours and won. The turn record still stored the tier we asked
    for, so a report could claim flex over a request that billed standard —
    a wrong answer with nothing to signal it.
    """

    def _send_turn(self):
        """Load `send_turn.py` with the OpenAI SDK stubbed.

        The module imports the SDK at module scope, and CI installs only
        `requests` — the suite is stdlib-only with no network by design, so the
        fix is to stub the import rather than pull a client library into CI for
        two tests. Nothing here touches the SDK: `_params_for_chat` and
        `_params_for_responses` are pure functions over a params dict.
        """
        import sys
        import types
        path = REPO_ROOT / "plugins/eng/skills/qa-prompt/scripts/send_turn.py"
        stub = types.ModuleType("openai")
        stub.OpenAI = object
        # Must be a real exception class: the module uses it in `except` clauses.
        stub.OpenAIError = type("OpenAIError", (Exception,), {})
        had_openai = "openai" in sys.modules
        previous = sys.modules.get("openai")
        sys.modules["openai"] = stub
        sys.path.insert(0, str(path.parent))
        try:
            spec = importlib.util.spec_from_file_location("qa_send_turn", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        finally:
            sys.path.remove(str(path.parent))
            if had_openai:
                sys.modules["openai"] = previous
            else:
                del sys.modules["openai"]
        return mod

    def test_a_prompt_level_service_tier_is_dropped(self):
        import contextlib
        import io
        mod = self._send_turn()
        for fn in ("_params_for_chat", "_params_for_responses"):
            with self.subTest(api=fn):
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    out = getattr(mod, fn)({"service_tier": "fast", "temperature": 0.5})
                self.assertNotIn("service_tier", out,
                                 "a prompt param would be spread after ours and win")
                self.assertEqual(0.5, out.get("temperature"), "other params must survive")
                self.assertIn("service_tier", err.getvalue(),
                              "dropping it silently is the same class of defect")

    def test_ordinary_params_are_untouched(self):
        mod = self._send_turn()
        out = mod._params_for_chat({"temperature": 0.2, "reasoning_effort": "low"})
        self.assertEqual({"temperature": 0.2, "reasoning_effort": "low"}, out)


class SetupErrorHandlingTests(unittest.TestCase):
    """A bad env tier is one clean error line, never a traceback."""

    def test_a_bad_env_tier_is_a_clean_error_before_any_network_call(self):
        """Reported on the typo, not after a Braintrust round-trip.

        Ordering is what makes this assertable at all: while the tier resolved
        at the END of the state build, a removed `except` clause was
        unreachable in any offline test, so the missing handler passed.
        """
        import os
        import subprocess
        for name in ("setup_run.py", "setup_sweep.py"):
            path = REPO_ROOT / "plugins/eng/skills/qa-prompt/scripts" / name
            with self.subTest(script=name):
                env = dict(os.environ)
                env["AINATIVE_OPENAI_SERVICE_TIER"] = "cheap"
                env["BRAINTRUST_API_KEY"] = "stub"
                env["OPENAI_EVALUATIONS_API_KEY"] = "stub"
                argv = ["python3", str(path), "https://example.invalid/p/x", "--no-context"]
                if name == "setup_sweep.py":
                    argv += ["--scenarios", "a"]
                proc = subprocess.run(argv, capture_output=True, text=True,
                                      env=env, timeout=120)
                self.assertNotIn("Traceback", proc.stderr,
                                 f"{name} let ServiceTierError escape as a traceback")
                self.assertIn("unknown service tier", proc.stderr,
                              f"{name} must name the bad tier")
                self.assertEqual(2, proc.returncode,
                                 f"{name} must exit 2, matching its other env errors")

    def test_sweep_resolves_the_tier_once_before_writing_any_state(self):
        """Resolving inside the loop half-writes a run before it fails."""
        text = (REPO_ROOT / "plugins/eng/skills/qa-prompt/scripts/setup_sweep.py").read_text()
        self.assertEqual(1, text.count("service_tier.resolve("),
                         "resolve once, before the scenario loop — not per scenario")


class LatencyPerArmTierTests(unittest.TestCase):
    """Every-arm-tiered configs are already explicit and must be accepted.

    `require_explicit` ran before `arms.json` was read, so it always saw `None`
    and aborted — rejecting exactly the tier-vs-tier comparison the docstring
    offers as the alternative to the env var.
    """

    SCRIPT = "plugins/eng/skills/qa-conversation/scripts/measure_latency.py"
    NEEDS_TIER = "must state its service tier explicitly"

    def _run(self, arms, tmpdir):
        import json
        import os
        import subprocess
        cfg = pathlib.Path(tmpdir) / "arms.json"
        cfg.write_text(json.dumps({"arms": arms, "user_turns": ["hi"]}))
        env = dict(os.environ)
        env.pop("AINATIVE_OPENAI_SERVICE_TIER", None)
        env["OPENAI_EVALUATIONS_API_KEY"] = "stub"
        return subprocess.run(
            ["python3", str(REPO_ROOT / self.SCRIPT), str(cfg), "1", "0"],
            capture_output=True, text=True, env=env, timeout=120,
        )

    def test_all_arms_tiered_is_accepted_without_the_env_var(self):
        import tempfile
        arms = [{"label": "flex", "prompt": "missing.json", "service_tier": "flex"},
                {"label": "std", "prompt": "missing.json", "service_tier": "standard"}]
        with tempfile.TemporaryDirectory() as d:
            proc = self._run(arms, d)
        self.assertNotIn(self.NEEDS_TIER, proc.stdout + proc.stderr,
                         "a fully per-arm config is explicit; it must not be rejected")

    def test_a_missing_arm_tier_still_requires_the_env_var(self):
        """The exemption must not become a way to skip the requirement."""
        import tempfile
        arms = [{"label": "flex", "prompt": "missing.json", "service_tier": "flex"},
                {"label": "unset", "prompt": "missing.json"}]
        with tempfile.TemporaryDirectory() as d:
            proc = self._run(arms, d)
        self.assertIn(self.NEEDS_TIER, proc.stdout + proc.stderr,
                      "one unstated arm means the run's tier is still unstated")


class RefusalDetectionTests(unittest.TestCase):
    """`flex_refusal_hint` fires on the two flex-specific failures and nothing else."""

    class _HttpError(Exception):
        def __init__(self, code, msg):
            super().__init__(msg)
            self.code = code

    def _mod(self):
        return _load_module(REPO_ROOT / "plugins/eng/skills/qa-prompt/scripts/service_tier.py")

    @staticmethod
    def _urllib_error(code, body):
        """A real `urllib.error.HTTPError`, whose `str()` is only the status line.

        This is the shape every `eng:qa-conversation` script sees, and the one
        the first version of the detector missed entirely: the
        `resource_unavailable` code lives in the response body, never in the
        string form, so a `str(err)` match never fired for those nine scripts.
        """
        import io
        import urllib.error
        return urllib.error.HTTPError(
            "https://api.openai.com/v1/chat/completions", code,
            "Too Many Requests" if code == 429 else "Bad Request",
            {}, io.BytesIO(body.encode()),
        )

    def test_a_urllib_capacity_refusal_is_read_from_the_response_body(self):
        mod = self._mod()
        err = self._urllib_error(429, '{"error":{"code":"resource_unavailable"}}')
        self.assertNotIn("resource_unavailable", str(err),
                         "premise: the code is NOT in the string form")
        self.assertIsNotNone(mod.flex_refusal_hint("flex", err))

    def test_a_urllib_rate_limit_is_still_not_a_flex_refusal(self):
        mod = self._mod()
        err = self._urllib_error(429, '{"error":{"code":"rate_limit_exceeded"}}')
        self.assertIsNone(mod.flex_refusal_hint("flex", err))

    def test_an_unreadable_body_degrades_instead_of_raising(self):
        """A detector that raises inside an error handler loses the original error."""
        mod = self._mod()

        class _Broken(Exception):
            code = 429

            def read(self):
                raise OSError("stream already consumed")

        self.assertIsNone(mod.flex_refusal_hint("flex", _Broken("boom")))

    def test_capacity_refusal_is_detected(self):
        mod = self._mod()
        err = self._HttpError(429, "429 resource_unavailable: no flex capacity")
        self.assertIsNotNone(mod.flex_refusal_hint("flex", err))

    def test_an_ineligible_model_is_detected(self):
        mod = self._mod()
        err = self._HttpError(400, "invalid_value: 'service_tier' is not supported")
        self.assertIsNotNone(mod.flex_refusal_hint("flex", err))

    def test_an_ordinary_rate_limit_is_not_treated_as_a_flex_refusal(self):
        """Retrying a real rate limit on standard pays 2x to hit the same wall."""
        mod = self._mod()
        err = self._HttpError(429, "429 rate_limit_exceeded: too many requests")
        self.assertIsNone(mod.flex_refusal_hint("flex", err))

    def test_a_non_flex_call_never_reports_a_flex_refusal(self):
        mod = self._mod()
        err = self._HttpError(429, "429 resource_unavailable")
        self.assertIsNone(mod.flex_refusal_hint("default", err))
        self.assertIsNone(mod.flex_refusal_hint("fast", err))

    def test_an_unrelated_error_is_not_a_flex_refusal(self):
        mod = self._mod()
        self.assertIsNone(mod.flex_refusal_hint("flex", ValueError("bad json")))


if __name__ == "__main__":
    unittest.main()
