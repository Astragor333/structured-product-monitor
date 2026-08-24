"""PostgreSQL-backed tests for idempotent event persistence."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, timedelta

import pytest
from psycopg import Connection

from src import monitor
from src.db import create_connection
from src.events import Event, save_events
from src.reconciliation import UNKNOWN_EXCHANGE_ISIN


AS_OF_DATE = date(2026, 8, 23)


@pytest.fixture
def event_connection() -> Iterator[Connection]:
    """Use session-local tables so persistence tests cannot alter demo data."""

    connection = create_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TEMP TABLE products (
                    isin VARCHAR(20) PRIMARY KEY,
                    product_type VARCHAR(20) NOT NULL,
                    underlying VARCHAR(30) NOT NULL,
                    currency VARCHAR(3) NOT NULL,
                    nominal NUMERIC(18, 4) NOT NULL,
                    issue_date DATE NOT NULL,
                    maturity_date DATE NOT NULL,
                    strike NUMERIC(18, 6),
                    barrier NUMERIC(18, 6),
                    bonus_level NUMERIC(18, 6),
                    cap NUMERIC(18, 6),
                    autocall_level NUMERIC(18, 6),
                    coupon NUMERIC(12, 8),
                    next_observation_date DATE,
                    status VARCHAR(20) NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TEMP TABLE exchange_listings (
                    isin VARCHAR(20) NOT NULL,
                    exchange VARCHAR(50) NOT NULL,
                    listing_status VARCHAR(20) NOT NULL,
                    listing_date DATE NOT NULL,
                    PRIMARY KEY (isin, exchange)
                )
                """
            )
            cursor.execute(
                """
                CREATE TEMP TABLE market_prices (
                    underlying VARCHAR(30) NOT NULL,
                    price_date DATE NOT NULL,
                    price NUMERIC(18, 6) NOT NULL,
                    PRIMARY KEY (underlying, price_date)
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


def count_events(connection: Connection) -> int:
    with connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM events")
        row = cursor.fetchone()
    assert row is not None
    return row[0]


def test_same_monitoring_run_twice_creates_no_duplicate_events(
    event_connection: Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with event_connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO exchange_listings (
                isin,
                exchange,
                listing_status,
                listing_date
            )
            VALUES (%s, %s, %s, %s)
            """,
            ("AT000SP999", "Vienna", "ACTIVE", date(2026, 1, 1)),
        )

    @contextmanager
    def use_test_connection() -> Iterator[Connection]:
        try:
            yield event_connection
            event_connection.commit()
        except Exception:
            event_connection.rollback()
            raise

    monkeypatch.setattr(monitor, "database_connection", use_test_connection)

    first_run_events = monitor.run_monitoring(AS_OF_DATE)
    first_count = count_events(event_connection)

    second_run_events = monitor.run_monitoring(AS_OF_DATE)
    second_count = count_events(event_connection)

    assert first_run_events == second_run_events
    assert [event.event_type for event in first_run_events] == [
        UNKNOWN_EXCHANGE_ISIN
    ]
    assert first_count == 1
    assert second_count == first_count


def test_same_event_identity_with_different_dates_is_allowed(
    event_connection: Connection,
) -> None:
    events = [
        Event(
            isin="TESTIDENTITY001",
            event_type="TEST_EVENT",
            severity="WARNING",
            event_date=AS_OF_DATE,
            description="First business date.",
        ),
        Event(
            isin="TESTIDENTITY001",
            event_type="TEST_EVENT",
            severity="WARNING",
            event_date=AS_OF_DATE + timedelta(days=1),
            description="Second business date.",
        ),
    ]

    assert save_events(event_connection, events) == 2
    assert count_events(event_connection) == 2


def test_different_event_types_for_same_isin_and_date_are_allowed(
    event_connection: Connection,
) -> None:
    events = [
        Event(
            isin="TESTIDENTITY002",
            event_type="FIRST_TEST_EVENT",
            severity="WARNING",
            event_date=AS_OF_DATE,
            description="First event type.",
        ),
        Event(
            isin="TESTIDENTITY002",
            event_type="SECOND_TEST_EVENT",
            severity="CRITICAL",
            event_date=AS_OF_DATE,
            description="Second event type.",
        ),
    ]

    assert save_events(event_connection, events) == 2
    assert count_events(event_connection) == 2
