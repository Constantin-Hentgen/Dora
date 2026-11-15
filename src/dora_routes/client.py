"""Typed client for a Dora HTTP service. No web framework and no BRouter needed on the calling side.

    from dora_routes import DoraClient

    with DoraClient("http://localhost:8000") as dora:
        result = dora.find_loops(place="bois de vincennes", distance_km=10, lang="fr")
        for loop in result.loops:
            print(loop.caption)
            open(f"{loop.id}.gpx", "wb").write(dora.gpx(loop))

`AsyncDoraClient` has the same methods, awaited. Requests are validated locally with the same `LoopRequest` the
service uses, so a typo fails before any network call (`pydantic.ValidationError`)."""

from __future__ import annotations

import base64
from typing import Any

import httpx

from dora_routes import __version__
from dora_routes.api import PlaceNotFoundError as LibraryPlaceNotFoundError
from dora_routes.models import LoopOut, LoopRequest, LoopsResponse

DEFAULT_TIMEOUT_S = 60.0  # a little over the service's 45 s request budget


class DoraError(Exception):
    """The service answered with an error. `status_code` and `detail` come from its JSON body."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"HTTP {status_code}: {detail}")
        self.status_code, self.detail = status_code, detail


class PlaceNotFoundError(DoraError, LibraryPlaceNotFoundError):
    """`place` could not be geocoded (HTTP 404). Also a `dora_routes.PlaceNotFoundError`, so one `except` covers both
    the library and the HTTP client."""


class ServiceUnavailableError(DoraError):
    """BRouter or the geocoder is unreachable behind the service (HTTP 502/503/504)."""


def _raise_for_error(res: httpx.Response, *, place: bool = False) -> None:
    if res.is_success:
        return
    try:
        body = res.json()
        detail = body["detail"] if isinstance(body["detail"], str) else str(body["detail"])
    except (ValueError, KeyError, TypeError):
        detail = res.text[:200]
    if res.status_code == httpx.codes.NOT_FOUND and place:
        raise PlaceNotFoundError(res.status_code, detail)
    if res.status_code in {502, 503, 504}:
        raise ServiceUnavailableError(res.status_code, detail)
    raise DoraError(res.status_code, detail)


def _build(request: LoopRequest | None, fields: dict[str, Any]) -> tuple[LoopRequest, bool]:
    req = request or LoopRequest(**fields)
    return req, req.place is not None


def _file_bytes(loop: LoopOut, kind: str) -> bytes | None:
    inline = loop.gpx_base64 if kind == "gpx" else loop.png_base64
    return base64.b64decode(inline) if inline else None


class DoraClient:
    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        *,
        timeout: float = DEFAULT_TIMEOUT_S,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers={"User-Agent": f"dora-routes-client/{__version__}"},
            transport=transport,
        )

    def __enter__(self) -> DoraClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def find_loops(
        self, request: LoopRequest | None = None, *, include: tuple[str, ...] = (), **fields: Any
    ) -> LoopsResponse:
        """`POST /v1/loops`. `include=("gpx", "png")` embeds the files, so `gpx()`/`png()` need no extra call."""
        req, has_place = _build(request, fields)
        res = self._client.post("/v1/loops", json=req.model_dump(mode="json", exclude_none=True), params=_inc(include))
        _raise_for_error(res, place=has_place)
        return LoopsResponse.model_validate(res.json())

    def gpx(self, loop: LoopOut) -> bytes:
        return _file_bytes(loop, "gpx") or self._get(f"/v1/loops/{loop.id}.gpx")

    def png(self, loop: LoopOut) -> bytes:
        return _file_bytes(loop, "png") or self._get(f"/v1/loops/{loop.id}.png")

    def health(self) -> dict[str, Any]:
        res = self._client.get("/health")
        if res.status_code not in {200, 503}:
            _raise_for_error(res)
        return res.json()

    def _get(self, path: str) -> bytes:
        res = self._client.get(path)
        _raise_for_error(res)
        return res.content


class AsyncDoraClient:
    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        *,
        timeout: float = DEFAULT_TIMEOUT_S,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers={"User-Agent": f"dora-routes-client/{__version__}"},
            transport=transport,
        )

    async def __aenter__(self) -> AsyncDoraClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def find_loops(
        self, request: LoopRequest | None = None, *, include: tuple[str, ...] = (), **fields: Any
    ) -> LoopsResponse:
        req, has_place = _build(request, fields)
        res = await self._client.post(
            "/v1/loops", json=req.model_dump(mode="json", exclude_none=True), params=_inc(include)
        )
        _raise_for_error(res, place=has_place)
        return LoopsResponse.model_validate(res.json())

    async def gpx(self, loop: LoopOut) -> bytes:
        return _file_bytes(loop, "gpx") or await self._get(f"/v1/loops/{loop.id}.gpx")

    async def png(self, loop: LoopOut) -> bytes:
        return _file_bytes(loop, "png") or await self._get(f"/v1/loops/{loop.id}.png")

    async def health(self) -> dict[str, Any]:
        res = await self._client.get("/health")
        if res.status_code not in {200, 503}:
            _raise_for_error(res)
        return res.json()

    async def _get(self, path: str) -> bytes:
        res = await self._client.get(path)
        _raise_for_error(res)
        return res.content


def _inc(include: tuple[str, ...]) -> list[tuple[str, str]]:
    return [("include", kind) for kind in include]
