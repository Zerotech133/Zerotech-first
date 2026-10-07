# 全A股每日扫描 → 预警池 → 模拟交易 实施计划

## 一、背景与已确认口径

现有缠论影子评估只覆盖 22 只自选票，各侧面过滤规则（M✓/盘✓/次✓/量✓/势/趋✓）样本积累慢（flt7 现仅 1 条）。用户需求：每日早上 7-8 点自动扫描全股市，达标信号进预警池，再模拟交易。经确认的口径：

1. **仅A股全市场**（5593 只，本地 parquet 就绪）
2. **全类型信号+全侧面标签入池**（1买/1卖/2买/2卖/3买/3卖 × mbei/bei/bei2/vv/reg/tbe；mt 60m 共振全市场无分钟数据恒 None 不判）
3. **模拟交易：检出日收盘入场 + 持有20交易日或反向信号离场**（扫描用截至昨日收盘 bars，检出即昨日收盘 = 可成交价，天然含确认滞后，无未来函数）
4. **不推送**（推送链路保持冻结），仅门户面板展示
5. 硬约束沿用：影子观察、阈值常量不擅改、`_t_*.py` 用完删、原子写、PS5.1 命令行禁中文

## 二、可行性已验证

- 数据：`quant_backtest/data/ashare/*.parquet` 每股一文件共 5593 个，846 根历史（2023-04 起），列 `[ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,volume,amount]`（tushare daily，不复权）；每日 23:35 计划任务 `AI量化RPS共振` 经 `rps_resonance.py` L272-351 增量同步，07:10 扫描时数据已备齐
- 算力：`analyze()` 实测 1.3ms/只 → 全市场单轮 ≈15s；截断重放 78ms/条
- 时段：07:10 空闲（ChanlunSample 06:00、RPS 23:35）

## 三、新文件 1：`quant_backtest/chanlun_scan.py`（核心模块）

常量集中于模块头部：`BACKFILL_DAYS=120`、`MAX_LAG=60`、`HOLD_DAYS=20`、`MIN_BARS=120`、路径常量。

| 函数 | 职责 |
|---|---|
| `_bars_from_parquet(path, asof=None)` | pandas 读 parquet → `[{dt,o,h,l,c,v}]`（trade_date int→"YYYY-MM-DD"，volume→v；volume≤0/停牌剔除；升序；asof 截断；len<MIN_BARS 返回 None 排除新股） |
| `_check_fresh()` | 抽 30 个文件取 max(trade_date)，对照 trade_cal 最近开市日，落后 ≥2 个交易日则报错退出（不静默补数据，数据由 23:35 任务负责） |
| `backfill(days=BACKFILL_DAYS)` | 一次性初始回填：逐 parquet 全历史 analyze → 只留 dt 落在 N 日窗内的信号 → 逐条截短重放（m=1..MAX_LAG，移植 `chanlun_backtest.eval_samples` L199-236 重放循环）找最早检出日 → entry_px=检出日收盘；重放不中弃掉；reg 用 `load_kline('000001.SH','1d',500)`+`regime_at`（失败置 None）；mt=None；键 (code,type,dt) 去重追加池 |
| `scan_all()` | 每日增量：枚举 5593 parquet → analyze → 全收 signals → entry_px=bars[-1].c（昨日收盘）→ 键去重追加池 → 新增行同步开纸面仓位 |
| `update_paper()` | 读账本逐票盯市：持仓满 HOLD_DAYS 个交易日按当日收盘离场(exit_reason=hold20)；期间同票出现反向类型信号提前离场(reverse)；回填的历史仓位（entry_date+20 日已过）首次运行即按既有 bars 一次性结算；逐日 last_px/pnl；closed 截留 5000 条 |
| `pool_eval()` | 按 (信号类型 × 侧面) 聚合纸面收益：全集 n/win/avg + 各侧面 True 子集（mbei/bei/bei2/vv/tbe=True、reg=顺风）n/win/avg，对齐 chanlun_eval_cache 风格，原子写 |
| `main()` | argparse：`--backfill`（backfill→update_paper→pool_eval，一次性手动跑）/ `--scan`（默认：fresh→scan_all→update_paper→pool_eval） |

股票名称：首次运行经 tushare `stock_basic` 拉一次存 `data/ashare_meta.json`（code→name），失败降级只显示代码。

## 四、数据存储（均为原子写）

- `data/chanlun_pool.jsonl` 行 schema：`{market:"CN", code, name, period:"1d", type, dt(信号日), detect_date(检出日), lag, price(理论极值价), entry_px, mbei, bei, bei2, vv, reg, tbe, mt:null, source:"scan"|"backfill", first_seen}`，唯一键 (code,type,dt)
- `data/chanlun_paper.json`：`{updated_at, open:[{code,name,type,sig_dt,entry_date,entry_px,days_held,last_px,pnl,tags{}}], closed:[...+exit_date,exit_px,ret,exit_reason,adj_flag]}`
- `data/chanlun_pool_eval_cache.json`：`{generated_at, hold_days:20, types:{类型:{all:{n,win,avg}, sides:{mbei:{},bei,bei2,vv,reg,tbe}}}, note:"检出日收盘入场·20交易日持有·不复权口径·影子观察"}`

## 五、新文件 2：`quant_backtest/start_chanlun_scan.ps1` + 计划任务

- ps1 照抄 `start_bt_scan.ps1` 母本（ASCII only、C:\aios junction 路径、日志 `C:\Temp\chanlun_scan_task.log`）
- 注册计划任务 `ChanlunScan`：每日 **07:10** 本地时间触发（对齐 ChanlunSample 写法：Administrator/InteractiveToken，Register-ScheduledTask），Action=ps1 → `python chanlun_scan.py --scan`

## 六、门户改动 `portal/portal_api.py`

1. `TASKS` 列表（L27 附近）加 `"ChanlunScan"` → 任务状态自动出现在现有状态接口
2. 导航（L281 附近）加 `("🛟 预警池", "/bt-chanlun-pool", "chanlun-pool")`
3. 新增 `_chanlun_pool_page()`：读 pool 末 100 条 / paper / pool_eval_cache 渲染——①最近入池信号表（代码/名称/类型/信号日/检出日/入场价/六侧面彩色标签，配色沿用现有 M✓紫/盘✓蓝/量✓橙/势蓝青/趋✓粉）②模拟交易各侧面战绩表（每侧面 n/win/avg vs 全集，n<10 灰显+「影子观察」标注）③ChanlunScan 任务状态与口径说明（含不复权、卖点为方向统计非做空损益的声明）
4. 路由分发（L2665 附近）加 `/bt-chanlun-pool`

## 七、验证步骤

1. `python -m py_compile chanlun_scan.py ../portal/portal_api.py`
2. 手动 `python chanlun_scan.py --backfill`（后台跑，计时核对 ≈8-10min）→ 检查 pool 条数、paper 结算数、pool_eval_cache 生成
3. 再跑 `python chanlun_scan.py --scan` → 验证零重复入池
4. 清进程重启门户 → 轮询 `/bt-chanlun-pool` 关键字 + 检查任务状态显示
5. 注册任务 → `Start-ScheduledTask` 试跑 → LastTaskResult=0 → 次日 07:10 后查日志与池增量
6. 清理临时脚本，更新 SKILL.md 与 project_memory

## 八、风险与口径注意（页面如实标注）

- **不复权数据**：持仓跨越除权事件的 closed 行打 `adj_flag` 且不计入 pool_eval（用 pre_close≠前日 close 链检测）
- **新股**：MIN_BARS=120 排除次新噪声
- **卖点**：A股无普遍融券，卖-type 战绩为方向统计（跌为胜），非真实做空损益
- **reg 依赖 zhice-db 在线**：失败置 None 不阻塞扫描
- **jsonl 增长**：全市场日增约 30-100 条，原子全量重写可承受
- 首轮回填后各侧面样本量一次性到位（估每侧面数百条），复评门槛仍是 n≥10 且显著优于全集，经用户确认才谈拦截
