# 第七轮：catalog v23（宏表 freq）+ unknown 65→52 + 看门狗 + SentimentBT 材料 · 回执（2026-11-02 11:30）

> 车道：dwagent-impl / wa-verify / wa-map + Lead（v22/v23 rebuild）

## 一、完成项

| 项 | 结果 | 证据 |
|---|---|---|
| **catalog v22/v23** | ✅ A4 宏表 freq D→M/Q（cpi/m/ppi/pmi=M、gdp=Q） | v22 因修正块误放 `__main__` 后未生效 → 已移至 main 前并 rebuild **v23**；catalog 实测 freq=正确；metadata PASS |
| **5 张宏表写回** | ✅ ts_cn_pmi 升 evidence_ok；cpi/m/ppi/gdp 保留 partial（真实滞后 lag63/125，非口径问题） | apply validation=True |
| **unknown 65 张收口** | ✅ unknown **65→52（-13）**、evidence_ok **134→147（+13）** | 4 批 dry-run→仅 13 张 ok apply（validation 全 true）；范围审计 exactly 13 条 |
| **TdxSync 看门狗脚本** | ✅ `tools/datawarehouse/tdx_sync_probe.py` + 10 测试 | 检查四任务 LastResult/lock 残留/part mtime+manifest rows 增量；L2 申请单已出 |
| **SentimentBT 恢复 L2 材料** | ✅ 25KB 五要素申请单 | 阶段 A 零外发（jev_impact.py 无 push）；首跑不带 --push；数据已就绪（news.db 11:02 11:05） |

## 二、资质分布（K8）

| 分级 | 起点 | 现在 |
|---|---|---|
| unknown | 223 | **52** |
| evidence_ok | 0 | **147** |
| evidence_partial | 25 | 47 |
| deprecated | 1 | 3 |
| planned | 7 | 7 |

## 三、未升级 52 张归因

- **41 张真实 stale**（xtcloud 为主，与 10-13 停更一致）→ 等今晚 TdxSync 四批补数；
- `stale_gt_40d` 1（shibor_lpr 低频阈值评估）；`future_date` 2（cb_basic/ts_trade_cal 日历类→建议 calendar 口径例外）；
- `no_latest_date` 2（firm-level-climate-change-exposure/ts_sge_basic）；`no_date_column` 1（shibor）；
- `no_parquet` 4（guba_posts/ts_eco_cal/ts_fund_company/ts_stock_company→登记修正/生命周期）；`store_missing` 1（v2_bar_daily pg 需只读 DSN）。

## 四、已知问题/边界

1. **v22 教训**：build_catalog 修正块必须位于 `__main__` 之前才生效 —— 已修并 v23 验证（catalog freq 实测正确）；
2. **看门狗回执**尚未由车道产出（脚本+测试+L2 申请单已交付，10 测试通过），不影响交付；
3. **今晚首夜验收**：TdxSync 四批（17:35/17:45/17:55/18:05）+ GlobalFutSync 18:15，按数据级清单核对；
4. **11-05 06:00 窗口**：8091 重启（DWH-105+F3/F4）+ nginx 接线（提醒已登记）。

## 五、待批 L2（累积）

- SentimentBT 恢复（阶段 A 零外发，07:20）；
- TdxSync 看门狗（每小时，异常走 alert_fail_wrap）；
- 11-05：8091 重启 + nginx 接线（既有提醒）。

## 六、下一步

1. 今晚 TdxSync/GlobalFutSync 首夜数据级验收；
2. unknown 52 → <40：DATA1 补 41 张 stale + 补 date_col（shibor/cnopendata2/ts_sge_basic）+ calendar 例外 + 4 空目录登记修正 + pg 只读探针；
3. 11-05 窗口 8091/nginx；
4. 明日 06:40 dw_snapshot 首日自动跑检查。