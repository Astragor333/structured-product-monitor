"""PostgreSQL-backed tests for the read-only dashboard query layer."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pandas as pd
import pytest
from psycopg import Connection

from src.dashboard import (
    DATA_QUALITY_EVENT_TYPES,
    LIFECYCLE_EVENT_TYPES,
    RECONCILIATION_EVENT_TYPES,
    EVENT_COLUMNS,
    PRODUCT_COLUMNS,
    filter_rows,
    format_event_details,
    load_dashboard_data,
    select_event_types,
)
from src.db import create_connection, read_only_database_connection
from src.lifecycle import BARRIER_BREACHED, MATURITY_WITHIN_7_DAYS
from src.reconciliation import LISTING_STATUS_MISMATCH, UNKNOWN_EXCHANGE_ISIN
from src.validation import INVALID_NOMINAL, MISSING_REQUIRED_FIELD


@pytest.fixture
def dashboard_connection() -> Iterator[Connection]:
    """Use temporary tables so dashboard-query tests cannot alter demo data."""

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
        yield connection
    finally:
        connection.rollback()
        connection.close()


def insert_product(connection: Connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO products (
                isin,
                product_type,
                underlying,
                currency,
                nominal,
                issue_date,
                maturity_date,
                barrier,
                bonus_level,
                status
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                "AT000SP002",
                "BONUS",
                "SAP.DE",
                "EUR",
                Decimal("1000"),
                date(2026, 1, 1),
                date(2027, 1, 1),
                Decimal("150"),
                Decimal("175"),
                "ACTIVE",
            ),
        )


def insert_event(
    connection: Connection,
    *,
    isin: str,
    event_type: str,
    severity: str,
    event_date: date,
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO events (
                isin,
                event_type,
                severity,
                event_date,
                description,
                details
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                isin,
                event_type,
                severity,
                event_date,
                f"Test event {event_type}.",
                '{"source": "test"}',
            ),
        )


def test_empty_database_results_are_returned_safely(
    dashboard_connection: Connection,
) -> None:
    data = load_dashboard_data(dashboard_connection)

    assert data.kpis.active_products == 0
    assert data.kpis.critical_events == 0
    assert data.kpis.warnings == 0
    assert data.products.empty
    assert tuple(data.products.columns) == PRODUCT_COLUMNS
    assert data.events.empty
    assert tuple(data.events.columns) == EVENT_COLUMNS


def test_dashboard_uses_persisted_events_and_retains_exchange_only_isin(
    dashboard_connection: Connection,
) -> None:
    insert_product(dashboard_connection)
    insert_event(
        dashboard_connection,
        isin="AT000SP002",
        event_type=LISTING_STATUS_MISMATCH,
        severity="CRITICAL",
        event_date=date(2026, 8, 23),
    )
    insert_event(
        dashboard_connection,
        isin="AT000SP002",
        event_type=BARRIER_BREACHED,
        severity="CRITICAL",
        event_date=date(2026, 8, 22),
    )
    insert_event(
        dashboard_connection,
        isin="AT000SP999",
        event_type=UNKNOWN_EXCHANGE_ISIN,
        severity="WARNING",
        event_date=date(2026, 8, 23),
    )
    insert_event(
        dashboard_connection,
        isin="AT000SP002",
        event_type=MATURITY_WITHIN_7_DAYS,
        severity="INFO",
        event_date=date(2026, 8, 27),
    )

    data = load_dashboard_data(dashboard_connection)

    assert data.kpis.active_products == 1
    assert data.kpis.critical_events == 2
    assert data.kpis.warnings == 1
    assert data.kpis.maturing_within_7_days == 1
    assert data.kpis.barrier_breaches == 1
    assert data.kpis.autocall_triggers == 0
    assert data.events["severity"].tolist() == [
        "CRITICAL",
        "CRITICAL",
        "WARNING",
        "INFO",
    ]

    unknown_event = data.events.loc[
        data.events["event_type"] == UNKNOWN_EXCHANGE_ISIN
    ].iloc[0]
    assert unknown_event["isin"] == "AT000SP999"
    assert pd.isna(unknown_event["product_type"])
    assert pd.isna(unknown_event["underlying"])


def test_event_categories_and_filters_are_presentation_only() -> None:
    events = pd.DataFrame(
        [
            {
                "severity": "CRITICAL",
                "event_type": LISTING_STATUS_MISMATCH,
                "product_type": "BONUS",
                "underlying": "SAP.DE",
            },
            {
                "severity": "CRITICAL",
                "event_type": BARRIER_BREACHED,
                "product_type": "BONUS",
                "underlying": "SAP.DE",
            },
            {
                "severity": "WARNING",
                "event_type": MISSING_REQUIRED_FIELD,
                "product_type": "BONUS",
                "underlying": "BMW.DE",
            },
            {
                "severity": "WARNING",
                "event_type": INVALID_NOMINAL,
                "product_type": "DISCOUNT",
                "underlying": "ADS.DE",
            },
        ]
    )
    original = events.copy(deep=True)

    assert len(select_event_types(events, RECONCILIATION_EVENT_TYPES)) == 1
    assert len(select_event_types(events, LIFECYCLE_EVENT_TYPES)) == 1
    assert len(select_event_types(events, DATA_QUALITY_EVENT_TYPES)) == 2

    filtered = filter_rows(
        events,
        {
            "severity": ["WARNING"],
            "product_type": ["BONUS"],
            "event_type": [],
        },
    )
    assert filtered["event_type"].tolist() == [MISSING_REQUIRED_FIELD]
    pd.testing.assert_frame_equal(events, original)


def test_event_details_are_rendered_as_readable_json() -> None:
    assert format_event_details(
        {"underlying": "SAP.DE", "breach_price": Decimal("148")}
    ) == '{"breach_price": "148", "underlying": "SAP.DE"}'
    assert format_event_details(None) == ""


def test_dashboard_connection_is_database_enforced_read_only() -> None:
    with read_only_database_connection() as connection:
        assert connection.read_only is True
        with connection.cursor() as cursor:
            cursor.execute("SHOW transaction_read_only")
            assert cursor.fetchone() == ("on",)
            cursor.execute("SELECT 1")
            assert cursor.fetchone() == (1,)
