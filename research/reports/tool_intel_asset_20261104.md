# 工具情报库资产化报告（W3 · 2026-11-04）

> 任务：task-161 · 把第二期「工具情报库 45 个工具」落成可消费资产（契约先行）
> 约束：不调外部模型、不联网、不 commit；**不改 nav/前端页面**（无前端门影响）

## 一、资产清单

| 文件 | 说明 | 大小/行数 |
|---|---|---|
| `agent_platform/registry/tool_intel.json` | 工具情报 registry（schema_version=`tool_intel.v1`）：45 工具（行情12/Agent17/量化7/回测4/研报3/绘图2；置信 高16/中29），含名称/类别/别名/描述/能力/地址/来源文件+行号/来源数/提及数/units 提及/置信 + 分类汇总 + 可信度政策 | 43,277B |
| `portal/api_learning_tool_intel.py` | 只读 API 独立模块（逻辑全在此，portal_api 仅 4 行早分支分发） | 99 行 |
| `portal/portal_api.py` | 仅新增 5 行路由早分支（`if path == '/api/learning/tool-intel'`），仍 3,706 行（≤5,200 预算） | +5 行 |
| `tools/portal_route_baseline.json` | 路由基线重写：**298 条**，含 `["GET","exact","/api/learning/tool-intel"]`；`--check` 零 diff | 298 routes |
| `tools/tests/test_api_learning_tool_intel.py` | 5 项测试（不依赖 8091 在线） | 5 passed |
| 本报告 | 文件/接口/契约/消费方式/验收 | — |

数据转换：`research/reports/tool_intel_20261103.json`（P1 工具情报库，只读）→ registry，保留全部工具字段并补 `schema_version/source.sha256/by_category/by_confidence/categories`。

## 二、API 契约（只读）

### 2.1 路径

```
GET /api/learning/tool-intel
```

> 8091 服务需**重启后生效**（路由早分支新增；本任务未重启服务——服务重启属 L2 操作，需 owner 批准）。静态降级：registry JSON 本身可直接读。

### 2.2 查询参数

| 参数 | 取值 | 默认 | 说明 |
|---|---|---|---|
| `category` | `行情|研报|量化|回测|绘图|Agent` | 全部 | 按类别过滤 |
| `confidence` | `高|中|低` | 全部 | 按证据强度过滤 |
| `q` | 关键词 | — | 匹配名称/描述/能力/别名/地址（不区分大小写） |
| `limit` | `1..100` | `100` | 返回条数 |
| `offset` | `≥0` | `0` | 分页偏移 |

### 2.3 响应字段（200）

```json
{
  "ok": true,
  "count": 45,
  "total": 45,
  "items": [
    {"name": "TradingView", "category": "行情", "confidence": "高",
     "description": "...", "capabilities": ["行情图表", "指标", "Pine脚本"],
     "address": "tradingview.com", "source_files": ["20261103_爱吃折耳根的Ace.md"],
     "source_lines": [{"file": "...", "line": 535}],
     "sources_count": 2, "mentions": 42, "units_mentions": 12,
     "aliases": ["TV", "TradingView 图表"]}
  ],
  "by_category": {"行情": 12, "Agent": 17, "量化": 7, "回测": 4, "研报": 3, "绘图": 2},
  "by_confidence": {"高": 16, "中": 29},
  "query": {"category": null, "confidence": null, "q": null, "limit": 100, "offset": 0},
  "schema_version": "tool_intel.v1",
  "generated_at": "2026-11-03T...",
  "source": {"path": "research/reports/tool_intel_20261103.json", "sha256": "...", "task": "..."}
}
```

### 2.4 错误码

| HTTP | code | 场景 |
|---|---|---|
| 400 | `INVALID_ARGUMENT` | category/confidence 非枚举；limit 非 1..100；offset <0/非法整数 |
| 503 | `NOT_READY` | `agent_platform/registry/tool_intel.json` 缺失（未资产化/被清理） |
| 500 | `INTERNAL` | registry 读取/序列化异常（兜底，不外泄堆栈） |

## 三、消费方式

```bash
# 全量
curl http://127.0.0.1:8734/api/learning/tool-intel
# 过滤 + 分页
curl "http://127.0.0.1:8734/api/learning/tool-intel?category=行情&limit=20"
curl "http://127.0.0.1:8734/api/learning/tool-intel?confidence=高&q=TradingView"
# 静态降级（无需 API）
curl http://127.0.0.1:8734/static/...  # 或直接读 registry JSON
```

Python：

```python
import json, urllib.request
data = json.load(urllib.request.urlopen(
    "http://127.0.0.1:8734/api/learning/tool-intel?category=量化&limit=10"))
for t in data["items"]:
    print(t["name"], t["category"], t["confidence"], t["address"])
```

军团消费建议：量化/数据/研发军团按 `category`（量化/回测/行情/研报）取工具做选型调研；`confidence=高` 优先，中置信需人工复核（registry 内已注记）；Agent 类工具仅参考，不替代审核流程。

## 四、验证（本期）

- [x] registry：45 工具、字段完整、类别/置信枚举合法、分类汇总合计 45
- [x] API 逻辑（mock）：200/字段/分类汇总/过滤（category/confidence/q/limit/offset）/400 非法参数/503 缺失 → **5 tests passed**
- [x] 路由：portal_api.py 早分支 5 行、总行数 3,706（≤5,200）
- [x] 路由基线：`--write-baseline` → 298 条（含 tool-intel）；`--check` **零 diff PASS**
- [x] 未改 nav/前端页面；不调模型、不联网；未 commit
- [ ] 8091 重启后生效（待 owner 批准；本任务不重启服务）
- [ ] 如需前端展示，另立任务（契约先行，本任务不触碰前端门）

## 五、回退

- 代码：`git checkout -- portal/portal_api.py portal/api_learning_tool_intel.py tools/portal_route_baseline.json`（按仓库纪律审批）；
- 资产：删除 `agent_platform/registry/tool_intel.json` 即 API 返回 503（其余功能不受影响）；
- 路由基线：回退后重跑 `python tools/portal_route_inventory.py --write-baseline` 再 `--check`。

> 工具情报为原文结构化整理，不构成工具推荐或投资建议。
