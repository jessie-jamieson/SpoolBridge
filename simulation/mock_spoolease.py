"""Mock SpoolEase server for integration testing.

Simulates SpoolEase's encrypted REST API with in-memory spool data.
Supports adding/removing spools and simulating filament consumption
via a separate admin API (unencrypted, on a different port).

Usage:
    python -m simulation.mock_spoolease

    # Or with custom settings:
    MOCK_SECURITY_KEY=TESTKEY MOCK_PORT=8080 MOCK_ADMIN_PORT=8081 python -m simulation.mock_spoolease

Encrypted API (port 8080) — mimics real SpoolEase:
    GET  /api/spools              → encrypted CSV of all spools
    GET  /api/spools-in-printers  → encrypted JSON of printer slots
    POST /api/test-key            → validates encryption key

Admin API (port 8081) — for test control:
    GET  /admin/spools            → JSON list of all spools (plaintext)
    POST /admin/spools            → add a new spool (JSON body)
    POST /admin/consume           → simulate filament consumption
    POST /admin/reset             → clear all spools
    GET  /admin/health            → health check
"""

from __future__ import annotations

import base64
import json
import os
import struct
import sys
import time
from dataclasses import dataclass, field

from aiohttp import web

# Add project root to path so we can import the encryption module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.encryption import decrypt, derive_key, encrypt


# ── Spool data store ────────────────────────────────────────────────

@dataclass
class MockSpool:
    id: str
    tag_id: str
    material_type: str = "PLA"
    material_subtype: str = ""
    color_name: str = "Black"
    color_code: str = "000000FF"
    note: str = ""
    brand: str = "Bambu"
    weight_advertised: int | None = 1000
    weight_core: int | None = 200
    weight_new: int | None = None
    weight_current: int | None = None
    slicer_filament: str = ""
    added_time: int | None = None
    encode_time: int | None = None
    added_full: bool | None = True
    consumed_since_add: float = 0.0
    consumed_since_weight: float = 0.0
    ext_has_k: bool = False
    data_origin: str = ""
    tag_type: str = "SpoolEaseV1"


def _encode_f32_base64(value: float) -> str:
    if value == 0.0:
        return ""
    raw = struct.pack("<f", value)
    return base64.b64encode(raw).rstrip(b"=").decode("ascii")


def _bool_yn(value: bool | None) -> str:
    if value is None:
        return ""
    return "y" if value else "n"


def _opt_int(value: int | None) -> str:
    return "" if value is None else str(value)


def spool_to_csv_row(s: MockSpool) -> str:
    fields = [
        s.id, s.tag_id, s.material_type, s.material_subtype,
        s.color_name, s.color_code, s.note, s.brand,
        _opt_int(s.weight_advertised), _opt_int(s.weight_core),
        _opt_int(s.weight_new), _opt_int(s.weight_current),
        s.slicer_filament, _opt_int(s.added_time), _opt_int(s.encode_time),
        _bool_yn(s.added_full),
        _encode_f32_base64(s.consumed_since_add),
        _encode_f32_base64(s.consumed_since_weight),
        "y" if s.ext_has_k else "n",
        s.data_origin, s.tag_type,
    ]
    return ",".join(fields)


class SpoolStore:
    def __init__(self) -> None:
        self.spools: dict[str, MockSpool] = {}
        self._next_id = 1

    def add_spool(self, **kwargs) -> MockSpool:
        spool_id = str(self._next_id)
        self._next_id += 1
        kwargs.setdefault("added_time", int(time.time()))
        spool = MockSpool(id=spool_id, **kwargs)
        self.spools[spool_id] = spool
        return spool

    def consume(self, spool_id: str, grams: float) -> MockSpool | None:
        spool = self.spools.get(spool_id)
        if spool is None:
            return None
        spool.consumed_since_add += grams
        spool.consumed_since_weight += grams
        return spool

    def to_csv(self) -> str:
        rows = [spool_to_csv_row(s) for s in self.spools.values()]
        return "\n".join(rows)

    def reset(self) -> None:
        self.spools.clear()
        self._next_id = 1


# ── Global state ────────────────────────────────────────────────────

SECURITY_KEY = os.environ.get("MOCK_SECURITY_KEY", "TESTKEY")
SALT = os.environ.get("MOCK_SALT", "example_salt")
ITERATIONS = int(os.environ.get("MOCK_ITERATIONS", "10000"))

encryption_key = derive_key(SECURITY_KEY, SALT, ITERATIONS)
store = SpoolStore()


# ── Encrypted API routes (mimics real SpoolEase) ────────────────────

async def handle_test_key(request: web.Request) -> web.Response:
    """POST /api/test-key — validate encryption key."""
    body = await request.text()
    try:
        decrypted = decrypt(encryption_key, body)
        return web.Response(text=encrypt(encryption_key, '{"status":"ok"}'))
    except Exception:
        return web.Response(status=400, text="Invalid key")


async def handle_get_spools(request: web.Request) -> web.Response:
    """GET /api/spools — return encrypted CSV of all spools."""
    csv_data = store.to_csv()
    encrypted = encrypt(encryption_key, csv_data)
    return web.Response(text=encrypted)


async def handle_get_spools_in_printers(request: web.Request) -> web.Response:
    """GET /api/spools-in-printers — return encrypted JSON."""
    # Simulate one printer with first spool loaded
    slots = {}
    for spool in store.spools.values():
        slots[f"printer1:tray1"] = spool.id
        break
    data = json.dumps({"spools": slots})
    encrypted = encrypt(encryption_key, data)
    return web.Response(text=encrypted)


# ── Admin API routes (plaintext, for test control) ──────────────────

async def admin_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "spool_count": len(store.spools)})


async def admin_get_spools(request: web.Request) -> web.Response:
    """GET /admin/spools — list all spools as JSON."""
    spools = []
    for s in store.spools.values():
        spools.append({
            "id": s.id,
            "tag_id": s.tag_id,
            "material_type": s.material_type,
            "color_name": s.color_name,
            "color_code": s.color_code,
            "brand": s.brand,
            "weight_advertised": s.weight_advertised,
            "weight_core": s.weight_core,
            "consumed_since_add": round(s.consumed_since_add, 2),
            "consumed_since_weight": round(s.consumed_since_weight, 2),
            "tag_type": s.tag_type,
        })
    return web.json_response(spools)


async def admin_add_spool(request: web.Request) -> web.Response:
    """POST /admin/spools — add a new spool.

    JSON body:
        tag_id (required), material_type, color_name, color_code, brand,
        weight_advertised, weight_core, tag_type
    """
    data = await request.json()
    tag_id = data.get("tag_id")
    if not tag_id:
        return web.json_response({"error": "tag_id is required"}, status=400)

    spool = store.add_spool(
        tag_id=tag_id,
        material_type=data.get("material_type", "PLA"),
        material_subtype=data.get("material_subtype", ""),
        color_name=data.get("color_name", "Black"),
        color_code=data.get("color_code", "000000FF"),
        brand=data.get("brand", "Bambu"),
        weight_advertised=data.get("weight_advertised", 1000),
        weight_core=data.get("weight_core", 200),
        note=data.get("note", ""),
        tag_type=data.get("tag_type", "SpoolEaseV1"),
    )
    print(f"[Mock SpoolEase] Added spool {spool.id}: {spool.brand} {spool.material_type} {spool.color_name} (tag={spool.tag_id})")
    return web.json_response({"id": spool.id, "tag_id": spool.tag_id}, status=201)


async def admin_consume(request: web.Request) -> web.Response:
    """POST /admin/consume — simulate filament consumption.

    JSON body: { "spool_id": "1", "grams": 25.5 }
    """
    data = await request.json()
    spool_id = data.get("spool_id")
    grams = data.get("grams", 0)

    if not spool_id:
        return web.json_response({"error": "spool_id is required"}, status=400)
    if grams <= 0:
        return web.json_response({"error": "grams must be positive"}, status=400)

    spool = store.consume(spool_id, grams)
    if spool is None:
        return web.json_response({"error": f"spool {spool_id} not found"}, status=404)

    print(f"[Mock SpoolEase] Consumed {grams:.1f}g on spool {spool_id} (total: {spool.consumed_since_add:.1f}g)")
    return web.json_response({
        "id": spool.id,
        "consumed_since_add": round(spool.consumed_since_add, 2),
    })


async def admin_reset(request: web.Request) -> web.Response:
    """POST /admin/reset — clear all spools."""
    store.reset()
    print("[Mock SpoolEase] All spools cleared")
    return web.json_response({"status": "reset"})


# ── Server setup ────────────────────────────────────────────────────

def create_encrypted_app() -> web.Application:
    """Create the encrypted API app (mimics real SpoolEase)."""
    app = web.Application()
    app.router.add_post("/api/test-key", handle_test_key)
    app.router.add_get("/api/spools", handle_get_spools)
    app.router.add_get("/api/spools-in-printers", handle_get_spools_in_printers)
    return app


def create_admin_app() -> web.Application:
    """Create the admin API app (for test control)."""
    app = web.Application()
    app.router.add_get("/admin/health", admin_health)
    app.router.add_get("/admin/spools", admin_get_spools)
    app.router.add_post("/admin/spools", admin_add_spool)
    app.router.add_post("/admin/consume", admin_consume)
    app.router.add_post("/admin/reset", admin_reset)
    return app


async def start_servers() -> None:
    """Start both servers concurrently."""
    import asyncio

    encrypted_port = int(os.environ.get("MOCK_PORT", "8080"))
    admin_port = int(os.environ.get("MOCK_ADMIN_PORT", "8081"))

    encrypted_app = create_encrypted_app()
    admin_app = create_admin_app()

    encrypted_runner = web.AppRunner(encrypted_app)
    admin_runner = web.AppRunner(admin_app)

    await encrypted_runner.setup()
    await admin_runner.setup()

    encrypted_site = web.TCPSite(encrypted_runner, "0.0.0.0", encrypted_port)
    admin_site = web.TCPSite(admin_runner, "0.0.0.0", admin_port)

    await encrypted_site.start()
    await admin_site.start()

    print(f"[Mock SpoolEase] Encrypted API running on port {encrypted_port}")
    print(f"[Mock SpoolEase] Admin API running on port {admin_port}")
    print(f"[Mock SpoolEase] Security key: {SECURITY_KEY}")
    print()
    print("Admin endpoints:")
    print(f"  GET  http://localhost:{admin_port}/admin/health")
    print(f"  GET  http://localhost:{admin_port}/admin/spools")
    print(f"  POST http://localhost:{admin_port}/admin/spools       (add spool)")
    print(f"  POST http://localhost:{admin_port}/admin/consume      (simulate usage)")
    print(f"  POST http://localhost:{admin_port}/admin/reset        (clear all)")
    print()

    # Keep running
    await asyncio.Event().wait()


if __name__ == "__main__":
    import asyncio
    asyncio.run(start_servers())
