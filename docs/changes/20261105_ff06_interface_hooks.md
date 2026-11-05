# FF06 · 现有脚本接入点留痕与接口契约（2026-11-05）

> **状态**：Phase 1 设计层留痕文档。本轮**只新建本文件**，未改任何既有文件、未执行任何系统修改命令、未 commit。
> **委派**：team-lead W-09｜**归属**：fullstack-engineer（后续维护归属；本轮由团长单写者统一落盘）
> **上游依据**：`[技术研发方案]`（§4 接口契约 / §10 接入点 / §11 冲突上报）、`docs/plans/20261105_data_backup_and_task_governance_plan.md`（治理方案）
> **只读取证**：`quant_backtest/miss_check.py`、`ops/db_dump_daily.py`、`tools/b4_restore_drill.py`、`jev-eval/backup_daily.ps1`、`news_collector/alert_fail_wrap.py`、`ops/test_health_check.py`
> **关联裁决**：团长 C-1~C-6；ADR-02 / 03 / 05 / 06 / 07 / 10 / 11 / 15 / 21；D0~D6 全部未批准

---

## 0. 文档定位与执行边界

本文件把「阶段 0 立台账收口径 + 阶段 2 质量闸门」代码/文档层设计所识别的**既有脚本接入点**固化为单一留痕，供实施期接线、质量测试官写用例、上线发布师定门禁、运维守护师建基线时引用。

**本轮硬边界（逐条声明，全部满足）**：

| # | 约束 | 本文件状态 |
|---|---|---|
| 1 | 不新增/修改/启用/禁用任何 Windows 计划任务 | 满足 |
| 2 | 不删除或改名任何现有文件 | 满足 |
| 3 | 不改 `ops/db_dump_daily.py` 的 `DUMPS` 与 `KEEP_DAYS` | 满足 |
| 4 | 不改写 `backups/db` 下任何既有产物内容 | 满足 |
| 5 | 不执行 `.enc` 解密演练 | 满足 |
| 6 | 不触碰生产服务器 10.205.0.118 | 满足 |
| 7 | 只写本文档一个绝对路径，不 commit | 满足 |

**Python 口径**：本机无 `python3` 别名，统一 `"C:\Program Files\Python311\python.exe" -X utf8`。
**PowerShell 口径**：5.1，写日志必须显式 `-Encoding UTF8`；`*>>` / `1>>` / `2>>` 重定向会写 UTF-16LE。

> **重要**：以下 16 条接入点的「本轮是否改」**一律为「否」**。它们是**设计层留痕**，不代表运行态已生效（ADR-02 禁止把「设计完成」写成「运行态已生效」）。实际改线一律排在 D1/D3/D4/D0 裁定之后的实施期。

---

## 1. A1~A16 接入点逐条留痕

### 1.1 留痕字段规范

每条接入点登记 11 个字段：

```
id | file | symbol | lines | caller | callee | args | returns | failure_exit
   | proposed_change | in_scope_this_round(=no) | decision_ref | owner | evidence
```

### 1.2 逐条明细

#### A1 · `quant_backtest/miss_check.py::REGISTRY`（74–153）

- **调用方**：`miss_check.run()`（276–364）遍历 `REGISTRY.items()`；实施期由 `ops/task_inventory.py::suggest_registry()` 产出候选。
- **被调方**：模块级常量 `REGISTRY: dict`（23 条，覆盖 18 个自定义任务）；非函数，无调用栈。
- **传参**：无（常量读取）。候选生成侧：`suggest_registry(snapshot: dict, *, include_disabled: bool = False) -> dict`。
- **返回**：`{name: {"expect": "HH:MM", "since": "YYYY-MM-DD", ...}}`；候选输出 `registry_candidates` JSON（顶层 `applied: false` 硬编码、`policy: "candidates-only"`、`candidates[].needs_human_confirm: true`、`write_target` 仅说明字符串）。
- **失败出口**：快照损坏 → `REGISTRY_REGEN_SKIPPED` + 保留既有 REGISTRY；查不到任务时 `run()` 回退短名（310–312）。`CainiaoPull` 19:00 未排班，保留在册继续报 MISS（设计意图，不得自动移除）。
- **建议改法（实施期）**：由 `suggest-registry` 产出候选 → 团长汇总 → 老田逐条确认 `expect` → D1/D2 裁定后由团长写入；补 `\JevRestoreDrill` / `\BackupDaily` / `\ResticTrialDaily`，`DbDumpDaily` 保持 `pending` 避免假 MISS。
- **本轮是否改**：**否**。`expect` 必须人工确认——`miss_check.py` docstring（8–10 行）明确「显式 expect 可审计，优于从 NextRun 反推」，故**不得**从 Triggers/NextRun 自动生成后写回。
- **关联**：C-2 / ADR-06 / ADR-10 / ADR-11 / D1 / D2。

#### A2 · `miss_check.py::_task_last_run`（156–181）

- **调用方**：`miss_check.run()` 第 310 行。
- **被调方**：`subprocess.run(["schtasks", "/query", "/tn", name, "/v", "/fo", "list"], capture_output=True, timeout=20)`。
- **传参**：`name: str`（短名，或 `cfg["path"]` 完整路径；子路径任务如 `\ZeroBound\TdxSync_*` 必须传完整路径）。
- **返回**：`datetime | None`（`None` = 从未运行）。
- **失败出口**：任何异常 → `None`；bytes 捕获 + `utf-8` / `mbcs` **双解** + 中英双 regex（`Last Run Time:` / `上次运行时间:`）；`startswith("1999/")` 排除从未运行哨兵。
- **建议改法（实施期）**：`task_inventory.query_schtasks()` 复用同一双解 + 中英双 regex 口径；**不抽取公共模块**（避免改动本文件）。
- **本轮是否改**：**否**。注意：本函数**只有 utf-8/mbcs 双解，无 UTF-16LE 分支**；UTF-16LE 属 `alert_fail_wrap.normalize_err_log()` 既有 wrapper 域。
- **关联**：—。

#### A3 · `miss_check.py::run` 告警调用（276–364，关键 350–359）

- **调用方**：`miss_check.run()` 检测到 MISS 时。
- **被调方**：`news_collector/alert_fail_wrap.py`（`subprocess.run`，timeout=30）→ `alert_fail.py` → 飞书。
- **传参**：`[sys.executable, ../news_collector/alert_fail_wrap.py, "miss_check", "MISS×%d" % len(miss), json.dumps(miss, ensure_ascii=False)]`。
- **返回**：`res` dict 原子写入 `data/miss_check_last.json`（`mkstemp` + `os.replace`，342–346）；有 MISS 推告警、全绿静默 exit 0。
- **失败出口**：`alert_fail` 调用异常 → 打印 `alert_fail 调用失败: ...`，不反噬任务 rc；wrapper 自身恒 `return 0`。
- **建议改法（实施期）**：第三参应传**真实 UTF-8 日志文件路径**（当前传 JSON 字符串被静默丢弃，见 §3 TD-08）；本轮仅登记。
- **本轮是否改**：**否**（缺陷登记 TD-08，本轮不修既有告警链）。
- **关联**：C-6 / TD-08 / IT-01。

#### A4 · `ops/db_dump_daily.py::DUMPS`（49–69）

- **调用方**：`main()` 第 163 行 `for item in DUMPS:`。
- **被调方**：模块级常量 `DUMPS: list[dict]`（5 项：3 个 `must=True` + 2 个 Neo4j `must=False`）。
- **传参**：每项 `{key, container, cmd, file, must}` 传入 `dump_one(item, out_dir)`。
- **返回**：无（常量）；`main()` 汇总 `manifest["items"]`。
- **失败出口**：`must=True` 项失败 → `n_must_fail += 1` → `sys.exit(1)`；全失败 → `exit(2)`。Neo4j `must=False` 失败属设计内。
- **建议改法（实施期）**：阶段 1 选 A 时把 `zhice_pg` 改为可配置开关（默认关闭），消除与主链的重复 dump（省 830s/天，硬前置 D0 解密演练 rc=0）。
- **本轮是否改**：**否（禁改）**。
- **关联**：D1（L2）/ 治理方案 §三阶段 1 选项 A。

#### A5 · `db_dump_daily.py::KEEP_DAYS`（43）

- **调用方**：`main()` 第 179 行 `rotate(KEEP_DAYS)`。
- **被调方**：模块级常量 `KEEP_DAYS = 14`。
- **传参**：`rotate(keep_days: int)`。
- **返回**：无（常量）。
- **失败出口**：轮转删除走 `SHFileOperationW FOF_ALLOWUNDO` 回收站，删除后逐个 `os.path.exists` 复核；失败则保留并告警（宁多留不误删）。
- **建议改法（实施期）**：口径收口为 **14**（Q3：`git log -S"KEEP_DAYS = 60"` 为空，60 从未入版本库）；`--keep-days` 参数从未实现（Q4），docstring 已改。
- **本轮是否改**：**否（禁改）**。
- **关联**：D3（L2）/ Q3 / Q4。

#### A6 · `db_dump_daily.py::dump_one`（82–104，关键 92–101）

- **调用方**：`main()` 第 164 行。
- **被调方**：`gzip.open(dst, "wb")` 收流 + `subprocess.run(["docker","exec",container,"sh","-c",cmd], stdout=f, stderr=PIPE, timeout=900)`。
- **传参**：`item: dict, out_dir: str, timeout: int = 900`。
- **返回**：`(ok: bool, size: int)`；`ok = p.returncode == 0 and size > 200`。
- **失败出口**：异常 → `(False, 0)`；`ok=False` 时 `main()` 删除空壳文件（171–172）。
- **建议改法（实施期）**：收流关闭后插入 `v = backup_format_gate.verify_artifact(dst)`，改为 `ok = p.returncode == 0 and size > 200 and v["ok"]`，并把 `format/magic/sha256/recoverable/checked_at/reason` 带入返回值。
- **本轮是否改**：**否**（C-3：接入排 D1/D3 之后）。
- **关联**：C-3 / 阶段 2-1 / R3。注：10-29 坏档是历史产物，K-STOCK-414 后本函数已用 `gzip.open` 收流（11-05 起正确）；闸门是防「再次静默标 ok=true」的第二道锁，不是修历史文件。

#### A7 · `db_dump_daily.py::main` manifest 写入（161–178，关键 165–166 / 177–178）

- **调用方**：`__main__`（本脚本当前**无自动排班**，492 个计划任务中 0 个引用）。
- **被调方**：`dump_one()` → `json.dump(manifest, ...)` → `rotate(KEEP_DAYS)`。
- **传参**：无（`main()` 不解析 `sys.argv`）。
- **返回**：`sys.exit(2 if n_ok == 0 else (1 if n_must_fail else 0))`。
- **失败出口**：manifest 现有字段 `{date, items:[{key, ok, size, file, must}], ok_count, must_fail, total}`；10-29 三个非 gzip 文件仍标 `ok=true` 即 R3 根因。
- **建议改法（实施期）**：`manifest["items"].append(gate_entry(item_dict, out_dir))`，**只增键不改名不删键**（保留 `key/ok/size/file/must`，新增 `format/magic/sha256/recoverable/checked_at/reason` + `schema_version/gate_version`）；存在 `must and not ok` 项时按 §3 契约推飞书。
- **本轮是否改**：**否**（C-3）。
- **关联**：C-3 / 阶段 2-1 / R3。

#### A8 · `db_dump_daily.py::rotate`（135–150）

- **调用方**：`main()` 第 179 行。
- **被调方**：`recycle_delete(paths)`（107–132，`ctypes` + `SHFileOperationW`）。
- **传参**：`keep_days: int`（当前常量 14）。
- **返回**：`None`（就地删除）。
- **失败出口**：`leftover` 非空时逐个 `log("警告：回收站删除失败，已保留 ...")`。
- **建议改法**：**不动**。轮转天数待 D3 裁定；首次排班会移走 10-09~10-15 / 10-29 留痕，建议先另存证据目录。
- **本轮是否改**：**否**。
- **关联**：D3。

#### A9 · `tools/b4_restore_drill.py::list_backups`（87–98）

- **调用方**：`main()` 第 215 行 `backups = list_backups()`。
- **被调方**：`BACKUP_ROOT.iterdir()`（`C:\项目\老田的AI助手\backups\db`）。
- **传参**：无。
- **返回**：`[(date_str, sql_gz_path)]`，**只收集目录名匹配 `\d{4}-\d{2}-\d{2}` 且内含 `quant_pg_alldb.sql.gz` 的目录**，按日期倒序；`main()` 默认取 `backups[0]`（最新目录，当前 11-05）。
- **失败出口**：`BACKUP_ROOT` 不存在 → `[]` → `P0-no-backup-dir` rc=2；`--date` 未匹配 → `P0-backup-not-found` rc=2。
- **建议改法（实施期）**：自动过滤待 **D4 裁定后实施期**接入 `should_select(verify_artifact(p))`，把 `format != "gzip"` 的目录排除。
- **本轮是否改**：**否**（**C-1 / ADR-05 / W-12 §2.2 S5 判据 2**）。**本轮不承诺代码自动过滤、不写死「可选备份数=0」**。本函数**不做任何格式过滤**，也**不是**「可恢复候选集」的全量枚举。
- **⚠ 因果链（勿写反）**：10-29 不被默认演练选中，仅由 `b4_restore_drill.py::list_backups()` **按日期倒序 + `main()` 取 `backups[0]`（=11-05，魔数 1f8b 已验证）**保证，**与 `INVALID_GZIP.flag` 无因果关系**；显式 `--date 2026-10-29` 时由人工看 flag 拦截。flag 是隔离留痕（mark-only-do-not-delete）+ 显式 `--date` 时人工拦截凭据 + 审计留痕，**不是运行态自动过滤闸门、不改变 `list_backups()` 行为**。**不得写死「`list_backups()` 返回可选数 = 0」**（`backups/db` 底账 9 个目录，字面不成立）。
- **关联**：C-1 / ADR-05 / D4 / T-11（已收敛）。

#### A10 · `b4_restore_drill.py::gzip_magic` / `gzip_stream_ok` / `sha256_of`（101–103 / 106–113 / 183–191）

- **调用方**：`main()` 第 243 行 `magic = gzip_magic(sql_gz)`、第 248 行 `ok, why = gzip_stream_ok(sql_gz)`、第 298 行 `sha256_of(dst)`；`count_copy_rows()` 与 `restore()` 亦用 `gzip.open`。
- **被调方**：`gzip.open(path, "rb")` / `hashlib.sha256()`。
- **传参**：`path`；`gzip_stream_ok(probe: int = 8 << 20)`；`sha256_of(chunk: int = 1 << 20)`。
- **返回**：`bytes`（前 2 字节）；`(bool, str)`；`str`（hexdigest）。
- **失败出口**：`gzip_stream_ok` 捕获所有异常 → `(False, str(exc)[:200])`；`main()` 据此写 `P1-gzip-stream-broken` rc=1。
- **建议改法（实施期）**：改为薄封装 `from ops.backup_format_gate import read_magic, stream_probe, sha256_of`，消除「同一规则多处实现」（R3 延伸）。
- **本轮是否改**：**否**。
- **关联**：阶段 2-2 / R2。sha256 口径 = **产物 `.gz` 文件字节**，非解压后 SQL 明文。

#### A11 · `b4_restore_drill.py::main` P1-a 校验段（242–252）

- **调用方**：季度/月度演练调度 `\JevRestoreDrill`（每月 1 日，只验 `quant_pg`）。
- **被调方**：`gzip_magic()` → `gzip_stream_ok()` → `count_copy_rows()`。
- **传参**：`--date` / `--table` / `--max-age-days` / `--keep-container` / `--no-snapshot` / `--json`。
- **返回**：`res` dict（`magic` / `rows_backup` / `rows_restored` / `md5` / `findings` / `rc`），结论行写 `C:\Temp\kg_sync_task.log`。
- **失败出口**：魔数非 `1f8b` → `P1-gzip-regression` rc=1；流坏 → `P1-gzip-stream-broken` rc=1；过期 → `P1-stale-backup` rc=1。
- **建议改法（实施期）**：登记为与闸门等价的重复实现；扩面到「当日全部必保产物」+ 每月 `.enc` 解密抽检需 **D0** 批准。
- **本轮是否改**：**否**（本轮 `.enc` 演练执行 = 0，不实跑 `Unprotect-File`）。
- **关联**：D0 / 阶段 2-2 / Q7。

#### A12 · `jev-eval/backup_daily.ps1::Test-Gzip`（332–348，调用 512–515）

- **调用方**：section 2 第 513 行 `foreach ($f in @(Get-ChildItem $DayDir -Filter "*.gz"))`。
- **被调方**：`[IO.Compression.GZipStream]` **全量**解压至 EOF。
- **传参**：`[string]$path`。
- **返回**：`[bool]`。
- **失败出口**：0 字节先拒（OPS-1011）；任何异常 → `$false` → `LogFail ("GZIP " + $f.Name)` → `$script:Failed++`。
- **建议改法**：**保持**。加密主链容器内 `pg_dump | gzip -c` 产物形态与副链宿主 gzip 收流不同，已有全量解压校验，**不套用 Python 8 MiB 探针**。
- **本轮是否改**：**否**。
- **关联**：治理方案 §1.1。

#### A13 · `backup_daily.ps1::Unprotect-File`（302–330）

- **调用方**：**当前无任何自动化调用**——只有定义（302）与文档注释（14 行「Manual restore of .enc」）；Q7 真风险：加密主链从未做过 `.enc` 解密恢复演练。
- **被调方**：`Get-AesKey` → `HMACSHA256` 校验 → `AESCryptoServiceProvider`（CBC/PKCS7）解密 → `[IO.File]::WriteAllBytes($dst, $pt)`。
- **传参**：`[string]$src, [string]$dst`。
- **返回**：无（写文件）。
- **失败出口**：`$data.Length -lt 49` → `throw "file too short"`；HMAC 逐字节比对不符 → `throw "HMAC mismatch"`。
- **建议改法（实施期）**：D0 解密演练的唯一调用点；演练须只读、在临时目录、产物用完即删；流程为 `Unprotect-File → gzip 校验 → 行数抽样 → rc`。
- **本轮是否改**：**否（绝对禁止执行 `.enc` 解密演练）**。
- **关联**：D0 / Q7 / FF11。

#### A14 · `backup_daily.ps1::Get-AesKey` / `Protect-File`（257–276 / 278–300）

- **调用方**：section 3 第 518–533 行 `foreach ($name in $EncryptTargets)`（当前 `$EncryptTargets = @("zhice_db.sql.gz")`）。
- **被调方**：`Get-AesKey`（`$AesKeyFile = Join-Path $env:USERPROFILE ".backup_aes_key"`，44 B Base64 = 32 B 密钥）；`Protect-File` 输出 `IV(16) + CT + HMAC(32)` 格式的 `.enc`。
- **传参**：`[string]$src, [string]$dst`。
- **返回**：无（写 `.enc`，成功后 `Remove-Item $src` 删除明文）。
- **失败出口**：密钥缺失且历史 `.enc` 存在 → 写 `BACKUP_KEY_LOST_<stamp>.flag` + `throw ABORT`（OPS-1001d F2，禁止静默轮换新 key）；ENC 异常 → `LogFail`。
- **建议改法**：FF11 安全审计项（ACL / 无轮换 / 无 KMS / 同一 key 兼做 AES-CBC 与 HMAC-SHA256 / 离线纸质副本），**仅登记不改**。
- **本轮是否改**：**否**。
- **关联**：FF11 / Q7。

#### A15 · `backup_daily.ps1` 第 498 / 509 行（`1>>` / `2>>` 重定向）

- **调用方**：section 1f（KB corpus 备份）、section 1g（P3 网络探测）。
- **被调方**：`& $kbPy $kbScript 1>> 'C:\Temp\kb_corpus_backup_out.log' 2>> '...err.log'`。
- **传参**：Python 绝对路径 + 脚本路径。
- **返回**：`$LASTEXITCODE`。
- **失败出口**：section 1f rc≠0 → 写 `KB_CORPUS_BACKUP_FAILED.flag`（`-Encoding utf8`）。
- **建议改法（实施期）**：PS 5.1 的 `*>>` / `1>>` / `2>>` 会写 **UTF-16LE**，下游按 utf-8 读会乱码（`alert_fail_wrap.py` 注释 10–12 行已记录同类坑）；改 `Out-File -Append -Encoding utf8` 或 `Add-Content -Encoding UTF8`。
- **本轮是否改**：**否（登记不修）**。
- **关联**：编码口径 / §3。

#### A16 · `backup_daily.ps1` 告警旗（570–587）

- **调用方**：主流程末尾 `if ($script:Failed -gt 0)`。
- **被调方**：`Set-Content -LiteralPath $flag -Encoding UTF8` 写 `jev-eval\alerts\BACKUP_FAILED_<stamp>.flag`；由 `AlertPush` 转发任务推飞书。
- **传参**：`$flagLines`（`type` / `stamp` / `failed_count` / `failed_item=` × N / `backup_log=`）。
- **返回**：无（写 flag）。
- **失败出口**：`catch` → `Log ("ALERT flag error: ...")`，不影响主流程退出码。
- **建议改法**：复用既有 flag + `AlertPush` 飞书链路，**不自建告警客户端**；新模块若需告警，按 §3 契约调用 `alert_fail_wrap.py`。
- **本轮是否改**：**否**。
- **关联**：§1.2 技术选型（告警复用）。

### 1.3 接入点总览表

| # | 文件:符号:行号 | 建议改法（实施期） | 本轮是否改 | 关联裁定 |
|---|---|---|---|---|
| A1 | `miss_check.py::REGISTRY` 74–153 | 候选 → 团长汇总 → 老田确认 expect → D1/D2 后写入 | **否** | C-2 / ADR-10 |
| A2 | `miss_check.py::_task_last_run` 156–181 | `query_schtasks` 复用双解口径，不抽公共模块 | **否** | — |
| A3 | `miss_check.py::run` 350–359 | 第三参改真实日志路径（TD-08 登记） | **否** | C-6 / TD-08 |
| A4 | `db_dump_daily.py::DUMPS` 49–69 | 选 A 时 `zhice_pg` 做开关（禁改） | **否** | D1 |
| A5 | `db_dump_daily.py::KEEP_DAYS` 43 | 口径收口 14（禁改） | **否** | D3 |
| A6 | `db_dump_daily.py::dump_one` 92–101 | 插 `verify_artifact`，`ok` 与 `v["ok"]` 相与 | **否** | C-3 |
| A7 | `db_dump_daily.py::main` 165–178 | `gate_entry()` 只增键 + 失败推飞书 | **否** | C-3 |
| A8 | `db_dump_daily.py::rotate` 135–150 | 不动 | **否** | D3 |
| A9 | `b4_restore_drill.py::list_backups` 87–98 | 自动过滤待 **D4 裁定后实施期**接入 `should_select(verify_artifact(p))`；**本轮不承诺代码自动过滤、不写死「可选备份数=0」** | **否** | C-1 / ADR-05 / W-12 §2.2 S5 |
| A10 | `b4::gzip_magic/stream_ok/sha256_of` 101/106/183 | 薄封装复用 gate | **否** | 阶段 2-2 |
| A11 | `b4::main` P1-a 242–252 | 登记重复实现，扩面待 D0 | **否** | D0 |
| A12 | `backup_daily.ps1::Test-Gzip` 332–348 | 保持主链全量校验 | **否** | — |
| A13 | `backup_daily.ps1::Unprotect-File` 302–330 | D0 唯一调用点，本轮禁执行 | **否** | D0 / Q7 |
| A14 | `backup_daily.ps1::Get-AesKey/Protect-File` 257–300 | FF11 审计登记，不改 | **否** | FF11 |
| A15 | `backup_daily.ps1` 498 / 509 | 改 `-Encoding utf8`（登记不修） | **否** | 编码口径 |
| A16 | `backup_daily.ps1` 告警旗 570–587 | 复用既有 flag + AlertPush | **否** | — |

---

## 2. FF 编号对照表（FF01~FF12）

### 2.1 声明（ADR-07 / ADR-20）

> **FF 编号是 forge-lead 错误预防对照表的「团队交付物编号」，不是 `C:\aios` 仓库既有文件编号。**
> 只读取证：对 `C:\aios` 执行 `Grep "FF06|FF11"` **无任何匹配**；product-scout 亦确认 FF01/FF02/FF09 同属团长任务包的团队交付物编号。落盘命名沿用既有 `docs/changes/20261105_*.md` 体系（本文件 = `20261105_ff06_interface_hooks.md`），由团长统一编号与登记对照表。

**按 ADR-20 / W-12 §2.2 S5 判据 1 补充（重要）**：

- 本文档中的 **FF01~FF12、A1~A16、D-4~D-7、C-1~C-6、IT-01、T-11 等全部编号，均为方案内自定义编号**，既**非仓库既有文件编号**，也**不作为白名单依据**。
- **本轮唯一有效的落盘白名单是 GOV-W 终表**（团长维护）。某个编号出现在本文档，不代表该编号对应的文件被允许新建或改动；是否可落盘一律以 GOV-W 终表为准。
- 本文档（W-09）自身也仅在 GOV-W 终表内方为有效委派；本文件不新增任何白名单条目。

### 2.2 对照表（本轮可核实范围）

| 编号 | 本轮协作中可核实的语义 | 对应本项目产出 / 接入点 |
|---|---|---|
| FF01 | 团长任务包团队交付物编号（product-scout 与 FF02/FF09 并列提及）；具体条款本轮未展开 | 待团长对照表确认 |
| FF02 | 「本机即生产环境 + 单人运维 + 492 计划任务并存，勿盲目引入 FastAPI/看板/云」——反过度设计 / 单机形态约束 | 技术方案 §1 选型、§2 模块化单体判据；A4~A8 |
| FF03 | 本轮协作消息未见明确定义 | 待团长对照表确认 |
| FF04 | Phase 2 测试保障方案「开发前门禁」 | 技术方案 §9.1 基线、§9.3 单测清单；qa-tester 用例集 A/B 册 |
| FF05 | 运维保障基线（ops-sre v1.1 衔接修订） | 技术方案 §7 执行环境差异、§9.1 非功能性承诺 |
| **FF06** | **接入点留痕与接口契约（本文件）** | **A1~A16 全表 + §3 TD-08 + §4 实施期清单** |
| FF07 | 本轮协作消息未见明确定义 | 待团长对照表确认 |
| FF08 | 本轮协作消息未见明确定义 | 待团长对照表确认 |
| FF09 | 「必须当期 / 可延后」范围分期 + 「架构不过度设计」 | 技术方案 §9 必须当期/可延后、§2 反过度设计声明 |
| FF10 | 本轮协作消息未见明确定义 | 待团长对照表确认 |
| FF11 | 安全审计提示：数据库 dump 明文落盘 / AES 密钥 `~/.backup_aes_key` / 异地 SFTP 凭据 | 技术方案 §8；本文件 §7；A13 / A14 / A16 |
| FF12 | 治理动作逐项核对（A0-x / A2-x 覆盖核对） | 技术方案 §10 接入点与阶段 0/2 动作对应表；A1/A4/A6/A9/A11/A13 |

> FF01/FF03/FF07/FF08/FF10 五项目前**无本轮可核实的明确定义**，按「不臆造」原则留待团长《编号命名空间对照表》确认，不编造条款内容。

### 2.3 命名空间消歧（ADR-21，避免 Phase 3~5 转交误读）

至少 8 套编号并存，**同名不同义**：

| 命名空间 | 含义 | 归属 |
|---|---|---|
| `F1–F5` | 拟新增**文件**编号（`ops/task_inventory.py` / `ops/backup_format_gate.py` / 两个 tests / fixtures） | fullstack-engineer；团长「单写者统一新建 F1–F5」同此套 |
| `F-01~F-11` | PRD **功能**编号（最终版 F-06=闸门、F-03=三处口径、F-09=演练扩面、F-04=坏档隔离） | product-manager |
| `AC-01~04` ⇔ `AC①~④` | 本轮四条可达验收 ⇔ QA 用例编号（新四条：盘点一致 / 坏档不被选中且不删不改 / 三处口径自洽 / 关键链设计完成） | team-lead / qa-tester |
| `M1~M6` | 远期成功度量（本轮降为目标态） | product-manager |
| `REQ-001~007` | 需求池 | product-scout |
| `A0-x` / `A2-x` | 治理方案阶段 0 / 阶段 2 动作 | 治理方案 |
| `D0~D6` | 待裁定清单（全部未批准）；`D-4~D-7` 为实施期接口补充项编号（不同命名空间） | team-lead |
| `C-01~08` / `E-01~11` / `R-xx` / `Q1~Q9` / `T-11` / `IT-01` / `C-1~C-6` | PRD 命令 / 事件 / 风险 / 治理方案问题 / QA 待确认项 / 实施期必测项 / 团长冲突裁决 | 各域 |

**旧 AC 编号（初稿 AC-01~AC-11）全部作废**，归档引用须标注「旧 AC-xx」。映射：旧 AC-01→新 AC-01；旧 AC-02/03→并入新 AC-04；旧 AC-04→新 **AC-02**；旧 AC-06→新 AC-03；旧 AC-05/07/08/09/10/11→B 册实施期契约或作废。

---

## 3. TD-08 专项 · 告警链路第三参契约缺陷

### 3.1 事实（只读取证）

`news_collector/alert_fail_wrap.py::main(argv)` 的契约为 `[task, code, raw_log]`：

- 第 163 行 `raw_log = argv[2] if len(argv) > 2 else ""`；
- 第 167 行 `norm_log = normalize_err_log(raw_log)`，而 `normalize_err_log()`（41–54）第一步即 `if not log_path or not os.path.isfile(log_path): return ""`——**第三参是「错误日志文件路径」，不是告警正文**；
- 第 169 行 `[sys.executable, "-X", "utf8", ALERT_FAIL_PY, task, code, norm_log]`；
- 第 218 行 `return 0`（观测层永远不反噬调用方 rc）。

**既有缺陷**：`miss_check.py:353-357` 把 `json.dumps(miss, ensure_ascii=False)` 作为第三参传入 → 被 `normalize_err_log()` 判定为非文件路径 → 返回 `""` → **MISS 详情静默丢弃**，告警正文实际只在 `code` 字段（如 `"MISS×3"`）里。

### 3.2 实施期强制契约（F1 / F2 必须遵守）

1. 第三参传**真实 UTF-8 日志文件路径**（`tempfile` 落一份 `.log`；由 `normalize_err_log()` 负责 BOM / UTF-16LE 嗅探转码）。
2. 摘要走 `code` 字段：**单行、无 `|`、≤120 字符**（适配 `_log_field()` 的 `|`/换行替换与 240 字符截断）。
3. **不得**把 JSON / 正文塞入第三参；正文不含 dump 内容、连接串、容器 env 凭据。
4. 告警投递与 `verify_artifact.ok` **解耦**：`ok` 仅由「魔数 `1f8b` / 前 8 MiB 流式解压 / sha256」三项决定，飞书不可达记 `ALERT_PENDING` 本地降级，**不翻转 `ok`**。

### 3.3 本轮处置

- **本轮不修**既有告警链（`miss_check.py` / `alert_fail_wrap.py` 均零改动）。
- 登记为 **IT-01 实施期契约测试必测项**：断言第三参为 `isfile` 为真的 UTF-8 `.log` 路径、`code` 字段含非空摘要（单行 / 无 `|` / ≤120 字符 / 无凭据）。
- **不进本轮四条 AC、不进本轮门禁、零真实飞书推送**（团长终裁 ADR-02：写进 AC 即「纸面达标」；本轮不接 `db_dump_daily.py`、不跑真实备份、不推告警）。
- QA 侧 BG-E12 保持 mock 级并标注「仅设计核对，不进门禁」。

---

## 4. 实施期必测项 / 接口补充清单（D-4~D-7，**本轮不实现**，ADR-15）

> 团长终裁：不单独出增量接口契约文档，避免再产生一份需维护的文档与范围蔓延。以下全部登记进全案文档「实施期接口补充清单」，属**设计层**，本轮不进代码、不验运行态。

| 项 | 内容 | 口径 | 本轮 |
|---|---|---|---|
| **D-4** | `DiffResult.change_type` **五类变更** | `added / removed / state_changed / rc_changed / schedule_changed`（排除 `attribution_changed`，本轮不做分诊处置） | 不实现 |
| **D-4** | **7 态状态标签 ↔ 退出码映射** | `rc=0`：`OK` / `DIFF` / `PENDING`；`rc=1`：`ALERT` / `ALERT_PENDING` / 数据校验失败；`rc=2`：`PERMISSION_DENIED` / `EMPTY_SNAPSHOT` / 环境不可用。原则：rc 只反映「命令是否完成」，不反映「数据是否健康」；`PENDING` 为 JSON 体内 `status` 标签（**rc=0**，`suggest-registry` 正常产出候选即命令成功） | 不实现 |
| **D-5** | 台账残留字段 | `attribution → status_tag`（仅可观测性登记、不定性、不处置）、`is_key_chain`、`source_snapshot_id`、`codepoint_verified`（布尔标志承载）、`key_chains{active:3,pending:1}`、`coverage_matrix` —— 归 v0.1 未来态 | 不进本轮代码 |
| **D-6** | `ALERT_PENDING` 降级标签 | 显式登记于 gate 输出；与 `ok` 解耦（见 §3.2 第 4 条） | 不实现 |
| **D-7** | F-07 三件设计件 | ① 必保清单 schema（分母=5、zerobound 标 `pending_decision:D5` 不虚构）② `list_backups` 从「只验 quant_pg」扩到「当日全部必保产物」且强制先过隔离 + 闸门 ③ 每月 `.enc` 解密抽检 SOP（`Unprotect-File → gzip 校验 → 行数抽样 → rc → D0 报备入口 → 回滚`） | **只定义不执行**；本轮 `.enc` 演练执行 **0 次** |
| **IT-01** | 告警契约测试 | 见 §3.3 | 实施期必测，不进本轮 AC |
| **A9 过滤** | `should_select(verify_artifact(p))` 接入 | D4 裁定后才实施；本轮不承诺代码自动过滤 | 不实现 |

---

## 5. F1 / F2 只读模块声明（ADR-06 / ADR-10）

### 5.1 `ops/task_inventory.py`（F1）

- **CLI**：5 个 argparse **子命令** `export / snapshot / diff / stats / suggest-registry`（ADR-10 以技术契约为准；PRD C-01 的 flag 式 `--regen-registry` 隐含「写回 REGISTRY」，与只读契约语义相反，已由 PM 对齐）。
- **只读证据**：`query_schtasks()` 只调 `schtasks /query`（bytes 捕获 + utf-8/mbcs 双解）；全部产物写 `ops/data/*.json`（运行时产物，不入 git）。
- **无 `--apply`**、无 `--host` / `--upload` / `--remote`；静态扫描 `Register-ScheduledTask` / `Set-ScheduledTask` / `Disable-ScheduledTask` / `Enable-ScheduledTask` / `New-ScheduledTask*` 出现次数 = **0**。
- **无任何写 `miss_check.py` 的代码路径**：`suggest_registry()` 输出顶层 `applied: false` 硬编码、`write_target` 仅说明字符串、`policy: "candidates-only"`；单测以 monkeypatch 锁死 `open` / `os.replace` 不被调用。
- **注入口**：`export --source file --file PATH`（等价库函数 `query_schtasks(csv_path=...)`）；可 patch 符号 = 模块级 `query_schtasks`，**无** `_run_schtasks` 私有 runner（`subprocess.run` 为内部实现细节，不作契约）。

### 5.2 `ops/backup_format_gate.py`（F2）

- **纯函数库 + CLI `verify PATH [--json] [--no-sha256]`**：`read_magic` / `stream_probe(probe=8<<20)` / `sha256_of(chunk=1<<20)` / `detect_format` / `verify_artifact` / `gate_entry` / `should_select` / `write_quarantine_flag(..., apply=False)`。
- **只读证据**：`verify_artifact()` 只 `open(path, "rb")`；所有 I/O 异常收敛为 `ok=false, reason=...` 的 verdict，**绝不抛穿 main 造成「没写 manifest 但 rc=0」**。
- `write_quarantine_flag()` 默认 `apply=False`，只返回「将写入」的 flag 内容与目标路径，**不落盘**；flag 内容以团长 C-1 的 **8 字段**为准（`type / date / files / format=plain-sql / recoverable=false / detected_magic=2d2d / policy=mark-only-do-not-delete / decision_ref=D4`），`manifest_patch` 不作本轮 flag 内容（禁改既有 `manifest.json`）。
- `gate_entry()` 返回 **NEW dict**，保留既有 `key/ok/size/file/must` 五键，只增键、不改入参。
- 常量：`GZIP_MAGIC = b"\x1f\x8b"`、`PROBE_BYTES = 8 << 20`、`SHA256_CHUNK = 1 << 20`；`format` 合法值 5 个 `gzip | plain-sql | encrypted-aes | empty | unknown`；`magic` 为**无空格小写 hex**（`magic.hex()`，`"2d2d"` 而非文档字面 `2d 2d`）；`reason` 为自由文本非枚举。
- **判定以魔数/字节为准，不靠后缀**（10-29 教训：后缀 `.gz` 内容是纯 SQL）；`encrypted-aes` 为启发式（`magic != 1f8b` + 长度 ≥ 49 + 后缀 `.enc`），且 **绝不调用任何解密**（D0 前禁用）。

### 5.3 分类契约修正（团长实测，已固化）

```python
SYSTEM_PATH_PREFIXES = ("\\Microsoft\\", "\\ZeroBound\\", "\\AIOS-")   # 三前缀，startswith(tuple)
NEVER_RUN_SENTINELS = ("1999-11-30", "1999/11/30",
                       "1999/11/30 00:00:00", "1999-11-30 00:00:00")
SCHEDULER_NOISE_RC = frozenset({267009, 267011, 2147943140, 1073807364})
# classify 顺序：never_run → noise → system → real_failed → disabled → running → ready
```

- 单前缀 `\Microsoft\` 实测得 system 180 / custom 312 / real_failed 36（**错误**）；三前缀才得 199 / 293 / 32 / 24（与治理方案 §1.3 golden 一致）。`\ZeroBound\` 下 19 个中文分片必须计入系统侧。
- `real_failed` 前置于状态机分支，避免「Ready/Disabled 且 rc≠0」（如 AlertWatch rc=3、AgentLegionHeartbeat rc=2）被吞掉。
- **`compute_stats` 两套口径分离**：`by_state{Ready:407,Disabled:76,Running:9}` 与 `custom_disabled:45` 按**原始 `state` 字段**统计，**不得**从 `category` 反推；`custom_real_failed:32` / `custom_never_run:24` 按 `category` 且显式 `not is_system(path)` 过滤（never_run 分支在 system 之前）。golden 单测分两组断言。
- **路径归属与哨兵关键链正交**：`\ZeroBound\TdxSync_A_高频 / B_财务 / C_低频 / D_缺口` 四片在 `miss_check.REGISTRY`（137–152）是**被监控的自定义业务关键链**，三前缀后 `is_system=true`；`suggest_registry()` **不得单按 `is_system` 过滤候选**，须保留「已在 REGISTRY 中」白名单。

### 5.4 `snapshot_id` 不变式

```
snapshot_id = sha256(canonical_json(sorted(tasks, key=path)))[:16]
canonical_json = json.dumps(..., ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
```

1. 只哈希规范化后的 **`tasks` 集合**；快照头 `generated_at / source / schema_version / counts / classification_rules` **不参与**。
2. 任务自身的 `created_at / last_run_at / next_run_at / last_result` 是 **schtasks 任务属性**，**必须保留参与哈希**（只排除生成侧元数据：`generated_at` / `checked_at` / 临时路径 / 采集端主机名）。
3. **`snapshot_id` 仅作内容指纹与追溯标识，不得作为跳过 diff 的判据**：`diff_snapshots()` 比对 `tasks[]` 内容而非 id；若剔除状态字段会使内容不同的快照产出相同 id，未来一旦「先比 id、相同则跳过 diff」即漏检 `state_changed/rc_changed/schedule_changed`。

---

## 6. 10-29 坏档隔离的因果链（ADR-05，防止把设计写成运行态）

- 10-29 三个 `.sql.gz` 实为纯 SQL 文本（魔数 `2d 2d`，`gzip.open` 报 `BadGzipFile`），manifest 却标 `ok=true`；根因是 Windows 下 `gzip.open + subprocess.run(stdout=f)` 时子进程写原 fd、gzip 头在 close 才追加（`b4_restore_drill.py` 注释 10–15 行已完整记录）。
- **处置（D4 建议「隔离标记、不删除」）**：纯新增 `INVALID_GZIP.flag`（团长单写者落盘）；**禁止**改该目录既有 `manifest.json`、不删 / 不改名 / 不重压缩 / 不改写既有 3 个 `.sql.gz` 的任何字节。
- **`INVALID_GZIP.flag` 不是运行态自动过滤闸门**：它只是 ① 隔离标记 `mark-only-do-not-delete` ② 显式 `--date 2026-10-29` 时的**人工拦截凭据** ③ 审计留痕。本轮「10-29 不被选为演练数据源」**与 flag 无因果**，仅由 `list_backups()` 日期倒序 + `main()` 取 `backups[0]`（11-05）保证。
- **不写死「`list_backups()` 返回可选数 = 0」**（T-11 已收敛）；实施期正确表述为「10-29 在候选集中的选中数 = 0」，需 D4 后接入 `should_select(verify_artifact(p))`。

---

## 7. FF11 安全审计提示（仅登记，本轮不改）

1. **数据库 dump 明文落盘**：副链 `backups/db/<date>/*.sql.gz` 是未加密全库明文（11-05 合计 1,066,759,563 B，raw 15,132,051,150 B），仅靠 NTFS ACL 与 `C:\backups_mirror` **同盘**镜像（治理方案已定性 false comfort）。不进 git / `_vault` / 异地打包；若需异地先按 `Protect-File` 同等 AES-256-CBC + HMAC 加密。告警正文限「文件名 + 格式 + 魔数 + 大小 + reason」，不含数据样本、连接串、凭据。复核 `quant_code.tar.gz` 白名单（461–478 行）是否覆盖 `backups/db`；回收站删除 ≠ 销毁；`logs/alert_send.log` 保持最小字段防二次泄露。
2. **AES 密钥 `~/.backup_aes_key`**：44 B Base64（32 B），单文件、无轮换、无 KMS、无 ACL 校验；`Protect-File` / `Unprotect-File` 同一把 key 兼做 AES-CBC 与 HMAC-SHA256（既有设计，仅登记不改）。审计项：ACL 应仅 `t`/SYSTEM 可读、是否入 git / `_vault` / 备份包、离线纸质副本存在性。密钥丢失即全部历史 `.enc` 永久不可解（`Get-AesKey` 已有 abort + `BACKUP_KEY_LOST_*.flag`，禁改成自动生成新 key）。**D0 演练须只读、临时目录、产物用完即删；本轮绝对禁止执行**。
3. **异地 SFTP 凭据**：`tools/offsite_push_remote.ps1` / `offsite_push_vault.ps1` → 生产服务器 10.205.0.118。凭据不得硬编码、不得写进本文件 / 日志 / 飞书；上传开关默认 OFF（v13 设计），本方案不引入任何触发上传的新代码路径（F1/F2 CLI 拒绝 `--host/--upload/--remote`）；上传前确认不含明文 DB dump；网络失败写独立 `OFFSITE_FAILED_*` flag。

---

## 8. 非功能性基线与硬边界（供质量测试官 / 运维守护师引用）

| 维度 | 承诺 |
|---|---|
| 并发 | **1**（单写者串行）；同目录禁止两进程并发写；第二实例 exit 2，不上分布式锁 |
| 响应 | 492 任务 `export ≤ 30s`（schtasks timeout 60s）；`diff`/`stats ≤ 1s`；8 MiB 探针 `≤ 2s`；1 GiB sha256 `≤ 15s`（chunk=1 MiB）；**不做整文件 gzip 解压** |
| 可用性 | 非在线服务 → 映射为每次调度可观测性 100%：`ok=false` 必须同时 manifest.ok=false + rc≠0 + 飞书到达；月度人工复核 ≥1 次 |
| 崩溃率 | 脚本异常**不得**产出 `ok=true`；`verify_artifact` 异常一律收敛为 `ok=false`；6 类夹具分支覆盖 100% |
| 安全 | 零明文口令进宿主/日志/告警；密钥不落代码库；dump 明文不出本机；告警正文无凭据 |
| 备份 | 台账快照每日 1 份、保留 ≥14 天（D3 待收口），单份 ≤1 MiB 可入 git；manifest / flag 原子写；校验记录 ≥7 天 |

**硬边界静态核验 7 个 0**：计划任务增改/启用/禁用 = 0；既有文件删改 = 0；`DUMPS`/`KEEP_DAYS` 变更 = 0；`backups/db` 既有产物字节改写 = 0；`.enc` 演练 = 0；10.205.0.118 访问 = 0；本阶段落盘 = 0（本文件为团长 W-09 明确委派的唯一例外）。

---

## 9. 关联裁决索引

| 裁决 | 内容 | 落点 |
|---|---|---|
| C-1 | 「新增 flag 文件」≠「改写既有产物内容」；本轮允许纯新增 `INVALID_GZIP.flag`，禁改既有 manifest；不改 `list_backups()` | §6 / A9 |
| C-2 | 候选链 `suggest-registry(applied=false)` → 团长汇总 → 老田确认 expect → D1/D2 后写入 | A1 / §5.1 |
| C-3 | `dump_one/main` 接入 `verify_artifact` 排 D1/D3 之后 | A6 / A7 / §4 |
| C-4 | FF06/FF11 为团队交付物编号，非仓库既有文件；落盘按既有命名体系 | §2.1 |
| C-5 | ADR-01 解除（四期路线同一单机 CLI 形态，无 Web/移动端/常驻服务） | §0 |
| C-6 | TD-08 登记技术债务，本轮不修 | §3 |
| ADR-02/03 | 禁止纸面达标；区分「设计约束（本轮文本）」与「运行态验收（本轮不做）」 | §0 / §3.3 / §6 |
| ADR-05 | 「只做一半」：10-29 隔离只做纯新增文件，不承诺代码自动过滤 | §6 / A9 |
| ADR-06/10 | F1/F2 只读、5 个子命令、`applied` 恒 False | §5 |
| ADR-07 | FF 编号声明 | §2.1 |
| ADR-11 | 旧 AC-01~11 定位为 v0.1/v0.2 未来退出条件 | §2.3 |
| ADR-15 | D-4~D-7 不出增量契约，登记「实施期接口补充清单」 | §4 |
| ADR-21 | 编号命名空间消歧（F1–F5 ⇔ F-01~F-11、旧/新 AC） | §2.3 |

**D0~D6 全部未批准**：D0 `.enc` 解密演练 / D1 `\DbDumpDaily` 范围 / D2 `\CainiaoPull` 排班 / D3 轮转天数 / D4 10-29 坏档 / D5 `zerobound-neo4j` / D6 任务瘦身与错峰。任何阶段 1 / 阶段 3 动作在批准前一律拒绝。

---

## 10. 变更记录

| 日期 | 变更 | 来源 |
|---|---|---|
| 2026-11-05 | 建立 A1~A16 接入点留痕、FF 编号对照、TD-08 专项、D-4~D-7 实施期清单、F1/F2 只读声明 | 团长 W-09 委派；基于 `[技术研发方案]` §4/§10/§11 |
| 2026-11-05 | 纳入三前缀 golden 修正、`classify` 顺序修正、两套口径分离、`snapshot_id` 不变式 | 团长实测裁决 + product-manager 补丁 10 |
| 2026-11-05 | 纳入 ADR-05 因果链收紧（flag 非运行态过滤闸门） | release-manager 跨方案提示 |
| 2026-11-05 | 纳入 PENDING→rc=0、7 态映射、旧 AC 作废与命名空间消歧 | product-manager 最终版 + ADR-11/21 |
| 2026-11-05 | W-09 追加约束：A9 显式「本轮否」+ D4 后接入措辞、§2.1 补 ADR-20「编号非白名单依据 / GOV-W 终表」、新增 §11 专项复核声明 | team-lead + release-manager（W-12 §2.2 S5 / §7 项 5） |

> **本轮执行声明**：除本文件外零落盘；未新增/修改/启用/禁用任何计划任务；未删除或改名任何文件；未改 `DUMPS` / `KEEP_DAYS` / `REGISTRY` / `b4_restore_drill.py` / `backup_daily.ps1`；未改写 `backups/db` 任何既有产物；未执行 `.enc` 解密演练；未触碰 10.205.0.118；未 commit。

---

## 11. 专项复核声明：设计完成 ≠ 运行态生效

> 引用 **ADR-03 / ADR-05 / ADR-11**；对齐 W-12 §7 项 5 专项复核与 §2.2 S5 退出判据。

### 11.1 总原则

**本文档全部内容为 Phase 1 设计层留痕与接口定义，不代表任何能力已在运行态生效、也不代表任何验收已通过。** 「设计完成」与「运行态生效」是两件事，不得互相替代、不得由前者推断后者（ADR-03 明令防止「纸面达标」）。具体判据归属：

| 维度 | 本轮状态 | 依据 |
|---|---|---|
| 设计约束（文本） | 已在本文档交付：告警正文走 `code`、第三参真实 UTF-8 日志路径、闸门三判据、16 条接入点留痕、D-4~D-7 补充清单 | 本文件 §1 / §3 / §4 |
| 运行态验收 | **本轮全部不做**：不接 `db_dump_daily.py`、不跑真实备份、不推告警、不实跑 pytest 门禁、不执行 `.enc` 解密演练 | ADR-02 / ADR-03 / 团长终裁四条 AC 不变 |
| 未来退出条件 | 旧 AC-01~AC-11 及 §六 M1~M6 定位为 v0.1/v0.2 退出条件 / 目标态，**不是本轮验收** | ADR-11 |

### 11.2 逐项复核（W-12 §2.2 S5 三条判据）

1. **判据 1 · 编号与清单齐备**：A1~A16 十六条接入点（§1.2）+ 总览表（§1.3）+ FF01~FF12 对照表（§2.2）齐备；§2.1 按 ADR-07 / ADR-20 声明「均为方案内自定义编号、非仓库既有文件编号、**非白名单依据**」，并明确**唯一有效白名单为 GOV-W 终表**。✅
2. **判据 2 · A9 显式「本轮是否改：否」**：§1.2 A9 与 §1.3 总览表均已写明「自动过滤待 **D4 裁定后实施期**接入 `should_select(verify_artifact(p))`；**本轮不承诺代码自动过滤、不写死『可选备份数=0』**」。✅
3. **判据 3 · 无因果 / 生效表述**：全文已扫描，无「加 flag 后演练自动跳过 10-29」「10-29 坏档永不被选中（运行态）」等措辞。正确表述固定为：10-29 不被默认演练选中，仅由 `list_backups()` 按日期倒序 + `main()` 取 `backups[0]`（=11-05）保证，**与 `INVALID_GZIP.flag` 无因果关系**；flag 为隔离留痕 + 显式 `--date` 人工拦截凭据，**不是运行态自动过滤闸门**（§1.2 A9、§6）。✅

### 11.3 易混淆点显式隔离（ADR-05）

| 表述 | 是否成立 | 原因 |
|---|---|---|
| 「新增 `INVALID_GZIP.flag`（纯新增文件）」 | ✅ 成立 | 团长 C-1 clarified：新增 flag ≠ 改写既有产物内容 |
| 「改 10-29 既有 `manifest.json` 标 `format: plain-sql`」 | ❌ 不成立 | 硬边界：不改写 `backups/db` 既有产物内容；`manifest_patch` 仅为设计件 |
| 「flag 使演练自动跳过 10-29」 | ❌ 不成立 | flag 不改变 `list_backups()` 行为；不选 10-29 是日期倒序的结果，无因果 |
| 「`list_backups()` 返回可选数 = 0」 | ❌ 不成立 | 底账 9 个目录；T-11 已收敛为「已收敛、不写死断言」 |
| 「D4 后接入 `should_select()` 自动过滤」 | ⏳ 未来 | 实施期动作，需 D4 裁定；本轮 `A9 是否改 = 否` |
| 「四条 AC 已通过」 | ⏳ 未来 | 本轮只做设计层验收；AC 是落盘后（Phase 2+）的判据 |

### 11.4 复核结论

W-09 满足 W-12 §2.2 S5 三条退出判据与 §7 项 5 专项复核：**只新建单一文件、不碰任何既有 modified 文件、编号声明完整、A9 显式「本轮否」、全文无因果或生效表述、设计态与运行态严格分离**。本文件不构成任何「已落地 / 已生效 / 已通过验收」的声明。
