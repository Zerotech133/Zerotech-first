# P0-A /api/jev 13 条入口摘除回执

- 窗口：2026-11-07T23:58:00+08:00 — 2026-11-08T00:08:00+08:00
- 开始/结束：2026-11-08T00:00:42+08:00 / 2026-11-08T00:01:48+08:00
- 耗时：65.8s；applied=True；restored=True；aborted=False
- nginx before/after：`5f80ffa665d06b474e9acac651312dbcb0e53ba8e7d8f35fd33d4a879c1378ac` / `5f80ffa665d06b474e9acac651312dbcb0e53ba8e7d8f35fd33d4a879c1378ac`
- 数据哈希不变：True
- 探针：HEAD；未向业务发送 POST；POST 路由仅验证 ingress 返回 503。
- 边界：这是 8734 ingress 入口摘除演练，不是进程级故障演练。

| # | 路由 | 声明方法 | before | during | after | 503 | 恢复基线 |
|---:|---|---|---:|---:|---:|---|---|
| 1 | `/api/jev/` | GET | 501 | 503 | 501 | PASS | PASS |
| 2 | `/api/jev/capabilities` | GET | 501 | 503 | 501 | PASS | PASS |
| 3 | `/api/jev/evidence` | GET | 501 | 503 | 501 | PASS | PASS |
| 4 | `/api/jev/evidence/item` | GET | 501 | 503 | 501 | PASS | PASS |
| 5 | `/api/jev/interpret` | GET | 501 | 503 | 501 | PASS | PASS |
| 6 | `/api/jev/intraday/` | GET | 501 | 503 | 501 | PASS | PASS |
| 7 | `/api/jev/platform` | GET | 501 | 503 | 501 | PASS | PASS |
| 8 | `/api/jev/review/` | GET | 401 | 503 | 401 | PASS | PASS |
| 9 | `/api/jev/story/` | GET | 501 | 503 | 501 | PASS | PASS |
| 10 | `/api/jev/target/` | GET | 501 | 503 | 501 | PASS | PASS |
| 11 | `/api/jev/trace` | GET | 501 | 503 | 501 | PASS | PASS |
| 12 | `/api/jev/wordcloud` | GET | 501 | 503 | 501 | PASS | PASS |
| 13 | `/api/jev/review/` | POST | 401 | 503 | 401 | PASS | PASS |

## 基线说明（重要）

- 本次演练统一使用 **HEAD 探针**，避免向业务发送 POST；上游 JEV GET 路由未实现 HEAD，故 HEAD 基线多为 **501**，`/api/jev/review/` 为 **401**。这不代表 JEV 业务不可用。
- 恢复后补充 **GET 基线**（只读、未发 POST）：`/api/jev/`、`/capabilities`、`/platform`、`/trace` 为 **200**；`/evidence`、`/evidence/item`、`/interpret`、`/intraday/`、`/story/`、`/target/` 为 **400 参数缺失**；`/review/` 为 **401**；`/wordcloud` 本次请求超时。401/400 属于业务鉴权/参数行为，不是 ingress 故障。
- 因此结论是：**8734 ingress 503 摘除与恢复 PASS；HEAD 探针不能用于判定 JEV 业务健康，业务验收需用 GET 基线另表记录。**
- `/api/jev-stack` 为独立路由，未被本规则命中，HEAD 前后均为 501。

### 恢复后 GET 基线（补充只读）

| 路由 | GET 状态 |
|---|---:|
| `/api/jev/` | 200 |
| `/api/jev/capabilities` | 200 |
| `/api/jev/platform` | 200 |
| `/api/jev/trace` | 200 |
| `/api/jev/evidence` | 400 |
| `/api/jev/evidence/item` | 400 |
| `/api/jev/interpret` | 400 |
| `/api/jev/intraday/` | 400 |
| `/api/jev/story/` | 400 |
| `/api/jev/target/` | 400 |
| `/api/jev/review/` | 401 |
| `/api/jev/wordcloud` | 200（复测） |

## 其他检查

- `/api/health` before/during/after：200 / 200 / 200
- `/api/d1/decisions`：200 / 200 / 200
- 8091 直连 `/api/jev/capabilities`：200 / 200 / 200
- `/api/jev-stack`（不应被规则命中）：501 / 501 / 501

## 证据

- `baseline_before.json`
- `drill_result.json`
- `timeline.log`
- `post_drill_get_matrix.json`
- `nginx.conf.before`
