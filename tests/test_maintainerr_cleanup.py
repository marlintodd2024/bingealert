import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.background.quality_monitor import QualityReleaseMonitor
from app.config import settings
from app.database import Base, EpisodeTracking, MediaRequest, Notification, User, get_db
from app.routers.webhooks import router
from app.services.email_service import EmailService
from app.services.maintainerr_service import (
    extract_request_keys,
    is_media_handled_notification,
)


class MaintainerrPayloadTests(unittest.TestCase):
    def test_extracts_current_stringified_media_items(self):
        payload = {
            "notification_type": "MEDIA_HANDLED",
            "mediaItems": json.dumps(
                [
                    {
                        "type": "movie",
                        "providerIds": {"tmdb": ["101"], "tvdb": []},
                    },
                    {
                        "type": "show",
                        "providerIds": {"tmdb": [202], "tvdb": [303]},
                    },
                ]
            ),
        }

        self.assertEqual(
            extract_request_keys(payload), {("movie", 101), ("tv", 202)}
        )

    def test_ignores_child_tv_scopes_and_invalid_ids(self):
        payload = {
            "mediaItems": [
                {"type": "season", "providerIds": {"tmdb": [202]}},
                {"type": "episode", "providerIds": {"tmdb": [303]}},
                {"type": "movie", "providerIds": {"tmdb": ["bad", 0]}},
            ]
        }

        self.assertEqual(extract_request_keys(payload), set())

    def test_accepts_maintainerr_handled_type_variants(self):
        for value in (16, "16", "MEDIA_HANDLED", "Media Handled"):
            with self.subTest(value=value):
                self.assertTrue(is_media_handled_notification(value))
        self.assertFalse(is_media_handled_notification("MEDIA_ABOUT_TO_BE_HANDLED"))


class MaintainerrWebhookTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            future=True,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True)

        db = self.Session()
        user = User(jellyseerr_id=1, email="viewer@example.com", username="viewer")
        db.add(user)
        db.flush()
        media_request = MediaRequest(
            user_id=user.id,
            jellyseerr_request_id=11,
            media_type="movie",
            tmdb_id=101,
            title="Example Movie",
            status="approved",
        )
        db.add(media_request)
        db.flush()
        db.add(
            Notification(
                user_id=user.id,
                request_id=media_request.id,
                notification_type="quality_waiting",
                subject="Waiting",
                body="Waiting",
                sent=False,
            )
        )
        db.commit()
        db.close()

        app = FastAPI()
        app.include_router(router, prefix="/webhooks")

        def override_get_db():
            session = self.Session()
            try:
                yield session
            finally:
                session.close()

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)
        self.previous_secret = settings.webhook_secret
        self.previous_allowlist = settings.webhook_allowed_ips
        self.previous_quality_monitor_enabled = settings.quality_monitor_enabled
        settings.webhook_secret = "test-secret"
        settings.webhook_allowed_ips = ""
        settings.quality_monitor_enabled = False

    def tearDown(self):
        self.client.close()
        settings.webhook_secret = self.previous_secret
        settings.webhook_allowed_ips = self.previous_allowlist
        settings.quality_monitor_enabled = self.previous_quality_monitor_enabled
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_handled_event_suppresses_and_grab_reactivates(self):
        response = self.client.post(
            "/webhooks/maintainerr",
            headers={"Authorization": "Bearer test-secret"},
            json={
                "notification_type": "MEDIA_HANDLED",
                "mediaItems": json.dumps(
                    [{"type": "movie", "providerIds": {"tmdb": ["101"]}}]
                ),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["processed_items"], 1)

        db = self.Session()
        media_request = db.query(MediaRequest).one()
        self.assertIsNotNone(media_request.quality_monitor_suppressed_at)
        self.assertEqual(media_request.quality_monitor_suppression_source, "maintainerr")
        self.assertEqual(db.query(Notification).count(), 0)
        db.close()

        response = self.client.post(
            "/webhooks/radarr",
            headers={"X-BingeAlert-Webhook-Secret": "test-secret"},
            json={
                "eventType": "Grab",
                "movie": {"id": 5, "title": "Example Movie", "tmdbId": 101},
            },
        )
        self.assertEqual(response.status_code, 200)

        db = self.Session()
        media_request = db.query(MediaRequest).one()
        self.assertIsNone(media_request.quality_monitor_suppressed_at)
        self.assertIsNone(media_request.quality_monitor_suppression_source)
        db.close()

    def test_new_seerr_request_for_cleaned_title_gets_fresh_lifecycle(self):
        db = self.Session()
        old_request = db.query(MediaRequest).one()
        old_request.quality_monitor_suppressed_at = datetime.utcnow()
        old_request.quality_monitor_suppression_source = "maintainerr"
        db.commit()
        db.close()

        response = self.client.post(
            "/webhooks/jellyseerr",
            headers={"Authorization": "Bearer test-secret"},
            json={
                "notification_type": "MEDIA_APPROVED",
                "subject": "New Request for Example Movie",
                "media": {"media_type": "movie", "tmdbId": 101},
                "request": {
                    "request_id": 12,
                    "requestedBy_email": "viewer@example.com",
                    "requestedBy_username": "viewer",
                },
                "extra": [],
            },
        )
        self.assertEqual(response.status_code, 200)

        db = self.Session()
        requests = db.query(MediaRequest).order_by(MediaRequest.id).all()
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0].jellyseerr_request_id, 11)
        self.assertIsNotNone(requests[0].quality_monitor_suppressed_at)
        self.assertEqual(requests[1].jellyseerr_request_id, 12)
        self.assertIsNone(requests[1].quality_monitor_suppressed_at)
        self.assertEqual(requests[1].status, "approved")
        db.close()


class _FakeSonarr:
    instance_name = "Sonarr"

    async def get_all_series(self):
        return [{"id": 7, "tmdbId": 202, "status": "continuing", "monitored": True}]

    async def get_episodes_by_series(self, series_id):
        return [
            {
                "seriesId": series_id,
                "seasonNumber": 1,
                "episodeNumber": 1,
                "hasFile": False,
                "monitored": True,
                "airDateUtc": (datetime.utcnow() - timedelta(days=30)).isoformat() + "Z",
            }
        ]


class _FakeRadarr:
    async def get_movies(self):
        return [
            {
                "id": 8,
                "tmdbId": 101,
                "status": "released",
                "hasFile": False,
                "monitored": False,
            }
        ]


class QualityMonitorCleanupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", poolclass=StaticPool, future=True)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True)

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    async def test_delivered_tv_episode_is_not_reopened_after_cleanup(self):
        db = self.Session()
        user = User(jellyseerr_id=2, email="tv@example.com", username="tv")
        db.add(user)
        db.flush()
        media_request = MediaRequest(
            user_id=user.id,
            jellyseerr_request_id=22,
            media_type="tv",
            tmdb_id=202,
            title="Example Show",
            status="approved",
        )
        db.add(media_request)
        db.flush()
        db.add(
            EpisodeTracking(
                request_id=media_request.id,
                series_id=7,
                season_number=1,
                episode_number=1,
                notified=True,
                available_in_plex=True,
            )
        )
        db.commit()

        monitor = QualityReleaseMonitor.__new__(QualityReleaseMonitor)
        monitor.sonarr_instances = [_FakeSonarr()]
        monitor.sonarr = monitor.sonarr_instances[0]
        await monitor._check_tv_show(media_request, db)

        self.assertEqual(db.query(Notification).count(), 0)
        db.close()

    async def test_unmonitored_movie_does_not_wait_for_quality(self):
        db = self.Session()
        user = User(jellyseerr_id=3, email="movie@example.com", username="movie")
        db.add(user)
        db.flush()
        media_request = MediaRequest(
            user_id=user.id,
            jellyseerr_request_id=33,
            media_type="movie",
            tmdb_id=101,
            title="Example Movie",
            status="approved",
        )
        db.add(media_request)
        db.commit()

        monitor = QualityReleaseMonitor.__new__(QualityReleaseMonitor)
        monitor.radarr = _FakeRadarr()
        await monitor._check_movie(media_request, db)

        self.assertEqual(db.query(Notification).count(), 0)
        db.close()

    async def test_processor_rechecks_suppression_before_smtp_send(self):
        db = self.Session()
        user = User(jellyseerr_id=4, email="race@example.com", username="race")
        db.add(user)
        db.flush()
        media_request = MediaRequest(
            user_id=user.id,
            jellyseerr_request_id=44,
            media_type="movie",
            tmdb_id=404,
            title="Race Movie",
            status="approved",
            quality_monitor_suppressed_at=datetime.utcnow(),
            quality_monitor_suppression_source="maintainerr",
        )
        db.add(media_request)
        db.flush()
        notification = Notification(
            user_id=user.id,
            request_id=media_request.id,
            notification_type="quality_waiting",
            subject="Waiting",
            body="Waiting",
            sent=False,
            send_after=datetime.utcnow() - timedelta(minutes=1),
        )
        db.add(notification)
        db.commit()

        service = EmailService.__new__(EmailService)
        service.send_email = AsyncMock(return_value=True)
        with patch(
            "app.services.sonarr_service.get_all_sonarr_instances", return_value=[]
        ):
            await service.process_pending_notifications(db)

        service.send_email.assert_not_awaited()
        db.refresh(notification)
        self.assertTrue(notification.sent)
        self.assertEqual(
            notification.error_message, "Skipped — media intentionally retired"
        )
        db.close()
