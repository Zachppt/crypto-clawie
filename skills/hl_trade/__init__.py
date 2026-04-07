"""
skills/hl_trade — Hyperliquid 永续合约交易
支持：开多/开空、平仓、设置杠杆、撤单、查询持仓

依赖：hyperliquid-python-sdk, eth-account

用法（OpenClaw / LLM 调用）：
  from skills.hl_trade import HLTradeSkill
  skill = HLTradeSkill(data_dir, memory_dir, env={})
  result = skill.run(action="open", symbol="ETH", side="long",
                     size_usd=100, leverage=3)
"""

import os
import json
import time
from pathlib import Path
from datetime import datetime, timezone

from skills.base import BaseSkill


class HLTradeSkill(BaseSkill):
    """Hyperliquid 永续合约交易技能。"""

    def run(self, action: str = "status", **kwargs) -> dict:
        action = action.lower()
        dispatch = {
            "open":         self._open_position,
            "close":        self._close_position,
            "cancel":       self._cancel_order,
            "leverage":     self._set_leverage,
            "positions":    self._get_positions,
            "status":       self._get_positions,
        }
        fn = dispatch.get(action)
        if not fn:
            return self.err(f"未知操作：{action}。可用：open / close / cancel / leverage / positions")
        return fn(**kwargs)

    # ── 内部：初始化 SDK ──────────────────────────────────────────────────────

    def _setup(self):
        """初始化 Hyperliquid SDK，返回 (info, exchange, address)。"""
        try:
            import eth_account
            from hyperliquid.info import Info
            from hyperliquid.exchange import Exchange
            from hyperliquid.utils import constants
        except ImportError:
            raise RuntimeError("请先安装：pip install hyperliquid-python-sdk eth-account")

        private_key = self.getenv("HL_PRIVATE_KEY")
        if not private_key:
            raise RuntimeError("HL_PRIVATE_KEY 未设置")

        use_testnet = self.getenv("HL_USE_TESTNET", "false").lower() == "true"
        base_url = constants.TESTNET_API_URL if use_testnet else constants.MAINNET_API_URL

        account  = eth_account.Account.from_key(private_key)
        info     = Info(base_url, skip_ws=True)
        exchange = Exchange(account, base_url)
        return info, exchange, account.address

    def _get_sz_decimals(self, info, symbol: str) -> int:
        """获取资产的数量精度。"""
        try:
            meta = info.meta()
            for asset in meta.get("universe", []):
                if asset["name"] == symbol:
                    return asset.get("szDecimals", 3)
        except Exception:
            pass
        return 3

    def _usd_to_sz(self, info, symbol: str, size_usd: float) -> float:
        """将 USD 金额转换为资产数量。"""
        mids  = info.all_mids()
        price = float(mids.get(symbol, 0))
        if not price:
            raise ValueError(f"无法获取 {symbol} 价格")
        dec = self._get_sz_decimals(info, symbol)
        return round(size_usd / price, dec)

    # ── 熔断检查 ──────────────────────────────────────────────────────────────

    def _check_circuit_breaker(self) -> tuple[bool, str]:
        """
        检查每日亏损熔断。
        返回 (blocked: bool, reason: str)。
        当日亏损超过账户 MAX_DAILY_LOSS_PCT% 时禁止新开仓。
        """
        max_loss_pct = float(self.getenv("MAX_DAILY_LOSS_PCT", "5"))

        account = self.load("hl_account.json")
        if not account:
            return False, ""
        acct_val = account.get("account_value_usdc", 0)
        if not acct_val:
            return False, ""

        history_path = self.data_dir.parent / "memory" / "trade_history.json"
        if not history_path.exists():
            return False, ""

        today = datetime.now(timezone.utc).date().isoformat()
        try:
            with open(history_path) as f:
                history = json.load(f)
        except Exception:
            return False, ""

        today_loss = sum(
            t.get("realized_pnl", 0)
            for t in history
            if t.get("timestamp", "")[:10] == today and t.get("realized_pnl", 0) < 0
        )

        loss_pct = abs(today_loss) / acct_val * 100
        if loss_pct >= max_loss_pct:
            return True, (
                f"今日亏损 `${abs(today_loss):.2f}` ({loss_pct:.1f}%) "
                f"已达熔断阈值 {max_loss_pct}%，新开仓已禁止\n"
                f"如需强制继续，请发送 `/override_circuit`"
            )
        return False, ""

    # ── 开仓 ─────────────────────────────────────────────────────────────────

    def _open_position(self, symbol: str = "ETH", side: str = "long",
                       size_usd: float = 100, leverage: int = None,
                       order_type: str = "market", price: float = None,
                       margin_mode: str = None, **_) -> dict:

        # 熔断检查（优先于 autonomous_mode 检查）
        blocked, reason = self._check_circuit_breaker()
        if blocked:
            return self.err(f"🔴 *熔断触发*\n{reason}")

        # 确认流程：autonomous_mode=false 时返回确认请求（非错误）
        autonomous = self.getenv("AUTONOMOUS_MODE", "false").lower() == "true"
        if not autonomous:
            lev = leverage or self.getenv("HL_DEFAULT_LEVERAGE", "3")
            return self.pending(
                f"⏳ *请确认开仓*\n\n"
                f"• 标的：`{symbol}`\n"
                f"• 方向：{'做多 📈' if side == 'long' else '做空 📉'}\n"
                f"• 金额：`${size_usd}` USDC\n"
                f"• 杠杆：`{lev}x`\n\n"
                f"⚠️ 自动执行已关闭（`AUTONOMOUS_MODE=false`）\n"
                f"请前往 Hyperliquid 手动下单，或在 .env 中开启自动模式。\n"
                f"发送 /position 查看当前持仓。"
            )

        max_pos = float(self.getenv("MAX_POSITION_SIZE_USD", "1000"))
        if size_usd > max_pos:
            return self.err(f"仓位 ${size_usd} 超过最大限制 ${max_pos}。请确认后重试。")

        try:
            info, exchange, address = self._setup()

            lev  = int(leverage or self.getenv("HL_DEFAULT_LEVERAGE", "3"))
            mode = (margin_mode or self.getenv("HL_DEFAULT_MARGIN_MODE", "cross")).lower()

            # 设置杠杆
            exchange.update_leverage(lev, symbol, is_cross=(mode == "cross"))

            # 计算数量
            sz     = self._usd_to_sz(info, symbol, size_usd)
            mids   = info.all_mids()
            mid_px = float(mids[symbol])
            is_buy = side == "long"

            if order_type == "market":
                slippage = 1.003 if is_buy else 0.997
                px = round(mid_px * slippage, 6)
                order_result = exchange.order(
                    symbol, is_buy, sz, px,
                    {"limit": {"tif": "Ioc"}},
                    reduce_only=False,
                )
            else:
                px = price or mid_px
                order_result = exchange.order(
                    symbol, is_buy, sz, float(px),
                    {"limit": {"tif": "Gtc"}},
                    reduce_only=False,
                )

            status = order_result.get("response", {}).get("data", {})
            self._record_trade(symbol, side, sz, mid_px, lev, order_result)

            emoji = "📈" if is_buy else "📉"
            return self.ok(
                f"{emoji} *开仓成功*\n"
                f"• 标的：`{symbol}`\n"
                f"• 方向：{'做多' if is_buy else '做空'}\n"
                f"• 数量：`{sz}` {symbol}\n"
                f"• 参考价：`${mid_px:,.2f}`\n"
                f"• 杠杆：`{lev}x` ({mode})\n"
                f"• 金额：`${size_usd}` USDC",
                data={"order": status},
            )

        except Exception as e:
            self.log.error(f"open_position failed: {e}")
            return self.err(f"开仓失败：{e}")

    # ── 平仓 ─────────────────────────────────────────────────────────────────

    def _close_position(self, symbol: str = "ETH", order_type: str = "market",
                        price: float = None, **_) -> dict:
        try:
            info, exchange, address = self._setup()

            state = info.user_state(address)
            target = None
            for pos_entry in state.get("assetPositions", []):
                p = pos_entry.get("position", {})
                if p.get("coin") == symbol and float(p.get("szi", 0)) != 0:
                    target = p
                    break

            if not target:
                return self.err(f"未找到 {symbol} 持仓")

            size    = float(target["szi"])
            is_buy  = size < 0  # 平空 → 买入；平多 → 卖出
            sz      = abs(size)
            mids    = info.all_mids()
            mid_px  = float(mids[symbol])

            if order_type == "market":
                slippage = 1.003 if is_buy else 0.997
                px = round(mid_px * slippage, 6)
                result = exchange.order(symbol, is_buy, sz, px,
                                        {"limit": {"tif": "Ioc"}}, reduce_only=True)
            else:
                px = price or mid_px
                result = exchange.order(symbol, is_buy, sz, float(px),
                                        {"limit": {"tif": "Gtc"}}, reduce_only=True)

            # 估算已实现盈亏（入场价来自缓存，无法精确，标记为估算）
            cached = self.load("hl_account.json")
            realized_pnl = 0.0
            if cached:
                for pos in cached.get("positions", []):
                    if pos["symbol"] == symbol:
                        realized_pnl = pos.get("unrealized_pnl", 0)
                        break
            self._record_trade(symbol, "close", sz, mid_px, 1, result,
                               realized_pnl=realized_pnl)

            return self.ok(
                f"✅ *平仓指令已发送*\n"
                f"• 标的：`{symbol}`\n"
                f"• 数量：`{sz}` {symbol}\n"
                f"• 参考价：`${mid_px:,.2f}`\n"
                f"• 预估盈亏：`${realized_pnl:+.2f}`",
                data={"order": result},
            )

        except Exception as e:
            self.log.error(f"close_position failed: {e}")
            return self.err(f"平仓失败：{e}")

    # ── 撤单 ─────────────────────────────────────────────────────────────────

    def _cancel_order(self, symbol: str = "ETH", order_id: int = None, **_) -> dict:
        if not order_id:
            return self.err("请提供 order_id")
        try:
            _, exchange, _ = self._setup()
            result = exchange.cancel(symbol, int(order_id))
            return self.ok(f"✅ 撤单成功：`{symbol}` 订单 `{order_id}`", data=result)
        except Exception as e:
            return self.err(f"撤单失败：{e}")

    # ── 设置杠杆 ──────────────────────────────────────────────────────────────

    def _set_leverage(self, symbol: str = "ETH", leverage: int = 3,
                      margin_mode: str = "cross", **_) -> dict:
        try:
            _, exchange, _ = self._setup()
            exchange.update_leverage(int(leverage), symbol, is_cross=(margin_mode == "cross"))
            return self.ok(
                f"✅ 杠杆已设置\n"
                f"• 标的：`{symbol}`\n"
                f"• 杠杆：`{leverage}x`\n"
                f"• 模式：`{margin_mode}`"
            )
        except Exception as e:
            return self.err(f"设置杠杆失败：{e}")

    # ── 查询持仓 ──────────────────────────────────────────────────────────────

    def _get_positions(self, **_) -> dict:
        """优先读缓存，缓存过期则实时查询。"""
        cached = self.load("hl_account.json")
        age    = self.data_age_minutes("hl_account.json")

        if cached and age < 10:
            return self._format_account(cached)

        # 缓存过期，实时查询
        try:
            info, _, address = self._setup()
            state = info.user_state(address)
            return self._format_account_raw(state)
        except Exception as e:
            if cached:
                return self._format_account(cached, stale=True)
            return self.err(f"查询持仓失败：{e}")

    def _format_account(self, data: dict, stale: bool = False) -> dict:
        positions = data.get("positions", [])
        acct_val  = data.get("account_value_usdc", 0)
        margin    = data.get("margin_used_usdc", 0)

        if not positions:
            text = f"💼 *账户概览*\n余额：`${acct_val:,.2f}` USDC\n当前无持仓"
        else:
            lines = [f"💼 *账户概览*\n余额：`${acct_val:,.2f}` USDC | 已用保证金：`${margin:,.2f}`\n"]
            for p in positions:
                side_emoji = "📈" if p["side"] == "long" else "📉"
                pnl_emoji  = "🟢" if p["unrealized_pnl"] >= 0 else "🔴"
                lines.append(
                    f"{side_emoji} `{p['symbol']}` {p['side']} × {p['leverage']}x\n"
                    f"  数量：{p['size']} | 入场：${p['entry_price']:,.2f}\n"
                    f"  爆仓：${p['liq_price']:,.2f} (距 {p['dist_to_liq_pct']:.1f}%)\n"
                    f"  未实现盈亏：{pnl_emoji} ${p['unrealized_pnl']:+.2f}"
                )
            text = "\n".join(lines)

        if stale:
            text += "\n\n⚠️ _数据可能已过期，fetcher 可能未运行_"
        return self.ok(text, data=data)

    def _format_account_raw(self, state: dict) -> dict:
        margin  = state.get("marginSummary", {})
        acct    = float(margin.get("accountValue", 0))
        used    = float(margin.get("totalMarginUsed", 0))
        positions = []

        for pos_entry in state.get("assetPositions", []):
            p    = pos_entry.get("position", {})
            size = float(p.get("szi", 0))
            if size == 0:
                continue
            positions.append({
                "symbol":       p.get("coin"),
                "side":         "long" if size > 0 else "short",
                "size":         abs(size),
                "entry_price":  float(p.get("entryPx") or 0),
                "liq_price":    float(p.get("liquidationPx") or 0),
                "unrealized_pnl": float(p.get("unrealizedPnl") or 0),
                "leverage":     p.get("leverage", {}).get("value", 1),
                "dist_to_liq_pct": 0,
            })

        data = {"account_value_usdc": acct, "margin_used_usdc": used, "positions": positions}
        return self._format_account(data)

    # ── 记录交易 ──────────────────────────────────────────────────────────────

    def _record_trade(self, symbol, side, sz, price, leverage, order_result,
                      realized_pnl: float = 0.0):
        history_path = self.data_dir.parent / "memory" / "trade_history.json"
        try:
            if history_path.exists():
                with open(history_path) as f:
                    history = json.load(f)
            else:
                history = []

            history.append({
                "timestamp":    datetime.now(timezone.utc).isoformat(),
                "symbol":       symbol,
                "side":         side,
                "size":         sz,
                "price":        price,
                "leverage":     leverage,
                "realized_pnl": realized_pnl,
                "platform":   "hyperliquid",
                "raw_result": str(order_result),
            })
            history_path.parent.mkdir(exist_ok=True)
            with open(history_path, "w") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.log.warning(f"record_trade failed: {e}")
