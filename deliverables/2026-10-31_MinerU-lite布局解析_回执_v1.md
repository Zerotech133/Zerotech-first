# MinerU-lite 布局感知解析 · 回执

## 一、为什么不用 MinerU 本体
- MinerU/Docling/Marker 依赖 torch + 布局模型（GB 级下载，本机无 GPU、pip 大包易挂起）；
- 项目文本层 PDF 的**表格/标题/阅读顺序**需求，用已有 PyMuPDF 即可覆盖大部分；先做轻量通道，重依赖留待明确需求。

## 二、交付
| 项 | 内容 |
|---|---|
| 脚本 | `tools/kb_corpus/pdf_layout.py`（PyMuPDF 文本块/字号/加粗启发式 + `find_tables()` markdown 表格） |
| 产物 | 逐页 `.layout.jsonl`（blocks/tables）+ `.layout.md`（标题/段落/表格） |
| 标准 | `docs/standards/2026-10-31_PDF知识入库标准_v1.md` 新增 §八「布局感知补充通道」 |

## 三、实测（真实研报）
| 文件 | 页 | 字数 | 表格 | 标题 | 耗时 |
|---|---|---|---|---|---|
| rp_...15406.pdf | 12 | 11,847 | 1 | 9 | 1.6s |
| rp_...15404.pdf | 37 | 21,162 | 0 | 337（幻灯式过检） | 5.1s |
| rp_...15419.pdf | 181 | 95,468 | **27** | 788 | 76.4s |

## 四、结论与边界
1. **表格提取可用**（181 页研报 27 张表），弥补纯 OCR/纯文本通道的核心缺口；
2. 标题启发式对**幻灯片式 PDF 会过检**（字号大→h1 过多）→ 该类文档只用表格通道；
3. 与现有 OCR 通道互补：扫描书走 OCR，文本层 PDF 走 pypdf 全文 + layout 结构；
4. 产物 schema 不变（页级 jsonl），未来可直接替换为 MinerU/Docling 输出。

## 五、下一步
- 在 `pdf_ingest.py` 中将 layout 通道接入 structure/index（文本层 PDF 自动双通道）；
- 如遇公式/复杂版面需求，再评估 MinerU（需 GPU 预算或离线模型包）。