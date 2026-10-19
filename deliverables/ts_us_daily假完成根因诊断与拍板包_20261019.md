# ts_us_daily 假完成根因诊断与拍板包（2026-10-19 晚，只读诊断 · 待老田拍板）

> 性质：只读诊断结论，供拍板。**未改任何代码与数据**。诊断工位 WS-DIAG，负责人已独立抽验关键 claim（manifest 条目 / 8 张零行表 / tdx_sync.py:152-186 代码结构 / 增量去重自锁）全部属实。

## 一、现状（实测）

- manifest：`quant_backtest/data/tdx/_manifest.json`（mtime 2026-10-19 18:15:30，178 张表）
- ts_us_daily 条目：`{"offset": 200000, "part": 1, "rows": 200000, "done": true}`
- 磁盘：`data/tdx/ts_us_daily/part_0000.parquet`（6,122,201 字节，18:15:29）实测 **200,000 行**，trade_date 覆盖 **2026-09-18 ~ 2026-10-15**（约 1 个月窗口），15,674 个 ts_code
- 远端只读实测：status 广告 rows=**15,359,085**；unfiltered 查询 total=**59,747,485**；offset=200000/300000 处均有行
- **本地完整度仅 0.3%~1.3%**——本地只是远端按日期 DESC 排序的头部窗口，`done=True` 声称完整实为 1%
- 系统性异常：manifest 中 **8 张表 done=True/rows=0**（ts_etf_index、ts_fut_index_daily、ts_fut_weekly_monthly、ts_hk_mins、ts_index_member、ts_opt_daily、ts_opt_mins、ts_rt_k）；其中 ts_hk_mins（远端 217,712,984 行）/ts_opt_daily（远端 23,487,514 行）**目录均不存在**；ts_fut_weekly_monthly 磁盘有 37,088 行孤儿分片而 manifest 记 rows=0；ts_etf_index/ts_fut_index_daily/ts_index_member 远端实为 0 行（合理空）；ts_opt_mins/ts_rt_k 远端 rows=-1（性质未知）
- 18:15 写入窗口：17:30 会话自 17:31 起连续写 27 张表，ts_us_daily 18:15:29 落盘、manifest 18:15:30 盖 done 后**再无任何写入**；task.log 无 attempt/exit 回执；out.log 该会话零 python 输出（块缓冲随疑似硬终止丢失）；调度器至 19:59 仍显示 Running(267014)

## 二、根因（按置信度）

### 主因（最高置信，代码级充分条件）：`tdx_sync.py:152-186` 把"翻页循环退出"无条件等同于"表同步完成"

循环仅两个出口：空页 break（159-160）、offset>=total break（175-176）；出口后直达 `st["done"]=True` + 落 manifest（182-183），**无任何后置校验**——不校验拉到行数>0、不校验落盘分片>0、不校验实拉行数与远端广告行数一致性。远端一次"HTTP 200 但零行"即被判完成。

三个子证据：
1. **原始挂账态复现**：空页停在第 1 页 → rows=0；flush() 因 buf 空直接返回；`_part_path` 只在 flush 内调用故**目录都不创建**——与旧挂账记录"目录不存在 + done=True/rows=0"逐字吻合
2. **当前态复现**：17:30 会话加载到 done=True/rows=0/part=0 旧条目 → `_parquet_latest_date` 无分片返回 None → 回落**全量模式**从 offset 0 重拉 → 恰好 40 页×5000=200,000 行触发一次落盘 → 第 41 页（offset=200000）空页 → break → 18:15:30 盖 done=True。远端 total 5974 万 ≫ 200K，offset>=total 的 break 不可能触发，**唯一可能是 offset=200000 处空页**（远端此刻实测有数据，属瞬时软失败）
3. **粘性自锁**：done=True 后本地最新日 20261015 成为增量起点（140-144 行），增量去重滤掉所有 ≤20261015 的行（161-168 行），而远端 DESC 第一页正好从 20261015 起 → 整页被滤空 → 立即 break → 再次 done、零新行。**缺史永不自愈**；即便增量恢复，也只补 >20261015 的新日期，不补 09-17 及更早历史

### 高置信（触发窗口）：远端"成功响应但零行"软失败与代码合谋

硬故障（DNS/非 JSON）抛异常进 failed 列表、不会盖 done——out.log 中 ts_us_daily 三次 ERROR（10-13/10-14 "Expecting value"×2、10-17 DNS×1）均为异常路径。假 done 只可能来自软失败；10-13~10-16 正值远端不稳期（板块日线远端 10-12 后停更、status 10-14 返非 JSON）。**该 bug 至今仍在代码里**，与远端是否恢复无关。

### 中高置信（放大/取证因素）：17:30 会话疑似被硬终止，stdout 块缓冲整体丢失

python 对重定向文件块缓冲约 8KB，该会话累计输出不足 8KB 随进程硬终止丢失（对比 10-17 正常退出 flush 出 45KB）。终止者与确切死因无可采信回执。

## 三、修复方向与所需授权（均未实施）

1. **最小代码改动（治本，建议先行）**：`sync_parquet_table` 在 182 行盖 done 前加后置闸门——①本表必须新落 ≥1 个分片；②实拉行数与远端广告行数（meta/total）差异超阈值（如 1%）记 incomplete 并计入 failed（接 `_run_sync` 现有非零退出与告警链）；③远端广告 rows>0 而实拉 0 行直接判失败。可选：空页改为重探一次再停，区分对端真空与瞬时空。
   **授权**：改生产同步脚本，按 K-STOCK-413/416 五要素流程（.bak 备份 + 单独审批）。
2. **ts_us_daily 专项愈合（二选一，待拍板）**：
   - (a) 全量重拉——需先重置 manifest 该项并移走 part_0000.parquet（删改仓内数据须授权），远端 1,536 万~5,947 万行按现速率为数小时级长跑，须单独五要素审批 + 运行窗口；
   - (b) 接受滚动窗口语义——把该表显式标为 partial（如 done=False + coverage_from=20260918）并同步下游对美股数据完整性的预期。
   - 现状 done=True 是最坏选项（声称完整实则 1%）。
3. **系统性清扫**：闸门上线后跑一次对账，把 8 张 zero-done（重点是 3 张有远端数据的）与孤儿分片（ts_fut_weekly_monthly）一并重拉或显式标注。
4. **可观测性**：`start_tdx_sync.ps1` 给 python 加 `-u`（或 PYTHONUNBUFFERED=1），避免硬终止时表级进度整体丢失；out.log 的 UTF-8/UTF-16 混合编码建议统一，否则 grep 长期失效。

## 四、一句话结论

**假完成不是远端数据问题，是 `tdx_sync.py` "空页/截断即 done" 的判定缺陷**——远端现在有 1,536 万行可拉，但脚本会把任何一次空页当完成，且 done 之后增量去重会让缺史永久锁死。是否重拉、接受窗口还是全量，请老田定；**定之前脚本闸门（方向 1）建议先行**，否则重拉同样可能再次被假 done 截断。
