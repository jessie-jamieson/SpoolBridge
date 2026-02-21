"""REST + WebSocket client for Spoolman API."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Coroutine

import aiohttp

from .config import BridgeConfig

logger = logging.getLogger(__name__)


def _json_encode_extra(extra: dict[str, str]) -> dict[str, str]:
    """Encode extra field values as JSON strings.

    Spoolman validates extra field values by calling json.loads(value),
    so each value must be a valid JSON literal (e.g. '"hello"' not 'hello').
    """
    return {k: json.dumps(v) for k, v in extra.items()}


class SpoolmanClient:
    """Communicates with Spoolman's REST API and WebSocket."""

    def __init__(self, config: BridgeConfig) -> None:
        self._base_url = config.spoolman_base_url
        self._ws_url = config.spoolman_ws_url
        self._tag_id_field = config.spoolman_tag_id_field
        self._spoolease_id_field = config.spoolman_spoolease_id_field
        self._timeout = aiohttp.ClientTimeout(total=10)
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    @staticmethod
    async def _raise_for_status(resp: aiohttp.ClientResponse, context: str = "") -> None:
        """Like raise_for_status but logs the response body for diagnostics."""
        if resp.status >= 400:
            body = await resp.text()
            prefix = f"{context}: " if context else ""
            logger.error(
                "%sHTTP %d from %s %s — body: %s",
                prefix, resp.status, resp.method, resp.url, body,
            )
            raise aiohttp.ClientResponseError(
                resp.request_info,
                resp.history,
                status=resp.status,
                message=f"{body}",
            )

    # ── Extra field setup ────────────────────────────────────────────

    async def ensure_extra_fields_exist(self, retries: int = 5, delay: float = 3.0) -> None:
        """Create the spoolease_tag_id and spoolease_id extra fields on spool entity if missing.

        Retries with delay to handle Spoolman not being ready at bridge startup.
        """
        # Define the fields we need
        needed_fields = {
            self._tag_id_field: {
                "name": "SpoolEase Tag ID",
                "field_type": "text",
                "order": 100,
            },
            self._spoolease_id_field: {
                "name": "SpoolEase ID",
                "field_type": "text",
                "order": 101,
            },
        }

        url = f"{self._base_url}/api/v1/field/spool"

        for attempt in range(1, retries + 1):
            try:
                session = await self._get_session()

                # Check existing fields
                async with session.get(url) as resp:
                    if resp.status == 200:
                        existing = await resp.json()
                        existing_keys = {f["key"] for f in existing}
                    else:
                        logger.warning(
                            "Failed to list extra fields (HTTP %d), attempt %d/%d",
                            resp.status, attempt, retries,
                        )
                        if attempt < retries:
                            await asyncio.sleep(delay)
                            continue
                        raise RuntimeError(f"Cannot list Spoolman extra fields after {retries} attempts")

                # Create missing fields
                all_ok = True
                for key, field_def in needed_fields.items():
                    if key in existing_keys:
                        logger.info("Extra field '%s' already exists", key)
                        continue
                    create_url = f"{url}/{key}"
                    async with session.post(create_url, json=field_def) as resp:
                        if resp.status in (200, 201):
                            logger.info("Created Spoolman extra field: %s", key)
                        else:
                            text = await resp.text()
                            logger.error(
                                "Failed to create extra field '%s': HTTP %d - %s",
                                key, resp.status, text,
                            )
                            all_ok = False

                if all_ok:
                    return
                if attempt < retries:
                    logger.info("Retrying extra field setup in %.0fs...", delay)
                    await asyncio.sleep(delay)
                else:
                    raise RuntimeError("Failed to create all required Spoolman extra fields")

            except (aiohttp.ClientError, OSError) as e:
                logger.warning(
                    "Spoolman not reachable for extra field setup (attempt %d/%d): %s",
                    attempt, retries, e,
                )
                if attempt < retries:
                    await asyncio.sleep(delay)
                else:
                    raise RuntimeError(f"Cannot reach Spoolman after {retries} attempts: {e}") from e

    # ── Vendor operations ────────────────────────────────────────────

    async def find_vendor(self, name: str) -> dict | None:
        """Find a vendor by exact name."""
        session = await self._get_session()
        url = f"{self._base_url}/api/v1/vendor"
        async with session.get(url, params={"name": name}) as resp:
            if resp.status != 200:
                return None
            vendors = await resp.json()
            # name param is partial match; find exact
            for v in vendors:
                if v["name"].lower() == name.lower():
                    return v
            return None

    async def create_vendor(self, name: str, empty_spool_weight: float | None = None) -> dict:
        """Create a new vendor in Spoolman."""
        session = await self._get_session()
        url = f"{self._base_url}/api/v1/vendor"
        payload: dict[str, Any] = {"name": name}
        if empty_spool_weight is not None:
            payload["empty_spool_weight"] = empty_spool_weight
        async with session.post(url, json=payload) as resp:
            await self._raise_for_status(resp, f"Create vendor '{name}'")
            vendor = await resp.json()
            logger.info("Created Spoolman vendor: %s (id=%d)", name, vendor["id"])
            return vendor

    async def get_or_create_vendor(self, name: str, empty_spool_weight: float | None = None) -> int:
        """Find or create a vendor. Returns the vendor ID."""
        if not name:
            name = "Unknown"
        existing = await self.find_vendor(name)
        if existing:
            return existing["id"]
        vendor = await self.create_vendor(name, empty_spool_weight)
        return vendor["id"]

    # ── Filament operations ──────────────────────────────────────────

    async def find_filament(self, vendor_id: int, material: str, color_hex: str) -> dict | None:
        """Find a filament by vendor, material, and color."""
        session = await self._get_session()
        url = f"{self._base_url}/api/v1/filament"
        params: dict[str, Any] = {
            "vendor.id": str(vendor_id),
            "material": material,
        }
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                return None
            filaments = await resp.json()
            # Check for exact color match
            for f in filaments:
                if (f.get("color_hex") or "").lower() == color_hex.lower():
                    return f
            # If no exact color match, return first material match
            return filaments[0] if filaments else None

    async def create_filament(
        self,
        name: str,
        vendor_id: int,
        material: str,
        color_hex: str,
        weight: float | None = None,
        spool_weight: float | None = None,
        density: float = 1.24,
        diameter: float = 1.75,
    ) -> dict:
        """Create a new filament in Spoolman."""
        session = await self._get_session()
        url = f"{self._base_url}/api/v1/filament"
        payload: dict[str, Any] = {
            "name": name,
            "vendor_id": vendor_id,
            "material": material,
            "density": density,
            "diameter": diameter,
        }
        if color_hex:
            payload["color_hex"] = color_hex
        if weight is not None:
            payload["weight"] = weight
        if spool_weight is not None:
            payload["spool_weight"] = spool_weight
        async with session.post(url, json=payload) as resp:
            await self._raise_for_status(resp, f"Create filament '{name}' (material={material})")
            filament = await resp.json()
            logger.info("Created Spoolman filament: %s %s (id=%d)", material, name, filament["id"])
            return filament

    async def get_or_create_filament(
        self,
        vendor_id: int,
        name: str,
        material: str,
        color_hex: str,
        weight: float | None = None,
        spool_weight: float | None = None,
    ) -> int:
        """Find or create a filament. Returns the filament ID."""
        existing = await self.find_filament(vendor_id, material, color_hex)
        if existing:
            return existing["id"]
        filament = await self.create_filament(
            name=name,
            vendor_id=vendor_id,
            material=material,
            color_hex=color_hex,
            weight=weight,
            spool_weight=spool_weight,
        )
        return filament["id"]

    # ── Spool operations ─────────────────────────────────────────────

    async def get_all_spools(self) -> list[dict]:
        """Get all spools from Spoolman."""
        session = await self._get_session()
        url = f"{self._base_url}/api/v1/spool"
        async with session.get(url, params={"allow_archived": "true"}) as resp:
            await self._raise_for_status(resp, "Get all spools")
            return await resp.json()

    async def create_spool(
        self,
        filament_id: int,
        initial_weight: float | None = None,
        spool_weight: float | None = None,
        used_weight: float = 0.0,
        comment: str = "",
        extra: dict[str, str] | None = None,
    ) -> dict:
        """Create a new spool in Spoolman."""
        session = await self._get_session()
        url = f"{self._base_url}/api/v1/spool"
        payload: dict[str, Any] = {"filament_id": filament_id}
        if initial_weight is not None:
            payload["initial_weight"] = initial_weight
        if spool_weight is not None:
            payload["spool_weight"] = spool_weight
        if used_weight > 0:
            payload["used_weight"] = used_weight
        if comment:
            payload["comment"] = comment
        if extra:
            payload["extra"] = _json_encode_extra(extra)
        logger.debug("Creating spool with payload: %s", payload)
        async with session.post(url, json=payload) as resp:
            await self._raise_for_status(resp, f"Create spool (filament_id={filament_id})")
            spool = await resp.json()
            logger.info("Created Spoolman spool (id=%d, filament_id=%d)", spool["id"], filament_id)
            return spool

    async def update_spool(self, spool_id: int, **fields: Any) -> dict:
        """Update specific fields on a Spoolman spool via PATCH."""
        if "extra" in fields and fields["extra"] is not None:
            fields["extra"] = _json_encode_extra(fields["extra"])
        session = await self._get_session()
        url = f"{self._base_url}/api/v1/spool/{spool_id}"
        async with session.patch(url, json=fields) as resp:
            await self._raise_for_status(resp, f"Update spool {spool_id}")
            return await resp.json()

    async def use_spool(self, spool_id: int, use_weight: float) -> dict:
        """Report filament consumption on a spool (incremental).

        Calls PUT /api/v1/spool/{id}/use which atomically increments used_weight.
        """
        session = await self._get_session()
        url = f"{self._base_url}/api/v1/spool/{spool_id}/use"
        async with session.put(url, json={"use_weight": use_weight}) as resp:
            await self._raise_for_status(resp, f"Use spool {spool_id}")
            spool = await resp.json()
            logger.info(
                "Reported %.1fg usage on Spoolman spool %d (total used: %.1fg)",
                use_weight,
                spool_id,
                spool.get("used_weight", 0),
            )
            return spool

    # ── WebSocket listener ───────────────────────────────────────────

    async def listen_websocket(
        self,
        callback: Callable[[str, dict], Coroutine[Any, Any, None]],
    ) -> None:
        """Connect to Spoolman WebSocket and invoke callback on spool events.

        Reconnects with exponential backoff on disconnection.
        callback(event_type, spool_payload) is called for each event.
        """
        backoff = 1
        max_backoff = 60
        ws_url = f"{self._ws_url}/api/v1/spool"

        while True:
            try:
                session = await self._get_session()
                async with session.ws_connect(ws_url) as ws:
                    logger.info("Connected to Spoolman WebSocket at %s", ws_url)
                    backoff = 1  # reset on successful connection
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                event = json.loads(msg.data)
                                event_type = event.get("type", "unknown")
                                payload = event.get("payload", {})
                                await callback(event_type, payload)
                            except json.JSONDecodeError:
                                logger.debug("Non-JSON WebSocket message: %s", msg.data[:100])
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            logger.warning("WebSocket error: %s", ws.exception())
                            break
                        elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED):
                            break
            except (aiohttp.ClientError, OSError) as e:
                logger.warning("Spoolman WebSocket connection failed: %s", e)
            except asyncio.CancelledError:
                logger.info("WebSocket listener cancelled")
                return

            logger.info("Reconnecting to Spoolman WebSocket in %ds...", backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)
