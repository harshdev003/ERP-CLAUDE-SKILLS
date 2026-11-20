#!/usr/bin/env python3
"""Fetch one WHOLE Aurie conversation for a conversation-guardrail alert.

Read-only. Builds the complete, ordered transcript of a conversation and marks
which assistant turns the Guardrails Handler produced, the handoff category of
each, and the turn the Slack alert fired on.

Why a dedicated reader and not `fetch_logs.py --conversation-id`
----------------------------------------------------------------
`fetch_logs.py` rebuilds a conversation from the *latest* call's input items,
capped at the prompter's `numberOfPreviousMessages` (~10 turns). A guardrail
reply earlier than that window is simply absent. On the 2026-10-07 test
conversation (26 turns, handoff at turn 5) it returned 10 turns and **no**
guardrail-handler turn. This skill's whole question is "what did the person
hear across the conversation", so a tail is not good enough.

Sources, in order of preference
-------------------------------
1. **Stored transcript** -- `POST /ai/conversations/export/:userId` (Intelligence
   Service, privileged route, `AURA_API_TOKEN`). The verbatim user/assistant
   messages from the `dialogue` table, i.e. exactly what the person saw,
   including the opener and every guardrail reply, in order. Joined to:
2. **The llmCall ledger** -- BigQuery `llm.llm_call` by conversationId (GCP_KEY) -- which names
   the prompter that produced each assistant message (join key: the assistant
   message's dialogue id == ledger `dialogueId`) and carries the turn's
   `transaction`. A handoff restarts the call loop under the handler's name, so
   a guardrail reply's ledger row reads `guardrails-handler-prompter`.
3. **GCP logs** -- `handoffToGuardrails` "Handing off to guardrails for crisis
   response" entries, which carry `category` + `transaction`. Joined to the
   ledger on `transaction`. Read through `gcloud_sa`, so it runs as the
   GCP_KEY service account (the user login only when GCP_KEY is unset). Best
   effort: without gcloud the category reads "unknown" and nothing else changes.

The export excludes incognito, deleted and removed conversations/messages. When
the conversation is not in the export, the transcript is rebuilt instead from
the ledger by fetching EVERY conversation-prompter response from OpenAI
(`Conversation [Production]`, `OPENAI_CONVERSATION_API_KEY`): its output is the
assistant reply and its newest user input item is the user message. Ledger rows
the export does not contain are hydrated the same way and listed separately, so
a removed guardrail reply is never silently missing.

Exit codes: 0 transcript built; 3 NOT ANALYSABLE (no source produced a
transcript -- never a no-risk verdict); 2 bad arguments.

Usage:
    fetch_conversation.py --conversation-id <UUID> [--user-id <UID>]
                          [--transaction <UUID>] [--json out.json] [--no-gcp]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import responses_reader as rr  # noqa: E402  (byte-identical copy, see docs/canonical-sources.md)
import gcloud_sa  # noqa: E402  (GCP_KEY service account, see docs/canonical-sources.md)

AURA_API = "https://services.aurahealth.io"
GCP_PROJECT = "firebase-auratech16"
HANDLER_PROMPTER = "guardrails-handler-prompter"
# Conversation [Production] -- coach, quick-processing and the guardrails handler
# all bill here, and a handoff never switches project (Intelligence Service
# `recordLlmCall`). See AI Knowledge `Tools/OpenAI` -> project inventory.
CONVERSATION_PROJECT_ID = "proj_esFyd4eq2VIAjKUn69X1ZFfk"
TIMEOUT = 60

# resource_map.md keys its tables US/CA/UK/AU/NZ/IE. ISO alpha-2 matches all but
# the UK (GB). Any other code passes through and takes the country-not-in-map path.
COUNTRY_CODE_TO_KEY = {
    "US": "US", "CA": "CA", "GB": "UK", "UK": "UK",
    "AU": "AU", "NZ": "NZ", "IE": "IE",
}

# Signals of a way to reach a real person, found in assistant text. A HINT for
# the crisis-reply check, never its verdict: the model still confirms the line
# fits the person's country (988/911 do not work outside the US/CA) and that it
# was actually offered, not merely mentioned. Generic "a licensed professional"
# or "someone you trust" is deliberately NOT a signal -- see crisis_reply_check.md.
RESOURCE_PATTERNS: list[tuple[str, str]] = [
    ("988", r"\b988\b"),
    ("911", r"\b911\b"),
    ("999", r"\b999\b"),
    ("112", r"\b112\b"),
    ("000 (AU)", r"(?<![\d,.])000(?![\d,.])"),
    ("111 (NZ emergency / NHS 111)", r"\b111\b"),
    ("741741 (Crisis Text Line)", r"\b741741\b|crisis text line"),
    ("116 123 (Samaritans)", r"116\s?123|samaritans"),
    ("13 11 14 (Lifeline AU)", r"13\s?11\s?14"),
    ("1737 (NZ)", r"\b1737\b"),
    ("Lifeline", r"\blifeline\b"),
    ("Talk Suicide / 9-8-8 CA", r"talk suicide|9-8-8"),
    ("Kids Help Phone", r"kids help phone"),
    ("Befrienders", r"befrienders"),
    ("DV hotline", r"799[-\s]?7233|domestic violence hotline|thehotline"),
    ("RAINN", r"\brainn\b|656[-\s]?4673"),
    ("Trafficking hotline", r"373[-\s]?7888|trafficking hotline"),
    ("Childhelp", r"childhelp|422[-\s]?4453"),
    ("Poison Control", r"poison control|222[-\s]?1222"),
    ("emergency services", r"emergency (services|number|room|department)"),
    ("crisis line (generic)", r"crisis (line|hotline|service)|suicide (hotline|line)|helpline|hotline"),
]

# Phrases that close or drop a topic. HINTS for check (b) only -- the model reads
# the turn in context; "we can leave that there" after a disclosure is the shape
# that motivated this skill (PROD-7238), but the same words after a neutral
# message are fine.
DROP_TOPIC_PATTERNS: list[tuple[str, str]] = [
    ("leave that there", r"leave (that|it|this) there"),
    ("set aside", r"set (that|it|this) aside"),
    ("move on", r"\bmove on\b|moving on"),
    ("change the subject", r"change the (subject|topic)"),
    ("come back to it later", r"come back to (that|it|this) (later|another time)"),
    ("park it", r"\bpark (that|it|this)\b"),
]


class FetchError(Exception):
    """A source could not be read. Carries a short, user-content-free reason."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _aura_token() -> str | None:
    return os.environ.get("AURA_API_TOKEN") or os.environ.get("AURA_ADMIN_TOKEN")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _up(value: Any) -> str:
    return str(value or "").upper()


def scan(text: str, patterns: list[tuple[str, str]]) -> list[str]:
    """Labels of every pattern found in `text` (case-insensitive), in table order."""
    found = []
    for label, pattern in patterns:
        if re.search(pattern, text or "", re.IGNORECASE):
            found.append(label)
    return found


# ---------------------------------------------------------------------------
# Source readers (network). Each raises FetchError; callers degrade.
# ---------------------------------------------------------------------------

def fetch_ledger(conversation_id: str) -> list[dict]:
    try:
        return rr.fetch_ledger_rows_by_conversation(conversation_id)
    except rr.ResponsesReaderError as exc:
        raise FetchError(f"ledger: {str(exc)[:200]}") from exc


def fetch_export(user_id: str, since: datetime | None, until: datetime | None) -> dict:
    """The user's stored conversation turns. `since`/`until` None = whole history.

    The window is only ever narrowed when the ledger has dated this conversation;
    a guessed window would silently cut turns off a long conversation.
    """
    token = _aura_token()
    if not token:
        raise FetchError("export: AURA_API_TOKEN not set")
    body = {}
    if since is not None:
        body["since"] = _iso(since)
    if until is not None:
        body["until"] = _iso(until)
    try:
        r = requests.post(
            f"{AURA_API}/ai/conversations/export/{user_id}",
            headers={"Authorization": f"Bearer {token}", "User-Agent": gcloud_sa.user_agent()},
            json=body,
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        raise FetchError(f"export: request error {type(exc).__name__}") from exc
    if r.status_code != 200:
        raise FetchError(f"export: HTTP {r.status_code}")
    try:
        return r.json()
    except ValueError as exc:
        raise FetchError("export: non-JSON response") from exc


def fetch_aura_user(user_id: str | None) -> dict | None:
    """`GET /users/:userId` -- first name, country, timezone, email. Never raises."""
    if not user_id:
        return None
    token = _aura_token()
    headers = {"User-Agent": gcloud_sa.user_agent()}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = requests.get(f"{AURA_API}/users/{user_id}", headers=headers, timeout=TIMEOUT)
    except requests.RequestException as exc:
        return {"_status": f"request error: {type(exc).__name__}"}
    if r.status_code == 404:
        return {"_status": "not_found"}
    if r.status_code != 200:
        return {"_status": f"http {r.status_code}"}
    try:
        d = r.json()
    except ValueError:
        return {"_status": "non-JSON response"}
    cc = (d.get("countryCode") or "").strip()
    return {
        "_status": "ok",
        "givenName": d.get("givenName"),
        "email": d.get("email"),
        "countryCode": cc or None,
        "resourceMapKey": COUNTRY_CODE_TO_KEY.get(cc.upper()) if cc else None,
        "timezone": d.get("timezone"),
    }


def fetch_handoffs(conversation_id: str, since: datetime) -> list[dict]:
    """GCP `handoffToGuardrails` entries for this conversation (both casings)."""
    if not shutil.which("gcloud"):
        raise FetchError("gcp: gcloud not on PATH")
    ids = sorted({conversation_id, conversation_id.upper(), conversation_id.lower()})
    id_clause = " OR ".join(f'jsonPayload.conversationId="{c}"' for c in ids)
    flt = (
        'jsonPayload.component="handoffToGuardrails" '
        'AND jsonPayload.message="Handing off to guardrails for crisis response" '
        f"AND ({id_clause}) "
        f'AND timestamp>="{_iso(since)}"'
    )
    try:
        out = gcloud_sa.run(
            ["logging", "read", flt, "--project", GCP_PROJECT,
             "--format", "json", "--limit", "500"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FetchError(f"gcp: {type(exc).__name__}") from exc
    if out.returncode != 0:
        raise FetchError(
            f"gcp: gcloud exit {out.returncode} as {gcloud_sa.credential()}: {out.stderr.strip()[:160]}")
    try:
        entries = json.loads(out.stdout or "[]")
    except ValueError as exc:
        raise FetchError("gcp: non-JSON output") from exc
    handoffs = []
    for e in entries:
        p = e.get("jsonPayload") or {}
        handoffs.append({
            "timestamp": e.get("timestamp"),
            "category": p.get("category") or "unreported",
            "transaction": p.get("transaction"),
        })
    handoffs.sort(key=lambda h: h.get("timestamp") or "")
    return handoffs


def hydrate_row(row: dict, api_key: str) -> dict:
    """One ledger row -> {user_text, assistant_text} from its stored response."""
    rid = row.get("openaiId") or ""
    if row.get("openaiAPI") != "responses" or not rid.startswith("resp_"):
        raise FetchError(f"hydrate: row is not a stored response ({row.get('openaiAPI')})")
    try:
        resp = rr.fetch_response(api_key, CONVERSATION_PROJECT_ID, rid)
        items = rr.fetch_input_items(api_key, CONVERSATION_PROJECT_ID, rid)
    except rr.ResponsesReaderError as exc:
        raise FetchError(f"hydrate {rid[:20]}…: {str(exc)[:120]}") from exc
    # input_items arrive newest-first; the newest user item is this turn's message.
    user_text = ""
    for item in items:
        if item.get("role") == "user":
            user_text = rr._item_text(item)
            if user_text:
                break
    return {"user_text": user_text, "assistant_text": rr._output_text(resp)}


# ---------------------------------------------------------------------------
# Assembly (pure -- covered by tests/test_conversation_guardrail_fetch.py)
# ---------------------------------------------------------------------------

def conversation_rows(ledger: list[dict]) -> list[dict]:
    """Ledger rows whose output the user sees, oldest first."""
    rows = [r for r in ledger if r.get("prompterName") in rr.CONVERSATION_PROMPTERS]
    return sorted(rows, key=lambda r: r.get("createdAt") or "")


def find_export_conversation(export: dict, conversation_id: str) -> dict | None:
    for conv in export.get("conversations") or []:
        if _up(conv.get("id")) == _up(conversation_id):
            return conv
    return None


def _message(role: str, text: str, created_at: str | None, row: dict | None,
             source: str) -> dict:
    return {
        "role": role,
        "text": text or "",
        "created_at": created_at,
        "prompter": (row or {}).get("prompterName") if role == "assistant" else None,
        "transaction": (row or {}).get("transaction") if role == "assistant" else None,
        "dialogue_id": (row or {}).get("dialogueId") if role == "assistant" else None,
        "source": source,
    }


def build_from_export(conv: dict, rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Export turns joined to ledger rows. Returns (messages, ledger rows unmatched)."""
    by_dialogue = {_up(r.get("dialogueId")): r for r in rows if r.get("dialogueId")}
    matched: set[str] = set()
    messages = []
    for turn in conv.get("turns") or []:
        role = turn.get("role")
        if role not in ("user", "assistant"):
            continue
        row = by_dialogue.get(_up(turn.get("id"))) if role == "assistant" else None
        if row is not None:
            matched.add(_up(row.get("dialogueId")))
        messages.append(_message(role, turn.get("message"), turn.get("createdAt"), row, "export"))
    unmatched = [r for r in rows if _up(r.get("dialogueId")) not in matched]
    return messages, unmatched


def build_from_hydrated(pairs: list[tuple[dict, dict]]) -> list[dict]:
    """[(ledger row, hydrate_row result)] -> alternating user/assistant messages."""
    messages = []
    for row, h in pairs:
        if h.get("user_text"):
            messages.append(_message("user", h["user_text"], row.get("createdAt"), None, "responses"))
        messages.append(_message("assistant", h.get("assistant_text"), row.get("createdAt"), row, "responses"))
    return messages


def annotate(messages: list[dict], handoffs: list[dict] | None,
             alert_transaction: str | None, start: int = 1) -> list[dict]:
    """Number messages from `start`, attach handoff category, alert marker and signals."""
    by_tx: dict[str, list[dict]] = {}
    for h in handoffs or []:
        by_tx.setdefault(_up(h.get("transaction")), []).append(h)
    for i, m in enumerate(messages, start):
        m["index"] = i
        tx = _up(m.get("transaction"))
        hs = by_tx.get(tx, []) if tx else []
        m["guardrail_handled"] = m["role"] == "assistant" and (
            m.get("prompter") == HANDLER_PROMPTER or bool(hs)
        )
        if m["guardrail_handled"]:
            if hs:
                m["handoff_category"] = hs[0]["category"]
            else:
                m["handoff_category"] = "unknown (no GCP log)" if handoffs is not None else "unknown (GCP not read)"
        m["is_alert_turn"] = bool(alert_transaction) and m["role"] == "assistant" and tx == _up(alert_transaction)
        if m["role"] == "assistant":
            m["resource_signals"] = scan(m["text"], RESOURCE_PATTERNS)
            m["drop_topic_signals"] = scan(m["text"], DROP_TOPIC_PATTERNS)
    return messages


def orphan_handoffs(messages: list[dict], handoffs: list[dict] | None) -> list[dict]:
    """GCP handoffs whose transaction matches no assistant message."""
    seen = {_up(m.get("transaction")) for m in messages if m.get("transaction")}
    return [h for h in handoffs or [] if _up(h.get("transaction")) not in seen]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _absent_tag(m: dict) -> str:
    return " (absent from stored transcript)" if m.get("absent_from_stored_transcript") else ""


def parse_turns(spec: str) -> set[int]:
    """`26-37,45` -> {26..37, 45}. The reviewer's key-turns view (SKILL.md Mode C)."""
    turns: set[int] = set()
    # The Slack `Key turns:` line uses an en dash (`#26–#37`); Mode C pastes it as-is.
    spec = spec.replace("\u2013", "-").replace("\u2014", "-")
    for part in (p.strip().lstrip("#") for p in spec.split(",")):
        if not part:
            continue
        lo, _, hi = part.partition("-")
        start, end = int(lo), int(hi.lstrip("#") or lo)
        if start > end:
            start, end = end, start
        turns.update(range(start, end + 1))
    return turns


def default_key_turns(report: dict) -> set[int]:
    """Two turns before the first guardrail-handled turn through four after the last —
    the window Mode C shows when the triage reply named no key turns."""
    every = list(report["messages"]) + list(report.get("ledger_only_messages") or [])
    gh = [m["index"] for m in every if m.get("guardrail_handled")]
    if not gh:
        return set()
    return set(range(max(1, min(gh) - 2), max(gh) + 5))


def render(report: dict, turns: set[int] | None = None) -> str:
    L: list[str] = []
    L.append("# Conversation guardrail readout")
    L.append("")
    if not report.get("transcript_complete", True):
        L.append("> **⚠️ TRANSCRIPT INCOMPLETE — " + "; ".join(report.get("incomplete_reasons") or ["unknown"])
                 + ".** Turns may be missing: the classification and the crisis-reply check verdict are "
                 "provisional, and the Slack reply must say so.")
        L.append("")
    L.append(f"- **Conversation:** `{report['conversation_id']}`")
    L.append(f"- **User:** `{report.get('user_id') or 'unresolved'}` (full UID — local only; redact in Slack)")
    L.append(f"- **Alert transaction:** `{report.get('alert_transaction') or 'not given'}`")
    L.append(f"- **Transcript source:** {report['source']}")
    L.append(f"- **Messages:** {len(report['messages'])} "
             f"({sum(m['role'] == 'user' for m in report['messages'])} user, "
             f"{sum(m['role'] == 'assistant' for m in report['messages'])} assistant)")
    for w in report.get("warnings") or []:
        L.append(f"- ⚠️ {w}")
    L.append("")

    L.append("## Aura user record")
    aura = report.get("aura_user")
    if not aura or aura.get("_status") != "ok":
        L.append(f"_Unavailable ({(aura or {}).get('_status', 'no UID')}) — first name → `Hi there,`, "
                 "country unknown (country-not-in-map path), no email for the reply._")
    else:
        L.append("| Field | Value |")
        L.append("|---|---|")
        L.append(f"| givenName | {aura.get('givenName') or '(blank)'} |")
        L.append(f"| email | {aura.get('email') or '(blank)'} |")
        L.append(f"| countryCode | {aura.get('countryCode') or '(blank)'} → resource_map key "
                 f"`{aura.get('resourceMapKey') or 'not mapped'}` |")
        L.append(f"| timezone | {aura.get('timezone') or '(blank)'} |")
    L.append("")

    L.append("## Guardrail-handled turns")
    # Recovered (absent-from-stored-transcript) turns are listed too: the person saw them.
    every = list(report["messages"]) + list(report.get("ledger_only_messages") or [])
    gh = [m for m in every if m.get("guardrail_handled")]
    if not gh:
        L.append("_None found in the transcript._")
    else:
        L.append("| Turn | Time | Prompter | Category | Alert turn |")
        L.append("|---|---|---|---|---|")
        for m in gh:
            L.append(f"| #{m['index']} | {(m.get('created_at') or '')[:19]} | {m.get('prompter') or '?'} | "
                     f"{m.get('handoff_category')}{_absent_tag(m)} | {'yes' if m.get('is_alert_turn') else ''} |")
    for h in report.get("orphan_handoffs") or []:
        L.append(f"- ⚠️ GCP logged a `{h['category']}` handoff at {h.get('timestamp')} whose transaction "
                 "matches no assistant message — the handler reply may be missing from every source.")
    L.append("")

    L.append("## Resource / drop-topic signals (hints — confirm in context)")
    sig = [m for m in every if m.get("resource_signals") or m.get("drop_topic_signals")]
    if not sig:
        L.append("_No assistant message mentions a hotline, emergency number or crisis line._")
    else:
        L.append("| Turn | Prompter | Resource signals | Drop-topic signals |")
        L.append("|---|---|---|---|")
        for m in sig:
            L.append(f"| #{m['index']} | {m.get('prompter') or '-'}{_absent_tag(m)} | {', '.join(m.get('resource_signals') or []) or '-'} | "
                     f"{', '.join(m.get('drop_topic_signals') or []) or '-'} |")
    L.append("")

    shown = report["messages"]
    if turns is None:
        L.append("## Transcript (whole conversation, oldest first)")
    else:
        shown = [m for m in shown if m["index"] in turns]
        L.append(f"## Key turns only ({len(shown)} of {len(report['messages'])} — "
                 "drop `--turns` for the whole conversation)")
    for m in shown:
        tags = [m["role"]]
        if m["role"] == "assistant":
            tags.append(m.get("prompter") or "no ledger row (opener / unattributed)")
        if m.get("guardrail_handled"):
            tags.append(f"GUARDRAIL · {m.get('handoff_category')}")
        if m.get("is_alert_turn"):
            tags.append("ALERT TURN")
        L.append("")
        L.append(f"### #{m['index']} · {' · '.join(tags)} · {(m.get('created_at') or '')[:19]}")
        L.append("")
        L.append(m["text"] or "_(empty)_")

    extra = report.get("ledger_only_messages") or []
    if turns is not None:
        extra = [m for m in extra if m["index"] in turns]
    if extra:
        L.append("")
        L.append("## Ledger turns absent from the stored transcript (removed or edited — still shown to the user at the time)")
        for m in extra:
            L.append("")
            tags = [m["role"], m.get("prompter") or "-"]
            if m.get("guardrail_handled"):
                tags.append(f"GUARDRAIL · {m.get('handoff_category')}")
            if m.get("is_alert_turn"):
                tags.append("ALERT TURN")
            L.append(f"### #{m['index']} · {' · '.join(tags)} · {(m.get('created_at') or '')[:19]}")
            L.append("")
            L.append(m["text"] or "_(empty)_")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run(conversation_id: str, user_id: str | None, alert_transaction: str | None,
        use_gcp: bool = True) -> dict:
    warnings: list[str] = []
    # Reasons the transcript may be missing turns. Non-empty -> transcript_complete
    # False, rendered as a banner the Slack reply must carry: a partial read must
    # never be presented as the whole conversation (a missed disclosure or a missed
    # crisis line flips both the classification and the crisis-reply check).
    incomplete: list[str] = []
    ledger: list[dict] = []
    ledger_ok = True
    try:
        ledger = fetch_ledger(conversation_id)
    except FetchError as exc:
        ledger_ok = False
        warnings.append(f"Ledger unreadable ({exc}) — no prompter attribution, no Responses rebuild.")
        incomplete.append("llmCall ledger unreadable — guardrail replies unattributed and turns missing "
                          "from the stored transcript cannot be detected")
    rows = conversation_rows(ledger)

    ledger_users = {r.get("userId") for r in ledger if r.get("userId")}
    if not user_id and len(ledger_users) == 1:
        user_id = next(iter(ledger_users))
    elif user_id and ledger_users and user_id not in ledger_users:
        warnings.append("The given UID does not match the ledger's userId for this conversation — "
                        "the conversation id is authoritative.")
        if len(ledger_users) == 1:
            user_id = next(iter(ledger_users))

    if ledger_ok and not ledger:
        incomplete.append("no llmCall ledger rows for this conversation — guardrail replies cannot be "
                          "attributed and missing turns cannot be detected")
    # The export window is narrowed only when the ledger dates the conversation.
    # Without ledger stamps the whole history is requested: a guessed window would
    # silently drop the start of a long conversation and still read as complete.
    stamps = [d for d in (_parse_iso(r.get("createdAt")) for r in ledger) if d]
    now = datetime.now(timezone.utc)
    since = (min(stamps) - timedelta(days=1)) if stamps else None
    until = (max(stamps) + timedelta(days=1)) if stamps else None

    handoffs: list[dict] | None = None
    if use_gcp:
        try:
            # gcloud needs a lower bound (it otherwise defaults to the last day);
            # without ledger stamps, reach back past Cloud Logging's retention.
            handoffs = fetch_handoffs(conversation_id, since or now - timedelta(days=400))
        except FetchError as exc:
            warnings.append(f"Handoff categories unavailable ({exc}).")

    messages: list[dict] = []
    ledger_only: list[dict] = []
    source = "none"
    conv = None
    if user_id:
        try:
            conv = find_export_conversation(fetch_export(user_id, since, until), conversation_id)
            if conv is None:
                warnings.append("Conversation not in the stored-transcript export (incognito, deleted, or "
                                "outside the window) — rebuilding from OpenAI responses.")
        except FetchError as exc:
            warnings.append(f"Stored-transcript export unreadable ({exc}) — rebuilding from OpenAI responses.")
    else:
        warnings.append("No UID resolved — the stored-transcript export needs one.")

    api_key = os.environ.get("OPENAI_CONVERSATION_API_KEY")
    if conv is not None:
        messages, unmatched = build_from_export(conv, rows)
        source = "stored transcript (IS export) joined to the llmCall ledger"
        if unmatched:
            warnings.append(f"{len(unmatched)} ledger turn(s) have no message in the stored transcript.")
            # Every absent turn that is not recovered — for ANY reason — keeps the
            # transcript incomplete: a removed guardrail reply the person saw is
            # exactly the turn the crisis-reply check must not silently miss.
            not_fetched, why = 0, []
            if not api_key:
                not_fetched, why = len(unmatched), ["OPENAI_CONVERSATION_API_KEY unset"]
            else:
                for row in unmatched:
                    try:
                        ledger_only.extend(build_from_hydrated([(row, hydrate_row(row, api_key))]))
                    except FetchError as exc:
                        not_fetched += 1
                        why.append(str(exc)[:80])
                        warnings.append(f"Could not hydrate an absent ledger turn ({exc}).")
            if not_fetched:
                incomplete.append(
                    f"{not_fetched} ledger turn(s) absent from the stored transcript and not fetched — "
                    + "; ".join(sorted(set(why)))
                )
    elif rows:
        if not api_key:
            warnings.append("OPENAI_CONVERSATION_API_KEY not set — cannot rebuild from OpenAI responses.")
        else:
            pairs, failed = [], 0
            for row in rows:
                try:
                    pairs.append((row, hydrate_row(row, api_key)))
                except FetchError:
                    failed += 1
            messages = build_from_hydrated(pairs)
            source = "OpenAI responses rebuilt from the llmCall ledger (no opener; user text from input items)"
            if failed:
                warnings.append(f"{failed} of {len(rows)} ledger turn(s) would not fetch — transcript is incomplete.")
                incomplete.append(f"{failed} of {len(rows)} turn(s) would not fetch from OpenAI")

    annotate(messages, handoffs, alert_transaction)
    # Recovered turns keep turn numbers (continuing after the stored transcript)
    # so the crisis-reply check can cite them like any other turn.
    annotate(ledger_only, handoffs, alert_transaction, start=len(messages) + 1)
    for m in ledger_only:
        m["absent_from_stored_transcript"] = True
    orphans = orphan_handoffs(messages + ledger_only, handoffs)
    if orphans:
        incomplete.append(f"{len(orphans)} logged handoff(s) have no reply in any source")
    alert_found = any(m.get("is_alert_turn") for m in messages + ledger_only)
    if alert_transaction and messages and not alert_found:
        warnings.append("The alert transaction matches no assistant message — check the alert turn by time.")

    return {
        "conversation_id": conversation_id,
        "user_id": user_id,
        "alert_transaction": alert_transaction,
        "source": source,
        "analysable": bool(messages),
        "transcript_complete": bool(messages) and not incomplete,
        "incomplete_reasons": incomplete,
        "warnings": warnings,
        "aura_user": fetch_aura_user(user_id),
        "handoffs": handoffs,
        "orphan_handoffs": orphans,
        "ledger_row_count": len(ledger),
        "conversation_row_count": len(rows),
        "messages": messages,
        "ledger_only_messages": ledger_only,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Fetch a whole Aurie conversation for a guardrail alert.")
    ap.add_argument("--conversation-id", required=True, help="Conversation UUID from the alert")
    ap.add_argument("--user-id", help="Firebase UID from the alert (derived from the ledger when omitted)")
    ap.add_argument("--transaction", help="Alert transaction — marks the alert turn")
    ap.add_argument("--json", dest="json_out", help="Also write the structured report here")
    ap.add_argument("--no-gcp", action="store_true", help="Skip the GCP handoff-category lookup")
    ap.add_argument("--turns", help="Print only these turns, e.g. `26-37,45`; `key` = around the guardrail turns "
                                    "(the reviewer's key-turns view — local only, never posted)")
    args = ap.parse_args()

    report = run(args.conversation_id, args.user_id, args.transaction, use_gcp=not args.no_gcp)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, ensure_ascii=False)
    if not report["analysable"]:
        print("# NOT ANALYSABLE — no source produced a transcript. This is NOT a no-risk verdict.")
        for w in report["warnings"]:
            print(f"# {w}")
        return 3
    turns = None
    if args.turns:
        turns = default_key_turns(report) if args.turns == "key" else parse_turns(args.turns)
    print(render(report, turns))
    return 0


if __name__ == "__main__":
    sys.exit(main())
