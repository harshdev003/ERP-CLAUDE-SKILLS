#!/usr/bin/env python3
"""Structural checks on every SKILL.md and its `references/` files.

These exist because of the length gate, not alongside it. The gate pushes
authors to move overflow into `references/`, and a botched move is silent: the
file still parses as Markdown, still renders, and still passes the line budget —
it has just lost a chunk of procedure, or kept a link that no longer resolves
from one directory deeper.

Both failures shipped in the very PR that introduced the gate. An extraction cut
through the middle of a `cat > … <<'EOF'` heredoc, taking the `EOF`, the
`gh pr create --draft` call and the whole Bugbot-then-ready cycle with it; and
three moved blocks kept `](references/x.md)` links that resolve to
`references/references/x.md` once the content lives in `references/`. Review
caught them. Review is not a mechanism.

Stdlib only, no network. Runs over the whole repo, so it covers files this PR
never touched.
"""
from __future__ import annotations

import pathlib
import re
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILLS = sorted((REPO / "plugins").rglob("SKILL.md"))

# A fence toggle is a line that is *only* a fence (optionally with a language),
# at any indentation — a fence nested in a list item is still a fence, and 222
# of them under `plugins/` are indented.
#
# "Only a fence" is the load-bearing half. ```` ```<free text>``` ```` is an
# inline pair that opens and closes itself; counting it as a toggle both reports
# every file using that notation as unbalanced *and* inverts `in_fence` for the
# rest of the file, so every later link goes unchecked and dangling paths sail
# through. Five such lines live in `profile-user/references/input-shapes.md`.
#
# The balance check and `_strip_code` deliberately share this one definition.
# When they disagreed, a fence that flipped `in_fence` could be invisible to the
# balance check, so an unclosed one would silently switch off link resolution
# for the remainder of the file and the suite would still pass.
FENCE_LINE = re.compile(r"^\s*```[A-Za-z0-9_+-]*\s*$")

# A single-backtick inline span. Prose quotes shell fragments this way to talk
# *about* them; only unquoted text is the real thing.
INLINE_CODE = re.compile(r"`[^`\n]*`")

# A Markdown inline link target: the `(...)` half of `[label](target)`, with an
# optional `"title"`. Nested parens are excluded, so a target is a single
# unbroken token — every real link in this repo is.
LINK_TARGET = re.compile(r"(?<!\\)\]\(([^()\s]+)(?:\s+\"[^\"]*\")?\)")

# A URI scheme (`https:`, `mailto:`, `slack:`). Not our files to resolve.
URI_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")

# Deliberate stand-ins rather than paths: `<url>`, `{id}`, `$VAR`, `outputs/*`,
# `outputs/.../transcript.md`. These name a value supplied at runtime, or a file
# a run generates, so there is nothing on disk to resolve and never will be.
# Writing one of these forms is how an author declares "not a real path" — the
# check honours that rather than guessing from the shape of the target.
PLACEHOLDER = re.compile(r"[<>{}$*…]|\.\.\.")


def _strip_code(text: str) -> str:
    """Blank out fenced blocks and inline spans.

    Both routinely contain `](...)` that is not a link: a regex alternation
    (`[abc](?:\\d{2})`), a shell snippet, or prose *documenting* the link forms
    this module checks. Resolving those reports files nobody ever claimed exist.
    A real navigable link lives in prose, so dropping code costs no coverage.
    """
    out, in_fence = [], False
    for line in text.split("\n"):
        if FENCE_LINE.match(line):
            in_fence = not in_fence
            out.append("")
        else:
            out.append("" if in_fence else INLINE_CODE.sub("", line))
    return "\n".join(out)


def _relative_links_in(text: str):
    """Yield `(lineno, target)` for every link that should resolve on disk.

    Skipped, in order: pure anchors (`#section`, same-file), absolute URLs and
    other URI schemes, and placeholders. Anything left is a repo-relative path,
    to be resolved against the linking file's own directory — which is the whole
    point, since that is exactly what an extraction one directory deeper gets
    wrong.
    """
    for lineno, line in enumerate(_strip_code(text).split("\n"), 1):
        for match in LINK_TARGET.finditer(line):
            target = match.group(1)
            if target.startswith("#") or URI_SCHEME.match(target) or PLACEHOLDER.search(target):
                continue
            yield lineno, target


def _relative_links(path: pathlib.Path):
    return _relative_links_in(path.read_text(encoding="utf-8"))

# References duplicated verbatim across skills. The spec makes a skill
# self-contained — it cannot read a sibling's `references/` — so shared guidance
# is physically copied, and a copy is only useful while it agrees with its twin.
DUPLICATED_REFERENCES = [
    (
        "plugins/shared/skills/create-aura-skill/references/best-practices.md",
        "plugins/shared/skills/update-aura-skill/references/best-practices.md",
    ),
]


def _markdown_files():
    """Every markdown file under `plugins/`.

    Not just SKILL.md + a one-level `references/*.md` glob, which left 20 files
    unchecked — `references/schemas/`, `agents/`, `commands/`. That gap mattered
    once the link check started resolving all of `plugins/**/*.md`: an unclosed
    fence in one of those files leaves `_strip_code` inside a fence for the rest
    of the file, so its later links are never resolved and the link check stays
    green while covering nothing. The balance check now covers exactly the set
    the link check scans.
    """
    return sorted((REPO / "plugins").rglob("*.md"))


class Fences(unittest.TestCase):
    """An odd number of ``` means a fence was opened and never closed — the
    signature of a cut that landed inside a code block."""

    def test_every_markdown_file_has_balanced_fences(self):
        broken = []
        for p in _markdown_files():
            toggles = sum(1 for ln in p.read_text(encoding="utf-8").split("\n")
                          if FENCE_LINE.match(ln))
            if toggles % 2:
                broken.append(f"{p.relative_to(REPO).as_posix()} ({toggles} fence lines)")
        self.assertEqual(broken, [], f"unbalanced ``` fences in: {broken}")

    def test_no_file_ends_inside_a_fence(self):
        """States the property the link check silently depends on.

        `_strip_code` blanks everything after an unclosed fence, so a file that
        ends mid-fence contributes no links at all — indistinguishable from a
        file with none. Balance implies this for a plain toggle, but the link
        check's coverage rests on it, so assert it directly rather than by
        inference.
        """
        stuck = [p.relative_to(REPO).as_posix() for p in _markdown_files()
                 if sum(1 for ln in p.read_text(encoding="utf-8").split("\n")
                        if FENCE_LINE.match(ln)) % 2]
        self.assertEqual(stuck, [], f"file ends inside an open fence: {stuck}")


class Heredocs(unittest.TestCase):
    """A `<<'EOF'` with no matching `EOF` means the block it opened was
    truncated. This is exactly how the create/update-aura-skill extraction
    swallowed `gh pr create --draft`.

    Inline code spans are stripped first. Prose that *documents* the heredoc
    anti-pattern writes the opener inside backticks, and a reference file made
    only of such prose has openers and no closer through no fault of its own —
    which reports a truncation that did not happen. A real heredoc sits on its
    own line inside a fenced block, never wrapped in single backticks, so
    dropping inline spans costs no coverage."""

    def test_every_opened_heredoc_is_closed(self):
        broken = []
        for p in _markdown_files():
            text = INLINE_CODE.sub("", p.read_text(encoding="utf-8"))
            opens = len(re.findall(r"<<-?'?EOF'?", text))
            closes = len([ln for ln in text.split("\n") if ln.strip() == "EOF"])
            if opens and closes == 0:
                broken.append(f"{p.relative_to(REPO).as_posix()} ({opens} opened, 0 closed)")
        self.assertEqual(broken, [], f"heredoc opened but never closed: {broken}")

    def test_a_truncated_heredoc_is_still_caught(self):
        """The guard must keep failing on the shape it was written for — an
        opener in a fenced block whose closer was cut off by an extraction."""
        truncated = "```bash\ncat > /tmp/body.md <<'EOF'\n## Title\n```\n"
        stripped = INLINE_CODE.sub("", truncated)
        self.assertTrue(re.findall(r"<<-?'?EOF'?", stripped))
        self.assertEqual([ln for ln in stripped.split("\n") if ln.strip() == "EOF"], [])

    def test_documenting_the_pattern_in_prose_is_not_a_truncation(self):
        """`--body "$(cat <<'EOF' … )"` written inside backticks describes the
        anti-pattern; it opens nothing."""
        prose = 'Never use the `--body "$(cat <<\'EOF\' … )"` form.\n'
        self.assertEqual(re.findall(r"<<-?'?EOF'?", INLINE_CODE.sub("", prose)), [])


class RelativeLinks(unittest.TestCase):
    """A moved block's relative links must be rewritten for its new depth.

    This class used to assert only the un-rewritten `](references/…)` form,
    because a repo-wide resolution check reported ~31 pre-existing dangling
    links and shipping it red would have meant a check people ignore. That left
    the other half of the rule untested — and `../` depth is the half you cannot
    eyeball. All 7 cross-skill links in `record-knowledge/references/schemas/`
    were already dead when someone happened to click one; nothing had flagged
    them, because a wrong-depth link still parses, still renders, and still
    passes every other gate. The backlog is now fixed rather than tolerated, so
    the general check below can fail loudly too.
    """

    def test_every_relative_link_resolves(self):
        """Resolve every repo-relative link against its own file's directory.

        Covers all of `plugins/**/*.md`, not just SKILL.md + `references/*.md`:
        agents, commands and nested `references/schemas/` carry cross-skill
        links too, and a `references/schemas/` file sits two levels below the
        skill root, which is where the depth is easiest to get wrong.
        """
        broken = []
        for path in sorted((REPO / "plugins").rglob("*.md")):
            for lineno, target in _relative_links(path):
                # A `#fragment` addresses a section of the target file; only the
                # path half is a thing on disk.
                resolved = path.parent / target.split("#", 1)[0]
                if not resolved.exists():
                    broken.append(f"{path.relative_to(REPO).as_posix()}:{lineno} -> {target}")
        self.assertEqual(broken, [], f"relative links that resolve to nothing: {broken}")

    def test_a_wrong_depth_link_is_caught(self):
        """The guard must keep failing on the shape it was written for.

        `plugins/<a>/skills/<s>/references/x.md` needs four `../` to reach
        `plugins/`; three lands inside the plugin and resolves to nothing. That
        is the exact form of all 7 links this check was added for.
        """
        ref = REPO / "plugins/shared/skills/record-knowledge/references/schemas/experiments.md"
        self.assertTrue(ref.is_file(), "fixture file moved; update this test")
        short = ref.parent / "../../../../data/skills/analyze-experiment/SKILL.md"
        correct = ref.parent / "../../../../../data/skills/analyze-experiment/SKILL.md"
        self.assertFalse(short.exists(), "one level short should resolve to nothing")
        self.assertTrue(correct.exists(), "the correct depth should resolve")

    def test_code_and_placeholders_are_not_treated_as_links(self):
        """Skipping these is what lets the check run red-free and stay honest.

        A regex alternation inside backticks, a fenced snippet, and a `<url>`
        stand-in all contain `](…)` without naming a file on disk.
        """
        self.assertEqual(list(_relative_links_in("Match `[ab](?:\\d{2})` per line.")), [])
        self.assertEqual(list(_relative_links_in("```bash\nsee [x](nope.md)\n```")), [])
        self.assertEqual(list(_relative_links_in("Filed [PROD-1](<url>).")), [])
        self.assertEqual(list(_relative_links_in("Jump to [top](#top).")), [])
        self.assertEqual(list(_relative_links_in("Docs at [x](https://e.com/a.md).")), [])

    def test_a_real_prose_link_is_still_seen(self):
        """The skips must not swallow the thing being checked."""
        self.assertEqual(list(_relative_links_in("See [x](../../CLAUDE.md#pr-workflow).")),
                         [(1, "../../CLAUDE.md#pr-workflow")])

    def test_an_inline_fence_pair_does_not_invert_the_rest_of_the_file(self):
        """A one-line ```` ```…``` ```` pair opens and closes itself.

        Counting it as a toggle would leave `in_fence` inverted for everything
        after it, so later links are silently never resolved — the check would
        still pass, having stopped looking. Five such lines exist under
        `plugins/`, which is why this is pinned rather than assumed.
        """
        doc = "```<free text>```\n\nThen [x](nope.md).\n"
        self.assertEqual(list(_relative_links_in(doc)), [(3, "nope.md")])

    def test_an_indented_fence_still_opens_a_block(self):
        """Fences nested in list items must still hide their contents.

        222 fences under `plugins/` are indented; anchoring the toggle at
        column 0 would scan all of them as prose.
        """
        doc = "- step:\n\n  ```bash\n  see [x](nope.md)\n  ```\n"
        self.assertEqual(list(_relative_links_in(doc)), [])

    def test_an_unclosed_fence_hides_every_later_link(self):
        """Demonstrates why `Fences` must cover the same files as this check.

        This is the failure mode, not a wish: after an unclosed fence the
        stripper blanks the rest of the file, so a dangling link below it is
        never resolved and this check passes having examined nothing. Nothing
        here can detect that — only the balance guard can, which is why the two
        share `FENCE_LINE` and one file set.
        """
        doc = "```bash\nopened, never closed\n\nThen [x](nope.md).\n"
        self.assertEqual(list(_relative_links_in(doc)), [])
        closed = "```bash\nopened\n```\n\nThen [x](nope.md).\n"
        self.assertEqual(list(_relative_links_in(closed)), [(5, "nope.md")])

    def test_reference_files_do_not_link_into_a_nested_references_dir(self):
        """`](references/x.md)` inside a reference file is the un-rewritten form
        of a link that was correct while the content still lived in SKILL.md.
        From `references/` it resolves to `references/references/x.md`."""
        offenders = []
        for skill in SKILLS:
            for ref in sorted(skill.parent.glob("references/*.md")):
                if re.search(r"\]\(references/", ref.read_text(encoding="utf-8")):
                    offenders.append(ref.relative_to(REPO).as_posix())
        self.assertEqual(offenders, [],
                         f"link written from the skill dir, not the references dir: {offenders}")


class Frontmatter(unittest.TestCase):
    """A cut near the top of the file would take the frontmatter with it."""

    def test_every_skill_has_a_closed_frontmatter_block_with_required_keys(self):
        bad = []
        for p in SKILLS:
            lines = p.read_text(encoding="utf-8").split("\n")
            rel = p.relative_to(REPO).as_posix()
            if not lines or lines[0].strip() != "---":
                bad.append(f"{rel}: no opening ---")
                continue
            try:
                end = next(i for i, l in enumerate(lines[1:], 1) if l.strip() == "---")
            except StopIteration:
                bad.append(f"{rel}: frontmatter never closed")
                continue
            # A lost closing `---` doesn't fail the search — it finds a later one
            # (a horizontal rule, or the next file section) and the oversized
            # slice still contains every required key. Anchor on the first body
            # heading instead: frontmatter always closes before it.
            heading = next((i for i, l in enumerate(lines) if l.startswith("# ")), len(lines))
            if end > heading:
                bad.append(f"{rel}: frontmatter closes at L{end + 1}, past the first "
                           f"heading at L{heading + 1} — closing --- is missing")
                continue
            fm = "\n".join(lines[1:end])
            for key in ("name:", "description:"):
                if key not in fm:
                    bad.append(f"{rel}: missing {key}")
            if not re.search(r'^\s+version:\s*"', fm, re.M):
                bad.append(f"{rel}: no quoted metadata.version")
        self.assertEqual(bad, [], f"frontmatter problems: {bad}")


class DuplicatedReferences(unittest.TestCase):
    """Shared reference docs copied into more than one skill must stay identical.

    `best-practices.md` lives twice — once under `create-aura-skill`, once under
    `update-aura-skill` — because a skill cannot read a sibling's `references/`.
    Nothing marks either as canonical; they are peers, and only whoever edits one
    remembering the other keeps them in step.

    That is not a mechanism, and it failed: a PR widening the changelog-framing
    tells list updated only the `create-aura-skill` copy, leaving the copy an
    agent actually reads while running `update-aura-skill` on the old list. The
    fix was inert for the skill it was written for, and no test could say so.
    """

    def test_duplicated_reference_files_are_byte_identical(self):
        for left, right in DUPLICATED_REFERENCES:
            lp, rp = REPO / left, REPO / right
            with self.subTest(pair=f"{left} == {right}"):
                self.assertTrue(lp.is_file(), f"missing: {left}")
                self.assertTrue(rp.is_file(), f"missing: {right}")
                self.assertEqual(
                    lp.read_bytes(),
                    rp.read_bytes(),
                    f"{left} and {right} have diverged. Edit one, then copy it over "
                    f"the other so both skills carry the same guidance.",
                )

    def test_the_pair_list_is_not_empty(self):
        """A list that silently empties turns this guard into a no-op."""
        self.assertTrue(DUPLICATED_REFERENCES)


class Coverage(unittest.TestCase):
    def test_the_suite_actually_found_skills(self):
        """Guards against a path change silently reducing this to zero checks."""
        self.assertGreater(len(SKILLS), 100, "SKILL.md discovery found almost nothing")


if __name__ == "__main__":
    unittest.main()
