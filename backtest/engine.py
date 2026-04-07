"""
backtest/engine.py — 策略回测引擎
支持基于历史 HL 市场数据的策略模拟。

用法：
  from backtest.engine import BacktestEngine, FundingArbStrategy

  engine = BacktestEngine()
  engine.load_sample_data(n_periods=200)        # 用合成数据测试
  # 或：engine.load_data("data/hl_market_history.json")  # 真实历史数据

  result = engine.run(FundingArbStrategy(entry_threshold=0.0005))
  print(result.summary())
"""

import json
import math
import random
import statistics
from dataclasses import dataclass, field
from pathlib import Path


# ── 数据结构 ─────────────────────────────────────────────────────────────────

@dataclass
class Trade:
    symbol:           str
    side:             str
    entry_time:       str
    exit_time:        str
    entry_price:      float
    exit_price:       float
    size_usd:         float
    funding_collected: float
    fees:             float
    pnl:              float   # 净 PnL（含资金费，扣手续费）


@dataclass
class BacktestResult:
    trades:        list[Trade] = field(default_factory=list)
    total_pnl:     float = 0.0
    total_funding: float = 0.0
    total_fees:    float = 0.0
    win_rate:      float = 0.0
    max_drawdown:  float = 0.0
    sharpe:        float = 0.0

    def summary(self, data_label: str = "合成数据") -> str:
        if not self.trades:
            return "📊 回测结果：无交易（入场阈值可能过高）"

        wins        = sum(1 for t in self.trades if t.pnl > 0)
        sharpe_note = "优秀 ✅" if self.sharpe >= 2 else "良好" if self.sharpe >= 1 else "一般 ⚠️"
        return (
            f"📊 *回测结果*（{data_label}，仅供参数验证）\n\n"
            f"• 总交易次数：{len(self.trades)}\n"
            f"• 胜率：{wins/len(self.trades)*100:.1f}%\n"
            f"• 净 PnL：`${self.total_pnl:+.2f}`"
            f"（资金费 +${self.total_funding:.2f} / 手续费 -${self.total_fees:.2f}）\n"
            f"• 最大回撤：{self.max_drawdown:.1f}%\n"
            f"• 夏普比率：{self.sharpe:.2f}（{sharpe_note}）\n\n"
            f"_基于合成价格模拟，结果不代表真实收益_"
        )


# ── 策略：资金费率套利 ────────────────────────────────────────────────────────

class FundingArbStrategy:
    """
    资金费率套利回测策略。
    当 |funding_8h| ≥ entry_threshold 时开仓做空（或做多），
    收取资金费直到费率回落至 exit_threshold。
    """

    def __init__(self, entry_threshold: float = 0.0005,
                 exit_threshold: float = 0.0001,
                 size_usd: float = 100,
                 fee_rate: float = 0.0002):
        self.entry_threshold = entry_threshold
        self.exit_threshold  = exit_threshold
        self.size_usd        = size_usd
        self.fee_rate        = fee_rate       # 单边手续费率
        self.positions: dict = {}

    def on_tick(self, tick: dict) -> list[dict]:
        """
        处理一个市场快照 tick。
        tick = {symbol, funding_8h, mark_price, timestamp}
        返回操作列表：[{"action": "open"|"close", "symbol": ..., "side": ...}]
        """
        actions = []
        sym  = tick["symbol"]
        rate = tick["funding_8h"]

        if sym in self.positions:
            if abs(rate) <= self.exit_threshold:
                actions.append({"action": "close", "symbol": sym, "price": tick["mark_price"]})
                del self.positions[sym]
        else:
            if abs(rate) >= self.entry_threshold:
                side = "short" if rate > 0 else "long"
                actions.append({"action": "open", "symbol": sym, "side": side, "price": tick["mark_price"]})
                self.positions[sym] = {
                    "entry_rate":  rate,
                    "entry_price": tick["mark_price"],
                    "entry_time":  tick["timestamp"],
                    "side":        side,
                    "funding_collected": 0.0,
                }

        return actions

    def accrue_funding(self, tick: dict):
        """每 tick 累计资金费收益（若有持仓）。"""
        sym = tick["symbol"]
        if sym in self.positions:
            # 每个 tick 代表一个 8h 周期（简化模型）
            funding_income = abs(tick["funding_8h"]) * self.size_usd
            self.positions[sym]["funding_collected"] += funding_income


# ── 回测引擎 ─────────────────────────────────────────────────────────────────

class BacktestEngine:

    def __init__(self):
        self.data: list[dict] = []

    def load_data(self, path: str):
        """从 JSON 文件加载历史市场快照。"""
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(
                f"历史数据文件不存在：{path}\n"
                f"请先运行 backtest/data_collector.py 积累历史快照，\n"
                f"或使用 load_sample_data() 用合成数据测试。"
            )
        with open(p) as f:
            self.data = json.load(f)

    def load_sample_data(self, n_periods: int = 200, symbols: list = None):
        """生成合成历史数据用于引擎测试。"""
        symbols = symbols or ["BTC", "ETH", "SOL"]
        self.data = []
        base_prices = {"BTC": 95000, "ETH": 3500, "SOL": 180}

        for i in range(n_periods):
            # 模拟有均值回归特性的资金费率
            for sym in symbols:
                base_rate = 0.0004 * math.sin(i * 0.15) + random.gauss(0, 0.0003)
                base_rate = max(-0.003, min(0.003, base_rate))  # 限制极端值
                price_drift = 1 + random.gauss(0, 0.015)

                self.data.append({
                    "timestamp":   f"2024-{(i//90)+1:02d}-{(i//3)%30+1:02d}T{(i*8)%24:02d}:00:00Z",
                    "symbol":      sym,
                    "funding_8h":  round(base_rate, 6),
                    "mark_price":  round(base_prices[sym] * price_drift, 2),
                    "open_interest": random.uniform(5e7, 3e8),
                })

    def run(self, strategy) -> BacktestResult:
        """在加载的数据上运行策略，返回 BacktestResult。"""
        if not self.data:
            raise RuntimeError("请先调用 load_data() 或 load_sample_data()")

        result        = BacktestResult()
        open_positions: dict = {}
        equity        = 0.0
        peak_equity   = 0.0

        for tick in self.data:
            sym   = tick["symbol"]
            price = tick["mark_price"]
            rate  = tick["funding_8h"]

            # 先累计资金费，再 snapshot，然后 on_tick 可安全删除 positions
            strategy.accrue_funding(tick)

            # Snapshot funding BEFORE on_tick (which may delete strategy.positions[sym])
            funding_snapshot = {
                s: strategy.positions[s]["funding_collected"]
                for s in list(strategy.positions)
            }

            actions = strategy.on_tick(tick)

            for action in actions:
                if action["action"] == "open":
                    open_positions[sym] = {
                        "entry_price":       action["price"],
                        "entry_time":        tick["timestamp"],
                        "side":              action["side"],
                        "size_usd":          strategy.size_usd,
                        "funding_collected": 0.0,
                    }

                elif action["action"] == "close" and sym in open_positions:
                    pos     = open_positions.pop(sym)
                    # 使用 snapshot 中的资金费（on_tick 可能已删除 strategy.positions[sym]）
                    funding = funding_snapshot.get(sym, 0)

                    # 价格 PnL
                    if pos["side"] == "short":
                        price_pnl = (pos["entry_price"] - price) / pos["entry_price"] * pos["size_usd"]
                    else:
                        price_pnl = (price - pos["entry_price"]) / pos["entry_price"] * pos["size_usd"]

                    fees     = pos["size_usd"] * strategy.fee_rate * 2  # 开 + 平
                    net_pnl  = price_pnl + funding - fees

                    result.trades.append(Trade(
                        symbol=sym,
                        side=pos["side"],
                        entry_time=pos["entry_time"],
                        exit_time=tick["timestamp"],
                        entry_price=pos["entry_price"],
                        exit_price=price,
                        size_usd=pos["size_usd"],
                        funding_collected=funding,
                        fees=fees,
                        pnl=net_pnl,
                    ))

                    equity          += net_pnl
                    result.total_pnl     += net_pnl
                    result.total_funding += funding
                    result.total_fees    += fees

                    peak_equity = max(peak_equity, equity)
                    if peak_equity > 0:
                        drawdown = (peak_equity - equity) / peak_equity * 100
                        result.max_drawdown = max(result.max_drawdown, drawdown)

        # 汇总统计
        if result.trades:
            wins            = sum(1 for t in result.trades if t.pnl > 0)
            result.win_rate = wins / len(result.trades)

            pnls = [t.pnl for t in result.trades]
            if len(pnls) > 1:
                mean_pnl = statistics.mean(pnls)
                std_pnl  = statistics.stdev(pnls)
                result.sharpe = (mean_pnl / std_pnl * (252 ** 0.5)) if std_pnl > 0 else 0

        return result
