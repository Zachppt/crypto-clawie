"""
skills/crypto_data — 市场价格与行情数据
数据来源：实时 Binance API（缓存 > 90s 时自动切换） + HL 缓存（资金费率）
"""
from __future__ import annotations

import requests as _req
from skills.base import BaseSkill

_SESS = _req.Session()
_SESS.headers["User-Agent"] = "crypto-clawie/2.0"
_LIVE_TIMEOUT = 5


class CryptoDataSkill(BaseSkill):

    # ── 实时价格（Binance 直连） ──────────────────────────────────────────────

    def _live_binance(self, symbol: str) -> dict | None:
        """直接调用 Binance 24h ticker，绕过缓存。失败返回 None。"""
        try:
            r = _SESS.get(
                "https://api.binance.com/api/v3/ticker/24hr",
                params={"symbol": f"{symbol}USDT"},
                timeout=_LIVE_TIMEOUT,
            )
            r.raise_for_status()
            d = r.json()
            return {
                "price":       float(d["lastPrice"]),
                "change_24h":  float(d["priceChangePercent"]),
                "volume_usdt": float(d["quoteVolume"]),
            }
        except Exception:
            return None

    def _live_binance_batch(self, symbols: list[str]) -> dict:
        """批量获取多个币种实时价格，返回 {SYM: {price, change_24h, ...}}。"""
        try:
            r = _SESS.get(
                "https://api.binance.com/api/v3/ticker/24hr",
                timeout=_LIVE_TIMEOUT,
            )
            r.raise_for_status()
            pairs = {s + "USDT" for s in symbols}
            result = {}
            for t in r.json():
                if t["symbol"] in pairs:
                    coin = t["symbol"].replace("USDT", "")
                    result[coin] = {
                        "price":       float(t["lastPrice"]),
                        "change_24h":  float(t["priceChangePercent"]),
                        "volume_usdt": float(t["quoteVolume"]),
                    }
            return result
        except Exception:
            return {}

    # ── 公共入口 ──────────────────────────────────────────────────────────────

    def run(self, action: str = "price", **kwargs) -> dict:
        dispatch = {
            "price":    self._price,
            "overview": self._market_overview,
            "fng":      self._fear_greed,
        }
        fn = dispatch.get(action.lower())
        if not fn:
            return self.err(f"未知操作：{action}。可用：price / overview / fng")
        return fn(**kwargs)

    def _price(self, symbol: str = "BTC", **_) -> dict:
        sym      = symbol.upper()
        snap     = self.load("market_snapshot.json")
        hl       = self.load("hl_market.json")
        snap_age = self.data_age_minutes("market_snapshot.json") * 60  # 转为秒

        # HL 缓存（含资金费率，5 分钟可接受）
        hl_asset = None
        if hl:
            hl_asset = next((a for a in hl.get("assets", []) if a["symbol"] == sym), None)

        # 价格：缓存 < 90s → 用缓存；否则实时获取
        price_data = None
        source     = "cache"

        if snap_age <= 90 and snap:
            cached = snap.get("prices", {}).get(sym)
            if cached:
                price_data = cached
                source     = "cache"

        if not price_data:
            live = self._live_binance(sym)
            if live:
                price_data = live
                source     = "live"

        # 最后退回 HL 缓存价格
        if not price_data and hl_asset:
            price_data = {
                "price":      hl_asset["mark_price"],
                "change_24h": hl_asset["change_24h_pct"],
            }
            source = "hl_cache"

        if not price_data:
            return self.err(f"未找到 {sym} 的价格数据")

        price  = price_data["price"]
        change = price_data.get("change_24h", 0)
        emoji  = "🟢" if change >= 0 else "🔴"

        text = (
            f"{emoji} *{sym}* `${price:,.2f}`\n"
            f"24h：`{change:+.2f}%`"
        )
        if hl_asset:
            text += f"\n资金费率(8h)：`{hl_asset['funding_8h']*100:+.4f}%`"
        if source == "live":
            text += "\n_✨ 实时价格_"

        return self.ok(text, data={**price_data, "source": source, "symbol": sym})

    def _market_overview(self, **_) -> dict:
        snap     = self.load("market_snapshot.json")
        hl       = self.load("hl_market.json")
        snap_age = self.data_age_minutes("market_snapshot.json") * 60

        if not snap and not hl:
            return self.err("市场数据未缓存，请检查 fetcher 是否运行")

        SYMS  = ["BTC", "ETH", "SOL", "BNB"]
        lines = ["📈 *市场行情*\n"]

        # 价格：缓存过期则实时获取
        if snap_age > 90:
            prices = self._live_binance_batch(SYMS)
            is_live = bool(prices)
        else:
            prices  = snap.get("prices", {}) if snap else {}
            is_live = False

        for sym in SYMS:
            p = prices.get(sym)
            if p:
                emoji = "🟢" if p["change_24h"] >= 0 else "🔴"
                lines.append(f"{emoji} `{sym:4s}` `${p['price']:>10,.2f}` {p['change_24h']:+.2f}%")

        # 恐慌贪婪（FNG 不需要实时，用缓存即可）
        if snap:
            fng = snap.get("fear_greed", {})
            if fng:
                emoji = "😱" if fng["value"] < 25 else "😨" if fng["value"] < 45 else "😐" if fng["value"] < 55 else "😊" if fng["value"] < 75 else "🤑"
                lines.append(f"\n恐慌贪婪：{emoji} `{fng['value']}` {fng['label']}")

        if is_live:
            lines.append("\n_✨ 实时价格_")

        return self.ok("\n".join(lines))

    def _fear_greed(self, **_) -> dict:
        snap = self.load("market_snapshot.json")
        if not snap:
            return self.err("数据未缓存")
        fng = snap.get("fear_greed", {})
        if not fng:
            return self.err("恐慌贪婪数据不可用")
        v     = fng["value"]
        label = fng["label"]
        emoji = "😱" if v < 25 else "😨" if v < 45 else "😐" if v < 55 else "😊" if v < 75 else "🤑"
        advice = (
            "极度恐慌，历史上常是买入时机，但需结合基本面" if v < 25 else
            "市场偏悲观，可关注低吸机会" if v < 45 else
            "市场中性，无明显方向" if v < 55 else
            "市场偏乐观，适度谨慎" if v < 75 else
            "极度贪婪，注意回调风险"
        )
        return self.ok(
            f"恐慌贪婪指数：{emoji} *{v}* ({label})\n💡 {advice}",
            data=fng,
        )
