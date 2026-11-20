---
name: reload-aura-skills
description: >
  Refresh, install, or reinstall AuraHealth AINative plugins and skills from the
  GitHub marketplace in Codex or Claude Code. Use when the user says "reload Aura
  skills", "refresh Aura skills", "update AINative skills", "pull latest Aura plugins",
  "install Aura skills from GitHub", "upgrade aura-tools", "reinstall shared@aura-tools",
  "reload all Aura plugins in Codex", asks why Codex is not seeing the latest Aura
  skill changes, or asks how to reload Aura skills in Claude Code. ALSO use to
  verify an install without reinstalling — "am I running the latest skills", "did
  my skills actually update", "check my installed plugins", "is my plugin cache
  stale", "why is a skill running an old version", "verify aura-tools is current",
  or when a skill's behaviour doesn't match the repo and a stale install is
  suspected. Do NOT use to create or edit skills in the repo; use create-aura-skill
  or update-aura-skill for authoring changes.
metadata:
  version: "3.1.0"
knowledge:
  reads:
    - Guide/Index
    - Tools/GitHub
---

# Reload Aura Skills

## Goal

Refresh the AuraHealth/AINative marketplace and install or reinstall the requested
Aura plugins in the current host, branching between Codex and Claude Code semantics.

## Inputs

- **Plugin set**: one or more of `shared`, `eng`, `product`, `data`, `marketing`, `qa`.
- **Host runtime**: detect first. Supported values are `codex`, `claude-code`, and `unknown`.
- **Source**: default to the GitHub marketplace `AuraHealth/AINative`. Use a local checkout only
  when the user explicitly asks for a local reload.

If the user says "all Aura skills" or "the Aura skills" without naming plugins, install all six
plugins. If the user names a team, always include `shared` plus that team plugin.
For Codex requests that say "reload Aura skills" without narrowing the plugin set, treat that as
all six plugins.

## Workflow

**A reload is not done until it has been verified.** An install that silently changed nothing
exits 0 and looks identical to one that worked. Never report that skills are current on the
strength of an install command exiting 0 — only on a [`check`](#verifying-the-install-claude-code)
that says so.

1. Explain that a CLI reload does not affect the current thread — the user must open a new thread or
   restart the host tool once it finishes (Desktop differs; see [Hosts](#hosts)).
2. Detect the runtime with this best-effort shell check:

   ```bash
   runtime=unknown

   # Claude Code wins when both families of env vars are present. Check the
   # session-wide Claude Code markers (CLAUDECODE / CLAUDE_CODE_*), NOT just
   # CLAUDE_PLUGIN_ROOT — the latter is only exported while Claude Code runs a
   # plugin's own hooks/commands/skill scripts, so it reads EMPTY inside a normal
   # Bash-tool subshell and would misfire this check to `unknown` on Claude Code.
   if [ -n "${CLAUDE_PLUGIN_ROOT:-}" ] || [ -n "${CLAUDECODE:-}" ] || [ -n "${CLAUDE_CODE_ENTRYPOINT:-}" ]; then
     runtime=claude-code
   fi

   if [ "$runtime" = unknown ] && { [ -n "${CODEX_HOME:-}" ] || [ -n "${CODEX_SHELL:-}" ] || [ -n "${CODEX_THREAD_ID:-}" ]; }; then
     runtime=codex
   fi

   if [ "$runtime" = unknown ] && command -v codex >/dev/null 2>&1; then
     runtime=codex
   fi

   echo "$runtime"
   ```

   Runtime precedence:

   ```text
   CLAUDECODE / CLAUDE_CODE_* / CLAUDE_PLUGIN_ROOT set -> claude-code
   otherwise any Codex env signal present             -> codex
   otherwise Codex CLI available                      -> codex
   otherwise                                          -> unknown
   ```

   This order is intentional. A Claude Code shell can still have `CODEX_HOME`
   exported from the user's environment, so the Claude Code signals must win.
   **Use `CLAUDECODE` (or `CLAUDE_CODE_ENTRYPOINT`) as the primary Claude Code
   signal** — those are present in every Claude Code shell. `CLAUDE_PLUGIN_ROOT`
   is only set in plugin-script execution context (so `${CLAUDE_PLUGIN_ROOT}/...`
   paths resolve when a skill's bundled script runs); it is absent from an
   ordinary Bash-tool call, so it can only *confirm* Claude Code, never rule it out.
   Treat `CODEX_HOME` as the most stable Codex signal only after the Claude Code check.
   `CODEX_SHELL` and `CODEX_THREAD_ID` are useful Codex hints but may be absent.
   If no runtime env marker is present but `codex` is on PATH, use the Codex reload branch.

3. If the runtime is `codex`, follow [Codex Reload](#codex-reload).
4. If the runtime is `claude-code`, follow [Claude Code Reload](#claude-code-reload).
5. If the runtime is `unknown`, ask the user which host they want to reload, then follow the
   matching section.

## Hosts

`check` surveys every host it finds on the machine and reports one block each. The verdict is the
**worst across hosts** — a stale Codex install is still stale when Claude Code is current.

A host only counts as present when it actually carries the marketplace. Claude Code is the
exception: it is always surveyed when its plugin tree exists, because `none-installed` there is
actionable. A Codex install that has never held aura-tools is skipped rather than reported
`none-installed` — it has nothing to be stale about, and failing the run over it would punish
having Codex on the machine at all.

The exit codes are **kinds, not a severity scale**, so a mixed machine ranks them by
actionability: any `stale` / `clone-behind` host yields `1` even when another host is
unverifiable, because `1` carries a concrete instruction and `2` only says "stop and say so".
Every host's own verdict is printed either way, so nothing is hidden.

| host | tree | verified how |
|---|---|---|
| `claude-code` | `<config>/plugins/cache/<marketplace>/<plugin>/<version>` | installed version (from `installed_plugins.json`) vs the version the clone's `.claude-plugin` manifest declares |
| `codex` | `$CODEX_HOME/plugins/cache/<marketplace>/<plugin>/<version>`, clone at `$CODEX_HOME/.tmp/marketplaces/<marketplace>` | installed version dir vs the version the clone's `.codex-plugin` manifest declares |

Both hosts use the **same rule**: compare declared version against installed version. Neither
compares commit shas — see [`references/caches.md`](references/caches.md).

`--host <name>` limits the run; the default surveys all detected hosts.

**Claude Desktop / local-agent mode is not a surveyed host.** It runs from a per-session extract
that `check` cannot see, refreshed independently of the CLI cache — on restart *and* mid-session on
a reload. So a `stale` CLI verdict says nothing about the Desktop session the user is typing into,
and vice versa. Establish which host they are on, verify that one, and say which you checked:
[`references/caches.md`](references/caches.md).

**Cursor is out of scope.** `~/.cursor/plugins/cache` exists but carries no aura-tools install, so
any implementation would be untested against a layout inferred from unrelated plugins.

**`wipe` and `prune` are Claude Code-only.** Their containment guards are written against that
cache root, and Codex's install lifecycle is unverified here — both refuse another `--host`.

## Verifying the install (Claude Code)

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/reload-aura-skills/scripts/check_install.py check
```

It compares two things, and both must hold before anything is called current: each plugin's
**installed version** against the version the clone's manifest declares, **and** the clone
against its own remote. A clone that was never refreshed matches a stale install perfectly, so
checking only the first would report "current" forever.

Branch on the **verdict / exit code**, not the printed text:

| verdict | code | what to do |
|---|---|---|
| `current` | `0` | Installed matches the clone and the clone matches its remote. Nothing to do — say so and stop. |
| `stale` | `1` | Installed predates the clone. Wipe those, reinstall, re-verify. |
| `clone-behind` | `1` | The **clone** is behind its remote. Refresh the marketplace first, then re-check. Do not wipe yet — a wipe + reinstall would just reinstall the same old commit. |
| `none-installed` | `2` | Nothing installed. Proceed to install; do **not** read this as current. |
| `unverified` | `2` | Couldn't confirm. Say so plainly. **Never report success.** |

Only `current` means "nothing to do". `2` is deliberately not `0` — "I couldn't check" is not
"it's fine", and conflating the two is the whole reason this step exists.

Flags: `--json` for machine-readable output, `--marketplace <name>` for a non-`aura-tools`
marketplace, `--no-fetch` to skip the clone's `git fetch` (only right after a marketplace update, when
the clone is known fresh — it prints a caveat, since nothing was re-checked). The check also lists
**orphaned version dirs** — copies left by earlier installs, one per refresh. Remove them with
[`prune`](#pruning-orphaned-version-dirs).

### When plugins are stale

Reinstalling on its own may no-op, so the cache dir has to go first. **This deletes files, so
plan and confirm before running it:**

1. Preview the exact targets and show them to the user:

   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/skills/reload-aura-skills/scripts/check_install.py wipe --dry-run
   ```

2. **Ask the user to confirm** before the real run. Mention that other sessions or cron jobs
   using those plugins can fail mid-task if they invoke a bundled script while the dir is gone.
   `wipe` has no in-use check: stop monitor loops (e.g. `eng:monitor-firebase` passes) first.
3. Only then:

   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/skills/reload-aura-skills/scripts/check_install.py wipe
   ```

**Never wipe on a verify-only request.** "Am I running the latest skills", "is my plugin cache
stale", and similar are read-only asks: run `check`, report, stop. Offer the wipe as a next step;
don't take it.

Scope: the wipe removes **stale plugins only**. `--plugins a,b` narrows that set — it does not
widen it, so naming a plugin that is already current skips it (with a warning) rather than
deleting it; `--force` overrides that if you really mean to. Every target is verified to sit under
the plugin cache at the expected depth before anything is removed.

Then run the [Claude Code Reload](#claude-code-reload) installs, and **run `check` again**. Report
the before/after versions, not "done". If it's still stale after a wipe + reinstall, stop and say so —
something upstream of this skill is wrong and another reload won't fix it.

### Pruning orphaned version dirs

Every install writes a new `<plugin>/<version>/` and leaves the previous one behind, so these
accumulate on each refresh. `check` lists them; `prune` removes them. `wipe` is the wrong tool —
it deletes the whole `<plugin>` dir, live copy included.

Run this as the **last** step of a successful reload, after `check` reports `current`. Same
plan-and-confirm gate as `wipe`:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/reload-aura-skills/scripts/check_install.py prune --dry-run
```

Show the targets and the reclaimable total, **get confirmation**, then:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/reload-aura-skills/scripts/check_install.py prune
```

An orphan is not always inert: a session or cron that started *before* the refresh may still be
running from it. So `prune` checks every process first (`ps` arguments and `lsof` working dirs) and
**skips any dir in use**, reporting it in both modes, e.g.
`eng/3.184.0  skipped: in use by pid 4242 (python3 …/eng/3.184.0/skills/monitor-firebase/…)`. If
the process check itself fails, every dir is skipped (`skipped: process check failed (…)`), never
deleted. Re-run later to reclaim a skipped dir. **Never prune on a verify-only request.** The check
only sees a process while it holds the path: a loop that is idle between calls isn't visible.

### Codex

`check --host codex` surveys Codex on the **same rule** as Claude Code — declared version vs
installed version. The only difference is where the installed version is read from: Codex keeps no
install manifest, so it comes from the version directory name rather than from
`installed_plugins.json`. Both catch the failure this skill exists for (a version that never
moved); neither can tell whether the *contents* of a matching directory were tampered with
out-of-band, which is out of scope.

## Codex Reload

1. Confirm the Codex CLI is available. If `codex --version` fails, stop and tell the user that
   Codex reload requires the Codex CLI in the current shell:

   ```bash
   codex --version
   ```

2. Inspect configured marketplaces:

   ```bash
   codex plugin marketplace list
   ```

3. If `aura-tools` is not listed, add the GitHub marketplace:

   ```bash
   codex plugin marketplace add AuraHealth/AINative
   ```

4. Refresh the GitHub marketplace snapshot:

   ```bash
   codex plugin marketplace upgrade aura-tools
   ```

   If this fails because the marketplace has not been registered yet, add the marketplace and retry.

5. Install or reinstall the target plugins. If the user did not name a plugin set, install all
   Aura plugins with this exact sequence:

   ```bash
   codex plugin marketplace upgrade aura-tools
   codex plugin add shared@aura-tools
   codex plugin add eng@aura-tools
   codex plugin add product@aura-tools
   codex plugin add data@aura-tools
   codex plugin add marketing@aura-tools
   codex plugin add qa@aura-tools
   ```

   For a named team plugin, still refresh the marketplace first, then install `shared@aura-tools`
   and the team plugin:

   ```bash
   codex plugin marketplace upgrade aura-tools
   codex plugin add shared@aura-tools
   codex plugin add <team>@aura-tools
   ```

6. Verify the installed plugin state:

   ```bash
   codex plugin list
   ```

7. Tell the user to start a new Codex thread before testing the refreshed skills. Codex does
   not have a current-thread `/reload-plugins` equivalent; active threads keep the old skill
   registry even after the plugin install succeeds.

## Claude Code Reload

Claude Code plugin commands are slash commands, not shell commands — the user runs them in the
Claude Code prompt. Do not run `codex` commands in this branch.

### Step 1 — capture the "before"

Record the starting versions so the reload can report what actually moved:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/reload-aura-skills/scripts/check_install.py \
  check --host claude-code --json > /tmp/aura-before.json || true
```

**`--host claude-code` on every `check` in this branch.** The default surveys all hosts and
returns the worst verdict, so on a machine that also has Codex an unrelated stale Codex install
would drive this branch's step-3 decision.

**This is a capture, not a gate — ignore its exit code.** A pre-reload host is normally
`clone-behind` or `stale`, both of which exit non-zero. Branching on that here would send you to
wipe-or-stop before the marketplace update has even run. The `|| true` is deliberate; the verdict
that decides anything is the one in step 3, after the update.

### Step 2 — update the marketplace

```text
/plugin marketplace update aura-tools
```

Where slash commands aren't available (Desktop, headless, cron), the same operation via the CLI —
the only form an agent can run itself. Never skip this step when the slash form is unavailable: step
3b against an unadvanced clone reports every plugin `current` at its old version
([`references/caches.md`](references/caches.md)).

```bash
claude plugin marketplace update aura-tools
```

This advances the clone and refreshes plugins **whose version number is unchanged** — the copy
lands in the existing `cache/<marketplace>/<plugin>/<version>/` dir and the recorded sha moves.
Often that is the whole job (`✔ Updated 1 marketplace (N plugins bumped)`).

**It does not reliably move a plugin whose version increased** — the cache is keyed by version, so
a bump needs a *new* directory and that step can be skipped silently. Measured 2026-07-28: with
`eng` at 1.6.0 and the manifest at 1.10.0, a marketplace update reported success and left `eng` on
1.6.0, stale across four merges, while same-version plugins refreshed fine. Step 3 catches this —
never treat step 2's success line as proof.

Do **not** run the `/plugin install` commands yet. They are a fallback, not the default: when the
update already bumped everything, each prints `Plugin '<x>@aura-tools' is already installed
globally`, which reads as a failure when nothing is wrong.

If `aura-tools` isn't registered yet, add it first: `/plugin marketplace add AuraHealth/AINative`.
If the marketplace name differs locally, use that name (or omit it to refresh all).

### Step 3 — verify

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/reload-aura-skills/scripts/check_install.py check --host claude-code
```

- **`current`** — done. Go to step 5. No installs needed.
- **`stale`** — the update didn't take. Go to step 3b.
- **`none-installed`** — nothing installed yet. Go to step 4.
- **`clone-behind`** — the marketplace update didn't land; retry it before anything else.
- **`unverified`** — say so plainly and stop. Never report success.

### Step 3b — `claude plugin update` (the fix for `stale`)

The remedy for a version that didn't move. Shell command, not a slash command:

```bash
for p in shared eng product data marketing qa; do claude plugin update "$p@aura-tools"; done
```

Each prints `✔ <p> is already at the latest version (x.y.z)` or `✔ <p> updated from <old> to
<new> … Restart to apply changes.` Then re-run step 3; on `current`, go to step 5 and **skip step
4 entirely**.

Prefer this over step 4: non-destructive, and the only verb that moves an installed plugin to a
new version. `claude plugin install` **no-ops** on an already-installed plugin however far behind
it is — which is why the wipe path exists, and why you rarely need it.

### Step 4 — installs (fallback only)

Only when step 3 returned `none-installed`, or when step 3b left it `stale`:

```text
/plugin install shared@aura-tools
/plugin install eng@aura-tools
/plugin install product@aura-tools
/plugin install data@aura-tools
/plugin install marketing@aura-tools
/plugin install qa@aura-tools
```

For a single team, install `shared@aura-tools` plus that team's plugin. On `stale` specifically,
`wipe` those plugins first (plan + confirm — see [When plugins are stale](#when-plugins-are-stale)),
or the install may no-op. Then re-run step 3.

**Try step 3b before wiping.** `wipe` deletes installed plugin directories; `claude plugin update`
achieves the same result without removing anything, and clears the ordinary version-bump case.
Reserve the wipe for a genuinely corrupted install that `update` cannot repair.

### Step 5 — reload the registry, then report

```text
/reload-plugins
```

This refreshes the skill registry in the current session. If skills are still missing afterwards,
start a new Claude Code session.

`/reload-plugins` succeeding says the registry reloaded — it says nothing about whether the files
on disk changed. Only `check` does. So finish by reporting the **version delta**:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/reload-aura-skills/scripts/check_install.py \
  check --host claude-code --json > /tmp/aura-after.json
python3 ${CLAUDE_PLUGIN_ROOT}/skills/reload-aura-skills/scripts/check_install.py delta \
  --before /tmp/aura-before.json --after /tmp/aura-after.json
```

Show that table to the user. A plugin whose version didn't move is listed as `unchanged` rather
than omitted — an invisible no-op is the failure this whole flow exists to surface.

## Keeping the CLI current automatically

To stop the CLI install cache drifting at all, run the same `claude plugin update` sweep from a
throttled `SessionStart` hook — script, hook JSON, and verification in
[`references/auto-update.md`](references/auto-update.md). `autoUpdate: true` alone is **not**
sufficient: it refreshes same-version plugins but leaves a version increase behind (Step 2). Know
the limit — updates apply on **restart**, so the guarantee is *never more than one session behind*
`main`, not *current within the running session*. Desktop needs none of this (see [Hosts](#hosts)).

## Local Checkout Reload

Use this path only when the user explicitly says the marketplace points at a local checkout.

1. Pull the local repo the marketplace points at:

   ```bash
   cd /path/to/AINative
   git pull
   ```

2. Reinstall the target plugins for the detected host:

   - **Claude Code: follow [Claude Code Reload](#claude-code-reload) as written** — capture the
     before, `/plugin marketplace update aura-tools`, `check`, and run the `/plugin install`
     commands **only** if the verdict isn't `current`. Never sequence the installs
     unconditionally. Finish with `/reload-plugins` and the version delta. Do not run `codex`.
   - Codex, all Aura plugins:

     ```bash
     codex plugin marketplace upgrade aura-tools
     codex plugin add shared@aura-tools
     codex plugin add eng@aura-tools
     codex plugin add product@aura-tools
     codex plugin add data@aura-tools
     codex plugin add marketing@aura-tools
     codex plugin add qa@aura-tools
     ```

   - Codex, one team plugin: run `codex plugin marketplace upgrade aura-tools`, then
     `codex plugin add shared@aura-tools`, then choose exactly one concrete team command:
     `codex plugin add eng@aura-tools`, `codex plugin add product@aura-tools`,
     `codex plugin add data@aura-tools`, `codex plugin add marketing@aura-tools`, or
     `codex plugin add qa@aura-tools`.

   If the runtime is unknown, ask the user to choose Codex or Claude Code before reinstalling.

3. Start a new Codex thread or a new Claude Code session.

## Output

Return:

- Whether the marketplace was added or upgraded.
- Which plugins were installed or reinstalled.
- Any command that failed and the exact next action.
- The detected runtime, or that it was unknown.
- A reminder that active threads/sessions keep their old skill list.

## Edge Cases

- **`aura-tools` name mismatch**: read the marketplace name from `codex plugin marketplace list`
  and substitute that name in `codex plugin add <plugin>@<marketplace>`.
- **GitHub access failure**: ask the user to confirm they can access `AuraHealth/AINative` and
  are authenticated to Codex/GitHub as needed.
- **Skill still missing after reload**: open a new thread first. If it is still missing, rerun
  `codex plugin list` and verify the relevant plugin is installed and enabled.
- **Local source confusion**: do not run `git pull` unless the marketplace source is a local
  checkout. GitHub marketplace upgrades are handled by `codex plugin marketplace upgrade`.
