"""
skills/hl_monitor — Hyperliquid 市场监控
功能：资金费率排行、未平仓量变化、账户持仓、爆仓风险评估
"""

from skills.base import BaseSkill


class HLMonitorSkill(BaseSkill):

    def run(self, action: str = "overview", **kwargs) -> dict:
        dispatch = {
            "overview":    self._overview,
            "funding":     self._funding_rates,
            "account":     self._account,
            "positions":   self._account,
            "liquidation": self._liq_risk,
            "oi":          self._open_interest,
        }
        fn = dispatch.get(action.lower())
        if not fn:
            return self.err(f"未知操作：{action}。可用：overview / funding / account / liquidation / oi")
        return fn(**kwargs)

    # ── 市场概览 ──────────────────────────────────────────────────────────────

    def _overview(self, **_) -> dict:
        market  = self.load("hl_market.json")
        account = self.load("hl_account.json")
        snap    = self.load("market_snapshot.json")

        if not market:
            return self.err("HL 市场数据未缓存，请检查 fetcher 是否运行")

        age = self.data_age_minutes("hl_market.json")
        stale = f"\n\n⚠️ _数据更新于 {age:.0f} 分钟前_" if age > 15 else ""

        top_funding = market.get("top_funding", [])[:5]
        fng = snap.get("fear_greed", {}) if snap else {}

        lines = ["📊 *HL 市场概览*\n"]

        if fng:
            emoji = "😱" if fng["value"] < 25 else "😨" if fng["value"] < 45 else "😐" if fng["value"] < 55 else "😊" if fng["value"] < 75 else "🤑"
            lines.append(f"恐慌贪婪指数：{emoji} `{fng['value']}` ({fng['label']})\n")

        lines.append("*资金费率 Top 5（|费率| 排行）：*")
        for a in top_funding:
            rate    = a["funding_8h"]
            ann     = a["funding_annualized"]
            emoji   = "🔴" if abs(rate) >= 0.001 else "🟡" if abs(rate) >= 0.0005 else "🟢"
            direction = "多付空" if rate > 0 else "空付多"
            lines.append(f"{emoji} `{a['symbol']}` {rate*100:+.4f}%/8h ({direction}) | 年化 {ann:+.1f}%")

        if account:
            acct_val = account.get("account_value_usdc", 0)
            pos_count = len(account.get("positions", []))
            liq_alerts = account.get("liq_alerts", [])
            lines.append(f"\n💼 账户：`${acct_val:,.2f}` USDC | 持仓：{pos_count} 个")
            if liq_alerts:
                lines.append(f"🚨 爆仓预警：{len(liq_alerts)} 个持仓需关注")

        return self.ok("\n".join(lines) + stale, data={"market": market, "account": account})

    # ── 资金费率详情 ──────────────────────────────────────────────────────────

    def _funding_rates(self, symbol: str = None, top: int = 20, **_) -> dict:
        market = self.load("hl_market.json")
        if not market:
            return self.err("HL 市场数据未缓存")

        assets = market.get("assets", [])

        if symbol:
            sym_upper = symbol.upper()
            asset = next((a for a in assets if a["symbol"] == sym_upper), None)
            if not asset:
                return self.err(f"未找到 {sym_upper} 的数据")
            rate  = asset["funding_8h"]
            ann   = asset["funding_annualized"]
            price = asset["mark_price"]
            direction = "多头付空头（做多有成本）" if rate > 0 else "空头付多头（做空有成本）"
            lvl = "🔴 极端" if abs(rate) >= 0.001 else "🟡 偏高" if abs(rate) >= 0.0005 else "🟢 正常"
            oi_usd = asset.get("open_interest", 0) * price
            text = (
                f"💹 *{sym_upper}*\n"
                f"价格：`${price:,.2f}` | 24h：`{asset['change_24h_pct']:+.2f}%`\n"
                f"资金费率(8h)：`{rate*100:+.4f}%` {lvl}\n"
                f"方向：{direction}\n"
                f"年化：`{ann:+.1f}%`\n"
                f"未平仓量：`${oi_usd/1e6:.1f}M`"
            )
            return self.ok(text, data=asset)

        # 全市场排行
        sorted_assets = sorted(assets, key=lambda x: abs(x["funding_8h"]), reverse=True)[:top]
        lines = [f"💹 *资金费率排行 Top {top}*\n"]
        for a in sorted_assets:
            rate  = a["funding_8h"]
            emoji = "🔴" if abs(rate) >= 0.001 else "🟡" if abs(rate) >= 0.0005 else "🟢"
            lines.append(f"{emoji} `{a['symbol']:6s}` {rate*100:+.4f}%/8h | 年化 {a['funding_annualized']:+.1f}%")

        return self.ok("\n".join(lines), data={"top_funding": sorted_assets})

    # ── 未平仓量 ──────────────────────────────────────────────────────────────

    def _open_interest(self, symbol: str = None, top: int = 10, **_) -> dict:
        market = self.load("hl_market.json")
        if not market:
            return self.err("HL 市场数据未缓存")

        assets = market.get("assets", [])

        if symbol:
            asset = next((a for a in assets if a["symbol"] == symbol.upper()), None)
            if not asset:
                return self.err(f"未找到 {symbol.upper()}")
            oi    = asset["open_interest"]
            price = asset["mark_price"]
            oi_usd = oi * price
            return self.ok(
                f"📊 *{symbol.upper()} 未平仓量*\n"
                f"OI：`{oi:,.0f}` {symbol.upper()} (`${oi_usd/1e6:.1f}M`)\n"
                f"标记价格：`${price:,.2f}`",
                data=asset,
            )

        sorted_oi = sorted(assets, key=lambda x: x["open_interest"] * x["mark_price"], reverse=True)[:top]
        lines = [f"📊 *未平仓量 Top {top}*\n"]
        for a in sorted_oi:
            oi_usd = a["open_interest"] * a["mark_price"]
            lines.append(f"• `{a['symbol']:6s}` OI：`${oi_usd/1e6:.1f}M`")

        return self.ok("\n".join(lines))

    # ── 账户持仓 ──────────────────────────────────────────────────────────────

    def _account(self, **_) -> dict:
        account = self.load("hl_account.json")
        age     = self.data_age_minutes("hl_account.json")

        if not account:
            return self.err("账户数据未缓存，请检查 HL_WALLET_ADDRESS 是否配置，fetcher 是否运行")

        positions  = account.get("positions", [])
        acct_val   = account.get("account_value_usdc", 0)
        margin     = account.get("margin_used_usdc", 0)
        liq_alerts = account.get("liq_alerts", [])

        stale = f"\n⚠️ _数据更新于 {age:.0f} 分钟前_" if age > 10 else ""

        if not positions:
            return self.ok(f"💼 *账户概览*\n余额：`${acct_val:,.2f}` USDC\n当前无持仓{stale}")

        lines = [
            f"💼 *账户概览*",
            f"余额：`${acct_val:,.2f}` USDC",
            f"已用保证金：`${margin:,.2f}` USDC",
            f"保证金占比：`{account.get('margin_ratio', 0):.1f}%`\n",
            "*持仓明细：*",
        ]

        for p in positions:
            side_emoji = "📈" if p["side"] == "long" else "📉"
            pnl_emoji  = "🟢" if p["unrealized_pnl"] >= 0 else "🔴"
            dist       = p["dist_to_liq_pct"]
            dist_emoji = "🚨" if dist < 5 else "⚠️" if dist < 15 else ""
            lines.append(
                f"{side_emoji} *{p['symbol']}* {p['side']} × {p['leverage']}x\n"
                f"  数量：`{p['size']}` | 入场价：`${p['entry_price']:,.2f}`\n"
                f"  爆仓价：`${p['liq_price']:,.2f}` {dist_emoji}(距 `{dist:.1f}%`)\n"
                f"  未实现盈亏：{pnl_emoji} `${p['unrealized_pnl']:+.2f}`"
            )

        if liq_alerts:
            lines.append(f"\n🚨 *爆仓风险预警 {len(liq_alerts)} 个持仓*")
            for a in liq_alerts:
                lines.append(f"  • `{a['symbol']}` — {a['level']} (距爆仓 {a['dist_pct']:.1f}%)")

        return self.ok("\n".join(lines) + stale, data=account)

    # ── 爆仓风险专项 ──────────────────────────────────────────────────────────

    def _liq_risk(self, **_) -> dict:
        account = self.load("hl_account.json")
        if not account:
            return self.err("账户数据未缓存")

        alerts = account.get("liq_alerts", [])
        if not alerts:
            return self.ok("✅ 当前无持仓处于高爆仓风险区间")

        lines = ["🚨 *爆仓风险报告*\n"]
        for a in alerts:
            emoji = "🚨" if a["level"] == "CRITICAL" else "🔴" if a["level"] == "HIGH" else "⚠️"
            lines.append(
                f"{emoji} `{a['symbol']}` — *{a['level']}*\n"
                f"  距爆仓：`{a['dist_pct']:.1f}%`\n"
                f"  建议：{'立即减仓或补充保证金！' if a['level'] == 'CRITICAL' else '密切关注，考虑减仓'}"
            )

        return self.ok("\n".join(lines), data={"liq_alerts": alerts})
