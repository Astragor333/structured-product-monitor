"""General structured-product lifecycle rules."""

from __future__ import annotations

from datetime import date, timedelta

from psycopg import Connection

from src.events import Event


EXPIRED_BUT_ACTIVE = "EXPIRED_BUT_ACTIVE"
MATURITY_WITHIN_7_DAYS = "MATURITY_WITHIN_7_DAYS"

# Rule A: internally active products whose maturity date has already passed.
# language=PostgreSQL
EXPIRED_BUT_ACTIVE_SQL = """
    SELECT
        isin,
        maturity_date
    FROM products
    WHERE maturity_date < %s
      AND status = %s
    ORDER BY isin
"""

# Rule B: internally active products maturing from today through seven days ahead.
# language=PostgreSQL
MATURITY_WITHIN_7_DAYS_SQL = """
    SELECT
        isin,
        maturity_date
    FROM products
    WHERE maturity_date BETWEEN %s AND %s
      AND status = %s
    ORDER BY isin
"""


def find_expired_but_active_events(
    connection: Connection,
    as_of_date: date,
) -> list[Event]:
    """Return critical events for matured products still marked active."""

    with connection.cursor() as cursor:
        cursor.execute(EXPIRED_BUT_ACTIVE_SQL, (as_of_date, "ACTIVE"))
        expired_products = cursor.fetchall()

    return [
        Event(
            isin=isin,
            event_type=EXPIRED_BUT_ACTIVE,
            severity="CRITICAL",
            event_date=maturity_date,
            description=(
                f"Product {isin} matured on {maturity_date.isoformat()} "
                "but remains ACTIVE."
            ),
            details={
                "maturity_date": maturity_date.isoformat(),
                "internal_status": "ACTIVE",
                "as_of_date": as_of_date.isoformat(),
            },
        )
        for isin, maturity_date in expired_products
    ]


def find_maturity_within_7_days_events(
    connection: Connection,
    as_of_date: date,
) -> list[Event]:
    """Return informational events for active products nearing maturity."""

    maturity_window_end = as_of_date + timedelta(days=7)
    with connection.cursor() as cursor:
        cursor.execute(
            MATURITY_WITHIN_7_DAYS_SQL,
            (as_of_date, maturity_window_end, "ACTIVE"),
        )
        upcoming_maturities = cursor.fetchall()

    return [
        Event(
            isin=isin,
            event_type=MATURITY_WITHIN_7_DAYS,
            severity="INFO",
            event_date=maturity_date,
            description=(
                f"Active product {isin} matures on {maturity_date.isoformat()}, "
                f"within 7 days of {as_of_date.isoformat()}."
            ),
            details={
                "maturity_date": maturity_date.isoformat(),
                "internal_status": "ACTIVE",
                "as_of_date": as_of_date.isoformat(),
            },
        )
        for isin, maturity_date in upcoming_maturities
    ]


def run_lifecycle(
    connection: Connection,
    as_of_date: date,
) -> list[Event]:
    """Run the general lifecycle rules implemented in Phase 7."""

    return find_expired_but_active_events(
        connection,
        as_of_date,
    ) + find_maturity_within_7_days_events(connection, as_of_date)
