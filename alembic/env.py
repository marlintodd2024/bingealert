"""Alembic environment for BingeAlert v2 (SQLite).

The database URL comes from app.config.settings.database_url, which resolves
to sqlite:///{data_dir}/{sqlite_filename}. data_dir defaults to /data and can
be overridden by the DATA_DIR env var (handy for running migrations locally).

We import app.database (not just Base) so the engine-level @event.listens_for
hook for PRAGMAs is registered before alembic opens its own connection.
"""
import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# Ensure the project root is on sys.path so `from app...` works regardless of
# where alembic is invoked from.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import app.database  # noqa: F401, E402 -- registers PRAGMA event listener
from app.config import settings  # noqa: E402
from app.database import Base  # noqa: E402


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Allow a full DATABASE_URL override (e.g. for one-off ops); otherwise use settings.
database_url = os.getenv("DATABASE_URL") or settings.database_url
config.set_main_option("sqlalchemy.url", database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        # SQLite needs batch mode for ALTER TABLE -- safe on other dialects too.
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
