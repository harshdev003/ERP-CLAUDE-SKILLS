---
name: setup-backend-database
description: >
  Interactive, guided onboarding that gets a teammate — data scientist, analyst, PM or
  engineer — set up to query Aura's backend Postgres (Cloud SQL) database for analysis:
  installs the tooling, helps them request access, starts the Cloud SQL Auth Proxy, connects
  DBeaver, and runs a first query. It is a guide-a-human walkthrough: explain the why, detect
  their OS and give platform-correct commands, and go one step at a time. Trigger on "set me
  up on the backend data", "help me connect to Postgres", "onboard me to the backend
  database", "how do I query the backend", "set up DBeaver", "set up the Cloud SQL proxy",
  "get me access to intelligenceDB", "I want to query our production database", or a new team
  member picking up a data-science or backend-data setup task. Do NOT use for BigQuery, GCP
  logs, or the Aura API — this covers the Postgres/Cloud SQL database only. Do NOT use to run
  analysis queries once connected.

metadata:
  version: "3.0.2"
knowledge:
  reads:
    - Guide/Index      # baseline routing aid (universal)
    - Tools/Slack      # drafting/sending the access-request DM to the admin
    - Tools/GCP        # gcloud, Cloud SQL projects, service accounts, IAM
---

# setup-backend-database

You are onboarding a human to Aura's **backend Postgres database** so they can query it for
analysis. Assume they may be a **first-timer at backend/DB setup** — patient, teacherly, one step
at a time. Explain the *why* behind each step, not just the command. Never dump the whole
walkthrough at once; guide interactively and wait for them at each checkpoint.

**This skill covers the Postgres / Cloud SQL database only** — not BigQuery, GCP logs, or the
Aura API. If they need those, point them elsewhere and stay focused here.

## Golden rules (read before you start)

- **Never handle real secrets.** You must NOT type passwords, keys, or tokens into any field, and
  you must NOT put real credentials into files, tickets, or messages. When a password is needed,
  the **user** enters it themselves. Credentials come from **Jerome (<redacted-email>)** or the
  **#engineering-postgres** Slack channel — never from you.
- **Ask permission before downloading/installing** anything on their machine, and before sending
  any message on their behalf.
- **Dev first, read-only.** Get them working against `dev` before prod. Build the `select`-only,
  always-`LIMIT` habit. Prod holds real user data — treat it with care and never exfiltrate PII.
- **Detect the OS once, up front** (Step 3) and use the matching command track throughout.

---

## Step 0 — Detect the operating system

Everything tool-related branches on OS. Determine it before giving any command:

- You usually already know the host OS from your environment/platform info. If unsure, run `uname`
  (macOS → `Darwin`; Git Bash on Windows → `MINGW*`/`MSYS*`), or check `$OS` in PowerShell.
- Confirm with the user in one line ("Looks like you're on **Windows** — I'll give you PowerShell
  commands") so they can correct you.

Throughout this skill, follow the **Windows** or **macOS** block as appropriate.

## Step 1 — Orient them: operational vs. analytical data

Most analysts already live in **analytical** data (Mixpanel, BigQuery) — reshaped *copies* built for
asking questions. The backend **Postgres** database is the **operational** store: the live source of
truth the app reads and writes every second. A user subscribes → their Postgres row changes
instantly.

> Analogy: BigQuery/Mixpanel are the **research library** (archives organized for browsing);
> Postgres is the **front desk** where transactions happen in real time. Much of BigQuery
> *originated* in Postgres and was copied over for analysis.

Why an analyst wants it: data not yet copied to the warehouse, the exact live state of something, or
tables that only exist in the app's own schema.

## Step 2 — Give them the mental model: the security gates

A production database is **never exposed to the open internet** — it sits in a private Google Cloud
network with no public address. Reaching it means passing gates, and **every tool maps to one gate**:

| Gate | Question | The tool |
|---|---|---|
| Identity | "Who are you?" | `gcloud auth login` |
| Delegated identity | "Can programs act as you?" | service-account key file (or `gcloud auth application-default login`) |
| Authorization | "Are you *allowed* to reach this DB?" | IAM role `Cloud SQL Client` (granted by the admin) |
| Network path | "How do I reach a DB with no public address?" | the **Cloud SQL Auth Proxy** (a secure tunnel) |
| Cockpit | "Where do I type SQL?" | **DBeaver** |
| Database key | "Are you a valid DB user?" | the `postgres` password |

There are **two independent "are you allowed?" layers** — the *cloud* layer (identity + IAM/key) and
the *database* layer (postgres user + password). That's why they need **both** cloud access **and**
the password.

The connection chain (draw this for them):

```
  ┌──────────┐   SQL over    ┌────────────────────┐  encrypted   ┌──────────────┐
  │ DBeaver  │ ─ localhost ─▶│ Cloud SQL Auth     │ ── tunnel ──▶│  Postgres    │
  │(type SQL)│    :PORT      │ Proxy (on laptop)  │  through GCP │  private net │
  └──────────┘ ◀── results ──└────────────────────┘ ◀────────────└──────────────┘
                                ▲ checks your Google              ▲ checks the
                                  identity + IAM/key                postgres password
```

The clever bit: the proxy opens a local "door" at `localhost:PORT`; DBeaver connects to *that*, and
the proxy handles all auth + encryption into the real cloud DB. That's why DBeaver's host is
`localhost` even though the database lives in Google's data center.

## Step 3 — Install the three tools (ask permission first)

They need **three** things: the Google Cloud SDK (`gcloud`, which also bundles `bq`), the **Cloud SQL
Auth Proxy** (v2), and **DBeaver Community**. Tell them what you're about to download and get a clear
yes before installing.

First check what's already present (`gcloud --version`, `dbeaver` presence, any existing proxy) and
skip anything installed.

### Windows

```powershell
winget install --id Google.CloudSDK -e --accept-source-agreements --accept-package-agreements
winget install --id DBeaver.DBeaver.Community -e --accept-source-agreements --accept-package-agreements
```

Cloud SQL Auth Proxy v2 (binaries live on Google's storage bucket, not GitHub release assets — find
the latest tag at https://github.com/GoogleCloudPlatform/cloud-sql-proxy/releases, then, in
PowerShell):

```powershell
New-Item -ItemType Directory -Force "$HOME\aura-db" | Out-Null
Invoke-WebRequest -Uri "https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/<VERSION>/cloud-sql-proxy.x64.exe" -OutFile "$HOME\aura-db\cloud-sql-proxy.exe"
& "$HOME\aura-db\cloud-sql-proxy.exe" --version
```

**PowerShell execution-policy gotcha:** the first `gcloud` call in PowerShell may fail with
`cannot be loaded because running scripts is disabled on this system` — it's blocking gcloud's
`.ps1` wrapper, nothing to do with the install. Two fixes:
- Quick, no settings change: call the `.cmd` wrapper — `gcloud.cmd auth login`.
- Permanent (the **user** runs this themselves — it changes a security setting, so you don't):
  `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`.

### macOS

```bash
brew install --cask google-cloud-sdk
brew install --cask dbeaver-community

# Cloud SQL Auth Proxy v2 — pick arm64 (Apple Silicon) or amd64 (Intel):
mkdir -p "$HOME/aura-db"
ARCH=$([ "$(uname -m)" = "arm64" ] && echo arm64 || echo amd64)
curl -sfL -o "$HOME/aura-db/cloud-sql-proxy" \
  "https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/<VERSION>/cloud-sql-proxy.darwin.$ARCH"
chmod +x "$HOME/aura-db/cloud-sql-proxy"
"$HOME/aura-db/cloud-sql-proxy" --version
```

(Find `<VERSION>`, e.g. `v2.23.0`, from the releases page above.)

## Step 4 — Authenticate

Have the user sign in with **the same Google account they use for the BigQuery web console** (that
account already has a working Aura identity). In a fresh terminal:

```
# user-gcloud: the proxy connects as the person, by design
gcloud auth login
```

(Windows: use `gcloud.cmd auth login` if the execution-policy error appears.) Each opens their
browser. This proves *who they are*; the service-account key (Step 6) is what actually authorizes the
DB connection.

## Step 5 — Request access from the admin

This is the real long pole — only an admin can grant it. The user needs, per environment they want:

1. **Access to the database**, either the **`Cloud SQL Client`** IAM role on the project for their
   Google account, **or** the pre-authorized **service-account key file** (the team's usual path).
2. The **`postgres` password** for that instance.

Source: **Jerome (<redacted-email>)** or **#engineering-postgres**.

**Draft this DM for them** (fill in dev/prod as they want). Offer to send it only with an explicit
go-ahead, and when sending, **compose the `shared:post-to-slack` skill** — do not call the Slack MCP
directly — with `--as-user` (so it comes from them) and `--footer eng:setup-backend-database` for
attribution. Never send credentials, only the *request*.

> Hey — getting set up on the backend database (Postgres / Cloud SQL). I've installed gcloud, the
> Cloud SQL Auth Proxy, and DBeaver, and authenticated with my `<me>@aurahealth.io` Google account.
>
> To connect I need, for **dev** (and later **prod**):
> 1. Either the **`Cloud SQL Client`** IAM role for `<me>@aurahealth.io`, or the service-account key
>    file, on:
>    - **`aura-dev`** (the `aura-db-dev` instance), and/or
>    - **`firebase-auratech16`** (the `aura-db-prod` instance).
> 2. The **`postgres` password(s)** for those instance(s).
>
> I'll be doing read-only exploration to get familiar — no writes, and I'll be careful with prod.
> Thanks! 🙏

## Step 6 — Save the service-account key file(s)

When the admin sends the key file(s) (e.g. `aura-dev-sql-proxy-user.json`,
`firebase-auratech16-sql-proxy-user.json`), the **user** downloads them into a protected,
git-ignored folder — suggest `~/.secrets/` (or `~/aura-db/.secrets/`). Remind them: these are live
credentials; never commit them to git, paste them into tickets, or reshare.

## Step 7 — Start the Cloud SQL Auth Proxy (dev first)

The Aura port convention: **dev → `5431`**, **prod → `5432`** (different ports so both proxies can
run at once). Start dev:

### Windows

```powershell
& "$HOME\aura-db\cloud-sql-proxy.exe" --credentials-file "$HOME\.secrets\aura-dev-sql-proxy-user.json" --port 5431 aura-dev:us-central1:aura-db-dev
```

### macOS

```bash
"$HOME/aura-db/cloud-sql-proxy" --credentials-file "$HOME/.secrets/aura-dev-sql-proxy-user.json" --port 5431 aura-dev:us-central1:aura-db-dev
```

**Set the expectation that trips up first-timers:** after a couple log lines it *sits there, cursor
blinking* — that is **success, not a hang**. Success looks like `Listening on 127.0.0.1:5431` and
`The proxy has started successfully and is ready for new connections!`. Tell them to **leave that
window open** (closing it or Ctrl+C shuts the tunnel) and do their work in a *separate* window /
DBeaver.

If they see `PERMISSION_DENIED` or `serviceusage.services.use`, the key/account lacks access — back
to Step 5 with the admin.

> The team also wraps this in a `make ENVIRONMENT=dev start-sql-proxy` target in the **AuraInfra**
> repo (using the older `cloud_sql_proxy` v1); the v2 command above is equivalent and standalone.

## Step 8 — Connect DBeaver

New connection → **PostgreSQL**, then:

| Field | Dev value |
|---|---|
| Host | `localhost` |
| Port | `5431` ← **must match the proxy's `--port`** |
| Database | `intelligenceDB` (the AI/Aurie backend DB; other service DBs exist too) |
| Username | `postgres` |
| Password | the dev password from the admin — **the user types this**, not you |

- Tick **Save password**. Leave other fields default.
- First PostgreSQL connection → DBeaver prompts to **Download driver files** (JDBC driver). Click
  **Download** — normal, one-time.
- Click **Test Connection ▸** → expect green **Connected** with a server version → **Finish**.

**The #1 gotcha:** DBeaver pre-fills port **5432** (the Postgres default). For **dev** that gives
`Connection refused: getsockopt … localhost:5432`. Fix: set **Port → 5431** (match the running
proxy). Rule of thumb: **DBeaver port must equal the proxy `--port`** (dev 5431 / prod 5432).
Other common snag: an authentication error usually means a stray space when pasting the password.

## Step 9 — First query, then explore (read-only)

Open a SQL editor (**SQL Editor → New SQL Script**) and run (Ctrl+Enter / Cmd+Enter):

```sql
select now();
```

A timestamp = the whole chain works end to end. 🎉 Then teach exploring an unfamiliar DB:

```sql
-- what tables exist (the database describing itself)
select table_name
from information_schema.tables
where table_schema = 'public'
order by table_name;

-- peek at a table — ALWAYS add limit while exploring
select * from <table_name> limit 10;
```

Reinforce the habit: **`select`-only, always `limit`** while exploring. On dev it's low-stakes; the
reflex is what keeps them safe on prod.

## Step 10 — Prod later, hardened

Only when they have a prod-specific need (the dev schema is the same, so all *learning* happens
safely on dev). When they do:

1. Start a **separate** proxy for prod on port **5432** with the prod key file and instance
   `firebase-auratech16:us-central1:aura-db-prod`.
2. In DBeaver, set the connection **Port → 5432** and set the **Connection type → "Production"**
   (in the connection settings). This paints the connection **red** and makes DBeaver **confirm
   before running any statement** — the single best guardrail against firing a query at prod while
   thinking you're on dev.
3. Same read-only discipline, dialed up: never write, never export user PII.

## Endgame — a Postgres MCP

Once the connection works, mention that a **Postgres MCP** can let the agent query the database
directly from chat and explain results — turning this into true AI-assisted analysis instead of the
user hand-copying result grids. Offer to help set it up as a follow-up.

## Quick reference

| Env | Instance connection name | Proxy port | Key file |
|---|---|---|---|
| Dev | `aura-dev:us-central1:aura-db-dev` | `5431` | `aura-dev-sql-proxy-user.json` |
| Prod | `firebase-auratech16:us-central1:aura-db-prod` | `5432` | `firebase-auratech16-sql-proxy-user.json` |

- Username: `postgres` · AI/Aurie DB: `intelligenceDB` · one shared instance per env, one DB per service.
- Canonical Notion refs: **Onboarding - Data Science Team**, and the Backend **Database** / **Setup**
  / **Postgres** pages under Technology.
- Secrets → **Jerome (<redacted-email>)** / **#engineering-postgres**. Never from this skill.
