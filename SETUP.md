# SETUP.md — 零界（老田的AI助手）环境与启动统一口径

> A10 交付（2026-11-10，真相源 v2 阶段 A）。目的：消灭「换台机器/重装后各启动器静默失效」——
> 所有解释器与路径必须可派生或可覆盖，禁止新增写死。改启动器前必读本文。

## 1. 解释器（venv）统一口径

| 用途 | 解释器 | 规则 |
|---|---|---|
| 量化/门户/工具链（portal、quant_backtest、tools、agent_platform） | 系统级 `C:\Program Files\Python311\python.exe` | 唯一装了 playwright/paramiko 的解释器；**任何脚本不得写死其他盘符路径**，统一用这个 |
| Hermes WebUI（8787 生产 / 8788 验证） | Hermes PM venv（Python 3.14.7） | **禁止写死哈希路径**。解析顺序（`_resolve_venv_py()`）：① env `HERMES_WEBUI_PYTHON` 或 `HERMES_VENV_PY` → ② 现役 legacy 路径（回滚资产，见启动器内常量）→ ③ 都不可用则 FATAL exit 2 |
| 重装 `.hermes` 后 | 新 venv | 正确动作 = 设 `HERMES_WEBUI_PYTHON=<新venv>\venv\Scripts\python.exe`，**不要**自动发现 installs/*（PM selected 环境身份敏感，adopt_selected 跨版本重写 sys.path → pydantic_core 崩溃判例） |

`.hermes` 现存 3 个 venv（installs/4cc1a9b84685bce9/environments/{6f13…,b527…,da53…}），
生产用的是 **da53…**（与 PM selected 身份一致）。不要「顺手清理」它们。

## 2. 启动入口清单

| 入口 | 方式 | 说明 |
|---|---|---|
| Hermes 8787 生产 | 计划任务每 30min 触发 `Hermes/start_hermes_bg.py` | 幂等：8787 已监听即 `[skip]` exit 0。重启走 `RESTART_8787.flag` 哨兵或 `restart_hermes_8787.ps1`，**勿手杀 session-0 进程** |
| Hermes 8788 验证 | `Hermes/start_hermes_next.py` | 手动启动；无 RESTART_FLAG，杀进程即可 |
| 8734 统一入口 | `netsh portproxy 8734→8080`（系统 iphlpsvc 承载） | 后端是 Docker 容器 **ai-portal**（portal/ 挂载 → /usr/share/nginx/html）。8080 死 = 8734 死 |
| 8091 量化生产 | `quant_backtest` 内 production 入口 | 另见真相源 v2 |
| OSLINK 8795 / KG 8796 | agent_platform/gateway | 契约见 osleg_http_api.py（CH2 answered_by/answer_ref 必填） |

## 3. 端口速查（本机 127.0.0.1）

| 端口 | 服务 | 健康探针 |
|---|---|---|
| 8734 | 统一入口（portproxy→8080） | `GET /health` 经 nginx |
| 8080 | ai-portal 容器（nginx 静态+API） | `GET /health` |
| 8091 | 量化生产 | — |
| 8787 | Hermes WebUI 生产（trusted-header 认证） | `GET /health`（公共） |
| 8788 | Hermes WebUI 验证 | `GET /health` |
| 8795 | OSLINK gateway | — |
| 8796 | KG API | `GET /api/health`（注意不是 /health） |
| 8097 | push_admin 管理面 | — |

## 4. 环境变量约定（启动器注入，勿手工绕过）

- `HERMES_DISABLE_LAZY_INSTALLS=1`：防 PM shim 转发到裸解释器（缺它守护链 40s 必死）
- `PYTHONUTF8=1`：全链 UTF-8
- `HERMES_WEBUI_TRUSTED_AUTH_HEADER`（默认 `X-Portal-User`）：认证拓扑；`HERMES_AUTH_REQUIRED=1` 下缺失则拒绝拉起
- 代理注入（HTTP_PROXY 等）：启动器一律剥离；Hermes 需 `NO_PROXY=localhost,127.0.0.1`

## 5. 硬规矩（违反即回滚）

1. **禁止新增写死**：盘符绝对路径、venv 哈希、用户名目录，一律 env 覆盖 + 派生（A2/A8 判例）。
2. 启动器守护配方（DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP + stdio 全 DEVNULL）**勿改回文件句柄重定向**——实测 ~1-2 分钟静默死。
3. `hermes-webui-next` = 生产基（改码只改它）；`hermes-webui` = 回滚资产勿动。
4. 改 `start_hermes_bg.py` / `start_hermes_next.py` 后：`py_compile` + 幂等冒烟（8787 在监听时应 `[skip]` exit 0）。
5. 计划任务侧须 AllowStartIfOnBatteries/DontStopIfGoingOnBatteries（K268）。
