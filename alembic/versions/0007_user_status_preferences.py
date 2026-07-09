"""Add user status portal and preferences.

Revision ID: 0007_user_status_preferences
Revises: 0006_webhook_event_log
Create Date: 2026-07-09
"""
import secrets

from alembic import op
import sqlalchemy as sa


revision = "0007_user_status_preferences"
down_revision = "0006_webhook_event_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("status_token", sa.String(), nullable=True))
    op.add_column("users", sa.Column("notification_mode", sa.String(), nullable=False, server_default="instant"))
    op.add_column("users", sa.Column("quiet_hours_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("quiet_hours_start", sa.String(), nullable=False, server_default="22:00"))
    op.add_column("users", sa.Column("quiet_hours_end", sa.String(), nullable=False, server_default="07:00"))
    op.add_column("users", sa.Column("notify_full_season_only", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("notify_quality_upgrades", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("users", sa.Column("preferred_channel", sa.String(), nullable=False, server_default="email"))

    users = sa.table("users", sa.column("id", sa.Integer), sa.column("status_token", sa.String))
    connection = op.get_bind()
    user_ids = [row[0] for row in connection.execute(sa.select(users.c.id)).fetchall()]
    for user_id in user_ids:
        connection.execute(
            users.update()
            .where(users.c.id == user_id)
            .values(status_token=secrets.token_urlsafe(32))
        )

    op.create_index("ix_users_status_token", "users", ["status_token"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_status_token", table_name="users")
    op.drop_column("users", "preferred_channel")
    op.drop_column("users", "notify_quality_upgrades")
    op.drop_column("users", "notify_full_season_only")
    op.drop_column("users", "quiet_hours_end")
    op.drop_column("users", "quiet_hours_start")
    op.drop_column("users", "quiet_hours_enabled")
    op.drop_column("users", "notification_mode")
    op.drop_column("users", "status_token")
