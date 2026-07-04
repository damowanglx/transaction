-- ============================================================
-- A股量化交易系统 — 数据库初始化
-- ClickHouse: 行情数据 (时序, 千万级K线)
-- PostgreSQL: 业务数据 (交易记录, 风控, 账户)
-- ============================================================

-- ============================================================
-- ClickHouse: K-line data (daily bars)
-- ============================================================
CREATE DATABASE IF NOT EXISTS quant;

USE quant;

-- Daily K-line table
CREATE TABLE IF NOT EXISTS quant.daily_bars
(
    ts_code       String,            -- Stock code (e.g. 600000.SH)
    trade_date    Date,              -- Trading date
    open          Float64,           -- Open price
    high          Float64,           -- High price
    low           Float64,           -- Low price
    close         Float64,           -- Close price
    pre_close     Float64,           -- Previous close
    change        Float64,           -- Price change
    pct_chg       Float64,           -- Price change percentage
    vol           Float64,           -- Volume (shares)
    amount        Float64,           -- Turnover (yuan)
    turnover_rate Float64,           -- Turnover rate
    pe            Nullable(Float64), -- PE ratio
    pb            Nullable(Float64), -- PB ratio
    is_st         UInt8 DEFAULT 0    -- ST flag: 0=normal, 1=ST
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(trade_date)
ORDER BY (ts_code, trade_date)
SETTINGS index_granularity = 8192;

-- Minute K-line table (for intraday strategies later)
CREATE TABLE IF NOT EXISTS quant.minute_bars
(
    ts_code    String,
    trade_time DateTime,            -- Minute timestamp
    open       Float64,
    high       Float64,
    low        Float64,
    close      Float64,
    vol        Float64,
    amount     Float64
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(trade_time)
ORDER BY (ts_code, trade_time)
SETTINGS index_granularity = 8192;

-- Stock basic info (dimension table, ReplacingMergeTree for upserts)
CREATE TABLE IF NOT EXISTS quant.stock_info
(
    ts_code       String,
    name          String,           -- Stock name in Chinese
    area          String,           -- Region
    industry      String,           -- Industry classification
    market        String,           -- Market: SH/SZ/BJ
    list_date     Date,             -- Listing date
    delist_date   Nullable(Date),   -- Delisting date
    updated_at    DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY ts_code;

-- ============================================================
-- PostgreSQL: Business data
-- ============================================================

-- Trade records
CREATE TABLE IF NOT EXISTS trade_records (
    id              BIGSERIAL PRIMARY KEY,
    ts_code         VARCHAR(15)  NOT NULL,
    direction       VARCHAR(4)   NOT NULL CHECK (direction IN ('BUY', 'SELL')),
    price           NUMERIC(10,4) NOT NULL,
    volume          INT          NOT NULL,        -- Shares
    amount          NUMERIC(16,2) NOT NULL,       -- Total amount in yuan
    commission      NUMERIC(10,2) NOT NULL DEFAULT 0,
    stamp_tax       NUMERIC(10,2) NOT NULL DEFAULT 0,
    trade_time      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    strategy_name   VARCHAR(100) NOT NULL,
    signal_reason   TEXT,                          -- Why this trade was made
    order_id        VARCHAR(50),                   -- Broker order ID
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_trade_ts_code ON trade_records(ts_code);
CREATE INDEX idx_trade_time    ON trade_records(trade_time);
CREATE INDEX idx_trade_strategy ON trade_records(strategy_name);

-- Daily P&L snapshots
CREATE TABLE IF NOT EXISTS daily_pnl (
    id              BIGSERIAL PRIMARY KEY,
    trade_date      DATE         NOT NULL,
    total_value     NUMERIC(16,2) NOT NULL,       -- Total portfolio value
    cash            NUMERIC(16,2) NOT NULL,       -- Available cash
    market_value    NUMERIC(16,2) NOT NULL,       -- Holdings market value
    daily_pnl       NUMERIC(12,2) NOT NULL,       -- Daily profit/loss
    daily_return    NUMERIC(10,6) NOT NULL,       -- Daily return rate
    cumulative_pnl  NUMERIC(14,2) NOT NULL,       -- Cumulative P&L
    positions_json  TEXT,                          -- Snapshot of all positions
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE(trade_date)
);

-- Risk events log
CREATE TABLE IF NOT EXISTS risk_events (
    id              BIGSERIAL PRIMARY KEY,
    event_time      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    event_type      VARCHAR(50)  NOT NULL,        -- STOP_LOSS, MELTDOWN, POSITION_EXCEED, etc.
    severity        VARCHAR(10)  NOT NULL CHECK (severity IN ('INFO', 'WARN', 'CRITICAL')),
    ts_code         VARCHAR(15),
    detail          TEXT         NOT NULL,
    resolved        BOOLEAN      NOT NULL DEFAULT FALSE,
    resolved_at     TIMESTAMPTZ
);

CREATE INDEX idx_risk_time ON risk_events(event_time);
CREATE INDEX idx_risk_type ON risk_events(event_type);

-- Strategy signals (for audit trail)
CREATE TABLE IF NOT EXISTS strategy_signals (
    id              BIGSERIAL PRIMARY KEY,
    signal_time     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    strategy_name   VARCHAR(100) NOT NULL,
    ts_code         VARCHAR(15)  NOT NULL,
    signal_type     VARCHAR(10)  NOT NULL CHECK (signal_type IN ('BUY', 'SELL', 'HOLD')),
    confidence      NUMERIC(5,4),                 -- 0.0 to 1.0
    factor_values   JSONB,                        -- Factor values at signal time
    executed        BOOLEAN      NOT NULL DEFAULT FALSE,  -- Whether acted upon
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_signal_time     ON strategy_signals(signal_time);
CREATE INDEX idx_signal_strategy ON strategy_signals(strategy_name);
CREATE INDEX idx_signal_code     ON strategy_signals(ts_code);
