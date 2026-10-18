# WhoTalk 品牌落地 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把系统从 "ZHICE·OS / zhice-OS V2" 全面改品牌为 "WhoTalk · AI Academy"——生成 logo 资产并替换前端 5 个品牌位（标签标题、favicon、登录/注册品牌区、侧边栏、首页大屏）。

**Architecture:** 一次性 PIL 脚本从黑底源图生成 4 个资产（横版透明 PNG / 方形透明 icon / 黑底 apple-touch / 黑底 favicon-32），透明化用 un-screen 算法（alpha=max(R,G,B) 反预乘），深色背景上观感等效 CSS screen 混合且无层叠上下文风险。前端替换均为展示层改动，品牌名 "WhoTalk" 硬编码（与原 ZHICE·OS 模式一致，不进 i18n）。

**Tech Stack:** Python 3 + Pillow + numpy（后端 `.venv` 已有，PIL 12.3.0）；React 19 + Vite；vitest + @testing-library/react。

**Spec:** `docs/superpowers/specs/2026-09-08-system-logo-design.md`

**关键背景（执行者必读）:**
- 本机前端 dev server 由宿主 vite 进程运行在 **IPv6 `[::1]:5174`**（不是 5173——5173 是另一套 docker 系统）。`curl 127.0.0.1:5174` 会失败，要用 `curl -g 'http://[::1]:5174/...'` 或 `http://localhost:5174`（Playwright 用 localhost 即可）
- 前端命令一律在 `frontend/` 目录下跑：`npm run test`（vitest run）、`npm run build`（tsc -b && vite build）
- Python 一律用仓库根 `.venv/bin/python`（系统 python3 无依赖保证）
- 改前端源码后 vite 热重载自动生效，无需重启
- 现有测试**没有**任何 "ZHICE" 文本断言（已核实），改名不会碰旧断言

---

### Task 0: 建分支

**Files:** 无（仅 git 操作）

- [x] **Step 0.1: 从 main 建特性分支**

```bash
cd /Users/join/Desktop/zhice/zhice-os-v2 && git checkout -b feat/whotalk-logo
```

Expected: `Switched to a new branch 'feat/whotalk-logo'`

---

### Task 1: logo 资产生成脚本

**Files:**
- Create: `scripts/make-logo-assets.py`
- Create（脚本产出）: `frontend/public/logo.png`, `frontend/public/logo-icon.png`, `frontend/public/apple-touch-icon.png`, `frontend/public/favicon-32.png`
- 源图输入：`/Users/join/Desktop/系统logo.jpg`（2172×724 黑底 JPG）

- [x] **Step 1.1: 写脚本**

创建 `scripts/make-logo-assets.py`，完整内容：

```python
#!/usr/bin/env python3
"""从 WhoTalk logo 源图生成前端品牌资产。

输入：横版黑底 logo（左火焰图形 + 右 "WhoTalk / AI ACADEMY" 文字）
输出（frontend/public/）：
  - logo.png              宽 1200 横版，透明底（un-screen 抠黑）
  - logo-icon.png         512x512 火焰方形，透明底（un-screen）
  - apple-touch-icon.png  180x180 火焰方形，纯黑底
  - favicon-32.png        32x32 火焰方形，纯黑底

可重复执行：换新源图后重跑即可覆盖。依赖 Pillow + numpy（后端 venv 自带）。
用法：.venv/bin/python scripts/make-logo-assets.py [源图路径]
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

REPO = Path(__file__).resolve().parent.parent
SRC_DEFAULT = Path('/Users/join/Desktop/系统logo.jpg')
OUT_DIR = REPO / 'frontend' / 'public'

# max(R,G,B) 低于此值视为纯黑（压掉 JPEG 噪点）
BLACK_THRESHOLD = 24
# 火焰方形图的黑底 padding 比例
PAD_RATIO = 0.06


def unscreen(img: Image.Image) -> Image.Image:
    """把黑底图反解成透明底。

    黑底图等效于彩色内容以 screen 叠加在纯黑上：
    alpha = max(R,G,B)，RGB 按 alpha 反预乘还原。
    深色背景上的观感与 CSS mix-blend-mode: screen 一致，
    且不受层叠上下文隔离影响。
    """
    arr = np.asarray(img.convert('RGB')).astype(np.float32)
    alpha = arr.max(axis=2)
    alpha[alpha < BLACK_THRESHOLD] = 0
    safe = np.where(alpha > 0, alpha, 1.0)
    rgb = (arr / safe[..., None] * 255.0).clip(0, 255)
    out = np.dstack([rgb, alpha]).astype(np.uint8)
    return Image.fromarray(out, 'RGBA')


def flame_square(src: Image.Image) -> Image.Image:
    """检测左半区火焰边界，裁黑底正方形（含 6% padding）。"""
    w, h = src.size
    left = src.crop((0, 0, w // 2, h)).convert('RGB')
    arr = np.asarray(left)
    mask = arr.max(axis=2) > BLACK_THRESHOLD
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        sys.exit(f'错误：左半区未检测到火焰图形（阈值 {BLACK_THRESHOLD}）')
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
    bw, bh = x1 - x0 + 1, y1 - y0 + 1
    side = int(max(bw, bh) * (1 + 2 * PAD_RATIO))
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    half = side // 2
    square = left.crop((cx - half, cy - half, cx + half, cy + half))
    # 裁剪越界部分 PIL 自动填黑；pad 再兜底一次尺寸
    return ImageOps.pad(square, (side, side), method=Image.LANCZOS, color=(0, 0, 0))


def main() -> None:
    src_path = Path(sys.argv[1]) if len(sys.argv) > 1 else SRC_DEFAULT
    src = Image.open(src_path)
    print(f'源图：{src_path} {src.size[0]}x{src.size[1]}')

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 1) 横版：透明底，压到宽 1200
    banner = unscreen(src)
    if banner.width > 1200:
        h = round(banner.height * 1200 / banner.width)
        banner = banner.resize((1200, h), Image.LANCZOS)
    banner.save(OUT_DIR / 'logo.png', optimize=True)

    # 2) 火焰方形：页面内用透明底，浏览器图标用黑底
    square = flame_square(src)
    unscreen(square).resize((512, 512), Image.LANCZOS).save(OUT_DIR / 'logo-icon.png', optimize=True)
    square.resize((180, 180), Image.LANCZOS).save(OUT_DIR / 'apple-touch-icon.png', optimize=True)
    square.resize((32, 32), Image.LANCZOS).save(OUT_DIR / 'favicon-32.png', optimize=True)

    for name in ('logo.png', 'logo-icon.png', 'apple-touch-icon.png', 'favicon-32.png'):
        p = OUT_DIR / name
        print(f'{name}: {p.stat().st_size // 1024} KB')


if __name__ == '__main__':
    main()
```

- [x] **Step 1.2: 运行脚本**

```bash
cd /Users/join/Desktop/zhice/zhice-os-v2 && .venv/bin/python scripts/make-logo-assets.py
```

Expected: 打印源图尺寸 2172x724 + 4 个文件各自 KB 数，退出码 0。若报 "未检测到火焰图形" 说明阈值或左半区假设不成立，需肉眼核查源图后调整（不可静默跳过）。

- [x] **Step 1.3: 验证产出（尺寸 + 透明通道 + 视觉）**

```bash
.venv/bin/python - <<'EOF'
from PIL import Image
for name, want in [('logo.png', 'RGBA'), ('logo-icon.png', 'RGBA'),
                   ('apple-touch-icon.png', 'RGB'), ('favicon-32.png', 'RGB')]:
    im = Image.open(f'frontend/public/{name}')
    assert im.mode == want, f'{name}: mode {im.mode} != {want}'
    print(name, im.size, im.mode)
im = Image.open('frontend/public/logo-icon.png')
a = im.getchannel('A')
print('icon alpha min/max:', a.getextrema())  # 期望 min=0（有透明区）
EOF
```

Expected: `logo.png (1200, 400) RGBA`；`logo-icon.png (512, 512) RGBA` 且 alpha min=0；apple-touch/favicon 为 RGB。

再用 Read 工具肉眼看 `frontend/public/logo-icon.png` 与 `frontend/public/logo.png`：火焰完整、文字清晰、背景透明（棋盘格）。

- [x] **Step 1.4: 提交**

```bash
git add scripts/make-logo-assets.py frontend/public/logo.png frontend/public/logo-icon.png frontend/public/apple-touch-icon.png frontend/public/favicon-32.png
git commit -m "feat(brand): WhoTalk logo 资产生成脚本与产出

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: index.html（标题 + favicon）

**Files:**
- Modify: `frontend/index.html:5-8`
- Delete: `frontend/public/favicon.svg`

- [x] **Step 2.1: 改 index.html**

`<head>` 整段替换为：

```html
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <link rel="icon" type="image/png" sizes="32x32" href="/favicon-32.png" />
    <link rel="apple-touch-icon" href="/apple-touch-icon.png" />
    <title>WhoTalk · AI Academy</title>
  </head>
```

（原 favicon.svg link 删除，title 由 `zhice-OS V2` 改为 `WhoTalk · AI Academy`）

- [x] **Step 2.2: 删除旧 favicon.svg**

```bash
git rm frontend/public/favicon.svg
```

（已核实全仓库仅 index.html 引用它）

- [x] **Step 2.3: 验证 dev server 出 200**

```bash
curl -g -s -o /dev/null -w '%{http_code}\n' 'http://[::1]:5174/favicon-32.png'
```

Expected: `200`（vite 自动服务 public/；若 dev server 未跑则跳过此步，Task 6 统一验）

- [x] **Step 2.4: 提交**

```bash
git add frontend/index.html
git commit -m "feat(brand): 标签标题与 favicon 切换 WhoTalk

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: 登录/注册页品牌区 + auth.css + i18n

**Files:**
- Modify: `frontend/src/pages/LoginPage.tsx:60-61`
- Modify: `frontend/src/pages/RegisterPage.tsx:65-66`
- Modify: `frontend/src/styles/auth.css:58-68`
- Modify: `frontend/src/i18n/locales/zh/pages.json:8`、`frontend/src/i18n/locales/en/pages.json:8`
- Test: `frontend/src/test/LoginPage.test.tsx`、`frontend/src/test/RegisterPage.test.tsx`

- [x] **Step 3.1: 先写失败测试**

`LoginPage.test.tsx` 的 describe 内、"渲染用户名输入框…" 用例之后加：

```tsx
  it('品牌区展示 WhoTalk logo 图', () => {
    renderLoginPage();
    const logo = screen.getByAltText('WhoTalk AI Academy');
    expect(logo).toBeInTheDocument();
    expect(logo).toHaveAttribute('src', '/logo.png');
  });
```

`RegisterPage.test.tsx` 的 describe 内、"渲染用户名/邮箱/密码…" 用例之后加：

```tsx
  it('品牌区展示 WhoTalk logo 图', () => {
    renderRegisterPage();
    const logo = screen.getByAltText('WhoTalk AI Academy');
    expect(logo).toBeInTheDocument();
    expect(logo).toHaveAttribute('src', '/logo.png');
  });
```

- [x] **Step 3.2: 跑测试确认失败**

```bash
cd frontend && npx vitest run src/test/LoginPage.test.tsx src/test/RegisterPage.test.tsx
```

Expected: 新增 2 个用例 FAIL（`Unable to find an element with alt text: 'WhoTalk AI Academy'`），其余全 PASS。

- [x] **Step 3.3: 改 LoginPage.tsx**

```tsx
      <section className="auth-brand">
        <div className="auth-brand-logo">ZHICE·OS</div>
        <div className="auth-brand-tag">{t('login.brandTag')}</div>
        <div className="auth-brand-sub">
```

替换为（删掉 tag 行，logo 文字换图片）：

```tsx
      <section className="auth-brand">
        <img className="auth-brand-logo" src="/logo.png" alt="WhoTalk AI Academy" />
        <div className="auth-brand-sub">
```

- [x] **Step 3.4: 改 RegisterPage.tsx**

同样结构，同样替换（两文件该段代码完全一致）。

- [x] **Step 3.5: 改 auth.css**

`.auth-brand-logo` 与 `.auth-brand-tag` 两个规则块：

```css
.auth-brand-logo {
  font-size: 40px;
  font-weight: 800;
  letter-spacing: 2px;
  color: var(--ac);
}
.auth-brand-tag {
  margin-top: 14px;
  font-size: 20px;
  color: var(--atext);
  font-weight: 600;
}
```

替换为：

```css
.auth-brand-logo {
  display: block;
  width: 260px;
  max-width: 100%;
  height: auto;
}
```

（`.auth-brand-tag` 规则整体删除——全仓库仅 Login/Register 两处引用，本任务已同步移除）

- [x] **Step 3.6: 删 i18n key**

`zh/pages.json` 删行：`"brandTag": "量化交易系统",`
`en/pages.json` 删行：`"brandTag": "Quantitative Trading System",`

- [x] **Step 3.7: 跑测试确认通过**

```bash
cd frontend && npx vitest run src/test/LoginPage.test.tsx src/test/RegisterPage.test.tsx
```

Expected: 全部 PASS（含新增 2 例）。

- [x] **Step 3.8: 提交**

```bash
git add src/pages/LoginPage.tsx src/pages/RegisterPage.tsx src/styles/auth.css src/i18n/locales/zh/pages.json src/i18n/locales/en/pages.json src/test/LoginPage.test.tsx src/test/RegisterPage.test.tsx
git commit -m "feat(brand): 登录/注册品牌区换 WhoTalk logo 图

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

（在 frontend/ 目录下 git add——或回仓库根用 `frontend/src/...` 全路径，二选一保持一致）

---

### Task 4: 侧边栏顶部（WorkspaceLayout）

**Files:**
- Modify: `frontend/src/layouts/WorkspaceLayout.tsx:89`
- Modify: `frontend/src/themes/tokens.css:184-185`

- [x] **Step 4.1: 改 WorkspaceLayout.tsx**

```tsx
        <button className="ws-logo" onClick={() => navigate('/')} title={t('shell.backToBigscreen')}>ZHICE·OS</button>
```

替换为：

```tsx
        <button className="ws-logo" onClick={() => navigate('/')} title={t('shell.backToBigscreen')}>
          <img className="ws-logo-icon" src="/logo-icon.png" alt="" />
          <span>WhoTalk</span>
        </button>
```

- [x] **Step 4.2: 改 tokens.css 的 .ws-logo**

```css
.ws-logo { font-family: var(--display); font-size: 16px; font-weight: 800; color: var(--cyan); cursor: pointer;
  letter-spacing: 1px; border: none; background: none; padding: 0; }
```

替换为：

```css
.ws-logo { display: inline-flex; align-items: center; gap: 8px; font-family: var(--display); font-size: 16px;
  font-weight: 800; color: var(--cyan); cursor: pointer; letter-spacing: 1px; border: none; background: none; padding: 0; }
.ws-logo-icon { width: 22px; height: 22px; display: block; }
```

- [x] **Step 4.3: tsc 验证**

```bash
cd frontend && npx tsc -b
```

Expected: 无输出（编译通过）。

- [x] **Step 4.4: 提交**

```bash
git add frontend/src/layouts/WorkspaceLayout.tsx frontend/src/themes/tokens.css
git commit -m "feat(brand): 侧边栏 logo 换火焰 icon + WhoTalk

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: 首页大屏（HomeBigscreen）

**Files:**
- Modify: `frontend/src/pages/home/HomeBigscreen.tsx:161`
- Modify: `frontend/src/themes/tokens.css:245-249`

- [x] **Step 5.1: 改 HomeBigscreen.tsx**

```tsx
          <div className="logo">ZHICE·OS</div>
```

替换为：

```tsx
          <div className="logo"><img src="/logo.png" alt="WhoTalk AI Academy" /></div>
```

- [x] **Step 5.2: 改 tokens.css 的 .logo**

```css
.logo { font-family: var(--display); font-size: 27px; font-weight: 900; letter-spacing: 2px;
  background: linear-gradient(100deg, var(--violet) 0%, var(--cyan) 50%, var(--violet) 100%);
  background-size: 200% 100%; -webkit-background-clip: text; background-clip: text; color: transparent;
  animation: shimmer 6s linear infinite; }
@keyframes shimmer { to { background-position: 200% 0; } }
```

替换为（流光文字样式废弃，keyframes shimmer 全仓库唯一消费者即此处，一并删除）：

```css
.logo { line-height: 0; }
.logo img { display: block; height: 46px; width: auto; }
```

`.logo-sub` 规则（下一行）保持不动——「量化交易指挥中心」是功能描述，保留。

- [x] **Step 5.3: tsc 验证**

```bash
cd frontend && npx tsc -b
```

Expected: 无输出。

- [x] **Step 5.4: 提交**

```bash
git add frontend/src/pages/home/HomeBigscreen.tsx frontend/src/themes/tokens.css
git commit -m "feat(brand): 首页大屏 logo 换 WhoTalk 横版图

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: 全量验证（测试 + 构建 + Playwright 实拍）

**Files:** 无新增（验证任务；如实拍发现视觉问题，修复后追加 commit）

- [x] **Step 6.1: vitest 全量**

```bash
cd frontend && npm run test
```

Expected: 全部 PASS，0 fail（此前 121 个用例 + 本次新增 2 个）。

- [x] **Step 6.2: 构建**

```bash
cd frontend && npm run build
```

Expected: `tsc -b` 无错误 + vite build 成功。

- [x] **Step 6.3: Playwright 实拍三处**

用 playwright MCP（`http://localhost:5174`）：

1. 导航到 `/login` → 截图 `login-logo.png`，确认：左栏 logo 图清晰、无黑色矩形边、文字正常、下方「四市场·全自动」+ chips 仍在
2. 在注册页注册临时账号（用户名 `whotalk_logo_check`，合法邮箱如 `whotalk_logo_check@test.local`，密码随意如 `Logo#2026`；公开注册默认 VIEWER 即可）→ 注册后自动登录
3. 截图首页大屏（`/`）→ 确认头部 WhoTalk logo 图 + 下方「量化交易指挥中心」仍在
4. 点侧边栏任意市场 tab 进入工作台 → 截图 → 确认左上「火焰 icon + WhoTalk」，点击它应回大屏
5. `browser_network_requests` 过滤 `favicon-32.png` → 确认 200

视觉红线：logo 出现黑底矩形边缘 = un-screen 失败（回查 Task 1 产出）；火焰残缺/裁切过头 = `flame_square` 边界问题，调 `BLACK_THRESHOLD`/`PAD_RATIO` 重跑脚本。

- [x] **Step 6.4: 主题轮换抽查（spec §6 要求）**

在个人中心（头像下拉 → 个人中心）把主题切到 emerald 与 amber，各截一张含侧边栏 logo 的工作台图。透明资产理论上与主题无耦合，肉眼确认火焰/文字在带色背景上无异常描边或发灰即可（carbon/aurora 与默认观感一致，抽查另外两套足够）。

- [x] **Step 6.5: 清理临时账号（可选）**

- [x] **Step 6.4: 清理临时账号（可选）**

```bash
cd /Users/join/Desktop/zhice/zhice-os-v2 && .venv/bin/python -c "
from zhice_os_v2.infrastructure.db.sqlalchemy_db import get_session
from zhice_os_v2.infrastructure.db.models import User
s = get_session()
u = s.query(User).filter_by(username='whotalk_logo_check').one_or_none()
if u: s.delete(u); s.commit(); print('deleted')
else: print('not found')
"
```

（仅本机 dev 库；失败不阻塞）

- [x] **Step 6.6: 收尾提交（如有修复）**

若有视觉修复，逐一 `git add <文件> && git commit -m "fix(brand): ..."`；无则跳过。

- [x] **Step 6.7: 汇报**

向用户展示三张截图，等待验收。**不要自行合回 main**——分支保留，由用户决定合并（用户惯例：验收后合本机 main）。
