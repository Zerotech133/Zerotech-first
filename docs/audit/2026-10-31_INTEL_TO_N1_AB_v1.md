# INTEL_TO_N1 A/B（reviewed JEV → N1 news_event）v1

- 工单：`WO-INTEL-N1AB-20261031`（任务板 task-13；worktree `C:\项目\老田的AI助手\.worktrees\WO-INTEL-N1AB-20261031`，分支 `task/WO-INTEL-N1AB-20261031`）
- 时间：2026-10-31 11:54 起（Asia/Shanghai）
- 拍板依据：`docs/plans/2026-10-31_情报板块研发方案_v1.md` §六（INTEL_TO_N1，默认关）与 `docs/plans/2026-10-31_情报板块研发计划_v1.md` I2（`WO-INTEL-P2-N1` / `WO-INTEL-P2-AB`）
- 范围：`agent_platform/engine/tools.py::make_context` 接线 + 新增 `agent_platform/intel_context.py` + 测试 + 本文档
- **不合并、不重启生产、不改生产 prompt/编排/评分契约**

---

## 1. 结论

1. 新增 feature flag **`INTEL_TO_N1`（默认 off）**：off 时 `make_context` 执行路径与旧版完全一致，输出**逐字一致**（有黄金文本 + 整 dict 一致性测试锁定；off 不导入/不调用 `intel_context`）。
2. 新增 `agent_platform/intel_context.py`：**只读** `news_collector/news.db -> jev_review_queue` 中 `status='resolved'` 的记录，按股票 code 归属（stocks / ts_code / 名称 / 标题代码 / companies 五路）生成 `news_event` 文本，显式带 `source=reviewed_jev` 与 `event_id` / `review_id`；无可用记录返回 `source=price_proxy` + `reason`；DB 缺失/坏/队列缺表同样回退并给出 reason；坏行不抛、收入 `bad_rows`（fail-visible）。
3. on 时 `make_context.news_event` 优先用 reviewed 文本；无可用复核证据则**保留旧 price_proxy 文本**（与 off 逐字一致），不出现「已核验」类伪造措辞。
4. A/B 以「同标的、同时段、两臂进程」方式运行：A=off（price_proxy）、B=on（reviewed_jev）；默认计划任务不动，手工隔离跑（`--root`/`--rehearsal`）采样，指标达标后再由老田/N1 决定是否默认开。
5. 当前运行态前提：2026-10-31 只读实测 `jev_review_queue` 为 **10,525 pending / 0 resolved** → flag on 暂等价 off（100% fallback），需先按 I1 产生人工复核记录，A/B 才有 B 臂样本。

---

## 2. Flag 语义

| `INTEL_TO_N1` | 行为 |
|---|---|
| 未设置 / `0` / `false` / `off` / `no` | **默认**。`make_context` 完全按旧逻辑（K 线派生 price_proxy）；不导入 `intel_context`，无额外 IO/打印 |
| `1` / `true` / `yes` / `on` / `y` | A/B 观察臂：`news_event` 优先 reviewed JEV；无可用 → 回退 price_proxy |

- flag 只影响 `make_context()["news_event"]` 这一路输入；`market_technical` / `fundamental` / `final_decision` / `name` 及 Hermes stock-data 补充逻辑均不变。
- **不改生产 prompt 文件**：`run_shadow.py`、`interpreter.py`、`registry/team_invest.json`、各角色 SOUL/prompt、评分/决策契约本单零改动；flag 关闭时连调用分支都不进入。

---

## 3. 实现

### 3.1 `agent_platform/intel_context.py`（新增，只读）

数据源（SQLite URI `file:...?mode=ro`，不建表/不写/不触发回填）：

```sql
SELECT id, queued_at, conf, bias, title, stocks, original_jev, status,
       reviewer, reviewed_at, decision, note
FROM jev_review_queue
WHERE status='resolved'
ORDER BY COALESCE(NULLIF(reviewed_at,''), queued_at) DESC
LIMIT 500   -- MAX_SCAN：只扫最近复核的 N 条（复核产能 200/日量级）
```

code 归属五路（任一命中即归属，`matched_by` 记通道）：

| 通道 | 规则 |
|---|---|
| `stocks` | JSON 数组条目归一：`600519` / `600519.SH` / `sh600519` / 名称 |
| `companies` | `original_jev.companies` 同上（str/dict 兼容 code/name/symbol 键） |
| `title` | 标题含该 code 的独立 6 位 token，或含该 code 的现用名/别名 |
| 名称 | 可选映射：`INTEL_N1_STOCK_NAMES` / `quant_backtest/data/stock_names.json` / `agent_platform/data/stock_names.json` / `news_config.watchlist`（都缺则只用代码匹配，不影响主链） |

复核可用性：

- `decision in {correct, accept, uncertain, needs_evidence, 空}` → 可用（文本如实写复核结论/复核人/时间）；
- `decision in {wrong, reject, rejected, invalid, false}` → **不作为方向证据**（`rejected` 计数可见；只有 rejected 时回退 price_proxy + `only_rejected_reviews`）；
- 坏行（`original_jev` JSON 坏/类型坏、缺 id、`stocks` 坏）→ 跳过并进 `bad_rows=[{id,error}]`，**不抛**；`stocks` 坏但 title/companies 命中时仍可用且坏字段可见。

返回结构（恒不抛）：

```json
{"source": "reviewed_jev | price_proxy", "reason": "",
 "text": "[reviewed_jev] 人工复核情报（source=reviewed_jev；只含 status=resolved 记录）…",
 "code": "600519", "event_ids": ["sina:xxx"], "review_ids": ["rv-xxxxxxxxxxxx"],
 "items": [{"event_id","review_id","title","bias","bias_cn","conf","decision",
            "reviewer","reviewed_at","note","summary","matched_by"}],
 "bad_rows": [{"id","error"}], "errors": [], "rejected": 0,
 "counts": {"scanned","matched","usable"}, "db": "<path>"}
```

- `review_id`：表内无独立复核 id 列 → 由 `(id, reviewer, reviewed_at)` 派生稳定 `rv-<sha256[:12]>`（可追溯、可复现；后续 ReviewRecord 契约落地后可替换为真 id）。
- 回退 `reason` 枚举：`no_reviewed_jev`（无 resolved 记录）/ `only_rejected_reviews` / `reviewed_unusable` / `news_db_missing` / `queue_missing` / `db_error` / `bad_code`。
- CLI 探针（A/B 采样）：`python -m agent_platform.intel_context --code 600519 --db <news.db> --json`。

### 3.2 `engine/tools.py::make_context` 接线（最小 diff）

```python
out = {...}                                   # 旧逻辑逐字不变
if _intel_to_n1_enabled():                    # off 时整段不执行
    try:
        _apply_intel_to_n1(out, code)         # reviewed 可用→换 news_event；否则保持旧文本
    except Exception:                         # 接线失败不崩主链，保持 price_proxy
        logging.warning(...)
# 原 Hermes stock-data 追加密沿用
```

- on 且 reviewed 可用：`out["news_event"] = "[reviewed_jev] …"`（含 event/review id）；
- on 但不可用：不改 `news_event`（旧 price_proxy 文本原样保留），打印一行 `[intel_to_n1] source=price_proxy reason=... code=... bad_rows=N` 供 A/B 归因；
- on 且成功：打印 `[intel_to_n1] source=reviewed_jev code=... events=N rejected=N bad_rows=N`；
- 异常（含 `intel_context` 导入失败/查询崩溃）：`logging.warning` 后保持旧文本，不打断 run_shadow。

---

## 4. A/B 设计

### 4.1 假设与分流

- **H1**：reviewed JEV 是比 K 线派生代理更直接的事件证据 → D1 裁决依据更可追溯、质量不降、延迟可忽略；
- **H0**：reviewed 文本引入 LLM 噪声/过长 prompt → 决策质量或时延变差，或 reviewed 覆盖率不足使两臂不可比。
- 分流单位：**同批次（同日、同 codes）两臂进程**；A 臂 `INTEL_TO_N1` 未设，B 臂 `=1`；其余环境（模型/温度/prompt/数据根结构）一致。
- 建议样本：同一批标的（run_shadow 篮子 5 票或 watchlist）≥5 个交易日、≥100 条 D1 决策；A/B 期间数据根用 `--root` + `--rehearsal` 隔离（禁飞书推送，零污染生产 ledger）。

### 4.2 指标

| 维度 | 指标 | 采集方式 | 判据（建议） |
|---|---|---|---|
| 覆盖率 | reviewed 覆盖率 = reviewed 可用的标的/决策数 | `intel_context` CLI 逐 code；或两臂日志 `source=` 计数 | B 臂 ≥80%（有 resolved 证据的标的）；<80% 先补复核产能 |
| 回退可见性 | fallback reason 分布（no_reviewed_jev/only_rejected/db_error…） | 两臂 `last_run.log` grep `[intel_to_n1] source=price_proxy` | 无 `db_error`/`queue_missing`；fallback 全部带 reason |
| 质量 | D1 决策 JSON 解析成功率、failed/parse_fail 行数、`change_gate` | shadow ledger 两臂对比；`dec_audit` 后验（5 日） | B 臂不低于 A 臂；change_gate 不降 |
| 延迟 | 每标的 `make_context` 增量、全链 elapsed | `intel_context` CLI `elapsed_ms`；两臂 ledger elapsed | B 臂增量 <50ms（只读查询+渲染）；全链无显著劣化 |
| 安全/诚实 | 坏行数、rejected 数、出现「已核验」伪造措辞 | `bad_rows` 计数；文本抽查 | 坏行 fail-visible；0 伪造措辞；0 崩溃 |
| 一致性 | 同标的同日两臂方向差异（bull/bear/neutral） | ledger decision 对比 | 差异可解释（事件 vs 代理）；不可解释差异记入复盘 |

### 4.3 启用步骤（A/B 观察，不碰默认生产）

默认生产（17:10 计划任务）**保持 flag 未设置 = off**，本单不修改计划任务/包装器。

```powershell
# B 臂（on）：隔离数据根 + 预演模式，避免污染生产账本
$env:INTEL_TO_N1 = "1"
$env:SHADOW_DATA_ROOT = "C:\Temp\shadow_ab_20261101_on"
python agent_platform\run_shadow.py --rehearsal --root C:\Temp\shadow_ab_20261101_on --codes 600519,000858,601318,300750,600036

# A 臂（off）：同命令，去掉 INTEL_TO_N1（或显式 =0），换数据根
Remove-Item Env:INTEL_TO_N1
$env:SHADOW_DATA_ROOT = "C:\Temp\shadow_ab_20261101_off"
python agent_platform\run_shadow.py --rehearsal --root C:\Temp\shadow_ab_20261101_off --codes 600519,000858,601318,300750,600036
```

- 若要改生产任务：在 `shadow_automation.ps1` run 分支加 `$env:INTEL_TO_N1='1'`（**ops 改动，另一个工单**；本单允许文件不含该脚本）；
- 每轮结束 `Remove-Item Env:INTEL_TO_N1` 或开新终端，保证默认 off。
- 复核侧前提：N1 复核工作台（`/api/jev/review/*`，`portal/api_jev_review.py`）先把低置信样本 `resolve`（correct/wrong/uncertain），B 臂才有 `status='resolved'` 数据。

### 4.4 回滚

| 层级 | 动作 | 生效 |
|---|---|---|
| 运行开关 | `Remove-Item Env:INTEL_TO_N1`（或 `=0`） | 下一次 run_shadow（一次性进程）即回旧行为；无重启 |
| A/B 数据 | 隔离数据根在 `C:\Temp\...`，直接丢弃/归档 | 生产 ledger 从未写入 |
| 代码级 | `git revert <commit>`（或关 flag 即可，无需回滚代码） | flag 默认 off，代码保留 |
| 若有包装器改动（另单） | 移除包装器中的 `$env:INTEL_TO_N1` 行 | 下一次计划任务 |

- 回滚后验收：`make_context` 输出与旧版逐字一致（本单测试已锁定）；`[intel_to_n1]` 日志消失。

---

## 5. 测试证据

```text
python -m pytest agent_platform/tests/test_intel_context.py agent_platform/tests/test_intel_to_n1.py -q
→ 25 passed in 0.99s

python -m pytest agent_platform/tests/test_make_context_empty.py agent_platform/tests/test_hermes_bridge.py \
  agent_platform/tests/test_auto_hooks_import.py agent_platform/tests/test_hermes_orchestrator.py \
  agent_platform/tests/test_run_strategy_shadow.py -q
→ 13 passed in 1.50s

python -m pytest agent_platform/tests -q       # 全量回归
→ 7 failed, 2618 passed, 7 skipped, 1 xfailed in 278.36s
  7 项失败：4 项为既有 worktree 环境类（test_evidence_collect / test_unified_projection /
  test_usage_ledger 的 `*_real_repo_read_only*` 读主仓状态；test_legion_intent_contract
  读 worktree 内不存在的 `.worktrees/tasks.json`）；另 3 项为顺序/状态 flaky
  （test_huangzhong_mcp::test_run_review_requires_confirm_and_audits /
  test_legion_command::test_minutes_bad_length_rejected /
  test_legion_ide_audit::test_acquire_lock_is_exclusive——单独跑均通过，后两项本就在
  test_gate 已知 flaky 名录），与本次改动无关。

powershell -NoProfile -File tools/test_gate.ps1 -Suite fast   # 合并质量闸
→ GATE PASS：无新增失败（portal+kstock+ide）
```

真实 news.db 只读探针（0 resolved → fallback，不炸）：

```text
python -m agent_platform.intel_context --code 600519 --db "C:\项目\老田的AI助手\news_collector\news.db" --json
→ {"source": "price_proxy", "reason": "no_reviewed_jev", "counts": {"scanned": 0, ...}}
```

覆盖矩阵：

| 用例文件 | 覆盖 |
|---|---|
| `test_intel_context.py` | flag 默认关/on 值；无 resolved → price_proxy+reason；DB 缺失/队列缺表 fail-visible；reviewed 文本含 `source=reviewed_jev`+event_id+review_id；五路归属（ts_code/bare/sh/name/title/companies）；坏行（坏 jev/坏 stocks/坏类型/缺 id）不炸且可见；仅 rejected → 回退、混合 → 只用可用行；limit + 新→旧；最小表结构（缺 reviewer/decision/reviewed_at 列）兼容；只读连接（文件只读仍可读、内容零改动）；CLI 探针 |
| `test_intel_to_n1.py` | **off 逐字一致**（黄金文本 + unset/0/false/off 四态整 dict 相等；off 不调用 `reviewed_news_event`、无额外打印、模块不可用也不影响）；on reviewed 替换文本且带 id/来源；on fallback = 旧文本逐字一致且无「已核验」；on 下 DB 缺失/坏行/接线异常均不崩主链；Hermes 补充在 reviewed/off 两路径行为不变 |

---

## 6. 未决 / 边界

1. **当前 0 resolved**：`jev_review_queue` 有 10,525 pending、0 resolved（只读实测）→ B 臂暂为 100% fallback；A/B 需与 I1 复核抽样（200/日）排期联动。
2. **review_id 为派生值**：`rv-<sha256(id|reviewer|reviewed_at)[:12]>`；待 ReviewRecord 契约（`WO-INTEL-P1-CONTRACT`）落地后换真 id。
3. **D1 `l1_basis` 未接线**：本单只做 `make_context.news_event` 输入 A/B；把 evidence 来源写进 D1 裁决依据属 I2 后续子项（未改生产 prompt/契约）。
4. **名称映射依赖运行态文件**：`stock_names.json`（`quant_backtest/data/`）在 worktree 数据根缺失时，仅代码/stocks/title 匹配可用；生产主仓存在该文件（5911 code2name/11682 name2code），A/B 覆盖率不受影响。映射读取失败只降级为代码匹配并 `logging.warning`。
5. **未做时间窗过滤**：默认扫最近 `MAX_SCAN=500` 条 resolved（典型 2–3 天复核量）；如需「只用近 N 日」，可在后续加 `INTEL_N1_SINCE_DAYS`（本单未启用，避免老复核事件干扰新样本的口径需与 N1 商定）。
6. 不新建数据库/常驻服务/端口；不恢复 8788 staging；不做 UI（N1 页面证据来源展示属 `WO-INTEL-P2-UI`）。
