"""
skills/crypto_alert — 异动信号扫描
扫描资金费率、价格变化、爆仓风险等异动信号。
"""

from skills.base import BaseSkill


class CryptoAlertSkill(BaseSkill):

    def run(self, action: str = "scan", **kwargs) -> dict:
        dispatch = {
            "scan":    self._scan_all,
            "funding": self._scan_funding,
            "price":   self._scan_price,
            "liq":     self._scan_liq,
        }
        fn = dispatch.get(action.lower(), self._scan_all)
        return fn(**kwargs)

    def _scan_all(self, **_) -> dict:
        signals = []
        signals.extend(self._get_funding_signals())
        signals.extend(self._get_price_signals())
        signals.extend(self._get_liq_signals())

        if not signals:
            return self.ok("✅ 当前无异动信号")

        lines = [f"🔔 *异动信号扫描* — 发现 {len(signals)} 个\n"]
        for s in signals:
            lines.append(f"{s['emoji']} [{s['level']}] {s['message']}")

        return self.ok("\n".join(lines), data={"signals": signals})

    def _scan_funding(self, threshold: float = None, **_) -> dict:
        signals = self._get_funding_signals(threshold)
        if not signals:
            return self.ok("✅ 无资金费率异动")
        lines = ["💹 *资金费率异动*\n"]
        for s in signals:
            lines.append(f"{s['emoji']} {s['message']}")
        return self.ok("\n".join(lines), data={"signals": signals})

    def _scan_price(self, threshold: float = 5.0, **_) -> dict:
        signals = self._get_price_signals(threshold)
        if not signals:
            return self.ok(f"✅ 无价格波动超过 {threshold}% 的信号")
        lines = [f"📊 *价格波动信号 (>{threshold}%)*\n"]
        for s in signals:
            lines.append(f"{s['emoji']} {s['message']}")
        return self.ok("\n".join(lines), data={"signals": signals})

    def _scan_liq(self, **_) -> dict:
        signals = self._get_liq_signals()
        if not signals:
            return self.ok("✅ 无爆仓风险信号")
        lines = ["🚨 *爆仓风险信号*\n"]
        for s in signals:
            lines.append(f"{s['emoji']} {s['message']}")
        return self.ok("\n".join(lines), data={"signals": signals})

    # ── 内部信号生成 ──────────────────────────────────────────────────────────

    def _get_funding_signals(self, threshold: float = None) -> list:
        market = self.load("hl_market.json")
        if not market:
            return []
        thr = threshold or float(self.getenv("HL_FUNDING_ALERT_THRESHOLD", "0.0005"))
        signals = []
        for a in market.get("assets", []):
            rate = a["funding_8h"]
            if abs(rate) >= thr:
                level  = "CRITICAL" if abs(rate) >= 0.001 else "WARNING"
                emoji  = "🔴" if level == "CRITICAL" else "🟡"
                direct = "多付空" if rate > 0 else "空付多"
                signals.append({
                    "type":    "funding",
                    "level":   level,
                    "emoji":   emoji,
                    "symbol":  a["symbol"],
                    "message": f"`{a['symbol']}` 资金费率 `{rate*100:+.4f}%`/8h ({direct}) 年化 `{a['funding_annualized']:+.1f}%`",
                })
        return signals

    def _get_price_signals(self, threshold: float = 5.0) -> list:
        market = self.load("hl_market.json")
        if not market:
            return []
        signals = []
        for a in market.get("assets", []):
            chg = a.get("change_24h_pct", 0)
            if abs(chg) >= threshold:
                emoji = "🟢" if chg > 0 else "🔴"
                signals.append({
                    "type":    "price",
                    "level":   "INFO",
                    "emoji":   emoji,
                    "symbol":  a["symbol"],
                    "message": f"`{a['symbol']}` 24h 变化 `{chg:+.2f}%` 当前价 `${a['mark_price']:,.2f}`",
                })
        return signals

    def _get_liq_signals(self) -> list:
        account = self.load("hl_account.json")
        if not account:
            return []
        signals = []
        for alert in account.get("liq_alerts", []):
            emoji = "🚨" if alert["level"] == "CRITICAL" else "🔴" if alert["level"] == "HIGH" else "⚠️"
            signals.append({
                "type":    "liquidation",
                "level":   alert["level"],
                "emoji":   emoji,
                "symbol":  alert["symbol"],
                "message": f"`{alert['symbol']}` 距爆仓 `{alert['dist_pct']:.1f}%` — {alert['level']}",
            })
        return signals
