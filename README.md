# AI Financial Advisor (IBKR + OpenAI + LangChain)

Event-driven swing portfolio monitor that ingests IBKR account/market/scanner data and runs AI analysis every minute or on significant movement triggers.

## Features
- IBKR live-first connection (`4001` by default, switchable via env)
- 60-second cycle for portfolio and risk metrics
- Hybrid trigger engine (`% move`, `PnL delta`, `z-score`)
- Light AI analysis every minute, deep web-enabled analysis on triggers
- Suggest-only recommendations (`NO_ACTION` or `SUGGEST_ACTION`)
- PostgreSQL persistence + console and JSON logs

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

## Commands
- `advisor doctor`: validates env, DB, OpenAI, and IBKR socket
- `advisor once`: executes a single collection/analysis cycle
- `advisor run`: starts scheduler loop at `RUN_INTERVAL_SECONDS`

## Safety
- This service does not place orders.
- It only emits recommendations for manual execution.
