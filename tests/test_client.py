"""Tests for the Komoot client parsing logic. No network: HTTP calls are mocked."""

from unittest.mock import MagicMock

from komoot_mcp import auth, client


def test_parse_segment_data_strips_prefix_and_computes_percentages():
    data = {"items": [
        {"from": 0, "to": 30, "element": "sb#asphalt"},
        {"from": 30, "to": 100, "element": "sb#gravel"},
    ]}
    parsed = client._parse_segment_data(data)
    assert parsed["total_segments"] == 2
    assert parsed["summary"]["gravel"]["percentage"] == 70.0
    assert parsed["summary"]["asphalt"]["span"] == 30


def test_compute_gravel_percentage_groups_surface_families():
    surfaces = {"summary": {
        "compacted": {"span": 50, "percentage": 50},
        "asphalt": {"span": 40, "percentage": 40},
        "unknown": {"span": 10, "percentage": 10},
    }}
    pct = client.compute_gravel_percentage(surfaces)
    assert pct == {"gravel_pct": 50.0, "paved_pct": 40.0, "other_pct": 10.0}


def test_list_tours_builds_request_and_summaries(monkeypatch):
    fake_auth = auth.KomootAuth(user_id="12345", token="t", display_name="x", created_at=0)
    monkeypatch.setattr(auth, "get_auth", lambda: fake_auth)

    response = MagicMock()
    response.json.return_value = {"_embedded": {"tours": [{
        "id": 1, "name": "Polder loop", "sport": "touringbicycle", "type": "tour_planned",
        "distance": 52000, "duration": 10800, "elevation_up": 120, "elevation_down": 118,
        "start_point": {"lat": 52.37, "lng": 4.90},
    }]}}
    captured = {}

    def fake_request(method, url, **kwargs):
        captured["method"], captured["url"], captured["params"] = method, url, kwargs["params"]
        return response

    monkeypatch.setattr(auth, "make_request", fake_request)

    tours = client.list_tours(center_lat=52.37, center_lon=4.90, max_distance=25000)

    assert captured["method"] == "GET"
    assert captured["url"].endswith("/users/12345/tours/")
    assert captured["params"]["center"] == "52.37,4.9"
    assert captured["params"]["max_distance"] == 25000
    assert tours == [{
        "id": 1, "name": "Polder loop", "sport": "touringbicycle", "type": "tour_planned",
        "status": "", "distance_km": 52.0, "duration_min": 180.0,
        "elevation_up_m": 120, "elevation_down_m": 118,
        "start_point": {"lat": 52.37, "lon": 4.90}, "difficulty": "", "date": "",
    }]
