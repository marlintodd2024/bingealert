"""Daily admin and user digest scheduler."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from app.config import settings
from app.database import SessionLocal, SystemConfig
from app.services.digest_service import send_due_user_digests


logger = logging.getLogger(__name__)
_ADMIN_ATTEMPT_KEY = "admin_daily_digest_last_attempt"
_POLL_MINUTES = 5


def _admin_digest_due(now: datetime) -> bool:
    if not settings.admin_daily_digest_enabled:
        return False
    hour = max(0, min(23, int(settings.admin_daily_digest_hour_utc or 0)))
    if now.hour < hour:
        return False

    db = SessionLocal()
    try:
        row = db.query(SystemConfig).filter(SystemConfig.key == _ADMIN_ATTEMPT_KEY).first()
        if row and row.value:
            try:
                if datetime.fromisoformat(row.value).date() >= now.date():
                    return False
            except ValueError:
                pass
        if row:
            row.value = now.isoformat()
            row.updated_at = now
        else:
            db.add(SystemConfig(key=_ADMIN_ATTEMPT_KEY, value=now.isoformat()))
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


async def run_digest_cycle(now: datetime | None = None) -> dict[str, object]:
    from app.background.system_health import (
        record_worker_failure,
        record_worker_started,
        record_worker_success,
    )

    current = now or datetime.utcnow()
    next_run_at = current + timedelta(minutes=_POLL_MINUTES)
    started_at = record_worker_started(
        "digest_delivery",
        "Digest delivery",
        next_run_at=next_run_at,
    )
    try:
        user_result = await send_due_user_digests(now=current)
        admin_result: dict[str, object] | None = None
        if _admin_digest_due(current):
            from app.services.reporting import send_admin_report

            admin_result = await send_admin_report("daily", days=1)

        failures = int(user_result.get("users_failed") or 0)
        if admin_result is not None and not admin_result.get("sent"):
            failures += 1
        if failures:
            raise RuntimeError(f"Digest delivery completed with {failures} failed recipient(s)")

        record_worker_success(
            "digest_delivery",
            "Digest delivery",
            started_at=started_at,
            next_run_at=next_run_at,
        )
        return {"users": user_result, "admin": admin_result}
    except asyncio.CancelledError:
        raise
    except Exception as error:
        record_worker_failure(
            "digest_delivery",
            "Digest delivery",
            error,
            started_at=started_at,
            next_run_at=next_run_at,
        )
        raise


async def digest_delivery_worker() -> None:
    from app.background.utils import is_maintenance_active

    logger.info("Digest delivery worker started")
    while True:
        try:
            if not is_maintenance_active():
                await run_digest_cycle()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error("digest delivery worker error: %s", error)
        await asyncio.sleep(_POLL_MINUTES * 60)
