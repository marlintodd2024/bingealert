"""Admin activity audit helpers."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.database import AdminActivityLog, SessionLocal


logger = logging.getLogger(__name__)


def record_admin_activity(
    action: str,
    message: str | None = None,
    *,
    status: str = "success",
    details: dict[str, Any] | None = None,
    actor: str | None = None,
    ip_address: str | None = None,
    db: Session | None = None,
) -> None:
    """Best-effort audit log write.

    If a request-scoped session is passed, the caller remains responsible for
    commit/rollback. Otherwise this helper opens and closes its own session.
    """
    owns_session = db is None
    session = db or SessionLocal()
    try:
        session.add(
            AdminActivityLog(
                action=action,
                status=status,
                message=message,
                details=json.dumps(details or {}, sort_keys=True),
                actor=actor,
                ip_address=ip_address,
                created_at=datetime.utcnow(),
            )
        )
        if owns_session:
            session.commit()
    except Exception:
        if owns_session:
            session.rollback()
        logger.debug("failed to record admin activity: %s", action, exc_info=True)
    finally:
        if owns_session:
            session.close()
