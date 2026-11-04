# 03 · 领域线通读（量化 / 情报KG / 学习内容 / 军团与D1 / 数据仓）

> 通读人：doc-domains（四人组之一）｜时点：2026-11-04 08:00 Asia/Shanghai｜性质：只读研究，未改仓库、未 git 写、未启停服务。
> 路径约定：**R**=C:\项目\老田的AI助手，**W**=C:\项目，**K**=C:\项目\zerobound-kg。标注 **[实测]**=本次命令输出；**[文档称]**=仅文档口径、本次未复测。
> 主线口径：战略唯一源 ROADMAP v4（R/docs/plans/2026-11-02_ROADMAP_v4_唯一战略与主排期.md:1-8）；执行层最新为 11-04 研发计划 v2（R/docs/plans/2026-11-04_研发计划_v2.md:4）。

## A. 五条线总表

| 线 | 一句话现状 | 成熟度(★1–5) | 关键数字（实测/文档称） | 现行计划（最新判例） | 对外产品? |
|---|---|---|---|---|---|
| **量化** | 研究资产厚、执行闭环薄：0 条可实盘 alpha，D1/盯盘均 paper-only | ★★（2） 数据/回测/策略四层资产齐，但「策略→选股→交易」未上线 | [实测] INTEL_RD_PLAN 6,201 行、mtime 10-27；[文档称] 13 卡/5 注册策略/0 approved/10 manifest/24 战法(6 tested 全 rejected)/36 假设（R/docs/audit/2026-11-01_策略库真值盘点_v1.md:12,18-23） | 盯盘完整系统 4 篇 MVP（11-04，只 commit 不 merge）；统一回测口径 v1；从策略到选股（pending_approval） | ❌ 内部工具/研究样板间（研报问答/晨报可作内容出口） |
| **情报KG** | 「上半身强、中段断」：采集/JEV 生产在役，复核→证据→消费未闭环 | ★★（2） news/JEV 双 12 万级真实在役，但复核静态、实体率 9.1% | [实测] news 120,364 行、JEV 120,255 行、hit 空 79.2%、xq_status 33,396、TDX 三表 380,365/214,167/149,776 仍停 11-01；[文档称] 队列 10,525 停 10-30、evidence-targets 3,237、news 带股 9.4%、军情司 8 席 1 履职（R/docs/audit/2026-11-03_情报板块开发盘点与现状研究_v1.md:17-24,126） | 10-31 情报板块研发计划仍 proposed；11-03 情报体系军团作战化 draft 待批；11-03 盘点为最新判例 | ❌ 内部工具（无 composition/Q2/对外契约） |
| **学习内容** | 五线中最接近可对外：流水线标准+门禁+期刊 001 成品 | ★★★（3） S0–S8 标准/GATE/发布器齐，期刊自检全绿；缺分发与受众验证 | [实测] learning.db 4 项目/13 源/241 卡/2,310 单元；期刊 001=502,054B、18 项自检全 ok；gzh 草稿 67 篇；[文档称] 单元 3,813（元数据 73%）、卡 256、候选池 418（R/docs/plans/2026-11-03_知识能力与治理研发计划_v1.md:10-16） | 学习数据后流程统一标准 v2（唯一权威）；知识能力与治理研发计划 v1（11-03→11-16） | ✅ 最接近对外（期刊/卡片/公众号内容），仍缺发布-分发-反馈闭环 |
| **军团与D1** | 编制齐、战力弱：77 席 5 角色在册，7 组合全 draft、Q2 近零 | ★★（2） 组织/接口/会议/Case 通，Q2 与签字未成立 | [实测] org 17 部门/77 席；five_roles 5；compositions 7 全 lifecycle=draft、kernel 0.9.0 draft；[文档称] Q2=1 PASS+1 条件+4 Q1、owner 签字 0/4；ACAD1 冻结 reconciled 0/13、blockers 20、四套卡口径 13/25/30/12（R/docs/plans/2026-11-04_研发计划_v2.md:111-119） | 11-04 研发计划 v2 §七/§八；D1 Q2 判例「未成立」（2 已证/2 部分/1 未证）；8 硬节点 11-04 HZ 中期、11-08 ACAD1 | ❌ 内部工具/组织底座（Case、会议、指挥部为内部产品） |
| **数据仓** | 内部最像产品：catalog/registry/质量/血缘成链，但暴露面与 SLO 未达标 | ★★★（3） 256 表在册、DWH-101~105 交付、ADR-019 落地 | [实测] catalog v39/256 表/14 别名（updated 11-04 06:24）；[文档称] 314 数据集 registered 265(84.4%) / exposed 72(22.9%)；KPI 9/10；K8 unknown 14；query clean 99.03%；KPI 五口径并存（R/docs/adr/2026-11-02_DWH-103_K5数据契约_pg_table_temp_v0.md:100；R/docs/plans/2026-11-03_数据仓研发计划_v4_历史数据前移版.md:4） | 数据仓研发计划 v4 历史数据前移版（11-05 上线窗口）；ADR-019；11-04 接口映射 | ❌ 内部数据平面（对智能体是产品，对外不可用） |

**成熟度依据**：★5=有对外 SLA/客户/回款；★4=完整对外产品（发布+分发+反馈）；★3=内部可用且有验收证据；★2=资产/组织齐但闭环与 Q2 未成立；★1=仅文档/停摆。排序：学习内容≈数据仓（3）> 量化≈情报≈军团（2）。

## B. 五条线精读

### B1 量化（★2｜内部工具/研究样板间）

- **现状**：四层资产真实——数据仓（314 数据集）、知识图谱（1,475 万节点/420.7 万边，R/quant_backtest/INTEL_RD_PLAN.md:35）、策略库、回测。但「可实盘 alpha」为零：5 条注册策略 0 approved，S01 超额 +0.17%≈0、S02 -9.6%/MDD -9.5%、S04 未建、S05 双方向 FAIL（R/docs/audit/2026-11-01_策略库真值盘点_v1.md:12,19-20）。
- **关键数字**：[文档称] 门户 13 卡（9 非工具+4 工具）、核心扫描 6、注册策略 5、manifest 10、量学 backlog 24（tested 6 全 rejected）、假设台账 36、paper 12 账户/21 持仓/828 委托（同文:18-23,43）；[实测] `INTEL_RD_PLAN.md`=6,201 行、最后修改 2026-10-27 15:11，正文最新条目停在 10-18（XQ-INTEL 两线，:6168-6199）→ 10-19 后 K 系列无总账。
- **现行计划/判例**：①盯盘完整系统 MVP（11-04 四篇：引擎/API/接线/告警；引擎只做「读 armed plan→判 quote→调既有 trigger_plan」，人工 gate，绝不 decide/execute，状态=代码+测试已提交只 commit 不 merge，R/docs/plans/2026-11-04_盯盘完整系统_引擎_v1.md:6,24-29）；②统一回测口径 v1 已交付（T+1 建仓/涨跌停阈值/成本/IC 口径成文，R/docs/plans/2026-11-01_统一回测口径_v1.md:2,27-48），但「后续接因子库/影子盘」未闭环；③从策略到选股 v1 仍 pending_approval（MVP 5,571→2,585→20 只，R/docs/plans/2026-11-01_从策略到选股能力_v1.md:2,62-67）；④H37/H39 假设卡 draft。
- **最大缺口**：① 策略→选股→paper→盯盘链路未打通（paper 与选股未自动对接，真值盘点:43）；② 无 approved 策略/无股票级上线记录，「选股 page/API」未批（从策略到选股:34,81-84）；③ 开发日志断档 8 天（INTEL_RD_PLAN 停 10-18/10-27），K 系列与 docs/plans 两本账。
- **对外产品判断**：❌ 内部工具。理由：所有信号被「未过影子盘+人工批准→不得对外发布荐股」红线约束（从策略到选股:88-92）；可对外的是研究内容（研报问答/晨报），不是量化能力。
- **接口**：← 数据仓（dataset/pg refs：quant-domain 4 条、v2_bar_daily 未登记 soft warning，R/docs/plans/2026-11-04_数据仓接口映射_v1.md:28）；← 情报（INTEL_TO_D1 flag 默认关）；→ 个人 OS 首页/日报/简报；→ D1（handoff→watch_plan→盯盘引擎）；研报/结论→商业 OS（ROADMAP v4:195-197）。

### B2 情报与知识图谱（★2｜内部工具）

- **现状**：采集/JEV 生产在役（news 9 源 15 分钟轮询、JEV 4 常驻服务、雪球/股吧/TDX/OSINT），门户工作台/总线/查询在；但复核→证据→消费链路断：复核队列 10,525 条全部 10-30 一次性建队、hit 后验 78.6% 未回填、calib 无任务、news 带股率 9.4%、INTEL_TO_N1/D1 flag 默认关（R/docs/audit/2026-11-03_情报板块开发盘点与现状研究_v1.md:13,19-24,126-129,148）。
- **关键数字**：[实测 11-04] news 120,364 行、news.stocks 严格非空 11,012（**9.1%**）、JEV dec_audit 120,255 行、hit 空 95,235（**79.2%**）、xq_status 33,396、TDX 三表 380,365/214,167/149,776 max_pub=2026-11-01（仍断更）；[文档称 11-03 07:3x] news 115,430、JEV 115,220、队列 10,525、evidence-targets 3,237、intel_collector 58 文件/84 测试/5 源、军情司 8 席 1 履职（同文:17-24,126）。
- **现行计划/判例**：①10-31 情报板块研发计划 v1 仍 **proposed**（同文:153）；②11-03 情报体系军团作战化研发计划 v1 为 **draft 待 A0**（R/docs/plans/2026-11-03_情报体系军团作战化研发计划_v1.md:3），内容=军情司 8 席→5 单元、Q2 11-16≥3/6；③11-03 盘点为最新事实判例，明确「不是没做，是上半身做完中段没接上」（同文:218）；④JEV 实证判例：n=2,308，corr(情绪×T+1)=**−0.006≈0**，conf≥0.9 占 75%、高置信命中 53.6%（R/docs/2026-10-17_JEV舆情判定实证报告_K-ORG-156-157.md:4,36-38）→ JEV 只作模型标注，不当信号（11-03 整体方案 v2 同口径）。
- **最大缺口**：① 复核运营未启动（队列静态、calib 无任务、hit 未回填）；② 证据—标的实体链弱（9.1%，目标 ≥50%）与三页 404/半私有鉴权（`/bt-jev`、`/api/intel-graph` 匿名 200）；③ 无编成无账本（不在 6 军团/Q2、无 intel composition/A2A 卡）；KG 侧 zerobound-kg 自评 READY-WITH-CONDITIONS、只做本机自用（K/README.md:43-45）。
- **对外产品判断**：❌ 内部工具。理由：情报中枢半私有半匿名、无对外契约（IntelEvent/A2A 未落码，同文:176-177）；价值在内部决策链，不能对外售卖。
- **接口**：→ 量化（INTEL_TO_D1/N1，flag off）；→ KG（news/xueqiu/guba/tushare/tdx/crypto 六源消费，同文:65）；→ 商业 OS（信号→五角色→Directive 闭环 A，R/docs/plans/2026-11-03_整体研发方案_v2_最优执行版.md:178-192）；← 数据仓（news.db/jev_audit 登记）。

### B3 学习与内容（★3｜最接近对外产品）

- **现状**：学习数据后流程收敛为唯一标准：STD（标准 v2）+SCHEMA（5 契约）+PIPE（learning_ops）+GATE（learning_gate），S0–S8 九段、G7 发布门 7 条硬清单、发布强制走 learning_release.py（R/docs/standards/2026-11-02_学习数据后流程统一标准_v2.md:7-14,21-30,46-55,71-87）。内容侧已出**期刊 001**成品：单文件 HTML 502,054B + issue.md 249,874B + manifest，18 项自检全部 ok=true（离线/锚点/输入只读/产物一致），输入 sha256 全登记（W/outputs/learning_issues/20261104/manifest.json:8-14,24-35,57,64,145,174,180；W/work/learning_publish/selftest.json）。
- **关键数字**：[实测] learning.db=4 项目/13 源/241 卡/2,310 单元（portal 卡库 400 张）；期刊 001：知识 315 条、结论 100 条（库 730）、有效单元 1,439/2,310=62.3%、空 claim 155；gzh 草稿 67 篇（最新 11-03）、video/output 仅 2 项且停在 2026-09-25。[文档称] 知识单元 3,813（元数据 2,795=73%、有效 1,018）、学习卡 256（视频号 84）、候选池 418；视频号流水线每日 06:30、首跑候选 772→预审通过 259/驳回 513（33.5%）（R/docs/plans/2026-11-03_知识能力与治理研发计划_v1.md:10-16；R/deliverables/2026-11-03_视频号卡片常态化_回执.md:42-51）。
- **现行计划/判例**：①学习数据后流程统一标准 v2 为唯一权威，**合并并取代** 10-31 知识全链路标准 v1 等 3 份（v2:3-5）；②知识能力与治理研发计划 v1（W0 元数据行过滤/重复 cohort/多源交叉验证；KPI 元数据≤5%、重复≤2%、多源≥30、卡≥400、8 因子）（同文:36-47,77-86）；③11-04 期刊 001 = 内容产品化的最新判例（自检 18/18、可复现、无计划任务/无服务，定期化需 L2）。
- **最大缺口**：① 发布—分发—反馈断：期刊无定期化任务（manifest:71 明示需 L2）、gzh/video 无统一台账；② 口径漂移：学习卡 400（门户）/256（计划）/241（learning.db）三口径、单元 3,813/2,310/1,439 三口径；③ 治理未达标：元数据行 73%、多源概念 0、去重「0」只覆盖视频号 800 单元（R/docs/plans/2026-11-03_量化投资知识库垂域模型微调训练方案_v1.md:28）。
- **对外产品判断**：✅ 最接近对外（期刊/学习卡/公众号文章本身可对外发布；单文件离线、可溯源、可复现），但仍是「内容成品」而非「产品能力」：无订阅/分发/效果回收。
- **接口**：→ 量化（结论库 730/Top100→策略假设四关，知识治理计划:47,66）；← 数据仓（yt_learning.db 13 表已登记待纳 catalog，R/docs/plans/2026-11-02_数据智能部组织架构与岗位说明书_v1.md:90）；→ 商业 OS（内容日历/IP）；gzh/video 为内容出口。

### B4 军团与组织（含 D1/ACAD1/黄忠/司马徽）（★2｜内部工具/组织底座）

- **现状**：编制齐（[实测] org.json 17 部门/77 席、五角色 5 席：田丰/诸葛亮/赵云/关羽/黄忠；17 席日报真实 17/17），指挥面通（五角色 `/api/chat` 5/5 岗位 persona 对齐，R/docs/audit/2026-11-04_五角色能力与接口对齐_v1.md:4,32-42）；但战力未证：7 个 composition 全部 lifecycle=draft、kernel 0.9.0 draft（[实测] registry.json）；Q2=1 PASS+1 条件+4 Q1、owner 签字 0/4、Hermes profiles 5/69 席、MCP 13（11 ok/2 failed）（R/docs/plans/2026-11-03_整体研发方案_v2_最优执行版.md:63-70）。
- **关键数字**：[文档称] 77 席（去重 69）/66 HR/52 team/130 技能/6 军团 38 席/95 页面/2,049 单；Directive 12（11 open）、LegionReport 9 组 green2/amber5/red2、unmapped 84.3%；agent_census 在册 69/干活中 0/静默 73（78%）（同文:63-70）；ACAD1 11-08 冻结 **NO_GO**：13 卡清点 13（HK9+US4）、reconciled 0/13、blockers 20、四套口径 13/25/30/12（R/docs/plans/2026-11-04_研发计划_v2.md:111-119）；[实测] compositions 7（比文档「6 军团」多 dev-legion 已入册）全 draft。
- **现行计划/判例**：①11-04 研发计划 v2 §七/§八：ACAD1 冻结闸门 C1–C7、HZ 11-10 最终验收（11-04 只中期复核 4/6、GoNoGo NO_GO）；②D1 Q2 判例：lusu-d1-flow v1.1.0 仍 draft，Q2 **未成立**（Q1/引用/健康已证，SLO 未证、回滚演练 pending、测试部分），明确不写 active（R/docs/plans/2026-11-04_D1_Q2验收证据_v1.md:8,14-21,66-69）；③IFS-COMP 判例：D1 7 API/3 页面/4 任务/7 ref 入 composition，validate errors=0 warnings=2，Q2 仍缺 health/SLO/回滚/契约测试四类（R/docs/plans/2026-11-03_IFS-COMP_D1组合对接登记_v1.md:12,14）；④五角色判例：接口全通但能力工具化不足（赵云/关羽/黄忠无独立 API）（五角色:66-71）。
- **最大缺口**：① Q2 与 owner 签字（7 组合 0 active、ACAD1 四口径冲突+0/13 复核）；② 席位≠战力（5/69 profile、干活中 0、静默 78%）；③ 验收证据链（HZ ③ 复核 28/28 但 ④ 满窗仅 2/153；SLO 无窗口实测、回滚演练 pending）。
- **对外产品判断**：❌ 内部工具/组织底座；Case/会议/`/hq` 指挥部是内部产品，无对外交付面（军团接口契约 v1 只是内部声明层）。
- **接口**：→ 量化（D1 handoff→watch_plan→盯盘引擎，盯盘引擎:14-16）；→ 数据仓（D1 7 data refs planned，接口映射:25-28）；→ 情报（WS1 情报侦察单元）；← Hermes 控制面（`interface/hermes-control`，13 MCP/98 tools）；→ 商业 OS（五角色→Directive→军团→LegionReport）。

### B5 数据仓（★3｜内部数据平面，最像产品）

- **现状**：catalog/registry/血缘/质量/快照工具成链（DWH-101~105 全部有证据），K5 契约 ADR-019（A0 已批）补齐 pg_table/Temp 白名单；11-04 新增数据仓接口映射，把 ACAD1/D1 的 `dataset/pg/*` 引用从「v2_* 名称软过」改为「registry 登记命中」为证据（R/docs/plans/2026-11-04_数据仓接口映射_v1.md:13-15）。
- **关键数字**：[实测] catalog version=39、tables=256、aliases=14、updated 2026-11-04T06:24；[文档称] 314 数据集/registered 265(84.4%)/exposed 72(22.9%)、四域 241/19/29/25、四象限 52/213/20/29（R/docs/adr/2026-11-02_DWH-103_K5数据契约_pg_table_temp_v0.md:100）；KPI 9/10、K8 unknown 14、物理表 256、display 258、查询 clean 99.03%、Hermes 98 tools/11 active、前端 7 视图（数据仓 v4:4）；tushare curated daily 1991→2026-11-02 共 18.39M 行（v4:28）；DWH 暴露面缺口=登记 84.4%/暴露 22.9%，2 条 soft warning（v2_zhice_tables/v2_bar_daily）（接口映射:15）。
- **现行计划/判例**：①数据仓研发计划 v4 历史数据前移版（11-03 终验→11-04 P0→11-05 上线窗口→11-06~16 P1→11-17~30 P2；原则「能回放的历史任务前移，依赖实时状态等真实窗口」，v4:3-5,13-16）；②数据仓板块复盘 v1（11-02）虽 STATUS=pending_approval 但正文自称「生效/唯一执行口径」（R/docs/plans/2026-11-02_数据仓板块复盘与整合计划_v1.md:3,5），已被 v3/v4 执行版实质超越；③ADR-019 判例：软校验吞缺口必须改为登记命中（ADR-019:15）。
- **最大缺口**：① 暴露面/资质：exposed 22.9%、资质 ~13%（unknown 223，复盘:14-16）；② 口径未一：314/265/72 vs catalog 256 vs display 258 vs 复盘 display 92；③ SLO/运营化未测（月报/ODCS/RBAC 排 11-17 后；dw 写回仍 staging+L2）。
- **对外产品判断**：❌ 内部数据平面。理由：方向明确「业务可自助、智能体可执行、质量可度量」，但只读、RBAC/审计/ODCS 均未落地（v4:101-103）；对外无 SLA/计费/隔离。
- **接口**：→ 量化（daily/复权/筹码/涨跌停/资金/财务/研报/舆情/指数；分钟K线/Level2 缺）；→ 学习（yt_learning.db、learning.db 登记）；→ Hermes/MCP（`dw_query`、98 tools）；→ 军团（组合 data refs）；← 采集线（news/xueqiu/guba/tdx）。

## C. 跨线接口与依赖（现行）

| 接口 | 方向 | 现状 | 证据 |
|---|---|---|---|
| 数据仓→量化 | dataset/pg/* refs | 12 条引用，10 命中 registry，2 条 soft warning；errors=0 | R/docs/plans/2026-11-04_数据仓接口映射_v1.md:15,25-29 |
| 数据仓→智能体 | MCP `dw_query`/`/api/dw/*` | 98 tools/11 active；`/api/dw` 经 8734 需登录（401→L2 已闭） | 数据仓 v4:4；DWH-105 证据 |
| 情报→量化 | INTEL_TO_D1 / INTEL_TO_N1 | 代码+测试+A/B 就绪，生产 flag 默认 off；reviewed 仅 100 | R/docs/audit/2026-11-03_情报板块开发盘点与现状研究_v1.md:148 |
| 情报→KG | 六源入图 | 每日 06:20 增量链；11-03 卡 notice_llm、news 水位落后 3,884 条 | 同上:157,196 |
| 学习→量化 | 结论库/因子→四关 | 结论 730 条/Top100；8 因子待实现；策略假设移交单待下 | 知识治理计划:47,66；微调方案:28 |
| 军团(D1)→量化 | handoff→armed watch_plan→trigger | 链路代码在；引擎 MVP 只 commit 不 merge；human gate | 盯盘引擎:14-29 |
| 量化→个人 OS | 首页/日报/简报/研报问答 | 门户化在（研报问答 HTTP 已批）；选股页/API 未批 | 从策略到选股:81-84 |
| 情报/军团→商业 OS | 信号→五角色→Directive→LegionReport→IP/材料 | 闭环 A 设计成型，实测 Directive 11 open、9 条 unmapped | 整体方案 v2:178-192；:63-70 |
| 数据仓→学习 | learning.db/yt_learning.db 登记 | 13 表已登记待纳 catalog；YouTube 14 层未独立登记 | R/docs/plans/2026-11-02_数据智能部组织架构与岗位说明书_v1.md:90 |

## D. 领域线计划冲突 / 过期文档清单（12 条）

| # | 文档/问题 | 冲突或过期点 | 证据 |
|---|---|---|---|
| 1 | 执行层计划 3 版并存：10-31 零界研发计划 v2 → 11-03 整体研发方案 v2（draft，自称执行层单一方案）→ 11-04 研发计划 v2 | 11-03 方案未批却自称「取代 RND v1」；11-04 计划又自称 ROADMAP 执行子计划；三本账并存 | 11-03 方案:3；11-04 研发计划 v2:3-4；ROADMAP v4:7 |
| 2 | R/quant_backtest/INTEL_RD_PLAN.md（K 系列「开发日志唯一真相」） | 6,201 行、mtime 10-27 15:11，正文停 10-18：10-19 后量化开发无总账 | [实测] mtime/行数；文件:1-2,6168-6199 |
| 3 | 数据仓板块复盘与整合计划 v1 | STATUS=pending_approval 但正文称「生效/唯一执行口径」；且被数据仓研发计划 v3/v4 超越 | 复盘:3,5 |
| 4 | 情报板块研发计划 v1（10-31） | 仍 proposed（未 active）；情报线至今无 active 计划；11-03 军团作战化 draft 未批 | 情报盘点:153；作战化:3 |
| 5 | 学习标准 v2 取代 3 份旧标准（10-31 知识全链路标准 v1、11-02 学习项目标准化流程 v1、11-02 原文件与Word入库规范 v1） | 旧文件未标 superseded，仍在 docs/standards 并列 | 学习标准 v2:3-5 |
| 6 | 计划通胀：PLAN_REGISTER 262 份（10-31 生成）、>30 天未标注 24 份（9.2%）、旧 INDEX 声称 91 件已过期 | [实测] docs/plans 已 366 份、docs/audit 254 份：两周内 +40%，治理跟不上 | R/docs/plans/PLAN_REGISTER_20261031.md:6-13；[实测] 计数 |
| 7 | ROADMAP v4 唯一战略源 vs 11-03 整体研发方案 v2（draft） | ROADMAP:7 称旧计划一律降为子计划/历史档；但 v2 仍自称「执行层单一方案」待批 → 层级冲突未消 | ROADMAP v4:7,78-82；11-03 方案:3 |
| 8 | 量化旧路线图：10-18 量化智能交易优先级与路线图 / PRODUCT_ROADMAP v2/v3 | 已被 ROADMAP v4 取代；统一回测「接因子库/影子盘」未闭环；从策略到选股仍 pending_approval | 统一回测:2；从策略到选股:2 |
| 9 | 数据仓口径四套：314 数据集/265/72、catalog 256 表、display 258、复盘 display 92 | 同一仓多口径，展示与登记未统一 | ADR-019:100；数据仓 v4:4；复盘:14-16 |
| 10 | 军团数量：文档「6 军团」vs [实测] 7 个 composition（dev-legion 11-04 上线后入册） | 文档/状态未同批更新；7 组合全 draft | 整体方案 v2:63；[实测] compositions 目录+registry |
| 11 | 内容出口：gzh/video 无计划、无台账 | gzh 67 篇（最新 11-03）、video/output 仅 2 项停 2026-09-25；均无 STATUS/回执链 | [实测] 目录/文件计数 |
| 12 | 学习/期刊未定期化 | 期刊 001 自检全绿但 manifest 明示「未建计划任务/服务，定期化需 L2」 | W/outputs/learning_issues/20261104/manifest.json:71 |

## E. Top 15 领域必读

| # | 文档 | 为什么必读 |
|---|---|---|
| 1 | R/docs/plans/2026-11-02_ROADMAP_v4_唯一战略与主排期.md | 唯一战略+主排期源（W0–W4/三 deadline/两北极星） |
| 2 | R/docs/plans/2026-11-04_研发计划_v2.md | 当日最新执行口径（HZ/ACAD1/merge 队列/风险） |
| 3 | R/docs/plans/2026-11-03_整体研发方案_v2_最优执行版.md | 5 作战单元+2 旗舰闭环+15 关键路径（draft 待批） |
| 4 | R/docs/audit/2026-11-03_情报板块开发盘点与现状研究_v1.md | 情报线唯一全链实测盘点（采集→判读→证据→复核→门户→编成） |
| 5 | R/docs/2026-10-17_JEV舆情判定实证报告_K-ORG-156-157.md | JEV 是否有效的实证判例（corr≈0、置信虚高） |
| 6 | R/docs/audit/2026-11-01_策略库真值盘点_v1.md | 量化资产真值（13 卡≠13 策略、0 approved） |
| 7 | R/docs/plans/2026-11-01_统一回测口径_v1.md | 回测唯一口径（T+1/涨跌停/成本/IC），防研究漂移 |
| 8 | R/docs/plans/2026-11-04_盯盘完整系统_引擎_v1.md（+API/接线/告警 3 篇） | 量化最新闭环设计（引擎边界/human gate） |
| 9 | R/quant_backtest/INTEL_RD_PLAN.md（K 系列开发日志，注意断档） | 量化/情报/KG 历史真相与教训（6,201 行，按 K 号检索） |
| 10 | R/docs/plans/2026-11-03_数据仓研发计划_v4_历史数据前移版.md | 数据仓执行主计划（11-05 窗口/P1-P3） |
| 11 | R/docs/adr/2026-11-02_DWH-103_K5数据契约_pg_table_temp_v0.md | K5 数据契约判例（登记命中/软校验禁令） |
| 12 | R/docs/plans/2026-11-04_数据仓接口映射_v1.md | ACAD1/D1 dataset ref→registry 的最新映射 |
| 13 | R/docs/standards/2026-11-02_学习数据后流程统一标准_v2.md | 学习流水线唯一标准（S0–S8/G7/发布器） |
| 14 | R/docs/plans/2026-11-03_知识能力与治理研发计划_v1.md | 学习/知识产品化 KPI 与 WBS（唯一 active 学习计划） |
| 15 | R/docs/plans/2026-11-04_D1_Q2验收证据_v1.md + R/docs/audit/2026-11-04_五角色能力与接口对齐_v1.md | 军团/D1 Q2 判例 + 五角色接口实况 |

补充必读证据（非文档）：W/outputs/learning_issues/20261104/manifest.json + W/work/learning_publish/selftest.json（期刊 001 全部数字与 18 项自检）。

## F. 方法与覆盖

- **覆盖**：五条线共精读/取证 **28 份**（全读 9 份：研发计划 v2、研发计划总结、知识治理计划、数据仓复盘、五角色审计、D1 Q2、视频号回执、从策略到选股、造物炉；分段精读 13 份；索引/抽取 6 份）。索引先行：STATUS.md 尾部（4,218 行）、docs/plans 366 份 mtime 排序、PLAN_REGISTER、ROADMAP v4 全文。
- **实测命令**（只读；无 git 写、无服务启停）：`Get-ChildItem` 计数（plans=366/audit=254/compositions=9 目录=7 组合+_schema+_templates）、`Get-Item` mtime（INTEL_RD_PLAN=10-27 15:11）、sqlite `mode=ro&immutable=1` 查询（news/jev_audit/xueqiu/tdx/learning.db）、JSON 解析（catalog v39/256/14、compositions 7 draft、org 17 部门/77 席、five_roles 5、kb_cards 400、conclusions 100）、`grep` selftest（18 项 ok=true）、gzh 67 篇/video 2 项。
- **口径纪律**：文档自测数字标 [文档称] 并保留原时点；本次复测标 [实测]。已知差异示例：news 行数 115,430（11-03 文档）→120,364（11-04 实测）；JEV 115,220→120,255；hit 空 78.6%→79.2%；news 带股 9.4%→9.1%（严格口径复测一致）；catalog 表 256（实测，与 v4 文档一致）。
- **未做/局限**：未运行 `hq_status/agent_census/q2_probe`（可能写状态，遵只读纪律）；未逐份读 7 个 composition manifest yaml 全文（只读 registry 摘要）；schtasks 本机被安全策略拦截（沿用文档称）；`docs/ops`、`docs/q2` 未逐篇覆盖（由其他读手负责）；INTEL_RD_PLAN 仅抽读头/尾+K 系列抽样，中间 6,000 行按需 K 号检索。
