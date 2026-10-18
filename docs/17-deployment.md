# 17 · 本机部署

## 当前部署（2026-08-18）

| 服务 | 地址 | 说明 |
|---|---|---|
| V2 API | http://127.0.0.1:8102 | FastAPI 后端，内存持久化 |
| V2 Web | http://127.0.0.1:5174 | Vite 前端开发服务器 |
| V2 Scheduler | 后台运行 | APScheduler 调度器 |

API 健康检查：

```text
http://127.0.0.1:8102/api/v1/health
```

前端页面：

```text
http://127.0.0.1:5174
```

## 启动方式

### 后端

```bash
cd /Users/join/Desktop/zhice/zhice-os-v2
PERSISTENCE_BACKEND=memory nohup .venv/bin/python -m uvicorn \
  zhice_os_v2.api.main:app --host 127.0.0.1 --port 8102 \
  > /tmp/zhice_os_v2_api.log 2>&1 &
```

### 前端

```bash
cd /Users/join/Desktop/zhice/zhice-os-v2/frontend
VITE_PROXY_TARGET=http://127.0.0.1:8102 npm run dev -- --host 127.0.0.1 --port 5174
```

### 调度器

```bash
cd /Users/join/Desktop/zhice/zhice-os-v2
PERSISTENCE_BACKEND=memory nohup .venv/bin/python -m zhice_os_v2.scheduler \
  > /tmp/zhice_os_v2_scheduler.log 2>&1 &
```

## 停止方式

```bash
lsof -nP -iTCP:8102 -sTCP:LISTEN
lsof -nP -iTCP:5174 -sTCP:LISTEN
kill <pid>
```

调度器进程按实际 PID 停止。
