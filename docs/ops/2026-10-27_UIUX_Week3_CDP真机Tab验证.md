# UI/UX Week3 真机 CDP 验证回执（Tab 顺序 + 命令面板 + 密度）

- 日期：2026-10-27
- 工单：`WO-LEGION-UIUX-W3R-20261027`
- 方法：Edge headless + `--remote-debugging-port=9333` + Python `websocket` 走 CDP：
  `Input.dispatchKeyEvent` 实际按 Tab / Ctrl+K / Enter，`Runtime.evaluate` 读取
  `document.activeElement` 与 `data-wb-density`。
- 地址：`http://127.0.0.1:8734/os-entry`

## 1. 真机 Tab 顺序（修复后）

```text
#1  A  .wb-skip        跳到主要内容
#2  A  .wb-nav-back    ← 返回
#3-10  A .wb-nav-flow  🤖晨报 / 🌅盘前 / 🚦信号 / 📺大屏 / 🌇盘后 / 📰日报 / 🧾模拟仓 / 🏛门面
#11-12 A .wb-nav-pill  🏛零界门面 / 🗂门户
#13-17 SUMMARY .wb-nav-pill  量化 / 情报 / 创作 / 开发 / 系统（当前）
#18 BUTTON active     诸葛亮 · 今日建议
#19 BUTTON            工作平台
#20 BUTTON            今日结果
```

- **skip link 是第 1 个 Tab 落点**；后续按顶栏动线 → 分区 → 页面视图按钮；
- 修复前真机抽查发现 nav.js 把顶栏插到 body 最前，skip link 被挤到第 17 位；
  已修：`tools/export_nav.py` 改为插在 `.wb-skip` 之后（提交 `b0427ded0`），
  并加 `tools/test_nav_skip_order.py` 回归。

## 2. 命令面板（真机）

```text
Ctrl+K          → palette.hidden = false
输入“紧凑”       → visible items = 1
Enter           → data-wb-density = compact，palette.hidden = true
```

- 打开/过滤/执行/关闭全链在真实浏览器通过；
- 命令仅视图/导航/刷新/密度，无危险动作、无 storage、无网络请求。

## 3. 验证命令

```powershell
# 启动 CDP（测试用；结束后 taskkill 该 PID 树）
msedge --headless=new --disable-gpu --no-sandbox \
  --remote-debugging-port=9333 --user-data-dir=C:\Temp\edge_cdp_prof \
  http://127.0.0.1:8734/os-entry

# Python CDP 脚本（临时）：Tab ×20 + Ctrl+K + 输入+Enter
python C:\Temp\cdp_tab.py
```

## 4. 仍需人工（少量）

- Shift+Tab 反向顺序、Esc 关闭后焦点归还（CDP 已发 Esc 逻辑但未逐项断言）；
- 375px 移动端布局；
- 建议真实浏览器 3 分钟抽查，以上自动结果作为基线。

## 5. 结论

Week3 的「命令面板 / 密度切换 / 键盘入口」已在真实浏览器验证；skip link 顺序缺陷已修复并加回归，无业务逻辑改动。
