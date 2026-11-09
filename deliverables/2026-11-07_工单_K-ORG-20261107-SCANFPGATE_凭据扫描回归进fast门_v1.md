# 工单 K-ORG-20261107-SCANFPGATE：凭据扫描器回归测试进 fast 门

| 项 | 值 |
|---|---|
| 编号 | K-ORG-20261107-SCANFPGATE |
| 日期 | 2026-11-07 |
| 状态 | **已执行**（老田按「关键单文件才进 fast」口径裁定纳入） |
| 前置 | K-ORG-20261107-GATESAFEURL（`c764f8bfe`）、K-ORG-20261107-ENTROPY（`5347ea5a7`） |
| 触发 | ENTROPY 轮补了 HIGH_ENTROPY 首个单测，但该测试当时**不在任何自动入口** |

---

## 一、为什么判定"该进"（不是所有新测试都该进）

判断依据是**失效的方向**，不是文件的重要性感受。

### 1.1 它是唯一守着"每次 commit 都跑的东西"的测试

`tools/scan_secrets.py` 有两条调用链，都指向同一个扫描器：

| 调用点 | 形态 | 失效后果 |
|---|---|---|
| `.git/hooks/pre-commit` | **WARN-ONLY + fail-open** | 静默放过，不影响提交 |
| `tools/devtask.py::_secret_scan_gate` | merge 侧**硬门**（扫 `master...branch`） | 硬门变纸门 |

pre-commit 那层明确不阻断（除非 `ZB_SECRETS_BLOCK=1`），所以"提交时打印了几行警告"**不构成**任何拦截能力。唯一真正拦人的是 devtask 的 merge 硬门——而它内部的判定与脱敏逻辑，正是 `scan_secrets.py` 本身。

### 1.2 扫描器退化的方向是"少报"，不是"报错"

这是决定性理由。对比一下两类组件的退化：

- 业务代码退化 → 抛异常 → 有 traceback → 迟早被发现
- 扫描器退化 → **少报几条 → 一切正常，rc=0，静默**

也就是说，`scan_secrets.py` 里任何一处豁免写宽了、正则被改坏、分支被短路，现象都是"干净通过"。**没有任何运行时信号会提示你它已经失灵**，只有一条断言能发现——而这条断言此前不存在于任何自动入口。

### 1.3 它此前是彻底的孤儿

全仓检索结果：

| 检索项 | 结果 |
|---|---|
| `-Suite tools` 的自动调用者 | **0**（`all` = portal/kstock/agent/quant/ide，不含 tools） |
| `test_scan_secrets_fp.py` 被任何配置/脚本引用 | **0** |

即该文件只在有人手打 `-Suite tools` 时才跑。这与批 5 那两条回环守卫"登记了但永不执行"是同一类失效，只是这次连登记都没有。

### 1.4 它刚好是变更面最热的文件

`scan_secrets.py` 有 8 轮误报治理史（kwarg 自引用、字面量、epoch、boundary、字典键、PascalCase、布尔开关、路径成分），**前 8 轮全部零单测**，靠人工补 allowlist。第 9 轮（V4）才补上 9 条。正在被反复修改的文件恰好零覆盖，是典型的高风险组合。

### 1.5 成本核算

| 项 | 实测 |
|---|---|
| 用例数 | 42 条 |
| 耗时 | **0.62s**（比 GATESAFEURL 那次进门的 `test_safe_urlopen.py` 还快） |
| 外部依赖 | 无。全部 `tmp_path` 合成夹具，不读仓库文件、不联网、不碰环境变量 |
| 慢用例 | 最慢 setup 0.05s，其余 ≤0.01s |

---

## 二、代价：5 处冻结计数（工单已全量核对，无遗漏）

上次 GATESAFEURL 工单列了 4 处、实际 6 处（漏了 `org_gate`）。这次改前先全仓 grep 字面量：

| # | 文件 | 改动 |
|---|---|---|
| 1 | `tools/test_gate.ps1` | `tools_key` 数组 +1 项（保 UTF-8 BOM + CRLF） |
| 2 | `test_test_gate_b1.py:103` | `TOOLS_KEY` 集合 +1 条（含注释） |
| 3 | `test_test_gate_b1.py:146` | `len(TOOLS_KEY) == 18` → `19` |
| 4 | `test_test_gate_b1.py:148` | `len(all_files) == 93` → `94`，文案补 `+凭据扫描回归(1)` |
| 5 | `test_test_gate_a5strict.py:77` | `("tools_key", 18)` → `19` |
| 6 | `test_test_gate_org_gate.py:65` | 同上（姊妹文件，**上次的漏项，这次主动核对到了**） |

`test_gate_coverage_w5.py` 无需改：它只冻结组合/存在性/重复，覆盖率是 `>=` 下限（复跑 11 passed 印证）。

补丁脚本 `tools/research/patch_scanfpgate.py`，沿用 batch5/6 与 gatesafeurl 的约定：`--dry/--apply/--check`、改前断言旧串 `count==1`、幂等判定在断言之前、改后 `ast.parse` 自证、字节级保 BOM 与行尾。

---

## 三、验证

| 项 | 结果 |
|---|---|
| 补丁 `--dry` | 6/6 锚点唯一（无 `missing`） |
| 补丁 `--check`（落盘后） | **6/6 applied** |
| 目标文件独立跑 | **42 passed, 0.62s** |
| 三个冻结测试（b1 + a5strict + org_gate） | **17 passed** |
| `test_gate_ps1_bom_and_parser` | **1 passed** |
| `test_gate_coverage_w5.py` | **11 passed** |
| BOM / 行尾 | ps1 `BOM=True`、663 行全 CRLF、裸 LF=0；三个 py 亦无混合 |

### 3.1 "新文件真的进了套件"的反证

门禁日志只打总数、不回显文件名，所以"没报错"不能证明它被执行。用收集数守恒反证：

```
19 个文件（含 test_scan_secrets_fp.py）→ 175 collected
18 个文件（去掉它）                    → 133 collected
差 42，与 pytest 独立跑的 "42 passed" 严丝合缝
```

`tools_key` 现 19 项，末位为 `tools/tests/test_scan_secrets_fp.py`。

---

## 四、遗留

- `test_gate.ps1` 头注释里"fast 增 B1+B2 关键集三套件（…共 88 文件）"的数字自 B2 起就已漂移（实际先 92、后 93、现 94）。属历史注释，本次未动，避免把一次计数变更搅进文档改写；如需校正建议单独一刀。
- `agent_platform/tools/scan_secrets.py` 是另一份扫描器，其测试（`agent_platform/tests/test_scan_secrets.py`）走 `agent` 套件，**天然在夜间 `all` 内**，不缺覆盖。本次只管 `tools/` 这份。
