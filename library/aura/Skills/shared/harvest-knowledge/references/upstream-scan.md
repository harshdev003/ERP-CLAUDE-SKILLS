# Upstream scan paths (Step 4)

How to find upstream canonical-source changes in the window, and how to tell a quiet window from a blind one.

Two paths run, and they do **different jobs**. REST enumerates the window; the MCP teamspace probe cross-checks it. Neither alone is sufficient, and they fail in opposite directions.

## What each tool can actually do

| | REST `search` | MCP `notion-search` |
|---|---|---|
| Sort by `last_edited_time` | **yes** | **no such parameter** |
| Filter by edit time | no (post-filter client-side) | no (post-filter client-side) |
| Filter by creation date | no | `created_date_range` only |
| Scope to a teamspace | no (post-filter via `root_page_of`) | **yes**, `teamspace_id`, server-side |
| Pagination | **cursor, unbounded** | none — `page_size` caps at **25** |
| Query | empty query enumerates | **semantic, query string required** |

Read the top row and the bottom row together: **only REST can enumerate "everything edited since X"**, because it is the only one that can order by edit time and page past 25 results. MCP `notion-search` takes a semantic query and returns up to 25 ranked matches — it answers "what in this teamspace is about X", not "what changed here since X".

**Do not filter a changed-since window with `created_date_range`.** It filters on *creation*, so a page created before `since` and edited inside the window is dropped server-side, and no client-side `last_edited_time` check can recover what the result set never contained. Edits to existing docs are the majority of what harvest exists to mirror, so a created-date filter silently removes most of the signal while returning a plausible-looking set.

## REST enumeration — the window scan

This is the path that establishes coverage. It runs whenever `NOTION_TOKEN` is set.

1. Resolve each candidate source teamspace to its **top-level (root) page id**. REST exposes no teamspace-listing endpoint, so resolve roots from the source teamspaces' landing pages (follow the canonical-source links the team maintains, or a small configured set of root ids). Keep the resolved `{root_page_id → teamspace_name}` map.
2. Enumerate recently-edited content: `notion.search(query="", sort={"direction": "descending", "timestamp": "last_edited_time"})`. Iterate and **stop as soon as a result's `last_edited_time` is older than `since`** — the sort guarantees everything after it is older too.
3. **Page all the way to that cutoff.** The helper's cursor pagination is unbounded, and reaching the cutoff is what makes the scan complete. Do not impose a result cap: a scan that stops early hasn't covered the window, and the missing tail is indistinguishable from an empty one.
4. For each surviving hit, call `notion.root_page_of(hit_id)` and keep only hits whose root is in the source-teamspace map. (One `get_page` per ancestor hop — cache roots you've already resolved.)

### The window is expensive because our own writes dominate it

REST has no teamspace filter, so step 2 enumerates the whole workspace and this teamspace's own automated writes fill most of it. A representative measurement: **992 of 993 in-window results were our own writes** — `Signals/Conversation Patterns` and `Signals/Cancellations` rows plus experiment-row updates and snapshots, 428 rows of it created earlier in the same session.

That is a cost problem, not a correctness one — *provided step 3 actually reaches the cutoff*. It turns into a correctness problem the moment the enumeration stops early, which is what happened on a run that halted at 300 results without reaching its cutoff and reported 0 upstream findings.

**Own-writes ratio.** Compute the share of in-window hits whose `root_page_of` resolves to the AI Knowledge teamspace root, and record it on the run page under `# Sources scanned` on every run.

| Condition | Verdict |
|---|---|
| Enumeration reached `since` | **Complete** — report the result, whatever the ratio |
| Enumeration stopped before reaching `since` (interrupted, rate-limited, capped by a caller) | **Unknown** — the window was never covered |
| Ratio > 0.9 **and** the scan completed | Complete, but flag the cost — the signal-to-noise is poor and a future run should expect the same |

A high ratio on a completed scan is **not** a swamped verdict. It means the sweep was expensive, not that it was blind. Only failing to reach `since` makes the result unknown.

## MCP teamspace probe — the cross-check

Runs alongside REST whenever the Notion MCP is available. `notion-get-teams` to list teamspaces, then `notion-search` per source teamspace with `teamspace_id` set and a handful of topic query terms drawn from the areas harvest tracks.

Its value is that server-side teamspace scoping makes it **immune to our own writes** — it sees upstream content directly, at whatever volume, without wading through 992 rows of our own. That is a genuinely different view from REST's, and it is how a real run surfaced upstream hits (Engineering 1, Prompts 8–9) on a day its REST leg had stopped short.

**Its hits still have to pass the window predicate.** Apply `last_edited_time >= since` client-side to every result and drop the rest — the probe ranks by semantic relevance, not recency, so most of what it returns on a broad query will be older than the window. A relevant page that hasn't changed since `since` is not a finding.

Its limits are structural and must be stated on the run page rather than assumed away:

- **Semantic, not exhaustive.** Results are ranked matches for the query terms supplied. Terms nobody thought to ask about return nothing, and that silence is not evidence of absence.
- **Relevance-ranked, not recency-ranked.** The 25 slots go to the best semantic matches, so an in-window edit can be crowded out by an older, more on-topic page. This compounds with the cap: the probe can miss a recent change while returning 25 results.
- **25 results per call, no cursor.** A teamspace returning 25 has more; there is no way to page further, and no way to narrow by edit time because the tool has no such filter.
- **Cannot establish coverage.** Never conclude "nothing changed upstream" from the MCP probe alone.

Record per teamspace what was actually asked and what came back — `Engineering: 4 query terms, 1 hit` — so a later reader can tell a thin probe from a thorough one.

## Reconciling the two legs

| REST | MCP probe | Verdict |
|---|---|---|
| Reached `since` | ran | **Complete.** Union both, de-duplicate by page id. |
| Reached `since` | unavailable | **Complete.** REST establishes coverage on its own. |
| Stopped short | ran | **Partial.** Report the MCP hits as real findings, and the window as *unknown* — the probe found things, but it cannot prove nothing else changed. |
| Stopped short | unavailable | **Unknown.** Say so; do not report zero findings. |

The failure this table exists to prevent is a run that reports "0 new / 0 updates" when what actually happened is that nothing looked. When the verdict is *unknown*, the next step in the summary is to re-run the REST enumeration to completion — not "load the MCP", which cannot answer the coverage question either way.

## Establishing drift on a mirrored page

An upstream hit that already has an L4 mirror raises one question: has the mirror fallen behind? Answer it against the mirror's **blocks**, never against a line-level diff of the two rendered pages.

### A line diff over-reports, and over-reporting is the dangerous direction

The two pages express the same content in different block structure, so a text diff reports shape as though it were substance. A missing row is loud — someone notices the gap. A **spurious** row is quiet: it reads exactly like a real finding, and acting on it rewrites content the mirror already carries, in the house voice, correctly.

Measured on `Prompt Style Guide`: **13 candidate gaps, 4 real.** The nine false positives were all structural.

| What the diff saw | What was actually true |
|---|---|
| Six one-word "missing lines" (`Updated`, `Added`, `Deleted`, …) | Upstream renders its changelog verbs as **table cells**; the mirror carries them in one prose sentence |
| A missing multi-line Avoid/Prefer example | Upstream keeps it in **one block**; the mirror splits it across several |
| 30 of 31 headings "differing" | The mirror applies the house **sentence-case** convention. The headings match 1:1 structurally |

Heading case is worth calling out separately: it makes almost every heading differ textually while the document structure is identical, so a diff that counts headings will always look alarming and never mean anything.

### The check

For each candidate line the diff surfaces, fetch the mirror's blocks and test the candidate against their text. A normalised comparison — lowercase, strip punctuation, then a similarity ratio against every block — is enough to separate a real gap from a re-flowed one. Only what survives that test becomes a write-plan entry.

Two things about that fetch decide whether the test is sound, and both default the wrong way here:

```python
blocks = notion.get_block_children(mirror_id, recursive=True, max_depth=6)
```

- **Pass `max_depth=6`, not the default 3.** A bullet inside a toggle inside a column sits below the default bound and is dropped without a signal, so its text reads as absent from the mirror. That is a false positive — arriving through the very check written to stop them. When a block at the bound still reports `has_children`, record the page as partially captured rather than concluding the content is missing.
- **Skip `child_page` subtrees while flattening.** `recursive=True` descends into nested pages, so a parent absorbs its children's text and a candidate living on the child looks present on the parent. That direction is a false *negative* — a real gap dismissed. The mirror's body is the mirror's own blocks; a nested page is a page in its own right, and Step 3's L4 index already visits it as one. Same asymmetry [`about-sections.md`](about-sections.md) documents for the About walk.

Do not skip this because the diff looks decisive. The diff is a **candidate generator**, not a verdict.

### What the method does catch

The point of the check is precision, not timidity — real drift exists and is worth finding. On the same page family, `Prompt Engineering Process Document` had genuinely fallen behind, and in a way that mattered more than staleness: the mirror **contradicted** upstream. It described Stages 6–8 as a strict sequence where upstream calls them iterative, and it called automated conversational scoring "unreliable" where upstream says scores can support evaluation but do not replace reading the transcripts.

So a mirror does not merely lag — it can assert the opposite of the canonical doc while looking complete. That is what makes the verified-against-blocks check worth running rather than dropping.

### Connecting the integration overwrites a root page's `last_edited_time`

Granting the Notion integration access to a page **writes that page**. Every newly connected subtree root therefore carries `last_edited_time` equal to the connection date, and whatever timestamp it held before is unrecoverable.

Descendants are **not** bumped — verified 9 of 9 down to four levels deep, all retaining their original dates — so the false-positive wave is bounded to the roots themselves, one hit each.

Two consequences for the first run after a connection:

- Those roots appear in the window as edits that are not edits. Exclude them by date, and say on the run page that they are connection artifacts rather than upstream changes.
- For a newly connected root, **drift cannot be established by date** — the evidence is gone. Establish it by content, per the check above.

This is one-time, at connection. Later runs read genuine timestamps.
