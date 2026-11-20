# tests

Repo-level Python tests. Run them with:

```bash
python3 -m unittest discover -s tests -v
```

No API keys, no network, no `pytest`. Stdlib `unittest` only, plus whatever the
module under test already imports (`requests`, for its import to resolve — the
tests never let a request out).

**That is enforced, not trusted** ([ENG-1746](https://linear.app/aura-health/issue/ENG-1746)).
[`live_guard.py`](live_guard.py) is installed by `test_000_live_guard.py`, which sorts first, so it is in
place before any other module loads. It removes every secret-looking environment variable, points gcloud
at an empty config, shims `gcloud`, `gsutil`, `bq`, `curl`, `wget`, `firebase`, `kubectl` and `whois` to
fail with exit 97 (child processes included), and refuses any Python socket that isn't loopback. It
exists because a test whose "today" was the host's date, not UTC, let `review-ip-blocks`' `extend --apply`
call a live `gcloud ... security-policies rules update` on prod Cloud Armor; only a missing IAM
permission stopped it. A module that can reach a live write when run on its own calls
`live_guard.install()` itself as well.

## The two decisions this directory encodes

**1. Tests live here, not inside the skill folder.**

Skills are self-contained at *runtime* (Architecture principle 1) — a skill
folder ships everything it needs to run. Tests are not part of running a skill:
nothing a skill does at runtime reads them, and shipping them inside the plugin
would push test fixtures into every install for no benefit.

The deciding factor is the duplication. `responses_reader.py` exists in six skill
directories. Tests inside each folder would mean six copies of the suite, and a
copy of a *test* drifting is worse than a copy of the code drifting — the drift
is invisible until the day the weaker copy is the one you trusted. One suite that
**runs against every copy** inverts that: a behavioural divergence between copies
fails the suite, so these tests double as a drift check that a byte-diff can't
replace (a copy can be byte-identical to the canonical and still be the wrong
canonical).

`tests/` is outside `plugins/`, so it is not subject to the version-bump gate.

**2. CI runs them, and the gate blocks merges.**

`.github/workflows/tests.yml` runs on PRs touching a tested file or the tests
themselves. It is a required check like `Version bump`. A gate that doesn't block
is a notification, and this repo already has evidence that notifications get
merged past: four consecutive PRs shipped real defects that hand-verification
signed off on.

The `paths` filter is deliberately an allowlist of what's actually covered.
Anything not listed is ungated — that's honest rather than implying coverage the
suite doesn't have. Widen it as coverage grows.

## What is covered

| File | Covers |
|---|---|
| `test_responses_reader.py` | The ledger→OpenAI read path: happy path, transcript-source fallback, unfetchable accounting, `input_items` failure marking, pagination past the 100-item cap, `input_items_for` modes, prompter filtering, case-insensitive conversation grouping, ledger token precedence |
| `test_fetch_logs.py` | `_is_conversation_prompter` — the completions-arm predicate that keeps side-effect records out of a user-pivot walk |
| `test_mixpanel_api.py` | The Export window's clamp to the **project** calendar: an out-of-range `to_date` narrows instead of raising, both ends move so a single-day window stays valid, in-range windows are untouched, `find_events_by_uid` inherits it, and the timezone resolution ladder (`/api/app/me` → env override → default → UTC) |
| `test_mixpanel_engage_ip0.py` | Every raw write to Mixpanel's ingestion endpoint `api.mixpanel.com/engage` under `plugins/` carries `ip=0` (query string) or `$ip` (payload), so a sender's IP can never become a profile's geolocation. The detector is checked against a fixture in the shape of the 2026-09-11 backfill that stamped 2,063,384 profiles West Vancouver (PROD-6322), and the query API (`mixpanel.com/api/query/engage`) is confirmed out of scope as a read |
| `test_gcloud_sa.py` | Every copy of `gcloud_sa.py` (glob-discovered, byte-identical to the canonical) runs gcloud as the `GCP_KEY` service account in a private `CLOUDSDK_CONFIG`, activates once per process (also under parallel callers), removes the key file after activation, and uses the user login only when `GCP_KEY` is unset. The load-bearing assertions are the negative ones: a key that fails to activate, or will not parse, runs nothing on the user config (ENG-1607) |
| `test_gcloud_gate.py` | No `gcloud` call under `plugins/` bypasses the skill's own `gcloud_sa.py`. Scripts and SKILL.md commands are both scanned, and a person-only setup block opts out with `# user-gcloud:`. Every helper reference points at a copy in the same skill. The detector is checked against fixtures, including the `fetch_conversation.py` line that turned 15 guardrail triage replies' handoff categories into `unknown` on 2026-10-20 (ENG-1607) |
| `test_workaround_notes.py` | `healthcheck-knowledge`'s detector #13 — repeated workaround notes across knowledge database rows. The load-bearing case runs the detector over a corpus fetched from the live `Learnings/Experiments` database and asserts the real 13-row cluster comes back; a detector for a known cluster that can't find that cluster is worth nothing, and a synthetic fixture can't make that assertion. Plus: a row repeating itself is not a cluster, two rows are Medium and three are High, copy-paste drift and markdown differences don't split a cluster, unrelated notes don't merge, the `(page_url, subcategory)` key survives the cluster growing (which is what lets a dismissed 2-row cluster come back once it escalates), the anchor is order-independent, and an empty result on a partial scan is never rendered as clean |
| `test_notion_rich_text.py` | `rich_text()` inline-markdown parsing: code spans become `code` annotations, bold and link nest in both directions (`**[l](u)**`, `[**l**](u)`, `` [`c`](u) ``), code spans stay leaves so `**` inside them is literal, unbalanced backticks pass through, and — the load-bearing negative assertion — no run's text ever still contains raw markdown |
| `test_scan_node_versions.py` | `eng:update-nodejs`'s archived-repo handling: remote-URL → `owner/repo` parsing, the tri-state `gh repo view --json isArchived` result, and every way the lookup can fail to answer (no `gh`, non-zero exit, unparseable or `null` response, non-GitHub origin) resolving to **unknown** rather than live-or-archived; archived repos excluded from `--apply`, excluded from the dominant-version vote and the drift list, and bucketed by `--expect`; unknowns written and counted as drift (fail-open on write) |
| `test_days_to_cancel.py` | `fetch_cancellations.py`'s `days_to_cancel`: the k8bot `Purchased` string is a **US Pacific wall clock** and the cancel instant is a UTC epoch, so a raw subtraction added the whole offset (a 30-second in-flow cancel read as 0.29 d). Covers a same-minute cancel, a genuine multi-day one, both DST boundaries and a cancel spanning the fall-back, the date-only `Purchased` form, `purchased_utc`, and the tzdata-less `_pacific_offset` fallback asserted against `zoneinfo` |
| `test_attribution_materiality.py` | Attribution **source**: `completions-fallback` (an outage) vs `completions-non-ledger` (a project the ledger never serves) reported separately so the note stops claiming an outage that didn't happen, dollars-per-class, and `store.py` refusing on the degraded *share* rather than a marker string |
| `test_attribution_coverage.py` | Attribution **outcome** — the independent failure `materiality` cannot see: a walk that used the right source and resolved nothing. Coverage ratio and its exclusions (skipped dev/internal projects), the clause reaching a *pre-epoch* note, `store.py` refusing a collapsed day, and the trailing-median user-count guard that catches a *partial* collapse coverage reads as healthy |
| `test_voice_attribution_coverage.py` | The **voice** half of the same guard ([ENG-1134]) — the blind spot ENG-1120 left, since all three of its guards read the LLM walk and the decisive one counts users with *LLM* cost. Per-vendor-path coverage, an empty LiveKit minute map collapsing all four minute-allocated vendors while their billing APIs still return real totals, the money-with-no-owner test that keeps a genuinely quiet day (and Deepgram's legitimate $0) passing, `voice_11labs_intelligence` held out of the denominator as unattributable *by design*, a dead path still reported when a large healthy path carries the blend over the threshold, and the trailing-median voice user-count guard keyed on `voice_cost` rather than `llm_cost` |
| `test_ledger_reader.py` | `data:report-aurie-conversations`'s window-wide `llmCall` ledger walk: keyset pagination past the 1000-row cap with the inclusive `to` boundary deduped on `id`, the no-progress guard (more rows in one millisecond than the page cap must terminate, not spin), a raising ledger surfacing as an error rather than zero conversations, a missing token, `coverage_start` on covered / partway-covered / entirely-uncovered windows, and coverage reported as a per-API **share** — including a surface present in `/stats` but absent from the walk. Plus the report's own aggregation semantics: the per-metric dedup split (one thread across both surfaces is one conversation but two calls), duration spans crossing surfaces, mixed-case `conversationId` folding, unknown prompters still reaching the table, and future-window clamping |
| `test_is_prompter_names.py` | Every IntelligenceService stored prompter name (`name`, `legacyNames`, arm `name`) in the checked-in snapshot `fixtures/is_prompter_names.json` is classified exactly once: `CONVERSATION_PROMPTERS` in every `responses_reader.py` copy, `JOURNAL_PROMPTERS`, or the test's not-a-turn list. Plus report-aurie-conversations `CONVERSATION_TYPES` covering each conversation name, the qa:chat-with-aurie and qa:chat-with-aura `--prompter` choices equal to IS `conversation.allowedTypes`, and `scripts/refresh-is-prompter-names.py` run against a small IS-shaped repo. CI can't reach IS: refresh the snapshot with that script after IS adds, renames or merges a prompter |
| `test_rating_grain.py` | The `Signals/Conversation Patterns` grain — one row per **rating**, not per thread ([ENG-1115]): `fetch_sweep_analyses.py` emits a distinct `rating_ts` per rating message so two ratings on one conversation stay two rows, a rating with no footer id is still recordable, and the `Slack URL` permalink round-trips the ts (the migration's recovery path); `sample.py` collapses a repeatedly-rated thread to one entry for the qualitative pass, in **both** frames — the Slack fallback spells the thread id `conversation_id` / `journal_id` / `rating_id`, never `source_id`, so a `source_id`-only read would make the collapse a silent no-op there |
| `test_rating_grain.py` | The `Signals/Conversation Patterns` grain — one row per **rating**, not per thread ([ENG-1115]): `fetch_sweep_analyses.py` emits a distinct `rating_ts` per rating message so two ratings on one conversation stay two rows, a rating with no footer id is still recordable, and the `Slack URL` permalink round-trips the ts (the migration's recovery path); `sample.py` collapses a repeatedly-rated thread to one entry for the qualitative pass, in **both** frames — the Slack fallback spells the thread id `conversation_id` / `journal_id` / `rating_id`, never `source_id`, so a `source_id`-only read would make the collapse a silent no-op there; and polarity is emitted per shape with **no pooled key at all**, because a blended rate across two-option and three-option surfaces is invalid ([ENG-1118]) and an absent field cannot be quoted by mistake |
| `test_rating_volume_rule.py` | Rating volume **annotates**, never drops, and the verdict doesn't move with the pass's width ([ENG-1048] / [ENG-1132]). Parameterised over all four `high_volume` annotators: a UID 5 past the threshold keeps every entry, acquires no `test_reason`, and no annotator may re-tighten below 20. Then the harvest writer's rolling-span rule: a burst's verdict is identical inside a 7-day and a 70-day pass, a steady 30-ratings-over-60-days user is **not** flagged (the mechanism behind the 817 dropped ratings), ratings a full span apart never share one, and uid-less entries are absent rather than zero-keyed. Plus the family-wide guard the CLAUDE.md `diff -q` checks structurally cannot be: it greps for the *rule* wherever it lives, so a fifth implementation under a new filename fails the suite instead of going unnoticed — the cancellation parsers' `TEST_BLAST_THRESHOLD` is excluded **by name with a stated reason** (there, volume really is a statement about the account) so an unexplained new hit still fails |
| `test_conversations_row_filter.py` | `data:update-conversations-top10`'s row filter: heading parsing (level-1 `# Sources` is not a canonical section, bullets keep their marker, pre-heading content is dropped), boilerplate matching (templated vertical slots, canned sentences, raw rater quotes, empty markers) and — the load-bearing negatives — a sweep row with every canonical heading is rejected, stays rejected when it grows a stub `## Hypotheses to validate`, and stays rejected when padded past the character floor; plus the byline's reconciliation, that its buckets sum to the total, that a rating-filter drop is named rather than absorbed into the stub count, and that the gate counts describe the whole corpus rather than the filtered slice |
| `test_prompt_testing_cost.py` | `eng:report-prompt-testing-cost`'s derived per-key dollars, both of whose defects render a plausible report rather than raising. **Tier normalisation**: `/usage/completions` says `flex-tier` while `/costs` says `flex | `, so an unnormalised lookup misses and every flex request bills as **$0.00** — the suite asserts the signature directly (non-zero requests, non-zero dollars), pins that an *unknown* tier is preserved rather than folded into `default` (folding prices it at the standard rate, equally invisible), and injects the broken fold to prove the guard bites. **Cached-vs-uncached split**: cached input is ~10x cheaper and the hit rate swings 30%–96%, so the corpus is built where the blended and split answers differ by ~4.8x and the **blended answer is pinned as wrong** — a blended implementation cannot pass. Plus key discovery by name not id (a non-`Claude` key in the same project contributes nothing; a 403 on one project is skipped, not fatal), the window-rate fallback, the ⚠️ unpriced-undercount disclosure, `/api_keys` pagination (a `Claude` key past the first page is a silent undercount — the walk follows `has_more`, terminates without forward progress, and keeps the pages already read when access fails mid-walk), and half-open window resolution including `--last 0` being rejected rather than silently meaning 30 |
| `test_elevenlabs_user_filter.py` | The server-side `user_id` narrowing on `report-elevenlabs-user-cost`'s walk ([ENG-1165]): the param is sent on every page, and — the load-bearing half — a silently-ignored filter degrades to **slow, never wrong**. ElevenLabs answers an unknown query param with 200 and a full unfiltered page, so a rename cannot be detected from the envelope; the per-detail `userId` check is what stops every workspace conversation being attributed to the requested user, and the degradation is reported rather than absorbed |
| `test_elevenlabs_user_filter_rule.py` | The same narrowing asserted as a **rule across implementations**, which a per-filename `diff -q` structurally cannot do ([ENG-1174]). Two skills walk `/v1/convai/conversations` for one user under two filenames, so the family is discovered by *shape* (pages the endpoint) and an undeclared third fails the suite — the seven aggregate/rank-N walkers are excluded **by name with a stated reason** (their question is "across all users", so a uid filter would make them wrong, not faster). Behavioural, with the transport stubbed: the param reaches the wire, a foreign user is never returned even with the filter ignored, a healthy run is not flagged as degraded, and one detail fetch (not a workspace walk) is proof the narrowing held. Plus `investigate-voice-issue`'s **two** budgets, either of which can run out first — the detail-fetch cap and the list page limit, so `exhaustive` has to consider both (server-side `call_start_after_unix`/`_before_unix` are what stop the page budget mattering; without them the walk pages forward from *now* and can reach the limit having examined nothing in range). **Capped ≠ absent** — a search that stops at `--max-detail-fetches` reports "NOT evidence" and is never phrased as "no match", because collapsing the two turned a give-up into a finding and blamed retention for it |
| `test_qa_eval_project.py` | QA/eval skills bill to `Evaluations [Production]`, never a product project ([ENG-1171]). `eng:qa-conversation` ran eval inference through `OPENAI_CONVERSATION_API_KEY` — a **product** project — and since OpenAI bills per project and eval traffic never reaches the `/ai/llm-calls` ledger, `eng:analyze-openai-cost-per-user` divided those dollars across the *real* users ($837 on 2026-08-21; per-user cost $0.06-0.09 → $0.318). Asserts no QA script references any product-project key (`OPENAI_ADMIN_API_KEY` excepted — it reads, never spends) and that every QA script doing OpenAI auth names the eval key. Pins the **destination**, not merely "moved off Conversation": `Evaluations` is the sole member of `INTERNAL_PROJECT_BASE_NAMES`, the only thing routing dollars to `skipped_project_dollars` (excluded from attribution *and* the coverage ratio) — anywhere else is walked, resolves to no user, and counts as uncovered, and at 70% of that day's org spend would have driven coverage under `store.py`'s 75% floor and blocked the whole day's write. So the suite also asserts `Evaluations` stays in the exclusion set and the floor still exists. Discovers scripts by walking the skill folders, so a new script is covered automatically and a rename fails loudly |
| `test_service_tier.py` | Spending OpenAI calls in the QA/eval skills default to the **flex** tier ([ENG-1247]). An omitted `service_tier` bills at standard, so the 2x choice is the one you get by writing nothing — and nothing in the response says so. Discovers spending call sites by walking the QA skills' scripts for the SDK `.create(` and raw `api.openai.com/v1/…` shapes, so a new spender is covered automatically and a rename cannot narrow the set. Asserts every spender states a tier and resolves it through the helper rather than a literal; that the wire values are OpenAI's (`standard` is sent as `default`; the pre-rename `priority` still maps to `fast`) and the flex fallback is `auto`, per their flex guide; that an unknown tier raises instead of falling back to 2x. Pins the two properties that fail silently: `measure_latency.py` refuses to inherit the default, because the tier is the variable it measures; and a refused flex call is never bare — the SDK path retries `auto` with a warning, the urllib path explains the refusal — since a capacity 429 in an error string is indistinguishable from a model that would not answer. The negative cases are load-bearing: an ordinary rate-limit 429 must NOT be treated as a flex refusal, or the retry pays 2x to hit the same wall. Two review findings are pinned here too: the refusal code lives only in the **response body** for `urllib.error.HTTPError`, whose `str()` is the status line alone, so a string-only match never fired for any of the nine `qa-conversation` scripts; and `--service-tier` must default to `None` rather than `flex`, since an unset flag that looks like an explicit choice silently outranks `AINATIVE_OPENAI_SERVICE_TIER` and the documented unattended override does nothing. Also covers the stop-or-standard policy for a tier name that isn't a tier (`AINATIVE_OPENAI_TIER_FALLBACK`): `stop` is the default, since an automatic fallback means one wrong character silently doubles every run's bill; `standard` continues at 1x and must announce it; a bogus policy value is itself refused; and the policy must not weaken the latency exemption, because unstated is not the same as unrecognised |
| `test_livekit_degraded_render.py` | A failed vendor read must never render as `$0.00` ([ENG-1214]). `eng:report-aurie-daily-costs` walked the forwarded LiveKit logs once per day — ~20 Cloud Logging read requests each against a 60/min cap — so day 3 raised 429 and every later day's LiveKit column printed `$0.00` and summed as a zero (a last-7d window on 2026-08-31 understated LiveKit by $34.84 with nothing in the table looking wrong, because Deepgram and LiveKit are LiveKit-path-only and a genuine `$0` day is legitimate). The **negative** assertions are the load-bearing ones — any implementation that blanks every zero passes the positive tests: a quiet day (real `$0`, no note) must still print `$0.00`, a fully-read window must carry no `≥` marker and no incomplete callout, and a persistent quota rejection must **raise** rather than return a short entry list (a truncated walk yields fewer minutes and looks exactly like a quiet day). Plus the store-first read that removes the quota pressure: stored days are not walked, `--livekit-rate-per-min` still applies because *minutes* are read rather than dollars, a day with no stored rate falls back to a walk instead of reporting zero, BigQuery failure degrades to the walk, and both sources failing is UNKNOWN. All seven mutations of the fix are caught, including the over-correction |
| `test_report_deepgram_inference.py` | Deepgram served through **LiveKit Inference** must not render as a well-formed `$0.00`. `eng:report-aurie-daily-costs` read Deepgram from Deepgram's usage API alone, which cannot see Inference STT; on 2026-09-29 the store booked $10.40 past the included credit block while the report printed $0.00 and understated Voice and Combined. Injects a Deepgram API that answers a clean `$0` and asserts stored days never consult it, unstored days are priced through the store's own credit-block helper, and a day whose transport or drawdown is unknown is `—` — with inside-the-block and quiet days staying real `$0.00` as the negative cases. |
| `test_cost_ledger_rule.py` | Nothing calls a cost movement **unexplained** before reading the `Cost Changes` ledger's `Impact` field ([ENG-1365]). A Cycle 45 harvest saw `llm_cost_per_paying_msg` step $0.0208 → $0.0285, wrote "still unexplained" into the `Learnings/Aurie Cost Engineering` headline and filed a card — while both halves sat as rows *on the ledger it was holding open*, mechanism and dollar figure in their `Impact` fields. The sibling `eng:report-aurie-daily-costs` had owned the rule all along, so this is the reimplementation-under-another-name case a filename-keyed `diff -q` cannot see. The family is discovered by shape — a SKILL.md that names the ledger, **or** declares it in `knowledge:` (either axis alone has a hole: the first misses a skill that declares the read then says only "refresh the metrics", the second misses one reporting movements off a ledger a composed skill fetched) — and an undeclared fourth fails the suite. **The load-bearing case is the pre-fix `harvest-cost-changes/SKILL.md`, kept byte-identical as a fixture**: it hits a bare ledger grep 12 times and a bare precedence grep 9 times, so either cheaper marker would have signed off on the defect — what it never contains, at any distance, is a *verdict* token, which is why the marker is ledger + verdict + ordering inside one 400-char window. Pointing satisfies the rule, because CLAUDE.md prefers a pointer to a restatement and a test that only accepted a restatement would push every future composer into duplicating prose — so a minimal deferral must pass while a bare member-table link must not. Discovery is case-sensitive: "cost changes" is also ordinary English, and an over-matching predicate gets narrowed by whoever it inconveniences |
| `test_livekit_latency_metrics.py` | The LiveKit `LATENCY_METRICS` tuple ([ENG-1218]). The canonical named 2 of the 5 per-turn metrics the agent emits while `analyze-livekit-latency` named all 5, so that family's `diff -q` sat permanently red — the state CLAUDE.md warns produces a guard people ignore. The load-bearing assertion is the one the byte-diff **cannot** make: six copies agreeing on a *narrowed* tuple is green to `diff -q` while silently dropping rows from the latency percentile table (no error, no empty section — just a shorter table that still looks complete), so the emitted set is pinned by name. Plus: every copy resolves it identically, the sole consumer keeps deriving its table from the tuple via an AST attribute-read count (a hardcoded metric name is a place a narrowing stops being observable; checked by shape so renaming the import alias doesn't false-trip), and membership is discovered by shape so a new `LATENCY_METRICS` under **any** filename fails the suite — `investigate-voice-issue`'s `correlate.py` is excluded **by path with a stated reason** (ElevenLabs `convai_*` metrics, sharing only the constant name) so an unexplained new hit still fails |
| `test_livekit_core_family.py` | The LiveKit `livekit_core.py` family, discovered by **shape** — the `LIVEKIT_LOG_NAME` anchor plus a `build_sessions` definition — so an undeclared copy under any filename fails, in both directions (a new module that isn't declared, and a declared one discovery can no longer see). `report-livekit-quota` carried an unnamed copy for the skill's whole life while the filename-keyed `diff -q` stayed green. Also: byte-identity across all six copies, with a `KNOWN_DRIFT` mechanism for declaring a divergence per file *and per name* — currently empty, since [ENG-1218] widened the canonical; a declared exception that stops being real fails the suite, so it is removed rather than left as a stale skip. Plus the log-stream anchor asserted both as a constant and as actually scoping `base_filter`, since a wrong stream returns zero entries and raises nothing; and `build_sessions` room-grouping + the `last−first` minutes proxy that quota, cost and the daily-cost store all bill from — including the private-named partial copy `livekit_minutes.py` |
| `test_triage_review_requests.py` | `eng:triage-review-requests`'s Bugbot gate, fed real GraphQL PR nodes captured with the script's own `fetch_pr` and mutated one field per case: a finding counts against head only by `originalCommit` (footer as fallback), never the re-anchored `commit`; older unresolved findings are counted, not failed; a running check-run is never judged; a completed check-run on head counts without a review and the review body is never read. The load-bearing half is `send-back`, which posts as the user: a moved or empty head, a PR that now passes, a running Bugbot and a draft must all refuse before `gh pr review` is reached |
| `test_rtdb_incident_log.py` | `eng:monitor-rtdb-probes`' Security Incident Log planner (`plan-incident-log.js`, run under `node`; skipped without it): sweeps split per IP on a 30-minute gap; a sweep joins its IP's row at any time, or a row of the same network (ASN) and category within 3 h — including across two IPs, in the shape of the mobile-network row whose 24-path curl sweep landed 2 h 53 min after its last line; category mapping (scanner UA, guessed name, root); the Firebase console's data viewer never logged; any other allowed read flagged and never written; re-planning an applied run writes nothing, including once a row's text reaches the 1990-character budget (newest `What they did` lines kept; `IP addresses` never trimmed, a full row opening a new one). Plus `root-attempts.js --probe-detail`: one line per attempt with IP and UA, mode 600, native-platform denials left out, stdout still IP-free. Documentation addresses and ASNs only |
| `test_000_live_guard.py` | The suite-wide live-call guard ([`live_guard.py`](live_guard.py), ENG-1746): installed before any other module, every blocked command fails in a child process, no credential reaches a test, a non-loopback socket is refused and loopback is not |
| `test_bump_packages.py` | `eng:update-node-packages`' `bump_packages.sh` (ENG-1751), run for real against a git repo with fake `npm`/`npx`: a worktree whose directory isn't the repo's name still gets that repo's pins, from the origin remote (SSH or HTTPS); an explicit `<repo-name>` wins and no remote falls back to the directory; other services get only the tier pins; Functions hold `@types/node` at the `engines.node` major; every applied pin is printed on the `PINS` line |
| `test_land_pr_no_merge.py` | `eng:update-node-packages`' `land_pr.sh` (ENG-1752) never merges on its own: the one `gh pr merge` sits behind a READY exit that only the literal `--merge` skips, and SKILL.md runs it without `--merge` first |
| `test_monitor_firebase_reclassify.py` | `eng:monitor-firebase`'s `reclassify.py` and the planner's `--reclassify` (ENG-1756), with Known Team Networks and whois stubbed: lines tagged `(unclassified: …)` are judged teammate, Not a teammate (an ALERT) or unknown; `--untagged-since` also takes untagged allowed whole-node lines from then on, never earlier ones, denied rows or lines already classified; Known Team Networks still unreadable or a failed whois leaves the lines for the next pass; the plan rewrites each tag to `(classified <date>: …)`, skips a line that changed since, and does nothing without the file |
| `test_monitor_firebase_notion_429.py` | `eng:monitor-firebase`'s Notion 429 handling (ENG-1755), with a fake `curl`: a 429 is retried honouring `Retry-After`, else backing off 1, 2, 4… s capped at 60; still 429 raises `RateLimited`; no other status is retried (a write may have applied); the network check names the reason in its FAIL and in `verdicts.json`; the planner tags each line logged only because the check failed, ` (unclassified: <reason>)`, counts them, and keeps the tag when a later pass extends the line |
| `test_preserve_prod_logs.py` | `eng:preserve-prod-logs` (ENG-1747), gcloud and HTTP stubbed: one GCS bucket per source named with the hold id; Coldline, uniform access, public access prevention, versioning, exactly 400-day retention in the hold project; nothing changes log retention or locks a bucket; a project's copies queue behind each other; named members are granted before legacy access is removed, temporary writers removed after the verify, all IAM as PERSON steps; a step waits for its dependencies and nothing runs without `--apply`; a copy records its operation and `status` follows it by listing; a verify with an empty bucket or a mismatched sample hour never passes; a uid never appears in printed output; Auth records are written without password hashes |
| `test_incident_group_window.py` | The Security Incident Log's group window (ENG-1760): `eng:flag-suspicious-traffic`' `GROUP_OPEN_DAYS` and `eng:monitor-rtdb-probes`' `GROUP_CLOSE_MS` both equal 2 days (Jerome, 2026-11-03; was 14). One rule in two writers, under different names, which no copy check can see |
| `test_firebase_realtime_reads.py` | `eng:profile-firebase-rtdb`'s `realtime-reads.js` and `eng:monitor-firebase`'s per-pass note (ENG-1765), fed the script's real output: allowed realtime gets, queries and listens grouped by op, path shape, auth (admin or client) and client with bytes, ids and IPs; denied, write and REST ops left out; counts only, never an IP or id; wired into `run-profile.sh` and `peek.sh` |
| `test_disable_user_account.py` | `eng:disable-user-account` (ENG-1780), Identity Toolkit stubbed at `call` and urlopen: a prod write is refused before any call unless it has `--prod`, a go and a card; disable sends `disableUser` with `validSince` (never moved back) and enable sends only `disableUser: false`; a read-back that doesn't match fails; a failed read or write is UNVERIFIED; a permission error names the role, project and service account; output never carries the email, the name or the full uid |
| `test_update_kubernetes.py` | `eng:update-kubernetes` (ENG-1741), gcloud and kubectl stubbed at their one call each: the plan targets the newest version on the channel, control plane before pools; refuses more than one minor at a time and prod ahead of dev; a deprecation insight or a non-COS node image hides the commands; a failed or empty read is UNVERIFIED, never current; the skill makes only reads. The checks: PDBs below budget alert, a zero-disruption PDB is a note; nodes off the target, unavailable Deployments, restarts and failed Jobs since the start alert; a CronJob not yet fired is a note; an empty namespace or failed read is UNVERIFIED |
| `test_ip_block_skills.py` (one row per block, ENG-1759) | A block is one Blocklist row listing every range; each add, removal, expiry and extension is one Security Change Log row that names no address, links the Incident row and is keyed so a re-run writes none; a Change Log failure is reported, never raised. REST bursts (in `test_rtdb_rest_burst_log.py`) group by path family and outcome within 2 days, so two IPs with different user agents are one row with Max IPs 2, and a probe group with two IPs is one row (`test_rtdb_incident_log.py`) |
| `test_ip_block_skills.py` | `eng:block-ip` and `eng:review-ip-blocks` (ENGBACK-2114), with GCP (`gcloud_sa.run`) and Notion stubbed, and refused by default in every loaded module; dates are the scripts' UTC `utc_today()`. Refusals: no readable Security Incident Log row, a non-public, malformed or too-broad range, more than 10 per rule, an overlap with a live rule, an expiry past 90 days, a range overlapping our own static or uptime-check addresses, an unexpired rule asked to expire. Preflight verdicts (team network, app-route and webhook 2xx warn; unread traffic warns, never OK), the row's overflow page read, traffic bucketed per range in chunks of 10, the plan written mode 600. The confirm note keeps earlier Notes runs and refuses past Notion's 100-run cap. The review's flags and extend/expire proposals, a skipped Blocklist join. No output names an address. The two `armor_blocklist.py` copies are byte-identical |
| `test_flag_suspicious_traffic.py` | `eng:flag-suspicious-traffic`, with plans built by running `detect.analyse` over synthetic load-balancer rows. Severity per case (Info routine, Low new type, Medium brute force and a flood that moved LB 5xx, High a non-public 2xx, data on a sensitive path, an answered sign-in after refused ones) and the Group key outcome. Incident Log groups: one row per open key with the IP and UA union and counts, minute counting so an overlap or retry never double-counts, older rows' IPs and UAs kept, a new row after 14 days or for another outcome, severity only raised, exit 2 when `Group key`/`Severity` is missing, ban data past 100 items kept whole in a child page, chunks that concatenate back exactly. Slack: default threshold Medium (Info/Low log only), threshold and mention from flag or env, mention only when configured and only on Medium/High, one message per group updated in place, thread replies for a new family or a 10x jump, escalation as a new post with no downgrade, a group ending after the gap; failure injection: a deleted message re-posts once, a transient update error retries without a second post, a failed post retries once, a Notion failure still posts and is not re-posted, the dry run writes nothing. LiveKit agent (UA + route) exempt and counted; spoofed UA or refused agent routes are not; answered POSTs never count toward UA rotation. `blocklist.py`: union across rows, our UA excluded, filters, CSV, read-only. Path families and public routes |
| `reader_harness.py` | Module loader + `FakeTransport`, the `_get_json` stub that makes failure branches reachable |

The `responses_reader` / `fetch_logs` / `mixpanel_api` / `notion_rest` /
`fetch_cancellations` suites
parameterise over **every copy** of the file in `plugins/`. The journal fork of
`fetch_logs.py` is skipped by name, with the reason stated in the code — skipping
on "does the symbol exist" would silently excuse any copy that lost it, which is
the regression being guarded. `scan_node_versions.py` has a single copy (it is
not a shared canonical), so its suite loads that one file directly.

### The per-copy claim has a trap in it

"Runs against every copy" is easy to *say* and easy to get silently wrong, in two
places — and a wrong version of it is worse than no claim at all, because the
suite reports N passes while testing one copy N times.

- **The file itself** caches under its module name, so every copy must load under
  a unique one.
- **Its siblings** are the subtle one. `fetch_logs.py` does
  `from responses_reader import ...` after putting its own dir on `sys.path`, and
  that import caches under the plain name `responses_reader` — identical for
  every copy. So the second copy's `fetch_logs` binds the *first* copy's reader.

The second trap was live in this suite until it was caught in review: breaking one
copy's `CONVERSATION_PROMPTERS` left all 30 `fetch_logs` tests green. `load_reader`
now evicts sibling names around each load and restores `sys.path` wholesale, and
`HarnessIsolation.test_each_copy_binds_its_own_responses_reader` fails if that
isolation is ever removed — verified by removing it.

Anything added here that loads a skill script by path inherits this: go through
`load_reader`, don't hand-roll an `importlib` call.

### A stub must fail, not hang

`FakeTransport._page` implements a real `after`/`has_more` cursor, and refuses to
serve the same offset more than a few times.

Without that refusal, a regression in the reader's `last_id` stop guard makes the
client stop advancing the cursor, the stub keeps answering `has_more=True` for
page 0, and the test spins until the CI job's timeout. The regression this suite
exists to catch would then present as a ~10-minute hang that reads like an infra
flake — strictly worse than no test, because a hang invites a re-run rather than
a diagnosis. Verified both ways: removing the reader's stop guard hung the suite
indefinitely before the refusal existed, and fails in ~0.07s after.

`HarnessFailsFast` covers both directions — the guard fires on a non-advancing
client, and does **not** fire on legitimate multi-page pagination.

General rule for anything added here: **a broken assumption must surface as a
failure with a stack trace.** Prefer raising to spinning, waiting, or retrying.

### The same rule applies to credentials, not just to stubs

`test_mixpanel_api.py` found the second shape of this. `_MixpanelClient(username=None,
…)` does not mean "no creds" — every constructor argument falls back to `os.environ`,
so on a developer machine with `MIXPANEL_SERVICE_ACCOUNT_*` set, the case meant to
exercise the *missing*-creds branch built a fully live client and issued a real
six-day Mixpanel Export with no `where` clause. The run hung until it was killed —
the same "reads like an infra flake" failure as a non-advancing cursor, and this time
it was also making live API calls.

So the suite scrubs `MIXPANEL_*` in `setUp` and the no-creds case asserts
`available is False` **before** it exercises anything. Generalise it: when a module
resolves configuration from the environment, a test that means to withhold that
configuration has to remove it from the environment, and then assert it is gone.
Passing `None` only re-enters the fallback.

## Why failure injection

Every defect in the table below lives in a branch real data cannot reach. OpenAI
does not fail on demand; a hand-written fixture doesn't cross a 100-item page
cap. Happy-path verification against a live user passes while the branch is
broken — which is exactly what happened four times.

`_get_json` is the single network chokepoint (ledger walk, `fetch_response`,
`fetch_input_items` all route through it), so stubbing that one function puts
every failure mode under the test's control.

## Historical defects — what actually reproduces

The [ENG-1024](https://linear.app/aura-health/issue/ENG-1024) card asked for each
of four historical defects to be reproduced by a test that fails against the
pre-fix commit. Three do. The fourth has no pre-fix commit to fail against, and
that is worth recording rather than papering over.

| Defect | Pre-fix commit | Reproducing test | Status |
|---|---|---|---|
| Transcript-source fetch failure left ledger stubs → empty transcript ([#736]→[#737]) | `bb6f459` | `test_unreadable_transcript_source_falls_back_to_next_latest` | ✅ fails there — transcript comes back `''` |
| `/input_items` failure rendered an assistant-only transcript unmarked ([#735]) | `3a77e01` | `test_input_items_failure_sets_messages_error_and_warns` | ✅ fails there — no `_messages_error` |
| Phantom insight-card conversations, 12 of 35 ([#738]) | `e238649` | every case in `test_fetch_logs.py` | ✅ fails there — `_is_conversation_prompter` does not exist |
| Wrong ledger auth token, 105 rows vs 19 ([#733]) | — | `test_ledger_token_prefers_aura_api_token` | ⚠️ **no pre-fix commit** — caught in review, never merged |
| `/input_items` not paginated ([#734]/[#735]) | — | `test_input_items_paginates_past_the_page_cap` | ⚠️ **no pre-fix commit** — caught in review, never merged |
| UTC-derived `to_date` rejected by Mixpanel's project calendar ([ENG-1071]) | `fb4806f` | `test_to_date_past_project_today_is_clamped_not_raised` | ✅ fails there — `to_date cannot be later than today; to_date=2026-08-05; today=2026-08-04` |
| Backticks emitted literally; `**[l](u)**` emitted raw markdown, no link ([ENG-1076]) | `c4f3d1c` | `test_code_span_becomes_a_code_annotation`, `test_link_nested_in_bold_is_a_bold_link`, `test_no_run_ever_contains_raw_markdown` | ✅ fails there — 14 failures on the reverted copy only, which also proves the per-copy parameterisation works as a drift check |
| Responses-API traffic invisible to the conversation report — 42% of calls, `insight-card` 0 vs 1,482, journal untracked ([ENG-1113]) | `e4de7aa` | `test_a_surface_present_in_stats_but_absent_from_the_walk_is_visible`, `test_an_unknown_prompter_still_reaches_the_table` | ✅ fails there — the pre-fix report has no coverage concept at all, so a whole missing API is unrepresentable |
| Cloud Logging 429 rendered four LiveKit days as `$0.00`, understating LiveKit by $34.84 and the Voice/Combined totals with it ([ENG-1214]) | `08d798f` | `test_failed_livekit_read_is_unknown_not_zero`, `test_unread_cell_is_not_a_dollar_zero`, `test_persistent_quota_error_raises_instead_of_undercounting` | ✅ mutation-checked — 7/7 mutations caught, incl. the over-correction that blanks legitimate quiet days |
| LiveKit latency drift: canonical carried 2 of 5 metrics, one copy carried 5 ([ENG-1218]) | `08d798f` | `test_copies_agree`, `test_full_emitted_set_is_covered` | ✅ fails there — `copies_agree` on the pre-fix tree, and `full_emitted_set` is the one that survives a same-narrow-everywhere "fix" the `diff -q` guard calls clean |
| One conversation counted twice across UUID casings, 32 of 587 (~5.5%), and lower-case threads never matching a voice session ([ENG-1113]) | `e4de7aa` | `test_mixed_case_conversation_ids_fold_to_one_thread` | ✅ mutation-checked — reverting the `.upper()` normalisation fails it |
| Per-message ratings collapsed onto the conversation-id dedup key — 53 rows lost in one 7-day harvest, `chat` ~21% ([ENG-1115]) | `e4de7aa` | all of `test_rating_grain.py` | ✅ fails there — 10 of 13 error (`rating_ts` and `_thread_key` do not exist); the 3 that pass are the ones asserting behaviour the defect never touched |

The last two were real defects during development, but they were fixed before the
PR merged, so `main` has never carried them. Their tests are regression guards,
not reproductions — a distinction worth keeping, because "this test fails against
the pre-fix commit" is a much stronger claim than "this test would have caught
it", and only one of them is checkable.

## Checking a test against a historical file

Both suites accept a path override, so you can point them at a file extracted
from any commit:

```bash
git show bb6f459:plugins/data/skills/analyze-user-conversations/scripts/responses_reader.py > /tmp/old.py
AINATIVE_READER_PATH=/tmp/old.py python3 -m unittest discover -s tests
```

`AINATIVE_FETCH_LOGS_PATH` does the same for `test_fetch_logs.py`, and
`AINATIVE_MIXPANEL_API_PATH` for `test_mixpanel_api.py`.

[ENG-1048]: https://linear.app/aura-health/issue/ENG-1048
[ENG-1071]: https://linear.app/aura-health/issue/ENG-1071
[ENG-1076]: https://linear.app/aura-health/issue/ENG-1076
[ENG-1113]: https://linear.app/aura-health/issue/ENG-1113
[ENG-1115]: https://linear.app/aura-health/issue/ENG-1115
[ENG-1118]: https://linear.app/aura-health/issue/ENG-1118
[ENG-1132]: https://linear.app/aura-health/issue/ENG-1132
[#733]: https://github.com/AuraHealth/AINative/pull/733
[#734]: https://github.com/AuraHealth/AINative/pull/734
[#735]: https://github.com/AuraHealth/AINative/pull/735
[#736]: https://github.com/AuraHealth/AINative/pull/736
[#737]: https://github.com/AuraHealth/AINative/pull/737
[#738]: https://github.com/AuraHealth/AINative/pull/738
[ENG-1214]: https://linear.app/aura-health/issue/ENG-1214
[ENG-1218]: https://linear.app/aura-health/issue/ENG-1218
[ENG-1247]: https://linear.app/aura-health/issue/ENG-1247
[ENG-1365]: https://linear.app/aura-health/issue/ENG-1365
