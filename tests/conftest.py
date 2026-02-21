"""Shared test fixtures."""

from __future__ import annotations

import pytest

from src.config import BridgeConfig


@pytest.fixture
def config() -> BridgeConfig:
    return BridgeConfig(
        spoolease_host="192.168.1.50",
        spoolease_security_key="TESTKEY",
        spoolease_port=80,
        spoolman_host="localhost",
        spoolman_port=7912,
        poll_interval_seconds=30,
        delta_threshold=0.1,
        mapping_file_path="/tmp/test_mapping.json",
    )
