from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch


_TEST_DATA_DIR = tempfile.mkdtemp(prefix="bingealert-tests-")
os.environ["DATA_DIR"] = _TEST_DATA_DIR

from app.background.digest_worker import _admin_digest_due  # noqa: E402
from app.background.ops_maintenance import purge_webhook_events  # noqa: E402
from app.background.quality_monitor import QualityReleaseMonitor  # noqa: E402
from app.background.system_health import _send_alert_channels  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import (  # noqa: E402
    Base,
    EpisodeTracking,
    MediaRequest,
    Notification,
    NotificationDeliveryLog,
    SessionLocal,
    SystemConfig,
    User,
    WebhookEventLog,
    engine,
)
from app.routers.webhooks import _sanitize_payload  # noqa: E402
from app.services.digest_service import (  # noqa: E402
    next_digest_at,
    season_is_complete,
    send_due_user_digests,
    should_defer_to_user_batch,
)
from app.services.email_service import EmailService  # noqa: E402
from app.services.notification_history import mark_notification_delivered  # noqa: E402


class FakeEmailService:
    def __init__(self, success: bool = True):
        self.success = success
        self.sent: list[dict] = []

    async def send_email(self, **kwargs) -> bool:
        self.sent.append(kwargs)
        return self.success

    def render_coming_soon_notification(self, **kwargs) -> str:
        return "coming soon"


class FakeSonarr:
    def __init__(self, episodes: list[dict]):
        self.episodes = episodes

    async def get_episodes_by_series(self, series_id: int) -> list[dict]:
        return self.episodes


class FakeFailingSonarr:
    async def get_episodes_by_series(self, series_id: int) -> list[dict]:
        raise RuntimeError("Sonarr unavailable")


class FakeTmdb:
    async def get_tv_poster(self, tmdb_id: int) -> None:
        return None

    async def get_movie_poster(self, tmdb_id: int) -> None:
        return None


class DatabaseTestCase(unittest.TestCase):
    def setUp(self) -> None:
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)

    def make_user(self, *, mode: str = "instant", full_season: bool = False) -> int:
        db = SessionLocal()
        try:
            user = User(
                jellyseerr_id=100,
                email="viewer@example.com",
                username="viewer",
                notification_mode=mode,
                notify_full_season_only=full_season,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            return user.id
        finally:
            db.close()

    def make_request(self, user_id: int, *, media_type: str = "movie") -> int:
        db = SessionLocal()
        try:
            request = MediaRequest(
                user_id=user_id,
                jellyseerr_request_id=200,
                media_type=media_type,
                tmdb_id=300,
                title="Release Candidate",
                status="approved",
            )
            db.add(request)
            db.commit()
            db.refresh(request)
            return request.id
        finally:
            db.close()


class DigestPreferenceTests(DatabaseTestCase, unittest.IsolatedAsyncioTestCase):
    def test_next_digest_is_first_daily_slot_after_creation(self) -> None:
        before = datetime(2026, 7, 9, 8, 30)
        after = datetime(2026, 7, 9, 10, 30)
        self.assertEqual(next_digest_at(before, 9), datetime(2026, 7, 9, 9, 0))
        self.assertEqual(next_digest_at(after, 9), datetime(2026, 7, 10, 9, 0))

    def test_digest_and_full_season_rows_are_owned_by_batch_worker(self) -> None:
        digest_user_id = self.make_user(mode="digest")
        request_id = self.make_request(digest_user_id)
        db = SessionLocal()
        try:
            movie = Notification(
                user_id=digest_user_id,
                request_id=request_id,
                notification_type="movie",
                subject="Movie Available: Release Candidate",
                body="body",
            )
            db.add(movie)
            db.commit()
            db.refresh(movie)
            self.assertTrue(should_defer_to_user_batch(movie))
        finally:
            db.close()

    def test_season_completion_requires_every_monitored_episode_file(self) -> None:
        incomplete = [
            {"seasonNumber": 1, "episodeNumber": 1, "monitored": True, "hasFile": True},
            {"seasonNumber": 1, "episodeNumber": 2, "monitored": True, "hasFile": False},
            {"seasonNumber": 1, "episodeNumber": 3, "monitored": False, "hasFile": False},
        ]
        self.assertFalse(season_is_complete(incomplete, 1))
        incomplete[1]["hasFile"] = True
        self.assertTrue(season_is_complete(incomplete, 1))

    async def test_due_digest_marks_movie_delivered_once(self) -> None:
        user_id = self.make_user(mode="digest")
        request_id = self.make_request(user_id)
        now = datetime(2026, 7, 9, 12, 0)
        db = SessionLocal()
        try:
            db.add(
                Notification(
                    user_id=user_id,
                    request_id=request_id,
                    notification_type="movie",
                    subject="Movie Available: Release Candidate",
                    body="body",
                    created_at=now - timedelta(days=1),
                    send_after=now - timedelta(hours=1),
                )
            )
            db.commit()
        finally:
            db.close()

        email = FakeEmailService()
        result = await send_due_user_digests(
            now=now,
            email_service=email,
            sonarr_instances=[],
        )
        self.assertEqual(result["users_sent"], 1)
        self.assertEqual(result["notifications_sent"], 1)
        self.assertEqual(len(email.sent), 1)

        db = SessionLocal()
        try:
            notification = db.query(Notification).one()
            request = db.query(MediaRequest).one()
            self.assertTrue(notification.sent)
            self.assertEqual(notification.sent_at, now)
            self.assertEqual(request.status, "available")
            self.assertEqual(db.query(NotificationDeliveryLog).count(), 1)
            mark_notification_delivered(db, notification, sent_at=now)
            db.commit()
            self.assertEqual(db.query(NotificationDeliveryLog).count(), 1)
        finally:
            db.close()

    async def test_regular_processor_hands_digest_rows_off_without_sending(self) -> None:
        user_id = self.make_user(mode="digest")
        request_id = self.make_request(user_id)
        db = SessionLocal()
        try:
            db.add(
                Notification(
                    user_id=user_id,
                    request_id=request_id,
                    notification_type="movie",
                    subject="Movie Available: Release Candidate",
                    body="body",
                    send_after=datetime.utcnow() - timedelta(minutes=1),
                )
            )
            db.commit()
            service = EmailService()
            service.send_email = AsyncMock(return_value=True)
            await service.process_pending_notifications(db)
            service.send_email.assert_not_awaited()
            self.assertFalse(db.query(Notification).one().sent)
        finally:
            db.close()

    async def test_regular_processor_sends_instant_movie_notification(self) -> None:
        user_id = self.make_user(mode="instant")
        request_id = self.make_request(user_id)
        db = SessionLocal()
        try:
            db.add(
                Notification(
                    user_id=user_id,
                    request_id=request_id,
                    notification_type="movie",
                    subject="Movie Available: Release Candidate",
                    body="body",
                    send_after=datetime.utcnow() - timedelta(minutes=1),
                )
            )
            db.commit()
            service = EmailService()
            service.send_email = AsyncMock(return_value=True)
            await service.process_pending_notifications(db)
            service.send_email.assert_awaited_once()
            self.assertTrue(db.query(Notification).one().sent)
            self.assertEqual(db.query(NotificationDeliveryLog).count(), 1)
        finally:
            db.close()

    async def test_full_season_waits_then_marks_episode_tracking(self) -> None:
        user_id = self.make_user(full_season=True)
        request_id = self.make_request(user_id, media_type="tv")
        now = datetime(2026, 7, 9, 12, 0)
        db = SessionLocal()
        try:
            db.add(
                EpisodeTracking(
                    request_id=request_id,
                    series_id=7,
                    season_number=1,
                    episode_number=1,
                    episode_title="Pilot",
                )
            )
            db.add(
                Notification(
                    user_id=user_id,
                    request_id=request_id,
                    notification_type="episode",
                    subject="New Episode: Release Candidate S01E01",
                    body="S01E01",
                    series_id=7,
                    created_at=now - timedelta(hours=2),
                    send_after=now - timedelta(hours=1),
                )
            )
            db.commit()
        finally:
            db.close()

        email = FakeEmailService()
        incomplete = FakeSonarr([
            {"seasonNumber": 1, "episodeNumber": 1, "monitored": True, "hasFile": True},
            {"seasonNumber": 1, "episodeNumber": 2, "monitored": True, "hasFile": False},
        ])
        held = await send_due_user_digests(
            now=now,
            force=True,
            email_service=email,
            sonarr_instances=[incomplete],
        )
        self.assertEqual(held["notifications_held"], 1)
        self.assertEqual(len(email.sent), 0)

        complete = FakeSonarr([
            {"seasonNumber": 1, "episodeNumber": 1, "monitored": True, "hasFile": True},
            {"seasonNumber": 1, "episodeNumber": 2, "monitored": True, "hasFile": True},
        ])
        sent = await send_due_user_digests(
            now=now,
            force=True,
            email_service=email,
            sonarr_instances=[complete],
        )
        self.assertEqual(sent["notifications_sent"], 1)
        db = SessionLocal()
        try:
            tracking = db.query(EpisodeTracking).one()
            self.assertTrue(tracking.notified)
            self.assertTrue(tracking.available_in_plex)
        finally:
            db.close()

    async def test_sonarr_outage_holds_season_without_blocking_other_digest_rows(self) -> None:
        user_id = self.make_user(mode="digest", full_season=True)
        request_id = self.make_request(user_id, media_type="tv")
        now = datetime(2026, 7, 9, 12, 0)
        db = SessionLocal()
        try:
            db.add_all([
                Notification(
                    user_id=user_id,
                    request_id=request_id,
                    notification_type="episode",
                    subject="New Episode: Release Candidate S01E01",
                    body="S01E01",
                    series_id=7,
                    created_at=now - timedelta(days=1),
                    send_after=now - timedelta(hours=1),
                ),
                Notification(
                    user_id=user_id,
                    request_id=request_id,
                    notification_type="coming_soon",
                    subject="Coming Soon: Release Candidate",
                    body="Coming soon",
                    created_at=now - timedelta(days=1),
                    send_after=now - timedelta(hours=1),
                ),
            ])
            db.commit()
        finally:
            db.close()

        email = FakeEmailService()
        result = await send_due_user_digests(
            now=now,
            email_service=email,
            sonarr_instances=[FakeFailingSonarr()],
        )
        self.assertEqual(result["notifications_sent"], 1)
        self.assertEqual(result["notifications_held"], 1)
        self.assertEqual(len(email.sent), 1)

        db = SessionLocal()
        try:
            rows = {
                row.notification_type: row.sent
                for row in db.query(Notification).all()
            }
            self.assertFalse(rows["episode"])
            self.assertTrue(rows["coming_soon"])
        finally:
            db.close()


class OperationsSafetyTests(DatabaseTestCase, unittest.IsolatedAsyncioTestCase):
    async def test_smtp_health_alert_never_calls_email_transport(self) -> None:
        smtp_row = {"service_key": "smtp", "service_type": "smtp", "service_name": "SMTP"}
        with (
            patch("app.background.system_health._send_service_alert", new_callable=AsyncMock) as email,
            patch("app.background.system_health._send_webhook_alert", new_callable=AsyncMock) as webhook,
            patch.object(settings, "service_health_email_alerts_enabled", True),
            patch.object(settings, "alert_webhook_enabled", False),
        ):
            await _send_alert_channels("outage", smtp_row)
            email.assert_not_awaited()
            webhook.assert_not_awaited()

    def test_webhook_payload_is_sanitized_recursively(self) -> None:
        payload = {
            "title": "Example",
            "apiKey": "top-secret",
            "nested": {"Authorization": "Bearer secret", "value": "kept"},
        }
        sanitized = _sanitize_payload(payload)
        self.assertEqual(sanitized["title"], "Example")
        self.assertEqual(sanitized["apiKey"], "[redacted]")
        self.assertEqual(sanitized["nested"]["Authorization"], "[redacted]")
        self.assertEqual(sanitized["nested"]["value"], "kept")

    def test_webhook_retention_keeps_recent_events(self) -> None:
        now = datetime.utcnow()
        db = SessionLocal()
        try:
            db.add_all([
                WebhookEventLog(
                    source_service="sonarr",
                    event_type="Download",
                    status="success",
                    created_at=now - timedelta(days=31),
                ),
                WebhookEventLog(
                    source_service="radarr",
                    event_type="Download",
                    status="success",
                    created_at=now - timedelta(days=2),
                ),
            ])
            db.commit()
            self.assertEqual(purge_webhook_events(db, 30), 1)
            db.commit()
            self.assertEqual(db.query(WebhookEventLog).count(), 1)
        finally:
            db.close()

    async def test_quality_preference_suppresses_quality_waiting_queue_row(self) -> None:
        user_id = self.make_user()
        request_id = self.make_request(user_id)
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.id == user_id).one()
            user.notify_quality_upgrades = False
            db.commit()
            request = db.query(MediaRequest).filter(MediaRequest.id == request_id).one()
            monitor = QualityReleaseMonitor.__new__(QualityReleaseMonitor)
            await monitor._send_quality_waiting_notification(request, "HD-1080p", db)
            self.assertEqual(db.query(Notification).count(), 0)
        finally:
            db.close()

    async def test_coming_soon_is_queued_instead_of_bypassing_preferences(self) -> None:
        user_id = self.make_user(mode="digest")
        request_id = self.make_request(user_id)
        db = SessionLocal()
        try:
            request = db.query(MediaRequest).filter(MediaRequest.id == request_id).one()
            monitor = QualityReleaseMonitor.__new__(QualityReleaseMonitor)
            monitor.tmdb = FakeTmdb()
            monitor.email_service = FakeEmailService()
            await monitor._send_coming_soon_notification(
                request,
                "2026-08-01T00:00:00Z",
                db,
            )
            notification = db.query(Notification).one()
            self.assertFalse(notification.sent)
            self.assertEqual(notification.notification_type, "coming_soon")
            self.assertEqual(len(monitor.email_service.sent), 0)
        finally:
            db.close()

    def test_admin_digest_scheduler_claims_only_once_per_day(self) -> None:
        with (
            patch.object(settings, "admin_daily_digest_enabled", True),
            patch.object(settings, "admin_daily_digest_hour_utc", 9),
        ):
            self.assertFalse(_admin_digest_due(datetime(2026, 7, 9, 8, 59)))
            self.assertTrue(_admin_digest_due(datetime(2026, 7, 9, 9, 0)))
            self.assertFalse(_admin_digest_due(datetime(2026, 7, 9, 12, 0)))
            self.assertTrue(_admin_digest_due(datetime(2026, 7, 10, 9, 0)))
        db = SessionLocal()
        try:
            self.assertEqual(db.query(SystemConfig).filter(
                SystemConfig.key == "admin_daily_digest_last_attempt"
            ).count(), 1)
        finally:
            db.close()


class MigrationUpgradeTests(unittest.TestCase):
    def test_v235_schema_upgrades_through_v3_head(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="bingealert-migration-") as data_dir:
            env = os.environ.copy()
            env["DATA_DIR"] = data_dir
            subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", "0005_notification_delivery_log"],
                cwd=repo_root,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            db_path = Path(data_dir) / "bingealert.db"
            with sqlite3.connect(db_path) as connection:
                connection.execute(
                    """
                    INSERT INTO users
                        (jellyseerr_id, email, username, is_active, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (501, "upgrade@example.com", "upgrade", 1, "2026-07-09", "2026-07-09"),
                )
                connection.commit()

            subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                cwd=repo_root,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            with sqlite3.connect(db_path) as connection:
                columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(users)").fetchall()
                }
                row = connection.execute(
                    "SELECT status_token, notification_mode, notify_full_season_only FROM users WHERE jellyseerr_id = 501"
                ).fetchone()
                tables = {
                    item[0]
                    for item in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                }
            self.assertIn("status_token", columns)
            self.assertIn("webhook_event_log", tables)
            self.assertTrue(row[0])
            self.assertEqual(row[1], "instant")
            self.assertEqual(row[2], 0)


if __name__ == "__main__":
    unittest.main()
