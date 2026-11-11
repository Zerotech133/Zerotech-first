# 上线发布方案（v1.3 整合定稿）· AIOS 本地数据资产与计划任务治理子系统

> **文件**：W-12 `C:/aios/docs/plans/20261105_release_plan.md`（即 `C:\aios\docs\plans\20261105_release_plan.md`）——Phase 3 唯一落盘产物（GOV-W 终表 W-01~W-14 由团长与各成员分别落盘）
> **版本**：v1.3 ＝ v1.0 全文 + v1.1 / v1.2 / 收口索引 / 增量更正 / 团长形式终裁（ADR-16~20）合并后的**单一全文**；此前所有消息版本不再作为执行依据
> **编制**：上线发布师 release-manager（Phase 3）｜团队 `aios-governance-forge`｜团长形式终裁见 §11
> **性质**：**发布作业指导书**，供团长单写者实际落盘时逐步执行。**本文件是方案文本，文档内所有「发布 / 回滚」命令均为作业指令，不得在编制阶段实际执行**
> **依据**：`release-deployment.md`（全章核读）；项目档案（ADR-01~09、ADR-16~20）；`20261105_data_backup_and_task_governance_plan.md`（§1.3 / 阶段 0·2 / §四 D0~D6 / §六 M1~M6）；PM 最终版 PRD；qa-tester v1.4；fullstack 代码级语义；ops-sre Phase 4
> **上游**：全栈研发师 [技术研发方案] 部署形态＝候选 A 单机 CLI（ADR-01 锁定）；质量测试官 [测试保障方案] 门禁**设计态 READY（G0-a）**
> **铁律**：只新建 W-12 一个文件；不改任何既有文件；不执行任何系统状态变更（不跑 schtasks / 不新增·修改·启用·禁用任何 Windows 计划任务 / 不 commit / 不碰 `10.205.0.118` / 不执行 `.enc` 解密）；`C:/aios` 与 `C:/项目/老田的AI助手` 为同一 junction（单 inode）；解释器统一 `C:/Program Files/Python311/python.exe -X utf8`（本机无 python3 别名）；PowerShell 5.1 写日志须显式 `-Encoding utf8`（`*>>` 会写 UTF-16LE）
> **命令约定**：下文命令统一 Git Bash；路径一律正斜杠书写（与 Windows 反斜杠等价）；含中文路径的文件操作一律 **Bash + Python**，不经 PowerShell

---

## 0. 发布形态适配声明（先纠偏：这不是云端 / 多端发布）

本机即生产；唯一使用者老田；零新增组件、零新增端、零网络监听、零常驻服务。对 `release-deployment.md` 逐条核对，结论为「不适用 + 给出等价物」——**核对本身不省略，但不套用云端模板**。

### 0.1 部署方案三选一对比结论（手册第二章「结合项目实际选择」）

| 方案 | 结论 | 不适配理由 |
|---|---|---|
| 云服务器 ECS/CVM | ❌ 不选 | 会触碰生产服务器 `10.205.0.118`（硬边界）；11GB 级魔数 / 流式解压 / sha256 与 Windows 计划任务只读盘点必须在宿主完成，上云即数据外送合规问题 |
| Serverless（函数 / 云托管） | ❌ 不选 | 冷启动与长任务受限（单次 dump 830s），违反「零新增组件 / 零网络监听 / 零常驻服务」，无弹性流量可言 |
| 静态托管（OSS+CDN / Vercel 类） | ❌ 不选 | 仅静态前端；本项目是 Python CLI + 本地 JSON 台账 + `atomic_write_json` 写入侧，非静态站点 |
| **实际选择：单写者本机就地落盘（In-place Local Publish）** | ✅ **选定（第四类）** | 制品＝新建文件本身；部署目标＝本机 junction 工作区；唯一环境即生产；零分发、零切流、零回滚流量；回滚＝逐文件进回收站 |

**环境划分（手册「必备四套」的本机等价，卡点一个不省）**：压缩为「**一环境 + 三层验证前移**」——L0 草稿自检（不落盘）→ L1 夹具单测（合成夹具 + pytest，等价开发 / 测试）→ L2 FREE 路径只读演练（`apply=False` / `--dry` / 只读子命令，等价预发）→ **生产 ＝ `C:/aios` 同一 inode**。
手册四大卡点全部保留：① 门禁不过禁止流入下一环节（见 §2 stage gate）；② **制品唯一**：生产运行的文件必须与发布单登记的 sha256 逐字一致，禁止落盘后临时改内容（改则重走门禁并更新发布单哈希），禁止「本地改完直传生产」；③ 版本号 `主.次.修订` + 可追溯（版本号 + 逐文件哈希 + 落盘时间戳；因不提交 git，不依赖 commit）；④ **回滚制品保留**：发布前哈希基线快照 + 25+ 既有 modified 文件状态快照存入发布单，随时可比对回退。
配置与密钥隔离：`~/.backup_aes_key` 与飞书 webhook 属生产配置，**不进代码库、不进制品、不进日志、不进飞书正文**；夹具侧只用合成样本，不读真实密钥。

### 0.2 各端发布与审核逐条核对（手册第三章，结论：全部 N/A）

| 端 | 核对结论 | 周期 / 常见被拒 / 所需资质 | 本项目等价物 |
|---|---|---|---|
| App Store（iOS） | **N/A**：零新增端、无分发、无 Apple Developer 账号 | 首审 1~7 天、被拒重排队、隐私清单 / IAP / 截图 / 演示账号 → 均不适用 | Bundle ID 与证书匹配 → 等价「GOV-W 白名单逐字核验」 |
| 华为应用市场（鸿蒙 / 安卓） | **N/A**：无 AGC 项目、无签名包、无渠道分发 | 软著 / 承诺函 / 隐私链接 / 元服务规范 → 均不适用 | 签名与加固冲突 → 等价「编码一致性与原子写」 |
| 微信小程序 | **N/A**：无小程序、无类目、无服务器域名 | 类目资质 / 域名备案+HTTPS 白名单 / 1~7 天审核 / 隐私保护指引 → 均不适用 | 服务器域名白名单 → 等价「GOV-W 白名单 + 零新增监听」 |
| 网站 / 后台 | **N/A**：无域名、无 ICP 备案主体、无 HTTPS 站点 | 备案 / 证书 / SEO → 均不适用 | HTTPS 证书有效期 → 等价「AES 密钥零操作 + 泄露检查」 |

⚠️ **审核风险提前告知（禁止等被拒才补救）**：本项目无外部审核方，但存在**内部合规审核**＝L2 批准状态与硬边界。最高风险不是「平台被拒」，而是「**未批准的 D0~D6 阶段 1/3 动作被夹带进发布**」与「**11GB 明文 dump / AES 密钥外泄**」。二者必须在落盘前告知老田与团长，并作为 G6 硬阻断项（§4 / §6）。

### 0.3 灰度策略（手册 4.1；「灰度为默认」）

无灰度用户群（唯一使用者老田），故采用**文件级分批 stage gate 作为等价灰度**：`S1 →（退出判据通过）→ S2 →（通过）→ S3 →（通过）→ S4 →（通过）→ S5 → S6`，每批「先只读演练、再落盘、再单测、再哈希复核」＝分阶段放量；每步失败即暂停后续、就地回退，不累积风险。
**直接全量的理由说明**：本轮是「纯新增只读文件 + 唯一用户 + 零存量状态变更」的冷启动式发布，无用户侧流量可切，故不做按比例灰度；但**分步落盘 + 逐文件可回退必须保留，不得合并为一次性全量写入**。此理由须记入发布单，供首月复盘。

---

## 1. 版本边界与发布范围

| 版本 / 项 | 包含 | 边界 / 状态 |
|---|---|---|
| **v0.1 · CLI 台账** | `ops/task_inventory.py` + 单测 + 合成夹具 | 5 个只读子命令 export / snapshot / diff / stats / suggest-registry；分类 7 值；噪声 RC frozenset {267009, 267011, 2147943140, 1073807364}；`1999-11-30` 从未运行哨兵；`suggest_registry` 硬编码 applied=false、零写 REGISTRY；`atomic_write_json` 用 mkstemp+os.replace；diff.removed 只置软删除标记 |
| **v0.2 · 质量闸门** | `ops/backup_format_gate.py` + 单测 + `INVALID_GZIP.flag` + 三处口径统一声明文档 | `verify_artifact()` 验魔数 1f8b + gzip 流式解压前 8 MiB + sha256；`gate_entry()` 保留既有 manifest 五键只增六键；`should_select()`；`write_quarantine_flag(..., apply=False)` 默认不落盘；I/O 异常一律收敛 ok=false 不抛穿 main |
| **运行时产物** | `ops/data/*.json` | **降级为「运行时产物，本轮不生成」，不再是文件编号**（ADR-20）；不改 `.gitignore`（ADR-09），由全案文档声明 + 老田手工加忽略规则 |
| **v0.3 · 演练扩面** | 不属本轮 | 需 **D0 `.enc` 解密恢复演练 rc=0 为硬前置**，**不得随本次发布启动** |
| **v1.0 · 排班收敛** | 不属本轮 | 需 **D1/D2/D3/D5/D6 的 L2 五要素批准**且 D0 rc=0 硬前置，**不得随本次发布启动** |

### 1.1 本轮唯一有效白名单：GOV-W 终表（W-01~W-14，ADR-20 ＋ 团长补发终表原文）

发布单与 G6③ 核验**一律以团长 GOV-W 终表为准**。所有 F / FF / PF / BG / TI / AC / G / T 编号均为**方案内引用，非白名单依据**（ADR-07 扩展）。以下为团长补发的**终表原文**（由 W-01~W-13 扩员至 **W-01~W-14**）：

| 编号 | 路径 | 编号 | 路径 |
|---|---|---|---|
| W-01 | `ops/task_inventory.py` | W-08 | `backups/db/2026-10-29/INVALID_GZIP.flag` |
| W-02 | `ops/backup_format_gate.py` | W-09 | `docs/changes/20261105_ff06_interface_hooks.md` |
| W-03 | `docs/plans/20261105_data_backup_and_task_governance_plan.md` | W-10 | `docs/changes/20261105_governance_terminology.md` |
| W-04 | `docs/plans/20261105_governance_project_dossier.md` | W-11 | `docs/plans/20261105_governance_full_case.md` |
| W-05 | `tests/test_task_inventory.py` | W-12 | `docs/plans/20261105_release_plan.md`（本文档） |
| W-06 | `tests/test_backup_format_gate.py` | W-13 | `docs/plans/20261105_governance_acceptance_report.md` |
| W-07 | `tests/test_governance_acceptance.py` | W-14 | `docs/plans/20261105_ops_sre_plan.md` |

**补充口径（团长补发，一并写入）**：
1. **W-14 扩员**：ops-sre 的 v1.1 运维全案获团长批准落盘为 W-14，白名单由 W-01~W-13 **同步扩至 W-01~W-14**；G-09 基线脚本 `C:/Temp/governance_zero_state_baseline.py` 的 `GOV_W` 已同步为 **14 条**。
2. **T-10 = A（全量 sha256）** 已由团长正式终裁；G-09 基线**同口径分层**：10-29 四文件全量 sha256 + 全库轻量指纹，**同一对象前后不混用算法**，故不产生哈希漂移误判（见 §3⑤ / §5.3）。
3. **W-08 已落盘**：团长新建，八字段、`files` 聚合 3 个 `2d2d` 坏档、`policy=mark-only, do-not-delete`；`manifest.json` 与三个 `.sql.gz` **零改写**；G-09 verify 已放行 **W-08 为 `backups/db` 下唯一允许新增**。
4. **构建缓存单列审计**：pytest / py_compile 产生的 `__pycache__`、`.pytest_cache`、`*.pyc` 由 G-09 基线脚本**单列审计，不判白名单失败**（见 §4.2 G6③）。
5. **补写约束**：本次仅 Edit W-12 这一个文件，不新建第二个文件、不改任何既有文件、不执行系统修改命令。

**降级项**：`ops/data/*.json` 降级为「运行时产物，本轮不生成」，**不再是文件编号**（ADR-20）。`tests/test_governance_acceptance.py` ＝ **W-07 已显式纳入**，G6③ 不得判白名单外新增（落盘前硬阻断 ③ 已解除）。

**发布侧执行规则**：`git status --porcelain` 新增文件必须 ⊆ GOV-W 终表（W-01~W-14）；任何终表外新增即 G6③ 异常 → 触发 FF05 全量回滚；**例外**：`__pycache__` / `.pytest_cache` / `*.pyc` 由基线脚本单列审计、不判失败。发布单登记白名单时须附本终表原文。夹具一律在 `tempfile.TemporaryDirectory()` 运行时自建、不落盘、不占编号。

### 1.2 硬边界（发布全程不得触碰）

新增 / 修改 / 启用 / 禁用任何 Windows 计划任务（含 DbDumpDaily、CainiaoPull）；删除或改名任何**发布前既有**文件；改写 `backups/db` 下任何既有产物内容；修改 `ops/db_dump_daily.py` 的 DUMPS / KEEP_DAYS、`quant_backtest/miss_check.py` 的 REGISTRY、`tools/b4_restore_drill.py`、`jev-eval/backup_daily.ps1`；执行 `.enc` 解密演练；触碰 `10.205.0.118`。D0~D6 **七项全部未批准**（ADR-04），绝不夹带任何阶段 1/3 动作。

### 1.3 并行作业约束

预检已发现 git 工作区 **25+ 处既有未提交改动**（含 `ops/db_dump_daily.py`、多个 registry / deliverables），说明本机另有并行作业 → 发布**只允许新建 GOV-W 终表内文件，不触碰任何已 modified 文件**；**不执行 `git add` / `git commit` / `git checkout --`**（避免把别人的改动一起提交或回退）。

---

## 2. 发布清单与顺序 ＋ CI/CD 等价流水线

### 2.1 落盘流水线（手册第一章卡点的单机等价）

```
L0 草稿自检(文本,不落盘) → py_compile / import 自检 → pytest 单测卡点(FAIL 即停)
  → 制品登记(逐文件 sha256 + 版本号 + 落盘时间戳)
  → L2 FREE 路径只读演练(apply=False / --dry / 只读子命令)
  → 落盘到本机生产(C:/aios 同一 inode) → 冒烟(5 子命令 --help / export)
  → 四条 AC 回归 + 零系统状态变更核验 → 出 G0-b 逐项「是/否 + 证据」报告
  → 人工确认(老田 / 团长) → 归档 + 发布单 + 回访登记
```

### 2.2 Stage 表（进入 / 退出判据，串行与并行标注）

| Stage | 动作 | 并行性 | 进入判据 | 退出判据（全部满足才可进下一步） |
|---|---|---|---|---|
| **S0** | pre-flight 只读预检（§3） | 只读，可与文本整理并行 | 单写者窗口已独占；G0-a 已 READY | 《发布前基线快照》产出：FREE 全部为 FREE、Python311 / pytest 可用、schtasks=492、ACL 可写、哈希基线 + git 25+ modified 快照留存 |
| **S1** | `ops/task_inventory.py` | **必须串行**（首步，建立单一事实来源） | S0 通过 + 基线未漂移 | 5 子命令 `--help` 均可解析；export / stats 逐项等于 §1.3；suggest-registry applied=false 且 REGISTRY 哈希不变；**连续跑 3 次**字节级一致、两次 diff 为空；自身 sha256 已登记 |
| **S2** | `ops/backup_format_gate.py` | **必须串行**（受单写者独占 + 同目录 ops/ 写入 + 门禁逐条验证约束，ADR-08） | S1 退出全通过 | 合成 1f8b 样本 ok=true、2d2d 伪样本 ok=false 且不抛穿 main；`gate_entry()` 五键保留只增六键；`write_quarantine_flag(apply=False)` 不产生文件；I/O 异常收敛 ok=false；注释含「flag 不改变 `list_backups()` 行为、不是运行态过滤闸门」 |
| **S3** | 两个单测 + 合成夹具目录 + **W-07** `tests/test_governance_acceptance.py` | **各文件内部可并行**；整体**必须在 S1+S2 之后串行**（单测 import 被测模块） | S1+S2 退出全通过 | `pytest -q` 全过、退出码 0；夹具为合成样本、大小上限内、不含任何 `backups/db` 生产明文 dump 或 `C:/backups_mirror` 真实 .enc；`git status` 新增 ⊆ GOV-W 终表（**W-07 已显式纳入，不得判白名单外新增**） |
| **S4** | **W-08** `backups/db/2026-10-29/INVALID_GZIP.flag` | **必须串行**（唯一对 10-29 目录的写入动作，需先完成该目录四文件哈希基线） | S3 通过 + 10-29 四文件哈希已留存 | ⚠️ **W-08 已由团长新建落盘，本步为核验而非再次落盘**：八字段正确、`files` 聚合 3 个 `2d2d` 坏档、`policy=mark-only, do-not-delete`；同目录 manifest.json + 三个 .sql.gz **四文件 sha256 与 S0 基线逐字节一致（口径 A）且零改写**；G-09 verify 放行 W-08 为 `backups/db` 下唯一允许新增；默认演练取最新 11-05、**10-29 目录不被选为演练数据源**；显式 `--date 10-29` 时人工看 flag 拦截；不改 `b4_restore_drill.py` |
| **S5** | 文档（项目档案 / `docs/changes/20261105_ff06_interface_hooks.md` / 全案整合文档 / W-12） | **各文档之间可并行**；整体在 S4 之后串行（口径声明需引用 flag 事实） | S4 通过 | 均为 GOV-W 终表内纯新增；FF06 文档含 16 条接入点 A1~A16 与 FF 编号对照表，且 **A9 必须标注「本轮是否改：否」**、写明「自动过滤待 D4 裁定后实施期接入 `should_select(verify_artifact(p))`；本轮不承诺代码自动过滤」、全文不得出现「加 flag 后演练自动跳过 10-29」等运行态生效表述；三处口径统一为「明文副链断档 + 两库真空」且 `db_dump_daily.py` 哈希不变；全案文档登记版本边界、技术债务（含 TD-08）、`ops/data/` 不入库声明 |
| **S6** | 归档 + 发布单 + 回访登记 | **必须最后串行** | S5 通过 + **G0-b 报告已签发** | 五件归档材料 24h 内完成；回访三节点登记完毕；`git status` 新增 ⊆ GOV-W 终表且既有 25+ modified 哈希与 S0 一致 |

**通用进入判据**：上一步退出判据全部满足 + 发布前基线未漂移 + 单写者窗口仍独占 + 无并行作业新增 modified 文件。
**通用退出判据**：按 GOV-W 终表**精确路径**新建 + `py_compile` / import 通过（代码文件）+ 该文件 sha256 已登记发布单 + `git status --porcelain` 新增 ⊆ GOV-W 终表 + 既有 25+ modified 文件哈希未变。

**发布窗口**：团长单写者独占；避开本机并行作业；避开 06–09 点 76 个 Ready 早高峰、07:45 BackupDaily 加密主链、07:56–08:33 与 10:08–10:25 备份写入窗口；建议工作日上午，留足 T+48h 观察。

### 2.3 每阶段观察指标（手册 4.1 单机等价）

错误率 → pytest 退出码 / 失败用例数；崩溃率 → py_compile / import 异常数；核心转化率 → 四条 AC 判据通过数；客诉量 → 老田反馈 + 飞书误报数（**本轮应为 0，见 §6**）；附加本机三项 → **哈希漂移数、GOV-W 终表外新增数、schtasks 计数漂移数**。任一指标异常立即暂停后续 stage 或触发 §5 回滚。

---

## 3. 发布前检查 pre-flight（全部只读）

> 以下命令均为团长落盘窗口内执行；本文档编制阶段未运行任何一条。中文路径一律走 **Bash + Python**，不经 PowerShell。

**① 路径 FREE 复核**（`lexists` 避免 junction / 断链误判）
```bash
"C:/Program Files/Python311/python.exe" -X utf8 -c "import os; ps=['C:/aios/ops/task_inventory.py','C:/aios/ops/backup_format_gate.py','C:/aios/backups/db/2026-10-29/INVALID_GZIP.flag','C:/aios/docs/changes/20261105_ff06_interface_hooks.md','C:/aios/docs/plans/20261105_release_plan.md']; [print(p,'FREE' if not os.path.lexists(p) else 'OCCUPIED-ABORT') for p in ps]"
```
判据：全部 FREE；任一 OCCUPIED → 停止，重新裁定路径，不得覆盖。

**② Python311 + pytest 可用性探测**
```bash
"C:/Program Files/Python311/python.exe" -X utf8 -c "import sys,pytest; print(sys.version.split()[0]); print('pytest',pytest.__version__)"
```
判据：3.11.x 且 pytest 可 import（本机**无 python3 别名**）。

**③ schtasks 只读可用性 + 计划任务数 492 基线**
```bash
"C:/Program Files/Python311/python.exe" -X utf8 -c "import subprocess; b=subprocess.run(['schtasks','/query','/fo','csv','/nh'],capture_output=True).stdout; print('rows',len([l for l in b.splitlines() if l.strip()]))"
```
判据：只数行、**不解析中文任务名**（OEM 代码页乱码规避）；以 `C:/Temp/tasks_inventory_20261105.json` 的 492 条为准交叉校验，Ready 407 / Disabled 76 / Running 9 一并记录。**全程只读，禁止任何 Register / Start / Disable / Unregister**。

**④ 目标目录 ACL / 可写性**
```bash
"C:/Program Files/Python311/python.exe" -X utf8 -c "import os; [print(p,os.access(p,os.W_OK)) for p in ['C:/aios/ops','C:/aios/docs/plans','C:/aios/docs/changes','C:/aios/backups/db/2026-10-29']]"
```
判据：四个父目录可写；`ops/data` 若不存在属预期（本轮不生成运行时产物，ADR-20）。

**⑤ 既有文件哈希基线（终裁 A：全量 sha256，发布前后同口径）**
```bash
"C:/Program Files/Python311/python.exe" -X utf8 -c "import hashlib,os,json,glob; d='C:/aios/backups/db/2026-10-29'; t=[d+'/manifest.json']+sorted(glob.glob(d+'/*.sql.gz'))+['C:/aios/ops/db_dump_daily.py','C:/aios/quant_backtest/miss_check.py','C:/aios/tools/b4_restore_drill.py','C:/aios/jev-eval/backup_daily.ps1']; o={p:hashlib.sha256(open(p,'rb').read()).hexdigest() for p in t if os.path.isfile(p)}; print(json.dumps(o,ensure_ascii=False,indent=2))"
```
判据：10-29 目录 1 个 manifest + 3 个 .sql.gz 全部有哈希；四个硬边界文件全部有哈希；整份 JSON 登记进发布单附件。**终裁 T-10 采纳 A（全量 sha256）**：F2 已按 A 实现、零改动；本命令与 §5.3 命令④ 必须前后同口径；GB 级耗时列实施期观测项、**不设阈值**。**G-09 基线同口径分层**：脚本 `C:/Temp/governance_zero_state_baseline.py` 对 10-29 四文件用全量 sha256、对其余全库用轻量指纹，**同一对象前后不混用算法**，故不产生哈希漂移误判；`__pycache__` / `.pytest_cache` / `*.pyc` 由该脚本单列审计、不判白名单失败。

**⑥ git 既有改动基线（只读）**
```bash
git -C C:/aios status --porcelain
```
判据：记录 25+ 既有 modified 文件清单与其哈希快照，作为「发布未触碰任何 modified 文件」比对基准；**不执行 add / commit / checkout**。

**⑦ 编码与回滚工具可用性**：确认 `-X utf8` 口径；PowerShell 5.1 写日志须显式 `-Encoding utf8`（`*>>` 会写 UTF-16LE，禁用）；确认 `C:/项目/.workbuddy/tools/recycle_del.py` 存在且 `--dry` 可执行（只做帮助探测，**不指向任何真实路径**）。

**⑧ junction 同一 inode 复核**：确认 `C:/aios` 与 `C:/项目/老田的AI助手` 指向同一目录，发布单注明「单 inode，无第二份制品」。

**⑨ 快照基准**：`C:/Temp/tasks_inventory_20261105.json` 存在且 492 条，作为 AC① 字节级比对源。**声明**：这是 pre-flight **只读基线核对**，**不是夹具数据来源**；夹具 golden 由治理方案 §1.3 计数**合成**，不复制该快照。发布单须把「只读基线」与「合成夹具」分列（FF11）。

**⑩ 窗口与应急联系人**：老田（唯一使用者 / 审批人）、团长（单写者 / 回滚执行人）、qa-tester（门禁复判）、ops-sre（应急衔接）窗口内可响应。

**产出**：《发布前基线快照》＝ ① FREE 结论 + ② 版本 + ③ 492 行数 / 分布 + ④ ACL + ⑤ 哈希 JSON + ⑥ git porcelain 输出 + ⑦⑧⑨⑩ 结论，登记于发布单附件（文本形式，**不额外新增 GOV-W 终表外文件**）。

---

## 4. 验证门禁（量化）

### 4.1 G0 两阶段签发（终裁①形式确认）

| 闸门 | 时点 | 判据 | 状态 |
|---|---|---|---|
| **G0-a 设计态** | **落盘前** | 「测试策略与门禁判据 / 规则集已交付且自洽」＝ **READY** | ✅ 已可签；qa-tester 对齐消息 + v1.1 修订作 G0-a 附件 |
| **G0-b 执行态** | **落盘 + 全部验证跑完后** | 「质量门禁执行结论 ＝ 通过」，须出**逐项「是/否 + 证据」测试报告**（格式见附件②） | 落盘后签；**六项证据缺一不签** |

**G0-b 六项证据清单**：① 两个单测 + W-07 `tests/test_governance_acceptance.py` 的 pytest 完整 stdout/stderr + rc；② 四条 AC 逐条判据实际值与证据路径；③ **性能 6 项设计预算声明齐备**（设计交付核对；**实测值栏必须存在、留空并标注「实施期填」，不设阈值、不跑真实性能测试**）；④ 零状态变更四项的前后指纹 diff；⑤ P0/P1 bug 清单与关闭状态；⑥ FF10 五行矩阵执行记录。
> 第③项**齐备定义**：该行存在 + 留空标注即算齐备，**不要求有数值**；证据项保留不删除。此修订消除了「不实测却必填实测」导致 G0-b 永远签不了的自相矛盾。

### 4.2 G1~G6 判据（终裁后定稿）

| 门禁 | 判据 |
|---|---|
| **G1 / AC①** | `task_inventory.py` **连续运行 3 次**字节级一致，export / stats 逐项等于治理方案 §1.3：总 **492** / Ready **407**·Disabled **76**·Running **9** / 自定义 **293**·系统 **199** / 真实失败 **32** / 从未运行 **24** / Disabled **45** / 06–09 高峰 **76** / REGISTRY **23 条覆盖 18 个自定义任务**（覆盖率 18/293≈6.1%）；噪声常量断言恰为 frozenset {267009, 267011, 2147943140, 1073807364}；`1999-11-30` 从未运行哨兵生效；**系统路径三前缀**（Python 源码字面量 `("\\Microsoft\\","\\ZeroBound\\","\\AIOS-")`），单前缀会得 180/312/36 错值（负向用例 TI-E19 反证）；`classify()` 顺序 `never_run→noise→system→real_failed→disabled→running→ready`；统计按原始 `state` 与 `category` **分两组断言、禁止混算**；suggest_registry applied=false 且零写 REGISTRY；diff.removed 仅软删除标记；两次运行 diff 为空 |
| **G2 / AC②** | flag 八字段 `type / date / files / format=plain-sql / recoverable=false / detected_magic=2d2d / policy=mark-only-do-not-delete / decision_ref=D4` 正确；同目录 manifest.json + 三个 .sql.gz **四文件 sha256 与基线逐字节一致（口径 A）**；**fullstack 代码级语义**：`list_backups()` 只收集含 `quant_pg_alldb.sql.gz` 的日期目录并倒序、`main()` 取 `backups[0]`、**不做格式过滤** → 默认演练取含该文件的最新日期目录（本轮 **11-05**，魔数 1f8b 已验），「**10-29 目录不被选为演练数据源**」；显式 `--date 10-29` 时**人工看 `INVALID_GZIP.flag` 拦截**；**不改 `b4_restore_drill.py`、不承诺自动过滤、不写死「可选备份数=0」**（T-11 已终裁关闭：本轮 AC-02 不含任何 `list_backups()` 返回值断言，「=0」全域不可达，整体降为 D4 后实施期目标态判据；AC-N04 按补丁 3 四条验证） |
| **G3 / AC③** | 三处口径统一为「**明文副链断档 + 两库真空**」，由新文档声明替代改原文件；`ops/db_dump_daily.py` 哈希与发布前一致（纳入 25+ modified 基线） |
| **G4 / AC④** | 关键链哨兵补链与 `DbDumpDaily=pending` 为**设计完成**（文档 / FF06 接入点存在），运行态零写入；**`miss_check.py` 哈希前后不变**；AC-04 按 ADR-05、不做运行态验证（终裁④） |
| **G5 质量** | P0 bug=0（G-01）；核心链路 **14 条 100%**（G-03）；两个单测 + W-07 `pytest -q` 全过、退出码 0；**性能不作为放行判据，仅核对设计预算声明齐备**（G-06 相应为「设计交付清单核对完成」） |
| **G6 零系统状态变更** | ① `schtasks` 计数 ＝ **492** 且状态分布一致，无任何 Register / Start / Disable / Unregister；② `backups/db` 既有文件哈希不变（口径 A、前后同口径；G-09 基线同口径分层：10-29 四文件全量 sha256 + 全库轻量指纹，**同一对象前后不混用算法**）；③ `git status --porcelain` 新增 ⊆ **GOV-W 终表 W-01~W-14**（**W-07 已显式纳入、W-08 为 `backups/db` 下唯一允许新增且已落盘、W-14 为扩员项**），既有 25+ modified 文件哈希与 S0 一致；**补注**：pytest / py_compile 产生的 `__pycache__`、`.pytest_cache`、`*.pyc` 由 G-09 基线脚本**单列审计，不判白名单失败**；④ **无任何 `.enc` 解密执行**（Unprotect-File 零调用、AES 密钥**内容**零读取〔mtime/ACL 元数据允许〕、无新增 .enc 产物）。另加 **AC-E08 全程反向扫描**（monkeypatch subprocess + 文件指纹）主动证明**硬边界 7 个 0**。附加：未触碰 `10.205.0.118`；DUMPS / KEEP_DAYS / REGISTRY / `b4_restore_drill.py` / `backup_daily.ps1` 哈希不变 |

### 4.3 门禁设计原则（终裁①正式登记进发布单「门禁设计原则」栏）

> **判据必须「可签」**：既不能未跑先过（**假绿 / 纸面达标**，ADR-02），也不能因自身矛盾或不可达而永远签不了（**假红 / 纸面阻塞**），更不能诱导实施者为满足断言而违反硬边界（如改 `b4_restore_drill.py`、跑真实性能 / 推送）。

本轮覆盖两处同源缺陷：① G0-b 第③项「不实测却必填实测」→ 修为「预算声明齐备、实测值栏留空标注实施期填」；② AC-02「=0」在硬边界下全域不可达 → G2 不写死该断言。

### 4.4 门禁失败处置与阻断条件

| 情形 | 处置 |
|---|---|
| G0-a 未 READY | **不进入落盘** |
| G0-b 未通过 | **不宣布发布完成**，按局部 / 全量回滚处置；发布单状态只记「已落盘·待执行态门禁」 |
| G1~G4 任一 AC 判据失败 | 停止进入下一 stage；已落盘当前批按 §5 **局部回滚**或修复后重跑该 stage；**禁止带失败项继续落盘** |
| G5 pytest 失败 / 出现 P0 | 同上；根因指向新增文件缺陷则整批回滚 |
| **G6 任一项异常** | **立即触发 FF05 全量回滚**（最高优先级），同步团长 + ops-sre |
| 既有 modified 文件被本轮改动 / GOV-W 终表外新增 | 判定**并行作业冲突**：立即停止、**不自行修复**（避免踩他人改动），上报团长协调 |
| 出现 .enc 解密 / 密钥读取 / 飞书正文含 dump | 立即中止 + 合规上报老田 |
| 任何豁免 | 记入《发布门禁偏差台账》，须老田逐条书面签字；**G6 四项不得豁免** |

---

## 5. 回滚预案 FF05（手册 4.2 模板，必填且可执行）

```
回滚触发条件：
  - G6 任一零系统状态变更项异常（任务数≠492 / 既有产物哈希漂移 / GOV-W 终表外新增 / .enc 解密痕迹）
  - G1~G5 任一 AC 或 pytest 失败且发布窗口内无法修复
  - 老田或团长任一方发出中止指令
  - 落盘过程中发现并行作业冲突（既有 modified 文件被触碰）
回滚步骤：见 §5.1 逐文件动作表（按发布逆序 S5→S1，⓪ 先 --dry 演练，每步删后 os.path.lexists 复核），预计 ≤10 min
回滚验证（证明系统回到发布前状态）：
  - 哈希基线逐文件比对全 OK（10-29 四文件 + 4 个硬边界文件 + 25+ modified 快照，口径 A）
  - schtasks 计数 = 492 且状态分布与 S0 一致
  - git status --porcelain 新增 ⊆ GOV-W 终表且回滚后清空；25+ modified 列表与 S0 快照逐字节一致
  - pytest（移除本轮新增测试文件后回到发布前测试集合）全过、退出码 0
  - ~/.backup_aes_key mtime 未变、无新增 .enc、飞书链路无本轮告警残留
  - 输出《回滚完成确认单》→ 团长 + ops-sre
通知机制：团长（执行人）→ 老田 + qa-tester + ops-sre；口径模板：发生了什么 / 影响范围 / 当前状态 / 后续处置
预计总耗时：≤ 10 min（纯新增文件删除，无数据迁移、无服务切流、无流量回切）
数据兼容性处理：本轮纯新增只读文件 + 纯新增 flag，无 DB schema / 既有配置 / 既有文件改写；
  suggest_registry(applied=false) 零写 REGISTRY、write_quarantine_flag(apply=False) 默认不落盘
  → 无状态残留，回滚为幂等删除，不存在向后兼容或「先加后删」问题；
  运行时产物 ops/data/*.json 本轮不生成（ADR-20）；若意外生成须一并纳入删除清单并复核；
  另须复核 ops/ 下无 mkstemp 临时残留。
```

### 5.1 逐文件回滚动作（按发布逆序，⓪ 先 `--dry` 演练一遍）

| # | 动作 | 预计 |
|---|---|---|
| 1 | S5 文档（项目档案 / FF06 文档 / 全案整合文档 / W-12）→ `recycle_del.py` 精确路径删除 | 2 min |
| 2 | S4 `INVALID_GZIP.flag` → **单个精确路径**删除 + 同目录四文件 hash 复核 | 2 min |
| 3 | S3 单测 / 合成夹具目录 / W-07（含意外生成的 `ops/data/*.json`）→ 精确路径删除 | 3 min |
| 4 | S2 `ops/backup_format_gate.py` → 精确路径删除 | 1 min |
| 5 | S1 `ops/task_inventory.py` → 精确路径删除 | 1 min |
| 6 | 复核 `ops/` 与 `ops/data/` 无 mkstemp 临时残留 | 1 min |

**每步删后一律以 `os.path.lexists` 复核，`REMOVED` 才算成功。**

### 5.2 两条本机铁律（回滚命令必须体现）

- **Windows 删除一律进回收站**（`SHFileOperationW` + `FOF_ALLOWUNDO`）。❌ 禁用反例（**仅列为禁令，不是可执行命令**）：Unix 删除命令、Windows `del`、`shutil.rmtree`。唯一允许工具 `C:/项目/.workbuddy/tools/recycle_del.py`。
- 坑：`SHFileOperationW` 删中文路径常返回 **rc=2** 但文件**确实已进回收站** → **判定必须以 `os.path.lexists` / `os.path.exists` 复核为准**，禁止凭返回码判失败后重复删除（重复删会误伤）。
- **PowerShell 5.1 处理含中文路径不可信**（`Test-Path` 返回 False、`DeleteFile` 报找不到但实际已删）→ 中文路径文件操作一律 **Bash + Python**，回滚命令链不经过 PowerShell。
- **回滚只删「本轮 GOV-W 终表内新建文件」**，不碰任何发布前既有文件 —— 不违反硬边界「删除或改名任何现有文件」（该边界指发布前既有文件）。回滚过程**不执行** `git add` / `git commit` / `git checkout --`。

### 5.3 可执行回滚命令（无 rm / del，路径全正斜杠，哈希口径 A 与 §3⑤ 同口径）

```bash
# ⓪ 先 --dry 演练（不产生任何删除）
"C:/Program Files/Python311/python.exe" -X utf8 "C:/项目/.workbuddy/tools/recycle_del.py" --dry "C:/aios/ops/task_inventory.py" "C:/aios/ops/backup_format_gate.py"

# ① 正式：逐文件精确路径（示例为 S2/S1；其余按 5.1 表逐个同样调用，严禁目录整体 / 通配 / 批量）
"C:/Program Files/Python311/python.exe" -X utf8 "C:/项目/.workbuddy/tools/recycle_del.py" "C:/aios/ops/task_inventory.py" "C:/aios/ops/backup_format_gate.py"

# ② 删后 exists 复核（rc=2 不是失败判据，lexists=False 才是）
"C:/Program Files/Python311/python.exe" -X utf8 -c "import os,sys; [print(p,'REMOVED' if not os.path.lexists(p) else 'STILL_EXISTS') for p in sys.argv[1:]]" "C:/aios/ops/task_inventory.py" "C:/aios/ops/backup_format_gate.py"

# ③ flag 单独精确删除（严禁对 10-29 目录整体做任何删除 / 批量 / 通配操作）
"C:/Program Files/Python311/python.exe" -X utf8 "C:/项目/.workbuddy/tools/recycle_del.py" "C:/aios/backups/db/2026-10-29/INVALID_GZIP.flag"

# ④ 回滚后哈希基线比对（终裁 A：全量 sha256；BASELINE_JSON 为发布单附件登记的基线路径）
"C:/Program Files/Python311/python.exe" -X utf8 -c "import hashlib,os,json; b=json.load(open('BASELINE_JSON',encoding='utf-8')); [print('OK' if (os.path.isfile(p) and hashlib.sha256(open(p,'rb').read()).hexdigest()==h) else 'DRIFT',p) for p,h in b.items()]"
```

### 5.4 flag 文件与文档类产物回滚的额外注意

- **flag**：只删 `INVALID_GZIP.flag` **单个精确路径**；**严禁**对 `C:/aios/backups/db/2026-10-29/` 目录整体做任何删除 / 批量 / 通配操作，否则会误删同目录既有 `manifest.json` 与三个 `.sql.gz`（直接违反硬边界）；删前删后对该**四个文件逐一 sha256 复核并登记**（口径 A）。
- **文档类产物**（含 W-12）：均为 GOV-W 终表内纯新增，走同一回收站工具逐个精确删除；但它们是本轮**留痕载体**，删除前须确认发布单 + 逐文件哈希清单已另行归档（**回滚本身也要留痕**），避免「回滚后无据可查」。

---

## 6. 审核 / 合规风险提示（风险提前告知）

| # | 风险 | 等级 | 缓解 / 门禁挂钩 |
|---|---|---|---|
| 1 | **L2 未批准项夹带**：D0~D6 七项全部未批准（ADR-04），若发布清单混入 Register-ScheduledTask / Start-ScheduledTask / 改 DUMPS·KEEP_DAYS / 改 REGISTRY / b4 演练扩面执行 / .enc 解密 / 轮转 / 错峰 / 瘦身，即越权变更生产状态 | **高** | 发布单逐项签字；G6 零系统状态变更核验；GOV-W 终表外新增即阻断；**`decision_ref=D4` 仅为引用编号，不代表 D4 已批准、不代表可执行 D4 后续动作（重压缩 / 删除 / 改 manifest.json）**，ADR-05 明确改既有 manifest 延 D4 后 |
| 2 | **生产产物外泄**：`backups/db` 是**未加密明文 dump**（11GB 级），`C:/backups_mirror` 是真实 `.enc`；若被拷入夹具，或误入 git / `_vault` / 异地 | **高** | 夹具**必须运行时自建合成样本**：六类 `plain_sql_2d2d.gz`（10-29 永久回归）/ `valid.sql.gz` / `truncated.sql.gz` / `zero_byte` / `bad_crc` / `encrypted_aes` + 注入式 I/O 异常（monkeypatch，非文件）；前 64B 含 `AIOS-SELF-BUILT` 标记；AC-E03 反向断言不含 `-- pg_dump` / 生产库名 / 凭据；**严禁从 `backups/db` 拷明文 dump（FF11）、严禁从 `C:/backups_mirror` 拷真实 .enc**；门禁检查新增文件大小上限与魔数；`ops/data/` 不入库；本轮**不改 .gitignore**（ADR-09） |
| 3 | **AES 密钥入包**：`~/.backup_aes_key`（44 B）若进代码库 / 制品 / 日志 / 飞书正文 | **高** | 密钥泄露检查项（新增文件不含该路径字面量与密钥）；发布单记录「零密钥读取」；G6④ 无 .enc 解密执行 + 密钥 mtime 前后不变 |
| 4 | **飞书告警正文含 dump / 凭据**：正文若携带 SQL 片段、dump 内容、webhook 地址、密钥即外泄 | 中高 | **本轮零真实推送、既有飞书链路零调用**；若门禁失败需通知，走「团长→老田」人工口径，不走飞书；正文只写「文件名 + rc + 阶段」，禁止 dump 内容或凭据 |
| 5 | **TD-08 既有缺陷**：`alert_fail_wrap.py` 第三参必须是**真实 UTF-8 日志路径而非正文**；PowerShell 5.1 用 `*>>` 会写 UTF-16LE 导致日志乱码 / 告警误判 | 中 | **TD-08 不进本轮 AC / 门禁**，登记为实施期契约测试必测项 **IT-01**（第三参为真实 UTF-8 `.log` 路径、`code` 字段单行无 `|`、≤120 字符）；本轮**不改该文件**；写日志一律显式 `-Encoding utf8`；登记技术债务交后续里程碑；Checklist 项 6 核验 |
| 6 | **新增文件不提交 git 时留痕缺失**：不执行 `git add` / `git commit`（避免把并行作业 25+ 改动一起提交） | 中 | ① 发布单（版本号 v0.1+v0.2、时间窗、单写者、**GOV-W 终表逐文件 sha256**、清单结果、回滚预案、豁免记录）；② 全案整合文档登记版本边界与编号对照；③ FF06 接入点文档记录 16 条接入点 A1~A16 与 FF 编号对照表（ADR-07）；④ 本机工作日志追加（`-Encoding utf8`）；⑤ 归档材料 24h 内归档、随版本保留 ≥1 年；⑥ 待并行作业结束后由老田 / 团长单独做一次**仅 GOV-W 终表文件**的提交（排除既有 modified）。**不提交 ≠ 无留痕** |

---

## 7. 上线检查 Checklist（手册第五章十项，逐项齐备）

- [ ] **1. 质量门禁结论**：G0-a 已 READY；G0-b 报告（逐项是/否 + 证据）齐备后签发；P0 bug=0、核心链路 14 条 100%、单测 `pytest` 全过、退出码 0
- [ ] **2. 生产配置核对**：解释器绝对路径 `-X utf8`（无 python3 别名）、PowerShell 5.1 显式 `-Encoding utf8`（禁用 `*>>` UTF-16LE）、junction FREE 且单 inode、pytest 与 `recycle_del.py` 可用、四个目标目录 ACL 可写
- [ ] **3. 证书 / 签名等价核验**：GOV-W 终表路径逐字匹配（Bundle ID 等价，坑 1）、`atomic_write_json` 为 mkstemp+os.replace、编码一致性、回收站 FOF_ALLOWUNDO 工具未被绕过（坑 2）
- [ ] **4. 域名 / 网络等价白名单**：git 新增 ⊆ GOV-W 终表、**零新增网络监听 / 域名 / 端**、飞书沿用既有链路且不调用（坑 3）
- [ ] **5. 资质 / 批准状态**：D0~D6 全部未批准复核、零夹带阶段 1/3 动作、全文无 Register/Start/Disable/Unregister 命令、`decision_ref=D4` 仅引用编号；**「设计完成 ≠ 运行态生效」专项复核**（ADR-03 / ADR-05 / ADR-11）：FF06 文档 A9 须标「本轮是否改：否」，全文不得出现「加 flag 后演练自动跳过 10-29」等生效表述
- [ ] **6. 隐私合规 / 密钥泄露检查**：明文 dump 与真实 .enc 不入夹具、不入 git/`_vault`/异地；AES 密钥不进包 / 日志 / 飞书正文；webhook 不入正文；夹具不含 `C:/backups_mirror` 真实 .enc（坑 4）；**禁止把告警正文当第三参传入**（TD-08 登记 IT-01，本轮零告警推送）
- [ ] **7. 监控告警衔接**：既有飞书链路**零调用**（不新增 webhook / 域名、不发真实告警、本轮告警频率＝0）；告警相关仅做回滚触发条件与应急口径的**文档衔接**；发布窗口避开 06–09 高峰与 07:45 / 07:56–08:33 / 10:08–10:25 备份窗口；F1 可重复运行且 diff 软删除不重复计数（坑 5 幂等）
- [ ] **8. 回滚方案已走查**：FF05 逐文件动作可执行、先 `--dry`、`lexists` 复核、`rc=2` 坑、中文路径 Bash+Python、≤10min、发布前哈希基线已留存（口径 A）、10-29 目录不做任何批量操作
- [ ] **9. 发布窗口与单写者确认**：团长独占、避开并行作业、**不执行 `git add` / `git commit` / `git checkout --`**、发布单提前 1 工作日发出
- [ ] **10. 应急联系人到位**：老田（审批）、团长（单写 / 回滚执行）、qa-tester（门禁复判）、ops-sre（应急衔接），窗口内可响应

---

## 8. 证书签名常见坑的规避措施（手册第六章，逐坑映射到 Checklist）

| 手册坑 | 本项目等价前置核验 | 若不核验的后果 | Checklist 项 |
|---|---|---|---|
| 1 iOS 证书 / 描述文件不匹配、Bundle ID 拼写、证书过期 | 路径 FREE 复核 + GOV-W 终表**精确路径逐字匹配** + 解释器绝对路径 `-X utf8` + junction 单 inode | 写到另一分支 / 静默跑到别的解释器 / 覆盖他人文件 | 3、2 |
| 2 Android 正式 / 调试签名混淆、加固后未重签、升级签名不一致 | `-X utf8` 与 `-Encoding utf8` 编码一致性；mkstemp+os.replace 原子性；回收站 FOF_ALLOWUNDO 工具不可被替换 | 半写产物被当有效制品 / 日志 UTF-16LE 乱码 | 3、2 |
| 3 小程序域名未备案、白名单漏配、基础库不兼容 | **GOV-W 终表白名单**（漏配＝终表外新增即阻断）、零新增网络监听、飞书沿用既有链路不新增域名 | 夹带新增文件或新端，破坏「零新增端」 | 4 |
| 4 HTTPS 证书链不完整、多域名漏配、到期未续 | `~/.backup_aes_key` 零读取、mtime 前后不变、密钥不进包；**无任何 .enc 解密执行**；真实 .enc 不入夹具 | 密钥外泄 / 触发硬边界 | 6 |
| 5 支付回调不可达、签名验证失败、重复回调不幂等 | `alert_fail_wrap.py` 第三参为**真实 UTF-8 日志路径**（TD-08 / IT-01）；F1 连续 3 次字节级一致、二次 diff 为空、diff.removed 仅软删除标记不重复计数 | 告警乱码 / 误报 / 492 统计漂移 | 7、1 |

> 每个坑均已出现在上线检查清单核验项里，**禁止凭记忆临场检查**。

---

## 9. 交付后回访节点（三个明确钩子；起点以 **G0-b 签发时刻**计，非落盘时刻）

| 节点 | 回来核对什么 | 联动 |
|---|---|---|
| **T+48h** | ① 再跑 `task_inventory.py` export / stats，与发布时快照 **diff 为空**；② 单测再次全过；③ `ops/data/*.json` **未被生成**、`ops/` 无 mkstemp 残留；④ `git status` 新增仍 ⊆ GOV-W 终表、492 计数未漂移；⑤ 飞书无任何本轮误报（应为 0） | 手册第十一章 T+30min / T+2h / T+24h 单机等价；72h 内出现 P0 立即触发 FF05 并同步团长与老田 |
| **T+1 周** | ① **492 任务统计漂移**：Ready 407 / Disabled 76 / Running 9 分布、真实失败 32、从未运行 24、06–09 高峰 76 是否变化，确认无任何计划任务被本轮或并行作业变更；② 10-29 四文件哈希仍与基线一致（口径 A）；③ 默认演练仍取 11-05、10-29 仍不被选为演练数据源；④ 既有 25+ modified 文件仍未被本轮触碰 | 结论交 ops-sre 作为告警基线校准输入 |
| **T+1 月** | 复盘治理方案 §六 **M1~M6**：必保库 3/5→5/5、.enc 解密演练 0→≥1、gzip 魔数全绿、关键链哨兵 18/293→100%、单日窗口 16.4min→≤3min、32 个失败任务全归因。⚠️ 这些是**阶段 1/3 之后的目标态，非本轮 AC**（ADR-02），本轮只做「基线复核 + 不纸面达标」；同时复盘 D0~D6 批准进度、TD-08 / IT-01 修复排期、v0.3（需 D0）/ v1.0（需 L2 五要素批准）启动条件是否具备 | 复盘结论作为运维方案输入；执行偏差与反馈 Top3 进入需求池 |

---

## 10. 发布材料归档与沟通

归档五件（**归档起点以 G0-b 签发时刻计；发布完成 24h 内归档，未归档视为发布未完成，随版本保留 ≥1 年**）：
1. **发布单**：版本内容 v0.1+v0.2、时间窗、单写者、**GOV-W 终表逐文件 sha256**、检查清单结果（含豁免说明）、**门禁设计原则「判据必须可签」**登记；
2. **制品记录**：版本号、逐文件 sha256、落盘时间戳、上一稳定版本＝发布前哈希基线快照位置（口径 A）；
3. **审核记录**：本轮＝内部合规审核（L2 批准状态复核、零夹带确认、终裁 ADR-16~20）；
4. **配置变更记录**：本轮＝**零配置变更、零密钥操作、零告警推送**声明；
5. **观察报告**：T+48h / T+1 周 / T+1 月结论。

**发布单状态机**：`G0-a READY` → `已落盘·待执行态门禁`（G0-b 未签发前**不得标记「发布完成」**）→ `G0-b 通过 → 发布完成`。
对内：发布单提前 1 个工作日发出并确认。对老田（唯一使用者）：说明「本次仅新增只读工具与留痕文档，**系统状态零变更**」，不用内部术语。事故口径模板提前备好（发生了什么 / 影响范围 / 当前状态 / 后续处置）。

---

## 11. 团长形式终裁记录（ADR-16~20）与上下游衔接

| # | 终裁项 | 结果 | 本文落点 |
|---|---|---|---|
| ① | G0 拆分（G0-a 设计态 READY / G0-b 执行态） | **形式确认**。G0-b 第③项按「性能 6 项设计预算声明齐备、实测值栏留空标注实施期填」为可签；「判据必须可签」原则正式登记进发布单门禁设计原则栏 | §4.1 / §4.3 / §10 |
| ② | T-10 哈希口径 | **采纳 A（全量 sha256）**，团长正式终裁。F2 已按 A 实现、零改动；§3⑤ 与 §5.3 命令④ 维持全量、前后同口径；GB 级耗时列实施期观测项、**不设阈值**。G-09 基线**同口径分层**：10-29 四文件全量 sha256 + 全库轻量指纹，**同一对象前后不混用算法** | §3⑤ / §4.2 G2·G6 / §5.3 |
| ③ | 编号与白名单 | **改用 GOV-W 编号表**，团长补发终表原文，由 W-01~W-13 **扩员至 W-01~W-14**（ops-sre v1.1 运维全案获批为 W-14；G-09 基线脚本 `GOV_W` 同步 14 条），本轮唯一有效白名单。`tests/test_governance_acceptance.py` ＝ **W-07，已显式纳入**；**W-08 已落盘**，为 `backups/db` 下唯一允许新增（八字段、`files` 聚合 3 个 `2d2d` 坏档、`policy=mark-only, do-not-delete`，既有 manifest 与三个 `.sql.gz` 零改写）；`ops/data/*.json` **降级为「运行时产物，本轮不生成」**，不再是文件编号；`__pycache__` / `.pytest_cache` / `*.pyc` 由基线脚本单列审计、不判白名单失败；所有 F/FF/PF/BG/TI/AC/G/T 编号均为方案内引用、非白名单依据（ADR-07 扩展）。**落盘前硬阻断 ③ 已解除** | §1.1 / 附件① / §2.2 / §4.2 G6 |
| ④ | AC-04 按 ADR-05、不做运行态验证 | **形式确认** | §4.2 G4 |
| ⑤ | T-11 / AC-02 | **按 PM 权威更正形式终裁关闭**。本轮 AC-02 不含任何 `list_backups()` 返回值断言；「=0」全域不可达，整体降为 **D4 后实施期目标态判据**；G2 维持不写死断言；AC-N04 按补丁 3 四条验证。早期「『=0』作用域可限为 10-29 单目录选中数」表述**已作废**（残留「本轮可断言 0」误读空间） | §4.2 G2 |

**上游**：Phase 1 [技术研发方案] 部署形态＝候选 A（ADR-01）；Phase 2 [测试保障方案] 门禁设计态 READY（G0-a）；G0-b 由团长落盘跑完后按附件②格式出具。
**下游交付 ops-sre**：部署形态（单写者本机就地落盘、零常驻服务 / 零网络监听 / 单 inode）；FF05 触发条件与逐文件动作；零系统状态变更基线（492 / 四文件哈希 A / GOV-W 白名单 / 无 .enc）；发布窗口；**本轮告警频率＝0、既有飞书链路零调用**；回访三节点起点为 G0-b 签发时刻；「发布单 + 哈希清单」可追溯口径。
**冲突处理**：测试未达质量门禁 → 拒绝安排发布 + 发布单注明阻塞项 + 上报团长，禁止带病上线；任何豁免须老田逐条书面签字，**G6 四项不得豁免**。

---

## 附件① 编号对照表

> ⚠️ **声明（ADR-07 / ADR-20）**：下表**全部为「本方案自定义用例 / 门禁编号」，非仓库既有文件编号**，不得当作既有文件引用；且**除 GOV-W 终表外，均非白名单依据**。

### A. 白名单依据（唯一有效）

**GOV-W 终表 W-01~W-14**（团长补发终表原文，本轮唯一有效白名单；完整表与 5 条补充口径见 **§1.1**）：

- W-01 `ops/task_inventory.py`｜W-02 `ops/backup_format_gate.py`｜W-03 `docs/plans/20261105_data_backup_and_task_governance_plan.md`｜W-04 `docs/plans/20261105_governance_project_dossier.md`
- W-05 `tests/test_task_inventory.py`｜W-06 `tests/test_backup_format_gate.py`｜W-07 `tests/test_governance_acceptance.py`｜W-08 `backups/db/2026-10-29/INVALID_GZIP.flag`
- W-09 `docs/changes/20261105_ff06_interface_hooks.md`｜W-10 `docs/changes/20261105_governance_terminology.md`｜W-11 `docs/plans/20261105_governance_full_case.md`｜W-12 `docs/plans/20261105_release_plan.md`
- W-13 `docs/plans/20261105_governance_acceptance_report.md`｜W-14 `docs/plans/20261105_ops_sre_plan.md`

W-07 已显式纳入；**W-08 为 `backups/db` 下唯一允许新增且已落盘**（八字段、三个 `2d2d` 坏档、既有 manifest 与三个 `.sql.gz` 零改写）；**W-14 为扩员项**（ops-sre v1.1 运维全案）；`ops/data/*.json` 降级「运行时产物、本轮不生成」，不再是文件编号。G-09 基线脚本 `C:/Temp/governance_zero_state_baseline.py` 的 `GOV_W` 已同步 14 条，并单列审计 `__pycache__` / `.pytest_cache` / `*.pyc`，**不判白名单失败**。发布单登记白名单须附本终表原文。

### B. 方案内引用编号（非白名单依据）

| 前缀 | 含义 | 归属册 | 本轮门禁 |
|---|---|---|---|
| `TI-N/E-xx` | `ops/task_inventory.py` 正常流 / 异常流用例 | N01 / E18~E23 / E19 / E20 入 A 册，其余 B 册 | A 册部分是 |
| `BG-N/E-xx` | `ops/backup_format_gate.py` 正常流 / 异常流用例 | 全部 B 册（闸门 F-06 属 v0.2 设计层） | 否 |
| `AC-N/E-xx` | W-07 `tests/test_governance_acceptance.py` 独立验收四条 AC | A 册 | 是 |
| `PF-N/E-xx` | 性能设计交付核对项 | B 册 | 否（不实测） |
| `CM-N/E-xx` | FF10 兼容矩阵用例 | A 册为主 | 部分 |
| `IT-01` | 实施期契约测试必测项（TD-08 告警正文） | B 册 | 否 |
| `G-01~G-09` | 质量门禁项（**G-09a/b/c/d 为零状态变更四子项**，与本文 G6 四项一一对应） | — | 是 |
| `T-01~T-11` | 测试阻塞项 / 口径待确认（T-03/04/06 已解除；**T-10 已终裁 A、T-11 已关闭、T-02 已由 GOV-W 终表解除**） | — | — |
| `TI-E19` | 单前缀反证用例（180/312/36） | A 册 | 是 |
| `AC-E09~E12` | AC-02 四个异常护栏（幂等 / 兜底 / 清单损坏 / 只读） | A 册 | 是 |
| `TI-E21~E23` | AC-01 三个异常标签（PERMISSION_DENIED / EMPTY_SNAPSHOT / NO_BASELINE） | A 册 | 是 |
| `AC-N08` | AC-04 设计约束（expect 显式可审计） | A 册 | 是 |

**发布侧引用约定**：发布单与 G0-b 报告只引 `G-01~G-09`（含 G-09a/b/c/d）及 GOV-W 终表编号；**不引 PF-* / BG-* 作放行判据**（B 册）。

## 附件② G0-b 报告模板与六项证据清单

**表头**：`测试报告 | {版本} | {日期} | 执行人：团长`
**明细表**（逐项写，**不得写「基本没问题」**）：`门禁项 | 判据 | 是/否 | 证据路径 | 实测值 | 备注`
**结论六段**：一、是否达到质量门禁（逐项对照给是/否）；二、测试范围（执行用例数 / 通过率 / 覆盖模块）；三、缺陷统计（P0 x 全关闭 / P1 x 状态 / P2·P3 存量与豁免）；四、兼容执行（FF10 五行矩阵覆盖与遗留）；五、性能与安全（关键数据与结论——**性能只写预算声明齐备，实测值栏留空标「实施期填」**）；六、遗留风险 + 规避建议。

**六项证据（缺任一项不签 G0-b）**：
1. 两个单测 + W-07 `tests/test_governance_acceptance.py` 的 pytest 完整 stdout/stderr + rc；
2. 四条 AC 逐条判据实际值与证据路径；
3. **性能 6 项设计预算声明齐备**（设计交付核对；实测值栏必须存在、留空并标注「实施期填」，不设阈值、不跑真实性能测试）——**该行存在 + 留空标注即算齐备，不要求有数值**；
4. 零状态变更四项前后指纹 diff（口径 A，前后同口径）；
5. P0/P1 bug 清单与关闭状态；
6. FF10 五行矩阵执行记录。

---

## 附录 口径纪律（落盘 / 写全案文档时务必遵守）

1. **flag 非自动过滤闸门**：`list_backups()` 只按日期倒序枚举含 `quant_pg_alldb.sql.gz` 的目录、不做格式过滤，`main()` 取 `backups[0]`（当前 11-05）→ **10-29 不被选为演练数据源与 `INVALID_GZIP.flag` 无因果**。`INVALID_GZIP.flag` 仅是 ① 隔离标记（mark-only-do-not-delete）② 显式 `--date 2026-10-29` 时的人工拦截凭据 ③ 审计留痕，**不是运行态自动过滤闸门**；自动过滤要等 **D4 裁定后实施期**接入 `should_select(verify_artifact(p))`（FF06 接入点 **A9，本轮标注「本轮是否改：否」**）。
2. **设计完成 ≠ 运行态生效**（ADR-03 / ADR-05 / ADR-11）：全案文档与对外表述必须写「隔离标记 + 人工拦截」，**不得写成「加了 flag 后演练自动跳过 10-29」「10-29 坏档永不被选中（运行态）」**；`DbDumpDaily=pending` 与关键链哨兵补链均为设计完成，不落运行态。
3. **性能与异常基线不进本轮四条 AC**（PM 最终版）：PF-* 与 BG-* 全在 B 册「实施期契约测试 / 设计交付核对」，落盘后仍跑、pytest 退出码 0，但**不作为四条 AC 放行判据**；性能只核对预算声明、不实测、不设阈值。
4. **本轮零真实动作**：零真实推送、零真实备份、零告警调用、零 `.enc` 解密、零计划任务变更；门禁失败通知走「团长→老田」人工口径。

---

**发布结论**：本方案为「无回滚不发版」的可执行作业指导书——FF05 具备触发条件、逐文件步骤、≤10min 预计耗时、数据兼容性处理与回滚后四重证明；灰度以文件级分批 stage gate 等价实现并给出直接全量理由；G0 两阶段签发杜绝假绿与假红；十项上线检查清单、证书签名五坑规避、四条 AC 量化门禁、审核 / 合规六项风险提前告知齐备。团长形式终裁 ADR-16~20 已全部并入，**落盘前硬阻断 ③ 已解除**；执行前仍须确认 G0-a READY 与哈希口径 A 前后同口径。

[上线发布方案]
