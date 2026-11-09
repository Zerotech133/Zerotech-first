# 📋 项目档案 · AIOS 数据资产与计划任务治理子系统

> 建档：2026-11-05 14:2x，团长 team-lead｜团队 `aios-governance-forge`（全栈智造专家团）
> 本档案为团队唯一进度事实来源；每次会话开场先同步进度，收尾前更新末尾三字段。
> 上游依据：`docs/plans/20261105_data_backup_and_task_governance_plan.md`（治理方案，全文已读）

---

## 一、项目档案（六字段）

| 字段 | 内容 |
|---|---|
| **项目名称与一句话定位** | AIOS 数据资产与计划任务治理子系统 —— 把「职责重叠 + 手写底册 + 缺格式闸门 + 数据无分层」收敛为单机可审计的单一事实来源与质量闸门 |
| **平台选型** | **候选 A（锁定）**：本机 Windows 桌面 CLI/脚本 + JSON 文件台账 + 既有飞书告警。三维打分 A 15/15、B 8/15、C 4/15；B（本地 FastAPI 只读看板）仅作对比项、C（云端托管）不推荐。零新增组件、零新增端、零系统状态变更（FF02） |
| **技术栈** | Python 3.11 标准库（统一 `"C:\Program Files\Python311\python.exe" -X utf8`；**禁用 `/c/项目/py312/python.exe`，其 `safe_path=True` 导致 `build_catalog.py` 无法 import 同目录 `linreg`**）+ JSON 台账 + pytest 9.0.2 + 既有飞书链路。**零新增组件、零新增端、零常驻服务、零系统状态变更**。PowerShell 5.1 写日志须显式 `-Encoding utf8`；中文路径文件操作走 Bash+Python，不用 PS `Test-Path` |
| **当前阶段** | **Phase 5 收口 / G0-b 执行态门禁签发**：Phase 1~5 五份方案 100% 交付；GOV-W 白名单 **13/14 已落盘**（W-01~W-12、W-14），仅 W-13 验收报告待团长写；F1/F2 两处真实实现缺陷（ADR-27 / ADR-30）已修并回归；三文件 pytest **106 passed**、G-09 分域门禁 **9 项全 PASS**。本卡为 16:2x 时点快照，**最终交付状态一律以 W-13 为准，本卡不再二次 Edit** |
| **已完成产出清单** | ① `[需求与平台选型报告]`；② `[产品定义方案]`；③ `[技术研发方案]`；④ `[测试保障方案 v1.4]`；⑤ `[上线发布方案 v1.3]`（W-12）；⑥ `[运维保障方案 v1.1]`（W-14）；⑦ `[运营增长方案]`；⑧ 团长终裁 **ADR-01~34**（§三）；⑨ 落盘 **W-01~W-12 / W-14 共 13 条**（含 W-09 接入点、W-10 统一口径、W-11 全案整合）；⑩ G-09 零状态变更基线（W-08 落盘前采集，T-10=A 全量 sha256）；⑪ W-08 10-29 隔离 flag（八字段、纯新增）；⑫ F1 复合键、F2 魔数读头两处实现修复；⑬ 团长兜底契约测试 `C:\Temp\gov_fallback_tests.py` **22 passed**；⑭ 三文件 pytest **106 passed** 与 G-09 **9/9 PASS** |
| **下一步计划** | 冻结 14 条 GOV-W → 统一采集最终逐文件 sha256（`C:\Temp\governance_final_collect.py collect`）→ 落 W-13 验收报告与 G0-b 执行态门禁 → `drift` 复核 `DRIFT=0` → 交付老田 → 团队 shutdown。**D0~D6 七项 L2/业务裁定全部未批准，不自动生效** |

### 扩展字段

- **架构决策摘要（为什么选 A 而非 B/C）**：使用者单人、本机即生产环境、已有 492 个计划任务与飞书告警链路；核心动作是 11GB 级本地文件魔数/解压/sha256、Windows 计划任务只读盘点、Docker 容器与 PowerShell，Web 看板无法承接写入侧，云端既承接不了又触发数据外送合规与备案。A 唯一不引入新失败域。
- **里程碑时间线**：v0.1 CLI 台账 → v0.2 质量闸门 → v0.3 演练扩面 → v1.0 排班收敛（**四期是同一单机 CLI 形态内的版本里程碑，不是新增端形态**；v1.0 需 L2 五要素批准才启动）。计划上线日期未定（Q2 待确认）。
- **技术债务清单**：① 两库真空 `cainiao_mysql`/`quant_pg`（阶段 1，需 L2）；② `zerobound-neo4j` 零备份（D5 待业务裁定）；③ 加密主链从未做 `.enc` 解密恢复演练（D0）；④ 11GB 库无分区、13 年日线单表、分钟线 1684 万行（阶段 3）；⑤ 10-29 三个纯 SQL 文本冒充 `.gz`、manifest 标 `ok=true`（本轮只隔离标记、不删不改）；⑥ 45 个自定义 Disabled 与 24 个从未运行任务未分诊；⑦ 06–09 点 76 个 Ready 任务未错峰。

---

## 二、范围与硬边界（团队共同契约）

**本轮实施范围**：仅治理方案阶段 0 + 阶段 2 的**代码/文档层** —— 任务盘点工具与台账、三处口径修正、10-29 坏档隔离标记、写入格式闸门设计、关键链哨兵补链设计、恢复演练扩面设计。

**绝对禁止（L2 与生产安全红线）**：
1. 新增/修改/启用/禁用任何 Windows 计划任务（含 `\DbDumpDaily`、`\CainiaoPull` 的 Register/Start/Disable/Unregister）；
2. 删除或改名任何现有文件，改写 `backups/db` 下任何既有产物内容；
3. 修改 `ops/db_dump_daily.py` 的 `DUMPS` 列表与 `KEEP_DAYS`；
4. 执行 `.enc` 解密演练、`b4_restore_drill` 演练扩面执行；
5. 触碰生产服务器 `10.205.0.118`；
6. 阶段 1 排班 / 阶段 3 架构改造 / `zerobound-neo4j` 裁定 / 任务瘦身错峰 / 轮转改 60。

**互斥协议（仓库铁律）**：多人并行共享同一工作区 → 每人只写分配给自己的**全新路径**，不删改他人文件；Phase 1 全体成员**只产出文本方案、不落盘**，由团长统一落盘。

**落盘前预检结论（14:2x 只读，数字已按码点复核订正）**：`ops/task_inventory.py`、`ops/backup_format_gate.py`、`backups/db/2026-10-29/INVALID_GZIP.flag`、全案文档四路径均为 FREE；**但 git 工作区实测有 110 处 tracked 变更 + 803 处 untracked**（`git status --porcelain -z`，码点 `[49,49,48]` / `[56,48,51]`；此前档案写「25+ 处modified」为抽样前 80 行所得，**已订正**），含 `ops/db_dump_daily.py`、多个 registry / deliverables，说明本机另有并行作业 → 本轮落地**只新建 GOV-W 白名单内文件，不触碰任何已处于 modified 状态的文件**，`db_dump_daily.py` 的 docstring 也不再改（三处口径修正中的该项改由 W-10 新文档统一声明，不改原文件）。

**git 仓库根订正**：`git -C C:\aios rev-parse --show-toplevel` = `C:/项目/老田的AI助手`；`C:\aios` 即该仓库工作树路径（同一 worktree），W-01~W-14 均为相对该根的路径。

---

## 三、团长裁决记录（ADR）

| # | 争议 | 裁决 | 影响 |
|---|---|---|---|
| ADR-01 | 「四期路线 / 多端版本路线图」是否引入新增端形态 | **否**。该措辞是团长把通用 PRD 模板章节名改写为本项目版本里程碑，四期全部在候选 A 单机 CLI 内。B 仍仅对比项、C 仍不推荐 | 解除 PRD 口径风险标注，平台选型 A 不变 |
| ADR-02 | 治理方案 §六 量化指标能否作本轮验收 | **不能**。必保库 5/5、解密演练 ≥1 次、单日窗口 ≤3min、哨兵 100%、失败任务全归因均为阶段 1/3 之后的**目标态**，本轮不可达，只能标注为远期指标 | 本轮验收收敛为下方四条可达项，防止纸面达标 |
| ADR-03 | 「关键链 100% 在册」口径 | 收敛为「关键链哨兵**在册设计完成**」，本轮不做运行态在册验收 | 选品洞察师已同步 PM 修正 AC |
| ADR-04 | D0~D6 与阶段 1/3 是否进本轮交付 | **不进**。七项裁定全部未批准；v0.3 演练扩面、v1.0 排班收敛归后续里程碑 | 防止范围蔓延（FF01/FF09） |
| ADR-05 | 10-29 隔离标记是否与「不改既有产物」冲突 | **不冲突，但只做一半**：新建 `INVALID_GZIP.flag` 属纯新增文件（允许），改既有 `manifest.json` 属改写（禁止，延 D4 后）。验收不承诺改 `b4_restore_drill.py` 过滤；默认演练按日期倒序取最新（11-05，魔数已验 1f8b）天然不会选中 10-29，显式 `--date` 时人工看 flag 拦截 | 消除「设计完成」被写成「运行态已生效」 |
| ADR-06 | REGISTRY 候选的人工裁定流程 | 候选（`applied=false` 硬编码）→ 团长汇总 → 老田逐条确认 `expect` → D1/D2 裁定后由团长写入。本轮零写入，`DbDumpDaily` 保持 pending。依据 `miss_check.py` docstring「expect 显式可审计优于从 NextRun 反推」 | 防止从 Triggers 自动反推后写回 |
| ADR-07 | FF06 / FF11 编号是什么 | 是 forge-lead 错误预防对照表的团队交付物编号，**非仓库既有文件**（Grep 无匹配已核实）。落盘按既有命名 `docs/changes/20261105_ff06_interface_hooks.md`，全案文档登记编号对照表 | 避免虚构既有文件名 |
| ADR-08 | 实施期 F1–F5 的写者 | 本轮 B 方案由**团长单写者统一落盘**（互斥协议 + 预检发现 25+ 处既有 modified 文件）；全栈研发师的文件归属表保留为「后续维护归属」，F1–F5 后续修改由其负责 | 单一写者最安全 |
| ADR-09 | `ops/data/` 不入库如何处理 | 本轮不改 `.gitignore`（既有文件），在全案文档声明「运行产物不入库，由老田手工加忽略规则」 | 不触碰 modified 文件 |
| ADR-10 | PRD flag 式 CLI vs 技术契约子命令 | **以技术契约 5 个 argparse 子命令为准**（export/snapshot/diff/stats/suggest-registry）。PRD `--regen-registry` 隐含写回 REGISTRY，与 `applied=false` 只读契约语义相反；`--query`→stats、`--export`→export。全栈研发师主动「让步到 PRD」的方向**已撤回** | 消除落盘时接口对不上 |
| ADR-11 | PRD AC-01~AC-11 的定位 | 定位为 **v0.1/v0.2 未来退出条件**，不是本轮验收。AC-02/03/04/05 含运行态动作（写回 REGISTRY、M4=4/4、list_backups 过滤、飞书告警），按 ADR-02/ADR-05 降级为设计层/实施期 | 防止把未来态写成本轮成果 |
| ADR-12 | PRD R-02 旁路 sidecar | 采纳「旁路隔离清单 sidecar」为新增设计件（不碰既有 manifest）；但 `list_backups()` 读取时过滤本轮**不做**，属 D4 后实施期，不得声称已生效 | 与 ADR-05 一致 |
| ADR-13 | **系统路径前缀口径（实测纠错）** | 契约原写单前缀 `\Microsoft\`，实测得 system 180 / custom 312 / real_failed 36，**与 §1.3 golden 不符**；三前缀 `\Microsoft\`+`\ZeroBound\`+`\AIOS-` 才得 199 / 293 / 32 / 24。落盘 F1 已按三前缀实现，`_is_system` 用 `startswith(tuple)` | 否则 AC-01 直接失败 |
| ADR-14 | `classify()` 判定顺序 | 原契约把 `Ready` 放在 `real_failed` 之前，会使 Ready 且 rc≠0 的任务标成 ready，category 与审计计数不同源。落盘 F1 调整为 never_run → noise → system → **real_failed** → disabled → running → ready | category 与 golden 同源 |
| ADR-15 | 是否出「增量接口契约补充」 | **不出**。D-4~D-7（diff 五类变更 / 7 态标签 / ALERT_PENDING / F-07 三件设计件）登记进全案文档「实施期接口补充清单」，本轮不实现；仅采纳 `snapshot_id = 快照内容 sha256 前 16 位` 并直接落进 F1 | 避免多一份待维护文档与范围蔓延 |

| ADR-16 | **T-10 哈希口径 A/B** | **采纳 A：全量 sha256**。F2 `sha256_of()` 已实现流式全量、`compute_sha256` 默认 True，零改动。G-09 基线同口径但分层：`db_full_sha256`（10-29 四文件全量）+ `db_fingerprint`（全库 size/mtime_ns/首 1 MiB，仅检测删改），**两层不混用**；发布前采集与回滚比对必须前后同口径，否则误判哈希漂移→误触发 FF05。GB 级耗时列实施期观测项、不设阈值 | 解除 qa-tester / release-manager / ops-sre 三方最后一项算法分歧 |
| ADR-17 | **T-02 白名单终表** | 改用 **GOV-W 编号表 W-01~W-14**（下表），为本轮唯一有效白名单。`tests/test_governance_acceptance.py` = **W-07 显式纳入**；release-manager 的 F6（`ops/data/*.json`）降级为「运行时产物，本轮不生成」。所有 F/FF/PF/BG/TI/AC/G/T 编号均为**方案内引用编号，非文件路径、非白名单依据**（ADR-07 扩展） | 解除落盘前硬阻断 ③；W-07 不再被 G6③ 误判 |
| ADR-18 | **T-11 / AC-02 终裁关闭** | 按 PM 权威更正：本轮 AC-02 **不含任何 `list_backups()` 返回值断言**；「可选备份数=0」在硬边界下**全域不可达**（默认路径取 11-05、显式 `--date 10-29` 仍返回 3、全量 9 目录），整体降为 **D4 后实施期目标态判据**。AC-N04 按补丁 3 四条验证；G2 不写死断言；档案 AC2 原文同步收窄 | 防止「设计完成」写成「运行态已生效」 |
| ADR-19 | **G0-b「判据必须可签」原则** | G0 拆分为 G0-a（设计态 READY，落盘前签）/ G0-b（执行态，落盘跑完出逐项「是/否+证据」报告才签）；G0-b 第③项改为「性能 6 项设计预算声明齐备、实测值栏留空标注『实施期填』」即可签，**不实测、不设阈值**；原则：不假绿、不假红、不诱导违反硬边界。AC-04 按 ADR-05 形式确认 | 性能不实测仍可签，避免假红 |
| ADR-20 | **flag 与 `list_backups()` 的因果边界（代码级取证）** | `list_backups()` 只收「含 `quant_pg_alldb.sql.gz`」的日期目录并倒序、`main()` 取 `backups[0]`，**不做格式过滤**。故 `INVALID_GZIP.flag` **只是隔离留痕 + 显式 `--date` 人工拦截凭据，不改变 `list_backups()` 行为、不是运行态过滤闸门**；10-29 未被默认选中是日期倒序的既有行为，**与 flag 无因果关系**。自动过滤待 D4 后接入 `should_select(verify_artifact(p))`。全文禁止「加 flag 后自动跳过 10-29」「永不被选中（运行态）」表述 | 锁死 ADR-05 最后一处漏网点；W-09 A9 标注「本轮是否改：否」 |
| ADR-21 | **补丁 10 / 新旧 AC-02 消歧** | `build_snapshot()` 已实现 `snapshot_id = sha256(canonical_json(norm))[:16]`，哈希输入只含任务集合、排除生成侧元数据；`diff_snapshots()` 比对 `tasks[]` 而非 id → 两条设计层不变式**实现已满足，F1 零改动**，仅补注释固化。旧 AC-02（PRD 未来退出条件）与**新 AC-02（本轮唯一可达 AC）**同名不同义，编号对照表登记消歧 | 避免误改已达标实现 |
| ADR-22 | FF01/FF03/FF07/FF08/FF10 定义 | **本轮未实例化，不臆造、不补写、不追问，直接结案**。FF 编号是 forge-lead 通用错误预防对照表编号，本轮实际实例化 7 项（FF02/04/05/06/09/11/12）；W-09 保持「待团长编号命名空间对照表确认」标注即可，**不做二次 Edit** 以免污染已采集的 sha256 清单 | 尊重 ADR-07 不虚构纪律 |
| ADR-23 | 叙事口径 vs AC3 验收口径是否合并 | **不合并、不改 AC3**。叙事层唯一句「加密主链健康，缺的是明文副链排班与格式闸门」；验收层 AC3 保持「明文副链断档 + 两库真空」原文（台账技术事实层）。全案 W-11 登记「叙事/验收口径对照表」 | 防止改 AC3 破坏三处文档自洽与验收可签性 |
| ADR-24 | B4 语义 / Q7 开关 / 三节点起算点 | B4 = **内容零读取，元数据只读不算**（`os.stat` mtime/ACL 不构成内容读取），发布窗口内暂停 M09 哈希采集，窗口后恢复；`ALERT_MASTER_SWITCH` 默认 OFF、Q7 授权主体=老田、本轮推送 0 条；T+24h/T+48h/T+7d 三节点**起算点 = G0-b 执行态门禁签发时刻**，非落盘时刻 | 与 Phase 3/4/5 单一口径一致 |
| ADR-25 | 「路径归属」与「哨兵关键链」正交 | `system/custom` 归属（供 §1.3 统计，三前缀判定）与 `KEY_CHAINS` 关键链（供 `miss_check` 监控）是**两套正交分类，互不推导**：`_is_system()` 不参与候选筛选，`suggest_registry()` 不按 `is_system` 过滤；`CainiaoPull` 冲突保留在册报 MISS 为设计意图 | 防止把系统任务误排除出关键链、或反推 REGISTRY |
| ADR-26 | ADR-08 单写者边界修订 | 团长仍单写者落 W-01~W-04、W-08、W-10、W-11、W-13；**按文件划分**委派 W-05~W-07（qa-tester）、W-09（fullstack-engineer）、W-12（release-manager）、W-14（ops-sre）。每人只写自己那一个/一组全新路径，不改他人文件；**ops-sre 因 v1.0 全文只在其上下文，由自己合并落盘** | 并行落盘且零冲突 |
| ADR-27 | **F1 `diff_snapshots()` 真实缺陷（团长独立验证发现）** | 原用 `t["path"]` 单键索引。实测 492 条任务仅 **97 个不同 path**，根目录 `\` 聚集 **291 条** → 自比报 `unchanged=97`，同目录不同 Name 被静默合并、增删改漏报。改为 `(path, name)` 复合键（492 个不同值、零重复）；`added/removed/changed` 每条新增 `name` 字段，`unchanged` 保持 path 字符串列表；tuple 只在模块内部做键、不进 JSON。验证：自比 `unchanged=492`；构造增删改各 1 → `{added:1,changed:1,removed:1,unchanged:490}` 精确识别；`GOLDEN_MATCH=True` 不受影响 | 修复 P0 级静默数据错；W-05 需按新契约更新断言 |
| ADR-28 | `policy` 值冲突（W-06 vs F2/W-08） | 权威值 = **`mark-only, do-not-delete`**（F2 `write_quarantine_flag()` 实现值，也是 W-08 落盘实物与 W-10/W-11/W-12 写入值）。裁定**改测试、不改实现**：W-08 已进入 G-09 基线，改实现会连锁污染 sha256 清单与零状态变更复核；测试应断言落盘实物。`mark-only-do-not-delete` 仅为叙述行文，不是 JSON 值 | 消除 1 条 pytest failed，且不动已落盘实物 |
| ADR-29 | **G-09 门禁判据分域（可签性修正）** | 首轮 verify 报 2 项 FAIL：tracked 集合变化 + 11 个白名单外 untracked。同口径重算取证：`REAL_ADDED_TRACKED=[]`（本轮零新增 tracked 变更），11 个新增全部是**外部并行作业**（watch_24h 复盘 / qpzm t9·t10 审计 / strategy_cost_alert，mtime 均晚于基线 15:04:12），另有 `portal/dw.html`、`portal/portal_api.py` 两条既有 modified 状态回退。判据原写「全世界都没动」，在有并行作业的机器上**永不可签**，违反 G0-b。改为分域：GOV-W / 工具自动产物 / **外部并行作业（带可审计归因理由，仅留痕、不参与放行）** / 未知（仍判 FAIL）；可签命题 = 「本轮未在 GOV-W 域产生新 tracked 变更 + 无未归因文件」。重写后 **9 项全 PASS，UNKNOWN=0** | 门禁从不可签变为可签，且不靠放宽（外部项不参与放行、未知仍 FAIL）。可作为 G0-b 证据④ |
| ADR-30 | **F2 `verify_artifact()` 魔数读头真实缺陷（团长兜底测试发现）** | `read_magic()` 默认只读 2 字节，而 `detect_format()` 的 `b"Salted__"` 是 **8 字节** → `VALID_FORMATS` 声明的 `encrypted-aes` 分支**永远不可达**，合法 `.enc` 被误判 `unknown`。修法：`verify_artifact()` 读两处头，对外 `v["magic"]` 仍保持 2 字节（`1f8b`/`2d2d`/`5361` 契约不变），另读 8 字节只喂 `detect_format()`。qa-tester 在 W-06 备注里已准确抓到该缺陷（「read_magic(n=2) 只读到 Sa」），但把缺陷当契约断言；团长裁定**修源码 + 改测试断言为 `encrypted-aes`**，非回归。团长兜底契约测试 **22 passed** 全绿 | 落点（release-manager 补强）：**W-12 §2.2 S2 退出判据**（1f8b ok=true / 2d2d ok=false）、**§4.2 G1·G2**、**§6 风险 2 六类夹具 `encrypted_aes`**、**G0-b 证据① pytest 全过**；域属 W-02（F2）v0.2 质量闸门 |
| ADR-31 | G1 前置修复汇总 | 两条实现修复须在 G1 前完成并回归：ADR-27（W-01 复合键）、ADR-30（W-02 魔数读头）。两者均**已修实现**，故 W-01 / W-02 内容为修复后版本，W-13 逐文件 sha256 清单必须取最终版，否则 G6③ / B2 会误判哈希漂移、误触发 FF05 全量回滚 | 与 W-12 补写、W-14 三次修订同一机理 |
| ADR-32 | **落盘回执必须经磁盘复核（团队纪律）** | qa-tester 首次回报「3 个测试文件已落盘、84 passed / 1 failed」，团长两次独立读盘 + 全盘定向搜索 5 个根均为 `FOUND_CODES=[48]`=0 → 回执与磁盘不符，**判定未交付**，要求重落并给 `os.path.exists` + sha12 证据。第二次核验 W-05 / W-06 确已落盘（`85b92ce8c60b` / `d9ccdc8a9fa5`），实跑 85 passed / 1 failed。确立纪律：**任何「已落盘」回报一律以团长独立读盘为准，纸面交付不计入验收** | 防止挂载视图差异导致全案虚假达标 |
| ADR-33 | **G-09 为签发时点门禁 + 外部作业动态归因** | 16:17 复核时门禁由 9/9 退化为 2 FAIL，取证为外部并行作业又新增 3 个产物：`research/reports/learning_gate_20261105_intake.md`（`stage=intake`）、`research/reports/factor_b1_full_eval_20261105.json`（`factor_lib v1.2.0` / py3.12 / pandas 3.0.5）、`quant_backtest/deliverables/2026-11-05_沪金b3信号_实时可见性_v7_边际证据判定.md`（`gold_signal_shadow.py` / `cross_poll.py`），mtime 均晚于基线 15:04:12，内容可独立核实、与本轮治理无关。终裁：① 按 ADR-29 **内容指纹举证**归类，逐文件写入 `external_job_evidence` 理由，**不新增目录前缀白名单、未知仍判 FAIL**；② G-09 是**时点门禁**，`verified_at` 即签发时刻，签发后外部作业新增归运行态观测（ops-sre T-09 / AL-22~26），不追溯推翻；但每次重跑 verify 必须重新归因，不允许一次归因永久放行；③ 修复后 **9 项全 PASS，ALL_PASS=True** | 在并行作业机器上让门禁既严格又可签，不靠放宽判据换绿灯 |
| ADR-34 | **G-09d 判据自身缺陷 + W-06 兜底接管** | `G-09d 外部并行作业域零静默丢弃` 原判据只认可 `EXTERNAL_JOB_PREFIXES` 路径前缀，不认可 ADR-29 允许的内容指纹举证 → ADR-33 新增指纹后反把本项判 FAIL（真实缺陷）。修为「外部域每个文件都必须取到可审计归因理由」（空集为真），并新增 `external_job_evidence` 明细字段。同时 qa-tester 多次下发后仍未改 W-06 第 113 行 `("encrypted_aes","unknown",...)`，团长按已声明的 **B 方案**接管：仅改该断言为 `encrypted-aes` + 同步 ADR-30 后 docstring 备注，**不动 F2 源码**，随即三文件 `pytest -q` **106 passed**。白名单自此冻结，qa-tester 停止写盘。**追加（16:26 复核）**：`G-09c 本轮未在 GOV-W 域产生新 tracked 变更` 同为错误命题——本轮正是要写白名单内文件，团长更新 W-04/W-11/W-06 后三条被算作 `new_tracked_gov` 而判 FAIL（git 口径：非 `??` 条目均入 `tracked_changes`）。修为「**白名单域新增 tracked 变更均可独立读盘验真**」（终表内 + `os.path.isfile` 为真）；G6③ 的真实命题「终表外新增 = 0」仍由 `new_tracked_unknown` 硬判 FAIL 保留，未放宽。另 `quant_backtest/chanlun_healthcheck.py`（16:23:53，缠论系统体检、`chanlun_conformance`）按内容指纹举证归入外部并行作业域 | 消除判据自相矛盾导致的假红；避免写者竞态，收口可交付 |

### GOV-W 白名单终表（唯一有效，14 条）

```
W-01  ops/task_inventory.py
W-02  ops/backup_format_gate.py
W-03  docs/plans/20261105_data_backup_and_task_governance_plan.md
W-04  docs/plans/20261105_governance_project_dossier.md
W-05  tests/test_task_inventory.py
W-06  tests/test_backup_format_gate.py
W-07  tests/test_governance_acceptance.py
W-08  backups/db/2026-10-29/INVALID_GZIP.flag
W-09  docs/changes/20261105_ff06_interface_hooks.md
W-10  docs/changes/20261105_governance_terminology.md
W-11  docs/plans/20261105_governance_full_case.md
W-12  docs/plans/20261105_release_plan.md
W-13  docs/plans/20261105_governance_acceptance_report.md
W-14  docs/plans/20261105_ops_sre_plan.md
```

**本轮验收四条（唯一可达 AC；AC2 已按 ADR-18 收窄）**：
1. `ops/task_inventory.py` 可重复运行，492 条统计与治理方案 §1.3 字节级一致（Ready 407 / Disabled 76 / Running 9；自定义 293 / 系统 199；真实失败 32；从未运行 24；Disabled 45；高峰 76；REGISTRY 23 条覆盖 18 个自定义任务）；
2. **10-29 三个坏档完成隔离留痕**（`INVALID_GZIP.flag` 八字段、`files` 聚合 3 个 `2d2d` 产物、`format=plain-sql`、`recoverable=false`、`policy=mark-only, do-not-delete`、`decision_ref=D4`），且 `manifest.json` 与三个 `.sql.gz` 全量 sha256 前后一致、**改写字节数 0、不删不改名不重压缩**；默认演练路径按日期倒序取 11-05 是既有行为（AC-N04 第 3 条），**flag 不是运行态过滤闸门**，本轮不承诺 `list_backups()` 返回值变化、不写死「可选备份数=0」；
3. 三处口径自洽为「明文副链断档 + 两库真空」（因预检发现 `db_dump_daily.py` 已 modified，本轮以 W-10 新文档统一声明替代改原文件）；
4. 关键链哨兵补链与 `DbDumpDaily=pending` 的**设计完成**（不落运行态）。

---

## 四、待确认（不擅自拍板）

- **Q1~Q9**（选品洞察师提出）：预算上限数字、交付时间/里程碑节奏、是否始终单人维护、平台/端主观偏好、是否需要远程查看台账、D0~D6 裁定结果、飞书 webhook 可用性与写入闸门复用许可、阶段 2 演练扩面设计是否纳入（依 ADR-04 已隐含收敛为「不进本轮」）、主体资质/域名（仅 C 触发）。
- **D0~D6**（治理方案 §四）：D0 `.enc` 解密演练、D1 `\DbDumpDaily` 范围 A/B/C、D2 `\CainiaoPull` 19:00 排班、D3 `KEEP_DAYS` 14/60、D4 坏档处置、D5 `zerobound-neo4j`、D6 任务瘦身错峰。团长建议顺序为 **D0 立即做 → D1 选 A → D2 批 → D3 定 14**，但**均未获批准，不自动生效**。

---

## 五、阶段复盘卡

```
📊 阶段复盘卡（Phase 1 → Phase 5 → B 方案落盘）
- 本阶段完成度：Phase 1~5 五份方案 100% 交付；GOV-W **13/14 已落盘**
  （W-01~W-12 / W-14），仅 W-13 验收报告待团长写（时点快照，最终以 W-13 为准）
- 已消解阻塞（共 8 类）：① ADR-01 四期路线口径；② ADR-02 目标态不得作本轮验收；
  ③ ADR-10 CLI 五子命令 vs PRD flag 式；④ ADR-13 三前缀实测纠错（单前缀得 180/312/36，
  与 golden 不符）；⑤ ADR-14 classify 顺序；⑥ T-10 哈希口径 A；⑦ T-02 白名单 GOV-W 终表
  （W-07 显式纳入，消除撞号）；⑧ T-11「list_backups 返回 0」全域不可达 → 关闭
- 关键纠偏（3 次）：a)「P0 db 备份断链」→ 加密主链健康、明文副链断档 + 两库真空；
  b)「加 flag 后自动跳过 10-29」→ flag 非运行态闸门，默认选中 11-05 是日期倒序既有行为；
  c) 档案「25+ 处 modified」→ 实测 110 tracked + 803 untracked（码点复核）
- 未解阻塞：无技术阻塞。D0~D6 七项 L2/业务裁定全部未批准，属有意挂起，不是阻塞
- 零状态变更基线：W-08 落盘前采集（C:\Temp\governance_zero_state_baseline.json），
  T-10=A 全量 sha256 同口径；**verify 九项门禁 9/9 PASS，ALL_PASS=True**（ADR-29/33/34 分域）
- 测试状态：三文件 `pytest -q` **106 passed**（ADR-30 断言收口后）；团长兜底契约测试 22 passed
- 下阶段计划：冻结 14 条 → 统一采集最终 sha256 → 落 W-13 + G0-b 执行态门禁 →
  `drift` 复核 DRIFT=0 → 交付老田 → 团队 shutdown
```
