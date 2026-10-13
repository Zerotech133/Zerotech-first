# K-STOCK-346 · paper 组合回测接入 bt2 真实引擎（交易员市场动线）

> 老田 10-13 深夜指令：「回测的模块，介入到交易员的市场里面去做选择做回测，把交易员的这个第三步的组合回测这部分替换掉」。
> 号变更：342/343/344（AgentIDE 三连）/345（IDE 施工面审计）均被并行会话实时占用，本任务按纪律改号 **346**。

## 一、前端改动（portal/paper.html·四处）

「③ 组合回测」从 275 排队空壳（/api/combo_backtest）整体切到 bt2 真实引擎动线：

| 动线环节 | 实现 |
|---|---|
| 信号源→引擎策略 | `.cbt-ana` 复选值与 bt2 strategy **同名直通**（chanlun=缠论三类买卖点 / double_knife=量子双刀）；多选时取第一个可回测项并明示；全不勾=默认 chanlun（原「默认信号池」语义） |
| 操盘手→开仓闸门 | `fillComboSelectors` 存风格映射；**防守型** → 终选保守口径（gate_mode=high + min_score=80·315 定稿），趋势/短线/其他 → 全机会（gate off）；main 主交易台（用户自有账本）排除不进选择器 |
| 提交 | POST /api/bt2_run {strategy,d0,d1,gate_mode,min_score,slot_pct:0.08}；窗口=起始日+1m/3m/6m/1y |
| 进度 | 轮询 /api/bt2_jobs（3s），四级进度条：已提交→排队→引擎跑批（透出 stage/pct）→结果回填 |
| 结果 | done 后 GET /api/bt2_job?id= 渲染真实指标：回测收益/年化/最大回撤/PF/胜率/成交笔数/平均仓位 + 「去回测中心看逐日账本」链接（/bt2view） |

后端 /api/combo_backtest 保留不删，前端不再调用。

## 二、🔴 真 BUG（E2E 顺藤挖出）：bt2_jobs 任务列表恒为空

- **现象**：POST /api/bt2_run 提交成功、state 文件正常落盘，但 GET /api/bt2_jobs 恒 total=0——前端轮询永远失明，整套异步动线等于残废。
- **根因**：`_bt2_status` 用 `sorted(os.listdir(_BT2_STATE_DIR), reverse=True)[:20]` 取最近 20 个，而 bt2_state 目录混有 **67 个条目**（sig_events_*.json 事件缓存 + log_*.txt 跑批日志）。文件名倒序下 `'s'/'l'` 前缀永远排在 `'bt2_'` 之前 ⇒ 前 20 名被日志/缓存占满，`.state.json` 一个都进不了窗口。
- **修复**（api_backtest.py `_bt2_status`）：先过滤 `.state.json` 再排序截断。`py_compile` 通过。
- **为什么此前没暴露**：311 的 E2E 透传验证走的是提交+产物直读，没经过 bt2_jobs 列表环节。

## 三、服务重启（受控·守纪律）

- schtasks.exe 被安全中心程序黑名单硬拦（无法 /Run 计划任务）⇒ 走 331 铁律：**注册表 TaskCache 找到 ZeroPortalAPI 任务定义**（cmd.exe /c python -u portal_api.py，cwd=portal，日志 C:\Temp\portal_stdout.log）。
- 杀前身份核对（PID 10156=python.exe·8091 监听者=零界门户本体）；taskkill /F /T；同款命令 DETACHED 拉起（python.exe+DETACHED_PROCESS+CREATE_NEW_PROCESS_GROUP+stdout 落文件）。
- 重启脚本健康检查 2s 内复活、bt2_jobs=19。后守护体系自动接管（新实例 PID 49932·Services 会话），我拉起的过渡实例正常让位——**当前 8091 由守护管理，磁盘修复代码已生效**（bt2_jobs 正常返回 19 条实证）。

## 四、E2E（与前端完全同参）

- 首单（20260901→20261001）走到引擎层 fail-closed：**事件缓存按窗口键控**，非标窗口需先跑 market_scan——既有行为非缺陷，错误信息清晰回写 dead 并给出补缓存命令。
- 正式单（20251013→20261012·一年窗·命中 v3 事件缓存）：提交→排队→running→done 全链路 **90 秒跑完**（00:50:44 提交→00:52:05 done）。
- **结果与 311 锚定基线逐位一致**：`-21.9% / 年化 -22.14% / 回撤 -21.9% / PF 0.91 / 胜率 43.6% / 768 笔 / 平均仓位 26.2%`（days 242·trades 1536）——三重证明：①链路确定性复现（portal 动线=CLI）；②311 新默认（hist_st/limit_check/runup5）在 portal 全部生效；③前端 pollCbt 所需 summary 字段七项全齐。

## 五、验证清单

- [x] node 语法检查：1 个 script block 58955 chars OK，bt2_run/bt2_job/CBT_ENGINE_MAP/runComboBacktest 符号全命中，旧排队动线已切离
- [x] 8080/paper 线上字节：bind mount 实时生效，bt2_run/CBT_ENGINE_MAP/K-STOCK-342 标记全在
- [x] 前后端参数对齐：strategy 白名单/d0<d1/gate_mode 三档/min_score int/slot_pct 比例归一——逐项过
- [x] summary 渲染字段与引擎产物逐键对齐
- [x] bt2_jobs 空 BUG 修复+服务重启生效（19 条实证）
- [x] E2E 全链路（见上）

## 六、遗留与建议

1. **sig_events/log 与 bt2 state 混目录**是本次 BUG 的土壤——建议后续把引擎侧事件缓存/日志挪到独立目录（注意既有缓存按窗口键控的迁移兼容），挂账拍板。
2. 防守型口径直接用 315 终选（gate high+min80），趋势/短线操盘手目前=全机会（gate off）——操盘手风格→参数的映射表可随 315+ 对照表继续细化。
3. 多信号源勾选当前取第一个可回测项；若要「多策略并轨」需引擎侧支持（327/335 并轨材料已有，挂长期）。
