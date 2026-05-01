"""Add send_after to notifications

Revision ID: 004
Revises: 003
Create Date: 2026-02-04 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '004'
down_revision = '003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add send_after column to notifications table
    op.add_column('notifications', sa.Column('send_after', sa.DateTime(), nullable=True))


def downgrade() -> None:
    # Remove send_after column
    op.drop_column('notifications', 'send_after')
