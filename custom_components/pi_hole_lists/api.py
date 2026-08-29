"""API client for the Pi-hole Lists integration.

Subclasses the ``HoleV6`` client from the exactly-pinned ``hole`` package
(bastgau/python-hole), reusing its session management (SID/CSRF tokens,
validity tracking, one-time 401 re-authentication) and adding the Pi-hole
v6 list endpoints used by this integration:

- ``GET /api/lists`` — rows are parsed into ``PiHoleList`` values
- ``PUT /api/lists/{quote(address, safe="")}?type=block`` — the body is built
  by ``PiHoleList.update_payload`` (``{"enabled": bool, "comment": str}``),
  which always echoes the comment because FTL's PUT is a full-row upsert
  that resets absent fields (see ``set_list_enabled``); the updated row is
  parsed back into a ``PiHoleList``

The subclass relies on ``HoleV6`` private internals (``_fetch_data``,
``_session_id``, ``_csrf_token``, ``ensure_auth``); ``hole==0.9.2`` is pinned
exactly and every path that uses them is covered by unit tests (see DESIGN.md).
"""

import asyncio
import logging
import socket
from urllib.parse import quote, urlparse

import aiohttp
from hole import HoleV6
from hole.exceptions import (
    HoleAuthenticationError,
    HoleConnectionError,
    HoleError,
    HoleResponseError,
)

from .models import PiHoleList

_LOGGER = logging.getLogger(__name__)


class PiHoleV6Lists(HoleV6):
    """Pi-hole v6 API client with blocklist (de)activation."""

    def __init__(
        self,
        url: str,
        password: str,
        *,
        session: aiohttp.ClientSession,
        verify_ssl: bool = True,
        timeout: int = 15,
    ) -> None:
        """Initialize the client from a ``scheme://host[:port]`` URL."""
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise HoleError(f"Invalid Pi-hole URL: {url}")
        try:
            port = parsed.port
        except ValueError as err:
            raise HoleError(f"Invalid port in Pi-hole URL: {url}") from err

        super().__init__(
            host=parsed.hostname,
            session=session,
            protocol=parsed.scheme,
            verify_tls=verify_ssl,
            password=password,
            port=port,
            timeout=timeout,
        )

    async def get_lists(self) -> list[PiHoleList]:
        """Fetch all lists (block and allow) from Pi-hole."""
        response = await self._fetch_data("/lists")
        return [PiHoleList.from_dict(row) for row in response["lists"]]

    async def set_list_enabled(
        self,
        list_obj: PiHoleList,
        enabled: bool,
    ) -> PiHoleList:
        """Enable or disable a list and return its updated state.

        FTL's PUT is a full-row upsert (``INSERT ... ON CONFLICT(address,type)
        DO UPDATE SET enabled, comment, type``), not a merge: any mutable
        field missing from the payload is reset to its default — ``comment``
        to NULL, ``enabled`` to true. ``PiHoleList.update_payload`` is the
        single source of the payload shape and always echoes the list's
        comment, otherwise toggling the switch silently wipes the list's
        comment in Pi-hole. Type is carried by the query parameter and groups
        are only touched when the payload contains a ``groups`` array (which
        we intentionally never send).
        """
        # The write path needs the full row: address and type are
        # URL/query parameters, so a model without them cannot be toggled.
        if not list_obj.address or not list_obj.type:
            raise HoleError(f"List {list_obj.id} is missing its address or type")
        await self.ensure_auth()

        # Pi-hole matches the list by its URL-encoded address; the query
        # parameter disambiguates the list type.
        url = (
            f"{self.base_url}/api/lists/{quote(list_obj.address, safe='')}"
            f"?type={list_obj.type}"
        )
        headers = {"X-FTL-SID": self._session_id}
        if self._csrf_token:
            headers["X-FTL-CSRF"] = self._csrf_token
        payload = list_obj.update_payload(enabled)

        try:
            async with asyncio.timeout(self.timeout):
                response = await self._session.put(
                    url, json=payload, headers=headers, ssl=self.verify_tls
                )

                # The session may have expired between polls: re-authenticate
                # once and retry, mirroring HoleV6._fetch_data.
                if response.status == 401:
                    _LOGGER.info("Session expired, re-authenticating")
                    await self.authenticate()
                    if self._session_id:
                        headers["X-FTL-SID"] = self._session_id
                        if self._csrf_token:
                            headers["X-FTL-CSRF"] = self._csrf_token
                    response = await self._session.put(
                        url, json=payload, headers=headers, ssl=self.verify_tls
                    )

                    # Still unauthorized after a retry means the credentials
                    # themselves are rejected, not just the session.
                    if response.status == 401:
                        raise HoleAuthenticationError(
                            "Authentication required", status=401
                        )

                if response.status != 200:
                    raise HoleError(
                        f"Failed to update list {list_obj.address}: {response.status}",
                        status=response.status,
                    )

                try:
                    response_data = await response.json()
                except (aiohttp.ContentTypeError, ValueError) as err:
                    raise HoleResponseError(
                        f"Invalid response updating list {list_obj.address}"
                    ) from err

                # FTL answers a single-item update with the full list wrapped
                # in {"lists": [...]} (plus a "processed" summary). Fall back
                # to parsing the bare body for older response shapes. Parse
                # failures are loud: a malformed response is a HoleResponseError
                # rather than a silently untyped row.
                try:
                    if "lists" in response_data:
                        lists = response_data["lists"]
                        if not lists:
                            raise HoleResponseError(
                                f"No list returned for {list_obj.address}"
                            )
                        updated_list = PiHoleList.from_dict(lists[0])
                    else:
                        updated_list = PiHoleList.from_dict(response_data)
                except (ValueError, TypeError) as err:
                    raise HoleResponseError(
                        f"Invalid response updating list {list_obj.address}"
                    ) from err
                return updated_list

        except (asyncio.TimeoutError, aiohttp.ClientError, socket.gaierror) as err:
            raise HoleConnectionError(
                f"Cannot update list {list_obj.address}: {err}"
            ) from err
