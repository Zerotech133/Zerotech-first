# tools/research/ —— 一次性研究脚本归档

计划 v1.2 §14.6 卫生行动归档目录：存放已完成论证使命的一次性研究/审计脚本。均为只读产线（不写产线数据、不改信号逻辑），产线四件（shadow_weekly_report / gate_shadow_collect / linkage_collector / linkage_collect_daily）不在本目录。归档日：2026-10-14。

> 注意：脚本内部多为 `HERE = dirname(__file__)` 相对定位，归档后在当前目录运行，产物落在 `tools/research/reports/`（原运行产物仍在 `quant_backtest/reports/` 不受影响）。解释器统一用 `"C:/Program Files/Python311/python.exe"`。

| 脚本 | 功能 | 留痕文档 | 复现命令（在 tools/research/ 下） |
|---|---|---|---|
| qd_backtest_1445.py | QD/强度 1445 全湖回测主口径（raw 与 hfq 双口径），产 reports/qd_backtest_1445_result*.json | docs/changes/20261014_knife_dev_changes.md | `"C:/Program Files/Python311/python.exe" -u tools/research/qd_backtest_1445.py`（原命令自 quant_backtest 根） |
| qd_variants.py | qd_backtest_1445 参数变体对照（前 2 年分段 + 后 1 年 OOS） | docs/changes/20261014_knife_dev_changes.md | `"C:/Program Files/Python311/python.exe" -u qd_variants.py` |
| qd_gate_variants.py | breadth 门槛 θ 变体对照（湖内全 CN 股 universe） | docs/changes/20261014_knife_dev_changes.md | `"C:/Program Files/Python311/python.exe" -u qd_gate_variants.py` |
| chanlun_rotation_v7.py | 缠论轮动 v7 权重无关性变体回测，产 chanlun_rotation_v7{TAG}_result.json | quant_backtest/docs/changes/20261014_chanlun_dev_changes.md | `"C:/Program Files/Python311/python.exe" chanlun_rotation_v7.py` |
| chanlun_beichi_grid.py | 缠论三买背驰网格回测（复用 _v7_stream_cache.json 事件流，H∈{5,10,20}），产 reports/chanlun_beichi_grid.json | quant_backtest/docs/changes/20261014_chanlun_dev_changes.md | `"C:/Program Files/Python311/python.exe" chanlun_beichi_grid.py`（日志 C:/Temp/chanlun_beichi.log） |
| cross_grid_backtest.py | 跨市场 W3-4 网格回测（TopN、净向分 Σ dir×w），产线 cross_poll 的口径判例 | docs/changes/20261014_cross_dev_changes.md | `"C:/Program Files/Python311/python.exe" cross_grid_backtest.py [--d0 20240902] [--d1 20260918]` |
| linkage_hitrate_backtest.py | 五信号联动历史命中率回放（episode 去重，判定 judge_matrix） | quant_backtest/docs/changes/20261014_linkage_dev_changes.md | `"C:/Program Files/Python311/python.exe" linkage_hitrate_backtest.py [--start 2024-01-01]` |
| linkage_resonance_audit.py | 联动共振审计（复用 hitrate 的 load_frames/judge_matrix） | quant_backtest/docs/changes/20261014_linkage_dev_changes.md | `"C:/Program Files/Python311/python.exe" linkage_resonance_audit.py` |
| linkage_timing_audit.py | 联动时点审计（复用 linkage_hitrate_backtest.judge_matrix） | quant_backtest/docs/changes/20261014_linkage_dev_changes.md | `"C:/Program Files/Python311/python.exe" linkage_timing_audit.py` |
| nine_dim_industry_pct.py | W5 九维行业分位全市场化改造对照回放（只读产线） | docs/changes/20261014_nine_industry_pct_changes.md | `"C:/Program Files/Python311/python.exe" nine_dim_industry_pct.py` |
| strong_threshold_calib.py | 强度阈值周频校准（只读历史），产 reports/strong_threshold_calib_* | docs/changes/20261014_strong_threshold_calib_changes.md | `"C:/Program Files/Python311/python.exe" strong_threshold_calib.py [--years 1]` |
| qpzm_jev_weight_audit.py | QPZM/JEV 权重审计，产 reports/qpzm_jev_weight_audit_20261014.json + _summary.md | docs/changes/20261014_qpzm_jev_weight_changes.md | `"C:/Program Files/Python311/python.exe" qpzm_jev_weight_audit.py` |

## 引用关系（归档时核查）

- `linkage_resonance_audit.py`、`linkage_timing_audit.py` import `linkage_hitrate_backtest` —— 三者同在本目录，同目录运行不受影响。
- 根目录 `test_qd_backtest_1445.py` 通过 importlib 引用 `qd_backtest_1445`，该测试同为研究产物（未列入本次归档清单），原地保留；若要运行它需先将 `qd_backtest_1445.py` 临时放回根目录或一并归档测试。
- `_v7_stream_cache.json` 留在 quant_backtest 根目录：产线 `shadow_weekly_report.py` 以根目录路径引用（P_STREAM），按"产线零改动"纪律不移动。
