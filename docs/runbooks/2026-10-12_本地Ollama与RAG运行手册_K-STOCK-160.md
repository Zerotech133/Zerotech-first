# 本地 Ollama 与 RAG 运行手册（K-STOCK-160）

本手册针对本仓库的本地 AI 聊天服务。服务只绑定回环地址：Ollama 为
`127.0.0.1:11434`，RAG Web 为 `127.0.0.1:8899`。所有命令均在仓库根目录
执行，脚本只允许访问回环地址，不会访问外网。

## 1. 启动前自检与端口守卫

先做完全离线的检查，确认 Python、文档目录和端口注册表结构：

```powershell
python tools/ollama_rag_smoke.py --dry-run preflight
```

端口注册表的唯一来源是 `tools/check_ports.py`。8899（Web）和 11434
（Ollama）必须在 `REGISTRY` 中；脚本会静态读取注册表，不会因为检查而启动服务。
服务实际运行前再执行一次端口状态检查：

```powershell
python tools/check_ports.py --strict
```

`8899` 是按需服务，离线属于 `OPTIONAL_OFFLINE`；未登记监听端口或硬性服务离线
时，先处理端口归属再启动，禁止改用未登记端口。

## 2. 启动 Ollama 与 8899 Web

确认本机 Ollama 已运行并且模型存在。默认模型为聊天模型
`qwen3.5:9b`、嵌入模型 `bge-m3`；启动前自检会检查 `/api/tags`：

```powershell
python -m ollama_service.launcher --check-only
```

自检通过后启动 Web。启动器发现 8899 已在线时会复用现有进程，不重复拉起：

```powershell
python -m ollama_service.launcher
```

也可以用运维脚本执行同一启动链路：

```powershell
python tools/ollama_rag_smoke.py start
```

离线演练只打印将执行的命令，不会启动进程：

```powershell
python tools/ollama_rag_smoke.py --dry-run start
```

页面地址为 `http://127.0.0.1:8899/`。RAG 请求为 `POST /api/rag`，请求体示例：
`{"message":"问题", "top_k":4}`；响应是 NDJSON，包含 `message`、`sources` 和
`done` 事件。知识文档只读 `knowledge_docs/` 下的 `.txt`、`.md`、`.markdown`。

## 3. RAG 索引 status、verify、rebuild

8899 服务的生产索引在进程内存中，文档变化后首次 RAG 请求会自动更新。运维脚本
提供一个可复核的 JSON 诊断索引，默认路径为 `work/rag_index.local.json`；该文件
用于核对来源、哈希和向量，不会替代 8899 的内存索引。

查看文档、诊断索引和端口注册状态：

```powershell
python tools/ollama_rag_smoke.py status
```

只读验证 UTF-8 文档、索引来源/`document_hash`/向量数量；索引不存在时会明确提示
8899 将按需在内存中重建：

```powershell
python tools/ollama_rag_smoke.py --index work/rag_index.local.json verify
```

重建会向本机 `11434/api/embed` 请求 `bge-m3` 向量并写入诊断索引。它不修改源文档，
也不访问外网：

```powershell
python tools/ollama_rag_smoke.py --index work/rag_index.local.json rebuild
```

重建前可先查看计划而不连 Ollama、不写文件：

```powershell
python tools/ollama_rag_smoke.py --dry-run --index work/rag_index.local.json rebuild
```

## 4. 可重复 smoke 验证

默认 smoke 只探测本机服务：GET 主页、GET Ollama `/api/tags`，并向 `/api/rag`
发送一条问题，检查 NDJSON 是否包含 `sources` 和 `done`。先用离线模式确认命令
和检查项：

```powershell
python tools/ollama_rag_smoke.py --dry-run smoke
```

服务和 `knowledge_docs/` 均准备好后运行本机 smoke：

```powershell
python tools/ollama_rag_smoke.py smoke
```

脚本退出码为 `0` 表示全部检查通过，`1` 表示失败；加 `--json` 可供 CI 或值班
记录消费。`--ollama-url` 与 `--web-url` 若指向非回环地址会被拒绝。

## 5. 故障恢复

| 现象 | 处理 |
| --- | --- |
| 启动前自检提示 Ollama 不可达 | 确认本机 Ollama 进程和 `127.0.0.1:11434`，再重跑 `python -m ollama_service.launcher --check-only`。 |
| 模型缺失 | 在本机 Ollama 环境补齐 `qwen3.5:9b` 与 `bge-m3`，然后重跑自检；脚本不会替你联网拉取。 |
| 8899 已被占用 | 先用 `python tools/check_ports.py --strict` 辨认监听进程，确认是既有 Web 服务后复用；若为未知进程，按端口守卫流程处理，禁止私改端口。 |
| RAG 返回 503 | 运行 `status` 和 `verify`，确认 `knowledge_docs/` 存在且文档为 UTF-8；修复目录后重启 8899 或发起一次新的 RAG 请求。 |
| 索引哈希或向量不匹配 | 删除诊断索引前先保留输出记录，执行 `python tools/ollama_rag_smoke.py --index work/rag_index.local.json rebuild`，再执行 `python tools/ollama_rag_smoke.py --index work/rag_index.local.json verify`。源文档始终只读。 |
| 服务卡死或重复启动 | 记录端口和 PID，确认不是线上服务后再按 Windows 进程管理流程停止；随后用 `start` 重启并重复 smoke。 |

恢复完成的证据至少包括：`preflight`、`status`、`verify` 和 `smoke` 的退出码，
以及 `check_ports.py --strict` 的输出。长时间命令请将输出重定向到文件保存，便于
后续审计。
