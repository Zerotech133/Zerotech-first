# id 稳定性存量清理工单（14 处）

> 生成：2026-11-13 · 编制：OSINT1（马良）· 关联：`docs/id_stability_audit.md`、`tools/id_stability_baseline.json`
>
> **本工单只定级与给补丁，不代改他线代码。** 原因见文末「为什么不代改」。

## 0. 一句话结论

全仓 `--all` 命中 **14 处**（已排除第三方库 `video/pylibs/*` 28 处噪音）。其中
**6 处是真 bug（会实际导致 id 漂移 / 回测不可复现 / 去重失效）**，5 处语义可疑，3 处无害。
**采集主线 `news_collector/` 为 0 命中**，故已对该目录单独硬拦（enforce）。

## 1. 统计

| 级别 | 含义 | 处数 |
|---|---|---|
| **P1** | 真 bug：id/指纹/种子跨进程漂移，破坏去重、缓存、可复现性 | **6** |
| **P2** | 语义可疑 / 测试不稳定：结果随进程变，但不一定立刻出事 | **5** |
| **P3** | 无害：唯一性已由其他因素保证，或仅测试临时路径 | **3** |

按归属线：`agent_platform` 3 · `quant_backtest` 3 · `tools/kb_corpus` 2 · `tools/tests` 5 · `deliverables`(已撤回) 1

## 2. P1 真 bug（6 处，建议优先）

### P1-1 `quant_backtest/tools/liudu_trial_run.py:180` —— 回测不可复现（最严重）
```python
path = _synth_price_path(seed=hash(r["symbol"]) % (10**8))
```
**问题**：合成价格路径的随机种子取自内置 `hash()`。同一 symbol 每次运行种子都不同 →
**同一回测两次跑出不同曲线**，历史结论无法复算。可复现性是回测的底线。
**补丁**：
```python
_seed = int(hashlib.sha256(str(r["symbol"]).encode("utf-8")).hexdigest()[:16], 16)
path = _synth_price_path(seed=_seed % (10**8))
```

### P1-2 / P1-3 `quant_backtest/sector_state.py:303` 与 `sector_replay.py:102` —— 行情指纹永远"变了"
```python
fp = hash(tuple(np.round(g["close"].to_numpy(float)[-60:], 3)))
```
**问题**：这是行情数据指纹（判断是否需要重算/缓存命中）。内置 `hash` 跨进程不稳定 →
每次进程启动指纹都不同 → **缓存恒失效**（或恒判"数据已变"）。两处代码完全相同，是复制粘贴双份。
**补丁**（`np.round(...)` 结果转 bytes 再摘要，确定性）：
```python
_arr = np.ascontiguousarray(np.round(g["close"].to_numpy(float)[-60:], 3))
fp = hashlib.blake2b(_arr.tobytes(), digest_size=8).hexdigest()
```
> 注意：若下游把 `fp` 当 **int** 用（如 `% N`），改 `int(..., 16)`；若当字符串键用，保持 hex 字符串即可。
> **改前需该线确认 fp 的消费方**，这是我不代改的主因之一。

### P1-4 `agent_platform/memory/store.py:136` —— 记忆条目 id 漂移
```python
mid = str(mid or ("%s:%s" % (layer, abs(hash(cue + content)) % 10**10)))
```
**问题**：未显式传 `mid` 时用 `hash(cue+content)` 生成 → 同一内容重启后 id 不同 →
**记忆去重失效、重复累积**（与判例 R22 sse 完全同病）。
**补丁**：
```python
_dig = hashlib.sha256(("%s|%s" % (cue, content)).encode("utf-8")).hexdigest()[:16]
mid = str(mid or ("%s:%s" % (layer, _dig)))
```
> 注：若历史 `mid` 已落库，改造后新旧 id 会不共存 → 需该线决定是否做一次性迁移（同 R22 的
> `normalize_sse_ids.py` 范式）。**迁移策略属业务裁定，不代做。**

### P1-5 `tools/kb_corpus/phase1_dq.py:49` —— MinHash 签名不可比
```python
m = min(hash((s, seed)) & 0xFFFFFFFF for s in sh)
```
**问题**：MinHash 的前提是"同一 shingle 在任何进程/任何批次都映射到同一个值"，否则
**昨日算的签名与今日算的签名不可比 → 近重复文档检测完全失效**。`hash` 加盐直接击穿该前提。
**补丁**（保证 32 位无符号，保持 `& 0xFFFFFFFF` 语义）：
```python
def _h32(s, seed):
    return int.from_bytes(
        hashlib.blake2b(("%s\x00%d" % (s, seed)).encode("utf-8"),
                        digest_size=4).digest(), "big")
...
m = min(_h32(s, seed) for s in sh)
```
> 注意：改后签名值与历史签名不兼容 → 需**全量重算一次**（一次性，同 R22 迁移范式）。

### P1-6 `tools/kb_corpus/assertion_conflict_fixture.py:35` —— 断言 id 漂移
```python
"assertion_id": f"a{abs(hash(action))%9999}",
```
**问题**：断言 id 本应是稳定标识（用于冲突追踪/去重）。跨进程漂移 → 同一断言被当成新断言。
**补丁**：
```python
_aid = int(hashlib.sha256(str(action).encode("utf-8")).hexdigest()[:8], 16) % 9999
"assertion_id": f"a{_aid}",
```
> 该文件位于 `tools/kb_corpus/`，从命名看是 fixture 生成器；改后需重生成一次 fixture。

## 3. P2 语义可疑 / 测试不稳定（5 处）

| # | 位置 | 原行 | 问题 | 建议 |
|---|---|---|---|---|
| P2-1 | `agent_platform/tools/data_lineage.py:104` | `rid = run_id or (ts + "-" + str(abs(hash(job)) % 100000))` | **双向可疑**：同 job 不同进程 → 不同 rid（血缘分裂）；同 job 多次运行 → 同 rid（本应唯一标识每次运行） | 先定语义：若要"每次运行唯一"→ 用 `uuid4()`；若要"同 job 稳定"→ `sha256(job)[:8]`。**属血缘模型裁定，不代改** |
| P2-2 | `tools/tests/test_kb_qa_bank_v2.py:63` | `h = hash(question) % 4` | 测试分桶随进程变 → 同一问题每次落到不同分支，覆盖不稳定 | `int(hashlib.sha256(q.encode()).hexdigest()[:8], 16) % 4` |
| P2-3 | `tools/tests/test_kb_qa_bank_v2.py:65` | `f"u{abs(hash(question)) % 35 + 1:04d}"` | **f-string 内**（3.11 tokenize 原本漏检），期望单元 id 随进程变 → 断言不可复现 | 同上摘要后取模 |
| P2-4 | `tools/tests/test_kb_qa_bank_v2.py:71` | `mode = hash(question) % 3` | 同上 | 同上 |
| P2-5 | `tools/tests/test_kb_qa_bank_v3.py:68` | `mode = hash(question) % 4` | 同上 | 同上 |

> 测试类命中虽不发生在生产，但会让**测试结果不可复现**（本地过、CI 挂），值得一次清干净。

## 4. P3 无害（3 处，建议加注释而非改代码）

| # | 位置 | 原行 | 为何无害 | 建议 |
|---|---|---|---|---|
| P3-1 | `agent_platform/tools/agent_sandbox.py:81` | `"sbx_" + str(abs(hash(cmd + str(time.time()))) % 10**8)` | 已含 `time.time()`，唯一性由时间保证；hash 只做混淆 | 行尾加 `# noqa: unstable-hash`（说明：唯一性由 time.time 保证） |
| P3-2 | `deliverables/withdrawn-warn-patch-20261020/test_devtask_owner_domain_status.py:85` | `abs(hash(repr(status)))` | 测试临时 JSON 文件名，`tmp_root` 已隔离；且该目录是**已撤回补丁** | 同上，或整目录归档移除 |
| P3-3 | `tools/tests/test_r1_daily_check.py:113` | `tmp_path / ("r1_%d" % (abs(hash(...)) % 1000000))` | `tmp_path` 天然隔离，名字只用于区分 | 同上 |

> P3 用 `# noqa: unstable-hash` 而非改代码，是因为**改代码会引入无收益的风险**；但必须带理由，
> 否则下一个人无法判断是有意豁免还是漏改。

## 5. 守卫当前策略（已生效）

`tools/git-hooks/pre-commit-id-guard.sh`（已安装，`install_guard.py` 生成，勿手改）：

| 暂存路径 | 模式 | 行为 |
|---|---|---|
| `news_collector/**` | **enforce** | 新增裸 hash → **阻断提交**（本线基线 0 命中，零误伤） |
| 其余目录 | audit | 新增裸 hash → 打印告警 + 留痕，不阻断 |
| 基线内 14 条 | 豁免 | 不告警（`tools/id_stability_baseline.json`，按「文件+源码行指纹」匹配） |
| `WB_ID_GUARD_ENFORCE=1` | 全量 enforce | 基线内仍豁免 |

验证（2026-11-13 实测）：
- 本线注入 → `rc=1` 阻断 ✓
- `tools/` 注入 → `rc=0` 告警 ✓
- 基线内文件（加无关注释后提交）→ **完全静默** ✓
- 同一文件在 Python 3.11 与 3.13 下命中数一致（14/14）✓

### 解释器盲区已修（重要）
原实现用 `tokenize`，**Python ≤3.11 把 f-string 整体当字符串**，`f"{hash(x)}"` 检不出——
这条盲区曾导致「手工用 3.11 扫出 12 处、钩子用 3.13 扫出 14 处」的不一致。
现已改为 **`ast` 为主判据**（跨版本一致）、`tokenize` 仅作语法错兜底。
本次因此**新发现 2 处**（P2-3、P1-6）。

## 6. 转全量 enforce 的判据（建议）

不建议用"观察一周"这类时间判据（时间不是质量指标）。建议**按线清零、逐线纳入硬拦**：

1. **已完成**：`news_collector/` 清零 → 已 enforce。
2. 下一批建议：`quant_backtest/`（3 处 P1，危害最大）清零 → 钩子 OWN 白名单加该目录。
3. 依次：`tools/kb_corpus/`（2 处 P1）→ `tools/tests/`（5 处）→ `agent_platform/`（3 处）。
4. `deliverables/withdrawn-*` 若确认废弃 → 直接从基线移除并归档，无需改代码。
5. 全部清零后 → 基线清空 → 全量 enforce（此时基线为空，任何裸 hash 都被拦）。

> 钩子 OWN 白名单目前硬编码为 `news_collector/*`，扩容时改 `pre-commit-id-guard.sh` 的
> `case` 分支即可（改源文件后重跑 `install_guard.py`，勿直接改 `.git/hooks/pre-commit`）。

## 7. 为什么不代改（边界说明）

1. **语义需业务裁定**：`data_lineage.rid` 到底要"唯一"还是"稳定"、`sector_state.fp` 的消费方是
   字符串键还是整数——这些不是代码风格问题，改错会静默改变业务行为。
2. **改后需该线验证**：指纹改了缓存会全量失效一次；MinHash 改了要全量重算；记忆 id 改了要考虑
   历史数据迁移。**我改完无法替他线验证，只会把风险转移到看起来"已经修好"的地方。**
3. **本席职责是治理不是代工**：我的输出止于「定位 + 定级 + 判据 + 工单」。代码归属线有权决定
   改法与时点，这也是责任可追溯的前提。

需要我做的只有两件事（都不越界）：**（a）某线清零后把它纳入硬拦白名单**；
**（b）提供一次性迁移脚本骨架**（如 `normalize_*_ids.py` 的只读 dry-run 版本，由该线确认后执行）。

## 8. 附：复现命令

```bash
PY="/c/Program Files/Python311/python.exe"

# 全仓普查（已排除第三方 video/pylibs/*）
"$PY" tools/id_stability_lint.py --all

# 只看新增（基线外）
"$PY" tools/id_stability_lint.py --all --no-baseline

# 列出基线内已知
"$PY" tools/id_stability_lint.py --all --show-known | grep '^\[known\]'

# 收缩基线（某线清零后执行，让已修项退出豁免）
"$PY" tools/id_stability_lint.py --all --write-baseline

# 守卫状态
"$PY" tools/git-hooks/install_guard.py --status
```
