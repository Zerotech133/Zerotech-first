# L2 记录：GlobalFutSync 总超时（2026-11-02 23:35）

> 背景：18:15 运行 21 分钟未完成（正常 160s），手动停止；已修 manifest 原子写/失败不覆盖，本次补总超时。

## 变更（`quant_backtest/global_fut_sync.py`）

1. `fetch()` / `fetch_history()` 新增可选 `deadline`：重试前/逐年窗前检查，超预算即返回已得数据（partial），不再无限重试；
2. main 新增 `--max-seconds`（默认 **600s**），计算 `_deadline` 并传入；
3. 与已修复项配合：manifest 原子写 + 失败不覆盖成功记录（`_fail_entry`）。

## 验证

| 项 | 结果 |
|---|---|
| py_compile | ✅ |
| `--dryrun --max-seconds 30` | ✅ rc=0（4 品种计划打印正常） |
| 语法门 | ✅ PASS（4148 文件/0 错 0 警） |
| 真实网络压测 | 未做（避免夜间限流）；明晚 18:15 计划任务自然验证 |

## 影响/回滚

- 正常情况路径不变（预算内完成）；超预算时输出 partial 并保留已成功品种；
- 回滚：`git checkout -- quant_backtest/global_fut_sync.py`（回到无总超时版本，不推荐）。

## 待观察

- 明晚 18:15 首跑 LastResult / 时长 / manifest；若仍 >10min，考虑单品种独立任务或换数据源（Sina/东财）。
