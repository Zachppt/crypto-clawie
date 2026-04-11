"""
skills/exchange_agg — 多交易所聚合行情与资金费率
数据源（均为公开 API，无需 Key）：
  • Hyperliquid Perp  — 本地缓存 hl_market.json
  • Binance Spot/Perp — api.binance.com / fapi.binance.com
  • OKX Spot/Perp    — okx.com/api/v5
  • Bybit Perp       — api.bybit.com/v5
  • Gate.io Perp     — api.gateio.ws/api/v4/futures
"""
import requests
from skills.base import BaseSkill

_S = requests.Session()
_S.headers.update({"User-Agent": "crypto-clawie/2.0"})
T = 8  # timeout seconds


def _get(url: str, params: dict = None) -> dict | list | None:
    try:
        r = _S.get(url, params=params, timeout=T)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


class ExchangeAggSkill(BaseSkill):

    def run(self, action: str = "compare", symbol: str = "BTC", **kwargs) -> dict:
        symbol = symbol.upper()
        dispatch = {
            "compare":    self._compare,
            "funding":    self._funding_compare,
            "divergence": self._divergence,
            "volume":     self._volume,
        }
        fn = dispatch.get(action.lower())
        if not fn:
            return self.err(f"未知操作：{action}。可用：compare / funding / divergence / volume")
        return fn(symbol=symbol, **kwargs)

    # ── 跨所价格对比 ──────────────────────────────────────────────────────────

    def _compare(self, symbol: str = "BTC", **_) -> dict:
        prices = {}

        d = _get(f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}USDT")
        if d and "price" in d:
            prices["Binance"] = float(d["price"])

        d = _get(f"https://www.okx.com/api/v5/market/ticker?instId={symbol}-USDT")
        if d and d.get("data"):
            prices["OKX"] = float(d["data"][0]["last"])

        d = _get(f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={symbol}USDT")
        items = (d or {}).get("result", {}).get("list", [])
        if items:
            prices["Bybit"] = float(items[0]["lastPrice"])

        d = _get(f"https://api.gateio.ws/api/v4/spot/tickers?currency_pair={symbol}_USDT")
        if d and isinstance(d, list) and d:
            prices["Gate.io"] = float(d[0].get("last", 0) or 0)

        market = self.load("hl_market.json")
        if market:
            for a in market.get("assets", []):
                if a["symbol"] == symbol:
                    prices["Hyperliquid"] = a["mark_price"]
                    break

        if not prices:
            return self.err(f"获取 {symbol} 价格失败，请检查网络连接")

        avg = sum(prices.values()) / len(prices)
        lines = [f"📊 *{symbol} 跨所价格对比*\n"]
        sorted_prices = sorted(prices.items(), key=lambda x: x[1], reverse=True)
        max_p = max(prices.values())
        min_p = min(prices.values())
        for ex, price in sorted_prices:
            diff_pct = (price - avg) / avg * 100
            sign = "+" if diff_pct >= 0 else ""
            marker = " ⬆️" if price == max_p and len(prices) > 1 else (
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

        return self.ok("\n".join(lines), data={"prices": prices, "avg": avg})

    # ── 跨所资金费率对比 ──────────────────────────────────────────────────────

    def _funding_compare(self, symbol: str = "BTC", **_) -> dict:
        rates = {}

        market = self.load("hl_market.json")
        if market:
            for a in market.get("assets", []):
                if a["symbol"] == symbol:
                    rates["Hyperliquid"] = a["funding_8h"]
                    break

        d = _get(f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}USDT")
        if d and isinstance(d, dict) and "lastFundingRate" in d:
            rates["Binance"] = float(d["lastFundingRate"])

        d = _get(f"https://www.okx.com/api/v5/public/funding-rate?instId={symbol}-USDT-SWAP")
        if d and d.get("data"):
            rates["OKX"] = float(d["data"][0]["fundingRate"])

        d = _get(f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={symbol}USDT")
        items = (d or {}).get("result", {}).get("list", [])
        if items and "fundingRate" in items[0]:
            rates["Bybit"] = float(items[0]["fundingRate"])

        d = _get(f"https://api.gateio.ws/api/v4/futures/usdt/contracts/{symbol}_USDT")
        if d and isinstance(d, dict) and "funding_rate" in d:
            rates["Gate.io"] = float(d["funding_rate"])

        if not rates:
            return self.err(f"获取 {symbol} 资金费率失败")

        lines = [f"💹 *{symbol} 跨所资金费率对比*\n"]
        for ex, rate in sorted(rates.items(), key=lambda x: abs(x[1]), reverse=True):
            ann       = rate * 3 * 365 * 100
            direction = "多付空 📈" if rate > 0 else "空付多 📉"
            emoji     = "🔴" if abs(rate) >= 0.001 else "🟡" if abs(rate) >= 0.0005 else "🟢"
            lines.append(
                f"{emoji} *{ex}*：`{rate*100:+.4f}%/8h` ({direction}) | 年化 `{ann:+.1f}%`"
            )

        if len(rates) >= 2:
            max_ex = max(rates, key=lambda x: rates[x])
            min_ex = min(rates, key=lambda x: rates[x])
            spread = rates[max_ex] - rates[min_ex]
            if spread >= 0.0003:
                ann_arb = spread * 3 * 365 * 100
                lines.append(
                    f"\n💡 *跨所套利机会*\n"
                    f"费率差：`{spread*100:+.4f}%/8h`（年化约 `{ann_arb:.0f}%`）\n"
                    f"做空 {max_ex}（高费率）+ 做多 {min_ex}（低费率）\n"
                    f"⚠️ 需评估对冲成本和手续费"
                )

        return self.ok("\n".join(lines), data={"rates": rates})

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
            d = _get(f"https://api.binance.com/api/v3/ticker/price?symbol={sym}USDT")
            if d and "price" in d:
                prices["Binance"] = float(d["price"])

            d = _get(f"https://www.okx.com/api/v5/market/ticker?instId={sym}-USDT")
            if d and d.get("data"):
                prices["OKX"] = float(d["data"][0]["last"])

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
        volumes = {}

        d = _get(f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}USDT")
        if d and isinstance(d, dict):
            volumes["Binance Spot"] = float(d.get("quoteVolume", 0))

        d = _get(f"https://fapi.binance.com/fapi/v1/ticker/24hr?symbol={symbol}USDT")
        if d and isinstance(d, dict) and "quoteVolume" in d:
            volumes["Binance Perp"] = float(d["quoteVolume"])

        d = _get(f"https://www.okx.com/api/v5/market/ticker?instId={symbol}-USDT")
        if d and d.get("data"):
            vol_ccy = float(d["data"][0].get("volCcy24h", 0) or 0)
            last    = float(d["data"][0].get("last", 1) or 1)
            volumes["OKX Spot"] = vol_ccy * last

        d = _get(f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={symbol}USDT")
        items = (d or {}).get("result", {}).get("list", [])
        if items and "turnover24h" in items[0]:
            volumes["Bybit Perp"] = float(items[0]["turnover24h"] or 0)

        if not volumes:
            return self.err(f"获取 {symbol} 成交量失败")

        total = sum(volumes.values())
        lines = [f"📈 *{symbol} 24h 成交量对比*\n"]
        for ex, vol in sorted(volumes.items(), key=lambda x: x[1], reverse=True):
            share = vol / total * 100 if total else 0
            lines.append(f"• *{ex}*：`${vol/1e9:.2f}B` ({share:.1f}%)")

        lines.append(f"\n合计：`${total/1e9:.2f}B`")
        return self.ok("\n".join(lines), data={"volumes": volumes, "total": total})
