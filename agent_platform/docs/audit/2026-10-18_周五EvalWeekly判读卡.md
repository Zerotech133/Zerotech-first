# 2026-10-18 周五 AgentShadowEvalWeekly 判读卡（只读梳理）

- **性质**：纯文档零代码判读卡，只读梳理 eval 链实现。与《2026-10-18_周一观察点现场SOP手册.md》同体例：触发与脚本链 → 输入清单 → 判据段 → 输出段 → 现场操作段 → 异常处置段。
- **取证方法**：直读计划任务 XML（utf-16）、`shadow_automation.ps1`、`eval_shadow.py`、`engine/perf_ledger.py` 全文，及 `data/` 各文件现场状态（2026-10-18 取证时点）。
- **重要前置事实**：本任务**不是零预演状态**——`eval_last_run.log`（10-17 23:29）已有一次数十行真实产出（结论 PASS，`shadow_automation.ps1:21-22` 注释记载 LastTaskResult=0 生产实证）。周五 17:45 的判读是「第二次及以后的例行判读」，本卡把那次产出作为基准样本写进判据段。

---

## 一、触发与脚本链

**任务锚点**：`C:\Windows\System32\Tasks\AgentShadowEvalWeekly`（XML 直读取证）

| 字段 | 值 |
|---|---|
| Description | `Shadow three-criteria eval (J1/J2/J3) every Friday 17:45, log to data/shadow/eval_last_run.log (P2 automation 2026-10-17)` |
| 触发 | CalendarTrigger，`StartBoundary=2026-10-17T17:45:00+08:00`，每周五（`<Friday />`），WeeksInterval=1 |
| 身份 | S4U / LeastPrivilege / UserId=t |
| 关键设置 | StartWhenAvailable=true（错过错点会补跑）；ExecutionTimeLimit=PT72H；Priority=7；IgnoreNew |
| 命令行 | `powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\aios\agent_platform\shadow_automation.ps1 -Mode eval` |

**路径说明**：`C:\aios` 是指向 `C:\项目\老田的AI助手` 的 junction（`ls -la /c/aios` 实证 symlink），故任务实跑仓库内 `agent_platform/shadow_automation.ps1`。脚本 `shadow_automation.ps1:10` 硬编码 `$here='C:\aios\agent_platform'`（ASCII-only 设计，注释第 7 行：中文路径判断 → 用 C:\aios junction）。

**脚本链**（`shadow_automation.ps1:28-34` eval 分支）：

```
AgentShadowEvalWeekly（周五 17:45）
  └─ powershell -File shadow_automation.ps1 -Mode eval
       └─ cmd /c ""C:\Program Files\Python311\python.exe" -X utf8
            "C:\aios\agent_platform\eval_shadow.py" --persist-root "C:\aios\agent_platform"
            > "data\shadow\eval_last_run.log" 2>&1"
```

- 重定向用 `cmd /c` 字节级透传而非 PS `1>`：PS 5.1 会把 native stdout 按 GBK 解码再以 UTF-16LE 重写（首 3 字节 FF FE BOM），中文全乱；此形态已在生产实证（`shadow_automation.ps1:29-33` 注释，19:51 mojibake verdict 勘误）。
- `--persist-root C:\aios\agent_platform` 是 M5-P2 安全阀的显式参数（`eval_shadow.py:162-164`：缺省拒写，演示永不落真仓）。

## 二、输入清单（读哪些账本/文件）

| # | 文件 | 代码出处 | 今天（10-18）状态 |
|---|---|---|---|
| 1 | `data/shadow/shadow_ledger.jsonl` | `eval_shadow.py:29`（LEDGER） | 存在，7407B，最后写入 10-14 17:14。内容：done=25 / failed=0，交易日 5 个（10-08/09/12/13/14），票 5 只（600519/000858/600036/601318/300750） |
| 2 | `data/checkpoints.db` | `eval_shadow.py:30`（DB） | 存在，319488B，10-18 11:54。J3 查 `traces` 表（run_id 回查） |
| 3 | 实时行情（`tools.quote_snapshot` / `tools.kline_daily(sym, 40)`） | `eval_shadow.py:27,217`（engine/tools 导入） | 运行时外部依赖：J2 后验基准=决策日 K 线收盘、出场=最新收盘；40 根日 K，每票缓存一次 |
| 4 | `data/perf_ledger.jsonl` | `engine/perf_ledger.py:34`（LEDGER_PATH_NAME） | 存在，10220B，10-17 22:58（上次 eval 沉淀产物，非本次输入；幂等键 perf_key=sha1(date\|symbol\|run_id)[:16]） |
| 5 | `data/trace/trace_links.jsonl` | `eval_shadow.py:145-146`（LinkStore） | 存在，13698B，10-17 22:46（J4 沉淀时的锚点登记面） |

**注意**：账本最后交易日=10-14。下个周五（10-23）评估时，若周一链 17:10 影子跑恢复落账，账本将含新交易日行——判读时以日志头 `影子账本：done=X failed=Y 交易日=Z 票数=N` 实测行为准，勿照抄本卡快照。

## 三、判据段（三判据 + 结论语义，全部引 `eval_shadow.py` 行号）

### J1 无人干预（`eval_shadow.py:179-183`）

- **公式**：`交易日数 >= 5（--min-days 默认 5） 且 done 行数 >= 交易日×5×0.9`
- **分母口径**：N_BASKET=5 篮子全集硬编码（`eval_shadow.py:31-33`，K-STOCK-021 修复：不再从 done 行自我引用收集 symbols）
- **证据行来源**：日志头 `影子账本：done= failed= 交易日= 票数=` 与 `[J1 无人干预] PASS/WAIT/PENDING（篮子覆盖率 X%, done 占比 Y%）`——双口径分开打印（N2 修复：覆盖率=done/(篮子×交易日)，done 占比=done/(done+failed)，勿混读）
- **PASS 形态**：`PASS`；数据不足 → `WAIT/PENDING`（中性，非失败）

### J2 秩相关>0（`eval_shadow.py:265-280`）

- **公式**：D1 score 与后验收益的 Spearman 秩相关 rho 存在（n>=3）且 >0；confidence-vs-ret 并列打印作辅证
- **采样口径**（K-STOCK-397 修复，`eval_shadow.py:10-15`）：逐行（交易日×票）后验——基准=决策日 K 线收盘（权威价，快照价兜底），出场=最新收盘；零持有期行（决策日=最新 bar）计 pending 跳过并打印 `[pending] N 行`
- **必须看的伴生行**：
  - `[warn] 全部后验收益=0：价格源疑似未刷新…J2 判读无效`（`eval_shadow.py:258-259`）——出现即 J2 作废，转异常段
  - `[warn] score 并列零占比 X%（HOLD/中性密集…）`（`eval_shadow.py:274-280`，K-ORG-129）——占比>50% 时 rho 解读须排除并列零；10-17 实测 78%（18/23），属已知形态
  - `[skip] N 行决策字段缺失未入 J2`（`eval_shadow.py:255-257`，K-STOCK-338）——样本缩水可见化
  - `[坚守组]/[倒戈组] conf-vs-ret rho=`（`eval_shadow.py:286-296`，K-STOCK-017/080）——倒戈票低 conf 语义已变，分组解读勿混算
- **证据行来源**：`[J2 秩相关] PASS/WAIT/FAIL（score-vs-ret rho=X, conf-vs-ret rho=Y, n=Z）`

### J3 lineage 可追溯（`eval_shadow.py:321-351`）

- **公式**：每笔 done 决策的 run_id 在 `checkpoints.db` traces 表可查到（`COUNT(*)>0`），`traced == len(done)` → PASS
- **降级语义**（P2 清仓 2026-10-18）：DB 损坏/表缺失不再炸全程——stderr 告警 + J3 判 **FAIL（DEGRADED，宁严勿假 PASS）**，主流程走完出结论；缺 run_id 键行计入分母（不可能假 PASS）
- **证据行来源**：`[J3 lineage] PASS/FAIL（X/Y 决策可回查辩论 trace）` 或 `DEGRADED` / `WAIT（无账本或无 checkpoint 库）`

### 结论行语义（`eval_shadow.py:353-355`）

```python
verdict = "PASS" if (j1 and j2 and j3) else ("PENDING" if not j1 else "PARTIAL")
```

| 结论 | 含义 | 处置 |
|---|---|---|
| `PASS` | 三判据全过 | 例行归档 |
| `PENDING` | **J1 未过**（数据不足，J2/J3 无从谈起） | 中性——等数据，勿当失败 |
| `PARTIAL` | J1 过但 J2/J3 至少一项挂 | 按异常段分诊 |
| 末尾括号 | `（满 5 交易日后 J2/J3 才具统计意义）` | 固定提示，n 小时 rho 波动大属正常 |

## 四、输出段（落哪里、怎么算 PASS/FAIL/PENDING）

| 输出面 | 落点 | 内容 |
|---|---|---|
| **主日志** | `data/shadow/eval_last_run.log`（每次**整文件覆盖**，非 append） | 上述全部判读行的唯一载体；UTF-8 纯文本 |
| **J4 绩效沉淀** | `data/perf_ledger.jsonl`（`engine/perf_ledger.py:6`，幂等键 perf_key） | 逐行后验入账；日志证据行 `[J4 perf] 沉淀 recorded=X replayed=Y skipped=Z findings=N`（10-17 实测 recorded=0 replayed=23——幂等重放语义） |
| **锚点登记** | `data/trace/trace_links.jsonl`（LinkStore） | 沉淀时同步登记，无独立判读行 |
| **阵容校准起草** | intel_wiring draft（建议权，adopt 须人工凭据；intel_wiring 缺席时降级留 finding，`eval_shadow.py:298-319`） | 日志证据行 `[roster-draft] <decision> <verdict_id> (<impact>)`；同统计指纹幂等不重复起草 |

**成败判定语义（与周一链的关键差异）**：`main()` 恒返回 0（`eval_shadow.py:356`），**退出码不携带判读信息**——PASS/FAIL/PENDING 只能读日志内容，没有周一 `check_observation_point.py` 那样的退出码矩阵。安全阀拒写、J3 降级等也都不改退出码。

**基准样本**（10-17 23:29 真实产出，判读对照基线）：结论 `PASS`；J1 覆盖率 100%；J2 score rho=+0.064 / conf rho=+0.202（n=23，并列零 warn 78%）；J3 25/25；J4 replayed=23 findings=0。

## 五、现场操作段（周五 17:45 后）

统一解释器口径与周一 SOP 相同（人工巡检用 `$PY`，生产任务自带 `C:\Program Files\Python311\python.exe`；本段全部只读，无一条命令写 data/）。

**17:50-18:00 判读三步**：

1. **看任务是否触发**（异常前置检查）：
   ```
   powershell -Command "Get-ScheduledTaskInfo AgentShadowEvalWeekly"
   ```
   LastRunTime=今天 17:45、LastTaskResult=0 为正常（267011=从未运行，见周一 SOP 异常矩阵 #4 同款口径）。

2. **读主日志判结论**（唯一判读面）：
   ```
   cat "C:/项目/老田的AI助手/agent_platform/data/shadow/eval_last_run.log"
   ```
   按顺序核对五类行（对照第三节判据）：
   - 头部 `影子账本：done=X failed=Y 交易日=Z` → 账本规模是否合理（failed>0 时看 `failed 票（N 行）:` 段）
   - `[J1 无人干预]` → PASS / WAIT-PENDING
   - `[J2 秩相关]` → rho 与 n；**必须同时看** `[warn]`（价格源/并列零）与 `[pending]/[skip]` 行，再决定采信与否
   - `[J3 lineage]` → PASS / FAIL / DEGRADED
   - `影子评估结论：` → PASS / PARTIAL / PENDING（按第四节语义表处置）
   - 顺手看 `[J4 perf] 沉淀 recorded=…` → 有此行=安全阀放行正常；出现 `[安全阀 M5-P2] persist 已拒` = 生产命令丢了 --persist-root，转异常段 #3

3. **只读交叉复核**（可选，不重跑 eval）：
   ```
   ls -la "C:/项目/老田的AI助手/agent_platform/data/perf_ledger.jsonl"
   ```
   mtime 应≈今天 17:45 后（新沉淀发生时）；行数变化与 `[J4 perf] recorded=` 对应。**不要为「验证」手动重跑 eval_shadow.py**——见红线段。

**与周一 SOP 的边界**（防撞车）：周一 SOP 管 10-19 当日链（17:10 AgentShadowDaily 首跑 + 17:25 MondayCheckpoint 门 3 判读 + 17:35 ChiefPush），判读靠 `tools/check_observation_point.py` 退出码 0/1/2；本卡只管**周五 17:45 周级评估**，退出码恒 0、判读只看 `eval_last_run.log` 内容。两条线共用 `shadow_ledger.jsonl` 但时点、工具、判读面零重叠。周一 SOP 红线 4「演示永不落真仓」在本卡的对应约束即第五节全段只读。

## 六、异常处置段

| # | 异常 | 可能原因 | 第一步取证 | 升级路径 |
|---|---|---|---|---|
| 1 | eval_last_run.log 不存在或 mtime 非 17:45 后 | 任务未触发/被禁用；StartWhenAvailable 补跑尚未发生 | `Get-ScheduledTaskInfo AgentShadowEvalWeekly`（LastTaskResult 267011=从未运行）；`ls /c/Windows/System32/Tasks/AgentShadowEvalWeekly` 存在性 | 报 lead——手动触发/重注册计划任务属系统处置，须老田拍板（参照 `docs/runbooks/2026-10-18_联动层一期验收演练.md` 白名单口径） |
| 2 | 日志首 3 字节 FF FE（中文乱码） | eval 分支重定向形态被改回 PS `1>`（19:51 mojibake 复发） | `head -c 3 data/shadow/eval_last_run.log \| xxd` | 报 lead；对照 `shadow_automation.ps1:29-33` 恢复 cmd /c 形态须拍板 |
| 3 | 日志出现 `[安全阀 M5-P2] persist 已拒` | 生产命令行丢失 `--persist-root` 参数 | 核对 `shadow_automation.ps1:33` 是否仍带该参数 | 评估结论（J1/J2/J3）仍有效，仅 J4 未沉淀；修复脚本属代码改动，须老田拍板 |
| 4 | `影子评估结论：PENDING` | J1 未过：账本交易日 <5 或覆盖率 <90%（影子链周中断账） | 看头部交易日列表缺哪几天；对照 `data/shadow/last_run.log`（17:10 链）当周是否落账 | 中性等待；若因 17:10 链断账，转周一 SOP 异常矩阵排查根因 |
| 5 | `影子评估结论：PARTIAL` 且 `[J2]` FAIL | rho<=0 或 n<3：小样本波动／并列零占比过高／价格源告警 | 先看 `[warn] 全部后验收益=0` 行——有则 J2 作废查行情源；再看 `[skip]/[pending]` 行算有效样本 | 价格源问题报 lead；单纯小样本波动记录待下周复评，勿当场调参 |
| 6 | `[J3 lineage] DEGRADED` 或 FAIL | checkpoints.db 损坏／traces 表异常／存在无 run_id 的 done 行 | 日志 stderr 告警行；`ls -la data/checkpoints.db` 看大小与 mtime | 报 lead——DB 修复属处置动作（对照周一 SOP 异常矩阵 #1 基线回滚思路），执行前须确认 |
| 7 | `[J4 perf] recorded>0 findings>0` 或 `[roster-draft]` 出现 | 新交易日行首次沉淀／阵容统计触发起草 | `tail -5 data/perf_ledger.jsonl` 看 perf_key 是否新（幂等重放不重复入账） | roster draft 仅为建议权，adopt 须人工凭据——报 lead 知悉即可 |

## 红线段（本卡三条）

1. **不手动重跑 eval_shadow.py**——带 `--persist-root` 重跑虽 perf_key 幂等（replayed 语义），但会整文件覆盖 `eval_last_run.log` 毁掉当天判读现场；确需重跑必须 `--no-persist` 或隔离 root，且须老田拍板。
2. **PENDING ≠ 失败**——J1 未过是数据不足的中性态（第四节语义表），勿按 FAIL 处置。
3. **真仓 data/ 只观察不试写**——本卡全部命令只读；演练一律走隔离 root（M5-P2 安全阀的教训：10-17 两次演示未隔离污染真仓）。

---

**既有记录对照**：`docs/runbooks/2026-10-19_影子链首跑护航预检.md:78` 已把「周五 10-23 17:45 首评」判读写为「`eval_last_run.log` 见 J4 沉淀（`recorded=` 行）而非安全阀提示；`影子评估结论：` 行非 FAIL」——与本卡第五节口径一致；`docs/runbooks/2026-10-18_联动层一期验收演练.md:93` 登记 days=32、LastTaskResult=0；三判据谱系见 `docs/audit/2026-10-10_项目全景解读与可行性评估_K-STOCK-088.md:35`、J2 修复见 `docs/changes/20261015_j2fix_changes.md`。

**声明**：本卡为只读梳理，取证过程未执行任何 eval/影子脚本，真仓 data/ 零写入（仅直读）；周五 eval 是否做隔离预演、按何口径预演，待老田拍板。
