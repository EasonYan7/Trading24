-- Trading24 数据库 schema (SQLite)

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker      TEXT NOT NULL,
    market      TEXT,                 -- us / cn / hk
    trade_date  TEXT NOT NULL,        -- yyyy-mm-dd
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL,        -- running / success / failed
    decision    TEXT,                 -- BUY / HOLD / SELL
    model       TEXT,
    tokens      INTEGER DEFAULT 0,
    cost_usd    REAL DEFAULT 0,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_ticker_date ON runs(ticker, trade_date);

CREATE TABLE IF NOT EXISTS reports (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     INTEGER NOT NULL REFERENCES runs(id),
    section    TEXT NOT NULL,         -- market/news/fundamentals/sentiment/bull/bear/research_mgr/trader/risk/final
    content    TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reports_run ON reports(run_id);

CREATE TABLE IF NOT EXISTS price_snapshots (
    ticker      TEXT NOT NULL,
    date        TEXT NOT NULL,        -- yyyy-mm-dd
    open        REAL, high REAL, low REAL, close REAL, volume INTEGER,
    ma5         REAL, ma20 REAL, ma60 REAL,
    rsi         REAL, macd REAL, macd_signal REAL,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS alert_configs (
    ticker   TEXT PRIMARY KEY,
    upper    REAL,
    lower    REAL,
    pct_move REAL,
    enabled  INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS notifications (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id  INTEGER REFERENCES runs(id),
    kind    TEXT,                     -- price_alert / daily_digest / health
    channel TEXT,
    sent_at TEXT,
    ok      INTEGER
);
