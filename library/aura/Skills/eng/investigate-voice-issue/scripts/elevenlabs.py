"""ElevenLabs Conversational AI reader for voice-issue investigation.

Covers the one join that makes a voice complaint traceable: an Aurie voice call
carries the **Aura** `conversationId` (and Firebase `userId`) inside
`conversation_initiation_client_data.dynamic_variables` on the ElevenLabs
conversation record. That is what ties a rating to a `conv_…` record, its turn
transcript, and its per-turn latency metrics.

The list endpoint does NOT return dynamic variables, so a match needs a detail
fetch per candidate. Narrowing therefore happens twice in `find_conversation`:
server-side by `user_id` when the caller knows the uid, then client-side by time
window. The uid filter is what keeps the walk `O(that user's calls)` rather than
`O(workspace voice volume)` — without it the detail-fetch budget is spent on
whatever the workspace happened to produce, so on a wide window the target call
is never reached and the search reports a miss it never earned.

Retention: ElevenLabs keeps convai conversation detail for ~10 days. Older
incidents resolve to no record; that is a retention miss, not a missing call.
See `Tools/ElevenLabs` for the plan and retention window (canonical).

Stdlib + requests only. No sibling-skill imports (skills are self-contained).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

try:
    import requests
except ImportError:  # pragma: no cover - dependency guard
    requests = None  # type: ignore[assignment]

API_ROOT = "https://api.elevenlabs.io"
DEFAULT_TIMEOUT = 30
MAX_RETRIES = 5

# Conversation detail is retained ~10 days; anything older cannot be fetched.
RETENTION_DAYS = 10


class ElevenLabsError(Exception):
    """Any ElevenLabs read failure. Callers degrade rather than crash."""


def _load_key() -> str:
    """Resolve the API key.

    `ELEVENLABS_ADMIN_API_KEY` first — the convai conversation endpoints are
    workspace-scoped and the admin key is the one guaranteed to reach them.
    """
    for name in ("ELEVENLABS_ADMIN_API_KEY", "ELEVENLABS_API_KEY"):
        val = os.environ.get(name)
        if val:
            return val
    raise ElevenLabsError(
        "No ElevenLabs key found. Set ELEVENLABS_ADMIN_API_KEY (preferred) or "
        "ELEVENLABS_API_KEY in ~/.claude/settings.json under `env`, then restart Claude Code."
    )


@dataclass
class Turn:
    """One transcript turn, latency metrics flattened out."""

    index: int
    role: str
    time_in_call_secs: Optional[int]
    chars: int
    interrupted: bool
    tools: list[str] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)

    def metric(self, name: str) -> Optional[float]:
        return self.metrics.get(name)


@dataclass
class Conversation:
    """An ElevenLabs convai conversation, with the Aura identifiers resolved."""

    conversation_id: str
    agent_id: Optional[str]
    status: Optional[str]
    start_unix: Optional[int]
    duration_secs: Optional[int]
    message_count: Optional[int]
    termination_reason: Optional[str]
    call_successful: Optional[str]
    aura_conversation_id: Optional[str]
    aura_user_id: Optional[str]
    platform: Optional[str]
    turns: list[Turn] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    @property
    def closed_normally(self) -> bool:
        """A client-initiated close with WebSocket code 1000 is a clean hang-up."""
        return "1000" in (self.termination_reason or "")

    @property
    def end_unix(self) -> Optional[int]:
        """When the call stopped — `None` while it is still running.

        A live record reports `duration_secs` 0, so treating it as a closed span
        would cover only its start instant and every event on it would come back
        unattributable.
        """
        if self.start_unix is None or self.in_progress:
            return None
        return self.start_unix + (self.duration_secs or 0)

    @property
    def in_progress(self) -> bool:
        """Still running at fetch time, so its duration and turn count are not final.

        Worth surfacing rather than rendering: a live call reads `0s / 0 turns`,
        which is indistinguishable from a dead one in a digest.

        Only `in-progress` qualifies. `processing` is a call that has already
        ended and is being post-processed — it has a real duration, and treating
        it as live would give a just-finished call an open-ended span that swallows
        every later event of the session.
        """
        return (self.status or "").lower() == "in-progress"

    def contains(self, when: float) -> bool:
        """Whether an absolute unix instant falls inside this call.

        Open-ended while the call is still running, so an event on a live call is
        attributable rather than falling outside every span.
        """
        if self.start_unix is None or when < self.start_unix:
            return False
        end = self.end_unix
        return True if end is None else when <= end


class ElevenLabsClient:
    """Thin wrapper over the convai conversation endpoints, with 429 backoff."""

    def __init__(self, key: str | None = None) -> None:
        if requests is None:
            raise ElevenLabsError(
                "The `requests` package is required. Install with: uv pip install --python ~/.claude/python-env/bin/python requests"
            )
        self.key = key or _load_key()
        self.session = requests.Session()
        self.session.headers.update({"xi-api-key": self.key, "Accept": "application/json"})

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = path if path.startswith("http") else f"{API_ROOT}{path}"
        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                r = self.session.get(url, params=params, timeout=DEFAULT_TIMEOUT)
            except Exception as e:  # requests.RequestException + socket errors
                last_exc = e
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 429:
                retry_after = r.headers.get("Retry-After")
                try:
                    wait = float(retry_after) if retry_after else 2 ** attempt
                except (TypeError, ValueError):
                    wait = 2 ** attempt
                time.sleep(min(wait, 60))
                continue
            if r.status_code == 401:
                raise ElevenLabsError(
                    "ElevenLabs returned 401. Check ELEVENLABS_ADMIN_API_KEY — it may be "
                    "expired, revoked, or scoped to the wrong workspace."
                )
            if r.status_code == 404:
                raise ElevenLabsError(f"ElevenLabs 404 (not found): {url}")
            if r.status_code >= 400:
                raise ElevenLabsError(f"ElevenLabs HTTP {r.status_code} for {url}")
            return r.json()
        if last_exc:
            raise ElevenLabsError(f"Network failure after {MAX_RETRIES} retries: {last_exc}")
        raise ElevenLabsError(f"Rate-limited too many times: {url}")

    def iter_conversation_summaries(
        self,
        *,
        agent_id: str | None = None,
        user_id: str | None = None,
        start_unix: int | None = None,
        end_unix: int | None = None,
        page_size: int = 100,
        max_pages: int = 10,
        progress: dict[str, Any] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield conversation summaries newest-first, following the cursor.

        Every narrowing here is server-side and all of it is best-effort:
        `user_id` scopes to one user, `call_start_after_unix` / `_before_unix`
        scope to the window. None of it is a correctness boundary — ElevenLabs
        ignores unknown query params without erroring, so callers still verify
        identity and timing on the detail record.

        The time bounds matter as much as the uid for a *bounded* search. Without
        them the walk starts at the newest workspace conversation and has to page
        forward to reach an older window, which at ~1,000 summaries per
        `max_pages` budget it may never do — reaching the page limit having
        examined nothing in range.

        `progress` is how that stop becomes visible: the generator sets
        `progress["page_limit_hit"] = True` when it returns because it ran out of
        page budget rather than out of conversations. A caller that cannot see
        the difference will report "searched to exhaustion" about a walk that
        never arrived.
        """
        if progress is not None:
            progress["page_limit_hit"] = False
            progress["pages_read"] = 0
        params: dict[str, Any] = {"page_size": page_size}
        if agent_id:
            params["agent_id"] = agent_id
        if user_id:
            params["user_id"] = user_id
        if start_unix is not None:
            params["call_start_after_unix"] = start_unix
        if end_unix is not None:
            params["call_start_before_unix"] = end_unix
        pages = 0
        while pages < max_pages:
            payload = self._get("/v1/convai/conversations", params)
            for item in payload.get("conversations", []):
                yield item
            pages += 1
            if progress is not None:
                progress["pages_read"] = pages
            cursor = payload.get("next_cursor")
            if not cursor or not payload.get("has_more"):
                return
            params = dict(params)
            params["cursor"] = cursor
        # Fell out of the loop with the cursor still live: more conversations
        # exist than the page budget allowed. Not exhaustion.
        if progress is not None:
            progress["page_limit_hit"] = True

    def get_conversation(self, conversation_id: str) -> Conversation:
        return _parse_conversation(self._get(f"/v1/convai/conversations/{conversation_id}"))

    def find_conversation(
        self,
        *,
        aura_conversation_id: str | None = None,
        user_id: str | None = None,
        start_unix: int | None = None,
        end_unix: int | None = None,
        agent_id: str | None = None,
        max_detail_fetches: int = 40,
    ) -> "ConversationSearch":
        """Find the call matching an Aura conversation id and/or Firebase uid.

        Summaries carry no dynamic variables, so identity is only knowable from
        detail. Two narrowings keep that affordable: `user_id` server-side (when
        known), then `start_time_unix_secs` against [start_unix, end_unix].
        `max_detail_fetches` is the backstop if the server-side filter is not in
        effect.

        Returns a `ConversationSearch`, never a bare `Optional`, because "no call
        exists" and "we stopped looking" are different answers and only one of
        them is a finding. Read `.conversation`, but branch on `.capped` before
        reporting a miss.
        """
        if not aura_conversation_id and not user_id:
            raise ValueError("find_conversation needs aura_conversation_id or user_id")
        want_conv = (aura_conversation_id or "").strip().lower()
        fetched = 0
        foreign = 0
        capped = False
        matches: list[Conversation] = []
        progress: dict[str, Any] = {}
        for summary in self.iter_conversation_summaries(
            agent_id=agent_id,
            user_id=user_id,
            start_unix=start_unix,
            end_unix=end_unix,
            progress=progress,
        ):
            started = summary.get("start_time_unix_secs")
            if start_unix is not None and (started is None or started < start_unix):
                # Summaries are newest-first, so anything older than the window
                # start means every later page is older too.
                break
            if end_unix is not None and started is not None and started > end_unix:
                continue
            if fetched >= max_detail_fetches:
                capped = True
                break
            cid = summary.get("conversation_id")
            if not cid:
                continue
            fetched += 1
            try:
                conv = self.get_conversation(cid)
            except ElevenLabsError:
                continue
            if user_id and conv.aura_user_id and conv.aura_user_id != user_id:
                # Asked to narrow by uid and got somebody else back: the server-side
                # filter is not in effect. Correctness is unaffected (identity is
                # decided here, on detail) — only the walk gets longer.
                foreign += 1
            if want_conv and (conv.aura_conversation_id or "").lower() == want_conv:
                matches.append(conv)
            elif not want_conv and user_id and conv.aura_user_id == user_id:
                matches.append(conv)
        # Oldest-first, so the list reads as the session did.
        matches.sort(key=lambda c: c.start_unix or 0)
        sessions = _session_groups(matches)
        if not want_conv and len(sessions) > 1:
            # Asked only for a uid, so the window can hold several unrelated
            # conversations. Handing them back as one set would make the
            # reconnect count, the dead-call check and the latency aggregate
            # describe a mix of sessions rather than the one complained about.
            matches = max(
                sessions,
                key=lambda g: (sum(c.duration_secs or 0 for c in g), len(g)),
            )
            matches.sort(key=lambda c: c.start_unix or 0)
        return ConversationSearch(matches, fetched, capped, user_id, foreign,
                                  bool(progress.get("page_limit_hit")),
                                  len(sessions))


@dataclass
class ConversationSearch:
    """Outcome of a `find_conversation` walk — the match *and* how hard it looked.

    Exists so a caller cannot accidentally report a give-up as an absence. Those
    two outcomes are indistinguishable in a bare `Optional[Conversation]`, and
    they point a reader at opposite conclusions: one says the complaint has no
    vendor record, the other says the search ran out of budget.
    """

    conversations: list[Conversation]
    detail_fetches: int
    capped: bool
    requested_user_id: Optional[str]
    foreign_details: int
    page_limit_hit: bool = False
    # How many distinct Aura conversations the walk saw. >1 means a uid-only
    # search narrowed to one session and the others were left out — worth
    # reporting, because a silently narrowed corpus reads like the whole one.
    sessions_seen: int = 1

    @property
    def conversation(self) -> Optional[Conversation]:
        """The substantive call of the session — the longest one.

        One Aura conversation routinely spans several vendor calls (a reconnect
        mints a new `conv_…` carrying the same `conversationId`), so there is no
        single "the" call. The longest is the one a one-line summary should name;
        anything that reasons about *when* something happened must use
        `conversations` instead, or it will judge an event against a call that
        was not running at the time.
        """
        if not self.conversations:
            return None
        return max(self.conversations, key=lambda c: c.duration_secs or 0)

    @property
    def found(self) -> bool:
        return bool(self.conversations)

    @property
    def reconnects(self) -> int:
        """Extra calls beyond the first — the fragmentation the user felt."""
        return max(len(self.conversations) - 1, 0)

    @property
    def exhaustive(self) -> bool:
        """True only if the search ran out of conversations, not out of budget.

        There are two budgets and either can run out first — the detail-fetch cap
        and the list page limit. Only an exhaustive search licenses the claim that
        no matching call exists.
        """
        return not self.capped and not self.page_limit_hit

    @property
    def server_side_user_filter_effective(self) -> Optional[bool]:
        """None when no uid was requested, else whether the narrowing held.

        `foreign_details` counts details that came back belonging to a different
        user than the one asked for. Any at all means the list was not filtered
        — ElevenLabs ignores unknown query params silently, so a renamed
        parameter shows up here rather than as an error.
        """
        if not self.requested_user_id:
            return None
        return self.foreign_details == 0

    def miss_reason(self, window_label: str, retention_days: int) -> str:
        """Why nothing matched, phrased so a give-up never reads as an absence."""
        if self.capped:
            return (
                f"searched {self.detail_fetches} candidates in {window_label} and hit the "
                f"detail-fetch cap before matching — this is NOT evidence the call is absent; "
                f"raise --max-detail-fetches or narrow the window"
            )
        if self.page_limit_hit:
            return (
                f"ran out of list pages before reaching the end of {window_label} "
                f"({self.detail_fetches} candidates examined) — this is NOT evidence the call "
                f"is absent; narrow the window with --at so fewer pages are needed"
            )
        if self.server_side_user_filter_effective is False:
            return (
                f"no match in {window_label} after {self.detail_fetches} candidates; the "
                f"server-side user filter was not in effect, so the search was workspace-wide "
                f"and may have been truncated by retention (~{retention_days} days)"
            )
        return (
            f"no match in {window_label} (searched {self.detail_fetches} candidates to "
            f"exhaustion; retention is ~{retention_days} days)"
        )


def _parse_conversation(detail: dict[str, Any]) -> Conversation:
    meta = detail.get("metadata") or {}
    init = detail.get("conversation_initiation_client_data") or {}
    dyn = init.get("dynamic_variables") or {}
    turns: list[Turn] = []
    for i, t in enumerate(detail.get("transcript") or []):
        raw_metrics = ((t.get("conversation_turn_metrics") or {}).get("metrics")) or {}
        metrics: dict[str, float] = {}
        for name, payload in raw_metrics.items():
            elapsed = (payload or {}).get("elapsed_time")
            if isinstance(elapsed, (int, float)):
                metrics[name] = float(elapsed)
        turns.append(
            Turn(
                index=i,
                role=t.get("role") or "?",
                time_in_call_secs=t.get("time_in_call_secs"),
                chars=len(t.get("message") or ""),
                interrupted=bool(t.get("interrupted")),
                tools=[tc.get("tool_name") for tc in (t.get("tool_calls") or []) if tc.get("tool_name")],
                metrics=metrics,
            )
        )
    return Conversation(
        conversation_id=detail.get("conversation_id") or "?",
        agent_id=detail.get("agent_id"),
        status=detail.get("status"),
        start_unix=meta.get("start_time_unix_secs"),
        duration_secs=meta.get("call_duration_secs"),
        message_count=len(turns) or meta.get("message_count"),
        termination_reason=meta.get("termination_reason"),
        call_successful=detail.get("analysis", {}).get("call_successful")
        if isinstance(detail.get("analysis"), dict)
        else None,
        aura_conversation_id=dyn.get("conversationId"),
        aura_user_id=dyn.get("userId"),
        platform=dyn.get("platform"),
        turns=turns,
        raw=detail,
    )


def _session_groups(convs: list[Conversation]) -> list[list[Conversation]]:
    """Split calls into one group per Aura conversation.

    A call with no Aura conversation id cannot be grouped with anything, so it
    forms its own group rather than being pooled with the other unidentified
    ones.
    """
    groups: dict[str, list[Conversation]] = {}
    for c in convs:
        key = (c.aura_conversation_id or f"__{c.conversation_id}").lower()
        groups.setdefault(key, []).append(c)
    return list(groups.values())


def call_containing(convs: list[Conversation], when: float) -> Optional[Conversation]:
    """The call that was running at an absolute unix instant, if any.

    An event can only be judged against the call it happened in. Checking it
    against some other call of the same session compares it to turns that were
    never on its wire, which fabricates both verdicts: absent turns read as a
    stall, and another call's turns clear a real one.
    """
    covering = [c for c in convs if c.contains(when)]
    if not covering:
        return None
    # Spans overlap when a record never closed — precisely the zombie-session
    # shape this skill investigates. The most recently started call is the one
    # the user was actually on, so a stale record cannot answer for it.
    return max(covering, key=lambda c: c.start_unix or 0)


def agent_turns_between(conv: Conversation, start_unix: float, end_unix: float) -> list[Turn]:
    """Agent turns whose start falls inside an absolute [start, end] unix window.

    This is what decides whether a client-side reply watchdog was a false
    positive: an agent turn inside the watchdog window means a reply did arrive.
    """
    if conv.start_unix is None:
        return []
    out = []
    for t in conv.turns:
        if t.role != "agent" or t.time_in_call_secs is None:
            continue
        absolute = conv.start_unix + t.time_in_call_secs
        if start_unix <= absolute <= end_unix:
            out.append(t)
    return out
