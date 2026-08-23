"""Unit tests for deterministic product validation rules."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from src.validation import (
    INVALID_DATE_RANGE,
    INVALID_NOMINAL,
    MISSING_REQUIRED_FIELD,
    UNKNOWN_PRODUCT_TYPE,
    validate_product,
)


AS_OF_DATE = date(2026, 8, 23)


def valid_product(**overrides: Any) -> dict[str, Any]:
    product: dict[str, Any] = {
        "isin": "TESTPRODUCT001",
        "product_type": "BONUS",
        "underlying": "TEST.UNDERLYING",
        "currency": "EUR",
        "nominal": Decimal("1000"),
        "issue_date": date(2026, 1, 1),
        "maturity_date": date(2027, 1, 1),
        "strike": None,
        "barrier": Decimal("70"),
        "bonus_level": Decimal("120"),
        "cap": Decimal("100"),
        "autocall_level": Decimal("100"),
        "coupon": Decimal("0.08"),
        "next_observation_date": date(2026, 8, 23),
        "status": "ACTIVE",
    }
    product.update(overrides)
    return product


def test_bonus_without_barrier_creates_missing_required_field_event() -> None:
    events = validate_product(valid_product(barrier=None), AS_OF_DATE)

    assert len(events) == 1
    assert events[0].event_type == MISSING_REQUIRED_FIELD
    assert events[0].severity == "WARNING"
    assert events[0].description == "BONUS product requires barrier."


def test_bonus_without_bonus_level_creates_missing_required_field_event() -> None:
    events = validate_product(valid_product(bonus_level=None), AS_OF_DATE)

    assert len(events) == 1
    assert events[0].event_type == MISSING_REQUIRED_FIELD
    assert events[0].description == "BONUS product requires bonus_level."


def test_discount_without_cap_creates_missing_required_field_event() -> None:
    events = validate_product(
        valid_product(product_type="DISCOUNT", cap=None),
        AS_OF_DATE,
    )

    assert len(events) == 1
    assert events[0].event_type == MISSING_REQUIRED_FIELD
    assert events[0].description == "DISCOUNT product requires cap."


def test_express_without_autocall_level_creates_missing_required_field_event() -> None:
    events = validate_product(
        valid_product(product_type="EXPRESS", autocall_level=None),
        AS_OF_DATE,
    )

    assert len(events) == 1
    assert events[0].event_type == MISSING_REQUIRED_FIELD
    assert events[0].description == "EXPRESS product requires autocall_level."


def test_unknown_product_type_creates_warning_event() -> None:
    events = validate_product(
        valid_product(product_type="UNKNOWN"),
        AS_OF_DATE,
    )

    assert len(events) == 1
    assert events[0].event_type == UNKNOWN_PRODUCT_TYPE
    assert events[0].severity == "WARNING"


def test_valid_product_creates_no_validation_event() -> None:
    assert validate_product(valid_product(), AS_OF_DATE) == []


def test_non_positive_nominal_creates_invalid_nominal_event() -> None:
    events = validate_product(valid_product(nominal=Decimal("0")), AS_OF_DATE)

    assert len(events) == 1
    assert events[0].event_type == INVALID_NOMINAL
    assert events[0].description == "Product nominal must be greater than zero."


def test_issue_date_after_maturity_creates_invalid_date_range_event() -> None:
    events = validate_product(
        valid_product(
            issue_date=date(2027, 1, 2),
            maturity_date=date(2027, 1, 1),
        ),
        AS_OF_DATE,
    )

    assert len(events) == 1
    assert events[0].event_type == INVALID_DATE_RANGE
    assert (
        events[0].description
        == "Product issue_date must be on or before maturity_date."
    )


def test_missing_universal_field_creates_missing_required_field_event() -> None:
    events = validate_product(valid_product(underlying=None), AS_OF_DATE)

    assert len(events) == 1
    assert events[0].event_type == MISSING_REQUIRED_FIELD
    assert events[0].description == "Product requires underlying."
