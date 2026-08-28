# DESIGN — `pi_hole_lists` Home Assistant integration

Status: approved design, ready for implementation. Update this file in the same
commit as any behavior change.

## Purpose

Expose each Pi-hole **v6** blocklist (`type=block` adlist) as a `switch` entity in
Home Assistant:

- Toggling the switch in HA enables/disables the list in Pi-hole.
- Changes made in the Pi-hole UI are reflected in HA within one poll interval.
- Works from any HA instance (≥ 2025.10) to any Pi-hole v6, including multiple
  Pi-hole instances (one config entry each).

## Non-goals (v1)

- Allow lists (`type=allow`) — same API path, planned as an options-flow toggle later.
- Per-group control of a list — `enabled` is global across groups; `groups` is
  exposed as an attribute only.
- Pi-hole v5 — this integration is v6 API only.
- Stats, blocking on/off, gravity control — core `pi_hole` and
  `bastgau/ha-pi-hole-v6` already cover those.

## Decisions

| # | Branch | Decision | Why |
|---|--------|----------|-----|
| 1 | Approach | Standalone HACS custom integration, own domain | Core `pi_hole` (v6-capable since HA 2025.8) has no per-list entities; a custom component cannot extend a core domain without replacing it |
| 2 | API client | Depend on `python-hole`, subclass `HoleV6` | Reuses the session/auth logic (SID/CSRF, validity, 401 re-auth) from the library HA core itself uses |
| 3 | Scope | Block lists only | Matches the stated need; allow lists are a trivial follow-up |
| 4 | Toggle semantics | Global per-list `enabled` | Per-list-per-group would mean editing the `groups` array — a different feature |
| 5 | Sync | `DataUpdateCoordinator` poll, default 5 min, options-flow configurable; toggle awaits PUT and updates state from the response | Same cadence as core/pi_hole_v6; PUT is ~10 ms, no optimistic state needed |
| 6 | Config flow | URL + app password + verify-SSL; reauth flow; multi-entry | App password is the recommended v6 credential |
| 7 | Version pin | `hole==0.9.2` exact | Same version core pins; protects the subclass from private-API drift |

## Pi-hole v6 API surface used

All verified against Pi-hole 2025.07.x.

- `POST /api/auth` body `{"password": "<app password>"}` →
  `{"session": {"valid": true, "sid": ..., "csrf": ..., "validity": 300}}`.
  Session valid 300 s **idle** by default (`webserver.session.timeout` configurable).
- `GET /api/lists` (header `X-FTL-SID: <sid>`) →
  `{"lists": [{"id", "address", "enabled", "groups", "type", "comment", "number",
  "invalid_domains", "status", "date_added", "date_modified", "date_updated", ...}]}`.
- `PUT /api/lists/{quote(address, safe="")}?type=block` body `{"enabled": bool}` →
  returns the updated list object. Partial body accepted.
- State-changing requests may require `X-FTL-CSRF` (from the session) — include when
  present (harmless; matches `HoleV6` behavior).
- Login attempts are **rate-limited** and concurrent sessions limited: never probe
  auth in loops; one attempt per config-flow step; logout politely on unload.

## Architecture

```
config entry (one per Pi-hole)
├── PiHoleV6Lists(HoleV6)                    # api.py
│     ├─ get_lists() -> list[dict]           #   GET /api/lists (inherited _fetch_data)
│     └─ set_list_enabled() -> dict          #   PUT ...?type=block, 401 re-auth once
├── PiHoleListsCoordinator(DataUpdateCoordinator[dict[int, dict]])
│     ├─ update: get_lists() -> {list_id: list} (type=block only)
│     └─ scan_interval: 5 min default, 1–60 min via options flow
└── SwitchEntity per list (CoordinatorEntity)
      ├─ is_on = list["enabled"]
      ├─ turn_on/off: await set_list_enabled(); update data from response; refresh
      └─ attrs: id, address, type, groups, comment, number, invalid_domains,
         status, date_updated
```

### `api.py` subclass notes

`HoleV6` has no list endpoints and its `_fetch_data()` is GET-only, so the subclass:

- `get_lists()`: `resp = await self._fetch_data("/lists")`; return `resp["lists"]`.
- `set_list_enabled(address, list_type, enabled)`: `await self.ensure_auth()`, then
  `PUT {base_url}/api/lists/{quote(address, safe="")}?type={list_type}` with
  `json={"enabled": enabled}` and sid/csrf headers; on 401 re-authenticate once and
  retry (mirror `_fetch_data`); non-200 → `HoleError`; return the updated list dict.

Reliance on `HoleV6` internals (`_fetch_data`, `_session_id`, `_csrf_token`,
`ensure_auth`, `base_url`) is the known risk → mitigated by the exact pin and by
unit tests covering the paths that use them.

### Entity model

- Device per config entry: "Pi-hole lists (<host>)".
- `unique_id = f"{entry_id}-{list_id}"` (multi-entry safe). Trade-off: recreating a
  config entry orphans entities — accepted; documented in README gotchas.
- `name`: list `comment` if set, else humanized address (host + last path segment).
- Entities are added/removed on every poll from coordinator data (lists created or
  deleted in the Pi-hole UI appear/disappear accordingly).

### Config flow

- `step_user`: URL (`scheme://host[:port]`, e.g. `http://pi.hole:8081`), app
  password, verify-SSL (shown for https). Validation: instantiate client →
  `authenticate()` → `logout()`. `HoleAuthenticationError` → "invalid password";
  `HoleConnectionError` → "cannot connect". No retry loops.
- `step_reauth`: on `ConfigEntryAuthFailed` from the coordinator (bad password).
- Options flow: scan interval (minutes, 1–60, default 5).
- Unload: `api.logout()` to release the FTL session.

## Version constraints

- `hole==0.9.2` requires Python ≥ 3.13 → HA ≥ **2025.10** (whose own floor is
  Python ≥ 3.13.2; `pyproject.toml` mirrors that).
- `hacs.json`: `"homeassistant": "2025.10.0"`.
- `manifest.json`: `"requirements": ["hole==0.9.2"]`,
  `"iot_class": "local_polling"`, `"integration_type": "device"`.
- Tooling: uv with a universal lockfile — CI synced with Python 3.13, local
  dev on Python 3.14.x. `homeassistant` is a dev dependency so tests import
  the real HA helper modules; the lockfile resolves to the newest HA that
  supports each Python (2026.2.x is the last 3.13-compatible line).

## Repo layout

```
custom_components/pi_hole_lists/
  __init__.py        # async_setup_entry, runtime data, platforms, logout on unload
  api.py             # PiHoleV6Lists(HoleV6)
  config_flow.py     # user + reauth + options
  coordinator.py
  switch.py
  entity.py          # base entity: device info, extra attrs
  const.py
  manifest.json
  strings.json
  translations/en.json
hacs.json
README.md
DESIGN.md            # this file
AGENTS.md
tests/
  test_api.py        # aioresponses: auth parse, lists parse, PUT path/headers,
                     # 401 re-auth, session-validity re-auth
  test_coordinator.py # type=block filtering, auth-failed/connection error mapping
  test_switch.py     # is_on mapping; turn_on/off calls set_list_enabled + refresh
.github/workflows/
  ci.yml             # hacs validate + hassfest + ruff + pytest (py3.13)
LICENSE              # MIT
```

## Testing strategy

- Unit: fake `aiohttp.ClientSession` (aioresponses) for all API paths; assert the
  PUT URL contains the URL-encoded address and `?type=block`, carries `X-FTL-SID`
  (+ `X-FTL-CSRF` when present), and that a 401 triggers exactly one re-auth retry.
- Integration is not tested against a live Pi-hole in CI; manual two-way sync test
  on a real instance before each release (README checklist).

## Risks & gotchas

- 300 s idle session timeout → polls ≥ 300 s always re-auth; 401 path must be solid.
- Pi-hole rate-limits failed logins — no probe loops anywhere.
- `hole` internals are private API surface → exact pin; bump deliberately with tests.
- `enabled` is global across groups — set expectations in README.
- Editing a list's address in Pi-hole keeps its `id` → unique_id stays stable.

## Task order

1. Scaffold repo: CI (hacs validate, hassfest, ruff, pytest), `.gitignore`, README,
   LICENSE.
2. Implement `api.py` + `tests/test_api.py`.
3. Implement `config_flow.py` (user/reauth/options), `coordinator.py`,
   `entity.py`/`switch.py` + `tests/test_switch.py`; `__init__.py` wiring.
4. Manual validation against a real Pi-hole v6 (both sync directions).
5. Tag `v0.1.0`; HACS custom-repository install instructions in README.

## Future / open

- Allow lists behind an options-flow toggle.
- Per-group list switches (membership edits) — only if demand appears.
- Upstream the list-switch concept to core `pi_hole` or `bastgau/ha-pi-hole-v6`;
  submit to HACS default repositories.
