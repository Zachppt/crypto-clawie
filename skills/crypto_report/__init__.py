"""
skills/crypto_report — 每日/每周报告生成
汇总市场数据、账户持仓、资金费率信号，生成 Markdown 报告。
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from skills.base import BaseSkill


class CryptoReportSkill(BaseSkill):

    def run(self, period: str = "daily", **_) -> dict:
        if period == "weekly":
            return self._weekly_report()
        return self._daily_report()

    # ── 每日报告 ──────────────────────────────────────────────────────────────

    def _daily_report(self) -> dict:
        now    = datetime.now(timezone.utc)
        market = self.load("hl_market.json")
        snap   = self.load("market_snapshot.json")
        account = self.load("hl_account.json")

        lines = [
            f"📋 *每日市场报告*",
            f"_{now.strftime('%Y-%m-%d %H:%M UTC')}_\n",
        ]

        # 恐慌贪婪
        if snap:
            fng = snap.get("fear_greed", {})
            if fng:
                emoji = "😱" if fng["value"] < 25 else "😨" if fng["value"] < 45 else "😐" if fng["value"] < 55 else "😊" if fng["value"] < 75 else "🤑"
                lines.append(f"*市场情绪*：{emoji} {fng['value']} ({fng['label']})\n")

        # 主流价格
        if snap:
            prices = snap.get("prices", {})
            lines.append("*主流价格*")
            for sym in ["BTC", "ETH", "SOL", "BNB"]:
                if sym in prices:
                    p     = prices[sym]
                    emoji = "🟢" if p["change_24h"] >= 0 else "🔴"
                    lines.append(f"{emoji} {sym}：${p['price']:,.2f} ({p['change_24h']:+.2f}%)")
            lines.append("")

        # HL 资金费率异常
        if market:
            extreme = [a for a in market.get("assets", []) if abs(a["funding_8h"]) >= 0.0005]
            if extreme:
                lines.append(f"*HL 资金费率异动* ({len(extreme)} 个标的)")
                for a in sorted(extreme, key=lambda x: abs(x["funding_8h"]), reverse=True)[:5]:
                    rate  = a["funding_8h"]
                    emoji = "🔴" if abs(rate) >= 0.001 else "🟡"
                    lines.append(f"{emoji} {a['symbol']}：{rate*100:+.4f}%/8h (年化 {a['funding_annualized']:+.1f}%)")
                lines.append("")

        # 账户状态
        if account:
            positions = account.get("positions", [])
            acct_val  = account.get("account_value_usdc", 0)
            lines.append(f"*账户状态*")
            lines.append(f"余额：${acct_val:,.2f} USDC")
            if positions:
                total_pnl = sum(p["unrealized_pnl"] for p in positions)
                pnl_emoji = "🟢" if total_pnl >= 0 else "🔴"
                lines.append(f"持仓数：{len(positions)} | 总未实现盈亏：{pnl_emoji} ${total_pnl:+.2f}")
                liq_alerts = account.get("liq_alerts", [])
                if liq_alerts:
                    lines.append(f"⚠️ 爆仓风险：{len(liq_alerts)} 个持仓需关注")
            else:
                lines.append("当前无持仓")

        text = "\n".join(lines)
        self._save_report("daily", text)
        return self.ok(text)

    # ── 每周报告 ──────────────────────────────────────────────────────────────

    def _weekly_report(self) -> dict:
        now = datetime.now(timezone.utc)

        lines = [
            f"📊 *每周复盘报告*",
            f"_{now.strftime('%Y-%m-%d')} (本周)_\n",
            "*本周交易记录*",
        ]

        # 读取交易历史
        history_path = self.memory_dir / "trade_history.json"
        if history_path.exists():
            try:
                with open(history_path) as f:
                    history = json.load(f)
                recent = history[-20:] if len(history) > 20 else history
                if recent:
                    for t in recent:
                        side_emoji = "📈" if t.get("side") == "long" else "📉"
                        lines.append(
                            f"{side_emoji} {t.get('symbol')} {t.get('side')} "
                            f"× {t.get('leverage')}x | "
                            f"${t.get('price', 0):,.2f} | "
                            f"{t.get('timestamp', '')[:10]}"
                        )
                else:
                    lines.append("本周无交易记录")
            except Exception:
                lines.append("读取交易记录失败")
        else:
            lines.append("暂无交易记录")

        lines.append("\n_如需深度复盘，请告诉我具体关注哪个交易。_")

        text = "\n".join(lines)
        self._save_report("weekly", text)
        return self.ok(text)

    def _save_report(self, period: str, text: str):
        reports_dir = self.data_dir.parent / "reports"
        reports_dir.mkdir(exist_ok=True)
        date    = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path    = reports_dir / f"{period}_{date}.md"
        path.write_text(text, encoding="utf-8")
