module.exports = {
  apps: [
    // ── OpenClaw Gateway ──────────────────────────────────────────────────────
    {
      name:         "openclaw-gateway",
      script:       "openclaw",
      args:         "gateway",
      exec_mode:    "fork",
      cwd:          process.env.HOME,
      autorestart:  true,
      restart_delay: 3000,
      max_restarts: 10,
    },

    // ── crypto-clawie 调度器（数据抓取 + 自动预警）────────────────────────────
    {
      name:         "clawie-scheduler",
      script:       "scheduler.py",
      interpreter:  "./venv/bin/python3",
      exec_mode:    "fork",
      cwd:          "/root/crypto-clawie",
      autorestart:  true,
      restart_delay: 3000,
      max_restarts: 10,
      env: { PYTHONUNBUFFERED: "1" },
    },

    // ── crypto-clawie Bot（Telegram 指令响应）────────────────────────────────
    {
      name:         "clawie-bot",
      script:       "bot.py",
      interpreter:  "./venv/bin/python3",
      exec_mode:    "fork",
      cwd:          "/root/crypto-clawie",
      autorestart:  true,
      restart_delay: 3000,
      max_restarts: 10,
      env: { PYTHONUNBUFFERED: "1" },
    },
  ],
};
