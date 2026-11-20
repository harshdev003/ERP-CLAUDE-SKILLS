# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "google-auth-oauthlib>=1.2",
# ]
# ///
"""One-time OAuth consent for the Google Sheets MCP server (per-user auth).

Each person runs this once on their own machine and signs in as themselves. Their
agent then sees exactly the sheets they can see: permissions mirror the human and
edits are attributed to them.

Usage:
    uv run authorize.py <path to oauth_client.json>

Environment:
    OAUTH_CLIENT_PATH  OAuth *client* JSON from Google Cloud (alternative to argv[1])
    TOKEN_PATH         where to store the token
                       (default: ~/.config/claude-sheets/token.json)

Scopes requested — deliberately narrow, no full-Drive access:
    spreadsheets              read/write spreadsheets the identity can open
    drive.metadata.readonly   find sheets by name; cannot read file contents
"""
import os
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.metadata.readonly",
]

DEFAULT_TOKEN = Path.home() / ".config" / "claude-sheets" / "token.json"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    client_path = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("OAUTH_CLIENT_PATH", "")
    if not client_path:
        print("ERROR: pass the OAuth client JSON path as an argument, or set OAUTH_CLIENT_PATH.")
        print("  uv run authorize.py ~/.config/claude-sheets/oauth_client.json")
        return 2

    client_file = Path(client_path).expanduser()
    if not client_file.is_file():
        print(f"ERROR: OAuth client file not found: {client_file}")
        return 2

    # A "Web application" client cannot complete the loopback flow below; fail early
    # with the actionable message instead of a confusing redirect error.
    import json

    client_kind = json.loads(client_file.read_text(encoding="utf-8"))
    if "installed" not in client_kind:
        print("ERROR: this is not a Desktop app OAuth client.")
        print("       Recreate it as Application type 'Desktop app' and download again.")
        return 2

    token_path = Path(os.environ.get("TOKEN_PATH") or DEFAULT_TOKEN).expanduser()
    token_path.parent.mkdir(parents=True, exist_ok=True)

    print("Opening your browser for Google consent...")
    print("  -> Sign in as the account whose sheets this agent should see.")
    print(f"  -> Scopes: {', '.join(s.rsplit('/', 1)[-1] for s in SCOPES)}")
    print("  -> An 'unverified app' notice is expected for an Internal app:")
    print("     choose Advanced -> Go to <app name>.\n")

    flow = InstalledAppFlow.from_client_secrets_file(str(client_file), SCOPES)
    # access_type=offline + prompt=consent guarantee a refresh token comes back, so
    # the server keeps working without re-prompting.
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

    token_path.write_text(creds.to_json(), encoding="utf-8")
    try:  # best effort: restrict the token to this user
        os.chmod(token_path, 0o600)
    except OSError:
        pass

    print(f"\nSUCCESS. Token saved to: {token_path}")
    print(f"Granted scopes: {', '.join(SCOPES)}")
    print("\nAdd this to the MCP server's env block:")
    print(f'  "TOKEN_PATH": "{token_path}"')
    print("\nThen call the server's whoami tool to confirm which account was authorized.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
