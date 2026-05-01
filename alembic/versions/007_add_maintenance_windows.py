"""Add maintenance_windows table for scheduled maintenance notifications

Revision ID: 007
Revises: 006
Create Date: 2026-02-28 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from datetime import datetime


# revision identifiers, used by Alembic.
revision = '007'
down_revision = '006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    
    if 'maintenance_windows' not in inspector.get_table_names():
        op.create_table(
            'maintenance_windows',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('title', sa.String(), nullable=False),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('start_time', sa.DateTime(), nullable=False),
            sa.Column('end_time', sa.DateTime(), nullable=False),
            sa.Column('announcement_sent', sa.Boolean(), server_default='false'),
            sa.Column('reminder_sent', sa.Boolean(), server_default='false'),
            sa.Column('completion_sent', sa.Boolean(), server_default='false'),
            sa.Column('cancelled', sa.Boolean(), server_default='false'),
            sa.Column('status', sa.String(), nullable=False, server_default='scheduled'),
            sa.Column('created_at', sa.DateTime(), nullable=True, default=datetime.utcnow),
            sa.Column('updated_at', sa.DateTime(), nullable=True, default=datetime.utcnow),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(op.f('ix_maintenance_windows_id'), 'maintenance_windows', ['id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_maintenance_windows_id'), table_name='maintenance_windows')
    op.drop_table('maintenance_windows')
