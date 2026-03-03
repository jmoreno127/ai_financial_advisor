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

`advisor once` and `advisor run` now wait up to 60 seconds for initial IBKR account/position data and emit connectivity progress every 10 seconds in `logs/decisions.jsonl`.

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
