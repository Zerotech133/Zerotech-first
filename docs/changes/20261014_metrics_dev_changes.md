# 20261014 metrics-dev 变更：P1 判据度量批（影子期上线判据自动化度量）

执行人：metrics-dev（滚动泳道）｜日期：2026-10-14｜产线：shadow_weekly_report.py / linkage_collect_daily.py / gate_shadow_collect.py / rps_resonance.py

## 一、任务背景与目标

计划 v1.2 §14.3 判据缺口：影子期各"上线判据"无自动化度量，W8 评审存在无数可依风险。本批给六项判据逐条配周报度量 + 两个 P1 小件，**零行为改动（注记式挂载除外）**，全部只读产线产物。

## 二、改动清单（4 文件 + 1 新增单测，均有备份）

| 文件 | 改动 | 备份 |
|---|---|---|
| shadow_weekly_report.py | 注记式扩展六项判据度量（§六/五节）+ `criteria` JSON 键 | `.bak_20261014_m` |
| linkage_collect_daily.py | 观察池第三路：cross 每日 TopN（小件7） | `.bak_20261014_m` |
| gate_shadow_collect.py | read_qpzm 读并透传 upfrac（小件8） | `.bak_20261014_m` |
| rps_resonance.py | market_env 返回值落 upfrac 字段（纯增量，不影响打分） | `.bak_20261014_m` |
| test_shadow_metrics_m.py | 新增单测 9 条（合成数据） | — |

## 三、逐判据度量实现方式

### 判据1：chanlun k33 对账≥30
- **实现**：`crit_k33_recon()` 读 `data/nine_dim_history.jsonl`（k33 警报池影子库，k33_shadow_check.py 同源），统计 `pool_type=="alert"` 行数与首条入池日期；≥30 判"已达标"。
- **字段漂移检查**：逐行检查 `{dt,code,pool_type}` 必需键 + 扫描 zg/zd 字段。实查结果：history 文件**不存在**（空池）；备查 `data/chanlun_samples.jsonl` 508 行**均无 zg/zd 字段**且最新日期 2026-09-16——**漂移不适用**，未来若出现 zg/zd 行会按行数标注提示。
- **首跑**：`n=0，state=待首采`（警报池空，低频属预期，勿调 ALERT_TH）。

### 判据2：linkage 命中率≥55%
- **实现**：`crit_linkage_hitrate()` → 纯函数 `calc_linkage_hitrate()`。数据源 `data/linkage_shadow.jsonl`（ref_date+code+signal+block.code），前向收益用**湖内日线**回填：个股 `data/ashare/*.parquet`（trade_date/close），板块 `data/tdx/tdx_sector_daily`（经 linkage.block_bars）。交易日历 = `reports/qd_breadth_series.json` dates（20230103 起 912 日）。
- **口径**（linkage_hitrate_backtest 同判例）：T+k=信号日收盘→T+k 收盘；A 绝对（>0 命中）/ B 跑赢板块（超额>0）双口径；episode 去重（同 code+signal 在 k 交易日内连续触发只计首日）；maturity 要求目标日在日历内且个股 bar 已更新过目标日。
- **首跑**：25 episodes，T+5 成熟 0 / pending 25（首采 2026-10-13，前向窗口未走完）→ `样本不足`，属真实口径，后续周报滚动出数。

### 判据3：strong coverage≥0.9
- **实现**：`crit_strong_coverage()` 读 `reports/strong_latest.json` 的 `member_coverage` 与 `full_scan` 字段，周报记录快照值形成趋势。
- **首跑**：member_coverage=**0.316**（<0.9，未达标）；full_scan 字段**当前快照未落**（旧版产出），已标注"strong_scan 产出含该字段后开始记录"。

### 判据4：ninedim 零漏报 20 日
- **实现**：`crit_ninedim_timer()` 读 `data/nine_dim_history.jsonl` 首条 alert 日期，有即报"计时中 day X/20"（X=日历交易日数，含首日）。
- **首跑**：`未启动`（警报池空，计时未开始——与判据1同源事实，如实呈现）。

### 判据5：qpzm 错杀 30 日运行
- **实现**：`crit_qpzm_gate_counts()` 读 `data/gate_shadow_3tier.jsonl`，按策略×档位（qd/qpzm/cross × θ40/45/50）计累计行数与 ok 行数 + 日期跨度。
- **首跑**：累计 **9 行（ok 9 行）**，3 策略 × 3 档各 1 行，日期跨 20261012~20261013；W8 评审以 ok 行为准。

### 判据6：G3 60 日计时
- **实现**：`crit_g3_progress()` 统一进度表：起算日锚（`G3_ANCHORS` 常量，注记式可改；jev/linkage/三档锚有数据推导兜底值）→ `trading_days_between()` 交易日计数 → X/60 进度。日历同判据2。
- **首跑**：双刀注记 0、jev 影子 9、k33 4、linkage 0、三档对照 0（交易日）。
- **锚日说明**：双刀注记 20261014=knife-dev 批上线日；jev 20260922=triples 首条 ts；k33 20261002=定稿日；linkage/三档 20261014=各自档案首行。均为注记值，改 `G3_ANCHORS` 即可，不影响其他逻辑。

## 四、P1 小件

### 小件7：cross TopN 入 linkage 观察池
- cross 扫描**无落盘产物**（cross_poll.build_cross 结果仅进程内缓存），故 `pool_cross_topn()` 运行时进程内调 `cross_poll.build_cross()` 取 `topn`（k≥3 共振、缺省 10 条），过滤 `*.SH/*.SZ/*.BJ`；**已注明来源**于脚本头注。
- fail-open：取源失败打印 `CROSS_POOL skip` 返回空，不阻塞持仓/watchlist 两路。
- 首跑 dry-run：cross 路产出 10 码，当前与 watchlist 高度重叠（cross 的 qpzm 源即 rps_latest，本就是 watchlist 驱动）→ 池仍 25 只。属数据事实，非逻辑缺陷。

### 小件8：upfrac 落 market_env 快照
- 根因：`rps_resonance.market_env()` 内部算 upfrac 参与打分但返回字典未带出 → `rps_latest.json` envs 无该字段 → gate_shadow_collect 恒 null。
- 修复：market_env 返回值纯增量落 `upfrac`（正常路径 round4；数据缺失路径显式 None 保持形态一致），不影响打分与闸门判定；`gate_shadow_collect.read_qpzm` 透传（缺失/坏值→None fail-open），`_mk_rows` 落档。
- 生效时点：**下次 rps_resonance 日批跑完** rps_latest.json 才带 upfrac；之前的影子行 upfrac 保持 null（如实留痕）。

## 五、验证

1. `shadow_weekly_report.py --json-only` 首跑成功，六项判据全部真实出数（缺数据的线如实标"待首采/样本不足/未启动"），报告落 `reports/shadow_weekly_report.{md,data.json}`，MD 新增"五、上线判据度量"节（原触发评审节顺延为六）。
2. 单测 `test_shadow_metrics_m.py` **9/9 通过**，覆盖 4 条核心路径：
   - 命中率计算：成熟命中/未命中、episode 去重、未到期 pending、口径B超额不命中；
   - 起算日推算：trading_days_between 边界（同日/倒序/非交易日锚）、ninedim 首条取 alert 最早；
   - upfrac 补齐：market_env 落字段（0.5 合成场景）+ 数据缺失路径 None + read_qpzm 透传/旧快照/坏值三态；
   - 缺失 fail-open：四判据缺文件全不抛、坏 jsonl 行跳过、单判据炸只标 error 不炸周报。
3. 回归：`test_gate_shadow_collect.py` 10/10 通过；四文件 py_compile 通过；`linkage_collect_daily.py --dry-run` 正常出池。

## 六、回退方法

```bash
cd E:/老田的AI助手/quant_backtest
cp shadow_weekly_report.py.bak_20261014_m shadow_weekly_report.py
cp linkage_collect_daily.py.bak_20261014_m linkage_collect_daily.py
cp gate_shadow_collect.py.bak_20261014_m gate_shadow_collect.py
cp rps_resonance.py.bak_20261014_m rps_resonance.py
# 可选：删除新增单测与新周报产物（reports/shadow_weekly_report.* 会由下次运行自然覆盖）
```

回退后周报恢复五线原版（无判据度量节）；gate_shadow_3tier.jsonl 已落的 9 行历史保留（其中 qpzm 行 upfrac=null 属历史事实）。

## 七、遗留与移交

1. upfrac 需待下次 qpzm 日批自然落盘；建议 W8 前抽查一行 qpzm 影子行确认非 null。
2. linkage 命中率预计 T+5 后（约 2026-10-20 起的周报）开始滚动出数。
3. G3_ANCHORS 起算日为注记值，如计划侧有官方起算日以计划为准改常量即可。
4. cross TopN 目前无落盘产物（进程内取）——若后续 cross 线出每日落盘文件，建议切换读文件并在此留痕更新。
5. 未注册任何计划任务、未推飞书、未动 positions.json 与生产数据（纪律遵守）。
