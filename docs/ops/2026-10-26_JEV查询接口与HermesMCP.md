# JEV 只读查询接口 + Hermes MCP 工具（2026-10-26）

> 工单：K-ORG-20261026-JEVAPI / JEVAPI2 / JEVDOC
> 目的：让 Hermes（及任何 MCP 客户端）按需读取 JEV 判定，回答「今天 JEV 抓到哪些利空/利多、哪些有后验命中、判断准不准」。

## 一、HTTP 只读接口（8091）

```
GET /api/jev/query
  hours=24              # 时间窗（1-720 小时，按「采集时间 created_at」计）
  bias=bull,bear        # 可选：bull/bear/neutral
  code=600519           # 可选：6 位 A 股代码（匹配 stocks/companies）
  event_type=policy     # 可选：policy/earnings/price/equity/supply/tech/risk/other
  with_posterior=1      # 可选：联查 T+1 后验（ret/bench/excess/hit）
  limit=50              # 可选：1-200
```

返回：`ok / window_hours / filters / summary_window{total,bull,bear,neutral} / total / returned / posterior_band_pct / items[]`
- `items[]`：`id, ts(发布), collected_at(采集), source, title, bias, conf, event_type, impact, companies, codes, reason, posterior{...}`
- 只读铁律：`news.db` / `jev_audit.db` 均 `mode=ro`；`summary_window` 是全窗口统计（不随 limit 截断）。
- 参数错误 400、news.db 缺失 503、内部错误 500（均 JSON）。

示例：
```bash
curl "http://127.0.0.1:8091/api/jev/query?hours=24&bias=bear&limit=5"
curl "http://127.0.0.1:8091/api/jev/query?hours=168&code=600519&with_posterior=1"
```

## 二、Hermes MCP 工具（jev-news server）

配置文件 `~/.hermes/config.yaml` 的 `mcp_servers` 已注册：
```yaml
  jev-news:
    command: "C:\\Program Files\\Python311\\python.exe"
    args:
      - "C:\\aios\\tools\\jev_mcp.py"
```
工具：
| 工具 | 说明 |
|---|---|
| `jev_query` | 查询判定（hours/bias/code/event_type/with_posterior/limit），返回中文摘要+条目 |
| `jev_calibration` | 校准状态（方向准确率 vs 全判 bull 基线、neutral 带宽、bear/conf 可用性） |

**Hermes 小模型的调用方式（重要）**：Hermes 把 MCP 工具全部归入 *deferred*（设计如此），
不能直接 `tool_call`；正确两步：
1. `tool_search`（参数 `queries=["jev"]`）→ 拿到 `jev_calibration` / `jev_query` 的 schema；
2. `tool_call`（name=`jev_calibration` 或 `jev_query`，arguments=…）→ 拿到结果。
（9B 实测：tool_search + tool_describe 可用；直接 tool_call 会报
`is not a known tool name. Deferred tools must be invoked through tool_call ...`）

## 三、验证证据（2026-10-26 21:09-21:25）

- 8091 接口：24h 窗口 1,900+ 条（多/空/中性齐全），`collected_at` 字段正常，参数矩阵/400 均验证。
- Hermes：`mcp-stderr.log` 出现 `===== starting MCP server 'jev-news' =====` + `[jev-mcp] start ...`；
  真实会话日志出现 `tool tool_search completed` / `tool_describe completed`（jev_calibration）。
- 工具数据面直测（stdio 协议）：
  - `jev_calibration` → 「方向类 n=1997，准确率 55.9%，基线 55.7%，增量 0.2pp；neutral 带宽 2.5% 命中 70%；bull/bear 均不可用」
  - `jev_query` → 24h 1,921 条（多 704/空 290/中性 927），返回利空条目含 conf/事件类型

## 四、运维坑（本次踩过，勿重犯）

1. **8091 旧进程可能属于其他会话/S4U**，普通 `taskkill` 报 Access denied；需提权 kill 后由官方 launcher 冷启动。
2. **不要杀提权 job 的父进程链**：`job_kill` 会连累 elevated launcher 的子进程（8091 曾被连带杀掉）；提权脚本用 `Start-Process -Verb RunAs` 后**不 -Wait**，改轮询端口。
3. **`PORTAL_API_RESTART.flag` 会被 5 分钟守护 launcher 抢先消费**，手工重启时勿设 flag（直接 kill→launcher）；flag 只留给守护脚本。
4. 8091 冷启动自带 import 冒烟预检（K-ORG-155），断链会拒绝拉起——这是保护，不要绕过。
5. 查询窗口用 **created_at（采集时间）**：news.ts 常是发布日（如次日 00:00 公告），按 ts 会漏/混入。
6. Hermes 重启：`Hermes/restart_hermes_8787.ps1` 需管理员（8787 属 SYSTEM 会话）；重启后 MCP 在**新 agent 会话**启动。

## 九、日常话术模板（Hermes 两步调用）

对 Hermes 说「查 JEV 今天有哪些利空」，模型按两步执行即可：

1. `tool_search`：`{"queries": ["jev"]}` → 拿到 `jev_query` / `jev_calibration` 的 schema；
2. `tool_call`：`{"name": "jev_query", "arguments": {"hours": 24, "bias": "bear", "limit": 10}}`
   或 `{"name": "jev_calibration", "arguments": {}}`。

注意：Hermes 的 MCP 工具默认 deferred，直接 `tool_call` 会报 unknown tool name；
若模型先说"未找到工具"，让它先执行第 1 步 tool_search 再重试。

## 十、2026-10-30 增强：`jev_digest` / `jev_risk`（Hermes 优先用这两个）

为减少 Hermes 小模型的多步调用，新增两个一站式工具（同一 MCP server，无需改 Hermes 配置）：

| 工具 | 参数 | 返回 |
|---|---|---|
| `jev_digest` | `hours`(1-720, 默认24)、`focus`(all/risk/bull/bear)、`top`(1-20, 默认8) | 多空统计 + Top 重点条目（含后验/周期）+ 校准状态 + 数据质量告警（低信号事件/数据落后） |
| `jev_risk` | `hours`(默认24)、`top`(默认10) | 近N小时高影响利空 Top N（含 T+1 后验） |

Hermes 话术：
1. `tool_search`：`{"queries": ["jev"]}`（deferred 工具必须先搜）
2. `tool_call`：`{"name": "jev_digest", "arguments": {"hours": 24, "focus": "risk", "top": 8}}`
   → 一句摘要：今天风险事件 + JEV 当前可信度（校准/数据质量）
3. 追问个股：`jev_query {"code": "600519", "hours": 168, "with_posterior": true}`

边界：仍为只读；不写库、不推送；`jev_digest` 会显式披露数据落后与低信号事件，避免把"低信号"当方向。

## 十一、2026-10-30 新增：`jev_feedback` + Hermes 默认工作流 + 风险告警桥

### 11.1 `jev_feedback`（写入反馈集，不直接改判定）
参数：`news_id`（必填）、`note`（1-500 字，必填）、`suggested_bias`（可选 bull/bear/neutral）、`labeler`（默认 hermes）。
落点：`agent_platform/data/jev_audit/review_feedback.jsonl`（append-only，同 news_id+说明幂等跳过）。
人工复核后由 `tools/jev_review_loop.py` 回灌为评测标注。

### 11.2 Hermes 默认工作流（提示词/能力卡建议）
- **盘前**：`jev_digest {"hours": 12, "focus": "risk", "top": 8}` → 风险清单 + JEV 可信度；
- **盘中**：`jev_risk {"hours": 2, "top": 5}` → 突发利空；追问个股 `jev_query {"code": "...", "hours": 168, "with_posterior": true}`；
- **盘后复盘**：`jev_calibration {}` 看校准/数据落后；对可疑条目 `jev_feedback` 留痕；
- 纪律：JEV 输出必须同时引用「校准/低信号/数据落后」信息，不得把低信号事件当方向信号。

### 11.3 风险告警桥（工具就绪，自动推送待批准）
`tools/jev_risk_alert.py`：扫描近 N 小时 bias=bear、impact=high、conf≥0.8（排除低信号），
去重后写 `risk_alerts.jsonl`；`--emit-flag` 可写 `jev-eval/alerts/JEV_RISK_<ts>.flag`
由现有 `alert_forward.ps1` 转发飞书。默认 dry-run，不注册任务、不主动外发。
上线自动调度（建议每小时，且仅交易时段）需老田 L2 批准。

**2026-10-30 21:30 更新（用户批准方案 B）**：已挂入 `JevBackfill` 小时轮 wrapper 尾部：
每轮判读后执行 `jev_risk_alert.py --hours 2 --emit-outbox`（仅写 `risk_alerts.jsonl`，**不外发**飞书/Hermes 推送）；日志特征 `P2.5 risk outbox exit=0`。

## 十二、2026-10-30 增量事件流（`since` / `cursor`，2026-10-31 重启后生效）

`GET /api/jev/query?since=2026-10-30%2010:00:00&limit=200`：
- `since`：增量游标（`YYYY-MM-DD HH:MM:SS` 或 ISO8601）；提供时按 `created_at >= since` 升序返回；
- 响应新增：`since`、`cursor`（本页最大 collected_at）、`cursor_mode`；
- 翻页：下一次把 `cursor` 作为 `since` 传入；边界同秒条目会重复，按 `id` 去重即可（`created_at_inclusive`）；
- 兼容：不传 `since` 时行为与旧版完全一致（按 `hours` 时间窗）。
MCP `jev_query` 已支持 `since` 参数透传。

