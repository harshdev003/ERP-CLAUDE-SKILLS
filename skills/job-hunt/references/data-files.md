# Data files (in C:\Users\Harshit\job-hunt\ — never in Git)

## config.json
Created by `tracker.py init` from `config.example.json`; I may edit it.

| Key | Default | Meaning |
|---|---|---|
| `lookback_days` | 3 | job freshness window; **hard max 5** (larger values are treated as 5) |
| `min_match_score` | 60 | keep jobs scoring at least this |
| `max_applies_per_portal_per_day` | 40 | daily cap per portal |
| `delay_between_applies_sec` | `[30, 60]` | random wait between submissions |
| `research_interval_hours` | 3 | re-search interval when no new matches |
| `naukri_refresh_time` | `"09:30"` | daily refresh time, IST |
| `timezone` | `"Asia/Kolkata"` | |
| `portals` | `["linkedin", "naukri"]` | |
| `excluded_locations` | `[]` | never apply here |
| `excluded_companies` | `[]` | never apply here |

## profile.json
```json
{
  "name": "", "email": "", "phone": "", "location": "",
  "experience": {"total_years": 0, "relevant_years": 0},
  "current": {"role": "", "company": "", "since": ""},
  "skills": {"primary": [], "secondary": []},
  "tools": [], "domains": [],
  "employment": [{"company": "", "title": "", "start": "", "end": "", "highlights": []}],
  "projects": [{"name": "", "stack": [], "summary": ""}],
  "education": [{"degree": "", "institution": "", "year": ""}],
  "certifications": [],
  "targets": {"titles": [], "keywords": [], "locations": [], "experience_range": [0, 0]},
  "form_answers": {
    "current_ctc": "", "expected_ctc": "", "notice_period": "",
    "preferred_locations": [], "open_to_relocation": "", "work_mode": "",
    "work_authorization": "", "linkedin_url": ""
  },
  "naukri_variants": [{"headline": "", "summary": ""}]
}
```
`form_answers` grows with every `needs-input` answer I give (key = a short normalized
form of the question). This file is the **only** source for screening answers.

## applied.csv
Header (written by `tracker.py init`):
`date,portal,job_id,url,company,role,location,score,resume_file,status,notes,dedupe_key`

- `status`: `applied | failed | manual | needs-input | skipped`
- `notes`: reason, plus `gaps: kw1; kw2` for JD keywords I don't have
- `dedupe_key`: normalized `company|title|location` (lowercase, punctuation and
  company suffixes like "pvt ltd", "inc" removed) — written by the script

## run-state.md
```markdown
# Job hunt — run state
Updated: <ISO time>
Status: running | waiting (next search <time>) | paused (<why>) | stopped
Resume: master.docx (<date>), dry run done: yes/no
Last search: linkedin <time>, naukri <time>
Paused portals: <portal — reason>
Scheduled tasks: refresh <id> at 09:30 IST; re-search <id> at <time>
Today: linkedin <n>/<cap>, naukri <n>/<cap>
Pending questions:
- <question> (jobs: <ids>)
Next step: <exact next action>
```

## refresh-log.md
One line per run:
`<YYYY-MM-DD HH:MM IST> | variant <i> | headline: ok/fail | resume: ok/fail | <note>`
