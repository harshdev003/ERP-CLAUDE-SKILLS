# GCP auth setup for `investigate-gcp-logs`

One-time per user. The skill resolves credentials in this order:

1. `GCP_KEY` (inline JSON) — **default for this skill**
2. `GOOGLE_APPLICATION_CREDENTIALS` (file path) — escape hatch
3. ADC (`gcloud auth application-default login`) — also works

This guide covers the recommended path: a service account with `roles/logging.viewer`, key inlined into `~/.claude/settings.json`.

## Service account spec

- **Name:** `claude` (or any descriptive name) — the canonical SA in use is `<redacted-email>`
- **Role on `firebase-auratech16`:** `roles/logging.viewer` (probe-confirmed sufficient for all `k8s_container` queries on 2026-05-06)
- **Escalation:** add `roles/logging.privateLogViewer` only if a specific query returns permission-denied on Data Access logs (Aura doesn't appear to emit them, so likely never needed)

## One-time setup

### Path A — `gcloud` CLI (one block)

```bash
# user-gcloud: one-time setup that creates the service account behind GCP_KEY
PROJECT=firebase-auratech16
SA=claude  # or whatever name you prefer
SA_EMAIL="${SA}@${PROJECT}.iam.gserviceaccount.com"

gcloud config set project "$PROJECT"

# Create the SA (skip if it already exists)
gcloud iam service-accounts create "$SA" \
  --display-name="claude-code-log-investigator" \
  --description="Read-only log access for the AINative investigate-gcp-logs skill"

gcloud projects add-iam-policy-binding "$PROJECT" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/logging.viewer"

# Create a JSON key
gcloud iam service-accounts keys create ~/sa-key.json \
  --iam-account="$SA_EMAIL"
```

### Path B — GCP Console (clicky)

1. <https://console.cloud.google.com/iam-admin/serviceaccounts?project=firebase-auratech16>
2. Create service account → name `claude` → **Create and continue**
3. Grant role: search `Logs Viewer` → select **Logging > Logs Viewer** → **Continue → Done**
4. Click into the SA → **Keys** → **Add key** → **Create new key** → **JSON** → **Create**
5. The browser downloads a JSON key file.

## Convert key to inline JSON for `settings.json`

`~/.claude/settings.json` env values must be single-line strings. Two equivalents:

```bash
# Most editors auto-escape on paste — this is the simplest output:
jq -c . ~/sa-key.json | pbcopy

# Or, fully-escaped JSON-string-literal (already double-quoted, ready to paste between "..." in settings):
jq -Rs . ~/sa-key.json | pbcopy
```

No `jq`? Python stdlib:

```bash
python3 -c 'import json,sys; print(json.dumps(json.load(open("/Users/'"$USER"'/sa-key.json"))))' | pbcopy
```

## Wire into `~/.claude/settings.json`

```json
{
  "env": {
    "GCP_KEY": "<paste single-line JSON here>"
  }
}
```

The skill derives the project ID from `GCP_KEY["project_id"]`. No separate `GCP_PROJECT_ID` needed. Override at call time with `--project <id>` for one-off cross-project queries.

## Clean up the temp file

```bash
rm ~/sa-key.json
```

The inline value in `settings.json` is now your one source of truth.

## Restart Claude Code

`settings.json` env is loaded at startup. Quit and relaunch. Verify with the in-process script:

```bash
python3 -c "
import json, os
k = json.loads(os.environ['GCP_KEY'])
print('SA:', k['client_email'])
print('Project:', k['project_id'])
"
```

## Install Python deps

The skill needs `google-cloud-logging`:

```bash
python3 -m pip install --user -r ${CLAUDE_PLUGIN_ROOT}/skills/investigate-gcp-logs/scripts/requirements.txt
```

**Use `python3 -m pip`, not bare `pip`.** On macOS the two often point to different Python interpreters; using `python3 -m pip` guarantees the install lands in the same Python the skill will run from.

## Smoke test (without invoking the skill)

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/investigate-gcp-logs/scripts/investigate.py --severity ERROR --since 1h
```

You should see a triage digest. If you get an AUTH ERROR with a clear remediation, follow it. If the digest is empty, broaden to `--severity WARNING --since 24h`.

## Cowork caveat

`mcp__workspace__bash` strips host env (PROD-1305), so `GCP_KEY` doesn't reach the sandbox automatically. At the start of any Cowork session that uses this skill:

```bash
# inside mcp__workspace__bash, at session start
export GCP_KEY='<paste from host ~/.claude/settings.json — single quotes preserve the JSON>'
```

The export persists for the lifetime of the workspace bash process. Do **not** write the value to any file under `outputs/` — that surface is user-visible.

## Rotation

- Keys don't expire automatically. Rotate every ~90 days: GCP Console → SA → Keys → create new, paste, delete old.
- If the key leaks (laptop loss, accidental commit, screenshot): GCP Console → SA → Disable, then create a new SA and key.
- Deleted keys are revoked instantly across all GCP services.

## Why service account vs your personal gcloud creds?

`gcloud auth application-default login` is also supported and more secure on a personal laptop (no key on disk, audited as you, auto-refreshes). The skill picks it up via `google.auth.default()` when neither env var is set.

We default to SA-key-via-`GCP_KEY` because it works in Cowork too (the gcloud browser flow doesn't), and the security delta is small as long as the key never leaves `~/.claude/settings.json`. Pick whichever matches your environment.
