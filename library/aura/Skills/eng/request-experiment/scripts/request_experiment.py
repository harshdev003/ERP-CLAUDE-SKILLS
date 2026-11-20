"""Ask Viktor in #support-bot to create an A/B experiment, then verify what was created.

  version   The minimum version for the experiment, read from git. No writes.
  request   Render the ask (and with --post, post it as the user in #support-bot).
  check     Viktor's and AuraBot's confirmation in the thread, then the server config.

The skill never creates an experiment and never addresses AuraBot. Viktor owns
creation and documentation; this asks, waits, and checks the result.

Copied from product:ship-idea's request-experiment / check-experiment (principle 1),
without the idea-row state machine.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SUPPORT_BOT_CHANNEL = "C02FQE15YGP"
VIKTOR = "U0AV303B4VB"
AURABOT = "U02FZEU0TDH"
AURA_API = "https://services.aurahealth.io"

TARGET_AURIE = "ai"
TARGET_AURA = "aura"

# Where each app's next release version lives on its default branch.
APPS: dict[str, dict[str, str]] = {
    TARGET_AURIE: {"repo": "AuraHealth/AuraConsumerApp", "ios": "project.yml",
                   "bundle": "com.aurahealth.AuraConsumerApp"},
    TARGET_AURA: {"repo": "AuraHealth/Aura",
                  "ios": "ios/Aura.xcodeproj/project.pbxproj",
                  "android": "android/app/build.gradle"},
}

PR_URL_RE = re.compile(r"github\.com/([^/\s]+)/([^/\s]+)/pull/(\d+)")
TAG_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)")
ARM_RE = re.compile(r"(?:^|[,;]\s*|\s)([a-d]):\s")


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


# ---------- arms ------------------------------------------------------------

def arm_letters(arms: str) -> str:
    """The arm letters `--arms` describes, in order: 'a: x, b: control' -> 'ab'."""
    seen: list[str] = []
    for letter in ARM_RE.findall(" " + (arms or "")):
        if letter not in seen:
            seen.append(letter)
    return "".join(seen)


def arms_problem(arms: str) -> str:
    """Why these arms can't be requested, or ''."""
    letters = arm_letters(arms)
    if len(letters) < 2:
        return (f"--arms describes {len(letters)} arm(s). An experiment needs at least two, "
                f"each with its meaning: 'a: <treatment>, b: control'.")
    if letters != "abcd"[:len(letters)]:
        return f"--arms letters are '{letters}'; use consecutive letters from a."
    if "control" not in (arms or "").lower():
        return "--arms names no control arm. Say which arm is today's behaviour: 'b: control'."
    return ""


# ---------- the request text ------------------------------------------------

def _one_line(text: str) -> str:
    return " ".join((text or "").split())


def render_request(experiment: str, summary: str, arms: str, version: str,
                   target_app: str, platform: str, refs: list[str]) -> str:
    """The ask to Viktor: ACTIVE on every arm, with the minimum version.

    Always the full arm set as `variants`. Never "create it off on the control
    arm": an experiment created on one arm assigns nobody to the treatment, and
    AuraBot has no update command, so turning it on is a manual edit afterwards.
    """
    variants = arm_letters(arms)
    target = ("Aurie (targetApp `ai`)" if target_app == TARGET_AURIE
              else "Aura (no targetApp)")
    lines = [
        f"<@{VIKTOR}> please create experiment `{experiment}`, *active* on all arms "
        f"(variants `{variants}`), with minimum version *{version}*.",
        f"• Summary: {_one_line(summary).rstrip('.')}.",
        f"• Arms: {_one_line(arms)}",
        f"• App: {target}, platform `{platform}`",
        f"• Minimum version {version}: the first release that carries the change, "
        f"so nobody on an older build is assigned.",
    ]
    if refs:
        lines.append("• Refs: " + " · ".join(refs))
    lines.append("Please confirm once AuraBot has created it.")
    return "\n".join(lines)


# ---------- the minimum version, from git ------------------------------------

def _gh(args: list[str]) -> Optional[Any]:
    """`gh` JSON output, or None when gh fails. Never raises."""
    try:
        out = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    try:
        return json.loads(out.stdout)
    except ValueError:
        return None


def _gh_raw(repo: str, path: str, ref: str) -> str:
    """A file's text at `ref`, or '' when gh can't read it."""
    try:
        out = subprocess.run(["gh", "api", f"repos/{repo}/contents/{path}?ref={ref}",
                              "-H", "Accept: application/vnd.github.raw"],
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out.stdout if out.returncode == 0 else ""


def _vtuple(v: str) -> Optional[tuple[int, int, int]]:
    m = re.match(r"^\s*(\d+)\.(\d+)\.(\d+)", v or "")
    return tuple(int(x) for x in m.groups()) if m else None  # type: ignore[return-value]


def file_version(text: str, path: str) -> str:
    """The app version a version file declares, or ''.

    project.yml and project.pbxproj carry `MARKETING_VERSION` once per target and
    configuration (extensions and test targets can lag), so the highest is the
    app's. build.gradle carries `versionName`.
    """
    if path.endswith("build.gradle"):
        found = re.findall(r'versionName\s+"(\d+\.\d+\.\d+)"', text or "")
    else:
        found = re.findall(r'MARKETING_VERSION\s*[:=]\s*"?(\d+\.\d+\.\d+)', text or "")
    best = max((_vtuple(v) for v in found), default=None)
    return ".".join(map(str, best)) if best else ""


def _contains(repo: str, sha: str, ref: str) -> Optional[bool]:
    """Whether `ref` carries commit `sha`; None when GitHub can't say."""
    data = _gh(["api", f"repos/{repo}/compare/{sha}...{ref}"])
    if not isinstance(data, dict):
        return None
    return data.get("status") in ("ahead", "identical")


def _tags(repo: str) -> Optional[list[str]]:
    try:
        out = subprocess.run(["gh", "api", "--paginate", f"repos/{repo}/tags?per_page=100",
                              "--jq", ".[].name"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.split() if out.returncode == 0 else None


def app_store_version(bundle_id: str) -> str:
    """The version the App Store serves today, or '' when it can't be read."""
    url = f"https://itunes.apple.com/lookup?bundleId={bundle_id}&country=us"
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            results = json.loads(resp.read().decode("utf-8")).get("results") or []
    except (urllib.error.URLError, OSError, ValueError):
        return ""
    return (results[0].get("version") or "") if results else ""


def next_release_version(target_app: str, platform: str, pr_url: str) -> tuple[str, str]:
    """(version, '') or ('', why). The next release's version, proven to carry the PR.

    The version is the default branch's version file today. It is accepted only when
    the PR's merge commit is on that branch, and no build of that version has
    already been tagged without it: minVersion is a version, not a build, so a
    `1.33.11-1` cut before the merge would put users without the change inside
    `minimum version 1.33.11`. The exception is a build that never reached the App
    Store (only TestFlight testers run it): when the store is still below the
    version, the earlier build is noted, not refused. Every unreadable step refuses
    rather than guesses.
    """
    app = APPS[target_app]
    repo = app["repo"]
    path = app.get(platform)
    if not path:
        return "", (f"no version file for {target_app} on '{platform}'. Aura's iOS and Android "
                    f"versions differ, so one experiment minimum can't cover both; request one "
                    f"platform at a time.")
    m = PR_URL_RE.search(pr_url or "")
    if not m:
        return "", "no GitHub PR URL — pass --pr, or --version with the reason it is right."
    if f"{m.group(1)}/{m.group(2)}".lower() != repo.lower():
        return "", f"the PR is in {m.group(1)}/{m.group(2)}, but {target_app} ships from {repo}."
    pr = _gh(["pr", "view", pr_url, "--json", "state,mergeCommit"])
    if not isinstance(pr, dict):
        return "", f"could not read {pr_url} with gh."
    sha = (pr.get("mergeCommit") or {}).get("oid") or ""
    if pr.get("state") != "MERGED" or not sha:
        return "", (f"{pr_url} is not merged, so no build can be shown to carry it. Ask once it "
                    f"merges.")
    meta = _gh(["api", f"repos/{repo}"])
    branch = meta.get("default_branch") if isinstance(meta, dict) else None
    if not branch:
        return "", f"could not read {repo}'s default branch."
    version = file_version(_gh_raw(repo, path, branch), path)
    if not version:
        return "", f"could not read the version from {repo}/{path} on {branch}."
    on_branch = _contains(repo, sha, branch)
    if on_branch is None:
        return "", f"could not check whether {branch} carries the merge {sha[:10]}."
    if not on_branch:
        return "", f"{branch} does not carry the merge {sha[:10]}; it won't be in {version}."
    tags = _tags(repo)
    if tags is None:
        return "", f"could not list {repo}'s tags."
    want = _vtuple(version)
    cut = [t for t in tags if (tm := TAG_VERSION_RE.match(t))
           and tuple(int(x) for x in tm.groups()) == want]
    without = []
    for tag in cut:
        inside = _contains(repo, sha, tag)
        if inside is None:
            return "", f"could not check whether tag {tag} carries the merge."
        if not inside:
            without.append(tag)
    if without:
        store = app_store_version(app["bundle"]) if platform == "ios" and app.get("bundle") else ""
        store_v = _vtuple(store)
        if store_v is None or store_v >= want:
            seen = (f"the App Store has {store}" if store
                    else "the App Store version can't be read" if app.get("bundle") and platform == "ios"
                    else "this app has no store check")
            return "", (f"build {', '.join(without)} of {version} is cut without the merge and "
                        f"{seen}, so users on that build would be assigned under minimum version "
                        f"{version}. Wait for the version bump, or pass --version with the right one.")
        print(f"  note: {', '.join(without)} lacks the merge but is not on the App Store "
              f"(it has {store}); only TestFlight testers run it.", file=sys.stderr)
    return version, ""


# ---------- the Aura API read ------------------------------------------------

def experiment_record(name: str) -> tuple[Optional[bool], str, dict]:
    """(exists, detail, record). `None` means the question could not be answered.

    "Could not check" must never read as "not there yet", or as "verified".
    """
    token = os.environ.get("AURA_API_TOKEN") or os.environ.get("AURA_ADMIN_TOKEN")
    if not token:
        return None, "AURA_API_TOKEN is not set — cannot read the experiment.", {}
    req = urllib.request.Request(
        f"{AURA_API}/experiments/{name}",
        headers={"Authorization": f"Bearer {token}", "User-Agent": user_agent(),
                 "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False, "HTTP 404 — not created yet.", {}
        if e.code in (401, 403):
            return None, f"HTTP {e.code} — the token was rejected; 'unknown', not 'absent'.", {}
        return None, f"HTTP {e.code} — {e.reason}", {}
    except Exception as e:  # noqa: BLE001 — any transport failure is 'unknown'
        return None, f"{type(e).__name__}: {e}", {}
    try:
        record = json.loads(body)
    except ValueError:
        return None, "HTTP 200 but the body is not JSON.", {}
    if not isinstance(record, dict) or not record:
        return None, "HTTP 200 but no experiment object in the body.", {}
    return True, "HTTP 200", record


def config_problems(record: dict, variants: str, version: str, target_app: str,
                    platform: str) -> list[str]:
    """Every way the created experiment differs from what was asked."""
    out = []
    active = str(record.get("active") or "")
    if active != variants:
        out.append(f"active is '{active}', asked '{variants}'"
                   + (" — it was created on one arm only" if len(active) == 1 else ""))
    if str(record.get("minVersion") or "") != version:
        out.append(f"minVersion is '{record.get('minVersion') or ''}', asked '{version}'")
    app = record.get("targetApp")
    if target_app == TARGET_AURIE and app != TARGET_AURIE:
        out.append(f"targetApp is '{app}', asked 'ai'")
    if target_app == TARGET_AURA and app:
        out.append(f"targetApp is '{app}', asked none (Aura)")
    # A missing platform targets every platform, and the minimum version was read
    # for one: Aura's iOS and Android versions differ.
    got = record.get("targetPlatform")
    if got != platform:
        out.append(f"targetPlatform is '{got or ''}', asked '{platform}'")
    return out


# ---------- the thread -------------------------------------------------------

def thread_state(messages: list[dict], experiment: str) -> dict[str, Any]:
    """AuraBot's 'Created experiment `X`' and Viktor's last word in the request thread."""
    created = any(m.get("user") == AURABOT and f"Created experiment `{experiment}`" in (m.get("text") or "")
                  for m in messages[1:])
    viktor = [m for m in messages[1:] if m.get("user") == VIKTOR]
    return {"created": created, "viktor_last": (viktor[-1].get("text") or "") if viktor else ""}


# ---------- commands ---------------------------------------------------------

def thread_link(ts: str, as_user: bool = False) -> str:
    """The request's permalink; the archives URL when Slack won't give one."""
    from slack_post import get_permalink  # noqa: PLC0415
    try:
        link = (get_permalink(SUPPORT_BOT_CHANNEL, ts, as_user=as_user) or {}).get("permalink")
    except SystemExit:
        link = ""
    return link or f"https://auramobile.slack.com/archives/{SUPPORT_BOT_CHANNEL}/p{ts.replace('.', '')}"


def _resolve_version(args: argparse.Namespace) -> str:
    if args.version:
        print(f"minimum version {args.version} (given with --version — not checked against git)",
              file=sys.stderr)
        return args.version
    version, why = next_release_version(args.target_app, args.platform, args.pr or "")
    if not version:
        print(f"REFUSED: {why}", file=sys.stderr)
        return ""
    print(f"minimum version {version} (next release; carries {args.pr})", file=sys.stderr)
    return version


def cmd_version(args: argparse.Namespace) -> int:
    version = _resolve_version(args)
    if not version:
        return 2
    print(version)
    return 0


def cmd_request(args: argparse.Namespace) -> int:
    problem = arms_problem(args.arms)
    if problem:
        print(f"REFUSED: {problem}", file=sys.stderr)
        return 2
    version = _resolve_version(args)
    if not version:
        return 2
    refs = [r for r in (args.card, args.pr) if r]
    text = render_request(args.experiment, args.summary, args.arms, version,
                          args.target_app, args.platform, refs)
    print(text)
    if not args.post:
        print("\n-- not posted. Add --post to send it to #support-bot as you. --", file=sys.stderr)
        return 0

    from slack_post import post_message  # noqa: PLC0415

    resp = post_message(channel=SUPPORT_BOT_CHANNEL, text=text, as_user=True,
                        footer_skill="eng:request-experiment")
    if not resp.get("ok"):
        print(f"\nSlack refused the post: {resp.get('error')}", file=sys.stderr)
        return 2
    ts = resp.get("ts")
    link = thread_link(ts, as_user=True)
    print(f"\nposted to #support-bot ts={ts} {link}", file=sys.stderr)
    print(f"Next: check --experiment {args.experiment} --ts {ts} --arms ... --version {version}",
          file=sys.stderr)
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    from slack_read import replies  # noqa: PLC0415

    variants = arm_letters(args.arms)
    deadline = time.time() + args.wait * 60
    while True:
        state = thread_state(replies(SUPPORT_BOT_CHANNEL, args.ts), args.experiment)
        if state["created"] or time.time() >= deadline:
            break
        time.sleep(30)
    if state["viktor_last"]:
        print(f"Viktor: {_one_line(state['viktor_last'])[:400]}")
    if not state["created"]:
        print(f"Not created yet: no AuraBot 'Created experiment `{args.experiment}`' in the thread.")
        return 1

    exists, detail, record = experiment_record(args.experiment)
    print(f"{args.experiment}: {detail}")
    if not exists:
        print("UNVERIFIED — AuraBot confirmed, but the server record could not be read. "
              "Not reporting it as verified.", file=sys.stderr)
        return 2
    problems = config_problems(record, variants, args.version, args.target_app, args.platform)
    link = thread_link(args.ts)
    if problems:
        print("MISMATCH: " + "; ".join(problems))
        print(f"Thread: {link}")
        return 2
    print(f"VERIFIED: active '{variants}', minVersion {args.version}.")
    print(f"Thread: {link}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def app_flags(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--target-app", choices=[TARGET_AURIE, TARGET_AURA], required=True)
        sp.add_argument("--platform", choices=["ios", "android"], default="ios")

    v = sub.add_parser("version", help="The minimum version, from git.")
    app_flags(v)
    v.add_argument("--pr", help="GitHub PR URL of the change.")
    v.add_argument("--version", help="Override; skips the git check.")
    v.set_defaults(func=cmd_version)

    r = sub.add_parser("request", help="Render, and with --post send, the ask to Viktor.")
    app_flags(r)
    r.add_argument("--experiment", required=True)
    r.add_argument("--summary", required=True, help="One sentence: what the experiment tests.")
    r.add_argument("--arms", required=True, help="'a: <treatment>, b: control'")
    r.add_argument("--pr", help="GitHub PR URL of the change.")
    r.add_argument("--card", help="Linear card URL.")
    r.add_argument("--version", help="Override; skips the git check.")
    r.add_argument("--post", action="store_true")
    r.set_defaults(func=cmd_request)

    c = sub.add_parser("check", help="Confirmation in the thread, then the server config.")
    app_flags(c)
    c.add_argument("--experiment", required=True)
    c.add_argument("--ts", required=True, help="The request's ts in #support-bot.")
    c.add_argument("--arms", required=True)
    c.add_argument("--version", required=True)
    c.add_argument("--wait", type=int, default=0, help="Minutes to wait for AuraBot's confirmation.")
    c.set_defaults(func=cmd_check)
    return p


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
