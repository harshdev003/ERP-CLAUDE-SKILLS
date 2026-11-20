"""Renderers for the voice-issue digest.

Every value that reaches stdout passes through `correlate`'s redaction helpers
first — uid prefix only, audio device names stripped, and no transcript text at
any point. The digest is safe to paste into Slack / Linear as-is.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional, Sequence

import correlate
from correlate import (
    FELT_METRIC,
    LatencyReport,
    OurLeg,
    Verdict,
    WatchdogVerdict,
    redact_audio_route,
    short_id,
    uid_prefix,
)
from elevenlabs import RETENTION_DAYS, Conversation

GRADE_MARK = {"ok": "🟢", "degraded": "🟡", "bad": "🔴", "unknown": "⚪"}
METRIC_LABEL = {
    "convai_ttf_audio_since_silence": "Time to first audio (felt)",
    "convai_llm_service_ttf_sentence": "LLM to first sentence",
    "convai_llm_service_tt_last_sentence": "LLM to last sentence",
    "convai_tts_service_ttfb": "TTS time to first byte",
    "convai_asr_trailing_service_latency": "ASR trailing (turn-taking)",
}


def _clock(unix: Optional[float]) -> str:
    if not unix:
        return "?"
    return datetime.fromtimestamp(float(unix), timezone.utc).strftime("%H:%M:%SZ")


def _stamp(unix: Optional[float]) -> str:
    if not unix:
        return "?"
    return datetime.fromtimestamp(float(unix), timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def render_header(*, user_id: str | None, aura_conversation_id: str | None, window: str) -> str:
    lines = ["# Voice issue investigation", ""]
    lines.append(f"- **User:** `{uid_prefix(user_id)}`")
    if aura_conversation_id:
        lines.append(f"- **Conversation:** `{short_id(aura_conversation_id)}`")
    lines.append(f"- **Window:** {window}")
    return "\n".join(lines)


def render_verdict(verdict: Verdict) -> str:
    lines = ["", "## Verdict", ""]
    lines.append(f"**User-perceived symptom** — {verdict.symptom}")
    lines.append("")
    if verdict.artifacts:
        lines.append("**Instrumentation artifacts** (present, but not the symptom):")
        for a in verdict.artifacts:
            lines.append(f"- {a}")
        lines.append("")
    if verdict.session_health:
        lines.append(f"**Session health** — {verdict.session_health}")
        lines.append("")
    lines.append(f"**Confidence** — {verdict.confidence}")
    return "\n".join(lines)


def render_vendor_call(convs: Sequence[Conversation]) -> str:
    """The session as ElevenLabs recorded it — every call, not just one.

    One Aura conversation routinely spans several vendor calls, and the
    fragmentation is usually the symptom rather than a detail: a reader who sees
    only one record cannot tell a single clean call from nine reconnects.
    """
    convs = sorted(convs, key=lambda c: c.start_unix or 0)
    lines = ["", "## Vendor call (ElevenLabs)", ""]
    if not convs:
        lines.append(
            f"No matching conversation resolved. Either the incident is outside the "
            f"~{RETENTION_DAYS}-day retention window, the call never reached ElevenLabs, "
            "or the search window was too narrow."
        )
        return "\n".join(lines)

    if len(convs) > 1:
        total = sum(c.duration_secs or 0 for c in convs)
        turns = sum(len(c.turns) for c in convs)
        lines.append(
            f"**{len(convs)} calls** on this Aura conversation — {len(convs) - 1} reconnect(s), "
            f"{total}s and {turns} turns in total."
        )
        lines.append("")
        lines.append("| # | Record | Started | Dur | Turns | Termination |")
        lines.append("|---:|---|---|---:|---:|---|")
        for i, c in enumerate(convs, 1):
            agent_turns = sum(1 for t in c.turns if t.role == "agent")
            # A live call has no final duration or turn count; rendering its zeros
            # reads exactly like a dead one.
            if c.in_progress:
                dur, count = "in progress", "—"
            else:
                dur = f"{c.duration_secs}s"
                count = f"{len(c.turns)}"
                if not agent_turns and (c.duration_secs or 0) >= correlate.DEAD_CALL_MIN_SECS:
                    count = f"**{len(c.turns)}** ⚠️"
            lines.append(
                f"| {i} | `{c.conversation_id}` | {_stamp(c.start_unix)} | {dur} | {count} "
                f"| {c.termination_reason or '?'} |"
            )
        lines.append("")
        lines.append(
            "_⚠️ marks a call that ran past "
            f"{correlate.DEAD_CALL_MIN_SECS}s with no agent turn at all — the user was "
            "connected with nothing coming back._"
        )
        lines.append("")
        lines.append("Longest call:")

    conv = max(convs, key=lambda c: c.duration_secs or 0)
    lines.append(f"- **Record:** `{conv.conversation_id}`")
    lines.append(f"- **Started:** {_stamp(conv.start_unix)}")
    if conv.in_progress:
        lines.append("- **Duration:** still in progress at fetch time — duration and turn count are not final")
    else:
        lines.append(f"- **Duration:** {conv.duration_secs}s over {len(conv.turns)} turns")
    lines.append(f"- **Status:** {conv.status} | **Termination:** {conv.termination_reason or '?'}")
    lines.append(f"- **Platform:** {conv.platform or '?'} | **Agent:** `{conv.agent_id or '?'}`")
    if conv.aura_user_id:
        lines.append(f"- **Joined via** `dynamic_variables.conversationId` → matched `{uid_prefix(conv.aura_user_id)}`")
    return "\n".join(lines)


def render_latency(report: LatencyReport) -> str:
    lines = ["", "## Turn latency", ""]
    if not report.findings:
        lines.append("No per-turn latency metrics on this call.")
        return "\n".join(lines)
    lines.append("| Metric | Worst | At | Grade |")
    lines.append("|---|---:|---:|:--:|")
    for f in report.findings:
        label = METRIC_LABEL.get(f.metric, f.metric)
        mark = GRADE_MARK.get(f.grade, "")
        at = f"{f.at_secs}s" if f.at_secs is not None else "?"
        grade = f"{mark} {f.grade}" if f.metric == FELT_METRIC else "—"
        lines.append(f"| {label} | {f.worst}s | {at} | {grade} |")
    lines.append("")
    lines.append(
        f"_Only time-to-first-audio is graded — it is what the user hears. "
        f"Thresholds: ≥{correlate.FELT_LATENCY_DEGRADED}s degraded, "
        f"≥{correlate.FELT_LATENCY_BAD}s bad (see `references/signals.md`)._"
    )
    if report.worst_turns:
        lines.append("")
        lines.append("Worst turns by felt latency:")
        for t in report.worst_turns:
            value = t.metric(FELT_METRIC)
            lines.append(
                f"- turn {t.index} at {t.time_in_call_secs}s — {round(value, 2) if value else '?'}s"
                + (" (interrupted)" if t.interrupted else "")
            )
    return "\n".join(lines)


def render_watchdogs(watchdogs: list[WatchdogVerdict]) -> str:
    lines = ["", "## Reply watchdog events", ""]
    if not watchdogs:
        lines.append("No reply-watchdog events for this user in the window.")
        return "\n".join(lines)
    for w in watchdogs:
        badge = {"false-positive": "🟡 false positive", "genuine": "🔴 genuine stall", "unknown": "⚪ unverifiable"}
        lines.append(f"- **{badge.get(w.verdict, w.verdict)}** — fired {_clock(w.fired_at)}")
        lines.append(
            f"  waited {w.waited_secs}s from {_clock(w.reply_start)}, "
            f"`agentState={w.agent_state or '?'}`, agent turns in window: {w.agent_turns_in_window}"
        )
        lines.append(f"  {w.detail}")
    return "\n".join(lines)


def render_client_errors(rows: list[dict[str, Any]], *, event_context: dict[str, Any] | None) -> str:
    """Bugsnag signatures the user hit, each with org-wide scale for context."""
    lines = ["", "## Client errors (Bugsnag)", ""]
    if not rows:
        lines.append("No Bugsnag errors for this user in the window.")
    else:
        lines.append("| Error | User | Org-wide (same window) | Reading |")
        lines.append("|---|---:|---:|---|")
        for r in rows:
            scale = r.get("org_events")
            users = r.get("org_users")
            scale_cell = f"{scale} ev / {users} users" if scale is not None else "?"
            lines.append(
                f"| {r.get('error_class')}: {r.get('message')} | {r.get('user_events', '?')} ev "
                f"| {scale_cell} | {r.get('reading', '')} |"
            )
        lines.append("")
        lines.append(
            "_Org-wide counts are the point: a signature that fires for hundreds of users is "
            "endemic and rarely explains one complaint on its own._"
        )
    if event_context:
        lines.append("")
        lines.append("Device / audio context from the user's own event:")
        lines.append(f"- app `{event_context.get('app_version') or '?'}` on {event_context.get('device') or '?'}")
        lines.append(
            f"- audio out `{redact_audio_route(event_context.get('route_outputs'))}` "
            f"at {event_context.get('sample_rate') or '?'}Hz "
            f"(preferred {event_context.get('preferred_sample_rate') or '?'}Hz)"
        )
        if event_context.get("sample_rate_mismatch"):
            lines.append(
                "- ⚠️ sample-rate mismatch — a Bluetooth hands-free route can force a lower rate "
                "than the agent audio format. Contributory for this user; check whether it generalises "
                "before treating it as the cause."
            )
        startup = event_context.get("startup_total")
        if startup:
            try:
                startup = f"{float(startup):.2f}"
            except (TypeError, ValueError):
                pass
            lines.append(f"- session startup {startup}s")
    return "\n".join(lines)


def render_our_leg(leg: OurLeg, *, truncated: bool = False) -> str:
    lines = ["", "## Our leg (Aura prod logs)", ""]
    if not leg.entry_count:
        lines.append("No prod log entries for this user in the window.")
        return "\n".join(lines)
    mark = GRADE_MARK.get(leg.grade, "")
    lines.append(f"- **Voice path:** {leg.voice_path}")
    lines.append(
        f"- **Aura LLM response:** median {leg.median_openai_secs}s, worst "
        f"{leg.worst_openai_secs}s {mark} {leg.grade} ({len(leg.openai_ms)} calls)"
    )
    lines.append(f"- **Log entries:** {leg.entry_count} ({leg.first_ts or '?'} → {leg.last_ts or '?'})")
    if truncated:
        # The cap is reported under Sources, but the numbers above are what a
        # reader quotes — so the caveat has to travel with them.
        lines.append(
            "- ⚠️ the entry cap was hit, so every figure in this section is computed from a "
            "**partial** read of the window — treat them as lower bounds, not the worst case"
        )
    if leg.elevenlabs_token_failed:
        lines.append("- ⚠️ a conversation-token call failed — the client could not start a voice session")
    if leg.experiments:
        interesting = {k: v for k, v in leg.experiments.items() if "livekit" in k.lower() or "voice" in k.lower()}
        if interesting:
            arms = ", ".join(f"`{k}={v}`" for k, v in sorted(interesting.items()))
            lines.append(f"- **Voice-relevant experiment arms:** {arms}")
    if leg.errors:
        lines.append("- **Errors logged:**")
        for e in leg.errors[:5]:
            lines.append(f"  - {e}")
    return "\n".join(lines)


def render_sources(sources: dict[str, str]) -> str:
    lines = ["", "## Sources", ""]
    for name, status in sources.items():
        lines.append(f"- **{name}** — {status}")
    return "\n".join(lines)


def render_next_steps(*, user_id: str | None, convs: Sequence[Conversation]) -> str:
    lines = ["", "→ Drill in:", ""]
    lines.append(f"   `search bugsnag by user {uid_prefix(user_id)}` (full error list — eng:search-bugsnag-by-user)")
    lines.append(f"   `search gcp by user {uid_prefix(user_id)}` (full log trace — eng:search-gcp-by-user)")
    if convs:
        lines.append("   `analyze elevenlabs latency` (is this call typical? — eng:analyze-elevenlabs-latency)")
    return "\n".join(lines)
