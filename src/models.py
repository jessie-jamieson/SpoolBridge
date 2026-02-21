"""Shared data models for the bridge service."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SpoolEaseRecord:
    """Mirrors SpoolEase's SpoolRecord struct (core/src/spool_record.rs)."""

    id: str
    tag_id: str  # 14-char hex (7 bytes), e.g. "04A3B2C1D5E6F7"
    material_type: str  # e.g. "PLA", "PETG", "ASA"
    material_subtype: str  # e.g. "CF", "Basic"
    color_name: str  # e.g. "Black", "Red"
    color_code: str  # 8-char RGBA hex, e.g. "FF0000FF"
    note: str
    brand: str
    weight_advertised: Optional[int]  # label weight in grams
    weight_core: Optional[int]  # empty spool weight in grams
    weight_new: Optional[int]  # initial full weight when marked new
    weight_current: Optional[int]  # latest scale measurement in grams
    slicer_filament: str
    added_time: Optional[int]  # unix timestamp
    encode_time: Optional[int]
    added_full: Optional[bool]
    consumed_since_add: float  # grams, total consumed since spool added
    consumed_since_weight: float  # grams, consumed since last weighed
    ext_has_k: bool
    data_origin: str
    tag_type: str  # "SpoolEaseV1", "Bambu Lab", "OpenPrintTag"

    def has_valid_tag_id(self) -> bool:
        """Check if the spool has a valid tag ID for mapping.

        Matches SpoolEase's has_valid_tag_id(): non-empty and not starting with '-'.
        Tags starting with '-' are invalidated (moved to another spool).
        """
        return bool(self.tag_id) and not self.tag_id.startswith("-")

    @property
    def color_hex_rgb(self) -> str:
        """Return the color code as 6-char RGB hex (strips alpha channel)."""
        if len(self.color_code) >= 6:
            return self.color_code[:6]
        return self.color_code


@dataclass
class SpoolMapping:
    """Links a spool between SpoolEase and Spoolman via NFC tag ID."""

    tag_id: str  # the NFC tag hex ID (primary link)
    spoolease_id: str  # SpoolEase's numeric ID
    spoolman_spool_id: int  # Spoolman's integer spool ID
    spoolman_filament_id: int  # Spoolman's filament record ID
    last_known_consumed: float  # last consumed_since_add value we synced
    created_at: str  # ISO timestamp


@dataclass
class SyncState:
    """Persistent state for the sync engine."""

    mappings: dict[str, SpoolMapping] = field(default_factory=dict)  # keyed by tag_id
    last_sync_time: Optional[str] = None
