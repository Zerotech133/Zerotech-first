# backfill_sprint 首跑验证报告

- 日期：2026-10-18（周六）
- 执行：sprint-verify-dev（数据线第五轮）
- 结论：**链路验证全部通过，可启动全量追平**；hard=1 遗留行已被 r2 批次补标清零。

## 一、链路首跑证据（--limit 1 --force）

- 环境：受管 venv `C:/Users/t/.workbuddy/binaries/python/envs/default/Scripts/python.exe`
- 前置状态：Ollama 127.0.0.1:11434 在线，`qwen3.5:9b` 在位；锁文件不存在（无残留）
- 命令：`python tools/backfill_sprint.py --limit 1 --force`
- 运行时间：13:57:29 - 13:57:54（25 秒）

### 逐项验证

| 验证项 | 结果 | 证据 |
|---|---|---|
| 锁创建/释放 | 通过 | 跑前无锁文件，跑后 `backfill_sprint.lock` 不存在（`ls` 确认 No such file） |
| --force 绕过窗口 | 通过 | 日志行 `window bypassed by --force` |
| 锁获取（O_EXCL） | 通过 | sprint 正常进入主循环，无 lock busy |
| 让路探测 | 通过（未触发，符合预期） | 状态日志最后写入 13:16:13，距今 >60s，`yielded=0`；启动期探测逻辑生效 |
| LLM 批量实跑 | 通过 | `OK 136223392 cat=104 广告推广/ad \| bullish conf=0.92 24.3s` |
| DB 实际写入 | 通过 | classified 2012→2013，ok 2009→2010，pending 16332→16331 |
| 进度日志落行 | 通过 | `backfill_sprint.log` 新增 9 行（sprint start/shard start/shard end/progress/sprint end/final 等） |
| 退出码 | 通过 | `EXIT_CODE=0`（RC_OK） |

### 日志原文（本次实跑段）

```
2026-10-18 13:57:29 | window bypassed by --force
2026-10-18 13:57:29 | sprint start source=18344 classified=2012 ok=2009 degraded=3 pending=16332
2026-10-18 13:57:29 | shard start done=0 shard=1
2026-10-18 13:57:54 | shard end done=1 rc=0
2026-10-18 13:57:54 | shard progress source=18344 classified=2013 ok=2010 degraded=3 pending=16331
2026-10-18 13:57:54 | sprint end done=1 yielded=0
2026-10-18 13:57:54 | sprint final source=18344 classified=2013 ok=2010 degraded=3 pending=16331
```

注：WorkBuddy automation 追平轮本次未活跃（状态日志 13:16 后无新写入），让路语义未实战触发；但其逻辑在 13:11-13:16 的锁测试段已验证（lock busy/stale take over 行为正常）。

## 二、hard=1 遗留行诊断

### 结论：**已清零，无需处置**

- `recontent_v13.out` 记录：`2026-10-18 12:32:07 | RE-HARD-FAIL 141757269: timed out`（v1.3 重补标三批后唯一 hard 行）
- 失败原因：**LLM 超时**（`urllib.request.urlopen(req, timeout=TIMEOUT_SEC=60)`），非内容异常、非 JSON 解析失败
- 该行特征：status_id=141757269，title=`#2020择基而动#汽车行业的变革正在悄悄进行`，desc 791 字符，含大量 HTML 标签（`<a>`/`<img>` emoji），文本较长——推测与 12:13 左右模型负载高共同导致 60s 超时
- **补标成功证据**：`xq_status_classified` 中该行 `parse_ok=1, labeled_at=2026-10-18 12:37:05, prompt_version=v1.2-re, model=qwen3.5:9b`（闲聊杂谈/neutral/ad conf=0.95），由 12:36:46 启动的 r2 recontent 批次完成
- 当前 degraded=3 行均为历史 parse_ok=0（unparseable JSON 截断类），非 hard 遗留，属既有 DEGRADED 语义，不影响追平

## 三、全量启动建议

### 当前存量（13:57 实测）

source=18,344 / classified=2,013 / pending=16,331

### 速度实测与外推

- 首跑单条 24.3s——**疑似含模型冷加载**（qwen3.5:9b 自 12:14 后未被批量调用，2 天前下载）
- 历史批量实测（12:13-12:14 状态日志）：单条稳定 4.4-5.2s，即 ~12 条/分钟
- 窗口剩余（假设 14:10 启动至 17:30）：200 分钟
  - 热态 5s/条：可完成 ~2,400 条
  - 若持续 24s/条（冷态不恢复）：仅 ~500 条，但 runner 会在 17:30 触发 `window ended mid-batch` 安全停机，不会超窗口

### 建议参数

```
python tools/backfill_sprint.py --limit 2000
```

- **不加 --force**：启用真实 13:30-17:30 窗口保护（批中检查 + 到点安全停）
- `--limit 2000`（缺省值即可）：热态下 2000 条约 2.8-3 小时，恰好收在窗口内；即使速度掉到 10s/条也只完成一半，剩余留给下一窗口/自动化轮，分片 500 + NOT IN 幂等可无损续跑
- 启动前用 `curl http://127.0.0.1:11434/api/generate -d '{"model":"qwen3.5:9b","keep_alive":-1}'`（或先小批 10 条）预热模型，可规避首条冷加载拖慢

### 风险与未决问题

1. **automation 撞写**：HOURLY 追平轮若整点触发，runner 让路语义会等待 60s×3 后 rc=4 退出——属预期行为，重跑即可，无数据风险
2. **速度未二次确认**：本次仅 1 条样本，建议全量启动后观察 `shard progress` 时间戳校准外推（第一个 shard=500 完成时刻即可算出真实速率）
3. degraded 3 行是否需要 v1.3 重补标轮处理，建议追平完成后由主会话决定

## 四、边界遵守声明

- 未修改任何源码；未做 git 操作；DB 仅 `mode=ro` 只读连接
- 仅新建本报告文件；锁文件已确认释放且无残留
