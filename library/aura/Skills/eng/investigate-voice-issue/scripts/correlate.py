"""Correlation + classification for a voice complaint.

Holds the judgement calls that turn four raw sources into one verdict, and keeps
them in one place so the model does not re-derive them per run:

- Which client-side errors are *instrumentation artifacts* rather than symptoms.
- Whether a reply watchdog that fired was a false positive.
- Which turn latencies are bad enough to be what the user actually felt.
- Redaction, applied before anything is rendered.

Thresholds are heuristics, not vendor SLOs — see `references/signals.md` for how
they were anchored and when to revisit them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from elevenlabs import Conversation, Turn, agent_turns_between, call_containing

# ---------------------------------------------------------------------------
# Known instrumentation artifacts
# ---------------------------------------------------------------------------

# The iOS client arms a fixed-duration watchdog when it expects an agent reply.
# It is not cancelled when the reply lands, so it fires on healthy calls and is
# the loudest error in the project — never report it as "the agent hung" without
# checking the vendor transcript for agent turns inside the watchdog window.
# Tracked in PROD-4395.
WATCHDOG_MESSAGE = "Conversation Reply Timeout Exceeded"

# Client errors that describe the transport rather than the agent's behaviour.
# Present them as session-health context, not as the user's symptom.
TRANSPORT_MESSAGES = ("Conversation is not connected.", "Network Request timed out.")

# ---------------------------------------------------------------------------
# Latency thresholds (seconds)
# ---------------------------------------------------------------------------

# `convai_ttf_audio_since_silence` is the felt number: how long after the user
# stops talking before any agent audio plays. The others are its components.
FELT_LATENCY_DEGRADED = 3.0
FELT_LATENCY_BAD = 5.0

# Our own leg — the Aura LLM response time seen in the GCP logs.
OUR_LEG_DEGRADED = 2.5
OUR_LEG_BAD = 4.0

FELT_METRIC = "convai_ttf_audio_since_silence"
LLM_FIRST_METRIC = "convai_llm_service_ttf_sentence"
LLM_LAST_METRIC = "convai_llm_service_tt_last_sentence"
TTS_METRIC = "convai_tts_service_ttfb"
ASR_METRIC = "convai_asr_trailing_service_latency"

LATENCY_METRICS = (FELT_METRIC, LLM_FIRST_METRIC, LLM_LAST_METRIC, TTS_METRIC, ASR_METRIC)


# ---------------------------------------------------------------------------
# Redaction (applied before render — see Compliance/Privacy)
# ---------------------------------------------------------------------------

def uid_prefix(uid: str | None, length: int = 8) -> str:
    """Shorten a Firebase uid for any external-facing output."""
    if not uid:
        return "?"
    return uid[:length] + "…" if len(uid) > length else uid


def redact_audio_route(route: str | None) -> str:
    """Keep the port type, drop the device name.

    A Bluetooth route renders as `BluetoothHFP:<owner>'s AirPods` — the device
    name is a person's name and must not reach a report. The port type is the
    diagnostic half, so keep it and replace the rest.
    """
    if not route:
        return "?"
    parts = []
    for chunk in route.split(","):
        port, _, name = chunk.strip().partition(":")
        parts.append(f"{port}:<device>" if name else port)
    return ",".join(parts)


_UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")


def short_id(value: str | None) -> str:
    """First segment of a UUID-ish id — enough to correlate, short enough to read."""
    if not value:
        return "?"
    return value.split("-")[0].lower()


# ---------------------------------------------------------------------------
# Watchdog classification
# ---------------------------------------------------------------------------

@dataclass
class WatchdogVerdict:
    """Whether a fired reply watchdog reflects a real stall."""

    verdict: str  # "false-positive" | "genuine" | "unknown"
    fired_at: Optional[float]
    reply_start: Optional[float]
    waited_secs: Optional[float]
    agent_state: Optional[str]
    agent_turns_in_window: int
    detail: str


def classify_watchdog(
    meta: dict[str, Any], convs: Sequence[Conversation]
) -> WatchdogVerdict:
    """Decide whether a watchdog event describes a stall the user could feel.

    The window is [replyStartTime, timestamp] from the event's own metadata. An
    agent turn inside it means the vendor delivered a reply while the client was
    still counting — the watchdog fired anyway, so it is a false positive.

    Takes *every* call of the session and picks the one that was running when the
    watchdog armed. One Aura conversation spans many vendor calls, so a single
    call cannot answer for all of them: judged against the wrong call, an event
    that never touched it finds no turns and is reported as a genuine stall at
    high confidence. When no matched call was running, the answer is `unknown` —
    never `genuine`, which the digest treats as evidence.
    """
    fired = _as_float(meta.get("timestamp"))
    started = _as_float(meta.get("replyStartTime"))
    state = meta.get("agentState")
    waited = round(fired - started, 1) if (fired and started) else None

    if not convs:
        return WatchdogVerdict(
            "unknown", fired, started, waited, state, 0,
            "No matching vendor conversation, so the window cannot be checked for agent turns.",
        )
    if fired is None or started is None:
        return WatchdogVerdict(
            "unknown", fired, started, waited, state, 0,
            "Event metadata carries no replyStartTime/timestamp pair to bound the window.",
        )

    # The watchdog arms when the user stops speaking, which happens on a live
    # call — so the call containing `replyStartTime` is the one that owns it.
    conv = call_containing(list(convs), started)
    if conv is None:
        return WatchdogVerdict(
            "unknown", fired, started, waited, state, 0,
            f"The {waited}s window falls outside every matched call, so no transcript "
            f"covers it — most likely it belongs to a call the search did not reach.",
        )

    turns = agent_turns_between(conv, started, fired)
    if turns:
        return WatchdogVerdict(
            "false-positive", fired, started, waited, state, len(turns),
            f"The agent produced {len(turns)} turn(s) inside the {waited}s window of "
            f"`{conv.conversation_id}`, and the call continued — so a reply did arrive "
            f"while the watchdog was counting.",
        )
    return WatchdogVerdict(
        "genuine", fired, started, waited, state, 0,
        f"No agent turn inside the {waited}s window of `{conv.conversation_id}` — the "
        f"agent really did go quiet here.",
    )


def _as_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Latency assessment
# ---------------------------------------------------------------------------

@dataclass
class LatencyFinding:
    """The worst observation for one metric across a call."""

    metric: str
    worst: float
    at_secs: Optional[int]
    turn_index: Optional[int]
    grade: str  # "ok" | "degraded" | "bad"


@dataclass
class LatencyReport:
    findings: list[LatencyFinding] = field(default_factory=list)
    worst_turns: list[Turn] = field(default_factory=list)
    verdict: str = "ok"

    def by_metric(self, name: str) -> Optional[LatencyFinding]:
        for f in self.findings:
            if f.metric == name:
                return f
        return None


def grade_felt(value: float) -> str:
    if value >= FELT_LATENCY_BAD:
        return "bad"
    if value >= FELT_LATENCY_DEGRADED:
        return "degraded"
    return "ok"


# A call shorter than this is an ordinary open-and-close; longer with no agent
# turn at all is a user sitting in a live call getting nothing back. Measured
# org-wide: 61 of 69 zero-message calls in a two-day window were under 30s.
DEAD_CALL_MIN_SECS = 60


def assess_latency(convs: Sequence[Conversation], top_n: int = 3) -> LatencyReport:
    """Worst value per metric plus the turns that carried the worst felt latency.

    Spans every call of the session: the user rated the session, not one leg of
    it, and the worst turn they sat through may be in any of the calls.
    """
    report = LatencyReport()
    turns_all = [t for c in convs for t in c.turns]
    if not turns_all:
        return report
    for metric in LATENCY_METRICS:
        best: Optional[LatencyFinding] = None
        for t in turns_all:
            value = t.metric(metric)
            if value is None:
                continue
            if best is None or value > best.worst:
                best = LatencyFinding(
                    metric=metric,
                    worst=round(value, 2),
                    at_secs=t.time_in_call_secs,
                    turn_index=t.index,
                    # Only the felt metric has a calibrated grade; the components
                    # are reported to explain it, not judged on their own.
                    grade=grade_felt(value) if metric == FELT_METRIC else "ok",
                )
        if best:
            report.findings.append(best)

    felt = report.by_metric(FELT_METRIC)
    report.verdict = felt.grade if felt else "unknown"
    rated = [t for t in turns_all if t.metric(FELT_METRIC) is not None]
    report.worst_turns = sorted(rated, key=lambda t: t.metric(FELT_METRIC) or 0, reverse=True)[:top_n]
    return report


# ---------------------------------------------------------------------------
# GCP leg (our own latency + which voice path the user was on)
# ---------------------------------------------------------------------------

_EXEC_TIME_RE = re.compile(r"Execution time for openai response:\s*(\d+)\s*ms", re.I)
_EXPERIMENT_RE = re.compile(r"Assigned experiment '([^']+)' value '([^']+)'")

# The LiveKit-integration experiment is re-cut under a new trailing version every
# time it restarts (…2 ended 2026-08-06, …3 is live, …4 is planned), so the arm is
# found by family rather than by a pinned name. A pinned name misses silently: the
# capture above populates whatever version the logs carry, and only the lookup goes
# stale, so `voice_path` falls through to "unknown" on exactly the LiveKit arm it
# exists to explain. Every member of the family must resolve, the unreleased one
# included.
_LIVEKIT_EXPERIMENT_PREFIX = "ailivekitintegration"
_TRAILING_VERSION_RE = re.compile(r"(\d+)$")


def newest_livekit_experiment(
    experiments: dict[str, str], stamps: Optional[dict[str, str]] = None
) -> Optional[tuple[str, str]]:
    """The LiveKit-integration assignment in force, as `(name, arm)`.

    Ordered by assignment timestamp first, which is the direct answer to *which arm
    was in force for this session*. RFC3339 stamps sort lexicographically, so a
    string compare is a time compare, and a stamped name beats an unstamped one —
    the same rule `summarize_our_leg` applies within a single experiment name.

    The trailing version number is only the tiebreak, for names whose assignment
    carried no stamp. Ordering on version alone would work today, because the cuts
    have launched sequentially (…2 ended 2026-08-06, …3 started 2026-08-11), but it
    would bake in a non-overlap assumption that a future …4 overlapping its
    predecessor breaks silently — and nobody would remember it was made.

    Per-name arm resolution already happened in `summarize_our_leg`, which keeps the
    newest assignment per experiment name.
    """
    matches = [n for n in experiments if n.lower().startswith(_LIVEKIT_EXPERIMENT_PREFIX)]
    if not matches:
        return None
    stamps = stamps or {}

    def in_force(name: str) -> tuple[str, int, str]:
        m = _TRAILING_VERSION_RE.search(name)
        return (stamps.get(name, ""), int(m.group(1)) if m else -1, name)

    newest = max(matches, key=in_force)
    return newest, experiments[newest]


@dataclass
class OurLeg:
    """What our own services contributed, read from the prod logs."""

    openai_ms: list[int] = field(default_factory=list)
    experiments: dict[str, str] = field(default_factory=dict)
    # When each experiment's winning assignment was stamped. Kept on the leg rather
    # than local to the fold so `voice_path` can ask which of two family members was
    # in force instead of inferring it from their version numbers.
    experiment_stamps: dict[str, str] = field(default_factory=dict)
    elevenlabs_token_issued: bool = False
    elevenlabs_token_failed: bool = False
    voice_limits_resolved: int = 0
    errors: list[str] = field(default_factory=list)
    entry_count: int = 0
    first_ts: Optional[str] = None
    last_ts: Optional[str] = None
    # True when the fetch hit its entry cap, so the window is only partly read and
    # an absent signal (no slow call, no token fetch) proves nothing.
    truncated: bool = False

    @property
    def worst_openai_secs(self) -> Optional[float]:
        return round(max(self.openai_ms) / 1000, 2) if self.openai_ms else None

    @property
    def median_openai_secs(self) -> Optional[float]:
        if not self.openai_ms:
            return None
        ordered = sorted(self.openai_ms)
        return round(ordered[len(ordered) // 2] / 1000, 2)

    @property
    def grade(self) -> str:
        worst = self.worst_openai_secs
        if worst is None:
            return "unknown"
        if worst >= OUR_LEG_BAD:
            return "bad"
        if worst >= OUR_LEG_DEGRADED:
            return "degraded"
        return "ok"

    @property
    def voice_path(self) -> str:
        """Which voice stack served this user.

        The LiveKit-integration assignment names the experiment arm; the 11Labs
        token call is the direct evidence of which path actually ran, so it wins
        when the two disagree. The arm is matched by family and the name that
        answered is reported, so the readout says which experiment it came from.
        """
        if self.elevenlabs_token_issued:
            return "ElevenLabs (conversation token issued)"
        found = newest_livekit_experiment(self.experiments, self.experiment_stamps)
        if found:
            name, arm = found
            return f"unclear — {name}={arm}, no 11Labs token call seen"
        return "unknown"


def summarize_our_leg(normalized_entries: list[Any]) -> OurLeg:
    """Fold normalized GCP log entries into the our-side picture.

    Order-independent by construction: entries may arrive newest- or oldest-first,
    so nothing here may depend on which write lands last. An experiment arm is
    resolved by comparing timestamps rather than by overwriting, because a user can
    be reassigned within a window and the arm that matters is the newest one — a
    last-write fold silently reports the oldest under a descending read.
    """
    leg = OurLeg()
    stamps: list[str] = []
    for entry in normalized_entries:
        leg.entry_count += 1
        message = getattr(entry, "message", "") or ""
        severity = (getattr(entry, "severity", "") or "").upper()
        ts = getattr(entry, "timestamp", None)
        if ts:
            stamps.append(ts)

        m = _EXEC_TIME_RE.search(message)
        if m:
            leg.openai_ms.append(int(m.group(1)))

        m = _EXPERIMENT_RE.search(message)
        if m:
            name, arm = m.group(1), m.group(2)
            seen = leg.experiment_stamps.get(name)
            # RFC3339 stamps sort lexicographically, so a string compare is a time
            # compare. An entry with no stamp only wins if nothing stamped exists.
            if name not in leg.experiments or (ts and (seen is None or ts > seen)):
                leg.experiments[name] = arm
                if ts:
                    leg.experiment_stamps[name] = ts

        lowered = message.lower()
        if "retrieved conversation token" in lowered or "conversation token from elevenlabs" in lowered:
            if "success" in lowered:
                leg.elevenlabs_token_issued = True
        if "conversation token" in lowered and ("fail" in lowered or "error" in lowered):
            leg.elevenlabs_token_failed = True
        if "resolvevoicelimits" in (getattr(entry, "component", "") or "").lower() or "voice limits" in lowered:
            leg.voice_limits_resolved += 1
        if severity in ("ERROR", "CRITICAL", "ALERT", "EMERGENCY"):
            leg.errors.append(f"{ts} {message}"[:200])

    if stamps:
        leg.first_ts = min(stamps)
        leg.last_ts = max(stamps)
    return leg


# ---------------------------------------------------------------------------
# Final verdict
# ---------------------------------------------------------------------------

@dataclass
class Verdict:
    """The separation the whole skill exists to produce."""

    symptom: str
    artifacts: list[str] = field(default_factory=list)
    session_health: str = ""
    confidence: str = "medium"


def build_verdict(
    convs: Sequence[Conversation],
    latency: LatencyReport,
    watchdogs: list[WatchdogVerdict],
    our_leg: OurLeg,
    client_errors: int = 0,
) -> Verdict:
    """Name the user-perceived symptom separately from instrumentation noise.

    `client_errors` is the number of Bugsnag signatures the user hit. It counts as
    an answered source in its own right: a transport-level signature produces rows
    but no watchdog metadata, so judging "did anything answer?" without it would
    declare an empty run while the digest lists client errors.
    """
    artifacts: list[str] = []
    convs = sorted(convs, key=lambda c: c.start_unix or 0)
    # The longest call stands in for the session in one-line prose; every
    # time-sensitive judgement uses the whole list.
    conv = max(convs, key=lambda c: c.duration_secs or 0) if convs else None
    reconnects = max(len(convs) - 1, 0)
    # A call the user sat through that produced nothing: alive long enough to be
    # waiting, with no agent turn on it at all.
    dead_calls = [
        c for c in convs
        if not any(t.role == "agent" for t in c.turns)
        and (c.duration_secs or 0) >= DEAD_CALL_MIN_SECS
        and not c.in_progress
    ]
    false_positives = [w for w in watchdogs if w.verdict == "false-positive"]
    genuine = [w for w in watchdogs if w.verdict == "genuine"]
    if false_positives:
        artifacts.append(
            f"{len(false_positives)} reply-watchdog event(s) fired while the agent was still "
            f"replying — instrumentation artifact, not a stall (PROD-4395)."
        )

    if not convs and not our_leg.entry_count and not watchdogs and not client_errors:
        # Nothing answered. This has to read differently from a clean call, because
        # the two look identical in a digest and only one of them is good news.
        return Verdict(
            symptom=(
                "Could not investigate — no source returned anything for this user and window. "
                "This is not evidence of a healthy call; check the Sources block for which legs "
                "were unavailable and widen the window or supply the missing credentials."
            ),
            artifacts=artifacts,
            session_health="unknown",
            confidence="none",
        )

    if conv is None:
        # No vendor call does not mean no evidence. A retention miss, a LiveKit-path
        # incident or a search miss all land here, and the Aura leg can still show a
        # slow LLM — which is the one thing we can attribute without the vendor.
        symptom = "Cannot attribute a vendor-side symptom — no ElevenLabs conversation resolved."
        confidence = "low"
        if our_leg.grade in ("degraded", "bad"):
            symptom += (
                f" Our own LLM leg is implicated independently of the vendor, though: worst Aura "
                f"response {our_leg.worst_openai_secs}s (median {our_leg.median_openai_secs}s "
                f"over {len(our_leg.openai_ms)} calls)."
            )
            confidence = "medium"
        if our_leg.truncated:
            symptom += " The prod-log read was truncated, so absent signals prove nothing."
        return Verdict(
            symptom=symptom,
            artifacts=artifacts,
            session_health="unknown",
            confidence=confidence,
        )

    total_secs = sum(c.duration_secs or 0 for c in convs)
    total_turns = sum(len(c.turns) for c in convs)
    abnormal = [c for c in convs if not c.closed_normally and not c.in_progress]
    live = [c for c in convs if c.in_progress]
    if len(convs) == 1 and not live:
        health = (
            f"call ran {conv.duration_secs}s over {len(conv.turns)} turns; "
            f"{'closed normally' if conv.closed_normally else f'termination: {conv.termination_reason}'}"
        )
    elif len(convs) == 1:
        health = "call was still in progress at fetch time — duration and turns are not final"
    else:
        # A live call has not closed at all, so it can be neither "closed
        # normally" nor abnormal — saying either contradicts the vendor table.
        closed = f"{len(abnormal)} closed abnormally" if abnormal else "all closed normally"
        if live:
            closed = (
                f"{len(live)} still in progress"
                + (f", {len(abnormal)} closed abnormally" if abnormal else "")
            )
        health = (
            f"{len(convs)} calls over {total_secs}s and {total_turns} turns "
            f"({reconnects} reconnect(s)); {closed}"
        )
    if reconnects:
        artifacts.append(
            f"The Aura conversation spans {len(convs)} vendor calls — {reconnects} reconnect(s). "
            f"A watchdog or latency figure read against a single call would describe only part "
            f"of what the user sat through."
        )

    felt = latency.by_metric(FELT_METRIC)
    if genuine:
        symptom = (
            f"The agent genuinely stopped replying — {len(genuine)} watchdog window(s) contain no "
            f"agent turn at all."
        )
        confidence = "high"
    elif dead_calls:
        longest_dead = max(dead_calls, key=lambda c: c.duration_secs or 0)
        symptom = (
            f"{len(dead_calls)} call(s) produced no agent turn at all despite running "
            f"{longest_dead.duration_secs}s — the user was connected with nothing coming back. "
            f"Consistent with the zombie-session pattern (ENGMOB-2781): the vendor call is "
            f"alive while the client has stopped conveying it."
        )
        confidence = "high"
    elif felt and felt.grade == "bad":
        symptom = (
            f"Slow / choppy agent audio: worst time-to-first-audio {felt.worst}s "
            f"(at {felt.at_secs}s into the call), against a {FELT_LATENCY_BAD}s bad threshold."
        )
        confidence = "high"
    elif felt and felt.grade == "degraded":
        symptom = (
            f"Marginal responsiveness: worst time-to-first-audio {felt.worst}s "
            f"(at {felt.at_secs}s), above the {FELT_LATENCY_DEGRADED}s degraded threshold."
        )
        confidence = "medium"
    elif abnormal:
        symptom = (
            f"{len(abnormal)} call(s) ended abnormally — termination reason: "
            f"{abnormal[-1].termination_reason}."
        )
        confidence = "medium"
    elif live:
        # Nothing has gone wrong *yet*, but the call had not finished, so a
        # clean bill of health would be asserting more than was observed.
        symptom = (
            f"{len(live)} call(s) were still in progress at fetch time, so this session is "
            f"not fully observed — no vendor-side degradation in what was captured. Re-run "
            f"once the call has ended before concluding it was healthy."
        )
        confidence = "low"
    else:
        symptom = (
            "No vendor-side degradation found: latency within thresholds and the call closed "
            "normally. Look at the transcript content or the device audio route."
        )
        confidence = "low" if not latency.findings else "medium"

    if our_leg.grade in ("degraded", "bad"):
        symptom += (
            f" Our own LLM leg contributed — worst Aura response {our_leg.worst_openai_secs}s "
            f"(median {our_leg.median_openai_secs}s)."
        )
    return Verdict(symptom=symptom, artifacts=artifacts, session_health=health, confidence=confidence)
