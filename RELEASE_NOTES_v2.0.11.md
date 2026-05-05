# BingeAlert v2.0.11

The post-launch stabilization for the v2.0.0 single-container rebuild. v2.0.11 itself is a one-line bugfix; this note also rolls up everything that's landed since v2.0.0 went live so anyone upgrading from the cutover image catches up in a single pull.

---

## What's in v2.0.11 specifically

**Fix:** Internal Server Error when adding a shared user to a request (Upcoming Episodes panel, request detail share dialog).

`app/routers/admin.py` had two `@router.post("/requests/{request_id}/share")` decorators. The second one constructed `SharedRequest(shared_at=...)` against a column the model never had (`added_at` is the real one); whenever FastAPI's route resolver picked the buggy handler, SQLAlchemy threw on the unknown kwarg and the broad except returned 500. Removed the duplicate — the canonical handler reads `user_id` as a query param, matching what `admin.html` actually sends.

---

## Highlights since v2.0.0

If you've been running v2.0.0 since cutover, here's what you actually get from this upgrade:

- **`/admin/config` Save button works.** v2.0.0 wrote settings to disk but read them from a stale in-memory singleton, so the UI looked like nothing happened. Saves now reload the singleton in place — most fields apply without a container restart.
- **Admin Logs tab works.** v2's image dropped the Docker CLI; the tab now uses the Python `docker` SDK against the bind-mounted `/var/run/docker.sock`. Compose templates set `group_add: "${DOCKER_GID}"` so the non-root container user can read the socket.
- **Upcoming Episodes shows everything.** With more than one Sonarr instance configured, the panel was silently dropping calendar episodes from all-but-the-last instance because `series_map` was only loaded once. Now per-instance, with each calendar episode tagged by source.
- **No more duplicate "Waiting for HD-1080p" rows.** Quality monitor's dedupe predicate ignored still-pending notifications; every cycle queued another. Now sent + pending both block re-creation.
- **Setup wizard captures every field reliably.** Adds an explicit "Review before saving" panel on step 6, marks `smtp_password` as `autocomplete="new-password"`, and rejects `smtp_user`-without-`smtp_password` server-side so a browser-blanked password field can't slip a half-config into `config.json`.
- **Admin email is editable from the dashboard.** The wizard collected it; the Settings page now has the matching input under Sender Information.
- **Logout actually logs you out.** Cookie deletion now matches the cookie-set attributes (HttpOnly / Path / SameSite). Logout button hides when the client is on a local-network CIDR, since "logout" there is a no-op.
- **Footer shows the running version.** Single source of truth in `app/__init__.py`; FastAPI app version + `/api/version` derive from it. Footer also drops the dead Discussions link and points at the actual `.yml` issue forms.
- **Login page footer no longer sits under the sign-in card.** Tucked into the bottom-right corner.
- **Three CodeQL findings cleared.** Review panel now uses `createElement`/`textContent`, validation regex is global, build-check workflow declares minimum permissions.
- **Three Dependabot alerts cleared.** `python-multipart 0.0.27`, `python-dotenv 1.2.2`.
- **Repo cleanup.** Dropped four obsolete v1 helper scripts and the misleading compose override example.

---

## Patch log

| Tag | Summary |
|-----|---------|
| **v2.0.11** | Fix 500 on add-user-to-request (duplicate route + wrong column name) |
| v2.0.10 | Footer version + drop dead Discussions link + fix `.md` → `.yml` issue templates + reposition login footer |
| v2.0.9 | Logout reliably clears the session cookie; hide button on local-network bypass |
| v2.0.8 | Drop obsolete v1 helper scripts (`setup.sh`, `update.sh`, `rebuild-data.sh`, override example) |
| v2.0.7 | CodeQL fixes — review panel XSS hardening, global asterisk regex, build-check workflow permissions |
| v2.0.6 | Admin Settings UI exposes `admin_email` |
| v2.0.5 | CVE bumps — `python-multipart 0.0.27`, `python-dotenv 1.2.2` |
| v2.0.4 | Wizard safeguards — review summary, `autocomplete="new-password"` on SMTP, cross-field validation |
| v2.0.3 | Dedupe pending `quality_waiting` / `coming_soon` notifications |
| v2.0.2 | Upcoming Episodes correctness for multi-Sonarr installs |
| v2.0.1 | In-place settings reload, `docker.sock` GID handling, `mask_secret` typo |
| v2.0.0 | Initial v2 release — single-container, SQLite, wizard-driven |

---

## Upgrade

In-place; no data migration:

```bash
cd /path/to/your/bingealert
sed -i -E 's|bingealert:2\.0\.[0-9]+|bingealert:2.0.11|' docker-compose.yml
docker compose pull
docker compose up -d --force-recreate
```

If you're upgrading from v2.0.0 directly (no intermediate versions), also export the docker group GID before the first restart so the Logs tab works:

```bash
export DOCKER_GID=$(stat -c '%g' /var/run/docker.sock)
```

After it's running, the dashboard footer should read `© 2026 BingeAlert v2.0.11`.

---

## Migrating from v1.5.x

See [README.md](README.md#migrating-from-v1) — the runbook hasn't changed since v2.0.0. `scripts/migrate_from_v1.py` continues to work against your existing Postgres dump.
