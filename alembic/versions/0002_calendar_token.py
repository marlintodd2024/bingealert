"""Add users.calendar_token for the per-user .ics feed.

Revision ID: 0002_calendar_token
Revises: 0001_baseline
Create Date: 2026-05-07

Adds a nullable, unique TEXT column on users to hold a stable random secret
that authenticates the per-user calendar feed at GET /calendar/{token}.ics.
Backfill phase generates a random token for every existing user so they get
working calendar links the first time they receive a notification email.

The application also lazy-generates a token on first send if it's still
NULL (e.g. a user added between this migration and the next deploy), so
new accounts after this migration don't need a separate backfill pass.
"""
from alembic import op
import sqlalchemy as sa


revision = "0002_calendar_token"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("calendar_token", sa.String(), nullable=True),
    )
    op.create_index(
        "ix_users_calendar_token", "users", ["calendar_token"], unique=True
    )

    # Backfill existing users with a random token. Generated row-by-row in
    # Python rather than via a SQL function so the same code path covers
    # SQLite (no usable random hex helper of fixed length) and Postgres,
    # and so the migration script and the application share the same
    # secrets module / token width.
    import secrets

    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id FROM users WHERE calendar_token IS NULL")
    ).fetchall()
    for (user_id,) in rows:
        bind.execute(
            sa.text("UPDATE users SET calendar_token = :t WHERE id = :i"),
            {"t": secrets.token_urlsafe(24), "i": user_id},
        )


def downgrade() -> None:
    op.drop_index("ix_users_calendar_token", table_name="users")
    op.drop_column("users", "calendar_token")
