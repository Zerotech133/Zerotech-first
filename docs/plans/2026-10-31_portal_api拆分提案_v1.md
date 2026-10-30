# portal_api 拆分提案 v1（A5 · 2026-10-31 · 关羽）

- 背景：`portal/portal_api.py` 当前 **5,153 / 5,200 行（99.1%）**，再增 ~47 行即触发 `mixin_integrity` 行数门 FAIL；且 Handler 内 `do_GET` 单函数 **2,031 行**、`do_POST` 547 行，已到可维护性红线。
- 先例：`api_backtest` 拆分（P54–P75，6,395→285 行）验证了"契约先行 + 纯移动"的低风险路径，本提案复用同一方法论。
- 性质：**L1 提案/施工单**（不直接改代码；施工需独立 worktree + 测试 + 合并门）。

## 一、现状测量（AST，2026-10-31）

| 项 | 数值 |
|---|---|
| portal_api.py 总行 | 5,153 / 预算 5,200 |
| Handler 类行数 | L605–5124（4,520 行） |
| Handler 方法数 | 34（`do_GET` 2,031 / `do_POST` 547 占 62%） |
| 大方法候选 | `_portal_page` 214、`_api_onboard` 202、`_linkage_page` 131、`_api_health` 119、`_api_jev_stack` 117、`_nl_page` 114、`_sys_health` 110、`_html_file` 82 |
| 已有 mixin 模块 | `api_misc` 2,705；`api_chanlun_pages` 2,328；`api_portfolio` 2,322；`api_common` 1,589；`api_screen` 1,339；`api_backtest_*` 系列… |
| 门禁约束 | `_mixin_integrity`：Handler 方法集零 diff（新增需显式 --baseline）；行数≤5,200；`portal/tests` 416 passed；fast 门 |

## 二、B0 契约（先冻结，再动代码）

1. **行数契约**：每批完成后 `portal_api.py` 行数只减不增；目标序列 5,153 → **4,800 → 4,400 → 4,000 → ≤3,600**（对齐 P4 完工口径 3,370，允许分阶段）；
2. **方法集契约**：`mixin_integrity` 基线（186 methods / 477 funcs）每批合并前跑 `--root <worktree>`，**零 diff**；新模块方法并入并集、Handler 保留**薄委托**；
3. **路由契约**：`do_GET`/`do_POST` 的**路由串与响应语义不变**；每批用现有 portal 测试 + 冒烟（`/`、`/api/health`、`/devops`、`/os-entry`、`/btview`、`/screen`、`/legion`）回归；
4. **纯移动**：搬函数体不改逻辑；仅允许 `self._xxx` 引用改为 `self._xxx`（Mixin 同 self）与 import 补齐；
5. **兼容**：对外符号（portal 测试/其他模块 import）保持可 import；必要时在新模块顶部 re-export。

## 三、分批方案（建议顺序）

| 批 | 内容 | 来源方法 | 目标 | 预估减行 |
|---|---|---|---|---|
| **B1** | 页面渲染族 | `_portal_page`、`_linkage_page`、`_nl_page`、`_html_file`、`_html/_html_open/_html_more`、`_hub_market_tabs` | 新 `api_pages_portal.py` | ~600 |
| **B2** | 健康/观测族 | `_api_health`、`_sys_health`、`_api_jev_stack`、`_api_ctl_alert`、`_sub_status_*`、`_freshness_warmup` | 新 `api_health_portal.py` | ~350 |
| **B3** | 管理/雇佣/持仓直达 | `_api_onboard`、`_api_manager_roster`、`_manager_run_json`、`_api_manager_hire/fire`、`_api_paper_direct_open`、`_api_reception`、`_api_minutes_inbox` | 新 `api_manager_portal.py` | ~400 |
| **B4** | do_GET 路由分发瘦身 | 将 do_GET 内按前缀的 `if/elif` 分派改为**注册表驱动**（route_prefix → self._method），行为等价 | Handler.do_GET | ~800+ |
| **B5** | do_POST 同法 | 同 B4 | Handler.do_POST | ~300 |
| **B6** | 收口 | 剩余小方法归并；行数 ≤3,600；清理未用 import | — | 余量 |

> B4/B5 属结构改动（非纯移动），需单独评审/回滚；若风险偏高可停在 B3（约 4,000 行，仍远低于预算）。

## 四、验收矩阵（每批）

1. `python tools/_mixin_integrity.py --check --root <worktree>` → 零 diff；
2. `python -m pytest portal/tests -q` → 基线 416 passed（新增失败即回滚）；
3. 路由冒烟（dev 起服或 `portal/test_legion_*` 类测试 + 手动 GET）：`/`、`/api/health`、`/api/overview`、`/devops`、`/os-entry`、`/btview`、`/screen`、`/legion`（登录后）；
4. `python tools/devtask.py merge <TID>` 通过 fast 门（含行数门）；
5. 每批回执记录：前后行数、移动方法、测试数。

## 五、风险与回滚

| 风险 | 处置 |
|---|---|
| Mixin 方法集漂移 | 每批跑 `--root` 校验；基线变更须同提交显式 `--baseline`（评审可见） |
| 路由分派错位（B4/B5） | 先加"路由表一致性测试"（遍历注册表 vs 旧 if/elif 名单 diff=0），再切换 |
| 并行线冲突（portal_api 高频改动） | 每批窗口 ≤1h、开工前 rebase master；遇冲突先窄单 |
| 回滚 | 单批 revert 即可；B0–B3 均为纯移动，回滚零语义风险 |

## 六、责任与排期

| 批 | Owner | 建议窗口 | 依赖 |
|---|---|---|---|
| B0 契约冻结 | 研发部（ARCH1 确认） | 11-03 前 | 本提案 |
| B1/B2/B3 | 研发部 DEV | 11-04 ~ 11-07 | B0 |
| B4/B5 | 研发部资深 + 关羽评审 | 11-10 ~ 11-13 | B1–B3 稳定 |
| B6 收口 | 研发部 | 11-14 | 全部 |

> 关羽角色：出提案/验收/门禁复核，不直接持有 B1–B6 施工（研发部执行）。

## 七、与既有计划的关系

- 承接 `docs/plans/2026-10-10_P4拆分施工图.md` 与 P4 完工总结的"只改不扩"口径；
- 纳入 `2026-10-30_零界研发计划_四周_v1.md` W2/W3 工程基建项；
- 行数门已是 fast 门一部分，本提案是"在 FAIL 之前主动还债"。
