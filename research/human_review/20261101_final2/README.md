# 20261101_final2 · 人工复核补齐 2 条（准备材料）

> 工单：`WO-VAL-HUMAN2-20261101`（task-30）｜生成：2026-11-01 14:22（Asia/Shanghai）
> 性质：**只准备材料，不代签、不写 DB、不联网**。`shortlist.csv` 的 `decision` 列**留空**，由老田本人填写；Agent 不填人工结论、不把机器/AI 建议当人工。
> 目标：人工复核 `resolved_human` **48 → 50**（人工 50 满额）；当前 `pending=152`。

## 1. 队列事实（只读实测，as-of 2026-11-01 14:22）

- 数据源：`news_collector/news.db` → `jev_review_queue`（只读；db mtime 2026-11-01 14:20:58）
- 机器预审：`C:\Temp\intel_pre_review_20261031.jsonl`（`intel-pre-review/v1`，2026-10-31 12:45 生成）

| total | pending | resolved | resolved_human | resolved_ai_first_pass | unreviewed_legacy |
|---|---|---|---|---|---|
| 10,525 | **152** | 98 | **48** | 50 | 10,275 |

## 2. 选择规则与本批 2 条

选择规则（按任务）：①优先 H37 银行 / H38 游戏；②**必须有明确公司/代码**（`companies`/`stocks` 非空且可核）；③排除重复标题、AI 已审（`review_type=ai_first_pass`）、无锚条目。

扫描结果（pending 152）：带锚 50 条；关键词分类命中——**银行类 5 条（带锚 2）**、**游戏类 0 条**、AI 终端类 3 条（带锚 2）。故：

| # | jev_id | 标题 | 归属 | 资产锚（板块 + 代码） | JEV reason（模型判读，供判断） | machine_suggestion |
|---|---|---|---|---|---|---|
| 1 | `em:202610223903056061` | 银行业要积极适配贷款利率定价基准改革 | **H37 银行政策脉冲**（政策预期类） | 申万银行 L1：工商银行 `601398.SH` / 兴业银行 `601166.SH` / 中国银行 `601988.SH` / 农业银行 `601288.SH` / 建设银行 `601939.SH`（5 只均为银行成分，逐码核 `stock_basic`） | 利率市场化深化将推动银行优化定价机制，长期利好行业稳健发展。 | accept（score=45，stable_judgement_accept） |
| 2 | `em:202610233906419956` | 歌尔股份：第三季度净利润同比下降1.51% 环比增长19% | **H39 AI 终端主题**（替 H38，见下） | 歌尔股份 `002241.SZ`（申万电子 L1；H39 锚池一级候选）⚠ 库内 `companies=832479` 在 `stock_basic` **无此代码**，以上游 002241.SZ 为准 | 环比大幅增长显示经营回暖，抵消部分同比下滑影响。 | accept（score=45，stable_judgement_accept） |

**为什么第 2 条不是 H38**：pending 152 中**没有任何**游戏/版号/Meta 眼镜类条目（标题+公司词表扫描 = 0），不存在满足「明确公司/代码」的 H38 候选。为不虚构游戏锚，改取同批三假设中的 **H39（AI 终端主题扩散）** 相关锚（歌尔股份，H39 锚池一级候选），并在 CSV `note` 与下表如实标注「H39 相关替代」。

**排除记录**：

- 银行类另一条带锚项 `em:202610203901445273`（豪威集团 `600884`「取得不超过4.5亿元回购专项贷款承诺函」）= **个股融资事件**，不是银行板块政策事件 → 不属 H37；
- AI 终端类另一条 `em:202610263908045671`（苹果加单传闻/蓝思科技）为**传闻类**且库内码 `300751` 与蓝思科技实际 `300433.SZ` 错配 → 未选；
- 重复标题：pending 中仅 2 对重复（「国务院学位办…」×2、「文化遗产保护法」×2），均不涉及本批；
- 本批 2 条均 `status=pending`、`review_type=''`（空），**未被 AI 初筛**（AI 初筛 50 条在 `review_type=ai_first_pass` 单独列账）。

## 3. 回填步骤（老田本人执行；Agent 不代填、不代签）

> 本目录只准备材料；下列命令均为**人工确认后由本人执行**。`reviewer` 必须由本人填真实姓名，禁止 `N1-AI` 或任何含 “AI” 的 reviewer（回填工具会拒绝，防 AI 代签）。
> 执行目录：**主仓根** `C:\项目\老田的AI助手`。回填工具按自身文件位置解析 `news.db`，在 worktree 内执行会报「news.db 不存在」（已实测）。

### Step 0 — 回填前确认两条仍在 pending（只读）

```powershell
$py = @'
import sqlite3
ids = ['em:202610223903056061','em:202610233906419956']
con = sqlite3.connect('file:C:/项目/老田的AI助手/news_collector/news.db?mode=ro', uri=True)
for i in ids:
    print(i, con.execute('select status, review_type from jev_review_queue where id=?', (i,)).fetchone())
'@
$py | C:\项目\py312\python.exe -
```

期望两行均为 `('pending', '')`；夜间 JEV 回填后 pending 可能变化，**回填前必须重跑本步**。

### Step 1 — 填 `shortlist.csv`

只填 `decision`（三选一：`correct` / `wrong` / `uncertain`）与可选 `note`；`machine_suggestion` 是机器建议，**不要改、不要当结论**；`jev_id` 不要改。

### Step 2 — 生成回填 CSV（映射 `jev_id`→`id`，补 `reviewer`）

在仓库根目录执行（`$rev` 由本人填写）：

```powershell
$src = 'research\human_review\20261101_final2\shortlist.csv'
$dst = 'C:\Temp\human_review_20261101_final2\apply.csv'
$rev = '<复核人姓名（如：老田）>'          # 不得含 "AI"
New-Item -ItemType Directory -Force (Split-Path $dst) | Out-Null
Import-Csv $src -Encoding UTF8 | ForEach-Object {
  [pscustomobject]@{ id = $_.jev_id; decision = $_.decision; reviewer = $rev; note = $_.note }
} | Export-Csv $dst -NoTypeInformation -Encoding UTF8
```

### Step 3 — dry-run 校验（不写库）

```powershell
C:\项目\py312\python.exe tools/intel_human_batch_apply.py --csv C:\Temp\human_review_20261101_final2\apply.csv --json
```

期望：`ok=true`、`dry_run=true`、`would_write=2`；若有 `bad_decision`/`reviewer_empty`/`not_pending`/`ai_reviewer_forbidden` 等错误 → 先修正，**不要 --apply**。

### Step 4 — 正式写库（`review_type=human`）

```powershell
C:\项目\py312\python.exe tools/intel_human_batch_apply.py --csv C:\Temp\human_review_20261101_final2\apply.csv --apply
```

工具行为（既有 v1 契约）：先做 sqlite 一致性备份（默认 `C:\Temp\news.db.human.bak-<stamp>`）→ 单事务只更新 `status='pending'` → 写 `review_type='human'` → 校验 pending 减少数==写入数 → manifest 落 `C:\Temp\intel_human_batch_apply_manifest.json`（含 backup、written、ids）。

### Step 5 — 校验 `resolved_human=50`

```powershell
$py = @'
import sqlite3
con = sqlite3.connect('file:C:/项目/老田的AI助手/news_collector/news.db?mode=ro', uri=True)
row = con.execute("select sum(status='resolved' and review_type='human'), sum(status='resolved' and review_type='ai_first_pass'), sum(status='pending'), sum(status='resolved') from jev_review_queue").fetchone()
print('resolved_human=%s resolved_ai_first_pass=%s pending=%s resolved=%s' % row)
'@
$py | C:\项目\py312\python.exe -
```

期望（本批 2 条都成功时）：

```text
resolved_human=50   resolved_ai_first_pass=50   pending=150   resolved=100
```

- 若只写成功 1 条：`resolved_human=49 / pending=151`，需补齐另一条（检查其是否已非 pending）；
- 备选校验：门户 `GET http://127.0.0.1:8734/api/jev/review/summary`（需门户登录会话；未登录 302/401 属既定口径，不当作失败），看 `resolved_human`。

### Step 6 — 回滚

- `--apply` 输出与 manifest 均含 backup 路径；停写后用该 backup 还原 `news_collector/news.db` 即可回滚（单文件、单事务，无其他副作用）；
- manifest 默认 `C:\Temp\intel_human_batch_apply_manifest.json`（审计用，勿手改）。

## 4. 风险与注意

1. **DB 公司码错配（本批已暴露）**：第 2 条库内 `companies=832479` 在 `stock_basic`（5,577 行）不存在，歌尔股份实际为 `002241.SZ`；第 1 条 5 个银行码全部核验通过。人工请**以 `anchor` 列为准**；该上游映射问题建议另立数据治理单（本单不改 DB）。
2. **H38 无候选**：pending 152 中游戏/版号类 0 条，第 2 条为 **H39 相关替代**；不得为凑 H38 虚构标的或锚。
3. **machine_suggestion 不是人工结论**：两条机器建议均为 `accept`；第 2 条模型判 `bull` 但标题净利同比 `-1.51%`，方向是否成立需人工独立判断。
4. **reviewer 纪律**：回填工具拒绝 `N1-AI`/含 “AI” 的 reviewer；人工结论只能由本人填写，AI 不得代签。
5. **时间漂移**：pending 会随 JEV 夜间回填变化；`--apply` 只更新 pending，非 pending 会 fail-visible（不会覆盖已 resolved）。
6. **边界**：本目录不写 DB、不注册计划任务、不联网；`decision` 留空是默认状态，本 README 不产生任何人工裁决。
7. **本批上限**：本批只补 2 条（48→50）；之后人工计数以 `resolved_human` 为唯一口径（`resolved` 总数含 AI 初筛，不可混用）。

## 5. 证据与可复现

- `shortlist.csv` — 2 条候选（本目录）；`decision` 全空；字段：`jev_id,title,companies,reason,anchor,machine_suggestion,decision,note`。
- 公司代码核验：`quant_backtest/data/tushare/stock_basic`（as-of 2026-10-30），银行 5 码全中；`832479` 无此代码；`002241.SZ=歌尔股份`。
- 机器预审：`C:\Temp\intel_pre_review_20261031.jsonl`（两 id：`machine_decision=accept`、`score=45`、`reason_code=stable_judgement_accept`）。
- 回填/校验工具：`tools/intel_human_batch_apply.py`（v1：只接受 `correct/wrong/uncertain`；只更新 `pending`；拒绝 AI reviewer；备份+单事务+pending 减少数校验+manifest）。
- 批次生成参考：`tools/intel_review_batch.py`（本批为人工精选 2 条，非整批 50 条再生成）。
- **Agent 预检（只读，已完成）**：用临时占位 CSV 在主仓根执行 `intel_human_batch_apply.py --json`（dry-run）→ `ok=true / dry_run=true / would_write=2 / valid=2`；前后 `news.db` sha256 一致（`43E12F0F…C0013D`），**未写库**。人工填好 decision/reviewer 后仍须按 Step 3 重跑 dry-run 再 `--apply`。

## 6. 变更记录

| 版本 | 日期 | 说明 |
|---|---|---|
| v1 | 2026-11-01 | 首版：从 pending 152 选 2 条（H37 银行 ×1 + H39 相关替代 ×1）；`decision` 留空；只准备材料，不代签、不写 DB。 |
