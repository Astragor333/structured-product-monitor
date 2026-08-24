"""PostgreSQL-backed tests for general lifecycle rules."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal

import pytest
from psycopg import Connection

from src.db import create_connection
from src.events import Event
from src.lifecycle import (
    BARRIER_BREACHED,
    EXPIRED_BUT_ACTIVE,
    MATURITY_WITHIN_7_DAYS,
    find_bonus_barrier_breach_events,
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
                    product_type VARCHAR(20) NOT NULL DEFAULT 'DISCOUNT',
                    underlying VARCHAR(30) NOT NULL DEFAULT 'TEST.UNDERLYING',
                    issue_date DATE NOT NULL DEFAULT DATE '2026-01-01',
                    maturity_date DATE NOT NULL,
                    barrier NUMERIC(18, 6),
                    status VARCHAR(20) NOT NULL
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


def insert_bonus_product(
    connection: Connection,
    isin: str,
    *,
    issue_date: date,
    maturity_date: date,
    barrier: Decimal | None,
    underlying: str = "TEST.UNDERLYING",
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO products (
                isin,
                product_type,
                underlying,
                issue_date,
                maturity_date,
                barrier,
                status
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                isin,
                "BONUS",
                underlying,
                issue_date,
                maturity_date,
                barrier,
                "ACTIVE",
            ),
        )


def insert_prices(
    connection: Connection,
    observations: list[tuple[date, Decimal]],
    *,
    underlying: str = "TEST.UNDERLYING",
) -> None:
    with connection.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO market_prices (underlying, price_date, price)
            VALUES (%s, %s, %s)
            """,
            [
                (underlying, price_date, price)
                for price_date, price in observations
            ],
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


def test_bonus_barrier_equality_creates_breach_event(
    lifecycle_connection: Connection,
) -> None:
    insert_bonus_product(
        lifecycle_connection,
        "TESTBONUS001",
        issue_date=date(2026, 8, 1),
        maturity_date=date(2027, 1, 1),
        barrier=Decimal("70"),
    )
    insert_prices(
        lifecycle_connection,
        [
            (date(2026, 8, 20), Decimal("100")),
            (date(2026, 8, 21), Decimal("90")),
            (date(2026, 8, 22), Decimal("70")),
            (date(2026, 8, 23), Decimal("85")),
        ],
    )

    events = find_bonus_barrier_breach_events(
        lifecycle_connection,
        AS_OF_DATE,
    )

    assert events == [
        Event(
            isin="TESTBONUS001",
            event_type=BARRIER_BREACHED,
            severity="CRITICAL",
            event_date=date(2026, 8, 22),
            description=(
                "Bonus product TESTBONUS001 first breached its barrier on "
                "2026-08-22."
            ),
            details={
                "underlying": "TEST.UNDERLYING",
                "barrier": 70.0,
                "breach_price": 70.0,
            },
        )
    ]


def test_bonus_prices_above_barrier_create_no_event(
    lifecycle_connection: Connection,
) -> None:
    insert_bonus_product(
        lifecycle_connection,
        "TESTBONUS002",
        issue_date=date(2026, 8, 1),
        maturity_date=date(2027, 1, 1),
        barrier=Decimal("70"),
    )
    insert_prices(
        lifecycle_connection,
        [
            (date(2026, 8, 20), Decimal("100")),
            (date(2026, 8, 21), Decimal("71")),
            (date(2026, 8, 22), Decimal("72")),
            (date(2026, 8, 23), Decimal("85")),
        ],
    )

    assert find_bonus_barrier_breach_events(
        lifecycle_connection,
        AS_OF_DATE,
    ) == []


def test_bonus_breach_followed_by_recovery_remains_breached(
    lifecycle_connection: Connection,
) -> None:
    insert_bonus_product(
        lifecycle_connection,
        "TESTBONUS003",
        issue_date=date(2026, 8, 1),
        maturity_date=date(2027, 1, 1),
        barrier=Decimal("70"),
    )
    insert_prices(
        lifecycle_connection,
        [
            (date(2026, 8, 20), Decimal("100")),
            (date(2026, 8, 21), Decimal("65")),
            (date(2026, 8, 22), Decimal("95")),
        ],
    )

    events = find_bonus_barrier_breach_events(
        lifecycle_connection,
        AS_OF_DATE,
    )

    assert len(events) == 1
    assert events[0].event_type == BARRIER_BREACHED
    assert events[0].event_date == date(2026, 8, 21)
    assert events[0].details["breach_price"] == 65.0


def test_bonus_price_before_issue_date_is_ignored(
    lifecycle_connection: Connection,
) -> None:
    insert_bonus_product(
        lifecycle_connection,
        "TESTBONUS004",
        issue_date=date(2026, 8, 20),
        maturity_date=date(2027, 1, 1),
        barrier=Decimal("70"),
    )
    insert_prices(
        lifecycle_connection,
        [
            (date(2026, 8, 19), Decimal("60")),
            (date(2026, 8, 20), Decimal("80")),
        ],
    )

    assert find_bonus_barrier_breach_events(
        lifecycle_connection,
        AS_OF_DATE,
    ) == []


def test_bonus_price_after_as_of_date_is_ignored(
    lifecycle_connection: Connection,
) -> None:
    insert_bonus_product(
        lifecycle_connection,
        "TESTBONUS005",
        issue_date=date(2026, 8, 1),
        maturity_date=date(2027, 1, 1),
        barrier=Decimal("70"),
    )
    insert_prices(
        lifecycle_connection,
        [
            (AS_OF_DATE, Decimal("80")),
            (AS_OF_DATE + timedelta(days=1), Decimal("60")),
        ],
    )

    assert find_bonus_barrier_breach_events(
        lifecycle_connection,
        AS_OF_DATE,
    ) == []


def test_bonus_without_barrier_is_skipped_safely(
    lifecycle_connection: Connection,
) -> None:
    insert_bonus_product(
        lifecycle_connection,
        "TESTBONUS006",
        issue_date=date(2026, 8, 1),
        maturity_date=date(2027, 1, 1),
        barrier=None,
    )
    insert_prices(
        lifecycle_connection,
        [(date(2026, 8, 20), Decimal("1"))],
    )

    assert find_bonus_barrier_breach_events(
        lifecycle_connection,
        AS_OF_DATE,
    ) == []


def test_bonus_multiple_breaches_use_first_breach_date(
    lifecycle_connection: Connection,
) -> None:
    insert_bonus_product(
        lifecycle_connection,
        "TESTBONUS007",
        issue_date=date(2026, 8, 1),
        maturity_date=date(2027, 1, 1),
        barrier=Decimal("70"),
    )
    insert_prices(
        lifecycle_connection,
        [
            (date(2026, 8, 20), Decimal("80")),
            (date(2026, 8, 21), Decimal("69")),
            (date(2026, 8, 22), Decimal("65")),
            (date(2026, 8, 23), Decimal("68")),
        ],
    )

    events = find_bonus_barrier_breach_events(
        lifecycle_connection,
        AS_OF_DATE,
    )

    assert len(events) == 1
    assert events[0].event_date == date(2026, 8, 21)
    assert events[0].details["breach_price"] == 69.0


def test_bonus_price_after_maturity_date_is_ignored(
    lifecycle_connection: Connection,
) -> None:
    insert_bonus_product(
        lifecycle_connection,
        "TESTBONUS008",
        issue_date=date(2026, 8, 1),
        maturity_date=date(2026, 8, 21),
        barrier=Decimal("70"),
    )
    insert_prices(
        lifecycle_connection,
        [
            (date(2026, 8, 21), Decimal("80")),
            (date(2026, 8, 22), Decimal("60")),
        ],
    )

    assert find_bonus_barrier_breach_events(
        lifecycle_connection,
        AS_OF_DATE,
    ) == []
