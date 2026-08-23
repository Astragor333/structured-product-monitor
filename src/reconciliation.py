"""Exchange-listing reconciliation rules."""

from __future__ import annotations

from datetime import date

from psycopg import Connection

from src.events import Event


MISSING_EXCHANGE_LISTING = "MISSING_EXCHANGE_LISTING"

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
