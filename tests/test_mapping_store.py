"""Tests for the mapping store (JSON file persistence)."""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from src.mapping_store import MappingStore
from src.models import SpoolMapping


@pytest.fixture
def tmp_file():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.unlink(path)  # start with no file
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def sample_mapping() -> SpoolMapping:
    return SpoolMapping(
        tag_id="04A3B2C1D5E6F7",
        spoolease_id="1",
        spoolman_spool_id=42,
        spoolman_filament_id=10,
        last_known_consumed=123.45,
        created_at="2025-01-01T00:00:00+00:00",
    )


class TestMappingStore:
    def test_fresh_start(self, tmp_file):
        store = MappingStore(tmp_file)
        store.load()
        assert store.state.mappings == {}

    def test_save_and_load(self, tmp_file, sample_mapping):
        store = MappingStore(tmp_file)
        store.set_mapping(sample_mapping)
        store.save()

        # Reload from disk
        store2 = MappingStore(tmp_file)
        store2.load()
        loaded = store2.get_by_tag_id("04A3B2C1D5E6F7")
        assert loaded is not None
        assert loaded.tag_id == "04A3B2C1D5E6F7"
        assert loaded.spoolease_id == "1"
        assert loaded.spoolman_spool_id == 42
        assert loaded.spoolman_filament_id == 10
        assert loaded.last_known_consumed == 123.45

    def test_get_by_tag_id(self, tmp_file, sample_mapping):
        store = MappingStore(tmp_file)
        store.set_mapping(sample_mapping)
        assert store.get_by_tag_id("04A3B2C1D5E6F7") is not None
        assert store.get_by_tag_id("NONEXISTENT") is None

    def test_get_by_spoolman_id(self, tmp_file, sample_mapping):
        store = MappingStore(tmp_file)
        store.set_mapping(sample_mapping)
        assert store.get_by_spoolman_id(42) is not None
        assert store.get_by_spoolman_id(999) is None

    def test_remove_by_tag_id(self, tmp_file, sample_mapping):
        store = MappingStore(tmp_file)
        store.set_mapping(sample_mapping)
        store.remove_by_tag_id("04A3B2C1D5E6F7")
        assert store.get_by_tag_id("04A3B2C1D5E6F7") is None

    def test_remove_by_spoolman_id(self, tmp_file, sample_mapping):
        store = MappingStore(tmp_file)
        store.set_mapping(sample_mapping)
        store.remove_by_spoolman_id(42)
        assert store.get_by_tag_id("04A3B2C1D5E6F7") is None

    def test_remove_nonexistent(self, tmp_file):
        store = MappingStore(tmp_file)
        store.remove_by_tag_id("NOPE")  # should not raise
        store.remove_by_spoolman_id(999)  # should not raise

    def test_last_sync_time_set_on_save(self, tmp_file, sample_mapping):
        store = MappingStore(tmp_file)
        store.set_mapping(sample_mapping)
        store.save()

        store2 = MappingStore(tmp_file)
        store2.load()
        assert store2.state.last_sync_time is not None

    def test_corrupt_file(self, tmp_file):
        """Corrupt JSON should be handled gracefully."""
        with open(tmp_file, "w") as f:
            f.write("{invalid json")
        store = MappingStore(tmp_file)
        store.load()  # should not raise
        assert store.state.mappings == {}

    def test_multiple_mappings(self, tmp_file):
        store = MappingStore(tmp_file)
        for i in range(5):
            m = SpoolMapping(
                tag_id=f"TAG{i:012d}",
                spoolease_id=str(i),
                spoolman_spool_id=100 + i,
                spoolman_filament_id=10 + i,
                last_known_consumed=float(i * 10),
                created_at="2025-01-01T00:00:00+00:00",
            )
            store.set_mapping(m)
        store.save()

        store2 = MappingStore(tmp_file)
        store2.load()
        assert len(store2.state.mappings) == 5

    def test_rebuild_from_spoolman_spools(self, tmp_file):
        """Spoolman returns extra field values as JSON-encoded strings."""
        store = MappingStore(tmp_file)
        spoolman_spools = [
            {
                "id": 1,
                "used_weight": 50.0,
                "filament": {"id": 10},
                "extra": {
                    # Spoolman stores values as JSON: '"AAAA..."' not 'AAAA...'
                    "spoolease_tag_id": '"AAAABBBBCCCCDD"',
                    "spoolease_id": '"5"',
                },
            },
            {
                "id": 2,
                "used_weight": 100.0,
                "filament": {"id": 20},
                "extra": {
                    "spoolease_tag_id": '"11223344556677"',
                    "spoolease_id": '"8"',
                },
            },
            {
                "id": 3,
                "used_weight": 0.0,
                "filament": {"id": 30},
                "extra": {},  # no tag — should be skipped
            },
        ]
        recovered = store.rebuild_from_spoolman_spools(
            spoolman_spools, "spoolease_tag_id", "spoolease_id",
        )
        assert recovered == 2
        m1 = store.get_by_tag_id("AAAABBBBCCCCDD")
        assert m1 is not None
        assert m1.spoolman_spool_id == 1
        assert m1.spoolease_id == "5"
        m2 = store.get_by_tag_id("11223344556677")
        assert m2 is not None
        assert m2.spoolman_spool_id == 2

    def test_rebuild_handles_plain_strings(self, tmp_file):
        """Also handle plain (non-JSON-encoded) strings gracefully."""
        store = MappingStore(tmp_file)
        spoolman_spools = [
            {
                "id": 1,
                "used_weight": 50.0,
                "filament": {"id": 10},
                "extra": {
                    "spoolease_tag_id": "AAAABBBBCCCCDD",
                    "spoolease_id": "5",
                },
            },
        ]
        recovered = store.rebuild_from_spoolman_spools(
            spoolman_spools, "spoolease_tag_id", "spoolease_id",
        )
        assert recovered == 1
        m1 = store.get_by_tag_id("AAAABBBBCCCCDD")
        assert m1 is not None
        assert m1.spoolease_id == "5"
