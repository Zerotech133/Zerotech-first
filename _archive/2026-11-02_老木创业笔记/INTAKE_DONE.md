# 老木创业笔记 — 已入库（S2 完成，进入流水线）

> 更新：2026-11-02T04:12:20+08:00｜状态：**ingested（源文件已入库）**｜流程 Owner：**DATA1 张松（兰台令）**；执行 DATA2 刘巴；A0 仅仲裁

## 一、现状
全盘检索 0 文件/0 引用/0 git 记录（范围见 `research/reports/project_archive_20261102.json`）：
- 仓库 deliverables/docs/research/portal/knowledge_docs/agent_platform/tools：0
- `C:\项目` 深度3 文件名 + 知识库（深度3 + 关键目录内容）：0
- `C:\Users\t` 深度2、百度网盘目录：0
- git 全历史：0

## 二、等待你提供（任选其一）
1. **PDF/图片**：放到 `C:\项目\PDF\`（建议文件名 `老木创业笔记*.pdf`），告诉我后我执行 OCR→知识单元→索引→学习卡；
2. **文本文档（md/docx/txt）**：放到 `C:\项目\知识库\` 指定目录，我走文本入库通道；
3. **仅给我实际路径**，我直接读取入库。

## 三、入库标准（与学习PDF一致）
- 源文件归档（不复制进 git）；OCR/清洗产出留 `C:\项目\PDF\_ocr`；
- 知识单元入 `portal/static/kb_units_all.json`，学习卡走审核流程（pending_review → 人工通过 → merge）；
- 建同结构 `manifest.json` + `ENTRY.md`；
- 选题/内容类结论不构成投资建议。

## 四、负责与升级
- 流程 Owner：DATA1 张松；执行：DATA2 刘沙/清洗线→DATA5 结构化→DATA3 质检→DATA4 标准化；A0 仅仲裁；审批：老田；
- 项目线登记（可选）：`agent_platform/registry/projects.json` 新增 `laomu-notes`（intake_pending）。


## 五、入库记录（2026-11-02T04:12:20+08:00）
- 源文件：`交易老木_29条视频文案汇编.md`（48,419 B，592 行，29 条视频文案；微信收发文件）
- 入库：`知识库/05_学习源件/laomu_notes/source/`（sha256 `16553a44…f21123`）
- 清洗：`知识库/20_清洗/laomu_notes/text/`（49,011 字）
- 流水线：288 条知识单元草稿；30 个高频概念；203 张学习卡候选（pending_review）
- 负责：DATA1 张松（流程 Owner）；执行 DATA2；A0 仅仲裁
