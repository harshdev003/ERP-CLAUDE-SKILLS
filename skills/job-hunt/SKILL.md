---
name: job-hunt
description: >
  Job-hunt automation: analyze my resume, find recent matching jobs on LinkedIn and
  Naukri in my Chrome, tailor the resume per job description truthfully, apply in
  batches I approve, refresh my Naukri profile daily, and keep going across sessions
  until I say stop. Trigger on /job-hunt, 'start job hunt', 'apply to jobs', 'continue
  job hunt', 'refresh naukri profile', 'job hunt status', 'stop job hunt'.
metadata:
  version: "1.0.0"
---

# job-hunt

Finds recent jobs that match my real experience, tailors my resume to each one
**truthfully**, applies only to the batches I approve, refreshes my Naukri profile
daily, and resumes across sessions from saved state until I say stop.

## Commands

| Command | Does |
|---|---|
| `/job-hunt <resume-path> [domains]` | Start, or restart with a new resume (Step 1, then the loop) |
| `/job-hunt` | Continue from `run-state.md` (Step 2 onwards) |
| `/job-hunt refresh-profile` | Only the Naukri profile refresh (Step 5) — what the daily task runs |
| `/job-hunt status` | Tracker summary (`tracker.py summary`) — no browsing |
| `/job-hunt stop` | Stop the loop, delete the scheduled tasks, save state, final report |

## Data location — private, never in Git

All personal data lives in `C:\Users\Harshit\job-hunt\` (`$JH` below) and is **never**
committed to ERP-CLAUDE-SKILLS or any other repo, pasted into a PR, issue, or chat
outside this conversation. This skill folder holds only instructions, helper scripts
and `config.example.json`.

| File | Purpose |
|---|---|
| `config.json` | limits and schedule (I edit it) — created from `config.example.json` |
| `profile.json` | master profile, the single source of truth for every answer |
| `master.docx` | editable master resume |
| `resumes\` | tailored resumes per job (`.docx` + `.pdf`) |
| `applied.csv` | every job seen or applied, with status |
| `run-state.md` | last action, next step, open questions — the session handoff |
| `refresh-log.md` | daily Naukri refresh log |

Formats of each file: [`references/data-files.md`](references/data-files.md).

Helper scripts (run with `python`, standard library only):

```bash
S=~/.claude/skills/job-hunt/scripts
python $S/tracker.py init                       # create $JH, config.json, csv header, state files
python $S/tracker.py seen --company C --title T --location L    # exit 0 = already seen
python $S/tracker.py add --json '<row>'          # append one row to applied.csv
python $S/tracker.py today                       # applies today per portal vs caps
python $S/tracker.py summary                     # counts, caps, top-10 gaps
powershell -ExecutionPolicy Bypass -File $S/docx_to_pdf.ps1 -In <file.docx> -Out <file.pdf>
```

## Hard rules (apply to every step)

1. **Truthful only.** Never add a skill, tool, employer, title, date, degree,
   certification or metric that is not in `master.docx` / `profile.json`. A JD keyword
   I don't have is logged as a **gap**, never inserted. Screening answers come only
   from `profile.json`; if one can't be answered truthfully, the job becomes
   `needs-input` — never guess.
2. **My login, my credentials.** Never type a password or OTP, never solve a CAPTCHA,
   never create an account on any site. On a login wall, CAPTCHA, or verification
   step: pause, tell me, and resume when I say done.
3. **Nothing is submitted without my approval** of the batch it belongs to (Step 4).
   The first batch for a new resume is a dry run.
4. **Pace, don't evade.** Respect `delay_between_applies_sec` and
   `max_applies_per_portal_per_day`. Never try to hide automation or get around a
   portal's limits or checks; if a portal pushes back (verification, "unusual
   activity", rate-limit message), pause that portal and tell me.
5. **In-portal only.** LinkedIn Easy Apply and Naukri Apply. External company sites →
   log `manual` with the link.
6. **Chrome etiquette.** Use Claude in Chrome; work in a new tab you created; never
   touch my other tabs; close your tabs when the loop stops.
7. **Credit rule wins** (`rules/personal.md`): if any plan window reaches 95% or extra
   usage goes up from the baseline, stop the loop, save `run-state.md`, and tell me.

Note for me: LinkedIn's and Naukri's terms restrict automated activity; keeping the
caps low and approving every batch keeps this close to how I'd apply by hand, but the
account risk is mine.

## Step 0: Load tools and state (every run)

- Load the Chrome tools in **one** ToolSearch call:
  `select:mcp__claude-in-chrome__tabs_context_mcp,mcp__claude-in-chrome__tabs_create_mcp,mcp__claude-in-chrome__tabs_close_mcp,mcp__claude-in-chrome__navigate,mcp__claude-in-chrome__computer,mcp__claude-in-chrome__read_page,mcp__claude-in-chrome__find,mcp__claude-in-chrome__get_page_text,mcp__claude-in-chrome__form_input,mcp__claude-in-chrome__file_upload,mcp__claude-in-chrome__browser_batch`
  (add `mcp__scheduled-tasks__*` when Step 5 or `stop` needs them).
- `python tracker.py init` (safe to re-run; never overwrites existing files).
- Read `config.json`, `profile.json` (if present) and `run-state.md`. If
  `run-state.md` lists pending questions, ask them first.
- Note the credit baseline (`mcp__ccd_session_mgmt__get_usage`, if available).

## Step 1: Analyze the resume (first run or new resume)

1. Read the resume (PDF via the `pdf` skill, DOCX via the `docx` skill).
2. If it is a PDF, rebuild it as `$JH\master.docx` with the **same content** in a clean
   ATS-friendly layout: single column, standard headings (Summary, Experience, Skills,
   Projects, Education, Certifications), real text (no text in tables, images, text
   boxes, headers/footers), standard fonts. Export a PDF with `docx_to_pdf.ps1` and show
   it to me **once** to approve the look. If it is a DOCX, copy it to `master.docx`.
3. Extract into `profile.json`: name, email, phone, location, total and relevant
   experience, current role/company, primary/secondary skills, tools, domains,
   projects, education, certifications (schema in `references/data-files.md`).
4. Propose target job titles, keywords, locations, and an experience range (use the
   optional `[domains]` argument). **Wait for my confirmation once**, then save to
   `profile.json.targets`.
5. Ask me **once**, in a single message, for the usual form fields the resume lacks:
   current CTC, expected CTC, notice period, preferred locations, open to relocation,
   work mode, work authorization, LinkedIn URL (plus anything obvious for my profile).
   Save to `profile.json.form_answers`; don't ask again.
6. Write 3–4 truthful Naukri headline/summary variants (only facts from the profile)
   to `profile.json.naukri_variants`.
7. Restarting with a new resume: keep `applied.csv` (no re-applying), replace
   `master.docx` and the profile facts, and treat the next batch as a dry run again.

## Step 2: Find jobs

Portal mechanics (URLs, filters, selectors that tend to change): [`references/portals.md`](references/portals.md).

- For each portal in `config.portals` and each target title (× preferred locations on
  Naukri), search with the date filter from `lookback_days` (hard max 5) and the
  experience filter. LinkedIn: Easy Apply filter on.
- For each result capture: portal, job id, URL, title, company, location, experience,
  posted date, full JD text (open the job to read the JD).
- **Skip** (log `skipped` with the reason, so it is never re-checked):
  - already in `applied.csv` with any status — `tracker.py seen` (dedupe key =
    normalized company + title + location; the same job on both portals counts once);
  - an "Applied" badge on the card;
  - clear mismatches: experience gap > 2 years from my range, unrelated stack,
    excluded locations, or posted earlier than the lookback window.
- **Score 0–100**: required-skills overlap 50%, title/seniority fit 20%,
  domain/industry 15%, location/work mode 15%. Keep jobs with
  `score >= min_match_score`; note each job's gaps (JD must-haves I don't have).
- Stop searching a portal for this round when it returns no new matches or today's cap
  is reached (`tracker.py today`).

## Step 3: Tailor the resume per job

- Copy `master.docx` to `$JH\resumes\<YYYY-MM-DD>-<company>-<role>.docx` (slugged), edit
  with the `docx` skill, export the PDF with `docx_to_pdf.ps1`.
- **Allowed:** reorder skills and bullets; rewrite the summary and bullets using JD
  keywords **for work I actually did**; put the most relevant projects first; tune the
  title line to the JD's role name only if it matches my real experience.
- **Forbidden:** anything in Hard rule 1. Before saving, diff the tailored text against
  the master facts: every skill/tool/employer/date/metric in the tailored resume must
  exist in the master. If not, remove it.
- Keep 1–2 pages, the master's layout, no text in tables/images. When uploading, the
  file is named `<Name>_Resume.pdf` (copy it to a temp folder under that name).

## Step 4: Apply — batch approval

1. Show the batch as a table: `#, company, role, portal, score, posted, gaps`.
   I reply **"apply all"**, **"apply 1,3,5"**, or **"skip"**. Unapproved jobs are logged
   `skipped` (reason: "not approved").
2. **First batch of a new resume = dry run:** show me 2 tailored resumes (PDF) first;
   submit nothing until I approve them.
3. For each approved job, on its portal only:
   - start Easy Apply / Apply; upload the tailored PDF; use the email and phone from
     `profile.json`; answer screening questions **only** from `profile.json`;
   - a question with no truthful answer in `profile.json` → stop that application, mark
     `needs-input`, collect the question, move on;
   - redirect to an external site or "create an account" → `manual` with the link;
   - **before submit**: verify the attached resume file name and contact details on the
     review screen; **after submit**: confirm the page shows the applied state.
   - wait `delay_between_applies_sec` (random within the range) between applications;
     never exceed `max_applies_per_portal_per_day`.
4. Log every job with `tracker.py add`: date, portal, job_id, url, company, role,
   location, score, resume_file, status (`applied | failed | manual | needs-input |
   skipped`), notes (reason, and `gaps: a; b; c`).
5. A step that fails twice → `failed` with the reason, move on. **3 failures in a row on
   a portal → pause that portal** and tell me.
6. End of batch: ask all `needs-input` questions **at once**; save my answers to
   `profile.json.form_answers`; those jobs can be retried in the next batch.

## Step 5: Daily Naukri profile refresh

- **First run:** create a daily scheduled task (`mcp__scheduled-tasks__create_scheduled_task`)
  at `naukri_refresh_time` (IST) whose prompt is `/job-hunt refresh-profile`. Record its
  id in `run-state.md`.
- **Refresh** (`/job-hunt refresh-profile`): open my Naukri profile in a new tab; set the
  resume headline to the **next** variant in `profile.json.naukri_variants` (round
  robin; index in `refresh-log.md`); re-upload the current master resume PDF; confirm
  both saved; log date, variant index and result in `refresh-log.md`.
- If it can't run (not logged in, Chrome closed, verification), log the reason and tell
  me at the start of the next session. Never sign in for me.

## Continuity — keep going until I say stop

- After each batch: report, update `run-state.md`, then go back to Step 2.
- No new matches left → schedule the next search `research_interval_hours` later
  (a scheduled task whose prompt is `/job-hunt`; record its id), tell me when, and stop
  this turn.
- `run-state.md` after every batch: counts, last search time per portal, paused
  portals, pending questions, scheduled task ids, next step — enough for any session
  to resume with no duplicates.
- Personal rules: refresh the handoff note at ~85% context and let auto-compact
  continue. If a fresh session is needed, start one whose prompt is `/job-hunt` (it
  reads `run-state.md`) and give me its link.
- **`/job-hunt stop`:** delete every job-hunt scheduled task (refresh and re-search),
  close my tabs, write the final `run-state.md`, and give the report.

## Report (after each batch and on stop)

From `tracker.py summary` plus this batch:
- found / applied / skipped (top reasons) / manual / needs-input counts;
- today's applies per portal vs `max_applies_per_portal_per_day`;
- top 10 missing keywords across JDs (to upskill or update my real resume — never to
  add to it untruthfully);
- next scheduled search and refresh times; any paused portal and why.
