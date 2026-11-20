# Live-traffic checks

Referenced from steps 2 and 4.4 of [`SKILL.md`](../SKILL.md). A candidate passes the
external-traffic gate only when every check that applies to it says the event is firing
for real users now. For an error or failure event, check 2 replaces checks 1 and 4: its
own count is too small to read by user or by build, so its parent is read instead.
Record the check that decided each hold in its row reason.

## 1. Small external counts — one tester or real users

`Internal` covers the QA account, not every developer's test profile. Where the external
count is small enough for one tester to produce, query unique users and a daily series
with the same `Internal` breakdown, reading only the external buckets. A few fires from
one user on one day is a developer testing, so the event is not live.

Web events need one more read. Aura Web QA accounts carry `Internal = false`, and
anonymous web traffic has no `$email`, so a short burst of anonymous web fires while the
feature's design ticket or its parent is still in engineering is QA, not users.

## 2. Error and failure events — judge against the parent event

An event that fires only when something goes wrong (`… Failed`, `… Upload Failed`,
`… Error`) is rare by design, so the one-user-one-day rule does not apply to it. Compare
it with the event it fails from, on the same builds — for example
`AI Conversation Attachment Upload Failed` against `AI Conversation Attachment Added`.
When the parent event is live, including on the latest builds (check 4 applied to the
parent), the failure event is wired and live, and a handful of fires is a low failure
rate. Hold it only when the parent itself is not live or every failure comes from
internal users.

## 3. Journey and experiment events — read the timing first

Events emitted by a scheduled journey (an Iterable journey, a day-N email, a delayed
offer) stay silent until users reach that step. Before calling a gap "not live", read
the launch date and step timing on the experiment's launch-monitoring card. Fires before
the launch are the production test. The hold reason names the date real traffic is due —
for example, offer-page events after the day-10 email.

## 4. App events — split by build

A 30-day or weekly external count can pass while an event has already gone silent on the
builds users are moving to. For mobile events, break the feature's family down by
`$app_version_string` (an event property) alongside `Internal`. An event that fires on
older builds but not on the latest ones, while its siblings in the same feature do fire
there, is not live: either the surface was removed or its tracking broke. Hold it, and
list it on the run card as a question for the app team. Error and failure events are
read through their parent instead (check 2).
