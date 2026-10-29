# PageShell head/footer 替换 wave 计划 v1

- 日期：2026-10-30
- 工单：`WO-LEGION-PAGESHELL4-20261030`
- 原则：**按 wave 替换，不一次性重写 95 页**；每页先备份截图/契约测试，再迁移。

## Wave 0（已完成）

- canonical 壳资产注入 101 页（missing 0 / duplicates 0）；
- 模板 `portal/_templates/page_shell_v1.html` 增加标准 `ui-page-footer`；
- 生成器 `--new` 可从模板产出新页；
- 组件视觉基线 warn-only 门接入合并流程。

## Wave 1（下一批，建议 8 页）

只选“head 只有 title + tokens/nav、body 结构简单、无内联大样式”的页面：

1. `agent_board.html`（运行时生成页，先做只读壳）
2. `picks_public/index.html`（跳转页，保持 meta refresh）
3. `perf.html`
4. `capability.html`
5. `ledger.html`
6. `data_daily.html`
7. `data_guojia.html`
8. `org.html`

迁移规则：

- 保留每页 `<title>`、业务 `<script>`、业务 `<style>`；
- 只替换 head 的公共资产段和 footer；
- 先复制旧文件到 `.worktrees/.../backup/`，跑 UI 审计 + 1440/390 截图；
- 一页一提交，不混批。

## Wave 2（域页）

量化/情报/创作/开发/系统各 1-2 页，优先 `/quant`、`/intel`、`/dev`、`/ops`。

## Wave 3（剩余）

其余页面；每批最多 10 页，发现破版立即回滚该批。

## 验收

- `page_shell_gen --check --all` missing=0；
- 页面无重复 canonical/导航；
- 1440/390 无横向溢出，无 pageerror；
- 旧业务 ID/脚本保留，数据契约不变。
