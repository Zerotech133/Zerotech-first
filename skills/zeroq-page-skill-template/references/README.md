# 骨架契约速查与本地自测

本文件是 `zeroq-page-skill-template` 的说明，对应《零界技能（Skill）开发规范与指南 v1.0》整理版
`docs/零界技能开发规范_v1.0.md`。**本包只做本地自测，未导入租户、未挂 Agent、未注册服务。**

## 一、契约对照

| 规范条目 | 本包实现 |
| --- | --- |
| 双轴推导 | `scripts/` 下有脚本 → `scripts_executables`；frontmatter `page: html/index.html` 且文件存在 → `pageEntry` 非空 |
| frontmatter | `name` / `description`（40–300 字符）/ `page` / `api` / `tags`；路径无 `..`、无绝对路径 |
| 页面自包含 | `html/index.html` 内联 CSS + JS，无 CDN、无外链资源、无顶层导航/弹窗/表单 |
| 数据获取 | `fetch("../../api/<path>", { credentials: "include" })` 命中数据桥 |
| api.py | stdlib only；stdin 收 `{path, method, query, body, companyId, skillId}`；stdout 出 JSON 或 `{statusCode, body, headers}` 信封；未知端点 404；只读 `sqlite3.connect(...mode=ro, uri=True)` |
| ingest.py | Agent run 时执行；写 `~/.zerobound/instances/default/skill-data/<companyId>/<skillId>/demo.db`；靠 `ZB_COMPANY_ID` / `ZB_SKILL_ID` 与数据桥对齐 |
| 空数据 | 库不存在或空表返回 `empty: true` + `hint`，页面给引导提示 |
| 包目录只读 | 运行时数据只写公司隔离目录，不写包内 |

## 二、本地自测（不经服务端，规范 §七.2）

在包根目录执行，Python 用本机 3.11（stdlib only，不需要 pandas）：

```bash
ZB_COMPANY_ID=local-selftest ZB_SKILL_ID=zeroq-page-skill-template \
  python scripts/ingest.py --source demo

echo '{"path":"summary","companyId":"local-selftest","skillId":"zeroq-page-skill-template"}' \
  | python scripts/api.py

echo '{"path":"items","query":{"label":"alpha"},"companyId":"local-selftest","skillId":"zeroq-page-skill-template"}' \
  | python scripts/api.py

echo '{"path":"nope","companyId":"local-selftest","skillId":"zeroq-page-skill-template"}' \
  | python scripts/api.py
```

预期：`ingest` 输出 `ok: true` 与库路径；`summary` 出 `count`；`items` 出按 label 过滤的行；
未知端点出 `statusCode` 404 信封。库不存在时 `summary` 出 `empty: true` 与 `hint`。

Windows 下可用 `set ZB_COMPANY_ID=...` 或在命令前临时设置环境变量；路径用正斜杠。
本机自动化回执见 `tmp/zeroq_skill_skeleton_selftest.md`。

## 三、换真实数据源

只改 `scripts/ingest.py` 的 `build_rows()`：把演示数据换成真实快照的 `(code, label, value, updated_at)` 四元组，
写库 schema 与 `api.py` 不用动。若需要更复杂的结构，先扩表再扩端点，页面仍只走数据桥。
**注意**：`api.py` 限定 stdlib only、30 秒、8MB，任何 pandas / parquet / 远程取数都必须留在 `ingest.py` 侧。

## 四、导入与挂载（本包未执行）

完整流程见规范 §七.3–§七.7：把包放进公司受管技能目录 → 调 `skills/import` → 列表接口验证
`pageEntry` 与 `trustLevel` → 验证页面与数据桥 → 用 `mode: "add"` 挂到 Agent（**不要用 `replace`**）。
执行前需要：零界终端 3100 服务可用、对应公司的 `companyId`、以及老田的明确批准。

本轮定位结论：`docs/零界技能规范_本仓差异注记.md` 与 `tmp/zeroq_3100_定位报告.md` 记载，
本机与该服务的已知部署清单中均未找到 3100，故 §七 的完整闭环**暂不可跑**。

## 五、已知限制

- Windows 无符号链接权限时 `ensureZeroBoundSkillSymlink` 会 EPERM，desired 状态仍持久化、页面链路不受影响；本机验证走人工灌数。
- 每请求 spawn python 有冷启动开销；高频轮询场景再考虑 TTL 缓存（本骨架未做）。
- `companyId` / `skillId` 用白名单字符校验防路径逃逸，非法值回落到本地自测缺省值。
