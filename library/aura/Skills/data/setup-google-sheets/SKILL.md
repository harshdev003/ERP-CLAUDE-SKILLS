---
name: setup-google-sheets
description: >
  Guided setup that gives Claude live read/write access to Google Sheets through a local MCP
  server backed by the Sheets API. Walks a human through picking an auth model (per-user
  OAuth, share-mode service account, or domain-wide delegation), creating a Google Cloud
  project, enabling the Sheets and Drive APIs, creating a consent screen and OAuth client,
  registering the MCP server, and verifying with a real read. Trigger on "set up google
  sheets", "set up the sheets MCP", "give Claude access to my spreadsheets", "connect Google
  Sheets", "wire up the Sheets API", "authorize google sheets", "set up sheets for a
  teammate", "re-authorize sheets", "my sheets token expired", "add someone to the sheets
  setup", "I want you to read/edit my sheet", "why can't you see my spreadsheet", "service
  account or OAuth for sheets?", or a Sheets call failing with an auth, 403 or
  missing-credentials error. Do NOT use to read an already-connected sheet, for BigQuery
  (`eng:query-bigquery`), or general setup (`shared:setup-ainative`).

metadata:
  version: "3.0.1"
knowledge:
  reads:
    - Guide/Index
    - Tools/Google Sheets
---

# Set up Google Sheets access

## Goal

Claude can call Google Sheets API v4 directly — discovering spreadsheets, reading
ranges, appending rows, writing formulas, and applying formatting — through a local
MCP server authenticated as a real identity.

## What this owns

Setup and auth only: choosing an auth model, provisioning credentials, registering the
MCP server, verifying it works, and diagnosing auth failures. Once access is live, the
sheet work itself is a plain MCP tool call, not this skill.

## Role and pacing

This is a **guide-a-human walkthrough**. Console steps require a browser and a signed-in
Google account, so Claude cannot perform them. Go **one step at a time**, explain the
"why" for each, and wait for the user to confirm before moving on. Do not dump all six
steps at once and do not assume a step succeeded.

Before any step that writes to disk (credentials, `.mcp.json`), state exactly which file
you will create or modify and get confirmation.

## Step 0 — Choose the auth model

Ask which model fits before touching a console. The three differ in *who Claude acts as*,
which determines what it can see and who must approve it.

| Model | Claude acts as | Sees | Needs a Workspace super-admin? |
|---|---|---|---|
| **Per-user OAuth** (default) | the human who consented | every sheet that person can open | No |
| **Share-mode** (service account) | a bot identity | only sheets explicitly shared with it | No |
| **Domain-wide delegation** | any user it impersonates | everything, org-wide | **Yes** |

**Recommend per-user OAuth** unless the user has a specific reason otherwise: no admin
dependency, permissions mirror the human automatically, files Claude creates are owned by
the user, and edits are attributable to a real person.

Steer toward **share-mode** only when the requirement is an isolated bot identity with a
deliberately tiny, explicitly-shared footprint. Steer toward **DWD** only for team-wide
rollout where per-person setup is too costly — and surface its risks first.

Read [`references/auth-models.md`](references/auth-models.md) for the full comparison,
DWD's risk profile, and the dedicated-bot-account variant. The rest of this skill
implements per-user OAuth; the same server supports all three via environment variables.

## Step 1 — Check prerequisites

The server runs on Python via `uv`, which resolves its dependencies from inline PEP 723
metadata into a cached isolated environment.

```bash
uv --version || echo "uv missing"
```

If `uv` is missing, install it (no admin rights needed). macOS / Linux:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Windows (PowerShell):

```powershell
irm https://astral.sh/uv/install.ps1 | iex
```

`uv` lands in `~/.local/bin` (macOS/Linux) or `%USERPROFILE%\.local\bin` (Windows), which
is often absent from `PATH`. Reference it by full path in commands and by absolute path in
`.mcp.json`. Resolve the real path once with `command -v uv || echo ~/.local/bin/uv` and
reuse it — Step 7 needs it.

## Step 2 — Create a dedicated Google Cloud project

Send the user to [console.cloud.google.com/projectcreate](https://console.cloud.google.com/projectcreate).

- Name it something obvious, e.g. `claude-sheets`.
- Set **Location/Organization to the company org** (not "No organization") — the *Internal*
  consent option only exists for projects inside a Workspace org.

**Use a dedicated project, never an existing production project.** Step 3 sets the consent
screen to Internal, and a production Firebase or app project's consent screen governs that
app's real "Sign in with Google" flow — switching it to Internal breaks sign-in for external
end users. A separate project makes that impossible.

## Step 3 — Enable the Sheets and Drive APIs

API enablement is **per-project**, so enable both inside the project created in Step 2:

- `https://console.cloud.google.com/apis/library/sheets.googleapis.com?project=<PROJECT_ID>`
- `https://console.cloud.google.com/apis/library/drive.googleapis.com?project=<PROJECT_ID>`

The API Library landing page shows only featured APIs; neither Sheets nor Drive appears
there, so link directly or have the user search by name.

Enabling these in a *different* project than the OAuth client produces a `403
accessNotConfigured` at call time, naming the project number that is missing the API — see
Troubleshooting.

## Step 4 — Configure the consent screen and OAuth client

In the same project, under **APIs & Services → OAuth consent screen** (newer consoles label
this **Google Auth Platform**):

1. **Audience: Internal.** This restricts the app to the Workspace org and skips Google's
   app-verification review.
2. **Branding:** app name (e.g. `Claude Sheets`) and a support email. Leave the **app logo
   empty** — uploading one triggers the verification requirement.
3. **Scopes:** skip. The server requests them at consent time.
4. **Credentials → Create credentials → OAuth client ID → Application type: Desktop app.**
   Download the JSON.

**Desktop app is required.** It is the client type that permits the loopback
(`http://localhost`) redirect the consent flow uses; a "Web application" client fails.

If **Internal** is not offered, the project landed outside the Workspace org. Fix the
project's organization rather than falling back to External — External requires either
app verification for these scopes or clicking through an "unverified app" warning, and adds
test-user management.

## Step 5 — Place credentials outside the code directory

Both credential files belong in a config directory, never beside the scripts, so they
cannot be committed:

```bash
mkdir -p ~/.config/claude-sheets
mv ~/Downloads/client_secret_*.json ~/.config/claude-sheets/oauth_client.json
```

Verify the client type before consenting — a `web` client wastes a round trip:

```bash
python3 -c "import json;d=json.load(open('$HOME/.config/claude-sheets/oauth_client.json'));print('installed (Desktop) OK' if 'installed' in d else 'WRONG TYPE: recreate as Desktop app')"
```

## Step 6 — Run the one-time consent

```bash
uv run ${CLAUDE_PLUGIN_ROOT}/skills/setup-google-sheets/scripts/authorize.py ~/.config/claude-sheets/oauth_client.json
```

A browser opens. The user signs in **as the identity Claude should act as**, then approves.
On an Internal unverified app, an "unverified app" notice is expected — **Advanced → Go to
&lt;app name&gt;** proceeds.

The script requests exactly two scopes and writes the token to
`~/.config/claude-sheets/token.json`:

| Scope | Grants |
|---|---|
| `spreadsheets` | read/write any spreadsheet the identity can open |
| `drive.metadata.readonly` | find sheets by name; **cannot read file contents** |

`drive.metadata.readonly` is deliberately narrower than full `drive`: it enables discovery
without granting access to non-spreadsheet documents, and avoids the *restricted* scope
class. Widen it only on request — see [`references/auth-models.md`](references/auth-models.md).

Run this from a terminal the user can see; the consent URL is also printed if no browser opens.

## Step 7 — Register the MCP server

State the file you are about to write, then create it. Project scope (`<repo>/.mcp.json`)
is a small dedicated file; user scope requires editing a large shared config, so prefer
project scope unless the user wants Sheets in every directory.

macOS / Linux:

```json
{
  "mcpServers": {
    "google-sheets": {
      "command": "/Users/<user>/.local/bin/uv",
      "args": ["run", "--directory", "<abs path to scripts/>", "server.py"],
      "env": { "TOKEN_PATH": "/Users/<user>/.config/claude-sheets/token.json" }
    }
  }
}
```

Windows — same shape, with backslashes escaped and the `.exe` suffix:

```json
{
  "command": "C:\\Users\\<user>\\.local\\bin\\uv.exe",
  "env": { "TOKEN_PATH": "C:\\Users\\<user>\\.config\\claude-sheets\\token.json" }
}
```

Use absolute paths for both `command` and `--directory`; MCP servers do not inherit a
useful working directory or `PATH`, and `~` is not expanded in either field. `TOKEN_PATH`
is the only variable per-user OAuth needs — the token file embeds the client id and secret
alongside the refresh token.

Confirm the configured command speaks MCP before relying on it:

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"probe","version":"1"}}}' | uv run --directory <scripts dir> server.py
```

A `result` containing `serverInfo` means the launch command works. Tools appear in the
session once the MCP server is picked up; if they do not, restart Claude Code.

## Step 8 — Verify with real calls

Run these in order and show the user the output. Each isolates a different failure:

1. **`whoami`** — confirms the auth model and, crucially, **which identity** Claude acts as.
   Verify the email is the intended one.
2. **`search_spreadsheets`** — confirms the Drive scope and API. Returns real sheet names.
3. **`get_spreadsheet_info`** on one of those IDs — confirms the Sheets API and returns each
   tab's name and numeric gid.
4. **`read_rows`** on a small range — confirms end-to-end data flow.

A `403` on one specific sheet while others work is **not** a broken setup: that sheet is not
shared with the authenticated identity. Confirm by opening it in the browser as that user.

## Multi-user rollout

Each additional person runs Step 6 on their own machine and points `TOKEN_PATH` at their own
token. Nothing else changes — same client JSON, same server, same registration. Their Claude
inherits exactly their own sheet access, and edits are attributed to them.

**Never distribute one account's credentials to several humans.** It flattens permissions
(everyone inherits the union of that account's access) and destroys attribution. When
per-person setup becomes too costly, switch to DWD via `IMPERSONATE_SUBJECT` rather than
sharing a token — no code change required.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `403 accessNotConfigured`, names a project number | Sheets/Drive API enabled in a different project than the OAuth client | Enable both in the OAuth client's project (Step 3) |
| `No credentials configured` | `TOKEN_PATH` unset or the file is missing | Check the `env` block in `.mcp.json`; re-run Step 6 |
| Token `invalid and cannot be refreshed` | Refresh token revoked, expired, or scopes changed | Re-run Step 6 |
| `403` on one sheet, others fine | That sheet is not shared with the authenticated identity | Share it, or authorise an identity that can already open it |
| Consent fails / redirect error | OAuth client is a "Web application" | Recreate as **Desktop app** (Step 4) |
| **Internal** unavailable on consent screen | Project sits outside the Workspace org | Move the project into the org (Step 2) |
| `ModuleNotFoundError: mcp.server.fastmcp` | MCP SDK 2.0 exposes `MCPServer`, not `FastMCP` | Keep the pinned dependency in `scripts/server.py`; import `MCPServer` |
| Tools absent after registering | MCP config not picked up | Restart Claude Code; re-run the `initialize` probe |
| Values read fine but colours/formats are missing | The values API carries no formatting | Request grid data with `effectiveFormat` — see [`references/sheets-api-patterns.md`](references/sheets-api-patterns.md) |

## Output

Report: the auth model chosen, the identity from `whoami`, the project ID, the scopes
granted, both credential paths, the MCP registration path, and the verification results.
Flag any credential that was exposed in plaintext during setup as needing rotation.

## Edge cases

- **Credential pasted into chat.** Treat it as compromised: finish setup, then have the user
  delete that key/client and issue a fresh one.
- **Existing service-account key available.** Usable for share-mode or DWD via
  `SERVICE_ACCOUNT_PATH`, but per-user OAuth stays the default recommendation.
- **Re-running this skill.** Safe. Step 6 overwrites the token in place; Step 7 rewrites the
  same registration; enabling an already-enabled API is a no-op. Nothing duplicates.
- **No Workspace org (personal Gmail).** Internal is unavailable; use External in Testing
  mode with the user added as a test user, and expect the unverified-app warning.

## References

- [`references/auth-models.md`](references/auth-models.md) — three auth models compared,
  scope menu, DWD risks, dedicated-bot-account variant.
- [`references/sheets-api-patterns.md`](references/sheets-api-patterns.md) — working the API
  once access is live: discovery discipline, `GridRange` indexing, field masks, one-call
  create-and-format, formulas, reading cell colours.
- `scripts/server.py` — the MCP server (all three auth models).
- `scripts/authorize.py` — one-time per-user consent.
