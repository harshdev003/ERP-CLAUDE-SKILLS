"""Decide whether any app version can still name an experiment.

An ended experiment is NOT dead weight: `chooseAll` reads a single-letter `active`
as "ended, serve the winner", so the row is still answering for every user. Archiving
one that some installed version still asks for silently drops those users onto the
control arm -- no error, no log. So the only question that matters is whether a name
appears in code, and the matcher must err toward keeping.

Four passes, each catching a form the others miss. See references/matcher.md.
"""

import io
import json
import os
import re
import subprocess
import tarfile

SOURCE_EXTS = (".js", ".jsx", ".ts", ".tsx", ".swift")
SKIP_DIRS = {"node_modules", ".git", "Pods", "build", "dist", ".next"}

# Pass 1 -- the key is read as an experiment.
RE_PROP = re.compile(r"(?:experiments?|experimentResults?|expResults?)\s*(?:\?\.|\.)\s*([A-Za-z_][A-Za-z0-9_]*)")
RE_INDEX = re.compile(r"(?:experiments?|experimentResults?)\s*(?:\?\.)?\[\s*['\"`]([^'\"`]+)['\"`]\s*\]")
# Bracket access with an IDENTIFIER, not a string: `experiments.chosen?.[AB_EXPERIMENTS.X]`.
# Captures the trailing identifier so pass 4 can resolve it back to a key. Found the
# hard way: `aiThreeDayTrialWeb` reaches AIWeb's paywall only through
# `experiments.chosen?.[AB_EXPERIMENTS.AI_THREE_DAY_TRIAL_WEB]`, which no pass matched,
# so it was classified as an unreferenced string and came within one step of being
# archived. A name can be read without ever appearing as a literal next to `experiments`.
RE_INDEX_IDENT = re.compile(
    r"(?:experiments?|experimentResults?)[\w.?]*\[\s*(?:[A-Za-z_$][\w$]*\s*\.\s*)*([A-Za-z_$][\w$]*)\s*\]")
RE_PATH = re.compile(r"experiments?/([A-Za-z_][A-Za-z0-9_]*)")
RE_ASSIGN = re.compile(r"assign_experiment=([A-Za-z_][A-Za-z0-9_%]*)")

# Pass 2 -- the key is registered for assignment.
RE_MARKER = re.compile(
    r"chooseAll\s*\(|chooseOnboarding\s*\(|chooseTargetedOnboarding\s*\(|"
    r"allExperiments|experimentsToAssign|expNames|EXPERIMENT_NAMES|"
    r"getExperiment|isExperiment|experiments\s*\?\.|experiments\s*\[",
    re.I,
)
REGISTRATION_WINDOW = 40

# Pass 3 -- a quoted literal, but only in a file that talks about experiments at all.
RE_QUOTED = re.compile(r"['\"`]([A-Za-z_][A-Za-z0-9_%]{2,})['\"`]")
# Byte-mode twins for the history patch stream, which is read as bytes to avoid
# decoding 175 MB of diff that is mostly not source.
RE_QUOTED_BYTES = re.compile(rb"['\"`]([A-Za-z_][A-Za-z0-9_%]{2,})['\"`]")
RE_PROP_BYTES = re.compile(rb"(?:experiments?|experimentResults?|expResults?)\s*(?:\?\.|\.)\s*([A-Za-z_][A-Za-z0-9_]*)")
RE_EXP_FILE = re.compile(r"[Ee]xperiment")

# Pass 4 -- the key is bound to a named constant that is used elsewhere.
RE_CONST = re.compile(
    r"""(?:(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=|
         ([A-Za-z_$][\w$]*)\s*:\s*|
         static\s+(?:let|var)\s+([A-Za-z_$][\w$]*)\s*=|
         case\s+([A-Za-z_$][\w$]*)\s*=)
        \s*['"`]([A-Za-z0-9_%]+)['"`]""",
    re.X,
)
# Field names from content data, not experiment constants. Binding `id: 'breathSleep'`
# in a track list must not keep the breathSleep experiment alive.
GENERIC_IDENTS = {
    "id", "key", "name", "title", "subTitle", "type", "Type", "category",
    "logCategory", "queryCategory", "detailsKey", "topicKey", "trackType",
    "overrideContentType", "unique", "sentFrom", "collapsed", "motivation",
    "work", "value", "label", "slug", "tab", "screen", "route", "icon",
    "image", "color",
}


def walk_tree(root, exts=SOURCE_EXTS):
    """Yield (path, text) for every source file under `root`."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for filename in filenames:
            if filename.endswith(exts):
                path = os.path.join(dirpath, filename)
                try:
                    yield path, open(path, errors="ignore").read()
                except OSError:
                    continue


def history_referenced(repo, paths, keys, chunk=1 << 20):
    """Every key that EVER appeared in this repo's history of `paths`.

    Streams `git log -p` -- one pass over every added, removed and context line in
    every commit -- and keeps any key seen as a quoted literal or an experiment
    property read. Exact with respect to time: a name added and deleted between two
    releases is still caught.

    This replaced checkpoint sampling, which was wrong in the direction that breaks
    users. Sampling the Aura app every 6 months for 4 years found 1,496 keys; this
    finds 1,999. The 490-row gap is experiments that lived and died between two
    samples -- each one a row the sampled run would have archived while an installed
    build could still ask for it.

    Cost is why sampling was ever tempting and why it is not needed: the Aura app's
    full patch stream for `src` + `app.js` is ~175 MB and scans in about 9 seconds.

    Deliberately crude -- quoted literals only, no file-level gating. Over-keeping
    costs savings; under-keeping drops users onto the control arm with no error.
    """
    keys = set(keys)
    seen = set()
    proc = subprocess.Popen(
        ["git", "-C", repo, "log", "-p", "--no-color", "--format=", "--", *paths],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    buffer = b""
    for block in iter(lambda: proc.stdout.read(chunk), b""):
        buffer += block
        lines = buffer.split(b"\n")
        buffer = lines.pop()
        for line in lines:
            for raw in RE_QUOTED_BYTES.findall(line):
                key = raw.decode("utf8", "ignore")
                if key in keys:
                    seen.add(key)
            for raw in RE_PROP_BYTES.findall(line):
                key = raw.decode("utf8", "ignore")
                if key in keys:
                    seen.add(key)
    proc.stdout.close()
    proc.wait()
    return seen


def history_last_seen(repo, paths, keys, chunk=1 << 20):
    """{key: YYYY-MM-DD} -- the last commit in which each key appears at all.

    The date a key stops appearing is the date its code was REMOVED, so every build
    released before it still carries that code. That is exactly the quantity the
    archive decision needs: pair it with the share of users still on builds older
    than that date, and "is this safe" becomes a measured number instead of a guess.

    Removal lines count. A key deleted in a cleanup shows the cleanup's date, which
    is correct -- builds shipped up to that point contain it.
    """
    keys = set(keys)
    seen = {}
    current = None
    proc = subprocess.Popen(
        ["git", "-C", repo, "log", "-p", "--no-color", "--format=@%cI", "--", *paths],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    buffer = b""
    for block in iter(lambda: proc.stdout.read(chunk), b""):
        buffer += block
        lines = buffer.split(b"\n")
        buffer = lines.pop()
        for line in lines:
            if line.startswith(b"@") and len(line) > 10 and line[1:5].isdigit():
                current = line[1:11].decode()
                continue
            if current is None:
                continue
            for regex in (RE_QUOTED_BYTES, RE_PROP_BYTES):
                for raw in regex.findall(line):
                    key = raw.decode("utf8", "ignore")
                    if key in keys and (key not in seen or current > seen[key]):
                        seen[key] = current
    proc.stdout.close()
    proc.wait()
    return seen


def walk_commit(repo, sha, paths, exts=(".js", ".jsx", ".ts", ".tsx")):
    """Yield (name, text) for a commit without checking it out."""
    proc = subprocess.run(["git", "-C", repo, "archive", sha, *paths], capture_output=True)
    if proc.returncode:
        return
    with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tar:
        for member in tar.getmembers():
            if member.isfile() and member.name.endswith(exts):
                yield member.name, tar.extractfile(member).read().decode("utf8", "ignore")


def match(files, keys):
    """Return {pass_name: set(keys)} for one corpus. `files` is consumed once."""
    keys = set(keys)
    read, registered, quoted = set(), set(), set()
    bindings, texts = {}, []
    indexed_by = set()

    for _, text in files:
        texts.append(text)

        for regex in (RE_PROP, RE_INDEX, RE_PATH, RE_ASSIGN):
            read |= {m for m in regex.findall(text) if m in keys}
        # An identifier used to index `experiments` is a READ. Record it so pass 4's
        # constant resolution can map it back to the key it holds.
        indexed_by |= set(RE_INDEX_IDENT.findall(text))

        lines = text.split("\n")
        hot = set()
        for i, line in enumerate(lines):
            if RE_MARKER.search(line):
                hot |= set(range(max(0, i - REGISTRATION_WINDOW),
                                 min(len(lines), i + REGISTRATION_WINDOW + 1)))
        for i in hot:
            registered |= {m for m in RE_QUOTED.findall(lines[i]) if m in keys}

        if RE_EXP_FILE.search(text):
            quoted |= {m for m in RE_QUOTED.findall(text) if m in keys}

        for m in RE_CONST.finditer(text):
            ident = m.group(1) or m.group(2) or m.group(3) or m.group(4)
            value = m.group(5)
            if value in keys and ident and ident not in GENERIC_IDENTS:
                bindings.setdefault(value, set()).add(ident)

    # A constant only keeps a key alive if the identifier is actually referenced.
    blob = "\n".join(texts)
    via_const = {
        key for key, idents in bindings.items()
        if any(re.search(r"\b%s\b" % re.escape(i), blob) for i in idents)
    }
    # A key whose constant is used to index `experiments` is read, not merely named.
    read |= {key for key, idents in bindings.items() if idents & indexed_by}

    return {"read": read, "registered": registered, "quoted": quoted, "const": via_const}


def referenced(files, keys):
    """Union of all four passes -- the keep-set for one corpus."""
    return set().union(*match(files, keys).values())
