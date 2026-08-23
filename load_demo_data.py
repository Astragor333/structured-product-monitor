"""Command-line entry point for loading the fixed demonstration datasets."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import psycopg

from src.config import ConfigurationError, PROJECT_ROOT
from src.db import DatabaseUnavailableError, database_connection
from src.ingestion import IngestionError, load_demo_data


LOGGER = logging.getLogger(__name__)
DEFAULT_DATA_DIRECTORY = PROJECT_ROOT / "data"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load the fixed CSV demo datasets into PostgreSQL."
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Truncate all four application tables before loading the demo data.",
    )
    parser.add_argument(
        "--data-directory",
        type=Path,
        default=DEFAULT_DATA_DIRECTORY,
        help="Directory containing the three CSV files.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(message)s",
    )

    try:
        with database_connection() as connection:
            counts = load_demo_data(
                connection,
                args.data_directory,
                fresh=args.fresh,
            )
    except (
        ConfigurationError,
        DatabaseUnavailableError,
        IngestionError,
        psycopg.Error,
    ) as exc:
        LOGGER.error("Demo-data loading failed: %s", exc)
        return 1

    print(f"Loaded products: {counts.products}")
    print(f"Loaded exchange listings: {counts.exchange_listings}")
    print(f"Loaded market prices: {counts.market_prices}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
