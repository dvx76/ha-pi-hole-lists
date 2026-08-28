# ha-pi-hole-lists — agent instructions

This repo is a Home Assistant custom integration, domain `pi_hole_lists`: Pi-hole
v6 blocklists as switch entities. `DESIGN.md` is the source of truth for behavior —
update it in the same commit as any behavior change.

## Commands

- Lint: `ruff check .` and `ruff format --check .`
- Tests: `python -m pytest`
- CI (on push): hacs validate, hassfest, ruff, pytest on Python 3.13.
  Keep `.github/workflows/ci.yml` green before merge.

## Rules

1. **No secrets or real infrastructure details.** No app passwords, tokens, LAN
   IPs, or real hostnames in code, docs, tests, or commit messages. Examples use
   `http://pi.hole:8081`.
2. **`hole==0.9.2` is pinned exactly** because `api.py` relies on `HoleV6`
   private internals (`_fetch_data`, `_session_id`, `_csrf_token`, `ensure_auth`).
   Any version bump requires tests green and a note in DESIGN.md.
3. **Tests required for `api.py` and `switch.py` changes** (aioresponses-based);
   new API paths get tests before merge.
4. **Releases**: bump `version` in `manifest.json`, tag `vX.Y.Z` on main — HACS
   users update from releases.
5. **No Pi-hole auth probing loops** anywhere (config flow, coordinator): v6
   rate-limits failed logins. One attempt per user action.
6. **Keep README accurate** when config flow fields or HA version floors change.

## Style

- Follow HA integration conventions (config flow v2, `DataUpdateCoordinator`,
  `CoordinatorEntity`, typed config entries) as used in HA 2025.10+ core.
- Ruff with default HA-friendly settings in `pyproject.toml` (E/F/I/W rules,
  line-length 88).
