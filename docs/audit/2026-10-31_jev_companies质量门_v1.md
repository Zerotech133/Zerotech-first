# JEV companies 质量门（WO-INTEL-COMPANIES-AUDIT-20261031）

## 背景

人工复核发现 `companies` 错链比 bias 标错更危险：错码会把新闻计入错误股票的
情绪池。首轮 targeted 8 条中即发现：

- `300542` 挂“天阳科技”（主数据：300542=新晨科技，天阳科技=300872）
- `301258` 挂“天准科技”（主数据：301258=富士莱，天准科技=688003）
- 村BA/香氛等弱链

## 质量门

`tools/jev_companies_audit.py`（只读）：

1. companies 必须 list[str]，每项 `^\d{6}$`，禁逗号串/重复；
2. 代码必须在本地主数据 `quant_backtest/data/ashare/_names.json`；
3. 代码↔名称一致：文中出现另一家已知公司名、且不属于该代码 → `company_name_mismatch` 硬错误；
4. 没有名称证据 → `weak_link_name_not_in_text`；
5. 同标题/同 hash 重复；
6. conf/impact 零方差 → `unreliable`；
7. 文体风险：网评/研讨/报告发布/例会/趋势/软文 + bull/bear 且无具体数据 → `style_risk`；
8. **reason-bias 一致性**：bear 的 reason 出现利多/利好/预期升温，或 bull 的 reason
   出现利空/下跌/承压 → `reason_bias_contradiction`，hard；
9. **asset anchor**：bull/bear 且无 companies/anchor → `asset_anchor_missing`，
   默认警告；`--strict-anchor` 才阻断；
10. **事件簇**：胡塞/也门/沙特/伊朗/中东/油价等 ≥3 条 → `event_clusters`，
    提示因子聚合要限权/去重。

## 批次入库前拦截

`tools/intel_batch_preflight.py` 对 `intel_review_batch.py` 生成的 JSONL/CSV 直接检查：
hard（格式/未知码/名称张冠李戴/重复 id）→ 退出 1，**不进入人工批次**；
weak/style → 警告，`--strict` 时也阻断。

`tools/intel_review_batch.py` 默认在写批次前调用本闸：
hard → `blocked_preflight`、拒绝写文件；主数据缺失时明确 skip；
`--no-preflight` 仅用于紧急回滚。

```powershell
python tools/intel_review_batch.py --limit 50 --write
```

需要“剔除坏行、保留干净批次”时：

```powershell
python tools/intel_review_batch.py --limit 50 --filter-hard --write
```

`--filter-hard` 会剔除 hard 错误 id 和重复组（每组保留第一条），并在 JSON 中记录
`preflight_filtered.removed/removed_ids/kept`。

同时输出人工填写 CSV（UTF-8-SIG、QUOTE_ALL、companies 用 `;` 分隔）：

```text
intel_review_batch_<date>.csv
```

这样 Excel 不会把 `128893;600031` 当千分位数字。

## anchor 派生

`news_collector/news_ai.py` 新增 `_derive_anchor()`：不改 TypeSafe 题，不重跑历史，
新判输出 `anchor`：

```text
companies 单票 → stock
companies 多票 → stock_basket
scope=commodity/fx/crypto → commodity/fx/crypto
scope=cn/hk/us → market
其它 → None
```

历史行不自动回填；后续批次/质量门可以据此判断是否有资产锚。

## 用法

```powershell
# 全量
python tools/jev_companies_audit.py --json

# 只看 pending（推荐）
python tools/jev_companies_audit.py --status pending --json

# 主数据在 worktree 外时显式指定
python tools/jev_companies_audit.py `
  --db C:\项目\老田的AI助手\news_collector\news.db `
  --master C:\项目\老田的AI助手\quant_backtest\data\ashare\_names.json `
  --status pending --json

# 严格模式：weak/style 也判失败
python tools/jev_companies_audit.py --status pending --strict
```

退出码：0=无硬错误；1=有硬错误；2=输入错误。

## 首轮结果（pending 172）

```text
hard=26  weak=45  style=3  duplicate_titles=2  duplicate_hashes=1
zero_variance: conf_distinct=1 impact_distinct=1 → unreliable=true
```

其中研报机构名（东吴/国金证券）已排除，不再误判为目标公司。

## 边界

- 只读，不改队列、不改原 JEV；
- 名称一致性依赖本地主数据；主数据过期时 `unknown_code` 需人工确认；
- 弱链不等于错误，只提示“文中无名称证据”；
- 不替代人工复核，只做入库前拦截。
