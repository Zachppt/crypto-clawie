"""
skills/exchange_agg — 多交易所聚合行情与资金费率
数据源（均为公开 API，无需 Key）：
  • Binance Spot/Perp  — api.binance.com / fapi.binance.com
  • OKX Spot/Perp     — okx.com/api/v5
  • Bybit Spot/Perp   — api.bybit.com/v5
  • Gate.io Spot/Perp — api.gateio.ws/api/v4
  • Bitget Spot/Perp  — api.bitget.com/api/v2
  • Hyperliquid Perp  — 本地缓存 hl_market.json
"""
from __future__ import annotations

import requests
from skills.base import BaseSkill

_S = requests.Session()
_S.headers.update({"User-Agent": "crypto-clawie/2.0"})
T = 8  # timeout seconds

# 所有支持现货的交易所
SPOT_EXCHANGES  = ["Binance", "OKX", "Bybit", "Gate.io", "Bitget"]
# 所有支持合约的交易所
PERP_EXCHANGES  = ["Binance", "OKX", "Bybit", "Gate.io", "Bitget", "Hyperliquid"]


def _get(url: str, params: dict = None) -> dict | list | None:
    try:
        r = _S.get(url, params=params, timeout=T)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


# ── 各交易所取价函数（返回 float | None） ─────────────────────────────────────

def _binance_spot(symbol: str) -> float | None:
    d = _get(f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}USDT")
    return float(d["price"]) if d and "price" in d else None

def _okx_spot(symbol: str) -> float | None:
    d = _get(f"https://www.okx.com/api/v5/market/ticker?instId={symbol}-USDT")
    return float(d["data"][0]["last"]) if d and d.get("data") else None

def _bybit_spot(symbol: str) -> float | None:
    d = _get(f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={symbol}USDT")
    items = (d or {}).get("result", {}).get("list", [])
    return float(items[0]["lastPrice"]) if items else None

def _gate_spot(symbol: str) -> float | None:
    d = _get(f"https://api.gateio.ws/api/v4/spot/tickers?currency_pair={symbol}_USDT")
    if d and isinstance(d, list) and d:
        v = d[0].get("last", 0)
        return float(v) if v else None
    return None

def _bitget_spot(symbol: str) -> float | None:
    d = _get(f"https://api.bitget.com/api/v2/spot/market/tickers?symbol={symbol}USDT")
    if d and d.get("code") == "00000" and d.get("data"):
        v = d["data"][0].get("close") or d["data"][0].get("lastPr")
        return float(v) if v else None
    return None

def _hl_price(symbol: str, market_cache: dict) -> float | None:
    if not market_cache:
        return None
    for a in market_cache.get("assets", []):
        if a["symbol"] == symbol:
            return a["mark_price"]
    return None

# ── 各交易所资金费率函数 ──────────────────────────────────────────────────────

def _binance_funding(symbol: str) -> float | None:
    d = _get(f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}USDT")
    return float(d["lastFundingRate"]) if d and isinstance(d, dict) and "lastFundingRate" in d else None

def _okx_funding(symbol: str) -> float | None:
    d = _get(f"https://www.okx.com/api/v5/public/funding-rate?instId={symbol}-USDT-SWAP")
    return float(d["data"][0]["fundingRate"]) if d and d.get("data") else None

def _bybit_funding(symbol: str) -> float | None:
    d = _get(f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={symbol}USDT")
    items = (d or {}).get("result", {}).get("list", [])
    return float(items[0]["fundingRate"]) if items and "fundingRate" in items[0] else None

def _gate_funding(symbol: str) -> float | None:
    d = _get(f"https://api.gateio.ws/api/v4/futures/usdt/contracts/{symbol}_USDT")
    return float(d["funding_rate"]) if d and isinstance(d, dict) and "funding_rate" in d else None

def _bitget_funding(symbol: str) -> float | None:
    d = _get(f"https://api.bitget.com/api/v2/mix/market/ticker?symbol={symbol}USDT&productType=USDT-FUTURES")
    if d and d.get("code") == "00000" and d.get("data"):
        v = d["data"][0].get("fundingRate")
        return float(v) if v is not None else None
    return None

def _hl_funding(symbol: str, market_cache: dict) -> float | None:
    if not market_cache:
        return None
    for a in market_cache.get("assets", []):
        if a["symbol"] == symbol:
            return a["funding_8h"]
    return None

# ── 上架状态行格式化 ──────────────────────────────────────────────────────────

def _listing_line(label: str, found: dict[str, bool], all_exchanges: list[str]) -> str:
    parts = []
    for ex in all_exchanges:
        icon = "✅" if found.get(ex) else "❌"
        parts.append(f"{icon}{ex}")
    listed   = sum(1 for v in found.values() if v)
    total    = len(all_exchanges)
    return f"_{label}：{' | '.join(parts)}（{listed}/{total}）_"


class ExchangeAggSkill(BaseSkill):

    def run(self, action: str = "compare", symbol: str = "BTC", **kwargs) -> dict:
        symbol = symbol.upper()
        dispatch = {
            "compare":   self._compare,
            "funding":   self._funding_compare,
            "divergence":self._divergence,
            "volume":    self._volume,
            "listings":  self._listings,
        }
        fn = dispatch.get(action.lower())
        if not fn:
            return self.err(f"未知操作：{action}。可用：compare / funding / divergence / volume / listings")
        return fn(symbol=symbol, **kwargs)

    # ── 跨所价格对比 ──────────────────────────────────────────────────────────

    def _compare(self, symbol: str = "BTC", **_) -> dict:
        market = self.load("hl_market.json")

        # 抓取各所价格，同时记录上架情况
        fetchers = [
            ("Binance",     _binance_spot(symbol)),
            ("OKX",         _okx_spot(symbol)),
            ("Bybit",       _bybit_spot(symbol)),
            ("Gate.io",     _gate_spot(symbol)),
            ("Bitget",      _bitget_spot(symbol)),
            ("Hyperliquid", _hl_price(symbol, market)),
        ]

        prices   = {ex: p for ex, p in fetchers if p is not None}
        spot_ok  = {ex: (p is not None) for ex, p in fetchers if ex != "Hyperliquid"}
        perp_ok  = {"Hyperliquid": _hl_price(symbol, market) is not None}

        if not prices:
            return self.err(f"所有交易所均无法获取 {symbol} 价格，请检查网络或确认币种名称")

        avg = sum(prices.values()) / len(prices)
        max_p = max(prices.values())
        min_p = min(prices.values())

        lines = [f"📊 *{symbol} 跨所价格对比*\n"]
        for ex, price in sorted(prices.items(), key=lambda x: x[1], reverse=True):
            diff_pct = (price - avg) / avg * 100
            sign     = "+" if diff_pct >= 0 else ""
            marker   = " ⬆️" if price == max_p and len(prices) > 1 else (
                       " ⬇️" if price == min_p and len(prices) > 1 else "")
            lines.append(f"• *{ex}*：`${price:,.2f}` ({sign}{diff_pct:.3f}%){marker}")

        if len(prices) > 1:
            spread     = max_p - min_p
            spread_pct = spread / avg * 100
            high_ex    = max(prices, key=prices.get)
            low_ex     = min(prices, key=prices.get)
            lines.append(f"\n最大价差：`${spread:.2f}` ({spread_pct:.3f}%) [{high_ex} vs {low_ex}]")
            if spread_pct >= 0.1:
                lines.append("⚡ _价差较大，可能存在套利机会或数据延迟_")

        # 上架情况（现货：5 家 CEX；合约单独标注 HL）
        lines.append("")
        lines.append(_listing_line("现货上架", spot_ok, SPOT_EXCHANGES))
        hl_listed = perp_ok["Hyperliquid"]
        lines.append(f"_合约 Hyperliquid：{'✅ 有永续合约' if hl_listed else '❌ 无永续合约'}_")

        return self.ok("\n".join(lines), data={
            "prices": prices, "avg": avg,
            "spot_listed": [ex for ex, ok in spot_ok.items() if ok],
            "hl_listed": hl_listed,
        })

    # ── 跨所资金费率对比 ──────────────────────────────────────────────────────

    def _funding_compare(self, symbol: str = "BTC", **_) -> dict:
        market = self.load("hl_market.json")

        fetchers = [
            ("Hyperliquid", _hl_funding(symbol, market)),
            ("Binance",     _binance_funding(symbol)),
            ("OKX",         _okx_funding(symbol)),
            ("Bybit",       _bybit_funding(symbol)),
            ("Gate.io",     _gate_funding(symbol)),
            ("Bitget",      _bitget_funding(symbol)),
        ]

        rates   = {ex: r for ex, r in fetchers if r is not None}
        perp_ok = {ex: (r is not None) for ex, r in fetchers}

        if not rates:
            return self.err(f"获取 {symbol} 资金费率失败，该币种可能无合约或网络异常")

        lines = [f"💹 *{symbol} 跨所资金费率对比*\n"]
        for ex, rate in sorted(rates.items(), key=lambda x: abs(x[1]), reverse=True):
            ann       = rate * 3 * 365 * 100
            direction = "多付空 📈" if rate > 0 else "空付多 📉"
            emoji     = "🔴" if abs(rate) >= 0.001 else "🟡" if abs(rate) >= 0.0005 else "🟢"
            lines.append(
                f"{emoji} *{ex}*：`{rate*100:+.4f}%/8h` ({direction}) | 年化 `{ann:+.1f}%`"
            )

        if len(rates) >= 2:
            max_ex = max(rates, key=rates.get)
            min_ex = min(rates, key=rates.get)
            spread = rates[max_ex] - rates[min_ex]
            if spread >= 0.0003:
                ann_arb = spread * 3 * 365 * 100
                lines.append(
                    f"\n💡 *跨所套利机会*\n"
                    f"费率差：`{spread*100:+.4f}%/8h`（年化约 `{ann_arb:.0f}%`）\n"
                    f"做空 {max_ex}（高费率）+ 做多 {min_ex}（低费率）\n"
                    f"⚠️ 需评估对冲成本和手续费"
                )

        # 合约上架情况
        lines.append("")
        lines.append(_listing_line("合约上架", perp_ok, PERP_EXCHANGES))

        return self.ok("\n".join(lines), data={
            "rates": rates,
            "perp_listed": [ex for ex, ok in perp_ok.items() if ok],
        })

    # ── 价差异动扫描 ──────────────────────────────────────────────────────────

    def _divergence(self, threshold_pct: float = 0.15, **_) -> dict:
        """扫描主流币跨所价差，找出超过阈值的异动。"""
        symbols = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "AVAX", "ARB"]
        market  = self.load("hl_market.json")
        hl_map  = {}
        if market:
            for a in market.get("assets", []):
                hl_map[a["symbol"]] = a["mark_price"]

        divergences = []
        for sym in symbols:
            prices = {}
            p = _binance_spot(sym)
            if p: prices["Binance"] = p
            p = _okx_spot(sym)
            if p: prices["OKX"] = p
            p = _bitget_spot(sym)
            if p: prices["Bitget"] = p
            if sym in hl_map:
                prices["HL"] = hl_map[sym]

            if len(prices) < 2:
                continue

            avg        = sum(prices.values()) / len(prices)
            spread_pct = (max(prices.values()) - min(prices.values())) / avg * 100

            if spread_pct >= threshold_pct:
                max_ex = max(prices, key=prices.get)
                min_ex = min(prices, key=prices.get)
                divergences.append({
                    "symbol":     sym,
                    "spread_pct": round(spread_pct, 4),
                    "prices":     prices,
                    "high_ex":    max_ex,
                    "low_ex":     min_ex,
                })

        divergences.sort(key=lambda x: x["spread_pct"], reverse=True)

        if not divergences:
            return self.ok(f"✅ 无明显跨所价差（阈值 {threshold_pct}%）")

        lines = [f"⚡ *跨所价差扫描*（>{threshold_pct}%）\n"]
        for d in divergences:
            lines.append(
                f"• `{d['symbol']}` 价差 `{d['spread_pct']:.3f}%`\n"
                f"  高：{d['high_ex']} `${d['prices'][d['high_ex']]:,.2f}`  "
                f"低：{d['low_ex']} `${d['prices'][d['low_ex']]:,.2f}`"
            )
        lines.append("\n💡 价差 > 0.3% 可能存在套利空间（考虑手续费和滑点）")

        return self.ok("\n".join(lines), data={"divergences": divergences})

    # ── 成交量对比 ────────────────────────────────────────────────────────────

    def _volume(self, symbol: str = "BTC", **_) -> dict:
        volumes: dict[str, float] = {}

        d = _get(f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}USDT")
        if d and isinstance(d, dict):
            volumes["Binance 现货"] = float(d.get("quoteVolume", 0))

        d = _get(f"https://fapi.binance.com/fapi/v1/ticker/24hr?symbol={symbol}USDT")
        if d and isinstance(d, dict) and "quoteVolume" in d:
            volumes["Binance 合约"] = float(d["quoteVolume"])

        d = _get(f"https://www.okx.com/api/v5/market/ticker?instId={symbol}-USDT")
        if d and d.get("data"):
            vol_ccy = float(d["data"][0].get("volCcy24h", 0) or 0)
            last    = float(d["data"][0].get("last", 1) or 1)
            volumes["OKX 现货"] = vol_ccy * last

        d = _get(f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={symbol}USDT")
        items = (d or {}).get("result", {}).get("list", [])
        if items and "turnover24h" in items[0]:
            volumes["Bybit 合约"] = float(items[0]["turnover24h"] or 0)

        # Bitget 现货
        d = _get(f"https://api.bitget.com/api/v2/spot/market/tickers?symbol={symbol}USDT")
        if d and d.get("code") == "00000" and d.get("data"):
            v = d["data"][0].get("usdtVol") or d["data"][0].get("quoteVol")
            if v:
                volumes["Bitget 现货"] = float(v)

        # Bitget 合约
        d = _get(f"https://api.bitget.com/api/v2/mix/market/ticker?symbol={symbol}USDT&productType=USDT-FUTURES")
        if d and d.get("code") == "00000" and d.get("data"):
            v = d["data"][0].get("quoteVolume") or d["data"][0].get("usdtVolume")
            if v:
                volumes["Bitget 合约"] = float(v)

        if not volumes:
            return self.err(f"获取 {symbol} 成交量失败")

        total = sum(volumes.values())
        lines = [f"📈 *{symbol} 24h 成交量对比*\n"]
        for ex, vol in sorted(volumes.items(), key=lambda x: x[1], reverse=True):
            share = vol / total * 100 if total else 0
            lines.append(f"• *{ex}*：`${vol/1e9:.2f}B` ({share:.1f}%)")

        lines.append(f"\n合计：`${total/1e9:.2f}B`")
        return self.ok("\n".join(lines), data={"volumes": volumes, "total": total})

    # ── 上架情况查询 ─────────────────────────────────────────────────────────

    def _listings(self, symbol: str = "BTC", **_) -> dict:
        """
        全面检查该代币在各交易所的上架情况（现货 + 合约）。
        """
        market = self.load("hl_market.json")

        # 并行取数据
        spot_results = {
            "Binance": _binance_spot(symbol),
            "OKX":     _okx_spot(symbol),
            "Bybit":   _bybit_spot(symbol),
            "Gate.io": _gate_spot(symbol),
            "Bitget":  _bitget_spot(symbol),
        }
        perp_results = {
            "Binance":     _binance_funding(symbol),
            "OKX":         _okx_funding(symbol),
            "Bybit":       _bybit_funding(symbol),
            "Gate.io":     _gate_funding(symbol),
            "Bitget":      _bitget_funding(symbol),
            "Hyperliquid": _hl_funding(symbol, market),
        }

        spot_listed = [ex for ex, v in spot_results.items() if v is not None]
        perp_listed = [ex for ex, v in perp_results.items() if v is not None]
        spot_miss   = [ex for ex, v in spot_results.items() if v is None]
        perp_miss   = [ex for ex, v in perp_results.items() if v is None]

        lines = [f"🔍 *{symbol} 交易所上架情况*\n"]

        lines.append("*现货（Spot）*")
        for ex in SPOT_EXCHANGES:
            p = spot_results.get(ex)
            if p is not None:
                p_str = f"${p:,.2f}" if p >= 0.01 else f"${p:.8f}".rstrip("0")
                lines.append(f"  ✅ {ex}  `{p_str}`")
            else:
                lines.append(f"  ❌ {ex}  未上架或暂不可用")

        lines.append("\n*永续合约（Perp）*")
        for ex in PERP_EXCHANGES:
            r = perp_results.get(ex)
            if r is not None:
                ann = r * 3 * 365 * 100
                lines.append(f"  ✅ {ex}  资金费率 `{r*100:+.4f}%/8h`（年化 `{ann:+.1f}%`）")
            else:
                lines.append(f"  ❌ {ex}  无合约或暂不可用")

        lines.append(
            f"\n现货：{len(spot_listed)}/{len(SPOT_EXCHANGES)} 家  "
            f"合约：{len(perp_listed)}/{len(PERP_EXCHANGES)} 家"
        )

        if spot_miss:
            lines.append(f"_现货未上架：{', '.join(spot_miss)}_")
        if perp_miss:
            lines.append(f"_合约未上架：{', '.join(perp_miss)}_")

        return self.ok("\n".join(lines), data={
            "symbol":       symbol,
            "spot_listed":  spot_listed,
            "perp_listed":  perp_listed,
            "spot_missing": spot_miss,
            "perp_missing": perp_miss,
        })
