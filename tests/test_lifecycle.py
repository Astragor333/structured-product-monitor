"""PostgreSQL-backed tests for general lifecycle rules."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta

import pytest
from psycopg import Connection

from src.db import create_connection
from src.events import Event
from src.lifecycle import (
    EXPIRED_BUT_ACTIVE,
    MATURITY_WITHIN_7_DAYS,
    find_expired_but_active_events,
    find_maturity_within_7_days_events,
    run_lifecycle,
)


AS_OF_DATE = date(2026, 8, 23)


@pytest.fixture
def lifecycle_connection() -> Iterator[Connection]:
    """Use a session-local product table so demo data remains unchanged."""

    connection = create_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TEMP TABLE products (
                    isin VARCHAR(20) PRIMARY KEY,
                    maturity_date DATE NOT NULL,
                    status VARCHAR(20) NOT NULL
                )
                """
            )
        yield connection
    finally:
        connection.rollback()
        connection.close()


def insert_product(
    connection: Connection,
    isin: str,
    maturity_date: date,
    status: str = "ACTIVE",
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO products (isin, maturity_date, status)
            VALUES (%s, %s, %s)
            """,
            (isin, maturity_date, status),
        )


def test_active_product_before_as_of_date_creates_expired_event(
    lifecycle_connection: Connection,
) -> None:
    maturity_date = AS_OF_DATE - timedelta(days=1)
    insert_product(lifecycle_connection, "TESTEXPIRED001", maturity_date)

    events = find_expired_but_active_events(
        lifecycle_connection,
        AS_OF_DATE,
    )

    assert events == [
        Event(
            isin="TESTEXPIRED001",
            event_type=EXPIRED_BUT_ACTIVE,
            severity="CRITICAL",
            event_date=maturity_date,
            description=(
                "Product TESTEXPIRED001 matured on 2026-08-22 "
                "but remains ACTIVE."
            ),
            details={
                "maturity_date": "2026-08-22",
                "internal_status": "ACTIVE",
                "as_of_date": "2026-08-23",
            },
        )
    ]


def test_maturity_exactly_on_as_of_date_is_not_expired(
    lifecycle_connection: Connection,
) -> None:
    insert_product(lifecycle_connection, "TESTTODAY001", AS_OF_DATE)

    events = run_lifecycle(lifecycle_connection, AS_OF_DATE)

    assert [event.event_type for event in events] == [MATURITY_WITHIN_7_DAYS]
    assert all(event.event_type != EXPIRED_BUT_ACTIVE for event in events)


def test_maturity_exactly_seven_days_ahead_creates_upcoming_event(
    lifecycle_connection: Connection,
) -> None:
    maturity_date = AS_OF_DATE + timedelta(days=7)
    insert_product(lifecycle_connection, "TESTUPCOMING001", maturity_date)

    events = find_maturity_within_7_days_events(
        lifecycle_connection,
        AS_OF_DATE,
    )

    assert events == [
        Event(
            isin="TESTUPCOMING001",
            event_type=MATURITY_WITHIN_7_DAYS,
            severity="INFO",
            event_date=maturity_date,
            description=(
                "Active product TESTUPCOMING001 matures on 2026-08-30, "
                "within 7 days of 2026-08-23."
            ),
            details={
                "maturity_date": "2026-08-30",
                "internal_status": "ACTIVE",
                "as_of_date": "2026-08-23",
            },
        )
    ]


def test_maturity_eight_days_ahead_creates_no_lifecycle_event(
    lifecycle_connection: Connection,
) -> None:
    insert_product(
        lifecycle_connection,
        "TESTLATER001",
        AS_OF_DATE + timedelta(days=8),
    )

    assert run_lifecycle(lifecycle_connection, AS_OF_DATE) == []


def test_expired_product_creates_only_expired_event(
    lifecycle_connection: Connection,
) -> None:
    insert_product(
        lifecycle_connection,
        "TESTEXPIRED002",
        AS_OF_DATE - timedelta(days=8),
    )

    events = run_lifecycle(lifecycle_connection, AS_OF_DATE)

    assert [event.event_type for event in events] == [EXPIRED_BUT_ACTIVE]
    assert all(
        event.event_type != MATURITY_WITHIN_7_DAYS
        for event in events
    )
