# L2 申请：SentimentBT 恢复（`\ZeroBound\SentimentBT` · 每日 07:20）

> **STATUS: pending_approval**（L2：新增 Windows 计划任务）｜ 2026-11-02 11:20 ｜ 申请人：wa-map（车道4，受 Lead 委托）｜ 批准人：A0/老田
> 关联：**S1-05**（《数据仓研发计划 v3》§四：「sentiment_bt/jev_impact 生成任务排查（恢复或标 deprecated）」）｜ 依据：`docs/audit/2026-11-02_drift_DSN与sentiment链路诊断.md` **§2**（判定：脚本活着、调度没了；方案 C：先 L1 止血 → 再按 L2 恢复）｜ 五要素规范：`docs/adr/ADR-012-l2-five-elements.md`
> 边界声明：**本文档只交申请材料，未执行、未创建/修改任何计划任务、未运行生成器、未推送、未 commit、未重启服务。**

---

## 0. 一页结论（给 A0 读）

| 项 | 内容 |
|---|---|
| 现象 | 情绪×次日回测两产物 **陈旧 357.3h**：`sentiment_bt_result.json`（2026-10-18 13:59，821KB）/ `jev_impact.json`（2026-10-18 13:59，0.4KB）；全机计划任务 Actions 扫描 **0 个任务引用本链**（生成任务缺失，不是脚本废弃） |
| 影响 | ①`/api/daily/data` 的 bt/impact/health 与 `data_daily.html` 仍展示 10-18 数字（health 显式 age=357.3h）；②飞书日报数据实证行已被 **L1 止血**（48h 门槛，commit `6e4a2cd4b`，8 测试）拦为告警行，不再把旧数当当日实证 |
| 申请 | 批准新增计划任务 **`\ZeroBound\SentimentBT` 每日 07:20**，先按**阶段A（不带 `--push`，零外发）**恢复产物；`age<48h` 连续 3 天验证后，**另批/同批授权**切阶段B（启用 `--push` 里程碑推送，前置水位 seed 防补推旧卡） |
| 不批准替代 | 保持 deprecated（零风险、放弃「情绪分组相关性」与「impact×CAR」两项实证指标；L1 止血已兜住外发误读） |
| 建议 | **批准阶段A**；阶段B 作为可选后续（A0 可同批授权但以水位 seed + PUSH skip 验证为前置） |

---

## 1. 背景与现状（证据链，2026-11-02 11:16–11:21 只读实测）

### 1.1 产物陈旧 + 生成任务缺失（非脚本废弃）

| 项 | 实测值 | 证据/命令 |
|---|---|---|
| `sentiment_bt_result.json` | **821.0 KB**，mtime **2026-10-18 13:59**，**age=357.3h**；`n=2308`、`n_dedup_total=3323`、`news_day_range=['2026-09-25','2026-10-18']`、`corr_score_pct1=-0.006` | `Get-Item` + 读 JSON |
| `jev_impact.json` | **0.4 KB**，同 mtime，age=357.3h；`impact_dist={"low":0,"medium":0,"high":0}`、`applied={medium:false,high:false}` | 同上 |
| `sentiment_bt_pushed.json`（推送水位） | 72B，mtime **2026-10-14 20:35**，`{"level":1300,"n":1334,"pushed_at":"2026-10-14T12:35:13Z"}` | 同上 |
| 生成脚本 | `news_collector/sentiment_backtest.py`（26,533B，mtime 10-29；`--push` 里程碑推送、JSON 原子写）；`news_collector/jev_impact.py`（3,335B，mtime 10-27；内部 `import sentiment_backtest as sbt` → `sbt.run()` 一次产出两产物） | 读码 + `--help` rc=0 |
| 启动器 | `news_collector/start_sentiment_bt.ps1`（调 **Python311** + 固定 `--push`；日志 `C:\Temp\sentiment_bt_task.log`/`_out.log`/`_err.log`；失败触发 `alert_fail_wrap.py`） | 读码 |
| 最后运行 | `C:\Temp\sentiment_bt_task.log` 末行 **`sentiment_bt exit=0 2026-10-14T20:35:13`**（历史每日 20:35 触发，exit→日志 约 13–19s） | 日志 |
| 计划任务 | **全任务 Actions 扫描 0 个引用** `sentiment_backtest.py`/`jev_impact.py`/`start_sentiment_bt.ps1`（今日 11:20 复核，含 `\ZeroBound\` 全量）；旧任务名 `StockCorrBacktest` 已不存在 | `Get-ScheduledTask` 全扫 |
| 10-18 产物来源 | 10-18 13:59 双产物同时写入且 `n=2308≠10-14 日志 n=1334` → 为一次人工/临时刷新，非计划任务 | 审计 §2.3 |

### 1.2 消费方（2 处）与 L1 止血现状

| 消费方 | 现状 |
|---|---|
| `portal/api_daily_data.py`：`bt_section()`/`impact_section()` 读两 JSON；`health_section()` 报 mtime/`generated_at`/`age_hours` | 实测 `/api/daily/data`（8091 与 8734 均 200）：`bt.generated_at=2026-10-18T05:59:00Z`、`health.items[sentiment_bt_result].age_hours=357.3` |
| `portal/data_daily.html`（节二/三 + 页脚标注上游 `sentiment_backtest / jev_impact`） | 200（17,269B），页面仍显示该组数据 |
| `news_collector/daily_report.py::_data_line()` → 飞书日报「📊 数据实证」行 | **L1 止血已上线**：`STALE_GATE_HOURS=48`，age>48h → 不外发数据行、改发「数据陈旧…整块跳过（S1-05 止血）」告警；两产物均陈旧则整块跳过（commit `6e4a2cd4b`，8 测试） |

### 1.3 数据与运行条件已就绪（恢复可行性预检，均只读）

| 预检 | 结果 |
|---|---|
| `news.db`（输入） | 127.2 MB，mtime **2026-11-02 11:09**；`news` 112,356 行，`jev` 非空 **111,835** 行；最新 created_at 11-02 11:05（NewsCollect 链活跃） |
| `jev.impact` 字段（jev_impact 分层依赖） | 最新 5,000 条 jev **100% 含 impact**：low 2,569 / medium 2,206 / high 225；全库 `like '%"impact"%'` 62,753/111,835 → **恢复后 `impact_dist` 预计非全 0**（旧产物全 0 为 10-18 时点口径；现码 K-ORG-173 已带 impact，`rows` 含 `impact` 字段，见 `sentiment_backtest.py:133-187`） |
| Python 解释器 | `C:\Program Files\Python311\python.exe` → **Python 3.11.9**；pandas/numpy/pyarrow 均在；`import datamart` 冒烟 **OK**；`sentiment_backtest.py --help` **rc=0** |
| 行情依赖 | `datamart` 读 trade_cal/ts_daily（read guard；TushareSync 23:30 每日维护），恢复后 T+1 覆盖到最近交易日 |
| git 副作用 | `news_collector/data/` 整目录 **gitignored**（`.gitignore:38`）→ 每日产物不产生 `git status` diff |

### 1.4 替代链不覆盖（审计 §2.4 复核）

`tools/jev_audit.py`（`\JevDriftDaily` 07:10 调用，今日 07:10 rc=0）已日更「判定账本/后验/方向准确率/conf 分桶」等**校准类**指标；但**不覆盖** ①sentiment_bt 的「情绪分×次日收益分组（conf/scope）相关性」②jev_impact 的「impact 分层 CAR」——两项仍有独立信息价值，非纯重复。

---

## 2. 五要素①：命令全文（待批；批准后执行）

> 执行前提：L2 批准 + 由 DATA1/DSH-Lead 在**当前登录用户交互式会话**执行（对齐既有 `\ZeroBound\` 任务口径，见 §2.3 安装脚本）。**阶段A全程不带 `--push`，零外发。**

### 2.0 预检（只读 + 备份，批准前后均可跑；当前已实测通过）

```powershell
$py = 'C:\Program Files\Python311\python.exe'
$nc = 'C:\项目\老田的AI助手\news_collector'

# 0) 查重：期望「找不到」
schtasks /Query /TN "\ZeroBound\SentimentBT"

# 1) 现状取证（期望 age≈357h，见 §1.1）
Get-ChildItem "$nc\data\sentiment_bt_result.json","$nc\data\jev_impact.json","$nc\data\sentiment_bt_pushed.json" |
  Select-Object Name,Length,LastWriteTime

# 2) 回滚备份（本次产物 + 水位）
$bk = 'C:\Temp\sentiment_bt_backup_20261102'
New-Item -ItemType Directory -Force $bk | Out-Null
Copy-Item "$nc\data\sentiment_bt_result.json" $bk -Force
Copy-Item "$nc\data\jev_impact.json"        $bk -Force
Copy-Item "$nc\data\sentiment_bt_pushed.json" $bk -Force
Get-ChildItem $bk | Select-Object Name,Length

# 3) 依赖/解释器冒烟（只读，无副作用；预期 OK / rc=0）
& $py -X utf8 -c "import sys;sys.path.insert(0,r'C:\项目\老田的AI助手\quant_backtest');import datamart;print('datamart_import_OK')"
& $py -X utf8 "$nc\sentiment_backtest.py" --help
```

### 2.1 首跑（**不带 `--push`**，L1，人工执行；输出必须落盘）

```powershell
$py = 'C:\Program Files\Python311\python.exe'
$nc = 'C:\项目\老田的AI助手\news_collector'
$log = 'C:\Temp\sentiment_bt_first_run_out.log'

# 推荐：单跑 jev_impact.py —— 其内部 sbt.run() 一次计算即写两产物（sentiment_bt_result.json + jev_impact.json），
# 且绝不触发推送（推送只在 sentiment_backtest.py __main__ 的 --push 分支）。
& $py -X utf8 "$nc\jev_impact.py" *> $log
"sbt_first_run_rc=$LASTEXITCODE" | Tee-Object -Append $log

# 若需看 sentiment 明细报告（可选，多约 20s、无副作用、无推送）：
& $py -X utf8 "$nc\sentiment_backtest.py" *>> $log
"sbt_detail_rc=$LASTEXITCODE" | Tee-Object -Append $log
```

**首跑期望**：`rc=0`；两产物 mtime=当日、`generated_at`=当日且 `n` 显著 >2308（预估 3,000–5,000，估算，首跑实测为准）、`news_day_range` 推进到最近交易日；`jev_impact.impact_dist` 非全 0；**不产生任何飞书推送**。
**若首跑失败**：不改码强行重试；记录 rc/日志 → 回 §6 回滚 + 报 DSH-Lead。

### 2.2 验收（首跑后立即做；明细见 §7）

```powershell
# A) 产物 age<48h
$now = Get-Date
Get-ChildItem "$nc\data\sentiment_bt_result.json","$nc\data\jev_impact.json" | ForEach-Object {
  "{0} age_h={1:N1}" -f $_.Name, ($now - $_.LastWriteTime).TotalHours
}
# B) 内嵌 generated_at/日期范围/n
& $py -X utf8 -c "import json;d=json.load(open(r'$nc\data\sentiment_bt_result.json',encoding='utf-8'));print(d['generated_at'],d['n'],d['news_day_range'])"
# C) API health 回落（期望 age_hours<48）
(Invoke-WebRequest 'http://127.0.0.1:8091/api/daily/data' -UseBasicParsing).Content | ConvertFrom-Json |
  ForEach-Object { $_.health.items } | Where-Object { $_.name -in 'sentiment_bt_result','jev_impact' } |
  Select-Object name,age_hours,generated_at
# D) 日报 L1 门槛放行（只读调函数，不推送）
& $py -X utf8 -c "import sys;sys.path.insert(0,r'$nc');import daily_report;print(daily_report._data_line())"
# 期望：输出「📊 数据实证…」正常行，不再出现"数据陈旧…已跳过（S1-05 止血）"
```

### 2.3 安装任务（阶段A：**无 `--push`**，L2 批准后执行）

```powershell
$py  = 'C:\Program Files\Python311\python.exe'
$nc  = 'C:\项目\老田的AI助手\news_collector'

$action  = New-ScheduledTaskAction -Execute $py `
             -Argument ('-X utf8 "' + $nc + '\jev_impact.py"') -WorkingDirectory $nc
$trigger = New-ScheduledTaskTrigger -Daily -At '07:20'
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
             -ExecutionTimeLimit (New-TimeSpan -Minutes 20) `
             -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskPath '\ZeroBound\' -TaskName 'SentimentBT' `
  -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
  -Description "零界·SentimentBT 情绪×次日回测+CAR实证 每日07:20（S1-05 阶段A：无push）"
```

- **为什么单 Action 用 `jev_impact.py`**：其内部 `sbt.run()` 已写 `sentiment_bt_result.json`，随后写 `jev_impact.json`——一次计算覆盖两产物、且无推送分支；避免「sentiment_backtest + jev_impact」双跑（2× 计算）。
- **变体B（严格保留旧脚本边界）**：Task 配两条 Action——① `python sentiment_backtest.py` ② `python jev_impact.py`（顺序执行；耗时约 2×）。仅在需要单独保留 sentiment 退出码语义时选用。
- **不直接安装现版 `start_sentiment_bt.ps1` 的原因**：该启动器**硬编码 `--push`**（会外发），只能用于阶段B。

### 2.4 安装后验证（L2 执行人留证）

```powershell
schtasks /Query /TN "\ZeroBound\SentimentBT" /V /FO LIST      # 期望：Ready / 下次 07:20 / 动作=Python311 jev_impact.py
schtasks /Run   /TN "\ZeroBound\SentimentBT"                  # 手动触发一次（仍无 push）
Start-Sleep -Seconds 90
schtasks /Query /TN "\ZeroBound\SentimentBT" /FO LIST         # 期望 LastTaskResult=0
Get-ChildItem "$nc\data\sentiment_bt_result.json","$nc\data\jev_impact.json" | Select Name,LastWriteTime
(Invoke-WebRequest 'http://127.0.0.1:8091/api/daily/data' -UseBasicParsing).Content | ConvertFrom-Json |
  ForEach-Object { $_.health.items } | Where-Object { $_.name -eq 'sentiment_bt_result' } | Select age_hours
# 期望：rc=0；两产物刷新；/api/daily/data health age_hours<48
```

### 2.5 阶段B（可选后续：启用 `--push` 里程碑推送；**需另批或同批明确授权**）

> 只有 A0 明确"验证后可启 push"或另批 L2 才执行；**执行前必须 seed 水位**，否则首日会补推 1 张旧里程碑卡（当前水位 level=1300/n=1334，新 milestone 预计 3,000+）。

```powershell
$py = 'C:\Program Files\Python311\python.exe'; $nc = 'C:\项目\老田的AI助手\news_collector'

# ① 备份 → ② 水位 seed 到当前产物 milestone（_maybe_push：level=200+((n-200)//100)*100）
& $py -X utf8 -c @"
import json, os, shutil, datetime
base = r'C:\项目\老田的AI助手\news_collector\data'
res = json.load(open(os.path.join(base,'sentiment_bt_result.json'), encoding='utf-8'))
n = res['n']; level = 200 + max(0,(n-200)//100)*100
dst = os.path.join(base,'sentiment_bt_pushed.json')
shutil.copy2(dst, dst + '.bak-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
json.dump({'level': level, 'n': n, 'pushed_at': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')},
          open(dst,'w',encoding='utf-8'), ensure_ascii=False, indent=1)
print('seeded', level, n)
"@

# ③ 切 Action 为既有启动器（--push + 日志 + 失败告警；见 start_sentiment_bt.ps1）
$a2 = New-ScheduledTaskAction -Execute 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' `
        -Argument '-NoProfile -ExecutionPolicy Bypass -File "C:\aios\news_collector\start_sentiment_bt.ps1"' `
        -WorkingDirectory 'C:\aios\news_collector'
Set-ScheduledTask -TaskPath '\ZeroBound\' -TaskName 'SentimentBT' -Action $a2 | Out-Null

# ④ 验证不补推旧卡（期望 skip，不发飞书）
Start-ScheduledTask -TaskPath '\ZeroBound\' -TaskName 'SentimentBT'
Start-Sleep -Seconds 90
Get-Content C:\Temp\sentiment_bt_out.log -Tail 5      # 期望 "PUSH skip: level ... already sent"，无 PUSH_OK level=1300
```

---

## 3. 五要素②：影响面

### 3.1 写/读/外发清单

| 类别 | 对象 | 说明 |
|---|---|---|
| **写（4 个文件，均 gitignored）** | `news_collector/data/sentiment_bt_result.json`（原子 `os.replace`，~0.8MB）｜`data/jev_impact.json`（~0.4KB）｜`data/history/sbt_daily.jsonl`（首次创建，append-only 每日 ~200B；M3 起势图消费）｜`data/sentiment_bt_pushed.json`（**仅阶段B push 成功时**写） | 全部位于 `news_collector/data/`，`.gitignore:38` 已忽略 → **不产生 git diff** |
| **读** | `news_collector/news.db`（127MB，sqlite `mode=ro`；jev 111,835 条）｜`news_config.json`（watchlist 58 项，名称→ts_code 映射）｜`quant_backtest` 行情（`datamart` trade_cal/ts_daily，受 read guard/发布锁保护） | 只读；不改新闻库、不改数据仓、不写 catalog/registry |
| **外发** | **阶段A：零外发**（无 push 分支，纯本地计算）｜阶段B：里程碑卡片/文本经 `card_build.push_card`→`send_feishu` 发飞书（仅 n 破百里程碑触发，水位防重；失败降级文本） | 阶段B 风险最高点，前置水位 seed 控制 |
| **服务** | **不碰** 8091/nginx/8734/8787、不重启服务、不动数据仓/凭据、不新增端口 | 无系统级变更 |
| **日志** | 阶段A 人工首跑：`C:\Temp\sentiment_bt_first_run_out.log`；阶段B 启动器：`C:\Temp\sentiment_bt_task.log`/`_out.log`/`_err.log`（既有路径，按启动器语义覆盖写） | 遵循 AGENTS §7「长跑输出落文件」 |
| **时间窗** | 每日 07:20（JevDriftDaily 07:10 之后；`DailyBrief0745` 07:45 / `AI盘前分析` 08:20 早报窗口之前；DailyReport 03:00/20:30 消费）；与 `ChanlunScan` 07:20 同点、`GuojiaDaily` 07:25 邻点 | 轻微 CPU/磁盘读争用，PT20M 上限充裕 |

### 3.2 资源与耗时（估算 + 历史证据）

- **历史实测**：`sentiment_bt_task.log` 显示 2026-10-05~10-14 每次 `exit=0`，按 20:35 定时触发→exit 时间戳差 **约 13–19s**（n=1334 时）。
- **本次预计**：`news_day_range` 从 09-25..10-18（24 天）推进到 09-25..11-01（38 天），n 预估 3,000–5,000；耗时 **估算 30–90s**（单次），内存峰值低（sqlite + parquet 读）；**首跑实测为准**，若 >5min 则暂停并复核（PT20M 是硬上限，不会挂死系统）。
- 触发点冲突：07:20 与 `ChanlunScan` 同分钟——首跑实测若资源争用明显，可由 L2 微调至 07:22（改触发器同样属 L2 变更）。

### 3.3 对既有输出/契约的变化

| 输出 | 恢复前 | 恢复后（预期） |
|---|---|---|
| `sentiment_bt_result.json` | `generated_at=2026-10-18`、n=2,308、range 09-25..10-18 | `generated_at`=当日、n≈3,000–5,000、range 推进；**字段/结构不变**（`bt_section` 契约沿用 generated_at/news_day_range/n/...） |
| `jev_impact.json` | `impact_dist` 全 0、`applied` 全 false | `impact_dist` 预计 low/medium/high 非全 0（当前 jev impact 覆盖 100% 最新样本）；`applied` 可能仍 false（需 impact≥30 且增益≥1pp） |
| `/api/daily/data` health | `age_hours=357.3` | `age_hours<48` |
| 飞书日报数据实证行 | L1 止血告警行 | 自动恢复为正常「📊 数据实证」行（`_data_line` 门槛按年龄自动判定，无需改码） |

---

## 4. 五要素③：不可逆点

| # | 不可逆点 | 说明 | 规避 |
|---|---|---|---|
| 1 | **`--push` 外发飞书不可撤回** | 阶段B 会把里程碑卡片/文本推到个人/群；已送达消息只能事后删除，**触达不可回收** | **首跑强制不带 `--push`**（阶段A 无任何外发）；阶段B 前 seed 水位 + PUSH skip 验证才放行；不启用 push 则无此点 |
| 2 | 产物覆盖 | 原子写覆盖旧 JSON（旧内容不保留） | §2.0 已备份到 `C:\Temp\sentiment_bt_backup_20261102\`；回滚见 §6 |
| 3 | 推送水位覆盖 | 阶段B seed/推送会覆盖 `sentiment_bt_pushed.json`（当前 level=1300/n=1334） | 备份（pre-seed 已带时间戳 `.bak-*`）；回滚恢复备份即回到旧水位 |
| 4 | 数据仓/服务 | **无**——本链不写数据仓、不碰服务，无需 8091/nginx 重启，无端口/凭据变更 | — |

---

## 5. 五要素④：替代方案与已尝试

| 方案 | 内容 | 结论 |
|---|---|---|
| **A（本申请）恢复调度** | 每日 07:20 恢复两产物生成；先无 push 后可选 push | ✅ **推荐**（消费方/页面契约零改动；成本最低） |
| B 保持 deprecated | `data_daily` 页面标注"已停更（历史）"；日报 L1 止血已生效 | ⚠️ 可行兜底（零风险，但放弃两项实证指标）；**L1 部分已上线** |
| C 只恢复 sentiment_bt | 不恢复 `jev_impact` | 🟡 半覆盖（impact×CAR 仍缺） |
| D 用 `jev_audit` 替代 | 仅日更校准指标 | ❌ 不覆盖「情绪分组相关性」与「impact×CAR」（审计 §2.4） |
| E 改代码/换 py312 | 重写/迁移 | ❌ 无必要：`sentiment_backtest.py`/`jev_impact.py` 未废弃、py311 依赖预检通过（`--help` rc=0、`import datamart` OK） |

**已尝试/已留证**：L1 止血已上线（`STALE_GATE_HOURS=48`，commit `6e4a2cd4b`，8 测试新增）；S1-05 诊断已完成（audit §2，含产物/任务/替代链全量证据）；本次预检（查重/备份/依赖冒烟/CLI help）全部只读通过。**未运行生成器、未推送任何消息。**

---

## 6. 五要素⑤：回滚预案（按序执行）

1. **删任务**（若已建）：
   `Unregister-ScheduledTask -TaskPath '\ZeroBound\' -TaskName 'SentimentBT' -Confirm:$false`
   （等价 `schtasks /Delete /TN "\ZeroBound\SentimentBT" /F`；旧任务本就不存在，无需恢复启用）
2. **停推送**（若已进入阶段B）：先删/禁用任务，再执行 3–4。
3. **恢复产物**：
   `Copy-Item 'C:\Temp\sentiment_bt_backup_20261102\sentiment_bt_result.json','C:\Temp\sentiment_bt_backup_20261102\jev_impact.json' 'C:\项目\老田的AI助手\news_collector\data\' -Force`
4. **恢复水位**：
   `Copy-Item 'C:\Temp\sentiment_bt_backup_20261102\sentiment_bt_pushed.json' 'C:\项目\老田的AI助手\news_collector\data\' -Force`
5. **验证回滚**：
   - `schtasks /Query /TN "\ZeroBound\SentimentBT"` → 找不到；
   - 两产物 mtime 回到 2026-10-18；
   - `/api/daily/data` health `age_hours>48`；`daily_report._data_line()` 重新输出「数据陈旧…已跳过（S1-05 止血）」告警行（L1 止血兜底，外发误读风险回到 0）。
6. **无需**回滚数据仓/服务（本次零接触）。

---

## 7. 验收命令与 DoD（S1-05）

```powershell
$py = 'C:\Program Files\Python311\python.exe'
$nc = 'C:\项目\老田的AI助手\news_collector'

# 1) 任务态
schtasks /Query /TN "\ZeroBound\SentimentBT" /FO LIST | Select-String 'TaskName|Status|Next Run Time|Last Result'
# 2) 产物新鲜度（DoD：age<48h 连续 3 天）
$now=Get-Date; Get-ChildItem "$nc\data\sentiment_bt_result.json","$nc\data\jev_impact.json" |
  ForEach-Object { "{0} age_h={1:N1} rc-check" -f $_.Name, ($now-$_.LastWriteTime).TotalHours }
# 3) API 回落（DoD 佐证）
(Invoke-WebRequest 'http://127.0.0.1:8091/api/daily/data' -UseBasicParsing).Content | ConvertFrom-Json |
  ForEach-Object { $_.health.items } | Where-Object { $_.name -in 'sentiment_bt_result','jev_impact' } | Select name,age_hours
# 4) 日报行恢复（只读，不推送）
& $py -X utf8 -c "import sys;sys.path.insert(0,r'$nc');import daily_report;print(daily_report._data_line())" | Select-String '数据实证|陈旧'
# 5) 无旧卡补推（阶段B 后检查）
Get-Content C:\Temp\sentiment_bt_out.log -Tail 5 | Select-String 'PUSH_OK|PUSH skip'
# 6) 页面可达
(Invoke-WebRequest 'http://127.0.0.1:8734/data_daily.html' -UseBasicParsing).StatusCode   # 期望 200
```

**DoD**：①任务每日 07:20 自动跑、`LastTaskResult=0`；②两产物 `age<48h` **连续 3 天**；③`/api/daily/data` health age<48h；④`daily_report` 数据行恢复为正常行（不再触发 48h 止血提示）；⑤无任何旧里程碑卡补推（阶段B 时验证 `PUSH skip`）；⑥页面 200 且数字更新。

---

## 8. 风险与缓解

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R1 | 首跑耗时超预期（n 增大；与 07:20 ChanlunScan 争用） | 07:45/08:20 早报窗口受影响 | 首跑人工计时；PT20M 硬上限；必要时 L2 微调 07:22 |
| R2 | `--push` 补推旧里程碑卡（水位停在 10-14） | 飞书误发旧卡 | 阶段A 完全禁 push；阶段B 前置 seed 水位 + `PUSH skip` 验证 |
| R3 | `news.db`/行情链再次断流 → 产物重新陈旧 | 回到当前状态 | L1 止血（48h）继续兜底；`\DailyReport` 20:30 可见告警行；news.db 由 NewsCollect 链维护 |
| R4 | `jev.impact` 字段口径/覆盖变化 | `impact_dist` 仍可能偏低或全 0 | 已实测最新 5,000 条 100% 含 impact；首跑后检查 `impact_dist`，若全 0 另立数据口径单（不阻塞 DoD age<48h） |
| R5 | 回归旧报错（py311 依赖/代码改动 10-29 后未跑过） | 首跑失败 | 预检 `--help`/`import datamart` 已过；首跑失败即按 §6 回滚并报 DSH-Lead，不盲目重试 |
| R6 | 任务注册权限（`\ZeroBound\` 目录 ACL / 当前用户 Principal） | 安装失败 | 对齐 `\ZeroBound\DataWarehouseSnapshot` 既证（`t` / Interactive / Limited，2026-11-02 安装成功）；失败则管理员窗口执行或改用 `schtasks /Create` |
| R7 | 产物被消费方按旧格式解析 | 页面/日报异常 | `sentiment_bt_result.json`/`jev_impact.json` 字段契约不变（消费方只读 generated_at/n/groups/...）；恢复后 `/api/daily/data` 与页面回归验证 |
| R8 | 回滚遗漏（未备份即首跑） | 无法恢复旧产物 | §2.0 强制先备份；备份路径固定在 `C:\Temp\sentiment_bt_backup_20261102\` |

---

## 9. 批准语建议（供 A0 勾选）

- [ ] **批准阶段A**：`\ZeroBound\SentimentBT` 每日 07:20，Action=`C:\Program Files\Python311\python.exe -X utf8 ...\news_collector\jev_impact.py`（**不带 --push**；首跑由 DATA1 人工执行并留证；DoD=age<48h 连续 3 天）。
- [ ] **批准阶段B（可选）**：阶段A 验收 3 天后，切启动器 `start_sentiment_bt.ps1`（启用 `--push` 里程碑推送）；执行前置=水位 seed + PUSH skip 验证。
- [ ] 不批准，维持 deprecated（L1 止血已上线，日报不再误发旧数）。

---

## 10. 只读参考与执行边界

- 参考：`docs/audit/2026-11-02_drift_DSN与sentiment链路诊断.md`（§2 S1-05）、`docs/plans/2026-11-02_数据仓研发计划_v3_执行版.md`（S1-05/S1-01）、`news_collector/sentiment_backtest.py`、`news_collector/jev_impact.py`、`news_collector/start_sentiment_bt.ps1`、`news_collector/daily_report.py`、`portal/api_daily_data.py`、`portal/data_daily.html`、`.gitignore:38`、既有 `\ZeroBound\DataWarehouseSnapshot` 任务口径。
- 边界：本单**只写本申请文档**；未执行生成器/未推送/未建改任务/未 commit/未重启服务；所有数字均为 2026-11-02 11:16–11:21 本机只读实测。
- 复核人：DATA1（数据面）/ DSH-Lead（验收）/ A0（L2 批准）。

---
*申请生成：2026-11-02 11:20 · wa-map（车道4）· S1-05 方案 C 恢复 L2 材料*
