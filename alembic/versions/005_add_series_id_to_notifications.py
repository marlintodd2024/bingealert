"""Add series_id to notifications

Revision ID: 005
Revises: 004
Create Date: 2026-02-05 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '005'
down_revision = '004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add series_id column to notifications table
    op.add_column('notifications', sa.Column('series_id', sa.Integer(), nullable=True))


def downgrade() -> None:
    # Remove series_id column
    op.drop_column('notifications', 'series_id')
