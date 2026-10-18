# WhoTalk 品牌落地设计（系统 logo 接入）

- 日期：2026-09-08
- 状态：已获用户确认（方案 A）
- 输入：`/Users/join/Desktop/系统logo.jpg`（2172×724 JPG，纯黑底，无透明通道）

## 1. 背景与决策记录

用户提供新 logo：左侧双色缠绕火焰图形（蓝冷焰 + 橙红热焰 + 金黄顶尖），
右侧 "WhoTalk" 彩色渐变大字 + "AI ACADEMY" 银色副标。

已确认的产品决策：

| 决策点 | 结论 |
|---|---|
| 系统名 | 全面改叫 **WhoTalk**（原 ZHICE·OS / zhice-OS V2 全部替换） |
| 副标语 | 跟 logo：**AI ACADEMY**（原「量化交易系统」不再作为登录页 tagline） |
| 技术路线 | **方案 A：位图保真 + mix-blend-mode: screen 融合**（否决：B 矢量重绘失真大 / C 只改名不用图） |

调研结论（决策依据）：

- 现有品牌位全部是**纯文字** "ZHICE·OS"，无任何图片 logo 资产；`public/` 仅有一个与品牌无关的通用紫色折线 favicon.svg
- 前端 4 套主题（carbon/aurora/emerald/amber）**全深色底** → 纯黑底 logo 可用 screen 混合模式零成本融入，无需抠图，无需浅色适配
- 后端代码无用户可见的 "ZHICE·OS/智策" 字样（仅包名 zhice_os_v2）→ 改名范围锁定前端
- 现有测试无 "ZHICE" 文本断言 → 改名对测试冲击面小

## 2. 资产与生成

一次性脚本 `scripts/make-logo-assets.py`（PIL，可重复执行，换 logo 图重跑即可），
从源图生成 4 个文件到 `frontend/public/`：

| 文件 | 规格 | 用途 |
|---|---|---|
| `logo.png` | 横版整图压缩至宽 ~1200，**透明底**（un-screen） | 登录/注册/大屏大展示位 |
| `logo-icon.png` | 512×512 方形，**透明底**（un-screen） | 侧边栏 icon |
| `apple-touch-icon.png` | 180×180 同源缩放，纯黑底 | iOS 主屏图标 |
| `favicon-32.png` | 32×32 同源缩放，纯黑底 | favicon |

方形裁剪算法：限定图像左半区域，按灰度阈值检测非黑像素的 bounding box，
取火焰图形边界，外扩 ~6% padding，居中出正方形（裁剪时背景保持纯黑，
页面内用的变体随后按 §4 做透明化）。

`public/favicon.svg`（旧紫色折线图）删除。

## 3. 品牌位改动明细

| 位置 | 文件:行 | 现在 | 改成 |
|---|---|---|---|
| 标签标题 | `frontend/index.html:7` | `zhice-OS V2` | `WhoTalk · AI Academy` |
| favicon | `frontend/index.html:6` | `/favicon.svg` | `/favicon-32.png`（PNG）+ 新增 apple-touch-icon link |
| 登录页品牌区 | `src/pages/LoginPage.tsx:60` | `ZHICE·OS` 40px 文字 | `<img src="/logo.png">` 宽 ~240px + `mix-blend-mode: screen` |
| 注册页品牌区 | `src/pages/RegisterPage.tsx:65` | 同上 | 同上 |
| 登录/注册 tag 行 | 同上两文件 | `auth-brand-tag`「量化交易系统」 | **删除该行**（横版图内已含 AI ACADEMY 副标）；下方 brandSub「四市场·全自动」与 markets chips 保留 |
| 侧边栏顶部 | `src/layouts/WorkspaceLayout.tsx:89` | `ZHICE·OS` 文字按钮 | 火焰 icon 20px（screen）+ `WhoTalk` 文字；保留点击回大屏与 title 提示 |
| 首页大屏 | `src/pages/home/HomeBigscreen.tsx:161` | `ZHICE·OS` 文字 | `logo.png` 宽 ~200px（screen）；下方 `logo-sub`「量化交易指挥中心」**保留**（功能描述，非品牌 tagline） |

## 4. 视觉适配策略

- 核心手法：**生成资产时用 PIL 反解 screen（un-screen）产出真透明 PNG**。
  黑底图等效于彩色内容 screen 叠加在纯黑上，故取 alpha = max(R,G,B)、RGB 按 alpha
  反预乘还原，即得透明底资产。深色背景上观感与 CSS `mix-blend-mode: screen` 等效，
  且规避层叠上下文隔离问题（`.auth-brand` 有 `position:relative; z-index:1`，
  会把 CSS blend 与页面背景光晕隔离，导致黑色矩形仍然可见）
- `logo.png` / `logo-icon.png`（页面内用）走透明化；`favicon-32.png` /
  `apple-touch-icon.png`（浏览器标签/iOS 主屏用）保留纯黑底——浏览器标签栏可能为浅色，
  黑底方块对比最强
- 4 套主题全适用（透明资产无背景色依赖）
- 图片加载失败时靠 `alt="WhoTalk AI Academy"` 兜底，无需额外错误处理

## 5. 文案与 i18n

- 品牌名 "WhoTalk" **硬编码**在组件中（与现状 `ZHICE·OS` 硬编码模式一致，专有名词不进 i18n）
- `login.brandTag` key（zh/en 两处）随 tag 行删除而删除
- 大屏 `home.subtitle`（量化交易指挥中心）保留不动
- 后端零改动

## 6. 测试与验收

1. `vitest` 全量：确认 img 替换文字不破坏 Login/Register 相关测试（现状无 ZHICE 断言）
2. Playwright 实拍：登录页 / 侧边栏 / 大屏三处 + 4 主题轮换截图，人工确认融合效果
3. favicon：浏览器网络面板实际请求 `/favicon-32.png` 200 验证
4. `tsc` 通过

## 7. 范围外（本次不做）

- Telegram bot 名、后端报告/推送文案中的品牌露出
- 远程生产环境（113/159）部署——logo 改动随下次常规部署走
- README/docs 中的旧系统名批量替换
- logo 源文件（AI/设计稿）管理
