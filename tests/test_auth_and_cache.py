"""Credential lookup order and cache location. No network, no real keychain."""

import pytest

from komoot_mcp import auth, cache


def test_env_vars_take_precedence_over_keyring(monkeypatch):
    monkeypatch.setenv("KOMOOT_EMAIL", "rider@example.com")
    monkeypatch.setenv("KOMOOT_PASSWORD", "placeholder")
    monkeypatch.setattr(auth.keyring, "get_password", lambda *a: pytest.fail("keyring queried"))
    assert auth.get_credentials() == ("rider@example.com", "placeholder")


def test_missing_credentials_raise_with_guidance(monkeypatch):
    monkeypatch.delenv("KOMOOT_EMAIL", raising=False)
    monkeypatch.delenv("KOMOOT_PASSWORD", raising=False)
    monkeypatch.setattr(auth.keyring, "get_password", lambda *a: None)
    with pytest.raises(ValueError, match="KOMOOT_EMAIL"):
        auth.get_credentials()


def test_cache_round_trip_in_configured_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("KOMOOT_MCP_CACHE_DIR", str(tmp_path / "cache"))
    assert cache.load_cache() == {"version": 1, "segments": {}, "highlights": None}

    data = cache.load_cache()
    cache.put_highlights(data, [{"id": 1, "name": "Windmill", "lat": 52.3, "lon": 4.9}])
    cache.save_cache(data)

    assert (tmp_path / "cache" / "route_cache.json").exists()
    reloaded = cache.load_cache()
    assert cache.get_cached_highlights(reloaded)[0]["name"] == "Windmill"
    assert cache.get_cached_segments(reloaded, 999) is None
