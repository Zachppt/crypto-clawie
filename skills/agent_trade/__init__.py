"""
skills/agent_trade — 多因子智能交易代理
数据来源：Binance 永续合约公开 API（两次并行请求，无需 API Key）
评分因子：
  1. 资金费率幅度（权重 50%）
  2. 24h 成交量确认（权重 20%）— 高成交量信号更可靠
  3. 价格动量同向（权重 20%）
  4. OKX 跨所确认（权重 15%）— 与 Binance 方向一致则加分
"""
from __future__ import annotations

import json
import requests as _req
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from skills.base import BaseSkill

_BN_FAPI = "https://fapi.binance.com/fapi/v1"
_TIMEOUT  = 8


def _fetch_market_data() -> list[dict]:
    """
    从 Binance 永续合约批量获取市场数据（2次并行请求）。
    返回资产列表，每项含：symbol, mark_price, change_24h_pct,
                          funding_8h, funding_annualized, open_interest, _vol_usdt
    """
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_idx = pool.submit(_req.get, f"{_BN_FAPI}/premiumIndex", timeout=_TIMEOUT)
            f_tkr = pool.submit(_req.get, f"{_BN_FAPI}/ticker/24hr",  timeout=_TIMEOUT)

        idx_list = f_idx.result().json()   # [{symbol, lastFundingRate, markPrice, ...}]
        tkr_list = f_tkr.result().json()   # [{symbol, priceChangePercent, quoteVolume, ...}]

        tkr_map: dict[str, dict] = {
            t["symbol"]: t for t in tkr_list if isinstance(t, dict)
        }

        assets = []
        for item in idx_list:
            if not isinstance(item, dict):
                continue
            sym = item.get("symbol", "")
            if not sym.endswith("USDT"):
                continue
            base       = sym[:-4]
            mark_price = float(item.get("markPrice", 0) or 0)
            if mark_price == 0:
                continue
            funding  = float(item.get("lastFundingRate", 0) or 0)
            tkr      = tkr_map.get(sym, {})
            chg_24h  = float(tkr.get("priceChangePercent", 0) or 0)
            vol_usdt = float(tkr.get("quoteVolume", 0) or 0)

            assets.append({
                "symbol":             base,
                "mark_price":         mark_price,
                "change_24h_pct":     chg_24h,
                "funding_8h":         funding,
                "funding_annualized": funding * 3 * 365 * 100,
                "open_interest":      vol_usdt / mark_price,  # 成交量代理
                "_vol_usdt":          vol_usdt,
            })

        return assets
    except Exception:
        return []


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
        """扫描 Binance 永续合约所有标的，返回高置信度交易机会。"""
        assets = _fetch_market_data()
        if not assets:
            return self.err("无法获取市场数据（Binance API），请检查网络连接")

        opportunities = []
        for a in assets:
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
        user_strategy = self._load_user_strategy()
        result        = self._analyze()
        if not result.get("success"):
            return result

        opps = result.get("data", {}).get("opportunities", [])
        if not opps:
            return self.ok("🤖 当前无需操作", data={"decisions": []})

        # 已有持仓（HL 可选，不影响主流程）
        open_symbols: set[str] = set()
        account = self.load("hl_account.json")
        if account:
            for pos in account.get("positions", []):
                open_symbols.add(pos["symbol"])

        # 应用用户策略约束
        if user_strategy:
            target_token = user_strategy.get("token", "").upper()
            allowed_dir  = user_strategy.get("direction", "both").lower()
            size_usd     = float(user_strategy.get("size_usd", self.getenv("AUTO_TRADE_SIZE_USD", "50")))
            if target_token:
                opps = [o for o in opps if o["symbol"] == target_token]
            if allowed_dir != "both":
                opps = [o for o in opps if o["side"] == allowed_dir]
            for opp in opps:
                opp["suggested_size_usd"] = size_usd

        decisions = []
        max_new   = int(self.getenv("AUTO_TRADE_MAX_POSITIONS", "2"))
        for opp in opps:
            if len(decisions) >= max_new:
                break
            if opp["symbol"] in open_symbols:
                continue
            decisions.append({
                "action":       "open",
                "symbol":       opp["symbol"],
                "side":         opp["side"],
                "size_usd":     opp["suggested_size_usd"],
                "confidence":   opp["total_score"],
                "reasons":      opp["factors"],
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
        history   = self._load_history()
        recent    = history[-5:] if history else []
        agent_on  = self.getenv("AGENT_TRADE_ENABLED", "false").lower() == "true"
        autonomous = self.getenv("AUTONOMOUS_MODE", "false").lower() == "true"

        status_icon = "✅" if (agent_on and autonomous) else "⏸️"
        lines = [
            f"🤖 *Agent 交易状态*\n",
            f"{status_icon} Agent：{'运行中' if (agent_on and autonomous) else '已暂停'}",
            f"{'✅' if autonomous else '⚠️'} 自主模式：{'开启' if autonomous else '关闭'}",
            f"\n*评分权重*",
            f"• 资金费率幅度：最高 50%",
            f"• 24h 成交量确认：最高 20%",
            f"• 价格动量同向：最高 20%",
            f"• OKX 跨所确认：最高 15%",
            f"\n数据来源：Binance 永续合约公开 API（实时）",
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
            "/alerts — 立即扫描市场",
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
        symbol   = asset["symbol"]
        funding  = asset["funding_8h"]
        vol_usdt = asset.get("_vol_usdt", 0)
        chg_24h  = asset.get("change_24h_pct", 0)

        threshold = float(self.getenv("HL_FUNDING_ALERT_THRESHOLD", "0.0005"))
        if abs(funding) < threshold:
            return None

        score   = 0.0
        factors = []
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

        # ─ 因子2：24h 成交量确认（权重 20%）──────────────────────────────
        # 高成交量 = 市场关注度高，信号更可靠
        if vol_usdt > 1e9:
            score += 0.20
            factors.append(f"成交量高 ${vol_usdt/1e9:.1f}B")
        elif vol_usdt > 3e8:
            score += 0.12
            factors.append(f"成交量中 ${vol_usdt/1e9:.2f}B")
        elif vol_usdt > 5e7:
            score += 0.06
            factors.append(f"成交量 ${vol_usdt/1e6:.0f}M")

        # ─ 因子3：价格动量同向（权重 20%）────────────────────────────────
        if (funding > 0 and chg_24h > 8) or (funding < 0 and chg_24h < -8):
            score += 0.20
            factors.append(f"动量强烈同向 {chg_24h:+.1f}%")
        elif (funding > 0 and chg_24h > 3) or (funding < 0 and chg_24h < -3):
            score += 0.12
            factors.append(f"动量同向 {chg_24h:+.1f}%")
        elif (funding > 0 and chg_24h > 1) or (funding < 0 and chg_24h < -1):
            score += 0.05
            factors.append(f"动量轻微同向 {chg_24h:+.1f}%")

        # ─ 因子4：OKX 跨所费率确认（权重 15%）───────────────────────────
        # 仅对评分已达基准的资产执行（避免对所有资产都发 HTTP 请求）
        if score >= 0.25:
            try:
                r = _req.get(
                    "https://www.okx.com/api/v5/public/funding-rate",
                    params={"instId": f"{symbol}-USDT-SWAP"},
                    timeout=4,
                )
                d = r.json().get("data", [{}])[0]
                okx_rate = float(d.get("fundingRate", 0) or 0)
                if (funding > 0 and okx_rate > 0.0003) or (funding < 0 and okx_rate < -0.0003):
                    score += 0.15
                    factors.append(f"OKX同向 {okx_rate*100:+.4f}%")
                elif (funding > 0 and okx_rate > 0) or (funding < 0 and okx_rate < 0):
                    score += 0.07
                    factors.append(f"OKX同向（弱）{okx_rate*100:+.4f}%")
            except Exception:
                pass

        score = min(round(score, 3), 1.0)

        base_size = float(self.getenv("AUTO_TRADE_SIZE_USD", "50"))
        max_pos   = float(self.getenv("MAX_POSITION_SIZE_USD", "500"))
        suggested = min(base_size * (0.8 + score), max_pos * 0.3)

        return {
            "symbol":             symbol,
            "side":               side,
            "total_score":        score,
            "factors":            factors,
            "funding_rate":       funding,
            "vol_usdt":           vol_usdt,
            "change_24h":         chg_24h,
            "suggested_size_usd": round(suggested, 1),
        }

    # ── 读取用户自定义策略 ────────────────────────────────────────────────────

    def _load_user_strategy(self) -> dict | None:
        path = self.memory_dir / "my_strategy.json"
        if not path.exists():
            return None
        try:
            s = json.load(open(path))
            return s if s.get("enabled") else None
        except Exception:
            return None

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
