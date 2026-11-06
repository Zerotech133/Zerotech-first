# governance_upgrade — 治理升级包远端仓库

本仓库由**远端研究节点**维护，用于把治理能力的升级（优化后的 `rules.json`、新检测器、
阈值/模型）回传给**本机治理节点**。本机通过 Git 双轨的 `sync-back` 拉取并过回归门禁后应用。

## 目录布局

```
（仓库根即升级包：可直接 `git init` 后推送远端；clone 后根目录即含下列文件）
├── rules.json              # 升级包主体（规则集本体；与 D:\rules.json 同构）
├── version                 # 纯文本版本戳，如 governance_upgrade.v1（sync-back 以此判定是否新）
├── changelog.md            # 本版变更说明
├── detectors/              # 未来新检测器放置处（占位 .gitkeep）
└── scripts/
    └── contract_check.py   # 契约校验（本机 sync-back 前可手动/CI 校验）
```

## 升级包契约（sync-back 消费约定）

| 文件 | 要求 |
|------|------|
| `version` | 必填，非空纯文本；每发一版递增（v1→v2…） |
| `rules.json` | 必填，合法 JSON，含 `version`/`normalize`/`fields`/`pii_gate` 字段 |
| `changelog.md` | 必填，说明本版变更 |

> 本机 `GitSync.load_pkg()` 读取上述三件；`upgrade_dir = <cache>`（仓库根即升级包）。

## 如何提交一个升级包（远端研究节点）

1. 修改 `rules.json`（例如按 M1 回执调整 `pii_gate.types`）。
2. 在 `version` 文件中 bump 版本（如 `governance_upgrade.v2`）。
3. 在 `changelog.md` 追加本次变更。
4. `git commit && git push` 到 `main`。
5. 本机 `sync_watch` 将检测新版本并触发 `sync-back`（应用前 `contract_check.py` 契约校验 + 28 用例回归门禁）。

### 示例：M1 回执后发 v2

```diff
  "pii_gate": {
    "enabled": true,
-   "types": ["email", "url", "phone", "contact", "handle"],
+   "types": ["email", "url", "phone", "handle"],
    "note": "按导出端 M1 回执，移除 contact（导出端已全量脱敏，守门不再拦截）"
  }
```
`version` → `governance_upgrade.v2`，`changelog.md` 记录原因即可。

## 本机对接（治理节点）

本机不暴露公网端口，采用 **pull 模式**：由系统计划任务 / cron 每分钟调用一次调度器。

```bash
# 单次检查（推荐：Windows 任务计划程序 / cron 每分钟）
python -m governance_node.sync_watch --repo-url git@host:govern/upgrade.git --once

# 或长驻循环（每 300s 查一次）
python -m governance_node.sync_watch --repo-url git@host:govern/upgrade.git --daemon --interval 300
```

调度器行为：
1. `git pull` 远端仓库到本地缓存（`~/.govern_cache/upgrade`）。
2. 读 `version`，与本地已应用版本（`upgrade_state.json`）比对。
3. 若不同 → 调用 `govern_cli sync-back --apply`：先跑 `D:\tests` 的 unittest 回归门禁，
   通过才覆盖 `D:\rules.json` 并备份旧版；不通过则保留原状。
4. 更新 `upgrade_state.json` 记录已应用版本与时间。

## 闭环总览

```
远端研究节点 (governance_upgrade 仓库)
      │  push 升级包（rules.json + version + changelog）
      ▼
本机 sync_watch --once (每分钟/cron)
      │  检测 version 变化
      ▼
govern_cli sync-back --apply
      │  unittest 回归门禁（28 用例）
      ├─ 通过 → 备份旧 rules.json + 覆盖 + 记录 state
      └─ 失败 → 保留原状，规则集不被污染
```

## 本机调度注册（Windows 任务计划）

远端仓库就绪后，在本机把调度器注册为每分钟 pull 一次：

1. 编辑 `D:\governance_node\sync_watch_runner.bat`，把 `GOV_REPO` 改为你的真实远端 URL。
2. **右键 `D:\governance_node\ops_register_scheduler.bat` → 以管理员身份运行**，
   注册任务 `GovernanceSyncWatch`（每分钟执行一次 runner）。
3. 此后远端每发一版（bump `version` + 改 `rules.json`），本机自动拉取、过回归门禁、应用。

> 仅做离线验证时，可改用 `sync_watch_local_demo.bat`（以本地 `D:\governance_upgrade` 作为 stand-in remote，无需真实远端即可跑通闭环）。

