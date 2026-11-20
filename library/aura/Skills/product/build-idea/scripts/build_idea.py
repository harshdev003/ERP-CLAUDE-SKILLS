"""Mechanical half of the build leg: approved idea -> Linear card -> PR, with the guards.

`build-idea` is mostly orchestration — worktrees, implementation, sim tests and PR
review already have skills that do them well. This script owns only the parts that
are deterministic or dangerous:

  plan            Resolve an approved idea and render the Linear card body. No writes.
  start           Record the card on the row and move it to implementing.
  guard-variant   Refuse to force an experiment variant on anything but a test account.
  guard-collision Refuse to start a build whose domain already has a running experiment.
  finish          Attach media + PR to the row. It stays implementing.

The two guards are the reason this is code rather than prose. Everything else in the
build leg fails loudly when it goes wrong — a bad branch, a failing test, a rejected
PR. Forcing a variant on a real user fails **silently**: the run looks fine, and the
damage is a contaminated metric nobody can see until an experiment reads wrong weeks
later. A rule that only lives in a SKILL.md is a rule that gets skipped at 2am.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import pathlib
from typing import Any, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from notion_rest import (  # noqa: E402
    MCPFallback,
    block_bullet,
    block_divider,
    block_heading,
    block_paragraph,
    make_rich_text,
    make_select,
    notion,
    rich_text_value,
    select_value,
    title_text,
)


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


AURIE_FEATURES_DS = "8d14f3eb-0c55-4f25-9322-ab2b2a69f092"
EXPERIMENTS_DS = "c0209685-17ac-49e2-849a-85fa8178decd"

# A Slack archives permalink -> (channel, thread_ts). The idea row's `Slack thread`
# is one of these; the PR-opened reply threads under it. Same shape ship-idea uses.
PERMALINK_RE = re.compile(r"/archives/(C[A-Z0-9]+)/p(\d{10})(\d{6})")

# Every feature card sits under its loop's parent, beside the loop-infrastructure
# sub-tasks. The other three loops get their own parent as they come online.
LOOP_PARENT = {"aurie-features": "PROD-5917"}

# Only an approved idea is buildable. `shared` or `pending` means nobody has said
# yes yet, and building one would be the loop deciding for the team.
BUILDABLE_STATUS = "approved"

# Screens drawn before the age-gate consent (the Get Started / Login tap). Nothing
# third-party runs there — ExperimentService needs Firebase and Mixpanel starts after
# consent — so no variant can be fetched and no exposure can fire. Canonical list:
# brainstorm-ideas `references/pre-consent-screens.md`; tests/test_pre_consent_screens.py
# keeps the copies equal.
PRE_CONSENT_SCREENS = {"welcome", "splash", "geographic_restriction"}
BUCKETING_PATH_MARKER = "bucketing path:"


def is_pre_consent(screen: str) -> bool:
    """The row's `Screen` is an accessibility identifier (`welcome_screen`); the list is bare."""
    v = (screen or "").strip().lower()
    if v.endswith("_screen"):
        v = v[: -len("_screen")]
    return v in PRE_CONSENT_SCREENS


def pre_consent_refusal(idea: dict[str, Any]) -> Optional[str]:
    """A message when the idea sits on a pre-consent screen with no bucketing path named."""
    if not is_pre_consent(idea.get("screen") or ""):
        return None
    if BUCKETING_PATH_MARKER in (idea.get("instrumentation") or "").lower():
        return None
    return (f"'{idea['screen']}' is drawn before consent, so no experiment can bucket or expose "
            f"there. Move the idea to a post-consent screen, or write `{BUCKETING_PATH_MARKER} "
            f"<how a user is bucketed and exposed>` in the row's Instrumentation first.")

# A sim-test address: local part begins `test+`, domain is exactly aurahealth.io.
#
# Written against the accounts that EXIST, and TWO shapes do. The sim-testing rule
# file documents `{prefix}+test+{unix}@aurahealth.io`, but `sim_test.sh` generates
# `test+{unix}@aurahealth.io` — and `users.json` holds both, 55 of the former and
# 3 of the latter. A guard written from the doc alone refused all 60; a guard
# written from the generator alone refused 3. Either way somebody turns the gate
# off, and then it protects nothing.
#
# So: the local part carries a `test+` segment, at the start or after one prefix,
# and the domain is exactly aurahealth.io. Verified against the real fleet — all
# 57 sim accounts pass.
#
# Must still refuse, and does:
#   `<redacted-email>y`  — typo'd domain, a real row in users.json
#   `<redacted-email>`         — a genuine Google account used for sign-in
#                                       testing; real credentials, never a
#                                       legitimate target for a forced variant
TEST_EMAIL_RE = re.compile(r"^(?:[^@\s+]+\+)?test\+[^@\s]+@aurahealth\.io$", re.I)

MAX_COLLISIONS_SHOWN = 10

RUNNING_STATUSES = {"Running", "In flight"}
AURIE_PLATFORMS = {"Aurie Mobile", "Aurie Web"}

# The experiment server, not the Notion mirror, is what decides whether an
# experiment is live.
#
# Measured 2026-09-14, and the mirror is wrong in BOTH directions: 7 experiments
# the API reports live are filed `Turned off` in Notion — `qpNewDoneScreen` among
# them, which the team's own tracker also lists as running — and 30 the mirror
# calls Running have actually ended.
#
# The two errors are not symmetric. An over-block is loud: somebody reads the
# list, sees a dead experiment, and passes `--override`. A miss is silent, and it
# is the exact failure this gate exists to stop — "no collision" reported for an
# experiment that is live, found out weeks later when two readouts disagree.
# Worse, the 30 false blocks train people to override, which is how the gate
# stops protecting anything at all.
AURA_API = "https://services.aurahealth.io"

# `?limit=N` is the only query knob that works — offset, page and cursor are
# silently ignored, so a default request returns the first 100 of ~3,000 rows and
# looks complete.
EXPERIMENTS_URL = f"{AURA_API}/experiments?limit=10000"

# `targetApp` is `ai` for Aurie and null for Aura.
AURIE_TARGET_APP = "ai"


# ---------- the guards ------------------------------------------------------

def is_test_account(email: str) -> bool:
    """True only for a sim-test address.

    Anchored at both ends on purpose: `<redacted-email>.evil.com` and
    `<redacted-email>` both fail, where a substring check on "test+" would
    pass the first.

    There is no override. Forcing a variant on a real account is not a thing the
    loop should be able to do with a flag — if someone genuinely needs it, that
    is a deliberate human action outside this skill.
    """
    return bool(TEST_EMAIL_RE.match((email or "").strip()))


def cmd_guard_variant(args: argparse.Namespace) -> int:
    """Gate on `POST /experiments/{name}/set/{uid}/{variant}`.

    This guard is the ONLY thing standing between the loop and a contaminated
    metric. Probed against the live service on 2026-09-14, with a nonexistent
    UUID so nothing could mutate:

        GET  /experiments/{name}              valid token 200 · bogus 403 · none 403
        (services now answer 401 to a bogus or missing token; 403 means wrong role)
        POST /experiments/{name}/set/{uid}/a  valid token 404 · bogus 404 · none 404

    The read is authenticated; the **write is not**. All three set-calls returned
    the same `USER_NOT_FOUND`, so the route resolves the user before validating
    any bearer token — a garbage token behaves exactly like a real one. (Whether
    it validates *after* resolving a real user could not be tested without
    mutating a real account, so treat "unauthenticated" as the working
    assumption rather than a proven fact.)

    An experiment assignment persists server-side. Setting one on a real user
    does not error, does not roll back, and quietly contaminates every metric
    that experiment reports. Hence: fails closed, and no override flag.
    """
    email = (args.email or "").strip()
    if not email:
        print("REFUSED: no --email given. The override needs the account it will "
              "apply to, and 'none supplied' is never a reason to proceed.", file=sys.stderr)
        return 2
    if not is_test_account(email):
        print(f"REFUSED: {email} is not a sim-test account.\n"
              f"  Only sim-test addresses may have a variant forced — `test+…@aurahealth.io`\n"
              f"  or `{{prefix}}+test+…@aurahealth.io`.\n"
              f"  Experiment assignments persist server-side: setting one on a real user\n"
              f"  contaminates that experiment's metrics permanently and cannot be undone.",
              file=sys.stderr)
        return 2
    print(f"OK: {email} is a sim-test account — forcing "
          f"{args.experiment or '<experiment>'}={args.variant or '<variant>'} is permitted.")
    return 0


def _aura_token() -> Optional[str]:
    return os.environ.get("AURA_API_TOKEN") or os.environ.get("AURA_ADMIN_TOKEN")


def live_aurie_from_api() -> tuple[Optional[set[str]], str]:
    """Names of Aurie experiments actually running. `None` means unknown.

    Live is `endedAt` unset AND `active` carrying more than one letter, because
    an experiment only confounds another one while it is **splitting traffic**.

    Measured by length, which counts a repeated-letter `active` as live.
    `newPricingPage` and `placeholder` carry `active="bb"` — two characters, one
    distinct arm — and nothing in the catalogue says which was meant. Counting
    them as live is the conservative direction *here*: a false collision costs a
    question, a missed one confounds a real experiment. `data:sync-experiments`'
    `reconcile.py` holds the same rows for a human instead, because it writes a
    durable status and the conservative direction there is the opposite one.

    A single letter means it has stopped splitting — and deliberately says
    nothing about why. Checked against the live catalogue, that one signal
    covers at least five situations: forced to one arm before release
    (`chatImageUpload`), pending release though already past its minVersion
    (`aurieWebViewPaywall3`), disabled by an unreachable minimum (`noFeed`, at
    `minVersion 20.0.0`), a permanent feature flag kept as a kill switch
    (`usageLimitsOldUsers`), or a genuine rollout. `result` is empty in all of
    them and `notes` records intent rather than state, so the distinction is not
    derivable here — and it does not need to be. None of them is splitting, so
    none of them can confound a new test.

    Do not build a verdict on this test. Whether one of these won is a fact only
    Yawar and Viktor hold.

    Tri-state on purpose. "Could not reach the server" must never collapse into
    "nothing is running": that turns an unanswerable question into a clean bill
    of health, which is the shape of every silent pass in this codebase.
    """
    token = _aura_token()
    if not token:
        return None, "AURA_API_TOKEN is not set."
    req = urllib.request.Request(
        EXPERIMENTS_URL,
        headers={"Authorization": f"Bearer {token}", "User-Agent": user_agent(), "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            rows = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code} from {AURA_API}/experiments — {e.reason}"
    except Exception as e:  # noqa: BLE001 — any transport failure is 'unknown'
        return None, f"{type(e).__name__}: {e}"
    if not isinstance(rows, list):
        return None, f"unexpected response shape: {type(rows).__name__}"
    live = {
        r["id"] for r in rows
        if r.get("id")
        and not r.get("endedAt")
        and len(str(r.get("active") or "")) > 1
        and r.get("targetApp") == AURIE_TARGET_APP
    }
    return live, f"{len(live)} live of {len(rows)} catalog rows"


def _notion_experiment_text() -> dict[str, str]:
    """name -> hypothesis, for domain matching. Text only — not the live signal."""
    out: dict[str, str] = {}
    for row in notion.query_data_source(EXPERIMENTS_DS, page_size=100):
        name = title_text(row, "Title").strip()
        if name:
            out[name] = " ".join(rich_text_value(row, "Hypothesis").split())
    return out


def running_experiments(domain: Optional[str] = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """(collisions, provenance). Live set from the API; hypothesis text from Notion.

    Notion supplies only the prose the domain keywords match against. When a live
    experiment has no Notion row — which happens the moment one is created, since
    `data:sync-experiments` imports on its own cadence — matching falls back to
    the name alone. That is weaker, and the caller is told, rather than the row
    being dropped for want of a description.
    """
    live, detail = live_aurie_from_api()
    text = _notion_experiment_text()
    prov: dict[str, Any] = {"source": "api", "detail": detail, "unmatched": []}

    if live is None:
        # Degrade to the mirror rather than refusing outright — but say so, because
        # the answer is now only as current as the last sync.
        prov["source"] = "notion-fallback"
        live = {n for n, _ in text.items()} & {
            title_text(r, "Title").strip()
            for r in notion.query_data_source(EXPERIMENTS_DS, page_size=100)
            if select_value(r, "Status") in RUNNING_STATUSES
            and select_value(r, "Platform") in AURIE_PLATFORMS
        }

    out = []
    for name in sorted(live):
        hypothesis = text.get(name, "")
        if name not in text:
            prov["unmatched"].append(name)
        if domain and not _touches_domain(domain, f"{name} {hypothesis}"):
            continue
        out.append({"name": name, "hypothesis": hypothesis,
                    "tracked": name in text})
    return out, prov


# Same vocabulary the sim-test priors builder uses to tag an experiment to a
# domain, kept in sync by hand. Duplicated rather than imported because the two
# live in different repos.
DOMAIN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "paywall": ("paywall", "price", "pricing", "plan", "trial", "subscribe", "subscription",
                "testimonial", "discount", "cta", "checkout", "purchase", "cancel", "refund",
                "upsell", "tier", "yearly", "monthly", "guarantee", "faq"),
    "onboarding": ("onboarding", "welcome", "signup", "sign up", "register", "quiz", "intro",
                   "value prop", "tutorial", "archetype", "commitment", "first session"),
    "home/feed": ("home", "feed", "dashboard", "card", "practice", "daily", "streak", "recommend"),
    "conversation": ("conversation", "chat", "voice", "livekit", "elevenlabs", "quick processing",
                     "quickprocessing", "prompter", "message", "bookmark", "done screen",
                     "dive deeper", "orb", "animation"),
    "journal": ("journal",),
    "grounding": ("grounding", "meditation", "track", "audio", "sleep", "player", "prayer"),
    "affirmations": ("affirmation",),
    "profile": ("profile", "memory", "insight"),
    "settings": ("settings", "preference"),
    "notifications": ("notification", "push", "reminder"),
    "growth/referral": ("referral", "invite", "share", "viral"),
}


# Matching experiment names to a domain is where this gate silently misses, and
# both failure modes are real and were both observed on the live catalog.
#
# A bare substring test reads words nobody wrote: `feed` sits inside
# `webSignupChooseFeeDefaultZero` ("Choose **Fee D**efault"), which put a web
# sign-up test on home/feed and blocked a build there.
#
# Camel-splitting alone then misses in the other, worse direction:
#   aurieCustomWebPaywall2   "paywall2" is not "paywall"
#   aurieAnimations2         plural AND a generation number
#   aiLiveKitIntegration4    the split breaks the compound: "live kit" is not "livekit"
# All three are live today, and a miss is the failure this gate exists to stop.
#
# So: split the humps, fold each token (trailing digits off, simple plural off),
# match a keyword's word sequence against the token sequence, and also against
# runs of adjacent tokens joined back up — which is what recovers `livekit`
# without letting `feed` match `fee default`, since the join is compared for
# equality, never containment.
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_TRAILING_DIGITS_RE = re.compile(r"\d+$")

# How many adjacent tokens may be joined to rebuild a compound keyword. 3 covers
# every compound in the vocabulary (`livekit`, `quickprocessing`, `donescreen`)
# without inventing long spurious joins.
MAX_COMPOUND_TOKENS = 3


def _fold(token: str) -> str:
    """Trailing generation number off, simple plural off."""
    t = _TRAILING_DIGITS_RE.sub("", token)
    if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
        t = t[:-1]
    return t


def _words(text: str) -> str:
    """Lowercased, camel-split, punctuation-to-space."""
    return re.sub(r"[^a-z0-9]+", " ", _CAMEL_RE.sub(" ", text or "").lower()).strip()


def _tokens(text: str) -> list[str]:
    return [_fold(w) for w in _words(text).split()]


def _touches_domain(domain: str, text: str) -> bool:
    toks = _tokens(text)
    for kw in DOMAIN_KEYWORDS.get(domain, ()):
        kt = _tokens(kw)
        if not kt:
            continue
        n = len(kt)
        # the keyword's words, in order, somewhere in the name or hypothesis
        if any(toks[i:i + n] == kt for i in range(len(toks) - n + 1)):
            return True
        # a single-word keyword written as a compound the camel split took apart
        if n == 1:
            for size in range(2, MAX_COMPOUND_TOKENS + 1):
                for i in range(len(toks) - size + 1):
                    if _fold("".join(toks[i:i + size])) == kt[0]:
                        return True
    return False


def _print_shared_metric_prompt(domain: str) -> None:
    """Printed on every pass — a clean one and an overridden one alike.

    An override is still a decision to build, so it needs the question this gate
    cannot answer for itself just as much as a clean pass does.
    """
    print(f"\nThis gate matches on DOMAIN. It cannot see a shared-metric collision — two "
          f"experiments\non different surfaces judged by the same primary metric — because "
          f"primary metric is not\na structured field anywhere. Ask Viktor in #support-bot "
          f"before you build:\n"
          f'  "Any running Aurie experiment sharing a primary metric with a {domain} change?"',
          file=sys.stderr)


def cmd_guard_collision(args: argparse.Namespace) -> int:
    """Refuse a build whose domain already has a running Aurie experiment.

    The card asks to refuse on "the same surface and the same primary metric".
    Neither is a structured field anywhere — surface lives in prose and the
    primary metric is usually only in the ticket — so this matches on **domain**
    instead, which is broader than asked.

    That is deliberate. A false stop costs one `--override` flag and a sentence
    of justification; a confounded pair of experiments costs the readout of both,
    and you find out weeks later when the numbers disagree with themselves. The
    Top-10 learnings already carry a live example: running `webYearlyPricing2`
    concurrently over the same UserWeb traffic made each test's cross-plan cash
    unreadable.
    """
    if not notion.available:
        raise MCPFallback("NOTION_TOKEN is not set.")
    live, prov = running_experiments(args.domain)

    if prov["source"] == "notion-fallback":
        print(f"! could not reach the experiment server ({prov['detail']}).\n"
              f"  Falling back to the Notion mirror, which is only as current as the last\n"
              f"  `data:sync-experiments` run. Measured 2026-09-14, the mirror missed 7 live\n"
              f"  experiments and named 30 that had already ended — so treat a pass here as\n"
              f"  unverified rather than clean.", file=sys.stderr)
    if prov["unmatched"]:
        print(f"! {len(prov['unmatched'])} live experiment(s) have no Learnings/Experiments row "
              f"yet, so they were matched on name alone: {', '.join(prov['unmatched'][:6])}"
              f"{' …' if len(prov['unmatched']) > 6 else ''}", file=sys.stderr)

    if not live:
        print(f"OK: no running Aurie experiment tagged to '{args.domain}'. ({prov['detail']})")
        _print_shared_metric_prompt(args.domain)
        return 0

    # Capped: `paywall` currently matches 22 live experiments, and a wall of 22
    # lines is read by nobody. The list is the point of this gate — the block is
    # just what makes someone look at it — so keep it short enough to be read.
    shown = live[:MAX_COLLISIONS_SHOWN]
    print(f"{len(live)} running Aurie experiment(s) already on '{args.domain}':", file=sys.stderr)
    for e in shown:
        detail = e["hypothesis"][:120] or "(no hypothesis recorded)"
        mark = "" if e["tracked"] else "  [untracked — name match only]"
        print(f"  - {e['name']} — {detail}{mark}", file=sys.stderr)
    if len(live) > len(shown):
        print(f"  … and {len(live) - len(shown)} more", file=sys.stderr)

    if args.override:
        print(f"\nOVERRIDDEN: {args.override}", file=sys.stderr)
        print("Recorded on the card so the readout can account for the overlap.", file=sys.stderr)
        _print_shared_metric_prompt(args.domain)
        return 0
    print("\nREFUSED: two experiments on one domain confound each other's readout.\n"
          "  Either wait for these to end, or pass --override '<why this is safe>'\n"
          "  — the reason goes on the Linear card so whoever reads the result knows.",
          file=sys.stderr)
    return 2


# ---------- thread reply + media --------------------------------------------

def parse_permalink(url: str) -> Optional[tuple[str, str]]:
    """(channel_id, thread_ts) from a Slack archives permalink, or None."""
    m = PERMALINK_RE.search(url or "")
    return (m.group(1), f"{m.group(2)}.{m.group(3)}") if m else None


def _first_file_url(row: dict[str, Any], prop: str) -> str:
    """URL of the first file on a Notion `files` property, or ''.

    Notion signs these URLs and they expire within the hour, which is fine — the
    only consumer downloads it immediately to hand to Slack.
    """
    files = ((row.get("properties", {}).get(prop) or {}).get("files")) or []
    if not files:
        return ""
    first = files[0]
    return (first.get("file") or first.get("external") or {}).get("url", "")


def _attach_media(channel: str, thread_ts: str, url: str, fingerprint: str, comment: str) -> bool:
    """Download a Notion-hosted file and upload it into the idea's thread.

    A threaded reply rather than part of the PR-opened message: the media is what
    lets a reviewer judge the thing, but a failed upload must not cost the post that
    carries the PR link. Mirrors `share-ideas`'s screenshot-attach.
    """
    try:
        import requests  # noqa: PLC0415
    except ImportError:
        return False
    tmp = ""
    try:
        from slack_post import upload_files  # noqa: PLC0415
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        suffix = os.path.splitext(url.split("?")[0])[1] or ".png"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False,
                                         prefix=f"pr_{fingerprint}_") as fh:
            fh.write(resp.content)
            tmp = fh.name
        out = upload_files(channel, [tmp], thread_ts=thread_ts, comment=comment,
                           footer_skill="product:build-idea")
        return bool(out.get("ok"))
    # SystemExit is caught deliberately: `upload_files` calls sys.exit() on a Slack
    # error, and SystemExit derives from BaseException, so a bare `except Exception`
    # would let it unwind and kill the run after the PR post already landed.
    except (Exception, SystemExit) as exc:  # noqa: BLE001
        print(f"  ! media upload failed ({exc})", file=sys.stderr)
        return False
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


# ---------- idea -> card ----------------------------------------------------

# An idea is addressed by its immutable, human-friendly `Idea ID` (`42`, `#42`,
# `IDEA-42`). Bounded to ≤6 digits so it can never be confused with a 12-hex
# fingerprint. The fingerprint and a title substring still resolve, for the rows
# whose references predate the id.
_IDEA_REF_RE = re.compile(r"^(?:idea[-\s]?|#)?(\d{1,6})$", re.IGNORECASE)


def _ref_idea_num(ref: str) -> Optional[int]:
    """The integer of an Idea-# reference (`42`, `#42`, `IDEA-42`), or None."""
    m = _IDEA_REF_RE.match((ref or "").strip())
    return int(m.group(1)) if m else None


def _row_idea(row: dict[str, Any]) -> tuple[Optional[int], str]:
    """(number, 'IDEA-42') from a row's auto-`Idea ID` property; (None, '') if unset."""
    uid = (row.get("properties", {}).get("Idea ID") or {}).get("unique_id") or {}
    num = uid.get("number")
    if num is None:
        return None, ""
    prefix = uid.get("prefix")
    return num, (f"{prefix}-{num}" if prefix else str(num))


def find_idea(ds_id: str, ref: str) -> dict[str, Any]:
    """Resolve an Idea ID, fingerprint, or title substring to one row."""
    rows = []
    for row in notion.query_data_source(ds_id, page_size=100):
        idea_num, idea_id = _row_idea(row)
        rows.append({
            "page_id": row["id"],
            "idea_num": idea_num,
            "idea_id": idea_id,
            "title": title_text(row, "Title"),
            "fingerprint": rich_text_value(row, "Fingerprint"),
            "status": select_value(row, "Status"),
            "screen": rich_text_value(row, "Screen"),
            "domain": select_value(row, "Domain"),
            "description": rich_text_value(row, "Description"),
            "rationale": rich_text_value(row, "Rationale"),
            "hypothesis": rich_text_value(row, "Hypothesis"),
            "lever": rich_text_value(row, "Lever"),
            "impact": select_value(row, "Impact"),
            "effort": select_value(row, "Effort"),
            "slack": (row.get("properties", {}).get("Slack thread") or {}).get("url") or "",
            "linear": (row.get("properties", {}).get("Linear issue") or {}).get("url") or "",
            "pr": (row.get("properties", {}).get("PR") or {}).get("url") or "",
            "note": rich_text_value(row, "Quick status note"),
            "instrumentation": rich_text_value(row, "Instrumentation"),
            "experiment": rich_text_value(row, "Experiment name"),
            "screenshot_url": _first_file_url(row, "Screenshot"),
            "video_url": _first_file_url(row, "Video"),
        })
    num = _ref_idea_num(ref)
    if num is not None:
        hit = [r for r in rows if r["idea_num"] == num]
        if len(hit) == 1:
            return hit[0]
        raise SystemExit(f"no idea has id {num} (IDEA-{num})")
    exact = [r for r in rows if r["fingerprint"] == ref.lower()]
    if len(exact) == 1:
        return exact[0]
    matches = [r for r in rows if ref.lower() in (r["title"] or "").lower()]
    if not matches:
        raise SystemExit(f"no idea matches '{ref}'")
    if len(matches) > 1:
        lines = "\n".join(f"  {r['idea_id'] or r['fingerprint']}  [{r['status']}] {r['title'][:70]}"
                          for r in matches)
        raise SystemExit(f"'{ref}' matched {len(matches)} rows — use an Idea ID:\n{lines}")
    return matches[0]


# Every card the loop files starts with this, so loop work is findable on any board.
CARD_PREFIX = "Loop: "
# The workspace-wide `Loop` label, on every idea card and sub-card.
LOOP_LABEL = "7d13621b-96dc-4092-a085-ae61aa438063"

def render_card(idea: dict[str, Any], experiment: str) -> tuple[str, str]:
    """(title, body) for the Linear card, in the PROD-2126 shape."""
    title = f"{CARD_PREFIX}ab:{experiment}: {idea['title']}"
    body = "\n".join([
        "|  |  |",
        "| -- | -- |",
        f"| **Experiment** | `{experiment}` |",
        f"| **Variants** | `a` = treatment, `b` / `nil` = control |",
        f"| **Surface** | `{idea['screen'] or '?'}` (domain: {idea['domain'] or '?'}) |",
        f"| **Primary metric** | {idea['lever'] or '?'} |",
        "",
        "## Overview",
        "",
        idea["description"] or "_(no description on the idea row)_",
        "",
        "## Why",
        "",
        idea["rationale"] or "_(no rationale on the idea row)_",
        "",
        "## Hypothesis",
        "",
        idea["hypothesis"] or "_(no hypothesis on the idea row)_",
        "",
        "## Integration notes",
        "",
        f"Gate the treatment on `{experiment}` via `experimentService.assignExperiments([...])`. "
        "`a` = treatment, `b` / `nil` = control — the control path must be the current behaviour "
        "exactly, so a failed assignment degrades to what ships today.",
        "",
        "## Analytics",
        "",
        f"Fire the `{experiment}` exposure event where the surface actually renders, not at "
        "assignment. An experiment stamped at user creation cannot tell an unexposed user from "
        "an unaffected one — `dailyPractices2` recorded a churn effect it could not have caused "
        "because only 23 of 57 arm-a payers ever saw the feature.",
        "",
        "## Links",
        "",
        f"- Idea: `{idea.get('idea_id') or idea['fingerprint']}` in Learnings/Loop/Aurie Features Ideas",
        f"- Slack thread: {idea['slack'] or '_(not shared)_'}",
    ])
    return title, body


def cmd_plan(args: argparse.Namespace) -> int:
    if not notion.available:
        raise MCPFallback("NOTION_TOKEN is not set.")
    idea = find_idea(args.data_source_id, args.ref)
    experiment = args.experiment or idea["experiment"] or "<experimentName>"

    print(f"Idea       : {idea['title']}")
    print(f"Idea ID    : {idea.get('idea_id') or '(none)'}")
    print(f"Fingerprint: {idea['fingerprint']}")
    print(f"Status     : {idea['status']}")
    print(f"Screen     : {idea['screen'] or '-'}  (domain: {idea['domain'] or '-'})")
    print(f"Experiment : {experiment}")
    print(f"Parent     : {LOOP_PARENT['aurie-features']}")
    if idea["linear"]:
        print(f"\n! already has a Linear issue: {idea['linear']}")
    if idea["status"] != BUILDABLE_STATUS:
        print(f"\nREFUSED: status is '{idea['status']}', not '{BUILDABLE_STATUS}'. "
              f"Only an approved idea is buildable — anything else means nobody has said yes.",
              file=sys.stderr)
        return 2
    if is_held(idea.get("note") or ""):
        print(f"\nREFUSED: held for an architecture decision — {idea['note'][:300]}",
              file=sys.stderr)
        return 2
    refusal = pre_consent_refusal(idea)
    if refusal:
        print(f"\nREFUSED: {refusal}", file=sys.stderr)
        return 2
    title, body = render_card(idea, experiment)
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)
    print(body)
    return 0


# A row may be written by `finish` only from a state the build leg actually
# passes through. `find_idea` accepts a title substring, so without this a loose
# ref could stamp one idea's PR onto an unrelated — or already rejected — row.
FINISHABLE_STATUSES = {"approved", "implementing"}


# The feature card is an ENGMOB (Mobile) card so it sits on the engineers' board.
# It is created in that team's CURRENT cycle (resolved at creation, not hardcoded —
# cycles roll every two weeks) and moves as the build does: In Progress when work
# starts (`start`), In Review when the PR opens (`finish`). Merge/release stay with
# ship-idea. Todo → In Progress → In Review are these team-scoped state ids.
ENGMOB_TEAM = "5497338e-312b-4ddf-ac5f-3339399c956d"
ENGMOB_STATE_IN_PROGRESS = "2f3af5c5-ed04-459b-84b0-f377d75141e2"
ENGMOB_STATE_IN_REVIEW = "7b782b06-d9c5-41ba-8588-35b193d928ae"
ENGMOB_STATE_BLOCKED = "e557d016-a014-44bd-8f4a-8d4509d49810"
ENGMOB_STATE_TODO = "090653f2-f21a-4718-89cd-0438e781f82c"

# The note prefix that holds an approved row out of the build queue until a human makes
# an architecture decision. The features-loop build step skips any approved row whose
# note starts with it, so the next approved idea is built instead of this one again.
ARCH_HOLD = "ARCHITECTURE DECISION PENDING"
ARCH_DECIDED = "Architecture decision"


def is_held(note: str) -> bool:
    return (note or "").startswith(ARCH_HOLD)

_LINEAR_IDENT_RE = re.compile(r"/issue/([A-Z]+-\d+)", re.I)


def _card_identifier(url: str) -> str:
    """The `ENGMOB-123` identifier out of a Linear issue URL, or ''."""
    m = _LINEAR_IDENT_RE.search(url or "")
    return m.group(1).upper() if m else ""


def _linear(*argv: str) -> Optional[dict[str, Any]]:
    """Run the vendored `linear_api.py` and return its parsed JSON, or None."""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "linear_api.py")
    try:
        out = subprocess.run([sys.executable, script, *argv],
                             capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            return None
        return json.loads(out.stdout or "null")
    except (Exception, SystemExit):  # noqa: BLE001
        return None


def _pr_is_draft(pr_url: str) -> Optional[bool]:
    """`isDraft` for the PR via `gh`, or None when it cannot be read.

    In Review is the state a human reviews in, so it belongs to a PR that is
    actually ready — not a draft. `finish` records the PR while it is still a
    draft, so it must not blindly mark the card In Review.
    """
    try:
        out = subprocess.run(["gh", "pr", "view", pr_url, "--json", "isDraft"],
                             capture_output=True, text=True, timeout=30)
        if out.returncode != 0:
            return None
        return bool(json.loads(out.stdout or "{}").get("isDraft"))
    except (Exception, SystemExit):  # noqa: BLE001
        return None


def move_card(card_url: str, state_id: str, label: str) -> None:
    """Move the feature card to `state_id`. Best-effort — a Linear miss never fails
    the Notion write that is the command's real job; it just prints a warning.

    Setting a state the card is already in is a no-op, so this is idempotent and
    safe to re-run.
    """
    ident = _card_identifier(card_url)
    if not ident:
        print(f"  ! no Linear card to move to {label} (no identifier in {card_url!r})",
              file=sys.stderr)
        return
    got = _linear("get-issue", "--id", ident)
    uuid = (got or {}).get("id")
    if not uuid:
        print(f"  ! could not resolve {ident} to move it to {label}", file=sys.stderr)
        return
    tmp = ""
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump({"id": uuid, "stateId": state_id}, fh)
            tmp = fh.name
        res = _linear("update-issue", "--input-file", tmp)
        if res and res.get("identifier"):
            print(f"  card → {label}: {res['identifier']}")
        else:
            print(f"  ! card not moved to {label} — do it by hand on {ident}", file=sys.stderr)
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def cmd_start(args: argparse.Namespace) -> int:
    """Record the Linear card on the row and move it to `implementing`.

    The card itself is created by `shared:add-linear-issues` — one Linear client
    in the repo, not two. This is the Notion half, which nothing else does: until
    it runs, an active build is still filed as `approved` and the row has no link
    to the card being built from it.
    """
    if not notion.available:
        raise MCPFallback("NOTION_TOKEN is not set.")
    idea = find_idea(args.data_source_id, args.ref)
    if idea["status"] != BUILDABLE_STATUS:
        print(f"REFUSED: status is '{idea['status']}', not '{BUILDABLE_STATUS}'.",
              file=sys.stderr)
        return 2
    if is_held(idea.get("note") or ""):
        print("REFUSED: the idea is held for an architecture decision — record it with "
              "`unhold --decision …` before starting the build.", file=sys.stderr)
        return 2
    refusal = pre_consent_refusal(idea)
    if refusal:
        print(f"REFUSED: {refusal}", file=sys.stderr)
        return 2
    card = args.linear or idea["linear"]
    if not card:
        print("REFUSED: no card — run `card` first.", file=sys.stderr)
        return 2
    args.linear = card
    props: dict[str, Any] = {
        "Linear issue": {"url": card},
        "Status": make_select("implementing"),
    }
    if args.experiment:
        props["Experiment name"] = make_rich_text(args.experiment)
    notion.update_page_props(idea["page_id"], props)
    print(f"~ {idea['fingerprint']} [{idea['status']} -> implementing]  {idea['title'][:60]}")
    print(f"  card: {args.linear}")
    if args.experiment:
        print(f"  experiment: {args.experiment}")
    # Work has started, so the card moves onto the engineers' board as In Progress.
    move_card(args.linear, ENGMOB_STATE_IN_PROGRESS, "In Progress")
    return 0


def cmd_finish(args: argparse.Namespace) -> int:
    """Record the PR on the row. Never merges anything.

    The status stays `implementing` — a PR being open is not a distinct state the
    loop needs, and GitHub tracks it better. What the row needs is the PR link and
    the media, so the release gate has something to look at.
    """
    if not notion.available:
        raise MCPFallback("NOTION_TOKEN is not set.")
    idea = find_idea(args.data_source_id, args.ref)
    if idea["status"] not in FINISHABLE_STATUSES:
        print(f"REFUSED: status is '{idea['status']}'. A PR can only be recorded on a row "
              f"the build leg is actually working — one of {sorted(FINISHABLE_STATUSES)}.\n"
              f"  Resolution accepts a title substring, so this guard is what stops a loose\n"
              f"  ref stamping this PR onto an unrelated or already-rejected idea.",
              file=sys.stderr)
        return 2
    props: dict[str, Any] = {
        "PR": {"url": args.pr},
        "Status": make_select("implementing"),
    }
    if args.note:
        props["Quick status note"] = make_rich_text(args.note)
    notion.update_page_props(idea["page_id"], props)
    print(f"~ {idea['fingerprint']} still implementing — PR recorded  {idea['title'][:60]}")
    print(f"  PR: {args.pr}")
    # In Review is where a human reviews, so it belongs to a PR that is actually
    # ready. `finish` runs at step 6 while the PR is still a draft (step 7 is
    # `gh pr ready`), so a draft holds In Progress — matching add-linear-issues'
    # draft→In Progress / ready→In Review convention. `ready` moves it when the PR
    # is marked ready. If finish is run after the PR is already ready, move it now.
    if idea["linear"]:
        draft = _pr_is_draft(args.pr)
        if draft is False:
            move_card(idea["linear"], ENGMOB_STATE_IN_REVIEW, "In Review")
        else:
            why = "still a draft" if draft else "draft state unreadable"
            print(f"  card stays In Progress ({why}) — run `build_idea.py ready {args.ref}` "
                  f"after `gh pr ready`.")

    if args.post:
        rc = _post_pr_opened(idea, args.pr)
        if rc != 0:
            return rc
    else:
        print(f"\nWould reply in the thread: PR opened for review: {args.pr}")
        print("\n-- not posted. Add --post to announce the PR in the idea's thread so a "
              "reviewer can judge it. --", file=sys.stderr)

    print("\nStopping here. Review, merge and release are human decisions — this skill "
          "does none of them.")
    return 0


def _post_pr_opened(idea: dict[str, Any], pr_url: str) -> int:
    """Reply the PR + both-variant media in the idea's thread. 0 ok, 2 refusal/failure.

    The media rides as follow-up uploads, so a failed upload cannot cost the PR post.
    The parent message only claims what is actually there: it never says "attached
    below" for media the row does not carry, and a follow-up names any upload that
    failed, so a reviewer is never told to look for media that is not in the thread.
    """
    if not idea["slack"]:
        print("REFUSED: the row has no Slack thread — nothing to reply to. An idea that "
              "was never shared has no thread the team is watching.", file=sys.stderr)
        return 2
    parsed = parse_permalink(idea["slack"])
    if not parsed:
        print(f"REFUSED: could not read a channel and timestamp out of {idea['slack']}. "
              f"Expected a Slack archives permalink.", file=sys.stderr)
        return 2
    channel, ts = parsed
    # Only the media the row actually carries — the claim below names exactly these,
    # so a reviewer is never sent looking for a type that is not there.
    media = []
    if idea.get("screenshot_url"):
        media.append(("Both-variant screenshots", idea["screenshot_url"], "screenshots"))
    if idea.get("video_url"):
        media.append(("Both-variant walkthrough", idea["video_url"], "video"))
    text = f"PR opened for review: {pr_url}"
    if media:
        phrase = " and ".join(noun for _, _, noun in media)
        text += f"\nSee the {phrase} of both variants in this thread, and on the card."
    else:
        text += "\nScreenshots and video are on the card and the idea row."

    from slack_post import post_message  # noqa: PLC0415
    resp = post_message(channel=channel, text=text, thread_ts=ts,
                        footer_skill="product:build-idea")
    if not resp.get("ok"):
        print(f"\nSlack refused the post: {resp.get('error')}", file=sys.stderr)
        return 2

    # Nothing after the PR reply may fail the run — a retry would duplicate that
    # reply. Uploads are guarded in _attach_media; the failure note is guarded here
    # too, since slack_post sys.exits on a non-JSON / curl failure.
    failed = [comment for comment, url, _ in media
              if not _attach_media(channel, ts, url, idea["fingerprint"], comment)]
    if failed:
        try:
            post_message(channel=channel,
                         text=f"(Couldn't attach: {', '.join(failed)} — see the card.)",
                         thread_ts=ts, footer_skill="product:build-idea")
        except (Exception, SystemExit) as exc:  # noqa: BLE001
            print(f"  ! could not post the media-failure note ({exc})", file=sys.stderr)
    print(f"\nreplied in thread ts={resp.get('ts')}", file=sys.stderr)
    return 0


def _reply_in_thread(idea: dict[str, Any], text: str) -> bool:
    """A reply in the idea's own thread — every update about an idea lives there."""
    parsed = parse_permalink(idea.get("slack") or "")
    if not parsed:
        print("  ! no Slack thread on the row — nothing to reply to", file=sys.stderr)
        return False
    channel, ts = parsed
    try:
        from slack_post import post_message  # noqa: PLC0415
        resp = post_message(channel=channel, text=text, thread_ts=ts,
                            footer_skill="product:build-idea")
    except (Exception, SystemExit) as exc:  # noqa: BLE001
        print(f"  ! could not reply in the thread ({exc})", file=sys.stderr)
        return False
    return bool(resp.get("ok"))


def _comment_on_card(card_url: str, body: str) -> None:
    ident = _card_identifier(card_url)
    got = _linear("get-issue", "--id", ident) if ident else None
    uuid = (got or {}).get("id")
    if not uuid:
        print("  ! no Linear card to comment on", file=sys.stderr)
        return
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as fh:
        fh.write(body)
    try:
        if _linear("comment", "--issue", uuid, "--body-file", fh.name) is None:
            print("  ! could not comment on the card", file=sys.stderr)
    finally:
        os.unlink(fh.name)


def plan_blocks(markdown: str, day: str) -> list[dict[str, Any]]:
    """The build plan as Notion blocks under a dated heading. `## x` is a sub-heading,
    `- x` / `* x` a bullet, a blank line a paragraph break; everything else a paragraph.
    Inline `**bold**`, `[link](url)` and `` `code` `` render through `rich_text`."""
    blocks: list[dict[str, Any]] = [block_divider(), block_heading(f"Build plan — {day}", 2)]
    para: list[str] = []

    def flush() -> None:
        if para:
            blocks.append(block_paragraph(" ".join(para)[:2000]))
            para.clear()

    for raw in markdown.splitlines():
        line = raw.rstrip()
        if not line.strip():
            flush()
        elif line.lstrip().startswith(("## ", "### ")):
            flush()
            blocks.append(block_heading(line.lstrip("# ").strip()[:2000], 3))
        elif line.lstrip().startswith(("- ", "* ")):
            flush()
            blocks.append(block_bullet(line.lstrip()[2:].strip()[:2000]))
        else:
            para.append(line.strip())
    flush()
    return blocks


# ---------- the feature card, created at planning --------------------------------
#
# The card exists from the plan on: the plan is posted to it, the Slack thread is
# attached to it, and the thread gets the card's link. Sub-cards (a backend change
# the feature needs, say) sit under it. Build, PR and review then move this card.

def _linear_input(payload: dict[str, Any], *argv: str) -> Optional[dict[str, Any]]:
    """Run a linear_api subcommand that takes an `--input-file` JSON payload."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump(payload, fh)
    try:
        return _linear(*argv, "--input-file", fh.name)
    finally:
        os.unlink(fh.name)


def _team_ids(key: str) -> tuple[str, str]:
    """(team id, Todo state id) for a Linear team key such as ENGBACK, or ('', '')."""
    try:
        from linear_api import gql  # noqa: PLC0415
        res = gql("""query($key: String!) { teams(filter: {key: {eq: $key}}) { nodes {
                       id states(filter: {name: {eq: "Todo"}}) { nodes { id } } } } }""",
                  {"key": key}, soft=True)
    except (Exception, SystemExit):  # noqa: BLE001
        return "", ""
    nodes = ((res or {}).get("teams") or {}).get("nodes") or []
    if not nodes:
        return "", ""
    states = (nodes[0].get("states") or {}).get("nodes") or []
    return nodes[0]["id"], (states[0]["id"] if states else "")


def _create_card(title: str, body: str, parent_ident: str, team_id: str = ENGMOB_TEAM,
                 state_id: str = ENGMOB_STATE_TODO) -> Optional[dict[str, Any]]:
    """A card in the team's current cycle, under `parent_ident`, assigned to the operator."""
    parent = _linear("get-issue", "--id", parent_ident)
    if not (parent or {}).get("id"):
        print(f"  ! could not resolve parent {parent_ident}", file=sys.stderr)
        return None
    payload: dict[str, Any] = {"teamId": team_id, "title": title, "description": body,
                               "parentId": parent["id"], "labelIds": [LOOP_LABEL]}
    if state_id:
        payload["stateId"] = state_id
    cycle = _linear("current-cycle", "--team", team_id)
    if (cycle or {}).get("id"):
        payload["cycleId"] = cycle["id"]
    me = _linear("whoami")
    if (me or {}).get("id"):
        payload["assigneeId"] = me["id"]
    return _linear_input(payload, "create-issue")


_LINK_SLACK = """
    mutation($issueId: String!, $url: String!) {
      attachmentLinkSlack(issueId: $issueId, url: $url, syncToCommentThread: true) { success }
    }"""


def _link_slack(card_uuid: str, url: str) -> bool:
    """Linear's own Slack link — what Slack's "Connect to apps → Link existing… Linear"
    makes: the thread shows on the issue and comments sync both ways. Needs the Linear
    app in the channel. A plain URL attachment of the same thread is replaced by it."""
    try:
        from linear_api import gql  # noqa: PLC0415
        vars_ = {"issueId": card_uuid, "url": url}
        res = gql(_LINK_SLACK, vars_, soft=True)
        if res is None:
            got = gql("""query($id: String!) { issue(id: $id) {
                           attachments { nodes { id url sourceType } } } }""",
                      {"id": card_uuid}, soft=True)
            plain = [a for a in (((got or {}).get("issue") or {}).get("attachments") or {})
                     .get("nodes", []) if a["url"] == url and a.get("sourceType") != "slack"]
            if not plain:
                return False
            gql("mutation($id: String!) { attachmentDelete(id: $id) { success } }",
                {"id": plain[0]["id"]}, soft=True)
            res = gql(_LINK_SLACK, vars_, soft=True)
    except (Exception, SystemExit):  # noqa: BLE001
        return False
    return bool(((res or {}).get("attachmentLinkSlack") or {}).get("success"))


def _attach_thread(card_uuid: str, idea: dict[str, Any]) -> None:
    """Link the idea's Slack thread to the card: the Slack-synced link, or a plain URL
    attachment when Linear's Slack integration refuses it. Linear keys attachments on
    the URL, so re-running updates the same one rather than adding a second."""
    if not idea.get("slack"):
        return
    if _link_slack(card_uuid, idea["slack"]):
        return
    label = f"Slack thread — {idea.get('idea_id') or idea['fingerprint']}"
    if _linear("attach-url", "--issue", card_uuid, "--url", idea["slack"],
               "--title", label) is None:
        print("  ! could not attach the Slack thread to the card", file=sys.stderr)


def cmd_card(args: argparse.Namespace) -> int:
    """Create (or adopt) the idea's Linear card, link it to the thread, record it on the row."""
    if not notion.available:
        raise MCPFallback("NOTION_TOKEN is not set.")
    idea = find_idea(args.data_source_id, args.ref)
    if idea["status"] not in FINISHABLE_STATUSES:
        print(f"REFUSED: status is '{idea['status']}' — a card is made for an approved idea.",
              file=sys.stderr)
        return 2
    url = idea["linear"] or args.linear or ""
    new = not idea["linear"]
    experiment = args.experiment or idea["experiment"] or "<experimentName>"
    if not url:
        title, body = render_card(idea, experiment)
        if not args.post:
            print(f"-- dry run: would create\n{title}\n\n{body}", file=sys.stderr)
            return 0
        made = _create_card(title, body, LOOP_PARENT["aurie-features"])
        if not (made or {}).get("url"):
            print("REFUSED: Linear did not create the card.", file=sys.stderr)
            return 2
        url = made["url"]
    elif not args.post:
        print(f"-- dry run: would link {url}", file=sys.stderr)
        return 0
    got = _linear("get-issue", "--id", _card_identifier(url))
    if (got or {}).get("id"):
        _attach_thread(got["id"], idea)
    if new:
        notion.update_page_props(idea["page_id"], {"Linear issue": {"url": url}})
        _reply_in_thread(idea, f"Linear card: {url}")
    print(f"~ {idea['idea_id'] or idea['fingerprint']} card {url}")
    return 0


def cmd_sub_card(args: argparse.Namespace) -> int:
    """A sub-card under the idea's card — for work the feature needs outside its PR."""
    if not notion.available:
        raise MCPFallback("NOTION_TOKEN is not set.")
    idea = find_idea(args.data_source_id, args.ref)
    if not idea["linear"]:
        print("REFUSED: the idea has no card yet — run `card` first.", file=sys.stderr)
        return 2
    with open(args.body_file, encoding="utf-8") as fh:
        body = fh.read()
    title = args.title if args.title.startswith(CARD_PREFIX) else CARD_PREFIX + args.title
    if not args.post:
        print(f"-- dry run: would create under {idea['linear']}\n{title}\n\n{body}",
              file=sys.stderr)
        return 0
    team_id, state_id = ENGMOB_TEAM, ENGMOB_STATE_TODO
    if args.team and args.team.upper() != "ENGMOB":
        team_id, state_id = _team_ids(args.team.upper())
        if not team_id:
            print(f"REFUSED: no Linear team with key {args.team}.", file=sys.stderr)
            return 2
    made = _create_card(title, body, _card_identifier(idea["linear"]), team_id, state_id)
    if not (made or {}).get("url"):
        print("REFUSED: Linear did not create the sub-card.", file=sys.stderr)
        return 2
    _attach_thread(made["id"], idea)
    _reply_in_thread(idea, f"Sub-card for this idea: {made['url']}")
    print(f"~ sub-card {made['url']}")
    return 0


def cmd_record_plan(args: argparse.Namespace) -> int:
    """Write the build plan onto the idea's Notion row (its page body), dated.

    The plan is the build's first artifact: what changes, where, how it is bucketed and
    exposed, what it needs outside the app, the risks, and the open questions. It lives
    on the row so the decision it leads to — continue, or `hold` — has its reasoning next
    to it. Appending keeps earlier plans (a re-plan after a hold) as history.
    """
    if not notion.available:
        raise MCPFallback("NOTION_TOKEN is not set.")
    idea = find_idea(args.data_source_id, args.ref)
    with open(args.file, encoding="utf-8") as fh:
        text = fh.read()
    if not text.strip():
        print("REFUSED: the plan file is empty.", file=sys.stderr)
        return 2
    from datetime import date  # noqa: PLC0415
    blocks = plan_blocks(text, date.today().isoformat())
    for i in range(0, len(blocks), 100):          # Notion appends at most 100 per call
        notion.append_block_children(idea["page_id"], blocks[i:i + 100])
    print(f"~ {idea['idea_id'] or idea['fingerprint']} build plan recorded "
          f"({len(blocks) - 2} blocks)  {idea['title'][:60]}")
    if idea.get("linear"):
        _comment_on_card(idea["linear"], f"**Build plan — {date.today().isoformat()}**\n\n{text}")
        print("  plan posted to the card")
    else:
        print("  ! no card on the row — run `card` first so the plan reaches it", file=sys.stderr)
    return 0


def cmd_hold(args: argparse.Namespace) -> int:
    """Hold an approved idea for an architecture decision, before any code is written.

    The decision and its options go on the row (as the note prefix that keeps the loop
    from re-dispatching it), on the Linear card (moved to Blocked), and — with --post —
    as a reply in the idea's thread. The row stays `approved`: nothing was built.
    """
    if not notion.available:
        raise MCPFallback("NOTION_TOKEN is not set.")
    idea = find_idea(args.data_source_id, args.ref)
    if idea["status"] != "approved":
        print(f"REFUSED: {idea['idea_id'] or idea['fingerprint']} is {idea['status']!r} — "
              f"hold is for an approved idea before its build starts.", file=sys.stderr)
        return 2
    text = f"{ARCH_HOLD} — {args.decision.strip()}"
    note = idea.get("note") or ""
    # Re-holding replaces the open questions but keeps the history behind them.
    history = (note.split(" | ", 1)[1] if " | " in note else "") if is_held(note) else note
    if history:
        text += f" | {history}"
    notion.update_page_props(idea["page_id"], {"Quick status note": make_rich_text(text[:2000])})
    print(f"~ {idea['fingerprint']} held for an architecture decision  {idea['title'][:60]}")
    if idea["linear"]:
        move_card(idea["linear"], ENGMOB_STATE_BLOCKED, "Blocked")
        _comment_on_card(idea["linear"], f"**{ARCH_HOLD}**\n\n{args.decision.strip()}")
    if args.post:
        _reply_in_thread(idea, f"Build paused before any code: it needs an architecture "
                               f"decision.\n{args.decision.strip()}")
    return 0


def cmd_unhold(args: argparse.Namespace) -> int:
    """Record the architecture decision and release the row back into the build queue."""
    if not notion.available:
        raise MCPFallback("NOTION_TOKEN is not set.")
    idea = find_idea(args.data_source_id, args.ref)
    if not is_held(idea.get("note") or ""):
        print(f"REFUSED: {idea['idea_id'] or idea['fingerprint']} is not held.", file=sys.stderr)
        return 2
    rest = idea["note"].split(" | ", 1)[1] if " | " in idea["note"] else ""
    text = f"{ARCH_DECIDED}: {args.decision.strip()}" + (f" | {rest}" if rest else "")
    notion.update_page_props(idea["page_id"], {"Quick status note": make_rich_text(text[:2000])})
    print(f"~ {idea['fingerprint']} released — {args.decision.strip()[:80]}")
    if idea["linear"]:
        move_card(idea["linear"], ENGMOB_STATE_TODO, "Todo")
        _comment_on_card(idea["linear"], f"**{ARCH_DECIDED}:** {args.decision.strip()}")
    if args.post:
        _reply_in_thread(idea, f"Architecture decision: {args.decision.strip()} — the build resumes.")
    return 0


def cmd_ready(args: argparse.Namespace) -> int:
    """Move the feature card to In Review, once the PR is marked ready.

    The step-7 companion to `gh pr ready`: `finish` holds the card In Progress
    while the PR is a draft, and this moves it when a human opens it for review,
    Best-effort and idempotent, like every card move here. It requests no
    reviewer: who reviews is the team's call, not the loop's.
    """
    if not notion.available:
        raise MCPFallback("NOTION_TOKEN is not set.")
    idea = find_idea(args.data_source_id, args.ref)
    if not idea["linear"]:
        print(f"REFUSED: {idea['fingerprint']} has no Linear card to move — run `start` first.",
              file=sys.stderr)
        return 2
    move_card(idea["linear"], ENGMOB_STATE_IN_REVIEW, "In Review")
    print(f"~ {idea['fingerprint']} card → In Review (PR ready)  {idea['title'][:60]}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="build_idea", description=__doc__.split("\n")[0])
    p.add_argument("--data-source-id", default=AURIE_FEATURES_DS)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("plan", help="Resolve an approved idea and render the card body")
    sp.add_argument("ref", help="Fingerprint or title substring")
    sp.add_argument("--experiment", help="Experiment flag name (defaults to the row's)")
    sp.set_defaults(func=cmd_plan)

    sp = sub.add_parser("start", help="Record the card on the row and move it to implementing")
    sp.add_argument("ref")
    sp.add_argument("--linear", help="Defaults to the card `card` recorded on the row")
    sp.add_argument("--experiment", help="Experiment flag name")
    sp.set_defaults(func=cmd_start)

    sp = sub.add_parser("guard-variant", help="Refuse to force a variant on a non-test account")
    sp.add_argument("--email", help="The account the override would apply to")
    sp.add_argument("--experiment")
    sp.add_argument("--variant")
    sp.set_defaults(func=cmd_guard_variant)

    sp = sub.add_parser("guard-collision", help="Refuse a build colliding with a running experiment")
    sp.add_argument("--domain", required=True, choices=sorted(DOMAIN_KEYWORDS))
    sp.add_argument("--override", help="Why the overlap is safe — recorded on the card")
    sp.set_defaults(func=cmd_guard_collision)

    sp = sub.add_parser("finish", help="Record the PR and media on the row")
    sp.add_argument("ref")
    sp.add_argument("--pr", required=True, help="Pull request URL")
    sp.add_argument("--note")
    sp.add_argument("--post", action="store_true",
                    help="Announce the PR + both-variant media in the idea's thread")
    sp.set_defaults(func=cmd_finish)

    sp = sub.add_parser("card", help="Create the idea's Linear card, link it to the thread")
    sp.add_argument("ref")
    sp.add_argument("--linear", help="Adopt an existing card instead of creating one")
    sp.add_argument("--experiment", help="Experiment flag name for the card title")
    sp.add_argument("--post", action="store_true", help="Create/link it (default: dry run)")
    sp.set_defaults(func=cmd_card)

    sp = sub.add_parser("sub-card", help="A sub-card under the idea's card")
    sp.add_argument("ref")
    sp.add_argument("--title", required=True)
    sp.add_argument("--body-file", required=True, dest="body_file")
    sp.add_argument("--team", default="ENGMOB",
                    help="Linear team key for the work, e.g. ENGBACK for a service change")
    sp.add_argument("--post", action="store_true", help="Create it (default: dry run)")
    sp.set_defaults(func=cmd_sub_card)

    sp = sub.add_parser("record-plan", help="Write the build plan onto the idea's Notion row")
    sp.add_argument("ref")
    sp.add_argument("--file", required=True, help="The plan as Markdown (## headings, - bullets)")
    sp.set_defaults(func=cmd_record_plan)

    sp = sub.add_parser("hold", help="Hold an approved idea for an architecture decision")
    sp.add_argument("ref")
    sp.add_argument("--decision", required=True,
                    help="The question and its 2–3 options, as the human will read them")
    sp.add_argument("--post", action="store_true", help="Also reply in the idea's thread")
    sp.set_defaults(func=cmd_hold)

    sp = sub.add_parser("unhold", help="Record the architecture decision and release the row")
    sp.add_argument("ref")
    sp.add_argument("--decision", required=True, help="What was decided, and by whom")
    sp.add_argument("--post", action="store_true", help="Also reply in the idea's thread")
    sp.set_defaults(func=cmd_unhold)

    sp = sub.add_parser("ready", help="Move the card to In Review once the PR is ready")
    sp.add_argument("ref")
    sp.set_defaults(func=cmd_ready)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except MCPFallback as e:
        print(f"build-idea: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
