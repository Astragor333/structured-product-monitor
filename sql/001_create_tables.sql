BEGIN;

CREATE TABLE IF NOT EXISTS products (
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
    status VARCHAR(20) NOT NULL,
    CONSTRAINT products_nominal_positive CHECK (nominal > 0),
    CONSTRAINT products_issue_before_maturity CHECK (issue_date <= maturity_date)
);

CREATE TABLE IF NOT EXISTS exchange_listings (
    isin VARCHAR(20) NOT NULL,
    exchange VARCHAR(50) NOT NULL,
    listing_status VARCHAR(20) NOT NULL,
    listing_date DATE NOT NULL,
    PRIMARY KEY (isin, exchange)
);

CREATE TABLE IF NOT EXISTS market_prices (
    underlying VARCHAR(30) NOT NULL,
    price_date DATE NOT NULL,
    price NUMERIC(18, 6) NOT NULL,
    PRIMARY KEY (underlying, price_date),
    CONSTRAINT market_prices_price_positive CHECK (price > 0)
);

CREATE TABLE IF NOT EXISTS events (
    event_id BIGSERIAL PRIMARY KEY,
    isin VARCHAR(20),
    event_type VARCHAR(50) NOT NULL,
    severity VARCHAR(20) NOT NULL,
    event_date DATE NOT NULL,
    detected_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    description TEXT NOT NULL,
    details JSONB,
    CONSTRAINT events_severity_valid CHECK (
        severity IN ('INFO', 'WARNING', 'CRITICAL')
    )
);

COMMIT;
