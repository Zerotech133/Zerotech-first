# guard 阈值敏感性研究（2026-11-01）

> 数据：`C:\项目\老田的AI助手\research\reports\kb_qa_guard_20261101.json`（12 条原 multi 答案）· 网格：inference_ratio ∈ [0.5, 0.6, 0.7, 0.8, 0.9] × min cited units ∈ [2, 3] · 只读模拟、不调 LLM、不改 guard 代码。

## 推荐默认（参数建议）

**inference_ratio 阈值 = 0.7，最小被引单元 = 2**

- 推荐阈值通过率与默认相同（50%），但更宽松（inference≤0.7/units≥2），对未见数据更稳健。
- 代理质量：引用可解析率 1.0、支撑率 1.0、幻觉率 0.0、推断密度 0.4375。
- 最小被引单元维持 2 个：提高单元数并未带来质量增益，反而增加 fallback。

当前默认（inference=0.6 / units=2）指标：
- 通过率 50%（6/12）、被拦截 6 条、可答率（保留） 50%。

## 阈值 × 通过率矩阵

| inference_ratio 阈值 | min units | 通过率 | 通过/拦截 | 拦截原因分布 | 可答率(保留) | 引用可解析率 | 支撑率 | 幻觉率 | 推断密度 |
|---|---|---|---|---|---|---|---|---|---|
| 0.5 | 2 | 50% | 6/6 | insufficient_cited_units:6；citation_missing:4；inference_heavy:4 | 50% | 100% | 100% | 0% | 0.4375 |
| 0.5 | 3 | 42% | 5/7 | insufficient_cited_units:7；citation_missing:4；inference_heavy:4 | 42% | 100% | 100% | 0% | 0.4375 |
| 0.6 | 2 | 50% | 6/6 | insufficient_cited_units:6；citation_missing:4；inference_heavy:4 | 50% | 100% | 100% | 0% | 0.4375 |
| 0.6 | 3 | 42% | 5/7 | insufficient_cited_units:7；citation_missing:4；inference_heavy:4 | 42% | 100% | 100% | 0% | 0.4375 |
| 0.7 | 2 | 50% | 6/6 | insufficient_cited_units:6；citation_missing:4；inference_heavy:1 | 50% | 100% | 100% | 0% | 0.4375 |
| 0.7 | 3 | 42% | 5/7 | insufficient_cited_units:7；citation_missing:4；inference_heavy:1 | 42% | 100% | 100% | 0% | 0.4375 |
| 0.8 | 2 | 50% | 6/6 | insufficient_cited_units:6；citation_missing:4；inference_heavy:1 | 50% | 100% | 100% | 0% | 0.4375 |
| 0.8 | 3 | 42% | 5/7 | insufficient_cited_units:7；citation_missing:4；inference_heavy:1 | 42% | 100% | 100% | 0% | 0.4375 |
| 0.9 | 2 | 50% | 6/6 | insufficient_cited_units:6；citation_missing:4；inference_heavy:1 | 50% | 100% | 100% | 0% | 0.4375 |
| 0.9 | 3 | 42% | 5/7 | insufficient_cited_units:7；citation_missing:4；inference_heavy:1 | 42% | 100% | 100% | 0% | 0.4375 |

## 备选阈值（权衡）

- inference=0.5 / units=2：通过率 50%（拦截 6 条），可解析率 1.0 / 推断密度 0.4375
- inference=0.5 / units=3：通过率 42%（拦截 7 条），可解析率 1.0 / 推断密度 0.4375
- inference=0.6 / units=2：通过率 50%（拦截 6 条），可解析率 1.0 / 推断密度 0.4375
- inference=0.6 / units=3：通过率 42%（拦截 7 条），可解析率 1.0 / 推断密度 0.4375

## 风险与后续

- 样本量 n=12 偏小，阈值差异可能来自样本偶然性；建议扩样到 30–50 条复核。
- inference_ratio 依赖模型输出【直接事实】/【推断】标记；未标注时比率为 0，可能低估计。
- 被拦截行的代理质量使用 guard fallback 后指标（fallback 仅保留可解析引用），属于保守估计。
- 数据集来自 wave15 v3 前 12 条（偏难答），不代表全量 50 条问题分布。
- 仅参数建议，不修改 guard 代码；实际切换需通过 CLI 参数/配置灰度并复跑评估。

## 结论

- 建议默认 inference_ratio 阈值=0.7、最小被引单元=2（不修改 guard 代码，仅调参）
- 阈值研究仅供研究参考，不构成投资建议；最终默认值需扩样复核后决定。
- 说明：只读模拟，不调 LLM、不修改 kb_qa_guard.py；被拦截行质量用 fallback 后指标代理
