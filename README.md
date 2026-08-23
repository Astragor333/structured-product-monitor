# Structured Product Lifecycle Monitor

A prototype monitoring system for structured-product lifecycle and exchange-listing processes.

> **Project status:** Phase 6 is complete. Product validation and all three exchange-reconciliation rules are implemented and persist idempotent events. Lifecycle rules and the dashboard are intentionally not implemented yet.

## Business context

Banks issuing large numbers of structured products need to reconcile internal product definitions with exchange listings and underlying market data. This project will model that operational workflow and surface data-quality, reconciliation, and lifecycle events without attempting derivative pricing.

The fixed CSV files in `data/` are read-only demo inputs. They must be ingested without modifying or silently correcting their contents.

## Architecture

```text
CSV input files
       |
       v
Data ingestion
       |
       v
PostgreSQL
       |
       +-------------------+
       v                   v
Reconciliation engine   Lifecycle engine
       |                   |
       +---------+---------+
                 v
             Event store
                 |
                 v
       Read-only Streamlit dashboard
```

Each layer will have one responsibility. CSV parsing, SQL/database access, business rules, event persistence, and presentation will remain separate.

## Planned product types

- **Bonus Certificate:** monitored for a barrier touch at any point in the relevant historical price path.
- **Discount Certificate:** monitored for required cap data, maturity, and listing state in the MVP.
- **Express Certificate:** evaluated for autocall only on its exact observation date.

## Implemented checks

- `MISSING_EXCHANGE_LISTING`: active internal product with no exchange-listing row
- `LISTING_STATUS_MISMATCH`: internal and exchange listing statuses disagree
- `UNKNOWN_EXCHANGE_ISIN`: exchange listing has no internal product
- `MISSING_REQUIRED_FIELD`: universal or product-specific field is missing
- `UNKNOWN_PRODUCT_TYPE`: product type is unsupported
- `INVALID_NOMINAL`: nominal is not greater than zero
- `INVALID_DATE_RANGE`: issue date is after maturity date

## Planned checks

- Expired but still active product
- Maturity within seven calendar days
- Historical Bonus barrier breach
- Express autocall trigger
- Missing Express observation-day price

## Technology

- Python 3.12+
- PostgreSQL
- SQL and `psycopg`
- pandas
- Streamlit
- pytest
- python-dotenv

## Repository layout

```text
structured-product-monitor/
|-- data/                  # Fixed, read-only demo CSV datasets
|-- sql/                   # PostgreSQL schema and query files (Phase 2 onward)
|-- src/                   # Configuration, database, ingestion, and future engines
|-- tests/                 # Automated-test placeholders for later rule phases
|-- app.py                 # Future Streamlit entry point
|-- load_demo_data.py      # Transactional CSV loader
|-- requirements.txt       # Python dependencies
|-- .env.example           # Safe database configuration template
|-- .gitignore             # Local, generated, and secret files excluded from Git
`-- README.md
```

## Setup

### Prerequisites

- Python 3.12 or newer
- PostgreSQL installed and available for Phase 2
- A PostgreSQL database and user with permission to create tables and indexes

### Python environment

From the repository root, create and activate a virtual environment, then install the dependencies.

PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### Configuration

Copy `.env.example` to `.env` and supply local PostgreSQL credentials:

```powershell
Copy-Item .env.example .env
```

Never commit `.env`. The demo monitoring date will be `2026-08-23`, and core business functions will receive their as-of date explicitly.

## Running

Apply the Phase 2 migrations with PostgreSQL's `psql` client after creating the configured database:

```powershell
psql -v ON_ERROR_STOP=1 -d structured_product_monitor -f sql/001_create_tables.sql
psql -v ON_ERROR_STOP=1 -d structured_product_monitor -f sql/002_indexes.sql
```

Connection options can be supplied through PostgreSQL environment variables or explicit `psql` arguments.

Load or update the fixed demo datasets:

```powershell
python load_demo_data.py
```

For a completely fresh demo database, clear all four application tables before loading:

```powershell
python load_demo_data.py --fresh
```

The fresh option also clears persisted events and resets their identity sequence. Later phases will add and verify commands for monitoring, tests, and the Streamlit dashboard.

Run validation and reconciliation monitoring for the deterministic demo date:

```powershell
python -m src.monitor --as-of-date 2026-08-23
```

Run the validation and reconciliation tests:

```powershell
python -m pytest tests/test_validation.py tests/test_reconciliation.py -v
```
