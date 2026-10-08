# WorkBuddy × 老田的AI助手 融合方案研究

> 生成：2026-10-08 08:30 ｜ 结论基于：全站目录摸底 + 生产拓扑实测 + 昨日 Jev 专项实战复盘

---

## 〇、结论先行

1. **融合通道已经打通并实战验证**：WorkBuddy（云上工作台，实际跑在本机）可以直接读写 `E:\老田的AI助手`——昨天的 Jev 专项报告就是第一次实战，且报告中的 P1-2（日志断链）已被本地侧当天修复（`zhice.ps1` A3 注释 + `C:\aios\jev-eval\logs\gate.log` 实时 UTF-8 JSON 验证通过）。**「WorkBuddy 出思路/审计 → 本地采纳/执行」的闭环已跑通一环。**
2. **正确的融合姿势不是「合并成一个系统」，而是分层分工**：本地计划任务管采集执行（102 个），zhice.ps1 管进程守护（5 分钟自愈），WorkBuddy 管分析、审计、研究、开发协作——各干各的，通过文件系统和 git 交接。
3. 本报告给出五层融合架构 + 四步落地路线，第 3 步（排查 3 个失败计划任务）本轮对话即可开工。

---

## 一、两套系统画像（本机实测事实）

### WorkBuddy（融合中的「云上大脑」）

- 会话工作区在 `C:\Users\Administrator\WorkBuddy\`，**物理上跑在你本机**，对 E 盘、C 盘有完整读写能力（沙箱内，个别程序如 schtasks 被安全策略拦截）
- 具备：多专家协作、技能（skills）、定时自动化（automations）、跨会话记忆、文件产出/预览
- 自带隔离 Python（managed 3.13），与产线 Python 互不污染

### 老田的AI助手（E 盘工作站，生产系统）

| 层 | 位置 | 说明 |
|---|---|---|
| 工作站聚合层 | `E:\老田的AI助手\` | 门户 portal、Jev 代码副本、文档/报告、运维脚本、知识图谱、9 个子项目 |
| 生产代码层 | `E:\zhice-os-v2-windows-20260920\code` | zhice OS v2 真身（venv Python），`zhice.ps1` 统一 start/stop/status/watchdog |
| Jev 生产层 | `C:\aios\jev-eval\` | 四件套 + logs/（Python 3.11，双 Python 治理：禁止裸 python） |
| 数据层 | zhice-db 容器（PostgreSQL）+ Docker 9 容器 | `v2_alert` / `v2_candidate_pool` / `v2_bar_daily` 等 |
| 触达层 | 飞书（卡片推送 + feishu_bot_ws）+ 门户 8080/nginx | 蓝卡/红卡/绿橙红卡 + 六大领域入口板 |
| 调度层 | Windows 计划任务 ×102 | 每日 03:00 备份链、巡检出 `ops/health_latest.json` |

**健康快照（今日 03:00 实测）**：6 个服务全绿（门户 API 8091 / nginx 8080 / 前端 5175 / QD 桥 9103 / 卡片回调 9104 / Jev 解释器），但 102 个计划任务中有 **3+ 个 bad**：`AI工作站Ollama服务` rc=4294967295、`AI盘前分析` rc=1、`ChanlunScanNotify` rc=3。

---

## 二、已验证的融合通道（昨日实证，不是设想）

| 通道 | 实证 |
|---|---|
| **文件直写** | Jev 专项报告直接落到 `E:\老田的AI助手\jev-eval\docs\`，本地方便采纳 |
| **代码审计** | 逐行读 `jev_gate.py` 等 3274 行，产出问题清单（P1×3/P2×2），本地侧当天修复 P1-2 |
| **运行时核查** | netstat 端口、进程、日志 mtime、review_data 行数统计、评测 JSON 摘要——不碰生产即可摸清现状 |
| **记忆衔接** | 融合规则（导入分工、路径、目录要点）已写入跨会话记忆，下次会话直接可用 |

**局限（已探明）**：schtasks 等系统程序被沙箱拦截 → WorkBuddy **不适合**做系统级任务注册/管理，这类仍归本地计划任务。

---

## 三、五层融合架构

```
┌─ L5 触达层（已有，不动）────────────────────────┐
│  飞书卡片/门户 8080 —— 所有告警触达仍走现有链路      │
├─ L4 任务分层（待建）───────────────────────────┤
│  本地计划任务=采集执行 · WorkBuddy automation=定时分析 │
│  · 人=决策  （AI 不抢系统管理权）                   │
├─ L3 知识蒸馏层（待建）──────────────────────────┤
│  正向：WorkBuddy 结论 → docs/thoughts/ 留痕        │
│  反向：INTEL_RD_PLAN.md K条目 → 蒸馏进跨会话记忆      │
├─ L2 运维巡检层（半通）──────────────────────────┤
│  WorkBuddy 读 health_latest.json / n1_report /     │
│  gate.log / 端口/进程 → 出「根因分析」而非只报 rc     │
├─ L1 代码协作层（已通）──────────────────────────┤
│  E 盘代码直读直写 + docs/changes/ 留痕 + git 保护垫  │
└──────────────────────────────────────────────┘
```

**核心分工原则**：本地计划任务负责「跑」，WorkBuddy 负责「看懂、研究、写」，飞书负责「送达」，你负责「拍板」。四者通过**文件系统**交接（health_latest.json、n1_report、docs/），不需要新增任何服务或接口——零侵入，符合 Jev 同款哲学。

---

## 四、立即兑现的三个融合场景

### 场景 1：失败任务根因排查（价值最高，本轮可开工）

`ops/health_latest.json` 每天只给 rc 码，不给原因。WorkBuddy 直接深挖：
- `AI工作站Ollama服务` rc=4294967295（0xFFFFFFFF，通常是进程被强杀/启动器崩溃）
- `AI盘前分析` rc=1（脚本退出码 1，需看任务日志）
- `ChanlunScanNotify` rc=3

产出：根因分析 + 修复建议落 `docs/ops/`，**修不修由你拍板**。

### 场景 2：Jev 遗留 P1 清尾

昨日报告 5 项问题已修 1（P1-2 日志断链），剩：
- P1-1 校准闭环欠回填（`triples_outcome` 61/874=7%）
- P1-3 评测基线 16 天未回归
- P2-1/P2-2 SQL 拼接与阈值硬编码

WorkBuddy 可直接改 `C:\aios\jev-eval`（改前 git 基线），回填脚本挂计划任务的动作由你在本机执行（沙箱限制）。

### 场景 3：镜像副本治理（新发现的隐患）

`E:\老田的AI助手\jev-eval` 与 `C:\aios\jev-eval` 是两份代码（当前字节级一致），但 C 盘侧已有新增文件（`auto_verify_jobs.ps1`）。**双份手工维护必然漂移**。建议：明确 C 盘为运行权威、E 盘为版本留档；长期解法是 git 收编（一处权威，另一处 checkout）。

---

## 五、红线（融合必须遵守的六条）

1. **生产进程/计划任务不杀不改**：zhice.ps1 watchdog 每 5 分钟自愈，AI 别跟它打架；进程识别已收紧（M3），不碰
2. **删除一律回收站 + 先备份 + 每批 ≤10**（既定铁律）
3. **密钥不进对话**：`JEV_API_KEY` / `TUSHARE_TOKEN` / `QD_PASSWORD` 在 .env.local 与 ps1 里，WorkBuddy 不读取、不引用、不外传
4. **双 Python 互不污染**：产线用 Python311（`zhice.ps1` 已锁定），WorkBuddy 用自己的 managed Python，互不装包
5. **改生产代码前先 git 基线**：`E:\老田的AI助手` 根目录及核心子项目目前无 git（仅 dify 有），这是融合最大的裸奔点
6. **分析产出先落文件再决策**：所有研究/审计/建议落 `docs/`，改不改由你拍板，AI 不擅自动生产

---

## 六、落地路线图（四步）

| 步骤 | 内容 | 产出 | 谁做 |
|---|---|---|---|
| 1. git 保护垫 | 工作站根目录 `git init` + .gitignore（排除 backups/、*.7z、__pycache__、logs），核心子项目跟进 | 版本保护基线 | WorkBuddy 执行，你确认 .gitignore |
| 2. 每日融合巡检 | automation 每早读 `health_latest.json` + `n1_report` + `gate.log` → 异常出根因分析落 `docs/ops/YYYY-MM-DD_巡检.md`，正常不出声 | 每日巡检自动化 | WorkBuddy（automation 配置） |
| 3. bad 任务排查 | 场景 1 三个 rc 异常任务逐个定位根因 | `docs/ops/` 根因报告 | WorkBuddy，本轮即可 |
| 4. 知识蒸馏 | 每月把 `INTEL_RD_PLAN.md` 新增 K 条目蒸馏成摘要进跨会话记忆 | 记忆持续保鲜 | WorkBuddy（月度 automation） |

---

## 七、风险与开放问题

- **automations 的沙箱边界待实测**：定时任务在无人会话里能否读写 E 盘、能否被允许跑 schtasks 查询，需第 2 步上线后验证一次；不行则降级为「每日手动说一句巡检」
- **git init 会把 workstation 变仓库**：dify 已有自己的 git（嵌套仓库），init 前需在 .gitignore 排除 `dify/`，避免嵌套仓库混乱
- **102 个计划任务的功能清单没有统一文档**：巡检自动化只能覆盖有日志的任务，建议后续让你或本地侧出一份任务-职责对照表（可由 WorkBuddy 从任务 XML 批量提取生成）

---

*证据链：`ops/health_latest.json`（2026-10-08 03:00）、`zhice.ps1`（A3 2026-10-07 注释）、`C:\aios\jev-eval\logs\gate.log`（08:19 实时）、E/C 两份 jev-eval 字节对比、昨日 JEV专项研究报告_2026-10-07.md。*
