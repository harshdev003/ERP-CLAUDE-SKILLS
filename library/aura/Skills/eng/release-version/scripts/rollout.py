#!/usr/bin/env python3
"""Did Spinnaker roll the release out? Read from spinbot's posts in #deployment.

The chain after a `v*` tag: CircleCI `test` -> `push` builds and pushes
`us-central1-docker.pkg.dev/aura-build/aura-repository/<image>:<tag>`; that push
fires a Pub/Sub trigger on the service's Spinnaker "Quick deployment" pipeline,
which patches the Kubernetes Deployment on dev, then promotes it to prod. spinbot
posts `Pipeline starting|complete|failed for <APP>` for every execution.

Spinnaker's own API (api.spin.aurahealth.io) sits behind OAuth, so a script has no
headless read of it. Slack is the only machine-readable record of an execution.

**A message carries no tag or image.** The match is: the right application AND the
right pipeline, first execution starting after the tag was created. Two repos can
share one application (`user` runs UserService's and UserWeb's pipelines), so the
pipeline name is load-bearing, not decoration -- matching the application alone
confirms one repo's release with another repo's rollout.

Slack reads go through `slack_read.py`, a copy of shared:read-slack's canonical
helper (requests, else curl -- never urllib, which cannot find the CA bundle on
stock macOS Python).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import slack_read  # noqa: E402  -- copy of shared:read-slack's canonical helper

DEPLOYMENT_CHANNEL = "GG6CJ6P0D"   # #deployment

# Repo -> (Spinnaker application, pipeline name). Pinned from the (app, pipeline)
# pairs spinbot actually posted in #deployment over the year to 2026-10-06; the
# test suite holds every entry to that observed set. Names are compared casefolded
# because the producer's own spellings vary ("Quick Deployment for Service").
# A repo absent here reports `unknown`, never a guess derived from its name.
PIPELINES: dict[str, tuple[str, str]] = {
    "AuraHealth/BusinessService": ("business", "Quick deployment for Service"),
    "AuraHealth/CoachService": ("coach", "Quick Deployment for Service"),
    "AuraHealth/ContentService": ("content", "Quick deployment"),
    "AuraHealth/EmailService": ("email", "Quick deployment"),
    "AuraHealth/ExperimentService": ("experiment", "Quick deployment"),
    "AuraHealth/FeedService": ("feed", "Quick deployment"),
    "AuraHealth/IntelligenceService": ("intelligence", "Quick deployment"),
    "AuraHealth/IterableService": ("iterable", "Quick deployment"),
    "AuraHealth/NotificationService": ("notification", "Quick deployment"),
    "AuraHealth/PaymentService": ("payment", "Quick deployment"),
    "AuraHealth/ReportService": ("report", "Quick deployment"),
    "AuraHealth/SupportService": ("support", "Quick deployment"),
    "AuraHealth/UserService": ("user", "Quick deployment for Service"),
    "AuraHealth/AIWeb": ("ai", "Quick deployment for Web"),
    "AuraHealth/BusinessWeb": ("business", "Quick deployment for Web"),
    "AuraHealth/CoachWeb": ("coach", "Quick deployment for Web"),
    "AuraHealth/ProxyWeb": ("proxy", "Quick Deployment"),
    "AuraHealth/UserWeb": ("user", "Quick deployment for Web"),
    "AuraHealth/Ingestor": ("ingestor", "Quick deployment"),
}

# Echo's Slack title is "<Type> <status> for <APP>" and the body
# "<App>'s <link|pipeline name> pipeline is starting|has <status>"
# (echo SlackNotificationAgent.getNotificationTitle / sendNotifications).
_TITLE = re.compile(r"^Pipeline (\w+) for (\S+)$")
_BODY = re.compile(r"/#/applications/([^/]+)/executions/details/(\w+)\|([^>]+)>")
_LINK = "https://spin.aurahealth.io/#/applications/{app}/executions/details/{eid}"

# Classified by explicit sets: a status nobody has seen is not a pass.
_TERMINAL_OK = {"complete"}
_TERMINAL_BAD = {"failed"}
_STARTED = {"starting"}


@dataclass(frozen=True)
class Event:
    ts: float
    status: str
    app: str
    pipeline: str
    execution: str


def _event(ts: float, title: str, body: str) -> Event | None:
    t = _TITLE.match((title or "").strip())
    b = _BODY.search(body or "")
    if not (t and b):
        return None
    return Event(ts=ts, status=t.group(1).lower(), app=b.group(1).lower(),
                 pipeline=b.group(3).strip(), execution=b.group(2))


def parse_api(messages: list[dict]) -> list[Event]:
    """Events from Slack `conversations.history` messages."""
    out = []
    for m in messages:
        for a in m.get("attachments") or []:
            ev = _event(float(m.get("ts") or 0), a.get("title") or "",
                        a.get("text") or a.get("fallback") or "")
            if ev:
                out.append(ev)
    return out


# Slack MCP `slack_read_channel` renders each message as a block:
#   Message TS: 1791273770.215569
#
#   Attachment: Pipeline complete for USER
#   User's <https://…/executions/details/01M4…|Quick deployment for Service> pipeline …
_MCP_BLOCK = re.compile(
    r"Message TS:\s*(\d+\.\d+)\s*\n\s*\nAttachment:\s*([^\n]+)\n([^\n]+)")


def parse_mcp(text: str) -> list[Event]:
    """Events from the Slack MCP `slack_read_channel` text rendering."""
    # The MCP result escapes `/` as `\/` when saved as raw JSON-ish text.
    text = text.replace("\\/", "/")
    out = []
    for ts, title, body in _MCP_BLOCK.findall(text):
        ev = _event(float(ts), title, body)
        if ev:
            out.append(ev)
    return out


def parse_log(text: str) -> list[Event]:
    """A saved #deployment read: Slack API JSON, or the MCP text rendering."""
    try:
        data = json.loads(text)
    except ValueError:
        return parse_mcp(text)
    if isinstance(data, dict):
        # A conversations.history page, or the MCP result saved with its envelope.
        if isinstance(data.get("messages"), str):
            return parse_mcp(data["messages"])
        data = data.get("messages") or []
    if isinstance(data, str):
        return parse_mcp(data)
    return parse_api(data)


def classify(events: list[Event], app: str, pipeline: str, since: float) -> dict:
    """The verdict for the first matching execution that started after `since`."""
    want = (app.casefold(), pipeline.casefold())
    mine = sorted((e for e in events
                   if (e.app.casefold(), e.pipeline.casefold()) == want and e.ts >= since),
                  key=lambda e: e.ts)
    # An execution belongs to this release only if it STARTED after the tag. A
    # `complete` whose `starting` predates the bound is an earlier image's rollout
    # finishing -- and a read windowed at the tag never even sees that `starting`.
    began = {e.execution for e in mine if e.status in _STARTED}
    mine = [e for e in mine if e.execution in began]
    label = f'"{pipeline}" for {app.upper()}'
    if not mine:
        return {"rollout": "not-started",
                "rollout_detail": f"no Spinnaker {label} execution since the tag — the "
                                  f"image push may not have finished, or the trigger "
                                  f"did not fire"}
    order: list[str] = []
    for e in mine:
        if e.execution not in order:
            order.append(e.execution)
    eid = order[0]
    run = [e for e in mine if e.execution == eid]
    statuses = {e.status for e in run}
    out = {"execution": _LINK.format(app=app.lower(), eid=eid),
           "rollout_started": _iso(min(e.ts for e in run))}
    if statuses & _TERMINAL_BAD:
        out.update(rollout="failed", rollout_detail=f"Spinnaker {label} failed")
    elif statuses & _TERMINAL_OK:
        out.update(rollout="complete",
                   rollout_finished=_iso(max(e.ts for e in run if e.status in _TERMINAL_OK)),
                   rollout_detail=f"Spinnaker {label} completed (dev, then prod)")
    elif statuses <= _STARTED:
        out.update(rollout="running",
                   rollout_detail=f"Spinnaker {label} has started and not finished")
    else:
        out.update(rollout="unknown",
                   rollout_detail=f"Spinnaker {label} reported {sorted(statuses)}")
    if len(order) > 1:
        # The first execution after the tag is taken as this release's. A later one
        # is another image push, which may already have replaced it.
        out["rollout_note"] = (f"{len(order)} executions since the tag; the first is "
                               f"read as this release's, a later one may supersede it")
    return out


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_history(token: str, oldest: float) -> list[dict]:
    """Every #deployment message since `oldest`, through shared:read-slack's helper."""
    return slack_read.history(DEPLOYMENT_CHANNEL, token=token, oldest=oldest)


def _out(args: list[str], cwd=None) -> str:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                          timeout=30).stdout.strip()


def tag_time(root, slug: str, tag: str) -> float | None:
    """When the tag was created: the local ref first, then GitHub.

    The lower bound for "after the tag". `creatordate` is the tagger date on an
    annotated tag and the commit date on a lightweight one; GitHub is asked the
    same question the same way.
    """
    try:
        local = _out(["git", "for-each-ref", f"refs/tags/{tag}",
                      "--format=%(creatordate:unix)"], cwd=root)
        if local:
            return float(local)
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    try:
        obj = json.loads(_out(["gh", "api", f"repos/{slug}/git/ref/tags/{tag}"])
                         or "{}").get("object") or {}
        kind = "tags" if obj.get("type") == "tag" else "commits"
        got = json.loads(_out(["gh", "api", f"repos/{slug}/git/{kind}/{obj['sha']}"])
                         or "{}")
        stamp = (got.get("tagger") or got.get("committer") or {}).get("date")
        if stamp:
            return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except (OSError, subprocess.SubprocessError, KeyError, ValueError):
        pass
    return None


def rollout_check(shape, tag: str, *, log_text: str | None = None,
                  fetch=fetch_history, since: float | None = None) -> dict:
    """Spinnaker's verdict on this release. `unknown` is NOT a pass.

    Reads `log_text` (a saved #deployment read, e.g. from the Slack MCP) when given,
    otherwise #deployment via SLACK_BOT_TOKEN.
    """
    target = PIPELINES.get(shape.slug)
    if not target:
        why = (f"{shape.slug} deploys from CircleCI (`deploy` -> `promote`), not "
               f"Spinnaker" if shape.shape == "functions" else
               f"no Spinnaker pipeline mapped for {shape.slug}")
        return {"rollout": "unknown", "rollout_detail": why}
    app, pipeline = target
    if since is None:
        since = tag_time(shape.root, shape.slug, tag)
    if since is None:
        return {"rollout": "unknown",
                "rollout_detail": f"could not read when {tag} was created, so no "
                                  f"execution can be tied to it"}
    if log_text is not None:
        events = parse_log(log_text)
    else:
        token = os.environ.get("SLACK_BOT_TOKEN")
        if not token:
            return {"rollout": "unknown",
                    "rollout_detail": "SLACK_BOT_TOKEN not set — read #deployment with "
                                      "shared:read-slack and pass it via --deployment-log"}
        try:
            events = parse_api(fetch(token, since))
        except (slack_read.SlackReadError, OSError, ValueError) as e:
            return {"rollout": "unknown",
                    "rollout_detail": f"#deployment unreadable: {e} — read it with "
                                      f"shared:read-slack and pass it via --deployment-log"}
    return classify(events, app, pipeline, since)
