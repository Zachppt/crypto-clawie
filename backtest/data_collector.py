"""
backtest/data_collector.py — 历史数据收集器
将当前 data/hl_market.json 快照追加到 data/hl_market_history.json，
积累历史数据用于回测。

用法：
  python backtest/data_collector.py       # 追加一次快照
  # 建议加入 scheduler.py 定时任务，每 8h 收集一次：
  # scheduler.add_job(collect_snapshot, IntervalTrigger(hours=8), id="backtest_collect")
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR    = Path(__file__).parent.parent
DATA_DIR    = BASE_DIR / "data"
HISTORY_FILE = DATA_DIR / "hl_market_history.json"


def collect_snapshot():
    """读取当前 hl_market.json，追加到历史数据文件。"""
    src = DATA_DIR / "hl_market.json"
    if not src.exists():
        print("hl_market.json 不存在，跳过。请检查 fetcher 是否运行。")
        return 0

    with open(src) as f:
        raw = json.load(f)

    data    = raw.get("data", raw)
    updated = raw.get("_updated", datetime.now(timezone.utc).isoformat())

    # 构建快照列表（每个 asset 一条记录）
    snapshot_records = []
    for a in data.get("assets", []):
        snapshot_records.append({
            "timestamp":    updated,
            "symbol":       a["symbol"],
            "funding_8h":   a["funding_8h"],
            "mark_price":   a["mark_price"],
            "open_interest": a["open_interest"],
            "change_24h_pct": a.get("change_24h_pct", 0),
        })

    # 追加到历史文件
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE) as f:
            history = json.load(f)
    else:
        history = []

    history.extend(snapshot_records)

    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, ensure_ascii=False)

    print(f"已追加 {len(snapshot_records)} 条记录，历史共 {len(history)} 条。")
    return len(snapshot_records)


if __name__ == "__main__":
    collect_snapshot()
