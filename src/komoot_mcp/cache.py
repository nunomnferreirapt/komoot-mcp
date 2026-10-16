"""Disk cache for parsed tour segments and saved highlights.

Stores post-parse, post-split segments so subsequent route builds skip
all Komoot API calls for already-known tours. Single JSON file at
``$KOMOOT_MCP_CACHE_DIR/route_cache.json`` (default ``~/.cache/komoot-mcp``).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_CACHE_DIR = "~/.cache/komoot-mcp"
CACHE_VERSION = 1
HIGHLIGHT_MAX_AGE_HOURS = 24


def cache_dir() -> Path:
    """Cache folder: $KOMOOT_MCP_CACHE_DIR, or ~/.cache/komoot-mcp."""
    return Path(os.environ.get("KOMOOT_MCP_CACHE_DIR", DEFAULT_CACHE_DIR)).expanduser()


def cache_file() -> Path:
    return cache_dir() / "route_cache.json"


def _empty_cache() -> dict[str, Any]:
    return {"version": CACHE_VERSION, "segments": {}, "highlights": None}


def load_cache() -> dict[str, Any]:
    """Load cache from disk. Returns empty structure on missing/corrupt/outdated file."""
    path = cache_file()
    if not path.exists():
        return _empty_cache()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != CACHE_VERSION:
            return _empty_cache()
        return data
    except (json.JSONDecodeError, KeyError):
        return _empty_cache()


def save_cache(cache: dict[str, Any]) -> None:
    """Write cache to disk."""
    cache_dir().mkdir(parents=True, exist_ok=True)
    cache_file().write_text(json.dumps(cache, indent=2), encoding="utf-8")


def get_cached_segments(cache: dict[str, Any], tour_id: int) -> list[dict] | None:
    """Return cached segment dicts for a tour, or None if not cached."""
    entry = cache.get("segments", {}).get(str(tour_id))
    if entry is None:
        return None
    return entry.get("segments")


def put_segments(
    cache: dict[str, Any],
    tour_id: int,
    tour_name: str,
    distance_km: float,
    segments: list[Any],
) -> None:
    """Store parsed+split segments for a tour. Accepts Segment dataclass instances."""
    serialized = []
    for seg in segments:
        serialized.append({
            "tour_id": seg.tour_id,
            "tour_name": seg.tour_name,
            "index": seg.index,
            "surface": seg.surface,
            "way_type": seg.way_type,
            "is_gravel": seg.is_gravel,
            "coords": [list(c) for c in seg.coords],
            "distance_m": seg.distance_m,
        })

    cache.setdefault("segments", {})[str(tour_id)] = {
        "cached_at": datetime.now(timezone.utc).isoformat(),
        "tour_name": tour_name,
        "distance_km": distance_km,
        "segments": serialized,
    }


def get_cached_highlights(cache: dict[str, Any]) -> list[dict] | None:
    """Return cached highlights if fresh (< 24h old), else None."""
    hl = cache.get("highlights")
    if hl is None:
        return None

    cached_at = hl.get("cached_at")
    if cached_at:
        try:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(cached_at)
            if age.total_seconds() > HIGHLIGHT_MAX_AGE_HOURS * 3600:
                return None
        except (ValueError, TypeError):
            return None

    return hl.get("items")


def put_highlights(cache: dict[str, Any], highlights: list[dict]) -> None:
    """Store saved highlights."""
    cache["highlights"] = {
        "cached_at": datetime.now(timezone.utc).isoformat(),
        "items": highlights,
    }


def clear_cache() -> None:
    """Delete the cache file."""
    path = cache_file()
    if path.exists():
        path.unlink()
