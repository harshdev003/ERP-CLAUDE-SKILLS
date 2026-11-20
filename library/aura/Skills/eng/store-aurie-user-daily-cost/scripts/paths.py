#!/usr/bin/env python3
"""Resolve the three local daily-cost store CSV paths.

BigQuery (`aura-main.aurie_cost`) is the SOURCE OF TRUTH; these CSVs are a
rebuildable local copy (a cache the pipeline writes and `bq_sink.py` mirrors up,
`bq_pull.py` rebuilds down). The cache lives under `$AURIE_COST_CACHE_DIR`
(default `~/.cache/aurie-cost`), so the paths don't need to be pinned in
settings.json.

Per-store resolution precedence:
    explicit --flag  >  $AURIE_*_CSV (legacy override)  >  <cache_dir>/<file>

The `$AURIE_*_CSV` env vars are still honored (back-compat) but no longer
required — an unset var falls through to the cache dir.

Stdlib only.
"""
from __future__ import annotations

import os

DEFAULT_CACHE_DIR = "~/.cache/aurie-cost"

# store key -> default filename in the cache dir
FILENAMES = {
    "per_user": "per_user_daily_cost.csv",
    "unit_costs": "daily_unit_costs.csv",
    "eng_kpis": "daily_eng_kpis.csv",
}

# store key -> legacy override env var
ENV = {
    "per_user": "AURIE_COST_CSV",
    "unit_costs": "AURIE_UNIT_COST_CSV",
    "eng_kpis": "AURIE_KPI_CSV",
}


def cache_dir() -> str:
    """The local cache directory (expanded), honoring $AURIE_COST_CACHE_DIR."""
    return os.path.expanduser(os.environ.get("AURIE_COST_CACHE_DIR", DEFAULT_CACHE_DIR))


def cache_file(store: str) -> str:
    """The pure cache-dir path for a store (ignores the legacy env override)."""
    return os.path.join(cache_dir(), FILENAMES[store])


def default_path(store: str) -> str:
    """Default path for a store: legacy $AURIE_*_CSV if set, else the cache file."""
    override = os.environ.get(ENV[store])
    return os.path.expanduser(override) if override else cache_file(store)


def resolve(store: str, explicit: str | None = None, ensure: bool = False) -> str:
    """Resolve a store path: --flag > $AURIE_*_CSV > <cache_dir>/<file>.

    With ensure=True, creates the parent directory (for writers).
    """
    path = os.path.expanduser(explicit) if explicit else default_path(store)
    if ensure:
        ensure_parent(path)
    return path


def ensure_parent(path: str) -> str:
    """Create the parent directory of `path` if needed; return `path`."""
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    return path
