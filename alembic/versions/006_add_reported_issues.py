"""Add reported_issues table for Seerr issue tracking

Revision ID: 006
Revises: 005
Create Date: 2026-02-26 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from datetime import datetime


# revision identifiers, used by Alembic.
revision = '006'
down_revision = '005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    
    if 'reported_issues' not in inspector.get_table_names():
        op.create_table(
            'reported_issues',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('seerr_issue_id', sa.Integer(), nullable=True),
            sa.Column('user_id', sa.Integer(), nullable=True),
            sa.Column('request_id', sa.Integer(), nullable=True),
            sa.Column('media_type', sa.String(), nullable=False),
            sa.Column('tmdb_id', sa.Integer(), nullable=False),
            sa.Column('title', sa.String(), nullable=False),
            sa.Column('issue_type', sa.String(), nullable=True),
            sa.Column('issue_message', sa.Text(), nullable=True),
            sa.Column('status', sa.String(), nullable=False, server_default='reported'),
            sa.Column('action_taken', sa.String(), nullable=True),
            sa.Column('resolved_at', sa.DateTime(), nullable=True),
            sa.Column('error_message', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True, default=datetime.utcnow),
            sa.Column('updated_at', sa.DateTime(), nullable=True, default=datetime.utcnow),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
            sa.ForeignKeyConstraint(['request_id'], ['media_requests.id'], ),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(op.f('ix_reported_issues_id'), 'reported_issues', ['id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_reported_issues_id'), table_name='reported_issues')
    op.drop_table('reported_issues')
