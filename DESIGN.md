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
- Stats, blocking on/off, **manual** gravity control — core `pi_hole` and
  `bastgau/ha-pi-hole-v6` already cover those. This integration does not add
  a "run gravity" button or service, but it *automatically* triggers a
  debounced gravity rebuild as a correctness side effect of enabling a list
  (see decision 9).

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
| 8 | List model | `@dataclass(frozen=True) PiHoleList` (models.py); lenient `from_dict` (only `id` required, unknown FTL keys dropped), `update_payload` builds the PUT body, `merge_update` copies only non-`None` fields | Typed immutable rows end the stringly-typed dict flow; the write path can no longer forget the comment (it always echoes it); lenient parsing fits trusted FTL output (KISS — no pydantic-style validation) |
| 9 | Gravity on enable | Every `turn_on` schedules a **debounced** gravity rebuild (`POST /api/action/gravity`); `turn_off` triggers nothing; surface state via a device-level "Gravity update" binary_sensor | Pi-hole v6 toggling `enabled=true` does not rebuild gravity — a list enabled since the last `pihole -g` contributes zero rows to the gravity table until a rebuild, so blocking stays ineffective despite the switch reading "on". Disabling is instant (view filter) and needs no gravity. Always on, no options-flow toggle (decision confirmed with the user): cheap (`pihole -g` is a no-op when nothing changed) and only fires on deliberate enabling |

## Pi-hole v6 API surface used

All verified against Pi-hole 2025.07.x.

- `POST /api/auth` body `{"password": "<app password>"}` →
  `{"session": {"valid": true, "sid": ..., "csrf": ..., "validity": 300}}`.
  Session valid 300 s **idle** by default (`webserver.session.timeout` configurable).
- `GET /api/lists` (header `X-FTL-SID: <sid>`) →
  `{"lists": [{"id", "address", "enabled", "groups", "type", "comment", "number",
  "invalid_domains", "status", "date_added", "date_modified", "date_updated", ...}]}`.
- `PUT /api/lists/{quote(address, safe="")}?type=block` body
  `{"enabled": bool, "comment": str}` → HTTP 200 with the updated row read
  back, wrapped in `{"lists": [<updated list>], "processed": {...}}`
  (verified in FTL `src/api/list.c` + `src/database/gravity-db.c`, v6.1 and
  master — the response is not the bare list object). **The body is a full-row
  upsert, not a merge**: FTL runs `INSERT ... ON CONFLICT(address,type) DO
  UPDATE SET enabled=:enabled, comment=:comment, type=:type`, so any mutable
  field missing from the body is reset — `comment` → NULL, `enabled` → true.
  The integration always echoes the list's current `comment` (the web UI does
  the same: `PUT` with `{groups, comment, enabled, type}`); `type` is carried
  by the query parameter, and `groups` are only touched when the body contains
  a `groups` array, which the integration intentionally never sends.
- State-changing requests may require `X-FTL-CSRF` (from the session) — include when
  present (harmless; matches `HoleV6` behavior).
- `POST /api/action/gravity` (verified against FTL v6.2.3 `src/api/api.c` +
  `src/api/action.c`) triggers `pihole -g`. FTL forks the process and
  **live-streams its CLI output over one long-lived chunked response**: the
  `200 text/plain` headers are sent *before* gravity completes, chunks stream
  until the run exits, then a chunked terminator is followed by a *second*
  JSON response that client-side parsers (aiohttp) never expose. The body is
  therefore **text, not JSON** — never call `.json()`. Per Pi-hole
  maintainers: use the endpoint, ignore the text, re-fetch `/api/lists`
  afterwards (the coordinator refresh does this). Requirements understood
  from the source and verified in testing:
  - Requires auth (SID + CSRF headers, mirror of the PUT path); a 401
    re-authenticates once, persistent 401 → `HoleAuthenticationError`.
  - **Not** gated by `webserver.api.allow_destructive` (unlike restartDNS /
    flush).
  - Status is 200 for both successful and failed runs once FTL has forked;
    failures surface as `[✗]` markers in the streamed text, which may carry
    ANSI escapes (stripped before scanning). HTTP 200 + clean output is the
    success contract.
  - Gravity can take **minutes** on large lists (StevenBlack ~30 MB, 235k
    domains) → the client's 15 s timeout must not apply; `run_gravity` uses
    the dedicated `GRAVITY_TIMEOUT = 1800` cap.
  - Concurrent runs collide on Pi-hole's gravity lock (`pihole -g` exits
    "already running") → the coordinator debounces (10 s window + one
    trailing rerun) so a burst of enables triggers one run.
  - The trailing second response **poisons the connection for reuse**: its
    bytes remain buffered past the chunked terminator, and the next request
    on the pooled connection would parse them as its response head ("Bad
    status line" on the following `/api/lists` fetch — observed on a real
    instance). The connection cannot be salvaged client-side: aiohttp
    releases it back to the pool the instant the payload hits EOF
    (`ClientResponse._response_eof`), before `run_gravity` regains control —
    `close()` after the read is a no-op. The POST therefore runs on a
    **dedicated one-shot `aiohttp.ClientSession`** that is fully closed
    when the read completes, so the poisoned connection can never be reused
    by the shared session (regression-tested in
    `test_run_gravity_uses_dedicated_session`; the behavior was also
    reproduced against a byte-faithful fake FTL stream before choosing this
    design).
- Login attempts are **rate-limited** and concurrent sessions limited: never probe
  auth in loops; one attempt per config-flow step; logout politely on unload.

## Architecture

```
config entry (one per Pi-hole)
├── PiHoleV6Lists(HoleV6)                    # api.py
│     ├─ get_lists() -> list[PiHoleList]     #   GET /api/lists (inherited _fetch_data)
│     ├─ set_list_enabled(PiHoleList, bool)  #   PUT ...?type=block, 401 re-auth once
│     │                                      #   -> PiHoleList (parsed response)
│     └─ run_gravity() -> bool               #   POST /api/action/gravity (401 re-auth
│                                            #   once, GRAVITY_TIMEOUT cap) on a
│                                            #   dedicated one-shot session; FTL streams
│                                            #   text, read only for [✗] markers
├── PiHoleListsCoordinator(DataUpdateCoordinator[dict[int, PiHoleList]])
│     ├─ update: get_lists() -> {list_id: PiHoleList} (type=block only)
│     ├─ scan_interval: 5 min default, 1–60 min via options flow
│     └─ gravity state machine (decision 9): schedule_gravity_update() starts
│        a debounced task (10 s) -> RUNNING -> run_gravity() -> refresh -> IDLE;
│        a toggle while the task is alive marks one trailing rerun instead of
│        cancelling the in-flight POST; failures -> FAILED. Every transition
│        notifies listeners via async_set_updated_data
├── SwitchEntity per list (CoordinatorEntity)
│     ├─ is_on = list_data.enabled
│     ├─ turn_on: await set_list_enabled(...); merge; refresh; then
│     │  coordinator.schedule_gravity_update()
│     ├─ turn_off: await set_list_enabled(...); merge; refresh (no gravity —
│     │  disabling is instant via the gravity view filter)
│     └─ attrs: id, address, type, groups, comment, number, invalid_domains,
│        status, date_updated
└── GravityUpdateSensor (CoordinatorEntity + BinarySensorEntity, one per entry)
      ├─ is_on = gravity_state in (pending, running)
      ├─ attrs: status = idle|pending|running|failed
      └─ re-renders via the same async_set_updated_data fan-out
```

List rows are typed as the frozen `PiHoleList` model (`models.py`) instead of
raw dicts: reads use attribute access, the PUT payload is built from the model
by `update_payload` (the comment can no longer be forgotten at the call site),
and the defensive toggle merge is `merge_update` (copies only non-`None`
fields of the response, so a hypothetical slim response can never wipe
details).

### `api.py` subclass notes

`HoleV6` has no list endpoints and its `_fetch_data()` is GET-only, so the subclass:

- `get_lists() -> list[PiHoleList]`: `resp = await self._fetch_data("/lists")`;
  parse `resp["lists"]` via `PiHoleList.from_dict`.
- `set_list_enabled(list_obj: PiHoleList, enabled: bool) -> PiHoleList`: `await
  self.ensure_auth()`, then `PUT {base_url}/api/lists/{quote(address, safe="")}
  ?type={list_obj.type}` with `json=list_obj.update_payload(enabled)` and
  sid/csrf headers; on 401 re-authenticate once and retry (mirror
  `_fetch_data`); non-200 → `HoleError`; unwrap the `{"lists": [...]}` response
  (fall back to the body as-is for other shapes) and parse it via `from_dict`
  (parse failures → `HoleResponseError`). The model is the single source of the
  payload shape: `update_payload` always carries `{"enabled": bool, "comment":
  str|None}` because FTL's PUT replaces the row and resets a missing `comment`
  to NULL (regression: toggling used to wipe the comment in Pi-hole, which
  renamed the HA entity).
- `run_gravity() -> bool`: `await self.ensure_auth()`, then
  `POST {base_url}/api/action/gravity` with SID/CSRF headers on a
  **dedicated one-shot `aiohttp.ClientSession`** (see the API-surface
  section: the trailing response makes the connection unfit for reuse, and
  aiohttp pools it at payload EOF before the caller can act — the shared
  session must never see this stream), wrapped in
  `asyncio.timeout(GRAVITY_TIMEOUT)` — explicitly **not** `self.timeout`.
  401 → re-authenticate once and retry; still 401 → `HoleAuthenticationError`;
  non-200 → `HoleError`; timeout/`ClientError`/`gaierror` → `HoleConnectionError`
  (all mirror `set_list_enabled`). `await response.text()` waits for the streamed
  run to complete; the body is never parsed as JSON. Strip ANSI escapes and scan
  for `[✗]` failure markers: clean output → `True`, `[✗]` present → warning +
  `False`. See the API-surface section for the streaming contract.

Reliance on `HoleV6` internals (`_fetch_data`, `_session_id`, `_csrf_token`,
`ensure_auth`, `base_url`) is the known risk → mitigated by the exact pin and by
unit tests covering the paths that use them.

### Entity model

- Device per config entry: "Pi-hole lists (<host>)".
- `unique_id = f"{entry_id}-{list_id}"` (multi-entry safe). Trade-off: recreating a
  config entry orphans entities — accepted; documented in README gotchas.
- `name`: list `comment` if set, else a humanized address: GitHub-hosted
  lists (`github.com`, `raw.githubusercontent.com`) are named `owner/repo`,
  everything else `host + last path segment`.
- Entities are added/removed on every poll from coordinator data (lists created or
  deleted in the Pi-hole UI appear/disappear accordingly). The platform's
  `async_add_entities` callback is called directly from the listener — it is a
  plain function returning `None` that schedules the add internally
  (HA ≥ 2025.10), so wrapping it in `hass.async_create_task` raises
  `TypeError: a coroutine was expected, got None`. Removals are scheduled via
  `hass.async_create_task(entity.async_remove(force_remove=True))` because
  `async_remove` is a coroutine.
- One extra device-level entity per entry: the *Gravity update* binary sensor
  (`binary_sensor.py`, `unique_id = f"{entry.entry_id}-gravity-update"`), on
  the same device as the switches. `is_on` = gravity rebuild pending or
  running; `status` attribute = `idle|pending|running|failed`. It is a
  `CoordinatorEntity` and re-renders through the same `async_set_updated_data`
  fan-out that pushes gravity state transitions — no separate update path.
  The coordinator's `gravity_state` is a plain attribute read on every
  notification, which also makes it trivial to build automation triggers on
  the switch-to-sensor events.

### Config flow

- `step_user`: URL (`scheme://host[:port]`, e.g. `http://pi.hole:8081`), app
  password, verify-SSL (shown for https). Validation: instantiate client →
  `authenticate()` → `logout()`. `HoleAuthenticationError` → "invalid password";
  `HoleConnectionError` → "cannot connect". No retry loops.
- `step_reauth`: on `ConfigEntryAuthFailed` from the coordinator (bad password).
- Options flow: scan interval (minutes, 1–60, default 5). The flow reads the
  entry through the HA-provided `OptionsFlow.config_entry` property (entry_id is
  `flow.handler`, set before the first step) and never stores it in `__init__` —
  `config_entry` has no setter since HA 2025.12.
- Unload: cancel any pending gravity task, then `api.logout()` to release the
  FTL session (the server-side `pihole -g` fork, if any, finishes on its own).

## Version constraints

- `hole==0.9.2` requires Python ≥ 3.13 → HA ≥ **2025.10** (whose own floor is
  Python ≥ 3.13.2; `pyproject.toml` mirrors that).
- `hacs.json`: `"homeassistant": "2025.10.0"`.
- HA floor stays **2025.10**: the options flow only relies on the
  `config_entry` property, which resolves via `flow.handler` on every supported
  version — the deprecated setter (removed in 2025.12) is not used.
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
  coordinator.py     # poll + debounced gravity state machine
  models.py          # frozen PiHoleList: from_dict / update_payload / merge_update
  switch.py
  binary_sensor.py   # device-level "Gravity update" sensor (one per entry)
  entity.py          # base entity: device info, extra attrs
  const.py
  manifest.json
  strings.json
  translations/en.json
  brand/             # HACS brand assets: icon.png (256x256), logo.png (512x512)
hacs.json
README.md
DESIGN.md            # this file
AGENTS.md
tests/
  test_api.py        # aioresponses: auth parse, lists parse, PUT path/headers,
                     # 401 re-auth, session-validity re-auth, gravity POST
  test_models.py     # from_dict parse/leniency, update_payload shape,
                     # merge_update non-None semantics, frozen instances
  test_coordinator.py # type=block filtering, auth-failed/connection error mapping,
                     # gravity debounce state machine (trailing rerun, failures,
                     # cancellation)
  test_switch.py     # is_on mapping; turn_on/off + refresh; name fallbacks
                     # (comment / GitHub owner-repo / host+segment)
  test_binary_sensor.py # gravity sensor is_on/status mapping, unique id,
                     # device info, one entity per entry
.github/workflows/
  ci.yml             # hacs validate + hassfest + ruff + pytest (py3.13)
LICENSE              # MIT
```

## Testing strategy

- Unit: fake `aiohttp.ClientSession` (aioresponses) for all API paths; assert the
  PUT URL contains the URL-encoded address and `?type=block`, carries `X-FTL-SID`
  (+ `X-FTL-CSRF` when present), the payload echoes the list's comment,
  unwraps the `{"lists": [...]}` response, and that a 401 triggers exactly one
  re-auth retry. The gravity POST is covered the same way: SID/CSRF headers,
  plain-text bodies (`content_type="text/plain"`), ANSI stripping, `[✗]` → `False`,
  401 one-retry, persistent 401 → `HoleAuthenticationError`, non-200 →
  `HoleError`, connection errors → `HoleConnectionError`, and the dedicated
  one-shot session (POST on it, closed afterwards — see the API-surface
  connection-poisoning note).
- Gravity debounce (`test_coordinator.py`, `GRAVITY_DEBOUNCE_SECONDS` patched to 0,
  `hass.async_create_task` faked onto the real event loop): single schedule
  transitions `pending → running → idle` and refreshes once; double schedule
  before the run coalesces into one `run_gravity` call; a schedule while the
  run is in flight triggers exactly one trailing rerun; `False`/exceptions →
  `failed` without refresh; `cancel_pending_gravity` cancels a live task
  without raising and without spawning a rerun.
- Model (`test_models.py`): lenient `from_dict` parsing (unknown keys dropped,
  missing `id` rejected), `update_payload` shape (comment always echoed, `null`
  when absent), `merge_update` copying only non-`None` fields, frozen instances.
- Binary sensor (`test_binary_sensor.py`): `is_on` maps pending/running → on
  and idle/failed → off, the `status` attribute mirrors
  `coordinator.gravity_state`, unique id/device info match the switches, and
  `async_setup_entry` creates exactly one entity per entry.
- Integration is not tested against a live Pi-hole in CI; manual two-way sync test
  on a real instance before each release (README checklist).

## Risks & gotchas

- 300 s idle session timeout → polls ≥ 300 s always re-auth; 401 path must be solid.
- Pi-hole rate-limits failed logins — no probe loops anywhere.
- `hole` internals are private API surface → exact pin; bump deliberately with tests.
- `enabled` is global across groups — set expectations in README.
- A malformed PUT response (missing `id`, non-parseable body) now raises
  `HoleResponseError` — a toggle fails loudly instead of silently carrying a
  raw dict; reads accept FTL output as trusted (only `id` is validated).
- FTL's PUT replaces the row: a toggle must echo the list's `comment` or
  Pi-hole wipes it (which would rename the HA entity to its address fallback).
  Echoing last-polled data can also overwrite a comment changed in the Pi-hole
  UI within one poll interval — the coordinator re-syncs on the next poll.
- Editing a list's address in Pi-hole keeps its `id` → unique_id stays stable.
- Pi-hole-side log noise during any gravity run: FTL logs transient
  `ERROR: SQLite3: no such table: main.gravity/antigravity` lines for DNS
  queries arriving in the sub-second database-swap window
  (pi-hole/pi-hole#6241 — expected v6 behavior, also on the weekly cron).
  Those queries are answered normally but unfiltered; blocking resumes when
  FTL logs "Gravity database has been updated, reloading now". Documented in
  the README gotchas so users don't misread it as an integration failure.
- Gravity is slow: `pihole -g` can take minutes on large lists. The run happens
  in a background task with a 30-minute cap (`GRAVITY_TIMEOUT`); the switch's
  turn_on completes immediately after scheduling, and the effect of an enable
  (domains actually blocking) lands when the rebuild finishes.
- A burst of enable toggles is coalesced by the 10 s debounce: one run covers
  everything enabled before it starts, and a toggle landing while a run is in
  flight marks exactly one trailing rerun (Pi-hole's gravity lock rejects
  overlapping runs with "already running"; a toggle can also land after the
  forked `pihole -g` already read the adlists, so the rerun is needed for
  correctness, not just politeness).
- Failure surfaces as sensor `status: failed` + a warning log. Success is
  judged on the streamed text (`[✗]` markers), because the HTTP status is 200
  for both outcomes once FTL has forked — note the status check happens after
  the full body was read, and a run that timed out at the 30-minute cap
  becomes `failed` with no way to distinguish "still running" from "hung".
- aiohttp only ever reads the first (text) response — FTL appends a second
  JSON response after the chunked terminator, which client parsers discard.
  The discarded bytes stay in the connection buffer, and aiohttp's
  auto-release at payload EOF pools the connection before the caller can
  close it — so the gravity POST must run on a dedicated one-shot session,
  never the shared one (see the API contract above).

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
