# Pi-hole Lists for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

Custom integration exposing every **Pi-hole v6 blocklist** as a switch entity in
Home Assistant. Toggle a list in HA and it is enabled/disabled in Pi-hole; changes
made in the Pi-hole UI appear in HA within one poll interval (default 5 min).

- Pi-hole **v6** only (API v6), Home Assistant ≥ **2025.10**
- One config entry per Pi-hole instance — multiple Pi-holes supported
- No per-group control: a switch toggles the list's `enabled` flag globally
  (group membership is exposed as an entity attribute)

## Installation

1. HACS → ⋮ → Custom repositories →
   `https://github.com/dvx76/ha-pi-hole-lists`, category **Integration**.
2. Install **Pi-hole Lists** in HACS, restart Home Assistant.
3. Settings → Devices & Services → Add Integration → **Pi-hole Lists**.
   - **URL**: `http://pi.hole` or with a custom port, e.g. `http://pi.hole:8081`
   - **App password**: Pi-hole UI → Settings → Web interface/API →
     *Configure app password* (your admin login password also works, but an app
     password is recommended)
   - **Verify SSL**: enable for https with a trusted certificate

## Options

- **Scan interval** (1–60 min, default 5): how often list states are polled from
  Pi-hole.

## Naming

Entities use the list's **Comment** (as shown in the Pi-hole UI) — it is
recommended to give every list a comment, e.g. `StevenBlack hosts` or
`Block TikTok`.

Without a comment the name is derived from the list URL: GitHub-hosted lists
(`github.com` / `raw.githubusercontent.com`) are named `owner/repo`, e.g.
`StevenBlack/hosts`; everything else uses `host/last-path-segment`. The
entity ID follows the name (prefixed with the Pi-hole device, e.g.
`switch.pi_hole_lists_..._stevenblack_hosts`), so ambiguous addresses produce
ambiguous entity IDs — hence the comment recommendation.

## Development

Tooling is managed with [uv](https://docs.astral.sh/uv/); the lockfile is
universal (Python 3.13 in CI, 3.14.x locally).

```sh
uv sync              # create the venv and install deps
uv run python -m pytest            # tests (aioresponses-based)
uv run ruff check .  && uv run ruff format --check .   # lint
```

## Release checklist

Before tagging `vX.Y.Z`:

1. CI green (hacs validate, hassfest, ruff, pytest).
2. Manual two-way sync test on a real Pi-hole v6:
   - Toggle a switch in HA → state flips in the Pi-hole UI.
   - Edit a list in the Pi-hole UI → HA reflects it within one poll interval.
   - Create and delete a list in the Pi-hole UI → entities appear/disappear.
3. Bump `version` in `manifest.json`, commit, tag `vX.Y.Z` on main.

## Gotchas

- The session Pi-hole issues is valid 300 s idle, so every poll re-authenticates —
  this is normal, not an error.
- Toggling a switch affects **all groups** the list belongs to.
- Re-adding the integration creates new entities (unique IDs are entry-scoped).
- Editing a list's URL in Pi-hole keeps its ID, so the HA entity follows it.

Design and development details: [DESIGN.md](DESIGN.md). License: MIT.
