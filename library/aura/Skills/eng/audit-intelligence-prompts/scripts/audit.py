#!/usr/bin/env python3
"""Three-way drift audit for IntelligenceService prompters.

Compares every prompter declared in `src/config.js → prompt.*` across:

1. `src/prompters/<name>.txt` + the per-prompter block in `src/config.js`
2. Braintrust `<Name> [Production]`
3. Braintrust `<Name> [Development]`

Outputs a matrix table (default) or JSON. Returns exit code 0 if no drift,
1 if drift is present (use `--strict` to make CI fail on drift), 2 on error.

Auth: BRAINTRUST_API_KEY must be set.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

try:
    import requests
except ImportError:  # pragma: no cover - import-time
    sys.stderr.write("audit.py requires `requests`. Install with: pip install requests\n")
    sys.exit(2)


BT_API = "https://api.braintrust.dev/v1"

# Strip the trailing `{{metadata.contextInstructions}}` template scaffold
# (with or without a preceding `---` separator) from a Braintrust prompt body
# before comparing against the IS repo `.txt`. Applies to the *appended* shape;
# a *delimited* template (Coach since 2026-10-20) keeps the body alone in
# messages[0], so the strip is a no-op there. See references/strip-scaffold.md.
STRIP_SCAFFOLD = re.compile(
    r"\s*(?:---\s*)?\{\{\s*metadata\.contextInstructions\s*\}\}\s*$",
    re.S,
)
_CONTEXT_TAG = re.compile(r"\{\{\s*metadata\.contextInstructions\s*\}\}")


def _msg_text(m: dict) -> str:
    c = m.get("content")
    if isinstance(c, list):
        return "".join(p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") == "text")
    return c or ""


def scaffold_shape(msgs: list) -> str:
    """How a Braintrust template carries the context — see references/strip-scaffold.md.

    `delimited`: system body, system `---`, user `---`, system context tag, ...
    `appended`:  the context tag ends the system body (after an optional `---`).
    `separate`:  the tag is in its own message without the `---` pair.
    `none`:      no context tag in any message.
    """
    texts = [(m.get("role"), _msg_text(m)) for m in msgs or []]
    for (ra, a), (rb, b) in zip(texts, texts[1:]):
        if ra in ("system", "developer") and a.strip() == "---" and rb == "user" and b.strip() == "---":
            return "delimited"
    if texts and STRIP_SCAFFOLD.search(texts[0][1]):
        return "appended"
    return "separate" if any(_CONTEXT_TAG.search(t) for _, t in texts[1:]) else "none"

# Display-name overrides for prompters whose BT name doesn't share its config-key prefix.
# Keys are config-key (lower) → expected BT display-name prefix (before the [Production]/[Development] tag).
NAME_OVERRIDES = {
    "insightcard": "Done Prompt",
    "memory": "Memory Array",
    "memoryingest": "Memory Ingestion Array",
    "memoryingestionsorting": "Memory Ingestion Sorting",
    "memorysorting": "Memory Sorting",
    "guardrailshandler": "Guardrails Handler",
    "onboardingresults": "Onboarding Results",
    "profileingest": "Profile Ingestion",
    "quickprocessing": "Quick Processing",
}

# Braintrust UI defaults that aren't actually written by IntelligenceService —
# strip them from params before comparing.
PARAM_NOISE_KEYS = {"tool_choice"}


@dataclass
class PromptInfo:
    """Snapshot of one Braintrust prompt."""
    id: str
    name: str
    slug: str
    model: Optional[str]
    params: dict
    tools_normalized: Optional[list]
    tool_params: dict  # {tool name: parameters schema} — inline + referenced
    body: str  # already scaffold-stripped
    tool_descriptions: dict = field(default_factory=dict)  # {tool name: description}
    scaffold: str = "none"  # scaffold_shape(): delimited | appended | separate | none


@dataclass
class AuditRow:
    """One row of the audit report."""
    config_key: str
    txt_file: str
    project: Optional[str]
    repo_body_len: int
    prod: Optional[PromptInfo]
    dev: Optional[PromptInfo]
    repo_vs_prod_body: Optional[bool]
    repo_vs_dev_body: Optional[bool]
    prod_vs_dev_body: Optional[bool]
    config_drift: dict = field(default_factory=dict)
    prod_vs_dev_settings: dict = field(default_factory=dict)
    note: str = ""

    @property
    def has_drift(self) -> bool:
        return (
            self.repo_vs_prod_body is False
            or self.repo_vs_dev_body is False
            or self.prod_vs_dev_body is False
            or bool(self.config_drift)
            or bool(self.prod_vs_dev_settings)
        )


# ---- config.js parsing -----------------------------------------------------

PROMPT_BLOCK_RE = re.compile(
    r"^    (\w+): \{(.+?)^    \},",
    re.M | re.S,
)


def parse_config_prompters(repo_dir: str) -> dict[str, dict[str, Any]]:
    """Parse `src/config.js → prompt.*` into a dict of {configKey: params}.

    The parser walks the file character-by-character to find the `prompt: {`
    block and extracts every two-level-indented `<key>: {...},` block within
    it. This is tolerant of formatting drift in config.js but assumes the
    canonical four-space indentation pattern used in this repo.
    """
    path = os.path.join(repo_dir, "src", "config.js")
    if not os.path.exists(path):
        raise SystemExit(f"src/config.js not found at {path}")
    text = open(path, encoding="utf-8").read()

    # Find the `prompt: {` opening
    m = re.search(r"^  prompt: \{$", text, re.M)
    if not m:
        raise SystemExit("could not locate `prompt: {` block in src/config.js")
    start = m.end()

    # Match braces to find the closing `  }`
    depth = 1
    i = start
    while i < len(text) and depth > 0:
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        i += 1
    if depth != 0:
        raise SystemExit("unbalanced braces in src/config.js prompt block")
    block = text[start:i - 1]

    result: dict[str, dict[str, Any]] = {}
    for m in PROMPT_BLOCK_RE.finditer(block):
        key = m.group(1)
        body = m.group(2)
        result[key] = _parse_prompter_block(body)
    return result


def _parse_prompter_block(block: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    # Array values may span lines. Joining them first is what makes `tools`
    # parse as a list: read line-by-line, `tools: [` coerces to the string "["
    # and every tool name is dropped on the floor, silently.
    for line in _join_multiline_arrays(block):
        # Strip line comments
        line = line.split("//", 1)[0].rstrip()
        m = re.match(r"\s+(\w+):\s*(.+?),?\s*$", line)
        if not m:
            continue
        k, v = m.group(1), m.group(2).rstrip(",")
        out[k] = _coerce(v)
    return out


def _join_multiline_arrays(block: str) -> list[str]:
    """Collapse `key: [` ... `]` spanning several lines into one line."""
    lines = block.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.split("//", 1)[0].rstrip()
        if re.match(r"\s+\w+:\s*\[\s*$", stripped):
            buf = [stripped]
            i += 1
            while i < len(lines):
                nxt = lines[i].split("//", 1)[0].strip()
                buf.append(nxt)
                if nxt.startswith("]"):
                    break
                i += 1
            out.append(" ".join(buf))
        else:
            out.append(line)
        i += 1
    return out


def _coerce(v: str) -> Any:
    v = v.strip()
    if (v.startswith("'") and v.endswith("'")) or (v.startswith('"') and v.endswith('"')):
        return v[1:-1]
    if v in ("true", "false"):
        return v == "true"
    if v.lstrip("-").isdigit():
        return int(v)
    # Array literal: ['a', 'b'] → list of strings
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        if not inner:
            return []
        parts = [p.strip() for p in inner.split(",") if p.strip()]
        out = []
        for p in parts:
            if (p.startswith("'") and p.endswith("'")) or (p.startswith('"') and p.endswith('"')):
                out.append(p[1:-1])
            else:
                out.append(p)
        return out
    # Anything else (object literal, expression) — leave as raw string
    return v


# ---- Braintrust ------------------------------------------------------------

class Braintrust:
    def __init__(self, api_key: str):
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {api_key}"})

    def list_projects(self) -> list[dict]:
        r = self._req("GET", f"{BT_API}/project?limit=200")
        return r.get("objects", [])

    def list_prompts(self, project_id: str) -> list[dict]:
        out, cursor = [], None
        while True:
            url = f"{BT_API}/prompt?project_id={project_id}&limit=200"
            if cursor:
                url += f"&starting_after={cursor}"
            data = self._req("GET", url)
            objs = data.get("objects", [])
            out.extend(objs)
            if not objs or len(objs) < 200:
                break
            cursor = objs[-1]["id"]
        return out

    def fetch_prompt(self, prompt_id: str) -> dict:
        return self._req("GET", f"{BT_API}/prompt/{prompt_id}")

    def fetch_function_name(self, function_id: str, _cache: dict = {}) -> Optional[str]:
        if function_id in _cache:
            return _cache[function_id]
        try:
            obj = self._req("GET", f"{BT_API}/function/{function_id}")
            name = obj.get("name") or obj.get("slug")
        except Exception:
            name = None
        _cache[function_id] = name
        return name

    def fetch_function_description(self, function_id: str, _cache: dict = {}) -> Optional[str]:
        """The tool description the model reads: drift here changes the call without changing the schema."""
        if function_id in _cache:
            return _cache[function_id]
        try:
            desc = self._req("GET", f"{BT_API}/function/{function_id}").get("description")
        except Exception:
            desc = None
        _cache[function_id] = desc
        return desc

    def fetch_function_schema(self, function_id: str, _cache: dict = {}) -> Optional[dict]:
        """The `parameters` of a referenced tool function.

        Name alone cannot answer "is this the same tool?" — a tool whose
        parameters changed under an unchanged name is the drift this exists to
        catch.
        """
        if function_id in _cache:
            return _cache[function_id]
        try:
            obj = self._req("GET", f"{BT_API}/function/{function_id}")
            schema = (obj.get("function_schema") or {}).get("parameters")
        except Exception:
            schema = None
        _cache[function_id] = schema
        return schema

    def _req(self, method: str, url: str, attempts: int = 4) -> dict:
        last_err = None
        for i in range(attempts):
            try:
                r = self.s.request(method, url, timeout=30)
                if r.status_code == 429:
                    time.sleep(2 * (i + 1))
                    continue
                r.raise_for_status()
                return r.json()
            except Exception as exc:
                last_err = exc
                time.sleep(1 + i)
        raise RuntimeError(f"Braintrust request failed after {attempts} attempts: {last_err}")


# ---- prompt-info extraction ------------------------------------------------

def _extract_info(obj: dict, bt: Optional["Braintrust"] = None) -> Optional[PromptInfo]:
    pd = obj.get("prompt_data") or {}
    pr = pd.get("prompt") or {}
    msgs = pr.get("messages") or []
    body_raw = ""
    if msgs and msgs[0].get("role") == "system":
        body_raw = _msg_text(msgs[0])
    # The body is messages[0] in both shapes; only the appended one carries
    # the scaffold inside it.
    body = STRIP_SCAFFOLD.sub("", body_raw)

    options = pd.get("options") or {}
    params = dict(options.get("params") or {})

    # Tools live in two places on a Braintrust prompt:
    #   - prompt_data.prompt.tools     → inline tool definitions (OpenAI-style)
    #   - prompt_data.tool_functions   → array of {id, type} referencing functions by UUID
    # Both must be merged before normalizing; otherwise a referenced tool in Prod
    # and the same tool inlined in Dev (or vice versa) shows as false drift.
    inline_tools = _normalize_tools(pr.get("tools")) or []
    tool_params = _tool_params(pr.get("tools"))
    tool_descriptions = _tool_descriptions(pr.get("tools"))
    referenced_names = []
    for tf in (pd.get("tool_functions") or []):
        if isinstance(tf, dict):
            fid = tf.get("id")
            if fid and bt is not None:
                name = bt.fetch_function_name(fid)
                if name:
                    referenced_names.append(name)
                    # An inline definition wins: it is what this prompt sends.
                    if name not in tool_params:
                        schema = bt.fetch_function_schema(fid)
                        if schema is not None:
                            tool_params[name] = schema
                    if name not in tool_descriptions:
                        desc = bt.fetch_function_description(fid)
                        if desc is not None:
                            tool_descriptions[name] = desc
    merged = sorted(set(inline_tools + referenced_names)) or None

    return PromptInfo(
        id=obj.get("id", ""),
        name=obj.get("name", ""),
        slug=obj.get("slug", ""),
        model=options.get("model"),
        params=params,
        tools_normalized=merged,
        tool_params=tool_params,
        body=body,
        tool_descriptions=tool_descriptions,
        scaffold=scaffold_shape(msgs),
    )


def _normalize_tools(tools: Any) -> Optional[list]:
    """Normalize tools field to a sorted list of names, or None.

    Returns None for: literal None, empty string, empty list, anything we
    can't make sense of. Returns sorted list of tool names otherwise.
    This matters because Braintrust sometimes records `tools: ''` (empty
    string) where production data has `tools: None` — same semantic, would
    show as drift if compared literally.
    """
    if tools is None or tools == "":
        return None
    if isinstance(tools, str):
        try:
            tools = json.loads(tools)
        except Exception:
            return None
    if not isinstance(tools, list):
        return None
    names = []
    for t in tools:
        if isinstance(t, dict):
            fn = t.get("function") or {}
            names.append(fn.get("name") or t.get("name"))
        else:
            names.append(t)
    cleaned = sorted([n for n in names if n])
    return cleaned or None


def _tool_params(tools: Any) -> dict:
    """{tool name: parameters schema} from an inline OpenAI-style tools field."""
    if isinstance(tools, str):
        try:
            tools = json.loads(tools)
        except Exception:
            return {}
    if not isinstance(tools, list):
        return {}
    out: dict = {}
    for t in tools:
        if not isinstance(t, dict):
            continue
        fn = t.get("function") or {}
        name = fn.get("name") or t.get("name")
        if name:
            out[name] = fn.get("parameters") if fn else t.get("parameters")
    return out


def _tool_descriptions(tools: Any) -> dict:
    """{tool name: description} from an inline OpenAI-style tools field."""
    if isinstance(tools, str):
        try:
            tools = json.loads(tools)
        except Exception:
            return {}
    if not isinstance(tools, list):
        return {}
    out: dict = {}
    for t in tools:
        if not isinstance(t, dict):
            continue
        fn = t.get("function") or t
        if fn.get("name") and fn.get("description") is not None:
            out[fn["name"]] = fn["description"]
    return out


def _repo_tool_descriptions(repo_dir: str, names: Any) -> dict:
    """{tool name: description} from the repo's `src/tools/<name>.json`, what production sends."""
    out: dict = {}
    if not isinstance(names, (list, tuple, set)):
        return out
    for name in names:
        if not isinstance(name, str):
            continue
        try:
            with open(os.path.join(repo_dir, "src", "tools", f"{name}.json"), encoding="utf-8") as fh:
                desc = (json.load(fh).get("function") or {}).get("description")
        except Exception:
            continue
        if desc is not None:
            out[name] = desc
    return out


def _repo_tool_params(repo_dir: str, names: Any) -> dict:
    """{tool name: parameters} read from the repo's own `src/tools/<name>.json`.

    This is what production actually sends: `toolManager.getSchema()` returns
    these bundled schemas. `config.js` lists only tool *names*, so the repo side
    of a tool comparison cannot come from config alone.
    """
    out: dict = {}
    if not isinstance(names, (list, tuple, set)):
        return out
    for name in names:
        if not isinstance(name, str):
            continue
        path = os.path.join(repo_dir, "src", "tools", f"{name}.json")
        try:
            with open(path, encoding="utf-8") as fh:
                obj = json.load(fh)
        except Exception:
            continue
        out[name] = ((obj.get("function") or {}).get("parameters"))
    return out


def _param_fingerprint(params: Any) -> Optional[tuple]:
    """The comparable shape of a tool's parameters.

    Deliberately narrow: the property names and the required set. Braintrust
    stores `$schema` and `additionalProperties` that the repo JSON does not, and
    may carry no descriptions at all, so a literal comparison would be entirely
    false drift. Property names and requiredness are what change the tool call
    the model is bound to emit — which is the drift worth reporting.
    """
    if not isinstance(params, dict):
        return None
    props = params.get("properties")
    props = sorted(props.keys()) if isinstance(props, dict) else []
    required = params.get("required")
    required = sorted(required) if isinstance(required, list) else []
    return (tuple(props), tuple(required))


def _norm_text(s: Any) -> Any:
    return " ".join(s.split()) if isinstance(s, str) else s


def _clip_pair(a: Any, b: Any, n: int = 70) -> tuple:
    """Both texts clipped around their first difference, so the excerpt shows the change."""
    if not (isinstance(a, str) and isinstance(b, str)):
        return a, b
    a, b = _norm_text(a), _norm_text(b)
    i = next((k for k, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
    start = max(0, i - 20)

    def cut(t: str) -> str:
        piece = t[start:start + n]
        return ("…" if start else "") + piece + ("…" if start + n < len(t) else "")
    return cut(a), cut(b)


def _tool_text_drift(left_params: dict, right_params: dict, left_desc: dict, right_desc: dict) -> dict:
    """{tool: ({field: left text}, {field: right text})} for the text the model reads.

    `_param_fingerprint` deliberately compares only names and requiredness, so a
    rewritten tool description or property description passed it — and that is
    the drift that changes routing: Braintrust's `handoffToGuardrails` kept a
    one-line description while production told the model `category` is
    telemetry, and the Braintrust copy triggered 3 times in 5 where production
    triggered 23 in 25. Whitespace is normalised; `$schema`/`additionalProperties`
    are Braintrust storage noise and not compared.
    """
    drift: dict = {}
    for name in sorted((set(left_params) | set(left_desc)) & (set(right_params) | set(right_desc))):
        lv: dict = {}
        rv: dict = {}
        # A description missing on one side is drift too: the model then reads no
        # description at all. Same rule as the property texts below.
        if _norm_text(left_desc.get(name)) != _norm_text(right_desc.get(name)):
            lv["description"], rv["description"] = _clip_pair(left_desc.get(name), right_desc.get(name))
        lp = ((left_params.get(name) or {}).get("properties") or {}) if isinstance(left_params.get(name), dict) else {}
        rp = ((right_params.get(name) or {}).get("properties") or {}) if isinstance(right_params.get(name), dict) else {}
        for prop in sorted(set(lp) & set(rp)):
            for fld in ("description", "enum"):
                a, b = (lp[prop] or {}).get(fld), (rp[prop] or {}).get(fld)
                if _norm_text(a) != _norm_text(b):
                    lv[f"{prop}.{fld}"], rv[f"{prop}.{fld}"] = _clip_pair(a, b)
        if lv:
            drift[name] = (lv, rv)
    return drift


def _tool_param_drift(left: dict, right: dict) -> dict:
    """{tool: (left fingerprint, right fingerprint)} for tools both sides name.

    Tools only one side declares are a *name* difference, already reported by
    the normalized name lists; reporting them here too would double-count.
    """
    drift: dict = {}
    for name in sorted(set(left) & set(right)):
        lf = _param_fingerprint(left.get(name))
        rf = _param_fingerprint(right.get(name))
        if lf != rf:
            drift[name] = (lf, rf)
    return drift


def _normalize_params(p: dict) -> dict:
    return {k: v for k, v in p.items() if k not in PARAM_NOISE_KEYS}


def _bt_params_to_config(p: dict) -> dict:
    out: dict = {}
    if "reasoning_effort" in p:
        out["reasoningEffort"] = p["reasoning_effort"]
    if "verbosity" in p:
        out["verbosity"] = p["verbosity"]
    if "temperature" in p:
        out["temperature"] = p["temperature"]
    return out


# ---- discovery -------------------------------------------------------------

# Aurie's runtime writes prompt SPANS to one BT project ({config.prompt.<x>.project})
# but the prompt TEMPLATE is sometimes stored in a different project entirely.
# Notable: `guardrailsHandler.project='conversation'` (spans) but the template
# is in the Guardrail project. So this script searches every BT project rather
# than trusting config.project to find the prompt template.

def _name_expectations(config_key: str) -> str:
    """Return the BT display-name prefix expected for this prompter (lower-case)."""
    override = NAME_OVERRIDES.get(config_key.lower())
    if override:
        return override.lower()
    return config_key.lower()


_NORM = re.compile(r"[\s\-_]")


def _norm_name(s: str) -> str:
    return _NORM.sub("", s.lower())


def _match_prompt_name(name: str, prefix_lower: str, suffixes: tuple[str, ...]) -> bool:
    n = name.strip().lower()
    n = re.sub(r"\s+", " ", n)
    matched_suffix = None
    for sfx in suffixes:
        if n.endswith(sfx.lower()):
            matched_suffix = sfx.lower()
            break
    if not matched_suffix:
        return False
    prefix_in_name = n[: -len(matched_suffix)].strip()
    prefix_in_name = prefix_in_name.rstrip(" -:")
    return _norm_name(prefix_in_name) == _norm_name(prefix_lower)


def find_strict_matches(
    candidates_by_project: dict[str, list[dict]],
    config_key: str,
    hint_project: Optional[str],
) -> tuple[Optional[dict], Optional[dict]]:
    """First-pass strict match by `<Prefix> [Production]` / `[Development]` suffix."""
    prefix = _name_expectations(config_key)
    prod_matches: list[tuple[str, dict]] = []
    dev_matches: list[tuple[str, dict]] = []
    for proj, prompts in candidates_by_project.items():
        for c in prompts:
            name = c.get("name") or ""
            if _match_prompt_name(name, prefix, ("[Production]",)):
                prod_matches.append((proj, c))
            if _match_prompt_name(name, prefix, ("[Development]", "[Dev]")):
                dev_matches.append((proj, c))

    def pick(matches: list[tuple[str, dict]]) -> Optional[dict]:
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0][1]
        if hint_project:
            for proj, c in matches:
                if proj == hint_project:
                    return c
        return matches[0][1]

    return pick(prod_matches), pick(dev_matches)


def find_untagged_fallback(
    candidates_in_hint_project: list[dict],
    claimed_ids: set[str],
) -> tuple[Optional[dict], Optional[dict]]:
    """Second-pass: look for plain `Production` / `Development` names that no
    other prompter has already claimed. Only fires inside the hinted project —
    we never bridge an untagged "Production" across projects.
    """
    prod, dev = None, None
    for c in candidates_in_hint_project:
        if c.get("id") in claimed_ids:
            continue
        name = (c.get("name") or "").strip().lower()
        if not prod and name == "production":
            prod = c
        if not dev and name == "development":
            dev = c
    return prod, dev


# ---- audit runner ----------------------------------------------------------

def run_audit(
    repo_dir: str,
    prompter_filter: Optional[str] = None,
    bt: Optional[Braintrust] = None,
) -> list[AuditRow]:
    bt = bt or Braintrust(os.environ["BRAINTRUST_API_KEY"])
    prompters = parse_config_prompters(repo_dir)
    if prompter_filter:
        if prompter_filter not in prompters:
            raise SystemExit(f"prompter `{prompter_filter}` not found in src/config.js")
        prompters = {prompter_filter: prompters[prompter_filter]}

    # Some prompters are non-Braintrust (e.g. `score` on Gemini) — they declare
    # no `project` field. Everything else gets compared against Braintrust by
    # searching every BT project for a matching display name.
    non_bt_keys = [k for k, c in prompters.items() if c.get("project") is None]

    # Pre-fetch every Braintrust project's prompts once
    bt_projects = bt.list_projects()
    project_prompts: dict[str, list[dict]] = {}
    project_id_by_name: dict[str, str] = {}
    for p in bt_projects:
        pid = p["id"]
        name = p["name"]
        project_id_by_name[name] = pid
        project_prompts[name] = bt.list_prompts(pid)

    # Pass 1: strict prefix-tagged matches across every project.
    # We do all strict matches first so claimed prompt IDs are known before
    # any prompter falls back to untagged-fallback.
    strict_matches: dict[str, tuple[Optional[dict], Optional[dict], Optional[str]]] = {}
    claimed: set[str] = set()
    for key, cfg in prompters.items():
        if key in non_bt_keys:
            continue
        hint_proj = cfg.get("project")
        hint_display = None
        if hint_proj:
            for p in bt_projects:
                if _norm_name(p["name"]) == _norm_name(hint_proj):
                    hint_display = p["name"]
                    break
        prod_raw, dev_raw = find_strict_matches(project_prompts, key, hint_display)
        strict_matches[key] = (prod_raw, dev_raw, hint_display)
        for m in (prod_raw, dev_raw):
            if m and m.get("id"):
                claimed.add(m["id"])

    rows: list[AuditRow] = []
    for key, cfg in prompters.items():
        txt = cfg.get("filename") or f"{key}.txt"
        txt_path = os.path.join(repo_dir, "src", "prompters", txt)
        repo_body = None
        if os.path.exists(txt_path):
            repo_body = open(txt_path, encoding="utf-8").read()

        if key in non_bt_keys:
            rows.append(
                AuditRow(
                    config_key=key, txt_file=txt, project=None,
                    repo_body_len=len(repo_body) if repo_body else 0,
                    prod=None, dev=None,
                    repo_vs_prod_body=None, repo_vs_dev_body=None, prod_vs_dev_body=None,
                    note="non-Braintrust provider (e.g. Gemini); no template to compare",
                )
            )
            continue

        prod_raw, dev_raw, hint_display = strict_matches[key]

        # Pass 2: if strict match failed for either side, fall back to
        # unclaimed untagged "Production"/"Development" inside the hinted
        # project. Won't accidentally claim a different prompter's prompt
        # because Pass 1 already added strict matches to `claimed`.
        if (not prod_raw or not dev_raw) and hint_display:
            fb_prod, fb_dev = find_untagged_fallback(
                project_prompts.get(hint_display, []), claimed
            )
            if not prod_raw and fb_prod:
                prod_raw = fb_prod
                claimed.add(fb_prod.get("id", ""))
            if not dev_raw and fb_dev:
                dev_raw = fb_dev
                claimed.add(fb_dev.get("id", ""))

        # Resolve the actual BT project the prompt lives in (may differ from hint)
        resolved_project = None
        if prod_raw or dev_raw:
            sample = prod_raw or dev_raw
            sample_pid = (sample or {}).get("project_id")
            for name, pid in project_id_by_name.items():
                if pid == sample_pid:
                    resolved_project = name
                    break

        # Fetch full prompt bodies (the list endpoint includes prompt_data but to be safe)
        prod = _extract_info(prod_raw, bt) if prod_raw else None
        dev = _extract_info(dev_raw, bt) if dev_raw else None

        def body_eq(a: Optional[str], b: Optional[str]) -> Optional[bool]:
            if a is None or b is None:
                return None
            return a.rstrip("\n") == b.rstrip("\n")

        r_p = body_eq(repo_body, prod.body if prod else None)
        r_d = body_eq(repo_body, dev.body if dev else None)
        p_d = body_eq(prod.body if prod else None, dev.body if dev else None)

        # config drift = config.js vs BT Production
        cfg_drift: dict = {}
        if prod:
            target = _bt_params_to_config(_normalize_params(prod.params))
            if cfg.get("model") != prod.model:
                cfg_drift["model"] = (cfg.get("model"), prod.model)
            for k in ("reasoningEffort", "verbosity"):
                if cfg.get(k) != target.get(k):
                    cfg_drift[k] = (cfg.get(k), target.get(k))
            # Names first. `_tool_param_drift` only inspects tools BOTH sides
            # declare, so without this a tool added to or removed from one side
            # reads as clean -- the same "documented check that never runs"
            # this comparison exists to close.
            cfg_tool_names = sorted(cfg.get("tools") or []) or None
            if cfg_tool_names != prod.tools_normalized:
                cfg_drift["tools"] = (cfg_tool_names, prod.tools_normalized)
            repo_tools = _repo_tool_params(repo_dir, cfg.get("tools"))
            # Flattened one key per tool: every drift value in this dict is a
            # (left, right) pair, which the renderers unpack.
            for tool_name, pair in _tool_param_drift(
                repo_tools, prod.tool_params
            ).items():
                cfg_drift[f"tool:{tool_name}.params"] = pair
            for tool_name, pair in _tool_text_drift(
                repo_tools, prod.tool_params,
                _repo_tool_descriptions(repo_dir, cfg.get("tools")), prod.tool_descriptions,
            ).items():
                cfg_drift[f"tool:{tool_name}.text"] = pair

        # prod ↔ dev settings drift
        pd_set: dict = {}
        if prod and dev:
            if prod.model != dev.model:
                pd_set["model"] = (prod.model, dev.model)
            if _normalize_params(prod.params) != _normalize_params(dev.params):
                pd_set["params"] = (_normalize_params(prod.params), _normalize_params(dev.params))
            if prod.scaffold != dev.scaffold:
                # Promoting dev to prod would change the template layout.
                pd_set["scaffold"] = (prod.scaffold, dev.scaffold)
            if prod.tools_normalized != dev.tools_normalized:
                pd_set["tools"] = (prod.tools_normalized, dev.tools_normalized)
            for tool_name, pair in _tool_param_drift(
                prod.tool_params, dev.tool_params
            ).items():
                pd_set[f"tool:{tool_name}.params"] = pair
            for tool_name, pair in _tool_text_drift(
                prod.tool_params, dev.tool_params, prod.tool_descriptions, dev.tool_descriptions,
            ).items():
                pd_set[f"tool:{tool_name}.text"] = pair

        note = ""
        if prod is None and dev is None:
            note = "no [Production] or [Development] prompt published on Braintrust"
        elif prod is None:
            note = "no [Production] prompt published on Braintrust"
        elif dev is None:
            note = "no [Development] prompt published on Braintrust"

        rows.append(
            AuditRow(
                config_key=key, txt_file=txt, project=resolved_project or hint_display,
                repo_body_len=len(repo_body) if repo_body else 0,
                prod=prod, dev=dev,
                repo_vs_prod_body=r_p, repo_vs_dev_body=r_d, prod_vs_dev_body=p_d,
                config_drift=cfg_drift, prod_vs_dev_settings=pd_set,
                note=note,
            )
        )
    return rows


# ---- rendering -------------------------------------------------------------

def _mark(v: Optional[bool]) -> str:
    if v is None:
        return "·"
    return "✓" if v else "✗"


def render_table(rows: list[AuditRow]) -> str:
    out = []
    hdr = f"{'Prompter':<24} {'Repo':>6} {'Prod':>6} {'Dev':>6}  R=P R=D P=D  CfgDrift                                P↔D-set"
    out.append(hdr)
    out.append("-" * 130)
    for r in rows:
        repo = r.repo_body_len
        prod = len(r.prod.body) if r.prod else 0
        dev = len(r.dev.body) if r.dev else 0
        cd = ", ".join(f"{k}:{a}→{b}" for k, (a, b) in r.config_drift.items()) if r.config_drift else "—"
        if r.prod_vs_dev_settings:
            pd = ",".join(r.prod_vs_dev_settings.keys())
        elif r.prod and r.dev:
            pd = "—"
        else:
            pd = "·"
        out.append(
            f"{r.config_key:<24} {repo:>6} {prod:>6} {dev:>6}   "
            f"{_mark(r.repo_vs_prod_body)}   {_mark(r.repo_vs_dev_body)}   {_mark(r.prod_vs_dev_body)}   "
            f"{cd:<38}  {pd}"
        )
    out.append("")

    drifters = [r for r in rows if r.has_drift]
    no_prod = [r for r in rows if r.note and "[Production]" in r.note and not r.has_drift]
    if drifters:
        out.append("=== DRIFTS ===")
        for r in drifters:
            out.append(f"\n{r.config_key}  ({r.project})")
            if r.repo_vs_prod_body is False:
                out.append(f"  • repo ↔ prod body differs ({r.repo_body_len} vs {len(r.prod.body)})")
            if r.repo_vs_dev_body is False:
                out.append(f"  • repo ↔ dev body differs ({r.repo_body_len} vs {len(r.dev.body)})")
            if r.prod_vs_dev_body is False:
                out.append(f"  • prod ↔ dev body differs ({len(r.prod.body)} vs {len(r.dev.body)})")
            for k, (a, b) in r.config_drift.items():
                out.append(f"  • config.js ↔ prod {k}: {a!r} → {b!r}")
            for k, (a, b) in r.prod_vs_dev_settings.items():
                out.append(f"  • prod ↔ dev {k}: {a!r} → {b!r}")
            if r.prod:
                out.append(f"  • prod UUID: {r.prod.id}  slug: {r.prod.slug}")
            if r.dev:
                out.append(f"  • dev UUID:  {r.dev.id}  slug: {r.dev.slug}")
    if no_prod:
        out.append("")
        out.append("=== NO [Production] PROMPT (not drift, surfaced for awareness) ===")
        for r in no_prod:
            out.append(f"  · {r.config_key}  ({r.project})  — {r.note}")
    if not drifters:
        out.append("")
        out.append("=== No drift detected ===")
    return "\n".join(out)


def render_json(rows: list[AuditRow]) -> str:
    payload = []
    for r in rows:
        d = {
            "configKey": r.config_key,
            "txtFile": r.txt_file,
            "project": r.project,
            "repoBodyLen": r.repo_body_len,
            "prodBodyLen": len(r.prod.body) if r.prod else None,
            "devBodyLen": len(r.dev.body) if r.dev else None,
            "repoVsProdBody": r.repo_vs_prod_body,
            "repoVsDevBody": r.repo_vs_dev_body,
            "prodVsDevBody": r.prod_vs_dev_body,
            "configDrift": {k: list(v) for k, v in r.config_drift.items()},
            "prodVsDevSettings": {k: list(v) for k, v in r.prod_vs_dev_settings.items()},
            "note": r.note,
            "prodUUID": r.prod.id if r.prod else None,
            "devUUID": r.dev.id if r.dev else None,
            "prodSlug": r.prod.slug if r.prod else None,
            "devSlug": r.dev.slug if r.dev else None,
            "prodScaffold": r.prod.scaffold if r.prod else None,
            "devScaffold": r.dev.scaffold if r.dev else None,
        }
        payload.append(d)
    return json.dumps(payload, indent=2, default=str)


# ---- entrypoint ------------------------------------------------------------

def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo",
        default=os.environ.get(
            "INTELLIGENCE_SERVICE_PATH",
            os.path.expanduser("~/Development/Aura/Services/IntelligenceService"),
        ),
        help="Path to AuraHealth/IntelligenceService checkout (default: $INTELLIGENCE_SERVICE_PATH or ~/Development/Aura/Services/IntelligenceService)",
    )
    parser.add_argument("--prompter", help="Audit a single prompter by config key (e.g. `coach`)")
    parser.add_argument("--output", choices=["table", "json"], default="table")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero on drift (default: always exit 0 unless an error)")
    args = parser.parse_args()

    if not os.environ.get("BRAINTRUST_API_KEY"):
        sys.stderr.write("BRAINTRUST_API_KEY is not set.\n")
        return 2

    # `.git` is a directory in a normal clone and a FILE in a git worktree.
    # Requiring a directory rejects every worktree, which is how this org works.
    if not os.path.exists(os.path.join(args.repo, ".git")):
        sys.stderr.write(f"Not a git repo: {args.repo}\n")
        return 2

    try:
        rows = run_audit(repo_dir=args.repo, prompter_filter=args.prompter)
    except SystemExit:
        raise
    except Exception as exc:
        sys.stderr.write(f"Audit failed: {exc}\n")
        return 2

    if args.output == "json":
        print(render_json(rows))
    else:
        print(render_table(rows))

    has_drift = any(r.has_drift for r in rows)
    if args.strict and has_drift:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
