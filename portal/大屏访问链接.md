# 量化策略数据大屏 · 访问链接

> 生成时间：2026-10-05 · 网关：本机 nginx（Docker ai-portal）:8080 → 门户 API :8091
> 数据源 `/api/screen`（60 秒缓存），页面每 60 秒自动刷新，无需手动刷新。

## 四块市场大屏

| 大屏 | 访问链接 | 数据范围 |
|---|---|---|
| 🇨🇳 A 股大屏（默认） | http://localhost:8080/screen | 仅 A 股：持仓/平仓/买卖点预警/缠论池/快讯全 A 股 |
| 🇺🇸 美股大屏 | http://localhost:8080/screen?mkt=US | 仅美股标的与美股快讯 |
| 🇭🇰 港股大屏 | http://localhost:8080/screen?mkt=HK | 仅港股标的与港股快讯 |
| 🪙 加密大屏 | http://localhost:8080/screen?mkt=CRYPTO | 仅加密标的与加密快讯 |
| 🌐 全部市场（混合） | http://localhost:8080/screen?mkt=ALL | 四市场混合视图（旧默认） |

## 局域网 / 远程访问（实测可达）

本机地址：**局域网 192.168.2.105**（WLAN）· **Tailscale 100.110.192.72**（异地组网，任何装了 Tailscale 的设备可用）。

| 大屏 | 局域网链接 | Tailscale 链接 |
|---|---|---|
| 🇨🇳 A 股 | http://192.168.2.105:8080/screen | http://100.110.192.72:8080/screen |
| 🇺🇸 美股 | http://192.168.2.105:8080/screen?mkt=US | http://100.110.192.72:8080/screen?mkt=US |
| 🇭🇰 港股 | http://192.168.2.105:8080/screen?mkt=HK | http://100.110.192.72:8080/screen?mkt=HK |
| 🪙 加密 | http://192.168.2.105:8080/screen?mkt=CRYPTO | http://100.110.192.72:8080/screen?mkt=CRYPTO |

> 注：本机 IP 若由路由器重新分配（DHCP）会变化，以届时 `ipconfig` 结果为准；Tailscale 地址固定不变。

## 使用说明

- **市场切换**：每块大屏顶部「市场切换」卡行可点击跳转对应市场大屏（选中卡蓝色高亮）。
- **标题可辨**：每块大屏页签与页头显示市场名（如「量化策略数据大屏 · 美股」），多屏摆放一眼可辨。
- **快讯跟随市场**：财经快讯面板默认筛选本市场，面板内 chips 可临时切换。
- **独立地址**：每块大屏 URL 相互独立，可分别收藏、可分屏各放一台显示器。
- **局域网访问**：同网段其他设备将 `localhost` 换成本机 IP 即可（如 `http://192.168.x.x:8080/screen?mkt=US`，需防火墙放行 8080）。

## 相关页面

| 页面 | 链接 |
|---|---|
| 买卖点全屏（当日全量信号） | http://localhost:8080/screen-sig |
| 门户首页 | http://localhost:8080/ |
| 大屏数据接口（JSON） | http://localhost:8080/api/screen |
