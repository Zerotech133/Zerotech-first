# api_backtest.py 拆分方案 v1（2026-10-30 · 关羽）

> 背景：`portal/api_backtest.py` **6,395 行**（`tools/check_portal_split.py` 实测 6,394>冻结基线 6,135，已触发 WARN），是全 portal 最大冻结文件。
> 方法：复用 api_kg 已验证套路（**契约先冻结 → 纯移动分批 → 委托收口 → 上限递减**）；本单只出方案，不改代码。

## 一、现状（AST 实测，2026-10-30）

| 项 | 数值 |
|---|---|
| 总行数 | 6,395 |
| 顶层函数 | 46（行 44–890：页面 HTML builder、custom/cbt 作业、_bt2_*、策略数据等） |
| 顶层常量 | 28（行 31–525、857–890：批次/报表/状态/日志路径、SA 状态、策略文件） |
| 类 | `Handler` 1 个，**20 个方法**（行 893–6395，方法体合计约 5,500 行） |
| 外部依赖面 | `portal_api` 显式 import 12 个符号 + 模块绑定（多继承） |

### 必须保留的对外契约（portal_api 批6 导入 + 其他模块）

- `portal_api`：`fut_page_html, sellpoint_page_html, _bt_custom_start, _bt_custom_status, _cbt_start, _cbt_status, _bt2_start, _bt2_status, _api_my_strategies, _bt2_result, _bt2_nl` + `import api_backtest`（基类引用）；
- `api_misc`：`get_backtest, strategies_data`；
- `api_screen`：`_bt_resolve, _bt_jev_news, build_daily_pulse, _pulse_ago`；
- `Handler` 20 方法（多继承基类，方法集不得缺失）：
  `_bt_sectors, _bt_report_page, _bt_ind_drill_page, _bt_lhb_page, _bt_seat_page, _bt_stock_page, _bt_stock_form, _bt_jev, _bt_brief, _bt_news, _sa_result, _sa_start, _weak_cases_page, _cross_page, _nine_dim_page, _algo_market_page, _algo_activate, _fin_page, _strategies_page, _sa_page`。

## 二、目标形态（3 层，与 api_kg 对齐）

```
portal/api_backtest.py          # 收口后 ≤300 行：Handler 20 个纯委托 + 契约符号再导出
portal/backtest/__init__.py     # 空
portal/backtest/bt_consts.py    # 常量（路径/批次/报表/状态）
portal/backtest/bt_jobs.py      # custom/cbt/bt2 作业函数（_bt_custom_* / _cbt_* / _bt2_* / 策略数据）
portal/backtest/bt_pages_html.py# 顶层页面 HTML builder（fut_page_html / sellpoint_page_html 等）
portal/backtest/bt_pages_*.py   # 页面渲染方法（按域拆 4~5 个 ≤1,200 行模块）
portal/backtest/bt_api.py       # JSON API 方法（_sa_results/_sa_start/_algo_activate 等）
```

硬约束（拆分守则）：新文件 ≤1,200 行；`api_backtest.py` 只减不增；`portal_api.py` 预算 5,200 不动。

## 三、迁移步骤（每步一个工单，纯移动，门禁全绿才进下一步）

| 批 | 内容 | 行范围/方法 | 目标模块 | 验收 |
|---|---|---|---|---|
| **B0 契约冻结** | 新增 `portal/tests/test_api_backtest_contract.py` | — | — | 20 方法快照 + 12 导出符号 + 行数上限 6,395（只减不增） |
| **B1 常量+作业** | 常量 28 个、顶层函数 46 个（行 31–890） | 行 31–890 | `bt_consts.py` / `bt_jobs.py` | 契约测试 + portal 全量；`check_portal_split` 行数下降 |
| **B2 页面 builder** | `fut_page_html`(57) `sellpoint_page_html`(294) 等 | 行 44–420 | `bt_pages_html.py` | 同上；页面 smoke（fut/sellpoint 各 1 次 200） |
| **B3 页面渲染 A** | `_bt_sectors, _bt_report_page, _bt_ind_drill_page, _bt_lhb_page, _bt_seat_page, _bt_stock_page, _bt_stock_form, _bt_jev, _bt_brief, _bt_news` | 行 943–3443 | `bt_pages_report.py`（报表/情报） | 同上 + 页面 smoke |
| **B4 页面渲染 B** | `_weak_cases_page, _cross_page, _nine_dim_page, _algo_market_page, _fin_page, _strategies_page, _sa_page` | 行 3466–6395 | `bt_pages_market.py` / `bt_pages_strategy.py` / `bt_pages_fund.py`（按域再分） | 同上 + 页面 smoke |
| **B5 JSON API** | `_sa_result, _sa_start, _algo_activate` | 行 3444–3450 / 4541–4567 | `bt_api.py` | 契约 + portal 全量 |
| **B6 收口** | api_backtest 只剩委托+再导出；上限 300；更新 `tools/check_portal_split.py` FROZEN 基线 | — | — | 行数 ≤300；`_mixin_integrity` 零 diff；golden/页面 smoke |

> 说明：`_bt_stock_page`（约 1,300 行）与 `_sa_page`（约 1,300 行）单方法超模块预算 → 纯移动阶段允许所在模块 >1,200（登记例外），B6 后再立"方法体拆分"小额工单（不改行为）。

## 四、验收 / 回滚

- 每批验收：`portal/tests/test_api_backtest_contract.py` 全绿；`portal/tests` 全量；`tools/_mixin_integrity.py --check` 零 diff；`check_portal_split` PASS（行数只减）；
- 页面 smoke：fut/sellpoint/report/ind/lhb/seat/stock/jev/brief/news/cross/nine_dim/algo/fin/strategies/sa 各 1 次 200 + 关键元素存在（可先用现有人工清单，后续补 golden）；
- 回滚：每批单 commit 可 revert；契约测试是回滚哨兵（先于行为变化报警）。

## 五、风险

1. `Handler` 20 方法与 portal_api 多继承强耦合 → 必须最后收口、每批保持方法可调用（委托实现同 api_kg）；
2. 页面无自动化 golden → 每批人工 smoke 清单必做，宁慢勿漏；
3. `_bt_stock_page`/`_sa_page` 超长方法 → 纯移动期接受模块超预算，二次拆分单独立项；
4. 并行会话正在改 api_backtest 相关页面/接口 → 每批从最新 master 起分支，用窄单 + 冲突立即重放。

## 六、排期建议

| 批 | 预估 | 依赖 |
|---|---|---|
| B0 契约 | 0.5 天 | 无（可立即开始） |
| B1 常量+作业 | 0.5 天 | B0 |
| B2 页面 builder | 0.5 天 | B0 |
| B3/B4 页面渲染 | 各 1 天 | B0（可并行拆两个 worktree） |
| B5 JSON API | 0.5 天 | B0 |
| B6 收口 | 0.5 天 | B1–B5 |

合计约 **4–5 天**（可 2 人并行压到 2–3 天）。

## 七、参考

- 套路来源：`docs/plans/2026-10-28_api_kg拆分方案_v1.md` §七（5913→97 行收尾记录）
- 守则：`docs/plans/2026-10-28_portal大文件拆分守则_v1.md`
- 守卫：`tools/check_portal_split.py`、`portal/tests/test_api_kg_contract.py`（可复制为 api_backtest 契约模板）

## 八、B0 完成记录（2026-10-30，P61）

- 新增 portal/tests/test_api_backtest_contract.py（5 条）：Handler 20 方法快照/可调用、17 个外部导入符号、行数上限 6,395（只减不增）、AST 方法集漂移守卫；
- 实测 5/5 绿；后续 B1-B5 每批合并后**必须下调 LINE_CEILING**（拆分期契约哨兵）。

## 九、B1 完成记录（2026-10-30，P68）

- **api_backtest.py 6,395 → 5,248 行**（-1,147）；契约上限同步下调至 5,248（只减不增）；
- 新模块：`portal/api_backtest_minute.py`（450 行，minute 注册表/校验/重放/产物）、
  `portal/api_backtest_bt2.py`（776 行，bt2 参数化回测/我的策略/产物读取）；均为纯移动；
- 依赖单向：两个新模块不 import api_backtest；api_backtest 以 `# noqa: F401` 再导出全部符号（外部导入不变）；
- 门禁同步：`portal/mixin_baseline.json` 显式重建（模块函数并入 api_* 新文件，union 396→424，diff 可评审）；
- 测试沙箱适配：`test_api_backtest_minute.py` 改为“隔离副本模块 + 转发 ModuleType”（保持单文件沙箱语义）；
- 验证：契约 5/5、portal 339 passed（1 known flaky）、ruff 净、`check_portal_split` PASS、mixin 零 diff。

后续：B2 页面 HTML builder（fut/sellpoint 等，L44-420）→ B3/B4 页面渲染 → B5 JSON API → B6 收口至 ≤300 行。
