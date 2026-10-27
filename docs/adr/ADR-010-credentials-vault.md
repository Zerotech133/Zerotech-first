# ADR-010：凭据集中到 `_vault`，密文副本 + token 仅环境注入

- **状态**：Accepted（L1 已执行 2026-10-27；Phase 2 消费方切换待做）
- **关联**：P4/P6；`tools/secrets/`；`docs/ops/2026-10-27_凭据收编_SOPS_age与TushareMCP_token轮换_操作单.md`

## 背景

历史上 token/key 分散在 `.mcp.json`、`_vault/secrets`、环境变量、备份文件中，存在明文暴露面。

## 决定

1. 凭据唯一存放地：`C:\项目\_vault\secrets`（明文层，Phase 1）+ `secrets.enc`（age 密文副本 + MANIFEST）；
2. 凭据不进 git、不进聊天/日志/命令历史；文档一律 `<masked>`；
3. 新增/轮换：写入明文 → `vault.py encrypt --recursive` → `verify --json` → `render_mcp.py` → 消费方/服务重启（L2）；
4. MCP server 只允许 `token_env` 引用，禁止 URL/配置内明文；旧值按已暴露处理必须轮换；
5. age 私钥必须离线备份（丢失=密文不可解）。

## 后果

- 正面：暴露面收敛、可校验（32/32 verify）、可轮换。
- 代价：Phase 2 前生产代码仍直读明文（未真正消除）；轮换需外部平台操作。
- 不做：不引入 Vault/KMS 服务（单机 SOPS/age 足够）。
