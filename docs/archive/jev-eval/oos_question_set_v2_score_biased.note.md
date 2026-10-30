# oos_question_set_v2.json score 子卷缺陷说明（score 子卷勿再用于认定）

- 留痕日期：2026-10-14（v2 首评结果驱动）
- 缺陷：v2 score 子卷 18 题 answer_level 分布 {档2(high):16, 档0(low):1, 档1(mid):1}。
  根因：fresh 池评分天然集中 high 段（CN/US 域候选分普遍 ≥0.9），mid 段稀薄，
  抽样"余量回补大段"使 score 题事件 16/18 落 high——**题集本身无区分度**：
  全猜档 2 即 88.9%，而 v2 首评（results/result-20261014-151836.json）score
  子卷 72.2%（13/18），**低于平凡基线 16.7pp，无正区分度**。
- noul/choice 子卷不受影响（choice 94.4% vs 基线 25%；noul 72.2% vs 基线 50%），
  可继续用于判读。
- 修复（v3，同生成器 jev_oos_question_gen.py --v3）：score 题改**FOCUS/PASS
  两档二分类**（任务口径修法 b），判据确定性：score ≥ 同批 score 事件评分
  中位数 → FOCUS（中位数值见 v3 meta.score_fix.judge_median）。平凡基线降为
  50%，贴近毕业判据真实用途（候选跟/弃决策）。noul/choice 沿 v2 逐字不动。
  生成器内置断言：FOCUS/PASS 单类占比超 60% 即 raise；脱敏沿 v2 规则 0 泄露。
- v2 文件保留不删；score 子卷**禁止**再指向任何能力认定评估。
