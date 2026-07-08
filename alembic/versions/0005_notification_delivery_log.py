"""Add durable notification delivery log.

Revision ID: 0005_notification_delivery_log
Revises: 0004_system_health
Create Date: 2026-07-08
"""
from alembic import op
import sqlalchemy as sa


revision = "0005_notification_delivery_log"
down_revision = "0004_system_health"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notification_delivery_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.Integer(), nullable=False),
        sa.Column("notification_type", sa.String(), nullable=False),
        sa.Column("dedupe_key", sa.String(), nullable=False),
        sa.Column("series_id", sa.Integer(), nullable=True),
        sa.Column("season_number", sa.Integer(), nullable=True),
        sa.Column("episode_number", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["request_id"], ["media_requests.id"]),
        sa.UniqueConstraint(
            "user_id",
            "request_id",
            "notification_type",
            "dedupe_key",
            name="_notification_delivery_uc",
        ),
    )
    op.create_index("ix_notification_delivery_log_user_id", "notification_delivery_log", ["user_id"])
    op.create_index("ix_notification_delivery_log_request_id", "notification_delivery_log", ["request_id"])
    op.create_index("ix_notification_delivery_log_notification_type", "notification_delivery_log", ["notification_type"])
    op.create_index("ix_notification_delivery_log_series_id", "notification_delivery_log", ["series_id"])


def downgrade() -> None:
    op.drop_index("ix_notification_delivery_log_series_id", table_name="notification_delivery_log")
    op.drop_index("ix_notification_delivery_log_notification_type", table_name="notification_delivery_log")
    op.drop_index("ix_notification_delivery_log_request_id", table_name="notification_delivery_log")
    op.drop_index("ix_notification_delivery_log_user_id", table_name="notification_delivery_log")
    op.drop_table("notification_delivery_log")
