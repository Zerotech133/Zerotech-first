# report_rc manifest 重建 + 资质复判 · 回执

> 2026-11-02 ｜ 执行席位：dwgov-impl（受 DATA1 委托）｜ 任务：task-29（车道2）
> 结果：**manifest 修复成功、report_rc 资质 evidence_partial → evidence_ok 写回成功；catalog_snapshot code=0**；metadata gate 因并发 task-30 新增 lifecycle 待 Lead 串行 rebuild 暂 FAIL（非本单改动，见 §5）。

## 一、结论

| 项 | 结果 |
|---|---|
| `manifest_repair --json` 预览 | report_rc：**94 files / rows=1,729,477**（footer 合计，与任务预期一致）+ xq_quote_snap（open，1 file/2 rows）同批可修；quarantine_prod_overwrite(deprecated)/xq_comment(planned) 按 lifecycle 跳过 |
| `manifest_repair --apply` | rc=0，changed=`["report_rc","xq_quote_snap"]`，自动备份+kg 流水 |
| tushare `_manifest.json` report_rc 键 | **已出现**：`{"done":true,"rows":1729477,"files":94,"repaired_at":"2026-11-02T08:52:21","repaired_by":"manifest_repair"}`（71 键） |
| `qualify_probe --tables report_rc` dry-run | 修复前 `evidence_partial`(reason=manifest_missing) → 修复后 **`evidence_ok`(reasons=[])** |
| `qualify_probe --apply` | rc=0、code=0、validation 11 项全 True；registry report_rc **unknown → evidence_ok**，写回备份 `.bak-20261102-085232`，display 174 保留 |
| catalog_snapshot | **code=0，issues=[]，drift_warn=[]**，physical 256/display 174/sha match |
| catalog.json / 代码 | **未改**（catalog mtime 仍 2026-11-02T06:22:47）；未 commit/未重启 |

## 二、前后对比

**manifest（tushare/`_manifest.json`）**：report_rc 键 无 → 有（done=true, files=94, rows=1,729,477）；同批 xq_quote_snap（xueqiu manifest）done/rows/files 已补（仍保留原 degraded note：quotec 403 黑名单）。备份：`tushare/_manifest.json.bak-20261102-085221`、`xueqiu/_manifest.json.bak-20261102-085221`（原文件 10,341 B / 5,388 B）。

**资质分布（registry physical 256）**：
| | evidence_ok | evidence_partial | unknown | deprecated | planned |
|---|---|---|---|---|---|
| 前 | 101 | 34 | 113 | 1 | 7 |
| 后 | **102** | 34 | **112** | 1 | 7 |

report_rc：`unknown` → `evidence_ok`；`evidence_checked_at=2026-11-02T08:52:32+08:00`；`unsupported_reason="资格取证批次1：文件/行齐备；静态/无日期列（no_date_column）日期检查豁免（口径标注 visible）/manifest，待发布/覆盖核验"`。

## 三、执行序列（kg 互斥纪律）

1. 操作前读 `C:\Temp\kg_sync_task.log`：尾部为已完成 `qual_probe ... exit=0`（无进行中）；当前 08:5x，**非 TushareSync 23:30 窗口**。
2. 预览/apply 过程中遇并发阻断：`agent_platform/catalogs/data_lifecycle.yaml` 被 task-30（wa-govern）工作区改动引入 YAML 语法错误（reason 多行 plain scalar 含 `SLA #14` 的 `#`），`lifecycle._load()` 抛 ParserError 使 `manifest_repair` 无法预览。已联系 wa-govern，其修复（改 `>-` 折叠标量）后立即重跑成功。
3. 本单 kg 补记（工具自动）：
   - `manifest_repair date=2026-11-02T08:52:21+08:00 tables=2 files=2 exit=0`
   - `qual_probe date=2026-11-02T08:52:32+08:00 tables=1 ok=1 partial=0 bad=0 exit=0`

## 四、改动文件（仅数据/manifest）

- `quant_backtest/data/tushare/_manifest.json`（+report_rc；备份 .bak-20261102-085221）
- `quant_backtest/data/xueqiu/_manifest.json`（xq_quote_snap 补齐；备份 .bak-20261102-085221）
- `quant_backtest/data/warehouse_registry.json`（report_rc 资格字段；备份 `.bak-20261102-085232`，576,998 B）
- 未改 catalog.json / 未改代码 / 未 commit / 未重启。

## 五、门禁与待办（重要）

- `catalog_snapshot.py --json` → **rc=0 / code=0 / issues=[]**。
- `metadata_consistency.py` → **rc=1，仅 2 条 issue**：
  `qualification_not_lifecycle:us_legacy:unknown!=deprecated`、`qualification_not_lifecycle:crypto_daily:unknown!=deprecated`。
  **归因**：并发 task-30 刚把 us_legacy/crypto_daily 标为 deprecated（lifecycle），但按 task-30 约定「**由 Lead 串行执行** build_catalog + registry_rebuild」，registry 尚未同步 → 属预期中间态，与本单 report_rc 改动无关（report_rc 相关检查全通过）。**建议 Lead 在串行收口时跑 `registry_rebuild.py --apply`**（会同步 lifecycle 资格 + 让 catalog 刷新 manifest_status/done），随后 metadata 应 PASS。

## 六、回滚

```powershell
Copy-Item "C:\项目\老田的AI助手\quant_backtest\data\tushare\_manifest.json.bak-20261102-085221" `
          "C:\项目\老田的AI助手\quant_backtest\data\tushare\_manifest.json" -Force
Copy-Item "C:\项目\老田的AI助手\quant_backtest\data\xueqiu\_manifest.json.bak-20261102-085221" `
          "C:\项目\老田的AI助手\quant_backtest\data\xueqiu\_manifest.json" -Force
Copy-Item "C:\项目\老田的AI助手\quant_backtest\data\warehouse_registry.json.bak-20261102-085232" `
          "C:\项目\老田的AI助手\quant_backtest\data\warehouse_registry.json" -Force
```
注：三处备份均为本单 apply 前状态；catalog.json/代码本单未改无需回滚。

## 七、复核命令

```powershell
python -X utf8 -c "import json;d=json.load(open(r'quant_backtest/data/tushare/_manifest.json',encoding='utf-8'));print(d['report_rc'])"
C:\项目\py312\python.exe -X utf8 tools/datawarehouse/qualify_probe.py --tables report_rc --dry-run --json
C:\项目\py312\python.exe -X utf8 tools/datawarehouse/catalog_snapshot.py --json
C:\项目\py312\python.exe -X utf8 tools/datawarehouse/metadata_consistency.py --json
Get-Content C:\Temp\kg_sync_task.log -Tail 3
```
