# 🦞 crypto-clawie

> Hyperliquid perpetual contract assistant — multi-exchange data aggregation, AI Agent collaboration, funding rate monitoring, auto-alerts, and Telegram delivery.

**Version:** 3.0.0 · **Python:** ≥ 3.9 · **License:** MIT · **[中文文档](README_CN.md)**

---

## Architecture

Two roles co-exist in the same Telegram group:

```
Telegram Group
├── @ScriptBot  (bot.py)              ← slash commands → structured data
│     Reads local skill cache
│     Price refresh every 60s
│     Full market refresh every 5min
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
# Required
HL_PRIVATE_KEY=0x...
HL_WALLET_ADDRESS=0x...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...

# Recommended
HL_USE_TESTNET=true          # Start on testnet
AUTONOMOUS_MODE=false        # Manual confirm all trades
MAX_POSITION_SIZE_USD=500
MAX_DAILY_LOSS_PCT=5
```

### 3. Verify

```bash
pm2 status
pm2 logs clawie-scheduler
```

---

## Command Reference

### Account
| Command | Description |
|---|---|
| `/position` | Holdings and account balance |
| `/liq` | Liquidation risk assessment |

### Market (Hyperliquid)
| Command | Description |
|---|---|
| `/market` | Overview: funding Top5 + sentiment + account |
| `/funding` | Funding rate leaderboard Top 20 |
| `/funding BTC` | Single asset funding details |
| `/oi` / `/oi BTC` | Open interest Top 10 / single asset |
| `/price ETH` | Real-time price (auto-fallback to live Binance if cache stale) |
| `/fng` | Fear & Greed index |
| `/BTC` `/ETH` `/SOL` | Quick: price + funding for any HL symbol |

### Multi-Exchange (Binance · OKX · Bybit · Gate.io · Bitget · HL)
| Command | Description |
|---|---|
| `/compare BTC` | Cross-exchange price comparison |
| `/exfunding BTC` | Cross-exchange funding rate comparison |
| `/vol BTC` | Cross-exchange volume comparison |
| `/divergence` | Scan price divergence across major coins |
| `/listings SOL` | Check which exchanges list a token (spot + perp) |

### News
| Command | Description |
|---|---|
| `/news` | Latest BlockBeats flash news |
| `/hlnews` | HL-related news only |

### Signals & Reports
| Command | Description |
|---|---|
| `/alerts` | Multi-factor signal scan with confidence scores |
| `/report` | Daily market + account summary |
| `/weekly` | Weekly recap |

### Focus Tracking
| Command | Description |
|---|---|
| `/focus SOL` | Start auto-tracking SOL every 15 min |
| `/focus SOL 30` | Custom interval (minutes) |
| `/focus report` | Generate report immediately |
| `/focus status` | View tracking config |
| `/focus cancel` | Stop tracking |

### Market Maker Phase Analysis
| Command | Description |
|---|---|
| `/mm SOL` | Analyze MM phase for a coin |
| `/mm scan` | Scan top coins by funding rate |

Phases: 📦 Accumulation · 🌀 Wash Trading · 📤 Distribution · 🚀 Pump Setup

### Exchange Net Flow
| Command | Description |
|---|---|
| `/netflow` | Last 24h CEX net flow (USDT ERC-20) |
| `/netflow 12` | Last 12 hours |
| `/netflow signal BTC` | Combined flow + funding signal |
| `/netflow wallets` | List monitored exchange hot wallets |

### Strategy Wizard
| Command | Description |
|---|---|
| `/strategy new` | 6-step interactive strategy setup |
| `/strategy show` | View current strategy |
| `/strategy on` / `off` | Enable / pause |
| `/strategy delete` | Delete strategy |

### Agent (Rule-Based Scoring)
| Command | Description |
|---|---|
| `/agent scan` | Multi-factor market scan |
| `/agent status` | Agent state + recent decisions |
| `/agent history` | Decision history |
| `/agent decide` | Generate executable decision |

### Context Prep for AI Agent
| Command | Description |
|---|---|
| `/ask <question>` | Assemble market context + question for AI Agent |
| `/deep BTC` | Deep context: MM phase + cross-exchange funding |
| `/advice` | Assemble position context for AI Agent advice |

### Trading
| Command | Description |
|---|---|
| `/trade open ETH long 100` | Open long ETH $100 |
| `/trade open BTC short 200 3` | Open short BTC $200, 3x leverage |
| `/trade close ETH` | Close position |
| `/trade leverage ETH 5 cross` | Set leverage |
| `/override_circuit` | Override daily loss circuit breaker (1 hour) |

### Arbitrage & Strategy
| Command | Description |
|---|---|
| `/arb scan` | Scan funding arbitrage opportunities |
| `/arb open BTC 500` | Record arb position ($500 USDC) |
| `/arb status` | View active arb positions + estimated income |
| `/arb close BTC` | Close arb record |
| `/grid BTC 90000 100000 10 50` | Create grid (low high grids size_per_grid) |
| `/grid` | View active grids |
| `/backtest` | Run funding arb backtest on synthetic data |

### On-chain Monitoring
| Command | Description |
|---|---|
| `/watch add ETH 0x... label` | Monitor an address |
| `/watch list` | List monitored addresses |
| `/watch ETH 0x...` | View recent transactions |
| `/watch remove ETH 0x...` | Remove monitoring |
| `/chains` | On-chain monitoring overview |

---

## AI Agent Collaboration

The AI Agent (OpenClaw) runs independently in the same Telegram group. It can read local `data/*.json` cache files and browse the internet.

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
| Liquidation alert | Three levels: <20% / <10% / <5% distance, SQLite-persisted |
| Alert dedup | SQLite-backed with TTL: funding 8h, liq 4h, news 24h |

---

## Scheduled Tasks

| Task | Interval | Description |
|---|---|---|
| Price fast refresh | Every 60s | Binance prices only, keeps quotes fresh |
| Full data fetch | Every 5min | HL market + account + FNG + news (async) |
| Funding alert | Every 5min | Scans after fresh data lands |
| Liquidation alert | Every 5min | Checks all position distances |
| News check | Every 15min | BlockBeats HL keyword filter |
| Focus tracker | Every 5min | Checks focus.json, pushes if interval elapsed |
| Daily report | 08:00 CST | Market + account summary |
| Weekly report | Mon 08:00 CST | Weekly recap |
| DB cleanup | Every 1 hour | Purge expired alert records |

---

## License

MIT
