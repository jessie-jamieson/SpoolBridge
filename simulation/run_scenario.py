"""Interactive simulation script that drives the mock SpoolEase + real Spoolman + bridge.

Walks through a realistic scenario:
1. Adds spools to mock SpoolEase (simulates scanning NFC tags)
2. Waits for bridge to sync them to Spoolman
3. Simulates Bambu printer usage (consuming filament)
4. Simulates Klipper usage (directly via Spoolman API)
5. Shows the unified view in Spoolman

Usage:
    # Start the mock SpoolEase and Spoolman first, then:
    python -m simulation.run_scenario

Environment:
    MOCK_ADMIN_URL   — default: http://localhost:8081
    SPOOLMAN_URL     — default: http://localhost:7912
    BRIDGE_POLL      — seconds to wait for bridge sync (default: 35)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import aiohttp

MOCK_ADMIN_URL = os.environ.get("MOCK_ADMIN_URL", "http://localhost:8081")
SPOOLMAN_URL = os.environ.get("SPOOLMAN_URL", "http://localhost:7912")
BRIDGE_POLL = int(os.environ.get("BRIDGE_POLL", "35"))


def header(text: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {text}")
    print(f"{'=' * 60}\n")


def step(text: str) -> None:
    print(f"  >> {text}")


async def wait_for_sync(seconds: int | None = None) -> None:
    wait = seconds or BRIDGE_POLL
    print(f"\n  ... waiting {wait}s for bridge to sync ...\n")
    await asyncio.sleep(wait)


async def check_services(session: aiohttp.ClientSession) -> bool:
    """Verify mock SpoolEase and Spoolman are reachable."""
    try:
        async with session.get(f"{MOCK_ADMIN_URL}/admin/health") as resp:
            if resp.status != 200:
                print(f"ERROR: Mock SpoolEase admin not responding (HTTP {resp.status})")
                return False
            print(f"  Mock SpoolEase: OK")
    except aiohttp.ClientError as e:
        print(f"ERROR: Mock SpoolEase not reachable at {MOCK_ADMIN_URL}: {e}")
        print(f"  Start it with: python -m simulation.mock_spoolease")
        return False

    try:
        async with session.get(f"{SPOOLMAN_URL}/api/v1/health") as resp:
            if resp.status != 200:
                print(f"ERROR: Spoolman not responding (HTTP {resp.status})")
                return False
            print(f"  Spoolman: OK")
    except aiohttp.ClientError as e:
        print(f"ERROR: Spoolman not reachable at {SPOOLMAN_URL}: {e}")
        print(f"  Start it with: docker run -p 7912:8000 ghcr.io/donkie/spoolman:latest")
        return False

    return True


async def show_spoolman_spools(session: aiohttp.ClientSession) -> list[dict]:
    """Fetch and display all Spoolman spools."""
    async with session.get(f"{SPOOLMAN_URL}/api/v1/spool", params={"allow_archived": "true"}) as resp:
        spools = await resp.json()

    if not spools:
        print("  (no spools in Spoolman)")
        return spools

    for s in spools:
        filament = s.get("filament", {})
        vendor = filament.get("vendor", {})
        extra = s.get("extra", {})
        tag_id = extra.get("spoolease_tag_id", "—")
        remaining = s.get("remaining_weight")
        remaining_str = f"{remaining:.1f}g" if remaining is not None else "unknown"
        print(
            f"  Spool #{s['id']}: {vendor.get('name', '?')} {filament.get('material', '?')} "
            f"{filament.get('name', '?')} | "
            f"used={s.get('used_weight', 0):.1f}g, remaining={remaining_str} | "
            f"tag={tag_id}"
        )
    return spools


async def run_scenario() -> None:
    async with aiohttp.ClientSession() as session:
        header("SpoolEase-Spoolman Bridge Simulation")
        step("Checking services...")

        if not await check_services(session):
            print("\nPlease start all services before running the simulation.")
            print("See the instructions above for how to start each service.")
            return

        # ── Step 1: Reset mock SpoolEase ──────────────────────────
        header("Step 1: Reset mock SpoolEase")
        await session.post(f"{MOCK_ADMIN_URL}/admin/reset")
        step("Cleared all mock spools")

        # ── Step 2: Add spools (simulate NFC tag scans) ──────────
        header("Step 2: Add spools to SpoolEase (simulating NFC scans)")

        spools_to_add = [
            {
                "tag_id": "04A1B2C3D4E5F6",
                "material_type": "PLA",
                "color_name": "Charcoal Black",
                "color_code": "333333FF",
                "brand": "Bambu",
                "weight_advertised": 1000,
                "weight_core": 230,
            },
            {
                "tag_id": "04AABBCCDDEE01",
                "material_type": "PETG",
                "color_name": "Fire Red",
                "color_code": "FF2200FF",
                "brand": "Polymaker",
                "weight_advertised": 1000,
                "weight_core": 195,
            },
            {
                "tag_id": "0411223344556A",
                "material_type": "TPU",
                "color_name": "White",
                "color_code": "FFFFFFFF",
                "brand": "eSUN",
                "weight_advertised": 500,
                "weight_core": 180,
            },
        ]

        mock_spool_ids = []
        for spool_data in spools_to_add:
            async with session.post(f"{MOCK_ADMIN_URL}/admin/spools", json=spool_data) as resp:
                result = await resp.json()
                mock_spool_ids.append(result["id"])
                step(f"Added: {spool_data['brand']} {spool_data['material_type']} {spool_data['color_name']} (id={result['id']})")

        step("Checking mock SpoolEase inventory:")
        async with session.get(f"{MOCK_ADMIN_URL}/admin/spools") as resp:
            mock_spools = await resp.json()
            for s in mock_spools:
                step(f"  {s['id']}: {s['brand']} {s['material_type']} {s['color_name']} — consumed: {s['consumed_since_add']}g")

        # ── Step 3: Wait for bridge to sync ──────────────────────
        header("Step 3: Waiting for bridge to sync spools to Spoolman")
        await wait_for_sync()

        step("Spoolman inventory after sync:")
        spoolman_spools = await show_spoolman_spools(session)

        if not spoolman_spools:
            print("\n  WARNING: No spools appeared in Spoolman!")
            print("  Is the bridge running? Check: docker logs spoolease-bridge")
            return

        # ── Step 4: Simulate Bambu usage ─────────────────────────
        header("Step 4: Simulate Bambu printer usage")

        step("Printing a benchy with the Bambu PLA Black (25.3g consumed)...")
        await session.post(f"{MOCK_ADMIN_URL}/admin/consume", json={"spool_id": mock_spool_ids[0], "grams": 25.3})

        step("Printing a case with the Polymaker PETG Red (87.1g consumed)...")
        await session.post(f"{MOCK_ADMIN_URL}/admin/consume", json={"spool_id": mock_spool_ids[1], "grams": 87.1})

        step("Waiting for bridge to sync consumption to Spoolman...")
        await wait_for_sync()

        step("Spoolman after Bambu usage:")
        await show_spoolman_spools(session)

        # ── Step 5: Simulate Klipper usage ───────────────────────
        header("Step 5: Simulate Klipper printer usage (directly via Spoolman)")

        # Find the PETG spool in Spoolman
        async with session.get(f"{SPOOLMAN_URL}/api/v1/spool") as resp:
            current_spools = await resp.json()

        petg_spool = None
        for s in current_spools:
            extra = s.get("extra", {})
            if extra.get("spoolease_tag_id") == "04AABBCCDDEE01":
                petg_spool = s
                break

        if petg_spool:
            step(f"Using PETG Red spool (Spoolman #{petg_spool['id']}) on Klipper printer — 42.0g consumed...")
            await session.put(
                f"{SPOOLMAN_URL}/api/v1/spool/{petg_spool['id']}/use",
                json={"use_weight": 42.0},
            )
        else:
            step("WARNING: Could not find PETG spool in Spoolman")

        # ── Step 6: Show unified view ────────────────────────────
        header("Step 6: Unified view — Spoolman as source of truth")

        step("Final Spoolman inventory (Bambu + Klipper usage combined):")
        await show_spoolman_spools(session)

        if petg_spool:
            print()
            step("Notice the PETG Red spool:")
            step(f"  - 87.1g used on Bambu (via bridge sync)")
            step(f"  - 42.0g used on Klipper (directly via Spoolman)")
            step(f"  - Total: 129.1g combined in Spoolman's unified view")
            print()
            step("Meanwhile, SpoolEase's display only shows 87.1g used")
            step("(it doesn't know about the Klipper usage)")

        # ── Step 7: More Bambu usage ─────────────────────────────
        header("Step 7: Another Bambu print (verify ongoing sync)")

        step("Printing another part with PLA Black (15.7g consumed)...")
        await session.post(f"{MOCK_ADMIN_URL}/admin/consume", json={"spool_id": mock_spool_ids[0], "grams": 15.7})

        await wait_for_sync()

        step("Spoolman after additional Bambu print:")
        await show_spoolman_spools(session)

        header("Simulation complete!")
        print("  The bridge successfully synchronized:")
        print("  - Spool inventory (SpoolEase -> Spoolman)")
        print("  - Bambu filament consumption (SpoolEase -> Spoolman)")
        print("  - Klipper usage tracked directly in Spoolman")
        print("  - Spoolman shows the unified view of all usage")
        print()


if __name__ == "__main__":
    asyncio.run(run_scenario())
