# 🤖 crypto-clawie

> A Hyperliquid perpetual contract trading agent — monitors funding rates, executes perp orders, detects liquidation risk, runs funding arbitrage strategies, and delivers real-time alerts via Telegram.

**Version:** 2.0.0 · **Python:** ≥ 3.10 · **License:** MIT

---

## What's New in v2

| Area | Upgrade |
|---|---|
| ⚡ **Performance** | Async concurrent fetcher (aiohttp) — 4 data sources in parallel, ~3s vs ~12s |
| 🔔 **Alerts** | SQLite-backed deduplication — survives restarts, no more alert spam |
| 🎯 **Signals** | Multi-factor confidence scoring — funding + OI + price momentum confluence |
| 💰 **Strategy** | Funding rate arbitrage skill — scan, open, track, close delta-neutral positions |
| 📊 **Grid** | Grid trading manager — place layered limit orders across a price range |
| 🧪 **Backtest** | Backtesting engine for strategy parameter validation |
| 🛡️ **Safety** | Daily loss circuit breaker — auto-blocks new positions after loss threshold |
| 🔧 **UX** | Telegram Inline confirmation flow, clear Binance hedge quantities, honest pending state |

---

## Architecture

```
Telegram ↔ bot.py (command router)
              │
              ├── skills/hl_monitor     ← funding rates, OI, positions, liquidation
              ├── skills/hl_trade       ← open/close/leverage/cancel (EIP-712)
              ├── skills/funding_arb    ← delta-neutral funding arbitrage
              ├── skills/hl_grid        ← grid order management
              ├── skills/crypto_alert   ← multi-factor signal scoring
              ├── skills/crypto_data    ← price + Fear & Greed
              ├── skills/crypto_news    ← BlockBeats news
              ├── skills/crypto_report  ← daily/weekly reports
              └── backtest/engine       ← offline strategy backtesting

Background (pm2):
  scheduler.py  ← APScheduler: fetch every 5m, alerts, daily/weekly reports
  fetcher.py    ← async aiohttp: HL + Binance + FNG + BlockBeats concurrent
  db.py         ← SQLite alert deduplication state
```

---

## Quick Start

### 1. Deploy (VPS one-liner)

```bash
bash <(curl -s https://raw.githubusercontent.com/Zachppt/crypto-clawie/main/setup.sh)
```

### 2. Configure `.env`

```env
# Required
HL_PRIVATE_KEY=0x...
HL_WALLET_ADDRESS=0x...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...

# Recommended
HL_USE_TESTNET=true          # Start on testnet
AUTONOMOUS_MODE=false        # Manual confirm all trades
MAX_POSITION_SIZE_USD=500
MAX_DAILY_LOSS_PCT=5         # Circuit breaker threshold
```

### 3. Verify

```bash
pm2 status
pm2 logs clawie-scheduler
```

---

## Telegram Commands

### Account
| Command | Description |
|---|---|
| `/position` | Holdings and account balance |
| `/liq` | Liquidation risk assessment |

### Market
| Command | Description |
|---|---|
| `/market` | Overview: funding + sentiment + account |
| `/funding` | Funding rate leaderboard Top 20 |
| `/funding BTC` | Single asset funding details |
| `/oi` | Open interest Top 10 |
| `/price ETH` | Real-time price |
| `/fng` | Fear & Greed index |
| `/BTC` `/ETH` `/SOL` | Quick: price + funding for any HL symbol |

### Alerts & Reports
| Command | Description |
|---|---|
| `/alerts` | Multi-factor signal scan with confidence scores |
| `/report` | Daily market + account summary |
| `/weekly` | Weekly recap |
| `/news` | Latest BlockBeats flash news |
| `/hlnews` | HL-related news only |

### Strategy
| Command | Description |
|---|---|
| `/arb scan` | Scan funding arbitrage opportunities |
| `/arb open BTC 500` | Record arb position ($500 USDC) |
| `/arb status` | View active arb positions + estimated income |
| `/arb close BTC` | Close arb position record |
| `/grid BTC 90000 100000 10 50` | Create grid (low high count size_per_grid_usd) |
| `/grid` | View all active grids |
| `/grid cancel <grid_id>` | Cancel a grid |
| `/backtest` | Run funding arb backtest on synthetic data |

---

## Funding Rate Arbitrage

Delta-neutral strategy: short HL perp + long Binance spot = collect funding with no directional exposure.

**Entry:** `|funding_8h| ≥ 0.05%` (≈ 54% annualized)  
**Exit:** `|funding_8h| ≤ 0.01%`

```
/arb scan               → find opportunities
/arb open BTC 500       → record HL short + shows exact Binance buy qty
/arb status             → track income, see exit signal
/arb close BTC          → close record + shows exact Binance sell qty
```

> Note: The HL perp leg executes automatically (requires `AUTONOMOUS_MODE=true`). The Binance spot hedge is always manual — the bot shows the exact quantity and price.

---

## Alert Thresholds

| Signal | Condition | Confidence |
|---|---|---|
| Funding extreme | `\|rate_8h\| ≥ 0.1%` | Up to 100% |
| Funding high | `\|rate_8h\| ≥ 0.05%` | 40–70% |
| OI confirmation | OI > $50M (same direction) | +20% |
| Price momentum | 24h move > 3% (same direction) | +10% |
| Low liquidity discount | UTC 16:00–22:00 (CST 00:00–06:00) | −10% |
| Liquidation risk critical | Distance < 5% | 100% |
| Liquidation risk high | Distance < 10% | 70% |
| Liquidation risk medium | Distance < 20% | 40% |

Only signals with **confidence ≥ 40%** are shown.

---

## Safety Design

| Guard | Behavior |
|---|---|
| `AUTONOMOUS_MODE=false` | Shows trade params + directs to manual execution |
| `MAX_POSITION_SIZE_USD` | Hard cap per trade |
| `MAX_DAILY_LOSS_PCT` | Circuit breaker: blocks new positions after daily loss limit |
| Private key | Lazy-loaded, never written to any file or log |
| Liquidation alert | Fires at <20% / <10% / <5% distance, persisted in SQLite |
| Alert deduplication | SQLite-backed, survives scheduler restarts (8h TTL funding, 4h liq, 24h news) |

---

## Scheduled Tasks

| Task | Interval | Description |
|---|---|---|
| `fetch` (async) | Every 5 min | HL + Binance + FNG + news concurrent |
| `funding_alert` | Every 5 min (+60s offset) | Scan after fresh data lands |
| `liq_alert` | Every 5 min (+60s offset) | Liquidation risk check |
| `news_check` | Every 15 min | BlockBeats HL-keyword filter |
| `daily_report` | 08:00 CST | Market + account summary |
| `weekly_report` | Mon 08:00 CST | Weekly recap |
| `db_cleanup` | Every 1 hour | Purge expired alert records |

---

## Environment Variables

```env
# Hyperliquid
HL_PRIVATE_KEY=              # 0x-prefixed EVM private key
HL_WALLET_ADDRESS=           # Wallet address
HL_USE_TESTNET=false
HL_DEFAULT_LEVERAGE=3
HL_DEFAULT_MARGIN_MODE=cross
HL_FUNDING_ALERT_THRESHOLD=0.0005
HL_LIQ_ALERT_THRESHOLD=0.15

# Telegram
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
TELEGRAM_ALERT_CHAT_ID=      # Optional separate alert channel

# News
BLOCKBEATS_API_KEY=

# Scheduler
FETCH_INTERVAL_MIN=5
NEWS_INTERVAL_MIN=15
DAILY_REPORT_HOUR=8          # CST hour

# Safety
AUTONOMOUS_MODE=false
MAX_POSITION_SIZE_USD=1000
MAX_DAILY_LOSS_PCT=5         # Circuit breaker: % of account value
```

---

## PM2 Commands

```bash
pm2 status
pm2 logs clawie-scheduler
pm2 logs clawie-bot
pm2 restart clawie-scheduler
pm2 save                     # persist across reboots
```

---

## License

MIT
