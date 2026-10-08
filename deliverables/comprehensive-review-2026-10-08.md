# AI 工作站全面审查报告（2026-10-08）

- 编号：K270（全面审查）
- 范围：E:\老田的AI助手 全项目（代码/图谱/运行时/备份/安全/多会话协同）
- 方法：今日 8 轮审计闭环回顾 + 盲区六维侦察（测试/暴露面/备份/未提交改动/根目录/技术债台账）
- 结论等级：**系统健康度良好，无新增 P0**｜遗留 P1 六项、P2 三项

---

## 一、执行摘要

今日（10-08）单日完成 8 轮审计与整改，全部闭环并有提交佐证：评审 P0 四项收口（T2 分层/ACL 测试/两线合流/定位拍板）、B4 备份加密验收+火绒总根因实锤+三项整改、任务注册表审计（105 任务四级分类）、K268 掉电击杀链根因实锤+对症双调整、电源深钻+孤儿核实+退役清理、保鲜 lag 交易日历感知（P1 清单清零）、K264 跨项目共图 schema 合流（11 边型补注册）。

盲区六维侦察新增发现：**①两个容器全网卡暴露**（ai-portal 8080 无鉴权 + Open WebUI 3000 默认开放注册）；**②今天改动的 5 个核心模块无 pytest 级回归网**（kg_schema/kg_client/kg_source_rating/kg_check/alert_fail 仅靠真跑验证）；**③工作区 18 处未提交改动**归属清晰（K266 线 6 处活跃工作 + gzh 内容线 2 处 + K269 运维文档 2 处），需归属会话自行提交；**④备份链 0647 新版完整健康**，磁盘脚本 17162B 已按 v9 三步一条龙还原。

定位已拍板 C 混合态（个人助手主线、审计线冻结扩批），研发方向建议据此收敛为四条主线 + 一张触发条件驱动的复活清单。

---

## 二、今日八轮审计闭环台账（全部 ✅）

| # | 轮次 | 关键结论 | 提交 |
|---|------|----------|------|
| 1 | 评审 P0 四项 | T2 分层 bug 修（85→84+独立 CROWD 层）；ACL 测试修（13P/1S）；两线合流（KG-M×/P× 分家+台账值@日期）；定位拍板 C 混合态 | 2b8da07/abf9937/e6a6dcb/110bdf4 |
| 2 | B4 备份验收 | 0647 新版通过（加密+镜像+DONE failed=0）；0048/0312 旧版缺陷暴露 | b4-backup-enc-acceptance 报告 |
| 3 | B4 整改三项 | 🔴 火绒隔离 ≥14 次实锤；OPS-1011 两补丁部署注册表；明文残留 6/6 回收站清理 | %TEMP% 备份在案 |
| 4 | 计划任务审计 | 105 非微软任务四级分类：66 正常/22 内联/10 真失败/6 拒触发/2 缺失/3 禁用 | f8342f3 + 报告 |
| 5 | K268 掉电击杀链 | 0x8007042B×5 全部同根因=掉电秒杀（EID=105 同秒 EID=201）；电池策略关闭+alert_fail 掉电标注 | ba88e97 |
| 6 | K268 补遗 | S3 修正+触发源=Button or Lid；掉电 230 次统计（A 类微抖动 47 次，9 月突变）；孤儿 6 候选全误报保留；2 缺失任务 XML 留档后删 | 50964a2 |
| 7 | 保鲜 lag 交易日历感知 | trade_days_lag 落地；coverage_lag 5→0、source_fresh 18→1；shibor_lpr 定性真断供（tushare 断更） | efe7b18 |
| 8 | K264 schema 合流 | 11 边型补注册（10 情报线+INVOLVES 自家缺口）；kg_client stats 41 边型全可见；保鲜按域分治 | 11d45b1 |

另有并行会话落地：全套件 438P/1S/0F（251fe70）、K269 删除者定案（5e88d01）+v9 三步一条龙部署（5a6e53e）、K-N1-002 检查器 v1.4（0a5737c/c6dc3b3）。

---

## 三、盲区六维侦察发现

### 3.1 测试全绿复跑（⚙️ 结果见 §3.7 回填）

- 今天本会话改动 5 模块（alert_fail/kg_source_rating/kg_check/kg_schema/kg_client）+ 并行会话改动 event_graph.py（RULES 误判词修复）/k266_m3_structure.py（+84 行新闻批次 B 案）/portal_api.py（+48 行文档影子 K266-B 适配）。
- **发现（🟡 P1-2）**：quant_backtest 30 个 test_*.py 中**零个**引用上述 5 个 kg 模块——kg_schema/kg_client/kg_source_rating/kg_check 无任何 pytest 级回归保护，今天全部靠「真跑 kg_check drift ok=True / coverage_lag 5→0」方式验证。alert_fail 的 18 个测试在 news_collector/test_alert_fail.py（已复跑 18 OK）。
- 全套件复跑已完成，含 2 项存量债发现与修复，详见 §3.7。

### 3.2 安全暴露面（🟡 P1-1）

| 端口 | 归属 | 绑定 | 鉴权 | 定性 |
|------|------|------|------|------|
| 8080 | ai-portal（nginx:alpine，门户全站） | **0.0.0.0+[::]** | **无**（/audit、/、文档影子、kg 查询全裸奔） | 🟡 全网卡暴露 |
| 3000 | dev-assistant（**Open WebUI v0.5.20**） | **0.0.0.0+[::]** | 应用层登录但**默认开放注册** | 🟡 局域网可自注册用 LLM |
| 8091 | portal_api（FastAPI） | 127.0.0.1 | 无（含 /api/audit_status） | 🟢 仅本地可接受 |
| 7687/7474 | neo4j-kg | 127.0.0.1 | 有（Neo4j 内建） | 🟢 |
| 5432 | zhice-db 主 PG | 127.0.0.1 | 有 | 🟢 |
| 11434 | Ollama | 127.0.0.1 | 无（Ollama 特性） | 🟢 仅本地 |
| 8443/17474/17687/15442/15432/5442 | zerobound/quantdinger/nqs-v2 | 127.0.0.1 | — | 🟢 |
| 8529/8787/8001/8098/6379/6333 | 未监听 | — | — | 🟢 相应服务未运行 |

- 运行中服务验证：8080/audit 200（10710B）、8091/health 200、/api/audit_status 200（M6 接线存活）。
- 9 容器中 7 个正确绑 127.0.0.1，仅 2 个 0.0.0.0——**收敛动作小（改端口映射参数），收益明确**。

### 3.3 备份体系（🟢 健康）

- backups/ 四代同堂：1114（10-07 旧版含明文残留，留 keep-4 自动 prune）→ 0048/0312（旧版缺陷代，已清理）→ **0647 新版全绿**：9 件套（6 archives + 4 enc 加密 dump），quant_db.sql.gz.enc 25,824B 与验收报告字节一致。
- 磁盘 backup_daily.ps1 17162B @10:03 已还原（K269 v9 三步一条龙：磁盘→注册表→git 三写铁律入注释头）；火绒对抗僵局实质解除（K268 定案=Trae agent 线做法触发，非恶意进程）。
- 今晚 03:00 注册表 OPS-1011 补丁首战待验（Test-Gzip 0 字节拒绝+Save-DockerStream 清 dest）。

### 3.4 多会话未提交改动归属（🟡 P1-4）

**8 modified + 10 untracked，归属全部可判，零神秘改动：**

| 归属线 | 文件 | 内容 | 风险 |
|--------|------|------|------|
| K266 活跃 | portal_api.py +48 | 文档影子：新闻按时间置顶/来源过滤/分页/上传支持 txt+md | 低（页面区域，与 M6 不同区） |
| K266 活跃 | k266_m3_structure.py +84 | 新闻批次 B 案（单条逐判+raw_ok 幂等重试） | 低 |
| K266 活跃 | k266_services.ps1 +16 | M3 单实例守卫（防并发重判） | 低 |
| K266 活跃 | news_collector/k266_state.json | last_rowid 20983→21276（摄取器 10:48 仍在跑） | 运行时状态 |
| K266 活跃 | event_graph.py +9 | RULES 删「向下修正/问询函/摘牌」三误判词（宁 None 不错判） | **中（判断逻辑变更，无专属测试）** |
| gzh 内容线 | drafts/index.json + 新草稿×2 | 公众号《AI 算力缺电…》draft+preview | 低 |
| K269 运维 | docs/ops 两份新文档 | v9 部署记录+删除者定案 | 低（应尽快入库） |
| 调试残留 | jev-eval 4 项+quant_backtest/_k260_*_check.py×2 | 测试输出+检查脚本 | 🟢 建议归属会话清理或入库 |
| 其他 | .trae/AGENT-PACT +7 | 协作文档 | 低 |

### 3.5 根目录杂物（🟢 P2-1）

31 条目中非项目资产：`tbl3mCOORkb98xES_*.manifest.json`×3（9-26 同分钟批量生成，某工具临时产物）、`backup_v4_base.txt`（13,857B，备份脚本 v4 基线存档，有保存价值但位置不当）、`video/`、`菜鸟/`、`knowledge_docs/`（定性待归属）。建议 P2 择机归档至 docs/ 或 backups/，不急。

### 3.6 技术债台账与治理面（🟢/🟡）

- 计划任务 105→今删 2=103 个非微软任务，一日 +7 的扩张速度未止——审计线冻结后无治理机制，建议每月一次注册表↔脚本对应巡检（已有 f8342f3 配方可复用）。
- 台账数字口径已改「值@日期+来源指针」，今日再回填 2 处（任务 105→103、边型 41·44）。
- 投委会影子 n=30 转正判据挂账约 6 周后；shibor_lpr 挂账 10-20 复核。

### 3.7 全套件复跑结果（✅ 已复跑+2 项存量债修复）

- 复跑范围：quant_backtest 30 个 test_*.py + news_collector/test_alert_fail.py（394 收集）。
- 首跑：**2 failed, 385 passed, 7 skipped**（185s）；日志 C:\Temp\qbt_suite_1008pm.log。
- **两个失败均定性为存量测试债（非今日回归）**，已修复并复验（**99 passed, 0 failed**，65s）：
  1. `test_qpzm_backtest.py::test_load_wide_eligibility`：fixture 只写 3 列（trade_date/open/close），而 K254 后复权改造后 load_wide 读 5 列（含 high/pre_close）→ read_parquet 缺列抛错被 continue 吞 → 误报 "no parquet data"。**佐证**：10-07 13:32 的 pytest_wb2 tmpdir 残留 parquet 就是 3 列——昨天已坏。修复：fixture 补 high=close、pre_close=close 前值（因子链 ratio=1，chg250 断言数学 295/45-1 不变）。
  2. `test_ui_drilldown.py::test_warehouse_scan_structure`：断言 6 分组，而 9aabecb（10-08 00:57 auto 快照）为 data_health 加了第 7 组「数据完整度（integrity）」。修复：补 mock `_scan_integrity`（空 metrics）+ 断言 6→7 + kind 清单插 integrity。
- **口径澄清**：上午 08:40 的「438 passed 全绿」（pytest_full.log）范围不含这两个文件（439 收集为 ai_operator/portal 侧集合）——两套跑法范围不同，不存在回归。
- 本会话今日改动的 5 模块（alert_fail/kg_source_rating/kg_check/kg_schema/kg_client）在全套件中零回归。

---

## 四、发现清单（严重度编码）

- 🔴 **P0：无新增**。今日全部 P0 已收口（T2 分层/备份明文/火绒对抗/掉电根因）。
- 🟡 **P1-1 暴露面**：8080（门户全站无鉴权）与 3000（Open WebUI 默认开放注册）全网卡绑定，局域网内任何设备可访问门户全部页面并可自注册 LLM 前端。
- 🟡 **P1-2 测试网缺口**：kg_schema/kg_client/kg_source_rating/kg_check 四模块零 pytest 保护；event_graph RULES 变更亦无专属测试。今天的改动验证全部依赖真跑，回归网为空。
- 🟡 **P1-3 供电硬件**：掉电 230 次（9 月起密度上升、微抖动 47 次）是软件层故障总根源，硬件排查是用户物理动作，软件侧已止损（电池策略+告警标注）但根因未除。
- 🟡 **P1-4 未提交改动堆积**：18 处跨 3 条线，K266 线处于活跃半成品状态（event_graph RULES 变更已生效于运行时但未入库）；若掉电/误操作丢失，K266 三文件 +84/+48/+16 行需重做。
- 🟡 **P1-5 shibor_lpr 真断供**：tushare 接口断更（最新 20260820，9-20 LPR 期次缺失），挂账 10-20 复核，再断一期启动换源评估。检查器如实告警是职责，保留。
- 🟡 **P1-6 任务注册表扩张**：103 个非微软任务且持续增长，审计线冻结后无周期治理机制。
- 🟢 **P2-1 根目录杂物**：3 manifest + backup_v4_base.txt + 3 目录待归属。
- 🟢 **P2-2 调试残留**：jev-eval 4 项测试输出 + _k260_*_check.py×2 待入库或清理。
- 🟢 **P2-3 文档收敛**：README 未提及 K266 文档影子页等新面板；gzh 草稿流程未文档化。

---

## 五、整改意见

### P0（立即）——无

今日闭环，无新增。

### P1（短期，本周内）

> **执行状态（10-08 11:4x）**：P1-1 ✅ 已执行（b95617a，两容器 127.0.0.1 绑定，外卡绑定零残留；dev-assistant 实核为 WEBUI_AUTH=false 免登录直用——决策保留鉴权现状挂拍板项，部署记录 docs/ops/2026-10-08_K270P1_暴露面收敛部署记录.md）；P1-2 ✅ 已执行（055081a，14 用例全绿，测试网立竿见影抓出 stats 5 边型漏网≈31 万条边并补齐）；P1-3 用户物理动作 / P1-4 归属会话动作维持原计划。

1. **暴露面收敛（1 小时级，收益最高）**：ai-portal 与 dev-assistant 端口映射改 `127.0.0.1:8080:80` / `127.0.0.1:3000:8080`（docker-compose 或重建容器）；如确有局域网访问需求（手机看盘等），保留 8080 但给 nginx 加 basic auth，Open WebUI 关闭开放注册（环境变量 `ENABLE_SIGNUP=false`）。
2. **测试网补建（2 小时级）**：为 kg_schema（常量清单断言：41 边型可见、INVOLVES 单次）、kg_client.stats（mock driver 返回结构断言）、kg_source_rating.trade_days_lag（4 个已知用例固化）补最小冒烟测试文件 test_kg_schema_smoke.py；event_graph 补 RULES 词表黄金用例（「向下修正」不再中 bear、「问询函」不再中处罚）。**本次审查已顺手修复 2 项存量失败**（qpzm fixture 列契约、ui_drilldown integrity 分组适配，见 §3.7）。
3. **未提交改动处置（归属会话动作）**：K266 线三文件+event_graph 修复建议今日内提交（尤其 event_graph RULES 已影响生产判断）；K269 两份文档直接入库；调试残留自行清理。
4. **用户物理动作（唯一根因解）**：电源硬件排查六项（§power-loss-kill 报告 7.3 表），验收口径=近 7 天 EID=105 微抖动归零。

### P2（择机）

1. 根目录杂物归档（3 manifest 可回收站，backup_v4_base.txt 挪 docs/ops/archive）。
2. 任务注册表月度巡检机制化（复用 f8342f3 配方）。
3. README 收敛 + 新面板补记。

---

## 六、研发方向建议（定位 C 混合态）

基准：`quant_backtest/docs/positioning-decision.md`（110bdf4 拍板）——**个人助手为主线，审计线冻结扩批，平台化由三触发条件拉动**（涪陵 OPC 付费/北理工演示/ARM 客户要求）。

### 主线一：超级助理纵深（核心投入）

Hermes Agent（执行/记忆，8787）+ OpenClaw（网关/插件）+ CowAgent（三层中文记忆）+ Khoj（知识检索）的整合架构已规划。建议下一步：
1. Hermes WebUI 补完（当前 8787 未监听=服务未启动，先恢复驻留并纳入任务注册表治理）。
2. 记忆层打通优先：CowAgent 三层记忆 → Hermes 执行记忆的读写接口先行，知识检索（Khoj/Qdrant）次之。
3. 每个整合点都走「影子运行→人审→入生产」纪律（K266 文档影子已示范此模式）。

### 主线二：量化系统低维护运行（守护态）

watch 保鲜+告警+备份已自动化闭环；RiskGuard 四规则重校准（误杀 +0.65% vs -0.25%）是唯一挂账的分析类工作，建议排入主线间歇期。K266 收尾后冻结扩批。

### 主线三：图谱资产维护（降速线）

K264 剩余=KGStockAnalysis 侧 10 边型补注册+金标签日常化（挂对方排期）；本侧 41 边型全注册已达稳态，kg_check 日常巡检即可。

### 主线四：内容与传播（轻量线）

gzh 量化观察栏已开（今日首篇草稿）；统一口号待定稿。涪陵 OPC 材料（6% 税率口径/公安合规/产业落地表述）按政府节奏推进，与北理工实验室材料并行。

### 一张纸结论

> **不做**：新平台功能、第八批审计、K265/K266 扩展。
> **做**：暴露面收敛+测试网补建（本周）、Hermes 记忆层打通（本月）、电源硬件排查（用户）、RiskGuard 重校准（间歇期）。
> **触发即启**：涪陵 OPC 付费需求 → 平台化预研重启。

---

## 七、附录：证据指针

- 全套件日志：C:\Temp\qbt_suite_1008pm.log（本审查复跑）
- 今日报告链：power-loss-kill-2026-10-08.md / b4-backup-enc-acceptance-2026-10-08.md / sched-task-audit-2026-10-08.md / docs/ops K268 定案+K269 v9 记录
- 台账：INTEL_RD_PLAN.md（KG-M 分家+值@日期）、docs/overall-progress.md、docs/positioning-decision.md
- 侦察原始数据：git status/diff 全景、docker ps/inspect、netstat、backups/ 目录清单（正文引用）
