#!/usr/bin/env python3
"""
Thin Aura AI API client for the chat-with-aurie skill.

Subcommands:
  health                              — GET <base_url>/, no auth, sanity check
  whoami                              — GET /users/me (server-authoritative). Falls back to JWT decode if the call fails.
  verify-token                        — POST /users/verifyToken (server-authoritative validity check)
  start  <conv_id> --message-file <p> — POST /ai/conversations/:id/start
  ask    <conv_id> --message-file <p> — POST /ai/dialogue/ask
  end    <conv_id> [--insight]        — POST /ai/conversations/:id/end

Reads from environment:
  AURA_USER_TOKEN  (required for whoami/start/ask/end and as the
                   user token verified by verify-token) — bearer token
  AURA_API_TOKEN   (required for verify-token) — service-level bearer
  AURA_BASE_URL    (default https://services.aurahealth.io)

Pre-flight:
- start/ask/end emit a stderr warning when AURA_USER_TOKEN's `exp` is
  in the past, but do NOT block — for some Aura users the server
  persists the JWT and accepts it past `exp`. Server is authoritative.
- whoami / verify-token both go to the server, so they bypass the
  expiry guess entirely.
- All endpoints output a single JSON line {http_status, body} on
  stdout (or, for whoami's JWT-decode fallback, a single decoded
  payload object).
- Exit code: 0 on 2xx, 2 on 4xx/5xx, 1 on local error.

The token NEVER appears in stdout/stderr or in process argv.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
import pathlib
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



# ---------- SSL context ---------------------------------------------------- #
# Python.org installs on macOS often ship without a CA bundle wired into
# urllib's default verify paths, leading to CERTIFICATE_VERIFY_FAILED on
# every HTTPS call. If certifi is available, use its bundle. Otherwise fall
# back to the system default (which works fine on most Linux distros).
try:
    import certifi  # type: ignore
    _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CONTEXT = ssl.create_default_context()


# ---------- token utilities ----------------------------------------------- #

def _b64url_decode(seg: str) -> bytes:
    seg += "=" * (-len(seg) % 4)
    return base64.urlsafe_b64decode(seg)


def jwt_payload(token: str) -> dict | None:
    """Return the JWT payload dict, or None if not a parseable JWT."""
    if token.count(".") != 2:
        return None
    try:
        return json.loads(_b64url_decode(token.split(".")[1]))
    except Exception:
        return None


def jwt_exp_seconds(token: str) -> int | None:
    pl = jwt_payload(token)
    if not pl:
        return None
    exp = pl.get("exp")
    return (exp - int(time.time())) if isinstance(exp, int) else None


def get_token() -> str:
    """Return AURA_USER_TOKEN or exit with a clear error."""
    tok = os.environ.get("AURA_USER_TOKEN", "").strip()
    if not tok:
        sys.exit("error: AURA_USER_TOKEN is not set in env")
    return tok


def get_service_token() -> str:
    """Return AURA_API_TOKEN or exit with a clear error."""
    tok = os.environ.get("AURA_API_TOKEN", "").strip()
    if not tok:
        sys.exit("error: AURA_API_TOKEN is not set in env (required for verify-token)")
    return tok


def get_base_url() -> str:
    return os.environ.get("AURA_BASE_URL", "https://services.aurahealth.io").rstrip("/")


def warn_if_token_expired(token: str) -> None:
    """
    Emit a stderr warning when the JWT's `exp` claim is in the past, but
    do NOT block the call — for some Aura users (long-lived test accounts),
    the server persists the JWT in the database and accepts it past `exp`.
    Only the server can authoritatively reject a token; we let it decide.
    """
    secs = jwt_exp_seconds(token)
    if secs is None:
        return  # opaque token, skip check
    if secs <= 0:
        print(
            f"warning: AURA_USER_TOKEN's `exp` was {-secs}s ago "
            f"({-secs/60:.1f} min). Some Aura users have server-side "
            f"persisted JWTs that still work past `exp` — proceeding. "
            f"If the call returns 401/403, refresh the token.",
            file=sys.stderr,
        )
    elif secs < 60:
        print(
            f"warning: AURA_USER_TOKEN expires in {secs}s — refresh soon",
            file=sys.stderr,
        )


# ---------- HTTP helpers --------------------------------------------------- #

def http_request(method: str, url: str,
                 body: dict | None = None,
                 token: str | None = None) -> tuple[int, dict | str]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("User-Agent", user_agent())
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60, context=_SSL_CONTEXT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw
    except urllib.error.URLError as e:
        sys.exit(f"error: connection failed — {e.reason}")


def emit(status: int, body: dict | str) -> None:
    print(json.dumps({"http_status": status, "body": body},
                     ensure_ascii=False, indent=2))
    if status >= 400:
        sys.exit(2)


def read_message(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read().rstrip("\n")


# ---------- subcommands ---------------------------------------------------- #

def cmd_health(_args: argparse.Namespace) -> None:
    """Unauthenticated GET / — confirms the server is up before we authenticate."""
    base = get_base_url()
    status, body = http_request("GET", base + "/")
    emit(status, body if isinstance(body, dict) else str(body)[:500])


def cmd_whoami(_args: argparse.Namespace) -> None:
    """
    Server-authoritative whoami via GET /users/me. Falls back to JWT
    decoding if the API call fails (network down, 4xx/5xx, etc.) so the
    user can still see *something* — clearly labelled as a fallback.
    """
    token = get_token()
    url = f"{get_base_url()}/users/me"
    status, body = http_request("GET", url, token=token)

    if status == 200 and isinstance(body, dict):
        # Pull a focused subset out for legibility; keep the rest available
        # via direct GET /users/me when needed. Avoid printing the full
        # 100+ field user record on every pre-flight.
        focus = {
            "source": "/users/me",
            "id": body.get("id"),
            "givenName": body.get("givenName"),
            "familyName": body.get("familyName"),
            "premium": body.get("premium"),
            "lifetime": body.get("lifetime"),
            "deviceOS": body.get("deviceOS"),
            "version": body.get("version"),
            "countryCode": body.get("countryCode"),
            "timezone": body.get("timezone"),
            "createdAt": body.get("createdAt"),
            "loggedInAt": body.get("loggedInAt"),
            "isCoach": body.get("isCoach"),
            "persona": body.get("persona"),
            "total_fields_in_response": len(body),
        }
        print(json.dumps(focus, indent=2, ensure_ascii=False))
        return

    # Fallback: server didn't answer — decode the JWT locally so the user
    # at least sees who they *think* they're impersonating. Make the
    # fallback obvious so it isn't mistaken for authoritative.
    print(
        f"warning: GET /users/me returned HTTP {status}; falling back to "
        f"local JWT decode (NOT server-authoritative)",
        file=sys.stderr,
    )
    pl = jwt_payload(token)
    if not pl:
        print(json.dumps(
            {"source": "fallback-jwt-decode",
             "token_kind": "opaque",
             "note": "Not a JWT — no owner to decode and the server didn't answer"},
            indent=2))
        sys.exit(2)
    secs = jwt_exp_seconds(token)
    out = {
        "source": "fallback-jwt-decode",
        "token_kind": "jwt",
        "name": pl.get("name"),
        "user_id": pl.get("user_id") or pl.get("sub"),
        "issuer": pl.get("iss"),
        "audience": pl.get("aud"),
        "sign_in_provider": (pl.get("firebase") or {}).get("sign_in_provider"),
        "expires_in_seconds": secs,
        "exp_in_past": (secs is not None and secs <= 0),
        "note": (
            "JWT decode is not authoritative — for users with server-side "
            "persisted tokens the JWT can be 'expired' but still valid. "
            "When /users/me is reachable, prefer that."
        ),
    }
    print(json.dumps(out, indent=2))


def cmd_verify_token(_args: argparse.Namespace) -> None:
    """
    Server-authoritative validity check via POST /users/verifyToken. Sends
    AURA_USER_TOKEN in the body, signed with AURA_API_TOKEN as the service
    bearer. Body shape: { token }. Response body: { verified, tokenUserId?, id? }.
    Always HTTP 200 — verdict is in the body.
    """
    user_token = get_token()
    api_token = get_service_token()
    url = f"{get_base_url()}/users/verifyToken"
    status, body = http_request(
        "POST", url, body={"token": user_token}, token=api_token,
    )
    emit(status, body)


def cmd_start(args: argparse.Namespace) -> None:
    token = get_token()
    warn_if_token_expired(token)
    msg = read_message(args.message_file)
    url = f"{get_base_url()}/ai/conversations/{args.conversation_id}/start"
    status, body = http_request("POST", url, {"firstMessage": msg}, token)
    emit(status, body)


def cmd_ask(args: argparse.Namespace) -> None:
    token = get_token()
    warn_if_token_expired(token)
    msg = read_message(args.message_file)
    url = f"{get_base_url()}/ai/dialogue/ask"
    body_in = {
        "conversationId": args.conversation_id,
        "message": msg,
        "polling": False,
        "stream": False,
        "voice": False,
        "prompterName": args.prompter,
    }
    status, body = http_request("POST", url, body_in, token)
    emit(status, body)


def cmd_end(args: argparse.Namespace) -> None:
    token = get_token()
    warn_if_token_expired(token)
    url = f"{get_base_url()}/ai/conversations/{args.conversation_id}/end"
    body_in = {"createInsightCard": True} if args.insight else {}
    status, body = http_request("POST", url, body_in, token)
    emit(status, body)


# ---------- entry point ---------------------------------------------------- #

def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(
        description="Thin Aura AI API client — start/ask/end + whoami/health"
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("health", help="Ping the base URL")
    sp.set_defaults(fn=cmd_health)

    sp = sub.add_parser(
        "whoami",
        help="Server-authoritative whoami via GET /users/me (falls back to JWT decode if the call fails)",
    )
    sp.set_defaults(fn=cmd_whoami)

    sp = sub.add_parser(
        "verify-token",
        help="Verify AURA_USER_TOKEN's validity via POST /users/verifyToken (requires AURA_API_TOKEN)",
    )
    sp.set_defaults(fn=cmd_verify_token)

    sp = sub.add_parser("start", help="Open an Aurie conversation")
    sp.add_argument("conversation_id")
    sp.add_argument("--message-file", required=True)
    sp.set_defaults(fn=cmd_start)

    sp = sub.add_parser("ask", help="Send a user turn")
    sp.add_argument("conversation_id")
    sp.add_argument("--message-file", required=True)
    sp.add_argument(
        "--prompter", default="coach",
        choices=["companion", "chat", "coach", "quickProcessing", "quickProcessingSentinel"],
        help="Aurie prompter (default: coach). Matches the server allowlist on /ai/dialogue/ask",
    )
    sp.set_defaults(fn=cmd_ask)

    sp = sub.add_parser("end", help="Close the conversation")
    sp.add_argument("conversation_id")
    sp.add_argument(
        "--insight", action="store_true",
        help="request createInsightCard=true (default off — basic close returns "
             "sparse body, no summary/followup)",
    )
    sp.set_defaults(fn=cmd_end)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
