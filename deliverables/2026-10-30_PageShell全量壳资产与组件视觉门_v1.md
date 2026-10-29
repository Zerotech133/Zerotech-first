# PageShell 全量壳资产 + 组件视觉基线门 v1

- 日期：2026-10-30
- 工单：`WO-LEGION-PAGESHELL2-20261030`
- 范围：96 个 portal 页面；PageShell 模板/生成器；组件视觉基线 warn-only 门

## 1. 全量壳资产

`tools/page_shell_gen.py --apply --all`：

```text
targets 96
missing 0（注入前 93）
changed 93
duplicates 5（历史重复资产，保留待清理）
```

canonical 资产：

```text
/static/tokens.css
/static/os_theme.css
/static/ui/ui.css
/static/nav.js
/static/ui/ui.js
```

- 只在末尾追加缺失项，不移动既有行；
- 未重写任何业务 head/footer；现有页面结构保持。

## 2. PageShell 模板

- `portal/_templates/page_shell_v1.html`：标准页骨架；
- `tools/page_shell_gen.py --new <name> --title ...`：新页从模板生成；
- 后续批次再把旧页 head/footer 替换为模板生成，本轮先统一壳资产。

## 3. 组件视觉基线 warn-only 门

新增：

- `docs/visual_baseline/2026-10-30_ui_kit/manifest.json`：ui.css/ui.js/ui_kit.html/两张截图 sha256；
- `tools/ui_visual_gate.py`：检查资产/截图/manifest；`--warn-only` 永远 rc=0；
- `tools/devtask.py`：合并门新增「组件视觉基线」warn-only 检查，失败/不一致只告警、不阻断。

## 4. 验证

- `pytest tools/test_page_shell_gen.py tools/test_ui_visual_gate.py`：6 passed；
- 抽样 7 页（home/quant/intel/os/creator/dev/ops）：
  - nav=1，无横向溢出，无 pageerror；
- `page_shell_gen --check --all`：missing 0；
- 临时服务已停止。

## 5. 剩余

- 5 处重复 canonical 资产（历史页）需逐页清理；
- 旧页 head/footer 模板替换按 wave 进行，不在本轮批量重写；
- 视觉基线在 UI Kit 变更时需同步更新 manifest（门只告警）。
