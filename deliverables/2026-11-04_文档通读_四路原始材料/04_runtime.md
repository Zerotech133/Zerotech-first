# 04 运行态 · 审计 · 复盘 · 风险通读（近 30 天优先）

> 口径：【实测】= 本次命令输出（2026-11-04 07:51–07:55 Asia/Shanghai，只读）；其余为「文档称」。
> 路径约定：`docs/...` = `C:\项目\老田的AI助手\docs\...`；`deliverables/...` = `C:\项目\deliverables\...`；`AIdeliv/` = 仓内 `deliverables/`；`STATUS-A` = `C:\项目\老田的AI助手\STATUS.md`，`STATUS-B` = `C:\项目\STATUS.md`。
> 边界：只读；无 git 写、未改仓库文件、未启停服务、未执行 L2、未整读 >300KB 大文件。
> 实测量级【实测】：docs/audit **268** 份、docs/ops **294** 份、docs/plans **411** 份（任务书 257/260 为旧口径）。

---

## A. 近 30 天事故/失败表

| # | 时间 | 事件 | 根因 | 处置 | 复发/现状 | 证据 |
|---|---|---|---|---|---|---|
| A1 | 10-31 23:11 | 治理「假 done」：3 个 P0 单被标 done 并删分支，master 无实现 | 仓外治理进程按「worktree 干净」自动置 done | 改回 review/doing 重建实现；孤儿目录移 C:\Temp；写入硬规则 | 未再记录；规则仍靠人工守 | `docs/audit/2026-11-01_P0审计修复落地回执_v1.md:71-83` |
| A2 | 11-01 | M4 签字门 0/4 且非闸门（报告≠门） | `m4_signoff_status.py` 只出报告，test_gate/release 不消费 | 改四签口径（第4签=ARCH1/A0监督）、滑期 | 11-01 回执仍标「未实现」；残余 | 同 A1 文件`:40,:63-64`；`docs/audit/2026-10-31_全项目代码审计复盘与开源方案补齐_v2.md:90-95` |
| A3 | 11-02 09:20 | ACL-104「部署炸弹」：生产 DB=0045，代码 head=0049（差 4 迁移） | 0046–0049 迁移未入 git（外部仓 186 脏文件）+ guard 只查端口不查 DB revision | 迁移纳管 `ce5e895`+preflight fail-closed `49f0b76`；11-02 11:22 ACL-105 升 0045→0049 | 已闭环（24h 观察） | `docs/audit/2026-11-02_ACL-104_BUG-001_止血复盘_v1.md:11-16,68` |
| A4 | 11-02 13:29 | 并发提交事故：`5c23d86f8` 把 DWH-103 wave2 在研文件（+124 行）带入 master，缺 ADR-019 | 并行会话 `git add -A`；共享仓无显式 pathspec | 补 ADR-019 收口+git notes 标注+AGENTS §3 立规 | 结构性风险仍在（主树脏 253【实测】） | `docs/audit/2026-11-02_并发提交事故_DWH103_wave2_v1.md:1-18` |
| A5 | 11-02 前起 | TdxSync 停更 53 张 stale 表 | DNS 解析失败（errno 11001）+ 远端 HTTP200 零行软失败 + 全量超 2h 被调度器终止（Last Result=267014） | 拆 ZeroBound\TdxSync_A/B/C/D 任务；TdxSync 已 Disabled | **未收口**：11-04 预检三表 max_date 仍 2026-11-01（stale 3 天） | `docs/audit/2026-11-02_xtcloud停更排查.md:11,19,37-39`；`docs/ops/2026-11-04_L2预检补充_v1.md:20` |
| A6 | 11-02→11-04 | Guba 采集反爬：三窗口 fail 60%/100%/66.7%；11-04 02:28 小批量 5/5=100% | 判定 IP/反爬级限制（非数据源全断，单只 smoke 3/3 成功过） | 未处置；建议 24h 冷却/换会话/代理（需 L2） | **复发中** | `deliverables/2026-11-03_Guba采集限流策略建议_v1.md:7-15,46-49`；`STATUS-A:4141` |
| A7 | 11-03 05:47 | 8091 重启窗口门户不可用 + 1 小时内多次重启 | 单点+内存会话（TTL 1h）+ supervisor `RunLevel=Limited/PT10M`+launcher 前台 | 方案/代码就绪（会话持久化默认关）；P0b/P0c 待 L2 | **未收口**：11-04 仍有多次受控重启（pid 23928→42848） | `docs/ops/2026-11-03_8091_stability_plan_v1.md:13-29`；`docs/ops/2026-11-04_L2预检补充_v1.md:17,31` |
| A8 | 11-03 15:40 | HZ 基线 7 failed（3465 passed） | 测试固定日期（3）+ 页面/测试契约漂移（2）+ DEV3 glm 绑定与 role_group 脱节（2） | 15:58 全量 3472 passed/0F；16:06 HZ daily PASS | 未复发；但同类「测试时间炸弹」11-04 又发（A9） | `docs/ops/2026-11-03_HZ基线失败清单_v1.md:3-15,36-42`；`docs/audit/2026-11-03_HZ_7F根因研究_v1.md:5-13` |
| A9 | 11-04 02:29 | 全量基线 4F（dev_gate×2/dev_legion×1/test_gate_coverage×1） | 固定日期+新增测试文件未入 fast 门（覆盖率 24.92%<25%） | `--now` 注入+测试并入已覆盖文件；02:43 3614 passed；04:20 **3669 passed/0F/2 skipped** | 已闭环 | `docs/audit/2026-11-04_代码审查_v1.md:56-63`；`docs/audit/2026-11-04_代码审查与项目复盘_v1.md:5,47` |
| A10 | 11-03 16:30 | merge 窗口 0 合入 | H5 钉扎 1+凭据误报 1+datawarehouse worktree 缺 gitignored 数据（fast 9F） | 补数据后 340 passed；21:42 又被 `login.html` 他人未提交改动挡 post-merge | 11-04 04:04 A0 批窗口例外，提前合 8 支 | `ROADMAP v4:139`；`docs/ops/2026-11-03_W0止血清单_v1.md:55-62,87-93`；`docs/ops/2026-11-04_窗口例外提前合并_批准记录_v1.md:38-42` |
| A11 | 11-04 04:1x | 安全 P0：受保护页 HTML 直链+大小写变体可无登录 200（含情报工作台） | 短路由有 auth_request，静态文件直链绕过；Windows bind-mount 大小写不敏感 | nginx 加 `~*` 正则覆盖 15 页并 reload；未登录全 302 | 运行态已生效；**主区 nginx.conf 仍未提交【实测 `M portal/nginx.conf`】** | `deliverables/2026-11-04_UI统一与安全修复_代码审查_复盘总结_v1.md:21,53-88`；【实测】`git status` 07:53 |
| A12 | 11-04 04:15 | 五角色 `/api/chat` 5/5 身份漂移（A0 自称"最高决策节点"、黄忠自称"Z.ai GLM 助手"） | /api/chat 只做模型绑定、不注入岗位 persona；赵云 prompt 缺失 | 注入 `five_role_persona`+补赵云 prompt+重启 8796；5/5 对齐 | 已闭环 | `docs/audit/2026-11-04_五角色能力与接口对齐_v1.md:9-42` |
| A13 | 11-04 02:45 | 学习 pipeline 夜间重建 units 覆盖人工去重（视频 800→1018、二期 1116→1293） | pipeline 只 clean→units，未接治理后置步骤 | 新增 `learning_curation.py` 接入 pipeline gate 前；E2E 恢复 800/1116/286 | 已修；缺 curation 幂等回归测试 | `deliverables/2026-11-04_代码审查与项目复盘_v1.md:20,64,80` |
| A14 | 11-04 04:59 | 8796 重启遇 Access denied；LegionOps sentinel 杀不掉 S4U 进程 | Session0/S4U 高权限进程，普通会话无法 taskkill | UAC 提权脚本 `ops/dsh_restart_8796.ps1`；新 pid 53704/现 32308 | 已知判例；不手工杀 SYSTEM 子进程 | `docs/ops/2026-11-04_8091_8796重启回执_v1.md:18-36` |
| A15 | 11-04 07:15 | P0 数据口径漂移：catalog v39/256 表 vs registry physical 304/display 258（差 48） | 重建/覆盖后口径不一致（同 updated 时间） | 不擅自重建；red 告警+投 DATA1/A0；DATA1 待决 | **未收口**（实测 07:53 仍是 304/256） | `docs/ops/2026-11-04_项目复检_v1.md:19-31`；【实测】`GET 8091/api/dw/stats` |
| A16 | 11-04 02:20 | restic 异地备份 OFFSITE_COPY rc=1（本机 backup/check rc=0） | 本机网络切 192.168.2.104，10.205.0.118 无路由（SSH 22 timeout） | 待网络恢复补跑；本地 repo 8.61GB 完好 | **未收口** | 【实测】`work/restic_trial/receipts/receipt-20261104.json`（ok=false/offsite.rc=1）；`STATUS-A:4140` |
| A17 | 11-04 06:54 | 百炼 SFT 评测 40 题仅 4/6 达标（citation 0.55 / groundedness 0.6184 / 拒答 1.0） | 引用训练样本丢上下文 | SFT v4 修正（4,039 条含上下文），未验证 | **未收口** | 【实测】`git log -12`：`b953c934a`（07:54） |
| A18 | 11-04 07:47 | HZ「六条 6/6 达成」与 07:15「中期 4/6/NO_GO」并存 | 07:15 为旧快照；系统任务 09:30/10:00/10:30 尚未跑 | 待正式复核；不得把中期当最终 PASS | **待签**（最终最早 11-10 22:15） | `STATUS-A:4212-4218`；`docs/ops/2026-11-04_项目复检_v1.md:41-44` |

---

## B. 假绿 / 门禁现状（已修 vs 仍开）

| # | 问题 | 现状 | 证据 |
|---|---|---|---|
| B1 | **H5 合并质量门「自证+可跳过」** | **已修**（H5-a master 钉扎 `test_gate.ps1/test_baseline.json`；H5-b 一次性 token 不认裸 env；baseline 只允许 ratchet；docs_only 去 `.js`）。合并 `77f825e1b` | 【实测】`tools/merge_gated.py:35-38,84-87`；`docs/audit/2026-11-01_P0审计修复落地回执_v1.md:10,38`；`STATUS-B:6350` |
| B1b | H5 残留：分支**自改** gate 脚本（6 支）与「落后 master」分支 | **仍开**：被拒而非绕过（HZ/UI 因 H5 钉扎 REJECT，待 owner rebase/独立审批）。门禁在有效工作 | `docs/audit/2026-11-04_W1_RESOLVER_与W0候选验收_v1.md:178`；`docs/ops/2026-11-04_1630主树与门禁预检_v1.md:19-21,32` |
| B2 | **H6 Codex 插件 OS 级隔离** | **仍开**：仅 AST 禁属性链+子进程代理；OS Job/AppContainer 未做，文档明示「AST 不是安全边界」 | `docs/audit/2026-11-01_第四波HQ与P0合并收口_v1.md:28`；`docs/audit/2026-11-01_代码审计与BUG修复复盘_v1.md:53`；`docs/audit/2026-10-31_..._v2.md:77-82` |
| B3 | **H7 exec_sandbox Job 失败 fail-open** | **已修**（fail-closed，`a18c467bd`）；文档称 8103 已加载（11-01 02:26），**未做真实失败演练**；本次无法读 8103 启动时间（S4U） | `docs/audit/2026-11-01_P0审计修复落地回执_v1.md:39`；`docs/audit/2026-10-31_..._v2.md:84-88` |
| B4 | **baseline/env 自证** | **部分修**：pin+token+ratchet 已上；但「known/env/flaky 只减不增」仍由分支侧维护，无第三方复核 | 【实测】`tools/merge_gated.py:14,84-87` |
| B5 | **同模型自评** | **仍开**：SEC2/QA1 均 glm-4.6；「禁同模型自评/跨厂商 reviewer」列为造物炉 F021（M2 11-17~28 才生产化） | `STATUS-B:6350-6351`；`docs/plans/2026-11-04_造物炉_研发计划_v1.md:134` |
| B6 | **auto 证据当 PASS（Q2 假绿）** | **部分修**：已提双状态字段/报告标题/抽检；实测仍是 auto 6/6 ready 但 Q2=PASS1/条件1/Q1=4、**owner 签字 0/6** | `docs/ops/2026-11-04_W1审计与WIP治理告警_v1.md:17-29`；`docs/ops/2026-11-03_系统问题体检与优化方案_v1.md:17,57-66` |
| B7 | **计划任务「绿色掩盖」** | **仍开**：`KgDailySync` rc=0 但 22 步不含 xtcloud 落库；`TdxSync` Disabled；仅看 rc 会误判数据链健康 | `docs/audit/2026-11-02_xtcloud停更排查.md:11,23` |
| B8 | **测试时间炸弹/硬编码快照** | **反复出现**：11-03 HZ 7F、11-04 4F、UI `page_manifest` 硬编码 129 页；规范已提「测试必须可注入 now/动态断言」，未全落地 | `docs/audit/2026-11-04_代码审查_v1.md:56-63`；`deliverables/2026-11-04_UI统一与安全修复_..._v1.md:96-100` |
| B9 | **审批形式化** | **仍开**：审批库 40/40 approved、0 rejected、pending=0（飞书待办未同步本地库） | `docs/ops/2026-11-03_审批抽样审计_v1.md:5,34-37` |
| B10 | **数据口径自证** | **仍开**：catalog 256 vs registry 304（A15）；contract_artifacts 4 个中 3 个 sha256_mismatch【实测】 | 【实测】`GET 8091/api/dw/stats` 07:53；A15 证据 |

---

## C. 未收口项清单（状态 + 证据）

| # | 项 | 当前状态 | 证据 |
|---|---|---|---|
| C1 | **WIP 双超限**（doing/worktree） | **超限 2–3 倍**：worktree **58**【实测 07:53】；主树 dirty **253**【实测】；doing 41–48（文档 07:50 记 41、03:28 记 42、04:05 记 48；ADGS W0 目标 ≤8/≤20） | 【实测】`git worktree list`/`git status --porcelain`；`STATUS-B:6351`；`docs/ops/2026-11-04_W1审计与WIP治理告警_v1.md:19-20`；`docs/ops/2026-11-04_项目复检_v1.md:36-37` |
| C2 | **计划任务失败/未跑** | ResticTrialDaily rc=1（02:20）；GubaCrawl rc=2（11-03 17:50）；TdxSync Disabled（last 0x41306）；LegionGateway8795 rc=1（07:52）；A0-Verify 08:05/HZ 09:30/10:00/10:30 均未跑（0x41303） | 【实测】`Get-ScheduledTaskInfo` 25 任务 07:52-07:54 |
| C3 | **数据链 stale** | `/api/dw/stats` `data_date=2026-11-02`；catalog v39/256 vs registry 304/258；contract_artifacts mismatch=3；TDX 三表 max_date=2026-11-01（stale 3 天）；cyq_perf 20261102 | 【实测】8091 stats 07:53；`docs/ops/2026-11-04_L2预检补充_v1.md:20`；`deliverables/2026-11-04_研发计划复盘_v1.md:54-66` |
| C4 | **8091 单点/会话内存** | 未收口：SessionStore 仍内存（TTL 1h）、持久化代码默认关、无受监督服务/健康门；「7 天无人工重启」目标未达 | `docs/ops/2026-11-03_8091_stability_plan_v1.md:7-18,184-187`；`docs/ops/2026-11-03_系统问题体检与优化方案_v1.md:26-41` |
| C5 | **Guba 反爬** | 未收口：11-04 02:28 小批量 5/5 失败；需 24h 冷却/换会话/代理（L2） | A6 证据 |
| C6 | **异地备份** | 未收口：offsite rc=1（网络变更）；本地 backup/check rc=0、repo 8.61GB | A16 证据 |
| C7 | **凭据轮换** | 未收口：A3-01 已裁定「接受风险」（Tushare token 已暴露未轮换）；B-09 SECVAULT Phase2 待老田/维护窗口；UI-UNIFY-TOKENS `scan_secrets` 6 HIGH_ENTROPY 误报 rc=1 未修 | `docs/ops/BLOCKERS_等老板清单.md:20,97`；`docs/audit/2026-11-04_W1_RESOLVER_与W0候选验收_v1.md:249` |
| C8 | **开源/数据暴露审批** | 未收口：注册暴露审批包（11-03）+数据暴露 Top50 审批表（11-04）待批；nginx json 泄露面（B-06）等维护窗口；OS Gate（gitleaks 全历史/filter-repo/许可证）未建 | `deliverables/2026-11-03_注册暴露审批包.md`、`deliverables/2026-11-04_数据暴露Top50审批表_v2.csv`；`docs/ops/BLOCKERS_等老板清单.md:55-60`；`docs/plans/2026-11-04_造物炉_研发计划_v1.md:259` |
| C9 | **ACAD1 11-08 冻结** | NO_GO：13 卡 **0/13** 人工复核、**20 blockers**、manifest `validated=false`、检查器 rc=2；冻结工具**未入 master**；并存 12/25/30/13 四套卡口径 | `docs/audit/2026-11-04_ACAD1冻结准备_v1.md:6,42,108-117` |
| C10 | **HZ 最终验收** | 待签：最终最早 11-10 22:15；中期≠最终，Go/No-Go 待老田 L3；07:47 六条 6/6 为人工复查，系统三任务未跑 | `docs/ops/2026-11-04_项目复检_v1.md:41-44`；`STATUS-A:4212-4218` |
| C11 | **W1 口径自相矛盾** | 03:28 审计：resolver **7 warnings→strict_fail=7、rc=1**，composition `validate --all --strict` **rc=1**；04:2x 试点：**131 refs/0 warning/rc=0**、composition 6/6 strict PASS。两文档同晨冲突，需 ARCH1 定稿 | `docs/ops/2026-11-04_W1审计与WIP治理告警_v1.md:15,20`；`docs/audit/2026-11-04_W1双周试点_v1.md:14-19,80-88` |
| C12 | **主树脏/配置未提交** | `portal/nginx.conf` **M（未提交，57,153B/11-04 04:22）**，A6 补丁基线因此漂移；会话称「主区 nginx +16 行安全段未提交」 | 【实测】`git status --porcelain portal/nginx.conf` 07:53；`STATUS-A:4203,4208`；`docs/ops/2026-11-04_L2预检补充_v1.md:22` |
| C13 | **A2A/MCP 契约未闭合** | A2A cards 仍 planned、`/api/a2a/*` 未实现；MCP registry 未与 `~/.hermes/config.yaml` 运行时对账；Hermes 2 MCP 失败待 L2 | `docs/audit/2026-11-03_W3代码审查与BUG修复_v1.md:26-33`；`docs/plans/2026-11-03_整体研发方案_v2_最优执行版.md:346` |
| C14 | **学习/量化工程债务** | 学习线 7 个陈旧测试失败+ruff 59 条；量化子项目 109 changed/66 untracked；W2 8 因子 0 通过、策略库 0 approved | `deliverables/2026-11-04_代码审查与项目复盘_v1.md:36-42`；`deliverables/2026-11-04_项目复盘总结_v1.md:91-99,132` |
| C15 | **单一事实源完整性** | `STATUS-B` 6,379 行/752,693B 含 **1 个 NUL 字节**（其自身 L6379 记录）；默认编码读取呈乱码【实测】；并行追加有覆盖风险 | 【实测】`[IO.File]::ReadAllLines(UTF8)` 正常 vs `Get-Content` 乱码；`STATUS-B:6379` |

---

## D. 待拍板 / 等老板（按优先级）

| 优先级 | # | 事项 | 建议/选项 | 证据 |
|---|---|---|---|---|
| **P0 今日** | D1 | **HZ 11-04 终版签收**：10:00 runner 12 步+finalize 后由老田 L3 定 Go/No-Go；不得把 07:47 中期 6/6 当最终 PASS | 按实际结果签收；最终最早 11-10 22:15 | `docs/ops/2026-11-04_当日执行Runbook_v1.md:17-20`；`STATUS-A:4212-4218` |
| **P0 今日** | D2 | **ACAD1 S1–S5 口径**（11-05 12:00 前）：冻结集合=13 review 卡、4 套口径取一、卡号规范 | A0 决策单已落，按推荐签 | `docs/audit/2026-11-04_ACAD1冻结准备_v1.md:42`；`docs/ops/2026-11-04_ACAD1冻结_A0决策单_v1.md` |
| **P0 明日** | D3 | **11-05 05:30 L2 窗口 A1–A6 逐项五要素**：A6 nginx 补丁基线漂移→执行或顺延；A5 JEV 注册需 `-A0Approval`；A4 TDX stale 属外部阻塞 | 逐项确认，不代批、沉默≠同意 | `docs/ops/2026-11-04_L2预检补充_v1.md:22,27-31`；`docs/ops/2026-11-04_当日执行Runbook_v1.md:51-54` |
| **P1 本周** | D4 | **8091 受监督服务+会话持久化 L2**（P0b/P0c，11-08 前）+ **q2-auditor 只读验收身份** + **Evidence Contract v1**（先 warn 两周→阻断） | 三项一起批，否则 Q2/验收链仍不可用 | `docs/plans/2026-11-03_整体研发方案_v2_最优执行版.md:424-427`；`docs/ops/2026-11-03_8091_stability_plan_v1.md:184-187` |
| **P1 本周** | D5 | **Guba 处置**：暂停后续窗口 24h/换会话/评估代理（L2） | 建议先冷却+单只 smoke 验证 | A6 证据 `:25-30` |
| **P1 本周** | D6 | **异地备份补跑**：主机网络恢复后立即 OFFSITE_COPY；考虑第二异地 | 网络变更已致断链 1 天+ | A16 证据 |
| **P1** | D7 | **B-03 MP 凭证**（等外部）；**B-06 nginx json 泄露面**（等维护窗口，加 json 拦截+/static/ ^~ 豁免） | B-06 建议 22:00 后 10 分钟窗口 | `docs/ops/BLOCKERS_等老板清单.md:14,55-60` |
| **P1** | D8 | **凭据轮换**：A3-01 已接受风险但需定期复评；SECVAULT Phase2 生产代码仍直读明文凭据 | 排维护窗口，先 SOPS 收编 | `docs/ops/BLOCKERS_等老板清单.md:20,96-97` |
| **P2** | D9 | **造物炉 D1–D8**（E3 范围/开源范围/许可证/预算/是否接 OpenHands/公开仓/独立评审模型/门锁优先级） | 11-06 前无异议即按默认执行并留痕 | `STATUS-B:6343,6359` |
| **P2** | D10 | **组织调整请求**（5–6 活跃+独立 QA+OPS 交叉；ARCH1 管理幅度）+ 5 作战单元名单/77 席归并 | ARCH1/A0 批；不改 org 真源 | `STATUS-A:4192-4197`；`docs/plans/2026-11-03_整体研发方案_v2_最优执行版.md:422` |
| **P2** | D11 | **11-16 前冻结新增能力**（页面/军团/组合/入口/技术栈）+ 六军团 Q2 owner 签字 0/6 | 冻结清单已列，A0 批后生效 | `docs/plans/2026-11-03_整体研发方案_v2_最优执行版.md:423`；`docs/ops/2026-11-04_W1审计与WIP治理告警_v1.md:17` |
| **P2** | D12 | **商业 OS P1 延至 G4 后**；Hermes 13/13 与三项自愈 L2 | 不批则稀释主线/13 不达 | `docs/plans/2026-11-03_整体研发方案_v2_最优执行版.md:425,431` |

---

## E. 运行态实测快照（2026-11-04 07:51–07:55 +08:00，全部【实测】）

| 项 | 实测值 |
|---|---|
| 8734 唯一入口 | `/` 200（68ms/8,879B）、`/hq` 200、`/dev_legion_v2.html` 200 |
| 8091 | `/health` 200（pid **42848**）；`/api/health` 200（7 服务：portal api/push hub 8097/hermes 8787/gate 9101/review 9102/qd bridge 9103/card retired） |
| 8091 数据 | `stats_version=v2`；catalog **v39/256 表**（06:24:56）vs registry **physical 304/display 258**；总 314 数据集/registered 265/exposed 72；PG **84/84** available；contract_artifacts 4（**mismatch 3**）；`data_date=2026-11-02` |
| 8787 Hermes | `/health` 200 `status=ok`；uptime 10,311s（约 05:01 启动），98 tools/11 active（文档称） |
| 8796 LegionOps | `/api/hq/meeting/list` 200（pid 32308） |
| 8105 智策 | `/` 200（54B）；`/docs` 200、`/openapi.json` 200（240KB）；`/health` **404**（健康端点非 /health，勿据此判死） |
| 其它端口 | 8080/8093/8096/8898 均 200；8103/8771/5446 LISTEN；**5175/8102 已关（L1 目标）**；**8789 盯盘控制台未启** |
| 计划任务 | A0-Daily-Board **Running**（07:50 首跑，0x41301）；A0-Verify next 08:05；HZ Preflight 09:30/Runner 10:00/Midterm 10:30 Ready 未跑；ResticTrialDaily last 02:20 **rc=1**；GubaCrawl last 11-03 17:50 **rc=2**；TdxSync **Disabled**（last 0x41306）；KgDailySync rc=0（06:20）、DW-Daily rc=0（06:30）、JevFeed rc=0（21:00）、JevDrift rc=0（07:10）、ZhiceAcad1Pool rc=0（03:15）、LegionOps rc=0；LegionGateway8795 rc=1 |
| 仓库 | master `08fac205f`（07:45:53）；`git worktree list`=**58**；`git status --porcelain`=**253** 项；`portal/nginx.conf`=**M 未提交** |
| 磁盘 | C: 已用 **737.7GB** / 可用 **1156.9GB**（约 38.9% 使用） |
| 备份 | 11-04 02:20：backup rc=0 / check rc=0 / forget rc=0 / **offsite rc=1**；本地 repo **8.61GB** |
| 首跑产物 | `deliverables/daily_briefs/2026-11-04/` 07:54 尚未生成（跑板仍在执行） |
| L1/验收 | L1 **18/18 PASS**（文档 07:15/07:21）；ACAD1 检查器 rc=2；HZ 系统任务未跑 |
| 边界 | 未重启/未 reload/未注册任务/未改配置/未碰 PG；未跑 L2 |

---

## F. 阻断「产品化」风险 Top10（按会不会让产品失败排序）

| # | 风险 | 为什么阻断产品化 | 缓解建议（最小可执行） |
|---|---|---|---|
| F1 | **产品价值未证：策略库 0 approved、W2 8 因子 0 通过、B 级代理全不通过、百炼评测 4/6** | 量化产品没有可上线 alpha；对外只能卖"平台"，卖不了"能力" | 收窄到情报决策闭环/软件交付闭环两旗舰；停止批量挖因子；负结果如实写进对外材料 |
| F2 | **门禁/验收可信度仍有缺口（H6 未修+同模型自评+auto≠PASS+计划任务绿色掩盖）** | 无法对外承诺"可信交付/可审计"；开源自动化更不可承诺 | H6 OS 沙箱进治理道单独立项；跨厂商 reviewer M2 前上线；Evidence Contract v1 先 warn→阻断；Q2 只认 owner 签字 |
| F3 | **数据口径/单一真源未闭合（catalog 304 vs 256、W1 7 vs 0、4 套卡口径、contract 3 mismatch）** | 所有面板/验收/对外数字可被质疑，客户一问就穿 | 11-05 三分类收口；DATA1 定 catalog 真源并 red 告警；口径对账进硬门；W1 口径由 ARCH1 定稿 |
| F4 | **WIP 超限+主树脏（58 worktree/253 dirty/doing 41–48）** | clean checkout 丢改动，交付不可重复，合并窗口持续被堵 | 11-05 收口到 ≤8/≤20；暂停新开 worktree；prune+归档；脏文件属主收口 |
| F5 | **数据链 stale/断更（TDX 三表 stale 3 天、Guba 100% fail、cyq T+1、inventory 停在 11-02）** | "数据实时"卖点不实，研究/回测不可复现 | freshness SLO+红警；数据缺口标"截至"且禁入信号；采集多窗口/退避/代理 |
| F6 | **8091 单点+内存会话+无监督服务** | 门户/验收/自动化随时中断；SLO 不成立 | L2 P0b/P0c（受监督+持久化+健康门）；7 天无人工重启观察；蓝绿/回滚预案 |
| F7 | **硬节点交付风险（ACAD1 11-08 NO_GO；HZ 最终 11-10；W1 11-16 口径矛盾）** | 产品第一批卡/验收节点失守，路线图信用受损 | ACAD1 11-05 定口径+人工复核登记+工具入 master；HZ 按 runner 实签；W1 先解 7 warnings |
| F8 | **安全/暴露/合规（H6、nginx json 泄露面、凭据未轮换、数据暴露 Top50 待批、开源 OS Gate 未建）** | 商业化/开源前置不成立；一次泄露即终止 | 维护窗口修 json+no-follow；凭据轮换排期；暴露审批先批后开；开源先 OS-0/1 内部 |
| F9 | **容灾断链（异地备份 offsite rc=1）** | 运营数据无异地副本，事故不可恢复 | 网络恢复即补跑；每周 check+失败告警；评估第二异地 |
| F10 | **组织/产能/计划通胀（77 席 vs 有效 ~5 人；Q2 owner 0/6；285 计划仅 1.4% 有 STATUS）** | 产品化退化为"文档化"，无人对结果负责 | 5 单元+Q2 绑单元；周报只报缺口；新计划必须挂 ROADMAP v4 并 24h 标 STATUS |

---

## G. Top15 审计/运行必读 + 方法与覆盖

| # | 必读 | 理由 |
|---|---|---|
| 1 | `docs/plans/2026-11-02_ROADMAP_v4_唯一战略与主排期.md` | 唯一战略+排期源；§八 R1–R6 风险、§一基线、§十一执行状态 |
| 2 | `docs/plans/2026-11-03_整体研发方案_v2_最优执行版.md` | 执行层唯一方案；§二 R1–R6 根因、§十三 R1–R12 风险、§十六 12 项待拍板 |
| 3 | `docs/audit/2026-10-31_全项目代码审计复盘与开源方案补齐_v2.md` | H1–H9 / M1–M10 安全与门禁主清单（H5/H6/H7 出处） |
| 4 | `docs/audit/2026-11-01_P0审计修复落地回执_v1.md` | merged/deployed/verified 三态矩阵 + 假 done 治理事故 |
| 5 | `docs/audit/2026-11-04_代码审查与项目复盘_v1.md` | 最新代码基线 3669 passed/0F 与 4 个系统性风险、5 条教训 |
| 6 | `docs/audit/2026-11-02_AI助手全项目复盘与框架结合卡点_v1.md` | 存量×ZBF 卡点 P0(6)/P1(10)、12 类资产结合度（55–60% 直接纳管） |
| 7 | `docs/ops/BLOCKERS_等老板清单.md` | B-01..B-10 集中阻塞账（注意正文最后更新时间 10-27，需与 11 月单合并读） |
| 8 | `docs/ops/2026-11-02_A0待签批清单_v1.md` | 「一次签完」范式；L2/ADR/迁移四项拍板记录与签批状态 |
| 9 | `docs/ops/2026-11-03_系统问题体检与优化方案_v1.md` | 8091 单点/验收身份/Q2 语义假绿/W3 范围四大问题与路线 |
| 10 | `docs/ops/2026-11-04_L2预检补充_v1.md` | A1–A7 逐项现状、A2/A6 假阻塞与补丁基线漂移（最新运行态 L2 依据） |
| 11 | `docs/ops/2026-11-04_项目复检_v1.md` | 07:15 全量体检（服务/任务/L1/W0/HZ/ACAD1）+ catalog P0 |
| 12 | `docs/audit/2026-11-04_ACAD1冻结准备_v1.md` | 13 卡逐卡证据/20 blockers/4 套口径，11-08 硬节点唯一依据 |
| 13 | `docs/ops/2026-11-04_W1审计与WIP治理告警_v1.md` | W1 9 gaps 与 WIP 双超限告警（与试点报告存在口径冲突，需对照读） |
| 14 | `docs/ops/2026-11-03_8091_stability_plan_v1.md` | 8091 三脆弱点+三套受监督方案+L2 五要素全文（唯一可执行 L2 包） |
| 15 | `STATUS-A:4112-4218` + `STATUS-B:6319-6379` | 两块板最新段：11-04 07:00–07:58 的 HZ/合并/造物炉/知识图谱/盯盘实时结论 |

**方法与覆盖**
- 索引：docs/audit 268、docs/ops 294、docs/plans 411（实测目录计数）；按文件名日期筛 10-28 之后候选；两板取尾段（STATUS-A 4,218 行取 4112–4218；STATUS-B 6,379 行取 6319–6379）；根 deliverables 按「复盘/审计/事故/风险/失败/回执」筛 11-01~11-04。
- 精读/部分精读 **35 份**：docs/audit 13、docs/ops 14、根 deliverables 6、plans 2 节选；另实测 13 个 HTTP 端点、25 个计划任务、git worktree/status/log、8091 stats、restic receipt、磁盘。
- 【实测】与文档称已分列；所有数字均来自上表证据行，未编造。未覆盖/待核：docs/ops 部分 11-02/11-03 长回执未逐行；`ZeroBound\TdxSync_*` 中文任务名未逐个取 LastResult；8105 健康口径（非 /health）；8103 进程启动时间因 S4U 不可读，H7 加载状态仅文档称。
