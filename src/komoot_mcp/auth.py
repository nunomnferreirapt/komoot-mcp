"""Komoot authentication: credentials from env vars or the system keychain, Basic Auth against api.komoot.de."""

import base64
import os
import time
from dataclasses import dataclass
from typing import Optional

import keyring
import requests

SERVICE_NAME = "komoot-mcp"
ENV_EMAIL = "KOMOOT_EMAIL"
ENV_PASSWORD = "KOMOOT_PASSWORD"
AUTH_URL = "https://api.komoot.de/v006/account/email/{email}/"

# Token TTL: KomootGPX caches tokens for 15 min; we use 10 min to be safe
_TOKEN_TTL = 600
_REQUEST_TIMEOUT = 30


@dataclass
class KomootAuth:
    """Authenticated Komoot credentials."""
    user_id: str
    token: str
    display_name: str
    created_at: float


_cached_auth: Optional[KomootAuth] = None


def store_credentials(email: str, password: str) -> None:
    """Store Komoot credentials in the system keychain (service 'komoot-mcp')."""
    keyring.set_password(SERVICE_NAME, "email", email)
    keyring.set_password(SERVICE_NAME, "password", password)


def get_credentials() -> tuple[str, str]:
    """Retrieve Komoot credentials.

    Order: KOMOOT_EMAIL and KOMOOT_PASSWORD environment variables, then the
    system keychain entry written by store_credentials().
    """
    email = os.environ.get(ENV_EMAIL)
    password = os.environ.get(ENV_PASSWORD)
    if email and password:
        return email, password
    email = keyring.get_password(SERVICE_NAME, "email")
    password = keyring.get_password(SERVICE_NAME, "password")
    if not email or not password:
        raise ValueError(
            "Komoot credentials not found. Set KOMOOT_EMAIL and KOMOOT_PASSWORD, "
            "or use the setup_credentials tool to store them in the keychain."
        )
    return email, password


def _basic_auth_header(username: str, password: str) -> str:
    """Create Basic Auth header value."""
    encoded = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("utf-8")
    return f"Basic {encoded}"


def authenticate(email: str, password: str) -> KomootAuth:
    """Authenticate with Komoot using Basic Auth.

    Returns user_id and session token for subsequent requests.
    Subsequent requests use Basic Auth with user_id:token (not email:password).
    """
    url = AUTH_URL.format(email=email)
    headers = {
        "Authorization": _basic_auth_header(email, password),
    }

    # No URL in any message: the sign-in URL carries the email.
    try:
        resp = requests.get(url, headers=headers, timeout=_REQUEST_TIMEOUT)
    except requests.RequestException as e:
        raise RuntimeError(f"Komoot sign-in failed: {type(e).__name__}, could not reach api.komoot.de") from None
    if resp.status_code >= 400:
        raise RuntimeError(f"Komoot sign-in failed with HTTP {resp.status_code}")

    data = resp.json()
    return KomootAuth(
        user_id=str(data["username"]),
        token=data["password"],
        display_name=data.get("user", {}).get("displayname", ""),
        created_at=time.time(),
    )


def _force_reauth() -> KomootAuth:
    """Force re-authentication, ignoring cached token."""
    global _cached_auth
    email, password = get_credentials()
    _cached_auth = authenticate(email, password)
    return _cached_auth


def get_auth() -> KomootAuth:
    """Get valid authentication, re-authenticating if token expired."""
    global _cached_auth

    now = time.time()
    if _cached_auth and (now - _cached_auth.created_at) < _TOKEN_TTL:
        return _cached_auth

    return _force_reauth()


def make_request(
    method: str,
    url: str,
    **kwargs,
) -> requests.Response:
    """Make an authenticated request to the Komoot API.

    Retries once with fresh auth on 401 (token expired server-side).
    """
    auth = get_auth()
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = _basic_auth_header(auth.user_id, auth.token)
    kwargs.setdefault("timeout", _REQUEST_TIMEOUT)

    resp = requests.request(method, url, headers=headers, **kwargs)

    # Retry once on 401: token may have been invalidated server-side
    if resp.status_code == 401:
        auth = _force_reauth()
        headers["Authorization"] = _basic_auth_header(auth.user_id, auth.token)
        resp = requests.request(method, url, headers=headers, **kwargs)

    resp.raise_for_status()
    return resp
