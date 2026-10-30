# X5 Tushare MCP token 轮换 · L2 五要素批准单 v1（2026-10-30 · 关羽）

> 类型：**凭据轮换（L2，含 8796 服务重启）**
> 现状：**已关闭（2026-10-31）——老田决定不轮换、接受继续使用风险**（该 token 已在别处使用，不能轻易更改）。本单保留为执行预案+风险接受登记；未执行任何 token/vault 变更。
> 依据：`docs/plans/2026-10-28_B09_TushareToken轮换执行手册_v1.md`、`docs/ops/2026-10-27_凭据收编_SOPS_age与TushareMCP_token轮换_操作单.md` §3。

## 一、命令全文（批准后照此执行）

```powershell
# 0) 备份旧密文（回滚用）
$ts = Get-Date -Format yyyyMMdd_HHmmss
Copy-Item "C:\项目\_vault\secrets.enc\mcp\tushare_mcp_token.age" `
          "C:\项目\_vault\backups\tushare_mcp_token.$ts.age" -Force

# 1) 老田操作：Tushare 控制台 → 用户中心 → 接口 TOKEN → 重置/生成（旧 token 立即失效）
#    新 token 不贴对话/日志；二选一交给脚本：
#    A. 交互（两次 getpass，不回显、不进历史）：
C:\项目\py312\python.exe -X utf8 tools\secrets\rotate_tushare_token.py
#    B. 受限文件（ACL 仅当前用户；用后删除）：
#    C:\项目\py312\python.exe tools\secrets\rotate_tushare_token.py --token-file C:\Temp\tushare_new.token
#    脚本自动：写 target → vault encrypt --recursive → vault verify --json → render_mcp → render_mcp --check

# 2) 用户级环境变量（从 vault 解密注入，不回显）
$tok = & C:\项目\py312\python.exe -c "import subprocess,sys;print(subprocess.run([sys.executable,'tools/secrets/vault.py','decrypt','mcp/tushare_mcp_token'],capture_output=True,text=True).stdout.strip())"
[Environment]::SetEnvironmentVariable("TUSHARE_MCP_TOKEN", $tok, "User")
Remove-Variable tok

# 3) 8796 换血重启（单独 L2 动作，同上批准）
New-Item -ItemType File -Path C:\Temp\LEGION_8796_RESTART.flag -Force
Start-ScheduledTask -TaskName LegionOps

# 4) 一键只读验收（新增）
C:\项目\py312\python.exe -X utf8 tools\secrets\post_rotation_check.py --json
```

## 二、影响面

- `_vault/secrets.enc/mcp/tushare_mcp_token.age`（密文更新）、`_vault/secrets/mcp/tushare_mcp_token`（明文 target，仅本地）、`.mcp.json` 重渲染；
- 用户级 `TUSHARE_MCP_TOKEN` 环境变量；
- **8796（军团网关）重启**：重启期间该服务不可用（约 10–30 秒）；不影响 8734 门户与业务数据；
- 不触碰：数据库、数据根、catalog、计划任务（除上述 LegionOps 触发）。

## 三、不可逆点

- **Tushare 控制台重置后旧 token 立即失效且不可恢复**（唯一不可逆点）；
- 本地密文/配置可回滚，但旧 token 无法复活（需重新生成再走一遍）。

## 四、替代方案与已尝试

| 方案 | 取舍 |
|---|---|
| 不轮换继续用旧 token | 该 token 曾以明文存在于可被 Agent 读取的配置，按**已暴露**处理；继续用=风险自担，不推荐 |
| 只换密文不重启 8796 | 新 token 不生效（客户端缓存旧值），等于没换；不采纳 |
| 本方案（控制台重置 + 引导脚本 + 一键验收 + 重启） | 一次 10–15 分钟窗口，脚本已 dry-run 通过；推荐 |
| 已尝试 | `rotate_tushare_token.py --dry-run` → `OK: 前置齐备`；`post_rotation_check.py` 试跑 → vault/render/token 三项 ✓（8796 未起 skip） |

## 五、回滚预案

1. 新 token 验证失败 → 恢复密文 `_vault/backups/tushare_mcp_token.<ts>.age` → `vault verify` → 重新 `render_mcp` → 重启 8796；
2. 仍失败 → Tushare 控制台**再生成一个**新 token 重走 §一（旧 token 已失效，不能靠本地恢复复活）；
3. 8796 重启失败 → 按 `LegionOps` 任务日志排查；端口 8796 健康检查（`/api/zhaoyun/mcp`）确认。

## 六、执行窗口与责任

| 项 | 值 |
|---|---|
| 建议窗口 | **非交易时段**（当前 03:xx 可执行；或收盘后 ≥15 分钟） |
| 用时 | 10–15 分钟 |
| 老田操作 | 控制台重置 token；在安全环境运行引导脚本（getpass 不回显）；批准 8796 重启 |
| 代理操作 | 备份密文、验收、留痕（回执） |
| 验收 | `tools/secrets/post_rotation_check.py --json` → `ok=true`（vault/render/token 全 ✓；8796 起则 enabled/工具数可见） |

## 七、当前状态证据（2026-10-30 03:0x）

- dry-run：`前置齐备，可执行正式轮换（需先在 tushare 控制台重置 token）`；旧值目标存在、vault/render/age 工具齐；
- 只读验收脚本试跑：`vault_verify ✓ / render_mcp_check ✓ / token_in_vault ✓（len=56 sha8=0f…masked）/ mcp_8796 skip`；
- 当前 token 仍是旧值（sha8 与历史暴露值前缀哈希不同，但长度/来源未变）→ **待老田生成新 token 后执行**.

## 八、批准栏（老田填写）

- [ ] 批准执行 §一 全部步骤（含 8796 重启）
- 批准人：________  日期：________  窗口：________


---

## 九、决策记录（2026-10-31 00:1x · 老田）

- **决定：不执行轮换，继续使用现有 Tushare MCP token，并接受该风险。**
- 理由：该 token 已在别处使用，重置会连带影响其他系统，不能轻易更改。
- 关羽已执行（仅安全前置，**未动 token/vault**）：旧密文备份（`C:\项目\_vaultackups	ushare_mcp_token.20261031_000421.age`）、
  `rotate_tushare_token.py --dry-run` 通过、`post_rotation_check.py` 基线；随后按指示**停止**，未执行控制台重置/写 target/注入环境变量/重启 8796。
- 残余风险与补偿控制见：`deliverables/2026-10-30_X5_token轮换_风险接受与残余风险登记_v1.md`。
- 复评日期：**2026-11-30**（已排提醒，与服务账号轮换日同窗复核）。
