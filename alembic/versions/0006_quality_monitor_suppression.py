"""Add reversible quality-monitor suppression for intentional media cleanup.

Revision ID: 0006_quality_monitor_suppression
Revises: 0005_notification_delivery_log
Create Date: 2026-09-13
"""
from alembic import op
import sqlalchemy as sa


revision = "0006_quality_monitor_suppression"
down_revision = "0005_notification_delivery_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("media_requests") as batch_op:
        batch_op.add_column(
            sa.Column("quality_monitor_suppressed_at", sa.DateTime(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("quality_monitor_suppression_source", sa.String(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("media_requests") as batch_op:
        batch_op.drop_column("quality_monitor_suppression_source")
        batch_op.drop_column("quality_monitor_suppressed_at")
