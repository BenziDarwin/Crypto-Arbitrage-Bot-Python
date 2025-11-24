DROP TABLE IF EXISTS triangular_arbitrage_log;

CREATE TABLE IF NOT EXISTS triangular_arbitrage_log (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    start_amount_usdt NUMERIC(18,6),
    end_amount_usdt NUMERIC(18,6),
    profit_usdt NUMERIC(18,6),
    profit_percentage NUMERIC(10,6),
    base_token VARCHAR(10),
    path VARCHAR(100),
    routers VARCHAR(100),
    executed BOOLEAN DEFAULT FALSE
);
