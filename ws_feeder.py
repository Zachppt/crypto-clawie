"""
ws_feeder.py — 实时价格 WebSocket 推送
使用 ccxt.pro 通过 WebSocket 订阅交易所行情，
持续更新 data/ws_prices.json，bot.py 读取该文件获得毫秒级实时价格。

用法：
  python ws_feeder.py
  nohup python ws_feeder.py > logs/ws_feeder.log 2>&1 &

依赖：ccxt >= 4.3（ccxt.pro 已内置）
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ── 配置 ──────────────────────────────────────────────────────────────────────

BASE_DIR  = Path(__file__).parent
DATA_DIR  = BASE_DIR / "data"
LOGS_DIR  = BASE_DIR / "logs"
DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

WS_CACHE = DATA_DIR / "ws_prices.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOGS_DIR / "ws_feeder.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("ws_feeder")

# 订阅的合约标的（Binance 永续，USDT 结算）
WATCH_SYMBOLS = [
    "BTC/USDT:USDT",
    "ETH/USDT:USDT",
    "SOL/USDT:USDT",
    "BNB/USDT:USDT",
    "ARB/USDT:USDT",
    "DOGE/USDT:USDT",
    "XRP/USDT:USDT",
    "AVAX/USDT:USDT",
    "OP/USDT:USDT",
    "PEPE/USDT:USDT",
]

# 本地缓存（线程安全写入的 Python dict，通过 asyncio 事件循环串行更新）
_prices: dict[str, dict] = {}

# ── 写缓存 ────────────────────────────────────────────────────────────────────

def _save():
    try:
        payload = {
            "_updated": datetime.now(timezone.utc).isoformat(),
            "prices":   _prices,
        }
        tmp = WS_CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False))
        tmp.replace(WS_CACHE)
    except Exception as e:
        log.warning(f"写缓存失败：{e}")


# ── 单标的 WebSocket 订阅 ─────────────────────────────────────────────────────

async def _watch_ticker(exchange, sym: str):
    base = sym.split("/")[0]
    log.info(f"开始订阅 {sym}")
    retry = 0
    while True:
        try:
            ticker = await exchange.watch_ticker(sym)
            _prices[base] = {
                "symbol":     base,
                "price":      ticker.get("last"),
                "bid":        ticker.get("bid"),
                "ask":        ticker.get("ask"),
                "change_24h": ticker.get("percentage"),       # percent
                "volume_24h": ticker.get("quoteVolume"),      # USDT
                "high_24h":   ticker.get("high"),
                "low_24h":    ticker.get("low"),
                "ts":         ticker.get("timestamp"),
            }
            retry = 0
        except asyncio.CancelledError:
            break
        except Exception as e:
            retry += 1
            wait = min(5 * retry, 60)
            log.warning(f"{sym} 订阅异常（第{retry}次）：{e}，{wait}s 后重试")
            await asyncio.sleep(wait)


# ── 定期写盘（每 500ms 聚合一次，避免频繁 IO）────────────────────────────────

async def _flush_loop():
    while True:
        await asyncio.sleep(0.5)
        if _prices:
            _save()


# ── 主入口 ────────────────────────────────────────────────────────────────────

async def main():
    try:
        import ccxt.pro as ccxtpro
    except ImportError:
        log.error(
            "ccxt.pro 未找到。请确认 ccxt >= 4.3：pip install 'ccxt>=4.3.0'\n"
            "如果已安装仍报错，尝试：pip install --upgrade ccxt"
        )
        sys.exit(1)

    exchange = ccxtpro.binance({
        "options": {"defaultType": "future"},
        "newUpdates": True,
    })

    log.info(f"WebSocket 推送启动，订阅 {len(WATCH_SYMBOLS)} 个标的")

    tasks = [
        asyncio.create_task(_watch_ticker(exchange, sym))
        for sym in WATCH_SYMBOLS
    ]
    tasks.append(asyncio.create_task(_flush_loop()))

    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        log.info("收到 Ctrl+C，关闭 WebSocket 连接...")
    finally:
        for t in tasks:
            t.cancel()
        await exchange.close()
        log.info("ws_feeder 已退出")


if __name__ == "__main__":
    asyncio.run(main())
