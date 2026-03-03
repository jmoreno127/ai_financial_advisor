CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id BIGSERIAL PRIMARY KEY,
    cycle_ts TIMESTAMPTZ NOT NULL,
    account_id TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (cycle_ts, account_id)
);

CREATE TABLE IF NOT EXISTS position_snapshots (
    id BIGSERIAL PRIMARY KEY,
    cycle_ts TIMESTAMPTZ NOT NULL,
    account_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (cycle_ts, account_id, symbol)
);

CREATE TABLE IF NOT EXISTS instrument_snapshots (
    id BIGSERIAL PRIMARY KEY,
    cycle_ts TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    source TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (cycle_ts, symbol, source)
);

CREATE TABLE IF NOT EXISTS trigger_events (
    id BIGSERIAL PRIMARY KEY,
    cycle_ts TIMESTAMPTZ NOT NULL,
    account_id TEXT NOT NULL,
    name TEXT NOT NULL,
    symbol TEXT,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ai_decisions (
    id BIGSERIAL PRIMARY KEY,
    cycle_ts TIMESTAMPTZ NOT NULL,
    account_id TEXT NOT NULL,
    model_used TEXT NOT NULL,
    deep_analysis BOOLEAN NOT NULL,
    request_payload JSONB NOT NULL,
    recommendation_payload JSONB NOT NULL,
    raw_response TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (cycle_ts, account_id)
);

CREATE TABLE IF NOT EXISTS service_heartbeats (
    id BIGSERIAL PRIMARY KEY,
    service_name TEXT NOT NULL,
    status TEXT NOT NULL,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    heartbeat_ts TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
