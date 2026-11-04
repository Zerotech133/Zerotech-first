# 组合门 + 内核守卫接入 devtask merge（P1.1 规格）

- **日期**：2026-11-02｜**状态**：Draft（待 Lead 评审后接线）
- **关联**：ADR-015（平台内核契约与组合模型）§2.3/§2.4/§7、ADR-016（唯一宿主）、ADGS v1.1 §9.6（门禁不绕过）§9.7（无证据不算完成）
- **依据基线**：master `bb73281c2`（2026-11-02）；行号以此为准，接线前用锚点 grep 复核
- **性质**：只定义挂载点、命令、失败语义与回滚。**本批不修改 `tools/devtask.py`**（写域外 + 需 Lead 评审后由 owner 接线）；本规格也不改 registry/manifest/运行时
- **上游工具**：`tools/composition.py`（P1 v0.2）、`tools/kernel_guard.py`（P1，含 `--commit-msg`）、`tools/tests/test_platform_composition.py`（23 用例）

## 〇、结论摘要（TL;DR）

| 项 | 结论 |
|---|---|
| 主挂载点 | `tools/devtask.py::cmd_merge`，**质量闸之后、合并锁之前**（现 L2294–L2305 之后、L2306 之前）插入一个条件门 `_composition_kernel_gate(tid, branch, changed)`，失败语义与质量闸同构（`status=review` + `_emit("guard_fail")` + `return 1`） |
| 第二道（P1.2） | `tools/merge_preflight.py::main` 在拿到 `files` 之后（现 L240–L248 之后）加只读复检，防直连 `git merge` 绕过 devtask |
| 阻断口径 | ① `compositions/**` 改动 → `composition.py plan --strict`（漂移=拒）；② 变更的具体 manifest → `composition.py validate <manifest> --json`（errors=拒，warnings 暂只告警）；③ 内核 artifact 改动 → `kernel_guard.py --files ... --commit-msg "<分支 tip 提交信息>"`（无 ADR 号=拒） |
| 关键约束 | 门必须读**待合并分支**内容：调用 `<任务worktree>/tools/*.py`（cwd=worktree），与 `_quality_gate` 的 `_gate_script_for(tid)` 同口径；worktree 缺失=拒（fail-visible） |
| 回滚 | `git revert <接线提交>` 单文件回滚；不涉数据/服务/注册表/端口，无不可逆点 |
| 本批未做 | 未改 devtask.py、未改 `platform/kernel_v1.yaml`、retire 仅 dry-run、钩子接入留 P1.2 |

## 一、现状基线（2026-11-02）

### 1.1 工具能力矩阵

| 命令 | 作用 | 关键开关 | 退出码 |
|---|---|---|---|
| `composition.py validate <id\|path\|--all>` | schema + 引用解析（agent/skill 硬，strategy/dataset/page/job 软） | `--strict`（warning→error）、`--json` | 0/1/2 |
| `composition.py plan` | registry.json vs `compositions/*/manifest.yaml` 漂移（缺失/多余/版本/生命周期/owner/tier/host/路径） | `--strict`、`--json` | 0/1/2 |
| `composition.py diff <left> [<right>\|--registry]` | 两个 manifest 的 metadata/spec/引用差异；或 manifest vs registry 快照 | `--registry`、`--strict`、`--json` | 差异非 strict=0（只报告），strict=1；2=参数错 |
| `composition.py retire <id\|path> --dry-run` | 退役预演（lifecycle→retired 补丁预览），**只打印不写盘**；不带 `--dry-run` 直接 rc=2 | `--dry-run`（必填）、`--strict`、`--json` | 0/1/2 |
| `composition.py status / audit` | 账本状态 / 孤儿审计 | — | 0 |
| `kernel_guard.py --files a,b --adr ADR-015` | 内核契约文件改动必须带 ADR | `--adr` | 0 允许 / 1 阻断 / 2 异常 |
| `kernel_guard.py --files a,b --commit-msg "<提交信息>"` | 提交信息中出现 `ADR-\d{3,}` 即视为关联（`--adr` 优先） | `--commit-msg` | 同上 |
| `kernel_guard.py --staged` / `--list` | 暂存区文件 / 列出受保护 artifact | — | 0 |

当前实测：`validate --all` = 7 个组合 / errors=0 / warnings 以 `validate --all` 实测为准；本分支实测 7 条（P1 已知软引用缺口，P2 resolver 收口）；`pytest tools/tests/test_platform_composition.py -q` = 25 passed。

### 1.2 devtask merge 现有门顺序（接线位置上下文）

`tools/devtask.py::cmd_merge`（L2151 起）：

1. 防呆：分支无提交拒绝（L2197–L2211）；
2. 脏文件重叠守卫（L2214–L2228）;
3. `GATES` 九道固定门（L2229–L2250，定义在 L427–L438）；
4. warn 门：安全扫描/gitleaks/OCR/视觉（L2251–L2258）；
5. L2 宿主机危险操作门（L2259–L2290）；
6. `_bundle_changed = _merge_changed_files(tid)`（L2293，**门函数可直接复用该文件清单**）；
7. 质量闸 `_quality_gate(tid)`（L2294–L2305，分支级 `test_gate -Suite fast`）；
8. 合并锁 `_acquire_merge_lock`（L2306–L2313）；
9. `git merge`（L2316），后置 worktree 回收等（L2319+）。

**接线点 = 步骤 7 之后、步骤 8 之前**（现 L2305 行尾与 L2306 之间）：此时测试已通过、合并锁尚未获取，门禁失败不会产生半合并态，也不会写 master。

### 1.3 两条硬约束（决定了接线写法）

1. **必须校验分支内容**：`composition.py` / `kernel_guard.py` 的 `ROOT = Path(__file__).parents[1]`，从 master 的 `tools/` 跑会读 master 文件。因此必须调用 `<任务worktree>/tools/composition.py` 并 `cwd=<worktree>`，与 `_quality_gate` 的 `_gate_script_for(tid)`（L1996：`WT_DIR/<tid>/tools/test_gate.ps1`）同口径；worktree 缺失 → 拒绝（不得回落到 master 跑）。
2. **fail-visible，不 fail-open**：工具缺失、JSON 解析失败、超时（建议 600s）一律按"拒收"处理，与质量闸"组件缺失即拒"（L2019）一致。

### 1.4 门触发条件（changed 命中矩阵）

`changed` 来自 L2293 `_merge_changed_files(tid)`（`merge_gated.branch_changed_files`，相对 master 的文件列表）。

| changed 命中 | 执行的命令（cwd=worktree） | 阻断语义 |
|---|---|---|
| `compositions/**`（任意，含 manifest/registry/schema/templates） | `python -X utf8 tools/composition.py plan --strict --json` | `rc!=0` 或 `ok!=true` → 拒（registry×manifest 漂移） |
| 其中具体变更的 `compositions/<id>/manifest.yaml` | `python -X utf8 tools/composition.py validate compositions/<id>/manifest.yaml --json`（逐个） | 任一 `errors>0` / `ok!=true` → 拒；warnings 只打印（P1） |
| changed ∩ `platform/kernel_v1.yaml` 的 artifacts（status=existing/frozen，含 `platform/kernel_v1.yaml` 自身、`tools/composition.py`、`tools/kernel_guard.py`、`tools/devtask.py` 等） | `python -X utf8 tools/kernel_guard.py --files <逗号连接> --commit-msg "<分支 tip 提交信息>"` | `rc==1` → 拒（改动内核契约但提交信息无 ADR 号）；`rc==2`/异常 → 拒（fail-visible） |
| 无命中（纯文档/配置单等） | 组合命令不跑；kernel_guard 仍快速执行（无命中则 rc=0，<1s） | 放行 |

注意：`validate --all --strict` **不能**作为全局阻断门——当前 28 条软引用 warning 会恒拒；P2 五类 resolver 收口后再升级（ADR-015 §8 P2）。全局只跑 `plan --strict`（当前 0 漂移）。

## 二、主挂载点 A（P1.1 唯一必需）

### 2.1 位置与调用

文件 `tools/devtask.py`：

- 插入代码位置：L2294–L2305 `_quality_gate` 块之后、L2306 `_lk0 = time.perf_counter()` 之前；
- 复用已算好的 `_bundle_changed`（L2293），不新增 git 调用；
- 失败处理复制质量闸块（L2299–L2305）结构与措辞。

建议接线代码（供评审粘贴，~60 行；行号仅标注插入点）：

```python
# ---- 组合门 + 内核守卫（P1.1，ADR-015 §2.4；规格 docs/specs/2026-11-02_composition_gate_devtask_v1.md）----
COMPOSITION_TRIGGER_PREFIX = "compositions/"


def _branch_tip_commit_msg(tid):
    """任务分支 tip 提交信息（kernel_guard --commit-msg 用；失败返回空串）。"""
    wt = os.path.join(WT_DIR, tid)
    r = subprocess.run(["git", "-C", wt, "log", "-1", "--format=%B", "task/%s" % tid],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return (r.stdout or "").strip()


def _run_branch_tool(tid, tool_rel, args):
    """在任务 worktree 内跑分支级工具 → (proc, error)。worktree/工具缺失=error（fail-visible）。"""
    wt = os.path.join(WT_DIR, tid)
    tool = os.path.join(wt, "tools", tool_rel)
    if not os.path.isfile(tool):
        return None, "任务 worktree 缺 tools/%s（拒绝在 master 上跑分支级门）" % tool_rel
    try:
        proc = subprocess.run([sys.executable, "-X", "utf8", tool, *args],
                              cwd=wt, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=600)
    except Exception as exc:  # noqa: BLE001 —— 超时/环境异常按拒收
        return None, "运行 tools/%s 异常：%r" % (tool_rel, exc)
    return proc, ""


def _composition_kernel_gate(tid, branch, changed):
    """组合门 + 内核守卫（条件门）：返回 (ok, detail)。不写 master、不改 registry/manifest。"""
    files = [str(f).replace("\\", "/") for f in (changed or []) if str(f).strip()]
    if not files:
        return True, "无改动文件（跳过）"
    detail = []

    # 1) 组合声明改动：registry × manifests 漂移（阻断）
    if any(f.startswith(COMPOSITION_TRIGGER_PREFIX) for f in files):
        proc, err = _run_branch_tool(tid, "composition.py", ["plan", "--strict", "--json"])
        if err:
            return False, err
        try:
            payload = json.loads((proc.stdout or "").strip() or "{}")
        except ValueError:
            return False, "composition plan --strict 输出非 JSON（rc=%d）" % proc.returncode
        if proc.returncode != 0 or not payload.get("ok", False):
            brief = "；".join("%s:%s" % (x.get("kind"), x.get("id"))
                             for x in (payload.get("findings") or [])[:5])
            return False, "composition plan --strict 拒收（rc=%d）：%s" % (proc.returncode, brief or "漂移")
        detail.append("composition plan ✓")

        # 2) 具体 manifest：validate（errors 阻断；P1 warnings 只打印）
        manifests = [f for f in files
                     if f.startswith(COMPOSITION_TRIGGER_PREFIX) and f.endswith("/manifest.yaml")]
        for rel in manifests:
            proc, err = _run_branch_tool(tid, "composition.py", ["validate", rel, "--json"])
            if err:
                return False, err
            try:
                rows = json.loads((proc.stdout or "").strip() or "[]")
            except ValueError:
                return False, "composition validate 输出非 JSON（rc=%d）" % proc.returncode
            bad = [r for r in rows if not r.get("ok")]
            if proc.returncode != 0 or bad:
                first = bad[0].get("errors") if bad else ""
                return False, "composition validate 拒收 %s：%s" % (rel, first)
            for r in rows:
                for w in (r.get("warnings") or [])[:3]:
                    print("    组合告警 %s：%s" % (rel, w))
        detail.append("composition validate ✓（manifest %d）" % len(manifests))

    # 3) 内核守卫：内核契约文件改动必须带 ADR 号（--adr 或提交信息含 ADR-号）
    proc, err = _run_branch_tool(
        tid, "kernel_guard.py",
        ["--files", ",".join(files), "--commit-msg", _branch_tip_commit_msg(tid)])
    if err:
        return False, err
    if proc.returncode != 0:
        return False, "kernel guard 拒收（提交信息需带 ADR-号）：%s" % (
            (proc.stdout or "").strip()[-240:])
    detail.append("kernel_guard ✓")
    return True, "；".join(detail)
```

`cmd_merge` 内插入（紧跟质量闸 `return 1` 块之后）：

```python
    _ck0 = time.perf_counter()
    ck_ok, ck_detail = _composition_kernel_gate(tid, t["branch"], _bundle_changed)
    _MERGE_TIMINGS.append(("组合/内核门", time.perf_counter() - _ck0))
    if ck_ok:
        print("  %-8s ✓ %s" % ("组合/内核", ck_detail))
    else:
        print("  %-8s ✗ %s" % ("组合/内核", ck_detail[:200]))
        print("门未过，拒绝合并。修完重跑。")
        t["status"] = "review"
        _save(tasks, changed=(tid,))
        _emit("guard_fail", tid, "组合/内核门未过：%s" % ck_detail[:120])
        return 1
```

### 2.2 为什么这样挂（设计理由）

- **不进 `GATES` 常量**：GATES 是每单都跑的固定门（L2229 循环），组合/内核门是条件门，且其依赖 `_bundle_changed`（L2293 才算出）；插在质量闸后语义为"测试通过后再做声明/内核对账"，失败点最贴近合并临界区。
- **不放进 L2 门之前**：L2 门是人工豁免通道（K-STOCK-416），组合/内核门不应与"宿主机级危险动作"混淆审批语义。
- **失败即回 review + `guard_fail` 事件**：与既有九门/质量闸一致，工单可追溯、事件层可度量（AGENTS §1 全量回流）。
- **纯文档单自动跳过组合命令**：changed 不含 `compositions/**` 时不跑 plan/validate，符合"文档/组合声明随时合并"（ADGS §8）与 merge_preflight 的 docs_only 放行口径（`tools/merge_preflight.py` L146–L150）。
- **kernel_guard 对所有非空 changed 都跑**：它自身很快（读 YAML + 列表比对，<1s），且是"内核改动必须带 ADR"的唯一执行点（ADR-015 §2.4/§7）。

### 2.3 接线提交本身的注意点（重要）

- 接线要改 `tools/devtask.py`，它是 `platform/kernel_v1.yaml` 中 `K8_governance` 的 `kind=gate, status=existing` artifact → **接线单的提交信息必须带 ADR 号**（建议 `ADR-015`，组合门/内核守卫即 ADR-015 决策项 2），否则 `kernel_guard` 在接线生效后会对"改 devtask.py 的本次合并"拒收。
- 本批 P1 工具收尾同理：`tools/composition.py`、`tools/kernel_guard.py` 均为 K0 artifact → 本批合并提交信息必须带 `ADR-015`（例：`feat(P1): composition plan/diff/retire + kernel_guard --commit-msg ADR-015`）。
- `platform/kernel_v1.yaml` 中 K0 note 仍写 "plan/diff/retire 待补"（已过时）：建议接线提交同步更新该 note（`platform/kernel_v1.yaml` 是共享文件，需单独 owner/评审，不在本批写域）。

## 三、失败语义表

| # | 场景 | 工具 rc | devtask 行为 | 工单状态 | 事件 | 人工处置 |
|---|---|---|---|---|---|---|
| 1 | `compositions/**` 改动导致 registry×manifest 漂移 | plan=1 | 拒收，打印前 5 条 finding | review | `guard_fail` | 修 registry 或 manifest 使其一致后重跑 merge |
| 2 | plan 输出非 JSON / 工具异常 / 超时 | 2 或异常 | 拒收（fail-visible） | review | `guard_fail` | 检查 worktree 完整性 / Python 依赖（yaml、jsonschema） |
| 3 | 变更 manifest 自身 errors>0（schema/硬引用） | validate=1 | 拒收 | review | `guard_fail` | 按打印的 ERR 修 manifest |
| 4 | 变更 manifest 只有软引用 warnings | validate=0 | 放行，打印前 3 条告警 | — | — | P2 resolver 收口后升 strict（另一张单） |
| 5 | 内核 artifact 改动，分支 tip 提交信息无 ADR-号 | kernel=1 | 拒收，引用 kernel_guard 输出 | review | `guard_fail` | 用 `git commit --amend` 在提交信息补 `ADR-0xx`（或新 commit 带 ADR 号）后重跑 merge；devtask 要求分支有提交，amend 不改变该前提 |
| 6 | kernel_guard 工具缺失 / kernel_v1.yaml 不可解析 | 异常 | 拒收（fail-visible） | review | `guard_fail` | 修复 worktree/内核清单后重跑 |
| 7 | 纯文档/配置单（无 compositions、无内核 artifact） | — | 组合命令跳过；kernel_guard 无命中放行 | — | — | 无需处置 |
| 8 | 紧急/回滚需绕过 | — | 仅既有 `DEVTOOL_MERGE_GATE=0`（回滚/紧急，回单注明"绕过质量闸+组合/内核门"） | review→done | `guard_fail` 留痕 | 工单 note 写绕过原因 + 五要素；24h 内补 ADR（ADR-015 §7）；不新增独立绕过硬开关 |

**不变量**：门不写 master、不写 `compositions/**`、不写注册表、不启停服务、不碰端口/计划任务/凭据；`plan`/`validate`/`diff`/`retire --dry-run` 全部只读。

## 四、第二道（P1.2，可选）：pre-merge-commit 钩子

- **目的**：防 `git merge` 直连绕过 devtask（既有钩子已防"绕过测试门"，本门补"绕过组合/内核门"），落实 ADR-015 §6「声明必须被执行点消费」。
- **挂载点**：`tools/merge_preflight.py::main`，在 L240 `files = changed_files()` 之后、L249 跑 `test_gate` 之前插入 `_composition_kernel_hook(files)`（约 15 行，失败 `return 1` 中止合并）。
- **命令与差异**：
  - 组合：`python -X utf8 tools/composition.py plan --strict --json`（cwd=REPO；钩子阶段只做只读漂移检查）。
  - 内核：`python -X utf8 tools/kernel_guard.py --files <逗号> --commit-msg <MERGE_HEAD tip 提交信息>`；提交信息取 `git -C <REPO> log -1 --format=%B MERGE_HEAD`（钩子运行时 HEAD=目标分支、MERGE_HEAD=待合入分支，取待合入分支 tip 的提交信息才是开发者的 ADR 声明）。
  - 钩子拿不到任务单上下文 → 只做"拒/放"，不做工单状态与事件；`docs_only` 仍按 L146–L150 口径放行（`compositions/**` 的 `.yaml` 不在 docs 前缀，仍会检查）。
- **风险**：钩子失败会在 git 中留下半合并态 → 处置 `git merge --abort`（与既有钩子失败一致）。故 P1.2 之前先只接 A。

## 五、回滚预案（五要素）

1. **命令全文（回滚）**：`git revert <接线提交sha>`（只改 `tools/devtask.py` 的接线块与测试文件；无 schema/数据/注册表/服务变更）。
2. **影响面**：仅 `devtask merge` 少一道条件门；不改变任何 manifest/registry/内核清单内容；不影响已合并提交与运行时。
3. **不可逆点**：无（纯代码门；不删文件、不写数据、不停服务、不改计划任务/凭据/端口）。
4. **替代方案与已尝试**：若只是某单被误拦 → 先修该单数据（plan/validate 均给出 finding 明细），不要回滚门；若门造成批量 merge 死锁或性能不可接受 → 回滚接线提交；期间可用 `DEVTOOL_MERGE_GATE=0` 应急（留痕）。
5. **回滚验证**：`python -m pytest tools/tests/test_platform_composition.py -q` 仍全绿；窗口期内做一次真实文档单合并演练；回滚窗口内若有内核 artifact 合并 → 列待补 ADR 清单，ARCH1 24h 内补审（ADR-015 §7）。
6. **RTO**：单文件 revert + 重跑门，<5 分钟；无需停机/迁移/通知外部。

## 六、验收与证据包（DOD）

| # | 命令 | 预期 |
|---|---|---|
| 1 | `python -X utf8 tools/composition.py validate --all` | rc=0；7 个组合；errors=0 |
| 2 | `python -X utf8 tools/composition.py plan --strict --json` | rc=0；`ok=true`；`findings=[]`（当前无漂移） |
| 3 | `python -X utf8 tools/composition.py diff compositions/<id>/manifest.yaml --registry --strict`（×7） | rc=0；无差异 |
| 4 | `python -X utf8 tools/kernel_guard.py --list` | rc=0；列出 K0/K8 artifacts 含 `platform/kernel_v1.yaml` |
| 5 | `python -X utf8 tools/kernel_guard.py --files platform/kernel_v1.yaml` | rc=1 BLOCK |
| 6 | `python -X utf8 tools/kernel_guard.py --files platform/kernel_v1.yaml --commit-msg "x ADR-015"` | rc=0，输出"已关联 ADR-015（来源 --commit-msg）" |
| 7 | `python -m pytest tools/tests/test_platform_composition.py -q` | 25 passed（验收下限 ≥9） |
| 8 | 接线演练（窗口内 16:30–17:00 / 21:40–22:10）：一张纯文档单 + 一张改 `compositions/**` 的单 | 前者放行；后者若制造漂移被拦、修正后放行 |
| 9 | 证据落盘 | 命令 + rc + 日志/产物路径 + 工单号 + 回滚命令；接线提交信息含 `ADR-015` |

预期门耗时：组合门 <2s、kernel guard <1s；无网络、无服务调用、无外部依赖（仅 `PyYAML`/`jsonschema`，Python311 已装）。

## 七、本批交付边界（明确未做）

- **未修改 `tools/devtask.py`**（按任务边界；§二 仅为评审用接线方案，接线由 Lead 决定 owner 与窗口）。
- 未修改 `platform/kernel_v1.yaml`（K0 note 过时项、status 升级需共享文件 owner 单独评审）。
- `composition.py retire` 只有 `--dry-run` 预演，**不提供 `--apply`**：退役写盘走 P2 审阅通道 + 人工批准（ADR-015 变更纪律：不得物理删除 manifest）。
- 组合门未接 `GATES` 常量与 pre-merge 钩子（P1.2，见 §四）。
- 未改 registry / manifests / 运行时 / 端口 / 计划任务 / 凭据；未做 git commit/merge（本批仅文件交付）。

## 八、风险与未决

| # | 风险/未决 | 处置建议 |
|---|---|---|
| R1 | P1 `validate --strict` 全局恒拒（28 条软引用 warning） | 门只用 `plan --strict` + 单 manifest `errors` 阻断；P2 resolver 收口后升级 |
| R2 | worktree 缺失时门 fail-visible 拒绝 | 与质量闸一致；异常 worktree 先修复再 merge |
| R3 | 开发者提交信息未写 ADR 号（AGENTS §4 只强制工单号） | 门提示明确（`--commit-msg` 与 `--adr` 双路径）；接线后需 A0/ARCH1 在 AGENTS/ADR 通告一次（写域外） |
| R4 | 门依赖 JSON 输出稳定性 | `--json` 已是机器契约（stdout 纯 JSON，人类汇总走 stderr）；本次测试锁 `test_diff_json_shape` / `test_plan_json_clean` |
| U1 | 是否新增独立绕过开关 `DEVTOOL_COMPOSITION_GATE=0` | 建议不新增；紧急走既有 `DEVTOOL_MERGE_GATE=0` + 工单留痕 + 24h 补 ADR |
| U2 | P1.2 钩子是否对每单无条件跑 plan | 建议是（<2s 且无副作用），P1.2 单独立单 |
| U3 | `compositions/**` 变更是否要求 registry 与 manifest 同提交 | 建议是；`plan --strict` 已强制一致（version/lifecycle/owner/tier/host/path） |

## 九、相关文件

- `tools/composition.py`（P1 v0.2：validate/plan/diff/retire/status/audit）
- `tools/kernel_guard.py`（P1：`--files/--staged/--adr/--commit-msg/--list`）
- `tools/tests/test_platform_composition.py`（23 用例，含 plan/diff/retire/commit-msg）
- `tools/devtask.py`（`cmd_merge`、`GATES`、`_quality_gate`、`_merge_changed_files`）
- `tools/merge_preflight.py`（pre-merge-commit 钩子，P1.2 挂载点）
- `compositions/README.md`、`compositions/registry.json`、`compositions/_schema/composition.schema.json`
- `platform/kernel_v1.yaml`（K0/K8 artifacts 冻结清单）
- `docs/adr/ADR-015-platform-kernel-composition-model.md`、`docs/adr/ADR-016-single-host-integration.md`

---

## 实现注记（2026-11-02，P1.2）

- 已接线：`tools/devtask.py::_composition_kernel_gate`（质量闸后/合并锁前）+ `tools/merge_preflight.py::composition_kernel_preflight`（钩子侧，共用同一判定内核 `composition_kernel_check`，单一实现防漂移）。
- **钩子复检刻意放在 `docs_only` 放行之前**（防内核契约类 `.md` 走纯文档豁免被直连 git merge 绕过）；以此实现为准，§四原措辞按本注记解释。
- 测试：`tools/tests/test_devtask_composition_gate.py` 19 用例；应急通道沿用 `DEVTOOL_MERGE_GATE=0`（留痕+24h 补 ADR）。

