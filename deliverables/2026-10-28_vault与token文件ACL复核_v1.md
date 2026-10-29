# `_vault` 与 token 文件 ACL 专项复核（只读）

- **时间**：2026-10-28 18:20–18:35
- **工单**：`WO-ACL-AUDIT-20261028`
- **范围**：`C:\项目\_vault`、`quant_backtest/.env.local`、`.mcp.local.json`、继承源 `C:\项目`
- **纪律**：**只读复核，未改任何 ACL、未删任何文件**；结论供安全专项决策

## 一、继承源：`C:\项目` 根 ACL

`icacls C:\项目`（所有子项默认继承）：

| 主体 | 权限 | 标志 |
|---|---|---|
| `Everyone` | 拒绝 DeleteSubdirectoriesAndFiles | (CI)(DENY)(DC) |
| `DESKTOP-ARF447U\t` | FullControl | (OI)(CI) |
| **`S-1-4-472564843-692282355`** | **Write, Delete, DeleteSubdirectoriesAndFiles, Synchronize** | (OI)(CI) |
| `BUILTIN\Administrators` | FullControl | (OI)(CI) |
| `NT AUTHORITY\SYSTEM` | FullControl | (OI)(CI) |
| **`BUILTIN\Users`** | **ReadAndExecute, Synchronize** | (OI)(CI)(RX) |
| **`Mandatory Label\Low Mandatory Level`** | NoWriteUp | (OI)(CI)(NW) |

- 未知 SID `S-1-4-472564843-692282355`：**无法翻译为本地账户名**（`SecurityIdentifier.Translate` 失败）；`S-1-4` 属 NT 非唯一权威，疑似沙箱/能力类 SID。
- 该 SID 的 **写/删/删子项** 权限会继承到 `_vault` 全部 token/密文文件。
- Low Mandatory Level 继承到 `_vault` 树；含义是低完整性进程可写（受 ACE 约束），需确认是否沙箱依赖。

## 二、关键对象现状

| 对象 | 当前 ACL 要点 | 风险判断 |
|---|---|---|
| `_vault`（根） | 继承 `Users:(RX)`、未知 SID `(W,D,DC)`、Low 标签 | 🟡 Users 可读根目录/归档；未知 SID 可写 |
| `_vault\secrets` | **无 Users**；未知 SID `(W,D,DC)`；Low 标签 | 🟢 Users 不可读；🟡 未知 SID 可写 |
| `_vault\secrets\mcp\tushare_mcp_token`（新 token 明文） | 用户 F、未知 SID W/D/DC、Admins/System F；**无 Users** | 🟢 Users 不可读；🟡 未知 SID 可写 |
| `_vault\secrets.enc\MANIFEST.json` | 用户/Admins/System F、未知 SID W/D/DC、**Users RX**、Low 标签 | 🟡 仅哈希清单，非明文；Users 可读 |
| `_vault\secrets_archive*` | 继承 Users RX | 🟡 归档目录 Users 可读（本次扫描未见完整旧/新 token） |
| `_vault\patches\...\.env.local.bak_*`（2 个） | 用户 F、未知 SID W/D/DC、Admins/System、**Users RX** | 🔴 **含旧 token 完整明文**（已外部失效），Users 可读 |
| `quant_backtest\.env.local`（新 token 明文） | 用户 F、未知 SID W/D/DC、Admins/System、**Users RX** | 🔴 **新 token 明文，本机所有用户可读** |
| `C:\项目\.mcp.local.json`（含新 token） | 显式：Admins/System/用户 F；Low 标签；无 Users | 🟢 基本收敛；Low 标签建议复核 |
| `老田的AI助手\.mcp.local.json`（含新 token） | 显式：Admins/System/用户 F；无 Users/Low | 🟢 收敛 |

> 补充扫描：对 `_vault` 下 4267 个文件全文匹配，**旧 token 完整值仅命中** 2 个 `.env.local.bak_*`（上述），**新 token 完整值仅命中** `_vault\secrets\mcp\tushare_mcp_token`（预期）；归档/回执/任务台账无新 token 全文。

## 三、结论

1. **最大问题不是加密区，而是继承链**：`C:\项目` 的 `BUILTIN\Users:(RX)` + 未知 SID 写/删 + Low 标签被继承到 `_vault` 与 `.env.local`；导致：
   - 新 token 明文的 `.env.local` 对本机 Users 可读；
   - 旧 token 明文的 2 个 `.bak` 对本机 Users 可读（旧值已失效，但仍是泄露残留）；
   - 未知 SID 对 token/密文有写/删除权限（来源待确认）。
2. `_vault\secrets` 本身已显式去 Users，方向正确；应把同样策略扩展到 `_vault` 根/归档/patches。
3. `.mcp.local.json` 的显式 ACL 已收敛（无 Users），但含 token 且 Low 标签需确认；建议后续把 rende 输出也纳入 vault 目录或 ACL 统一。

## 四、建议（按优先级，均未执行）

### P0：旧 token 明文备份与 `.env.local`

```powershell
# 1) 旧 token 备份（旧值已失效，仍建议从"Users 可读"位置移走/加密；不直接删）
#    C:\项目\_vault\patches\exec_20261025\r5_old_20261026_194521\.env.local.bak_20261026_195005
#    ...bak_20261026_233236
#    → 移入仅 owner/SYSTEM/Admins 可读的归档目录，或在确认无回滚需求后进回收站

# 2) .env.local 断继承 + 显式 ACL（示例，未执行）
icacls "C:\项目\老田的AI助手\quant_backtest\.env.local" /inheritance:r `
  /grant:r "DESKTOP-ARF447U\t:(F)" "BUILTIN\Administrators:(F)" "NT AUTHORITY\SYSTEM:(F)"
```

- 更彻底：Phase 2 让 TushareSync 从 DPAPI/credential_store 读 token，不再保留明文 `.env.local`。

### P1：`_vault` 根与归档收敛

```powershell
# 示例（未执行）：先断继承，再显式授权；保留子目录 secrets 现状
icacls "C:\项目\_vault" /inheritance:r /grant:r "DESKTOP-ARF447U\t:(OI)(CI)(F)" `
  "BUILTIN\Administrators:(OI)(CI)(F)" "NT AUTHORITY\SYSTEM:(OI)(CI)(F)"
# 归档/patches 同样处理；确认无 Users 继承后再恢复必要子项权限
```

### P1：未知 SID 与 Low 标签溯源（**不要盲删**）

- 用 `Get-Acl C:\项目`、`whoami /groups`（沙箱进程）对齐 `S-1-4-472564843-692282355`，确认是否 DSH/浏览器沙箱依赖；
- 若确认无依赖：从 `C:\项目` 根去掉该 SID 的 W/D/DC，再重继承；
- Low Mandatory Level 若为沙箱写权限而设，应**只作用于沙箱工作区**，不应罩住 `_vault`/凭据目录。

### P2：`.mcp.local.json` 与根 `Users:(RX)`

- `.mcp.local.json` 已无 Users；建议确认 Low 标签来源并考虑移到 vault 内渲染；
- `C:\项目` 根 `Users:(RX)` 对单机风险有限，但不应继承进 `_vault`；通过 P1 断继承解决。

## 五、变更后验收（建议）

```powershell
icacls "C:\项目\_vault" | Select-String "Users|S-1-4"
icacls "C:\项目\老田的AI助手\quant_backtest\.env.local" | Select-String "Users|S-1-4"
# 服务侧：TushareSync 23:30 仍 rc=0；8796 MCP 258 tools；render --check 明文命中=0
```

| 版本 | 日期 | 说明 |
|---|---|---|
| v1 | 2026-10-28 | 只读复核：继承链/未知 SID/旧 token 备份/`.env.local` Users RX；未改 ACL |
