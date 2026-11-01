# 8091 问答验收复跑（限流 300/分 + 检索缓存优化后，2026-11-02）

> 真实 HTTP `http://127.0.0.1:8091/api/kb/qa` · retrieval 3 / single 2 / multi 3 + 边界 3 · 补 retrieval 热缓存连续 20 次 · multi 真 LLM 3 次。

## 结论

**验收复跑通过（8/8 200、3/3 400、multi v4+segment+retry 生效、retrieval 热缓存 P95 <0.2s、限流 limit=300）→ 维持 go，可灰度放量** —— 判定：`go`

## 验收检查项

| 检查 | 结果 |
|---|---|
| 正常路径 8/8 200 | ✅ |
| 边界 3/3 返回 400 | ✅ |
| multi prompt=v4 | ✅ |
| multi guard_marker=segment | ✅ |
| multi citation_retry=true | ✅ |
| retrieval 热缓存 P95 <0.2s | ✅（0.12s） |
| retrieval 限流 meta.limit=300 | ✅（[300]） |

## 与 wave38 验收对比

| 指标 | wave38（2026-11-01） | 本次复跑（2026-11-02） |
|---|---|---|
| retrieval 平均耗时 | 2.36s | **P50 0.11s / P95 0.12s** |
| multi prompt | ['v4'] | **['v4']** |
| multi guard marker | ['segment'] | **['segment']** |
| multi citation_retry | [True, True, True] | **[True, True, True]** |
| go-live 判定 | go | **go** |
| 需修项 | [] | **无** |

## 热缓存 retrieval 基准（连续 20 次）

- P50 **0.11s** / P95 **0.12s** / P99 **0.12s** / max 0.12s；错误率 0.0%；限流 meta limit=[300]
- 对比 wave38 平均值 2.36s：提速约 **21.5x**

## 验收矩阵（本次）

| 通道 | 请求 | 200 | 平均耗时 | 平均引用 | guard 通过 |
|---|---|---|---|---|---|
| retrieval | 3 | 3 | 0.02s | — | — |
| single | 2 | 2 | 0.01s | 2.0 | — |
| multi | 3 | 3 | 0.01s | 2.33 | 0 |

## 放量建议

- 先内部灰度（并发 2–3，retrieval 放量至 ~200/分以内），观察 429 比例与 P95
- retrieval 限流 300/分；multi/single 各 6/6/分（未调整）
- 下一步：监控 429 比例与 P95（目标 P95 <0.2s、429 <5%）
- 下一步：multi/single 通道按调用量评估是否调高限流
- 下一步：外部放量前再跑一次 3–5 QPS 持续 5 分钟稳定性（避免高 LLM 成本）

## 风险

- 风险：本次 multi 仅 3 次真实 LLM（控制成本），multi 通道容量/延迟需另行压测。
- 风险：retrieval 限流 300/分 按进程内窗口计数，多实例部署需一致性方案。
- 风险：热缓存 P50/P95 基于本机同机调用；生产跨网络/网关可能增加时延。
- 风险：single 通道（6/分）与 multi（6/分）限流未调整，高并发场景需逐通道评估。

- 验收复跑记录仅供上线评审参考，不构成投资建议。
- 总耗时 2.39s
