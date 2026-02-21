"""Bridge configuration loaded from environment variables."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass


@dataclass
class BridgeConfig:
    # SpoolEase connection (required)
    spoolease_host: str
    spoolease_security_key: str
    spoolease_port: int = 80
    spoolease_use_https: bool = False

    # SpoolEase encryption parameters (must match SpoolEase settings.rs)
    spoolease_salt: str = "example_salt"
    spoolease_iterations: int = 10_000

    # Spoolman connection
    spoolman_host: str = "spoolman"
    spoolman_port: int = 8000

    # Sync behavior
    poll_interval_seconds: int = 30
    initial_sync_delay: int = 5
    delta_threshold: float = 0.1  # minimum grams before syncing

    # Mapping persistence
    mapping_file_path: str = "/data/mapping.json"

    # Logging
    log_level: str = "INFO"

    # Spoolman extra field names
    spoolman_tag_id_field: str = "spoolease_tag_id"
    spoolman_spoolease_id_field: str = "spoolease_id"

    @property
    def spoolease_base_url(self) -> str:
        scheme = "https" if self.spoolease_use_https else "http"
        return f"{scheme}://{self.spoolease_host}:{self.spoolease_port}"

    @property
    def spoolman_base_url(self) -> str:
        return f"http://{self.spoolman_host}:{self.spoolman_port}"

    @property
    def spoolman_ws_url(self) -> str:
        return f"ws://{self.spoolman_host}:{self.spoolman_port}"


def _env(key: str, default: str | None = None) -> str:
    val = os.environ.get(key)
    if val is not None:
        return val
    if default is not None:
        return default
    print(f"Error: required environment variable {key} is not set.", file=sys.stderr)
    sys.exit(1)


def _env_bool(key: str, default: bool) -> bool:
    val = os.environ.get(key)
    if val is None:
        return default
    return val.lower() in ("true", "1", "yes")


def _env_int(key: str, default: int) -> int:
    val = os.environ.get(key)
    if val is None:
        return default
    return int(val)


def _env_float(key: str, default: float) -> float:
    val = os.environ.get(key)
    if val is None:
        return default
    return float(val)


def load_config() -> BridgeConfig:
    return BridgeConfig(
        spoolease_host=_env("BRIDGE_SPOOLEASE_HOST"),
        spoolease_security_key=_env("BRIDGE_SPOOLEASE_SECURITY_KEY"),
        spoolease_port=_env_int("BRIDGE_SPOOLEASE_PORT", 80),
        spoolease_use_https=_env_bool("BRIDGE_SPOOLEASE_USE_HTTPS", False),
        spoolease_salt=_env("BRIDGE_SPOOLEASE_SALT", "example_salt"),
        spoolease_iterations=_env_int("BRIDGE_SPOOLEASE_ITERATIONS", 10_000),
        spoolman_host=_env("BRIDGE_SPOOLMAN_HOST", "spoolman"),
        spoolman_port=_env_int("BRIDGE_SPOOLMAN_PORT", 8000),
        poll_interval_seconds=_env_int("BRIDGE_POLL_INTERVAL_SECONDS", 30),
        initial_sync_delay=_env_int("BRIDGE_INITIAL_SYNC_DELAY", 5),
        delta_threshold=_env_float("BRIDGE_DELTA_THRESHOLD", 0.1),
        mapping_file_path=_env("BRIDGE_MAPPING_FILE_PATH", "/data/mapping.json"),
        log_level=_env("BRIDGE_LOG_LEVEL", "INFO"),
        spoolman_tag_id_field=_env("BRIDGE_SPOOLMAN_TAG_ID_FIELD", "spoolease_tag_id"),
        spoolman_spoolease_id_field=_env("BRIDGE_SPOOLMAN_SPOOLEASE_ID_FIELD", "spoolease_id"),
    )
