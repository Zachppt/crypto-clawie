"""
skills/mm_analysis — 做市商行为阶段识别（跨所版）
数据来源：Binance + OKX + Bybit + Hyperliquid（ccxt + HL 缓存）

阶段定义：
  ACCUMULATION  — 积累期：低调建仓，价格横盘，各所费率一致偏低
  WASH_TRADING  — 洗盘期：高量小波，跨所费率分歧，MM 套利对冲
  DISTRIBUTION  — 派发期：多所费率极端正值共识，OI 增而价格难涨
  PUMP_SETUP    — 拉升准备：各所费率负值/重置，OI 稳定，浮筹出清

核心跨所信号：
  • 资金费率共识  — 极差 < 0.01%   → 市场方向一致
  • 资金费率分歧  — 极差 > 0.05%   → MM 正在跨所套利对冲
  • OI 分布      — Binance 占比异常 → 机构集中建/清仓
  • 加权费率      — 按成交量加权的"真实"市场费率
"""
from __future__ import annotations

import math
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from skills.base import BaseSkill

# ── 常量 ──────────────────────────────────────────────────────────────────────

PHASE_LABELS = {
    "ACCUMULATION": "积累期",
    "WASH_TRADING":  "洗盘期",
    "DISTRIBUTION":  "派发期",
    "PUMP_SETUP":    "拉升准备期",
}

PHASE_EMOJIS = {
    "ACCUMULATION": "🟡",
    "WASH_TRADING":  "🔵",
    "DISTRIBUTION":  "🔴",
    "PUMP_SETUP":    "🟢",
}

PHASE_TIPS = {
    "ACCUMULATION": (
        "主力低调建仓，价格区间收窄。\n"
        "• 策略：耐心等待突破信号，不追高\n"
        "• 跨所信号：各所费率一致且低 → 机构悄悄吸筹"
    ),
    "WASH_TRADING": (
        "主力震仓洗盘，制造恐慌让散户割肉。\n"
        "• 策略：坚持 / 小仓低接（需有强信念）\n"
        "• 跨所信号：费率分歧大 → MM 在跨所套利同时洗盘"
    ),
    "DISTRIBUTION": (
        "主力在高位向散户派发筹码，风险极高。\n"
        "• 策略：考虑减仓/做空，不接高位\n"
        "• 跨所信号：多所费率极端一致正值 → 散户全面疯多，MM在出货"
    ),
    "PUMP_SETUP": (
        "浮筹清洗完毕，资金费率重置，可能酝酿拉升。\n"
        "• 策略：关注做多机会，量价齐升时跟进\n"
        "• 跨所信号：负费率共识 / 各所OI稳定 → MM已完成建仓"
    ),
}

_PERP_TYPE = {
    "binance": "future",
    "okx":     "swap",
    "bybit":   "linear",
}

_EXCHANGE_LABELS = {
    "binance": "Binance",
    "okx":     "OKX",
    "bybit":   "Bybit",
}

# Binance 在全市场 perp 成交量的估算权重（用于加权费率）
_VOLUME_WEIGHTS = {"binance": 0.55, "okx": 0.25, "bybit": 0.20}


# ── ccxt 数据抓取 ──────────────────────────────────────────────────────────────

def _fetch_one(ex_name: str, symbol: str) -> tuple[str, dict | None]:
    """
    从单个交易所并行抓取：资金费率 + OI + Ticker。
    返回 (exchange_name, data_dict | None)
    """
    try:
        import ccxt
        ex = getattr(ccxt, ex_name)({
            "options": {"defaultType": _PERP_TYPE[ex_name]},
            "timeout": 9000,
        })
        sym = f"{symbol}/USDT:USDT"

        funding, oi_amount, ticker = None, None, None

        def _get_funding():
            fr = ex.fetch_funding_rate(sym)
            return float(fr.get("fundingRate") or 0)

        def _get_oi():
            oi = ex.fetch_open_interest(sym)
            val = oi.get("openInterestValue") or oi.get("openInterestAmount")
            return float(val or 0)

        def _get_ticker():
            return ex.fetch_ticker(sym)

        with ThreadPoolExecutor(max_workers=3) as pool:
            f_funding = pool.submit(_get_funding)
            f_oi      = pool.submit(_get_oi)
            f_ticker  = pool.submit(_get_ticker)
            funding   = f_funding.result(timeout=9)
            oi_raw    = f_oi.result(timeout=9)
            ticker    = f_ticker.result(timeout=9)

        price      = float(ticker.get("last") or 0)
        volume_24h = float(ticker.get("quoteVolume") or 0)
        change_24h = float(ticker.get("percentage") or 0)

        # OI: 如果 openInterestValue 不存在，用数量 × 价格估算
        oi_usd = oi_raw if oi_raw > 1000 else oi_raw * price

        return ex_name, {
            "funding":    funding,
            "oi_usd":     oi_usd,
            "price":      price,
            "volume_24h": volume_24h,
            "change_24h": change_24h,
        }
    except Exception:
        return ex_name, None


def _fetch_all_exchanges(symbol: str) -> dict[str, dict | None]:
    """并行抓取 Binance / OKX / Bybit，返回 {exchange: data}。"""
    results = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futs = {pool.submit(_fetch_one, ex, symbol): ex for ex in _PERP_TYPE}
        for fut in as_completed(futs, timeout=12):
            ex_name, data = fut.result()
            results[ex_name] = data
    return results


# ── 跨所信号计算 ───────────────────────────────────────────────────────────────

def _cross_signals(cross: dict[str, dict | None], hl_funding: float
                   ) -> tuple[dict, list[str], dict]:
    """
    计算跨所信号，返回 (score_additions, reasons, cross_stats)。
    cross_stats 包含摘要数据供展示用。
    """
    scores = {"ACCUMULATION": 0, "WASH_TRADING": 0, "DISTRIBUTION": 0, "PUMP_SETUP": 0}
    reasons: list[str] = []

    # 收集各所有效数据
    fundings: list[float] = []
    ois:      list[float] = []
    volumes:  list[float] = []
    ex_labels: list[str]  = []

    for ex, d in cross.items():
        if d and d.get("funding") is not None:
            fundings.append(d["funding"])
            ex_labels.append(_EXCHANGE_LABELS.get(ex, ex))
            if d.get("oi_usd"):
                ois.append(d["oi_usd"])
            if d.get("volume_24h"):
                volumes.append(d["volume_24h"])

    # 加入 HL
    fundings.append(hl_funding)
    ex_labels.append("HL")

    if len(fundings) < 2:
        return scores, ["跨所数据不足，无法计算分歧"], {}

    avg_f   = sum(fundings) / len(fundings)
    std_f   = statistics.stdev(fundings)
    max_f   = max(fundings)
    min_f   = min(fundings)
    div_f   = max_f - min_f   # 极差

    # 加权平均费率（按交易所权重，HL 给 10%）
    weighted = 0.0
    w_total  = 0.0
    for ex, d in cross.items():
        if d and d.get("funding") is not None:
            w = _VOLUME_WEIGHTS.get(ex, 0.1)
            weighted += d["funding"] * w
            w_total  += w
    weighted += hl_funding * 0.10
    w_total  += 0.10
    weighted_avg = weighted / w_total if w_total else avg_f

    total_oi  = sum(ois)
    total_vol = sum(volumes)

    # ── 信号 1：费率共识 or 分歧 ─────────────────────────────────────────────
    if div_f < 0.0001:
        reasons.append(f"各所费率高度一致（极差 {div_f*100:.4f}%），市场方向一致")
        if weighted_avg > 0.0008:
            scores["DISTRIBUTION"] += 4
        elif weighted_avg > 0.0003:
            scores["DISTRIBUTION"] += 2
            scores["ACCUMULATION"] += 1
        elif weighted_avg > -0.0001:
            scores["ACCUMULATION"] += 3
        else:
            scores["PUMP_SETUP"]   += 3
            scores["ACCUMULATION"] += 1

    elif div_f > 0.0005:
        reasons.append(f"⚡ 跨所费率分歧显著（极差 {div_f*100:.4f}%），MM 正在套利对冲")
        scores["WASH_TRADING"] += 3
        # 找出费率最高/最低所
        ex_funding = {ex: d["funding"] for ex, d in cross.items() if d and d.get("funding") is not None}
        if ex_funding:
            high_ex = max(ex_funding, key=ex_funding.get)
            low_ex  = min(ex_funding, key=ex_funding.get)
            high_lbl = _EXCHANGE_LABELS.get(high_ex, high_ex)
            low_lbl  = _EXCHANGE_LABELS.get(low_ex, low_ex)
            reasons.append(
                f"MM 策略推断：做空 {high_lbl}（收 {ex_funding[high_ex]*100:+.4f}% 费率），"
                f"做多 {low_lbl}（{ex_funding[low_ex]*100:+.4f}%）"
            )
            if ex_funding[high_ex] > 0.001:
                scores["DISTRIBUTION"] += 2  # 高费率所在派发
    else:
        reasons.append(f"各所费率轻微分歧（极差 {div_f*100:.4f}%），过渡状态")
        scores["ACCUMULATION"] += 1
        scores["WASH_TRADING"] += 1

    # ── 信号 2：加权费率综合判断 ──────────────────────────────────────────────
    abs_wf = abs(weighted_avg)
    if abs_wf < 0.0002:
        reasons.append(f"加权费率接近零（{weighted_avg*100:+.4f}%），情绪中性/重置")
        scores["ACCUMULATION"] += 2
        scores["PUMP_SETUP"]   += 1
    elif weighted_avg > 0.001:
        reasons.append(f"加权费率极端正值（{weighted_avg*100:+.4f}%），散户全面做多")
        scores["DISTRIBUTION"] += 3
    elif weighted_avg > 0.0004:
        reasons.append(f"加权费率偏高（{weighted_avg*100:+.4f}%），多头占优但有回调风险")
        scores["DISTRIBUTION"] += 1
    elif weighted_avg < -0.0005:
        reasons.append(f"加权费率显著负值（{weighted_avg*100:+.4f}%），极端空头情绪")
        scores["PUMP_SETUP"]   += 3
    elif weighted_avg < -0.0001:
        reasons.append(f"加权费率微负（{weighted_avg*100:+.4f}%），空头略多，潜在反弹")
        scores["PUMP_SETUP"]   += 1
        scores["ACCUMULATION"] += 1

    # ── 信号 3：OI 分布 ───────────────────────────────────────────────────────
    if total_oi > 0 and ois:
        binance_oi = (cross.get("binance") or {}).get("oi_usd") or 0
        bn_share   = binance_oi / total_oi if total_oi else 0
        if bn_share > 0.7:
            reasons.append(f"Binance OI 占比 {bn_share*100:.0f}%（偏高），机构集中在此操作")
            scores["ACCUMULATION"] += 1
        elif bn_share < 0.3 and binance_oi > 0:
            reasons.append(f"Binance OI 占比 {bn_share*100:.0f}%（偏低），仓位可能已迁移至其他所")
            scores["WASH_TRADING"] += 1

    # ── 信号 4：成交量/OI 比（全市场换手率） ─────────────────────────────────
    if total_oi > 0 and total_vol > 0:
        turnover = total_vol / total_oi
        if turnover < 0.5:
            scores["ACCUMULATION"] += 2
            reasons.append(f"全市场换手率低（{turnover:.2f}x），成交清淡，积累特征")
        elif turnover > 3:
            scores["WASH_TRADING"] += 2
            scores["DISTRIBUTION"] += 1
            reasons.append(f"全市场换手率高（{turnover:.2f}x），大量筹码换手")

    stats = {
        "weighted_avg_funding": round(weighted_avg, 6),
        "funding_divergence":   round(div_f, 6),
        "funding_std":          round(std_f, 6),
        "total_oi_usd":         total_oi,
        "total_vol_usd":        total_vol,
        "exchange_count":       len([d for d in cross.values() if d]),
    }
    return scores, reasons, stats


# ── HL 原有评分（保留，作为补充信号） ─────────────────────────────────────────

def _score_hl(funding: float, change_24h: float, oi_usd: float,
              turnover_ratio: float | None, spread_pct: float | None
              ) -> tuple[dict, list[str]]:
    scores  = {"ACCUMULATION": 0, "WASH_TRADING": 0, "DISTRIBUTION": 0, "PUMP_SETUP": 0}
    reasons = []

    abs_rate = abs(funding)
    if abs_rate < 0.0002:
        scores["ACCUMULATION"] += 2; scores["PUMP_SETUP"] += 1
        reasons.append(f"HL 费率近零（{funding*100:+.4f}%）")
    elif abs_rate < 0.0005:
        if funding > 0: scores["DISTRIBUTION"] += 1
        else:           scores["PUMP_SETUP"] += 1; scores["ACCUMULATION"] += 1
    elif abs_rate < 0.001:
        if funding > 0: scores["DISTRIBUTION"] += 2; scores["WASH_TRADING"] += 1
        else:           scores["PUMP_SETUP"] += 3
        reasons.append(f"HL 费率偏{'高正' if funding>0 else '大负'}（{funding*100:+.4f}%）")
    else:
        if funding > 0: scores["DISTRIBUTION"] += 3
        else:           scores["PUMP_SETUP"] += 4
        reasons.append(f"HL 费率极端（{funding*100:+.4f}%）")

    abs_chg = abs(change_24h)
    if abs_chg < 2:
        scores["ACCUMULATION"] += 3; scores["WASH_TRADING"] += 1
        reasons.append(f"价格横盘（24h {change_24h:+.1f}%）")
    elif abs_chg < 5:
        if change_24h > 0: scores["DISTRIBUTION"] += 1; scores["PUMP_SETUP"] += 1
        else:               scores["WASH_TRADING"] += 2; scores["PUMP_SETUP"] += 1
        reasons.append(f"价格{'小涨' if change_24h>0 else '小跌'}（{change_24h:+.1f}%）")
    elif abs_chg < 12:
        if change_24h > 0: scores["DISTRIBUTION"] += 2
        else:               scores["WASH_TRADING"] += 2; scores["PUMP_SETUP"] += 2
        reasons.append(f"价格{'明显上涨' if change_24h>0 else '大幅回调'}（{change_24h:+.1f}%）")
    else:
        if change_24h > 0: scores["DISTRIBUTION"] += 3
        else:               scores["WASH_TRADING"] += 3
        reasons.append(f"价格{'暴涨' if change_24h>0 else '暴跌'}（{change_24h:+.1f}%）")

    if turnover_ratio is not None:
        if turnover_ratio < 0.5:
            scores["ACCUMULATION"] += 2
        elif turnover_ratio > 4:
            scores["WASH_TRADING"] += 3; scores["DISTRIBUTION"] += 2

    if spread_pct is not None and spread_pct > 0.3:
        scores["WASH_TRADING"] += 2
        reasons.append(f"HL-Binance 价差偏大（{spread_pct:.3f}%）")

    return scores, reasons


def _pick_phase(scores: dict, funding: float) -> str:
    phase = max(scores, key=scores.get)
    max_s = scores[phase]
    tied  = [p for p, s in scores.items() if s == max_s]
    if len(tied) > 1:
        if funding > 0.0003:   phase = "DISTRIBUTION" if "DISTRIBUTION" in tied else tied[0]
        elif funding < -0.0003: phase = "PUMP_SETUP"   if "PUMP_SETUP"   in tied else tied[0]
        else:                   phase = "ACCUMULATION" if "ACCUMULATION" in tied else tied[0]
    return phase


# ── Skill ──────────────────────────────────────────────────────────────────────

class MMAnalysisSkill(BaseSkill):

    def run(self, action: str = "analyze", **kwargs) -> dict:
        dispatch = {
            "analyze": self._analyze,
            "cross":   self._cross_only,
            "scan":    self._scan_all,
        }
        fn = dispatch.get(action.lower())
        if not fn:
            return self.err(f"未知操作：{action}。可用：analyze / cross / scan")
        return fn(**kwargs)

    # ── 完整分析（HL + 跨所） ─────────────────────────────────────────────────

    def _analyze(self, symbol: str = "BTC", **_) -> dict:
        sym    = symbol.upper()
        market = self.load("hl_market.json")

        # ── HL 数据 ──────────────────────────────────────────────────────────
        hl_asset = None
        if market:
            hl_asset = next((a for a in market.get("assets", []) if a["symbol"] == sym), None)

        hl_funding   = hl_asset["funding_8h"]   if hl_asset else 0.0
        change_24h   = hl_asset["change_24h_pct"] if hl_asset else 0.0
        price        = hl_asset["mark_price"]    if hl_asset else 0.0
        hl_oi_usd    = (hl_asset["open_interest"] * price) if hl_asset else 0.0

        # ── 跨所数据（并行） ──────────────────────────────────────────────────
        cross = _fetch_all_exchanges(sym)
        cross_available = sum(1 for d in cross.values() if d)

        # ── HL 换手率（Binance 合约量 / HL OI）───────────────────────────────
        bn_vol = (cross.get("binance") or {}).get("volume_24h")
        turnover = (bn_vol / hl_oi_usd) if bn_vol and hl_oi_usd else None

        # ── HL 基础评分 ───────────────────────────────────────────────────────
        hl_scores, hl_reasons = _score_hl(hl_funding, change_24h, hl_oi_usd, turnover, None)

        # ── 跨所信号评分 ──────────────────────────────────────────────────────
        cross_scores, cross_reasons, cross_stats = _cross_signals(cross, hl_funding)

        # ── 合并得分（跨所权重 1.5x） ─────────────────────────────────────────
        merged = {}
        for p in hl_scores:
            merged[p] = hl_scores[p] + int(cross_scores[p] * 1.5)

        phase      = _pick_phase(merged, hl_funding)
        confidence = merged[phase] / max(sum(merged.values()), 1)
        conf_label = "高" if confidence >= 0.5 else "中" if confidence >= 0.3 else "低"
        emoji      = PHASE_EMOJIS[phase]

        # ── 格式化输出 ────────────────────────────────────────────────────────
        lines = [
            f"🕵️ *{sym} 做市商阶段分析*（跨所综合）\n",
            f"{emoji} 当前阶段：*{PHASE_LABELS[phase]}*（置信度：{conf_label} {confidence*100:.0f}%）\n",
        ]

        if cross_available >= 2:
            lines.append("*跨所核心信号：*")
            for r in cross_reasons:
                lines.append(f"  • {r}")

        lines.append("\n*HL 辅助信号：*")
        for r in hl_reasons:
            lines.append(f"  • {r}")

        lines.append("\n*各阶段综合得分：*")
        for p, s in sorted(merged.items(), key=lambda x: x[1], reverse=True):
            bar = "█" * min(s, 12) + "░" * max(12 - s, 0)
            lines.append(f"  {PHASE_EMOJIS[p]} {PHASE_LABELS[p]}：`{bar}` {s}分")

        lines.append(f"\n💡 *操作建议*\n{PHASE_TIPS[phase]}")

        # ── 数据快照 ──────────────────────────────────────────────────────────
        lines.append(f"\n📊 *数据快照*")
        if price:
            lines.append(f"价格：`${price:,.4f}` | 24h：`{change_24h:+.2f}%`")
        lines.append(f"HL 资金费率：`{hl_funding*100:+.4f}%/8h`")
        if cross_available:
            w_avg = cross_stats.get("weighted_avg_funding", 0)
            div   = cross_stats.get("funding_divergence", 0)
            lines.append(f"加权费率（全所）：`{w_avg*100:+.4f}%/8h`")
            lines.append(f"费率极差：`{div*100:.4f}%`（{'共识' if div < 0.0001 else '分歧' if div > 0.0005 else '轻微分歧'}）")
            tot_oi = cross_stats.get("total_oi_usd", 0)
            if tot_oi:
                lines.append(f"全市场总 OI：`${tot_oi/1e6:.0f}M`")

        # 各所费率横排
        lines.append("\n*各所费率：*")
        for ex, d in cross.items():
            if d and d.get("funding") is not None:
                lbl = _EXCHANGE_LABELS.get(ex, ex)
                f   = d["funding"]
                em  = "🔴" if abs(f) > 0.001 else "🟡" if abs(f) > 0.0005 else "🟢"
                lines.append(f"  {em} {lbl}：`{f*100:+.4f}%`  OI `${d['oi_usd']/1e6:.0f}M`")
        lines.append(f"  {'🔴' if abs(hl_funding)>0.001 else '🟡' if abs(hl_funding)>0.0005 else '🟢'} HL：`{hl_funding*100:+.4f}%`  OI `${hl_oi_usd/1e6:.0f}M`")

        if cross_available == 0:
            lines.append("\n⚠️ _跨所数据获取失败，仅基于 HL 数据分析，结论参考性有限_")

        return self.ok("\n".join(lines), data={
            "symbol":     sym,
            "phase":      phase,
            "phase_label": PHASE_LABELS[phase],
            "confidence": round(confidence, 3),
            "scores":     merged,
            "hl_funding": hl_funding,
            "cross_stats": cross_stats,
            "cross_data": {ex: d for ex, d in cross.items() if d},
        })

    # ── 纯跨所快速视图 ────────────────────────────────────────────────────────

    def _cross_only(self, symbol: str = "BTC", **_) -> dict:
        """只看跨所费率 + OI 分布，不做综合评分。速度更快。"""
        sym   = symbol.upper()
        cross = _fetch_all_exchanges(sym)

        market   = self.load("hl_market.json")
        hl_asset = None
        if market:
            hl_asset = next((a for a in market.get("assets", []) if a["symbol"] == sym), None)
        hl_funding = hl_asset["funding_8h"] if hl_asset else None
        hl_oi      = (hl_asset["open_interest"] * hl_asset["mark_price"]) if hl_asset else 0

        _, _, stats = _cross_signals(cross, hl_funding or 0)

        lines = [f"🌐 *{sym} 跨所做市商视图*\n"]

        all_fundings = {}
        all_ois      = {}
        for ex, d in cross.items():
            if d:
                lbl = _EXCHANGE_LABELS.get(ex, ex)
                all_fundings[lbl] = d.get("funding", 0)
                all_ois[lbl]      = d.get("oi_usd", 0)
        if hl_funding is not None:
            all_fundings["HL"] = hl_funding
            all_ois["HL"]      = hl_oi

        lines.append("*资金费率（多付空 📈 / 空付多 📉）：*")
        for lbl, f in sorted(all_fundings.items(), key=lambda x: abs(x[1]), reverse=True):
            em  = "🔴" if abs(f) > 0.001 else "🟡" if abs(f) > 0.0005 else "🟢"
            dir = "多付空 📈" if f > 0 else "空付多 📉"
            lines.append(f"  {em} {lbl}：`{f*100:+.4f}%/8h` ({dir})")

        total_oi = sum(all_ois.values())
        lines.append(f"\n*OI 分布*（总计 `${total_oi/1e6:.0f}M`）：")
        for lbl, oi in sorted(all_ois.items(), key=lambda x: x[1], reverse=True):
            share = oi / total_oi * 100 if total_oi else 0
            bar   = "█" * int(share / 5)
            lines.append(f"  {lbl}：`${oi/1e6:.0f}M` {bar} {share:.0f}%")

        div = stats.get("funding_divergence", 0)
        w_avg = stats.get("weighted_avg_funding", 0)
        consensus = "高度共识 ✅" if div < 0.0001 else "明显分歧 ⚡" if div > 0.0005 else "轻微分歧"
        lines.append(f"\n加权费率：`{w_avg*100:+.4f}%/8h`  |  费率极差：`{div*100:.4f}%`（{consensus}）")

        if div > 0.0005:
            lines.append(
                "⚡ _费率分歧大 = MM 正在跨所套利：_\n"
                "_  做空高费率所（收费）+ 做多低费率所（市场中性）_"
            )

        return self.ok("\n".join(lines), data={"symbol": sym, "cross_stats": stats,
                                               "fundings": all_fundings, "ois": all_ois})

    # ── 全市场扫描（保留 HL 快速版） ──────────────────────────────────────────

    def _scan_all(self, top: int = 10, **_) -> dict:
        market = self.load("hl_market.json")
        if not market:
            return self.err("市场数据未缓存")

        top_assets = sorted(
            market.get("assets", []),
            key=lambda x: abs(x["funding_8h"]),
            reverse=True,
        )[:top]

        from collections import Counter
        phase_results = []
        for a in top_assets:
            funding    = a["funding_8h"]
            change_24h = a["change_24h_pct"]
            oi_usd     = a["open_interest"] * a["mark_price"]
            hl_s, _    = _score_hl(funding, change_24h, oi_usd, None, None)
            phase      = _pick_phase(hl_s, funding)
            phase_results.append((a["symbol"], phase, funding))

        phase_counts = Counter(p for _, p, _ in phase_results)

        lines = [f"🕵️ *做市商阶段扫描 Top {top}*（HL 快速版，/mm 指定币种可跨所分析）\n"]
        for sym, phase, rate in phase_results:
            lines.append(
                f"{PHASE_EMOJIS[phase]} `{sym:6s}` {PHASE_LABELS[phase]}  "
                f"费率 `{rate*100:+.4f}%`"
            )
        lines.append("\n*阶段分布：*")
        for p, cnt in phase_counts.most_common():
            lines.append(f"  {PHASE_EMOJIS[p]} {PHASE_LABELS[p]}：{cnt} 个")
        lines.append("\n_提示：/mm BTC 可查跨所综合分析（含 Binance/OKX/Bybit）_")

        return self.ok("\n".join(lines), data={"phase_results": [
            {"symbol": s, "phase": p, "funding": r} for s, p, r in phase_results
        ]})
