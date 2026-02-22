# SpoolEase-Spoolman Bridge

A bidirectional sync service that bridges filament tracking between **SpoolEase** (Bambu Lab's ESP32-S3 NFC spool tracker) and **Spoolman** (the Klipper ecosystem's filament management system).

If you use both Bambu Lab and Klipper printers, this bridge gives you a single, unified view of all your filament inventory and consumption — no matter which printer used the spool.

## How It Works

```
┌──────────────┐     poll every 30s      ┌─────────────────────┐     REST API      ┌──────────────┐
│              │ ◄────────────────────────│                     │ ──────────────────►│              │
│   SpoolEase  │   encrypted REST API    │   SpoolEase-Spoolman│                    │   Spoolman   │
│   (ESP32)    │                         │       Bridge        │ ◄──────────────────│   (Klipper)  │
│              │                         │                     │   WebSocket events │              │
└──────────────┘                         └─────────────────────┘                    └──────────────┘
       │                                          │                                        │
  NFC tag scans                          mapping.json (persist)                   Filament database
  Bambu printer usage                    consumption deltas                       Spool CRUD + usage
```

The bridge runs two concurrent loops:

1. **Polling loop** (SpoolEase → Spoolman): Fetches spools from SpoolEase every 30 seconds, detects new spools, calculates consumption deltas, and pushes updates to Spoolman.
2. **WebSocket listener** (Spoolman → Bridge): Listens for real-time events from Spoolman (e.g. spools deleted by the user) and keeps internal mappings in sync.

## Features

- **Automatic spool detection** — New spools scanned by SpoolEase are automatically created in Spoolman with the correct vendor, filament type, and color.
- **Consumption tracking** — Filament usage from Bambu printers is synced to Spoolman in near real-time (configurable polling interval).
- **Metadata sync** — Brand, material, color, weight, and core weight are kept in sync between systems.
- **Mapping persistence** — Spool mappings are stored locally and can be automatically recovered from Spoolman if the mapping file is lost.
- **Encrypted communication** — All SpoolEase API communication uses AES-256-GCM encryption (matching the device's esp-hal-app-framework protocol).
- **Graceful error handling** — Per-spool error isolation, exponential backoff on disconnections, and atomic file writes to prevent data corruption.

## Prerequisites

- A **SpoolEase** device on your local network ([spoolease.com](https://spoolease.com))
- A **Spoolman** instance (v0.17+) — included in the Docker Compose setup, or bring your own
- **Docker** and **Docker Compose** (recommended), or **Python 3.11+** for running natively

## Quick Start (Docker Compose)

This is the recommended way to run the bridge. It starts both Spoolman and the bridge together.

### 1. Clone the repository

```bash
git clone https://github.com/your-username/spoolease-spoolman-bridge.git
cd spoolease-spoolman-bridge
```

### 2. Configure your environment

Edit `docker-compose.yaml` and set the two required values:

```yaml
environment:
  - BRIDGE_SPOOLEASE_HOST=192.168.1.50        # Your SpoolEase device IP
  - BRIDGE_SPOOLEASE_SECURITY_KEY=ABCDEFG     # Your 7-char security key
```

> **Finding your security key:** The 7-character alphanumeric key is displayed on the SpoolEase device screen. It's used to encrypt all communication between the bridge and the device.

### 3. Start the services

```bash
docker compose up -d
```

That's it. The bridge will:
1. Validate the SpoolEase encryption key
2. Set up custom tracking fields in Spoolman
3. Recover any existing spool mappings
4. Run an initial full sync
5. Begin continuous polling and WebSocket monitoring

### 4. Access Spoolman

Open **http://localhost:7912** in your browser to see your spools.

## Configuration Reference

All settings are configured via environment variables prefixed with `BRIDGE_`.

### Required

| Variable | Description |
|----------|-------------|
| `BRIDGE_SPOOLEASE_HOST` | SpoolEase device IP address or hostname |
| `BRIDGE_SPOOLEASE_SECURITY_KEY` | 7-character security key from SpoolEase display |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `BRIDGE_SPOOLEASE_PORT` | `80` | SpoolEase HTTP port |
| `BRIDGE_SPOOLEASE_USE_HTTPS` | `false` | Use HTTPS for SpoolEase connection |
| `BRIDGE_SPOOLMAN_HOST` | `spoolman` | Spoolman hostname (Docker service name by default) |
| `BRIDGE_SPOOLMAN_PORT` | `8000` | Spoolman port (internal container port) |
| `BRIDGE_POLL_INTERVAL_SECONDS` | `30` | How often to poll SpoolEase for changes (seconds) |
| `BRIDGE_DELTA_THRESHOLD` | `0.1` | Minimum filament change in grams before syncing to Spoolman |
| `BRIDGE_MAPPING_FILE_PATH` | `/data/mapping.json` | Path to the persistent spool mapping file |
| `BRIDGE_LOG_LEVEL` | `INFO` | Log verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `BRIDGE_INITIAL_SYNC_DELAY` | `5` | Seconds to wait before first sync (lets services stabilize) |

A `.env.example` file is included for reference when running outside of Docker.

## Running Without Docker

If you prefer not to use Docker, you can run both Spoolman and the bridge natively on your machine.

### 1. Install and Run Spoolman

The bridge requires a running Spoolman instance. If you don't already have one, follow the [Spoolman installation guide](https://github.com/Donkie/Spoolman#installation) to set it up. The most common non-Docker approach is:

```bash
# Install Spoolman (requires Python 3.9+ and pip)
pip install spoolman

# Run it (default port 8000)
spoolman
```

Alternatively, you can install Spoolman from source — see their repo for details. Once running, verify it's accessible:

```bash
curl http://localhost:8000/api/v1/health
```

### 2. Install the Bridge

Requires **Python 3.11+**.

```bash
git clone https://github.com/your-username/spoolease-spoolman-bridge.git
cd spoolease-spoolman-bridge

# Create and activate a virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate   # On Windows: venv\Scripts\activate

# Install the bridge and its dependencies
pip install -e .

# Or include dev/test dependencies too
pip install -e ".[dev]"
```

### 3. Configure

```bash
cp .env.example .env
```

Edit `.env` with your values:

```bash
# REQUIRED — your SpoolEase device
BRIDGE_SPOOLEASE_HOST=192.168.1.50
BRIDGE_SPOOLEASE_SECURITY_KEY=ABCDEFG

# Point to your local Spoolman instance (not the Docker service name)
BRIDGE_SPOOLMAN_HOST=localhost
BRIDGE_SPOOLMAN_PORT=8000

# Use a local path instead of the Docker volume path
BRIDGE_MAPPING_FILE_PATH=./mapping.json
```

> **Important:** When running outside Docker, change `BRIDGE_SPOOLMAN_HOST` from `spoolman` (the Docker service name) to `localhost` or your Spoolman machine's IP. Also change `BRIDGE_MAPPING_FILE_PATH` to a writable local path.

### 4. Run the Bridge

```bash
python -m src.main
```

The bridge will start, validate your SpoolEase key, sync your spools, and keep running until you stop it with `Ctrl+C`.

To run it in the background:

```bash
nohup python -m src.main > bridge.log 2>&1 &
```

Or set it up as a systemd service for automatic startup (Linux):

```ini
# /etc/systemd/system/spoolease-bridge.service
[Unit]
Description=SpoolEase-Spoolman Bridge
After=network.target

[Service]
Type=simple
User=your-username
WorkingDirectory=/path/to/spoolease-spoolman-bridge
EnvironmentFile=/path/to/spoolease-spoolman-bridge/.env
ExecStart=/path/to/spoolease-spoolman-bridge/venv/bin/python -m src.main
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now spoolease-bridge
```

## Using With an Existing Spoolman Instance

If you already have Spoolman running, you can run just the bridge and point it at your existing instance:

1. Remove or comment out the `spoolman` service from `docker-compose.yaml`
2. Set `BRIDGE_SPOOLMAN_HOST` to your Spoolman instance's hostname/IP
3. Set `BRIDGE_SPOOLMAN_PORT` to the port Spoolman is listening on (usually `8000`)
4. Run `docker compose up -d`

## Project Structure

```
spoolease-spoolman-bridge/
├── src/
│   ├── main.py              # Entry point — startup, validation, sync orchestration
│   ├── config.py            # Environment variable loading and configuration
│   ├── sync_engine.py       # Core sync logic — polling, deltas, WebSocket listener
│   ├── spoolease_client.py  # Encrypted REST client for SpoolEase device
│   ├── spoolman_client.py   # REST + WebSocket client for Spoolman
│   ├── mapping_store.py     # Persistent JSON mapping store with atomic writes
│   ├── encryption.py        # AES-256-GCM encryption (PBKDF2-HMAC-SHA256 key derivation)
│   ├── csv_parser.py        # SpoolEase custom CSV format parser
│   ├── models.py            # Data models (SpoolEaseRecord, SpoolMapping, SyncState)
│   └── logging_config.py    # Structured logging setup
├── tests/
│   ├── test_encryption.py   # Encryption roundtrip and key derivation tests
│   ├── test_csv_parser.py   # CSV parsing with special encoding tests
│   ├── test_mapping_store.py# Mapping persistence and recovery tests
│   ├── test_sync_engine.py  # Sync logic and delta calculation tests
│   └── conftest.py          # Shared test fixtures
├── simulation/              # Mock SpoolEase server for integration testing
├── docker-compose.yaml      # Production deployment
├── docker-compose.simulation.yaml  # Integration test environment
├── Dockerfile               # Bridge container image
├── pyproject.toml           # Python project metadata and dependencies
└── .env.example             # Environment variable template
```

## Testing

### Unit Tests

```bash
pip install -e ".[dev]"
pytest
```

With coverage:

```bash
pytest --cov=src
```

### Simulation (Integration Testing)

A full simulation environment is provided with a mock SpoolEase device. This lets you test the entire pipeline without real hardware.

```bash
# Start the simulation stack (Spoolman + mock SpoolEase + bridge)
docker compose -f docker-compose.simulation.yaml up --build

# In another terminal, run the scenario script
python -m simulation.run_scenario
```

The scenario script walks through a realistic workflow: adding spools, simulating Bambu printer consumption, simulating Klipper-side usage, and verifying everything stays in sync.

You can also interact with the mock SpoolEase manually:

```bash
# List mock spools
curl http://localhost:8081/admin/spools

# Add a spool
curl -X POST http://localhost:8081/admin/spools \
  -H 'Content-Type: application/json' \
  -d '{"tag_id":"04AABBCCDDEE01","material_type":"PLA","brand":"Bambu","color_name":"Red"}'

# Simulate filament consumption
curl -X POST http://localhost:8081/admin/consume \
  -H 'Content-Type: application/json' \
  -d '{"spool_id":"1","grams":25.0}'

# Check results in Spoolman
curl http://localhost:7912/api/v1/spool
```

## Troubleshooting

### Bridge can't connect to SpoolEase

- Verify the device is powered on and connected to your network
- Confirm the IP address: `ping <BRIDGE_SPOOLEASE_HOST>`
- Check that the security key matches exactly (7 characters, case-sensitive)
- Look at bridge logs: `docker compose logs -f spoolease-bridge`

### Spools appear in SpoolEase but not in Spoolman

- The bridge only syncs spools that have a valid NFC tag ID — spools without tags are skipped
- Check the bridge logs for sync errors: `docker compose logs -f spoolease-bridge`
- Try restarting the bridge to trigger a fresh full sync

### Consumption isn't updating

- The default polling interval is 30 seconds — changes aren't instant
- The delta threshold is 0.1g by default — very small changes may be batched
- Set `BRIDGE_LOG_LEVEL=DEBUG` for detailed sync logging

### Mapping file lost or corrupted

The bridge automatically recovers mappings from Spoolman's extra fields on startup. Simply restart the bridge and it will rebuild the mapping file.

## Tech Stack

- **Python 3.11+** with async/await (asyncio)
- **aiohttp** — async HTTP client and WebSocket support
- **cryptography** — AES-256-GCM encryption for SpoolEase API
- **Docker** — containerized deployment

## License

This project is not yet licensed. See [LICENSE](LICENSE) for details once a license is chosen.
