---
name: 零界页面型技能骨架
description: 自带页面型技能的最小可运行模板，演示 SKILL.md frontmatter、数据桥 api.py、采集脚本 ingest.py 与自包含 html 页面的完整契约；数据为本地演示数据，不连接任何真实数据源，供本地自测与二次开发复制使用。
page: html/index.html
api: scripts/api.py
tags:
  - template
  - 可视化
---

# 零界页面型技能骨架

本包是《零界技能（Skill）开发规范与指南 v1.0》中「自带页面型」技能的最小实现骨架：
安全轴为 `scripts_executables`（`scripts/` 下有脚本），展示轴 `pageEntry` 为 `html/index.html`。

## 目录

```
SKILL.md              本文件，唯一入口
scripts/ingest.py     采集/建库：Agent run 时执行，写公司隔离目录
scripts/api.py        数据桥：页面请求时由 server spawn，只读查询
html/index.html       页面入口，自包含、无外网资源
references/README.md  契约速查与本地自测说明
```

## 运行环境

- `ingest.py` 与 `api.py` 均 **stdlib only**，不依赖 pandas、parquet 或任何第三方包。
- 上下文环境变量：`ZB_COMPANY_ID` / `ZB_SKILL_ID`。两者必须同时被 `ingest.py`（写）与数据桥（读）使用，才能落到同一个库。
- 库路径：`~/.zerobound/instances/default/skill-data/<companyId>/<skillId>/demo.db`。
- 运行时数据**不写进技能包目录**；包目录视为只读代码。

## 用法（写给 Agent）

1. 采集：在 Agent run 中执行 `python scripts/ingest.py --source demo`，把演示数据写入公司隔离库。
   换真实数据源时只改 `ingest.py` 的 `build_rows()`，写库 schema 不动。
2. 汇报：照旧输出四段式文本，**无需在汇报里声明页面链接**；页面由系统自动挂载。
3. 查看：Agent 详情 Skills 行的「打开页面」、对话页顶部「技能页面」chip，或汇报首页技能页面卡片。

## 数据桥端点

| path | 返回 |
| --- | --- |
| `summary` | `empty` / `count` / `updatedAt` |
| `items` | `empty` / `items`（支持 `query.label` 过滤） |
| `health` | `ok` |
| 其它 | `statusCode` 为 404 的信封 |

库不存在或为空时返回带 `empty: true` 的结构与引导提示，不抛异常。

## 本地自测（不经服务端）

见 `references/README.md`。规范 §七.2 的两条命令即可验证写库与数据桥，无需 3100 服务。

## 禁止事项

- 页面及其依赖必须自包含在 `html/` 内，不得引用外网 CDN。
- `api.py` 只做只读查询，写数据一律由 `ingest.py` 负责。
- 包内禁止放 `.env` 与密钥文件。
- **本骨架仅供本地自测**：未导入租户、未挂 Agent、未注册任何计划任务或服务。
