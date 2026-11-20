---
name: weekly-retro
description: Write Ashish's weekly retro for the "Retros - 2026 - Product & Eng" Notion page — short, human, approach-based (how the work was done, not what was done) — from what actually happened that week (standups, Slack, Linear cards, PRs, session notes). Use when the user says "weekly retro", "write my retro", "retro for this week", "/weekly-retro", or asks what worked / didn't work this week.
---

# Weekly retro

Produces the user's entry for the Friday team retro. It is about **how** the week's
work was done: the ways of working, new tactics and tools (mostly AI-native), and
who helped. It is not a list of what got done. The team already sees the tasks in the
user's daily standup posts, so the retro stays high level and reads like the user
typed it.

**Output goes in chat only.** Never create, draft, edit or remove anything in Notion,
the Retros page included. Reading the page (read only) to check the user's last entry
is fine.

## Arguments

Optional date inside the week (`2026-10-23`, `last week`). Default: the current
week, Monday to today. If "last week" would land on a week the user already has an
entry for, ask which week they mean.

## 1. Gather the week

Collect quietly; none of this detail goes into the retro itself. Run the sources in
parallel. In order of how much they give:

1. **My #standup posts** (Slack, channel `CPTFXHNP2`, user `U064HNE8Q0Y`). One
   plain-language post per working day, split into Completed / In Progress / Blocked.
   Slack search, keywords `["Standup"]`, filters
   `from:<@U064HNE8Q0Y> in:#standup after:<sunday before the week>`, sort by
   timestamp, `include_context: false`.
   - **Blocked items that stay blocked day after day** are "didn't work" candidates.
2. **My other Slack messages this week**: same filter without `in:#standup` and
   without keywords. DMs and threads show who helped (quick reviews, quick answers)
   and the friction a standup post smooths over.
3. **Session notes**: memory files changed in the window
   (`find ~/.claude/projects/*/memory -name '*.md' -newermt <monday>`) and handoff
   notes `~/.claude/handoffs/<date>-*.md` dated in the window. These hold the *how*:
   new methods tried, things run in parallel, what AI did, what failed. If nearly
   every memory file shows up, they were rewritten in bulk (an index cleanup), so the
   date says nothing: use the handoff notes and standups instead.
4. **GitHub**: my PRs touched in the window, across every repo:
   ```bash
   gh search prs --author @me --owner AuraHealth --updated ">=<monday>" --json repository,title,state,closedAt,isDraft --limit 100
   ```
   `--owner AuraHealth` keeps out the user's work in other orgs, which isn't part of
   this retro.
5. **Linear**: `list_issues`, assignee `me`, `updatedAt` from Monday, fields
   `title, status, completedAt, parentId`. Noisy: any edit counts as an update. Keep
   only cards completed in the window and cards a standup mentions.

If the Slack connector errors, retry once. If it still fails, write from the session
notes, PRs and Linear, and say in one line that standups weren't read.

**Before calling a win "live" or "in the latest release"**, check it: `git tag
--contains <merge sha>` and `gh release list` for app work, the App Store lookup for
what users actually have, the live site for web work. A tag means the latest release
build, not the store. A Linear status of "To Be Released" means not live yet.

Group the week into 3–5 areas (a big piece of work, a release, voice work, support,
QA…). For each one ask: *what new way of working, tactic or tool did I use here, and
what did it make easier?* and *who helped, and with what?* **Check every point
happened inside the window**: work built in an earlier week and only merged or
released this week does not count as this week's approach.

## 2. Write it

**Always use the user's own pattern from the Retros page** (set 2026-11-20, after he
pointed at his real entries: "Still not in pattern what I write in retro… we can show
both process and review"). Read his last two entries on the page first (read only) to
match them. His entries mix four kinds of point, each a `•` main point with `◦`
sub-points that give the concrete example:

1. **What improved in process** (AI Native, QA, release, support…), with the example.
2. **What we shipped this week**: app and version, in plain words.
3. **What is coming next**: "… will be released this week / next week".
4. **Thanks** as a sub-point under the point it belongs to ("Thanks to Naveed for the
   quick QA and PR review").

```
What Worked Well
Ashish

• <process improvement>
  ◦ <concrete example from this week>
• Shipped this week:
  ◦ <app version: what users get>
• <what comes next> will be released this week.
  ◦ Thanks to <person> for <what they did>.

What Didn't Work Well & Changes/Suggestions
Ashish

• <what was slow or went wrong, honestly: "QA is better but the data side was a little
  late, we could have released faster">
  ◦ Next time I will <change>.
```

His real entries (6 Nov, 2 Oct, 30 Oct) are under "His own entries" below; match
their tone: plain, first person, 4–6 main points, a sub-point or two each.

- **Main point:** one short sentence about *how* the work was done or went: using AI
  in a new way, a process that got faster, a piece of work and what made it easy.
  First person is fine ("I…", "Using AI to…").
- **Write it as the user, not about the user.** Before drafting, put yourself in his
  place and look back at his week. What was the big moment (a release that finally
  went out, a hard bug)? What did he change in how he works? Who stepped in to help?
  Start from what mattered most to him, and write it in his plain words ("I now…",
  "Instead of waiting…", "Rohan suggested…"), not as a polished outside report.
  (2026-11-20: "can you be at place of me and think then create".)
- **Sub-points and thanks are part of his pattern.** A sub-point gives the concrete
  example or the thanks; it never just repeats its main point.
- **Wins (optional, 1–2):** when there aren't enough different approach points, or
  they start to sound alike, add a win: something users, coaches or the team got this
  week, in plain words, ending with where it stands ("In the latest
  release.", "Confirmed on the live site."). Put wins first. Only real, checked wins.
- **Didn't work:** usually one point. Write a problem I found and fixed that way:
  where I found it, that I fixed it, then "next time I will…".
- No Asks/Discussion section unless the user asks for one.

## Rules — each one came from the user rejecting a draft

- **Process and shipped, both.** Show how the work was done *and* what shipped or is
  coming. Never list support case details ("Coach booking page: checked her
  availability for a month…") or every small fix; name what users got.
- **This week only.** Every point must come from something done Monday to today.
- **Fresh wording each week.** Follow the shape of the previous entry, never copy its
  sentences ("AI Native approach for each task", "tokens were enough"…). Each point has
  to come from this week's work.
- **Short and concrete.** One short sentence per main point, one per sub-point. If it
  needs a second sentence, it is too detailed. A main point can be a short heading
  ("Release process improved", "Shipped this week") with the detail in its sub-points.
  Aim for the length of the 2026-11-20 entry: about 4–10 words a main point, 6–12 a
  sub-point. (2026-11-20: "can you make points short".)
- **Didn't work can be more than one point**, each with a "Next time I will…"
  sub-point.
- **Important points only, no over-explaining.** Drop any sub-point that just explains
  its main point. Thanks go where someone really helped, not on every point.
- **4–6 main points, each a real thing from this week**, never padding. Never write a
  win and a "didn't work" point about the same thing. Before showing, check the entry
  against his own entries below for length and tone.
- **No repeated wording across points.** Don't open or end every point the same way.
  Say "AI" in one or two points at most, use "thanks to" once, and vary words like
  "test", "voice" and "working". Read the whole entry before showing it; if two lines
  sound alike, reword one.
- **No technicalities.** No PR numbers, ticket ids, branch or file names, tool names
  (Metro, CDP, simulator, gh stack…) or percentages. An app version is fine on a
  shipped point ("Aura 3.35.0"), the way the team writes releases.
- **A problem I found and fixed reads that way.** "…was working before but stopped
  working. Found it during a voice task and fixed it, next time I will…". Not as a
  mistake that just happened.
- **Don't raise the PR review backlog** — the user has dropped it twice.
- **Don't mention old retros by date** or "closing items from earlier weeks".
- **Don't claim what you haven't seen.** If a point depends on something you couldn't
  check, leave it out.
- **Status words match where the work really stands.** Write "fixed" only once it's
  merged or shipped, and "Apple-approved" only once Apple approved it. Otherwise write
  "in review" or "sent to Apple". Don't add "so X never happens" results nobody measured.
  (2026-11-20: a draft said "Apple-approved backup" for a build still in review and
  "fixed across both apps" for PRs still in review. The user asked for a more realistic
  rewrite.) The App Store lookup can lag about a day after a release, so it can't prove
  that a release from the last day is live.
- **No pasted team content.** Match the page's shape, never another person's words.

## His own entries (the pattern to follow)

From the Retros page, read 2026-11-20. Match the shape and tone, never reuse the points.

Week of 2026-11-20, approved by the user ("looks good"). The latest one and the best
reference for length: short main points, short sub-points.

```
What Worked Well
Ashish

• AI Native approach helping to run tasks in parallel
  ◦ Paywall fix done for Aura, Aurie, UserWeb and AIWeb together, in review now.
  ◦ Tested the one-hour expiry in minutes by shortening it on a test build.
• Release process improved
  ◦ Approved PRs tested together before merging.
  ◦ Experiment settings checked before every release, thanks to Naveed.
  ◦ Old version kept ready with Apple as a backup, thanks to Rohan.
• Shipped this week
  ◦ Aura 3.35.0: old design removed, everyone on 4.0.
  ◦ Aurie 1.33.16: new voice test for existing users.
  ◦ Business dashboard: weekly and monthly trends for Steve.
• Real-phone checks now get their own card, so fixes don't wait.
• New Aurie widget will be released this week.
  ◦ Thanks to Naveed for the quick QA and PR review.

What Didn't Work Well & Changes/Suggestions
Ashish

• Widget QA went well, but the data side came late, release could have been faster.
  ◦ Next time I will ask for the data design at the start.
• Paywall card payments failed after an hour open, found from Yawar's QA, fixes in review.
  ◦ Next time I will check how long a preloaded page stays signed in.
```

Week of 2026-11-06:

```
What Worked Well
Ashish

• AI Native helping a lot in order to parallel task
◦ Creating loom on iphone duo for both Aura and Aurie.
◦ Old Design cleanup getting much easier and AI finds issue while working on different screen as it found relations on previous screen which might be miss In-Human review
• The Aurie Voice issue was discovered during testing of the new experiment. The Fix was then deployed.
• New voice experiment is ready for launch, QA got approved by yawar
• Old design clean is been partially ready will releasing next week.

What Didn't Work Well & Changes/Suggestions
Ashish

• When Cartesia was released, Aurie's speaking speed was affected. I investigated this thoroughly, as I had QA'd the voice extensively and it worked during our testing phases. However, it stopped working on deployment.
```

Week of 2026-10-02:

```
• Have changed how I have handled the design QA 4.0, now using Claude in order to compare both experiments and understand what could be issues doing on screen QA at a time.
◦ which found few issue on paywall plan, coaching booking, joining the live session.
• Started automating a few support tasks now directly to Claude and getting better results from the previous way of doing support tickets.
• I've completed the design 4.0 final changes and incorporated all comments. I'll be releasing this week after we've stabilized on iOS.
```

## Chat-approved one-liner format (superseded 2026-11-20)

Week of 2026-11-09, approved in chat but never on the page. The user later said it is
not his pattern. Kept only as history.

```
What Worked Well
Ashish

• Tested the old design removal against a full checklist with AI before it reached master.
• Had a separate AI reviewer check each area of the app for anything lost.
• New features now go behind an experiment from the start.
• Created experiments straight from Slack, the way the team does.
• Started a support bot that drafts replies for a person to check.
• Extended app testing to the Home Screen widget and older phones.
• Compared voice plans against our real usage before picking one.

What Didn't Work Well & Changes/Suggestions
Ashish

• Merged the business dashboard before its release was planned, next time I will merge only when it can go out in the next release.
```

## Earlier formats (superseded, don't follow)

Week of 2026-10-26 (the user's own entry on the page):

```
What Worked Well
Ashish

• I predominantly use an AI Native approach for each task.
◦ AI native knowledge sharing meetings are great, thanks to jerome
◦ Still now token are getting sufficient over the week.
◦ Sim-Test are getting better on each run
• Release Light theme version 4.0, with help AI QA is much more easy.
◦ Thanks for Quick response on both team product + design
• QA process has been improved now the build are getting more faster response
◦ Thanks to Yawar
• Support Ticket are easy to handle with AI
◦ It's easy to create a reply or solve the issue.

What Didn't Work Well & Changes/Suggestions
Ashish

• Light theme should have run through an experiment it was mix-up on my end. next time i will make sure.
```

Week of 2026-11-02 (approved by the user):

```
What Worked Well
Ashish

• Using AI to run work side by side, like testing the build while it is still uploading.
◦ Saves a lot of waiting time.
• Old design cleanup is partially ready, every part was checked screen by screen before merging.
◦ Rohan and Naveed reviewed it quickly, thanks to both.
• New Aurie voices were tried in every group before they reached Steve.
◦ His picks went straight into the next round.
• Support tickets are easier to dig into with AI.
◦ Looking at the real data first gives the support team a clear answer.

What Didn't Work Well & Changes/Suggestions
Ashish

• Aurie's speaking speed was fine before but quietly stopped working, so everyone heard the normal pace. I caught it while working on the new voices and fixed it, next time I will listen to Aurie after every change.
```

The 10-26 entry is his own and matches "His own entries" above (sub-points and thanks
are his pattern). The 11-02 one was a chat draft.

## After writing

Show it in chat and stop.

**Keep improving this skill as you go.** Whenever the user corrects a draft, or the
run turns up something this skill got wrong or missed (a wrong week, a source that
didn't work, points that came out repeated or task-like), update this file in the
same turn: Rules for style, Gather for sources, Write it for shape. Don't wait for the
end of the run, so the next run starts from the fix.
