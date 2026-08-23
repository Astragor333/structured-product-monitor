"""PostgreSQL-backed tests for exchange-listing reconciliation."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import pytest
from psycopg import Connection

from src.db import create_connection
from src.events import Event, save_events
from src.reconciliation import (
    MISSING_EXCHANGE_LISTING,
    MISSING_EXCHANGE_LISTING_SQL,
    find_missing_exchange_listing_events,
)


AS_OF_DATE = date(2026, 8, 23)


@pytest.fixture
def reconciliation_connection() -> Iterator[Connection]:
    """Use session-local tables so tests cannot change the demo database."""

    connection = create_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TEMP TABLE products (
                    isin VARCHAR(20) PRIMARY KEY,
                    status VARCHAR(20) NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TEMP TABLE exchange_listings (
                    isin VARCHAR(20) NOT NULL,
                    exchange VARCHAR(50) NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TEMP TABLE events (
                    event_id BIGSERIAL PRIMARY KEY,
                    isin VARCHAR(20),
                    event_type VARCHAR(50) NOT NULL,
                    severity VARCHAR(20) NOT NULL,
                    event_date DATE NOT NULL,
                    detected_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    description TEXT NOT NULL,
                    details JSONB
                )
                """
            )
            cursor.execute(
                """
                CREATE UNIQUE INDEX uq_test_events_identity
                ON events (COALESCE(isin, ''), event_type, event_date)
                """
            )
        yield connection
    finally:
        connection.rollback()
        connection.close()


def test_active_product_with_exchange_row_creates_no_event(
    reconciliation_connection: Connection,
) -> None:
    with reconciliation_connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO products (isin, status) VALUES (%s, %s)",
            ("TESTLISTED001", "ACTIVE"),
        )
        cursor.execute(
            "INSERT INTO exchange_listings (isin, exchange) VALUES (%s, %s)",
            ("TESTLISTED001", "Vienna"),
        )

    events = find_missing_exchange_listing_events(
        reconciliation_connection,
        AS_OF_DATE,
    )

    assert events == []


def test_active_product_without_exchange_row_creates_and_persists_event(
    reconciliation_connection: Connection,
) -> None:
    with reconciliation_connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO products (isin, status) VALUES (%s, %s)",
            ("TESTMISSING001", "ACTIVE"),
        )

    events = find_missing_exchange_listing_events(
        reconciliation_connection,
        AS_OF_DATE,
    )

    assert "LEFT JOIN exchange_listings" in MISSING_EXCHANGE_LISTING_SQL
    assert events == [
        Event(
            isin="TESTMISSING001",
            event_type=MISSING_EXCHANGE_LISTING,
            severity="CRITICAL",
            event_date=AS_OF_DATE,
            description="Active product TESTMISSING001 has no exchange listing.",
            details={"internal_status": "ACTIVE"},
        )
    ]

    inserted_count = save_events(reconciliation_connection, events)
    assert inserted_count == 1

    with reconciliation_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT isin, event_type, severity, event_date
            FROM events
            """
        )
        assert cursor.fetchone() == (
            "TESTMISSING001",
            MISSING_EXCHANGE_LISTING,
            "CRITICAL",
            AS_OF_DATE,
        )
