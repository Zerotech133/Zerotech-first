# X5 · Tushare MCP token 轮换 + 8796 重启 · 五要素执行包 v3

- **创建**：2026-10-28 11:05｜**状态**：**待新 token + 老田批准 L2**；本包未执行
- **取代**：`2026-10-27_X5_Tushare_token轮换_五要素执行包_v2.md` 的 8796 重启段（v2 依据旧 legion_command 版本；当前服务代码已更新）
- **触发**：Tushare MCP/API token 旧值（sha8 `05240a8e`）已按泄露处理，必须外部重置

---

## 一、现状证据（2026-10-28 10:55–11:05 实测）

| 项 | 值 |
|---|---|
| 8796 进程 | pid **26944**（python.exe，`legion_command/serve.py`；代码 mtime 2026-10-28 07:07） |
| 8796 健康 | `GET /v1/health` 200 `{"ok": true, "service": "dsh-llm-gateway"}` |
| MCP 状态 | `GET /api/zhaoyun/mcp` → ok=true，4 servers；`tushare-http` ok=true **258 tools** |
| 重启入口 | HKCU Run `LegionCommandConsole` + 计划任务 **LegionOps**（Ready）→ `C:\aios\ops\start_legion_ops.ps1` |
| 重启 sentinel | `C:\Temp\LEGION_8796_RESTART.flag`（脚本消费后删除，杀 8796 再拉起） |
| 明文 target | `C:\项目\_vault\secrets\mcp\tushare_mcp_token` → **不存在** |
| User env | `TUSHARE_MCP_TOKEN` sha8=`05240a8e`（旧值） |
| 平台保险箱 | `work/credentials/tushare.dpapi` → sha8=`05240a8e`、**known_leaked=true**（credential_store 已加固） |
| 冻结回执 | DATA1 仍缺、harness ahead=0 → 与 X5 无关，但同属当前闸门 |

> 当前服务标识是 `dsh-llm-gateway`（不是旧包的 legion-command）；验收按此为准。

---

## 二、五要素

### 1）命令全文（分两段：老田外部动作 + 本地执行）

**A. 老田侧（token 不经过对话/不落日志）**

```text
1) Tushare 控制台 → Token 管理 → 重置/生成新 token（旧值按平台策略失效）
2) 把新 token 单行写入：
   C:\项目\_vault\secrets\_inbox\tushare_token.txt
3) 回话：「token 已写入 + 批准 X5-8796 重启」
```

**B. 本地执行（老田回话后，DSH-Lead 执行；L2 重启只做一次）**

```powershell
$py = 'C:\Program Files\Python311\python.exe'
Set-Location C:\项目\老田的AI助手

# B0 执行前基线（不回显 token）
& $py tools/credential_store.py list
& $py tools/secrets/vault.py verify --json
& $py tools/secrets/render_mcp.py --check
$oldPid = (Get-NetTCPConnection -LocalPort 8796 -State Listen | Select-Object -First 1).OwningProcess
"old_pid=$oldPid"

# B1 校验 inbox 新 token 指纹（只算哈希，不打印）
& $py -c "import hashlib,pathlib; p=pathlib.Path(r'C:\项目\_vault\secrets\_inbox\tushare_token.txt'); t=p.read_text(encoding='utf-8').strip().splitlines()[0]; h=hashlib.sha256(t.encode()).hexdigest()[:8]; print('len=%d sha8=%s old=%s' % (len(t),h,h=='05240a8e'))"
# 必须：len>=20 且 old=False；否则停止并回报

# B2 轮换（写 target → vault encrypt → verify → render → render --check）
& $py tools/secrets/rotate_tushare_token.py --token-file C:\项目\_vault\secrets\_inbox\tushare_token.txt

# B3 凭据库元数据刷新 + 更新 User 环境变量（不打印值）
& $py tools/credential_store.py refresh tushare
$tok = (Get-Content 'C:\项目\_vault\secrets\mcp\tushare_mcp_token' -Raw -Encoding utf8).Trim()
[Environment]::SetEnvironmentVariable('TUSHARE_MCP_TOKEN', $tok, 'User')
$env:TUSHARE_MCP_TOKEN = $tok

# B4 重启 8796（L2，一次性 sentinel；LegionOps 任务只杀 8796 再拉起）
New-Item -ItemType File -Path 'C:\Temp\LEGION_8796_RESTART.flag' -Force | Out-Null
schtasks /run /tn "LegionOps"
Start-Sleep -Seconds 5
for ($i=0; $i -lt 30; $i++) {
  try { $r=Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:8796/v1/health' -TimeoutSec 5; if ($r.StatusCode -eq 200) { break } } catch {}
  Start-Sleep -Seconds 2
}

# B5 验收（token 不回显）
$newPid = (Get-NetTCPConnection -LocalPort 8796 -State Listen | Select-Object -First 1).OwningProcess
"new_pid=$newPid"
Get-Process -Id $newPid | Select-Object Id,StartTime
(Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:8796/v1/health').Content
$j = (Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:8796/api/zhaoyun/mcp' -TimeoutSec 15).Content | ConvertFrom-Json
$j.servers | Select-Object name,ok,@{n='ntools';e={ if($_.tools){@($_.tools).Count}else{0} }} | Format-Table -AutoSize
& $py tools/secrets/vault.py verify --json
& $py tools/secrets/render_mcp.py --check
& $py tools/credential_store.py list | Select-String tushare

# B6 清理旧明文备份（旧 token 已作废；归档不删除）
$arc='C:\项目\_archive\2026-10-28_x5_token_bak'
New-Item -ItemType Directory -Force $arc | Out-Null
Move-Item 'C:\项目\老田的AI助手\.mcp.json.bak-20261027' $arc -ErrorAction SilentlyContinue
Move-Item 'C:\项目\.mcp.json.bak-20261027' $arc -ErrorAction SilentlyContinue
```

### 2）影响面

- 仅 **8796（legion_command / 赵云 MCP 网关）**：重启窗口 **5–20 秒**；期间 `/api/zhaoyun*`、`/v1/health`、本地 MCP 工具短暂不可用。
- **不影响**：8091/8734/8795、飞书 bot、数据根、Ollama、其它服务。
- token 生效范围：`tushare-http`（258 tools）重新鉴权；旧 token 按 Tushare 策略失效。
- 旧 `LegionOps` 脚本同时保活 8093/8096（幂等，不会重复起）。

### 3）不可逆点

- **旧 token 一经 Tushare 控制台重置即不可恢复**（外部系统状态）。
- 本地 env/vault/credential_store 新值覆盖旧值；旧密文不可逆，但明文源在 Phase 2 前保留。
- 8796 重启本身可逆（可再启），无数据变更。

### 4）替代方案与已尝试

| 方案 | 说明 | 结论 |
|---|---|---|
| **本包（轮换 + 重启 8796）** | 立即消除暴露面 | 推荐 |
| 只改 vault/env、不重启 | 8796 仍用旧 env，新 token 不生效；旧 token 已失效 → MCP 不可用 | 不可行 |
| 暂缓轮换 | 旧 token 继续暴露 | 风险自留，不推荐 |
| 只停用 tushare-http | `servers.json enabled=false` + 重启 8796 | 仅轮换失败时保底 |

已尝试：L1 收编（密文副本/占位化/render 工具）；credential_store 加固（旧指纹拒写）；当前 MCP 258 tools 实测可用（旧 token 仍在用）。

### 5）回滚预案

```powershell
# 若 B5 验收失败：
# 1) 老田在 Tushare 控制台重新生成 token（旧 token 已失效，无本机回滚）
# 2) 重写 inbox → 重跑 B2/B3 → 再 B4 重启
# 3) 保底：停用 tushare-http（servers.json enabled=false；L2 重启 8796），保留 filesystem/git/zhaoyun-read
# 4) 本地 env 无旧值可回（旧 token 已外部失效）；不得把 known_leaked 旧值重新注入
& $py tools/secrets/vault.py verify --json   # 回滚后仍须 ok=true
```

- sentinel 一次性；脚本失败留日志 `C:\Temp\legion_ops_restart.log`，不阻塞 8091/8734。
- 若新 token 测活失败，**不要**把旧 token 写回（credential_store 会拒绝，`--force` 也不应使用）。

---

## 三、验收判据（B5）

1. `/v1/health` 200 且 `service=dsh-llm-gateway`；
2. `/api/zhaoyun/mcp` ok=true、4 servers，`tushare-http` ok=true、tools=**258**；
3. `vault.py verify --json` ok=true；`render_mcp.py --check` ok=True 且明文命中=0；
4. 8796 新 pid 启动时间 > 本次 env 更新时间；日志无鉴权失败/异常堆栈；
5. `credential_store list` 中 tushare：`sha256_8` ≠ `05240a8e`、`known_leaked=false`；
6. 旧 `.bak-20261027` 已归档；tracked 文件 grep 明文 token = 0；
7. 回执落 `deliverables/2026-10-28_X5_token轮换_执行回执.md`，STATUS/DOCS_INDEX 同步。

## 四、待批（两步）

1. **老田执行**：Tushare 控制台重置 → inbox 写入 → 回「token 已写入」；
2. **批准 8796 重启（L2）**：回「批准 X5-8796 重启」→ 执行 B0–B6 并出回执。
   - 若暂缓：回「X5 暂缓」，本包仅归档。

## 五、关联提醒

- age 私钥离线备份仍未完成；若 key 丢失，`secrets.enc` 永久不可解。
- Phase 2（消费方改 env/resolver、清明文）不在本包范围。
- 当前 DATA1/harness 闸门与 X5 独立；⑤ 全量仍等 DATA1。

| 版本 | 日期 | 说明 |
|---|---|---|
| v1 | 2026-10-27 | 首版（legion_command 旧版） |
| v2 | 2026-10-27 | 补充受阻状态/验收 |
| v3 | 2026-10-28 | 按当前 `dsh-llm-gateway` / MCP 258 / LegionOps sentinel / credential_store 加固刷新 |
