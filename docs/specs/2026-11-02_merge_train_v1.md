# merge train 门 v1（W1/UF-09 工具化 + 接入规格）

- **日期**：2026-11-02｜**状态**：工具已交付（独立、未接 devtask/钩子）；接入建议待 Lead 评审
- **关联**：ADGS v1.1 §七 merge train（`docs/standards/2026-11-02_敏捷开发与治理标准_v1.md:122-127`）、`AGENTS.md §9.8`、ADR-015（组合层门禁）、K-STOCK-416 L2 审批协议
- **代码**：`tools/merge_train.py`（纯 stdlib、只读、无副作用）；测试 `tools/tests/test_merge_train.py`（19 例）
- **性质**：本批**不改** `tools/devtask.py`、`tools/merge_preflight.py`（P1.2 已接组合/内核门）；本文只给挂载建议与失败语义，接线由 Lead 评审后另行执行

## 一、规则（工具唯一口径，Asia/Shanghai = 固定 UTC+8，无 DST）

| 项 | 时段 | 语义 |
|---|---|---|
| 主合并窗口 | **16:30–17:00** | 左闭右开：16:30 可合，17:00 起关闭 |
| 次合并窗口 | **21:40–22:10** | 左闭右开：21:40 可合，22:10 起关闭 |
| 禁并期 | **09:15–15:05、17:10–18:40、20:00–20:20、21:20–21:40** | 左闭右开；仅代码改动禁止合并 |
| 文档/组合声明旁路 | 随时（含禁并期） | 改动**仅含** `docs/**`、`deliverables/**`、`compositions/**` 或任意 `*.md` 时允许合并 |
| 窗口外 | 除上述外 | 只提交不合（allowed=false） |
| 紧急 | `--force` 或 L2 流程 | 显式放行 + 醒目警告，必须工单留痕（五要素/批准人），24h 内复盘 |

**判定优先级**：`--force` ＞ 文档旁路 ＞ 窗口内 ＞ 禁并期 ＞ 窗口外。
**周五加班班次**（标准允许“周五按需加一班，18:00 前结束”）不在自动判定内：属人工按 L2/公告安排，需走 `--force` 或后续配置项（见 §六）。

## 二、用法

```powershell
# 状态（现在是否可合 / 原因 / 当前与下一窗口 / 禁并期）
python -X utf8 tools/merge_train.py status
python -X utf8 tools/merge_train.py status --json
python -X utf8 tools/merge_train.py status --now 2026-11-02T16:45:00+08:00 --json

# 改动判定（rc=0 allowed / 1 blocked）
python -X utf8 tools/merge_train.py check --files portal/api_kg.py
python -X utf8 tools/merge_train.py check --files docs/a.md,compositions/x/manifest.yaml --json
python -X utf8 tools/merge_train.py check --force --json        # 紧急放行（须留痕）

# 退出码：status 正常=0；check 0=allowed/1=blocked；参数错误=2
```

**JSON 契约**（`status`/`check --json`，stdout 纯 JSON）：

| 字段 | 说明 |
|---|---|
| `allowed` | bool，是否允许合并 |
| `reason` | 人读原因（窗口/禁并期/旁路/force 警告） |
| `mode` | `window` / `docs_bypass` / `force` / `forbidden` / `outside_window` |
| `window` | 当前窗口 `{name,label,start_hm,end_hm}` 或 `null` |
| `next_window` | 下一窗口 `{name,label,start(ISO),start_hm,end_hm}`（跨日已处理） |
| `forbidden_until` | 当前禁并期结束 ISO 或 `null` |
| `forbidden` / `forbidden_periods` / `windows` | 当前禁并期/规则表（供展示与对账） |
| `files` / `bypass_files` / `non_bypass_files` | 传入改动与旁路分类 |
| `force` / `force_warning` | 紧急放行标记与警告文案 |
| `schema` / `now` / `timezone` | `zerobound/merge-train/v1`、判定时点、时区说明 |

## 三、未来接入建议 A：`tools/devtask.py::cmd_merge`

**挂载点**：质量闸 `_quality_gate(tid)` 之后、组合/内核门 `_composition_kernel_gate(...)` 之前
（当前 master：质量闸块 L2366–L2377，组合门调用 L2379；接线代码插在 L2377 与 L2379 之间）。
理由：质量闸耗时最长，先过；merge train 是“何时可合”门，放组合/内核门（内容正确性门）前，失败信息更直观；仍在合并锁之前，不产生半合并态。

建议接线（~30 行，供评审粘贴）：

```python
def _merge_train_gate(tid, changed, force=False):
    """merge train 门（W1/UF-09）：窗口/禁并期/文档旁路判定。返回 (ok, detail)。"""
    tool = os.path.join(REPO, "tools", "merge_train.py")
    if not os.path.isfile(tool):
        return False, "merge train 工具缺失（tools/merge_train.py）"  # fail-visible
    files = ",".join(str(f) for f in (changed or []))
    args = [sys.executable, "-X", "utf8", tool, "check", "--files", files, "--json"]
    if force:
        args.append("--force")
    try:
        p = subprocess.run(args, cwd=REPO, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
    except Exception as exc:  # noqa: BLE001 —— 异常=拒（fail-visible）
        return False, "merge train 判定异常：%r" % exc
    try:
        payload = json.loads((p.stdout or "").strip())
    except ValueError:
        return False, "merge train 输出非 JSON（rc=%d）" % p.returncode
    return p.returncode == 0, payload.get("reason") or "merge train 拒绝"
```

`cmd_merge` 内（质量闸 `return 1` 块之后）插入：`force = "--train-force" in flags`（要求同时带 `--l2-ack --l2-reason`，复用 K-STOCK-416 五要素通道；`--train-force` 记 note + `_emit("approval_verdict", ...)` 留痕）；失败处理与其它门同构：`t["status"]="review"` + `_save` + `_emit("guard_fail", tid, "merge train 未过：...")` + `return 1`。

## 四、未来接入建议 B：`tools/merge_preflight.py`（第二道，防直连 git merge）

**挂载点**：`main()` 中 `composition_kernel_preflight(files)` 复检 PASS 之后、`if docs: return 0` 早退之后、`test_gate` 之前（当前 master：组合/内核复检 L369–L375，docs 早退 L377–L379，测试门 L380 起）。
理由：docs-only 已被钩子放行且 merge train 本身允许文档随时合并；代码合并走窗口判定；钩子不提供 `--force`（直连 git merge 的紧急场景走既有 `DEVTOOL_MERGE_GATE=0` 显式绕过 + 回单留痕，或改走 devtask 的 `--train-force`）。

建议接线（~15 行）：

```python
# ---- merge train（W1/UF-09）：窗口外/禁并期禁止直连 git merge ----
if not docs:
    def _merge_train_check(files):
        tool = HERE / "merge_train.py"
        if not tool.is_file():
            return None, "仓库缺 tools/merge_train.py"
        try:
            return subprocess.run([sys.executable, "-X", "utf8", str(tool), "check",
                                   "--files", ",".join(files), "--json"],
                                  cwd=str(REPO), capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=60), ""
        except Exception as exc:  # noqa: BLE001
            return None, "merge train 判定异常：%r" % exc
    _mt, _mt_err = _merge_train_check(files)
    if _mt is None:
        print("[merge-gate] merge train 异常：%s → 拒绝合并" % _mt_err)
        return 2
    try:
        _mt_payload = json.loads((_mt.stdout or "").strip())
    except ValueError:
        print("[merge-gate] merge train 输出非 JSON → 拒绝合并")
        return 1
    if _mt.returncode != 0:
        print("[merge-gate] merge train 未过：%s" % (_mt_payload.get("reason") or "窗口外/禁并期"))
        print("[merge-gate] 处置：窗口内重试（16:30–17:00 / 21:40–22:10）；紧急走 devtask --train-force 留痕")
        return 1
    print("[merge-gate] merge train PASS：%s" % (_mt_payload.get("reason") or ""))
```

## 五、失败语义

| 场景 | 工具 rc | devtask 行为 | hook 行为 | 处置 |
|---|---|---|---|---|
| 主/次窗口内，代码改动 | 0 | 放行（继续组合/内核门） | 放行（继续测试门） | 正常合并 |
| 禁并期/窗口外，代码改动 | 1 | 拒收：status=review + `guard_fail` | 拒收：中止合并（git 留半合并态时 `git merge --abort`） | 等窗口；紧急走 `--train-force`（devtask）或 `DEVTOOL_MERGE_GATE=0`（钩子，留痕） |
| 仅文档/组合声明（任意时刻） | 0 | 放行 | docs-only 已放行 | 随时合并 |
| 工具缺失/输出非 JSON/超时 | 2 或异常 | 拒（fail-visible） | 拒（fail-visible） | 修复工具/worktree 后重跑 |
| `--force`（仅 devtask 通道） | 0 | 放行 + 警告 + 工单留痕 + `approval_verdict` 事件 | 不提供（钩子无 force） | 五要素 + 24h 复盘 |
| `--now` 非法 | 2 | 不适用（门不传 --now） | 不适用 | 仅测试用途 |

**不新增绕过硬开关**：紧急沿用既有 `DEVTOOL_MERGE_GATE=0`（仅回滚/紧急、回单注明、审计留痕）；`--force` 只是显式留痕通道，不是静默跳过。

## 六、范围外（本批未做，需后续单）

1. **班次容量/队列**（标准 §七：窗口开始 FIFO、每班上限 6 个、失败退回下一班）——本工具不建模队列，接入时由 Lead/owner 排班或后续 `merge_train queue` 子命令。
2. **周五加班班次**（18:00 前结束）自动判定——非固定窗口，需人工安排/`--force` 留痕或配置化。
3. **Git 自动取改动**（默认从暂存区/分支取 `--files`）——本工具只用调用方传入的 `--files`，保持零副作用；devtask/hook 已有 `changed_files` 可直接传入。
4. **CI/计划任务接线**——未接（本批只交付工具+规格）。
5. **不改 devtask.py / merge_preflight.py**（按任务边界）。

## 七、验收与证据

```text
python -X utf8 -m py_compile tools/merge_train.py              → rc=0
python -m pytest tools/tests/test_merge_train.py -q            → 19 passed
python -X utf8 tools/merge_train.py status --json              → rc=0（JSON 含 allowed/reason/window/next_window/forbidden_until）
python -X utf8 tools/merge_train.py check --now 2026-11-02T16:30:00+08:00 --json   → rc=0（主窗口边界）
python -X utf8 tools/merge_train.py check --now 2026-11-02T17:00:00+08:00 --json   → rc=1（边界关窗）
python -X utf8 tools/merge_train.py check --now 2026-11-02T21:40:00+08:00 --json   → rc=0（次窗口边界）
python -X utf8 tools/merge_train.py check --files docs/a.md --now 2026-11-02T10:00:00+08:00 --json → rc=0（文档旁路）
```

## 八、回滚

- 工具本身只读、无文件/网络/服务副作用；回滚=删除工具/测试文件或 `git revert` 接线提交，无数据/服务/运行时变更。
- 接入回滚：删除 devtask 内 `_merge_train_gate` 调用块 + hook 内 merge train 检查块（各 <20 行，一次提交内完成）；回滚后合并行为恢复旧状。
- `--force` 留痕不可撤销：审批记录/事件（`approval_verdict`/`guard_fail`）按治理要求保留，回滚不删除记录。
- 回滚后复验：`python -X utf8 tools/merge_train.py status --json`（rc=0）+ 一次真实文档单合并演练（窗口内/外各一次）。

## 九、测试覆盖（19 例）

主窗边界 16:30/16:45、17:00 关窗、次窗口边界 21:40、禁并期早/晚/次窗口前缓冲、窗外（15:30）/跨日（22:30→次日 16:30）、文档旁路（docs/、deliverables/、根 *.md）、compositions 旁路、混合文件不旁路、窗口内代码改动、`--force`、JSON 契约字段、naive `--now` 按 Asia/Shanghai、非法 `--now` rc=2、status 文本规则展示。
