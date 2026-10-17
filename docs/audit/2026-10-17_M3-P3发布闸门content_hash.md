# M3-P3 发布闸门 content_hash 绑定（2026-10-17）

> M3 总纲验收句：**每份交付物都有来源、审查、版本、负责人和可回滚记录。**
> 总纲幂等规则落地：**审批绑定目标 revision 与 content_hash，目标变更后原审批自动失效。**

## 一、交付物

| 层 | 改动 |
|---|---|
| 契约 | `contracts/objects.py` Approval 尾部追加 `content_hash`（默认空，零破坏） |
| 存储 | `engine/approvals.py`：SCHEMA 加列 + `_ensure_columns` 容错迁移（对齐 trace_id 先例）；record 写入/读回；**同键异 hash 拒绝**（既有凭据绑 H1、本次声明 H2 → ValueError，store 层统一防住） |
| gzh | `approve_transition`：published 落凭据绑当次 md 指纹（`_md_content_hash`）；幂等快路径命中凭据后校验当前真源 hash，**不匹配 → 批 A 发 B 拒绝**（要求打回 draft 重新走门） |
| pitch | `approve_transition`：approved/delivered 落凭据绑 deck 文件指纹（`_deck_content_hash`）；**delivered 前置校验**——最新 approved 凭据 hash vs 当前 deck，不匹配拒绝（拒绝在旧批准上交付新内容，真源零越门） |
| skill | `approve`：落凭据绑八要素定义 canonical JSON 指纹（批准时点=reviewed 态，即决策所基于的内容）；重放快路径校验定义变更 → 拒绝（走 deprecated 下线后注册新版本） |

测试：`test_gate_content_hash.py` 9 例——hash 落库读回 round-trip / 同键异 hash 拒绝 / **legacy 无 hash 凭据重放兼容**（存量 31 条不受影响，真实仓 fact-check 重放实证）/ gzh published 绑 hash + 篡改拒绝 + 打回重批绑新一轮 / pitch approved 绑 hash + deliver 拒绝且真源停在 approved / clean deliver 通过 / skill 定义篡改拒绝。

## 二、批 A 发 B 语义（三处防线）

1. **store 层**：同幂等键、content_hash 不同 → ValueError（并发窗口兜底）；
2. **gzh 快路径**：published 凭据在、md 已改 → GzhWiringError「批 A 发 B 拒绝…打回 draft 修改后重新走门」；
3. **pitch delivered 前置**：approved 凭据绑的 hash ≠ 当前 deck → 拒绝，批准内容与交付内容强制一致。

**打回重批 = 正常演进**：gzh reviewed→draft 打回后修改再 reviewed，轮次化键 #r2 落新凭据绑新 hash——变更失效只打击「在旧批准上重放/交付」，不挡正常迭代。

## 三、判例

1. **批准时点绑定**：content_hash=决策所基于的内容（skill 绑 reviewed 态定义而非 approved 态——落凭据在真源 _save 之前，天然正确）；
2. **legacy 兼容靠空值跳过**：旧凭据 content_hash 为空 → 校验跳过按原语义重放，存量账本零迁移；
3. **变更失效≠禁止变更**：失效规则只封「旧批准重放」，轮次化键承接正常重批（两机制正交）；
4. **pitch 双门链校验**：delivered 门校验的不只是自己的 hash，还有前置 approved 凭据的 hash——门链上传递内容一致性。

## 四、验收门 3 核对（M3 七门制）

| 项 | 状态 |
|---|---|
| 三类受门转移 Approval 带 content_hash | ✅（gzh published / pitch approved+delivered / skill approved） |
| 批 A 发 B 被拒绝（测试实锤） | ✅（gzh 快路径 / pitch 前置 / skill 重放，三线全锁） |
| 存量凭据兼容 | ✅（legacy 重放 + 真实仓 fact-check 实证 replayed=True） |
| 零事实源破坏 | ✅（拒绝路径真源不动，pitch 停在 approved） |
| 全仓回归 | ✅ 131/131 |

## 五、下一步

- **M3-P4**：审计回放扩展（academy/pitch 交叉校验清 T2、replay_query 加 artifact 段、evidence_collect 补 artifact 指纹域、T1 剩余三处）。
