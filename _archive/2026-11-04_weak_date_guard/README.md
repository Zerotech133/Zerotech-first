# 2026-11-04 weak 产线「日期契约」改动归档

编号：`WEAK-TRACK-DATE-GUARD-20261104`（首版）、`WEAK-TRADING-DAY-GUARD-20261104`（休市日契约补充）

本目录存放该次改动的改前版本，供回滚使用。**不要随工作区清理删除。**

> 注意：4 个 `.bak-20261104-2227` 命中 `.gitignore` 的 `*.bak-*`（第 111 行），
> **只存在于本机、不入库**。干净 clone 拿不到备份文件，回滚走 §10-28 基线仍在 git
> 一节（`git show HEAD:<file>`）或本 README 的改动清单手工反补。

## 改了什么

| 文件 | 改动 |
|---|---|
| `quant_backtest/weak_track_summary.py` | 新增 `valid_day(s)`：YYYY-MM-DD 且真实存在、年份 ∈ [2015, 今天]；带时间 ts 截前 10 位；缺失判 False |
| `quant_backtest/weak_track_check.py` | 新增 `_last_px_d()` / `_guard_px_d()`：非法 px_d 拒写并回退上一条合法交易日，缺失静默沿用，合法但倒退只告警；被拦原值记入 `state["px_d_bad"]`；落盘 `fp` 提前到构造 state 之前 |
| `quant_backtest/weak_track_audit.py` | 第 6 项体检 `_bad_date_stats()`：`日期坏点:n行(样本)`（缺失不算坏）+ `日期倒退:n处(prev→cur)`；`_FIX` 与全绿文案同步「六项」 |
| `quant_backtest/weak_track_close.py` | `close_fields` 的 `nd` 改为 `len(_dset - {"", "?"})`——缺失值不计交易日（301216 实测 8 → 7 日） |
| `quant_backtest/test_weak_date_guard.py` | 新增套件，G1-G14：边界判定 + 闸门行为 + 日数口径 |
| `quant_backtest/test_weak_audit.py` | 扩 T10 坏点 / T11 无误报 / T12 倒退；计数改 7 例 / 6 异常 |

## 回滚

本目录 4 个 `.bak-20261104-2227` 是**改前**内容（除 close.py）。回滚：

```bash
cd "C:/项目/老田的AI助手"
cp _archive/2026-11-04_weak_date_guard/weak_track_summary.py.bak-20261104-2227 quant_backtest/weak_track_summary.py
cp _archive/2026-11-04_weak_date_guard/weak_track_check.py.bak-20261104-2227   quant_backtest/weak_track_check.py
cp _archive/2026-11-04_weak_date_guard/weak_track_audit.py.bak-20261104-2227   quant_backtest/weak_track_audit.py
cp _archive/2026-11-04_weak_date_guard/test_weak_audit.py.bak-20261104-2227    quant_backtest/test_weak_audit.py
rm quant_backtest/test_weak_date_guard.py     # 新增套件，回滚时一并移除
```

`weak_track_close.py` **本目录无备份**（其 21:40 备份随 2026-11-04 22:55 回收站清空永久删除）。回滚它的 `nd` 改动只有 3 行，用 git 反向应用即可：

```bash
cd "C:/项目/老田的AI助手"
git diff HEAD -- quant_backtest/weak_track_close.py   # 查看全部改动（含 _close_ts + nd）
git checkout HEAD -- quant_backtest/weak_track_close.py   # 回到 10-28 提交版（会一并丢弃 _close_ts 改动）
```

## 10-28 基线仍在 git

`weak_track_archive/card/learn` 三个文件的 10-28 版本已由提交 `b7ac2192b`（WO-GUANYU-B17-20261028）完整保存，回收站清空**未造成回滚能力损失**：

```bash
git show HEAD:quant_backtest/weak_track_card.py > /tmp/card_1028.py
```

## 验证

```bash
cd "C:/项目/老田的AI助手/quant_backtest"
PY=/c/Users/t/.workbuddy/binaries/python/envs/default/Scripts/python.exe
for t in test_weak_date_guard test_weak_audit test_weak_close_archive test_weak_archive test_weak_learn test_weak_summary; do
  echo -n "$t -> "; "$PY" -X utf8 "$t.py" 2>&1 | grep -E "ALL PASS|FAILED" | tail -1
done
"$PY" -X utf8 weak_track_audit.py      # 真实数据预览（应报 B 类新案缺 + 日期倒退 1 处）
```

## 口径备忘

- 缺失值（`""`/`"?"`）**不算**坏日期，也不算交易日。
- 合法但小于上一条合法 px_d = 倒退：照写、只告警，交 §13 审计跟踪。
- 301216 归档流实测：坏点 0、倒退 1（`2026-10-02 → 2026-09-30`）、缺失 1（`"?"`）。

## 补充（23:15）：休市日契约 —— 上面那条"倒退"其实判错了

用上交所《2026 年部分节假日休市安排》（上证公告〔2025〕45 号）复核后确认：
**2026-10-01~10-07 国庆休市，10-02 根本没有开市**。所以"倒退"的两端里，
错的是前一日 `2026-10-02`，对的是 `2026-09-30`（节前真实最后交易日）——
旧口径把正确值一起算进了异常。

改动三处：
- 新增 `quant_backtest/cn_trading_days.py`：本地休市日历（只登记已核实年份，
  未覆盖年份退回「周一至周五」不误杀），`why_not()` 区分周末/休市。
- `weak_track_check._guard_px_d`：px_d 落在周末/休市 → 同坏值一样**拒写**，
  回退上一条"合法交易日"（`_last_px_d` 也加了交易日过滤）。
- `weak_track_audit`：新增第 7 项「非交易日」，第 8 项「日期倒退」改为
  **只在两侧都是交易日时才判**（休市前后不连续是正常的）。

数据侧（已执行，可回滚）：
- `quant_backtest/weak_track_repair_px_d.py`（新工具，默认 dry-run，
  `--apply` 才写）把 301216 归档流里 6 行 `px_d=2026-10-02` 改成 `2026-09-30`，
  原值写进 `px_d_fix` 留痕，其它字段不动。
- 备份：`reports/weak_track_301216.closed_20261020.jsonl.bak-20261104-2315`
  （4,399 B，改前）。回滚就是把它拷回去。
- 注意：`weak_track_close.nd` 现在只数真实交易日，故 301216 重算会由 7 日变 6 日；
  已落盘的结案 md 保持 7 日不回溯（与 22:34「历史 md 不回溯」口径一致）。
- ~~顺带生成 B 类新案模板：`reports/weak_cases/weak_review_301216_20261104.md`~~
  该模板与结案结论（「不立新案」）冲突，已移至
  `_archive/2026-11-04_301216_newcase_superseded/`，见下节。

## 补充（23:25）：B 类新案行三态 —— 「未立」是合法终态

301216 结案结论原文是「按破位归档定性，**不立新案**」，但 §13 审计的
第 5 项写死「B 类结案必须有 `🔄 新案已建` 行」，于是持续报 `B 类新案缺`——
**这是审计规则与业务结论冲突，属误报**，不是数据缺口。

处理（没有为了变绿去补假行）：

1. 结案报告第 18 行补真实记录：
   `🔄 新案未立：结案结论「按破位归档定性，不立新案」（三线共振仅部分成立，不满足完整 B 判据共振要件）`
2. `weak_track_audit` 增加前缀豁免：`🔄 新案未立` 视为合法终态，
   不再报 `B 类新案缺`；但**必须由人写进报告**，机器不从结案结论自动推断
   （否则「漏建新案」会被静默当成「不立新案」）。
3. 豁免**不覆盖**另外两点，保持严格：
   - `🔄 新案生成失败` 仍报 `B 类新案缺`（T15 用例守住）；
   - 声明未立但任务仍 Ready，仍报 `任务未停`（不因豁免放行）。
4. `_FIX["B 类新案缺"]` 文案补「结案已定性不立新案的，补『🔄 新案未立：<依据>』行」。

测试：`test_weak_audit` 新增 T14（声明未立 → 全绿）/ T15（生成失败 + 任务 Ready
→ 双 issue），计数改 9 例 / 7 异常；T9 补第三种合法输出「审计全绿」
（原断言只认空态与异常明细，真实数据全绿时漏判）。

**没有走 `--send` 重推**，原因见下节。

## 为什么不 `weak_track_close --newcase-name ... --send` 重推

| 阻塞点 | 事实 |
|---|---|
| 默认流已不存在 | `reports/weak_track_301216.jsonl` 已于 10-20 归档为 `.closed_20261020.jsonl`，不带 `--stream` 直接报「状态流不存在」rc=1 |
| 带 `--stream` 会二次归档 | 第 389 行 `os.rename(fp, REP/arch)`，arch 用当天日期 → 产出 `...closed_20261020.closed_20261104.jsonl`，破坏归档唯一性 |
| 会重复推送 | `--send` 必发飞书卡片，业务上等于再结一次案 |
| 结论会被重写 | `note` 是必填参数，原结论未留存为变量，重推必然重写结论，可能与 10-20 定稿不一致 |
| 结案时间会变 | `_close_ts` 取状态流末条 ts，但报告会另存为 `weak_close_301216_20261104.md`，形成第三份结案报告 |

结论：重推是「再造一次结案」，不是「补一行」。

## 补充（23:40）：休市日历的年份盲区

`cn_trading_days.HOLIDAYS` 只登记了已核实的 **2026**。实测 2027 关键日全部被判为
交易日（2027 元旦是周五 → 误判开市），这类错误不报错、只静默把节假日算成交易日。
距跨年不足两个月，故补三处：

1. **固定休市日 `FIXED = ((1, 1),)`**：元旦任意年份都休市，依据《全国年节及纪念日
   放假办法》法定假日 + A 股历年惯例，不需等年度公告。春节（农历）、清明（节气）、
   端午、中秋、国庆（含调休）**不猜**，仍等公告。
2. **`confidence(d)` → exact / weekday_only / invalid**：未登记年份只能按周末判断，
   必须诚实标注，而不是假装可信；`needs_update(today)` 在 11 月后提醒补登次年。
3. **审计第 9 项「日历未覆盖」**：px_d 落在未登记年份的工作日时单独报出。
   注意与 `valid_day` 的交互——`valid_day` 拒绝未来年份，所以 2027 的数据**今天
   造不出来**（会被判坏点），该项的真正触发场景是「2027 年日历还没补登」时的实盘数据。
   测试 T16 因此改用临时清空 `HOLIDAYS` 模拟未登记状态（try/finally 恢复）。

写入端 `_guard_px_d` 对 `weekday_only` 的日期**不阻断**（拒写会停掉整条跟踪），
只打一行 warn 说明「当日是否开市无法确认」。

## 补登 2027 的操作（等上交所年度公告）

```python
# quant_backtest/cn_trading_days.py
HOLIDAYS = {
    2026: (...),
    2027: (
        # 依上证公告〔2026〕xx 号填写，格式 (起月, 起日, 止月, 止日) 闭区间
    ),
}
```

补登后 `needs_update()` 自动不再提醒，H8/H10 与 T16 的自适应断言自动跳过，
不需要改测试。补记是唯一不产生副作用的做法。

