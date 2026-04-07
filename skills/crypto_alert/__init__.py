"""
skills/crypto_alert — 多因子异动信号扫描
基于置信度评分模型，结合资金费率、OI、价格动量综合判断。
"""

from datetime import datetime, timezone
from skills.base import BaseSkill


def _confidence_bar(score: float, width: int = 10) -> str:
    """将 0-1 置信度转换为可视化进度条。"""
    filled = round(score * width)
    return "█" * filled + "░" * (width - filled) + f" {score*100:.0f}%"


def _is_low_liquidity() -> bool:
    """判断是否处于亚洲低流动性时段 (UTC 16:00-22:00 = CST 00:00-06:00)。"""
    hour = datetime.now(timezone.utc).hour
    return 16 <= hour < 22


class CryptoAlertSkill(BaseSkill):

    def run(self, action: str = "scan", **kwargs) -> dict:
        dispatch = {
            "scan":        self._scan_all,
            "funding":     self._scan_funding,
            "price":       self._scan_price,
            "liq":         self._scan_liq,
            "funding_arb": self._scan_funding_arb,
        }
        fn = dispatch.get(action.lower(), self._scan_all)
        return fn(**kwargs)

    # ── 总扫描 ────────────────────────────────────────────────────────────────

    def _scan_all(self, min_confidence: float = 0.4, **_) -> dict:
        signals = []
        signals.extend(self._get_funding_signals())
        signals.extend(self._get_price_signals())
        signals.extend(self._get_liq_signals())

        # 按置信度过滤和排序
        signals = [s for s in signals if s["confidence"] >= min_confidence]
        signals.sort(key=lambda x: x["confidence"], reverse=True)

        if not signals:
            return self.ok("✅ 当前无异动信号（置信度 ≥ 40%）")

        lines = [f"🔔 *异动信号* — {len(signals)} 个\n"]
        for s in signals:
            lines.append(
                f"{s['emoji']} *[{s['level']}]* {s['symbol']} {s['type_label']}\n"
                f"  {s['detail']}\n"
                f"  置信度：{_confidence_bar(s['confidence'])}"
            )
            if s.get("advice"):
                lines.append(f"  💡 {s['advice']}")
            lines.append("")

        return self.ok("\n".join(lines).rstrip(), data={"signals": signals})

    # ── 资金费率扫描 ──────────────────────────────────────────────────────────

    def _scan_funding(self, threshold: float = None, **_) -> dict:
        signals = self._get_funding_signals(threshold)
        if not signals:
            return self.ok("✅ 无资金费率异动")
        lines = ["💹 *资金费率异动*\n"]
        for s in signals:
            lines.append(
                f"{s['emoji']} `{s['symbol']}` {s['detail']}\n"
                f"  置信度：{_confidence_bar(s['confidence'])}"
            )
        return self.ok("\n".join(lines), data={"signals": signals})

    # ── 价格扫描 ──────────────────────────────────────────────────────────────

    def _scan_price(self, threshold: float = 5.0, **_) -> dict:
        signals = self._get_price_signals(threshold)
        if not signals:
            return self.ok(f"✅ 无价格波动超过 {threshold}% 的信号")
        lines = [f"📊 *价格波动信号 (>{threshold}%)*\n"]
        for s in signals:
            lines.append(
                f"{s['emoji']} `{s['symbol']}` {s['detail']}\n"
                f"  置信度：{_confidence_bar(s['confidence'])}"
            )
        return self.ok("\n".join(lines), data={"signals": signals})

    # ── 爆仓风险扫描 ──────────────────────────────────────────────────────────

    def _scan_liq(self, **_) -> dict:
        signals = self._get_liq_signals()
        if not signals:
            return self.ok("✅ 无爆仓风险信号")
        lines = ["🚨 *爆仓风险信号*\n"]
        for s in signals:
            lines.append(
                f"{s['emoji']} `{s['symbol']}` {s['detail']}\n"
                f"  置信度：{_confidence_bar(s['confidence'])}"
            )
        return self.ok("\n".join(lines), data={"signals": signals})

    # ── 套利机会扫描 ──────────────────────────────────────────────────────────

    def _scan_funding_arb(self, min_rate: float = 0.0005, **_) -> dict:
        """扫描资金费率套利机会（|rate| ≥ min_rate），按绝对值排序返回 Top 5。"""
        market = self.load("hl_market.json")
        if not market:
            return self.err("市场数据未缓存")

        opps = []
        for a in market.get("assets", []):
            rate = a["funding_8h"]
            if abs(rate) < min_rate:
                continue
            ann = abs(rate) * 3 * 365 * 100
            opps.append({
                "symbol":    a["symbol"],
                "rate_8h":   rate,
                "ann_yield": round(ann, 1),
                "price":     a["mark_price"],
                "side":      "做空HL+买现货" if rate > 0 else "做多HL+卖现货",
            })

        opps.sort(key=lambda x: abs(x["rate_8h"]), reverse=True)
        opps = opps[:5]

        if not opps:
            return self.ok(f"✅ 当前无套利机会（阈值 {min_rate*100:.3f}%/8h）")

        lines = [f"💰 *资金费率套利机会 Top {len(opps)}*\n"]
        for o in opps:
            emoji = "🔴" if abs(o["rate_8h"]) >= 0.001 else "🟡"
            lines.append(
                f"{emoji} `{o['symbol']}` {o['rate_8h']*100:+.4f}%/8h | 年化 ~{o['ann_yield']:.0f}%\n"
                f"  策略：{o['side']} | 价格：${o['price']:,.2f}"
            )
        lines.append("\n💡 _套利前请确认对冲成本和手续费_")

        return self.ok("\n".join(lines), data={"opportunities": opps})

    # ── 内部：多因子信号生成 ──────────────────────────────────────────────────

    def _get_funding_signals(self, threshold: float = None) -> list:
        market = self.load("hl_market.json")
        if not market:
            return []
        thr = threshold or float(self.getenv("HL_FUNDING_ALERT_THRESHOLD", "0.0005"))
        low_liq = _is_low_liquidity()
        signals = []

        assets_map = {a["symbol"]: a for a in market.get("assets", [])}

        for a in market.get("assets", []):
            rate     = a["funding_8h"]
            abs_rate = abs(rate)
            if abs_rate < thr:
                continue

            # 基础置信度（费率幅度）
            if abs_rate >= 0.002:
                confidence = 1.0
            elif abs_rate >= 0.001:
                confidence = 0.7
            else:
                confidence = 0.4

            factors = [f"资金费率 {rate*100:+.4f}%/8h"]

            # OI 同向确认：费率为正（多拥挤）+ OI 也在涨
            oi = a.get("open_interest", 0)
            price = a.get("mark_price", 1)
            oi_usd = oi * price
            if oi_usd > 0:
                # 简单判断：OI 绝对值大于 5000 万认为有热度
                if oi_usd > 5e7:
                    confidence = min(confidence + 0.2, 1.0)
                    factors.append(f"OI ${oi_usd/1e6:.0f}M 较高")

            # 价格动量确认
            chg = a.get("change_24h_pct", 0)
            if (rate > 0 and chg > 3) or (rate < 0 and chg < -3):
                confidence = min(confidence + 0.1, 1.0)
                factors.append(f"价格24h {chg:+.1f}% 同向")

            # 低流动性时段折扣
            if low_liq:
                confidence = max(confidence - 0.1, 0.1)
                factors.append("低流动性时段")

            direction = "多头付空头" if rate > 0 else "空头付多头"
            level = "CRITICAL" if abs_rate >= 0.001 else "WARNING"
            emoji = "🔴" if level == "CRITICAL" else "🟡"
            ann   = a["funding_annualized"]

            advice = None
            if rate > 0 and abs_rate >= 0.001:
                advice = "多头严重拥挤，考虑减仓或做空套利"
            elif rate > 0 and abs_rate >= 0.0005:
                advice = "做多前检查资金费成本"

            signals.append({
                "type":       "funding",
                "type_label": "资金费率异动",
                "level":      level,
                "emoji":      emoji,
                "symbol":     a["symbol"],
                "confidence": round(confidence, 2),
                "detail":     f"{rate*100:+.4f}%/8h ({direction}) | 年化 {ann:+.1f}%",
                "factors":    factors,
                "advice":     advice,
                "message":    f"`{a['symbol']}` 资金费率 `{rate*100:+.4f}%`/8h ({direction})",
            })

        return signals

    def _get_price_signals(self, threshold: float = 5.0) -> list:
        market = self.load("hl_market.json")
        if not market:
            return []
        low_liq = _is_low_liquidity()
        signals = []

        for a in market.get("assets", []):
            chg = a.get("change_24h_pct", 0)
            if abs(chg) < threshold:
                continue

            # 基础置信度
            if abs(chg) >= 20:
                confidence = 1.0
            elif abs(chg) >= 10:
                confidence = 0.7
            else:
                confidence = 0.4

            factors = [f"24h 价格变化 {chg:+.2f}%"]
            rate = a.get("funding_8h", 0)

            # 资金费率同向确认
            if (chg > 0 and rate > 0.0005) or (chg < 0 and rate < -0.0005):
                confidence = min(confidence + 0.2, 1.0)
                factors.append(f"资金费率同向 {rate*100:+.4f}%")

            if low_liq:
                confidence = max(confidence - 0.1, 0.1)
                factors.append("低流动性时段")

            emoji = "🟢" if chg > 0 else "🔴"
            signals.append({
                "type":       "price",
                "type_label": "价格异动",
                "level":      "INFO",
                "emoji":      emoji,
                "symbol":     a["symbol"],
                "confidence": round(confidence, 2),
                "detail":     f"24h {chg:+.2f}% | 当前 ${a['mark_price']:,.2f}",
                "factors":    factors,
                "advice":     None,
                "message":    f"`{a['symbol']}` 24h {chg:+.2f}%",
            })

        return signals

    def _get_liq_signals(self) -> list:
        account = self.load("hl_account.json")
        if not account:
            return []
        signals = []
        for alert in account.get("liq_alerts", []):
            dist  = alert["dist_pct"]
            level = alert["level"]

            if dist < 5:
                confidence = 1.0
                advice = "🚨 立即减仓或追加保证金！"
            elif dist < 10:
                confidence = 0.7
                advice = "密切关注，考虑减仓"
            else:
                confidence = 0.4
                advice = "保持关注"

            emoji = {"CRITICAL": "🚨", "HIGH": "🔴", "MEDIUM": "⚠️"}.get(level, "⚠️")
            signals.append({
                "type":       "liquidation",
                "type_label": "爆仓风险",
                "level":      level,
                "emoji":      emoji,
                "symbol":     alert["symbol"],
                "confidence": confidence,
                "detail":     f"距爆仓仅剩 {dist:.1f}%",
                "factors":    [f"爆仓距离 {dist:.1f}%"],
                "advice":     advice,
                "message":    f"`{alert['symbol']}` 距爆仓 {dist:.1f}% — {level}",
            })

        return signals
