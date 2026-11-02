# HQ 控制面 Q2/SLO 门 v1（W3 前置）

- 日期：2026-11-03｜范围：`/hq` 控制面 + 6 军团 Hermes 接口
- 结论：**HQ 控制面 Q2 PASS；6 军团 Q2 全量尚未完成（1 PASS + 1 条件 + 4 Q1），W3 目标补齐。**
- 证据工具：`tools/hq_q2_probe.py`、`tools/composition.py validate`、`tools/portal_route_inventory.py`

## 一、SLO 定义（HQ 控制面）

| 指标 | 目标 | 门禁 |
|---|---|---|
| 可用性 | ≥99.5% | `/health`、9 API、`/hq` 全部 200 |
| API p50 | <300ms | 探针统计 |
| API p95 | **<800ms** | `hq_q2_probe`（snapshot 聚合读单列） |
| 页面 `/hq` | 200，<1500ms | 探针 |
| 未登录 `/api/hq/snapshot` | **401** | 鉴权不绕过 |
| 回滚 | 旧 8091/nginx 配置可回退 | 进程/配置回滚 |
| 测试 | ≥1 回归 | HQ 14 tests |

## 二、实测（2026-11-03 05:25）

| 项 | 实测 |
|---|---|
| 检查项 | **12/12 PASS** |
| API p50 | **61.9ms** |
| API p95 | **606.9ms**（<800ms） |
| `/hq` | 200 |
| 未登录 `/api/hq/snapshot` | 401 |
| L1 | 18/18 PASS |
| HQ tests | 14 passed |
| 冷启动 | 首拍 snapshot 696ms，warmup 后计入稳态 |

**命令**：`python -X utf8 tools/hq_q2_probe.py` → PASS。

## 三、6 军团 Q2 矩阵（诚实口径）

| 军团 | Q2 状态 | 阻塞/证据 |
|---|---|---|
| acad1-legion | ✅ PASS | 2026-11-02 Q2 评审 PASS |
| data-warehouse | 🟡 条件通过 | API `/api/dw/stats` 200；页面 `/dw`/`/dataops` 404（W3 WF-1） |
| huangzhong-acceptance | ⚪ Q1 | 11-04 验收未执行 |
| lusu-d1-flow | ⚪ Q1 | Q2 证据未齐 |
| quant-domain | ⚪ Q1 | L1 已签，Q2 未评审 |
| quant-strategy-line | ⚪ Q1 | Q2 证据未齐 |

> 不虚报“全量 Q2”：当前 1 PASS + 1 条件 + 4 Q1；W3 出口目标 Q2≥2、页面 104/104、契约 strict。

## 四、Q2 门禁规则（对每个军团）

1. Q1 四件套齐全；
2. 引用全部可解析（validate/plan 0）；
3. 健康探针 + SLO（本文件口径）通过；
4. 回滚预案 + 至少 1 条测试；
5. 页面/任务/契约在册；
6. 未达标不得标 active（C 级以上）。

## 五、证据

- `tools/hq_q2_probe.py`（12/12 PASS，p50 61.9ms/p95 606.9ms）
- `tools/composition.py validate --all`（6 组合 errors=0 warnings=0）
- `tools/portal_route_inventory.py --check`（291 条零 diff）
- L1 18/18；HQ 测试 14 passed；W3 wiring 6 passed
