# BingeAlert v2.0 — Release Notes

This covers the entire v2.0 line: the **v2.0.0 rebuild** that took BingeAlert from a two-container Postgres install to a single-container SQLite install with a wizard-driven first-run, plus the **v2.0.1 → v2.0.11** stabilization patches that landed in the days after.

If you ran a v1.5.x install: read [Migrating from v1](#migrating-from-v1) — `scripts/migrate_from_v1.py` will copy your existing Postgres data into v2's new SQLite verbatim.

If you're just upgrading along the v2 patches: every release in the 2.0.x line is a `docker compose pull && up -d --force-recreate` away. No data migrations needed.

---

## Table of Contents

- [v2.0.11 — share-route fix](#v2011)
- [v2.0.10 — footer version + login footer position](#v2010)
- [v2.0.9 — logout reliability](#v209)
- [v2.0.8 — repo cleanup](#v208)
- [v2.0.7 — CodeQL hardening](#v207)
- [v2.0.6 — admin email Settings UI](#v206)
- [v2.0.5 — CVE patches](#v205)
- [v2.0.4 — wizard safeguards](#v204)
- [v2.0.3 — notification dedupe](#v203)
- [v2.0.2 — upcoming-episodes multi-Sonarr fix](#v202)
- [v2.0.1 — settings reload + docker.sock GID + admin/config typo](#v201)
- [v2.0.0 — single-container, SQLite, wizard-driven](#v200)
- [Migrating from v1.5.x](#migrating-from-v1)
- [Upgrade procedure](#upgrade-procedure)

---

<a id="v2011"></a>
## v2.0.11 — share-route fix

**Internal Server Error when adding a user to a request** (Upcoming Episodes panel, request detail share dialog).

`app/routers/admin.py` had two `@router.post("/requests/{request_id}/share")` decorators. The buggy one constructed `SharedRequest(shared_at=...)` — a column the model never had (`added_at` is the real one). FastAPI's resolver picked one or the other based on registration order; whenever the buggy one won, SQLAlchemy raised on the unknown kwarg → 500. Removed the duplicate.

---

<a id="v2010"></a>
## v2.0.10 — footer version + login footer position

- **Footer shows the running version.** New `app/__init__.py` exposes `__version__`; FastAPI's `app.version` and `GET /api/version` both pull from it. The admin and login footers fetch and render `© 2026 BingeAlert v2.0.10`. Future releases just bump the constant.
- **Drop the Discussions link.** GitHub Discussions isn't enabled on the repo, so the footer link 404'd.
- **Fix issue-template links.** `.md` → `.yml` to match the actual templates in `.github/ISSUE_TEMPLATE/`. Previously the links opened a blank issue with no pre-filled form.
- **Login footer moved out from under the sign-in card.** `position: fixed; bottom: 16px; right: 20px;` so it sits in the bottom-right corner instead of pushing the layout around.

---

<a id="v209"></a>
## v2.0.9 — logout reliability

- **Logout reliably clears the session cookie.** `delete_cookie` now passes the same `path` / `httponly` / `samesite` / `secure` attributes that were used at `set_cookie` time. Recent Chrome and Edge match cookies for deletion strictly by attribute set; without that, the `Max-Age=0` cookie was treated as a different cookie and the original session lingered.
- **Logout button hides on local-network CIDR bypass.** In that mode the user is authenticating via CIDR, not the session cookie — clicking logout would delete a cookie they don't have, then bounce them back to the dashboard via CIDR auth on the next request. Hiding the button matches reality.

---

<a id="v208"></a>
## v2.0.8 — repo cleanup

Dropped four files that were obsolete after the v2 rebuild:

- `setup.sh`, `update.sh` — v1 bash helpers; v2 flow is `mkdir data && docker compose up -d` and `docker compose pull && up -d --force-recreate`
- `rebuild-data.sh` — calls `/admin/sync/*` endpoints which all have buttons in the dashboard now
- `docker-compose.override.yml.example` — referenced v1's `portal-api` and `postgres` service names that don't exist in v2

---

<a id="v207"></a>
## v2.0.7 — CodeQL hardening

Three CodeQL alerts opened against v2.0.6:

- **HIGH — DOM text reinterpreted as HTML** in the wizard's review panel. The panel built rows by interpolating user-typed form values into a template string and assigning to `innerHTML`. Rebuilt with `createElement` + `textContent` so user input can never be parsed as markup.
- **HIGH — Incomplete string escaping or encoding** in `validateStep`. `.replace("*", "")` only strips the first asterisk; switched to a global regex.
- **Moderate — Workflow does not contain permissions** on the build-check workflow. Added `permissions: contents: read` (the workflow only checks out and builds, never pushes).

---

<a id="v206"></a>
## v2.0.6 — admin email Settings UI

The wizard collected `admin_email` on first run, the backend persisted and exposed it, and the worker code consulted it for weekly summaries and stuck-download alerts — but the Settings page in `admin.html` had no input to view or change it. Once you finished the wizard, the only way to update the address was to edit `config.json` by hand.

Added an "Admin Email (for weekly reports & system alerts)" input under Sender Information. Wired up to populate from `config.admin_email` on load and to ship `config.admin_email` in the save payload.

---

<a id="v205"></a>
## v2.0.5 — CVE patches

- `python-multipart` `0.0.20 → 0.0.27` — closes [GHSA-mj87-hwqh-73pj](https://github.com/advisories/GHSA-mj87-hwqh-73pj) (DoS via large multipart preamble) and a related arbitrary file write advisory.
- `python-dotenv` `1.0.0 → 1.2.2` — closes [GHSA-mf9w-mj56-hr94](https://github.com/advisories/GHSA-mf9w-mj56-hr94) (symlink following in `set_key`).

`python-multipart 0.0.21+` requires Python ≥3.10; we're on `python:3.11-slim` so this is fine.

---

<a id="v204"></a>
## v2.0.4 — wizard safeguards

Found in production: the wizard could submit successfully with `smtp_password` blank because going Back to fix one field blanked the password input (browser navigation quirk). Three safety nets:

- **`autocomplete="new-password"`** on the SMTP password input (matches what `admin_password` already had); defangs Chrome's autofill/stage-save interference on `type=password`.
- **"Review before saving" panel** on step 6 listing every captured field. Passwords / API keys / tokens render as `set (N chars)` or `(blank)` so the user can spot a missing field before submit.
- **Backend cross-field validation:** if `smtp_user` is set, `smtp_password` must be too. Returns `400` immediately instead of letting the wizard succeed with an unauthenticatable SMTP config.

Audited end-to-end via TestClient: all 20 wizard fields round-trip correctly when present in the payload (including `admin_password` getting bcrypted and never persisted in plain).

---

<a id="v203"></a>
## v2.0.3 — notification dedupe

Carried over from v1: `_already_notified_quality_wait` and `_already_notified_coming_soon` only counted notifications with `sent == True`. A still-pending notification (waiting on its `send_after` delay) didn't dedupe, so each `quality_monitor` cycle queued another row for the same request. Visible in the admin Notifications tab as duplicate "Waiting for HD-1080p: <title>" pending entries.

Fix: include `sent == False` in the dedupe predicate. Sent rows still get the per-type cooldown window (7 days for `quality_waiting`, 30 for `coming_soon`).

---

<a id="v202"></a>
## v2.0.2 — upcoming-episodes multi-Sonarr fix

Carried over from v1: with more than one Sonarr instance configured (e.g. main + anime), `/admin/upcoming-episodes` only built `series_map` from the **last** instance in the loop. Calendar episodes from any other instance had their `seriesId` looked up against the wrong map, didn't match, and got silently dropped before the user-request join even ran. Users with split Sonarrs were missing all upcoming episodes from whichever instance loaded second-to-last.

Fix: per-instance `series_maps`, tag each calendar episode with `_instance_idx` at fetch time, look up against the matching map during the join.

---

<a id="v201"></a>
## v2.0.1 — settings reload + docker.sock GID + admin/config typo

Three small fixes uncovered during v2.0.0's first production cutover:

- **In-place settings reload after `/admin/config` save.** v2.0.0 persisted to `/data/config.json` correctly but read back from the in-memory singleton, which only refreshed at process boot — so the UI looked like saves were no-ops. New `app.config.reload_from_disk()` reconstructs a fresh `Settings` and copies its fields into the module-level singleton. Most fields apply immediately; engine-level state (DB URL, in-flight HTTP clients in workers) still needs a restart and the response message says so.
- **`docker.sock` permission for the Logs tab.** The container runs as uid 1000 which isn't in the host's docker group, so `/var/run/docker.sock` came back as "Permission denied" with the bind mount alone. Compose files now set `group_add: "${DOCKER_GID:-docker}"`; export `DOCKER_GID=$(stat -c '%g' /var/run/docker.sock)` before `up -d`.
- **`/admin/config` GET threw `NameError: mask_secret`.** One surviving call site after the v2 rename to `_mask_secret`. Caused "Failed to load auth settings" warnings; the auth section of the response fell back to defaults.

Plus the wizard's `<form novalidate>` fix from earlier on `main`: a malformed `admin_email` field in step 1 was silently killing submit on step 6 because the browser couldn't focus the invalid input to show its tooltip (it was in a `display: none` section).

---

<a id="v200"></a>
## v2.0.0 — single-container, SQLite, wizard-driven

The v2 ground-up rebuild. Same notification engine; redesigned deploy and storage.

### What changed vs v1.5.x

- **One container, no Postgres.** SQLite at `/data/bingealert.db` with WAL mode + `foreign_keys=ON` + `synchronous=NORMAL` applied per connection. Image runs as non-root uid 1000.
- **First-run wizard.** Six-step web form at `/setup` — SMTP, Jellyseerr/Overseerr, Sonarr (+ optional anime), Radarr, Plex, Auth. Writes `/data/config.json` and restarts the container automatically. No `.env` editing needed for the basic install.
- **Auth required by default.** bcrypt password + HMAC session cookie, bypassable for clients matching `local_network_cidrs` (default home-LAN ranges). Optional Cloudflare Turnstile.
- **Single consolidated alembic baseline.** v1's eight migrations collapsed into `0001_baseline`. `alembic check` confirms model/migration parity.
- **`scripts/migrate_from_v1.py`** copies prod Postgres data into a fresh SQLite verbatim, with row-count parity check and `sqlite_sequence` reset.
- **Admin dashboard's Settings tab writes through to `config.json`** (rebuilt from the v1 `.env`-writer).
- **Logs tab uses the Python `docker` SDK.** Reading `/var/run/docker.sock` directly; Docker CLI was removed from the image (~70 MB lighter).
- **CI build-check workflow.** Every push and PR runs the Dockerfile build, no GHCR push.

### Carried forward unchanged

Smart episode batching, Plex availability check, quality/release monitoring, import failure auto-fix, issue auto-fix, stuck download detection, anime routing, shared requests, maintenance windows, weekly summary, reconciliation worker, PWA assets.

### Known limitations at ship (all fixed in 2.0.x patches)

- Settings page didn't reload the in-memory singleton → fixed in **2.0.1**
- Logs tab returned 503 due to docker socket perms → fixed in **2.0.1** (GID handling) and **2.0.7** (workflow perms)

---

## Migrating from v1.5.x

See [README → Migrating from v1](README.md#migrating-from-v1) for the full runbook. Short version:

1. `pg_dump` your live Postgres as a safety net.
2. Stop v1 (`docker compose down` from the v1 dir).
3. Bring up v2 in a fresh dir, walk the wizard, then stop it again.
4. `rm ./data/bingealert.db`
5. Run `scripts/migrate_from_v1.py --postgres "<source>" --sqlite ./data/bingealert.db` (one-shot container, needs `psycopg2-binary`).
6. `docker compose up -d`. Webhook URLs (`/webhooks/jellyseerr`, `/sonarr`, `/radarr`) are unchanged so Sonarr/Radarr/Seerr need no upstream reconfiguration.

---

## Upgrade procedure

For in-place upgrades along the v2 line:

```bash
cd /path/to/your/bingealert
sed -i -E 's|bingealert:2\.0\.[0-9]+|bingealert:2.0.11|' docker-compose.yml
docker compose pull
docker compose up -d --force-recreate
```

If you're upgrading from v2.0.0 directly to v2.0.11 (skipping intermediate patches), also export the docker group GID **before the first restart** so the Logs tab works:

```bash
export DOCKER_GID=$(stat -c '%g' /var/run/docker.sock)
```

After the upgrade, the dashboard footer should read `© 2026 BingeAlert v2.0.11`.
