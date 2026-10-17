# osint_video —— 视频号 OSINT 采集 → 文字提取 → 结构化语料管线

> 「老田的AI助手」子系统 ｜ 架构基线：`resdl_osint_architecture.md`（v1）+ `osint_v2_requirements_and_roadmap.md`（v2 增量）
> 当前实现范围：**T01（基础设施 + 链接队列）+ T03（ASR/OCR 抽取管线）——PoC 阶段**
> T02（采集服务 D）/ T04（Go 采集 B）/ T05（合规入库与向量化）尚未实现，见文末「路线图」。

---

## 1. 目录结构

```
osint_video/
├── README.md                  # 本文档
├── requirements.txt           # Python 依赖（核心 / 抽取 / 后续阶段分组）
├── .env.example               # 环境变量样例（复制为 .env）
├── config/
│   ├── settings.py            # pydantic-settings 全局配置（限速硬下限校验）
│   ├── whitelist.yaml         # 合规白名单骨架（T05 生效）
│   └── secrets/               # 密钥目录（gitignore；yuanbao_cookie.txt 应急直改通道，T02 使用）
├── common/
│   ├── logging_setup.py       # JSON 结构化日志 + logs/audit.log 审计日志
│   ├── db.py                  # PG 连接池（psycopg3 + SQLAlchemy Core，惰性单例）
│   ├── feishu.py              # 飞书 webhook 客户端（未配置/失败时降级为日志）
│   └── paths.py               # 全系统唯一路径来源（data/raw|work|store、logs）
├── queue_link/
│   ├── models.py              # LinkTask 模型 + 状态机常量/流转表
│   ├── repository.py          # 入队去重 / 认领 / 流转 / 指数退避重试 / 死信
│   └── api.py                 # FastAPI 队列服务（端口 8300）
├── extraction/
│   ├── audio_extract.py       # ffmpeg → 16k mono wav；ffprobe 探时长
│   ├── asr_sensevoice.py      # FunASR SenseVoice-Small + fsmn-vad（重依赖延迟导入）
│   ├── frame_extract.py       # 1fps 抽帧 + CLAHE 增强 + 相邻帧差异去重
│   ├── ocr_paddle.py          # PaddleOCR PP-OCRv4 + 时间轴相邻合并（延迟导入）
│   ├── align_merge.py         # ASR/OCR 双通道时间戳贪心对齐（纯标准库）
│   └── pipeline.py            # 编排 + 断点重跑 + StructuredDoc(schema_version=1.0)
├── store/
│   └── schema.sql             # 全部 DDL：osint_link_task / osint_credential / osint_target / osint_comment
└── scripts/
    ├── init_db.py             # 建表 + 打印确认
    └── poc_manual_test.py     # PoC：mp4 目录 → 抽取管线 → 质量报表 Markdown
```

运行期产物目录（自动创建，`common/paths.py` 管理）：

```
data/raw/{yyyy-mm}/{video_id}/   # 明文 mp4（T02 下载解密后落此；PoC 手工放置亦可）
data/work/{video_id}/            # 抽取中间产物：audio.wav / frames/ / *.json 检查点
data/store/                      # 本地成品预留（当前管线成品只入库不落盘）
logs/app.log                     # 应用日志（JSON 行格式）
logs/audit.log                   # 采集审计日志（保留 ≥180 天）
```

---

## 2. 环境准备

### 2.1 系统要求

| 项 | 要求 | 说明 |
|---|---|---|
| Python | 3.11+（老田新机为 3.11.9） | 类型注解基于 3.11 语法 |
| ffmpeg/ffprobe | ≥ 6.0 | 抽取管线宿主机必须；队列模块不需要 |
| PostgreSQL | 15+，端口 5432 | 老田现有 Docker 容器即可；队列/结构化入库共用 |
| Qdrant | T05 才用 | PoC 阶段无需启动 |

### 2.2 安装依赖

```bash
cd /c/项目/老田的AI助手/osint_video
python -m venv .venv
.venv/Scripts/activate        # Windows；Linux/macOS: source .venv/bin/activate

# ① 核心依赖（队列 API / 日志 / PG，体积小，必装）
pip install -r requirements.txt
#   requirements.txt 会跳过注释分组，funasr/torch/paddle 等重依赖见 ②

# ② 抽取管线重依赖（仅 T03 抽取机安装；队列机可跳过）
pip install "funasr>=1.1"
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu   # CPU 版即可
pip install "paddlepaddle>=2.6" "paddleocr>=2.7"                                # NPU/GPU 版见 PaddlePaddle 官网
pip install opencv-python-headless numpy

# ③ ffmpeg（Windows）
winget install -e --id Gyan.FFmpeg
#   安装后重开终端，ffmpeg -version 验证
```

> 模型说明：SenseVoice-Small 与 fsmn-vad 首次运行时从 ModelScope 自动下载（约 1GB），默认缓存在 `~/.cache/modelscope`。

### 2.3 初始化配置

```bash
cp .env.example .env
# 编辑 .env：PG 连接信息必填；FEISHU_WEBHOOK_URL 可选（不配则降级为日志输出）

# 生成 Cookie 加密密钥（POST /api/v1/credentials/yuanbao_cookie 需要）
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# 把输出写入 .env 的 FERNET_KEY=
```

### 2.4 初始化数据库

```bash
python scripts/init_db.py
# 输出示例：
#   [ok] CREATE TABLE osint_link_task      行数=0
#   [ok] CREATE TABLE osint_credential     行数=0
#   [ok] CREATE TABLE osint_target         行数=0
#   [ok] CREATE TABLE osint_comment        行数=0
```

创建 4 张表：`osint_link_task`（链接队列状态机）、`osint_credential`（凭证热更新）、`osint_target`（v2 监控对象）、`osint_comment`（v2 评论）。全部 `IF NOT EXISTS`，重复执行安全。

---

## 3. 运行：链接队列 API（T01）

```bash
# 项目根目录下启动（独立 8300 端口，故障隔离，架构 §8-8）
uvicorn queue_link.api:app --host 0.0.0.0 --port 8300
# 或：python -m queue_link.api
```

### 3.1 接口一览（响应统一 `{code, data, message}`，code=0 成功）

```bash
# ① 批量投递 sph 链接（幂等：url_hash 去重）
curl -X POST http://127.0.0.1:8300/api/v1/links \
  -H "Content-Type: application/json" \
  -d '{"urls": ["https://channels.weixin.qq.com/web/channels/xxx"], "created_by": "manual"}'
# → {"code":0, "data":{"accepted":1, "duplicated":0}, "message":"ok"}

# ② 队列查询（按状态过滤 + 分页）
curl "http://127.0.0.1:8300/api/v1/links?status=pending&page=1&size=20"

# ③ 元宝 Cookie 热更新（401 挂起后的人工通道；自动恢复 waiting_cookie 任务）
curl -X POST http://127.0.0.1:8300/api/v1/credentials/yuanbao_cookie \
  -H "Content-Type: application/json" \
  -d '{"cookie": "q_v_uid=...; sess=...", "updated_by": "laotian"}'
# → {"code":0, "data":{"name":"yuanbao_cookie", "status":"active", "resumed_waiting": 3}, "message":"ok"}

# ④ 各状态计数（日报数据源）
curl http://127.0.0.1:8300/api/v1/stats
```

### 3.2 状态机（`queue_link/models.py`）

```
pending → parsed → downloaded → decrypted → extracted → done
   │                                   任意处理态 → failed（指数退避 5min/30min/2h）→ 超限 → dead
   └→ waiting_cookie（Cookie 过期挂起）──人工更新 Cookie──→ pending
route=AUTO 的任务 D 重试超限 → 自动改 route=B 回 pending 再战
```

PG 不可用时所有接口降级返回 HTTP 503（服务不 crash）；投递与 Cookie 更新均写入 `logs/audit.log`。

---

## 4. 运行：PoC 抽取质量验证（T03）

用官方 res-downloader.exe 手工下载 3~5 个 mp4（PoC 不依赖采集服务，管线对视频来源零耦合）：

```bash
# 把 mp4 放进任意目录，例如 C:\temp\poc_mp4\
python scripts/poc_manual_test.py C:/temp/poc_mp4
# 可选参数：
#   --out 报告.md     指定报表输出路径（默认 data/work/poc_report_<时间戳>.md）
#   --force           忽略检查点强制重跑
#   --no-asr/--no-ocr 单通道降级验证
```

每视频产出：

```
data/work/{video_id}/
├── audio.wav              # ffmpeg -vn -ar 16000 -ac 1
├── asr_segments.json      # [{start, end, text, conf}]
├── frames/                # 1fps CLAHE 增强帧（相邻差异去重后）
├── frame_index.json       # [{index, ts_sec, path}]
├── ocr_items.json         # [{start, end, text, conf}]（时间轴相邻合并后）
└── structured_doc.json    # StructuredDoc（§3 schema，schema_version=1.0）
```

质量报表内容：每视频 ASR/OCR 段数、平均置信度、覆盖率、总时长、失败清单、汇总均值。
**断点重跑**：中间产物即检查点，删掉 `structured_doc.json` 后重跑只补缺失阶段；`--force` 全量重跑。

### 4.1 降级行为

- ASR 模型不可用（funasr 未装/加载失败）→ 自动仅 OCR 通道，日志 warning，不中断；
- OCR 模型不可用 → 仅 ASR 通道；
- ffmpeg 缺失 / 视频损坏 → 该视频记入失败清单（属硬错误，不降级）。

---

## 5. 日志与审计

- 应用日志：`logs/app.log`，JSON 行格式（ts/level/logger/message + 扩展字段）；
- 审计日志：`logs/audit.log`，独立文件，记录"谁投了什么链接、谁更新了 Cookie、任务何时入死信"，保留 ≥180 天；
- 日志级别用 `.env` 的 `LOG_LEVEL` 控制（默认 INFO）。

---

## 6. 常见问题（FAQ）

| 症状 | 处置 |
|---|---|
| `未找到可执行文件 ffmpeg` | `winget install -e --id Gyan.FFmpeg` 后重开终端（错误信息自带指引） |
| PG 接口全部 503 | 检查 PG 容器 `docker ps`、`.env` 的 PG_* 配置；`python scripts/init_db.py` 验证连通 |
| `ASR 模型加载失败` | 确认已装 funasr/torch；首次运行需联网下载模型；离线机可预下载后拷贝 `~/.cache/modelscope` |
| PaddleOCR 构造报错 | paddlepaddle 与 paddleocr 版本需配套，见错误信息中的安装指引；3.x 与 2.x API 已做兼容 |
| FERNET_KEY 相关 400 | 按 `.env.example` 注释重新生成密钥 |
| 限速配置报错 `低于硬下限 30s` | 合规护栏：`COLLECT_INTERVAL_SEC` 只能 ≥30，禁止机器速 |

---

## 7. 已知限制（PoC 阶段）

1. **ASR 置信度为占位值**：SenseVoice-Small 不输出 token 级置信度，`asr_segments[].conf` 当前为固定默认值 0.80（详见 `asr_sensevoice.py` 注释）；质量评估请以报表覆盖率 + 人工抽听为准（验收线 CER≤8%）。
2. `claim_next()` 未加 `FOR UPDATE SKIP LOCKED` 行锁：PoC 无并发 worker；T02 上线时替换为锁定认领（代码注释已标注）。
3. compliance/entities/topics 字段在 StructuredDoc 中为占位（空数组/默认值），由 T05 ComplianceFilter 回填。
4. v2 的 `osint_target`/`osint_comment` 仅建表（含索引），读写仓储随 T06/T07 实现。
5. ffmpeg 的 wav 与抽帧两条通道在管线内并行执行（线程池 2 worker）；模型推理仍为串行加载后各占一线程。

---

## 8. 路线图（对照架构任务表）

| 任务 | 状态 | 内容 |
|---|---|---|
| T01 基础设施 + 链接队列 | ✅ 本 PoC | 队列 API / 状态机 / 重试 / 死信 / 审计日志 |
| T03 抽取管线 | ✅ 本 PoC | ASR+OCR 双通道 + 对齐 + 质量报表 —— **Go/No-Go 决策依据** |
| T02 采集服务 D | ⏳ | 元宝解析链 + 下载解密 + Cookie 热更新 + 令牌桶限速 |
| T06 监控探路（v2） | ⏳ | finder 门户 Cookie：账号 feed + 评论接口验证 |
| T04 Go 采集 B / T05 合规入库 / T07-T10 | ⏳ | 见 v1/v2 路线图 |
