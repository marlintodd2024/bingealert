# BingeAlert v2.2.3 — fix DB connection leak in approval-time quality check

A patch release that fixes a SQLAlchemy connection leak in the post-approval quality-check background task. Over time the leak fully exhausts the connection pool, after which webhook handling, the notification processor, the maintenance worker, and admin endpoints all stall and emit `QueuePool limit of size 5 overflow 10 reached, connection timed out, timeout 30.00`.

In-place upgrade, no migration, no compose change. **Restart the container** after upgrading — leaked connections held by the old process are only released on restart.

---

## Symptoms before the fix

After the app had been running for a while (typically once enough request approvals had accumulated), users would see:

- Admin dashboard "just spinning" — stat cards stuck on `—`, Users tab stuck on "Loading users…"
- 504s from the reverse proxy
- Notification processor unable to send (notifications pile up, then ship in oversized batches once a connection finally frees — e.g. a single email containing 48 episodes)
- Sonarr / Radarr / maintenance-worker logs filled with `sqlalchemy.exc.TimeoutError: QueuePool limit ... reached`
- Starlette `RuntimeError: No response returned.` — a downstream symptom of handlers being cancelled mid-query while waiting on the pool

---

## Root cause

`check_request_quality_status` in [app/routers/webhooks.py:640](app/routers/webhooks.py#L640) is fired as a background task for every newly approved Jellyseerr request. It opened a session with `next(get_db())` but the matching `db.close()` was at the end of the happy path, **outside** any `try/finally`:

```python
db = next(get_db())                    # <-- connection acquired
request = db.query(...).first()
if not request:
    return                              # <-- LEAK: returns without close
monitor = QualityReleaseMonitor()
if request.media_type == 'tv':
    await monitor._check_tv_show(...)   # <-- raises => LEAK
elif request.media_type == 'movie':
    await monitor._check_movie(...)     # <-- raises => LEAK
db.close()                              # <-- only reached on success
```

Three leak paths: the early `return` when the request row was missing, and any exception from the Sonarr- or Radarr-touching `_check_tv_show` / `_check_movie` calls. Each leak permanently retired one connection from the pool. With `pool_size=5 + max_overflow=10 = 15`, ~15 such events during a process's lifetime were enough to fully brick the app.

The other three `next(get_db())` callsites in `webhooks.py` (`_send_admin_issue_notification`, `_auto_fix_issue`, `_check_issue_resolution`) and all background workers were already wrapped correctly in `try/finally` — only this one was broken.

---

## What changed

### `check_request_quality_status` in `app/routers/webhooks.py:640`

Body wrapped in `try/finally` so `db.close()` runs on every exit path — early return, exception, or success:

```python
db = next(get_db())
try:
    request = db.query(MediaRequest).filter(MediaRequest.id == request_id).first()
    if not request:
        logger.warning(f"Request {request_id} not found for quality check")
        return
    monitor = QualityReleaseMonitor()
    if request.media_type == 'tv':
        await monitor._check_tv_show(request, db)
    elif request.media_type == 'movie':
        await monitor._check_movie(request, db)
    logger.info(f"Completed immediate quality check for request {request_id}")
finally:
    db.close()
```

That's the entirety of the runtime change.

---

## Upgrade

In-place, no migration:

```bash
cd /path/to/your/bingealert
sed -i -E 's|bingealert:2\.[0-9]+\.[0-9]+|bingealert:2.2.3|' docker-compose.yml
docker compose pull
docker compose up -d --force-recreate
```

The `--force-recreate` is important on this release: leaked connections from the running v2.2.2 process are only released by restarting the container.

---

## What didn't change

- No schema, no migration, no settings.
- No webhook contract change — Jellyseerr / Sonarr / Radarr / Plex integrations are byte-for-byte identical.
- The N+1 in `/api/admin/upcoming-episodes` was investigated during diagnosis but is **not** fixed in this release; at the default 7-day window it runs in ~80ms even with 144 TV requests, so it was deferred to keep the patch minimal.
