# Q2 验收编排 Runbook v1（S2 · data-warehouse + 4 Q1）

- 日期：2026-11-03｜适用：P0 **S2**「q2-auditor 只读验收身份」｜工具：`tools/q2_acceptance.py`
- 目标：一条命令编排 **data-warehouse 登录/SLO 探针** + **4 个 Q1 军团（huangzhong-acceptance / lusu-d1-flow / quant-domain / quant-strategy-line）Q2 证据包状态**；结论显式区分 **AUTO-EVIDENCE** 与 **OWNER-SIGNOFF**。
- 性质：**只读编排**。不修改 Q2 证据包、不写生产数据、不启停服务、不改 nginx；唯一写点是显式 `--out`。**不伪造登录态、不代签**。

## 一、前置：A0 会话文件（一次性，不入库）

1. 浏览器登录 `http://127.0.0.1:8734`（唯一入口）。
2. DevTools → Application → Cookies → 复制 `zb_session` 的值。
3. 写入一次性文件（不要发到聊天/不要提交）：

```powershell
Set-Content -Path C:\Temp\zb_session.txt -Value "zb_session=<value>" -Encoding ascii
```

- 工具只读该文件，**不在任何输出中打印 cookie 内容**；用完立即删除：

```powershell
Remove-Item C:\Temp\zb_session.txt
```

- 默认会话文件：`C:\Temp\zb_session.txt`；也可用 `--cookie-file <path>` 指定；`--cookie "zb_session=..."` 可直传（避免落盘，但不建议在 shell 历史留存）。

## 二、命令

```powershell
cd C:\项目\老田的AI助手

# 1) 无会话先跑（预期 PENDING-LOGIN / rc=2，可解释）
python -X utf8 tools/q2_acceptance.py --markdown
python -X utf8 tools/q2_acceptance.py --json

# 2) A0 提供会话后跑（DW 页面/API/SLO 自动证据）
python -X utf8 tools/q2_acceptance.py --json
python -X utf8 tools/q2_acceptance.py --cookie-file C:\Temp\zb_session.txt --markdown

# 3) 落盘留证（唯一写点；默认只打印）
python -X utf8 tools/q2_acceptance.py --json --out C:\Temp\q2_acceptance_20261103.json
```

## 三、输出解读

- **结论三元组**：`conclusion.state` / `conclusion.auto_evidence` / `conclusion.owner_signoff`
  - `AUTO-EVIDENCE`：本工具/探针可自动收集的证据（DW 探针、SLO、Q1 证据包自动状态）= `PASS / PENDING / FAIL`；
  - `OWNER-SIGNOFF`：军团 owner 人工签署（本工具**不代签**，默认 `PENDING`）；
  - `state`：`AUTO-EVIDENCE-PASS` / `PENDING` / `ERROR`。
- **会话区**：`session.provided`、`session.source`、`session.blocker`、`dw.auth_state`（无会话=`PENDING-LOGIN`）。
- **DW 探针**：`/api/dw/stats`、`/dw`、`/dataops` 未登录路由状态、登录后页面 200、API p95（<800ms）。
- **Q1 表格**：owner / 自动证据（`AUTO-EVIDENCE-COLLECTED` vs `PARTIAL`）/ 证据包文件是否存在 / OWNER-SIGNOFF / manual gate / 缺口。
- **退出码**：
  - `0` = AUTO-EVIDENCE-PASS（**仍须 owner 签署才算 Q2 PASS**）；
  - `2` = PENDING（无会话 `PENDING-LOGIN`、DW 未过、或 Q1 自动证据/证据包有缺口）；
  - `3` = ERROR（fail-visible：证据矩阵缺失/损坏、显式会话文件缺失、探针异常、`--out` 写失败）。

### 典型输出（2026-11-03 06:59 实测，无会话）

```text
结论：state=PENDING｜AUTO-EVIDENCE=PENDING｜OWNER-SIGNOFF=PENDING
会话：provided=false｜source=C:\Temp\zb_session.txt｜blocker=默认会话文件不存在（未登录）
DW：auth_state=PENDING-LOGIN（不伪造登录）
Q1：4/4 自动证据齐（AUTO-EVIDENCE-COLLECTED）、证据包存在、签署 PENDING
rc=2
```

## 四、验收流程（A0 主持）

1. 先跑（无会话）确认 `PENDING-LOGIN` 与 Q1 汇总符合预期；
2. A0 按 §一 提供一次性会话文件；
3. 再跑：`dw.auth_state=PASS`（`/dw`、`/dataops` 均 200、SLO <800ms）且 Q1 自动证据齐 → `state=AUTO-EVIDENCE-PASS`（rc=0）；
4. **Q2 结论仍由各军团 owner 签署**：`docs/q2/<legion>_Q2_evidence_v1.md` 的「签署（待 owner）」节由 owner 填 owner/日期/结论；工具会自动识别为 `OWNER-SIGNOFF=SIGNED`（仅在签署节无占位符且含 owner/日期/结论时）；
5. 完成后删除 `C:\Temp\zb_session.txt`，`--out` 的 JSON 如需留档另存（不含 cookie）。

## 五、边界（红线）

- 只读：**不写/不改** `docs/q2/` 证据包、不写生产数据/任务/会议；唯一写点是显式 `--out`。
- **不伪造登录态**：无会话一律 `PENDING-LOGIN`；不使用假 cookie、不把未登录结果标 PASS。
- **不代签**：`OWNER-SIGNOFF` 只能由 owner 在证据包签署节填写；工具不写签署、不把自动证据当 Q2 PASS。
- 不启停服务、不改 nginx/登录守卫、不执行 L2 变更；不 git 提交。
- 与既有工具的关系：`tools/dw_q2_probe.py`（DW 探针单一实现，本工具复用）；`tools/q2_evidence.py`（证据包生成/重跑，本工具只读其产物矩阵）；本工具不重跑证据生成器、不覆盖证据包。

## 六、常见问题

| 现象 | 含义/处置 |
|---|---|
| 默认会话文件不存在 → rc=2 PENDING | 正常（未登录）；A0 提供会话后重跑 |
| 显式 `--cookie-file` 指向缺失/空文件 → rc=3 ERROR | fail-visible；检查路径/文件内容（`zb_session=` 或纯值均可） |
| `Q2 证据矩阵不可读/解析失败` → rc=3 ERROR | 先跑 `python -X utf8 tools/q2_evidence.py --write` 重建自动证据（属 owner/ARCH1 动作，本工具不代跑） |
| Q1 某军团 `gaps=evidence-file-missing / auto-evidence-partial` → PENDING | 该军团 Q2 自动证据未齐；按 manual gate 补证据后再跑 |
| 有会话但仍 `auth_state=FAIL` | 检查会话是否过期/角色不符；重新登录取新 cookie；不要改 nginx 绕过 |
