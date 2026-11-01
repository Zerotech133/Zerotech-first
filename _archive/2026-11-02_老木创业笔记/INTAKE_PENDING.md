# 老木创业笔记 — 入库占位（待源文件）

> 建档时间：2026-11-02T03:45:48+08:00｜状态：**intake_pending（未找到源文件）**｜负责 AI：**A0 田丰**｜向老田负责

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
- 负责 AI：A0（田丰，参谋长，组织枢纽）；执行：按需派研究/数据线；审批：老田；
- 项目线登记（可选）：`agent_platform/registry/projects.json` 新增 `laomu-notes`（intake_pending）。
