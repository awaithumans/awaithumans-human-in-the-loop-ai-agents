"""Alembic environment.

Wires alembic to SQLModel metadata (same source as the app) and reads
the database URL from the app's Settings so there's one source of
truth. Synchronous — alembic itself is sync; we derive the sync URL
from the same config that feeds the async app engine.
"""

from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

from alembic import context
from awaithumans.server.core.config import settings

# Register every model so SQLModel.metadata sees them during autogenerate.
# The import is side-effect only.
from awaithumans.server.db import models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    # `disable_existing_loggers=False` is critical: the default (True)
    # marks every logger created before this call as disabled, which
    # silently drops any subsequent log records. Because alembic runs
    # inside the app's startup lifespan (init_db -> alembic upgrade),
    # leaving the default flips the app's own `awaithumans.server`
    # logger to disabled and every post-migration warning / info call
    # vanishes. That hid the ephemeral-SQLite warning in production
    # and broke caplog-based tests for the same code path. Keeping
    # alembic's own log config (which only configures the alembic /
    # sqlalchemy loggers) while leaving everything else alone is the
    # behavior we actually want.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Override sqlalchemy.url with the app's resolved sync URL so alembic
# and the app agree on which database to touch, regardless of how the
# operator configured it (DATABASE_URL, DB_PATH, etc.).
config.set_main_option("sqlalchemy.url", settings.database_url_sync)

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting. Used for `alembic upgrade --sql`."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # SQLite needs batch mode for ALTER TABLE
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Connect to the database and run migrations."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = config.get_main_option("sqlalchemy.url")
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # SQLite needs batch mode for ALTER TABLE
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
