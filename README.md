# AI Financial Advisor (IBKR + OpenAI + LangChain)

Event-driven swing portfolio monitor that ingests IBKR account/market/scanner data and runs AI analysis every minute or on significant movement triggers.

## Features
- IBKR live-first connection (`4001` by default, switchable via env)
- 60-second cycle for portfolio and risk metrics
- Watchlist supports futures contracts with explicit expiry/exchange
- Hybrid trigger engine (`% move`, `PnL delta`, `z-score`)
- Light AI analysis every minute, deep web-enabled analysis on triggers
- Suggest-only recommendations (`NO_ACTION` or `SUGGEST_ACTION`)
- PostgreSQL persistence + console and JSON logs
- Follow-up chat mode using latest recommendation context
- Follow-up chat enriches requested instruments with IBKR historical bars (1w/3d/5h VWAP, volatility, drawdown, volume, returns)

## Quick Start
1. Create a virtualenv and install dependencies:
   - `pip install -e .`
2. Copy env template:
   - `cp .env.example .env`
3. Fill `.env` (`OPENAI_API_KEY`, `IBKR_ACCOUNT_ID`, `POSTGRES_DSN`)
4. Ensure TWS or IB Gateway is running and API access is enabled.
5. Initialize DB tables by running:
   - `advisor doctor`
6. Run one cycle:
   - `advisor once`
7. Run continuous monitor:
   - `advisor run`

## Watchlist Formats
`WATCHLIST` accepts mixed instruments:
- Stock shorthand: `AAPL`
- Stock explicit: `STK:AAPL:SMART:USD`
- Futures shorthand: `MGC:202604:COMEX` or `MGC:20260428:COMEX`
- Futures explicit: `FUT:MNQ:202606:CME:USD`

Examples:
- `WATCHLIST=MGC:202604:COMEX,SI:202605:COMEX,MNQ:202606:CME`
- `WATCHLIST=STK:SPY:SMART:USD,MES:202606:CME`

## Commands
- `advisor doctor`: validates env, DB, OpenAI, and IBKR socket
- `advisor once`: executes a single collection/analysis cycle
- `advisor run`: starts scheduler loop at `RUN_INTERVAL_SECONDS`
- `advisor chat`: follow-up conversation using latest stored recommendation context
  - Single turn: `advisor chat --question "Should I add Gold exposure?"`
  - Interactive: `advisor chat`
- `advisor backtest --config configs/trading_mvp.yaml`: run systematic futures backtests (ORB + VWAP variants)
- `advisor validate --config configs/trading_mvp.yaml`: run walk-forward OOS validation and persist best variant
- `advisor paper-run --config configs/trading_mvp.yaml`: run local paper-trading loop (auto-orders only after validation pass)
- `advisor kill-switch --config configs/trading_mvp.yaml --on|--off`: set DB kill switch for new paper entries

Follow-up turns are persisted in PostgreSQL table `ai_followup_turns`.
IBKR historical bars used by follow-up are cached in `instrument_historical_bars` and pruned by `HIST_CACHE_RETENTION_DAYS`.

`advisor once` and `advisor run` now wait up to 60 seconds for initial IBKR account/position data and emit connectivity progress every 10 seconds in `logs/decisions.jsonl`.

## Historical Follow-Up Settings
- `IBKR_HIST_BAR_SIZE` (default `5 mins`)
- `IBKR_HIST_WHAT_TO_SHOW` (default `TRADES`)
- `IBKR_HIST_USE_RTH` (default `false`, includes RTH+ETH when false)
- `IBKR_HIST_DURATION` (default `8 D`)
- `IBKR_HIST_TIMEOUT_SECONDS` (default `20`)
- `HIST_CACHE_RETENTION_DAYS` (default `30`)

## Safety
- This service does not place orders.
- It only emits recommendations for manual execution.

## To start the docker of postgres, run:

```bash
docker run --name ai-advisor-postgres \
  --env-file .env \
  -p 5432:5432 \
  -v ai_advisor_pgdata:/var/lib/postgresql/data \
  -d postgres:16
```
