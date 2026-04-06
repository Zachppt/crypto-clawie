# TOOLS.md - Tools & API Reference

## API Keys（从 .env 读取）

- `HL_PRIVATE_KEY` — Hyperliquid 交易签名
- `HL_WALLET_ADDRESS` — HL 查询地址
- `TELEGRAM_BOT_TOKEN` — 推送通知
- `BLOCKBEATS_API_KEY` — 快讯数据

## 数据 API（免费，无需 Key）

| 来源 | 用途 | 端点 |
|---|---|---|
| Hyperliquid Info | 价格/资金费率/持仓 | `https://api.hyperliquid.xyz/info` |
| Binance 现货 | 价格补充 | `https://api.binance.com/api/v3` |
| Binance 合约 | 资金费率/OI 参考 | `https://fapi.binance.com/fapi/v1` |
| CoinGecko | 市值/概览 | `https://api.coingecko.com/api/v3` |
| Fear & Greed | 市场情绪 | `https://api.alternative.me/fng` |

## 本地缓存文件（优先读取）

| 文件 | 内容 | 更新频率 |
|---|---|---|
| `data/hl_market.json` | HL 资金费率、OI、价格 | 每 5 分钟 |
| `data/hl_account.json` | 持仓、余额、保证金 | 每 5 分钟 |
| `data/market_snapshot.json` | Binance 价格 + 恐慌贪婪 | 每 5 分钟 |
| `data/news_cache.json` | BlockBeats 最新快讯 | 每 15 分钟 |

## Skills（Python 模块）

| Skill | 调用方式 | 主要功能 |
|---|---|---|
| `hl_trade` | `python -m skills.hl_trade` | 开仓/平仓/杠杆/撤单 |
| `hl_monitor` | `python -m skills.hl_monitor` | 资金费率/持仓/爆仓风险 |
| `crypto_data` | `python -m skills.crypto_data` | 价格/OI/市场概览 |
| `crypto_alert` | `python -m skills.crypto_alert` | 信号扫描 |
| `crypto_news` | `python -m skills.crypto_news` | 新闻搜索 |
| `crypto_report` | `python -m skills.crypto_report` | 报告生成 |

## 常用命令

```bash
# 手动抓取数据
cd ~/crypto-clawie && source venv/bin/activate && python fetcher.py

# 查看调度器状态
pm2 status

# 查看实时日志
pm2 logs clawie-scheduler --lines 50

# 查看数据文件
cat ~/crypto-clawie/data/hl_market.json | python3 -m json.tool
cat ~/crypto-clawie/data/hl_account.json | python3 -m json.tool
```

## BlockBeats API

```bash
curl -H "Authorization: YOUR_KEY" \
  "https://api.theblockbeats.news/v1/open-api/open-flash?size=10&page=1&type=push"
```
