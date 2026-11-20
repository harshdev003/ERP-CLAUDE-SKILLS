# Detector catalogue

The thirteen detectors `healthcheck-knowledge` runs, in priority order. Detectors 1–5 are the baseline; 6 is a stretch goal. Each is independent — one erroring is logged under Detector status and the rest continue (rule 5).

Every detector reads the per-page capture from Step 4, so the fields it can rely on are defined there: `blocks[]` (each with `type`, `heading`, `text`, `hrefs[]`), plus `child_count`, `path`, and `body` — the block texts joined in document order, for detectors that want whole-page text rather than per-block structure.

1. **Contradictions** — two pages make incompatible claims (e.g., Aurie modality count differs between pages). Initial detection is scoped to page titles + `**At a glance:**` openers + headline numeric/list facts. No deep semantic comparison in v1.
2. **Stale claims** — absolute dates older than 6 months, "as of <old year>", hardcoded user counts, version pins that may have moved on. **Distinguish provenance from freshness**: phrases like "tracking spend since 2022" or "data covers 2022-onwards" are provenance statements (when the data series began), not freshness claims about the page. Skip when the surrounding context describes a historic data range (e.g. tab names like `'22-26`). Only flag when the phrase implies the *page itself* hasn't been re-validated.
3. **Orphan pages** — pages not linked from any other page in the teamspace AND not referenced by any AINative skill. Three independent checks must all miss before a page is flagged as orphan:
    - **In-teamspace outbound links** — walk `# Related` sections + every body block's rich_text (including mentions and inline links).
    - **AINative repo grep** — for each candidate, grep the AINative checkout for **two** needles and treat a hit on either as a reference: the full path (`Procedures/AI Native/Feedback Analysis — Run Cadence`) **and** the bare leaf title (`Feedback Analysis — Run Cadence`). Skills that declare `knowledge.reads: ['Tools/Notion']` in SKILL.md frontmatter count as references. This catches the dominant case where a page is consumed by a skill but never linked from another L4 page.

        ```bash
        grep -rlF --include='*.md' "$path"  plugins/ || grep -rlF --include='*.md' "$leaf" plugins/
        ```

        **Both needles, not just the path.** A `knowledge.reads:` entry uses the full path, but skill *prose* overwhelmingly names the page by its title alone — so a path-only grep misses the more common form. Measured on the 2026-09-03 run it reported 6 referenced pages as orphans, `Procedures/AI Native/Feedback Analysis — Run Cadence` (cited by three cadence skills) among them. Use `-F`: paths routinely contain regex metacharacters (`Rating & Feedback Events (*… RATED*)`).
    - **Full-text reverse search** — for each remaining candidate, search every crawled page for the candidate's `page_id` (un-hyphenated) in **both** `body` **and** every block's `hrefs[]`. The hrefs half is the one that matters: a Notion page link carries the target id in the href and shows a human-readable title as its text, so a `body`-only search misses precisely the hyperlink mechanism this check exists to catch, and reports linked pages as orphans.

        **Extract the id from the URL's last path segment — never by regexing the whole hyphen-stripped href.** A Notion URL is `…/p/<Title-Slug>-<32 hex>`, and stripping hyphens fuses the slug's trailing letters onto the id. Any of them that are hex extend the run past 32, so a greedy `[0-9a-f]{32}` returns a **window shifted left by that many characters** — a well-formed id that matches nothing:

        ```python
        HEX32 = re.compile(r"[0-9a-f]{32}")

        def page_id(href):                      # None when the href is not a page link
            if href.startswith("notionpage:"):  # mention.page.id / link_to_page.page_id
                return href.split(":", 1)[1]
            seg  = urlparse(href).path.rstrip("/").split("/")[-1]
            tail = seg.replace("-", "").lower()[-32:]
            return tail if HEX32.fullmatch(tail) else None
        ```

        `Prod-Log-Triage-37b75e…364a8` is the worked case: the `e` ending "Triage" makes a 33-character run, so the greedy form yields `e37b75e…364a` and the page reports as an orphan while being correctly linked. Boundary-anchoring (`(?<![0-9a-f])`) does not rescue it — the run is genuinely 33 long, so the assertion fails and it matches nothing at all. Do **not** pass `re.I` either: it lets uppercase slug letters join the run and shifts the window further.
   A page is only flagged orphan if *all three* checks miss. **Exclude** dated Signals snapshots (parent is what gets linked), recently-created pages (< 7 days old — expected lag), and Tools/Aura API subpages (parent is the canonical entry point — downgrade these to Low).
4. **Missing concepts** — bolded or linked terms that recur ≥ 3 times across pages but have no dedicated page to link to. Severity **Low**.

    **Bold is an annotation, not `**` characters — and reading the wrong one makes the detector silently blind.** Notion stores emphasis in `rich_text[].annotations.bold`; a regex for `\*\*…\*\*` over `plain_text` matches almost nothing (12 blocks teamspace-wide, all of them markdown *examples*). A run built that way returns **0 candidates and looks like a clean teamspace**. Capture `annotations.bold` per run — 166 pages carry **3,633** bold runs, which is the corpus this detector actually has.

    **Then exclude three things, or the result is unusable.** Measured on the live corpus 2026-09-20: the bare rule produced **47 candidates and zero were real.** Not one. Each exclusion below names what produced the noise:

    - **The house page template.** `Guide` → Authoring rules *mandates* a bolded `At a glance:` opener, so it is bolded on effectively every page — **57 pages**, the single largest "candidate". Same for `Purpose`, `Quick summary`, `What good looks like`, `What works`, `What fails`, `Contact`, `Category`, `Why`. These are section labels the template requires, not concepts anyone could write a page about. A detector that reports its own house style is the self-documentation trap #8, #11 and #12 all carry.
    - **Function words used for emphasis.** Authors bold `not`, `and`, `no`, `only`, `both`, `before`, `every`, `all`, `type` to stress a clause. `not` alone appears on 39 pages. Require a noun phrase: reject single function words outright, and reject any candidate that is not capitalised or multi-word.
    - **A field label whose subject already has a page.** `Viktor skills` recurs on 6 `Tools/*` pages as the label of a *"which skills consume this"* line — and `Team/Viktor` exists. The title match missed it because the bolded string is the label, not the subject. Match candidates against page titles by **containment in both directions**, not equality.

    **And confirm a survivor in context before reporting it.** The two candidates that survived every filter above still both dissolved on inspection: `front prompt` is prose about the Front agent's prompt row, already documented at `Tools/Aura API/Support`. At Low severity the whole value of this detector is that a finding is worth a glance — so read the surrounding block first, and report nothing rather than a list nobody will finish.

5. **Data gaps** — pages that exist but are skeletons: missing `# Sources`, missing expected sections for their type (e.g., an App page with no ICP section while its sibling has one). **`# Sources` check must inspect block type + content** (e.g. `block.type == 'heading_1'` with rich_text plain_text equal to `"Sources"`), NOT a regex over rendered body text — Notion block-text rendering strips heading markers (`#`), so a regex like `^#\s*Sources$` matches nothing and reports every page as missing. (Bug observed and fixed 2026-06-05.)
6. **Duplicate content** *(stretch)* — two pages covering the same subject with different phrasing. Use title similarity + `body` word-set Jaccard > 0.6. No embeddings in v1.
7. **Outdated links** — run it in **four arms, split by what is actually decidable**, and report each arm's coverage separately. A single "check every URL with WebFetch" pass is why this detector went unrun for its whole history: most of the corpus cannot be decided that way, and a 404 from an undecidable link is indistinguishable from a dead one.

    | Arm | Corpus (2026-09-03) | Method | Verdict |
    |---|---|---|---|
    | **a. Autolinked bare filename** | 14 | local — no network | **Decidable, free.** Report as findings |
    | **b. Internal Notion links** | 715 | `notion-fetch` the target id | **Partly decidable.** A 404 is `unresolvable`, not a finding — see below |
    | **c. GitHub** | 350 | `gh api repos/{owner}/{repo}/...` | **Decidable only when authenticated** |
    | **d. Public docs** | ~112 | WebFetch, capped + rate-limited | Decidable; cap and report the cap |
    | **e. Auth-walled consoles** | ~111 | — | **Not decidable. Skip; never report** |

    **Arm (a) needs no network and finds the errors nobody is looking for.** Notion autolinks a bare filename that looks like a domain, so prose reading "see the SKILL.md" silently becomes a hyperlink to `http://skill.md/` — an unintended off-site link on an internal page, pointing at a domain Aura does not control. Match any href whose host is a bare `*.md` (or has no path beyond `/`) and whose link text is the filename itself. Severity **Medium**; the fix is to unlink the text, never to repoint it.

    **Arm (b): a 404 is NOT a finding, and the table above overstates it.** Run on 2026-09-20 the rule *"404 or archived = finding"* produced **19 findings of which zero were real** — 0% precision, worse than the arm it was meant to be the reliable one. The integration answers 404 for three different situations and only one is a dead link:

    - **The target is outside the teamspaces the integration is connected to** — 17 of the 19. `Guide` → Notion REST setup already says it: without the teamspace connection, REST returns 404. These were `# Sources` citations to canonical upstream docs (`Aurie – Messaging Master Doc`, `Aurie - Team Home`, `Aurie - Product`, `Grace Periods`), several of which **name their teamspace in the citation text itself** — "*in the Product teamspace*". A human clicking them in Notion is fine. Reporting them invites the one fix that must never happen here: **deleting provenance from `Identity/*`, `Domain/*` and `Products/*` because a tool could not see the target.**
    - **The id is a character wrong.** `Products/Aura/Features/Recommendation System` cited `f446e82d467a44b9**9**3346ba4b4ef669a` for `f446e82d467a44b**6**93346ba4b4ef669a` — one transposed digit at index 15. It 404s and renders as *Untitled*, identical to a deletion, and the page was alive the whole time. Before declaring a target dead, look for a near-miss id in the live tree; the walk had already found `Models` sitting under `Recommendations`.
    - **The page really is gone** — 1 of the 19. `Procedures/Engineering/Cost Optimization`, and it was confirmable by a *second route*: the path is absent from the tree walk **and** a successor exists (`Procedures/Product/Prompt Engineering/Cost Engineering & Optimization`).

    **So the arm's verdict is `unresolvable`, never `dead`,** and a finding needs corroboration beyond the status code: the target's *path* missing from the tree walk, a named successor, or a human confirming in the browser. Where the cause is the connection gap, the fix is to **connect the integration to that teamspace** — one action that retires the whole class — not to touch a single page.

    **And read the citation text before judging it.** These are `# Sources` bullets; the prose names the document and often its location, which is usually enough to classify the 404 without any API call at all.

    **Arm (c) must use `gh`, not WebFetch.** Every AuraHealth repo is private, so an unauthenticated fetch of a live URL returns 404 — identical to a deleted one. WebFetch on a `github.com/AuraHealth/...` link therefore reports the entire corpus as broken. That is 350 of 587 external links, so getting this wrong does not degrade the detector, it inverts it. Where `gh` is unavailable, report arm (c) as **not run** rather than falling back to WebFetch.

    **Arm (e) is a deliberate non-finding.** A vendor console (`linear.app`, `mixpanel.com`, `app.bugsnag.com`, `dashboard.stripe.com`, `*.slack.com`, `*.postman.co`, `console.cloud.google.com`, `braintrust.dev`) answers 401/403 to an anonymous request whether or not the resource exists. Keep these on an allowlist and exclude them from both the findings and the denominator — counting them as "checked" overstates coverage.

    Cap arm (d) per run and dedupe by URL first (587 links resolve to 330 distinct). Report per-arm counts under Detector status, e.g. `#7: a 14/14, b 715/715, c 0/350 (no gh), d 96/112 (capped), e 111 skipped`. **A per-arm count is the whole point** — one aggregate number lets an undecidable arm's silence read as a pass.
8. **Linear card links in knowledge content** — Linear cards are ephemeral by design: they belong to in-flight work, and their content moves or closes without warning, so an L4 page citing one degrades into a pointer at something that no longer says what it said. Severity Medium.

    **Match card URLs, not the `linear.app` domain.** The finding is about *ephemerality*, and only a card has it — a workspace root, a `/settings/*` page, a `/docs/*` page, or a saved view is as durable as any other product URL. Match the card path:

    ```python
    CARD_URL_RE = re.compile(r"https?://(?:www\.)?linear\.app/[^/\s]+/issue/", re.I)
    ```

    Anything else on the domain is **not a finding**. A page that documents a tool has to be able to link to it: `Tools/Linear` cites the workspace root and the `/settings/api` page an operator must visit to generate `LINEAR_API_KEY`, and both are as durable as any vendor-documentation URL the teamspace already cites.

    **Match on `hrefs[]`, not on body text.** In this teamspace a Linear reference is almost always a *linked* ticket id — a bullet reading `PROD-2365 — Create PVC in Cartesia` has the card URL in its href and nowhere in its text. Measured across the teamspace, **~97% of references are href-only**, so a scan of rendered text finds almost none of them. Test every block's `hrefs[]` with `CARD_URL_RE`, and its `text` with the same regex for the rare pasted raw URL.

    **The text arm needs the same URL shape — never a bare domain mention.** Any page stating this rule — the `Guide` authoring rules, this file — necessarily names the domain in prose, so a bare-substring text arm makes the rule's own documentation a standing finding and forces authors to write around the detector. Requiring the full card-URL shape removes that class of false positive rather than managing it. Naming a ticket id in prose (`ENGBACK-1252`) is likewise not a finding: the rule targets links, not mentions.

    **Report Sources hits and prose hits separately** — they are different jobs and cost wildly different amounts. Use the block's `heading` (Step 4) to classify, matching the heading on its **first word** rather than on equality:

    ```python
    is_sources = block["heading"].strip().lower().split()[:1] == ["sources"]
    ```

    An equality test against `"Sources"` misses a section-scoped provenance heading — `Tools/OpenAI` carries `Sources (this section)` — and silently reclassifies those entries as prose. That inverts the costing this split exists to produce: three mechanical unlinks were booked as paragraph rewrites on the 2026-09-03 run. Anything whose heading *starts* with `Sources` is a provenance block.

    | Where | `subcategory` | Fix | Effort |
    |---|---|---|---|
    | Under a `Sources` heading | `sources` | Drop the pointer. If the bullet's only content was the ticket, delete the bullet; if it carried a durable source too (`GitHub: … and Linear PROD-1961 — …`), strip the Linear clause and keep the rest. | mechanical |
    | Anywhere else (body prose) | `prose` | Rewrite the surrounding sentence to state the fact directly, or delete it if it only tracked in-flight work. | judgement, one at a time |

    Collapsing the two into one count is not a cosmetic reporting choice: it prices a provenance line at the same rate as a paragraph that needs rewriting, which is how a remediation gets estimated at four times its real cost.

    **Before deleting the last `Sources` bullet on a page, check what else is under that heading.** A page left with a `# Sources` heading and no entries has lost its provenance, and **no detector will tell you**: #5 tests that the heading exists, not that it has content, and Step 6's pre-review deliberately creates empty `Sources` headings for pages that never had one — so the empty state is indistinguishable from a known-and-tolerated placeholder. Surface it in the finding's description so the human decides at review time; do not rely on a later run to catch it, and do not invent a source to fill the gap.

    **Exclusions.** Immutable audit run pages (`Guide/Harvest/*`, `Guide/Health Check/*`) are out of scope — `harvest-knowledge` rule 9 forbids rewriting a past run page, so flagging them produces findings nobody is allowed to action. `harvest-knowledge` rule 11 already stops new ones being created. Step 2's crawl excludes them by path, so they should not reach this detector at all; if one does, the crawl exclusion has drifted.

    **No other exceptions.** A `# Sources` entry citing Linear is still a finding — it is merely a cheap one. `# Sources` should cite durable canonical docs (Notion pages, Drive files, web articles, GitHub PRs). If a Linear thread was the original ingest source, capture the *content* in the page body and drop the pointer.

9. **Guide/Index drift** — fetch `Guide/Index` and reconcile against the L4 page tree:
    - **Stale entry** (`subcategory: stale-entry`) — index lists a path whose target page no longer exists or has been renamed. Severity Medium.
    - **Missing entry** (`subcategory: missing-entry`) — a page or database exists in L4 but has no row in the index. Severity Medium. **Scope this to the pages `Guide/Index` is actually generated from: the whole tree, to the leaves**, excluding only `Guide/Harvest`, `Guide/Health Check`, dated `Signals/<topic>` snapshots, the section roots themselves and `Guide/Index`. Measured on 2026-09-03 the index carried 153 entries against 169 in-scope crawled pages, reaching three levels deep under `Tools/Aura API` and `Procedures/Engineering/Monthly Tasks`. A shallower assumed scope is the dangerous direction: it silently drops most of the tree out of the `missing-entry` comparison, so real drift on a deep page cannot be reported at all.

      That scope is `harvest-knowledge`'s to define; it is restated here rather than linked because a skill cannot read a sibling skill's reference at runtime (Architecture principle 1). If the index walk changes there, update this clause in the same PR — the two drifting apart is what produces either a flood of false findings or a silent blind spot.
    - **`scope-gap` is retired — do not reintroduce it.** It existed to absorb pages the index generator was assumed never to visit, at Low severity, on the theory that their absence was a depth decision rather than drift. With the scope above there is no such category: a crawled page missing from the index is a `missing-entry`, Medium, whatever its depth. Keeping both leaves two rules answering one question with different severities, and the quieter one wins by being quieter — the exact shape that let deep-page drift go unreported.
    - **Stale description** (`subcategory: stale-description`) — index description no longer matches the page's current `**At a glance:**` opener (best-effort string compare; ignore minor whitespace). Severity Low.
    - **Stale regeneration** (`subcategory: stale-regeneration`) — `Last regenerated` timestamp on the index is older than 30 days. Severity Low.
   Each variant sets its own `subcategory` — they share a page and a category, so without it Step 5's de-dupe collapses four distinct problems with different fixes into one finding.

   Suggested fix for any of these: re-run `harvest-knowledge` (which regenerates `Guide/Index` end-to-end). For one-off corrections, a targeted `notion-update-page` is acceptable but the next harvest run will overwrite anyway.

10. **Placement violations** — every L4 page must sit under one of the top-level sections per `Guide/Placement Rules`. The section set is **whatever Step 2 resolved from About** — never a hardcoded list, since the taxonomy can gain a section (today it resolves ten: Compliance, Domain, Guide, Identity, Learnings, Procedures, Products, Signals, Team, Tools). Walk the page tree captured in Step 4 and flag any page whose `parent` resolves to:
    - The workspace root, the AI Knowledge teamspace root, or any node outside the resolved section set. **Severity High.** Note that **About is the router page, not a section** — it is where the sections are linked from, so it is never a valid parent and never itself a finding. Suggested fix: move under the most specific applicable subsection per `Guide/Placement Rules`; if none exists, propose creating one in the same review loop. Skip the `About` landing page itself — it is the section root by design.

      **This arm cannot be decided from a page's `parent` alone, and a run that reports it as clean is claiming more than it checked.** The REST page object has no teamspace field: `parent` reads `{"type": "workspace", "workspace": true}` identically for all nine section roots, for `About`, and for every top-level page of every *other* teamspace the integration can see. A `/search` sweep on 2026-09-03 returned 13,784 pages outside the tree walk, of which 4 had a workspace parent — all four other-teamspace content (`Recommendations / Search`, `Weekly OKR Status (06-03-2026)`, a price-increase rollout plan, an Aurie Web onboarding dump). Nothing in the payload separates those from a genuinely rootless AI Knowledge page.

      So run it as a **triage step, never as a pass/fail check**. Subtract the known-good set — the section ids Step 2 resolved, plus `About` — from the workspace-parent pages `/search` returns, and report the remaining count under Detector status as *undecided, N candidates for human triage*, listing their titles. A subtree that links into the teamspace **promotes** a candidate to a High finding; its absence clears nothing, because a genuinely rootless AI Knowledge page — precisely what this arm exists to catch — is by definition not reachable from the About-rooted crawl and so cannot link into it. Treating the link check as two-sided is what turns "only other-teamspace roots matched" into a zero-finding result, which is the false clean this whole note warns about.

      **The candidate set is only meaningful if the sweep that produced it was unfiltered.** Enumerate with a bare `/search` over `object: "page"`, paginated to exhaustion, and apply **no** teamspace filter. This is the one place in the skill where the `root_page_of` ancestry post-filter that Step 4's crawl depends on must *not* be reused: it keeps only pages already under a resolved section, so the leftover set is empty by construction. Walking the Step 4 tree instead has the same effect for the same reason. Either mistake produces a confident zero while never having looked for a rootless page.

      Sanity-check the sweep before reading anything into its result: it must return **far more** pages than the crawl, because it spans the whole workspace the integration can see (13,784 against 169 in scope on 2026-09-03). A total close to the crawl size means the sweep was filtered — report the arm as not run.

      **This arm therefore has no clean state.** Zero *promoted* findings, zero *candidates from a verified-unfiltered sweep*, and *not run* are three different results. Only the second rules anything out, and only once the sanity check above has passed. Write whichever one holds; never collapse them into a tick.
    - A section root when a more specific subsection clearly exists (e.g. a UX teardown filed under `Learnings/` directly when `Learnings/UX Priors` exists; an experiment writeup at `Learnings/` instead of in the `Learnings/Experiments` DB). **Severity Medium.** Suggested fix: move into the matching subsection or DB row.
    **Heuristic for the section-root check.** All three gates must pass before the page is a finding. A single shared title token is not evidence of misplacement — sibling pages in one section routinely share a product name or a section word, so overlap on one token alone flags correctly-placed pages far more often than misfiled ones.

    1. **Parent is the section page** (Identity, Domain, …), not a subsection.
    2. **The candidate has no children of its own** (`child_count == 0` from Step 4). A page with children is a subsection, not a misfiled leaf — it is where other pages were deliberately filed. This gate alone resolves the `Signals/Aurie Conversations` ↔ `Signals/Aurie Weekly Cost` pair: both are snapshot parents with 3 and 8 dated children.
    3. **A sibling sub-page or database is a strong match** — see below.

    Strong match means, after tokenising both titles — lowercase, split on non-alphanumerics, **stem before stoplisting** (strip a trailing plural `s` on tokens longer than 3 chars, then drop stoplist members) — **either**:

    - **≥ 2 tokens overlap between the two titles**, or
    - **≥ 2 of the candidate's tokens appear in the sibling's `**At a glance:**` opener.** This second path is what catches the real case, where the misfiled page's subject matter is described by the sibling but not repeated in its title.

    Anything weaker is **not a finding at all** — do not downgrade it to Low. Low-severity noise trains the reader to skim the Low section, which is exactly where genuine placement drift will surface.

    **Stoplist.** Three classes of token carry no placement signal because they are *expected* to recur across siblings in the same section:

    | Class | Tokens |
    |---|---|
    | Brand / product | `aura`, `aurie`, `google`, `openai`, `anthropic`, `claude`, `gemini`, `slack`, `notion`, `linear`, `mixpanel`, `braintrust`, `elevenlabs`, `11labs`, `livekit`, `cartesia`, `deepgram`, `bugsnag`, `circleci`, `gcp`, `bigquery`, `firebase`, `github`, `postman`, `apple` |
    | Section names | `identity`, `domain`, `procedures`, `learnings`, `tools`, `team`, `signals`, `guide`, `compliance`, `about` |
    | Generic / structural | `the`, `a`, `an`, `of`, `and`, `for`, `to`, `in`, `on`, `vs`, `api`, `app`, `page`, `note`, `overview`, `index`, `data`, `report`, `daily`, `weekly`, `monthly`, `new`, `our` |

    Stemming runs **before** the stoplist so every entry is listed in its singular form only — `report` covers `reports`, `app` covers `apps`. Listing a plural as well is harmless but redundant; listing *only* a plural (or a `foo(s)` shorthand) silently fails to match anything.

    Extend the brand class when a new vendor page family lands in `Tools/` — a vendor name shared by two `Tools/` siblings is a naming convention, not evidence of misplacement.

    **Worked cases.** Each row states how the gates resolve a real pair in the teamspace:

    | Candidate | Sibling | Result |
    |---|---|---|
    | `Signals/Aurie Conversations` (3 children) | `Signals/Aurie Weekly Cost` | no finding — gate 2 |
    | `Signals/Aurie Weekly Cost` (8 children) | `Signals/Aurie Conversations` | no finding — gate 2 |
    | `Tools/Google Ads` (0 children) | `Tools/Google Sheets` | no finding — `google` is a brand token, leaving `{ads}` ∩ `{sheets}` = ∅ |
    | `Tools/Google Sheets` (0 children) | `Tools/Google Ads` | no finding — same, reversed |
    | `Learnings/UX Teardown - Calm Onboarding` (0 children) | `Learnings/UX Priors` | **fires** — titles share only `ux`, but the sibling's opener ("Durable UX priors distilled from onboarding teardowns…") matches `ux`, `onboarding`, `teardown` |

    **Exceptions:** section index pages themselves (the section roots) are never findings — they're meant to live there.

11. **Broken internal links** — scan for wiki-link syntax `[[Some/Path]]`, **skipping any marker presented as code**. Notion stores that two different ways and the detector has to handle both, so scan `blocks[].rich_text` **runs**, not the flattened `body`:

    ```python
    def is_example(run, full_text, m):          # m = the [[...]] match in full_text
        if run.get("annotations", {}).get("code"):        # a real code span: backticks are gone
            return True
        return (full_text[m.start()-1:m.start()] == "`"   # literal backticks kept as characters
                and full_text[m.end():m.end()+1] == "`")
    ```

    `body` is the runs joined in document order, so a **code-annotated** span reaches it with its backticks already stripped — testing `body` for a backtick character sees nothing and the example is flagged anyway. The reverse case is just as real: on `Tools/Notion` the markers are plain runs (`annotations.code == false`) whose backticks survive as literal characters, so an annotation-only test misses them. Neither check alone is sufficient. Notion does not render `[[...]]` as a hyperlink — the text appears literal, the target page is left orphan (no real backlink), and downstream readers can't navigate. Severity Medium. Suggested fix: replace each `[[Path]]` marker with a real Notion page link via `rich_text` with `text.link.url` set to the target page URL. **When fixing, also verify the target's `archived` state via `notion-fetch` before linking** — if the target is archived, the resolved link points at a hidden page (still works but appears struck-through). In that case, prompt the user before linking; they may want to un-archive instead. (Failure mode + fix flow observed and codified 2026-06-05 on Tools/OpenAI ↔ Procedures/Engineering/Cost Optimization, which was archived between harvest and healthcheck.)

    **A backtick-wrapped marker is a documentation example, not a broken link.** `Tools/Notion` carries a section titled *"Wiki-link syntax `[[Path]]` doesn't render in Notion"* whose four markers are this rule's own worked examples; the genuine findings on the same run carried none. Without the exclusion the detector fires on the page explaining the failure mode, and the only way to silence it is to stop documenting the syntax — so the exclusion removes the false positive rather than managing it. This is the same self-documentation trap detector #8 warns about for its text arm and #12 for `create-aura-skill`'s indicator table: **any rule stated in prose will match itself.**

    **When the target no longer exists, do not invent a replacement link.** Check whether the page was renamed or superseded, and repoint only when a successor genuinely covers the same content; otherwise drop the pointer and keep the sentence. Re-read the surrounding sentence while you are there — a dangling wiki-link is usually old enough that the prose around it has drifted too. On 2026-09-04 `Tools/ElevenLabs` pointed at a deleted `Procedures/Engineering/Cost Optimization` *and* cited "Lever 8" from it, while the successor page's table has five levers with Cartesia at number 1; fixing only the link would have left a confident wrong number behind.

12. **Skill ↔ `Tools/Slack` declaration gap** — scan every `SKILL.md` under `plugins/` (stripping the YAML frontmatter first, otherwise the detector flags every existing `Tools/Slack` declarer on its own comment) for Slack-write indicators and verify the file declares `Tools/Slack` in `knowledge.reads`. Indicators (any one triggers): `chat.postMessage` / `chat_postMessage` / `slack_send_message` calls, `SLACK_BOT_TOKEN` references, hardcoded channel IDs (regex `\b[CG][0-9A-Z]{8,12}\b` **with at least one digit** — e.g. `re.search(r'\d', m)` on each match, or `\b[CG](?=[0-9A-Z]{8,12}\b)(?=[A-Z0-9]*\d)[0-9A-Z]{8,12}\b`), or prose like "post to #channel" / "Slack reply" / "post a Slack summary" / "threaded reply". A skill that matches any indicator but doesn't declare is a finding. **Severity Medium.**

    **Three guards, each measured against a real false positive.** Run against the live repo on 2026-09-20 the indicator list alone returned **5 candidates and only 1 was real** — a 20% precision that would get the detector ignored, which is the failure mode a noisy gate always reaches.

    - **The channel-id regex matches UUID segments.** `\b[CG][0-9A-Z]{8,12}\b` has a word boundary at a hyphen, so `619D571B-7328-47DF-89CC-C66F70C35540` yields `C66F70C35540` — twelve chars, starts with `C`, contains digits, passes every stated condition. `eng:investigate-gcp-logs` was flagged on a **transaction-id example in an error message**. Reject a match whose surrounding token is a UUID: require that the character before the boundary is not `-`, or discard any match inside a `[0-9A-F]{8}-[0-9A-F]{4}-` run.
    - **A token inventory is not a Slack write.** `shared:setup-ainative` names `SLACK_BOT_TOKEN` in a shell loop that checks *whether keys are set*. An env-var name appearing in a list of env-var names is a mention, not a call. Require the token to appear near a request, not inside a whitespace-delimited inventory.
    - **A skill that documents the rule matches the rule.** `shared:create-aura-skill` carries the marker→path table that tells authors to declare `Tools/Slack` when they see `chat.postMessage` — so the table trips the detector on its own instructions. Stripping the frontmatter (already required above) does not cover this; the body needs it too. Skip fenced code and markdown tables whose cells are the marker list, the same self-documentation trap #8 and #11 carry.

    **And prose alone is the weakest indicator.** `data:analyze-user-behavior` matched `Slack reply` in a sentence about where its *output might be pasted* — the skill ships no posting mechanism at all (`grep -rn "chat.postMessage\|SLACK_BOT_TOKEN" ` over its folder returns nothing). Treat the prose pattern as corroborating, never as sufficient on its own: a skill with a prose hit and no call and no token is not a finding.

    The one true positive that survived all four guards was [`product:build-idea`](../../../../product/skills/build-idea/SKILL.md) — it bundles `slack_post.py` and its `finish --post` path replies in the idea's Slack thread with uploads, while declaring only `Tools/Notion` and `Tools/Linear`.

    **The digit requirement on the channel-id arm is load-bearing.** A real Slack channel id always mixes
    digits and letters (`C05PC18APLH`), while the letters-only form of that pattern matches any 9-13
    character all-caps word — `CHANGELOG`, `COMPLETION`, `CANCELLED`, `CORRECTLY`, `COLLAPSED`, `CLAUDECODE`
    are all real hits from `plugins/`. Without it the arm reports 8 false positives for every true one, and
    a detector that is wrong 8 times out of 9 gets skimmed rather than read — which costs the one finding it
    exists to surface.

    **Verify every hit against the matched text before reporting it.** Two of the remaining indicators are
    self-matching by construction: `shared:create-aura-skill` carries the dependency→`Tools/*` mapping table
    (a row literally listing `SLACK_BOT_TOKEN`, `chat.postMessage`) and `shared:setup-ainative` carries the
    env-var inventory. Both name the indicator without doing the thing. Print the matched substring per hit
    so a reviewer can dismiss these in one pass rather than re-deriving them each run. Suggested fix: add `- Tools/Slack  # channel directory + Post format footer spec` to the skill's `knowledge.reads` block. Convention introduced in [PROD-2473](https://linear.app/aura-health/issue/PROD-2473) — once declared, the canonical footer spec on the `Tools/Slack` page is pre-fetched via `retrieve-knowledge` before the skill runs, so authors can render the standard attribution footer on every Slack post without restating it in the SKILL.md body.

    Sub-check (Severity Low): for skills that DO declare `Tools/Slack`, check that the body references the attribution footer in one of the accepted forms — a `_Generated by ` literal, **or** any deterministic path that hands the id to the helper rather than typing the line: `--footer <id>` on a `slack_post.py` / `shared:post-to-slack` call, `slack_post.py footer --skill <id>`, **or** a `footer=<id>` parameter on a composed `shared:post-to-slack` invocation (preferred as of [PROD-3636](https://linear.app/aura-health/issue/PROD-3636); a model-typed footer literal silently drops the `:<version>`).

    **`footer=<id>` is the form an agent-driven skill uses, and omitting it from the accepted list inverts the sub-check.** A skill that *invokes* `shared:post-to-slack` passes named parameters in prose; only a skill that *shells out* writes `--footer`. Matching just the flag therefore flags the composed path — the one this convention asks for — while a skill that hand-types the footer literal passes on the `_Generated by ` arm. Measured on 2026-09-03: 1 of 2 footer-gap findings was `data:run-rating-feedback-analysis-cadence`, which passes `footer=data:investigate-user-rating` and is fully compliant. A declaration with none of these is a templating gap — the spec is pre-fetched but the skill doesn't actually use it. Suggested fix: route the skill's Slack post through `--footer <id>`. **Exception:** skills whose YAML uses `Tools/Slack` purely for channel-ID resolution (no actual post step in the body) — manually verify before fixing.

    Sub-check (Severity Medium): flag a skill whose body calls a Slack **primitive directly** — `slack_send_message` / `slack_read_thread` / `slack_search_public` / `slack_read_channel` / `slack_send_message_draft`, or prose telling the runner to call `chat.postMessage` / "the Slack MCP" directly — **instead of composing** `shared:post-to-slack` / `shared:read-slack`. Since PROD-2756 / PROD-2764 those two are the canonical way every skill connects to Slack: agent-driven skills *invoke* them, deterministic in-script loops *import* the byte-identical `slack_post.py` / `slack_read.py` helper copy. **Exception:** a skill that ships a `scripts/slack_post.py` or `scripts/slack_read.py` copy and uses it in-process — a `from slack_post import …` / `from slack_read import …` in its scripts is the signal — is compliant; don't flag it. Suggested fix: convert the primitive call to invoke `shared:post-to-slack` / `shared:read-slack` (pattern in PROD-2771 / PROD-2775).

13. **Repeated workaround note** — the same workaround sentence appears in **two or more different rows** of a knowledge database. Deterministic, and implemented rather than described: [`scripts/workaround_notes.py`](../scripts/workaround_notes.py). Severity **Medium** at two rows, **High** at three or more.

    **What it is for.** A finding about *how a tool behaves*, written into the output that tool produced, protects that one row and nothing else — CLAUDE.md, *A finding about the tool belongs in the tool, not in the output*. The pathology has a written signature: the note is copy-pasted into the next row, and the next, until it reads as a house convention rather than an open defect. `Learnings/Experiments` carried one word for word in 13 rows for months while every other guard in this repo stayed green, because none of them reads a row body ([PROD-6225](https://linear.app/aura-health/issue/PROD-6225) / [PROD-6175](https://linear.app/aura-health/issue/PROD-6175)).

    **Corpus: database rows only, not pages.** Scope it to `Learnings/*` and `Signals/*` **table** rows, where each row is an independent analysis and a shared sentence means one was copied into the next. Section pages legitimately share boilerplate; running this over them reports the boilerplate. Exclude `code` blocks from the row text — a marker inside a code sample is an example, the same self-documentation trap #8, #11 and #12 carry.

    **The 20-row sample cap in Step 4 does not apply here, and an empty result under it is not a clean result.** `Learnings/Experiments` holds **3,087 rows**; a sampled crawl of 20 could not have seen a 13-row cluster even in principle. So the detector takes its own corpus with a declared `scanned` / `total`, and reports **PARTIAL** whenever `scanned < total`. A bounded pre-filter is fine — rows edited since the last run, or rows in a terminal `Status` — but the bound is *reported*, never silently folded into a clean-looking zero. Same rule as *Tag, don't drop, wherever the output persists*.

    **Repetition is the signal, not the wording.** `MARKERS` only nominates a sentence for clustering; a nominated sentence that appears once is never reported. So a loose marker list costs nothing and a missing marker costs the whole cluster — widen it freely, tighten it only with an argument. On the real corpus the loose list nominated sentences in 25 rows and reported 2 clusters; the single-row nominations included `"the raw cut is trustworthy"`, which is the *opposite* claim and never clusters.

    **A row repeating itself is not a cluster** — the copy-forward pathology is *between* rows, and an analysis restating its own caveat under two headings is ordinary writing. Count distinct rows.

    **The finding's key has to survive the cluster growing, and both halves of it are built for that.** Step 5 de-dupes and Step 3 reconciles on `(page_url, category, subcategory)` — so anything in the key that moves when a row joins loses the prior run's decision, silently. A 2-row cluster rejected in one run has to come back as High once it reaches three (Step 5 re-surfaces a rejected finding whose severity escalated), and it can only do that if the key held. Two things would otherwise move it, and neither is a fact about the defect:

    - **The anchor is the database, not a member row.** The 14th row to carry a note may sort before all thirteen, so a row anchor moves exactly when the cluster grows. No row is "the" row anyway — the defect is in the mechanism that wrote every row in the database. The corpus therefore declares `database: {path, url}`; a caller that omits it gets a row anchor and `key_stable: false` on every finding, which is the honest degrade rather than a stable-looking key that isn't.
    - **`subcategory` hashes the *dominant* spelling, not the first-seen one.** Arrival order is a property of the scan — a corpus built from a different subset (rows edited since the last run, say) hands the same cluster a different first member. The representative is the spelling most rows carry, ties broken lexicographically.

    With the database as the anchor, `subcategory` is the *whole* of what separates two findings in one database — so it carrying the note, rather than being left unset as #8's `sources`/`prose` split suggests, is load-bearing.

    **A note that links its card is acquitted, and that is the only way this finding closes.** What the detector looks for is a mechanism-level finding recorded *only* in the output; once the note carries a `linear.app` link the defect is filed and the next reader of the row can follow it, so the row drops out of the cluster. Without that, the detector is **uncloseable**: its own fix leaves the notes in place, so the cluster still fires after the card is filed, and Step 5 reads a still-firing `Approved + Fixed` finding as a **regression** and re-raises it at full severity on every later run. A High finding nobody can close is a High finding nobody reads — the same argument #12's digit requirement makes about noise.

    Three boundaries on the acquittal, each erring toward *still reporting*, because the reviewer closes a false positive by adding the link that was asked for anyway:

    - **The link is read from the block's `hrefs`, not its visible text** — and this arm is the whole mechanism, not a refinement. A Notion link shows human-readable text and carries the URL in `rich_text[].href`, so a filed card reads as a bare `PROD-1234` on the page. Measured on the real `Learnings/Experiments` corpus: **12 card links, every one in an href, none in visible text** — the same trap detector #8 documents at ~97% teamspace-wide. An acquittal that scans rendered text cannot fire on a single production row, so the close path would look implemented and never work.
    - **It is judged on the note's own block**, not the row. A card linked three sections away does not help the reader of the note.
    - **A bare `PROD-1234` with no href does not acquit.** The rule is that the note *links* the card; a bare id is also indistinguishable from `GPT-4` or `nova-3` under any pattern loose enough to catch every team prefix.
    - **Acquitted rows are counted, never silently dropped** (`linked_rows` on the finding, and a line in the rendered report). An acquittal nobody totals is just a quieter drop — *Tag, don't drop, wherever the output persists*.

    **So the corpus is rows of blocks carrying their hrefs**, which is what Step 4 already captures (`rich_text[].href`, page mentions, `link_to_page`). Flat `text` is still accepted — one block per line — but it **cannot acquit**, and a corpus in that shape reports `acquittal: BLIND` on the run and `acquittal_blind: true` on every finding. Judged on the corpus *shape*, not on whether an href happened to be present: a caller passing blocks is href-aware even over rows that link nothing, while a caller passing flat text can never acquit whatever the rows say. Without the flag, a detector whose close path silently cannot fire renders identically to one whose rows are all genuinely unlinked — the same failure the PARTIAL rule exists to prevent, one level down.

    **Suggested fix.** File a card against the mechanism the note describes, fix it there, and add the card's Linear link to the note in every row the finding lists. **Leave the notes** — each is a true statement about that analysis. What they must not stay is the only place the defect is recorded.

    Pinned by [`tests/test_workaround_notes.py`](../../../../../tests/test_workaround_notes.py), whose load-bearing case runs the detector over a corpus fetched from the live database on 2026-09-19 and asserts the 13-row cluster comes back. A detector for a known cluster that cannot find that cluster is worth nothing, and a synthetic fixture cannot make that assertion.

14. **Dead declared knowledge path** — a skill or agent declares a `knowledge.reads` / `knowledge.writes` path that resolves nowhere in AI Knowledge. Deterministic, and implemented rather than described: [`scripts/declared_paths.py`](../scripts/declared_paths.py). Severity **High**.

    **What it is for.** `CLAUDE.md` → *Knowledge manifest* → *What it unlocks* has promised this row since the manifest shipped — "`healthcheck-knowledge` flags skills whose declared paths no longer exist in Notion" — and nothing implemented it. Detector #3 greps skills to decide whether a **page** is orphaned, which is the reverse direction and cannot answer this. So a declaration pointing at a moved or deleted page was caught by nothing: not the version gate, not `check-skill-length.py`, not the `tests/` suite (offline by design, so it cannot reach Notion), not this healthcheck. Two were found by hand on 2026-09-19 — a moved `Tools/Google Sheets (API access)` and a `Procedures/AI Native/Features Loop` entry sitting in `Guide/Index` as plain text with no link — and the first run of this detector found four more ([ENG-1353](https://linear.app/aura-health/issue/ENG-1353) / [ENG-1351](https://linear.app/aura-health/issue/ENG-1351)).

    **Why High.** A dead read fails *open*. `retrieve-knowledge` resolves nothing, returns nothing, and the skill runs on less context than it declared — no exception, no warning, output that looks ordinary. Same shape as the stale-install incident in *Plugin versioning*: the reload reports success. A wrong value is loud; a missing one has no shape.

    **A path is dead only when BOTH routes miss.** Resolve it the way `retrieve-knowledge` Step 2 does — the `Guide/Index` lookup **and** the About → section → children walk. Either alone produces confident false positives in a different direction: the index lags every page created between harvest runs (sibling skills write pages, and people create them by hand), while a walk truncated by a depth cap or a transient failure drops live subtrees. The index acquits what the walk missed and the walk acquits what the index has not caught up with.

    **Corpus: every manifest, skills and agents alike.** `plugins/*/skills/*/SKILL.md` and `plugins/*/agents/*.md` — Principle 4 puts agents under the same rule. Skip `"*"` (a declaration of unrestricted scope, not a claim about a page) and the universal `Guide/Index` baseline. Parse the **frontmatter slice only**: several skills document the convention by example in their body, and a whole-file scan reads the wrong block — the same trap *Skill versioning* names for version bumps.

    **Name a successor only when it is one.** A page move leaves the leaf title intact at a new path, which is exactly the `Tools/Google Sheets (API access)` → `Tools/Google Sheets/Google Sheets (API access)` shape, so a **unique** leaf match across the tree is a safe repoint to propose. Anything else — no match, or several — reports *no successor found* rather than guessing. On the run that found it, `Tools/ElevenLabs` was really `Tools/11Labs` — a different leaf title, which no leaf match reaches; proposing the nearest-looking page there would be a confident wrong answer of the kind *Verify the path production takes* warns about. (The page has since been renamed to `ElevenLabs` and every declared path repointed, so the example is historical — deliberately kept in its original spelling, because a blanket rewrite of it destroys the contrast the sentence is making.)

    **`subcategory` is the dead path**, so Step 5 de-dupes per path rather than per declarer: one finding names every manifest that declared it, and the key survives a second skill picking up the same dead spelling.

    **Suggested fix.** Repoint, then **grep every spelling before declaring the sweep complete** — the slash form, the `›` breadcrumb form the knowledge skills write, and prose. The PROD-6205 sweep matched `Identity/Apps` and missed `Identity › Apps`, leaving `ingest-knowledge` ready to recreate the retired subtree on its next run.

    Pinned by [`tests/test_declared_paths.py`](../../../../../tests/test_declared_paths.py), whose load-bearing case runs the detector over the real manifests and a tree fetched from the live teamspace on 2026-09-19 and asserts the four dead paths that first run found come back. `TestResolutionNeedsBothRoutes` fails an index-only or walk-only resolver directly.

Each finding is shaped:

```
{ severity, category, subcategory, page_title, page_url, description, suggested_fix, related_pages[] }
```

`subcategory` distinguishes variants of one detector whose fixes differ in kind, so they survive Step 5's de-dupe as separate findings. Today only detector #8 uses it (`sources` vs `prose`); leave it unset elsewhere.
