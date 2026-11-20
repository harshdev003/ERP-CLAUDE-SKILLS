# The two caches

A machine running Aura skills holds them in **two independent places**. They answer different
questions, neither predicts the other, and `check` reads only one. Confusing them produces a
confident report that is exactly backwards.

| | Where | Governs | Refreshed by |
|---|---|---|---|
| **Per-session extract** (Desktop / local-agent mode) | `local-agent-mode-sessions/<session>/…/rpm/plugin_<id>/` | the skill text the **running conversation** executes | pulled from the marketplace on session start, and again mid-session when a reload runs |
| **CLI install cache** (Claude Code) | `<config>/plugins/cache/<marketplace>/<plugin>/<version>` | **new CLI sessions** | `marketplace update` to advance the clone, then `plugin update` to move the installed version |

## Desktop re-extracts mid-session

The per-session copy comes from the marketplace **independent of the CLI install cache**, so Desktop
self-refreshes on restart — and a reload re-extracts it without one, so a Desktop session can be
current while the CLI is stale.

Measured 2026-08-06: a reload 32 seconds after a merge put qa 1.1.2 / eng 1.26.1 / data 2.9.1 into an
already-running Desktop session, while the same machine's CLI clone still sat behind the merge commit
and its install cache still held qa 1.1.1. The session copy's `plugin.json` and `SKILL.md` both
carried an mtime 32s after the merge — the reload rewrote them in place.

There is no clone and no install manifest under a session extract, so there is nothing to diff it
against. Read the version out of the copy itself:

```bash
SESS="<…>/local-agent-mode-sessions/<session>/<sub>/rpm"
for d in "$SESS"/plugin_*; do
  python3 -c "import json,sys;j=json.load(open(sys.argv[1]));print(j['name'],j['version'])" \
    "$d/.claude-plugin/plugin.json" 2>/dev/null
done
```

## Verify the cache the user is actually running

`check` surveys the **CLI** cache only. So a `stale` verdict there says nothing about the Desktop
session the user is typing into, and a current Desktop session is not evidence the CLI is current.
Establish which host the user is on, verify that one, and **say which you checked**. Reading the CLI
cache and telling a Desktop user "your reload didn't take" inverts the truth.

## Reading mtimes without lying to yourself

`stat -f %Sm` prints **local** time. Appending a literal `Z` to its output labels local time as UTC
and shifts every timestamp by the UTC offset — enough to place an event hours before a merge it
actually followed, which reads as "impossible" and invites the wrong conclusion. Convert explicitly:

```bash
python3 -c "import os,sys,datetime as d; print(d.datetime.fromtimestamp(os.stat(sys.argv[1]).st_mtime, d.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))" <file>
```

## Why version and not commit sha

`installed_plugins.json` also records a `gitCommitSha`, and comparing it to the clone HEAD looks
sharper. **Do not.** That sha is stamped only when a plugin is *re-extracted*, which happens only
when **that plugin** changes — so any commit to the repo moves HEAD while every untouched plugin
keeps an older sha, and reads as stale while being current. On a healthy install that is most of
them.

Version equality is safe because the CI bump gate makes it so: any change under `plugins/<p>/`
must bump that plugin's manifest version, so equal versions imply equal content.

**Do not "fix" the sha comparison by unshallowing the clone** — Claude Code owns it and re-fetches
it shallow (`depth=1`) on its own schedule, so any local deepening is undone at the next
auto-update.

## Why step 2 cannot be skipped

`claude plugin update` (step 3b) moves an installed plugin to whatever version the **clone**
declares. Run against a clone that was never advanced, it reports every plugin `current` at its old
version — a false pass indistinguishable from a real one. Advance the clone first, by whichever form
the host allows.
