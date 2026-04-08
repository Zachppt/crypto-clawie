"""
skills/hl_grid — 网格交易管理
在指定价格区间内均匀分布限价单，利用市场波动赚取价差。
网格状态持久化到 memory/grid_positions.json

用法（Telegram）：
  /grid status                              — 查看所有网格
  /grid BTC 90000 100000 10 50             — 创建网格（低价 高价 格数 每格USD）
  /grid cancel BTC_grid_1                  — 取消指定网格
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from skills.base import BaseSkill


class HLGridSkill(BaseSkill):

    def run(self, action: str = "status", **kwargs) -> dict:
        # 解析简洁的命令格式：/grid BTC 90000 100000 10 50
        args = kwargs.get("args", [])
        if args and args[0].upper() not in ("STATUS", "CANCEL", "PNL"):
            return self._create_grid_from_args(args)

        dispatch = {
            "status": self._grid_status,
            "cancel": self._cancel_grid,
            "pnl":    self._grid_pnl,
            "create": lambda **kw: self._create_grid_from_args(kw.get("args", [])),
        }
        fn = dispatch.get(action.lower())
        if fn is None:
            return self.err(f"未知操作：{action}。可用：status / cancel / pnl 或直接 /grid BTC 低价 高价 格数 每格USD")
        return fn(**kwargs)

    # ── 创建网格 ──────────────────────────────────────────────────────────────

    def _create_grid_from_args(self, args: list) -> dict:
        """解析 /grid BTC 90000 100000 10 50 格式的命令。"""
        try:
            symbol        = args[0].upper()
            price_low     = float(args[1])
            price_high    = float(args[2])
            grid_count    = int(args[3]) if len(args) > 3 else 10
            size_per_grid = float(args[4]) if len(args) > 4 else 50
        except (IndexError, ValueError):
            return self.err(
                "格式错误。正确格式：\n"
                "`/grid <币种> <低价> <高价> <格数> <每格USD>`\n"
                "例：`/grid BTC 90000 100000 10 50`"
            )
        return self._create_grid(symbol=symbol, price_low=price_low, price_high=price_high,
                                 grid_count=grid_count, size_per_grid_usd=size_per_grid)

    def _create_grid(self, symbol: str, price_low: float, price_high: float,
                     grid_count: int = 10, size_per_grid_usd: float = 50, **_) -> dict:
        """创建网格并挂限价单。"""
        blocked, reason = self._check_circuit_breaker()
        if blocked:
            return self.err(f"🔴 *熔断触发*\n{reason}")

        if price_low >= price_high:
            return self.err("低价必须小于高价")
        if grid_count < 2:
            return self.err("格数至少为 2")
        if grid_count > 50:
            return self.err("格数不超过 50（避免手续费过高）")

        # 获取当前价格
        market = self.load("hl_market.json")
        assets = {a["symbol"]: a for a in market.get("assets", [])} if market else {}
        asset  = assets.get(symbol)
        if not asset:
            return self.err(f"未找到 {symbol} 市场数据")

        current_price = asset["mark_price"]
        if not (price_low < current_price < price_high):
            return self.err(
                f"当前价格 ${current_price:,.2f} 不在网格区间 "
                f"[${price_low:,.2f}, ${price_high:,.2f}] 内"
            )

        # 计算网格价格层级
        step   = (price_high - price_low) / (grid_count - 1)
        levels = [round(price_low + step * i, 2) for i in range(grid_count)]

        # 区分买单（低于当前价）和卖单（高于当前价）
        buy_levels  = [p for p in levels if p < current_price]
        sell_levels = [p for p in levels if p > current_price]

        total_capital = size_per_grid_usd * grid_count

        # 尝试在 HL 挂单（若 SDK 可用）
        order_ids = []
        sdk_error = None
        try:
            info, exchange, address = self._setup_sdk()
            for px in buy_levels:
                sz = round(size_per_grid_usd / px, asset.get("sz_decimals", 3))
                result = exchange.order(symbol, True, sz, px, {"limit": {"tif": "Gtc"}})
                oid = result.get("response", {}).get("data", {})
                order_ids.append({"price": px, "side": "buy", "status": "placed", "result": str(oid)})

            for px in sell_levels:
                sz = round(size_per_grid_usd / px, asset.get("sz_decimals", 3))
                result = exchange.order(symbol, False, sz, px, {"limit": {"tif": "Gtc"}})
                oid = result.get("response", {}).get("data", {})
                order_ids.append({"price": px, "side": "sell", "status": "placed", "result": str(oid)})
        except Exception as e:
            sdk_error = str(e)
            # SDK 不可用时仅记录网格状态，不挂实际订单
            order_ids = [{"price": p, "side": "buy",  "status": "pending"} for p in buy_levels] + \
                        [{"price": p, "side": "sell", "status": "pending"} for p in sell_levels]

        # 持久化网格状态
        grids = self._load_grids()
        grid_id = f"{symbol}_grid_{int(time.time())}"
        grids[grid_id] = {
            "grid_id":        grid_id,
            "symbol":         symbol,
            "price_low":      price_low,
            "price_high":     price_high,
            "grid_count":     grid_count,
            "size_per_grid":  size_per_grid_usd,
            "total_capital":  total_capital,
            "current_price":  current_price,
            "levels":         levels,
            "orders":         order_ids,
            "created_at":     datetime.now(timezone.utc).isoformat(),
            "status":         "active",
        }
        self._save_grids(grids)

        sdk_note = f"\n\n⚠️ SDK 挂单失败（{sdk_error}）\n订单已记录为 pending，需手动在 HL 挂单" if sdk_error else ""

        return self.ok(
            f"✅ *网格已创建* — `{grid_id}`\n\n"
            f"• 标的：`{symbol}` | 当前价 `${current_price:,.2f}`\n"
            f"• 区间：`${price_low:,.2f}` — `${price_high:,.2f}`\n"
            f"• 格数：`{grid_count}` | 每格：`${size_per_grid_usd}`\n"
            f"• 总资金：`${total_capital:,.0f}` USDC\n"
            f"• 买单：{len(buy_levels)} 个 | 卖单：{len(sell_levels)} 个"
            f"{sdk_note}",
            data={"grid_id": grid_id, "orders": order_ids}
        )

    # ── 查看网格状态 ──────────────────────────────────────────────────────────

    def _grid_status(self, **_) -> dict:
        grids = self._load_grids()
        if not grids:
            return self.ok(
                "📊 当前无活跃网格\n\n"
                "创建网格：`/grid <币种> <低价> <高价> <格数> <每格USD>`\n"
                "示例：`/grid BTC 90000 100000 10 50`"
            )

        market = self.load("hl_market.json")
        assets = {a["symbol"]: a for a in market.get("assets", [])} if market else {}
        lines  = [f"📊 *网格状态* — {len(grids)} 个\n"]

        for gid, g in grids.items():
            sym     = g["symbol"]
            cur_px  = assets.get(sym, {}).get("mark_price", g.get("current_price", 0))
            in_range = g["price_low"] <= cur_px <= g["price_high"]

            range_emoji = "🟢" if in_range else "🟡"
            lines.append(
                f"{range_emoji} `{gid}`\n"
                f"  {sym} | ${g['price_low']:,.0f}—${g['price_high']:,.0f} | {g['grid_count']} 格\n"
                f"  当前价 ${cur_px:,.2f} | 总资金 ${g['total_capital']:,.0f}\n"
                f"  创建：{g['created_at'][:10]}"
            )

        lines.append(f"\n取消网格：`/grid cancel <grid_id>`")
        return self.ok("\n".join(lines), data={"grids": grids})

    # ── 取消网格 ──────────────────────────────────────────────────────────────

    def _cancel_grid(self, grid_id: str = None, **_) -> dict:
        if not grid_id:
            return self.err("请指定 grid_id，例如：/grid cancel BTC_grid_1234567890")

        grids = self._load_grids()
        if grid_id not in grids:
            return self.err(f"未找到网格 `{grid_id}`\n发送 /grid status 查看所有网格")

        g = grids.pop(grid_id)
        self._save_grids(grids)

        return self.ok(
            f"✅ *网格已取消* — `{grid_id}`\n\n"
            f"• 标的：`{g['symbol']}`\n"
            f"• 区间：${g['price_low']:,.0f} — ${g['price_high']:,.0f}\n\n"
            f"⚡ 请在 Hyperliquid 手动撤销对应限价单"
        )

    def _grid_pnl(self, **_) -> dict:
        return self._grid_status()

    # ── SDK 初始化（复用 hl_trade 模式）─────────────────────────────────────

    def _setup_sdk(self):
        import eth_account
        from hyperliquid.info import Info
        from hyperliquid.exchange import Exchange
        from hyperliquid.utils import constants

        pk = self.getenv("HL_PRIVATE_KEY")
        if not pk:
            raise RuntimeError("HL_PRIVATE_KEY 未设置")

        use_testnet = self.getenv("HL_USE_TESTNET", "false").lower() == "true"
        base_url    = constants.TESTNET_API_URL if use_testnet else constants.MAINNET_API_URL
        account     = eth_account.Account.from_key(pk)
        return Info(base_url, skip_ws=True), Exchange(account, base_url), account.address

    # ── 持久化 ────────────────────────────────────────────────────────────────

    def _grids_path(self) -> Path:
        p = self.data_dir.parent / "memory" / "grid_positions.json"
        p.parent.mkdir(exist_ok=True)
        return p

    def _load_grids(self) -> dict:
        p = self._grids_path()
        if not p.exists():
            return {}
        try:
            with open(p) as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_grids(self, grids: dict):
        with open(self._grids_path(), "w") as f:
            json.dump(grids, f, indent=2, ensure_ascii=False)
