#!/bin/bash
# setup.sh — crypto-clawie VPS 一键部署脚本
# 用法：bash setup.sh

set -e
echo "======================================"
echo "  crypto-clawie 部署脚本"
echo "======================================"

INSTALL_DIR="$HOME/crypto-clawie"

# ── 1. 删除旧 Agent ────────────────────────────────────────────────────────
echo ""
echo "[1/6] 清理旧 Agent..."
pm2 delete crypto-agent 2>/dev/null || true
pm2 delete clawie-scheduler 2>/dev/null || true
rm -rf "$HOME/crypto-agent"
echo "✓ 旧 Agent 已删除"

# ── 2. 克隆新项目 ──────────────────────────────────────────────────────────
echo ""
echo "[2/6] 克隆 crypto-clawie..."
if [ -d "$INSTALL_DIR" ]; then
  echo "目录已存在，执行 git pull..."
  cd "$INSTALL_DIR" && git pull
else
  # 替换为你的 GitHub 仓库地址
  git clone https://github.com/Zachppt/crypto-clawie.git "$INSTALL_DIR"
fi
cd "$INSTALL_DIR"
echo "✓ 代码已就绪"

# ── 3. Python 虚拟环境 ────────────────────────────────────────────────────
echo ""
echo "[3/6] 安装 Python 依赖..."
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
if [ ! -f .env ]; then
  cp .env.example .env
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
else
  echo "✓ .env 已存在，跳过"
fi

# ── 6. 更新 OpenClaw 配置 ─────────────────────────────────────────────────
echo ""
echo "[6/6] 更新 OpenClaw 配置..."
OPENCLAW_JSON="$HOME/.openclaw/openclaw.json"
if [ -f "$OPENCLAW_JSON" ]; then
  # 备份旧配置
  cp "$OPENCLAW_JSON" "${OPENCLAW_JSON}.bak"
  # 用 python 更新 workspace 路径
  python3 - <<PYEOF
import json
with open("$OPENCLAW_JSON") as f:
    cfg = json.load(f)
cfg["workspace"] = "$INSTALL_DIR"
with open("$OPENCLAW_JSON", "w") as f:
    json.dump(cfg, f, indent=2)
print("✓ openclaw.json workspace 已更新为 $INSTALL_DIR")
PYEOF
else
  echo "⚠️  未找到 openclaw.json，请手动将 workspace 指向 $INSTALL_DIR"
fi

# ── 启动 ───────────────────────────────────────────────────────────────────
echo ""
echo "======================================"
echo "  启动服务"
echo "======================================"

source venv/bin/activate

# 测试抓取一次
echo "测试数据抓取..."
python fetcher.py --task market_snapshot && echo "✓ 数据抓取正常"

# 启动调度器
pm2 start scheduler.py \
  --name clawie-scheduler \
  --interpreter "$INSTALL_DIR/venv/bin/python3" \
  --cwd "$INSTALL_DIR"

pm2 save
echo "✓ clawie-scheduler 已启动"

echo ""
echo "======================================"
echo "  部署完成！"
echo "======================================"
echo ""
echo "查看日志：    pm2 logs clawie-scheduler"
echo "查看状态：    pm2 status"
echo "手动抓取：    cd $INSTALL_DIR && source venv/bin/activate && python fetcher.py"
echo "重启调度器：  pm2 restart clawie-scheduler"
echo ""
