# 工单 K-ORG-20261107-GATESAFEURL：回环豁免断言进 fast 门

| 项 | 值 |
|---|---|
| 工单号 | `K-ORG-20261107-GATESAFEURL` |
| 提出 | 2026-11-07 |
| 类型 | 质量门禁补漏（写面小、但涉及冻结清单） |
| 优先级 | P2（不阻断当前交付，但已写好的守卫处于"永不执行"状态） |
| 状态 | **已执行**（2026-11-07，见 §九；实际改 6 处，比工单多 1 处补漏） |
| 前置 | 批 5（`93a904901`）、批 6（`478b7f1db`） |

---

## 一、问题：写好的断言，没有任何自动入口会执行它

批 5 在 `tools/tests/test_safe_urlopen.py` 里加了两条守卫：

1. `test_every_copy_has_loopback_exemption` —— 仓库内每份 `safe_urlopen.py` 副本都必须含 `is_loopback` / `_open_no_proxy`。防的是「再长出第 7 份 `safe_http.py` 那种退化副本」。
2. `test_legion_command_safe_http_delegates_to_shared_impl` —— `legion_command/safe_http.py` 必须是委派，不得自带实现。

问题是这两条**永远不会被自动跑到**：

| 入口 | 含哪些套件 | 是否含 `tools/tests` |
|---|---|---|
| `-Suite fast` | portal, kstock, ide, agent_key, quant_key, tools_key, datawarehouse | **否** |
| `-Suite all`（夜间全量） | portal, kstock, agent, quant, ide | **否** |
| `-Suite tools` | tools（= `tools/tests` 整目录） | 是，但**只能手工触发** |

也就是说，`all` 也不是"全量"——它不含 `tools`。这条断言写在了一个只有人主动想起来才会跑的地方，等于没写。

`test_gate.ps1` 头注释里写的是"tools 套件供夜间/手动全量，不进 fast"，但事实上夜间 `all` 也不含它，这个注释与代码不一致（顺带建议修正注释，避免下次又按注释以为夜间会跑）。

---

## 二、方案 A（推荐）：单文件插入 `tools_key`

`tools_key` 本来就是"文件列表型"套件，专为"把单文件送进 fast 门"设计，且基线查找支持按 `suiteKey`（`tools/tests`）回退。把 `tools/tests/test_safe_urlopen.py` 一个文件挂进去即可。

支撑数据（已实测）：

| 项 | 值 |
|---|---|
| 该文件用例数 | 8 |
| 门禁精确调用形态耗时 | **0.81s**（`python -m pytest tools/tests/test_safe_urlopen.py -q --timeout=120 -p no:cacheprovider`，Python311） |
| 外部依赖 | 无网络、无服务；`git ls-files` 取副本清单，无 git 环境自动 skip |
| 结果 | `8 passed` |

成本约 1s，对 9 分钟量级的 fast 门可忽略。

---

## 三、必须同批改动的四处（不做则冻结测试红）

冻结清单不是"加一行"就完事，有两处测试把**成员与数量**都写死了。

### 1. `tools/test_gate.ps1` — `$paths['tools_key']` 追加一行

位置：`tools_key` 数组末尾（现为 `'tools/tests/test_market_analysis.py'`，第 266 行）。追加：

```powershell
        # ---- 回环禁代理豁免副本一致性（2026-11-07 K-ORG-20261107-GATESAFEURL，+1）----
        # 每份 safe_urlopen.py 必须含 is_loopback/_open_no_proxy；legion_command/safe_http.py
        # 必须委派。第 7 份退化副本曾静默存在半个月，扫描器按"调的是 safe_urlopen"判
        # GUARDED，看不见它。8 条纯离线，约 1s。
        'tools/tests/test_safe_urlopen.py'
```

**注意**：该文件必须保持 **UTF-8 BOM**——`test_test_gate_a5strict.py::test_gate_ps1_bom_and_parser` 断言 `raw[:3] == b"\xef\xbb\xbf"`，且要能被 PowerShell 5.1 解析。改完必须跑这条测试。

### 2. `tools/tests/test_test_gate_b1.py` — 三处

```python
TOOLS_KEY = {
    ...
    "tools/tests/test_market_analysis.py",
    # ---- 回环豁免副本一致性（2026-11-07 K-ORG-20261107-GATESAFEURL，+1）----
    "tools/tests/test_safe_urlopen.py",
}
```

- 第 144 行：`assert len(AGENT_KEY) == 52 and len(QUANT_KEY) == 23 and len(TOOLS_KEY) == 17` → `== 18`
- 第 146 行：`assert len(all_files) == 92, "B1(57)+B2(31)+黄忠聚合入口(1)+学习治理契约(2)+盘后分析(1) 应为 92 文件"` → `== 93`，文案追加 `+回环豁免(1)`，数字改 93

### 3. `tools/tests/test_test_gate_a5strict.py` — 一处

第 77 行：

```python
for name, count in (("agent_key", 52), ("quant_key", 23), ("tools_key", 17)):
```

→ `("tools_key", 18)`

### 4. `tools/test_baseline.json` — 本次无需改

该文件 8 条全绿地进 fast，不需要 `known_failures` 登记。基线查找会按 `suiteKey='tools/tests'` 回退取既有登记，不影响。

> 若日后该文件出现环境相关失败（如 CI 无 git），需在 `tools/test_baseline.json` 的 `"tools/tests"` 键下登记（`env_failures` 而非 `known_failures`，保持"环境问题不掩盖真实回归"的口径）。

---

## 四、不需要改的两处（已核对）

| 文件 | 为什么不用改 |
|---|---|
| `agent_platform/tests/test_gate_coverage_w5.py` | 只冻结 fast 的**套件组合**、目标文件存在性、套件内重复登记；覆盖率是 `>=` 下限（`COVERAGE_FLOOR_PCT` / `FILES_FLOOR_PCT`），加文件只会抬高覆盖率 |
| `tools/tests/test_huangzhong_tools_suite_gate.py` | 是黄忠 15 文件的聚合入口，与本单无交集 |

---

## 五、方案 B（否决）：把整个 `tools/tests` 纳入 fast

`tools/tests` 现有 **374** 个测试文件（`ls tools/tests/test_*.py | wc -l`）。其中相当一部分依赖网络、生产服务、发布物或本机端口，且 fast 门已因 sys.path 冲突而采用"每套件独立 pytest"的结构。整目录纳入会让 fast 门时长与稳定性都失控。否决。

---

## 六、影响面 / 风险 / 回滚

**影响面**
- fast 门：+1 文件、+8 用例、约 +1s。
- `-Suite tools`（手工）：两个冻结测试的期望值同步 +1，仍应全绿。
- 其它套件、其它人分支：仅 `tools_key` 清单条目变化；`test_gate_coverage_w5.py` 在 ide 套件里跑，其断言不受影响（已核对）。

**风险**
1. 忘改冻结测试 → `-Suite tools` 红（不会在 fast 里暴露，因为这两个测试本身不在 fast 清单内）。这是主要风险点：**改完必须手工跑一次 `-Suite tools` 的两个冻结文件**确认。
2. 破坏 BOM → `test_gate_ps1_bom_and_parser` 红，且 PowerShell 5.1 中文可能乱码。
3. `tools/tests/test_safe_urlopen.py` 依赖 `git ls-files`；干净 worktree 内有 git 正常，无 git 环境会自动 skip 而非红。

**回滚**：四处改动即四条独立 hunk，`git revert` 单个提交即可；无数据/状态副作用。

---

## 七、验证清单（可复跑）

```bash
# 1) 文件本身在门禁形态下通过（用门禁真实解释器，PATH 里那份没有 pytest-timeout）
"C:/Program Files/Python311/python.exe" -m pytest tools/tests/test_safe_urlopen.py \
  -q --timeout=120 -p no:cacheprovider
# 期望 8 passed in <1s

# 2) 两个冻结测试（改清单后必须绿）
"C:/Program Files/Python311/python.exe" -m pytest \
  tools/tests/test_test_gate_b1.py tools/tests/test_test_gate_a5strict.py -q -p no:cacheprovider

# 3) BOM + PS5.1 解析
"C:/Program Files/Python311/python.exe" -m pytest \
  tools/tests/test_test_gate_a5strict.py::test_gate_ps1_bom_and_parser -q -p no:cacheprovider

# 4) 全量门禁
powershell -NoProfile -ExecutionPolicy Bypass -File tools\test_gate.ps1 -Suite fast
```

---

## 八、待老田裁定

1. **是否执行本工单**（改动落在 `tools/test_gate.ps1` + 两个冻结测试，属共享门禁配置）。
2. **顺手修注释**：`test_gate.ps1` 头注释"tools 套件供夜间/手动全量"与事实不符（夜间 `all` 不含 tools）。建议改为"tools 套件仅手工触发；关键单文件请登记进 `tools_key`"。是否同批改，请定。

> 本工单不改任何配置；上面所有"现状"数据均为 2026-11-07 实测，可按下节命令复核。

---

## 九、执行记录（2026-11-07 10:2x）

### 9.1 与工单的差异：实际改 6 处，不是 4 处

工单 §三 漏登记了一个**同构冻结点**，全仓检索 `len(TOOLS_KEY) == 17` / `("tools_key", 17)` 时扫出：

```
tools/tests/test_test_gate_org_gate.py:65
    for name, count in (("agent_key", 52), ("quant_key", 23), ("tools_key", 17)):
```

它和 `test_test_gate_a5strict.py:77` 是**完全相同的一句**（org_gate 是 a5strict 的姊妹文件，都校验 `test_gate.ps1` 的清单不回归）。只改工单那四处，`-Suite tools` 仍会因这一条红。

**若只改工单写的四处，风险 1（忘改冻结测试）实际已经发生了一次**——只是工单没把这一处列进来。

### 9.2 落地的 6 个 hunk

| # | 文件 | 改动 | 来源 |
|---|---|---|---|
| 1 | `tools/test_gate.ps1` | `tools_key` 数组 `'tools/tests/test_market_analysis.py'` 补逗号，追加 4 行注释 + `'tools/tests/test_safe_urlopen.py'` | 工单 §三.1 |
| 2 | `tools/tests/test_test_gate_b1.py` | `TOOLS_KEY` 集合追加 `"tools/tests/test_safe_urlopen.py"` + 1 行注释 | 工单 §三.2 |
| 3 | 同上 144 行 | `len(TOOLS_KEY) == 17` → `18` | 工单 §三.2 |
| 4 | 同上 146 行 | `len(all_files) == 92` → `93`，文案加 `+回环豁免(1)` | 工单 §三.2 |
| 5 | `tools/tests/test_test_gate_a5strict.py:77` | `("tools_key", 17)` → `18` | 工单 §三.3 |
| 6 | `tools/tests/test_test_gate_org_gate.py:65` | 同上 | **工单漏登记，本次补** |

补丁脚本：`tools/research/patch_gatesafeurl.py`（`--dry/--apply/--check`，断言旧串 count==1、幂等、保留 BOM 与 CRLF、改完 `ast.parse` 自证）。

### 9.3 验证结果（全部通过）

| 项 | 命令 | 结果 |
|---|---|---|
| 目标文件门禁形态 | `pytest tools/tests/test_safe_urlopen.py -q --timeout=120 -p no:cacheprovider` | **8 passed in 1.03s** |
| 三个冻结测试 | `pytest test_test_gate_b1.py test_test_gate_a5strict.py test_test_gate_org_gate.py -q` | **17 passed in 2.44s** |
| BOM + PS5.1 解析 | `pytest ...::test_gate_ps1_bom_and_parser` | **1 passed**；文件 BOM 仍在（`raw[:3]==b"\xef\xbb\xbf"`） |
| w5 覆盖率冻结 | `pytest agent_platform/tests/test_gate_coverage_w5.py -q` | **11 passed**（工单判断正确，不受影响） |
| 行尾 | 字节复核 | ps1 CRLF 653→658 全 CRLF、裸 LF=0；三个 py 亦全 CRLF，无混合 |
| 全量 fast 门 | `test_gate.ps1 -Suite fast` | 12 套件 12m03s；`tools_key` **rc=0 / failed 0 / Known 0 / New 0** |

### 9.4 关键自证：8 条用例真的进了 fast 门

门禁日志只打 `133 passed in 62.18s`，不回显文件名，所以"没报红"不足以证明新文件被跑到。用收集数反证：

```
18 files（含 safe_urlopen）→ 133 tests collected
17 files（去 safe_urlopen）→ 125 tests collected
差 8，与门禁日志的 133 passed 严丝合缝
```

即 `tools_key` 里那 8 条回环豁免断言**确实执行并通过**，不再是"永不执行"状态。

### 9.5 fast 门 7 条失败与本次无关（逐条核对，集合与前次一致）

| 失败 | 归因 |
|---|---|
| `test_acad1_sso_client.py::test_route_baseline_unchanged`<br>`test_d1_envelope_ui.py::test_route_baseline_unchanged`<br>`test_d1_training_page.py::test_route_inventory_unchanged_page_route_not_scanned` | 并行 Agent 新增 `portal/api_jev_wordcloud.py` 一条路由 `('GET','exact','/api/jev/wordcloud')`，未 `--write-baseline`；`[route]` 段同时报的正是这 1 条「新增路由」，两处互证 |
| `test_round7_closeout.py::test_nginx_is_r8_applied`<br>`...::test_cumulative_diff_is_pure_addition`<br>`...::test_pending_r8_matches_applied` | 未跟踪的 `reports/nginx.conf.pending-r8` 与 `portal/nginx.conf` 字节不一致（前次已归因，本批未碰 nginx） |
| `[route]` 新增路由 1 | 同第一行 |

`[org_gate]` 段报 `WARN-ONLY 无法解析为 JSON rc=1`，属 `tools/org_model_consistency.py` 自身 warning，warn-only 不计入 GATE FAIL，与 pytest 侧 `test_test_gate_org_gate.py` 无关（后者不被任何套件调用，本次手工跑绿）。

### 9.6 未做的事

- ~~头注释未改（工单 §八.2）~~ → **已于 2026-11-07 10:5x 补齐**：`test_gate.ps1:10` 那句
  「tools 套件供夜间/手动全量」改为「tools 套件仅手工触发、不进 fast；关键单文件请登记进
  `tools_key`」。字节级替换保 BOM + CRLF，行数不变；`test_gate_ps1_bom_and_parser` 与三个
  冻结测试复跑 17 passed。
- `tools/test_baseline.json` 未动：`tools_key` 本次 Known 0 / New 0，无需登记。

