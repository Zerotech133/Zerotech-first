# 2026-11-04 weak 产线「日期契约」改动归档

编号：`WEAK-TRACK-DATE-GUARD-20261104`

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
