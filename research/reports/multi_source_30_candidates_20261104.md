# 多源第 30 候选调研（W19，20261104）

> 只做候选评估：**未下载全文、未入库、未改 canonical**（canonical 仍 29/30）。
> 检索方式：web_search + 1 次 web_fetch 核验（仅看标题/定义，不保存原文）。

## 一、结论（先看）

- 找到 **3 篇公开可引用资料**（官方投教 1 / 券商投教 1 / 开源文档 1），均围绕同一概念 **布林线（Bollinger Bands / BOLL）**；
- canonical v1（29 概念）中 **无布林线/BOLL/布林带**（已程序化核对概念+成员）；最近的既有概念是「技术指标」「KDJ金叉」，需人工划清边界；
- 3 篇来自 **不同独立来源类型**（official_edu / broker_edu / opensource_docs），满足「≥2 独立来源」的多源定义 → **可将「布林线」作为第 30 个跨源多源概念候选**；
- 但 **不能直接入库**：canonical 现仅有 blogger/research_report 两类来源，需申请扩展 source_type + 人工签核；签核前 canonical 保持 29/30。

## 二、候选资料

| # | 标题 | 来源/类型 | URL | 许可/引用 | 置信 |
|---|---|---|---|---|---|
| C1 | 布林线的3大锦囊，小白也能秒懂的股市“波动风向标” | 中国投资者网（证监会/中证协投教平台）· `official_edu` | [链接](https://www.investor.org.cn/xxzx/tzzkt/gp/jczs/xsrsbd/202507/t20250724_812013.shtml) | 公开投教内容：引用标题/URL/短摘并注明来源；禁全文转载 | 0.80 |
| C2 | 布林线｜概念百科-金融公开课 | 国泰君安证券 · `broker_edu` | [链接](https://ua.gtja.com/cos/open/baike/223.html) | 券商公开投教页面：引用链接+短摘；不得转载全文 | 0.75 |
| C3 | backtrader.indicators.bollinger — BollingerBands API | Backtrader 开源文档 · `opensource_docs` | [链接](https://backtrader.readthedocs.io/en/latest/api/indicators/backtrader.indicators.bollinger.html) | GPLv3 项目文档：注明项目/版本，可用定义/参数口径，不复制大段代码 | 0.85 |

**C2 已 web_fetch 核验**：页面标题「布林线｜概念百科」、英文名 **Bollinger Bands**、定义“根据统计学标准差原理设计的技术分析指标”、三条轨道线（仅核验，未存全文）；C1/C3 由 web_search 命中官方标题与 URL。

## 三、与 canonical 概念比对

- canonical（`multi_source_canonical_20261104.json`）：**29 概念 / target 30 / meets false**；定义要求“≥2 个独立 (source_type, source_key)”，语义合并组需人工签核；
- 程序化检索 canonical 概念与成员：`布林 / BOLL / 布林带 / 均线 / MACD / RSI / 标准差 / 波动率` **均为 0 命中**；只有 `KDJ`（KDJ金叉）存在；
- 因此「布林线」是**干净的新概念候选**，与既有「技术指标」「KDJ金叉」语义邻近但不重合（需在签核时明确边界，防止误并）。

## 四、最可能补的多源概念：布林线（Bollinger Bands）

- **定义（候选口径）**：以 N 期移动平均为中轨，上下轨为中轨 ± k 倍标准差构成波带，用于描述价格波动范围、趋势及波动收敛/扩张；C3 给出可计算参数口径（period/devfactor），C1/C2 给概念与用途解释；
- **独立来源**：official_edu（中国投资者网）×1 + broker_edu（国泰君安）×1 + opensource_docs（Backtrader）×1 = **3 个独立来源类型**，满足跨源多源规则；
- **预期效果**：若 3 源登记为独立 source_key 并人工签核 → canonical **29→30（target 达成）**；
- **置信**：概念存在/定义证据充分（0.8）；canonical 准入（0.5，取决于 source_type 扩展与签核流程）。

### 准入前提 / 风险

1. 仅引用（标题/URL/短摘）登记，**不下载全文、不转载**；C1/C2 为投教科普、C3 为开源文档，需在 manifest 记录许可与引用范围；
2. canonical 现有 source_type 仅 blogger/research_report，**扩展 official_edu/broker_edu/opensource_docs 需 DATA4/A0 评审**；
3. 新概念需**人工签核**并写清与「技术指标」「KDJ金叉」的语义边界；
4. 券商百科为通俗解读，公式/参数以 C3 为准交叉核对；GPLv3 许可仅约束代码，文档引用需注明项目/版本。

## 五、备选

- **均线（MA）**：canonical 无“均线”，公开投教/开源文档覆盖广；本次未像布林线一样锁定同级的官方原文，置信较低（0.6），可在布林线被否时作为备选。
- **RSI**：未找到可明确定位的交易所/协会投教原文，不推荐（0.3）。

## 六、建议下一步（不自动执行）

1. DATA2：对 C1-C3 做来源元数据登记（标题/URL/机构/许可/引用范围），**仅引用不存全文**；
2. DATA4：评审 source_type 扩展与新概念归一（布林线 vs 技术指标/KDJ），出 canonical v1.1 草案；
3. A0/老田：签核后更新 canonical 29→30；签核前保持 29/30，不自动入库。

> 若需正式入库，另立采集/签核任务；本报告仅为候选评估证据，不构成投资建议。
