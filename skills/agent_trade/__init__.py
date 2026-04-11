"""
skills/agent_trade — 多因子智能交易代理
相比简单阈值触发，agent 具备：
  1. 多因子评分加权（资金费率 + OI + 价格动量 + 跨所费率确认）
  2. 动态仓位大小（置信度高 → 仓位更大）
  3. 决策日志（可追溯每次开平仓原因）
  4. 持仓退出多条件判断
"""
import json
import requests
from datetime import datetime, timezone
from skills.base import BaseSkill

T = 5  # timeout seconds


def _get(url: str) -> dict | None:
    try:
        r = requests.get(url, timeout=T, headers={"User-Agent": "crypto-clawie/2.0"})
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


class AgentTradeSkill(BaseSkill):

    def run(self, action: str = "analyze", **kwargs) -> dict:
        dispatch = {
            "analyze": self._analyze,
            "decide":  self._decide,
            "status":  self._status,
            "history": self._decision_history,
        }
        fn = dispatch.get(action.lower())
        if not fn:
            return self.err(f"未知操作：{action}。可用：analyze / decide / status / history")
        return fn(**kwargs)

    # ── 全市场分析 ────────────────────────────────────────────────────────────

    def _analyze(self, min_score: float = 0.6, **_) -> dict:
        """扫描所有资产，返回高置信度交易机会。"""
        market = self.load("hl_market.json")
        if not market:
            return self.err("市场数据未缓存，请等待 fetcher 运行")

        opportunities = []
        for a in market.get("assets", []):
            opp = self._score_asset(a)
            if opp and opp["total_score"] >= min_score:
                opportunities.append(opp)

        opportunities.sort(key=lambda x: x["total_score"], reverse=True)

        if not opportunities:
            return self.ok(
                f"🤖 当前无高置信度机会（阈值 {min_score*100:.0f}%）\n"
                f"资金费率异动不足或多因子未能汇聚"
            )

        lines = [f"🤖 *Agent 市场分析*\n找到 {min(len(opportunities), 5)} 个机会\n"]
        for opp in opportunities[:5]:
            filled = round(opp["total_score"] * 10)
            bar    = "█" * filled + "░" * (10 - filled)
            emoji  = "📈" if opp["side"] == "long" else "📉"
            lines.append(
                f"{emoji} *{opp['symbol']}* {opp['side'].upper()}\n"
                f"  置信度：`{bar} {opp['total_score']*100:.0f}%`\n"
                f"  因子：{' | '.join(opp['factors'])}\n"
                f"  建议仓位：`${opp['suggested_size_usd']:.0f}` USDC"
            )

        return self.ok("\n".join(lines), data={"opportunities": opportunities})

    # ── 生成交易决策 ──────────────────────────────────────────────────────────

    def _decide(self, **_) -> dict:
        """生成可执行的交易决策（排除已有持仓的标的）。"""
        result = self._analyze()
        if not result.get("success"):
            return result

        opps = result.get("data", {}).get("opportunities", [])
        if not opps:
            return self.ok("🤖 当前无需操作", data={"decisions": []})

        account      = self.load("hl_account.json")
        open_symbols = set()
        if account:
            for pos in account.get("positions", []):
                open_symbols.add(pos["symbol"])

        decisions = []
        max_new   = int(self.getenv("AUTO_TRADE_MAX_POSITIONS", "2"))
        for opp in opps:
            if len(decisions) >= max_new:
                break
            if opp["symbol"] in open_symbols:
                continue
            decisions.append({
                "action":      "open",
                "symbol":      opp["symbol"],
                "side":        opp["side"],
                "size_usd":    opp["suggested_size_usd"],
                "confidence":  opp["total_score"],
                "reasons":     opp["factors"],
                "funding_rate": opp["funding_rate"],
            })

        self._log_decision(decisions)

        if not decisions:
            return self.ok("🤖 无新开仓机会（已有持仓或未达阈值）", data={"decisions": []})

        lines = [f"🤖 *Agent 决策：{len(decisions)} 笔*\n"]
        for d in decisions:
            emoji = "📈" if d["side"] == "long" else "📉"
            lines.append(
                f"{emoji} `{d['symbol']}` {d['side']} `${d['size_usd']:.0f}`\n"
                f"  置信度 {d['confidence']*100:.0f}% | {' + '.join(d['reasons'][:2])}"
            )
        return self.ok("\n".join(lines), data={"decisions": decisions})

    # ── 代理状态 ──────────────────────────────────────────────────────────────

    def _status(self, **_) -> dict:
        history = self._load_history()
        recent  = history[-5:] if history else []

        agent_enabled = self.getenv("AGENT_TRADE_ENABLED", "false").lower() == "true"
        autonomous    = self.getenv("AUTONOMOUS_MODE", "false").lower() == "true"

        status_icon = "✅" if (agent_enabled and autonomous) else "⏸️"
        lines = [
            f"🤖 *Agent 交易状态*\n",
            f"{status_icon} Agent：{'运行中' if (agent_enabled and autonomous) else '已暂停'}",
            f"{'✅' if autonomous else '⚠️'} 自主模式：{'开启' if autonomous else '关闭'}",
            f"\n*评分权重*",
            f"• 资金费率幅度：最高 50%",
            f"• OI 规模确认：最高 20%",
            f"• 价格动量同向：最高 20%",
            f"• 跨所费率确认（Binance）：最高 15%",
            f"\n*近期决策*",
        ]

        if recent:
            for d in reversed(recent):
                ts    = d.get("timestamp", "")[:16]
                count = len(d.get("decisions", []))
                syms  = ", ".join(dec["symbol"] for dec in d.get("decisions", []))
                lines.append(f"• {ts} — {count} 笔 {syms}")
        else:
            lines.append("暂无决策记录")

        lines.extend([
            "\n*命令*",
            "/agent scan — 立即分析市场",
            "/agent history — 历史决策记录",
        ])
        return self.ok("\n".join(lines), data={"history": recent})

    def _decision_history(self, **_) -> dict:
        history = self._load_history()
        if not history:
            return self.ok("暂无决策历史")
        lines = ["📋 *Agent 决策历史（最近 10 条）*\n"]
        for d in reversed(history[-10:]):
            ts = d.get("timestamp", "")[:16]
            for dec in d.get("decisions", []):
                emoji = "📈" if dec["side"] == "long" else "📉"
                lines.append(
                    f"{emoji} {ts} `{dec['symbol']}` {dec['side']} "
                    f"`${dec.get('size_usd', 0):.0f}` ({dec.get('confidence', 0)*100:.0f}%)\n"
                    f"  {' | '.join(dec.get('reasons', []))}"
                )
        return self.ok("\n".join(lines))

    # ── 内部：资产多因子评分 ──────────────────────────────────────────────────

    def _score_asset(self, asset: dict) -> dict | None:
        symbol  = asset["symbol"]
        funding = asset["funding_8h"]
        oi      = asset.get("open_interest", 0)
        price   = asset.get("mark_price", 1) or 1
        chg_24h = asset.get("change_24h_pct", 0)

        threshold = float(self.getenv("HL_FUNDING_ALERT_THRESHOLD", "0.0005"))
        if abs(funding) < threshold:
            return None  # 费率不够，不分析

        score   = 0.0
        factors = []
        # 资金费率为正 → 做空（收取多头支付的费用）
        side    = "short" if funding > 0 else "long"

        # ─ 因子1：资金费率幅度（权重 50%）─────────────────────────────────
        if abs(funding) >= 0.002:
            score += 0.50
            ann = abs(funding) * 3 * 365 * 100
            factors.append(f"费率极端 {funding*100:+.4f}% (年化{ann:.0f}%)")
        elif abs(funding) >= 0.001:
            score += 0.35
            ann = abs(funding) * 3 * 365 * 100
            factors.append(f"费率很高 {funding*100:+.4f}% (年化{ann:.0f}%)")
        elif abs(funding) >= 0.0005:
            score += 0.20
            factors.append(f"费率偏高 {funding*100:+.4f}%")

        # ─ 因子2：OI 规模（权重 20%）──────────────────────────────────────
        oi_usd = oi * price
        if oi_usd > 1e8:
            score += 0.20
            factors.append(f"OI ${oi_usd/1e6:.0f}M 极大")
        elif oi_usd > 5e7:
            score += 0.12
            factors.append(f"OI ${oi_usd/1e6:.0f}M 较大")
        elif oi_usd > 1e7:
            score += 0.06
            factors.append(f"OI ${oi_usd/1e6:.0f}M 一般")

        # ─ 因子3：价格动量同向（权重 20%）────────────────────────────────
        # 资金费率正（多拥挤）且价格仍在涨 → 做空信号更强
        if (funding > 0 and chg_24h > 8) or (funding < 0 and chg_24h < -8):
            score += 0.20
            factors.append(f"动量强烈同向 {chg_24h:+.1f}%")
        elif (funding > 0 and chg_24h > 3) or (funding < 0 and chg_24h < -3):
            score += 0.12
            factors.append(f"动量同向 {chg_24h:+.1f}%")
        elif (funding > 0 and chg_24h > 1) or (funding < 0 and chg_24h < -1):
            score += 0.05
            factors.append(f"动量轻微同向 {chg_24h:+.1f}%")

        # ─ 因子4：Binance 跨所费率确认（权重 15%）──────────────────────
        try:
            d = _get(f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}USDT")
            if d and isinstance(d, dict) and "lastFundingRate" in d:
                bn_rate = float(d["lastFundingRate"])
                # 同向确认
                if (funding > 0 and bn_rate > 0.0003) or (funding < 0 and bn_rate < -0.0003):
                    score += 0.15
                    factors.append(f"Binance同向 {bn_rate*100:+.4f}%")
                elif (funding > 0 and bn_rate > 0) or (funding < 0 and bn_rate < 0):
                    score += 0.07
                    factors.append(f"Binance同向（弱）{bn_rate*100:+.4f}%")
        except Exception:
            pass

        score = min(round(score, 3), 1.0)

        # 动态仓位：置信度越高，建议仓位越大（基础 * 1~2 倍，不超过上限30%）
        base_size = float(self.getenv("AUTO_TRADE_SIZE_USD", "50"))
        max_pos   = float(self.getenv("MAX_POSITION_SIZE_USD", "500"))
        suggested = min(base_size * (0.8 + score), max_pos * 0.3)

        return {
            "symbol":            symbol,
            "side":              side,
            "total_score":       score,
            "factors":           factors,
            "funding_rate":      funding,
            "oi_usd":            oi_usd,
            "change_24h":        chg_24h,
            "suggested_size_usd": round(suggested, 1),
        }

    # ── 决策日志 ──────────────────────────────────────────────────────────────

    def _load_history(self) -> list:
        path = self.memory_dir / "agent_decisions.json"
        if not path.exists():
            return []
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            return []

    def _log_decision(self, decisions: list):
        if not decisions:
            return
        history = self._load_history()
        history.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "decisions": decisions,
        })
        if len(history) > 200:
            history = history[-200:]
        path = self.memory_dir / "agent_decisions.json"
        path.parent.mkdir(exist_ok=True)
        with open(path, "w") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
