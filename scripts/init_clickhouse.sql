-- ClickHouse K-line data initialization
CREATE DATABASE IF NOT EXISTS quant;

CREATE TABLE IF NOT EXISTS quant.daily_bars
(
    ts_code       String,
    trade_date    Date,
    open          Float64,
    high          Float64,
    low           Float64,
    close         Float64,
    pre_close     Float64,
    change        Float64,
    pct_chg       Float64,
    vol           Float64,
    amount        Float64,
    turnover_rate Float64,
    pe            Nullable(Float64),
    pb            Nullable(Float64),
    is_st         UInt8 DEFAULT 0
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(trade_date)
ORDER BY (ts_code, trade_date)
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS quant.minute_bars
(
    ts_code    String,
    trade_time DateTime,
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

CREATE TABLE IF NOT EXISTS quant.stock_info
(
    ts_code       String,
    name          String,
    area          String,
    industry      String,
    market        String,
    list_date     Date,
    delist_date   Nullable(Date),
    updated_at    DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY ts_code;
