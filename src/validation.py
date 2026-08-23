"""Deterministic validation rules for internal product definitions."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from psycopg import Connection
from psycopg.rows import dict_row

from src.events import Event


MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"
UNKNOWN_PRODUCT_TYPE = "UNKNOWN_PRODUCT_TYPE"
INVALID_NOMINAL = "INVALID_NOMINAL"
INVALID_DATE_RANGE = "INVALID_DATE_RANGE"

SUPPORTED_PRODUCT_TYPES = frozenset({"BONUS", "DISCOUNT", "EXPRESS"})

UNIVERSAL_REQUIRED_FIELDS = (
    "isin",
    "product_type",
    "underlying",
    "currency",
    "nominal",
    "issue_date",
    "maturity_date",
    "status",
)

PRODUCT_REQUIRED_FIELDS = {
    "BONUS": ("barrier", "bonus_level"),
    "DISCOUNT": ("cap",),
    "EXPRESS": (
        "barrier",
        "autocall_level",
        "coupon",
        "next_observation_date",
    ),
}

# language=PostgreSQL
PRODUCTS_FOR_VALIDATION_SQL = """
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


def _is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _event_isin(product: Mapping[str, Any]) -> str | None:
    value = product.get("isin")
    return None if _is_missing(value) else str(value)


def _missing_field_event(
    product: Mapping[str, Any],
    field_name: str,
    as_of_date: date,
    *,
    product_specific: bool,
) -> Event:
    product_type = product.get("product_type")
    if product_specific:
        description = f"{product_type} product requires {field_name}."
    else:
        description = f"Product requires {field_name}."

    return Event(
        isin=_event_isin(product),
        event_type=MISSING_REQUIRED_FIELD,
        severity="WARNING",
        event_date=as_of_date,
        description=description,
        details={
            "field": field_name,
            "product_type": None if _is_missing(product_type) else product_type,
        },
    )


def validate_product(
    product: Mapping[str, Any],
    as_of_date: date,
) -> list[Event]:
    """Validate one typed product record and return all business-data events."""

    events: list[Event] = []

    for field_name in UNIVERSAL_REQUIRED_FIELDS:
        if _is_missing(product.get(field_name)):
            events.append(
                _missing_field_event(
                    product,
                    field_name,
                    as_of_date,
                    product_specific=False,
                )
            )

    product_type = product.get("product_type")
    if not _is_missing(product_type):
        if product_type not in SUPPORTED_PRODUCT_TYPES:
            events.append(
                Event(
                    isin=_event_isin(product),
                    event_type=UNKNOWN_PRODUCT_TYPE,
                    severity="WARNING",
                    event_date=as_of_date,
                    description=f"Unsupported product type: {product_type}.",
                    details={"product_type": product_type},
                )
            )
        else:
            for field_name in PRODUCT_REQUIRED_FIELDS[product_type]:
                if _is_missing(product.get(field_name)):
                    events.append(
                        _missing_field_event(
                            product,
                            field_name,
                            as_of_date,
                            product_specific=True,
                        )
                    )

    nominal = product.get("nominal")
    if not _is_missing(nominal) and nominal <= 0:
        events.append(
            Event(
                isin=_event_isin(product),
                event_type=INVALID_NOMINAL,
                severity="WARNING",
                event_date=as_of_date,
                description="Product nominal must be greater than zero.",
                details={"field": "nominal", "value": str(nominal)},
            )
        )

    issue_date = product.get("issue_date")
    maturity_date = product.get("maturity_date")
    if (
        not _is_missing(issue_date)
        and not _is_missing(maturity_date)
        and issue_date > maturity_date
    ):
        events.append(
            Event(
                isin=_event_isin(product),
                event_type=INVALID_DATE_RANGE,
                severity="WARNING",
                event_date=as_of_date,
                description="Product issue_date must be on or before maturity_date.",
                details={
                    "issue_date": issue_date.isoformat(),
                    "maturity_date": maturity_date.isoformat(),
                },
            )
        )

    return events


def validate_products(
    connection: Connection,
    as_of_date: date,
) -> list[Event]:
    """Load typed products from PostgreSQL and validate each independently."""

    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(PRODUCTS_FOR_VALIDATION_SQL)
        products = cursor.fetchall()

    validation_events: list[Event] = []
    for product in products:
        validation_events.extend(validate_product(product, as_of_date))
    return validation_events
