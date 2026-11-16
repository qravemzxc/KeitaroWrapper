"""Thin async client for the Keitaro Admin API (v1)."""

import time
from typing import Any

import httpx

from app.config import Settings, get_settings


class KeitaroError(Exception):
    def __init__(self, message: str, status_code: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


class KeitaroClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.admin_api_url,
            headers={"Api-Key": settings.keitaro_api_key, "Accept": "application/json"},
            timeout=settings.keitaro_timeout,
            transport=transport,
        )
        self._offers_cache: tuple[float, list[dict]] | None = None

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            resp = await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise KeitaroError(f"Keitaro недоступен: {exc}") from exc
        if resp.status_code >= 400:
            try:
                payload = resp.json()
            except ValueError:
                payload = resp.text
            raise KeitaroError(_error_text(payload, resp.status_code), resp.status_code, payload)
        if not resp.content:
            return None
        return resp.json()

    # --- dictionaries -------------------------------------------------------
    async def domains(self) -> list[dict]:
        return await self._request("GET", "/domains")

    async def campaign_groups(self) -> list[dict]:
        return await self._request("GET", "/groups", params={"type": "campaigns"})

    async def traffic_sources(self) -> list[dict]:
        return await self._request("GET", "/traffic_sources")

    async def offers(self, force: bool = False) -> list[dict]:
        now = time.monotonic()
        if not force and self._offers_cache and now - self._offers_cache[0] < self.settings.offers_cache_ttl:
            return self._offers_cache[1]
        data = await self._request("GET", "/offers")
        self._offers_cache = (now, data)
        return data

    async def offer(self, offer_id: int) -> dict:
        return await self._request("GET", f"/offers/{offer_id}")

    # --- campaigns ----------------------------------------------------------
    async def campaigns(self) -> list[dict]:
        return await self._request("GET", "/campaigns")

    async def campaign(self, campaign_id: int) -> dict:
        return await self._request("GET", f"/campaigns/{campaign_id}")

    async def create_campaign(self, data: dict) -> dict:
        return await self._request("POST", "/campaigns", json=data)

    async def delete_campaign(self, campaign_id: int) -> None:
        await self._request("DELETE", f"/campaigns/{campaign_id}")

    # --- streams ------------------------------------------------------------
    async def streams(self, campaign_id: int) -> list[dict]:
        return await self._request("GET", f"/campaigns/{campaign_id}/streams")

    async def stream(self, stream_id: int) -> dict:
        return await self._request("GET", f"/streams/{stream_id}")

    async def create_stream(self, data: dict) -> dict:
        return await self._request("POST", "/streams", json=data)

    async def update_stream(self, stream_id: int, data: dict) -> dict:
        # Keitaro replaces the `offers` list of the stream with the given one,
        # matching existing rows by offer_id.
        return await self._request("PUT", f"/streams/{stream_id}", json=data)

    # --- reports ------------------------------------------------------------
    async def report(self, payload: dict) -> dict:
        return await self._request("POST", "/report/build", json=payload)


def _error_text(payload: Any, status: int) -> str:
    if isinstance(payload, dict):
        if "error" in payload:
            return f"Keitaro {status}: {payload['error']}"
        parts = []
        for field, msgs in payload.items():
            msgs = msgs if isinstance(msgs, list) else [msgs]
            parts.append(f"{field}: {'; '.join(map(str, msgs))}")
        return f"Keitaro {status}: " + ", ".join(parts)
    return f"Keitaro {status}: {str(payload)[:300]}"


_client: KeitaroClient | None = None


def get_keitaro() -> KeitaroClient:
    global _client
    if _client is None:
        _client = KeitaroClient(get_settings())
    return _client


async def close_keitaro() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
