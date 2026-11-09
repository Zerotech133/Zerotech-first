# RFC 20261106 · JEV 回填与 M3 的锁互斥死锁

- 状态：**已实施（2026-11-06 晚，经批准）**——`jev_backfill.py` 落地 `_backlog_left()` +
  `_decide_skip()`，main() 仅在「确认 M3 持锁」分支内探测积压（保住 OS-FIX-JEVLOCK 契约）；
  单测 `test_jev_backfill_contend.py` 11 项 + 既有回归 15 项全过。
  实施前手工预演（17:14 竞争模式 round 1）已验证：能拿锁、能判（25min/120 条）、无锁协议破坏。
  回滚开关不变：`JEV_M3_CONTEND_MIN_BACKLOG=999999` 即恢复旧行为。
- 提出：2026-11-06 17:10
- 关联：K266-PROBE-YIELD（闸门3）、M3SKIP（2026-10-30）、OS-FIX-JEVLOCK（2026-10-26）
- 影响组件：`news_collector/jev_backfill.py`、`jev-eval/jev_backfill_task.ps1`

## 1. 现象

2026-11-06 当日 JEV 判定积压从 146 涨到 **716**（16:50 实测），并触发 `alerted=1`。
逐轮日志（`jev-eval/review_data/jev_backfill_task.log`）：

| 时刻 | 结果 |
|---|---|
| 08:00 / 11:00 / 12:00 / 13:00 / 17:00 | `BACKFILL skip: m3_active holder=k266_m3 alive=True`（整轮零判读） |
| 09:00 / 10:00 | `P0 night window: skip hourly`（夜窗，设计内） |
| 16:45 | `judged=56/393 left=716`，收工原因 `ollama_lock: tick 发现锁所有权丢失（holder=k266_m3）→ 未抢回，本轮收工` |

即：白天可用的整点窗口，几乎全部被 M3 占掉；唯一挤进去的一轮也在半途丢锁。

## 2. 现有机制（三处，各自都对，拼起来死锁）

1. **M3 独占锁**：`k266_services.ps1` 每 5 分钟巡检，`doc_ingest` 有新文档就异步拉起
   `k266_m3_structure.py`，进程一启动即持有 `locks/ollama_batch.lock`（purpose=k266_m3）。
2. **回填主动让位**：`jev_backfill.py:288` 的 `_m3_active_skip()` 检测到锁 holder 是
   k266/m3 且进程存活，**整轮直接 return**（注释：M3SKIP 2026-10-30，避免"抢到再丢/只判 8 条"）。
3. **闸门3 让位判据靠延迟**：`k266_services.ps1:113-118` 设 `K266_PROBE_YIELD=1`，
   M3 探测到 JEV judge/人类交互延迟 > 6s 才让位（交还锁、睡 60s 重探）。

**实测补充（2026-11-06 17:12:57）**：M3 持锁期间直接观察到 `locks/ollama_batch.lock`
短时消失、17:13:23 以 82 字节重建 —— 即 `LEASE_S=300` 的**租约让位窗口真实存在**（YIELD_S=20s）。
而这个窗口当前**没有任何消费者**：回填在 `_m3_active_skip` 分支就 return 了，从不进入
`acquire()`。方案 B 的本质，就是把"积压高时的回填"放回这个已经空转了几百个周期的窗口里去抢。

## 3. 根因（死锁环）

```
M3 持锁 ──> jev_backfill 见 m3_active 整轮跳过（不发任何请求）
                        │
                        ▼
        Ollama 队列里只有 M3 自己 ──> 探测延迟正常 ──> 闸门3 永不触发让位
                        │
                        ▼
                    M3 一直持锁（回到起点）
```

**闸门3 的触发条件依赖 judge 发请求，而 M3SKIP 保证 judge 不发请求。** 两个 2026-10 的
保护性改动各自正确，叠加后互为前提，形成闭环。8 小时内 5 个整点轮全部空转即此。

附带缺陷：`_m3_active_skip` 只要看到 M3 存活就放弃整轮，没有"积压已经很高，应该争一下"的出口
——积压越高，让位越坚决，与实际诉求相反。

## 4. 方案对比

### 方案 A（最小改动，改 ps1 策略）
在 `jev_backfill_task.ps1` 里按积压决定是否设 `JEV_SKIP_IF_M3=0`（`_m3_active_skip` 已有该后门）：

```powershell
# 先查一次积压，>150 时参与锁竞争，靠 M3 租约让位拿窗口
$left = & $py -X utf8 -B (Join-Path $dir 'jev_backfill.py') --probe-left 2>$null
if ([int]$left -gt 150) { $env:JEV_SKIP_IF_M3 = '0' }
```
- 优点：不动 Python 逻辑，env 可一键回退。
- 缺点：需要新增 `--probe-left` 轻量出口（或直接用 sqlite 查）；决策分散在 ps1。

### 方案 B（推荐，改 1 个函数）
`_m3_active_skip()` 增加积压出口：积压 ≥ 阈值时返回 False（参与竞争），阈值可 env 覆盖。

```python
M3_CONTEND_MIN_BACKLOG = int(os.environ.get("JEV_M3_CONTEND_MIN_BACKLOG") or 150)

def _m3_active_skip(lock_mod) -> bool:
    """M3 持有且存活则 True；积压高于阈值时改为参与竞争（抢 M3 租约让位窗口）。"""
    if os.environ.get("JEV_SKIP_IF_M3", "1").strip().lower() in ("0","false","no","off"):
        return False
    try:
        info = lock_mod.holder_info()
    except Exception:
        return False
    if not info.get("alive"):
        return False
    purpose = str(info.get("purpose") or "").lower()
    if not ("k266" in purpose or "m3" in purpose):
        return False
    # 新增：积压高时不再无条件让位
    # _backlog_left() 为本次新增小助手：复用 L314 那条
    # SELECT COUNT(*) FROM news WHERE jev IS NULL OR jev='' ，sqlite3 只读打开、timeout=3s
    try:
        left = _backlog_left()
    except Exception:
        return True            # 查不到积压就退回保守行为
    if left >= M3_CONTEND_MIN_BACKLOG:
        print("BACKFILL contend: backlog=%d >= %d, 参与锁竞争（M3 租约让位窗口）"
              % (left, M3_CONTEND_MIN_BACKLOG), flush=True)
        return False
    return True
```
配套：`acquire(wait_s=900)` 在"竞争模式"下降为 `JEV_CONTEND_WAIT_S`（默认 600），避免整轮空耗 15 分钟。
- 优点：决策集中在业务代码、带日志、env 可回退、白天/夜间语义一致。
- 缺点：需要一次轻量 COUNT 查询（<5ms）。

### 方案 C（文件信号量）
judge 写 `C:\Temp\k266_yield_request`，M3 每批判完检查该标志则让位。
- 优点：跨进程解耦彻底，不依赖锁租约。
- 缺点：要改 M3 主循环（生产批处理），影响面最大；标志文件的清理/陈旧需额外约定。

**推荐 B**：改动最小、可回退、不触碰 M3 主循环，且逻辑上用"积压"这个真实诉求替代了"看到 M3 就让"的机械判断。

## 5. 影响面与风险

| 项 | 评估 |
|---|---|
| M3 吞吐 | 竞争期 Ollama 单槽仍串行，M3 批次略慢；不改变 M3 判定质量与产物 |
| judge 质量 | 无变化（同一模型、同一提示词、同一 timeout 180s） |
| 最坏情形 | 积压高但 M3 长达 10 分钟不让位 → 回填 `acquire` 等 600s 后正常退出，等价现状，无副作用 |
| 告警噪声 | 竞争模式会多打一行 `BACKFILL contend`，不新增告警 |

## 6. 回滚

`JEV_M3_CONTEND_MIN_BACKLOG=999999`（或 `JEV_SKIP_IF_M3=1` 保持原值）即恢复"看到 M3 就整轮让位"。
ps1 层不需要回滚（未改 ps1 时）。

## 7. 验收

1. 单测：mock `holder_info()` 返回 k266+m3、`_backlog_left()` 分别返回 50 / 200，
   断言 `_m3_active_skip` 为 True / False；`JEV_SKIP_IF_M3=0` 恒 False。
2. 实战：M3 持锁期间手动跑一轮，日志应出现 `BACKFILL contend: backlog=...`，
   并在 M3 一个 LEASE_S(300s) 周期内出现 `judged>0`。
3. 回归：积压 <150 时行为与现状完全一致（日志仍为 `BACKFILL skip: m3_active`）。
4. 不影响夜窗：`k266_m3.pause` 存在时 ps1 仍先 exit 0，不进竞争分支。

## 8. 待批决策点

- [ ] 是否采用方案 B（推荐）
- [ ] 阈值默认值 150 是否合适（当前积压 716，告警线 100）
- [ ] 竞争模式 `acquire` 等待上限 600s 是否接受（占满则整轮不判）

批准后实施：改 `jev_backfill.py` 1 个函数 + 新增 1 个常量 + 2 个单测，跑
`pytest test_jev_backfill_lock_order.py` 与 `test_jev_p1_fixes.py` 回归。
