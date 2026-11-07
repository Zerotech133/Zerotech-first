# 零界 · 开源情报能力平台 —— 上手指南

> **产品定位（A0-DEC-20261107 覆盖 2026-11-04 A0 P5）**：零界 = 开源情报（OSINT）抓取与数据能力平台，金融为垂类——让有判断力、有想法、但不愿亲自承担完整软件工程的人，快速组建一支可长期协作的多 Agent 团队，把想法变成有依据、受约束、可试用、可迭代、可验收的事业成果。
> **能力场景（不定义整个产品）**：A 股量化情报工作站（缠论信号 + 九维选股 + 弱于板块产线 + 三线联动复盘；全链自动化：计划任务采集/计算，飞书推送触达，门户可视化翻看）、情报知识图谱、学习内容、数字军团与平台内核。
> 本文档面向使用者：30 秒明白是什么，3 分钟会上手。开发细节见 `quant_backtest/INTEL_RD_PLAN.md`（K 系列编号索引；该日志正文停在 2026-10-18，10-19 后以 `docs/plans/` + `docs/audit/` 为准）。

---

## 一、每天只看两个入口

### 入口 1：飞书推送（被动接收，看完即懂）

| 收到什么 | 什么意思 | 你要做什么 |
|---|---|---|
| 🔎 **蓝卡**（周一 15:35） | 全市场扫描发现弱于板块候选 + ★重点标记 | 想跟踪就挑案建案（见下方命令）；不看好忽略即可 |
| ⚠ **红卡**（触发时） | 在案个股触发了复评条件（破关键位/超额恶化等） | 打开卡片里指向的案例档案，按 §8 十分钟 SOP 走一遍 |
| **绿/橙/红卡**（结案） | 案件按 A 修复/B 持续/C 崩塌结案 | 无需动作；B 类已自动建新案继续跟踪 |
| **文本简报**（每日） | 知识图谱日报（KgDailySync 产出） | 阅读 |

**蓝卡挑案优先级**（二道筛，卡片与门户一致）：**★强转弱 ＞ ★强失速 ＞ ★深度垫底**。
- 强转弱 = 曾进九维强势警报池（≥75 分）后转弱——前强补跌型，复盘价值最高
- 强失速 = 当日仍强势（RPS50>80）但短期弱于板块——失速预警
- 深度垫底 = 超额 ≤-20pp 且板块内分位 ≥90——极端弱势

### 入口 2：门户（主动翻看）`http://127.0.0.1:8734`（**唯一人类入口**，K-STOCK-113 立宪；8080 仅为容器/局域网反代链路，勿当入口）

首页是**领域入口板**：六大卡一屏（点击下钻专项，不再长页面滚动）。

| 页面 | 看什么 |
|---|---|
| `/`（8734 首页） | 领域入口板：量化股票 / 情报中枢 / 内容创作 / 开发者（预留）/ 系统运维 / 更多领域（预留），每卡带实时微指标 |
| `/quant` | 量化股票专项（结果驾驶舱）：**今日预警速览**（/api/alerts 聚合盘前/缠论/九维/weak/共振五路预警值）+ KPI + 策略资产组合 + 页面目录（过程页收纳）+ 自选行情 + 回测 + 量化工具坞 |
| `/strategies` | 策略资产中心：13 条策略记分卡（实盘 6 / 影子 3 / 工具 4，2026-10-26 实测 `portal/strategies.json`）+ 生命周期带。**上新一条策略 = 在 `portal/strategies.json` 注册一条 JSON，三处 UI（首页/策略中心/导航）自动同步，前端零改动** |
| `/creator` | 内容创作专项：公众号草稿库 + 短视频成片库 + 「录一段」速录通道 |
| `/ops` | 系统运维专项：OS 控制台（计划任务/备份磁盘/告警/容器/采集管道） |
| `/dev` | 开发者专项（预留）：「敲代码」领域规划位 + Dify/WebUI 在线基建 |
| `/fin` | 金融量化导航中枢（量化域全部子页入口） |
| `/intel` | 情报工场（首页同款四卡生命周期板）：🛰 开源情报收集（六源+61 标的监控）→ 📡 证据流（JEV 判断/晨报）→ 🕸 知识图谱（六件套行式下钻·个股三面下钻）→ 📨 飞书预警输出（大盘+五路实时值·晨/盘后定时+30分钟阈值监听即时推·个人+群·立即推送按钮+文档枢纽）+ 今日证据流简表 |
| `/bt-linkage?code=600519.SH` | 三线联动页：个股×板块×大盘归一化对比 + 五信号标注（①抗跌 ②滞涨 ③共振底 ④孤军深入 ⑤三共振） |
| `/bt-weak` | 弱于板块案例库：在案状态表 / 已结案 / 最新扫描 Top10 + 非重点参考 |

> 门户打不开时：`schtasks /run /tn "AI工作站门户API"`（它是常驻+5 分钟看门狗，rc=0x800710E0 是健康标志，不用管）。

---

## 二、weak 产线操作三步（唯一需要动手的环节）

```powershell
$PY = "C:\Program Files\Python311\python.exe"; cd e:\老田的AI助手\quant_backtest

# ① 挑案建案（收到蓝卡后）——板块号自动解析，档案自动生成
& $PY weak_track_newcase.py 301216 --name 万凯新材 --pivots 16.12,16.80,17.93
# 输出的 Register-ScheduledTask 命令复制执行 → 每日 15:30 自动跟踪

# ② 查在案状态（随时）
& $PY weak_track_view.py 301216 --tail 20

# ③ 结案（归因完成后，A 修复/B 持续/C 崩塌；B 类可自动建新案滚动）
& $PY weak_track_close.py 301216 --class B --send --newcase-name 万凯新材R2

# ④ 案例库结案汇总（§10，每月 1 日 08:30 自动推送·任务 WeakMonthlySummary；随时手动）
& $PY weak_track_summary.py --send

# ⑤ 复盘学习层（§11，每月 1 日 08:40 自动推送·任务 WeakMonthlyLearn；
#    从结案库提炼规律：剧本兑现率/跟踪时长/触发关联，n<5 只积累不下结论）
& $PY weak_track_learn.py --send

# ⑥ 超期未结案巡检（§12，超龄 60 天/停滞 3 天/任务失联三检测；
#    全健康不打扰，异常才推琥珀卡催办；口径 --max-days/--stall-days 可调）
& $PY weak_track_watch.py

# ⑦ 结案质量审计（§13，字段/归档/档案/任务停/B类新案五项；
#    close md 声明 vs reports/ 实物，全绿不打扰，异常推 turquoise 卡）
& $PY weak_track_audit.py

# ⑧ 案例库飞书归档（§14，每月 1 日 08:50 自动推送·任务 WeakMonthlyArchive；
#    全量快照 md（含在案观察节）→ 专属文件夹归置 → 上传 → 导入 docx →
#    清理源 md → 推 wathet 卡（最近结案 3 案+触发累计）。
#    需 app 开通 drive:file:upload / docs:document:import /
#    space:folder:create / space:document:delete，未开通时任务 rc=2
#    并在日志打印授权直达链接，绝不发半截链路）
& $PY weak_track_archive.py --send

# ⑨ 周报聚合推送（§15，每周一 15:50 自动推送·任务 WeakWeeklyReport；
#    §12 巡检 + §13 审计 + 本周动态（新立案/结案/跟踪打点/触发+活跃
#    明细）+ 环比趋势（state 基线）合并一张 indigo 卡，全绿也推
#    （主动汇报产线状态，与两节静默纪律分层），错峰 WeakScan 15:35）
& $PY weak_track_weekly.py --send

# ⑩ 任务群健康守护（§16，看守守夜人：Weak* 全任务族三检测——状态/
#    结果/错失运行，WeakMonthlyArchive rc=2 等授权属已知待办不告警；
#    每日 15:55 自动巡检·任务 WeakDailyHealth（晚于日任务对收链兜底），
#    全绿静默不推送，异常推红卡 🚨）
& $PY weak_track_health.py --send

# ⑪ 产线导航卡（§17，17 节全貌按节奏分组：每日/每周/每月自动+手动
#    按需+你唯一要做的日常；每月 1 日 08:20 自动推送·任务
#    WeakMonthlyGuide——月度链开场，先看地图再看事实，隔月回看一翻即上手）
& $PY weak_track_guide.py --send
```

状态流 `reports/weak_track_<代码>.jsonl`、案例档案 `reports/weak_cases/`（门户 `/bt-weak` 可视化同源）。

**归因方法（六问，档案 §2 逐条过）**：① 有无利空 ② 主力是否砸盘 ③ 前强补跌还是趋势走弱 ④ 票特异还是板块共性 ⑤ 风格错位吗 ⑥ 分清因果。命令块①-⑥ 已写进档案，逐条跑数，**定性推断必须过数据**（万凯案判例：③ 的直觉预填被 500/250 双窗口数据推翻）。

---

## 三、方法论一页纸

1. **缠论信号（信号 1）**：三类买卖点识别，ChanlunScan 每日 05:50 全市场扫描；影子模式验证中（K33ShadowCheck 周五对账）。
2. **九维选股漏斗**：RPS50>80 强势池 → 多维评分 → 综合分 ≥75 入警报池（K33）；全史分位方向统一 **0=最强**。
3. **weak 产线（信号 2）**：r20 超额 ≤-5pp × 日均额 ≥2 亿 → 二道筛定级 → 建案跟踪 → 触发复评 → 归因结案。全链：扫描→建案→归因→跟踪→复评→结案→门户归档。
4. **三线联动**：任何票都放在「个股×所属板块×大盘」三线归一化里看强弱，五信号引擎自动标注分型。
5. **纪律**：触发红卡十分钟内走完 §8 复评；结案必须给类别（A/B/C）不允许悬案；B 类滚动建新案直到出结论。

---

## 四、目录地图

| 目录 | 用途 |
|---|---|
| `quant_backtest/` | **核心**：缠论/九维/weak 产线/三线联动全部代码与开发日志（INTEL_RD_PLAN.md） |
| `portal/` | 门户 API（8091）+ 页面 |
| `news_collector/` | 金融新闻采集 + 每日简报 + 飞书发送器（send_feishu.py，全站共用） |
| `jev-eval/` | JEV 评估告警链（backup_alert.py 三通道发送范式出处） |
| `reports/` | 产线数据落盘：weak_track 状态流 / weak_scan 扫描 / weak_cases 档案 / 联动图 |
| `knowledge_docs/` | 知识库文档（缠论/金融/法律/尽调等 6 部） |
| ~~`知识图谱/`~~（2026-10-27 移出主仓） | 席位/关联图谱研究（快照、SQL、study，第三方 qianjiang-graph Java/TS）——已归档至 `_archive/2026-10-27_code-cleanup/知识图谱/`，主仓不再跟踪；理念移植见 `portal/api_kg.py` / `quant_backtest/kg_portal.py` 注释 |
| `gzh/` | 公众号撰文产线（config + gzh_writer） |
| `video/` | 视频产线（pipeline） |
| `ops/` | 健康检查（health_check.py；health_latest.json 已退役——2026-10-18 起不再落盘，后继数据源 GET :8091/api/execution/health（R1 总线）） |
| `prototypes/` | 仪表盘原型（HTML） |
| `quantdinger/` | QuantDinger 部署（docker-compose） |
| `dify/`、`dify-backup/` | Dify 平台及其数据备份 |
| `pylibs/` | 本地依赖库（PIL/colorama/qrcode） |
| `tools/` | 工具箱（ffmpeg 等） |
| `backups/` | 工作站定期备份 |

---

## 五、自动化时序（核心任务）

| 时点 | 任务 | 产出 |
|---|---|---|
| 05:50 每日 | ChanlunScan | 缠论信号扫描（mls） |
| 06:20 每日 | KgDailySync | 20 步知识图谱同步 → 每日简报推送 |
| 06:40 每日 | NineDimScan | 九维快照（K33 警报池随数据积累） |
| 08:30 每日 | PostHolidayCheck | 开市首验四链路（mls/K33/影子/weak），全过静默 |
| 15:30 每日 | WeakTrackCheck | 在案个股三线跟踪，触发即红卡 |
| 周一 15:35 | WeakScan | 全市场弱于板块扫描 → 蓝卡 |
| 周五 08:00 | K33ShadowCheck | 影子监测周对账 |
| 常驻 | PG-Robot | 飞书群 @机器人 中文问数（multi_agent 本地 LLM） |
| 5 分钟看门狗 | AI工作站门户API | 门户保活 |

完整 53 任务：`Get-ScheduledTask | ? {$_.State -ne 'Disabled'}`。

> **部署约定**：产线 Python = `C:\Program Files\Python311\python.exe`（唯一）。新建计划任务、ps1 启动器、文档示例中的 python 引用一律写显式完整路径；改 `.ps1` 文件注意保持 UTF-8 BOM（丢了会被 PowerShell 5.1 按 GBK 解析出语法错，watchdog 判例）。

---

## 六、出问题先看这（排障速查）

1. **推送没收到**：先测通道 `& $PY news_collector\send_feishu.py <任意utf8文本文件>` → 输出 SEND_OK/SEND_FAIL；三通道（OpenAPI→lark-cli）自动降级，全挂查网络/代理（开机早期代理未就绪判例：等 1 分钟自愈）。
2. **任务没跑**：`schtasks /query /tn "<任务名>" /v` 看 Last Result；日志在 `C:\Temp\*_<task>.log`，PowerShell 看中文日志加 `-Encoding utf8`。
3. **jsonl/JSON 读不出**：BOM 判例——读取前 `lstrip("\ufeff")`。
4. **门户改了代码**：kill python 进程 → `schtasks /run /tn "AI工作站门户API"`（换血判例）。
5. **结案/推送类命令**：默认预览，`--send` 才真发（双段式防误触判例）。
6. 健康总检：`& $PY ops\health_check.py`（ops/health_latest.json 已退役，2026-10-18 起结果不再落盘；执行健康后继数据源 GET :8091/api/execution/health（R1 总线））。
7. **装了包还报 ModuleNotFoundError / 行为不对**：本机有两个 Python——**产线唯一解释器 `C:\Program Files\Python311`**（全部任务/脚本已显式引用），用户 Python312 仅限个人交互实验、产线不依赖。产线装依赖只装 3.11；新脚本/新任务一律写显式完整路径，**禁止裸 `python`**（它会走 PATH 解析到 3.12，card-handler 判例）。

---

## 七、深入文档索引

- `quant_backtest/INTEL_RD_PLAN.md` —— 开发日志唯一真相（K 系列编号，含全部判例）
- `quant_backtest/linkage_weak_review_template.md` —— weak 产线九节规格模板（§1-§9 SOP 与命令块）
- `knowledge_docs/缠论技术分析知识库.md` —— 方法论底座
- 顶层 `zhice-os-v2-集成说明.md` / `newquantsystem_v2-*` —— 历史架构与源码恢复报告
