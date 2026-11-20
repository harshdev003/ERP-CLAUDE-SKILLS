# Reading and writing `metadata.version` safely

Canonical rule: `/CLAUDE.md` → **Skill versioning** → frontmatter-block only. This page is
the operational companion — what the trap is, and how `scripts/version_bump.py` avoids it.

## The trap

Several skills **document** the frontmatter convention by example, so their `SKILL.md` body
contains extra `metadata:` / `version: "…"` blocks that are content, not configuration:

| File | The example it carries |
|---|---|
| `shared:create-aura-skill` | the scaffold it emits for a new skill (`"1.0.0"`) |
| `shared:update-aura-skill` | the seed example in its Step 3 version-presence check |
| `/CLAUDE.md` | the frontmatter-shape example under *Skill versioning* |

A whole-file `grep` / `sed` / string replace hits whichever occurrence comes first in the
file. That misfires in both directions at once: you *read* a version that isn't the skill's,
then *write* the new version into a documentation example.

## Why counting diff lines isn't enough

The obvious assertion — "the diff should show exactly one `-`/`+` version pair" — does not
work. A corrupted body example produces exactly one pair too, indistinguishable from a real
bump. The check has to compare **line numbers** against the closing frontmatter fence.

This matters because it is the only defence at this layer. `scripts/check-version-bumps.py`
(the CI gate) parses the frontmatter correctly, so it *will* fail a missing bump — but it has
no way to notice that a documentation example was corrupted. That direction is silent.

## Usage

```bash
S="$SKILL_DIR/SKILL.md"
V="${CLAUDE_PLUGIN_ROOT}/skills/update-aura-skill/scripts/version_bump.py"

python3 "$V" read "$S"                    # -> 1.4.0   (frontmatter only, never a body example)
python3 "$V" assert "$S"                  # after the bump; non-zero exit = wrong target
python3 "$V" assert "$S" --base origin/main
```

`assert` exits non-zero with the offending line numbers when the change landed outside the
frontmatter, and when the count isn't exactly one. Run it before committing.

## Writing the bump

Split on the frontmatter fence and substitute inside that slice only:

```python
head, fm, body = pathlib.Path(skill_md).read_text().split("---", 2)
fm = re.sub(r'(version:\s*")[^"]+(")', rf'\g<1>{new}\g<2>', fm, count=1)
pathlib.Path(skill_md).write_text("---".join([head, fm, body]))
```

## Where this came from

While preparing [#709](https://github.com/AuraHealth/AINative/pull/709),
`grep -A1 '^metadata:' … | tail -1` reported `1.0.0` for `create-aura-skill` and
`update-aura-skill`, which were actually on `1.3.0` and `1.4.0`. The follow-on replace then
rewrote `update-aura-skill`'s scaffold example from `"1.0.0"` to `"1.1.0"` and left both real
versions untouched — shipping a wrong seed to every skill scaffolded from that template,
while the bump the PR needed never happened. Filed as
[ENG-983](https://linear.app/aura-health/issue/ENG-983).
