"""
skills/exchange_trade — 多交易所统一交易层
使用 ccxt 支持 Binance / OKX / Bybit 永续合约开平仓。
Hyperliquid 仍由 skills/hl_trade 处理（原生 SDK，EIP-712 签名）。

.env 配置：
  TRADING_EXCHANGE=hyperliquid   # 默认交易所
  BINANCE_API_KEY / BINANCE_SECRET_KEY
  OKX_API_KEY / OKX_SECRET_KEY / OKX_PASSPHRASE
  BYBIT_API_KEY / BYBIT_SECRET_KEY
"""
from __future__ import annotations

from skills.base import BaseSkill

# ccxt 永续合约 symbol 格式：BTC/USDT:USDT
_SYMBOL_MAP = {
    "binance": lambda s: f"{s}/USDT:USDT",
    "okx":     lambda s: f"{s}/USDT:USDT",
    "bybit":   lambda s: f"{s}/USDT:USDT",
}

_EXCHANGE_LABELS = {
    "binance": "Binance 合约",
    "okx":     "OKX 合约",
    "bybit":   "Bybit 合约",
}


class ExchangeTradeSkill(BaseSkill):

    def run(self, action: str = "positions", exchange: str = "binance", **kwargs) -> dict:
        ex = exchange.lower().strip()
        dispatch = {
            "open":      self._open,
            "close":     self._close,
            "positions": self._positions,
            "leverage":  self._set_leverage,
        }
        fn = dispatch.get(action.lower())
        if not fn:
            return self.err(f"未知操作：{action}。可用：open / close / positions / leverage")
        return fn(exchange=ex, **kwargs)

    # ── 初始化 ccxt 交易所实例 ────────────────────────────────────────────────

    def _get_exchange(self, exchange: str):
        try:
            import ccxt
        except ImportError:
            return None, "⚠️ 请先安装：`pip install ccxt`"

        key    = self.getenv(f"{exchange.upper()}_API_KEY")
        secret = self.getenv(f"{exchange.upper()}_SECRET_KEY")

        if not key or not secret:
            return None, (
                f"⚠️ 未配置 {exchange.upper()} 凭证\n\n"
                f"在 `.env` 中添加：\n"
                f"`{exchange.upper()}_API_KEY=...`\n"
                f"`{exchange.upper()}_SECRET_KEY=...`"
            )

        options = {"defaultType": "future"}
        params  = {"apiKey": key, "secret": secret, "options": options}

        if exchange == "okx":
            passphrase = self.getenv("OKX_PASSPHRASE")
            if not passphrase:
                return None, "⚠️ OKX 还需要配置 `OKX_PASSPHRASE`"
            params["password"] = passphrase
            options["defaultType"] = "swap"

        if exchange == "bybit":
            options["defaultType"] = "future"

        try:
            ex_cls = getattr(ccxt, exchange)
            return ex_cls(params), None
        except AttributeError:
            return None, f"⚠️ 不支持的交易所：{exchange}"
        except Exception as e:
            return None, f"⚠️ 初始化 {exchange} 失败：{e}"

    # ── 开仓 ─────────────────────────────────────────────────────────────────

    def _open(self, exchange: str, symbol: str = "BTC", side: str = "long",
              size_usd: float = 100.0, leverage: int = None, **_) -> dict:

        autonomous = self.getenv("AUTONOMOUS_MODE", "false").lower() == "true"
        if not autonomous:
            return self.err(
                "需要开启 `AUTONOMOUS_MODE=true` 才能自动执行交易\n"
                "修改 `.env` 后重启 bot 生效"
            )

        blocked, reason = self._check_circuit_breaker()
        if blocked:
            return self.err(f"🔒 熔断保护：{reason}")

        ex, err = self._get_exchange(exchange)
        if not ex:
            return self.err(err)

        label  = _EXCHANGE_LABELS.get(exchange, exchange)
        sym_fn = _SYMBOL_MAP.get(exchange)
        if not sym_fn:
            return self.err(f"不支持的交易所：{exchange}")

        ccxt_symbol = sym_fn(symbol.upper())
        order_side  = "buy" if side == "long" else "sell"
        lev         = leverage or int(self.getenv("HL_DEFAULT_LEVERAGE", "3"))

        try:
            ex.load_markets()

            # 设置杠杆
            try:
                ex.set_leverage(lev, ccxt_symbol)
            except Exception:
                pass  # 部分交易所不支持 API 设杠杆

            # 获取当前价格
            ticker = ex.fetch_ticker(ccxt_symbol)
            price  = ticker["last"]
            if not price:
                return self.err(f"无法获取 {symbol} 价格")

            max_pos = float(self.getenv("MAX_POSITION_SIZE_USD", "500"))
            if size_usd > max_pos:
                return self.err(f"仓位 ${size_usd} 超过上限 ${max_pos}")

            qty = round(size_usd / price, 6)

            order = ex.create_order(
                ccxt_symbol, "market", order_side, qty,
                params={"reduceOnly": False},
            )

            oid   = order.get("id", "—")
            filled = order.get("filled") or qty
            avg_px = order.get("average") or price

            return self.ok(
                f"✅ *{label} 开仓成功*\n\n"
                f"• 标的：`{symbol.upper()}`\n"
                f"• 方向：{'做多 📈' if side == 'long' else '做空 📉'}\n"
                f"• 成交数量：`{filled}`\n"
                f"• 成交均价：`${avg_px:,.2f}`\n"
                f"• 杠杆：`{lev}x`\n"
                f"• 订单 ID：`{oid}`",
                data={"order": order, "exchange": exchange},
            )

        except Exception as e:
            return self.err(f"{label} 开仓失败：{e}")

    # ── 平仓 ─────────────────────────────────────────────────────────────────

    def _close(self, exchange: str, symbol: str = "BTC", **_) -> dict:
        autonomous = self.getenv("AUTONOMOUS_MODE", "false").lower() == "true"
        if not autonomous:
            return self.err("需要开启 `AUTONOMOUS_MODE=true` 才能自动执行")

        ex, err = self._get_exchange(exchange)
        if not ex:
            return self.err(err)

        label       = _EXCHANGE_LABELS.get(exchange, exchange)
        ccxt_symbol = _SYMBOL_MAP[exchange](symbol.upper())

        try:
            ex.load_markets()
            positions = ex.fetch_positions([ccxt_symbol])
            pos = next((p for p in positions if abs(p.get("contracts") or 0) > 0), None)

            if not pos:
                return self.ok(f"📭 {label} 当前无 {symbol.upper()} 持仓")

            contracts  = abs(pos["contracts"])
            close_side = "sell" if pos["side"] == "long" else "buy"

            order = ex.create_order(
                ccxt_symbol, "market", close_side, contracts,
                params={"reduceOnly": True},
            )

            avg_px = order.get("average") or 0
            pnl    = pos.get("unrealizedPnl") or 0

            return self.ok(
                f"✅ *{label} 平仓成功*\n\n"
                f"• 标的：`{symbol.upper()}`\n"
                f"• 平仓数量：`{contracts}`\n"
                f"• 成交均价：`${avg_px:,.2f}`\n"
                f"• 未实现盈亏：`${pnl:+.2f}`",
                data={"order": order, "exchange": exchange},
            )

        except Exception as e:
            return self.err(f"{label} 平仓失败：{e}")

    # ── 查询持仓 ──────────────────────────────────────────────────────────────

    def _positions(self, exchange: str, **_) -> dict:
        ex, err = self._get_exchange(exchange)
        if not ex:
            return self.err(err)

        label = _EXCHANGE_LABELS.get(exchange, exchange)

        try:
            ex.load_markets()
            positions = [
                p for p in ex.fetch_positions()
                if abs(p.get("contracts") or 0) > 0
            ]

            if not positions:
                return self.ok(f"📭 *{label}*\n当前无持仓")

            balance = ex.fetch_balance()
            total   = balance.get("USDT", {}).get("total") or 0

            lines = [f"💼 *{label}*\n余额：`${total:,.2f}` USDT\n"]
            for p in positions:
                sym    = p.get("symbol", "")
                side   = p.get("side", "")
                size   = abs(p.get("contracts") or 0)
                entry  = p.get("entryPrice") or 0
                pnl    = p.get("unrealizedPnl") or 0
                lev    = p.get("leverage") or "—"
                emoji  = "📈" if side == "long" else "📉"
                pnl_e  = "🟢" if pnl >= 0 else "🔴"
                lines.append(
                    f"{emoji} *{sym}* {side} ×{lev}x\n"
                    f"  数量：`{size}` | 入场：`${entry:,.2f}`\n"
                    f"  未实现盈亏：{pnl_e} `${pnl:+.2f}`"
                )

            return self.ok("\n".join(lines), data={"positions": positions})

        except Exception as e:
            return self.err(f"{label} 查询持仓失败：{e}")

    # ── 设置杠杆 ──────────────────────────────────────────────────────────────

    def _set_leverage(self, exchange: str, symbol: str = "BTC",
                      leverage: int = 3, **_) -> dict:
        ex, err = self._get_exchange(exchange)
        if not ex:
            return self.err(err)

        label       = _EXCHANGE_LABELS.get(exchange, exchange)
        ccxt_symbol = _SYMBOL_MAP[exchange](symbol.upper())

        try:
            ex.load_markets()
            ex.set_leverage(leverage, ccxt_symbol)
            return self.ok(
                f"✅ *{label}* `{symbol.upper()}` 杠杆已设置为 `{leverage}x`"
            )
        except Exception as e:
            return self.err(f"{label} 设置杠杆失败：{e}")
