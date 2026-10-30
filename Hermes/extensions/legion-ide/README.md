# 军团 IDE · Hermes 扩展

方式 A：作为 Hermes WebUI 扩展运行，不修改 Hermes 核心。

## 启用（8788 原型）

```powershell
$env:HERMES_WEBUI_EXTENSION_DIR='C:\aios\Hermes\extensions\legion-ide'
$env:HERMES_WEBUI_EXTENSION_SCRIPT_URLS='/extensions/app.js'
$env:HERMES_WEBUI_EXTENSION_STYLESHEET_URLS='/extensions/app.css'
python C:\aios\Hermes\start_hermes_next.py
```

打开：

```text
http://127.0.0.1:8788/
```

右下角出现「⚔ 军团 IDE」按钮。

## 生产 8787

在 `Hermes/start_hermes_bg.py` 的 env 中加同样三项，然后重启 Hermes 8787。  
**不要直接覆盖核心 static 文件**；本扩展只用官方 `/extensions/` 注入面。

## 原生 panel

- Hermes rail / sidebar 新增「军团 IDE」入口
- 点击进入 `#mainLegionIde` 主视图，不是遮罩
- Chat 与军团 IDE 可正常互切
- 右下角悬浮按钮保留为 fallback

## PWA 桌面入口

```text
C:\Users\t\Desktop\零界Hermes（PWA）.lnk
```

目标：

```text
msedge.exe --app=http://127.0.0.1:8734/hermes/
  --user-data-dir=%LOCALAPPDATA%\LegionHermes\EdgeProfile
```

8734 的 `/extensions/` 已加 nginx 同源反代到 8787。

## 功能

- Agent 卡片（63 个，来自 `/agent_ide_bundle.json` 或 `/api/ide/agents`）
- 点击 Agent 自动切换模型
- Agent 详情：职责/工具/技能/prompt
- 历史会话列表：服务端持久化 + 本地兜底 / 恢复 / 删除 / 新会话
- SSE 实时事件（失败自动回退轮询）
- 原生文件浏览器：目录下钻 / 点文件读取
- 代码片段注入：文件读取 + 鼠标选区
- 段落级自动上下文：AST symbol + 邻近 diff
- 旧 IDE 选中桥（`?standalone=1&legion_pick=1`）
- 剪贴板引用兜底（复制旧 IDE 内容 → 一键插入）
- 取上下文（AST symbols）
- 工单下拉（来自 `/api/ide/openhands/tasks`）
- 模型选择
- 开始/停止 OpenHands
- 实时 Action/Observation
- 危险命令「批准/拒绝」
- 最终答复
- diff + 确认提交到工单

## API 地址

默认：

```text
http://127.0.0.1:8796
```

可在页面加载前覆盖：

```html
<script>window.LEGION_OH_BASE = "http://127.0.0.1:8796";</script>
```

## 实测

```text
Hermes 8788 → 右下角按钮 → panel
任务：Create HERMES_PANEL_OK.txt
→ OH 事件显示
→ 危险 shell 等待确认 → 批准
→ ✅ 完成
→ 宿主 worktree 出现 HERMES_PANEL_OK.txt
```

