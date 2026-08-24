"""Structured-product lifecycle rules."""

from __future__ import annotations

from datetime import date, timedelta

from psycopg import Connection

from src.events import Event


EXPIRED_BUT_ACTIVE = "EXPIRED_BUT_ACTIVE"
MATURITY_WITHIN_7_DAYS = "MATURITY_WITHIN_7_DAYS"
BARRIER_BREACHED = "BARRIER_BREACHED"
AUTOCALL_TRIGGERED = "AUTOCALL_TRIGGERED"
MISSING_OBSERVATION_PRICE = "MISSING_OBSERVATION_PRICE"

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

# Rule D: due Express products and their price on exactly the configured
# observation date. LEFT JOIN keeps products whose exact-date price is missing.
# language=PostgreSQL
EXPRESS_OBSERVATION_SQL = """
    SELECT
        p.isin,
        p.underlying,
        p.next_observation_date,
        p.autocall_level,
        mp.price AS observation_price
    FROM products AS p
    LEFT JOIN market_prices AS mp
        ON mp.underlying = p.underlying
       AND mp.price_date = p.next_observation_date
    WHERE p.product_type = %s
      AND p.next_observation_date <= %s
      AND p.barrier IS NOT NULL
      AND p.autocall_level IS NOT NULL
      AND p.coupon IS NOT NULL
      AND p.next_observation_date IS NOT NULL
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


def find_express_autocall_events(
    connection: Connection,
    as_of_date: date,
) -> list[Event]:
    """Evaluate due Express products using only their exact observation price."""

    with connection.cursor() as cursor:
        cursor.execute(EXPRESS_OBSERVATION_SQL, ("EXPRESS", as_of_date))
        due_observations = cursor.fetchall()

    events: list[Event] = []
    for (
        isin,
        underlying,
        observation_date,
        autocall_level,
        observation_price,
    ) in due_observations:
        if observation_price is None:
            events.append(
                Event(
                    isin=isin,
                    event_type=MISSING_OBSERVATION_PRICE,
                    severity="WARNING",
                    event_date=observation_date,
                    description=(
                        f"Express product {isin} has no market price on "
                        f"observation date {observation_date.isoformat()}."
                    ),
                    details={
                        "underlying": underlying,
                        "observation_date": observation_date.isoformat(),
                        "autocall_level": float(autocall_level),
                    },
                )
            )
        elif observation_price >= autocall_level:
            events.append(
                Event(
                    isin=isin,
                    event_type=AUTOCALL_TRIGGERED,
                    severity="CRITICAL",
                    event_date=observation_date,
                    description=(
                        f"Express product {isin} met its autocall level on "
                        f"{observation_date.isoformat()}."
                    ),
                    details={
                        "underlying": underlying,
                        "observation_price": float(observation_price),
                        "autocall_level": float(autocall_level),
                    },
                )
            )

    return events


def run_lifecycle(
    connection: Connection,
    as_of_date: date,
) -> list[Event]:
    """Run all lifecycle rules implemented through Phase 9."""

    return (
        find_expired_but_active_events(connection, as_of_date)
        + find_maturity_within_7_days_events(connection, as_of_date)
        + find_bonus_barrier_breach_events(connection, as_of_date)
        + find_express_autocall_events(connection, as_of_date)
    )
