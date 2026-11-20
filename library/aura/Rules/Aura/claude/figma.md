# Figma — Read-Only

**Figma is read-only. Never create, edit, move, rename, delete, comment on, or publish
anything in a Figma or FigJam file.** The job is to make the app match Figma — never to
make Figma match the app.

This holds even though the connected account has editor access, and even when the design
is obviously wrong. Editor access is not permission to write.

## Related Rules

- `ui-design-system.md` for turning extracted values into theme tokens.
- `.claude/skills/figma-to-code/SKILL.md` for the extract → build → verify method.

## Why

- **Figma is the designer's file, not a scratchpad.** It is the handoff contract between
  design and eng. Design owns it; eng consumes it.
- **An agent edit is invisible.** There is no PR, no diff, no review — nothing the designer
  opted into. They find out when the file no longer matches what they drew, usually days
  later, with no record of who changed it or why.
- **It poisons every later extraction.** Every subsequent screen is built by reading this
  file. A silent edit means the next screen is built against the agent's own change and the
  error compounds instead of surfacing.
- **Editor access is incidental.** The Figma MCP wants an editor seat to read some files at
  all (and `download_assets` needs one). Holding the seat to *read* is not consent to write.

## What "modify" covers

Anything that changes bytes in a Figma-hosted document:

- frames, layers, components, variants, styles, variables
- **comments and annotations** — a comment is a write; ask first
- FigJam boards, diagrams, slides
- Code Connect mappings (`add_code_connect_map`, `send_code_connect_mappings`)
- uploaded assets, shaders, generative plugins
- creating a new Figma file — including an empty scratch one

## Tools

| Allowed (read) | Forbidden (write) |
|---|---|
| `get_design_context`, `get_metadata`, `get_screenshot` | `use_figma` |
| `get_variable_defs`, `get_figjam`, `get_motion_context` | `create_new_file` |
| `get_code_connect_map`, `get_code_connect_suggestions` | `add_code_connect_map`, `send_code_connect_mappings` |
| `get_libraries`, `search_design_system`, `list_*` | `upload_assets`, `weave_upload_asset` |
| `get_shader`, `get_shader_effect`, `get_shader_fill` | `create_shader`, `update_shader` |
| `download_assets` (downloads locally) | `create_generative_plugin`, `update_generative_plugin` |
| `whoami`, `get_figma_skill`, `read_skill_uri` | `generate_diagram` |
| the Figma REST API, read-only scope | `weave_run_tool` (generic write escape hatch) |

The forbidden column is enforced by the `deny` block in `.claude/settings.json`, so these
calls are refused by the harness rather than left to judgement. **If the Figma MCP server id
changes, that deny list must be updated** — entries are `mcp__<server-id>__<tool>`.

Any tool not listed above is judged by the same test: *does it change bytes in a
Figma-hosted document?* If yes, it is forbidden. `DesignSync` and any future sync tool are
covered by that test, not exempt from it.

## Skills

Never invoke the write-oriented Figma skills: `figma:figma-use`, `figma:figma-generate-design`,
`figma:figma-create-new-file`, `figma:figma-generate-library`, `figma:figma-generate-diagram`,
`figma:figma-use-figjam`, `figma:figma-use-slides`, `figma:figma-use-motion`,
`figma:figma-implement-motion`, `figma:figma-code-connect`.

Use `figma-to-code` (this repo's skill) for implementation work. `figma:figma-design-to-code`
is read-oriented and fine as background method.

## When the design is wrong, missing, or ambiguous

This is the case that tempts an edit. Don't take it.

1. **Do not fix it in Figma.** Not the spacing, not a missing state, not an obvious typo.
2. **Build what is actually there**, or stop if it is not buildable.
3. **Report it to the user** with the node URL, what is wrong, and the proposed fix *in words*.
4. The user relays it to the designer. The designer changes Figma. You re-extract.

That round trip is the process already in use and it works — the mini player was missing
from the 4.0 file, it was raised in Slack, and Alex added the frames. The correct output of
"design is missing" is a message, never a frame.

```
✅ GOOD
   "playlist row spacing isn't in the 4.0 file — node 3226-25964 has no
    gap token on the horizontal list. Built to 12pt to match the sibling
    row; worth Alex confirming."

❌ BAD
   [adds the missing gap in Figma so the extraction is clean]
```

## The one exception

There is no standing exception. If the user explicitly asks for a specific Figma edit in
that conversation, then: do only that edit, only in the node they named, confirm the change
before making it, and report what changed afterwards.

Approval is **per-request and per-session**. "You can edit Figma" said once does not carry
to the next request, the next file, or the next conversation.

## Don't

- Don't edit Figma to make an extraction cleaner or a diff simpler.
- Don't leave a Figma comment to flag a problem — flag it in Slack/Linear where the team looks.
- Don't create a scratch/duplicate Figma file to experiment in.
- Don't publish or update Code Connect mappings as a side effect of implementing a screen.
- Don't treat "I have editor access" as "I may write".
