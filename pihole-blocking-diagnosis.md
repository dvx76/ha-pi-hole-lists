# Pi-hole "nothing is blocked" — diagnosis & fix plan

Host: raspberrypi (arm64), container `pihole`, image `pihole/pihole:2025.07.1`
Core v6.1.4 / Web v6.2.1 / FTL v6.2.3 — upstreams 195.130.130.2, 8.8.8.8, 8.8.4.4
All times below are CEST (UTC+2).

## Root cause

The effective (active) blocklist is **empty**, even though Pi-hole reports
"blocking is enabled" and holds 218k gravity domains in memory.

Evidence chain:

1. `gravity` table (raw): 235,214 domains — but 100% of them come from
   adlist id 1 (StevenBlack, **disabled**) and id 27 (phishing_army, **disabled**).
   Zero rows from adlist id 2 (TikTok, the only **enabled** list).
2. `vw_gravity` (the enabled-state-filtered view FTL evaluates): **0 rows**.
   The view filters `WHERE adlist.enabled = 1` — so with only TikTok enabled,
   and TikTok having no rows in `gravity`, the effective blocklist is empty.
3. Live test: `dig eu1.clevertap-prod.com @127.0.0.1` (a domain present in the
   raw gravity table) was forwarded upstream and answered with real IPs.
4. `query_storage`: **zero** GRAVITY-blocked queries since the FTL restart at
   12:03 today; last "gravity blocked" line in pihole.log was 11:59:24.
5. Daily blocking was healthy every prior day (e.g. 3,742 blocks on Aug 28,
   2,973 on Aug 27, ...) — this broke **today** only.

## Timeline of what happened

- **Aug 24 21:55** — gravity update ran with TikTok **enabled** (backup
  `gravity.db.2`); TikTok's ~6,650 domains were in gravity and blocking worked.
- **Aug 29 11:39–11:40** — a gravity update ran while TikTok was **disabled**
  and StevenBlack + phishing_army were enabled. Result: `gravity` table was
  rebuilt containing *only* StevenBlack + phishing_army; TikTok's domains were
  dropped (its list `date_updated` still shows Aug 24).
- **Aug 29 ~11:49–12:03** — lists were toggled in the UI: StevenBlack and
  phishing_army disabled, TikTok enabled. In Pi-hole v6, toggling a list does
  **not** rebuild gravity — it only changes the `enabled` flag, which instantly
  deactivates the (now disabled) lists' domains.
- **Aug 29 12:03:41** — container restarted; FTL reloaded the same state.
- Result: enabled lists (TikTok) have no domains in gravity → **0 effective
  domains → nothing is blocked.** FTL still reports 218,199 domains (raw
  count) and "blocking enabled", which makes this look healthy at first glance.

## Not problems (ruled out)

- Container health, port 53 listeners, upstreams — all fine.
- Client/group assignments — fine (no custom client entries; all adlists in
  group 0; TikTok list mapped to group 0).
- The 4 "Added from Query Log" entries are allowlist (type 0) entries — normal.
- No FTL error/warning messages, no blocking timer, no failed list downloads.
- The TikTok list file itself is healthy: 6,668 hosts incl. `tiktok.com`,
  `www.tiktok.com`, `api.tiktok.com`, app/CDN/telemetry endpoints.

## Fix (requires Code Mode — not executed yet)

1. Decide the desired list set:
   - TikTok-only blocking: leave lists as-is (only TikTok enabled).
   - Restore ad-blocking too: also re-enable StevenBlack and/or phishing_army.
2. Rebuild gravity:
   `docker exec pihole pihole -g`
   (or "Tools → Update Gravity" in the web UI).
   This regenerates the `gravity` table from the *currently enabled* lists, so
   TikTok's ~6,650 domains become active.
3. Verify:
   - `docker exec pihole pihole -q www.tiktok.com` → should match adlist 2
   - `docker exec pihole dig +short www.tiktok.com @127.0.0.1` → 0.0.0.0/NODATA
   - pihole.log should show "gravity blocked www.tiktok.com"
4. Note: the container's weekly cron (Sunday ~04:29) would eventually do the
   same rebuild automatically, but running it now is immediate.

## Caveats after fixing

- Clients/OSes may have cached pre-fix answers (short-lived); browsers using
  DoH or hardcoded DNS bypass Pi-hole entirely.
- Future habit: after enabling/disabling lists, run Update Gravity — list
  toggles alone don't add/remove domains from the gravity table.

## Sources: why list toggles don't (fully) take effect without `pihole -g`

Verified against the exact FTL version in use (v6.2.3, commit 88737f62).

1. Official docs — https://docs.pi-hole.net/database/domain-database/
   - "The `gravity` and `antigravity` table consists of the domains that have
     been processed by Pi-hole's `gravity` (`pihole -g`) command."
   - "During each run of `pihole -g`, these tables are flushed and completely
     rebuilt from the newly obtained set of domains to be blocked or allowed."
   - "Pi-hole's *FTL*DNS reads the tables through the various views, omitting
     any disabled domains."
   - `adlist.enabled`: "Flag whether domain should be used by `pihole-FTL`
     (0 = disabled, 1 = enabled)".

2. FTL source (github.com/pi-hole/FTL, tag v6.2.3):
   - src/database/gravity-db.c (~line 233): per-client lookup statement
     `SELECT adlist_id FROM vw_gravity WHERE domain = ? AND group_id IN (...);`
     prepared for every client (gravityDB_prepare_client_statements, ~line 876).
     Blocking decisions are made against the *view*, not the raw table.
   - The view (in gravity.db) filters `WHERE adlist.enabled = 1 ...` — so only
     domains from ENABLED lists can ever match, and only if the domain is
     present in the base `gravity` table (populated exclusively by `pihole -g`).
   - src/database/database-thread.c (~lines 121-125, 216-217): FTL watches
     gravity.db (`gravity_updated()`), and on any change sets RELOAD_GRAVITY →
     `FTL_reload_all_domainlists()` — i.e. toggling `enabled` is picked up
     without a restart (log line "Gravity database has been updated, reloading
     now", gravity-db.c ~line 2831 — seen in this instance's FTL.log at 11:40).
   - gravity-db.c (~lines 2017-2021): *deleting* an adlist via API removes its
     rows from `gravity` immediately. There is no equivalent code path that
     *adds* a (re-)enabled list's domains back — only `pihole -g` does that.

3. This instance as living proof:
   - `gravity` table: 235,214 rows (StevenBlack + phishing_army only).
   - `vw_gravity`: 0 rows (both are disabled; TikTok enabled but absent).
   - `/api/info/ftl`: `database.gravity = 218199, lists = 1`.
   - Zero GRAVITY-status queries after the toggles + 12:03 restart.

Net rules for automation:
- Disabling a list: effective in seconds (view filter), no gravity needed.
- Enabling a list: only effective if its domains are still in the gravity
  table — i.e. gravity must have last run while it was enabled. Follow a
  toggle-on with `pihole -g` (or POST /api/action/gravity).
- Adding a new list: always requires `pihole -g`.
- Deleting a list: takes effect immediately (FTL removes its gravity rows).
