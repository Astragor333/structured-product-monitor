"""Exchange-listing reconciliation rules."""

from __future__ import annotations

from datetime import date

from psycopg import Connection

from src.events import Event


MISSING_EXCHANGE_LISTING = "MISSING_EXCHANGE_LISTING"
LISTING_STATUS_MISMATCH = "LISTING_STATUS_MISMATCH"
UNKNOWN_EXCHANGE_ISIN = "UNKNOWN_EXCHANGE_ISIN"

# Rule A: active internal products with no exchange-listing row.
# language=PostgreSQL
MISSING_EXCHANGE_LISTING_SQL = """
    SELECT p.isin
    FROM products AS p
    LEFT JOIN exchange_listings AS e
        ON p.isin = e.isin
    WHERE p.status = %s
      AND e.isin IS NULL
    ORDER BY p.isin
"""

# Rule B: internal and exchange statuses disagree for a listed product.
# language=PostgreSQL
LISTING_STATUS_MISMATCH_SQL = """
    SELECT
        p.isin,
        p.status AS internal_status,
        e.listing_status AS exchange_status,
        e.exchange
    FROM products AS p
    JOIN exchange_listings AS e
        ON p.isin = e.isin
    WHERE p.status <> e.listing_status
    ORDER BY p.isin, e.exchange
"""

# Rule C: an exchange listing has no corresponding internal product.
# language=PostgreSQL
UNKNOWN_EXCHANGE_ISIN_SQL = """
    SELECT
        e.isin,
        e.exchange,
        e.listing_status
    FROM exchange_listings AS e
    LEFT JOIN products AS p
        ON e.isin = p.isin
    WHERE p.isin IS NULL
    ORDER BY e.isin, e.exchange
"""


def find_missing_exchange_listing_events(
    connection: Connection,
    as_of_date: date,
) -> list[Event]:
    """Return critical events for active products absent from exchange data."""

    with connection.cursor() as cursor:
        cursor.execute(MISSING_EXCHANGE_LISTING_SQL, ("ACTIVE",))
        missing_isins = [row[0] for row in cursor.fetchall()]

    return [
        Event(
            isin=isin,
            event_type=MISSING_EXCHANGE_LISTING,
            severity="CRITICAL",
            event_date=as_of_date,
            description=f"Active product {isin} has no exchange listing.",
            details={"internal_status": "ACTIVE"},
        )
        for isin in missing_isins
    ]


def find_listing_status_mismatch_events(
    connection: Connection,
    as_of_date: date,
) -> list[Event]:
    """Return critical events when internal and exchange statuses disagree."""

    with connection.cursor() as cursor:
        cursor.execute(LISTING_STATUS_MISMATCH_SQL)
        mismatches = cursor.fetchall()

    return [
        Event(
            isin=isin,
            event_type=LISTING_STATUS_MISMATCH,
            severity="CRITICAL",
            event_date=as_of_date,
            description=(
                f"Product {isin} has internal status {internal_status} but "
                f"exchange status {exchange_status} on {exchange}."
            ),
            details={
                "internal_status": internal_status,
                "exchange_status": exchange_status,
                "exchange": exchange,
            },
        )
        for isin, internal_status, exchange_status, exchange in mismatches
    ]


def find_unknown_exchange_isin_events(
    connection: Connection,
    as_of_date: date,
) -> list[Event]:
    """Return warning events for exchange ISINs absent from product master."""

    with connection.cursor() as cursor:
        cursor.execute(UNKNOWN_EXCHANGE_ISIN_SQL)
        unknown_listings = cursor.fetchall()

    return [
        Event(
            isin=isin,
            event_type=UNKNOWN_EXCHANGE_ISIN,
            severity="WARNING",
            event_date=as_of_date,
            description=(
                f"Exchange listing {isin} on {exchange} has no internal product."
            ),
            details={
                "exchange_status": exchange_status,
                "exchange": exchange,
            },
        )
        for isin, exchange, exchange_status in unknown_listings
    ]


def run_reconciliation(
    connection: Connection,
    as_of_date: date,
) -> list[Event]:
    """Run all reconciliation rules implemented through Phase 5."""

    return (
        find_missing_exchange_listing_events(connection, as_of_date)
        + find_listing_status_mismatch_events(connection, as_of_date)
        + find_unknown_exchange_isin_events(connection, as_of_date)
    )
