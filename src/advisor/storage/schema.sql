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

CREATE TABLE IF NOT EXISTS ai_followup_turns (
    id BIGSERIAL PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    turn_index INTEGER NOT NULL,
    account_id TEXT,
    decision_cycle_ts TIMESTAMPTZ,
    model_used TEXT NOT NULL,
    user_question TEXT NOT NULL,
    assistant_answer TEXT NOT NULL,
    context_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (conversation_id, turn_index)
);

CREATE TABLE IF NOT EXISTS instrument_historical_bars (
    id BIGSERIAL PRIMARY KEY,
    instrument_key TEXT NOT NULL,
    bar_ts TIMESTAMPTZ NOT NULL,
    open DOUBLE PRECISION NOT NULL,
    high DOUBLE PRECISION NOT NULL,
    low DOUBLE PRECISION NOT NULL,
    close DOUBLE PRECISION NOT NULL,
    volume DOUBLE PRECISION NOT NULL,
    wap DOUBLE PRECISION NOT NULL,
    bar_count INTEGER NOT NULL,
    bar_size TEXT NOT NULL,
    what_to_show TEXT NOT NULL,
    use_rth BOOLEAN NOT NULL,
    source TEXT NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (instrument_key, bar_ts, bar_size, what_to_show, use_rth)
);

CREATE INDEX IF NOT EXISTS idx_instrument_historical_bars_key_ts
    ON instrument_historical_bars (instrument_key, bar_ts DESC);

CREATE INDEX IF NOT EXISTS idx_instrument_historical_bars_fetched_at
    ON instrument_historical_bars (fetched_at);
