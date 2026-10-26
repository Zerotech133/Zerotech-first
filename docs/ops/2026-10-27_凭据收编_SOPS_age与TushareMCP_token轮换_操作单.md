# 2026-10-27 凭据收编：SOPS+age 凭据保险库与 Tushare MCP token 轮换操作单

> - 工单：**K-DATA-20261027-SECVAULT**（L1 安全）
> - 执行：secvault-impl（Agent Teams 执行席位）· 2026-10-27 · 分支 `task/K-DATA-20261027-SECVAULT`（**未 merge**，由 Lead 统一合并）
> - 范围：`C:\项目\_vault\secrets`（约 30 个明文凭据）与两处 `.mcp.json` 中的 Tushare MCP token
> - **红线：本单不出现任何 token / 密码 / 私钥明文；所有值一律 `<masked>`。** 本单中的命令均不打印凭据。
> - 结论：**L1 已完成**——密文副本 + MANIFEST 校验 + MCP token 移出明文配置；明文层与生产代码**零改动、零断线**。
>   原地清除明文与消费方切换属 **Phase 2**（§4），本轮不做。

---

## 0. 一页结论

| 项 | 状态 |
|---|---|
| age / sops 便携二进制 | ✅ 已落地 `C:\项目\老田的AI助手\bin\`（age v1.3.2、sops 3.13.3，sops 已对官方 checksums 校验通过；不进 git） |
| `_vault/secrets` 加密副本 | ✅ 32 个文件全部落 `C:\项目\_vault\secrets.enc\`（23,235 B → 29,635 B）+ `MANIFEST.json`；`vault.py verify` 32/32 通过 |
| 两处 `.mcp.json` 明文 token | ✅ 已替换为 `${TUSHARE_MCP_TOKEN}` 占位（结构不变），原文备份 `*.bak-20261027`；**grep 明文 token 命中数 = 0** |
| 工具与测试 | ✅ `tools/secrets/{vault.py,render_mcp.py,tests/test_vault.py,README.md}`；unittest 22/22 通过 |
| 生产影响 | ✅ **零断线**：明文文件原位未动、消费方代码未改、服务未重启 |
| 用户待办（本单外） | ① **轮换 Tushare MCP token**（§3，旧值按已暴露处理）；② 重启 MCP 客户端前先 `render_mcp.py`/Phase 2 resolver；③ 立即离线备份 age 私钥（§5） |

**为什么本轮不原地加密**：`_vault/secrets/**` 被 `portal/api_common.py`、`portal/api_legion_ops_v2.py`、
`agent_platform/engine/deepseek.py`、`news_collector/config_local.py`、`quant_backtest/config_local.py`
等**生产代码直接按明文路径读取**；原地加密会立即断产线。故本轮只做「旁路密文副本」，消费方切换后再清明文。

---

## 1. 本轮落盘 / 入库清单

| 路径 | 内容 | 入 git |
|---|---|---|
| `tools/secrets/vault.py` | 保险库 CLI：init/encrypt/decrypt/exec-env/verify/scan（age 优先，AES-GCM 兜底，全 fail-closed） | ✅ |
| `tools/secrets/render_mcp.py` | 模板 + token 密文 → `.mcp.local.json`；`--check` 校验（不打印 token） | ✅ |
| `tools/secrets/tests/test_vault.py` | 22 个 unittest（往返/MANIFEST/fail-closed/CLI/age 后端） | ✅ |
| `tools/secrets/README.md` | 工具来源、sha256、用法、升级与红线 | ✅ |
| `.mcp.json.template` | 主仓 MCP 模板（tushareMcp，`${TUSHARE_MCP_TOKEN}`） | ✅ |
| `tools/secrets/templates/parent.mcp.json.template` | `C:\项目\.mcp.json` 的模板（tushareMcp + stockDataMcp） | ✅ |
| `.gitignore` | 新增 `_vault/{keys,secrets,secrets.enc}/`、`.mcp.local.json`、`*.bak-20261027`、`*.age` 保险垫 | ✅ |
| `C:\项目\_vault\secrets.enc\` | 32 个 `.age` + `MANIFEST.json` | ❌（repo 外，按设计不入库） |
| `C:\项目\_vault\keys\age.key` + `age.recipients` | age 身份 + 收件人（ACL 已收紧） | ❌（repo 外，**绝不入库**） |
| `C:\项目\老田的AI助手\bin\{age,age-keygen,sops}.exe` + `SECVAULT-TOOLS.json` | 便携工具（`bin/*.exe` 已被忽略） | ❌ |
| `*.bak-20261027`（两处 `.mcp.json` 备份） | 含明文 token 的旧配置 | ❌（仅本机，勿外发） |
| `.mcp.local.json`（worktree 根、`C:\项目\`） | 本机渲染配置（含 token，gitignored、ACL 收紧） | ❌ |

---

## 2. 已收编清单（32 个文件；**不含任何明文值**）

密文位置 = `C:\项目\_vault\secrets.enc\<同名路径>.age`；`MANIFEST.json` 记录
`path / sha256_plain / sha256_cipher / bytes / encrypted_at / tool_version / cipher`。

| # | 文件（相对 `_vault/secrets/`） | 类型 | 字节 | 生产代码直读 |
|---|---|---|---|---|
| 1 | `cainiao_oauth2.env` | dotenv | 465 | **是**：`portal/api_common.py:686`（R39 私有区优先，`C:\Temp` 回退） |
| 2 | `C_Temp_cainiao_oauth2.env` | dotenv（C:\Temp 迁移副本） | 465 | 否（历史副本，0 引用） |
| 3 | `C_Temp_lark…secret.txt` | 单行文本 | 35 | 否（0 引用） |
| 4 | `C_Temp_MIGRATION_MANIFEST.tsv` | 迁移清单 TSV | 600 | 否 |
| 5 | `C_Temp_om…secrets.txt` | 多行文本 | 49 | 否（0 引用） |
| 6 | `C_Temp_portal…token` | 单行 token | 64 | 否（代码回退读 `C:\Temp\portal_api_token` 原件，非此副本） |
| 7 | `C_Temp_w1…check.txt` | 单行文本 | 42 | 否（0 引用） |
| 8 | `deepseek_api_key.meta.json` | 元数据 JSON | 356 | 否（未见引用） |
| 9 | `deepseek_api_key.txt` | 单行 key | 35 | **是**：`agent_platform/engine/deepseek.py:40-50`、`agent_platform/skills/llm-deepseek/scripts/verify.py:86`、`agent_platform/tools/build_skills.py:76` |
| 10 | `news_config_private.json` | 私有配置 JSON | 461 | **是**：`news_collector/config_local.py:35-37`（另 `jev_base_backfill.py`/`jev_base_dedupe.py`/`news_jev_chart.py` 报错文案指向） |
| 11 | `portal_api_token` | 单行 token | 64 | **是**：`portal/api_common.py:220-226`、`portal/api_legion_ops_v2.py:35-43` |
| 12 | `quant_config_private.json` | 私有配置 JSON | 276 | **是**：`quant_backtest/config_local.py:34-36` |
| 13 | `README.md` | 目录说明 | 926 | 否 |
| 14 | `ROTATION_FILLBACK.env` | dotenv（轮换填回报文） | 2,065 | 否（tracked 0 引用，手工/运维用） |
| 15-21 | `ROTATION_FILLBACK.env.bak*`（7 个） | dotenv 历史备份 | 1,988–2,065 | 否 |
| 22 | `ROTATION_FILLBACK_TEMPLATE.md` | 模板 | 1,833 | 否 |
| 23 | `cainiao/cainiao_mysql_root.pw` | 单行密码 | 18 | 否（DB 运维凭据，tracked 0 引用） |
| 24 | `cainiao/cainiao_oauth2_clients.env` | dotenv | 351 | 否（轮换 runbook 手工用） |
| 25 | `cainiao/cainiao_oauth2_default.env` | dotenv | 47 | 否（同上） |
| 26 | `cainiao/cainiao_xmap_ro.pw` | 单行密码 | 16 | 否（只读账号，0 引用） |
| 27-31 | `_retired_C_Temp_<日期>/（5 个退役副本：cainiao_oauth2 + 4 个 token/secret 文本，全名见 MANIFEST）` | 退役副本 | 35–465 | 否 |
| 32 | `mcp/tushare_mcp_token` | 单行 token（**本轮从 `.mcp.json` 迁入**） | 56 | 由 `tools/secrets/render_mcp.py` 消费 |

- 合计：**32 文件 / 23,235 B 明文** → 密文 **29,635 B**；`MANIFEST.json` 14,576 B。
- `verify` 已断言每个密文 `sha256_cipher` 与 MANIFEST 一致（32/32），篡改/缺失即 rc=1。

---

## 3. 已暴露 token 与轮换操作单（**用户执行，我们不碰外部账号**）

### 3.1 暴露面盘点（Tushare MCP token）

| 位置 | 本轮处理 | 现状 |
|---|---|---|
| `C:\项目\老田的AI助手\.mcp.json`（`tushareMcp.url` 查询参数内） | 替换为 `${TUSHARE_MCP_TOKEN}` | **0 明文**；原文备份 `.mcp.json.bak-20261027` |
| `C:\项目\.mcp.json`（同上，另含 `stockDataMcp` 本地 stdio 项） | 同上 | **0 明文**；原文备份 `.mcp.json.bak-20261027` |
| `C:\项目\_vault\secrets\mcp\tushare_mcp_token` + `secrets.enc/mcp/tushare_mcp_token.age` | 新建：密文副本 | 明文在私有区（Phase 2 清除）；密文已入 MANIFEST |
| `.mcp.local.json`（本机渲染产物 ×2） | 运行时渲染（gitignored、ACL 收紧） | 含明文，仅本机；Phase 2 改 resolver 后应删除 |

> 判定：该 token 曾以明文存在于两个可被工具/Agent 读取的配置文件，**按已暴露处理，必须轮换**（旧值作废）。
> 只要本单的 `.bak-20261027` / `.mcp.local.json` / `secrets/mcp/` 明文还在，就不能把「移除」当成「彻底消除」。

### 3.2 轮换前基线（先记状态，便于回退）

```powershell
$py = C:\项目\py312\python.exe
Set-Location C:\项目\老田的AI助手
& $py tools/secrets/vault.py verify --json          # 期望 ok=true, checked=32
& $py tools/secrets/render_mcp.py --check           # 期望 ok=True, token 明文命中=0
```

### 3.3 生成新 token 并写入保险库

1. 登录 **Tushare 控制台/账号** → 进入 MCP/API Token 管理 → **重置/生成新 token**（旧 token 立即失效或按平台策略）；
2. 用交互式 getpass 写入（不回显、不进命令历史）：

```powershell
$py = C:\项目\py312\python.exe
& $py -c "import getpass,pathlib; p=pathlib.Path(r'C:\项目\_vault\secrets\mcp\tushare_mcp_token'); p.write_text(getpass.getpass('new token: ').strip(), encoding='utf-8'); print('written', p)"
```

3. 加密增量更新（未变文件自动复用，只有 token 密文重写）+ 校验：

```powershell
& $py tools/secrets/vault.py encrypt C:\项目\_vault\secrets --recursive
& $py tools/secrets/vault.py verify --json
```

### 3.4 重新渲染本机 MCP 配置

```powershell
& $py tools/secrets/render_mcp.py            # 渲染 worktree 根 / C:\项目 下 .mcp.local.json（不打印 token）
& $py tools/secrets/render_mcp.py --check    # 期望 ok=True；两处真实配置明文 token 命中=0
```

### 3.5 重启相关服务**前**的验收（本轮纪律：不重启、不注册任务）

1. `vault.py verify --json` → `ok=true`；
2. `render_mcp.py --check` → `ok=True` 且 `token 明文命中=0`；
3. 把 `.mcp.local.json` 复制为对应目录的 `.mcp.json`（**仅本机运行时**），或完成 Phase 2 改用 resolver：
   `Copy-Item .mcp.local.json .mcp.json -Force`；
4. 用一次只读 MCP 工具调用（如 tushare 行情查询）验证新 token 可用；**失败则立即回滚 §5**；
5. 验证通过后才重启 MCP 客户端/相关服务；`stockDataMcp` 为本地 stdio，不涉 token。

### 3.6 建议顺带轮换（已收编但历史冗杂）

- `portal_api_token`（`secrets/portal_api_token`，被 `api_common.py`/`api_legion_ops_v2.py` 直读）；
- `deepseek_api_key.txt`（agent_platform 直读）；
- `cainiao_oauth2.env` / `cainiao_mysql_root.pw` / `cainiao_xmap_ro.pw`。
  步骤同上（新值写入对应明文文件 → `encrypt` → 消费方按 §4 切到 `exec-env`/resolver 后重启验收）。

---

## 4. 未完成的 Phase 2（消费方切换 → 原地清除明文）

### 4.1 目标架构

```
明文 _vault/secrets/**  ──(本轮)──>  _vault/secrets.enc/**（age 密文 + MANIFEST）
        │                                  │
        │  Phase 2 切换后不再被代码直读      ├─ 进程启动时：vault.py exec-env <enc.env> -- <cmd>
        ▼                                  └─ 代码内：tools/secrets/resolver.py get_secret(name)
     删除/归档（先双读验收 ≥7 天再删）
```

### 4.2 消费方与改造点

| 文件 | 现状（明文直读） | Phase 2 改造点 |
|---|---|---|
| `agent_platform/engine/deepseek.py:40-50` | 向上搜索 `_vault/secrets/deepseek_api_key.txt` | ① 优先 `DEEPSEEK_API_KEY` env（已支持）；② 新增 `resolver.get_secret("deepseek_api_key")`；③ 启动器用 `exec-env deepseek.env.age` 注入 |
| `portal/api_common.py:215-226` | `_vault\secrets\portal_api_token` → `C:\Temp` 回退链 | 改读 `PORTAL_API_TOKEN` env（启动脚本用 `exec-env`）；保留仅 env 回退，删除文件直读分支 |
| `portal/api_legion_ops_v2.py:35-43` | 同上（第二套重复回退链） | 收敛到 `api_common` 的 resolver，或同样改 env-only |
| `portal/api_common.py:686` | cainiao OAuth `_vault\secrets\cainiao_oauth2.env` | `exec-env cainiao_oauth2.env.age` 注入 OAuth client id/secret 后启动 8091；代码读 env |
| `news_collector/config_local.py:35-37` | `_vault\secrets\news_config_private.json` | `resolver.get_json("news_config_private")`，或采集任务启动命令套 `exec-env` |
| `quant_backtest/config_local.py:34-36` | `_vault\secrets\quant_config_private.json` | 同上 |
| `agent_platform/skills/llm-deepseek/scripts/verify.py:86`、`agent_platform/tools/build_skills.py:76`、`tools/seat_capability_inject.py` | 检查/文档引用明文路径 | 改为探测 `vault.py verify` 与 env，不再断言明文路径存在 |
| `news_collector/jev_base_{backfill,dedupe}.py`、`news_jev_chart.py` | 报错文案指向明文路径 | 文案改指 `vault.py exec-env` / `SECVAULT_HOME` |

### 4.3 切换顺序（建议）

1. 新建 `tools/secrets/resolver.py`（进程内缓存解密结果，**不落盘、不打日志**；无 key 时 fail-closed 并给出明确提示）；
2. 消费方先「双读」：`env 优先 → 明文回退`，每处单独 PR/小批，观察日志；
3. 启动脚本（`start_*.ps1` / 计划任务）改为 `vault.py exec-env` 包装；
4. 双读验收 **≥7 天** + `verify --json` 全绿后，停止读取明文；
5. 最后才把 `_vault/secrets/**` 原地加密/移入 `secrets.enc` 并删除明文（删除前做一次全量备份 + 回收站）；
6. 关闭 `.mcp.local.json` 路线，MCP 配置统一走 resolver/渲染到内存。

### 4.4 原地清除明文的门槛（缺一不可）

- 全部消费方已切换且 `grep -r "_vault/secrets"` 无**运行时**命中（文档/历史除外）；
- `vault.py verify --json` 连续 7 天全绿；
- age 私钥已离线备份并可恢复演练；
- 明文清除前保留一次加密全量 + 备份包（`_vault/secrets_archive_<日期>` 同款归档机制）。

---

## 5. 回滚预案

| 场景 | 操作 | 影响 |
|---|---|---|
| 密文副本异常/不需要了 | 删除或回收站处理 `C:\项目\_vault\secrets.enc\` | **产线无影响**（明文层从未移动） |
| `.mcp.json` 需要恢复旧行为 | `Copy-Item C:\项目\_vault\...`？不——直接用备份：`Copy-Item 'C:\项目\.mcp.json.bak-20261027' 'C:\项目\.mcp.json' -Force`；主仓同理 | 重新引入明文 token；**仅限应急、限时**，之后仍须轮换 |
| 渲染出错/想重来 | 删除 `.mcp.local.json` 后重跑 `render_mcp.py` | 无影响（gitignored） |
| 工具代码要回退 | 分支未合并：`git checkout .` 或丢弃 worktree；已合并：`git revert <commit>` | 无产线影响（本轮未改生产代码） |
| **age 私钥丢失** | 现有 `secrets.enc` **永久不可解** → 从离线备份恢复 key；若无备份：`init --force` 生成新身份 → 明文仍在 → 重新 `encrypt` 全量 | 密文重建；明文层未动所以可恢复 |
| 新 token 验证失败 | Tushare 控制台回滚/重新生成；本地恢复 §5 备份；`render_mcp.py` 重渲染 | 需在重启前完成，避免带错 token 起服务 |

> 本轮**没有**改动任何生产代码、服务配置或计划任务，因此对产线而言「回滚」≈ 删除新增旁路文件。

---

## 6. 风险与未决项

1. **明文仍在 + 直读**：L1 只增不改，暴露面未真正消除；本单闭环的是「配置仓库化/工具化」与「MCP token 明文移出」两件事，真正的密钥卫生要 Phase 2。
2. **明文 token 的残余副本**：`.bak-20261027` ×2、`.mcp.local.json` ×2、`_vault/secrets/mcp/tushare_mcp_token`（明文）——均在 repo 外/已忽略，但仍是本机暴露面；轮换后旧值残留也应清理（回收站）。
3. **age 私钥单点**：无备份=灾难；建议立即离线双份备份（U 盘/密码管理器），并做一次恢复演练。
4. **sops 未接入**：本轮 `vault.py` 直连 age 已满足 L1；sops 预留给 Phase 2 的「dotenv/JSON 同级加密 + 多接收人 + sops exec-env 编排」。
5. **worktree 下 `scan_secrets.py --all` 假绿**：其 `collect_targets` 以 `.git` 为目录判断仓库，worktree 的 `.git` 是文件 → 扫 0 文件、rc=0。本单验证改用 `vault.py scan`（worktree 下自动改扫「暂存+未忽略未跟踪」文件，等价 pre-commit）。**合并回主仓后请再跑一次** `python tools/scan_secrets.py --all --baseline tools/scan_secrets_allowlist.tsv`。
6. **MCP 客户端在占位符状态下重启会缺 token**：见 §3.5，重启前必须渲染/恢复。
7. 本轮未重启服务、未注册计划任务、未碰外部账号；`sqlite` 只读未涉及。

---

## 7. 本轮验证证据（可复跑）

在 worktree `C:\项目\老田的AI助手\.worktrees\K-DATA-20261027-SECVAULT` 下：

| 检查 | 命令 | 结果 |
|---|---|---|
| 语法 | `python -m compileall -q tools/secrets` | rc=0 |
| 单测 | `python -m unittest discover -s tools/secrets/tests -v` | **22 tests OK**（含 age 后端往返、AES-GCM 兜底、fail-closed、CLI） |
| 保险库初始化 | `python tools/secrets/vault.py init` | backend=age；key `_vault\keys\age.key`（ACL: owner+SYSTEM+Administrators）；recipients 已生成 |
| 加密 | `python tools/secrets/vault.py encrypt C:\项目\_vault\secrets --recursive` | file_count=32, encrypted=32, plain 23,235 B → cipher 29,635 B |
| 密文校验 | `python tools/secrets/vault.py verify --json` | `ok=true, checked=32, failed=0, backend=age` |
| MCP 渲染校验 | `python tools/secrets/render_mcp.py --check` | `ok=True`；两处真实 `.mcp.json` `token 明文命中=0`、`placeholder_in_config=True` |
| 明文凭据扫描 | `python tools/secrets/vault.py scan`（worktree 用暂存/未跟踪清单；主仓用 `scan_secrets.py --all --baseline`） | 未豁免命中=0（本单 8 条 docs 表格 HIGH_ENTROPY 误报已按仓库惯例登记 `tools/scan_secrets_allowlist.tsv`，category=false-positive） |
| 端口门 | `python tools/check_ports.py --strict` | rc=0（未登记 0、冲突 0） |

提交：随分支 `task/K-DATA-20261027-SECVAULT` 提交（hash 见 `git log -1 --oneline`；**未 merge**）。

---

## 8. 附：常用命令速查（全部不打印敏感值）

```powershell
$py = C:\项目\py312\python.exe
$V  = C:\项目\老田的AI助手\tools\secrets\vault.py   # 合并后路径
& $py $V verify --json
& $py $V decrypt cainiao\cainiao_oauth2.env.age                  # 仅 stdout
& $py $V exec-env ROTATION_FILLBACK.env.age -- python your_task.py
& $py C:\项目\老田的AI助手\tools\secrets\render_mcp.py --check
```
