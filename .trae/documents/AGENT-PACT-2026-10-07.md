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
