"""
skills/ta_analysis — OHLCV 行情 + 技术指标分析
通过 ccxt 获取 K 线数据，计算 RSI / MA / Bollinger Bands / MACD。
支持任意 ccxt 交易所 + 时间周期，默认 Binance 永续合约。

用法（通过 bot.py /ta 命令调用）：
  action=analysis  — RSI + MA + BB 全分析（默认）
  action=signal    — 精简交易信号
  action=ohlcv     — 原始 K 线 + 基本统计
"""
from __future__ import annotations

import math
from skills.base import BaseSkill

# ── ccxt 工厂 ──────────────────────────────────────────────────────────────────

_PERP_TYPE = {
    "binance": "future",
    "okx":     "swap",
    "bybit":   "linear",
    "gateio":  "swap",
    "bitget":  "swap",
}

def _make_ex(exchange: str, market_type: str = "perp"):
    import ccxt
    name = exchange.lower()
    cls  = getattr(ccxt, name, None)
    if cls is None:
        raise ValueError(f"不支持的交易所：{exchange}")
    if market_type == "spot":
        opts = {"defaultType": "spot"}
    else:
        opts = {"defaultType": _PERP_TYPE.get(name, "future")}
    return cls({"options": opts, "timeout": 10000})


# ── 技术指标函数 ───────────────────────────────────────────────────────────────

def _sma(closes: list[float], period: int) -> float | None:
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def _ema(closes: list[float], period: int) -> float | None:
    """指数移动平均（EMA），用 SMA 种子 + Wilder 乘数。"""
    if len(closes) < period:
        return None
    k   = 2.0 / (period + 1)
    ema = sum(closes[:period]) / period
    for price in closes[period:]:
        ema = price * k + ema * (1 - k)
    return ema


def _rsi(closes: list[float], period: int = 14) -> float | None:
    """Wilder 平滑 RSI。"""
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    avg_gain = sum(max(d, 0) for d in deltas[:period]) / period
    avg_loss = sum(max(-d, 0) for d in deltas[:period]) / period
    for d in deltas[period:]:
        avg_gain = (avg_gain * (period - 1) + max(d, 0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-d, 0)) / period
    if avg_loss == 0:
        return 100.0
    return round(100 - 100 / (1 + avg_gain / avg_loss), 2)


def _bollinger(closes: list[float], period: int = 20, mult: float = 2.0) -> dict | None:
    """Bollinger Bands：中轨 SMA{period}，上/下轨 ± {mult}σ。"""
    if len(closes) < period:
        return None
    subset   = closes[-period:]
    mid      = sum(subset) / period
    variance = sum((x - mid) ** 2 for x in subset) / period
    std      = math.sqrt(variance)
    return {
        "upper":     round(mid + mult * std, 4),
        "middle":    round(mid, 4),
        "lower":     round(mid - mult * std, 4),
        "bandwidth": round(mult * 2 * std / mid * 100, 2),  # % of middle
        "std":       round(std, 4),
    }


def _macd(closes: list[float],
          fast: int = 12, slow: int = 26, signal: int = 9
          ) -> dict | None:
    """MACD：DIF = EMA{fast} - EMA{slow}，DEA = EMA{signal} of DIF，柱 = DIF - DEA。"""
    if len(closes) < slow + signal:
        return None
    # 生成 MACD 线序列
    dif_series = []
    for i in range(slow - 1, len(closes)):
        ema_fast = _ema(closes[: i + 1], fast)
        ema_slow = _ema(closes[: i + 1], slow)
        if ema_fast is not None and ema_slow is not None:
            dif_series.append(ema_fast - ema_slow)

    if len(dif_series) < signal:
        return None

    dea = _ema(dif_series, signal)
    if dea is None:
        return None

    dif  = dif_series[-1]
    hist = dif - dea
    return {
        "dif":  round(dif, 4),
        "dea":  round(dea, 4),
        "hist": round(hist, 4),
    }


# ── 信号解读辅助 ───────────────────────────────────────────────────────────────

def _rsi_signal(rsi: float) -> tuple[str, str]:
    """返回 (emoji, 描述)。"""
    if rsi >= 80:
        return "🔴", f"极度超买 ({rsi:.1f})"
    if rsi >= 70:
        return "🟠", f"超买 ({rsi:.1f})"
    if rsi <= 20:
        return "🟢", f"极度超卖 ({rsi:.1f})"
    if rsi <= 30:
        return "🟡", f"超卖 ({rsi:.1f})"
    return "⚪", f"中性 ({rsi:.1f})"


def _bb_position(price: float, bb: dict) -> tuple[str, str]:
    upper, lower, mid = bb["upper"], bb["lower"], bb["middle"]
    width = upper - lower
    if width == 0:
        return "⚪", "带宽为零"
    pct = (price - lower) / width * 100
    if price > upper:
        return "🔴", f"突破上轨 ({pct:.0f}%位)"
    if price < lower:
        return "🟢", f"跌破下轨 ({pct:.0f}%位)"
    if pct >= 70:
        return "🟠", f"靠近上轨 ({pct:.0f}%位)"
    if pct <= 30:
        return "🟡", f"靠近下轨 ({pct:.0f}%位)"
    return "⚪", f"带内中性 ({pct:.0f}%位)"


def _trend_signal(price: float, ema20: float | None, ema50: float | None,
                  ema200: float | None) -> str:
    parts = []
    if ema20 and price > ema20:
        parts.append("EMA20↑")
    elif ema20:
        parts.append("EMA20↓")
    if ema50 and price > ema50:
        parts.append("EMA50↑")
    elif ema50:
        parts.append("EMA50↓")
    if ema200 and price > ema200:
        parts.append("EMA200↑")
    elif ema200:
        parts.append("EMA200↓")
    bulls  = sum(1 for p in parts if "↑" in p)
    bears  = sum(1 for p in parts if "↓" in p)
    emoji  = "📈" if bulls > bears else "📉" if bears > bulls else "↔️"
    return f"{emoji} {' | '.join(parts)}" if parts else "数据不足"


class TAAnalysisSkill(BaseSkill):

    def run(self, action: str = "analysis", symbol: str = "BTC",
            exchange: str = "binance", timeframe: str = "1h",
            limit: int = 200, **kwargs) -> dict:
        symbol   = symbol.upper()
        action   = action.lower()
        dispatch = {
            "analysis": self._analysis,
            "signal":   self._signal,
            "ohlcv":    self._ohlcv,
        }
        fn = dispatch.get(action)
        if not fn:
            return self.err(f"未知操作：{action}。可用：analysis / signal / ohlcv")
        return fn(symbol=symbol, exchange=exchange, timeframe=timeframe,
                  limit=limit, **kwargs)

    # ── 获取 K 线 ─────────────────────────────────────────────────────────────

    def _fetch_ohlcv(self, symbol: str, exchange: str, timeframe: str,
                     limit: int) -> tuple[list, str | None]:
        """返回 (closes_list, error_msg)。"""
        try:
            ex    = _make_ex(exchange, market_type="perp")
            sym   = f"{symbol}/USDT:USDT"
            # 部分交易所需要先 load_markets
            candles = ex.fetch_ohlcv(sym, timeframe, limit=limit)
            if not candles:
                return [], f"无 {symbol} {timeframe} K 线数据"
            return candles, None
        except Exception as e:
            return [], f"获取 K 线失败：{e}"

    # ── 完整技术分析 ──────────────────────────────────────────────────────────

    def _analysis(self, symbol: str, exchange: str, timeframe: str,
                  limit: int, **_) -> dict:
        candles, err = self._fetch_ohlcv(symbol, exchange, timeframe, max(limit, 200))
        if err:
            return self.err(err)

        closes  = [c[4] for c in candles]   # index 4 = close
        highs   = [c[2] for c in candles]
        lows    = [c[3] for c in candles]
        volumes = [c[5] for c in candles]
        price   = closes[-1]
        ex_label = exchange.capitalize()

        # ── 指标计算 ────────────────────────────────────────────────────────
        rsi14  = _rsi(closes, 14)
        rsi7   = _rsi(closes, 7)
        sma20  = _sma(closes, 20)
        sma50  = _sma(closes, 50)
        ema20  = _ema(closes, 20)
        ema50  = _ema(closes, 50)
        ema200 = _ema(closes, 200)
        bb20   = _bollinger(closes, 20)
        macd_d = _macd(closes)

        # ── 24h 统计 ────────────────────────────────────────────────────────
        recent = candles[-24:] if len(candles) >= 24 else candles
        h24    = max(c[2] for c in recent)
        l24    = min(c[3] for c in recent)
        v24    = sum(c[5] for c in recent) * price  # approx USD volume
        chg24  = (price - recent[0][1]) / recent[0][1] * 100 if recent else 0

        # ── 格式化输出 ──────────────────────────────────────────────────────
        tf_label = {"1m": "1分", "5m": "5分", "15m": "15分",
                    "1h": "1小时", "4h": "4小时", "1d": "日"}.get(timeframe, timeframe)
        lines = [
            f"📐 *{symbol} 技术分析* [{ex_label} · {tf_label}K]\n",
            f"价格：`${price:,.4f}`  24h：`{chg24:+.2f}%`",
            f"24h 高/低：`${h24:,.4f}` / `${l24:,.4f}`",
            f"24h 成交量（估）：`${v24/1e6:.1f}M`\n",
        ]

        # RSI
        rsi_e, rsi_s = _rsi_signal(rsi14) if rsi14 else ("❓", "数据不足")
        lines.append(f"*RSI*")
        lines.append(f"  RSI(14)：{rsi_e} `{rsi14}`  |  RSI(7)：`{rsi7}`")
        lines.append(f"  信号：{rsi_s}\n")

        # 移动均线
        lines.append("*均线*")
        def _px(v): return f"`${v:,.4f}`" if v else "`—`"
        def _vs(v):
            if not v: return ""
            d = (price - v) / v * 100
            return f" ({'↑' if d >= 0 else '↓'}{abs(d):.2f}%)"
        lines.append(f"  SMA20：{_px(sma20)}{_vs(sma20)}  SMA50：{_px(sma50)}{_vs(sma50)}")
        lines.append(f"  EMA20：{_px(ema20)}{_vs(ema20)}  EMA50：{_px(ema50)}{_vs(ema50)}")
        lines.append(f"  EMA200：{_px(ema200)}{_vs(ema200)}")
        trend = _trend_signal(price, ema20, ema50, ema200)
        lines.append(f"  趋势：{trend}\n")

        # Bollinger Bands
        lines.append("*布林带* (20,2)")
        if bb20:
            bb_e, bb_s = _bb_position(price, bb20)
            lines.append(f"  上轨：`${bb20['upper']:,.4f}`")
            lines.append(f"  中轨：`${bb20['middle']:,.4f}`")
            lines.append(f"  下轨：`${bb20['lower']:,.4f}`")
            lines.append(f"  带宽：`{bb20['bandwidth']:.2f}%`  σ：`{bb20['std']:,.4f}`")
            lines.append(f"  信号：{bb_e} {bb_s}\n")
        else:
            lines.append("  数据不足\n")

        # MACD
        lines.append("*MACD* (12,26,9)")
        if macd_d:
            hist_e  = "🟢" if macd_d["hist"] > 0 else "🔴"
            cross_s = "金叉 📈" if macd_d["dif"] > macd_d["dea"] else "死叉 📉"
            lines.append(f"  DIF：`{macd_d['dif']:+.4f}`  DEA：`{macd_d['dea']:+.4f}`")
            lines.append(f"  柱线：{hist_e} `{macd_d['hist']:+.4f}` ({cross_s})\n")
        else:
            lines.append("  数据不足\n")

        # 综合评分
        score = self._score(rsi14, price, ema20, ema50, bb20, macd_d)
        lines.append(f"*综合偏向*：{score}")

        return self.ok("\n".join(lines), data={
            "symbol": symbol, "exchange": exchange, "timeframe": timeframe,
            "price": price, "rsi14": rsi14, "rsi7": rsi7,
            "sma20": sma20, "sma50": sma50, "ema20": ema20, "ema50": ema50, "ema200": ema200,
            "bb": bb20, "macd": macd_d,
        })

    # ── 精简信号 ──────────────────────────────────────────────────────────────

    def _signal(self, symbol: str, exchange: str, timeframe: str,
                limit: int, **_) -> dict:
        candles, err = self._fetch_ohlcv(symbol, exchange, timeframe, max(limit, 200))
        if err:
            return self.err(err)

        closes = [c[4] for c in candles]
        price  = closes[-1]

        rsi14  = _rsi(closes, 14)
        ema20  = _ema(closes, 20)
        ema50  = _ema(closes, 50)
        ema200 = _ema(closes, 200)
        bb20   = _bollinger(closes, 20)
        macd_d = _macd(closes)
        score  = self._score(rsi14, price, ema20, ema50, bb20, macd_d)

        rsi_e, rsi_s = _rsi_signal(rsi14) if rsi14 else ("❓", "—")
        bb_e, bb_s   = _bb_position(price, bb20) if bb20 else ("❓", "—")

        tf_label = {"1h": "1h", "4h": "4h", "1d": "日线"}.get(timeframe, timeframe)
        lines = [
            f"⚡ *{symbol} 快速信号* [{exchange.capitalize()} {tf_label}]",
            f"价格：`${price:,.4f}`",
            f"RSI：{rsi_e} {rsi_s}",
            f"BB ：{bb_e} {bb_s}",
            f"趋势：{_trend_signal(price, ema20, ema50, ema200)}",
        ]
        if macd_d:
            cross = "金叉 📈" if macd_d["dif"] > macd_d["dea"] else "死叉 📉"
            lines.append(f"MACD：{cross}  柱 `{macd_d['hist']:+.4f}`")
        lines.append(f"\n*综合偏向*：{score}")
        lines.append("⚠️ _仅供参考，不构成投资建议_")

        return self.ok("\n".join(lines), data={
            "price": price, "rsi14": rsi14, "bb": bb20,
            "ema20": ema20, "ema50": ema50, "macd": macd_d,
        })

    # ── 原始 K 线 ─────────────────────────────────────────────────────────────

    def _ohlcv(self, symbol: str, exchange: str, timeframe: str,
               limit: int, **_) -> dict:
        n       = min(limit, 20)  # 只展示最近 20 根
        candles, err = self._fetch_ohlcv(symbol, exchange, timeframe, max(limit, n))
        if err:
            return self.err(err)

        closes  = [c[4] for c in candles]
        recent  = candles[-n:]
        price   = closes[-1]
        h_all   = max(c[2] for c in candles)
        l_all   = min(c[3] for c in candles)
        avg_vol = sum(c[5] for c in candles) / len(candles)

        tf_label = {"1h": "1h", "4h": "4h", "1d": "日线"}.get(timeframe, timeframe)
        lines = [
            f"📊 *{symbol} K 线* [{exchange.capitalize()} {tf_label} · 最近{n}根]\n",
            f"当前价：`${price:,.4f}`",
            f"区间极值：高 `${h_all:,.4f}` / 低 `${l_all:,.4f}`",
            f"平均成交量：`{avg_vol:,.2f}`\n",
            "```",
            f"{'时间':<16} {'开':<12} {'高':<12} {'低':<12} {'收':<12} 涨跌",
        ]
        import datetime
        for c in recent:
            ts   = datetime.datetime.utcfromtimestamp(c[0] / 1000).strftime("%m-%d %H:%M")
            chg  = (c[4] - c[1]) / c[1] * 100
            sign = "+" if chg >= 0 else ""
            lines.append(
                f"{ts:<16} {c[1]:<12.2f} {c[2]:<12.2f} {c[3]:<12.2f} {c[4]:<12.2f} {sign}{chg:.2f}%"
            )
        lines.append("```")

        return self.ok("\n".join(lines), data={"candles": recent})

    # ── 综合偏向评分 ──────────────────────────────────────────────────────────

    @staticmethod
    def _score(rsi: float | None, price: float, ema20: float | None,
               ema50: float | None, bb: dict | None, macd_d: dict | None) -> str:
        bull = 0
        bear = 0

        if rsi is not None:
            if rsi < 30:  bull += 2
            elif rsi < 45: bull += 1
            elif rsi > 70: bear += 2
            elif rsi > 55: bear += 1

        if ema20 and price > ema20: bull += 1
        elif ema20: bear += 1

        if ema50 and price > ema50: bull += 1
        elif ema50: bear += 1

        if bb:
            upper, lower = bb["upper"], bb["lower"]
            if price < lower:   bull += 2
            elif price > upper: bear += 2

        if macd_d:
            if macd_d["hist"] > 0: bull += 1
            else:                  bear += 1

        total = bull + bear
        if total == 0:
            return "❓ 数据不足"
        bull_pct = bull / total * 100
        if bull_pct >= 70:
            return f"📈 看多  (多{bull} / 空{bear})"
        if bull_pct <= 30:
            return f"📉 看空  (多{bull} / 空{bear})"
        return f"↔️ 中性  (多{bull} / 空{bear})"
