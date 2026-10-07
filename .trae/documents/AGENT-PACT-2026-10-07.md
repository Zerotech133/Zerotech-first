# AGENT-PACT 摘要（给 Trae-CN 会话）— 双 Agent 边界协定 v1.0

> 正式全文：`E:\KGStockAnalysis\docs\planning\dual-agent-pact-2026-10-07.md`（修改请去全文，此处 WorkBuddy 会话会同步）
> 发起背景：老田指示「两个 ID 的工作不要重叠抢端口、知识图谱改动别打架」。2026-10-07 生效。

## 一句话

两边共用这台机器的 Neo4j / PostgreSQL / Ollama / Tushare 表，**写入排队、读随意**；文件目录各管各的；新增服务先登记端口。

## 五条硬规则

1. **Neo4j 大批量写入互斥**：K260 回填（你们，进行中）期间，WorkBuddy 侧六座桥图谱写入不动笔；反之亦然。动笔前在协定 §四 登记窗口。WorkBuddy 桥写入用独立标签前缀（`CNPool_*`/`LJ_*`），不碰 MarketEvent/HotMoney/Seat 等 12 类既有边型。
2. **Ollama 算力：交互优先，批量让路**：/kg/ask、预警推送优先；大批量校准/回测推理排收盘后/深夜单飞行。
3. **ts_* 表**：你们只读，KG-TushareSync（00:10）/TdxSync（17:40）写入（WorkBuddy）。T11 摘 dryrun 转正式前会等 K260 回填完成。
4. **停写窗口联席**：任何一方开维护窗口（如 R2/R3 口令轮换），提前在协定 §四 登记 + **双方各自停自己的看门狗**（C7 期间 ZhiceWatchdog 打破过窗口）。
5. **目录所有权**：`portal/`、`quant_backtest/`、`jev-eval/`、`news_collector/`、`tools/`、`.trae/` 归 Trae-CN；`E:\KGStockAnalysis\` 归 WorkBuddy；`C:\aios` 为 junction（→ `E:\老田的AI助手`，即 Trae-CN 工作区），其下目录权属**按子目录分别登记**（见 §目录所有权补充）；`ollama_service/`+`llamacpp_35b/` 文件归 WorkBuddy、服务双方共用。越界只读。

## 端口登记（10-07 实测，新增先登记）

8091 portal 唯一实例(Trae)（**8080 是 docker 容器端口，非 portal**）｜ 8060 local_service(WB，当前未在听) ｜ 9101-9103 jev 指标(Trae) ｜ 11434 Ollama(共享) ｜ 8030 35b(WB 按需) ｜ 8001/8098(WB) ｜ 5432/6379/6333/7474/7687(共享)

## 目录所有权补充（v1.0 修订）

`C:\aios` 是指向 `E:\老田的AI助手` 的 **junction 链接**（SYSTEM 计划任务经此纯 ASCII 通道，规避中文路径秒退）——**同一物理目录，非副本、非字节同步**：`C:\aios\portal` ≡ `E:\老田的AI助手\portal`（同一份文件，归 Trae-CN 管理，WorkBuddy 不碰）；`C:\aios\quant_backtest` ≡ `E:\老田的AI助手\quant_backtest`（**目录域归 Trae-CN**，其中 **PG-Robot 产线**（`p_g_robot.py` 及相关）归 WorkBuddy）。**改任一路径即改同一份文件**。

## N-Agent 扩展（v1.1，老田指示：未来多 Agent/多 IDE 工具）

协定升级为**注册制**：新 Agent/IDE 开工前在全文 §四 登记（名字/管辖区/标签与表前缀/端口/资源权限）。已预留：**AI 选股 Agent**（图谱前缀 `AS_`、表 `as_train_`/`as_bt_`、端口 8061-8069）。批量算力（训练/回测/蒸馏）一律窗口制；产品化走「服务化→影子期→灰度」三步。详见全文 §六 与整合方案 §九。

**Agent 接入套件已发布**（10-07 10:08）：`E:\KGStockAnalysis\agents\AGENT-KIT.md`——新 Agent 即插即用五面标准（接口/记忆库/图库/凭据/算力）。首个注册 Agent：**临界探子**（lingjie_scout，别名探针；开源社区巡视→拉代码→判优劣→整合建言；前缀 `SC_`/`sc_`/`scout_*`，文件契约 inbox/outbox 不占端口，图库默认只读，独立凭据 SC_GITHUB_TOKEN）。章程：`KGStockAnalysis\docs\planning\agent-scout-charter-2026-10-07.md`。

## 命名口径（老田 10-07 钦定，两侧统一）

项目总名**临界**；图谱=**元子**；数据仓=**数据城池**；数据联动=**数据血缘**。命名宪章：`E:\KGStockAnalysis\docs\planning\lingjie-naming-charter-2026-10-07.md`。新子项目取名照宪章两族办理（微观物理×文明设施），登记追加。

## 事件流水（append-only，见全文 §四）

- 10-07 09:50 WorkBuddy：协定建立。K260 回填期间本侧不动图谱写入。
- 10-07 09:55 WorkBuddy：首轮冲突实扫完成——portal 经复核系**同一物理目录**（`C:\aios` 为指向 `E:\老田的AI助手` 的 junction，非「双份部署」，10-07 Trae-CN 更正）；8080 端口表纠错；本侧 8060 未在听已记录。
- **冲突处理约定（老田指示）**：任一会话发现两侧行为冲突 → 立即向老田报告冲突点 + 最优解决方案，不擅自裁决。
- 凭据备忘：R1 飞书 APP_SECRET 已于 10-07 09:32 轮换（config.json+HKLM 双写，jev 三组件已验证存活）；R2/R3 轮换将开联席窗口。
- 10-07 17:25 Trae-CN（老田现场授权·方案甲）：**PG-Robot 产线变更**——①`p_g_robot.py` 双分叉合并为单一权威版（KGStockAnalysis 分支：环境变量凭据 fail-closed；产线分支：TAT 初始化 10×60s 代理自愈），py_compile 通过、双处 SHA256 一致，备份 `quant_backtest\p_g_robot.py.bak-20261007`，PG-Robot 重启验证 TAT init ok + consume ready；②根因：`BOT_OPEN_ID` 常量 `ou_6bbb99…` 随 app 换绑失效（现 app `cli_aa3e4f1b58f85cdd`，群成员 API 实测 bot `ou_012790…`），已修正上线；③**遗留**：该 app 未订阅 `im.message.receive_v1`（event status SUB=- / RECEIVED=0，正确 @ + 全新总线实测仍零事件）→ 待老田开发者后台开启「长连接 + 接收消息事件」后端到端即通；④治理注记：越界（WorkBuddy 辖区 PG-Robot 产线）经老田现场授权执行。
- 10-07 18:35 Trae-CN：**更正 17:25 认知 + PG-Robot 端到端全通**——①认知更正：`BOT_OPEN_ID` 并未失效，open_id 为 **app 维度**，`ou_6bbb99…` 始终是 app `cli_aa3e4f1b58f85cdd` 视角下 PG-Robot 的真实 open_id（以本 app 身份发 @、服务端 mention 回绑实证）；17:25 改用的 `ou_012790…` 系以他 app 凭据查群成员所得（跨命名空间误用），已回改双份并重启；②真根因（app 未订阅事件）已闭环：老田后台开通「长连接 + `im.message.receive_v1` + `im:message`」并发布版本后事件推送恢复（RECEIVED 0→1）；③连带修复：`nl_query.py` MODEL `qwen3:8b-8k` 已不在本机 Ollama（/api/chat 404）→ 改 `qwen3.5:9b`；④端到端全通：`600337`（42.3s）、`300516`（26.7s，群内他人）均 SEND_OK，产线恢复；⑤教训：跨 app 查询所得 open_id 不可跨用。
- 10-07 19:05 Trae-CN（老田现场授权）：**nl_query.py A/B 全量合并 + 三项 env 化**——①合并为单一权威版（原差异仅 6 行：A 侧 `kg_paths` 数据指针 vs B 侧 `HERE/data` 缓存路径，融合为 try/except ImportError 回落，两侧行为实测不变：A→`E:\KGDataRoot\nlquery`、B→`quant_backtest\data\nlquery`），备份 `nl_query.py.bak-20261007`，双处 SHA256 一致；②env 化：`PG_BOT_OPEN_ID` / `PG_ROBOT_CHAT_IDS`（CLI 优先）/ `OLLAMA_URL`，`p_g_robot.py` 与 `nl_query.py` 均 A/B 同步；③产线重启验证 ready + websocket connected。
- 10-07 20:05 Trae-CN（老田授权「图谱作战 Phase A 开工」）：**A1 数据燃料落地**——①`cainiao_history_ingest.py` 扩展：桥 2c 四表收全（新抽指令 23,702 + 实盘委托 6,822 入 `KGStockAnalysis/data/cainiao_inbox/`）；②收益回填层 `cainiao_calib_backfill.py`（T1b）：`calib_sample.db.calib_returns` 表 11,005 条 (signal,判定,real_ret) 三元组（as-of 双基准 r1/r5/r20/r60/r5e/r20e/mdd20），全量零缺失、独立对账 MATCH，供 JEV/RiskGuard/锦标赛/九维 V4 四处消费，只出数不判态。
- 10-07 21:35 Trae-CN（老田授权「继续 A3」）：**A3 JEV 运维四项全落地**（jev-eval 系本会话辖区）——①回填真凶：报告称 7% 实为 **0%**（`run_backfill` SQL `%d`/`%s` 混用 TypeError 被静默吞），修复后一次回填 **429 条**，影子报告首验 **FOCUS-PASS 收益差 +1.95%**（n=194/235）；新增覆盖率守卫（<50% 飞书告警）+ 任务 `JevReviewBackfill` 每日 20:30；②日志根治：`log()` 双写 `JEV_LOG_DIR` + `zhice.ps1` 重定向固定 `logs/`；③任务 `JevEvalMonthly`（30 天）+ 基线漂移守卫，当晚新基线 93.3% 无漂移；④SQL 7 处参数化 + 6 阈值 env 化；⑤判例：PS1 含中文必须 UTF-8 BOM（丢 BOM → PS 5.1 GBK 炸裂已踩已修）；`JEVBackfill-Hourly` 系 news_collector 线同名异物；QD(15000) 未启动桥自愈（列入巡检）。（※时钟注记：本行 21:35 为存档笔误，实测 ≈20:55）
- 10-07 21:06 Trae-CN（老田授权「执行 Phase B」）：**Phase B 全五项收官**（时间戳 Get-Date 实测）——①B1 九维：已由另一会话完成（`nine_dim.py`/任务 06:40/门户 `/bt-9dim`），零重造；休市池停 09-30，10-08 复市自恢复；②B2 锦标赛：2-agent 已就位，SEEN 元组幂等键已修，bias 8 条 legacy_cons（新 agent 零卡系休市非故障）；③B3 面谈材料 `riskguard-jev-calib-brief-2026-10-07.md`（WorkBuddy 辖区经授权写入）+ 飞书送达——R2 已毕业(vol_mult=2.5)、R1 口径失效待三选一、反例真判定仅 31 条；④B4 战绩层（唯一开发项，本会话辖区）：`kg_portal.py` `battle_records()` 四源聚合（缠论池/锦标赛/JEV/RiskGuard，单位统一 pct、逐源容错、sides n≥10 滤除）+ `records` kind；`portal_api.py` `/kg/records/` 转发 + 作战页战绩面板；CLI 四层全绿（FOCUS−PASS +1.95 与 A3 一致；R1/R2 各 2989、R3 2 吻合）；门户 8091 热重启（22232→45388），端点/页面均 200；⑤待老田六项面谈拍板：R1 三选一/R3 扩展/反例预标/PENDING 27 条/JEV 毕业门槛/QD 拉起。
- 10-07 22:11 Trae-CN（老田逐项拍板「执行 B3 六项面谈拍板」）：**六项拍板全落地**（结果存 brief §五；WorkBuddy 辖区经授权写入）——①**R1 挂起观察**：`kg_riskguard.py` `SUSPENDED_RULES`+evaluate 过滤（不评估不入账，calib 重放保留，可逆）；②**R3 扩展首跑**（`kg_riskguard_calib.py --r3`，asof 累计 base 对齐引擎全历史语义）：min_base=10 全网格休眠（全库仅 1 标的 ≥10）；降阈 3 档 n_block=11 diff −4.47pp t=−2.59 粗显著，密度闸零区分——降阈 3 / bear_min_n 5 留下期带数据拍板；③**反例预标**：525 条 qwen3.5:9b 批量预标进行中（断点续跑、失败不落盘、槽被占探针让路——实测他方 4-shard 批任务队列竞争，PACT §2.3），10% 抽检清单自动生成供人检，预标≠转正；④**JEV 毕业三条件落码**（`signal_review_gate.py` 报告实时判读）：≥+1% 持续 2 周 + FOCUS n≥300 + REJECT n≥30（不放宽触发率）——当前 ①✓+1.95% ②194/300 ③0/30；⑤PENDING 27 条老田自处；⑥QD 不拉起。
- 10-07 23:49 Trae-CN（老田逐项拍板研究三项）：**研究拍板落地 + R4 首跑 + 预标收官**（存 brief §八）——①毕业条件③改「高危组负收益验证」（news_p≥0.5 CN n≥10 且均值<CN-PASS；REJECT 阈值 0.70 不动）——根因：触发率 0.11% 攒 30 条需 464 交易日，语义猎极端尾部 98.8%<0.4，放宽也无效；②条件①双口径判读以同市场域（剔 CN）为主——**辛普森悖论**：+1.95pp 混合口径主因 PASS 含 61 条 CN 大跌票而 FOCUS CN=0，同市场域仅 +0.67pp；run_report 落码分市场表+新③口径（实跑通过），10-08 回补重验；③R4 重放同夜完成（ingest 两表 19,416 行 + `--r4` 1,573 账户日/58 账户）：**倒 U 型**——0.25 档集中反赚 +2.95pp（t=+3.31）、现行 0.30 误杀（miskill +25.5pp）、≥0.40 才显著危险 -8.81pp（t=-3.81）——上调 0.35~0.40 档候选与 R3 两参数同留下期面谈；④预标 525/525 零失败（bear 478/bull 47），53 条抽检清单待老田 10-08 人检。
- 10-08 00:12 Trae-CN（老田指令「跑数据的同时核对一下@飞书机器人，300419是错误的，仔细检查」）：**PG-Robot 300419 错答修复**——根因两层：①data_lookup 传 codes='300419.SH'（深市码错配沪后缀）+ 非法 fields + 2023 区间 → n=0 误断「查无此股」；②双重编造股票名（纳思达/泰永长征均错，权威名称=ST浩丰）。修复三层：agent_tools.py 新增 stock_info 身份卡 + _fix_codes 后缀纠错（3→SZ/6→SH，000001.SH 指数保护）+ pool_rows 加 code；multi_agent.py ROUTER 纯代码路由三 specialist 全派 + 禁编名 + 全市场新闻公司名≠所查代码；nl_query.py SYSTEM/HINTS 同款。回归：旧错误调用现 n=3 自动纠 .SZ；两轮 LLM 实跑最终答案全对（ST浩丰/0930 收 7.96/池内 3买×2/事件无）；错误双缓存已清，p_g_robot 热重启 67416→59216（事件监听 ready）。
- 10-08 00:35 Trae-CN（老田拍板「先处理那两项人工动作」）：**反例预标转正 + PENDING 27 条定性**——①抽检转正：辅助核验全量忠实度机械 525/525（bear↔破位卖出/bull↔满足买入 100% 一致、反向污染 0，唯一 flag 301479 复核亦忠实）+ 抽样 53 条 ts_daily 交叉（25 收盘确认/13 边缘/15 盘中破位尾盘收回，弱反例记录在案，不构成否决）；老田确认同意率 100% → **prelabeled 转正为可用反例池**（bear 478/bull 47，bear 侧定位），清单 53 条回填✓+尾注；②PENDING 27 条：老田拍板 acc37 系统性差异 14 条整批标假，acc33×5/acc40×3/acc41×2 共 10 条标真，纯转账/冻结 3 条标假——合计 10 真/17 假，标注单落 brief §六供菜鸟后台照单点选。
- 10-08 01:08 Trae-CN（回归测试「纳思达最新行情」暴露新洞，老田指令链「好的，执行吧」续修）：**PG-Robot 名称→代码查询工具层根治**——①问题：8b 模型名称→码靠记忆不可靠（曾断言「纳思达正确代码 002183」=怡亚通，实为 002180 现名奔图科技）；池内反查/验证式候选码/「必须立即再调」/ROUTER 附候选码+SYNTH 禁断言四轮提示词方案均无效（8b 权重极限）→ 转工具层根治；②根治：新增 `fetch_stock_names.py` 生成 `data/stock_names.json`——现用名=datamart stock_basic（5911，本地零 API）∪ tushare namechange 曾用名（14231 行全量裸调；tushare 包 pro_api 同 token 报「token不对」系包侧坑）→ name2code 11682 条（撞车 53 跳过）；agent_tools.py `_stock_names()` 进程缓存（json 缺失回落 datamart）+ stock_info 反查升级全市场含曾用名（name_note「曾用名 X，现名 Y」）+ 未知名拒答；quant_judge 补名称场景规则，data_lookup/nl_query 删过时「候选码再调」指令；③验证：直测 6/6（纳思达→002180 奔图科技/浩丰科技→300419 ST浩丰/600000/艾派克/未知名拒答）；LLM 全链路「纳思达最新行情怎么样」完美（002180.SZ 奔图科技+0930 收 16.14 跌 0.74%+池内 3卖 04-30，无错误码断言）；py_compile×4；p_g_robot 重启 68728→16440；④教训：提示词例子会被抄进答案（示例用占位符）；8b 记忆类事实必须工具层兜底，名称↔代码映射彻底脱离模型记忆。
- 10-08 01:15 Trae-CN（老田「好的，执行吧」→ 发起下期面谈三拍板，全选推荐即拍即落）：**R4/R3 三定值落地 params.yaml**——①R4 max_pos_weight **0.30→0.35**（§八倒 U 型：0.30 误杀区 miskill +25.5pp、0.40 危险 -8.81pp 但 n=33 偏小不采）；②R3 min_event_base **10→3**（10 档全网格休眠 n=0 全库仅 1 标的 ≥10；3 档 n=11 diff -4.47pp t=-2.59）；③R3 bear_min_n **2→5**（熊市稀疏少出手）——三键入列 calibrated_keys，calib_ver → "2026-10-08-user-approved"；enforce 仍恒降级影子（GUARD_KEYS 余键未校准，宁严勿松铁律不破），影子出数即刻用新定值；kg_riskguard_calib.py 同步（R3_MIN_BASE 改 params 动态读、保留历史档 10 对照）；验证全 PASS（kg_params/load_thresholds/calib/py_compile）。存 brief §九。
- 10-08 02:05 Trae-CN（R1 复议前置达成 → 老田批准「重设计 P10 分位闸」）：**R1 重设计落地**——①证据重放（223 有效采样日）：原候选「衰竭占比 P90」被数据否决（占比最高 0.0061 无法分档）；对称口径 health0≤P10 t=-3.51；**防前视滚动分位 LB125/P10 t=-5.33 强显著**（block 46 日 -3.56% vs pass 52 日 +1.05%、miskill -26.09pp；P05~P20 分位全稳健非过拟合）；②发现 health0 时变漂移（数据起步期 0.0089~0.0428 vs 丰富期 0.28~0.53）→ 旧固定阈结构性失效、滚动分位自适应；③落地三件：params.yaml 新键（lb=125/q=0.10 入 calibrated_keys 现 8 键）+ kg_riskguard.py（rule_r1 重写市场级 block 日、SUSPENDED_RULES=() 复活、health0.jsonl 序列底座回填 223 日）+ calib --r1b 重放子命令；④验证全过（py_compile/旧测试/eval 直测 shadow_block 出分位判定 health0=0.2043≤P10阈0.2731，R3 min_base=3 同步生效）。存 brief §十。
- 10-08 02:25 Trae-CN（R1 重设计收尾汇报后老田「好的，执行吧」→ 发起 enforce 余键校准，三拍板全选推荐即拍即落）：**R5 三守卫键落地 GUARD_KEYS 9/9**——①min_market_samples 维持 500（网格实证恰挡唯一污染日 20251021 n=377/h=0.0，t -5.33→-5.53；1000 档误伤正常日、1500 档崩 t=0.98）；②min_vol_days 维持 20（rolling(20,min_periods=20) 结构自洽，守卫永不误触发，404 万行零反例）；③bear_density 维持 0.5 保守档（0.3/0.5/0.8 零区分实证，密度 max<0.3/日，bear_min_n 单闸主导）——calibrated_keys 8→11 键，**enforce_allowed()=True（三重门条件②达成）**；enforce 仍未启用（余显式 --enforce + 样本外对账 |t|≥3 + 老田 enforce 批准，默认恒 shadow 无自动路径）；enforce 注释同步、验证全过。存 brief §十一。
