"""Tests for the sync engine — the core sync logic."""

from __future__ import annotations

import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from src.config import BridgeConfig
from src.mapping_store import MappingStore
from src.models import SpoolEaseRecord, SpoolMapping
from src.sync_engine import SyncEngine


def _make_record(
    id: str = "1",
    tag_id: str = "04A3B2C1D5E6F7",
    material: str = "PLA",
    brand: str = "Bambu",
    color_name: str = "Black",
    color_code: str = "000000FF",
    consumed: float = 0.0,
    weight_advertised: int | None = 1000,
    weight_core: int | None = 200,
) -> SpoolEaseRecord:
    return SpoolEaseRecord(
        id=id,
        tag_id=tag_id,
        material_type=material,
        material_subtype="",
        color_name=color_name,
        color_code=color_code,
        note="",
        brand=brand,
        weight_advertised=weight_advertised,
        weight_core=weight_core,
        weight_new=None,
        weight_current=None,
        slicer_filament="",
        added_time=None,
        encode_time=None,
        added_full=True,
        consumed_since_add=consumed,
        consumed_since_weight=0.0,
        ext_has_k=False,
        data_origin="",
        tag_type="SpoolEaseV1",
    )


@pytest.fixture
def config() -> BridgeConfig:
    return BridgeConfig(
        spoolease_host="192.168.1.50",
        spoolease_security_key="TESTKEY",
        delta_threshold=0.1,
        mapping_file_path=tempfile.mktemp(suffix=".json"),
    )


@pytest.fixture
def spoolease_mock() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def spoolman_mock() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def mapping_store(config) -> MappingStore:
    store = MappingStore(config.mapping_file_path)
    return store


@pytest.fixture
def engine(spoolease_mock, spoolman_mock, mapping_store, config) -> SyncEngine:
    return SyncEngine(spoolease_mock, spoolman_mock, mapping_store, config)


class TestNewSpoolDetection:
    @pytest.mark.asyncio
    async def test_creates_spool_in_spoolman(self, engine, spoolease_mock, spoolman_mock, mapping_store):
        """New spool in SpoolEase should create vendor, filament, and spool in Spoolman."""
        record = _make_record()
        spoolease_mock.get_spools.return_value = [record]
        spoolman_mock.get_or_create_vendor.return_value = 1
        spoolman_mock.get_or_create_filament.return_value = 10
        spoolman_mock.create_spool.return_value = {"id": 42}

        await engine.full_sync()

        spoolman_mock.get_or_create_vendor.assert_called_once()
        spoolman_mock.get_or_create_filament.assert_called_once()
        spoolman_mock.create_spool.assert_called_once()

        # Mapping should be saved
        mapping = mapping_store.get_by_tag_id("04A3B2C1D5E6F7")
        assert mapping is not None
        assert mapping.spoolman_spool_id == 42
        assert mapping.spoolman_filament_id == 10

    @pytest.mark.asyncio
    async def test_skips_spool_without_tag(self, engine, spoolease_mock, spoolman_mock):
        """Spools without a valid tag_id should be skipped."""
        record = _make_record(tag_id="")
        spoolease_mock.get_spools.return_value = [record]

        await engine.full_sync()

        spoolman_mock.create_spool.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_spool_with_dash_tag(self, engine, spoolease_mock, spoolman_mock):
        """Spools with invalidated (dash-prefixed) tag_id should be skipped."""
        record = _make_record(tag_id="-04A3B2C1D5E6F")
        spoolease_mock.get_spools.return_value = [record]

        await engine.full_sync()

        spoolman_mock.create_spool.assert_not_called()


class TestDeltaSync:
    @pytest.mark.asyncio
    async def test_positive_delta_reports_usage(self, engine, spoolease_mock, spoolman_mock, mapping_store):
        """Increased consumption should report delta to Spoolman."""
        mapping = SpoolMapping(
            tag_id="04A3B2C1D5E6F7",
            spoolease_id="1",
            spoolman_spool_id=42,
            spoolman_filament_id=10,
            last_known_consumed=100.0,
            created_at="2025-01-01T00:00:00",
        )
        mapping_store.set_mapping(mapping)

        record = _make_record(consumed=150.0)
        spoolease_mock.get_spools.return_value = [record]
        spoolman_mock.use_spool.return_value = {"id": 42, "used_weight": 150.0}

        await engine.full_sync()

        spoolman_mock.use_spool.assert_called_once_with(42, pytest.approx(50.0, abs=0.1))

        # Mapping should be updated
        updated = mapping_store.get_by_tag_id("04A3B2C1D5E6F7")
        assert updated is not None
        assert abs(updated.last_known_consumed - 150.0) < 0.1

    @pytest.mark.asyncio
    async def test_zero_delta_no_sync(self, engine, spoolease_mock, spoolman_mock, mapping_store):
        """No change in consumption should NOT call use_spool."""
        mapping = SpoolMapping(
            tag_id="04A3B2C1D5E6F7",
            spoolease_id="1",
            spoolman_spool_id=42,
            spoolman_filament_id=10,
            last_known_consumed=100.0,
            created_at="2025-01-01T00:00:00",
        )
        mapping_store.set_mapping(mapping)

        record = _make_record(consumed=100.0)
        spoolease_mock.get_spools.return_value = [record]

        await engine.full_sync()

        spoolman_mock.use_spool.assert_not_called()

    @pytest.mark.asyncio
    async def test_tiny_delta_below_threshold(self, engine, spoolease_mock, spoolman_mock, mapping_store):
        """Delta below threshold (0.1g) should NOT trigger sync."""
        mapping = SpoolMapping(
            tag_id="04A3B2C1D5E6F7",
            spoolease_id="1",
            spoolman_spool_id=42,
            spoolman_filament_id=10,
            last_known_consumed=100.0,
            created_at="2025-01-01T00:00:00",
        )
        mapping_store.set_mapping(mapping)

        record = _make_record(consumed=100.05)
        spoolease_mock.get_spools.return_value = [record]

        await engine.full_sync()

        spoolman_mock.use_spool.assert_not_called()

    @pytest.mark.asyncio
    async def test_negative_delta_resets_baseline(self, engine, spoolease_mock, spoolman_mock, mapping_store):
        """Negative delta (spool reset) should reset baseline without reporting usage."""
        mapping = SpoolMapping(
            tag_id="04A3B2C1D5E6F7",
            spoolease_id="1",
            spoolman_spool_id=42,
            spoolman_filament_id=10,
            last_known_consumed=500.0,
            created_at="2025-01-01T00:00:00",
        )
        mapping_store.set_mapping(mapping)

        record = _make_record(consumed=10.0)  # much lower — reset
        spoolease_mock.get_spools.return_value = [record]

        await engine.full_sync()

        spoolman_mock.use_spool.assert_not_called()

        updated = mapping_store.get_by_tag_id("04A3B2C1D5E6F7")
        assert updated is not None
        assert abs(updated.last_known_consumed - 10.0) < 0.1


class TestSpoolEaseUnreachable:
    @pytest.mark.asyncio
    async def test_skips_sync_when_offline(self, engine, spoolease_mock, spoolman_mock):
        """When SpoolEase is unreachable, sync should be skipped gracefully."""
        spoolease_mock.get_spools.return_value = None

        await engine.full_sync()

        spoolman_mock.use_spool.assert_not_called()
        spoolman_mock.create_spool.assert_not_called()


class TestWebSocketEvents:
    @pytest.mark.asyncio
    async def test_deleted_spool_removes_mapping(self, engine, mapping_store, config):
        """Deleted Spoolman spool should remove its mapping."""
        mapping = SpoolMapping(
            tag_id="04A3B2C1D5E6F7",
            spoolease_id="1",
            spoolman_spool_id=42,
            spoolman_filament_id=10,
            last_known_consumed=100.0,
            created_at="2025-01-01T00:00:00",
        )
        mapping_store.set_mapping(mapping)

        await engine._handle_ws_event("deleted", {"id": 42})

        assert mapping_store.get_by_tag_id("04A3B2C1D5E6F7") is None

    @pytest.mark.asyncio
    async def test_updated_event_no_tag_ignored(self, engine, mapping_store):
        """Updated events for spools without tag should be silently ignored."""
        await engine._handle_ws_event("updated", {"id": 99, "extra": {}})
        # no crash is success

    @pytest.mark.asyncio
    async def test_event_no_id_ignored(self, engine):
        """Events without spool ID should be ignored."""
        await engine._handle_ws_event("updated", {})
        # no crash is success
