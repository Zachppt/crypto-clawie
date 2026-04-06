# 🤖 crypto-clawie

> A Hyperliquid perpetual contract trading agent built on the **OpenClaw framework**.
> Monitors funding rates, executes perp orders, detects liquidation risk, and delivers real-time alerts via Telegram.

**Version:** 1.0.0 · **Python:** ≥ 3.10 · **License:** MIT

---

## Features

| Skill | Description |
|---|---|
| 📡 **HL Monitor** | Funding rates, open interest, liquidation risk, account positions |
| ⚡ **HL Trade** | Open/close positions, set leverage, cancel orders on Hyperliquid |
| 📊 **Crypto Data** | Price tracking, Fear & Greed index via Binance + CoinGecko |
| 🔔 **Alert** | Funding rate spikes, price anomalies, liquidation warnings |
| 📰 **News** | BlockBeats flash news, HL-related event filtering |
| 📋 **Report** | Daily & weekly market + account summaries |

---

## Architecture

```
OpenClaw (Telegram ↔ LLM)
        │
        ▼
   AGENTS.md          ← Session protocol (what to read at startup)
   SOUL.md            ← Agent identity & rules
   SKILLS.md          ← Skill index
        │
        ▼
   skills/
   ├── hl_trade/      ← Hyperliquid order execution (EIP-712 via SDK)
   ├── hl_monitor/    ← Funding rates, positions, liquidation risk
   ├── crypto_data/   ← Price data & Fear/Greed index
   ├── crypto_alert/  ← Signal scanner
   ├── crypto_news/   ← BlockBeats news
   └── crypto_report/ ← Daily/weekly reports

Background processes (pm2):
   fetcher.py         ← Caches market + HL data every 5 min
   scheduler.py       ← Monitors alerts, pushes Telegram notifications
```

---

## Quick Start

### 1. Clone & Setup (VPS)

```bash
bash <(curl -s https://raw.githubusercontent.com/Zachppt/crypto-clawie/main/setup.sh)
```

The setup script will:
- Remove the old `crypto-agent` directory
- Clone this repo to `~/crypto-clawie`
- Create a Python virtual environment and install dependencies
- Prompt you to fill in `.env`
- Update OpenClaw workspace config
- Start the scheduler via pm2

### 2. Configure `.env`

```env
# Required for trading
HL_PRIVATE_KEY=0x...
HL_WALLET_ADDRESS=0x...

# Required for Telegram alerts
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# Optional but recommended
BLOCKBEATS_API_KEY=
HL_USE_TESTNET=false
AUTONOMOUS_MODE=false
MAX_POSITION_SIZE_USD=1000
```

### 3. Verify

```bash
pm2 status                         # scheduler should be online
pm2 logs clawie-scheduler          # check for errors
cat ~/crypto-clawie/data/hl_market.json | python3 -m json.tool
```

---

## Directory Structure

```
crypto-clawie/
├── AGENTS.md              ← OpenClaw session protocol
├── SOUL.md                ← Agent identity & behavior rules
├── USER.md                ← User preferences
├── MEMORY.md              ← Long-term memory
├── HEARTBEAT.md           ← Scheduled task config
├── TOOLS.md               ← API & tool reference
├── fetcher.py             ← Data fetcher (Binance + HL + BlockBeats)
├── scheduler.py           ← Background scheduler (APScheduler)
├── setup.sh               ← One-command VPS deploy script
├── requirements.txt
├── .env.example
├── skills/
│   ├── base.py            ← BaseSkill class
│   ├── hl_trade/          ← Hyperliquid trading
│   ├── hl_monitor/        ← HL market monitoring
│   ├── crypto_data/       ← Price & market data
│   ├── crypto_alert/      ← Anomaly detection
│   ├── crypto_news/       ← News scanning
│   └── crypto_report/     ← Report generation
├── data/                  ← JSON cache (auto-created)
├── memory/                ← Daily conversation logs
├── reports/               ← Generated reports
└── logs/                  ← Script logs
```

---

## Hyperliquid Trading

All orders are signed using **EIP-712** via the official `hyperliquid-python-sdk`:

```
HL_PRIVATE_KEY → eth_account.Account → Exchange SDK → Hyperliquid API
```

- Private key loaded **lazily** — only when a trade fires
- Never written to any file or log
- Autonomous trading disabled by default (`AUTONOMOUS_MODE=false`)

### Supported Actions (via Telegram)

```
查看持仓 / 账户余额
查看资金费率
以 3 倍杠杆做多 ETH 100 USDC
平仓 ETH
设置 BTC 杠杆为 5 倍
撤销订单 [order_id]
```

---

## Scheduled Tasks

| Task | Interval | Description |
|---|---|---|
| `fetch_market` | Every 5 min | HL funding rates, OI, prices + Binance snapshot |
| `fetch_hl_account` | Every 5 min | Account positions, margin, liquidation distance |
| `check_alerts` | Every 5 min | Scan for funding/liquidation signals → Telegram |
| `fetch_news` | Every 15 min | BlockBeats flash news |
| `daily_report` | 08:00 CST | Market + account summary |
| `weekly_report` | Monday 08:00 CST | Weekly recap |

---

## Alert Thresholds

| Signal | Condition | Level |
|---|---|---|
| Funding rate high | \|rate_8h\| ≥ 0.05% | ⚠️ Warning |
| Funding rate extreme | \|rate_8h\| ≥ 0.1% | 🔴 Critical |
| OI spike | Change ≥ ±20% | ⚠️ Warning |
| Liquidation risk medium | Distance < 20% | ⚠️ Warning |
| Liquidation risk high | Distance < 10% | 🔴 Critical |
| Liquidation risk urgent | Distance < 5% | 🚨 Emergency |

---

## Environment Variables

```env
# Hyperliquid
HL_PRIVATE_KEY=              # 0x-prefixed EVM private key — NEVER commit
HL_WALLET_ADDRESS=           # Corresponding wallet address
HL_USE_TESTNET=false         # true = testnet
HL_DEFAULT_LEVERAGE=3        # Default leverage (1–50)
HL_DEFAULT_MARGIN_MODE=cross # cross | isolated
HL_FUNDING_ALERT_THRESHOLD=0.0005
HL_LIQ_ALERT_THRESHOLD=0.15

# Telegram
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
TELEGRAM_ALERT_CHAT_ID=      # Optional, defaults to TELEGRAM_CHAT_ID

# News
BLOCKBEATS_API_KEY=

# Scheduler
FETCH_INTERVAL_MIN=5
NEWS_INTERVAL_MIN=15
DAILY_REPORT_HOUR=8          # CST hour

# Safety
AUTONOMOUS_MODE=false        # true = allow auto trade execution
MAX_POSITION_SIZE_USD=1000
```

---

## PM2 Commands

```bash
pm2 status                          # check all processes
pm2 logs clawie-scheduler           # live log stream
pm2 restart clawie-scheduler        # restart scheduler
pm2 stop clawie-scheduler           # stop scheduler
```

---

## Safety Design

| Guard | Behavior |
|---|---|
| `AUTONOMOUS_MODE=false` | Agent explains trade params, waits for user confirmation |
| Max position size | Hard cap per trade (default $1,000) |
| Trade log | Every executed order saved to `memory/trade_history.json` |
| Private key | Lazy-loaded, never logged or written to file |
| Liquidation alert | Fires at <20% / <10% / <5% distance |

---

## License

MIT
