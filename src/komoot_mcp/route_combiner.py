"""Route combiner v3: two-output gravel route builder.

Produces TWO outputs for every route request:
  A) Waypoints GPX: ordered gravel entry/exit points for import into Komoot planner
  C) Tour-chain GPX: continuous sections of actual saved tours connected by OSRM

Key principle: saved tours have detailed, correct coordinates that follow actual
rideable paths. We chain large continuous sections of these tours, never individual
sparse segments.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import gpxpy
import gpxpy.gpx
import requests

from .cache import (
    load_cache, save_cache, get_cached_segments, put_segments,
    get_cached_highlights, put_highlights,
)
from .client import GRAVEL_SURFACES, get_tour_segments, get_saved_highlights

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OSRM_BASE = "https://router.project-osrm.org/route/v1/cycling"
MAX_OSRM_CALLS = 30
CORRIDOR_WIDTH_M = 5000
HIGHLIGHT_RADIUS_M = 500
MAX_CONNECTOR_M = 5000  # max gap between tour runs before skipping
MIN_RUN_M = 500  # minimum tour run length to include


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Segment:
    """A contiguous portion of a tour with a single surface type."""
    tour_id: int
    tour_name: str
    index: int
    surface: str
    way_type: str
    is_gravel: bool
    coords: list[tuple[float, float, float]]
    distance_m: float

    @property
    def start(self) -> tuple[float, float]:
        return (self.coords[0][0], self.coords[0][1])

    @property
    def end(self) -> tuple[float, float]:
        return (self.coords[-1][0], self.coords[-1][1])

    @property
    def midpoint(self) -> tuple[float, float]:
        mid = self.coords[len(self.coords) // 2]
        return (mid[0], mid[1])


@dataclass
class TourRun:
    """A continuous section of a saved tour within the corridor.

    Unlike a Segment (single surface type), a TourRun spans multiple
    consecutive segments from the same tour: preserving the full
    detailed coordinates of the original tour.
    """
    tour_id: int
    tour_name: str
    coords: list[tuple[float, float, float]]
    distance_m: float
    gravel_m: float
    progress_start: float  # 0-1 along the corridor line
    progress_end: float

    @property
    def start(self) -> tuple[float, float]:
        return (self.coords[0][0], self.coords[0][1])

    @property
    def end(self) -> tuple[float, float]:
        return (self.coords[-1][0], self.coords[-1][1])

    @property
    def gravel_pct(self) -> float:
        return (self.gravel_m / self.distance_m * 100) if self.distance_m > 0 else 0


@dataclass
class Waypoint:
    """A gravel entry/exit point for the waypoints GPX."""
    lat: float
    lon: float
    name: str
    progress: float  # 0-1 along corridor


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in meters between two GPS points."""
    R = 6_371_000
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _segment_distance(coords: list[tuple[float, float, float]]) -> float:
    total = 0.0
    for i in range(1, len(coords)):
        total += _haversine(coords[i - 1][0], coords[i - 1][1], coords[i][0], coords[i][1])
    return total


def _project_onto_line(
    point_lat: float, point_lon: float,
    start_lat: float, start_lon: float,
    end_lat: float, end_lon: float,
) -> tuple[float, float]:
    """Project a point onto start→end line. Returns (progress 0-1, perp_distance_m)."""
    avg_lat = (start_lat + end_lat) / 2
    lat_scale = 111_320
    lon_scale = 111_320 * math.cos(math.radians(avg_lat))

    dx = (end_lon - start_lon) * lon_scale
    dy = (end_lat - start_lat) * lat_scale
    px = (point_lon - start_lon) * lon_scale
    py = (point_lat - start_lat) * lat_scale

    line_len_sq = dx * dx + dy * dy
    if line_len_sq < 1:
        return 0.0, _haversine(point_lat, point_lon, start_lat, start_lon)

    t = (px * dx + py * dy) / line_len_sq
    proj_x, proj_y = t * dx, t * dy
    perp_dist = math.sqrt((px - proj_x) ** 2 + (py - proj_y) ** 2)
    return t, perp_dist


def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Bearing in degrees (0=N, 90=E)."""
    dlon = math.radians(lon2 - lon1)
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    x = math.sin(dlon) * math.cos(rlat2)
    y = math.cos(rlat1) * math.sin(rlat2) - math.sin(rlat1) * math.cos(rlat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


# ---------------------------------------------------------------------------
# Segment parsing
# ---------------------------------------------------------------------------

def _build_surface_map(items: list[dict]) -> dict[int, str]:
    index_map: dict[int, str] = {}
    for item in items:
        element = item.get("element", "")
        clean = element.split("#", 1)[1] if "#" in element else element
        for idx in range(item.get("from", 0), item.get("to", 0)):
            index_map[idx] = clean
    return index_map


def parse_segments(tour_data: dict[str, Any]) -> list[Segment]:
    """Split a tour into Segment objects, one per contiguous surface type."""
    tour_id = tour_data["tour_id"]
    tour_name = tour_data["name"]
    coords_raw = tour_data["coordinates"]
    surfaces_raw = tour_data["surfaces"]
    way_types_raw = tour_data["way_types"]

    if not coords_raw or not surfaces_raw:
        return []

    surface_map = _build_surface_map(surfaces_raw)
    way_type_map = _build_surface_map(way_types_raw)

    coord_list: list[tuple[float, float, float]] = []
    for c in coords_raw:
        coord_list.append((c.get("lat", 0.0), c.get("lng", 0.0), c.get("alt", 0.0)))

    if len(coord_list) < 2:
        return []

    segments: list[Segment] = []
    seg_start = 0
    current_surface = surface_map.get(0, "unknown")

    for i in range(1, len(coord_list)):
        surface_at_i = surface_map.get(i, current_surface)
        if surface_at_i != current_surface or i == len(coord_list) - 1:
            end_idx = i + 1 if i == len(coord_list) - 1 and surface_at_i == current_surface else i + 1
            seg_coords = coord_list[seg_start:end_idx]
            if len(seg_coords) >= 2:
                segments.append(Segment(
                    tour_id=tour_id, tour_name=tour_name,
                    index=len(segments), surface=current_surface,
                    way_type=way_type_map.get(seg_start, "unknown"),
                    is_gravel=current_surface in GRAVEL_SURFACES,
                    coords=seg_coords,
                    distance_m=_segment_distance(seg_coords),
                ))
            seg_start = i
            current_surface = surface_at_i

    return segments


# ---------------------------------------------------------------------------
# OSRM routing
# ---------------------------------------------------------------------------

_osrm_cache: dict[tuple, dict | None] = {}
_osrm_call_count = 0


def osrm_route(lat1: float, lon1: float, lat2: float, lon2: float) -> dict[str, Any] | None:
    """Call OSRM cycling router. Returns {distance_m, coords} or None."""
    global _osrm_call_count

    key = (round(lat1, 5), round(lon1, 5), round(lat2, 5), round(lon2, 5))
    if key in _osrm_cache:
        return _osrm_cache[key]

    if _osrm_call_count >= MAX_OSRM_CALLS:
        _osrm_cache[key] = None
        return None

    url = f"{OSRM_BASE}/{lon1},{lat1};{lon2},{lat2}"
    try:
        resp = requests.get(url, params={"overview": "full", "geometries": "geojson"}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        _osrm_call_count += 1

        routes = data.get("routes", [])
        if not routes:
            _osrm_cache[key] = None
            return None

        route = routes[0]
        coords = [(c[1], c[0], 0) for c in route["geometry"]["coordinates"]]
        result = {"distance_m": route.get("distance", 0), "coords": coords}
        _osrm_cache[key] = result
        return result

    except (requests.RequestException, KeyError, IndexError):
        _osrm_cache[key] = None
        return None


# ---------------------------------------------------------------------------
# Tour runs: continuous sections of saved tours within the corridor
# ---------------------------------------------------------------------------

def _build_tour_runs(
    all_segments: list[Segment],
    start_lat: float, start_lon: float,
    end_lat: float, end_lon: float,
    corridor_width_m: float,
) -> list[TourRun]:
    """Group consecutive segments from the same tour into continuous runs within the corridor.

    A run breaks when:
    - The tour changes
    - A segment falls outside the corridor
    - A segment moves significantly backward (progress drops)
    """
    # Group segments by tour_id, preserving original order within each tour
    by_tour: dict[int, list[Segment]] = {}
    for seg in all_segments:
        by_tour.setdefault(seg.tour_id, []).append(seg)

    runs: list[TourRun] = []

    for tour_id, tour_segs in by_tour.items():
        # Sort by original index within the tour
        tour_segs.sort(key=lambda s: s.index)

        current_run_coords: list[tuple[float, float, float]] = []
        current_run_gravel = 0.0
        run_start_progress = 0.0
        last_progress = -1.0

        for seg in tour_segs:
            mid = seg.midpoint
            progress, perp_dist = _project_onto_line(
                mid[0], mid[1], start_lat, start_lon, end_lat, end_lon,
            )

            in_corridor = (perp_dist <= corridor_width_m and -0.1 <= progress <= 1.1)
            moves_forward = (progress >= last_progress - 0.05)

            if in_corridor and moves_forward:
                # Continue or start a run
                if not current_run_coords:
                    run_start_progress = progress
                # Append coordinates (skip first if overlapping with previous segment end)
                if current_run_coords and seg.coords:
                    current_run_coords.extend(seg.coords[1:])
                else:
                    current_run_coords.extend(seg.coords)
                if seg.is_gravel:
                    current_run_gravel += seg.distance_m
                last_progress = progress
            else:
                # Break the run: save what we have
                if current_run_coords and len(current_run_coords) >= 2:
                    dist = _segment_distance(current_run_coords)
                    if dist >= MIN_RUN_M:
                        runs.append(TourRun(
                            tour_id=tour_id,
                            tour_name=seg.tour_name,
                            coords=current_run_coords,
                            distance_m=dist,
                            gravel_m=current_run_gravel,
                            progress_start=run_start_progress,
                            progress_end=last_progress,
                        ))
                current_run_coords = []
                current_run_gravel = 0.0
                last_progress = progress if in_corridor else -1.0

                # If this segment is in corridor, start a new run with it
                if in_corridor:
                    current_run_coords = list(seg.coords)
                    current_run_gravel = seg.distance_m if seg.is_gravel else 0.0
                    run_start_progress = progress

        # Close final run for this tour
        if current_run_coords and len(current_run_coords) >= 2:
            dist = _segment_distance(current_run_coords)
            if dist >= MIN_RUN_M:
                runs.append(TourRun(
                    tour_id=tour_id,
                    tour_name=tour_segs[0].tour_name,
                    coords=current_run_coords,
                    distance_m=dist,
                    gravel_m=current_run_gravel,
                    progress_start=run_start_progress,
                    progress_end=last_progress,
                ))

    return runs


def _select_best_runs(
    runs: list[TourRun],
    budget_m: float,
    highlights: list[dict[str, Any]],
) -> list[TourRun]:
    """Select non-overlapping tour runs that maximize gravel, ordered by progress."""
    if not runs:
        return []

    # Score each run
    scored: list[tuple[float, TourRun]] = []
    for run in runs:
        score = run.gravel_m * 2.0 + run.distance_m * 0.3
        # Highlight bonus
        mid_lat = run.coords[len(run.coords) // 2][0]
        mid_lon = run.coords[len(run.coords) // 2][1]
        for hl in highlights:
            if hl.get("lat") and hl.get("lon"):
                if _haversine(mid_lat, mid_lon, hl["lat"], hl["lon"]) < HIGHLIGHT_RADIUS_M:
                    score += 1000
        scored.append((score, run))

    # Sort by progress_start, then by score within similar progress
    scored.sort(key=lambda x: (x[1].progress_start, -x[0]))

    # Greedy selection: pick runs that don't overlap in progress
    selected: list[TourRun] = []
    used_distance = 0.0
    last_progress_end = -1.0

    # First pass: pick the highest-scoring run for each progress zone
    # Divide into zones of ~0.1 progress (10% of corridor each)
    zones: dict[int, list[tuple[float, TourRun]]] = {}
    for score, run in scored:
        zone = int(run.progress_start * 10)
        zones.setdefault(zone, []).append((score, run))

    # Pick best from each zone
    for zone_id in sorted(zones.keys()):
        zone_runs = zones[zone_id]
        zone_runs.sort(key=lambda x: -x[0])  # best score first

        for score, run in zone_runs:
            # Check no overlap with already selected
            overlaps = False
            for sel in selected:
                if (run.progress_start < sel.progress_end and run.progress_end > sel.progress_start):
                    overlaps = True
                    break

            if overlaps:
                continue

            # Check budget
            connector_est = 0.0
            if selected:
                last = selected[-1]
                connector_est = _haversine(last.end[0], last.end[1], run.start[0], run.start[1])

            if used_distance + connector_est + run.distance_m > budget_m * 0.8:
                continue

            selected.append(run)
            used_distance += run.distance_m + connector_est
            break  # one per zone

    # Sort selected by progress
    selected.sort(key=lambda r: r.progress_start)

    # Flip runs that go backward
    for i, run in enumerate(selected):
        if run.progress_end < run.progress_start:
            selected[i] = TourRun(
                tour_id=run.tour_id, tour_name=run.tour_name,
                coords=list(reversed(run.coords)),
                distance_m=run.distance_m, gravel_m=run.gravel_m,
                progress_start=run.progress_end, progress_end=run.progress_start,
            )

    return selected


# ---------------------------------------------------------------------------
# Waypoint extraction (Option A)
# ---------------------------------------------------------------------------

WAYPOINT_SPACING_M = 1500  # drop a waypoint every ~1.5 km along tour runs


def _extract_waypoints(
    runs: list[TourRun],
    start_lat: float, start_lon: float,
    end_lat: float, end_lon: float,
    highlights: list[dict[str, Any]],
) -> list[Waypoint]:
    """Extract dense waypoints from selected tour runs.

    Places a waypoint every ~1.5 km along the actual tour coordinates,
    so that Komoot's planner is forced to follow the saved gravel paths
    instead of taking paved shortcuts.
    """
    waypoints: list[Waypoint] = []

    # Start: always the exact user-specified point
    waypoints.append(Waypoint(lat=start_lat, lon=start_lon, name="Start", progress=0.0))

    for run in runs:
        # Walk along the run coordinates, dropping a waypoint every WAYPOINT_SPACING_M
        accumulated = 0.0
        last_wp_idx = 0

        # Always add the entry point
        progress, _ = _project_onto_line(
            run.start[0], run.start[1], start_lat, start_lon, end_lat, end_lon,
        )
        waypoints.append(Waypoint(
            lat=run.start[0], lon=run.start[1],
            name=f"Enter: {run.tour_name}",
            progress=progress,
        ))

        for i in range(1, len(run.coords)):
            step = _haversine(
                run.coords[i - 1][0], run.coords[i - 1][1],
                run.coords[i][0], run.coords[i][1],
            )
            accumulated += step

            if accumulated >= WAYPOINT_SPACING_M:
                pt = run.coords[i]
                prog, _ = _project_onto_line(
                    pt[0], pt[1], start_lat, start_lon, end_lat, end_lon,
                )
                waypoints.append(Waypoint(
                    lat=pt[0], lon=pt[1],
                    name=f"Via: {run.tour_name}",
                    progress=prog,
                ))
                accumulated = 0.0
                last_wp_idx = i

        # Always add the exit point
        progress, _ = _project_onto_line(
            run.end[0], run.end[1], start_lat, start_lon, end_lat, end_lon,
        )
        waypoints.append(Waypoint(
            lat=run.end[0], lon=run.end[1],
            name=f"Exit: {run.tour_name}",
            progress=progress,
        ))

    # End: always the exact user-specified point, always last
    waypoints.append(Waypoint(lat=end_lat, lon=end_lon, name="End", progress=1.0))

    # Sort by progress
    waypoints.sort(key=lambda w: w.progress)

    # Ensure End is always last (even if progress sorting puts something after it)
    end_wp = Waypoint(lat=end_lat, lon=end_lon, name="End", progress=1.0)
    waypoints = [w for w in waypoints if w.name != "End"]
    waypoints.append(end_wp)

    # Deduplicate very close waypoints (< 100m)
    deduped: list[Waypoint] = [waypoints[0]]
    for wp in waypoints[1:]:
        if wp.name == "End":
            deduped.append(wp)
        elif _haversine(deduped[-1].lat, deduped[-1].lon, wp.lat, wp.lon) > 100:
            deduped.append(wp)

    return deduped


def _build_waypoints_gpx(waypoints: list[Waypoint], route_name: str) -> str:
    """Build a GPX with route waypoints (not track)."""
    gpx = gpxpy.gpx.GPX()

    # Add waypoints
    for wp in waypoints:
        gpx.waypoints.append(gpxpy.gpx.GPXWaypoint(
            latitude=wp.lat, longitude=wp.lon, name=wp.name,
        ))

    # Also add as a route for apps that support it
    route = gpxpy.gpx.GPXRoute(name=route_name)
    for wp in waypoints:
        route.points.append(gpxpy.gpx.GPXRoutePoint(
            latitude=wp.lat, longitude=wp.lon, name=wp.name,
        ))
    gpx.routes.append(route)

    return gpx.to_xml()


# ---------------------------------------------------------------------------
# Tour-chain GPX (Option C)
# ---------------------------------------------------------------------------

def _connect_runs_with_osrm(
    runs: list[TourRun],
    start_lat: float, start_lon: float,
    end_lat: float, end_lon: float,
) -> list[dict[str, Any]]:
    """Chain tour runs with OSRM connectors to produce a rideable GPX track."""
    route: list[dict[str, Any]] = []

    if not runs:
        r = osrm_route(start_lat, start_lon, end_lat, end_lon)
        if r:
            route.append(_osrm_edge(r))
        return route

    # Start → first run
    first = runs[0]
    gap = _haversine(start_lat, start_lon, first.start[0], first.start[1])
    if gap > 50:
        r = osrm_route(start_lat, start_lon, first.start[0], first.start[1])
        if r:
            route.append(_osrm_edge(r))

    # Add first run
    route.append(_run_edge(first))

    # Chain consecutive runs
    for i in range(1, len(runs)):
        prev, curr = runs[i - 1], runs[i]
        gap = _haversine(prev.end[0], prev.end[1], curr.start[0], curr.start[1])

        if gap > 50:
            if gap > MAX_CONNECTOR_M:
                # Too far: OSRM might produce a huge detour, but it's on real roads
                pass
            r = osrm_route(prev.end[0], prev.end[1], curr.start[0], curr.start[1])
            if r:
                route.append(_osrm_edge(r))

        route.append(_run_edge(curr))

    # Last run → end
    last = runs[-1]
    gap = _haversine(last.end[0], last.end[1], end_lat, end_lon)
    if gap > 50:
        r = osrm_route(last.end[0], last.end[1], end_lat, end_lon)
        if r:
            route.append(_osrm_edge(r))

    return route


def _run_edge(run: TourRun) -> dict[str, Any]:
    """Convert a TourRun to a route edge dict."""
    return {
        "distance_m": run.distance_m,
        "is_gravel": run.gravel_m > run.distance_m * 0.3,
        "gravel_m": run.gravel_m,
        "surface": "mixed",
        "tour_id": run.tour_id,
        "tour_name": run.tour_name,
        "segment_index": -1,
        "coords": run.coords,
        "tier": 1 if run.gravel_m > run.distance_m * 0.3 else 2,
        "near_highlights": [],
    }


def _osrm_edge(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "distance_m": r["distance_m"],
        "is_gravel": False,
        "gravel_m": 0,
        "surface": "road",
        "tour_id": -1,
        "tour_name": "osrm_connector",
        "segment_index": -1,
        "coords": r["coords"],
        "tier": 3,
        "near_highlights": [],
    }


# ---------------------------------------------------------------------------
# GPX output
# ---------------------------------------------------------------------------

def build_gpx(route: list[dict[str, Any]], route_name: str) -> str:
    """Stitch route edges into a GPX track."""
    gpx = gpxpy.gpx.GPX()
    track = gpxpy.gpx.GPXTrack(name=route_name)
    gpx.tracks.append(track)

    segment = gpxpy.gpx.GPXTrackSegment()
    track.segments.append(segment)

    for edge in route:
        for coord in edge.get("coords", []):
            lat, lon = coord[0], coord[1]
            alt = coord[2] if len(coord) > 2 else None
            segment.points.append(gpxpy.gpx.GPXTrackPoint(
                latitude=lat, longitude=lon, elevation=alt,
            ))

    return gpx.to_xml()


# ---------------------------------------------------------------------------
# Route statistics
# ---------------------------------------------------------------------------

def compute_route_stats(route: list[dict[str, Any]]) -> dict[str, Any]:
    total_distance = 0.0
    gravel_distance = 0.0
    osrm_distance = 0.0
    tours_used: dict[int, str] = {}
    osrm_connectors = 0
    highlights_visited: list[str] = []

    for edge in route:
        dist = edge.get("distance_m", 0)
        total_distance += dist
        gravel_distance += edge.get("gravel_m", 0) if "gravel_m" in edge else (dist if edge.get("is_gravel") else 0)

        if edge.get("tier") == 3:
            osrm_distance += dist
            osrm_connectors += 1

        tid = edge.get("tour_id", -1)
        if tid > 0:
            tours_used[tid] = edge.get("tour_name", "")

        for name in edge.get("near_highlights", []):
            if name and name not in highlights_visited:
                highlights_visited.append(name)

    gravel_pct = round(100 * gravel_distance / total_distance, 1) if total_distance > 0 else 0
    osrm_pct = round(100 * osrm_distance / total_distance, 1) if total_distance > 0 else 0

    return {
        "distance_km": round(total_distance / 1000, 2),
        "gravel_pct": gravel_pct,
        "gravel_km": round(gravel_distance / 1000, 2),
        "osrm_pct": osrm_pct,
        "osrm_km": round(osrm_distance / 1000, 2),
        "tours_used": [{"id": tid, "name": name} for tid, name in tours_used.items()],
        "segments_used": len(route),
        "osrm_connectors": osrm_connectors,
        "highlights_visited": highlights_visited,
    }


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _segments_from_cache(cached: list[dict]) -> list[Segment]:
    return [
        Segment(
            tour_id=s["tour_id"], tour_name=s["tour_name"],
            index=s["index"], surface=s["surface"], way_type=s["way_type"],
            is_gravel=s["is_gravel"],
            coords=[tuple(c) for c in s["coords"]],
            distance_m=s["distance_m"],
        )
        for s in cached
    ]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def combine_routes(
    tour_ids: list[int],
    start_lat: float,
    start_lon: float,
    distance_km: float = 50.0,
    min_gravel_pct: float = 60.0,
    route_name: str = "Gravel-AI route",
    use_saved_highlights: bool = True,
    force_refresh: bool = False,
    end_lat: float | None = None,
    end_lon: float | None = None,
) -> dict[str, Any]:
    """Build a gravel route with two outputs: waypoints GPX + tour-chain GPX.

    For point-to-point: provide end_lat/end_lon.
    For loops: omit them.

    Returns:
        {
            gpx_xml: str (tour-chain GPX: Option C),
            waypoints_gpx_xml: str (waypoints GPX: Option A),
            stats: {...},
            waypoints: [{lat, lon, name}, ...],
        }
    """
    global _osrm_cache, _osrm_call_count
    _osrm_cache = {}
    _osrm_call_count = 0

    is_loop = end_lat is None or end_lon is None
    eff_end_lat = start_lat if is_loop else end_lat
    eff_end_lon = start_lon if is_loop else end_lon

    # 1. Load segments from cache
    cache = load_cache() if not force_refresh else {"version": 1, "segments": {}, "highlights": None}
    cache_dirty = False

    all_segments: list[Segment] = []
    for tour_id in tour_ids:
        cached_segs = get_cached_segments(cache, tour_id)
        if cached_segs is not None:
            all_segments.extend(_segments_from_cache(cached_segs))
        else:
            tour_data = get_tour_segments(tour_id)
            segments = parse_segments(tour_data)
            all_segments.extend(segments)
            put_segments(cache, tour_id, tour_data["name"], tour_data["distance_km"], segments)
            cache_dirty = True

    if not all_segments:
        return {"error": "No segments could be extracted from the provided tours."}

    # 2. Load highlights
    highlights: list[dict[str, Any]] = []
    if use_saved_highlights:
        cached_hl = get_cached_highlights(cache)
        if cached_hl is not None:
            highlights = cached_hl
        else:
            try:
                highlights = get_saved_highlights()
                put_highlights(cache, highlights)
                cache_dirty = True
            except Exception:
                pass

    if cache_dirty:
        save_cache(cache)

    budget_m = distance_km * 1000

    # 3. Build tour runs within the corridor
    runs = _build_tour_runs(
        all_segments, start_lat, start_lon, eff_end_lat, eff_end_lon,
        corridor_width_m=CORRIDOR_WIDTH_M,
    )

    # 4. Select best non-overlapping runs
    selected_runs = _select_best_runs(runs, budget_m, highlights)

    # 5. Option A: Waypoints GPX
    waypoints = _extract_waypoints(
        selected_runs, start_lat, start_lon, eff_end_lat, eff_end_lon, highlights,
    )
    waypoints_gpx = _build_waypoints_gpx(waypoints, f"{route_name} (waypoints)")

    # 6. Option C: Tour-chain GPX
    route_edges = _connect_runs_with_osrm(
        selected_runs, start_lat, start_lon, eff_end_lat, eff_end_lon,
    )

    # Tag highlights
    for edge in route_edges:
        _tag_highlight_proximity(edge, highlights)

    stats = compute_route_stats(route_edges)
    chain_gpx = build_gpx(route_edges, route_name)

    return {
        "gpx_xml": chain_gpx,
        "waypoints_gpx_xml": waypoints_gpx,
        "stats": stats,
        "waypoints": [{"lat": w.lat, "lon": w.lon, "name": w.name} for w in waypoints],
    }


def _tag_highlight_proximity(edge: dict, highlights: list[dict]) -> None:
    coords = edge.get("coords", [])
    if not coords or not highlights:
        return
    samples = [coords[0], coords[len(coords) // 2], coords[-1]]
    names = []
    for pt in samples:
        for hl in highlights:
            if hl.get("lat") and hl.get("lon"):
                if _haversine(pt[0], pt[1], hl["lat"], hl["lon"]) < HIGHLIGHT_RADIUS_M:
                    name = hl.get("name", "")
                    if name and name not in names:
                        names.append(name)
    edge["near_highlights"] = names
