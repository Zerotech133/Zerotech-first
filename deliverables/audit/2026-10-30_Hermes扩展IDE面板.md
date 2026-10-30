# Hermes 扩展 IDE 面板（方式 A）

- 日期：2026-10-30
- 状态：**8788 原型已跑通**
- 范围：Hermes 官方扩展面 + 现有 OH API

## 结论

**可以，而且比 Tauri 更合适。** Hermes 官方支持扩展注入：

```text
HERMES_WEBUI_EXTENSION_DIR
HERMES_WEBUI_EXTENSION_SCRIPT_URLS
HERMES_WEBUI_EXTENSION_STYLESHEET_URLS
```

不需要 fork `ui.js` / `panels.js`。

## 已实现

扩展目录：

```text
Hermes/extensions/legion-ide/
  app.js
  app.css
  README.md
```

panel 功能：

- 「⚔ 军团 IDE」右下角入口
- 工单下拉（38 个 doing/review 工单）
- 模型选择
- 开始 / 停止
- 实时 Action / Observation
- 危险命令「批准 / 拒绝」
- 最终答复
- diff + 确认提交

## 接入方式

- 8788 原型：`start_hermes_next.py` 继承扩展环境变量
- 生产 8787：在 `start_hermes_bg.py` env 加同样三项，重启后生效
- 未修改 Hermes 核心源码

## 实测

```text
http://127.0.0.1:8788/
→ 右下角「⚔ 军团 IDE」
→ 工单 K-QUANT-20261030-OHLOOP-TEST
→ Create HERMES_PANEL_OK.txt
→ ActionEvent TerminalAction
→ 等待确认 → 批准
→ ✅ 完成
→ 宿主 worktree 出现 HERMES_PANEL_OK.txt
```

## 对比 Tauri

| 维度 | Hermes 扩展 | Tauri 壳 |
|---|---|---|
| 入口 | 同 Hermes | 新 exe |
| 会话/审批/PWA | 复用 | 重做 |
| 维护 | 一个扩展目录 | 前端 + 壳 + 通信 |
| 当前可运行 | ✅ 8788 | ❌ WebView2 受限 |

## 下一步

1. 在 8787 启用扩展（需重启 Hermes）
2. 先保持 8788 作为验证环境
3. 后续把 8799 过渡网关切回 8796
4. Tauri 降为可选 PWA/桌面封装，不再主攻
