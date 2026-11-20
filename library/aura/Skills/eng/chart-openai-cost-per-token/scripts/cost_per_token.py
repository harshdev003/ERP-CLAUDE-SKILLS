"""Daily OpenAI cost per 1M tokens, one number per day, as a bar chart.

cost per 1M tokens = billed dollars that day (Costs API, all line items for the model)
                     ÷ tokens that day (Usage API: input incl. cached + output) × 1M

Scope: one model (``--model``, matched against the Costs API line-item prefix and the
Usage API ``model`` field), optionally one project (``--project`` name or id). With no
``--model``, every model in scope is summed.

Days on or after ``--highlight-from`` are drawn in red, and the average of the days
before it is drawn as a dotted baseline, so a step change (a cache collapse, a price
change, a model swap) is visible at a glance.

Only aggregate dollars and token counts are read. No user, key or prompt data.

Env: OPENAI_ADMIN_API_KEY (Admin key with /v1/organization/* access).
"""

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import requests

OPENAI_API = "https://api.openai.com/v1"
UTC = timezone.utc


def _request(path: str, params: dict, api_key: str) -> dict:
    """GET with retry on network errors, 429 and 5xx."""
    last_err = ""
    for attempt in range(6):
        try:
            r = requests.get(
                f"{OPENAI_API}{path}",
                headers={"Authorization": f"Bearer {api_key}"},
                params=params,
                timeout=60,
            )
        except requests.RequestException as e:
            last_err = str(e)
            time.sleep(min(2**attempt, 30))
            continue
        if r.status_code == 429 or r.status_code >= 500:
            last_err = f"{r.status_code}: {r.text[:200]}"
            time.sleep(min(float(r.headers.get("Retry-After", 2**attempt)), 30))
            continue
        if not r.ok:
            sys.exit(f"OpenAI {r.status_code} on {path}: {r.text[:300]}")
        return r.json()
    sys.exit(f"OpenAI {path}: gave up after retries; last error: {last_err}")


def _paged(path: str, params: dict, api_key: str) -> list[dict]:
    out: list[dict] = []
    page = None
    while True:
        p = dict(params)
        if page:
            p["page"] = page
        data = _request(path, p, api_key)
        out.extend(data.get("data", []))
        page = data.get("next_page")
        if not data.get("has_more") or not page:
            return out


def resolve_project(query: str, api_key: str) -> tuple[str, str]:
    """Exact name -> unique substring -> exact id (active + archived projects)."""
    projects: dict[str, str] = {}
    after = None
    while True:
        params: dict = {"limit": 100, "include_archived": "true"}
        if after:
            params["after"] = after
        data = _request("/organization/projects", params, api_key)
        for p in data.get("data", []):
            projects[p["id"]] = p.get("name") or p["id"]
        if not data.get("has_more") or not data.get("data"):
            break
        after = data["data"][-1]["id"]
    q = query.lower()
    for matcher in (lambda n: n.lower() == q, lambda n: q in n.lower()):
        hits = [(pid, n) for pid, n in projects.items() if matcher(n)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            listing = "\n".join(f"  - {n} ({pid})" for pid, n in hits)
            sys.exit(f"Several projects match {query!r}; pass the project_id:\n{listing}")
    if query in projects:
        return query, projects[query]
    sys.exit(f"No OpenAI project matches {query!r}.")


def _day(ts: int) -> date:
    return datetime.fromtimestamp(ts, UTC).date()


def collect_by_model(project_id, start: date, end: date, api_key: str, top: int) -> dict[str, list[dict]]:
    """Daily rows for each of the `top` models by spend in the window."""
    spend: dict[str, float] = defaultdict(float)
    window = {
        "start_time": int(datetime(start.year, start.month, start.day, tzinfo=UTC).timestamp()),
        "end_time": int(datetime(end.year, end.month, end.day, tzinfo=UTC).timestamp()),
        "bucket_width": "1d",
        "limit": 31,
    }
    if project_id:
        window["project_ids"] = [project_id]
    for b in _paged("/organization/costs", {**window, "group_by": ["line_item"]}, api_key):
        for r in b.get("results", []):
            line = (r.get("line_item") or "").split(" | ", 1)[-1]
            if ", " in line:
                spend[line.rsplit(", ", 1)[0]] += float(r["amount"]["value"])
    models = [m for m, _ in sorted(spend.items(), key=lambda kv: -kv[1])[:top]]
    return {m: collect(m, project_id, start, end, api_key) for m in models}


def render_by_model(series: dict[str, list[dict]], highlight_from, title: str, out_png: str) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as md
    import matplotlib.pyplot as plt

    plt.rcParams.update({"axes.spines.top": False, "axes.spines.right": False})
    fig, ax = plt.subplots(figsize=(13, 5.8))
    for model, rows in series.items():
        if not rows:
            continue
        xs = [datetime.fromisoformat(r["date"]).replace(tzinfo=UTC) for r in rows]
        ys = [r["cost_per_1m_tokens"] for r in rows]
        ax.plot(xs, ys, marker="o", ms=3, lw=1.8, label=model)
        ax.text(xs[-1], ys[-1], f"  {model}", fontsize=8, va="center")
    if highlight_from:
        ax.axvline(datetime(highlight_from.year, highlight_from.month, highlight_from.day, tzinfo=UTC), color="#dc2626", ls="--", lw=1)
    ax.set_ylabel("$ per 1M tokens (all tokens)")
    ax.set_title(title, loc="left", fontsize=11)
    ax.xaxis.set_major_locator(md.AutoDateLocator(tz=UTC))
    ax.xaxis.set_major_formatter(md.DateFormatter("%b %d", tz=UTC))
    ax.set_ylim(bottom=0)
    ax.grid(axis="y", alpha=0.25)
    ax.legend(loc="upper left", fontsize=8, frameon=False)
    fig.tight_layout()
    fig.savefig(out_png, dpi=130)


def collect(model, project_id, start: date, end: date, api_key: str) -> list[dict]:
    """Per-day dollars and tokens for [start, end) UTC."""
    window = {
        "start_time": int(datetime(start.year, start.month, start.day, tzinfo=UTC).timestamp()),
        "end_time": int(datetime(end.year, end.month, end.day, tzinfo=UTC).timestamp()),
        "bucket_width": "1d",
        "limit": 31,
    }
    if project_id:
        window["project_ids"] = [project_id]

    tokens: dict[date, list[int]] = defaultdict(lambda: [0, 0, 0])  # input, cached, output
    token_models: set[str] = set()
    for b in _paged("/organization/usage/completions", {**window, "group_by": ["model"]}, api_key):
        d = _day(b["start_time"])
        for r in b.get("results", []):
            if model and r.get("model") != model:
                continue
            token_models.add(r.get("model"))
            t = tokens[d]
            t[0] += r.get("input_tokens", 0)
            t[1] += r.get("input_cached_tokens", 0)
            t[2] += r.get("output_tokens", 0)

    dollars: dict[date, float] = defaultdict(float)
    for b in _paged("/organization/costs", {**window, "group_by": ["line_item"]}, api_key):
        d = _day(b["start_time"])
        for r in b.get("results", []):
            line = r.get("line_item") or ""
            # Non-default tiers are prefixed, e.g. "flex | gpt-5.2-2025-12-11, input".
            # Usage tokens aren't split by tier, so every tier's dollars count.
            line = line.split(" | ", 1)[-1]
            line_model = line.rsplit(", ", 1)[0] if ", " in line else line
            # Only dollars for models that have completion tokens in scope. Image, embedding
            # and audio line items have no /usage/completions tokens and would inflate $/1M.
            if line_model not in token_models:
                continue
            dollars[d] += float(r["amount"]["value"])

    rows = []
    for d in sorted(set(dollars) & set(tokens)):
        inp, cached, out = tokens[d]
        total = inp + out
        if not total:
            continue
        rows.append(
            {
                "date": d.isoformat(),
                "cost_usd": round(dollars[d], 2),
                "input_tokens": inp,
                "cached_input_tokens": cached,
                "output_tokens": out,
                "cache_hit_pct": round(100 * cached / inp, 1) if inp else None,
                "cost_per_1m_tokens": round(dollars[d] / total * 1e6, 3),
            }
        )
    return rows


def render(rows: list[dict], highlight_from, title: str, out_png: str) -> float | None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as md
    import matplotlib.pyplot as plt

    xs = [datetime.fromisoformat(r["date"]).replace(tzinfo=UTC) for r in rows]
    ys = [r["cost_per_1m_tokens"] for r in rows]
    hot = [highlight_from is not None and date.fromisoformat(r["date"]) >= highlight_from for r in rows]
    base = [y for y, h in zip(ys, hot) if not h]
    avg = sum(base) / len(base) if (highlight_from and base) else None

    plt.rcParams.update({"axes.spines.top": False, "axes.spines.right": False})
    fig, ax = plt.subplots(figsize=(13, 5.5))
    ax.bar(xs, ys, 0.8, color=["#dc2626" if h else "#2563eb" for h in hot])
    for x, y in zip(xs, ys):
        ax.text(x, y + max(ys) * 0.01, f"${y:.2f}", ha="center", fontsize=7, color="#374151")
    if avg is not None:
        ax.axhline(avg, color="#6b7280", ls=":", lw=1)
        ax.text(xs[0], avg + max(ys) * 0.02, f"average before {highlight_from:%b %d}: ${avg:.2f}", fontsize=9, color="#374151")
    ax.set_ylabel("$ per 1M tokens (all tokens)")
    ax.set_title(title, loc="left", fontsize=11)
    interval = max(1, len(rows) // 12)
    ax.xaxis.set_major_locator(md.DayLocator(interval=interval, tz=UTC))
    ax.xaxis.set_major_formatter(md.DateFormatter("%b %d", tz=UTC))
    ax.set_ylim(0, max(ys) * 1.15)
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(out_png, dpi=130)
    return avg


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Daily OpenAI cost per 1M tokens, one bar per day")
    ap.add_argument("--model", help="Model as OpenAI reports it, e.g. chat-latest or gpt-5.4-2026-03-05. Omit for all models.")
    ap.add_argument("--project", help="OpenAI project name (exact or unique substring) or project_id. Omit for the whole org.")
    ap.add_argument("--days", type=int, default=30, help="Last N full UTC days (default 30). Ignored with --since.")
    ap.add_argument("--since", help="UTC start date YYYY-MM-DD (inclusive).")
    ap.add_argument("--until", help="UTC end date YYYY-MM-DD (exclusive, default today).")
    ap.add_argument("--highlight-from", help="YYYY-MM-DD: draw days from here in red and the average of earlier days as a baseline.")
    ap.add_argument("--out", default="cost_per_token.png", help="PNG path (default cost_per_token.png).")
    ap.add_argument("--json", dest="json_out", help="Also write the daily rows as JSON to this path.")
    ap.add_argument("--by-model", action="store_true", help="One line per model (top --top by spend) instead of one combined bar per day.")
    ap.add_argument("--top", type=int, default=6, help="With --by-model: how many models to plot (default 6).")
    a = ap.parse_args()

    api_key = os.environ.get("OPENAI_ADMIN_API_KEY")
    if not api_key:
        sys.exit("OPENAI_ADMIN_API_KEY is not set (needs /v1/organization/* access).")

    today = datetime.now(UTC).date()
    end = date.fromisoformat(a.until) if a.until else today
    start = date.fromisoformat(a.since) if a.since else end - timedelta(days=a.days)
    hl = date.fromisoformat(a.highlight_from) if a.highlight_from else None

    project_id, project_name = (None, None)
    if a.project:
        project_id, project_name = resolve_project(a.project, api_key)

    if a.by_model:
        series = collect_by_model(project_id, start, end, api_key, a.top)
        scope = f"top {len(series)} models by spend" + (f", project {project_name}" if project_name else ", whole org")
        render_by_model(series, hl, f"OpenAI cost per 1M tokens by model, daily ({scope}), UTC", a.out)
        for model, rows in series.items():
            if not rows:
                continue
            ys = [r["cost_per_1m_tokens"] for r in rows]
            print(f"{model:28}  min ${min(ys):.3f}  max ${max(ys):.3f}  last ${ys[-1]:.3f}  ({len(rows)} days)")
        print(f"\nChart: {a.out}")
        if a.json_out:
            with open(a.json_out, "w", encoding="utf-8") as f:
                json.dump({"scope": scope, "models": series}, f, indent=2)
            print(f"JSON: {a.json_out}")
        return

    rows = collect(a.model, project_id, start, end, api_key)
    if not rows:
        sys.exit("No days with both cost and token data in that window. Check --model / --project (the Costs API also lags ~1 day).")

    scope = f"{a.model or 'all models'}" + (f", project {project_name}" if project_name else ", whole org")
    title = f"OpenAI cost per 1M tokens, daily ({scope}), {rows[0]['date']} to {rows[-1]['date']} UTC. Total $ (Costs API) / total tokens (Usage API)"
    avg = render(rows, hl, title, a.out)

    print(f"{'date':10}  {'cost $':>9}  {'tokens (M)':>10}  {'cached %':>8}  {'$/1M':>6}")
    for r in rows:
        tm = (r["input_tokens"] + r["output_tokens"]) / 1e6
        cp = f"{r['cache_hit_pct']:.1f}" if r["cache_hit_pct"] is not None else "-"
        print(f"{r['date']}  {r['cost_usd']:9.2f}  {tm:10.1f}  {cp:>8}  {r['cost_per_1m_tokens']:6.3f}")
    if avg is not None:
        after = [r for r in rows if date.fromisoformat(r["date"]) >= hl]
        if after:
            # What the highlighted days would have cost at the earlier average rate.
            expected = sum((r["input_tokens"] + r["output_tokens"]) / 1e6 * avg for r in after)
            actual = sum(r["cost_usd"] for r in after)
            print(f"\nBaseline ${avg:.3f}/1M before {hl}. From {hl}: ${actual:,.2f} billed vs ${expected:,.2f} at the baseline rate (+${actual - expected:,.2f}).")
    print(f"\nChart: {a.out}")
    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as f:
            json.dump({"scope": scope, "highlight_from": a.highlight_from, "baseline_per_1m": avg, "days": rows}, f, indent=2)
        print(f"JSON: {a.json_out}")


if __name__ == "__main__":
    main()
