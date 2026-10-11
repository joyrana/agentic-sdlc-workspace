"""LangGraph Postgres checkpointer lifecycle."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg import Connection
from psycopg.rows import dict_row

from agentic_sdlc.core.db import psycopg_url

_setup_done = False


@contextmanager
def checkpointer() -> Iterator[PostgresSaver]:
    global _setup_done
    with Connection.connect(
        psycopg_url(), autocommit=True, prepare_threshold=0, row_factory=dict_row
    ) as conn:
        saver = PostgresSaver(conn)
        if not _setup_done:
            saver.setup()
            _setup_done = True
        yield saver
