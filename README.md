# 🦞 crypto-clawie

> Universal crypto trading assistant — cross-exchange data aggregation, AI Agent collaboration, funding rate monitoring, auto-alerts, and Telegram delivery. Hyperliquid is one supported trading venue among many.

**Version:** 4.0.0 · **Python:** ≥ 3.9 · **License:** MIT · **[中文文档](README_CN.md)**

---

## Architecture

Two roles co-exist in the same Telegram group:

```
Telegram Group
├── @ScriptBot  (bot.py)              ← slash commands → structured data
│     Binance/OKX/Bybit/HL via ccxt
│     WebSocket real-time prices (ws_feeder.py)
│     Full market refresh every 5 min
│
└── @AI Agent   (OpenClaw)            ← @mention → reasoning + web search
      Reads data/*.json cache
      User-configured LLM (Claude / GPT / Gemini / local)
      Can access real-time internet data
```

| Mode | How | Best for |
|---|---|---|
| Direct data | `/command` | Price, funding, positions, alerts |
| AI analysis | `@AIAgent question` or `/ask /deep /advice` | Reasoning, judgment, synthesis |

---

## Quick Start

### 1. Deploy (VPS one-liner)

```bash
bash <(curl -s https://raw.githubusercontent.com/Zachppt/crypto-clawie/main/setup.sh)
```

### 2. Configure `.env`

```env
# ── Telegram (required) ─────────────────────────────────────
TELEGRAM_BOT_TOKEN=           # from @BotFather
TELEGRAM_CHAT_ID=             # from @userinfobot

# ── Topic IDs (optional, for supergroup topics) ─────────────
TELEGRAM_TOPIC_ALERT=
TELEGRAM_TOPIC_MARKET=
TELEGRAM_TOPIC_POSITION=
TELEGRAM_TOPIC_TRADE=

# ── Trading exchange (set one or more) ──────────────────────
TRADING_EXCHANGE=hyperliquid  # binance | okx | bybit | hyperliquid

# Hyperliquid (for HL trading + position monitoring)
HL_PRIVATE_KEY=0x...
HL_WALLET_ADDRESS=0x...
HL_USE_TESTNET=true

# Binance (for Binance trading)
BINANCE_API_KEY=
BINANCE_SECRET_KEY=

# OKX
OKX_API_KEY=
OKX_SECRET_KEY=
OKX_PASSPHRASE=

# Bybit
BYBIT_API_KEY=
BYBIT_SECRET_KEY=

# ── Recommended ─────────────────────────────────────────────
AUTONOMOUS_MODE=false         # false = manual confirm all trades
MAX_POSITION_SIZE_USD=500
MAX_DAILY_LOSS_PCT=5

# ── News ────────────────────────────────────────────────────
BLOCKBEATS_API_KEY=           # BlockBeats flash news API key

# ── On-chain monitoring ──────────────────────────────────────
ETHERSCAN_API_KEY=
```

> **Note:** Market data commands (`/market`, `/funding`, `/alerts`, `/compare` etc.) require **no API keys** — they use Binance/OKX public APIs. Keys are only needed for account and trading commands.

### 3. Start

```bash
# With pm2 (recommended)
pm2 start scheduler.py --name clawie-scheduler --interpreter venv/bin/python3
pm2 start bot.py       --name clawie-bot       --interpreter venv/bin/python3
pm2 start ws_feeder.py --name clawie-ws        --interpreter venv/bin/python3
pm2 save

# Or with nohup
nohup venv/bin/python3 bot.py       > logs/bot.log       2>&1 &
nohup venv/bin/python3 scheduler.py > logs/scheduler.log 2>&1 &
nohup venv/bin/python3 ws_feeder.py > logs/ws.log        2>&1 &
```

### 4. Verify

```bash
pm2 status
pm2 logs clawie-bot --lines 30
```

---

## Command Reference

### Account (requires API key)
| Command | Description |
|---|---|
| `/position` | Holdings and balance (reads `TRADING_EXCHANGE`) |
| `/position binance` | Specify exchange: binance / okx / bybit / hl |
| `/liq` | Liquidation risk (Hyperliquid only) |

### Market (no config needed — cross-exchange public APIs)
| Command | Description |
|---|---|
| `/market` | Main coin prices + Fear & Greed index |
| `/funding` | Cross-exchange funding rate leaderboard |
| `/funding BTC` | Single asset: Binance / OKX / Bybit / HL comparison |
| `/oi` | Hyperliquid full-market OI ranking Top 10 |
| `/price ETH` | Real-time price (WebSocket → Binance fallback) |
| `/fng` | Fear & Greed index |
| `/BTC` `/ETH` `/SOL` | Quick: cross-exchange price + top funding rates |

### Cross-Exchange Aggregation
| Command | Description |
|---|---|
| `/compare BTC` | Cross-exchange price + volume + spread (Binance/OKX/Bybit/Gate/Bitget/HL) |
| `/divergence` | Scan price divergence across major coins |
| `/listings SOL` | Check which exchanges list a token (spot + perp) |

### News
| Command | Description |
|---|---|
| `/news` | Latest BlockBeats flash news (10 items) |
| `/news hl` | HL-related news only |

### Signals & Reports
| Command | Description |
|---|---|
| `/alerts` | Multi-factor signal scan — Binance perp universe, 200+ assets, no key needed |
| `/report` | Daily market + account summary |
| `/weekly` | Weekly recap |

### Market Maker Phase Analysis (cross-exchange: Binance + OKX + Bybit + HL)
| Command | Description |
|---|---|
| `/mm BTC` | MM phase analysis — funding + OI distribution + phase scoring |
| `/mm BTC cross` | Cross-exchange data only |
| `/mm scan` | Full-market quick scan |

Phases: 📦 Accumulation · 🌀 Wash Trading · 📤 Distribution · 🚀 Pump Setup

### Technical Analysis
| Command | Description |
|---|---|
| `/ta BTC` | 1h full analysis (RSI + MA + BB + MACD) |
| `/ta ETH 4h` | Specify timeframe |
| `/ta SOL 1d signal` | Signal summary only |

### Focus Tracking
| Command | Description |
|---|---|
| `/track SOL` | Auto-track SOL every 15 min |
| `/track SOL 30` | Custom interval (minutes) |
| `/track report` | Generate deep report immediately (uses AI Agent) |
| `/track status` | View tracking config |
| `/track cancel` | Stop tracking |

### Exchange Net Flow
| Command | Description |
|---|---|
| `/netflow` | Last 24h CEX net flow |
| `/netflow signal BTC` | Combined flow + funding signal |
| `/netflow wallets` | List monitored exchange hot wallets |

### Strategy Wizard
| Command | Description |
|---|---|
| `/strategy new` | 6-step interactive strategy setup |
| `/strategy show` | View current strategy |
| `/strategy on` / `off` | Enable / pause |
| `/strategy delete` | Delete strategy |

### Agent (Rule-Based Scoring — Binance perp data, live)
| Command | Description |
|---|---|
| `/alerts` | Multi-factor market scan (= primary signal command) |
| `/agent status` | Scoring weights + recent decisions |
| `/agent history` | Decision history |
| `/agent decide` | Generate executable decision |

**Scoring factors:**

| Factor | Weight |
|---|---|
| Funding rate magnitude | up to 50% |
| 24h volume confirmation | up to 20% |
| Price momentum alignment | up to 20% |
| OKX cross-exchange confirmation | up to 15% |

### Context Prep for AI Agent
| Command | Description |
|---|---|
| `/ask <question>` | Assemble market context + question for AI Agent |
| `/deep BTC` | Deep context: MM phase + cross-exchange funding + TA signal |
| `/advice` | Assemble position context for AI Agent advice |

### Trading (requires API key)
| Command | Description |
|---|---|
| `/trade open ETH long 100` | Open long ETH $100 (default exchange) |
| `/trade open BTC short 200 binance` | Open short on Binance |
| `/trade close ETH` | Close position |
| `/trade leverage ETH 5 cross` | Set leverage |
| `/override_circuit` | Override daily loss circuit breaker (1 hour) |

### Arbitrage & Grid
| Command | Description |
|---|---|
| `/arb scan` | Scan funding arbitrage opportunities |
| `/arb open BTC 500` | Record arb position |
| `/arb status` | View active arb positions + estimated income |
| `/grid BTC 90000 100000 10 50` | Create grid (Hyperliquid) |
| `/grid` | View active grids |

### On-chain Monitoring
| Command | Description |
|---|---|
| `/watch add ETH 0x... label` | Monitor an address |
| `/watch list` | List monitored addresses |
| `/watch ETH 0x...` | View recent transactions |
| `/chains` | On-chain monitoring overview |

### System
| Command | Description |
|---|---|
| `/status` | Bot status + all exchange API key config |
| `/help` | Full command list |

---

## AI Agent Collaboration

The AI Agent (OpenClaw) runs independently in the same Telegram group.

**Direct mention:**
```
@AIBot Is the SOL funding rate good for arbitrage right now?
→ AI reads local cache + internet, responds with analysis
```

**Script-assisted (richer context):**
```
/deep SOL          → bot formats all SOL data into a context block
@AIBot Based on the above data, long or short?
→ AI reasons from the structured context
```

---

## Safety Design

| Guard | Behavior |
|---|---|
| `AUTONOMOUS_MODE=false` | Inline keyboard confirmation for all trades |
| `MAX_POSITION_SIZE_USD` | Hard cap per trade |
| `MAX_DAILY_LOSS_PCT` | Circuit breaker: blocks new positions after daily loss limit |
| Private key | Lazy-loaded, never written to logs, signed locally |
| Liquidation alert | Three levels: <20% / <10% / <5% distance |
| Alert dedup | SQLite-backed with TTL: funding 8h, liq 4h, news 24h |

---

## Scheduled Tasks

| Task | Interval | Description |
|---|---|---|
| Price fast refresh | Every 60s | Binance prices only |
| Full data fetch | Every 5min | Market snapshot + account + FNG + news |
| Funding alert | Every 5min | Scans after fresh data lands |
| Liquidation alert | Every 5min | Checks all position distances |
| News check | Every 15min | BlockBeats keyword filter |
| Focus tracker | Every 5min | Pushes report if interval elapsed |
| Daily report | 08:00 CST | Market + account summary |
| Weekly report | Mon 08:00 CST | Weekly recap |
| DB cleanup | Every 1 hour | Purge expired alert records |

---

## License

MIT
