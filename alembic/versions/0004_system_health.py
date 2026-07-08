"""Add system health status tables.

Revision ID: 0004_system_health
Revises: 0003_issue_scope
Create Date: 2026-07-08
"""
from alembic import op
import sqlalchemy as sa


revision = "0004_system_health"
down_revision = "0003_issue_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "service_health_status",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("service_key", sa.String(), nullable=False),
        sa.Column("service_name", sa.String(), nullable=False),
        sa.Column("service_type", sa.String(), nullable=False),
        sa.Column("configured", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(), nullable=False, server_default="unknown"),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.Column("last_ok_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("alert_sent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_alert_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_service_health_status_service_key",
        "service_health_status",
        ["service_key"],
        unique=True,
    )

    op.create_table(
        "worker_health_status",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("worker_key", sa.String(), nullable=False),
        sa.Column("worker_name", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="unknown"),
        sa.Column("last_started_at", sa.DateTime(), nullable=True),
        sa.Column("last_finished_at", sa.DateTime(), nullable=True),
        sa.Column("last_success_at", sa.DateTime(), nullable=True),
        sa.Column("next_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_duration_ms", sa.Integer(), nullable=True),
        sa.Column("run_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_worker_health_status_worker_key",
        "worker_health_status",
        ["worker_key"],
        unique=True,
    )

    op.create_table(
        "service_health_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("service_key", sa.String(), nullable=False),
        sa.Column("service_name", sa.String(), nullable=False),
        sa.Column("service_type", sa.String(), nullable=False),
        sa.Column("configured", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("checked_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_service_health_events_service_key",
        "service_health_events",
        ["service_key"],
        unique=False,
    )
    op.create_index(
        "ix_service_health_events_checked_at",
        "service_health_events",
        ["checked_at"],
        unique=False,
    )

    op.create_table(
        "admin_activity_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="success"),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("actor", sa.String(), nullable=True),
        sa.Column("ip_address", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_admin_activity_log_action", "admin_activity_log", ["action"])
    op.create_index("ix_admin_activity_log_status", "admin_activity_log", ["status"])
    op.create_index("ix_admin_activity_log_created_at", "admin_activity_log", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_admin_activity_log_created_at", table_name="admin_activity_log")
    op.drop_index("ix_admin_activity_log_status", table_name="admin_activity_log")
    op.drop_index("ix_admin_activity_log_action", table_name="admin_activity_log")
    op.drop_table("admin_activity_log")
    op.drop_index("ix_service_health_events_checked_at", table_name="service_health_events")
    op.drop_index("ix_service_health_events_service_key", table_name="service_health_events")
    op.drop_table("service_health_events")
    op.drop_index("ix_worker_health_status_worker_key", table_name="worker_health_status")
    op.drop_table("worker_health_status")
    op.drop_index("ix_service_health_status_service_key", table_name="service_health_status")
    op.drop_table("service_health_status")
