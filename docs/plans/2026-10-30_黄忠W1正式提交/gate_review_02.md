# 七检解读 02：语法门拦截真实案例（WO-SYNTAX-GATE-20261028）
> STATUS: unspecified（owner: TBD；2026-11-07 前补状态或标 superseded）



> 工具：`tools/check_syntax.py`（语法门：SyntaxError 阻断；`--fail-on-warning` 下 SyntaxWarning 也阻断）
> 拦截案例：merge 前置语法门在 `quant_backtest/kg_build_p2.py` 报 invalid escape 告警 → merge 被拒 → 修复后复跑告警 0 才放行
> 修复提交：2b3186685（`quant_backtest/kg_build_p2.py` 3 处 `\` → `\\`，3 insertions / 3 deletions）· 合并提交：c066407e5（merge task/WO-SYNTAX-GATE-20261028，2026-10-28 07:07）
> 核对：`git merge-base --is-ancestor c066407e5 HEAD` → 退出码 0（master 祖先，2026-10-30 实测）

## 一、拦截原始输出（2026-10-28 记录）

```
语法门  ✗ 扫描 2893 / 错误 0 / 告警 1 WARN:quant_backtest/kg_build_p2.py:invalid escape sequence '\ '
```

- 来源路径：`docs/plans/2026-10-28_黄忠W1验收样例与批改标准_v1.md:56`（原文记录）；`docs/plans/2026-10-28_黄忠W1示范答卷/gate_review_02.md:6` 同文引录。
- 时点说明：2893 为 2026-10-28 当日的 `git ls-files '*.py'` 去 vendored 后文件数；2026-10-30 现场扫描为 **3059 文件**（仓内 py 文件增长所致，时点不同不可混用）。
- 判读：错误 0 但告警 1 → 门禁判 FAIL（`--fail-on-warning` 下 `code=1`）→ merge 被拒；教训=门禁看「错误与告警都为 0」，不是只看错误数。

## 二、现场复现（2026-10-30，worktree）

```powershell
# 取修复前版本并复现告警（Python 3.12 对非法转义发 SyntaxWarning）
git show 2b3186685^:quant_backtest/kg_build_p2.py   # 修复前
git show 2b3186685:quant_backtest/kg_build_p2.py    # 修复后
# 复现结果：修复前 warning_count=2（SyntaxWarning: invalid escape sequence '\ '），
#           修复后 warning_count=0，当前工作树文件 warning_count=0
# 现场门禁扫描
C:\项目\py312\python.exe -X utf8 tools\check_syntax.py --fail-on-warning
# 实测：语法门：3059 文件 / 错误 2 / 告警 0；结果 FAIL
#   错误 2 = tools/research/h10_quality_sensitivity.py、tools/s2_sign_pilot.py 的 BOM（U+FEFF）编译错误，
#   属仓内既有问题，与本单/本案例无关（本单为 docs-only，不修代码；已记入 self_review 问题 3）
# 定向验证案例文件（修复后应无告警）
C:\项目\py312\python.exe -X utf8 tools\check_syntax.py --fail-on-warning --files quant_backtest/kg_build_p2.py
```

- 证据路径：修复前文件副本 `C:\Temp\hz_kg_build_p2_before_fix.py`；复现输出交付副本 `C:\项目\deliverables\2026-10-30_黄忠W1正式提交_证据\syntax_gate_repro.txt`；门禁现场输出 `C:\Temp\hzev_syntax_gate_current.json`（副本同上目录 `syntax_gate_20261030.json`）。
- 复现口径：`compile()` + `warnings.catch_warnings(record=True)` + `simplefilter("always")`，统计含 `invalid escape` 的消息；3 处字符中 1 处在注释（注释不产生告警），故告警消息 2 条，check_syntax 每文件只记首条 → 历史记录「告警 1」自洽。

## 三、逐项判读（七检项全覆盖）

| 检查项 | 本案例结果 | 判读 / 证据 |
|---|---|---|
| compileall | ok=True（quality_gate 的代码编译项通过） | 但 `compileall -q` 只拦 SyntaxError、不因告警失败；语法门比 compileall 更严格，故 compileall 绿不能替代语法门 |
| ports | ok=True | 与本失败无关；端口注册自检独立运行（注册/冲突口径不受语法告警影响） |
| secrets | ok=True | 与本失败无关；明文扫描不读语法告警 |
| names | ok=True | 与本失败无关；名称唯一性为独立检查 |
| capabilities | ok=True | 与本失败无关；能力自检读注册表，不编译 py 文件 |
| evals | ok=True | 与本失败无关；行为评估 smoke 不覆盖非法转义这类静态告警 |
| tests | ok=True | 与本失败无关；单测可跑不代表语法门放行——告警类问题只有语法门能拦 |

## 四、结论与通过线

- 通过线：`错误 0` 且 `告警 0` 且 `code=0` 才放行（阈值 `error_count <= 0`、`warning_count <= 0`）。
- 修复判定：`2b3186685` 将 3 处 `\ ` 改为 `\\ `，复跑该文件告警 `ok=0`；`c066407e5` 合并后「merge 语法门恢复」。
- 回滚口径：回退该 3 行字符会让告警重新出现 → 语法门重新 FAIL，故该修复属「门禁必需」，不得以「告警无害」自我豁免。
- 门禁纪律：不得用 `-W ignore`、不得跳过语法门、不得只跑 compileall 冒充语法门。
