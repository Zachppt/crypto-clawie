"""
crypto-clawie 沙盒测试套件
测试所有 Skill 及 BacktestEngine 的核心功能，无需真实 API Key 或 Telegram。
运行方式：
    python3 -m pytest tests/test_sandbox.py -v
    或直接：python3 tests/test_sandbox.py
"""

import sys
import json
import time
import shutil
import tempfile
from pathlib import Path
from datetime import datetime, timezone

# ── 路径设置 ─────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

DATA_DIR   = ROOT / "data"
MEMORY_DIR = ROOT / "memory"
ENV = {}   # 沙盒模式：不需要真实 API 密钥


def make_skill(cls):
    """实例化 Skill，注入测试用的 data/memory 路径。"""
    return cls(data_dir=DATA_DIR, memory_dir=MEMORY_DIR, env=ENV)


# ════════════════════════════════════════════════════════════════════════════
# 测试工具
# ════════════════════════════════════════════════════════════════════════════

PASS = "✅ PASS"
FAIL = "❌ FAIL"
results = []


def check(name: str, result: dict, expect_success: bool = True,
          contains: str = None, data_key: str = None):
    """断言辅助：打印测试结果并记录。"""
    ok   = result.get("success") == expect_success
    text = result.get("text", "")

    if ok and contains and contains not in text:
        ok = False

    if ok and data_key and data_key not in result.get("data", {}):
        ok = False

    status = PASS if ok else FAIL
    results.append((name, ok))
    short_text = text[:80].replace("\n", " ") if text else "(no text)"
    print(f"  {status}  {name}")
    if not ok:
        print(f"         result={result}")
    return ok


# ════════════════════════════════════════════════════════════════════════════
# 1. BaseSkill — 数据加载与辅助方法
# ════════════════════════════════════════════════════════════════════════════

def test_base_skill():
    print("\n[1] BaseSkill")
    from skills.base import BaseSkill

    class _Dummy(BaseSkill):
        def run(self, **kw):
            pass

    sk = _Dummy(data_dir=DATA_DIR, memory_dir=MEMORY_DIR, env={"MY_KEY": "hello"})

    # load hl_market.json
    data = sk.load("hl_market.json")
    check("load hl_market.json 返回 dict",
          {"success": data is not None and isinstance(data, dict)})
    check("hl_market.json 包含 assets",
          {"success": "assets" in data and len(data["assets"]) > 0})

    # load 不存在的文件
    missing = sk.load("nonexistent.json")
    check("load 不存在文件返回 None", {"success": missing is None})

    # data_age_minutes（文件有 _updated，应 < 1000 分钟）
    age = sk.data_age_minutes("hl_market.json")
    check("data_age_minutes 返回合理值", {"success": 0 < age < 100000})

    # getenv
    val = sk.getenv("MY_KEY")
    check("getenv 从 env dict 读取", {"success": val == "hello"})

    # ok / err
    r_ok  = BaseSkill.ok("test ok", {"k": "v"})
    r_err = BaseSkill.err("something wrong")
    check("ok() 返回 success=True",  {"success": r_ok["success"] is True})
    check("err() 返回 success=False", {"success": r_err["success"] is False})
    check("err() text 包含 ❌", {"success": "❌" in r_err["text"]})


# ════════════════════════════════════════════════════════════════════════════
# 2. CryptoDataSkill — 价格与行情
# ════════════════════════════════════════════════════════════════════════════

def test_crypto_data():
    print("\n[2] CryptoDataSkill")
    from skills.crypto_data import CryptoDataSkill
    sk = make_skill(CryptoDataSkill)

    # price — 已知 symbol
    r = sk.run(action="price", symbol="BTC")
    check("price BTC 成功", r, contains="BTC")
    check("price BTC 含资金费率", r, contains="资金费率")

    r = sk.run(action="price", symbol="ETH")
    check("price ETH 成功", r, contains="ETH")

    # price — 未知 symbol
    r = sk.run(action="price", symbol="FAKECOIN999")
    check("price 未知 symbol 返回错误", r, expect_success=False)

    # overview
    r = sk.run(action="overview")
    check("overview 成功", r, contains="市场行情")
    check("overview 含 BTC", r, contains="BTC")

    # fear_greed
    r = sk.run(action="fng")
    check("fng 成功", r)
    check("fng 含数值", r, contains="72")

    # 未知 action
    r = sk.run(action="unknown_action")
    check("未知 action 返回错误", r, expect_success=False)


# ════════════════════════════════════════════════════════════════════════════
# 3. HLMonitorSkill — HL 市场监控
# ════════════════════════════════════════════════════════════════════════════

def test_hl_monitor():
    print("\n[3] HLMonitorSkill")
    from skills.hl_monitor import HLMonitorSkill
    sk = make_skill(HLMonitorSkill)

    # overview
    r = sk.run(action="overview")
    check("overview 成功", r, contains="HL 市场概览")
    check("overview 含资金费率 Top", r, contains="Top 5")

    # funding — 全市场排行
    r = sk.run(action="funding")
    check("funding 排行 成功", r, contains="资金费率排行")

    # funding — 单个 symbol
    r = sk.run(action="funding", symbol="WIF")
    check("funding WIF 成功", r, contains="WIF")
    check("funding WIF 含年化", r, contains="年化")

    # funding — 不存在的 symbol
    r = sk.run(action="funding", symbol="FAKECOIN")
    check("funding 不存在 symbol 错误", r, expect_success=False)

    # open_interest
    r = sk.run(action="oi")
    check("oi 排行 成功", r, contains="未平仓量 Top")

    r = sk.run(action="oi", symbol="BTC")
    check("oi BTC 成功", r, contains="BTC")

    # account
    r = sk.run(action="account")
    check("account 成功", r, contains="账户概览")
    check("account 含余额", r, contains="2,340.50")

    # liquidation
    r = sk.run(action="liquidation")
    check("liquidation 成功", r)

    # 未知 action
    r = sk.run(action="bad_action")
    check("未知 action 错误", r, expect_success=False)


# ════════════════════════════════════════════════════════════════════════════
# 4. CryptoAlertSkill — 多因子信号扫描
# ════════════════════════════════════════════════════════════════════════════

def test_crypto_alert():
    print("\n[4] CryptoAlertSkill")
    from skills.crypto_alert import CryptoAlertSkill
    sk = make_skill(CryptoAlertSkill)

    # scan 全扫描（WIF/DOGE 应触发信号）
    r = sk.run(action="scan")
    check("scan 全扫描 成功", r)
    check("scan 发现 WIF 信号", r, contains="WIF")

    # funding 专项扫描
    r = sk.run(action="funding")
    check("funding 信号扫描 成功", r)

    # funding 自定义高阈值（不应有信号）
    r = sk.run(action="funding", threshold=0.01)
    check("funding 高阈值无信号", r, contains="无资金费率异动")

    # price 专项扫描（SOL +5.8% / HYPE +12.5% 应触发）
    r = sk.run(action="price")
    check("price 信号扫描 成功", r)

    # liq 爆仓风险扫描（ETH dist=5.7% 应触发）
    r = sk.run(action="liq")
    check("liq 爆仓扫描 成功", r)

    # funding_arb 套利机会（WIF/DOGE ≥ 0.0005）
    r = sk.run(action="funding_arb")
    check("funding_arb 成功", r, contains="套利机会")
    check("funding_arb 含 WIF", r, contains="WIF")

    # funding_arb 高阈值（无机会）
    r = sk.run(action="funding_arb", min_rate=0.05)
    check("funding_arb 高阈值无机会", r, contains="无套利机会")

    # min_confidence 过滤（全部过滤）
    r = sk.run(action="scan", min_confidence=1.1)
    check("scan 极高置信度过滤无信号", r, contains="无异动信号")


# ════════════════════════════════════════════════════════════════════════════
# 5. FundingArbSkill — 套利仓位管理
# ════════════════════════════════════════════════════════════════════════════

def test_funding_arb():
    print("\n[5] FundingArbSkill")
    from skills.funding_arb import FundingArbSkill
    sk = make_skill(FundingArbSkill)

    # 确保清空旧套利记录
    arb_path = MEMORY_DIR / "arb_positions.json"
    if arb_path.exists():
        arb_path.write_text("{}")

    # scan — 应找到 WIF, DOGE, ETH, HYPE
    r = sk.run(action="scan")
    check("scan 成功", r, contains="套利机会")
    check("scan 含 WIF", r, contains="WIF")

    # scan 高阈值（0.002 → 只剩 WIF 0.00183，不满足）
    r = sk.run(action="scan", min_rate=0.002)
    check("scan 高阈值无机会", r, contains="无套利机会")

    # open — WIF 满足阈值
    r = sk.run(action="open", symbol="WIF", size_usd=500)
    check("open WIF 成功", r, contains="套利仓位已记录")
    check("open WIF 含金额", r, contains="500")
    check("open WIF 含 Binance 提示", r, contains="Binance")

    # open — 重复开仓（覆盖上一条）
    r = sk.run(action="open", symbol="WIF", size_usd=200)
    check("open WIF 重复开仓覆盖", r)

    # open — 费率未达阈值 (ARB 0.00008 < 0.0005)
    r = sk.run(action="open", symbol="ARB", size_usd=100)
    check("open ARB 费率不足被拒绝", r, expect_success=False, contains="未达入场阈值")

    # open — 缺少 symbol
    r = sk.run(action="open")
    check("open 缺 symbol 报错", r, expect_success=False)

    # status — WIF 仓位应存在
    r = sk.run(action="status")
    check("status 成功", r, contains="套利仓位状态")
    check("status 含 WIF", r, contains="WIF")

    # close — 关闭 WIF
    r = sk.run(action="close", symbol="WIF")
    check("close WIF 成功", r, contains="套利仓位已关闭")
    check("close WIF 含 Binance 平仓提示", r, contains="Binance")

    # close — 再次关闭（已不存在）
    r = sk.run(action="close", symbol="WIF")
    check("close 不存在仓位报错", r, expect_success=False)

    # close — 缺少 symbol
    r = sk.run(action="close")
    check("close 缺 symbol 报错", r, expect_success=False)

    # status — 关闭后无仓位
    r = sk.run(action="status")
    check("status 关闭后无仓位", r, contains="无活跃套利仓位")

    # pnl（等价于 status）
    r = sk.run(action="pnl")
    check("pnl 成功", r)

    # 未知 action
    r = sk.run(action="xxx")
    check("未知 action 报错", r, expect_success=False)


# ════════════════════════════════════════════════════════════════════════════
# 6. HLGridSkill — 网格交易
# ════════════════════════════════════════════════════════════════════════════

def test_hl_grid():
    print("\n[6] HLGridSkill")
    from skills.hl_grid import HLGridSkill
    sk = make_skill(HLGridSkill)

    # 清空旧网格
    grid_path = MEMORY_DIR / "grid_positions.json"
    if grid_path.exists():
        grid_path.write_text("{}")

    # status — 无网格
    r = sk.run(action="status")
    check("status 无网格", r, contains="无活跃网格")

    # 创建网格 — BTC (当前价 95230，区间 90000-100000)
    r = sk.run(action="create", args=["BTC", "90000", "100000", "10", "50"])
    check("create BTC 网格成功", r, contains="网格已创建")
    check("create BTC 含 grid_id", r, data_key="grid_id")

    # 从 data 取 grid_id
    grid_id = r["data"].get("grid_id", "")
    check("grid_id 非空", {"success": bool(grid_id)})

    # status — 有网格
    r = sk.run(action="status")
    check("status 有网格", r, contains="网格状态")

    # 参数格式错误（缺参数）
    r = sk.run(action="create", args=["BTC"])
    check("create 缺参数报错", r, expect_success=False)

    # 价格区间错误（低价 > 高价）
    r = sk.run(action="create", args=["BTC", "100000", "90000", "10", "50"])
    check("create 高低价反转报错", r, expect_success=False)

    # 格数太少
    r = sk.run(action="create", args=["BTC", "90000", "100000", "1", "50"])
    check("create 格数不足报错", r, expect_success=False)

    # 当前价格不在区间内（ETH 3480，区间 1000-2000）
    r = sk.run(action="create", args=["ETH", "1000", "2000", "10", "50"])
    check("create 价格不在区间报错", r, expect_success=False, contains="不在网格区间")

    # 未知 symbol
    r = sk.run(action="create", args=["FAKECOIN", "1", "100", "5", "10"])
    check("create 未知 symbol 报错", r, expect_success=False)

    # cancel — 取消已有网格
    r = sk.run(action="cancel", grid_id=grid_id)
    check("cancel 成功", r, contains="网格已取消")

    # cancel — 取消不存在的网格
    r = sk.run(action="cancel", grid_id="nonexistent_grid_123")
    check("cancel 不存在网格报错", r, expect_success=False)

    # cancel — 缺 grid_id
    r = sk.run(action="cancel")
    check("cancel 缺 grid_id 报错", r, expect_success=False)

    # pnl（等价于 status）
    r = sk.run(action="pnl")
    check("pnl 成功", r)

    # 未知 action
    r = sk.run(action="bad")
    check("未知 action 报错", r, expect_success=False)


# ════════════════════════════════════════════════════════════════════════════
# 7. CryptoReportSkill — 每日/每周报告
# ════════════════════════════════════════════════════════════════════════════

def test_crypto_report():
    print("\n[7] CryptoReportSkill")
    from skills.crypto_report import CryptoReportSkill
    sk = make_skill(CryptoReportSkill)

    # 每日报告
    r = sk.run(period="daily")
    check("daily 报告成功", r, contains="每日市场报告")
    check("daily 含恐慌贪婪", r, contains="市场情绪")
    check("daily 含 BTC 价格", r, contains="BTC")
    check("daily 含账户状态", r, contains="账户状态")

    # 每周报告（无交易记录）
    r = sk.run(period="weekly")
    check("weekly 报告成功", r, contains="每周复盘报告")
    check("weekly 含交易记录节", r, contains="交易记录")

    # 写入假交易历史后再测 weekly
    trade_history_path = MEMORY_DIR / "trade_history.json"
    MEMORY_DIR.mkdir(exist_ok=True)
    trade_history_path.write_text(json.dumps([
        {"symbol": "BTC", "side": "long", "leverage": 3,
         "price": 94000, "timestamp": "2026-04-07T10:00:00Z"},
        {"symbol": "ETH", "side": "short", "leverage": 2,
         "price": 3500,  "timestamp": "2026-04-06T14:00:00Z"},
    ], ensure_ascii=False))
    r = sk.run(period="weekly")
    check("weekly 含交易记录", r, contains="BTC")

    # 报告文件已保存
    reports_dir = ROOT / "reports"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    check("daily 报告文件已存在",
          {"success": (reports_dir / f"daily_{today}.md").exists()})


# ════════════════════════════════════════════════════════════════════════════
# 8. CryptoNewsSkill — 新闻快讯
# ════════════════════════════════════════════════════════════════════════════

def test_crypto_news():
    print("\n[8] CryptoNewsSkill")
    from skills.crypto_news import CryptoNewsSkill
    sk = make_skill(CryptoNewsSkill)

    # latest
    r = sk.run(action="latest")
    check("latest 成功", r, contains="最新快讯")
    check("latest 含 Hyperliquid 条目", r, contains="Hyperliquid")

    # latest limit=3
    r = sk.run(action="latest", limit=3)
    check("latest limit=3 成功", r)

    # hl — HL 相关新闻（标题含 Hyperliquid）
    r = sk.run(action="hl")
    check("hl 新闻成功", r, contains="HL 相关快讯")

    # search — 命中
    r = sk.run(action="search", keyword="WIF")
    check("search WIF 成功", r, contains="WIF")

    # search — 未命中
    r = sk.run(action="search", keyword="TOTALLY_NOT_IN_NEWS")
    check("search 未命中", r, contains="未找到")

    # search — 空关键词
    r = sk.run(action="search", keyword="")
    check("search 空关键词报错", r, expect_success=False)

    # 情感分析：正面（ETF/批准）
    from skills.crypto_news import CryptoNewsSkill as CNS
    inst = make_skill(CNS)
    s = inst._sentiment("SEC 批准以太坊现货 ETF 上市")
    check("情感：正面", {"success": s == "positive"})

    # 情感分析：负面（黑客）
    s = inst._sentiment("某协议遭黑客攻击漏洞损失")
    check("情感：负面", {"success": s == "negative"})

    # 情感分析：中性
    s = inst._sentiment("比特币价格今日横盘")
    check("情感：中性", {"success": s == "neutral"})

    # 无缓存数据场景
    missing_sk = CryptoNewsSkill(
        data_dir=DATA_DIR / "_nonexistent_dir_",
        memory_dir=MEMORY_DIR,
        env={}
    )
    r = missing_sk.run(action="latest")
    check("无缓存返回错误", r, expect_success=False)


# ════════════════════════════════════════════════════════════════════════════
# 9. BacktestEngine — 回测引擎
# ════════════════════════════════════════════════════════════════════════════

def test_backtest():
    print("\n[9] BacktestEngine")
    from backtest.engine import BacktestEngine, FundingArbStrategy, BacktestResult, Trade

    # load_sample_data + run
    engine = BacktestEngine()
    engine.load_sample_data(n_periods=300)
    check("load_sample_data 加载成功",
          {"success": len(engine.data) == 300 * 3})  # 3 symbols × 300 periods

    result = engine.run(FundingArbStrategy(entry_threshold=0.0005))
    check("run 返回 BacktestResult", {"success": isinstance(result, BacktestResult)})
    check("trades 列表非空", {"success": len(result.trades) > 0})
    check("win_rate 在 0-1", {"success": 0 <= result.win_rate <= 1})
    check("max_drawdown >= 0", {"success": result.max_drawdown >= 0})
    check("total_funding >= 0", {"success": result.total_funding >= 0})
    check("total_fees >= 0", {"success": result.total_fees >= 0})

    # summary 格式
    summary = result.summary()
    check("summary 含回测结果", {"success": "回测结果" in summary})
    check("summary 含胜率", {"success": "胜率" in summary})
    check("summary 含夏普比率", {"success": "夏普比率" in summary})

    # 自定义策略参数（高阈值 → 少交易）
    result2 = engine.run(FundingArbStrategy(entry_threshold=0.005))
    check("高阈值策略交易次数更少",
          {"success": len(result2.trades) <= len(result.trades)})

    # 空数据 → 无交易
    engine3 = BacktestEngine()
    engine3.data = []
    try:
        engine3.run(FundingArbStrategy())
        check("空数据应抛 RuntimeError", {"success": False})
    except RuntimeError:
        check("空数据抛 RuntimeError ✓", {"success": True})

    # load_data — 不存在文件
    try:
        engine.load_data("nonexistent.json")
        check("load_data 不存在文件应抛 FileNotFoundError", {"success": False})
    except FileNotFoundError:
        check("load_data 不存在文件抛 FileNotFoundError ✓", {"success": True})

    # 加载真实模拟历史文件
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
    sample_history = [
        {"timestamp": "2026-01-01T00:00:00Z", "symbol": "BTC",
         "funding_8h": 0.0008, "mark_price": 95000.0, "open_interest": 1e8},
        {"timestamp": "2026-01-01T08:00:00Z", "symbol": "BTC",
         "funding_8h": 0.0002, "mark_price": 95500.0, "open_interest": 1e8},
        {"timestamp": "2026-01-01T16:00:00Z", "symbol": "BTC",
         "funding_8h": 0.00005, "mark_price": 96000.0, "open_interest": 1e8},
    ]
    json.dump(sample_history, tmp)
    tmp.close()

    engine4 = BacktestEngine()
    engine4.load_data(tmp.name)
    check("load_data 真实文件成功", {"success": len(engine4.data) == 3})
    result4 = engine4.run(FundingArbStrategy(entry_threshold=0.0005))
    check("真实文件回测运行完成", {"success": isinstance(result4, BacktestResult)})

    Path(tmp.name).unlink()

    # summary — 无交易时输出特殊提示
    empty_result = BacktestResult()
    s = empty_result.summary()
    check("无交易 summary 提示", {"success": "无交易" in s})


# ════════════════════════════════════════════════════════════════════════════
# 10. 边界条件 & 数据一致性
# ════════════════════════════════════════════════════════════════════════════

def test_edge_cases():
    print("\n[10] 边界条件 & 数据一致性")

    # FundingArbSkill：无市场数据时的错误处理
    from skills.funding_arb import FundingArbSkill
    no_data_sk = FundingArbSkill(
        data_dir=DATA_DIR / "_missing_",
        memory_dir=MEMORY_DIR,
        env={}
    )
    r = no_data_sk.run(action="scan")
    check("无市场数据 scan 报错", r, expect_success=False)

    r = no_data_sk.run(action="open", symbol="BTC", size_usd=100)
    check("无市场数据 open 报错", r, expect_success=False)

    # CryptoAlertSkill：无市场数据时返回空列表（不崩溃）
    from skills.crypto_alert import CryptoAlertSkill
    no_data_alert = CryptoAlertSkill(
        data_dir=DATA_DIR / "_missing_",
        memory_dir=MEMORY_DIR,
        env={}
    )
    r = no_data_alert.run(action="scan")
    check("无数据 scan 不崩溃", {"success": True})

    # HLMonitorSkill：无账户数据时报错
    from skills.hl_monitor import HLMonitorSkill
    no_acct_sk = HLMonitorSkill(
        data_dir=DATA_DIR / "_missing_",
        memory_dir=MEMORY_DIR,
        env={}
    )
    r = no_acct_sk.run(action="account")
    check("无账户数据 account 报错", r, expect_success=False)

    # HLGridSkill：ETH 当前价格不在区间
    from skills.hl_grid import HLGridSkill
    grid_sk = make_skill(HLGridSkill)
    r = grid_sk.run(action="create", args=["ETH", "4000", "5000", "5", "50"])
    check("ETH 价格低于区间下界报错", r, expect_success=False)

    # FundingArbSkill：pnl（等价 status）在无数据时不崩溃
    from skills.funding_arb import FundingArbSkill
    arb_sk = make_skill(FundingArbSkill)
    r = arb_sk.run(action="pnl")
    check("pnl 无数据不崩溃", {"success": True})


# ════════════════════════════════════════════════════════════════════════════
# 主入口
# ════════════════════════════════════════════════════════════════════════════

def run_all():
    print("=" * 60)
    print("  crypto-clawie 沙盒测试套件")
    print("=" * 60)

    MEMORY_DIR.mkdir(exist_ok=True)

    test_base_skill()
    test_crypto_data()
    test_hl_monitor()
    test_crypto_alert()
    test_funding_arb()
    test_hl_grid()
    test_crypto_report()
    test_crypto_news()
    test_backtest()
    test_edge_cases()

    # ── 汇总 ──────────────────────────────────────────────────────────────
    total   = len(results)
    passed  = sum(1 for _, ok in results if ok)
    failed  = total - passed

    print("\n" + "=" * 60)
    print(f"  结果：{passed}/{total} 通过  |  {failed} 失败")
    print("=" * 60)

    if failed:
        print("\n失败项：")
        for name, ok in results:
            if not ok:
                print(f"  ❌ {name}")
    else:
        print("\n  全部测试通过 ✅")

    return failed == 0


if __name__ == "__main__":
    success = run_all()
    sys.exit(0 if success else 1)
