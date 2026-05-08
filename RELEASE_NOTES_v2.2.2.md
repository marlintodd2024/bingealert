# BingeAlert v2.2.2 — SHA-256 for calendar UIDs (CodeQL #21)

A patch release that swaps the SHA-1 fingerprint used to build per-event iCalendar `UID`s in v2.2.0's calendar feed for SHA-256, clearing CodeQL alert #21 ("Use of a broken or weak cryptographic hashing algorithm on sensitive data" — HIGH).

In-place upgrade, no migration, no compose change.

---

## What changed

### `_build_uid()` in `app/routers/calendar.py:105`

Was:

```python
digest = hashlib.sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
```

Now:

```python
digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
```

Same shape (16 hex chars), same purpose (stable opaque fingerprint of `{user_id}:{series_id}:S##E##`), zero security boundary either way — the input is already unique by structure, no secret material is involved. The `usedforsecurity=False` hint was meant to say "this is fine"; CodeQL's weak-hash rule flags SHA-1 on string data either way, so SHA-256 keeps the rule satisfied without changing anything observable.

### Side effect: calendar UIDs change for anyone subscribed during v2.2.0/v2.2.1

If a user already subscribed to their `.ics` feed during the brief v2.2.0 / v2.2.1 window, their calendar app will see one cycle of "old SHA-1 UIDs disappear, new SHA-256 UIDs appear" on the next poll. Calendar apps reconcile feed contents by UID per RFC 5545, so the practical visible effect is at most a flicker as the same events re-render under new identifiers. No duplicates persist past the next poll.

If you only just deployed v2.2.0 and nobody has subscribed yet, this is a no-op.

---

## Upgrade

In-place, no migration:

```bash
cd /path/to/your/bingealert
sed -i -E 's|bingealert:2\.[0-9]+\.[0-9]+|bingealert:2.2.2|' docker-compose.yml
docker compose pull
docker compose up -d --force-recreate
```

---

## CodeQL state after this patch

| | v2.2.1 | v2.2.2 |
|---|---|---|
| Open HIGH alerts | 1 (#21) | 0 |

---

## What didn't change

- Calendar feed format, fields, time window, cache headers, status logic — all identical to v2.2.0/v2.2.1.
- Email footer — unchanged.
- No migration, no schema, no settings.
