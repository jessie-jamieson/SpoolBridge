"""Persistent mapping between SpoolEase and Spoolman spool records.

Stores mapping data as a JSON file with atomic writes to prevent corruption.
Can rebuild mapping from Spoolman extra fields if the mapping file is lost.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from typing import Any

from .models import SpoolMapping, SyncState

logger = logging.getLogger(__name__)


def _decode_extra_str(value: str | None) -> str | None:
    """Decode a Spoolman extra field value back to a plain string.

    Spoolman stores extra field values as JSON-encoded strings.
    e.g. the tag ID "04AA..." is stored as '"04AA..."' (with JSON quotes).
    This function handles both JSON-encoded and plain string values.
    """
    if not value:
        return None
    try:
        decoded = json.loads(value)
        return str(decoded)
    except (json.JSONDecodeError, TypeError):
        return value


class MappingStore:
    """Persistent JSON-file store for tag_id <-> spoolman_id mappings."""

    def __init__(self, file_path: str) -> None:
        self._file_path = file_path
        self._state = SyncState()

    @property
    def state(self) -> SyncState:
        return self._state

    def load(self) -> None:
        """Load mapping state from disk. Does nothing if file doesn't exist."""
        if not os.path.exists(self._file_path):
            logger.info("No mapping file found at %s, starting fresh", self._file_path)
            return
        try:
            with open(self._file_path) as f:
                data = json.load(f)
            self._state = _deserialize_state(data)
            logger.info("Loaded %d spool mappings from %s", len(self._state.mappings), self._file_path)
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.error("Failed to parse mapping file %s: %s — starting fresh", self._file_path, e)
            self._state = SyncState()

    def save(self) -> None:
        """Save mapping state to disk atomically."""
        self._state.last_sync_time = datetime.now(timezone.utc).isoformat()
        data = _serialize_state(self._state)

        # Ensure parent directory exists
        parent = os.path.dirname(self._file_path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        # Atomic write: write to temp file, then rename
        fd, tmp_path = tempfile.mkstemp(dir=parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_path, self._file_path)
        except Exception:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def get_by_tag_id(self, tag_id: str) -> SpoolMapping | None:
        return self._state.mappings.get(tag_id)

    def get_by_spoolman_id(self, spoolman_id: int) -> SpoolMapping | None:
        for mapping in self._state.mappings.values():
            if mapping.spoolman_spool_id == spoolman_id:
                return mapping
        return None

    def set_mapping(self, mapping: SpoolMapping) -> None:
        self._state.mappings[mapping.tag_id] = mapping

    def remove_by_tag_id(self, tag_id: str) -> None:
        self._state.mappings.pop(tag_id, None)

    def remove_by_spoolman_id(self, spoolman_id: int) -> None:
        to_remove = [
            tag_id
            for tag_id, m in self._state.mappings.items()
            if m.spoolman_spool_id == spoolman_id
        ]
        for tag_id in to_remove:
            del self._state.mappings[tag_id]

    def rebuild_from_spoolman_spools(
        self, spools: list[dict], tag_id_field: str, spoolease_id_field: str,
    ) -> int:
        """Rebuild mappings from Spoolman spools that have SpoolEase extra fields.

        Used when the mapping file is lost but the data exists in Spoolman.
        Returns the number of mappings recovered.
        """
        recovered = 0
        for spool in spools:
            extra = spool.get("extra", {})
            # Extra field values from Spoolman are JSON-encoded strings
            tag_id = _decode_extra_str(extra.get(tag_id_field))
            se_id = _decode_extra_str(extra.get(spoolease_id_field)) or ""
            if not tag_id:
                continue
            filament = spool.get("filament", {})
            mapping = SpoolMapping(
                tag_id=tag_id,
                spoolease_id=se_id,
                spoolman_spool_id=spool["id"],
                spoolman_filament_id=filament.get("id", 0),
                last_known_consumed=spool.get("used_weight", 0.0),
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            self._state.mappings[tag_id] = mapping
            recovered += 1
        if recovered:
            logger.info("Rebuilt %d mappings from Spoolman extra fields", recovered)
        return recovered


def _serialize_state(state: SyncState) -> dict[str, Any]:
    return {
        "last_sync_time": state.last_sync_time,
        "mappings": {
            tag_id: {
                "tag_id": m.tag_id,
                "spoolease_id": m.spoolease_id,
                "spoolman_spool_id": m.spoolman_spool_id,
                "spoolman_filament_id": m.spoolman_filament_id,
                "last_known_consumed": m.last_known_consumed,
                "created_at": m.created_at,
            }
            for tag_id, m in state.mappings.items()
        },
    }


def _deserialize_state(data: dict[str, Any]) -> SyncState:
    mappings = {}
    for tag_id, m in data.get("mappings", {}).items():
        mappings[tag_id] = SpoolMapping(
            tag_id=m["tag_id"],
            spoolease_id=m["spoolease_id"],
            spoolman_spool_id=m["spoolman_spool_id"],
            spoolman_filament_id=m["spoolman_filament_id"],
            last_known_consumed=m["last_known_consumed"],
            created_at=m["created_at"],
        )
    return SyncState(
        mappings=mappings,
        last_sync_time=data.get("last_sync_time"),
    )
