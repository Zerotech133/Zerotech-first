# 本地 Ollama/RAG 运行手册
**文档编号**：K-STOCK-160 (修订版)  
**适用对象**：运维值班人员、老田 AI 工作站维护组  
**生效状态**：已归档至 docs/plans INDEX，旧五源只读冻结

---

## 1. 服务启动

### 1.1 Ollama 核心服务拉起
确保系统环境满足 Docker 或原生运行时要求。执行以下命令启动 Ollama 守护进程：

```bash
ollama serve
```
*注意：生产环境建议配置 systemd 服务以实现开机自启与看门狗机制。*

### 1.2 常用模型加载
老田工作站主要依赖 `qwen3.5` 系列模型。需根据显存资源选择拉起规格：

- **轻量级任务**（<8GB 显存）：
  ```bash
  ollama pull qwen3.5:4b
  ollama run qwen3.5:4b "你好，请开始工作"
  ```
- **标准推理任务**（>12GB 显存）：
  ```bash
  ollama pull qwen3.5:9b
  ollama run qwen3.5:9b "你好，请开始工作"
  ```

### 1.3 RAG Web 服务启动
RAG 检索增强生成服务依赖 Ollama 后端。启动独立容器或脚本：

```bash
# 进入项目根目录
cd /path/to/old-tian-ai-workstation

# 启动 RAG 网关服务 (假设使用 uvicorn/fastapi)
python -m tools.rag_server --host 0.0.0.0 --port 8000 --ollama-host localhost:11434
```
*确认环境变量 `OLLAMA_HOST` 已正确指向本地端口。*

---

## 2. 健康检查

运维值班人员需每 5 分钟轮询一次以下指标，确保服务存活。

### 2.1 API 接口探活
通过 curl 命令检查 Ollama 核心状态：

```bash
curl -s http://localhost:11434/api/tags | jq .
```
*判据：返回 JSON 数组且非空，表示模型列表可访问。*

### 2.2 系统资源探活
检查底层操作系统及 GPU 驱动状态：

```bash
curl -s http://localhost:11434/api/os | jq .
```
*判据：`gpu_count` > 0 且 `system_info` 无报错。*

### 2.3 Python 脚本自动化检查
使用内置运维脚本进行深度健康诊断（输出日志至 `/var/log/ollama_health.log`）：

```bash
python ops/health_check.py --verbose
```
*脚本将自动检测模型加载状态、显存占用率及网络连通性，若发现异常将自动生成告警工单。*

---

## 3. 模型路由与降级

当主模型响应超时或显存溢出时，系统需自动切换至降级策略。配置集中管理文件位于 `config/ollama_model_route.json`。

### 3.1 配置文件结构
该 JSON 文件定义了优先级队列与降级规则：

```json
{
  "default_model": "qwen3.5:9b",
  "fallback_chain": [
    {
      "priority": 2,
      "model": "qwen3.5:4b",
      "condition": "latency > 2000ms OR gpu_memory_usage > 90%"
    },
    {
      "priority": 1,
      "model": "llama3.2:1b",
      "condition": "cpu_only"
    }
  ],
  "maintenance_mode": false
}
```

### 3.2 路由逻辑说明
- **默认路径**：优先使用 `qwen3.5:9b` 处理复杂推理。
- **降级触发**：
  - 若响应延迟超过 2000ms，自动切换至 `qwen3.5:4b`。
  - 若 GPU 显存占用率超过 90%，强制切换至小参数模型或 CPU 模式。
- **维护模式**：当 `maintenance_mode` 为 true 时，所有请求将返回静态提示页，禁止模型调用。

---

## 4. Smoke 验证

部署变更后，必须执行烟雾测试以确认链路通畅。

### 4.1 脚本用法
运行位于 `tools/ollama_rag_smoke.py` 的自动化测试脚本：

```bash
python tools/ollama_rag_smoke.py --config config/ollama_model_route.json --timeout 30s
```

### 4.2 通过判据
脚本执行完毕后，控制台输出需满足以下条件方可视为“通过”：
1. **连通性**：成功连接 `http://localhost:11434`。
2. **模型加载**：`/api/tags` 返回至少一个有效模型标签。
3. **推理响应**：发送测试 Prompt 后，在超时时间内收到非空 JSON 响应。
4. **RAG 检索**：模拟查询向量库，返回 Top-K 片段且无空值。

*若任一判据失败，脚本将输出具体错误码（如 `ERR_MODEL_NOT_FOUND`, `ERR_OOM`）并建议排查方向。*

---

## 5. 常见问题

### 5.1 模型未加载
**现象**：API 返回 `model not found` 或长时间无响应。  
**原因**：模型文件损坏或未拉取。  
**解决**：
```bash
ollama rm qwen3.5:9b
ollama pull qwen3.5:9b
```

### 5.2 显存不足 (OOM)
**现象**：服务崩溃或报错 `CUDA out of memory`。  
**原因**：当前模型参数量超出 GPU 容量，或上下文窗口过大。  
**解决**：
1. 修改 `config/ollama_model_route.json` 启用降级策略。
2. 减少 `OLLAMA_NUM_PARALLEL` 参数（默认 1）。
3. 切换至 `qwen3.5:4b` 或更小模型。

### 5.3 端口占用
**现象**：启动时报错 `Address already in use`。  
**原因**：Ollama 服务未正常关闭，残留进程占用 11434 端口。  
**解决**：
```bash
# 查找并杀死残留进程
lsof -i :11434 | grep PID
kill -9 <PID>
ollama serve &
```

### 5.4 Think 模式空响应
**现象**：启用 `think` 模式后，模型仅输出思考过程而无最终答案。  
**原因**：Prompt 模板未包含强制结束符，或模型版本不支持该指令。  
**解决**：
1. 检查 System Prompt 是否包含 `<end_thought>` 标记。
2. 确认使用的模型版本支持 Chain-of-Thought 输出（qwen3.5 系列默认支持）。
3. 若需强制结束，在 Prompt 末尾追加 `</think>` 或设置 `max_tokens` 限制思考长度。