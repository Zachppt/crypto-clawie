"""
skills/funding_arb — 资金费率套利策略管理
策略逻辑：
  当 HL 资金费率(8h) ≥ 入场阈值时：
    - 做空 HL perp（收取资金费）
    - 买入等量 Binance 现货（对冲 delta）
  当费率回落至退出阈值时平掉双边仓位。

  入场阈值（默认）：0.05%/8h = 年化 ~54.75%
  退出阈值（默认）：0.01%/8h
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from skills.base import BaseSkill


class FundingArbSkill(BaseSkill):

    ENTRY_THRESHOLD = 0.0005   # 0.05%/8h
    EXIT_THRESHOLD  = 0.0001   # 0.01%/8h

    def run(self, action: str = "scan", **kwargs) -> dict:
        dispatch = {
            "scan":   self._scan_opportunities,
            "status": self._arb_status,
            "open":   self._open_arb,
            "close":  self._close_arb,
            "pnl":    self._arb_pnl,
        }
        fn = dispatch.get(action.lower())
        if not fn:
            return self.err(f"未知操作：{action}。可用：scan / status / open / close / pnl")
        return fn(**kwargs)

    # ── 扫描套利机会 ──────────────────────────────────────────────────────────

    def _scan_opportunities(self, min_rate: float = None, **_) -> dict:
        market = self.load("hl_market.json")
        if not market:
            return self.err("市场数据未就绪，请检查 fetcher 是否运行")

        threshold = min_rate or self.ENTRY_THRESHOLD
        opps = []

        for a in market.get("assets", []):
            rate     = a["funding_8h"]
            abs_rate = abs(rate)
            if abs_rate < threshold:
                continue

            ann    = abs_rate * 3 * 365 * 100
            price  = a.get("mark_price", 0)
            oi_usd = a.get("open_interest", 0) * price

            opps.append({
                "symbol":    a["symbol"],
                "rate_8h":   rate,
                "ann_yield": round(ann, 1),
                "oi_usd_m":  round(oi_usd / 1e6, 1),
                "price":     price,
                "side":      "short" if rate > 0 else "long",
                "direction": "做空HL + 买现货" if rate > 0 else "做多HL + 卖现货",
            })

        opps.sort(key=lambda x: abs(x["rate_8h"]), reverse=True)
        opps = opps[:10]

        if not opps:
            return self.ok(f"✅ 当前无套利机会（阈值：{threshold*100:.3f}%/8h）")

        lines = [f"💰 *资金费率套利机会* — {len(opps)} 个\n"]
        for o in opps:
            emoji = "🔴" if abs(o["rate_8h"]) >= 0.001 else "🟡"
            lines.append(
                f"{emoji} `{o['symbol']}` {o['rate_8h']*100:+.4f}%/8h | 年化 ~{o['ann_yield']:.0f}%\n"
                f"  策略：{o['direction']} | OI ${o['oi_usd_m']:.0f}M"
            )

        lines.append("\n💡 _记录套利仓位：/arb open <币种> <金额USD>_")
        lines.append("_需同步在 Binance 建立对冲现货仓位_")

        return self.ok("\n".join(lines), data={"opportunities": opps})

    # ── 查看套利状态 ──────────────────────────────────────────────────────────

    def _arb_status(self, **_) -> dict:
        positions = self._load_arb_positions()
        if not positions:
            return self.ok("📋 当前无活跃套利仓位\n\n发送 /arb scan 查看机会")

        market   = self.load("hl_market.json")
        assets   = {a["symbol"]: a for a in market.get("assets", [])} if market else {}
        now_ts   = datetime.now(timezone.utc).timestamp()
        total    = 0.0
        lines    = [f"📋 *套利仓位状态* — {len(positions)} 个\n"]

        for sym, pos in positions.items():
            current      = assets.get(sym, {})
            cur_rate     = current.get("funding_8h", 0)
            entry_rate   = pos.get("entry_funding", 0)
            hours_held   = (now_ts - pos.get("opened_at", now_ts)) / 3600
            est_income   = abs(entry_rate) * pos.get("size_usd", 0) * (hours_held / 8)
            total       += est_income
            exit_signal  = "🔴 建议平仓" if abs(cur_rate) <= self.EXIT_THRESHOLD else "🟢 持有中"

            lines.append(
                f"• `{sym}` {pos.get('side', 'short')} | ${pos.get('size_usd', 0):.0f}\n"
                f"  入场 {entry_rate*100:+.4f}% → 当前 {cur_rate*100:+.4f}%\n"
                f"  持有 {hours_held:.0f}h | 预估收益 +${est_income:.2f}\n"
                f"  {exit_signal}"
            )

        lines.append(f"\n💰 总预估资金费收益：`+${total:.2f}`")
        return self.ok("\n".join(lines), data={"positions": positions, "estimated_pnl": total})

    # ── 开启套利 ──────────────────────────────────────────────────────────────

    def _open_arb(self, symbol: str = None, size_usd: float = 100, **_) -> dict:
        if not symbol:
            return self.err("请指定币种，例如：/arb open BTC 500")

        market = self.load("hl_market.json")
        if not market:
            return self.err("市场数据未就绪")

        assets = {a["symbol"]: a for a in market.get("assets", [])}
        sym    = symbol.upper()
        asset  = assets.get(sym)
        if not asset:
            return self.err(f"未找到 {sym} 市场数据")

        rate = asset["funding_8h"]
        if abs(rate) < self.ENTRY_THRESHOLD:
            return self.err(
                f"⚠️ {sym} 费率 {rate*100:+.4f}%/8h 未达入场阈值 "
                f"{self.ENTRY_THRESHOLD*100:.3f}%/8h，不建议套利"
            )

        side = "short" if rate > 0 else "long"
        ann  = abs(rate) * 3 * 365 * 100

        positions       = self._load_arb_positions()
        positions[sym]  = {
            "symbol":        sym,
            "side":          side,
            "size_usd":      size_usd,
            "entry_funding": rate,
            "entry_price":   asset["mark_price"],
            "opened_at":     datetime.now(timezone.utc).timestamp(),
            "opened_at_str": datetime.now(timezone.utc).isoformat(),
        }
        self._save_arb_positions(positions)

        return self.ok(
            f"✅ *套利仓位已记录* — `{sym}`\n\n"
            f"• HL 方向：{'做空 perp 📉' if side == 'short' else '做多 perp 📈'}\n"
            f"• 金额：`${size_usd}` USDC\n"
            f"• 入场费率：`{rate*100:+.4f}%/8h`（年化 ~{ann:.0f}%）\n"
            f"• 入场价格：`${asset['mark_price']:,.2f}`\n\n"
            f"⚡ 请同步在 Binance {'买入' if side == 'short' else '卖出'}等量 {sym} 现货\n"
            f"退出信号：费率 ≤ {self.EXIT_THRESHOLD*100:.3f}%/8h 时执行 /arb close {sym}"
        )

    # ── 关闭套利 ──────────────────────────────────────────────────────────────

    def _close_arb(self, symbol: str = None, **_) -> dict:
        if not symbol:
            return self.err("请指定币种，例如：/arb close BTC")

        positions = self._load_arb_positions()
        sym       = symbol.upper()
        if sym not in positions:
            return self.err(f"未找到 {sym} 套利仓位记录")

        pos       = positions.pop(sym)
        self._save_arb_positions(positions)

        hours_held = (datetime.now(timezone.utc).timestamp() - pos.get("opened_at", 0)) / 3600
        est_income = abs(pos["entry_funding"]) * pos["size_usd"] * (hours_held / 8)

        return self.ok(
            f"✅ *套利仓位已关闭* — `{sym}`\n\n"
            f"• 持有时间：`{hours_held:.0f}` 小时\n"
            f"• 预估资金费收益：`+${est_income:.2f}`\n\n"
            f"⚡ 请同步在 Binance 平掉对冲现货仓位"
        )

    def _arb_pnl(self, **_) -> dict:
        return self._arb_status()

    # ── 内部工具 ──────────────────────────────────────────────────────────────

    def _arb_positions_path(self) -> Path:
        path = self.data_dir.parent / "memory" / "arb_positions.json"
        path.parent.mkdir(exist_ok=True)
        return path

    def _load_arb_positions(self) -> dict:
        path = self._arb_positions_path()
        if not path.exists():
            return {}
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_arb_positions(self, positions: dict):
        with open(self._arb_positions_path(), "w") as f:
            json.dump(positions, f, indent=2, ensure_ascii=False)
