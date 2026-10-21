# GOV-MIN-003｜高风险路径调用链静态附录（2026-10-18）

> 范围：以 `path-migration-matrix-2026-10-18.md:34-36,113-137,149-150,173-176` 的 H/R+ 风险为入口，定点审阅 Git 快照、TTL 删除、TushareSync 部署、K265 图谱删写与 K266 收件箱写移。另抽样核对回测引擎与快照 CLI 的写根。仅阅读源码与矩阵；未执行脚本、查询计划任务/注册表/ACL/图数据库，未读取账本、JSONL/parquet 数据内容，未移动/删除原有文件。此附录**不是全仓搜索或穷尽清单**；所有迁移、部署、清理、入图、生产试跑均维持 **BLOCKED**。
>
> 引用写法均为本仓相对源码路径与源码行号；“直接证据”只指静态语句，不证明运行态挂载、磁盘存在或 owner 授权；“假设/待证”单列。矩阵中的 `R+` 要求变更前目录/文件哈希、写者归属及写入侧独立回滚；不能只回退源码（矩阵 `:9-13`）。

## 1. Git 自动快照：跨 Agent 广域暂存/提交

- **直接证据 / 静态调用链**：`portal/agent_auto_commit.py:233-234` CLI → `main():140` → `LOG_DIR.mkdir(:143)` → `git("status", "--porcelain")(:148)` → `git("add", "-A", "--", *EXCLUDE_PATHSPECS)(:159-168)` → 暂存差异/体积/秘密守卫 `:175-193` → `commit_with_retry(:198-200)` → `git("commit",...)(:64-76)`；Git 命令固定 `git -C REPO`，`REPO=E:/老田的AI助手`（`:29,54-61`）。状态完成分支 `finish(:223-230)`。`git reset -q` 守卫分支在 `:181-193`，会变动暂存区，而不是仅撤销本轮 add 的路径。
- **潜在写目标**：所选 Git 仓库的 index/对象/refs 与全部未被忽略且未被排除的文件变更历史，含删除项；即使空跑/失败，`E:/老田的AI助手/portal/logs/` 创建、`agent_commit_status.json` 覆写及 `auto_commit.log` append 可发生（`:30-32,143,225-227`）。`.gitignore` 具体生效与目标 repo 归属未核实，不将其视为证明。
- **已有保护及局限**：明确 pathspec 排除 MEMORY、几类演练账本、`work/tmp*`（`:40-51`）；新增文件数/体积上限（`:35-38,116-137`），敏感文件名/暂存 diff 正则（`:84-113`，diff 限 8,000,000 字符），锁冲突重试（`:33,64-76,157-168`）。体积保护只计新增文件，旧文件改写/删除及截断后秘密信息不能由此覆盖；已预先暂存的内容也参与 `diff --cached`/commit，且 `git reset -q` 可影响别人的暂存工作。以上为代码推导风险，**不是**已发生事故。注释“定时调用”（`:3-17`）不是调度器注册证据。
- **待证 / owner / 回滚前提**：Git/门户运维 owner 给出实际调度条目及动作、服务身份、`E:` 实际仓映射与排除清单评审、运行期间其他 Git 写者隔离方案、变更前 index/HEAD/工作区与目标文件的只读快照和哈希；经离线复现验证守卫、人工批准冻结提交窗口。误提交后的处置须保留原 commit 与证据，按仓库协作策略决定 revert/恢复暂存及下游传播处理，不能以强制 reset 或删除历史代替回滚。**BLOCKED**。

## 2. TTL 清理：可递归删除文件及空目录

- **直接证据 / 静态调用链**：`tools/disk_ttl_cleanup.py:129-130` CLI → `main():64` 解析 `--target`（必填、可重复）、`--days`（必填整数）、`--execute`（`:65-70`）→ `(REPO/t).resolve()` + 根/越界/`.git` 检查（`:75-81`）→ `collect_expired(:33-45,83-91)` 依据 `stat().st_mtime < cutoff` 选文件 → 无 `--execute` 于 `:93-95` 返回；否则输入严格 `EXECUTE`（`:101-105`）→ `f.unlink(:107-115)` → `remove_empty_dirs(:48-61,116)` 的 `rmdir` → 日志目录创建/追加（`:120-125`）。仓根固定 `E:/老田的AI助手`（`:29`）。
- **潜在写/删目标**：调用者指定的任一仓内相对目录下过期文件、空子目录；`E:/老田的AI助手/portal/logs/disk_cleanup.log` append（`:30,121-123`）。示例 `quant_backtest/data` 只是注释 `:11-14`，**没有固定白名单**；`--days` 未校验为正数，负数可能使新文件满足阈值；验证基于入口路径 `.resolve()`，收集与删除之间无逐文件再次约束/二次 stat，路径/mtime 并发变化的风险是静态推导，未证实利用。
- **已有保护及局限**：默认 dry-run、目标须在解析后仓根之内且不得为根/含 `.git`（`:77-80,93-95`），真删需要 flag 和交互确认（`:101-105`），删除单文件 OSError 被捕获（`:112-115`）。未证明目标限于运行产物、不覆盖源码/账本或目录存在性的生产状况；磁盘删除不是 Git 回滚可以完全弥补的操作。
- **待证 / owner / 回滚前提**：磁盘/数据产线 owner 对每个 `--target` 的写者、保留策略、是否跨链接/挂载与备份责任签字；只读枚举和目标清单/哈希、TTL 与时区/时间戳口径、并发冻结窗口、可恢复备份及恢复演练；双人批准具体删除清单后才可讨论执行。**BLOCKED**；本审计未作 dry-run。

## 3. TushareSync 部署：注册表、SYSTEM 任务、launcher 回写与 ACL

- **直接证据 / 静态调用链**：`quant_backtest/deploy_tushare_sync.ps1:11-23` 从同目录读 launcher 并 base64 拆分 → `:25-28` 写 `HKLM:\SOFTWARE\TushareSync\Deploy` 的 `part1/part2` → `:31-43` 构建任务 action；任务内部先从注册表恢复 `$s`（`:33-35`），仅扫描 `E:\` 直系子目录找 marker（`:36-37`），回写 `$root\quant_backtest\start_tushare_sync.ps1`（`:38`），捕获异常 append `C:\Temp\tushare_diag.log`（`:39`），随后动态 dot-source `$s`、重定向 `C:\Temp\tushare_run.log`（`:40`）。部署自身 `:45-102` 组 XML UTC 每日 15:30 触发、`S-1-5-18` / Highest（`:69-73`）、可按需启动和失败重启（`:75-90`），先注销同名任务再注册（`:99-102`）；`:104-119` 建 `C:\Temp` 并再次扫描 `E:\`，若 ACL 修复脚本存在即调用 `-Path $root -Fix`。**未阅读/执行 ACL 被调脚本，因此不推断其具体授权差异。**
- **潜在写目标及关键非等价性**：HKLM 注册表键值、任务 `TushareSync` 的系统任务定义、E 盘找到的 launcher、`C:\Temp` 日志，以及由 source 动态运行的同步任务写入范围；`start_tushare_sync.ps1:6-19` 独立运行时优先 `C:\aios`、才回退扫描 `E:\`，`:20-34` 写日志及执行数据同步。但**部署 action 的 E 盘回写发生在 launcher 的 C 盘优先逻辑之前**；它们不是同一选择器；如果 E 盘未命中，捕获错误后 `$s` 仍可能被 dot-source（`:33-40`），不能把部署视为安全读操作。ACL `-Fix` 可能更改权限，具体范围待脚本和现场授权核对。
- **已有保护及局限**：部署缺 launcher 即抛错（`:14-16`）；任务 action 未找到 E 根会抛错并记诊断日志（`:36-40`）；ACL 脚本不存在时跳过（`:112-118`）。这些不等于预备份任务 XML/注册表，也不保证 SYSTEM 可访问目标、任务运行时间正确或 E/C 路径一致。`:127-130` 仅输出验证/人工启动提示，不构成已经执行证明。
- **待证 / owner / 回滚前提**：任务/Windows 权限 owner 提供只读导出的已注册任务 XML、动作/触发/身份与现有运行日志摘要、注册表原值、目标 launcher 哈希、junction/目录映射、ACL 前后差异及 `check-system-acl.ps1` 参数语义；数据同步 owner 界定实际数据写范围/备份/幂等性。独立审批生产窗口、任务停写隔离、恢复原 XML+注册表+ACL+launcher 与同步数据的演练，且不中途丢失历史。**BLOCKED**；未查询任务/注册表/ACL。

## 4. K265：影子文件与 Neo4j 图删写的不同门禁

- **影子写直接证据**：`quant_backtest/k265_phase0.py:164-165` CLI → `main():109` 读旧 E 盘 notice parquet（`:110`）、查询 KG 字典（`:17-18,120-121`）→ `open(OUT,"w")(:20,125)` 覆写 `.../data/semantica/k265_phase0_shadow.jsonl`、循环写/flush（`:126-145`）。`k265_phase1.py:176-177` CLI → `main():105` 读图谱概念（`:111-115`）和旧影子已有 pair（`:132-143`）→ `open(OUT,"a")(:20,147)` append `k265_phase1_shadow.jsonl`（`:148-154`），分片参数是 `--from/--count`；没有入图动作。前者覆盖而后者追加，回滚策略不能混同。
- **图写直接证据**：`k265_phase1_commit.py:123-124` CLI → `main():50` → KG 品牌名单/影子读取（`:71-76`）→ `ok` + `conf>=min_conf`（默认 .8）及人工 exclude、品牌概念过滤（`:56-69,77-93`）→ `KgClient._session`（`:99-100`）→ `MATCH Concept` / `MERGE (a)-[r:<e["edge"]>]->(b)` / `SET r.source='concept_llm_v1', r.conf/reason/overlap/batch`（`:102-108`），再读计数（`:116-120`）。`kg_client.py:26-40,62-63` 表明连接参数可默认取 `kg_schema` 配置并创建 Neo4j session；具体生产库/身份未从静态代码证明。MERGE 后 SET 可**覆盖既有同类型同端点边属性**，`source` 不是独占性证明；代码没有可逆备份/人工批准检测，未知 `edge` 值还会进入字符串格式化的 Cypher 关系类型，应离线验证输入约束。
- **图删除直接证据**：`k265_rj_cleanup.py` 在**模块顶层**读取 exclude/影子、查询图（`:11-50`），构建只针对 `batch='rj1'` 且不在 pv2 白名单的 `kill`（`:23-56`）；只在 `kill` 非空且命令行含 `--go` 时调用 `sess.run(... DELETE r)`（`:56-60`）。删除 Cypher 按 `Concept` 两端名称、动态关系类型和 `source='concept_llm_v1'` 匹配，**没有在 DELETE 查询中约束 `r.batch='rj1'`**（`:58-60`）：若同类型同端点/source 存在其它 batch 边，删域可能超出预览 `kill` 的语义；必须用离线样本/只读查询证明，并补严删除谓词后再议。未带 `--go` 也会读 KG/文件，绝不可拿来本审计“试跑”。`k265_rj_close.py:58-69` 另将待审清单 `k265_ingest_pending.txt` 以 `w` 覆写；它不是图删写授权。
- **保护与待证**：`commit` 有阈值/品牌/可选排除，但默认无强制人工清单；cleanup 有 pv2/conf≥.95/剔除及 `--go`，但无备份及删除查询 batch 保护。KG/data owner 需确认 URI/库/账号与图关系唯一性、影子/排除文件归属和哈希、图关系**逐边导出及属性前像**、batch 一致性、变更窗口内其余写者、离线样例及人工复核；必须先证明完整恢复既有边/属性，不可仅靠 `source` 批量反向删除（可能伤及原有同源边）。矩阵 archive 版本 `:128-130` 是否运行未证，不能视作已停用。**全部 BLOCKED**。

## 5. K266：多个 pending 生产者与非事务性搬运/登记

- **直接证据 / 来源链**：`quant_backtest/k266_clip_watch.py:46-47` → `main():16` 创建旧 E `inbox/docs/pending`（`:12,18`），循环读取剪贴板图像并写 `clip_<秒>.png`（`:25-38`）；仅字节级去重及 >2048 B 门槛，文件名秒粒度，非跨进程独占。`k266_seed_reports.py:24-55` 把外部取得且有 PDF magic 的报告写同一 pending `rp_<infoCode>.pdf`（`:10,38-47`）；网络/内容可信性不在本静态审计保证。`portal/portal_api.py:2531-2559` POST `/kg/doc/upload` 参数清洗和扩展名白名单、空 body 判断后，以 `STATION/inbox/docs/pending/up_<秒>_<name>` 写字节，**该根由 STATION 派生而非 K266 固定 E 根**；不证明二者实际上指向同一目录。HTTP 入口的其他鉴权/容量边界未在本定点审查中完整核验。
- **摄取链及目标**：`k266_doc_ingest.py:176-177` → `main():153-173` 创建 `E:/老田的AI助手/inbox/docs/{pending,done,reject}` 与 `E:/老田的AI助手/quant_backtest/data/k266_docs`（`:25-28,157-159`）→ `scan_once(:132-150)` 遍历 pending 并按文件名前缀标记来源 → `process(:88-129)` 计算 hash、读取 registry 决定去重；重复/不支持/异常直接 `shutil.move` 到 reject（`:91-116`）；PDF OCR 分支 `REG/_page.png` 创建、复用及删除（`:61-76`）；成功先 append `doc_texts.jsonl`（含全文），再 append `registry.jsonl`，最后 move 到 done（`:118-126`）。`--once` 一次处理、`--loop` 持续处理（`:153-173`）。这些 append 与 move 无共享事务；中途故障可留下文本/registry 与 inbox 状态不一致，这是静态推导，未验证现场数据。
- **保护与待证**：按 SHA256 已登记去重、类型分流和异常转 reject（`:88-116`）；PDF OCR 最多前 8 页（`:70`），但不是数据完整性证明。portal 使用 STATION 根而守护使用旧 E 固定根，需 portal/KG/新闻采集 owner 共同给出两根的只读解析/同一性、服务写者及后台守护调度、命名碰撞策略、原件与 registry/doc_texts 的目录清单和哈希、涉及隐私与保留期限、失败状态修复规则。先冻结所有投递者，备份 pending/done/reject 原件及两本登记文件，验证文件搬移与登记一致性的离线恢复演练；不能只把文件搬回 pending 后盲重放，以免重复/覆盖。**BLOCKED**；未读取登记或文档内容。

## 6. 同矩阵另外两条 H/R+ 的定点边界（非主路径）

- `quant_backtest/backtest_engine_v2.py:28-33` 旧 E 根派生输入与 `OUT_DIR/MINUTE_OUT_DIR`；`:12` 声明 `run_job` 写结果。`bt2_tune_compare.py:18-43` 仅在该模块 import 路径上当 `eng.BASE` 目录不存在才改引擎变量；不能外推到引擎 CLI/其他导入者，也不能据注释认定数据完整。回测/数据 owner 需逐入口、输出文件前像/哈希、并发写者和离线比对，保持 BLOCKED；本附录不读账本。
- `tools/rebuild_academy_source_snapshot.py:293-306` CLI 的 `--root` 默认旧 E，同时 `--output` 为必填；`:287-289` 明确写候选 snapshot 与 manifest JSON。必须由源数据/评估 owner 证明 `--root` 只读域、`--output` 归属、旧输出备份与 candidate-only 非训练/生产资格；迁移与执行保持 BLOCKED。

## 自检与交接

1. 本附录主审 **5 类高风险链**（Git、TTL、任务/ACL、K265、K266），另 **2 条**非主路径抽样；引用的每个 `file:line` 均针对实际阅读源码定位。该计数不是矩阵 H/R+ 总数、不是全仓路径总数，也不扩展矩阵的 124 个审阅条目口径。
2. 静态证据可说明“代码会怎样”而不能说明“现在由谁、在哪个盘、对哪个实例执行”；任务 XML/注册表/ACL、junction、实际数据和账本内容均无现场证据。任何疑似不一致须由各 owner 提供只读证据及审批，不授权运行或迁移。
3. 文档变更仅为此新增附录；没有修改源码或现有矩阵、没有提交。实施状态保持 **BLOCKED**，所需通用前提是 owner 签名、影响域与哈希、只读基线、离线测试、生产只读探测、写者隔离、独立数据回滚演练、人工变更批准。
