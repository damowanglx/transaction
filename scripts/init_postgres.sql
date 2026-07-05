-- PostgreSQL business data initialization
CREATE TABLE IF NOT EXISTS trade_records (
    id              BIGSERIAL PRIMARY KEY,
    ts_code         VARCHAR(15)  NOT NULL,
    direction       VARCHAR(4)   NOT NULL CHECK (direction IN ('BUY', 'SELL')),
    price           NUMERIC(10,4) NOT NULL,
    volume          INT          NOT NULL,
    amount          NUMERIC(16,2) NOT NULL,
    commission      NUMERIC(10,2) NOT NULL DEFAULT 0,
    stamp_tax       NUMERIC(10,2) NOT NULL DEFAULT 0,
    trade_time      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    strategy_name   VARCHAR(100) NOT NULL,
    signal_reason   TEXT,
    order_id        VARCHAR(50),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_trade_ts_code ON trade_records(ts_code);
CREATE INDEX IF NOT EXISTS idx_trade_time    ON trade_records(trade_time);
CREATE INDEX IF NOT EXISTS idx_trade_strategy ON trade_records(strategy_name);

CREATE TABLE IF NOT EXISTS daily_pnl (
    id              BIGSERIAL PRIMARY KEY,
    trade_date      DATE         NOT NULL,
    total_value     NUMERIC(16,2) NOT NULL,
    cash            NUMERIC(16,2) NOT NULL,
    market_value    NUMERIC(16,2) NOT NULL,
    daily_pnl       NUMERIC(12,2) NOT NULL,
    daily_return    NUMERIC(10,6) NOT NULL,
    cumulative_pnl  NUMERIC(14,2) NOT NULL,
    positions_json  TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE(trade_date)
);

CREATE TABLE IF NOT EXISTS risk_events (
    id              BIGSERIAL PRIMARY KEY,
    event_time      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    event_type      VARCHAR(50)  NOT NULL,
    severity        VARCHAR(10)  NOT NULL CHECK (severity IN ('INFO', 'WARN', 'CRITICAL')),
    ts_code         VARCHAR(15),
    detail          TEXT         NOT NULL,
    resolved        BOOLEAN      NOT NULL DEFAULT FALSE,
    resolved_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_risk_time ON risk_events(event_time);
CREATE INDEX IF NOT EXISTS idx_risk_type ON risk_events(event_type);

CREATE TABLE IF NOT EXISTS strategy_signals (
    id              BIGSERIAL PRIMARY KEY,
    signal_time     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    strategy_name   VARCHAR(100) NOT NULL,
    ts_code         VARCHAR(15)  NOT NULL,
    signal_type     VARCHAR(10)  NOT NULL CHECK (signal_type IN ('BUY', 'SELL', 'HOLD')),
    confidence      NUMERIC(5,4),
    factor_values   JSONB,
    executed        BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_signal_time     ON strategy_signals(signal_time);
CREATE INDEX IF NOT EXISTS idx_signal_strategy ON strategy_signals(strategy_name);
CREATE INDEX IF NOT EXISTS idx_signal_code     ON strategy_signals(ts_code);
