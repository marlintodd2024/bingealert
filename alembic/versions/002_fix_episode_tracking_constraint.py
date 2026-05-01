"""Fix episode tracking unique constraint

Revision ID: 002
Revises: 001
Create Date: 2026-02-04 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '002'
down_revision = '001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop old constraint
    op.drop_constraint('_series_season_episode_uc', 'episode_tracking', type_='unique')
    
    # Add new constraint that includes request_id
    op.create_unique_constraint(
        '_request_series_season_episode_uc',
        'episode_tracking',
        ['request_id', 'series_id', 'season_number', 'episode_number']
    )


def downgrade() -> None:
    # Drop new constraint
    op.drop_constraint('_request_series_season_episode_uc', 'episode_tracking', type_='unique')
    
    # Restore old constraint
    op.create_unique_constraint(
        '_series_season_episode_uc',
        'episode_tracking',
        ['series_id', 'season_number', 'episode_number']
    )
