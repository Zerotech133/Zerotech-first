# 假期后首个交易日 · 预期报告模板

> 用途：国庆假期后首个交易日（预期 2026-10-09 周五，以实际开市日为准）三链路
> 自动事件的开市核验。核验时点建议：当日 08:30 后（三任务均已跑完）。
> 填写方式：逐项填「实际值」与「判定」（✅ 正常 / ⚠️ 偏差需分析 / ❌ 失败需处置），
> 全部核验后按 §5 结论归档，异常走 §4。

## 0. 报告头

| 项 | 值 |
|---|---|
| 核验交易日 | ____（实际开市日） |
| 核验时点 | ____ |
| 前一快照基线 | nine_dim trade_date=20260930 · chanlun pool 最新 detect_date=2026-09-28 |

## 1. 链路一：ChanlunScan（05:50，mls 首批自然数据）

| 核验项 | 预期 | 实际 | 判定 |
|---|---|---|---|
| 任务运行 | exit=0（查 Last Result） | | |
| 扫描输入 | trade_date 已刷新至开市日（假期数据到位） | | |
| 新信号数 | ≥0 均正常（假期后首日不可预判；0 也属正常） | | |
| **mls 覆盖** | 若有新信号：新行 mls 全覆盖（0~1 浮点）；缺失=score_rows 失败需查 err 日志 | | |
| 门户 mls 列 | /bt-chanlun-pool 榜单新行亮值（旧行仍 —） | | |

```powershell
schtasks /query /tn "ChanlunScan" /v /fo LIST | findstr /C:"Last Run" /C:"Last Result"
# 新信号 mls 抽查（quant_backtest 下）
& "C:\Program Files\Python311\python.exe" -X utf8 -c "import json; rs=[json.loads(l) for l in open(r'data\chanlun_pool.jsonl',encoding='utf-8') if l.strip()]; d=max(r.get('detect_date') or '' for r in rs); new=[r for r in rs if r.get('detect_date')==d]; print('detect_date',d,'new',len(new),'mls_ok',sum(1 for r in new if r.get('mls') is not None))"
& "C:\Program Files\Python311\python.exe" -X utf8 page_verify.py /bt-chanlun-pool "<th>mls</th>"
```

## 2. 链路二：NineDimScan（06:40，K33 新代码首次自动运行）

| 核验项 | 预期 | 实际 | 判定 |
|---|---|---|---|
| 任务运行 | exit=0 | | |
| 快照刷新 | trade_date=开市日（不再是 20260930） | | |
| band 字段 | rows 全部带 band（n_alert/alert_th 键存在） | | |
| 警报池 | n_alert=0 正常（≥75 罕见）；**n_alert>0 = 警报池首批入池**，记录票数与代码 | | |
| 门户 | /bt-9dim 头部行「≥75 拥挤警报 N」与快照一致 | | |

```powershell
schtasks /query /tn "NineDimScan" /v /fo LIST | findstr /C:"Last Run" /C:"Last Result"
& "C:\Program Files\Python311\python.exe" -X utf8 -c "import json; s=json.load(open(r'data\nine_dim_scores.json',encoding='utf-8')); print('td',s.get('trade_date'),'n_alert',s.get('n_alert'),'alert_th',s.get('alert_th'),'rows',len(s.get('rows',[])))"
& "C:\Program Files\Python311\python.exe" -X utf8 page_verify.py /bt-9dim "≥75 拥挤警报"
```

## 3. 链路三：K33ShadowCheck（周五 08:00；非周五跳过本节）

| 核验项 | 预期 | 实际 | 判定 |
|---|---|---|---|
| 任务运行 | exit=0（1/2 会触发推送，收不到推送=正常） | | |
| B1/B2 输出 | 与警报池实际状态一致（入池 0 → n=0 通过；入池 N → thinned 统计） | | |
| 推送 | 未收到 alert_fail 推送 | | |

```powershell
schtasks /query /tn "K33ShadowCheck" /v /fo LIST | findstr /C:"Last Run" /C:"Last Result"
Get-Content C:\Temp\k33_shadow_out.log -Encoding UTF8
Get-Content C:\Temp\k33_shadow_task.log -Encoding UTF8 | Select-Object -Last 3
```

## 4. 链路四：weak 产线（WeakTrackCheck 每日 15:30 / WeakScan 周一 15:35）

> 08:30 核验时当日任务尚未到点——只验注册健康+存量数据+门户页；当日产出由任务自身推送触达（触发红卡/扫描蓝卡），异常走 alert_fail。

| 核验项 | 预期 | 实际 | 判定 |
|---|---|---|---|
| WeakTrackCheck / WeakScan 任务 | 均注册且 Last Result=0 | | |
| 在案状态流 | ≥1 份，尾行可读（code/数据日为假期前值属预期） | | |
| 最新扫描档 | CSV 存在且含 focus 列（二道筛产出） | | |
| 门户案例库页 | /bt-weak 200 且「弱于板块」命中 | | |

```powershell
schtasks /query /tn "WeakTrackCheck" /v /fo LIST | findstr /C:"Last Run" /C:"Last Result"
schtasks /query /tn "WeakScan" /v /fo LIST | findstr /C:"Last Run" /C:"Last Result"
# 状态流尾行 / 扫描 focus 列
Get-Content C:\Temp\weak_track_301216_out.log -Encoding UTF8 | Select-Object -Last 5
Get-Content e:\老田的AI助手\reports\weak_scan_*.csv -Encoding UTF8 -TotalCount 1
```

## 5. 异常处置分支（任一 ❌ 时）

| 异常 | 处置 |
|---|---|
| 任务未跑/非零退出 | 查 C:\Temp\ 对应 *_err.log；数据未同步先查 rps_resonance（23:35）是否完成 |
| 新信号 mls 缺失 | score_rows 失败：看 chanlun_scan 日志「score_rows skip」行，常见=模型文件缺失/列不匹配，按 chanlun_mlf 重建 |
| NineDim 无 band/旧结构 | 门户进程吃旧代码（判例：核对监听 PID 创建时间 > 代码 mtime，taskkill /F 重启） |
| 警报池入池但页面无显示 | 同上门户重启；确认 history.jsonl 已生成 |
| K33ShadowCheck exit=2 | 收到推送后按 nine_dim_k33_checklist.md §A 全链复评 |
| weak 任务非零/状态流坏 | 查 C:\Temp\weak_scan_err.log / weak_track_*_err.log；状态流尾行坏按 §8 第零步 weak_track_view --tail 复核 |

## 6. 结论区

- 四链路判定汇总：____ ✅/⚠️/❌
- 警报池首批入池：是/否，若是有：代码 ____ 共 N 票
- mls 首批数据：是/否，若是有：N 条，值域抽查 ____
- weak 产线：在案 N 案 / 最新扫描档 ____
- 是否触发任何处置分支：否 / 是（记录：____）
- 归档：核验完成后本报告另存为 `post_holiday_check_<开市日>.md` 并在 INTEL_RD_PLAN 追加一行结论
