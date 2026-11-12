# id 稳定性审计报告（R-01）

> 编制：OSINT1（马良）· 2026-11-12 23:45
> 依据：R-01「全库 id 稳定性治理 + 静态回归护栏」（研发方案 v1 §三）
> 方法：`tools/id_stability_lint.py`（tokenize 精确判定）+ 逐文件人工复核用途
> **边界**：本报告只判「id 生成是否确定性」，不裁业务语义、不改他线代码。

---

## 一、执行摘要

| 项 | 结果 |
|---|---|
| 本线缺陷修复 | ✅ `obs_bridge.py` 裸 `hash()` → sha256 内容指纹（已改） |
| 静态检查器 | ✅ 新建 `tools/id_stability_lint.py`（audit/enforce 双模式，tokenize 判定） |
| 钩子块 | ✅ 已注册 `[WB-ID-GUARD]`（`install_guard.py` BLOCKS），`--status` 显示**未安装** |
| 审计范围 | `news_collector/` + `portal/` + `tools/` 共 2440 个受控 `.py` |
| 本线命中 | **0**（修后） |
| 他线遗留命中 | **5 处**（`tools/kb_corpus/` 1、`tools/tests/` 4）——分级上报，未擅自改 |
| 跨进程确定性实证 | ✅ 新实现两次独立进程一致；旧实现两次不一致（见 §五） |

---

## 二、病因：为什么裸 `hash()` 是数据缺陷而非风格问题

Python 内置 `hash()` 对 str/bytes/tuple **按进程加盐**（CPython 默认 `PYTHONHASHSEED` 随机）。后果：

- 同一输入，**两次运行得到两个不同值**；
- 任何用它做 **id / 快照号 / 去重键 / 分片键 / 缓存键** 的代码，都无法跨进程复现。

**两次真实判例**：

| 判例 | 现象 | 后果 |
|---|---|---|
| **R22**（sse 采集器） | 旧 id 用 `hash()`，同帖每日重抓 id 不同 → 按 id 去重失效 | 184 文件 **1774 行仅 672 唯一，1102 行重复（62% 膨胀）** |
| **R-01**（obs_bridge） | `snapshot_id` 用 `hash((source_id, now))` | 同一次采集重放得到不同快照号，观测无法回溯／无法幂等 |

> 注：R-01 的 `snapshot_id` 当前**不参与去重**（每次 run 的 `now` 本身不同），故未造成数据膨胀；属"未爆的技术债"，但因其为观测回溯的唯一锚点，一旦将来用于幂等/对账即失效——故仍须修。

---

## 三、全量普查表（9 个采集/快照模块 + 3 个 portal 快照）

| # | 模块 | id 形态 | 算法 | 确定性 | 碰撞风险 | 判定 |
|---|---|---|---|---|---|---|
| 1 | `sse_collector.py` | `sse:<code>:<hex16>` | `sha256(code\|ts\|content)[:16]` | ✅ 确定 | 极低（64bit） | R22 已修 |
| 2 | `taoguba_collector.py` | 主帖 `tgb:T<topicId>` / 回复 `tgb:R<hex12>` | 外部 topicId / `md5(uid\|ts\|content[:80])[:12]` | ✅ 确定 | 🟡 低：前 80 字相同则碰撞 | 保持（见 §六 D3） |
| 3 | `irm_collector.py` | `irm:<esId>` | 外部平台 id | ✅ 确定 | 无 | 保持 |
| 4 | `news_collector.py` | `sina:<id>` / `em:<code>` / `wsc:<id>` / `ann:<art_code>` / `survey:<key>` / `report:<infoCode>` | 6 类外部 id | ✅ 确定 | 无 | 保持 |
| 5 | `qianping_collector.py` | 无 id（全市场快照按日存） | — | n/a | — | 保持 |
| 6 | `xq_stock_timeline.py` | `xqtl:<code>:<statusId>` | 外部 status id | ✅ 确定 | 无 | 保持 |
| 7 | `xq_kol.py` | `xq_kol:<sid>` | 外部 status id | ✅ 确定 | 无 | 保持 |
| 8 | `obs_bridge.py` | `bridge-<date>-<hex16>` | `sha256(source_id\|now)[:16]` | ✅ 确定 | 极低 | **R-01 本次修** |
| 9 | `portal/watch_admin_snapshot.py` | `uuid4().hex` | UUID v4 随机 | ⚠️ 随机 | 无 | 设计使然（见下） |
| 10 | `portal/watch_tower_snapshot.py` | `uuid4().hex` | UUID v4 随机 | ⚠️ 随机 | 无 | 设计使然（见下） |

**关于 uuid4 的判定（重要，不误伤）**：`watch_*_snapshot.py` 的 `snapshot_id` 用 uuid4 生成——**这是对的**，因为它的语义是"每次生成一个全新快照的唯一标识"，不是"同一内容的确定性指纹"。若把它改成确定性哈希，反而会让两次快照撞成同一个 id。
**但**：若将来要求"同一时刻重跑应得到同一快照号"（幂等回溯），则必须改。当前无此需求，判**非缺陷**。

---

## 四、遗留命中分级（他线，未擅自改）

三目录内 5 处（`--all` 全仓另有 37 处，多在语料/测试夹具，另附说明）：

| 位置 | 代码 | 用途推断 | 级别 | 归属 | 建议 |
|---|---|---|---|---|---|
| `tools/kb_corpus/phase1_dq.py:49` | `min(hash((s, seed)) & 0xFFFFFFFF for s in sh)` | 疑似**分片/采样** | 🟡 **P2** | 知识库线 | 若为分片键：跨进程不稳定 → 每次运行分片不同 → **结果不可复现**，应改 sha256；若仅进程内去重则无害，请归属线确认 |
| `tools/tests/test_kb_qa_bank_v2.py:63` | `hash(question) % 4` | 测试参数分散 | 🟢 P3 | 测试 | 影响：测试用例分配到不同 mode 会**每次运行不同** → flaky。建议换 `sha256(question)[:8] % 4` |
| `tools/tests/test_kb_qa_bank_v2.py:71` | `hash(question) % 3` | 同上 | 🟢 P3 | 测试 | 同上 |
| `tools/tests/test_kb_qa_bank_v3.py:68` | `hash(question) % 4` | 同上 | 🟢 P3 | 测试 | 同上 |
| `tools/tests/test_r1_daily_check.py:113` | `abs(hash((str(now), last_result, last_run_text))) % 1000000` | 临时目录名 | 🟢 P3 | 测试 | 影响：同一输入两次跑得不同 tmp 路径。该测试若意在验"幂等"，用 hash 反其道，建议改确定性 |

> **为什么不由我改**：`tools/kb_corpus/`、`tools/tests/` 属他线/测试资产，改动会触碰其语义与归属；且 `phase1_dq.py` 的用途我未读全，贸然改可能破坏分片逻辑。按治理纪律：**发现问题 → 分级 → 归属 → 上报，不越界代改**。

**`--all` 全仓 42 处的说明**：扣除上述 5 处后另 37 处分布在 `research/`、语料脚本、第三方封装等；本报告**不对其逐个定性**（超出 R-01 三目录范围），仅登记"存在存量"。若要全仓清零，须先按目录划归属。

---

## 五、验收证据（实测，非声明）

| 判据（研发方案 §三 R-01） | 实测结果 |
|---|---|
| 三目录裸 `hash(` 零命中（本线） | ✅ 修后 `news_collector/` 零命中；剩余 5 处均为他线 |
| lint 对故意注入报错退出非 0 | ✅ 探针 `hash((1,2))` → 命中 1 处；`--enforce` **rc=1**；干净文件 **rc=0** |
| 分类准确性（不误伤） | ✅ 同一探针内 `hashlib.*` / 注释 `# hash(3)` / 字符串 `"hash(3)"` / 属性 `d.hash(4)` / `# noqa: unstable-hash` **全部未命中** |
| obs_bridge 跨进程一致 | ✅ 新实现两次独立进程 `878480759d67ef43` == `878480759d67ef43`；旧实现 `<21574954>` ≠ `<75500748>` |
| 普查表覆盖 | ✅ 10 个模块（7 采集 + 1 桥 + 2 portal 快照） |

---

## 六、护栏状态与启用条件

**已就绪**
- `tools/id_stability_lint.py`：可独立跑（`--enforce` 阻断 / 默认 audit / `--all` 全仓 / `--paths` 指定）。
- `tools/git-hooks/pre-commit-id-guard.sh`：只查**暂存 .py**，默认 audit（告警不阻断），`WB_ID_GUARD_ENFORCE=1` 才阻断；python 缺失时 fail-open。
- `tools/git-hooks/install_guard.py`：`BLOCKS` 新增 `idguard`，`--status` 显示 `idguard 块 : 未安装`。

**为什么暂不安装**（两个硬理由）
1. **存量未清**：三目录尚有 5 处他线命中。此时装 audit 会给所有线的每次提交打印告警噪音；装 enforce 会**直接阻断他线提交**（严重越界）。
2. **共享基础设施**：`.git/hooks/pre-commit` 是全仓共享，装块属于对**所有线**的行为变更，按纪律须老田裁定时点。

**启用命令（老田点头后一条命令）**
```
python tools/git-hooks/install_guard.py          # 安装（audit 模式，只告警）
WB_ID_GUARD_ENFORCE=1 git commit ...             # 单次强制拦截
python tools/git-hooks/install_guard.py --uninstall   # 卸载（保留原有内容）
```
**建议启用时序**：先让归属线清掉 §四 的 5 处（或加 `# noqa: unstable-hash` 并说明）→ 再安装 audit → 观察一周无噪音 → 转 enforce。

---

## 七、局限性与待裁定

**局限性（OSINT 自省）**
- 本报告只判「id 生成是否确定性」，**不判断 id 语义是否正确**（如 taoguba 用 `content[:80]` 截断是有意设计，非缺陷，但碰撞概率我不做量化）。
- `phase1_dq.py:49` 的用途为**推断**（未读全上下文），故标 P2 而非 P1；若确认为分片键，应升 P1。
- 全仓 `--all` 的 37 处未逐一定性（超出三目录范围）。
- lint 用 tokenize，**语法错误文件会被跳过**（静默）；当前三目录无此类文件。

**待裁定**
1. `idguard` 钩子块**何时安装**（建议先清 5 处存量）？
2. §四 的 5 处他线命中，**是否由我代改**（默认不代改，等归属线处理）？
3. 全仓 37 处是否纳入下一阶段治理（需先划目录归属）？

---

## 八、2026-11-13 增补：三项裁定落地 + 全量 14 处 + 解释器盲区修正

本节**追加**，不改上文历史结论（上文「5 处 / 37 处」为 2026-11-12 单时点观测，此处为复核后的准确值）。

### 8.1 数字订正（两处）

| 项 | 原记 | 实际 | 原因 |
|---|---|---|---|
| 全仓 `--all` 命中 | 37 处 | **40 处原始 → 14 处待治理** | 原 37 为口径遗漏；40 中 **28 处在 `video/pylibs/*`**（第三方库），应排除 |
| 三目录命中 | 5 处 | **7 处**（三目录口径）/ **14 处**（全仓口径） | 漏检 2 处 f-string 内调用，见 8.2 |

> 上一轮我曾把 `video/pylibs/*` 误读为顶层 `pylibs`，导致"排除第三方"第一版无效。真实路径是
> `video/pylibs/...`（pylibs 在第二级），故排除规则改为「任一路径段命中」。

### 8.2 关键缺陷修正：tokenize 的 f-string 盲区

原判据用 `tokenize`。**Python ≤3.11 把整个 f-string 当作一个 STRING token**，
所以 `f"u{abs(hash(question)) % 35 + 1:04d}"` 里的裸 `hash()` **检不出来**；
3.12+ 会拆开才检出。实测后果：

- 手工用 **3.11** 扫 → 12 处；钩子用 **3.13** 跑 → 14 处 → 同一文件报「新增」，**基线失效**。

**修正**：判据改为 **`ast` 为主**（`ast.Name(id='hash')` 作 `ast.Call` 的 func；
`xx.hash()` / `hashlib.*` 是 `ast.Attribute`，天然排除），`tokenize` 仅作语法错兜底。
修正后 **3.11 与 3.13 结果完全一致（均 14 处）**。

**收益**：新发现 2 处真实漏检 —— `tools/tests/test_kb_qa_bank_v2.py:65`、
`tools/kb_corpus/assertion_conflict_fixture.py:35`（均为 f-string 内生成 id）。

### 8.3 三项裁定的处置

| 裁定项 | 处置 | 依据 |
|---|---|---|
| ① 何时装 idguard | **已装**（2026-11-13）。不等"清完再装"——改用**基线豁免 + 分域 enforce**：`news_collector/**` 硬拦（该目录基线 0 命中，零误伤）、其余目录 audit 只告警 | 时间不是质量判据；分域让「该保护的立刻受保护」，不必等全网清零 |
| ② 5 处是否代改 | **不代改**。已出 `docs/id_stability_cleanup_worklist.md`，逐条给级别+补丁+归属线 | 改法涉业务语义（`rid` 要唯一还是要稳定、`fp` 消费方类型），改错静默改变业务行为；且改后需该线验证 |
| ③ `--all` 是否纳入 | **纳入，但先剥第三方**。28 处 `video/pylibs/*` 属库内部实现，非本仓治理对象 | 治理对象应是「我们生成的 id」，不是第三方库的 hash 实现 |

### 8.4 14 处定级摘要（详见工单）

- **P1 真 bug 6 处**：回测种子（`liudu_trial_run.py:180`）、行情指纹（`sector_state.py:303` +
  `sector_replay.py:102`）、记忆 id（`memory/store.py:136`）、MinHash 签名（`phase1_dq.py:49`）、
  断言 id（`assertion_conflict_fixture.py:35`）
- **P2 5 处**：血缘 rid（`data_lineage.py:104`）+ 测试分桶/期望 id（`tools/tests/*` 4 处）
- **P3 3 处**：沙箱名（已含 `time.time()`）、已撤回补丁内的临时文件、`tmp_path` 隔离的临时目录

**采集主线 `news_collector/` 0 命中** —— 这是本席能立刻转硬拦的前提。

### 8.5 验收证据（2026-11-13 实测）

```
本线 news_collector/ 注入裸 hash        → hook rc=1 阻断 ✓
tools/ 注入裸 hash                      → hook rc=0 告警 ✓
基线内文件（加无关注释后暂存）           → hook 输出 0 行，完全静默 ✓
--all 双解释器一致性                    → 3.11=14 / 3.13=14 ✓
--all 基线生效后                        → 0 新增 / 14 条已知 ✓
```

### 8.6 转全量 enforce 的判据（替代"观察一周"）

按线清零、逐线纳入硬拦：`news_collector/`（**已完成**）→ `quant_backtest/`（3 处 P1，危害最大）
→ `tools/kb_corpus/`（2 处 P1）→ `tools/tests/`（5 处）→ `agent_platform/`（3 处）→
`deliverables/withdrawn-*`（确认废弃则归档）。全部清零 → 基线清空 → 全量 enforce。

---

## 修订记录

- v1（2026-11-12 23:45）：初版。R-01 修复 + 检查器 + 钩子块注册 + 普查表 + 实证。
- v2（2026-11-13 00:xx）：追加 §八。三项裁定处置；全仓命中订正 37→14（排除 `video/pylibs/*` 28 处）；
  判据改 AST 修复 f-string 盲区（3.11/3.13 结果一致，新发现 2 处）；idguard 已装（分域 enforce）；
  存量清理工单见 `docs/id_stability_cleanup_worklist.md`。
