"""v2 baseline schema (consolidates v1.5.x migrations 001-008).

Revision ID: 0001_baseline
Revises:
Create Date: 2026-05-04

The shape here matches the post-008 production Postgres schema exactly so that
scripts/migrate_from_v1.py can copy rows verbatim. Cleanup work flagged in the
v2 turnover (notifications.status enum, system_config retirement, UTC-aware
timestamps) lives in *future* migrations, not this one -- this revision is the
data compatibility contract with v1.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # system_config: key/value store for app-managed runtime state.
    # (User-facing config lives in /data/config.json in v2.)
    op.create_table(
        "system_config",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", sa.String(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_system_config_key", "system_config", ["key"], unique=True)

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("jellyseerr_id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("plex_id", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("deactivated_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_id", "users", ["id"], unique=False)
    op.create_index("ix_users_jellyseerr_id", "users", ["jellyseerr_id"], unique=True)

    op.create_table(
        "media_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("jellyseerr_request_id", sa.Integer(), nullable=False),
        sa.Column("media_type", sa.String(), nullable=False),
        sa.Column("tmdb_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("season_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_media_requests_id", "media_requests", ["id"], unique=False)
    op.create_index(
        "ix_media_requests_jellyseerr_request_id",
        "media_requests",
        ["jellyseerr_request_id"],
        unique=True,
    )

    op.create_table(
        "shared_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("added_at", sa.DateTime(), nullable=True),
        sa.Column("added_by", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["request_id"], ["media_requests.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["added_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id", "user_id", name="_request_user_uc"),
    )
    op.create_index("ix_shared_requests_id", "shared_requests", ["id"], unique=False)

    op.create_table(
        "episode_tracking",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.Integer(), nullable=False),
        sa.Column("series_id", sa.Integer(), nullable=False),
        sa.Column("season_number", sa.Integer(), nullable=False),
        sa.Column("episode_number", sa.Integer(), nullable=False),
        sa.Column("episode_title", sa.String(), nullable=True),
        sa.Column("air_date", sa.DateTime(), nullable=True),
        sa.Column("notified", sa.Boolean(), nullable=True),
        sa.Column("available_in_plex", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["request_id"], ["media_requests.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "request_id",
            "series_id",
            "season_number",
            "episode_number",
            name="_request_series_season_episode_uc",
        ),
    )
    op.create_index("ix_episode_tracking_id", "episode_tracking", ["id"], unique=False)

    op.create_table(
        "reported_issues",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("seerr_issue_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("request_id", sa.Integer(), nullable=True),
        sa.Column("media_type", sa.String(), nullable=False),
        sa.Column("tmdb_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("issue_type", sa.String(), nullable=True),
        sa.Column("issue_message", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="reported"),
        sa.Column("action_taken", sa.String(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["request_id"], ["media_requests.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reported_issues_id", "reported_issues", ["id"], unique=False)
    op.create_index(
        "ix_reported_issues_seerr_issue_id",
        "reported_issues",
        ["seerr_issue_id"],
        unique=False,
    )

    op.create_table(
        "maintenance_windows",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("start_time", sa.DateTime(), nullable=False),
        sa.Column("end_time", sa.DateTime(), nullable=False),
        sa.Column("announcement_sent", sa.Boolean(), server_default=sa.false()),
        sa.Column("reminder_sent", sa.Boolean(), server_default=sa.false()),
        sa.Column("completion_sent", sa.Boolean(), server_default=sa.false()),
        sa.Column("cancelled", sa.Boolean(), server_default=sa.false()),
        sa.Column("status", sa.String(), nullable=False, server_default="scheduled"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_maintenance_windows_id", "maintenance_windows", ["id"], unique=False
    )

    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.Integer(), nullable=False),
        sa.Column("notification_type", sa.String(), nullable=False),
        sa.Column("subject", sa.String(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("sent", sa.Boolean(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("send_after", sa.DateTime(), nullable=True),
        sa.Column("series_id", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["request_id"], ["media_requests.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_notifications_id", "notifications", ["id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_notifications_id", table_name="notifications")
    op.drop_table("notifications")

    op.drop_index("ix_maintenance_windows_id", table_name="maintenance_windows")
    op.drop_table("maintenance_windows")

    op.drop_index("ix_reported_issues_seerr_issue_id", table_name="reported_issues")
    op.drop_index("ix_reported_issues_id", table_name="reported_issues")
    op.drop_table("reported_issues")

    op.drop_index("ix_episode_tracking_id", table_name="episode_tracking")
    op.drop_table("episode_tracking")

    op.drop_index("ix_shared_requests_id", table_name="shared_requests")
    op.drop_table("shared_requests")

    op.drop_index("ix_media_requests_jellyseerr_request_id", table_name="media_requests")
    op.drop_index("ix_media_requests_id", table_name="media_requests")
    op.drop_table("media_requests")

    op.drop_index("ix_users_jellyseerr_id", table_name="users")
    op.drop_index("ix_users_id", table_name="users")
    op.drop_table("users")

    op.drop_index("ix_system_config_key", table_name="system_config")
    op.drop_table("system_config")
