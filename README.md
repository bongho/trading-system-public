# Trading System

An automated trading system for Korean markets (KRX stocks via Kiwoom Securities, crypto via Upbit) with an AI agent layer for strategy analysis and signal review.

> **Korean README**: [README.ko.md](README.ko.md)

---

## Features

- **Multi-broker**: Kiwoom Securities REST API (KRX stocks) + Upbit (crypto)
- **3 built-in strategies**: SimpleRSI, Double Bollinger Band Short, Squeeze MTF
- **AI agent**: OpenAI / Claude backend for signal review and market commentary
- **Risk management**: Daily loss limit, per-position size cap
- **Notifications**: Telegram bot (command & alert) + Discord daily report
- **Backtesting**: Built-in engine per strategy
- **Docker-first**: Single `docker compose up` to run

---

## Architecture

### System Overview

```mermaid
graph TB
    subgraph External["External Services"]
        KW[Kiwoom REST API<br/>api.kiwoom.com]
        UB[Upbit API<br/>pyupbit]
        TG[Telegram API]
        DC[Discord Webhook]
        AI[OpenAI / Claude API]
    end

    subgraph Core["Trading System"]
        BOT[Telegram Bot<br/>/portfolio /trade /ai ...]
        SCH[TradingScheduler<br/>APScheduler]
        STR[Strategy Registry<br/>SimpleRSI · DoubleBB · SqueezeMTF]
        AGT[AI Agent Orchestrator<br/>signal review & commentary]
        EXE[Executor]
        RM[Risk Manager<br/>daily loss · position cap]
        COL[Market Data Collector<br/>read-through cache]
        DB[(SQLite DB<br/>trades · market data · strategies)]
    end

    TG <-->|commands & alerts| BOT
    BOT --> EXE
    SCH -->|trigger| STR
    STR -->|signals| EXE
    STR <-->|OHLCV| COL
    AGT <-->|analysis| AI
    AGT -->|reviewed signals| EXE
    EXE --> RM
    RM -->|approved| KW
    RM -->|approved| UB
    EXE -->|record| DB
    COL <-->|cache| DB
    COL <-->|fetch| KW
    COL <-->|fetch| UB
    EXE -->|trade alert| TG
    SCH -->|daily report| DC
```

### Trade Execution Flow

```mermaid
sequenceDiagram
    participant S as Strategy
    participant A as AI Agent (optional)
    participant E as Executor
    participant R as RiskManager
    participant B as Broker (Kiwoom/Upbit)
    participant D as DB

    S->>E: TradeSignal(symbol, side, amount)
    opt AI review enabled
        E->>A: review_signal(signal, market_context)
        A-->>E: approved / rejected / modified
    end
    E->>R: check_risk(signal, portfolio)
    R-->>E: approved (within daily loss & position limits)
    E->>B: buy(symbol, amount) / sell(symbol, volume)
    B-->>E: TradeResult(order_id, price, volume)
    E->>D: save trade record
    E-->>Telegram: notify trade
```

### Directory layout

```
src/
├── agents/          # AI agent (OpenAI / Claude backends, orchestrator, sandbox)
├── brokers/         # BrokerAdapter implementations (Kiwoom, Upbit)
├── data/            # Market data collector with SQLite read-through cache
├── db/              # SQLite schema & repositories
├── engine/          # Executor, RiskManager, Scheduler, Backtest
├── reporters/       # Discord reporter, Telegram notifier
├── strategies/      # Strategy base class + 3 built-in strategies
├── telegram/        # Bot + command handlers
└── utils/           # Technical indicators (RSI, BB, Squeeze, ...)
```

---

## Strategies

| Strategy | Broker | Interval | Signal logic |
|----------|--------|----------|--------------|
| **SimpleRSI** | Upbit | 5m | RSI < 30 → buy, RSI > 70 → sell |
| **DoubleBBShort** | Upbit | 15m | Price breaks outer BB (2σ) + RSI oversold → reversal entry |
| **SqueezeMTF** | Upbit | 5m | BB inside KC (squeeze on) → momentum explodes → MTF-confirmed entry |

---

## Quick Start

### Prerequisites

- Python 3.12+
- Docker & Docker Compose
- API credentials (see [Configuration](#configuration))

### Run with Docker (recommended)

```bash
cp .env.example .env
# Fill in your credentials in .env
docker compose up -d
docker compose logs -f
```

### Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# Fill in .env
python -m src.main
```

---

## Configuration

Copy `.env.example` to `.env` and fill in the values:

```env
# Upbit (crypto)
UPBIT_ACCESS_KEY=
UPBIT_SECRET_KEY=

# Kiwoom Securities REST API
KIWOOM_APP_KEY=
KIWOOM_APP_SECRET=
KIWOOM_ACCOUNT_NO=
KIWOOM_IS_PAPER=true        # true = mock trading, false = live

# Telegram
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# Discord (optional — daily report)
DISCORD_WEBHOOK_URL=

# AI Agent (optional — at least one)
ANTHROPIC_API_KEY=
OPENAI_API_KEY=

# Database
DB_PATH=data/trading.db
```

### Credential setup guides

| Service | How to get credentials |
|---------|------------------------|
| **Upbit** | [Upbit Open API](https://upbit.com/service_center/open_api_guide) → Create API key |
| **Kiwoom** | [Kiwoom REST API](https://apiportal.kiwoom.com) → Register app → Get appkey / secretkey |
| **Telegram bot** | [@BotFather](https://t.me/BotFather) → `/newbot` → get token; get chat ID via `@userinfobot` |
| **Discord** | Server settings → Integrations → Webhooks → Create |
| **Anthropic** | [console.anthropic.com](https://console.anthropic.com) → API Keys |
| **OpenAI** | [platform.openai.com](https://platform.openai.com) → API Keys |

> **Security note**: Never commit `.env` to version control. The file is in `.gitignore`. Use environment secrets in production (Docker secrets, GitHub Actions secrets, etc.).

---

## Kiwoom REST API

This project uses the official **Kiwoom Securities REST API** (`api.kiwoom.com`), not the legacy OpenAPI+.

- Production: `https://api.kiwoom.com`
- Mock trading: `https://mockapi.kiwoom.com` (KRX only — set `KIWOOM_IS_PAPER=true`)
- Auth: OAuth2 client credentials, token auto-refreshed every 24h
- All requests: `HTTP POST` + JSON body

---

## CI/CD

GitHub Actions workflow (`.github/workflows/docker-publish.yml`) builds and pushes the Docker image to GitHub Container Registry on every push to `main`:

```
ghcr.io/bongho/trading-system-public:main
ghcr.io/bongho/trading-system-public:sha-<commit>
```

Pull the image:

```bash
docker pull ghcr.io/bongho/trading-system-public:main
```

---

## Testing

```bash
pytest -v
pytest --cov=src --cov-report=term-missing
```

---

## License

MIT
