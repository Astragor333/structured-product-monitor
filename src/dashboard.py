"""Read-only PostgreSQL queries and display helpers for the dashboard."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
from typing import Any

import pandas as pd
from psycopg import Connection
from psycopg.rows import dict_row

from src.lifecycle import (
    AUTOCALL_TRIGGERED,
    BARRIER_BREACHED,
    EXPIRED_BUT_ACTIVE,
    MATURITY_WITHIN_7_DAYS,
    MISSING_OBSERVATION_PRICE,
)
from src.reconciliation import (
    LISTING_STATUS_MISMATCH,
    MISSING_EXCHANGE_LISTING,
    UNKNOWN_EXCHANGE_ISIN,
)
from src.validation import (
    INVALID_DATE_RANGE,
    INVALID_NOMINAL,
    MISSING_REQUIRED_FIELD,
    UNKNOWN_PRODUCT_TYPE,
)


RECONCILIATION_EVENT_TYPES = (
    MISSING_EXCHANGE_LISTING,
    LISTING_STATUS_MISMATCH,
    UNKNOWN_EXCHANGE_ISIN,
)

LIFECYCLE_EVENT_TYPES = (
    BARRIER_BREACHED,
    AUTOCALL_TRIGGERED,
    EXPIRED_BUT_ACTIVE,
    MATURITY_WITHIN_7_DAYS,
    MISSING_OBSERVATION_PRICE,
)

DATA_QUALITY_EVENT_TYPES = (
    MISSING_REQUIRED_FIELD,
    UNKNOWN_PRODUCT_TYPE,
    INVALID_NOMINAL,
    INVALID_DATE_RANGE,
)

PRODUCT_COLUMNS = (
    "isin",
    "product_type",
    "underlying",
    "currency",
    "nominal",
    "issue_date",
    "maturity_date",
    "strike",
    "barrier",
    "bonus_level",
    "cap",
    "autocall_level",
    "coupon",
    "next_observation_date",
    "status",
)

EVENT_COLUMNS = (
    "severity",
    "isin",
    "product_type",
    "underlying",
    "event_type",
    "event_date",
    "description",
    "details",
)

# Dashboard KPI values come only from current product records and persisted events.
# No lifecycle or reconciliation condition is recalculated here.
# language=PostgreSQL
DASHBOARD_KPIS_SQL = """
    SELECT
        (SELECT COUNT(*) FROM products WHERE status = %s) AS active_products,
        (SELECT COUNT(*) FROM events WHERE severity = %s) AS critical_events,
        (SELECT COUNT(*) FROM events WHERE severity = %s) AS warnings,
        (
            SELECT COUNT(*)
            FROM events
            WHERE event_type = %s
        ) AS maturing_within_7_days,
        (
            SELECT COUNT(*)
            FROM events
            WHERE event_type = %s
        ) AS barrier_breaches,
        (
            SELECT COUNT(*)
            FROM events
            WHERE event_type = %s
        ) AS autocall_triggers
"""

# language=PostgreSQL
PRODUCTS_SQL = """
    SELECT
        isin,
        product_type,
        underlying,
        currency,
        nominal,
        issue_date,
        maturity_date,
        strike,
        barrier,
        bonus_level,
        cap,
        autocall_level,
        coupon,
        next_observation_date,
        status
    FROM products
    ORDER BY isin
"""

# LEFT JOIN deliberately retains exchange-only events such as
# UNKNOWN_EXCHANGE_ISIN when no internal product exists.
# language=PostgreSQL
EVENTS_WITH_PRODUCTS_SQL = """
    SELECT
        e.severity,
        e.isin,
        p.product_type,
        p.underlying,
        e.event_type,
        e.event_date,
        e.description,
        e.details
    FROM events AS e
    LEFT JOIN products AS p
        ON p.isin = e.isin
    ORDER BY
        CASE e.severity
            WHEN 'CRITICAL' THEN 1
            WHEN 'WARNING' THEN 2
            WHEN 'INFO' THEN 3
            ELSE 4
        END,
        e.event_date DESC,
        e.isin NULLS LAST,
        e.event_type
"""


@dataclass(frozen=True, slots=True)
class DashboardKpis:
    """Counts displayed in the dashboard overview."""

    active_products: int
    critical_events: int
    warnings: int
    maturing_within_7_days: int
    barrier_breaches: int
    autocall_triggers: int


@dataclass(frozen=True, slots=True)
class DashboardData:
    """One read-only snapshot used to render a Streamlit rerun."""

    kpis: DashboardKpis
    products: pd.DataFrame
    events: pd.DataFrame


def _dataframe(
    rows: Sequence[Mapping[str, Any]],
    columns: Sequence[str],
) -> pd.DataFrame:
    """Build a consistently shaped frame, including for an empty result."""

    return pd.DataFrame.from_records(rows, columns=columns)


def load_dashboard_data(connection: Connection) -> DashboardData:
    """Read products, persisted events, and KPI counts from PostgreSQL."""

    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            DASHBOARD_KPIS_SQL,
            (
                "ACTIVE",
                "CRITICAL",
                "WARNING",
                MATURITY_WITHIN_7_DAYS,
                BARRIER_BREACHED,
                AUTOCALL_TRIGGERED,
            ),
        )
        kpi_row = cursor.fetchone()
        if kpi_row is None:
            raise RuntimeError("PostgreSQL returned no dashboard KPI row.")

        cursor.execute(PRODUCTS_SQL)
        product_rows = cursor.fetchall()

        cursor.execute(EVENTS_WITH_PRODUCTS_SQL)
        event_rows = cursor.fetchall()

    return DashboardData(
        kpis=DashboardKpis(
            active_products=int(kpi_row["active_products"]),
            critical_events=int(kpi_row["critical_events"]),
            warnings=int(kpi_row["warnings"]),
            maturing_within_7_days=int(
                kpi_row["maturing_within_7_days"]
            ),
            barrier_breaches=int(kpi_row["barrier_breaches"]),
            autocall_triggers=int(kpi_row["autocall_triggers"]),
        ),
        products=_dataframe(product_rows, PRODUCT_COLUMNS),
        events=_dataframe(event_rows, EVENT_COLUMNS),
    )


def select_event_types(
    events: pd.DataFrame,
    event_types: Sequence[str],
) -> pd.DataFrame:
    """Return persisted events belonging to one presentation category."""

    if events.empty:
        return events.copy()
    return events.loc[events["event_type"].isin(event_types)].reset_index(
        drop=True
    )


def available_filter_values(data: pd.DataFrame, column: str) -> list[str]:
    """Return sorted non-null values suitable for a Streamlit multiselect."""

    if data.empty or column not in data.columns:
        return []
    return sorted(str(value) for value in data[column].dropna().unique())


def filter_rows(
    data: pd.DataFrame,
    selections: Mapping[str, Sequence[str]],
) -> pd.DataFrame:
    """Apply optional presentation filters without mutating source data."""

    filtered = data.copy()
    for column, selected_values in selections.items():
        if selected_values and column in filtered.columns:
            filtered = filtered.loc[filtered[column].isin(selected_values)]
    return filtered.reset_index(drop=True)


def format_event_details(value: Any) -> str:
    """Render a JSON/JSONB value as a compact, readable string."""

    if value is None or value == "":
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
