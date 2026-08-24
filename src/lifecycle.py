"""General structured-product lifecycle rules."""

from __future__ import annotations

from datetime import date, timedelta

from psycopg import Connection

from src.events import Event


EXPIRED_BUT_ACTIVE = "EXPIRED_BUT_ACTIVE"
MATURITY_WITHIN_7_DAYS = "MATURITY_WITHIN_7_DAYS"
BARRIER_BREACHED = "BARRIER_BREACHED"

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

# Rule C: first historical Bonus barrier breach inside the product's valid
# monitoring period. The lateral query deliberately checks the full price path,
# rather than only the latest observation.
# language=PostgreSQL
BONUS_BARRIER_BREACH_SQL = """
    SELECT
        p.isin,
        p.underlying,
        p.barrier,
        first_breach.price_date,
        first_breach.price
    FROM products AS p
    JOIN LATERAL (
        SELECT
            mp.price_date,
            mp.price
        FROM market_prices AS mp
        WHERE mp.underlying = p.underlying
          AND mp.price_date >= p.issue_date
          AND mp.price_date <= LEAST(%s, p.maturity_date)
          AND mp.price <= p.barrier
        ORDER BY mp.price_date
        LIMIT 1
    ) AS first_breach ON TRUE
    WHERE p.product_type = %s
      AND p.barrier IS NOT NULL
    ORDER BY p.isin
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


def find_bonus_barrier_breach_events(
    connection: Connection,
    as_of_date: date,
) -> list[Event]:
    """Return the first valid historical barrier breach for each Bonus product."""

    with connection.cursor() as cursor:
        cursor.execute(BONUS_BARRIER_BREACH_SQL, (as_of_date, "BONUS"))
        first_breaches = cursor.fetchall()

    return [
        Event(
            isin=isin,
            event_type=BARRIER_BREACHED,
            severity="CRITICAL",
            event_date=breach_date,
            description=(
                f"Bonus product {isin} first breached its barrier on "
                f"{breach_date.isoformat()}."
            ),
            details={
                "underlying": underlying,
                "barrier": float(barrier),
                "breach_price": float(breach_price),
            },
        )
        for isin, underlying, barrier, breach_date, breach_price in first_breaches
    ]


def run_lifecycle(
    connection: Connection,
    as_of_date: date,
) -> list[Event]:
    """Run all lifecycle rules implemented through Phase 8."""

    return (
        find_expired_but_active_events(connection, as_of_date)
        + find_maturity_within_7_days_events(connection, as_of_date)
        + find_bonus_barrier_breach_events(connection, as_of_date)
    )
