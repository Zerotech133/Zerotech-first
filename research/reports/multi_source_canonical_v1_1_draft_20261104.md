# 多源 canonical v1.1 草案（W22，2026-11-04）：布林线 3 源入库 → 30/30（待老田签核）

> **状态：draft_pending_signoff（草案，未签核）**。正式 canonical `multi_source_canonical_20261104.json` **未改动**（sha256 前后一致，29/30 保持）；本草案仅作为签核用证据包，签核通过后由正式流程升级为 v1.1。
> 结论：**count = 30 / 30**（29 既有 + 布林线 1），布林线 = **3 个独立来源 / 3 个独立来源类型**，满足 canonical 定义；待老田人工签核后生效。

## 一、交付与产物

| 产物 | 路径 | 说明 |
|---|---|---|
| 草案 JSON | `research/reports/multi_source_canonical_v1_1_draft_20261104.json` | count=30、concepts=30、布林线条目（sources/evidence/boundary）、validation、rollback |
| 草案 MD | `research/reports/multi_source_canonical_v1_1_draft_20261104.md` | 本文件 |
| 多源输入 v5 | `research/reports/knowledge_multisource_20261104_v5.json`（+md） | v4 六路输入 + 公开投教布林线 units（7 路）；概念 1647、多源 10 |
| 同义归并 v5 | `research/reports/concept_synonyms_20261104_v5.json`（+md） | 1647→1596 簇、多源 10→**20 确认** |
| 学习项目 | `agent_platform/registry/learning_projects.json` → `公开投教_布林线`（status=distilling） | 新项目注册（仅本项目管理） |
| 源件与 units | `C:\项目\知识库\05_学习源件\公开投教_布林线\source\`（3 篇短摘）；`20_清洗\公开投教_布林线\`；`30_知识单元\公开投教_布林线_units_draft.jsonl`（**3 units**） | 合规短摘入库 + pipeline clean→units |

## 二、3 个公开源短摘（均仅引用，不全文转载）

| # | 来源/类型 | 标题与 URL | 许可/引用说明 | 短摘要点（≤300 字） |
|---|---|---|---|---|
| C1 | 中国投资者网 · **official_edu** | [标题链接](https://www.investor.org.cn/xxzx/tzzkt/gp/jczs/xsrsbd/202507/t20250724_812013.shtml)；投教栏目「投资者课堂·股票·基础知识」 | 公开投教栏目内容；仅引用标题/URL/标题级短摘并注明来源，不转载正文图片 | 该文将布林线定位为股市的“波动风向标”，以“3 大锦囊”向中小投资者科普布林线用法；布林线属衡量股价波动范围/状态的技术分析工具（标题级引用） |
| C2 | 国泰君安证券 · **broker_edu** | [布林线｜概念百科-金融公开课](https://ua.gtja.com/cos/open/baike/223.html) | 券商公开投教页面；仅引用链接+≤300 字定义短摘并注明来源 | 英文名 Bollinger Bands，根据统计学中的标准差原理设计的技术分析指标；以“波带”形式反映股价波动范围，又称“布林带”；由三条轨道线组成；1983 年 John Bollinger 创造；相关概念 MACD、KDJ |
| C3 | Backtrader 开源项目文档 · **opensource_docs** | [backtrader.indicators.bollinger](https://backtrader.readthedocs.io/en/latest/api/indicators/backtrader.indicators.bollinger.html) | 项目文档（Backtrader/GPLv3）；仅引用指标定义与参数口径，不复制代码；注明项目/文档版本 | Volatility bands，测量市场波动；公式：midband=SMA(close, period)，topband=midband+devfactor×StdDev，botband=midband−devfactor×StdDev；参数 period=20, devfactor=2.0；另有 %B 线 |

短摘获取日期：2026-11-04（W22）；每篇 .md 含标题/URL/来源类型/许可说明，正文短摘均 ≤300 字（C1 为标题级引用，正文未稳定抽取，已在源件中如实标注）。

## 三、pipeline 与多源重算
1. **入库**：`05_学习源件/公开投教_布林线/source/` 3 篇短摘 → intake 复制到 `20_清洗/.../text/` → `learning_ops.clean_md` → `draft_units` → `30_知识单元/公开投教_布林线_units_draft.jsonl`，**units=3**（每源 1 条知识单元；元数据表格行/标题行按 pipeline 规则跳过，`meta_skipped=0`）。
2. **units 溯源**：每条 unit 带 `evidence.file`（C1/C2/C3 短摘 .md）与行号；草案 JSON `evidence.knowledge_units[]` 记录 evidence 文件 sha256，`units_traceable=true`。
3. **多源 v5**：复用 W4 扩输入方法（临时 adapter：units→显式 `source_type/source_key/source_label`），输入 = v4 六路 + 布林线 units（共 7 路）；输出 `knowledge_multisource_20261104_v5.json`：记录 1737、概念 **1647**、多源 **10**（布林线为新增多源概念）。
4. **同义归并 v5**：1647→**1596** 簇（噪声 15、合并 20 簇），多源 **10→20 确认**；布林线参与合并但**不与「技术指标」「KDJ金叉」合并**（边界校验通过）。

## 四、布林线多源判定（canonical 定义符合性）
- **独立来源 3**：official_edu（中国投资者网 URL）、broker_edu（国泰君安 URL）、opensource_docs（Backtrader 文档 URL）——`(source_type, source_key)` 三元独立；
- **独立来源类型 3**（≥2）✅；**来源≥2** ✅；无冲突/无重复来源；
- **概念边界**：正式 canonical 中无「布林线/布林带/BOLL」；最近概念为「技术指标」（上位类）与「KDJ金叉」（振荡指标交叉信号）——布林线为**波动带类指标**，签核时禁止并入两者；
- **准入 amendment**：新增 source_type（official_edu/broker_edu/opensource_docs）需 DATA4/A0 评审（草案 `definition.amendment_v1_1` 已登记）。

## 五、草案自检（validation，全 True）
```json
{"units_traceable": true, "units": 3, "bollinger_source_types": 3, "bollinger_sources": 3,
 "boundary_ok": true, "official_canonical_unchanged": true,
 "count_formula": "29 + 1 = 30", "self_consistent": true}
```
- 正式 canonical sha256 前后一致 → **未改正式文件**；草案 count == len(concepts) == 29+1 == **30**；`meets_pending_signoff=true`（签核后 meets=true）。

## 六、工具限制与补丁（如实报告）
- **限制**：`knowledge_multisource.py` 原只识别 blogger/research_report/book/other 四类来源；公开投教类 source_type 无法表达（W19 已提示需扩展）。
- **已实施补丁（向后兼容，待评审）**：`_source_from_unit` 支持显式 `source_type/source_key/source_label`（未提供时保持原推断逻辑）；`SOURCE_TYPE_LABEL` 增加 official_edu/broker_edu/opensource_docs/public_encyclopedia 标签。该补丁使布林线可在 v5 中呈现「3 独立来源类型」；**生产准入仍需 DATA4/A0 评审**（草案 amendment 已登记）。
- **其他限制**：C1 正文被页面导航截断，未做正文原文摘录（仅标题级引用，源件已注明）；待 DATA2 补录正文短摘后可替换 claim 并重算。

## 七、回滚
- 草案**不写正式 canonical**：删除本草案 JSON/MD 即回滚（正式文件 sha256 `4fdcc57ef51b54ba58b5be14ebe7a7ca4a4db269e1a125f8ec68b4f8b159b661` 未变）；
- 移除 registry 中 `公开投教_布林线` 项目并删除 `知识库/{05_学习源件,20_清洗,30_知识单元}/公开投教_布林线*` 即可完全撤回入库；
- v5 为独立报告，不影响 v4/canonical v1。

## 八、签核请求（老田）
1. 确认布林线作为第 30 个多源概念（3 独立来源类型）**准入**；
2. 确认 source_type 扩展（official_edu/broker_edu/opensource_docs）**准入**；
3. 确认「布林线 ≠ 技术指标/KDJ金叉」的边界口径；
4. 签核后由正式流程将 canonical 由 29→30（v1.1），并更新训练数据矩阵 V17 / KPI / 导出读数。
