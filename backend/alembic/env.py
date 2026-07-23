"""Alembic environment.

The URL comes from app.core.config, NOT from alembic.ini. Two reasons:

  1. alembic.ini is committed. A real connection string in it is a leaked
     credential; a placeholder in it is a second source of truth that will
     drift from .env.
  2. Migrations must run against exactly the database the app talks to. Two
     config paths eventually means two different databases.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Importing the package registers every model on Base.metadata. Without it,
# autogenerate compares an EMPTY metadata against the live database and
# cheerfully emits a migration that drops every table.
import app.models  # noqa: F401
from app.core.config import get_settings
from app.core.database import Base

config = context.config

# Escape % so ConfigParser does not treat it as interpolation syntax — Neon
# connection strings are URL-encoded and can legitimately contain one.
config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
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
            # Autogenerate ignores type changes by default, so widening a
            # column from String(32) to String(64) would produce an empty
            # migration and the schema would silently drift.
            compare_type=True,
            # Same blind spot for server defaults.
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
