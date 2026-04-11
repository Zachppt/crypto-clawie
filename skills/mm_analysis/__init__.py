"""
skills/mm_analysis — 做市商行为阶段识别
基于多维快照信号推断主力/做市商当前处于哪个操盘阶段：
  ACCUMULATION  — 积累期：低调建仓，价格横盘，情绪中性
  WASH_TRADING  — 洗盘期：高量小波，震出浮筹
  DISTRIBUTION  — 派发期：高位放量，筹码转手给散户
  PUMP_SETUP    — 二次拉升准备：浮筹出清，资金费率重置

检测维度：
  • 资金费率（HL）
  • 24h 价格涨跌幅
  • 未平仓量（OI）当前水平
  • 跨所价差（异常则洗盘/操控信号）
  • 交易所净流量（需 Etherscan Key）
  • 成交量/OI 比（换手率）
"""
from __future__ import annotations

import requests
from skills.base import BaseSkill

_S = requests.Session()
_S.headers["User-Agent"] = "crypto-clawie/2.0"
_T = 5

PHASE_LABELS = {
    "ACCUMULATION": "积累期",
    "WASH_TRADING":  "洗盘期",
    "DISTRIBUTION":  "派发期",
    "PUMP_SETUP":    "拉升准备期",
}

PHASE_TIPS = {
    "ACCUMULATION": (
        "主力低调建仓，价格区间收窄，是进场前的观察窗口。\n"
        "• 策略：耐心等待突破信号，不追高\n"
        "• 风险：积累期可能持续数周"
    ),
    "WASH_TRADING": (
        "主力震仓洗盘，制造恐慌让散户低价割肉。\n"
        "• 策略：坚持持仓 / 小仓位低接（需有较强信念）\n"
        "• 风险：短期仍可能继续下探"
    ),
    "DISTRIBUTION": (
        "主力在高位向散户派发筹码，风险极高。\n"
        "• 策略：考虑减仓或做空，避免接高位盘\n"
        "• 风险：价格可能仍会短期上涨再暴跌"
    ),
    "PUMP_SETUP": (
        "浮筹清洗完毕，资金费率重置，可能酝酿二次拉升。\n"
        "• 策略：可关注做多机会，量价齐升时跟进\n"
        "• 风险：未必一定拉升，需配合其他信号确认"
    ),
}


def _binance_volume(symbol: str) -> float | None:
    """获取 Binance 合约 24h 成交量（USD）。"""
    try:
        r = _S.get(
            f"https://fapi.binance.com/fapi/v1/ticker/24hr?symbol={symbol}USDT",
            timeout=_T,
        )
        r.raise_for_status()
        d = r.json()
        return float(d.get("quoteVolume", 0))
    except Exception:
        return None

def _binance_spread(symbol: str, hl_price: float) -> float | None:
    """计算 Binance 现货价与 HL 价格的价差百分比。"""
    try:
        r = _S.get(
            f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}USDT",
            timeout=_T,
        )
        r.raise_for_status()
        bn_price = float(r.json()["price"])
        if hl_price and bn_price:
            return abs(bn_price - hl_price) / hl_price * 100
    except Exception:
        pass
    return None


class MMAnalysisSkill(BaseSkill):

    def run(self, action: str = "analyze", **kwargs) -> dict:
        dispatch = {
            "analyze": self._analyze,
            "scan":    self._scan_all,
        }
        fn = dispatch.get(action.lower())
        if not fn:
            return self.err(f"未知操作：{action}。可用：analyze / scan")
        return fn(**kwargs)

    # ── 单币种分析 ────────────────────────────────────────────────────────────

    def _analyze(self, symbol: str = "BTC", **_) -> dict:
        sym    = symbol.upper()
        market = self.load("hl_market.json")
        if not market:
            return self.err("HL 市场数据未缓存，请等待 fetcher 运行")

        asset = next((a for a in market.get("assets", []) if a["symbol"] == sym), None)
        if not asset:
            return self.err(f"未找到 {sym} 的数据")

        funding   = asset["funding_8h"]
        change_24h= asset["change_24h_pct"]
        price     = asset["mark_price"]
        oi        = asset["open_interest"] * price  # OI in USD

        # 额外指标（失败了不影响主流程）
        volume_24h = _binance_volume(sym)
        spread_pct = _binance_spread(sym, price)

        # 换手率：成交量 / OI（高换手 = 大量换手，可能洗盘或派发）
        turnover_ratio = (volume_24h / oi) if volume_24h and oi else None

        phase, scores, reasons = self._score_phases(
            funding, change_24h, oi, turnover_ratio, spread_pct
        )

        phase_label = PHASE_LABELS[phase]
        tip         = PHASE_TIPS[phase]
        confidence  = scores[phase] / max(sum(scores.values()), 1)

        conf_label = (
            "高" if confidence >= 0.5 else
            "中" if confidence >= 0.3 else "低"
        )

        phase_emoji = {
            "ACCUMULATION": "🟡",
            "WASH_TRADING":  "🔵",
            "DISTRIBUTION":  "🔴",
            "PUMP_SETUP":    "🟢",
        }[phase]

        lines = [
            f"🕵️ *{sym} 做市商阶段分析*\n",
            f"{phase_emoji} 当前阶段：*{phase_label}*（置信度：{conf_label} {confidence*100:.0f}%）\n",
            f"*信号依据：*",
        ]
        for r in reasons:
            lines.append(f"  • {r}")

        lines.append(f"\n*各阶段得分：*")
        for p, s in sorted(scores.items(), key=lambda x: x[1], reverse=True):
            bar = "█" * s + "░" * (10 - min(s, 10))
            lines.append(f"  {PHASE_LABELS[p]}：`{bar}` {s}分")

        lines.append(f"\n💡 *操作建议*\n{tip}")

        # 附加数据指标
        meta_lines = [f"\n📊 *数据快照*"]
        meta_lines.append(f"价格：`${price:,.2f}` | 24h：`{change_24h:+.2f}%`")
        meta_lines.append(f"资金费率：`{funding*100:+.4f}%/8h`")
        meta_lines.append(f"OI：`${oi/1e6:.1f}M`")
        if turnover_ratio is not None:
            meta_lines.append(f"换手率(量/OI)：`{turnover_ratio:.2f}x`"
                              + (" ⚠️ 异常高" if turnover_ratio > 3 else ""))
        if spread_pct is not None:
            meta_lines.append(f"Binance-HL 价差：`{spread_pct:.3f}%`"
                              + (" ⚠️ 异常大" if spread_pct > 0.2 else ""))
        lines.extend(meta_lines)

        return self.ok("\n".join(lines), data={
            "symbol":          sym,
            "phase":           phase,
            "phase_label":     phase_label,
            "confidence":      round(confidence, 3),
            "scores":          scores,
            "reasons":         reasons,
            "funding_8h":      funding,
            "change_24h_pct":  change_24h,
            "oi_usd":          round(oi, 0),
            "turnover_ratio":  turnover_ratio,
            "spread_pct":      spread_pct,
        })

    # ── 全市场扫描 ────────────────────────────────────────────────────────────

    def _scan_all(self, top: int = 10, **_) -> dict:
        """扫描资金费率 Top 资产，输出各阶段分布概览。"""
        market = self.load("hl_market.json")
        if not market:
            return self.err("市场数据未缓存")

        # 取资金费率绝对值前 top 名
        top_assets = sorted(
            market.get("assets", []),
            key=lambda x: abs(x["funding_8h"]),
            reverse=True,
        )[:top]

        phase_results = []
        for a in top_assets:
            funding    = a["funding_8h"]
            change_24h = a["change_24h_pct"]
            price      = a["mark_price"]
            oi         = a["open_interest"] * price
            phase, scores, _ = self._score_phases(funding, change_24h, oi, None, None)
            phase_results.append((a["symbol"], phase, funding))

        # 统计各阶段数量
        from collections import Counter
        phase_counts = Counter(p for _, p, _ in phase_results)

        lines = [f"🕵️ *做市商阶段扫描 Top {top}（按资金费率排行）*\n"]

        phase_emojis = {
            "ACCUMULATION": "🟡",
            "WASH_TRADING":  "🔵",
            "DISTRIBUTION":  "🔴",
            "PUMP_SETUP":    "🟢",
        }
        for sym, phase, rate in phase_results:
            lines.append(
                f"{phase_emojis[phase]} `{sym:6s}` {PHASE_LABELS[phase]}  "
                f"费率 `{rate*100:+.4f}%`"
            )

        lines.append(f"\n*阶段分布*：")
        for p, cnt in phase_counts.most_common():
            lines.append(f"  {phase_emojis[p]} {PHASE_LABELS[p]}：{cnt} 个")

        return self.ok("\n".join(lines), data={"phase_results": [
            {"symbol": s, "phase": p, "funding": r} for s, p, r in phase_results
        ]})

    # ── 核心评分逻辑 ──────────────────────────────────────────────────────────

    def _score_phases(
        self,
        funding: float,
        change_24h: float,
        oi_usd: float,
        turnover_ratio: float | None,
        spread_pct: float | None,
    ) -> tuple[str, dict, list]:
        """
        基于当前快照对四个做市商阶段打分（0–10），返回最高分阶段。
        返回: (phase_name, scores_dict, reasons_list)
        """
        scores  = {"ACCUMULATION": 0, "WASH_TRADING": 0, "DISTRIBUTION": 0, "PUMP_SETUP": 0}
        reasons = []

        # ── 资金费率信号 ─────────────────────────────────────────────────────
        abs_rate = abs(funding)

        if abs_rate < 0.0002:
            scores["ACCUMULATION"] += 3
            scores["PUMP_SETUP"]   += 2
            reasons.append(f"资金费率接近零（{funding*100:+.4f}%），市场情绪中性/重置")

        elif 0.0002 <= abs_rate < 0.0005:
            if funding > 0:
                scores["ACCUMULATION"] += 1
                scores["DISTRIBUTION"] += 1
                reasons.append(f"资金费率微正（{funding*100:+.4f}%），多头略占优")
            else:
                scores["ACCUMULATION"] += 2
                scores["PUMP_SETUP"]   += 1
                reasons.append(f"资金费率微负（{funding*100:+.4f}%），空头略多，可能积累")

        elif 0.0005 <= abs_rate < 0.001:
            if funding > 0:
                scores["DISTRIBUTION"] += 2
                scores["WASH_TRADING"] += 1
                reasons.append(f"资金费率偏高正值（{funding*100:+.4f}%），多头成本上升")
            else:
                scores["PUMP_SETUP"]   += 3
                scores["WASH_TRADING"] += 1
                reasons.append(f"资金费率负值偏大（{funding*100:+.4f}%），空头过热，反弹基础强")

        else:  # > 0.001
            if funding > 0:
                scores["DISTRIBUTION"] += 4
                reasons.append(f"资金费率极端正值（{funding*100:+.4f}%），多头严重过热，派发特征")
            else:
                scores["PUMP_SETUP"]   += 4
                reasons.append(f"资金费率极端负值（{funding*100:+.4f}%），极端恐慌，二次拉升前常见")

        # ── 价格变化信号 ─────────────────────────────────────────────────────
        abs_change = abs(change_24h)

        if abs_change < 2:
            scores["ACCUMULATION"] += 3
            scores["WASH_TRADING"] += 1
            reasons.append(f"价格横盘（24h {change_24h:+.1f}%），典型积累或整理形态")

        elif 2 <= abs_change < 5:
            if change_24h > 0:
                scores["DISTRIBUTION"] += 1
                scores["PUMP_SETUP"]   += 1
                reasons.append(f"价格温和上涨（{change_24h:+.1f}%）")
            else:
                scores["WASH_TRADING"] += 2
                scores["PUMP_SETUP"]   += 1
                reasons.append(f"价格小幅回调（{change_24h:+.1f}%），可能洗盘")

        elif 5 <= abs_change < 12:
            if change_24h > 0:
                scores["DISTRIBUTION"] += 2
                reasons.append(f"价格明显上涨（{change_24h:+.1f}%），可能进入派发区")
            else:
                scores["WASH_TRADING"] += 2
                scores["PUMP_SETUP"]   += 2
                reasons.append(f"价格大幅回调（{change_24h:+.1f}%），浮筹出清中")

        else:  # > 12%
            if change_24h > 0:
                scores["DISTRIBUTION"] += 3
                reasons.append(f"价格暴涨（{change_24h:+.1f}%），典型派发高峰")
            else:
                scores["WASH_TRADING"] += 3
                reasons.append(f"价格暴跌（{change_24h:+.1f}%），强力洗盘或清算")

        # ── 换手率信号（成交量/OI） ──────────────────────────────────────────
        if turnover_ratio is not None:
            if turnover_ratio < 0.5:
                scores["ACCUMULATION"] += 2
                reasons.append(f"换手率低（{turnover_ratio:.2f}x），成交清淡，积累特征")
            elif 0.5 <= turnover_ratio < 2:
                pass  # 正常
            elif 2 <= turnover_ratio < 4:
                scores["WASH_TRADING"] += 2
                scores["DISTRIBUTION"] += 1
                reasons.append(f"换手率较高（{turnover_ratio:.2f}x），大量筹码换手")
            else:  # > 4
                scores["WASH_TRADING"] += 3
                scores["DISTRIBUTION"] += 2
                reasons.append(f"换手率异常高（{turnover_ratio:.2f}x），可能洗盘/派发")

        # ── 跨所价差信号 ─────────────────────────────────────────────────────
        if spread_pct is not None:
            if spread_pct > 0.3:
                scores["WASH_TRADING"] += 2
                reasons.append(f"跨所价差异常大（{spread_pct:.2f}%），可能存在操纵/拉盘")
            elif spread_pct > 0.1:
                scores["WASH_TRADING"] += 1
                reasons.append(f"跨所价差偏大（{spread_pct:.2f}%）")

        # ── 选出最高分阶段 ────────────────────────────────────────────────────
        phase = max(scores, key=scores.get)

        # 平局处理：多个阶段同分，用资金费率方向打破
        max_score = scores[phase]
        tied = [p for p, s in scores.items() if s == max_score]
        if len(tied) > 1:
            if funding > 0.0003:
                phase = "DISTRIBUTION" if "DISTRIBUTION" in tied else tied[0]
            elif funding < -0.0003:
                phase = "PUMP_SETUP" if "PUMP_SETUP" in tied else tied[0]
            else:
                phase = "ACCUMULATION" if "ACCUMULATION" in tied else tied[0]

        return phase, scores, reasons
