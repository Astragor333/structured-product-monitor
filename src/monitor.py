"""Monitoring orchestration and command-line entry point."""

from __future__ import annotations

import argparse
from datetime import date
import logging

import psycopg

from src.config import ConfigurationError, DEFAULT_AS_OF_DATE
from src.db import DatabaseUnavailableError, database_connection
from src.events import Event, save_events
from src.reconciliation import run_reconciliation
from src.validation import validate_products


LOGGER = logging.getLogger(__name__)


def run_monitoring(as_of_date: date) -> list[Event]:
    """Run implemented validation and reconciliation rules, then persist events."""

    with database_connection() as connection:
        LOGGER.info("Running product validation")
        validation_events = validate_products(connection, as_of_date)

        LOGGER.info("Running reconciliation")
        reconciliation_events = run_reconciliation(connection, as_of_date)
        all_events = validation_events + reconciliation_events
        inserted_count = save_events(connection, all_events)

    LOGGER.info(
        "Generated %s events and persisted %s new events",
        len(all_events),
        inserted_count,
    )
    return all_events


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "as-of date must use YYYY-MM-DD format"
        ) from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run structured-product monitoring rules."
    )
    parser.add_argument(
        "--as-of-date",
        type=_parse_date,
        default=DEFAULT_AS_OF_DATE,
        help=f"Monitoring date in YYYY-MM-DD format (default: {DEFAULT_AS_OF_DATE}).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    try:
        generated_events = run_monitoring(args.as_of_date)
    except (ConfigurationError, DatabaseUnavailableError, psycopg.Error) as exc:
        LOGGER.error("Monitoring failed: %s", exc)
        return 1

    for event in generated_events:
        print(
            f"{event.severity} {event.event_type} "
            f"{event.isin or '-'} {event.event_date}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
