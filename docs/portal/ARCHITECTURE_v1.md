# portal 架构说明 v1（2026-10-28 · 关羽）

> portal 此前**仅 1 篇业务文档**（briefs）；本文补"代码架构/入口/文件地图/门禁"，作为新同学与新 Agent 的入口。

## 一、进程与入口

```
人类入口（唯一）：http://127.0.0.1:8734   （nginx 同源反代，K-STOCK-113 立宪）
        ↓
portal_api.py（ThreadingHTTPServer，8091，约 4,600 行）—— 路由 + 聚合，只改不扩（预算 5,200）
        ↓
portal/api_*.py（40+ 模块）—— 领域 handler
portal/*.py（静态页服务/调度/推送/飞书/盯盘等工具）
```

- 启动：`portal/start_portal_api.ps1`（内置顶层 import 冒烟预检；断链拒绝拉起）；
- **改代码 ≠ 生效**：8091 是常驻服务，必须重启换血并验证新进程出生时间 > 文件 mtime；
- 运行态数据：`portal/data/`（dev_events.jsonl / 审批 / 缓存）；备份不在此目录。

## 二、API 文件地图（行数 = 2026-10-28 实测）

| 文件 | 行数 | 职责 |
|---|---:|---|
| `api_backtest.py` | **6,135** | 回测编排（巨型，冻结待拆） |
| `api_kg.py` | **5,841** | 图谱查询/关系（巨型，冻结待拆） |
| `api_misc.py` | 2,710 | 杂项聚合（冻结待拆） |
| `api_chanlun_pages.py` | 2,328 | 缠论页面数据（冻结待拆） |
| `api_portfolio.py` | 2,322 | 组合/持仓（冻结待拆） |
| `api_common.py` | 1,577 | 公共鉴权/工具（导航数据源 nav.js 由此派生） |
| `api_screen.py` | 1,336 | 选股屏 |
| `api_bridge.py` / `api_events.py` / `api_news_intel.py` / `api_notify.py` / … | <1,000 | 领域接口 |

**拆分守则**：`docs/plans/2026-10-28_portal大文件拆分守则_v1.md`；守卫 `tools/check_portal_split.py`。
**硬规则**：新 handler 必须落新文件；`portal_api.py` 只做 import + 注册；冻结文件只减不增。

## 三、新增一个接口的标准流程

1. 在 `portal/api_<domain>.py`（新文件 ≤1,200 行）实现 handler；
2. 在 `portal_api.py` **只加 import + 路由注册**（禁止写业务逻辑）；
3. 前端页面遵循三件套：`tokens.css` + nav.js（动态页自动/静态页 `tools/unify_nav.py` 注入）+ 短屏化；
4. 单测放 `portal/tests/test_api_<domain>.py`；
5. `python -m pytest portal/tests -q`（291 基线）+ `python tools/check_portal_split.py`；
6. 合并走 `devtask`；改常驻代码后需按重启流程换血。

## 四、测试与门禁

- 测试：`python -m pytest portal/tests -q --timeout=120` → 基线 **291 passed / 1 xfailed**；
- 门禁：Mixin 方法集 / 导航统一 / 端口注册 / 明文凭据 / L2 / gitleaks（merge 自动跑）；
- 规模守卫：`tools/check_portal_split.py`（建议并入 `tools/test_gate.ps1 -Suite portal`）。

## 五、已知债务（详见全代码审计报告）

- 5 个巨型 API 文件（合计 ~19k 行）→ 按守则拆分；
- portal 文档长期缺失（本篇为首个架构说明）；
- `portal_api.py` 4,596/5,200（88% 预算），只改不扩。

## 六、相关文档

- 拆分守则：`docs/plans/2026-10-28_portal大文件拆分守则_v1.md`
- 全代码审计：`deliverables/2026-10-28_老田AI助手_全代码审计报告_v1.md`
- 测试基线：`deliverables/2026-10-27_测试基线_v1.md`

## api_kg 拆分收尾（2026-10-30 P57）

- `portal/api_kg.py` 5,913 → **97 行**：只保留 Handler 委托 + 顶层 helper；
- 实体逻辑与内嵌 JS 常量分居 `portal/kg/*`（kg_html/kg_pages_{misc,events,canvas}/kg_service/kg_js）；
- 依赖单向：`portal/kg/*` 不得 import `api_kg`（契约测试守卫）；
- 行数守卫：api_kg ≤ 300 行，只减不增。
