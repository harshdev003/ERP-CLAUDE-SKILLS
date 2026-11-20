# Portal mechanics

Portals change their markup often. Prefer `find` / `read_page` (accessibility tree)
over hard-coded coordinates, and re-read the page after every navigation. If a step
fails twice, log `failed` with what you saw and move on (SKILL.md Step 4.5).

Stop and tell me on any: login page, OTP, CAPTCHA, "verify you're human",
"unusual activity", or a rate-limit message. Never work around them.

## LinkedIn

- **Search URL** (one per target title, URL-encode the keywords):
  `https://www.linkedin.com/jobs/search/?keywords=<title>&location=<location>&f_AL=true&f_TPR=r<seconds>&sortBy=DD`
  - `f_AL=true` = Easy Apply only
  - `f_TPR=r259200` = past 3 days, `r432000` = past 5 days (`lookback_days × 86400`)
  - `f_E` experience level (optional): 1 internship, 2 entry, 3 associate,
    4 mid-senior, 5 director, 6 executive — comma-separated
  - `f_WT` work mode (optional): 1 on-site, 2 remote, 3 hybrid
  - `sortBy=DD` = most recent first; paginate with `&start=25`, `50`, …
- **Job id**: the number in `/jobs/view/<id>/` or `currentJobId=<id>`.
- **JD**: the job details pane ("About the job") — `get_page_text` on the job view.
- **Skip signals**: "Applied" label on the card; "No longer accepting applications".
- **Easy Apply flow**: Easy Apply button → contact info (check email/phone match
  `profile.json`) → resume step (upload tailored PDF, select it) → screening questions
  (one or more pages) → Review → Submit. Uncheck "Follow company" if you like; it
  doesn't matter for the application.
- **Applied confirmation**: "Your application was sent" dialog, then the job shows
  "Applied <time> ago".
- A button that says **Apply** (not Easy Apply) opens the company site → `manual`.

## Naukri

- **Search URL** (one per title × location):
  `https://www.naukri.com/<title-slug>-jobs-in-<location-slug>?k=<title>&l=<location>&experience=<years>&jobAge=<days>`
  - `jobAge` = freshness in days (1, 3, 7, 15, 30): use `3` for lookback 3; for 4–5 use
    `7` and drop results posted more than `lookback_days` ago by their posted date.
  - `experience` = my total years (Naukri matches ranges that include it).
  - Sort by date if the page offers it; paginate via the page links.
- **Job id**: the trailing number in the job URL (`…-<jobId>?…`).
- **JD**: "Job description" section plus "Key Skills" chips on the job page.
- **Apply types**:
  - **Apply** / **Apply on Naukri** → in-portal; may ask chatbot-style screening
    questions in a side drawer — answer only from `profile.json`.
  - **Apply on company site** → `manual` with the link.
- **Applied confirmation**: "Applied" state on the button / "You have successfully
  applied" message.
- **Profile refresh** (Step 5): `https://www.naukri.com/mnjuser/profile` →
  "Resume headline" edit → replace text → Save; "Update resume" / "Upload resume" →
  choose the master PDF → confirm the new upload date shows today.

## Rate and caps
- Wait a random `delay_between_applies_sec` between submissions on the same portal.
- Count today's applies with `tracker.py today` before each submission; stop the
  portal at `max_applies_per_portal_per_day`.
