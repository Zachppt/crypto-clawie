"""
skills/crypto_data — 市场价格与行情数据
数据来源：本地缓存（Binance + HL）
"""

from skills.base import BaseSkill


class CryptoDataSkill(BaseSkill):

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
        snap = self.load("market_snapshot.json")
        hl   = self.load("hl_market.json")
        sym  = symbol.upper()

        # 优先用 HL 价格（永续标记价）
        if hl:
            asset = next((a for a in hl.get("assets", []) if a["symbol"] == sym), None)
            if asset:
                price  = asset["mark_price"]
                change = asset["change_24h_pct"]
                emoji  = "🟢" if change >= 0 else "🔴"
                return self.ok(
                    f"{emoji} *{sym}* `${price:,.2f}`\n"
                    f"24h：`{change:+.2f}%`\n"
                    f"资金费率(8h)：`{asset['funding_8h']*100:+.4f}%`",
                    data=asset,
                )

        # 退回 Binance 价格
        if snap:
            prices = snap.get("prices", {})
            if sym in prices:
                p      = prices[sym]
                emoji  = "🟢" if p["change_24h"] >= 0 else "🔴"
                return self.ok(
                    f"{emoji} *{sym}* `${p['price']:,.2f}`\n24h：`{p['change_24h']:+.2f}%`",
                    data=p,
                )

        return self.err(f"未找到 {sym} 的价格数据")

    def _market_overview(self, **_) -> dict:
        snap = self.load("market_snapshot.json")
        hl   = self.load("hl_market.json")

        if not snap and not hl:
            return self.err("市场数据未缓存，请检查 fetcher 是否运行")

        lines = ["📈 *市场行情*\n"]

        # 主流价格
        if snap:
            prices = snap.get("prices", {})
            for sym in ["BTC", "ETH", "SOL", "BNB"]:
                if sym in prices:
                    p     = prices[sym]
                    emoji = "🟢" if p["change_24h"] >= 0 else "🔴"
                    lines.append(f"{emoji} `{sym:4s}` `${p['price']:>10,.2f}` {p['change_24h']:+.2f}%")

        # 恐慌贪婪
        if snap:
            fng = snap.get("fear_greed", {})
            if fng:
                emoji = "😱" if fng["value"] < 25 else "😨" if fng["value"] < 45 else "😐" if fng["value"] < 55 else "😊" if fng["value"] < 75 else "🤑"
                lines.append(f"\n恐慌贪婪：{emoji} `{fng['value']}` {fng['label']}")

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
