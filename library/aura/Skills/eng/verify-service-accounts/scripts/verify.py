#!/usr/bin/env python3
"""Health-check every Aura service identity in one environment.

Enumerates the `SERVICE_TOKEN`-bearing `.env.<env>` files in an AuraInfra
checkout, then for each identity asserts that the stored credential still
*works* — not merely that it parses.

Two probes per identity, both read-only:

  POST {host}/users/verifyToken   -> does the token resolve to an account?
  GET  {host}/users/me            -> does that account still carry its role?

The second probe is the one that matters. `verifyToken` answers "this string
maps to a live account" and nothing more; it returns `verified: true` for an
account whose `role` has gone `null`, which is a principal that can no longer
authorize anything. `GET /users/me` goes through the same `userModel.retrieve`
call the `authorize` middleware reads `user.role` from, so a green run here
means the identity can do its job.

Token values are never printed. Identification is by `sha256[:12]` fingerprint.
"""

import argparse
import base64
import concurrent.futures
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request
import re


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


HOSTS = {
    "dev": "https://services.dev.aurahealth.io",
    "prod": "https://services.aurahealth.io",
}

CLUSTER_CONTEXTS = {
    "dev": "gke_aura-dev_us-central1-c_aura-dev-cluster-a",
    "prod": "gke_firebase-auratech16_us-central1-c_aura-prod-cluster-a",
}

FALLBACK_ROLE = "service"
MANIFEST_ROOT = "k8s/apps"

# Aura files a service identity under the sub-domain its account belongs to, and
# the role follows the sub-domain: `@services.*` accounts carry `service`,
# `@systems.*` carry `system` (emotion-system is the one `systems` identity).
# Deriving the expectation from the address gives a per-identity answer with no
# UID-to-role table to maintain, and the address is not the field that breaks —
# a role going null leaves the email untouched, so the check still fires.
DOMAIN_ROLE = {"services": "service", "systems": "system"}

INFRA_CANDIDATES = (
    "~/Development/Aura/Infra/AuraInfra",
    "~/AuraInfra",
    "~/code/AuraInfra",
    "~/repos/AuraInfra",
    "~/src/AuraInfra",
)


# --------------------------------------------------------------------------
# discovery
# --------------------------------------------------------------------------

def find_infra_dir(explicit: str | None) -> pathlib.Path:
    """Resolve the AuraInfra checkout, preferring an explicit path."""
    candidates = [explicit] if explicit else []
    if not explicit:
        candidates.append(os.getcwd())
        candidates.extend(INFRA_CANDIDATES)
    for c in candidates:
        if not c:
            continue
        p = pathlib.Path(c).expanduser()
        if (p / MANIFEST_ROOT).is_dir():
            return p
    raise SystemExit(
        "Could not find an AuraInfra checkout containing "
        f"{MANIFEST_ROOT}/. Pass --infra-dir."
    )


def enumerate_identities(infra: pathlib.Path, env: str) -> list[dict]:
    """Every `.env.<env>` under k8s/apps that defines SERVICE_TOKEN.

    Enumerated, never hardcoded: the set is whatever the manifests declare, so
    a service added outside `services/` (emotion-system lives under `ai/` in
    the `aura-ai` namespace) is picked up without a code change here.
    """
    out = []
    for path in sorted((infra / MANIFEST_ROOT).rglob(f".env.{env}")):
        token = read_env_value(path, "SERVICE_TOKEN")
        if token is None:
            continue
        rel = path.relative_to(infra)
        out.append(
            {
                "service": path.parts[-3],
                "group": path.parts[-4],
                "env_file": str(rel),
                "token": token,
            }
        )
    return out


def read_env_value(path: pathlib.Path, key: str) -> str | None:
    """First `KEY=value` in a dotenv file, quotes stripped."""
    prefix = f"{key}="
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip().strip('"').strip("'")
    return None


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


# --------------------------------------------------------------------------
# local claim inspection
# --------------------------------------------------------------------------

def decode_claims(token: str) -> dict:
    """JWT payload claims, decoded locally without verifying the signature.

    Informational only. A stored SERVICE_TOKEN is a Firebase ID token carrying
    a 1-hour `exp`, and on the persisted path user-service never re-checks it —
    so `exp` says nothing about whether the credential works. See
    references/service-token.md.
    """
    parts = token.split(".")
    if len(parts) != 3:
        return {"shape": "opaque"}
    try:
        raw = parts[1]
        payload = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    except Exception:
        return {"shape": "unparsable"}
    return {
        "shape": "jwt",
        "email": payload.get("email"),
        "aud": payload.get("aud"),
        "sub": payload.get("sub"),
        "iat": payload.get("iat"),
        "exp": payload.get("exp"),
    }


def iso(ts) -> str:
    if not isinstance(ts, (int, float)):
        return "-"
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


# --------------------------------------------------------------------------
# probes
# --------------------------------------------------------------------------

def http_json(url: str, timeout: int, headers=None, body=None):
    """Return (status, parsed-or-None, error-string-or-None)."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": user_agent(), **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            status = resp.status
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        status = exc.code
    except Exception as exc:
        return None, None, f"{type(exc).__name__}: {exc}"
    try:
        return status, json.loads(text), None
    except ValueError:
        # A reached-but-refused endpoint (`sendStatus(403)` sends a bare status
        # phrase). The status carries the verdict, so this is not an error.
        return status, None, None


def probe(identity: dict, host: str, timeout: int) -> dict:
    token = identity["token"]
    result = dict(identity)
    result.pop("token")
    result["fingerprint"] = fingerprint(token)
    result["claims"] = decode_claims(token)

    status, body, err = http_json(
        f"{host}/users/verifyToken", timeout, body={"token": token}
    )
    result["verify"] = {
        "status": status,
        "error": err,
        # `verifyToken` answers HTTP 200 for both verdicts — the verdict is in
        # the body, so never gate on the status code.
        "verified": bool(body.get("verified")) if isinstance(body, dict) else None,
        "id": body.get("id") if isinstance(body, dict) else None,
        # Present only when user-service reached Firebase. On the persisted
        # path it resolves the token straight from its token table and omits
        # the field, which is the normal case for a service account.
        "tokenUserId": body.get("tokenUserId") if isinstance(body, dict) else None,
    }

    status, body, err = http_json(
        f"{host}/users/me", timeout, headers={"Authorization": f"Bearer {token}"}
    )
    result["account"] = {
        "status": status,
        "error": err,
        "id": body.get("id") if isinstance(body, dict) else None,
        "role": body.get("role") if isinstance(body, dict) else None,
        "email": body.get("email") if isinstance(body, dict) else None,
        "deletedAt": body.get("deletedAt") if isinstance(body, dict) else None,
    }
    return result


def cluster_fingerprint(context: str, namespace: str, secret: str, timeout: int):
    """sha256[:12] of the live Secret's SERVICE_TOKEN, or an error string.

    The token value is fingerprinted in-process and never printed or written.
    """
    try:
        proc = subprocess.run(
            [
                "kubectl", "--context", context, "-n", namespace,
                "get", "secret", secret,
                "-o", "jsonpath={.data.SERVICE_TOKEN}",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if proc.returncode != 0:
        return None, (proc.stderr or "kubectl failed").strip().splitlines()[0][:160]
    if not proc.stdout.strip():
        return None, "no SERVICE_TOKEN key in the live Secret"
    try:
        value = base64.b64decode(proc.stdout).decode("utf-8", errors="replace")
    except Exception as exc:
        return None, f"undecodable Secret value: {exc}"
    return fingerprint(value), None


# --------------------------------------------------------------------------
# judgement
# --------------------------------------------------------------------------

def sealed_secret_state(infra: pathlib.Path, identity: dict) -> tuple[str, str | None]:
    """Whether a committed SealedSecret declares SERVICE_TOKEN for this identity.

    Only presence is checkable. SealedSecret values are RSA+AES ciphertext with
    a random session key, so the same plaintext seals to a different string
    every time — a committed value can never be compared against the `.env`
    one, by fingerprint or otherwise.
    """
    env_dir = (infra / identity["env_file"]).parent
    files = sorted(env_dir.glob("*secret*.json"))
    if not files:
        return "missing", "no committed *-secret.json beside the .env file"
    for f in files:
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except Exception as exc:
            return "unreadable", f"{f.name}: {exc}"
        if "SERVICE_TOKEN" in doc.get("spec", {}).get("encryptedData", {}):
            return "present", None
    return "missing", f"{files[0].name} declares no SERVICE_TOKEN"


def baseline_entry(baseline: dict | None, service: str) -> dict:
    """One identity's pinned expectations, tolerating a bare-UID string."""
    if not baseline:
        return {}
    entry = baseline.get(service)
    if isinstance(entry, str):
        return {"uid": entry}
    return entry if isinstance(entry, dict) else {}


def expected_role_for(row: dict, override: str | None, baseline: dict | None) -> str:
    """The role this identity is supposed to carry.

    Precedence: an explicit `--expected-role` (applies to every identity), then
    a pinned baseline role, then the account's address sub-domain, then
    `service`. Roles differ per identity — asserting one global value reports a
    healthy `system` account as broken.
    """
    if override:
        return override
    pinned = baseline_entry(baseline, row["service"]).get("role")
    if pinned:
        return pinned
    # Fall back to the JWT's own email when /users/me could not be reached, so
    # an identity that fails to authenticate is still held to the right role.
    email = row["account"]["email"] or row["claims"].get("email") or ""
    label = email.split("@")[-1].split(".")[0]
    return DOMAIN_ROLE.get(label, FALLBACK_ROLE)


def judge(row: dict, expected_role: str | None, baseline: dict | None) -> dict:
    """Collapse the probes into a verdict plus the reasons behind it."""
    fails, warns, notes = [], [], []
    expected_role = expected_role_for(row, expected_role, baseline)
    row["expected_role"] = expected_role
    verify, account = row["verify"], row["account"]
    service = row["service"]

    if verify["error"]:
        fails.append(f"verifyToken unreachable ({verify['error']})")
    elif verify["status"] != 200:
        # verifyToken answers 200 for both verdicts, so anything else is the
        # endpoint failing rather than a verdict about the credential. Saying
        # "the token resolves to no account" here would be a wrong diagnosis.
        fails.append(f"verifyToken returned HTTP {verify['status']} — the endpoint is failing, not the token")
    elif verify["verified"] is not True:
        fails.append("verifyToken returned verified=false — the token resolves to no account")
    elif not verify["id"]:
        fails.append("verifyToken verified but returned no id")

    # Assert the pair only when the server sent both. On the persisted path
    # `tokenUserId` is absent, so requiring it would fail every long-lived
    # service identity.
    if verify["tokenUserId"] and verify["id"] and verify["tokenUserId"] != verify["id"]:
        fails.append(f"tokenUserId {verify['tokenUserId']} != id {verify['id']}")

    if account["error"]:
        fails.append(f"GET /users/me unreachable ({account['error']})")
    elif account["status"] != 200:
        fails.append(
            f"GET /users/me returned {account['status']} — the identity cannot authenticate"
        )
    else:
        if account["role"] != expected_role:
            fails.append(
                f"role is {account['role']!r}, expected {expected_role!r} — "
                "the identity authenticates but authorizes nothing"
            )
        if account["deletedAt"]:
            fails.append(f"account is soft-deleted (deletedAt={account['deletedAt']})")
        if verify["id"] and account["id"] and verify["id"] != account["id"]:
            fails.append(f"verifyToken id {verify['id']} != /users/me id {account['id']}")
        # The account the credential resolves to must be the one named by the
        # directory it is stored under. Catches a token copied into the wrong
        # service's env file.
        local = (account["email"] or "").split("@")[0]
        if local and local != service:
            fails.append(
                f"identity mismatch: this is {service}'s env file but the token "
                f"resolves to {local!r}"
            )

    if baseline is not None:
        expected_uid = baseline_entry(baseline, service).get("uid")
        got = account["id"] or verify["id"]
        if expected_uid and got and expected_uid != got:
            fails.append(f"UID changed: baseline {expected_uid}, live {got}")
        elif not expected_uid and got:
            warns.append("not in the baseline — new identity")

    if row["sealed"][0] != "present":
        warns.append(f"sealed secret: {row['sealed'][1]}")

    cluster = row.get("cluster")
    if cluster:
        if cluster["error"]:
            warns.append(f"live Secret unreadable: {cluster['error']}")
        elif cluster["fingerprint"] != row["fingerprint"]:
            warns.append(
                f"live Secret differs from the .env file "
                f"({cluster['fingerprint']} vs {row['fingerprint']})"
            )

    claims = row["claims"]
    exp = claims.get("exp")
    if isinstance(exp, (int, float)) and exp < time.time():
        # Informational, never a failure. user-service persists a token on
        # first successful verification and answers from that table forever
        # after, so an `exp` in the past has no bearing on whether the
        # credential works — 26 of 28 identities are in exactly this state.
        notes.append(f"JWT exp {iso(exp)} is past (not a fault — see references/service-token.md)")
    elif claims.get("shape") != "jwt":
        notes.append(f"token is not a JWT ({claims.get('shape')})")

    row["verdict"] = "FAIL" if fails else ("WARN" if warns else "OK")
    row["fails"], row["warns"], row["notes"] = fails, warns, notes
    return row


# --------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------

def render(env: str, rows: list[dict]) -> str:
    lines = [
        f"## {env} — {HOSTS[env]}",
        "",
        "| Service | UID | Verified | Role | Expected | Token exp | Verdict |",
        "|---|---|---|---|---|---|---|",
    ]
    mark = {"OK": "OK", "WARN": "WARN", "FAIL": "FAIL"}
    for r in rows:
        uid = r["account"]["id"] or r["verify"]["id"] or "—"
        verified = {True: "yes", False: "**no**", None: "?"}[r["verify"]["verified"]]
        role = r["account"]["role"] or "**none**"
        exp = iso(r["claims"].get("exp"))
        lines.append(
            f"| `{r['service']}` | `{uid}` | {verified} | {role} | "
            f"{r['expected_role']} | {exp} | {mark[r['verdict']]} |"
        )
    lines.append("")

    for r in rows:
        if not (r["fails"] or r["warns"]):
            continue
        lines.append(f"**{r['service']}** (`{r['env_file']}`, token `{r['fingerprint']}`)")
        for f in r["fails"]:
            lines.append(f"- FAIL — {f}")
        for w in r["warns"]:
            lines.append(f"- WARN — {w}")
        lines.append("")

    n_fail = sum(1 for r in rows if r["verdict"] == "FAIL")
    n_warn = sum(1 for r in rows if r["verdict"] == "WARN")
    expired = sum(
        1 for r in rows
        if isinstance(r["claims"].get("exp"), (int, float)) and r["claims"]["exp"] < time.time()
    )
    lines.append(
        f"{len(rows)} identities · {len(rows) - n_fail - n_warn} OK · "
        f"{n_warn} WARN · {n_fail} FAIL · {expired} carrying a past `exp` "
        f"(expected — not counted as a fault)"
    )
    return "\n".join(lines)


def run_env(args, env: str) -> list[dict]:
    infra = find_infra_dir(args.infra_dir)
    identities = enumerate_identities(infra, env)
    if not identities:
        raise SystemExit(f"No SERVICE_TOKEN-bearing .env.{env} files under {infra}/{MANIFEST_ROOT}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        rows = list(pool.map(lambda i: probe(i, HOSTS[env], args.timeout), identities))

    by_service = {i["service"]: i for i in identities}
    for row in rows:
        row["sealed"] = sealed_secret_state(infra, by_service[row["service"]])
        if args.check_cluster:
            namespace = "aura-ai" if row["group"] == "ai" else "aura-services"
            fp, err = cluster_fingerprint(
                CLUSTER_CONTEXTS[env], namespace, f"{row['service']}-secret", args.timeout
            )
            row["cluster"] = {"fingerprint": fp, "error": err, "namespace": namespace}

    baseline = None
    if args.baseline and not args.write_baseline:
        try:
            baseline = json.loads(pathlib.Path(args.baseline).read_text(encoding="utf-8")).get(env)
        except OSError:
            baseline = None

    return [judge(r, args.expected_role, baseline) for r in rows]


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--env",
        required=True,
        choices=["dev", "prod", "both"],
        help="Environment to sweep. Required — there is no default, so prod is never implicit.",
    )
    parser.add_argument("--infra-dir", help="AuraInfra checkout (default: auto-discover)")
    parser.add_argument(
        "--expected-role",
        help="Hold every identity to this role, instead of resolving one per identity",
    )
    parser.add_argument(
        "--check-cluster",
        action="store_true",
        help="Also fingerprint the live cluster Secret via kubectl and compare it to the .env value",
    )
    parser.add_argument("--baseline", help="JSON file of expected UIDs, keyed by env then service")
    parser.add_argument("--write-baseline", action="store_true", help="Write the observed UIDs to --baseline")
    parser.add_argument("--json", action="store_true", help="Emit the raw result instead of a table")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--concurrency", type=int, default=6)
    args = parser.parse_args()

    envs = ["dev", "prod"] if args.env == "both" else [args.env]
    results = {env: run_env(args, env) for env in envs}

    if args.write_baseline:
        if not args.baseline:
            raise SystemExit("--write-baseline needs --baseline <path>")
        # A baseline records what each identity is *supposed* to look like, so
        # it is only as good as the moment it is taken. Pinning an identity
        # that is currently failing would enshrine the outage as the
        # expectation — the exact fault (a null role) this skill exists to
        # catch would become the baseline every later run is measured against.
        pinned, skipped = {}, []
        for env, rows in results.items():
            pinned[env] = {}
            for r in rows:
                uid = r["account"]["id"] or r["verify"]["id"]
                role = r["account"]["role"]
                if not uid or not role:
                    skipped.append(f"{env}/{r['service']}")
                    continue
                pinned[env][r["service"]] = {"uid": uid, "role": role}
        pathlib.Path(args.baseline).write_text(
            json.dumps(pinned, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        total = sum(len(v) for v in pinned.values())
        print(f"Wrote {total} pinned identities for {', '.join(envs)} to {args.baseline}")
        if skipped:
            print(
                f"Refused to pin {len(skipped)} identity(ies) with no resolvable uid or "
                f"role: {', '.join(skipped)}. Fix them, then re-run.",
                file=sys.stderr,
            )

    if args.json:
        print(json.dumps(results, indent=2, sort_keys=True, default=str))
    else:
        print("\n\n".join(render(env, results[env]) for env in envs))

    failures = [r for rows in results.values() for r in rows if r["verdict"] == "FAIL"]
    if failures:
        print(
            "\nFAILING: "
            + ", ".join(f"{r['service']} ({r['env_file'].split('/')[-1]})" for r in failures),
            file=sys.stderr,
        )
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # the sweep itself could not run
        print(f"verify-service-accounts could not run: {exc}", file=sys.stderr)
        sys.exit(2)
