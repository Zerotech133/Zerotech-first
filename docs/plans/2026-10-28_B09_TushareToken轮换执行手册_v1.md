# B-09 Tushare token 轮换 · 执行手册 v1（待 token 到即执行）
> STATUS: unspecified（owner: TBD；2026-11-07 前补状态或标 superseded）



> 2026-10-28 · dry-run 已通过（前置齐备）· 执行窗口：10–15 分钟
> 依据：`tools/secrets/rotate_tushare_token.py`（R1 引导）+ SECVAULT L1
> **token 不要贴到对话/日志**；放受限文件或当面输入。

## 一、现状（dry-run 结果）

```
python tools/secrets/rotate_tushare_token.py --dry-run
→ vault_dir = C:\项目\_vault\secrets        (exists=True)
→ vault.py / render_mcp.py / age.exe        (exists=True)
→ OK: 前置齐备，可执行正式轮换（需先在 tushare 控制台重置 token）
```

## 二、执行步骤（token 到手后）

1. **备份旧密文**（回滚用）：
   ```powershell
   $ts = Get-Date -Format yyyyMMdd_HHmmss
   Copy-Item "C:\项目\_vault\secrets.enc\mcp\tushare_mcp_token.age" "C:\项目\_vault\backups\tushare_mcp_token.$ts.age" -Force
   ```
2. **Tushare 控制台重置 token**（新 token 出现后，旧 token 立即失效）；
3. **运行轮换引导**（两种方式任选）：
   ```powershell
   # A. 交互：两次 getpass 输入新 token（不回显）
   C:\项目\py312\python.exe tools\secrets\rotate_tushare_token.py
   # B. 受限文件（ACL 仅当前用户；文件不自动删除）
   C:\项目\py312\python.exe tools\secrets\rotate_tushare_token.py --token-file C:\Temp\tushare_new.token
   ```
   脚本自动执行：写 target → `vault encrypt --recursive` → `vault verify --json` → `render_mcp` → `render_mcp --check`。
4. **更新用户级环境变量**（从 vault 解密注入，不回显、不落 shell 历史）：
   ```powershell
   $tok = & C:\项目\py312\python.exe -c "import subprocess,sys;print(subprocess.run([sys.executable,'tools/secrets/vault.py','decrypt','mcp/tushare_mcp_token'],capture_output=True,text=True).stdout.strip())"
   [Environment]::SetEnvironmentVariable("TUSHARE_MCP_TOKEN", $tok, "User")
   Remove-Variable tok
   ```
5. **重启 8796**（MCP 配置/凭据换血）：
   ```powershell
   New-Item -ItemType File -Path C:\Temp\LEGION_8796_RESTART.flag -Force
   Start-ScheduledTask -TaskName LegionOps
   ```
6. **验证**（4 项）：
   - `GET http://127.0.0.1:8796/api/zhaoyun/mcp` → tushare-http enabled=true，工具数 258；
   - 一次 tushare 只读 smoke（如 `stock_basic` 返回行）；
   - `python tools/secrets/vault.py verify --json` → 全部 ok；
   - `python tools/secrets/render_mcp.py --check` → ok；
7. **回滚**（新 token 或配置有问题时）：
   - 恢复密文：`Copy-Item C:\项目\_vault\backups\tushare_mcp_token.<ts>.age C:\项目\_vault\secrets.enc\mcp\tushare_mcp_token.age -Force`；
   - 重新 `vault verify` + `render_mcp` + 重启 8796；
   - **注意**：Tushare 重置后旧 token 已失效——若需旧 token 恢复可用，回滚前需在控制台再生成一个 token 并重复步骤 2–6。

## 三、五要素摘要

| 要素 | 内容 |
|---|---|
| 命令全文 | 见第二节 |
| 影响面 | MCP token/密文/8796 重启；不涉及 8734 入口与业务数据 |
| 不可逆点 | Tushare 旧 token 立即失效（重置即不可逆）；密文备份可回滚但旧 token 不可复活 |
| 替代方案 | 不轮换（继续用旧 token 直至到期/泄露）；或仅先换密文不重启（不推荐） |
| 回滚 | 恢复密文备份 + 重渲染 + 重启；必要时重新生成 token |
| 验收 | 步骤 6 四项全过 |

## 四、风险提示

- token 泄露路径：命令行历史/日志/聊天——本手册全部用环境变量注入或受限文件，**禁止** `echo`/`setx` 明文；
- 轮换窗口内 MCP 工具短暂不可用（重启 8796）；
- 轮换后 `servers.json` 不应包含明文 token（由 render_mcp 生成引用）。
