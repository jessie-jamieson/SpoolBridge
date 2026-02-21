"""SpoolEase-Spoolman Bridge Service entry point.

Orchestrates startup, validation, and concurrent sync tasks.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from .config import load_config
from .logging_config import setup_logging
from .mapping_store import MappingStore
from .spoolease_client import SpoolEaseClient
from .spoolman_client import SpoolmanClient
from .sync_engine import SyncEngine

logger = logging.getLogger(__name__)


async def run() -> None:
    config = load_config()
    setup_logging(config.log_level)

    logger.info("SpoolEase-Spoolman Bridge starting up")
    logger.info("SpoolEase: %s", config.spoolease_base_url)
    logger.info("Spoolman:  %s", config.spoolman_base_url)
    logger.info("Poll interval: %ds", config.poll_interval_seconds)

    spoolease = SpoolEaseClient(config)
    spoolman = SpoolmanClient(config)
    mapping_store = MappingStore(config.mapping_file_path)
    sync_engine = SyncEngine(spoolease, spoolman, mapping_store, config)

    try:
        # 1. Validate SpoolEase encryption key
        logger.info("Validating SpoolEase security key...")
        if not await spoolease.test_key():
            logger.error(
                "SpoolEase security key validation failed. "
                "Check BRIDGE_SPOOLEASE_SECURITY_KEY and ensure the device is reachable."
            )
            sys.exit(1)

        # 2. Ensure Spoolman extra fields exist
        logger.info("Ensuring Spoolman extra fields exist...")
        await spoolman.ensure_extra_fields_exist()

        # 3. Load or rebuild mapping
        mapping_store.load()
        if not mapping_store.state.mappings:
            # Try to rebuild from Spoolman extra fields
            logger.info("No existing mappings — checking Spoolman for recoverable data...")
            try:
                spoolman_spools = await spoolman.get_all_spools()
                recovered = mapping_store.rebuild_from_spoolman_spools(
                    spoolman_spools,
                    config.spoolman_tag_id_field,
                    config.spoolman_spoolease_id_field,
                )
                if recovered:
                    mapping_store.save()
            except Exception as e:
                logger.warning("Could not rebuild mappings from Spoolman: %s", e)

        # 4. Initial sync delay (let services stabilize)
        if config.initial_sync_delay > 0:
            logger.info("Waiting %ds before initial sync...", config.initial_sync_delay)
            await asyncio.sleep(config.initial_sync_delay)

        # 5. Run initial full sync
        logger.info("Running initial full sync...")
        await sync_engine.full_sync()

        # 6. Start concurrent sync tasks
        logger.info("Bridge is running. Starting sync loops.")
        await asyncio.gather(
            sync_engine.periodic_sync_loop(),
            sync_engine.websocket_listener(),
        )
    except RuntimeError as e:
        logger.error("Fatal startup error: %s", e)
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Shutting down (keyboard interrupt)")
    except asyncio.CancelledError:
        logger.info("Shutting down (cancelled)")
    finally:
        await spoolease.close()
        await spoolman.close()
        logger.info("Bridge stopped")


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
