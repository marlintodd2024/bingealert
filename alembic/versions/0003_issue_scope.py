"""Track season and episode scope on reported issues.

Revision ID: 0003_issue_scope
Revises: 0002_calendar_token
Create Date: 2026-05-20
"""
from alembic import op
import sqlalchemy as sa


revision = "0003_issue_scope"
down_revision = "0002_calendar_token"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("reported_issues", sa.Column("season_number", sa.Integer(), nullable=True))
    op.add_column("reported_issues", sa.Column("episode_number", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("reported_issues", "episode_number")
    op.drop_column("reported_issues", "season_number")
