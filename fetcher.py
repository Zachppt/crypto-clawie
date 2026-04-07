"""
fetcher.py — 异步数据抓取器
使用 asyncio + aiohttp 并发抓取 Binance、Hyperliquid、CoinGecko、BlockBeats 数据。

用法：
  python fetcher.py          # 并发抓取全量数据
  python fetcher.py --task hl_market
  python fetcher.py --task hl_account
  python fetcher.py --task market_snapshot
  python fetcher.py --task news
"""

import os
import sys
import json
import asyncio
import logging
import argparse
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

load_dotenv()

# ── 目录配置 ────────────────────────────────────────────────────────────────

BASE_DIR  = Path(__file__).parent
DATA_DIR  = BASE_DIR / "data"
LOGS_DIR  = BASE_DIR / "logs"
DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

# ── 日志 ─────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOGS_DIR / "fetcher.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# ── 常量 ─────────────────────────────────────────────────────────────────────

HL_USE_TESTNET  = os.getenv("HL_USE_TESTNET", "false").lower() == "true"
HL_API_URL      = "https://api.hyperliquid-testnet.xyz" if HL_USE_TESTNET else "https://api.hyperliquid.xyz"
HL_WALLET_ADDR  = os.getenv("HL_WALLET_ADDRESS", "")
BLOCKBEATS_KEY  = os.getenv("BLOCKBEATS_API_KEY", "")

BINANCE_SPOT_URL = "https://api.binance.com/api/v3"
FNG_URL          = "https://api.alternative.me/fng/?limit=1"

WATCHED_SYMBOLS = ["BTC", "ETH", "SOL", "BNB", "ARB", "OP", "AVAX", "DOGE", "PEPE", "WIF"]

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)
RETRY_DELAY     = 2  # 秒

# ── 工具函数 ─────────────────────────────────────────────────────────────────

def _save(filename: str, data: dict | list):
    """将数据连同时间戳保存到 data/ 目录。"""
    path    = DATA_DIR / filename
    wrapped = {"_updated": datetime.now(timezone.utc).isoformat(), "data": data}
    with open(path, "w") as f:
        json.dump(wrapped, f, ensure_ascii=False, indent=2)
    log.info(f"Saved {filename} ({len(str(data))} bytes)")


async def _get(session: aiohttp.ClientSession, url: str,
               params: dict = None, headers: dict = None) -> dict | list | None:
    """异步 GET，失败后重试一次。"""
    for attempt in range(2):
        try:
            async with session.get(url, params=params, headers=headers,
                                   timeout=REQUEST_TIMEOUT) as resp:
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except Exception as e:
            if attempt == 0:
                log.warning(f"GET {url} failed (attempt 1): {e}, retrying...")
                await asyncio.sleep(RETRY_DELAY)
            else:
                log.warning(f"GET {url} failed (attempt 2): {e}")
                return None


async def _post(session: aiohttp.ClientSession, url: str,
                payload: dict) -> dict | None:
    """异步 POST，失败后重试一次。"""
    for attempt in range(2):
        try:
            async with session.post(url, json=payload,
                                    timeout=REQUEST_TIMEOUT) as resp:
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except Exception as e:
            if attempt == 0:
                log.warning(f"POST {url} failed (attempt 1): {e}, retrying...")
                await asyncio.sleep(RETRY_DELAY)
            else:
                log.warning(f"POST {url} failed (attempt 2): {e}")
                return None

# ── Hyperliquid 市场数据 ───────────────────────────────────────────────────────

async def _fetch_hl_market_async(session: aiohttp.ClientSession) -> dict:
    """抓取 HL 全市场：价格、资金费率、未平仓量。"""
    result = await _post(session, f"{HL_API_URL}/info", {"type": "metaAndAssetCtxs"})
    if not result or len(result) < 2:
        log.error("fetch_hl_market: empty response")
        return {}

    meta, ctxs = result[0], result[1]
    universe   = meta.get("universe", [])

    assets = []
    for i, asset in enumerate(universe):
        if i >= len(ctxs):
            break
        ctx = ctxs[i]
        try:
            funding    = float(ctx.get("funding", 0))
            oi         = float(ctx.get("openInterest", 0))
            mark_px    = float(ctx.get("markPx") or ctx.get("midPx") or 0)
            prev_day   = ctx.get("prevDayPx")
            change_pct = (
                (mark_px - float(prev_day)) / float(prev_day) * 100
                if prev_day and float(prev_day) else 0
            )
            assets.append({
                "symbol":             asset["name"],
                "index":              i,
                "sz_decimals":        asset.get("szDecimals", 3),
                "mark_price":         mark_px,
                "change_24h_pct":     round(change_pct, 2),
                "funding_8h":         funding,
                "funding_annualized": round(funding * 3 * 365 * 100, 2),
                "open_interest":      oi,
            })
        except (ValueError, TypeError):
            continue

    top_funding = sorted(assets, key=lambda x: abs(x["funding_8h"]), reverse=True)[:10]
    data = {"assets": assets, "top_funding": top_funding, "total_assets": len(assets)}
    _save("hl_market.json", data)
    return data


# ── Hyperliquid 账户数据 ───────────────────────────────────────────────────────

async def _fetch_hl_account_async(session: aiohttp.ClientSession) -> dict:
    """抓取 HL 账户持仓、余额、爆仓风险。需要 HL_WALLET_ADDRESS。"""
    if not HL_WALLET_ADDR:
        log.info("fetch_hl_account: HL_WALLET_ADDRESS not set, skip")
        return {}

    result = await _post(session, f"{HL_API_URL}/info",
                         {"type": "clearinghouseState", "user": HL_WALLET_ADDR})
    if not result:
        return {}

    margin        = result.get("marginSummary", {})
    account_value = float(margin.get("accountValue", 0))
    margin_used   = float(margin.get("totalMarginUsed", 0))
    ntl_pos       = float(margin.get("totalNtlPos", 0))

    positions  = []
    liq_alerts = []

    for pos_entry in result.get("assetPositions", []):
        p    = pos_entry.get("position", {})
        size = float(p.get("szi", "0"))
        if size == 0:
            continue

        entry_px = float(p.get("entryPx") or 0)
        liq_px   = float(p.get("liquidationPx") or 0)
        unreal   = float(p.get("unrealizedPnl") or 0)
        side     = "long" if size > 0 else "short"
        coin     = p.get("coin", "")
        leverage = p.get("leverage", {})
        lev_val  = leverage.get("value", 1) if isinstance(leverage, dict) else 1

        dist_pct = 0.0
        if liq_px and entry_px:
            if side == "long":
                dist_pct = (entry_px - liq_px) / entry_px * 100
            else:
                dist_pct = (liq_px - entry_px) / entry_px * 100
            dist_pct = max(dist_pct, 0)

        pos = {
            "symbol":          coin,
            "side":            side,
            "size":            abs(size),
            "entry_price":     entry_px,
            "liq_price":       liq_px,
            "dist_to_liq_pct": round(dist_pct, 2),
            "unrealized_pnl":  round(unreal, 4),
            "leverage":        lev_val,
        }
        positions.append(pos)

        if dist_pct < 5:
            liq_alerts.append({"symbol": coin, "level": "CRITICAL", "dist_pct": dist_pct})
        elif dist_pct < 10:
            liq_alerts.append({"symbol": coin, "level": "HIGH",     "dist_pct": dist_pct})
        elif dist_pct < 20:
            liq_alerts.append({"symbol": coin, "level": "MEDIUM",   "dist_pct": dist_pct})

    data = {
        "account_value_usdc":  round(account_value, 2),
        "margin_used_usdc":    round(margin_used, 2),
        "total_position_usdc": round(ntl_pos, 2),
        "margin_ratio":        round(margin_used / account_value * 100, 2) if account_value else 0,
        "positions":           positions,
        "liq_alerts":          liq_alerts,
    }
    _save("hl_account.json", data)
    return data


# ── Binance + 市场快照 ────────────────────────────────────────────────────────

async def _fetch_market_snapshot_async(session: aiohttp.ClientSession) -> dict:
    """并发抓取 Binance 价格和恐慌贪婪指数。"""
    pairs = [s + "USDT" for s in WATCHED_SYMBOLS]

    ticker_task = _get(session, f"{BINANCE_SPOT_URL}/ticker/24hr")
    fng_task    = _get(session, FNG_URL)
    ticker, fng_raw = await asyncio.gather(ticker_task, fng_task)

    prices = {}
    if ticker:
        for t in ticker:
            sym = t["symbol"]
            if sym in pairs:
                coin = sym.replace("USDT", "")
                prices[coin] = {
                    "price":       float(t["lastPrice"]),
                    "change_24h":  float(t["priceChangePercent"]),
                    "volume_usdt": float(t["quoteVolume"]),
                }

    fng = {}
    if fng_raw and fng_raw.get("data"):
        d   = fng_raw["data"][0]
        fng = {"value": int(d["value"]), "label": d["value_classification"]}

    data = {"prices": prices, "fear_greed": fng}
    _save("market_snapshot.json", data)
    return data


# ── 新闻 ─────────────────────────────────────────────────────────────────────

async def _fetch_news_async(session: aiohttp.ClientSession) -> list:
    """抓取 BlockBeats 快讯。"""
    if not BLOCKBEATS_KEY:
        log.info("fetch_news: BLOCKBEATS_API_KEY not set, skip")
        return []

    result = await _get(
        session,
        "https://api.theblockbeats.news/v1/open-api/open-flash",
        params={"size": 20, "page": 1, "type": "push"},
        headers={"Authorization": BLOCKBEATS_KEY},
    )
    items = []
    if result and result.get("data", {}).get("data"):
        for item in result["data"]["data"]:
            items.append({
                "title":   item.get("title", ""),
                "content": item.get("content", ""),
                "time":    item.get("add_time", ""),
            })

    _save("news_cache.json", items)
    return items


# ── 并发主入口 ────────────────────────────────────────────────────────────────

async def _fetch_all_async():
    """并发抓取所有数据源。"""
    import time
    log.info("=== Full fetch starting (async) ===")
    t0 = time.time()

    async with aiohttp.ClientSession() as session:
        tasks = [
            _fetch_hl_market_async(session),
            _fetch_market_snapshot_async(session),
            _fetch_news_async(session),
        ]
        if HL_WALLET_ADDR:
            tasks.append(_fetch_hl_account_async(session))

        await asyncio.gather(*tasks, return_exceptions=True)

    log.info(f"=== Full fetch done in {time.time() - t0:.1f}s ===")


# ── 公共同步接口（向后兼容：scheduler.py 中 import fetcher; fetcher.fetch_all()）─

def fetch_all():
    asyncio.run(_fetch_all_async())

def fetch_hl_market() -> dict:
    async def _run():
        async with aiohttp.ClientSession() as s:
            return await _fetch_hl_market_async(s)
    return asyncio.run(_run())

def fetch_hl_account() -> dict:
    async def _run():
        async with aiohttp.ClientSession() as s:
            return await _fetch_hl_account_async(s)
    return asyncio.run(_run())

def fetch_market_snapshot() -> dict:
    async def _run():
        async with aiohttp.ClientSession() as s:
            return await _fetch_market_snapshot_async(s)
    return asyncio.run(_run())

def fetch_news() -> list:
    async def _run():
        async with aiohttp.ClientSession() as s:
            return await _fetch_news_async(s)
    return asyncio.run(_run())


# ── CLI 入口 ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--task",
        choices=["hl_market", "hl_account", "market_snapshot", "news", "all"],
        default="all",
    )
    args = parser.parse_args()

    task_map = {
        "hl_market":       fetch_hl_market,
        "hl_account":      fetch_hl_account,
        "market_snapshot": fetch_market_snapshot,
        "news":            fetch_news,
        "all":             fetch_all,
    }
    task_map[args.task]()
