---
name: aios-domain-scaffold
description: 在本 AI 工作站（E:\老田的AI助手）新增能力域时按固定脚手架搭建——复用素材库、本地 Ollama、飞书推送、计划任务与门户面板。触发词包括 加新域、新能力、搭管线、接入门户、自动化某信息源。不用于一次性脚本、单文件修改或纯问答。
---

# AI 工作站能力域脚手架

本工作站的 4 个已落地域（新闻采集 / 量化回测 / 公众号 / 短视频）共用同一骨架。
新域按此骨架搭，禁止重新发明基建。详细代码模板见 [references/templates.md](references/templates.md)；
计划任务深层坑见已有 skill `.trae/skills/system-task-deploy/`（playbook.md 权威）。

## 固定基建（先查表，禁止重建）

| 能力 | 位置 | 用法 |
|---|---|---|
| 素材库 | `news_collector/news.db` | SQLite `news(id,source,ts,title,content,hit,stocks,pushed,digested,created_at)`，按 `created_at` 时间窗取材，`hit DESC` 优先 |
| 本地 LLM | `http://127.0.0.1:11434/api/chat` | 模型 `qwen3:8b-8k`，`"think": false`，system prompt 三件套：只依据素材/禁止编造/严格输出格式 |
| 飞书推送 | `news_collector/send_feishu.py` | `argv[1]`=UTF-8 消息文件路径，子进程列表传参，判 `SEND_OK`。不要另写发送逻辑 |
| ffmpeg/ffprobe | `tools/ffmpeg-9.0.2-essentials_build/bin/` | 全路径调用；中间文件一律走 ASCII 目录 `C:\Temp\*` |
| 离线 TTS | 系统 Python 自带 pyttsx3 | 每段合成必须独立子进程（引擎不可复用）；edge-tts 被墙不可用 |
| 门户 API | `portal/portal_api.py`（8091，SYSTEM 任务自愈） | 加 `get_<域>()` + overview 字段 + TASKS 名单 |
| 门户前端 | `portal/index.html` | TASK_SHORT 映射 + panel div + loadConsole 渲染段 |
| ASCII 通道 | `C:\aios` junction → 本工作区 | SYSTEM 任务/启动器路径必须零中文 |

## 七步脚手架（顺序执行）

1. **域目录 + config.json**：`<域>/config.json` 每次运行时读取（即改即生效原则），含可调参数与 `"model"`。
2. **核心脚本**：素材获取 → LLM 结构化输出 → 硬校验+规范化（LLM 输出必须当不可信输入解析，JSON 用 `re.search(r"\{.*\}", ..., re.S)` 兜底抽取）。
3. **落盘留档**：产物归 `<域>/output/` 或 `drafts/`，`index.json` 记录元数据（`.tmp` + `os.replace` 原子写，截断保留 30 条）。
4. **飞书审阅**：消息含标题、元信息、全文或摘要、产物路径；`--dry` 模式必做（打印不发）。
5. **计划任务**：ASCII 启动器（`Start-Process -PassThru` + `Wait-Process`）→ XML 导出改写导入注册 → 验证 NextRunTime 换算与 LastTaskResult。时刻换算：**北京时间 T = 边界写 (T-6h)+02:00**（绝对 UTC，冬夏令时不漂移）。
6. **门户接入**：`get_<域>()` 容错读 index.json → overview 加字段 → index.html 加 TASK_SHORT、面板、渲染（空态显示"待首发"黄徽标，勿红叉）。
7. **端到端验证**：先 `--dry`，再真实触发计划任务跑一轮，验证 result=0 + 产物 + 门户字段，最后向用户**披露发了几条飞书消息**。

## 铁律

- SYSTEM 链路全程零中文路径（经 `C:\aios`）；沙箱测试走 `E:\` 直路径（junction 写入会被静默丢弃）。
- 沙箱禁写系统目录（pip 用 `--target` 装进域内 pylibs；软件下载静态版进 `tools/`）。
- PS5.1 下外部命令 stderr 会渲染成 CLIXML "Error"，内容正常即忽略。
- 每轮结束把新坑追记到 system-task-deploy 的 playbook.md。
- 配置/风格类参数进 config.json，代码内不写死。
