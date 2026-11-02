# 学习因子实验室（W2-5）可视化交付报告（2026-11-03）

## 数据链路
- 源报告：`research/reports/learning_factors_20261103.json`（W2 因子库 learning_factors v1.0.0，8 因子，窗口 20260803~20261102，60 日 × 50 只）。
- 门户数据：`portal/static/learning_factors.json`（schema `learning_factors_dashboard.v1`）由源报告聚合生成：因子名/title/公式/数据依赖/PIT/方向/覆盖率（n_valid/n_expected）/统计量/样例值/状态、data_gaps、unimplemented、summary。
- 页面：`portal/learning_projects.html` 新增独立面板「学习因子实验室（W2）」+ 独立 `<script>` 块（仅读取 `/static/learning_factors.json`，**未改动任何既有区块/渲染逻辑**）。

## 渲染说明
- 汇总行：因子数 8 / 已实现 8 / PIT 安全 7 / 平均覆盖率 **81.97%** / 区间 / 生成时间；
- 因子表：标题+name、公式、覆盖率（含 n_valid/n_expected）、PIT、方向、最新样例（ts_code=value）、状态（可用≥80%/部分>0/数据缺口=0，颜色 ok/bad/muted）；
- 缺口区：`data_gaps` 7 项（前 8 条，单条截断 120 字）+ 未实现因子（0）。

## 验证
- HTTP：`/learning_projects.html` 200（含「学习因子实验室（W2）」）；`/static/learning_factors.json` 200（factors=8）；
- 测试：`tools/tests/test_learning_dashboard.py` 新增 2 项（因子 JSON 契约、页面区块+HTTP）；
- 门禁：unify_nav --check / portal_route_inventory --check / check_syntax --fail-on-warning 全绿；
- 数据缺口备注：`cyq_chips` 依赖因子覆盖率 0%（筹码数据窗口内 0 只/0 日），已在 data_gaps 标注，属数据仓补采项而非代码缺陷。
