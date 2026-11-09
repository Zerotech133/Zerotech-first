# 缺陷修复 K-ORG-20261107-INBOXLOCK：inbox 锁文件 open 的瞬时拒绝被当成投递失败

| 项 | 值 |
|---|---|
| 编号 | K-ORG-20261107-INBOXLOCK |
| 日期 | 2026-11-07 |
| 状态 | **已修复**（不需裁定：纯代码缺陷，方向明确且修复可验证） |
| 发现方式 | fast 门 `agent_key` 段多出一条失败，逐条归因时挖出 |
| 影响 | `agent_platform/gateway/inbox_gateway.py::submit()` 在 Windows 多进程并发下约 16% 抛异常 |

---

## 一、先纠正我自己的一个判断

上一轮我在门禁归因里写的是"判环境抖动，不是本批"，依据是该文件单独复跑 3/3 绿。**这个依据不成立**：并发缺陷在低并发下本来就不复现。3/3 绿只能说明"单人跑不出来"，不能说明"没问题"。

`test_submit_concurrent_windows_spawn_processes` 不在 `test_baseline.json` 的已知 flaky 名单里，说明历史上它没被当成抖动处理过。我差点用"登记 flaky"把这个信号盖掉——这一步会同时掩盖生产缺陷，所以先做了复现再决定。

## 二、定性与复现

写 `tools/research/repro_inbox_lock_race.py`，按用例同样形态（4 个 spawn 子进程、同 store、同幂等键）循环：

| 阶段 | 结果 |
|---|---|
| 修复前 25 轮 | **4 轮失败（16%）**，全部 `PermissionError@lock` |
| 修复后 40 轮 | **0 轮失败** |

失败栈：

```
inbox_gateway.py:110  with path.open("a+b") as lock:
PermissionError: [Errno 13] Permission denied: ...\.inbox_gateway.lock
```

## 三、根因：同一个函数里两套等待语义

```python
with path.open("a+b") as lock:      # ← 失败立即抛，无重试
    if os.name == "nt":
        while True:                  # ← 加锁失败一直重试
            try:
                msvcrt.locking(...); break
            except OSError as exc:
                if exc.errno not in (EACCES, EAGAIN, EDEADLK): raise
                time.sleep(0.01)
```

`msvcrt.locking` 那层已经明确认为 `EACCES` 是"等一下就好"，但**打开锁文件本身**遇到同一个 `EACCES` 就直接上抛。成因是 Windows 上并发创建/打开同一文件的瞬时共享模式竞争（安全软件持有扫描句柄是常见来源），属同一类瞬时态。

后果不是测试红，而是：异常穿过 `submit()` → 调用方看到"投递失败"。**生产侧等于丢消息**，而调用方无法区分"锁瞬时占用"和"真的写不进去"。

## 四、修复

```python
_LOCK_OPEN_DEADLINE_S = 5.0
_LOCK_OPEN_BACKOFF_S = 0.01

def _open_lock_file(path: Path):
    deadline = time.monotonic() + _LOCK_OPEN_DEADLINE_S
    while True:
        try:
            return path.open("a+b")
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(_LOCK_OPEN_BACKOFF_S)
```

`_store_lock` 改为 `with _open_lock_file(path) as lock:`。

**上限 5s 后仍失败则原样抛出**——持续拒绝是真故障（权限、磁盘、路径），不能无限等成 hang。这条比"加重试"本身更重要：只加重试会把真故障变成静默卡死。

## 五、回归测试（2 条，锁住两条语义）

| 用例 | 断言 |
|---|---|
| `test_store_lock_retries_transient_open_permission_error` | 前 2 次 open 报 Errno 13 → 第 3 次成功，`submit` 返回 ok，且 open 调用数 ≥3 |
| `test_store_lock_open_gives_up_after_deadline` | 持续拒绝 + deadline 0.05s → 抛 `PermissionError`（不 hang） |

## 六、验证

| 项 | 结果 |
|---|---|
| 目标文件 | **27 passed**（原 25 + 新 2） |
| 全部涉及 `inbox_gateway` 的测试（7 文件） | **147 passed**，15.08s |
| 竞争复现（修复后 40 轮） | **0 失败** |
| "测试有牙"检查 `verify_inboxlock_tests_have_teeth.py` | **8/8**：旧形态 n=1 即抛、新形态 n=3 成功、持续失败 n=6 后抛（0.05s/10ms 合理）、无失败路径不变（n=1）、同键二次投递 `replayed=True` 且 id 相同 |
| 行尾 / BOM | 两文件均全 CRLF、裸 LF=0、无 BOM（与原状一致） |

`test_inbox_gateway_lifecycle.py` 本就登记在 `agent_key` 冻结清单内，无需改门禁配置。

## 七、同构面（未修，建议单开工单）

全仓 `msvcrt.locking|fcntl.flock` 共 18 个文件命中，抽 5 处看 open 侧：

| 文件 | open 是否重试 | 备注 |
|---|---|---|
| `portal/os_goal_store.py` | **否**（`os.open(O_CREAT\|O_RDWR)`） | 加锁侧有 10s deadline，open 侧同缺口；单写场景并发低 |
| `quant_backtest/xq_request_gate.py` | 待查 | `os.lseek` + NBLCK，看得到 deadline 逻辑 |
| `quant_backtest/warehouse_commit.py` | 待查 | Windows 走 LockFileEx，`os.open` 无重试 |
| `portal/api_portfolio.py` | 待查 | 只用 `_os_lock(fd)`，open 在调用方 |
| `tools/devtask.py` | 有（`_tasks_file_lock` 外层重试） | 已具备 |

结论：这是**一类**问题而非一个点。建议按"锁 open 必须与锁等待同语义"做一次专项审计，不并进本次提交。
