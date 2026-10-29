# PageShell 生成器 + UI Kit 高频面接入 v1

- 日期：2026-10-30
- 工单：`WO-LEGION-PAGESHELL-20261030`
- 范围：`/os-entry`、`/os-board` 两个高频面；PageShell 生成器/模板

## 1. PageShell 生成器

新增：

- `portal/_templates/page_shell_v1.html`：标准页骨架（tokens → os_theme → ui.css → nav.js → ui.js；page-head + actions + content）；
- `tools/page_shell_gen.py`：
  - `--check`：检查页面是否缺 canonical 资产；
  - `--apply`：幂等追加缺失资产（末尾追加，不移动既有行，避免 secret-scan 误报）；
  - `--revert --apply`：回滚注入；
  - `--new <name> --title ...`：从模板生成新页；
  - `--all`：面向 94 页的批量接入入口（本轮先接两页）。

canonical 资产：

```text
/static/tokens.css
/static/os_theme.css
/static/ui/ui.css
/static/nav.js
/static/ui/ui.js
```

## 2. UI Kit 接入

### /os-entry

- 引入 `ui.css` / `ui.js`；
- KPI 卡：`ui-kpi os-kpi`；
- 面板：`ui-panel os-panel`；
- 保留原有四视图/折叠/下钻/偏好逻辑。

### /os-board

- 引入 `ui.css` / `ui.js`；
- 产出卡：`ui-card acard`；
- 指标卡：`ui-kpi tile`；
- 保留搜索/折叠/偏好。

## 3. 验证

- 生成器：check → apply → check，目标 2 页 missing 0 / changed 2 / duplicates 0；
- UI 接入静态契约：5 tests passed；
- 浏览器：两页无 pageerror；`/os-entry` ui-kpi=4、ui-panel=18；`/os-board` ui-kpi=31、ui-card=16；
- 截图：`docs/visual_baseline/2026-10-30_p1_ui_kit/`。

## 4. 回滚

```powershell
python tools/page_shell_gen.py --revert --apply --pages portal/os_entry_v9_prod.html portal/os.html
```

## 5. 下一步

- 用 `--all` 分批接入剩余页面，先做 `--check` 报告；
- 逐步把页内 head/footer 替换为模板生成；本轮只统一资产，不重写业务页；
- 组件级视觉基线纳入合并门 warn-only。
