# zhice-os-v2 Windows 部署包（2026-09-20）

**从这里开始**：完整部署步骤见 ➜ **`docs\DEPLOY-WINDOWS.md`**（从装软件到验证的完整说明，约 30–60 分钟完成部署）。

快速三句话：

1. 装 Python 3.12 + PostgreSQL 16 + Node 20，按文档 §2–§3 建库并恢复 `database\prod_data.dump`（业务数据：账号/策略/持仓/成交全量）；
2. 按 §4–§6 装依赖、启动 API + 调度器 + 前端，浏览器开 **http://localhost:5175**（用 localhost，不是 127.0.0.1），沿用源系统账号登录；
3. 按 §7 跑 `scripts\import_market_history.py` 导入历史行情（可选但建议，分钟线 888 万行约 10–30 分钟）。

⚠️ `code\.env.local` 含真实凭据（Telegram/WxPusher/DataCenter），勿外发勿入库。
