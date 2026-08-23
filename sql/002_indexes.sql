BEGIN;

CREATE INDEX IF NOT EXISTS idx_products_underlying
    ON products (underlying);

CREATE INDEX IF NOT EXISTS idx_products_maturity
    ON products (maturity_date);

CREATE INDEX IF NOT EXISTS idx_market_prices_underlying_date
    ON market_prices (underlying, price_date);

CREATE INDEX IF NOT EXISTS idx_events_isin
    ON events (isin);

CREATE INDEX IF NOT EXISTS idx_events_type
    ON events (event_type);

CREATE UNIQUE INDEX IF NOT EXISTS uq_events_identity
    ON events (COALESCE(isin, ''), event_type, event_date);

COMMIT;
