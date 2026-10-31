# Hermes ACAD1（司马徽）扩展包 · 构建流水线 v1

> 日期：2026-10-31 ｜ 任务：P2-3（Hermes 司马徽扩展构建流水线/版本化）
> 产物：`Hermes/extensions/legion-ide/zhice/`（Zhice 研究台生产构建，Hermes WebUI 同源静态扩展）
> 版本：`20261031_134907_f4230a89` ｜ 源 commit：`f4230a892e2d`（源码树 dirty）
> 边界：未改 Hermes 核心、未改 CSP、未重启 8787、未动 8796。

## 1. 一条命令重建

```powershell
cd C:\项目\老田的AI助手
python tools\hermes_acad1_build.py
# 或（ASCII-only 薄包装，等价）
powershell -NoProfile -ExecutionPolicy Bypass -File tools\hermes_acad1_build.ps1
```

常用变体：

```powershell
python tools\hermes_acad1_build.py --verify-only          # 只复核现有产物/清单/静态路径
python tools\hermes_acad1_build.py --publish-live        # 构建后把 zhice/ 覆盖发布到 8787 生产扩展目录
python tools\hermes_acad1_build.py --api-base http://127.0.0.1:8105/api/v1
python tools\hermes_acad1_build.py --json                # 机器可读回执
```

前置：Node.js ≥ 20（本机实测 `24.20.0`），且源码目录已 `npm ci`
（`C:\项目\zhice-os-v2-windows-20260920\code\frontend`）。构建脚本本身只用 Python 3 标准库。

## 2. 固定构建参数（写死在脚本默认值，可用 CLI 覆盖）

| 变量 | 值 | 用途 |
|---|---|---|
| `VITE_BASE` | `/extensions/zhice/` | Hermes `/extensions/zhice/` 子路径 |
| `VITE_ROUTER` | `hash` | hash 路由（静态托管无 history fallback） |
| `VITE_ENABLED_MARKETS` | `CN,US,HK,CRYPTO` | 启用市场（四市场） |
| `VITE_API_BASE` | `http://127.0.0.1:8105/api/v1` | 生产后端（127.0.0.1:8105，CSP connect-src 放行段） |

## 3. 构建产物与版本清单

| 项 | 值 |
|---|---|
| 产物目录 | `C:\项目\老田的AI助手\Hermes\extensions\legion-ide\zhice` |
| 文件数 / 体积 | 8 个 / 2.2 MB |
| 版本清单 | `zhice/VERSION.json`（sha256 `6a02f6a141224cff7fb89ae01b8f447d3515f32458190ef56fb0c73a13e94270`） |
| 构建时间 | 2026-10-31T13:49:15+08:00（耗时 7.8s） |
| 源 commit | `f4230a892e2da0650eea78bcb819fc680fe3eb8d`（branch `master`，dirty=True，56 个变更文件） |
| 源码树摘要 | `94b05da8a2adb05f8d913314ccfea68bf9b2a9cd586d2f62f343ff2726fd76fa`（168 文件 / 1.5 MB） |
| 构建工具 | node v24.20.0 / vite 8.2.1 / tsc 6.0.3 |

产物文件 sha256：

| 文件 | 字节 | sha256 |
|---|---|---|
| `apple-touch-icon.png` | 24068 | `3495cab7606f62196f7bd5d7bd8c24f5f2987fd16afbf7642ea1f34ce10cd907` |
| `assets/index-BHhUDjC8.js` | 1846588 | `f59f62cfb7ef443e3c6042b7e87ce326665a337c368f6290ae9cebb1b040ea0e` |
| `assets/index-Dl42rMEp.css` | 16229 | `1948d2a4ae5b63a8a34443775411e612eb8f1c886db89949a2b8f98420f53a84` |
| `favicon-32.png` | 1570 | `bbd3795f5d8104a80ec23fc6647fd6f8b6eaf2d16009d4319dce4eb2b4b39e91` |
| `index.html` | 620 | `4948501c8cc7391739b86c843f60aa8a94911714c95c78e69ccb34696817f1f6` |
| `logo-icon.png` | 145097 | `8ea7de811a2f5190e3af54232c698e004a4404ee0fa62489bd683f6148480dc4` |
| `logo.png` | 34946 | `05ade2be5d4e85d94b0a9eb40c72cf9da7d8a0255173e79ae895d5567e73b667` |
| `logo.whotalk.bak.png` | 223238 | `4863b2446c51afb085a8bef1be6ce0dae99eb1ab5e80c5385151fe0dc49d9f9f` |

`VERSION.json` 字段：`schema/version/built_at/source/build/output/patch/verify/live_publish/live_probe`，可离线核对源码 commit、时间、每个文件 hash 与补丁计数。

## 4. 为什么构建后要打补丁

Zhice 源码有 4 处**相对** `/api/v1` 写死调用 + 2 处**根绝对** logo 路径，直接构建到 `/extensions/zhice/` 会打到 Hermes 源（404）或 8787 根（logo 404）。脚本按字节替换（不改 UTF-8 编码、不改行尾）：

| 规则 | 替换 | 次数 |
|---|---|---|
| WebSocket host | `://${location.host}/api/v1` → `://<api-host>/api/v1` | 1 |
| 相对 API → 绝对 | `/api/v1...` → `http://127.0.0.1:8105/api/v1...` | 4 |
| logo 根路径 | `/logo.png`、`/logo-icon.png` → `/extensions/zhice/logo*.png` | 2 |

> 旧手工复制流程的缺陷（本脚本已消除）：PowerShell 文本读写把 LF 变 CRLF；WS `location.host` 未改（连到 8787 源）；`/logo*.png` 未改（8787 根 404）；无版本清单、不可审计、每次重建结果不可比对。

补丁后断言（全部通过）：相对 `/api/v1` = 0、`location.host` WS = 0、相对 logo = 0、`index.html` 全部资源引用存在、无根绝对资产引用、CSS 无相对 url/API。

## 5. 验证（本单实测）

### 5.1 构建产物 + 清单自洽

- 逐文件 sha256 重算与 `VERSION.json` 一致：✅
- `index.html` 引用可解析、补丁断言通过：✅
- 可重现：**同参数两次重建**产物逐字节一致（sha256 相同）；不同 `VITE_API_BASE` 会改变 JS 内容 hash（属预期）

### 5.2 静态路径复现（生产 Hermes `/extensions/` handler，本机临时端口）

- 模式：`production-handler`（C:\项目\老田的AI助手\Hermes\hermes-webui-next\api\extensions.py）｜ 结果：✅ 全绿
- 逐文件校验：HTTP 200 + `Content-Type` + `Cache-Control: no-store` + 字节级 sha256一致（6 个路径），`%2e%2e` 穿越请求 404：✅

### 5.3 8787 在线探针（只读，不重启）

- `GET http://127.0.0.1:8787/health`：HTTP 200；进程 `server_started_at` = 1793409096.180473（构建/发布前后未变 ⇒ 未重启）
- `GET /extensions/app.js`（既有入口资产）：HTTP 200，sha256 `e90997188aafb892`
- `GET /extensions/zhice/index.html`：HTTP **200**；zhice 全量资源字节级一致：✅ 8787 已直接可读（无需重启）
- 直连 8787 不带 trusted-header 会 302 到 `/login`（认证行为未变；8734 nginx 在 auth_request 通过后注入 `X-Portal-User`）

### 5.4 生产扩展目录发布（lead 批准的 A 方案：add-only）

- 生产扩展根（launcher env）：`C:\项目\_vault\legion_ide\release\legion-ide\current`
- junction/链接解析目标：`C:\项目\_vault\legion_ide\release\legion-ide\20261031_092952_7e6dbe8a`（is_junction=True）
- 新增 `zhice/`：8 个文件 / 2.2 MB，目录 `C:\项目\_vault\legion_ide\release\legion-ide\20261031_092952_7e6dbe8a\zhice`，发布时刻 2026-10-31T13:49:17+08:00
- 发布方式：新增 `zhice/` 子目录；现有 app.js/app.css/a0-mainchat.js/README.md/manifest.json **未改**（发布前后 sha256 对账：✅ 一致）
- 发布目录/时间/hash 明细：见 `MANIFEST.json` 的 `live_publish` 与 `VERIFY.json`；发布前 manifest 备份 `live-manifest-before.json`
- 回退：删除该目录下 `zhice/` 即回到发布前状态（junction 每请求解析，无需重启）

## 6. 8787 生效/回归路径（本单只做静态可读，不改入口 wiring）

- 运行中的 8787 通过 `HERMES_WEBUI_EXTENSION_DIR` 指向 `C:\项目\_vault\legion_ide\release\legion-ide\current`（junction，每请求解析 → 放入文件即生效，**无需重启**）。
- 本单只**新增**了 `zhice/` 覆盖层，未改 `start_hermes_*.py`、未改 app.js 的司马徽入口（入口仍按现状工作）。若要让「司马徽」面板 iframe 指向 `/extensions/zhice/index.html#/cn/replay?tab=selection`，属入口 wiring 变更，需另单（涉及已 tracked 的 app.js 与 release 发布流程）。
- ⚠️ 发布 overlay 的持久性：`current` 每次 promote 会切到只含 4 个白名单文件的新 release 目录，`zhice/` 覆盖层需要重新 `--publish-live`；`tools/release_legion_ide.py` 的 `FILES` 白名单（4 文件）与 `Hermes/*` gitignore 目前都不含 zhice —— 长期方案需扩展 release 工具（本单未改，避免扩大范围）。

## 7. 回滚

```powershell
# 1) 仅回退 8787 覆盖层（生产扩展目录里删 zhice/，回收站删除）
python -c "from pathlib import Path; import sys; sys.path.insert(0, 'tools'); import hermes_acad1_build as b; print(b.rollback_live(Path(r'C:\项目\_vault\legion_ide\release\legion-ide\current')))"
# 2) 回退工作树产物：从 Hermes/extensions/legion-ide/_build_backup/ 取上一版 zhice
# 3) 彻底回退：revert 本单提交（tools/* + deliverables/*），删除 zhice/ 与 _build_backup/
```

## 8. 已知风险 / 残余项（未在本单范围）

1. **CSP**：生产 CSP `connect-src` 仅放行 `http://127.0.0.1:*` / `ws://127.0.0.1:*`。本单 API base 已按 lead 指示改为 `http://127.0.0.1:8105/api/v1`（在放行段内）；若未来改回 `127.0.0.2:*` 或其它 host，会被 CSP 拦截（不改 CSP 前提下不可用）。
2. **8105 后端**：本单只做静态构建/托管验证；`127.0.0.1:8105` 的 API 可用性/数据由lead 的后端批次负责（构建不依赖它在线）。
3. **token 存储**：SPA 将 `zhice_token` 写入 Hermes 源 localStorage（同源 iframe 共享存储域），属既有安全边界，未改。
4. **双源漂移**：5175 dev 与 `/extensions/zhice/` 构建可能漂移；本流水线以「同参数重建 + hash 清单」保证构建可重建，但源码 dirty 状态需一并留档（`source-dirty.diff`、`source-untracked-files.json`）。
5. **git 边界**：`Hermes/*` 被 gitignore，`zhice/` 产物与 `_build_backup/` 不进库；可审计版本信息以本 deliverables 清单 + `VERSION.json` 为准。

## 9. 源可重现信息

- 源码仓库：`C:\项目\zhice-os-v2-windows-20260920`（branch `master`，HEAD `f4230a892e2da0650eea78bcb819fc680fe3eb8d`）
- dirty 文件数：56（清单见 `source-untracked-files.json` / `source-dirty.diff`）
- 源码树摘要（src/public/构建配置）sha256：`94b05da8a2adb05f8d913314ccfea68bf9b2a9cd586d2f62f343ff2726fd76fa`

## 10. 本目录文件

| 文件 | 说明 |
|---|---|
| `README.md` | 本文件（使用/验证/回滚/风险） |
| `MANIFEST.json` | 机器可读完整清单（源 commit/dirty/时间/产物 hash/补丁计数/验证/发布） |
| `VERIFY.json` | 本次验证原始结果（静态路径 + 8787 探针 + 文件对账） |
| `build-20261031_134907_f4230a89.log` | 构建/补丁命令完整输出（含 rc/耗时） |
| `source-dirty.diff` | 源码树 tracked 变更 diff（快照包无干净 HEAD，重建需同 dirty 树） |
| `source-untracked-files.json` | 源码树 untracked 文件 hash（含 zhice 前端新增页） |
| `live-manifest-before.json` | 发布前生产扩展 manifest 备份（A 方案证据） |

---
_本文件由 `tools/hermes_acad1_build.py` 自动生成；重建会覆盖。_
