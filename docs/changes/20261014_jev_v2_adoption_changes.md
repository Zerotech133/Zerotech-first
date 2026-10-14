# W5c · jev 毕业判据 v2 正式采纳落地（变更留痕）

- 日期：2026-10-14
- 执行：jev-v2-dev
- 性质：**判据口径采纳落地（老田 2026-10-14 批准按建议方案执行）+ 新增两个只读文件 + 周报注记式集成 + 留痕报告**。未改 jev_eval.py / signal_review_gate.py / jev_graduation_bydomain.py 任何一行，未动毕业相关历史产物，未推飞书，未注册计划任务，产线 DB 零写入。

## 一、v2 判据正式文案（自本日起为正式口径）

每市场域**独立判定**，两条同时满足才算该域达标：

- **n_eff ≥ 30**：有效标的数（唯一 ts_code 计数，非名义条数）；
- **均值 > +1pp**：FOCUS 组 T+5 前向均值，**标的聚类口径**（先按标的求均值再等权）；
- **辅助指标（无硬门槛）**：同域 FOCUS−PASS > 0——对市场 regime 的分母效应免疫性更好，不设硬门槛。

**域级特殊规则**：

- **CN**：不设 FOCUS 判据，状态单列"**入口缺位**"（quality 从未判 low，FOCUS 恒 0）；修 quality 在 CN 的触发是前置课题；
- **CRYPTO**：**过渡期剔除**出毕业判定（处置 A），单独监控曲线；quality 模型对 crypto 有区分度（非恒 low）后**自动并入**（处置 C，目标态）。

**总灯色（周报简化规则）**：任一 live 域达标 → 绿；无达标但有 FOCUS−PASS>0 的域 → 黄；全负 → 红。

- 判据来源：docs/changes/20261014_jev_oos_grad_changes.md 第四节（v2 建议）+ CRYPTO 三选一处置（A+C 组合）；
- 采纳决定：**老田 2026-10-14 批准按建议方案执行**（C 为目标态 + A 为过渡态；不建议 B）。

## 二、代码落点（全部新增/注记式，零破坏）

| 文件 | 性质 | 说明 |
|---|---|---|
| jev-eval/jev_graduation_status.py | **新增**，只读 | v2 全域状态输出。数据源与 jev_graduation_bydomain.py **完全一致**（直接 import 其 load_all/domain_stats：triples.jsonl + replay 合并 2070 条，T+5 前向，回填窗口 2026-08-13~10-08）。判定核心抽成纯函数 `judge_domain(n_eff, cluster_mean, gap, domain)`（jev_graduation_status.py:52）+ `overall_light(domains)`（:75）。CLI：默认人读摘要；`--json` 打印状态 JSON；`--md` 打印可直接嵌周报五线表的行。每次运行覆写 review_data/graduation_status_v2.json |
| jev-eval/test_jev_graduation_v2.py | **新增**，纯合成数据 | 15 个用例：US 双过/单过、HK n 不足、CRYPTO 过渡剔除（指标再好也 False）、CN 缺位、边界（n_eff=30 恰达标、均值恰 +1.0pp 不达标须严格大于）、辅助 gap 无硬门槛、总灯色 green/yellow/red。运行：`"C:/Program Files/Python311/python.exe" test_jev_graduation_v2.py`（当前 15/15 OK） |
| jev-eval/review_data/graduation_status_v2.json | **新增**，产物 | v2 状态快照，周报消费方 |
| quant_backtest/shadow_weekly_report.py | **注记式修改** | jev 线接入 v2（见第三节）；改前备份 `.bak_20261014_v2` |

## 三、周报集成方式（shadow_weekly_report.py）

- **改前备份**：`quant_backtest/shadow_weekly_report.py.bak_20261014_v2`（与原文件逐字节一致）；
- **取数顺序**（`jev_v2_status()`，shadow_weekly_report.py:322）：优先读 `review_data/graduation_status_v2.json`（校验 `adopted=="v2"` 且有 domains）；文件缺失/口径不符 → 子进程调 `jev_graduation_status.py --json` 刷新一次（timeout 120s）；
- **失败回退**：取数全程 try/except 注记式——任何异常（文件缺、脚本崩、超时、口径不符）在 `data.grad_v2_error` 留错误串、notes 标注"v2 状态获取失败…本次回退 v1 口径"，然后走原 v1 判定逻辑（池化 n≥300 + 均值 +1pp），周报不炸；
- **展示**：五线表 jev 行在"累计"列追加 v2 各域摘要（US/HK n_eff+聚类均值+✓✗、CRYPTO"过渡监控 Xpp"、CN"入口缺位"）；明细区 grad_v2 块含每域 {n_eff, cluster_mean_pp, focus_pass_gap_pp, pass, state}；CRYPTO 显示"过渡期监控"曲线值（聚类均值）；灯色按第一节简化规则；v2 分支同样保留覆盖率 <50% 红线检查；
- **触发条件节**（第五节）文案同步改为 v2 口径，v1 保留为对照口径标注；
- 周报自动化链路（每周五 18:00 调用方）不需要任何改动——入口不变，仅 line_jev 内部换口径。

## 四、首跑状态核对（真实数据，2026-10-14）

运行 `"C:/Program Files/Python311/python.exe" jev_graduation_status.py`，与 20261014_jev_oos_grad_changes.md 第四节"v2 下当前状态"逐项核对：

| 域 | n_eff | 聚类均值 | FOCUS−PASS | 判定 | 文档预期 | 一致 |
|---|---|---|---|---|---|---|
| US | 44 | -0.86pp | +1.55pp | 均值未过线（n_eff ✓ 均值 ✗） | n_eff 44 ✓，聚类均值 -0.86pp ✗ | ✓ |
| HK | 14 | -0.86pp | +2.40pp | n与均值均未达标 | n_eff 14 ✗（FOCUS−PASS +2.40pp 最强） | ✓ |
| CRYPTO | 6 | -2.44pp | -1.70pp | 过渡期剔除（监控值） | 过渡期剔除，单独监控 | ✓ |
| CN | 0 | — | — | 入口缺位 | 入口缺位 | ✓ |

- 样本：2070 条合并、FOCUS 名义 336、窗口 2026-08-13 ~ 2026-10-08（T+5 回填截止）——与 bydomain 拆分产物完全一致；
- 总灯色：**黄**（无域达标，但 US/HK FOCUS−PASS 为正）；
- 周报实跑验证：五线表 jev 行正确展示 v2 各域摘要、灯色黄、notes 归因清晰；回退路径实测（模拟状态文件与脚本均不可用）→ 正确标注"v2 状态获取失败…回退 v1 口径"并给出 v1 判定（n=336 达标、均值 -1.04pp 未过线）。

**结论：v2 口径下仍未毕业，归因清晰**——US 离线最近（均值未转正 +1.55pp 相对优势），HK 样本积累中（+2.40pp 最强），不再是 CRYPTO 结构性拖累混淆全池的糊涂账。

## 五、回退方法

- **周报回退 v1**：任一即可——
  1. 还原备份：`cp shadow_weekly_report.py.bak_20261014_v2 shadow_weekly_report.py`（周报完全回到 v1 行为）；
  2. 软回退：删除/改名 `jev-eval/review_data/graduation_status_v2.json` 且使脚本不可用（如改名 jev_graduation_status.py）→ 周报自动回退 v1 并在 notes 标注（已实测）；
- **状态脚本/测试**：jev_graduation_status.py、test_jev_graduation_v2.py、graduation_status_v2.json 均为新增文件，可独立删除，不影响任何现有链路；
- jev_eval.py / signal_review_gate.py / jev_graduation_bydomain.py / 历史毕业产物：零改动，无需回退；
- 判据口径本身若需撤回 v2：周报还原来备份即恢复 v1 展示；graduation_status_v2.json 中 `adopted` 字段标 "v2"，消费方校验该字段，口径变更时会自动触发回退而非误读。

## 六、产物清单

| 文件 | 性质 |
|---|---|
| jev-eval/jev_graduation_status.py | 新增，只读，v2 全域状态输出（judge_domain/overall_light 纯函数 + --json/--md CLI） |
| jev-eval/test_jev_graduation_v2.py | 新增，单元测试 15 用例（合成数据） |
| jev-eval/review_data/graduation_status_v2.json | 新增，v2 状态快照（首跑 2026-10-14） |
| quant_backtest/shadow_weekly_report.py | 注记式修改，jev 线接 v2 + 回退 v1 |
| quant_backtest/shadow_weekly_report.py.bak_20261014_v2 | 改前完整备份 |
| quant_backtest/reports/shadow_weekly_report.{md,json} | 周报产物（本轮实跑刷新，jev 线已是 v2 口径） |
