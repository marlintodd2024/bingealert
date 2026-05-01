"""Alembic environment configuration for BingeAlert.

Builds the database URL from environment variables (matching docker-compose.yml)
and imports the SQLAlchemy metadata from app.database for autogenerate support.
"""
import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# Make the project root importable so `from app.database import Base` works
# regardless of where alembic is invoked from.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import Base  # noqa: E402

# Alembic Config object, gives access to .ini values.
config = context.config

# Configure Python logging from alembic.ini.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Build the SQLAlchemy URL from the same env vars docker-compose injects.
# Defaults match docker-compose.yml so local dev "just works" too.
db_user = os.getenv("DB_USER", "notifyuser")
db_password = os.getenv("DB_PASSWORD", "")
db_host = os.getenv("DB_HOST", "bingealert-db")
db_port = os.getenv("DB_PORT", "5432")
db_name = os.getenv("DB_NAME", "notifications")

# Allow a full DATABASE_URL override if someone sets one explicitly.
database_url = os.getenv("DATABASE_URL") or (
    f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
)
config.set_main_option("sqlalchemy.url", database_url)

# Target metadata for autogenerate.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emits SQL, no DB connection)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (real DB connection)."""
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
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
