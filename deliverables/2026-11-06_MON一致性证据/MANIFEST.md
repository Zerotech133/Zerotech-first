# 证据清单：2026-11-06 MON 引擎与 MONPAGE 一致性核对

## 一、结论

**一致，MON 留在 11-06 16:30 B 批。** 门户 `/api/monitor/*` 与 MON 引擎接口字段一致；
差异仅内部实现（PIT 口径增强、pools 并发安全、detail 文案近3→近4）。证据分两阶段：

- **Stage 1（核对时）**：master `e78d1f55c` / MON `0f2865065`（behind 277）；模拟合并树 83 passed；
  merge-tree 唯一冲突 `tools/portal_route_baseline.json`（MON 298 vs master 305）。
- **Stage 2（2026-11-06 03:05 复核）**：并发会话已将 MON 同步到 master（tip `c9d57be50`，behind 0，
  route baseline 305 已对齐）；merge-tree 无冲突（树 `00f4ef3b7`）；MON worktree 接口测试 49 passed。

## 二、可直接打开的复核顺序

> **入库对应关系（审计口径）**：本目录 tracked **30** = payload **29**（§五 清单 29 行）+ `MANIFEST.md` 自身；其中 3 个 `*.log` 被 `.gitignore` 忽略、经 `git add -f` 强制入库（force-add，见 §五、§八）。

1. `tests_sim.log`（83 passed 日志）+ `tests_sim.rc`（rc=0）
2. `merge_tree_predicted_master_vs_mon.txt`（Stage1 预测输出）+ `predicted_tree_blobs.txt`
3. `merge_tree_predicted_current_20261106_0305.txt`（Stage2 当前无冲突预测）
4. `tests_mon_worktree_current_20261106_0305.log`（Stage2 49 passed）
5. `field_probe.py` 两输出 JSON；`alert_master.json` vs `alert_mon.json` 对比
6. `monitor_diff_master_vs_monbranch.diff` + `ast_diff_result.json`

## 三、复跑命令

```powershell
$py = 'C:\Program Files\Python311\python.exe'

# 基线（master 当前引擎）：43 passed
cd 'C:\项目\老田的AI助手'
& $py -X utf8 -m pytest -q portal/tests/test_api_monitor.py analysis/tests/test_monitor.py -p no:cacheprovider

# Stage1 合并模拟（复现 83 passed；需先建临时 worktree 并覆盖 MON 引擎/测试）
git worktree add --detach C:\Temp\mon_merge_sim_repro e78d1f55c
git show task/K-STOCK-ANALYSIS-MON-20261104:analysis/monitor.py > C:\Temp\mon_merge_sim_repro\analysis\monitor.py
git show task/K-STOCK-ANALYSIS-MON-20261104:analysis/tests/test_monitor.py > C:\Temp\mon_merge_sim_repro\analysis\tests\test_monitor.py
git show e78d1f55c:analysis/tests/test_monitor.py > C:\Temp\mon_merge_sim_repro\analysis\tests\test_monitor_master_legacy.py
cd C:\Temp\mon_merge_sim_repro
Remove-Item Env:WATCH_TRIGGER_LEDGER_PATH -ErrorAction SilentlyContinue
$env:PYTHONIOENCODING='utf-8'; $env:PYTHONUTF8='1'
& $py -X utf8 -m pytest -q portal/tests/test_api_monitor.py analysis/tests/test_monitor.py analysis/tests/test_monitor_master_legacy.py -p no:cacheprovider
cd 'C:\项目\老田的AI助手'; git worktree remove --force C:\Temp\mon_merge_sim_repro

# 合并预测（当前 tips）
git merge-tree --write-tree master task/K-STOCK-ANALYSIS-MON-20261104

# 字段探针（分别在主仓与 MON worktree 运行）
& $py -X utf8 C:\Temp\mon_consistency_20261106\field_probe.py
```

注：`tests_sim.log` 是 `*>` 重定向的捕获输出，仅含 pytest 进度与汇总行；完整 stdout 未另存。

## 四、保留期限与位置

| 位置 | 性质 | 保留要求 |
|---|---|---|
| `C:\项目\老田的AI助手\deliverables\2026-11-06_MON一致性证据\`（本目录，git 跟踪） | **正式归档** | 随仓库永久保留（提交后可由审计直接复核） |
| `C:\Temp\mon_consistency_20261106\`（工作目录） | 临时工作副本 | 保留至 **2026-11-13**（复核窗口期）；C:\Temp 可能被系统/人工清理，审计请用正式归档 |
| `C:\Temp\mon_merge_sim` / `mon_merge_sim_repro`（模拟 worktree） | 临时，已删除/可复建 | 无需保留；按第三节命令可复现 |

说明：目录内文本均为 UTF-8（部分 JSON/日志含 BOM，可直接用 VS Code/记事本/PowerShell `Get-Content -Encoding UTF8` 打开）；
`monitor_master.py` / `monitor_monbranch.py` 为 Python 源文件快照，大小 46,836B / 53,035B。
本目录 tracked **30** = payload **29** + `MANIFEST.md` 自身；其中 3 个 `*.log` 被 `.gitignore` 忽略、经 `git add -f` 强制入库（force-add，见 §五、§八）。

## 五、文件清单（payload 29 + MANIFEST.md 自身 = tracked 30；SHA-256）

| 文件 | 大小(B) | SHA-256 | 用途 |
|---|---:|---|---|
| `alert_field_probe.py` | 1042 | `A79A243EA730B2E2EC3704BE279837832B888FDDBA8391531D32CFE8098EDC40` | 合成告警字段探针脚本（注入含未来日期事件） |
| `alert_master.json` | 440 | `6DE0C215F7AF84548633D7C42BEF60BBFB4E1197495C1D7BFB380EFF8367BA84` | master 引擎合成告警输出 |
| `alert_mon.json` | 440 | `DDE2B20AA44C7F82704B0EC4A43B6A5B55EF579F76D40092D7AE38E120E59749` | MON 引擎合成告警输出（schema 同，仅 detail 文案 近3→近4） |
| `api_monitor_master.py` | 5574 | `D48A4B7D04E5F6E6F1A5FE47D55267482A6B1E1895D4847F691B8B1F71A583A5` | master 门户 portal/api_monitor.py 快照（/api/monitor/* 接口层） |
| `ast_diff.py` | 1141 | `4F24B8E34BB57CB4A53BBC90B416A83E6A48C22C938A081DC40562B58BA0751C` | 公共 API/签名对比脚本（ast） |
| `ast_diff_result.json` | 377 | `AA75A1F7C4A5A1CD4D21C6D59F32CA7FB59A4293990CDD135FA66751D9B11519` | ast 对比结果：公共函数全同，仅内部函数新增/私有签名变化 |
| `blobs_mon_current_20261106_0305.txt` | 249 | `FAC0680DF768FBCE67F99A18798C803AA70120ACA3AD73058C9E41DF88E0B569` | Stage2 当前 MON tip c9d57be50 的关键 blob 映射 |
| `field_probe.py` | 1925 | `2123CCBC06BD145FBA7F7547DBE1E9F5040D597034F253201C5D8B29E5E9D50E` | 运行时字段探针脚本（summary/load_pools/list_pools/list_alerts/run_monitor） |
| `gen_manifest.py` | 9874 | `3F8A5AA7ED338D8E5B53E1C128772C172C4652BCAF99B7273328872D3A72470B` | 本清单生成脚本（重新计算 SHA-256 并可复跑） |
| `merge_tree_predicted_current_20261106_0305.txt` | 86 | `A692DACCE2AA0CB00AECC109F34E194CD4A6DB741F7123BA84F290283AA0D382` | Stage2 merge-tree 预测（当前 tip）：无冲突，结果树 00f4ef3b7 |
| `merge_tree_predicted_master_vs_mon.txt` | 832 | `3E397031B8B48FB7A3A36190A84180DF670189E28209ADC56E7AABBD454864E2` | Stage1 merge-tree 预测（原 tip 0f2865065）：唯一冲突 tools/portal_route_baseline.json |
| `monitor_diff_master_vs_monbranch.diff` | 12581 | `2865D48488D2CB15433CABB346C8635F1CD97F280BE0CCA03C6232B43FC794F4` | 两版引擎 unified diff（187+/43−；PIT/锁/文案差异） |
| `monitor_master.py` | 46836 | `CA0952A519E08FB52B91567B4822CCB697847A9B6E13862121A34587C0EDF89B` | master(MONPAGE 合入版) analysis/monitor.py 全文快照（Stage1 基线） |
| `monitor_monbranch.py` | 53035 | `EA316335BDF2F96806324DDAAFE356DAFFD3CC2271890AA39FC42C95D9F90335` | MON 分支 analysis/monitor.py 全文快照（Stage1 基线） |
| `predicted_tree_blobs.txt` | 325 | `440518BE9C1F00B6B00E1FB1221E127828B1AD59DED9367C2E02EB68C37A014D` | Stage1 预测结果树 4396b081b 的关键 blob 映射 |
| `probe_master.json` | 732 | `086FCB2134EE2DA660FB177336BBAD0C879D9076F409741BADF6B7D7DAE88757` | master 引擎运行时字段输出 |
| `probe_mon_engine.json` | 720 | `7ECA219EE0AD37756FB3A5F5DE9DE3A62510803A4A3EC05E4CCA1B146F3C3A30` | MON 引擎运行时字段输出（与 master 一致） |
| `route_compare.py` | 613 | `26AF5CE3798E1BE4790D393C79E39910066008182865CC78916B3BBA64F58850` | route baseline 对比脚本（top keys） |
| `route_compare_result.txt` | 275 | `5306B31891DB8FB82437E670F77B86AE869FBF897E0524D4DF01C23DF8460D84` | route baseline 结构对比结果（Stage1） |
| `route_total.py` | 507 | `A32193C3E49BE1C6C608518EBBEBC07ED10DEDF74B5D5E5108B1D3F07AB1DCC7` | route baseline 总数对比脚本 |
| `route_total_result.txt` | 343 | `DFE9A4A42A991633B03D63F8CF2DFBE28C0D4B51430BB3F3A06D1240AE06EE5B` | route baseline 总数：master 305 / MON 298（Stage1，MON 尚未同步） |
| `route_total_result_current_20261106_0305.txt` | 343 | `DFE9A4A42A991633B03D63F8CF2DFBE28C0D4B51430BB3F3A06D1240AE06EE5B` | Stage2 当前值：master 305 / MON 305（已同步） |
| `test_api_monitor_master.py` | 4962 | `1DB1A7701CEC60DC667BCBB52DDA7A2D917DAAA8D433BF7F0A47DEBA947BBA78` | master 门户 portal/tests/test_api_monitor.py 快照 |
| `test_monitor_master.py` | 29259 | `9D97E54BE168CE25C95FD08F1FB4E3063938D05D937817761294D058E934C54D` | master 版 analysis/tests/test_monitor.py 快照 |
| `test_monitor_monbranch.py` | 34951 | `7B1C9E405292B3A3A460089C85321582840DF09D734CABAB0EE7BD7BEE02E914` | MON 版 analysis/tests/test_monitor.py 快照（较 master 基座 +130 行） |
| `tests_baseline_master.log` | 204 | `A5B54AAD390D3520D00D7746DEA6ECB40408BC01491D96F3F469DEF0F9BA5BAA` | master 基线测试日志：43 passed（**force-add**：`.gitignore` 忽略 `*.log`，`git add -f`） |
| `tests_mon_worktree_current_20261106_0305.log` | 204 | `8316928D459CA2242BDF5BFE011E53A6CDA609245BFBF22B5E121A71B002F2F4` | Stage2 在已同步 MON worktree 的接口测试：49 passed（**force-add**：`.gitignore` 忽略 `*.log`，`git add -f`） |
| `tests_sim.log` | 366 | `99ED887626B2E4EAA0ECD82097DF1D79AE82CF70FF15AE16F90542169B90B661` | 合并模拟（master 树 + MON 引擎/测试）测试日志：83 passed（**force-add**：`.gitignore` 忽略 `*.log`，`git add -f`） |
| `tests_sim.rc` | 6 | `5C26234D431079B7BE42D1B71D7CA849FC2BDAF901D9E32751CD99DF20A79E0F` | 合并模拟测试返回码：rc=0 |

说明：§五 表格 **29 行 = payload**；`MANIFEST.md` 未列入该表，自身也被 git 跟踪（+1 → tracked 30）。其中 `tests_baseline_master.log`、`tests_mon_worktree_current_20261106_0305.log`、`tests_sim.log` 三个 `*.log` 被 `.gitignore` 忽略，经 `git add -f` 强制入库（force-add）。

## 六、核对时的版本钉扎

- Stage1 master: `e78d1f55c`（OPS-H2B-MERGE-RECEIPT-20261106）；MON: `0f2865065`（behind 277）
- Stage1 模拟结果树: `4396b081b1083db1373cb9766a9326fec73e52f2`
- Stage2 master: `3c2a3a9d2`；MON: `c9d57be50`（behind 0）
- Stage2 merge-tree 树: `00f4ef3b70051d90e428d0f22055ffadbae1bfa4`

## 七、口径备注

- 两阶段证据均保留；Stage1 的“冲突”是当时分支未同步的真实状态，Stage2 已由并发会话 rebase 消除。
- 本轮只读核对，未修改 MON 分支、未合入；MON 分支的同步提交 `c9d57be50` 由并发会话完成。
- `/api/monitor/*` 接口字段：`summary` keys=`alerts_total,by_severity,last_run,pools`；`run_monitor` keys=`ok,pool,as_of,scanned,alerts,new_alerts,by_severity,items,run_id,dry_run`；alert 条目 keys=`code,rule,severity,title,detail,trade_date,dedupe_key`。

## 八、入库与 tracked 对应（审计用）

- 口径：**tracked 30 = payload 29 + `MANIFEST.md` 自身 1**；磁盘文件数同为 30，无多余、无未跟踪、无忽略残留。
- force-add：`tests_baseline_master.log`、`tests_mon_worktree_current_20261106_0305.log`、`tests_sim.log` 三个文件被 `.gitignore` 忽略（`*.log`），经 `git add -f` 强制入库；其余 26 个 payload 为常规入库。
- 提交：`2f9759a44`（27 文件，payload 主体）+ `3dffd52a2`（+3 个 `.log`，force-add）；`MANIFEST.md` 随 `2f9759a44` 提交。
- 复核命令（仓库根运行，期望输出见注释）：

```powershell
$dir = 'deliverables/2026-11-06_MON一致性证据'
git ls-files -- $dir | Measure-Object -Line                                  # 30
git ls-files -- $dir | Where-Object { $_ -notmatch 'MANIFEST.md' } | Measure-Object -Line  # 29
git ls-files -- $dir | Where-Object { $_ -match '\.log$' } | Measure-Object -Line          # 3
git status --porcelain -- $dir                                               # 空
git status --porcelain --ignored -- $dir                                     # 空（无 !! 行）
git check-ignore -- "$dir/*"                                                 # rc=1（无输出）
```
