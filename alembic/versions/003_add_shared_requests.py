"""Add shared_requests table

Revision ID: 003
Revises: 002
Create Date: 2026-02-04 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from datetime import datetime


# revision identifiers, used by Alembic.
revision = '003'
down_revision = '002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Check if table already exists
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    
    if 'shared_requests' not in inspector.get_table_names():
        # Create shared_requests table
        op.create_table(
            'shared_requests',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('request_id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('added_at', sa.DateTime(), nullable=True, default=datetime.utcnow),
            sa.Column('added_by', sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(['request_id'], ['media_requests.id'], ),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
            sa.ForeignKeyConstraint(['added_by'], ['users.id'], ),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('request_id', 'user_id', name='_request_user_uc')
        )
        op.create_index(op.f('ix_shared_requests_id'), 'shared_requests', ['id'], unique=False)


def downgrade() -> None:
    # Drop shared_requests table
    op.drop_index(op.f('ix_shared_requests_id'), table_name='shared_requests')
    op.drop_table('shared_requests')
