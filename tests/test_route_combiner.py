"""Pure geometry and segment splitting in the route combiner. No network."""

from komoot_mcp import route_combiner as rc


def test_haversine_amsterdam_to_utrecht_is_about_35_km():
    d = rc._haversine(52.3676, 4.9041, 52.0907, 5.1214)
    assert 34_000 < d < 36_000


def test_parse_segments_splits_on_surface_change():
    coords = [{"lat": 52.0 + i * 0.001, "lng": 4.9, "alt": 0.0} for i in range(6)]
    tour = {
        "tour_id": 7, "name": "Test tour",
        "coordinates": coords,
        "surfaces": [
            {"from": 0, "to": 3, "element": "sb#asphalt"},
            {"from": 3, "to": 6, "element": "sb#gravel"},
        ],
        "way_types": [{"from": 0, "to": 6, "element": "wt#path"}],
    }
    segments = rc.parse_segments(tour)
    assert [s.surface for s in segments] == ["asphalt", "gravel"]
    assert [s.is_gravel for s in segments] == [False, True]
    assert all(s.way_type == "path" for s in segments)
    assert sum(s.distance_m for s in segments) > 500


def test_compute_route_stats_counts_connectors_and_gravel():
    route = [
        {"distance_m": 8000, "gravel_m": 6000, "tier": 1, "tour_id": 1, "tour_name": "A", "near_highlights": ["Mill"]},
        {"distance_m": 2000, "gravel_m": 0, "tier": 3, "tour_id": -1, "tour_name": "osrm_connector", "near_highlights": []},
    ]
    stats = rc.compute_route_stats(route)
    assert stats["distance_km"] == 10.0
    assert stats["gravel_pct"] == 60.0
    assert stats["osrm_connectors"] == 1
    assert stats["tours_used"] == [{"id": 1, "name": "A"}]
    assert stats["highlights_visited"] == ["Mill"]
