"""Encrypted REST API client for SpoolEase (ESP32-S3 device)."""

from __future__ import annotations

import json
import logging

import aiohttp

from .config import BridgeConfig
from .csv_parser import parse_spools_csv
from .encryption import decrypt, derive_key, encrypt
from .models import SpoolEaseRecord

logger = logging.getLogger(__name__)


class SpoolEaseClient:
    """Communicates with SpoolEase's encrypted REST API.

    All requests use Content-Type: application/text with AES-256-GCM encrypted bodies.
    All responses are encrypted text that must be decrypted before parsing.
    """

    def __init__(self, config: BridgeConfig) -> None:
        self._base_url = config.spoolease_base_url
        self._key = derive_key(
            config.spoolease_security_key,
            config.spoolease_salt,
            config.spoolease_iterations,
        )
        self._timeout = aiohttp.ClientTimeout(total=10)  # ESP32 can be slow
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    def _encrypt(self, data: str) -> str:
        return encrypt(self._key, data)

    def _decrypt(self, data: str) -> str:
        return decrypt(self._key, data)

    async def test_key(self) -> bool:
        """Validate that our encryption key is correct.

        Sends an encrypted test payload to /api/test-key.
        Returns True if the key is valid (HTTP 200), False otherwise.
        """
        session = await self._get_session()
        url = f"{self._base_url}/api/test-key"
        body = self._encrypt(json.dumps({"test": "Hello"}))
        try:
            async with session.post(
                url,
                data=body,
                headers={"Content-Type": "application/text"},
            ) as resp:
                if resp.status == 200:
                    logger.info("SpoolEase key validation successful")
                    return True
                logger.error("SpoolEase key validation failed (HTTP %d)", resp.status)
                return False
        except (aiohttp.ClientError, OSError) as e:
            logger.error("SpoolEase unreachable during key test: %s", e)
            return False

    async def get_spools(self) -> list[SpoolEaseRecord] | None:
        """Fetch all spools from SpoolEase.

        Returns a list of SpoolEaseRecord, or None if the device is unreachable.
        """
        session = await self._get_session()
        url = f"{self._base_url}/api/spools"
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    logger.warning("SpoolEase GET /api/spools returned HTTP %d", resp.status)
                    return None
                encrypted_text = await resp.text()
                csv_text = self._decrypt(encrypted_text)
                records = parse_spools_csv(csv_text)
                logger.debug("Fetched %d spools from SpoolEase", len(records))
                return records
        except (aiohttp.ClientError, OSError) as e:
            logger.warning("SpoolEase unreachable: %s", e)
            return None
        except Exception as e:
            logger.error("Failed to parse SpoolEase spools: %s", e)
            return None

    async def get_spools_in_printers(self) -> dict[str, str] | None:
        """Fetch which spools are currently loaded in printer slots.

        Returns a dict mapping slot identifiers to spool IDs, or None if unreachable.
        """
        session = await self._get_session()
        url = f"{self._base_url}/api/spools-in-printers"
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    logger.warning("SpoolEase GET /api/spools-in-printers returned HTTP %d", resp.status)
                    return None
                encrypted_text = await resp.text()
                json_text = self._decrypt(encrypted_text)
                data = json.loads(json_text)
                return data.get("spools", {})
        except (aiohttp.ClientError, OSError) as e:
            logger.warning("SpoolEase unreachable: %s", e)
            return None
        except Exception as e:
            logger.error("Failed to parse SpoolEase printer slots: %s", e)
            return None
