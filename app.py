"""Read-only Streamlit dashboard for persisted monitoring results."""

from __future__ import annotations

from collections.abc import Sequence
import logging

import pandas as pd
import psycopg
import streamlit as st

from src.config import ConfigurationError, DEFAULT_AS_OF_DATE
from src.dashboard import (
    DATA_QUALITY_EVENT_TYPES,
    LIFECYCLE_EVENT_TYPES,
    RECONCILIATION_EVENT_TYPES,
    DashboardData,
    available_filter_values,
    filter_rows,
    format_event_details,
    load_dashboard_data,
    select_event_types,
)
from src.db import DatabaseUnavailableError, read_only_database_connection


LOGGER = logging.getLogger(__name__)

PRODUCT_DISPLAY_COLUMNS = (
    "isin",
    "product_type",
    "underlying",
    "currency",
    "nominal",
    "issue_date",
    "maturity_date",
    "barrier",
    "bonus_level",
    "cap",
    "autocall_level",
    "coupon",
    "next_observation_date",
    "status",
)

ATTENTION_COLUMNS = (
    "severity",
    "isin",
    "product_type",
    "underlying",
    "event_type",
    "event_date",
    "description",
)

EVENT_DISPLAY_COLUMNS = ATTENTION_COLUMNS + ("details",)

EVENT_FILTERS = (
    ("severity", "Severity"),
    ("product_type", "Product type"),
    ("event_type", "Event type"),
    ("underlying", "Underlying"),
)

PRODUCT_FILTERS = (
    ("product_type", "Product type"),
    ("underlying", "Underlying"),
    ("status", "Status"),
)


def _apply_filters(
    data: pd.DataFrame,
    filter_definitions: Sequence[tuple[str, str]],
    *,
    key_prefix: str,
) -> pd.DataFrame:
    """Render compact multiselect controls and apply their selections."""

    filter_columns = st.columns(len(filter_definitions))
    selections: dict[str, list[str]] = {}

    for container, (field, label) in zip(
        filter_columns,
        filter_definitions,
        strict=True,
    ):
        with container:
            selections[field] = st.multiselect(
                label,
                options=available_filter_values(data, field),
                key=f"{key_prefix}_{field}",
            )

    return filter_rows(data, selections)


def _display_table(
    data: pd.DataFrame,
    columns: Sequence[str],
    *,
    empty_message: str,
) -> None:
    """Display a consistently formatted table or a clear empty state."""

    if data.empty:
        st.info(empty_message)
        return

    visible_columns = [column for column in columns if column in data.columns]
    displayed = data.loc[:, visible_columns].copy()
    if "details" in displayed.columns:
        displayed["details"] = displayed["details"].map(format_event_details)

    st.dataframe(
        displayed,
        hide_index=True,
    )


def _render_kpis(data: DashboardData) -> None:
    kpis = data.kpis
    cards = st.columns(6)
    labels_and_values = (
        ("Active Products", kpis.active_products),
        ("Critical Events", kpis.critical_events),
        ("Warnings", kpis.warnings),
        ("Maturing Within 7 Days", kpis.maturing_within_7_days),
        ("Barrier Breaches", kpis.barrier_breaches),
        ("Autocall Triggers", kpis.autocall_triggers),
    )
    for card, (label, value) in zip(cards, labels_and_values, strict=True):
        card.metric(label, value)


def _render_overview(data: DashboardData) -> None:
    _render_kpis(data)
    st.subheader("Products Requiring Attention")
    filtered = _apply_filters(
        data.events,
        EVENT_FILTERS,
        key_prefix="overview",
    )
    _display_table(
        filtered,
        ATTENTION_COLUMNS,
        empty_message="No events found for the selected filters.",
    )


def _render_products(data: DashboardData) -> None:
    st.subheader("Internal Product Master")
    filtered = _apply_filters(
        data.products,
        PRODUCT_FILTERS,
        key_prefix="products",
    )
    _display_table(
        filtered,
        PRODUCT_DISPLAY_COLUMNS,
        empty_message="No products found for the selected filters.",
    )


def _render_event_tab(
    events: pd.DataFrame,
    *,
    heading: str,
    key_prefix: str,
) -> None:
    st.subheader(heading)
    filtered = _apply_filters(
        events,
        EVENT_FILTERS,
        key_prefix=key_prefix,
    )
    _display_table(
        filtered,
        EVENT_DISPLAY_COLUMNS,
        empty_message="No events found for the selected filters.",
    )


def _load_data() -> DashboardData:
    with read_only_database_connection() as connection:
        return load_dashboard_data(connection)


def main() -> None:
    st.set_page_config(
        page_title="Structured Product Lifecycle Monitor",
        layout="wide",
    )

    st.title("Structured Product Lifecycle Monitor")
    st.caption(f"As-of Date: {DEFAULT_AS_OF_DATE.isoformat()}")

    st.sidebar.header("Dashboard")
    st.sidebar.date_input(
        "As-of Date",
        value=DEFAULT_AS_OF_DATE,
        disabled=True,
        help="Monitoring is run separately; this dashboard only reads results.",
    )
    st.sidebar.button(
        "Refresh data",
        use_container_width=True,
        help="Reload products and persisted events from PostgreSQL.",
    )
    st.sidebar.caption(
        "Read-only view. Monitoring and data changes are performed outside "
        "the dashboard."
    )

    try:
        data = _load_data()
    except (ConfigurationError, DatabaseUnavailableError) as exc:
        st.error(f"Unable to connect to the dashboard database: {exc}")
        st.stop()
    except psycopg.Error:
        LOGGER.exception("PostgreSQL dashboard query failed")
        st.error(
            "The dashboard connected to PostgreSQL but could not read its "
            "data. Check that the project database schema is up to date."
        )
        st.stop()

    overview_tab, products_tab, reconciliation_tab, lifecycle_tab, quality_tab = (
        st.tabs(
            [
                "Overview",
                "Products",
                "Reconciliation",
                "Lifecycle Events",
                "Data Quality",
            ]
        )
    )

    with overview_tab:
        _render_overview(data)

    with products_tab:
        _render_products(data)

    with reconciliation_tab:
        _render_event_tab(
            select_event_types(data.events, RECONCILIATION_EVENT_TYPES),
            heading="Reconciliation Events",
            key_prefix="reconciliation",
        )

    with lifecycle_tab:
        _render_event_tab(
            select_event_types(data.events, LIFECYCLE_EVENT_TYPES),
            heading="Lifecycle Events",
            key_prefix="lifecycle",
        )

    with quality_tab:
        _render_event_tab(
            select_event_types(data.events, DATA_QUALITY_EVENT_TYPES),
            heading="Validation and Data-Quality Events",
            key_prefix="data_quality",
        )


if __name__ == "__main__":
    main()
