# pdf_ingest 双通道接入 · 回执

## 一、改动
| 步骤 | 文本层 PDF（kind=text） | 扫描版（kind=scanned） |
|---|---|---|
| ocr | 自动改跑 **layout 通道**（PyMuPDF 文本块/标题/表格）→ `layout.jsonl`/`.md` | 原 OCR（RapidOCR） |
| structure | 从 layout 标题（h1/h2）抽取章节，`source=layout` | 从 OCR 文本抽取（原逻辑） |
| distill | 从 layout 段落聚合知识单元（每章 ≤12 页/9000 字） | 原 OCR 文本 |
| index | 摘要中增加「N 张表格 / M 个标题（layout 通道）」 | 原逻辑 |
| verify | 按 layout 字符数折算页覆盖/质量（文本层无需 OCR） | 原 OCR 指标 |
| 阈值 | 支持 `--units-min/--chapters-min`（短研报放宽需注明） | 默认 10/5 |

## 二、实测
| 用例 | 结果 |
|---|---|
| 3 份文本层研报（12/73/181 页） | scan→layout→structure→distill→verify **ok=true**（units-min 5）；181 页含 27 表 |
| 两本扫描书（伏击涨停/涨停密码） | **ok=true**（默认阈值 10/5）回归通过 |
| 契约测试 `test_pdf_ingest_contract.py` | **4/4 通过** |
| pipeline_check（书籍） | 未变化（ok=true） |

## 三、说明
- 同一套 CLI/SOP，现在**自动**区分：扫描书→OCR，文本层研报→全文+布局/表格；
- 短文档阈值放宽属于「报告注明」类例外，不改变标准默认值；
- 页面 `/kb_ingest.html` 的 verify 徽章仍读 `_ingest/verify.json`（书籍目录），文本层演示未混入正式目录。

## 四、交付
- 代码：`tools/kb_corpus/pdf_ingest.py`（双通道 + 可配置阈值）
- 标准：`docs/standards/2026-10-31_PDF知识入库标准_v1.md` §8.1
- 演示产物：`C:\项目\work\ingest_text_demo\_ingest\`（3 份研报全流程产物）