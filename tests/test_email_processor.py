from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch


_TEST_DATA_DIR = tempfile.mkdtemp(prefix="bingealert-email-tests-")
os.environ["DATA_DIR"] = _TEST_DATA_DIR

from app.database import (  # noqa: E402
    Base,
    MediaRequest,
    Notification,
    NotificationDeliveryLog,
    SessionLocal,
    User,
    engine,
)
from app.services.email_service import EmailService  # noqa: E402


class EmailProcessorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)

    def _make_user_and_request(self) -> tuple[int, int]:
        db = SessionLocal()
        try:
            user = User(
                jellyseerr_id=100,
                email="viewer@example.com",
                username="viewer",
            )
            db.add(user)
            db.commit()
            db.refresh(user)

            request = MediaRequest(
                user_id=user.id,
                jellyseerr_request_id=200,
                media_type="tv",
                tmdb_id=300,
                title="Release Candidate",
                status="approved",
            )
            db.add(request)
            db.commit()
            db.refresh(request)
            return user.id, request.id
        finally:
            db.close()

    async def test_row_failure_does_not_block_remaining_notifications(self) -> None:
        user_id, request_id = self._make_user_and_request()
        ready_at = datetime.utcnow() - timedelta(minutes=1)
        db = SessionLocal()
        try:
            db.add_all([
                Notification(
                    user_id=user_id,
                    request_id=request_id,
                    notification_type="episode",
                    subject="New Episode: First Series S01E01",
                    body="first",
                    series_id=101,
                    send_after=ready_at,
                ),
                Notification(
                    user_id=user_id,
                    request_id=request_id,
                    notification_type="episode",
                    subject="New Episode: Second Series S01E01",
                    body="second",
                    series_id=202,
                    send_after=ready_at,
                ),
            ])
            db.commit()

            service = EmailService()
            service.send_email = AsyncMock(side_effect=[RuntimeError("SMTP exploded"), True])
            with (
                patch("app.services.sonarr_service.get_all_sonarr_instances", return_value=[]),
                self.assertLogs("app.services.email_service", level="ERROR") as logs,
            ):
                await service.process_pending_notifications(db)

            self.assertTrue(any("Failed processing TV notification" in line for line in logs.output))
            self.assertEqual(service.send_email.await_count, 2)
            rows = db.query(Notification).order_by(Notification.id).all()
            self.assertFalse(rows[0].sent)
            self.assertIn("Processing failed: SMTP exploded", rows[0].error_message)
            self.assertTrue(rows[1].sent)
            self.assertEqual(db.query(NotificationDeliveryLog).count(), 1)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
