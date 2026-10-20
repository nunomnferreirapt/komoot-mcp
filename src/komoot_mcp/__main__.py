"""Komoot MCP server: access tours, surfaces, highlights, and GPX data."""

import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from . import auth, client, route_combiner

mcp = FastMCP("komoot-mcp")

DEFAULT_ROUTES_DIR = "~/komoot-mcp-routes"


def routes_dir() -> Path:
    """Where built routes are saved: $KOMOOT_MCP_ROUTES_DIR, or ~/komoot-mcp-routes."""
    return Path(os.environ.get("KOMOOT_MCP_ROUTES_DIR", DEFAULT_ROUTES_DIR)).expanduser()


@mcp.tool()
async def setup_credentials(email: str, password: str) -> str:
    """Store Komoot credentials in the system keychain for future use.

    Call this once before using any other Komoot tools, unless you set the
    KOMOOT_EMAIL and KOMOOT_PASSWORD environment variables instead.
    Credentials are verified first, then stored in the system keychain under
    service 'komoot-mcp'; a failed sign-in stores nothing.
    """
    try:
        komoot_auth = auth.authenticate(email, password)
    except Exception as e:
        return f"Credentials not stored, verification failed: {e}. Check email/password."
    auth.store_credentials(email, password)
    return f"Credentials verified and stored. User: {komoot_auth.display_name} (ID: {komoot_auth.user_id})"


@mcp.tool()
async def list_tours(
    sport_type: str = "",
    tour_type: str = "tour_planned",
    limit: int = 50,
    near_lat: float | None = None,
    near_lon: float | None = None,
    radius_km: float = 25.0,
) -> str:
    """List your Komoot tours with optional filtering by sport type, location, and radius.

    Use near_lat/near_lon to search tours near a geographic point.

    Args:
        sport_type: Sport type filter. Empty string = all sports. Common: touringbicycle, mtb, mtb_easy, racebike, hike.
        tour_type: Tour type. 'tour_planned' for planned, 'tour_recorded' for recorded.
        limit: Max number of tours to return.
        near_lat: Filter by latitude (center point).
        near_lon: Filter by longitude (center point).
        radius_km: Search radius in km (used with near_lat/near_lon).
    """
    try:
        max_distance = int(radius_km * 1000) if near_lat is not None else None
        tours = client.list_tours(
            sport_types=sport_type,
            tour_type=tour_type,
            limit=limit,
            center_lat=near_lat,
            center_lon=near_lon,
            max_distance=max_distance,
        )
        return json.dumps(tours, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
async def get_tour_details(
    tour_id: int,
    include_coordinates: bool = False,
) -> str:
    """Get full details of a Komoot tour including surface types and way types.

    Returns surface breakdown (gravel, asphalt, dirt, etc.) and way types
    (path, cycleway, street, etc.) which are essential for gravel ride planning.

    Args:
        tour_id: The Komoot tour ID.
        include_coordinates: If True, include full GPS coordinate array (can be 1000+ points).
            Defaults to False to keep responses compact for scoring/ranking.
    """
    try:
        tour = client.get_tour(tour_id=tour_id, include_coordinates=include_coordinates)
        return json.dumps(tour, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
async def download_tour_gpx(tour_id: int, save_path: str = "") -> str:
    """Download a Komoot tour as GPX file.

    Args:
        tour_id: The Komoot tour ID.
        save_path: Optional file path to save the GPX. If empty, returns GPX XML as text.
    """
    try:
        gpx_xml = client.get_tour_gpx(tour_id=tour_id)
        if save_path:
            path = Path(save_path).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(gpx_xml, encoding="utf-8")
            return f"GPX saved to {path} ({len(gpx_xml)} bytes)"
        return gpx_xml
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
async def upload_gpx_to_komoot(
    gpx_path: str,
    name: str,
    sport: str = "touringbicycle",
) -> str:
    """Upload a GPX file to Komoot as a new planned tour.

    Args:
        gpx_path: Path to the GPX file to upload.
        name: Name for the new tour on Komoot.
        sport: Sport type. Common: touringbicycle, mtb, racebike.
    """
    path = Path(gpx_path).expanduser()
    if not path.exists():
        return json.dumps({"error": f"GPX file not found at {path}"})

    try:
        gpx_content = path.read_text(encoding="utf-8")
        result = client.upload_gpx(
            gpx_content=gpx_content,
            name=name,
            sport=sport,
        )
        return json.dumps(result, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
async def get_highlights(lat: float, lon: float, radius_m: int = 5000) -> str:
    """Get highlights and points of interest near a location.

    Highlights include scenic viewpoints, interesting places, and user-recommended
    spots that are useful for planning attractive gravel routes.

    Args:
        lat: Latitude of the center point.
        lon: Longitude of the center point.
        radius_m: Search radius in meters (default 5000).
    """
    try:
        highlights = client.get_highlights(lat=lat, lon=lon, radius_m=radius_m)
        return json.dumps(highlights, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
async def get_saved_highlights() -> str:
    """Get your saved/bookmarked Komoot highlights.

    Returns your personally saved highlights (scenic spots, viewpoints, interesting
    places you've bookmarked). These are used as waypoint attractors when building
    combined routes: the route combiner prefers paths that pass near your saved highlights.
    """
    try:
        highlights = client.get_saved_highlights()
        return json.dumps(highlights, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
async def get_tour_segments(tour_id: int) -> str:
    """Get raw segment data (coordinates + surfaces + way types) for a tour.

    Unlike get_tour_details which aggregates surface data, this returns the raw
    segment arrays needed for route combination: splitting tours by surface type
    and recombining the best gravel segments.

    Args:
        tour_id: The Komoot tour ID.
    """
    try:
        data = client.get_tour_segments(tour_id=tour_id)
        return json.dumps({
            "tour_id": data["tour_id"],
            "name": data["name"],
            "distance_km": data["distance_km"],
            "num_coordinates": len(data["coordinates"]),
            "num_surface_segments": len(data["surfaces"]),
            "num_way_type_segments": len(data["way_types"]),
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
async def build_combined_route(
    tour_ids: list[int],
    start_lat: float,
    start_lon: float,
    distance_km: float = 50.0,
    min_gravel_pct: float = 60.0,
    route_name: str = "Gravel-AI route",
    save_path: str = "",
    use_saved_highlights: bool = True,
    force_refresh: bool = False,
    end_lat: float | None = None,
    end_lon: float | None = None,
) -> str:
    """Build a custom gravel route by combining the best segments from multiple saved tours.

    Selects gravel segments from saved tours within a corridor between start and end,
    ordered by forward progress. Connects them via OSRM cycling router. Every km follows
    either a saved-tour path or a short OSRM connector.

    For loops: omit end_lat/end_lon. For point-to-point: provide both.

    Uses a disk cache for parsed segments: only fetches new tours from the API.
    Set force_refresh=True to re-fetch all tour data.

    Args:
        tour_ids: List of Komoot tour IDs to extract segments from.
        start_lat: Starting point latitude.
        start_lon: Starting point longitude.
        distance_km: Target route distance in km (default 50).
        min_gravel_pct: Minimum gravel percentage target (default 60).
        route_name: Name for the combined route.
        save_path: File path to save GPX. Defaults to $KOMOOT_MCP_ROUTES_DIR/<route-name>.gpx
            (or ~/komoot-mcp-routes/ when the variable is not set).
        use_saved_highlights: If True, fetch saved highlights and use as waypoint attractors (default True).
        force_refresh: If True, ignore cache and re-fetch all tour data from Komoot API (default False).
        end_lat: End point latitude (for point-to-point routes). Omit for loops.
        end_lon: End point longitude (for point-to-point routes). Omit for loops.
    """
    try:
        result = route_combiner.combine_routes(
            tour_ids=tour_ids,
            start_lat=start_lat,
            start_lon=start_lon,
            distance_km=distance_km,
            min_gravel_pct=min_gravel_pct,
            route_name=route_name,
            use_saved_highlights=use_saved_highlights,
            force_refresh=force_refresh,
            end_lat=end_lat,
            end_lon=end_lon,
        )

        if "error" in result:
            return json.dumps(result)

        # Determine save path
        if save_path:
            path = Path(save_path).expanduser()
        else:
            path = routes_dir() / f"{route_name.replace(' ', '-').lower()}.gpx"
        path.parent.mkdir(parents=True, exist_ok=True)

        # Save Option C: tour-chain GPX (full track)
        path.write_text(result["gpx_xml"], encoding="utf-8")

        # Save Option A: waypoints GPX
        wp_path = path.with_name(path.stem + "-waypoints.gpx")
        wp_path.write_text(result["waypoints_gpx_xml"], encoding="utf-8")

        output = {
            "gpx_path": str(path),
            "waypoints_gpx_path": str(wp_path),
            "waypoints": result.get("waypoints", []),
            **result["stats"],
        }
        return json.dumps(output, indent=2)

    except Exception as e:
        return json.dumps({"error": str(e)})


def main():
    """Run the Komoot MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
