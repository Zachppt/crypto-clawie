"""
skills/ai_agent — 数据上下文整理器
将实时数据格式化成可读的上下文块，发到 Telegram 群里。
推理和分析由群组内的 AI Agent（OpenClaw @DigentZach）完成，本模块不调用任何 LLM。

actions:
  ask       — 整理市场数据 + 用户问题，供 AI Agent 分析
  deep      — 指定币种深度数据（MM 阶段 + 跨所费率）
  deep_dive — deep 的别名（/track report 使用）
  advice    — 整理持仓数据，供 AI Agent 给出操作建议
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from skills.base import BaseSkill


class ContextBuilder:
    """从各数据源取数，打包成 AI Agent 可读的文本上下文。"""

    def __init__(self, data_dir: Path, memory_dir: Path, env: dict):
        self.data_dir   = data_dir
        self.memory_dir = memory_dir
        self.env        = env

    def _load(self, filename: str):
        path = self.data_dir / filename
        if not path.exists():
            return None
        try:
            raw = json.load(open(path))
            return raw.get("data") if isinstance(raw, dict) and "data" in raw else raw
        except Exception:
            return None

    def market_context(self, symbol: str = None) -> str:
        """组装市场数据上下文（文本格式，供 AI Agent 阅读）。"""
        parts = []
        now   = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        parts.append(f"数据采集时间：{now}")

        # ── 主流币行情（Binance 缓存或 WS 实时）──────────────────────────────
        snap = self._load("market_snapshot.json")
        if snap:
            prices = snap.get("prices", {})
            price_lines = []
            for sym in ["BTC", "ETH", "SOL", "BNB"]:
                if sym in prices:
                    p = prices[sym]
                    price_lines.append(
                        f"  {sym}: ${p['price']:,.2f} ({p['change_24h']:+.2f}%)"
                        + (f" 成交量 ${p.get('volume_usdt', 0)/1e9:.1f}B" if p.get("volume_usdt") else "")
                    )
            if price_lines:
                parts.append(f"\n【主流币行情（Binance）】\n" + "\n".join(price_lines))
            fng = snap.get("fear_greed", {})
            if fng:
                parts.append(f"\n【市场情绪】恐慌贪婪指数：{fng['value']} ({fng['label']})")

        # ── WS 实时价格（若更新）──────────────────────────────────────────────
        ws = self._load("ws_prices.json")
        if ws:
            prices_map = ws if isinstance(ws, dict) else ws.get("prices", {})
            ws_lines   = []
            watch_syms = [symbol.upper()] if symbol else ["BTC", "ETH", "SOL"]
            for sym in watch_syms:
                wp = prices_map.get(sym)
                if wp and wp.get("price"):
                    ws_lines.append(
                        f"  {sym}: ${float(wp['price']):,.2f} "
                        f"({float(wp.get('change_24h') or 0):+.2f}%) ⚡ WebSocket"
                    )
            if ws_lines:
                parts.append(f"\n【实时价格（WS）】\n" + "\n".join(ws_lines))

        # ── 指定币种 HL 补充数据（若缓存存在）───────────────────────────────
        if symbol:
            market = self._load("hl_market.json")
            if market:
                asset = next(
                    (a for a in market.get("assets", []) if a["symbol"] == symbol.upper()),
                    None
                )
                if asset:
                    parts.append(
                        f"\n【{symbol.upper()} Hyperliquid 数据】\n"
                        f"标记价格：${asset['mark_price']:,.4f}\n"
                        f"24h 涨跌：{asset['change_24h_pct']:+.2f}%\n"
                        f"资金费率(8h)：{asset['funding_8h']*100:+.5f}%\n"
                        f"年化资金费率：{asset['funding_annualized']:+.1f}%\n"
                        f"未平仓量：${asset['open_interest'] * asset['mark_price'] / 1e6:.1f}M"
                    )

        # ── 账户持仓（HL，若已配置）──────────────────────────────────────────
        account = self._load("hl_account.json")
        if account and account.get("positions"):
            positions = account["positions"]
            pos_lines = []
            for p in positions:
                pos_lines.append(
                    f"  {p['symbol']} {p['side']} ×{p['leverage']}x "
                    f"数量{p['size']} 入场${p['entry_price']:,.2f} "
                    f"未实现盈亏${p['unrealized_pnl']:+.2f} "
                    f"距爆仓{p['dist_to_liq_pct']:.1f}%"
                )
            parts.append(
                f"\n【当前持仓（Hyperliquid）】"
                f"账户净值：${account.get('account_value_usdc', 0):,.2f} USDC\n"
                + "\n".join(pos_lines)
            )
        elif account:
            parts.append(
                f"\n【当前账户（Hyperliquid）】"
                f"净值：${account.get('account_value_usdc', 0):,.2f} USDC，无持仓"
            )

        # ── 新闻（最近 5 条）────────────────────────────────────────────────
        news = self._load("news_cache.json")
        if news:
            items = news[:5] if isinstance(news, list) else []
            if items:
                news_lines = [f"  • {item.get('title', '')[:80]}" for item in items]
                parts.append(f"\n【最新快讯（前5条）】\n" + "\n".join(news_lines))

        # ── 用户策略（若已配置）──────────────────────────────────────────────
        strat_path = self.memory_dir / "my_strategy.json"
        if strat_path.exists():
            try:
                strat = json.load(open(strat_path))
                if strat.get("enabled"):
                    parts.append(
                        f"\n【用户策略配置】\n"
                        f"  目标标的：{strat.get('token', '—')} | 方向：{strat.get('direction', '—')}\n"
                        f"  每笔仓位：${float(strat.get('size_usd', 0)):.0f} | "
                        f"止损：{strat.get('stop_pct', '—')}% | 止盈：{strat.get('profit_pct', '—')}%"
                    )
            except Exception:
                pass

        return "\n".join(parts)

    def focus_context(self) -> str | None:
        path = self.memory_dir / "focus.json"
        if not path.exists():
            return None
        try:
            f = json.load(open(path))
            return f"当前专项追踪：{f.get('token', '?')}，每 {f.get('interval_min', 15)} 分钟报告"
        except Exception:
            return None


class AIAgentSkill(BaseSkill):

    def run(self, action: str = "ask", **kwargs) -> dict:
        dispatch = {
            "ask":       self._ask,
            "deep":      self._deep_dive,
            "deep_dive": self._deep_dive,   # /track report 使用的别名
            "advice":    self._advice,
        }
        fn = dispatch.get(action.lower())
        if not fn:
            return self.err(f"未知操作：{action}。可用：ask / deep / advice")
        return fn(**kwargs)

    # ── /ask — 整理市场数据 + 附上用户问题 ───────────────────────────────────

    def _ask(self, question: str = "", symbol: str = None, **_) -> dict:
        if not question:
            return self.err("请提供问题，例如：`/ask 现在 SOL 适合做多吗？`")

        ctx     = ContextBuilder(self.data_dir, self.memory_dir, self.env)
        context = ctx.market_context(symbol=symbol)
        focus   = ctx.focus_context()
        if focus:
            context += f"\n\n{focus}"

        text = (
            f"📊 *市场数据上下文*\n\n"
            f"{context}\n\n"
            f"{'─' * 20}\n\n"
            f"❓ *问题：* {question}"
        )
        return self.ok(text, data={"question": question})

    # ── /advice — 整理持仓 + 市场数据，供 AI Agent 给操作建议 ──────────────

    def _advice(self, **_) -> dict:
        ctx     = ContextBuilder(self.data_dir, self.memory_dir, self.env)
        context = ctx.market_context()

        # 检查是否有持仓
        account  = ctx._load("hl_account.json")
        has_pos  = bool(account and account.get("positions"))
        no_pos_note = (
            "\n\n_当前账户无持仓。可用 `/deep BTC` 整理市场数据，"
            "再 @AI Agent 分析入场时机。_"
            if not has_pos else ""
        )

        text = (
            f"📊 *持仓管理上下文*\n\n"
            f"{context}"
            f"{no_pos_note}\n\n"
            f"{'─' * 20}\n\n"
            f"_数据已整理完毕，请 @AI Agent 给出操作建议_"
        )
        return self.ok(text)

    # ── /deep — 指定币种深度数据整理 ─────────────────────────────────────────

    def _deep_dive(self, symbol: str = "BTC", **_) -> dict:
        sym   = symbol.upper()
        parts = []

        ctx = ContextBuilder(self.data_dir, self.memory_dir, self.env)
        parts.append(ctx.market_context(symbol=sym))

        # ── MM 阶段分析 ───────────────────────────────────────────────────────
        try:
            from skills.mm_analysis import MMAnalysisSkill
            mm = MMAnalysisSkill(self.data_dir, self.memory_dir, self.env)
            mr = mm.run(action="analyze", symbol=sym)
            if mr.get("success") and mr.get("data"):
                d = mr["data"]
                parts.append(
                    f"\n【{sym} 做市商阶段】\n"
                    f"  阶段：{d.get('phase_label', '未知')}（置信度 {d.get('confidence', 0)*100:.0f}%）\n"
                    f"  信号：{'; '.join(d.get('reasons', []))}"
                )
        except Exception as e:
            parts.append(f"\n【MM 分析】获取失败：{e}")

        # ── 跨所资金费率（使用 exchange_agg，无需 HL 缓存）───────────────────
        try:
            from skills.exchange_agg import ExchangeAggSkill
            agg      = ExchangeAggSkill(self.data_dir, self.memory_dir, self.env)
            fund_r   = agg.run(action="funding", symbol=sym)
            if fund_r.get("success") and fund_r.get("data", {}).get("rates"):
                rates    = fund_r["data"]["rates"]
                rate_str = " | ".join(
                    f"{ex}: {r*100:+.4f}%/8h" for ex, r in rates.items()
                )
                parts.append(f"\n【跨所资金费率（实时）】\n  {rate_str}")
        except Exception as e:
            parts.append(f"\n【跨所费率】获取失败：{e}")

        # ── 技术分析摘要 ──────────────────────────────────────────────────────
        try:
            from skills.ta_analysis import TAAnalysisSkill
            ta   = TAAnalysisSkill(self.data_dir, self.memory_dir, self.env)
            ta_r = ta.run(action="signal", symbol=sym, timeframe="1h")
            if ta_r.get("success"):
                parts.append(f"\n【{sym} 技术信号（1h）】\n  {ta_r['text']}")
        except Exception as e:
            parts.append(f"\n【技术分析】获取失败：{e}")

        context = "\n".join(parts)
        text = (
            f"🔬 *{sym} 深度数据上下文*\n\n"
            f"{context}\n\n"
            f"{'─' * 20}\n\n"
            f"_数据已整理完毕，@AI Agent 可直接分析_"
        )
        return self.ok(text, data={"symbol": sym})
