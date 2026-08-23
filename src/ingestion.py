"""Strict CSV parsing and transactional demo-data ingestion."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import logging
from pathlib import Path
from typing import Any, cast

import pandas as pd
from psycopg import Connection


LOGGER = logging.getLogger(__name__)

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
PRODUCT_NUMERIC_COLUMNS = (
    "nominal",
    "strike",
    "barrier",
    "bonus_level",
    "cap",
    "autocall_level",
    "coupon",
)
PRODUCT_REQUIRED_NUMERIC_COLUMNS = ("nominal",)
PRODUCT_DATE_COLUMNS = ("issue_date", "maturity_date", "next_observation_date")
PRODUCT_REQUIRED_DATE_COLUMNS = ("issue_date", "maturity_date")

EXCHANGE_LISTING_COLUMNS = (
    "isin",
    "exchange",
    "listing_status",
    "listing_date",
)

MARKET_PRICE_CSV_COLUMNS = ("underlying", "date", "price")
MARKET_PRICE_DATABASE_COLUMNS = ("underlying", "price_date", "price")

# language=PostgreSQL
INSERT_PRODUCTS_SQL = """
    INSERT INTO products (
        isin, product_type, underlying, currency, nominal, issue_date,
        maturity_date, strike, barrier, bonus_level, cap, autocall_level,
        coupon, next_observation_date, status
    )
    VALUES (
        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
    )
    ON CONFLICT (isin) DO UPDATE SET
        product_type = EXCLUDED.product_type,
        underlying = EXCLUDED.underlying,
        currency = EXCLUDED.currency,
        nominal = EXCLUDED.nominal,
        issue_date = EXCLUDED.issue_date,
        maturity_date = EXCLUDED.maturity_date,
        strike = EXCLUDED.strike,
        barrier = EXCLUDED.barrier,
        bonus_level = EXCLUDED.bonus_level,
        cap = EXCLUDED.cap,
        autocall_level = EXCLUDED.autocall_level,
        coupon = EXCLUDED.coupon,
        next_observation_date = EXCLUDED.next_observation_date,
        status = EXCLUDED.status
"""

# language=PostgreSQL
INSERT_EXCHANGE_LISTINGS_SQL = """
    INSERT INTO exchange_listings (
        isin, exchange, listing_status, listing_date
    )
    VALUES (%s, %s, %s, %s)
    ON CONFLICT (isin, exchange) DO UPDATE SET
        listing_status = EXCLUDED.listing_status,
        listing_date = EXCLUDED.listing_date
"""

# language=PostgreSQL
INSERT_MARKET_PRICES_SQL = """
    INSERT INTO market_prices (underlying, price_date, price)
    VALUES (%s, %s, %s)
    ON CONFLICT (underlying, price_date) DO UPDATE SET
        price = EXCLUDED.price
"""

# language=PostgreSQL
TRUNCATE_DEMO_TABLES_SQL = """
    TRUNCATE TABLE events, exchange_listings, market_prices, products
    RESTART IDENTITY
"""


class IngestionError(RuntimeError):
    """Base error for invalid or unreadable input data."""


class MissingCsvFileError(IngestionError):
    """Raised when an expected CSV input file does not exist."""


class MissingColumnsError(IngestionError):
    """Raised when a CSV file does not contain its required columns."""


class InvalidColumnValueError(IngestionError):
    """Raised when a date or numeric value cannot be parsed."""


@dataclass(frozen=True, slots=True)
class DemoData:
    """Typed database rows parsed from the three fixed CSV files."""

    products: list[tuple[Any, ...]]
    exchange_listings: list[tuple[Any, ...]]
    market_prices: list[tuple[Any, ...]]


@dataclass(frozen=True, slots=True)
class LoadCounts:
    """Number of rows processed for each demo dataset."""

    products: int
    exchange_listings: int
    market_prices: int


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise MissingCsvFileError(f"CSV file not found: {path}")

    try:
        return cast(pd.DataFrame, pd.read_csv(str(path), dtype="string"))
    except (
        OSError,
        pd.errors.EmptyDataError,
        pd.errors.ParserError,
        UnicodeDecodeError,
    ) as exc:
        raise IngestionError(f"Could not parse CSV file {path}: {exc}") from exc


def _require_columns(
    frame: pd.DataFrame,
    required_columns: tuple[str, ...],
    path: Path,
) -> None:
    missing = [column for column in required_columns if column not in frame.columns]
    if missing:
        raise MissingColumnsError(
            f"CSV file {path} is missing required columns: {', '.join(missing)}"
        )


def _convert_date_column(
    frame: pd.DataFrame,
    column: str,
    path: Path,
    *,
    required: bool,
) -> None:
    raw_values = frame[column]
    present = raw_values.notna() & raw_values.str.strip().ne("")
    parsed = cast(
        pd.Series,
        pd.to_datetime(
            raw_values.where(present),
            format="%Y-%m-%d",
            errors="coerce",
        ),
    )
    invalid = present & parsed.isna()

    if invalid.any():
        row_numbers = (invalid[invalid].index + 2).tolist()
        raise InvalidColumnValueError(
            f"CSV file {path} has invalid dates in column '{column}' "
            f"on rows {row_numbers}. Expected YYYY-MM-DD."
        )
    if required and (~present).any():
        row_numbers = ((~present)[~present].index + 2).tolist()
        raise InvalidColumnValueError(
            f"CSV file {path} has missing dates in required column '{column}' "
            f"on rows {row_numbers}."
        )

    frame[column] = parsed.dt.date


def _convert_numeric_column(
    frame: pd.DataFrame,
    column: str,
    path: Path,
    *,
    required: bool,
) -> None:
    raw_values = frame[column]
    present = raw_values.notna() & raw_values.str.strip().ne("")
    parsed = pd.to_numeric(raw_values.where(present), errors="coerce")
    invalid = present & parsed.isna()

    if invalid.any():
        row_numbers = (invalid[invalid].index + 2).tolist()
        raise InvalidColumnValueError(
            f"CSV file {path} has invalid numbers in column '{column}' "
            f"on rows {row_numbers}."
        )
    if required and (~present).any():
        row_numbers = ((~present)[~present].index + 2).tolist()
        raise InvalidColumnValueError(
            f"CSV file {path} has missing numbers in required column '{column}' "
            f"on rows {row_numbers}."
        )

    frame[column] = parsed


def _to_python_value(value: Any) -> Any:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.date()
    if hasattr(value, "item"):
        return value.item()
    return value


def _records(
    frame: pd.DataFrame,
    columns: tuple[str, ...],
    *,
    decimal_columns: tuple[str, ...] = (),
) -> list[tuple[Any, ...]]:
    records: list[tuple[Any, ...]] = []
    for row in frame.loc[:, columns].itertuples(index=False, name=None):
        converted: list[Any] = []
        for column, raw_value in zip(columns, row, strict=True):
            value = _to_python_value(raw_value)
            if value is not None and column in decimal_columns:
                value = Decimal(str(value))
            converted.append(value)
        records.append(tuple(converted))
    return records


def read_products(path: Path) -> list[tuple[Any, ...]]:
    """Read and structurally validate the internal product CSV."""

    frame = _read_csv(path)
    _require_columns(frame, PRODUCT_COLUMNS, path)

    for column in PRODUCT_NUMERIC_COLUMNS:
        _convert_numeric_column(
            frame,
            column,
            path,
            required=column in PRODUCT_REQUIRED_NUMERIC_COLUMNS,
        )
    for column in PRODUCT_DATE_COLUMNS:
        _convert_date_column(
            frame,
            column,
            path,
            required=column in PRODUCT_REQUIRED_DATE_COLUMNS,
        )

    return _records(frame, PRODUCT_COLUMNS, decimal_columns=PRODUCT_NUMERIC_COLUMNS)


def read_exchange_listings(path: Path) -> list[tuple[Any, ...]]:
    """Read and structurally validate the exchange-listing CSV."""

    frame = _read_csv(path)
    _require_columns(frame, EXCHANGE_LISTING_COLUMNS, path)
    _convert_date_column(frame, "listing_date", path, required=True)
    return _records(frame, EXCHANGE_LISTING_COLUMNS)


def read_market_prices(path: Path) -> list[tuple[Any, ...]]:
    """Read and structurally validate the historical market-price CSV."""

    frame = _read_csv(path)
    _require_columns(frame, MARKET_PRICE_CSV_COLUMNS, path)
    _convert_date_column(frame, "date", path, required=True)
    _convert_numeric_column(frame, "price", path, required=True)
    frame = frame.rename(columns={"date": "price_date"})
    return _records(
        frame,
        MARKET_PRICE_DATABASE_COLUMNS,
        decimal_columns=("price",),
    )


def read_demo_data(data_directory: Path) -> DemoData:
    """Parse all demo files before starting database writes."""

    return DemoData(
        products=read_products(data_directory / "products.csv"),
        exchange_listings=read_exchange_listings(
            data_directory / "exchange_listings.csv"
        ),
        market_prices=read_market_prices(data_directory / "market_prices.csv"),
    )


def load_demo_data(
    connection: Connection,
    data_directory: Path,
    *,
    fresh: bool = False,
) -> LoadCounts:
    """Load all demo data into PostgreSQL as one caller-controlled transaction."""

    demo_data = read_demo_data(data_directory)

    with connection.cursor() as cursor:
        if fresh:
            cursor.execute(TRUNCATE_DEMO_TABLES_SQL)

        cursor.executemany(INSERT_PRODUCTS_SQL, demo_data.products)
        cursor.executemany(
            INSERT_EXCHANGE_LISTINGS_SQL,
            demo_data.exchange_listings,
        )
        cursor.executemany(INSERT_MARKET_PRICES_SQL, demo_data.market_prices)

    counts = LoadCounts(
        products=len(demo_data.products),
        exchange_listings=len(demo_data.exchange_listings),
        market_prices=len(demo_data.market_prices),
    )
    LOGGER.info("Loaded %s products", counts.products)
    LOGGER.info("Loaded %s exchange listings", counts.exchange_listings)
    LOGGER.info("Loaded %s market prices", counts.market_prices)
    return counts
