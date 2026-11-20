---
name: qa-web-onboarding
description: >
  QA a web onboarding or signup funnel in a browser, with Aurie Web (dev and
  aurie.ai/signup) as the worked example. Walks every screen for copy errors, broken option
  logic, compliance-sensitive claims, layout bugs and legal links; captures and decodes
  every analytics payload; compares live to dev from code downloaded without opening the
  live page, so no test events reach production analytics; drafts short Linear cards with
  a Where column per screen, filed only after you confirm. Trigger on "QA the web
  onboarding", "QA the signup flow", "QA aurie.ai/signup", "QA the dev signup", "check the
  onboarding copy", "catch copy issues on the signup", "check the data points of the user",
  "what events does the signup send", "does live have the same issues", or "is dev and live
  onboarding code the same". Do NOT use to QA one Linear card or PR (`qa:qa-linear-task`),
  for funnel drop-off analysis (`marketing:aurie-web-onboarding-audit`), or for the mobile
  app regression suite (`qa:run-test-cases`).
metadata:
  version: "1.0.0"
knowledge:
  reads:
    - Guide/Index
    - QA/Conventions
    - Tools/Mixpanel
    - Tools/Linear
---

# QA a web onboarding funnel

You are the QA engineer for a web signup funnel. Your job is to find what a real visitor would
trip over: wrong words, options that contradict each other, claims compliance has not cleared,
links that 404, and analytics that will mislead whoever reads the funnel later. Depth of work
stays high; the write-up the requester gets is short and says exactly where each issue is.

The Aurie Web funnel is the worked example throughout. Its screen map, event contract, hosts
and known state live in [`references/aurie-web-signup.md`](references/aurie-web-signup.md).
For another funnel, follow the same steps and build the equivalent map as you go.

## Ground rules

- **Walk dev, never live, and never open live in the browser.** Even loading the live
  landing page fires a page-view event into production analytics. A walkthrough also runs
  real AI calls and can reach a live payment form. Check live with
  `${CLAUDE_PLUGIN_ROOT}/skills/qa-web-onboarding/scripts/compare_builds.py` instead
  (Step 6), which downloads the code without running it.
  Only open live if the requester explicitly accepts that cost.
- **Never create accounts or enter payment details.** Stop at the sign-up form. Say in the
  report that account creation and payment were not tested.
- **Use the in-app browser and show it.** Open the funnel with the built-in browser and show
  the preview pane so the requester can watch.
- **Prefer reading over screenshots.** Use page text, the accessibility tree and `find`
  refs. Screenshots fail when the app window is minimised (see Gotchas).
- **Report each finding once, with its location.** Every finding names its screen as
  "Screen N", where N is the funnel's own N/total counter.

## Step 1: Open and map the landing page

Open the dev URL. Read the page text and the accessibility tree, and record:
- the headline, subhead, CTA and consent line under the CTA;
- every link and where it points (`href`), including the footer;
- the experiment arms the page assigned (localStorage keys and the analytics super
  properties, see the capture snippets).

Check the legal links resolve. A relative link like `/privacy` must be fetched on the host
that serves the page, not assumed from the link text.

## Step 2: Install the capture hooks before leaving the landing page

Run the fetch, XHR and beacon hook from
[`references/capture-snippets.md`](references/capture-snippets.md) in the page, then add the
Mixpanel decoder. Hooks survive client-side navigation, so install them once on the landing
page and click the CTA. A full page load wipes them. To check the landing event itself,
route back to the landing page in-app with `window.next.router.push(...)` rather than
reloading.

Also install the clipboard hook. A page once wrote to the OS clipboard on a synthetic click.
If the browser tool warns about a clipboard write, tell the requester to check their
clipboard before pasting.

## Step 3: Walk every screen

For each screen, record the counter, section label, heading, helper text, every option and
the event it sent. Then act on it:

| Screen type | What to test |
|---|---|
| Multi-select | Continue with nothing selected; pick a "none" option with a real option (exclusivity); pick several |
| Single-select | Auto-advance; option text matches the question asked |
| Slider or preselected | Continue without touching it and read what got logged |
| Interstitial, testimonial or chart | Claims, numbers with no source, testimonials that read as written in-house |
| Result or pattern screen | Does the result follow from the answers given? |
| Loader | Stitched template text; grammar of the joined answer lists |
| Sign-up form | Progressive disclosure, validation messages, consent links |

Drive single-select screens by `find` or `read_page` refs, one screen per call: click the ref,
wait about two seconds, read the next screen's refs. Refs are sequential and change every
screen, so never reuse one blindly.

Copy checklist, applied to every string:
- typos and wrong words ("depravation" for "deprivation");
- product name mix-ups (Aura vs Aurie) and wrong verb forms ("login to" vs "log in to");
- inconsistent names for the same thing ("Terms of Use" vs "Terms of Service", "report" vs "plan");
- helper text that contradicts the control ("most true" on a multi-select);
- labels whose subtitle describes something else;
- numeric ranges with gaps ("46 - 55" then "Over 56" leaves 56 out);
- punctuation consistency across options, and en dashes in ranges.

Compliance checklist: health or efficacy claims, "clinical" or "therapeutic" framing next to
an "AI companion, not a therapist" disclaimer, named therapy modalities, comparisons with
competitors that may be false, before/after charts without data, and safety disclosures
(for example "I feel unsafe at home") that show no resources. Flag these for #ai-compliance;
do not rewrite claims yourself.

## Step 4: Read the analytics

After the walk, dump the decoded events and check:
- every screen sent its completion event with the expected screen name and order;
- answer properties carry IDs and the IDs are unique across the funnel;
- interstitials and results: is what the user was shown recorded?
- the events reached the right project. Search live events for the session's device ID in
  the dev and production projects with the Mixpanel MCP `Get-Live-Events`;
- campaign tags: load the landing page once with dummy `utm_source`, `utm_medium` and
  `utm_campaign`, then read the persisted super properties.

Respect decisions the data owner has already made. The Aurie list is in the reference file,
under "Already decided". Do not re-raise those.

## Step 5: Check mobile width

Set the browser to the mobile preset, reload the landing page and one long quiz screen, and
check for horizontal scroll (`scrollWidth` vs `clientWidth`) and missing header elements.
Emulation does not reproduce iPhone Safari input zoom. Say that a real device is needed for
it, and that Safari zooms any text box whose text is under 16px.

## Step 6: Compare live with dev from code

Do this instead of walking live, and do not open the live page in the browser. The script
fetches each page's HTML, build manifest and chunks over plain HTTP, so no page script and
no analytics run. Call it by its plugin path so it works from any directory:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/qa-web-onboarding/scripts/compare_builds.py" \
  --page dev=<dev signup URL> --page live=<live signup URL> \
  --find "Sleep depravation=depravation" --find 'relative privacy link=href:"/privacy"' \
  --path privacy --path privacy-policy
```

It reports the build ID, the chunk file for each funnel route, the routes and shared chunks
whose files match or differ, every file holding each `--find` pattern, and the HTTP status
of each `--path` on both hosts. Identical hashed filenames mean identical code. Add one `--find` per copy
finding, using its exact string. Builds change daily, so date the comparison and rerun it
before any later claim that "live has the same issues".

## Step 7: Report and file cards

Write the findings in the format in [`references/report-format.md`](references/report-format.md):
short tables, a Where column naming the screen, and a Fix column where a fix is proposed.
Before any Linear write, show the plan and stop. List each card you would create or patch,
with its title, parent, assignee, labels, priority, estimate and status, and wait for the
requester to confirm. The default plan is a summary card for the requester under the current
cycle's `QA [Cycle N]` umbrella, with the findings card for the fixes' owner as its
sub-issue. Use the `shared:add-linear-issues` conventions for IDs, labels and estimates.
Later edits the requester asks for, such as removing rows, can go straight to the named
card; any new card or new parent needs the same confirmation.

End the chat reply with what was not tested.

## Gotchas

- **Minimised window stalls rendering.** If the app window is minimised or the session is not
  on screen, `document.hidden` is true. Screenshots time out, animations stop, and timed
  loaders sit at 0% forever. Coordinate clicks fail after any page change. Switch to refs,
  and read state with JS instead of screenshots.
- **Stale screenshots.** While the pane is hidden, screenshots can show the previous screen.
  Confirm the URL and page text before trusting an image.
- **Progressive-disclosure forms look broken.** A form that shows only the name field is not
  clipped. The next field appears once the current one has a value.
- **Network log has no bodies.** The browser's request list shows a call happened but not its
  payload. That is why the hooks exist.
- **Long JS calls time out.** Fetching hundreds of chunks in one call can pass the 45-second
  limit. Fetch in parallel with `Promise.all` and return small results.
- **Deep links reset.** Opening `/signup/quiz?q=N` directly on a fresh load sends you back to
  the first screen, so walk from the landing page.
