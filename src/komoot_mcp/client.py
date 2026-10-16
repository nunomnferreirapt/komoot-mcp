"""HTTP client for the Komoot API v007 using Basic Auth."""

from typing import Any, Optional

from . import auth

API_BASE = "https://api.komoot.de/v007"


def list_tours(
    sport_types: str = "",
    tour_type: str = "tour_planned",
    limit: int = 50,
    center_lat: Optional[float] = None,
    center_lon: Optional[float] = None,
    max_distance: Optional[int] = None,
) -> list[dict[str, Any]]:
    """List the authenticated user's tours with optional filtering."""
    komoot_auth = auth.get_auth()
    user_id = komoot_auth.user_id

    params: dict[str, Any] = {
        "type": tour_type,
        "limit": limit,
        "page": 0,
    }
    if sport_types:
        params["sport_types"] = sport_types
    if center_lat is not None and center_lon is not None:
        params["center"] = f"{center_lat},{center_lon}"
        if max_distance:
            params["max_distance"] = max_distance

    url = f"{API_BASE}/users/{user_id}/tours/"
    resp = auth.make_request("GET", url, params=params)
    data = resp.json()

    tours = []
    for tour in data.get("_embedded", {}).get("tours", []):
        tours.append(_extract_tour_summary(tour))
    return tours


def get_tour(
    tour_id: int,
    embed: str = "surfaces,way_types",
    include_coordinates: bool = False,
) -> dict[str, Any]:
    """Get full tour details including surfaces and way types.

    Args:
        tour_id: Komoot tour ID.
        embed: Comma-separated list of embedded data to request.
        include_coordinates: If True, also fetch and return full coordinate array.
            Defaults to False to avoid bloating the response (can be 1000+ points).
    """
    if include_coordinates and "coordinates" not in embed:
        embed = f"{embed},coordinates"

    url = f"{API_BASE}/tours/{tour_id}"
    params = {"_embedded": embed}
    resp = auth.make_request("GET", url, params=params)
    data = resp.json()

    result = _extract_tour_summary(data)

    embedded = data.get("_embedded", {})

    if "coordinates" in embedded:
        coords = embedded["coordinates"]
        result["coordinates"] = {
            "items": coords.get("items", []),
        }

    if "surfaces" in embedded:
        result["surfaces"] = _parse_segment_data(embedded["surfaces"])
        result["surface_breakdown"] = compute_gravel_percentage(result["surfaces"])

    if "way_types" in embedded:
        result["way_types"] = _parse_segment_data(embedded["way_types"])

    return result


def get_tour_segments(tour_id: int) -> dict[str, Any]:
    """Get raw segment data (coordinates + surfaces + way_types) for route combination.

    Unlike get_tour() which aggregates surface data into percentages, this returns
    the raw {from, to, element} arrays alongside full coordinates, so segments can
    be split and recombined by surface type.
    """
    url = f"{API_BASE}/tours/{tour_id}"
    params = {"_embedded": "coordinates,surfaces,way_types"}
    resp = auth.make_request("GET", url, params=params)
    data = resp.json()

    embedded = data.get("_embedded", {})

    return {
        "tour_id": tour_id,
        "name": data.get("name", ""),
        "distance_km": round(data.get("distance", 0) / 1000, 2),
        "coordinates": embedded.get("coordinates", {}).get("items", []),
        "surfaces": embedded.get("surfaces", {}).get("items", []),
        "way_types": embedded.get("way_types", {}).get("items", []),
    }


def get_tour_gpx(tour_id: int) -> str:
    """Download a tour as GPX XML."""
    url = f"{API_BASE}/tours/{tour_id}.gpx"
    resp = auth.make_request("GET", url)
    return resp.text


def upload_gpx(
    gpx_content: str,
    name: str,
    sport: str = "touringbicycle",
) -> dict[str, Any]:
    """Upload a GPX file to Komoot as a new planned tour."""
    url = f"{API_BASE}/tours/"
    params = {"data_type": "gpx", "sport": sport, "name": name}
    resp = auth.make_request(
        "POST", url, params=params,
        data=gpx_content.encode("utf-8"),
    )
    data = resp.json()

    tour_id = data.get("id", "")
    return {
        "tour_id": tour_id,
        "name": name,
        "url": f"https://www.komoot.com/tour/{tour_id}",
        "status": resp.status_code,
    }


def get_saved_highlights() -> list[dict[str, Any]]:
    """Get the authenticated user's saved/bookmarked highlights."""
    komoot_auth = auth.get_auth()
    user_id = komoot_auth.user_id

    url = f"{API_BASE}/users/{user_id}/highlights/"
    resp = auth.make_request("GET", url)
    data = resp.json()

    highlights = []
    for h in data.get("_embedded", {}).get("items", []):
        start_point = h.get("start_point", {})
        highlights.append({
            "id": h.get("id"),
            "name": h.get("name", ""),
            "type": h.get("type", ""),
            "category": h.get("category", ""),
            "categories": h.get("categories", []),
            "lat": start_point.get("lat"),
            "lon": start_point.get("lng"),
        })
    return highlights


def get_highlights(
    lat: float,
    lon: float,
    radius_m: int = 5000,
) -> list[dict[str, Any]]:
    """Get highlights/POIs near a location."""
    url = f"{API_BASE}/highlights/"
    params = {
        "lat": lat,
        "lng": lon,
        "radius": radius_m,
    }
    resp = auth.make_request("GET", url, params=params)
    data = resp.json()

    highlights = []
    for h in data.get("_embedded", {}).get("highlights", data.get("highlights", [])):
        highlights.append({
            "id": h.get("id"),
            "name": h.get("name", ""),
            "type": h.get("type", ""),
            "lat": h.get("lat") or h.get("_embedded", {}).get("location", {}).get("lat"),
            "lon": h.get("lng") or h.get("_embedded", {}).get("location", {}).get("lng"),
            "tip_count": h.get("tip_count", 0),
            "image_url": h.get("image_url", ""),
        })
    return highlights


def _extract_tour_summary(tour: dict[str, Any]) -> dict[str, Any]:
    """Extract key fields from a tour object."""
    return {
        "id": tour.get("id"),
        "name": tour.get("name", ""),
        "sport": tour.get("sport", ""),
        "type": tour.get("type", ""),
        "status": tour.get("status", ""),
        "distance_km": round(tour.get("distance", 0) / 1000, 2),
        "duration_min": round(tour.get("duration", 0) / 60, 1),
        "elevation_up_m": round(tour.get("elevation_up", 0), 0),
        "elevation_down_m": round(tour.get("elevation_down", 0), 0),
        "start_point": _extract_start_point(tour),
        "difficulty": (tour.get("difficulty") or {}).get("grade", ""),
        "date": tour.get("date", ""),
    }


def _extract_start_point(tour: dict[str, Any]) -> dict[str, float]:
    """Extract starting coordinates from tour."""
    start = tour.get("start_point", {})
    if start:
        return {"lat": start.get("lat", 0), "lon": start.get("lng", 0)}
    vector = tour.get("vector", {})
    if vector:
        return {"lat": vector.get("lat", 0), "lon": vector.get("lng", 0)}
    return {"lat": 0, "lon": 0}


def _parse_segment_data(data: dict[str, Any]) -> dict[str, Any]:
    """Parse surface or way_type segment data into aggregated percentages.

    The API returns items with {from, to, element} where from/to are point indices
    and element is prefixed (e.g. 'sb#asphalt', 'wt#cycleway').
    """
    items = data.get("items", [])
    if not items:
        return {"summary": {}, "total_segments": 0}

    type_spans: dict[str, int] = {}
    total_span = 0

    for item in items:
        element = item.get("element", "")
        clean_type = element.split("#", 1)[1] if "#" in element else element
        span = item.get("to", 0) - item.get("from", 0)
        type_spans[clean_type] = type_spans.get(clean_type, 0) + span
        total_span += span

    summary = {}
    for stype, span in sorted(type_spans.items(), key=lambda x: -x[1]):
        pct = round(100 * span / total_span, 1) if total_span > 0 else 0
        summary[stype] = {"span": span, "percentage": pct}

    return {"summary": summary, "total_segments": len(items)}


# Gravel-relevant surface types from Komoot
GRAVEL_SURFACES = {"compacted", "gravel", "fine_gravel", "ground", "grass", "sand", "dirt"}
PAVED_SURFACES = {"asphalt", "paved", "paving_stones", "concrete", "cobblestone", "wood"}


def compute_gravel_percentage(surfaces: dict[str, Any]) -> dict[str, float]:
    """Compute gravel vs paved percentage from parsed surface data."""
    summary = surfaces.get("summary", {})
    total = sum(v["span"] for v in summary.values())
    if total == 0:
        return {"gravel_pct": 0, "paved_pct": 0, "other_pct": 0}

    gravel_span = sum(v["span"] for k, v in summary.items() if k in GRAVEL_SURFACES)
    paved_span = sum(v["span"] for k, v in summary.items() if k in PAVED_SURFACES)
    other_span = total - gravel_span - paved_span

    return {
        "gravel_pct": round(100 * gravel_span / total, 1),
        "paved_pct": round(100 * paved_span / total, 1),
        "other_pct": round(100 * other_span / total, 1),
    }
