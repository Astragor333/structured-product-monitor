"""PostgreSQL connection and transaction helpers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg import Connection

from src.config import DatabaseConfig, get_database_config


class DatabaseUnavailableError(RuntimeError):
    """Raised when PostgreSQL cannot be reached with the configured settings."""


def create_connection(config: DatabaseConfig | None = None) -> Connection:
    """Create a PostgreSQL connection using validated application settings."""

    database_config = config or get_database_config()
    try:
        return psycopg.connect(**database_config.as_connection_kwargs())
    except psycopg.OperationalError as exc:
        raise DatabaseUnavailableError(
            "Could not connect to PostgreSQL. Check that the service is running "
            "and that the .env database settings are correct."
        ) from exc


@contextmanager
def database_connection(
    config: DatabaseConfig | None = None,
) -> Iterator[Connection]:
    """Provide a connection with commit, rollback, and close handled safely."""

    connection = create_connection(config)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
