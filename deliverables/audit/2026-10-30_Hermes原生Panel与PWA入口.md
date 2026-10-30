# Hermes 原生 Panel + PWA 桌面入口

- 日期：2026-10-30
- 工单：K-QUANT-20261030-HERMESPANEL
- 状态：**8787 已实测；8734 同源入口已打通**

## 1. 原生 panel

扩展不再只有悬浮窗：

- Hermes 左侧 rail 新增「军团 IDE」按钮
- 移动/侧栏 nav 同步新增
- 点击进入 `#mainLegionIde` 主视图
- Chat 和军团 IDE 可互相切换
- 悬浮按钮保留为 fallback

实测：

```text
8734/hermes/
nav_buttons=2
rail_visible=True
panel_visible=True
chat_visible=False
工单 35 项
```

切回 Chat：

```text
panel_visible=False
chat_visible=True
```

## 2. PWA 桌面入口

桌面快捷方式：

```text
C:\Users\t\Desktop\零界Hermes（PWA）.lnk
```

目标：

```text
msedge.exe --app=http://127.0.0.1:8734/hermes/
  --user-data-dir=%LOCALAPPDATA%\LegionHermes\EdgeProfile
```

打开后是独立 Edge App 窗口（无地址栏/标签页），Hermes 页面和扩展正常加载。

## 3. 8734 同源修复

Hermes 扩展资源固定从 `/extensions/` 加载，原 nginx 只代理 `/hermes/`，导致 PWA 入口下扩展 404。

新增：

```nginx
location ^~ /extensions/ {
    proxy_pass http://host.docker.internal:8787;
}
```

现验证：

```text
GET /extensions/app.js → 200
GET /hermes/ → 200 且包含 /extensions/app.js
```

## 4. 验收

| 项目 | 状态 |
|---|---|
| 原生 nav 入口 | ✅ |
| 主视图 panel | ✅ |
| Chat 切换恢复 | ✅ |
| 8734 同源扩展资源 | ✅ |
| PWA 独立窗口 | ✅ 已打开 |
| Tauri | 暂停，不再主攻 |

## 8799 → 8796 收口

已完成：

- 8796 旧进程退出后，启动最新代码 8796（pid=39808）
- nginx `/openhands/` 从 8799 改回 8796
- 8799 已停止并从端口注册表移除
- 扩展 API base 从 8799 改为 8796
- Hermes 原生 panel 通过 8796 完成 E2E

最终验证：

```text
GET /openhands/api/ide/openhands/tasks → 200
Hermes panel → OH 执行 → ✅ 完成
宿主 worktree 文件存在
8799 DOWN
```

## 生产 8787

启动器已固化扩展 env；后续重启会继续加载「军团 IDE」panel。
