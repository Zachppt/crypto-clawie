#!/bin/bash
# setup.sh — crypto-clawie VPS 一键部署/更新脚本
# 首次部署：bash <(curl -s https://raw.githubusercontent.com/Zachppt/crypto-clawie/main/setup.sh)
# 后续更新：cd ~/crypto-clawie && bash setup.sh

set -e

INSTALL_DIR="$HOME/crypto-clawie"
SCHEDULER_PID="$INSTALL_DIR/scheduler.pid"
BOT_PID="$INSTALL_DIR/bot.pid"
LOG_FILE="$INSTALL_DIR/logs/scheduler.log"
BOT_LOG="$INSTALL_DIR/logs/bot.log"
IS_UPDATE=false

echo "======================================"
echo "  crypto-clawie 部署脚本"
echo "======================================"

# ── 判断首次部署还是更新 ───────────────────────────────────────────────────
if [ -f "$INSTALL_DIR/.env" ]; then
  IS_UPDATE=true
  echo ""
  echo "  检测到已有安装，进入更新模式"
fi

# ── 1. 停止旧进程 ──────────────────────────────────────────────────────────
echo ""
echo "[1/6] 停止旧进程..."
for pid_file in "$SCHEDULER_PID" "$BOT_PID"; do
  if [ -f "$pid_file" ]; then
    OLD_PID=$(cat "$pid_file")
    kill "$OLD_PID" 2>/dev/null && echo "✓ 旧进程 ($OLD_PID) 已停止" || echo "  旧进程不存在，跳过"
    rm -f "$pid_file"
  fi
done
echo "✓ 完成"

# ── 2. 拉取代码 ───────────────────────────────────────────────────────────
echo ""
echo "[2/6] 拉取最新代码..."
if [ -d "$INSTALL_DIR" ]; then
  cd "$INSTALL_DIR"
  git pull --ff-only
else
  git clone https://github.com/Zachppt/crypto-clawie.git "$INSTALL_DIR"
  cd "$INSTALL_DIR"
fi
echo "✓ 代码已就绪"

# ── 3. Python 依赖 ────────────────────────────────────────────────────────
echo ""
echo "[3/6] 安装/更新 Python 依赖..."
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "✓ 依赖安装完成"

# ── 4. 创建目录 ────────────────────────────────────────────────────────────
echo ""
echo "[4/6] 创建数据目录..."
mkdir -p data logs reports memory
echo "✓ 目录已创建"

# ── 5. 环境变量 ────────────────────────────────────────────────────────────
echo ""
echo "[5/6] 配置环境变量..."
if [ -f .env ]; then
  echo "✓ .env 已存在，保留用户配置，跳过"
else
  cat > .env << 'ENVEOF'
# ============================================================
# crypto-clawie 环境变量配置
# 切勿将此文件提交到 Git！
# ============================================================

# ── Hyperliquid（必填）───────────────────────────────────────
HL_PRIVATE_KEY=0x               # EVM 私钥
HL_WALLET_ADDRESS=0x            # 对应钱包地址
HL_USE_TESTNET=false            # true = 先用测试网验证
HL_DEFAULT_LEVERAGE=3           # 默认杠杆（1–50）
HL_DEFAULT_MARGIN_MODE=cross    # cross | isolated
HL_FUNDING_ALERT_THRESHOLD=0.0005
HL_LIQ_ALERT_THRESHOLD=0.15

# ── Telegram（必填）──────────────────────────────────────────
TELEGRAM_BOT_TOKEN=             # BotFather 获取
TELEGRAM_CHAT_ID=               # 你的 Chat ID
TELEGRAM_ALERT_CHAT_ID=        # 预警频道（可选，默认同上）

# ── 新闻 ──────────────────────────────────────────────────────
BLOCKBEATS_API_KEY=             # BlockBeats API Key（推荐）

# ── 调度器 ───────────────────────────────────────────────────
FETCH_INTERVAL_MIN=5
NEWS_INTERVAL_MIN=15
DAILY_REPORT_HOUR=8             # CST 小时数

# ── 安全 ─────────────────────────────────────────────────────
AUTONOMOUS_MODE=false           # true = 允许自动执行交易（谨慎！）
MAX_POSITION_SIZE_USD=500       # 单笔最大仓位（USD），建议从小值起步
MAX_DAILY_LOSS_PCT=5            # 每日亏损熔断阈值（账户净值百分比）
ENVEOF

  chmod 600 .env

  echo ""
  echo "⚠️  请编辑 .env 文件，填入以下必填项："
  echo "    HL_PRIVATE_KEY     — Hyperliquid 私钥"
  echo "    HL_WALLET_ADDRESS  — 钱包地址"
  echo "    TELEGRAM_BOT_TOKEN — Telegram Bot Token"
  echo "    TELEGRAM_CHAT_ID   — 你的 Chat ID"
  echo ""
  echo "    编辑命令：nano $INSTALL_DIR/.env"
  echo ""
  read -p "编辑完成后按 Enter 继续..." _
fi

# ── 6. 更新 OpenClaw 配置（若已安装）────────────────────────────────────────
# OpenClaw 是与本项目配套的 AI 分析助手，若未使用可忽略此步骤。
# 此步骤仅更新 OpenClaw 工作目录指向，不影响 bot/scheduler 运行。
echo ""
echo "[6/6] 更新 OpenClaw 配置（未安装时自动跳过）..."
OPENCLAW_JSON="$HOME/.openclaw/openclaw.json"
if [ -f "$OPENCLAW_JSON" ]; then
  cp "$OPENCLAW_JSON" "${OPENCLAW_JSON}.bak"
  python3 - <<PYEOF
import json
with open("$OPENCLAW_JSON") as f:
    cfg = json.load(f)
cfg["workspace"] = "$INSTALL_DIR"
cfg.setdefault("agents", {}).setdefault("defaults", {})["workspace"] = "$INSTALL_DIR"
with open("$OPENCLAW_JSON", "w") as f:
    json.dump(cfg, f, indent=2)
print("✓ openclaw.json workspace 已更新（顶层 + agents.defaults）")
PYEOF
else
  echo "⚠️  未找到 openclaw.json，请手动将 workspace 指向 $INSTALL_DIR"
fi

# ── 启动服务 ──────────────────────────────────────────────────────────────
echo ""
echo "======================================"
echo "  启动服务"
echo "======================================"

source venv/bin/activate

# 测试数据抓取
echo "测试数据抓取..."
python fetcher.py --task market_snapshot && echo "✓ 数据抓取正常"

# 后台启动调度器
nohup python scheduler.py >> "$LOG_FILE" 2>&1 &
echo $! > "$SCHEDULER_PID"
echo "✓ clawie-scheduler 已启动 (PID: $(cat $SCHEDULER_PID))"

# 后台启动 Telegram Bot
nohup python bot.py >> "$BOT_LOG" 2>&1 &
echo $! > "$BOT_PID"
echo "✓ clawie-bot 已启动 (PID: $(cat $BOT_PID))"

echo ""
echo "======================================"
if [ "$IS_UPDATE" = true ]; then
  echo "  更新完成！"
else
  echo "  部署完成！"
fi
echo "======================================"
echo ""
echo "查看调度器日志：  tail -f $LOG_FILE"
echo "查看 Bot 日志：   tail -f $BOT_LOG"
echo "停止所有服务：    kill \$(cat $SCHEDULER_PID) \$(cat $BOT_PID)"
echo ""

