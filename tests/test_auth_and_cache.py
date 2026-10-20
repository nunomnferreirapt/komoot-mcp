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


def test_sign_in_connection_error_hides_the_email(monkeypatch):
    def refuse(url, **kwargs):
        raise auth.requests.ConnectionError(f"Max retries exceeded with url: {url}")

    monkeypatch.setattr(auth.requests, "get", refuse)
    with pytest.raises(RuntimeError) as excinfo:
        auth.authenticate("rider@example.com", "placeholder")
    assert "rider@example.com" not in str(excinfo.value)
    assert excinfo.value.__cause__ is None


def test_api_requests_get_a_default_timeout(monkeypatch):
    seen = {}

    class Ok:
        status_code = 200

        def raise_for_status(self):
            pass

    def fake_request(method, url, **kwargs):
        seen.update(kwargs)
        return Ok()

    monkeypatch.setattr(auth, "get_auth", lambda: auth.KomootAuth("12345", "token", "Rider", 0.0))
    monkeypatch.setattr(auth.requests, "request", fake_request)
    auth.make_request("GET", "https://api.komoot.de/v007/tours/1")
    assert seen["timeout"] == auth._REQUEST_TIMEOUT


def test_setup_credentials_stores_nothing_when_sign_in_fails(monkeypatch):
    import asyncio

    from komoot_mcp import __main__ as server

    def fail(email, password):
        raise RuntimeError("Komoot sign-in failed with HTTP 401")

    monkeypatch.setattr(auth, "authenticate", fail)
    monkeypatch.setattr(auth, "store_credentials", lambda *a: pytest.fail("stored before verifying"))
    result = asyncio.run(server.setup_credentials("rider@example.com", "wrong"))
    assert result.startswith("Credentials not stored")
