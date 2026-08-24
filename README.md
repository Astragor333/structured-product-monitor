# Structured Product Lifecycle Monitor

## Project Overview

Structured Product Lifecycle Monitor is a prototype of an internal banking
operations system. It loads synthetic structured-product data into PostgreSQL,
reconciles internal products with exchange listings, validates product
definitions, evaluates lifecycle conditions, persists operational events, and
presents the results in a read-only Streamlit dashboard.

This repository is a demonstration project. It is not a production banking
system and does not perform derivative pricing.

## Motivation

Banks that issue structured products need operational controls around:

- exchange-listing completeness and status;
- upcoming and overdue maturities;
- missing or invalid product terms;
- historical barrier observations;
- Express Certificate autocall observation dates.

The project demonstrates how these checks can be separated into testable
business-rule modules while PostgreSQL provides durable, idempotent event
persistence.

## Architecture

```text
CSV Demo Data
      |
      v
Data Ingestion
      |
      v
PostgreSQL
      |
      v
Validation / Reconciliation / Lifecycle Engines
      |
      v
Events
      |
      v
Streamlit Dashboard
```

Responsibilities are deliberately separated:

- **Data layer:** environment configuration, PostgreSQL connections, schema,
  CSV parsing, ingestion, and read-only dashboard queries.
- **Business logic:** validation, reconciliation, maturity, historical barrier,
  and exact-date autocall rules return event objects.
- **Persistence:** the event service stores events independently of rule
  evaluation.
- **Presentation:** `app.py` reads products and persisted events. It does not
  recalculate monitoring conditions or write to the database.

Logical event identity is `isin + event_type + event_date`. PostgreSQL enforces
this with a unique index, and inserts use `ON CONFLICT DO NOTHING`, so repeating
the same monitoring run does not create duplicate events.

## Supported Product Types

- **BONUS:** validates barrier and bonus-level terms and checks the complete
  valid historical price path for the first barrier breach.
- **DISCOUNT:** validates the required cap and participates in general
  validation, reconciliation, and maturity monitoring.
- **EXPRESS:** validates its observation terms and evaluates autocall only
  against the exact observation-date market price.

## Implemented Monitoring Rules

| Rule | Result |
|---|---|
| `MISSING_EXCHANGE_LISTING` | Active internal product has no exchange listing |
| `LISTING_STATUS_MISMATCH` | Internal and exchange listing statuses differ |
| `UNKNOWN_EXCHANGE_ISIN` | Exchange listing has no internal product |
| `MISSING_REQUIRED_FIELD` | Universal or product-specific data is missing |
| `UNKNOWN_PRODUCT_TYPE` | Product type is not supported |
| `INVALID_NOMINAL` | Nominal is not greater than zero |
| `INVALID_DATE_RANGE` | Issue date is after maturity date |
| `EXPIRED_BUT_ACTIVE` | Product has matured but remains active |
| `MATURITY_WITHIN_7_DAYS` | Active product matures within seven calendar days |
| `BARRIER_BREACHED` | BONUS product reached or crossed its barrier historically |
| `AUTOCALL_TRIGGERED` | EXPRESS observation-date price reached its autocall level |
| `MISSING_OBSERVATION_PRICE` | Due EXPRESS product lacks its exact-date price |

## Technology Stack

- Python 3.12+
- PostgreSQL
- SQL and psycopg
- pandas
- Streamlit
- pytest
- python-dotenv

## Project Structure

```text
structured-product-monitor/
|-- data/                       # Fixed synthetic CSV demo data
|-- sql/
|   |-- 001_create_tables.sql   # PostgreSQL tables and constraints
|   `-- 002_indexes.sql         # Query and event-identity indexes
|-- src/
|   |-- config.py               # Environment configuration
|   |-- db.py                   # Transaction and read-only connections
|   |-- ingestion.py            # Strict CSV parsing and loading
|   |-- events.py               # Event model and persistence
|   |-- validation.py           # Product-data validation rules
|   |-- reconciliation.py       # Internal/exchange reconciliation
|   |-- lifecycle.py            # Maturity, barrier, and autocall rules
|   |-- monitor.py              # Monitoring orchestration and CLI
|   `-- dashboard.py            # Read-only dashboard queries and filters
|-- tests/                      # PostgreSQL-backed automated tests
|-- app.py                      # Streamlit entry point
|-- load_demo_data.py           # Demo-data loader CLI
|-- requirements.txt
|-- .env.example
`-- README.md
```

## Setup

### 1. Clone the repository

```powershell
git clone https://github.com/Astragor333/structured-product-monitor.git
cd structured-product-monitor
```

### 2. Create and activate a Python virtual environment

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

macOS or Linux:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 4. Create the PostgreSQL database

With PostgreSQL running and `psql` available:

```powershell
psql -U postgres -d postgres -c "CREATE DATABASE structured_product_monitor;"
```

Skip this command if the database already exists. If you use another database
user or database name, use the same values in the following commands and in
`.env`.

### 5. Configure local environment variables

Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

macOS or Linux:

```bash
cp .env.example .env
```

Open `.env` and replace `your_password_here` with your local PostgreSQL
password. The real `.env` is ignored by Git and must never be committed.

### 6. Apply the database schema

```powershell
psql -v ON_ERROR_STOP=1 -U postgres -d structured_product_monitor -f sql/001_create_tables.sql
psql -v ON_ERROR_STOP=1 -U postgres -d structured_product_monitor -f sql/002_indexes.sql
```

### 7. Load the synthetic demo data

For a reproducible fresh demo, this command clears the four application tables
before loading the fixed CSV files:

```powershell
python load_demo_data.py --fresh
```

To update the three source tables without clearing existing events, omit
`--fresh`:

```powershell
python load_demo_data.py
```

### 8. Run monitoring

```powershell
python -m src.monitor --as-of-date 2026-08-23
```

### 9. Start the dashboard

```powershell
streamlit run app.py
```

Open `http://localhost:8501` if Streamlit does not open it automatically.

## Running Tests

The tests use PostgreSQL temporary tables, so the configured database must be
running and accessible.

```powershell
python -m pytest tests -v
```

## Running Monitoring

Pass the monitoring date explicitly in ISO format:

```powershell
python -m src.monitor --as-of-date 2026-08-23
```

Running the same date again is safe: logically identical events are not
inserted twice.

## Running the Dashboard

```powershell
streamlit run app.py
```

The dashboard is read-only. Its refresh button reloads products and persisted
events but does not execute monitoring or change product data.

## Demo Dataset

The CSV files in `data/` are fixed, synthetic demonstration inputs. They
intentionally contain a small number of listing, lifecycle, and product-data
anomalies so that the monitoring rules produce meaningful events. The loader
does not silently correct the source files.

## Screenshots

No dashboard screenshot is committed yet. For a portfolio presentation, start
the dashboard and capture the **Overview** tab after running the demo workflow.

## Limitations / Future Improvements

Current limitations include synthetic data, no live market feed, simplified
product terms, only three product types, no derivative pricing, and local
deployment.

Possible future extensions include automated market-data ingestion, payoff
calculation, more product types, scheduled monitoring and alerting, and a
historical product-detail view.
