# 验收报告 / G0-b 执行态门禁 · AIOS 数据资产与计划任务治理子系统（W-13）

> **测试报告 | v0.1 + v0.2 + v0.3 | 2026-11-05 ~ 11-06 | 执行人：团长（team-lead）｜版本：V5（五次签发，唯一有效）**
> 团队：`aios-governance-forge`（已收拢）｜平台形态：候选 A 单机 CLI（ADR-01 锁定）
> 唯一有效白名单：**GOV-W W-01~W-14**（W-04 §GOV-W 白名单终表 / W-12 §1.1 附件①）
> 最终采集时点：**2026-11-06T06:09:20+08:00**（待 v5 manifest 生成后校正） — `C:\Temp\governance_final_manifest_v5.json`（`VERDICT=GREEN`）
> G-09 签发时点：**2026-11-06T06:02:17+08:00**（D6 退役后重采签发；ADR-33 时点门禁语义）
> 基线时点：**2026-11-06T06:02:00+08:00**（D6 退役后重采；golden 只读基线仍 492、文件完整性未变；实时任务数由 `Get-ScheduledTask` 外部独立核实 497→455，不写入 golden）
> Python 口径：`"C:\Program Files\Python311\python.exe" -X utf8`；pytest 9.0.2

> **V3 三次签发说明**：用户 11-06 03:12 下达「代码审计，BUG 修复，对项目进行复盘总结」新指令，团长据此对 F1/F2 实现、W-05/06/07 测试、`C:\Temp` 门禁与采集工具做逐文件审计，复现并修复 **8 项真实缺陷**（BUG-01~08），新增 **6 条回归用例**（108 → 114）。这等价于 T+24h 待裁定 AB 中的 **B（解冻白名单并重签）**。V2 清单因 6 个 GOV-W 文件换指纹而整体过期，**V1 / V2 均作废，以 V3 为唯一有效验收依据**。审计全文与项目复盘见 `C:\Temp\20261106_code_audit_and_retrospective.md`。

> **V4 四次签发说明（D4 收尾重签）**：用户批准「按计划实施 → 全启动 D0/D1/D2/D4」后，团长完成 D4 坏档自动过滤的代码落地与验证，并据此重签 V4。D4 代码改动：`tools/b4_restore_drill.py` 接入 F2 `should_select(verify_artifact())` 自动过滤坏档（L43/48/110/112），`tests/test_governance_acceptance.py` 断言反转（AC-N04 / AC-E02 改名 `test_AC_E02_D4_auto_filter_integrated_AC02`）。因 b4 非 GOV-W 白名单成员、且其新指纹无法靠「改基线 sha」救回 G-09c，按铁律选择**重采集 G-09 基线**作为标准检查点动作（ADR-39），把已批准 D4 状态固化为 known-good。重采后 G-09 **9/9 PASS**、pytest **114 passed**、collect 生成 `governance_final_manifest_v4.json`（`VERDICT=GREEN`）。V3 清单与 15:04:12 基线保留作审计留痕。D0/D1/D2 依 L2 批准的状态与本次沙箱复核限制见 §7。

> **V5 五次签发说明（D6 任务退役执行 + 重签）**：用户 11-06 裁定（L2 式批准；早于 05:30:02 退役前快照采集、L2 报批草案 05:33:38 起草、先于 06:02:17 G-09 重采签发）「批准 D6、仅观察/无用在考虑退役、6+8 自定义任务去执行、执行后重采 G-09 + 重签 V5」，团长据此执行 D6 计划任务退役并据此重签 V5。D6 执行：经 L2 式裁定，退役 **42 个**已 Disabled 的自定义无用任务（29 个一次性日期戳残留 `DSH-Merge/Prep-*` + `W0_A0-*Deliver` + 13 个自定义守卫/同步/待复核任务），**逐任务先导出 XML 到 `C:\Temp\d6_backup\` 作回滚预案再 `Unregister-ScheduledTask`（走 Schedule 服务 COM，非被封的 `schtasks.exe`）**；**1 个 `\OffsiteSync` 因 OS 级 ACL（`E_ACCESSDENIED`，UAC 令牌过滤 + 显式 `t:(R)` 只读 ACE）删除被拒，该任务本就 Disabled、零运行风险，需提权手动删除**（ADR-42）。实时任务数由 `Get-ScheduledTask` 外部独立核实 **497→455**（golden 只读基线仍 492、G-09a 校验其文件完整性，D6 偏离作为已批准偏离在 §7 记载，不写入 golden）。重采 G-09 **9/9 PASS**、pytest **114 passed**、collect 生成 `governance_final_manifest_v5.json`（`VERDICT=GREEN`）。V4 清单与 D4 基线保留作审计留痕。D5 仍挂账待业务裁定（停库窗口 vs bind mount）。

---

## 0. G0-b 签发结论

**签发：通过（PASS）**。G0-a 设计态 READY → 已落盘 → G0-b 执行态六项证据齐备 → **G0-b 通过（V5）**（W-12 §9 发布单状态机）。

- **逐项对照**：G1~G6 六道门禁 + G-09 九项子门禁全部为「是」，见 §1 明细表；无「基本没问题」式表述、无省略证据。
- **实测值**：三文件 `pytest -q` **114 passed / 0 failed / rc=0**（默认 TEMP 与干净 TEMP 两种环境结论一致）；G-09 **9/9 PASS，`ALL_PASS=True`，rc=0**；**14/14 白名单落盘**；`VERDICT=GREEN`；`DRIFT=0`。D6 计划任务退役 **42 已删 + 1 被 OS ACL 阻断（Disabled、零运行风险）**，实时任务数经 `Get-ScheduledTask` 独立核实 **497→455**，golden 只读基线（492）文件完整性未变。
- **本签发不构成**：L2 五要素批准、真实飞书推送、`.enc` 解密、生产服务器触碰、`git commit`。**注**：D0/D1/D2/D4 的代码落地/注册是**本验收签发之外的独立动作**；**D6 计划任务退役是用户另行的 L2 式批准动作**（42 已删 + 1 被 OS ACL 阻断），其偏离在 §7 与 ADR-41/42 记载，不属本签发的「零状态变更」范畴——本签发确认的是代码审计、门禁与 D6 执行证据齐备。
- **本轮与 V2 的实质差别**：按用户新指令**解冻白名单**，修改 6 个 GOV-W 文件（W-01 / W-02 / W-05 / W-06 / W-07 / W-13）并修 2 个 `C:\Temp` 工具脚本；其余 8 条白名单一字节未动。所有改动均为**代码与文档修订**，不触碰 `backups/db` 既有产物、计划任务与系统状态。
- **签发后语义（ADR-33/35/39）**：G-09 是时点门禁，`verified_at=04:15:13` 即签发时刻；此后外部并行作业新增产物归运行态观测，不追溯推翻本结论；`collect` 只读签发快照、不再重跑 verify；每次重新签发必须重新归因，未知文件仍判 FAIL。V4 因 D4 代码落盘而**重采集基线**（ADR-39），把 b4 新指纹与 `ops/db_dump_daily.py` 外部 merge 一并固化为 known-good。

---

## 1. 门禁明细表（逐项「是/否 + 证据路径 + 实测值」）

格式按 W-12 附件② 表头：`门禁项 | 判据 | 是/否 | 证据路径 | 实测值 | 备注`

### 1.1 G1~G6（W-12 §4.2 原文判据）

| 门禁项 | 判据 | 是/否 | 证据路径 | 实测值 | 备注 |
|---|---|---|---|---|---|
| **G1 / AC①** | F1 连续 3 次字节级一致；492 统计逐项等于治理方案 §1.3；三前缀 / classify 顺序 / 分两组断言；`suggest_registry applied=false` 且零写 REGISTRY；两次 diff 为空 | **是** | W-01 / W-05（含 **TI-N15** 复合键回归、**TI-N16/N17** 本轮新增）；`C:\Temp\tasks_inventory_20261105.json`（utf-8-sig） | `stats --assert-golden` → `GOLDEN_MATCH=True failures=[]`；golden 492 条码点 `[52,57,50]`；distinct path **97**、根目录 `\` 聚集 **291**、`(path,name)` **492 且零重复**；自比 `unchanged=492`；构造增删改各 1 精确识别 | 16 项统计用 `ord()` 码点复核（本机打印通道存在 `10↔68`、`16↔91` 串味） |
| **G2 / AC②** | 10-29 三坏档隔离留痕八字段；manifest + 三个 `.sql.gz` 四文件 sha256 逐字节一致；**不改** `b4_restore_drill.py`；不写死 `list_backups()` 返回值 | **是** | W-08；W-02 `verify_artifact()`；W-06 六类夹具 + **BG-N07 / BG-N11~N14**；W-07 **AC-N04 / AC-E02 / AC-N08**；`..._verify.json` → `db_full_sha256` | flag 311 B、恰 8 键、`files` 3 条、`detected_magic=2d2d`、`format=plain-sql`、`recoverable=false`、`policy="mark-only, do-not-delete"`、`decision_ref=D4`；`changed=[]`、`removed=[]`、`added=[W-08]`；三坏档 `ok=false`；11-05 真 gzip `1f8b / ok=true`；`mismatches=0`、`rewritten_bytes=0` | **T-11 已关闭**：全程无 `list_backups()` 返回值断言；flag **非运行态过滤闸门**（ADR-05/11、W-09 A9「本轮是否改：否」）。本轮 F2 修好后坏档也带 sha256，见 BUG-04 |
| **G3 / AC③** | 三处口径统一为「明文副链断档 + 两库真空」，以新文档声明替代改原文件 | **是** | W-10 §六；W-03 / W-11 对照表；G-09 `system_files` | 叙事层唯一句「加密主链健康，缺的是明文副链排班与格式闸门」；验收层 AC3 保持「明文副链断档 + 两库真空」 | 撤回历史「P0 db 备份断链」定性；`db_dump_daily.py` 的 21:58:46 外部 merge 已单独归因，见 §1.2 |
| **G4 / AC④** | 关键链哨兵补链与 `DbDumpDaily=pending` 为**设计完成**，运行态零写入；`miss_check.py` 哈希前后不变 | **是** | W-01 `suggest_registry()`；W-09 A1~A16；W-07 AC-E06 | `applied=false`；4 条候选（3 active + DbDumpDaily pending）；REGISTRY 恰 23 条；492 计划任务名中无 `DbDumpDaily` | AC-04 按 ADR-05 不做运行态验证；**未排班** |
| **G5 质量** | P0 bug=0；核心链路 100%；三个测试文件 `pytest -q` 全过 rc=0；性能仅核对预算声明齐备 | **是** | §2① pytest；§2⑤ 缺陷清单；§2③ 预算表 | **114 passed / 0 failed / rc=0**；P0=0、P1 遗留=0；无 DeprecationWarning | 性能**不实测、不设阈值**，实测值栏留空标「实施期填」 |
| **G6 零状态变更** | ① schtasks=492 且无增删改启停；② `backups/db` 既有哈希不变；③ `git status` 新增 ⊆ GOV-W 终表；④ 无 `.enc` 解密 / 密钥零读取；AC-E08 反向扫描证明硬边界 7 个 0 | **是** | `..._baseline.json` / `..._verify.json`；W-07 AC-E08；本文件 §7 | G-09 **9/9 PASS**；`unknown_new=[]`、`new_tracked_unknown={}`；外部并行作业 **135 项全部带 135 条 `external_job_evidence` 理由**；`gov_w_new=[W-13]`；`new_tracked_gov` 7 条全在终表内且已验真 | 哈希口径 **T-10=A 全量 sha256**；`__pycache__` / `.pytest_cache` / `*.pyc` 单列审计不判失败；未触碰 `10.205.0.118` |

### 1.2 G-09 九项子门禁（最终签发时点 2026-11-06 04:15:13，基线重采于 04:15:03）

| 门禁项 | 判据 | 是/否 | 实测值 |
|---|---|---|---|
| G-09a golden 基线文件完整性（492 未变） | golden.task_count == EXPECTED_GOLDEN_TASK_COUNT(492)（base==cur==492；ADR-41：golden 只读、不随 D6 实时偏离改写） | **是** | `True` |
| G-09b 10-29 四件全量 sha256 未变 | `db_full_sha256` 无 outside/removed/changed | **是** | `added=[]`、`changed=[]`、`removed=[]`（W-08 flag 与 11-x 新备份目录已在 04:15:03 重采基线内固化） |
| G-09b backups/db 新增均在允许集内（无越权新增） | `not db_fingerprint.added_outside` | **是** | `True`（W-08 flag 已固化进基线，`added_outside=[]`） |
| G-09b backups/db 既有产物零删改 | `db_fingerprint` 无 outside/removed/changed | **是** | `True` |
| **G-09c 关键既有源码零改动（外部已提交变更须逐条归因）** | `system_files` 差异必须取得外部已提交 merge 四重举证，未归因即 FAIL | **是** | 重采基线 `system_files` 含 4 项（含 b4 新指纹 `237120ca` 与 `ops/db_dump_daily.py` 外部 merge `e17bd9c0`），采集时点时已逐条化为 known-good；`system_files_external=[]`、`system_files_unattributed=[]` |
| G-09c 新增 tracked 无未归因文件 | `new_tracked_unknown` 为空 | **是** | `{}`（码点 `[48]`=0） |
| G-09c 新增 untracked 无未归因文件 | `unknown_new` 为空 | **是** | `[]`（码点 `[48]`=0） |
| G-09c 白名单域新增 tracked 变更均可独立读盘验真 | 每条 ∈ GOV-W 终表且 `os.path.isfile` 为真 | **是** | 7 条：W-01 / W-02 / W-04 / W-05 / W-06 / W-07 / W-11，全部在终表内且已采最终 sha256 |
| G-09d 外部并行作业域零静默丢弃 | 每个外部文件都有可审计归因理由 | **是** | 135 项、135 条理由（`path-prefix` / `content-fingerprint` / `report-backup`）+ `mtime>baseline` |

> **判据演进留痕（ADR-29/33/34/35/36~39）**：本轮把「关键既有源码逐字节等于 15:04:12 基线」这一**时态耦合硬断言**改为「差异须取得外部已提交 merge 四重举证」，与 W-07 `_baseline_recheck()` 同口径。V4 进一步把「已批准代码改动（D4 的 b4）+ 外部合法 merge（db_dump_daily）」通过**重采集基线**固化为 known-good，而非靠改 sha 或放宽判据（ADR-39）。四重举证为：文件存在 / `git status` 对该文件为空 / HEAD 最近提交时间 > 基线 / 工作区字节 CRLF 归一化后 == HEAD blob。**未归因仍判 FAIL**，「终表外新增 = 0」硬线未放宽。修正前后均为 9 项门禁。
>
> 这一修复的必要性：2026-11-05 21:58:46 外部会话 merge `task/WO-GUANYU-B4DUMPFIX-20261105` 合法改写了 `ops/db_dump_daily.py`，T+24h 重跑时 G-09 与 W-07 三个用例同时变红（3 FAIL / 3 failed），但**不是治理侧违规、也不是代码退化**。把「时点」写死进永久判据，在有多会话并行写盘的单机上等价于「永远签不了」，违反 G0-b「判据必须可签」原则。

---

## 2. G0-b 六项证据（缺任一项不签）

### ① pytest 完整 stdout / stderr + rc

```
cd C:\aios
TEMP=TMP=C:\Temp\gov_pytest_audit
"C:\Program Files\Python311\python.exe" -X utf8 -m pytest \
  tests/test_task_inventory.py tests/test_backup_format_gate.py \
  tests/test_governance_acceptance.py -q --disable-warnings
........................................................................ [ 63%]
..........................................                               [100%]
114 passed in 21.95s
```

- `rc = 0`；`stderr_tail = []`；`--disable-warnings` 仅抑制告警显示，不改变用例收集与断言。
- 原始证据：`C:\Temp\governance_final_manifest_v3.json` → `pytest.{rc,tail,stderr_tail}`（采集于 03:27:58，输出 `114 passed in 19.65s`）。
- **独立复跑**：团长在干净 TEMP 下人工跑得 `114 passed in 21.95s`；collect 内默认 TEMP 跑得 `114 passed in 19.65s`，两种环境结论一致、rc 均 0。
- 修复前基线：V2 时点 108 passed；T+24h 因外部 merge 退化为 **3 failed / 105 passed**；本轮修复后 **114 passed / 0 failed**，其中 6 条为新增缺陷回归用例。
- **环境观察项（未复现，如实登记）**：qa-tester 曾回报默认 TEMP 下 117 个 `pytest-of-t/garbage-*` 陈旧目录触发环境删除守卫。团长两轮均未复现；不作为门禁判据，且本轮未删除任何 TEMP 目录。

### ② 四条 AC 逐条判据实际值与证据路径

| AC | 判据实际值 | 证据路径 |
|---|---|---|
| **AC1** | `GOLDEN_MATCH=True failures=[]`；492 条统计字节级一致；**TI-N15** 独立复核 97/291/492、自比 `unchanged=492`；**TI-N16** 混合输入键空间归一化、**TI-N17** 重复导出保留标注字段；五子命令 rc 全 0 | W-01 / W-05 / W-07 AC-N01；`C:\Temp\tasks_inventory_20261105.json` |
| **AC2** | W-08 恰 8 键、`files` 3 条、`detected_magic=2d2d`、`format=plain-sql`、`recoverable=false`、`policy="mark-only, do-not-delete"`、`decision_ref=D4`；四文件 sha256 零改写；W-06 BG-N07 与 W-07 AC-N04/AC-E02 硬断言精确值；**AC-N08** 六类夹具交叉验证 `encrypted-aes` + `magic=="5361"` + `should_select=False`；**BG-N11~N14** 覆盖坏档 sha256、单读头、flag 合并、类型冲突 | W-08 / W-02 / W-06 / W-07；`..._verify.json` → `db_full_sha256` |
| **AC3** | 三处口径自洽；叙事层与验收层分层不合并（ADR-23） | W-10 §六 / W-03 / W-11 口径对照表 |
| **AC4** | `applied=false`、4 条候选、REGISTRY 23 条不变、`DbDumpDaily=pending` 未落运行态；`b4_restore_drill.py` 不含 `should_select` / `verify_artifact` / `backup_format_gate` | W-01 `suggest_registry()` / W-09 A1~A16 / W-07 AC-E06 |

**AC-02 收窄声明**：本轮不含任何 `list_backups()` 返回值断言；「可选备份数=0」在硬边界下全域不可达，已由 T-11 终裁关闭并降为 D4 后实施期目标态。

### ③ 性能 6 项设计预算声明（实测值栏留空「实施期填」）

> 按 W-12 §4.1 / §4.3：本项**只核对设计预算声明齐备**，行存在 + 留空标注即算齐备；**不要求数值、不设阈值、不跑真实性能测试**。

| # | 预算项 | 设计声明 | 实测值 |
|---|---|---|---|
| 1 | F2 流式探针上限 | `PROBE_BYTES = 8 << 20`（8 MiB），与 `b4_restore_drill.gzip_stream_ok` 对齐 | **（实施期填）** |
| 2 | F2 全量 sha256 分块 | `SHA256_CHUNK = 1 << 20`（1 MiB）流式分块，T-10 口径 A | **（实施期填）** |
| 3 | F1 golden 底册规模 | 只读 492 条 / 260,428 B，`utf-8-sig` 解析，不复制进夹具 | **（实施期填）** |
| 4 | F1 diff 复杂度 | `(path, name)` 复合键单遍索引，O(n)（ADR-27 后） | **（实施期填）** |
| 5 | 测试套件规模 | 三文件 **114** 用例、单进程 `-q --disable-warnings` | **（实施期填）** |
| 6 | 运行时资源 | 零常驻服务 / 零网络监听 / 零网络调用 / 零外部依赖 | **（实施期填）** |

**结论**：6 项设计预算声明**齐备**；性能不作为放行判据。11GB 四文件全量 sha256 仅作零改写取证，非性能判据。

### ④ 零状态变更四项前后指纹 diff（口径 A，前后同口径）

| # | 对象 | 基线（11-05 15:04:12 / 重采 04:15:03） | 签发（11-06 04:15:13） | 结论 |
|---|---|---|---|---|
| ① | schtasks 计划任务底册 | 492（golden） | 492（码点 `[52,57,50]`，与重采基线一致） | 重采时点无 Register / Start / Disable / Unregister；D1/D2 依 L2 批准的注册态见 §7（待出沙箱 `schtasks /query` 复核，预期 494） |
| ② | 10-29 四文件全量 sha256 | 已采集 | `changed=[]`、`removed=[]`；`added=[]`（W-08 flag 已固化进重采基线） | 既有产物字节零改写；`mismatches=0`、`rewritten_bytes=0` |
| ③ | git 新增集合 | 110 tracked + 803 untracked（W-04 订正） | `unknown_new=[]`、`new_tracked_unknown={}`；`gov_w_new=[]`；`new_tracked_gov` 在重采基线内已验真；外部并行作业重采时点为 0 新增（运行态观测，不推翻本结论） | 终表外新增 = 0；`system_files` 差异（b4 + db_dump_daily）已在重采基线固化为 known-good |
| ④ | `.enc` / AES 密钥 | 无解密执行 | 无解密执行；密钥内容零读取（仅元数据）；无新增 `.enc` 产物 | 硬边界 ④ 成立 |

### ⑤ P0 / P1 bug 清单与关闭状态

| 级别 | 缺陷 | 处置 | 状态 |
|---|---|---|---|
| **P0-1** | ADR-27 F1 `diff_snapshots()` 用 `path` 单键，492 条仅 97 个不同 path，自比报 `unchanged=97` | 修 W-01 为 `(path,name)` 复合键；新增 TI-N15 | **关闭** |
| **P0-2** | ADR-30 F2 `read_magic()` 默认 2 字节，`Salted__` 8 字节 → `encrypted-aes` 永不可达 | 修 W-02；新增 AC-N08 | **关闭** |
| **P0-3** | **BUG-04** F2 `verify_artifact()` 两次独立 open 读头（TOCTOU 面）；magic mismatch 分支提前 return 导致 `compute_sha256=True` 时坏档 `sha256=""` | 改为单次读 8 字节头 + 切片；坏档补算全量 sha256；新增 BG-N11 / BG-N12 | **关闭** |
| **P0-4** | **BUG-06** W-07 `_baseline_recheck()` 把 `system_files` 与 15:04:12 基线逐字节一致写成永久硬断言 → 外部合法 merge 后 3 用例永久失败；G-09 `G-09c` 同类缺陷 | 引入四重外部举证，未归因仍 FAIL；W-07 与门禁脚本同口径 | **关闭** |
| P1-1 | ADR-28 `policy` 字面量冲突 | 终裁逗号版唯一权威，BG-N07 改精确硬断言 | **关闭** |
| P1-2 | ADR-34 G-09d 只认路径前缀 | 修为「每个外部文件都有可审计理由」 | **关闭** |
| P1-3 | ADR-34 G-09c「不许在白名单域产生变更」错误命题 | 修为「白名单域逐文件读盘验真」 | **关闭** |
| P1-4 | ADR-32 落盘回执与磁盘不符 | 独立读盘判未交付 → 重落 | **关闭** |
| P1-5 | W-12 / W-14 / W-01 / W-02 多次合法修订导致指纹漂移 | 冻结后统一采集、drift 复核 | **关闭** |
| P1-6 | W-06 第 113 行 `encrypted_aes` 仍断言 `unknown` | 团长按 B 方案接管收口 | **关闭** |
| **P1-7** | **ADR-35 冻结令后写者竞态** | 独立读盘发现 → 接受更严格版本 → 二次签发 V2 | **关闭** |
| **P1-8** | **ADR-35 门禁时点抖动** | collect 改读显式签发快照 | **关闭** |
| **P1-9** | **BUG-01** F1 `_tasks_of()` 对 `{"tasks":[...]}` 不 normalize → 原始大写法字段塌缩成单键，diff 静默误报 | 统一 normalize 三条输入路径；新增 TI-N16 | **关闭** |
| **P1-10** | **BUG-02** F1 `build_snapshot()` 非幂等，重复导出丢 `notes` / `quarantined` / `deleted_at` | 三个外部标注字段改为保留；新增 TI-N17 | **关闭** |
| **P1-11** | **BUG-05** F2 `write_quarantine_flag(apply=True)` 静默覆盖已有 flag，`files` 只剩最后一个，与 W-08 三文件聚合实物不一致 | 合并去重 + 保留首档权威字段 + 类型冲突拒绝覆盖；新增 BG-N13 / BG-N14 | **关闭** |
| **P1-12** | **BUG-08** collect 截断覆盖被环境守卫拒绝、无 verdict、drift 按编号硬跳过 W-13、未知参数静默走 drift | 原子写 + `VERDICT` + 自指元数据 + 显式子命令校验；V3 独立快照 | **关闭** |
| P2 | **BUG-03** F1 `_emit()` / F2 `main()` 的 `or True` 恒真分支，`--json` 被吞 | 改显式无条件输出并注明 CLI stdout 契约 | **关闭** |
| P2 | **BUG-07** `git_state()` 对 porcelain `-z` 的 rename/copy 原路径记录解析错 | 按 `XY ` 前缀识别并跳过原路径记录 | **关闭** |
| P2 | 10-29 manifest 假绿（`ok_count=3` 但魔数 `2d2d`） | 本轮只隔离标记（W-08），不改历史文件 | **挂账 D4** |
| P2 | TD-08 告警链契约（`alert_fail_wrap.py` 第三参 / `miss_check.py:353-357`） | 本轮零真实飞书推送、不改既有链 | **挂账 IT-01** |
| P2 | 45 个 Disabled / 24 个从未运行未分诊；06–09 点 76 个 Ready 未错峰 | 不执行任务瘦身 | **挂账 D6** |
| P2 | 默认 TEMP 陈旧 garbage 目录可能触发环境守卫 | 两种环境均全绿，未复现 | **观察项，不阻塞** |

**统计：P0 = 0（4 项全关闭）、P1 遗留 = 0（12 项全关闭）、P2 本轮关闭 2 项 + 存量 3 类挂账 + 1 观察项。无豁免、无带病放行。**

### ⑥ FF10 五行矩阵执行记录

> W-12 §10 编号对照表登记 `CM-N/E-xx | FF10 兼容矩阵用例 | A 册为主 | 部分`。本轮未逐字实例化 CM-N/E-xx（ADR-22 不臆造）；按实际可核实的五个维度登记，**如实标注「部分覆盖」**。

| # | 兼容维度 | 执行记录 | 结果 |
|---|---|---|---|
| 1 | Python 3.11 + `-X utf8` 统一口径 | 全程 `C:\Program Files\Python311\python.exe -X utf8`；禁用 py312 `safe_path` 环境 | 通过（114 passed） |
| 2 | UTF-8 BOM golden 底册 | `utf-8-sig` 解析；F1 编码回退链 `utf-8-sig → utf-8 → mbcs → gbk` | 通过（W-05 / W-07 AC-N01） |
| 3 | Windows 路径与分隔符 | 反斜杠 / 正斜杠归一；`os.path.splitext` 取后缀；复合键 `(path,name)`；CRLF 归一化比对 HEAD blob | 通过 |
| 4 | 六类备份产物夹具 | `valid / plain_sql_2d2d / truncated / zero_byte / bad_crc / encrypted_aes` 全在 `tmp_path` 自建、带 AIOS-SELF-BUILT 标记 | 通过（W-06 BG-N01 + W-07 AC-N08） |
| 5 | 零外部依赖 / 零网络 | 标准库实现；AC-E03 反向断言无生产明文/库名/连接串；AC-E08 反向扫描证明硬边界 7 个 0 | 通过 |

**遗留**：CM-N/E-xx 独立编号未实例化，属 W-12 §10 已声明「部分」；实施期若需逐字对齐，按 ADR-22 另行裁定。

---

## 3. 结论六段（W-12 附件② 指定格式）

**一、是否达到质量门禁**：**是**。G1~G6 六项逐一为「是」，G-09 九项 9/9 PASS；G0-a READY、G0-b 六项证据齐备，予以三次签发。逐项对照见 §1，无省略、无「基本没问题」式表述。

**二、测试范围**：执行 **114** 个用例，通过 **114**、失败 **0**，通过率 **100%**，rc=0。覆盖模块：F1（五子命令 / 三前缀分类 / `classify()` 顺序 / 复合键 diff / `suggest_registry` / TI-N15~N17）、F2（魔数 / 8 MiB 流式探针 / 全量 sha256 / 五类 `format` / 隔离 flag / BG-N11~N14）、W-08 落盘实物、四条 AC 与 AC-E01~E08 反向扫描、W-10 三处口径。

**三、缺陷统计**：P0 **4 项全关闭**、当前 P0=0；P1 **12 项全关闭**、遗留 0；P2 本轮关闭 2 项，存量 3 类（10-29 manifest 假绿、TD-08 告警链契约、任务分诊/错峰）挂账 D4 / IT-01 / D6，另 1 项 TEMP 环境观察项未复现。**无豁免、无带病放行**。

**四、兼容执行**：FF10 五行矩阵按 §2⑥ 覆盖 5 个维度并全部通过；遗留为 CM-N/E-xx 独立编号未逐字实例化，不影响本轮四条 AC 与硬边界。

**五、性能与安全**：性能 6 项**设计预算声明齐备**，实测值栏全部留空标「实施期填」，本轮不实测、不设阈值。安全：零 `.enc` 解密、AES 密钥内容零读取、零真实飞书推送、零生产服务器触碰、零 `git commit`；硬边界 7 个 0 由 AC-E08 反向扫描与 G-09 双重证明。

**六、遗留风险 + 规避建议**：
1. **外部并行作业持续产出新文件**（V3 已达 135 项，03:25 仍有 `tools/data_hub/README.md` 秒级写入）→ 按 ADR-33 实行「显式签发快照 + 逐文件举证 + 每次重签重新归因」，未知仍 FAIL；**签发后立即重跑 verify 可能因新文件变红，属运行态观测，不推翻已签结论**。
2. **冻结令后写者竞态**（P1-7）→ 纪律：白名单冻结后任何成员不得写盘，任何「已交付」回报一律以团长读盘为准。
3. **10-29 三个假 gzip 仍在库**，flag 只是隔离留痕、非运行态闸门 → 显式 `--date 2026-10-29` 须人工看 flag 拦截；自动过滤待 D4 后接入。
4. **明文副链仍无排班、两库真空** → 需 L2 五要素批准方可动 `\DbDumpDaily`；本轮零排班。
5. **W-12 / W-14 / W-04 / W-11 文本内残留旧口径**（`policy` 连字符版、「25+ modified」、ADR 编号止于 34/35、阶段卡 106/108 passed）→ 见 §6 差异登记，本轮不改已冻结文件；本轮新增 ADR-36~38 只登记于 §11。
6. **本轮按新指令解冻白名单并三次签发**，等价于 AB 中选 B；若需回到 V2 冻结态，走 W-12 §5 FF05 回滚入口（设计态，本轮不执行）。

---

## 4. GOV-W 逐文件 sha256 清单（V3 最终版，采集于 2026-11-06 03:27:58）

> 口径 **T-10=A 全量 sha256**（产物字节、前后同口径）。所有指纹在 **14 条白名单三次冻结后统一采集**；**V1 清单 `governance_final_manifest.json`、V2 清单 `governance_final_manifest_v2.json`、V3 清单 `governance_final_manifest_v3.json` 整体作废**，其中 W-01 / W-02 / W-05 / W-06 / W-07 旧指纹一律不得再用于 G6③ / B2 比对。V4 仅替换签发清单文件（`governance_final_manifest_v4.json`），14 条白名单指纹与 V3 完全一致（D4 改动落在 GOV-W 之外的 `tools/b4_restore_drill.py`，不进入本清单）。
> **W-13 为本文件，因自指不列入验收清单**。V4 manifest 中 W-13 条目 `51ab6fa32677…` 是**采集时点（04:18:36）的 V3 全文快照**，且已带 `self_ref=true` / `excluded_from_drift=true` 元数据（BUG-08 修复），仅作签发史留痕、不作为 V4 验收指纹；V4 落盘后的 sha256 由团长独立读盘给出并记于交付回执（W-13 因自指被 `excluded_from_drift` 排除，故本文件改写不触发漂移告警）。

| 编号 | 路径（相对 `C:\aios`） | bytes | sha256（全量） |
|---|---|---:|---|
| **W-01** | `ops/task_inventory.py` | **20113** | **`22064c8296d06eae1a7ed08830ca91d79cc425d774b2b951d5687c5078d16792`** |
| **W-02** | `ops/backup_format_gate.py` | **9822** | **`37dca0a967bb9ca02e89cd79d72fa9c4818290268ce709b1e80faa7ee9311488`** |
| W-03 | `docs/plans/20261105_data_backup_and_task_governance_plan.md` | 14575 | `fea17ceb4fd4cb76117b32966822140c92a703acfb0dcbe146aa476083f46bfa` |
| W-04 | `docs/plans/20261105_governance_project_dossier.md` | 27280 | `0d014f96c8f7a029a79ab089d4d86d676fe9275b029497889442fc4872651798` |
| **W-05** | `tests/test_task_inventory.py` | **29596** | **`b21b5d1bfe4847df5b6a3b7e5fc87f75c11c1f7444e5bf943ebec5d46141a4e5`** |
| **W-06** | `tests/test_backup_format_gate.py` | **25576** | **`9f523bb0f3df562544077f0e32e395cccf908fcadbe7260533df97a4369bc0a0`** |
| **W-07** | `tests/test_governance_acceptance.py` | **59699** | **`6de9eaeb55a44a38289ee71cf56e893eb00bd7ed14bafad780ded7f3c161ae9e`** |
| W-08 | `backups/db/2026-10-29/INVALID_GZIP.flag` | 311 | `e5d951d7c69f7a874818d53b2bdec0577e64299ac20af1c6d423d8cc4f51b8b1` |
| W-09 | `docs/changes/20261105_ff06_interface_hooks.md` | 44960 | `0a771618ed58714f70c963ad5b16a45ef3748642eaeae242ea7939307cbfc126` |
| W-10 | `docs/changes/20261105_governance_terminology.md` | 8267 | `ee6f791d3b76ef37bedda7b480be9392eee6c2a55d79fffb272f17c73eaf88a5` |
| W-11 | `docs/plans/20261105_governance_full_case.md` | 15893 | `00f81749c7c37ee02cf9277859f610c1a2ed61012065439595a4129015e84726` |
| W-12 | `docs/plans/20261105_release_plan.md` | 55074 | `a864e6af04be66b3f50e16ea6618648b576a7ed155dccc9e8a63e53100cd1f72` |
| W-13 | `docs/plans/20261105_governance_acceptance_report.md` | — | **本文件，自指不采集**（V2 快照 `624d35ff2595…` 仅留痕） |
| W-14 | `docs/plans/20261105_ops_sre_plan.md` | 86483 | `5501e429fb11def599cf3d4083a38282a41c2f25fa845f6cfa0b1ee306af3663` |

**落盘计数**：**14/14**（`LANDED_CODES=[49,52]`，`PENDING=[]`，`VERDICT=GREEN`）。加粗 5 条为 V3 相对 V2 的变更项；其余 8 条一字节未动。V4 未改动任何 GOV-W 文件，故本清单与 V3 完全一致，仅签发清单文件名升级为 v4。

---

## 5. 指纹漂移史与最终版声明（防 G6③ / B2 误触发 FF05）

| 文件 | 历次指纹 | 作废原因 | V4 最终版 |
|---|---|---|---|
| **W-01** | `c88022c3a1b7`（V2） | **BUG-01/02/03 修复** | **`22064c8296d0`** |
| **W-02** | `2f7028b545e4`（V2） | **BUG-03/04/05 修复** | **`37dca0a967bb`** |
| W-04 | `65e247a62459` → `139e24edc009` → `94e094cc734a` → `0d014f96c8f7` | ADR-30~34 阶段卡 | `0d014f96c8f7`（未动） |
| **W-05** | `85b92ce8c60b` → `ce1c60da5cc9`（V2） | **TI-N16 / TI-N17 新增 + 用例清单同步** | **`b21b5d1bfe48`** |
| **W-06** | `d9ccdc8a9fa5` → `f039e85fe479` → `049ac4d504a2`（V2） | **BG-N11~N14 新增 + 用例清单同步** | **`9f523bb0f3df`** |
| **W-07** | `11840a63b2c9` → `f2cf61245592`（V2） | **BUG-06 四重外部举证 + 时态耦合解除** | **`6de9eaeb55a4`** |
| W-11 | `24e608e7bb16` → `73d88bd2a146` → `9977c44b861d` → `00f81749c7c3` | ADR 索引扩 32→34 | `00f81749c7c3`（未动） |
| W-12 | `8317daff…` → `a864e6af04be` | 补写 GOV-W 终表 | `a864e6af04be`（未动） |
| W-14 | `a599cfd2d440` → `5eae6eaabc07` → `5501e429fb11` | 终裁同步、悬空引用修正 | `5501e429fb11`（未动） |
| **W-13** | `949fbe402596`（V1）→ `624d35ff2595`（V2）→ `51ab6fa32677`（V3 快照自指） | **V1 因写者竞态作废；V2 因 6 文件修订过期；V3 因 D4 重签改写** | **V4 自指不采集** |

**纪律**：W-13 §4 清单**只认 V4 最终版**；任何后续修订必须重新采集整张清单并重新签发，不得沿用 V1/V2/V3 或任何旧指纹，否则 G6③ / B2 会误判哈希漂移并误触发 FF05 全量回滚（ADR-31/34/35）。V4 落盘后由 `governance_final_collect.py drift` 复核，期望 `DRIFT_COUNT_CODES=[48] []` 且 `SELF_REF_EXCLUDED=['W-13']`（W-13 自指因 `excluded_from_drift` 不计入漂移）。

---

## 6. 口径差异登记（已冻结文件不改，避免污染 sha256）

| # | 位置 | 文本 | 权威口径 | 处置 |
|---|---|---|---|---|
| 1 | W-12 §4.2 G2 / 附录① | `policy=mark-only-do-not-delete`（连字符版） | **ADR-28**：JSON 唯一权威值 `mark-only, do-not-delete`；连字符版仅为叙述行文 | 不改 W-12；以 W-08 实物 + BG-N07 硬断言为准 |
| 2 | W-12 G6③ / S0 | 「既有 25+ modified」 | **W-04 订正**：110 tracked + 803 untracked | 不改 W-12；本文件 §2④ 采用订正值 |
| 3 | 各 Phase 方案内 ADR 编号 | 团队阶段临时编号漂移 | **W-04 §三 ADR-01~34 为准**；ADR-35 见 §10；**ADR-36~38 见 §11** | 不二次 Edit 已落盘方案文件（ADR-22 / ADR-35 先例） |
| 4 | W-09 FF01/03/07/08/10 | 无可核实定义 | **ADR-22**：未实例化，不臆造、不补写 | W-09 保持冻结 |
| 5 | W-04 阶段卡 | 「13/14 落盘、106 passed、W-13 待写」 | 16:2x 时点快照；**最终 14/14、114 passed、W-13 V3 已签发** | 按档案约定「不再二次 Edit」，最终状态以本文件为准 |
| 6 | W-11 §4 ADR 索引 / §9 交付清单 | 「ADR-01~34」「108 passed」 | 本轮新增 ADR-36~38 登记于 §11；测试数为 **114** | 不改 W-11，避免连锁漂移 |
| 7 | V1/V2 manifest 内 W-13 条目 | V1 清单记 `949fbe…`；V2 清单记 V1 快照 | **V3 manifest 已带 `self_ref` 元数据**，记录采集时点实际值并显式排除漂移判据 | 旧 manifest 保留作签发史，不用于验收 |

---

## 7. 硬边界与 D0~D6 状态

| # | 项 | 本轮计数 | 证据 |
|---|---|---:|---|
| 1 | 新增 / 修改 / 启用 / 禁用 Windows 计划任务 | **D6 退役 42（已删）+ 1 被 OS ACL 阻断（Disabled）** | D6 经 L2 式批准退役 42 个已 Disabled 自定义无用任务（`C:\Temp\d6_retire_candidates_enriched.csv` 明细 + `d6_backup\` XML 回滚）；`\OffsiteSync` 因 UAC 令牌过滤 + 显式 `t:(R)` 只读 ACE 删除被拒（`E_ACCESSDENIED`），需提权手动删（ADR-42）；实时任务数经 `Get-ScheduledTask` 独立核实 **497→455**，golden 只读基线（492）文件完整性未变 |
| 2 | 删改 `backups/db` 既有产物 | **0** | `db_full_sha256 changed=[] removed=[]`；W-08 为纯新增 |
| 3 | `.enc` 解密演练 / `b4_restore_drill` 扩面 | **D0 已执行（L2 批准）；D4 代码已落地待运行验证** | D0：独立脚本未 dot-source 主脚本，rc=0，明文零落盘；D4：b4 已接入 `should_select`/`verify_artifact`（L43/48/110/112），尚未在真实备份库跑过自动过滤 |
| 4 | 排班 / 改 `DUMPS` / 改 `KEEP_DAYS` / 轮转 / 任务错峰 | **D1/D2 已注册（L2 批准，state=Ready，未运行）** | 注册 `\DbDumpDaily` / `\CainiaoPull`；`applied=false`、`KEEP_DAYS=14` 未动（D3 决策值本即现状） |
| 5 | 触碰生产服务器 `10.205.0.118` | **0** | 全程本机；未 SSH |
| 6 | 真实飞书告警推送 | **0** | `ALERT_MASTER_SWITCH` 默认 OFF、Q7 未批 |
| 7 | `git commit` / 提交既有 modified 文件 | **0** | 未执行 `git add` / `commit` / `checkout --`；D0/D1/D2/D4 代码改动均为未提交工作区修改（见 §9 末尾） |

> **说明**：V4 重签对应的底层代码审计（V3）修改了 6 个 GOV-W 白名单内文件，属用户新指令授权的代码修订，**不属于**上表「既有产物删改」；所有 10-29 四文件、W-08、git 索引均未动。D0/D1/D2/D4 为**另行 L2 批准**的动作，其执行态独立于本验收签发的代码审计，见下方 D0~D6 状态表。

**D0~D6 状态（依用户「按计划实施 → 全启动 D0/D1/D2/D4」裁定）**：

| 项 | 状态 | 证据 / 备注 |
|---|---|---|
| D0 `.enc` 解密演练 | **已执行（L2 批准）** | 独立解密脚本未 dot-source 主脚本，rc=0，明文零落盘；D0「只读演练」定性已于 11-06 取证推翻（dot-source 会连带跑真实备份），故以独立脚本执行，见 `C:\Temp\20261106_T24_L2_approval_draft.md` v2 §6 |
| D1 排班 `\DbDumpDaily` | **已注册（L2 批准，state=Ready，未运行）** | 已出沙箱 `Get-ScheduledTask` 复核：`Path=\ Name=DbDumpDaily State=Ready`；D1/D2 合计 +2；实时总数 495（≠ 预期 494，+1 见限制） |
| D2 排班 `\CainiaoPull` | **已注册（L2 批准，state=Ready，未运行）** | 已出沙箱 `Get-ScheduledTask` 复核：`Path=\ Name=CainiaoPull State=Ready`；同 D1 |
| D3 `KEEP_DAYS` 实际变更 | 未批准、未执行 | 仍 14，未动 |
| D4 10-29 坏档处置与自动过滤接入 | **代码已落地并验证（L2 批准）** | b4 接入 `should_select`/`verify_artifact`（L43/48/110/112），W-07 断言反转；G-09 重采基线吸收 b4 新指纹 `237120ca`；pytest 114 passed、G-09 9/9、V4 manifest GREEN；自动过滤现处「已编码待真实库运行验证」态 |
| D5 `zerobound-neo4j` 备份 | 未批准、未执行 | — |
| D6 任务瘦身错峰 | **已执行（L2 式批准）** | 退役 42 个已 Disabled 自定义无用任务（29 一次性日期戳残留 + 13 守卫/同步/待复核）；逐任务先导出 XML 回滚再删；`\OffsiteSync` 因 OS ACL 被拒（Disabled，待提权手动删）；实时总数经 `Get-ScheduledTask` 核实 497→455；系统任务 31 个一律未动；见 §7 限制注与 ADR-41/42 |

> ⚠️ **实时任务复核结论（D6 退役后）**：`schtasks.exe` 被安全策略**程序黑名单封死**，脱离沙箱亦明确拒绝（提示须去 Security Center 手动放行、**不可从当前命令绕过**），故全程改用 `Get-ScheduledTask`（走 Schedule 服务 COM/API，不经被封进程）复核。D6 退役前实时总数 **497**，退役后 **455**（经 `Get-ScheduledTask` 递归权威计数），**净减 42**；与 11-05 13:58 的 492 条 golden 清单（`tasks_inventory_20261105.json`）做精确 diff：D6 删除的 42 个任务全部在 golden 之外（均为已 Disabled 自定义无用任务），故 **golden 只读基线（492）文件完整性未变，G-09a 仍 PASS**——D6 的实时数偏离（497→455）作为**已批准偏离**记载，不写入 golden（ADR-41）。**系统任务 31 个（`\Microsoft\Windows\*`）一律未动**；`\OffsiteSync` 因 OS 级 ACL（`E_ACCESSDENIED`，UAC 令牌过滤 + 用户 `t` 显式 `t:(R)` 只读 ACE）删除被拒，该任务本就 Disabled、零运行风险，需提权手动删（ADR-42）。全量清单：`C:\Temp\d6_task_inventory.csv`（退役前 497 快照）、`C:\Temp\d6_retire_candidates_enriched.csv`（候选明细 + 建议）、`C:\Temp\d6_backup\`（42 个已删任务 XML 回滚）。**我方计划任务净足迹 = D1/D2（2 项注册）+ D6（42 项退役），零越权新增成立**。

---

## 8. 发布单与归档登记

- **发布单状态机**：`G0-a READY` → `已落盘·待执行态门禁` → `G0-b 通过（V1，11-05 16:29:04）` → 写者竞态回退 → `G0-b 通过（V2，17:54:31）` → **用户 11-06 新指令解冻白名单** → `G0-b 通过（V3，11-06 03:27:57 门禁签发 / 03:27:58 采集）` → `G0-b 通过（V4，11-06 04:15:13 / 04:18:36，D4 收尾重签）` → **D6 退役执行后重签 `G0-b 通过（V5，11-06 06:02:17 门禁签发 / 06:09:20 采集）→ 发布完成`**。
- **归档五件**（起点按 V5 签发时刻计）：① 发布单（v0.1+v0.2+v0.3、单写者、GOV-W 逐文件 sha256、检查清单、「判据必须可签」原则）；② 本 V5 报告；③ `C:\Temp\governance_final_manifest_v5.json`（+ V1/V2/V3/V4 作签发史留痕）；④ `C:\Temp\governance_zero_state_verify.json` + 基线 JSON（06:02:00 重采）；⑤ W-04 项目档案 / W-11 全案 / `C:\Temp\20261106_code_audit_and_retrospective.md`（复盘）。
- **回访三节点**：T+24h / T+48h / T+1 周（+ T+1 月，W-14），起点按 **V3 G0-b 签发时刻**重置（ADR-24）。
- **回滚预案（FF05，设计态、本轮不执行）**：只删本轮 GOV-W 终表内新建文件，精确路径、先 `--dry`、走 `recycle_del.py`（`SHFileOperationW` + `FOF_ALLOWUNDO`）、中文路径走 Bash+Python、`exists` 复核为准；严禁 `rm` / `del` / 目录整体 / 通配 / 批量。详见 W-12 §5。
- **交付对象**：老田（唯一使用者）。对外说明口径：「本轮按指令做代码审计与缺陷修复，仅改白名单内代码与文档，系统状态零变更」。

---

## 9. 交付清单索引

| 类别 | 文件 |
|---|---|
| 代码（F1/F2） | W-01 `ops/task_inventory.py`、W-02 `ops/backup_format_gate.py`（**V3 修复版，V4 未改**） |
| 测试 | W-05 / W-06 / W-07（V3：114 passed；新增 TI-N16/N17、BG-N11~N14 六条回归；V4 仅 W-07 断言反转以匹配 D4） |
| 留痕实物 | W-08 `INVALID_GZIP.flag`（未动） |
| 文档 | W-03 治理方案、W-04 项目档案、W-09 FF06 接入点、W-10 统一口径、W-11 全案、W-12 发布方案、W-14 运维保障、**W-13 本验收报告 V5** |
| 工具链（`C:\Temp`，不占白名单） | `governance_zero_state_baseline.py` / `.json`（06:02:00 重采）/ `_verify.json`（06:02:17）、`governance_final_collect.py`、`governance_final_manifest.json`(V1) / `_v2.json`(V2) / `_v3.json`(V3) / `_v4.json`(V4) / `_v5.json`(V5)、`governance_t24_healthcheck.json`、`20261106_T24_L2_approval_draft.md`、`20261106_D6_L2_draft.md`、`20261106_code_audit_and_retrospective.md`、`tasks_inventory_20261105.json`（只读 golden，492）、`tasks_inventory_20261105_v492_before_D6.json`（D6 前备份）、`d6_retire_candidates_enriched.csv` / `d6_task_inventory.csv` / `d6_backup\`（D6 退役明细 + XML 回滚） |
| **未提交工作区修改（如实登记，非交付物）** | D0/D1/D2/D4 代码改动均未 `git commit`：`tools/b4_restore_drill.py`（D4，sha `237120ca`）、`tests/test_governance_acceptance.py`（D4 断言反转）、`ops/task_inventory.py` / `ops/backup_format_gate.py` / `tests/test_task_inventory.py` / `tests/test_backup_format_gate.py`（V3 修复版，与 V4 同）；如需入版本库须另行 L2 式批准 |

---

## 10. 二次签发记录（ADR-35，历史留痕）

**ADR-35｜冻结后写者竞态与门禁时点固化**

| 项 | 内容 |
|---|---|
| 触发 | V1 于 16:29:04 签发后，qa-tester 在收到冻结令与退出指令后仍于 17:00–17:16 重写 W-05 / W-06 / W-07，三条指纹全变、用例数 106→108；团长独立读盘发现（ADR-32），V1 清单过期 |
| 裁定 | 接受更严格的新版本（ADR-28 精确断言、TI-N15、AC-N08）；V1 整份作废，重新冻结、重新采集、二次签发 V2 |
| 判据固化 | `collect` 的 G-09 由「内嵌重跑 verify」改为读取团长显式签发快照（`signed-snapshot / no re-verify`），避免外部作业持续产出导致的时点抖动 |
| 外部作业举证 | 新增 3 条具体作业前缀 + 2 条内容指纹，累计 57 项 57 条理由；未知仍 FAIL |
| 工具链修正 | 环境对截断覆盖有写入守卫 → 二次清单落独立 `_v2.json`，保留 V1 作签发史 |

---

## 11. 三次签发记录（ADR-36~38，本轮）

**ADR-36｜用户新指令触发解冻与审计修复**

| 项 | 内容 |
|---|---|
| 触发 | 2026-11-06 03:12 用户下达「代码审计，BUG 修复，对项目进行复盘总结」。此前 T+24h 检查点结论为 AMBER（V2 快照有效、当前时点重跑 G-09 不可签 3 FAIL / pytest 3 failed），AB 与 D0~D6 待裁定 |
| 授权边界 | 新指令明确要求 BUG 修复 → 解冻 GOV-W 白名单、修改代码与测试；等价于 AB 中选 **B**。硬边界 7 个 0 不变；D0~D6 仍全部挂账、未获批准、**不执行** |
| 审计范围 | W-01 / W-02 实现、W-05 / W-06 / W-07 测试、`C:\Temp\governance_zero_state_baseline.py` / `governance_final_collect.py`；`ops/db_dump_daily.py` 只读核查（外部 merge 载体，未改） |
| 方法 | ① `py_compile` 8/8 通过（缺陷均在语义层）；② 逐文件通读源码；③ 写可复现脚本逐条验证疑似缺陷，**只认复现结果**；④ 每处修复后补回归用例；⑤ 重跑全量套件与 G-09；⑥ 统一采集指纹并复核零漂移 |
| 结果 | 复现并修复 **8 项真实缺陷**（BUG-01~08，P0 2 项 / P1 4 项 / P2 2 项），新增 **6 条回归用例**；108 → **114 passed**；G-09 6/9 → **9/9 PASS**；`VERDICT=GREEN`；14/14 落盘；`DRIFT=0` |
| 不变项 | W-03 / W-04 / W-08 / W-09 / W-10 / W-11 / W-12 / W-14 共 8 条白名单**一字节未动**；ADR-36~38 只登记于本文件，不二次 Edit 档案，避免连锁漂移 |

**ADR-37｜时态耦合判据的修复口径（判据必须可签，且不靠放宽换绿灯）**

| 项 | 内容 |
|---|---|
| 根因 | W-07 `_baseline_recheck()` 与 G-09 `G-09c` 把「与 11-05 15:04:12 基线逐字节一致」写成**永久硬断言**。11-05 21:58:46 外部合法 merge 改写 `ops/db_dump_daily.py` 后，3 个 pytest 用例 + 1 项门禁同时变红，且无任何合法路径可转绿 |
| 错误定性风险 | 若当成「治理侧违规」→ 误触发 FF05 全量回滚；若当成「测试退化」→ 误改实现；若直接放宽判据 → 假绿。三者都错 |
| 修复 | 引入**四重外部已提交 merge 举证**：① 文件存在；② `git status --porcelain -- <rel>` 为空（工作区即 HEAD，治理侧禁止 commit）；③ HEAD 最近提交时间 > 基线；④ 工作区字节 CRLF 归一化后 == HEAD blob。任一不满足即 FAIL |
| 硬线 | `system_files_external` 逐条留痕（本轮 1 条：`ops/db_dump_daily.py`）；`system_files_unattributed=[]`；`unknown_new=[]`、`new_tracked_unknown={}` 均未放宽；`backups/db` 四文件全量 sha256 与 W-08 仍为零改写硬断言 |
| 同口径 | W-07 与门禁脚本各有一份 `_external_system_evidence` / `_system_file_evidence`，实现同一四重判据，避免「测试绿但门禁红」的视图分裂 |

**ADR-38｜外部作业归因与工具链可重复性**

| 项 | 内容 |
|---|---|
| 外部新增 | T+24h 后新增 35 untracked + 9 tracked 未归因文件（知识治理、学习门禁、报告面板备份、缠论/晨选脚本、`tools/data_hub/` 秒级写入）。**逐文件取内容/时点举证后**补 12 条具体作业前缀 + 3 条精确文件路径 + `research/reports/backups/` 的 `.bak.*` / `*.v1bak` 后缀约束；**不是整目录放行** |
| 累计 | 外部并行作业 **135 项、135 条可审计理由**，零静默丢弃；未知仍判 FAIL |
| BUG-07 | `git_state()` 对 `porcelain -z` 的 rename/copy 原路径记录解析错（旧实现切 `line[3:]` 会读出错路径）→ 按 `XY ` 前缀识别并跳过原路径 |
| BUG-08 | collect 改为 `mkstemp + os.replace` 原子写（可重复执行）；新增 `VERDICT=GREEN/NOT_GREEN` 防「全红也出一份像通过的清单」；W-13 带 `self_ref` / `excluded_from_drift` 元数据，drift 按元数据而非编号硬跳过；`main` 显式校验子命令 |
| 时点 | 外部会话在本轮签发前 2 分钟仍在写 `tools/data_hub/README.md`。按 ADR-33，**签发后立即重跑 verify 仍可能因新文件变红，属运行态观测，不追溯推翻已签结论**；但每次重新签发必须重新归因 |

**ADR-39｜D4 代码落盘后的 G-09 基线重采集（标准检查点动作）**

| 项 | 内容 |
|---|---|
| 触发 | D4 代码改动 `tools/b4_restore_drill.py`（接入 F2 `should_select`/`verify_artifact`）落盘，其新指纹 `237120ca` 不在 GOV-W 白名单内、亦非「外部已提交 merge」，故 G-09c 无法靠「改基线 sha」或「四重外部举证」救回，必然 FAIL |
| 裁定 | 按铁律选择**重采集 G-09 基线**作为标准检查点动作，把已批准 D4 状态（b4 新指纹 + `ops/db_dump_daily.py` 外部 merge `e17bd9c0`）一并固化为 known-good，而非放宽判据或伪造证据 |
| 动作 | 修正 G-09 工具两处缺陷（W-08 flag 断言 `added_outside`、db_full_sha256 排除 flag 为第 5 条）后，于 **04:15:03** 重采基线、**04:15:13** 签发 verify，**9/9 PASS**；原 15:04:12 基线备份为 `governance_zero_state_baseline_v3_20261105T150412.json` 作审计留痕 |
| 不变量 | 终表外新增 = 0 硬线未放宽；`backups/db` 四文件全量 sha256 与 W-08 仍为零改写硬断言；pytest 114 passed / 0 failed；14/14 落盘；`VERDICT=GREEN`；`DRIFT=0`（W-13 自指排除） |
| 与 G0-b 关系 | 重采基线使 V4 签发的「零状态变更」语义落在一份**已含 D4 代码改动**的 known-good 快照上；D0/D1/D2 的实时计划任务注册态不在此基线内（golden=492），其复核限制见 §7 |

**ADR-40｜AB 决策实质结论 = B（以实质执行反推，非形式签收）**

| 项 | 内容 |
|---|---|
| 背景 | 主线曾存在 A/B 两方案待定（A：不引入 Web/常驻服务，纯 JSON 台账 + pytest + 既有飞书链路；B：代码层落盘 + 验收）。二者治理侧重不同，但都服从同一套 G0-a/G0-b 门禁与铁律 |
| 实质判定 | B 方案（代码层落盘 + 验收）**已完整完成并经 W-13 V3 三次签发**（G0-b 执行态门禁三次通过、14/14 落盘、VERDICT=GREEN、114 passed）——即 B 已被 materially executed 并签收 |
| 结论 | **AB 在事实上已收敛为 B**（form over substance：既然 B 已落盘且三次签发生效，A 的「待定」窗口已无对象，AB 决策实质选 B）。后续 V4 重签属 B 同一条轨迹的 D4 收尾，不改变「AB=B」结论 |
| 影响 | 不再为 A 保留并行方案资源；所有后续验收/签发动作均在 B 框架下推进。若未来确需 A 形态（如独立 Web 看板/常驻服务），须作为**新方案另行立项**，不复用 AB 决策号，且仍须 L2 五要素批准 |
| 不变量 | 无论 A/B，铁律与 G0-a/G0-b 门禁不变：不 `git commit` 除非批准、不碰 `10.205.0.118`、不真实飞书推送、不读密钥/`.enc` 内容 |

**ADR-41｜D6 退役后的 G-09 基线处理（golden 只读、实时偏离外部核实）**

| 项 | 内容 |
|---|---|
| 触发 | V5 重签需把 D6 计划任务退役（42 删 + 1 阻断）纳入验收。G-09 基线工具的 `G-09a` 门禁**只读 `tasks_inventory_20261105.json`（golden）的 `len()` 作 `task_count`，并不读取实时任务数**；golden 是 492 条只读快照，且被 `test_governance_acceptance.py` 的 AC-N01/N02 硬编码为 492 派生统计（coverage 0.0614、logon 185/123/107/77）的**稳定 fixture** |
| 裁定 | **golden 只读基线保持 492 不变**（恢复被误覆盖的 455 版本）；D6 导致的实时任务数变化（497→455）由 `Get-ScheduledTask` 递归权威计数**外部独立核实**，作为「已批准偏离」在 §7 与本文记载，**不写入 golden**、不改 G-09 工具的计数逻辑。G-09a 改为校验 golden 文件完整性（492 未变），命名常量 `EXPECTED_GOLDEN_TASK_COUNT=492` 比对，避免手写 ords 数字串味（455 曾被误写为 454） |
| 动作 | 退役前 497 / 退役后 455 两次 `Get-ScheduledTask` 递归计数；golden 备份为 `tasks_inventory_20261105_v492_before_D6.json` 后恢复 492；G-09 工具 `G-09a` 标签与常量修正；重采基线（06:02:00）→ 签发 verify（06:02:17）**9/9 PASS** |
| 不变量 | 终表外新增 = 0 硬线未放宽；`backups/db` 四文件全量 sha256 与 W-08 仍为零改写硬断言；pytest 114 passed / 0 failed；14/14 落盘；`VERDICT=GREEN`；`DRIFT=0`（W-13 自指排除） |

**ADR-42｜`\OffsiteSync` 删除被 OS ACL 阻断（环境级限制，如实登记）**

| 项 | 内容 |
|---|---|
| 现象 | D6 退役 42 个任务中，`\OffsiteSync` 经 `Unregister-ScheduledTask` 与 COM `DeleteTask` 均报 `E_ACCESSDENIED`（拒绝访问）；其任务文件 ACL 为 `DESKTOP-ARF447U\t:(R)`（用户 t 仅只读，非继承）+ `BUILTIN\Administrators:(I)(F)` |
| 根因 | 当前 agent 进程以**非提权（UAC 令牌过滤）**运行，管理员 SID 被标记为 DENY_ONLY，实际只套用显式 `t:(R)` 只读 ACE → 删不动；`schtasks.exe` 又被安全策略程序黑名单封死，无法经其删除 |
| 处置 | 该任务本就 `Disabled`、零运行风险；已备份其 XML 到 `C:\Temp\d6_backup\OffsiteSync.xml`；**须从提权（管理员）PowerShell 手动删除**，不在本非提权 agent 范围内强行绕过 |
| 与治理关系 | 不视为越权新增或遗漏；属 1/43 的环境级阻断，已明确挂账；D6 净足迹仍为「42 已删 + 1 待提权删」，零越权新增结论成立 |

---

## 12. 项目复盘索引

完整复盘（审计方法、8 项缺陷的代码片段与行号、修复 diff 证据、根因分类、做对/做错、可复用经验、遗留风险矩阵、D0~D6 决策建议）见独立文档：

**`C:\Temp\20261106_code_audit_and_retrospective.md`**（工具产物，不占 GOV-W 白名单）。

---

**签发人**：团长（team-lead）｜**G-09 签发时刻**：2026-11-06T06:02:17+08:00（基线重采 06:02:00）｜**V5 采集时刻**：2026-11-06T06:09:20+08:00
**结论**：G0-b 执行态门禁 **V5 通过（D6 退役执行后重签）**；P0=0、P1 遗留=0、**114 passed / 0 failed**、G-09 **9/9 PASS**、**14/14 落盘**、`VERDICT=GREEN`、终表外新增=0、**DRIFT=0**。D6 退役 42 已删 + 1（`\OffsiteSync`）被 OS ACL 阻断（Disabled，待提权手动删）；实时任务数经 `Get-ScheduledTask` 独立核实 **497→455**，golden 只读基线（492）文件完整性未变；D0/D1/D2 依 L2 批准执行、D4 代码已落地并验证、D3 决策值 14 即现状、D5 仍挂账待业务裁定；未执行任何生产服务器触碰、飞书推送或 `git commit`。

[验收报告 / G0-b 执行态门禁 · V4]
