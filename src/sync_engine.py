"""Core sync logic for bidirectional SpoolEase <-> Spoolman synchronization.

Two concurrent loops:
1. Periodic poll: SpoolEase -> Spoolman (fetch spools, compute deltas, report usage)
2. WebSocket listener: Spoolman events (track Klipper usage, clean up deleted mappings)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from .config import BridgeConfig
from .mapping_store import MappingStore, _decode_extra_str
from .models import SpoolEaseRecord, SpoolMapping
from .spoolease_client import SpoolEaseClient
from .spoolman_client import SpoolmanClient

logger = logging.getLogger(__name__)

# Default filament density (g/cm³) for common materials
MATERIAL_DENSITIES: dict[str, float] = {
    "PLA": 1.24,
    "PETG": 1.27,
    "ABS": 1.04,
    "ASA": 1.07,
    "TPU": 1.21,
    "PA": 1.14,
    "PC": 1.20,
    "PVA": 1.23,
    "HIPS": 1.04,
}
DEFAULT_DENSITY = 1.24  # PLA as fallback


class SyncEngine:
    """Orchestrates bidirectional sync between SpoolEase and Spoolman."""

    def __init__(
        self,
        spoolease: SpoolEaseClient,
        spoolman: SpoolmanClient,
        mapping_store: MappingStore,
        config: BridgeConfig,
    ) -> None:
        self._spoolease = spoolease
        self._spoolman = spoolman
        self._store = mapping_store
        self._config = config

    async def full_sync(self) -> None:
        """Run a full synchronization cycle.

        For each SpoolEase spool with a valid tag_id:
        - If not mapped: auto-create in Spoolman and save mapping
        - If mapped: compute delta and report usage to Spoolman
        """
        records = await self._spoolease.get_spools()
        if records is None:
            logger.warning("Skipping sync — SpoolEase unreachable")
            return

        valid_records = [r for r in records if r.has_valid_tag_id()]
        logger.info("Syncing %d spools (%d with valid tags)", len(records), len(valid_records))

        for record in valid_records:
            try:
                await self._sync_single_spool(record)
            except Exception as e:
                logger.error("Failed to sync spool %s (tag=%s): %s", record.id, record.tag_id, e)

        self._store.save()

    async def _sync_single_spool(self, record: SpoolEaseRecord) -> None:
        """Sync a single SpoolEase spool to Spoolman."""
        mapping = self._store.get_by_tag_id(record.tag_id)

        if mapping is None:
            await self._create_spoolman_spool(record)
        else:
            await self._sync_existing_spool(record, mapping)

    async def _create_spoolman_spool(self, record: SpoolEaseRecord) -> None:
        """Create a new spool in Spoolman from a SpoolEase record."""
        logger.info(
            "New spool detected: tag=%s, %s %s %s",
            record.tag_id, record.brand, record.material_type, record.color_name,
        )

        # 1. Get or create vendor
        vendor_id = await self._spoolman.get_or_create_vendor(
            name=record.brand or "Unknown",
            empty_spool_weight=float(record.weight_core) if record.weight_core else None,
        )

        # 2. Get or create filament
        density = MATERIAL_DENSITIES.get(record.material_type.upper(), DEFAULT_DENSITY)
        filament_id = await self._spoolman.get_or_create_filament(
            vendor_id=vendor_id,
            name=record.color_name or record.material_type,
            material=record.material_type,
            color_hex=record.color_hex_rgb,
            weight=float(record.weight_advertised) if record.weight_advertised else None,
            spool_weight=float(record.weight_core) if record.weight_core else None,
        )

        # 3. Create spool
        extra = {
            self._config.spoolman_tag_id_field: record.tag_id,
            self._config.spoolman_spoolease_id_field: record.id,
        }
        spool = await self._spoolman.create_spool(
            filament_id=filament_id,
            initial_weight=float(record.weight_advertised) if record.weight_advertised else None,
            spool_weight=float(record.weight_core) if record.weight_core else None,
            used_weight=record.consumed_since_add,
            comment=record.note or "",
            extra=extra,
        )

        # 4. Save mapping
        mapping = SpoolMapping(
            tag_id=record.tag_id,
            spoolease_id=record.id,
            spoolman_spool_id=spool["id"],
            spoolman_filament_id=filament_id,
            last_known_consumed=record.consumed_since_add,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._store.set_mapping(mapping)
        logger.info(
            "Mapped SpoolEase spool %s (tag=%s) -> Spoolman spool %d",
            record.id, record.tag_id, spool["id"],
        )

    async def _sync_existing_spool(self, record: SpoolEaseRecord, mapping: SpoolMapping) -> None:
        """Sync consumption delta for an already-mapped spool."""
        delta = record.consumed_since_add - mapping.last_known_consumed

        if delta > self._config.delta_threshold:
            # Positive delta: filament was used on Bambu printer
            await self._spoolman.use_spool(mapping.spoolman_spool_id, delta)
            mapping.last_known_consumed = record.consumed_since_add
            self._store.set_mapping(mapping)
            logger.debug(
                "Synced +%.1fg for tag=%s (SpoolEase total: %.1fg)",
                delta, record.tag_id, record.consumed_since_add,
            )
        elif delta < -self._config.delta_threshold:
            # Negative delta: spool was likely reset (new spool on same tag)
            logger.warning(
                "Consumption decreased for tag=%s (%.1f -> %.1f) — likely spool reset/replacement",
                record.tag_id, mapping.last_known_consumed, record.consumed_since_add,
            )
            # Reset the tracking baseline; don't report negative usage
            mapping.last_known_consumed = record.consumed_since_add
            mapping.spoolease_id = record.id
            self._store.set_mapping(mapping)

        # Also sync metadata changes if they differ
        await self._sync_metadata(record, mapping)

    async def _sync_metadata(self, record: SpoolEaseRecord, mapping: SpoolMapping) -> None:
        """Check for metadata changes and update Spoolman if needed.

        Only syncs comment/note changes to avoid excessive API calls.
        """
        # We could add more metadata sync here in the future
        # For now, just ensure the SpoolEase ID is up to date
        if mapping.spoolease_id != record.id:
            mapping.spoolease_id = record.id
            try:
                await self._spoolman.update_spool(
                    mapping.spoolman_spool_id,
                    extra={
                        self._config.spoolman_tag_id_field: record.tag_id,
                        self._config.spoolman_spoolease_id_field: record.id,
                    },
                )
            except Exception as e:
                logger.debug("Failed to update metadata for spool %d: %s", mapping.spoolman_spool_id, e)

    # ── Periodic sync loop ───────────────────────────────────────────

    async def periodic_sync_loop(self) -> None:
        """Run full_sync in a loop with configured interval."""
        logger.info("Starting periodic sync loop (interval=%ds)", self._config.poll_interval_seconds)
        while True:
            try:
                await self.full_sync()
            except asyncio.CancelledError:
                logger.info("Periodic sync loop cancelled")
                return
            except Exception as e:
                logger.error("Sync cycle failed: %s", e)
            await asyncio.sleep(self._config.poll_interval_seconds)

    # ── WebSocket event handler ──────────────────────────────────────

    async def websocket_listener(self) -> None:
        """Listen to Spoolman WebSocket for spool events."""
        logger.info("Starting Spoolman WebSocket listener")
        await self._spoolman.listen_websocket(self._handle_ws_event)

    async def _handle_ws_event(self, event_type: str, payload: dict) -> None:
        """Handle a WebSocket event from Spoolman."""
        spool_id = payload.get("id")
        if spool_id is None:
            return

        if event_type == "deleted":
            mapping = self._store.get_by_spoolman_id(spool_id)
            if mapping:
                logger.info(
                    "Spoolman spool %d was deleted — removing mapping for tag=%s",
                    spool_id, mapping.tag_id,
                )
                self._store.remove_by_spoolman_id(spool_id)
                self._store.save()

        elif event_type == "updated":
            # Log if this is a spool we're tracking (could be Klipper usage)
            extra = payload.get("extra", {})
            tag_id_raw = extra.get(self._config.spoolman_tag_id_field)
            # Extra field values from Spoolman are JSON-encoded strings
            tag_id = _decode_extra_str(tag_id_raw)
            if tag_id:
                used_weight = payload.get("used_weight", 0)
                logger.debug(
                    "Spoolman spool %d updated (tag=%s, used_weight=%.1fg)",
                    spool_id, tag_id, used_weight,
                )
