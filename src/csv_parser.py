"""Parse SpoolEase's CSV spool record format.

SpoolEase serializes SpoolRecord via serde_csv_core. The CSV has no header row.
Fields are in struct definition order from core/src/spool_record.rs.
Special serialization:
  - Optional<i32> fields: empty string for None, integer string for Some
  - Optional<bool>: "y"/"n"/"" (via serialize_optional_bool_yn)
  - bool: "y"/"n" (via serialize_bool_yn)
  - f32 fields: base64-no-pad encoded little-endian bytes, empty string for 0.0
"""

from __future__ import annotations

import base64
import csv
import io
import struct
from typing import Optional

from .models import SpoolEaseRecord


def _parse_f32_base64(s: str) -> float:
    """Decode a base64-no-pad encoded little-endian f32."""
    if not s:
        return 0.0
    padded = s + "=" * (-len(s) % 4)
    raw = base64.b64decode(padded)
    return struct.unpack("<f", raw)[0]


def _parse_optional_int(s: str) -> Optional[int]:
    if not s:
        return None
    return int(s)


def _parse_optional_bool_yn(s: str) -> Optional[bool]:
    if not s:
        return None
    return s.lower() == "y"


def _parse_bool_yn(s: str) -> bool:
    return s.lower() == "y"


def parse_spools_csv(csv_text: str) -> list[SpoolEaseRecord]:
    """Parse the decrypted CSV response from GET /api/spools.

    CSV field order matches the SpoolRecord struct field order:
    id, tag_id, material_type, material_subtype, color_name, color_code,
    note, brand, weight_advertised, weight_core, weight_new, weight_current,
    slicer_filament, added_time, encode_time, added_full,
    consumed_since_add, consumed_since_weight, ext_has_k, data_origin, tag_type
    """
    records = []
    reader = csv.reader(io.StringIO(csv_text))
    for row in reader:
        if not row or len(row) < 21:
            continue
        record = SpoolEaseRecord(
            id=row[0],
            tag_id=row[1],
            material_type=row[2],
            material_subtype=row[3],
            color_name=row[4],
            color_code=row[5],
            note=row[6],
            brand=row[7],
            weight_advertised=_parse_optional_int(row[8]),
            weight_core=_parse_optional_int(row[9]),
            weight_new=_parse_optional_int(row[10]),
            weight_current=_parse_optional_int(row[11]),
            slicer_filament=row[12],
            added_time=_parse_optional_int(row[13]),
            encode_time=_parse_optional_int(row[14]),
            added_full=_parse_optional_bool_yn(row[15]),
            consumed_since_add=_parse_f32_base64(row[16]),
            consumed_since_weight=_parse_f32_base64(row[17]),
            ext_has_k=_parse_bool_yn(row[18]),
            data_origin=row[19],
            tag_type=row[20],
        )
        records.append(record)
    return records
