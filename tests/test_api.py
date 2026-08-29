"""Tests for the Pi-hole Lists API client (aioresponses-based).

Covers every path that relies on HoleV6 private internals: auth parsing,
lists parsing, the PUT URL/path/headers, the one-time 401 re-authentication
retry, and the session-validity re-authentication.
"""

import time
from urllib.parse import unquote

import aiohttp
import pytest
from aioresponses import aioresponses
from hole.exceptions import (
    HoleAuthenticationError,
    HoleConnectionError,
    HoleError,
    HoleResponseError,
)

from custom_components.pi_hole_lists.api import PiHoleV6Lists
from custom_components.pi_hole_lists.models import PiHoleList

URL = "http://pi.hole:8081"
PASSWORD = "super-secret-app-password"

AUTH_URL = f"{URL}/api/auth"
LISTS_URL = f"{URL}/api/lists"
ADDRESS = "https://example.com/ads.txt"

AUTH_ENDPOINT = "/api/auth"
LIST_ENDPOINT = "/api/lists"

BLOCK_LIST = {
    "id": 1,
    "address": ADDRESS,
    "enabled": True,
    "groups": [1, 2],
    "type": "block",
    "comment": "Example blocklist",
    "number": 12345,
    "invalid_domains": 3,
    "status": "enabled",
    "date_added": 1750000000,
    "date_modified": 1750000000,
    "date_updated": 1750000000,
}
ALLOW_LIST = {
    **BLOCK_LIST,
    "id": 2,
    "address": "https://example.com/allow.txt",
    "type": "allow",
    "comment": "Example allowlist",
}


def _list_model(**overrides: object) -> PiHoleList:
    """Build the block-list model, optionally overriding fields."""
    return PiHoleList.from_dict({**BLOCK_LIST, **overrides})


def _session_payload(sid: str = "sid-123", csrf: str = "csrf-456") -> dict:
    """Return a typical Pi-hole v6 auth response."""
    return {"session": {"valid": True, "sid": sid, "csrf": csrf, "validity": 300}}


def _list_update_payload(list_obj: dict) -> dict:
    """Return the real FTL response shape for a single-item list update.

    Verified in FTL source (api_list.c, v6.1 and master): the PUT handler
    applies the payload and answers with the row read back, wrapped in
    ``{"lists": [...], "processed": {...}}``.
    """
    return {
        "lists": [list_obj],
        "processed": {"errors": [], "success": [{"item": list_obj["address"]}]},
    }


def _request_calls(mocked: aioresponses, method: str, path: str) -> list:
    """Return the recorded request calls for a method and API path prefix.

    ``mocked.requests`` maps ``(method, url)`` to a list of calls, so repeated
    calls to the same URL accumulate in one list.
    """
    calls = []
    for (req_method, url), request_calls in mocked.requests.items():
        if req_method == method and str(url).startswith(f"{URL}{path}"):
            calls.extend(request_calls)
    return calls


def _request_urls(mocked: aioresponses, method: str, path: str) -> list:
    """Return the recorded URLs (yarl objects) for a method and path prefix."""
    return [
        url
        for (req_method, url) in mocked.requests
        if req_method == method and str(url).startswith(f"{URL}{path}")
    ]


@pytest.mark.asyncio
async def test_authenticate_parses_session():
    """Authenticate stores sid/csrf/validity; logout clears the session."""
    with aioresponses() as mocked:
        mocked.post(AUTH_URL, status=200, payload=_session_payload())
        async with aiohttp.ClientSession() as session:
            api = PiHoleV6Lists(URL, PASSWORD, session=session)
            await api.authenticate()

            assert api._session_id == "sid-123"
            assert api._csrf_token == "csrf-456"
            assert api._session_validity is not None

            mocked.delete(AUTH_URL, status=200)
            await api.logout()

            assert api._session_id is None
            assert api._session_validity is None


@pytest.mark.asyncio
async def test_authenticate_raises_on_invalid_password():
    """A 401 from the auth endpoint surfaces as HoleAuthenticationError."""
    with aioresponses() as mocked:
        mocked.post(
            AUTH_URL,
            status=401,
            payload={"error": {"message": "Unauthorized"}},
        )
        async with aiohttp.ClientSession() as session:
            api = PiHoleV6Lists(URL, PASSWORD, session=session)
            with pytest.raises(HoleAuthenticationError):
                await api.authenticate()


@pytest.mark.asyncio
async def test_authenticate_wraps_connection_errors():
    """Network errors during authentication surface as HoleConnectionError."""
    with aioresponses() as mocked:
        mocked.post(AUTH_URL, exception=aiohttp.ClientConnectionError("no route"))
        async with aiohttp.ClientSession() as session:
            api = PiHoleV6Lists(URL, PASSWORD, session=session)
            with pytest.raises(HoleConnectionError):
                await api.authenticate()


@pytest.mark.asyncio
async def test_get_lists_parses_lists():
    """GET /api/lists returns the parsed list objects and sends the SID."""
    with aioresponses() as mocked:
        mocked.post(AUTH_URL, status=200, payload=_session_payload())
        mocked.get(LISTS_URL, status=200, payload={"lists": [BLOCK_LIST, ALLOW_LIST]})
        async with aiohttp.ClientSession() as session:
            api = PiHoleV6Lists(URL, PASSWORD, session=session)
            lists = await api.get_lists()

        assert lists == [
            PiHoleList.from_dict(BLOCK_LIST),
            PiHoleList.from_dict(ALLOW_LIST),
        ]

        assert len(_request_urls(mocked, "GET", LIST_ENDPOINT)) == 1
        assert (
            str(_request_urls(mocked, "GET", LIST_ENDPOINT)[0])
            == f"{URL}{LIST_ENDPOINT}"
        )
        call = _request_calls(mocked, "GET", LIST_ENDPOINT)[0]
        assert call.kwargs["headers"]["X-FTL-SID"] == "sid-123"
        assert call.kwargs["headers"]["X-FTL-CSRF"] == "csrf-456"


@pytest.mark.asyncio
async def test_get_lists_reauth_once_on_401():
    """A 401 triggers exactly one re-authentication and one retry."""
    with aioresponses() as mocked:
        mocked.post(AUTH_URL, status=200, payload=_session_payload(), repeat=True)
        mocked.delete(AUTH_URL, status=200)
        mocked.get(LISTS_URL, status=401)
        mocked.get(LISTS_URL, status=200, payload={"lists": [BLOCK_LIST]})
        async with aiohttp.ClientSession() as session:
            api = PiHoleV6Lists(URL, PASSWORD, session=session)
            lists = await api.get_lists()

        assert lists == [PiHoleList.from_dict(BLOCK_LIST)]
        assert len(_request_calls(mocked, "GET", LIST_ENDPOINT)) == 2
        assert len(_request_calls(mocked, "POST", AUTH_ENDPOINT)) == 2
        assert len(_request_calls(mocked, "DELETE", AUTH_ENDPOINT)) == 1


@pytest.mark.asyncio
async def test_get_lists_reauth_when_session_expired():
    """An idle session past its validity re-authenticates on the next poll."""
    with aioresponses() as mocked:
        mocked.post(AUTH_URL, status=200, payload=_session_payload(), repeat=True)
        mocked.delete(AUTH_URL, status=200)
        mocked.get(LISTS_URL, status=200, payload={"lists": [BLOCK_LIST]})
        async with aiohttp.ClientSession() as session:
            api = PiHoleV6Lists(URL, PASSWORD, session=session)
            await api.authenticate()
            assert api._session_id == "sid-123"

            # Simulate a session that outlived its 300 s idle validity.
            api._session_validity = time.time() - 1
            lists = await api.get_lists()

        assert lists == [PiHoleList.from_dict(BLOCK_LIST)]
        assert len(_request_calls(mocked, "POST", AUTH_ENDPOINT)) == 2
        assert len(_request_calls(mocked, "DELETE", AUTH_ENDPOINT)) == 1


@pytest.mark.asyncio
async def test_set_list_enabled_sends_put():
    """The PUT hits the URL-encoded address with type=block and full headers."""
    updated = {**BLOCK_LIST, "enabled": False}
    with aioresponses() as mocked:
        mocked.post(AUTH_URL, status=200, payload=_session_payload())
        mocked.put(
            f"{URL}/api/lists/https%3A%2F%2Fexample.com%2Fads.txt?type=block",
            status=200,
            payload=_list_update_payload(updated),
        )
        async with aiohttp.ClientSession() as session:
            api = PiHoleV6Lists(URL, PASSWORD, session=session)
            result = await api.set_list_enabled(_list_model(), False)

        assert result == PiHoleList.from_dict(updated)

        put_calls = _request_calls(mocked, "PUT", LIST_ENDPOINT)
        assert len(put_calls) == 1
        # The address is sent as a single URL-encoded path segment. yarl
        # decodes %3A/%2F in .path, so assert on the raw (encoded) path.
        urls = _request_urls(mocked, "PUT", LIST_ENDPOINT)
        assert len(urls) == 1
        url = urls[0]
        assert url.raw_path.startswith(f"{LIST_ENDPOINT}/")
        encoded_address = url.raw_path.rsplit("/", 1)[-1]
        assert unquote(encoded_address) == ADDRESS
        assert url.query.get("type") == "block"

        call = put_calls[0]
        assert call.kwargs["headers"]["X-FTL-SID"] == "sid-123"
        assert call.kwargs["headers"]["X-FTL-CSRF"] == "csrf-456"
        # FTL's PUT replaces the row: the comment must be echoed or it is
        # reset to NULL (regression: toggling used to wipe the comment and
        # rename the HA entity to its address fallback).
        assert call.kwargs["json"] == {
            "enabled": False,
            "comment": "Example blocklist",
        }


@pytest.mark.asyncio
async def test_set_list_enabled_sends_null_comment_when_none():
    """A list without a comment is toggled with a JSON null comment.

    FTL treats JSON null and a missing key identically (both leave the
    comment NULL), but ``update_payload`` always carries the key so the
    payload shape stays uniform.
    """
    updated = {**BLOCK_LIST, "enabled": True, "comment": None}
    with aioresponses() as mocked:
        mocked.post(AUTH_URL, status=200, payload=_session_payload())
        mocked.put(
            f"{URL}/api/lists/https%3A%2F%2Fexample.com%2Fads.txt?type=block",
            status=200,
            payload=_list_update_payload(updated),
        )
        async with aiohttp.ClientSession() as session:
            api = PiHoleV6Lists(URL, PASSWORD, session=session)
            result = await api.set_list_enabled(_list_model(comment=None), True)

        assert result == PiHoleList.from_dict(updated)
        call = _request_calls(mocked, "PUT", LIST_ENDPOINT)[0]
        assert call.kwargs["json"] == {"enabled": True, "comment": None}


@pytest.mark.asyncio
async def test_set_list_enabled_parses_bare_list_response():
    """A response without the "lists" wrapper is parsed as a bare row."""
    updated = {**BLOCK_LIST, "enabled": True}
    with aioresponses() as mocked:
        mocked.post(AUTH_URL, status=200, payload=_session_payload())
        mocked.put(
            f"{URL}/api/lists/https%3A%2F%2Fexample.com%2Fads.txt?type=block",
            status=200,
            payload=updated,
        )
        async with aiohttp.ClientSession() as session:
            api = PiHoleV6Lists(URL, PASSWORD, session=session)
            result = await api.set_list_enabled(_list_model(), True)

        assert result == PiHoleList.from_dict(updated)


@pytest.mark.asyncio
async def test_set_list_enabled_raises_on_empty_lists_wrapper():
    """An empty "lists" wrapper surfaces as HoleResponseError."""
    with aioresponses() as mocked:
        mocked.post(AUTH_URL, status=200, payload=_session_payload())
        mocked.put(
            f"{URL}/api/lists/https%3A%2F%2Fexample.com%2Fads.txt?type=block",
            status=200,
            payload={"lists": []},
        )
        async with aiohttp.ClientSession() as session:
            api = PiHoleV6Lists(URL, PASSWORD, session=session)
            with pytest.raises(HoleResponseError):
                await api.set_list_enabled(_list_model(), True)


@pytest.mark.asyncio
async def test_set_list_enabled_raises_on_unparseable_response():
    """A response that cannot be parsed surfaces as HoleResponseError."""
    with aioresponses() as mocked:
        mocked.post(AUTH_URL, status=200, payload=_session_payload())
        mocked.put(
            f"{URL}/api/lists/https%3A%2F%2Fexample.com%2Fads.txt?type=block",
            status=200,
            payload={"lists": [{"status": "enabled"}]},
        )
        async with aiohttp.ClientSession() as session:
            api = PiHoleV6Lists(URL, PASSWORD, session=session)
            with pytest.raises(HoleResponseError):
                await api.set_list_enabled(_list_model(), True)


@pytest.mark.asyncio
async def test_set_list_enabled_reauth_once_on_401():
    """A 401 on the PUT triggers exactly one re-auth and one retry."""
    updated = {**BLOCK_LIST, "enabled": True}
    with aioresponses() as mocked:
        mocked.post(AUTH_URL, status=200, payload=_session_payload(), repeat=True)
        mocked.delete(AUTH_URL, status=200)
        mocked.put(
            f"{URL}/api/lists/https%3A%2F%2Fexample.com%2Fads.txt?type=block",
            status=401,
        )
        mocked.put(
            f"{URL}/api/lists/https%3A%2F%2Fexample.com%2Fads.txt?type=block",
            status=200,
            payload=_list_update_payload(updated),
        )
        async with aiohttp.ClientSession() as session:
            api = PiHoleV6Lists(URL, PASSWORD, session=session)
            result = await api.set_list_enabled(_list_model(), True)

        assert result == PiHoleList.from_dict(updated)
        assert len(_request_calls(mocked, "PUT", LIST_ENDPOINT)) == 2
        assert len(_request_calls(mocked, "POST", AUTH_ENDPOINT)) == 2
        assert len(_request_calls(mocked, "DELETE", AUTH_ENDPOINT)) == 1


@pytest.mark.asyncio
async def test_set_list_enabled_raises_on_401_after_retry():
    """Persistent 401 on the PUT surfaces as HoleAuthenticationError."""
    with aioresponses() as mocked:
        mocked.post(AUTH_URL, status=200, payload=_session_payload(), repeat=True)
        mocked.delete(AUTH_URL, status=200)
        mocked.put(
            f"{URL}/api/lists/https%3A%2F%2Fexample.com%2Fads.txt?type=block",
            status=401,
            repeat=True,
        )
        async with aiohttp.ClientSession() as session:
            api = PiHoleV6Lists(URL, PASSWORD, session=session)
            with pytest.raises(HoleAuthenticationError):
                await api.set_list_enabled(_list_model(), True)


@pytest.mark.asyncio
async def test_set_list_enabled_raises_on_non_200():
    """A non-200 PUT response surfaces as HoleError with its status."""
    with aioresponses() as mocked:
        mocked.post(AUTH_URL, status=200, payload=_session_payload())
        mocked.put(
            f"{URL}/api/lists/https%3A%2F%2Fexample.com%2Fads.txt?type=block",
            status=500,
            payload={"error": {"message": "Internal error"}},
        )
        async with aiohttp.ClientSession() as session:
            api = PiHoleV6Lists(URL, PASSWORD, session=session)
            with pytest.raises(HoleError) as exc_info:
                await api.set_list_enabled(_list_model(), True)

        assert exc_info.value.status == 500


@pytest.mark.asyncio
async def test_set_list_enabled_wraps_connection_errors():
    """Network errors during the PUT surface as HoleConnectionError."""
    with aioresponses() as mocked:
        mocked.post(AUTH_URL, status=200, payload=_session_payload())
        mocked.put(
            f"{URL}/api/lists/https%3A%2F%2Fexample.com%2Fads.txt?type=block",
            exception=aiohttp.ClientConnectionError("connection reset"),
        )
        async with aiohttp.ClientSession() as session:
            api = PiHoleV6Lists(URL, PASSWORD, session=session)
            with pytest.raises(HoleConnectionError):
                await api.set_list_enabled(_list_model(), True)


@pytest.mark.asyncio
async def test_init_rejects_url_without_scheme():
    """A scheme-less URL is rejected up front with a HoleError."""
    async with aiohttp.ClientSession() as session:
        with pytest.raises(HoleError):
            PiHoleV6Lists("pi.hole:8081", PASSWORD, session=session)


@pytest.mark.asyncio
async def test_init_accepts_https_url():
    """An https URL maps to the https protocol with verify_ssl honored."""
    async with aiohttp.ClientSession() as session:
        api = PiHoleV6Lists(
            "https://pi.hole:8443", PASSWORD, session=session, verify_ssl=False
        )
        assert api.protocol == "https"
        assert api.port == 8443
        assert api.base_url == "https://pi.hole:8443"
        assert api.verify_tls is False
