"""Tests for the SpoolEase CSV spool record parser."""

from __future__ import annotations

import base64
import struct

from src.csv_parser import _parse_f32_base64, parse_spools_csv


def _encode_f32(value: float) -> str:
    """Encode a float as base64-no-pad little-endian f32 (matching SpoolEase)."""
    if value == 0.0:
        return ""
    raw = struct.pack("<f", value)
    return base64.b64encode(raw).rstrip(b"=").decode("ascii")


class TestParseF32Base64:
    def test_zero(self):
        assert _parse_f32_base64("") == 0.0

    def test_positive(self):
        encoded = _encode_f32(42.5)
        result = _parse_f32_base64(encoded)
        assert abs(result - 42.5) < 0.001

    def test_small_value(self):
        encoded = _encode_f32(0.1)
        result = _parse_f32_base64(encoded)
        assert abs(result - 0.1) < 0.01

    def test_large_value(self):
        encoded = _encode_f32(1000.0)
        result = _parse_f32_base64(encoded)
        assert abs(result - 1000.0) < 0.1


class TestParseSpoolsCsv:
    def _make_row(
        self,
        id: str = "1",
        tag_id: str = "04A3B2C1D5E6F7",
        material_type: str = "PLA",
        material_subtype: str = "",
        color_name: str = "Black",
        color_code: str = "000000FF",
        note: str = "",
        brand: str = "Bambu",
        weight_advertised: str = "1000",
        weight_core: str = "200",
        weight_new: str = "",
        weight_current: str = "",
        slicer_filament: str = "",
        added_time: str = "",
        encode_time: str = "",
        added_full: str = "y",
        consumed_since_add: float = 0.0,
        consumed_since_weight: float = 0.0,
        ext_has_k: str = "n",
        data_origin: str = "",
        tag_type: str = "SpoolEaseV1",
    ) -> str:
        csa = _encode_f32(consumed_since_add)
        csw = _encode_f32(consumed_since_weight)
        fields = [
            id, tag_id, material_type, material_subtype, color_name, color_code,
            note, brand, weight_advertised, weight_core, weight_new, weight_current,
            slicer_filament, added_time, encode_time, added_full,
            csa, csw, ext_has_k, data_origin, tag_type,
        ]
        return ",".join(fields)

    def test_single_spool(self):
        csv = self._make_row()
        records = parse_spools_csv(csv)
        assert len(records) == 1
        r = records[0]
        assert r.id == "1"
        assert r.tag_id == "04A3B2C1D5E6F7"
        assert r.material_type == "PLA"
        assert r.color_name == "Black"
        assert r.color_code == "000000FF"
        assert r.brand == "Bambu"
        assert r.weight_advertised == 1000
        assert r.weight_core == 200
        assert r.weight_new is None
        assert r.weight_current is None
        assert r.added_full is True
        assert r.consumed_since_add == 0.0
        assert r.ext_has_k is False
        assert r.tag_type == "SpoolEaseV1"

    def test_with_consumption(self):
        csv = self._make_row(consumed_since_add=123.45, consumed_since_weight=50.0)
        records = parse_spools_csv(csv)
        r = records[0]
        assert abs(r.consumed_since_add - 123.45) < 0.1
        assert abs(r.consumed_since_weight - 50.0) < 0.1

    def test_multiple_spools(self):
        rows = [
            self._make_row(id="1", tag_id="AAAABBBBCCCCDD", material_type="PLA"),
            self._make_row(id="2", tag_id="11223344556677", material_type="PETG"),
            self._make_row(id="3", tag_id="FFEEDDCCBBAA99", material_type="ABS"),
        ]
        csv = "\n".join(rows)
        records = parse_spools_csv(csv)
        assert len(records) == 3
        assert records[0].material_type == "PLA"
        assert records[1].material_type == "PETG"
        assert records[2].material_type == "ABS"

    def test_empty_csv(self):
        records = parse_spools_csv("")
        assert records == []

    def test_optional_fields_empty(self):
        csv = self._make_row(
            weight_advertised="",
            weight_core="",
            added_time="",
            added_full="",
        )
        records = parse_spools_csv(csv)
        r = records[0]
        assert r.weight_advertised is None
        assert r.weight_core is None
        assert r.added_time is None
        assert r.added_full is None

    def test_valid_tag_id(self):
        csv = self._make_row(tag_id="04A3B2C1D5E6F7")
        r = parse_spools_csv(csv)[0]
        assert r.has_valid_tag_id() is True

    def test_invalid_tag_id_empty(self):
        csv = self._make_row(tag_id="")
        r = parse_spools_csv(csv)[0]
        assert r.has_valid_tag_id() is False

    def test_invalid_tag_id_dash(self):
        csv = self._make_row(tag_id="-04A3B2C1D5E6F")
        r = parse_spools_csv(csv)[0]
        assert r.has_valid_tag_id() is False

    def test_color_hex_rgb(self):
        csv = self._make_row(color_code="FF0000FF")
        r = parse_spools_csv(csv)[0]
        assert r.color_hex_rgb == "FF0000"

    def test_short_row_skipped(self):
        """Rows with fewer than 21 fields should be skipped."""
        csv = "1,04A3B2C1D5E6F7,PLA"
        records = parse_spools_csv(csv)
        assert records == []
