import hashlib
import os
import subprocess

ARCH = r"C:\项目\老田的AI助手\deliverables\2026-11-06_MON一致性证据"

PURPOSE = {
    "monitor_master.py": "master(MONPAGE 合入版) analysis/monitor.py 全文快照（Stage1 基线）",
    "monitor_monbranch.py": "MON 分支 analysis/monitor.py 全文快照（Stage1 基线）",
    "monitor_diff_master_vs_monbranch.diff": "两版引擎 unified diff（187+/43−；PIT/锁/文案差异）",
    "test_monitor_master.py": "master 版 analysis/tests/test_monitor.py 快照",
    "test_monitor_monbranch.py": "MON 版 analysis/tests/test_monitor.py 快照（较 master 基座 +130 行）",
    "api_monitor_master.py": "master 门户 portal/api_monitor.py 快照（/api/monitor/* 接口层）",
    "test_api_monitor_master.py": "master 门户 portal/tests/test_api_monitor.py 快照",
    "ast_diff.py": "公共 API/签名对比脚本（ast）",
    "ast_diff_result.json": "ast 对比结果：公共函数全同，仅内部函数新增/私有签名变化",
    "field_probe.py": "运行时字段探针脚本（summary/load_pools/list_pools/list_alerts/run_monitor）",
    "probe_master.json": "master 引擎运行时字段输出",
    "probe_mon_engine.json": "MON 引擎运行时字段输出（与 master 一致）",
    "alert_field_probe.py": "合成告警字段探针脚本（注入含未来日期事件）",
    "alert_master.json": "master 引擎合成告警输出",
    "alert_mon.json": "MON 引擎合成告警输出（schema 同，仅 detail 文案 近3→近4）",
    "route_compare.py": "route baseline 对比脚本（top keys）",
    "route_total.py": "route baseline 总数对比脚本",
    "route_compare_result.txt": "route baseline 结构对比结果（Stage1）",
    "route_total_result.txt": "route baseline 总数：master 305 / MON 298（Stage1，MON 尚未同步）",
    "route_total_result_current_20261106_0305.txt": "Stage2 当前值：master 305 / MON 305（已同步）",
    "tests_baseline_master.log": "master 基线测试日志：43 passed",
    "tests_sim.log": "合并模拟（master 树 + MON 引擎/测试）测试日志：83 passed",
    "tests_sim.rc": "合并模拟测试返回码：rc=0",
    "merge_tree_predicted_master_vs_mon.txt": "Stage1 merge-tree 预测（原 tip 0f2865065）：唯一冲突 tools/portal_route_baseline.json",
    "predicted_tree_blobs.txt": "Stage1 预测结果树 4396b081b 的关键 blob 映射",
    "blobs_mon_current_20261106_0305.txt": "Stage2 当前 MON tip c9d57be50 的关键 blob 映射",
    "merge_tree_predicted_current_20261106_0305.txt": "Stage2 merge-tree 预测（当前 tip）：无冲突，结果树 00f4ef3b7",
    "tests_mon_worktree_current_20261106_0305.log": "Stage2 在已同步 MON worktree 的接口测试：49 passed",
    "gen_manifest.py": "本清单生成脚本（重新计算 SHA-256 并可复跑）",
}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def git(*args):
    try:
        return subprocess.check_output(["git", "-C", r"C:\项目\老田的AI助手"] + list(args), text=True).strip()
    except Exception as exc:  # noqa: BLE001
        return f"ERR {exc}"


rows = []
for name in sorted(os.listdir(ARCH)):
    p = os.path.join(ARCH, name)
    if os.path.isfile(p):
        rows.append((name, os.path.getsize(p), sha256(p), PURPOSE.get(name, "（未归类）")))

lines = []
lines.append("# 证据清单：2026-11-06 MON 引擎与 MONPAGE 一致性核对")
lines.append("")
lines.append("## 一、结论")
lines.append("")
lines.append("**一致，MON 留在 11-06 16:30 B 批。** 门户 `/api/monitor/*` 与 MON 引擎接口字段一致；")
lines.append("差异仅内部实现（PIT 口径增强、pools 并发安全、detail 文案近3→近4）。证据分两阶段：")
lines.append("")
lines.append("- **Stage 1（核对时）**：master `e78d1f55c` / MON `0f2865065`（behind 277）；模拟合并树 83 passed；")
lines.append("  merge-tree 唯一冲突 `tools/portal_route_baseline.json`（MON 298 vs master 305）。")
lines.append("- **Stage 2（2026-11-06 03:05 复核）**：并发会话已将 MON 同步到 master（tip `c9d57be50`，behind 0，")
lines.append("  route baseline 305 已对齐）；merge-tree 无冲突（树 `00f4ef3b7`）；MON worktree 接口测试 49 passed。")
lines.append("")
lines.append("## 二、可直接打开的复核顺序")
lines.append("")
lines.append("1. `tests_sim.log`（83 passed 日志）+ `tests_sim.rc`（rc=0）")
lines.append("2. `merge_tree_predicted_master_vs_mon.txt`（Stage1 预测输出）+ `predicted_tree_blobs.txt`")
lines.append("3. `merge_tree_predicted_current_20261106_0305.txt`（Stage2 当前无冲突预测）")
lines.append("4. `tests_mon_worktree_current_20261106_0305.log`（Stage2 49 passed）")
lines.append("5. `field_probe.py` 两输出 JSON；`alert_master.json` vs `alert_mon.json` 对比")
lines.append("6. `monitor_diff_master_vs_monbranch.diff` + `ast_diff_result.json`")
lines.append("")
lines.append("## 三、复跑命令")
lines.append("")
lines.append("```powershell")
lines.append("$py = 'C:\\Program Files\\Python311\\python.exe'")
lines.append("")
lines.append("# 基线（master 当前引擎）：43 passed")
lines.append("cd 'C:\\项目\\老田的AI助手'")
lines.append("& $py -X utf8 -m pytest -q portal/tests/test_api_monitor.py analysis/tests/test_monitor.py -p no:cacheprovider")
lines.append("")
lines.append("# Stage1 合并模拟（复现 83 passed；需先建临时 worktree 并覆盖 MON 引擎/测试）")
lines.append("git worktree add --detach C:\\Temp\\mon_merge_sim_repro e78d1f55c")
lines.append("git show task/K-STOCK-ANALYSIS-MON-20261104:analysis/monitor.py > C:\\Temp\\mon_merge_sim_repro\\analysis\\monitor.py")
lines.append("git show task/K-STOCK-ANALYSIS-MON-20261104:analysis/tests/test_monitor.py > C:\\Temp\\mon_merge_sim_repro\\analysis\\tests\\test_monitor.py")
lines.append("git show e78d1f55c:analysis/tests/test_monitor.py > C:\\Temp\\mon_merge_sim_repro\\analysis\\tests\\test_monitor_master_legacy.py")
lines.append("cd C:\\Temp\\mon_merge_sim_repro")
lines.append("Remove-Item Env:WATCH_TRIGGER_LEDGER_PATH -ErrorAction SilentlyContinue")
lines.append("$env:PYTHONIOENCODING='utf-8'; $env:PYTHONUTF8='1'")
lines.append("& $py -X utf8 -m pytest -q portal/tests/test_api_monitor.py analysis/tests/test_monitor.py analysis/tests/test_monitor_master_legacy.py -p no:cacheprovider")
lines.append("cd 'C:\\项目\\老田的AI助手'; git worktree remove --force C:\\Temp\\mon_merge_sim_repro")
lines.append("")
lines.append("# 合并预测（当前 tips）")
lines.append("git merge-tree --write-tree master task/K-STOCK-ANALYSIS-MON-20261104")
lines.append("")
lines.append("# 字段探针（分别在主仓与 MON worktree 运行）")
lines.append("& $py -X utf8 C:\\Temp\\mon_consistency_20261106\\field_probe.py")
lines.append("```")
lines.append("")
lines.append("注：`tests_sim.log` 是 `*>` 重定向的捕获输出，仅含 pytest 进度与汇总行；完整 stdout 未另存。")
lines.append("")
lines.append("## 四、保留期限与位置")
lines.append("")
lines.append("| 位置 | 性质 | 保留要求 |")
lines.append("|---|---|---|")
lines.append("| `C:\\项目\\老田的AI助手\\deliverables\\2026-11-06_MON一致性证据\\`（本目录，git 跟踪） | **正式归档** | 随仓库永久保留（提交后可由审计直接复核） |")
lines.append("| `C:\\Temp\\mon_consistency_20261106\\`（工作目录） | 临时工作副本 | 保留至 **2026-11-13**（复核窗口期）；C:\\Temp 可能被系统/人工清理，审计请用正式归档 |")
lines.append("| `C:\\Temp\\mon_merge_sim` / `mon_merge_sim_repro`（模拟 worktree） | 临时，已删除/可复建 | 无需保留；按第三节命令可复现 |")
lines.append("")
lines.append("说明：目录内文本均为 UTF-8（部分 JSON/日志含 BOM，可直接用 VS Code/记事本/PowerShell `Get-Content -Encoding UTF8` 打开）；")
lines.append("`monitor_master.py` / `monitor_monbranch.py` 为 Python 源文件快照，大小 46,836B / 53,035B。")
lines.append("")
lines.append("## 五、文件清单（SHA-256）")
lines.append("")
lines.append("| 文件 | 大小(B) | SHA-256 | 用途 |")
lines.append("|---|---:|---|---|")
for name, size, digest, purpose in rows:
    lines.append(f"| `{name}` | {size} | `{digest}` | {purpose} |")
lines.append("")
lines.append("## 六、核对时的版本钉扎")
lines.append("")
lines.append(f"- Stage1 master: `e78d1f55c`（OPS-H2B-MERGE-RECEIPT-20261106）；MON: `0f2865065`（behind 277）")
lines.append(f"- Stage1 模拟结果树: `4396b081b1083db1373cb9766a9326fec73e52f2`")
lines.append(f"- Stage2 master: `{git('rev-parse', '--short', 'master')}`；MON: `{git('rev-parse', '--short', 'task/K-STOCK-ANALYSIS-MON-20261104')}`（behind 0）")
lines.append(f"- Stage2 merge-tree 树: `00f4ef3b70051d90e428d0f22055ffadbae1bfa4`")
lines.append("")
lines.append("## 七、口径备注")
lines.append("")
lines.append("- 两阶段证据均保留；Stage1 的“冲突”是当时分支未同步的真实状态，Stage2 已由并发会话 rebase 消除。")
lines.append("- 本轮只读核对，未修改 MON 分支、未合入；MON 分支的同步提交 `c9d57be50` 由并发会话完成。")
lines.append("- `/api/monitor/*` 接口字段：`summary` keys=`alerts_total,by_severity,last_run,pools`；`run_monitor` keys=`ok,pool,as_of,scanned,alerts,new_alerts,by_severity,items,run_id,dry_run`；alert 条目 keys=`code,rule,severity,title,detail,trade_date,dedupe_key`。")

with open(os.path.join(ARCH, "MANIFEST.md"), "w", encoding="utf-8", newline="\n") as f:
    f.write("\n".join(lines) + "\n")
print("MANIFEST written:", os.path.join(ARCH, "MANIFEST.md"))
print("files:", len(rows))
