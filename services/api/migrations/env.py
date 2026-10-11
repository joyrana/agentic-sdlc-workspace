from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

import agentic_sdlc.orm  # noqa: F401  (registers tables)
from agentic_sdlc.core.db import Base
from agentic_sdlc.core.settings import get_settings

config = context.config
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", get_settings().database_url)
target_metadata = Base.metadata

# Objects managed outside the ORM: LangGraph's checkpointer tables (created by PostgresSaver.setup())
# and indexes written by hand in migrations.
_EXTERNAL_TABLES = {"checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations"}
_MANUAL_INDEXES = {"ix_document_blocks_fts", "ix_workflow_jobs_claim"}


def include_object(obj, name, type_, reflected, compare_to):  # type: ignore[no-untyped-def]
    if type_ == "table" and name in _EXTERNAL_TABLES:
        return False
    return not (type_ == "index" and (name in _MANUAL_INDEXES or (name or "").startswith("checkpoint")))


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
