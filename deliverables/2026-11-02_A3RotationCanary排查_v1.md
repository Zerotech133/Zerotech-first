# A3RotationCanary 8 处「已泄露旧值未轮换」排查 v1（脱敏 · 只读）

> **STATUS: analysis_done**（只读；未执行轮换、未修改任何文件/任务/配置；未 commit/未重启）
> 2026-11-02 14:43–15:05 ｜ 席位：wa-map（车道3，task-70）｜ 触发：窗口预检 P1 —— `A3RotationCanary` 2026-11-02 07:00 `rc=1`「8 处已泄露旧值未轮换」
> **安全约束**：本文件及排查过程**不打印任何明文值**；所有凭据只以「类别 + 载体 + 行号/字段 + 长度 + sha256 前 8 位」呈现（sha8 亦为 canary 既有口径）。
> 既有裁定：老田 **2026-10-27 05:05 裁定 B「接受风险（ACCEPTED_RISK）」**（引 `deliverables/2026-10-26_A3轮换_状态与风险登记_待拍板.md` §6）。本单不改变该裁定，只做现状排查与建议。

---

## 0. 一页结论（给 Lead/A0）

| 项 | 结论 |
|---|---|
| 8 处口径 | `rotation_status.py --status` 按「载体 × 去重旧指纹」计数 = **8**（6 个载体文件 / **9 个物理行** / **8 个不同旧值**）；自 **10-30 起稳定为 8**（10-27 11→9、10-30 起 8，无新增泄露） |
| 是否假阳性 | **否（文件侧无假阳性）**：8 处均为旧值仍在载体文件中；其中 3 条已于 10-27/10-28 完成轮换（canary 11→8 的降幅来源），基线保留属正确的回归检测 |
| 根因（未轮换的原因） | **新值从未提供**：`ROTATION_FILLBACK.env` 的 **12 个 secret 键仍全为 len=0**（mtime **2026-10-26 21:57**，6 天未变）；外部平台重置（tushare/模型商/智谱/飞书/第三方）**待老田本人完成**，执行侧无新值可写 → 属「受控等待」，非执行遗漏 |
| 风险分级 | **高 3**（R2a/R2b LLM+智谱，活跃消费；XQ_A_TOKEN 雪球线活跃读取）｜**中 3**（kg 部署三旧值、kg 入湖旧 PG、C:\Temp 5 脚本硬编码）｜**低 3**（osint PG 库下线、zhice TUSHARE_TOKEN 无代码消费、2 处运行中容器 bootstrap 残留） |
| 关键新发现（超出 canary 视野） | ①**`C:\Temp` 5 个脚本硬编码旧 quant-pg 口令**（canary 不扫）；②**运行中容器 env 残留旧值**：`quant-pg`/`zhice-db` 的 `POSTGRES_PASSWORD`=旧指纹（bootstrap 口径，DB 侧口令据 R5/11-02 诊断已换新，旧值大概率已失效，需 DB 侧验证）；③**canary 存在标签/载体语义混淆**（见 §3.9）与**误报口径**（accepted-risk 期 rc=1 属预期，`task_exit_audit` 已把 A3RotationCanary 期望码登记为 1） |
| 建议 | ①保持裁定 B 与每日 canary；②**按 §5 轮换建议**由老田先做 R2a/R2b 与 XQ token 平台重置（填 fillback 后 `rotation_status.py --apply` 一次自动改 4 载体）；③**本周 L1 卫生**：清理 C:\Temp 5 脚本、对齐 kg deploy/.env 到当前生效值、更新 canary 基线与 ledger（§6）；④**不要**为了 rc=0 删旧指纹/藏旧值 |

---

## 1. Canary 机制（读码结论，源码只读）

`C:\项目\_reviews_20261026\rotation_canary.ps1`（1,283B，BOM）→ 调 `rotation_status.py --status`：

- **扫描面 = 8 个固定载体**（硬编码，`CARRIERS`）：`C:\项目\.mcp.json`、`repo\.mcp.json`、`quant_backtest\.env.local`、`portal\audit_ai_config.json`、`osint_video\.env`、`zerobound-kg\deploy\.env`、`zerobound-kg\ingestion\.env.local`、`zhice-os-v2…\code\.env.local`。
- **匹配法**：每文件提取 ≥16 字符的 `[A-Za-z0-9_\-\.]` token → `sha256(值)[:8]` → 与 `OLD_FP`（**11 条已泄露旧指纹**）比对；**只输出类别/长度/sha8，从不打印明文**。
- **计数法**：每载体 `present += distinct OLD hashes` 求和 → 「N 处」；同一值出现在多行只计 1（因此 `kg_deploy_env` 3 行=2、`kg_ing_env` 2 行=1）。
- **rc 语义**：`0`=旧指纹全未命中（疑似已轮换/文件被改，须人工复核，脚本会黄色提示"不要当没事"）；`1`=仍有旧值（**accepted-risk 期预期**）；`2`=异常（10-30 00:13 曾出现 1 次 rc=2 空输出，需关注）。
- **调度**：计划任务 `\A3RotationCanary`，每日 **07:00**，`powershell -File rotation_canary.ps1`；日志 `C:\Temp\rotation_canary.log`（追加，含每次 rc 与载体明细）。
- **历史趋势（日志实测）**：10-27 05:05 **11** → 10-27 14:39 **9** → 10-30 00:14 起 **8**（持续到 11-02 07:00，**4 天稳定**）。
- **已清除的 3 条（canary 11→8 来源）**：`tushare MCP token`（sha8 `05240a8e`）原在 `.mcp.json`×2 + `quant_env`；经 10-27 14:38 与 10-28 17:36 两次轮换（`_vault/secrets/mcp/tushare_mcp_token` 10-28 17:36；`quant_env` 的 `XT_DATA_KEY` 现为新指纹 `0fd89b25`）→ 已不再命中。**基线仍保留该指纹 = 正确的回归检测，不建议删除。**

---

## 2. 「8 处」清单（canary 计数口径；对应 9 个物理行 · 脱敏：载体 / 行号 / 字段 / 类别 / 长度 / sha8）

| # | 载体（路径） | 行 | 字段 | 凭据类别（canary 标签） | len | sha8 | 文件内实际状态 |
|---:|---|---:|---|---|---:|---|---|
| 1 | `C:\项目\老田的AI助手\quant_backtest\.env.local` | 9 | `XQ_A_TOKEN` | tushare API token（**标签存疑，见 §3.9**） | 40 | `7ab29836` | 旧值仍在；同日文件内 `XT_DATA_KEY`（MCP token）已是新值 `0fd89b25` |
| 2 | `…\portal\audit_ai_config.json` | 4 | `key` | LLM `sk-` key | 51 | `497522e6` | 旧值仍在（JSON 字段，文件 mtime 未变） |
| 3 | 同上 | 6 | `zhipu_key` | 智谱 key | 49 | `dc8a0228` | 旧值仍在 |
| 4 | `…\osint_video\.env` | 4 | `PG_PASSWORD` | osint PG password | 43 | `c27099a9` | 旧值仍在（目标 PG 5448 当前不可达，见 §3.4） |
| 5 | `C:\项目\zerobound-kg\deploy\.env` | 3 | `NEO4J_AUTH` | kg deploy secret1 | 17 | `9809be8a` | 旧值仍在；**运行中 neo4j-kg 已用新值**（见 §3.5） |
| 6 | 同上 | 6 | `KG_PORTAL_NEO4J_PASSWORD` | kg deploy secret1（同值副本） | 17 | `9809be8a` | 同上；KG portal 服务当前未运行 |
| 7 | 同上 | 9 | `KG_PORTAL_AUTH_SECRET` | kg deploy secret2 | 23 | `5bd8888f` | 旧值仍在；KG portal 当前未运行 |
| 8 | `C:\项目\zerobound-kg\ingestion\.env.local` | 2、3 | `PG_DSN` / `PG_PASSWORD` | kg ingestion PG | 18 | `ece9baa0` | 旧值仍在（2 行同值，canary 计 1）；**运行中 quant-pg 容器 bootstrap env 亦为该旧值**（见 §3.6） |
| 9 | `C:\项目\zhice-os-v2-windows-20260920\code\.env.local` | 14 | `TUSHARE_TOKEN` | zhice tushare | 56 | `97b7a9a4` | 旧值仍在；**zhice 仓 Python 代码无 TUSHARE_TOKEN 消费**（仅 compose env 透传，相关 app 容器未运行） |

> 合计：**6 文件 / 9 物理行 / 8 个不同旧值 = canary 的「8 处」**。全部载体均已被 `.gitignore` 忽略（非 git 跟踪）；`audit_ai_config.json` 经 8734/8080 实测 **404**（nginx B-06 封禁）；但**本机 `BUILTIN\Users` 组对这些文件有 ReadAndExecute**（ACL 实测，见 §3.10）。

---

## 3. 逐项分类判断（真风险 / 假阳性 / 归属与在用性）

### 3.1 R2a LLM `sk-` key（#2）— **真风险·高（活跃消费 + 可花钱）**
- 消费方（代码实测）：`agent_platform/a0_agent.py:57/66/91`、`agent_platform/engine/llm.py:352/373`、`portal/api_misc.py:577` 等 21 处；`audit_ai_config.json` 的 `key` 仍为旧值，文件 mtime 未变 → **现行调用仍可能用旧 key**（"落盘≠生效"需按消费方读取方式确认；多为调用时读文件）。
- 影响：LLM 调用可被第三方复用（花钱/配额）。**归属：D 线（DSH-Lead）**；新值需老田在服务商控制台吊销重发 → 填 `R2_LLM_API_KEY`。

### 3.2 R2b 智谱 key（#3）— **真风险·高（活跃消费）**
- 消费方同 §3.1（`zhipu_key` 优先级链见 `a0_agent/engine/llm`）；影响：智谱额度可被盗用。归属 D 线；新值填 `R2_ZHIPU_API_KEY`。

### 3.3 XQ_A_TOKEN（#1）— **真风险·中高（活跃读取，但标签/语义存疑）**
- 代码实测：`quant_backtest/xueqiu_common.py:133-160` 明确「三级链第 1 级：`.env.local` 的 `XQ_A_TOKEN`」，`xueqiu_sync.py:131-136` 在 token 批使用 → **雪球线（XueqiuSync/XueqiuCrawl）每日任务会读取该旧值**（读取≠必成功；另有浏览器登录态回退）。
- 但 canary/A3 文档把它标为「tushare API token」→ **标签与载体语义不一致**（`XQ_` 前缀 + pysnowball 用法更像雪球 token）。**需 owner（DATA1）核对是 tushare 还是雪球 token**；未核对前两者风险都按"旧值仍暴露"处理。
- 归属：DATA1（量化/雪球采集）；动作：若雪球 token → 重新登录雪球取新 token 写 `XQ_A_TOKEN`；若确为 tushare → 随 R1 平台重置。

### 3.4 osint PG password（#4）— **低-中（休眠库）**
- 目标：`PG_HOST/PORT=5448`，TCP 实测 **不可达**（本机无 osint-pg 容器运行；docker ps 17 容器无 osint-pg）→ 当前**不在服务中**；消费方 `osint_video/common/db.py:34` 存在（PG 通道）。
- 归属：INTEL1/OSINT 线；动作：待 osint PG 恢复前轮换（fillback `R7_OSINT_PASSWORD` 已预留键，len=0），恢复后使用新值；**不要把旧值带到恢复后的库**。

### 3.5 kg deploy env 三值（#5/#6/#7）— **假阳性/残留·中（现行 DB 已换，文件/compose 会回退）**
- 运行中 `neo4j-kg` 容器 `NEO4J_AUTH` 密码指纹 **≠ 旧 secret1**（新值 sha8 `4b8328cc`，len 12）→ **Neo4j 已在 R5 子集完成轮换**；`deploy/.env` 的 3 个旧值 = 文件未同步的**残留**。
- 风险不在"当前被用"，而在：①**下次 `docker compose up`/重建会把 Neo4j/portal 回退到旧口令**（neo4j-kg 容器无 compose label，当前为手工 run，来源待核）；②`KG_PORTAL_AUTH_SECRET`（旧 secret2）一旦 portal 起来即生效，且其为认证签名密钥。
- 归属：KG 维护方（DATA1 / zerobound-kg）；动作：**本地对齐（L1）**——从当前生效值（`quant_backtest/.env.local` 的 `KG_NEO4J_PASSWORD` 新值 `2ca9e044`；portal 密钥用新随机）更新 `deploy/.env` 3 键并留备份；同步受控堆栈时验证 Neo4j 连通。

### 3.6 kg ingestion PG（#8）— **残留·中（文件+容器 bootstrap 双残留，现行认证已换）**
- 当前 quant-pg **认证口令已换新**：`quant_backtest/.env.local` 的 `XMAP_PG_PASSWORD`/`LINEAGE_PASSWORD` = 新值 `4a86bc54`（今日 QuantMetaSync/psycopg2 实测 **CONNECT_OK**，见 `STATUS.md` 2026-11-02 05:45 段；同源脱敏口径见 `docs/audit/2026-11-02_drift_DSN与sentiment链路诊断.md`）。
- 旧值 `ece9baa0` 仍存在两处：①`ingestion/.env.local` 2 行；②**运行中 `quant-pg` 容器 env `POSTGRES_PASSWORD`**（bootstrap 引导值，非现行认证值；**旧值在 DB 侧是否仍可认证需 L2 只读验证，未验证**）。
- 归属：DATA1；动作：①ingestion env 更新为新值或标 deprecated（该模块是否在用需核对）；②容器 bootstrap 残留建议随下次受控重建更新（**不重启现网容器**）。

### 3.7 zhice TUSHARE_TOKEN（#9）— **低（当前无代码消费）**
- `zhice-os-v2` 仓 Python 代码 **0 处**读取 `TUSHARE_TOKEN`（仅 `docker-compose*.yml` 透传给容器；相关 app 容器未运行；8105 API 为 native python，未读该键）。
- 归属：zhice OS/A0；动作：随 R7 批次重置或**若确认无用则删除该行**（降低暴露面）；勿在应用容器恢复时带旧值。

### 3.8 运行中容器 bootstrap 残留（canary 盲区，2 处）— **低-中（需 DB 侧验证后定性）**
- `quant-pg`：`POSTGRES_PASSWORD` = 旧指纹 `ece9baa0`；`zhice-db`：`POSTGRES_PASSWORD` = 旧指纹 `b9530f12`（zhice V2 DB 旧值）。容器 env 非空即暴露面（可 `docker inspect` 读取），但 **bootstrap 值≠现行认证口令**（zhice-db 现行口令源 `zhice/.env.local V2_DB_PASSWORD` 已为新值 `f31128d2`，drift 诊断已实测可用）。
- 动作：不热改运行容器；**下次受控重建时一并清理**（属 L2 重建窗口）；把该项纳入 canary 的"residue"分类（§6），避免与"文件旧值未轮换"混算。

### 3.9 canary 标签/基线问题（重要）
- `OLD_FP` 11 条标注与载体存在不一致：①`7ab29836` 标 tushare API 但载体是雪球链读取的 `XQ_A_TOKEN`（§3.3）；②`b9530f12`/`48856039`（zhice V2 DB/Telegram）未出现在当前 8 处里（已从载体清除），基线保留正确。
- `tools/credential_store.py:22-24` 仅有 `KNOWN_LEAKED_SHA8 = {"05240a8e"}`（MCP token），与 canary 的 11 条**非单一事实源** → 建议同步（§6）。

### 3.10 暴露面与 ACL（只读取证）
- 载体均 **非 git 跟踪**（`.gitignore` 忽略）、不在 webroot（`audit_ai_config.json` 8734/8080 均 **404**）；备份/`_vault` 目录有界扫描 **未见旧值**（9,519 文件/90s 抽样）。
- **ACL**：6 个载体文件均含 `BUILTIN\Users:ReadAndExecute`（本机多用户/远程会话可读）；`_vault\secrets` 已 owner-only。建议（L1，owner=DATA1/A0）把 6 载体 ACL 收到 owner-only（与 `_vault/secrets` 同口径），降低本地横向读取面。

### 3.11 另发现：`C:\Temp` 5 个脚本硬编码旧 quant-pg 口令（canary 盲区，**中风险卫生问题**）
有界扫描（C:\Temp 等 10 个根，9,519 文件）发现 5 个 **2026-10-19** 的临时脚本明文内嵌旧指纹 `ece9baa0`（kg ingestion/quant-pg 旧值）：

| 文件 | 行 | 字段 |
|---|---:|---|
| `C:\Temp\check_stock_basic.py` | 3 | `password` |
| `C:\Temp\cleanup_trade_pool.py` | 4 | `password` |
| `C:\Temp\exec_trade_pool_migrate.py` | 9 | 行内（含 user/password 拼接） |
| `C:\Temp\migrate_trade_pool.py` | 5 | `password` |
| `C:\Temp\test_quant_pg.py` | 4 | 行内 |

→ **canary 不扫 C:\Temp**；建议：①按 Windows 删除纪律（回收站）清理这 5 个临时脚本（owner=DATA1）；②canary 扫描面加入 `C:\Temp\*.py/*.ps1` 的凭据模式；③`C:\Temp` 历史日志/输出中的同值亦建议一次专项清查（本单未逐日志扫，避免范围外读取）。

---

## 4. 风险分级汇总

| 级别 | 项 | 理由 | 归属 |
|---|---|---|---|
| **高** | R2a LLM sk-（#2） | 活跃消费 + 直接花钱类 key，旧值仍为现行配置 | D 线/DSH-Lead |
| **高** | R2b 智谱（#3） | 同上（额度） | D 线/DSH-Lead |
| **中高** | XQ_A_TOKEN（#1） | 雪球每日任务活跃读取；标签/来源待核对 | DATA1 |
| **中** | kg deploy 三值（#5/6/7） | 现行 DB 已换，但文件未同步 → compose 重建回退风险；portal 签名密钥 | DATA1/KG |
| **中** | kg ingestion PG（#8）+ 容器 bootstrap 残留 | 文件+容器双残留；现行认证已换新（旧值是否仍可认证待 L2 验证） | DATA1 |
| **中（卫生）** | C:\Temp 5 脚本硬编码（§3.11） | 明文凭据落在临时目录；本地 Users 可读 | DATA1 |
| **低** | osint PG（#4） | 目标库当前不可达/服务下线 | INTEL1/OSINT |
| **低** | zhice TUSHARE_TOKEN（#9） | 无 Python 消费方，仅 compose 透传（app 未跑） | zhice/A0 |
| **低** | 6 载体 ACL `Users:Read`（§3.10） | 本机单人多会话环境，暴露面有限 | DATA1/A0 |

---

## 5. 轮换建议（谁 / 何时 / 何通道 / 验收；含 L1·L2 分级）

> **纪律**：轮换=凭据变更；外部平台动作**必须老田本人**（不代登录/不代签）。新值统一落 `C:\项目\_vault\secrets\ROTATION_FILLBACK.env`（**当前 12 个 secret 键全 len=0，需填**），再由 DSH-Lead 跑 `rotation_status.py --apply` 自动改 4 个 R1/R2 载体（自动备份；只打 len/sha8）。
> 顺序建议：**R2a/R2b（高，5 分钟/项）→ XQ_A_TOKEN（中高，先核对来源）→ kg 本地对齐（L1，无需外部）→ C:\Temp 清理（L1）→ osint/zhice（低，随恢复/批次）**。

| # | 项 | 老田动作（外部平台） | 本地动作（执行方） | 验收 | 级别 |
|---|---|---|---|---|---|
| R2a | LLM `sk-`（#2） | 服务商控制台**吊销重发** → 填 `R2_LLM_API_KEY` | `rotation_status.py --apply`（自动改 `audit_ai_config.json.key` + 备份；按消费方缓存决定是否需 8091 换血） | 旧 key 调用 401；新 key 跑一次审计/判读成功；canary 该条消失 | L2（凭据）+L1（写盘） |
| R2b | 智谱（#3） | 智谱控制台吊销重发 → 填 `R2_ZHIPU_API_KEY` | 同上（`zhipu_key`） | 同上（一次 GLM 调用成功） | L2+L1 |
| XQ | `XQ_A_TOKEN`（#1） | **先核对**是雪球 token 还是 tushare token（§3.3）；雪球：重新登录/浏览器态刷新；tushare：随 R1 重置 | 更新 `quant_backtest/.env.local`（走 `xq_token_refresh` 或手填，备份） | `xueqiu_sync` token 批不再 skip/取数成功；canary 该条消失 | L1（若雪球 self-service） |
| R5a | osint PG（#4） | 待 osint-pg 恢复时轮换库口令 → 填 `R7_OSINT_PASSWORD` | 更新 `osint_video/.env` 的 PG_PASSWORD | `osint_video` PG 通道连通/降级为显式 skip；canary 消失 | L2（DB） |
| R5b | kg deploy 三值（#5/6/7） | **无需外部**（Neo4j 已换） | **L1 本地对齐**：`deploy/.env` 的 `NEO4J_AUTH`/`KG_PORTAL_NEO4J_PASSWORD` 改为当前生效值（源：`quant_backtest/.env.local KG_NEO4J_PASSWORD`，新指纹 `2ca9e044`）；`KG_PORTAL_AUTH_SECRET` 生成新随机；备份 `.bak_<ts>` | `docker compose config` 不含旧指纹；受控重建后 Neo4j 连通；canary 该 2 条消失 | L1 |
| R5c | kg ingestion（#8） | 无需外部 | 更新 `ingestion/.env.local` 两行为现行 `XMAP_PG_PASSWORD` 值；若模块确实废弃 → 标 deprecated 并加注释 | ingestion 连通（或显式标废弃）；canary 该条消失 | L1 |
| R7 | zhice TUSHARE_TOKEN（#9） | 随 R7 第三方批次重置（或确认无用） | 删除该行或更新；同步 compose 契约 | 无代码消费（已证）+ canary 消失 | L1/L2 |
| — | C:\Temp 5 脚本（§3.11） | — | 回收站删除（Windows 删除纪律） | 文件不存在；bounded scan 0 命中 | L1 |
| — | 容器 bootstrap 残留（§3.8） | — | 下次受控重建（L2 窗口）时用新 env 重建 | `docker inspect` 指纹≠旧值 | L2 |
| — | 6 载体 ACL | — | `icacls` 收到 owner-only（参照 `_vault/secrets`） | ACL 复核 | L1 |

**90 天复查**：按 B 裁定（2026-10-27 起算）→ **2027-01-25** 复查；对外表述纪律不变：**「已泄露未轮换（2026-10-27 接受风险）」**，禁止写"已修/已完成"。

---

## 6. Canary 基线与守卫更新建议（不删旧指纹）

1. **保留 `OLD_FP` 11 条**：已清 3 条（`05240a8e` 等）继续作回归检测；仅补注释 `cleaned_at`（勿删）。
2. **扫描面扩容（分层）**：
   - `file_carriers`（现 8 个）：保持 rc=1 语义（旧值在文件=未轮换）；
   - 新增 `temp_carriers`：`C:\Temp\*.py`/`*.ps1` 凭据模式扫描（§3.11）；
   - 新增 `container_env`（只读 `docker inspect` + sha8）：把 `quant-pg`/`zhice-db` bootstrap 残留列为 **residue 分类**（不并入 8 处计数，避免 rc 永远 1）；
   - 新增 `backup_carriers`：轮换产生的 `.bak_*`、`_vault` 备份（若存在旧值 → 高优先清理）。
3. **单一事实源**：`rotation_status.OLD_FP`、`tools/credential_store.py KNOWN_LEAKED_SHA8`、`ROTATION_LEDGER` 三处统一（建议以 ledger 为准生成另外两处；当前 credential_store 只含 1 条）。
4. **输出结构化**：canary 日志改 JSON（`counts_by_class / new_hits / residue`），便于每日比对；**只在"数量增加/新载体命中"时告警**（当前 rc=1 每日无通知，靠人看日志）；保留 rc=1 预期语义（`task_exit_audit.py:105` 已登记 A3RotationCanary 期望码=1）。
5. **补台账**：`_reviews_20261026\ROTATION_LEDGER_draft.tsv` 在文档中被引用但**实测不存在** → 重建 R1–R8 台账（项/所有者/fillback 键/状态/证据/下次复查），本单 §5 可直接作为初版行。
6. **标签勘误**：`7ab29836` 的类别与载体语义（tushare vs 雪球）需 owner 核对后修正 OLD_FP 注释（避免误派轮换）。
7. **恢复 rc=0 的唯一路径**：8 处文件全部清理（轮换/对齐/删除）后 canary 自然归 0；**不要**通过删基线/改脚本制造 rc=0。

---

## 7. 验证命令（安全：只输出 len/sha8/计数，勿打印明文）

```powershell
# 1) canary 只读状态（不改任何文件；输出为类别/len/sha8）
C:\项目\py312\python.exe -X utf8 C:\项目\_reviews_20261026\rotation_status.py --status

# 2) 任务态与日志（rc=1=accepted-risk 期预期；rc=2 需查）
schtasks /Query /TN "A3RotationCanary" /V /FO LIST
Get-ScheduledTaskInfo -TaskName A3RotationCanary | Select-Object LastRunTime,LastTaskResult,NextRunTime
Get-Content C:\Temp\rotation_canary.log -Tail 20

# 3) fillback 是否已填（只看键名与长度：12 个 secret 键应 len>0 才算有值）
Get-Content C:\项目\_vault\secrets\ROTATION_FILLBACK.env |
  Where-Object { $_ -match '=' -and $_ -notmatch '^#' } |
  ForEach-Object { $p=$_.Split('=',2); "{0} len={1}" -f $p[0].Trim(), $p[1].Trim().Trim('"').Length }

# 4) 轮换后自动写回（仅 DSH-Lead 在 fillback 有值后执行；自动备份）
# C:\项目\py312\python.exe -X utf8 C:\项目\_reviews_20261026\rotation_status.py --dry-run
# C:\项目\py312\python.exe -X utf8 C:\项目\_reviews_20261026\rotation_status.py --apply

# 5) 复验（期望：旧指纹消失；若 rc=0 须人工确认"已轮换/文件被改"）
C:\项目\py312\python.exe -X utf8 C:\项目\_reviews_20261026\rotation_status.py --status
# 6) 载体暴露面复核（期望 404）
curl.exe -s -o NUL -w "%{http_code}" http://127.0.0.1:8734/audit_ai_config.json
```

---

## 8. 边界与未做（如实）

- **未执行任何轮换/写操作**；未改数据/代码/任务/配置；未重启；未 commit。仅只读：读脚本/日志/文档、`rotation_status.py --status` 语义读码、容器 `docker inspect`（凭据仅算 sha8）、有界文件扫描（9,519 文件，仅输出路径/计数）。
- **未做 DB 侧认证验证**（旧 PG 值是否仍可认证需 L2 只读探针；本单不代做）。
- **未逐 C:\Temp 历史日志扫描**（范围控制；建议另立专项）。
- 本文件**零明文凭据**；证据保留 sha8 前 8 位（canary 既有口径）与路径/行号，可复核。

---
*排查生成：2026-11-02 15:05 · wa-map（车道3）· task-70 · 只读，未轮换/未修改*
