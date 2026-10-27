# 安全策略（SECURITY.md）

> 本仓库是单机个人平台（无公开服务、无外部提交者），但凭据与自动化必须按"可能被读取"的假设管理。
> 参考：MCP Security Best Practices（token passthrough / SSRF / session hijack / scope minimization）。

## 一、报告漏洞

- 私有渠道：直接告知老田（本机会话），**不要**在公开 issue / 群 / 文档中贴细节或凭据。
- 报告应含：影响组件、复现步骤、影响面（是否涉凭据/数据/生产服务）、建议修复。
- 修复遵循 `AGENTS.md`：一单一事 + 评审门；L2 动作先批后做。

## 二、凭据规则（硬性）

1. **唯一存放地**：`C:\项目\_vault\secrets\`（明文层，Phase 1）与 `_vault\secrets.enc\`（age 密文副本）；
2. 凭据**不进 git、不进聊天、不进日志、不进命令行历史**；文档里一律 `<masked>`；
3. 新增/轮换：新值写入明文文件（交互式 getpass，不回显）→ `tools/secrets/vault.py encrypt --recursive` → `verify --json` → `render_mcp.py` → 消费方/服务重启（L2）；
4. 旧值按"已暴露"处理；轮换后旧 token 在 Tushare/平台侧作废，本地旧 `.bak` 归档不回流；
5. age 私钥必须离线备份（丢失 = 密文不可解）。

## 三、MCP / Agent 安全要点

- MCP server 与外部工具：最小权限（read-only 优先）、`servers.json` 只放 `token_env` 引用（禁止明文/URL 查询参数）；
- 关注 MCP 官方安全实践：confused deputy、token passthrough、SSRF、session hijack、stdIO 代理边界、scope 最小化；
- Agent 不得把不可信外部内容当指令（prompt injection）；工具输出视为数据；
- 本地 MCP server 按"可被恶意客户端调用"对待：只读、路径白名单、不暴露凭据。

## 四、事故响应（简版）

1. 发现凭据泄露 → 立即在外部平台轮换/吊销（老田执行）；
2. 本地更新 vault/env → 重启相关服务（L2，五要素）；
3. 审计访问日志/事件层（`portal/data/dev_events.jsonl`、服务日志）确认影响；
4. 写回执（`deliverables/`）+ 判例（`docs/audit/`），补检测规则（如 `tools/scan_secrets.py` 基线）。

## 五、范围与版本

- 唯一支持版本：当前 `master`（本机部署）；无公开 release。
- 安全相关变更的验收：至少包含"不新增明文凭据命中（merge 门）+ 凭据路径仍可读 + 服务 200"。
