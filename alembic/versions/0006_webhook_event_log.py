"""Add webhook event log.

Revision ID: 0006_webhook_event_log
Revises: 0005_notification_delivery_log
Create Date: 2026-07-09
"""
from alembic import op
import sqlalchemy as sa


revision = "0006_webhook_event_log"
down_revision = "0005_notification_delivery_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "webhook_event_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_service", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="received"),
        sa.Column("payload", sa.Text(), nullable=True),
        sa.Column("result_message", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("matched_request_ids", sa.Text(), nullable=True),
        sa.Column("matched_user_ids", sa.Text(), nullable=True),
        sa.Column("processed_items", sa.Integer(), nullable=True),
        sa.Column("replay_of_id", sa.Integer(), nullable=True),
        sa.Column("replayed_at", sa.DateTime(), nullable=True),
        sa.Column("client_ip", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_webhook_event_log_source_service", "webhook_event_log", ["source_service"])
    op.create_index("ix_webhook_event_log_event_type", "webhook_event_log", ["event_type"])
    op.create_index("ix_webhook_event_log_status", "webhook_event_log", ["status"])
    op.create_index("ix_webhook_event_log_replay_of_id", "webhook_event_log", ["replay_of_id"])
    op.create_index("ix_webhook_event_log_created_at", "webhook_event_log", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_webhook_event_log_created_at", table_name="webhook_event_log")
    op.drop_index("ix_webhook_event_log_replay_of_id", table_name="webhook_event_log")
    op.drop_index("ix_webhook_event_log_status", table_name="webhook_event_log")
    op.drop_index("ix_webhook_event_log_event_type", table_name="webhook_event_log")
    op.drop_index("ix_webhook_event_log_source_service", table_name="webhook_event_log")
    op.drop_table("webhook_event_log")
